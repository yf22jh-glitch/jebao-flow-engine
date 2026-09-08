from decimal import Decimal
from fractions import Fraction

import pytest

from jebao_flow.local_control.dosing import (
    build_dosing_channel_schedule_preview,
    plan_average_interval_dosing,
    plan_daily_dosing,
)


def test_daily_total_is_balanced_exactly_across_even_times() -> None:
    plan = plan_daily_dosing(
        channel=2,
        daily_amount="100",
        dose_count=6,
        first_dose_minute=60,
        amount_step="0.1",
    )

    assert [entry.local_time for entry in plan.entries] == [
        "01:00",
        "05:00",
        "09:00",
        "13:00",
        "17:00",
        "21:00",
    ]
    assert sum((entry.amount for entry in plan.entries), Decimal(0)) == Decimal("100")
    assert max(entry.amount for entry in plan.entries) - min(
        entry.amount for entry in plan.entries
    ) == Decimal("0.1")
    assert plan.unused_entry_count == 18
    assert plan.public_dict()["wire_encoding_available"] is False
    assert plan.public_dict()["device_amount_resolution"] == "unknown"
    assert plan.public_dict()["calibration_evidence_required"] is True
    assert str(plan.public_dict()).isascii()


def test_non_divisible_integer_total_preserves_exact_sum_without_float_rounding() -> None:
    plan = plan_daily_dosing(channel=1, daily_amount=10, dose_count=3)

    assert [entry.amount for entry in plan.entries] == [
        Decimal("3"),
        Decimal("3.5"),
        Decimal("3.5"),
    ]
    assert [entry.local_time for entry in plan.entries] == ["00:00", "08:00", "16:00"]
    assert sum((entry.amount for entry in plan.entries), Decimal(0)) == Decimal(10)


def test_daily_dosing_plan_defaults_to_half_milliliter_steps() -> None:
    plan = plan_daily_dosing(channel=1, daily_amount="7.5", dose_count=3)

    assert plan.amount_step == Decimal("0.5")
    assert [entry.amount for entry in plan.entries] == [
        Decimal("2.5"),
        Decimal("2.5"),
        Decimal("2.5"),
    ]
    assert plan.public_dict()["app_direct_amount_step"] == "1"
    assert plan.public_dict()["app_direct_sub_milliliter_supported"] is False


def test_planner_does_not_round_values_beyond_decimal_context_precision() -> None:
    total = Decimal("123456789012345678901234567890.5")
    plan = plan_daily_dosing(
        channel=1,
        daily_amount=total,
        dose_count=3,
    )

    assert sum((Fraction(entry.amount) for entry in plan.entries), Fraction()) == Fraction(total)


def test_first_dose_wraps_without_changing_sequence_or_total() -> None:
    plan = plan_daily_dosing(
        channel=4,
        daily_amount="12",
        dose_count=3,
        first_dose_minute=20 * 60,
    )

    assert [entry.local_time for entry in plan.entries] == ["20:00", "04:00", "12:00"]
    assert [entry.sequence for entry in plan.entries] == [1, 2, 3]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"channel": 0, "daily_amount": 1, "dose_count": 1}, "channel"),
        ({"channel": 1, "daily_amount": 1, "dose_count": 25}, "between 1 and 24"),
        ({"channel": 1, "daily_amount": "1.05", "dose_count": 1, "amount_step": "0.1"}, "multiple"),
        (
            {"channel": 1, "daily_amount": 2, "dose_count": 3, "amount_step": 1},
            "too small",
        ),
        ({"channel": 1, "daily_amount": 1.0, "dose_count": 1}, "exact decimal"),
    ),
)
def test_planner_rejects_only_intrinsically_unrepresentable_requests(kwargs, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        plan_daily_dosing(**kwargs)


def test_half_milliliter_daily_average_uses_shortest_integer_app_interval() -> None:
    plan = plan_average_interval_dosing(
        channel=1,
        target_daily_average="0.5",
    )

    assert plan.interval_days == 2
    assert plan.dose_amount == Decimal("1")
    assert plan.public_dict()["method"] == "integer_dose_over_app_interval"
    assert plan.public_dict()["wire_encoding_available"] is False
    assert plan.public_dict()["interval_device_semantics"] == "unknown"


def test_average_interval_planner_never_rounds_an_unrepresentable_target() -> None:
    with pytest.raises(ValueError, match="no exact integer-dose interval"):
        plan_average_interval_dosing(
            channel=1,
            target_daily_average="0.333",
        )


def test_integer_daily_plan_materializes_the_exact_app_channel_image() -> None:
    plan = plan_daily_dosing(
        channel=3,
        daily_amount="12",
        dose_count=4,
        amount_step="1",
    )

    preview = build_dosing_channel_schedule_preview(plan)

    assert preview.datapoint == "CH3SWTime"
    assert len(preview.image) == 96
    assert preview.image[:16] == bytes(
        (
            0,
            0,
            0,
            3,
            6,
            0,
            0,
            3,
            12,
            0,
            0,
            3,
            18,
            0,
            0,
            3,
        )
    )
    assert preview.image[16:] == bytes(80)
    assert preview.public_dict() == {
        "schema_version": 1,
        "status": "app_wire_preview_only",
        "channel": 3,
        "datapoint": "CH3SWTime",
        "entry_count": 24,
        "entry_size": 4,
        "image_size": 96,
        "image_sha256": preview.image_sha256,
        "layout": "hour_minute_volume_uint16_be",
        "whole_datapoint_replacement": True,
        "app_display_unit": "mL",
        "physical_unit": "unknown",
        "device_acceptance": "unknown",
        "wire_send_available": False,
    }
    assert "image" not in preview.public_dict()
    assert str(preview.public_dict()).isascii()


def test_fractional_local_plan_is_never_rounded_into_an_app_image() -> None:
    plan = plan_daily_dosing(
        channel=1,
        daily_amount="7.5",
        dose_count=3,
    )

    assert plan.app_schedule_preview_available is False
    with pytest.raises(ValueError, match="integer-milliliter"):
        build_dosing_channel_schedule_preview(plan)
