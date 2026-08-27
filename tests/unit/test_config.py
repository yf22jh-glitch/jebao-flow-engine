from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jebao_flow.config import AppConfig, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_example_configuration_is_valid() -> None:
    config = load_config(ROOT / "config.example.yaml")

    assert config.instance.id == "main"
    assert [device.id for device in config.devices] == [
        "wavemaker_left",
        "wavemaker_right",
        "wavemaker_bar",
        "return_main",
        "return_aux",
        "dosing_main",
    ]
    assert config.groups[0].members[1].phase == 180
    assert config.groups[0].members[2].role == "crossflow"
    assert config.groups[0].execution_strategy == "software_independent"
    assert config.groups[0].native_pair is None
    assert config.runtime.dry_run is True
    assert config.runtime.mode == "observer"
    assert config.observer.poll_interval_seconds == 5
    assert config.observer.publish_heartbeat_seconds == 300
    assert all(not device.control.allow_hardware_writes for device in config.devices)


def test_legacy_group_without_topology_fields_defaults_to_software_independent() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    group = raw["groups"][0]
    group.pop("execution_strategy", None)
    group.pop("native_pair", None)

    config = AppConfig.model_validate(raw)

    assert config.groups[0].execution_strategy == "software_independent"
    assert config.groups[0].native_pair is None


def test_unknown_group_member_is_rejected() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["groups"][0]["members"][1]["device"] = "missing_pump"

    with pytest.raises(ValidationError, match="references unknown devices"):
        AppConfig.model_validate(raw)


def test_unknown_configuration_key_is_rejected() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["mqtt"]["password"] = "must-not-be-stored-here"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AppConfig.model_validate(raw)


def test_invalid_product_key_is_rejected() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["devices"][0]["product_key"] = "not-a-product-key"

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        AppConfig.model_validate(raw)


def test_device_identity_normalizes_mac_address() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["devices"][0]["identity"] = {"mac_address": "AA:BB:CC:DD:EE:FF"}

    config = AppConfig.model_validate(raw)

    assert config.devices[0].identity is not None
    assert config.devices[0].identity.mac_address == "aabbccddeeff"


def test_duplicate_physical_identity_is_rejected() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["devices"][0]["identity"] = {"device_id": "same-vendor-id"}
    raw["devices"][1]["identity"] = {"device_id": "same-vendor-id"}

    with pytest.raises(ValidationError, match="identity device_ids must be unique"):
        AppConfig.model_validate(raw)


def test_observer_accepts_future_pattern_but_control_rejects_it() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["groups"][0]["default"]["pattern"] = "native"

    observer = AppConfig.model_validate(raw)

    assert observer.groups[0].default.pattern.value == "native"
    raw["runtime"]["mode"] = "control"
    with pytest.raises(ValidationError, match="unimplemented patterns"):
        AppConfig.model_validate(raw)


def test_observer_models_native_pair_with_independent_crossflow_helper() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    group = raw["groups"][0]
    group["execution_strategy"] = "native_linked"
    group["default"]["pattern"] = "native"
    group["native_pair"] = {
        "master": "wavemaker_left",
        "slave": "wavemaker_right",
        "relation": "async",
    }
    group["members"][1].update(gain=1.0, phase=0)

    config = AppConfig.model_validate(raw)

    pair = config.groups[0].native_pair
    assert pair is not None
    assert pair.master == "wavemaker_left"
    assert pair.slave == "wavemaker_right"
    assert pair.relation == "async"
    assert config.groups[0].members[2].device == "wavemaker_bar"
    assert config.groups[0].members[2].gain == 0.75
    assert config.groups[0].members[2].phase == 90


@pytest.mark.parametrize(
    ("field", "value"),
    (("gain", 0.85), ("phase", 180), ("invert", True)),
)
def test_native_slave_rejects_software_tuning(field: str, value: object) -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    group = raw["groups"][0]
    group["execution_strategy"] = "native_linked"
    group["default"]["pattern"] = "native"
    group["native_pair"] = {
        "master": "wavemaker_left",
        "slave": "wavemaker_right",
        "relation": "sync",
    }
    group["members"][1].update(gain=1.0, phase=0, invert=False)
    group["members"][1][field] = value

    with pytest.raises(ValidationError, match="native slave cannot use software"):
        AppConfig.model_validate(raw)


def test_control_mode_rejects_unqualified_native_pair() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["runtime"]["mode"] = "control"
    group = raw["groups"][0]
    group["execution_strategy"] = "native_linked"
    group["default"]["pattern"] = "native"
    group["native_pair"] = {
        "master": "wavemaker_left",
        "slave": "wavemaker_right",
        "relation": "async",
    }
    group["members"][1].update(gain=1.0, phase=0)

    with pytest.raises(ValidationError, match="unqualified native-linked groups"):
        AppConfig.model_validate(raw)


def test_device_cannot_belong_to_multiple_groups() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["groups"].append(
        {
            "id": "other_flow",
            "name": "Other Flow",
            "members": [{"device": "wavemaker_left"}],
        }
    )

    with pytest.raises(ValidationError, match="belongs to multiple groups"):
        AppConfig.model_validate(raw)
