from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.session import STATE_REPLY_ACTION
from jebao_flow.q2_epoch import (
    EpochConclusion,
    ExactRestoreEvidence,
    PilotTimingEvidence,
    Q2EpochError,
    Q2EpochKind,
    Q2EpochManifestReference,
    Q2EpochStore,
    Q2FinalJudgment,
    Q2ScheduleExpectation,
    Q2Thresholds,
    _classify_complete_series,
    _decode_sample,
    combine_q2_epoch_receipts,
)
from jebao_flow.read_only_collector import (
    PilotPlanReference,
    VerifiedPilotInterval,
    VerifiedPilotRawSample,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def test_q2_import_graph_does_not_load_device_or_frozen_write_modules() -> None:
    source = """
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'src'))
import jebao_flow.q2_epoch
prefixes = (
    'jebao_flow.devices',
    'jebao_flow.hardware_test',
    'jebao_flow.protocol.connection',
    'jebao_flow.protocol.control_session',
    'jebao_flow.schedule_flow_experiment_cli',
    'jebao_flow.schedule_linkage_cli',
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(prefixes))))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []


def test_q2_decode_rederives_transport_schedule_role_and_auto_flow_from_raw() -> None:
    raw = bytearray(452)
    raw[0] = 0b11  # enabled, TimerON, independent
    raw[1] = 2  # manual constant
    raw[2] = 35
    raw[6] = 2  # AutoMode constant
    raw[7] = 32
    raw[11:20] = bytes((0, 0, 24, 0, 2, 32, 0, 0, 0))
    raw[443:451] = bytes((20, 26, 8, 30, 0, 0, 0, 10))
    wire = encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes([STATE_REPLY_ACTION]) + raw,
    )
    interval = VerifiedPilotInterval(
        started_utc="2026-08-30T00:00:00Z",
        completed_utc="2026-08-30T00:00:01Z",
        started_monotonic_ns=1,
        completed_monotonic_ns=2,
    )
    sample = VerifiedPilotRawSample(
        role="b",
        identity_binding_sha256=SHA_B,
        sample_manifest_sha256=SHA_A,
        raw_wire_frame_sha256=hashlib.sha256(wire).hexdigest(),
        attempt=interval,
        identity_before=interval,
        read=interval,
        identity_after=interval,
        raw_wire_frame=wire,
    )

    decoded = _decode_sample(sample)

    assert decoded.auto_flow == 32
    assert decoded.auto_mode == "constant"
    assert decoded.linkage == "independent"
    assert decoded.timer_on is True
    assert decoded.schedule_entries[0]["parameters"]["flow"] == 32

    invalid_frequency = bytearray(raw)
    invalid_frequency[8] = 0xEE
    invalid_wire = encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes([STATE_REPLY_ACTION]) + invalid_frequency,
    )
    with pytest.raises(Q2EpochError, match="q2_auto_frequency_invalid"):
        _decode_sample(replace(sample, raw_wire_frame=invalid_wire))


def _private_root(tmp_path: Path, name: str = "q2") -> Path:
    root = tmp_path / name
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _plan(tmp_path: Path, *, count: int = 286, cadence_ns: int = 20_000_000_000):
    directory = _private_root(tmp_path, "collector") / "JFS-measurement"
    directory.mkdir(mode=0o700)
    (directory / "attempts").mkdir(mode=0o700)
    return PilotPlanReference(
        plan_artifact_id="JFP-measurement",
        series_id="JFS-measurement",
        plan_sha256=SHA_C,
        epoch="measurement",
        planned_pair_count=count,
        requested_cadence_ns=cadence_ns,
        series_directory=directory,
    )


def _plan_claim(plan: PilotPlanReference) -> dict:
    return {
        "ordered_targets": [
            {
                "expected_identity_binding_sha256": SHA_A,
                "logical_id": "master",
                "product_key": "50dbc92221fd4d33ae69a1fedd43b555",
            },
            {
                "expected_identity_binding_sha256": SHA_B,
                "logical_id": "slave",
                "product_key": "50dbc92221fd4d33ae69a1fedd43b555",
            },
        ],
        "acquisition": {
            "planned_pair_count": plan.planned_pair_count,
            "requested_cadence_ns": plan.requested_cadence_ns,
            "retry_policy": "none",
            "accept_reports": False,
        },
    }


class _FakeCollectorStore:
    def __init__(self, plan: PilotPlanReference) -> None:
        self.plan = plan
        self.references = {
            "JFS-timing-one": replace(
                plan,
                plan_artifact_id="JFP-timing-one",
                series_id="JFS-timing-one",
                plan_sha256=SHA_A,
            ),
            "JFS-timing-two": replace(
                plan,
                plan_artifact_id="JFP-timing-two",
                series_id="JFS-timing-two",
                plan_sha256=SHA_B,
            ),
        }

    def verify_plan(self, reference: PilotPlanReference) -> dict:
        return _plan_claim(reference)

    def load(self, series_id: str) -> PilotPlanReference:
        return self.references[series_id]

    def verify_completed_series(
        self, reference: PilotPlanReference, *, expected_series_sha256: str
    ) -> dict:
        assert expected_series_sha256 in {SHA_C, SHA_D}
        return {
            "started": {"started_utc": "2026-08-28T00:00:00Z"},
            "completed": {"completed_utc": "2026-08-28T00:10:00Z"},
            "status": "pilot_completed_all_acquisitions_accepted",
            "completed_pair_count": reference.planned_pair_count,
        }

    def extract_verified_accepted_pair(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ) -> SimpleNamespace:
        assert expected_series_sha256 in {SHA_C, SHA_D}
        return SimpleNamespace(
            attempt=SimpleNamespace(started_monotonic_ns=ordinal * 20_000_000_000),
            pair_completion_gap_ns=10_000_000_000,
        )


class _ExactVerifier:
    def verify_q2_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        assert not_after.tzinfo is not None
        return evidence

    def verify_q2_historical_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        assert not_after.tzinfo is not None
        return evidence


class _CompletionCutoffVerifier:
    def __init__(self, completed_at: datetime) -> None:
        self.completed_at = completed_at
        self.cutoffs: list[datetime] = []

    def verify_q2_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        self.cutoffs.append(not_after)
        if self.completed_at > not_after:
            raise Q2EpochError("q2_restore_completion_after_manifest")
        return evidence

    def verify_q2_historical_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        return self.verify_q2_exact_restore_evidence(evidence, not_after=not_after)


def _restore() -> ExactRestoreEvidence:
    return ExactRestoreEvidence(
        baseline_artifact_id="ERB-baseline",
        baseline_sha256=SHA_A,
        sentinel_receipt_id="ERQ-sentinel",
        sentinel_receipt_sha256=SHA_B,
        baseline_receipt_id="ERQ-baseline",
        baseline_receipt_sha256=SHA_C,
        final_restore_operation_id="er-final-baseline",
        final_restore_record_sha256=SHA_D,
        expected_identity_bindings_sha256=(SHA_A, SHA_B),
    )


def _thresholds(plan: PilotPlanReference) -> Q2Thresholds:
    return Q2Thresholds(
        requested_cadence_ns=plan.requested_cadence_ns,
        maximum_actual_cadence_ns=22_000_000_000,
        maximum_pair_gap_ns=12_500_000_000,
        freshness_window_ns=44_000_000_000,
        boundary_exclusion_ns=12_500_000_000,
        stability_window_ns=300_000_000_000,
    )


def _schedule() -> Q2ScheduleExpectation:
    return Q2ScheduleExpectation(
        master_schedule_image_sha256=SHA_C,
        slave_schedule_image_sha256=SHA_D,
    )


def test_prepare_commits_owner_only_prestarted_manifest_and_detects_tamper(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    store = Q2EpochStore(_private_root(tmp_path))
    reference = store.prepare(
        _FakeCollectorStore(plan),  # type: ignore[arg-type]
        plan,
        epoch_kind=Q2EpochKind.INDEPENDENT_CONTROL,
        timing_evidence=(
            PilotTimingEvidence("JFS-timing-one", SHA_C),
            PilotTimingEvidence("JFS-timing-two", SHA_D),
        ),
        restore_evidence=_restore(),
        restore_evidence_verifier=_ExactVerifier(),
        thresholds=_thresholds(plan),
        schedule=_schedule(),
        utc_clock=lambda: datetime(2026, 8, 30, tzinfo=UTC),
    )

    manifest = store.verify_manifest(reference)
    assert manifest["expected_roles"] == ["independent", "independent"]
    assert manifest["acquisition_contract"]["write_count"] == 0
    assert stat.S_IMODE((reference.directory / "manifest.json").stat().st_mode) == 0o600
    path = reference.directory / "manifest.json"
    value = json.loads(path.read_text())
    value["thresholds"]["stability_window_ns"] = 1
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(Q2EpochError, match="q2_manifest_digest_mismatch"):
        store.verify_manifest(reference)


def test_prepare_uses_one_utc_cutoff_for_archive_verification_and_manifest(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    collector = _FakeCollectorStore(plan)
    store = Q2EpochStore(_private_root(tmp_path))
    created_at = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)
    clock_values = iter((created_at, created_at - timedelta(hours=1)))
    verifier = _CompletionCutoffVerifier(created_at - timedelta(seconds=1))

    reference = store.prepare(
        collector,  # type: ignore[arg-type]
        plan,
        epoch_kind=Q2EpochKind.INDEPENDENT_CONTROL,
        timing_evidence=(
            PilotTimingEvidence("JFS-timing-one", SHA_C),
            PilotTimingEvidence("JFS-timing-two", SHA_D),
        ),
        restore_evidence=_restore(),
        restore_evidence_verifier=verifier,
        thresholds=_thresholds(plan),
        schedule=_schedule(),
        utc_clock=lambda: next(clock_values),
    )

    manifest = store.verify_manifest(reference)
    assert verifier.cutoffs == [created_at]
    assert manifest["created_utc"] == "2026-08-30T04:00:00Z"
    # A second clock read would have returned a regressed value; prepare consumed exactly one.
    assert next(clock_values) == created_at - timedelta(hours=1)


def test_prepare_rejects_restore_completion_after_fixed_manifest_cutoff(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    created_at = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)
    verifier = _CompletionCutoffVerifier(created_at + timedelta(microseconds=1))

    with pytest.raises(Q2EpochError, match="q2_restore_completion_after_manifest"):
        Q2EpochStore(_private_root(tmp_path)).prepare(
            _FakeCollectorStore(plan),  # type: ignore[arg-type]
            plan,
            epoch_kind=Q2EpochKind.ASYNC,
            timing_evidence=(
                PilotTimingEvidence("JFS-timing-one", SHA_C),
                PilotTimingEvidence("JFS-timing-two", SHA_D),
            ),
            restore_evidence=_restore(),
            restore_evidence_verifier=verifier,
            thresholds=_thresholds(plan),
            schedule=_schedule(),
            utc_clock=lambda: created_at,
        )
    assert verifier.cutoffs == [created_at]


def test_classify_reuses_persisted_manifest_cutoff_for_restore_reverification(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    collector = _FakeCollectorStore(plan)
    store = Q2EpochStore(_private_root(tmp_path))
    created_at = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)
    reference = store.prepare(
        collector,  # type: ignore[arg-type]
        plan,
        epoch_kind=Q2EpochKind.ASYNC,
        timing_evidence=(
            PilotTimingEvidence("JFS-timing-one", SHA_C),
            PilotTimingEvidence("JFS-timing-two", SHA_D),
        ),
        restore_evidence=_restore(),
        restore_evidence_verifier=_CompletionCutoffVerifier(created_at - timedelta(seconds=1)),
        thresholds=_thresholds(plan),
        schedule=_schedule(),
        utc_clock=lambda: created_at,
    )
    forged_future = _CompletionCutoffVerifier(created_at + timedelta(seconds=1))

    with pytest.raises(Q2EpochError, match="q2_restore_completion_after_manifest"):
        store.classify_and_commit(
            reference,
            collector,  # type: ignore[arg-type]
            expected_series_sha256=SHA_C,
            restore_evidence_verifier=forged_future,
        )
    assert forged_future.cutoffs == [created_at]


def test_prepare_refuses_started_series_and_unverified_timing(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    (plan.series_directory / "started.json").write_text("{}")
    store = Q2EpochStore(_private_root(tmp_path))
    with pytest.raises(Q2EpochError, match="q2_collector_series_already_started"):
        store.prepare(
            _FakeCollectorStore(plan),  # type: ignore[arg-type]
            plan,
            epoch_kind=Q2EpochKind.ASYNC,
            timing_evidence=(PilotTimingEvidence("JFS-timing-one", SHA_C),),
            restore_evidence=_restore(),
            restore_evidence_verifier=_ExactVerifier(),
            thresholds=_thresholds(plan),
            schedule=_schedule(),
        )


def test_prepare_rejects_thresholds_not_deterministically_derived_from_pilots(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    store = Q2EpochStore(_private_root(tmp_path))
    loosened = replace(_thresholds(plan), maximum_pair_gap_ns=99_000_000_000)
    with pytest.raises(Q2EpochError, match="q2_thresholds_not_pilot_derived"):
        store.prepare(
            _FakeCollectorStore(plan),  # type: ignore[arg-type]
            plan,
            epoch_kind=Q2EpochKind.ASYNC,
            timing_evidence=(
                PilotTimingEvidence("JFS-timing-one", SHA_C),
                PilotTimingEvidence("JFS-timing-two", SHA_D),
            ),
            restore_evidence=_restore(),
            restore_evidence_verifier=_ExactVerifier(),
            thresholds=loosened,
            schedule=_schedule(),
        )


def test_load_manifest_requires_external_digest_trust_root(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    collector = _FakeCollectorStore(plan)
    store = Q2EpochStore(_private_root(tmp_path))
    reference = store.prepare(
        collector,  # type: ignore[arg-type]
        plan,
        epoch_kind=Q2EpochKind.ASYNC,
        timing_evidence=(
            PilotTimingEvidence("JFS-timing-one", SHA_C),
            PilotTimingEvidence("JFS-timing-two", SHA_D),
        ),
        restore_evidence=_restore(),
        restore_evidence_verifier=_ExactVerifier(),
        thresholds=_thresholds(plan),
        schedule=_schedule(),
    )
    with pytest.raises(Q2EpochError, match="q2_manifest_marker_mismatch"):
        store.load_manifest(
            reference.manifest_id,
            collector,  # type: ignore[arg-type]
            expected_manifest_sha256=SHA_D,
        )


MASTER_ENTRIES = (
    {
        "slot": 0,
        "start": "00:00",
        "end": "24:00",
        "mode": "constant",
        "parameters": {"flow": 35},
    },
)


def _schedule_clock(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


SLAVE_ENTRIES = tuple(
    {
        "slot": index,
        "start": _schedule_clock(index * 30),
        "end": _schedule_clock((index + 1) * 30),
        "mode": "constant",
        "parameters": {"flow": 32 if index % 2 == 0 else 40},
    }
    for index in range(48)
)


def _active_slave_flow(at: datetime) -> int:
    minute = at.hour * 60 + at.minute
    return 32 if (minute // 30) % 2 == 0 else 40


def _pair(ordinal: int, at: datetime, *, slave_flow: int | None = None):
    from jebao_flow.q2_epoch import _DecodedPair, _DecodedSample

    host_ns = ordinal * 20_000_000_000
    common = {
        "identity_binding_sha256": SHA_A,
        "read_started_ns": host_ns,
        "read_completed_ns": host_ns + 1_000_000_000,
        "device_time": at,
        "enabled": True,
        "timer_on": True,
        "linkage": "independent",
        "auto_mode": "constant",
        "auto_frequency": 20,
        "schedule_image_sha256": SHA_C,
        "schedule_entries": MASTER_ENTRIES,
        "liveness_valid": True,
    }
    master = _DecodedSample(role="a", auto_flow=35, **common)
    slave = _DecodedSample(
        role="b",
        identity_binding_sha256=SHA_B,
        read_started_ns=host_ns + 10_000_000_000,
        read_completed_ns=host_ns + 11_000_000_000,
        device_time=at,
        enabled=True,
        timer_on=True,
        linkage="independent",
        auto_mode="constant",
        auto_flow=_active_slave_flow(at) if slave_flow is None else slave_flow,
        auto_frequency=20,
        schedule_image_sha256=SHA_D,
        schedule_entries=SLAVE_ENTRIES,
        liveness_valid=True,
    )
    return _DecodedPair(
        ordinal=ordinal,
        pair_started_ns=host_ns,
        pair_gap_ns=10_000_000_000,
        master=master,
        slave=slave,
    )


def _classification_manifest(count: int) -> dict:
    return {
        "collector": {"planned_pair_count": count},
        "expected_identity_bindings_sha256": [SHA_A, SHA_B],
        "expected_roles": ["independent", "independent"],
        "thresholds": {
            "requested_cadence_ns": 20_000_000_000,
            "maximum_actual_cadence_ns": 21_000_000_000,
            "maximum_pair_gap_ns": 15_000_000_000,
            "freshness_window_ns": 15_000_000_000,
            "boundary_exclusion_ns": 5_000_000_000,
            "stability_window_ns": 300_000_000_000,
        },
        "schedule_expectation": {
            "master_schedule_image_sha256": SHA_C,
            "slave_schedule_image_sha256": SHA_D,
            "master_flow": 35,
            "slave_flows": [32, 40],
            "master_slot_count": 1,
            "slave_slot_count": 48,
            "slave_slot_duration_seconds": 1_800,
        },
    }


def _complete_pairs() -> list:
    start = datetime(2026, 8, 30, 0, 0, 10)
    return [_pair(i, start + timedelta(seconds=20 * i)) for i in range(286)]


def test_classifier_establishes_only_complete_three_boundary_plus_stability_series() -> None:
    pairs = _complete_pairs()
    result = _classify_complete_series(_classification_manifest(len(pairs)), pairs, [])

    assert result["conclusion"] == EpochConclusion.INDEPENDENTLY_APPLIED.value
    assert result["valid_boundary_count"] == 3
    assert result["rejected_boundary_count"] == 0
    assert result["post_third_stability_satisfied"] is True


def test_omitted_pair_breaks_consecutive_run_instead_of_hiding_failure() -> None:
    pairs = _complete_pairs()
    # Ordinal 90 is the first sample after the 00:30 boundary.  Keeping later data must not let
    # the verifier cherry-pick three apparently good boundaries.
    pairs[90] = None
    result = _classify_complete_series(
        _classification_manifest(len(pairs)),
        pairs,
        [{"ordinal": 90, "reason": "acquisition_read_failure"}],
    )

    assert result["conclusion"] == EpochConclusion.UNESTABLISHED.value
    assert any(
        "acquisition_gap_at_boundary" in record["reasons"] for record in result["boundary_records"]
    )


def test_conflicting_valid_boundary_forces_unknown_not_majority_pass() -> None:
    pairs = _complete_pairs()
    # Make both samples around the second boundary follow the master.  Two independent boundaries
    # plus one conflicting boundary may never be turned into a PASS by majority/cherry-picking.
    pairs[179] = _pair(179, pairs[179].slave.device_time, slave_flow=35)
    pairs[180] = _pair(180, pairs[180].slave.device_time, slave_flow=35)
    result = _classify_complete_series(_classification_manifest(len(pairs)), pairs, [])

    assert result["conclusion"] == EpochConclusion.UNESTABLISHED.value
    assert result["conclusion_reason"] == "conflicting_valid_boundaries"


@pytest.mark.parametrize(
    ("fixed_slave_flow", "expected"),
    [
        (32, EpochConclusion.SLOT_NOT_APPLIED),
        (40, EpochConclusion.SLOT_NOT_APPLIED),
        (35, EpochConclusion.MASTER_FOLLOWING),
    ],
)
def test_classifier_preserves_fixed_nontransition_outcomes_through_stability(
    fixed_slave_flow: int,
    expected: EpochConclusion,
) -> None:
    pairs = [
        _pair(ordinal, pair.slave.device_time, slave_flow=fixed_slave_flow)
        for ordinal, pair in enumerate(_complete_pairs())
    ]

    result = _classify_complete_series(_classification_manifest(len(pairs)), pairs, [])

    assert result["conclusion"] == expected.value
    assert result["post_third_stability_satisfied"] is True


def _receipt(kind: str, conclusion: str) -> dict:
    return {
        "epoch_kind": kind,
        "conclusion": conclusion,
        "manifest_sha256": SHA_A,
        "collector_series_sha256": SHA_B,
        "expected_identity_bindings_sha256": [SHA_A, SHA_B],
        "exact_restore": {"baseline_sha256": SHA_C},
        "schedule_expectation": {"master": SHA_C, "slave": SHA_D},
    }


class _ReceiptVerifier:
    def verify_receipt(self, _manifest, receipt) -> dict:
        return receipt.payload


def _receipt_reference(payload: dict, digest: str):
    return SimpleNamespace(payload=payload, receipt_sha256=digest)


@pytest.mark.parametrize(
    ("async_conclusion", "judgment"),
    [
        (EpochConclusion.INDEPENDENTLY_APPLIED.value, Q2FinalJudgment.YES),
        (EpochConclusion.MASTER_FOLLOWING.value, Q2FinalJudgment.NO),
        (EpochConclusion.SLOT_NOT_APPLIED.value, Q2FinalJudgment.NO),
        (EpochConclusion.UNESTABLISHED.value, Q2FinalJudgment.UNKNOWN),
    ],
)
def test_final_mapping_requires_control_and_async_receipts(
    async_conclusion: str, judgment: Q2FinalJudgment
) -> None:
    result = combine_q2_epoch_receipts(
        _ReceiptVerifier(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        _receipt_reference(
            _receipt(
                Q2EpochKind.INDEPENDENT_CONTROL.value,
                EpochConclusion.INDEPENDENTLY_APPLIED.value,
            ),
            SHA_C,
        ),
        object(),  # type: ignore[arg-type]
        _receipt_reference(_receipt(Q2EpochKind.ASYNC.value, async_conclusion), SHA_D),
    )
    assert result.judgment is judgment


def test_final_mapping_rejects_context_mix_as_unknown() -> None:
    control = _receipt(
        Q2EpochKind.INDEPENDENT_CONTROL.value,
        EpochConclusion.INDEPENDENTLY_APPLIED.value,
    )
    async_epoch = _receipt(Q2EpochKind.ASYNC.value, EpochConclusion.INDEPENDENTLY_APPLIED.value)
    async_epoch["exact_restore"] = {"baseline_sha256": SHA_D}

    result = combine_q2_epoch_receipts(
        _ReceiptVerifier(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        _receipt_reference(control, SHA_C),
        object(),  # type: ignore[arg-type]
        _receipt_reference(async_epoch, SHA_D),
    )
    assert result.judgment is Q2FinalJudgment.UNKNOWN
    assert result.reason == "epoch_evidence_context_mismatch"


def test_receipt_hash_is_content_bound() -> None:
    receipt = _receipt(Q2EpochKind.ASYNC.value, EpochConclusion.MASTER_FOLLOWING.value)
    original = hashlib.sha256(
        (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    tampered = dict(receipt, conclusion=EpochConclusion.INDEPENDENTLY_APPLIED.value)
    changed = hashlib.sha256(
        (json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    assert original != changed


def test_manifest_reference_cannot_be_rebound_to_other_directory(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    root = _private_root(tmp_path)
    store = Q2EpochStore(root)
    reference = Q2EpochManifestReference(
        manifest_id="Q2M-missing",
        manifest_sha256=SHA_A,
        epoch_kind=Q2EpochKind.ASYNC,
        collector_plan=plan,
        directory=root,
    )
    with pytest.raises(Q2EpochError, match="q2_manifest_directory_invalid"):
        store.verify_manifest(reference)
