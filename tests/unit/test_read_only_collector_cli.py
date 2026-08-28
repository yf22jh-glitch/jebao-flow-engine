from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import read_only_collector_cli
from jebao_flow.read_only_collector import (
    CollectorPreflightError,
    PilotTerminalError,
    PublicPilotMetadata,
)
from jebao_flow.source_attestation import SourceAttestationError

_REAL_VERIFY_SOURCE_TREE = read_only_collector_cli.verify_collector_source_tree
_TEST_SOURCE_ATTESTATION = object()


@pytest.fixture(autouse=True)
def _clean_collector_source(monkeypatch) -> None:
    monkeypatch.setattr(
        read_only_collector_cli,
        "verify_collector_source_tree",
        lambda _commit: _TEST_SOURCE_ATTESTATION,
    )


def _argv() -> list[str]:
    return [
        "--config",
        "/private/config-with-secret.yaml",
        "--first",
        "first",
        "--second",
        "second",
        "--output-root",
        "/private/captures",
        "--samples",
        "3",
        "--cadence-seconds",
        "5",
        "--collector-commit",
        "a" * 40,
    ]


def test_cli_outputs_only_public_artifact_metadata(monkeypatch, capsys) -> None:
    private_config = object()
    private_targets = (object(), object())

    monkeypatch.setattr(read_only_collector_cli, "load_config", lambda _path: private_config)
    monkeypatch.setattr(
        read_only_collector_cli,
        "select_capture_pair",
        lambda config, first, second: private_targets,
    )

    class Store:
        def __init__(self, _root) -> None:
            pass

        def prepare(self, *_args, **_kwargs):
            assert _kwargs["source_attestation"] is _TEST_SOURCE_ATTESTATION
            return SimpleNamespace(plan_artifact_id="JFP-public")

        async def run(self, *_args, **_kwargs) -> PublicPilotMetadata:
            assert _kwargs["source_attestation"] is _TEST_SOURCE_ATTESTATION
            return PublicPilotMetadata(
                plan_artifact_id="JFP-public",
                series_id="JFS-public",
                plan_sha256="b" * 64,
                series_sha256="c" * 64,
                status="pilot_completed_all_acquisitions_accepted",
                validity_scope="acquisition_only_not_q2_boundary",
                q2_boundary_classification="not_authorized",
                utc_started="2026-08-28T00:00:00Z",
                utc_completed="2026-08-28T00:00:01Z",
                planned_pair_count=3,
                completed_pair_count=3,
                accepted_pair_count=3,
                rejected_pair_count=0,
                read_failure_pair_count=0,
                expected_identity_bindings_sha256=("d" * 64, "e" * 64),
            )

    monkeypatch.setattr(read_only_collector_cli, "PilotSeriesStore", Store)

    result = read_only_collector_cli.main(_argv())
    output = capsys.readouterr()

    assert result == 0
    public = json.loads(output.out)
    assert public["series_id"] == "JFS-public"
    assert public["validity_scope"] == "acquisition_only_not_q2_boundary"
    assert public["q2_boundary_classification"] == "not_authorized"
    assert "/private" not in output.out
    assert output.err == ""


def test_cli_redacts_private_exception_text(monkeypatch, capsys) -> None:
    def fail(_path):
        raise RuntimeError("device 10.0.0.9 private-id private-mac /private/config")

    monkeypatch.setattr(read_only_collector_cli, "load_config", fail)

    result = read_only_collector_cli.main(_argv())
    output = capsys.readouterr()

    assert result == 2
    assert output.out == ""
    assert output.err == "capture failed: private_operation_error\n"
    assert "10.0.0.9" not in output.err
    assert "private-id" not in output.err
    assert "/private" not in output.err


def test_cli_rejects_invalid_port_without_loading_private_config(monkeypatch, capsys) -> None:
    loaded = False

    def load(_path):
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(read_only_collector_cli, "load_config", load)

    result = read_only_collector_cli.main([*_argv(), "--control-port", "0"])

    assert result == 2
    assert loaded is False
    assert capsys.readouterr().err == "capture failed: network_port_invalid\n"


def test_source_tree_preflight_returns_opaque_attestation(monkeypatch) -> None:
    monkeypatch.setattr(
        read_only_collector_cli,
        "attest_collector_source_tree",
        lambda commit, *, cwd=None: (commit, cwd),
    )

    assert _REAL_VERIFY_SOURCE_TREE("a" * 40, cwd=Path("repo")) == (
        "a" * 40,
        Path("repo"),
    )


def test_source_tree_preflight_maps_privacy_safe_attestation_error(monkeypatch) -> None:
    def fail(_commit, *, cwd=None):
        raise SourceAttestationError("collector_runtime_source_path_mismatch")

    monkeypatch.setattr(read_only_collector_cli, "attest_collector_source_tree", fail)
    with pytest.raises(
        CollectorPreflightError,
        match="collector_runtime_source_path_mismatch",
    ):
        _REAL_VERIFY_SOURCE_TREE("a" * 40)


def test_cli_parse_error_never_echoes_private_argument(capsys) -> None:
    argv = _argv()
    argv[argv.index("3")] = "/private/secret-sample-count"

    result = read_only_collector_cli.main(argv)
    output = capsys.readouterr()

    assert result == 2
    assert output.out == ""
    assert output.err == "capture failed: command_line_invalid\n"
    assert "/private" not in output.err


@pytest.mark.parametrize(
    ("argument", "value", "failure_code"),
    [
        ("--timeout", "nan", "network_timeout_invalid"),
        ("--timeout", "inf", "network_timeout_invalid"),
        ("--cadence-seconds", "nan", "pilot_cadence_invalid"),
        ("--cadence-seconds", "inf", "pilot_cadence_invalid"),
    ],
)
def test_cli_rejects_nonfinite_timing_before_loading_private_config(
    monkeypatch,
    capsys,
    argument: str,
    value: str,
    failure_code: str,
) -> None:
    loaded = False

    def load(_path):
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(read_only_collector_cli, "load_config", load)
    argv = _argv()
    if argument in argv:
        argv[argv.index(argument) + 1] = value
    else:
        argv.extend((argument, value))

    result = read_only_collector_cli.main(argv)

    assert result == 2
    assert loaded is False
    assert capsys.readouterr().err == f"capture failed: {failure_code}\n"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), asyncio.CancelledError()])
def test_cli_interrupt_exits_130_without_traceback_or_private_text(
    monkeypatch,
    capsys,
    interruption: BaseException,
) -> None:
    async def interrupted(_args):
        raise interruption

    monkeypatch.setattr(read_only_collector_cli, "_run", interrupted)

    result = read_only_collector_cli.main(_argv())
    output = capsys.readouterr()

    assert result == 130
    assert output.out == ""
    assert output.err == "capture aborted: operator_interrupt\n"
    assert "Traceback" not in output.err


@pytest.mark.parametrize(
    "code",
    ["capture_cancelled_after_read", "capture_cancelled", "keyboard_interrupt"],
)
def test_cli_interrupt_outputs_only_public_abort_metadata(
    monkeypatch,
    capsys,
    code: str,
) -> None:
    async def interrupted(_args):
        raise PilotTerminalError(
            code,
            plan_artifact_id="JFP-public",
            series_id="JFS-public",
            plan_sha256="a" * 64,
            abort_sha256="b" * 64,
            durability_unknown=False,
        )

    monkeypatch.setattr(read_only_collector_cli, "_run", interrupted)

    result = read_only_collector_cli.main(_argv())
    output = capsys.readouterr()

    assert result == 130
    assert output.out == ""
    public = json.loads(output.err)
    assert public == {
        "abort_sha256": "b" * 64,
        "code": code,
        "durability_unknown": False,
        "plan_artifact_id": "JFP-public",
        "plan_sha256": "a" * 64,
        "q2_boundary_classification": "not_authorized",
        "series_id": "JFS-public",
        "status": "pilot_aborted_not_q2_boundary",
    }
    assert "Traceback" not in output.err


def test_cli_noninterrupt_terminal_error_is_public_and_exits_two(monkeypatch, capsys) -> None:
    async def failed(_args):
        raise PilotTerminalError(
            "pilot_abort_durability_unconfirmed",
            plan_artifact_id="JFP-public",
            series_id="JFS-public",
            plan_sha256="a" * 64,
            abort_sha256=None,
            durability_unknown=True,
        )

    monkeypatch.setattr(read_only_collector_cli, "_run", failed)

    result = read_only_collector_cli.main(_argv())
    public = json.loads(capsys.readouterr().err)

    assert result == 2
    assert public["code"] == "pilot_abort_durability_unconfirmed"
    assert public["abort_sha256"] is None
    assert public["durability_unknown"] is True
    assert public["q2_boundary_classification"] == "not_authorized"
