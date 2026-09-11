"""CLI for one offline immutable local-control qualification-set merge."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.qualification_set import (
    QualificationSetMerger,
    expected_additions_from_manifest,
    load_current_manifest,
)
from jebao_flow.local_control.service import (
    HARDWARE_SAFETY_VOLUME_ID_FILE_ENV,
    read_current_hardware_safety_volume_id,
)

_CURRENT_ENV = "JEBAO_FLOW_LOCAL_QUALIFICATIONS"
_MANIFEST_ENV = "JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: current_operation_failed_closed\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _PrivateArgumentParser(prog="jebao-flow-local-qualification-set")
    parser.add_argument(
        "--current",
        type=Path,
        default=_environment_path(_CURRENT_ENV),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        required=True,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_environment_path(_MANIFEST_ENV) or _default_manifest_path(),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--expect-add",
        action="append",
        nargs=2,
        required=True,
        metavar=("LOGICAL_MEMBER", "FUNCTION"),
        help=argparse.SUPPRESS,
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    _activate_current_hardware_safety_volume_identity()
    current = load_current_qualifications(args.current)
    candidate = load_current_qualifications(args.candidate)
    manifest = load_current_manifest(args.manifest)
    additions = expected_additions_from_manifest(
        manifest,
        tuple((member, function) for member, function in args.expect_add),
    )
    result = QualificationSetMerger(args.output).merge(current, candidate, additions)
    _print_json(result.public_dict())
    return 0


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _activate_current_hardware_safety_volume_identity() -> None:
    identity_file = _environment_path(HARDWARE_SAFETY_VOLUME_ID_FILE_ENV)
    if identity_file is None:
        return
    os.environ[HARDWARE_SAFETY_VOLUME_ID_ENV] = read_current_hardware_safety_volume_id(
        identity_file
    )


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
        print("jebao-flow-local-qualification-set: operator_interrupt", file=sys.stderr)
        return 130
    except Exception:
        print(
            "jebao-flow-local-qualification-set: current_operation_failed_closed",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
