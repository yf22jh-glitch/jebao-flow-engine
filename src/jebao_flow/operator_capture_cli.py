"""Privacy-bounded CLI for offline packet evidence and app capability inspection."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from jebao_flow.operator_capture.app_catalog import public_app_catalog
from jebao_flow.operator_capture.install_prep import (
    CaptureInstallMode,
    InstallPreparationError,
    prepare_fresh_install,
)
from jebao_flow.operator_capture.model import CaptureOrigin
from jebao_flow.operator_capture.paths import durable_private_path
from jebao_flow.operator_capture.pcap import PcapImporter
from jebao_flow.operator_capture.store import (
    CaptureStoreError,
    DurableCaptureSession,
    list_capture_sessions,
    read_public_capture_events,
    verify_capture_session,
)

_CAPTURE_ROOT_ENV = "JEBAO_FLOW_OPERATOR_CAPTURE_ROOT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-observe")
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog = subparsers.add_parser("catalog", help="show the static app capability catalog")
    catalog.add_argument("--product-key", help="restrict output to one lowercase product key")

    ingest = subparsers.add_parser(
        "ingest-pcap",
        help="ingest classic PCAP bytes from stdin into the private evidence root",
    )
    ingest.add_argument(
        "--source-label", required=True, help="public source token for this capture"
    )
    ingest.add_argument("--product-key", help="product binding for bounded semantic decoding")
    ingest.add_argument("--device-port", type=int, default=12416)

    verify = subparsers.add_parser("verify", help="verify a retained capture generation")
    verify.add_argument("session_id")

    events = subparsers.add_parser("events", help="show privacy-bounded capture metadata")
    events.add_argument("session_id")
    events.add_argument("--start-seq", type=int, default=1)
    events.add_argument("--limit", type=int, default=100)
    subparsers.add_parser("list", help="list retained capture generations")

    prepare = subparsers.add_parser(
        "prepare-install",
        help="rederive current installation variables and replace the private manifest",
    )
    prepare.add_argument(
        "--mode",
        choices=[mode.value for mode in CaptureInstallMode],
        default=CaptureInstallMode.OFFLINE_PCAP.value,
    )
    prepare.add_argument("--manifest", type=Path, default=_default_install_manifest())
    prepare.add_argument("--capture-root", type=Path, default=_default_capture_root())
    prepare.add_argument("--mirror-interface")
    return parser


def _capture_root() -> Path:
    value = os.environ.get(_CAPTURE_ROOT_ENV)
    if not value:
        raise CaptureStoreError(f"{_CAPTURE_ROOT_ENV} is required")
    try:
        return durable_private_path(value, "operator capture root")
    except ValueError as error:
        raise CaptureStoreError(str(error)) from error


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _run(args: argparse.Namespace) -> int:
    if args.command == "catalog":
        _print_json(public_app_catalog(args.product_key))
        return 0
    if args.command == "verify":
        _print_json(verify_capture_session(_capture_root(), args.session_id).public_dict())
        return 0
    if args.command == "events":
        _print_json(
            list(
                read_public_capture_events(
                    _capture_root(),
                    args.session_id,
                    start_seq=args.start_seq,
                    limit=args.limit,
                )
            )
        )
        return 0
    if args.command == "list":
        _print_json([item.public_dict() for item in list_capture_sessions(_capture_root())])
        return 0
    if args.command == "ingest-pcap":
        return _ingest(args)
    if args.command == "prepare-install":
        prepared = prepare_fresh_install(
            manifest_path=args.manifest,
            capture_root=args.capture_root,
            capture_mode=CaptureInstallMode(args.mode),
            mirror_interface=args.mirror_interface,
        )
        _print_json(prepared.public_dict())
        return 0
    raise AssertionError("unhandled operator capture command")


def _ingest(args: argparse.Namespace) -> int:
    session = DurableCaptureSession(
        _capture_root(),
        origin=CaptureOrigin.IMPORTED_PCAP,
        source_label=args.source_label,
    )
    try:
        result = PcapImporter(
            session,
            device_port=args.device_port,
            product_key=args.product_key,
        ).ingest(sys.stdin.buffer)
        status = "desynchronized" if result.desynchronized_direction_count else "completed"
        verified = session.seal(status=status)
        _print_json({"capture": verified.public_dict(), "import": result.public_dict()})
        return 2 if status == "desynchronized" else 0
    except (CaptureStoreError, ValueError) as error:
        session_id = session.session_id
        session.close_incomplete()
        try:
            verified = verify_capture_session(_capture_root(), session_id).public_dict()
        except CaptureStoreError:
            verified = {"session_id": session_id, "status": "unverifiable"}
        _print_json(
            {
                "capture": verified,
                "error": type(error).__name__,
                "status": "failed_closed",
            }
        )
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        return _run(parser.parse_args(argv))
    except (CaptureStoreError, InstallPreparationError, KeyError, ValueError) as error:
        print(f"jebao-flow-observe: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("jebao-flow-observe: operator_interrupt", file=sys.stderr)
        return 130
    except Exception:
        print("jebao-flow-observe: private_operation_error", file=sys.stderr)
        return 2


def _default_install_manifest() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(config_home)
        if config_home and Path(config_home).is_absolute()
        else Path.home() / ".config"
    )
    return base / "jebao-flow" / "operator-install.json"


def _default_capture_root() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = (
        Path(state_home)
        if state_home and Path(state_home).is_absolute()
        else Path.home() / ".local" / "state"
    )
    return base / "jebao-flow" / "operator-captures"


if __name__ == "__main__":
    raise SystemExit(main())
