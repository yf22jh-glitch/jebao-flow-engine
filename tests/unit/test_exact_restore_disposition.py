from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from jebao_flow.exact_restore import (
    ExactRestoreActionOutcome,
    ExactRestoreActionResult,
    ExactRestoreAuthorityActivation,
    ExactRestoreController,
    ExactRestoreCycle,
    ExactRestoreErrorCode,
    ExactRestorePhase,
    ExactRestorePreflightError,
    ExactRestoreRecord,
    _receipt_from_final_verified_record,
    prepare_exact_restore_record,
    prepare_qualified_final_restore_record,
)
from jebao_flow.exact_restore_composition import (
    build_verified_baseline,
    select_operation_targets,
)
from jebao_flow.exact_restore_disposition import (
    _ALLOWED_ACTION_OUTCOMES,
    AttendedDispositionIssuer,
    ExactRestoreDispositionArchive,
    ExactRestoreDispositionError,
    ParkedDispositionAuthority,
    ParkedDispositionEvidenceReference,
    ParkedDispositionManifest,
    ParkedDispositionReason,
    ParkedDispositionService,
    ParkedDispositionTombstone,
    load_parked_disposition_manifest,
    public_parked_disposition,
    public_parked_tombstone_status,
)
from jebao_flow.exact_restore_receipts import (
    ExactRestoreOperationFinalization,
    ExactRestoreOperationFinalizationConflictError,
    ExactRestoreReceiptArchiveError,
)
from jebao_flow.exact_restore_store import (
    ExactRestoreJournalClaimError,
    ExactRestoreJournalStore,
)
from jebao_flow.read_only_collector import (
    PilotSeriesStore,
    VerifiedPilotInterval,
    VerifiedPilotPairArtifact,
)
from jebao_flow.recovery_supervisor import _ExactRestoreArtifactPresence
from tests.unit.test_exact_restore import (
    BOOT_A_SHA256,
    FakeGuard,
    RestoreHarness,
    _armed_controller,
    _authority,
    _lost_barrier_operation_id,
    _promoted_record,
    _safe_targets,
)
from tests.unit.test_exact_restore_composition import (
    NOW,
    _artifact,
    _config,
)
from tests.unit.test_exact_restore_composition import (
    _manifest as _restore_manifest,
)
from tests.unit.test_exact_restore_composition import (
    _split_manifest as _split_restore_manifest,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class _CollectorStore:
    def __init__(self, artifact: VerifiedPilotPairArtifact) -> None:
        self.artifact = artifact
        self.loads = 0
        self.extractions = 0

    def load(self, series_id: str) -> SimpleNamespace:
        assert series_id == self.artifact.series_id
        self.loads += 1
        return SimpleNamespace(
            plan_artifact_id=self.artifact.plan_artifact_id,
            plan_sha256=self.artifact.plan_sha256,
            series_id=self.artifact.series_id,
        )

    def extract_verified_accepted_pair(
        self,
        reference: SimpleNamespace,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ) -> VerifiedPilotPairArtifact:
        assert reference.series_id == self.artifact.series_id
        assert expected_series_sha256 == self.artifact.series_sha256
        assert ordinal == self.artifact.ordinal
        self.extractions += 1
        return self.artifact


def _awaiting_record_and_artifact(
    *,
    operation_id: str = "operation-parked-qualification",
) -> tuple[ExactRestoreRecord, VerifiedPilotPairArtifact]:
    restore_manifest = _restore_manifest()
    config = _config()
    artifact = _artifact(config)
    baseline = build_verified_baseline(
        restore_manifest,
        select_operation_targets(config, restore_manifest),
        artifact,
        now=NOW,
    )
    prepared = prepare_exact_restore_record(
        baseline,
        _safe_targets(),
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=operation_id,
        now=NOW,
    )
    authority = _authority(
        prepared,
        now=NOW,
        monotonic_now=1_000_000_000,
        boot_identity_sha256=BOOT_A_SHA256,
    )
    activation = ExactRestoreAuthorityActivation(
        authority_sha256=authority.authority_sha256,
        boot_identity_sha256=authority.boot_identity_sha256,
        accepted_wall=authority.issued_at,
        accepted_monotonic_ns=authority.issued_monotonic_ns,
        deadline_monotonic_ns=authority.deadline_monotonic_ns,
    )
    completed_at = NOW + timedelta(seconds=1)
    completed = tuple(
        ExactRestoreActionResult(
            index=action.index,
            action_id=action.action_id,
            outcome=ExactRestoreActionOutcome.WRITTEN_VERIFIED,
            pre_state_sha256=_digest(f"pre-{action.index}"),
            post_state_sha256=_digest(f"post-{action.index}"),
            completed_at=completed_at,
        )
        for action in prepared.actions
    )
    payload = prepared.model_dump(mode="json")
    payload.update(
        phase=ExactRestorePhase.AWAITING_FINAL_VERIFY.value,
        authority=authority.model_dump(mode="json"),
        authority_activation=activation.model_dump(mode="json"),
        completed_actions=[item.model_dump(mode="json") for item in completed],
        updated_at=completed_at,
    )
    awaiting = ExactRestoreRecord.model_validate(payload)

    attempt_started = NOW + timedelta(seconds=2)
    attempt = VerifiedPilotInterval(
        started_utc=attempt_started.isoformat().replace("+00:00", "Z"),
        completed_utc=(attempt_started + timedelta(seconds=22)).isoformat().replace("+00:00", "Z"),
        started_monotonic_ns=10_000_000_000,
        completed_monotonic_ns=32_000_000_000,
    )
    return awaiting, replace(artifact, attempt=attempt)


def _manifest_for(
    record: ExactRestoreRecord,
    artifact: VerifiedPilotPairArtifact,
    journal_sha256: str,
) -> ParkedDispositionManifest:
    return ParkedDispositionManifest(
        operation_id=record.operation_id,
        operation_sha256=hashlib.sha256(record.operation_id.encode("ascii")).hexdigest(),
        journal_sha256=journal_sha256,
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        baseline_sha256=record.baseline_sha256,
        action_plan_sha256=record.action_plan_sha256,
        authority_chain_sha256=record.authority_chain_sha256,
        evidence=ParkedDispositionEvidenceReference(
            plan_artifact_id=artifact.plan_artifact_id,
            plan_sha256=artifact.plan_sha256,
            series_artifact_id=artifact.series_id,
            series_sha256=artifact.series_sha256,
            accepted_pair_ordinal=artifact.ordinal,
        ),
        maximum_pair_completion_gap_seconds=20,
        minimum_total_attempt_seconds=20,
    )


def _authority_for(manifest: ParkedDispositionManifest) -> ParkedDispositionAuthority:
    issued = NOW + timedelta(minutes=1)
    issued_monotonic_ns = 40_000_000_000
    return ParkedDispositionAuthority(
        manifest_sha256=manifest.manifest_sha256,
        operation_sha256=manifest.operation_sha256,
        journal_sha256=manifest.journal_sha256,
        evidence_series_sha256=manifest.evidence.series_sha256,
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        boot_identity_sha256=BOOT_A_SHA256,
        issued_monotonic_ns=issued_monotonic_ns,
        deadline_monotonic_ns=issued_monotonic_ns + 5 * 60 * 1_000_000_000,
        confirmation_token_sha256=_digest("park-confirmation"),
    )


def _case(
    tmp_path: Path,
    *,
    operation_id: str = "operation-parked-qualification",
    record_payload_update: dict[str, Any] | None = None,
) -> SimpleNamespace:
    safety_root = tmp_path / "hardware-safety"
    safety_root.mkdir(mode=0o700, parents=True)
    journal = ExactRestoreJournalStore._for_test(safety_root / "exact-restore.json")
    archive = ExactRestoreDispositionArchive._for_test(safety_root)
    record, artifact = _awaiting_record_and_artifact(operation_id=operation_id)
    if record_payload_update is not None:
        payload = record.model_dump(mode="json")
        payload.update(record_payload_update)
        record = ExactRestoreRecord.model_validate(payload)
    with journal.claim():
        journal.create(record.model_dump(mode="json"))
    journal_sha256 = hashlib.sha256(journal.path.read_bytes()).hexdigest()
    manifest = _manifest_for(record, artifact, journal_sha256)
    collector = _CollectorStore(artifact)
    guard = FakeGuard()
    service = ParkedDispositionService(
        manifest,
        journal_store=journal,
        collector_store=collector,
        archive=archive,
        guard=guard,  # type: ignore[arg-type]
        wall_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
        monotonic_clock=lambda: 41_000_000_000,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    return SimpleNamespace(
        archive=archive,
        artifact=artifact,
        authority=_authority_for(manifest),
        collector=collector,
        guard=guard,
        journal=journal,
        manifest=manifest,
        record=record,
        safety_root=safety_root,
        service=service,
    )


def test_owner_only_manifest_loader_rejects_unsafe_metadata(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = tmp_path / "park-manifest.json"
    path.write_text(case.manifest.model_dump_json(), encoding="utf-8")
    path.chmod(0o600)

    assert load_parked_disposition_manifest(path) == case.manifest
    path.chmod(0o644)
    with pytest.raises(ExactRestoreDispositionError, match="disposition_manifest_invalid"):
        load_parked_disposition_manifest(path)


def test_park_installs_backed_terminal_barriers_without_network_or_device_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("network or discovery construction reached")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_datagram_endpoint", forbidden)
    eligibility = case.service.verify()
    disposition = case.service.park(case.authority)

    assert eligibility.record == case.record
    assert case.collector.loads == 2
    assert case.collector.extractions == 2
    assert disposition.qualification_achieved is False
    assert disposition.device_network_access is False
    assert disposition.device_write_count == 0
    finalization = case.archive.load_operation_finalization(case.record.operation_id)
    assert finalization == {
        "version": 2,
        "operation_sha256": disposition.operation_finalization_sha256,
        "disposition_sha256": disposition.disposition_sha256,
        "outcome": "parked_unqualified",
        "cycle": "sentinel_qualification",
    }
    assert case.archive.load_final_verified_receipt(disposition.disposition_sha256) is None
    assert case.archive.load_parked_disposition(case.record.operation_id) == disposition

    tombstone_payload = case.journal.load()
    tombstone = ParkedDispositionTombstone.model_validate(tombstone_payload)
    assert _ExactRestoreArtifactPresence.model_validate(tombstone_payload).phase == (
        "parked_unqualified"
    )
    with pytest.raises(ValidationError):
        ExactRestoreRecord.model_validate(tombstone_payload)
    metadata = case.journal.path.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_nlink == 1
    assert metadata.st_uid == case.journal.path.parent.stat().st_uid
    assert (
        public_parked_tombstone_status(
            tombstone.model_dump(mode="json"),
            archive=case.archive,
        )["status"]
        == "exact_restore_parked_unqualified"
    )
    assert case.service.resume_if_archived() == disposition


def test_source_revocation_does_not_block_write_free_verify_and_park(tmp_path: Path) -> None:
    case = _case(tmp_path, operation_id=_lost_barrier_operation_id())

    eligibility = case.service.verify()
    disposition = case.service.park(case.authority)

    assert eligibility.record.operation_id == case.record.operation_id
    assert disposition.operation_sha256 == case.manifest.operation_sha256
    assert disposition.device_network_access is False
    assert disposition.device_write_count == 0
    assert case.service.resume_if_archived() == disposition


def test_park_cli_runs_the_full_service_path_with_os_network_construction_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jebao_flow.exact_restore_cli as cli

    case = _case(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("park CLI reached an OS network construction boundary")

    class Issuer:
        def confirm_and_park(
            self,
            service: ParkedDispositionService,
            eligibility: object,
        ):
            assert service is case.service
            assert eligibility == case.service.verify()
            return service.park(case.authority)

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_datagram_endpoint", forbidden)
    monkeypatch.setattr(cli, "load_parked_disposition_manifest", lambda _path: case.manifest)
    monkeypatch.setattr(
        cli,
        "build_parked_disposition_service",
        lambda _manifest, *, artifact_root: case.service,
    )
    monkeypatch.setattr(cli, "AttendedDispositionIssuer", Issuer)
    args = cli.build_parser().parse_args(
        [
            "park",
            "--disposition-manifest",
            str(tmp_path / "private-manifest.json"),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )

    command = cli._run(args)
    try:
        command.send(None)
    except StopIteration as completed:
        result = completed.value
    else:  # pragma: no cover - park is deliberately a synchronous, write-free branch
        raise AssertionError("park CLI unexpectedly suspended on an async operation")

    assert result["status"] == "exact_restore_parked_unqualified"
    assert result["device_network_access"] is False
    assert result["device_write_count"] == 0


def test_production_factory_uses_the_canonical_pilot_series_store(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(case.manifest.model_dump_json(), encoding="utf-8")
    manifest_path.chmod(0o600)
    script = """
import sys
sys.path.insert(0, 'src')
from jebao_flow.exact_restore_disposition import (
    build_parked_disposition_service,
    load_parked_disposition_manifest,
)
from jebao_flow.protocol.discovery import DiscoveryProvider

def forbidden(*_args, **_kwargs):
    raise AssertionError('DiscoveryProvider construction reached')

DiscoveryProvider.__new__ = staticmethod(forbidden)
manifest = load_parked_disposition_manifest(sys.argv[1])
service = build_parked_disposition_service(manifest, artifact_root=sys.argv[2])
print(type(service._collector_store).__name__)
"""

    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(manifest_path), str(artifact_root)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == PilotSeriesStore.__name__


def test_archive_before_tombstone_crash_resumes_without_a_second_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)
    original_save = case.journal.save

    def crash_before_tombstone(_payload: object) -> None:
        raise OSError("simulated journal replace failure")

    monkeypatch.setattr(case.journal, "save", crash_before_tombstone)
    with pytest.raises(ExactRestoreDispositionError, match="disposition_tombstone_unconfirmed"):
        case.service.park(case.authority)

    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record
    archived = case.archive.load_parked_disposition(case.record.operation_id)
    assert archived is not None
    monkeypatch.setattr(case.journal, "save", original_save)

    resumed = case.service.resume_if_archived()

    assert resumed == archived
    assert ParkedDispositionTombstone.model_validate(case.journal.load()).disposition_sha256 == (
        archived.disposition_sha256
    )


def test_late_journal_save_error_converges_only_on_the_exact_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)
    original_save = case.journal.save

    def save_then_report_error(payload: dict[str, object]) -> None:
        original_save(payload)
        raise OSError("simulated post-replace error")

    monkeypatch.setattr(case.journal, "save", save_then_report_error)

    disposition = case.service.park(case.authority)

    tombstone = ParkedDispositionTombstone.model_validate(case.journal.load())
    assert tombstone.disposition_sha256 == disposition.disposition_sha256
    assert case.archive.load_parked_disposition(case.record.operation_id) == disposition


def test_completed_tombstone_rejects_a_different_manifest_as_conflict(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.service.park(case.authority)
    changed_manifest = ParkedDispositionManifest.model_validate(
        {
            **case.manifest.model_dump(mode="json"),
            "minimum_total_attempt_seconds": 21,
        }
    )
    conflicting = ParkedDispositionService(
        changed_manifest,
        journal_store=case.journal,
        collector_store=case.collector,
        archive=case.archive,
        guard=case.guard,  # type: ignore[arg-type]
        wall_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
        monotonic_clock=lambda: 41_000_000_000,
        boot_identity=lambda: BOOT_A_SHA256,
    )

    with pytest.raises(ExactRestoreDispositionError, match="disposition_tombstone_conflict"):
        conflicting.load_completed()


def test_archive_failure_never_mutates_live_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)

    def fail_archive(**_kwargs: object) -> None:
        raise OSError("simulated archive failure")

    monkeypatch.setattr(case.archive, "persist_parked_bundle", fail_archive)
    with pytest.raises(OSError, match="simulated archive failure"):
        case.service.park(case.authority)

    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record
    assert case.archive.load_operation_finalization(case.record.operation_id) is None


@pytest.mark.asyncio
async def test_only_a_distinct_manifest_bound_v2_sentinel_can_replace_a_tombstone(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.service.park(case.authority)
    harness = RestoreHarness(case.record.baseline, case.journal)  # type: ignore[arg-type]
    guard = FakeGuard()
    controller = ExactRestoreController(
        case.journal,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=case.archive,
        clock=lambda: NOW + timedelta(minutes=2),
        monotonic_clock=lambda: 50_000_000_000,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    successor = prepare_exact_restore_record(
        case.record.baseline,
        case.record.safe_targets,
        operation_id="different-reviewed-operation",
        now=NOW + timedelta(minutes=2),
    )

    qualification_controller, _store, _guard, _qualification_harness, _armed = _armed_controller()
    qualified = await qualification_controller.execute()
    staged = prepare_qualified_final_restore_record(
        qualified,
        operation_id="different-reviewed-final-operation",
        now=NOW + timedelta(minutes=2),
    )
    split_policy = case.record.baseline.verification_policy.model_copy(
        update={"max_final_acquisition_duration_seconds": 30.0}
    )
    split_baseline = case.record.baseline.model_copy(update={"verification_policy": split_policy})
    split_successor = prepare_exact_restore_record(
        split_baseline,
        case.record.safe_targets,
        operation_id="different-reviewed-v2-operation",
        now=NOW + timedelta(minutes=2),
    )
    same_operation_split_successor = prepare_exact_restore_record(
        split_baseline,
        case.record.safe_targets,
        operation_id=case.record.operation_id,
        now=NOW + timedelta(minutes=2),
    )

    calls = (
        lambda: controller.create(successor),
        lambda: controller.create(split_successor, manifest_version=1),
        lambda: controller.create(same_operation_split_successor, manifest_version=2),
        lambda: controller.create_qualified_final_restore(staged),
        lambda: controller.promote_to_baseline_restore(
            operation_id="different-reviewed-promoted-operation"
        ),
    )
    with pytest.raises(ExactRestorePreflightError) as outside_lease:
        controller.create(split_successor, manifest_version=2)
    assert outside_lease.value.code is ExactRestoreErrorCode.AUTHORITY

    with guard.lease():
        guard.clear()
        for call in calls:
            with pytest.raises(ExactRestorePreflightError) as rejected:
                call()
            assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY

        assert ParkedDispositionTombstone.model_validate(case.journal.load())
        controller.create(split_successor, manifest_version=2)

    assert ExactRestoreRecord.model_validate(case.journal.load()) == split_successor
    assert case.archive.load_operation_finalization(case.record.operation_id) is not None
    assert harness.writes == []


def test_v2_tombstone_successor_requires_exact_permanent_backing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)
    case.service.park(case.authority)
    original_tombstone = ParkedDispositionTombstone.model_validate(case.journal.load())
    guard = FakeGuard()
    harness = RestoreHarness(case.record.baseline, case.journal)  # type: ignore[arg-type]
    controller = ExactRestoreController(
        case.journal,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=case.archive,
        clock=lambda: NOW + timedelta(minutes=2),
        monotonic_clock=lambda: 50_000_000_000,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    split_policy = case.record.baseline.verification_policy.model_copy(
        update={"max_final_acquisition_duration_seconds": 30.0}
    )
    split_baseline = case.record.baseline.model_copy(update={"verification_policy": split_policy})
    successor = prepare_exact_restore_record(
        split_baseline,
        case.record.safe_targets,
        operation_id="different-reviewed-v2-operation",
        now=NOW + timedelta(minutes=2),
    )

    def fail_confirmation(_payload: object) -> object:
        raise OSError("simulated missing parked backing")

    monkeypatch.setattr(case.archive, "confirm_parked_tombstone", fail_confirmation)
    with guard.lease():
        guard.clear()
        with pytest.raises(ExactRestorePreflightError) as rejected:
            controller.create(successor, manifest_version=2)

    assert rejected.value.code is ExactRestoreErrorCode.JOURNAL
    assert ParkedDispositionTombstone.model_validate(case.journal.load()) == original_tombstone
    assert harness.writes == []


@pytest.mark.asyncio
async def test_all_nine_controller_mutators_reject_one_parked_operation_before_io(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.service.park(case.authority)
    calls = {"observe": 0, "resolve": 0}

    async def observe(_role: object) -> object:
        calls["observe"] += 1
        raise AssertionError("parked operation reached observation")

    def resolve(_role: object, _observation: object) -> object:
        calls["resolve"] += 1
        raise AssertionError("parked operation reached a device")

    controller = ExactRestoreController(
        case.journal,
        FakeGuard(),
        observe=observe,
        resolve_device=resolve,
        qualification_receipts=case.archive,
        clock=lambda: NOW + timedelta(minutes=2),
        monotonic_clock=lambda: 50_000_000_000,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    prepared = prepare_exact_restore_record(
        case.record.baseline,
        case.record.safe_targets,
        operation_id=case.record.operation_id,
        now=NOW + timedelta(minutes=2),
    )
    qualification_controller, _store, _guard, _harness, _armed = _armed_controller()
    qualified = await qualification_controller.execute()
    staged = prepare_qualified_final_restore_record(
        qualified,
        operation_id=case.record.operation_id,
        now=NOW + timedelta(minutes=2),
    )
    receipt = _receipt_from_final_verified_record(qualified)
    existing_authority = case.record.authority
    assert existing_authority is not None

    synchronous_calls = (
        lambda: controller.create(prepared),
        lambda: controller.create_qualified_final_restore(staged),
        controller.promote_to_baseline_restore,
        lambda: controller.arm(existing_authority),
        lambda: controller.reauthorize(existing_authority),
        lambda: controller.recover(existing_authority),
        lambda: controller.clear_after_receipt(receipt),
    )
    for call in synchronous_calls:
        with pytest.raises(ExactRestorePreflightError) as rejected:
            call()
        assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    for call in (controller.execute, controller.finalize):
        with pytest.raises(ExactRestorePreflightError) as rejected:
            await call()
        assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY

    assert calls == {"observe": 0, "resolve": 0}
    assert ParkedDispositionTombstone.model_validate(case.journal.load())


def test_success_and_park_finalizations_conflict_in_both_orders(tmp_path: Path) -> None:
    case = _case(tmp_path / "park-first")
    disposition = case.service.park(case.authority)
    success_finalization = ExactRestoreOperationFinalization(
        operation_sha256=disposition.operation_finalization_sha256,
        receipt_sha256="f" * 64,
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
    )
    with pytest.raises(ExactRestoreOperationFinalizationConflictError):
        with case.archive._claim():
            root = case.archive._require_active_claim()
            case.archive._persist_finalization_under_claim(
                root,
                success_finalization,
                case.archive._canonical_finalization(success_finalization),
            )

    reverse_root = tmp_path / "success-first"
    reverse_root.mkdir(mode=0o700)
    reverse = ExactRestoreDispositionArchive._for_test(reverse_root)
    with reverse._claim():
        root = reverse._require_active_claim()
        reverse._persist_finalization_under_claim(
            root,
            success_finalization,
            reverse._canonical_finalization(success_finalization),
        )
    with pytest.raises(ExactRestoreOperationFinalizationConflictError):
        reverse.persist_parked_bundle(
            journal_canonical=case.archive.path_for_parked_journal_snapshot(
                disposition.journal_snapshot_sha256
            ).read_bytes(),
            disposition=disposition,
        )


def test_legacy_binary_shapes_fail_closed_on_v2_finalization_and_tombstone(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    disposition = case.service.park(case.authority)
    finalization = case.archive.load_operation_finalization(case.record.operation_id)
    tombstone = case.journal.load()

    class LegacyFinalization(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)

        version: Literal[1] = 1
        operation_sha256: str
        receipt_sha256: str
        cycle: ExactRestoreCycle

    with pytest.raises(ValidationError):
        LegacyFinalization.model_validate(finalization)
    with pytest.raises(ValidationError):
        ExactRestoreRecord.model_validate(tombstone)

    legacy_store = ExactRestoreJournalStore._for_test(case.journal.path)
    legacy_prepared = prepare_exact_restore_record(
        case.record.baseline,
        case.record.safe_targets,
        operation_id="legacy-replay",
        now=NOW + timedelta(minutes=2),
    )
    with legacy_store.claim(), pytest.raises(ExactRestoreJournalClaimError):
        legacy_store.create(legacy_prepared.model_dump(mode="json"))
    calls = {"device": 0}

    def legacy_resume() -> None:
        ExactRestoreRecord.model_validate(legacy_store.load())
        calls["device"] += 1

    with pytest.raises(ValidationError):
        legacy_resume()  # legacy arm
    with pytest.raises(ValidationError):
        legacy_resume()  # legacy execute
    assert calls["device"] == 0
    assert disposition.device_write_count == 0


def test_attended_park_authority_uses_a_distinct_exact_challenge(tmp_path: Path) -> None:
    case = _case(tmp_path)

    class Channel:
        def __init__(self) -> None:
            self.summary = ""

        def write(self, value: str) -> None:
            self.summary = value

        def read_line(self, _max_bytes: int) -> str:
            return self.summary.rsplit("Type exactly: ", 1)[1].splitlines()[0]

    channel = Channel()

    @contextmanager
    def channel_factory():
        yield channel

    walls = iter((NOW + timedelta(minutes=1), NOW + timedelta(minutes=1, seconds=1)))
    monotonic = iter((40_000_000_000, 41_000_000_000))
    issuer = AttendedDispositionIssuer(
        channel_factory=channel_factory,
        wall_clock=lambda: next(walls),
        monotonic_clock=lambda: next(monotonic),
        boot_identity=lambda: BOOT_A_SHA256,
        entropy=lambda _size: bytes.fromhex("11" * 32),
    )

    authority = issuer._confirm(case.service.verify())

    assert "AUTHORIZE-PARK-" in channel.summary
    assert "device_network_access: false" in channel.summary
    assert "qualification_achieved: false" in channel.summary
    assert authority.intent == "park_unqualified_without_device_io"


def test_live_lock_holder_is_reported_as_retryable_not_tombstone_corruption(
    tmp_path: Path,
) -> None:
    """A concurrent archive holder must never surface as tombstone invalidity."""

    case = _case(tmp_path)
    case.service.park(case.authority)
    payload = case.journal.load()
    assert isinstance(payload, dict)
    holder = ExactRestoreDispositionArchive._for_test(case.archive.root)

    with holder._claim():
        with pytest.raises(ExactRestoreDispositionError) as rejected:
            public_parked_tombstone_status(payload, archive=case.archive)
        assert rejected.value.code == "disposition_archive_claim_unavailable"

        with pytest.raises(ExactRestoreDispositionError) as loading:
            case.archive.load_parked_disposition(case.manifest.operation_id)
        assert loading.value.code == "disposition_archive_claim_unavailable"

    released = public_parked_tombstone_status(payload, archive=case.archive)
    assert released["phase"] == "parked_unqualified"
    assert released["qualification_achieved"] is False


def test_translated_archive_refusal_survives_the_outer_tombstone_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contention between a successful confirm and the following load keeps its own code."""

    case = _case(tmp_path)
    case.service.park(case.authority)
    payload = case.journal.load()
    assert isinstance(payload, dict)

    def _contended_load(_self: object, _operation_id: str) -> None:
        raise ExactRestoreDispositionError("disposition_archive_claim_unavailable")

    monkeypatch.setattr(
        ExactRestoreDispositionArchive,
        "load_parked_disposition",
        _contended_load,
    )

    with pytest.raises(ExactRestoreDispositionError) as status_refusal:
        public_parked_tombstone_status(payload, archive=case.archive)
    assert status_refusal.value.code == "disposition_archive_claim_unavailable"

    with pytest.raises(ExactRestoreDispositionError) as park_refusal:
        case.service.park(case.authority)
    assert park_refusal.value.code == "disposition_archive_claim_unavailable"


def test_real_tombstone_mismatch_still_closes_as_tombstone_invalid(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.service.park(case.authority)
    payload = case.journal.load()
    assert isinstance(payload, dict)
    forged = dict(payload)
    forged["disposition_sha256"] = "f" * 64

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        public_parked_tombstone_status(forged, archive=case.archive)

    assert rejected.value.code == "disposition_tombstone_invalid"


def _parse_artifact_start(artifact: VerifiedPilotPairArtifact) -> Any:
    return datetime.fromisoformat(artifact.attempt.started_utc.replace("Z", "+00:00"))


def _completed_at(record_payload: dict[str, Any], moment: object) -> list[dict[str, Any]]:
    actions = [dict(item) for item in record_payload["completed_actions"]]
    for item in actions:
        item["completed_at"] = moment
    return actions


def test_write_free_journal_update_after_the_artifact_stays_eligible(tmp_path: Path) -> None:
    """An attended reauthorization moves updated_at without writing to any device."""

    later = (NOW + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    case = _case(tmp_path, record_payload_update={"updated_at": later})
    assert case.record.updated_at > _parse_artifact_start(case.artifact)

    disposition = case.service.park(case.authority)

    assert disposition.qualification_achieved is False
    assert ParkedDispositionTombstone.model_validate(case.journal.load())


def test_artifact_at_or_before_the_last_write_is_still_refused(tmp_path: Path) -> None:
    base = _awaiting_record_and_artifact()[0].model_dump(mode="json")
    same = (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
    case = _case(
        tmp_path,
        record_payload_update={
            "completed_actions": _completed_at(base, same),
            "updated_at": same,
        },
    )

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_artifact_predates_restore"
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


@pytest.mark.parametrize("kept", [7, 0])
def test_awaiting_record_cannot_exist_with_an_incomplete_action_prefix(kept: int) -> None:
    """The relaxed artifact gate relies on a complete prefix, enforced one layer deeper.

    A journal awaiting final verification cannot even be represented with a short prefix, so
    the disposition path can never see one.
    """

    payload = _awaiting_record_and_artifact()[0].model_dump(mode="json")
    payload["completed_actions"] = payload["completed_actions"][:kept]

    with pytest.raises(ValidationError, match="complete action prefix"):
        ExactRestoreRecord.model_validate(payload)


def test_action_without_written_verified_outcome_is_refused(tmp_path: Path) -> None:
    base = _awaiting_record_and_artifact()[0].model_dump(mode="json")
    actions = [dict(item) for item in base["completed_actions"]]
    actions[-1]["outcome"] = ExactRestoreActionOutcome.ALREADY_SATISFIED.value

    case = _case(tmp_path, record_payload_update={"completed_actions": actions})

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_journal_ineligible"


def test_disposition_import_graph_has_only_reviewed_protocol_edges() -> None:
    script = """
import json, sys
sys.path.insert(0, 'src')
import jebao_flow.exact_restore_disposition
print(json.dumps(sorted(name for name in sys.modules if name.startswith('jebao_flow.'))))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(json.loads(result.stdout)) == {
        "jebao_flow.config",
        "jebao_flow.exact_restore",
        "jebao_flow.exact_restore_authority",
        "jebao_flow.exact_restore_disposition",
        "jebao_flow.exact_restore_guard",
        "jebao_flow.exact_restore_receipts",
        "jebao_flow.exact_restore_store",
        "jebao_flow.groups",
        "jebao_flow.groups.calculator",
        "jebao_flow.groups.models",
        "jebao_flow.hardware_safety",
        "jebao_flow.patterns",
        "jebao_flow.patterns.anti_phase",
        "jebao_flow.patterns.base",
        "jebao_flow.patterns.constant",
        "jebao_flow.patterns.gyre",
        "jebao_flow.patterns.nutrient_transport",
        "jebao_flow.patterns.randomized",
        "jebao_flow.patterns.sync",
        "jebao_flow.patterns.tidal_swell",
        "jebao_flow.physical_identity",
        "jebao_flow.protocol",
        "jebao_flow.protocol.codec",
        "jebao_flow.protocol.control",
        "jebao_flow.protocol.discovery",
        "jebao_flow.protocol.errors",
        "jebao_flow.protocol.models",
        "jebao_flow.protocol.profiles",
        "jebao_flow.protocol.schedule",
        "jebao_flow.protocol.schedule_wire",
        "jebao_flow.protocol.schema",
        "jebao_flow.protocol.session",
        "jebao_flow.read_only_collector",
        "jebao_flow.safety",
        "jebao_flow.safety.limits",
        "jebao_flow.source_attestation",
    }


def _v2_manifest_kwargs(record: ExactRestoreRecord, artifact: object) -> dict[str, Any]:
    return {
        "version": 2,
        "reason": ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED,
        "required_action_outcome": None,
        "minimum_total_attempt_seconds": None,
        "predecessor_depth": 0,
        "completed_action_outcomes": tuple(item.outcome for item in record.completed_actions),
    }


def test_version_one_manifest_keeps_its_exact_legacy_shape() -> None:
    """Widening the model must not add a key or move a digest for an existing park."""

    record, artifact = _awaiting_record_and_artifact()
    manifest = _manifest_for(record, artifact, "a" * 64)
    payload = manifest.model_dump(mode="json")

    assert sorted(payload) == [
        "action_plan_sha256",
        "authority_chain_sha256",
        "baseline_sha256",
        "completed_action_count",
        "cycle",
        "device_network_access",
        "device_write_count",
        "evidence",
        "journal_sha256",
        "maximum_pair_completion_gap_seconds",
        "minimum_total_attempt_seconds",
        "operation_id",
        "operation_sha256",
        "outcome",
        "qualification_achieved",
        "reason",
        "required_action_outcome",
        "version",
    ]
    assert manifest.manifest_sha256 == (
        "1e6b0a060c5431b0273819577d13468d523eb33db51d52989aebee19b91403b3"
    )


def test_version_two_manifest_must_be_a_generic_unresolved_park() -> None:
    record, artifact = _awaiting_record_and_artifact()
    base = _manifest_for(record, artifact, "a" * 64).model_dump(mode="json")
    base.update(_v2_manifest_kwargs(record, artifact))

    # A version 2 park has no durable failure frame, so it may not claim the overload reason
    # nor carry the overload evidence bound.
    with pytest.raises(ValidationError, match="generic unresolved park"):
        ParkedDispositionManifest.model_validate(
            {**base, "reason": ParkedDispositionReason.FINAL_VERIFICATION_POLICY_OVERLOAD}
        )
    with pytest.raises(ValidationError, match="generic unresolved park"):
        ParkedDispositionManifest.model_validate({**base, "minimum_total_attempt_seconds": 20})
    with pytest.raises(ValidationError, match="generic unresolved park"):
        ParkedDispositionManifest.model_validate(
            {**base, "required_action_outcome": "written_verified"}
        )

    assert ParkedDispositionManifest.model_validate(base).version == 2


def test_version_two_manifest_rejects_unsupported_and_mismatched_shapes() -> None:
    record, artifact = _awaiting_record_and_artifact()
    base = _manifest_for(record, artifact, "a" * 64).model_dump(mode="json")
    base.update(_v2_manifest_kwargs(record, artifact))
    outcomes = list(base["completed_action_outcomes"])

    # An outcome that is not an `ExactRestoreActionOutcome` at all is refused by the field type
    # before any validator runs.  That is not what closes the set: a value added to the enum
    # would parse here, and only the membership gate would still refuse it.  See
    # `test_accepted_outcome_set_is_exactly_the_three_reasoned_about` for that half.
    with pytest.raises(ValidationError, match="already_satisfied"):
        ParkedDispositionManifest.model_validate(
            {**base, "completed_action_outcomes": [*outcomes[:-1], "some_future_outcome"]}
        )
    with pytest.raises(ValidationError, match="outcome vector length"):
        ParkedDispositionManifest.model_validate(
            {**base, "completed_action_outcomes": outcomes[:-1]}
        )
    with pytest.raises(ValidationError, match="deterministic plan"):
        ParkedDispositionManifest.model_validate({**base, "completed_action_count": 6})
    with pytest.raises(ValidationError, match="predecessor depth"):
        ParkedDispositionManifest.model_validate({**base, "predecessor_depth": 1})


def test_version_one_manifest_rejects_version_two_only_fields() -> None:
    record, artifact = _awaiting_record_and_artifact()
    base = _manifest_for(record, artifact, "a" * 64).model_dump(mode="json")

    with pytest.raises(ValidationError, match="original overload park"):
        ParkedDispositionManifest.model_validate({**base, "predecessor_depth": 0})
    with pytest.raises(ValidationError, match="original overload park"):
        ParkedDispositionManifest.model_validate(
            {**base, "completed_action_outcomes": ["written_verified"] * 8}
        )


def test_artifact_between_a_regressed_and_an_earlier_completion_is_refused(
    tmp_path: Path,
) -> None:
    """A clock that goes backwards must not let pre-write evidence terminalize the journal.

    Completion stamps are not monotonic across the prefix, so the last index can be earlier than
    a preceding write's completion.  The gate has to use the maximum, not `[-1]`.
    """

    base = _awaiting_record_and_artifact()[0].model_dump(mode="json")
    actions = [dict(item) for item in base["completed_actions"]]
    # The artifact starts at NOW+2s.  Put an earlier action's completion after it and let the
    # clock regress for the final action, so only the maximum catches the ordering.
    actions[-2]["completed_at"] = (NOW + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    actions[-1]["completed_at"] = (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    case = _case(
        tmp_path,
        record_payload_update={
            "completed_actions": actions,
            "updated_at": (NOW + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        },
    )
    last_index_completion = case.record.completed_actions[-1].completed_at
    maximum_completion = max(item.completed_at for item in case.record.completed_actions)
    artifact_started = datetime.fromisoformat(
        case.artifact.attempt.started_utc.replace("Z", "+00:00")
    )
    assert last_index_completion < artifact_started < maximum_completion

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_artifact_predates_restore"
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


# ---------------------------------------------------------------------------
# Version 2 end-to-end: the same park has to work for every cycle the engine can
# actually strand, not only the sentinel qualification the original park was cut for.
# ---------------------------------------------------------------------------


def _split_baseline_and_artifact() -> tuple[Any, VerifiedPilotPairArtifact]:
    """Build a split-policy baseline from the same artifact the version 1 fixture uses."""

    restore_manifest = _split_restore_manifest()
    config = _config()
    artifact = _artifact(config)
    baseline = build_verified_baseline(
        restore_manifest,
        select_operation_targets(config, restore_manifest),
        artifact,
        now=NOW,
    )
    attempt_started = NOW + timedelta(seconds=2)
    attempt = VerifiedPilotInterval(
        started_utc=attempt_started.isoformat().replace("+00:00", "Z"),
        completed_utc=(attempt_started + timedelta(seconds=22)).isoformat().replace("+00:00", "Z"),
        started_monotonic_ns=10_000_000_000,
        completed_monotonic_ns=32_000_000_000,
    )
    return baseline, replace(artifact, attempt=attempt)


async def _v2_prepared(baseline: Any, *, depth: int) -> ExactRestoreRecord:
    """Return a prepared record whose embedded predecessor depth is exactly `depth`."""

    if depth == 0:
        return prepare_exact_restore_record(
            baseline,
            _safe_targets(),
            cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
            operation_id="operation-parked-sentinel-v2",
            now=NOW,
        )
    if depth == 1:
        return _promoted_record(baseline=baseline, operation_id="operation-parked-baseline-v2")
    controller, _store, _guard, _harness, _armed = _armed_controller(
        cycle=ExactRestoreCycle.BASELINE_RESTORE,
        baseline=baseline,
    )
    qualified = await controller.execute()
    return prepare_qualified_final_restore_record(
        qualified,
        operation_id="operation-parked-final-v2",
        now=NOW,
    )


def _awaiting_from(
    prepared: ExactRestoreRecord,
    *,
    outcomes: tuple[ExactRestoreActionOutcome, ...] | None = None,
) -> ExactRestoreRecord:
    authority = _authority(
        prepared,
        now=NOW,
        monotonic_now=1_000_000_000,
        boot_identity_sha256=BOOT_A_SHA256,
    )
    activation = ExactRestoreAuthorityActivation(
        authority_sha256=authority.authority_sha256,
        boot_identity_sha256=authority.boot_identity_sha256,
        accepted_wall=authority.issued_at,
        accepted_monotonic_ns=authority.issued_monotonic_ns,
        deadline_monotonic_ns=authority.deadline_monotonic_ns,
    )
    vector = outcomes or tuple(ExactRestoreActionOutcome.WRITTEN_VERIFIED for _ in prepared.actions)
    assert len(vector) == len(prepared.actions)
    completed_at = NOW + timedelta(seconds=1)
    completed = tuple(
        ExactRestoreActionResult(
            index=action.index,
            action_id=action.action_id,
            outcome=outcome,
            pre_state_sha256=_digest(f"pre-{prepared.operation_id}-{action.index}"),
            post_state_sha256=_digest(f"post-{prepared.operation_id}-{action.index}"),
            completed_at=completed_at,
        )
        for action, outcome in zip(prepared.actions, vector, strict=True)
    )
    payload = prepared.model_dump(mode="json")
    payload.update(
        phase=ExactRestorePhase.AWAITING_FINAL_VERIFY.value,
        authority=authority.model_dump(mode="json"),
        authority_activation=activation.model_dump(mode="json"),
        completed_actions=[item.model_dump(mode="json") for item in completed],
        updated_at=completed_at,
    )
    return ExactRestoreRecord.model_validate(payload)


def _v2_manifest_for(
    record: ExactRestoreRecord,
    artifact: VerifiedPilotPairArtifact,
    journal_sha256: str,
) -> ParkedDispositionManifest:
    policy = record.baseline.verification_policy
    return ParkedDispositionManifest(
        version=2,
        operation_id=record.operation_id,
        operation_sha256=hashlib.sha256(record.operation_id.encode("ascii")).hexdigest(),
        journal_sha256=journal_sha256,
        cycle=record.cycle,
        baseline_sha256=record.baseline_sha256,
        action_plan_sha256=record.action_plan_sha256,
        authority_chain_sha256=record.authority_chain_sha256,
        completed_action_count=len(record.completed_actions),
        required_action_outcome=None,
        evidence=ParkedDispositionEvidenceReference(
            plan_artifact_id=artifact.plan_artifact_id,
            plan_sha256=artifact.plan_sha256,
            series_artifact_id=artifact.series_id,
            series_sha256=artifact.series_sha256,
            accepted_pair_ordinal=artifact.ordinal,
        ),
        maximum_pair_completion_gap_seconds=policy.max_final_pair_gap_seconds,
        predecessor_depth=_embedded_depth(record),
        completed_action_outcomes=tuple(item.outcome for item in record.completed_actions),
        reason=ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED,
    )


def _embedded_depth(record: ExactRestoreRecord) -> int:
    depth = 0
    parent = record.qualification_final_record
    while parent is not None:
        depth += 1
        parent = parent.qualification_final_record
    return depth


def _archive_predecessor_chain(archive: Any, record: ExactRestoreRecord) -> None:
    """Mirror the promotion step that binds a finished predecessor before its successor runs."""

    ancestors = []
    parent = record.qualification_final_record
    while parent is not None:
        ancestors.append(parent)
        parent = parent.qualification_final_record
    for ancestor in reversed(ancestors):
        archive.persist_final_verified_receipt(_receipt_from_final_verified_record(ancestor))


async def _v2_case(
    tmp_path: Path,
    *,
    depth: int,
    outcomes: tuple[ExactRestoreActionOutcome, ...] | None = None,
    archive_predecessors: bool = True,
) -> SimpleNamespace:
    safety_root = tmp_path / "hardware-safety"
    safety_root.mkdir(mode=0o700, parents=True)
    journal = ExactRestoreJournalStore._for_test(safety_root / "exact-restore.json")
    archive = ExactRestoreDispositionArchive._for_test(safety_root)
    baseline, artifact = _split_baseline_and_artifact()
    record = _awaiting_from(await _v2_prepared(baseline, depth=depth), outcomes=outcomes)
    assert _embedded_depth(record) == depth
    if archive_predecessors:
        _archive_predecessor_chain(archive, record)
    with journal.claim():
        journal.create(record.model_dump(mode="json"))
    journal_sha256 = hashlib.sha256(journal.path.read_bytes()).hexdigest()
    manifest = _v2_manifest_for(record, artifact, journal_sha256)
    collector = _CollectorStore(artifact)
    guard = FakeGuard()
    service = ParkedDispositionService(
        manifest,
        journal_store=journal,
        collector_store=collector,
        archive=archive,
        guard=guard,  # type: ignore[arg-type]
        wall_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
        monotonic_clock=lambda: 41_000_000_000,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    return SimpleNamespace(
        archive=archive,
        artifact=artifact,
        authority=_authority_for(manifest),
        collector=collector,
        guard=guard,
        journal=journal,
        manifest=manifest,
        record=record,
        safety_root=safety_root,
        service=service,
    )


@pytest.mark.parametrize(
    ("depth", "expected_cycle", "expected_actions"),
    [
        (0, ExactRestoreCycle.SENTINEL_QUALIFICATION, 8),
        (1, ExactRestoreCycle.BASELINE_RESTORE, 6),
        (2, ExactRestoreCycle.BASELINE_RESTORE, 6),
    ],
)
async def test_version_two_park_covers_every_strandable_cycle_depth(
    tmp_path: Path,
    depth: int,
    expected_cycle: ExactRestoreCycle,
    expected_actions: int,
) -> None:
    """Sentinel, baseline restore and phase-5 staged restore all park through one path.

    Phase 5 shares `BASELINE_RESTORE` with the baseline cycle, so the depth is what tells them
    apart.  All three must reach a terminal park with a permanent version 2 finalization.
    """

    case = await _v2_case(tmp_path, depth=depth)
    assert case.record.cycle is expected_cycle
    assert len(case.record.completed_actions) == expected_actions

    disposition = case.service.park(case.authority)

    assert disposition.version == 2
    assert disposition.cycle is expected_cycle
    assert disposition.predecessor_depth == depth
    assert disposition.reason is ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED
    assert disposition.completed_action_count == expected_actions
    tombstone = ParkedDispositionTombstone.model_validate(case.journal.load())
    assert tombstone.disposition_sha256 == disposition.disposition_sha256
    finalization = case.archive.load_operation_finalization(case.record.operation_id)
    assert finalization is not None
    assert finalization["version"] == 2
    assert finalization["outcome"] == "parked_unqualified"
    assert finalization["cycle"] == expected_cycle.value
    assert finalization["disposition_sha256"] == disposition.disposition_sha256


async def test_version_two_park_accepts_the_ordinary_baseline_skip_vector(
    tmp_path: Path,
) -> None:
    """The baseline cycle's normal distribution is four writes and two skips.

    Safe fallback writes both roles, the restore schedule is already satisfied on both, and the
    restore outer writes both.  The version 1 rule demanded every action be `written_verified`,
    which would have refused this ordinary case outright; the ordered vector carries it instead.
    """

    outcomes = (
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
        ExactRestoreActionOutcome.ALREADY_SATISFIED,
        ExactRestoreActionOutcome.ALREADY_SATISFIED,
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
    )
    case = await _v2_case(tmp_path, depth=1, outcomes=outcomes)

    disposition = case.service.park(case.authority)

    assert disposition.completed_action_outcomes == outcomes
    assert case.archive.load_operation_finalization(case.record.operation_id) is not None
    status = public_parked_tombstone_status(case.journal.load(), archive=case.archive)
    assert status["parked_cycle"] == ExactRestoreCycle.BASELINE_RESTORE.value
    assert status["predecessor_depth"] == 1
    assert status["reason"] == ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED.value
    assert status["past_action_outcomes"] == [outcome.value for outcome in outcomes]


def _rebuilt(manifest: ParkedDispositionManifest, **changes: Any) -> ParkedDispositionManifest:
    """Round-trip a manifest with one dimension changed.

    Nothing is filled in here on purpose.  An earlier version of this helper pinned the absent
    optional keys back to `None`, which quietly hid a real defect: the model's own version 2
    dump could not be revalidated, because the version 1 outcome default came back on the way in.
    """

    payload = manifest.model_dump(mode="json")
    payload.update(changes)
    return ParkedDispositionManifest.model_validate(payload)


async def test_version_two_manifest_must_match_the_journal_it_names(tmp_path: Path) -> None:
    """Every declared dimension is bound to the journal, not merely self-consistent.

    A manifest that validates on its own but describes a different journal has to be refused;
    otherwise an owner could park one operation under another operation's description.
    """

    case = await _v2_case(tmp_path, depth=1)
    mismatches = {
        "depth": _rebuilt(case.manifest, predecessor_depth=2),
        "gap_bound": _rebuilt(case.manifest, maximum_pair_completion_gap_seconds=19.0),
        "outcomes": _rebuilt(
            case.manifest,
            completed_action_outcomes=[
                ExactRestoreActionOutcome.ALREADY_SATISFIED.value,
                *[outcome.value for outcome in (case.manifest.completed_action_outcomes or ())[1:]],
            ],
        ),
    }
    for label, manifest in mismatches.items():
        service = ParkedDispositionService(
            manifest,
            journal_store=case.journal,
            collector_store=case.collector,
            archive=case.archive,
            guard=case.guard,  # type: ignore[arg-type]
            wall_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
            monotonic_clock=lambda: 41_000_000_000,
            boot_identity=lambda: BOOT_A_SHA256,
        )
        with pytest.raises(ExactRestoreDispositionError) as rejected:
            service.park(_authority_for(manifest))
        assert rejected.value.code == "disposition_journal_ineligible", label

    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_manifest_version_is_bound_to_the_journals_own_policy(tmp_path: Path) -> None:
    """The disposition version follows the journal's policy shape and cannot be chosen freely.

    A split-policy journal is a version 2 park and a legacy-policy journal is a version 1 park.
    Crossing them would let a generic park claim overload evidence it never had, or an overload
    park hide a baseline cycle's skips behind the retired all-written rule.
    """

    split_case = await _v2_case(tmp_path / "split", depth=0)
    legacy_case = _case(tmp_path / "legacy")

    version_one_over_split = _rebuilt(
        split_case.manifest,
        version=1,
        required_action_outcome="written_verified",
        minimum_total_attempt_seconds=20,
        predecessor_depth=None,
        completed_action_outcomes=None,
        reason=ParkedDispositionReason.FINAL_VERIFICATION_POLICY_OVERLOAD.value,
    )
    version_two_over_legacy = _rebuilt(
        legacy_case.manifest,
        version=2,
        required_action_outcome=None,
        minimum_total_attempt_seconds=None,
        predecessor_depth=0,
        completed_action_outcomes=[
            item.outcome.value for item in legacy_case.record.completed_actions
        ],
        reason=ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED.value,
    )

    for case, manifest in (
        (split_case, version_one_over_split),
        (legacy_case, version_two_over_legacy),
    ):
        service = ParkedDispositionService(
            manifest,
            journal_store=case.journal,
            collector_store=case.collector,
            archive=case.archive,
            guard=case.guard,  # type: ignore[arg-type]
            wall_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
            monotonic_clock=lambda: 41_000_000_000,
            boot_identity=lambda: BOOT_A_SHA256,
        )
        with pytest.raises(ExactRestoreDispositionError) as rejected:
            service.park(_authority_for(manifest))
        assert rejected.value.code == "disposition_journal_ineligible"
        assert case.archive.load_operation_finalization(case.record.operation_id) is None
        assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_cycle_and_count_and_depth_cannot_disagree_inside_one_manifest(
    tmp_path: Path,
) -> None:
    """The cycle fixes the action count and the admissible depths at construction time."""

    case = await _v2_case(tmp_path, depth=1)
    with pytest.raises(ValidationError):
        _rebuilt(case.manifest, cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION.value)
    with pytest.raises(ValidationError):
        _rebuilt(case.manifest, predecessor_depth=0)
    with pytest.raises(ValidationError):
        _rebuilt(case.manifest, completed_action_count=8)

    sentinel_case = await _v2_case(tmp_path / "sentinel", depth=0)
    with pytest.raises(ValidationError):
        _rebuilt(sentinel_case.manifest, predecessor_depth=1)


async def test_park_refuses_an_unbarriered_predecessor_before_asking_for_consent(
    tmp_path: Path,
) -> None:
    """A baseline park whose sentinel never got its permanent success barrier must not proceed.

    Parking on top of an unbarriered ancestor would leave that ancestor replayable, which is the
    duplicate-write hazard the predecessor finalization exists to close.
    """

    case = await _v2_case(tmp_path, depth=1, archive_predecessors=False)

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_predecessor_unbarriered"
    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_install_claim_repeats_the_predecessor_check_it_does_not_delegate_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decisive chain check lives inside the claim that installs the index.

    With the public dry check disabled the park must still refuse, proving the inner gate is a
    real barrier rather than a restatement of the pre-consent one.
    """

    case = await _v2_case(tmp_path, depth=2, archive_predecessors=False)
    monkeypatch.setattr(
        "jebao_flow.exact_restore_disposition._require_public_predecessor_chain",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_predecessor_unbarriered"
    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_cross_binding_is_checked_before_the_index_can_name_the_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared journal cross-binding runs on the stored blob before the index exists.

    The injection point is the one binder both the pre-index validator and the final loader call,
    so this fixes the ordering of that rule.  It does not exercise the archive's own decoder
    refusing the bytes; the test below that one covers that seam separately.
    """

    case = await _v2_case(tmp_path, depth=1)
    calls: list[int] = []

    def refuse_binding(*_args: Any, **_kwargs: Any) -> None:
        calls.append(1)
        raise ExactRestoreDispositionError("disposition_archive_binding_mismatch")

    monkeypatch.setattr(
        "jebao_flow.exact_restore_disposition._require_disposition_binds_journal",
        refuse_binding,
    )

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_archive_binding_mismatch"
    # The stored bytes were read back and bound once, before the index was reached.
    assert calls == [1]
    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


def test_version_one_stored_disposition_keeps_its_exact_legacy_bytes(tmp_path: Path) -> None:
    """The blob a version 1 park writes must stay byte-identical to what shipped before.

    Widening the model added four optional fields.  If any of them serialized as an explicit null
    the stored bytes would change, the archive's exact-key decoder would refuse them, and every
    already-parked operation would become unreadable.  The digest below was recomputed from the
    unwidened sources and must not move.
    """

    case = _case(tmp_path)
    disposition = case.service.park(case.authority)
    payload = disposition.model_dump(mode="json")

    assert set(payload) == {
        "action_plan_sha256",
        "authority_chain_sha256",
        "baseline_sha256",
        "completed_action_count",
        "cycle",
        "device_network_access",
        "device_write_count",
        "disposition_authority",
        "disposition_authority_sha256",
        "disposition_id",
        "evidence",
        "journal_snapshot_sha256",
        "manifest_sha256",
        "operation_finalization_sha256",
        "operation_sha256",
        "outcome",
        "pair_completion_gap_ns",
        "qualification_achieved",
        "reason",
        "recorded_at",
        "total_attempt_ns",
        "version",
    }
    assert payload["version"] == 1
    assert payload["reason"] == "final_verification_policy_overload"
    assert disposition.disposition_sha256 == (
        "0552d597dccb0c83d60de1e49f81bd89074912bea9b3557cb32d3fb072309905"
    )
    assert case.archive.load_parked_disposition(case.record.operation_id) == disposition


async def test_version_two_stored_disposition_adds_exactly_three_keys(tmp_path: Path) -> None:
    """A version 2 blob is the legacy shape plus the three fields the generic park needs."""

    case = await _v2_case(tmp_path, depth=2)
    disposition = case.service.park(case.authority)
    payload = disposition.model_dump(mode="json")
    legacy = _case(tmp_path / "legacy")
    legacy_payload = legacy.service.park(legacy.authority).model_dump(mode="json")

    assert set(payload) - set(legacy_payload) == {
        "completed_action_outcomes",
        "maximum_pair_completion_gap_seconds",
        "predecessor_depth",
    }
    assert set(legacy_payload) - set(payload) == set()
    assert case.archive.load_parked_disposition(case.record.operation_id) == disposition


async def test_version_two_park_reaches_no_network_and_writes_no_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generalised park keeps the original park's read-only guarantee for every cycle."""

    case = await _v2_case(tmp_path, depth=2)

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("network or discovery construction reached")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_datagram_endpoint", forbidden)

    eligibility = case.service.verify()
    disposition = case.service.park(case.authority)

    assert eligibility.record == case.record
    assert disposition.device_network_access is False
    assert disposition.device_write_count == 0
    assert disposition.qualification_achieved is False
    assert case.service.resume_if_archived() == disposition
    # Release the socket ban before this test's event loop is torn down; the loop closes its own
    # self-pipe with a unix socketpair, which is not the traffic this test forbids.
    monkeypatch.undo()


async def test_version_two_park_accepts_a_verified_after_uncertain_action(
    tmp_path: Path,
) -> None:
    """An action whose write was uncertain and then verified is a legitimate outcome.

    It is not part of the ordinary distribution, so it is pinned separately: the closed set has
    three members and a park must accept all three, not only the two the normal case produces.
    """

    outcomes = (
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN,
        ExactRestoreActionOutcome.ALREADY_SATISFIED,
        ExactRestoreActionOutcome.ALREADY_SATISFIED,
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN,
    )
    case = await _v2_case(tmp_path, depth=1, outcomes=outcomes)

    disposition = case.service.park(case.authority)

    assert disposition.completed_action_outcomes == outcomes
    assert case.archive.load_parked_disposition(case.record.operation_id) == disposition


def test_accepted_outcome_set_is_exactly_the_three_reasoned_about() -> None:
    """The gate that closes the outcome set is membership, not the field type.

    Widening `ExactRestoreActionOutcome` would let the field parse a new member, so this pins the
    set itself.  A fourth outcome must arrive as a deliberate change here, with the boundary
    argument revisited, rather than by inheriting acceptance from an enum edit elsewhere.
    """

    assert _ALLOWED_ACTION_OUTCOMES == frozenset(
        {
            ExactRestoreActionOutcome.ALREADY_SATISFIED,
            ExactRestoreActionOutcome.WRITTEN_VERIFIED,
            ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN,
        }
    )
    assert set(ExactRestoreActionOutcome) == _ALLOWED_ACTION_OUTCOMES


async def test_version_two_manifest_survives_its_own_canonical_round_trip(
    tmp_path: Path,
) -> None:
    """A manifest this model writes must be one the owner-only loader can read back.

    The serializer drops the optional keys it holds as `None`, so a version 2 document carries no
    `required_action_outcome`.  A plain field default put the version 1 value back on the way in
    and the loader refused the model's own output; the default is version-aware for that reason.
    """

    case = await _v2_case(tmp_path, depth=2)
    raw = case.manifest.model_dump_json()
    assert "required_action_outcome" not in json.loads(raw)

    reloaded = ParkedDispositionManifest.model_validate_json(raw)

    assert reloaded == case.manifest
    assert reloaded.manifest_sha256 == case.manifest.manifest_sha256
    assert reloaded.required_action_outcome is None

    path = tmp_path / "v2-manifest.json"
    path.write_bytes(raw.encode("utf-8"))
    path.chmod(0o600)
    loaded = load_parked_disposition_manifest(path)

    assert loaded == case.manifest
    assert loaded.manifest_sha256 == case.manifest.manifest_sha256


def test_version_one_manifest_round_trip_keeps_its_default_and_its_bytes(
    tmp_path: Path,
) -> None:
    """Making the outcome default version-aware must not change version 1 in either direction."""

    record, artifact = _awaiting_record_and_artifact()
    # The same fixed journal digest the shape golden uses, so the digest below stays comparable.
    manifest = _manifest_for(record, artifact, "a" * 64)
    raw = manifest.model_dump_json()
    payload = json.loads(raw)

    assert payload["required_action_outcome"] == "written_verified"
    assert ParkedDispositionManifest.model_validate_json(raw) == manifest
    # A version 1 document written before the field existed still gets the default.
    del payload["required_action_outcome"]
    assert ParkedDispositionManifest.model_validate(payload) == manifest
    assert manifest.manifest_sha256 == (
        "1e6b0a060c5431b0273819577d13468d523eb33db51d52989aebee19b91403b3"
    )

    path = tmp_path / "v1-manifest.json"
    path.write_bytes(raw.encode("utf-8"))
    path.chmod(0o600)
    assert load_parked_disposition_manifest(path) == manifest


async def test_predecessor_chain_is_confirmed_inside_the_active_install_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decisive chain check runs under the very claim that installs the index.

    Confirming it under a separate claim would leave a window between the check and the install.
    The claim is asserted from inside the checker, and the same assertion is shown to fail once
    the claim is released, so the check is not vacuous.
    """

    case = await _v2_case(tmp_path, depth=1)
    observed: list[tuple[int, int]] = []
    original = ExactRestoreDispositionArchive._require_archived_predecessor_chain

    def spy(self: Any, root_descriptor: int, journal_canonical: bytes) -> None:
        observed.append((self._require_active_claim(), root_descriptor))
        original(self, root_descriptor, journal_canonical)

    monkeypatch.setattr(
        ExactRestoreDispositionArchive,
        "_require_archived_predecessor_chain",
        spy,
    )

    disposition = case.service.park(case.authority)

    assert len(observed) == 1
    active_descriptor, passed_descriptor = observed[0]
    assert active_descriptor == passed_descriptor
    assert case.archive.load_parked_disposition(case.record.operation_id) == disposition
    with pytest.raises(ExactRestoreReceiptArchiveError):
        case.archive._require_active_claim()


async def test_backing_decoder_failure_leaves_no_finalization_and_no_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive's own read-back decoder is a barrier at its real seam.

    When the decoder refuses the stored bytes, the pre-index call is the only one that runs: no
    finalization is installed and the live journal is still a journal.
    """

    case = await _v2_case(tmp_path, depth=1)
    calls: list[int] = []
    original = ExactRestoreDispositionArchive._require_parked_backing_finalization

    def seam(self: Any, root_descriptor: int, finalization: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ExactRestoreReceiptArchiveError(
                "parked exact-restore disposition does not decode"
            )
        return original(self, root_descriptor, finalization)

    monkeypatch.setattr(
        ExactRestoreDispositionArchive,
        "_require_parked_backing_finalization",
        seam,
    )

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_archive_failed"
    # Only the pre-index call ran; the post-install confirmation was never reached.
    assert calls == [1]
    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_park_refuses_a_predecessor_barrier_that_names_another_receipt(
    tmp_path: Path,
) -> None:
    """A barrier that exists but binds a different receipt does not protect the ancestor."""

    case = await _v2_case(tmp_path, depth=1, archive_predecessors=False)
    ancestor = case.record.qualification_final_record
    assert ancestor is not None
    genuine = _receipt_from_final_verified_record(ancestor)
    impostor = genuine.model_copy(
        update={"final_raw_frame_sha256": (_digest("other-master"), _digest("other-slave"))}
    )
    assert impostor.receipt_sha256 != genuine.receipt_sha256
    case.archive.persist_final_verified_receipt(impostor)

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_predecessor_unbarriered"
    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_park_refuses_a_predecessor_barrier_that_no_longer_decodes(
    tmp_path: Path,
) -> None:
    """A damaged barrier file is not a barrier; it must fail closed, not read as absent."""

    case = await _v2_case(tmp_path, depth=1)
    ancestor = case.record.qualification_final_record
    assert ancestor is not None
    barriers = sorted(case.safety_root.glob("exact-restore-finalized-*.json"))
    assert len(barriers) == 1
    barriers[0].write_bytes(b"{not json\n")

    with pytest.raises(ExactRestoreDispositionError) as rejected:
        case.service.park(case.authority)

    assert rejected.value.code == "disposition_predecessor_unbarriered"
    assert case.archive.load_operation_finalization(case.record.operation_id) is None
    assert ExactRestoreRecord.model_validate(case.journal.load()) == case.record


async def test_version_two_disposition_violates_the_immediate_predecessors_accepted_shape(
    tmp_path: Path,
) -> None:
    """Shape-level proof that the binary shipped one commit ago cannot misread a version 2 park.

    This does not run that binary.  It reconstructs the two acceptance rules that binary held —
    a single fixed key set with a single reason in the archive decoder, and a `Literal[1]` record
    model — and shows a real version 2 disposition violating both.  The predecessor already
    understood version 2 *finalizations*, since the version 1 park installs one, so the
    disposition is where it has to close.  Executing the old binary against a live archive is the
    complementary half and was done out of tree during review; this is the part the repository
    can keep honest on its own.
    """

    case = await _v2_case(tmp_path, depth=2)
    disposition = case.service.park(case.authority)
    stored = disposition.model_dump(mode="json")

    # The predecessor's archive decoder: one key set, one reason, no branch on version.
    predecessor_expected_keys = {
        "version",
        "disposition_id",
        "outcome",
        "qualification_achieved",
        "device_network_access",
        "device_write_count",
        "reason",
        "manifest_sha256",
        "operation_sha256",
        "operation_finalization_sha256",
        "cycle",
        "journal_snapshot_sha256",
        "baseline_sha256",
        "action_plan_sha256",
        "authority_chain_sha256",
        "completed_action_count",
        "evidence",
        "pair_completion_gap_ns",
        "total_attempt_ns",
        "disposition_authority",
        "disposition_authority_sha256",
        "recorded_at",
    }
    assert set(stored) != predecessor_expected_keys
    assert set(stored) - predecessor_expected_keys == {
        "completed_action_outcomes",
        "maximum_pair_completion_gap_seconds",
        "predecessor_depth",
    }
    assert stored["reason"] != "final_verification_policy_overload"

    class PredecessorParkedDisposition(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)

        version: Literal[1] = 1
        reason: Literal["final_verification_policy_overload"]

    with pytest.raises(ValidationError):
        PredecessorParkedDisposition.model_validate(
            {"version": stored["version"], "reason": stored["reason"]}
        )
    # The tombstone the predecessor would find in the journal path is still not a journal record.
    with pytest.raises(ValidationError):
        ExactRestoreRecord.model_validate(case.journal.load())


async def test_archived_public_view_keeps_the_observation_apart_from_the_bound(
    tmp_path: Path,
) -> None:
    """After a park, an operator still has to be able to tell the two numbers apart.

    A version 2 record stores the approved bound, so both are shown.  A version 1 record never
    stored it and reports the observation with a null bound rather than a reconstructed one.
    """

    case = await _v2_case(tmp_path, depth=1)
    disposition = case.service.park(case.authority)
    view = public_parked_disposition(disposition)

    assert view["pair_completion_gap_ns"] == disposition.pair_completion_gap_ns
    assert view["maximum_pair_completion_gap_seconds"] == (
        case.record.baseline.verification_policy.max_final_pair_gap_seconds
    )
    assert view["pair_completion_gap_ns"] <= int(
        float(view["maximum_pair_completion_gap_seconds"]) * 1_000_000_000  # type: ignore[arg-type]
    )

    legacy = _case(tmp_path / "legacy")
    legacy_view = public_parked_disposition(legacy.service.park(legacy.authority))

    assert legacy_view["maximum_pair_completion_gap_seconds"] is None
    assert legacy_view["pair_completion_gap_ns"] == legacy.artifact.pair_completion_gap_ns
