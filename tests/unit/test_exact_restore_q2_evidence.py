from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.exact_restore import (
    ExactRestoreController,
    ExactRestoreCycle,
    ExactRestoreReceipt,
    ExactRestoreRecord,
    _receipt_from_final_verified_record,
    prepare_qualified_final_restore_record,
)
from jebao_flow.exact_restore_composition import ExactRestoreOperationManifest
from jebao_flow.exact_restore_q2_evidence import (
    ExactRestoreQ2EvidenceError,
    ExactRestoreQ2EvidenceVerifier,
    QualifiedBaselineArchive,
    QualifiedBaselineBundle,
    _baseline_artifact_id,
    build_qualified_baseline_bundle,
)
from jebao_flow.exact_restore_receipts import ExactRestoreReceiptArchive
from jebao_flow.q2_epoch import ExactRestoreEvidence

NOW = datetime(2026, 8, 30, 6, 0, tzinfo=UTC)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _manifest(*, nonce: str = "1" * 32) -> ExactRestoreOperationManifest:
    return ExactRestoreOperationManifest.model_validate(
        {
            "operation_nonce": nonce,
            "evidence": {
                "plan_artifact_id": "JFP-test",
                "plan_sha256": _digest("plan"),
                "series_artifact_id": "JFS-test",
                "series_sha256": _digest("series"),
                "accepted_pair_ordinal": 0,
            },
            "devices": [
                {
                    "role": "master",
                    "logical_id": "pump_a",
                    "power_policy": {
                        "min_power": 30,
                        "max_power": 80,
                        "power_step": 1,
                        "attended_max_power": 80,
                    },
                    "safe_constant_power": 30,
                    "safe_constant_frequency": 30,
                },
                {
                    "role": "slave",
                    "logical_id": "pump_b",
                    "power_policy": {
                        "min_power": 30,
                        "max_power": 80,
                        "power_step": 1,
                        "attended_max_power": 80,
                    },
                    "safe_constant_power": 30,
                    "safe_constant_frequency": 30,
                },
            ],
            "verification_policy": {
                "max_observation_age_seconds": 30,
                "max_final_pair_gap_seconds": 20,
            },
        }
    )


def _receipts(
    manifest: ExactRestoreOperationManifest,
    *,
    baseline_sha256: str | None = None,
    qualification_override: str | None = None,
    completed_at: datetime = NOW,
) -> tuple[ExactRestoreReceipt, ExactRestoreReceipt]:
    baseline_sha = baseline_sha256 or _digest("baseline")
    sentinel = ExactRestoreReceipt(
        operation_id=manifest.sentinel_operation_id,
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        baseline_sha256=baseline_sha,
        action_plan_sha256=_digest("sentinel-plan"),
        authority_sha256=_digest("sentinel-authority"),
        authority_chain_sha256=_digest("sentinel-chain"),
        qualification_receipt_sha256=None,
        completed_action_count=8,
        final_raw_frame_sha256=(_digest("sentinel-a"), _digest("sentinel-b")),
        completed_at=completed_at,
    )
    baseline = ExactRestoreReceipt(
        operation_id=manifest.baseline_operation_id,
        cycle=ExactRestoreCycle.BASELINE_RESTORE,
        baseline_sha256=baseline_sha,
        action_plan_sha256=_digest("baseline-plan"),
        authority_sha256=_digest("baseline-authority"),
        authority_chain_sha256=_digest("baseline-chain"),
        qualification_receipt_sha256=(
            sentinel.receipt_sha256 if qualification_override is None else qualification_override
        ),
        completed_action_count=6,
        final_raw_frame_sha256=(_digest("baseline-a"), _digest("baseline-b")),
        completed_at=completed_at + timedelta(seconds=1),
    )
    return sentinel, baseline


def _bundle(
    manifest: ExactRestoreOperationManifest,
    sentinel: ExactRestoreReceipt,
    baseline: ExactRestoreReceipt,
    record: ExactRestoreRecord,
    *,
    persisted_at: datetime | None = None,
    maximum_handoff_age_seconds: float | None = None,
) -> QualifiedBaselineBundle:
    return QualifiedBaselineBundle(
        artifact_id=_baseline_artifact_id(
            baseline.baseline_sha256,
            baseline.receipt_sha256,
        ),
        baseline_sha256=baseline.baseline_sha256,
        sentinel_operation_id=manifest.sentinel_operation_id,
        sentinel_receipt_sha256=sentinel.receipt_sha256,
        baseline_operation_id=manifest.baseline_operation_id,
        baseline_receipt_sha256=baseline.receipt_sha256,
        qualification_receipt_sha256=sentinel.receipt_sha256,
        qualified_record=record,
        expected_identity_bindings_sha256=tuple(
            device.identity_binding_sha256 for device in record.baseline.devices
        ),
        maximum_handoff_age_seconds=(
            manifest.verification_policy.max_observation_age_seconds
            if maximum_handoff_age_seconds is None
            else maximum_handoff_age_seconds
        ),
        sentinel_completed_at=sentinel.completed_at,
        baseline_completed_at=baseline.completed_at,
        persisted_at=persisted_at or baseline.completed_at + timedelta(seconds=1),
    )


def _qualified_chain(
    manifest: ExactRestoreOperationManifest,
) -> tuple[ExactRestoreRecord, ExactRestoreReceipt, ExactRestoreReceipt]:
    """Build a real finalized chain; bundle validation must never rely on loose test doubles."""

    from tests.unit import test_exact_restore as kit

    qualification = kit._sentinel_final_record(operation_id=manifest.sentinel_operation_id)
    store = kit.MemoryStore()
    store.record = qualification.model_dump(mode="json")
    guard = kit.FakeGuard()
    receipt_store = kit.MemoryQualificationReceiptStore()
    harness = kit.RestoreHarness(qualification.baseline, store)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=receipt_store,
        clock=lambda: kit.NOW,
        monotonic_clock=lambda: kit.MONOTONIC_NS,
        boot_identity=lambda: kit.BOOT_A_SHA256,
    )
    promoted = controller.promote_to_baseline_restore(operation_id=manifest.baseline_operation_id)
    controller.arm(kit._authority(promoted))
    finalized = asyncio.run(controller.execute())
    baseline_receipt = asyncio.run(controller.finalize())
    sentinel_receipt = _receipt_from_final_verified_record(qualification)
    return finalized, sentinel_receipt, baseline_receipt


def _archives(tmp_path: Path):
    root = tmp_path / "safety"
    root.mkdir(mode=0o700)
    return (
        ExactRestoreReceiptArchive._for_test(root),
        QualifiedBaselineArchive._for_test(root),
    )


class _MemoryJournal:
    def __init__(self, record: ExactRestoreRecord | None) -> None:
        self.record = record

    def load(self) -> dict[str, object] | None:
        return None if self.record is None else self.record.model_dump(mode="json")


def _complete_with_journal(
    tmp_path: Path,
    *,
    manifest: ExactRestoreOperationManifest | None = None,
):
    selected = manifest or _manifest()
    receipts, qualified = _archives(tmp_path)
    record, sentinel, baseline = _qualified_chain(selected)
    receipts.persist_final_verified_receipt(sentinel)
    receipts.persist_final_verified_receipt(baseline)
    bundle = _bundle(selected, sentinel, baseline, record)
    qualified.persist(bundle)
    staged = prepare_qualified_final_restore_record(
        record,
        operation_id=selected.final_restore_operation_id,
        now=bundle.persisted_at,
    )
    journal = _MemoryJournal(staged)
    verifier = ExactRestoreQ2EvidenceVerifier(
        selected,
        receipt_archive=receipts,
        qualified_archive=qualified,
        journal_store=journal,
        clock=lambda: bundle.persisted_at + timedelta(seconds=1),
    )
    return verifier, bundle, journal


def _complete(tmp_path: Path, *, manifest: ExactRestoreOperationManifest | None = None):
    verifier, bundle, _journal = _complete_with_journal(tmp_path, manifest=manifest)
    return verifier, bundle


def test_bundle_builder_uses_final_record_binding_order_and_verification_policy() -> None:
    manifest = _manifest()
    record, _sentinel, baseline = _qualified_chain(manifest)

    bundle = build_qualified_baseline_bundle(
        manifest=manifest,
        record=record,
        baseline_receipt=baseline,
        now=baseline.completed_at + timedelta(seconds=1),
    )

    assert bundle.expected_identity_bindings_sha256 == tuple(
        device.identity_binding_sha256 for device in record.baseline.devices
    )
    assert (
        bundle.maximum_handoff_age_seconds
        == manifest.verification_policy.max_observation_age_seconds
    )

    changed_manifest = manifest.model_copy(
        update={
            "verification_policy": manifest.verification_policy.model_copy(
                update={"max_observation_age_seconds": 600}
            )
        }
    )
    with pytest.raises(ExactRestoreQ2EvidenceError, match="qualified_chain_mismatch"):
        build_qualified_baseline_bundle(
            manifest=changed_manifest,
            record=record,
            baseline_receipt=baseline,
            now=baseline.completed_at + timedelta(seconds=1),
        )


def test_verifier_derives_exact_evidence_from_two_finalization_indexes(tmp_path: Path) -> None:
    verifier, bundle = _complete(tmp_path)
    expected_staged = prepare_qualified_final_restore_record(
        bundle.qualified_record,
        operation_id=_manifest().final_restore_operation_id,
        now=bundle.persisted_at,
    )

    derived = verifier.derive_q2_exact_restore_evidence()

    assert derived == ExactRestoreEvidence(
        baseline_artifact_id=bundle.artifact_id,
        baseline_sha256=bundle.baseline_sha256,
        sentinel_receipt_id=f"ERQ-{bundle.sentinel_receipt_sha256}",
        sentinel_receipt_sha256=bundle.sentinel_receipt_sha256,
        baseline_receipt_id=f"ERQ-{bundle.baseline_receipt_sha256}",
        baseline_receipt_sha256=bundle.baseline_receipt_sha256,
        final_restore_operation_id=_manifest().final_restore_operation_id,
        final_restore_record_sha256=expected_staged.authority_context_sha256,
        expected_identity_bindings_sha256=bundle.expected_identity_bindings_sha256,
    )
    assert (
        verifier.verify_q2_exact_restore_evidence(
            derived,
            not_after=bundle.persisted_at + timedelta(seconds=1),
        )
        == derived
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("baseline_artifact_id", "ERB-" + "0" * 64),
        ("baseline_sha256", "0" * 64),
        ("sentinel_receipt_id", "ERQ-" + "0" * 64),
        ("sentinel_receipt_sha256", "0" * 64),
        ("baseline_receipt_id", "ERQ-" + "0" * 64),
        ("baseline_receipt_sha256", "0" * 64),
        ("final_restore_operation_id", "er-final-" + "0" * 64),
        ("final_restore_record_sha256", "0" * 64),
        ("expected_identity_bindings_sha256", ("0" * 64, "1" * 64)),
    ],
)
def test_forged_caller_claim_is_never_accepted(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    verifier, bundle = _complete(tmp_path)
    valid = verifier.derive_q2_exact_restore_evidence()
    forged = replace(valid, **{field: replacement})

    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_restore_evidence_claim_mismatch"):
        verifier.verify_q2_exact_restore_evidence(
            forged,
            not_after=bundle.persisted_at + timedelta(seconds=1),
        )


def test_different_manifest_cannot_reuse_stale_finalizations(tmp_path: Path) -> None:
    verifier, bundle = _complete(tmp_path, manifest=_manifest())
    valid = verifier.derive_q2_exact_restore_evidence()
    receipts = ExactRestoreReceiptArchive._for_test(bundle_path := tmp_path / "safety")
    qualified = QualifiedBaselineArchive._for_test(bundle_path)
    stale = ExactRestoreQ2EvidenceVerifier(
        _manifest(nonce="2" * 32),
        receipt_archive=receipts,
        qualified_archive=qualified,
        clock=lambda: NOW + timedelta(minutes=1),
    )

    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_restore_unfinalized"):
        stale.verify_q2_exact_restore_evidence(
            valid,
            not_after=NOW + timedelta(minutes=1),
        )


def test_q2_evidence_requires_the_phase5_restore_to_remain_staged(tmp_path: Path) -> None:
    verifier, bundle, journal = _complete_with_journal(tmp_path)

    journal.record = None
    assert verifier.load_verified_qualified_bundle() == bundle
    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_final_restore_not_staged"):
        verifier.derive_q2_exact_restore_evidence()


def test_historical_q2_evidence_remains_verifiable_after_phase5_clears_journal(
    tmp_path: Path,
) -> None:
    verifier, bundle, journal = _complete_with_journal(tmp_path)
    evidence = verifier.derive_q2_exact_restore_evidence()

    journal.record = None

    assert (
        verifier.verify_q2_historical_exact_restore_evidence(
            evidence,
            not_after=bundle.persisted_at + timedelta(seconds=1),
        )
        == evidence
    )


def test_q2_evidence_rejects_a_different_prepared_restore_plan(tmp_path: Path) -> None:
    verifier, bundle, journal = _complete_with_journal(tmp_path)
    journal.record = prepare_qualified_final_restore_record(
        bundle.qualified_record,
        operation_id="er-final-different",
        now=bundle.persisted_at,
    )

    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_final_restore_stage_mismatch"):
        verifier.derive_q2_exact_restore_evidence()


def test_receipt_without_finalization_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    _receipts_archive, qualified = _archives(tmp_path)

    class ReceiptOnly:
        def load_operation_finalization(self, operation_id: str):
            return None

        def load_final_verified_receipt(self, receipt_sha256: str):
            raise AssertionError("receipt must not be consulted without finalization")

        def confirm_operation_finalization(self, receipt: ExactRestoreReceipt):
            raise AssertionError("unfinalized receipt cannot be confirmed")

    verifier = ExactRestoreQ2EvidenceVerifier(
        manifest,
        receipt_archive=ReceiptOnly(),
        qualified_archive=qualified,
        clock=lambda: NOW,
    )
    fake = ExactRestoreEvidence(
        baseline_artifact_id="ERB-" + "0" * 64,
        baseline_sha256="0" * 64,
        sentinel_receipt_id="ERQ-" + "0" * 64,
        sentinel_receipt_sha256="0" * 64,
        baseline_receipt_id="ERQ-" + "1" * 64,
        baseline_receipt_sha256="1" * 64,
        final_restore_operation_id="er-final-" + "4" * 64,
        final_restore_record_sha256="5" * 64,
        expected_identity_bindings_sha256=("2" * 64, "3" * 64),
    )
    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_restore_unfinalized"):
        verifier.verify_q2_exact_restore_evidence(fake, not_after=NOW)


def test_q2_manifest_cutoff_overrides_later_adapter_clock(tmp_path: Path) -> None:
    verifier, bundle = _complete(tmp_path)
    valid = verifier.derive_q2_exact_restore_evidence()

    with pytest.raises(
        ExactRestoreQ2EvidenceError,
        match="q2_restore_not_completed_before_manifest",
    ):
        verifier.verify_q2_exact_restore_evidence(
            valid,
            not_after=bundle.persisted_at - timedelta(microseconds=1),
        )


def test_completed_restore_outside_manifest_handoff_window_is_stale(tmp_path: Path) -> None:
    verifier, bundle = _complete(tmp_path)
    valid = verifier.derive_q2_exact_restore_evidence()

    with pytest.raises(
        ExactRestoreQ2EvidenceError,
        match="q2_restore_not_completed_before_manifest",
    ):
        verifier.verify_q2_exact_restore_evidence(
            valid,
            not_after=(
                bundle.baseline_completed_at
                + timedelta(seconds=bundle.maximum_handoff_age_seconds)
                + timedelta(microseconds=1)
            ),
        )


def test_future_completion_cannot_authorize_earlier_q2_manifest(tmp_path: Path) -> None:
    manifest = _manifest()
    receipts, qualified = _archives(tmp_path)
    record, sentinel, baseline = _qualified_chain(manifest)
    receipts.persist_final_verified_receipt(sentinel)
    receipts.persist_final_verified_receipt(baseline)
    bundle = _bundle(
        manifest,
        sentinel,
        baseline,
        record,
        persisted_at=baseline.completed_at + timedelta(seconds=1),
    )
    qualified.persist(bundle)
    verifier = ExactRestoreQ2EvidenceVerifier(
        manifest,
        receipt_archive=receipts,
        qualified_archive=qualified,
        clock=lambda: baseline.completed_at - timedelta(microseconds=1),
    )

    with pytest.raises(
        ExactRestoreQ2EvidenceError,
        match="q2_restore_not_completed_before_manifest",
    ):
        verifier.derive_q2_exact_restore_evidence()


def test_mismatched_qualification_chain_fails_before_bundle_claim(tmp_path: Path) -> None:
    manifest = _manifest()
    receipts, qualified = _archives(tmp_path)
    sentinel, baseline = _receipts(
        manifest,
        qualification_override=_digest("other-qualification"),
    )
    receipts.persist_final_verified_receipt(sentinel)
    receipts.persist_final_verified_receipt(baseline)
    verifier = ExactRestoreQ2EvidenceVerifier(
        manifest,
        receipt_archive=receipts,
        qualified_archive=qualified,
        clock=lambda: NOW + timedelta(minutes=1),
    )

    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_restore_receipt_chain_invalid"):
        verifier.derive_q2_exact_restore_evidence()


def test_cross_baseline_final_receipts_are_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    receipts, qualified = _archives(tmp_path)
    sentinel, baseline = _receipts(manifest)
    cross_baseline_payload = baseline.model_dump(mode="json")
    cross_baseline_payload["baseline_sha256"] = _digest("different-baseline")
    cross_baseline = ExactRestoreReceipt.model_validate(cross_baseline_payload)
    receipts.persist_final_verified_receipt(sentinel)
    receipts.persist_final_verified_receipt(cross_baseline)
    verifier = ExactRestoreQ2EvidenceVerifier(
        manifest,
        receipt_archive=receipts,
        qualified_archive=qualified,
        clock=lambda: NOW + timedelta(minutes=1),
    )

    with pytest.raises(ExactRestoreQ2EvidenceError, match="q2_restore_receipt_chain_invalid"):
        verifier.derive_q2_exact_restore_evidence()


def test_bundle_file_cannot_claim_a_different_embedded_artifact(tmp_path: Path) -> None:
    _receipts_archive, archive = _archives(tmp_path)
    manifest = _manifest()
    record, sentinel, baseline = _qualified_chain(manifest)
    expected = _bundle(manifest, sentinel, baseline, record)
    archive.persist(expected)
    other_manifest = _manifest(nonce="2" * 32)
    other_record, other_sentinel, other_baseline = _qualified_chain(other_manifest)
    other = _bundle(other_manifest, other_sentinel, other_baseline, other_record)
    artifact_name = f"exact-restore-q2-qualified-{expected.artifact_id[4:]}.json"
    stored_path = tmp_path / "safety" / artifact_name
    stored_path.write_bytes(
        json.dumps(
            other.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    stored_path.chmod(0o600)

    with pytest.raises(ExactRestoreQ2EvidenceError, match="qualified_bundle_artifact_mismatch"):
        archive.load(expected.artifact_id)


def test_bundle_cannot_expand_manifest_handoff_window(tmp_path: Path) -> None:
    manifest = _manifest()
    record, sentinel, baseline = _qualified_chain(manifest)
    with pytest.raises(ValueError, match="qualified baseline record does not match"):
        _bundle(
            manifest,
            sentinel,
            baseline,
            record,
            maximum_handoff_age_seconds=(
                manifest.verification_policy.max_observation_age_seconds + 1
            ),
        )
