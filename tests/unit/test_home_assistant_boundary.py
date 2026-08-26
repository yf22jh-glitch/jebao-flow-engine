import json
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
