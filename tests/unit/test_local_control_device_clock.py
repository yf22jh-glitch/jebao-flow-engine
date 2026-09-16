"""Clock arithmetic and command ownership; no firmware simulation claims."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from jebao_flow.local_control.device_clock import clock_expectation, clock_readback_matches
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, build_device_clock_payload
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.protocol.profiles import (
    AQUARIUM_PUMP,
    DC_PUMP_PRO,
    LOCAL_WAVEMAKER,
    LOCAL_WAVEMAKER_PRO,
)
from tests.unit.test_local_control_aquarium_power import _aquarium_status
from tests.unit.test_local_control_bar_qualification import _bar_status
from tests.unit.test_local_control_qualification_producer import _status as _pro_status
from tests.unit.test_local_control_recovery_order import _fixture


def _decoded(value):
    return {
        "YMDData": bytes([value.year // 100, value.year % 100, value.month, value.day]),
        "HMSData": bytes([0, value.hour, value.minute, value.second]),
    }


def test_clock_readback_advances_across_midnight_not_equal_to_old_snapshot():
    requested = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=9)))
    prepared = requested.astimezone(UTC)
    evidence = clock_expectation(requested, prepared)
    assert clock_readback_matches(
        evidence,
        _decoded(requested + timedelta(seconds=7)),
        observed_at=prepared + timedelta(seconds=7),
    )
    assert not clock_readback_matches(
        evidence, _decoded(requested), observed_at=prepared + timedelta(seconds=20)
    )


def test_invalid_clock_readback_is_not_reported_as_verified():
    now = datetime.now(UTC)
    assert not clock_readback_matches(
        clock_expectation(now, now), {"YMDData": b"\0" * 4, "HMSData": b"\0" * 4}, observed_at=now
    )


def _clock_record(tmp_path, schema=DC_PUMP_PRO):
    coordinator, store, old, _ = _fixture(tmp_path, [], started=False)
    target = old.actions[0].target.model_copy(update={"product_key": schema.product_key})
    endpoint = replace(coordinator._resolver.endpoints[0], target=target)
    coordinator._resolver.endpoints = (endpoint,)
    now = datetime.now(UTC).replace(microsecond=0)
    writer = ConnectedDeviceWriter(endpoint, device_local_time=lambda: now)
    raw = store.load_blob(old.actions[0].baseline_status_sha256)
    if schema is LOCAL_WAVEMAKER_PRO:
        raw = _pro_status()
    elif schema is LOCAL_WAVEMAKER:
        raw = _bar_status()
    elif schema is AQUARIUM_PUMP:
        raw = _aquarium_status()
    prepared = writer.prepare_scalar(
        LocalControlIntent(
            request_id="clock_command_01",
            target=target,
            function="device_clock_sync",
            value=None,
        ),
        raw,
    )
    record = coordinator._create_record(
        request_id="clock_command_01", intent_sha256="a" * 64, prepared=(prepared,)
    )
    action = record.actions[0].model_copy(update={"phase": ActionPhase.UNCERTAIN})
    changed = record.evolve(phase=TransactionPhase.RECOVERY_REQUIRED, actions=(action,))
    store.save(record, changed)
    return coordinator, store, changed, prepared, now


@pytest.mark.parametrize(
    "schema", [DC_PUMP_PRO, LOCAL_WAVEMAKER_PRO, LOCAL_WAVEMAKER, AQUARIUM_PUMP]
)
def test_clock_command_uses_app_bytes_and_has_no_sendable_recovery(tmp_path, schema):
    coordinator, store, record, prepared, now = _clock_record(tmp_path, schema)
    assert prepared.forward_payload == build_device_clock_payload(
        prepared.target.target.product_key, now
    )
    assert prepared.recovery_policy == "observe_clock" and not prepared.no_change
    assert record.actions[0].recovery_policy == "observe_clock"
    with pytest.raises(RuntimeError, match="never constructs"):
        coordinator._recovery_prepared(record.actions[0], prepared.target)
    # The old fixture record overlaps; the clock itself is never a gate on pump control.
    assert record not in store.unfinished_for((prepared.target.target.physical_identity_key,))
    assert json.loads(prepared.expected_readback)["local_time"] == now.isoformat()


@pytest.mark.parametrize(
    "schema", [DC_PUMP_PRO, LOCAL_WAVEMAKER_PRO, LOCAL_WAVEMAKER, AQUARIUM_PUMP]
)
async def test_clock_recovery_is_observation_only_and_never_resends(tmp_path, schema):
    from tests.unit.test_local_control_coordinator import _Session, _SessionFactory

    coordinator, store, record, prepared, now = _clock_record(tmp_path, schema)
    raw = bytearray(prepared.baseline_status)
    for name, value in _decoded(now).items():
        offset = schema.by_name(name).position.byte_offset
        raw[offset : offset + 4] = value
    session = _Session([bytes(raw)])
    factory = _SessionFactory({"test-address": [session]})
    coordinator._writer_factory = lambda endpoint: ConnectedDeviceWriter(
        endpoint, session_factory=factory
    )
    result = await coordinator.recover(record.request_id)
    assert result.phase is TransactionPhase.VERIFIED and result.physical_write_count == 0
    assert session.sent == []
    assert store.load(record.request_id).actions[0].acknowledgement_sha256 is None
