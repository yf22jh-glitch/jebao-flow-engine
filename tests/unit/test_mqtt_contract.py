import json

import pytest
import yaml
from pydantic import ValidationError

from jebao_flow.config import AppConfig
from jebao_flow.groups.models import GroupState, PatternKind
from jebao_flow.mqtt.models import (
    DeviceAction,
    DeviceCommand,
    DeviceControlMode,
    GroupAction,
    GroupCommand,
)
from jebao_flow.mqtt.service import GroupControlService
from jebao_flow.mqtt.topics import MqttTopics


@pytest.fixture
def service() -> GroupControlService:
    raw = yaml.safe_load(open("config.example.yaml", encoding="utf-8"))
    return GroupControlService(AppConfig.model_validate(raw))


def test_topics_round_trip_group_command() -> None:
    topics = MqttTopics("/jebao-flow/main/")

    topic = topics.group_command("main_flow")

    assert topic == "jebao-flow/main/groups/main_flow/command"
    assert topics.parse_group_command(topic) == "main_flow"
    assert topics.parse_group_command("jebao-flow/main/devices/pump/command") is None
    assert topics.parse_device_command(
        topics.device_command("wavemaker_left")
    ) == "wavemaker_left"


def test_command_requires_a_change() -> None:
    with pytest.raises(ValidationError, match="at least one change"):
        GroupCommand(request_id="request_123")


def test_service_applies_patch_and_calculates_member_targets(
    service: GroupControlService,
) -> None:
    result = service.apply(
        "main_flow",
        GroupCommand(
            request_id="request_123",
            pattern=PatternKind.GYRE,
            power=60,
            period_seconds=30,
        ),
    )
    state = service.snapshot("main_flow")

    assert result.accepted is True
    assert state.revision == 1
    assert state.pattern is PatternKind.GYRE
    assert state.power == 60
    assert state.period_seconds == 30
    assert state.hardware_writes_locked is True
    assert set(state.members) == {
        "wavemaker_left",
        "wavemaker_right",
        "wavemaker_bar",
    }
    assert state.members["wavemaker_bar"].role == "crossflow"
    assert state.members["wavemaker_bar"].phase == 90


def test_request_id_is_idempotent(service: GroupControlService) -> None:
    command = GroupCommand(request_id="duplicate_123", power=50)

    first = service.apply("main_flow", command)
    second = service.apply("main_flow", command.model_copy(update={"power": 55}))

    assert first == second
    assert service.snapshot("main_flow").power == 50


def test_emergency_stop_requires_explicit_clear(service: GroupControlService) -> None:
    stopped = service.apply(
        "main_flow",
        GroupCommand(request_id="emergency_1", action=GroupAction.EMERGENCY_STOP),
    )
    rejected = service.apply(
        "main_flow",
        GroupCommand(request_id="normal_123", enabled=True),
    )
    cleared = service.apply(
        "main_flow",
        GroupCommand(request_id="clear_123", action=GroupAction.CLEAR_EMERGENCY),
    )

    assert stopped.accepted is True
    assert rejected.accepted is False
    assert rejected.reason == "emergency_stop_locked"
    assert cleared.accepted is True
    assert service.snapshot("main_flow").status is GroupState.STOPPED


def test_invalid_limits_do_not_change_state(service: GroupControlService) -> None:
    before = service.snapshot("main_flow")

    result = service.apply(
        "main_flow",
        GroupCommand(request_id="badlimits_1", min_power=80, max_power=40),
    )

    assert result.accepted is False
    assert service.snapshot("main_flow") == before


def test_contract_serializes_as_plain_json(service: GroupControlService) -> None:
    payload = json.loads(service.system_config.model_dump_json())

    assert payload["instance_id"] == "main"
    assert payload["groups"] == [{"id": "main_flow", "name": "메인 수류"}]
    assert [device["type"] for device in payload["devices"]] == [
        "wavemaker",
        "wavemaker",
        "wavemaker",
        "return_pump",
        "return_pump",
        "dosing_pump",
    ]
    assert "reef_crest" in payload["patterns"]


def test_individual_command_enters_manual_override(service: GroupControlService) -> None:
    result = service.apply_device(
        "wavemaker_left",
        DeviceCommand(request_id="device_override_1", power=44),
    )
    device = service.device_snapshot("wavemaker_left")
    group = service.snapshot("main_flow")

    assert result.accepted is True
    assert device.power == 44
    assert device.control_mode is DeviceControlMode.MANUAL_OVERRIDE
    assert group.members["wavemaker_left"].target_power == 44
    assert group.members["wavemaker_left"].control_mode is DeviceControlMode.MANUAL_OVERRIDE


def test_manual_device_can_resume_group_control(service: GroupControlService) -> None:
    service.apply_device(
        "wavemaker_right",
        DeviceCommand(request_id="device_override_2", power=40),
    )
    result = service.apply_device(
        "wavemaker_right",
        DeviceCommand(
            request_id="device_resume_1",
            action=DeviceAction.RESUME_GROUP,
        ),
    )

    assert result.accepted is True
    assert service.device_snapshot("wavemaker_right").control_mode is DeviceControlMode.GROUP
    assert (
        service.device_snapshot("wavemaker_right").power
        == service.snapshot("main_flow").members["wavemaker_right"].target_power
    )
    assert (
        service.snapshot("main_flow").members["wavemaker_right"].control_mode
        is DeviceControlMode.GROUP
    )


def test_explicit_group_power_switch_resumes_all_members(service: GroupControlService) -> None:
    service.apply_device(
        "wavemaker_bar",
        DeviceCommand(request_id="device_override_3", enabled=False),
    )

    result = service.apply(
        "main_flow",
        GroupCommand(request_id="group_resume_1", enabled=True),
    )

    assert result.accepted is True
    assert service.device_snapshot("wavemaker_bar").control_mode is DeviceControlMode.GROUP
    assert service.device_snapshot("wavemaker_bar").power != 30


def test_dosing_control_is_locked_until_capability_mapping_exists(
    service: GroupControlService,
) -> None:
    result = service.apply_device(
        "dosing_main",
        DeviceCommand(request_id="dosing_unsafe_1", enabled=True),
    )

    assert result.accepted is False
    assert result.reason == "enabled_control_unsupported"


def test_group_emergency_stop_blocks_individual_restart(
    service: GroupControlService,
) -> None:
    service.apply(
        "main_flow",
        GroupCommand(request_id="emergency_group_1", action=GroupAction.EMERGENCY_STOP),
    )

    result = service.apply_device(
        "wavemaker_left",
        DeviceCommand(request_id="unsafe_restart_1", enabled=True),
    )

    assert result.accepted is False
    assert result.reason == "group_emergency_stop_locked"
    assert all(
        not service.device_snapshot(device_id).enabled
        and service.device_snapshot(device_id).power == 0
        for device_id in ("wavemaker_left", "wavemaker_right", "wavemaker_bar")
    )
