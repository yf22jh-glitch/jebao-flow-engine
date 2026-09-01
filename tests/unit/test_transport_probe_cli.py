from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import transport_probe_cli
from jebao_flow.transport_probe import (
    ProbePublicMetadata,
    TransportProbeError,
)


def test_command_line_errors_never_echo_private_values(capsys: pytest.CaptureFixture[str]) -> None:
    private_value = "/private/home/operator/secret-artifacts"

    result = transport_probe_cli.main(["--unknown", private_value])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "transport_probe_command_line_invalid\n"
    assert private_value not in captured.err
    assert "usage:" not in captured.err


def test_revalidation_rejects_probe_pin_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    attestation = object()
    calls: list[str] = []

    def validate(value: object, *, expected_commit: str) -> object:
        assert value is attestation
        assert expected_commit == "a" * 40
        calls.append("collector")
        return value

    def verify(*_args, **_kwargs) -> str:
        calls.append("probe")
        return "b" * 64

    monkeypatch.setattr(
        transport_probe_cli,
        "validate_collector_source_attestation",
        validate,
    )
    monkeypatch.setattr(transport_probe_cli, "verify_source_pin", verify)

    with pytest.raises(
        TransportProbeError,
        match="transport_probe_source_attestation_stale",
    ):
        transport_probe_cli._revalidate_before_network(
            attestation,  # type: ignore[arg-type]
            commit_sha="a" * 40,
            repo_root=Path("."),
            expected_probe_digest="c" * 64,
        )

    assert calls == ["collector", "probe"]


async def test_run_commits_plan_then_revalidates_before_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    attestation = SimpleNamespace(runtime_source_digest_sha256="b" * 64)
    targets = (object(), object())
    reference = object()
    config = SimpleNamespace(
        observer=SimpleNamespace(targets=("255.255.255.255",), bind_address="0.0.0.0")
    )
    stores: list[object] = []

    def verify_source(_commit: str, _root: Path):
        events.append("verify")
        return attestation, "c" * 64

    def load(_path: str):
        events.append("config")
        return config

    def select(_config: object, _first: str, _second: str):
        events.append("select")
        return targets

    class Store:
        def __init__(self, _root: Path) -> None:
            events.append("store")
            stores.append(self)

        def prepare(self, *args, **kwargs):
            assert args == (targets,)
            assert kwargs["commit_sha"] == "a" * 40
            assert kwargs["expected_linkages"] == ("master", "async_slave")
            events.append("prepare")
            return reference

    def revalidate(*_args, **_kwargs) -> None:
        events.append("revalidate")

    async def run(*args, **_kwargs) -> ProbePublicMetadata:
        assert args[:3] == (reference, stores[0], targets)
        events.append("network")
        return ProbePublicMetadata(
            artifact_id="JTP-public",
            artifact_sha256="d" * 64,
            plan_sha256="e" * 64,
            status="probe_completed_context_valid",
            q2_verdict="UNKNOWN",
            target_outcomes=("first", "second"),
            target_linkage_contexts=(
                "expected_linkage_observed",
                "expected_linkage_observed",
            ),
            target_report_frame_counts=(0, 1),
            target_reply_frame_counts=(1, 0),
            expected_identity_bindings_sha256=("f" * 64, "0" * 64),
            utc_started="2026-09-01T00:00:00Z",
            utc_completed="2026-09-01T00:00:01Z",
        )

    monkeypatch.setattr(transport_probe_cli, "_verify_probe_source_tree", verify_source)
    monkeypatch.setattr(transport_probe_cli, "load_config", load)
    monkeypatch.setattr(transport_probe_cli, "select_capture_pair", select)
    monkeypatch.setattr(transport_probe_cli, "TransportProbeStore", Store)
    monkeypatch.setattr(transport_probe_cli, "_revalidate_before_network", revalidate)
    monkeypatch.setattr(transport_probe_cli, "run_transport_probe", run)
    args = Namespace(
        repo_root=Path("."),
        config="private-config",
        first="master",
        second="slave",
        first_expected_linkage="master",
        second_expected_linkage="async_slave",
        output_root=Path("private-output"),
        probe_commit="a" * 40,
        control_port=12416,
        discovery_port=12414,
        timeout=5.0,
    )

    result = await transport_probe_cli._run(args)

    assert result == 0
    assert events == [
        "verify",
        "config",
        "select",
        "store",
        "prepare",
        "revalidate",
        "network",
    ]
    output = capsys.readouterr().out
    assert '"status": "probe_completed_context_valid"' in output
    assert '"q2_verdict": "UNKNOWN"' in output


def test_probe_pin_covers_all_value_and_admission_modules() -> None:
    pinned = {module for module, _relative in transport_probe_cli.PROBE_PINNED_SOURCES}

    assert {
        "jebao_flow.config",
        "jebao_flow.physical_identity",
        "jebao_flow.protocol.codec",
        "jebao_flow.protocol.discovery",
        "jebao_flow.protocol.models",
        "jebao_flow.protocol.profiles",
        "jebao_flow.protocol.schedule",
        "jebao_flow.protocol.schedule_wire",
        "jebao_flow.protocol.schema",
        "jebao_flow.protocol.session",
        "jebao_flow.read_only_collector",
        "jebao_flow.transport_probe",
        "jebao_flow.transport_probe_cli",
    } <= pinned
