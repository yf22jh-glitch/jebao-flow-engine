from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_power_qualification_cli as cli
from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.qualification_producer import QualificationProductionResult
from jebao_flow.local_control.service import HARDWARE_SAFETY_VOLUME_ID_FILE_ENV
from jebao_flow.protocol.profiles import AQUARIUM_PUMP, DC_PUMP_PRO, LOCAL_WAVEMAKER_PRO


def _args(tmp_path: Path, *, write_enabled: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        config=tmp_path / "config.yaml",
        manifest=tmp_path / "manifest.json",
        template_root=[tmp_path / "template"],
        compose_generation="compose-generation",
        operation_id="power_qualification_0001",
        target="private_logical_member",
        active_template_sha256="a" * 64,
        qualification_output=tmp_path / "candidate.json",
        write_enabled=write_enabled,
    )


@pytest.mark.parametrize("product_key,producer_name", [
    (LOCAL_WAVEMAKER_PRO.product_key, "PowerQualificationProducer"),
    (DC_PUMP_PRO.product_key, "PowerQualificationProducer"),
    (AQUARIUM_PUMP.product_key, "AquariumPowerQualificationProducer"),
])
def test_run_builds_the_dedicated_power_producer_and_prints_only_public_result(
    tmp_path: Path,
    monkeypatch,
    capsys,
    product_key,
    producer_name,
) -> None:
    args = _args(tmp_path)
    calls: list[tuple[str, object]] = []
    def epoch():
        return SimpleNamespace(target=SimpleNamespace(product_key=product_key))
    resolver = object()
    journal = object()
    lease = object()
    artifacts = object()

    class _Producer:
        def __init__(self, **kwargs) -> None:
            calls.append(("producer", kwargs))

        async def execute(self, operation_id: str) -> QualificationProductionResult:
            calls.append(("execute", operation_id))
            return QualificationProductionResult(
                operation_id=operation_id,
                phase=TransactionPhase.VERIFIED,
                journal_sha256="b" * 64,
                target_id="c" * 64,
                receipt_count=2,
                physical_write_count=3,
                qualification_sha256="d" * 64,
                completed_at=datetime(2026, 9, 13, 4, tzinfo=UTC),
            )

    def _epoch_source(**kwargs):
        calls.append(("epoch", kwargs))
        return epoch

    monkeypatch.setattr(
        cli,
        "validate_current_compose_inputs",
        lambda value: calls.append(("compose", value)),
    )
    monkeypatch.setattr(
        cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: calls.append(("volume", True)),
    )
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", _epoch_source)
    monkeypatch.setattr(cli, "_resolver", lambda path: resolver)
    monkeypatch.setattr(cli, "LocalControlJournalStore", lambda: journal)
    monkeypatch.setattr(cli, "LocalControlLease", lambda: lease)
    monkeypatch.setattr(cli, "QualificationArtifactStore", lambda path: artifacts)
    monkeypatch.setattr(cli, producer_name, _Producer)

    assert cli._run(args) == 0

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == ""
    assert payload == {
        "completed_at": "2026-09-13T04:00:00+00:00",
        "journal_sha256": "b" * 64,
        "operation_id": "power_qualification_0001",
        "phase": "verified",
        "physical_write_count": 3,
        "private_values_exposed": False,
        "qualification_sha256": "d" * 64,
        "receipt_count": 2,
        "target_id": "c" * 64,
    }
    assert calls[0:2] == [
        ("compose", "compose-generation"),
        ("volume", True),
    ]
    epoch_call = next(value for name, value in calls if name == "epoch")
    assert epoch_call == {
        "config_path": args.config,
        "manifest_path": args.manifest,
        "template_roots": tuple(args.template_root),
        "logical_member": args.target,
        "active_template_sha256": args.active_template_sha256,
        "target_validator": cli._validate_power_target,
    }
    producer_call = next(value for name, value in calls if name == "producer")
    assert producer_call["epoch_provider"] is epoch
    assert producer_call["journal"] is journal
    assert producer_call["lease"] is lease
    assert producer_call["artifacts"] is artifacts
    assert calls[-1] == ("execute", args.operation_id)
    assert args.target not in output.out
    assert str(args.config) not in output.out


def test_missing_write_opt_in_stops_before_current_or_network_access(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(cli, "_run", cli._run)
    monkeypatch.setattr(
        cli,
        "validate_current_compose_inputs",
        lambda _value: touched.append("compose"),
    )
    monkeypatch.setattr(
        cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: touched.append("volume"),
    )
    private_values = (
        str(tmp_path / "private-config.yaml"),
        str(tmp_path / "private-manifest.json"),
        str(tmp_path / "private-template"),
        str(tmp_path / "private-candidate.json"),
        "private_logical_member",
    )

    result = cli.main(
        [
            "--config",
            private_values[0],
            "--manifest",
            private_values[1],
            "--template-root",
            private_values[2],
            "--qualification-output",
            private_values[3],
            "--target",
            private_values[4],
            "--operation-id",
            "power_qualification_0002",
            "--active-template-sha256",
            hashlib.sha256(b"template").hexdigest(),
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert touched == []
    assert output.out == ""
    assert output.err == (
        "jebao-flow-local-power-qualify: current_operation_failed_closed\n"
    )
    assert all(value not in output.err for value in private_values)


def test_parser_and_runtime_failures_never_echo_private_values(monkeypatch, capsys) -> None:
    private_value = "private_logical_member_must_not_escape"

    with pytest.raises(SystemExit) as stopped:
        cli.main(["--physical-identity-key", private_value])
    parser_output = capsys.readouterr()
    assert stopped.value.code == 2
    assert parser_output.out == ""
    assert parser_output.err == (
        "jebao-flow-local-power-qualify: current_operation_failed_closed\n"
    )
    assert private_value not in parser_output.err

    def _fail(_args) -> int:
        raise KeyError(private_value)

    monkeypatch.setattr(cli, "_run", _fail)
    result = cli.main(
        [
            "--target",
            private_value,
            "--operation-id",
            "power_qualification_0003",
            "--active-template-sha256",
            "a" * 64,
            "--qualification-output",
            "/private/candidate.json",
            "--write-enabled",
        ]
    )
    runtime_output = capsys.readouterr()
    assert result == 2
    assert runtime_output.out == ""
    assert runtime_output.err == (
        "jebao-flow-local-power-qualify: current_operation_failed_closed\n"
    )
    assert private_value not in runtime_output.err


def test_volume_identity_file_is_activated_without_being_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    identity_file = tmp_path / "volume-id"
    monkeypatch.setenv(HARDWARE_SAFETY_VOLUME_ID_FILE_ENV, str(identity_file))
    monkeypatch.delenv(HARDWARE_SAFETY_VOLUME_ID_ENV, raising=False)
    monkeypatch.setattr(
        cli,
        "read_current_hardware_safety_volume_id",
        lambda path: "private-volume-identity" if path == identity_file else "wrong",
    )

    cli._activate_current_hardware_safety_volume_identity()

    assert os.environ[HARDWARE_SAFETY_VOLUME_ID_ENV] == "private-volume-identity"


def test_cli_has_no_recovery_or_raw_physical_identity_surface() -> None:
    parser = cli.build_parser()
    destinations = {action.dest for action in parser._actions}

    assert "target" in destinations
    assert "physical_identity_key" not in destinations
    assert "physical_key" not in destinations
    assert "command" not in destinations
    assert "recover" not in parser.format_help()
