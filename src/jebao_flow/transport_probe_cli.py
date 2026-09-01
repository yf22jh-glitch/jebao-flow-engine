"""Run one identity-bound strict-read transport probe from exact committed source.

This module is deliberately not installed as a console entry point. Invoke it with
``python -B -P -m jebao_flow.transport_probe_cli`` from the exact clean checkout.
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

from jebao_flow.capability_matrix import (
    CapabilityClaimError,
    SourcePin,
    verify_source_pin,
)
from jebao_flow.config import load_config
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.session import DEFAULT_CONTROL_PORT, ReadOnlyGizwitsSession
from jebao_flow.read_only_collector import CollectorError, select_capture_pair
from jebao_flow.read_only_collector_cli import verify_collector_source_tree
from jebao_flow.source_attestation import (
    CollectorSourceAttestation,
    SourceAttestationError,
    validate_collector_source_attestation,
)
from jebao_flow.transport_probe import (
    TransportProbeError,
    TransportProbeStore,
    run_transport_probe,
)

_LINKAGE_CHOICES = ("independent", "master", "sync_slave", "async_slave")

PROBE_PINNED_SOURCES: tuple[SourcePin, ...] = (
    ("jebao_flow.capability_matrix", "src/jebao_flow/capability_matrix.py"),
    ("jebao_flow.config", "src/jebao_flow/config.py"),
    ("jebao_flow.physical_identity", "src/jebao_flow/physical_identity.py"),
    ("jebao_flow.protocol.codec", "src/jebao_flow/protocol/codec.py"),
    ("jebao_flow.protocol.discovery", "src/jebao_flow/protocol/discovery.py"),
    ("jebao_flow.protocol.models", "src/jebao_flow/protocol/models.py"),
    ("jebao_flow.protocol.profiles", "src/jebao_flow/protocol/profiles.py"),
    ("jebao_flow.protocol.schedule", "src/jebao_flow/protocol/schedule.py"),
    ("jebao_flow.protocol.schedule_wire", "src/jebao_flow/protocol/schedule_wire.py"),
    ("jebao_flow.protocol.schema", "src/jebao_flow/protocol/schema.py"),
    ("jebao_flow.protocol.session", "src/jebao_flow/protocol/session.py"),
    ("jebao_flow.read_only_collector", "src/jebao_flow/read_only_collector.py"),
    ("jebao_flow.transport_probe", "src/jebao_flow/transport_probe.py"),
    ("jebao_flow.transport_probe_cli", "src/jebao_flow/transport_probe_cli.py"),
)


class _SafeArgumentParser(argparse.ArgumentParser):
    """Never echo a private argv value in a parse failure."""

    def error(self, _message: str) -> None:
        raise TransportProbeError("transport_probe_command_line_invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="jebao-flow-transport-probe")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--first-expected-linkage", choices=_LINKAGE_CHOICES, required=True)
    parser.add_argument("--second-expected-linkage", choices=_LINKAGE_CHOICES, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--probe-commit", required=True)
    parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    parser.add_argument("--discovery-port", type=int, default=12414)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser


def _verify_probe_source_tree(
    commit_sha: str,
    repo_root: Path,
) -> tuple[CollectorSourceAttestation, str]:
    attestation = verify_collector_source_tree(commit_sha, cwd=repo_root)
    source_digest = verify_source_pin(
        repo_root,
        commit_sha,
        PROBE_PINNED_SOURCES,
        self_module_name="jebao_flow.transport_probe_cli",
        self_module_file=__file__,
    )
    return attestation, source_digest


def _revalidate_before_network(
    attestation: CollectorSourceAttestation,
    *,
    commit_sha: str,
    repo_root: Path,
    expected_probe_digest: str,
) -> None:
    validate_collector_source_attestation(attestation, expected_commit=commit_sha)
    current_probe_digest = verify_source_pin(
        repo_root,
        commit_sha,
        PROBE_PINNED_SOURCES,
        self_module_name="jebao_flow.transport_probe_cli",
        self_module_file=__file__,
    )
    if current_probe_digest != expected_probe_digest:
        raise TransportProbeError("transport_probe_source_attestation_stale")


async def _run(args: argparse.Namespace) -> int:
    if not 1 <= args.control_port <= 65535 or not 1 <= args.discovery_port <= 65535:
        raise TransportProbeError("transport_probe_network_port_invalid")
    if args.timeout <= 0 or not math.isfinite(args.timeout):
        raise TransportProbeError("transport_probe_network_timeout_invalid")

    source_attestation, probe_source_digest = _verify_probe_source_tree(
        args.probe_commit,
        args.repo_root,
    )
    config = load_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    store = TransportProbeStore(args.output_root)
    reference = store.prepare(
        targets,
        commit_sha=args.probe_commit,
        collector_source_digest_sha256=(
            source_attestation.runtime_source_digest_sha256
        ),
        probe_source_digest_sha256=probe_source_digest,
        response_timeout_seconds=args.timeout,
        expected_linkages=(
            args.first_expected_linkage,
            args.second_expected_linkage,
        ),
    )

    _revalidate_before_network(
        source_attestation,
        commit_sha=args.probe_commit,
        repo_root=args.repo_root,
        expected_probe_digest=probe_source_digest,
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

    metadata = await run_transport_probe(
        reference,
        store,
        targets,
        discovery_factory=discovery_factory,
        session_factory=session_factory,
        discovery_timeout_seconds=args.timeout,
    )
    print(json.dumps(asdict(metadata), ensure_ascii=True, sort_keys=True))
    return 0 if metadata.status == "probe_completed_context_valid" else 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return asyncio.run(_run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("transport_probe_operator_interrupt", file=sys.stderr)
        return 130
    except (
        CapabilityClaimError,
        CollectorError,
        SourceAttestationError,
        TransportProbeError,
    ) as error:
        print(error.code, file=sys.stderr)
        return 2
    except Exception:
        # Exception text can contain private addresses, identifiers, or filesystem paths.
        print("transport_probe_private_operation_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
