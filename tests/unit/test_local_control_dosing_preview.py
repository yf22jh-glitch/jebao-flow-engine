import json
from decimal import Decimal

import pytest

from jebao_flow.local_control.dosing_preview import preview_dosing_plan
from jebao_flow.local_control.mqtt import LocalControlMqttError
from jebao_flow.operator_capture.app_catalog import DOSING_PUMP_APP
from tests.unit.test_local_control_pump_preview import _router


def test_daily_dosing_preserves_requested_total_count_and_app_image():
    result = preview_dosing_plan(
        {
            "method": "daily",
            "channel": 3,
            "daily_amount": "13",
            "dose_count": 4,
            "first_dose_minute": 90,
        }
    )
    entries = result["plan"]["entries"]
    assert [entry["local_time"] for entry in entries] == ["01:30", "07:30", "13:30", "19:30"]
    assert sum(Decimal(entry["amount"]) for entry in entries) == Decimal("13")
    assert len(entries) == 4 and result["dose_day_image"]["image_size"] == 96
    assert result["dose_day_image"]["datapoint"] == "CH3SWTime"
    assert result["hardware_write"] is False and result["wire_send_available"] is False


def test_fractional_dosing_remains_exact_local_plan_not_rounded_wire_image():
    result = preview_dosing_plan(
        {
            "method": "daily",
            "channel": 1,
            "daily_amount": "0.5",
            "dose_count": 1,
            "amount_step": "0.5",
        }
    )
    assert result["plan"]["entries"][0]["amount"] == "0.5"
    assert result["dose_day_image"] is None
    assert result["image_unavailable_reason"] == "fractional_or_out_of_app_range"


def test_half_ml_average_is_one_integer_ml_every_two_days_only_as_local_plan():
    result = preview_dosing_plan(
        {"method": "average_interval", "channel": 2, "target_daily_average": "0.5"}
    )
    assert result["average"]["interval_days"] == 2
    assert result["average"]["dose_amount"] == "1"
    assert result["plan"]["entries"][0]["amount"] == "1"
    assert result["dose_day_image"]["datapoint"] == "CH2SWTime"
    assert result["interval_device_semantics"] == "unknown"
    assert result["physical_unit"] == "unknown"
    assert result["calendar_schedule_installed"] is False


def test_average_plan_preserves_requested_count_on_each_dose_day():
    result = preview_dosing_plan(
        {"method": "average_interval", "channel": 1, "target_daily_average": "0.5", "dose_count": 3}
    )
    assert result["average"]["interval_days"] == 6
    assert result["average"]["dose_amount"] == "3"
    assert [entry["amount"] for entry in result["plan"]["entries"]] == ["1", "1", "1"]
    with pytest.raises(ValueError, match="30 days"):
        preview_dosing_plan(
            {
                "method": "average_interval",
                "channel": 1,
                "target_daily_average": "0.5",
                "dose_count": 24,
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"channel": True},
        {"channel": 5},
        {"dose_count": 0},
        {"dose_count": 25},
        {"daily_amount": 0.5},
        {"daily_amount": "1e99999999"},
        {"daily_amount": "NaN"},
        {"daily_amount": "0"},
        {"first_dose_minute": 1440},
        {"operation": "apply"},
        {"method": "manual_dose"},
        {"daily_amount": "0.1234567"},
    ],
)
def test_dosing_request_rejects_ambiguous_amounts_and_unknown_operations(changes):
    value = {"method": "daily", "channel": 1, "daily_amount": "12", "dose_count": 4}
    with pytest.raises((ValueError, TypeError)):
        preview_dosing_plan({**value, **changes})


async def test_dosing_mqtt_in_observer_mode_does_not_need_coordinator_or_write_opt_in():
    router = _router(DOSING_PUMP_APP.product_key)
    topic = "jebao-flow/main/local/devices/pump/functions/channel_schedule/preview"
    assert router.command_topics == () and router.request_topics == (topic,)
    request = {
        "request_id": "dosing_preview_01",
        "plan": {"method": "daily", "channel": 4, "daily_amount": "12", "dose_count": 4},
    }
    response = await router.handle(topic, json.dumps(request))
    assert response.result["plan"]["channel"] == 4
    assert response.result["hardware_write"] is False
    assert response.result["dose_day_image"]["image_size"] == 96
    for forbidden in ({"operation": "apply"}, {"plan": {"method": "power", "value": True}}):
        with pytest.raises(LocalControlMqttError):
            await router.handle(topic, json.dumps({**request, **forbidden}))
    replay = await router.handle(topic, json.dumps(request))
    assert replay.result == response.result
