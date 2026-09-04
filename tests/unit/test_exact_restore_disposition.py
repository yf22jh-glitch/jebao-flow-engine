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
from datetime import timedelta
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
    AttendedDispositionIssuer,
    ExactRestoreDispositionArchive,
    ExactRestoreDispositionError,
    ParkedDispositionAuthority,
    ParkedDispositionEvidenceReference,
    ParkedDispositionManifest,
    ParkedDispositionService,
    ParkedDispositionTombstone,
    load_parked_disposition_manifest,
    public_parked_tombstone_status,
)
from jebao_flow.exact_restore_receipts import (
    ExactRestoreOperationFinalization,
    ExactRestoreOperationFinalizationConflictError,
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


def _awaiting_record_and_artifact() -> tuple[ExactRestoreRecord, VerifiedPilotPairArtifact]:
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
        operation_id="operation-parked-qualification",
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


def _case(tmp_path: Path) -> SimpleNamespace:
    safety_root = tmp_path / "hardware-safety"
    safety_root.mkdir(mode=0o700, parents=True)
    journal = ExactRestoreJournalStore._for_test(safety_root / "exact-restore.json")
    archive = ExactRestoreDispositionArchive._for_test(safety_root)
    record, artifact = _awaiting_record_and_artifact()
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
