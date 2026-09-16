"""Offline tests for the Bar wavemaker's app-shaped control planner.

These pin the four distinct app callbacks the Bar template uses to change ``Mode``, ``PulseTide``
and ``Linkage`` -- none of which the app ever writes on its own. They assert the planned payload
and its validation only; nothing here claims the firmware accepts or applies any command.
"""

from __future__ import annotations

import dataclasses

import pytest

from jebao_flow.local_control.bar_controls import (
    BarControlCommand,
    prepare_bar_control_command,
)
from jebao_flow.local_control.device_writer import build_control_payload, get_product_schema
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRODUCT_KEY

_FULL_CURRENT = {
    "Flow": 60,
    "Frequency": 80,
    "Mode": "classic",
    "PulseTide": True,
    "TimerON": True,
    "FeedSwitch": True,
    "Linkage": "slave",
    "FeedTime": 12,
}

_SHAPES = (
    (
        "manual_mode",
        "constant",
        "mode_select_onchange",
        {
            "TimerON": False,
            "FeedSwitch": False,
            "Linkage": "independent",
            "Mode": "constant",
            "PulseTide": False,
            "Frequency": 80,
            "Flow": 60,
        },
    ),
    (
        "pulse_or_tide",
        False,
        "manual_setting_modal_confirm",
        {"Flow": 60, "Frequency": 80, "PulseTide": False},
    ),
    (
        "linkage_role",
        "independent",
        "switch_to_manual",
        {
            "TimerON": False,
            "Flow": 60,
            "Frequency": 80,
            "Mode": "classic",
            "PulseTide": True,
            "Linkage": "independent",
        },
    ),
    (
        "linkage_role",
        "master",
        "edit_link_mode",
        {
            "Linkage": "master",
            "TimerON": False,
            "FeedSwitch": False,
            "Mode": "classic",
            "Flow": 60,
            "Frequency": 80,
            "PulseTide": True,
        },
    ),
    (
        "linkage_role",
        "slave",
        "edit_mode_save",
        {
            "TimerON": False,
            "Flow": 60,
            "Mode": "classic",
            "PulseTide": True,
            "Frequency": 80,
            "Linkage": "slave",
            "FeedSwitch": False,
        },
    ),
)

_REQUIRED_CURRENT_KEYS = {
    ("manual_mode", "constant"): {"Flow", "Frequency"},
    ("pulse_or_tide", True): {"Flow", "Frequency"},
    ("linkage_role", "independent"): {"Flow", "Frequency", "Mode", "PulseTide"},
    ("linkage_role", "master"): {"Flow", "Frequency", "Mode", "PulseTide"},
    ("linkage_role", "slave"): {"Flow", "Frequency", "Mode"},
}


@pytest.mark.parametrize(("function", "value", "action", "expected"), _SHAPES)
def test_planned_shapes_match_the_app_callbacks(function, value, action, expected):
    command = prepare_bar_control_command(function, value, current=_FULL_CURRENT)

    assert command.action == action
    assert dict(command.fields) == expected


@pytest.mark.parametrize(("function", "value", "action", "expected"), _SHAPES)
def test_every_shape_carries_flow_and_no_feeding_field(function, value, action, expected):
    command = prepare_bar_control_command(function, value, current=_FULL_CURRENT)

    assert "Flow" in command.fields
    assert "FeedTime" not in command.fields


def test_switch_to_manual_omits_feed_switch_but_the_other_roles_carry_it():
    independent = prepare_bar_control_command("linkage_role", "independent", current=_FULL_CURRENT)
    master = prepare_bar_control_command("linkage_role", "master", current=_FULL_CURRENT)
    slave = prepare_bar_control_command("linkage_role", "slave", current=_FULL_CURRENT)

    assert "FeedSwitch" not in independent.fields
    assert master.fields["FeedSwitch"] is False
    assert slave.fields["FeedSwitch"] is False


def test_the_two_same_named_app_callbacks_stay_distinguishable():
    role_save = prepare_bar_control_command("linkage_role", "slave", current=_FULL_CURRENT)
    modal_confirm = prepare_bar_control_command("pulse_or_tide", True, current=_FULL_CURRENT)

    assert role_save.action != modal_confirm.action
    assert "Linkage" in role_save.fields
    assert "Linkage" not in modal_confirm.fields


@pytest.mark.parametrize("mode", ("classic", "sine", "random", "constant"))
@pytest.mark.parametrize("pulse", (True, False))
def test_independent_keeps_pulse_only_while_the_mode_is_classic(mode, pulse):
    current = dict(_FULL_CURRENT, Mode=mode, PulseTide=pulse)

    command = prepare_bar_control_command("linkage_role", "independent", current=current)

    assert command.fields["PulseTide"] is (pulse and mode == "classic")


@pytest.mark.parametrize("mode", ("classic", "sine", "random", "constant"))
@pytest.mark.parametrize("pulse", (True, False))
def test_master_passes_the_current_pulse_through_unconditionally(mode, pulse):
    current = dict(_FULL_CURRENT, Mode=mode, PulseTide=pulse)

    command = prepare_bar_control_command("linkage_role", "master", current=current)

    assert command.fields["PulseTide"] is pulse
    assert command.fields["Mode"] == mode


@pytest.mark.parametrize("mode", ("classic", "sine", "random", "constant"))
@pytest.mark.parametrize("pulse", (True, False))
def test_slave_derives_pulse_from_the_mode_and_ignores_the_current_pulse(mode, pulse):
    current = dict(_FULL_CURRENT, Mode=mode, PulseTide=pulse)

    command = prepare_bar_control_command("linkage_role", "slave", current=current)

    assert command.fields["PulseTide"] is (mode == "classic")


@pytest.mark.parametrize("desired", (True, False))
def test_pulse_or_tide_is_planned_whatever_the_current_mode_is(desired):
    for mode in ("classic", "sine", "random", "constant"):
        current = dict(_FULL_CURRENT, Mode=mode)

        command = prepare_bar_control_command("pulse_or_tide", desired, current=current)

        assert command.fields["PulseTide"] is desired
        assert "Mode" not in command.fields


@pytest.mark.parametrize("mode", ("classic", "sine", "random", "constant"))
def test_manual_mode_always_clears_pulse_and_claims_independence(mode):
    command = prepare_bar_control_command("manual_mode", mode, current=_FULL_CURRENT)

    assert command.fields["Mode"] == mode
    assert command.fields["PulseTide"] is False
    assert command.fields["Linkage"] == "independent"
    assert command.fields["TimerON"] is False
    assert command.fields["FeedSwitch"] is False


@pytest.mark.parametrize(("key", "required"), sorted(_REQUIRED_CURRENT_KEYS.items()))
def test_only_the_echoed_current_keys_are_required(key, required):
    function, value = key
    minimal = {name: _FULL_CURRENT[name] for name in required}

    command = prepare_bar_control_command(function, value, current=minimal)

    assert command.fields
    for name in sorted(required):
        missing = {other: minimal[other] for other in minimal if other != name}
        with pytest.raises(ValueError, match=f"missing {name}"):
            prepare_bar_control_command(function, value, current=missing)


@pytest.mark.parametrize(("function", "value", "action", "expected"), _SHAPES)
def test_planned_fields_are_immutable(function, value, action, expected):
    command = prepare_bar_control_command(function, value, current=_FULL_CURRENT)

    with pytest.raises(TypeError):
        command.fields["Flow"] = 31  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.action = "other"  # type: ignore[misc]
    assert isinstance(command, BarControlCommand)


@pytest.mark.parametrize(("function", "value", "action", "expected"), _SHAPES)
def test_planned_fields_encode_against_the_real_product_schema(function, value, action, expected):
    schema = get_product_schema(LOCAL_WAVEMAKER_PRODUCT_KEY)
    command = prepare_bar_control_command(function, value, current=_FULL_CURRENT)

    payload = build_control_payload(schema, dict(command.fields))

    assert payload


def test_an_unaudited_function_is_refused():
    with pytest.raises(ValueError, match="no audited Bar app control shape"):
        prepare_bar_control_command("feed_switch", True, current=_FULL_CURRENT)


@pytest.mark.parametrize("value", (0, 1, "classic", None, 1.0))
def test_pulse_or_tide_refuses_anything_that_is_not_a_bool(value):
    with pytest.raises(TypeError, match="true or false"):
        prepare_bar_control_command("pulse_or_tide", value, current=_FULL_CURRENT)


@pytest.mark.parametrize("function", ("manual_mode", "linkage_role"))
@pytest.mark.parametrize("value", (0, True, None, ("classic",)))
def test_enum_controls_refuse_non_string_values(function, value):
    with pytest.raises(TypeError):
        prepare_bar_control_command(function, value, current=_FULL_CURRENT)


@pytest.mark.parametrize(
    ("function", "value"),
    (
        ("manual_mode", "feed"),
        ("manual_mode", "pulse"),
        ("manual_mode", "independent"),
        ("linkage_role", "classic"),
        ("linkage_role", "follower"),
    ),
)
def test_enum_controls_refuse_values_outside_the_audited_app_choices(function, value):
    with pytest.raises(ValueError, match="audited app choices"):
        prepare_bar_control_command(function, value, current=_FULL_CURRENT)


@pytest.mark.parametrize(
    ("name", "value"),
    (("Flow", -1), ("Flow", 101), ("Frequency", -1), ("Frequency", 101)),
)
def test_current_values_outside_the_copyable_range_are_refused(name, value):
    current = dict(_FULL_CURRENT, **{name: value})

    with pytest.raises(ValueError, match="copyable range"):
        prepare_bar_control_command("manual_mode", "constant", current=current)


@pytest.mark.parametrize(
    ("name", "value"), (("Flow", 0), ("Flow", 29), ("Frequency", 0), ("Frequency", 4))
)
def test_a_current_value_below_the_app_input_minimum_is_still_copied(name, value):
    """The app echoes whatever the device reports; its picker minimum is not re-applied here."""

    current = dict(_FULL_CURRENT, **{name: value})

    command = prepare_bar_control_command("manual_mode", "constant", current=current)

    assert command.fields[name] == value


@pytest.mark.parametrize(("name", "value"), (("Flow", True), ("Flow", 60.0), ("Frequency", "80")))
def test_current_scalar_types_are_refused(name, value):
    current = dict(_FULL_CURRENT, **{name: value})

    with pytest.raises(TypeError, match="must be an integer"):
        prepare_bar_control_command("manual_mode", "constant", current=current)


@pytest.mark.parametrize("value", (0, True, None))
def test_a_current_mode_of_the_wrong_type_is_refused(value):
    current = dict(_FULL_CURRENT, Mode=value)

    with pytest.raises(TypeError, match="audited app waveform name"):
        prepare_bar_control_command("linkage_role", "slave", current=current)


def test_an_unknown_current_mode_is_refused():
    current = dict(_FULL_CURRENT, Mode="feed")

    with pytest.raises(ValueError, match="not an audited app waveform"):
        prepare_bar_control_command("linkage_role", "slave", current=current)


@pytest.mark.parametrize("value", (0, 1, None, "true"))
def test_a_current_pulse_of_the_wrong_type_is_refused(value):
    current = dict(_FULL_CURRENT, PulseTide=value)

    with pytest.raises(TypeError, match="must be true or false"):
        prepare_bar_control_command("linkage_role", "master", current=current)


@pytest.mark.parametrize("current", (None, (), "Flow=60", 60))
def test_a_current_state_that_is_not_a_mapping_is_refused(current):
    with pytest.raises(TypeError, match="must be a mapping"):
        prepare_bar_control_command("manual_mode", "constant", current=current)
