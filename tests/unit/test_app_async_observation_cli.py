from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import app_async_observation_cli
from jebao_flow.app_async_observation import AppAsyncStage
from jebao_flow.app_async_observation_cli import (
    AppAsyncCliError,
    _PrepareSpec,
    _read_owner_only_json,
    build_parser,
    main,
)
from jebao_flow.read_only_collector import CaptureTransportPolicy

SHA = "a" * 64
COMMIT = "b" * 40


def _common() -> list[str]:
    return [
        "--artifact-root",
        "/private/artifacts",
        "--exact-operation-manifest",
        "/private/exact-manifest.json",
        "--collector-commit",
        COMMIT,
    ]


def _prepare_argv() -> list[str]:
    return [
        "prepare",
        *_common(),
        "--config",
        "/private/config.yaml",
        "--first",
        "pump-a",
        "--second",
        "pump-b",
        "--prepare-spec",
        "/private/app-spec.json",
    ]


def test_parser_exposes_only_read_only_and_human_attestation_commands() -> None:
    parser = build_parser()
    prepared = parser.parse_args(_prepare_argv())
    collected = parser.parse_args(
        [
            "collect",
            *_common(),
            "--manifest-id",
            "AAM-public",
            "--manifest-sha256",
            SHA,
            "--config",
            "/private/config.yaml",
            "--first",
            "pump-a",
            "--second",
            "pump-b",
            "--stage",
            "observation",
        ]
    )

    assert prepared.command == "prepare"
    assert collected.command == "collect"
    assert collected.stage == "observation"
    assert not any("write" in name or "restore" == name for name in vars(collected))


def test_prepare_spec_requires_a_structurally_sufficient_observation_span() -> None:
    spec = _PrepareSpec.model_validate(
        {
            "version": 1,
            "operation_nonce": "0" * 32,
            "master_boundary_local": "2026-09-03T12:00:00",
            "checkpoint_pair_count": 1,
            "checkpoint_cadence_seconds": 1,
            "observation_pair_count": 43,
            "observation_cadence_seconds": 10,
        }
    )
    assert spec.validate_observation_span() is spec

    short = spec.model_copy(update={"observation_pair_count": 42})
    with pytest.raises(
        AppAsyncCliError,
        match="app_async_observation_plan_span_too_short",
    ):
        short.validate_observation_span()


def test_owner_only_json_reader_rejects_group_readable_or_oversized_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"series_sha256_by_stage": {}}))
    path.chmod(0o600)
    assert _read_owner_only_json(path) == {"series_sha256_by_stage": {}}

    path.chmod(0o640)
    with pytest.raises(AppAsyncCliError, match="app_async_private_json_invalid"):
        _read_owner_only_json(path)

    path.chmod(0o600)
    path.write_bytes(b" " * (512 * 1024 + 1))
    with pytest.raises(AppAsyncCliError, match="app_async_private_json_invalid"):
        _read_owner_only_json(path)


def test_cli_import_graph_does_not_load_hardware_writer_ha_or_frozen_modules() -> None:
    source = """
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'src'))
import jebao_flow.app_async_observation_cli
forbidden = (
    'jebao_flow.devices.lan',
    'jebao_flow.protocol.control_session',
    'jebao_flow.devices.linkage',
    'jebao_flow.devices.schedule_flow_experiment',
    'jebao_flow.devices.schedule_linkage',
    'jebao_flow.devices.schedule_transaction',
    'jebao_flow.integrations.home_assistant',
)
print(json.dumps(sorted(name for name in forbidden if name in sys.modules)))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []


def test_invalid_network_arguments_fail_before_source_or_private_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    touched = False

    def touched_source(_commit: str):
        nonlocal touched
        touched = True

    monkeypatch.setattr(app_async_observation_cli, "_attest_app_sources", touched_source)
    result = main(
        [
            "collect",
            *_common(),
            "--manifest-id",
            "AAM-public",
            "--manifest-sha256",
            SHA,
            "--config",
            "/private/config.yaml",
            "--first",
            "pump-a",
            "--second",
            "pump-b",
            "--stage",
            "observation",
            "--control-port",
            "0",
        ]
    )
    assert result == 2
    assert touched is False
    assert capsys.readouterr().err == "app async observation failed: network_port_invalid\n"


def test_cli_redacts_unexpected_private_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(_args: argparse.Namespace) -> dict[str, object]:
        raise RuntimeError("10.0.0.9 private-device /private/config.yaml")

    monkeypatch.setattr(app_async_observation_cli, "_run", fail)
    result = main(_prepare_argv())
    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "app async observation failed: private_operation_error\n"
    assert "10.0.0.9" not in output.err
    assert "/private" not in output.err


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), asyncio.CancelledError()])
def test_cli_interrupt_is_terminal_and_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    interruption: BaseException,
) -> None:
    async def interrupted(_args: argparse.Namespace) -> dict[str, object]:
        raise interruption

    monkeypatch.setattr(app_async_observation_cli, "_run", interrupted)
    result = main(_prepare_argv())
    output = capsys.readouterr()
    assert result == 130
    assert output.out == ""
    assert output.err == "app async observation aborted: operator_interrupt\n"


def test_prepare_creates_all_four_prebound_plans_before_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = object()
    targets = (object(), object())
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        app_async_observation_cli,
        "_attest_app_sources",
        lambda _commit: (source, "c" * 64),
    )
    monkeypatch.setattr(
        app_async_observation_cli,
        "_read_owner_only_json",
        lambda _path: {
            "version": 1,
            "operation_nonce": "0" * 32,
            "master_boundary_local": "2026-09-03T12:00:00",
            "checkpoint_pair_count": 2,
            "checkpoint_cadence_seconds": 3,
            "observation_pair_count": 43,
            "observation_cadence_seconds": 10,
        },
    )
    monkeypatch.setattr(app_async_observation_cli, "load_locked_config", lambda _path: object())
    monkeypatch.setattr(
        app_async_observation_cli,
        "select_capture_pair",
        lambda *_args: targets,
    )
    verifier = SimpleNamespace(derive_q2_exact_restore_evidence=lambda: object())
    monkeypatch.setattr(app_async_observation_cli, "_exact_verifier", lambda _path: verifier)

    plans = []

    class CollectorStore:
        def prepare(self, *_args, **kwargs):
            calls.append(("plan", kwargs["transport_policy"]))
            plan = SimpleNamespace(
                plan_artifact_id=f"JFP-{len(plans)}",
                plan_sha256=SHA,
                series_id=f"JFS-{len(plans)}",
            )
            plans.append(plan)
            return plan

    class AppStore:
        def prepare(self, _collector, prepared, **kwargs):
            calls.append(("manifest", tuple(prepared)))
            assert kwargs["verifier_runtime_source_digest_sha256"] == "c" * 64
            return SimpleNamespace(
                manifest_id="AAM-public",
                manifest_sha256=SHA,
                operation_id="app-async-public",
            )

    monkeypatch.setattr(
        app_async_observation_cli,
        "_stores",
        lambda _root: (CollectorStore(), AppStore()),
    )
    args = build_parser().parse_args(_prepare_argv())
    value = app_async_observation_cli._prepare(args)

    assert [kind for kind, _value in calls] == ["plan", "plan", "plan", "plan", "manifest"]
    assert [policy for _kind, policy in calls[:3]] == [
        CaptureTransportPolicy.EXPLICIT_ONLY,
        CaptureTransportPolicy.EXPLICIT_ONLY,
        CaptureTransportPolicy.EXPLICIT_ONLY,
    ]
    assert calls[3][1] is CaptureTransportPolicy.REPLY_OR_REPORT
    assert tuple(value["collector_plans"]) == tuple(stage.value for stage in AppAsyncStage)
    assert value["device_writes"] == 0
    assert value["ha_actions"] == 0


def test_source_attestation_compares_both_runtime_modules_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_attestation = object()
    monkeypatch.setattr(
        app_async_observation_cli,
        "verify_collector_source_tree",
        lambda _commit: fake_attestation,
    )
    payloads = {
        relative: Path(reported).read_bytes()
        for relative, reported in app_async_observation_cli._APP_RUNTIME_SOURCES
    }

    def fake_git(_root: Path, *arguments: str) -> bytes:
        if arguments == ("rev-parse", "--show-toplevel"):
            return f"{tmp_path}\n".encode()
        if arguments == ("rev-parse", "HEAD"):
            return f"{COMMIT}\n".encode()
        if arguments[0] == "show":
            return payloads[arguments[1].split(":", 1)[1]]
        raise AssertionError(arguments)

    for relative, reported in app_async_observation_cli._APP_RUNTIME_SOURCES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(reported).read_bytes())
    monkeypatch.setattr(app_async_observation_cli, "_run_git", fake_git)
    monkeypatch.setattr(
        app_async_observation_cli,
        "_APP_RUNTIME_SOURCES",
        tuple((relative, str(tmp_path / relative)) for relative in payloads),
    )

    attestation, digest = app_async_observation_cli._attest_app_sources(COMMIT)
    assert attestation is fake_attestation
    assert len(digest) == 64
