"""Offline tests for the Aquarium Pump's app-shaped control planner.

These pin the payloads two cached copies of the product template agree on, and the schedule
parsing the app's own seed depends on. They assert the planned command only; nothing here claims
the firmware accepts or applies anything, and the cached copies are not the active template.
"""

from __future__ import annotations

import dataclasses

import pytest

from jebao_flow.local_control.aquarium_controls import (
    AquariumControlCommand,
    prepare_aquarium_control_command,
    prepare_aquarium_schedule_seed,
)

SPEED = 40
FEED_TIME = 5
EE = bytes([0xEE]) * 6

_CURRENT = {
    "TimerON": False,
    "FeedSwitch": False,
    "FeedTime": FEED_TIME,
    "Motor_Speed": SPEED,
    "Mode": False,
    "AutoMode": "stopped",
    "AutoGears": 0,
}


def _slot(start_hour, start_minute, end_hour, end_minute, mode, level) -> bytes:
    return bytes([start_hour, start_minute, end_hour, end_minute, mode, level])


def _image(*rows: bytes) -> bytes:
    used = b"".join(rows)
    return used + EE * (48 - len(rows))


ALL_DAY_AUTO = _image(_slot(0, 0, 24, 0, 1, SPEED))


def _command(function, value, current=None, **kwargs) -> AquariumControlCommand:
    return prepare_aquarium_control_command(
        function, value, dict(_CURRENT if current is None else current), **kwargs
    )


@pytest.mark.parametrize(
    ("function", "value", "current", "action", "expected"),
    (
        (
            "motor_speed",
            35,
            {"TimerON": False},
            "speed_slider_end",
            {"Motor_Speed": 35},
        ),
        (
            "motor_speed",
            35,
            {"TimerON": True},
            "speed_slider_end",
            {"Motor_Speed": 35, "TimerON": False},
        ),
        (
            "feed_switch",
            True,
            {"FeedTime": FEED_TIME},
            "switch_feed_on",
            {"FeedSwitch": True, "TimerON": False, "FeedTime": FEED_TIME},
        ),
        (
            "feed_switch",
            False,
            {"FeedTime": FEED_TIME, "Motor_Speed": SPEED},
            "switch_feed_off",
            {
                "FeedSwitch": False,
                "TimerON": False,
                "FeedTime": FEED_TIME,
                "Motor_Speed": SPEED,
            },
        ),
        (
            "feeding_duration",
            7,
            {"FeedSwitch": True},
            "feed_time_confirm",
            {"FeedTime": 7, "FeedSwitch": True},
        ),
        (
            "schedule_enable",
            False,
            {"Motor_Speed": SPEED},
            "timer_off",
            {"TimerON": False, "Motor_Speed": SPEED},
        ),
    ),
)
def test_planned_shapes_match_the_cached_handlers(function, value, current, action, expected):
    command = _command(function, value, current)

    assert command.action == action
    assert dict(command.fields) == expected
    assert dict(command.persistent_readback) == expected
    assert command.selected_slot is None


def test_only_the_echoed_current_keys_are_required():
    """Feeding never needs the speed unless the command actually returns the pump to it."""

    assert _command("feed_switch", True, {"FeedTime": FEED_TIME}).fields
    with pytest.raises(ValueError, match="missing Motor_Speed"):
        _command("feed_switch", False, {"FeedTime": FEED_TIME})
    with pytest.raises(ValueError, match="missing TimerON"):
        _command("motor_speed", 35, {})
    with pytest.raises(ValueError, match="missing FeedSwitch"):
        _command("feeding_duration", 7, {})


def test_turning_the_timer_on_seeds_from_the_schedule_and_excludes_it_from_persistence():
    command = _command(
        "schedule_enable",
        True,
        {},
        schedule_image=ALL_DAY_AUTO,
        local_minute=600,
        minimum_speed=30,
        maximum_speed=45,
    )

    assert command.action == "timer_on"
    assert dict(command.fields) == {
        "TimerON": True,
        "FeedSwitch": False,
        "AutoMode": "auto",
        "AutoGears": SPEED,
    }
    # The seed is derived state, so it is not something a caller may restore or compare.
    assert dict(command.persistent_readback) == {"TimerON": True, "FeedSwitch": False}
    assert command.selected_slot == 0


def test_turning_the_timer_on_without_a_schedule_or_a_minute_is_refused():
    with pytest.raises(ValueError, match="requires the stored schedule"):
        _command("schedule_enable", True, {}, schedule_image=ALL_DAY_AUTO)
    with pytest.raises(ValueError, match="requires the stored schedule"):
        _command("schedule_enable", True, {}, local_minute=600)


def test_a_seed_carries_no_persistent_state():
    seed = prepare_aquarium_schedule_seed(
        ALL_DAY_AUTO, local_minute=0, minimum_speed=30, maximum_speed=45
    )

    assert seed.action == "schedule_seed"
    assert dict(seed.fields) == {"AutoMode": "auto", "AutoGears": SPEED}
    assert dict(seed.persistent_readback) == {}


@pytest.mark.parametrize("mode,name", ((0, "stopped"), (1, "auto"), (2, "feed")))
def test_slot_mode_maps_onto_the_auto_mode_enum_without_an_offset(mode, name):
    """Unlike the Bar, this product's slot mode is already the AutoMode value."""

    level = SPEED if mode == 1 else 0
    seed = prepare_aquarium_schedule_seed(
        _image(_slot(0, 0, 24, 0, mode, level)),
        local_minute=600,
        minimum_speed=30,
        maximum_speed=45,
    )

    assert seed.fields["AutoMode"] == name
    assert seed.fields["AutoGears"] == level
    assert seed.selected_slot == 0


def test_an_all_zero_row_is_a_real_stopped_row_not_an_unused_one():
    """The release parser skips only the ee row, so a zeroed slot still covers the day."""

    seed = prepare_aquarium_schedule_seed(
        _image(bytes(6)), local_minute=600, minimum_speed=30, maximum_speed=45
    )

    assert dict(seed.fields) == {"AutoMode": "stopped", "AutoGears": 0}
    # A real row, so the slot is named; the no-slot branch would report none.
    assert seed.selected_slot == 0


def test_an_entirely_unused_image_reports_no_slot_at_all():
    seed = prepare_aquarium_schedule_seed(
        EE * 48, local_minute=600, minimum_speed=30, maximum_speed=45
    )

    assert dict(seed.fields) == {"AutoMode": "stopped"}
    assert seed.selected_slot is None


def test_a_midnight_end_covers_the_whole_day():
    """The app reads (endHour || 24), so an end of 00:00 is 24:00."""

    for minute in (0, 1, 719, 1439):
        seed = prepare_aquarium_schedule_seed(
            _image(_slot(0, 0, 0, 0, 1, SPEED)),
            local_minute=minute,
            minimum_speed=30,
            maximum_speed=45,
        )
        assert seed.fields["AutoGears"] == SPEED


def test_the_last_overlapping_row_wins_the_selection():
    image = _image(_slot(0, 0, 24, 0, 1, 40), _slot(0, 0, 24, 0, 1, 35))

    seed = prepare_aquarium_schedule_seed(
        image, local_minute=600, minimum_speed=30, maximum_speed=45
    )

    assert seed.fields["AutoGears"] == 35
    assert seed.selected_slot == 1


def test_a_generated_gap_wins_its_own_hours_and_never_leaks_its_invented_level():
    """A gap carries a synthetic level of 100, which must never reach the device or the bounds."""

    image = _image(_slot(0, 0, 6, 0, 1, 40))

    covered = prepare_aquarium_schedule_seed(
        image, local_minute=180, minimum_speed=30, maximum_speed=45
    )
    gapped = prepare_aquarium_schedule_seed(
        image, local_minute=600, minimum_speed=30, maximum_speed=45
    )

    assert dict(covered.fields) == {"AutoMode": "auto", "AutoGears": 40}
    assert covered.selected_slot == 0
    assert dict(gapped.fields) == {"AutoMode": "stopped"}
    assert gapped.selected_slot is None


def test_a_feeding_row_keeps_its_forced_zero_gear_without_tripping_the_bounds():
    seed = prepare_aquarium_schedule_seed(
        _image(_slot(0, 0, 24, 0, 2, 0)),
        local_minute=600,
        minimum_speed=30,
        maximum_speed=45,
    )

    assert dict(seed.fields) == {"AutoMode": "feed", "AutoGears": 0}


def test_a_running_auto_row_outside_the_bounds_is_refused():
    image = _image(_slot(0, 0, 12, 0, 1, 40), _slot(12, 0, 24, 0, 1, 90))

    with pytest.raises(ValueError, match="exceeds 30..45"):
        prepare_aquarium_schedule_seed(image, local_minute=60, minimum_speed=30, maximum_speed=45)


@pytest.mark.parametrize(
    "row",
    (
        _slot(24, 0, 24, 0, 1, 40),
        _slot(0, 60, 24, 0, 1, 40),
        _slot(0, 0, 25, 0, 1, 40),
        _slot(0, 0, 24, 30, 1, 40),
        _slot(0, 0, 12, 60, 1, 40),
    ),
)
def test_a_row_with_invalid_wall_clock_syntax_is_refused(row):
    with pytest.raises(ValueError, match="invalid wall-clock syntax"):
        prepare_aquarium_schedule_seed(
            _image(row), local_minute=600, minimum_speed=30, maximum_speed=45
        )


def test_a_row_with_a_mode_the_app_does_not_list_is_refused():
    with pytest.raises(ValueError, match="mode outside the app's own list"):
        prepare_aquarium_schedule_seed(
            _image(_slot(0, 0, 24, 0, 3, 40)),
            local_minute=600,
            minimum_speed=30,
            maximum_speed=45,
        )


@pytest.mark.parametrize("image", (b"", EE * 47, EE * 49, "not bytes", None))
def test_an_image_that_is_not_the_complete_schedule_is_refused(image):
    with pytest.raises(ValueError, match="complete 288-byte image"):
        prepare_aquarium_schedule_seed(image, local_minute=600, minimum_speed=30, maximum_speed=45)


def test_a_copied_feeding_duration_of_zero_is_carried_but_a_new_one_is_refused():
    """Zero is what the device reported; the picker minimum belongs to new values only."""

    carried = _command("feed_switch", True, {"FeedTime": 0})

    assert carried.fields["FeedTime"] == 0
    with pytest.raises(ValueError, match="declared app range 1..60"):
        _command("feeding_duration", 0, {"FeedSwitch": False})


def test_a_copied_speed_below_the_picker_minimum_is_carried_but_a_new_one_is_refused():
    carried = _command("feed_switch", False, {"FeedTime": FEED_TIME, "Motor_Speed": 10})

    assert carried.fields["Motor_Speed"] == 10
    with pytest.raises(ValueError, match="declared app range 30..100"):
        _command("motor_speed", 29, {"TimerON": False})


@pytest.mark.parametrize("value", (101, -1))
def test_a_copied_value_outside_the_copyable_range_is_refused(value):
    with pytest.raises(ValueError, match="copyable range"):
        _command("feed_switch", False, {"FeedTime": FEED_TIME, "Motor_Speed": value})


def test_a_new_speed_outside_the_caller_bounds_is_refused():
    with pytest.raises(ValueError, match="approved bounds 30..45"):
        _command("motor_speed", 50, {"TimerON": False}, minimum_speed=30, maximum_speed=45)


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    ((29, 45), (30, 101), (45, 30), (30, True)),
)
def test_output_bounds_outside_the_app_range_are_refused(minimum, maximum):
    with pytest.raises((ValueError, TypeError)):
        _command(
            "motor_speed",
            35,
            {"TimerON": False},
            minimum_speed=minimum,
            maximum_speed=maximum,
        )


@pytest.mark.parametrize("minute", (-1, 1440, True, "600", None))
def test_a_local_minute_outside_the_day_is_refused(minute):
    with pytest.raises((ValueError, TypeError)):
        prepare_aquarium_schedule_seed(
            ALL_DAY_AUTO, local_minute=minute, minimum_speed=30, maximum_speed=45
        )


@pytest.mark.parametrize("function", ("feed_switch", "schedule_enable"))
@pytest.mark.parametrize("value", (0, 1, "true", None))
def test_boolean_controls_refuse_anything_that_is_not_a_bool(function, value):
    with pytest.raises(TypeError, match="true or false"):
        _command(function, value, {"FeedTime": FEED_TIME, "Motor_Speed": SPEED})


@pytest.mark.parametrize("function", ("motor_speed", "feeding_duration"))
@pytest.mark.parametrize("value", (True, 35.0, "35", None))
def test_integer_controls_refuse_anything_that_is_not_an_integer(function, value):
    with pytest.raises(TypeError, match="requires an integer"):
        _command(function, value, {"TimerON": False, "FeedSwitch": False})


def test_an_unaudited_function_is_refused():
    with pytest.raises(ValueError, match="no audited Aquarium app control shape"):
        _command("transport_mode", "wireless")


@pytest.mark.parametrize("current", (None, (), "TimerON", 5))
def test_a_current_state_that_is_not_a_mapping_is_refused(current):
    with pytest.raises(TypeError, match="must be a mapping"):
        prepare_aquarium_control_command("motor_speed", 35, current)


def test_the_transport_mode_datapoint_is_never_written():
    """The cached copies have no handler for it, so this planner must not invent one."""

    commands = [
        _command("motor_speed", 35, {"TimerON": True}),
        _command("feed_switch", True, {"FeedTime": FEED_TIME}),
        _command("feed_switch", False, {"FeedTime": FEED_TIME, "Motor_Speed": SPEED}),
        _command("feeding_duration", 7, {"FeedSwitch": False}),
        _command("schedule_enable", False, {"Motor_Speed": SPEED}),
        _command("schedule_enable", True, {}, schedule_image=ALL_DAY_AUTO, local_minute=600),
    ]

    for command in commands:
        assert "Mode" not in command.fields
        assert "AutoFeedTime" not in command.fields


def test_planned_mappings_are_immutable():
    command = _command("schedule_enable", True, {}, schedule_image=ALL_DAY_AUTO, local_minute=0)

    with pytest.raises(TypeError):
        command.fields["Motor_Speed"] = 31  # type: ignore[index]
    with pytest.raises(TypeError):
        command.persistent_readback["TimerON"] = False  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.action = "other"  # type: ignore[misc]
