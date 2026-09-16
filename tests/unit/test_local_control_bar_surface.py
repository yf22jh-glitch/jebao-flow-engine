"""Bar app surface inventory and identity routing, not hardware qualification."""

import asyncio

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.mqtt import LocalControlMqttRouter
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER
from tests.unit.test_local_control_ha import _manifest, _qualification
from tests.unit.test_local_control_mqtt import _Coordinator, _OwnershipService

FUNCTIONS = frozenset(
    {
        "power",
        "schedule_enable",
        "feed_switch",
        "pulse_or_tide",
        "linkage_role",
        "manual_mode",
        "manual_flow",
        "manual_frequency",
        "feeding_duration",
        "schedule",
        "device_clock_sync",
    }
)


def _surface():
    original = _manifest()
    devices = tuple(
        d.model_copy(update={"product_key": LOCAL_WAVEMAKER.product_key}) for d in original.devices
    )
    manifest = original.model_copy(update={"devices": devices})
    catalog = build_local_control_catalog(
        tuple(
            _qualification(f).model_copy(update={"product_key": LOCAL_WAVEMAKER.product_key})
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


def test_bar_app_has_ten_controls_and_one_schedule_api_for_only_the_bound_identity():
    manifest, _, projection = _surface()
    selected, other = manifest.devices
    exposed = {
        e.function: e
        for e in projection.entities
        if e.logical_member == selected.logical_member
        and (e.control_available or e.schedule_request_topic)
    }
    assert set(exposed) == FUNCTIONS
    assert sum(e.control_available for e in exposed.values()) == 10
    assert exposed["schedule"].schedule_request_topic
    assert exposed["device_clock_sync"].platform is HomeAssistantPlatform.BUTTON
    assert not any(
        e.control_available or e.schedule_request_topic
        for e in projection.entities
        if e.logical_member == other.logical_member
    )


@pytest.mark.parametrize(
    ("function", "payload", "expected"),
    (
        ("power", "ON", True),
        ("schedule_enable", "ON", True),
        ("feed_switch", "ON", True),
        ("pulse_or_tide", "OFF", False),
        ("linkage_role", "slave", "slave"),
        ("manual_mode", "classic", "classic"),
        ("manual_flow", "40", 40),
        ("manual_frequency", "20", 20),
        ("feeding_duration", "10", 10),
        ("device_clock_sync", "PRESS", None),
    ),
)
async def test_bar_ha_command_keeps_the_selected_identity_and_function(function, payload, expected):
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
