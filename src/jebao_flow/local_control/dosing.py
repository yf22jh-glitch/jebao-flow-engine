"""Exact local dosing plans plus a non-sending app-shaped schedule preview.

The planner turns one daily total into at most 24 balanced doses. Integer-milliliter plans can be
materialized into the statically audited 96-byte app image, but this module cannot send it and does
not claim that the current firmware accepts the image or that its physical unit is confirmed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction

_DAY_MINUTES = 24 * 60
_MAX_ENTRIES_PER_CHANNEL = 24
_MAX_APP_INTERVAL_DAYS = 30
_MAX_APP_VOLUME = Decimal("9999")
_APP_AMOUNT_STEP = Decimal("1")
_UNIT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,15}\Z")


@dataclass(frozen=True, slots=True)
class PlannedDose:
    sequence: int
    minute_of_day: int
    local_time: str
    amount: Decimal

    def public_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "minute_of_day": self.minute_of_day,
            "local_time": self.local_time,
            "amount": _decimal_text(self.amount),
        }


@dataclass(frozen=True, slots=True)
class DailyDosingPlan:
    channel: int
    daily_amount: Decimal
    dose_count: int
    first_dose_minute: int
    amount_step: Decimal
    unit: str
    entries: tuple[PlannedDose, ...]

    def __post_init__(self) -> None:
        if len(self.entries) != self.dose_count:
            raise ValueError("daily dosing entry count does not match the requested dose count")
        if sum((Fraction(entry.amount) for entry in self.entries), Fraction()) != Fraction(
            self.daily_amount
        ):
            raise ValueError("daily dosing entries do not preserve the exact requested total")
        if len({entry.minute_of_day for entry in self.entries}) != len(self.entries):
            raise ValueError("daily dosing entries must use distinct times")

    @property
    def unused_entry_count(self) -> int:
        return _MAX_ENTRIES_PER_CHANNEL - len(self.entries)

    @property
    def app_schedule_preview_available(self) -> bool:
        return self.unit == "mL" and all(
            _is_app_integer_amount(entry.amount) for entry in self.entries
        )

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self._content_dict())).hexdigest()

    def _content_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "daily_amount": _decimal_text(self.daily_amount),
            "dose_count": self.dose_count,
            "first_dose_minute": self.first_dose_minute,
            "amount_step": _decimal_text(self.amount_step),
            "unit": self.unit,
            "entries": [entry.public_dict() for entry in self.entries],
        }

    def public_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": 1,
            "status": "planned_locally",
            **self._content_dict(),
            "unused_entry_count": self.unused_entry_count,
            "plan_sha256": self.plan_sha256,
            "wire_encoding_available": self.app_schedule_preview_available,
            "device_acceptance": "unknown",
            "device_amount_resolution": "unknown",
            "app_direct_amount_step": "1",
            "app_direct_sub_milliliter_supported": False,
            "app_schedule_preview_available": self.app_schedule_preview_available,
            "app_calibration_minimum": "10",
            "app_calibration_increment": "10",
            "calibration_evidence_required": True,
        }
        if not str(payload).isascii():
            raise AssertionError("daily dosing public output must use English ASCII")
        return payload


def plan_daily_dosing(
    *,
    channel: int,
    daily_amount: Decimal | str | int,
    dose_count: int,
    first_dose_minute: int = 0,
    amount_step: Decimal | str | int = Decimal("0.5"),
    unit: str = "mL",
) -> DailyDosingPlan:
    """Split an exact daily total across evenly spaced, balanced doses.

    Time gaps differ by at most one minute. Amounts differ by at most one explicit ``amount_step``
    and their Decimal sum is exactly ``daily_amount``. No cached plan or device value is read.
    """

    if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 4:
        raise ValueError("dosing channel must be between 1 and 4")
    if isinstance(dose_count, bool) or not isinstance(dose_count, int):
        raise TypeError("daily dose count must be an integer")
    if not 1 <= dose_count <= _MAX_ENTRIES_PER_CHANNEL:
        raise ValueError("daily dose count must be between 1 and 24")
    if (
        isinstance(first_dose_minute, bool)
        or not isinstance(first_dose_minute, int)
        or not 0 <= first_dose_minute < _DAY_MINUTES
    ):
        raise ValueError("first dose minute must be between 0 and 1439")
    if _UNIT_RE.fullmatch(unit) is None:
        raise ValueError("dosing unit must be a bounded English ASCII token")

    total = _exact_decimal(daily_amount, "daily amount")
    step = _exact_decimal(amount_step, "amount step")
    if total <= 0 or step <= 0:
        raise ValueError("daily amount and amount step must be positive")
    total_units, remainder = divmod(*_common_scale_integers(total, step))
    if remainder:
        raise ValueError("daily amount must be an exact multiple of amount step")
    if total_units < dose_count:
        raise ValueError("daily amount is too small to give every requested dose a positive amount")

    entries: list[PlannedDose] = []
    for index in range(dose_count):
        previous_units = (index * total_units) // dose_count
        current_units = ((index + 1) * total_units) // dose_count
        dose_units = current_units - previous_units
        minute = (first_dose_minute + (index * _DAY_MINUTES) // dose_count) % _DAY_MINUTES
        entries.append(
            PlannedDose(
                sequence=index + 1,
                minute_of_day=minute,
                local_time=f"{minute // 60:02d}:{minute % 60:02d}",
                amount=_multiply_decimal_by_integer(step, dose_units),
            )
        )

    return DailyDosingPlan(
        channel=channel,
        daily_amount=total,
        dose_count=dose_count,
        first_dose_minute=first_dose_minute,
        amount_step=step,
        unit=unit,
        entries=tuple(entries),
    )


@dataclass(frozen=True, slots=True)
class DosingChannelSchedulePreview:
    """One complete app-shaped channel image; never a device-acceptance claim."""

    channel: int
    datapoint: str
    image: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.channel <= 4:
            raise ValueError("dosing channel must be between 1 and 4")
        if self.datapoint != f"CH{self.channel}SWTime":
            raise ValueError("dosing schedule datapoint does not match the channel")
        if len(self.image) != 96:
            raise ValueError("dosing channel image must be exactly 96 bytes")

    @property
    def image_sha256(self) -> str:
        return hashlib.sha256(self.image).hexdigest()

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "app_wire_preview_only",
            "channel": self.channel,
            "datapoint": self.datapoint,
            "entry_count": 24,
            "entry_size": 4,
            "image_size": len(self.image),
            "image_sha256": self.image_sha256,
            "layout": "hour_minute_volume_uint16_be",
            "whole_datapoint_replacement": True,
            "app_display_unit": "mL",
            "physical_unit": "unknown",
            "device_acceptance": "unknown",
            "wire_send_available": True,
        }


def build_dosing_channel_schedule_preview(
    plan: DailyDosingPlan,
) -> DosingChannelSchedulePreview:
    """Materialize the app's 24 x 4-byte schedule layout without sending it.

    The installed app accepts integer milliliters only. A local half-milliliter planning value is
    therefore rejected here instead of being rounded or silently scaled.
    """

    if not isinstance(plan, DailyDosingPlan):
        raise TypeError("dosing schedule preview requires a DailyDosingPlan")
    if plan.unit != "mL":
        raise ValueError("app-shaped dosing preview requires the app display unit mL")
    image = bytearray(96)
    for index, entry in enumerate(plan.entries):
        if not _is_app_integer_amount(entry.amount):
            raise ValueError("app-shaped dosing preview requires integer-milliliter doses")
        amount = int(entry.amount)
        start = index * 4
        image[start : start + 4] = bytes(
            (entry.minute_of_day // 60, entry.minute_of_day % 60, amount >> 8, amount & 0xFF)
        )
    return DosingChannelSchedulePreview(
        channel=plan.channel,
        datapoint=f"CH{plan.channel}SWTime",
        image=bytes(image),
    )


@dataclass(frozen=True, slots=True)
class AverageIntervalDosingPlan:
    """Planning-only integer dose whose multi-day average matches a smaller daily target."""

    channel: int
    target_daily_average: Decimal
    interval_days: int
    dose_amount: Decimal
    unit: str

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self._content_dict())).hexdigest()

    def _content_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "target_daily_average": _decimal_text(self.target_daily_average),
            "interval_days": self.interval_days,
            "dose_amount": _decimal_text(self.dose_amount),
            "unit": self.unit,
        }

    def public_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": 1,
            "status": "planned_locally",
            "method": "integer_dose_over_app_interval",
            **self._content_dict(),
            "plan_sha256": self.plan_sha256,
            "app_direct_amount_step": "1",
            "app_interval_range": [1, _MAX_APP_INTERVAL_DAYS],
            "wire_encoding_available": True,
            "device_acceptance": "unknown",
            "interval_device_semantics": "unknown",
        }
        if not str(payload).isascii():
            raise AssertionError("average dosing public output must use English ASCII")
        return payload


def plan_average_interval_dosing(
    *,
    channel: int,
    target_daily_average: Decimal | str | int,
    unit: str = "mL",
) -> AverageIntervalDosingPlan:
    """Find the shortest app-shaped interval with an exact integer-mL average.

    This is a local calculation only. The English app UI exposes integer milliliters and a
    0..30 interval picker labelled Days, but the vendor definition does not declare that interval
    unit and no accepted device write exists. The result therefore never becomes a wire command.
    """

    if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 4:
        raise ValueError("dosing channel must be between 1 and 4")
    if _UNIT_RE.fullmatch(unit) is None:
        raise ValueError("dosing unit must be a bounded English ASCII token")
    target = _exact_decimal(target_daily_average, "target daily average")
    if target <= 0:
        raise ValueError("target daily average must be positive")

    for interval_days in range(1, _MAX_APP_INTERVAL_DAYS + 1):
        dose_amount = _multiply_decimal_by_integer(target, interval_days)
        _units, remainder = divmod(*_common_scale_integers(dose_amount, _APP_AMOUNT_STEP))
        if remainder == 0 and dose_amount <= _MAX_APP_VOLUME:
            return AverageIntervalDosingPlan(
                channel=channel,
                target_daily_average=target,
                interval_days=interval_days,
                dose_amount=dose_amount,
                unit=unit,
            )
    raise ValueError("no exact integer-dose interval exists within the audited app range")


def _exact_decimal(value: Decimal | str | int, description: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{description} must use an exact decimal string or integer")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{description} is not a decimal") from error
    if not result.is_finite():
        raise ValueError(f"{description} must be finite")
    return result


def _is_app_integer_amount(value: Decimal) -> bool:
    return value == value.to_integral_value() and Decimal(1) <= value <= _MAX_APP_VOLUME


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _common_scale_integers(total: Decimal, step: Decimal) -> tuple[int, int]:
    total_tuple = total.as_tuple()
    step_tuple = step.as_tuple()
    common_exponent = min(int(total_tuple.exponent), int(step_tuple.exponent))
    total_integer = _decimal_coefficient(total) * 10 ** (
        int(total_tuple.exponent) - common_exponent
    )
    step_integer = _decimal_coefficient(step) * 10 ** (int(step_tuple.exponent) - common_exponent)
    return total_integer, step_integer


def _decimal_coefficient(value: Decimal) -> int:
    parts = value.as_tuple()
    coefficient = int("".join(str(digit) for digit in parts.digits) or "0")
    return -coefficient if parts.sign else coefficient


def _multiply_decimal_by_integer(value: Decimal, multiplier: int) -> Decimal:
    parts = value.as_tuple()
    coefficient = _decimal_coefficient(value) * multiplier
    sign = int(coefficient < 0)
    digits = tuple(int(character) for character in str(abs(coefficient)))
    return Decimal((sign, digits, parts.exponent))


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


__all__ = [
    "AverageIntervalDosingPlan",
    "DailyDosingPlan",
    "DosingChannelSchedulePreview",
    "PlannedDose",
    "build_dosing_channel_schedule_preview",
    "plan_average_interval_dosing",
    "plan_daily_dosing",
]
