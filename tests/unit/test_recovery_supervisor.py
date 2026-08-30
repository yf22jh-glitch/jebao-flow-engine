import asyncio
import os
from contextlib import contextmanager, nullcontext
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
from jebao_flow.devices.schedule_linkage import (
    ScheduleLinkageBusyError,
    ScheduleLinkageJournalClaimError,
    ScheduleLinkagePhase,
    ScheduleLinkageRecord,
)
from jebao_flow.devices.schedule_transaction import TemporaryScheduleRecord
from jebao_flow.devices.verification import (
    DeviceVerificationPhase,
    DeviceVerificationRecord,
    DeviceVerificationRecoveryReason,
)
from jebao_flow.exact_restore_store import (
    ExactRestoreJournalClaimError,
    ExactRestoreJournalStore,
)
from jebao_flow.hardware_test import HardwareTestIntentPhase
from jebao_flow.persistence.schedule_linkage import ScheduleLinkageJournalError
from jebao_flow.recovery_supervisor import (
    RecoveryArtifactError,
    RecoveryArtifacts,
    RecoveryDispatchBusyError,
    RecoverySupervisor,
    RecoverySupervisorDependencies,
    RecoverySupervisorStatus,
    run_once,
)
from jebao_flow.schedule_linkage_cli import (
    ScheduleLinkageCliError,
    ScheduleLinkageIntentOutcome,
    ScheduleLinkageIntentPhase,
)


def _config() -> AppConfig:
    return cast(AppConfig, SimpleNamespace(instance=SimpleNamespace(id="main")))


def _native_intent(
    phase: HardwareTestIntentPhase = HardwareTestIntentPhase.STARTED,
    *,
    version: int = 2,
) -> object:
    return SimpleNamespace(
        version=version,
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


def _schedule_intent(
    phase: ScheduleLinkageIntentPhase = ScheduleLinkageIntentPhase.STARTED,
    *,
    record: object | None = None,
) -> object:
    spec = getattr(record, "spec", SimpleNamespace(operation_id="schedule-operation"))
    snapshots = getattr(record, "snapshots", (SimpleNamespace(device_id="master"),))
    return SimpleNamespace(
        phase=phase,
        instance_id="main",
        operation_id="schedule-operation",
        preflight=SimpleNamespace(spec=spec, snapshots=snapshots),
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
            recovery_reason=(DeviceVerificationRecoveryReason.SAFETY_INTERLOCK if safety else None),
            created_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            updated_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            expires_at=now - timedelta(minutes=1) if stale else now + timedelta(seconds=5),
            operation_id="verification-operation",
        ),
    )


def _schedule_record(
    *,
    stale: bool = False,
    safety: bool = False,
    mutation_scope: str = "linkage_only",
) -> ScheduleLinkageRecord:
    now = datetime.now(UTC)
    return cast(
        ScheduleLinkageRecord,
        SimpleNamespace(
            phase=ScheduleLinkagePhase.RECOVERY_REQUIRED,
            mutation_scope=mutation_scope,
            created_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            updated_at=now - timedelta(minutes=2) if stale else now - timedelta(seconds=1),
            expires_at=now - timedelta(minutes=1) if stale else now + timedelta(seconds=5),
            operation_id="schedule-operation",
            spec=SimpleNamespace(operation_id="schedule-operation"),
            snapshots=(SimpleNamespace(device_id="master"),),
            error="safety_interlock" if safety else "role-only detach verification failed",
        ),
    )


def _schedule_artifacts(
    *,
    stale: bool = False,
    safety: bool = False,
    mutation_scope: str = "linkage_only",
) -> RecoveryArtifacts:
    record = _schedule_record(
        stale=stale,
        safety=safety,
        mutation_scope=mutation_scope,
    )
    return RecoveryArtifacts(
        schedule_intent=_schedule_intent(record=record),
        schedule_journal=record,
    )


def _dependencies(
    artifacts: RecoveryArtifacts,
    *,
    native_dispatch=None,
    verification_dispatch=None,
    schedule_dispatch=None,
    latch_present=False,
    exact_restore_admission=None,
) -> RecoverySupervisorDependencies:
    async def unused_native(config, args) -> int:
        del config, args
        raise AssertionError("native recovery must not be dispatched")

    async def unused_verification(config, args) -> int:
        del config, args
        raise AssertionError("verification recovery must not be dispatched")

    async def unused_schedule(config, args) -> int:
        del config, args
        raise AssertionError("schedule recovery must not be dispatched")

    return RecoverySupervisorDependencies(
        validate_safety_root=lambda: None,
        scan_artifacts=lambda: artifacts,
        latch_present=lambda: latch_present,
        native_dispatch=native_dispatch or unused_native,
        verification_dispatch=verification_dispatch or unused_verification,
        schedule_dispatch=schedule_dispatch or unused_schedule,
        exact_restore_admission=exact_restore_admission or (lambda: nullcontext(True)),
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
        schedule_dispatch=dispatch,
    )
    supervisor = RecoverySupervisor(_config(), dependencies=dependencies)

    assert await supervisor.run_once() is RecoverySupervisorStatus.IDLE
    assert callbacks == 0
    assert supervisor.recovery_in_flight is False


async def test_temporary_schedule_journal_blocks_all_automatic_recovery() -> None:
    callbacks = 0

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        return 0

    artifacts = RecoveryArtifacts(
        native_journal=_native_record(),
        temporary_schedule_journal=cast(TemporaryScheduleRecord, SimpleNamespace()),
    )
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            native_dispatch=dispatch,
            verification_dispatch=dispatch,
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert callbacks == 0


async def test_exact_restore_journal_blocks_all_automatic_recovery() -> None:
    callbacks = 0

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        return 0

    artifacts = RecoveryArtifacts(
        native_journal=_native_record(),
        exact_restore_journal=cast(
            object,
            SimpleNamespace(
                version=1,
                operation_id="exact-restore-operation",
                phase="restoring",
            ),
        ),
    )
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            native_dispatch=dispatch,
            verification_dispatch=dispatch,
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert callbacks == 0


async def test_exact_restore_created_after_scan_blocks_dispatch(tmp_path: Path) -> None:
    callbacks = 0
    store = ExactRestoreJournalStore._for_test(tmp_path / "exact-restore.json")

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        return 0

    @contextmanager
    def exact_restore_wins_before_admission():
        with store.claim():
            store.create({"version": 1, "operation_id": "exact", "phase": "prepared"})
        with store.claim():
            yield store.load() is None

    artifacts = RecoveryArtifacts(native_journal=_native_record())
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            native_dispatch=dispatch,
            exact_restore_admission=exact_restore_wins_before_admission,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert callbacks == 0


async def test_legacy_dispatch_holds_exact_claim_until_callback_finishes(tmp_path: Path) -> None:
    callbacks = 0
    owner = ExactRestoreJournalStore._for_test(tmp_path / "exact-restore.json")
    contender = ExactRestoreJournalStore._for_test(tmp_path / "exact-restore.json")

    @contextmanager
    def exact_restore_admission():
        with owner.claim():
            yield owner.load() is None

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        with pytest.raises(ExactRestoreJournalClaimError):
            with contender.claim():
                raise AssertionError("contender acquired during legacy dispatch")
        return 0

    artifacts = RecoveryArtifacts(native_journal=_native_record())
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            native_dispatch=dispatch,
            exact_restore_admission=exact_restore_admission,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert callbacks == 1
    with contender.claim():
        contender.create({"version": 1, "operation_id": "later", "phase": "prepared"})


async def test_busy_exact_admission_defers_legacy_dispatch() -> None:
    callbacks = 0

    @contextmanager
    def busy_admission():
        raise ExactRestoreJournalClaimError("busy")
        yield True  # pragma: no cover - contextmanager shape only

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(native_journal=_native_record()),
            native_dispatch=dispatch,
            exact_restore_admission=busy_admission,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.BUSY
    assert callbacks == 0


async def test_schedule_flow_v3_native_intent_always_requires_attended_recovery() -> None:
    callbacks = 0

    async def dispatch(config, args) -> int:
        nonlocal callbacks
        del config, args
        callbacks += 1
        return 0

    artifacts = RecoveryArtifacts(
        native_intent=_native_intent(version=3),
        native_journal=_native_record(),
    )
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(artifacts, native_dispatch=dispatch),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert callbacks == 0


@pytest.mark.parametrize("workflow", ["native", "verification", "schedule"])
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

    async def schedule(config, args) -> int:
        del config
        calls.append(("schedule", args))
        return 0

    if workflow == "native":
        artifacts = RecoveryArtifacts(native_journal=_native_record())
    elif workflow == "verification":
        artifacts = RecoveryArtifacts(verification_journal=_verification_record())
    else:
        record = _schedule_record()
        artifacts = RecoveryArtifacts(
            schedule_intent=_schedule_intent(record=record),
            schedule_journal=record,
        )
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            native_dispatch=native,
            verification_dispatch=verification,
            schedule_dispatch=schedule,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert len(calls) == 1
    called_workflow, args = calls[0]
    assert called_workflow == workflow
    assert args.confirm is None
    assert args.recovery_first is True
    assert (
        args.command
        == {
            "native": "recover-linkage",
            "verification": "recover-device-verification",
            "schedule": "recover-schedule-linkage",
        }[workflow]
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


async def test_started_schedule_intent_without_journal_dispatches_no_write_recovery() -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config
        calls += 1
        assert args.command == "recover-schedule-linkage"
        assert args.recovery_first is True
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(schedule_intent=_schedule_intent()),
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 1


async def test_recovery_required_schedule_intent_without_journal_requires_attendance() -> None:
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(
                schedule_intent=_schedule_intent(ScheduleLinkageIntentPhase.RECOVERY_REQUIRED)
            )
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED


async def test_schedule_changed_native_journal_requires_attendance_without_dispatch() -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        return 0

    record = _native_record()
    record.phase = LinkageTransactionPhase.RECOVERY_REQUIRED
    record.recovery_reason = LinkageRecoveryReason.SCHEDULE_CHANGED
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(native_journal=record),
            native_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert calls == 0


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
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert calls == 0


@pytest.mark.parametrize("other", ["native", "verification"])
async def test_schedule_conflict_with_other_nonterminal_has_zero_callbacks(
    other: str,
) -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        return 0

    values: dict[str, object] = {"schedule_intent": _schedule_intent()}
    values[f"{other}_intent"] = _native_intent() if other == "native" else _verification_intent()
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(**values),
            native_dispatch=dispatch,
            verification_dispatch=dispatch,
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED
    assert calls == 0


@pytest.mark.parametrize(
    "corrupt_name",
    [
        "native_linkage_journal_path",
        "schedule_linkage_journal_path",
        "temporary_schedule_journal_path",
        "exact_restore_journal_path",
    ],
)
async def test_corrupt_artifact_is_error_with_zero_callbacks(
    tmp_path: Path,
    monkeypatch,
    corrupt_name: str,
) -> None:
    paths = {
        "native_linkage_intent_path": tmp_path / "native-intent.json",
        "native_linkage_journal_path": tmp_path / "native-journal.json",
        "verification_intent_path": tmp_path / "verification-intent.json",
        "verification_journal_path": tmp_path / "verification-journal.json",
        "schedule_linkage_intent_path": tmp_path / "schedule-intent.json",
        "schedule_linkage_journal_path": tmp_path / "schedule-journal.json",
        "temporary_schedule_journal_path": tmp_path / "temporary-schedule.json",
        "exact_restore_journal_path": tmp_path / "exact-restore.json",
    }
    corrupt = paths[corrupt_name]
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
        schedule_dispatch=dispatch,
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
        _schedule_artifacts(stale=True),
        _schedule_artifacts(safety=True),
        _schedule_artifacts(mutation_scope="full_target"),
    ],
)
async def test_unsafe_automatic_recovery_requires_attendance_without_dispatch(
    artifacts: RecoveryArtifacts,
) -> None:
    supervisor = RecoverySupervisor(_config(), dependencies=_dependencies(artifacts))

    assert await supervisor.run_once() is RecoverySupervisorStatus.ATTENDED_REQUIRED


@pytest.mark.parametrize("mismatch", ["operation", "spec", "snapshots", "instance"])
async def test_schedule_candidate_requires_exact_intent_record_binding(
    mismatch: str,
) -> None:
    record = _schedule_record()
    intent = _schedule_intent(record=record)
    if mismatch == "operation":
        intent.operation_id = "different-operation"
    elif mismatch == "spec":
        intent.preflight.spec = SimpleNamespace(operation_id="different-operation")
    elif mismatch == "snapshots":
        intent.preflight.snapshots = (SimpleNamespace(device_id="different"),)
    else:
        intent.instance_id = "different-instance"
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(schedule_intent=intent, schedule_journal=record)
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR


async def test_schedule_journal_without_instance_bound_intent_is_error() -> None:
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(RecoveryArtifacts(schedule_journal=_schedule_record())),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR


async def test_schedule_record_within_expiry_grace_is_recoverable() -> None:
    now = datetime.now(UTC)
    record = _schedule_record()
    record.created_at = now - timedelta(seconds=20)
    record.updated_at = now - timedelta(seconds=20)
    record.expires_at = now - timedelta(seconds=10)
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
                schedule_intent=_schedule_intent(record=record),
                schedule_journal=record,
            ),
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 1


async def test_terminal_intent_with_fully_detached_journal_finishes_clear_crash() -> None:
    calls = 0
    record = _schedule_record()
    record.linkage_write_intent_device_ids = ("master", "slave")
    record.detached_device_ids = ("slave", "master")
    intent = _schedule_intent(
        ScheduleLinkageIntentPhase.TERMINAL,
        record=record,
    )
    intent.outcome = ScheduleLinkageIntentOutcome.ROLES_DETACHED

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config
        calls += 1
        assert args.command == "recover-schedule-linkage"
        assert args.recovery_first is True
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(schedule_intent=intent, schedule_journal=record),
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 1


async def test_terminal_intent_with_incomplete_detach_requires_attendance() -> None:
    record = _schedule_record()
    record.linkage_write_intent_device_ids = ("master", "slave")
    record.detached_device_ids = ("slave",)
    intent = _schedule_intent(
        ScheduleLinkageIntentPhase.TERMINAL,
        record=record,
    )
    intent.outcome = ScheduleLinkageIntentOutcome.ROLES_DETACHED
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            RecoveryArtifacts(schedule_intent=intent, schedule_journal=record)
        ),
    )

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
        exact_restore_admission=lambda: nullcontext(True),
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


@pytest.mark.parametrize(
    "busy_error",
    [
        ScheduleLinkageBusyError("busy"),
        ScheduleLinkageJournalClaimError("claimed"),
        ScheduleLinkageCliError("another schedule-linkage process is active"),
    ],
)
async def test_schedule_busy_errors_are_retried_on_next_poll(
    busy_error: BaseException,
) -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        if calls == 1:
            raise busy_error
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            _schedule_artifacts(),
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.BUSY
    assert await supervisor.run_once() is RecoverySupervisorStatus.RECOVERED
    assert calls == 2


async def test_generic_schedule_journal_error_is_latched_without_retry() -> None:
    calls = 0

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        raise ScheduleLinkageJournalError("private journal failure")

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            _schedule_artifacts(),
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR
    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR
    assert calls == 1


async def test_schedule_dispatch_self_mutation_is_latched_without_retry() -> None:
    calls = 0
    artifacts = _schedule_artifacts()

    async def dispatch(config, args) -> int:
        nonlocal calls
        del config, args
        calls += 1
        record = artifacts.schedule_journal
        assert record is not None
        record.updated_at += timedelta(microseconds=1)
        raise ScheduleLinkageJournalError("private journal failure after intent update")

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            artifacts,
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR
    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR
    assert calls == 1


async def test_schedule_dispatch_error_text_is_not_logged(caplog) -> None:
    secret = "private-device-endpoint"

    async def dispatch(config, args) -> int:
        del config, args
        raise RuntimeError(secret)

    caplog.set_level("INFO", logger="jebao_flow.recovery_supervisor")
    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            _schedule_artifacts(),
            schedule_dispatch=dispatch,
        ),
    )

    assert await supervisor.run_once() is RecoverySupervisorStatus.ERROR
    assert secret not in caplog.text


async def test_schedule_recovery_exposes_sanitized_inflight_status() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(config, args) -> int:
        del config, args
        started.set()
        await release.wait()
        return 0

    supervisor = RecoverySupervisor(
        _config(),
        dependencies=_dependencies(
            _schedule_artifacts(),
            schedule_dispatch=dispatch,
        ),
    )
    task = asyncio.create_task(supervisor.run_once())
    await started.wait()

    assert supervisor.status is RecoverySupervisorStatus.RECOVERING_SCHEDULE
    assert supervisor.recovery_in_flight is True
    release.set()
    assert await task is RecoverySupervisorStatus.RECOVERED


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
    monkeypatch.setattr(module, "schedule_linkage_intent_path", lambda: tmp_path / "missing-4")
    monkeypatch.setattr(module, "schedule_linkage_journal_path", lambda: tmp_path / "missing-5")
    monkeypatch.setattr(module, "temporary_schedule_journal_path", lambda: tmp_path / "missing-6")
    monkeypatch.setattr(module, "exact_restore_journal_path", lambda: tmp_path / "missing-7")

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
    monkeypatch.setattr(module, "schedule_linkage_intent_path", lambda: tmp_path / "missing-4")
    monkeypatch.setattr(module, "schedule_linkage_journal_path", lambda: tmp_path / "missing-5")
    monkeypatch.setattr(module, "temporary_schedule_journal_path", lambda: tmp_path / "missing-6")
    monkeypatch.setattr(module, "exact_restore_journal_path", lambda: tmp_path / "missing-7")

    with pytest.raises(RecoveryArtifactError, match="metadata is unsafe"):
        module._default_scan_artifacts()
