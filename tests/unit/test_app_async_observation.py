from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow.app_async_observation import (
    APP_ASYNC_PHYSICAL_CLAIM_SCOPE,
    AppAsyncConclusion,
    AppAsyncObservationError,
    AppAsyncObservationStore,
    AppAsyncStage,
    AppReportedTuple,
    AppUiActionAttestation,
    _classify_observation_series,
    _normalize_unused_slots,
    build_expected_schedule_image,
    expected_ui_actions,
)
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET,
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION, STATE_REPORT_ACTION
from jebao_flow.q2_epoch import ExactRestoreEvidence
from jebao_flow.read_only_collector import (
    CaptureTransportPolicy,
    PilotPlanReference,
    VerifiedPilotInterval,
    VerifiedPilotPairArtifact,
    VerifiedPilotRawSample,
)

MASTER_BINDING = "1" * 64
SLAVE_BINDING = "2" * 64
COMMIT = "a" * 40
SOURCE_DIGEST = "b" * 64
MASTER_BOUNDARY = datetime(2026, 9, 3, 12, 0)
SLAVE_BOUNDARY = MASTER_BOUNDARY + timedelta(minutes=5)

MASTER_BEFORE = AppReportedTuple(mode="sine", flow=35, frequency=30)
MASTER_AFTER = AppReportedTuple(mode="constant", flow=30, frequency=0)
SLAVE_BEFORE = AppReportedTuple(mode="constant", flow=32, frequency=0)
SLAVE_AFTER = AppReportedTuple(mode="sine", flow=40, frequency=30)
MASTER_MANUAL = AppReportedTuple(mode="constant", flow=30, frequency=32)
SLAVE_MANUAL = AppReportedTuple(mode="random", flow=89, frequency=34)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _acquisition(policy: CaptureTransportPolicy) -> dict[str, object]:
    result: dict[str, object] = {
        "transport_policy": policy.value,
        "accept_reports": policy is CaptureTransportPolicy.REPLY_OR_REPORT,
        "accepted_actions": list(policy.accepted_action_names),
    }
    if policy is CaptureTransportPolicy.EXPLICIT_ONLY:
        result["required_action"] = "0x03"
    return result


class _SeriesProvider:
    def __init__(self, root: Path) -> None:
        self.references: dict[AppAsyncStage, PilotPlanReference] = {}
        self.plans: dict[str, dict[str, object]] = {}
        self.series_digests: dict[AppAsyncStage, str] = {}
        self.artifacts: dict[tuple[AppAsyncStage, int], VerifiedPilotPairArtifact] = {}
        self.stage_by_series: dict[str, AppAsyncStage] = {}
        for stage in AppAsyncStage:
            count = 3 if stage is AppAsyncStage.OBSERVATION else 1
            series_id = f"JFS-app-{stage.value}"
            directory = root / series_id
            directory.mkdir(mode=0o700)
            reference = PilotPlanReference(
                plan_artifact_id=f"JFP-app-{stage.value}",
                series_id=series_id,
                plan_sha256=_digest(f"plan-{stage.value}"),
                epoch=f"app_async_{stage.value}",
                planned_pair_count=count,
                requested_cadence_ns=(
                    210_000_000_000 if stage is AppAsyncStage.OBSERVATION else 10_000_000_000
                ),
                series_directory=directory,
            )
            policy = (
                CaptureTransportPolicy.REPLY_OR_REPORT
                if stage is AppAsyncStage.OBSERVATION
                else CaptureTransportPolicy.EXPLICIT_ONLY
            )
            self.references[stage] = reference
            self.stage_by_series[series_id] = stage
            self.series_digests[stage] = _digest(f"series-{stage.value}")
            self.plans[series_id] = {
                "collector_commit_sha": COMMIT,
                "collector_runtime_source_digest_sha256": _digest("collector-source"),
                "ordered_targets": [
                    {"expected_identity_binding_sha256": MASTER_BINDING},
                    {"expected_identity_binding_sha256": SLAVE_BINDING},
                ],
                "acquisition": _acquisition(policy),
            }

    def load(self, series_id: str) -> PilotPlanReference:
        return self.references[self.stage_by_series[series_id]]

    def verify_plan(self, reference: PilotPlanReference) -> dict[str, object]:
        return self.plans[reference.series_id]

    def verify_completed_series(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
    ) -> dict[str, object]:
        stage = self.stage_by_series[reference.series_id]
        assert expected_series_sha256 == self.series_digests[stage]
        return {"records": [{"outcome": "accepted"} for _ in range(reference.planned_pair_count)]}

    def extract_verified_accepted_pair(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ) -> VerifiedPilotPairArtifact:
        stage = self.stage_by_series[reference.series_id]
        assert expected_series_sha256 == self.series_digests[stage]
        return self.artifacts[(stage, ordinal)]


class _RestoreVerifier:
    def __init__(self) -> None:
        primary = SimpleNamespace(
            plan_artifact_id="JFP-baseline-primary",
            series_artifact_id="JFS-baseline-primary",
            pair_ordinal=0,
            pair_manifest_sha256=_digest("baseline-primary-pair"),
        )
        corroborating = SimpleNamespace(
            plan_artifact_id="JFP-baseline-corroborating",
            series_artifact_id="JFS-baseline-corroborating",
            pair_ordinal=0,
            pair_manifest_sha256=_digest("baseline-corroborating-pair"),
        )
        devices = (
            SimpleNamespace(
                logical_id="pump-a",
                identity_binding_sha256=MASTER_BINDING,
                outer=SimpleNamespace(
                    mode=MASTER_MANUAL.mode,
                    power=MASTER_MANUAL.flow,
                    frequency=MASTER_MANUAL.frequency,
                ),
                schedule=SimpleNamespace(image_sha256=_digest("original-master-image")),
            ),
            SimpleNamespace(
                logical_id="pump-b",
                identity_binding_sha256=SLAVE_BINDING,
                outer=SimpleNamespace(
                    mode=SLAVE_MANUAL.mode,
                    power=SLAVE_MANUAL.flow,
                    frequency=SLAVE_MANUAL.frequency,
                ),
                schedule=SimpleNamespace(image_sha256=_digest("original-slave-image")),
            ),
        )
        baseline = SimpleNamespace(
            evidence=primary,
            corroborating_evidence=corroborating,
            devices=devices,
        )
        self.bundle = SimpleNamespace(
            artifact_id="exact-restore-q2-qualified-test",
            baseline_sha256=_digest("exact-baseline"),
            qualified_record=SimpleNamespace(baseline=baseline),
        )
        self.evidence = ExactRestoreEvidence(
            baseline_artifact_id=self.bundle.artifact_id,
            baseline_sha256=self.bundle.baseline_sha256,
            sentinel_receipt_id="ERQ-sentinel",
            sentinel_receipt_sha256=_digest("sentinel"),
            baseline_receipt_id="ERQ-baseline",
            baseline_receipt_sha256=_digest("baseline-receipt"),
            final_restore_operation_id="er-final-test",
            final_restore_record_sha256=_digest("restore-record"),
            expected_identity_bindings_sha256=(MASTER_BINDING, SLAVE_BINDING),
        )

    def derive_q2_exact_restore_evidence(
        self,
        *,
        not_after: datetime | None = None,
    ) -> ExactRestoreEvidence:
        assert not_after is None or not_after.tzinfo is not None
        return self.evidence

    def verify_q2_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        assert not_after.tzinfo is not None
        assert evidence == self.evidence
        return self.evidence

    def load_verified_qualified_bundle(
        self,
        *,
        not_after: datetime | None = None,
    ) -> object:
        assert not_after is None or not_after.tzinfo is not None
        return self.bundle


def _zero_encode_unused(image: bytes) -> bytes:
    result = bytearray(image)
    for slot in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT):
        start = slot * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
        if image[start : start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE] == (LOCAL_WAVEMAKER_PRO_UNUSED_EE):
            result[start : start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE] = bytes(
                LOCAL_WAVEMAKER_PRO_SLOT_SIZE
            )
    return bytes(result)


def _wire(
    *,
    device_time: datetime,
    linkage: str,
    manual: AppReportedTuple,
    auto: AppReportedTuple,
    schedule_image: bytes,
    action: int,
) -> bytes:
    role_code = {
        "independent": 0,
        "master": 1,
        "sync_slave": 2,
        "async_slave": 3,
    }[linkage]
    raw = bytearray(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE)
    raw[0] = 0b00000011 | role_code << 2
    raw[1] = LOCAL_WAVEMAKER_PRO_MODES.index(manual.mode)
    raw[2] = manual.flow
    raw[3] = manual.frequency
    raw[6] = LOCAL_WAVEMAKER_PRO_MODES.index(auto.mode)
    raw[7] = auto.flow
    raw[8] = auto.frequency
    assert len(schedule_image) == LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    raw[
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET : LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
        + LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    ] = schedule_image
    raw[443:451] = bytes(
        (
            device_time.year // 100,
            device_time.year % 100,
            device_time.month,
            device_time.day,
            0,
            device_time.hour,
            device_time.minute,
            device_time.second,
        )
    )
    return encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes((action,)) + bytes(raw),
    )


def _interval(ordinal: int, *, offset_ms: int = 0) -> VerifiedPilotInterval:
    started = datetime(2026, 9, 3, 2, 0, tzinfo=UTC) + timedelta(seconds=ordinal * 10)
    start_ns = 1_000_000_000 + ordinal * 10_000_000_000 + offset_ms * 1_000_000
    return VerifiedPilotInterval(
        started_utc=started.isoformat().replace("+00:00", "Z"),
        completed_utc=(started + timedelta(milliseconds=100)).isoformat().replace("+00:00", "Z"),
        started_monotonic_ns=start_ns,
        completed_monotonic_ns=start_ns + 100_000_000,
    )


def _artifact(
    stage: AppAsyncStage,
    ordinal: int,
    *,
    master_time: datetime,
    slave_time: datetime,
    master_linkage: str,
    slave_linkage: str,
    master_auto: AppReportedTuple,
    slave_auto: AppReportedTuple,
    master_image: bytes,
    slave_image: bytes,
    slave_manual: AppReportedTuple = SLAVE_BEFORE,
) -> VerifiedPilotPairArtifact:
    action = STATE_REPORT_ACTION if stage is AppAsyncStage.OBSERVATION else STATE_REPLY_ACTION
    wires = (
        _wire(
            device_time=master_time,
            linkage=master_linkage,
            manual=MASTER_MANUAL,
            auto=master_auto,
            schedule_image=master_image,
            action=action,
        ),
        _wire(
            device_time=slave_time,
            linkage=slave_linkage,
            manual=slave_manual,
            auto=slave_auto,
            schedule_image=slave_image,
            action=action,
        ),
    )
    samples = []
    for index, (role, binding, wire) in enumerate(
        zip(("a", "b"), (MASTER_BINDING, SLAVE_BINDING), wires, strict=True)
    ):
        interval = _interval(ordinal, offset_ms=index * 200)
        samples.append(
            VerifiedPilotRawSample(
                role=role,
                identity_binding_sha256=binding,
                sample_manifest_sha256=_digest(f"{stage.value}-{ordinal}-{role}-sample"),
                raw_wire_frame_sha256=hashlib.sha256(wire).hexdigest(),
                attempt=interval,
                identity_before=interval,
                read=interval,
                identity_after=interval,
                raw_wire_frame=wire,
            )
        )
    return VerifiedPilotPairArtifact(
        plan_artifact_id=f"JFP-app-{stage.value}",
        plan_sha256=_digest(f"plan-{stage.value}"),
        series_id=f"JFS-app-{stage.value}",
        series_sha256=_digest(f"series-{stage.value}"),
        ordinal=ordinal,
        pair_manifest_sha256=_digest(f"{stage.value}-{ordinal}-pair"),
        attempt=_interval(ordinal),
        pair_completion_gap_ns=200_000_000,
        samples=(samples[0], samples[1]),
    )


def _prepared(
    tmp_path: Path,
) -> tuple[
    AppAsyncObservationStore,
    _SeriesProvider,
    _RestoreVerifier,
    object,
    bytes,
    bytes,
]:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    provider = _SeriesProvider(root)
    verifier = _RestoreVerifier()
    store = AppAsyncObservationStore(root)
    reference = store.prepare(
        provider,  # type: ignore[arg-type]
        provider.references,
        master_boundary_local=MASTER_BOUNDARY,
        operation_nonce="0" * 32,
        verifier_commit_sha=COMMIT,
        verifier_runtime_source_digest_sha256=SOURCE_DIGEST,
        restore_verifier=verifier,
        utc_clock=lambda: datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
    )
    master_image = _zero_encode_unused(build_expected_schedule_image("master", MASTER_BOUNDARY))
    slave_image = _zero_encode_unused(build_expected_schedule_image("slave", SLAVE_BOUNDARY))
    return store, provider, verifier, reference, master_image, slave_image


def _populate_checkpoints(
    provider: _SeriesProvider,
    master_image: bytes,
    slave_image: bytes,
) -> None:
    roles = {
        AppAsyncStage.SCHEDULES_ARMED: ("independent", "independent"),
        AppAsyncStage.SYNC_SELECTED: ("master", "sync_slave"),
        AppAsyncStage.ASYNC_SELECTED: ("master", "async_slave"),
    }
    for stage, (master_role, slave_role) in roles.items():
        provider.artifacts[(stage, 0)] = _artifact(
            stage,
            0,
            master_time=MASTER_BOUNDARY - timedelta(minutes=2),
            slave_time=MASTER_BOUNDARY - timedelta(minutes=2),
            master_linkage=master_role,
            slave_linkage=slave_role,
            master_auto=MASTER_BEFORE,
            slave_auto=SLAVE_BEFORE,
            master_image=master_image,
            slave_image=slave_image,
        )


def _populate_observation(
    provider: _SeriesProvider,
    master_image: bytes,
    slave_image: bytes,
    *,
    pre_slave: AppReportedTuple,
    mixed_slave: AppReportedTuple,
    post_slave: AppReportedTuple,
    clock_regression: bool = False,
) -> None:
    times = (
        (MASTER_BOUNDARY - timedelta(minutes=2), MASTER_BOUNDARY - timedelta(minutes=2)),
        (MASTER_BOUNDARY + timedelta(minutes=2), MASTER_BOUNDARY + timedelta(minutes=2)),
        (MASTER_BOUNDARY + timedelta(minutes=7), MASTER_BOUNDARY + timedelta(minutes=7)),
    )
    if clock_regression:
        times = (times[0], times[2], times[1])
    for ordinal, ((master_time, slave_time), master_auto, slave_auto) in enumerate(
        zip(
            times,
            (MASTER_BEFORE, MASTER_AFTER, MASTER_AFTER),
            (pre_slave, mixed_slave, post_slave),
            strict=True,
        )
    ):
        provider.artifacts[(AppAsyncStage.OBSERVATION, ordinal)] = _artifact(
            AppAsyncStage.OBSERVATION,
            ordinal,
            master_time=master_time,
            slave_time=slave_time,
            master_linkage="master",
            slave_linkage="async_slave",
            master_auto=master_auto,
            slave_auto=slave_auto,
            master_image=master_image,
            slave_image=slave_image,
        )


def test_expected_schedule_uses_two_slots_and_normalizes_zero_unused() -> None:
    image = build_expected_schedule_image("master", MASTER_BOUNDARY)
    assert len(image) == LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    assert image[:9] == bytes((0, 0, 12, 0, 1, 35, 30, 0, 0))
    assert image[9:18] == bytes((12, 0, 24, 0, 2, 30, 0, 0, 0))
    assert _normalize_unused_slots(_zero_encode_unused(image)) == image


def test_expected_ui_actions_include_strict_sync_checkpoint() -> None:
    actions = expected_ui_actions()
    assert [item.ordinal for item in actions] == list(range(10))
    assert actions[8].action_name == "select_slave_parent"
    assert actions[8].checkpoint_after is AppAsyncStage.SYNC_SELECTED
    assert actions[9].action_name == "select_async"


@pytest.mark.parametrize(
    ("pre_slave", "mixed_slave", "post_slave", "expected"),
    (
        (
            MASTER_BEFORE,
            MASTER_AFTER,
            MASTER_AFTER,
            AppAsyncConclusion.MASTER_TIMING_MASTER_CONTENT,
        ),
        (
            SLAVE_BEFORE,
            SLAVE_AFTER,
            SLAVE_AFTER,
            AppAsyncConclusion.MASTER_TIMING_SLAVE_CONTENT,
        ),
        (
            MASTER_BEFORE,
            MASTER_BEFORE,
            MASTER_AFTER,
            AppAsyncConclusion.SLAVE_TIMING_MASTER_CONTENT,
        ),
        (
            SLAVE_BEFORE,
            SLAVE_BEFORE,
            SLAVE_AFTER,
            AppAsyncConclusion.SLAVE_TIMING_SLAVE_SIDE_OR_ROLE_ENTRY_MIRROR,
        ),
    ),
)
def test_raw_observation_maps_all_four_precommitted_outcomes(
    tmp_path: Path,
    pre_slave: AppReportedTuple,
    mixed_slave: AppReportedTuple,
    post_slave: AppReportedTuple,
    expected: AppAsyncConclusion,
) -> None:
    store, provider, _verifier, reference, master_image, slave_image = _prepared(tmp_path)
    _populate_checkpoints(provider, master_image, slave_image)
    _populate_observation(
        provider,
        master_image,
        slave_image,
        pre_slave=pre_slave,
        mixed_slave=mixed_slave,
        post_slave=post_slave,
    )
    manifest = store.verify_manifest(reference, provider)  # type: ignore[arg-type]
    observation = _classify_observation_series(
        manifest,
        provider,
        series_sha256=provider.series_digests[AppAsyncStage.OBSERVATION],
        armed_images=(master_image, slave_image),
    )
    assert observation["conclusion"] == expected.value
    assert observation["stability_duration_claimed"] is False
    assert observation["physical_claim_scope"] == APP_ASYNC_PHYSICAL_CLAIM_SCOPE
    assert observation["action_counts"] == {"0x03": 0, "0x04": 6}


def test_clock_regression_forces_unknown_without_hiding_raw_pairs(tmp_path: Path) -> None:
    store, provider, _verifier, reference, master_image, slave_image = _prepared(tmp_path)
    _populate_checkpoints(provider, master_image, slave_image)
    _populate_observation(
        provider,
        master_image,
        slave_image,
        pre_slave=SLAVE_BEFORE,
        mixed_slave=SLAVE_AFTER,
        post_slave=SLAVE_AFTER,
        clock_regression=True,
    )
    manifest = store.verify_manifest(reference, provider)  # type: ignore[arg-type]
    observation = _classify_observation_series(
        manifest,
        provider,
        series_sha256=provider.series_digests[AppAsyncStage.OBSERVATION],
        armed_images=(master_image, slave_image),
    )
    assert observation["conclusion"] == AppAsyncConclusion.UNKNOWN.value
    assert observation["conclusion_reason"] == "participant_device_clock_not_monotonic"
    assert observation["participant_device_clocks_monotonic"] is False
    assert observation["invariant_violations"][0]["pair"]["master"]["device_time"]


def test_role_checkpoint_rejects_any_schedule_byte_change(tmp_path: Path) -> None:
    store, provider, verifier, reference, master_image, slave_image = _prepared(tmp_path)
    _populate_checkpoints(provider, master_image, slave_image)
    changed = bytearray(slave_image)
    changed[9 + 5] = 41
    provider.artifacts[(AppAsyncStage.SYNC_SELECTED, 0)] = _artifact(
        AppAsyncStage.SYNC_SELECTED,
        0,
        master_time=MASTER_BOUNDARY - timedelta(minutes=2),
        slave_time=MASTER_BOUNDARY - timedelta(minutes=2),
        master_linkage="master",
        slave_linkage="sync_slave",
        master_auto=MASTER_BEFORE,
        slave_auto=SLAVE_BEFORE,
        master_image=master_image,
        slave_image=bytes(changed),
    )
    with pytest.raises(AppAsyncObservationError, match="app_async_checkpoint_state_mismatch"):
        store.verify_checkpoints(
            reference,
            provider,  # type: ignore[arg-type]
            series_sha256_by_stage={
                AppAsyncStage.SCHEDULES_ARMED: provider.series_digests[
                    AppAsyncStage.SCHEDULES_ARMED
                ],
                AppAsyncStage.SYNC_SELECTED: provider.series_digests[AppAsyncStage.SYNC_SELECTED],
            },
            through_stage=AppAsyncStage.SYNC_SELECTED,
            restore_verifier=verifier,
            utc_clock=lambda: datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        )


def test_public_classification_commits_bound_receipt_and_records_mirror_tuple(
    tmp_path: Path,
) -> None:
    store, provider, verifier, reference, master_image, slave_image = _prepared(tmp_path)
    _populate_checkpoints(provider, master_image, slave_image)
    _populate_observation(
        provider,
        master_image,
        slave_image,
        pre_slave=SLAVE_BEFORE,
        mixed_slave=SLAVE_AFTER,
        post_slave=SLAVE_AFTER,
    )
    attestation = AppUiActionAttestation(
        manifest_id=reference.manifest_id,
        manifest_sha256=reference.manifest_sha256,
        observed_utc=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        completed_ordinals=tuple(range(10)),
    )
    receipt = store.classify_and_commit(
        reference,
        provider,  # type: ignore[arg-type]
        series_sha256_by_stage=provider.series_digests,
        action_attestation=attestation,
        restore_verifier=verifier,
        utc_clock=lambda: datetime(2026, 9, 3, 2, 1, tzinfo=UTC),
    )
    assert receipt.conclusion is AppAsyncConclusion.MASTER_TIMING_SLAVE_CONTENT
    value = store.verify_receipt(reference, receipt)
    assert value["physical_claim_scope"] == APP_ASYNC_PHYSICAL_CLAIM_SCOPE
    assert value["restore_status"] == (
        "precommitted_and_staged_not_claimed_complete_by_this_receipt"
    )
    sync = next(item for item in value["checkpoints"] if item["stage"] == "sync_selected")
    assert sync["slave_manual_tuple"] == SLAVE_BEFORE.model_dump(mode="json")
    assert sync["unused_encoding_observation"] == [
        "semantically_exact_unused_zero_or_ee_differs",
        "semantically_exact_unused_zero_or_ee_differs",
    ]
    assert "independently_applied" not in json.dumps(value, sort_keys=True)


def test_checkpoint_exact_role_write_bytes_are_compared_to_armed_bytes(
    tmp_path: Path,
) -> None:
    store, provider, verifier, reference, master_image, slave_image = _prepared(tmp_path)
    _populate_checkpoints(provider, master_image, slave_image)
    result = store.verify_checkpoints(
        reference,
        provider,  # type: ignore[arg-type]
        series_sha256_by_stage={
            stage: provider.series_digests[stage] for stage in _CHECKPOINT_STAGES_FOR_TEST
        },
        through_stage=AppAsyncStage.ASYNC_SELECTED,
        restore_verifier=verifier,
        utc_clock=lambda: datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    )
    assert result["through_stage"] == "async_selected"
    assert [item["stage"] for item in result["checkpoints"]] == [
        "schedules_armed",
        "sync_selected",
        "async_selected",
    ]


_CHECKPOINT_STAGES_FOR_TEST = (
    AppAsyncStage.SCHEDULES_ARMED,
    AppAsyncStage.SYNC_SELECTED,
    AppAsyncStage.ASYNC_SELECTED,
)


def test_manifest_rejects_boundary_crossing_midnight(tmp_path: Path) -> None:
    _store, provider, verifier, _reference, _master_image, _slave_image = _prepared(tmp_path)
    separate_root = tmp_path / "separate"
    separate_root.mkdir(mode=0o700)
    with pytest.raises(AppAsyncObservationError, match="app_async_boundary_invalid"):
        AppAsyncObservationStore(separate_root).prepare(
            provider,  # type: ignore[arg-type]
            provider.references,
            master_boundary_local=datetime(2026, 9, 3, 23, 57),
            operation_nonce="1" * 32,
            verifier_commit_sha=COMMIT,
            verifier_runtime_source_digest_sha256=SOURCE_DIGEST,
            restore_verifier=verifier,
            utc_clock=lambda: datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        )


def test_raw_report_is_rejected_under_explicit_checkpoint_policy(tmp_path: Path) -> None:
    store, provider, verifier, reference, master_image, slave_image = _prepared(tmp_path)
    _populate_checkpoints(provider, master_image, slave_image)
    artifact = provider.artifacts[(AppAsyncStage.SCHEDULES_ARMED, 0)]
    samples = []
    for sample in artifact.samples:
        decoded = bytearray(sample.raw_wire_frame)
        # Rebuild through the helper because a wire checksum also covers the action byte.
        participant = "master" if sample.role == "a" else "slave"
        wire = _wire(
            device_time=MASTER_BOUNDARY - timedelta(minutes=2),
            linkage="independent",
            manual=MASTER_MANUAL if participant == "master" else SLAVE_BEFORE,
            auto=MASTER_BEFORE if participant == "master" else SLAVE_BEFORE,
            schedule_image=master_image if participant == "master" else slave_image,
            action=STATE_REPORT_ACTION,
        )
        samples.append(
            replace(
                sample,
                raw_wire_frame=wire,
                raw_wire_frame_sha256=hashlib.sha256(wire).hexdigest(),
            )
        )
        assert decoded
    provider.artifacts[(AppAsyncStage.SCHEDULES_ARMED, 0)] = replace(
        artifact, samples=(samples[0], samples[1])
    )
    with pytest.raises(AppAsyncObservationError, match="app_async_state_frame_invalid"):
        store.verify_checkpoints(
            reference,
            provider,  # type: ignore[arg-type]
            series_sha256_by_stage={
                AppAsyncStage.SCHEDULES_ARMED: provider.series_digests[
                    AppAsyncStage.SCHEDULES_ARMED
                ]
            },
            through_stage=AppAsyncStage.SCHEDULES_ARMED,
            restore_verifier=verifier,
            utc_clock=lambda: datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        )
