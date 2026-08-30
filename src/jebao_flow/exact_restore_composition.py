"""Production composition boundary for attended standalone exact restore.

This module is the only production location that turns the otherwise write-free deployment
configuration into a write-enabled :class:`LanJebaoDevice`.  It does so only for the two exact
physical bindings embedded in an immutable, attended operation manifest.  The native ASYNC
experiment and its frozen write harness are intentionally not imported.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from jebao_flow.config import AppConfig, DeviceConfig
from jebao_flow.exact_restore import (
    ExactRestoreBaseline,
    ExactRestoreController,
    ExactRestoreCycle,
    ExactRestoreDeviceBaseline,
    ExactRestoreEvidenceReference,
    ExactRestoreRecord,
    ExactRestoreRole,
    ExactRestoreVerificationPolicy,
    ExactScheduleImage,
    OuterControlSnapshot,
    RestorePowerPolicy,
    SafeManualTarget,
    prepare_exact_restore_record,
    prepare_qualified_final_restore_record,
)
from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.exact_restore_receipts import ExactRestoreReceiptArchive
from jebao_flow.exact_restore_runtime import FreshExplicitRestoreObserver, RestoreWriter
from jebao_flow.exact_restore_store import ExactRestoreJournalStore
from jebao_flow.hardware_safety import (
    emergency_stop_latch_path,
    native_linkage_intent_path,
    native_linkage_journal_path,
    schedule_linkage_intent_path,
    schedule_linkage_journal_path,
    temporary_schedule_journal_path,
    verification_intent_path,
    verification_journal_path,
)
from jebao_flow.physical_identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.models import LinkageRole
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LocalWavemakerProScheduleSnapshot,
)
from jebao_flow.protocol.session import (
    DEFAULT_CONTROL_PORT,
    STATE_REPLY_ACTION,
    ReadOnlyGizwitsSession,
)
from jebao_flow.read_only_collector import (
    CaptureTarget,
    PilotSeriesStore,
    VerifiedPilotPairArtifact,
    select_capture_pair,
)
from jebao_flow.safety.limits import PowerLimits

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_MAX_PRIVATE_FILE_BYTES = 1024 * 1024


class ExactRestoreCompositionError(RuntimeError):
    """Privacy-safe production composition refusal."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ExactRestoreArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_artifact_id: str = Field(min_length=1, max_length=80)
    plan_sha256: Sha256Digest
    series_artifact_id: str = Field(min_length=1, max_length=80)
    series_sha256: Sha256Digest
    accepted_pair_ordinal: int = Field(ge=0)


class ExactRestoreRoleOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ExactRestoreRole
    logical_id: str = Field(min_length=1, max_length=128)
    power_policy: RestorePowerPolicy
    safe_constant_power: int = Field(ge=0, le=100)
    safe_constant_frequency: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_safe_target(self) -> Self:
        if not self.power_policy.permits(self.safe_constant_power):
            raise ValueError("safe constant power violates the attended policy")
        return self


class ExactRestoreNetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    discovery_port: int = Field(default=12414, ge=1, le=65535)
    control_port: int = Field(default=DEFAULT_CONTROL_PORT, ge=1, le=65535)
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    max_identity_age_seconds: float = Field(default=5.0, gt=0, le=30)


class ExactRestoreOperationManifest(BaseModel):
    """Owner-only, predeclared inputs for one complete qualification operation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    version: Literal[1] = 1
    operation_nonce: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    evidence: ExactRestoreArtifactReference
    devices: tuple[ExactRestoreRoleOperation, ExactRestoreRoleOperation]
    verification_policy: ExactRestoreVerificationPolicy
    network: ExactRestoreNetworkPolicy = Field(default_factory=ExactRestoreNetworkPolicy)

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if tuple(item.role for item in self.devices) != (
            ExactRestoreRole.MASTER,
            ExactRestoreRole.SLAVE,
        ):
            raise ValueError("operation devices must be ordered master then slave")
        if self.devices[0].logical_id == self.devices[1].logical_id:
            raise ValueError("operation logical devices must be distinct")
        if self.network.max_identity_age_seconds > (
            self.verification_policy.max_observation_age_seconds
        ):
            raise ValueError("identity age cannot exceed the observation age")
        return self

    @property
    def manifest_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    def operation_id_for_cycle(self, cycle: ExactRestoreCycle) -> str:
        """Bind every manifest field and the exact cycle into one opaque durable id."""

        label = {
            ExactRestoreCycle.SENTINEL_QUALIFICATION: "sentinel",
            ExactRestoreCycle.BASELINE_RESTORE: "baseline",
        }[cycle]
        return self._operation_id(label)

    def _operation_id(self, label: str) -> str:
        digest = hashlib.sha256(
            b"jebao-flow/exact-restore-composition/v1\0"
            + label.encode("ascii")
            + b"\0"
            + bytes.fromhex(self.manifest_sha256)
        ).hexdigest()
        return f"er-{label}-{digest}"

    @property
    def sentinel_operation_id(self) -> str:
        return self.operation_id_for_cycle(ExactRestoreCycle.SENTINEL_QUALIFICATION)

    @property
    def baseline_operation_id(self) -> str:
        return self.operation_id_for_cycle(ExactRestoreCycle.BASELINE_RESTORE)

    @property
    def final_restore_operation_id(self) -> str:
        return self._operation_id("final")

    def for_role(self, role: ExactRestoreRole) -> ExactRestoreRoleOperation:
        return next(item for item in self.devices if item.role is role)


@dataclass(frozen=True, slots=True)
class ExactRestoreComposition:
    manifest: ExactRestoreOperationManifest
    config: AppConfig
    targets: Mapping[ExactRestoreRole, CaptureTarget]
    controller: ExactRestoreController
    store: ExactRestoreJournalStore
    guard: ExactRestoreGuard
    observer: FreshExplicitRestoreObserver


def _read_owner_only_regular_file(path: str | Path, *, code: str) -> bytes:
    """Read one stable 0600 owner file without following its final component."""

    selected = Path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ExactRestoreCompositionError(code)
    descriptor = -1
    try:
        before = selected.lstat()
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(selected, flags)
        opened = os.fstat(descriptor)
        current = selected.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or before.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_size > _MAX_PRIVATE_FILE_BYTES
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ExactRestoreCompositionError(code)
        chunks: list[bytes] = []
        remaining = _MAX_PRIVATE_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        reread = os.fstat(descriptor)
        if (
            len(payload) > _MAX_PRIVATE_FILE_BYTES
            or (reread.st_dev, reread.st_ino, reread.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or stat.S_IMODE(reread.st_mode) != 0o600
            or reread.st_uid != os.geteuid()
            or reread.st_nlink != 1
        ):
            raise ExactRestoreCompositionError(code)
        after = selected.lstat()
        if (
            (after.st_dev, after.st_ino, after.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or not stat.S_ISREG(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
        ):
            raise ExactRestoreCompositionError(code)
        return payload
    except ExactRestoreCompositionError:
        raise
    except OSError as error:
        raise ExactRestoreCompositionError(code) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_locked_config(path: str | Path) -> AppConfig:
    payload = _read_owner_only_regular_file(path, code="private_config_invalid")
    try:
        raw = yaml.safe_load(payload.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root is not a mapping")
        return AppConfig.model_validate(raw)
    except (UnicodeDecodeError, ValueError, ValidationError, yaml.YAMLError) as error:
        raise ExactRestoreCompositionError("private_config_invalid") from error


def load_operation_manifest(path: str | Path) -> ExactRestoreOperationManifest:
    payload = _read_owner_only_regular_file(path, code="operation_manifest_invalid")
    try:
        return ExactRestoreOperationManifest.model_validate_json(payload)
    except (ValueError, ValidationError) as error:
        raise ExactRestoreCompositionError("operation_manifest_invalid") from error


def select_operation_targets(
    config: AppConfig,
    manifest: ExactRestoreOperationManifest,
) -> dict[ExactRestoreRole, CaptureTarget]:
    selected = select_capture_pair(
        config,
        manifest.devices[0].logical_id,
        manifest.devices[1].logical_id,
    )
    enabled_pro_ids = {
        device.id
        for device in config.devices
        if device.enabled and device.product_key == LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
    }
    selected_ids = {item.logical_id for item in selected}
    if enabled_pro_ids != selected_ids:
        raise ExactRestoreCompositionError("operation_pair_not_exact")
    devices = {device.id: device for device in config.devices}
    for operation in manifest.devices:
        configured = devices[operation.logical_id].limits
        policy = operation.power_policy
        if (
            policy.min_power < configured.min_power
            or policy.max_power > configured.max_power
            or policy.attended_max_power > configured.max_power
        ):
            raise ExactRestoreCompositionError("attended_policy_exceeds_device_limits")
    return {
        ExactRestoreRole.MASTER: selected[0],
        ExactRestoreRole.SLAVE: selected[1],
    }


def _physical_binding_for_target(target: CaptureTarget) -> PhysicalDeviceBinding:
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=target.vendor_device_id,
        mac_address=target.mac_address,
        product_key=target.product_key,
        config_fingerprint=target.config_fingerprint,
    )
    if physical_identity_key(binding) != target.identity_binding_sha256:
        raise ExactRestoreCompositionError("operation_binding_mismatch")
    return binding


def _decode_verified_sample(
    *,
    role: ExactRestoreRole,
    target: CaptureTarget,
    wire_frame: bytes,
) -> tuple[OuterControlSnapshot, ExactScheduleImage, tuple[str, ...]]:
    try:
        frame = decode_frame(wire_frame)
        if (
            frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
            or len(frame.payload) != LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE + 1
            or frame.payload[0] != STATE_REPLY_ACTION
        ):
            raise ValueError("not one explicit Pro reply")
        status = frame.payload[1:]
        schema = get_product_schema(target.product_key)
        values = schema.decode_status(status)
        enabled = values["SwitchON"]
        timer_enabled = values["TimerON"]
        linkage = values["Linkage"]
        mode = values["Mode"]
        power = values["Flow"]
        frequency = values["Frequency"]
        if type(enabled) is not bool or type(timer_enabled) is not bool:
            raise ValueError("invalid outer booleans")
        if not isinstance(linkage, str) or not isinstance(mode, str):
            raise ValueError("invalid outer enums")
        if type(power) is not int or type(frequency) is not int:
            raise ValueError("invalid outer numbers")
        snapshot = LocalWavemakerProScheduleSnapshot.from_status(status).validate()
        outer = OuterControlSnapshot(
            enabled=enabled,
            timer_enabled=timer_enabled,
            linkage=LinkageRole(linkage),
            mode=mode,
            power=power,
            frequency=frequency,
        )
        schedule = ExactScheduleImage.from_bytes(snapshot.image)
        problems = tuple(schema.active_problems(values))
    except Exception as error:
        raise ExactRestoreCompositionError("baseline_frame_invalid") from error
    if role not in {ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE}:
        raise ExactRestoreCompositionError("baseline_role_invalid")
    return outer, schedule, problems


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExactRestoreCompositionError("baseline_timing_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ExactRestoreCompositionError("baseline_timing_invalid")
    return parsed.astimezone(UTC)


def build_verified_baseline(
    manifest: ExactRestoreOperationManifest,
    targets: Mapping[ExactRestoreRole, CaptureTarget],
    artifact: VerifiedPilotPairArtifact,
    *,
    now: datetime | None = None,
) -> ExactRestoreBaseline:
    """Decode one fully verified preserved pair without substituting manifest state claims."""

    evidence = manifest.evidence
    if (
        artifact.plan_artifact_id != evidence.plan_artifact_id
        or artifact.plan_sha256 != evidence.plan_sha256
        or artifact.series_id != evidence.series_artifact_id
        or artifact.series_sha256 != evidence.series_sha256
        or artifact.ordinal != evidence.accepted_pair_ordinal
        or tuple(sample.role for sample in artifact.samples) != ("a", "b")
    ):
        raise ExactRestoreCompositionError("baseline_artifact_mismatch")
    if artifact.pair_completion_gap_ns > int(
        manifest.verification_policy.max_final_pair_gap_seconds * 1_000_000_000
    ):
        raise ExactRestoreCompositionError("baseline_pair_gap_exceeded")

    sample_completion_times = tuple(
        _parse_utc(sample.read.completed_utc) for sample in artifact.samples
    )
    captured_at = max(sample_completion_times)
    current = now or datetime.now(UTC)
    if (
        current.tzinfo is None
        or current.utcoffset() != timedelta(0)
        or any(current < completed for completed in sample_completion_times)
    ):
        raise ExactRestoreCompositionError("baseline_timing_invalid")
    if any(
        (current - completed).total_seconds()
        > manifest.verification_policy.max_observation_age_seconds
        for completed in sample_completion_times
    ):
        raise ExactRestoreCompositionError("baseline_observation_expired")

    devices: list[ExactRestoreDeviceBaseline] = []
    for role, sample in zip(
        (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE),
        artifact.samples,
        strict=True,
    ):
        target = targets[role]
        operation = manifest.for_role(role)
        if (
            operation.logical_id != target.logical_id
            or sample.identity_binding_sha256 != target.identity_binding_sha256
            or hashlib.sha256(sample.raw_wire_frame).hexdigest() != sample.raw_wire_frame_sha256
        ):
            raise ExactRestoreCompositionError("baseline_binding_mismatch")
        outer, schedule, problems = _decode_verified_sample(
            role=role,
            target=target,
            wire_frame=sample.raw_wire_frame,
        )
        if problems:
            raise ExactRestoreCompositionError("baseline_device_fault")
        try:
            devices.append(
                ExactRestoreDeviceBaseline(
                    role=role,
                    logical_id=target.logical_id,
                    physical_binding=_physical_binding_for_target(target),
                    outer=outer,
                    schedule=schedule,
                    power_policy=operation.power_policy,
                    raw_frame_sha256=sample.raw_wire_frame_sha256,
                )
            )
        except ValueError as error:
            raise ExactRestoreCompositionError("baseline_admission_failed") from error

    return ExactRestoreBaseline(
        devices=(devices[0], devices[1]),
        evidence=ExactRestoreEvidenceReference(
            plan_artifact_id=artifact.plan_artifact_id,
            series_artifact_id=artifact.series_id,
            pair_ordinal=artifact.ordinal,
            pair_manifest_sha256=artifact.pair_manifest_sha256,
        ),
        verification_policy=manifest.verification_policy,
        captured_at=captured_at,
    )


def safe_targets_for_manifest(
    manifest: ExactRestoreOperationManifest,
) -> tuple[SafeManualTarget, SafeManualTarget]:
    return tuple(
        SafeManualTarget(
            role=item.role,
            power=item.safe_constant_power,
            frequency=item.safe_constant_frequency,
        )
        for item in manifest.devices
    )  # type: ignore[return-value]


def extract_manifest_pair(
    manifest: ExactRestoreOperationManifest,
    artifact_root: str | Path,
) -> VerifiedPilotPairArtifact:
    store = PilotSeriesStore(Path(artifact_root))
    reference = store.load(manifest.evidence.series_artifact_id)
    if (
        reference.plan_artifact_id != manifest.evidence.plan_artifact_id
        or reference.plan_sha256 != manifest.evidence.plan_sha256
    ):
        raise ExactRestoreCompositionError("baseline_artifact_mismatch")
    try:
        return store.extract_verified_accepted_pair(
            reference,
            expected_series_sha256=manifest.evidence.series_sha256,
            ordinal=manifest.evidence.accepted_pair_ordinal,
        )
    except Exception as error:
        raise ExactRestoreCompositionError("baseline_artifact_invalid") from error


def _safe_read_legacy_intent_phase(path: Path) -> str | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    payload = _read_owner_only_regular_file(path, code="legacy_workflow_state_invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExactRestoreCompositionError("legacy_workflow_state_invalid") from error
    if not isinstance(value, dict) or not isinstance(value.get("phase"), str):
        raise ExactRestoreCompositionError("legacy_workflow_state_invalid")
    return value["phase"]


def require_cross_workflow_quiescent() -> None:
    """Fail closed on every legacy journal, nonterminal intent, or emergency latch."""

    for path in (
        native_linkage_journal_path(),
        verification_journal_path(),
        schedule_linkage_journal_path(),
        temporary_schedule_journal_path(),
    ):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ExactRestoreCompositionError("legacy_workflow_state_invalid") from error
        raise ExactRestoreCompositionError("legacy_workflow_nonterminal")
    for path in (
        native_linkage_intent_path(),
        verification_intent_path(),
        schedule_linkage_intent_path(),
    ):
        phase = _safe_read_legacy_intent_phase(path)
        if phase is not None and phase != "terminal":
            raise ExactRestoreCompositionError("legacy_workflow_nonterminal")
    try:
        emergency_stop_latch_path().lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ExactRestoreCompositionError("emergency_latch_unavailable") from error
    else:
        raise ExactRestoreCompositionError("emergency_latch_active")


class _SharedRolePacer:
    def __init__(self, interval_seconds: float) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds < 0.1:
            raise ValueError("write interval must be finite and at least 100ms")
        self._interval = interval_seconds
        self._lock = asyncio.Lock()
        self._last_attempt_completed: float | None = None

    async def wait_before_connect(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            if self._last_attempt_completed is not None:
                delay = self._interval - (loop.time() - self._last_attempt_completed)
                if delay > 0:
                    await asyncio.sleep(delay)

    async def run_write(self, operation: Callable[[], Any]) -> Any:
        # Controller execution is serial, but retain the lock so an accidental second adapter
        # cannot bypass the shared completion timestamp.
        async with self._lock:
            loop = asyncio.get_running_loop()
            result = operation()
            try:
                if hasattr(result, "__await__"):
                    return await result
                return result
            finally:
                # An exception after transport handoff is an uncertain attempt and must still
                # consume the device-wide interval.
                self._last_attempt_completed = loop.time()


class _PacedRestoreWriter:
    """Keep the per-device command interval across fresh action-specific LAN adapters."""

    def __init__(self, writer: RestoreWriter, pacer: _SharedRolePacer) -> None:
        self._writer = writer
        self._pacer = pacer

    @property
    def address(self) -> str:
        return self._writer.address

    @property
    def physical_binding(self) -> PhysicalDeviceBinding | None:
        return self._writer.physical_binding

    async def connect(self) -> None:
        # Pace before connecting so the later post-connect identity ticket is always issued
        # after the wait. Waiting below the ticket boundary could let identity freshness expire.
        await self._pacer.wait_before_connect()
        await self._writer.connect()

    async def disconnect(self) -> None:
        await self._writer.disconnect()

    def connected_session_token(self) -> object:
        return self._writer.connected_session_token()

    async def write_target_connected(
        self,
        target: Any,
        *,
        connected_session_token: object,
        guard: Callable[[], bool] | None = None,
    ) -> None:
        await self._pacer.run_write(
            lambda: self._writer.write_target_connected(
                target,
                connected_session_token=connected_session_token,
                guard=guard,
            )
        )

    async def restore_schedule_image_connected(
        self,
        image: bytes,
        *,
        connected_session_token: object,
        guard: Callable[[], bool] | None = None,
    ) -> object:
        return await self._pacer.run_write(
            lambda: self._writer.restore_schedule_image_connected(
                image,
                connected_session_token=connected_session_token,
                guard=guard,
            )
        )


def _device_configs(config: AppConfig) -> dict[str, DeviceConfig]:
    return {device.id: device for device in config.devices}


def _build_attended_production_composition(
    *,
    config: AppConfig,
    manifest: ExactRestoreOperationManifest,
) -> ExactRestoreComposition:
    """Construct the installed CLI's attended write runtime.

    This private function has exactly one production caller: ``exact_restore_cli``.  Imports of
    the write-capable LAN adapter are local so the ``prepare`` command never loads that graph.
    """

    from jebao_flow.devices.lan import LanJebaoDevice
    from jebao_flow.protocol.control_session import GizwitsSession

    targets = select_operation_targets(config, manifest)
    configs = _device_configs(config)
    bindings = {role: _physical_binding_for_target(target) for role, target in targets.items()}
    pacers = {
        role: _SharedRolePacer(
            configs[target.logical_id].control.minimum_command_interval_ms / 1000
        )
        for role, target in targets.items()
    }
    network = manifest.network

    def discovery_factory() -> GizwitsDiscovery:
        return GizwitsDiscovery(
            targets=config.observer.targets,
            bind_address=config.observer.bind_address,
            port=network.discovery_port,
        )

    def read_session_factory(address: str) -> ReadOnlyGizwitsSession:
        return ReadOnlyGizwitsSession(
            address,
            port=network.control_port,
            connect_timeout_seconds=network.timeout_seconds,
            response_timeout_seconds=network.timeout_seconds,
        )

    def writer_factory(role: ExactRestoreRole, endpoint: Any) -> RestoreWriter:
        target = targets[role]
        device = configs[target.logical_id]
        policy = manifest.for_role(role).power_policy

        def session_factory(address: str) -> GizwitsSession:
            return GizwitsSession(
                address,
                port=network.control_port,
                connect_timeout_seconds=network.timeout_seconds,
                response_timeout_seconds=network.timeout_seconds,
            )

        writer = LanJebaoDevice(
            device_id=target.logical_id,
            address=endpoint.address,
            product_key=target.product_key,
            power_limits=PowerLimits(
                min_power=policy.min_power,
                max_power=policy.attended_max_power,
            ),
            power_step=policy.power_step,
            minimum_command_interval_ms=device.control.minimum_command_interval_ms,
            readback_delay_ms=device.control.readback_delay_ms,
            readback_attempts=device.control.readback_attempts,
            allow_hardware_writes=True,
            physical_binding=bindings[role],
            session_factory=session_factory,
        )
        return _PacedRestoreWriter(writer, pacers[role])

    observer = FreshExplicitRestoreObserver(
        targets=targets,
        discovery_factory=discovery_factory,
        session_factory=read_session_factory,
        max_identity_age_seconds=network.max_identity_age_seconds,
        discovery_timeout_seconds=network.timeout_seconds,
        writer_factory=writer_factory,
    )
    store = ExactRestoreJournalStore()
    guard = ExactRestoreGuard()
    archive = ExactRestoreReceiptArchive()
    controller = ExactRestoreController(
        store,
        guard,
        observe=observer.observe,
        resolve_device=observer.resolve_device,
        qualification_receipts=archive,
    )
    return ExactRestoreComposition(
        manifest=manifest,
        config=config,
        targets=targets,
        controller=controller,
        store=store,
        guard=guard,
        observer=observer,
    )


def prepare_operation(
    *,
    config: AppConfig,
    manifest: ExactRestoreOperationManifest,
    artifact_root: str | Path,
    now: datetime | None = None,
) -> ExactRestoreRecord:
    """Create PREPARED under the global lease; no device network operation occurs."""

    targets = select_operation_targets(config, manifest)
    artifact = extract_manifest_pair(manifest, artifact_root)
    baseline = build_verified_baseline(manifest, targets, artifact, now=now)
    record = prepare_exact_restore_record(
        baseline,
        safe_targets_for_manifest(manifest),
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=manifest.sentinel_operation_id,
        now=now,
    )
    store = ExactRestoreJournalStore()
    guard = ExactRestoreGuard()

    async def unavailable_observer(_role: ExactRestoreRole) -> object:
        raise ExactRestoreCompositionError("prepare_has_no_device_runtime")

    def unavailable_resolver(_role: ExactRestoreRole, _observation: object) -> object:
        raise ExactRestoreCompositionError("prepare_has_no_device_runtime")

    controller = ExactRestoreController(
        store,
        guard,
        observe=unavailable_observer,
        resolve_device=unavailable_resolver,
        qualification_receipts=ExactRestoreReceiptArchive(),
    )
    with guard.lease():
        guard.clear()
        if not guard.permitted:
            raise ExactRestoreCompositionError("safety_interlock_blocked")
        require_cross_workflow_quiescent()
        archive = ExactRestoreReceiptArchive()
        try:
            finalized = tuple(
                archive.load_operation_finalization(operation_id)
                for operation_id in (
                    manifest.sentinel_operation_id,
                    manifest.baseline_operation_id,
                )
            )
            if any(item is not None for item in finalized):
                raise ExactRestoreCompositionError("operation_already_finalized")
        except ExactRestoreCompositionError:
            raise
        except Exception as error:
            raise ExactRestoreCompositionError("operation_finalization_unavailable") from error
        controller.create(record)
    return record


def stage_qualified_final_restore(
    composition: ExactRestoreComposition,
    qualified_record: ExactRestoreRecord,
    *,
    now: datetime | None = None,
) -> ExactRestoreRecord:
    """Durably stage the already-qualified phase-5 restore before any app write."""

    if (
        qualified_record.operation_id != composition.manifest.baseline_operation_id
        or qualified_record.cycle is not ExactRestoreCycle.BASELINE_RESTORE
        or qualified_record.phase.value != "final_verified"
    ):
        raise ExactRestoreCompositionError("qualified_record_invalid")
    try:
        staged = prepare_qualified_final_restore_record(
            qualified_record,
            operation_id=composition.manifest.final_restore_operation_id,
            now=now,
        )
    except (TypeError, ValueError) as error:
        raise ExactRestoreCompositionError("qualified_record_invalid") from error
    with composition.guard.lease():
        composition.guard.clear()
        if not composition.guard.permitted:
            raise ExactRestoreCompositionError("safety_interlock_blocked")
        require_cross_workflow_quiescent()
        if composition.store.load() is not None:
            raise ExactRestoreCompositionError("exact_restore_journal_not_empty")
        composition.controller.create_qualified_final_restore(staged)
    confirmed = load_bound_record(composition)
    if confirmed != staged:
        raise ExactRestoreCompositionError("final_restore_stage_unconfirmed")
    return confirmed


def load_bound_record(composition: ExactRestoreComposition) -> ExactRestoreRecord:
    """Load a journal while proving it still belongs to the supplied private manifest/config."""

    payload = composition.store.load()
    if payload is None:
        raise ExactRestoreCompositionError("exact_restore_journal_absent")
    try:
        record = ExactRestoreRecord.model_validate(payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise ExactRestoreCompositionError("exact_restore_journal_invalid") from error
    if record.cycle is ExactRestoreCycle.SENTINEL_QUALIFICATION:
        expected_operation_id = composition.manifest.sentinel_operation_id
    elif (
        record.qualification_final_record is not None
        and record.qualification_final_record.cycle is ExactRestoreCycle.BASELINE_RESTORE
    ):
        expected_operation_id = composition.manifest.final_restore_operation_id
    else:
        expected_operation_id = composition.manifest.baseline_operation_id
    if record.operation_id != expected_operation_id:
        raise ExactRestoreCompositionError("operation_manifest_mismatch")
    if record.safe_targets != safe_targets_for_manifest(composition.manifest):
        raise ExactRestoreCompositionError("operation_manifest_mismatch")
    for role in (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE):
        baseline = record.baseline.for_role(role)
        target = composition.targets[role]
        operation = composition.manifest.for_role(role)
        if (
            baseline.logical_id != target.logical_id
            or baseline.physical_binding != _physical_binding_for_target(target)
            or baseline.power_policy != operation.power_policy
        ):
            raise ExactRestoreCompositionError("operation_manifest_mismatch")
    return record


def public_record_status(record: ExactRestoreRecord | None) -> dict[str, object]:
    if record is None:
        return {"status": "idle"}
    return {
        "action_count": len(record.actions),
        "completed_action_count": len(record.completed_actions),
        "cycle": record.cycle.value,
        "error_code": record.error_code.value if record.error_code is not None else None,
        "inflight": record.inflight is not None,
        "operation_sha256": hashlib.sha256(record.operation_id.encode("ascii")).hexdigest(),
        "phase": record.phase.value,
        "status": "exact_restore_present",
    }


__all__ = [
    "ExactRestoreComposition",
    "ExactRestoreCompositionError",
    "ExactRestoreOperationManifest",
    "build_verified_baseline",
    "extract_manifest_pair",
    "load_bound_record",
    "load_locked_config",
    "load_operation_manifest",
    "prepare_operation",
    "public_record_status",
    "require_cross_workflow_quiescent",
    "safe_targets_for_manifest",
    "select_operation_targets",
    "stage_qualified_final_restore",
]
