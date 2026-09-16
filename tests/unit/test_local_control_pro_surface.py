"""Pro app controls and routing; software coverage does not answer native Q1/Q2."""

import asyncio

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.mqtt import LocalControlMqttRouter
from tests.unit.test_local_control_ha import _manifest, _qualification
from tests.unit.test_local_control_mqtt import _Coordinator, _OwnershipService

APP_FUNCTIONS = frozenset(
    {
        "power",
        "manual_flow",
        "manual_mode",
        "manual_frequency",
        "feeding_duration",
        "schedule_enable",
        "linkage_role",
        "custom_wave_frequency",
        "schedule",
        "schedule_reset",
        "device_clock_sync",
        "slave_common_flow",
    }
)


def _surface():
    manifest = _manifest()
    catalog = build_local_control_catalog(
        tuple(_qualification(f) for f in APP_FUNCTIONS),
        current_content=manifest.content,
        _qualification_verifier_for_test=lambda _: frozenset(
            {
                manifest.devices[0].physical_identity_key,
            }
        ),
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        topic_prefix="jebao/test",
        discovery_prefix="homeassistant",
        control_surface_enabled=True,
    )
    return manifest, catalog, projection


def test_all_pro_app_controls_are_exposed_only_for_the_bound_identity():
    manifest, catalog, projection = _surface()
    selected, other = manifest.devices
    exposed = {
        e.function: e
        for e in projection.entities
        if e.logical_member == selected.logical_member
        and (e.control_available or e.schedule_request_topic)
    }
    assert set(exposed) == APP_FUNCTIONS
    assert sum(e.control_available for e in exposed.values()) == 11
    assert exposed["schedule"].schedule_request_topic
    for f in ("device_clock_sync", "schedule_reset"):
        assert exposed[f].platform is HomeAssistantPlatform.BUTTON
    assert exposed["slave_common_flow"].platform is HomeAssistantPlatform.NUMBER
    assert not any(
        e.control_available or e.schedule_request_topic
        for e in projection.entities
        if e.logical_member == other.logical_member
    )
    extension = catalog.find(selected.product_key, "slave_per_slot_flow_extension")
    assert extension.encoding_available and not extension.control_exposed
    assert not any(e.slot_index is not None for e in projection.entities)


@pytest.mark.parametrize(
    ("function", "payload", "expected"),
    (
        ("power", "ON", True),
        ("manual_flow", "40", 40),
        ("manual_mode", "sine", "sine"),
        ("manual_frequency", "20", 20),
        ("feeding_duration", "10", 10),
        ("schedule_enable", "ON", True),
        ("linkage_role", "independent", "independent"),
        ("custom_wave_frequency", "5", 5),
        ("slave_common_flow", "39", 39),
        ("schedule_reset", "PRESS", None),
        ("device_clock_sync", "PRESS", None),
    ),
)
async def test_pro_ha_commands_reach_the_exact_identity_and_app_function(
    function,
    payload,
    expected,
):
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
    assert intent.value == expected and result.result.accepted
    if expected is None:
        assert result.state_value is None
