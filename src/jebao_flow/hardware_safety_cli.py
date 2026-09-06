"""Explicit verifier and one-shot provisioner for the shared safety-volume identity."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    hardware_safety_volume_identity_sha256,
    provision_hardware_safety_volume_identity,
    validate_hardware_safety_root,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-hardware-safety")
    parser.add_argument("command", choices=("verify", "provision"))
    return parser


def _print_public(payload: dict[str, object], *, file=None) -> None:
    print(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        file=file,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            validate_hardware_safety_root()
            status = "verified"
        else:
            created = provision_hardware_safety_volume_identity()
            status = "provisioned" if created else "already_provisioned"
        _print_public(
            {
                "identity_sha256": hardware_safety_volume_identity_sha256(),
                "status": status,
            }
        )
        return 0
    except HardwareSafetyRootError as error:
        _print_public(
            {
                "reason": str(error),
                "status": "rejected",
            },
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        _print_public(
            {
                "reason": "operator_interrupt",
                "status": "aborted",
            },
            file=sys.stderr,
        )
        return 130
    except Exception:
        # Unexpected exception text can contain host paths or the private identity.
        _print_public(
            {
                "reason": "private_operation_error",
                "status": "rejected",
            },
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover - installed console script
    raise SystemExit(main())
