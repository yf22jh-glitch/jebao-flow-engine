"""Pure planning of the Aquarium Pump's app-shaped control commands.

Evidence is two **cached** copies of the product's React Native template that are present on the
operator's machine, with sha256 prefixes ``c5f255be`` (the newer) and ``b937f098``. They are not
the active selected template: that locator is absent from the local cache, so nothing here is
claimed to be what the installed app is running today. The two copies differ only in their
transpilation target -- the newer one is ES5-transpiled, the older is the ES6 original -- and
carry the same twelve call sites and the same payloads, which is why a single planner can
describe both.

What the app writes, and what it never writes. ``Motor_Speed``, ``FeedSwitch``, ``FeedTime``,
``TimerON``, ``AutoMode``, ``AutoGears``, ``YMDData``/``HMSData`` and the ``AutoTime`` image all
have handlers. ``Mode`` and ``AutoFeedTime`` have none: their only occurrence in either copy is a
local default in the app's own mock state. ``Mode`` is deliberately absent from this module.

The handlers reproduced here:

``speed_slider_end``
    ``{Motor_Speed}``, and ``TimerON=false`` **only** when the timer is currently on. The app
    reads the live timer to decide, so a device with the timer off gets a single-field write.

``switch_feed_on`` / ``switch_feed_off``
    Feeding is asymmetric. Turning it on writes ``{FeedSwitch, TimerON, FeedTime}``; turning it
    off also writes ``Motor_Speed``, returning the pump to its manual output. Both frames copy
    the current feeding duration rather than choosing one.

``feed_time_confirm``
    ``{FeedTime, FeedSwitch}`` -- a new duration plus the current switch value.

``timer_on`` / ``timer_off``
    Turning the timer on writes ``{TimerON, FeedSwitch}`` and then seeds ``AutoMode`` and
    ``AutoGears`` from the schedule slot covering the caller's local minute. Turning it off
    writes ``{TimerON, Motor_Speed}``. The seed is **not** persistent state: it is excluded from
    ``persistent_readback`` so a caller never treats a seed as something to restore.

The schedule image. Slots are six bytes, ``[startHour, startMinute, endHour, endMinute, mode,
level]``, and the app's encoder forces ``level`` to zero whenever ``mode`` is not the auto mode.
The release parser skips only the all-``0xEE`` row; an all-zero row is a real row, exactly as on
the other products. Slot ``mode`` maps straight onto the ``AutoMode`` enum with no offset, which
is unlike the Bar's one-based slot mode.

Generated gap rows carry ``mode = -1`` and, unlike the Bar's, a synthetic ``level`` of 100. That
is why the output bound below applies only to real auto rows: a gap's invented 100 and a feeding
row's forced 0 are not outputs this pump would run, and bounding them would refuse ordinary
schedules.

This module plans only. It sends nothing, reads no device, and asserts nothing about what the
firmware accepts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.schedule import AQUARIUM_PUMP_PRODUCT_KEY

_SLOT_SIZE = 6
_SLOT_COUNT = 48
_IMAGE_SIZE = _SLOT_SIZE * _SLOT_COUNT
_UNUSED_ROW = bytes([0xEE]) * _SLOT_SIZE
_MINUTES_PER_DAY = 1440
# The AutoMode enum, which a slot's mode indexes directly.
_AUTO_MODES = ("stopped", "auto", "feed")
_AUTO_SLOT_MODE = 1
_GAP_MODE = -1
_GAP_LEVEL = 100


@dataclass(frozen=True, slots=True)
class AquariumControlCommand:
    """One app-shaped command: what it writes, what of that is persistent, and where it came from.

    ``persistent_readback`` omits the ``Auto*`` seed. A seed is derived from the schedule, not
    owned state, so it is never something to compare against a historical value or restore.
    """

    fields: Mapping[str, Any]
    persistent_readback: Mapping[str, Any]
    action: str
    selected_slot: int | None = None


@dataclass(frozen=True, slots=True)
class _Slot:
    start: int
    end: int
    mode: int
    level: int = 0
    index: int | None = None


def prepare_aquarium_control_command(
    function: str,
    value: object,
    current: Mapping[str, Any],
    *,
    schedule_image: bytes | None = None,
    local_minute: int | None = None,
    minimum_speed: int = 30,
    maximum_speed: int = 100,
) -> AquariumControlCommand:
    """Plan the app command shape for one Aquarium control change.

    Only the current keys a given shape actually echoes are required. ``schedule_image`` and
    ``local_minute`` are used solely to seed a timer that is being turned on, and are ignored by
    every other function.
    """

    if not isinstance(current, Mapping):
        raise TypeError("current Aquarium state must be a mapping")
    if function == "motor_speed":
        return _plan_motor_speed(value, current, minimum_speed, maximum_speed)
    if function == "feed_switch":
        return _plan_feed_switch(value, current)
    if function == "feeding_duration":
        return _plan_feeding_duration(value, current)
    if function == "schedule_enable":
        return _plan_schedule_enable(
            value,
            current,
            schedule_image=schedule_image,
            local_minute=local_minute,
            minimum_speed=minimum_speed,
            maximum_speed=maximum_speed,
        )
    raise ValueError("function has no audited Aquarium app control shape")


def prepare_aquarium_schedule_seed(
    schedule_image: bytes,
    *,
    local_minute: int,
    minimum_speed: int,
    maximum_speed: int,
) -> AquariumControlCommand:
    """Plan the separate seed the app sends after a schedule save while the timer is on.

    Raising is part of the contract: a caller may use this to check that every runnable auto row
    of an image stays inside the approved output bounds, and discard the command itself.
    """

    slots = _app_slots(schedule_image, minimum=minimum_speed, maximum=maximum_speed)
    selected = _selected_slot(slots, _require_local_minute(local_minute))
    if selected is None or selected.mode == _GAP_MODE:
        # The app's own no-slot and gap branches both send a stopped mode and nothing else.
        return _command({"AutoMode": _AUTO_MODES[0]}, {}, "schedule_seed", None)
    return _command(
        {"AutoMode": _AUTO_MODES[selected.mode], "AutoGears": selected.level},
        {},
        "schedule_seed",
        selected.index,
    )


def _plan_motor_speed(
    value: object,
    current: Mapping[str, Any],
    minimum_speed: int,
    maximum_speed: int,
) -> AquariumControlCommand:
    speed = _new_integer(value, "motor_speed")
    minimum, maximum = _require_bounds(minimum_speed, maximum_speed)
    if not minimum <= speed <= maximum:
        raise ValueError(
            f"Aquarium motor speed {speed} is outside the approved bounds {minimum}..{maximum}"
        )
    fields: dict[str, Any] = {"Motor_Speed": speed}
    if _current_bool(current, "TimerON"):
        fields["TimerON"] = False
    return _command(fields, fields, "speed_slider_end")


def _plan_feed_switch(value: object, current: Mapping[str, Any]) -> AquariumControlCommand:
    desired = _require_bool("feed_switch", value)
    fields: dict[str, Any] = {
        "FeedSwitch": desired,
        "TimerON": False,
        "FeedTime": _copied_integer(current, "FeedTime", "feeding_duration"),
    }
    if not desired:
        # Only the off direction returns the pump to its manual output.
        fields["Motor_Speed"] = _copied_integer(current, "Motor_Speed", "motor_speed")
    return _command(fields, fields, "switch_feed_on" if desired else "switch_feed_off")


def _plan_feeding_duration(value: object, current: Mapping[str, Any]) -> AquariumControlCommand:
    fields = {
        "FeedTime": _new_integer(value, "feeding_duration"),
        "FeedSwitch": _current_bool(current, "FeedSwitch"),
    }
    return _command(fields, fields, "feed_time_confirm")


def _plan_schedule_enable(
    value: object,
    current: Mapping[str, Any],
    *,
    schedule_image: bytes | None,
    local_minute: int | None,
    minimum_speed: int,
    maximum_speed: int,
) -> AquariumControlCommand:
    desired = _require_bool("schedule_enable", value)
    if not desired:
        fields = {
            "TimerON": False,
            "Motor_Speed": _copied_integer(current, "Motor_Speed", "motor_speed"),
        }
        return _command(fields, fields, "timer_off")
    if schedule_image is None or local_minute is None:
        raise ValueError("Aquarium timer ON requires the stored schedule and a local minute")
    persistent = {"TimerON": True, "FeedSwitch": False}
    seed = prepare_aquarium_schedule_seed(
        schedule_image,
        local_minute=local_minute,
        minimum_speed=minimum_speed,
        maximum_speed=maximum_speed,
    )
    return _command(
        {**persistent, **seed.fields},
        persistent,
        "timer_on",
        seed.selected_slot,
    )


def _app_slots(image: object, *, minimum: int, maximum: int) -> list[_Slot]:
    """Port the cached template's AutoTime parser and gap fill.

    The release branch excludes only the ``ee`` row, never the all-zero row, so a zeroed slot is
    a real stopped row covering 00:00..00:00. Gap rows are appended afterwards and can win the
    selection below, exactly as in the app.
    """

    if not isinstance(image, (bytes, bytearray)) or len(image) != _IMAGE_SIZE:
        raise ValueError("Aquarium schedule requires the complete 288-byte image")
    minimum, maximum = _require_bounds(minimum, maximum)
    slots: list[_Slot] = []
    for index in range(_SLOT_COUNT):
        row = bytes(image[index * _SLOT_SIZE : (index + 1) * _SLOT_SIZE])
        if row == _UNUSED_ROW:
            continue
        start_hour, start_minute, end_hour, end_minute, mode, level = row
        if not (
            start_hour < 24
            and start_minute < 60
            and end_hour <= 24
            and end_minute < 60
            and (end_hour != 24 or end_minute == 0)
        ):
            raise ValueError(f"Aquarium slot {index} has invalid wall-clock syntax")
        if mode >= len(_AUTO_MODES):
            raise ValueError(f"Aquarium slot {index} has a mode outside the app's own list")
        _, level_maximum = declared_app_integer_range(AQUARIUM_PUMP_PRODUCT_KEY, "motor_speed")
        if level > level_maximum:
            raise ValueError(f"Aquarium slot {index} gear {level} is outside the app range")
        # Only a running auto row is an output this pump would drive. The app's encoder forces
        # every other mode's level to zero, and a generated gap row carries an invented 100, so
        # bounding those would refuse ordinary schedules rather than protect anything.
        if mode == _AUTO_SLOT_MODE and not minimum <= level <= maximum:
            raise ValueError(f"Aquarium slot {index} gear {level} exceeds {minimum}..{maximum}")
        slots.append(
            _Slot(
                start_hour * 60 + start_minute,
                end_hour * 60 + end_minute,
                mode,
                level,
                index,
            )
        )

    # The app sorts a separate interval list, collects endpoints in insertion order, and fills
    # pairs which are not an exact real interval. Sorting or merging those endpoints ourselves
    # would silently change its overlap and gap behaviour.
    intervals = sorted(((slot.start, slot.end) for slot in slots), key=lambda pair: pair[0])
    points = list(dict.fromkeys(value for interval in intervals for value in interval))
    gaps = []
    for index, point in enumerate(points):
        if index == len(points) - 1:
            first = points[0]
            if (point != _MINUTES_PER_DAY or first != 0) and point != first:
                gaps.append((0 if point == _MINUTES_PER_DAY else point, first))
        else:
            following = points[index + 1]
            if point != following:
                gaps.append((point, following))
    slots.extend(
        _Slot(start, end, _GAP_MODE, _GAP_LEVEL)
        for start, end in gaps
        if (start, end) not in intervals
    )
    return slots


def _selected_slot(slots: list[_Slot], local_minute: int) -> _Slot | None:
    """Pick the row covering this minute, keeping the app's last-match-wins assignment."""

    selected: _Slot | None = None
    for slot in slots:
        # The app reads (endHour || 24), so a 00:mm end becomes 24:mm. Preserve that, including
        # the inclusive end boundary, rather than inferring what the firmware does.
        end = slot.end if slot.end >= 60 else slot.end + _MINUTES_PER_DAY
        matches = (
            slot.start <= local_minute <= end
            if slot.start < end
            else local_minute <= end or local_minute >= slot.start
        )
        if matches:
            selected = slot
    return selected


def _command(
    fields: Mapping[str, Any],
    persistent: Mapping[str, Any],
    action: str,
    selected_slot: int | None = None,
) -> AquariumControlCommand:
    return AquariumControlCommand(
        fields=MappingProxyType(dict(fields)),
        persistent_readback=MappingProxyType(dict(persistent)),
        action=action,
        selected_slot=selected_slot,
    )


def _require_bool(function: str, value: object) -> bool:
    if type(value) is not bool:
        raise TypeError(f"Aquarium {function} requires true or false")
    return value


def _require_bounds(minimum: int, maximum: int) -> tuple[int, int]:
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
    ):
        raise TypeError("Aquarium output bounds must be integers")
    app_minimum, app_maximum = declared_app_integer_range(AQUARIUM_PUMP_PRODUCT_KEY, "motor_speed")
    if not app_minimum <= minimum <= maximum <= app_maximum:
        raise ValueError(
            f"Aquarium output bounds must stay within the app range {app_minimum}..{app_maximum}"
        )
    return minimum, maximum


def _require_local_minute(local_minute: object) -> int:
    if isinstance(local_minute, bool) or not isinstance(local_minute, int):
        raise TypeError("Aquarium schedule seed requires an integer local minute")
    if not 0 <= local_minute < _MINUTES_PER_DAY:
        raise ValueError("Aquarium schedule seed requires a local minute within the day")
    return local_minute


def _new_integer(value: object, app_function: str) -> int:
    """Validate a value the caller is choosing, against the app's own input range."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Aquarium {app_function} requires an integer")
    minimum, maximum = declared_app_integer_range(AQUARIUM_PUMP_PRODUCT_KEY, app_function)
    if not minimum <= value <= maximum:
        raise ValueError(
            f"Aquarium {app_function} {value} is outside the declared app range "
            f"{minimum}..{maximum}"
        )
    return value


def _copied_integer(current: Mapping[str, Any], name: str, app_function: str) -> int:
    """Validate a value the app copies from what the device already reports.

    The app's picker minimum belongs to the screen where an operator chooses a new value; the app
    does not re-apply it when echoing a current one, so neither does this. A device reporting a
    feeding duration of zero is copied as zero rather than normalised to the picker's one.
    """

    value = _current_value(current, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"current Aquarium {name} must be an integer")
    _, maximum = declared_app_integer_range(AQUARIUM_PUMP_PRODUCT_KEY, app_function)
    if not 0 <= value <= maximum:
        raise ValueError(
            f"current Aquarium {name} {value} is outside the copyable range 0..{maximum}"
        )
    return value


def _current_value(current: Mapping[str, Any], name: str) -> Any:
    try:
        return current[name]
    except KeyError:
        raise ValueError(f"current Aquarium state is missing {name}") from None


def _current_bool(current: Mapping[str, Any], name: str) -> bool:
    value = _current_value(current, name)
    if type(value) is not bool:
        raise TypeError(f"current Aquarium {name} must be true or false")
    return value


__all__ = [
    "AquariumControlCommand",
    "prepare_aquarium_control_command",
    "prepare_aquarium_schedule_seed",
]
