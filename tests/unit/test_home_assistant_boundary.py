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
    assert "34CD" not in source


def test_lovelace_card_calls_entities_and_never_mqtt_directly() -> None:
    card = (COMPONENT / "frontend" / "jebao-flow-card.js").read_text(encoding="utf-8")

    assert "callService" in card
    assert "mqtt.async_" not in card.lower()
    assert "mqtt.publish" not in card.lower()
    assert "WebSocket" not in card
    assert "fetch(" not in card


def test_korean_translation_is_valid_json() -> None:
    translation = json.loads(
        (COMPONENT / "translations" / "ko.json").read_text(encoding="utf-8")
    )

    assert translation["config"]["step"]["user"]["data"]["topic_prefix"]
