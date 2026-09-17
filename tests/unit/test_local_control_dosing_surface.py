"""Four-channel Dosing HA surface and routed app-shaped commands."""

import json
from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.mqtt import LocalControlMqttError, LocalControlMqttRouter
from jebao_flow.local_control.state import LocalControlStateProjector
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.profiles import DOSING_PUMP
from tests.unit.test_local_control_ha import _manifest
from tests.unit.test_local_control_mqtt import _Coordinator

FUNCTIONS = frozenset(
    {
        "power",
        "manual_channel",
        "channel_timer_enable",
        "interval_days",
        "calibration_channel",
        "calibration",
        "calibration_amount",
        "channel_schedule",
    }
)


def _surface():
    original = _manifest()
    devices = tuple(
        device.model_copy(update={"product_key": DOSING_PUMP.product_key})
        for device in original.devices
    )
    manifest = original.model_copy(update={"devices": devices})
    selected = devices[0]
    catalog = build_local_control_catalog(
        (),
        current_content=manifest.content,
        identity_control_authorizations={
            (DOSING_PUMP.product_key, function): (selected.physical_identity_key,)
            for function in FUNCTIONS
        },
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        topic_prefix="jebao/test",
        discovery_prefix="homeassistant",
        control_surface_enabled=True,
        preview_requests_enabled=True,
    )
    return manifest, catalog, projection


def _raw_status(**changes) -> bytes:
    payload = build_control_payload(DOSING_PUMP, {"Calib1": 20, **changes})
    writable = payload[1 + DOSING_PUMP.attribute_flags_size :]
    return writable + bytes(DOSING_PUMP.raw_status_size - len(writable))


def _router():
    manifest, catalog, projection = _surface()
    coordinator = _Coordinator([], [])
    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(),
        coordinator=coordinator,
    )
    return manifest, catalog, projection, coordinator, router


def test_all_dosing_controls_are_identity_bound_and_channel_scoped():
    manifest, catalog, projection = _surface()
    selected, other = manifest.devices
    selected_entities = [
        entity
        for entity in projection.entities
        if entity.logical_member == selected.logical_member
        and (entity.control_available or entity.schedule_request_topic)
    ]

    assert len(selected_entities) == 20
    assert sum(entity.function == "manual_channel" for entity in selected_entities) == 4
    assert sum(entity.function == "channel_timer_enable" for entity in selected_entities) == 4
    assert sum(entity.function == "interval_days" for entity in selected_entities) == 4
    assert sum(entity.function == "channel_schedule" for entity in selected_entities) == 4
    assert {
        entity.channel_index
        for entity in selected_entities
        if entity.function == "channel_schedule"
    } == {1, 2, 3, 4}
    amount = next(entity for entity in selected_entities if entity.function == "calibration_amount")
    assert amount.platform is HomeAssistantPlatform.NUMBER
    assert amount.native_step == 10
    assert all(
        catalog.find(DOSING_PUMP.product_key, function).qualification is None
        for function in FUNCTIONS
    )
    assert not any(
        entity.control_available or entity.schedule_request_topic
        for entity in projection.entities
        if entity.logical_member == other.logical_member
    )


@pytest.mark.parametrize(
    "function,channel,payload,expected",
    [
        ("manual_channel", 3, "ON", {"channel": 3, "value": True}),
        ("channel_timer_enable", 2, "OFF", {"channel": 2, "value": False}),
        ("interval_days", 4, "7", {"channel": 4, "value": 7}),
    ],
)
async def test_channel_control_topics_preserve_the_selected_channel(
    function, channel, payload, expected
):
    manifest, _catalog, projection, coordinator, router = _router()
    entity = next(
        entity
        for entity in projection.entities
        if entity.logical_member == manifest.devices[0].logical_member
        and entity.function == function
        and entity.channel_index == channel
    )

    result = await router.handle(entity.command_topic, payload)

    (intent,) = coordinator.scalar
    assert intent.function == function and intent.value == expected
    assert intent.target == manifest.devices[0]
    assert result.result.accepted


async def test_schedule_apply_materializes_one_complete_channel_image():
    manifest, _catalog, projection, coordinator, router = _router()
    entity = next(
        entity
        for entity in projection.entities
        if entity.logical_member == manifest.devices[0].logical_member
        and entity.function == "channel_schedule"
        and entity.channel_index == 2
    )
    request = {
        "operation": "apply",
        "request_id": "dosing_apply_0001",
        "plan": {"method": "daily", "channel": 2, "daily_amount": "12", "dose_count": 4},
    }

    result = await router.handle(entity.schedule_request_topic, json.dumps(request))

    (intent,) = coordinator.scalar
    assert intent.function == "channel_schedule"
    assert intent.value["channel"] == 2
    assert len(intent.value["image"]) == 96
    assert result.result.accepted


async def test_schedule_apply_rejects_a_plan_for_another_channel():
    _manifest_value, _catalog, projection, _coordinator, router = _router()
    entity = next(
        entity
        for entity in projection.entities
        if entity.function == "channel_schedule" and entity.channel_index == 1
    )
    request = {
        "operation": "apply",
        "request_id": "dosing_apply_0002",
        "plan": {"method": "daily", "channel": 2, "daily_amount": "12", "dose_count": 4},
    }
    with pytest.raises(LocalControlMqttError, match="channel differs"):
        await router.handle(entity.schedule_request_topic, json.dumps(request))


def test_fresh_state_projects_each_channel_without_cross_channel_aliasing():
    manifest, catalog, projection = _surface()
    target = manifest.devices[0]
    publications = LocalControlStateProjector(
        manifest=manifest,
        projection=projection,
        catalog=catalog,
    ).project(
        target,
        _raw_status(
            channe2=True,
            Timer3ON=True,
            IntervalT4=7,
            CALSet="calibrate_3",
        ),
        observed_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    by_topic = {item.topic: item.payload for item in publications}

    assert (
        by_topic[
            f"jebao/test/local/devices/{target.logical_member}/functions/manual_channel/channels/2/state"
        ]
        == "ON"
    )
    assert (
        by_topic[
            f"jebao/test/local/devices/{target.logical_member}/functions/channel_timer_enable/channels/3/state"
        ]
        == "ON"
    )
    assert (
        by_topic[
            f"jebao/test/local/devices/{target.logical_member}/functions/interval_days/channels/4/state"
        ]
        == "7"
    )
    assert (
        by_topic[
            f"jebao/test/local/devices/{target.logical_member}/functions/calibration_channel/state"
        ]
        == "channel_3"
    )
