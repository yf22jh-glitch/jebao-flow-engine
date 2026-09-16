"""Audited DC command bytes and owned rollback, not simulated firmware acceptance."""

import json

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    LocalDeviceWriteError,
    verify_prepared_readback,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.state import _primary_observed_value
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.profiles import DC_PUMP_PRO
from tests.unit.test_local_control_bar_qualification import _binding
from tests.unit.test_local_control_dc_qualification import _status


def _prepare(
    function,
    value,
    *,
    timer=True,
    enabled=True,
    flow=40,
    maximum=50,
    schedule_flow=40,
    auto_flow=100,
):
    target = _binding(product_key=DC_PUMP_PRO.product_key)
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(target, "not-connected"), control_flow_bounds=(30, maximum)
    )
    raw = bytearray(_status(timer_enabled=timer, enabled=enabled, flow=flow))
    raw[6] = auto_flow
    raw[9:393] = b"\xee" * 384
    raw[9:17] = bytes([0, 0, 23, 59, 0, schedule_flow, 0, 0])
    intent = LocalControlIntent(
        request_id="dc_timer_test_01", target=target, function=function, value=value
    )
    return writer.prepare_scalar(intent, bytes(raw)), bytes(raw)


@pytest.mark.parametrize(
    "function,value,expected",
    [
        ("schedule_enable", True, {"TimerON": True, "SwitchON": True}),
        ("schedule_enable", False, {"TimerON": False, "SwitchON": False}),
        ("manual_enable", True, {"TimerON": False, "SwitchON": True}),
        ("manual_enable", False, {"TimerON": True, "SwitchON": False}),
    ],
)
def test_each_app_screen_has_its_own_power_coupling(function, value, expected):
    prepared, raw = _prepare(function, value)
    assert prepared.forward_payload == build_control_payload(DC_PUMP_PRO, expected)
    assert prepared.rollback_payload == build_control_payload(
        DC_PUMP_PRO, {"TimerON": True, "SwitchON": True}
    )
    assert json.loads(prepared.expected_readback) == expected
    assert prepared.baseline_status == raw
    assert prepared.restore_on_recovery is True


def test_same_desired_state_is_idempotent_not_an_admission_failure():
    prepared, raw = _prepare("schedule_enable", True)
    assert prepared.no_change
    assert verify_prepared_readback(prepared, raw)


def test_verification_owns_only_sent_fields_not_clock_auto_or_manual_settings():
    prepared, raw = _prepare("manual_enable", True)
    changed = bytearray(raw)
    timer_bit = DC_PUMP_PRO.by_name("TimerON").position.bit_offset
    changed[0] &= ~(1 << timer_bit)
    changed[2:9] = bytes([35, 50, 11, 3, 38, 8, 9])
    assert verify_prepared_readback(prepared, bytes(changed))
    assert set(json.loads(prepared.expected_readback)) == {"TimerON", "SwitchON"}


def test_switching_to_manual_checks_the_output_it_would_enable():
    with pytest.raises(LocalDeviceWriteError, match="manual flow 81"):
        _prepare("manual_enable", True, flow=81)
    prepared, _ = _prepare("manual_enable", True, flow=81, maximum=100)
    assert not prepared.no_change


def test_enabling_schedule_checks_future_slot_output_not_stale_auto():
    with pytest.raises(LocalDeviceWriteError, match="slot 0 flow 100"):
        _prepare("schedule_enable", True, timer=False, schedule_flow=100)
    prepared, _ = _prepare("schedule_enable", True, timer=False, auto_flow=100)
    assert not prepared.no_change


@pytest.mark.parametrize("value", [1, 0, "ON", None])
def test_bool_is_not_coerced(value):
    with pytest.raises(TypeError, match="true or false"):
        _prepare("schedule_enable", value)


@pytest.mark.parametrize(
    "timer,power,schedule,manual",
    [
        (True, True, True, False),
        (False, True, False, True),
        (True, False, False, False),
        (False, False, False, False),
    ],
)
def test_ha_switches_report_actual_power_and_timer_not_inverse_timer_alone(
    timer, power, schedule, manual
):
    catalog = build_local_control_catalog()
    observed = {"TimerON": timer, "SwitchON": power}
    for function, expected in (("schedule_enable", schedule), ("manual_enable", manual)):
        descriptor = catalog.find(DC_PUMP_PRO.product_key, function)
        assert _primary_observed_value(descriptor, observed) is expected


@pytest.mark.parametrize("timer", (True, False))
def test_dc_schedule_edit_does_not_require_turning_the_timer_off(timer):
    value = {
        "family": "dc_pro",
        "slots": [
            {
                "slot_index": 0,
                "start": "00:00",
                "end": "24:00",
                "mode": "sine",
                "flow": 39,
                "frequency": 20,
            }
        ],
    }
    prepared, raw = _prepare("schedule", value, timer=timer, auto_flow=100)
    assert prepared.verification_kind == "schedule_image"
    assert prepared.expected_readback[:8] == bytes([0, 0, 24, 0, 2, 39, 20, 0])
    assert prepared.baseline_status == raw
    with pytest.raises(LocalDeviceWriteError, match="flow 100"):
        _prepare("schedule", {**value, "slots": [{**value["slots"][0], "flow": 100}]}, timer=timer)


def test_dc_power_on_checks_the_output_it_enables_but_off_can_always_stop_it():
    with pytest.raises(LocalDeviceWriteError, match="flow 100"):
        _prepare("power", True, enabled=False, schedule_flow=100)
    prepared, _ = _prepare("power", False, schedule_flow=100)
    assert not prepared.restore_on_recovery
