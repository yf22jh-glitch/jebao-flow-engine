"""Bounded HA-facing dosing planning requests; no device, clock, journal or sender access."""

from dataclasses import replace
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, TypeAdapter

from jebao_flow.local_control.dosing import (
    build_dosing_channel_schedule_preview,
    plan_average_interval_dosing,
    plan_daily_dosing,
)

# Bounded, non-exponential decimal input prevents an MQTT request from expanding an enormous
# Decimal exponent. Floating JSON numbers are deliberately not rounded into a dosing amount.
Amount = (
    Annotated[
        str, StringConstraints(pattern=r"^(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?$", max_length=13)
    ]
    | Annotated[StrictInt, Field(ge=0, le=239976)]
)


class _PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channel: Annotated[StrictInt, Field(ge=1, le=4)]
    dose_count: Annotated[StrictInt, Field(ge=1, le=24)]
    first_dose_minute: Annotated[StrictInt, Field(ge=0, lt=1440)] = 0


class DailyDosingRequest(_PlanRequest):
    method: Literal["daily"]
    daily_amount: Amount
    amount_step: Amount = "1"


class AverageDosingRequest(_PlanRequest):
    method: Literal["average_interval"]
    target_daily_average: Amount
    dose_count: Annotated[StrictInt, Field(ge=1, le=24)] = 1


_REQUEST = TypeAdapter(
    Annotated[DailyDosingRequest | AverageDosingRequest, Field(discriminator="method")]
)


def preview_dosing_plan(value: object) -> dict[str, object]:
    request = _REQUEST.validate_python(value)
    average = None
    if isinstance(request, DailyDosingRequest):
        plan = plan_daily_dosing(
            channel=request.channel,
            daily_amount=request.daily_amount,
            dose_count=request.dose_count,
            first_dose_minute=request.first_dose_minute,
            amount_step=request.amount_step,
        )
    else:
        average = plan_average_interval_dosing(
            channel=request.channel, target_daily_average=request.target_daily_average
        )
        # Integer-mL intervals are multiples of the shortest exact interval. Select the shortest
        # such multiple that can give every requested dose-day entry a positive whole mL.
        amount = int(average.dose_amount)
        multiple = max(1, (request.dose_count + amount - 1) // amount)
        if average.interval_days * multiple > 30:
            raise ValueError("requested dose count has no exact interval within the app's 30 days")
        average = replace(
            average,
            interval_days=average.interval_days * multiple,
            dose_amount=Decimal(amount * multiple),
        )
        # This is the dose-day image only. It does not turn a local interval calculation into a
        # firmware interval/timer command or claim that the physical unit is proven.
        plan = plan_daily_dosing(
            channel=request.channel,
            daily_amount=average.dose_amount,
            dose_count=request.dose_count,
            first_dose_minute=request.first_dose_minute,
            amount_step="1",
        )
    image = (
        build_dosing_channel_schedule_preview(plan).public_dict()
        if plan.app_schedule_preview_available
        else None
    )
    return {
        "method": request.method,
        "plan": plan.public_dict(),
        "average": average.public_dict() if average is not None else None,
        "dose_day_image": image,
        "image_unavailable_reason": None if image is not None else "fractional_or_out_of_app_range",
        "hardware_write": False,
        "wire_send_available": False,
        "device_acceptance": "unverified",
        "physical_unit": "unknown",
        "interval_device_semantics": "unknown",
        "calendar_schedule_installed": False,
    }
