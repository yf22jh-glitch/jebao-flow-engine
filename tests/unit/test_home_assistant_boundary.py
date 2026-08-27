import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "jebao_flow"


def test_custom_component_manifest_is_valid_json() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["domain"] == "jebao_flow"
    assert manifest["iot_class"] == "local_push"
    assert "mqtt" in manifest["dependencies"]
    assert manifest["requirements"] == []


def test_home_assistant_layer_contains_no_device_protocol_code() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in COMPONENT.rglob("*")
        if path.suffix in {".py", ".js"}
    )

    assert "jebao_flow.protocol" not in source
    assert "import gizwits" not in source.lower()
    assert "import socket" not in source


def test_lovelace_card_calls_entities_and_never_mqtt_directly() -> None:
    card = (COMPONENT / "frontend" / "jebao-flow-card.js").read_text(encoding="utf-8")

    assert "callService" in card
    assert "mqtt.async_" not in card.lower()
    assert "mqtt.publish" not in card.lower()
    assert "WebSocket" not in card
    assert "fetch(" not in card
    assert "jebao_flow_instance_id" in card
    assert "jebao_flow_entry_id" in card
    assert "jebao_flow_topic_prefix" in card
    assert "escapeHtml(available ? (STATUS_LABELS[status] || status)" in card
    assert "읽기 전용 관찰 모드" in card
    assert "네이티브 Linkage 실험 기능 잠금" in card
    assert 'individualControls.includes("power")' in card
    assert 'individualControls.includes("enabled")' in card
    assert 'actuation === "native_sync_slave"' in card
    assert 'actuation === "native_async_slave"' in card
    assert "장비 보고 Flow" in card
    assert (
        "const state = isPlainObject(reference?.state) ? reference.state : reference;"
        in card
    )
    assert "available && !locked && isUsableEntity(entities[control])" in card
    assert "isUsableEntity(devicePowerEntity)" in card
    assert "isUsableEntity(deviceEnabledEntity)" in card
    assert "isUsableEntity(resumeGroupEntity)" in card
    assert "!observerMode && isUsableEntity(powerState)" in card
    assert "!observerMode && isUsableEntity(enabledState)" in card
    assert "그룹 제어 잠금" in card


def test_pattern_select_uses_group_specific_server_contract() -> None:
    source = (COMPONENT / "select.py").read_text(encoding="utf-8")
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")

    assert "self.runtime.group_patterns(self.group_id)" in source
    assert "def group_patterns(self, group_id: str)" in runtime
    assert "return self.patterns" in runtime


def test_dynamic_group_and_device_control_locks_are_scoped_to_their_entities() -> None:
    number = (COMPONENT / "number.py").read_text(encoding="utf-8")
    switch = (COMPONENT / "switch.py").read_text(encoding="utf-8")
    number_group, number_device = number.split("class JebaoFlowDevicePowerNumber", 1)
    switch_group, switch_device = switch.split("class JebaoFlowDeviceSwitch", 1)

    assert "self.group_control_available(self.entity_description.key)" in number_group
    assert 'advertises_control("power")' in number_device
    assert 'self.group_control_available("enabled")' in switch_group
    assert 'advertises_control("enabled")' in switch_device


def test_group_factories_and_entities_follow_per_group_control_contract() -> None:
    number = (COMPONENT / "number.py").read_text(encoding="utf-8")
    switch = (COMPONENT / "switch.py").read_text(encoding="utf-8")
    select = (COMPONENT / "select.py").read_text(encoding="utf-8")
    button = (COMPONENT / "button.py").read_text(encoding="utf-8")
    entity = (COMPONENT / "entity.py").read_text(encoding="utf-8")

    assert "in runtime.group_controls" in number
    assert '"enabled" in runtime.group_controls' in switch
    assert '"pattern" in runtime.group_controls' in select
    assert "in runtime.group_controls" in button
    assert 'state_payload.get("hardware_writes_locked", True) is False' in entity
    assert "self.advertises_control(control)" in entity


def test_old_v1_daemon_json_keeps_legacy_group_controls_and_power_semantics() -> None:
    contract = runpy.run_path(str(COMPONENT / "contract.py"))
    resolve_controls = contract["resolve_group_controls"]
    resolve_power_semantics = contract["resolve_device_power_semantics"]
    legacy_controls = contract["LEGACY_V1_GROUP_CONTROLS"]
    payload = json.loads(
        """
        {
          "schema_version": 1,
          "runtime_mode": "control",
          "groups": [{"id": "main_flow", "name": "Main Flow"}],
          "devices": [{"id": "left", "name": "Left", "type": "wavemaker"}]
        }
        """
    )

    assert resolve_controls(payload["groups"][0], observer_mode=False) == legacy_controls
    assert resolve_controls(payload["groups"][0], observer_mode=True) == ()
    assert resolve_power_semantics(payload["devices"][0]) == "output"

    payload["groups"][0]["controls"] = []
    payload["devices"][0]["power_semantics"] = "reported_flow"
    assert resolve_controls(payload["groups"][0], observer_mode=False) == ()
    assert resolve_power_semantics(payload["devices"][0]) == "reported_flow"


def test_actual_power_sensor_labels_reported_flow_without_breaking_old_v1() -> None:
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    entity = (COMPONENT / "entity.py").read_text(encoding="utf-8")
    contract = (COMPONENT / "contract.py").read_text(encoding="utf-8")
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")

    assert 'if self.power_semantics == "reported_flow"' in source
    assert '"장비 보고 Flow"' in source
    assert 'else "장비 보고 출력"' in source
    assert '"power_semantics": self.power_semantics' in entity
    assert "self.runtime.device_power_semantics(self.device_id)" in entity
    assert "def device_power_semantics(self, device_id: str)" in runtime
    assert 'device.get("power_semantics", POWER_SEMANTICS_OUTPUT)' in contract


def test_schedule_sensor_is_separate_and_excludes_device_clock() -> None:
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    status_sensor = source.split("class JebaoFlowDeviceStatusSensor", 1)[1].split(
        "class JebaoFlowActualPowerSensor", 1
    )[0]
    schedule_sensor = source.split("class JebaoFlowScheduleSensor", 1)[1]

    assert 'if "schedule" in device.get("observables", ())' in source
    assert 'super().__init__(runtime, device, "schedule")' in schedule_sensor
    assert '"slot_capacity": _schedule_slot_capacity(' in schedule_sensor
    assert '"entries": _schedule_entries(schedule.get("entries"))' in schedule_sensor
    assert '"invalid_slots": _invalid_schedule_slots(' in schedule_sensor
    assert 'self.state_payload.get("schedule")' not in status_sensor
    assert "device_local_time" not in schedule_sensor


def test_lovelace_cards_render_only_validated_schedule_values() -> None:
    card = (COMPONENT / "frontend" / "jebao-flow-card.js").read_text(
        encoding="utf-8"
    )

    assert 'if (control === "schedule") discovered[deviceId].schedule = entityId;' in card
    assert card.count("renderScheduleDetails(scheduleState)") == 2
    assert "Array.isArray(attributes.entries)" in card
    assert "isPlainObject(value.parameters)" in card
    assert 'if (value === "24:00") return true;' in card
    assert "modeCode < 0 || modeCode > 255" in card
    assert "escapeHtml(scheduleModeLabel(entry.mode))" in card
    assert "escapeHtml(scheduleParameterLabel(parameter.key))" in card
    assert "escapeHtml(parameter.value)" in card
    assert 'if (value === false || value === 0) return "펄스";' in card
    assert 'if (value === true || value === 1) return "조석";' in card
    assert 'feed_time: "급여 값"' in card
    assert 'gears: "출력/단계"' in card
    assert "device_local_time" not in card
    assert "raw_hex" not in card.lower()


def test_korean_translation_is_valid_json() -> None:
    translation = json.loads(
        (COMPONENT / "translations" / "ko.json").read_text(encoding="utf-8")
    )

    assert translation["config"]["step"]["user"]["data"]["topic_prefix"]
