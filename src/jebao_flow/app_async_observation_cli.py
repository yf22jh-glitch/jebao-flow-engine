"""Source-only CLI for the precommitted app-driven ASYNC observation.

The CLI prepares and consumes read-only collector plans.  It never imports the native linkage
experiment harness and never sends a device control frame.  The only live state changes in this
workflow are explicit human actions in the vendor application and separately authorized exact
restore operations executed by their own tool.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from jebao_flow import app_async_observation
from jebao_flow.app_async_observation import (
    AppAsyncObservationError,
    AppAsyncObservationStore,
    AppAsyncStage,
    parse_action_attestation,
    parse_series_digest_map,
)
from jebao_flow.exact_restore_composition import (
    ExactRestoreCompositionError,
    load_locked_config,
    load_operation_manifest,
)
from jebao_flow.exact_restore_q2_evidence import (
    ExactRestoreQ2EvidenceError,
    ExactRestoreQ2EvidenceVerifier,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.session import DEFAULT_CONTROL_PORT, ReadOnlyGizwitsSession
from jebao_flow.read_only_collector import (
    CaptureTransportPolicy,
    CollectorError,
    CollectorPreflightError,
    PilotSeriesStore,
    PilotTerminalError,
    select_capture_pair,
)
from jebao_flow.read_only_collector_cli import verify_collector_source_tree
from jebao_flow.source_attestation import CollectorSourceAttestation, SourceAttestationError

_MAX_SPEC_BYTES = 512 * 1024
_APP_RUNTIME_SOURCES = (
    ("src/jebao_flow/app_async_observation.py", app_async_observation.__file__),
    ("src/jebao_flow/app_async_observation_cli.py", __file__),
)
_CHECKPOINT_STAGES = (
    AppAsyncStage.SCHEDULES_ARMED,
    AppAsyncStage.SYNC_SELECTED,
    AppAsyncStage.ASYNC_SELECTED,
)


class AppAsyncCliError(RuntimeError):
    """Privacy-safe command failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _PrepareSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_nonce: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    master_boundary_local: datetime
    checkpoint_pair_count: int = Field(ge=1, le=512)
    checkpoint_cadence_seconds: float = Field(gt=0)
    observation_pair_count: int = Field(ge=3, le=512)
    observation_cadence_seconds: float = Field(gt=0)

    @field_validator("master_boundary_local")
    @classmethod
    def require_naive_exact_minute(cls, value: datetime) -> datetime:
        if value.tzinfo is not None or value.second != 0 or value.microsecond != 0:
            raise ValueError("master boundary must be a naive exact minute")
        return value

    @field_validator("checkpoint_cadence_seconds", "observation_cadence_seconds")
    @classmethod
    def require_finite_cadence(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("cadence must be finite")
        return value

    def validate_observation_span(self) -> Self:
        # One pre-boundary point and one post-(T+5 minute)-boundary point require at least the
        # five-minute offset plus both exact boundary minutes that the reviewed plan excludes.
        planned_span = (self.observation_pair_count - 1) * self.observation_cadence_seconds
        if planned_span < 7 * 60:
            raise AppAsyncCliError("app_async_observation_plan_span_too_short")
        return self


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise AppAsyncCliError("command_line_invalid")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--exact-operation-manifest", required=True)
    parser.add_argument("--collector-commit", required=True)


def _add_manifest_reference(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="jebao-flow-app-async-observation")
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_SafeArgumentParser,
    )

    prepare = commands.add_parser("prepare")
    _add_common(prepare)
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--first", required=True)
    prepare.add_argument("--second", required=True)
    prepare.add_argument("--prepare-spec", required=True)

    collect = commands.add_parser("collect")
    _add_common(collect)
    _add_manifest_reference(collect)
    collect.add_argument("--config", required=True)
    collect.add_argument("--first", required=True)
    collect.add_argument("--second", required=True)
    collect.add_argument(
        "--stage",
        choices=tuple(stage.value for stage in AppAsyncStage),
        required=True,
    )
    collect.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    collect.add_argument("--discovery-port", type=int, default=12414)
    collect.add_argument("--timeout", type=float, default=5.0)

    checkpoints = commands.add_parser("verify-checkpoints")
    _add_common(checkpoints)
    _add_manifest_reference(checkpoints)
    checkpoints.add_argument("--evidence", required=True)
    checkpoints.add_argument(
        "--through-stage",
        choices=tuple(stage.value for stage in _CHECKPOINT_STAGES),
        required=True,
    )

    classify = commands.add_parser("classify")
    _add_common(classify)
    _add_manifest_reference(classify)
    classify.add_argument("--evidence", required=True)
    return parser


def _read_owner_only_json(path: str | Path) -> dict[str, Any]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        named = Path(path).lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_size > _MAX_SPEC_BYTES
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise AppAsyncCliError("app_async_private_json_invalid")
        payload = bytearray()
        while len(payload) <= _MAX_SPEC_BYTES:
            chunk = os.read(descriptor, min(65_536, _MAX_SPEC_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named_after = Path(path).lstat()
        if (
            len(payload) > _MAX_SPEC_BYTES
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)
        ):
            raise AppAsyncCliError("app_async_private_json_changed")
    except AppAsyncCliError:
        raise
    except (FileNotFoundError, OSError) as error:
        raise AppAsyncCliError("app_async_private_json_unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppAsyncCliError("app_async_private_json_invalid") from error
    if not isinstance(value, dict):
        raise AppAsyncCliError("app_async_private_json_invalid")
    return value


def _run_git(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise AppAsyncCliError("app_async_verifier_repository_unavailable") from error


def _attest_app_sources(
    expected_commit: str,
) -> tuple[CollectorSourceAttestation, str]:
    """Bind both the existing collector and these two exact source modules."""

    try:
        collector_attestation = verify_collector_source_tree(expected_commit)
    except (CollectorPreflightError, SourceAttestationError) as error:
        raise AppAsyncCliError(getattr(error, "code", "app_async_source_invalid")) from error
    try:
        root = Path(
            _run_git(Path(__file__).parent, "rev-parse", "--show-toplevel").decode("utf-8").strip()
        ).resolve(strict=True)
    except (OSError, UnicodeDecodeError) as error:
        raise AppAsyncCliError("app_async_verifier_repository_unavailable") from error
    if _run_git(root, "rev-parse", "HEAD").decode("ascii").strip() != expected_commit:
        raise AppAsyncCliError("app_async_verifier_commit_mismatch")
    source_digests: list[tuple[str, str]] = []
    for relative, reported in _APP_RUNTIME_SOURCES:
        if not isinstance(reported, str):
            raise AppAsyncCliError("app_async_verifier_source_path_invalid")
        expected_path = (root / relative).resolve(strict=True)
        if Path(os.path.abspath(reported)).resolve(strict=True) != expected_path:
            raise AppAsyncCliError("app_async_verifier_source_path_invalid")
        payload = expected_path.read_bytes()
        committed = _run_git(root, "show", f"{expected_commit}:{relative}")
        if payload != committed:
            raise AppAsyncCliError("app_async_verifier_source_blob_mismatch")
        source_digests.append((relative, hashlib.sha256(payload).hexdigest()))
    digest = hashlib.sha256(
        json.dumps(
            source_digests,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    return collector_attestation, digest


def _exact_verifier(path: str | Path) -> ExactRestoreQ2EvidenceVerifier:
    return ExactRestoreQ2EvidenceVerifier(load_operation_manifest(path))


def _stores(root: str | Path) -> tuple[PilotSeriesStore, AppAsyncObservationStore]:
    path = Path(root)
    return PilotSeriesStore(path), AppAsyncObservationStore(path)


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    source_attestation, verifier_source_digest = _attest_app_sources(args.collector_commit)
    try:
        spec = _PrepareSpec.model_validate(_read_owner_only_json(args.prepare_spec))
    except (TypeError, ValueError) as error:
        raise AppAsyncCliError("app_async_prepare_spec_invalid") from error
    spec.validate_observation_span()
    config = load_locked_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    collector_store, app_store = _stores(args.artifact_root)
    restore_verifier = _exact_verifier(args.exact_operation_manifest)
    # Verify the complete staged restore before creating any durable collector plan.
    restore_verifier.derive_q2_exact_restore_evidence()
    plans = {}
    for stage in AppAsyncStage:
        observation = stage is AppAsyncStage.OBSERVATION
        plans[stage] = collector_store.prepare(
            targets,
            source_attestation=source_attestation,
            planned_pair_count=(
                spec.observation_pair_count if observation else spec.checkpoint_pair_count
            ),
            requested_cadence_seconds=(
                spec.observation_cadence_seconds if observation else spec.checkpoint_cadence_seconds
            ),
            collector_commit_sha=args.collector_commit,
            epoch=f"app_async_{stage.value}",
            transport_policy=(
                CaptureTransportPolicy.REPLY_OR_REPORT
                if observation
                else CaptureTransportPolicy.EXPLICIT_ONLY
            ),
        )
    reference = app_store.prepare(
        collector_store,
        plans,
        master_boundary_local=spec.master_boundary_local,
        operation_nonce=spec.operation_nonce,
        verifier_commit_sha=args.collector_commit,
        verifier_runtime_source_digest_sha256=verifier_source_digest,
        restore_verifier=restore_verifier,
    )
    return {
        "status": "app_async_observation_prepared_no_network",
        "manifest_id": reference.manifest_id,
        "manifest_sha256": reference.manifest_sha256,
        "operation_id": reference.operation_id,
        "verifier_runtime_source_digest_sha256": verifier_source_digest,
        "collector_plans": {
            stage.value: {
                "plan_artifact_id": plans[stage].plan_artifact_id,
                "plan_sha256": plans[stage].plan_sha256,
                "series_id": plans[stage].series_id,
            }
            for stage in AppAsyncStage
        },
        "network_contact": False,
        "device_writes": 0,
        "ha_actions": 0,
    }


def _load_bound(
    args: argparse.Namespace,
) -> tuple[
    CollectorSourceAttestation,
    PilotSeriesStore,
    AppAsyncObservationStore,
    Any,
    ExactRestoreQ2EvidenceVerifier,
]:
    source_attestation, verifier_source_digest = _attest_app_sources(args.collector_commit)
    collector_store, app_store = _stores(args.artifact_root)
    reference = app_store.load_manifest(
        args.manifest_id,
        collector_store,
        expected_manifest_sha256=args.manifest_sha256,
    )
    restore_verifier = _exact_verifier(args.exact_operation_manifest)
    manifest = app_store.verify_restore_ready(
        reference,
        collector_store,
        restore_verifier=restore_verifier,
    )
    if (
        manifest.verifier_commit_sha != args.collector_commit
        or manifest.verifier_runtime_source_digest_sha256 != verifier_source_digest
    ):
        raise AppAsyncCliError("app_async_verifier_source_binding_mismatch")
    return source_attestation, collector_store, app_store, reference, restore_verifier


async def _collect(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.control_port <= 65535 or not 1 <= args.discovery_port <= 65535:
        raise AppAsyncCliError("network_port_invalid")
    if args.timeout <= 0 or not math.isfinite(args.timeout):
        raise AppAsyncCliError("network_timeout_invalid")
    source, collector_store, app_store, reference, restore_verifier = _load_bound(args)
    stage = AppAsyncStage(args.stage)
    manifest = app_store.verify_restore_ready(
        reference,
        collector_store,
        restore_verifier=restore_verifier,
    )
    binding = next(item for item in manifest.collector_plans if item.stage is stage)
    plan = collector_store.load(binding.series_id)
    config = load_locked_config(args.config)
    targets = select_capture_pair(config, args.first, args.second)
    if (
        tuple(target.identity_binding_sha256 for target in targets)
        != manifest.expected_identity_bindings_sha256
    ):
        raise AppAsyncCliError("app_async_runtime_identity_mismatch")

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
        plan,
        targets,
        source_attestation=source,
        discovery_factory=discovery_factory,
        session_factory=session_factory,
        discovery_timeout_seconds=args.timeout,
    )
    return {
        "status": metadata.status,
        "stage": stage.value,
        "manifest_id": reference.manifest_id,
        "manifest_sha256": reference.manifest_sha256,
        "operation_id": reference.operation_id,
        "collector": asdict(metadata),
        "device_writes": 0,
        "ha_actions": 0,
    }


def _evidence_map(payload: dict[str, Any]) -> dict[AppAsyncStage, str]:
    value = payload.get("series_sha256_by_stage")
    return parse_series_digest_map(value)


def _verify_checkpoints(args: argparse.Namespace) -> dict[str, Any]:
    _source, collector_store, app_store, reference, restore_verifier = _load_bound(args)
    series = _evidence_map(_read_owner_only_json(args.evidence))
    return app_store.verify_checkpoints(
        reference,
        collector_store,
        series_sha256_by_stage=series,
        through_stage=AppAsyncStage(args.through_stage),
        restore_verifier=restore_verifier,
    )


def _classify(args: argparse.Namespace) -> dict[str, Any]:
    _source, collector_store, app_store, reference, restore_verifier = _load_bound(args)
    payload = _read_owner_only_json(args.evidence)
    series = _evidence_map(payload)
    attestation = parse_action_attestation(payload.get("action_attestation"))
    receipt = app_store.classify_and_commit(
        reference,
        collector_store,
        series_sha256_by_stage=series,
        action_attestation=attestation,
        restore_verifier=restore_verifier,
    )
    return {
        "status": "app_async_observation_classified_from_preserved_raw",
        "receipt_id": receipt.receipt_id,
        "receipt_sha256": receipt.receipt_sha256,
        "manifest_id": receipt.manifest_id,
        "manifest_sha256": receipt.manifest_sha256,
        "operation_id": receipt.operation_id,
        "conclusion": receipt.conclusion.value,
        "stability_duration_claimed": False,
        "physical_claim_scope": (app_async_observation.APP_ASYNC_PHYSICAL_CLAIM_SCOPE),
        "device_writes": 0,
        "ha_actions": 0,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "prepare":
        return _prepare(args)
    if args.command == "collect":
        return await _collect(args)
    if args.command == "verify-checkpoints":
        return _verify_checkpoints(args)
    if args.command == "classify":
        return _classify(args)
    raise AppAsyncCliError("command_line_invalid")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = asyncio.run(_run(build_parser().parse_args(argv)))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        if result.get("status") == "pilot_completed_with_rejections":
            return 1
        return 0
    except PilotTerminalError as error:
        print(
            json.dumps(
                {
                    "status": "app_async_read_only_capture_aborted",
                    "code": error.code,
                    "series_id": error.series_id,
                    "plan_sha256": error.plan_sha256,
                    "abort_sha256": error.abort_sha256,
                    "device_writes": 0,
                    "ha_actions": 0,
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
        print("app async observation aborted: operator_interrupt", file=sys.stderr)
        return 130
    except (
        AppAsyncCliError,
        AppAsyncObservationError,
        CollectorError,
        ExactRestoreCompositionError,
        ExactRestoreQ2EvidenceError,
        SourceAttestationError,
    ) as error:
        print(
            f"app async observation failed: {getattr(error, 'code', 'private_operation_error')}",
            file=sys.stderr,
        )
        return 2
    except Exception:
        print("app async observation failed: private_operation_error", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - source module entry point
    raise SystemExit(main())
