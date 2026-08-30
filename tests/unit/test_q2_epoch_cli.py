from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow.exact_restore_composition import ExactRestoreCompositionError
from jebao_flow.q2_epoch import (
    ExactRestoreEvidence,
    PilotTimingEvidence,
    Q2EpochKind,
    Q2ScheduleExpectation,
    Q2Thresholds,
)
from jebao_flow.q2_epoch_cli import (
    Q2CliError,
    _parse_epoch_spec,
    _prepare_pair,
    _read_owner_only_json,
    build_parser,
    main,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _spec(kind: Q2EpochKind) -> dict[str, object]:
    return {
        "version": 1,
        "epoch_kind": kind.value,
        "planned_pair_count": 286,
        "requested_cadence_seconds": 20,
        "timing_evidence": [
            {"series_id": "JFS-timing-a", "series_sha256": SHA_A},
            {"series_id": "JFS-timing-b", "series_sha256": SHA_B},
        ],
        "schedule_expectation": {
            "master_schedule_image_sha256": SHA_C,
            "slave_schedule_image_sha256": SHA_D,
        },
    }


def _evidence() -> ExactRestoreEvidence:
    return ExactRestoreEvidence(
        baseline_artifact_id="ERB-baseline",
        baseline_sha256=SHA_A,
        sentinel_receipt_id="ERQ-sentinel",
        sentinel_receipt_sha256=SHA_B,
        baseline_receipt_id="ERQ-baseline",
        baseline_receipt_sha256=SHA_C,
        final_restore_operation_id="er-final-operation",
        final_restore_record_sha256=SHA_D,
        expected_identity_bindings_sha256=(SHA_A, SHA_B),
    )


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        collector_commit=SHA_A,
        config="private-config",
        first="a",
        second="b",
        artifact_root="private-artifacts",
        control_epoch_spec="control-spec",
        async_epoch_spec="async-spec",
        operation_manifest="operation-manifest",
    )


def test_parser_exposes_atomic_pair_preparation_without_write_controls() -> None:
    parsed = build_parser().parse_args(
        [
            "prepare-pair",
            "--collector-commit",
            SHA_A,
            "--config",
            "config",
            "--first",
            "a",
            "--second",
            "b",
            "--artifact-root",
            "artifacts",
            "--control-epoch-spec",
            "control",
            "--async-epoch-spec",
            "async",
            "--operation-manifest",
            "operation",
        ]
    )

    assert parsed.command == "prepare-pair"
    assert "write" not in vars(parsed)


def test_q2_cli_import_graph_does_not_load_hardware_write_or_frozen_modules() -> None:
    source = """
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'src'))
import jebao_flow.q2_epoch_cli
forbidden = (
    'jebao_flow.devices.lan',
    'jebao_flow.protocol.control_session',
    'jebao_flow.devices.linkage',
    'jebao_flow.devices.schedule_flow_experiment',
    'jebao_flow.devices.schedule_linkage',
    'jebao_flow.devices.schedule_transaction',
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


def test_owner_only_epoch_spec_is_stable_and_private(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(_spec(Q2EpochKind.ASYNC)))
    path.chmod(0o600)

    assert _read_owner_only_json(path)["epoch_kind"] == "async"

    path.chmod(0o640)
    with pytest.raises(Q2CliError, match="epoch_spec_not_owner_only"):
        _read_owner_only_json(path)


def test_epoch_spec_keeps_discriminating_values_fixed() -> None:
    kind, count, cadence, timing, schedule = _parse_epoch_spec(
        _spec(Q2EpochKind.INDEPENDENT_CONTROL)
    )

    assert kind is Q2EpochKind.INDEPENDENT_CONTROL
    assert count == 286
    assert cadence == 20.0
    assert timing == (
        PilotTimingEvidence("JFS-timing-a", SHA_A),
        PilotTimingEvidence("JFS-timing-b", SHA_B),
    )
    assert schedule == Q2ScheduleExpectation(SHA_C, SHA_D)


def test_epoch_spec_rejects_a_series_too_short_for_real_thirty_minute_slots() -> None:
    value = _spec(Q2EpochKind.INDEPENDENT_CONTROL)
    value["planned_pair_count"] = 285

    with pytest.raises(Q2CliError, match="epoch_spec_series_too_short"):
        _parse_epoch_spec(value)


def test_prepare_pair_prevalidates_both_specs_before_any_durable_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jebao_flow.q2_epoch_cli as module

    calls: list[str] = []
    monkeypatch.setattr(module, "_attest", lambda _commit: object())
    monkeypatch.setattr(module, "load_config", lambda _path: object())
    monkeypatch.setattr(
        module,
        "select_capture_pair",
        lambda *_args: (
            SimpleNamespace(identity_binding_sha256=SHA_A),
            SimpleNamespace(identity_binding_sha256=SHA_B),
        ),
    )
    monkeypatch.setattr(
        module,
        "_read_owner_only_json",
        lambda path: (
            _spec(Q2EpochKind.INDEPENDENT_CONTROL)
            if path == "control-spec"
            else {**_spec(Q2EpochKind.ASYNC), "planned_pair_count": 287}
        ),
    )
    monkeypatch.setattr(
        module,
        "_stores",
        lambda _root: (
            SimpleNamespace(prepare=lambda *args, **kwargs: calls.append("plan")),
            object(),
        ),
    )

    with pytest.raises(Q2CliError, match="epoch_pair_spec_mismatch"):
        _prepare_pair(_args())
    assert calls == []


def test_prepare_pair_binds_both_manifests_to_one_staged_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jebao_flow.q2_epoch_cli as module

    targets = (
        SimpleNamespace(identity_binding_sha256=SHA_A),
        SimpleNamespace(identity_binding_sha256=SHA_B),
    )
    evidence = _evidence()
    thresholds = Q2Thresholds(
        requested_cadence_ns=20_000_000_000,
        maximum_actual_cadence_ns=22_000_000_000,
        maximum_pair_gap_ns=13_000_000_000,
        freshness_window_ns=44_000_000_000,
        boundary_exclusion_ns=13_000_000_000,
        stability_window_ns=300_000_000_000,
    )

    class Collector:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        def prepare(self, _targets, **kwargs):
            kind = kwargs["epoch"]
            self.kinds.append(kind)
            return SimpleNamespace(
                plan_artifact_id=f"JFP-{kind}",
                plan_sha256=SHA_A if kind == "independent_control" else SHA_B,
                series_id=f"JFS-{kind}",
            )

    class EpochStore:
        def __init__(self) -> None:
            self.claims: list[tuple[Q2EpochKind, ExactRestoreEvidence]] = []

        def prepare(self, _collector, plan, *, epoch_kind, restore_evidence, **_kwargs):
            self.claims.append((epoch_kind, restore_evidence))
            return SimpleNamespace(
                manifest_id=f"Q2M-{epoch_kind.value}",
                manifest_sha256=plan.plan_sha256,
            )

    class Verifier:
        def derive_q2_exact_restore_evidence(self) -> ExactRestoreEvidence:
            return evidence

    collector = Collector()
    epoch_store = EpochStore()
    monkeypatch.setattr(module, "_attest", lambda _commit: object())
    monkeypatch.setattr(module, "load_config", lambda _path: object())
    monkeypatch.setattr(module, "select_capture_pair", lambda *_args: targets)
    monkeypatch.setattr(
        module,
        "_read_owner_only_json",
        lambda path: (
            _spec(Q2EpochKind.INDEPENDENT_CONTROL)
            if path == "control-spec"
            else _spec(Q2EpochKind.ASYNC)
        ),
    )
    monkeypatch.setattr(module, "_stores", lambda _root: (collector, epoch_store))
    monkeypatch.setattr(module, "_exact_verifier", lambda _path: Verifier())
    monkeypatch.setattr(
        module,
        "derive_q2_thresholds_from_pilots",
        lambda *_args, **_kwargs: thresholds,
    )

    result = _prepare_pair(_args())

    assert result["status"] == "q2_epoch_pair_prepared_no_network"
    assert collector.kinds == ["independent_control", "async"]
    assert epoch_store.claims == [
        (Q2EpochKind.INDEPENDENT_CONTROL, evidence),
        (Q2EpochKind.ASYNC, evidence),
    ]
    assert result["final_restore_record_sha256"] == SHA_D


def test_main_maps_private_failures_without_reflecting_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import jebao_flow.q2_epoch_cli as module

    async def fail(_args):
        raise ExactRestoreCompositionError("operation_manifest_invalid")

    monkeypatch.setattr(module, "_run", fail)
    assert (
        main(
            [
                "combine",
                "--collector-commit",
                SHA_A,
                "--artifact-root",
                "private",
                "--control-manifest-id",
                "control",
                "--control-manifest-sha256",
                SHA_A,
                "--control-receipt-sha256",
                SHA_B,
                "--async-manifest-id",
                "async",
                "--async-manifest-sha256",
                SHA_C,
                "--async-receipt-sha256",
                SHA_D,
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload == {"code": "operation_manifest_invalid", "status": "q2_epoch_refused"}
