import json
from datetime import UTC, datetime

import pytest

from jebao_flow.groups.models import GroupConfig, GroupMember
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.group_command import group_control_qualification
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _binding(member: str, digit: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=digit * 64,
        logical_role=member,
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 9, tzinfo=UTC),
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="a" * 64,
        template_sha256="c" * 64,
        schema_sha256="b" * 64,
        firmware_version="current",
        receipt_sha256=("d" * 64, "e" * 64),
    )


def _inputs(
    *,
    power_keys: frozenset[str],
    manual_flow_keys: frozenset[str],
):
    group = GroupConfig(
        id="main_flow",
        name="Main Flow",
        members=(GroupMember(device="left"), GroupMember(device="right")),
    )
    content = InstallContentBindings(
        app_catalog_sha256="a" * 64,
        protocol_schema_sha256="b" * 64,
        template_sha256=("c" * 64,),
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 9, tzinfo=UTC),
        devices=(_binding("left", "1"), _binding("right", "2")),
        content=content,
    )
    by_function = {
        "power": power_keys,
        "manual_flow": manual_flow_keys,
    }
    catalog = build_local_control_catalog(
        (_qualification("power"), _qualification("manual_flow")),
        current_content=content,
        _qualification_verifier_for_test=lambda item: by_function[item.function],
    )
    return group, manifest, catalog


@pytest.mark.parametrize(
    ("power_keys", "manual_flow_keys", "expected_missing", "switch_count"),
    [
        pytest.param(
            frozenset({"1" * 64, "2" * 64}),
            frozenset({"1" * 64, "2" * 64}),
            (),
            1,
            id="two-of-two",
        ),
        pytest.param(
            frozenset({"1" * 64, "2" * 64}),
            frozenset({"1" * 64}),
            (("right", "manual_flow"),),
            0,
            id="power-two-flow-one",
        ),
        pytest.param(
            frozenset({"1" * 64}),
            frozenset({"1" * 64, "2" * 64}),
            (("right", "power"),),
            0,
            id="power-one-flow-two",
        ),
        pytest.param(
            frozenset({"1" * 64}),
            frozenset({"2" * 64}),
            (("left", "manual_flow"), ("right", "power")),
            0,
            id="zero-members-have-two-of-two",
        ),
    ],
)
def test_group_switch_requires_exact_two_of_two_member_qualification(
    power_keys: frozenset[str],
    manual_flow_keys: frozenset[str],
    expected_missing: tuple[tuple[str, str], ...],
    switch_count: int,
) -> None:
    group, manifest, catalog = _inputs(
        power_keys=power_keys,
        manual_flow_keys=manual_flow_keys,
    )

    qualification = group_control_qualification(group, manifest, catalog)
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
        groups=(group,),
    )

    assert qualification.qualified is (switch_count == 1)
    assert qualification.missing == expected_missing
    assert qualification.diagnostic_reason == (
        None if switch_count else "physical_device_not_qualified"
    )
    assert len(projection.group_entities) == 1
    assert len(projection.group_control_entities) == switch_count
    assert len(projection.group_resume_entities) == switch_count * 2
    assert projection.group_entities[0].command_topic is None
    encoded = json.dumps(qualification.public_dict(), ensure_ascii=True)
    assert encoded.isascii()
    assert manifest.devices[0].physical_identity_key not in encoded


def test_qualified_group_switch_is_non_optimistic_and_separate_from_actual_truth() -> None:
    keys = frozenset({"1" * 64, "2" * 64})
    group, manifest, catalog = _inputs(power_keys=keys, manual_flow_keys=keys)

    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
        groups=(group,),
    )

    [actual] = projection.group_entities
    [control] = projection.group_control_entities
    discovery = control.mqtt_discovery_payload()
    assert actual.unique_id != control.unique_id
    assert actual.command_topic is None
    assert control.command_topic.endswith("/local/groups/main_flow/control/set")
    assert discovery["optimistic"] is False
    assert discovery["qos"] == 0
    assert discovery["state_topic"] != actual.state_topic


def test_qualified_group_projects_one_stateless_resume_button_per_member() -> None:
    keys = frozenset({"1" * 64, "2" * 64})
    group, manifest, catalog = _inputs(power_keys=keys, manual_flow_keys=keys)

    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
        groups=(group,),
    )

    assert tuple(entity.logical_member for entity in projection.group_resume_entities) == (
        "left",
        "right",
    )
    for entity in projection.group_resume_entities:
        discovery = entity.mqtt_discovery_payload()
        assert entity.platform.value == "button"
        assert discovery["payload_press"] == "PRESS"
        assert discovery["qos"] == 0
        assert "state_topic" not in discovery
        assert entity.command_topic.endswith(f"/{entity.logical_member}/resume/set")
        assert entity.availability_topic.endswith(
            f"/{entity.logical_member}/resume/availability"
        )
