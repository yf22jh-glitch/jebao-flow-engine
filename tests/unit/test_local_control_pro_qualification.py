"""First-write bookkeeping tests: raw field copy, not a firmware/Sync simulation."""

import asyncio
import hashlib
import json

import pytest

from jebao_flow import local_control_pro_qualification_cli as cli
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalError,
    TransactionPhase,
    native_detach_request_id,
)
from jebao_flow.local_control.models import LocalControlIntent, QualificationBinding
from jebao_flow.local_control.pro_qualification import ProQualificationProducer, _app_trial_values
from jebao_flow.local_control.qualification import verify_qualification_receipts
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationEpoch,
    QualificationProductionError,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO as SCHEMA
from tests.unit.test_local_control_wavemaker_lifecycle import FUNCTIONS, MemoryTransport, Rig, _plan

OPERATION = "pro_qualification_0001"


def _setup(tmp_path):
    rig = Rig(tmp_path, qualified=False)
    # Recorded inactive FeedTime=0 is permitted only as owned raw baseline, not user input.
    for name, status in rig.states.items():
        raw = bytearray(status)
        raw[SCHEMA.by_name("FeedTime").position.byte_offset] = 0
        rig.states[name] = bytes(raw)
    rig.originals = dict(rig.states)
    epochs = [
        QualificationEpoch(
            target=target,
            config_sha256="2" * 64,
            manifest_sha256="3" * 64,
            manifest_generation="4" * 32,
            min_power=30,
            max_power=100,
            app_version="1.0",
            app_catalog_sha256="0" * 64,
            template_sha256="1" * 64,
            schema_sha256=current_protocol_schema_sha256(),
        )
        for target in rig.targets
    ]
    producer = ProQualificationProducer(
        catalog=rig.catalog,
        resolver=rig.resolver,
        journal=rig.store,
        lease=rig.lease,
        epoch_providers=(lambda: epochs[0], lambda: epochs[1]),
        artifact_factory=lambda target, function: QualificationArtifactStore.for_test(
            tmp_path / "receipts",
            tmp_path / "candidates" / f"{target.logical_member}-{function}.json",
        ),
        session_factory=lambda address: MemoryTransport(address, rig),
    )
    return rig, producer, epochs


@pytest.mark.asyncio
async def test_actual_scalar_evidence_full_owned_restore_and_zero_write_replay(tmp_path):
    rig, producer, _ = _setup(tmp_path)
    result = await producer.execute(OPERATION)
    assert result["qualification_pairs"] == 18
    assert result["receipt_count"] == 36
    assert result["restored"] and result["current_state_verified"]
    assert rig.states == rig.originals
    source = rig.store.load(OPERATION)
    restored = rig.store.load(native_detach_request_id(OPERATION))
    assert source.phase is restored.phase is TransactionPhase.VERIFIED
    assert len(source.actions) == 38 and len(restored.actions) == 6
    assert all(a.phase is ActionPhase.VERIFIED for a in source.actions[2:])
    assert len(rig.sent) <= 44  # Logical plan is not a physical-send count.
    assert all(not accepts_reports for _, accepts_reports in rig.reads)
    candidates = list((tmp_path / "candidates").glob("*.json"))
    assert len(candidates) == 18
    assert len(list((tmp_path / "receipts").glob("*.json"))) == 36
    for candidate in candidates:
        binding = QualificationBinding.model_validate(
            json.loads(candidate.read_bytes())["qualifications"][0]
        )
        receipts = verify_qualification_receipts(
            binding, _receipt_root_for_test=tmp_path / "receipts"
        )
        assert len(receipts) == 2
        assert receipts[0].request_id != receipts[1].request_id
        assert receipts[0].control_payload_sha256 != receipts[1].control_payload_sha256
        assert binding.function in FUNCTIONS
        for receipt in receipts:
            assert receipt.intent_sha256 == source.intent_sha256
            assert len(rig.store.load_blob(receipt.readback_sha256)) == SCHEMA.raw_status_size
            assert rig.store.load_blob(receipt.acknowledgement_sha256) == b"synthetic-ack"
            assert receipt.schema_sha256 == current_protocol_schema_sha256()
    sent, reads = len(rig.sent), len(rig.reads)
    replay = await producer.execute(OPERATION)
    assert replay["candidates"] == result["candidates"]
    assert replay["current_state_verified"] is False
    assert (len(rig.sent), len(rig.reads)) == (sent, reads)


@pytest.mark.asyncio
async def test_normal_attach_remains_unqualified(tmp_path):
    rig, producer, _ = _setup(tmp_path)
    with pytest.raises(ValueError):
        await producer.attach(request_id="ordinary_attach", plan=_plan(), targets=rig.targets)
    assert not rig.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", [1, 3, 8, 19, 20, 38])
async def test_ack_loss_stops_forward_and_restores_without_qualification(tmp_path, fail_at):
    rig, producer, _ = _setup(tmp_path)
    rig.fail_ack = fail_at
    with pytest.raises(TimeoutError):
        await producer.execute(OPERATION)
    assert rig.states == rig.originals
    assert rig.store.load(OPERATION).phase is TransactionPhase.ROLLED_BACK
    assert rig.store.load(native_detach_request_id(OPERATION)).phase is TransactionPhase.VERIFIED
    assert len([item for item in rig.sent if item[2] == OPERATION]) == fail_at
    assert not list((tmp_path / "candidates").glob("*.json"))
    assert not list((tmp_path / "receipts").glob("*.json"))
    sent = len(rig.sent)
    with pytest.raises(QualificationProductionError):
        await producer.execute(OPERATION)
    assert len(rig.sent) == sent


@pytest.mark.asyncio
async def test_cancellation_restores_then_propagates(tmp_path):
    rig, producer, _ = _setup(tmp_path)
    rig.cancel_ack = 3
    with pytest.raises(asyncio.CancelledError):
        await producer.execute(OPERATION)
    assert rig.states == rig.originals
    assert not list((tmp_path / "receipts").glob("*.json"))


@pytest.mark.asyncio
async def test_forward_epoch_change_does_not_revoke_owned_restore(tmp_path):
    rig, producer, epochs = _setup(tmp_path)

    def change_epoch(number, address, changes):
        if number == 3:
            epochs[0] = epochs[0].model_copy(update={"config_sha256": "9" * 64})

    rig.before_send = change_epoch
    with pytest.raises(QualificationProductionError, match="epoch changed"):
        await producer.execute(OPERATION)
    assert rig.states == rig.originals
    assert len([item for item in rig.sent if item[2] == OPERATION]) == 3
    assert not list((tmp_path / "receipts").glob("*.json"))


@pytest.mark.asyncio
async def test_epoch_change_during_restore_prevents_receipt_issuance(tmp_path):
    rig, producer, epochs = _setup(tmp_path)

    def change_during_restore(number, address, changes):
        if number == 39:
            epochs[0] = epochs[0].model_copy(update={"config_sha256": "9" * 64})

    rig.before_send = change_during_restore
    with pytest.raises(QualificationProductionError, match="epoch changed"):
        await producer.execute(OPERATION)
    assert rig.states == rig.originals
    assert rig.store.load(native_detach_request_id(OPERATION)).phase is TransactionPhase.VERIFIED
    assert not list((tmp_path / "receipts").glob("*.json"))


@pytest.mark.asyncio
async def test_reply_only_final_failure_grants_nothing_and_is_read_only_recoverable(tmp_path):
    rig, producer, _ = _setup(tmp_path)
    rig.reject_final_reply = True
    with pytest.raises(TimeoutError):
        await producer.execute(OPERATION)
    assert rig.states == rig.originals
    assert not list((tmp_path / "receipts").glob("*.json"))
    sent = len(rig.sent)
    rig.reject_final_reply = False
    await rig.controller().recover_native(request_id=OPERATION, targets=rig.targets)
    assert len(rig.sent) == sent
    result = await producer.execute(OPERATION)
    assert result["receipt_count"] == 36
    assert not result["current_state_verified"]
    assert len(rig.sent) == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("schema_sha256", "f" * 64), ("max_power", 39)])
async def test_invalid_epoch_rejected_before_any_hardware(tmp_path, field, value):
    rig, producer, epochs = _setup(tmp_path)
    epochs[0] = epochs[0].model_copy(update={field: value})
    with pytest.raises(QualificationProductionError):
        await producer.execute(OPERATION)
    assert not rig.sent and not rig.reads


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
async def test_fixed_pair_size_is_enforced_before_any_hardware(tmp_path, count):
    rig, producer, epochs = _setup(tmp_path)
    producer._epoch_providers = tuple((lambda: epochs[0]) for _ in range(count))
    with pytest.raises(QualificationProductionError, match="exactly two"):
        await producer.execute(OPERATION)
    assert not rig.sent and not rig.reads


@pytest.mark.asyncio
async def test_output_metadata_failure_precedes_hardware(tmp_path):
    rig, producer, _ = _setup(tmp_path)

    def unavailable(target, function):
        raise PermissionError("synthetic output failure")

    producer._artifact_factory = unavailable
    with pytest.raises(PermissionError):
        await producer.execute(OPERATION)
    assert not rig.sent and not rig.reads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["readback", "report", "kind", "ack", "ack_missing_blob", "ack_corrupt_blob", "final"],
)
async def test_offline_finalizer_rechecks_raw_and_declared_action_before_receipts(
    tmp_path, corruption
):
    rig, producer, epochs = _setup(tmp_path)

    # Finalizer failure injects no firmware behavior: keep completed durable records only.
    def no_publish(target, function):
        store = QualificationArtifactStore.for_test(
            tmp_path / "receipts", tmp_path / "candidates" / "unused"
        )
        store.finalize = lambda *_: "0" * 64
        return store

    producer._artifact_factory = no_publish
    await producer.execute(OPERATION)
    record = rig.store.load(OPERATION)
    actions = list(record.actions)
    action = actions[2]
    if corruption in {"readback", "report"}:
        entry = json.loads(rig.store.load_blob(action.native_evidence_sha256))
        capture = json.loads(rig.store.load_blob(entry["capture_sha256"]))
        if corruption == "report":
            capture["action"] = 4
        else:
            capture["status_sha256"] = rig.store.install_blob(rig.originals["master"])
        entry["capture_sha256"] = rig.store.install_blob(json.dumps(capture).encode())
        action = action.model_copy(
            update={"native_evidence_sha256": rig.store.install_blob(json.dumps(entry).encode())}
        )
    elif corruption == "kind":
        action = action.model_copy(update={"verification_kind": "scalar_fields"})
    elif corruption == "ack":
        # An absent ACK is invalid; an empty, sequence-confirmed body is valid.
        action = action.model_copy(update={"acknowledgement_sha256": None})
    elif corruption == "ack_missing_blob":
        action = action.model_copy(update={"acknowledgement_sha256": "f" * 64})
    elif corruption == "ack_corrupt_blob":
        (rig.store.blobs / action.acknowledgement_sha256).write_bytes(b"tampered-test-ack")
    else:
        restored = rig.store.load(native_detach_request_id(OPERATION))
        final = json.loads(rig.store.load_blob(restored.native_final_sha256))
        final["devices"] = []
        rig.store.save(
            restored,
            restored.evolve().model_copy(
                update={"native_final_sha256": rig.store.install_blob(json.dumps(final).encode())}
            ),
        )
    actions[2] = action
    record = record.model_copy(update={"actions": tuple(actions)})
    error_type = {
        "ack_missing_blob": FileNotFoundError,
        "ack_corrupt_blob": LocalControlJournalError,
    }.get(corruption, QualificationProductionError)
    with pytest.raises(error_type):
        producer._finalize(record, tuple(epochs), current=False)
    assert not list((tmp_path / "receipts").glob("*.json"))


def test_inactive_feed_zero_rollback_selects_only_owned_field_and_new_zero_still_rejected(tmp_path):
    rig, _, _ = _setup(tmp_path)
    writer = ConnectedDeviceWriter(rig.endpoints[0])
    intent = LocalControlIntent(
        request_id="feed_qualification", target=rig.targets[0], function="feeding_duration", value=1
    )
    prepared = writer.prepare_scalar(intent, rig.originals["master"])
    flags = prepared.rollback_payload[1 : 1 + SCHEMA.attribute_flags_size][::-1]
    selected = [
        a.name for a in SCHEMA.attributes if a.writable and flags[a.id // 8] & (1 << (a.id % 8))
    ]
    assert selected == ["FeedTime"]
    assert (
        prepared.rollback_payload[1 + SCHEMA.attribute_flags_size :]
        == rig.originals["master"][: SCHEMA.attribute_values_size]
    )
    assert json.loads(prepared.baseline_readback) == {"FeedTime": 0}
    assert (
        hashlib.sha256(prepared.forward_payload).hexdigest()
        != hashlib.sha256(prepared.rollback_payload).hexdigest()
    )
    with pytest.raises(ValueError):
        writer.prepare_scalar(intent.model_copy(update={"value": 0}), rig.originals["master"])


def test_cli_no_write_opt_in_reports_error_without_loading_private_input(capsys):
    result = cli.main(
        [
            "--operation-id",
            OPERATION,
            "--target",
            "first",
            "--target",
            "second",
            "--active-template-sha256",
            "0" * 64,
            "--qualification-output-root",
            "unused",
        ]
    )
    assert result == 2
    output = json.loads(capsys.readouterr().out)
    assert output["accepted"] is False
    assert output["record"] is None
    assert "recover-wavemaker" in output["next_command"]


@pytest.mark.parametrize("function", ["feeding_duration", "custom_wave_frequency"])
@pytest.mark.parametrize("baseline", [0, 1, 2, 5, 6, 100])
def test_trial_values_use_declared_app_range_not_encoder_range(function, baseline):
    minimum, maximum = declared_app_integer_range(SCHEMA.product_key, function)
    first, second = _app_trial_values(SCHEMA.product_key, function, baseline)
    assert minimum <= first <= maximum and minimum <= second <= maximum
    assert first != second and first != baseline
    assert {first, second} == {minimum, minimum + 1}


@pytest.mark.asyncio
async def test_sequence_confirmed_empty_ack_body_can_qualify_and_replay(tmp_path, monkeypatch):
    # ACK return shape observed in docs/runs/2026-09-15-pro-qualification-custom-frequency-stop.md;
    # this wraps only transport bookkeeping, not a guessed firmware response rule.
    original_send = MemoryTransport.send_raw_control

    async def empty_body(session, payload):
        await original_send(session, payload)
        return b""

    monkeypatch.setattr(MemoryTransport, "send_raw_control", empty_body)
    rig, producer, _ = _setup(tmp_path)
    result = await producer.execute(OPERATION)
    assert result["qualification_pairs"] == 18 and result["receipt_count"] == 36
    assert result["restored"] and rig.states == rig.originals
    record = rig.store.load(OPERATION)
    assert all(
        a.acknowledgement_sha256 == hashlib.sha256(b"").hexdigest() for a in record.actions[2:]
    )
    assert all(rig.store.load_blob(a.acknowledgement_sha256) == b"" for a in record.actions[2:])
    sent, reads = len(rig.sent), len(rig.reads)
    replay = await producer.execute(OPERATION)
    assert replay["candidates"] == result["candidates"]
    assert not replay["current_state_verified"]
    assert (len(rig.sent), len(rig.reads)) == (sent, reads)
