"""Explicit operator CLI for one supported local-wavemaker manual-flow qualification."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.compose import validate_current_compose_inputs
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.install import load_current_private_config
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.qualification_producer import (
    CurrentQualificationEpochSource,
    ManualFlowQualificationProducer,
    QualificationArtifactStore,
)
from jebao_flow.local_control.resolver import CurrentEndpointResolver
from jebao_flow.local_control.service import (
    HARDWARE_SAFETY_VOLUME_ID_FILE_ENV,
    read_current_hardware_safety_volume_id,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery

_CONFIG_ENV = "JEBAO_FLOW_CONFIG"
_MANIFEST_ENV = "JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"
_TEMPLATE_ROOTS_ENV = "JEBAO_FLOW_APP_TEMPLATE_ROOTS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-local-qualify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser(
        "run",
        help="run or offline-finalize one supported three-frame manual-flow qualification",
    )
    _add_current_inputs(run)
    run.add_argument("--operation-id", required=True)
    run.add_argument("--target", required=True, help=argparse.SUPPRESS)
    run.add_argument("--downward-step", type=int, required=True)
    run.add_argument("--active-template-sha256", required=True)
    run.add_argument("--qualification-output", type=Path, required=True)
    run.add_argument(
        "--write-enabled",
        action="store_true",
        help="explicitly opt in to the separately approved three-frame operation",
    )

    recover = subparsers.add_parser(
        "recover",
        help="restore one unfinished qualification operation without resuming its test phases",
    )
    _add_current_inputs(recover, include_templates=False)
    recover.add_argument("--operation-id", required=True)
    recover.add_argument(
        "--write-enabled",
        action="store_true",
        help="explicitly opt in to the separately approved recovery operation",
    )
    return parser


def _add_current_inputs(
    parser: argparse.ArgumentParser,
    *,
    include_templates: bool = True,
) -> None:
    parser.add_argument("--config", type=Path, default=_environment_path(_CONFIG_ENV))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_environment_path(_MANIFEST_ENV) or _default_manifest_path(),
    )
    if include_templates:
        parser.add_argument(
            "--template-root",
            action="append",
            type=Path,
            default=None,
        )
    parser.add_argument("--compose-generation", help=argparse.SUPPRESS)


def _run(args: argparse.Namespace) -> int:
    if not args.write_enabled:
        raise ValueError("qualification operation requires explicit write opt-in")
    if args.config is None:
        raise ValueError("current qualification config is required")
    if args.compose_generation:
        validate_current_compose_inputs(args.compose_generation)
    _activate_current_hardware_safety_volume_identity()

    if args.command == "run":
        template_roots = (
            tuple(args.template_root)
            if args.template_root is not None
            else tuple(_environment_paths(_TEMPLATE_ROOTS_ENV))
        )
        if not template_roots:
            raise ValueError("current qualification template root is required")
        epoch_source = CurrentQualificationEpochSource(
            config_path=args.config,
            manifest_path=args.manifest,
            template_roots=template_roots,
            logical_member=args.target,
            active_template_sha256=args.active_template_sha256,
        )
        producer = ManualFlowQualificationProducer(
            epoch_provider=epoch_source,
            resolver_factory=lambda: _resolver(args.config),
            journal=LocalControlJournalStore(),
            lease=LocalControlLease(),
            artifacts=QualificationArtifactStore(args.qualification_output),
        )
        result = asyncio.run(producer.execute(args.operation_id, downward_step=args.downward_step))
        _print_json(result.public_dict())
        return 0

    if args.command == "recover":
        config = load_current_private_config(args.config)
        coordinator = LocalControlCoordinator(
            catalog=build_local_control_catalog(),
            resolver=_resolver_from_config(config),
            journal=LocalControlJournalStore(),
            lease=LocalControlLease(),
        )
        result = asyncio.run(coordinator.recover(args.operation_id))
        _print_json(result.public_dict())
        return 0
    raise AssertionError("unhandled qualification command")


def _resolver(config_path: Path) -> CurrentEndpointResolver:
    return _resolver_from_config(load_current_private_config(config_path))


def _resolver_from_config(config) -> CurrentEndpointResolver:
    return CurrentEndpointResolver(
        GizwitsDiscovery(
            targets=config.observer.targets,
            bind_address=config.observer.bind_address,
        ),
        timeout_seconds=config.observer.discovery_timeout_seconds,
    )


def _activate_current_hardware_safety_volume_identity() -> None:
    identity_file = _environment_path(HARDWARE_SAFETY_VOLUME_ID_FILE_ENV)
    if identity_file is None:
        return
    os.environ[HARDWARE_SAFETY_VOLUME_ID_ENV] = read_current_hardware_safety_volume_id(
        identity_file
    )


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _environment_paths(name: str) -> list[Path]:
    value = os.environ.get(name)
    return [Path(item) for item in value.split(os.pathsep) if item] if value else []


def _default_manifest_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    root = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".config"
    )
    return root / "jebao-flow" / "local-install.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        return _run(parser.parse_args(argv))
    except KeyboardInterrupt:
        print("jebao-flow-local-qualify: operator_interrupt", file=sys.stderr)
        return 130
    except Exception:
        print("jebao-flow-local-qualify: current_operation_failed_closed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
