"""Explicit forward-only CLI for the bounded local-control recovery drill."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from jebao_flow.config import AppConfig, RuntimeMode
from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.compose import validate_current_compose_inputs
from jebao_flow.local_control.install import load_current_private_config
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.models import FreshInstallManifest, LocalDeviceBinding
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.qualification_producer import load_current_install_manifest
from jebao_flow.local_control.recovery_drill import ActualRecoveryForwardDrill
from jebao_flow.local_control.resolver import CurrentEndpointResolver
from jebao_flow.local_control.service import (
    HARDWARE_SAFETY_VOLUME_ID_FILE_ENV,
    read_current_hardware_safety_volume_id,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery

_CONFIG_ENV = "JEBAO_FLOW_CONFIG"
_MANIFEST_ENV = "JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"
_QUALIFICATION_ENV = "JEBAO_FLOW_LOCAL_QUALIFICATIONS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-local-recovery-drill")
    parser.add_argument("--config", type=Path, default=_environment_path(_CONFIG_ENV))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_environment_path(_MANIFEST_ENV) or _default_manifest_path(),
    )
    parser.add_argument(
        "--qualifications",
        type=Path,
        default=_environment_path(_QUALIFICATION_ENV),
    )
    parser.add_argument("--compose-generation", help=argparse.SUPPRESS)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--target", required=True, help=argparse.SUPPRESS)
    parser.add_argument(
        "--write-enabled",
        action="store_true",
        help="explicitly opt in to the separately approved 40-to-39 forward drill",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    if not args.write_enabled:
        raise ValueError("recovery drill requires explicit write opt-in")
    if args.config is None or args.qualifications is None:
        raise ValueError("current recovery drill inputs are required")
    if args.compose_generation:
        validate_current_compose_inputs(args.compose_generation)
    _activate_current_hardware_safety_volume_identity()

    config = load_current_private_config(args.config)
    manifest = load_current_install_manifest(args.manifest)
    target = _require_current_target(config, manifest, args.target)
    qualifications = load_current_qualifications(args.qualifications)
    catalog = build_local_control_catalog(
        qualifications,
        current_content=manifest.content,
    )
    drill = ActualRecoveryForwardDrill(
        catalog=catalog,
        resolver=_resolver_from_config(config),
        journal=LocalControlJournalStore(),
        lease=LocalControlLease(),
    )
    result = asyncio.run(drill.execute(args.operation_id, target))
    _print_json(result.public_dict())
    return 0


def _require_current_target(
    config: AppConfig,
    manifest: FreshInstallManifest,
    logical_member: str,
) -> LocalDeviceBinding:
    if config.runtime.mode is not RuntimeMode.CONTROL or config.runtime.dry_run:
        raise ValueError("recovery drill requires the current live-control config")
    manifest_matches = tuple(
        item for item in manifest.devices if item.logical_member == logical_member
    )
    config_matches = tuple(item for item in config.devices if item.id == logical_member)
    if len(manifest_matches) != 1 or len(config_matches) != 1:
        raise ValueError("recovery drill target is not uniquely current")
    target = manifest_matches[0]
    configured = config_matches[0]
    if (
        not configured.enabled
        or not configured.control.allow_hardware_writes
        or configured.product_key != target.product_key
    ):
        raise ValueError("recovery drill target is not current and write-enabled")
    return target


def _resolver_from_config(config: AppConfig) -> CurrentEndpointResolver:
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
        print("jebao-flow-local-recovery-drill: operator_interrupt", file=sys.stderr)
        return 130
    except Exception:
        print("jebao-flow-local-recovery-drill: current_operation_failed_closed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
