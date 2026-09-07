from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def test_private_env_files_are_excluded_from_container_build_context() -> None:
    ignored = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert ".env" in ignored
    assert ".env.*" in ignored
    assert not any(pattern.startswith("!.env") for pattern in ignored)


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

    for service_name in (
        "jebao-flowd",
        "jebao-flow-recovery",
        "jebao-flow-hardware-safety-provision",
    ):
        mounts = compose["services"][service_name]["volumes"]
        safety_mounts = [mount for mount in mounts if mount.endswith(":/hardware-safety")]
        assert safety_mounts == [expected_mount]

    assert compose["volumes"]["jebao-flow-hardware-safety"] == {
        "name": "jebao-flow-hardware-safety"
    }


def test_compose_requires_one_external_identity_for_every_safety_root_user() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    expected = (
        "${JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID:"
        "?set JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID before starting}"
    )

    for service_name in (
        "jebao-flowd",
        "jebao-flow-recovery",
        "jebao-flow-hardware-safety-provision",
    ):
        environment = compose["services"][service_name]["environment"]
        assert environment["JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID"] == expected


def test_example_env_declares_but_does_not_embed_the_private_volume_identity() -> None:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()

    assert "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID=" in lines


def test_provisioning_service_is_explicit_bounded_and_not_restartable() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["jebao-flow-hardware-safety-provision"]

    assert service["profiles"] == ["hardware-safety-provisioning"]
    assert service["restart"] == "no"
    assert service["network_mode"] == "none"
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp"]
    assert service["entrypoint"] == ["jebao-flow-hardware-safety"]
    assert service["command"] == ["verify"]
    assert service["volumes"] == ["jebao-flow-hardware-safety:/hardware-safety"]
    assert service["environment"] == {
        "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID": (
            "${JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID:"
            "?set JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID before starting}"
        )
    }
    assert "depends_on" not in service


def test_existing_volume_migration_compose_is_external_and_offline() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.hardware-safety-migration.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["jebao-flow-hardware-safety-migrate"]

    assert compose["name"] == "jebao-flow-hardware-safety-migration"
    assert service["profiles"] == ["hardware-safety-migration"]
    assert service["image"] == (
        "sha256:b8f35abb0ac5a31f8173e78585eedb183c548eb104369ec2ee9f4f6b63437484"
    )
    assert service["platform"] == "linux/amd64"
    assert "build" not in service
    assert service["restart"] == "no"
    assert service["pull_policy"] == "never"
    assert service["network_mode"] == "none"
    assert service["environment"] == {
        "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID": (
            "${JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID:"
            "?set deployment volume identity before migration}"
        )
    }
    assert service["volumes"] == ["jebao-flow-hardware-safety:/hardware-safety"]
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp", "/data"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["entrypoint"] == ["jebao-flow-hardware-safety"]
    assert service["command"] == ["verify"]
    assert "depends_on" not in service
    assert compose["volumes"]["jebao-flow-hardware-safety"] == {
        "name": "jebao-flow-hardware-safety",
        "external": True,
    }


def test_existing_volume_holder_is_exact_pinned_read_only_and_not_a_writer() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.hardware-safety-migration.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["jebao-flow-hardware-safety-holder"]

    assert service["profiles"] == ["hardware-safety-holder"]
    assert service["image"] == (
        "sha256:b8f35abb0ac5a31f8173e78585eedb183c548eb104369ec2ee9f4f6b63437484"
    )
    assert service["platform"] == "linux/amd64"
    assert "build" not in service
    assert service["container_name"] == "jebao-flow-hardware-safety-holder"
    assert service["restart"] == "unless-stopped"
    assert service["pull_policy"] == "never"
    assert service["network_mode"] == "none"
    assert "environment" not in service
    assert service["volumes"] == ["jebao-flow-hardware-safety:/hardware-safety:ro"]
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp", "/data"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["entrypoint"] == ["/bin/sleep"]
    assert service["command"] == ["infinity"]
    assert "depends_on" not in service


def test_identity_console_script_is_installed() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'jebao-flow-hardware-safety = "jebao_flow.hardware_safety_cli:main"' in pyproject
