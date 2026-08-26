import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from jebao_flow.config import AppConfig
from jebao_flow.device_verification_cli import VerificationIntentPhase
from jebao_flow.devices.linkage import (
    LinkageRecoveryReason,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
)
from jebao_flow.devices.verification import (
    DeviceVerificationPhase,
    DeviceVerificationRecord,
    DeviceVerificationRecoveryReason,
)
from jebao_flow.hardware_test import HardwareTestIntentPhase
from jebao_flow.recovery_supervisor import (
    RecoveryArtifactError,
    RecoveryArtifacts,
    RecoveryDispatchBusyError,
    RecoverySupervisor,
    RecoverySupervisorDependencies,
    RecoverySupervisorStatus,
    run_once,
)


def _config() -> AppConfig:
    return cast(AppConfig, SimpleNamespace(instance=SimpleNamespace(id="main")))


def _native_intent(
    phase: HardwareTestIntentPhase = HardwareTestIntentPhase.STARTED,
) -> object:
    return SimpleNamespace(
        phase=phase,
        instance_id="main",
        operation_id="native-operation",
    )


def _verification_intent(
    phase: VerificationIntentPhase = VerificationIntentPhase.STARTED,
) -> object:
    return SimpleNamespace(
        phase=phase,
        instance_id="main",
        operation_id="verification-operation",
    )


def _native_record(
    *,
    stale: bool = False,
    safety: bool = False,
    timer_enabled: bool = False,
) -> LinkageTransactionRecord:
    now = datetime.now(UTC)
    return cast(
        LinkageTransactionRecord,
        SimpleNamespace(
            phase=(
                LinkageTransactionPhase.RECOVERY_REQUIRED
                if safety
                else LinkageTransactionPhase.ACTIVE
            ),
            recovery_reason=(LinkageRecoveryReason.SAFETY_INTERLOCK if safety else None),
            created_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            updated_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            expires_at=now - timedelta(minutes=1) if stale else now + timedelta(seconds=5),
            snapshots=(SimpleNamespace(timer_enabled=timer_enabled),),
            operation_id="native-operation",
        ),
    )


def _verification_record(
    *,
    stale: bool = False,
    safety: bool = False,
) -> DeviceVerificationRecord:
    now = datetime.now(UTC)
    return cast(
        DeviceVerificationRecord,
        SimpleNamespace(
            phase=(
                DeviceVerificationPhase.RECOVERY_REQUIRED
                if safety
                else DeviceVerificationPhase.LOWER_POWER_ACTIVE
            ),
            recovery_reason=(
                DeviceVerificationRecoveryReason.SAFETY_INTERLOCK if safety else None
            ),
            created_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            updated_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            expires_at=now - timedelta(minutes=1) if stale else now + timedelta(seconds=5),
            operation_id="verification-operation",
        ),
    )


def _dependencies(
    artifacts: RecoveryArtifacts,
    *,
    native_dispatch=None,
    verification_dispatch=None,
    latch_present=False,
) -> RecoverySupervisorDependencies:
    async def unused_native(config, args) -> int:
        del config, args
        raise AssertionError("native recovery must not be dispatched")

    async def unused_verification(config, args) -> int:
        del config, args
        raise AssertionError("verification recovery must not be dispatched")

    return RecoverySupervisorDependencies(
        validate_safety_root=lambda: None,
        scan_artifacts=lambda: artifacts,
        latch_present=lambda: latch_present,
        native_dispatch=native_dispatch or unused_native,
        verification_dispatch=verification_dispatch or unused_verification,
    )


async def test_idle_poll_has_zero_recovery_callbacks() -> None:
    callbacks = 0

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        return 0

    dependencies = _dependencies(
        RecoveryArtifacts(),
        native_dispatch=dispatch,
        verification_dispatch=dispatch,
    )
    supervisor = RecoverySupervisor(_config(), dependencies=dependencies)

    assert await supervisor.run_once() is RecoverySupervisorStatus.IDLE
    assert callbacks == 0
    assert supervisor.recovery_in_flight is False


@pytest.mark.parametrize("workflow", ["native", "verification"])
async def test_one_workflow_dispatches_only_recovery_first_namespace(workflow: str) -> None:
    calls: list[tuple[str, object]] = []

    async def native(config, args) -> int:
        del config
        calls.append(("native", args))
        return 0

    async def verification(config, args) -> int:
        del config
        calls.append(("verification", args))
        return 0

    artifacts = (
        RecoveryArtifacts(native_journal=_native_record())
        if workflow == "native"
        else RecoveryArtifacts(verification_journal=_verification_record())
    )
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            native_dispatch=native,
            verification_dispatch=verification,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert len(calls) == 1
    called_workflow, args = calls[0]
    assert called_workflow == workflow
    assert args.confirm is None
    assert args.recovery_first is True
    assert args.command == (
        "recover-linkage" if workflow == "native" else "recover-device-verification"
    )


@pytest.mark.parametrize(
    "phase",
    [HardwareTestIntentPhase.STARTED, HardwareTestIntentPhase.RECOVERY_REQUIRED],
)
async def test_recovery_intent_without_journal_uses_recovery_dispatch(
    phase: HardwareTestIntentPhase,
) -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config
        calls += 1
        assert args.command == "recover-linkage"
        assert args.recovery_first is True
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(native_intent=_native_intent(phase)),
            native_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 1


async def test_dual_nonterminal_conflict_has_zero_callbacks() -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(
                native_intent=_native_intent(),
                verification_intent=_verification_intent(),
            ),
            native_dispatch=dispatch,
            verification_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert calls == 0


async def test_corrupt_artifact_is_error_with_zero_callbacks(tmp_path: Path, monkeypatch) -> None:
    paths = {
        "native_linkage_intent_path": tmp_path / "native-intent.json",
        "native_linkage_journal_path": tmp_path / "native-journal.json",
        "verification_intent_path": tmp_path / "verification-intent.json",
        "verification_journal_path": tmp_path / "verification-journal.json",
    }
    corrupt = paths["native_linkage_journal_path"]
    corrupt.write_text("not-json", encoding="utf-8")
    corrupt.chmod(0o600)

    import jebao_flow.recovery_supervisor as module

    for name, path in paths.items():
        monkeypatch.setattr(module, name, lambda path=path: path)
    monkeypatch.setattr(module, "emergency_stop_latch_path", lambda: tmp_path / "latch")
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        return 0

    dependencies = RecoverySupervisorDependencies(
        validate_safety_root=lambda: None,
        native_dispatch=dispatch,
        verification_dispatch=dispatch,
    )
    supervisor = RecoverySupervisor(_config(), dependencies=dependencies)

    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR
    assert calls == 0


@pytest.mark.parametrize(
    "artifacts",
    [
        RecoveryArtifacts(native_journal=_native_record(stale=True)),
        RecoveryArtifacts(native_journal=_native_record(safety=True)),
        RecoveryArtifacts(native_journal=_native_record(timer_enabled=True)),
        RecoveryArtifacts(verification_journal=_verification_record(stale=True)),
        RecoveryArtifacts(verification_journal=_verification_record(safety=True)),
    ],
)
async def test_unsafe_automatic_recovery_requires_attendance_without_dispatch(
    artifacts: RecoveryArtifacts,
) -> None:
    supervisor = RecoverySupervisor(_config(), dependencies=_dependencies(artifacts))

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED


async def test_attended_required_is_visible_at_default_warning_level(caplog) -> None:
    caplog.set_level("WARNING", logger="jebao_flow.recovery_supervisor")
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(RecoveryArtifacts(native_journal=_native_record(stale=True))),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert any(
        getattr(record, "supervisor_status", None) == "attended_required"
        for record in caplog.records
    )


async def test_persistent_latch_blocks_dispatch_until_cleared() -> None:
    latch = True
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        return 0

    dependencies = RecoverySupervisorDependencies(
        validate_safety_root=lambda: None,
        scan_artifacts=lambda: RecoveryArtifacts(native_journal=_native_record()),
        latch_present=lambda: latch,
        native_dispatch=dispatch,
        verification_dispatch=dispatch,
    )
    supervisor = RecoverySupervisor(_config(), dependencies=dependencies)

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert calls == 0
    latch = False
    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 1


async def test_busy_is_retried_on_next_poll() -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        if calls == 1:
            raise RecoveryDispatchBusyError("busy")
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(native_journal=_native_record()),
            native_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.BUSY
    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 2


async def test_stop_waits_for_inflight_recovery() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = False

    async def dispatch(config, args) -> int:
        nonlocal completed
        del config, args
        started.set()
        await release.wait()
        completed = True
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        poll_interval_seconds=0.1,
        dependencies=_dependencies(
            RecoveryArtifacts(verification_intent=_verification_intent()),
            verification_dispatch=dispatch,
        ),
    )
    task = asyncio.create_task(supervisor.run())
    await started.wait()

    supervisor.request_stop()
    await asyncio.sleep(0)
    assert task.done() is False
    assert supervisor.recovery_in_flight is True

    release.set()
    assert await task is RecoverySupervisorStatus.STOPPED
    assert completed is True
    assert supervisor.recovery_in_flight is False


@pytest.mark.parametrize("interval", [0, 0.099, 60.001, float("inf"), float("nan"), True])
def test_poll_interval_is_strictly_bounded(interval: float) -> None:
    with pytest.raises(ValueError):
        RecoverySupervisor(_config(), poll_interval_seconds=interval)


async def test_public_run_once_validates_root_once_and_returns_status() -> None:
    validations = 0

    def validate() -> None:
        nonlocal validations
        validations += 1

    status = await run_once(
        _config(),
        dependencies=RecoverySupervisorDependencies(
            validate_safety_root=validate,
            scan_artifacts=RecoveryArtifacts,
            latch_present=lambda: False,
        ),
    )

    assert status is RecoverySupervisorStatus.IDLE
    assert validations == 1


def test_artifact_reader_rejects_symlink_without_following_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("private", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "native-journal.json"
    os.symlink(target, link)

    import jebao_flow.recovery_supervisor as module

    monkeypatch.setattr(module, "native_linkage_journal_path", lambda: link)
    monkeypatch.setattr(module, "native_linkage_intent_path", lambda: tmp_path / "missing-1")
    monkeypatch.setattr(module, "verification_intent_path", lambda: tmp_path / "missing-2")
    monkeypatch.setattr(module, "verification_journal_path", lambda: tmp_path / "missing-3")

    with pytest.raises(RecoveryArtifactError):
        module._default_scan_artifacts()


def test_artifact_reader_rejects_hardlink(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "native-journal.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)
    os.link(path, tmp_path / "journal-alias")

    import jebao_flow.recovery_supervisor as module

    monkeypatch.setattr(module, "native_linkage_journal_path", lambda: path)
    monkeypatch.setattr(module, "native_linkage_intent_path", lambda: tmp_path / "missing-1")
    monkeypatch.setattr(module, "verification_intent_path", lambda: tmp_path / "missing-2")
    monkeypatch.setattr(module, "verification_journal_path", lambda: tmp_path / "missing-3")

    with pytest.raises(RecoveryArtifactError, match="metadata is unsafe"):
        module._default_scan_artifacts()
