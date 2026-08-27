"""Local Wavemaker Pro schedule wire primitives.

This module deliberately stays separate from :mod:`jebao_flow.protocol.schema`.  The normal
``ProductSchema`` exposes a small operational datapoint set, whereas this device has 48 binary
``AutoTime`` datapoints.  Keeping the schedule image explicit prevents raw schedule bytes from
leaking into ``DeviceState.observed_attributes`` and avoids making ordinary controls allocate 48
otherwise-unused attributes.

The immutable image is exactly the 48 nine-byte slots.  Device YMD/HMS bytes are outside that
image and are never copied into a schedule control payload.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from jebao_flow.protocol.control import CONTROL_ACTION
from jebao_flow.protocol.models import ScheduleEntry
from jebao_flow.protocol.schedule import (
    LOCAL_WAVEMAKER_PRO_MODES,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    SLOT_CAPACITY,
    decode_schedule,
)

LOCAL_WAVEMAKER_PRO_SLOT_COUNT = SLOT_CAPACITY
LOCAL_WAVEMAKER_PRO_SLOT_SIZE = 9
LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE = (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
)
LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET = 11
LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_END = (
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET + LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
)
LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE = 452

LOCAL_WAVEMAKER_PRO_FIRST_SCHEDULE_DP_ID = 13
LOCAL_WAVEMAKER_PRO_LAST_SCHEDULE_DP_ID = 60
LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE = 8
LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE = 451
LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE = (
    1 + LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE + LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE
)

LOCAL_WAVEMAKER_PRO_UNUSED_ZERO = bytes(LOCAL_WAVEMAKER_PRO_SLOT_SIZE)
LOCAL_WAVEMAKER_PRO_UNUSED_EE = bytes([0xEE]) * LOCAL_WAVEMAKER_PRO_SLOT_SIZE

_PARAMETER_NAMES = frozenset({"flow", "frequency", "feed_time", "custom_frequency"})

type BytesLike = bytes | bytearray | memoryview
type SchedulePatchSource = Mapping[int, BytesLike] | Iterable[tuple[int, BytesLike]]


class ScheduleWireValidationError(ValueError):
    """Raised when a schedule image or slot is not safe to encode."""


@dataclass(frozen=True, slots=True)
class LocalWavemakerProScheduleSnapshot:
    """Immutable, byte-exact copy of the 48-slot schedule region."""

    image: bytes = field(repr=False)

    def __post_init__(self) -> None:
        image = _require_exact_bytes(
            self.image,
            expected=LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
            label="schedule image",
        )
        object.__setattr__(self, "image", image)

    @classmethod
    def from_status(cls, raw_status: BytesLike) -> LocalWavemakerProScheduleSnapshot:
        return cls(extract_local_wavemaker_pro_schedule_image(raw_status))

    def slot_wire(self, slot_index: int) -> bytes:
        return get_local_wavemaker_pro_slot_wire(self.image, slot_index)

    def with_slot_wire(
        self,
        slot_index: int,
        slot_wire: BytesLike,
    ) -> LocalWavemakerProScheduleSnapshot:
        return type(self)(
            patch_local_wavemaker_pro_schedule_slot(self.image, slot_index, slot_wire)
        )

    def with_entry(self, entry: ScheduleEntry) -> LocalWavemakerProScheduleSnapshot:
        if not isinstance(entry, ScheduleEntry):
            raise TypeError("schedule entry must be a ScheduleEntry")
        return self.with_slot_wire(entry.slot, encode_local_wavemaker_pro_schedule_entry(entry))

    def validate(self) -> LocalWavemakerProScheduleSnapshot:
        validate_local_wavemaker_pro_schedule_image(self.image)
        return self


def local_wavemaker_pro_schedule_datapoint_id(slot_index: int) -> int:
    """Map zero-based slot index to the audited AutoTime datapoint id (13..60)."""

    return LOCAL_WAVEMAKER_PRO_FIRST_SCHEDULE_DP_ID + _require_slot_index(slot_index)


def local_wavemaker_pro_schedule_status_offset(slot_index: int) -> int:
    """Return the raw-status byte offset of a slot (11..434)."""

    return LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET + (
        _require_slot_index(slot_index) * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
    )


def extract_local_wavemaker_pro_schedule_image(raw_status: BytesLike) -> bytes:
    """Extract exactly 432 schedule bytes from a complete 452-byte Pro status.

    This is intentionally a byte-exact snapshot operation.  Use
    :func:`validate_local_wavemaker_pro_schedule_image` separately before constructing writes;
    snapshot extraction itself must also preserve an unknown or malformed device slot for an
    exact recovery comparison.
    """

    raw = _require_exact_bytes(
        raw_status,
        expected=LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
        label="Local Wavemaker Pro status",
    )
    return raw[
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET:
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_END
    ]


def get_local_wavemaker_pro_slot_wire(image: BytesLike, slot_index: int) -> bytes:
    """Return one byte-exact nine-byte slot from a schedule image."""

    raw = _require_exact_bytes(
        image,
        expected=LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
        label="schedule image",
    )
    index = _require_slot_index(slot_index)
    start = index * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
    return raw[start : start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE]


def decode_local_wavemaker_pro_slot_wire(
    slot_wire: BytesLike,
    *,
    slot_index: int = 0,
) -> ScheduleEntry | None:
    """Strictly decode one slot through the existing product schedule decoder.

    Both all-zero and all-``0xee`` unused representations return ``None``.  Active slots must
    satisfy the decoder's time/mode rules and the audited parameter ranges.
    """

    wire = _require_exact_bytes(
        slot_wire,
        expected=LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
        label="schedule slot",
    )
    index = _require_slot_index(slot_index)
    if wire in {LOCAL_WAVEMAKER_PRO_UNUSED_ZERO, LOCAL_WAVEMAKER_PRO_UNUSED_EE}:
        return None

    raw_status = bytearray(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE)
    raw_status[
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET:
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_END
    ] = bytes([0xEE]) * LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    status_start = local_wavemaker_pro_schedule_status_offset(index)
    raw_status[status_start : status_start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE] = wire

    schedule = decode_schedule(
        LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        raw_status,
        enabled=False,
    )
    if schedule is None:  # product key is a module constant
        raise AssertionError("Local Wavemaker Pro decoder is unavailable")
    if schedule.invalid_slots:
        raise ScheduleWireValidationError(f"schedule slot {index} has invalid time or mode")
    if len(schedule.entries) != 1 or schedule.entries[0].slot != index:
        raise ScheduleWireValidationError(f"schedule slot {index} is not an active slot")

    entry = schedule.entries[0]
    _validate_entry_parameters(entry)
    return entry


def validate_local_wavemaker_pro_slot_wire(
    slot_wire: BytesLike,
    *,
    slot_index: int = 0,
) -> bytes:
    """Validate one encoded slot and return an immutable byte copy."""

    wire = _require_exact_bytes(
        slot_wire,
        expected=LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
        label="schedule slot",
    )
    decode_local_wavemaker_pro_slot_wire(wire, slot_index=slot_index)
    return wire


def validate_local_wavemaker_pro_schedule_image(image: BytesLike) -> bytes:
    """Validate every active slot in an exact 432-byte schedule image."""

    raw = _require_exact_bytes(
        image,
        expected=LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
        label="schedule image",
    )
    for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT):
        start = index * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
        try:
            decode_local_wavemaker_pro_slot_wire(
                raw[start : start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE],
                slot_index=index,
            )
        except ScheduleWireValidationError as error:
            raise ScheduleWireValidationError(f"invalid schedule image: {error}") from error
    return raw


def encode_local_wavemaker_pro_schedule_entry(entry: ScheduleEntry) -> bytes:
    """Strictly encode one decoded-style entry into its nine-byte AutoTime value."""

    if not isinstance(entry, ScheduleEntry):
        raise TypeError("schedule entry must be a ScheduleEntry")

    try:
        expected_mode_code = LOCAL_WAVEMAKER_PRO_MODES.index(entry.mode)
    except ValueError as error:
        choices = ", ".join(LOCAL_WAVEMAKER_PRO_MODES)
        raise ScheduleWireValidationError(
            f"schedule slot {entry.slot} mode must be one of {choices}"
        ) from error
    if entry.mode_code != expected_mode_code:
        raise ScheduleWireValidationError(
            f"schedule slot {entry.slot} mode/code mismatch: "
            f"{entry.mode!r} requires {expected_mode_code}, got {entry.mode_code}"
        )

    start_hour, start_minute = _parse_wall_clock(entry.start, allow_24=False, label="start")
    end_hour, end_minute = _parse_wall_clock(entry.end, allow_24=True, label="end")
    parameters = _validate_entry_parameters(entry)
    encoded = bytes(
        (
            start_hour,
            start_minute,
            end_hour,
            end_minute,
            expected_mode_code,
            parameters["flow"],
            parameters["frequency"],
            parameters["feed_time"],
            parameters["custom_frequency"],
        )
    )
    if encoded in {LOCAL_WAVEMAKER_PRO_UNUSED_ZERO, LOCAL_WAVEMAKER_PRO_UNUSED_EE}:
        raise ScheduleWireValidationError(
            "active schedule entry encodes to an unused-slot sentinel"
        )

    # Enforce parity with the read path before these bytes can reach a control builder.
    decoded = decode_local_wavemaker_pro_slot_wire(encoded, slot_index=entry.slot)
    if decoded is None:
        raise ScheduleWireValidationError("encoded schedule entry was decoded as unused")
    return encoded


def patch_local_wavemaker_pro_schedule_slot(
    image: BytesLike,
    slot_index: int,
    slot_wire: BytesLike,
) -> bytes:
    """Return an image with exactly one validated slot replaced.

    The source object is never mutated.  Every byte outside the selected nine-byte span is copied
    unchanged, including whether each unused slot uses ``00`` or ``ee`` sentinels.
    """

    source = _require_exact_bytes(
        image,
        expected=LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
        label="schedule image",
    )
    index = _require_slot_index(slot_index)
    replacement = validate_local_wavemaker_pro_slot_wire(slot_wire, slot_index=index)
    start = index * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
    return source[:start] + replacement + source[start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE :]


def build_schedule_control_payload(patches: SchedulePatchSource) -> bytes:
    """Build one Pro schedule control payload without sending it.

    Input can be a mapping or an iterable of ``(slot_index, raw_9_bytes)`` pairs.  Iterable input
    permits explicit duplicate detection.  Only the selected AutoTime flags (DP 13..60) and their
    corresponding 9-byte value spans are populated; all operational values and YMD/HMS bytes stay
    zero and unflagged.
    """

    items = _normalize_patch_items(patches)
    flags = bytearray(LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE)
    values = bytearray(LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE)

    for slot_index, slot_wire in items:
        datapoint_id = local_wavemaker_pro_schedule_datapoint_id(slot_index)
        flags[datapoint_id // 8] |= 1 << (datapoint_id % 8)
        status_offset = local_wavemaker_pro_schedule_status_offset(slot_index)
        values[status_offset : status_offset + LOCAL_WAVEMAKER_PRO_SLOT_SIZE] = slot_wire

    payload = bytes([CONTROL_ACTION]) + bytes(reversed(flags)) + bytes(values)
    if len(payload) != LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE:
        raise AssertionError("Local Wavemaker Pro schedule payload size invariant failed")
    return payload


def build_local_wavemaker_pro_schedule_control_payload(
    patches: SchedulePatchSource,
) -> bytes:
    """Product-explicit alias for :func:`build_schedule_control_payload`."""

    return build_schedule_control_payload(patches)


def _normalize_patch_items(patches: SchedulePatchSource) -> tuple[tuple[int, bytes], ...]:
    if isinstance(patches, Mapping):
        source: Iterable[tuple[int, BytesLike]] = patches.items()
    else:
        if isinstance(patches, (str, bytes, bytearray, memoryview)):
            raise TypeError("schedule patches must be a mapping or iterable of pairs")
        try:
            source = iter(patches)
        except TypeError as error:
            raise TypeError("schedule patches must be a mapping or iterable of pairs") from error

    normalized: list[tuple[int, bytes]] = []
    seen: set[int] = set()
    for ordinal, item in enumerate(source):
        try:
            slot_index, slot_wire = item
        except (TypeError, ValueError) as error:
            raise TypeError(f"schedule patch {ordinal} must be an index/value pair") from error
        index = _require_slot_index(slot_index)
        if index in seen:
            raise ScheduleWireValidationError(f"duplicate schedule slot {index}")
        seen.add(index)
        normalized.append(
            (
                index,
                validate_local_wavemaker_pro_slot_wire(slot_wire, slot_index=index),
            )
        )

    if not normalized:
        raise ScheduleWireValidationError("at least one schedule slot patch is required")
    return tuple(normalized)


def _validate_entry_parameters(entry: ScheduleEntry) -> dict[str, int]:
    actual_names = set(entry.parameters)
    missing = _PARAMETER_NAMES - actual_names
    extra = actual_names - _PARAMETER_NAMES
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if extra:
            details.append(f"unexpected {', '.join(sorted(extra))}")
        raise ScheduleWireValidationError(
            f"schedule slot {entry.slot} parameters are invalid ({'; '.join(details)})"
        )

    parameters = {
        "flow": _require_bounded_integer(entry.parameters["flow"], "flow", 0, 100),
        "frequency": _require_bounded_integer(
            entry.parameters["frequency"], "frequency", 0, 100
        ),
        "feed_time": _require_bounded_integer(
            entry.parameters["feed_time"], "feed_time", 0, 60
        ),
        "custom_frequency": _require_bounded_integer(
            entry.parameters["custom_frequency"], "custom_frequency", 0, 100
        ),
    }
    if entry.mode == "feed" and parameters["feed_time"] == 0:
        raise ScheduleWireValidationError("feed schedule slots require feed_time in 1..60")
    return parameters


def _require_bounded_integer(value: int | bool, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"schedule {label} must be an integer")
    if not minimum <= value <= maximum:
        raise ScheduleWireValidationError(
            f"schedule {label} must be in {minimum}..{maximum}, got {value}"
        )
    return value


def _parse_wall_clock(value: str, *, allow_24: bool, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, str)
        or len(value) != 5
        or value[2] != ":"
        or not value[:2].isascii()
        or not value[:2].isdigit()
        or not value[3:].isascii()
        or not value[3:].isdigit()
    ):
        raise ScheduleWireValidationError(f"schedule {label} time must use HH:MM")
    hour = int(value[:2])
    minute = int(value[3:])
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    if allow_24 and hour == 24 and minute == 0:
        return hour, minute
    raise ScheduleWireValidationError(f"schedule {label} time is outside its wire range")


def _require_slot_index(slot_index: int) -> int:
    if isinstance(slot_index, bool) or not isinstance(slot_index, int):
        raise TypeError("schedule slot index must be an integer")
    if not 0 <= slot_index < LOCAL_WAVEMAKER_PRO_SLOT_COUNT:
        raise ScheduleWireValidationError(
            f"schedule slot index must be in 0..{LOCAL_WAVEMAKER_PRO_SLOT_COUNT - 1}"
        )
    return slot_index


def _require_exact_bytes(value: BytesLike, *, expected: int, label: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{label} must be bytes-like")
    raw = bytes(value)
    if len(raw) != expected:
        raise ScheduleWireValidationError(
            f"{label} must be exactly {expected} bytes, got {len(raw)}"
        )
    return raw


__all__ = [
    "LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE",
    "LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE",
    "LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE",
    "LOCAL_WAVEMAKER_PRO_FIRST_SCHEDULE_DP_ID",
    "LOCAL_WAVEMAKER_PRO_LAST_SCHEDULE_DP_ID",
    "LOCAL_WAVEMAKER_PRO_PRODUCT_KEY",
    "LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE",
    "LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE",
    "LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_END",
    "LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET",
    "LOCAL_WAVEMAKER_PRO_SLOT_COUNT",
    "LOCAL_WAVEMAKER_PRO_SLOT_SIZE",
    "LOCAL_WAVEMAKER_PRO_UNUSED_EE",
    "LOCAL_WAVEMAKER_PRO_UNUSED_ZERO",
    "LocalWavemakerProScheduleSnapshot",
    "ScheduleWireValidationError",
    "build_local_wavemaker_pro_schedule_control_payload",
    "build_schedule_control_payload",
    "decode_local_wavemaker_pro_slot_wire",
    "encode_local_wavemaker_pro_schedule_entry",
    "extract_local_wavemaker_pro_schedule_image",
    "get_local_wavemaker_pro_slot_wire",
    "local_wavemaker_pro_schedule_datapoint_id",
    "local_wavemaker_pro_schedule_status_offset",
    "patch_local_wavemaker_pro_schedule_slot",
    "validate_local_wavemaker_pro_schedule_image",
    "validate_local_wavemaker_pro_slot_wire",
]
