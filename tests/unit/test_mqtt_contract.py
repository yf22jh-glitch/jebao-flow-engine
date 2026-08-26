import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from pydantic import ValidationError

from jebao_flow.config import AppConfig, MqttConfig
from jebao_flow.devices.observer import ObserverEvent, ObserverStatus
from jebao_flow.groups.models import GroupState, PatternKind
from jebao_flow.mqtt.client import MqttAdapter
from jebao_flow.mqtt.models import (
    DeviceAction,
    DeviceCommand,
    DeviceControlMode,
    GroupAction,
    GroupCommand,
)
from jebao_flow.mqtt.service import GroupControlService
from jebao_flow.mqtt.topics import MqttTopics
from jebao_flow.protocol.models import DeviceSchedule, DeviceState, ScheduleEntry


def _device_schedule(
    *,
    boundary: str = "08:01",
    clock: datetime | None = None,
) -> DeviceSchedule:
    return DeviceSchedule(
        enabled=True,
        device_local_time=clock,
        entries=(
            ScheduleEntry(
                slot=0,
                start="00:00",
                end=boundary,
                mode="constant",
                mode_code=0,
                parameters={"flow": 60, "frequency": 0, "feed_time": 0},
            ),
            ScheduleEntry(
                slot=1,
                start=boundary,
                end="22:00",
                mode="feed",
                mode_code=4,
                parameters={"flow": 0, "frequency": 0, "feed_time": 240},
            ),
        ),
    )


@pytest.fixture
def service() -> GroupControlService:
    raw = yaml.safe_load(open("config.example.yaml", encoding="utf-8"))
    raw["runtime"]["mode"] = "control"
    raw["runtime"]["dry_run"] = False
    for device in raw["devices"]:
        device["control"]["allow_hardware_writes"] = True
    return GroupControlService(
        AppConfig.model_validate(raw),
        command_executor_ready=True,
    )


@pytest.fixture
def observer_service() -> GroupControlService:
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
    assert state.hardware_writes_locked is False
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


def test_observation_refresh_preserves_emergency_stop_until_explicit_clear(
    service: GroupControlService,
) -> None:
    stopped = service.apply(
        "main_flow",
        GroupCommand(request_id="emergency_observation_1", action=GroupAction.EMERGENCY_STOP),
    )
    observed_at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)

    service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.ONLINE,
            observed_at,
            state=DeviceState(
                online=True,
                enabled=True,
                power=55,
                observed_at=observed_at,
            ),
        )
    )

    assert stopped.accepted is True
    assert service.snapshot("main_flow").status is GroupState.EMERGENCY_STOP

    cleared = service.apply(
        "main_flow",
        GroupCommand(
            request_id="clear_after_observation_1",
            action=GroupAction.CLEAR_EMERGENCY,
        ),
    )

    assert cleared.accepted is True
    assert service.snapshot("main_flow").status is GroupState.STOPPED


def test_control_mode_without_executor_rejects_and_does_not_advertise_control() -> None:
    raw = yaml.safe_load(open("config.example.yaml", encoding="utf-8"))
    raw["runtime"].update(mode="control", dry_run=False)
    for device in raw["devices"]:
        device["control"]["allow_hardware_writes"] = True
    service = GroupControlService(AppConfig.model_validate(raw))
    before = service.snapshot("main_flow")

    group_result = service.apply(
        "main_flow",
        GroupCommand(request_id="missing_executor_group_1", power=45),
    )
    device_result = service.apply_device(
        "wavemaker_left",
        DeviceCommand(request_id="missing_executor_device_1", power=45),
    )

    assert group_result.accepted is False
    assert group_result.reason == "control_executor_unavailable"
    assert device_result.accepted is False
    assert device_result.reason == "control_executor_unavailable"
    assert service.snapshot("main_flow") == before
    assert service.system_config.runtime_mode == "observer"
    assert service.system_config.features == ("observer", "hardware_write_lock")
    assert all(not device.controls for device in service.system_config.devices)
    assert all(state.hardware_writes_locked for state in service.device_snapshots())


def test_runtime_and_device_write_locks_reject_control_commands() -> None:
    raw = yaml.safe_load(open("config.example.yaml", encoding="utf-8"))
    raw["runtime"].update(mode="control", dry_run=False)
    for device in raw["devices"]:
        device["control"]["allow_hardware_writes"] = True
    locked_device = next(
        device for device in raw["devices"] if device["id"] == "wavemaker_left"
    )
    locked_device["control"]["allow_hardware_writes"] = False
    service = GroupControlService(
        AppConfig.model_validate(raw),
        command_executor_ready=True,
    )

    group_result = service.apply(
        "main_flow",
        GroupCommand(request_id="locked_group_write_1", power=45),
    )
    device_result = service.apply_device(
        "wavemaker_left",
        DeviceCommand(request_id="locked_device_write_1", power=45),
    )

    assert group_result.accepted is False
    assert group_result.reason == "hardware_writes_locked"
    assert device_result.accepted is False
    assert device_result.reason == "hardware_writes_locked"
    descriptor = next(
        device
        for device in service.system_config.devices
        if device.id == "wavemaker_left"
    )
    assert descriptor.controls == ()
    assert "feed" not in service.system_config.features
    assert "emergency_stop" not in service.system_config.features


def test_dry_run_rejects_and_does_not_advertise_control() -> None:
    raw = yaml.safe_load(open("config.example.yaml", encoding="utf-8"))
    raw["runtime"]["mode"] = "control"
    for device in raw["devices"]:
        device["control"]["allow_hardware_writes"] = True
    service = GroupControlService(
        AppConfig.model_validate(raw),
        command_executor_ready=True,
    )

    result = service.apply(
        "main_flow",
        GroupCommand(request_id="dry_run_group_write_1", power=45),
    )

    assert result.accepted is False
    assert result.reason == "hardware_writes_locked"
    assert service.system_config.runtime_mode == "observer"
    assert all(not device.controls for device in service.system_config.devices)


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
    assert payload["runtime_mode"] == "control"
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
    dosing = next(device for device in payload["devices"] if device["id"] == "dosing_main")
    assert "schedule" in dosing["observables"]
    assert "power" not in dosing["observables"]


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


def test_observer_mode_rejects_commands_without_mutating_desired_state(
    observer_service: GroupControlService,
) -> None:
    before_group = observer_service.snapshot("main_flow")
    before_device = observer_service.device_snapshot("wavemaker_left")

    group_result = observer_service.apply(
        "main_flow",
        GroupCommand(request_id="observer_group_1", power=40),
    )
    device_result = observer_service.apply_device(
        "wavemaker_left",
        DeviceCommand(request_id="observer_device_1", power=40),
    )

    assert group_result.accepted is False
    assert group_result.reason == "observer_mode_read_only"
    assert device_result.accepted is False
    assert device_result.reason == "observer_mode_read_only"
    assert observer_service.snapshot("main_flow") == before_group
    assert observer_service.device_snapshot("wavemaker_left") == before_device
    assert observer_service.system_config.runtime_mode == "observer"
    assert all(not device.controls for device in observer_service.system_config.devices)


def test_observer_initializes_with_future_pattern_without_calculating_it() -> None:
    raw = yaml.safe_load(open("config.example.yaml", encoding="utf-8"))
    raw["groups"][0]["default"]["pattern"] = "native"

    service = GroupControlService(AppConfig.model_validate(raw))

    assert service.snapshot("main_flow").pattern is PatternKind.NATIVE
    assert service.snapshot("main_flow").status is GroupState.STARTING


def test_control_command_rejects_unimplemented_pattern(
    service: GroupControlService,
) -> None:
    before = service.snapshot("main_flow")

    result = service.apply(
        "main_flow",
        GroupCommand(request_id="unsupported_pattern_1", pattern=PatternKind.NATIVE),
    )

    assert result.accepted is False
    assert result.reason == "unsupported_pattern"
    assert service.snapshot("main_flow") == before


def test_observation_updates_actual_without_mutating_desired(
    observer_service: GroupControlService,
) -> None:
    observed_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    before = observer_service.device_snapshot("wavemaker_left")
    observer_service.record_observer_event(
        ObserverEvent(
            device_id="wavemaker_left",
            status=ObserverStatus.ONLINE,
            occurred_at=observed_at,
            state=DeviceState(
                online=True,
                enabled=True,
                power=47,
                mode="sine",
                frequency=31,
                observed_attributes={
                    "TimerON": True,
                    "AutoMode": "random",
                    "AutoFlow": 52,
                },
                observed_at=observed_at,
            ),
        )
    )

    device = observer_service.device_snapshot("wavemaker_left")
    member = observer_service.snapshot("main_flow").members["wavemaker_left"]
    assert (device.enabled, device.power, device.control_mode) == (
        before.enabled,
        before.power,
        before.control_mode,
    )
    assert (device.actual_enabled, device.actual_power) == (True, 47)
    assert (device.actual_mode, device.actual_frequency) == ("sine", 31)
    assert device.observed_attributes["TimerON"] is True
    assert device.last_seen_at == observed_at
    assert device.last_changed_at is None
    assert device.last_configuration_changed_at is None
    assert member.actual_power == 47
    assert member.actual_mode == "sine"
    assert member.observed_attributes["AutoFlow"] == 52


def test_identical_poll_only_advances_last_seen(
    observer_service: GroupControlService,
) -> None:
    first_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    state = DeviceState(
        online=True,
        enabled=True,
        power=47,
        mode="constant",
        observed_attributes={"TimerON": True},
        observed_at=first_at,
    )
    observer_service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.ONLINE,
            first_at,
            state=state,
        )
    )
    first = observer_service.device_snapshot("wavemaker_left")
    group_revision = observer_service.snapshot("main_flow").revision

    second_at = first_at + timedelta(seconds=5)
    observer_service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.ONLINE,
            second_at,
            state=state.model_copy(update={"observed_at": second_at}),
        )
    )
    second = observer_service.device_snapshot("wavemaker_left")

    assert second.last_seen_at == second_at
    assert second.last_changed_at == first.last_changed_at
    assert second.last_configuration_changed_at == first.last_configuration_changed_at
    assert second.revision == first.revision
    assert observer_service.snapshot("main_flow").revision == group_revision


def test_second_poll_records_actual_and_configuration_change_times(
    observer_service: GroupControlService,
) -> None:
    first_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    first = DeviceState(
        online=True,
        enabled=True,
        power=47,
        mode="constant",
        observed_attributes={"TimerON": True, "AutoFlow": 47},
        observed_at=first_at,
    )
    observer_service.record_observer_event(
        ObserverEvent("wavemaker_left", ObserverStatus.ONLINE, first_at, state=first)
    )
    changed_at = first_at + timedelta(minutes=1)
    observer_service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.ONLINE,
            changed_at,
            state=first.model_copy(
                update={
                    "power": 55,
                    "observed_attributes": {"TimerON": True, "AutoFlow": 55},
                    "observed_at": changed_at,
                }
            ),
        )
    )

    device = observer_service.device_snapshot("wavemaker_left")
    assert device.actual_power == 55
    assert device.last_changed_at == changed_at
    assert device.last_configuration_changed_at == changed_at


def test_schedule_baseline_and_change_are_published_without_mutating_desired(
    observer_service: GroupControlService,
) -> None:
    first_at = datetime(2026, 8, 26, 0, 51, tzinfo=UTC)
    before = observer_service.device_snapshot("return_main")
    baseline = DeviceState(
        online=True,
        enabled=True,
        power=81,
        schedule=_device_schedule(
            boundary="08:00",
            clock=datetime(2026, 8, 26, 9, 51),
        ),
        observed_at=first_at,
    )
    observer_service.record_observer_event(
        ObserverEvent("return_main", ObserverStatus.ONLINE, first_at, state=baseline)
    )
    first = observer_service.device_snapshot("return_main")

    assert first.schedule is not None
    assert first.schedule.entries[0].end == "08:00"
    assert first.last_configuration_changed_at is None
    assert (first.enabled, first.power, first.control_mode) == (
        before.enabled,
        before.power,
        before.control_mode,
    )

    changed_at = first_at + timedelta(minutes=1)
    changed = baseline.model_copy(
        update={
            "schedule": _device_schedule(
                boundary="08:01",
                clock=datetime(2026, 8, 26, 9, 52),
            ),
            "observed_at": changed_at,
        }
    )
    observer_service.record_observer_event(
        ObserverEvent("return_main", ObserverStatus.ONLINE, changed_at, state=changed)
    )
    device = observer_service.device_snapshot("return_main")

    assert device.schedule is not None
    assert [entry.end for entry in device.schedule.entries] == ["08:01", "22:00"]
    assert device.last_configuration_changed_at == changed_at
    assert device.change_source == "external_or_native"
    assert (device.enabled, device.power, device.control_mode) == (
        before.enabled,
        before.power,
        before.control_mode,
    )


def test_device_clock_advance_does_not_create_a_schedule_change(
    observer_service: GroupControlService,
) -> None:
    first_at = datetime(2026, 8, 26, 0, 51, tzinfo=UTC)
    baseline = DeviceState(
        online=True,
        enabled=True,
        power=81,
        schedule=_device_schedule(clock=datetime(2026, 8, 26, 9, 51)),
        observed_at=first_at,
    )
    observer_service.record_observer_event(
        ObserverEvent("return_main", ObserverStatus.ONLINE, first_at, state=baseline)
    )
    first = observer_service.device_snapshot("return_main")

    second_at = first_at + timedelta(seconds=5)
    observer_service.record_observer_event(
        ObserverEvent(
            "return_main",
            ObserverStatus.ONLINE,
            second_at,
            state=baseline.model_copy(
                update={
                    "schedule": _device_schedule(
                        clock=datetime(2026, 8, 26, 9, 51, 5)
                    ),
                    "observed_at": second_at,
                }
            ),
        )
    )
    second = observer_service.device_snapshot("return_main")

    assert second.revision == first.revision
    assert second.last_configuration_changed_at is None
    assert second.schedule is not None
    assert second.schedule.device_local_time == datetime(2026, 8, 26, 9, 51, 5)


def test_offline_preserves_last_actual_and_recovery_clears_error(
    observer_service: GroupControlService,
) -> None:
    first_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    state = DeviceState(
        online=True,
        enabled=True,
        power=47,
        observed_at=first_at,
    )
    observer_service.record_observer_event(
        ObserverEvent("wavemaker_left", ObserverStatus.ONLINE, first_at, state=state)
    )
    observer_service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.OFFLINE,
            first_at + timedelta(seconds=5),
            error="read timeout",
        )
    )

    offline = observer_service.device_snapshot("wavemaker_left")
    assert offline.online is False
    assert offline.actual_power == 47
    assert offline.error == "read timeout"

    recovered_at = first_at + timedelta(seconds=10)
    observer_service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.ONLINE,
            recovered_at,
            state=state.model_copy(update={"observed_at": recovered_at}),
        )
    )
    recovered = observer_service.device_snapshot("wavemaker_left")
    assert recovered.online is True
    assert recovered.error is None
    assert recovered.actual_power == 47


def test_online_member_fault_degrades_group(observer_service: GroupControlService) -> None:
    observed_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    for index, device_id in enumerate(
        ("wavemaker_left", "wavemaker_right", "wavemaker_bar")
    ):
        observer_service.record_observer_event(
            ObserverEvent(
                device_id,
                ObserverStatus.ONLINE,
                observed_at + timedelta(seconds=index),
                state=DeviceState(
                    online=True,
                    enabled=True,
                    power=45,
                    error="Fault_OverTemp" if device_id == "wavemaker_bar" else None,
                    observed_at=observed_at + timedelta(seconds=index),
                ),
            )
        )

    assert observer_service.snapshot("main_flow").status is GroupState.DEGRADED


async def test_mqtt_publisher_retains_observer_device_and_group_state(
    observer_service: GroupControlService,
) -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.published = []
            self.done = asyncio.Event()

        async def publish(self, topic, payload, **kwargs) -> None:
            self.published.append((str(topic), json.loads(payload), kwargs))
            if len(self.published) >= 2:
                self.done.set()

    adapter = MqttAdapter(
        MqttConfig(host="mqtt.example", topic_prefix="jebao-flow/main"),
        observer_service,
    )
    client = RecordingClient()
    observed_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    publisher = asyncio.create_task(adapter._publish_observer_updates(client))
    try:
        observer_service.record_observer_event(
            ObserverEvent(
                "wavemaker_left",
                ObserverStatus.ONLINE,
                observed_at,
                state=DeviceState(
                    online=True,
                    enabled=True,
                    power=47,
                    observed_at=observed_at,
                ),
            )
        )
        await asyncio.wait_for(client.done.wait(), timeout=1)
    finally:
        publisher.cancel()
        await asyncio.gather(publisher, return_exceptions=True)

    by_topic = {topic: (payload, metadata) for topic, payload, metadata in client.published}
    device_payload, device_metadata = by_topic[
        "jebao-flow/main/devices/wavemaker_left/state"
    ]
    assert device_payload["actual_power"] == 47
    assert device_metadata["retain"] is True
    assert "jebao-flow/main/groups/main_flow/state" in by_topic


async def test_identical_poll_is_not_republished_before_heartbeat(
    observer_service: GroupControlService,
) -> None:
    first_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    state = DeviceState(
        online=True,
        enabled=True,
        power=47,
        observed_at=first_at,
    )
    observer_service.record_observer_event(
        ObserverEvent("wavemaker_left", ObserverStatus.ONLINE, first_at, state=state)
    )
    await observer_service.wait_for_updates()
    second_at = first_at + timedelta(seconds=5)

    observer_service.record_observer_event(
        ObserverEvent(
            "wavemaker_left",
            ObserverStatus.ONLINE,
            second_at,
            state=state.model_copy(update={"observed_at": second_at}),
        )
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(observer_service.wait_for_updates(), timeout=0.01)
