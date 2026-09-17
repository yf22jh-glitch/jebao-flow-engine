"""App-derived controls for the four-channel Dosing Pump.

Three cached copies of ``com.jebao.doser.1`` use the same command shapes.  This module mirrors
those field contracts without adding a physical-volume conversion: schedule volumes are the app's
integer display values and calibration values are the app's 10-unit steps.  The HA calibration
channel select writes the same ``CALSet`` datapoint as an absolute value instead of replaying the
app's incremental button presses.  Restoring a datapoint cannot undo liquid that was dispensed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

DOSING_PUMP_PRODUCT_KEY = "5b3c136fd4b74f3fb2a366a254c76c9a"
_CHANNELS = range(1, 5)
_CALIBRATION_CHOICES = tuple(f"channel_{channel}" for channel in _CHANNELS)


@dataclass(frozen=True, slots=True)
class DosingControlCommand:
    """One app-equivalent command and the persistent fields it owns."""

    fields: Mapping[str, object]
    action: str
    channel: int | None = None

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("dosing command requires at least one field")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


def prepare_dosing_control_command(
    function: str,
    value: object,
    current: Mapping[str, Any],
) -> DosingControlCommand:
    """Build one of the audited Dosing app commands.

    Channel-scoped controls use ``{"channel": 1..4, "value": ...}``.  The calibration switch
    copies the current channel and amount because every audited app copy sends those three fields
    together.
    """

    if function == "power":
        return _command({"switch": _boolean(value, "power")}, "power_toggle")
    if function == "manual_channel":
        channel, selected = _channel_value(value)
        return _command(
            {f"channe{channel}": _boolean(selected, "manual channel")},
            "manual_channel_toggle",
            channel,
        )
    if function == "channel_timer_enable":
        channel, selected = _channel_value(value)
        return _command(
            {f"Timer{channel}ON": _boolean(selected, "channel timer")},
            "channel_timer_set",
            channel,
        )
    if function == "interval_days":
        channel, selected = _channel_value(value)
        days = _integer(selected, "interval days", minimum=0, maximum=30)
        return _command({f"IntervalT{channel}": days}, "interval_confirm", channel)
    if function == "calibration_channel":
        if value not in _CALIBRATION_CHOICES:
            raise ValueError("calibration channel must be channel_1 through channel_4")
        channel = int(str(value).rsplit("_", 1)[1])
        return _command({"CALSet": f"calibrate_{channel}"}, "calibration_channel", channel)
    if function == "calibration_amount":
        amount = _integer(value, "calibration amount", minimum=10, maximum=100)
        if amount % 10:
            raise ValueError("calibration amount must use the app's 10-unit step")
        return _command({"Calib1": amount}, "calibration_amount")
    if function == "calibration":
        enabled = _boolean(value, "calibration")
        channel = _current_calibration_channel(current)
        amount = _integer(
            current.get("Calib1"),
            "current calibration amount",
            minimum=10,
            maximum=100,
        )
        return _command(
            {"CALSW": enabled, "CALSet": f"calibrate_{channel}", "Calib1": amount},
            "calibration_toggle",
            channel,
        )
    raise ValueError("unsupported Dosing control function")


def dosing_channel_image_field(channel: int) -> str:
    """Return the exact per-channel 96-byte schedule datapoint name."""

    selected = _channel(channel)
    return f"CH{selected}SWTime"


def parse_dosing_schedule_value(value: object) -> tuple[int, bytes, int | None]:
    """Parse the bounded JSON value used by the journalled schedule apply route."""

    if not isinstance(value, dict) or not {"channel", "image"} <= set(value):
        raise ValueError("Dosing schedule requires channel and image")
    if set(value) - {"channel", "image", "interval_days"}:
        raise ValueError("Dosing schedule contains unexpected fields")
    channel = _channel(value["channel"])
    raw_image = value["image"]
    if not isinstance(raw_image, list) or len(raw_image) != 96:
        raise ValueError("Dosing schedule image must contain exactly 96 bytes")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255
        for item in raw_image
    ):
        raise ValueError("Dosing schedule image values must be bytes")
    for offset in range(0, 96, 4):
        hour, minute, volume_high, volume_low = raw_image[offset : offset + 4]
        volume = volume_high << 8 | volume_low
        if hour > 23 or minute > 59 or volume > 9999:
            raise ValueError("Dosing schedule entries exceed the audited app input range")
    interval = value.get("interval_days")
    if interval is not None:
        interval = _integer(interval, "interval days", minimum=0, maximum=30)
    return channel, bytes(raw_image), interval


def encode_dosing_channel_readback(channel: int, image: bytes) -> bytes:
    """Bind a channel number to a 96-byte image for journal verification."""

    selected = _channel(channel)
    exact = bytes(image)
    if len(exact) != 96:
        raise ValueError("Dosing channel schedule image must be exactly 96 bytes")
    return bytes((selected,)) + exact


def decode_dosing_channel_readback(value: bytes) -> tuple[int, bytes]:
    """Decode the journal-only channel/image binding."""

    exact = bytes(value)
    if len(exact) != 97:
        raise ValueError("Dosing channel readback must be channel plus 96 bytes")
    return _channel(exact[0]), exact[1:]


def _command(
    fields: Mapping[str, object],
    action: str,
    channel: int | None = None,
) -> DosingControlCommand:
    return DosingControlCommand(fields=fields, action=action, channel=channel)


def _channel_value(value: object) -> tuple[int, object]:
    if not isinstance(value, dict) or set(value) != {"channel", "value"}:
        raise ValueError("channel control requires exactly channel and value")
    return _channel(value["channel"]), value["value"]


def _channel(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in _CHANNELS:
        raise ValueError("Dosing channel must be between 1 and 4")
    return value


def _boolean(value: object, description: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{description} requires true or false")
    return value


def _integer(value: object, description: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{description} requires an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{description} must be between {minimum} and {maximum}")
    return value


def _current_calibration_channel(current: Mapping[str, Any]) -> int:
    selected = current.get("CALSet")
    if isinstance(selected, str) and selected.startswith("calibrate_"):
        try:
            return _channel(int(selected.rsplit("_", 1)[1]))
        except (ValueError, TypeError):
            pass
    if isinstance(selected, bool) or not isinstance(selected, int):
        raise ValueError("current calibration channel is unavailable")
    return _channel(selected + 1)


__all__ = [
    "DOSING_PUMP_PRODUCT_KEY",
    "DosingControlCommand",
    "decode_dosing_channel_readback",
    "dosing_channel_image_field",
    "encode_dosing_channel_readback",
    "parse_dosing_schedule_value",
    "prepare_dosing_control_command",
]
