"""Read-only decoding of audited Jebao controller schedules.

The four supported pump families all expose 48 daily slots, but their offsets, slot sizes,
mode numbers, and parameter bytes differ.  Decoding is therefore selected by product key; no
write/encode counterpart is intentionally provided here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from jebao_flow.protocol.models import DeviceSchedule, ScheduleEntry

SLOT_CAPACITY = 48

DC_PUMP_PRO_PRODUCT_KEY = "0696a19599bc484f8e1866f5ccf4ee7e"
LOCAL_WAVEMAKER_PRODUCT_KEY = "1d8c63eaccac4205b92c84d77d5a08fb"
LOCAL_WAVEMAKER_PRO_PRODUCT_KEY = "50dbc92221fd4d33ae69a1fedd43b555"
AQUARIUM_PUMP_PRODUCT_KEY = "6a5c47b3ea364ecb841b47f5997a1775"

LOCAL_WAVEMAKER_PRO_MODES = (
    "pulse",
    "sine",
    "constant",
    "random",
    "tidal",
    "nutrient_transport",
    "circulation",
    "feed",
    "custom",
)

ParameterDecoder = Callable[[str, bytes], dict[str, int | bool]]


@dataclass(frozen=True, slots=True)
class _ScheduleSpec:
    raw_status_size: int
    slot_offset: int
    slot_size: int
    ymd_offset: int
    hms_offset: int
    modes: tuple[str, ...]
    decode_parameters: ParameterDecoder


def _dc_parameters(_mode: str, slot: bytes) -> dict[str, int | bool]:
    return {
        "flow": slot[5],
        "frequency": slot[6],
        "feed_time": slot[7],
    }


def _local_wavemaker_parameters(mode: str, slot: bytes) -> dict[str, int | bool]:
    pulse_tide = slot[7]
    if pulse_tide not in {0, 1}:
        raise ValueError("pulse/tide selector is outside its audited boolean range")
    return {
        "feed_time" if mode == "feed" else "flow": slot[5],
        "frequency": slot[6],
        "pulse_tide": bool(pulse_tide),
    }


def _local_wavemaker_pro_parameters(_mode: str, slot: bytes) -> dict[str, int | bool]:
    return {
        "flow": slot[5],
        "frequency": slot[6],
        "feed_time": slot[7],
        "custom_frequency": slot[8],
    }


def _aquarium_pump_parameters(mode: str, slot: bytes) -> dict[str, int | bool]:
    return {"feed_time" if mode == "feed" else "gears": slot[5]}


_SPECS: dict[str, _ScheduleSpec] = {
    DC_PUMP_PRO_PRODUCT_KEY: _ScheduleSpec(
        raw_status_size=402,
        slot_offset=9,
        slot_size=8,
        ymd_offset=393,
        hms_offset=397,
        # The public slot description disagrees with the product's AutoMode schema for codes
        # 0..2. A live A/B read established code 0=constant, while 3=random and 4=feed agree in
        # both sources. Keep 1 and 2 explicitly unverified until each has its own A/B capture.
        modes=("constant", "unverified_1", "unverified_2", "random", "feed"),
        decode_parameters=_dc_parameters,
    ),
    LOCAL_WAVEMAKER_PRODUCT_KEY: _ScheduleSpec(
        raw_status_size=401,
        slot_offset=8,
        slot_size=8,
        ymd_offset=392,
        hms_offset=396,
        modes=("stopped", "classic", "sine", "random", "constant", "feed"),
        decode_parameters=_local_wavemaker_parameters,
    ),
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY: _ScheduleSpec(
        raw_status_size=452,
        slot_offset=11,
        slot_size=9,
        ymd_offset=443,
        hms_offset=447,
        modes=LOCAL_WAVEMAKER_PRO_MODES,
        decode_parameters=_local_wavemaker_pro_parameters,
    ),
    AQUARIUM_PUMP_PRODUCT_KEY: _ScheduleSpec(
        raw_status_size=302,
        slot_offset=13,
        slot_size=6,
        ymd_offset=5,
        hms_offset=9,
        modes=("stopped", "auto", "feed"),
        decode_parameters=_aquarium_pump_parameters,
    ),
}


def decode_schedule(
    product_key: str,
    raw_status: bytes | bytearray | memoryview,
    *,
    enabled: bool,
) -> DeviceSchedule | None:
    """Decode a known pump schedule, returning ``None`` for unsupported products.

    Unused slots are represented by an all-zero or all-``0xee`` blob.  A populated slot with an
    invalid wall-clock value, unknown mode, or invalid audited selector is omitted from ``entries``
    and recorded by index in ``invalid_slots``.
    """

    spec = _SPECS.get(product_key)
    if spec is None:
        return None
    if not isinstance(enabled, bool):
        raise TypeError("schedule enabled must be a boolean")

    raw = bytes(raw_status)
    if len(raw) != spec.raw_status_size:
        raise ValueError(
            f"schedule status for {product_key} must be {spec.raw_status_size} bytes, "
            f"got {len(raw)}"
        )

    entries: list[ScheduleEntry] = []
    invalid_slots: list[int] = []
    for slot_index in range(SLOT_CAPACITY):
        start = spec.slot_offset + slot_index * spec.slot_size
        slot = raw[start : start + spec.slot_size]
        if _is_unused(slot):
            continue
        try:
            start_time = _format_time(slot[0], slot[1], allow_24=False)
            end_time = _format_time(slot[2], slot[3], allow_24=True)
            mode_code = slot[4]
            mode = spec.modes[mode_code]
            parameters = spec.decode_parameters(mode, slot)
        except (IndexError, ValueError):
            invalid_slots.append(slot_index)
            continue
        entries.append(
            ScheduleEntry(
                slot=slot_index,
                start=start_time,
                end=end_time,
                mode=mode,
                mode_code=mode_code,
                parameters=parameters,
            )
        )

    return DeviceSchedule(
        enabled=enabled,
        device_local_time=_decode_device_local_time(raw, spec),
        entries=tuple(entries),
        invalid_slots=tuple(invalid_slots),
    )


def decode_device_schedule(
    product_key: str,
    raw_status: bytes | bytearray | memoryview,
    *,
    enabled: bool,
) -> DeviceSchedule | None:
    """Compatibility-friendly explicit name for :func:`decode_schedule`."""

    return decode_schedule(product_key, raw_status, enabled=enabled)


def _is_unused(slot: bytes) -> bool:
    return slot == bytes(len(slot)) or slot == bytes([0xEE]) * len(slot)


def _format_time(hour: int, minute: int, *, allow_24: bool) -> str:
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    if allow_24 and hour == 24 and minute == 0:
        return "24:00"
    raise ValueError("schedule time is outside its supported wall-clock range")


def _decode_device_local_time(raw: bytes, spec: _ScheduleSpec) -> datetime | None:
    ymd = raw[spec.ymd_offset : spec.ymd_offset + 4]
    hms = raw[spec.hms_offset : spec.hms_offset + 4]
    clock = ymd + hms
    if _is_unused(clock) or hms[0] != 0:
        return None
    try:
        return datetime(
            ymd[0] * 100 + ymd[1],
            ymd[2],
            ymd[3],
            hms[1],
            hms[2],
            hms[3],
        )
    except ValueError:
        return None


__all__ = [
    "AQUARIUM_PUMP_PRODUCT_KEY",
    "DC_PUMP_PRO_PRODUCT_KEY",
    "LOCAL_WAVEMAKER_PRODUCT_KEY",
    "LOCAL_WAVEMAKER_PRO_MODES",
    "LOCAL_WAVEMAKER_PRO_PRODUCT_KEY",
    "SLOT_CAPACITY",
    "decode_device_schedule",
    "decode_schedule",
]
