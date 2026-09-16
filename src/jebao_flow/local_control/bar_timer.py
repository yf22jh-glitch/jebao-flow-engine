"""Pure Bar app timer command planning; no transport or hardware authority.

The active app template e609fa34...6329 supplies switchToProgram/switchToManual.
The caller supplies a fresh schedule and its configured local minute (not device Auto* history).
Auto* values seed the app command, but are never persistent readback expectations.
This module does not authorize generic recovery or expose a qualified runtime control.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_MODES = ("stopped", "classic", "sine", "random", "constant", "feed")


@dataclass(frozen=True, slots=True)
class BarTimerCommand:
    fields: Mapping[str, Any]
    persistent_readback: Mapping[str, Any]
    selected_slot: int | None


@dataclass(frozen=True, slots=True)
class _Slot:
    start: int
    end: int
    mode: int
    flow: int = 0
    frequency: int = 0
    pulse_tide: int = 0
    index: int | None = None


def _app_slots(image: bytes, *, minimum: int, maximum: int) -> list[_Slot]:
    """Port the release template's At -> _t, including generated gap rows.

    The release branch excludes ee rows, NOT all-zero rows. Do not substitute the
    generic display decoder, which intentionally treats both as unused. Gap rows
    are appended and can win over real rows, just as in the audited app.
    """
    if len(image) != 384:
        raise ValueError("Bar timer requires the complete 384-byte schedule")
    slots = []
    for index in range(48):
        row = image[index * 8 : (index + 1) * 8]
        if row == b"\xee" * 8:
            continue
        sh, sm, eh, em, mode, flow, frequency, pulse = row
        if not (sh < 24 and sm < 60 and eh <= 24 and em < 60 and (eh != 24 or em == 0)):
            raise ValueError(f"Bar timer slot {index} has invalid wall-clock syntax")
        if mode >= len(_MODES) or pulse not in (0, 1) or frequency > 100:
            raise ValueError(f"Bar timer slot {index} has invalid app parameters")
        # Check every runnable slot, not just today's seed, so a later boundary cannot
        # introduce a larger output. Feeding stores minutes, not pump output, in flow.
        if mode in (1, 2, 3, 4) and not minimum <= flow <= maximum:
            raise ValueError(f"Bar timer slot {index} flow {flow} exceeds {minimum}..{maximum}")
        if mode == 5 and not 1 <= flow <= 60:
            raise ValueError(f"Bar timer slot {index} feeding duration is outside 1..60")
        slots.append(_Slot(sh * 60 + sm, eh * 60 + em, mode, flow, frequency, pulse, index))

    # The app sorts a separate interval list, collects endpoints in insertion order,
    # and fills pairs which are not an exact real interval. Sorting or merging those
    # endpoints ourselves would silently change its overlap/gap behavior.
    intervals = sorted(((s.start, s.end) for s in slots), key=lambda pair: pair[0])
    points = list(dict.fromkeys(value for interval in intervals for value in interval))
    gaps = []
    for index, point in enumerate(points):
        if index == len(points) - 1:
            first = points[0]
            if (point != 1440 or first != 0) and point != first:
                gaps.append((0 if point == 1440 else point, first))
        else:
            following = points[index + 1]
            if point != following:
                gaps.append((point, following))
    slots.extend(_Slot(start, end, -1) for start, end in gaps if (start, end) not in intervals)
    return slots


def prepare_bar_timer_command(
    enabled: bool,
    current: Mapping[str, Any],
    *,
    schedule_image: bytes,
    local_minute: int,
    minimum_flow: int,
    maximum_flow: int,
) -> BarTimerCommand:
    """Build the standalone app command, with caller-owned time and output limits.

    No SwitchON field is written. ON uses the program tab (Linkage=independent),
    not the linked-master editor's onProgramSave. OFF copies fresh manual settings.
    No rollback payload is invented here: runtime integration must own that contract.
    """
    if type(enabled) is not bool:
        raise TypeError("Bar timer enable requires a boolean")
    if type(local_minute) is not int or not 0 <= local_minute < 1440:
        raise ValueError("Bar timer requires a configured local minute within the day")
    if (
        type(minimum_flow) is not int
        or type(maximum_flow) is not int
        or not 30 <= minimum_flow <= maximum_flow <= 100
    ):
        raise ValueError("Bar timer output bounds must stay within the app range 30..100")
    if not enabled:
        flow = current["Flow"]
        if type(flow) is not int or not minimum_flow <= flow <= maximum_flow:
            raise ValueError("Bar timer OFF manual flow is outside the approved output bounds")
        fields = {
            "TimerON": False,
            "Flow": flow,
            "Frequency": current["Frequency"],
            "Mode": current["Mode"],
            "PulseTide": current["Mode"] == "classic" and current["PulseTide"],
            "Linkage": "independent",
        }
        return BarTimerCommand(fields, dict(fields), None)

    selected = None
    for slot in _app_slots(schedule_image, minimum=minimum_flow, maximum=maximum_flow):
        # It() uses (endHour || 24), so 00:mm becomes 24:mm; preserve the actual app
        # rule, including its inclusive end boundary, rather than inferring firmware.
        end = slot.end if slot.end >= 60 else slot.end + 1440
        matches = (
            slot.start <= local_minute <= end
            if slot.start < end
            else local_minute <= end or local_minute >= slot.start
        )
        if matches:
            selected = slot
    persistent = {"TimerON": True, "FeedSwitch": False, "Linkage": "independent"}
    fields = {
        **persistent,
        "AutoMode": _MODES[max(0, selected.mode)] if selected else "stopped",
        "AutoPulseTide": selected.pulse_tide == 1 if selected else False,
        "AutoFlow": selected.flow if selected else 0,
        "AutoFreq": selected.frequency if selected else 0,
    }
    return BarTimerCommand(fields, persistent, selected.index if selected else None)
