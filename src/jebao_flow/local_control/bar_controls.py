"""Pure planning of the Bar wavemaker's app-shaped manual control commands.

The Bar app never writes ``Mode``, ``PulseTide`` or ``Linkage`` on their own. Every screen that
changes one of them sends a command carrying the other manual parameters with it. The mode/role
paths also force ``TimerON`` and ``Linkage``; all except independent write ``FeedSwitch`` too.
This module reproduces
those exact shapes so a caller cannot issue a single-field write the app has no equivalent for.

Evidence is the currently installed Bar React Native template ``index.js`` (sha256 prefix
``e609fa34``). Five distinct callbacks are reproduced here, and they are **not** interchangeable:

``switch_to_manual`` (byte 83155)
    ``{TimerON, Flow, Frequency, Mode, PulseTide, Linkage: independent}`` -- six fields, and
    notably no ``FeedSwitch``. ``PulseTide`` survives only while ``Mode`` is ``classic``.

``mode_select_onchange`` (byte 126739)
    ``{TimerON, FeedSwitch, Linkage: independent, Mode, PulseTide, Frequency, Flow}``. The same
    handler has a second branch that sets ``FeedSwitch`` and ``FeedTime`` instead of ``Mode``;
    that branch is feeding, not a waveform, and is deliberately not reachable from here -- the
    Bar's ``Mode`` datapoint is a two-bit enum with no feeding member.

``edit_link_mode`` (byte 84119)
    ``{Linkage: master, TimerON, FeedSwitch, Mode, Flow, Frequency, PulseTide}``. Here
    ``PulseTide`` is passed through unconditionally; the app applies no ``classic`` condition.
    The app reaches this callback from a mode picker that can also select feeding, which sends
    ``{Linkage, TimerON, FeedSwitch, FeedTime}`` instead. That feeding selection belongs to the
    ``feed_switch``/``feeding_duration`` functions, so the role change planned here always carries
    the current manual waveform parameters and never a feeding payload.

``edit_mode_save`` (byte 84523)
    ``{TimerON, Flow, Mode, PulseTide, Frequency, Linkage: slave, FeedSwitch}``. ``PulseTide`` is
    derived from ``Mode`` alone -- true when the mode is ``classic``, false otherwise -- so the
    current ``PulseTide`` is not read on this path.

    A **different** function with the same name lives at byte 127069 in the manual-setting screen
    and forwards its modal payload verbatim without touching ``Linkage``. That one is the
    ``manual_setting_modal_confirm`` shape below. The two must not be conflated.

``manual_setting_modal_confirm`` (byte 127069, modal at byte 76672)
    The modal's confirm callback switches on ``FeedSwitch ? 4 : Mode`` and emits
    ``{Flow, Frequency, PulseTide}`` for ``classic``, ``{Flow, Frequency}`` for ``sine``,
    ``{Flow}`` for ``random``/``constant`` and ``{FeedTime, FeedSwitch}`` for feeding. Only the
    ``classic`` shape carries ``PulseTide``, and it always carries ``Flow`` and ``Frequency`` with
    it. The modal labels ``PulseTide`` false as "Pulse" and true as "Cross-flow" (byte 76683). A
    second array at byte 126536 pairs those labels with the opposite values, but its identifier is
    never referenced anywhere and its labels skip the translation helper every rendered label goes
    through, so it is not the live mapping.

    Writing ``PulseTide`` while the current ``Mode`` is not ``classic`` is allowed here: the
    schema can encode the bit, but no app screen exposes ``PulseTide`` outside ``classic``, and
    what the device does with it in the other modes is not established by the app bytes. That is
    recorded as a limitation, not enforced as a precondition -- this planner adds no gate on the
    current mode.

Everything in this module is offline planning. It asserts nothing about what the firmware accepts,
sends nothing, and reads no device. Values are validated only for type, audited app enum
membership and the declared app integer ranges.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from jebao_flow.operator_capture.app_catalog import (
    REGISTERED_APP_PRODUCTS,
    AppFunction,
    declared_app_integer_range,
)
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRODUCT_KEY

_CLASSIC_MODE = "classic"
_INDEPENDENT = "independent"
_MASTER = "master"
_SLAVE = "slave"


@dataclass(frozen=True, slots=True)
class BarControlCommand:
    """One app-shaped Bar command: the fields it owns and the app callback it reproduces."""

    fields: Mapping[str, Any]
    action: str


def prepare_bar_control_command(
    function: str,
    value: object,
    *,
    current: Mapping[str, Any],
) -> BarControlCommand:
    """Plan the app command shape for one Bar control change.

    ``current`` is a decoded status mapping. Only the keys a given shape actually echoes are
    required, so a caller never has to supply fields the planned command does not own.
    """

    if not isinstance(current, Mapping):
        raise TypeError("current Bar state must be a mapping")
    if function == "manual_mode":
        return _plan_manual_mode(value, current)
    if function == "pulse_or_tide":
        return _plan_pulse_or_tide(value, current)
    if function == "linkage_role":
        return _plan_linkage_role(value, current)
    raise ValueError("function has no audited Bar app control shape")


def _plan_manual_mode(value: object, current: Mapping[str, Any]) -> BarControlCommand:
    desired = _require_choice("manual_mode", value)
    return _command(
        "mode_select_onchange",
        {
            "TimerON": False,
            "FeedSwitch": False,
            "Linkage": _INDEPENDENT,
            "Mode": desired,
            "PulseTide": False,
            "Frequency": _current_frequency(current),
            "Flow": _current_flow(current),
        },
    )


def _plan_pulse_or_tide(value: object, current: Mapping[str, Any]) -> BarControlCommand:
    desired = _require_bool("pulse_or_tide", value)
    return _command(
        "manual_setting_modal_confirm",
        {
            "Flow": _current_flow(current),
            "Frequency": _current_frequency(current),
            "PulseTide": desired,
        },
    )


def _plan_linkage_role(value: object, current: Mapping[str, Any]) -> BarControlCommand:
    role = _require_choice("linkage_role", value)
    flow = _current_flow(current)
    frequency = _current_frequency(current)
    mode = _current_mode(current)
    if role == _INDEPENDENT:
        surviving_pulse = _current_bool(current, "PulseTide") and mode == _CLASSIC_MODE
        return _command(
            "switch_to_manual",
            {
                "TimerON": False,
                "Flow": flow,
                "Frequency": frequency,
                "Mode": mode,
                "PulseTide": surviving_pulse,
                "Linkage": _INDEPENDENT,
            },
        )
    if role == _MASTER:
        return _command(
            "edit_link_mode",
            {
                "Linkage": _MASTER,
                "TimerON": False,
                "FeedSwitch": False,
                "Mode": mode,
                "Flow": flow,
                "Frequency": frequency,
                "PulseTide": _current_bool(current, "PulseTide"),
            },
        )
    return _command(
        "edit_mode_save",
        {
            "TimerON": False,
            "Flow": flow,
            "Mode": mode,
            # The app derives this from the mode alone and ignores the current PulseTide.
            "PulseTide": mode == _CLASSIC_MODE,
            "Frequency": frequency,
            "Linkage": _SLAVE,
            "FeedSwitch": False,
        },
    )


def _command(action: str, fields: dict[str, Any]) -> BarControlCommand:
    return BarControlCommand(fields=MappingProxyType(dict(fields)), action=action)


def _app_function(name: str) -> AppFunction:
    product = REGISTERED_APP_PRODUCTS[LOCAL_WAVEMAKER_PRODUCT_KEY]
    matches = tuple(function for function in product.functions if function.name == name)
    if len(matches) != 1:
        raise KeyError("function is not unique in the registered app catalog")
    return matches[0]


def _require_choice(function: str, value: object) -> str:
    choices = _app_function(function).choices
    if not isinstance(value, str):
        raise TypeError(f"Bar {function} requires one of {', '.join(choices)}")
    if value not in choices:
        raise ValueError(f"Bar {function} is not in the audited app choices")
    return value


def _require_bool(function: str, value: object) -> bool:
    if type(value) is not bool:
        raise TypeError(f"Bar {function} requires true or false")
    return value


def _current_value(current: Mapping[str, Any], name: str) -> Any:
    try:
        return current[name]
    except KeyError:
        raise ValueError(f"current Bar state is missing {name}") from None


def _current_bool(current: Mapping[str, Any], name: str) -> bool:
    value = _current_value(current, name)
    if type(value) is not bool:
        raise TypeError(f"current Bar {name} must be true or false")
    return value


def _current_mode(current: Mapping[str, Any]) -> str:
    value = _current_value(current, "Mode")
    choices = _app_function("manual_mode").choices
    if not isinstance(value, str):
        raise TypeError("current Bar Mode must be an audited app waveform name")
    if value not in choices:
        raise ValueError("current Bar Mode is not an audited app waveform")
    return value


def _current_integer(current: Mapping[str, Any], name: str, app_function: str) -> int:
    """Validate one copied current value.

    Every integer this planner emits is a copy of what the device already reports, copied the way
    the app copies it. The app's *input* minimum belongs to the screens where an operator picks a
    new value; the app does not re-apply it when echoing a current one, so neither does this. The
    declared app maximum and the datapoint's own floor of zero do apply, and the operational flow
    bounds stay with the writer that owns them.
    """

    value = _current_value(current, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"current Bar {name} must be an integer")
    _, maximum = declared_app_integer_range(LOCAL_WAVEMAKER_PRODUCT_KEY, app_function)
    if not 0 <= value <= maximum:
        raise ValueError(f"current Bar {name} {value} is outside the copyable range 0..{maximum}")
    return value


def _current_flow(current: Mapping[str, Any]) -> int:
    return _current_integer(current, "Flow", "manual_flow")


def _current_frequency(current: Mapping[str, Any]) -> int:
    return _current_integer(current, "Frequency", "manual_frequency")


__all__ = ["BarControlCommand", "prepare_bar_control_command"]
