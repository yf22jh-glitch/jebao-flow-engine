"""Inert local model for master schedules and per-slot slave flow overrides.

The model deliberately has no wire encoder.  It lets the UI and Home Assistant contracts be built
and tested while firmware acceptance remains unknown and the write path remains frozen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.operator_capture.capability import AppExpressibility, DeviceSupport
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY

PRO_SCHEDULE_SLOT_COUNT = 48
SAFE_FLOW_MINIMUM, SAFE_FLOW_MAXIMUM = declared_app_integer_range(
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    "manual_flow",
)
_PRO_MODES = {
    "pulse",
    "sine",
    "constant",
    "random",
    "tidal",
    "nutrient_transport",
    "circulation",
    "feed",
    "custom",
}
_LOGICAL_MEMBER_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class NativeLinkageMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class ScheduleClockOwnership(StrEnum):
    DEVICE_OWNED = "device_owned"


class ScheduleTimingInterpretation(StrEnum):
    INDEXED_ONLY = "indexed_only_precedence_unknown"


@dataclass(frozen=True, slots=True)
class ProScheduleSlot:
    index: int
    start_minute: int
    end_minute: int
    mode: str
    flow: int
    frequency: int
    feed_time: int
    custom_frequency: int

    def __post_init__(self) -> None:
        if not 0 <= self.index < PRO_SCHEDULE_SLOT_COUNT:
            raise ValueError("schedule slot index must be between 0 and 47")
        if not 0 <= self.start_minute < 24 * 60:
            raise ValueError("schedule start must be between 00:00 and 23:59")
        if not 0 <= self.end_minute <= 24 * 60:
            raise ValueError("schedule end must be between 00:00 and 24:00")
        if self.mode not in _PRO_MODES:
            raise ValueError("schedule mode is not in the app-declared Pro mode catalog")
        _validate_safe_flow(self.flow)
        if not 0 <= self.frequency <= 100:
            raise ValueError("schedule frequency must be between 0 and 100")
        if not 0 <= self.feed_time <= 60:
            raise ValueError("schedule feed time must be between 0 and 60")
        if not 0 <= self.custom_frequency <= 100:
            raise ValueError("custom-wave frequency must be between 0 and 100")

    def public_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "mode": self.mode,
            "flow": self.flow,
            "frequency": self.frequency,
            "feed_time": self.feed_time,
            "custom_frequency": self.custom_frequency,
        }


@dataclass(frozen=True, slots=True)
class SlaveFlowOverride:
    slot_index: int
    flow: int

    def __post_init__(self) -> None:
        if not 0 <= self.slot_index < PRO_SCHEDULE_SLOT_COUNT:
            raise ValueError("slave override slot must be between 0 and 47")
        _validate_safe_flow(self.flow)


@dataclass(frozen=True, slots=True)
class SlaveSchedule:
    logical_member: str
    product_key: str
    flow_overrides: tuple[SlaveFlowOverride, ...] = ()

    def __post_init__(self) -> None:
        if not _LOGICAL_MEMBER_RE.fullmatch(self.logical_member):
            raise ValueError("slave member must be a bounded logical name")
        if self.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("per-slot slave flow is scoped to Local Wavemaker Pro")
        indexes = [item.slot_index for item in self.flow_overrides]
        if len(indexes) != len(set(indexes)):
            raise ValueError("slave flow overrides must have unique slot indexes")


@dataclass(frozen=True, slots=True)
class CommonSlaveFlow:
    """App-visible common Flow, deliberately separate from schedule-slot AutoFlow."""

    logical_member: str
    product_key: str
    flow: int

    def __post_init__(self) -> None:
        if not _LOGICAL_MEMBER_RE.fullmatch(self.logical_member):
            raise ValueError("slave member must be a bounded logical name")
        if self.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("common slave flow is scoped to Local Wavemaker Pro")
        _validate_safe_flow(self.flow)


@dataclass(frozen=True, slots=True)
class NativeSchedulePlan:
    """Desired logical schedule, not a claim that firmware will apply it."""

    linkage_mode: NativeLinkageMode
    master_product_key: str
    master_slots: tuple[ProScheduleSlot | None, ...]
    slaves: tuple[SlaveSchedule, ...]
    clock_ownership: ScheduleClockOwnership = ScheduleClockOwnership.DEVICE_OWNED
    timing_interpretation: ScheduleTimingInterpretation = ScheduleTimingInterpretation.INDEXED_ONLY
    master_schedule_app: AppExpressibility = AppExpressibility.EXPRESSIBLE
    per_slot_flow_app: AppExpressibility = AppExpressibility.INFEASIBLE
    per_slot_flow_device: DeviceSupport = DeviceSupport.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.linkage_mode, NativeLinkageMode):
            raise TypeError("linkage mode must be a NativeLinkageMode")
        if self.master_product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("native schedule master must be a Local Wavemaker Pro")
        if len(self.master_slots) != PRO_SCHEDULE_SLOT_COUNT:
            raise ValueError("master schedule must contain the 48-position slot vector")
        active_indexes = {
            index
            for index, slot in enumerate(self.master_slots)
            if slot is not None and slot.index == index
        }
        if any(
            slot is not None and slot.index != index for index, slot in enumerate(self.master_slots)
        ):
            raise ValueError("each active master schedule slot must match its vector position")
        if not self.slaves:
            raise ValueError("native schedule plan requires at least one slave")
        member_names = [item.logical_member for item in self.slaves]
        if len(member_names) != len(set(member_names)):
            raise ValueError("native schedule slave members must be unique")
        if any(
            override.slot_index not in active_indexes
            for slave in self.slaves
            for override in slave.flow_overrides
        ):
            raise ValueError("slave flow overrides require an active master schedule slot")
        if self.clock_ownership is not ScheduleClockOwnership.DEVICE_OWNED:
            raise ValueError("the inert schedule model cannot take ownership of the device clock")
        if self.timing_interpretation is not ScheduleTimingInterpretation.INDEXED_ONLY:
            raise ValueError("overlap precedence is not known from retained device evidence")
        if self.master_schedule_app is not AppExpressibility.EXPRESSIBLE:
            raise ValueError("the app exposes the Local Wavemaker Pro master schedule editor")
        if self.per_slot_flow_app is not AppExpressibility.INFEASIBLE:
            raise ValueError("the app has no slave per-slot flow editor")
        if self.per_slot_flow_device is not DeviceSupport.UNKNOWN:
            raise ValueError("device support cannot be promoted by the inert schedule model")

    def materialize_slave_slots(self, logical_member: str) -> tuple[ProScheduleSlot | None, ...]:
        try:
            slave = next(item for item in self.slaves if item.logical_member == logical_member)
        except StopIteration as error:
            raise KeyError("native schedule has no such logical slave member") from error
        overrides = {item.slot_index: item.flow for item in slave.flow_overrides}
        return tuple(
            None if slot is None else replace(slot, flow=overrides.get(slot.index, slot.flow))
            for slot in self.master_slots
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "linkage_mode": self.linkage_mode.value,
            "master_product_key": self.master_product_key,
            "clock_ownership": self.clock_ownership.value,
            "timing_interpretation": self.timing_interpretation.value,
            "master_schedule_app": self.master_schedule_app.value,
            "per_slot_flow_support": {
                "app": self.per_slot_flow_app.value,
                "device": self.per_slot_flow_device.value,
            },
            "master_slots": [
                None if slot is None else slot.public_dict() for slot in self.master_slots
            ],
            "slaves": [
                {
                    "logical_member": slave.logical_member,
                    "product_key": slave.product_key,
                    "slots": [
                        None if slot is None else slot.public_dict()
                        for slot in self.materialize_slave_slots(slave.logical_member)
                    ],
                    "flow_overrides": [
                        {"slot_index": item.slot_index, "flow": item.flow}
                        for item in slave.flow_overrides
                    ],
                }
                for slave in self.slaves
            ],
        }


def _validate_safe_flow(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("flow must be an integer")
    if not SAFE_FLOW_MINIMUM <= value <= SAFE_FLOW_MAXIMUM:
        raise ValueError(
            f"flow must stay inside the declared {SAFE_FLOW_MINIMUM}..{SAFE_FLOW_MAXIMUM} app range"
        )


__all__ = [
    "CommonSlaveFlow",
    "NativeLinkageMode",
    "NativeSchedulePlan",
    "PRO_SCHEDULE_SLOT_COUNT",
    "ProScheduleSlot",
    "SAFE_FLOW_MAXIMUM",
    "SAFE_FLOW_MINIMUM",
    "ScheduleClockOwnership",
    "ScheduleTimingInterpretation",
    "SlaveSchedule",
    "SlaveFlowOverride",
]
