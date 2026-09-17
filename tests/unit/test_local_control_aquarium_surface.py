"""Eight declared functions, scoped HA commands; not real-device qualification."""

import asyncio

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.mqtt import LocalControlMqttRouter
from jebao_flow.protocol.profiles import AQUARIUM_PUMP
from tests.unit.test_local_control_ha import _manifest, _qualification
from tests.unit.test_local_control_mqtt import _Coordinator, _OwnershipService

FUNCTIONS = frozenset(
    {
        "power",
        "schedule_enable",
        "transport_mode",
        "feed_switch",
        "motor_speed",
        "feeding_duration",
        "schedule",
        "device_clock_sync",
    }
)


def _surface():
    original = _manifest()
    devices = tuple(
        d.model_copy(update={"product_key": AQUARIUM_PUMP.product_key}) for d in original.devices
    )
    manifest = original.model_copy(update={"devices": devices})
    catalog = build_local_control_catalog(
        tuple(
            _qualification(f).model_copy(update={"product_key": AQUARIUM_PUMP.product_key})
            for f in FUNCTIONS
        ),
        current_content=manifest.content,
        _qualification_verifier_for_test=lambda _: frozenset({devices[0].physical_identity_key}),
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        topic_prefix="jebao/test",
        discovery_prefix="homeassistant",
        control_surface_enabled=True,
    )
    return manifest, catalog, projection


def _identity_surface():
    original = _manifest()
    devices = tuple(
        d.model_copy(update={"product_key": AQUARIUM_PUMP.product_key}) for d in original.devices
    )
    manifest = original.model_copy(update={"devices": devices})
    identity = devices[0].physical_identity_key
    catalog = build_local_control_catalog(
        (),
        current_content=manifest.content,
        identity_control_authorizations={
            (AQUARIUM_PUMP.product_key, function): (identity,)
            for function in FUNCTIONS - {"transport_mode"}
        },
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        topic_prefix="jebao/test",
        discovery_prefix="homeassistant",
        control_surface_enabled=True,
    )
    return manifest, catalog, projection


def test_seven_app_functions_bind_to_one_identity_only_and_transport_stays_diagnostic():
    manifest, catalog, projection = _surface()
    selected, other = manifest.devices
    exposed = {
        e.function: e
        for e in projection.entities
        if e.logical_member == selected.logical_member
        and (e.control_available or e.schedule_request_topic)
    }
    assert set(exposed) == FUNCTIONS - {"transport_mode"}
    assert sum(e.control_available for e in exposed.values()) == 6
    assert exposed["schedule"].schedule_request_topic
    assert exposed["device_clock_sync"].platform is HomeAssistantPlatform.BUTTON
    assert catalog.find(selected.product_key, "transport_mode").encoding_available
    assert not catalog.find(selected.product_key, "transport_mode").control_exposed
    assert "May Disconnect" in catalog.find(selected.product_key, "transport_mode").exposed_name
    assert not any(
        e.control_available or e.schedule_request_topic
        for e in projection.entities
        if e.logical_member == other.logical_member
    )


def test_identity_bound_surface_exposes_all_seven_executable_functions_without_artifacts():
    manifest, catalog, projection = _identity_surface()
    selected, other = manifest.devices
    exposed = {
        entity.function: entity
        for entity in projection.entities
        if entity.logical_member == selected.logical_member
        and (entity.control_available or entity.schedule_request_topic)
    }

    assert set(exposed) == FUNCTIONS - {"transport_mode"}
    assert all(
        catalog.find(selected.product_key, function).qualification is None for function in exposed
    )
    assert all(
        catalog.find(selected.product_key, function).control_exposed_for(
            selected.physical_identity_key
        )
        for function in exposed
    )
    assert not any(
        entity.control_available or entity.schedule_request_topic
        for entity in projection.entities
        if entity.logical_member == other.logical_member
    )


@pytest.mark.parametrize(
    "function,payload,value",
    [
        ("power", "ON", True),
        ("schedule_enable", "ON", True),
        ("feed_switch", "OFF", False),
        ("motor_speed", "40", 40),
        ("feeding_duration", "10", 10),
        ("device_clock_sync", "PRESS", None),
    ],
)
async def test_ha_routes_exact_function_to_selected_device(function, payload, value):
    manifest, catalog, projection = _surface()
    coordinator = _Coordinator([], [])
    ownership = _OwnershipService()
    ownership.operation_lock = asyncio.Lock()
    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(),
        coordinator=coordinator,
        ownership_service=ownership,
        operation_lock=ownership.operation_lock,
    )
    entity = next(
        e
        for e in projection.entities
        if e.logical_member == manifest.devices[0].logical_member and e.function == function
    )
    result = await router.handle(entity.command_topic, payload)
    (intent,) = coordinator.scalar
    assert intent.target == manifest.devices[0] and intent.function == function
    assert intent.value == value and result.result.accepted
