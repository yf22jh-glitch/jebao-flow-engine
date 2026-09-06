from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from jebao_flow import hardware_safety, hardware_safety_cli
from jebao_flow.hardware_safety import HardwareSafetyRootError
from tests.unit.hardware_safety_support import (
    TEST_HARDWARE_SAFETY_VOLUME_ID,
    install_test_hardware_safety_identity,
)


def _real_cli_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_identity: bool,
) -> Path:
    root = tmp_path / "mounted"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)
    monkeypatch.setenv(
        hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV,
        TEST_HARDWARE_SAFETY_VOLUME_ID,
    )
    if with_identity:
        install_test_hardware_safety_identity(root, monkeypatch)
    return root


def test_identity_digest_is_derived_from_the_real_configured_value(monkeypatch) -> None:
    monkeypatch.setenv(
        hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV,
        TEST_HARDWARE_SAFETY_VOLUME_ID,
    )

    assert (
        hardware_safety.hardware_safety_volume_identity_sha256()
        == hashlib.sha256(TEST_HARDWARE_SAFETY_VOLUME_ID.encode("ascii")).hexdigest()
    )


@pytest.mark.parametrize(
    ("command", "with_identity", "expected_status"),
    [("verify", True, "verified"), ("provision", False, "provisioned")],
)
def test_real_cli_never_prints_the_private_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    command: str,
    with_identity: bool,
    expected_status: str,
) -> None:
    _real_cli_root(tmp_path, monkeypatch, with_identity=with_identity)

    assert hardware_safety_cli.main([command]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "identity_sha256": hashlib.sha256(
            TEST_HARDWARE_SAFETY_VOLUME_ID.encode("ascii")
        ).hexdigest(),
        "status": expected_status,
    }
    assert TEST_HARDWARE_SAFETY_VOLUME_ID not in captured.out
    assert TEST_HARDWARE_SAFETY_VOLUME_ID not in captured.err


def test_verify_reports_only_the_identity_digest(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        hardware_safety_cli,
        "validate_hardware_safety_root",
        lambda: calls.append("verify"),
    )
    monkeypatch.setattr(
        hardware_safety_cli,
        "hardware_safety_volume_identity_sha256",
        lambda: "a" * 64,
    )

    assert hardware_safety_cli.main(["verify"]) == 0

    assert calls == ["verify"]
    assert json.loads(capsys.readouterr().out) == {
        "identity_sha256": "a" * 64,
        "status": "verified",
    }


@pytest.mark.parametrize(
    ("created", "status"),
    [(True, "provisioned"), (False, "already_provisioned")],
)
def test_provision_reports_created_or_idempotent_status(
    monkeypatch,
    capsys,
    created: bool,
    status: str,
) -> None:
    monkeypatch.setattr(
        hardware_safety_cli,
        "provision_hardware_safety_volume_identity",
        lambda: created,
    )
    monkeypatch.setattr(
        hardware_safety_cli,
        "hardware_safety_volume_identity_sha256",
        lambda: "b" * 64,
    )

    assert hardware_safety_cli.main(["provision"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "identity_sha256": "b" * 64,
        "status": status,
    }


def test_hardware_safety_failure_reports_a_public_reason(monkeypatch, capsys) -> None:
    def reject() -> None:
        raise HardwareSafetyRootError("shared hardware-safety identity marker is invalid")

    monkeypatch.setattr(hardware_safety_cli, "validate_hardware_safety_root", reject)

    assert hardware_safety_cli.main(["verify"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "reason": "shared hardware-safety identity marker is invalid",
        "status": "rejected",
    }


def test_unexpected_failure_redacts_exception_text(monkeypatch, capsys) -> None:
    private_text = "private-volume-id-and-host-path"

    def reject() -> None:
        raise OSError(private_text)

    monkeypatch.setattr(hardware_safety_cli, "validate_hardware_safety_root", reject)

    assert hardware_safety_cli.main(["verify"]) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.err) == {
        "reason": "private_operation_error",
        "status": "rejected",
    }
    assert private_text not in captured.err
