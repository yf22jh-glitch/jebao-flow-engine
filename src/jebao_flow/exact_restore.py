"""Crash-safe, at-most-once orchestration for an attended exact baseline restore.

This module contains no discovery or LAN implementation.  It owns only the deterministic
restore plan, durable intent-before-write transitions, and verification rules.  A composition
edge supplies fresh explicit observations and a narrowly gated physical adapter.
"""

from __future__ import annotations

import hashlib
import json
import time
from asyncio import CancelledError
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from jebao_flow.physical_identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    decode_local_wavemaker_pro_slot_wire,
    get_local_wavemaker_pro_slot_wire,
    patch_local_wavemaker_pro_schedule_slot,
    validate_local_wavemaker_pro_schedule_image,
)

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
OperationId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def system_boot_identity_sha256() -> str:
    """Return a stable hash for the current Linux boot, or fail closed."""

    try:
        with open("/proc/sys/kernel/random/boot_id", "rb") as boot_id_file:
            boot_identity = boot_id_file.read(128).strip()
    except OSError as error:
        raise RuntimeError("system boot identity is unavailable") from error
    if not boot_identity:
        raise RuntimeError("system boot identity is empty")
    return hashlib.sha256(boot_identity).hexdigest()


def system_boottime_ns() -> int:
    """Return suspend-inclusive Linux boot time, or fail closed when unavailable."""

    try:
        clock_id = time.CLOCK_BOOTTIME
        value = time.clock_gettime_ns(clock_id)
    except (AttributeError, OSError) as error:
        raise RuntimeError("suspend-inclusive boot clock is unavailable") from error
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("suspend-inclusive boot clock returned an invalid value")
    return value


# Compatibility for any in-module/downstream diagnostic import that predates the public helper.
_system_boot_identity_sha256 = system_boot_identity_sha256


def _timedelta_ns(value: timedelta) -> int:
    return (value.days * 86_400 + value.seconds) * 1_000_000_000 + value.microseconds * 1_000


class ExactRestoreErrorCode(StrEnum):
    JOURNAL = "journal"
    AUTHORITY = "authority"
    EXPIRED = "expired"
    BINDING = "binding"
    SAFETY_INTERLOCK = "safety_interlock"
    INVALID_BASELINE = "invalid_baseline"
    BASELINE_EXPIRED = "baseline_expired_before_first_write"
    UNSAFE_STATE = "unsafe_state"
    UNCERTAIN_WRITE = "uncertain_write"
    VERIFY_MISMATCH = "verify_mismatch"
    DEVICE_IO = "device_io"


class ExactRestoreError(RuntimeError):
    """Privacy-safe exact-restore failure."""

    def __init__(
        self,
        code: ExactRestoreErrorCode,
        *,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.diagnostic = dict(diagnostic or {})
        super().__init__(f"exact restore failed: {code.value}")


class ExactRestorePreflightError(ExactRestoreError):
    pass


class ExactRestoreRecoveryRequired(ExactRestoreError):
    pass


class ExactRestoreRole(StrEnum):
    MASTER = "master"
    SLAVE = "slave"


class ExactRestoreCycle(StrEnum):
    BASELINE_RESTORE = "baseline_restore"
    SENTINEL_QUALIFICATION = "sentinel_qualification"


class ExactRestorePhase(StrEnum):
    PREPARED = "prepared"
    ARMED = "armed"
    RESTORING = "restoring"
    AWAITING_FINAL_VERIFY = "awaiting_final_verify"
    FINAL_VERIFIED = "final_verified"
    RECOVERY_REQUIRED = "recovery_required"


class ExactRestoreActionKind(StrEnum):
    SAFE_FALLBACK = "safe_fallback"
    QUALIFY_SENTINEL = "qualify_sentinel"
    RESTORE_SCHEDULE = "restore_schedule"
    RESTORE_OUTER = "restore_outer"


class ExactRestoreActionOutcome(StrEnum):
    ALREADY_SATISFIED = "already_satisfied"
    WRITTEN_VERIFIED = "written_verified"
    VERIFIED_AFTER_UNCERTAIN = "verified_after_uncertain"


class ExactRestoreAuthorityScope(StrEnum):
    EXACT_BASELINE_ONLY = "exact_baseline_only"
    BOOTSTRAP_QUALIFICATION = "bootstrap_qualification"


class OuterControlSnapshot(BaseModel):
    """The six allow-listed controls restored by v1; Auto* is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    timer_enabled: bool
    linkage: LinkageRole
    mode: str = Field(min_length=1)
    power: int = Field(ge=0, le=100)
    frequency: int = Field(ge=0, le=100)


class ExactScheduleImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_hex: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{864}$")] = Field(repr=False)
    image_sha256: Sha256Digest

    @model_validator(mode="after")
    def validate_exact_image(self) -> Self:
        image = validate_local_wavemaker_pro_schedule_image(self.image_bytes)
        if hashlib.sha256(image).hexdigest() != self.image_sha256:
            raise ValueError("schedule image digest mismatch")
        return self

    @property
    def image_bytes(self) -> bytes:
        return bytes.fromhex(self.image_hex)

    @classmethod
    def from_bytes(cls, image: bytes | bytearray | memoryview) -> Self:
        exact = validate_local_wavemaker_pro_schedule_image(bytes(image))
        return cls(
            image_hex=exact.hex(),
            image_sha256=hashlib.sha256(exact).hexdigest(),
        )


class RestorePowerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_power: int = Field(ge=0, le=100)
    max_power: int = Field(ge=0, le=100)
    power_step: int = Field(ge=1, le=100)
    attended_max_power: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.min_power > self.max_power:
            raise ValueError("minimum power exceeds maximum power")
        if not self.min_power <= self.attended_max_power <= self.max_power:
            raise ValueError("attended maximum must be inside configured power limits")
        return self

    def permits(self, power: int, *, feed_stop: bool = False) -> bool:
        if feed_stop and power == 0:
            return True
        return self.min_power <= power <= self.attended_max_power and power % self.power_step == 0


class ExactRestoreVerificationPolicy(BaseModel):
    """Manifest-approved host-time bounds for explicit restore observations."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    max_observation_age_seconds: float = Field(gt=0)
    max_final_pair_gap_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_pair_within_age(self) -> Self:
        if self.max_final_pair_gap_seconds > self.max_observation_age_seconds:
            raise ValueError("final pair gap cannot exceed the observation age bound")
        return self

    @property
    def policy_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class ExactRestoreDeviceBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ExactRestoreRole
    logical_id: str = Field(min_length=1)
    physical_binding: PhysicalDeviceBinding
    outer: OuterControlSnapshot
    schedule: ExactScheduleImage
    power_policy: RestorePowerPolicy
    raw_frame_sha256: Sha256Digest

    @model_validator(mode="after")
    def validate_v1_admission(self) -> Self:
        if (
            self.outer.enabled is not True
            or self.outer.timer_enabled is not True
            or self.outer.linkage is not LinkageRole.INDEPENDENT
        ):
            raise ValueError("v1 baseline must be ON, TimerON and independent")
        if not self.power_policy.permits(self.outer.power):
            raise ValueError("baseline manual power is outside the attended safe policy")
        _validate_schedule_power_policy(self.schedule.image_bytes, self.power_policy)
        active_non_feed_flows = [
            entry.parameters["flow"]
            for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
            if (
                entry := decode_local_wavemaker_pro_slot_wire(
                    get_local_wavemaker_pro_slot_wire(self.schedule.image_bytes, index),
                    slot_index=index,
                )
            )
            is not None
            and entry.mode != "feed"
        ]
        if not active_non_feed_flows:
            raise ValueError("baseline requires an active non-feed schedule flow ceiling")
        if self.outer.power > max(active_non_feed_flows):
            raise ValueError("latent manual power exceeds the active non-feed schedule ceiling")
        return self

    @property
    def identity_binding_sha256(self) -> str:
        return physical_identity_key(self.physical_binding)


class ExactRestoreEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_artifact_id: str = Field(min_length=1, max_length=80)
    series_artifact_id: str = Field(min_length=1, max_length=80)
    pair_ordinal: int = Field(ge=0)
    pair_manifest_sha256: Sha256Digest


class ExactRestoreBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    devices: tuple[ExactRestoreDeviceBaseline, ExactRestoreDeviceBaseline]
    evidence: ExactRestoreEvidenceReference
    verification_policy: ExactRestoreVerificationPolicy
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_aware_capture_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("baseline capture time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        roles = tuple(device.role for device in self.devices)
        if roles != (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE):
            raise ValueError("baseline devices must be ordered master then slave")
        if self.devices[0].logical_id == self.devices[1].logical_id:
            raise ValueError("baseline logical devices must be distinct")
        if self.devices[0].identity_binding_sha256 == self.devices[1].identity_binding_sha256:
            raise ValueError("baseline physical bindings must be distinct")
        return self

    @property
    def baseline_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))

    def for_role(self, role: ExactRestoreRole) -> ExactRestoreDeviceBaseline:
        return next(device for device in self.devices if device.role is role)


class SafeManualTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ExactRestoreRole
    power: int = Field(ge=0, le=100)
    frequency: int = Field(ge=0, le=100)
    mode: Literal["constant"] = "constant"


class ExactRestoreAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0, le=31)
    action_id: str = Field(min_length=1, max_length=80)
    role: ExactRestoreRole
    kind: ExactRestoreActionKind
    target_sha256: Sha256Digest
    sentinel_slot: int | None = Field(default=None, ge=0, lt=LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    sentinel_image_sha256: Sha256Digest | None = None

    @model_validator(mode="after")
    def validate_sentinel_fields(self) -> Self:
        sentinel = self.kind is ExactRestoreActionKind.QUALIFY_SENTINEL
        if sentinel != (self.sentinel_slot is not None):
            raise ValueError("sentinel action requires exactly one slot")
        if sentinel != (self.sentinel_image_sha256 is not None):
            raise ValueError("sentinel action requires an expected image digest")
        return self


class ExactRestoreActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0, le=31)
    action_id: str = Field(min_length=1, max_length=80)
    outcome: ExactRestoreActionOutcome
    pre_state_sha256: Sha256Digest
    post_state_sha256: Sha256Digest
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def require_aware_completion(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("action completion time must be timezone-aware")
        return value


class ExactRestoreInflightAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0, le=31)
    action_id: str = Field(min_length=1, max_length=80)
    target_sha256: Sha256Digest
    pre_state_sha256: Sha256Digest
    authority_sha256: Sha256Digest
    intent_at: datetime

    @field_validator("intent_at")
    @classmethod
    def require_aware_intent(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("intent time must be timezone-aware")
        return value

    @property
    def inflight_sha256(self) -> str:
        """Bind crash-resume authority to this exact durable write intent."""

        return _sha256_json(self.model_dump(mode="json"))


class ExactRestoreAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationId
    cycle: ExactRestoreCycle
    baseline_sha256: Sha256Digest
    action_plan_sha256: Sha256Digest
    verification_policy_sha256: Sha256Digest
    journal_context_sha256: Sha256Digest
    scope: ExactRestoreAuthorityScope
    qualification_receipt_sha256: Sha256Digest | None = None
    issued_at: datetime
    expires_at: datetime
    boot_identity_sha256: Sha256Digest
    issued_monotonic_ns: int = Field(ge=0)
    deadline_monotonic_ns: int = Field(ge=0)
    confirmation_token_sha256: Sha256Digest
    permit_enabled_restore: Literal[True] = True
    permit_crash_resume: bool = False
    crash_resume_inflight_sha256: Sha256Digest | None = None

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_aware_authority_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authority time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0) or lifetime > timedelta(minutes=15):
            raise ValueError("authority lifetime must be at most fifteen minutes")
        if (
            self.deadline_monotonic_ns <= self.issued_monotonic_ns
            or self.deadline_monotonic_ns - self.issued_monotonic_ns != _timedelta_ns(lifetime)
        ):
            raise ValueError("authority monotonic lifetime must match its wall lifetime")
        expected_scope = (
            ExactRestoreAuthorityScope.EXACT_BASELINE_ONLY
            if self.cycle is ExactRestoreCycle.BASELINE_RESTORE
            else ExactRestoreAuthorityScope.BOOTSTRAP_QUALIFICATION
        )
        if self.scope is not expected_scope:
            raise ValueError("authority scope does not match its restore cycle")
        if self.cycle is ExactRestoreCycle.BASELINE_RESTORE:
            if self.qualification_receipt_sha256 is None:
                raise ValueError("baseline authority must bind qualification evidence")
        elif self.qualification_receipt_sha256 is not None:
            raise ValueError("bootstrap authority cannot consume qualification evidence")
        if self.permit_crash_resume != (self.crash_resume_inflight_sha256 is not None):
            raise ValueError("crash-resume authority must bind exactly one inflight action")
        return self

    @property
    def authority_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class ExactRestoreAuthorityActivation(BaseModel):
    """Durable, boot-bound consumption window for one attended authority grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_sha256: Sha256Digest
    boot_identity_sha256: Sha256Digest
    accepted_wall: datetime
    accepted_monotonic_ns: int = Field(ge=0)
    deadline_monotonic_ns: int = Field(ge=0)

    @field_validator("accepted_wall")
    @classmethod
    def require_aware_acceptance_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authority acceptance time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_monotonic_window(self) -> Self:
        if self.deadline_monotonic_ns < self.accepted_monotonic_ns:
            raise ValueError("authority monotonic deadline precedes acceptance")
        return self


class ExactRestoreObservation(BaseModel):
    """One explicitly requested state with host acquisition provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ExactRestoreRole
    identity_binding_sha256: Sha256Digest
    outer: OuterControlSnapshot
    schedule: ExactScheduleImage
    raw_frame_sha256: Sha256Digest
    requested_at: datetime
    observed_at: datetime
    received_at: datetime
    requested_monotonic_ns: int = Field(ge=0)
    observed_monotonic_ns: int = Field(ge=0)
    received_monotonic_ns: int = Field(ge=0)

    @field_validator("requested_at", "observed_at", "received_at")
    @classmethod
    def require_utc_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("observation acquisition times must be UTC")
        return value

    @model_validator(mode="after")
    def validate_acquisition_order(self) -> Self:
        if not self.requested_at <= self.observed_at <= self.received_at:
            raise ValueError("observation acquisition timestamps are out of order")
        if not (
            self.requested_monotonic_ns <= self.observed_monotonic_ns <= self.received_monotonic_ns
        ):
            raise ValueError("observation monotonic timestamps are out of order")
        return self


class ExactRestoreFinalEvidence(BaseModel):
    """Durable proof that one bounded exact pair produced a specific receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_sha256: Sha256Digest
    observations: tuple[ExactRestoreObservation, ExactRestoreObservation]
    completed_at: datetime
    completed_monotonic_ns: int = Field(ge=0)

    @field_validator("completed_at")
    @classmethod
    def require_utc_completion(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("final verification completion time must be UTC")
        return value

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if tuple(item.role for item in self.observations) != (
            ExactRestoreRole.MASTER,
            ExactRestoreRole.SLAVE,
        ):
            raise ValueError("final evidence must be ordered master then slave")
        if any(item.received_at > self.completed_at for item in self.observations):
            raise ValueError("final evidence cannot complete before observation receipt")
        if any(
            item.received_monotonic_ns > self.completed_monotonic_ns for item in self.observations
        ):
            raise ValueError("final evidence monotonic completion precedes observation receipt")
        return self


class ExactRestoreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: OperationId
    cycle: ExactRestoreCycle
    phase: ExactRestorePhase
    baseline: ExactRestoreBaseline
    baseline_sha256: Sha256Digest
    safe_targets: tuple[SafeManualTarget, SafeManualTarget]
    actions: tuple[ExactRestoreAction, ...] = Field(min_length=6, max_length=8)
    action_plan_sha256: Sha256Digest
    qualification_receipt_sha256: Sha256Digest | None = None
    qualification_final_record: ExactRestoreRecord | None = Field(default=None, repr=False)
    prior_authorities: tuple[ExactRestoreAuthority, ...] = ()
    prior_authority_activations: tuple[ExactRestoreAuthorityActivation | None, ...] = ()
    authority: ExactRestoreAuthority | None = None
    authority_activation: ExactRestoreAuthorityActivation | None = None
    completed_actions: tuple[ExactRestoreActionResult, ...] = ()
    inflight: ExactRestoreInflightAction | None = None
    error_code: ExactRestoreErrorCode | None = None
    final_evidence: ExactRestoreFinalEvidence | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_aware_record_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("record time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("record update time precedes creation")
        if self.baseline.baseline_sha256 != self.baseline_sha256:
            raise ValueError("baseline digest mismatch")
        if _action_plan_sha256(self.actions) != self.action_plan_sha256:
            raise ValueError("action plan digest mismatch")
        if tuple(target.role for target in self.safe_targets) != (
            ExactRestoreRole.MASTER,
            ExactRestoreRole.SLAVE,
        ):
            raise ValueError("safe targets must be ordered master then slave")
        for target in self.safe_targets:
            baseline = self.baseline.for_role(target.role)
            if not baseline.power_policy.permits(target.power):
                raise ValueError("safe manual target violates the baseline power policy")
        expected_actions = build_exact_restore_plan(
            self.baseline,
            self.safe_targets,
            cycle=self.cycle,
        )
        if self.actions != expected_actions:
            raise ValueError("actions do not match the deterministic restore plan")
        expected_indexes = tuple(range(len(self.actions)))
        if tuple(action.index for action in self.actions) != expected_indexes:
            raise ValueError("actions must have contiguous indexes")
        completed_count = len(self.completed_actions)
        if completed_count > len(self.actions):
            raise ValueError("completed actions cannot exceed the deterministic plan")
        for index, result in enumerate(self.completed_actions):
            action = self.actions[index]
            if result.index != index or result.action_id != action.action_id:
                raise ValueError("completed actions must be an exact plan prefix")
        authority_chain = (*self.prior_authorities, *((self.authority,) if self.authority else ()))
        activation_chain = (
            *self.prior_authority_activations,
            *((self.authority_activation,) if self.authority else ()),
        )
        if len(self.prior_authority_activations) != len(self.prior_authorities):
            raise ValueError("authority activation history must align with prior grants")
        if len(activation_chain) != len(authority_chain):
            raise ValueError("current authority activation must align with its grant")
        authority_digests = tuple(item.authority_sha256 for item in authority_chain)
        if len(authority_digests) != len(set(authority_digests)):
            raise ValueError("authority chain cannot contain duplicate grants")
        for index, item in enumerate(authority_chain):
            if (
                item.operation_id != self.operation_id
                or item.cycle is not self.cycle
                or item.baseline_sha256 != self.baseline_sha256
                or item.action_plan_sha256 != self.action_plan_sha256
                or item.verification_policy_sha256
                != self.baseline.verification_policy.policy_sha256
                or item.qualification_receipt_sha256 != self.qualification_receipt_sha256
            ):
                raise ValueError("authority chain is not bound to the immutable restore plan")
            if index and item.issued_at < authority_chain[index - 1].issued_at:
                raise ValueError("authority chain must follow issue-time order")
            if (
                index
                and item.boot_identity_sha256 == authority_chain[index - 1].boot_identity_sha256
                and item.issued_monotonic_ns < authority_chain[index - 1].issued_monotonic_ns
            ):
                raise ValueError("same-boot authority chain must follow monotonic issue order")
            activation = activation_chain[index]
            if activation is not None:
                if (
                    activation.authority_sha256 != item.authority_sha256
                    or activation.boot_identity_sha256 != item.boot_identity_sha256
                    or not item.issued_at <= activation.accepted_wall <= item.expires_at
                    or not item.issued_monotonic_ns
                    <= activation.accepted_monotonic_ns
                    <= item.deadline_monotonic_ns
                    or activation.deadline_monotonic_ns != item.deadline_monotonic_ns
                ):
                    raise ValueError("authority activation does not match its grant")
        if self.inflight is not None:
            if completed_count >= len(self.actions):
                raise ValueError("completed plan cannot have an inflight action")
            action = self.actions[completed_count]
            if (
                self.inflight.index != completed_count
                or self.inflight.action_id != action.action_id
                or self.inflight.target_sha256 != action.target_sha256
            ):
                raise ValueError("inflight action must be the exact next plan action")
            if self.inflight.authority_sha256 not in authority_digests:
                raise ValueError("inflight action must bind an authority in the durable chain")
        if self.authority is not None:
            if self.authority.cycle is not self.cycle:
                raise ValueError("authority cycle does not match the record")
            expected_scope = (
                ExactRestoreAuthorityScope.EXACT_BASELINE_ONLY
                if self.cycle is ExactRestoreCycle.BASELINE_RESTORE
                else ExactRestoreAuthorityScope.BOOTSTRAP_QUALIFICATION
            )
            if self.authority.scope is not expected_scope:
                raise ValueError("authority scope does not match the record cycle")
        if self.cycle is ExactRestoreCycle.SENTINEL_QUALIFICATION:
            if (
                self.qualification_receipt_sha256 is not None
                or self.qualification_final_record is not None
            ):
                raise ValueError("bootstrap qualification cannot consume qualification evidence")
        else:
            qualification = self.qualification_final_record
            if qualification is None or self.qualification_receipt_sha256 is None:
                raise ValueError("baseline restore requires embedded qualification provenance")
            if (
                qualification.phase is not ExactRestorePhase.FINAL_VERIFIED
                or qualification.operation_id == self.operation_id
                or qualification.baseline != self.baseline
                or qualification.safe_targets != self.safe_targets
            ):
                raise ValueError("qualification provenance is not an exact finalized restore")
            if qualification.cycle is ExactRestoreCycle.BASELINE_RESTORE:
                parent = qualification.qualification_final_record
                if (
                    parent is None
                    or parent.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION
                    or parent.phase is not ExactRestorePhase.FINAL_VERIFIED
                ):
                    raise ValueError("final restore must consume one qualified baseline cycle")
            elif qualification.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION:
                raise ValueError("baseline restore qualification cycle is invalid")
            qualification_receipt = _receipt_from_final_verified_record(qualification)
            if (
                qualification.updated_at > self.created_at
                or qualification_receipt.completed_at > self.created_at
            ):
                raise ValueError("qualification provenance postdates baseline promotion")
            if qualification_receipt.receipt_sha256 != self.qualification_receipt_sha256:
                raise ValueError("qualification receipt digest does not match embedded provenance")
        if self.phase is ExactRestorePhase.PREPARED:
            if (
                self.authority is not None
                or self.prior_authorities
                or self.prior_authority_activations
                or self.authority_activation is not None
                or completed_count
                or self.inflight is not None
            ):
                raise ValueError("prepared record cannot contain write authority or progress")
        elif self.phase is ExactRestorePhase.ARMED:
            if self.authority is None or completed_count or self.inflight is not None:
                raise ValueError("armed record requires authority and no progress")
        elif self.authority is None:
            raise ValueError("post-armed record requires bound authority")
        if self.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY:
            if completed_count != len(self.actions) or self.inflight is not None:
                raise ValueError("final verification requires the complete action prefix")
        if self.phase is ExactRestorePhase.FINAL_VERIFIED:
            if (
                completed_count != len(self.actions)
                or self.inflight is not None
                or self.final_evidence is None
                or self.authority_activation is None
            ):
                raise ValueError("final-verified record requires complete durable evidence")
        elif self.final_evidence is not None:
            raise ValueError("final evidence is only valid in the final-verified phase")
        if self.phase is ExactRestorePhase.RECOVERY_REQUIRED:
            if self.error_code is None:
                raise ValueError("recovery-required record needs a typed error")
        elif self.error_code is not None:
            raise ValueError("only recovery-required records may contain an error")
        return self

    @property
    def authority_chain_sha256(self) -> str:
        authorities = (*self.prior_authorities, *((self.authority,) if self.authority else ()))
        activations = (
            *self.prior_authority_activations,
            *((self.authority_activation,) if self.authority else ()),
        )
        return _sha256_json(
            [
                {
                    "authority": authority.model_dump(mode="json"),
                    "activation": (
                        activation.model_dump(mode="json") if activation is not None else None
                    ),
                }
                for authority, activation in zip(authorities, activations, strict=True)
            ]
        )

    @property
    def authority_context_sha256(self) -> str:
        """Digest the exact journal snapshot shown to an attended approver.

        This deliberately covers the whole validated record. In particular it binds the
        phase, exact completed prefix, exact inflight intent, error evidence, and current
        authority-chain head. A newly consumed authority changes the record, so this digest
        is checked only at arm/reauthorize/recover consumption, never during live execution.
        """

        return _sha256_json(self.model_dump(mode="json"))


class ExactRestoreReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: OperationId
    cycle: ExactRestoreCycle
    baseline_sha256: Sha256Digest
    action_plan_sha256: Sha256Digest
    authority_sha256: Sha256Digest
    authority_chain_sha256: Sha256Digest
    qualification_receipt_sha256: Sha256Digest | None
    completed_action_count: int = Field(ge=6, le=8)
    final_raw_frame_sha256: tuple[Sha256Digest, Sha256Digest]
    completed_at: datetime
    outcome: Literal["exact_restored"] = "exact_restored"

    @field_validator("completed_at")
    @classmethod
    def require_aware_receipt_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("receipt time must be UTC")
        return value

    @model_validator(mode="after")
    def validate_cycle_evidence(self) -> Self:
        expected_count = 6 if self.cycle is ExactRestoreCycle.BASELINE_RESTORE else 8
        if self.completed_action_count != expected_count:
            raise ValueError("receipt action count does not match its restore cycle")
        if self.cycle is ExactRestoreCycle.BASELINE_RESTORE:
            if self.qualification_receipt_sha256 is None:
                raise ValueError("baseline receipt must bind qualification evidence")
        elif self.qualification_receipt_sha256 is not None:
            raise ValueError("qualification receipt cannot consume prior qualification evidence")
        return self

    @property
    def receipt_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


def _receipt_from_final_verified_record(record: ExactRestoreRecord) -> ExactRestoreReceipt:
    """Recompute the only receipt represented by one complete final journal record."""

    evidence = record.final_evidence
    authority = record.authority
    if (
        record.phase is not ExactRestorePhase.FINAL_VERIFIED
        or evidence is None
        or authority is None
    ):
        raise ValueError("receipt provenance is not final verified")
    receipt = ExactRestoreReceipt(
        operation_id=record.operation_id,
        cycle=record.cycle,
        baseline_sha256=record.baseline_sha256,
        action_plan_sha256=record.action_plan_sha256,
        authority_sha256=authority.authority_sha256,
        authority_chain_sha256=record.authority_chain_sha256,
        qualification_receipt_sha256=record.qualification_receipt_sha256,
        completed_action_count=len(record.completed_actions),
        final_raw_frame_sha256=tuple(item.raw_frame_sha256 for item in evidence.observations),
        completed_at=evidence.completed_at,
    )
    if receipt.receipt_sha256 != evidence.receipt_sha256:
        raise ValueError("final evidence receipt digest mismatch")
    return receipt


class ExactRestoreDevice(Protocol):
    @property
    def identity_binding_sha256(self) -> str: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def read_connected_identity_binding_sha256(self) -> str: ...

    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: Callable[[], bool] | None = None,
    ) -> None: ...

    async def restore_schedule_image(
        self,
        image: bytes,
        *,
        guard: Callable[[], bool] | None = None,
    ) -> object: ...


class ExactRestoreStore(Protocol):
    def claim(self) -> AbstractContextManager[None]: ...

    def create(self, payload: Mapping[str, Any]) -> None: ...

    def load(self) -> dict[str, Any] | None: ...

    def save(self, payload: Mapping[str, Any]) -> None: ...

    def clear(self) -> None: ...

    def reload_and_confirm_successor(self, expected: Mapping[str, Any] | None) -> bool: ...


class ExactRestoreQualificationReceiptStore(Protocol):
    """Durably archive receipts and prevent replay of finalized operations."""

    def persist_final_verified_receipt(self, receipt: ExactRestoreReceipt) -> None:
        """Idempotently persist and fsync one exact receipt before returning."""
        ...

    def load_final_verified_receipt(
        self,
        receipt_sha256: str,
    ) -> Mapping[str, Any] | None: ...

    def load_operation_finalization(
        self,
        operation_id: str,
    ) -> Mapping[str, Any] | None: ...

    def confirm_operation_finalization(
        self,
        receipt: ExactRestoreReceipt,
    ) -> Mapping[str, Any]: ...


class ExactRestoreGuard(Protocol):
    @property
    def permitted(self) -> bool: ...

    @property
    def epoch(self) -> int: ...

    def clear(self) -> None: ...

    def trip(self) -> None: ...

    def lease(self) -> AbstractContextManager[None]: ...


class _CommitAfterVerifiedLease:
    """Persist FINAL_VERIFIED only after the deployment lease exits cleanly.

    The final explicit pair is first held in memory.  A crash or lease-integrity failure before
    ``__exit__`` therefore leaves the durable journal at ``AWAITING_FINAL_VERIFY`` and a later
    attended invocation must capture a new pair instead of issuing a receipt from an unconfirmed
    lease boundary.
    """

    def __init__(
        self,
        lease: AbstractContextManager[None],
        commit: Callable[[ExactRestoreRecord], None],
    ) -> None:
        self._lease = lease
        self._commit = commit
        self._staged: ExactRestoreRecord | None = None

    def __enter__(self) -> Self:
        self._lease.__enter__()
        return self

    def stage(self, record: ExactRestoreRecord) -> None:
        if self._staged is not None or record.phase is not ExactRestorePhase.FINAL_VERIFIED:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        self._staged = record

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        suppressed = self._lease.__exit__(exc_type, exc_value, traceback)
        if exc_type is None and not suppressed and self._staged is not None:
            self._commit(self._staged)
        return bool(suppressed)


FreshObservation = Callable[[ExactRestoreRole], Awaitable[Any]]
DeviceResolver = Callable[
    [ExactRestoreRole, ExactRestoreObservation],
    ExactRestoreDevice,
]
Clock = Callable[[], datetime]
MonotonicClock = Callable[[], int]
BootIdentity = Callable[[], str]


@dataclass(slots=True)
class _LiveAuthorityWindow:
    authority_sha256: str
    boot_identity_sha256: str
    deadline_monotonic_ns: int
    last_wall_time: datetime
    last_monotonic_ns: int


def _validate_schedule_power_policy(image: bytes, policy: RestorePowerPolicy) -> None:
    exact = validate_local_wavemaker_pro_schedule_image(image)
    for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT):
        entry = decode_local_wavemaker_pro_slot_wire(
            get_local_wavemaker_pro_slot_wire(exact, index),
            slot_index=index,
        )
        if entry is None:
            continue
        power = entry.parameters["flow"]
        if not policy.permits(power, feed_stop=entry.mode == "feed"):
            raise ValueError(f"schedule slot {index} power violates the attended safe policy")


def _sentinel_target(image: bytes) -> tuple[int, bytes]:
    exact = validate_local_wavemaker_pro_schedule_image(image)
    for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT):
        current = get_local_wavemaker_pro_slot_wire(exact, index)
        if current == LOCAL_WAVEMAKER_PRO_UNUSED_ZERO:
            replacement = LOCAL_WAVEMAKER_PRO_UNUSED_EE
        elif current == LOCAL_WAVEMAKER_PRO_UNUSED_EE:
            replacement = LOCAL_WAVEMAKER_PRO_UNUSED_ZERO
        else:
            continue
        return index, patch_local_wavemaker_pro_schedule_slot(exact, index, replacement)
    raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)


def _validated_record_update(
    record: ExactRestoreRecord,
    **updates: object,
) -> ExactRestoreRecord:
    payload = record.model_dump(mode="json")
    payload.update(updates)
    return ExactRestoreRecord.model_validate(payload)


def _action_plan_sha256(actions: tuple[ExactRestoreAction, ...]) -> str:
    return _sha256_json([action.model_dump(mode="json") for action in actions])


def _safe_fallback_target(target: SafeManualTarget) -> DeviceTarget:
    return DeviceTarget(
        enabled=True,
        power=target.power,
        mode=target.mode,
        frequency=target.frequency,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=False,
    )


def _exact_outer_target(baseline: ExactRestoreDeviceBaseline) -> DeviceTarget:
    return DeviceTarget(
        enabled=baseline.outer.enabled,
        power=baseline.outer.power,
        mode=baseline.outer.mode,
        frequency=baseline.outer.frequency,
        linkage=baseline.outer.linkage,
        timer_enabled=baseline.outer.timer_enabled,
    )


def build_exact_restore_plan(
    baseline: ExactRestoreBaseline,
    safe_targets: tuple[SafeManualTarget, SafeManualTarget],
    *,
    cycle: ExactRestoreCycle,
) -> tuple[ExactRestoreAction, ...]:
    """Derive the only v1 action order; callers cannot inject arbitrary payloads."""

    if tuple(target.role for target in safe_targets) != (
        ExactRestoreRole.MASTER,
        ExactRestoreRole.SLAVE,
    ):
        raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)
    targets = {target.role: target for target in safe_targets}
    for role, target in targets.items():
        if not baseline.for_role(role).power_policy.permits(target.power):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)

    specs: list[tuple[ExactRestoreRole, ExactRestoreActionKind, dict[str, Any]]] = [
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.SAFE_FALLBACK, {}),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.SAFE_FALLBACK, {}),
    ]
    if cycle is ExactRestoreCycle.SENTINEL_QUALIFICATION:
        for role in (ExactRestoreRole.SLAVE, ExactRestoreRole.MASTER):
            slot, expected = _sentinel_target(baseline.for_role(role).schedule.image_bytes)
            specs.append(
                (
                    role,
                    ExactRestoreActionKind.QUALIFY_SENTINEL,
                    {
                        "sentinel_slot": slot,
                        "sentinel_image_sha256": hashlib.sha256(expected).hexdigest(),
                    },
                )
            )
    specs.extend(
        (
            (ExactRestoreRole.SLAVE, ExactRestoreActionKind.RESTORE_SCHEDULE, {}),
            (ExactRestoreRole.MASTER, ExactRestoreActionKind.RESTORE_SCHEDULE, {}),
            (ExactRestoreRole.SLAVE, ExactRestoreActionKind.RESTORE_OUTER, {}),
            (ExactRestoreRole.MASTER, ExactRestoreActionKind.RESTORE_OUTER, {}),
        )
    )

    actions: list[ExactRestoreAction] = []
    for index, (role, kind, extra) in enumerate(specs):
        target_payload: dict[str, Any] = {
            "baseline_sha256": baseline.baseline_sha256,
            "index": index,
            "kind": kind.value,
            "role": role.value,
        }
        if kind is ExactRestoreActionKind.SAFE_FALLBACK:
            target_payload["device_target"] = _safe_fallback_target(targets[role]).model_dump(
                mode="json"
            )
        elif kind is ExactRestoreActionKind.RESTORE_SCHEDULE:
            target_payload["schedule_sha256"] = baseline.for_role(role).schedule.image_sha256
        elif kind is ExactRestoreActionKind.RESTORE_OUTER:
            target_payload["device_target"] = _exact_outer_target(
                baseline.for_role(role)
            ).model_dump(mode="json")
        elif kind is ExactRestoreActionKind.QUALIFY_SENTINEL:
            target_payload.update(extra)
        actions.append(
            ExactRestoreAction(
                index=index,
                action_id=f"{index:02d}-{role.value}-{kind.value}",
                role=role,
                kind=kind,
                target_sha256=_sha256_json(target_payload),
                **extra,
            )
        )
    return tuple(actions)


def prepare_exact_restore_record(
    baseline: ExactRestoreBaseline,
    safe_targets: tuple[SafeManualTarget, SafeManualTarget],
    *,
    cycle: ExactRestoreCycle = ExactRestoreCycle.SENTINEL_QUALIFICATION,
    operation_id: str | None = None,
    now: datetime | None = None,
) -> ExactRestoreRecord:
    if cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION:
        raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)
    created_at = now or datetime.now(UTC)
    actions = build_exact_restore_plan(baseline, safe_targets, cycle=cycle)
    return ExactRestoreRecord(
        operation_id=operation_id or uuid4().hex,
        cycle=cycle,
        phase=ExactRestorePhase.PREPARED,
        baseline=baseline,
        baseline_sha256=baseline.baseline_sha256,
        safe_targets=safe_targets,
        actions=actions,
        action_plan_sha256=_action_plan_sha256(actions),
        created_at=created_at,
        updated_at=created_at,
    )


def prepare_qualified_final_restore_record(
    qualified: ExactRestoreRecord,
    *,
    operation_id: str,
    now: datetime | None = None,
) -> ExactRestoreRecord:
    """Create the durable phase-5 restore plan from a fully qualified baseline record."""

    if (
        qualified.cycle is not ExactRestoreCycle.BASELINE_RESTORE
        or qualified.phase is not ExactRestorePhase.FINAL_VERIFIED
        or qualified.qualification_final_record is None
        or qualified.qualification_final_record.cycle
        is not ExactRestoreCycle.SENTINEL_QUALIFICATION
    ):
        raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)
    try:
        qualification_receipt = _receipt_from_final_verified_record(qualified)
    except (TypeError, ValueError) as error:
        raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE) from error
    created_at = now or datetime.now(UTC)
    actions = build_exact_restore_plan(
        qualified.baseline,
        qualified.safe_targets,
        cycle=ExactRestoreCycle.BASELINE_RESTORE,
    )
    return ExactRestoreRecord(
        operation_id=operation_id,
        cycle=ExactRestoreCycle.BASELINE_RESTORE,
        phase=ExactRestorePhase.PREPARED,
        baseline=qualified.baseline,
        baseline_sha256=qualified.baseline_sha256,
        safe_targets=qualified.safe_targets,
        actions=actions,
        action_plan_sha256=_action_plan_sha256(actions),
        qualification_receipt_sha256=qualification_receipt.receipt_sha256,
        qualification_final_record=qualified,
        created_at=created_at,
        updated_at=created_at,
    )


class ExactRestoreController:
    """Execute one immutable plan with durable at-most-once action boundaries."""

    def __init__(
        self,
        store: ExactRestoreStore,
        guard: ExactRestoreGuard,
        *,
        observe: FreshObservation,
        resolve_device: DeviceResolver,
        qualification_receipts: ExactRestoreQualificationReceiptStore | None = None,
        clock: Clock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = system_boottime_ns,
        boot_identity: BootIdentity = system_boot_identity_sha256,
    ) -> None:
        self._store = store
        self._guard = guard
        self._observe = observe
        self._resolve_device = resolve_device
        self._qualification_receipts = qualification_receipts
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._boot_identity = boot_identity

    def create(self, record: ExactRestoreRecord) -> None:
        if (
            record.phase is not ExactRestorePhase.PREPARED
            or record.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)
        payload = record.model_dump(mode="json")
        with self._store.claim():
            try:
                self._store.create(payload)
            except BaseException:
                if not self._store.reload_and_confirm_successor(payload):
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from None

    def create_qualified_final_restore(self, record: ExactRestoreRecord) -> None:
        """Create only a phase-5 record whose immediate parent is a qualified baseline cycle."""

        parent = record.qualification_final_record
        if (
            record.phase is not ExactRestorePhase.PREPARED
            or record.cycle is not ExactRestoreCycle.BASELINE_RESTORE
            or parent is None
            or parent.cycle is not ExactRestoreCycle.BASELINE_RESTORE
            or parent.phase is not ExactRestorePhase.FINAL_VERIFIED
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.INVALID_BASELINE)
        payload = record.model_dump(mode="json")
        with self._store.claim():
            self._require_operation_not_finalized(record.operation_id)
            try:
                self._store.create(payload)
            except BaseException:
                if not self._store.reload_and_confirm_successor(payload):
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from None

    def promote_to_baseline_restore(
        self,
        *,
        operation_id: str | None = None,
    ) -> ExactRestoreRecord:
        """Atomically replace a finalized sentinel with its qualified baseline successor."""

        with self._store.claim():
            qualification = self._load()
            if (
                qualification.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION
                or qualification.phase is not ExactRestorePhase.FINAL_VERIFIED
            ):
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            try:
                qualification_receipt = _receipt_from_final_verified_record(qualification)
                promoted = self._prepare_baseline_successor(
                    qualification,
                    qualification_receipt,
                    operation_id=operation_id or uuid4().hex,
                )
            except (TypeError, ValueError) as error:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error
            self._require_operation_not_finalized(promoted.operation_id)
            self._save_exact(promoted)
            promoted_payload = promoted.model_dump(mode="json")
            try:
                confirmed = self._store.reload_and_confirm_successor(promoted_payload)
            except BaseException as error:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error
            if not confirmed:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            return promoted

    def arm(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord:
        with self._store.claim():
            record = self._load()
            if record.phase is not ExactRestorePhase.PREPARED:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            self._require_fresh_initial_sentinel_baseline(record)
            if record.cycle is ExactRestoreCycle.BASELINE_RESTORE:
                qualification_final = record.qualification_final_record
                if qualification_final is None:
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
                try:
                    qualification_receipt = _receipt_from_final_verified_record(qualification_final)
                except (TypeError, ValueError) as error:
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY) from error
                qualification_receipt_sha256 = qualification_receipt.receipt_sha256
                if qualification_receipt_sha256 != record.qualification_receipt_sha256:
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
            else:
                qualification_receipt_sha256 = None
            self._validate_authority(
                record,
                authority,
                qualification_receipt_sha256=qualification_receipt_sha256,
                require_current_context=True,
            )
            self._validate_crash_resume_binding(record, authority)
            self._require_operation_not_finalized(record.operation_id)
            activation = self._activate_authority(authority)
            armed = _validated_record_update(
                record,
                phase=ExactRestorePhase.ARMED,
                authority=authority,
                authority_activation=activation,
                qualification_receipt_sha256=qualification_receipt_sha256,
                updated_at=self._now(),
            )
            self._save_exact(armed)
            return armed

    def _prepare_baseline_successor(
        self,
        qualification: ExactRestoreRecord,
        receipt: ExactRestoreReceipt,
        *,
        operation_id: str,
    ) -> ExactRestoreRecord:
        actions = build_exact_restore_plan(
            qualification.baseline,
            qualification.safe_targets,
            cycle=ExactRestoreCycle.BASELINE_RESTORE,
        )
        created_at = self._now()
        return ExactRestoreRecord(
            operation_id=operation_id,
            cycle=ExactRestoreCycle.BASELINE_RESTORE,
            phase=ExactRestorePhase.PREPARED,
            baseline=qualification.baseline,
            baseline_sha256=qualification.baseline_sha256,
            safe_targets=qualification.safe_targets,
            actions=actions,
            action_plan_sha256=_action_plan_sha256(actions),
            qualification_receipt_sha256=receipt.receipt_sha256,
            qualification_final_record=qualification,
            created_at=created_at,
            updated_at=created_at,
        )

    def reauthorize(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord:
        """Durably renew attended authority without replaying completed or inflight actions."""

        with self._store.claim():
            record = self._load()
            if record.phase not in {
                ExactRestorePhase.ARMED,
                ExactRestorePhase.RESTORING,
                ExactRestorePhase.AWAITING_FINAL_VERIFY,
            }:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            current = record.authority
            if current is None:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            self._validate_authority(record, authority, require_current_context=True)
            self._validate_crash_resume_binding(record, authority)
            activation = self._activate_authority(authority)
            existing_digests = {
                item.authority_sha256 for item in (*record.prior_authorities, current)
            }
            if (
                authority.authority_sha256 in existing_digests
                or authority.issued_at < current.issued_at
            ):
                raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
            resumed = _validated_record_update(
                record,
                prior_authorities=(*record.prior_authorities, current),
                prior_authority_activations=(
                    *record.prior_authority_activations,
                    record.authority_activation,
                ),
                authority=authority,
                authority_activation=activation,
                updated_at=self._now(),
            )
            self._save_exact(resumed)
            return resumed

    def recover(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord:
        """Atomically consume fresh attended authority for a latched recovery record."""

        with self._store.claim():
            record = self._load()
            if record.phase is not ExactRestorePhase.RECOVERY_REQUIRED:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            current = record.authority
            if current is None:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            self._validate_authority(record, authority, require_current_context=True)
            self._validate_crash_resume_binding(record, authority)
            activation = self._activate_authority(authority)
            existing_digests = {
                item.authority_sha256 for item in (*record.prior_authorities, current)
            }
            if (
                authority.authority_sha256 in existing_digests
                or authority.issued_at < current.issued_at
            ):
                raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
            next_phase = (
                ExactRestorePhase.AWAITING_FINAL_VERIFY
                if len(record.completed_actions) == len(record.actions)
                else ExactRestorePhase.RESTORING
            )
            recovered = _validated_record_update(
                record,
                phase=next_phase,
                prior_authorities=(*record.prior_authorities, current),
                prior_authority_activations=(
                    *record.prior_authority_activations,
                    record.authority_activation,
                ),
                authority=authority,
                authority_activation=activation,
                error_code=None,
                updated_at=self._now(),
            )
            self._save_exact(recovered)
            return recovered

    async def execute(self) -> ExactRestoreRecord:
        # The guard lease trips its epoch on every exit, including preflight failures.
        with (
            self._store.claim(),
            _CommitAfterVerifiedLease(self._guard.lease(), self._save_exact) as final_commit,
        ):
            self._guard.clear()
            if not self._guard.permitted:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.SAFETY_INTERLOCK)
            epoch = self._guard.epoch
            record = self._load()
            if record.phase not in {
                ExactRestorePhase.ARMED,
                ExactRestorePhase.RESTORING,
                ExactRestorePhase.AWAITING_FINAL_VERIFY,
            }:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            self._require_fresh_initial_sentinel_baseline(record)
            authority = self._require_live_authority(record)
            authority_window = self._start_authority_window(record, authority)

            if record.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY:
                verified, _receipt = await self._capture_final_pair(
                    record,
                    authority,
                    authority_window,
                    epoch,
                )
                final_commit.stage(verified)
                return verified

            if record.inflight is not None:
                record = await self._reconcile_inflight(
                    record,
                    authority,
                    authority_window,
                    epoch,
                )
            while len(record.completed_actions) < len(record.actions):
                self._require_fresh_initial_sentinel_baseline(record)
                authority = self._require_live_authority(record)
                self._require_authority_window(authority_window, authority)
                action = record.actions[len(record.completed_actions)]
                try:
                    before = await self._fresh(action.role, record)
                except ExactRestoreRecoveryRequired as error:
                    self._latch(record, error.code)
                    raise
                self._require_guard_or_latch(record, epoch, authority, authority_window)
                if self._action_satisfied(record, action, before):
                    # A skip still gets a distinct post-action explicit observation.  If the
                    # target drifted between reads, the second read becomes the pre-write state.
                    try:
                        after_skip = await self._fresh(action.role, record)
                    except ExactRestoreRecoveryRequired as error:
                        self._latch(record, error.code)
                        raise
                    self._require_guard_or_latch(record, epoch, authority, authority_window)
                    if self._action_satisfied(record, action, after_skip):
                        record = self._complete(
                            record,
                            action,
                            before,
                            after_skip,
                            ExactRestoreActionOutcome.ALREADY_SATISFIED,
                        )
                        self._save_exact(record)
                        continue
                    before = after_skip

                self._require_guard_or_latch(record, epoch, authority, authority_window)
                inflight = ExactRestoreInflightAction(
                    index=action.index,
                    action_id=action.action_id,
                    target_sha256=action.target_sha256,
                    pre_state_sha256=before.raw_frame_sha256,
                    authority_sha256=authority.authority_sha256,
                    intent_at=self._now(),
                )
                pending = _validated_record_update(
                    record,
                    phase=ExactRestorePhase.RESTORING,
                    inflight=inflight,
                    updated_at=self._now(),
                )
                self._save_exact(pending)
                record = pending
                # Confirm the exact durable successor before resolving an endpoint or sending.
                if self._load() != pending:
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)

                device = self._resolve_device(action.role, before)
                if device.identity_binding_sha256 != before.identity_binding_sha256:
                    self._latch(record, ExactRestoreErrorCode.BINDING)
                    raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.BINDING)
                write_uncertain = False
                connected_binding_mismatch = False
                disconnect_failed = False
                cancellation: CancelledError | None = None
                try:
                    await device.connect()
                    try:
                        connected_binding = await device.read_connected_identity_binding_sha256()
                    except CancelledError:
                        raise
                    except BaseException:
                        connected_binding_mismatch = True
                    else:
                        if connected_binding != before.identity_binding_sha256:
                            connected_binding_mismatch = True
                    if not connected_binding_mismatch:
                        self._require_guard(epoch, authority, authority_window)
                        try:
                            await self._write_action(
                                record,
                                action,
                                device,
                                epoch,
                                authority,
                                authority_window,
                            )
                        except CancelledError:
                            raise
                        except BaseException:
                            write_uncertain = True
                except CancelledError as error:
                    cancellation = error
                except BaseException:
                    write_uncertain = True
                finally:
                    try:
                        await device.disconnect()
                    except CancelledError as error:
                        if cancellation is None:
                            cancellation = error
                    except BaseException:
                        disconnect_failed = True

                if cancellation is not None:
                    raise cancellation
                if disconnect_failed:
                    self._latch(record, ExactRestoreErrorCode.UNCERTAIN_WRITE)
                    raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.UNCERTAIN_WRITE)
                if connected_binding_mismatch:
                    self._latch(record, ExactRestoreErrorCode.BINDING)
                    raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.BINDING)
                # The independent explicit observation begins only after the writer connection
                # is closed.  A write or disconnect exception is uncertain and is never resent.
                try:
                    after = await self._fresh(action.role, record)
                except CancelledError:
                    raise
                except ExactRestoreRecoveryRequired as error:
                    code = (
                        ExactRestoreErrorCode.BINDING
                        if error.code is ExactRestoreErrorCode.BINDING
                        else ExactRestoreErrorCode.UNCERTAIN_WRITE
                    )
                    self._latch(record, code)
                    raise ExactRestoreRecoveryRequired(code) from None
                except BaseException:
                    self._latch(record, ExactRestoreErrorCode.UNCERTAIN_WRITE)
                    raise ExactRestoreRecoveryRequired(
                        ExactRestoreErrorCode.UNCERTAIN_WRITE
                    ) from None
                self._require_guard_or_latch(record, epoch, authority, authority_window)
                if not self._action_satisfied(record, action, after):
                    code = (
                        ExactRestoreErrorCode.UNCERTAIN_WRITE
                        if write_uncertain
                        else ExactRestoreErrorCode.VERIFY_MISMATCH
                    )
                    self._latch(record, code)
                    raise ExactRestoreRecoveryRequired(code)
                outcome = (
                    ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN
                    if write_uncertain
                    else ExactRestoreActionOutcome.WRITTEN_VERIFIED
                )

                record = self._complete(record, action, before, after, outcome)
                self._save_exact(record)

            self._require_guard_or_latch(record, epoch, authority, authority_window)
            awaiting = _validated_record_update(
                record,
                phase=ExactRestorePhase.AWAITING_FINAL_VERIFY,
                updated_at=self._now(),
            )
            self._save_exact(awaiting)
            verified, _receipt = await self._capture_final_pair(
                awaiting,
                authority,
                authority_window,
                epoch,
            )
            final_commit.stage(verified)
            return verified

    async def finalize(self) -> ExactRestoreReceipt:
        """Return the durable receipt, or resume an interrupted guarded final capture."""

        with self._store.claim():
            record = self._load()
            if record.phase is ExactRestorePhase.FINAL_VERIFIED:
                return self._receipt_from_final_record(record)
            if record.phase is not ExactRestorePhase.AWAITING_FINAL_VERIFY:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            with _CommitAfterVerifiedLease(self._guard.lease(), self._save_exact) as final_commit:
                self._guard.clear()
                if not self._guard.permitted:
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.SAFETY_INTERLOCK)
                epoch = self._guard.epoch
                authority = self._require_live_authority(record)
                authority_window = self._start_authority_window(record, authority)
                verified, receipt = await self._capture_final_pair(
                    record,
                    authority,
                    authority_window,
                    epoch,
                )
                final_commit.stage(verified)
                return receipt

    async def _capture_final_pair(
        self,
        record: ExactRestoreRecord,
        authority: ExactRestoreAuthority,
        authority_window: _LiveAuthorityWindow,
        epoch: int,
    ) -> tuple[ExactRestoreRecord, ExactRestoreReceipt]:
        if record.phase is not ExactRestorePhase.AWAITING_FINAL_VERIFY:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        self._require_guard_or_latch(record, epoch, authority, authority_window)
        verification_started_at = self._now()
        verification_started_monotonic_ns = self._monotonic_ns()
        ordered_observations: list[ExactRestoreObservation] = []
        for role in (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE):
            observation = await self._fresh(role, record)
            self._require_guard_or_latch(record, epoch, authority, authority_window)
            ordered_observations.append(observation)
        ordered = (ordered_observations[0], ordered_observations[1])
        completed_at = self._now()
        completed_monotonic_ns = self._monotonic_ns()
        policy = record.baseline.verification_policy
        max_age_ns = int(policy.max_observation_age_seconds * 1_000_000_000)
        max_pair_ns = int(policy.max_final_pair_gap_seconds * 1_000_000_000)
        for observation in ordered:
            baseline = record.baseline.for_role(observation.role)
            if (
                observation.requested_at < record.updated_at
                or observation.received_at > completed_at
                or observation.received_monotonic_ns > completed_monotonic_ns
                or completed_monotonic_ns - observation.observed_monotonic_ns > max_age_ns
                or not self._matches_baseline(baseline, observation)
            ):
                raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.VERIFY_MISMATCH)
        verification_window_ns = max(item.received_monotonic_ns for item in ordered) - min(
            item.requested_monotonic_ns for item in ordered
        )
        if (
            completed_at < verification_started_at
            or completed_monotonic_ns < verification_started_monotonic_ns
            or completed_monotonic_ns - verification_started_monotonic_ns > max_pair_ns
            or verification_window_ns > max_pair_ns
        ):
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.VERIFY_MISMATCH)
        receipt = ExactRestoreReceipt(
            operation_id=record.operation_id,
            cycle=record.cycle,
            baseline_sha256=record.baseline_sha256,
            action_plan_sha256=record.action_plan_sha256,
            authority_sha256=authority.authority_sha256,
            authority_chain_sha256=record.authority_chain_sha256,
            qualification_receipt_sha256=record.qualification_receipt_sha256,
            completed_action_count=len(record.completed_actions),
            final_raw_frame_sha256=tuple(item.raw_frame_sha256 for item in ordered),
            completed_at=completed_at,
        )
        final_evidence = ExactRestoreFinalEvidence(
            receipt_sha256=receipt.receipt_sha256,
            observations=ordered,
            completed_at=completed_at,
            completed_monotonic_ns=completed_monotonic_ns,
        )
        self._require_guard_or_latch(record, epoch, authority, authority_window)
        verified = _validated_record_update(
            record,
            phase=ExactRestorePhase.FINAL_VERIFIED,
            final_evidence=final_evidence,
            updated_at=completed_at,
        )
        return verified, receipt

    def _receipt_from_final_record(self, record: ExactRestoreRecord) -> ExactRestoreReceipt:
        try:
            return _receipt_from_final_verified_record(record)
        except (TypeError, ValueError) as error:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error

    def clear_after_receipt(self, receipt: ExactRestoreReceipt) -> None:
        with self._store.claim():
            record = self._load()
            if record.phase is not ExactRestorePhase.FINAL_VERIFIED:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            try:
                supplied = ExactRestoreReceipt.model_validate(
                    receipt.model_dump(mode="json")
                    if isinstance(receipt, ExactRestoreReceipt)
                    else receipt
                )
            except (TypeError, ValueError) as error:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error
            expected = self._receipt_from_final_record(record)
            if supplied != expected or supplied.receipt_sha256 != expected.receipt_sha256:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            receipt_store = self._qualification_receipts
            if receipt_store is None:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            receipts = [expected]
            if record.qualification_final_record is not None:
                receipts.insert(
                    0,
                    self._receipt_from_final_record(record.qualification_final_record),
                )
            try:
                for archived_receipt in receipts:
                    receipt_store.persist_final_verified_receipt(archived_receipt)
                    archived_payload = receipt_store.load_final_verified_receipt(
                        archived_receipt.receipt_sha256
                    )
                    if archived_payload is None:
                        raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
                    archived = ExactRestoreReceipt.model_validate(archived_payload)
                    if (
                        archived != archived_receipt
                        or archived.receipt_sha256 != archived_receipt.receipt_sha256
                    ):
                        raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
                    confirmed_finalization = receipt_store.confirm_operation_finalization(
                        archived_receipt
                    )
                    loaded_finalization = receipt_store.load_operation_finalization(
                        archived_receipt.operation_id
                    )
                    if (
                        not isinstance(confirmed_finalization, Mapping)
                        or not isinstance(loaded_finalization, Mapping)
                        or dict(confirmed_finalization) != dict(loaded_finalization)
                        or confirmed_finalization.get("receipt_sha256")
                        != archived_receipt.receipt_sha256
                        or confirmed_finalization.get("cycle") != archived_receipt.cycle.value
                    ):
                        raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
            except ExactRestorePreflightError:
                raise
            except BaseException as error:
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error
            try:
                self._store.clear()
            except BaseException:
                if not self._store.reload_and_confirm_successor(None):
                    raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from None

    async def _reconcile_inflight(
        self,
        record: ExactRestoreRecord,
        authority: ExactRestoreAuthority,
        authority_window: _LiveAuthorityWindow,
        epoch: int,
    ) -> ExactRestoreRecord:
        inflight = record.inflight
        if inflight is None:
            return record
        if (
            authority.permit_crash_resume is not True
            or authority.crash_resume_inflight_sha256 != inflight.inflight_sha256
        ):
            self._latch(record, ExactRestoreErrorCode.AUTHORITY)
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.AUTHORITY)
        action = record.actions[inflight.index]
        self._require_guard_or_latch(record, epoch, authority, authority_window)
        try:
            observed = await self._fresh(action.role, record)
        except CancelledError:
            raise
        except ExactRestoreRecoveryRequired as error:
            code = (
                ExactRestoreErrorCode.BINDING
                if error.code is ExactRestoreErrorCode.BINDING
                else ExactRestoreErrorCode.UNCERTAIN_WRITE
            )
            self._latch(record, code)
            raise ExactRestoreRecoveryRequired(code) from None
        except BaseException:
            self._latch(record, ExactRestoreErrorCode.UNCERTAIN_WRITE)
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.UNCERTAIN_WRITE) from None
        self._require_guard_or_latch(record, epoch, authority, authority_window)
        if not self._action_satisfied(record, action, observed):
            self._latch(record, ExactRestoreErrorCode.UNCERTAIN_WRITE)
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.UNCERTAIN_WRITE)
        completed = self._complete(
            record,
            action,
            observed,
            observed,
            ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN,
            pre_state_sha256=inflight.pre_state_sha256,
        )
        self._save_exact(completed)
        return completed

    async def _write_action(
        self,
        record: ExactRestoreRecord,
        action: ExactRestoreAction,
        device: ExactRestoreDevice,
        epoch: int,
        authority: ExactRestoreAuthority,
        authority_window: _LiveAuthorityWindow,
    ) -> None:
        def guard() -> bool:
            return self._guard_allows(
                epoch, authority, authority_window
            ) and self._initial_sentinel_wire_baseline_is_fresh(record)

        baseline = record.baseline.for_role(action.role)
        safe = next(target for target in record.safe_targets if target.role is action.role)
        if action.kind is ExactRestoreActionKind.SAFE_FALLBACK:
            # Linkage detach, TimerOFF and the bounded manual triple are one control frame.  In
            # particular, no manual-only write is ever sent to a still-linked async slave.
            await device.write_target(_safe_fallback_target(safe), guard=guard)
        elif action.kind is ExactRestoreActionKind.QUALIFY_SENTINEL:
            if action.sentinel_slot is None or action.sentinel_image_sha256 is None:
                raise AssertionError("validated sentinel action has no slot")
            sentinel_slot, sentinel_image = _sentinel_target(baseline.schedule.image_bytes)
            if (
                sentinel_slot != action.sentinel_slot
                or hashlib.sha256(sentinel_image).hexdigest() != action.sentinel_image_sha256
            ):
                raise AssertionError("sentinel action does not match its immutable target")
            await device.restore_schedule_image(sentinel_image, guard=guard)
        elif action.kind is ExactRestoreActionKind.RESTORE_SCHEDULE:
            await device.restore_schedule_image(baseline.schedule.image_bytes, guard=guard)
        elif action.kind is ExactRestoreActionKind.RESTORE_OUTER:
            await device.write_target(_exact_outer_target(baseline), guard=guard)
        else:
            raise AssertionError(f"unknown restore action {action.kind}")

    async def _fresh(
        self,
        role: ExactRestoreRole,
        record: ExactRestoreRecord,
    ) -> ExactRestoreObservation:
        requested_at = self._now()
        requested_monotonic_ns = self._monotonic_ns()
        value = await self._observe(role)
        received_at = self._now()
        received_monotonic_ns = self._monotonic_ns()
        observation = ExactRestoreObservation.model_validate(
            value.model_dump(mode="json") if isinstance(value, ExactRestoreObservation) else value
        )
        baseline = record.baseline.for_role(role)
        if (
            observation.role is not role
            or observation.identity_binding_sha256 != baseline.identity_binding_sha256
        ):
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.BINDING)
        max_age = timedelta(seconds=record.baseline.verification_policy.max_observation_age_seconds)
        if (
            observation.requested_at < requested_at
            or observation.received_at > received_at
            or observation.requested_monotonic_ns < requested_monotonic_ns
            or observation.received_monotonic_ns > received_monotonic_ns
            or received_monotonic_ns - observation.observed_monotonic_ns
            > int(max_age.total_seconds() * 1_000_000_000)
        ):
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.DEVICE_IO)
        return observation

    def _action_satisfied(
        self,
        record: ExactRestoreRecord,
        action: ExactRestoreAction,
        observation: ExactRestoreObservation,
    ) -> bool:
        baseline = record.baseline.for_role(action.role)
        safe = next(target for target in record.safe_targets if target.role is action.role)
        outer = observation.outer
        safe_outer = OuterControlSnapshot(
            enabled=True,
            timer_enabled=False,
            linkage=LinkageRole.INDEPENDENT,
            mode=safe.mode,
            power=safe.power,
            frequency=safe.frequency,
        )
        if action.kind is ExactRestoreActionKind.SAFE_FALLBACK:
            return outer == safe_outer
        if action.kind is ExactRestoreActionKind.QUALIFY_SENTINEL:
            slot = action.sentinel_slot
            if slot is None:
                return False
            expected_slot, expected = _sentinel_target(baseline.schedule.image_bytes)
            return (
                outer == safe_outer
                and slot == expected_slot
                and action.sentinel_image_sha256 == observation.schedule.image_sha256
                and observation.schedule.image_bytes == expected
            )
        if action.kind is ExactRestoreActionKind.RESTORE_SCHEDULE:
            return (
                outer == safe_outer
                and observation.schedule.image_sha256 == baseline.schedule.image_sha256
                and observation.schedule.image_bytes == baseline.schedule.image_bytes
            )
        if action.kind is ExactRestoreActionKind.RESTORE_OUTER:
            return self._matches_baseline(baseline, observation)
        return False

    @staticmethod
    def _matches_baseline(
        baseline: ExactRestoreDeviceBaseline,
        observation: ExactRestoreObservation,
    ) -> bool:
        return (
            observation.identity_binding_sha256 == baseline.identity_binding_sha256
            and observation.outer == baseline.outer
            and observation.schedule.image_bytes == baseline.schedule.image_bytes
        )

    def _complete(
        self,
        record: ExactRestoreRecord,
        action: ExactRestoreAction,
        before: ExactRestoreObservation,
        after: ExactRestoreObservation,
        outcome: ExactRestoreActionOutcome,
        *,
        pre_state_sha256: str | None = None,
    ) -> ExactRestoreRecord:
        result = ExactRestoreActionResult(
            index=action.index,
            action_id=action.action_id,
            outcome=outcome,
            pre_state_sha256=pre_state_sha256 or before.raw_frame_sha256,
            post_state_sha256=after.raw_frame_sha256,
            completed_at=self._now(),
        )
        return _validated_record_update(
            record,
            phase=ExactRestorePhase.RESTORING,
            completed_actions=(*record.completed_actions, result),
            inflight=None,
            updated_at=self._now(),
        )

    def _latch(
        self,
        record: ExactRestoreRecord,
        code: ExactRestoreErrorCode,
    ) -> ExactRestoreRecord:
        latched = _validated_record_update(
            record,
            phase=ExactRestorePhase.RECOVERY_REQUIRED,
            error_code=code,
            updated_at=self._now(),
        )
        self._save_exact(latched)
        return latched

    def _load(self) -> ExactRestoreRecord:
        payload = self._store.load()
        if payload is None:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        try:
            return ExactRestoreRecord.model_validate(payload)
        except (TypeError, ValueError) as error:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error

    def _require_operation_not_finalized(self, operation_id: str) -> None:
        receipt_store = self._qualification_receipts
        if receipt_store is None:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        try:
            finalization = receipt_store.load_operation_finalization(operation_id)
        except BaseException as error:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from error
        if finalization is None:
            return
        if not isinstance(finalization, Mapping):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        # Any valid durable entry means this operation already reached FINAL_VERIFIED.  It is
        # never safe to reuse its operation id or any still-live attended grant for a new plan.
        raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)

    def _save_exact(self, record: ExactRestoreRecord) -> None:
        payload = record.model_dump(mode="json")
        try:
            self._store.save(payload)
        except BaseException:
            if not self._store.reload_and_confirm_successor(payload):
                raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL) from None

    def _validate_authority(
        self,
        record: ExactRestoreRecord,
        authority: ExactRestoreAuthority,
        *,
        qualification_receipt_sha256: str | None = None,
        require_current_context: bool = False,
    ) -> None:
        wall_time = self._now()
        monotonic_ns = self._monotonic_ns()
        boot_identity_sha256 = self._boot_identity_sha256()
        expected_qualification = (
            qualification_receipt_sha256
            if qualification_receipt_sha256 is not None
            else record.qualification_receipt_sha256
        )
        if (
            authority.operation_id != record.operation_id
            or authority.cycle is not record.cycle
            or authority.baseline_sha256 != record.baseline_sha256
            or authority.action_plan_sha256 != record.action_plan_sha256
            or authority.verification_policy_sha256
            != record.baseline.verification_policy.policy_sha256
            or authority.qualification_receipt_sha256 != expected_qualification
            or (
                require_current_context
                and authority.journal_context_sha256 != record.authority_context_sha256
            )
            or authority.boot_identity_sha256 != boot_identity_sha256
            or not authority.issued_at <= wall_time <= authority.expires_at
            or not authority.issued_monotonic_ns <= monotonic_ns <= authority.deadline_monotonic_ns
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
        expected_scope = (
            ExactRestoreAuthorityScope.EXACT_BASELINE_ONLY
            if record.cycle is ExactRestoreCycle.BASELINE_RESTORE
            else ExactRestoreAuthorityScope.BOOTSTRAP_QUALIFICATION
        )
        if authority.scope is not expected_scope:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)

    @staticmethod
    def _validate_crash_resume_binding(
        record: ExactRestoreRecord,
        authority: ExactRestoreAuthority,
    ) -> None:
        inflight = record.inflight
        if inflight is None:
            valid = (
                authority.permit_crash_resume is False
                and authority.crash_resume_inflight_sha256 is None
            )
        else:
            valid = (
                authority.permit_crash_resume is True
                and authority.crash_resume_inflight_sha256 == inflight.inflight_sha256
            )
        if not valid:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)

    def _require_live_authority(self, record: ExactRestoreRecord) -> ExactRestoreAuthority:
        authority = record.authority
        activation = record.authority_activation
        if (
            authority is None
            or activation is None
            or activation.authority_sha256 != authority.authority_sha256
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
        self._validate_authority(record, authority)
        return authority

    def _activate_authority(
        self,
        authority: ExactRestoreAuthority,
    ) -> ExactRestoreAuthorityActivation:
        accepted_wall = self._now()
        accepted_monotonic_ns = self._monotonic_ns()
        boot_identity_sha256 = self._boot_identity_sha256()
        if (
            authority.boot_identity_sha256 != boot_identity_sha256
            or not authority.issued_at <= accepted_wall <= authority.expires_at
            or not authority.issued_monotonic_ns
            <= accepted_monotonic_ns
            <= authority.deadline_monotonic_ns
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
        return ExactRestoreAuthorityActivation(
            authority_sha256=authority.authority_sha256,
            boot_identity_sha256=boot_identity_sha256,
            accepted_wall=accepted_wall,
            accepted_monotonic_ns=accepted_monotonic_ns,
            deadline_monotonic_ns=authority.deadline_monotonic_ns,
        )

    def _start_authority_window(
        self,
        record: ExactRestoreRecord,
        authority: ExactRestoreAuthority,
    ) -> _LiveAuthorityWindow:
        activation = record.authority_activation
        if activation is None or activation.authority_sha256 != authority.authority_sha256:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
        wall_time = self._now()
        monotonic_ns = self._monotonic_ns()
        boot_identity_sha256 = self._boot_identity_sha256()
        if (
            boot_identity_sha256 != activation.boot_identity_sha256
            or wall_time < activation.accepted_wall
            or not authority.issued_at <= wall_time <= authority.expires_at
            or monotonic_ns < activation.accepted_monotonic_ns
            or monotonic_ns > activation.deadline_monotonic_ns
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
        return _LiveAuthorityWindow(
            authority_sha256=authority.authority_sha256,
            boot_identity_sha256=boot_identity_sha256,
            deadline_monotonic_ns=activation.deadline_monotonic_ns,
            last_wall_time=wall_time,
            last_monotonic_ns=monotonic_ns,
        )

    def _authority_window_allows(
        self,
        window: _LiveAuthorityWindow,
        authority: ExactRestoreAuthority,
    ) -> bool:
        try:
            wall_time = self._now()
            monotonic_ns = self._monotonic_ns()
            boot_identity_sha256 = self._boot_identity_sha256()
        except ExactRestoreError:
            return False
        allowed = (
            authority.authority_sha256 == window.authority_sha256
            and boot_identity_sha256 == window.boot_identity_sha256
            and monotonic_ns >= window.last_monotonic_ns
            and monotonic_ns <= window.deadline_monotonic_ns
            and wall_time >= window.last_wall_time
            and authority.issued_at <= wall_time <= authority.expires_at
        )
        if allowed:
            window.last_wall_time = wall_time
            window.last_monotonic_ns = monotonic_ns
        return allowed

    def _require_authority_window(
        self,
        window: _LiveAuthorityWindow,
        authority: ExactRestoreAuthority,
    ) -> None:
        if not self._authority_window_allows(window, authority):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)

    def _guard_allows(
        self,
        epoch: int,
        authority: ExactRestoreAuthority,
        authority_window: _LiveAuthorityWindow,
    ) -> bool:
        return (
            self._guard.permitted is True
            and self._guard.epoch == epoch
            and self._authority_window_allows(authority_window, authority)
        )

    def _require_guard(
        self,
        epoch: int,
        authority: ExactRestoreAuthority,
        authority_window: _LiveAuthorityWindow,
    ) -> None:
        self._require_authority_window(authority_window, authority)
        if self._guard.permitted is not True or self._guard.epoch != epoch:
            raise ExactRestoreRecoveryRequired(ExactRestoreErrorCode.SAFETY_INTERLOCK)

    def _require_guard_or_latch(
        self,
        record: ExactRestoreRecord,
        epoch: int,
        authority: ExactRestoreAuthority,
        authority_window: _LiveAuthorityWindow,
    ) -> None:
        try:
            self._require_guard(epoch, authority, authority_window)
        except ExactRestoreRecoveryRequired as error:
            self._latch(record, error.code)
            raise

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        return value

    @staticmethod
    def _sentinel_has_possible_write(record: ExactRestoreRecord) -> bool:
        return record.inflight is not None or any(
            result.outcome is not ExactRestoreActionOutcome.ALREADY_SATISFIED
            for result in record.completed_actions
        )

    def _initial_sentinel_baseline_freshness(
        self,
        record: ExactRestoreRecord,
    ) -> tuple[bool, dict[str, Any]]:
        if record.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION:
            return True, {}
        try:
            now = self._now()
        except ExactRestorePreflightError:
            return False, {"reason": "clock_unavailable"}
        captured_at = record.baseline.captured_at
        policy = record.baseline.verification_policy
        maximum_age_ns = int(policy.max_observation_age_seconds * 1_000_000_000)
        maximum_pair_gap_ns = int(policy.max_final_pair_gap_seconds * 1_000_000_000)
        if now < captured_at:
            return False, {
                "reason": "clock_regression",
                "capture_age_ms": _timedelta_ns(now - captured_at) // 1_000_000,
                "maximum_age_ms": maximum_age_ns // 1_000_000,
                "maximum_pair_gap_ms": maximum_pair_gap_ns // 1_000_000,
            }
        capture_age_ns = _timedelta_ns(now - captured_at)
        # The record stores pair completion rather than both private host timestamps. Treat the
        # older member as if it completed one approved pair-gap earlier, which is equivalent to
        # adding that gap to the observed age at every first-write admission.
        conservative_age_ns = capture_age_ns + maximum_pair_gap_ns
        details = {
            "reason": "baseline_age_exceeded",
            "capture_age_ms": capture_age_ns // 1_000_000,
            "conservative_age_ms": conservative_age_ns // 1_000_000,
            "maximum_age_ms": maximum_age_ns // 1_000_000,
            "maximum_pair_gap_ms": maximum_pair_gap_ns // 1_000_000,
        }
        return conservative_age_ns <= maximum_age_ns, details

    def _initial_sentinel_baseline_is_fresh(self, record: ExactRestoreRecord) -> bool:
        fresh, _diagnostic = self._initial_sentinel_baseline_freshness(record)
        return fresh

    def _require_fresh_initial_sentinel_baseline(self, record: ExactRestoreRecord) -> None:
        # Once an inflight intent or a non-skip result exists, restoration must remain available
        # indefinitely; rejecting recovery because the original snapshot aged would strand a
        # possibly modified controller.  Freshness is only a gate before the first possible write.
        if self._sentinel_has_possible_write(record):
            return
        fresh, diagnostic = self._initial_sentinel_baseline_freshness(record)
        if not fresh:
            raise ExactRestorePreflightError(
                ExactRestoreErrorCode.BASELINE_EXPIRED,
                diagnostic=diagnostic,
            )

    def _initial_sentinel_wire_baseline_is_fresh(self, record: ExactRestoreRecord) -> bool:
        # ``record.inflight`` is already durable at the real wire boundary.  Unlike the admission
        # helper above, do not let that newly-created intent bypass the final under-lock age check.
        if record.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION:
            return True
        if any(
            result.outcome is not ExactRestoreActionOutcome.ALREADY_SATISFIED
            for result in record.completed_actions
        ):
            return True
        return self._initial_sentinel_baseline_is_fresh(record)

    def _monotonic_ns(self) -> int:
        value = self._monotonic_clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.JOURNAL)
        return value

    def _boot_identity_sha256(self) -> str:
        try:
            value = self._boot_identity()
        except BaseException as error:
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY) from error
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ExactRestorePreflightError(ExactRestoreErrorCode.AUTHORITY)
        return value


__all__ = [
    "ExactRestoreAction",
    "ExactRestoreActionKind",
    "ExactRestoreActionOutcome",
    "ExactRestoreAuthority",
    "ExactRestoreAuthorityActivation",
    "ExactRestoreAuthorityScope",
    "ExactRestoreBaseline",
    "ExactRestoreController",
    "ExactRestoreCycle",
    "ExactRestoreDeviceBaseline",
    "ExactRestoreError",
    "ExactRestoreErrorCode",
    "ExactRestoreEvidenceReference",
    "ExactRestoreFinalEvidence",
    "ExactRestoreObservation",
    "ExactRestorePhase",
    "ExactRestorePreflightError",
    "ExactRestoreQualificationReceiptStore",
    "ExactRestoreReceipt",
    "ExactRestoreRecord",
    "ExactRestoreRecoveryRequired",
    "ExactRestoreRole",
    "ExactRestoreVerificationPolicy",
    "ExactScheduleImage",
    "OuterControlSnapshot",
    "RestorePowerPolicy",
    "SafeManualTarget",
    "build_exact_restore_plan",
    "prepare_exact_restore_record",
    "prepare_qualified_final_restore_record",
    "system_boot_identity_sha256",
    "system_boottime_ns",
]
