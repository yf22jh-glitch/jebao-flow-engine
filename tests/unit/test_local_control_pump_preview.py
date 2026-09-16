import hashlib
import json
from datetime import UTC, datetime

import pytest

from jebao_flow import local_control_cli
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.device_writer import build_device_clock_payload
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.models import FreshInstallManifest, InstallContentBindings
from jebao_flow.local_control.mqtt import (
    LocalControlMqttError,
    LocalControlMqttRouter,
    _parse_wavemaker_request,
)
from jebao_flow.local_control.pump_preview import ReturnPumpSchedule, preview_device_clock
from jebao_flow.protocol.schedule import (
    AQUARIUM_PUMP_PRODUCT_KEY,
    DC_PUMP_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRODUCT_KEY,
    decode_schedule,
)
from tests.unit.test_local_control_coordinator import _binding


def _slot(**changes):
    return {"slot_index": 3, "start": "06:00", "end": "12:00", **changes}


@pytest.mark.parametrize("mode,code", [("constant", 0), ("random", 3), ("feed", 4)])
def test_dc_schedule_preview_preserves_geometry_and_known_mode_mapping(mode, code):
    plan = ReturnPumpSchedule(
        family="dc_pro",
        slots=[
            _slot(
                mode=mode,
                flow=35,
                frequency=20,
                feed_time=10,
            )
        ],
    )
    image = plan.compile_image()
    assert len(image) == 384
    assert image[:24] == b"\xee" * 24
    assert image[24:32] == bytes([6, 0, 12, 0, code, 35, 20, 10])
    raw = bytearray(402)
    raw[9:393] = image
    decoded = decode_schedule(plan.product_key, raw, enabled=False)
    assert decoded.entries[0].mode == mode
    assert decoded.entries[0].parameters["flow"] == 35
    assert plan.preview()["wire_send_available"] is False


@pytest.mark.parametrize("mode", ["pulse", "sine", "unverified_1", "unverified_2"])
def test_dc_schedule_never_invents_ambiguous_mode_meanings(mode):
    with pytest.raises(ValueError, match="unverified"):
        ReturnPumpSchedule(family="dc_pro", slots=[_slot(mode=mode, flow=35)])


@pytest.mark.parametrize("end", ["06:00", "05:59"])
def test_dc_equal_or_backward_slot_matches_app_clear_contract(end):
    plan = ReturnPumpSchedule(family="dc_pro", slots=[_slot(mode="constant", flow=35, end=end)])
    assert plan.compile_image() == b"\xee" * 384
    assert plan.preview()["cleared_slot_indexes"] == [3]


@pytest.mark.parametrize(
    "mode,code,speed", [("stopped", 0, None), ("auto", 1, 35), ("feed", 2, None)]
)
def test_aquarium_preview_uses_six_bytes_and_app_zero_for_feed(mode, code, speed):
    plan = ReturnPumpSchedule(family="aquarium", slots=[_slot(mode=mode, speed=speed)])
    image = plan.compile_image()
    assert len(image) == 288
    assert image[18:24] == bytes([6, 0, 12, 0, code, speed or 0])
    raw = bytearray(302)
    raw[13:301] = image
    decoded = decode_schedule(plan.product_key, raw, enabled=False)
    assert decoded.entries[0].mode == mode
    assert plan.preview()["device_acceptance"] == "unverified"


@pytest.mark.parametrize(
    "slot",
    [
        _slot(mode="auto", speed=29),
        _slot(mode="auto", speed=True),
        _slot(mode="auto"),
        _slot(mode="feed", feed_time=10),
        _slot(mode="auto", speed=35, flow=35),
        _slot(mode="pulse", speed=35),
    ],
)
def test_aquarium_rejects_invented_or_cross_family_parameters(slot):
    with pytest.raises(ValueError):
        ReturnPumpSchedule(family="aquarium", slots=[slot])


@pytest.mark.parametrize("family,size", [("dc_pro", 384), ("aquarium", 288)])
def test_empty_return_schedule_is_offline_full_unused_image(family, size):
    plan = ReturnPumpSchedule(family=family, slots=[])
    assert plan.compile_image() == b"\xee" * size
    assert plan.preview()["hardware_write"] is False


@pytest.mark.parametrize(
    "product",
    [
        LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        LOCAL_WAVEMAKER_PRODUCT_KEY,
        DC_PUMP_PRO_PRODUCT_KEY,
        AQUARIUM_PUMP_PRODUCT_KEY,
    ],
)
def test_clock_preview_reuses_exact_app_builder_without_timezone_or_restore_claim(product):
    local = datetime.fromisoformat("2026-09-16T12:34:56.900000+09:00")
    preview = preview_device_clock(product, local)
    payload = build_device_clock_payload(product, local)
    assert preview["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert preview["encoded_local_time"] == "2026-09-16T12:34:56+09:00"
    assert preview["timezone_encoded"] is False
    assert preview["restore_previous_clock"] is False
    assert preview["wire_send_available"] is False
    assert preview["verification_contract"] == "not_defined"


def test_clock_preview_requires_caller_timezone():
    with pytest.raises(ValueError, match="offset"):
        preview_device_clock(LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, datetime(2026, 9, 16))


@pytest.mark.parametrize(
    "family,product",
    [
        ("dc_pro", DC_PUMP_PRO_PRODUCT_KEY),
        ("aquarium", AQUARIUM_PUMP_PRODUCT_KEY),
    ],
)
async def test_return_schedule_apply_parser_is_ready_but_unqualified_route_remains_closed(
    family,
    product,
):
    target = _binding("pump", "a").model_copy(update={"product_key": product})
    payload = json.dumps(
        {
            "operation": "apply",
            "request_id": "return_apply_0001",
            "schedule": {"family": family, "slots": []},
        }
    )
    operation, intent, plan = _parse_wavemaker_request(target, payload)
    assert operation == "apply" and intent.target == target
    assert isinstance(plan, ReturnPumpSchedule)
    assert intent.value == plan.model_dump(mode="json")
    router = _router(product)
    request_topic = "jebao-flow/main/local/devices/pump/functions/schedule/request"
    assert request_topic not in router.request_topics
    with pytest.raises(LocalControlMqttError):
        await router.handle(request_topic, payload)
    other = target.model_copy(update={"product_key": LOCAL_WAVEMAKER_PRO_PRODUCT_KEY})
    with pytest.raises(LocalControlMqttError, match="family"):
        _parse_wavemaker_request(other, payload)


def _router(product):
    target = _binding("pump", "a").model_copy(update={"product_key": product})
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 16, tzinfo=UTC),
        devices=(target,),
        content=InstallContentBindings(
            app_catalog_sha256="0" * 64,
            protocol_schema_sha256="1" * 64,
        ),
    )
    catalog = build_local_control_catalog()
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        control_surface_enabled=False,
        preview_requests_enabled=True,
    )
    return LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(),
        coordinator=None,
    )


@pytest.mark.parametrize(
    "family,product",
    [
        ("pro", LOCAL_WAVEMAKER_PRO_PRODUCT_KEY),
        ("bar", LOCAL_WAVEMAKER_PRODUCT_KEY),
        ("dc_pro", DC_PUMP_PRO_PRODUCT_KEY),
        ("aquarium", AQUARIUM_PUMP_PRODUCT_KEY),
    ],
)
async def test_observer_schedule_and_clock_preview_need_no_write_coordinator(family, product):
    router = _router(product)
    assert router.command_topics == ()
    assert len(router.request_topics) == 2
    topic = "jebao-flow/main/local/devices/pump/functions/schedule/preview"
    response = await router.handle(
        topic,
        json.dumps(
            {
                "request_id": "preview_0001",
                "schedule": {"family": family, "slots": []},
            }
        ),
    )
    assert response.result["hardware_write"] is False
    clock = await router.handle(
        topic.replace("schedule", "device_clock_sync"),
        json.dumps(
            {
                "request_id": "preview_0002",
                "local_time": "2026-09-16T12:00:00+09:00",
            }
        ),
    )
    assert clock.result["wire_send_available"] is False
    with pytest.raises(LocalControlMqttError):
        await router.handle(
            topic,
            json.dumps(
                {
                    "request_id": "preview_0003",
                    "operation": "apply",
                    "schedule": {"family": family, "slots": []},
                }
            ),
        )


def test_return_and_clock_preview_cli_do_not_prepare_live_runtime(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline preview must not open a device/config/journal")

    monkeypatch.setattr(local_control_cli, "_prepare_current", forbidden)
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"family": "dc_pro", "slots": []}))
    assert local_control_cli.main(["plan-return-pump", "--file", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["image_bytes"] == 384
    assert (
        local_control_cli.main(
            [
                "plan-device-clock",
                "--product-key",
                AQUARIUM_PUMP_PRODUCT_KEY,
                "--local-time",
                "2026-09-16T12:00:00+09:00",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["hardware_write"] is False
