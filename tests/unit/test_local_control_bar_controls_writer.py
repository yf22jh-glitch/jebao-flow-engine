"""App field ownership and exact recovery; no simulated firmware claims."""

import json
from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    LocalDeviceWriteError,
    build_control_payload,
    verify_prepared_baseline,
    verify_prepared_readback,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.state import _primary_observed_value
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER
from tests.unit.test_local_control_bar_timer_qualification import TRIAL_IMAGE, _binding, _status


def _prepare(function, value, *, maximum=50, **status):
    target = _binding()
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(target, "not-connected"),
        control_flow_bounds=(30, maximum),
        bar_timer_local_time=lambda: datetime(2026, 9, 17, 12, tzinfo=UTC),
    )
    raw = _status(image=TRIAL_IMAGE, **status)
    intent = LocalControlIntent(
        request_id="bar_app_control_01", target=target, function=function, value=value
    )
    return writer.prepare_scalar(intent, raw), raw, writer


@pytest.mark.parametrize(
    "function,value,expected",
    (
        (
            "manual_mode",
            "sine",
            {
                "Mode": "sine",
                "TimerON": False,
                "FeedSwitch": False,
                "Linkage": "independent",
                "PulseTide": False,
                "Flow": 40,
                "Frequency": 80,
            },
        ),
        ("pulse_or_tide", True, {"PulseTide": True, "Flow": 40, "Frequency": 80}),
        (
            "linkage_role",
            "independent",
            {
                "TimerON": False,
                "Flow": 40,
                "Frequency": 80,
                "Mode": "constant",
                "PulseTide": False,
                "Linkage": "independent",
            },
        ),
        (
            "linkage_role",
            "master",
            {
                "TimerON": False,
                "Flow": 40,
                "Frequency": 80,
                "Mode": "constant",
                "PulseTide": True,
                "Linkage": "master",
                "FeedSwitch": False,
            },
        ),
        (
            "linkage_role",
            "slave",
            {
                "TimerON": False,
                "Flow": 40,
                "Frequency": 80,
                "Mode": "constant",
                "PulseTide": False,
                "Linkage": "slave",
                "FeedSwitch": False,
            },
        ),
    ),
)
def test_app_payload_and_rollback_own_all_and_only_coupled_fields(function, value, expected):
    item, raw, _ = _prepare(function, value, pulse=True, feed_switch=True)
    initial = LOCAL_WAVEMAKER.decode_status(raw)
    assert item.forward_payload == build_control_payload(LOCAL_WAVEMAKER, expected)
    assert json.loads(item.expected_readback) == expected
    baseline = {name: initial[name] for name in expected}
    assert item.rollback_payload == build_control_payload(LOCAL_WAVEMAKER, baseline)
    assert json.loads(item.baseline_readback) == baseline
    assert item.baseline_status == raw and verify_prepared_baseline(item, raw)
    assert not any(name.startswith("Auto") for name in expected)
    assert "SwitchON" not in expected


def test_same_mode_still_clears_the_app_coupled_feeding_bit():
    item, _, _ = _prepare("manual_mode", "constant", feed_switch=True)
    assert not item.no_change
    assert json.loads(item.expected_readback)["FeedSwitch"] is False


def test_classic_slave_sets_pulse_even_when_original_pulse_is_false():
    item, _, _ = _prepare("linkage_role", "slave", mode=0)
    assert json.loads(item.expected_readback)["PulseTide"] is True


def test_pulse_is_not_a_timer_or_mode_gate_and_inactive_zero_frequency_is_copied():
    item, _, _ = _prepare("pulse_or_tide", True, mode=3, timer=True, frequency=0)
    assert json.loads(item.expected_readback) == {"PulseTide": True, "Flow": 40, "Frequency": 0}


def test_timer_on_and_other_role_do_not_block_app_manual_selection():
    item, _, _ = _prepare("manual_mode", "classic", timer=True, linkage=2, auto_flow=40)
    assert json.loads(item.expected_readback)["TimerON"] is False
    assert json.loads(item.baseline_readback)["TimerON"] is True


def test_master_selection_during_program_uses_program_save_not_manual_master():
    item, raw, _ = _prepare("linkage_role", "master", timer=True, auto_mode=4, auto_flow=40)
    assert item.forward_payload == build_control_payload(
        LOCAL_WAVEMAKER,
        {
            "TimerON": True,
            "FeedSwitch": False,
            "Linkage": "master",
            "AutoMode": "constant",
            "AutoFlow": 40,
            "AutoFreq": 0,
            "AutoPulseTide": False,
        },
    )
    assert json.loads(item.expected_readback) == {
        "TimerON": True,
        "FeedSwitch": False,
        "Linkage": "master",
    }
    assert verify_prepared_baseline(item, raw)
    assert not any(k.startswith("Auto") for k in json.loads(item.baseline_readback))


def test_configured_output_limit_still_applies_to_copied_flow():
    with pytest.raises(LocalDeviceWriteError, match="flow 81"):
        _prepare("manual_mode", "classic", flow=81)
    item, _, _ = _prepare("manual_mode", "classic", flow=81, maximum=100)
    assert json.loads(item.expected_readback)["Flow"] == 81


def test_verification_does_not_compare_unsent_clock_auto_or_power():
    item, _, _ = _prepare("pulse_or_tide", True)
    changed = _status(
        image=TRIAL_IMAGE,
        pulse=True,
        enabled=False,
        timer=True,
        mode=2,
        linkage=1,
        auto_mode=3,
        auto_flow=100,
    )
    assert verify_prepared_readback(item, changed)
    assert not verify_prepared_readback(item, _status(pulse=True, flow=39))


@pytest.mark.parametrize(
    "function,field,value",
    (
        ("manual_mode", "Mode", "constant"),
        ("pulse_or_tide", "PulseTide", True),
        ("linkage_role", "Linkage", "slave"),
    ),
)
def test_ha_observation_uses_primary_field_not_coupled_timer(function, field, value):
    descriptor = build_local_control_catalog().find(LOCAL_WAVEMAKER.product_key, function)
    assert _primary_observed_value(descriptor, {field: value, "TimerON": False}) == value


def test_owned_restore_preserves_inactive_pulse_and_feeding_without_claiming_elapsed_time():
    _, raw, writer = _prepare("manual_mode", "classic", pulse=True, feed_switch=True)
    fields = ("TimerON", "Mode", "Flow", "Frequency", "PulseTide", "Linkage", "FeedSwitch")
    item = writer.prepare_bar_restore_controls(raw, fields=fields)
    assert item.function == "bar_owned_controls"
    assert item.forward_payload == item.rollback_payload
    assert item.expected_readback == item.baseline_readback
    assert json.loads(item.expected_readback)["PulseTide"] is True
    assert json.loads(item.expected_readback)["FeedSwitch"] is True
    assert verify_prepared_readback(item, raw)


@pytest.mark.parametrize("fields", ((), ("AutoFlow",), ("YMDData",), ("Flow", "Flow")))
def test_owned_restore_cannot_target_dynamic_or_unowned_fields(fields):
    _, raw, writer = _prepare("manual_mode", "classic")
    with pytest.raises(ValueError, match="restore fields"):
        writer.prepare_bar_restore_controls(raw, fields=fields)


@pytest.mark.parametrize("timer,linkage", ((False, 0), (True, 0), (True, 1), (False, 2)))
def test_full_image_save_has_no_invented_timer_or_role_admission(timer, linkage):
    value = {
        "family": "bar",
        "slots": [
            {
                "slot_index": 0,
                "start": "00:00",
                "end": "24:00",
                "mode": "constant",
                "flow": 39,
                "frequency": 0,
                "pulse_tide": False,
            }
        ],
    }
    item, _, _ = _prepare("schedule", value, timer=timer, linkage=linkage)
    assert item.verification_kind == "schedule_image"
    assert item.expected_readback[:8] == bytes((0, 0, 24, 0, 4, 39, 0, 0))
    with pytest.raises(ValueError, match="flow 100"):
        _prepare(
            "schedule",
            {**value, "slots": [{**value["slots"][0], "flow": 100}]},
            timer=timer,
            linkage=linkage,
        )
