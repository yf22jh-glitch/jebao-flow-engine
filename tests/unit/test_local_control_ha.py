from datetime import UTC, datetime

from jebao_flow.groups.models import GroupConfig, GroupMember
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.operator_capture.app_catalog import APP_SHELL_FUNCTIONS
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _binding(member: str, identity: str, role: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=identity * 64,
        logical_role=role,
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _manifest() -> FreshInstallManifest:
    return FreshInstallManifest(
        generation="f" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(
            _binding("wavemaker_master", "a", "master"),
            _binding("wavemaker_slave", "b", "slave"),
        ),
        content=InstallContentBindings(
            app_catalog_sha256="c" * 64,
            protocol_schema_sha256=current_protocol_schema_sha256(),
            template_sha256=("d" * 64,),
        ),
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="c" * 64,
        template_sha256="d" * 64,
        schema_sha256=current_protocol_schema_sha256(),
        firmware_version="current",
        receipt_sha256=("e" * 64, "f" * 64),
    )


def test_unqualified_functions_are_english_diagnostics_without_commands() -> None:
    projection = project_home_assistant(
        _manifest(),
        build_local_control_catalog(),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )

    assert projection.entities
    assert all(entity.platform is HomeAssistantPlatform.SENSOR for entity in projection.entities)
    assert all(entity.command_topic is None for entity in projection.entities)
    manual_flow = next(
        entity
        for entity in projection.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )
    assert manual_flow.diagnostic_reason == (
        "awaiting_version_bound_ack_and_readback_qualification"
    )
    assert projection.public_dict()["control_count"] == 0
    assert sum(entity.logical_member == "local_system" for entity in projection.entities) == len(
        APP_SHELL_FUNCTIONS
    )
    assert str(projection.public_dict()).isascii()


def test_qualified_scalar_uses_native_clamped_number_contract() -> None:
    projection = project_home_assistant(
        _manifest(),
        build_local_control_catalog(
            (_qualification("manual_flow"),),
            current_content=_manifest().content,
            _qualification_verifier_for_test=lambda _item: frozenset({"a" * 64}),
        ),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )
    flow = next(
        entity
        for entity in projection.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )

    assert flow.platform is HomeAssistantPlatform.NUMBER
    assert flow.native_minimum == 30
    assert flow.native_maximum == 75
    assert flow.command_topic is not None
    assert flow.mqtt_discovery_payload()["min"] == 30
    assert flow.mqtt_discovery_payload()["max"] == 75
    assert flow.mqtt_discovery_payload()["qos"] == 0
    assert flow.mqtt_discovery_payload()["optimistic"] is False
    assert flow.mqtt_discovery_payload()["availability_mode"] == "all"
    assert [
        item["topic"] for item in flow.mqtt_discovery_payload()["availability"]
    ] == [
        "jebao-flow/main/local/availability",
        "jebao-flow/main/local/devices/wavemaker_master/availability",
    ]


def test_qualified_schedule_reset_is_an_explicit_nonoptimistic_button() -> None:
    projection = project_home_assistant(
        _manifest(),
        build_local_control_catalog(
            (_qualification("schedule_reset"),),
            current_content=_manifest().content,
            _qualification_verifier_for_test=lambda _item: frozenset({"a" * 64}),
        ),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )
    reset = next(
        entity
        for entity in projection.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "schedule_reset"
    )

    assert reset.platform is HomeAssistantPlatform.BUTTON
    assert reset.control_available is True
    assert reset.command_topic is not None
    assert reset.mqtt_discovery_payload()["payload_press"] == "PRESS"
    assert reset.mqtt_discovery_payload()["qos"] == 0


def test_priority_schedule_projects_independent_flow_for_every_slave_slot() -> None:
    manifest = _manifest()
    master, slave = manifest.devices
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slaves=(FreshSlaveSchedule(target=slave),),
    )
    projection = project_home_assistant(
        manifest,
        build_local_control_catalog(
            (_qualification("slave_per_slot_flow_extension"),),
            current_content=manifest.content,
            _qualification_verifier_for_test=lambda _item: frozenset({"b" * 64}),
        ),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        schedule_plans=(plan,),
    )
    slots = [entity for entity in projection.entities if entity.slot_index is not None]

    assert len(slots) == 48
    assert [entity.slot_index for entity in slots] == list(range(48))
    assert all(entity.logical_member == "wavemaker_slave" for entity in slots)
    assert all((entity.native_minimum, entity.native_maximum) == (30, 75) for entity in slots)
    assert all(entity.command_topic is not None for entity in slots)


def test_schedule_plan_must_match_fresh_install_binding() -> None:
    manifest = _manifest()
    master, slave = manifest.devices
    changed = slave.model_copy(update={"physical_identity_key": "0" * 64})
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.SYNC,
        master=master,
        slaves=(FreshSlaveSchedule(target=changed),),
    )

    try:
        project_home_assistant(
            manifest,
            build_local_control_catalog(
                (_qualification("slave_per_slot_flow_extension"),),
                current_content=manifest.content,
                _qualification_verifier_for_test=lambda _item: frozenset({"b" * 64}),
            ),
            discovery_prefix="homeassistant",
            topic_prefix="jebao-flow/main",
            schedule_plans=(plan,),
        )
    except ValueError as error:
        assert "current install manifest" in str(error)
    else:
        raise AssertionError("stale schedule binding was accepted")


def test_one_slave_cannot_be_projected_in_two_current_topologies() -> None:
    manifest = _manifest()
    master, slave = manifest.devices
    plans = tuple(
        FreshMasterSchedulePlan(
            linkage_mode=mode,
            master=master,
            slaves=(FreshSlaveSchedule(target=slave),),
        )
        for mode in (NativeLinkageMode.SYNC, NativeLinkageMode.ASYNC)
    )

    try:
        project_home_assistant(
            manifest,
            build_local_control_catalog(),
            discovery_prefix="homeassistant",
            topic_prefix="jebao-flow/main",
            schedule_plans=plans,
        )
    except ValueError as error:
        assert "multiple current schedule topologies" in str(error)
    else:
        raise AssertionError("one slave was projected into two current topologies")


def test_same_product_device_without_receipts_remains_diagnostic() -> None:
    manifest = _manifest()
    projection = project_home_assistant(
        manifest,
        build_local_control_catalog(
            (_qualification("manual_flow"),),
            current_content=manifest.content,
            _qualification_verifier_for_test=lambda _item: frozenset({"a" * 64}),
        ),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )
    master = next(
        item
        for item in projection.entities
        if item.logical_member == "wavemaker_master" and item.function == "manual_flow"
    )
    slave = next(
        item
        for item in projection.entities
        if item.logical_member == "wavemaker_slave" and item.function == "manual_flow"
    )

    assert master.control_available is True
    assert master.stage == "qualified"
    assert slave.control_available is False
    assert slave.stage == "app_static"
    assert slave.diagnostic_reason == "physical_device_not_qualified"


def test_group_actual_output_is_a_command_free_diagnostic_sensor() -> None:
    manifest = _manifest()
    group = GroupConfig(
        id="main_flow",
        name="Main Flow",
        members=(
            GroupMember(device="wavemaker_master"),
            GroupMember(device="wavemaker_slave"),
        ),
    )

    projection = project_home_assistant(
        manifest,
        build_local_control_catalog((), current_content=manifest.content),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        groups=(group,),
    )

    [entity] = projection.group_entities
    discovery = entity.mqtt_discovery_payload()
    assert entity.platform is HomeAssistantPlatform.SENSOR
    assert entity.group_id == "main_flow"
    assert entity.command_topic is None
    assert "command_topic" not in discovery
    assert discovery["entity_category"] == "diagnostic"
    assert discovery["availability_mode"] == "all"
    assert discovery["availability"][1]["topic"] == entity.availability_topic
    assert projection.public_dict()["group_entity_count"] == 1
    assert str(projection.public_dict()).isascii()


def test_observer_projection_suppresses_qualified_controls_without_losing_diagnostics() -> None:
    manifest = _manifest()
    master, slave = manifest.devices
    group = GroupConfig(
        id="main_flow",
        name="Main Flow",
        members=(
            GroupMember(device=master.logical_member),
            GroupMember(device=slave.logical_member),
        ),
    )
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slaves=(FreshSlaveSchedule(target=slave),),
    )
    catalog = build_local_control_catalog(
        (
            _qualification("power"),
            _qualification("manual_flow"),
            _qualification("slave_per_slot_flow_extension"),
        ),
        current_content=manifest.content,
        _qualification_verifier_for_test=lambda _item: frozenset(
            {master.physical_identity_key, slave.physical_identity_key}
        ),
    )
    control = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        schedule_plans=(plan,),
        groups=(group,),
    )
    explicit_control = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        schedule_plans=(plan,),
        groups=(group,),
        control_surface_enabled=True,
    )
    observer = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        schedule_plans=(plan,),
        groups=(group,),
        control_surface_enabled=False,
    )

    assert explicit_control.public_dict() == control.public_dict()
    assert control.control_surface_diagnostic_reason is None
    assert control.group_control_entities
    assert control.group_resume_entities
    assert len([entity for entity in control.entities if entity.slot_index is not None]) == 48

    control_flow = next(
        entity
        for entity in control.entities
        if entity.logical_member == master.logical_member and entity.function == "manual_flow"
    )
    observer_flow = next(
        entity
        for entity in observer.entities
        if entity.logical_member == master.logical_member and entity.function == "manual_flow"
    )
    assert control_flow.platform is HomeAssistantPlatform.NUMBER
    assert observer_flow.platform is HomeAssistantPlatform.SENSOR
    assert observer_flow.stage == control_flow.stage == "qualified"
    assert observer_flow.diagnostic_reason == control_flow.diagnostic_reason
    assert observer_flow.diagnostic_reason is None
    assert observer_flow.command_topic is None
    assert observer_flow.control_available is False

    control_reasons = {
        (entity.logical_member, entity.function): entity.diagnostic_reason
        for entity in control.entities
        if entity.slot_index is None
    }
    observer_reasons = {
        (entity.logical_member, entity.function): entity.diagnostic_reason
        for entity in observer.entities
    }
    assert observer_reasons == control_reasons

    assert observer.control_surface_diagnostic_reason == "runtime_mode_observer"
    assert str(observer.public_dict()).isascii()
    assert observer.public_dict()["control_count"] == 0
    assert observer.group_control_entities == ()
    assert observer.group_resume_entities == ()
    assert len(observer.group_entities) == 1
    assert not [entity for entity in observer.entities if entity.slot_index is not None]
    assert any(entity.function == "schedule" for entity in observer.entities)
    assert all(entity.command_topic is None for entity in observer.entities)
    assert all("command_topic" not in payload for _topic, payload in observer.discovery_records())
