"""Write-free command boundary for Q2 measurement epochs.

The CLI never controls the Jebao app and never constructs a hardware writer.  App role/schedule
changes remain a separate attended operation.  This module only commits an epoch manifest, runs
the source-attested read-only collector, classifies preserved evidence, and combines two verified
receipts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import stat
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jebao_flow.config import load_config
from jebao_flow.exact_restore_composition import (
    ExactRestoreCompositionError,
    load_operation_manifest,
)
from jebao_flow.exact_restore_q2_evidence import (
    ExactRestoreQ2EvidenceError,
    ExactRestoreQ2EvidenceVerifier,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.session import DEFAULT_CONTROL_PORT, ReadOnlyGizwitsSession
from jebao_flow.q2_epoch import (
    PilotTimingEvidence,
    Q2EpochError,
    Q2EpochKind,
    Q2EpochStore,
    Q2ScheduleExpectation,
    combine_q2_epoch_receipts,
    derive_q2_thresholds_from_pilots,
    q2_minimum_series_span_ns,
)
from jebao_flow.read_only_collector import (
    CollectorError,
    PilotSeriesStore,
    PilotTerminalError,
    select_capture_pair,
)
from jebao_flow.read_only_collector_cli import verify_collector_source_tree
from jebao_flow.source_attestation import SourceAttestationError

_SPEC_VERSION = 1
_MAX_SPEC_BYTES = 64 * 1024


class Q2CliError(RuntimeError):
    """Privacy-safe command/preflight error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise Q2CliError("command_line_invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="jebao-flow-q2-epoch")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="commit a Q2 manifest before acquisition")
    _add_attestation(prepare)
    _add_private_pair(prepare)
    prepare.add_argument("--epoch-spec", required=True)
    prepare.add_argument("--operation-manifest", required=True)

    prepare_pair = commands.add_parser(
        "prepare-pair",
        help="commit both control and ASYNC manifests before any app write",
    )
    _add_attestation(prepare_pair)
    _add_private_pair(prepare_pair)
    prepare_pair.add_argument("--control-epoch-spec", required=True)
    prepare_pair.add_argument("--async-epoch-spec", required=True)
    prepare_pair.add_argument("--operation-manifest", required=True)

    collect = commands.add_parser("collect", help="run the bound write-free collector plan")
    _add_attestation(collect)
    _add_private_pair(collect)
    _add_manifest_reference(collect)
    collect.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    collect.add_argument("--discovery-port", type=int, default=12414)
    collect.add_argument("--timeout", type=float, default=5.0)

    classify = commands.add_parser("classify", help="verify raw series and commit epoch receipt")
    _add_attestation(classify)
    classify.add_argument("--artifact-root", required=True)
    _add_manifest_reference(classify)
    classify.add_argument("--series-sha256", required=True)
    classify.add_argument("--operation-manifest", required=True)

    combine = commands.add_parser("combine", help="combine verified control and ASYNC receipts")
    _add_attestation(combine)
    combine.add_argument("--artifact-root", required=True)
    for prefix in ("control", "async"):
        combine.add_argument(f"--{prefix}-manifest-id", required=True)
        combine.add_argument(f"--{prefix}-manifest-sha256", required=True)
        combine.add_argument(f"--{prefix}-receipt-sha256", required=True)
    return parser


def _add_attestation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--collector-commit", required=True)


def _add_private_pair(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--artifact-root", required=True)


def _add_manifest_reference(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)


def _read_owner_only_json(path: str | Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise Q2CliError("epoch_spec_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > _MAX_SPEC_BYTES
        ):
            raise Q2CliError("epoch_spec_not_owner_only")
        payload = bytearray()
        while len(payload) <= _MAX_SPEC_BYTES:
            chunk = os.read(descriptor, min(65_536, _MAX_SPEC_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) > _MAX_SPEC_BYTES or (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise Q2CliError("epoch_spec_changed_during_read")
    except OSError as error:
        raise Q2CliError("epoch_spec_unavailable") from error
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Q2CliError("epoch_spec_json_invalid") from error
    if not isinstance(value, dict):
        raise Q2CliError("epoch_spec_claim_invalid")
    return value


def _parse_epoch_spec(
    value: dict[str, Any],
) -> tuple[
    Q2EpochKind,
    int,
    float,
    tuple[PilotTimingEvidence, ...],
    Q2ScheduleExpectation,
]:
    expected_keys = {
        "version",
        "epoch_kind",
        "planned_pair_count",
        "requested_cadence_seconds",
        "timing_evidence",
        "schedule_expectation",
    }
    if set(value) != expected_keys or value.get("version") != _SPEC_VERSION:
        raise Q2CliError("epoch_spec_claim_invalid")
    try:
        kind = Q2EpochKind(value["epoch_kind"])
        count = value["planned_pair_count"]
        cadence = value["requested_cadence_seconds"]
        evidence_claims = value["timing_evidence"]
        schedule_claim = value["schedule_expectation"]
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= 10_000
            or not isinstance(cadence, (int, float))
            or isinstance(cadence, bool)
            or cadence <= 0
            or not math.isfinite(cadence)
            or not isinstance(evidence_claims, list)
            or not isinstance(schedule_claim, dict)
            or set(schedule_claim)
            != {"master_schedule_image_sha256", "slave_schedule_image_sha256"}
        ):
            raise Q2CliError("epoch_spec_claim_invalid")
        evidence = tuple(
            PilotTimingEvidence(
                series_id=claim["series_id"],
                series_sha256=claim["series_sha256"],
            )
            for claim in evidence_claims
            if isinstance(claim, dict) and set(claim) == {"series_id", "series_sha256"}
        )
        if len(evidence) != len(evidence_claims):
            raise Q2CliError("epoch_spec_claim_invalid")
        schedule = Q2ScheduleExpectation(**schedule_claim)
        requested_cadence_ns = round(float(cadence) * 1_000_000_000)
        if (count - 1) * requested_cadence_ns < q2_minimum_series_span_ns(schedule):
            raise Q2CliError("epoch_spec_series_too_short")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, Q2CliError):
            raise
        raise Q2CliError("epoch_spec_claim_invalid") from error
    return kind, count, float(cadence), evidence, schedule


def _attest(commit: str):
    try:
        return verify_collector_source_tree(commit)
    except SourceAttestationError as error:
        raise Q2CliError(error.code) from error


def _stores(root: str | Path) -> tuple[PilotSeriesStore, Q2EpochStore]:
    path = Path(root)
    return PilotSeriesStore(path), Q2EpochStore(path)


def _exact_verifier(path: str | Path) -> ExactRestoreQ2EvidenceVerifier:
    return ExactRestoreQ2EvidenceVerifier(load_operation_manifest(path))


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    attestation = _attest(args.collector_commit)
    config = load_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    collector_store, q2_store = _stores(args.artifact_root)
    kind, count, cadence, timing, schedule = _parse_epoch_spec(
        _read_owner_only_json(args.epoch_spec)
    )
    verifier = _exact_verifier(args.operation_manifest)
    evidence = verifier.derive_q2_exact_restore_evidence()
    requested_cadence_ns = round(cadence * 1_000_000_000)
    thresholds = derive_q2_thresholds_from_pilots(
        collector_store,
        timing,
        requested_cadence_ns=requested_cadence_ns,
        expected_identity_bindings_sha256=tuple(
            target.identity_binding_sha256 for target in targets
        ),
    )
    # Every validation above is read-only.  The first new durable object is now the collector plan;
    # the Q2 manifest immediately binds it before any discovery or TCP connection can occur.
    plan = collector_store.prepare(
        targets,
        source_attestation=attestation,
        planned_pair_count=count,
        requested_cadence_seconds=cadence,
        collector_commit_sha=args.collector_commit,
        epoch=kind.value,
    )
    manifest = q2_store.prepare(
        collector_store,
        plan,
        epoch_kind=kind,
        timing_evidence=timing,
        restore_evidence=evidence,
        restore_evidence_verifier=verifier,
        thresholds=thresholds,
        schedule=schedule,
    )
    return {
        "status": "q2_epoch_prepared_no_network",
        "epoch_kind": kind.value,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "collector_plan_artifact_id": plan.plan_artifact_id,
        "collector_plan_sha256": plan.plan_sha256,
        "collector_series_id": plan.series_id,
        "thresholds": asdict(thresholds),
    }


def _prepare_pair(args: argparse.Namespace) -> dict[str, Any]:
    """Precommit both epoch contexts while the exact-restore handoff is still fresh."""

    attestation = _attest(args.collector_commit)
    config = load_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    collector_store, q2_store = _stores(args.artifact_root)
    control = _parse_epoch_spec(_read_owner_only_json(args.control_epoch_spec))
    async_epoch = _parse_epoch_spec(_read_owner_only_json(args.async_epoch_spec))
    if (
        control[0] is not Q2EpochKind.INDEPENDENT_CONTROL
        or async_epoch[0] is not Q2EpochKind.ASYNC
        or control[1:] != async_epoch[1:]
    ):
        raise Q2CliError("epoch_pair_spec_mismatch")
    _kind, count, cadence, timing, schedule = control
    verifier = _exact_verifier(args.operation_manifest)
    evidence = verifier.derive_q2_exact_restore_evidence()
    bindings = tuple(target.identity_binding_sha256 for target in targets)
    thresholds = derive_q2_thresholds_from_pilots(
        collector_store,
        timing,
        requested_cadence_ns=round(cadence * 1_000_000_000),
        expected_identity_bindings_sha256=bindings,
    )

    prepared: dict[str, dict[str, Any]] = {}
    for kind in (Q2EpochKind.INDEPENDENT_CONTROL, Q2EpochKind.ASYNC):
        plan = collector_store.prepare(
            targets,
            source_attestation=attestation,
            planned_pair_count=count,
            requested_cadence_seconds=cadence,
            collector_commit_sha=args.collector_commit,
            epoch=kind.value,
        )
        manifest = q2_store.prepare(
            collector_store,
            plan,
            epoch_kind=kind,
            timing_evidence=timing,
            restore_evidence=evidence,
            restore_evidence_verifier=verifier,
            thresholds=thresholds,
            schedule=schedule,
        )
        prepared[kind.value] = {
            "manifest_id": manifest.manifest_id,
            "manifest_sha256": manifest.manifest_sha256,
            "collector_plan_artifact_id": plan.plan_artifact_id,
            "collector_plan_sha256": plan.plan_sha256,
            "collector_series_id": plan.series_id,
        }
    return {
        "status": "q2_epoch_pair_prepared_no_network",
        "epochs": prepared,
        "thresholds": asdict(thresholds),
        "final_restore_operation_id": evidence.final_restore_operation_id,
        "final_restore_record_sha256": evidence.final_restore_record_sha256,
    }


async def _collect(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.control_port <= 65_535 or not 1 <= args.discovery_port <= 65_535:
        raise Q2CliError("network_port_invalid")
    if args.timeout <= 0 or not math.isfinite(args.timeout):
        raise Q2CliError("network_timeout_invalid")
    attestation = _attest(args.collector_commit)
    config = load_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    collector_store, q2_store = _stores(args.artifact_root)
    manifest = q2_store.load_manifest(
        args.manifest_id,
        collector_store,
        expected_manifest_sha256=args.manifest_sha256,
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

    metadata = await collector_store.run(
        manifest.collector_plan,
        targets,
        source_attestation=attestation,
        discovery_factory=discovery_factory,
        session_factory=session_factory,
        discovery_timeout_seconds=args.timeout,
    )
    result = asdict(metadata)
    result["status"] = "q2_epoch_collection_completed"
    result["manifest_id"] = manifest.manifest_id
    result["manifest_sha256"] = manifest.manifest_sha256
    return result


def _classify(args: argparse.Namespace) -> dict[str, Any]:
    _attest(args.collector_commit)
    collector_store, q2_store = _stores(args.artifact_root)
    manifest = q2_store.load_manifest(
        args.manifest_id,
        collector_store,
        expected_manifest_sha256=args.manifest_sha256,
    )
    receipt = q2_store.classify_and_commit(
        manifest,
        collector_store,
        expected_series_sha256=args.series_sha256,
        restore_evidence_verifier=_exact_verifier(args.operation_manifest),
    )
    return {
        "status": "q2_epoch_classified",
        "epoch_kind": receipt.epoch_kind.value,
        "conclusion": receipt.conclusion.value,
        "manifest_id": receipt.manifest_id,
        "manifest_sha256": receipt.manifest_sha256,
        "receipt_id": receipt.receipt_id,
        "receipt_sha256": receipt.receipt_sha256,
    }


def _combine(args: argparse.Namespace) -> dict[str, Any]:
    _attest(args.collector_commit)
    collector_store, q2_store = _stores(args.artifact_root)
    control_manifest = q2_store.load_manifest(
        args.control_manifest_id,
        collector_store,
        expected_manifest_sha256=args.control_manifest_sha256,
    )
    async_manifest = q2_store.load_manifest(
        args.async_manifest_id,
        collector_store,
        expected_manifest_sha256=args.async_manifest_sha256,
    )
    control_receipt, _ = q2_store.load_receipt(
        control_manifest,
        expected_receipt_sha256=args.control_receipt_sha256,
    )
    async_receipt, _ = q2_store.load_receipt(
        async_manifest,
        expected_receipt_sha256=args.async_receipt_sha256,
    )
    result = combine_q2_epoch_receipts(
        q2_store,
        control_manifest,
        control_receipt,
        async_manifest,
        async_receipt,
    )
    return {"status": "q2_final_judgment", **asdict(result)}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "prepare":
        return _prepare(args)
    if args.command == "prepare-pair":
        return _prepare_pair(args)
    if args.command == "collect":
        return await _collect(args)
    if args.command == "classify":
        return _classify(args)
    if args.command == "combine":
        return _combine(args)
    raise Q2CliError("command_line_invalid")


def _failure(code: str, *, exit_code: int = 2) -> int:
    print(
        json.dumps({"code": code, "status": "q2_epoch_refused"}, sort_keys=True),
        file=sys.stderr,
    )
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        result = asyncio.run(_run(args))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return _failure("operator_interrupt", exit_code=130)
    except PilotTerminalError as error:
        return _failure(error.code, exit_code=130 if "cancel" in error.code else 2)
    except (
        Q2CliError,
        Q2EpochError,
        CollectorError,
        ExactRestoreCompositionError,
        ExactRestoreQ2EvidenceError,
    ) as error:
        return _failure(error.code)
    except Exception:
        return _failure("private_operation_error")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
