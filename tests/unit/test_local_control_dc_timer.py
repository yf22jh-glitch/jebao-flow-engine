"""Offline tests for the DC Pump timer command planner.

These pin the two audited app screens' opposite ``SwitchON`` coupling. They assert the planned
payload only; nothing here claims the firmware accepts or applies either command.
"""

from __future__ import annotations

import dataclasses

import pytest

from jebao_flow.local_control.dc_timer import (
    DcTimerCommand,
    DcTimerScreen,
    prepare_dc_timer_command,
)


@pytest.mark.parametrize(
    ("screen", "desired", "expected"),
    (
        (DcTimerScreen.SCHEDULE, True, {"TimerON": True, "SwitchON": True}),
        (DcTimerScreen.SCHEDULE, False, {"TimerON": False, "SwitchON": False}),
        (DcTimerScreen.MANUAL, True, {"TimerON": True, "SwitchON": False}),
        (DcTimerScreen.MANUAL, False, {"TimerON": False, "SwitchON": True}),
    ),
)
def test_each_screen_plans_its_own_audited_payload(screen, desired, expected) -> None:
    command = prepare_dc_timer_command(screen=screen, desired_timer_enabled=desired)
    assert dict(command.fields) == expected
    assert command.screen is screen
    assert command.desired_timer_enabled is desired


def test_the_two_screens_agree_on_the_timer_and_disagree_only_on_power() -> None:
    for desired in (True, False):
        schedule = prepare_dc_timer_command(
            screen=DcTimerScreen.SCHEDULE, desired_timer_enabled=desired
        )
        manual = prepare_dc_timer_command(
            screen=DcTimerScreen.MANUAL, desired_timer_enabled=desired
        )
        assert schedule.fields["TimerON"] == manual.fields["TimerON"] == desired
        assert schedule.fields["SwitchON"] is not manual.fields["SwitchON"]


def test_turning_the_timer_off_stops_the_pump_only_on_the_schedule_screen() -> None:
    """The safety-relevant asymmetry: only one screen's OFF keeps the pump running."""

    schedule_off = prepare_dc_timer_command(
        screen=DcTimerScreen.SCHEDULE, desired_timer_enabled=False
    )
    manual_off = prepare_dc_timer_command(screen=DcTimerScreen.MANUAL, desired_timer_enabled=False)
    assert schedule_off.power_after is False
    assert manual_off.power_after is True


@pytest.mark.parametrize("screen", tuple(DcTimerScreen))
@pytest.mark.parametrize("desired", (True, False))
def test_power_after_always_repeats_the_planned_power_field(screen, desired) -> None:
    command = prepare_dc_timer_command(screen=screen, desired_timer_enabled=desired)
    assert command.power_after is command.fields["SwitchON"]


@pytest.mark.parametrize("screen", tuple(DcTimerScreen))
@pytest.mark.parametrize("desired", (True, False))
def test_only_the_two_audited_fields_are_planned_and_never_an_auto_seed(screen, desired) -> None:
    command = prepare_dc_timer_command(screen=screen, desired_timer_enabled=desired)
    assert set(command.fields) == {"TimerON", "SwitchON"}
    assert not [name for name in command.fields if name.startswith("Auto")]
    assert all(type(value) is bool for value in command.fields.values())


def test_planned_fields_cannot_be_mutated_by_a_caller() -> None:
    command = prepare_dc_timer_command(screen=DcTimerScreen.MANUAL, desired_timer_enabled=False)
    with pytest.raises(TypeError):
        command.fields["SwitchON"] = False  # type: ignore[index]
    with pytest.raises(TypeError):
        command.fields["AutoFlow"] = 100  # type: ignore[index]
    assert dict(command.fields) == {"TimerON": False, "SwitchON": True}


def test_the_command_itself_is_immutable() -> None:
    command = prepare_dc_timer_command(screen=DcTimerScreen.SCHEDULE, desired_timer_enabled=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.power_after = False  # type: ignore[misc]


@pytest.mark.parametrize("desired", (1, 0, None, "true", 1.0))
def test_a_non_boolean_desired_state_is_refused(desired) -> None:
    with pytest.raises(TypeError, match="boolean desired state"):
        prepare_dc_timer_command(screen=DcTimerScreen.MANUAL, desired_timer_enabled=desired)


@pytest.mark.parametrize("screen", ("manual", "schedule", None, 0))
def test_a_screen_must_be_named_by_its_enum(screen) -> None:
    with pytest.raises(TypeError, match="DcTimerScreen"):
        prepare_dc_timer_command(screen=screen, desired_timer_enabled=True)


def test_the_planner_takes_no_current_state_argument() -> None:
    """Desired-state planning: the payload is determined without reading a changing device value."""

    import inspect

    parameters = inspect.signature(prepare_dc_timer_command).parameters
    assert set(parameters) == {"screen", "desired_timer_enabled"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())


def test_repeated_planning_is_stable() -> None:
    first = prepare_dc_timer_command(screen=DcTimerScreen.MANUAL, desired_timer_enabled=True)
    second = prepare_dc_timer_command(screen=DcTimerScreen.MANUAL, desired_timer_enabled=True)
    assert isinstance(first, DcTimerCommand)
    assert dict(first.fields) == dict(second.fields)
    assert first.power_after is second.power_after
