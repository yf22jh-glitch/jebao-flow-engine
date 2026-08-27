"""Byte-exact temporary schedule staging with durable compensating recovery.

This core deliberately changes schedule slots only.  Timer authority, native linkage roles and
the five-to-ten minute observation are supplied by a higher-level attended workflow through the
``observe`` callback.  Original images are durable before the first write, every possible write
has a durable intent, and recovery always rewrites all 48 original slots before clearing the
journal.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.devices.linkage import LinkageSafetyInterlock
from jebao_flow.protocol.models import DeviceState, LinkageRole
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    decode_local_wavemaker_pro_slot_wire,
    get_local_wavemaker_pro_slot_wire,
    patch_local_wavemaker_pro_schedule_slot,
    validate_local_wavemaker_pro_schedule_image,
    validate_local_wavemaker_pro_slot_wire,
)

DeviceIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
]
OperationIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]
WireHex = Annotated[
    str,
    StringConstraints(
        min_length=LOCAL_WAVEMAKER_PRO_SLOT_SIZE * 2,
        max_length=LOCAL_WAVEMAKER_PRO_SLOT_SIZE * 2,
        pattern=r"^[0-9a-f]+$",
    ),
]
ImageHex = Annotated[
    str,
    StringConstraints(
        min_length=LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE * 2,
        max_length=LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE * 2,
        pattern=r"^[0-9a-f]+$",
    ),
]


class TemporaryScheduleErrorCode(StrEnum):
    JOURNAL_BUSY = "journal_busy"
    OPERATION_BUSY = "operation_busy"
    SAFETY_INTERLOCK = "safety_interlock"
    FORWARD_DEADLINE = "forward_deadline"
    UNSUPPORTED_DEVICE = "unsupported_device"
    BINDING_MISMATCH = "binding_mismatch"
    UNSAFE_INITIAL_STATE = "unsafe_initial_state"
    SNAPSHOT_FAILED = "snapshot_failed"
    SOURCE_CHANGED = "source_changed"
    STAGE_WRITE_FAILED = "stage_write_failed"
    STAGE_VERIFY_FAILED = "stage_verify_failed"
    OBSERVATION_FAILED = "observation_failed"
    OBSERVATION_TIMEOUT = "observation_timeout"
    OBSERVER_NOT_STOPPED = "observer_not_stopped"
    CONTROL_DISARM_UNVERIFIED = "control_disarm_unverified"
    MANUAL_RECOVERY_AUTHORITY_REQUIRED = "manual_recovery_authority_required"
    RESTORE_WRITE_FAILED = "restore_write_failed"
    RESTORE_VERIFY_FAILED = "restore_verify_failed"
    JOURNAL_FAILED = "journal_failed"
    RECOVERY_AUTHORITY_EXPIRED = "recovery_authority_expired"


class TemporaryScheduleError(RuntimeError):
    """A redacted error that never renders schedule bytes or private identifiers."""

    def __init__(self, code: TemporaryScheduleErrorCode) -> None:
        self.code = code
        super().__init__(f"temporary schedule transaction failed: {code.value}")


class TemporarySchedulePreflightError(TemporaryScheduleError):
    pass


class TemporaryScheduleBusyError(TemporaryScheduleError):
    pass


class TemporaryScheduleApplyError(TemporaryScheduleError):
    pass


class TemporaryScheduleRecoveryError(TemporaryScheduleError):
    pass


class TemporaryScheduleObserverUnstoppableError(TemporaryScheduleRecoveryError):
    """The nested observer did not finish compensation after losing write authority."""


class TemporaryScheduleRollbackUnsafeError(TemporaryScheduleRecoveryError):
    """TimerOFF plus independent linkage was not proven before schedule compensation."""


class TemporaryScheduleJournalClaimError(TemporaryScheduleError):
    pass


class TemporaryScheduleJournalError(TemporaryScheduleError):
    pass


class TemporarySchedulePhase(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    STAGED = "staged"
    OBSERVING = "observing"
    ROLLING_BACK = "rolling_back"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"


class TemporaryScheduleKind(StrEnum):
    FIELD_OBSERVATION = "field_observation"
    SENTINEL_QUALIFICATION = "sentinel_qualification"


class TemporaryScheduleProgressKind(StrEnum):
    """Redacted milestones emitted around the exact schedule transaction."""

    SNAPSHOT_STARTED = "snapshot_started"
    SNAPSHOT_COMPLETED = "snapshot_completed"
    STAGE_WRITE_STARTED = "stage_write_started"
    STAGE_VERIFIED = "stage_verified"
    RESTORE_STARTED = "restore_started"
    RESTORE_COMPLETED = "restore_completed"
    FAILED = "failed"


class TemporaryScheduleProgressEvent(BaseModel):
    """Privacy-safe transaction progress; it deliberately contains no device identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TemporaryScheduleProgressKind
    schedule_kind: TemporaryScheduleKind
    occurred_at: datetime
    completed_participants: int = Field(ge=0, le=2)
    error_code: TemporaryScheduleErrorCode | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("schedule progress timestamps must be timezone-aware")
        if self.kind is TemporaryScheduleProgressKind.FAILED:
            if self.error_code is None:
                raise ValueError("failed schedule progress requires an allow-listed code")
        elif self.error_code is not None:
            raise ValueError("only failed schedule progress may include an error code")
        if self.kind is TemporaryScheduleProgressKind.SNAPSHOT_STARTED:
            if self.completed_participants != 0:
                raise ValueError("snapshot-started progress cannot have completed participants")
        elif self.kind is TemporaryScheduleProgressKind.SNAPSHOT_COMPLETED:
            if self.completed_participants != 2:
                raise ValueError("snapshot-completed progress must cover both participants")
        return self


class ObservationCompletion(StrEnum):
    DISARM_VERIFIED = "disarm_verified"


class ScheduleSlotPatch(BaseModel):
    """One JSON-safe, validated nine-byte AutoTime replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: int = Field(ge=0, lt=LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    wire_hex: WireHex = Field(repr=False)
    behavior_neutral_unused_toggle: bool = False

    @model_validator(mode="after")
    def validate_wire(self) -> Self:
        validate_local_wavemaker_pro_slot_wire(self.wire_bytes, slot_index=self.slot)
        if self.behavior_neutral_unused_toggle and self.wire_bytes not in {
            LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
            LOCAL_WAVEMAKER_PRO_UNUSED_EE,
        }:
            raise ValueError("an unused-slot qualification must write an unused sentinel")
        return self

    @property
    def wire_bytes(self) -> bytes:
        return bytes.fromhex(self.wire_hex)

    @classmethod
    def from_wire(cls, slot: int, wire: bytes | bytearray | memoryview) -> Self:
        return cls(slot=slot, wire_hex=bytes(wire).hex())

    @classmethod
    def unused_sentinel_toggle(
        cls,
        slot: int,
        current_wire: bytes | bytearray | memoryview,
    ) -> Self:
        """Build an explicitly behavior-neutral 00↔EE qualification patch."""

        current = bytes(current_wire)
        if current == LOCAL_WAVEMAKER_PRO_UNUSED_ZERO:
            replacement = LOCAL_WAVEMAKER_PRO_UNUSED_EE
        elif current == LOCAL_WAVEMAKER_PRO_UNUSED_EE:
            replacement = LOCAL_WAVEMAKER_PRO_UNUSED_ZERO
        else:
            raise ValueError("sentinel qualification requires an unused source slot")
        return cls(
            slot=slot,
            wire_hex=replacement.hex(),
            behavior_neutral_unused_toggle=True,
        )


class DeviceSchedulePatch(BaseModel):
    """Selected schedule replacements for one logical controller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: DeviceIdentifier
    slots: tuple[ScheduleSlotPatch, ...] = Field(min_length=1, max_length=48)

    @model_validator(mode="after")
    def validate_slot_order(self) -> Self:
        indices = tuple(slot.slot for slot in self.slots)
        if indices != tuple(sorted(indices)) or len(set(indices)) != len(indices):
            raise ValueError("schedule slot patches must be unique and ordered")
        return self

    def as_wire_mapping(self) -> dict[int, bytes]:
        return {slot.slot: slot.wire_bytes for slot in self.slots}


class TemporaryScheduleSpec(BaseModel):
    """A bounded two-controller staging plan for an attended field observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationIdentifier = Field(default_factory=lambda: uuid4().hex)
    kind: TemporaryScheduleKind = TemporaryScheduleKind.FIELD_OBSERVATION
    device_patches: tuple[DeviceSchedulePatch, ...] = Field(min_length=2, max_length=2)
    forward_timeout_seconds: float = Field(default=60, gt=0, le=300)
    observation_timeout_seconds: float = Field(default=600, gt=0, le=900)
    observation_cancel_timeout_seconds: float = Field(default=180, ge=120, le=300)
    disarm_verify_timeout_seconds: float = Field(default=30, gt=0, le=120)
    restore_timeout_seconds: float = Field(default=90, gt=0, le=180)
    recovery_authority_seconds: float = Field(default=1800, ge=60, le=3600)

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        ids = tuple(patch.device_id for patch in self.device_patches)
        if len(set(ids)) != 2:
            raise ValueError("temporary schedule devices must be distinct")
        for patch in self.device_patches:
            if self.kind is TemporaryScheduleKind.FIELD_OBSERVATION:
                if tuple(slot.slot for slot in patch.slots) != tuple(
                    range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
                ) or any(slot.behavior_neutral_unused_toggle for slot in patch.slots):
                    raise ValueError(
                        "field observation must provide all 48 non-qualification slots"
                    )
            elif len(patch.slots) != 1 or not patch.slots[0].behavior_neutral_unused_toggle:
                raise ValueError(
                    "sentinel qualification must contain one behavior-neutral slot per device"
                )
        if self.recovery_authority_seconds <= (
            self.forward_timeout_seconds
            + self.observation_timeout_seconds
            + self.observation_cancel_timeout_seconds
            + self.disarm_verify_timeout_seconds
            + 8 * self.restore_timeout_seconds
        ):
            raise ValueError("recovery authority must outlive staging and observation")
        return self


class ScheduleImageSnapshot(BaseModel):
    """Privacy-preserving binding plus an exact, durable 432-byte image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: DeviceIdentifier
    physical_binding: PhysicalDeviceBinding
    image_hex: ImageHex = Field(repr=False)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_image(self) -> Self:
        image = self.image_bytes
        validate_local_wavemaker_pro_schedule_image(image)
        if not _constant_time_equal(hashlib.sha256(image).hexdigest(), self.image_sha256):
            raise ValueError("schedule snapshot digest does not match its image")
        return self

    @property
    def image_bytes(self) -> bytes:
        return bytes.fromhex(self.image_hex)

    @classmethod
    def from_image(
        cls,
        *,
        device_id: str,
        physical_binding: PhysicalDeviceBinding,
        image: bytes | bytearray | memoryview,
    ) -> Self:
        exact = validate_local_wavemaker_pro_schedule_image(bytes(image))
        return cls(
            device_id=device_id,
            physical_binding=physical_binding,
            image_hex=exact.hex(),
            image_sha256=hashlib.sha256(exact).hexdigest(),
        )


class TemporaryScheduleRecord(BaseModel):
    """Durable intent-before-write state, including a crash-safe completed tombstone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    mutation_scope: Literal["schedule_slots_only"] = "schedule_slots_only"
    operation_id: OperationIdentifier
    phase: TemporarySchedulePhase
    spec: TemporaryScheduleSpec
    snapshots: tuple[ScheduleImageSnapshot, ...] = Field(min_length=2, max_length=2)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    stage_write_intent_device_ids: tuple[DeviceIdentifier, ...] = ()
    staged_device_ids: tuple[DeviceIdentifier, ...] = ()
    restored_device_ids: tuple[DeviceIdentifier, ...] = ()
    error_code: TemporaryScheduleErrorCode | None = None

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("record operation_id must match its spec")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.created_at, self.updated_at, self.expires_at)
        ):
            raise ValueError("journal timestamps must be timezone-aware")
        if self.updated_at < self.created_at or self.expires_at <= self.created_at:
            raise ValueError("journal timestamps are not monotonic")
        expected_expiry = self.created_at + timedelta(seconds=self.spec.recovery_authority_seconds)
        if self.expires_at != expected_expiry:
            raise ValueError("journal expiry must match the approved recovery authority")

        expected = tuple(patch.device_id for patch in self.spec.device_patches)
        if tuple(snapshot.device_id for snapshot in self.snapshots) != expected:
            raise ValueError("snapshots must follow the staging device order")
        identities = tuple(
            physical_identity_key(snapshot.physical_binding) for snapshot in self.snapshots
        )
        if len(set(identities)) != 2:
            raise ValueError("schedule snapshots must bind distinct physical controllers")

        intents = self.stage_write_intent_device_ids
        staged = self.staged_device_ids
        restored = self.restored_device_ids
        if intents not in ((), expected[:1], expected):
            raise ValueError("stage write intents must be a device-order prefix")
        if staged != intents[: len(staged)]:
            raise ValueError("staged progress must be an intent prefix")
        restore_order = tuple(reversed(intents))
        if restored != restore_order[: len(restored)]:
            raise ValueError("restore progress must be a reverse-intent prefix")

        if self.phase is TemporarySchedulePhase.PREPARED and (intents or staged or restored):
            raise ValueError("a prepared journal cannot contain write progress")
        if self.phase in {
            TemporarySchedulePhase.STAGED,
            TemporarySchedulePhase.OBSERVING,
        }:
            if intents != expected or staged != expected or restored:
                raise ValueError("an observation-ready journal must verify both temporary images")
        if self.phase is TemporarySchedulePhase.RECOVERY_REQUIRED:
            if not intents or self.error_code is None:
                raise ValueError("recovery requires write intent and a typed error")
        elif self.error_code is not None:
            raise ValueError("only recovery-required journals can contain an error")
        if self.phase is TemporarySchedulePhase.COMPLETED and restored != tuple(reversed(intents)):
            raise ValueError("a completed journal must prove every intended restore")
        return self


class TemporaryScheduleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationIdentifier
    staged_images_verified: Literal[True] = True
    observation_completed: bool
    original_images_restored: Literal[True] = True
    completed_at: datetime


class TemporaryScheduleJournalStore(Protocol):
    def load(self) -> TemporaryScheduleRecord | None: ...

    def lease(self) -> AbstractContextManager[None]: ...

    def create(self, record: TemporaryScheduleRecord) -> None: ...

    def save(self, record: TemporaryScheduleRecord) -> None: ...

    def confirms_lease_successor(self, record: TemporaryScheduleRecord) -> bool: ...

    def clear(self) -> None: ...


Observation = Callable[[TemporaryScheduleRecord], Awaitable[ObservationCompletion]]
SnapshotAuthorizer = Callable[
    [TemporaryScheduleSpec, tuple[ScheduleImageSnapshot, ...]],
    None,
]
TemporaryScheduleProgressObserver = Callable[[TemporaryScheduleProgressEvent], None]


class TemporaryScheduleController:
    """Stage two exact schedule images, run one observer, then restore both images."""

    def __init__(
        self,
        devices: Mapping[str, JebaoDevice],
        store: TemporaryScheduleJournalStore,
        *,
        safety_interlock: LinkageSafetyInterlock,
        monotonic_clock: Callable[[], float] | None = None,
        snapshot_authorizer: SnapshotAuthorizer | None = None,
        progress_observer: TemporaryScheduleProgressObserver | None = None,
    ) -> None:
        self._devices = dict(devices)
        self._store = store
        self._safety_interlock = safety_interlock
        self._monotonic_clock = monotonic_clock
        self._snapshot_authorizer = snapshot_authorizer
        self._progress_observer = progress_observer
        self._run_lock = asyncio.Lock()
        self._active_operation_id: str | None = None
        self._safety_epoch: int | None = None
        self._forward_deadline: float | None = None
        self._orphaned_observations: dict[str, asyncio.Task[ObservationCompletion]] = {}

    async def run(
        self,
        spec: TemporaryScheduleSpec,
        *,
        observe: Observation | None = None,
    ) -> TemporaryScheduleResult:
        """Execute the bounded staging window and exact compensating restore."""

        try:
            spec = TemporaryScheduleSpec.model_validate(spec.model_dump(mode="python"))
        except Exception:
            raise TemporarySchedulePreflightError(
                TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
            ) from None
        if self._run_lock.locked():
            raise TemporaryScheduleBusyError(TemporaryScheduleErrorCode.OPERATION_BUSY)
        if spec.kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION and observe is not None:
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE)
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except TemporaryScheduleJournalClaimError:
                raise TemporaryScheduleBusyError(TemporaryScheduleErrorCode.JOURNAL_BUSY) from None
            try:
                return await self._run_owned(spec, observe)
            finally:
                lease.__exit__(None, None, None)

    async def recover_pending(self) -> bool:
        """Automatically recover only while the durable authority window is current."""

        return await self._recover_pending(manual=False)

    async def manual_recover(
        self,
        *,
        disarm_verified: bool = False,
        observer_stopped: bool = False,
    ) -> bool:
        """Explicitly recover even after automatic authority expires.

        The caller must provide attended, deployment-wide exclusion before invoking this method
        on a stale journal; physical binding checks still apply here.
        """

        return await self._recover_pending(
            manual=True,
            disarm_verified=disarm_verified,
            observer_stopped=observer_stopped,
        )

    async def _recover_pending(
        self,
        *,
        manual: bool,
        disarm_verified: bool = False,
        observer_stopped: bool = False,
    ) -> bool:
        """Recover without resuming observation, under the journal's exclusive lease."""

        if self._run_lock.locked():
            raise TemporaryScheduleBusyError(TemporaryScheduleErrorCode.OPERATION_BUSY)
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except TemporaryScheduleJournalClaimError:
                raise TemporaryScheduleBusyError(TemporaryScheduleErrorCode.JOURNAL_BUSY) from None
            try:
                record = self._load_exact(recovery=True)
                if record is None:
                    return False
                if (
                    record.phase is TemporarySchedulePhase.COMPLETED
                    or not record.stage_write_intent_device_ids
                ):
                    self._clear_exact()
                    return True
                if not manual and datetime.now(UTC) > record.expires_at:
                    raise TemporaryScheduleRecoveryError(
                        TemporaryScheduleErrorCode.RECOVERY_AUTHORITY_EXPIRED
                    )
                if (
                    not manual
                    and record.error_code is TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
                ):
                    raise TemporaryScheduleRecoveryError(
                        TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
                    )
                orphan = self._orphaned_observations.get(record.operation_id)
                if orphan is not None and not orphan.done():
                    raise TemporaryScheduleRecoveryError(
                        TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
                    )
                observation_may_have_armed_controls = (
                    record.phase is TemporarySchedulePhase.OBSERVING
                    or record.error_code
                    in {
                        TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED,
                        TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED,
                    }
                )
                if observation_may_have_armed_controls and (
                    not manual
                    or not disarm_verified
                    or (
                        record.error_code is TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
                        and not observer_stopped
                    )
                ):
                    raise TemporaryScheduleRecoveryError(
                        TemporaryScheduleErrorCode.MANUAL_RECOVERY_AUTHORITY_REQUIRED
                    )
                self._active_operation_id = record.operation_id
                self._validate_recovery_bindings(record)
                await self._rollback_uninterruptibly(record)
                return True
            finally:
                self._active_operation_id = None
                lease.__exit__(None, None, None)

    async def _run_owned(
        self,
        spec: TemporaryScheduleSpec,
        observe: Observation | None,
    ) -> TemporaryScheduleResult:
        if self._load_exact() is not None:
            raise TemporaryScheduleBusyError(TemporaryScheduleErrorCode.JOURNAL_BUSY)
        if not self._safety_interlock.permitted:
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.SAFETY_INTERLOCK)

        self._active_operation_id = spec.operation_id
        self._safety_epoch = self._safety_interlock.epoch
        self._forward_deadline = self._monotonic() + spec.forward_timeout_seconds
        record: TemporaryScheduleRecord | None = None
        journal_created = False
        observation_completed = False
        operation_error: BaseException | None = None
        try:
            self._emit_progress(
                TemporaryScheduleProgressKind.SNAPSHOT_STARTED,
                spec.kind,
                completed_participants=0,
            )
            snapshots = await self._capture_snapshots(spec)
            if self._snapshot_authorizer is not None:
                try:
                    self._snapshot_authorizer(spec, snapshots)
                except TemporaryScheduleError:
                    raise
                except Exception:
                    raise TemporarySchedulePreflightError(
                        TemporaryScheduleErrorCode.SNAPSHOT_FAILED
                    ) from None
            self._emit_progress(
                TemporaryScheduleProgressKind.SNAPSHOT_COMPLETED,
                spec.kind,
                completed_participants=2,
            )
            self._require_forward_write()
            started_at = datetime.now(UTC)
            record = TemporaryScheduleRecord(
                operation_id=spec.operation_id,
                phase=TemporarySchedulePhase.PREPARED,
                spec=spec,
                snapshots=snapshots,
                created_at=started_at,
                updated_at=started_at,
                expires_at=started_at + timedelta(seconds=spec.recovery_authority_seconds),
            )
            expected_images = self._expected_images(spec, snapshots)
            self._create_exact(record)
            journal_created = True
            for participant_index, patch in enumerate(spec.device_patches):
                self._emit_progress(
                    TemporaryScheduleProgressKind.STAGE_WRITE_STARTED,
                    spec.kind,
                    completed_participants=participant_index,
                )
                await self._revalidate_forward_source(
                    patch.device_id,
                    self._snapshot(record, patch.device_id),
                )
                self._require_forward_write()
                intent = record.model_copy(
                    update={
                        "phase": TemporarySchedulePhase.APPLYING,
                        "updated_at": self._record_now(record),
                        "stage_write_intent_device_ids": (
                            *record.stage_write_intent_device_ids,
                            patch.device_id,
                        ),
                    }
                )
                self._save_exact(intent)
                record = intent

                device = self._device(patch.device_id)
                try:
                    await asyncio.wait_for(
                        device.write_schedule_slots(
                            patch.as_wire_mapping(),
                            guard=self._forward_write_allowed,
                        ),
                        timeout=self._forward_remaining(),
                    )
                    self._require_forward_write()
                except TimeoutError as error:
                    operation_error = error
                    raise TemporaryScheduleApplyError(
                        TemporaryScheduleErrorCode.FORWARD_DEADLINE
                    ) from None
                except TemporaryScheduleError:
                    raise
                except BaseException as error:
                    operation_error = error
                    raise TemporaryScheduleApplyError(
                        TemporaryScheduleErrorCode.STAGE_WRITE_FAILED
                    ) from None
                try:
                    actual = await asyncio.wait_for(
                        device.read_schedule_image_explicit(),
                        timeout=self._forward_remaining(),
                    )
                    self._require_forward_write()
                except TimeoutError as error:
                    operation_error = error
                    raise TemporaryScheduleApplyError(
                        TemporaryScheduleErrorCode.FORWARD_DEADLINE
                    ) from None
                except TemporaryScheduleError:
                    raise
                except BaseException as error:
                    operation_error = error
                    raise TemporaryScheduleApplyError(
                        TemporaryScheduleErrorCode.STAGE_VERIFY_FAILED
                    ) from None
                if bytes(actual) != expected_images[patch.device_id]:
                    raise TemporaryScheduleApplyError(
                        TemporaryScheduleErrorCode.STAGE_VERIFY_FAILED
                    )
                staged = record.model_copy(
                    update={
                        "updated_at": self._record_now(record),
                        "staged_device_ids": (*record.staged_device_ids, patch.device_id),
                    }
                )
                self._save_exact(staged)
                record = staged
                self._emit_progress(
                    TemporaryScheduleProgressKind.STAGE_VERIFIED,
                    spec.kind,
                    completed_participants=participant_index + 1,
                )

            staged_record = record.model_copy(
                update={
                    "phase": TemporarySchedulePhase.STAGED,
                    "updated_at": self._record_now(record),
                }
            )
            self._save_exact(staged_record)
            record = staged_record
            if observe is not None:
                observing_record = record.model_copy(
                    update={
                        "phase": TemporarySchedulePhase.OBSERVING,
                        "updated_at": self._record_now(record),
                    }
                )
                self._save_exact(observing_record)
                record = observing_record
                try:
                    await self._observe_bounded(record, observe)
                    observation_completed = True
                    disarmed_record = record.model_copy(
                        update={
                            "phase": TemporarySchedulePhase.STAGED,
                            "updated_at": self._record_now(record),
                        }
                    )
                    self._save_exact(disarmed_record)
                    record = disarmed_record
                except asyncio.CancelledError as error:
                    operation_error = error
                    raise
                except TemporaryScheduleError as error:
                    operation_error = error
                    raise
                except BaseException as error:
                    operation_error = error
                    raise TemporaryScheduleRollbackUnsafeError(
                        TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
                    ) from None
        except BaseException as error:
            operation_error = operation_error or error
            if isinstance(error, TemporaryScheduleError):
                self._emit_progress(
                    TemporaryScheduleProgressKind.FAILED,
                    spec.kind,
                    completed_participants=(
                        len(record.staged_device_ids) if record is not None else 0
                    ),
                    error_code=error.code,
                    best_effort=True,
                )
            if not journal_created or record is None:
                raise
            if isinstance(
                error,
                TemporaryScheduleObserverUnstoppableError | TemporaryScheduleRollbackUnsafeError,
            ):
                code = (
                    TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
                    if isinstance(error, TemporaryScheduleObserverUnstoppableError)
                    else TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
                )
                self._persist_recovery_error(
                    self._load_exact(recovery=True) or record,
                    code,
                )
                raise error from None
            try:
                await self._rollback_uninterruptibly(record)
            except asyncio.CancelledError:
                raise
            except TemporaryScheduleRecoveryError:
                raise
            if isinstance(operation_error, asyncio.CancelledError):
                raise operation_error from None
            if isinstance(error, TemporaryScheduleError):
                raise error from None
            raise TemporaryScheduleApplyError(
                TemporaryScheduleErrorCode.STAGE_WRITE_FAILED
            ) from None
        else:
            await self._rollback_uninterruptibly(record)
            return TemporaryScheduleResult(
                operation_id=spec.operation_id,
                observation_completed=observation_completed,
                completed_at=datetime.now(UTC),
            )
        finally:
            self._active_operation_id = None
            self._safety_epoch = None
            self._forward_deadline = None

    async def _capture_snapshots(
        self,
        spec: TemporaryScheduleSpec,
    ) -> tuple[ScheduleImageSnapshot, ...]:
        devices = tuple(self._device(patch.device_id) for patch in spec.device_patches)
        for patch, device in zip(spec.device_patches, devices, strict=True):
            if device.device_id != patch.device_id:
                raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.BINDING_MISMATCH)
            self._validate_device(device)
        bindings = tuple(device.physical_binding for device in devices)
        if any(binding is None for binding in bindings):
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSUPPORTED_DEVICE)
        concrete_bindings = tuple(binding for binding in bindings if binding is not None)
        if len({physical_identity_key(binding) for binding in concrete_bindings}) != 2:
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.BINDING_MISMATCH)
        try:
            observed = await asyncio.wait_for(
                asyncio.gather(
                    *(self._read_safe_schedule_source_on_new_session(device) for device in devices)
                ),
                timeout=self._forward_remaining(),
            )
            for state, _ in observed:
                self._validate_safe_initial_state(state)
            images = tuple(image for _, image in observed)
            return tuple(
                ScheduleImageSnapshot.from_image(
                    device_id=device.device_id,
                    physical_binding=binding,
                    image=image,
                )
                for device, binding, image in zip(
                    devices,
                    concrete_bindings,
                    images,
                    strict=True,
                )
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.FORWARD_DEADLINE) from None
        except TemporaryScheduleError:
            raise
        except BaseException:
            raise TemporarySchedulePreflightError(
                TemporaryScheduleErrorCode.SNAPSHOT_FAILED
            ) from None

    async def _observe_bounded(
        self,
        record: TemporaryScheduleRecord,
        observe: Observation,
    ) -> None:
        observation = asyncio.create_task(observe(record))
        interlock = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        try:
            try:
                done, _ = await asyncio.wait(
                    {observation, interlock},
                    timeout=record.spec.observation_timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                self._safety_interlock.trip()
                await self._finish_observation_uninterruptibly(record, observation)
                raise

            if interlock in done or not self._safety_interlock.permitted:
                cancellation_received = await self._finish_observation_uninterruptibly(
                    record,
                    observation,
                )
                if cancellation_received:
                    raise asyncio.CancelledError
                raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.SAFETY_INTERLOCK)

            if observation in done:
                cancellation_received = await self._finish_observation_uninterruptibly(
                    record,
                    observation,
                )
                if cancellation_received:
                    raise asyncio.CancelledError
                return

            # A timed-out observer loses all forward-write authority before cancellation.  Known
            # observers must release their own role/control transaction while handling cancel.
            self._safety_interlock.trip()
            cancellation_received = await self._finish_observation_uninterruptibly(
                record,
                observation,
            )
            if cancellation_received:
                raise asyncio.CancelledError
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.OBSERVATION_TIMEOUT)
        finally:
            if not interlock.done():
                interlock.cancel()
            if interlock.done():
                _consume_wait_result(interlock)
            else:
                interlock.add_done_callback(_consume_wait_result)

    async def _finish_observation_uninterruptibly(
        self,
        record: TemporaryScheduleRecord,
        observation: asyncio.Task[ObservationCompletion],
    ) -> bool:
        """Stop the observer and prove controls safe as one shielded compensation.

        The boolean reports cancellation received while compensation was running.  A failed
        compensation always takes precedence so the caller retains the recovery journal instead
        of treating cancellation as authority to restore an armed schedule.
        """

        compensation = asyncio.create_task(
            self._finish_observation(
                record,
                observation,
            )
        )
        cancellation_received = False
        while not compensation.done():
            try:
                await asyncio.shield(compensation)
            except asyncio.CancelledError:
                cancellation_received = True
        compensation.result()
        return cancellation_received

    async def _finish_observation(
        self,
        record: TemporaryScheduleRecord,
        observation: asyncio.Task[ObservationCompletion],
    ) -> None:
        await self._cancel_observation(
            observation,
            timeout_seconds=record.spec.observation_cancel_timeout_seconds,
            operation_id=record.operation_id,
        )
        await self._verify_disarmed_controls(record)

    async def _cancel_observation(
        self,
        observation: asyncio.Task[ObservationCompletion],
        *,
        timeout_seconds: float,
        operation_id: str,
    ) -> None:
        if observation.done():
            try:
                completion = await observation
            except asyncio.CancelledError:
                raise TemporaryScheduleRollbackUnsafeError(
                    TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
                ) from None
            except Exception:
                raise TemporaryScheduleRollbackUnsafeError(
                    TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
                ) from None
            self._require_disarm_completion(completion)
            return
        observation.cancel()
        done, _ = await asyncio.wait({observation}, timeout=timeout_seconds)
        if observation not in done:
            self._orphaned_observations[operation_id] = observation
            observation.add_done_callback(
                lambda task: self._finish_orphaned_observation(operation_id, task)
            )
            raise TemporaryScheduleObserverUnstoppableError(
                TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
            )
        try:
            completion = await observation
        except asyncio.CancelledError:
            raise TemporaryScheduleRollbackUnsafeError(
                TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
            ) from None
        except Exception:
            raise TemporaryScheduleRollbackUnsafeError(
                TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
            ) from None
        self._require_disarm_completion(completion)

    @staticmethod
    def _require_disarm_completion(completion: ObservationCompletion) -> None:
        if completion is not ObservationCompletion.DISARM_VERIFIED:
            raise TemporaryScheduleRollbackUnsafeError(
                TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
            )

    def _finish_orphaned_observation(
        self,
        operation_id: str,
        task: asyncio.Task[ObservationCompletion],
    ) -> None:
        if self._orphaned_observations.get(operation_id) is task:
            self._orphaned_observations.pop(operation_id, None)
        _consume_observation_result(task)

    def _expected_images(
        self,
        spec: TemporaryScheduleSpec,
        snapshots: tuple[ScheduleImageSnapshot, ...],
    ) -> dict[str, bytes]:
        source = {snapshot.device_id: snapshot.image_bytes for snapshot in snapshots}
        expected: dict[str, bytes] = {}
        field_evidence: list[tuple[tuple[tuple[str, str, str], ...], tuple[int, int]]] = []
        for patch in spec.device_patches:
            image = source[patch.device_id]
            for slot in patch.slots:
                current_wire = get_local_wavemaker_pro_slot_wire(image, slot.slot)
                if slot.behavior_neutral_unused_toggle and (
                    current_wire
                    not in {
                        LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
                        LOCAL_WAVEMAKER_PRO_UNUSED_EE,
                    }
                    or current_wire == slot.wire_bytes
                ):
                    raise TemporarySchedulePreflightError(
                        TemporaryScheduleErrorCode.SNAPSHOT_FAILED
                    )
                entry = decode_local_wavemaker_pro_slot_wire(
                    slot.wire_bytes,
                    slot_index=slot.slot,
                )
                if entry is not None:
                    flow = entry.parameters["flow"]
                    capabilities = self._device(patch.device_id).capabilities
                    limits = capabilities.power_limits
                    # Flow zero is the one intentional feed/stop exception to the normal
                    # operating minimum. A non-zero feed value still controls physical output and
                    # must not bypass the same max/step gate enforced by the final LAN adapter.
                    feed_stop = entry.mode == "feed" and flow == 0
                    if not feed_stop and (
                        not limits.min_power <= flow <= limits.max_power
                        or flow % capabilities.power_step
                    ):
                        raise TemporarySchedulePreflightError(
                            TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
                        )
                image = patch_local_wavemaker_pro_schedule_slot(
                    image,
                    slot.slot,
                    slot.wire_bytes,
                )
            expected[patch.device_id] = image
            if spec.kind is TemporaryScheduleKind.FIELD_OBSERVATION:
                field_evidence.append(
                    self._validate_field_schedule_image(
                        patch.device_id,
                        image,
                    )
                )
        if field_evidence:
            master_topology, master_flows = field_evidence[0]
            slave_topology, slave_flows = field_evidence[1]
            if (
                master_topology != slave_topology
                or slave_flows[0] == slave_flows[1]
                or master_flows[1] == slave_flows[1]
            ):
                raise TemporarySchedulePreflightError(
                    TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
                )
        return expected

    async def _revalidate_forward_source(
        self,
        device_id: str,
        snapshot: ScheduleImageSnapshot,
    ) -> None:
        self._require_forward_write()
        device = self._device(device_id)
        try:
            state, image = await asyncio.wait_for(
                self._read_safe_schedule_source_on_new_session(device),
                timeout=self._forward_remaining(),
            )
            self._validate_safe_initial_state(state)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.FORWARD_DEADLINE) from None
        except TemporaryScheduleError:
            raise
        except BaseException:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.SOURCE_CHANGED) from None
        if bytes(image) != snapshot.image_bytes:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.SOURCE_CHANGED)
        self._require_forward_write()

    @staticmethod
    async def _read_safe_schedule_source_on_new_session(
        device: JebaoDevice,
    ) -> tuple[DeviceState, bytes]:
        """Read control gates and schedule bytes after discarding all queued stream state."""

        await device.disconnect()
        await device.connect()
        state = await device.get_state()
        image = await device.read_schedule_image_explicit()
        return state, bytes(image)

    async def _verify_disarmed_controls(self, record: TemporaryScheduleRecord) -> None:
        reads = tuple(
            asyncio.create_task(
                self._read_disarmed_state_on_new_session(self._device(snapshot.device_id))
            )
            for snapshot in record.snapshots
        )
        try:
            _, pending = await asyncio.wait(
                reads,
                timeout=record.spec.disarm_verify_timeout_seconds,
            )
            if pending:
                for task in reads:
                    if not task.done():
                        task.cancel()
                        task.add_done_callback(_consume_state_read_result)
                    else:
                        _consume_state_read_result(task)
                raise TemporaryScheduleRollbackUnsafeError(
                    TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
                )
            states: list[DeviceState] = []
            state_read_failed = False
            for task in reads:
                try:
                    states.append(task.result())
                except BaseException:
                    state_read_failed = True
            if state_read_failed:
                raise TemporaryScheduleRollbackUnsafeError(
                    TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
                )
            for state in states:
                self._validate_safe_initial_state(state)
        except asyncio.CancelledError:
            for task in reads:
                if not task.done():
                    task.cancel()
                    task.add_done_callback(_consume_state_read_result)
                else:
                    _consume_state_read_result(task)
            raise
        except TemporaryScheduleRollbackUnsafeError:
            raise
        except BaseException:
            raise TemporaryScheduleRollbackUnsafeError(
                TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
            ) from None

    @staticmethod
    async def _read_disarmed_state_on_new_session(device: JebaoDevice) -> DeviceState:
        """Discard callback-owned stream state before accepting TimerOFF/linkage evidence."""

        await device.disconnect()
        await device.connect()
        return await device.get_state()

    def _validate_field_schedule_image(
        self,
        device_id: str,
        image: bytes,
    ) -> tuple[tuple[tuple[str, str, str], ...], tuple[int, int]]:
        entries = tuple(
            entry
            for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
            if (
                entry := decode_local_wavemaker_pro_slot_wire(
                    get_local_wavemaker_pro_slot_wire(image, index),
                    slot_index=index,
                )
            )
            is not None
        )
        if len(entries) != 2:
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE)
        ordered = tuple(sorted(entries, key=lambda entry: _wall_minutes(entry.start)))
        if ordered[0].mode == ordered[1].mode:
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE)
        cursor = 0
        capabilities = self._device(device_id).capabilities
        limits = capabilities.power_limits
        for entry in ordered:
            start = _wall_minutes(entry.start)
            end = _wall_minutes(entry.end)
            if start != cursor or end <= start:
                raise TemporarySchedulePreflightError(
                    TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
                )
            cursor = end
            flow = entry.parameters["flow"]
            feed_stop = entry.mode == "feed" and flow == 0
            if not feed_stop and (
                not limits.min_power <= flow <= limits.max_power
                or flow % capabilities.power_step
            ):
                raise TemporarySchedulePreflightError(
                    TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
                )
        # Local Wavemaker Pro app schedules observed in the field terminate at 23:59. Keep
        # accepting the protocol-level 24:00 form for decoded fixtures, but do not require it
        # for a field image generated with the controller's real-device convention.
        if cursor not in {23 * 60 + 59, 24 * 60}:
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE)
        topology = tuple((entry.start, entry.end, entry.mode) for entry in ordered)
        flows = tuple(entry.parameters["flow"] for entry in ordered)
        return topology, (flows[0], flows[1])

    async def _rollback_uninterruptibly(self, record: TemporaryScheduleRecord) -> None:
        task = asyncio.create_task(self._rollback(record))
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        try:
            task.result()
        except TemporaryScheduleError as error:
            self._emit_progress(
                TemporaryScheduleProgressKind.FAILED,
                record.spec.kind,
                completed_participants=len(record.restored_device_ids),
                error_code=error.code,
                best_effort=True,
            )
            raise
        if cancellation_received:
            raise asyncio.CancelledError

    async def _rollback(self, caller: TemporaryScheduleRecord) -> None:
        self._emit_progress(
            TemporaryScheduleProgressKind.RESTORE_STARTED,
            caller.spec.kind,
            completed_participants=len(caller.restored_device_ids),
            best_effort=True,
        )
        durable = self._load_exact(recovery=True)
        if (
            durable is None
            or durable.operation_id != caller.operation_id
            or durable.spec != caller.spec
            or durable.snapshots != caller.snapshots
        ):
            raise TemporaryScheduleRecoveryError(TemporaryScheduleErrorCode.JOURNAL_FAILED)
        self._validate_recovery_bindings(durable)
        if durable.stage_write_intent_device_ids:
            try:
                await self._verify_disarmed_controls(durable)
            except TemporaryScheduleRollbackUnsafeError:
                self._persist_recovery_error(
                    durable,
                    TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED,
                )
                raise
        rolling = durable.model_copy(
            update={
                "phase": TemporarySchedulePhase.ROLLING_BACK,
                "updated_at": self._record_now(durable),
                "error_code": None,
            }
        )
        self._save_exact(rolling, recovery=True)
        record = rolling

        for device_id in reversed(record.stage_write_intent_device_ids):
            snapshot = self._snapshot(record, device_id)
            device = self._device(device_id)
            try:
                await asyncio.wait_for(
                    device.restore_schedule_image(
                        snapshot.image_bytes,
                        guard=lambda operation_id=record.operation_id: (
                            self._active_operation_id == operation_id
                        ),
                    ),
                    timeout=record.spec.restore_timeout_seconds,
                )
            except BaseException:
                try:
                    actual = await asyncio.wait_for(
                        device.read_schedule_image_explicit(),
                        timeout=record.spec.restore_timeout_seconds,
                    )
                except BaseException:
                    self._persist_recovery_error(
                        record,
                        TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED,
                    )
                    raise TemporaryScheduleRecoveryError(
                        TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED
                    ) from None
                if bytes(actual) != snapshot.image_bytes:
                    self._persist_recovery_error(
                        record,
                        TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED,
                    )
                    raise TemporaryScheduleRecoveryError(
                        TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED
                    ) from None

            try:
                actual = await asyncio.wait_for(
                    device.read_schedule_image_explicit(),
                    timeout=record.spec.restore_timeout_seconds,
                )
            except BaseException:
                self._persist_recovery_error(
                    record,
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED,
                )
                raise TemporaryScheduleRecoveryError(
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED
                ) from None
            if bytes(actual) != snapshot.image_bytes:
                self._persist_recovery_error(
                    record,
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED,
                )
                raise TemporaryScheduleRecoveryError(
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED
                )
            if device_id not in record.restored_device_ids:
                restored = record.model_copy(
                    update={
                        "updated_at": self._record_now(record),
                        "restored_device_ids": (*record.restored_device_ids, device_id),
                    }
                )
                self._save_exact(restored, recovery=True)
                record = restored
                if len(record.restored_device_ids) < 2:
                    self._emit_progress(
                        TemporaryScheduleProgressKind.RESTORE_COMPLETED,
                        record.spec.kind,
                        completed_participants=len(record.restored_device_ids),
                        best_effort=True,
                    )
        await self._verify_all_original_images(record)
        completed = record.model_copy(
            update={
                "phase": TemporarySchedulePhase.COMPLETED,
                "updated_at": self._record_now(record),
                "error_code": None,
            }
        )
        self._save_exact(completed, recovery=True)
        self._emit_progress(
            TemporaryScheduleProgressKind.RESTORE_COMPLETED,
            record.spec.kind,
            completed_participants=2,
            best_effort=True,
        )
        self._clear_exact()

    def _emit_progress(
        self,
        kind: TemporaryScheduleProgressKind,
        schedule_kind: TemporaryScheduleKind,
        *,
        completed_participants: int,
        error_code: TemporaryScheduleErrorCode | None = None,
        best_effort: bool = False,
    ) -> None:
        observer = self._progress_observer
        if observer is None:
            return
        event = TemporaryScheduleProgressEvent(
            kind=kind,
            schedule_kind=schedule_kind,
            occurred_at=datetime.now(UTC),
            completed_participants=completed_participants,
            error_code=error_code,
        )
        if not best_effort:
            try:
                observer(event)
            except Exception:
                raise TemporaryScheduleJournalError(
                    TemporaryScheduleErrorCode.JOURNAL_FAILED
                ) from None
            return
        try:
            observer(event)
        except Exception:
            # Exact rollback must continue even when diagnostic persistence is unavailable.
            pass

    async def _verify_all_original_images(self, record: TemporaryScheduleRecord) -> None:
        for device_id in record.stage_write_intent_device_ids:
            snapshot = self._snapshot(record, device_id)
            try:
                actual = await asyncio.wait_for(
                    self._device(device_id).read_schedule_image_explicit(),
                    timeout=record.spec.restore_timeout_seconds,
                )
            except BaseException:
                self._persist_recovery_error(
                    record,
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED,
                )
                raise TemporaryScheduleRecoveryError(
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED
                ) from None
            if bytes(actual) != snapshot.image_bytes:
                self._persist_recovery_error(
                    record,
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED,
                )
                raise TemporaryScheduleRecoveryError(
                    TemporaryScheduleErrorCode.RESTORE_VERIFY_FAILED
                )

    def _persist_recovery_error(
        self,
        record: TemporaryScheduleRecord,
        code: TemporaryScheduleErrorCode,
    ) -> None:
        recovery = record.model_copy(
            update={
                "phase": TemporarySchedulePhase.RECOVERY_REQUIRED,
                "updated_at": self._record_now(record),
                "error_code": code,
            }
        )
        try:
            self._save_exact(recovery, recovery=True)
        except TemporaryScheduleRecoveryError:
            pass

    def _validate_recovery_bindings(self, record: TemporaryScheduleRecord) -> None:
        for snapshot in record.snapshots:
            device = self._device(snapshot.device_id)
            if (
                device.device_id != snapshot.device_id
                or device.physical_binding != snapshot.physical_binding
            ):
                raise TemporaryScheduleRecoveryError(TemporaryScheduleErrorCode.BINDING_MISMATCH)

    @staticmethod
    def _snapshot(
        record: TemporaryScheduleRecord,
        device_id: str,
    ) -> ScheduleImageSnapshot:
        return next(snapshot for snapshot in record.snapshots if snapshot.device_id == device_id)

    def _validate_device(self, device: JebaoDevice) -> None:
        binding = device.physical_binding
        if (
            device.capabilities.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            or binding is None
            or binding.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
        ):
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSUPPORTED_DEVICE)

    @staticmethod
    def _validate_safe_initial_state(state: DeviceState) -> None:
        if (
            state.online is not True
            or state.error is not None
            or state.timer_enabled is not False
            or state.linkage is not LinkageRole.INDEPENDENT
        ):
            raise TemporarySchedulePreflightError(TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE)

    def _device(self, device_id: str) -> JebaoDevice:
        try:
            return self._devices[device_id]
        except KeyError:
            raise TemporarySchedulePreflightError(
                TemporaryScheduleErrorCode.UNSUPPORTED_DEVICE
            ) from None

    def _forward_write_allowed(self) -> bool:
        deadline = self._forward_deadline
        return (
            self._active_operation_id is not None
            and self._safety_epoch is not None
            and self._safety_interlock.permitted
            and self._safety_interlock.epoch == self._safety_epoch
            and deadline is not None
            and self._monotonic() < deadline
        )

    def _require_forward_write(self) -> None:
        if not self._safety_interlock.permitted or (
            self._safety_epoch is not None and self._safety_interlock.epoch != self._safety_epoch
        ):
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.SAFETY_INTERLOCK)
        if not self._forward_write_allowed():
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.FORWARD_DEADLINE)

    def _forward_remaining(self) -> float:
        self._require_forward_write()
        deadline = self._forward_deadline
        if deadline is None:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.FORWARD_DEADLINE)
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.FORWARD_DEADLINE)
        return remaining

    def _create_exact(self, record: TemporaryScheduleRecord) -> None:
        try:
            self._store.create(record)
        except Exception:
            raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.JOURNAL_FAILED) from None

    def _save_exact(
        self,
        record: TemporaryScheduleRecord,
        *,
        recovery: bool = False,
    ) -> None:
        try:
            self._store.save(record)
        except Exception:
            error_type = TemporaryScheduleRecoveryError if recovery else TemporaryScheduleApplyError
            raise error_type(TemporaryScheduleErrorCode.JOURNAL_FAILED) from None

    def _clear_exact(self) -> None:
        try:
            self._store.clear()
            remaining = self._load_exact(recovery=True)
        except Exception:
            raise TemporaryScheduleRecoveryError(
                TemporaryScheduleErrorCode.JOURNAL_FAILED
            ) from None
        if remaining is not None:
            raise TemporaryScheduleRecoveryError(TemporaryScheduleErrorCode.JOURNAL_FAILED)

    def _load_exact(self, *, recovery: bool = False) -> TemporaryScheduleRecord | None:
        try:
            record = self._store.load()
            if record is None:
                return None
            return TemporaryScheduleRecord.model_validate(record.model_dump(mode="python"))
        except Exception:
            error_type = TemporaryScheduleRecoveryError if recovery else TemporaryScheduleApplyError
            raise error_type(TemporaryScheduleErrorCode.JOURNAL_FAILED) from None

    def _record_now(self, record: TemporaryScheduleRecord) -> datetime:
        now = datetime.now(UTC)
        if now < record.updated_at:
            return record.updated_at
        return now

    def _monotonic(self) -> float:
        if self._monotonic_clock is not None:
            return self._monotonic_clock()
        return asyncio.get_running_loop().time()


def temporary_schedule_confirmation_token(
    spec: TemporaryScheduleSpec,
    snapshots: tuple[ScheduleImageSnapshot, ...],
) -> str:
    """Hash one plan and its exact snapshots without rendering either image."""

    canonical = {
        "version": 1,
        "spec": spec.model_dump(mode="json"),
        "snapshot_digests": [
            {
                "device_id": snapshot.device_id,
                "physical_binding": snapshot.physical_binding.model_dump(mode="json"),
                "image_sha256": snapshot.image_sha256,
            }
            for snapshot in snapshots
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def behavior_neutral_unused_slot_patch(
    image: bytes | bytearray | memoryview,
    *,
    preferred_slot: int | None = None,
) -> ScheduleSlotPatch:
    """Select one unused slot and build a fresh-image-bound 00↔EE qualification patch.

    The transaction rechecks the source slot against its own fresh snapshot, so using this
    helper cannot silently turn an active slot into an unused one if the schedule changed between
    planning and execution.
    """

    exact = validate_local_wavemaker_pro_schedule_image(bytes(image))
    candidates = (
        (preferred_slot,)
        if preferred_slot is not None
        else tuple(range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT))
    )
    for slot in candidates:
        current = get_local_wavemaker_pro_slot_wire(exact, slot)
        if current in {LOCAL_WAVEMAKER_PRO_UNUSED_ZERO, LOCAL_WAVEMAKER_PRO_UNUSED_EE}:
            return ScheduleSlotPatch.unused_sentinel_toggle(slot, current)
    raise ValueError("schedule image has no unused slot for behavior-neutral qualification")


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def _wall_minutes(value: str) -> int:
    hour_text, minute_text = value.split(":", maxsplit=1)
    return int(hour_text) * 60 + int(minute_text)


def _consume_observation_result(task: asyncio.Task[ObservationCompletion]) -> None:
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


def _consume_wait_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


def _consume_state_read_result(task: asyncio.Task[DeviceState]) -> None:
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


__all__ = [
    "DeviceSchedulePatch",
    "Observation",
    "ObservationCompletion",
    "ScheduleImageSnapshot",
    "ScheduleSlotPatch",
    "SnapshotAuthorizer",
    "TemporaryScheduleApplyError",
    "TemporaryScheduleBusyError",
    "TemporaryScheduleController",
    "TemporaryScheduleError",
    "TemporaryScheduleErrorCode",
    "TemporaryScheduleJournalClaimError",
    "TemporaryScheduleJournalError",
    "TemporaryScheduleJournalStore",
    "TemporaryScheduleKind",
    "TemporaryScheduleObserverUnstoppableError",
    "TemporarySchedulePhase",
    "TemporarySchedulePreflightError",
    "TemporaryScheduleRecord",
    "TemporaryScheduleRecoveryError",
    "TemporaryScheduleRollbackUnsafeError",
    "TemporaryScheduleResult",
    "TemporaryScheduleSpec",
    "behavior_neutral_unused_slot_patch",
    "temporary_schedule_confirmation_token",
]
