import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    verify_prepared_baseline,
    verify_prepared_readback,
)
from jebao_flow.local_control.dosing_controls import (
    decode_dosing_channel_readback,
    parse_dosing_schedule_value,
    prepare_dosing_control_command,
)
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.profiles import DOSING_PUMP
from tests.unit.test_local_control_coordinator import (
    _coordinator,
    _Resolver,
    _Session,
    _SessionFactory,
)


def _binding() -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="dosing_pump",
        product_key=DOSING_PUMP.product_key,
        physical_identity_key="d" * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 17, tzinfo=UTC),
    )


def _status(**changes) -> bytes:
    values = {"Calib1": 20, **changes}
    payload = build_control_payload(DOSING_PUMP, values)
    writable = payload[1 + DOSING_PUMP.attribute_flags_size :]
    return writable + bytes(DOSING_PUMP.raw_status_size - len(writable))


def _writer() -> ConnectedDeviceWriter:
    return ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "not-connected"),
        session_factory=lambda _address: None,
    )


def _schedule_image(*, minute: int, volume: int) -> bytes:
    return b"".join(bytes((hour, minute, volume >> 8, volume & 0xFF)) for hour in range(24))


def _intent(function: str, value: object) -> LocalControlIntent:
    return LocalControlIntent(
        request_id="dosing_command_01",
        target=_binding(),
        function=function,
        value=value,
    )


@pytest.mark.parametrize(
    "function,value,expected",
    [
        ("power", True, {"switch": True}),
        ("manual_channel", {"channel": 2, "value": True}, {"channe2": True}),
        (
            "channel_timer_enable",
            {"channel": 3, "value": False},
            {"Timer3ON": False},
        ),
        ("interval_days", {"channel": 4, "value": 7}, {"IntervalT4": 7}),
        ("calibration_channel", "channel_3", {"CALSet": "calibrate_3"}),
        ("calibration_amount", 40, {"Calib1": 40}),
    ],
)
def test_app_scalar_shapes_are_exact(function, value, expected):
    command = prepare_dosing_control_command(function, value, DOSING_PUMP.decode_status(_status()))
    assert dict(command.fields) == expected


def test_calibration_toggle_copies_current_channel_and_amount():
    current = DOSING_PUMP.decode_status(_status(CALSet="calibrate_4", Calib1=70))
    command = prepare_dosing_control_command("calibration", True, current)
    assert dict(command.fields) == {
        "CALSW": True,
        "CALSet": "calibrate_4",
        "Calib1": 70,
    }


@pytest.mark.parametrize("amount", [9, 11, 101])
def test_calibration_amount_uses_the_app_range_and_step(amount):
    with pytest.raises(ValueError):
        prepare_dosing_control_command(
            "calibration_amount", amount, DOSING_PUMP.decode_status(_status())
        )


def test_writer_owns_only_the_fields_in_the_app_command():
    before = _status(channe2=False)
    prepared = _writer().prepare_scalar(
        _intent("manual_channel", {"channel": 2, "value": True}), before
    )
    assert prepared.forward_payload == build_control_payload(DOSING_PUMP, {"channe2": True})
    assert prepared.rollback_payload == build_control_payload(DOSING_PUMP, {"channe2": False})
    assert json.loads(prepared.expected_readback) == {"channe2": True}
    assert verify_prepared_baseline(prepared, before)
    assert verify_prepared_readback(prepared, _status(channe2=True))


def test_power_manual_and_calibration_stops_never_restart_on_recovery():
    power = _writer().prepare_scalar(
        _intent("power", False),
        _status(switch=True),
    )
    manual = _writer().prepare_scalar(
        _intent("manual_channel", {"channel": 1, "value": False}),
        _status(channe1=True),
    )
    calibration = _writer().prepare_scalar(
        _intent("calibration", False),
        _status(CALSW=True, CALSet="calibrate_1", Calib1=20),
    )
    assert not power.restore_on_recovery
    assert not manual.restore_on_recovery
    assert not calibration.restore_on_recovery


def test_channel_schedule_replaces_only_one_complete_96_byte_datapoint():
    original = _schedule_image(minute=0, volume=10)
    desired = _schedule_image(minute=30, volume=20)
    before = _status(CH3SWTime=original)
    (prepared,) = _writer().prepare_dosing_schedule_save(
        _intent("channel_schedule", {"channel": 3, "image": list(desired)}), before
    )
    channel, image = decode_dosing_channel_readback(prepared.expected_readback)
    assert channel == 3 and image == desired
    assert prepared.forward_payload == build_control_payload(DOSING_PUMP, {"CH3SWTime": desired})
    assert prepared.rollback_payload == build_control_payload(DOSING_PUMP, {"CH3SWTime": original})
    assert verify_prepared_readback(prepared, _status(CH3SWTime=desired))
    assert verify_prepared_baseline(prepared, before)


def test_average_schedule_adds_one_separate_interval_frame():
    desired = bytes(96)
    actions = _writer().prepare_dosing_schedule_save(
        _intent(
            "channel_schedule",
            {"channel": 2, "image": list(desired), "interval_days": 6},
        ),
        _status(IntervalT2=1),
    )
    assert [action.function for action in actions] == ["channel_schedule", "interval_days"]
    assert actions[1].forward_payload == build_control_payload(DOSING_PUMP, {"IntervalT2": 6})


def test_schedule_json_is_bounded_to_one_channel_image():
    assert parse_dosing_schedule_value({"channel": 1, "image": [0] * 96, "interval_days": 2}) == (
        1,
        bytes(96),
        2,
    )
    for invalid in (
        {"channel": 0, "image": [0] * 96},
        {"channel": 1, "image": [0] * 95},
        {"channel": 1, "image": [0] * 96, "extra": True},
    ):
        with pytest.raises(ValueError):
            parse_dosing_schedule_value(invalid)


@pytest.mark.parametrize(
    "entry",
    [
        [24, 0, 0, 1],
        [0, 60, 0, 1],
        [0, 0, 0x27, 0x10],
    ],
)
def test_schedule_entries_stay_inside_the_app_picker_range(entry):
    with pytest.raises(ValueError, match="app input range"):
        parse_dosing_schedule_value({"channel": 1, "image": entry + [0] * 92})


async def test_schedule_readback_failure_recovers_the_owned_channel_without_replaying_forward(
    tmp_path: Path,
):
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="current")
    original = _schedule_image(minute=0, volume=10)
    desired = _schedule_image(minute=30, volume=20)
    before = _status(CH2SWTime=original)
    after = _status(CH2SWTime=desired)
    failed = _Session([before])
    recovery = _Session([after, before])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [failed, recovery]}),
        identity_control_authorizations={
            (DOSING_PUMP.product_key, "channel_schedule"): frozenset({target.physical_identity_key})
        },
    )
    intent = LocalControlIntent(
        request_id="dosing_schedule_recovery_01",
        target=target,
        function="channel_schedule",
        value={"channel": 2, "image": list(desired)},
    )

    with pytest.raises(LocalControlExecutionError, match="readback is unavailable"):
        await coordinator.execute(intent)
    uncertain = journal.load(intent.request_id)
    assert uncertain is not None
    assert uncertain.phase is TransactionPhase.RECOVERY_REQUIRED
    assert uncertain.actions[0].phase is ActionPhase.UNCERTAIN
    assert uncertain.actions[0].verification_kind == "dosing_channel_image"
    assert len(failed.sent) == 1

    restored = await coordinator.recover(intent.request_id)

    assert restored.phase is TransactionPhase.ROLLED_BACK
    assert restored.physical_write_count == 1
    assert len(failed.sent) == 1
    assert recovery.sent == [journal.load_blob(uncertain.actions[0].rollback_payload_sha256)]
