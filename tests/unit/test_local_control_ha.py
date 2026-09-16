from datetime import UTC, datetime

import pytest

import jebao_flow.local_control.ha as ha_module
from jebao_flow.groups.models import GroupConfig, GroupMember
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import (
    GroupControlHomeAssistantEntityDescriptor,
    GroupHomeAssistantEntityDescriptor,
    GroupMemberResumeHomeAssistantEntityDescriptor,
    HomeAssistantEntityDescriptor,
    HomeAssistantPlatform,
    HomeAssistantProjection,
    project_home_assistant,
)
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.operator_capture.app_catalog import (
    APP_SHELL_FUNCTIONS,
    declared_app_integer_range,
)
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


@pytest.mark.parametrize("enabled", (False, True))
def test_bar_timer_switch_and_schedule_request_require_qualified_identity(enabled):
    bar = "1d8c63eaccac4205b92c84d77d5a08fb"
    manifest = _manifest()
    manifest = manifest.model_copy(
        update={
            "devices": tuple(
                device.model_copy(update={"product_key": bar}) for device in manifest.devices
            )
        }
    )
    qualified, other = manifest.devices
    catalog = build_local_control_catalog(
        tuple(
            _qualification(function).model_copy(update={"product_key": bar})
            for function in ("schedule_enable", "schedule")
        ),
        current_content=manifest.content,
        _qualification_verifier_for_test=lambda _item: frozenset({qualified.physical_identity_key}),
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        topic_prefix="jebao/test",
        discovery_prefix="homeassistant",
        control_surface_enabled=enabled,
    )
    for member, expected in ((qualified.logical_member, enabled), (other.logical_member, False)):
        timer = next(
            e
            for e in projection.entities
            if e.logical_member == member and e.function == "schedule_enable"
        )
        schedule = next(
            e
            for e in projection.entities
            if e.logical_member == member and e.function == "schedule"
        )
        assert timer.control_available is expected
        assert (timer.command_topic is not None) is expected
        assert (schedule.schedule_request_topic is not None) is expected
        if expected:
            assert timer.platform is HomeAssistantPlatform.SWITCH


_ProjectedEntity = (
    HomeAssistantEntityDescriptor
    | GroupHomeAssistantEntityDescriptor
    | GroupControlHomeAssistantEntityDescriptor
    | GroupMemberResumeHomeAssistantEntityDescriptor
)


def _projected_entities(projection: HomeAssistantProjection) -> tuple[_ProjectedEntity, ...]:
    return (
        *projection.entities,
        *projection.group_entities,
        *projection.group_control_entities,
        *projection.group_resume_entities,
    )


def _device_identifier(entity: _ProjectedEntity) -> str:
    device = entity.mqtt_discovery_payload()["device"]
    assert isinstance(device, dict)
    identifiers = device["identifiers"]
    assert isinstance(identifiers, list)
    [identifier] = identifiers
    assert isinstance(identifier, str)
    return identifier


def _qualified_projection(
    topic_prefix: str,
    *,
    discovery_prefix: str = "homeassistant",
    control_surface_enabled: bool = True,
) -> HomeAssistantProjection:
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
    return project_home_assistant(
        manifest,
        catalog,
        discovery_prefix=discovery_prefix,
        topic_prefix=topic_prefix,
        schedule_plans=(plan,),
        groups=(group,),
        control_surface_enabled=control_surface_enabled,
    )


def _unqualified_projection(
    topic_prefix: str,
    *,
    discovery_prefix: str = "homeassistant",
    control_surface_enabled: bool = True,
) -> HomeAssistantProjection:
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
    return project_home_assistant(
        manifest,
        build_local_control_catalog((), current_content=manifest.content),
        discovery_prefix=discovery_prefix,
        topic_prefix=topic_prefix,
        schedule_plans=(plan,),
        groups=(group,),
        control_surface_enabled=control_surface_enabled,
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


def test_qualified_scalar_uses_app_declared_number_contract() -> None:
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
    declared_range = declared_app_integer_range(
        LOCAL_WAVEMAKER_PRO.product_key,
        "manual_flow",
    )
    assert (flow.native_minimum, flow.native_maximum) == declared_range
    assert flow.command_topic is not None
    assert (
        flow.mqtt_discovery_payload()["min"],
        flow.mqtt_discovery_payload()["max"],
    ) == declared_range
    assert flow.mqtt_discovery_payload()["qos"] == 0
    assert flow.mqtt_discovery_payload()["optimistic"] is False
    assert flow.mqtt_discovery_payload()["availability_mode"] == "all"
    assert [item["topic"] for item in flow.mqtt_discovery_payload()["availability"]] == [
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
    assert all(
        (entity.native_minimum, entity.native_maximum)
        == declared_app_integer_range(LOCAL_WAVEMAKER_PRO.product_key, "manual_flow")
        for entity in slots
    )
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


def test_owned_discovery_variants_partition_across_mode_and_qualification() -> None:
    control = _qualified_projection("jebao-flow/main")
    observer = _qualified_projection(
        "jebao-flow/main",
        control_surface_enabled=False,
    )
    unqualified = _unqualified_projection("jebao-flow/main")
    partitions: list[tuple[set[str], set[str]]] = []
    for projection in (control, observer, unqualified):
        current = {topic for topic, _payload in projection.discovery_records()}
        tombstones = set(projection.discovery_tombstone_topics())
        assert current.isdisjoint(tombstones)
        assert projection.discovery_tombstone_topics() == tuple(sorted(tombstones))
        partitions.append((current, tombstones))

    control_current, control_tombstones = partitions[0]
    observer_current, observer_tombstones = partitions[1]
    assert control_current | control_tombstones == observer_current | observer_tombstones
    assert control_current | control_tombstones == partitions[2][0] | partitions[2][1]

    control_flow = next(
        entity
        for entity in control.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )
    observer_flow = next(
        entity
        for entity in observer.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )
    assert control_flow.unique_id == observer_flow.unique_id
    number_topic = f"{control.discovery_prefix}/number/{control_flow.unique_id}/config"
    sensor_topic = f"{control.discovery_prefix}/sensor/{control_flow.unique_id}/config"
    assert number_topic in control_current
    assert sensor_topic in control_tombstones
    assert sensor_topic in observer_current
    assert number_topic in observer_tombstones

    slot = next(entity for entity in control.entities if entity.slot_index == 0)
    slot_topic = f"{control.discovery_prefix}/number/{slot.unique_id}/config"
    [group_control] = control.group_control_entities
    group_control_topic = f"{control.discovery_prefix}/switch/{group_control.unique_id}/config"
    resume_topics = {
        f"{control.discovery_prefix}/button/{entity.unique_id}/config"
        for entity in control.group_resume_entities
    }
    assert slot_topic in control_current
    assert slot_topic in observer_tombstones
    assert group_control_topic in control_current
    assert group_control_topic in observer_tombstones
    assert resume_topics <= control_current
    assert resume_topics <= observer_tombstones

    [actual] = control.group_entities
    actual_topic = f"{control.discovery_prefix}/sensor/{actual.unique_id}/config"
    system = next(entity for entity in control.entities if entity.logical_member == "local_system")
    system_topic = f"{control.discovery_prefix}/sensor/{system.unique_id}/config"
    assert {actual_topic, system_topic} <= control_current & observer_current
    assert {actual_topic, system_topic}.isdisjoint(control_tombstones | observer_tombstones)
    assert not any(
        "/binary_sensor/" in topic
        for current, tombstones in partitions
        for topic in current | tombstones
    )


def test_owned_discovery_tombstones_are_current_namespace_only_and_bounded() -> None:
    projection = _qualified_projection("jebao-flow/main")
    other = _qualified_projection("jebao-flow/other")
    [namespace] = {entity.instance_namespace for entity in _projected_entities(projection)}
    current = {topic for topic, _payload in projection.discovery_records()}
    tombstones = set(projection.discovery_tombstone_topics())
    all_variants = current | tombstones

    for topic in tombstones:
        relative = topic.removeprefix(f"{projection.discovery_prefix}/")
        platform, object_id, config = relative.split("/")
        assert topic == (f"{projection.discovery_prefix}/{platform}/{object_id}/{config}")
        assert config == "config"
        assert object_id.count(namespace) == 1
    assert all(topic.startswith(f"{projection.discovery_prefix}/") for topic in tombstones)
    assert tombstones.isdisjoint(topic for topic, _payload in other.discovery_records())
    assert tombstones.isdisjoint(other.discovery_tombstone_topics())
    runtime_topics = {
        projection.global_availability_topic,
        f"{projection.topic_prefix}/local/status",
        f"{projection.topic_prefix}/local/groups/status",
    }
    for _topic, payload in projection.discovery_records():
        for key in (
            "state_topic",
            "command_topic",
            "availability_topic",
            "json_attributes_topic",
        ):
            value = payload.get(key)
            if isinstance(value, str):
                runtime_topics.add(value)
        availability = payload.get("availability")
        if isinstance(availability, list):
            runtime_topics.update(
                item["topic"]
                for item in availability
                if isinstance(item, dict) and isinstance(item.get("topic"), str)
            )
    assert tombstones.isdisjoint(runtime_topics)

    manifest = _manifest()
    catalog = build_local_control_catalog(
        (
            _qualification("power"),
            _qualification("manual_flow"),
            _qualification("slave_per_slot_flow_extension"),
        ),
        current_content=manifest.content,
        _qualification_verifier_for_test=lambda _item: frozenset(
            device.physical_identity_key for device in manifest.devices
        ),
    )
    device_function_count = sum(
        descriptor.product_key == device.product_key
        for device in manifest.devices
        for descriptor in catalog.functions
    )
    system_function_count = sum(descriptor.product_key is None for descriptor in catalog.functions)
    group_member_count = 2
    upper_bound = (
        (2 * device_function_count) + system_function_count + 48 + 1 + 1 + group_member_count
    )
    assert len(all_variants) <= upper_bound

    manifest = _manifest()
    reduced = project_home_assistant(
        manifest,
        build_local_control_catalog((), current_content=manifest.content),
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )
    reduced_variants = {topic for topic, _payload in reduced.discovery_records()} | set(
        reduced.discovery_tombstone_topics()
    )
    topology_entities = (
        *(entity for entity in projection.entities if entity.slot_index is not None),
        *projection.group_entities,
        *projection.group_control_entities,
        *projection.group_resume_entities,
    )
    full_topology_only = {
        f"{projection.discovery_prefix}/{entity.platform.value}/{entity.unique_id}/config"
        for entity in topology_entities
    }
    assert full_topology_only
    assert full_topology_only.isdisjoint(reduced_variants)


def test_device_variant_candidates_reuse_the_existing_platform_fold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    catalog = build_local_control_catalog((), current_content=manifest.content)
    expected_calls = sorted(
        (descriptor.product_key, descriptor.function)
        for device in manifest.devices
        for descriptor in catalog.functions
        if descriptor.product_key == device.product_key
    )
    actual_calls: list[tuple[str | None, str]] = []
    original = ha_module._platform_for

    def tracking_platform(descriptor, *, control_available):
        if control_available:
            actual_calls.append((descriptor.product_key, descriptor.function))
        return original(descriptor, control_available=control_available)

    monkeypatch.setattr(ha_module, "_platform_for", tracking_platform)
    projection = _unqualified_projection(
        "jebao-flow/main",
        control_surface_enabled=False,
    )

    assert sorted(actual_calls) == expected_calls
    assert not any("/binary_sensor/" in topic for topic in projection.discovery_tombstone_topics())


def test_topic_prefix_namespaces_every_discovery_identity_family() -> None:
    left = _qualified_projection("jebao-flow/left")
    right = _qualified_projection("jebao-flow/right")

    assert any(
        entity.logical_member != "local_system" and entity.slot_index is None
        for entity in left.entities
    )
    assert any(entity.logical_member == "local_system" for entity in left.entities)
    assert any(entity.slot_index is not None for entity in left.entities)
    assert left.group_entities
    assert left.group_control_entities
    assert left.group_resume_entities

    identity_sets: list[tuple[set[str], set[str], set[str]]] = []
    for projection in (left, right):
        entities = _projected_entities(projection)
        records = projection.discovery_records()
        [namespace] = {entity.instance_namespace for entity in entities}
        discovery_topics: set[str] = set()
        unique_ids: set[str] = set()
        device_identifiers: set[str] = set()

        assert len(records) == len(entities)
        for entity, (topic, payload) in zip(entities, records, strict=True):
            object_id = topic.rsplit("/", 2)[-2]
            identifier = _device_identifier(entity)
            assert topic == (
                f"{projection.discovery_prefix}/{entity.platform.value}/{entity.unique_id}/config"
            )
            assert payload["unique_id"] == entity.unique_id == object_id
            assert entity.unique_id.count(namespace) == 1
            assert object_id.count(namespace) == 1
            assert identifier.count(namespace) == 1
            discovery_topics.add(topic)
            unique_ids.add(entity.unique_id)
            device_identifiers.add(identifier)
        identity_sets.append((discovery_topics, unique_ids, device_identifiers))

    left_sets, right_sets = identity_sets
    assert all(
        left_values.isdisjoint(right_values)
        for left_values, right_values in zip(
            left_sets,
            right_sets,
            strict=True,
        )
    )


def test_topic_prefix_namespace_is_golden_deterministic_and_discovery_prefix_independent() -> None:
    expected = "instance_ce1ffb4517be8e9224bd3b7aa88921621397bc66eb679d38817a78f24bdb1e49"
    first = _qualified_projection("jebao-flow/main")
    repeated = _qualified_projection("jebao-flow/main")
    other_discovery = _qualified_projection(
        "jebao-flow/main",
        discovery_prefix="otherassistant",
    )

    assert len(expected) == 73
    assert expected.isascii()
    assert {entity.instance_namespace for entity in _projected_entities(first)} == {expected}
    assert first.public_dict() == repeated.public_dict()
    assert first.discovery_records() == repeated.discovery_records()
    assert {entity.unique_id for entity in _projected_entities(first)} == {
        entity.unique_id for entity in _projected_entities(other_discovery)
    }
    assert {_device_identifier(entity) for entity in _projected_entities(first)} == {
        _device_identifier(entity) for entity in _projected_entities(other_discovery)
    }
    assert {topic for topic, _payload in first.discovery_records()}.isdisjoint(
        topic for topic, _payload in other_discovery.discovery_records()
    )


def test_namespaced_devices_preserve_grouping_and_existing_runtime_topics() -> None:
    prefix = "jebao-flow/main"
    projection = _qualified_projection(prefix)
    namespace = next(iter(_projected_entities(projection))).instance_namespace
    device_identifiers: dict[str, set[str]] = {}
    for entity in projection.entities:
        device_identifiers.setdefault(entity.logical_member, set()).add(_device_identifier(entity))
    assert all(len(identifiers) == 1 for identifiers in device_identifiers.values())
    assert device_identifiers["local_system"] == {f"jebao_flow_local_{namespace}_local_system"}

    group_entities = (
        *projection.group_entities,
        *projection.group_control_entities,
        *projection.group_resume_entities,
    )
    group_identifiers = {_device_identifier(entity) for entity in group_entities}
    assert group_identifiers == {f"jebao_flow_local_{namespace}_group_main_flow"}
    assert all(
        _device_identifier(entity) != next(iter(device_identifiers[entity.logical_member]))
        for entity in projection.group_resume_entities
    )

    flow = next(
        entity
        for entity in projection.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )
    slot = next(entity for entity in projection.entities if entity.slot_index == 0)
    system = next(
        entity for entity in projection.entities if entity.logical_member == "local_system"
    )
    [actual] = projection.group_entities
    [control] = projection.group_control_entities
    resume = projection.group_resume_entities[0]

    assert flow.state_topic == (
        f"{prefix}/local/devices/wavemaker_master/functions/manual_flow/state"
    )
    assert flow.command_topic == (
        f"{prefix}/local/devices/wavemaker_master/functions/manual_flow/set"
    )
    assert slot.state_topic == (
        f"{prefix}/local/schedules/wavemaker_master_async/slaves/wavemaker_slave/slots/0/flow/state"
    )
    assert slot.command_topic == (
        f"{prefix}/local/schedules/wavemaker_master_async/slaves/wavemaker_slave/slots/0/flow/set"
    )
    assert system.state_topic == f"{prefix}/local/system/functions/{system.function}/state"
    assert actual.state_topic == f"{prefix}/local/groups/main_flow/actual-output/state"
    assert actual.availability_topic == (
        f"{prefix}/local/groups/main_flow/actual-output/availability"
    )
    assert control.command_topic == f"{prefix}/local/groups/main_flow/control/set"
    assert resume.command_topic.endswith(
        f"/local/groups/main_flow/members/{resume.logical_member}/resume/set"
    )
    assert projection.global_availability_topic == f"{prefix}/local/availability"
    assert projection.device_availability_topic("wavemaker_master") == (
        f"{prefix}/local/devices/wavemaker_master/availability"
    )


def test_instance_namespace_is_mode_independent_without_restoring_observer_controls() -> None:
    control = _qualified_projection("jebao-flow/main")
    observer = _qualified_projection(
        "jebao-flow/main",
        control_surface_enabled=False,
    )
    control_entities = _projected_entities(control)
    observer_entities = _projected_entities(observer)
    [namespace] = {entity.instance_namespace for entity in control_entities}

    assert {entity.instance_namespace for entity in observer_entities} == {namespace}
    assert {_device_identifier(entity) for entity in observer_entities} == {
        _device_identifier(entity) for entity in control_entities
    }

    control_common = {
        (entity.logical_member, entity.function): (
            entity.unique_id,
            _device_identifier(entity),
        )
        for entity in control.entities
        if entity.slot_index is None
    }
    observer_common = {
        (entity.logical_member, entity.function): (
            entity.unique_id,
            _device_identifier(entity),
        )
        for entity in observer.entities
    }
    assert observer_common == control_common
    assert observer.group_entities[0].unique_id == control.group_entities[0].unique_id
    assert observer.group_control_entities == ()
    assert observer.group_resume_entities == ()
    assert not [entity for entity in observer.entities if entity.slot_index is not None]
    assert all(entity.command_topic is None for entity in observer.entities)
    assert observer.control_surface_diagnostic_reason == "runtime_mode_observer"


def test_invalid_topic_prefix_is_rejected_before_namespace_or_entity_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid topic prefix reached namespace or entity construction")

    monkeypatch.setattr(ha_module, "_discovery_instance_namespace", unexpected_call)
    monkeypatch.setattr(ha_module, "_project_device_function", unexpected_call)

    with pytest.raises(ValueError, match="MQTT topic prefix"):
        project_home_assistant(
            _manifest(),
            build_local_control_catalog(),
            discovery_prefix="homeassistant",
            topic_prefix="invalid prefix",
        )


def test_instance_namespace_hashes_the_exact_unnormalized_topic_prefix() -> None:
    plain = _qualified_projection("a/b")
    trailing_slash = _qualified_projection("a/b/")
    plain_namespace = next(iter(_projected_entities(plain))).instance_namespace
    trailing_namespace = next(iter(_projected_entities(trailing_slash))).instance_namespace
    plain_flow = next(
        entity
        for entity in plain.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )
    trailing_flow = next(
        entity
        for entity in trailing_slash.entities
        if entity.logical_member == "wavemaker_master" and entity.function == "manual_flow"
    )

    assert plain_namespace != trailing_namespace
    assert plain_flow.state_topic == (
        "a/b/local/devices/wavemaker_master/functions/manual_flow/state"
    )
    assert plain_flow.command_topic == (
        "a/b/local/devices/wavemaker_master/functions/manual_flow/set"
    )
    assert trailing_flow.state_topic == (
        "a/b//local/devices/wavemaker_master/functions/manual_flow/state"
    )
    assert trailing_flow.command_topic == (
        "a/b//local/devices/wavemaker_master/functions/manual_flow/set"
    )
