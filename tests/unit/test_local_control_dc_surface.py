"""DC app-function inventory and MQTT routing; no hardware acceptance claims."""

import asyncio

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.mqtt import LocalControlMqttRouter
from jebao_flow.protocol.profiles import DC_PUMP_PRO
from tests.unit.test_local_control_ha import _manifest, _qualification
from tests.unit.test_local_control_mqtt import _Coordinator, _OwnershipService

FUNCTIONS = frozenset(
    {
        "power",
        "manual_flow",
        "manual_mode",
        "manual_frequency",
        "feeding_duration",
        "schedule_enable",
        "manual_enable",
        "schedule",
        "schedule_reset",
        "device_clock_sync",
    }
)


def _surface():
    original = _manifest()
    devices = tuple(
        d.model_copy(update={"product_key": DC_PUMP_PRO.product_key}) for d in original.devices
    )
    manifest = original.model_copy(update={"devices": devices})
    catalog = build_local_control_catalog(
        tuple(
            _qualification(f).model_copy(update={"product_key": DC_PUMP_PRO.product_key})
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


def test_all_ten_dc_app_functions_have_a_surface_and_only_the_bound_device_is_exposed():
    manifest, _, projection = _surface()
    selected, other = manifest.devices
    exposed = {
        e.function: e
        for e in projection.entities
        if e.logical_member == selected.logical_member
        and (e.control_available or e.schedule_request_topic)
    }
    assert set(exposed) == FUNCTIONS
    assert sum(e.control_available for e in exposed.values()) == 9
    assert exposed["schedule"].schedule_request_topic
    assert exposed["device_clock_sync"].platform is HomeAssistantPlatform.BUTTON
    assert exposed["schedule_reset"].platform is HomeAssistantPlatform.BUTTON
    for function in ("schedule_enable", "manual_enable"):
        assert exposed[function].platform is HomeAssistantPlatform.SWITCH
        assert "Controls Power" in exposed[function].name
    assert not any(
        e.control_available or e.schedule_request_topic
        for e in projection.entities
        if e.logical_member == other.logical_member
    )


@pytest.mark.parametrize(
    ("function", "payload", "expected"),
    (
        ("power", "ON", True),
        ("manual_flow", "40", 40),
        ("manual_mode", "sine", "sine"),
        ("manual_frequency", "20", 20),
        ("feeding_duration", "10", 10),
        ("schedule_enable", "ON", True),
        ("manual_enable", "ON", True),
        ("schedule_reset", "PRESS", None),
        ("device_clock_sync", "PRESS", None),
    ),
)
async def test_dc_ha_commands_reach_the_same_identity_and_exact_function(
    function, payload, expected
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
    assert ownership.power_on_requests == [
        function in {"power", "manual_enable", "schedule_enable"}
    ]
    if expected is None:
        assert result.state_value is None
