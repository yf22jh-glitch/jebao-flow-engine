"""Pure DC Pump timer command planning; no transport, no device authority.

The active DC template ``bc20fe3b…`` exposes the timer on two different screens and they
write ``SwitchON`` in opposite directions:

* the Schedule screen switch sends ``{TimerON: !current, SwitchON: !current}`` — power
  follows the new timer state, so turning the timer off also stops the pump;
* the ManualMode screen timer sends ``{TimerON: !current, SwitchON: current}`` — power is
  the inverse of the new timer state, so turning the timer off leaves the pump running.

The app reaches both payloads only by toggling, so the emitted pair is fully determined by the
desired timer state. This module therefore plans from the desired state alone and never reads a
current value. Callers that must avoid a redundant physical write compare their owned fields and
take the existing no-change path; that decision is not made here.

``Auto*`` datapoints are never planned. The active template reads them for display and writes
none of them, so no seed belongs in a DC timer command. Restoring the operation's original
persistent fields is likewise the caller's contract, not this module's.

This is a static reading of the audited app template. It asserts nothing about what the firmware
does with either payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

_TIMER_FIELD = "TimerON"
_POWER_FIELD = "SwitchON"


class DcTimerScreen(Enum):
    """The audited app screen whose timer contract a command reproduces.

    Naming the screen is required because the two screens disagree about ``SwitchON``. A caller
    cannot select the coupling with a bare boolean and cannot get it by accident.
    """

    SCHEDULE = "schedule"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class DcTimerCommand:
    """One planned DC timer command and the power state its own payload requests."""

    screen: DcTimerScreen
    desired_timer_enabled: bool
    fields: Mapping[str, bool]
    power_after: bool


def prepare_dc_timer_command(
    *,
    screen: DcTimerScreen,
    desired_timer_enabled: bool,
) -> DcTimerCommand:
    """Plan the exact two-field payload the named app screen writes for this timer state.

    ``power_after`` repeats the planned ``SwitchON`` so a caller can refuse a screen whose
    coupling would stop the pump. Selecting :attr:`DcTimerScreen.SCHEDULE` with
    ``desired_timer_enabled=False`` plans ``SwitchON=False``; that is the audited app behaviour
    and is deliberately not silently rewritten here.
    """

    if not isinstance(screen, DcTimerScreen):
        raise TypeError("DC timer command requires a DcTimerScreen")
    if type(desired_timer_enabled) is not bool:
        raise TypeError("DC timer command requires a boolean desired state")

    if screen is DcTimerScreen.SCHEDULE:
        power = desired_timer_enabled
    else:
        power = not desired_timer_enabled

    fields = MappingProxyType({_TIMER_FIELD: desired_timer_enabled, _POWER_FIELD: power})
    return DcTimerCommand(
        screen=screen,
        desired_timer_enabled=desired_timer_enabled,
        fields=fields,
        power_after=power,
    )


__all__ = ["DcTimerCommand", "DcTimerScreen", "prepare_dc_timer_command"]
