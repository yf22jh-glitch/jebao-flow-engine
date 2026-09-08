"""Install, inspect, and run the isolated local-first control service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.dosing import (
    build_dosing_channel_schedule_preview,
    plan_average_interval_dosing,
    plan_daily_dosing,
)
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.install import (
    build_fresh_install_manifest,
    hash_current_template_contents,
    load_current_private_config,
    replace_install_manifest,
)
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.mqtt import LocalControlMqttAdapter, LocalControlMqttRouter
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.resolver import CurrentEndpointResolver
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.operator_capture.app_catalog import public_app_catalog
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode
from jebao_flow.protocol.discovery import GizwitsDiscovery

_CONFIG_ENV = "JEBAO_FLOW_CONFIG"
_MANIFEST_ENV = "JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"
_QUALIFICATION_ENV = "JEBAO_FLOW_LOCAL_QUALIFICATIONS"
_TEMPLATE_ROOTS_ENV = "JEBAO_FLOW_APP_TEMPLATE_ROOTS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-local")
    subparsers = parser.add_subparsers(dest="command", required=True)
    catalog = subparsers.add_parser("catalog", help="show the complete English app catalog")
    catalog.add_argument("--product-key")

    dosing = subparsers.add_parser(
        "plan-dosing",
        help="split a daily dosing total into an exact, evenly timed local plan",
    )
    dosing.add_argument("--channel", type=int, required=True)
    dosing.add_argument("--daily-amount", required=True)
    dosing.add_argument("--doses", type=int, required=True)
    dosing.add_argument("--first-dose", default="00:00", metavar="HH:MM")
    dosing.add_argument("--amount-step", default="0.5")
    dosing.add_argument("--unit", default="mL")
    dosing.add_argument(
        "--app-image-preview",
        action="store_true",
        help="include metadata for the exact 96-byte app-shaped image when doses are integers",
    )

    average_dosing = subparsers.add_parser(
        "plan-average-dosing",
        help="find an integer dose interval for an exact smaller daily average",
    )
    average_dosing.add_argument("--channel", type=int, required=True)
    average_dosing.add_argument("--daily-average", required=True)
    average_dosing.add_argument("--unit", default="mL")

    for name, help_text in (
        ("prepare-install", "replace the install manifest from current inputs"),
        ("ha-schema", "show the current Home Assistant projection"),
        ("serve", "run the qualified Home Assistant/MQTT command service"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _add_install_arguments(command)
        if name in {"ha-schema", "serve"}:
            command.add_argument(
                "--qualifications",
                type=Path,
                default=_environment_path(_QUALIFICATION_ENV),
            )
            command.add_argument(
                "--schedule-pair",
                action="append",
                default=[],
                metavar="MODE:MASTER:SLAVE",
                help="current stable logical topology; repeat for additional slaves",
            )
        if name == "serve":
            command.add_argument(
                "--write-enabled",
                action="store_true",
                help="expose qualified controls after current config safety locks also allow them",
            )

    recover = subparsers.add_parser(
        "recover",
        help="recover one unfinished local-control request from its fresh baseline",
    )
    _add_install_arguments(recover)
    recover.add_argument("request_id")
    return parser


def _add_install_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=_environment_path(_CONFIG_ENV))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_environment_path(_MANIFEST_ENV) or _default_manifest_path(),
    )
    parser.add_argument("--role-attested-by", required=True)
    parser.add_argument(
        "--template-root",
        action="append",
        type=Path,
        default=None,
    )


def _run(args: argparse.Namespace) -> int:
    if args.command == "catalog":
        _print_json(public_app_catalog(args.product_key))
        return 0
    if args.command == "plan-dosing":
        plan = plan_daily_dosing(
            channel=args.channel,
            daily_amount=args.daily_amount,
            dose_count=args.doses,
            first_dose_minute=_parse_clock_minute(args.first_dose),
            amount_step=args.amount_step,
            unit=args.unit,
        )
        payload = plan.public_dict()
        if args.app_image_preview:
            payload["app_image_preview"] = build_dosing_channel_schedule_preview(
                plan
            ).public_dict()
        _print_json(payload)
        return 0
    if args.command == "plan-average-dosing":
        plan = plan_average_interval_dosing(
            channel=args.channel,
            target_daily_average=args.daily_average,
            unit=args.unit,
        )
        _print_json(plan.public_dict())
        return 0
    prepared = _prepare_current(args)
    if args.command == "prepare-install":
        _print_json(_public_install_summary(prepared.manifest))
        return 0
    if args.command == "recover":
        coordinator = _coordinator(prepared.config, build_local_control_catalog())
        result = asyncio.run(coordinator.recover(args.request_id))
        _print_json(result.public_dict())
        return 0
    if args.command in {"ha-schema", "serve"}:
        qualifications = load_current_qualifications(args.qualifications)
        catalog = build_local_control_catalog(
            qualifications,
            current_content=prepared.manifest.content,
        )
        schedule_plans = _schedule_plans(
            prepared.manifest,
            prepared.config,
            tuple(args.schedule_pair),
        )
        projection = project_home_assistant(
            prepared.manifest,
            catalog,
            discovery_prefix=prepared.config.mqtt.discovery_prefix,
            topic_prefix=prepared.config.mqtt.topic_prefix,
            schedule_plans=schedule_plans,
        )
        if args.command == "ha-schema":
            _print_json(projection.public_dict())
            return 0
        _require_live_opt_in(args, prepared.config, projection)
        coordinator = _coordinator(prepared.config, catalog)
        router = LocalControlMqttRouter(
            projection=projection,
            manifest=prepared.manifest,
            catalog=catalog,
            schedule_plans=schedule_plans,
            coordinator=coordinator,
        )
        asyncio.run(
            _serve(
                LocalControlMqttAdapter(
                    prepared.config.mqtt,
                    projection=projection,
                    router=router,
                )
            )
        )
        return 0
    raise AssertionError("unhandled local-control command")


class _PreparedCurrent:
    def __init__(self, *, config, manifest) -> None:
        self.config = config
        self.manifest = manifest


def _prepare_current(args: argparse.Namespace) -> _PreparedCurrent:
    if args.config is None:
        raise ValueError(f"--config or {_CONFIG_ENV} is required")
    config = load_current_private_config(args.config)
    rederived_at = datetime.now(UTC)
    template_roots = (
        tuple(args.template_root)
        if args.template_root is not None
        else tuple(_environment_paths(_TEMPLATE_ROOTS_ENV))
    )
    template_sha256 = hash_current_template_contents(template_roots)
    manifest = build_fresh_install_manifest(
        config,
        role_attested_by=args.role_attested_by,
        role_attested_at=rederived_at,
        template_sha256=template_sha256,
        now=rederived_at,
    )
    replace_install_manifest(args.manifest, manifest)
    return _PreparedCurrent(config=config, manifest=manifest)


def _coordinator(config, catalog) -> LocalControlCoordinator:
    resolver = CurrentEndpointResolver(
        GizwitsDiscovery(
            targets=config.observer.targets,
            bind_address=config.observer.bind_address,
        ),
        timeout_seconds=config.observer.discovery_timeout_seconds,
    )
    return LocalControlCoordinator(
        catalog=catalog,
        resolver=resolver,
        journal=LocalControlJournalStore(),
        lease=LocalControlLease(),
    )


def _schedule_plans(manifest, config, explicit: tuple[str, ...]):
    by_member = {device.logical_member: device for device in manifest.devices}
    triples: list[tuple[NativeLinkageMode, str, str]] = []
    if explicit:
        for value in explicit:
            parts = value.split(":")
            if len(parts) != 3:
                raise ValueError("schedule pair must be MODE:MASTER:SLAVE")
            mode, master, slave = parts
            triples.append((NativeLinkageMode(mode), master, slave))
    else:
        for group in config.groups:
            if group.native_pair is None:
                continue
            triples.append(
                (
                    NativeLinkageMode(group.native_pair.relation.value),
                    group.native_pair.master,
                    group.native_pair.slave,
                )
            )
    slave_members = [slave for _mode, _master, slave in triples]
    if len(slave_members) != len(set(slave_members)):
        raise ValueError("one slave cannot belong to multiple current schedule topologies")
    grouped: dict[tuple[NativeLinkageMode, str], list[str]] = {}
    for mode, master, slave in triples:
        grouped.setdefault((mode, master), []).append(slave)
    plans: list[FreshMasterSchedulePlan] = []
    for (mode, master), slaves in grouped.items():
        try:
            master_binding = by_member[master]
            slave_bindings = tuple(by_member[slave] for slave in slaves)
        except KeyError as error:
            raise ValueError("schedule pair references a missing current logical member") from error
        plans.append(
            FreshMasterSchedulePlan(
                linkage_mode=mode,
                master=master_binding,
                slaves=tuple(FreshSlaveSchedule(target=slave) for slave in slave_bindings),
            )
        )
    return tuple(plans)


def _require_live_opt_in(args, config, projection) -> None:
    if projection.public_dict()["control_count"] == 0:
        return
    if not args.write_enabled:
        raise ValueError("qualified controls require the current --write-enabled invocation")
    if config.runtime.dry_run:
        raise ValueError("current private config still has runtime.dry_run enabled")
    controlled_members = {
        entity.logical_member for entity in projection.entities if entity.control_available
    }
    blocked = {
        device.id
        for device in config.devices
        if device.id in controlled_members and not device.control.allow_hardware_writes
    }
    if blocked:
        raise ValueError("current private config keeps one or more controlled devices write-locked")


async def _serve(adapter: LocalControlMqttAdapter) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass
    await adapter.run(stop_event)


def _public_install_summary(manifest) -> dict[str, object]:
    return {
        "status": "prepared",
        "generation": manifest.generation,
        "rederived_at": manifest.rederived_at.isoformat(),
        "device_count": len(manifest.devices),
        "template_count": len(manifest.content.template_sha256),
        "app_catalog_sha256": manifest.content.app_catalog_sha256,
        "protocol_schema_sha256": manifest.content.protocol_schema_sha256,
        "previous_values_reused": False,
        "private_paths_exposed": False,
    }


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _parse_clock_minute(value: str) -> int:
    parts = value.split(":")
    malformed_parts = any(
        len(part) != 2 or not part.isascii() or not part.isdigit() for part in parts
    )
    if len(parts) != 2 or malformed_parts:
        raise ValueError("first dose time must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("first dose time must use a valid 24-hour clock value")
    return hour * 60 + minute


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _environment_paths(name: str) -> list[Path]:
    value = os.environ.get(name)
    return [Path(item) for item in value.split(os.pathsep) if item] if value else []


def _xdg_base(variable: str, fallback: tuple[str, ...]) -> Path:
    configured = os.environ.get(variable)
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return Path.home().joinpath(*fallback)


def _default_manifest_path() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", (".config",)) / "jebao-flow" / "local-install.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        return _run(parser.parse_args(argv))
    except KeyboardInterrupt:
        print("jebao-flow-local: operator_interrupt", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError):
        print("jebao-flow-local: current_operation_failed_closed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
