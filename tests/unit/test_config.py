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
    assert config.runtime.dry_run is True
    assert all(not device.control.allow_hardware_writes for device in config.devices)


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
