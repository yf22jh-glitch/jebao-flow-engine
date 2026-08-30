"""Attended CLI for the mandatory first physical-write qualification.

The CLI is separate from the general hardware-test entry point so it can be reviewed and used as
a one-device gate before native linkage is even eligible.  It only exposes sanitized output;
private vendor identifiers, MAC addresses, and discovered addresses remain inside the adapter
factory and privacy-preserving bindings.
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
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jebao_flow.config import AppConfig, DeviceConfig, DeviceType, RuntimeMode, load_config
from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.factory import create_lan_device, create_read_only_lan_device
from jebao_flow.devices.identity import PhysicalDeviceBinding, configuration_fingerprint
from jebao_flow.devices.observer import resolve_device_bindings
from jebao_flow.devices.verification import (
    AttendedRestoreAuthority,
    DeviceVerificationError,
    DeviceVerificationJournalStore,
    DeviceVerificationPhase,
    DeviceVerificationRecord,
    DeviceVerificationRecoveryReason,
    DeviceVerificationResult,
    DeviceVerificationSnapshot,
    DeviceVerificationSpec,
    DeviceVerificationStopReason,
    FirstPhysicalWriteVerifier,
    JsonDeviceVerificationJournalStore,
)
from jebao_flow.hardware_guard import (
    DeploymentHardwareGuard,
    HardwareOperationBusyError,
    HardwareOperationLockError,
)
from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    emergency_stop_latch_path,
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
    HardwareTestIntentPhase,
    JsonHardwareTestIntentStore,
)
from jebao_flow.logging import configure_logging
from jebao_flow.persistence.linkage import JsonLinkageJournalStore, LinkageJournalError
from jebao_flow.persistence.qualification import (
    DeviceQualificationReceipt,
    JsonQualificationStore,
    QualificationStoreError,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.models import Capability, DeviceTarget, DiscoveredDevice, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.schedule_intent_validation import (
    TerminalScheduleIntentError,
    validate_terminal_schedule_intent_payload,
)

_TOKEN_VERSION = 1
_MAX_ATTENDED_POWER = 45
_MAX_PRO_WRITERS = 2
_MAX_COMMAND_INTERVAL_MS = 2000
_MAX_READBACK_DELAY_MS = 1000
_MAX_READBACK_ATTEMPTS = 3
_MAX_DISCOVERY_TIMEOUT_SECONDS = 5
_AUTOMATIC_RECOVERY_GRACE_SECONDS = 30
_ATTENDED_AUTHORITY_SECONDS = 120
_MAX_SAFETY_ARTIFACT_BYTES = 1024 * 1024
_SAFETY_RECOVERY_REASONS = frozenset(
    {
        DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
        DeviceVerificationRecoveryReason.SAFETY_STOP_FAILED,
    }
)


class DeviceVerificationCliError(RuntimeError):
    """A sanitized, fail-closed CLI refusal."""


class DeviceVerificationConfirmationError(DeviceVerificationCliError):
    """The supplied confirmation no longer authorizes the exact fresh state."""


class VerificationIntentPhase(StrEnum):
    ARMED = "armed"
    STARTED = "started"
    RECOVERY_REQUIRED = "recovery_required"
    TERMINAL = "terminal"


class VerificationIntentOutcome(StrEnum):
    RESTORED = "restored"
    QUALIFIED = "qualified"
    ABORTED = "aborted"
    RECOVERY_REQUIRED = "recovery_required"
    RECOVERED = "recovered"
    CRASHED_BEFORE_FIRST_WRITE = "crashed_before_first_write"
    PREVIEW_CANCELLED = "preview_cancelled"
    REFUSED_BEFORE_FIRST_WRITE = "refused_before_first_write"


class DeviceVerificationIntent(BaseModel):
    """Durable one-shot intent; the embedded binding contains only hashed identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=1, ge=1, le=1)
    instance_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1)
    phase: VerificationIntentPhase
    confirmation_token: str = Field(pattern=r"^JFV-[0-9A-F]{20}$")
    spec: DeviceVerificationSpec
    snapshot: DeviceVerificationSnapshot
    created_at: datetime
    updated_at: datetime
    outcome: VerificationIntentOutcome | None = None

    @model_validator(mode="after")
    def validate_intent(self) -> Self:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("intent operation must match its specification")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("intent timestamps must be timezone-aware")
        if self.phase is VerificationIntentPhase.TERMINAL:
            if self.outcome is None:
                raise ValueError("terminal intent requires an outcome")
        elif self.outcome is not None:
            raise ValueError("nonterminal intent cannot contain an outcome")
        return self


class JsonDeviceVerificationIntentStore:
    """Atomic 0600 intent/token store with a nonblocking O_NOFOLLOW lease."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def load(self) -> DeviceVerificationIntent | None:
        _require_safe_regular_file(self.path, allow_absent=True, label="verification intent")
        if not self.path.exists():
            return None
        descriptor = -1
        try:
            descriptor = _open_nofollow(self.path, os.O_RDONLY)
            _validate_open_file(descriptor, self.path, mode=0o600)
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                return DeviceVerificationIntent.model_validate_json(stream.read())
        except (OSError, ValidationError, ValueError) as error:
            raise DeviceVerificationCliError("verification intent is unreadable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def save(self, intent: DeviceVerificationIntent) -> None:
        _require_safe_regular_file(self.path, allow_absent=True, label="verification intent")
        temporary_path: Path | None = None
        try:
            temporary_path = _write_atomic_temporary(self.path, intent.model_dump_json(indent=2))
            temporary_path.replace(self.path)
            _fsync_directory(self.path.parent)
        except OSError as error:
            raise DeviceVerificationCliError("cannot persist verification intent") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @contextmanager
    def lease(self) -> Iterator[None]:
        descriptor = -1
        try:
            flags = os.O_CREAT | os.O_RDWR
            descriptor = _open_nofollow(self.lock_path, flags, mode=0o600)
            _validate_open_file(descriptor, self.lock_path, mode=0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeviceVerificationCliError(
                    "another device-verification process is active"
                ) from error
            _validate_open_file(descriptor, self.lock_path, mode=0o600)
            yield
        except DeviceVerificationCliError:
            raise
        except OSError as error:
            raise DeviceVerificationCliError("cannot lease verification intent") from error
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)


class _Discovery(Protocol):
    async def __call__(self, config: AppConfig) -> list[DiscoveredDevice]: ...


ReadOnlyDeviceFactory = Callable[[DeviceConfig, str, str], JebaoDevice]
WritableDeviceFactory = Callable[[DeviceConfig, AppConfig], JebaoDevice]
GuardFactory = Callable[[], DeploymentHardwareGuard]
Clock = Callable[[], datetime]


async def _default_discover(config: AppConfig) -> list[DiscoveredDevice]:
    discovery = GizwitsDiscovery(
        targets=config.observer.targets,
        bind_address=config.observer.bind_address,
    )
    return await discovery.discover(timeout_seconds=config.observer.discovery_timeout_seconds)


def _default_writer(config: DeviceConfig, app_config: AppConfig) -> JebaoDevice:
    return create_lan_device(config, app_config.runtime)


def _default_guard() -> DeploymentHardwareGuard:
    return DeploymentHardwareGuard()


@dataclass(frozen=True, slots=True)
class VerificationCliDependencies:
    discover: _Discovery = _default_discover
    read_only_device_factory: ReadOnlyDeviceFactory = create_read_only_lan_device
    writable_device_factory: WritableDeviceFactory = _default_writer
    guard_factory: GuardFactory = _default_guard
    clock: Clock = lambda: datetime.now(UTC)
    validate_safety_root: Callable[[], None] = validate_hardware_safety_root


DEFAULT_DEPENDENCIES = VerificationCliDependencies()


class ConfirmingDeviceVerificationStore(DeviceVerificationJournalStore):
    """Recheck the exact fresh snapshot immediately before the first durable journal."""

    def __init__(
        self,
        delegate: JsonDeviceVerificationJournalStore,
        *,
        instance_id: str,
        device_id: str,
        expected_token: str,
        before_create: Callable[[], None],
        before_clear: Callable[[], None],
        before_load: Callable[[], None] = lambda: None,
        expected_loaded_record: DeviceVerificationRecord | None = None,
        require_loaded_record_match: bool = False,
    ) -> None:
        self._delegate = delegate
        self._instance_id = instance_id
        self._device_id = device_id
        self._expected_token = expected_token
        self._before_create = before_create
        self._before_clear = before_clear
        self._before_load = before_load
        self._expected_loaded_record = expected_loaded_record
        self._require_loaded_record_match = require_loaded_record_match
        self.created_record: DeviceVerificationRecord | None = None

    def load(self) -> DeviceVerificationRecord | None:
        self._before_load()
        _require_safe_regular_file(
            self._delegate.path,
            allow_absent=True,
            label="verification journal",
        )
        record = self._delegate.load()
        if self._require_loaded_record_match and record != self._expected_loaded_record:
            raise DeviceVerificationConfirmationError(
                "recovery journal changed after confirmation; no restore frame was sent"
            )
        return record

    def create(self, record: DeviceVerificationRecord) -> None:
        self._before_create()
        actual = verification_confirmation_token(
            self._instance_id,
            self._device_id,
            record.spec,
            record.snapshot,
        )
        if not hmac.compare_digest(actual, self._expected_token):
            raise DeviceVerificationConfirmationError(
                "device state changed after preflight; no control frame was sent"
            )
        _require_safe_regular_file(
            self._delegate.path,
            allow_absent=True,
            label="verification journal",
        )
        self._delegate.create(record)
        self.created_record = record

    def save(self, record: DeviceVerificationRecord) -> None:
        _require_safe_regular_file(
            self._delegate.path,
            # ``enforce_safety_stop`` must be able to recreate a just-cleared journal when an
            # emergency signal lands in the post-restore race window.
            allow_absent=True,
            label="verification journal",
        )
        self._delegate.save(record)

    def clear(self) -> None:
        _require_safe_regular_file(
            self._delegate.path,
            allow_absent=False,
            label="verification journal",
        )
        # Terminal intent reaches disk before the only durable proof of a possible write is
        # removed. STARTED plus no journal therefore proves a pre-first-write crash.
        self._before_clear()
        self._delegate.clear()


def _open_nofollow(path: Path, flags: int, *, mode: int = 0o600) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise DeviceVerificationCliError("O_NOFOLLOW is required for hardware safety files")
    return os.open(path, flags | os.O_NOFOLLOW, mode)


def _require_safe_regular_file(path: Path, *, allow_absent: bool, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_absent:
            return
        raise DeviceVerificationCliError(f"{label} disappeared") from None
    except OSError as error:
        raise DeviceVerificationCliError(f"{label} metadata is unavailable") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise DeviceVerificationCliError(f"{label} has unsafe metadata")


def _validate_open_file(descriptor: int, path: Path, *, mode: int) -> None:
    opened = os.fstat(descriptor)
    current = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or opened.st_uid != os.geteuid()
        or current.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != mode
        or stat.S_IMODE(current.st_mode) != mode
        or opened.st_nlink != 1
        or current.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise DeviceVerificationCliError("hardware safety file changed while opening")


def _write_atomic_temporary(destination: Path, payload: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = _open_nofollow(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _persist_emergency_latch() -> None:
    """Durably create the deployment-wide emergency marker before tripping memory state."""

    path = emergency_stop_latch_path()
    descriptor = -1
    created = False
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        descriptor = _open_nofollow(path, flags, mode=0o600)
        created = True
        os.fchmod(descriptor, 0o600)
        _validate_open_file(descriptor, path, mode=0o600)
        payload = b"device_verification_emergency_stop\n"
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    except FileExistsError:
        # Any existing filesystem object is already treated as an authoritative latch by the
        # deployment guard.  Never follow, replace, or weaken it here.
        pass
    except OSError as error:
        raise DeviceVerificationCliError("cannot persist emergency-stop latch") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if created:
        _fsync_directory(path.parent)


def verification_confirmation_token(
    instance_id: str,
    device_id: str,
    spec: DeviceVerificationSpec,
    snapshot: DeviceVerificationSnapshot,
) -> str:
    canonical = {
        "version": _TOKEN_VERSION,
        "instance_id": instance_id,
        "device_id": device_id,
        "spec": spec.model_dump(mode="json"),
        "snapshot": snapshot.model_dump(mode="json"),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFV-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def verification_recovery_token(
    instance_id: str,
    device_id: str,
    spec: DeviceVerificationSpec,
    snapshot: DeviceVerificationSnapshot,
    revision: DeviceVerificationIntent | DeviceVerificationRecord,
) -> str:
    preview = verification_confirmation_token(instance_id, device_id, spec, snapshot)
    if isinstance(revision, DeviceVerificationRecord):
        revision_data = {
            "kind": "journal",
            "phase": revision.phase.value,
            "write_started": revision.write_started,
            "recovery_reason": (
                revision.recovery_reason.value if revision.recovery_reason is not None else None
            ),
            "error_code": revision.error_code.value if revision.error_code is not None else None,
            "created_at": revision.created_at.isoformat(),
            "updated_at": revision.updated_at.isoformat(),
            "expires_at": revision.expires_at.isoformat(),
        }
    else:
        revision_data = {
            "kind": "intent",
            "phase": revision.phase.value,
            "updated_at": revision.updated_at.isoformat(),
            "outcome": revision.outcome.value if revision.outcome is not None else None,
        }
    canonical = json.dumps(revision_data, sort_keys=True, separators=(",", ":"))
    encoded = f"recover:{preview}:{canonical}".encode()
    return f"JVR-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--device", required=True, help="configured logical device ID")
    parser.add_argument("--target-power", required=True, type=int)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--verification-interval", type=float, default=0.25)


def build_parser(*, prog: str = "jebao-flow-device-verify") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser(
        "preflight-device",
        help="capture and arm a strictly read-only first-write preview",
    )
    _add_spec_arguments(preflight)

    run = commands.add_parser(
        "run-device-verification",
        help="execute one exact armed single-device qualification",
    )
    _add_spec_arguments(run)
    run.add_argument("--confirm", required=True)

    recover = commands.add_parser(
        "recover-device-verification",
        help="restore, never resume, an unfinished qualification",
    )
    recovery_mode = recover.add_mutually_exclusive_group(required=True)
    recovery_mode.add_argument("--confirm")
    recovery_mode.add_argument("--recovery-first", action="store_true")

    commands.add_parser("verification-status", help="show sanitized verification state")
    return parser


def _spec_from_args(args: argparse.Namespace) -> DeviceVerificationSpec:
    return DeviceVerificationSpec(
        operation_id=args.operation_id,
        target_power=args.target_power,
        duration_seconds=args.duration,
        verification_interval_seconds=args.verification_interval,
    )


def _validate_runtime_and_writer_scope(config: AppConfig) -> dict[str, DeviceConfig]:
    if config.runtime.mode is not RuntimeMode.CONTROL:
        raise DeviceVerificationCliError("device verification requires runtime.mode=control")
    if config.runtime.dry_run:
        raise DeviceVerificationCliError("device verification requires runtime.dry_run=false")
    if config.observer.enabled:
        raise DeviceVerificationCliError("device verification requires observer.enabled=false")
    if not config.runtime.state_path.is_absolute():
        raise DeviceVerificationCliError("runtime.state_path must be an absolute persistent path")
    if config.observer.discovery_timeout_seconds > _MAX_DISCOVERY_TIMEOUT_SECONDS:
        raise DeviceVerificationCliError("discovery timeout exceeds the attended safety cap")

    writers = {
        device.id: device for device in config.devices if device.control.allow_hardware_writes
    }
    if len(writers) > _MAX_PRO_WRITERS:
        raise DeviceVerificationCliError("at most two Pro controllers may enable hardware writes")
    if any(
        device.type is not DeviceType.WAVEMAKER
        or device.product_key != LOCAL_WAVEMAKER_PRO.product_key
        for device in writers.values()
    ):
        raise DeviceVerificationCliError(
            "hardware writes may only be enabled for Local Wavemaker Pro controllers"
        )
    return writers


def _selected_config(config: AppConfig, device_id: str) -> DeviceConfig:
    writers = _validate_runtime_and_writer_scope(config)
    selected = next((device for device in config.devices if device.id == device_id), None)
    if selected is None:
        raise DeviceVerificationCliError("selected device is not configured")
    if selected.id not in writers or not selected.enabled:
        raise DeviceVerificationCliError("selected device is not enabled for hardware writes")
    if (
        selected.type is not DeviceType.WAVEMAKER
        or selected.product_key != LOCAL_WAVEMAKER_PRO.product_key
    ):
        raise DeviceVerificationCliError("selected device must be Local Wavemaker Pro")
    identity = selected.identity
    if identity is None or identity.device_id is None or identity.mac_address is None:
        raise DeviceVerificationCliError("selected device requires exact vendor ID and MAC")
    control = selected.control
    if control.minimum_command_interval_ms > _MAX_COMMAND_INTERVAL_MS:
        raise DeviceVerificationCliError("command interval exceeds the attended safety cap")
    if control.readback_delay_ms > _MAX_READBACK_DELAY_MS:
        raise DeviceVerificationCliError("readback delay exceeds the attended safety cap")
    if control.readback_attempts > _MAX_READBACK_ATTEMPTS:
        raise DeviceVerificationCliError("readback attempts exceed the attended safety cap")
    return selected


def _config_binding(config: DeviceConfig) -> PhysicalDeviceBinding:
    identity = config.identity
    if (
        identity is None
        or identity.device_id is None
        or identity.mac_address is None
        or config.product_key is None
    ):
        raise DeviceVerificationCliError("stable physical identity is incomplete")
    fingerprint_source = config.model_dump(
        mode="json",
        exclude={"address", "discovery", "name"},
    )
    fingerprint_source["product_key"] = config.product_key
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=identity.device_id,
        mac_address=identity.mac_address,
        product_key=config.product_key,
        config_fingerprint=configuration_fingerprint(fingerprint_source),
    )


async def _resolve_selected(
    config: AppConfig,
    selected: DeviceConfig,
    dependencies: VerificationCliDependencies,
) -> tuple[str, str]:
    try:
        discovered = await dependencies.discover(config)
        resolved = resolve_device_bindings((selected,), discovered)
    except Exception as error:
        raise DeviceVerificationCliError("stable-identity discovery failed") from error
    endpoint = resolved.get(selected.id)
    if endpoint is None or endpoint.product_key != LOCAL_WAVEMAKER_PRO.product_key:
        raise DeviceVerificationCliError("selected exact Pro identity did not resolve uniquely")
    return endpoint.address, endpoint.product_key


async def _read_only_device(
    config: AppConfig,
    selected: DeviceConfig,
    dependencies: VerificationCliDependencies,
) -> JebaoDevice:
    address, product_key = await _resolve_selected(config, selected, dependencies)
    return dependencies.read_only_device_factory(selected, address, product_key)


async def _writable_device(
    config: AppConfig,
    selected: DeviceConfig,
    dependencies: VerificationCliDependencies,
) -> JebaoDevice:
    address, product_key = await _resolve_selected(config, selected, dependencies)
    values = selected.model_dump(mode="python")
    values.update({"address": address, "product_key": product_key, "discovery": None})
    resolved_config = DeviceConfig.model_validate(values)
    return dependencies.writable_device_factory(resolved_config, config)


async def _capture_snapshot(
    device: JebaoDevice,
    spec: DeviceVerificationSpec,
) -> DeviceVerificationSnapshot:
    connected = False
    try:
        await device.connect()
        connected = True
        capabilities = device.capabilities
        if (
            capabilities.product_key != LOCAL_WAVEMAKER_PRO.product_key
            or capabilities.model != LOCAL_WAVEMAKER_PRO.name
            or "constant" not in capabilities.native_modes
            or LinkageRole.INDEPENDENT not in capabilities.linkage_roles
        ):
            raise DeviceVerificationCliError("resolved controller is not audited Pro hardware")
        required = {
            Capability.ENABLED,
            Capability.POWER,
            Capability.MODE,
            Capability.FREQUENCY,
            Capability.LINKAGE,
            Capability.TIMER,
        }
        if not required <= capabilities.writable:
            raise DeviceVerificationCliError("controller lacks exact-restore capabilities")
        binding = device.physical_binding
        if binding is None:
            raise DeviceVerificationCliError("controller has no exact physical binding")
        state = await device.get_state()
        snapshot = DeviceVerificationSnapshot.from_state(
            state,
            physical_binding=binding,
        )
        limits = capabilities.power_limits
        step = capabilities.power_step
        if (
            snapshot.power > _MAX_ATTENDED_POWER
            or not limits.min_power <= snapshot.power <= limits.max_power
            or snapshot.power % step
            or not limits.min_power <= spec.target_power <= limits.max_power
            or spec.target_power % step
            or not 1 <= snapshot.power - spec.target_power <= 5
        ):
            raise DeviceVerificationCliError("current or target power is outside safe bounds")
        preview = getattr(device, "preview_target", None)
        if callable(preview):
            preview(
                DeviceTarget(
                    enabled=True,
                    power=snapshot.power,
                    mode="constant",
                    frequency=snapshot.frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                )
            )
            preview(DeviceTarget(enabled=True, power=spec.target_power))
        return snapshot
    except DeviceVerificationCliError:
        raise
    except Exception as error:
        raise DeviceVerificationCliError("read-only Pro preflight failed") from error
    finally:
        if connected or device.connected:
            try:
                await device.disconnect()
            except Exception:
                pass


def _updated_intent(
    intent: DeviceVerificationIntent,
    phase: VerificationIntentPhase,
    outcome: VerificationIntentOutcome | None,
    *,
    now: datetime,
) -> DeviceVerificationIntent:
    return intent.model_copy(update={"phase": phase, "outcome": outcome, "updated_at": now})


def _load_verification_journal(
    store: JsonDeviceVerificationJournalStore,
) -> DeviceVerificationRecord | None:
    _require_safe_regular_file(store.path, allow_absent=True, label="verification journal")
    return store.load()


def _assert_no_native_conflict() -> None:
    _assert_no_schedule_linkage_conflict()
    _require_safe_regular_file(
        native_linkage_journal_path(),
        allow_absent=True,
        label="native-linkage journal",
    )
    native_journal = JsonLinkageJournalStore(native_linkage_journal_path()).load()
    if native_journal is not None:
        raise DeviceVerificationCliError("unfinished native-linkage operation blocks verification")
    _require_safe_regular_file(
        native_linkage_intent_path(),
        allow_absent=True,
        label="native-linkage intent",
    )
    native_intent = JsonHardwareTestIntentStore(native_linkage_intent_path()).load()
    if native_intent is not None and native_intent.phase is not HardwareTestIntentPhase.TERMINAL:
        raise DeviceVerificationCliError("active native-linkage intent blocks verification")


def _assert_no_schedule_linkage_conflict() -> None:
    journal = schedule_linkage_journal_path()
    _require_safe_regular_file(
        journal,
        allow_absent=True,
        label="schedule-linkage journal",
    )
    if os.path.lexists(journal):
        raise DeviceVerificationCliError(
            "unfinished schedule-linkage operation blocks verification"
        )

    intent_path = schedule_linkage_intent_path()
    _require_safe_regular_file(
        intent_path,
        allow_absent=True,
        label="schedule-linkage intent",
    )
    if not os.path.lexists(intent_path):
        return
    descriptor = -1
    try:
        descriptor = _open_nofollow(intent_path, os.O_RDONLY)
        _validate_open_file(descriptor, intent_path, mode=0o600)
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            encoded = stream.read(_MAX_SAFETY_ARTIFACT_BYTES + 1)
        if len(encoded.encode()) > _MAX_SAFETY_ARTIFACT_BYTES:
            raise DeviceVerificationCliError("schedule-linkage intent is too large")
        payload = json.loads(encoded)
    except (OSError, TypeError, ValueError) as error:
        raise DeviceVerificationCliError("schedule-linkage intent is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        validate_terminal_schedule_intent_payload(payload)
    except TerminalScheduleIntentError as error:
        raise DeviceVerificationCliError(
            "nonterminal schedule-linkage intent blocks verification"
        ) from error


def _validate_artifact_paths() -> None:
    for path, label in (
        (verification_intent_path(), "verification intent"),
        (verification_journal_path(), "verification journal"),
        (native_linkage_intent_path(), "native-linkage intent"),
        (native_linkage_journal_path(), "native-linkage journal"),
        (schedule_linkage_intent_path(), "schedule-linkage intent"),
        (schedule_linkage_journal_path(), "schedule-linkage journal"),
    ):
        _require_safe_regular_file(path, allow_absent=True, label=label)
    directory = qualification_directory()
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise DeviceVerificationCliError("qualification directory is unavailable") from error
    if (
        directory.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise DeviceVerificationCliError("qualification directory has unsafe metadata")


async def _preflight(
    config: AppConfig,
    spec: DeviceVerificationSpec,
    device_id: str,
    intent_store: JsonDeviceVerificationIntentStore,
    journal_store: JsonDeviceVerificationJournalStore,
    dependencies: VerificationCliDependencies,
) -> int:
    selected = _selected_config(config, device_id)
    guard = dependencies.guard_factory()
    guard.clear()
    if not guard.permitted:
        raise DeviceVerificationCliError("persistent safety latch is active")
    # Read-only preflight still owns the deployment lease.  This closes the native workflow's
    # journal-before-adapter-create window and makes its exact snapshot/token one serialized
    # observation, even though preflight itself sends no control frame.
    with guard.lease():
        guard.clear()
        if not guard.permitted:
            raise DeviceVerificationCliError("persistent safety latch is active")
        _assert_no_native_conflict()
        if _load_verification_journal(journal_store) is not None:
            raise DeviceVerificationCliError("unfinished device recovery blocks preflight")
        existing = intent_store.load()
        if existing is not None:
            if existing.phase is not VerificationIntentPhase.TERMINAL:
                raise DeviceVerificationCliError("unfinished device-verification intent exists")
            if existing.operation_id == spec.operation_id:
                raise DeviceVerificationCliError("terminal operation IDs cannot be replayed")

        device = await _read_only_device(config, selected, dependencies)
        snapshot = await _capture_snapshot(device, spec)
        if not guard.permitted:
            raise DeviceVerificationCliError("persistent safety latch became active")
        token = verification_confirmation_token(
            config.instance.id,
            selected.id,
            spec,
            snapshot,
        )
        now = dependencies.clock()
        intent_store.save(
            DeviceVerificationIntent(
                instance_id=config.instance.id,
                operation_id=spec.operation_id,
                device_id=selected.id,
                phase=VerificationIntentPhase.ARMED,
                confirmation_token=token,
                spec=spec,
                snapshot=snapshot,
                created_at=now,
                updated_at=now,
            )
        )
    print("Device-verification preflight passed; no control frame was sent.")
    print(f"Current: constant/{snapshot.power}% timer=off linkage=independent")
    print(f"Target: constant/{spec.target_power}%")
    print(f"Maximum operation duration: {spec.duration_seconds:g}s")
    print(f"Confirmation token: {token}")
    return 0


async def _run_with_signals(
    controller: FirstPhysicalWriteVerifier,
    device: JebaoDevice,
    spec: DeviceVerificationSpec,
    guard: DeploymentHardwareGuard,
    confirming_store: ConfirmingDeviceVerificationStore,
    fallback_record: DeviceVerificationRecord,
) -> DeviceVerificationResult:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    installed: list[signal.Signals] = []
    signal_count = 0
    latch_error = False

    def request_stop() -> None:
        nonlocal signal_count, latch_error
        signal_count += 1
        if signal_count == 1:
            stop_event.set()
            return
        try:
            _persist_emergency_latch()
        except DeviceVerificationCliError:
            latch_error = True
        finally:
            # The local trip follows the durable latch attempt and immediately invalidates the
            # epoch checked under the adapter's mutation lock.
            guard.trip()

    async def enforce_late_emergency_stop() -> None:
        # A normal controller return releases the deployment lease and intentionally trips the
        # process-local epoch.  Only a durable latch (or the explicit second-signal path) is a late
        # emergency here; treating normal lease release as an e-stop would issue an unsafe extra
        # fallback write after an otherwise completed operation.
        if signal_count < 2 and not guard.emergency_latch_active and not latch_error:
            return
        record = confirming_store.created_record
        if record is None:
            # The fresh JFV snapshot can be rejected before journal creation.  Re-read the exact
            # controller state after the latch, so the fallback recovery target is not merely the
            # older preflight preview.  Failure remains fail-closed and uses the attended-only
            # safety fallback rather than skipping the OFF command.
            connected = False
            try:
                await device.connect()
                connected = True
                state = await device.get_state()
                binding = device.physical_binding
                if binding is None or binding != fallback_record.snapshot.physical_binding:
                    raise DeviceVerificationConfirmationError(
                        "emergency snapshot physical binding changed"
                    )
                snapshot = DeviceVerificationSnapshot.from_state(
                    state,
                    physical_binding=binding,
                )
                observed_at = datetime.now(UTC)
                record = fallback_record.model_copy(
                    update={
                        "snapshot": snapshot,
                        "created_at": observed_at,
                        "updated_at": observed_at,
                        "expires_at": observed_at
                        + timedelta(seconds=fallback_record.spec.duration_seconds),
                    }
                )
            except Exception:
                record = fallback_record
            finally:
                if connected or device.connected:
                    try:
                        await device.disconnect()
                    except Exception:
                        pass
        await controller.enforce_safety_stop(record)
        raise DeviceVerificationCliError("emergency stop did not retain its recovery journal")

    for candidate in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(candidate, request_stop)
            installed.append(candidate)
        except (NotImplementedError, RuntimeError):
            continue

    run_task = asyncio.create_task(controller.run(spec), name="device-verification")
    stop_task = asyncio.create_task(stop_event.wait(), name="device-verification-stop")
    try:
        done, _ = await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done and not run_task.done():
            stopped = await controller.stop(spec.operation_id)
            if not stopped:
                # A first signal that wins the task-start race must still result in zero writes.
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
                return DeviceVerificationResult(
                    operation_id=spec.operation_id,
                    stop_reason=DeviceVerificationStopReason.STOPPED_BEFORE_WRITE,
                    lower_power_applied=False,
                    completed_at=datetime.now(UTC),
                )
        return await run_task
    except asyncio.CancelledError:
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        raise
    finally:
        for candidate in installed:
            loop.remove_signal_handler(candidate)
        try:
            # Removing a handler does not discard callbacks already queued by the event loop.
            # Drain them once, then enforce the persistent latch as the final pre-receipt gate.
            await asyncio.sleep(0)
            await enforce_late_emergency_stop()
        finally:
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)


async def _run_verification(
    config: AppConfig,
    spec: DeviceVerificationSpec,
    device_id: str,
    confirmation: str,
    intent_store: JsonDeviceVerificationIntentStore,
    journal_store: JsonDeviceVerificationJournalStore,
    dependencies: VerificationCliDependencies,
) -> int:
    selected = _selected_config(config, device_id)
    _assert_no_native_conflict()
    if _load_verification_journal(journal_store) is not None:
        raise DeviceVerificationCliError("unfinished device recovery exists")
    intent = intent_store.load()
    if intent is None or intent.phase is not VerificationIntentPhase.ARMED:
        raise DeviceVerificationCliError("run requires an armed preflight")
    if (
        intent.instance_id != config.instance.id
        or intent.device_id != selected.id
        or intent.spec != spec
    ):
        raise DeviceVerificationCliError("run arguments do not match the armed preflight")
    if not hmac.compare_digest(confirmation, intent.confirmation_token):
        raise DeviceVerificationConfirmationError("confirmation token does not match")

    device = await _writable_device(config, selected, dependencies)
    started_intent = _updated_intent(
        intent,
        VerificationIntentPhase.STARTED,
        None,
        now=dependencies.clock(),
    )
    intent_store.save(started_intent)

    def before_create() -> None:
        _assert_no_native_conflict()
        current = intent_store.load()
        if current != started_intent:
            raise DeviceVerificationCliError("verification intent changed before first write")

    def before_clear() -> None:
        intent_store.save(
            _updated_intent(
                started_intent,
                VerificationIntentPhase.TERMINAL,
                VerificationIntentOutcome.RESTORED,
                now=dependencies.clock(),
            )
        )

    confirming_store = ConfirmingDeviceVerificationStore(
        journal_store,
        instance_id=config.instance.id,
        device_id=selected.id,
        expected_token=intent.confirmation_token,
        before_create=before_create,
        before_clear=before_clear,
        before_load=_assert_no_native_conflict,
    )
    guard = dependencies.guard_factory()
    guard.clear()
    if not guard.permitted:
        raise DeviceVerificationCliError("persistent safety latch is active")
    controller = FirstPhysicalWriteVerifier(
        device,
        confirming_store,
        global_guard=guard,
    )
    fallback_created_at = dependencies.clock()
    fallback_record = DeviceVerificationRecord(
        operation_id=spec.operation_id,
        phase=DeviceVerificationPhase.PREPARED,
        spec=spec,
        snapshot=intent.snapshot,
        created_at=fallback_created_at,
        updated_at=fallback_created_at,
        expires_at=fallback_created_at + timedelta(seconds=spec.duration_seconds),
        write_started=False,
    )
    try:
        result = await _run_with_signals(
            controller,
            device,
            spec,
            guard,
            confirming_store,
            fallback_record,
        )
    except BaseException:
        pending = _load_verification_journal(journal_store)
        current = intent_store.load()
        if pending is not None:
            intent_store.save(
                started_intent.model_copy(
                    update={
                        "phase": VerificationIntentPhase.RECOVERY_REQUIRED,
                        "outcome": None,
                        "confirmation_token": verification_confirmation_token(
                            config.instance.id,
                            selected.id,
                            pending.spec,
                            pending.snapshot,
                        ),
                        "spec": pending.spec,
                        "snapshot": pending.snapshot,
                        "updated_at": dependencies.clock(),
                    }
                )
            )
        elif current is None or current.phase is not VerificationIntentPhase.TERMINAL:
            intent_store.save(
                _updated_intent(
                    started_intent,
                    VerificationIntentPhase.TERMINAL,
                    VerificationIntentOutcome.REFUSED_BEFORE_FIRST_WRITE,
                    now=dependencies.clock(),
                )
            )
        raise

    if _load_verification_journal(journal_store) is not None:
        pending = _load_verification_journal(journal_store)
        if pending is None:
            raise DeviceVerificationCliError("recovery journal changed during inspection")
        intent_store.save(
            started_intent.model_copy(
                update={
                    "phase": VerificationIntentPhase.RECOVERY_REQUIRED,
                    "outcome": None,
                    "confirmation_token": verification_confirmation_token(
                        config.instance.id,
                        selected.id,
                        pending.spec,
                        pending.snapshot,
                    ),
                    "spec": pending.spec,
                    "snapshot": pending.snapshot,
                    "updated_at": dependencies.clock(),
                }
            )
        )
        raise DeviceVerificationCliError("exact restore did not clear the recovery journal")

    if not result.lower_power_applied:
        intent_store.save(
            _updated_intent(
                started_intent,
                VerificationIntentPhase.TERMINAL,
                VerificationIntentOutcome.ABORTED,
                now=dependencies.clock(),
            )
        )
        print("Device verification stopped safely; no qualification receipt was issued.")
        return 0

    created = confirming_store.created_record
    if created is None or created.snapshot != intent.snapshot:
        raise DeviceVerificationCliError("fresh qualification snapshot is unavailable")
    completed_at = result.completed_at
    receipt_store = JsonQualificationStore(qualification_directory())
    receipt_store.save(
        DeviceQualificationReceipt(
            operation_id=spec.operation_id,
            device_id=selected.id,
            physical_binding=created.snapshot.physical_binding,
            original_power=created.snapshot.power,
            step_power=spec.target_power,
            completed_at=completed_at,
            valid_until=completed_at + timedelta(hours=24),
        )
    )
    intent_store.save(
        _updated_intent(
            started_intent,
            VerificationIntentPhase.TERMINAL,
            VerificationIntentOutcome.QUALIFIED,
            now=dependencies.clock(),
        )
    )
    print("Device verification completed, exact state restored, receipt valid for 24 hours.")
    return 0


def _device_id_for_record(config: AppConfig, record: DeviceVerificationRecord) -> str:
    matches: list[str] = []
    for candidate in config.devices:
        try:
            if _config_binding(candidate) == record.snapshot.physical_binding:
                matches.append(candidate.id)
        except DeviceVerificationCliError:
            continue
    if len(matches) != 1:
        raise DeviceVerificationCliError("recovery binding does not map to one configured Pro")
    return matches[0]


def _recovery_source(
    config: AppConfig,
    intent: DeviceVerificationIntent | None,
    record: DeviceVerificationRecord,
) -> tuple[str, DeviceVerificationSpec, DeviceVerificationSnapshot]:
    device_id = intent.device_id if intent is not None else _device_id_for_record(config, record)
    if intent is not None and (
        intent.instance_id != config.instance.id
        or intent.operation_id != record.operation_id
        or intent.spec != record.spec
        or intent.snapshot != record.snapshot
    ):
        raise DeviceVerificationCliError("verification intent and recovery journal disagree")
    return device_id, record.spec, record.snapshot


async def _recover_verification(
    config: AppConfig,
    confirmation: str | None,
    recovery_first: bool,
    intent_store: JsonDeviceVerificationIntentStore,
    journal_store: JsonDeviceVerificationJournalStore,
    dependencies: VerificationCliDependencies,
) -> int:
    _validate_runtime_and_writer_scope(config)
    _assert_no_native_conflict()
    intent = intent_store.load()
    record = _load_verification_journal(journal_store)

    if record is None:
        if intent is None or intent.phase is VerificationIntentPhase.TERMINAL:
            print("No unfinished device verification needs recovery.")
            return 0
        if intent.instance_id != config.instance.id:
            raise DeviceVerificationCliError("verification intent belongs to another instance")
        if intent.phase is VerificationIntentPhase.STARTED:
            intent_store.save(
                _updated_intent(
                    intent,
                    VerificationIntentPhase.TERMINAL,
                    VerificationIntentOutcome.CRASHED_BEFORE_FIRST_WRITE,
                    now=dependencies.clock(),
                )
            )
            print("Interrupted verification closed as proven no-write; no frame was sent.")
            return 0
        if intent.phase is VerificationIntentPhase.RECOVERY_REQUIRED:
            raise DeviceVerificationCliError(
                "recovery-required intent has no journal; synthetic writes are blocked"
            )
        if recovery_first:
            intent_store.save(
                _updated_intent(
                    intent,
                    VerificationIntentPhase.TERMINAL,
                    VerificationIntentOutcome.PREVIEW_CANCELLED,
                    now=dependencies.clock(),
                )
            )
            print("Armed preview has no written transaction; no frame was sent.")
            return 0
        token = verification_recovery_token(
            config.instance.id,
            intent.device_id,
            intent.spec,
            intent.snapshot,
            intent,
        )
        if confirmation is None or not hmac.compare_digest(confirmation, token):
            raise DeviceVerificationConfirmationError("recovery confirmation does not match")
        intent_store.save(
            _updated_intent(
                intent,
                VerificationIntentPhase.TERMINAL,
                VerificationIntentOutcome.PREVIEW_CANCELLED,
                now=dependencies.clock(),
            )
        )
        print("Armed preview cancelled; no control frame was sent.")
        return 0

    device_id, spec, snapshot = _recovery_source(config, intent, record)
    selected = _selected_config(config, device_id)
    token = verification_recovery_token(config.instance.id, device_id, spec, snapshot, record)
    now = dependencies.clock()
    if recovery_first:
        if record.recovery_reason in _SAFETY_RECOVERY_REASONS:
            raise DeviceVerificationCliError(
                "safety-interlock recovery requires attended confirmation"
            )
        if record.write_started:
            deadline = record.expires_at + timedelta(seconds=_AUTOMATIC_RECOVERY_GRACE_SECONDS)
            if now < record.created_at or now < record.updated_at or now > deadline:
                raise DeviceVerificationCliError(
                    "automatic recovery window expired or wall clock moved backwards"
                )
    elif confirmation is None or not hmac.compare_digest(confirmation, token):
        raise DeviceVerificationConfirmationError("recovery confirmation does not match")

    guard = dependencies.guard_factory()
    guard.clear()
    if not guard.permitted:
        raise DeviceVerificationCliError("persistent safety latch blocks exact ON restore")
    device = await _writable_device(config, selected, dependencies)
    if intent is None:
        intent = DeviceVerificationIntent(
            instance_id=config.instance.id,
            operation_id=record.operation_id,
            device_id=selected.id,
            phase=VerificationIntentPhase.RECOVERY_REQUIRED,
            confirmation_token=verification_confirmation_token(
                config.instance.id,
                selected.id,
                record.spec,
                record.snapshot,
            ),
            spec=record.spec,
            snapshot=record.snapshot,
            created_at=record.created_at,
            updated_at=now,
        )
        intent_store.save(intent)
    else:
        intent = _updated_intent(
            intent,
            VerificationIntentPhase.RECOVERY_REQUIRED,
            None,
            now=now,
        )
        intent_store.save(intent)

    recovery_intent = intent

    def before_clear() -> None:
        intent_store.save(
            _updated_intent(
                recovery_intent,
                VerificationIntentPhase.TERMINAL,
                VerificationIntentOutcome.RECOVERED,
                now=dependencies.clock(),
            )
        )

    recovery_store = ConfirmingDeviceVerificationStore(
        journal_store,
        instance_id=config.instance.id,
        device_id=selected.id,
        expected_token=intent.confirmation_token,
        before_create=lambda: (_ for _ in ()).throw(
            DeviceVerificationCliError("recovery may not create or resume a test journal")
        ),
        before_clear=before_clear,
        before_load=_assert_no_native_conflict,
        expected_loaded_record=record,
        require_loaded_record_match=True,
    )
    controller = FirstPhysicalWriteVerifier(
        device,
        recovery_store,
        global_guard=guard,
    )
    authority = None
    if confirmation is not None:
        issued_at = dependencies.clock()
        authority = AttendedRestoreAuthority(
            operation_id=record.operation_id,
            physical_binding=record.snapshot.physical_binding,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=_ATTENDED_AUTHORITY_SECONDS),
        )
    try:
        recovered = await controller.recover_pending(attended_authority=authority)
    except BaseException:
        if _load_verification_journal(journal_store) is not None:
            intent_store.save(
                _updated_intent(
                    recovery_intent,
                    VerificationIntentPhase.RECOVERY_REQUIRED,
                    None,
                    now=dependencies.clock(),
                )
            )
        raise
    if not recovered or _load_verification_journal(journal_store) is not None:
        raise DeviceVerificationCliError("exact device recovery did not complete")
    print("Unfinished device verification was exactly restored and closed; no receipt issued.")
    return 0


def _status(
    config: AppConfig,
    intent_store: JsonDeviceVerificationIntentStore,
    journal_store: JsonDeviceVerificationJournalStore,
    dependencies: VerificationCliDependencies,
) -> int:
    intent = intent_store.load()
    record = _load_verification_journal(journal_store)
    other_workflow_conflict = False
    try:
        _assert_no_native_conflict()
    except DeviceVerificationCliError:
        other_workflow_conflict = True
    guard = dependencies.guard_factory()
    latch_active = guard.emergency_latch_active
    print(f"Verification intent: {intent.phase.value if intent is not None else 'none'}")
    print(f"Verification journal: {record.phase.value if record is not None else 'none'}")
    recovery_reason = (
        record.recovery_reason.value if record is not None and record.recovery_reason else "none"
    )
    print(f"Recovery reason: {recovery_reason}")
    print(f"Persistent safety latch: {'active' if latch_active else 'clear'}")
    print(f"Other hardware workflow conflict: {'yes' if other_workflow_conflict else 'no'}")
    source = record or intent
    if source is not None:
        device_id = (
            intent.device_id if intent is not None else _device_id_for_record(config, record)
        )
        print(
            "Recovery confirmation token: "
            + verification_recovery_token(
                config.instance.id,
                device_id,
                source.spec,
                source.snapshot,
                source,
            )
        )
    return 0


async def dispatch(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    dependencies: VerificationCliDependencies = DEFAULT_DEPENDENCIES,
) -> int:
    """Dispatch API reusable by ``jebao-flow-hwtest`` and isolated unit tests."""

    dependencies.validate_safety_root()
    _validate_artifact_paths()
    intent_store = JsonDeviceVerificationIntentStore(verification_intent_path())
    journal_store = JsonDeviceVerificationJournalStore(verification_journal_path())
    with intent_store.lease():
        if args.command == "preflight-device":
            return await _preflight(
                config,
                _spec_from_args(args),
                args.device,
                intent_store,
                journal_store,
                dependencies,
            )
        if args.command == "run-device-verification":
            return await _run_verification(
                config,
                _spec_from_args(args),
                args.device,
                args.confirm,
                intent_store,
                journal_store,
                dependencies,
            )
        if args.command == "recover-device-verification":
            return await _recover_verification(
                config,
                args.confirm,
                args.recovery_first,
                intent_store,
                journal_store,
                dependencies,
            )
        if args.command == "verification-status":
            return _status(config, intent_store, journal_store, dependencies)
    raise AssertionError("unhandled device-verification command")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        config = load_config(args.config)
        return asyncio.run(dispatch(config, args))
    except (
        DeviceVerificationCliError,
        HardwareSafetyRootError,
        HardwareOperationBusyError,
        HardwareOperationLockError,
    ) as error:
        print(f"device verification refused: {error}", file=sys.stderr)
        return 2
    except DeviceVerificationError as error:
        print(f"device verification failed safely: {error.code.value}", file=sys.stderr)
        return 2
    except (LinkageJournalError, QualificationStoreError):
        print("device verification refused: durable safety state is unavailable", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"device verification failed safely ({type(error).__name__})", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("device verification interrupted after restore", file=sys.stderr)
        return 130


__all__ = [
    "ConfirmingDeviceVerificationStore",
    "DEFAULT_DEPENDENCIES",
    "DeviceVerificationCliError",
    "DeviceVerificationConfirmationError",
    "DeviceVerificationIntent",
    "JsonDeviceVerificationIntentStore",
    "VerificationCliDependencies",
    "VerificationIntentOutcome",
    "VerificationIntentPhase",
    "build_parser",
    "dispatch",
    "main",
    "verification_confirmation_token",
    "verification_recovery_token",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
