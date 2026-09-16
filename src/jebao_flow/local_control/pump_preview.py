"""Non-sending return-pump schedules and app-shaped clock previews.

Contracts: docs/capabilities/registered-device-app-analysis.md, Schedule/reset/clock and
DC/Aquarium sections. Ambiguous mode meanings are not inferred from manual controls.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from jebao_flow.protocol.models import ScheduleEndTime, ScheduleStartTime
from jebao_flow.protocol.schedule import (
    AQUARIUM_PUMP_PRODUCT_KEY,
    DC_PUMP_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
)


class ReturnPumpSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_index: Annotated[StrictInt, Field(ge=0, lt=48)]
    start: ScheduleStartTime
    end: ScheduleEndTime
    mode: str
    flow: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    frequency: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    feed_time: Annotated[StrictInt, Field(ge=0, le=60)] = 0
    speed: Annotated[StrictInt, Field(ge=30, le=100)] | None = None


class ReturnPumpSchedule(BaseModel):
    """Complete offline image; neither accepted ranges nor permission to send."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    family: Literal["dc_pro", "aquarium"]
    slots: tuple[ReturnPumpSlot, ...] = Field(max_length=48)

    @model_validator(mode="after")
    def validate_slots(self) -> Self:
        if len({slot.slot_index for slot in self.slots}) != len(self.slots):
            raise ValueError("return-pump slot indexes must be unique")
        for slot in self.slots:
            if self.family == "dc_pro":
                if slot.mode not in {"constant", "pulse", "sine", "random", "feed"}:
                    raise ValueError("DC schedule mode is not in the audited app enum")
                if slot.speed is not None:
                    raise ValueError("DC schedules use flow, not Aquarium speed")
                if slot.flow is None or not (
                    30 <= slot.flow <= 100 or slot.mode == "feed" and slot.flow == 0
                ):
                    raise ValueError("DC schedule flow must follow the declared app range")
                if slot.mode == "feed" and slot.feed_time == 0:
                    raise ValueError("DC feeding preview requires a declared duration of 1..60")
            else:
                if slot.mode not in {"stopped", "auto", "feed"}:
                    raise ValueError("Aquarium schedules use stopped/auto/feed, not wave modes")
                if slot.flow is not None or slot.frequency or slot.feed_time:
                    raise ValueError("Aquarium slots do not encode flow/frequency/feed duration")
                if (slot.mode == "auto") != (slot.speed is not None):
                    raise ValueError("only Aquarium auto slots take an app speed value")
        return self

    @property
    def product_key(self) -> str:
        return DC_PUMP_PRO_PRODUCT_KEY if self.family == "dc_pro" else AQUARIUM_PUMP_PRODUCT_KEY

    def compile_image(self) -> bytes:
        size = 8 if self.family == "dc_pro" else 6
        image = bytearray(b"\xee" * (48 * size))
        for slot in self.slots:
            # Audited DC slot save clears equal/backward intervals. Never claim overnight wrap.
            if self.family == "dc_pro" and slot.start >= slot.end:
                continue
            times = tuple(
                int(part) for value in (slot.start, slot.end) for part in value.split(":")
            )
            if self.family == "dc_pro":
                wire = bytes(
                    (
                        *times,
                        # Active dcPro bc20fe3b template: Te/Pe and ScheduleDetail use
                        # this same enum. This is app encoding, not firmware acceptance.
                        {"constant": 0, "pulse": 1, "sine": 2, "random": 3, "feed": 4}[slot.mode],
                        slot.flow,
                        slot.frequency,
                        slot.feed_time,
                    )
                )
            else:
                # The app writes zero for BOTH stopped and feed; physical feeding is unverified.
                wire = bytes(
                    (
                        *times,
                        {"stopped": 0, "auto": 1, "feed": 2}[slot.mode],
                        slot.speed if slot.mode == "auto" else 0,
                    )
                )
            image[slot.slot_index * size : (slot.slot_index + 1) * size] = wire
        return bytes(image)

    def preview(self) -> dict[str, object]:
        image = self.compile_image()
        return {
            **self.model_dump(mode="json"),
            "image_bytes": len(image),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "hardware_write": False,
            "device_acceptance": "unverified",
            "wire_send_available": False,
            "cleared_slot_indexes": [
                slot.slot_index
                for slot in self.slots
                if self.family == "dc_pro" and slot.start >= slot.end
            ],
            "unresolved": (
                ["feeding_accepted_range", "device_write_acceptance"]
                if self.family == "dc_pro"
                else ["feeding_zero_byte_behavior", "motor_accepted_range", "timer_coupling"]
            ),
        }


def preview_device_clock(product_key: str, local_time: datetime) -> dict[str, object]:
    """Build but never send. Require caller-selected zone; do not use this host's clock."""
    from jebao_flow.local_control.device_writer import build_device_clock_payload

    if local_time.tzinfo is None or local_time.utcoffset() is None:
        raise ValueError("clock preview requires an explicit UTC offset")
    effective = local_time.replace(microsecond=0)
    payload = build_device_clock_payload(product_key, effective)
    return {
        "requested_local_time": local_time.isoformat(),
        "encoded_local_time": effective.isoformat(),
        "timezone_encoded": False,
        "precision": "whole_seconds",
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "hardware_write": False,
        "wire_send_available": False,
        "device_acceptance": "unverified",
        "verification_contract": (
            "advancing_clock"
            if product_key in {DC_PUMP_PRO_PRODUCT_KEY, LOCAL_WAVEMAKER_PRO_PRODUCT_KEY}
            else "not_defined"
        ),
        "restore_previous_clock": False,
    }
