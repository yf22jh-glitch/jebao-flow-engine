from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.config import AppConfig
from jebao_flow.exact_restore import (
    ExactRestoreCycle,
    ExactRestoreRole,
    prepare_exact_restore_record,
)
from jebao_flow.exact_restore_composition import (
    ExactRestoreComposition,
    ExactRestoreCompositionError,
    ExactRestoreOperationManifest,
    _PacedRestoreWriter,
    _SharedRolePacer,
    build_verified_baseline,
    extract_manifest_pairs,
    load_bound_record,
    load_locked_config,
    load_operation_manifest,
    require_cross_workflow_quiescent,
    safe_targets_for_manifest,
    select_operation_targets,
)
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION
from jebao_flow.read_only_collector import (
    VerifiedPilotInterval,
    VerifiedPilotPairArtifact,
    VerifiedPilotRawSample,
)

NOW = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)
PRODUCT_KEY = LOCAL_WAVEMAKER_PRO_PRODUCT_KEY


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "mqtt.test", "topic_prefix": "jebao-flow/test"},
            "runtime": {"dry_run": True},
            "observer": {"targets": ["broadcast.test"]},
            "devices": [
                {
                    "id": "pump_a",
                    "name": "A",
                    "type": "wavemaker",
                    "product_key": PRODUCT_KEY,
                    "identity": {
                        "device_id": "private-a",
                        "mac_address": "001122334455",
                    },
                    "limits": {"min_power": 30, "max_power": 80},
                },
                {
                    "id": "pump_b",
                    "name": "B",
                    "type": "wavemaker",
                    "product_key": PRODUCT_KEY,
                    "identity": {
                        "device_id": "private-b",
                        "mac_address": "66778899aabb",
                    },
                    "limits": {"min_power": 30, "max_power": 80},
                },
            ],
        }
    )


def _manifest_dict() -> dict[str, object]:
    return {
        "version": 1,
        "operation_nonce": "1" * 32,
        "evidence": {
            "plan_artifact_id": "JFP-test",
            "plan_sha256": "a" * 64,
            "series_artifact_id": "JFS-test",
            "series_sha256": "b" * 64,
            "accepted_pair_ordinal": 2,
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
            "max_observation_age_seconds": 60,
            "max_final_pair_gap_seconds": 20,
        },
        "network": {
            "discovery_port": 12414,
            "control_port": 12416,
            "timeout_seconds": 5,
            "max_identity_age_seconds": 5,
        },
    }


def _manifest() -> ExactRestoreOperationManifest:
    return ExactRestoreOperationManifest.model_validate(_manifest_dict())


def _corroborated_manifest_dict() -> dict[str, object]:
    payload = _manifest_dict()
    payload["corroborating_evidence"] = {
        "plan_artifact_id": "JFP-corroborating",
        "plan_sha256": "f" * 64,
        "series_artifact_id": "JFS-corroborating",
        "series_sha256": "9" * 64,
        "accepted_pair_ordinal": 3,
    }
    devices = payload["devices"]
    assert isinstance(devices, list)
    devices[0]["accepted_latent_manual_power"] = 35
    devices[1]["accepted_latent_manual_power"] = 40
    return payload


def _corroborated_manifest() -> ExactRestoreOperationManifest:
    return ExactRestoreOperationManifest.model_validate(_corroborated_manifest_dict())


def _wire(*, flow: int, schedule_flow: int) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE)
    raw[0] = 0b00000011  # ON, TimerON, independent, no fault bits.
    raw[1] = 2
    raw[2] = flow
    raw[3] = 30
    image = bytearray(LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48)
    image[:9] = bytes((0, 0, 24, 0, 2, schedule_flow, 30, 0, 0))
    assert len(image) == LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    raw[
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET : LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
        + LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    ] = image
    raw[443:451] = bytes((20, 26, 8, 30, 0, 5, 0, 0))
    return encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes((STATE_REPLY_ACTION,)) + bytes(raw),
    )


def _interval(start: datetime, milliseconds: int) -> VerifiedPilotInterval:
    completed = start + timedelta(milliseconds=milliseconds)
    return VerifiedPilotInterval(
        started_utc=start.isoformat().replace("+00:00", "Z"),
        completed_utc=completed.isoformat().replace("+00:00", "Z"),
        started_monotonic_ns=1_000_000_000,
        completed_monotonic_ns=1_000_000_000 + milliseconds * 1_000_000,
    )


def _artifact(config: AppConfig) -> VerifiedPilotPairArtifact:
    targets = select_operation_targets(config, _manifest())
    wires = (_wire(flow=35, schedule_flow=60), _wire(flow=40, schedule_flow=70))
    samples = []
    for role, label, wire in zip(
        (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE),
        ("a", "b"),
        wires,
        strict=True,
    ):
        interval = _interval(NOW - timedelta(seconds=2), 100)
        samples.append(
            VerifiedPilotRawSample(
                role=label,
                identity_binding_sha256=targets[role].identity_binding_sha256,
                sample_manifest_sha256=("c" if label == "a" else "d") * 64,
                raw_wire_frame_sha256=hashlib.sha256(wire).hexdigest(),
                attempt=interval,
                identity_before=interval,
                read=interval,
                identity_after=interval,
                raw_wire_frame=wire,
            )
        )
    return VerifiedPilotPairArtifact(
        plan_artifact_id="JFP-test",
        plan_sha256="a" * 64,
        series_id="JFS-test",
        series_sha256="b" * 64,
        ordinal=2,
        pair_manifest_sha256="e" * 64,
        attempt=_interval(NOW - timedelta(seconds=2), 500),
        pair_completion_gap_ns=11_000_000_000,
        samples=(samples[0], samples[1]),
    )


def _corroborating_artifact(config: AppConfig) -> VerifiedPilotPairArtifact:
    return replace(
        _artifact(config),
        plan_artifact_id="JFP-corroborating",
        plan_sha256="f" * 64,
        series_id="JFS-corroborating",
        series_sha256="9" * 64,
        ordinal=3,
        pair_manifest_sha256="8" * 64,
    )


def test_manifest_digest_binds_every_field_and_yields_opaque_operation_id() -> None:
    first = _manifest()
    changed = _manifest_dict()
    changed["operation_nonce"] = "2" * 32
    second = ExactRestoreOperationManifest.model_validate(changed)

    assert first.sentinel_operation_id.startswith("er-sentinel-")
    assert first.baseline_operation_id.startswith("er-baseline-")
    assert first.sentinel_operation_id != first.baseline_operation_id
    assert first.sentinel_operation_id != second.sentinel_operation_id


def test_legacy_manifest_serialization_preserves_digest_operation_ids_and_round_trip() -> None:
    manifest = _manifest()
    payload = manifest.model_dump(mode="json")

    assert "corroborating_evidence" not in payload
    assert all("accepted_latent_manual_power" not in device for device in payload["devices"])
    assert manifest.manifest_sha256 == (
        "0030516a478d64935fe3bd0e5d52155b6928ebeaaddab21703a6e1cc71f07361"
    )
    assert manifest.sentinel_operation_id == (
        "er-sentinel-61b4213d78db247e6cf38920825c8e658249b7a73d262fef07e86008a438c648"
    )
    assert manifest.baseline_operation_id == (
        "er-baseline-3a4dd43501e462139490a75cdb679041bc6e6012ac021024d7bee1a7106a0893"
    )
    assert manifest.final_restore_operation_id == (
        "er-final-b23421f8558d76b3b75876129c81ae0c868ab4e8b81eda3faebdb43c857bd532"
    )
    assert ExactRestoreOperationManifest.model_validate(payload) == manifest


def test_exact_latent_manual_power_requires_distinct_manifest_corroboration() -> None:
    missing = _manifest_dict()
    devices = missing["devices"]
    assert isinstance(devices, list)
    devices[1]["accepted_latent_manual_power"] = 40
    with pytest.raises(ValueError, match="requires corroborating evidence"):
        ExactRestoreOperationManifest.model_validate(missing)

    duplicated = _corroborated_manifest_dict()
    duplicated["corroborating_evidence"] = duplicated["evidence"]
    with pytest.raises(ValueError, match="independent capture"):
        ExactRestoreOperationManifest.model_validate(duplicated)

    manifest = _corroborated_manifest()
    assert manifest.devices[0].accepted_latent_manual_power == 35
    assert manifest.devices[1].accepted_latent_manual_power == 40
    assert "accepted_latent_manual_power" in manifest.model_dump(mode="json")["devices"][0]
    assert "corroborating_evidence" in manifest.model_dump(mode="json")


def test_attended_policy_cannot_expand_configured_device_limits() -> None:
    changed = _manifest_dict()
    changed["devices"][0]["power_policy"]["max_power"] = 90  # type: ignore[index]
    changed["devices"][0]["power_policy"]["attended_max_power"] = 90  # type: ignore[index]
    manifest = ExactRestoreOperationManifest.model_validate(changed)

    with pytest.raises(
        ExactRestoreCompositionError,
        match="attended_policy_exceeds_device_limits",
    ):
        select_operation_targets(_config(), manifest)


def test_owner_only_manifest_and_config_loaders_reject_broad_permissions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(_config().model_dump(mode="json")))
    manifest_path = tmp_path / "operation.json"
    manifest_path.write_text(json.dumps(_manifest_dict()))
    config_path.chmod(0o600)
    manifest_path.chmod(0o600)

    assert load_locked_config(config_path) == _config()
    assert load_operation_manifest(manifest_path) == _manifest()

    manifest_path.chmod(0o640)
    with pytest.raises(ExactRestoreCompositionError, match="operation_manifest_invalid"):
        load_operation_manifest(manifest_path)


def test_verified_pair_decodes_raw_into_admitted_exact_baseline() -> None:
    config = _config()
    manifest = _manifest()
    targets = select_operation_targets(config, manifest)

    baseline = build_verified_baseline(
        manifest,
        targets,
        _artifact(config),
        now=NOW,
    )

    assert baseline.devices[0].outer.power == 35
    assert baseline.devices[1].outer.power == 40
    assert baseline.devices[0].outer.linkage.value == "independent"
    assert baseline.devices[1].schedule.image_bytes[:9] == bytes((0, 0, 24, 0, 2, 70, 30, 0, 0))
    assert baseline.evidence.pair_manifest_sha256 == "e" * 64


def test_two_independent_pairs_build_one_exactly_corroborated_baseline() -> None:
    config = _config()
    manifest = _corroborated_manifest()
    targets = select_operation_targets(config, manifest)
    primary = _artifact(config)
    corroborating = _corroborating_artifact(config)

    baseline = build_verified_baseline(
        manifest,
        targets,
        primary,
        corroborating,
        now=NOW,
    )

    assert baseline.evidence.pair_manifest_sha256 == primary.pair_manifest_sha256
    assert baseline.corroborating_evidence is not None
    assert (
        baseline.corroborating_evidence.pair_manifest_sha256
        == corroborating.pair_manifest_sha256
    )
    assert [device.accepted_latent_manual_power for device in baseline.devices] == [35, 40]


def test_bound_record_rechecks_both_manifest_references_and_exact_latent_values() -> None:
    config = _config()
    manifest = _corroborated_manifest()
    targets = select_operation_targets(config, manifest)
    baseline = build_verified_baseline(
        manifest,
        targets,
        _artifact(config),
        _corroborating_artifact(config),
        now=NOW,
    )

    def composition_for(record):
        class Store:
            def load(self):
                return record.model_dump(mode="json")

        return ExactRestoreComposition(
            manifest=manifest,
            config=config,
            targets=targets,
            controller=object(),
            store=Store(),
            guard=object(),
            observer=object(),
        )

    record = prepare_exact_restore_record(
        baseline,
        safe_targets_for_manifest(manifest),
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=manifest.sentinel_operation_id,
        now=NOW,
    )
    assert load_bound_record(composition_for(record)) == record

    wrong_evidence = baseline.evidence.model_copy(
        update={"series_artifact_id": "JFS-wrong-primary"}
    )
    wrong_baseline = baseline.model_copy(update={"evidence": wrong_evidence})
    wrong_record = prepare_exact_restore_record(
        wrong_baseline,
        safe_targets_for_manifest(manifest),
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=manifest.sentinel_operation_id,
        now=NOW,
    )
    with pytest.raises(ExactRestoreCompositionError, match="operation_manifest_mismatch"):
        load_bound_record(composition_for(wrong_record))

    wrong_slave = baseline.devices[1].model_copy(
        update={
            "outer": baseline.devices[1].outer.model_copy(update={"power": 39}),
            "accepted_latent_manual_power": 39,
        }
    )
    wrong_baseline = baseline.model_copy(
        update={"devices": (baseline.devices[0], wrong_slave)}
    )
    wrong_record = prepare_exact_restore_record(
        wrong_baseline,
        safe_targets_for_manifest(manifest),
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=manifest.sentinel_operation_id,
        now=NOW,
    )
    with pytest.raises(ExactRestoreCompositionError, match="operation_manifest_mismatch"):
        load_bound_record(composition_for(wrong_record))


def test_manifest_pairs_are_loaded_and_verified_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jebao_flow.exact_restore_composition as module

    manifest = _corroborated_manifest()
    primary = _artifact(_config())
    corroborating = _corroborating_artifact(_config())
    artifacts = {
        primary.series_id: primary,
        corroborating.series_id: corroborating,
    }
    references = {
        primary.series_id: manifest.evidence,
        corroborating.series_id: manifest.corroborating_evidence,
    }
    calls: list[tuple[str, str, int]] = []

    class Store:
        def __init__(self, _root: Path) -> None: ...

        def load(self, series_id: str):
            evidence = references[series_id]
            assert evidence is not None
            return type(
                "Reference",
                (),
                {
                    "plan_artifact_id": evidence.plan_artifact_id,
                    "plan_sha256": evidence.plan_sha256,
                },
            )()

        def extract_verified_accepted_pair(
            self,
            reference,
            *,
            expected_series_sha256: str,
            ordinal: int,
        ) -> VerifiedPilotPairArtifact:
            series_id = next(
                value
                for value, evidence in references.items()
                if evidence is not None
                and evidence.plan_artifact_id == reference.plan_artifact_id
            )
            calls.append((series_id, expected_series_sha256, ordinal))
            return artifacts[series_id]

    monkeypatch.setattr(module, "PilotSeriesStore", Store)

    assert extract_manifest_pairs(manifest, tmp_path) == (primary, corroborating)
    assert calls == [
        (primary.series_id, manifest.evidence.series_sha256, 2),
        (
            corroborating.series_id,
            manifest.corroborating_evidence.series_sha256,
            3,
        ),
    ]


def test_corroborating_pair_must_match_outer_controls_and_schedule_bytes_exactly() -> None:
    config = _config()
    manifest = _corroborated_manifest()
    targets = select_operation_targets(config, manifest)
    corroborating = _corroborating_artifact(config)
    changed_wire = _wire(flow=40, schedule_flow=71)
    changed_slave = replace(
        corroborating.samples[1],
        raw_wire_frame=changed_wire,
        raw_wire_frame_sha256=hashlib.sha256(changed_wire).hexdigest(),
    )
    changed_pair = replace(
        corroborating,
        samples=(corroborating.samples[0], changed_slave),
    )

    with pytest.raises(ExactRestoreCompositionError, match="baseline_corroboration_mismatch"):
        build_verified_baseline(
            manifest,
            targets,
            _artifact(config),
            changed_pair,
            now=NOW,
        )

    with pytest.raises(
        ExactRestoreCompositionError,
        match="baseline_corroboration_not_independent",
    ):
        build_verified_baseline(
            manifest,
            targets,
            _artifact(config),
            replace(
                corroborating,
                pair_manifest_sha256=_artifact(config).pair_manifest_sha256,
            ),
            now=NOW,
        )


def test_corroborating_pair_independently_enforces_pair_gap_and_observation_age() -> None:
    config = _config()
    manifest = _corroborated_manifest()
    targets = select_operation_targets(config, manifest)
    primary = _artifact(config)
    corroborating = _corroborating_artifact(config)

    with pytest.raises(ExactRestoreCompositionError, match="baseline_pair_gap_exceeded"):
        build_verified_baseline(
            manifest,
            targets,
            primary,
            replace(corroborating, pair_completion_gap_ns=21_000_000_000),
            now=NOW,
        )

    stale_read = _interval(NOW - timedelta(minutes=2), 100)
    stale_samples = tuple(
        replace(sample, read=stale_read) for sample in corroborating.samples
    )
    with pytest.raises(ExactRestoreCompositionError, match="baseline_observation_expired"):
        build_verified_baseline(
            manifest,
            targets,
            primary,
            replace(corroborating, samples=stale_samples),
            now=NOW,
        )


def test_verified_pair_fails_closed_on_expiry_pair_gap_and_binding() -> None:
    config = _config()
    manifest = _manifest()
    targets = select_operation_targets(config, manifest)
    artifact = _artifact(config)

    with pytest.raises(ExactRestoreCompositionError, match="baseline_observation_expired"):
        build_verified_baseline(manifest, targets, artifact, now=NOW + timedelta(minutes=2))

    too_wide = replace(artifact, pair_completion_gap_ns=21_000_000_000)
    with pytest.raises(ExactRestoreCompositionError, match="baseline_pair_gap_exceeded"):
        build_verified_baseline(manifest, targets, too_wide, now=NOW)

    wrong_sample = replace(artifact.samples[0], identity_binding_sha256="f" * 64)
    wrong_pair = replace(artifact, samples=(wrong_sample, artifact.samples[1]))
    with pytest.raises(ExactRestoreCompositionError, match="baseline_binding_mismatch"):
        build_verified_baseline(manifest, targets, wrong_pair, now=NOW)


def test_cross_workflow_admission_allows_terminal_intent_and_rejects_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jebao_flow.exact_restore_composition as module

    paths = {
        name: tmp_path / name
        for name in (
            "native-journal",
            "verification-journal",
            "schedule-journal",
            "temporary-journal",
            "native-intent",
            "verification-intent",
            "schedule-intent",
            "latch",
        )
    }
    for key, function in {
        "native-journal": "native_linkage_journal_path",
        "verification-journal": "verification_journal_path",
        "schedule-journal": "schedule_linkage_journal_path",
        "temporary-journal": "temporary_schedule_journal_path",
        "native-intent": "native_linkage_intent_path",
        "verification-intent": "verification_intent_path",
        "schedule-intent": "schedule_linkage_intent_path",
        "latch": "emergency_stop_latch_path",
    }.items():
        monkeypatch.setattr(module, function, lambda path=paths[key]: path)
    paths["native-intent"].write_text('{"phase":"terminal"}')
    paths["native-intent"].chmod(0o600)

    require_cross_workflow_quiescent()
    paths["native-journal"].write_text("{}")
    paths["native-journal"].chmod(0o600)
    with pytest.raises(ExactRestoreCompositionError, match="legacy_workflow_nonterminal"):
        require_cross_workflow_quiescent()


class _Writer:
    address = "private.test"
    physical_binding = None

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def connected_session_token(self) -> object:
        return object()

    async def write_target_connected(self, *args, **kwargs) -> None:
        self.calls.append(asyncio.get_running_loop().time())

    async def restore_schedule_image_connected(self, *args, **kwargs) -> object:
        self.calls.append(asyncio.get_running_loop().time())
        return object()


@pytest.mark.asyncio
async def test_shared_pacer_spans_fresh_writer_instances() -> None:
    first = _Writer()
    second = _Writer()
    pacer = _SharedRolePacer(0.1)
    one = _PacedRestoreWriter(first, pacer)
    two = _PacedRestoreWriter(second, pacer)

    await one.connect()
    await one.write_target_connected(object(), connected_session_token=object(), guard=lambda: True)
    await two.connect()
    await two.restore_schedule_image_connected(
        b"image", connected_session_token=object(), guard=lambda: True
    )

    assert second.calls[0] - first.calls[0] >= 0.09
