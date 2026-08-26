"""Fail-closed, one-shot hardware harness for native Jebao linkage tests.

This module is intentionally separate from the read-only ``jebao-flowctl`` command.  It is only
for a short, attended aquarium-side test after the normal daemon and every other controller have
been stopped.
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
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jebao_flow.config import AppConfig, DeviceConfig, DeviceType, RuntimeMode, load_config
from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.factory import create_lan_device, create_read_only_lan_device
from jebao_flow.devices.identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    physical_identity_key,
)
from jebao_flow.devices.linkage import (
    DeviceControlSnapshot,
    LinkageJournalClaimError,
    LinkageRecoveryAuthority,
    LinkageRecoveryReason,
    LinkageSafetyInterlock,
    LinkageTestSpec,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    TemporaryLinkageController,
)
from jebao_flow.devices.observer import ResolvedDevice, resolve_device_bindings
from jebao_flow.hardware_guard import DeploymentHardwareGuard
from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    emergency_stop_latch_path,
    native_linkage_intent_path,
    native_linkage_journal_path,
    physical_lock_directory,
    qualification_directory,
    validate_hardware_safety_root,
    verification_intent_path,
    verification_journal_path,
)
from jebao_flow.logging import configure_logging
from jebao_flow.persistence import (
    DeviceQualificationReceipt,
    JsonLinkageJournalStore,
    JsonQualificationStore,
    LinkageJournalError,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO

_TOKEN_VERSION = 1
_MAX_ATTENDED_POWER = 45
_MAX_ATTENDED_DURATION_SECONDS = 10
_MAX_SCHEDULE_BOOTSTRAP_DURATION_SECONDS = 180
_MAX_ATTENDED_COMMAND_INTERVAL_MS = 2000
_MAX_ATTENDED_READBACK_DELAY_MS = 1000
_MAX_ATTENDED_READBACK_ATTEMPTS = 3
_MAX_ATTENDED_DISCOVERY_TIMEOUT_SECONDS = 5
_MAX_AUTOMATIC_RECOVERY_GRACE_SECONDS = 30
_RECOVERY_ATTEMPTS = 3
_RECOVERY_RETRY_SECONDS = 1.0
_LATE_EMERGENCY_STOP_TIMEOUT_SECONDS = 35.0
_MAX_SAFETY_ARTIFACT_BYTES = 1024 * 1024
_AUDITED_SNAPSHOT_MODES = frozenset({"constant", "pulse", "sine"})


class HardwareTestError(RuntimeError):
    """A fail-closed harness validation or lifecycle error."""


class ConfirmationMismatchError(HardwareTestError):
    """The confirmed preview is no longer identical to the controller's fresh snapshot."""


def _require_private_regular_metadata(metadata: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise HardwareTestError(f"{label} has unsafe metadata")


def _validate_open_private_file(descriptor: int, path: Path, *, label: str) -> None:
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise HardwareTestError(f"{label} changed while opening") from error
    _require_private_regular_metadata(opened, label=label)
    _require_private_regular_metadata(current, label=label)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise HardwareTestError(f"{label} changed while opening")


def _open_existing_private_file(
    path: Path,
    *,
    label: str,
    allow_absent: bool,
) -> int | None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise HardwareTestError("O_NOFOLLOW is required for hardware safety files")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_absent:
            return None
        raise HardwareTestError(f"{label} disappeared") from None
    except OSError as error:
        raise HardwareTestError(f"{label} metadata is unavailable") from error
    _require_private_regular_metadata(metadata, label=label)

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        _validate_open_private_file(descriptor, path, label=label)
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


class HardwareTestIntentPhase(StrEnum):
    ARMED = "armed"
    STARTED = "started"
    RECOVERY_REQUIRED = "recovery_required"
    TERMINAL = "terminal"


class HardwareTestIntent(BaseModel):
    """Durable one-shot intent that prevents a service restart from replaying a test."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=1, ge=1, le=1)
    instance_id: str
    operation_id: str
    phase: HardwareTestIntentPhase
    confirmation_token: str
    spec: LinkageTestSpec
    snapshots: tuple[DeviceControlSnapshot, ...] = Field(min_length=2, max_length=2)
    created_at: datetime
    updated_at: datetime
    outcome: str | None = None


class JsonHardwareTestIntentStore:
    """Small atomic JSON store with a process-wide one-shot lease."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def load(self) -> HardwareTestIntent | None:
        descriptor = _open_existing_private_file(
            self.path,
            label="hardware-test intent",
            allow_absent=True,
        )
        if descriptor is None:
            return None
        try:
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                payload = stream.read(_MAX_SAFETY_ARTIFACT_BYTES + 1)
            if len(payload.encode()) > _MAX_SAFETY_ARTIFACT_BYTES:
                raise HardwareTestError("the hardware-test intent is too large")
            return HardwareTestIntent.model_validate_json(payload)
        except HardwareTestError:
            raise
        except (OSError, ValidationError, ValueError) as error:
            raise HardwareTestError(
                "the hardware-test intent is unreadable; refusing to proceed"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def save(self, intent: HardwareTestIntent) -> None:
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existing = _open_existing_private_file(
                self.path,
                label="hardware-test intent",
                allow_absent=True,
            )
            if existing is not None:
                os.close(existing)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(intent.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.path)
            self._fsync_parent()
        except OSError as error:
            raise HardwareTestError("cannot persist the hardware-test one-shot intent") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @contextmanager
    def lease(self) -> Iterator[None]:
        descriptor = -1
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not hasattr(os, "O_NOFOLLOW"):
                raise HardwareTestError("O_NOFOLLOW is required for hardware safety files")
            flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
            descriptor = os.open(self.lock_path, flags, 0o600)
            _validate_open_private_file(
                descriptor,
                self.lock_path,
                label="hardware-test one-shot lease",
            )
        except HardwareTestError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise HardwareTestError("cannot open the hardware-test one-shot lease") from error
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise HardwareTestError(
                    "another hardware-test process is already running"
                ) from error
            _validate_open_private_file(
                descriptor,
                self.lock_path,
                label="hardware-test one-shot lease",
            )
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _fsync_parent(self) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class PhysicalDeviceLease:
    """Cross-instance, privacy-preserving lease over the exact two physical pumps."""

    def __init__(self, directory: Path, lock_keys: Sequence[str]) -> None:
        self.directory = directory
        self._lock_keys = tuple(sorted(lock_keys))

    @classmethod
    def from_selected(
        cls,
        config: AppConfig,
        selected: Mapping[str, DeviceConfig],
    ) -> PhysicalDeviceLease:
        lock_keys = tuple(_physical_lock_key(device) for device in selected.values())
        if len(lock_keys) != 2 or len(set(lock_keys)) != 2:
            raise HardwareTestError("selected stable physical identities must be distinct")
        return cls(canonical_hardware_lock_directory(config), lock_keys)

    @contextmanager
    def acquire(self) -> Iterator[None]:
        descriptors: list[int] = []
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata = self.directory.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or self.directory.is_symlink()
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise HardwareTestError("physical-device safety lease directory is unsafe")
        except OSError as error:
            raise HardwareTestError("cannot open the physical-device safety lease") from error
        try:
            for lock_key in self._lock_keys:
                path = self.directory / f"{lock_key}.lock"
                descriptor = -1
                try:
                    if not hasattr(os, "O_NOFOLLOW"):
                        raise HardwareTestError(
                            "O_NOFOLLOW is required for hardware safety files"
                        )
                    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
                    descriptor = os.open(path, flags, 0o600)
                    _validate_open_private_file(
                        descriptor,
                        path,
                        label="physical-device safety lease",
                    )
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    _validate_open_private_file(
                        descriptor,
                        path,
                        label="physical-device safety lease",
                    )
                except HardwareTestError:
                    if descriptor >= 0:
                        os.close(descriptor)
                    raise
                except BlockingIOError as error:
                    os.close(descriptor)
                    raise HardwareTestError(
                        "a selected physical device is owned by another hardware test"
                    ) from error
                except OSError as error:
                    if descriptor >= 0:
                        os.close(descriptor)
                    raise HardwareTestError(
                        "cannot open the physical-device safety lease"
                    ) from error
                descriptors.append(descriptor)
            yield
        finally:
            for descriptor in reversed(descriptors):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


class PersistentSafetyInterlock(DeploymentHardwareGuard):
    """In-memory core interlock additionally gated by a canonical persistent e-stop marker."""

    def __init__(self, latch_path: Path) -> None:
        super().__init__(latch_path=latch_path)


class ConfirmingLinkageJournalStore:
    """Reject a fresh controller snapshot unless it exactly matches the armed preview token."""

    def __init__(
        self,
        delegate: JsonLinkageJournalStore,
        *,
        instance_id: str,
        expected_token: str,
        qualification_store: JsonQualificationStore | None = None,
        before_clear: Callable[[], None] | None = None,
        before_load: Callable[[], None] = lambda: None,
        expected_loaded_record: LinkageTransactionRecord | None = None,
        require_loaded_record_match: bool = False,
    ) -> None:
        self._delegate = delegate
        self._instance_id = instance_id
        self._expected_token = expected_token
        self._qualification_store = qualification_store
        self._before_clear = before_clear
        self._before_load = before_load
        self._expected_loaded_record = expected_loaded_record
        self._require_loaded_record_match = require_loaded_record_match
        self.created_record: LinkageTransactionRecord | None = None

    def _assert_expected_record_unchanged(self) -> None:
        if (
            self._require_loaded_record_match
            and self._delegate.load() != self._expected_loaded_record
        ):
            raise ConfirmationMismatchError(
                "recovery journal changed after confirmation; no restore frame was sent"
            )

    def load(self) -> LinkageTransactionRecord | None:
        self._before_load()
        record = self._delegate.load()
        if self._require_loaded_record_match and record != self._expected_loaded_record:
            raise ConfirmationMismatchError(
                "recovery journal changed after confirmation; no restore frame was sent"
            )
        return record

    def lease(self):
        return self._delegate.lease()

    def create(self, record: LinkageTransactionRecord) -> None:
        actual = preview_confirmation_token(
            self._instance_id,
            record.spec,
            record.snapshots,
        )
        if not hmac.compare_digest(actual, self._expected_token):
            raise ConfirmationMismatchError(
                "device state changed after preflight; no control frame was sent"
            )
        if self._qualification_store is not None:
            _require_current_qualifications(self._qualification_store, record.snapshots)
        _assert_no_verification_conflict()
        self._delegate.create(record)
        self.created_record = record

    def save(self, record: LinkageTransactionRecord) -> None:
        # An attended recovery may need several bounded attempts. Accept only the exact
        # successor durably written through this wrapper; a journal changed by any other
        # writer still fails the next comparison against that successor.
        self._before_load()
        self._assert_expected_record_unchanged()
        try:
            self._delegate.save(record)
        except BaseException:
            # Atomic replace can complete before a later fsync/error is reported. Track the
            # successor only when the durable file is already byte-semantically that record,
            # then preserve the original failure for the bounded retry loop.
            if self._require_loaded_record_match and self._delegate.load() == record:
                self._expected_loaded_record = record
            raise
        if self._require_loaded_record_match:
            self._expected_loaded_record = record

    def clear(self) -> None:
        self._before_load()
        self._assert_expected_record_unchanged()
        if self._before_clear is not None:
            # Persist terminal intent before removing the only proof that writes happened.  A
            # STARTED intent without a journal then unambiguously means a pre-first-write crash.
            self._before_clear()
        self._delegate.clear()
        if self._require_loaded_record_match:
            self._expected_loaded_record = None


def canonical_journal_path(config: AppConfig) -> Path:
    del config
    return native_linkage_journal_path()


def canonical_intent_path(config: AppConfig) -> Path:
    del config
    return native_linkage_intent_path()


def canonical_safety_latch_path(config: AppConfig) -> Path:
    del config
    return emergency_stop_latch_path()


def canonical_hardware_lock_directory(config: AppConfig) -> Path:
    del config
    return physical_lock_directory()


def canonical_qualification_directory(config: AppConfig) -> Path:
    del config
    return qualification_directory()


def _require_current_qualifications(
    store: JsonQualificationStore,
    snapshots: Sequence[DeviceControlSnapshot],
) -> None:
    now = datetime.now(UTC)
    for snapshot in snapshots:
        receipt = store.load(snapshot.physical_binding)
        if receipt is None or not receipt.is_valid_for(snapshot.physical_binding, now=now):
            raise HardwareTestError(
                "both selected controllers require a current single-device qualification"
            )


def _assert_no_verification_conflict() -> None:
    journal_path = verification_journal_path()
    if os.path.lexists(journal_path):
        if journal_path.is_symlink():
            raise HardwareTestError("device-verification recovery state is unsafe")
        raise HardwareTestError(
            "unfinished device verification exists; recover it before native linkage"
        )

    intent_path = verification_intent_path()
    if not os.path.lexists(intent_path):
        return
    if intent_path.is_symlink():
        raise HardwareTestError("device-verification intent is unsafe")
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(intent_path, flags)
        metadata = os.fstat(descriptor)
        current = os.stat(intent_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or metadata.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or metadata.st_nlink != 1
            or current.st_nlink != 1
            or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise HardwareTestError("device-verification intent has unsafe metadata")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            payload = json.load(stream)
    except HardwareTestError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise HardwareTestError("device-verification intent is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    required_fields = {
        "version",
        "instance_id",
        "operation_id",
        "device_id",
        "phase",
        "confirmation_token",
        "spec",
        "snapshot",
        "created_at",
        "updated_at",
        "outcome",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required_fields
        or payload.get("version") != 1
        or payload.get("phase") != "terminal"
        or not isinstance(payload.get("outcome"), str)
        or not isinstance(payload.get("confirmation_token"), str)
        or not payload["confirmation_token"].startswith("JFV-")
    ):
        raise HardwareTestError(
            "nonterminal device verification exists; close it before native linkage"
        )


def _physical_lock_key(config: DeviceConfig) -> str:
    identity = config.identity
    if identity is None or identity.device_id is None or identity.mac_address is None:
        raise HardwareTestError("selected devices require vendor ID and MAC identity selectors")
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=identity.device_id,
        mac_address=identity.mac_address,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        config_fingerprint=configuration_fingerprint({"scope": "native-linkage-hardware-lock-v1"}),
    )
    return physical_identity_key(binding)


def _safety_latch_present(path: Path) -> bool:
    # lexists is fail-closed for a broken symlink as well as a regular marker file.
    return os.path.lexists(path)


def activate_persistent_safety_latch(path: Path) -> None:
    """Atomically persist an attended e-stop marker; never clears an existing latch."""

    descriptor = -1
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, b"emergency_stop\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        parent_descriptor = os.open(path.parent, flags)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except OSError as error:
        raise HardwareTestError("cannot persist the emergency-stop safety latch") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def preview_confirmation_token(
    instance_id: str,
    spec: LinkageTestSpec,
    snapshots: Sequence[DeviceControlSnapshot],
) -> str:
    canonical = {
        "version": _TOKEN_VERSION,
        "instance_id": instance_id,
        "spec": spec.model_dump(mode="json"),
        "snapshots": [
            snapshot.model_dump(mode="json")
            for snapshot in sorted(snapshots, key=lambda value: value.device_id)
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFL-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def recovery_confirmation_token(
    instance_id: str,
    spec: LinkageTestSpec,
    snapshots: Sequence[DeviceControlSnapshot],
    revision: HardwareTestIntent | LinkageTransactionRecord,
) -> str:
    preview = preview_confirmation_token(instance_id, spec, snapshots)
    if isinstance(revision, LinkageTransactionRecord):
        revision_data = {
            "kind": "journal",
            "version": revision.version,
            "operation_id": revision.operation_id,
            "phase": revision.phase.value,
            "recovery_reason": (
                revision.recovery_reason.value if revision.recovery_reason is not None else None
            ),
            "error": revision.error,
            "created_at": revision.created_at.isoformat(),
            "updated_at": revision.updated_at.isoformat(),
            "expires_at": revision.expires_at.isoformat(),
            "failed_device_ids": list(revision.failed_device_ids),
            "restored_device_ids": list(revision.restored_device_ids),
        }
    else:
        revision_data = {
            "kind": "intent",
            "version": revision.version,
            "operation_id": revision.operation_id,
            "phase": revision.phase.value,
            "created_at": revision.created_at.isoformat(),
            "updated_at": revision.updated_at.isoformat(),
            "outcome": revision.outcome,
        }
    canonical = json.dumps(revision_data, sort_keys=True, separators=(",", ":"))
    encoded = f"recover:{preview}:{canonical}".encode()
    return f"JFR-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--master", required=True, help="configured logical master name")
    parser.add_argument("--slave", required=True, help="configured logical slave name")
    parser.add_argument(
        "--slave-role",
        required=True,
        choices=(LinkageRole.SYNC_SLAVE.value, LinkageRole.ASYNC_SLAVE.value),
    )
    parser.add_argument("--mode", required=True, choices=("constant", "pulse", "sine"))
    parser.add_argument("--master-power", required=True, type=int)
    parser.add_argument("--slave-power", required=True, type=int)
    parser.add_argument("--frequency", required=True, type=int)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--verification-interval", type=float, default=1)
    parser.add_argument(
        "--bootstrap-active-schedule",
        action="store_true",
        help="journal, pause, qualify and restore an already-active local schedule",
    )
    parser.add_argument(
        "--slave-power-after",
        type=int,
        help="change only the active async slave to this power during monitoring",
    )
    parser.add_argument(
        "--power-change-after",
        type=float,
        help="seconds after ACTIVE before applying --slave-power-after",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-hwtest")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="read and arm an exact no-write preview")
    _add_spec_arguments(preflight)

    run = subparsers.add_parser(
        "run-native-linkage",
        help="execute one previously armed and confirmed native-linkage test",
    )
    _add_spec_arguments(run)
    run.add_argument("--confirm", required=True, help="token printed by preflight")

    recover = subparsers.add_parser(
        "recover-linkage",
        help="preview or confirm exact recovery of an unfinished one-shot test",
    )
    recovery_mode = recover.add_mutually_exclusive_group()
    recovery_mode.add_argument("--confirm", help="recovery token printed without this option")
    recovery_mode.add_argument(
        "--recovery-first",
        action="store_true",
        help="startup-safe automatic recovery when no persistent safety latch is active",
    )
    subparsers.add_parser("status", help="show sanitized one-shot and recovery state")
    return parser


def _spec_from_args(args: argparse.Namespace) -> LinkageTestSpec:
    spec = LinkageTestSpec(
        operation_id=args.operation_id,
        master_device_id=args.master,
        slave_device_id=args.slave,
        slave_role=LinkageRole(args.slave_role),
        mode=args.mode,
        master_power=args.master_power,
        slave_power=args.slave_power,
        frequency=args.frequency,
        duration_seconds=args.duration,
        verification_interval_seconds=args.verification_interval,
        bootstrap_active_schedule=args.bootstrap_active_schedule,
        slave_power_after=args.slave_power_after,
        power_change_after_seconds=args.power_change_after,
    )
    requested_powers = [spec.master_power, spec.slave_power]
    if spec.slave_power_after is not None:
        requested_powers.append(spec.slave_power_after)
    if max(requested_powers) > _MAX_ATTENDED_POWER:
        raise HardwareTestError(f"attended linkage targets are capped at {_MAX_ATTENDED_POWER}%")
    duration_cap = (
        _MAX_SCHEDULE_BOOTSTRAP_DURATION_SECONDS
        if spec.bootstrap_active_schedule
        else _MAX_ATTENDED_DURATION_SECONDS
    )
    if spec.duration_seconds > duration_cap:
        raise HardwareTestError(
            f"attended linkage tests are capped at {duration_cap} seconds"
        )
    return spec


def _validate_config(
    config: AppConfig,
    selected_ids: frozenset[str],
) -> dict[str, DeviceConfig]:
    if config.runtime.mode is not RuntimeMode.CONTROL:
        raise HardwareTestError("hardware test requires runtime.mode=control")
    if config.runtime.dry_run:
        raise HardwareTestError("hardware test requires runtime.dry_run=false")
    if config.observer.enabled:
        raise HardwareTestError("hardware test requires observer.enabled=false")
    if len(selected_ids) != 2:
        raise HardwareTestError("hardware test requires exactly two distinct devices")

    by_id = {device.id: device for device in config.devices}
    if not selected_ids.issubset(by_id):
        raise HardwareTestError("selected devices are not present in the private configuration")
    write_enabled = {device.id for device in config.devices if device.control.allow_hardware_writes}
    if write_enabled != selected_ids:
        raise HardwareTestError(
            "hardware writes must be enabled for exactly the selected two devices"
        )

    selected = {device_id: by_id[device_id] for device_id in selected_ids}
    for device in selected.values():
        if not device.enabled or device.type is not DeviceType.WAVEMAKER:
            raise HardwareTestError("selected devices must be enabled wavemakers")
        if (
            device.identity is None
            or device.identity.device_id is None
            or device.identity.mac_address is None
        ):
            raise HardwareTestError(
                "selected devices require both vendor ID and MAC identity selectors"
            )
        if device.product_key is not None and device.product_key != LOCAL_WAVEMAKER_PRO.product_key:
            raise HardwareTestError("selected devices must be Local Wavemaker Pro controllers")
        control = device.control
        if control.minimum_command_interval_ms > _MAX_ATTENDED_COMMAND_INTERVAL_MS:
            raise HardwareTestError("hardware-test command interval exceeds the audited maximum")
        if control.readback_delay_ms > _MAX_ATTENDED_READBACK_DELAY_MS:
            raise HardwareTestError("hardware-test read-back delay exceeds the audited maximum")
        if control.readback_attempts > _MAX_ATTENDED_READBACK_ATTEMPTS:
            raise HardwareTestError("hardware-test read-back attempts exceed the audited maximum")
    if config.observer.discovery_timeout_seconds > _MAX_ATTENDED_DISCOVERY_TIMEOUT_SECONDS:
        raise HardwareTestError("hardware-test discovery timeout exceeds the audited maximum")
    if not config.runtime.state_path.is_absolute():
        raise HardwareTestError("runtime.state_path must be an absolute persistent path")
    return selected


async def _resolve_selected(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
) -> dict[str, ResolvedDevice]:
    discovery = GizwitsDiscovery(
        targets=config.observer.targets,
        bind_address=config.observer.bind_address,
    )
    try:
        discovered = await discovery.discover(
            timeout_seconds=config.observer.discovery_timeout_seconds
        )
    except Exception as error:
        raise HardwareTestError("stable-identity discovery failed") from error
    resolved = resolve_device_bindings(tuple(selected.values()), discovered)
    if set(resolved) != set(selected):
        raise HardwareTestError("the selected stable identities did not resolve uniquely")
    if any(
        endpoint.product_key != LOCAL_WAVEMAKER_PRO.product_key for endpoint in resolved.values()
    ):
        raise HardwareTestError("both selected devices must resolve as Local Wavemaker Pro")
    if len({endpoint.address for endpoint in resolved.values()}) != 2:
        raise HardwareTestError("the selected identities resolved to the same endpoint")
    return resolved


async def _build_devices(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    *,
    writable: bool,
) -> dict[str, JebaoDevice]:
    resolved = await _resolve_selected(config, selected)
    devices: dict[str, JebaoDevice] = {}
    for device_id, device_config in selected.items():
        endpoint = resolved[device_id]
        if writable:
            resolved_values = device_config.model_dump(mode="python")
            resolved_values.update(
                {
                    "address": endpoint.address,
                    "product_key": endpoint.product_key,
                    "discovery": None,
                }
            )
            # Re-validate the resolved location instead of bypassing DeviceConfig validators via
            # model_copy(update=...).  This is the final config object allowed to create a writer.
            resolved_config = DeviceConfig.model_validate(resolved_values)
            devices[device_id] = create_lan_device(resolved_config, config.runtime)
        else:
            devices[device_id] = create_read_only_lan_device(
                device_config,
                endpoint.address,
                endpoint.product_key,
            )
    return devices


@asynccontextmanager
async def _connected(devices: Mapping[str, JebaoDevice]):
    connected: list[JebaoDevice] = []
    try:
        for device in devices.values():
            await device.connect()
            connected.append(device)
        yield
    finally:
        for device in reversed(connected):
            try:
                await device.disconnect()
            except Exception:
                pass


def _safe_power(device: JebaoDevice) -> int:
    capabilities = device.capabilities
    minimum = capabilities.power_limits.min_power
    step = capabilities.power_step
    safe = ((minimum + step - 1) // step) * step
    if safe > min(capabilities.power_limits.max_power, _MAX_ATTENDED_POWER):
        raise HardwareTestError("a selected device has no safe attended-test power")
    return safe


async def _capture_preview(
    devices: Mapping[str, JebaoDevice],
    spec: LinkageTestSpec,
) -> tuple[DeviceControlSnapshot, ...]:
    roles = {
        spec.master_device_id: LinkageRole.MASTER,
        spec.slave_device_id: spec.slave_role,
    }
    powers = {
        spec.master_device_id: spec.master_power,
        spec.slave_device_id: spec.slave_power,
    }
    snapshots: list[DeviceControlSnapshot] = []
    for device_id in (spec.master_device_id, spec.slave_device_id):
        device = devices[device_id]
        state = await device.get_state()
        if not state.online or state.error:
            raise HardwareTestError("both selected devices must be online and error-free")
        if spec.bootstrap_active_schedule and state.timer_enabled is not True:
            raise HardwareTestError(
                "schedule bootstrap requires TimerON with a decoded active schedule"
            )
        if not spec.bootstrap_active_schedule and state.timer_enabled is not False:
            raise HardwareTestError(
                "disable TimerON in the vendor app before attended hardware testing"
            )
        physical_binding = device.physical_binding
        if physical_binding is None:
            raise HardwareTestError("a selected device has no exact stable physical binding")
        snapshot = DeviceControlSnapshot.from_state(
            device_id,
            state,
            physical_binding=physical_binding,
        )
        if not spec.bootstrap_active_schedule and snapshot.mode not in _AUDITED_SNAPSHOT_MODES:
            raise HardwareTestError("current mode is outside the audited exact-restore modes")
        if not spec.bootstrap_active_schedule and snapshot.power > _MAX_ATTENDED_POWER:
            raise HardwareTestError(
                f"current outputs must be at or below {_MAX_ATTENDED_POWER}% before preflight"
            )
        snapshots.append(snapshot)

        preview_target = getattr(device, "preview_target", None)
        if callable(preview_target):
            if spec.bootstrap_active_schedule:
                qualification, stepped = (
                    TemporaryLinkageController._bootstrap_qualification_levels(device)
                )
                qualification_target = DeviceTarget(
                    enabled=True,
                    power=qualification,
                    mode="constant",
                    frequency=spec.frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                )
                preview_target(qualification_target)
                preview_target(qualification_target.model_copy(update={"power": stepped}))
                preview_target(qualification_target)
            else:
                preview_target(
                    DeviceTarget(
                        enabled=True,
                        power=_safe_power(device),
                        mode="constant",
                        frequency=spec.frequency,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=False,
                    )
                )
            preview_target(
                DeviceTarget(
                    enabled=True,
                    power=powers[device_id],
                    mode=spec.mode,
                    frequency=spec.frequency,
                    linkage=roles[device_id],
                    timer_enabled=False,
                )
            )
            if device_id == spec.slave_device_id and spec.slave_power_after is not None:
                preview_target(
                    DeviceTarget(
                        enabled=True,
                        power=spec.slave_power_after,
                        mode=spec.mode,
                        frequency=spec.frequency,
                        linkage=spec.slave_role,
                        timer_enabled=False,
                    )
                )
            preview_target(
                DeviceTarget(
                    enabled=True,
                    power=_safe_power(device),
                    mode="constant",
                    frequency=spec.frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                )
            )
            preview_target(
                DeviceTarget(
                    enabled=snapshot.enabled,
                    power=snapshot.power,
                    mode=snapshot.mode,
                    frequency=snapshot.frequency,
                    linkage=snapshot.linkage,
                    timer_enabled=snapshot.timer_enabled,
                )
            )
    return tuple(snapshots)


def _print_preview(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    spec: LinkageTestSpec,
    snapshots: Sequence[DeviceControlSnapshot],
    token: str,
) -> None:
    by_id = {snapshot.device_id: snapshot for snapshot in snapshots}
    print("Native-linkage preflight passed; no control frame was sent.")
    for label, device_id, target_power in (
        ("Master", spec.master_device_id, spec.master_power),
        ("Slave", spec.slave_device_id, spec.slave_power),
    ):
        snapshot = by_id[device_id]
        print(f"{label}: {selected[device_id].name}")
        print(
            "  current="
            f"{snapshot.mode}/{snapshot.power}% timer={'on' if snapshot.timer_enabled else 'off'}; "
            f"test={spec.mode}/{target_power}%"
        )
    print(f"Duration: {spec.duration_seconds:g}s")
    if spec.bootstrap_active_schedule:
        print("Schedule bootstrap: active TimerON will be paused and exactly restored.")
    if spec.slave_power_after is not None:
        print(
            "Async slave live power check: "
            f"{spec.slave_power}% -> {spec.slave_power_after}% after "
            f"{spec.power_change_after_seconds:g}s"
        )
    print(f"Confirmation token: {token}")
    print(f"Journal directory: {canonical_journal_path(config).parent}")


def _updated_intent(
    intent: HardwareTestIntent,
    phase: HardwareTestIntentPhase,
    outcome: str | None,
) -> HardwareTestIntent:
    return intent.model_copy(
        update={
            "phase": phase,
            "updated_at": datetime.now(UTC),
            "outcome": outcome,
        }
    )


async def _preflight(
    config: AppConfig,
    spec: LinkageTestSpec,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
    qualification_store: JsonQualificationStore,
) -> int:
    _assert_no_verification_conflict()
    selected_ids = frozenset({spec.master_device_id, spec.slave_device_id})
    selected = _validate_config(config, selected_ids)
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        if _safety_latch_present(canonical_safety_latch_path(config)):
            raise HardwareTestError("persistent safety latch is active")
        if journal_store.load() is not None:
            raise HardwareTestError("unfinished linkage recovery exists; run recover-linkage")
        existing = intent_store.load()
        if existing is not None:
            if (
                existing.instance_id != config.instance.id
                and existing.phase is not HardwareTestIntentPhase.TERMINAL
            ):
                raise HardwareTestError(
                    "another instance owns the deployment-wide hardware-test intent"
                )
            if existing.phase in {
                HardwareTestIntentPhase.STARTED,
                HardwareTestIntentPhase.RECOVERY_REQUIRED,
            }:
                raise HardwareTestError("unfinished one-shot intent requires recover-linkage")
            if (
                existing.operation_id != spec.operation_id
                and existing.phase is not HardwareTestIntentPhase.TERMINAL
            ):
                raise HardwareTestError("another preflight is already armed")
            if (
                existing.operation_id == spec.operation_id
                and existing.phase is HardwareTestIntentPhase.TERMINAL
            ):
                raise HardwareTestError(
                    "terminal operation IDs cannot be replayed; choose a new ID"
                )

        devices = await _build_devices(config, selected, writable=False)
        async with _connected(devices):
            snapshots = await _capture_preview(devices, spec)
        if not spec.bootstrap_active_schedule:
            _require_current_qualifications(qualification_store, snapshots)
        token = preview_confirmation_token(config.instance.id, spec, snapshots)
        now = datetime.now(UTC)
        intent_store.save(
            HardwareTestIntent(
                instance_id=config.instance.id,
                operation_id=spec.operation_id,
                phase=HardwareTestIntentPhase.ARMED,
                confirmation_token=token,
                spec=spec,
                snapshots=snapshots,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
        )
    _print_preview(config, selected, spec, snapshots, token)
    return 0


async def _run_with_sigint(
    controller: TemporaryLinkageController,
    spec: LinkageTestSpec,
    *,
    interrupt_event: asyncio.Event | None = None,
    emergency_event: asyncio.Event | None = None,
    safety_interlock: LinkageSafetyInterlock | None = None,
    safety_latch_path: Path | None = None,
    late_emergency_cleanup: Callable[[], Awaitable[None]] | None = None,
) -> Any:
    loop = asyncio.get_running_loop()
    local_event = interrupt_event or asyncio.Event()
    installed_handlers: list[signal.Signals] = []
    signal_count = 0
    latch_errors: list[HardwareTestError] = []
    emergency_requested = False

    def emergency_stop() -> None:
        nonlocal emergency_requested
        emergency_requested = True
        if safety_interlock is None or safety_latch_path is None:
            return
        try:
            activate_persistent_safety_latch(safety_latch_path)
        except HardwareTestError as error:
            latch_errors.append(error)
        finally:
            # Even if durable storage failed, stop normal ON-state rollback in this process.
            safety_interlock.trip()

    def handle_stop_signal() -> None:
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            local_event.set()
        else:
            emergency_stop()

    if interrupt_event is None:
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(stop_signal, handle_stop_signal)
                installed_handlers.append(stop_signal)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - platform fallback
                break

    async def monitor_emergency_event() -> None:
        if emergency_event is not None:
            await emergency_event.wait()
            emergency_stop()

    run_task = asyncio.create_task(controller.run(spec), name="native-linkage-hardware-test")
    signal_task = asyncio.create_task(local_event.wait(), name="native-linkage-stop-signal")
    emergency_task = asyncio.create_task(
        monitor_emergency_event(),
        name="native-linkage-emergency-signal",
    )
    try:
        done, _ = await asyncio.wait(
            {run_task, signal_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if signal_task in done and not run_task.done():
            while (  # noqa: ASYNC110 - controller exposes state, not an activation event
                controller.active_operation_id is None and not run_task.done()
            ):
                await asyncio.sleep(0)
            if not run_task.done():
                await controller.stop(spec.operation_id)
        return await run_task
    finally:
        # Stop accepting harness-owned signals before the final emergency check. Any callback
        # already queued gets a chance to run at the gather await below; a late callback can no
        # longer appear after cleanup and leave an ON device behind a newly-created latch.
        for stop_signal in installed_handlers:
            loop.remove_signal_handler(stop_signal)
        if emergency_event is not None and emergency_event.is_set() and not emergency_requested:
            emergency_stop()
        for waiter in (signal_task, emergency_task):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(signal_task, emergency_task, return_exceptions=True)
        if safety_latch_path is not None and _safety_latch_present(safety_latch_path):
            emergency_requested = True
        if emergency_requested and late_emergency_cleanup is not None:
            async with asyncio.timeout(_LATE_EMERGENCY_STOP_TIMEOUT_SECONDS):
                await late_emergency_cleanup()
        if (
            latch_errors
            and safety_latch_path is not None
            and not _safety_latch_present(safety_latch_path)
        ):
            raise latch_errors[0]


async def _run_native_linkage(
    config: AppConfig,
    spec: LinkageTestSpec,
    confirmation: str,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    interlock: PersistentSafetyInterlock,
) -> int:
    _assert_no_verification_conflict()
    selected = _validate_config(
        config,
        frozenset({spec.master_device_id, spec.slave_device_id}),
    )
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        if _safety_latch_present(canonical_safety_latch_path(config)):
            raise HardwareTestError("persistent safety latch is active")
        if journal_store.load() is not None:
            raise HardwareTestError("unfinished linkage recovery exists; run recover-linkage")
        intent = intent_store.load()
        if intent is None or intent.phase is not HardwareTestIntentPhase.ARMED:
            raise HardwareTestError("run requires an armed preflight")
        if intent.instance_id != config.instance.id or intent.spec != spec:
            raise HardwareTestError("run arguments do not match the armed preflight")
        if not hmac.compare_digest(confirmation, intent.confirmation_token):
            raise ConfirmationMismatchError(
                "confirmation token does not match; no control frame was sent"
            )
        devices = await _build_devices(config, selected, writable=True)
        intent = _updated_intent(intent, HardwareTestIntentPhase.STARTED, None)
        intent_store.save(intent)

        def mark_terminal_before_clear() -> None:
            intent_store.save(_updated_intent(intent, HardwareTestIntentPhase.TERMINAL, "restored"))

        confirming_store = ConfirmingLinkageJournalStore(
            journal_store,
            instance_id=config.instance.id,
            expected_token=intent.confirmation_token,
            qualification_store=(
                None if spec.bootstrap_active_schedule else qualification_store
            ),
            before_clear=mark_terminal_before_clear,
        )
        controller = TemporaryLinkageController(
            devices,
            confirming_store,
            safety_interlock=interlock,
        )
        fallback_now = datetime.now(UTC)
        fallback_record = LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.PREPARED,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=fallback_now,
            updated_at=fallback_now,
            expires_at=fallback_now + timedelta(seconds=intent.spec.duration_seconds),
        )

        async def enforce_late_emergency_stop() -> None:
            await controller.enforce_safety_stop(fallback_record)

        async with _connected(devices):
            interlock.clear()
            try:
                result = await _run_with_sigint(
                    controller,
                    spec,
                    safety_interlock=interlock,
                    safety_latch_path=canonical_safety_latch_path(config),
                    late_emergency_cleanup=enforce_late_emergency_stop,
                )
            except BaseException:
                pending = journal_store.load()
                current_intent = intent_store.load()
                if pending is not None:
                    intent_store.save(
                        _updated_intent(
                            intent,
                            HardwareTestIntentPhase.RECOVERY_REQUIRED,
                            "recovery_required",
                        )
                    )
                elif (
                    current_intent is None
                    or current_intent.phase is not HardwareTestIntentPhase.TERMINAL
                ):
                    intent_store.save(
                        _updated_intent(
                            intent,
                            HardwareTestIntentPhase.TERMINAL,
                            "stopped_before_first_write",
                        )
                    )
                raise
            finally:
                interlock.trip()

        if journal_store.load() is not None:
            intent_store.save(
                _updated_intent(
                    intent,
                    HardwareTestIntentPhase.RECOVERY_REQUIRED,
                    "recovery_required",
                )
            )
            raise HardwareTestError("linkage journal remains after run; recovery is required")
        current_intent = intent_store.load()
        if current_intent is None or current_intent.phase is not HardwareTestIntentPhase.TERMINAL:
            # This is normally already durable via the journal wrapper's before-clear hook.
            intent_store.save(_updated_intent(intent, HardwareTestIntentPhase.TERMINAL, "restored"))
        if spec.bootstrap_active_schedule:
            created = confirming_store.created_record
            if created is None or created.snapshots != intent.snapshots:
                raise HardwareTestError("schedule-bootstrap qualification snapshot is unavailable")
            expected_qualified = {snapshot.device_id for snapshot in created.snapshots}
            if set(result.bootstrap_qualified_device_ids) != expected_qualified:
                raise HardwareTestError(
                    "schedule-bootstrap qualification did not complete for both devices"
                )
            if (
                spec.slave_power_after is not None
                and result.slave_power_change_verified is not True
            ):
                raise HardwareTestError(
                    "async slave live power change was not verified; "
                    "no qualification receipts were issued"
                )
            for snapshot in created.snapshots:
                qualification_power, stepped_power = (
                    TemporaryLinkageController._bootstrap_qualification_levels(
                        devices[snapshot.device_id]
                    )
                )
                qualification_store.save(
                    DeviceQualificationReceipt(
                        operation_id=spec.operation_id,
                        device_id=snapshot.device_id,
                        physical_binding=snapshot.physical_binding,
                        original_power=qualification_power,
                        step_power=stepped_power,
                        completed_at=result.completed_at,
                        valid_until=result.completed_at + timedelta(hours=24),
                    )
                )
    print(
        "Native-linkage test completed and the saved state was restored "
        f"({result.stop_reason.value})."
    )
    return 0


def _recovery_source(
    config: AppConfig,
    intent: HardwareTestIntent | None,
    record: LinkageTransactionRecord | None,
) -> tuple[
    LinkageTestSpec,
    tuple[DeviceControlSnapshot, ...],
    HardwareTestIntent | LinkageTransactionRecord,
]:
    if record is not None:
        if intent is not None and (
            intent.instance_id != config.instance.id
            or intent.operation_id != record.operation_id
            or intent.spec != record.spec
            or intent.snapshots != record.snapshots
        ):
            raise HardwareTestError("one-shot intent and recovery journal disagree")
        return record.spec, record.snapshots, record
    if intent is None or intent.phase is HardwareTestIntentPhase.TERMINAL:
        raise HardwareTestError("there is no unfinished native-linkage operation")
    if intent.instance_id != config.instance.id:
        raise HardwareTestError("one-shot intent belongs to another instance")
    return intent.spec, intent.snapshots, intent


def _status(
    config: AppConfig,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
) -> int:
    if not config.runtime.state_path.is_absolute():
        raise HardwareTestError("runtime.state_path must be an absolute persistent path")
    intent = intent_store.load()
    record = journal_store.load()

    intent_status = intent.phase.value if intent is not None else "none"
    journal_status = record.phase.value if record is not None else "none"
    latch_active = _safety_latch_present(canonical_safety_latch_path(config))
    if record is not None and record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
        next_action = (
            "clear the persistent safety latch, then use attended confirmed recovery"
            if latch_active
            else "use attended recover-linkage confirmation (automatic recovery is blocked)"
        )
    elif record is not None:
        next_action = (
            "clear the persistent safety latch outside this harness"
            if latch_active
            else "recover-linkage --recovery-first"
        )
    elif intent is not None and intent.phase is HardwareTestIntentPhase.STARTED:
        next_action = "recover-linkage (closes proven no-write crash state)"
    elif intent is not None and intent.phase is HardwareTestIntentPhase.RECOVERY_REQUIRED:
        next_action = "manual inspection (recovery journal is missing)"
    elif intent is not None and intent.phase is HardwareTestIntentPhase.ARMED:
        next_action = "run-native-linkage or confirmed preview cancellation"
    else:
        next_action = "preflight with a new operation ID"
    print(f"One-shot intent: {intent_status}")
    print(f"Recovery journal: {journal_status}")
    print(f"Persistent safety latch: {'active' if latch_active else 'clear'}")
    print(f"Next action: {next_action}")
    if record is not None or (
        intent is not None and intent.phase is not HardwareTestIntentPhase.TERMINAL
    ):
        spec, snapshots, revision = _recovery_source(config, intent, record)
        print(
            "Recovery confirmation token: "
            + recovery_confirmation_token(
                config.instance.id,
                spec,
                snapshots,
                revision,
            )
        )
    return 0


async def _recover_once(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    journal_store: JsonLinkageJournalStore | ConfirmingLinkageJournalStore,
    authority: LinkageRecoveryAuthority,
    interlock: PersistentSafetyInterlock,
) -> bool:
    if _safety_latch_present(canonical_safety_latch_path(config)):
        raise HardwareTestError("persistent safety latch is active")
    devices = await _build_devices(config, selected, writable=True)
    controller = TemporaryLinkageController(
        devices,
        journal_store,
        safety_interlock=interlock,
    )
    async with _connected(devices):
        interlock.clear()
        try:
            return await controller.recover_pending(authority=authority)
        finally:
            interlock.trip()


async def _recover_linkage(
    config: AppConfig,
    confirmation: str | None,
    recovery_first: bool,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
    interlock: PersistentSafetyInterlock,
) -> int:
    _assert_no_verification_conflict()
    intent = intent_store.load()
    record = journal_store.load()

    if (
        recovery_first
        and record is None
        and (intent is None or intent.phase is HardwareTestIntentPhase.TERMINAL)
    ):
        print("No unfinished native-linkage operation needs startup recovery.")
        return 0

    if record is None and intent is not None:
        if intent.instance_id != config.instance.id:
            raise HardwareTestError("one-shot intent belongs to another instance")
        if intent.phase is HardwareTestIntentPhase.STARTED:
            # STARTED precedes connect/controller.run; the core journal precedes its first write;
            # terminal intent precedes journal removal.  This state therefore proves zero writes.
            intent_store.save(
                _updated_intent(
                    intent,
                    HardwareTestIntentPhase.TERMINAL,
                    "crashed_before_first_write",
                )
            )
            print("The interrupted operation was closed as proven no-write; no frame was sent.")
            return 0
        if intent.phase is HardwareTestIntentPhase.RECOVERY_REQUIRED:
            raise HardwareTestError(
                "recovery-required intent has no journal; refusing synthetic hardware writes"
            )

    spec, snapshots, revision = _recovery_source(config, intent, record)
    if (
        recovery_first
        and record is not None
        and record.phase is not LinkageTransactionPhase.PREPARED
    ):
        if any(snapshot.timer_enabled for snapshot in record.snapshots):
            raise HardwareTestError(
                "automatic recovery of a TimerON snapshot is blocked; "
                "use attended confirmed recovery"
            )
        now = datetime.now(UTC)
        automatic_deadline = record.expires_at + timedelta(
            seconds=_MAX_AUTOMATIC_RECOVERY_GRACE_SECONDS
        )
        if now < record.created_at or now < record.updated_at or now > automatic_deadline:
            raise HardwareTestError(
                "automatic recovery window expired or the wall clock moved; "
                "use attended confirmed recovery"
            )
    token = recovery_confirmation_token(config.instance.id, spec, snapshots, revision)
    selected = _validate_config(
        config,
        frozenset({spec.master_device_id, spec.slave_device_id}),
    )
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        # Re-read both stores while owning both physical identities, before any connection/write.
        if journal_store.load() != record or intent_store.load() != intent:
            raise HardwareTestError("recovery state changed; request a new status/preview")

        if record is None:
            if recovery_first:
                print("No written transaction needs startup recovery; no frame was sent.")
                return 0
            if confirmation is None:
                print("Preview cancellation is fail-closed; no control frame was sent.")
                print(f"Recovery confirmation token: {token}")
                return 0
            if not hmac.compare_digest(confirmation, token):
                raise ConfirmationMismatchError(
                    "recovery confirmation token does not match; no control frame was sent"
                )
            if intent is None or intent.phase is not HardwareTestIntentPhase.ARMED:
                raise HardwareTestError("recovery journal is missing; no writes are permitted")
            intent_store.save(
                _updated_intent(
                    intent,
                    HardwareTestIntentPhase.TERMINAL,
                    "armed_preview_cancelled",
                )
            )
            print("The armed preview was closed; no control frame was sent.")
            return 0

        if recovery_first and record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
            raise HardwareTestError(
                "safety-interlock recovery requires an attended confirmation; "
                "automatic ON-state recovery is blocked"
            )
        if _safety_latch_present(canonical_safety_latch_path(config)):
            raise HardwareTestError(
                "persistent safety latch is active; exact ON-state recovery is blocked"
            )
        if not recovery_first:
            if confirmation is None:
                print("Recovery is fail-closed; no control frame was sent.")
                print(f"Recovery confirmation token: {token}")
                return 0
            if not hmac.compare_digest(confirmation, token):
                raise ConfirmationMismatchError(
                    "recovery confirmation token does not match; no control frame was sent"
                )

        if intent is not None:
            intent = _updated_intent(
                intent,
                HardwareTestIntentPhase.RECOVERY_REQUIRED,
                "recovery_started",
            )
            intent_store.save(intent)

        before_clear: Callable[[], None] | None = None
        if intent is not None:

            def mark_recovery_terminal_before_clear() -> None:
                intent_store.save(
                    _updated_intent(
                        intent,
                        HardwareTestIntentPhase.TERMINAL,
                        "recovered",
                    )
                )

            before_clear = mark_recovery_terminal_before_clear

        recovery_store = ConfirmingLinkageJournalStore(
            journal_store,
            instance_id=config.instance.id,
            expected_token=preview_confirmation_token(
                config.instance.id,
                spec,
                snapshots,
            ),
            before_clear=before_clear,
            before_load=_assert_no_verification_conflict,
            expected_loaded_record=record,
            require_loaded_record_match=True,
        )

        recovered = False
        for attempt in range(1, _RECOVERY_ATTEMPTS + 1):
            if _safety_latch_present(canonical_safety_latch_path(config)):
                break
            try:
                recovered = await _recover_once(
                    config,
                    selected,
                    recovery_store,
                    (
                        LinkageRecoveryAuthority.AUTOMATIC
                        if recovery_first
                        else LinkageRecoveryAuthority.ATTENDED
                    ),
                    interlock,
                )
            except Exception:
                recovered = False
            if recovered and journal_store.load() is None:
                break
            if attempt < _RECOVERY_ATTEMPTS:
                await asyncio.sleep(_RECOVERY_RETRY_SECONDS)

        if not recovered or journal_store.load() is not None:
            if intent is not None:
                intent_store.save(
                    _updated_intent(
                        intent,
                        HardwareTestIntentPhase.RECOVERY_REQUIRED,
                        "recovery_required",
                    )
                )
            raise HardwareTestError(
                f"exact recovery did not complete after {_RECOVERY_ATTEMPTS} bounded attempts"
            )

    if intent is None:
        now = datetime.now(UTC)
        intent = HardwareTestIntent(
            instance_id=config.instance.id,
            operation_id=spec.operation_id,
            phase=HardwareTestIntentPhase.TERMINAL,
            confirmation_token=preview_confirmation_token(
                config.instance.id,
                spec,
                snapshots,
            ),
            spec=spec,
            snapshots=snapshots,
            created_at=record.created_at,
            updated_at=now,
            outcome="recovered",
        )
    else:
        intent = _updated_intent(intent, HardwareTestIntentPhase.TERMINAL, "recovered")
    intent_store.save(intent)
    print("The unfinished native-linkage operation was restored and closed.")
    return 0


async def _dispatch(config: AppConfig, args: argparse.Namespace) -> int:
    validate_hardware_safety_root()
    journal_store = JsonLinkageJournalStore(canonical_journal_path(config))
    intent_store = JsonHardwareTestIntentStore(canonical_intent_path(config))
    qualification_store = JsonQualificationStore(canonical_qualification_directory(config))
    with intent_store.lease():
        if args.command == "status":
            return _status(config, intent_store, journal_store)
        interlock = PersistentSafetyInterlock(canonical_safety_latch_path(config))
        with interlock.lease():
            if args.command == "preflight":
                return await _preflight(
                    config,
                    _spec_from_args(args),
                    intent_store,
                    journal_store,
                    qualification_store,
                )
            if args.command == "run-native-linkage":
                return await _run_native_linkage(
                    config,
                    _spec_from_args(args),
                    args.confirm,
                    intent_store,
                    journal_store,
                    qualification_store,
                    interlock,
                )
            if args.command == "recover-linkage":
                return await _recover_linkage(
                    config,
                    args.confirm,
                    args.recovery_first,
                    intent_store,
                    journal_store,
                    interlock,
                )
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        config = load_config(args.config)
        return asyncio.run(_dispatch(config, args))
    except HardwareTestError as error:
        print(f"hardware test refused: {error}", file=sys.stderr)
        return 2
    except HardwareSafetyRootError as error:
        print(f"hardware test refused: {error}", file=sys.stderr)
        return 2
    except (LinkageJournalError, LinkageJournalClaimError):
        print(
            "hardware test refused: recovery state is unavailable or already owned",
            file=sys.stderr,
        )
        return 2
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        # Do not echo protocol objects or discovery identities from lower layers.
        print(f"hardware test failed safely ({type(error).__name__})", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - platform fallback without signal handlers
        print("hardware test interrupted after rollback", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
