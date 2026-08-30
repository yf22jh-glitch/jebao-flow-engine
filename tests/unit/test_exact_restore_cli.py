from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow.exact_restore import ExactRestorePhase
from jebao_flow.exact_restore_cli import (
    _continue_final_restore,
    _continue_qualification,
    _failure,
    build_parser,
)
from jebao_flow.exact_restore_composition import ExactRestoreCompositionError


def _record(
    phase: ExactRestorePhase,
    cycle: str,
    *,
    qualification_parent_cycle: str | None = None,
) -> SimpleNamespace:
    parent = (
        SimpleNamespace(cycle=SimpleNamespace(value=qualification_parent_cycle))
        if qualification_parent_cycle is not None
        else None
    )
    return SimpleNamespace(
        phase=phase,
        cycle=SimpleNamespace(value=cycle),
        qualification_final_record=parent,
    )


class _Controller:
    def __init__(self, composition: SimpleNamespace) -> None:
        self.composition = composition
        self.executions = 0
        self.finalizations = 0
        self.promotions = 0
        self.clears = 0
        self.fail_execute = False

    async def execute(self):
        self.executions += 1
        if self.fail_execute:
            raise RuntimeError("private device detail")
        current = self.composition.current
        self.composition.current = _record(ExactRestorePhase.FINAL_VERIFIED, current.cycle.value)
        return self.composition.current

    async def finalize(self):
        self.finalizations += 1
        return object()

    def promote_to_baseline_restore(self, *, operation_id: str):
        assert operation_id == self.composition.manifest.baseline_operation_id
        self.promotions += 1
        self.composition.current = _record(
            ExactRestorePhase.PREPARED,
            "baseline_restore",
        )
        return self.composition.current

    def clear_after_receipt(self, receipt: object) -> None:
        assert receipt is not None
        self.composition.events.append("clear")
        self.clears += 1


class _Issuer:
    def __init__(self, composition: SimpleNamespace) -> None:
        self.composition = composition
        self.arms = 0
        self.reauthorizations = 0
        self.recoveries = 0

    def confirm_and_arm(self, controller: object, record: SimpleNamespace):
        del controller
        self.arms += 1
        self.composition.current = _record(ExactRestorePhase.ARMED, record.cycle.value)
        return self.composition.current

    def confirm_and_reauthorize(self, controller: object, record: SimpleNamespace):
        del controller
        self.reauthorizations += 1
        self.composition.current = _record(ExactRestorePhase.RESTORING, record.cycle.value)
        return self.composition.current

    def confirm_and_recover(self, controller: object, record: SimpleNamespace):
        del controller
        self.recoveries += 1
        self.composition.current = _record(ExactRestorePhase.RESTORING, record.cycle.value)
        return self.composition.current


@pytest.fixture
def composition(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    import jebao_flow.exact_restore_cli as module

    value = SimpleNamespace(
        current=_record(ExactRestorePhase.PREPARED, "sentinel_qualification"),
        manifest=SimpleNamespace(baseline_operation_id="er-baseline-test"),
        events=[],
    )
    value.controller = _Controller(value)
    monkeypatch.setattr(module, "_load_current", lambda candidate: candidate.current)
    bundle = SimpleNamespace(
        qualified_record=object(),
        persisted_at=datetime(2026, 8, 30, tzinfo=UTC),
    )
    monkeypatch.setattr(module, "_persist_q2_bundle", lambda *args: bundle)

    def stage_final(
        candidate: SimpleNamespace,
        selected_bundle: SimpleNamespace,
    ) -> SimpleNamespace:
        assert selected_bundle.persisted_at == datetime(2026, 8, 30, tzinfo=UTC)
        assert selected_bundle.qualified_record is not None
        candidate.events.append("stage_final")
        candidate.current = _record_factory = _record(
            ExactRestorePhase.PREPARED,
            "baseline_restore",
            qualification_parent_cycle="baseline_restore",
        )
        return _record_factory

    monkeypatch.setattr(module, "_stage_final_restore", stage_final)
    return value


def test_parser_has_no_confirmation_token_surface() -> None:
    parser = build_parser()
    rendered = parser.format_help()
    assert "token" not in rendered.casefold()
    with pytest.raises(ExactRestoreCompositionError, match="command_line_invalid"):
        parser.parse_args(["qualify", "--confirmation-token", "secret"])


def test_failure_reports_privacy_safe_baseline_age_diagnostic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        _failure(
            "baseline_expired_before_first_write",
            diagnostic={
                "reason": "baseline_age_exceeded",
                "capture_age_ms": 11_000,
                "conservative_age_ms": 31_000,
                "maximum_age_ms": 30_000,
                "maximum_pair_gap_ms": 20_000,
            },
        )
        == 2
    )

    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "baseline_expired_before_first_write"
    assert payload["diagnostic"]["conservative_age_ms"] == 31_000


def test_cli_import_keeps_write_transport_unloaded_until_attended_composition() -> None:
    script = """
import json, sys
sys.path.insert(0, 'src')
import jebao_flow.exact_restore_cli
print(json.dumps(sorted(name for name in sys.modules if name in {
    'jebao_flow.devices.lan', 'jebao_flow.protocol.control_session'
})))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []


@pytest.mark.asyncio
async def test_qualify_requires_two_attended_arms_and_clears_only_after_baseline_final(
    composition: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jebao_flow.exact_restore_cli as module

    issuer = _Issuer(composition)
    monkeypatch.setattr(
        module,
        "_persist_q2_bundle",
        lambda *args: (
            composition.events.append("qualified_bundle")
            or SimpleNamespace(
                qualified_record=object(),
                persisted_at=datetime(2026, 8, 30, tzinfo=UTC),
            )
        ),
    )

    result = await _continue_qualification(
        composition,
        recover_first=False,
        issuer=issuer,
    )

    assert result.cycle.value == "baseline_restore"
    assert issuer.arms == 2
    assert issuer.reauthorizations == 0
    assert composition.controller.executions == 2
    assert composition.controller.finalizations == 2
    assert composition.controller.promotions == 1
    assert composition.controller.clears == 1
    assert result.phase is ExactRestorePhase.PREPARED
    assert composition.events == ["qualified_bundle", "clear", "stage_final"]


@pytest.mark.asyncio
async def test_recover_requires_recovery_phase_and_uses_recovery_grant(
    composition: SimpleNamespace,
) -> None:
    issuer = _Issuer(composition)
    with pytest.raises(ExactRestoreCompositionError, match="recovery_not_required"):
        await _continue_qualification(composition, recover_first=True, issuer=issuer)

    composition.current = _record(ExactRestorePhase.RECOVERY_REQUIRED, "baseline_restore")
    result = await _continue_qualification(composition, recover_first=True, issuer=issuer)
    assert result.cycle.value == "baseline_restore"
    assert issuer.recoveries == 1
    assert composition.controller.executions == 1
    assert composition.controller.clears == 1


@pytest.mark.asyncio
async def test_phase5_restore_uses_staged_parent_and_never_promotes(
    composition: SimpleNamespace,
) -> None:
    composition.current = _record(
        ExactRestorePhase.PREPARED,
        "baseline_restore",
        qualification_parent_cycle="baseline_restore",
    )
    issuer = _Issuer(composition)

    result = await _continue_final_restore(
        composition,
        recover_first=False,
        issuer=issuer,
    )

    assert result.phase is ExactRestorePhase.FINAL_VERIFIED
    assert issuer.arms == 1
    assert composition.controller.executions == 1
    assert composition.controller.finalizations == 1
    assert composition.controller.promotions == 0
    assert composition.controller.clears == 1


@pytest.mark.asyncio
async def test_failure_preserves_journal_and_never_clears(
    composition: SimpleNamespace,
) -> None:
    issuer = _Issuer(composition)
    composition.controller.fail_execute = True

    with pytest.raises(RuntimeError, match="private device detail"):
        await _continue_qualification(composition, recover_first=False, issuer=issuer)

    assert composition.current.phase is ExactRestorePhase.ARMED
    assert composition.controller.clears == 0
