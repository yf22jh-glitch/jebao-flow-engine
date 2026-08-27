"""Attended one-shot CLI for a TimerON, linkage-only schedule boundary diagnostic.

This harness deliberately has its own fixed intent and journal.  It can only assign native
``Linkage`` roles and delegates every physical write, including compensation, to
``ScheduleActiveLinkageController``.  It never writes power, mode, frequency, TimerON, SwitchON,
or schedule slots.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import signal
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jebao_flow.config import AppConfig, DeviceConfig, load_config
from jebao_flow.device_verification_cli import (
    DeviceVerificationIntent,
    VerificationIntentPhase,
    verification_confirmation_token,
)
from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.schedule_linkage import (
    ScheduleActiveLinkageController,
    ScheduleLinkageBusyError,
    ScheduleLinkageJournalClaimError,
    ScheduleLinkagePreflight,
    ScheduleLinkagePreflightError,
    ScheduleLinkageRecord,
    ScheduleLinkageSnapshot,
    ScheduleLinkageSpec,
    schedule_linkage_confirmation_token,
)
from jebao_flow.hardware_guard import DeploymentHardwareGuard
from jebao_flow.hardware_safety import (
    emergency_stop_latch_path,
    hardware_safety_root,
    native_linkage_intent_path,
    native_linkage_journal_path,
    qualification_directory,
    schedule_linkage_intent_path,
    schedule_linkage_journal_path,
    validate_hardware_safety_root,
    verification_intent_path,
    verification_journal_path,
)
from jebao_flow.hardware_test import (
    TERMINAL_SCHEDULE_FLOW_OUTCOMES,
    HardwareTestError,
    HardwareTestIntent,
    HardwareTestIntentPhase,
    PhysicalDeviceLease,
    _build_devices,
    _connected,
    _safety_latch_present,
    _validate_config,
    hardware_test_intent_confirmation_token,
)
from jebao_flow.logging import configure_logging
from jebao_flow.persistence.qualification import JsonQualificationStore
from jebao_flow.persistence.schedule_linkage import (
    JsonScheduleLinkageJournalStore,
    ScheduleLinkageJournalError,
)

_TOKEN_VERSION = 1
_AUTOMATIC_RECOVERY_GRACE_SECONDS = 30
_RECOVERY_ATTEMPTS = 3
_RECOVERY_RETRY_SECONDS = 1.0
_MAX_INTENT_BYTES = 1024 * 1024
_TERMINAL_NATIVE_OUTCOMES = frozenset(
    {
        "armed_preview_cancelled",
        "crashed_before_first_write",
        "experiment_failed_restored",
        "per_slot_power_verified",
        "slave_flow_fixed_at_previous",
        "slave_flow_followed_master",
        "unexpected_effective_state",
        "recovered",
        "restored",
        "stopped_before_first_write",
    }
)


class ScheduleLinkageCliError(RuntimeError):
    """Sanitized, fail-closed schedule diagnostic refusal."""


class ScheduleLinkageConfirmationError(ScheduleLinkageCliError):
    """An attended token does not authorize the exact current durable state."""


class ScheduleLinkageIntentPhase(StrEnum):
    ARMED = "armed"
    STARTED = "started"
    RECOVERY_REQUIRED = "recovery_required"
    TERMINAL = "terminal"


class ScheduleLinkageIntentOutcome(StrEnum):
    ROLES_DETACHED = "roles_detached"
    BOUNDARY_VERIFIED = "boundary_verified"
    RECOVERED = "recovered"
    CRASHED_BEFORE_FIRST_WRITE = "crashed_before_first_write"
    PREVIEW_CANCELLED = "preview_cancelled"
    REFUSED_BEFORE_FIRST_WRITE = "refused_before_first_write"


class ScheduleLinkageIntent(BaseModel):
    """Durable one-shot authority containing the exact read-only preflight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=1, ge=1, le=1)
    instance_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=128)
    phase: ScheduleLinkageIntentPhase
    confirmation_token: str = Field(pattern=r"^JFS-[0-9A-F]{20}$")
    preflight: ScheduleLinkagePreflight
    created_at: datetime
    updated_at: datetime
    outcome: ScheduleLinkageIntentOutcome | None = None

    @model_validator(mode="after")
    def validate_intent(self) -> Self:
        if self.operation_id != self.preflight.spec.operation_id:
            raise ValueError("intent operation must match its exact preflight")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
        ):
            raise ValueError("intent timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("intent update time cannot precede its creation")
        canonical = {
            "version": _TOKEN_VERSION,
            "instance_id": self.instance_id,
            "preflight": self.preflight.model_dump(mode="json"),
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        expected_token = f"JFS-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"
        expected_core_token = schedule_linkage_confirmation_token(
            self.preflight.spec,
            self.preflight.snapshots,
        )
        if not hmac.compare_digest(
            self.preflight.confirmation_token, expected_core_token
        ) or not hmac.compare_digest(self.confirmation_token, expected_token):
            raise ValueError("intent confirmation token does not match its exact preflight")
        if self.phase is ScheduleLinkageIntentPhase.TERMINAL:
            if self.outcome is None:
                raise ValueError("terminal schedule intent requires an outcome")
        elif self.outcome is not None:
            raise ValueError("nonterminal schedule intent cannot contain an outcome")
        return self


def _require_private_regular(metadata: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ScheduleLinkageCliError(f"{label} has unsafe metadata")


def _assert_direct_safety_child(path: Path, *, expected_name: str) -> None:
    if path.parent != hardware_safety_root() or path.name != expected_name:
        raise ScheduleLinkageCliError("schedule safety artifact path is not canonical")


def _open_private(path: Path, *, label: str, allow_absent: bool) -> int | None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise ScheduleLinkageCliError("O_NOFOLLOW is required for schedule safety files")
    try:
        initial = path.lstat()
    except FileNotFoundError:
        if allow_absent:
            return None
        raise ScheduleLinkageCliError(f"{label} disappeared") from None
    except OSError as error:
        raise ScheduleLinkageCliError(f"{label} metadata is unavailable") from error
    _require_private_regular(initial, label=label)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        _require_private_regular(opened, label=label)
        _require_private_regular(current, label=label)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ScheduleLinkageCliError(f"{label} changed while opening")
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class JsonScheduleLinkageIntentStore:
    """Atomic 0600 intent store with a nonblocking, no-follow one-shot lease."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def load(self) -> ScheduleLinkageIntent | None:
        descriptor = _open_private(
            self.path,
            label="schedule-linkage intent",
            allow_absent=True,
        )
        if descriptor is None:
            return None
        try:
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                payload = stream.read(_MAX_INTENT_BYTES + 1)
            if len(payload.encode()) > _MAX_INTENT_BYTES:
                raise ScheduleLinkageCliError("schedule-linkage intent is too large")
            return ScheduleLinkageIntent.model_validate_json(payload)
        except ScheduleLinkageCliError:
            raise
        except (OSError, ValidationError, ValueError) as error:
            raise ScheduleLinkageCliError("schedule-linkage intent is unreadable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def save(self, intent: ScheduleLinkageIntent) -> None:
        existing = _open_private(
            self.path,
            label="schedule-linkage intent",
            allow_absent=True,
        )
        if existing is not None:
            os.close(existing)
        temporary: Path | None = None
        try:
            payload = intent.model_dump_json(indent=2) + "\n"
            if len(payload.encode()) > _MAX_INTENT_BYTES:
                raise ScheduleLinkageCliError("schedule-linkage intent is too large")
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            _fsync_directory(self.path.parent)
        except OSError as error:
            raise ScheduleLinkageCliError("cannot persist schedule-linkage intent") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def lease(self) -> Iterator[None]:
        descriptor = -1
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            opened = os.fstat(descriptor)
            current = os.stat(self.lock_path, follow_symlinks=False)
            _require_private_regular(opened, label="schedule-linkage intent lease")
            _require_private_regular(current, label="schedule-linkage intent lease")
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ScheduleLinkageCliError("schedule-linkage intent lease changed")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ScheduleLinkageCliError(
                    "another schedule-linkage process is active"
                ) from error
            yield
        except ScheduleLinkageCliError:
            raise
        except OSError as error:
            raise ScheduleLinkageCliError("cannot lease schedule-linkage intent") from error
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)


def schedule_confirmation_token(instance_id: str, preflight: ScheduleLinkagePreflight) -> str:
    """Bind attended authorization to this instance and the complete absolute-boundary proof."""

    canonical = {
        "version": _TOKEN_VERSION,
        "instance_id": instance_id,
        "preflight": preflight.model_dump(mode="json"),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFS-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def schedule_recovery_token(
    instance_id: str,
    record: ScheduleLinkageRecord | None,
    intent: ScheduleLinkageIntent | None,
) -> str:
    canonical = {
        "version": _TOKEN_VERSION,
        "instance_id": instance_id,
        "intent": intent.model_dump(mode="json") if intent is not None else None,
        "record": record.model_dump(mode="json") if record is not None else None,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFSR-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _updated_intent(
    intent: ScheduleLinkageIntent,
    phase: ScheduleLinkageIntentPhase,
    outcome: ScheduleLinkageIntentOutcome | None,
    *,
    now: datetime | None = None,
) -> ScheduleLinkageIntent:
    requested = now or datetime.now(UTC)
    if requested.tzinfo is None or requested.utcoffset() is None:
        raise ScheduleLinkageCliError("schedule-linkage clock must be timezone-aware")
    clamped = max(requested, intent.created_at, intent.updated_at)
    return intent.model_copy(
        update={
            "phase": phase,
            "outcome": outcome,
            "updated_at": clamped,
        }
    )


def _intent_matches_record(
    intent: ScheduleLinkageIntent | None,
    record: ScheduleLinkageRecord | None,
) -> bool:
    return bool(
        intent is not None
        and record is not None
        and intent.operation_id == record.operation_id
        and intent.preflight.spec == record.spec
        and intent.preflight.snapshots == record.snapshots
    )


def _read_other_intent_phase(path: Path, *, label: str, workflow: str) -> str | None:
    descriptor = _open_private(path, label=label, allow_absent=True)
    if descriptor is None:
        return None
    try:
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            encoded = stream.read(_MAX_INTENT_BYTES + 1)
        if len(encoded.encode()) > _MAX_INTENT_BYTES:
            raise ScheduleLinkageCliError(f"{label} is too large")
        payload = json.loads(encoded)
    except ScheduleLinkageCliError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise ScheduleLinkageCliError(f"{label} is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        if workflow == "native":
            native = HardwareTestIntent.model_validate(payload)
            expected_token = hardware_test_intent_confirmation_token(native)
            valid = (
                native.phase is HardwareTestIntentPhase.TERMINAL
                and native.outcome
                in (
                    TERMINAL_SCHEDULE_FLOW_OUTCOMES
                    if native.version == 3
                    else _TERMINAL_NATIVE_OUTCOMES
                )
                and native.created_at.tzinfo is not None
                and native.created_at.utcoffset() is not None
                and native.updated_at.tzinfo is not None
                and native.updated_at.utcoffset() is not None
                and native.updated_at >= native.created_at
                and native.operation_id == native.spec.operation_id
                and hmac.compare_digest(native.confirmation_token, expected_token)
            )
        else:
            verification = DeviceVerificationIntent.model_validate(payload)
            expected_token = verification_confirmation_token(
                verification.instance_id,
                verification.device_id,
                verification.spec,
                verification.snapshot,
            )
            valid = (
                verification.phase is VerificationIntentPhase.TERMINAL
                and verification.created_at.tzinfo is not None
                and verification.created_at.utcoffset() is not None
                and verification.updated_at.tzinfo is not None
                and verification.updated_at.utcoffset() is not None
                and verification.updated_at >= verification.created_at
                and hmac.compare_digest(verification.confirmation_token, expected_token)
            )
    except (TypeError, ValidationError, ValueError) as error:
        raise ScheduleLinkageCliError(f"{label} is unreadable") from error
    if not valid:
        raise ScheduleLinkageCliError(f"{label} is nonterminal")
    return "terminal"


def _assert_no_other_workflow_conflict() -> None:
    for path, label in (
        (native_linkage_journal_path(), "native-linkage journal"),
        (verification_journal_path(), "device-verification journal"),
    ):
        if os.path.lexists(path):
            descriptor = _open_private(path, label=label, allow_absent=False)
            if descriptor is not None:
                os.close(descriptor)
            raise ScheduleLinkageCliError(
                "another unfinished hardware workflow blocks schedule-linkage"
            )
    for path, label, workflow in (
        (native_linkage_intent_path(), "native-linkage intent", "native"),
        (verification_intent_path(), "device-verification intent", "verification"),
    ):
        phase = _read_other_intent_phase(path, label=label, workflow=workflow)
        if phase is not None and phase != "terminal":
            raise ScheduleLinkageCliError(
                "another nonterminal hardware workflow blocks schedule-linkage"
            )


def _require_qualifications(
    store: JsonQualificationStore,
    qualification_operation_id: str,
    snapshots: Sequence[ScheduleLinkageSnapshot],
    *,
    now: datetime,
) -> None:
    for snapshot in snapshots:
        receipt = store.load(snapshot.physical_binding)
        if (
            receipt is None
            or receipt.device_id != snapshot.device_id
            or receipt.operation_id != qualification_operation_id
            or not receipt.is_valid_for(snapshot.physical_binding, now=now)
        ):
            raise ScheduleLinkageCliError(
                "both exact controllers require current receipts from the named qualification"
            )


class ConfirmingScheduleLinkageJournalStore:
    """Revalidate intent, receipt, conflicts, and durable successors at journal boundaries."""

    def __init__(
        self,
        delegate: JsonScheduleLinkageJournalStore,
        *,
        instance_id: str,
        expected_preflight: ScheduleLinkagePreflight,
        expected_token: str,
        qualification_store: JsonQualificationStore,
        before_clear: Callable[[], None],
        now: Callable[[], datetime],
        expected_loaded_record: ScheduleLinkageRecord | None = None,
        require_loaded_record_match: bool = False,
    ) -> None:
        self._delegate = delegate
        self._instance_id = instance_id
        self._expected_preflight = expected_preflight
        self._expected_token = expected_token
        self._qualification_store = qualification_store
        self._before_clear = before_clear
        self._now = now
        self._expected_loaded_record = expected_loaded_record
        self._require_loaded_record_match = require_loaded_record_match

    def _assert_record(self, actual: ScheduleLinkageRecord | None) -> None:
        if self._require_loaded_record_match and actual != self._expected_loaded_record:
            raise ScheduleLinkageConfirmationError(
                "schedule recovery journal changed after confirmation"
            )

    def load(self) -> ScheduleLinkageRecord | None:
        _assert_no_other_workflow_conflict()
        actual = self._delegate.load()
        self._assert_record(actual)
        return actual

    def lease(self):
        return self._delegate.lease()

    def create(self, record: ScheduleLinkageRecord) -> None:
        preflight = ScheduleLinkagePreflight(
            spec=record.spec,
            snapshots=record.snapshots,
            confirmation_token=schedule_linkage_confirmation_token(record.spec, record.snapshots),
        )
        actual_token = schedule_confirmation_token(self._instance_id, preflight)
        if (
            preflight != self._expected_preflight
            or not hmac.compare_digest(actual_token, self._expected_token)
        ):
            raise ScheduleLinkageConfirmationError(
                "fresh schedule evidence changed after attended confirmation"
            )
        _require_qualifications(
            self._qualification_store,
            record.spec.qualification_operation_id,
            record.snapshots,
            now=self._now(),
        )
        _assert_no_other_workflow_conflict()
        self._delegate.create(record)

    def save(self, record: ScheduleLinkageRecord) -> None:
        _assert_no_other_workflow_conflict()
        self._assert_record(self._delegate.load())
        try:
            self._delegate.save(record)
        except BaseException:
            if self._require_loaded_record_match and self._delegate.load() == record:
                self._expected_loaded_record = record
            raise
        if self._require_loaded_record_match:
            self._expected_loaded_record = record

    def confirms_lease_successor(self, record: ScheduleLinkageRecord) -> bool:
        _assert_no_other_workflow_conflict()
        confirmed = self._delegate.confirms_lease_successor(record)
        if confirmed and self._require_loaded_record_match:
            self._expected_loaded_record = record
        return confirmed

    def clear(self) -> None:
        _assert_no_other_workflow_conflict()
        self._assert_record(self._delegate.load())
        # This is deliberately before journal removal.  STARTED with no journal can therefore
        # only mean a crash before the core created its durable first-write record.
        self._before_clear()
        self._delegate.clear()
        if self._require_loaded_record_match:
            self._expected_loaded_record = None


BuildDevices = Callable[
    [AppConfig, Mapping[str, DeviceConfig]],
    Awaitable[dict[str, JebaoDevice]],
]


async def _default_readers(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
) -> dict[str, JebaoDevice]:
    return await _build_devices(config, selected, writable=False)


async def _default_writers(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
) -> dict[str, JebaoDevice]:
    return await _build_devices(config, selected, writable=True)


@dataclass(frozen=True, slots=True)
class ScheduleCliDependencies:
    validate_safety_root: Callable[[], None] = validate_hardware_safety_root
    build_readers: BuildDevices = _default_readers
    build_writers: BuildDevices = _default_writers
    guard_factory: Callable[[], DeploymentHardwareGuard] = DeploymentHardwareGuard
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


DEFAULT_DEPENDENCIES = ScheduleCliDependencies()


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--qualification-operation-id", required=True)
    parser.add_argument("--master", required=True)
    parser.add_argument("--slave", required=True)
    parser.add_argument("--observation-window", type=float, default=180)
    parser.add_argument("--verification-interval", type=float, default=1)
    parser.add_argument("--minimum-lead", type=float, default=45)
    parser.add_argument("--ambiguous-band", type=float, default=1)
    parser.add_argument("--post-boundary-stability", type=float, default=0)
    parser.add_argument("--maximum-clock-skew", type=float, default=2)
    parser.add_argument("--clock-advance-tolerance", type=float, default=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-schedule-test")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="arm an exact read-only schedule preview")
    _add_spec_arguments(preflight)
    run = commands.add_parser(
        "run-schedule-linkage",
        help="execute one armed TimerON linkage-only boundary diagnostic",
    )
    _add_spec_arguments(run)
    run.add_argument("--confirm", required=True)
    recover = commands.add_parser(
        "recover-schedule-linkage",
        help="preview/confirm role-only detach or perform eligible startup recovery",
    )
    mode = recover.add_mutually_exclusive_group()
    mode.add_argument("--confirm")
    mode.add_argument("--recovery-first", action="store_true")
    commands.add_parser("status", help="show sanitized schedule diagnostic state")
    return parser


def _spec_from_args(args: argparse.Namespace) -> ScheduleLinkageSpec:
    return ScheduleLinkageSpec(
        operation_id=args.operation_id,
        qualification_operation_id=args.qualification_operation_id,
        master_device_id=args.master,
        slave_device_id=args.slave,
        observation_window_seconds=args.observation_window,
        verification_interval_seconds=args.verification_interval,
        minimum_lead_seconds=args.minimum_lead,
        ambiguous_band_seconds=args.ambiguous_band,
        post_boundary_stability_seconds=getattr(args, "post_boundary_stability", 0),
        maximum_clock_skew_seconds=args.maximum_clock_skew,
        clock_advance_tolerance_seconds=args.clock_advance_tolerance,
    )


def _validate_paths() -> None:
    _assert_direct_safety_child(
        schedule_linkage_journal_path(), expected_name="schedule-linkage.json"
    )
    _assert_direct_safety_child(
        schedule_linkage_intent_path(), expected_name="schedule-linkage-intent.json"
    )


def _qualification_authorizer(
    store: JsonQualificationStore,
    clock: Callable[[], datetime],
) -> Callable[[ScheduleLinkageSpec, tuple[ScheduleLinkageSnapshot, ...]], None]:
    def authorize(
        spec: ScheduleLinkageSpec,
        snapshots: tuple[ScheduleLinkageSnapshot, ...],
    ) -> None:
        _require_qualifications(
            store,
            spec.qualification_operation_id,
            snapshots,
            now=clock(),
        )

    return authorize


async def _preflight(
    config: AppConfig,
    spec: ScheduleLinkageSpec,
    intent_store: JsonScheduleLinkageIntentStore,
    journal_store: JsonScheduleLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    dependencies: ScheduleCliDependencies,
) -> int:
    selected = _validate_config(
        config, frozenset({spec.master_device_id, spec.slave_device_id})
    )
    guard = dependencies.guard_factory()
    # Even this read-only observation owns the deployment and exact physical leases so its token
    # cannot be captured inside another workflow's journal-before-connect gap.
    with guard.lease(), PhysicalDeviceLease.from_selected(config, selected).acquire():
        guard.clear()
        if not guard.permitted or _safety_latch_present(emergency_stop_latch_path()):
            raise ScheduleLinkageCliError("persistent safety latch is active")
        _assert_no_other_workflow_conflict()
        if journal_store.load() is not None:
            raise ScheduleLinkageCliError("unfinished schedule-linkage recovery blocks preflight")
        existing = intent_store.load()
        if existing is not None:
            if existing.phase is not ScheduleLinkageIntentPhase.TERMINAL:
                raise ScheduleLinkageCliError("unfinished schedule-linkage intent exists")
            if existing.operation_id == spec.operation_id:
                raise ScheduleLinkageCliError("terminal operation IDs cannot be replayed")
        devices = await dependencies.build_readers(config, selected)
        controller = ScheduleActiveLinkageController(
            devices,
            journal_store,
            prerequisite_authorizer=_qualification_authorizer(
                qualification_store, dependencies.clock
            ),
            safety_interlock=guard,
        )
        async with _connected(devices):
            preflight = await controller.preflight(spec)
        if not guard.permitted:
            raise ScheduleLinkageCliError("persistent safety latch became active")
        token = schedule_confirmation_token(config.instance.id, preflight)
        now = dependencies.clock()
        intent_store.save(
            ScheduleLinkageIntent(
                instance_id=config.instance.id,
                operation_id=spec.operation_id,
                phase=ScheduleLinkageIntentPhase.ARMED,
                confirmation_token=token,
                preflight=preflight,
                created_at=now,
                updated_at=now,
            )
        )
    print("Schedule-linkage preflight passed; no control frame was sent.")
    print(f"Boundary window: {spec.observation_window_seconds:g}s")
    print(f"Confirmation token: {token}")
    return 0


async def _run_with_signals(
    controller: ScheduleActiveLinkageController,
    preflight: ScheduleLinkagePreflight,
    *,
    interrupt_event: asyncio.Event | None = None,
) -> Any:
    loop = asyncio.get_running_loop()
    stop_event = interrupt_event or asyncio.Event()
    installed: list[signal.Signals] = []

    def request_stop() -> None:
        stop_event.set()

    if interrupt_event is None:
        for event in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(event, request_stop)
            except (NotImplementedError, RuntimeError):  # pragma: no cover
                continue
            installed.append(event)

    run_task = asyncio.create_task(
        controller.run(preflight), name="schedule-linkage-boundary-test"
    )
    signal_task = asyncio.create_task(stop_event.wait(), name="schedule-linkage-stop")
    cancellation_received = False
    try:
        try:
            done, _ = await asyncio.wait(
                {run_task, signal_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            cancellation_received = True
            done = set()
        if (cancellation_received or signal_task in done) and not run_task.done():
            while not await controller.stop(preflight.spec.operation_id):
                if run_task.done():
                    break
                await asyncio.sleep(0)
        # The core shields strict slave-then-master detach.  Signals and cancellation of this
        # wrapper both request a normal stop and then preserve that task to completion.
        while not run_task.done():
            try:
                await asyncio.shield(run_task)
            except asyncio.CancelledError:
                cancellation_received = True
        result = run_task.result()
        if cancellation_received:
            raise asyncio.CancelledError
        return result
    finally:
        for event in installed:
            loop.remove_signal_handler(event)
        if not signal_task.done():
            signal_task.cancel()
        await asyncio.gather(signal_task, return_exceptions=True)


async def _run_schedule_linkage(
    config: AppConfig,
    spec: ScheduleLinkageSpec,
    confirmation: str,
    intent_store: JsonScheduleLinkageIntentStore,
    journal_store: JsonScheduleLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    dependencies: ScheduleCliDependencies,
) -> int:
    selected = _validate_config(
        config, frozenset({spec.master_device_id, spec.slave_device_id})
    )
    guard = dependencies.guard_factory()
    with guard.lease(), PhysicalDeviceLease.from_selected(config, selected).acquire():
        guard.clear()
        if not guard.permitted:
            raise ScheduleLinkageCliError("persistent safety latch is active")
        _assert_no_other_workflow_conflict()
        if journal_store.load() is not None:
            raise ScheduleLinkageCliError("unfinished schedule-linkage recovery exists")
        intent = intent_store.load()
        if intent is None or intent.phase is not ScheduleLinkageIntentPhase.ARMED:
            raise ScheduleLinkageCliError("run requires an armed schedule preflight")
        if (
            intent.instance_id != config.instance.id
            or intent.operation_id != spec.operation_id
            or intent.preflight.spec != spec
        ):
            raise ScheduleLinkageCliError("run arguments do not match the armed preflight")
        expected = schedule_confirmation_token(config.instance.id, intent.preflight)
        if not hmac.compare_digest(expected, intent.confirmation_token) or not hmac.compare_digest(
            confirmation, intent.confirmation_token
        ):
            raise ScheduleLinkageConfirmationError(
                "confirmation token does not match the armed schedule evidence"
            )
        _require_qualifications(
            qualification_store,
            spec.qualification_operation_id,
            intent.preflight.snapshots,
            now=dependencies.clock(),
        )

        # STARTED is durable before writable adapters can connect.  With no later journal this is
        # therefore provable no-write and recovery closes only the tombstone.
        started = _updated_intent(intent, ScheduleLinkageIntentPhase.STARTED, None)
        intent_store.save(started)
        devices = await dependencies.build_writers(config, selected)

        def terminal_before_clear() -> None:
            intent_store.save(
                _updated_intent(
                    started,
                    ScheduleLinkageIntentPhase.TERMINAL,
                    ScheduleLinkageIntentOutcome.ROLES_DETACHED,
                    now=dependencies.clock(),
                )
            )

        confirming_store = ConfirmingScheduleLinkageJournalStore(
            journal_store,
            instance_id=config.instance.id,
            expected_preflight=intent.preflight,
            expected_token=intent.confirmation_token,
            qualification_store=qualification_store,
            before_clear=terminal_before_clear,
            now=dependencies.clock,
        )
        controller = ScheduleActiveLinkageController(
            devices,
            confirming_store,
            prerequisite_authorizer=_qualification_authorizer(
                qualification_store, dependencies.clock
            ),
            safety_interlock=guard,
        )
        async with _connected(devices):
            try:
                result = await _run_with_signals(controller, intent.preflight)
            except BaseException:
                pending = journal_store.load()
                current = intent_store.load()
                if pending is not None:
                    intent_store.save(
                        _updated_intent(
                            started,
                            ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
                            None,
                            now=dependencies.clock(),
                        )
                    )
                elif current is None or current.phase is not ScheduleLinkageIntentPhase.TERMINAL:
                    intent_store.save(
                        _updated_intent(
                            started,
                            ScheduleLinkageIntentPhase.TERMINAL,
                            ScheduleLinkageIntentOutcome.REFUSED_BEFORE_FIRST_WRITE,
                            now=dependencies.clock(),
                        )
                    )
                raise
            finally:
                guard.trip()
        if journal_store.load() is not None:
            intent_store.save(
                _updated_intent(
                    started,
                    ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
                    None,
                    now=dependencies.clock(),
                )
            )
            raise ScheduleLinkageCliError("role-only recovery journal remains after the run")
        final_outcome = (
            ScheduleLinkageIntentOutcome.BOUNDARY_VERIFIED
            if result.schedule_transition_verified
            else ScheduleLinkageIntentOutcome.ROLES_DETACHED
        )
        intent_store.save(
            _updated_intent(
                started,
                ScheduleLinkageIntentPhase.TERMINAL,
                final_outcome,
                now=dependencies.clock(),
            )
        )
    print("Schedule boundary diagnostic completed; both native roles were detached.")
    print(
        "Controller-register transition evidence: "
        + ("verified" if result.schedule_transition_verified else "not verified")
    )
    return 0


def _fresh_for_automatic(record: ScheduleLinkageRecord, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        return False
    try:
        return (
            record.mutation_scope == "linkage_only"
            and record.created_at <= now
            and record.updated_at <= now
            and now
            <= record.expires_at
            + timedelta(seconds=_AUTOMATIC_RECOVERY_GRACE_SECONDS)
        )
    except (OverflowError, TypeError):
        return False


def _record_has_safety_error(record: ScheduleLinkageRecord) -> bool:
    value = (record.error or "").lower()
    return any(word in value for word in ("safety", "interlock", "emergency", "latch"))


def _is_terminal_clear_crash(
    intent: ScheduleLinkageIntent | None,
    record: ScheduleLinkageRecord,
) -> bool:
    expected_detached = tuple(reversed(record.linkage_write_intent_device_ids))
    return bool(
        intent is not None
        and intent.phase is ScheduleLinkageIntentPhase.TERMINAL
        and intent.outcome
        in {
            ScheduleLinkageIntentOutcome.ROLES_DETACHED,
            ScheduleLinkageIntentOutcome.BOUNDARY_VERIFIED,
            ScheduleLinkageIntentOutcome.RECOVERED,
        }
        and record.detached_device_ids == expected_detached
    )


def _record_preflight(record: ScheduleLinkageRecord) -> ScheduleLinkagePreflight:
    return ScheduleLinkagePreflight(
        spec=record.spec,
        snapshots=record.snapshots,
        confirmation_token=schedule_linkage_confirmation_token(record.spec, record.snapshots),
    )


async def _recover_once(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    store: ConfirmingScheduleLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    guard: DeploymentHardwareGuard,
    dependencies: ScheduleCliDependencies,
) -> bool:
    devices = await dependencies.build_writers(config, selected)
    controller = ScheduleActiveLinkageController(
        devices,
        store,
        prerequisite_authorizer=_qualification_authorizer(
            qualification_store, dependencies.clock
        ),
        safety_interlock=guard,
    )
    async with _connected(devices):
        guard.clear()
        if not guard.permitted:
            raise ScheduleLinkageCliError("persistent safety latch is active")
        try:
            return await controller.recover_pending()
        finally:
            guard.trip()


async def _recover_schedule_linkage(
    config: AppConfig,
    confirmation: str | None,
    recovery_first: bool,
    intent_store: JsonScheduleLinkageIntentStore,
    journal_store: JsonScheduleLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    dependencies: ScheduleCliDependencies,
) -> int:
    _assert_no_other_workflow_conflict()
    intent = intent_store.load()
    record = journal_store.load()
    if record is None:
        if intent is None or intent.phase is ScheduleLinkageIntentPhase.TERMINAL:
            if recovery_first:
                print("No unfinished schedule-linkage operation needs recovery.")
                return 0
            raise ScheduleLinkageCliError("there is no unfinished schedule-linkage operation")
        if intent.instance_id != config.instance.id:
            raise ScheduleLinkageCliError("schedule-linkage intent belongs to another instance")
        if intent.phase is ScheduleLinkageIntentPhase.STARTED:
            intent_store.save(
                _updated_intent(
                    intent,
                    ScheduleLinkageIntentPhase.TERMINAL,
                    ScheduleLinkageIntentOutcome.CRASHED_BEFORE_FIRST_WRITE,
                    now=dependencies.clock(),
                )
            )
            print("Interrupted schedule diagnostic was closed as proven no-write.")
            return 0
        if intent.phase is ScheduleLinkageIntentPhase.RECOVERY_REQUIRED:
            raise ScheduleLinkageCliError(
                "recovery-required intent has no role journal; refusing synthetic writes"
            )
        token = schedule_recovery_token(config.instance.id, None, intent)
        if confirmation is None:
            if recovery_first:
                raise ScheduleLinkageCliError("armed preview requires attended cancellation")
            print("Preview cancellation is fail-closed; no control frame was sent.")
            print(f"Recovery confirmation token: {token}")
            return 0
        if not hmac.compare_digest(confirmation, token):
            raise ScheduleLinkageConfirmationError("recovery token does not match")
        intent_store.save(
            _updated_intent(
                intent,
                ScheduleLinkageIntentPhase.TERMINAL,
                ScheduleLinkageIntentOutcome.PREVIEW_CANCELLED,
                now=dependencies.clock(),
            )
        )
        print("The armed schedule preview was closed; no control frame was sent.")
        return 0

    if record.mutation_scope != "linkage_only":
        raise ScheduleLinkageCliError("recovery journal is not role-only")
    exact_match = _intent_matches_record(intent, record)
    now = dependencies.clock()
    eligible_phase = bool(
        intent is not None
        and (
            intent.phase
            in {
                ScheduleLinkageIntentPhase.STARTED,
                ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
            }
            or _is_terminal_clear_crash(intent, record)
        )
    )
    automatically_eligible = (
        recovery_first
        and exact_match
        and intent is not None
        and intent.instance_id == config.instance.id
        and eligible_phase
        and _fresh_for_automatic(record, now)
        and not _record_has_safety_error(record)
    )
    token = schedule_recovery_token(config.instance.id, record, intent)
    if not automatically_eligible:
        if recovery_first:
            raise ScheduleLinkageCliError("schedule role recovery requires attended confirmation")
        if confirmation is None:
            print("Role-only recovery requires attended confirmation; no frame was sent.")
            print(f"Recovery confirmation token: {token}")
            return 0
        if not hmac.compare_digest(confirmation, token):
            raise ScheduleLinkageConfirmationError("recovery token does not match")

    selected = _validate_config(
        config,
        frozenset({record.spec.master_device_id, record.spec.slave_device_id}),
    )
    guard = dependencies.guard_factory()
    with guard.lease(), PhysicalDeviceLease.from_selected(config, selected).acquire():
        guard.clear()
        if not guard.permitted or _safety_latch_present(emergency_stop_latch_path()):
            raise ScheduleLinkageCliError("persistent safety latch is active")
        _assert_no_other_workflow_conflict()
        if journal_store.load() != record or intent_store.load() != intent:
            raise ScheduleLinkageConfirmationError(
                "schedule recovery state changed after confirmation"
            )
        preflight = _record_preflight(record)
        active_created_at = (
            intent.created_at
            if exact_match and intent is not None
            else record.created_at
        )
        active_now = dependencies.clock()
        if active_now.tzinfo is None or active_now.utcoffset() is None:
            raise ScheduleLinkageCliError("schedule-linkage clock must be timezone-aware")
        active_intent = ScheduleLinkageIntent(
            instance_id=config.instance.id,
            operation_id=record.operation_id,
            phase=ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
            confirmation_token=schedule_confirmation_token(config.instance.id, preflight),
            preflight=preflight,
            created_at=active_created_at,
            updated_at=max(active_now, active_created_at),
        )
        intent_store.save(active_intent)

        def terminal_before_clear() -> None:
            intent_store.save(
                _updated_intent(
                    active_intent,
                    ScheduleLinkageIntentPhase.TERMINAL,
                    ScheduleLinkageIntentOutcome.RECOVERED,
                    now=dependencies.clock(),
                )
            )

        recovery_store = ConfirmingScheduleLinkageJournalStore(
            journal_store,
            instance_id=config.instance.id,
            expected_preflight=preflight,
            expected_token=active_intent.confirmation_token,
            qualification_store=qualification_store,
            before_clear=terminal_before_clear,
            now=dependencies.clock,
            expected_loaded_record=record,
            require_loaded_record_match=True,
        )
        recovered = False
        for attempt in range(_RECOVERY_ATTEMPTS):
            if _safety_latch_present(emergency_stop_latch_path()):
                break
            try:
                recovered = await _recover_once(
                    config,
                    selected,
                    recovery_store,
                    qualification_store,
                    guard,
                    dependencies,
                )
            except (
                ScheduleLinkageBusyError,
                ScheduleLinkageCliError,
                ScheduleLinkageConfirmationError,
                ScheduleLinkageJournalClaimError,
                ScheduleLinkageJournalError,
                ScheduleLinkagePreflightError,
            ):
                raise
            except Exception:
                recovered = False
            if recovered and journal_store.load() is None:
                break
            if attempt + 1 < _RECOVERY_ATTEMPTS:
                await dependencies.sleep(_RECOVERY_RETRY_SECONDS)
        if not recovered or journal_store.load() is not None:
            intent_store.save(
                _updated_intent(
                    active_intent,
                    ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
                    None,
                    now=dependencies.clock(),
                )
            )
            raise ScheduleLinkageCliError(
                "role-only detach did not complete after bounded attempts"
            )
    intent_store.save(
        _updated_intent(
            active_intent,
            ScheduleLinkageIntentPhase.TERMINAL,
            ScheduleLinkageIntentOutcome.RECOVERED,
            now=dependencies.clock(),
        )
    )
    print("The unfinished schedule-linkage roles were detached and closed.")
    return 0


def _status(
    config: AppConfig,
    intent_store: JsonScheduleLinkageIntentStore,
    journal_store: JsonScheduleLinkageJournalStore,
) -> int:
    intent = intent_store.load()
    record = journal_store.load()
    conflict = False
    try:
        _assert_no_other_workflow_conflict()
    except ScheduleLinkageCliError:
        conflict = True
    latch = _safety_latch_present(emergency_stop_latch_path())
    print(f"Schedule intent: {intent.phase.value if intent is not None else 'none'}")
    print(f"Role-only journal: {record.phase.value if record is not None else 'none'}")
    print(f"Persistent safety latch: {'active' if latch else 'clear'}")
    print(f"Other workflow conflict: {'yes' if conflict else 'no'}")
    if record is not None or (
        intent is not None and intent.phase is not ScheduleLinkageIntentPhase.TERMINAL
    ):
        print(
            "Recovery confirmation token: "
            + schedule_recovery_token(config.instance.id, record, intent)
        )
    return 0


async def dispatch(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    dependencies: ScheduleCliDependencies = DEFAULT_DEPENDENCIES,
) -> int:
    dependencies.validate_safety_root()
    _validate_paths()
    intent_store = JsonScheduleLinkageIntentStore(schedule_linkage_intent_path())
    journal_store = JsonScheduleLinkageJournalStore(schedule_linkage_journal_path())
    qualification_store = JsonQualificationStore(qualification_directory())
    with intent_store.lease():
        if args.command == "status":
            return _status(config, intent_store, journal_store)
        _assert_no_other_workflow_conflict()
        if args.command == "preflight":
            return await _preflight(
                config,
                _spec_from_args(args),
                intent_store,
                journal_store,
                qualification_store,
                dependencies,
            )
        if args.command == "run-schedule-linkage":
            return await _run_schedule_linkage(
                config,
                _spec_from_args(args),
                args.confirm,
                intent_store,
                journal_store,
                qualification_store,
                dependencies,
            )
        if args.command == "recover-schedule-linkage":
            return await _recover_schedule_linkage(
                config,
                args.confirm,
                args.recovery_first,
                intent_store,
                journal_store,
                qualification_store,
                dependencies,
            )
    raise AssertionError("unhandled schedule-linkage command")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        config = load_config(args.config)
        return asyncio.run(dispatch(config, args))
    except (
        HardwareTestError,
        ScheduleLinkageCliError,
        ScheduleLinkageJournalError,
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
    ):
        print("schedule-linkage command failed safely", file=sys.stderr)
        return 2


__all__ = [
    "DEFAULT_DEPENDENCIES",
    "ConfirmingScheduleLinkageJournalStore",
    "JsonScheduleLinkageIntentStore",
    "ScheduleCliDependencies",
    "ScheduleLinkageCliError",
    "ScheduleLinkageConfirmationError",
    "ScheduleLinkageIntent",
    "ScheduleLinkageIntentOutcome",
    "ScheduleLinkageIntentPhase",
    "build_parser",
    "dispatch",
    "main",
    "schedule_confirmation_token",
    "schedule_recovery_token",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
