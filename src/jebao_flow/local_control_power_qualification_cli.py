"""Explicit operator CLI for one safer-OFF local-control power qualification."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV
from jebao_flow.local_control.compose import validate_current_compose_inputs
from jebao_flow.local_control.install import load_current_private_config
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.power_qualification_producer import PowerQualificationProducer
from jebao_flow.local_control.qualification_producer import (
    CurrentQualificationEpochSource,
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


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: current_operation_failed_closed\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _PrivateArgumentParser(prog="jebao-flow-local-power-qualify")
    parser.add_argument("--config", type=Path, default=_environment_path(_CONFIG_ENV))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_environment_path(_MANIFEST_ENV) or _default_manifest_path(),
    )
    parser.add_argument("--template-root", action="append", type=Path, default=None)
    parser.add_argument("--compose-generation", help=argparse.SUPPRESS)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--target", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--active-template-sha256", required=True)
    parser.add_argument("--qualification-output", type=Path, required=True)
    parser.add_argument(
        "--write-enabled",
        action="store_true",
        help="explicitly opt in to the separately approved ON/OFF/ON operation",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    if not args.write_enabled:
        raise ValueError("power qualification requires explicit write opt-in")
    if args.config is None:
        raise ValueError("current power qualification config is required")
    if args.compose_generation:
        validate_current_compose_inputs(args.compose_generation)
    _activate_current_hardware_safety_volume_identity()

    template_roots = (
        tuple(args.template_root)
        if args.template_root is not None
        else tuple(_environment_paths(_TEMPLATE_ROOTS_ENV))
    )
    if not template_roots:
        raise ValueError("current power qualification template root is required")
    epoch_source = CurrentQualificationEpochSource(
        config_path=args.config,
        manifest_path=args.manifest,
        template_roots=template_roots,
        logical_member=args.target,
        active_template_sha256=args.active_template_sha256,
    )
    producer = PowerQualificationProducer(
        epoch_provider=epoch_source,
        resolver_factory=lambda: _resolver(args.config),
        journal=LocalControlJournalStore(),
        lease=LocalControlLease(),
        artifacts=QualificationArtifactStore(args.qualification_output),
    )
    result = asyncio.run(producer.execute(args.operation_id))
    _print_json(result.public_dict())
    return 0


def _resolver(config_path: Path) -> CurrentEndpointResolver:
    config = load_current_private_config(config_path)
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
        print("jebao-flow-local-power-qualify: operator_interrupt", file=sys.stderr)
        return 130
    except Exception:
        print(
            "jebao-flow-local-power-qualify: current_operation_failed_closed",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
