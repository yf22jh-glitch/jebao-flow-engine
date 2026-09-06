from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def _dockerfile_volume_paths(dockerfile: str) -> list[str]:
    declared: list[str] = []
    for line_number, raw_line in enumerate(dockerfile.splitlines(), start=1):
        line = raw_line.strip()
        parts = line.split(maxsplit=1)
        if not parts:
            continue
        instruction = parts[0]
        if instruction.upper() != "VOLUME":
            continue
        assert len(parts) == 2 and parts[1].startswith("["), (
            f"Dockerfile line {line_number}: VOLUME must use JSON array syntax"
        )
        payload = parts[1]
        try:
            paths = json.loads(payload)
        except json.JSONDecodeError as error:
            raise AssertionError(
                f"Dockerfile line {line_number}: VOLUME must contain valid JSON"
            ) from error
        assert isinstance(paths, list) and all(isinstance(path, str) for path in paths)
        declared.extend(paths)
    return declared


def test_image_does_not_auto_provision_the_hardware_safety_volume() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    declared = _dockerfile_volume_paths(dockerfile)
    assert declared == ["/data"]
    assert "/hardware-safety" not in declared
    assert "mkdir -p /config /data /hardware-safety" in dockerfile
    assert "chown -R jebao-flow:jebao-flow /config /data /hardware-safety" in dockerfile
    assert "chmod 0700 /hardware-safety" in dockerfile


@pytest.mark.parametrize(
    "unsafe_instruction",
    [
        'volume ["/hardware-safety"]',
        '  VOLUME ["/data", "/hardware-safety"]',
        'VOLUME\t["/hardware-safety"]',
    ],
)
def test_volume_parser_detects_case_and_indentation_variants(
    unsafe_instruction: str,
) -> None:
    dockerfile = f'VOLUME ["/data"]\n{unsafe_instruction}\n'

    assert "/hardware-safety" in _dockerfile_volume_paths(dockerfile)


def test_volume_parser_fails_closed_on_shell_syntax() -> None:
    with pytest.raises(AssertionError, match="must use JSON array syntax"):
        _dockerfile_volume_paths("VOLUME /data /hardware-safety\n")


def test_compose_services_share_the_exact_named_hardware_safety_volume() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    expected_mount = "jebao-flow-hardware-safety:/hardware-safety"

    for service_name in ("jebao-flowd", "jebao-flow-recovery"):
        mounts = compose["services"][service_name]["volumes"]
        safety_mounts = [mount for mount in mounts if mount.endswith(":/hardware-safety")]
        assert safety_mounts == [expected_mount]

    assert compose["volumes"]["jebao-flow-hardware-safety"] == {
        "name": "jebao-flow-hardware-safety"
    }
