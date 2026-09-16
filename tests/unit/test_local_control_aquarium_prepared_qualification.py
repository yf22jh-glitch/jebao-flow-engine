"""Existing byte-copy transport and fault injection; no simulated firmware claims."""

import json

import pytest

from jebao_flow.local_control.aquarium_prepared_qualification import (
    PreparedAquariumScalarQualificationProducer,
)
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import LocalDeviceWriteError
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.local_control.qualification import verify_qualification_receipts
from jebao_flow.local_control.qualification_producer import QualificationProductionError
from jebao_flow.local_control.resolver import CurrentEndpointResolutionError
from jebao_flow.protocol.profiles import AQUARIUM_PUMP, LOCAL_WAVEMAKER
from tests.unit.test_local_control_aquarium_qualification import _status
from tests.unit.test_local_control_bar_qualification import (
    _binding,
    _epoch,
    _loaded_binding,
    _producer,
    _Session,
    _WriterFactory,
)


def _make(tmp_path, *, function="motor_speed", states=None, sent=None, sessions=None, **kwargs):
    sent = [] if sent is None else sent
    if sessions is None:
        states = states or [_status(speed=x) for x in (60, 40, 39, 40, 60)]
        sessions = [
            _Session([before, after], sent)
            for before, after in zip(states[:-1], states[1:], strict=True)
        ]
    return _producer(
        tmp_path,
        function=function,
        sessions=sessions,
        target=kwargs.pop("target", _binding(product_key=AQUARIUM_PUMP.product_key)),
        producer_type=PreparedAquariumScalarQualificationProducer,
        **kwargs,
    ), sent


@pytest.mark.parametrize("function", ["motor_speed", "feeding_duration"])
async def test_preparation_and_original_restore_share_one_journal_and_only_two_receipts(
    tmp_path, function
):
    states = (
        [_status(speed=x) for x in (60, 40, 39, 40, 60)]
        if function == "motor_speed"
        else [
            _status(speed=60),
            _status(speed=40),
            _status(speed=40, feed_time=1),
            _status(speed=40),
            _status(speed=60),
        ]
    )
    (producer, journal, lease, resolver, factory), sent = _make(
        tmp_path, function=function, states=states
    )
    result = await producer.execute("prepared_ok", preparation_speed=40)
    record = journal.load("prepared_ok")
    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 4 and len(sent) == 4 and result.receipt_count == 2
    assert record.native_context_sha256 is None
    assert len({action.baseline_status_sha256 for action in record.actions}) == 1
    assert journal.load_blob(record.actions[0].baseline_status_sha256) == states[0]
    intent = json.loads(journal.load_blob(record.intent_sha256))
    assert intent["original_motor_speed"] == 60 and intent["trial"]["baseline_motor_speed"] == 40
    assert record.actions[3].forward_payload_sha256 == record.actions[0].rollback_payload_sha256
    for action in record.actions:
        assert action.phase is ActionPhase.VERIFIED and action.acknowledgement_sha256 is not None
        baseline = json.loads(journal.load_blob(action.baseline_readback_sha256))
        assert baseline == (
            {"Motor_Speed": 60} if action.function == "motor_speed" else {"FeedTime": 10}
        )
        assert action.restore_on_recovery is True
    receipts = verify_qualification_receipts(
        _loaded_binding(tmp_path), _receipt_root_for_test=tmp_path / "receipts"
    )
    assert {item.control_payload_sha256 for item in receipts} == {
        record.actions[1].forward_payload_sha256,
        record.actions[2].forward_payload_sha256,
    }
    assert len(lease.acquired) == 1
    replay = await producer.execute("prepared_ok", preparation_speed=40)
    assert replay.physical_write_count == 0 and resolver.calls == 4 and factory.created == 4
    with pytest.raises(QualificationProductionError, match="different intent"):
        await producer.execute("prepared_ok", preparation_speed=41)
    assert len(sent) == 4


@pytest.mark.parametrize("speed", [29, 46, 60, True])
async def test_preparation_cannot_bypass_existing_trial_limits(tmp_path, speed):
    (producer, journal, _lease, resolver, _factory), sent = _make(tmp_path)
    with pytest.raises(ValueError):
        await producer.execute("bad_preparation", preparation_speed=speed)
    assert sent == [] and journal.load("bad_preparation") is None and resolver.calls == 0


@pytest.mark.parametrize(
    "raw",
    [
        _status(speed=39),
        _status(speed=101),
        _status(speed=60, enabled=False),
        _status(speed=60, timer_enabled=True),
    ],
)
async def test_invalid_original_or_upward_preparation_has_no_write_or_journal(tmp_path, raw):
    sent = []
    (producer, journal, *_), _ = _make(tmp_path, sent=sent, sessions=[_Session([raw], sent)])
    with pytest.raises((ValueError, QualificationProductionError)):
        await producer.execute("bad_original", preparation_speed=40)
    assert sent == [] and journal.load("bad_original") is None


async def test_original_restore_must_fit_config_without_raising_the_config_limit(tmp_path):
    target = _binding(product_key=AQUARIUM_PUMP.product_key)
    (producer, journal, *_), sent = _make(
        tmp_path, target=target, epoch=_epoch(target=target, maximum=50)
    )
    with pytest.raises(ValueError, match="original speed"):
        await producer.execute("original_above_config", preparation_speed=40)
    assert sent == [] and journal.load("original_above_config") is None


async def test_feeding_zero_is_not_normalized_or_prepared(tmp_path):
    sent = []
    (producer, journal, *_), _ = _make(
        tmp_path,
        function="feeding_duration",
        sent=sent,
        sessions=[_Session([_status(speed=60, feed_time=0)], sent)],
    )
    with pytest.raises((ValueError, QualificationProductionError), match="outside"):
        await producer.execute("feed_zero_original", preparation_speed=40)
    assert sent == [] and journal.load("feed_zero_original") is None


async def test_non_aquarium_is_refused_before_resolution(tmp_path):
    (producer, journal, _lease, resolver, _factory), sent = _make(
        tmp_path,
        function="feeding_duration",
        target=_binding(product_key=LOCAL_WAVEMAKER.product_key),
    )
    with pytest.raises(QualificationProductionError, match="target encoder"):
        await producer.execute("wrong_product", preparation_speed=40)
    assert sent == [] and resolver.calls == 0 and journal.load("wrong_product") is None


@pytest.mark.parametrize("failure_index", range(4))
@pytest.mark.parametrize("function", ["motor_speed", "feeding_duration"])
async def test_each_acknowledged_failure_recovers_only_original_never_low_trial(
    tmp_path, failure_index, function
):
    states = (
        [_status(speed=x) for x in (60, 40, 39, 40, 60)]
        if function == "motor_speed"
        else [
            _status(speed=60),
            _status(speed=40),
            _status(speed=40, feed_time=1),
            _status(speed=40),
            _status(speed=60),
        ]
    )
    sent = []
    sessions = [_Session([states[i], states[i + 1]], sent) for i in range(failure_index)]
    sessions.append(_Session([states[failure_index], LocalDeviceWriteError("readback lost")], sent))
    (producer, journal, lease, resolver, _factory), _ = _make(
        tmp_path, function=function, sessions=sessions, sent=sent
    )
    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("prepared_fault", preparation_speed=40)
    record = journal.load("prepared_fault")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[failure_index].phase is ActionPhase.UNCERTAIN
    assert record.actions[failure_index].acknowledgement_sha256 is not None
    assert len(sent) == failure_index + 1
    assert not list((tmp_path / "receipts").iterdir())
    with pytest.raises(QualificationProductionError):
        await producer.execute("prepared_fault", preparation_speed=40)
    assert len(sent) == failure_index + 1
    recovered = []
    # A conservative read of the changed field forces its original rollback.
    # Existing status-byte fixtures, not a new firmware-behavior simulator.
    recovery_sessions = []
    for action in reversed(record.actions[: failure_index + 1]):
        before = _status(speed=39, feed_time=1)
        after = _status(speed=60) if action.function == "motor_speed" else _status(speed=39)
        recovery_sessions.append(_Session([before, after], recovered))
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=resolver,
        journal=journal,
        lease=lease,
        writer_factory=_WriterFactory(recovery_sessions),
    )
    result = await coordinator.recover("prepared_fault")
    assert result.phase is TransactionPhase.ROLLED_BACK
    assert recovered == [
        journal.load_blob(action.rollback_payload_sha256)
        for action in reversed(record.actions[: failure_index + 1])
    ]
    assert all(
        payload == journal.load_blob(record.actions[0].rollback_payload_sha256)
        or (
            function == "feeding_duration"
            and payload == journal.load_blob(record.actions[1].rollback_payload_sha256)
        )
        for payload in recovered
    )
    assert not list((tmp_path / "receipts").iterdir())


async def test_verified_without_actual_original_readback_cannot_finalize(tmp_path):
    (producer, journal, *_), _sent = _make(tmp_path)
    await producer.execute("prepared_evidence", preparation_speed=40)
    record = journal.load("prepared_evidence")
    bad = record.model_copy(
        update={
            "actions": (
                *record.actions[:3],
                record.actions[3].model_copy(
                    update={
                        "diagnostic": "aquarium_status_sha256."
                        + journal.install_blob(_status(speed=40)),
                    }
                ),
            )
        }
    )
    with pytest.raises(QualificationProductionError, match="readback"):
        producer._validate_prepared_evidence(bad, producer._load_prepared_intent(record))


async def test_every_payload_and_original_is_durable_before_first_send(tmp_path):
    (producer, journal, *_), sent = _make(tmp_path)
    original_execute = producer._execute_action

    async def checked(record, index, writer, prepared, permit, **kwargs):
        if index == 0:
            durable = journal.load(record.request_id)
            assert len(durable.actions) == 4 and sent == []
            assert journal.load_blob(durable.intent_sha256)
            for action in durable.actions:
                assert action.phase is ActionPhase.PREPARED
                for digest in (
                    action.forward_payload_sha256,
                    action.rollback_payload_sha256,
                    action.baseline_status_sha256,
                    action.expected_readback_sha256,
                    action.baseline_readback_sha256,
                ):
                    assert journal.load_blob(digest)
        return await original_execute(record, index, writer, prepared, permit, **kwargs)

    producer._execute_action = checked
    await producer.execute("durable_before_send", preparation_speed=40)


@pytest.mark.parametrize("failure_index", range(4))
async def test_unacknowledged_send_never_retries_or_issues_a_receipt(tmp_path, failure_index):
    states = [_status(speed=x) for x in (60, 40, 39, 40, 60)]
    sent = []
    sessions = [_Session([states[i], states[i + 1]], sent) for i in range(failure_index)]
    failed = _Session([states[failure_index]], sent, send_error=LocalDeviceWriteError("lost ACK"))
    sessions.append(failed)
    (producer, journal, *_), _ = _make(tmp_path, sent=sent, sessions=sessions)
    with pytest.raises(LocalDeviceWriteError, match="lost ACK"):
        await producer.execute("unacknowledged_write", preparation_speed=40)
    record = journal.load("unacknowledged_write")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[failure_index].acknowledgement_sha256 is None
    assert record.actions[failure_index].phase is ActionPhase.UNCERTAIN
    assert failed.quarantined and len(sent) == failure_index + 1
    assert not list((tmp_path / "receipts").iterdir())


async def test_last_ack_without_original_value_is_not_success(tmp_path):
    states = [_status(speed=x) for x in (60, 40, 39, 40, 40)]
    (producer, journal, *_), sent = _make(tmp_path, states=states)
    with pytest.raises(QualificationProductionError, match="readback did not match"):
        await producer.execute("original_not_restored", preparation_speed=40)
    record = journal.load("original_not_restored")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED and len(sent) == 4
    assert record.actions[3].acknowledgement_sha256 is not None
    assert not list((tmp_path / "receipts").iterdir())


async def test_preserved_payload_cannot_restore_a_value_other_than_initial_raw(tmp_path):
    (producer, journal, *_), _sent = _make(tmp_path)
    await producer.execute("original_payload_check", preparation_speed=40)
    record = journal.load("original_payload_check")
    action = record.actions[3].model_copy(
        update={
            "forward_payload_sha256": record.actions[0].forward_payload_sha256,
        }
    )
    bad = record.model_copy(update={"actions": (*record.actions[:3], action)})
    with pytest.raises(QualificationProductionError, match="payload evidence"):
        producer._validate_prepared_evidence(bad, producer._load_prepared_intent(record))


async def test_unrelated_unfinished_record_blocks_preparation_without_new_writes(tmp_path):
    (producer, journal, _lease, resolver, _factory), sent = _make(
        tmp_path,
        resolver_fail_at=2,
    )
    with pytest.raises(CurrentEndpointResolutionError, match="resolution failed"):
        await producer.execute("first_prepared_owner", preparation_speed=40)
    assert len(sent) == 1
    with pytest.raises(QualificationProductionError, match="requires recovery"):
        await producer.execute("second_prepared_owner", preparation_speed=40)
    assert resolver.calls == 2 and len(sent) == 1
    assert journal.load("second_prepared_owner") is None
