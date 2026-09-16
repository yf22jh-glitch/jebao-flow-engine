"""App payload/durable software contracts; not simulated pump acceptance."""

import json
from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.catalog import (
    build_local_control_catalog,
    internal_function_value,
    public_function_value,
)
from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    LocalDeviceWriteError,
    build_aquarium_app_payload,
    build_control_payload,
    verify_prepared_baseline,
    verify_prepared_readback,
)
from jebao_flow.local_control.journal import ActionPhase, LocalControlJournalStore, TransactionPhase
from jebao_flow.local_control.models import InstallContentBindings, LocalControlIntent
from jebao_flow.local_control.pump_preview import ReturnPumpSchedule
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import AQUARIUM_PUMP
from tests.unit.test_local_control_aquarium_power import _binding, _Lease
from tests.unit.test_local_control_aquarium_qualification import _status as _base_status
from tests.unit.test_local_control_coordinator import _qualification, _Resolver, _Session


def _schedule(speed=40):
    return ReturnPumpSchedule.model_validate(
        {
            "family": "aquarium",
            "slots": [
                {"slot_index": 0, "start": "00:00", "end": "24:00", "mode": "auto", "speed": speed}
            ],
        }
    )


def _status(*, image=None, auto_speed=40, **changes):
    raw = bytearray(_base_status(**changes))
    raw[3] = auto_speed
    raw[13:301] = _schedule().compile_image() if image is None else image
    return bytes(raw)


def _intent(function, value):
    return LocalControlIntent(
        request_id="aquarium_app_command", target=_binding(), function=function, value=value
    )


def _writer(*, session=None, maximum=50):
    return ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "not-connected", wifi_firmware_version="wifi-current"),
        session_factory=(lambda address: session) if session else lambda address: None,
        control_flow_bounds=(30, maximum),
        device_local_time=lambda: datetime(2026, 9, 17, 12, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "function,value,changes,expected",
    [
        ("motor_speed", 39, {}, {"Motor_Speed": 39}),
        ("motor_speed", 39, {"timer_enabled": True}, {"Motor_Speed": 39, "TimerON": False}),
        ("feed_switch", True, {}, {"FeedSwitch": True, "TimerON": False, "FeedTime": 10}),
        (
            "feed_switch",
            False,
            {"feed": True},
            {"FeedSwitch": False, "TimerON": False, "FeedTime": 10, "Motor_Speed": 40},
        ),
        ("feeding_duration", 2, {"feed": True}, {"FeedTime": 2, "FeedSwitch": True}),
        ("schedule_enable", False, {"timer_enabled": True}, {"TimerON": False, "Motor_Speed": 40}),
        ("transport_mode", "wireless", {}, {"Mode": True}),
        ("transport_mode", "ap", {"transport": True}, {"Mode": False}),
    ],
)
def test_app_command_owns_exactly_its_persistent_fields(function, value, changes, expected):
    raw = _status(**changes)
    item = _writer().prepare_scalar(_intent(function, value), raw)
    original = AQUARIUM_PUMP.decode_status(raw)
    baseline = {name: original[name] for name in expected}
    assert item.forward_payload == build_control_payload(AQUARIUM_PUMP, expected)
    assert json.loads(item.expected_readback) == expected
    assert item.rollback_payload == build_control_payload(AQUARIUM_PUMP, baseline)
    assert json.loads(item.baseline_readback) == baseline
    assert item.baseline_status == raw and verify_prepared_baseline(item, raw)
    assert not any(name.startswith("Auto") for name in baseline)


def test_timer_on_seeds_schedule_not_stale_auto_and_owns_no_seed():
    raw = _status(auto_mode=1, auto_speed=100)
    item = _writer().prepare_scalar(_intent("schedule_enable", True), raw)
    assert item.forward_payload == build_control_payload(
        AQUARIUM_PUMP,
        {
            "TimerON": True,
            "FeedSwitch": False,
            "AutoMode": "auto",
            "AutoGears": 40,
        },
    )
    assert json.loads(item.expected_readback) == {"TimerON": True, "FeedSwitch": False}
    assert json.loads(item.baseline_readback) == {"TimerON": False, "FeedSwitch": False}
    # Persistent verification is deliberately not an Auto seed acceptance claim.
    assert verify_prepared_readback(item, _status(timer_enabled=True, auto_speed=100))


@pytest.mark.parametrize(
    "function,value", [("schedule_enable", False), ("feed_switch", True), ("feed_switch", False)]
)
def test_entering_manual_checks_the_output_it_will_activate(function, value):
    with pytest.raises((ValueError, LocalDeviceWriteError), match="(output|speed|bounds)"):
        _writer().prepare_scalar(_intent(function, value), _status(timer_enabled=True, speed=81))


def test_captured_zero_duration_is_copied_but_never_becomes_new_input():
    raw = _status(feed_time=0)
    item = _writer().prepare_scalar(_intent("feed_switch", True), raw)
    assert json.loads(item.expected_readback)["FeedTime"] == 0
    decoded = AQUARIUM_PUMP.decode_status(item.forward_payload[9:] + b"\x00")
    assert decoded["FeedTime"] == 0 and decoded["FeedSwitch"] is True
    assert verify_prepared_baseline(item, raw)
    with pytest.raises(ValueError):
        _writer().prepare_scalar(_intent("feeding_duration", 0), raw)
    with pytest.raises(ValueError):
        build_aquarium_app_payload({"FeedTime": 0}, _status(feed_time=10))


def test_transport_direction_uses_vendor_declaration_in_both_directions():
    product = AQUARIUM_PUMP.product_key
    for label, bit in (("ap", False), ("wireless", True)):
        assert internal_function_value(product, "transport_mode", label) is bit
        assert public_function_value(product, "transport_mode", bit) == label
    with pytest.raises(ValueError):
        _writer().prepare_scalar(_intent("transport_mode", "guess"), _status())


def test_owned_restore_is_exact_raw_and_not_a_no_change_action():
    raw = _status(feed_time=0, timer_enabled=True, transport=True, feed=True)
    fields = ("FeedSwitch", "FeedTime", "Motor_Speed", "TimerON")
    item = _writer().prepare_aquarium_restore_controls(raw, fields)
    assert item.function == "aquarium_owned_controls" and not item.no_change
    assert item.forward_payload == item.rollback_payload
    assert item.forward_payload[9:] == raw[:301]
    assert json.loads(item.expected_readback) == {
        name: AQUARIUM_PUMP.decode_status(raw)[name] for name in fields
    }
    assert verify_prepared_readback(item, raw) and item.baseline_status == raw
    for invalid in ((), ("AutoGears",), ("SwitchON",), ("Mode",), ("Motor_Speed", "Motor_Speed")):
        with pytest.raises(ValueError):
            _writer().prepare_aquarium_restore_controls(raw, invalid)


@pytest.mark.parametrize(
    "timer,enabled", [(False, False), (False, True), (True, False), (True, True)]
)
def test_future_schedule_outputs_are_bounded_even_while_inactive(timer, enabled):
    with pytest.raises(ValueError, match="exceeds"):
        _writer().prepare_scalar(
            _intent("schedule", _schedule(100).model_dump(mode="json")),
            _status(timer_enabled=timer, enabled=enabled),
        )


def test_power_off_remains_available_when_current_output_exceeds_limit():
    raw = _status(speed=81)
    off = _writer().prepare_scalar(_intent("power", False), raw)
    assert not off.restore_on_recovery
    with pytest.raises(LocalDeviceWriteError, match="output 81"):
        _writer().prepare_scalar(_intent("power", True), raw)


@pytest.mark.parametrize("timer", [False, True])
def test_full_save_has_one_image_and_only_active_timer_adds_a_separate_seed(timer):
    raw = _status(timer_enabled=timer)
    planned = _writer().prepare_aquarium_schedule_save(
        _intent("schedule", _schedule(39).model_dump(mode="json")), raw
    )
    assert len(planned) == (2 if timer else 1)
    assert all(item.baseline_status == raw for item in planned)
    assert planned[0].expected_readback == _schedule(39).compile_image()
    if timer:
        assert planned[1].forward_payload == build_control_payload(
            AQUARIUM_PUMP, {"AutoMode": "auto", "AutoGears": 39}
        )
        assert planned[1].rollback_payload == planned[0].rollback_payload
        assert not planned[1].no_change
    assert (
        len(
            _writer().prepare_aquarium_schedule_save(
                _intent("schedule", _schedule().model_dump(mode="json")), raw
            )
        )
        == 1
    )


def _coordinator(tmp_path, session):
    qualification = _qualification("schedule").model_copy(
        update={"product_key": AQUARIUM_PUMP.product_key}
    )
    content = InstallContentBindings(
        app_catalog_sha256=qualification.app_catalog_sha256,
        protocol_schema_sha256=qualification.schema_sha256,
        template_sha256=(qualification.template_sha256,),
    )

    def verifier(item):
        return frozenset({_binding().physical_identity_key})

    catalog = build_local_control_catalog(
        (qualification,), current_content=content, _qualification_verifier_for_test=verifier
    )
    writer = _writer(session=session)
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    coordinator = LocalControlCoordinator(
        catalog=catalog,
        resolver=_Resolver((writer.endpoint,)),
        journal=journal,
        lease=_Lease(),
        writer_factory=lambda endpoint: _writer(session=session),
        qualification_verifier=verifier,
    )
    return coordinator, journal


async def test_full_save_is_durable_before_first_frame_and_replay_sends_nothing(tmp_path):
    before = _status(timer_enabled=True)
    after = _status(timer_enabled=True, image=_schedule(39).compile_image())
    session = _Session([before, after, after])
    coordinator, journal = _coordinator(tmp_path, session)
    original_send = session.send_raw_control

    async def checked_send(payload):
        record = journal.load("aquarium_app_command")
        assert len(record.actions) == 2
        for action in record.actions:
            for digest in (action.forward_payload_sha256, action.rollback_payload_sha256):
                assert journal.load_blob(digest)
        return await original_send(payload)

    session.send_raw_control = checked_send
    intent = _intent("schedule", _schedule(39).model_dump(mode="json"))
    result = await coordinator.execute(intent)
    assert result.phase is TransactionPhase.VERIFIED and result.physical_write_count == 2
    assert len(session.sent) == 2
    replay = await coordinator.execute(intent)
    assert replay.physical_write_count == 0 and len(session.sent) == 2


async def test_seed_ack_then_bad_image_is_uncertain_and_recovery_never_reseeds(tmp_path):
    before = _status(timer_enabled=True)
    after = _status(timer_enabled=True, image=_schedule(39).compile_image())
    session = _Session([before, after, before])
    coordinator, journal = _coordinator(tmp_path, session)
    with pytest.raises(LocalControlExecutionError, match="readback"):
        await coordinator.execute(_intent("schedule", _schedule(39).model_dump(mode="json")))
    record = journal.load("aquarium_app_command")
    assert tuple(a.phase for a in record.actions) == (ActionPhase.VERIFIED, ActionPhase.UNCERTAIN)
    assert record.actions[1].acknowledgement_sha256 is not None
    seed = session.sent[1]
    session.statuses.extend([after, before, before])
    session.sent.clear()
    result = await coordinator.recover("aquarium_app_command")
    assert result.phase is TransactionPhase.ROLLED_BACK
    assert session.sent == [journal.load_blob(record.actions[0].rollback_payload_sha256)]
    assert seed not in session.sent
