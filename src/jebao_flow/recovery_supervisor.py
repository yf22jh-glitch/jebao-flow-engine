"""Always-on, recovery-only supervisor for attended hardware workflows.

The supervisor is deliberately inert while no durable recovery artifact exists.  Its idle poll
opens only the six fixed intent/journal files and the persistent emergency-stop marker; device
discovery, TCP connections, and all control writes remain inside the already-audited recovery
dispatchers and are reached only for one unambiguous, fresh automatic-recovery candidate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import math
import os
import signal
import stat
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ValidationError

from jebao_flow.config import AppConfig, load_config
from jebao_flow.device_verification_cli import (
    DeviceVerificationCliError,
    DeviceVerificationIntent,
    VerificationIntentPhase,
)
from jebao_flow.device_verification_cli import dispatch as dispatch_device_verification
from jebao_flow.devices.linkage import (
    LinkageJournalClaimError,
    LinkageRecoveryReason,
    LinkageTransactionBusyError,
    LinkageTransactionRecord,
)
from jebao_flow.devices.schedule_linkage import (
    ScheduleLinkageBusyError,
    ScheduleLinkageJournalClaimError,
    ScheduleLinkageRecord,
)
from jebao_flow.devices.schedule_transaction import TemporaryScheduleRecord
from jebao_flow.devices.verification import (
    DeviceVerificationBusyError,
    DeviceVerificationRecord,
)
from jebao_flow.hardware_guard import HardwareOperationBusyError
from jebao_flow.hardware_safety import (
    emergency_stop_latch_path,
    native_linkage_intent_path,
    native_linkage_journal_path,
    schedule_linkage_intent_path,
    schedule_linkage_journal_path,
    temporary_schedule_journal_path,
    validate_hardware_safety_root,
    verification_intent_path,
    verification_journal_path,
)
from jebao_flow.hardware_test import (
    HardwareTestError,
    HardwareTestIntent,
    HardwareTestIntentPhase,
)
from jebao_flow.hardware_test import _dispatch as dispatch_native_linkage
from jebao_flow.logging import configure_logging
from jebao_flow.schedule_linkage_cli import (
    ScheduleLinkageCliError,
    ScheduleLinkageIntent,
    ScheduleLinkageIntentOutcome,
    ScheduleLinkageIntentPhase,
)
from jebao_flow.schedule_linkage_cli import dispatch as dispatch_schedule_linkage

_LOGGER = logging.getLogger(__name__)
_AUTOMATIC_RECOVERY_GRACE_SECONDS = 30
_MAX_ARTIFACT_BYTES = 1024 * 1024


class RecoverySupervisorStatus(StrEnum):
    """Sanitized supervisor state suitable for logs and service health reporting."""

    STARTING = "starting"
    IDLE = "idle"
    RECOVERING_NATIVE = "recovering_native"
    RECOVERING_VERIFICATION = "recovering_verification"
    RECOVERING_SCHEDULE = "recovering_schedule"
    RECOVERED = "recovered"
    BUSY = "busy"
    ATTENDED_REQUIRED = "attended_required"
    ERROR = "error"
    STOPPED = "stopped"


class RecoveryArtifactError(RuntimeError):
    """A fixed safety artifact could not be inspected without following unsafe metadata."""


class RecoveryDispatchBusyError(RuntimeError):
    """Dependency-injection marker used when a recovery dispatcher is temporarily busy."""


@dataclass(frozen=True, slots=True)
class RecoveryArtifacts:
    """One consistent-enough read of the durable workflow artifacts.

    The dispatchers re-read and lease their stores before connection, so this scan is only a
    fail-closed eligibility decision and never an authority for a physical write.
    """

    native_intent: HardwareTestIntent | None = None
    native_journal: LinkageTransactionRecord | None = None
    verification_intent: DeviceVerificationIntent | None = None
    verification_journal: DeviceVerificationRecord | None = None
    schedule_intent: ScheduleLinkageIntent | None = None
    schedule_journal: ScheduleLinkageRecord | None = None
    temporary_schedule_journal: TemporaryScheduleRecord | None = None


ArtifactScanner = Callable[[], RecoveryArtifacts]
LatchReader = Callable[[], bool]
NativeDispatcher = Callable[[AppConfig, argparse.Namespace], Awaitable[int]]
VerificationDispatcher = Callable[[AppConfig, argparse.Namespace], Awaitable[int]]
ScheduleDispatcher = Callable[[AppConfig, argparse.Namespace], Awaitable[int]]
Clock = Callable[[], datetime]
BusyClassifier = Callable[[BaseException], bool]


def _load_fixed_artifact[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
) -> ModelT | None:
    """Read one optional 0600 artifact without following its final path component."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise RecoveryArtifactError("safe artifact reads require O_NOFOLLOW")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RecoveryArtifactError("safety artifact metadata is unavailable") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_ARTIFACT_BYTES
    ):
        raise RecoveryArtifactError("safety artifact metadata is unsafe")

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_size > _MAX_ARTIFACT_BYTES
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RecoveryArtifactError("safety artifact changed during inspection")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            return model.model_validate_json(stream.read(_MAX_ARTIFACT_BYTES + 1))
    except RecoveryArtifactError:
        raise
    except (OSError, ValidationError, ValueError) as error:
        raise RecoveryArtifactError("safety artifact is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _default_scan_artifacts() -> RecoveryArtifacts:
    return RecoveryArtifacts(
        native_intent=_load_fixed_artifact(
            native_linkage_intent_path(),
            HardwareTestIntent,
        ),
        native_journal=_load_fixed_artifact(
            native_linkage_journal_path(),
            LinkageTransactionRecord,
        ),
        verification_intent=_load_fixed_artifact(
            verification_intent_path(),
            DeviceVerificationIntent,
        ),
        verification_journal=_load_fixed_artifact(
            verification_journal_path(),
            DeviceVerificationRecord,
        ),
        schedule_intent=_load_fixed_artifact(
            schedule_linkage_intent_path(),
            ScheduleLinkageIntent,
        ),
        schedule_journal=_load_fixed_artifact(
            schedule_linkage_journal_path(),
            ScheduleLinkageRecord,
        ),
        temporary_schedule_journal=_load_fixed_artifact(
            temporary_schedule_journal_path(),
            TemporaryScheduleRecord,
        ),
    )


def _default_latch_present() -> bool:
    try:
        emergency_stop_latch_path().lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise RecoveryArtifactError("persistent safety latch is unavailable") from error
    # Every filesystem object, including a broken symlink, is an active fail-closed latch.
    return True


async def _default_native_dispatch(config: AppConfig, args: argparse.Namespace) -> int:
    return await dispatch_native_linkage(config, args)


async def _default_verification_dispatch(config: AppConfig, args: argparse.Namespace) -> int:
    return await dispatch_device_verification(config, args)


async def _default_schedule_dispatch(config: AppConfig, args: argparse.Namespace) -> int:
    return await dispatch_schedule_linkage(config, args)


def _default_is_busy(error: BaseException) -> bool:
    if isinstance(
        error,
        RecoveryDispatchBusyError
        | HardwareOperationBusyError
        | LinkageJournalClaimError
        | LinkageTransactionBusyError
        | DeviceVerificationBusyError
        | ScheduleLinkageBusyError
        | ScheduleLinkageJournalClaimError,
    ):
        return True
    # The CLI intent leases predate typed busy errors. Match only their exact sanitized
    # messages; never publish or log exception text from a lower network/device layer.
    return (
        isinstance(error, HardwareTestError)
        and str(error) == "another hardware-test process is already running"
    ) or (
        isinstance(error, DeviceVerificationCliError)
        and str(error) == "another device-verification process is active"
    ) or (
        isinstance(error, ScheduleLinkageCliError)
        and str(error) == "another schedule-linkage process is active"
    )


@dataclass(frozen=True, slots=True)
class RecoverySupervisorDependencies:
    """Injectable filesystem, clock, and dispatcher boundary for deterministic tests."""

    validate_safety_root: Callable[[], None] = validate_hardware_safety_root
    scan_artifacts: ArtifactScanner = _default_scan_artifacts
    latch_present: LatchReader = _default_latch_present
    native_dispatch: NativeDispatcher = _default_native_dispatch
    verification_dispatch: VerificationDispatcher = _default_verification_dispatch
    schedule_dispatch: ScheduleDispatcher = _default_schedule_dispatch
    clock: Clock = lambda: datetime.now(UTC)
    is_busy: BusyClassifier = _default_is_busy


DEFAULT_DEPENDENCIES = RecoverySupervisorDependencies()


def _phase_is_nonterminal(intent: object | None) -> bool:
    if intent is None:
        return False
    phase = getattr(intent, "phase", None)
    return phase not in {
        HardwareTestIntentPhase.TERMINAL,
        VerificationIntentPhase.TERMINAL,
        ScheduleLinkageIntentPhase.TERMINAL,
    }


def _phase_needs_recovery(intent: object | None) -> bool:
    if intent is None:
        return False
    return getattr(intent, "phase", None) in {
        HardwareTestIntentPhase.STARTED,
        HardwareTestIntentPhase.RECOVERY_REQUIRED,
        VerificationIntentPhase.STARTED,
        VerificationIntentPhase.RECOVERY_REQUIRED,
        ScheduleLinkageIntentPhase.STARTED,
        ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
    }


def _artifact_fingerprint(artifacts: RecoveryArtifacts) -> str:
    digest = hashlib.sha256()
    for artifact in (
        artifacts.native_intent,
        artifacts.native_journal,
        artifacts.verification_intent,
        artifacts.verification_journal,
        artifacts.schedule_intent,
        artifacts.schedule_journal,
        artifacts.temporary_schedule_journal,
    ):
        if artifact is None:
            encoded = b"none"
        elif isinstance(artifact, BaseModel):
            encoded = artifact.model_dump_json().encode()
        else:  # Test doubles remain private; only the digest is retained or logged.
            encoded = repr(artifact).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _timestamps_are_stale(record: object, now: datetime) -> bool:
    try:
        created_at = record.created_at
        updated_at = record.updated_at
        expires_at = record.expires_at
        if (
            now.tzinfo is None
            or now.utcoffset() is None
            or created_at.tzinfo is None
            or created_at.utcoffset() is None
            or updated_at.tzinfo is None
            or updated_at.utcoffset() is None
            or expires_at.tzinfo is None
            or expires_at.utcoffset() is None
        ):
            return True
        deadline = expires_at + timedelta(seconds=_AUTOMATIC_RECOVERY_GRACE_SECONDS)
        return now < created_at or now < updated_at or now > deadline
    except (AttributeError, OverflowError, TypeError):
        return True


def _has_safety_recovery_reason(record: object) -> bool:
    reason = getattr(record, "recovery_reason", None)
    value = getattr(reason, "value", reason)
    return isinstance(value, str) and value.startswith("safety_")


def _has_safety_like_error(record: object) -> bool:
    """Recognize safety markers without exposing journal error text to logs or output."""

    error = getattr(record, "error", None)
    if error is None:
        return False
    if not isinstance(error, str):
        return True
    normalized = error.casefold().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("safety", "failsafe", "interlock", "emergency", "e_stop", "latch")
    )


def _native_timer_restore_requires_attendance(record: object) -> bool:
    try:
        return any(snapshot.timer_enabled is not False for snapshot in record.snapshots)
    except (AttributeError, TypeError):
        return True


def _intent_matches_record(intent: object | None, record: object | None) -> bool:
    if intent is None or record is None:
        return True
    return getattr(intent, "operation_id", None) == getattr(record, "operation_id", None)


class RecoverySupervisor:
    """Poll durable state and invoke at most one audited automatic recovery at a time."""

    def __init__(
        self,
        config: AppConfig,
        *,
        poll_interval_seconds: float = 0.5,
        dependencies: RecoverySupervisorDependencies = DEFAULT_DEPENDENCIES,
    ) -> None:
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, int | float)
            or not math.isfinite(poll_interval_seconds)
            or not 0.1 <= poll_interval_seconds <= 60
        ):
            raise ValueError("poll interval must be between 0.1 and 60 seconds")
        self._config = config
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._dependencies = dependencies
        self._status = RecoverySupervisorStatus.STARTING
        self._stop_event = asyncio.Event()
        self._run_once_lock = asyncio.Lock()
        self._validated = False
        self._blocked_fingerprint: str | None = None
        self._inflight: asyncio.Task[int] | None = None

    @property
    def status(self) -> RecoverySupervisorStatus:
        return self._status

    @property
    def recovery_in_flight(self) -> bool:
        return self._inflight is not None and not self._inflight.done()

    def request_stop(self) -> None:
        """Request graceful shutdown without cancelling an in-flight exact recovery."""

        self._stop_event.set()

    async def run_once(self) -> RecoverySupervisorStatus:
        """Inspect one poll cycle and, when safe, execute one recovery-only dispatch."""

        async with self._run_once_lock:
            if not self._validated:
                try:
                    self._dependencies.validate_safety_root()
                except Exception:
                    self._set_status(RecoverySupervisorStatus.ERROR)
                    return self._status
                self._validated = True

            try:
                artifacts = self._dependencies.scan_artifacts()
                latch_active = self._dependencies.latch_present()
                now = self._dependencies.clock()
            except Exception:
                self._set_status(RecoverySupervisorStatus.ERROR)
                return self._status

            fingerprint = _artifact_fingerprint(artifacts)
            # Temporary schedule bytes must be restored before any outer control-state journal
            # can safely re-enable TimerON.  This supervisor intentionally has no authority to
            # guess that cross-journal order; the attended schedule recovery command owns it.
            if artifacts.temporary_schedule_journal is not None:
                self._blocked_fingerprint = None
                self._set_status(RecoverySupervisorStatus.ATTENDED_REQUIRED)
                return self._status
            native_pending = artifacts.native_journal is not None or _phase_needs_recovery(
                artifacts.native_intent
            )
            verification_pending = (
                artifacts.verification_journal is not None
                or _phase_needs_recovery(artifacts.verification_intent)
            )
            schedule_pending = artifacts.schedule_journal is not None or _phase_needs_recovery(
                artifacts.schedule_intent
            )
            native_nonterminal = artifacts.native_journal is not None or _phase_is_nonterminal(
                artifacts.native_intent
            )
            verification_nonterminal = (
                artifacts.verification_journal is not None
                or _phase_is_nonterminal(artifacts.verification_intent)
            )
            schedule_nonterminal = (
                artifacts.schedule_journal is not None
                or _phase_is_nonterminal(artifacts.schedule_intent)
            )

            if sum((native_nonterminal, verification_nonterminal, schedule_nonterminal)) > 1:
                self._blocked_fingerprint = None
                self._set_status(RecoverySupervisorStatus.ATTENDED_REQUIRED)
                return self._status
            if latch_active:
                self._blocked_fingerprint = None
                self._set_status(RecoverySupervisorStatus.ATTENDED_REQUIRED)
                return self._status
            if not native_pending and not verification_pending and not schedule_pending:
                self._blocked_fingerprint = None
                self._set_status(RecoverySupervisorStatus.IDLE)
                return self._status

            if native_pending:
                decision = self._native_candidate_status(artifacts, now)
                dispatcher = self._dependencies.native_dispatch
                args = argparse.Namespace(
                    command="recover-linkage",
                    confirm=None,
                    recovery_first=True,
                )
                recovering = RecoverySupervisorStatus.RECOVERING_NATIVE
            elif verification_pending:
                decision = self._verification_candidate_status(artifacts, now)
                dispatcher = self._dependencies.verification_dispatch
                args = argparse.Namespace(
                    command="recover-device-verification",
                    confirm=None,
                    recovery_first=True,
                )
                recovering = RecoverySupervisorStatus.RECOVERING_VERIFICATION
            else:
                decision = self._schedule_candidate_status(artifacts, now)
                dispatcher = self._dependencies.schedule_dispatch
                args = argparse.Namespace(
                    command="recover-schedule-linkage",
                    confirm=None,
                    recovery_first=True,
                )
                recovering = RecoverySupervisorStatus.RECOVERING_SCHEDULE

            if decision is not None:
                self._blocked_fingerprint = None
                self._set_status(decision)
                return self._status
            if self._blocked_fingerprint == fingerprint:
                # An unexpected failure is never retried as a physical recovery command storm.
                self._set_status(RecoverySupervisorStatus.ERROR)
                return self._status

            self._set_status(recovering)
            try:
                result = await self._dispatch_uninterruptibly(dispatcher, args)
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                if self._dependencies.is_busy(error):
                    self._blocked_fingerprint = None
                    self._set_status(RecoverySupervisorStatus.BUSY)
                else:
                    self._blocked_fingerprint = self._latest_artifact_fingerprint(
                        fallback=fingerprint
                    )
                    self._set_status(RecoverySupervisorStatus.ERROR)
                return self._status

            if result != 0:
                self._blocked_fingerprint = self._latest_artifact_fingerprint(
                    fallback=fingerprint
                )
                self._set_status(RecoverySupervisorStatus.ERROR)
                return self._status
            self._blocked_fingerprint = None
            self._set_status(RecoverySupervisorStatus.RECOVERED)
            return self._status

    def _latest_artifact_fingerprint(self, *, fallback: str) -> str:
        """Latch dispatcher-authored state so its own failure cannot trigger a retry storm."""

        try:
            return _artifact_fingerprint(self._dependencies.scan_artifacts())
        except Exception:
            return fallback

    async def run(self) -> RecoverySupervisorStatus:
        """Run until requested to stop, preserving every in-flight recovery to completion."""

        while not self._stop_event.is_set():
            await self.run_once()
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue
        self._set_status(RecoverySupervisorStatus.STOPPED)
        return self._status

    def _native_candidate_status(
        self,
        artifacts: RecoveryArtifacts,
        now: datetime,
    ) -> RecoverySupervisorStatus | None:
        intent = artifacts.native_intent
        record = artifacts.native_journal
        if intent is not None and getattr(intent, "instance_id", None) != self._config.instance.id:
            return RecoverySupervisorStatus.ERROR
        if not _intent_matches_record(intent, record):
            return RecoverySupervisorStatus.ERROR
        if getattr(intent, "version", None) == 3:
            # Version three owns nested role and byte-exact schedule recovery domains. The
            # generic native dispatcher cannot safely infer their inverse order, even if only
            # the outer journal remains visible at this instant.
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        if record is None:
            return (
                None
                if getattr(intent, "phase", None)
                in {
                    HardwareTestIntentPhase.STARTED,
                    HardwareTestIntentPhase.RECOVERY_REQUIRED,
                }
                else RecoverySupervisorStatus.ATTENDED_REQUIRED
            )
        if (
            _has_safety_recovery_reason(record)
            or record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED
        ):
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        if _native_timer_restore_requires_attendance(record):
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        if _timestamps_are_stale(record, now):
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        return None

    def _verification_candidate_status(
        self,
        artifacts: RecoveryArtifacts,
        now: datetime,
    ) -> RecoverySupervisorStatus | None:
        intent = artifacts.verification_intent
        record = artifacts.verification_journal
        if intent is not None and getattr(intent, "instance_id", None) != self._config.instance.id:
            return RecoverySupervisorStatus.ERROR
        if not _intent_matches_record(intent, record):
            return RecoverySupervisorStatus.ERROR
        if record is None:
            return (
                None
                if getattr(intent, "phase", None)
                in {
                    VerificationIntentPhase.STARTED,
                    VerificationIntentPhase.RECOVERY_REQUIRED,
                }
                else RecoverySupervisorStatus.ATTENDED_REQUIRED
            )
        if _has_safety_recovery_reason(record) or _timestamps_are_stale(record, now):
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        return None

    def _schedule_candidate_status(
        self,
        artifacts: RecoveryArtifacts,
        now: datetime,
    ) -> RecoverySupervisorStatus | None:
        intent = artifacts.schedule_intent
        record = artifacts.schedule_journal
        if intent is None:
            # A journal without its instance-bound intent cannot establish recovery authority.
            return RecoverySupervisorStatus.ERROR
        if getattr(intent, "instance_id", None) != self._config.instance.id:
            return RecoverySupervisorStatus.ERROR
        if record is None:
            # STARTED is durably persisted before journal creation.  With no journal it proves
            # that no role write was authorized, and the CLI can close the intent without I/O.
            return (
                None
                if getattr(intent, "phase", None) is ScheduleLinkageIntentPhase.STARTED
                else RecoverySupervisorStatus.ATTENDED_REQUIRED
            )
        expected_detached = tuple(
            reversed(getattr(record, "linkage_write_intent_device_ids", ()))
        )
        terminal_clear_crash = (
            getattr(intent, "phase", None) is ScheduleLinkageIntentPhase.TERMINAL
            and getattr(intent, "outcome", None)
            in {
                ScheduleLinkageIntentOutcome.ROLES_DETACHED,
                ScheduleLinkageIntentOutcome.BOUNDARY_VERIFIED,
                ScheduleLinkageIntentOutcome.RECOVERED,
            }
            and getattr(record, "detached_device_ids", None) == expected_detached
        )
        if (
            getattr(intent, "phase", None)
            not in {
                ScheduleLinkageIntentPhase.STARTED,
                ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
            }
            and not terminal_clear_crash
        ):
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        if getattr(record, "mutation_scope", None) != "linkage_only":
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        preflight = getattr(intent, "preflight", None)
        if (
            getattr(intent, "operation_id", None) != getattr(record, "operation_id", None)
            or getattr(preflight, "spec", None) != getattr(record, "spec", None)
            or getattr(preflight, "snapshots", None) != getattr(record, "snapshots", None)
        ):
            return RecoverySupervisorStatus.ERROR
        if _has_safety_like_error(record) or _timestamps_are_stale(record, now):
            return RecoverySupervisorStatus.ATTENDED_REQUIRED
        return None

    async def _dispatch_uninterruptibly(
        self,
        dispatcher: NativeDispatcher | VerificationDispatcher | ScheduleDispatcher,
        args: argparse.Namespace,
    ) -> int:
        task = asyncio.create_task(dispatcher(self._config, args))
        self._inflight = task
        cancellation_received = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancellation_received = True
                    self.request_stop()
            result = task.result()
        finally:
            self._inflight = None
        if cancellation_received:
            raise asyncio.CancelledError
        return result

    def _set_status(self, status: RecoverySupervisorStatus) -> None:
        if status is self._status:
            return
        self._status = status
        # Never attach an operation ID, device ID, endpoint, exception text, or artifact content.
        log = (
            _LOGGER.warning
            if status
            in {
                RecoverySupervisorStatus.ATTENDED_REQUIRED,
                RecoverySupervisorStatus.ERROR,
            }
            else _LOGGER.info
        )
        log("recovery_supervisor_status", extra={"supervisor_status": status.value})


async def run_once(
    config: AppConfig,
    *,
    dependencies: RecoverySupervisorDependencies = DEFAULT_DEPENDENCIES,
) -> RecoverySupervisorStatus:
    """Public one-cycle API for health checks and deterministic integration tests."""

    return await RecoverySupervisor(config, dependencies=dependencies).run_once()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-recovery")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


async def _run_with_signals(
    supervisor: RecoverySupervisor,
    *,
    once: bool,
) -> RecoverySupervisorStatus:
    if once:
        return await supervisor.run_once()

    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, supervisor.request_stop)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-POSIX fallback
            continue
        installed.append(event)
    try:
        return await supervisor.run()
    finally:
        for event in installed:
            loop.remove_signal_handler(event)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        config = load_config(args.config)
        supervisor = RecoverySupervisor(
            config,
            poll_interval_seconds=args.poll_interval,
        )
        status = asyncio.run(_run_with_signals(supervisor, once=args.once))
    except (OSError, RuntimeError, ValueError, ValidationError):
        print("recovery supervisor failed safely", file=sys.stderr)
        return 2
    print(f"Recovery supervisor status: {status.value}")
    return 2 if args.once and status is RecoverySupervisorStatus.ERROR else 0


__all__ = [
    "DEFAULT_DEPENDENCIES",
    "RecoveryArtifactError",
    "RecoveryArtifacts",
    "RecoveryDispatchBusyError",
    "RecoverySupervisor",
    "RecoverySupervisorDependencies",
    "RecoverySupervisorStatus",
    "build_parser",
    "main",
    "run_once",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
