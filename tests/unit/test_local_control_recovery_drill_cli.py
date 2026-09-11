from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from jebao_flow import local_control_recovery_drill_cli as cli
from jebao_flow.config import AppConfig
from jebao_flow.local_control.install import build_fresh_install_manifest
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.local_control.recovery_drill import (
    LocalControlRecoveryDrillError,
    RecoveryDrillForwardResult,
)


def _config(*, write_enabled: bool = True, mode: str = "control", dry_run: bool = False):
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/test"},
            "runtime": {"mode": mode, "dry_run": dry_run},
            "devices": [
                {
                    "id": "private_member",
                    "name": "Private Member",
                    "type": "wavemaker",
                    "product_key": "50dbc92221fd4d33ae69a1fedd43b555",
                    "discovery": "auto",
                    "identity": {
                        "device_id": "private-test-device",
                        "mac_address": "020000000001",
                    },
                    "control": {"allow_hardware_writes": write_enabled},
                }
            ],
        }
    )


def _manifest(config: AppConfig):
    return build_fresh_install_manifest(
        config,
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 11, tzinfo=UTC),
        template_sha256=("1" * 64,),
        now=datetime(2026, 9, 11, tzinfo=UTC),
    )


def test_cli_requires_explicit_write_opt_in_before_current_input_access(
    monkeypatch,
    capsys,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: touched.append("safety"),
    )

    result = cli.main(
        [
            "--config",
            "/private/config",
            "--manifest",
            "/private/manifest",
            "--qualifications",
            "/private/qualifications",
            "--operation-id",
            "recovery_drill_1001",
            "--target",
            "private_member",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert touched == []
    assert output.out == ""
    assert output.err == "jebao-flow-local-recovery-drill: current_operation_failed_closed\n"
    assert "private_member" not in output.err


def test_cli_reports_only_the_exact_planned_forward_result(monkeypatch, capsys) -> None:
    config = _config()
    manifest = _manifest(config)
    calls: list[object] = []
    monkeypatch.setattr(cli, "validate_current_compose_inputs", calls.append)
    monkeypatch.setattr(
        cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: calls.append("safety"),
    )
    monkeypatch.setattr(cli, "load_current_private_config", lambda _path: config)
    monkeypatch.setattr(cli, "load_current_install_manifest", lambda _path: manifest)
    monkeypatch.setattr(cli, "load_current_qualifications", lambda _path: (object(),))
    catalog = object()
    monkeypatch.setattr(cli, "build_local_control_catalog", lambda *_args, **_kwargs: catalog)
    monkeypatch.setattr(cli, "_resolver_from_config", lambda _config: object())
    monkeypatch.setattr(cli, "LocalControlJournalStore", lambda: object())
    monkeypatch.setattr(cli, "LocalControlLease", lambda: object())

    class _Drill:
        def __init__(self, **kwargs) -> None:
            assert kwargs["catalog"] is catalog
            calls.append("drill")

        async def execute(self, operation_id, target):
            assert operation_id == "recovery_drill_1002"
            assert target.logical_member == "private_member"
            return RecoveryDrillForwardResult(
                operation_id=operation_id,
                phase=TransactionPhase.RECOVERY_REQUIRED,
                action_phase=ActionPhase.UNCERTAIN,
                diagnostic="forward_readback_unavailable",
                revision=3,
                journal_sha256="2" * 64,
                target_id="3" * 64,
                acknowledgement_sha256="4" * 64,
                forward_readback_sha256="5" * 64,
            )

    monkeypatch.setattr(cli, "ActualRecoveryForwardDrill", _Drill)

    result = cli.main(
        [
            "--config",
            "/private/config",
            "--manifest",
            "/private/manifest",
            "--qualifications",
            "/private/qualifications",
            "--compose-generation",
            "a" * 32,
            "--operation-id",
            "recovery_drill_1002",
            "--target",
            "private_member",
            "--write-enabled",
        ]
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["planned_forward_complete"] is True
    assert payload["physical_write_count"] == 1
    assert payload["created_receipt_count"] == 0
    assert payload["private_values_exposed"] is False
    assert "private_member" not in output.out
    assert calls == ["a" * 32, "safety", "drill"]


def test_cli_non_designated_or_unverified_result_is_non_success_and_redacted(
    monkeypatch,
    capsys,
) -> None:
    private_value = "private_member_must_not_escape"

    def _fail_closed(_args) -> int:
        raise LocalControlRecoveryDrillError(private_value)

    monkeypatch.setattr(cli, "_run", _fail_closed)

    result = cli.main(
        [
            "--operation-id",
            "recovery_drill_1003",
            "--target",
            private_value,
            "--write-enabled",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-local-recovery-drill: current_operation_failed_closed\n"
    assert private_value not in output.err


@pytest.mark.parametrize(
    "config",
    (
        _config(write_enabled=False),
        _config(mode="observer", dry_run=True),
    ),
)
def test_current_target_must_be_live_control_enabled(config: AppConfig) -> None:
    with pytest.raises(ValueError, match="recovery drill requires|write-enabled"):
        cli._require_current_target(config, _manifest(config), "private_member")
