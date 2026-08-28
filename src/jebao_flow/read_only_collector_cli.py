"""Bounded source-module CLI for the identity-bound, write-free pilot collector.

This module is deliberately not installed as a console entry point.  The first pilot must invoke
the exact committed source module from a fresh checkout so an older installed wrapper cannot make
provenance claims on behalf of newer repository bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from jebao_flow.config import load_config
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.session import DEFAULT_CONTROL_PORT, ReadOnlyGizwitsSession
from jebao_flow.read_only_collector import (
    CollectorError,
    CollectorPreflightError,
    PilotSeriesStore,
    PilotTerminalError,
    select_capture_pair,
)
from jebao_flow.source_attestation import (
    CollectorSourceAttestation,
    SourceAttestationError,
    attest_collector_source_tree,
)


def verify_collector_source_tree(
    expected_commit: str,
    *,
    cwd: Path | None = None,
) -> CollectorSourceAttestation:
    """Bind the pilot to the exact clean tracked commit executing the collector."""

    try:
        return attest_collector_source_tree(expected_commit, cwd=cwd)
    except SourceAttestationError as error:
        raise CollectorPreflightError(error.code) from error


class _SafeArgumentParser(argparse.ArgumentParser):
    """Never echo private argv values in parse failures."""

    def error(self, _message: str) -> None:
        raise CollectorPreflightError("command_line_invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="jebao-flow-readonly-collect")
    parser.add_argument("--config", required=True, help="private locked deployment config")
    parser.add_argument("--first", required=True, help="first logical device id")
    parser.add_argument("--second", required=True, help="second logical device id")
    parser.add_argument("--output-root", required=True, help="owner-only private artifact root")
    parser.add_argument("--samples", required=True, type=int, help="predeclared pair count")
    parser.add_argument(
        "--cadence-seconds",
        required=True,
        type=float,
        help="fixed monotonic interval between pair starts",
    )
    parser.add_argument(
        "--collector-commit",
        required=True,
        help="clean lowercase Git commit containing this collector",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=DEFAULT_CONTROL_PORT,
        help="device TCP control port",
    )
    parser.add_argument("--discovery-port", type=int, default=12414)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not 1 <= args.control_port <= 65535 or not 1 <= args.discovery_port <= 65535:
        raise CollectorError("network_port_invalid")
    if args.timeout <= 0 or not math.isfinite(args.timeout):
        raise CollectorError("network_timeout_invalid")
    if args.cadence_seconds <= 0 or not math.isfinite(args.cadence_seconds):
        raise CollectorError("pilot_cadence_invalid")

    source_attestation = verify_collector_source_tree(args.collector_commit)
    config = load_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    store = PilotSeriesStore(Path(args.output_root))
    plan = store.prepare(
        targets,
        source_attestation=source_attestation,
        planned_pair_count=args.samples,
        requested_cadence_seconds=args.cadence_seconds,
        collector_commit_sha=args.collector_commit,
    )

    def discovery_factory() -> GizwitsDiscovery:
        return GizwitsDiscovery(
            targets=config.observer.targets,
            bind_address=config.observer.bind_address,
            port=args.discovery_port,
        )

    def session_factory(address: str) -> ReadOnlyGizwitsSession:
        return ReadOnlyGizwitsSession(
            address,
            port=args.control_port,
            connect_timeout_seconds=args.timeout,
            response_timeout_seconds=args.timeout,
        )

    metadata = await store.run(
        plan,
        targets,
        source_attestation=source_attestation,
        discovery_factory=discovery_factory,
        session_factory=session_factory,
        discovery_timeout_seconds=args.timeout,
    )
    print(json.dumps(asdict(metadata), ensure_ascii=False, sort_keys=True))
    return 0 if metadata.status == "pilot_completed_all_acquisitions_accepted" else 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return asyncio.run(_run(args))
    except PilotTerminalError as error:
        print(
            json.dumps(
                {
                    "abort_sha256": error.abort_sha256,
                    "code": error.code,
                    "durability_unknown": error.durability_unknown,
                    "plan_artifact_id": error.plan_artifact_id,
                    "plan_sha256": error.plan_sha256,
                    "q2_boundary_classification": "not_authorized",
                    "series_id": error.series_id,
                    "status": "pilot_aborted_not_q2_boundary",
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        if error.code in {
            "capture_cancelled_after_read",
            "capture_cancelled",
            "keyboard_interrupt",
        }:
            return 130
        return 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("capture aborted: operator_interrupt", file=sys.stderr)
        return 130
    except CollectorError as error:
        print(f"capture failed: {error.code}", file=sys.stderr)
        return 2
    except Exception:
        # Raw exception strings can contain private addresses, identifiers or absolute paths.
        print("capture failed: private_operation_error", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - installed console script
    raise SystemExit(main())
