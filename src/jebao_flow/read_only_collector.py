"""Identity-bound, write-free capture of one Jebao device pair.

The collector deliberately bypasses the long-running observer and every hardware-write adapter.
Each device is rediscovered, connected through a fresh authenticated session, and read exactly
once with unsolicited reports disabled. The preserved raw artifact is the exact Gizwits wire
frame read from TCP for the accepted state response. Authentication frames are never retained.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import stat
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Never, Protocol

from jebao_flow.config import AppConfig, DeviceConfig, DeviceType
from jebao_flow.physical_identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    normalize_mac_address,
    physical_identity_key,
)
from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.discovery import DiscoveryProvider
from jebao_flow.protocol.errors import (
    AuthenticationError,
    ProtocolConnectionError,
    ProtocolDecodeError,
    ProtocolError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES, decode_schedule
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LocalWavemakerProScheduleSnapshot,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION, RawStateCapture
from jebao_flow.source_attestation import (
    CollectorSourceAttestation,
    SourceAttestationError,
    validate_collector_source_attestation,
)

CAPTURE_SCHEMA_VERSION = 1
RAW_FORMAT = "gizwits-gagent-wire-frame-v1"
CAPTURE_KIND = "explicit_state_reply_wire_frame"
TRANSPORT_ACTION = "0x03"
_ARTIFACT_ID_PREFIX = "JFC"
_PLAN_ID_PREFIX = "JFP"
_SERIES_ID_PREFIX = "JFS"
PILOT_PLAN_SCHEMA_VERSION = 2
PILOT_SERIES_SCHEMA_VERSION = 1
MAX_PILOT_PAIR_COUNT = 10_000
_SAFE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
_SUMMARY_FIELDS = (
    "SwitchON",
    "TimerON",
    "Linkage",
    "Mode",
    "Flow",
    "Frequency",
    "AutoMode",
    "AutoFlow",
    "AutoFreq",
)


class CollectorError(RuntimeError):
    """Base class carrying a public, privacy-safe failure code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CollectorPreflightError(CollectorError):
    """Raised before any network read when the private plan is not safe."""


class ArtifactStoreError(CollectorError):
    """Raised when a private capture bundle cannot be committed or verified."""


class DurabilityUnconfirmedError(ArtifactStoreError):
    """Raised after an operation whose final filesystem durability is unknown."""


class PilotTerminalError(ArtifactStoreError):
    """Privacy-safe terminal pilot result with stable public artifact identifiers."""

    def __init__(
        self,
        code: str,
        *,
        plan_artifact_id: str,
        series_id: str,
        plan_sha256: str,
        abort_sha256: str | None,
        durability_unknown: bool,
    ) -> None:
        values = (code, plan_artifact_id, series_id)
        if any(
            not value
            or len(value) > 80
            or any(character not in _SAFE_ID_CHARACTERS for character in value)
            for value in values
        ):
            raise ValueError("pilot_terminal_metadata_invalid")
        if not (
            len(plan_sha256) == 64
            and all(character in "0123456789abcdef" for character in plan_sha256)
            and (
                abort_sha256 is None
                or (
                    len(abort_sha256) == 64
                    and all(
                        character in "0123456789abcdef"
                        for character in abort_sha256
                    )
                )
            )
            and isinstance(durability_unknown, bool)
        ):
            raise ValueError("pilot_terminal_metadata_invalid")
        super().__init__(code)
        self.plan_artifact_id = plan_artifact_id
        self.series_id = series_id
        self.plan_sha256 = plan_sha256
        self.abort_sha256 = abort_sha256
        self.durability_unknown = durability_unknown


class ReadOnlySession(Protocol):
    """Narrow transport surface available to the collector."""

    async def connect(self) -> None: ...

    async def authenticate(self) -> bytes: ...

    async def read_raw_state_capture(
        self,
        *,
        accept_reports: bool = False,
    ) -> RawStateCapture: ...

    async def disconnect(self) -> None: ...

    def quarantine(self) -> None: ...


DiscoveryFactory = Callable[[], DiscoveryProvider]
SessionFactory = Callable[[str], ReadOnlySession]
UtcClock = Callable[[], datetime]
MonotonicClock = Callable[[], int]


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    """One logical role bound to private identifiers that are never serialized."""

    logical_id: str
    product_key: str
    identity_binding_sha256: str
    vendor_device_id: str = field(repr=False)
    mac_address: str = field(repr=False)
    config_fingerprint: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResolvedCaptureEndpoint:
    logical_id: str
    product_key: str
    identity_binding_sha256: str
    address: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ClockStamp:
    utc: datetime
    monotonic_ns: int


@dataclass(frozen=True, slots=True)
class DeviceSample:
    logical_id: str
    expected_identity_binding_sha256: str
    observed_identity_binding_sha256_before: str | None
    observed_identity_binding_sha256_after: str | None
    observed_endpoint_token_before: str | None
    observed_endpoint_token_after: str | None
    product_key: str
    status: str
    attempt_started: ClockStamp
    attempt_completed: ClockStamp
    identity_before_started: ClockStamp
    identity_before_completed: ClockStamp
    identity_after_started: ClockStamp | None = None
    identity_after_completed: ClockStamp | None = None
    read_started: ClockStamp | None = None
    read_completed: ClockStamp | None = None
    raw_wire_frame: bytes | None = field(default=None, repr=False)
    state_summary: dict[str, Any] | None = None
    state_observation: dict[str, Any] | None = None
    failure_code: str | None = None
    failure_class: str | None = None
    failure_phase: str | None = None


class SampleCaptureCancelled(asyncio.CancelledError):
    """Cancellation carrying a complete raw frame that must be committed before propagation."""

    def __init__(self, sample: DeviceSample) -> None:
        super().__init__("capture_cancelled_after_read")
        self.sample = sample


@dataclass(frozen=True, slots=True)
class PairCapture:
    status: str
    started: ClockStamp
    completed: ClockStamp
    samples: tuple[DeviceSample, DeviceSample]
    pair_completion_gap_ns: int | None


@dataclass(frozen=True, slots=True)
class CaptureContext:
    plan_artifact_id: str
    plan_sha256: str
    epoch: str
    sample_index: int


@dataclass(frozen=True, slots=True)
class PublicArtifactMetadata:
    artifact_id: str
    status: str
    utc_started: str
    utc_completed: str
    expected_identity_bindings_sha256: tuple[str, str]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class PilotPlanReference:
    plan_artifact_id: str
    series_id: str
    plan_sha256: str
    epoch: str
    planned_pair_count: int
    requested_cadence_ns: int
    series_directory: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class PublicPilotMetadata:
    plan_artifact_id: str
    series_id: str
    plan_sha256: str
    series_sha256: str
    status: str
    validity_scope: str
    q2_boundary_classification: str
    utc_started: str
    utc_completed: str
    planned_pair_count: int
    completed_pair_count: int
    accepted_pair_count: int
    rejected_pair_count: int
    read_failure_pair_count: int
    expected_identity_bindings_sha256: tuple[str, str]


@dataclass(frozen=True, slots=True)
class VerifiedPilotInterval:
    """Public host timing copied from an already verified private manifest."""

    started_utc: str
    completed_utc: str
    started_monotonic_ns: int
    completed_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class VerifiedPilotRawSample:
    """One accepted sample with immutable raw evidence and privacy-safe provenance."""

    role: str
    identity_binding_sha256: str
    sample_manifest_sha256: str
    raw_wire_frame_sha256: str
    attempt: VerifiedPilotInterval
    identity_before: VerifiedPilotInterval
    read: VerifiedPilotInterval
    identity_after: VerifiedPilotInterval
    raw_wire_frame: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class VerifiedPilotPairArtifact:
    """An accepted pair extracted only after complete-series and pair re-verification."""

    plan_artifact_id: str
    plan_sha256: str
    series_id: str
    series_sha256: str
    ordinal: int
    pair_manifest_sha256: str
    attempt: VerifiedPilotInterval
    pair_completion_gap_ns: int
    samples: tuple[VerifiedPilotRawSample, VerifiedPilotRawSample]


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CollectorPreflightError("clock_not_timezone_aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _public_verified_interval(value: dict[str, Any]) -> VerifiedPilotInterval:
    """Convert an interval only after the enclosing verifier accepted it."""

    return VerifiedPilotInterval(
        started_utc=value["started_utc"],
        completed_utc=value["completed_utc"],
        started_monotonic_ns=value["started_monotonic_ns"],
        completed_monotonic_ns=value["completed_monotonic_ns"],
    )


def _stamp(utc_clock: UtcClock, monotonic_clock: MonotonicClock) -> ClockStamp:
    return ClockStamp(utc=utc_clock(), monotonic_ns=monotonic_clock())


def _fingerprint_for(config: DeviceConfig) -> str:
    source = config.model_dump(
        mode="json",
        exclude={"address", "discovery", "name"},
    )
    return configuration_fingerprint(source)


def _capture_target(config: DeviceConfig) -> CaptureTarget:
    identity = config.identity
    if (
        identity is None
        or identity.device_id is None
        or identity.mac_address is None
        or config.product_key is None
    ):
        raise CollectorPreflightError("capture_identity_incomplete")
    if not config.enabled:
        raise CollectorPreflightError("capture_device_disabled")
    if (
        config.type is not DeviceType.WAVEMAKER
        or config.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
    ):
        raise CollectorPreflightError("capture_device_not_local_wavemaker_pro")

    fingerprint = _fingerprint_for(config)
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=identity.device_id,
        mac_address=identity.mac_address,
        product_key=config.product_key,
        config_fingerprint=fingerprint,
    )
    return CaptureTarget(
        logical_id=config.id,
        product_key=config.product_key,
        identity_binding_sha256=physical_identity_key(binding),
        vendor_device_id=identity.device_id,
        mac_address=normalize_mac_address(identity.mac_address),
        config_fingerprint=fingerprint,
    )


def select_capture_pair(
    config: AppConfig,
    first_id: str,
    second_id: str,
) -> tuple[CaptureTarget, CaptureTarget]:
    """Validate a locked private config and select two distinct Pro controllers."""

    if config.runtime.dry_run is not True:
        raise CollectorPreflightError("runtime_not_dry_run")
    if any(device.control.allow_hardware_writes for device in config.devices):
        raise CollectorPreflightError("hardware_writes_not_fully_locked")
    if first_id == second_id:
        raise CollectorPreflightError("capture_pair_not_distinct")

    devices_by_id = {device.id: device for device in config.devices}
    try:
        first = _capture_target(devices_by_id[first_id])
        second = _capture_target(devices_by_id[second_id])
    except KeyError as error:
        raise CollectorPreflightError("capture_device_unknown") from error
    if first.identity_binding_sha256 == second.identity_binding_sha256:
        raise CollectorPreflightError("capture_binding_not_distinct")
    return first, second


def resolve_exact_endpoint(
    target: CaptureTarget,
    discovered: Sequence[DiscoveredDevice],
) -> ResolvedCaptureEndpoint:
    """Resolve one target while rejecting every partial or endpoint identity collision."""

    def normalized_mac(candidate: DiscoveredDevice) -> str | None:
        if candidate.mac_address is None:
            return None
        try:
            return normalize_mac_address(candidate.mac_address)
        except ValueError:
            return None

    related = [
        candidate
        for candidate in discovered
        if candidate.device_id == target.vendor_device_id
        or normalized_mac(candidate) == target.mac_address
    ]
    exact = [
        candidate
        for candidate in related
        if candidate.device_id == target.vendor_device_id
        and normalized_mac(candidate) == target.mac_address
        and candidate.product_key == target.product_key
    ]
    if len(related) != 1 or len(exact) != 1:
        raise CollectorError("identity_not_exactly_resolved")

    candidate = exact[0]
    endpoint_claims = [item for item in discovered if item.address == candidate.address]
    if len(endpoint_claims) != 1:
        raise CollectorError("identity_endpoint_ambiguous")

    observed = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=candidate.device_id,
        mac_address=candidate.mac_address or "",
        product_key=candidate.product_key or "",
        config_fingerprint=target.config_fingerprint,
    )
    observed_key = physical_identity_key(observed)
    if observed_key != target.identity_binding_sha256:
        raise CollectorError("identity_binding_mismatch")
    return ResolvedCaptureEndpoint(
        logical_id=target.logical_id,
        product_key=target.product_key,
        identity_binding_sha256=observed_key,
        address=candidate.address,
    )


def _state_summary(
    product_key: str,
    raw_status: bytes,
    *,
    validity_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = get_product_schema(product_key)
    values = schema.decode_status(raw_status)
    schedule = decode_schedule(
        product_key,
        raw_status,
        enabled=bool(values.get(schema.timer_attribute, False)),
    )
    summary: dict[str, Any] = {
        "schema_name": schema.name,
        "raw_size": len(raw_status),
        "active_problems": list(schema.active_problems(values)),
        "fields": {name: values[name] for name in _SUMMARY_FIELDS if name in values},
    }
    if schedule is not None:
        summary["device_local_time"] = (
            schedule.device_local_time.isoformat()
            if schedule.device_local_time is not None
            else None
        )
        summary["schedule_entry_count"] = len(schedule.entries)
        summary["schedule_invalid_slots"] = list(schedule.invalid_slots)
    if product_key == LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
        snapshot = LocalWavemakerProScheduleSnapshot.from_status(raw_status)
        image = snapshot.image
        summary["schedule_image_sha256"] = hashlib.sha256(image).hexdigest()
        try:
            snapshot.validate()
        except ValueError:
            summary["schedule_parameter_ranges_valid"] = False
        else:
            summary["schedule_parameter_ranges_valid"] = True
        _validate_pro_summary(summary, validity_policy or capture_validity_policy())
    return summary


def _capture_validity_policy_v2() -> dict[str, Any]:
    """Return the immutable v2 policy retained for offline verification."""

    return {
        "predicate_version": "local-wavemaker-pro-acquisition-v2",
        "acquisition_predicate": {
            "serial_payload_size_bytes": 453,
            "wire_frame_storage": "exact_observed_length_no_normalization",
            "status_payload_size_bytes": 452,
            "required_action": TRANSPORT_ACTION,
            "attributes": {
                "SwitchON": {"type": "boolean"},
                "TimerON": {"type": "boolean"},
                "Linkage": {
                    "type": "enum",
                    "allowed": ["independent", "master", "sync_slave", "async_slave"],
                },
                "Mode": {"type": "enum", "allowed": list(LOCAL_WAVEMAKER_PRO_MODES)},
                "Flow": {"type": "integer", "minimum": 0, "maximum": 100},
                "Frequency": {"type": "integer", "minimum": 0, "maximum": 100},
                "AutoMode": {
                    "type": "enum",
                    "allowed": list(LOCAL_WAVEMAKER_PRO_MODES),
                },
                "AutoFlow": {"type": "integer", "minimum": 0, "maximum": 100},
                "AutoFreq": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "identity_check": "same_exact_udp_binding_before_and_after_tcp_read",
        },
        "state_observation_predicate": {
            "affects_acquisition_outcome": False,
            "active_faults_empty": True,
            "schedule_invalid_slots_empty": True,
            "schedule_parameter_ranges_valid": True,
            "device_local_time_present": True,
        },
    }


def capture_validity_policy() -> dict[str, Any]:
    """Return the policy to embed in newly prepared pilot plans."""

    return _capture_validity_policy_v2()


def _validate_recorded_capture_policy(policy: object) -> dict[str, Any]:
    """Validate one digest-pinned policy without consulting the current policy factory."""

    if not isinstance(policy, dict):
        raise ArtifactStoreError("pilot_validity_policy_invalid")
    if policy.get("predicate_version") != "local-wavemaker-pro-acquisition-v2":
        raise ArtifactStoreError("pilot_validity_policy_unsupported")
    if policy != _capture_validity_policy_v2():
        raise ArtifactStoreError("pilot_validity_policy_invalid")
    acquisition = policy.get("acquisition_predicate")
    observation = policy.get("state_observation_predicate")
    if not isinstance(acquisition, dict) or not isinstance(observation, dict):
        raise ArtifactStoreError("pilot_validity_policy_invalid")
    expected_transport = {
        "serial_payload_size_bytes": 453,
        "wire_frame_storage": "exact_observed_length_no_normalization",
        "status_payload_size_bytes": 452,
        "required_action": TRANSPORT_ACTION,
        "identity_check": "same_exact_udp_binding_before_and_after_tcp_read",
    }
    if any(acquisition.get(key) != value for key, value in expected_transport.items()):
        raise ArtifactStoreError("pilot_validity_policy_invalid")
    attributes = acquisition.get("attributes")
    if not isinstance(attributes, dict) or set(attributes) != set(_SUMMARY_FIELDS):
        raise ArtifactStoreError("pilot_validity_policy_invalid")
    if observation != {
        "affects_acquisition_outcome": False,
        "active_faults_empty": True,
        "schedule_invalid_slots_empty": True,
        "schedule_parameter_ranges_valid": True,
        "device_local_time_present": True,
    }:
        raise ArtifactStoreError("pilot_validity_policy_invalid")
    return policy


def _validate_pro_summary(summary: dict[str, Any], policy: dict[str, Any]) -> None:
    fields = summary["fields"]
    acquisition = _validate_recorded_capture_policy(policy)["acquisition_predicate"]
    for name, rule in acquisition["attributes"].items():
        value = fields.get(name)
        if rule["type"] == "boolean":
            if not isinstance(value, bool):
                raise CollectorError("status_boolean_invalid")
            continue
        if rule["type"] == "enum":
            if not isinstance(value, str) or value not in rule["allowed"]:
                raise CollectorError("status_enum_invalid")
            continue
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < rule["minimum"]
            or value > rule["maximum"]
        ):
            raise CollectorError("status_numeric_range_invalid")


def _state_observation(summary: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "active_faults_empty": not bool(summary.get("active_problems")),
        "schedule_invalid_slots_empty": not bool(summary.get("schedule_invalid_slots")),
        "schedule_parameter_ranges_valid": summary.get("schedule_parameter_ranges_valid") is True,
        "device_local_time_present": summary.get("device_local_time") is not None,
    }
    return {
        "predicate_version": "local-wavemaker-pro-state-observation-v1",
        "affects_acquisition_outcome": False,
        "passed": all(checks.values()),
        "checks": checks,
    }


def _failure_code(error: BaseException) -> str:
    if isinstance(error, CollectorError):
        return error.code
    if isinstance(error, ProtocolTimeoutError):
        return "protocol_timeout"
    if isinstance(error, ProtocolConnectionError):
        return "protocol_connection_error"
    if isinstance(error, AuthenticationError):
        return "authentication_error"
    if isinstance(error, UnexpectedResponseError):
        return "unexpected_response"
    if isinstance(error, ProtocolDecodeError):
        return "protocol_decode_error"
    if isinstance(error, ProtocolError):
        return "protocol_error"
    if isinstance(error, TimeoutError):
        return "network_timeout"
    if isinstance(error, OSError):
        return "network_io_error"
    if isinstance(error, (KeyError, TypeError, ValueError)):
        return "invalid_status_payload"
    return "capture_error"


def _failure_class(error: BaseException) -> str:
    if isinstance(error, CollectorError):
        return "CollectorError"
    for error_type in (
        ProtocolTimeoutError,
        ProtocolConnectionError,
        AuthenticationError,
        UnexpectedResponseError,
        ProtocolDecodeError,
        ProtocolError,
    ):
        if isinstance(error, error_type):
            return error_type.__name__
    if isinstance(error, TimeoutError):
        return "TimeoutError"
    if isinstance(error, OSError):
        return "OSError"
    if isinstance(error, KeyError):
        return "KeyError"
    if isinstance(error, TypeError):
        return "TypeError"
    if isinstance(error, ValueError):
        return "ValueError"
    return "Exception"


def _pilot_abort_code(error: BaseException) -> str:
    if isinstance(error, SampleCaptureCancelled):
        return "capture_cancelled_after_read"
    if isinstance(error, asyncio.CancelledError):
        return "capture_cancelled"
    if isinstance(error, KeyboardInterrupt):
        return "keyboard_interrupt"
    if isinstance(error, CollectorError):
        return error.code
    if isinstance(error, OSError):
        return "artifact_io_error"
    return "private_operation_error"


def _pilot_durability_unknown(error: BaseException) -> bool:
    return isinstance(error, (DurabilityUnconfirmedError, OSError))


def _private_endpoint_token(nonce: bytes, address: str) -> str:
    return hashlib.sha256(b"jebao-flow:ephemeral-endpoint:" + nonce + address.encode()).hexdigest()


async def collect_device_sample(
    target: CaptureTarget,
    *,
    discovery_factory: DiscoveryFactory,
    session_factory: SessionFactory,
    discovery_timeout_seconds: float,
    validity_policy: dict[str, Any] | None = None,
    utc_clock: UtcClock = lambda: datetime.now(UTC),
    monotonic_clock: MonotonicClock = time.monotonic_ns,
) -> DeviceSample:
    """Collect one explicit state payload using a fresh identity check and TCP session."""

    attempt_started = _stamp(utc_clock, monotonic_clock)
    identity_before_started = _stamp(utc_clock, monotonic_clock)
    identity_before_completed = identity_before_started
    identity_after_started: ClockStamp | None = None
    identity_after_completed: ClockStamp | None = None
    read_started: ClockStamp | None = None
    read_completed: ClockStamp | None = None
    raw_wire_frame: bytes | None = None
    session: ReadOnlySession | None = None
    observed_binding_before: str | None = None
    observed_binding_after: str | None = None
    endpoint_token_before: str | None = None
    endpoint_token_after: str | None = None
    endpoint_nonce = secrets.token_bytes(32)
    failure_phase = "identity_before"
    sample: DeviceSample | None = None
    cancellation: asyncio.CancelledError | None = None

    def failed_sample(error: BaseException, *, code: str | None = None) -> DeviceSample:
        return DeviceSample(
            logical_id=target.logical_id,
            expected_identity_binding_sha256=target.identity_binding_sha256,
            observed_identity_binding_sha256_before=observed_binding_before,
            observed_identity_binding_sha256_after=observed_binding_after,
            observed_endpoint_token_before=endpoint_token_before,
            observed_endpoint_token_after=endpoint_token_after,
            product_key=target.product_key,
            status="acquisition_invalid",
            attempt_started=attempt_started,
            attempt_completed=_stamp(utc_clock, monotonic_clock),
            identity_before_started=identity_before_started,
            identity_before_completed=identity_before_completed,
            identity_after_started=identity_after_started,
            identity_after_completed=identity_after_completed,
            read_started=read_started,
            read_completed=read_completed,
            raw_wire_frame=raw_wire_frame,
            failure_code=code or _failure_code(error),
            failure_class=_failure_class(error),
            failure_phase=failure_phase,
        )

    try:
        try:
            discovered = await discovery_factory().discover(
                timeout_seconds=discovery_timeout_seconds
            )
        finally:
            identity_before_completed = _stamp(utc_clock, monotonic_clock)
        endpoint = resolve_exact_endpoint(target, discovered)
        observed_binding_before = endpoint.identity_binding_sha256
        endpoint_token_before = _private_endpoint_token(endpoint_nonce, endpoint.address)
        session = session_factory(endpoint.address)
        failure_phase = "connect"
        await session.connect()
        failure_phase = "authenticate"
        await session.authenticate()
        failure_phase = "read"
        read_started = _stamp(utc_clock, monotonic_clock)
        try:
            raw_capture = await session.read_raw_state_capture(accept_reports=False)
        finally:
            read_completed = _stamp(utc_clock, monotonic_clock)
        raw_wire_frame = raw_capture.wire_frame

        failure_phase = "identity_after"
        identity_after_started = _stamp(utc_clock, monotonic_clock)
        try:
            discovered_after = await discovery_factory().discover(
                timeout_seconds=discovery_timeout_seconds
            )
        finally:
            identity_after_completed = _stamp(utc_clock, monotonic_clock)
        endpoint_after = resolve_exact_endpoint(target, discovered_after)
        observed_binding_after = endpoint_after.identity_binding_sha256
        endpoint_token_after = _private_endpoint_token(endpoint_nonce, endpoint_after.address)
        if endpoint_after.address != endpoint.address:
            raise CollectorError("identity_endpoint_changed_during_read")
        failure_phase = "validate_status"
        if raw_capture.action != STATE_REPLY_ACTION:
            raise CollectorError("state_reply_action_not_explicit")
        summary = _state_summary(
            endpoint.product_key,
            raw_capture.status_payload,
            validity_policy=validity_policy,
        )
        attempt_completed = _stamp(utc_clock, monotonic_clock)
        sample = DeviceSample(
            logical_id=target.logical_id,
            expected_identity_binding_sha256=target.identity_binding_sha256,
            observed_identity_binding_sha256_before=observed_binding_before,
            observed_identity_binding_sha256_after=observed_binding_after,
            observed_endpoint_token_before=endpoint_token_before,
            observed_endpoint_token_after=endpoint_token_after,
            product_key=target.product_key,
            status="acquisition_valid",
            attempt_started=attempt_started,
            attempt_completed=attempt_completed,
            identity_before_started=identity_before_started,
            identity_before_completed=identity_before_completed,
            identity_after_started=identity_after_started,
            identity_after_completed=identity_after_completed,
            read_started=read_started,
            read_completed=read_completed,
            raw_wire_frame=raw_wire_frame,
            state_summary=summary,
            state_observation=_state_observation(summary),
        )
    except asyncio.CancelledError as error:
        if session is not None:
            session.quarantine()
        if raw_wire_frame is None:
            raise
        cancellation = error
        sample = failed_sample(error, code="capture_cancelled_after_read")
    except Exception as error:
        sample = failed_sample(error)
    finally:
        if session is not None:
            try:
                await session.disconnect()
            except asyncio.CancelledError as error:
                session.quarantine()
                if raw_wire_frame is None:
                    raise
                failure_phase = "disconnect"
                cancellation = error
                sample = failed_sample(error, code="capture_cancelled_after_read")
            except Exception:
                session.quarantine()
    if sample is None:
        raise CollectorError("capture_sample_missing")
    if cancellation is not None:
        raise SampleCaptureCancelled(sample) from cancellation
    return sample


async def collect_pair(
    targets: tuple[CaptureTarget, CaptureTarget],
    *,
    discovery_factory: DiscoveryFactory,
    session_factory: SessionFactory,
    discovery_timeout_seconds: float,
    validity_policy: dict[str, Any] | None = None,
    utc_clock: UtcClock = lambda: datetime.now(UTC),
    monotonic_clock: MonotonicClock = time.monotonic_ns,
) -> PairCapture:
    """Collect A then B so the completion gap is explicit and reproducible."""

    if discovery_timeout_seconds <= 0:
        raise CollectorPreflightError("discovery_timeout_not_positive")
    started = _stamp(utc_clock, monotonic_clock)
    samples = tuple(
        [
            await collect_device_sample(
                target,
                discovery_factory=discovery_factory,
                session_factory=session_factory,
                discovery_timeout_seconds=discovery_timeout_seconds,
                validity_policy=validity_policy,
                utc_clock=utc_clock,
                monotonic_clock=monotonic_clock,
            )
            for target in targets
        ]
    )
    completed = _stamp(utc_clock, monotonic_clock)
    first_completed = samples[0].read_completed
    second_completed = samples[1].read_completed
    pair_gap = (
        second_completed.monotonic_ns - first_completed.monotonic_ns
        if first_completed is not None and second_completed is not None
        else None
    )
    status = (
        "acquisition_valid"
        if all(sample.status == "acquisition_valid" for sample in samples)
        else "acquisition_invalid"
    )
    return PairCapture(
        status=status,
        started=started,
        completed=completed,
        samples=(samples[0], samples[1]),
        pair_completion_gap_ns=pair_gap,
    )


def _safe_artifact_id(value: str) -> str:
    contains_unsafe_character = any(
        character not in _SAFE_ID_CHARACTERS for character in value
    )
    if not value or len(value) > 80 or contains_unsafe_character:
        raise ArtifactStoreError("artifact_id_invalid")
    return value


def _new_artifact_id() -> str:
    return f"{_ARTIFACT_ID_PREFIX}-{secrets.token_hex(16)}"


def _new_plan_id() -> str:
    return f"{_PLAN_ID_PREFIX}-{secrets.token_hex(16)}"


def _new_series_id() -> str:
    return f"{_SERIES_ID_PREFIX}-{secrets.token_hex(16)}"


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _clock_interval(started: ClockStamp, completed: ClockStamp) -> dict[str, Any]:
    return {
        "started_utc": _utc_text(started.utc),
        "completed_utc": _utc_text(completed.utc),
        "started_monotonic_ns": started.monotonic_ns,
        "completed_monotonic_ns": completed.monotonic_ns,
    }


def _optional_clock_interval(
    started: ClockStamp | None,
    completed: ClockStamp | None,
) -> dict[str, Any] | None:
    if started is None and completed is None:
        return None
    if started is None or completed is None:
        raise ArtifactStoreError("capture_timing_incomplete")
    return _clock_interval(started, completed)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_capture_context(context: CaptureContext) -> None:
    _safe_artifact_id(context.plan_artifact_id)
    _safe_artifact_id(context.epoch)
    if not _is_sha256(context.plan_sha256):
        raise ArtifactStoreError("capture_plan_digest_invalid")
    if (
        not isinstance(context.sample_index, int)
        or isinstance(context.sample_index, bool)
        or context.sample_index < 0
    ):
        raise ArtifactStoreError("capture_sample_index_invalid")


def _state_frame_parts(wire_frame: bytes) -> tuple[int, bytes]:
    frame = decode_frame(wire_frame)
    if frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE or not frame.payload:
        raise ValueError("wire frame is not a serial state response")
    return frame.payload[0], frame.payload[1:]


def _observed_explicit_reply(wire_frame: bytes | None) -> bool | None:
    if wire_frame is None:
        return None
    try:
        action, _status = _state_frame_parts(wire_frame)
    except (TypeError, ValueError, ProtocolError):
        return False
    return action == STATE_REPLY_ACTION


def _sample_outcome(sample: DeviceSample) -> str:
    if sample.status == "acquisition_valid":
        return "accepted"
    if sample.raw_wire_frame is not None:
        return "predicate_rejected"
    return "read_failure"


def _failed_predicates(sample: DeviceSample) -> list[str]:
    if sample.status == "acquisition_valid" or sample.raw_wire_frame is None:
        return []
    mapping = {
        "state_reply_action_not_explicit": "required_action",
        "identity_endpoint_changed_during_read": "endpoint_unchanged_during_read",
        "identity_binding_mismatch": "expected_binding_before_and_after_read",
        "identity_not_exactly_resolved": "expected_binding_before_and_after_read",
        "status_boolean_invalid": "attribute_type",
        "status_enum_invalid": "attribute_enum",
        "status_numeric_range_invalid": "attribute_integer_range",
        "invalid_status_payload": "status_payload_decodable",
    }
    return [mapping.get(sample.failure_code or "", "capture_validity_predicate")]


def _read_private_regular_file(path: Path, *, failure_code: str) -> bytes:
    try:
        metadata = path.lstat()
    except (FileNotFoundError, OSError) as error:
        raise ArtifactStoreError(failure_code) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
    ):
        raise ArtifactStoreError(failure_code)
    try:
        return path.read_bytes()
    except OSError as error:
        raise ArtifactStoreError(failure_code) from error


def _residue_entry_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular_file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    return "other"


def _digest_residue_regular_file(path: Path, metadata: os.stat_result) -> str:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise ArtifactStoreError("pilot_residue_entry_changed")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if os.fstat(descriptor).st_size != opened.st_size:
            raise ArtifactStoreError("pilot_residue_entry_changed")
        return digest.hexdigest()
    except ArtifactStoreError:
        raise
    except OSError as error:
        raise ArtifactStoreError("pilot_residue_unreadable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _pilot_residue_inventory(
    series_directory: Path,
    *,
    completed_pair_count: int,
) -> list[dict[str, Any]]:
    """Inventory every non-prefix entry without following or removing residue."""

    if completed_pair_count < 0:
        raise ArtifactStoreError("pilot_residue_prefix_invalid")
    completed_attempts = {
        f"{ordinal:06d}" for ordinal in range(completed_pair_count)
    }
    excluded_root_names = {
        "plan.json",
        "plan.commit.json",
        "started.json",
        "attempts",
        "aborted.json",
        "aborted.commit.json",
    }
    entries: list[dict[str, Any]] = []

    def visit(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ArtifactStoreError("pilot_residue_unreadable") from error
        entry_type = _residue_entry_type(metadata.st_mode)
        digest = (
            _digest_residue_regular_file(path, metadata)
            if entry_type == "regular_file"
            else None
        )
        entries.append(
            {
                "relative_leaf": path.relative_to(series_directory).as_posix(),
                "entry_type": entry_type,
                "size_bytes": metadata.st_size,
                "sha256": digest,
            }
        )
        if entry_type != "directory":
            return
        try:
            children = sorted(path.iterdir(), key=lambda child: child.name)
        except OSError as error:
            raise ArtifactStoreError("pilot_residue_unreadable") from error
        for child in children:
            visit(child)

    try:
        root_entries = sorted(series_directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ArtifactStoreError("pilot_residue_unreadable") from error
    for root_entry in root_entries:
        if root_entry.name in excluded_root_names:
            continue
        visit(root_entry)

    attempts = series_directory / "attempts"
    try:
        attempt_entries = sorted(attempts.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ArtifactStoreError("pilot_residue_unreadable") from error
    for attempt_entry in attempt_entries:
        if attempt_entry.name in completed_attempts:
            continue
        visit(attempt_entry)
    return sorted(entries, key=lambda entry: entry["relative_leaf"])


def _verified_interval(value: object, *, failure_code: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise ArtifactStoreError(failure_code)
    started = value.get("started_monotonic_ns")
    completed = value.get("completed_monotonic_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(completed, int)
        or isinstance(completed, bool)
        or started < 0
        or completed < started
    ):
        raise ArtifactStoreError(failure_code)
    for key in ("started_utc", "completed_utc"):
        text = value.get(key)
        if not isinstance(text, str) or not text.endswith("Z"):
            raise ArtifactStoreError(failure_code)
        try:
            datetime.fromisoformat(text[:-1] + "+00:00")
        except ValueError as error:
            raise ArtifactStoreError(failure_code) from error
    return started, completed


def _sample_manifest(
    sample: DeviceSample,
    *,
    role: str,
    raw_name: str | None,
    raw_sha256: str | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "role": role,
        "logical_id": sample.logical_id,
        "expected_identity_binding_sha256": sample.expected_identity_binding_sha256,
        "observed_identity_binding_sha256_before": (
            sample.observed_identity_binding_sha256_before
        ),
        "observed_identity_binding_sha256_after": (
            sample.observed_identity_binding_sha256_after
        ),
        "observed_endpoint_token_before": sample.observed_endpoint_token_before,
        "observed_endpoint_token_after": sample.observed_endpoint_token_after,
        "product_key": sample.product_key,
        "status": sample.status,
        "validity_scope": "acquisition_only_not_q2_boundary",
        "outcome": _sample_outcome(sample),
        "failed_predicates": _failed_predicates(sample),
        "attempt": _clock_interval(sample.attempt_started, sample.attempt_completed),
        "identity_check": {
            "method": "same_exact_udp_binding_before_and_after_tcp_read",
            "before": _clock_interval(
                sample.identity_before_started,
                sample.identity_before_completed,
            ),
            "after": _optional_clock_interval(
                sample.identity_after_started,
                sample.identity_after_completed,
            ),
        },
        "explicit_reply_observed": _observed_explicit_reply(sample.raw_wire_frame),
        "failure_code": sample.failure_code,
        "failure_class": sample.failure_class,
        "failure_phase": sample.failure_phase,
        "evidence": {
            "raw_wire_frame": {
                "grade": "a",
                "available": sample.raw_wire_frame is not None,
            },
            "identity_and_host_timing": {"grade": "b", "available": True},
            "state_summary": {
                "grade": "b",
                "available": sample.state_summary is not None,
            },
        },
    }
    value["read"] = _optional_clock_interval(sample.read_started, sample.read_completed)
    value["raw"] = (
        {
            "path": raw_name,
            "size": len(sample.raw_wire_frame or b""),
            "sha256": raw_sha256,
            "format": RAW_FORMAT,
        }
        if raw_name is not None and raw_sha256 is not None
        else None
    )
    value["state_summary"] = sample.state_summary
    value["state_observation"] = sample.state_observation
    return value


class RawCaptureStore:
    """Commit one capture as an owner-only, atomically published private directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            metadata = root.lstat()
        except FileNotFoundError as error:
            raise ArtifactStoreError("artifact_root_missing") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("artifact_root_not_private_directory")

    def commit(
        self,
        capture: PairCapture,
        *,
        context: CaptureContext,
        artifact_id: str | None = None,
    ) -> PublicArtifactMetadata:
        _validate_capture_context(context)
        identifier = _safe_artifact_id(artifact_id or _new_artifact_id())
        final_path = self.root / identifier
        temporary_path = self.root / f".{identifier}.tmp-{secrets.token_hex(8)}"
        if final_path.exists() or final_path.is_symlink():
            raise ArtifactStoreError("artifact_already_exists")
        temporary_path.mkdir(mode=0o700)

        sample_values: list[dict[str, Any]] = []
        try:
            for role, sample in zip(("a", "b"), capture.samples, strict=True):
                raw_name: str | None = None
                raw_digest: str | None = None
                if sample.raw_wire_frame is not None:
                    raw_name = f"{role}.reply.frame.bin"
                    raw_digest = hashlib.sha256(sample.raw_wire_frame).hexdigest()
                    _write_exclusive(temporary_path / raw_name, sample.raw_wire_frame)
                sample_values.append(
                    _sample_manifest(
                        sample,
                        role=role,
                        raw_name=raw_name,
                        raw_sha256=raw_digest,
                    )
                )

            manifest = {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "artifact_id": identifier,
                "status": capture.status,
                "capture_kind": CAPTURE_KIND,
                "plan_artifact_id": context.plan_artifact_id,
                "plan_sha256": context.plan_sha256,
                "epoch": context.epoch,
                "sample_index": context.sample_index,
                "required_action": TRANSPORT_ACTION,
                "accept_reports_policy": False,
                "raw_format": RAW_FORMAT,
                "collection": {
                    "started_utc": _utc_text(capture.started.utc),
                    "completed_utc": _utc_text(capture.completed.utc),
                    "started_monotonic_ns": capture.started.monotonic_ns,
                    "completed_monotonic_ns": capture.completed.monotonic_ns,
                },
                "pair_completion_gap_ns": capture.pair_completion_gap_ns,
                "samples": sample_values,
            }
            manifest_payload = _canonical_json(manifest)
            manifest_digest = hashlib.sha256(manifest_payload).hexdigest()
            _write_exclusive(temporary_path / "manifest.json", manifest_payload)
            _fsync_directory(temporary_path)
            os.rename(temporary_path, final_path)
            try:
                _fsync_directory(self.root)
            except OSError as error:
                raise DurabilityUnconfirmedError("artifact_parent_fsync_unconfirmed") from error
            _write_exclusive(
                final_path / "commit.json",
                _canonical_json(
                    {
                        "schema_version": CAPTURE_SCHEMA_VERSION,
                        "artifact_id": identifier,
                        "manifest_sha256": manifest_digest,
                    }
                ),
            )
            try:
                _fsync_directory(final_path)
            except OSError as error:
                raise DurabilityUnconfirmedError("artifact_commit_fsync_unconfirmed") from error
        except BaseException:
            # Private partial evidence is deliberately retained. It must never be published as a
            # successful artifact, and a later forensic inspection may explain a failed fsync.
            raise

        verified = self.verify(identifier)
        return PublicArtifactMetadata(
            artifact_id=identifier,
            status=verified["status"],
            utc_started=verified["collection"]["started_utc"],
            utc_completed=verified["collection"]["completed_utc"],
            expected_identity_bindings_sha256=tuple(
                sample["expected_identity_binding_sha256"]
                for sample in verified["samples"]
            ),  # type: ignore[arg-type]
            manifest_sha256=manifest_digest,
        )

    def verify(self, artifact_id: str) -> dict[str, Any]:
        identifier = _safe_artifact_id(artifact_id)
        directory = self.root / identifier
        try:
            metadata = directory.lstat()
        except (FileNotFoundError, OSError) as error:
            raise ArtifactStoreError("artifact_directory_invalid") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("artifact_directory_invalid")

        manifest_path = directory / "manifest.json"
        manifest_payload = _read_private_regular_file(
            manifest_path,
            failure_code="artifact_manifest_invalid",
        )
        try:
            manifest = json.loads(manifest_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("artifact_manifest_unreadable") from error

        commit_payload = _read_private_regular_file(
            directory / "commit.json",
            failure_code="artifact_commit_marker_invalid",
        )
        try:
            commit_marker = json.loads(commit_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("artifact_commit_marker_invalid") from error
        if commit_marker != {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "artifact_id": identifier,
            "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        }:
            raise ArtifactStoreError("artifact_commit_marker_mismatch")

        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != CAPTURE_SCHEMA_VERSION
            or manifest.get("artifact_id") != identifier
            or manifest.get("capture_kind") != CAPTURE_KIND
            or manifest.get("required_action") != TRANSPORT_ACTION
            or manifest.get("accept_reports_policy") is not False
            or manifest.get("raw_format") != RAW_FORMAT
        ):
            raise ArtifactStoreError("artifact_manifest_claim_invalid")
        try:
            _validate_capture_context(
                CaptureContext(
                    plan_artifact_id=manifest["plan_artifact_id"],
                    plan_sha256=manifest["plan_sha256"],
                    epoch=manifest["epoch"],
                    sample_index=manifest["sample_index"],
                )
            )
        except (KeyError, TypeError, ArtifactStoreError) as error:
            raise ArtifactStoreError("artifact_plan_reference_invalid") from error
        _verified_interval(manifest.get("collection"), failure_code="artifact_timing_invalid")

        samples = manifest.get("samples")
        if not isinstance(samples, list) or len(samples) != 2:
            raise ArtifactStoreError("artifact_manifest_samples_invalid")
        sample_statuses: list[str] = []
        binding_keys: list[str] = []
        read_completions: list[int | None] = []
        claimed_files = {"manifest.json", "commit.json"}
        for expected_role, sample in zip(("a", "b"), samples, strict=True):
            if not isinstance(sample, dict):
                raise ArtifactStoreError("artifact_manifest_sample_invalid")
            status_value = sample.get("status")
            if status_value not in {"acquisition_valid", "acquisition_invalid"} or sample.get(
                "role"
            ) != expected_role:
                raise ArtifactStoreError("artifact_manifest_sample_claim_invalid")
            if sample.get("validity_scope") != "acquisition_only_not_q2_boundary":
                raise ArtifactStoreError("artifact_validity_scope_invalid")
            sample_statuses.append(status_value)
            binding_key = sample.get("expected_identity_binding_sha256")
            if not _is_sha256(binding_key):
                raise ArtifactStoreError("artifact_identity_binding_invalid")
            binding_keys.append(binding_key)
            observed_before = sample.get("observed_identity_binding_sha256_before")
            observed_after = sample.get("observed_identity_binding_sha256_after")
            for observed_binding in (observed_before, observed_after):
                if observed_binding is not None and not _is_sha256(observed_binding):
                    raise ArtifactStoreError("artifact_observed_identity_binding_invalid")
            endpoint_before = sample.get("observed_endpoint_token_before")
            endpoint_after = sample.get("observed_endpoint_token_after")
            for endpoint_token in (endpoint_before, endpoint_after):
                if endpoint_token is not None and not _is_sha256(endpoint_token):
                    raise ArtifactStoreError("artifact_endpoint_token_invalid")

            attempt = _verified_interval(
                sample.get("attempt"),
                failure_code="artifact_sample_timing_invalid",
            )
            identity = sample.get("identity_check")
            if (
                not isinstance(identity, dict)
                or identity.get("method")
                != "same_exact_udp_binding_before_and_after_tcp_read"
            ):
                raise ArtifactStoreError("artifact_identity_method_invalid")
            before = _verified_interval(
                identity.get("before"),
                failure_code="artifact_identity_timing_invalid",
            )
            after_value = identity.get("after")
            after = (
                _verified_interval(
                    after_value,
                    failure_code="artifact_identity_timing_invalid",
                )
                if after_value is not None
                else None
            )
            read_value = sample.get("read")
            read = (
                _verified_interval(
                    read_value,
                    failure_code="artifact_read_timing_invalid",
                )
                if read_value is not None
                else None
            )
            read_completions.append(read[1] if read is not None else None)
            if before[0] < attempt[0] or before[1] > attempt[1]:
                raise ArtifactStoreError("artifact_identity_timing_order_invalid")
            if read is not None and (read[0] < before[1] or read[1] > attempt[1]):
                raise ArtifactStoreError("artifact_read_timing_order_invalid")
            if after is not None and (
                read is None or after[0] < read[1] or after[1] > attempt[1]
            ):
                raise ArtifactStoreError("artifact_identity_timing_order_invalid")

            evidence = sample.get("evidence")
            if not isinstance(evidence, dict):
                raise ArtifactStoreError("artifact_evidence_taxonomy_invalid")
            raw = sample.get("raw")
            if raw is None:
                if status_value == "acquisition_valid":
                    raise ArtifactStoreError("artifact_valid_sample_missing_raw")
                if sample.get("explicit_reply_observed") is not None:
                    raise ArtifactStoreError("artifact_reply_observation_without_raw")
                if evidence.get("raw_wire_frame") != {"grade": "a", "available": False}:
                    raise ArtifactStoreError("artifact_evidence_taxonomy_invalid")
                if sample.get("state_summary") is not None:
                    raise ArtifactStoreError("artifact_summary_without_raw")
                continue
            if not isinstance(raw, dict):
                raise ArtifactStoreError("artifact_raw_claim_invalid")
            raw_name = raw.get("path")
            if (
                raw_name != f"{expected_role}.reply.frame.bin"
                or raw.get("format") != RAW_FORMAT
            ):
                raise ArtifactStoreError("artifact_raw_path_invalid")
            claimed_files.add(raw_name)
            raw_path = directory / raw_name
            payload = _read_private_regular_file(
                raw_path,
                failure_code="artifact_raw_file_invalid",
            )
            if len(payload) != raw.get("size") or not secrets.compare_digest(
                hashlib.sha256(payload).hexdigest(), str(raw.get("sha256"))
            ):
                raise ArtifactStoreError("artifact_raw_digest_mismatch")
            try:
                action, status_payload = _state_frame_parts(payload)
            except (TypeError, ValueError, ProtocolError) as error:
                raise ArtifactStoreError("artifact_raw_frame_invalid") from error
            explicit_reply = action == STATE_REPLY_ACTION
            if sample.get("explicit_reply_observed") is not explicit_reply:
                raise ArtifactStoreError("artifact_reply_observation_mismatch")
            if evidence.get("raw_wire_frame") != {"grade": "a", "available": True}:
                raise ArtifactStoreError("artifact_evidence_taxonomy_invalid")
            if status_value == "acquisition_valid":
                if not explicit_reply:
                    raise ArtifactStoreError("artifact_raw_action_not_explicit_reply")
                if (
                    observed_before != binding_key
                    or observed_after != binding_key
                    or observed_before != observed_after
                    or endpoint_before is None
                    or endpoint_before != endpoint_after
                    or after is None
                ):
                    raise ArtifactStoreError("artifact_valid_identity_binding_mismatch")
                product_key = sample.get("product_key")
                if not isinstance(product_key, str):
                    raise ArtifactStoreError("artifact_product_key_invalid")
                try:
                    decoded_summary = _state_summary(product_key, status_payload)
                except (CollectorError, KeyError, TypeError, ValueError) as error:
                    raise ArtifactStoreError("artifact_valid_raw_decode_failed") from error
                if decoded_summary != sample.get("state_summary"):
                    raise ArtifactStoreError("artifact_state_summary_mismatch")
                if any(
                    sample.get(field) is not None
                    for field in ("failure_code", "failure_class", "failure_phase")
                ):
                    raise ArtifactStoreError("artifact_valid_failure_claim_invalid")
            elif not all(
                isinstance(sample.get(field), str) and sample.get(field)
                for field in ("failure_code", "failure_class", "failure_phase")
            ):
                raise ArtifactStoreError("artifact_invalid_failure_claim_missing")
        if len(set(binding_keys)) != 2:
            raise ArtifactStoreError("artifact_identity_bindings_not_distinct")

        try:
            actual_files = {entry.name for entry in directory.iterdir()}
        except OSError as error:
            raise ArtifactStoreError("artifact_directory_unreadable") from error
        if actual_files != claimed_files:
            raise ArtifactStoreError("artifact_file_set_mismatch")

        expected_status = (
            "acquisition_valid"
            if sample_statuses == ["acquisition_valid", "acquisition_valid"]
            else "acquisition_invalid"
        )
        if manifest.get("status") != expected_status:
            raise ArtifactStoreError("artifact_pair_status_mismatch")
        pair_gap = manifest.get("pair_completion_gap_ns")
        expected_pair_gap = (
            read_completions[1] - read_completions[0]
            if read_completions[0] is not None and read_completions[1] is not None
            else None
        )
        if expected_pair_gap is not None and expected_pair_gap < 0:
            raise ArtifactStoreError("artifact_pair_read_order_invalid")
        if pair_gap != expected_pair_gap:
            raise ArtifactStoreError("artifact_pair_gap_mismatch")
        return manifest


class PilotSeriesStore:
    """Durably preserve a bounded, no-retry pilot series before any classification work."""

    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            metadata = root.lstat()
        except FileNotFoundError as error:
            raise ArtifactStoreError("pilot_root_missing") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("pilot_root_not_private_directory")

    def prepare(
        self,
        targets: tuple[CaptureTarget, CaptureTarget],
        *,
        source_attestation: CollectorSourceAttestation,
        planned_pair_count: int,
        requested_cadence_seconds: float,
        collector_commit_sha: str,
        epoch: str = "pilot",
        utc_clock: UtcClock = lambda: datetime.now(UTC),
    ) -> PilotPlanReference:
        """Publish an immutable pilot plan before the first discovery or TCP connection."""

        if (
            not isinstance(planned_pair_count, int)
            or isinstance(planned_pair_count, bool)
            or not 1 <= planned_pair_count <= MAX_PILOT_PAIR_COUNT
        ):
            raise CollectorPreflightError("pilot_pair_count_invalid")
        if (
            not isinstance(requested_cadence_seconds, (int, float))
            or isinstance(requested_cadence_seconds, bool)
            or requested_cadence_seconds <= 0
            or not math.isfinite(requested_cadence_seconds)
        ):
            raise CollectorPreflightError("pilot_cadence_invalid")
        requested_cadence_ns = round(float(requested_cadence_seconds) * 1_000_000_000)
        if requested_cadence_ns <= 0:
            raise CollectorPreflightError("pilot_cadence_invalid")
        if (
            not isinstance(collector_commit_sha, str)
            or len(collector_commit_sha) != 40
            or any(character not in "0123456789abcdef" for character in collector_commit_sha)
        ):
            raise CollectorPreflightError("collector_commit_sha_invalid")
        try:
            validated_attestation = validate_collector_source_attestation(
                source_attestation,
                expected_commit=collector_commit_sha,
            )
        except SourceAttestationError as error:
            raise CollectorPreflightError(error.code) from error
        epoch_value = _safe_artifact_id(epoch)
        if targets[0].identity_binding_sha256 == targets[1].identity_binding_sha256:
            raise CollectorPreflightError("capture_binding_not_distinct")
        if any(
            target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY for target in targets
        ):
            raise CollectorPreflightError("pilot_target_not_local_wavemaker_pro")

        plan_id = _new_plan_id()
        series_id = _new_series_id()
        final_path = self.root / series_id
        temporary_path = self.root / f".{series_id}.tmp-{secrets.token_hex(8)}"
        if final_path.exists() or final_path.is_symlink():
            raise ArtifactStoreError("pilot_series_already_exists")
        temporary_path.mkdir(mode=0o700)
        (temporary_path / "attempts").mkdir(mode=0o700)

        plan = {
            "schema_version": PILOT_PLAN_SCHEMA_VERSION,
            "kind": "readonly_collector_pilot_plan",
            "plan_artifact_id": plan_id,
            "series_id": series_id,
            "epoch": epoch_value,
            "created_utc": _utc_text(utc_clock()),
            "collector_commit_sha": collector_commit_sha,
            "collector_runtime_source_digest_sha256": (
                validated_attestation.runtime_source_digest_sha256
            ),
            "ordered_targets": [
                {
                    "role": role,
                    "logical_id": target.logical_id,
                    "product_key": target.product_key,
                    "expected_identity_binding_sha256": target.identity_binding_sha256,
                    "config_fingerprint_sha256": target.config_fingerprint,
                }
                for role, target in zip(("a", "b"), targets, strict=True)
            ],
            "acquisition": {
                "planned_pair_count": planned_pair_count,
                "planned_ordinals": list(range(planned_pair_count)),
                "requested_cadence_ns": requested_cadence_ns,
                "pair_order": ["a", "b"],
                "retry_policy": "none",
                "late_sample_policy": "record_and_continue_without_skipping",
                "fresh_discovery_before_and_after_each_read": True,
                "fresh_authenticated_session_per_device_sample": True,
                "reads_per_session": 1,
                "accept_reports": False,
                "required_command": "0x0091",
                "required_action": TRANSPORT_ACTION,
                "serial_payload_size_bytes": 453,
                "status_payload_size_bytes": 452,
                "wire_frame_storage": "exact_observed_length_no_normalization",
            },
            "validity_predicate": capture_validity_policy(),
            "durability": {
                "barrier": "os_fsync_file_and_directory",
                "claim": "kernel_flush_requested_not_power_loss_guarantee",
            },
            "interpretation": {
                "scope": "collector_transport_and_timing_pilot",
                "q2_boundary_classification_authorized": False,
                "expected_epoch_roles": None,
                "expected_schedule_images": None,
                "judgment_thresholds": {
                    "maximum_pair_gap_ns": None,
                    "freshness_window_ns": None,
                    "boundary_exclusion_ns": None,
                    "stability_window_ns": None,
                },
                "thresholds_must_be_derived_from_this_series": True,
            },
        }
        plan_payload = _canonical_json(plan)
        plan_digest = hashlib.sha256(plan_payload).hexdigest()
        try:
            _write_exclusive(temporary_path / "plan.json", plan_payload)
            _fsync_directory(temporary_path / "attempts")
            _fsync_directory(temporary_path)
            os.rename(temporary_path, final_path)
            _fsync_directory(self.root)
            _write_exclusive(
                final_path / "plan.commit.json",
                _canonical_json(
                    {
                        "schema_version": PILOT_PLAN_SCHEMA_VERSION,
                        "plan_artifact_id": plan_id,
                        "series_id": series_id,
                        "plan_sha256": plan_digest,
                    }
                ),
            )
            _fsync_directory(final_path)
        except OSError as error:
            raise DurabilityUnconfirmedError("pilot_plan_durability_unconfirmed") from error

        reference = PilotPlanReference(
            plan_artifact_id=plan_id,
            series_id=series_id,
            plan_sha256=plan_digest,
            epoch=epoch_value,
            planned_pair_count=planned_pair_count,
            requested_cadence_ns=requested_cadence_ns,
            series_directory=final_path,
        )
        self.verify_plan(reference)
        return reference

    def verify_plan(self, reference: PilotPlanReference) -> dict[str, Any]:
        _safe_artifact_id(reference.plan_artifact_id)
        _safe_artifact_id(reference.series_id)
        _safe_artifact_id(reference.epoch)
        if not _is_sha256(reference.plan_sha256):
            raise ArtifactStoreError("pilot_plan_reference_invalid")
        directory = self.root / reference.series_id
        try:
            metadata = directory.lstat()
        except (FileNotFoundError, OSError) as error:
            raise ArtifactStoreError("pilot_series_directory_invalid") from error
        if (
            directory != reference.series_directory
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("pilot_series_directory_invalid")
        plan_payload = _read_private_regular_file(
            directory / "plan.json",
            failure_code="pilot_plan_file_invalid",
        )
        digest = hashlib.sha256(plan_payload).hexdigest()
        if not secrets.compare_digest(digest, reference.plan_sha256):
            raise ArtifactStoreError("pilot_plan_digest_mismatch")
        marker_payload = _read_private_regular_file(
            directory / "plan.commit.json",
            failure_code="pilot_plan_commit_marker_invalid",
        )
        try:
            plan = json.loads(plan_payload.decode("utf-8"))
            marker = json.loads(marker_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_plan_json_invalid") from error
        if not isinstance(plan, dict):
            raise ArtifactStoreError("pilot_plan_claim_invalid")
        if marker != {
            "schema_version": PILOT_PLAN_SCHEMA_VERSION,
            "plan_artifact_id": reference.plan_artifact_id,
            "series_id": reference.series_id,
            "plan_sha256": reference.plan_sha256,
        }:
            raise ArtifactStoreError("pilot_plan_commit_marker_mismatch")
        acquisition = plan.get("acquisition")
        interpretation = plan.get("interpretation")
        targets = plan.get("ordered_targets")
        collector_commit = plan.get("collector_commit_sha")
        collector_runtime_source_digest = plan.get(
            "collector_runtime_source_digest_sha256"
        )
        if (
            plan.get("schema_version") != PILOT_PLAN_SCHEMA_VERSION
            or plan.get("kind") != "readonly_collector_pilot_plan"
            or plan.get("plan_artifact_id") != reference.plan_artifact_id
            or plan.get("series_id") != reference.series_id
            or plan.get("epoch") != reference.epoch
            or not isinstance(acquisition, dict)
            or acquisition.get("planned_pair_count") != reference.planned_pair_count
            or acquisition.get("planned_ordinals")
            != list(range(reference.planned_pair_count))
            or acquisition.get("requested_cadence_ns") != reference.requested_cadence_ns
            or acquisition.get("pair_order") != ["a", "b"]
            or acquisition.get("retry_policy") != "none"
            or acquisition.get("late_sample_policy")
            != "record_and_continue_without_skipping"
            or acquisition.get("fresh_discovery_before_and_after_each_read") is not True
            or acquisition.get("fresh_authenticated_session_per_device_sample") is not True
            or acquisition.get("reads_per_session") != 1
            or acquisition.get("accept_reports") is not False
            or acquisition.get("required_command") != "0x0091"
            or acquisition.get("required_action") != TRANSPORT_ACTION
            or acquisition.get("serial_payload_size_bytes") != 453
            or acquisition.get("status_payload_size_bytes") != 452
            or acquisition.get("wire_frame_storage")
            != "exact_observed_length_no_normalization"
            or plan.get("durability")
            != {
                "barrier": "os_fsync_file_and_directory",
                "claim": "kernel_flush_requested_not_power_loss_guarantee",
            }
            or not isinstance(interpretation, dict)
            or interpretation.get("scope") != "collector_transport_and_timing_pilot"
            or interpretation.get("q2_boundary_classification_authorized") is not False
            or interpretation.get("expected_epoch_roles") is not None
            or interpretation.get("expected_schedule_images") is not None
            or interpretation.get("judgment_thresholds")
            != {
                "maximum_pair_gap_ns": None,
                "freshness_window_ns": None,
                "boundary_exclusion_ns": None,
                "stability_window_ns": None,
            }
            or interpretation.get("thresholds_must_be_derived_from_this_series") is not True
            or not isinstance(targets, list)
            or len(targets) != 2
            or [target.get("role") for target in targets if isinstance(target, dict)]
            != ["a", "b"]
            or [target.get("product_key") for target in targets if isinstance(target, dict)]
            != [LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, LOCAL_WAVEMAKER_PRO_PRODUCT_KEY]
            or not isinstance(collector_commit, str)
            or len(collector_commit) != 40
            or any(character not in "0123456789abcdef" for character in collector_commit)
            or not _is_sha256(collector_runtime_source_digest)
        ):
            raise ArtifactStoreError("pilot_plan_claim_invalid")
        _validate_recorded_capture_policy(plan.get("validity_predicate"))
        created_utc = plan.get("created_utc")
        if not isinstance(created_utc, str) or not created_utc.endswith("Z"):
            raise ArtifactStoreError("pilot_plan_created_time_invalid")
        try:
            datetime.fromisoformat(created_utc[:-1] + "+00:00")
        except ValueError as error:
            raise ArtifactStoreError("pilot_plan_created_time_invalid") from error
        bindings = [target.get("expected_identity_binding_sha256") for target in targets]
        config_fingerprints = [target.get("config_fingerprint_sha256") for target in targets]
        if (
            any(not _is_sha256(binding) for binding in bindings)
            or len(set(bindings)) != 2
            or any(not _is_sha256(value) for value in config_fingerprints)
        ):
            raise ArtifactStoreError("pilot_plan_binding_invalid")
        attempts = directory / "attempts"
        try:
            attempts_metadata = attempts.lstat()
        except (FileNotFoundError, OSError) as error:
            raise ArtifactStoreError("pilot_attempts_directory_invalid") from error
        if (
            not stat.S_ISDIR(attempts_metadata.st_mode)
            or stat.S_ISLNK(attempts_metadata.st_mode)
            or stat.S_IMODE(attempts_metadata.st_mode) != 0o700
            or attempts_metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("pilot_attempts_directory_invalid")
        return plan

    def load(self, series_id: str) -> PilotPlanReference:
        """Load and verify a plan reference from one private series directory."""

        safe_series_id = _safe_artifact_id(series_id)
        directory = self.root / safe_series_id
        marker_payload = _read_private_regular_file(
            directory / "plan.commit.json",
            failure_code="pilot_plan_commit_marker_invalid",
        )
        plan_payload = _read_private_regular_file(
            directory / "plan.json",
            failure_code="pilot_plan_file_invalid",
        )
        try:
            marker = json.loads(marker_payload.decode("utf-8"))
            plan = json.loads(plan_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_plan_json_invalid") from error
        if not isinstance(marker, dict) or not isinstance(plan, dict):
            raise ArtifactStoreError("pilot_plan_claim_invalid")
        plan_sha256 = marker.get("plan_sha256")
        acquisition = plan.get("acquisition")
        plan_artifact_id = marker.get("plan_artifact_id")
        epoch = plan.get("epoch")
        planned_pair_count = (
            acquisition.get("planned_pair_count") if isinstance(acquisition, dict) else None
        )
        requested_cadence_ns = (
            acquisition.get("requested_cadence_ns") if isinstance(acquisition, dict) else None
        )
        if (
            marker.get("schema_version") != PILOT_PLAN_SCHEMA_VERSION
            or marker.get("series_id") != safe_series_id
            or not _is_sha256(plan_sha256)
            or not isinstance(acquisition, dict)
            or not isinstance(plan_artifact_id, str)
            or not isinstance(epoch, str)
            or not isinstance(planned_pair_count, int)
            or isinstance(planned_pair_count, bool)
            or not isinstance(requested_cadence_ns, int)
            or isinstance(requested_cadence_ns, bool)
        ):
            raise ArtifactStoreError("pilot_plan_commit_marker_invalid")
        reference = PilotPlanReference(
            plan_artifact_id=plan_artifact_id,
            series_id=safe_series_id,
            plan_sha256=plan_sha256,
            epoch=epoch,
            planned_pair_count=planned_pair_count,
            requested_cadence_ns=requested_cadence_ns,
            series_directory=directory,
        )
        self.verify_plan(reference)
        return reference

    async def run(
        self,
        reference: PilotPlanReference,
        targets: tuple[CaptureTarget, CaptureTarget],
        *,
        source_attestation: CollectorSourceAttestation,
        discovery_factory: DiscoveryFactory,
        session_factory: SessionFactory,
        discovery_timeout_seconds: float,
        utc_clock: UtcClock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = time.monotonic_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> PublicPilotMetadata:
        """Capture every predeclared ordinal once; invalid reads are evidence, never retries."""

        plan = self.verify_plan(reference)
        try:
            validated_attestation = validate_collector_source_attestation(
                source_attestation,
                expected_commit=plan["collector_commit_sha"],
            )
        except SourceAttestationError as error:
            raise CollectorPreflightError(error.code) from error
        if (
            validated_attestation.runtime_source_digest_sha256
            != plan["collector_runtime_source_digest_sha256"]
        ):
            raise CollectorPreflightError("collector_source_attestation_stale")
        plan_targets = plan["ordered_targets"]
        for target, planned in zip(targets, plan_targets, strict=True):
            if (
                target.logical_id != planned["logical_id"]
                or target.product_key != planned["product_key"]
                or target.identity_binding_sha256
                != planned["expected_identity_binding_sha256"]
                or target.config_fingerprint != planned["config_fingerprint_sha256"]
            ):
                raise CollectorPreflightError("pilot_runtime_target_mismatch")
        if discovery_timeout_seconds <= 0:
            raise CollectorPreflightError("discovery_timeout_not_positive")
        if any(
            (reference.series_directory / name).exists()
            for name in (
                "started.json",
                "series.json",
                "series.commit.json",
                "aborted.json",
                "aborted.commit.json",
            )
        ):
            raise CollectorPreflightError("pilot_series_already_started")

        series_started = _stamp(utc_clock, monotonic_clock)
        schedule_anchor_ns = series_started.monotonic_ns
        started_payload = _canonical_json(
            {
                "schema_version": PILOT_SERIES_SCHEMA_VERSION,
                "kind": "readonly_collector_pilot_started",
                "plan_artifact_id": reference.plan_artifact_id,
                "plan_sha256": reference.plan_sha256,
                "series_id": reference.series_id,
                "started": _clock_interval(series_started, series_started),
                "schedule_anchor_monotonic_ns": schedule_anchor_ns,
            }
        )
        records: list[dict[str, Any]] = []
        try:
            _write_exclusive(reference.series_directory / "started.json", started_payload)
            _fsync_directory(reference.series_directory)
        except OSError:
            self._raise_terminal_abort(
                reference,
                records=records,
                aborted_at=_stamp(utc_clock, monotonic_clock),
                error=DurabilityUnconfirmedError(
                    "pilot_started_durability_unconfirmed"
                ),
            )

        try:
            await self._capture_ordinals(
                reference,
                targets,
                plan=plan,
                records=records,
                series_started=series_started,
                schedule_anchor_ns=schedule_anchor_ns,
                discovery_factory=discovery_factory,
                session_factory=session_factory,
                discovery_timeout_seconds=discovery_timeout_seconds,
                utc_clock=utc_clock,
                monotonic_clock=monotonic_clock,
                sleep=sleep,
            )
        except BaseException as error:
            self._raise_terminal_abort(
                reference,
                records=records,
                aborted_at=_stamp(utc_clock, monotonic_clock),
                error=error,
            )
        try:
            return self._commit_completed_series(
                reference,
                records=records,
                plan_targets=plan_targets,
                series_started=series_started,
                series_completed=_stamp(utc_clock, monotonic_clock),
            )
        except BaseException as error:
            self._raise_terminal_abort(
                reference,
                records=records,
                aborted_at=_stamp(utc_clock, monotonic_clock),
                error=error,
            )

    def _commit_completed_series(
        self,
        reference: PilotPlanReference,
        *,
        records: list[dict[str, Any]],
        plan_targets: list[dict[str, Any]],
        series_started: ClockStamp,
        series_completed: ClockStamp,
    ) -> PublicPilotMetadata:
        counts = {
            outcome: sum(record["outcome"] == outcome for record in records)
            for outcome in _PAIR_OUTCOMES
        }
        series_status = (
            "pilot_completed_all_acquisitions_accepted"
            if counts["accepted"] == reference.planned_pair_count
            else "pilot_completed_with_rejected_or_failed_acquisitions"
        )
        series = {
            "schema_version": PILOT_SERIES_SCHEMA_VERSION,
            "kind": "readonly_collector_pilot_series",
            "plan_artifact_id": reference.plan_artifact_id,
            "plan_sha256": reference.plan_sha256,
            "series_id": reference.series_id,
            "status": series_status,
            "validity_scope": "acquisition_only_not_q2_boundary",
            "q2_boundary_classification": "not_authorized",
            "started": _clock_interval(series_started, series_started),
            "completed": _clock_interval(series_completed, series_completed),
            "planned_pair_count": reference.planned_pair_count,
            "completed_pair_count": len(records),
            "counts": counts,
            "records": records,
        }
        series_payload = _canonical_json(series)
        series_digest = hashlib.sha256(series_payload).hexdigest()
        try:
            _write_exclusive(reference.series_directory / "series.json", series_payload)
            _write_exclusive(
                reference.series_directory / "series.commit.json",
                _canonical_json(
                    {
                        "schema_version": PILOT_SERIES_SCHEMA_VERSION,
                        "series_id": reference.series_id,
                        "series_sha256": series_digest,
                    }
                ),
            )
            _fsync_directory(reference.series_directory)
        except OSError as error:
            raise DurabilityUnconfirmedError(
                "pilot_series_durability_unconfirmed"
            ) from error
        verified = self.verify_completed_series(
            reference, expected_series_sha256=series_digest
        )
        bindings = tuple(
            target["expected_identity_binding_sha256"] for target in plan_targets
        )
        return PublicPilotMetadata(
            plan_artifact_id=reference.plan_artifact_id,
            series_id=reference.series_id,
            plan_sha256=reference.plan_sha256,
            series_sha256=series_digest,
            status=verified["status"],
            validity_scope="acquisition_only_not_q2_boundary",
            q2_boundary_classification=verified["q2_boundary_classification"],
            utc_started=verified["started"]["started_utc"],
            utc_completed=verified["completed"]["completed_utc"],
            planned_pair_count=verified["planned_pair_count"],
            completed_pair_count=verified["completed_pair_count"],
            accepted_pair_count=verified["counts"]["accepted"],
            rejected_pair_count=verified["counts"]["predicate_rejected"],
            read_failure_pair_count=verified["counts"]["read_failure"],
            expected_identity_bindings_sha256=bindings,  # type: ignore[arg-type]
        )

    async def _capture_ordinals(
        self,
        reference: PilotPlanReference,
        targets: tuple[CaptureTarget, CaptureTarget],
        *,
        plan: dict[str, Any],
        records: list[dict[str, Any]],
        series_started: ClockStamp,
        schedule_anchor_ns: int,
        discovery_factory: DiscoveryFactory,
        session_factory: SessionFactory,
        discovery_timeout_seconds: float,
        utc_clock: UtcClock,
        monotonic_clock: MonotonicClock,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        for ordinal in range(reference.planned_pair_count):
            scheduled_ns = schedule_anchor_ns + ordinal * reference.requested_cadence_ns
            remaining_ns = scheduled_ns - monotonic_clock()
            if remaining_ns > 0:
                await sleep(remaining_ns / 1_000_000_000)
            intent_digest = self._commit_intent(
                reference,
                ordinal=ordinal,
                scheduled_monotonic_ns=scheduled_ns,
                scheduled_utc=series_started.utc
                + timedelta(microseconds=(ordinal * reference.requested_cadence_ns) / 1_000),
                utc_clock=utc_clock,
                monotonic_clock=monotonic_clock,
            )
            pair_started = _stamp(utc_clock, monotonic_clock)
            sample_records: list[dict[str, Any]] = []
            samples: list[DeviceSample] = []
            for role, target in zip(("a", "b"), targets, strict=True):
                try:
                    sample = await collect_device_sample(
                        target,
                        discovery_factory=discovery_factory,
                        session_factory=session_factory,
                        discovery_timeout_seconds=discovery_timeout_seconds,
                        validity_policy=plan["validity_predicate"],
                        utc_clock=utc_clock,
                        monotonic_clock=monotonic_clock,
                    )
                except SampleCaptureCancelled as error:
                    self._commit_series_sample(
                        reference,
                        ordinal=ordinal,
                        role=role,
                        sample=error.sample,
                    )
                    raise
                samples.append(sample)
                sample_records.append(
                    self._commit_series_sample(
                        reference,
                        ordinal=ordinal,
                        role=role,
                        sample=sample,
                    )
                )
            pair_completed = _stamp(utc_clock, monotonic_clock)
            first_read = samples[0].read_completed
            second_read = samples[1].read_completed
            pair_gap = (
                second_read.monotonic_ns - first_read.monotonic_ns
                if first_read is not None and second_read is not None
                else None
            )
            pair_outcome = _pair_outcome(tuple(record["outcome"] for record in sample_records))
            records.append(
                self._commit_pair_record(
                    reference,
                    ordinal=ordinal,
                    intent_sha256=intent_digest,
                    pair_started=pair_started,
                    pair_completed=pair_completed,
                    pair_completion_gap_ns=pair_gap,
                    pair_outcome=pair_outcome,
                    sample_records=sample_records,
                )
            )

    def _raise_terminal_abort(
        self,
        reference: PilotPlanReference,
        *,
        records: list[dict[str, Any]],
        aborted_at: ClockStamp,
        error: BaseException,
    ) -> Never:
        abort_code = _pilot_abort_code(error)
        durability_unknown = _pilot_durability_unknown(error)
        try:
            abort_digest = self._commit_aborted_series(
                reference,
                records=records,
                aborted_at=aborted_at,
                error=error,
                durability_unknown=durability_unknown,
            )
        except BaseException:
            terminal_code = (
                abort_code
                if abort_code
                in {
                    "capture_cancelled_after_read",
                    "capture_cancelled",
                    "keyboard_interrupt",
                }
                else "pilot_abort_durability_unconfirmed"
            )
            raise PilotTerminalError(
                terminal_code,
                plan_artifact_id=reference.plan_artifact_id,
                series_id=reference.series_id,
                plan_sha256=reference.plan_sha256,
                abort_sha256=None,
                durability_unknown=True,
            ) from None
        raise PilotTerminalError(
            abort_code,
            plan_artifact_id=reference.plan_artifact_id,
            series_id=reference.series_id,
            plan_sha256=reference.plan_sha256,
            abort_sha256=abort_digest,
            durability_unknown=durability_unknown,
        ) from None

    def _commit_aborted_series(
        self,
        reference: PilotPlanReference,
        *,
        records: list[dict[str, Any]],
        aborted_at: ClockStamp,
        error: BaseException,
        durability_unknown: bool,
    ) -> str:
        """Publish a terminal marker for a prefix-complete interrupted pilot."""

        completed_ordinals = [record.get("ordinal") for record in records]
        if completed_ordinals != list(range(len(records))):
            raise ArtifactStoreError("pilot_abort_prefix_invalid") from error
        trailing_ordinal = len(records)
        trailing_path = reference.series_directory / "attempts" / f"{trailing_ordinal:06d}"
        trailing_roles: list[str] = []
        trailing_attempt: dict[str, Any] | None = None
        try:
            trailing_metadata = trailing_path.lstat()
        except FileNotFoundError:
            trailing_metadata = None
        except OSError as inventory_error:
            raise ArtifactStoreError("pilot_residue_unreadable") from inventory_error
        if trailing_metadata is not None and stat.S_ISDIR(trailing_metadata.st_mode):
            for role in ("a", "b"):
                role_path = trailing_path / role
                try:
                    role_metadata = role_path.lstat()
                except FileNotFoundError:
                    continue
                except OSError as inventory_error:
                    raise ArtifactStoreError("pilot_residue_unreadable") from inventory_error
                if stat.S_ISDIR(role_metadata.st_mode):
                    trailing_roles.append(role)
            try:
                pair_record_present = (trailing_path / "pair.json").lstat() is not None
            except FileNotFoundError:
                pair_record_present = False
            except OSError as inventory_error:
                raise ArtifactStoreError("pilot_residue_unreadable") from inventory_error
            trailing_attempt = {
                "ordinal": trailing_ordinal,
                "committed_roles": trailing_roles,
                "pair_record_present": pair_record_present,
            }
        counts = {
            outcome: sum(record.get("outcome") == outcome for record in records)
            for outcome in _PAIR_OUTCOMES
        }
        residue_inventory = _pilot_residue_inventory(
            reference.series_directory,
            completed_pair_count=len(records),
        )
        aborted = {
            "schema_version": PILOT_SERIES_SCHEMA_VERSION,
            "kind": "readonly_collector_pilot_aborted",
            "plan_artifact_id": reference.plan_artifact_id,
            "plan_sha256": reference.plan_sha256,
            "series_id": reference.series_id,
            "status": "pilot_aborted_prefix_only_not_q2_boundary",
            "q2_boundary_classification": "not_authorized",
            "aborted": _clock_interval(aborted_at, aborted_at),
            "abort_code": _pilot_abort_code(error),
            "durability_unknown": durability_unknown,
            "completed_ordinals": completed_ordinals,
            "last_complete_ordinal": completed_ordinals[-1] if completed_ordinals else None,
            "counts": counts,
            "records": records,
            "trailing_attempt": trailing_attempt,
            "residue_inventory": residue_inventory,
        }
        payload = _canonical_json(aborted)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            _write_exclusive(reference.series_directory / "aborted.json", payload)
            _write_exclusive(
                reference.series_directory / "aborted.commit.json",
                _canonical_json(
                    {
                        "schema_version": PILOT_SERIES_SCHEMA_VERSION,
                        "series_id": reference.series_id,
                        "aborted_sha256": digest,
                    }
                ),
            )
            _fsync_directory(reference.series_directory)
        except OSError as durability_error:
            raise DurabilityUnconfirmedError(
                "pilot_abort_durability_unconfirmed"
            ) from durability_error
        self.verify_partial_series(reference, expected_aborted_sha256=digest)
        return digest

    def _commit_intent(
        self,
        reference: PilotPlanReference,
        *,
        ordinal: int,
        scheduled_monotonic_ns: int,
        scheduled_utc: datetime,
        utc_clock: UtcClock,
        monotonic_clock: MonotonicClock,
    ) -> str:
        attempts = reference.series_directory / "attempts"
        name = f"{ordinal:06d}"
        final_path = attempts / name
        temporary_path = attempts / f".{name}.tmp-{secrets.token_hex(8)}"
        if final_path.exists() or final_path.is_symlink():
            raise ArtifactStoreError("pilot_attempt_already_exists")
        created = _stamp(utc_clock, monotonic_clock)
        intent_payload = _canonical_json(
            {
                "schema_version": PILOT_SERIES_SCHEMA_VERSION,
                "kind": "readonly_collector_pair_intent",
                "plan_artifact_id": reference.plan_artifact_id,
                "plan_sha256": reference.plan_sha256,
                "series_id": reference.series_id,
                "ordinal": ordinal,
                "roles": ["a", "b"],
                "retry_policy": "none",
                "scheduled_utc": _utc_text(scheduled_utc),
                "scheduled_monotonic_ns": scheduled_monotonic_ns,
                "schedule_offset_ns": created.monotonic_ns - scheduled_monotonic_ns,
                "lateness_ns": max(0, created.monotonic_ns - scheduled_monotonic_ns),
                "intent_created": _clock_interval(created, created),
            }
        )
        digest = hashlib.sha256(intent_payload).hexdigest()
        try:
            temporary_path.mkdir(mode=0o700)
            _write_exclusive(temporary_path / "intent.json", intent_payload)
            _fsync_directory(temporary_path)
            os.rename(temporary_path, final_path)
            _fsync_directory(attempts)
        except OSError as error:
            raise DurabilityUnconfirmedError(
                "pilot_intent_durability_unconfirmed"
            ) from error
        return digest

    def _commit_series_sample(
        self,
        reference: PilotPlanReference,
        *,
        ordinal: int,
        role: str,
        sample: DeviceSample,
    ) -> dict[str, Any]:
        attempt = reference.series_directory / "attempts" / f"{ordinal:06d}"
        final_path = attempt / role
        temporary_path = attempt / f".{role}.tmp-{secrets.token_hex(8)}"
        if final_path.exists() or final_path.is_symlink():
            raise ArtifactStoreError("pilot_sample_already_exists")
        raw_name: str | None = None
        raw_digest: str | None = None
        if sample.raw_wire_frame is not None:
            raw_name = "raw.frame"
            raw_digest = hashlib.sha256(sample.raw_wire_frame).hexdigest()
        sample_value = _sample_manifest(
            sample,
            role=role,
            raw_name=raw_name,
            raw_sha256=raw_digest,
        )
        sample_value.update(
            {
                "schema_version": PILOT_SERIES_SCHEMA_VERSION,
                "kind": "readonly_collector_device_sample",
                "plan_artifact_id": reference.plan_artifact_id,
                "plan_sha256": reference.plan_sha256,
                "series_id": reference.series_id,
                "ordinal": ordinal,
            }
        )
        sample_payload = _canonical_json(sample_value)
        sample_digest = hashlib.sha256(sample_payload).hexdigest()
        try:
            temporary_path.mkdir(mode=0o700)
            if raw_name is not None and sample.raw_wire_frame is not None:
                _write_exclusive(temporary_path / raw_name, sample.raw_wire_frame)
            _write_exclusive(temporary_path / "sample.json", sample_payload)
            _fsync_directory(temporary_path)
            os.rename(temporary_path, final_path)
            _fsync_directory(attempt)
        except OSError as error:
            raise DurabilityUnconfirmedError(
                "pilot_sample_durability_unconfirmed"
            ) from error
        return {
            "role": role,
            "outcome": sample_value["outcome"],
            "sample_sha256": sample_digest,
        }

    def _commit_pair_record(
        self,
        reference: PilotPlanReference,
        *,
        ordinal: int,
        intent_sha256: str,
        pair_started: ClockStamp,
        pair_completed: ClockStamp,
        pair_completion_gap_ns: int | None,
        pair_outcome: str,
        sample_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        attempt = reference.series_directory / "attempts" / f"{ordinal:06d}"
        pair = {
            "schema_version": PILOT_SERIES_SCHEMA_VERSION,
            "kind": "readonly_collector_pair",
            "plan_artifact_id": reference.plan_artifact_id,
            "plan_sha256": reference.plan_sha256,
            "series_id": reference.series_id,
            "ordinal": ordinal,
            "outcome": pair_outcome,
            "intent_sha256": intent_sha256,
            "attempt": _clock_interval(pair_started, pair_completed),
            "pair_completion_gap_ns": pair_completion_gap_ns,
            "samples": sample_records,
        }
        payload = _canonical_json(pair)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            _write_exclusive(attempt / "pair.json", payload)
            _fsync_directory(attempt)
        except OSError as error:
            raise DurabilityUnconfirmedError("pilot_pair_durability_unconfirmed") from error
        return {"ordinal": ordinal, "outcome": pair_outcome, "pair_sha256": digest}

    def verify_completed_series(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
    ) -> dict[str, Any]:
        plan = self.verify_plan(reference)
        if not _is_sha256(expected_series_sha256):
            raise ArtifactStoreError("pilot_series_expected_digest_invalid")
        directory = reference.series_directory
        if (directory / "aborted.json").exists() or (
            directory / "aborted.commit.json"
        ).exists():
            raise ArtifactStoreError("pilot_terminal_state_ambiguous")
        started_payload = _read_private_regular_file(
            directory / "started.json",
            failure_code="pilot_started_file_invalid",
        )
        series_payload = _read_private_regular_file(
            directory / "series.json",
            failure_code="pilot_series_file_invalid",
        )
        series_digest = hashlib.sha256(series_payload).hexdigest()
        if not secrets.compare_digest(series_digest, expected_series_sha256):
            raise ArtifactStoreError("pilot_series_digest_mismatch")
        marker_payload = _read_private_regular_file(
            directory / "series.commit.json",
            failure_code="pilot_series_commit_marker_invalid",
        )
        try:
            started = json.loads(started_payload.decode("utf-8"))
            series = json.loads(series_payload.decode("utf-8"))
            marker = json.loads(marker_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_series_json_invalid") from error
        if marker != {
            "schema_version": PILOT_SERIES_SCHEMA_VERSION,
            "series_id": reference.series_id,
            "series_sha256": expected_series_sha256,
        }:
            raise ArtifactStoreError("pilot_series_commit_marker_mismatch")
        if (
            not isinstance(started, dict)
            or started.get("schema_version") != PILOT_SERIES_SCHEMA_VERSION
            or started.get("kind") != "readonly_collector_pilot_started"
            or started.get("plan_artifact_id") != reference.plan_artifact_id
            or started.get("plan_sha256") != reference.plan_sha256
            or started.get("series_id") != reference.series_id
            or not isinstance(started.get("schedule_anchor_monotonic_ns"), int)
            or isinstance(started.get("schedule_anchor_monotonic_ns"), bool)
            or started.get("schedule_anchor_monotonic_ns") < 0
        ):
            raise ArtifactStoreError("pilot_started_claim_invalid")
        started_interval = _verified_interval(
            started.get("started"), failure_code="pilot_started_timing_invalid"
        )
        if started.get("schedule_anchor_monotonic_ns") != started_interval[0]:
            raise ArtifactStoreError("pilot_started_schedule_anchor_invalid")
        if (
            not isinstance(series, dict)
            or series.get("schema_version") != PILOT_SERIES_SCHEMA_VERSION
            or series.get("kind") != "readonly_collector_pilot_series"
            or series.get("plan_artifact_id") != reference.plan_artifact_id
            or series.get("plan_sha256") != reference.plan_sha256
            or series.get("series_id") != reference.series_id
            or series.get("planned_pair_count") != reference.planned_pair_count
            or series.get("completed_pair_count") != reference.planned_pair_count
            or series.get("validity_scope") != "acquisition_only_not_q2_boundary"
            or series.get("q2_boundary_classification") != "not_authorized"
        ):
            raise ArtifactStoreError("pilot_series_claim_invalid")
        series_started_interval = _verified_interval(
            series.get("started"), failure_code="pilot_series_timing_invalid"
        )
        series_completed_interval = _verified_interval(
            series.get("completed"), failure_code="pilot_series_timing_invalid"
        )
        if (
            series.get("started") != started.get("started")
            or series_started_interval != started_interval
            or series_completed_interval[0] < series_started_interval[1]
        ):
            raise ArtifactStoreError("pilot_series_timing_order_invalid")
        records = series.get("records")
        if (
            not isinstance(records, list)
            or any(not isinstance(record, dict) for record in records)
            or [record.get("ordinal") for record in records]
            != list(range(reference.planned_pair_count))
        ):
            raise ArtifactStoreError("pilot_series_ordinal_set_invalid")
        attempts = directory / "attempts"
        expected_names = {f"{ordinal:06d}" for ordinal in range(reference.planned_pair_count)}
        try:
            actual_names = {entry.name for entry in attempts.iterdir()}
        except OSError as error:
            raise ArtifactStoreError("pilot_attempts_directory_unreadable") from error
        if actual_names != expected_names:
            raise ArtifactStoreError("pilot_series_attempt_set_mismatch")

        recomputed_counts = {outcome: 0 for outcome in _PAIR_OUTCOMES}
        for ordinal, record in enumerate(records):
            if not isinstance(record, dict):
                raise ArtifactStoreError("pilot_series_record_invalid")
            pair = self._verify_attempt(reference, plan, ordinal=ordinal)
            pair_payload = _read_private_regular_file(
                attempts / f"{ordinal:06d}" / "pair.json",
                failure_code="pilot_pair_file_invalid",
            )
            if (
                record.get("pair_sha256") != hashlib.sha256(pair_payload).hexdigest()
                or record.get("outcome") != pair["outcome"]
            ):
                raise ArtifactStoreError("pilot_series_record_mismatch")
            recomputed_counts[pair["outcome"]] += 1
        if series.get("counts") != recomputed_counts:
            raise ArtifactStoreError("pilot_series_counts_mismatch")
        expected_status = (
            "pilot_completed_all_acquisitions_accepted"
            if recomputed_counts["accepted"] == reference.planned_pair_count
            else "pilot_completed_with_rejected_or_failed_acquisitions"
        )
        if series.get("status") != expected_status:
            raise ArtifactStoreError("pilot_series_status_mismatch")
        return series

    def extract_verified_accepted_pair(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ) -> VerifiedPilotPairArtifact:
        """Return one accepted raw pair after full-series and exact-attempt verification.

        This is an evidence extraction boundary, not a Q2 classifier.  It deliberately
        exposes neither private paths nor physical identifiers.
        """

        series = self.verify_completed_series(
            reference,
            expected_series_sha256=expected_series_sha256,
        )
        records = series["records"]
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 0
            or ordinal >= len(records)
        ):
            raise ArtifactStoreError("pilot_artifact_ordinal_invalid")
        record = records[ordinal]
        if record.get("outcome") != "accepted":
            raise ArtifactStoreError("pilot_artifact_pair_not_accepted")

        plan = self.verify_plan(reference)
        pair = self._verify_attempt(
            reference,
            plan,
            ordinal=ordinal,
            include_artifact_payloads=True,
        )
        pair_payload = pair.pop("_verified_pair_payload", None)
        verified_samples = pair.pop("_verified_samples", None)
        if (
            pair.get("outcome") != "accepted"
            or not isinstance(pair_payload, bytes)
            or not isinstance(verified_samples, tuple)
            or len(verified_samples) != 2
        ):
            raise ArtifactStoreError("pilot_artifact_pair_not_accepted")
        pair_digest = hashlib.sha256(pair_payload).hexdigest()
        if not secrets.compare_digest(pair_digest, str(record.get("pair_sha256"))):
            raise ArtifactStoreError("pilot_artifact_pair_digest_mismatch")

        samples: list[VerifiedPilotRawSample] = []
        for role, verified in zip(("a", "b"), verified_samples, strict=True):
            sample = verified.pop("_verified_sample_manifest", None)
            sample_payload = verified.pop("_verified_sample_payload", None)
            raw_wire_frame = verified.pop("_verified_raw_wire_frame", None)
            if (
                verified.get("outcome") != "accepted"
                or not isinstance(sample, dict)
                or not isinstance(sample_payload, bytes)
                or not isinstance(raw_wire_frame, bytes)
                or sample.get("role") != role
            ):
                raise ArtifactStoreError("pilot_artifact_sample_not_accepted")
            identity = sample.get("identity_check")
            raw_claim = sample.get("raw")
            if (
                not isinstance(identity, dict)
                or not isinstance(raw_claim, dict)
                or not isinstance(sample.get("attempt"), dict)
                or not isinstance(sample.get("read"), dict)
                or not isinstance(identity.get("before"), dict)
                or not isinstance(identity.get("after"), dict)
            ):
                raise ArtifactStoreError("pilot_artifact_sample_provenance_invalid")
            identity_binding = sample.get("expected_identity_binding_sha256")
            raw_digest = hashlib.sha256(raw_wire_frame).hexdigest()
            if (
                not _is_sha256(identity_binding)
                or sample.get("observed_identity_binding_sha256_before") != identity_binding
                or sample.get("observed_identity_binding_sha256_after") != identity_binding
                or not secrets.compare_digest(raw_digest, str(raw_claim.get("sha256")))
            ):
                raise ArtifactStoreError("pilot_artifact_sample_provenance_invalid")
            samples.append(
                VerifiedPilotRawSample(
                    role=role,
                    identity_binding_sha256=identity_binding,
                    sample_manifest_sha256=hashlib.sha256(sample_payload).hexdigest(),
                    raw_wire_frame_sha256=raw_digest,
                    attempt=_public_verified_interval(sample["attempt"]),
                    identity_before=_public_verified_interval(identity["before"]),
                    read=_public_verified_interval(sample["read"]),
                    identity_after=_public_verified_interval(identity["after"]),
                    raw_wire_frame=raw_wire_frame,
                )
            )

        pair_attempt = pair.get("attempt")
        pair_gap = pair.get("pair_completion_gap_ns")
        if (
            not isinstance(pair_attempt, dict)
            or not isinstance(pair_gap, int)
            or isinstance(pair_gap, bool)
            or pair_gap < 0
        ):
            raise ArtifactStoreError("pilot_artifact_pair_provenance_invalid")
        return VerifiedPilotPairArtifact(
            plan_artifact_id=reference.plan_artifact_id,
            plan_sha256=reference.plan_sha256,
            series_id=reference.series_id,
            series_sha256=expected_series_sha256,
            ordinal=ordinal,
            pair_manifest_sha256=pair_digest,
            attempt=_public_verified_interval(pair_attempt),
            pair_completion_gap_ns=pair_gap,
            samples=(samples[0], samples[1]),
        )

    def verify_partial_series(
        self,
        reference: PilotPlanReference,
        *,
        expected_aborted_sha256: str,
    ) -> dict[str, Any]:
        """Verify a terminal, prefix-complete interrupted series without treating it as Q2."""

        plan = self.verify_plan(reference)
        if not _is_sha256(expected_aborted_sha256):
            raise ArtifactStoreError("pilot_abort_expected_digest_invalid")
        directory = reference.series_directory
        started_payload = _read_private_regular_file(
            directory / "started.json", failure_code="pilot_started_file_invalid"
        )
        aborted_payload = _read_private_regular_file(
            directory / "aborted.json", failure_code="pilot_abort_file_invalid"
        )
        if not secrets.compare_digest(
            hashlib.sha256(aborted_payload).hexdigest(), expected_aborted_sha256
        ):
            raise ArtifactStoreError("pilot_abort_digest_mismatch")
        marker_payload = _read_private_regular_file(
            directory / "aborted.commit.json",
            failure_code="pilot_abort_commit_marker_invalid",
        )
        try:
            started = json.loads(started_payload.decode("utf-8"))
            aborted = json.loads(aborted_payload.decode("utf-8"))
            marker = json.loads(marker_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_abort_json_invalid") from error
        if marker != {
            "schema_version": PILOT_SERIES_SCHEMA_VERSION,
            "series_id": reference.series_id,
            "aborted_sha256": expected_aborted_sha256,
        }:
            raise ArtifactStoreError("pilot_abort_commit_marker_mismatch")
        if (
            not isinstance(started, dict)
            or started.get("schema_version") != PILOT_SERIES_SCHEMA_VERSION
            or started.get("kind") != "readonly_collector_pilot_started"
            or started.get("plan_artifact_id") != reference.plan_artifact_id
            or started.get("plan_sha256") != reference.plan_sha256
            or started.get("series_id") != reference.series_id
        ):
            raise ArtifactStoreError("pilot_started_claim_invalid")
        started_timing = _verified_interval(
            started.get("started"), failure_code="pilot_started_timing_invalid"
        )
        if started.get("schedule_anchor_monotonic_ns") != started_timing[0]:
            raise ArtifactStoreError("pilot_started_schedule_anchor_invalid")
        if (
            not isinstance(aborted, dict)
            or aborted.get("schema_version") != PILOT_SERIES_SCHEMA_VERSION
            or aborted.get("kind") != "readonly_collector_pilot_aborted"
            or aborted.get("plan_artifact_id") != reference.plan_artifact_id
            or aborted.get("plan_sha256") != reference.plan_sha256
            or aborted.get("series_id") != reference.series_id
            or aborted.get("status") != "pilot_aborted_prefix_only_not_q2_boundary"
            or aborted.get("q2_boundary_classification") != "not_authorized"
            or not isinstance(aborted.get("abort_code"), str)
            or not aborted.get("abort_code")
            or not isinstance(aborted.get("durability_unknown"), bool)
        ):
            raise ArtifactStoreError("pilot_abort_claim_invalid")
        _verified_interval(aborted.get("aborted"), failure_code="pilot_abort_timing_invalid")
        records = aborted.get("records")
        completed_ordinals = aborted.get("completed_ordinals")
        if (
            not isinstance(records, list)
            or any(not isinstance(record, dict) for record in records)
            or [record.get("ordinal") for record in records] != list(range(len(records)))
            or completed_ordinals != list(range(len(records)))
            or len(records) > reference.planned_pair_count
            or aborted.get("last_complete_ordinal")
            != (len(records) - 1 if records else None)
        ):
            raise ArtifactStoreError("pilot_abort_prefix_invalid")

        recomputed_counts = {outcome: 0 for outcome in _PAIR_OUTCOMES}
        attempts = directory / "attempts"
        for ordinal, record in enumerate(records):
            pair = self._verify_attempt(reference, plan, ordinal=ordinal)
            pair_payload = _read_private_regular_file(
                attempts / f"{ordinal:06d}" / "pair.json",
                failure_code="pilot_pair_file_invalid",
            )
            if (
                record.get("pair_sha256") != hashlib.sha256(pair_payload).hexdigest()
                or record.get("outcome") != pair.get("outcome")
            ):
                raise ArtifactStoreError("pilot_abort_record_mismatch")
            recomputed_counts[pair["outcome"]] += 1
        if aborted.get("counts") != recomputed_counts:
            raise ArtifactStoreError("pilot_abort_counts_mismatch")

        trailing = aborted.get("trailing_attempt")
        if trailing is not None:
            if (
                not isinstance(trailing, dict)
                or trailing.get("ordinal") != len(records)
                or len(records) >= reference.planned_pair_count
                or not isinstance(trailing.get("pair_record_present"), bool)
            ):
                raise ArtifactStoreError("pilot_trailing_attempt_claim_invalid")
            self._verify_trailing_attempt(
                reference,
                plan,
                ordinal=len(records),
                expected_roles=trailing.get("committed_roles"),
                expected_pair_record_present=trailing["pair_record_present"],
            )
        residue_inventory = aborted.get("residue_inventory")
        actual_inventory = _pilot_residue_inventory(
            directory,
            completed_pair_count=len(records),
        )
        if not isinstance(residue_inventory, list) or residue_inventory != actual_inventory:
            raise ArtifactStoreError("pilot_abort_residue_inventory_mismatch")
        return aborted

    def _verify_trailing_attempt(
        self,
        reference: PilotPlanReference,
        plan: dict[str, Any],
        *,
        ordinal: int,
        expected_roles: object,
        expected_pair_record_present: bool,
    ) -> None:
        if expected_roles not in ([], ["a"], ["a", "b"]):
            raise ArtifactStoreError("pilot_trailing_roles_invalid")
        attempt = reference.series_directory / "attempts" / f"{ordinal:06d}"
        try:
            metadata = attempt.lstat()
            actual_files = {entry.name for entry in attempt.iterdir()}
        except (FileNotFoundError, OSError) as error:
            raise ArtifactStoreError("pilot_trailing_attempt_invalid") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
            or "intent.json" not in actual_files
            or (("pair.json" in actual_files) is not expected_pair_record_present)
        ):
            raise ArtifactStoreError("pilot_trailing_attempt_invalid")
        actual_roles: list[str] = []
        for role in ("a", "b"):
            try:
                role_metadata = (attempt / role).lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ArtifactStoreError("pilot_trailing_attempt_invalid") from error
            if stat.S_ISDIR(role_metadata.st_mode):
                actual_roles.append(role)
        if actual_roles != expected_roles:
            raise ArtifactStoreError("pilot_trailing_attempt_invalid")
        intent_payload = _read_private_regular_file(
            attempt / "intent.json", failure_code="pilot_intent_file_invalid"
        )
        try:
            intent = json.loads(intent_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_intent_json_invalid") from error
        if (
            not isinstance(intent, dict)
            or intent.get("kind") != "readonly_collector_pair_intent"
            or intent.get("plan_artifact_id") != reference.plan_artifact_id
            or intent.get("plan_sha256") != reference.plan_sha256
            or intent.get("series_id") != reference.series_id
            or intent.get("ordinal") != ordinal
            or intent.get("roles") != ["a", "b"]
            or intent.get("retry_policy") != "none"
        ):
            raise ArtifactStoreError("pilot_intent_claim_invalid")
        intent_timing = _verified_interval(
            intent.get("intent_created"), failure_code="pilot_intent_timing_invalid"
        )
        self._verify_intent_schedule(intent, intent_timing)
        for role in expected_roles:
            target_index = 0 if role == "a" else 1
            self._verify_series_sample(
                reference,
                ordinal=ordinal,
                role=role,
                expected_target=plan["ordered_targets"][target_index],
                validity_policy=plan["validity_predicate"],
            )

    def _verify_attempt(
        self,
        reference: PilotPlanReference,
        plan: dict[str, Any],
        *,
        ordinal: int,
        include_artifact_payloads: bool = False,
    ) -> dict[str, Any]:
        attempt = reference.series_directory / "attempts" / f"{ordinal:06d}"
        try:
            metadata = attempt.lstat()
        except (FileNotFoundError, OSError) as error:
            raise ArtifactStoreError("pilot_attempt_directory_invalid") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("pilot_attempt_directory_invalid")
        try:
            actual_files = {entry.name for entry in attempt.iterdir()}
        except OSError as error:
            raise ArtifactStoreError("pilot_attempt_directory_unreadable") from error
        if actual_files != {"intent.json", "a", "b", "pair.json"}:
            raise ArtifactStoreError("pilot_attempt_file_set_mismatch")
        intent_payload = _read_private_regular_file(
            attempt / "intent.json",
            failure_code="pilot_intent_file_invalid",
        )
        pair_payload = _read_private_regular_file(
            attempt / "pair.json",
            failure_code="pilot_pair_file_invalid",
        )
        try:
            intent = json.loads(intent_payload.decode("utf-8"))
            pair = json.loads(pair_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_attempt_json_invalid") from error
        for value, kind in (
            (intent, "readonly_collector_pair_intent"),
            (pair, "readonly_collector_pair"),
        ):
            if (
                not isinstance(value, dict)
                or value.get("kind") != kind
                or value.get("plan_artifact_id") != reference.plan_artifact_id
                or value.get("plan_sha256") != reference.plan_sha256
                or value.get("series_id") != reference.series_id
                or value.get("ordinal") != ordinal
            ):
                raise ArtifactStoreError("pilot_attempt_claim_invalid")
        if pair.get("intent_sha256") != hashlib.sha256(intent_payload).hexdigest():
            raise ArtifactStoreError("pilot_intent_digest_mismatch")
        if intent.get("retry_policy") != "none" or intent.get("roles") != ["a", "b"]:
            raise ArtifactStoreError("pilot_intent_policy_invalid")
        intent_timing = _verified_interval(
            intent.get("intent_created"), failure_code="pilot_intent_timing_invalid"
        )
        self._verify_intent_schedule(intent, intent_timing)
        _verified_interval(pair.get("attempt"), failure_code="pilot_pair_timing_invalid")
        sample_claims = pair.get("samples")
        if not isinstance(sample_claims, list) or len(sample_claims) != 2:
            raise ArtifactStoreError("pilot_pair_samples_invalid")
        verified_samples = []
        for role, planned_target, claim in zip(
            ("a", "b"), plan["ordered_targets"], sample_claims, strict=True
        ):
            verified = self._verify_series_sample(
                reference,
                ordinal=ordinal,
                role=role,
                expected_target=planned_target,
                validity_policy=plan["validity_predicate"],
                include_artifact_payload=include_artifact_payloads,
            )
            if (
                not isinstance(claim, dict)
                or claim.get("role") != role
                or claim.get("outcome") != verified["outcome"]
                or claim.get("sample_sha256") != verified["sample_sha256"]
            ):
                raise ArtifactStoreError("pilot_pair_sample_claim_mismatch")
            verified_samples.append(verified)
        pair_outcome = _pair_outcome(tuple(item["outcome"] for item in verified_samples))
        read_completions = [item["read_completed_monotonic_ns"] for item in verified_samples]
        expected_gap = (
            read_completions[1] - read_completions[0]
            if read_completions[0] is not None and read_completions[1] is not None
            else None
        )
        if expected_gap is not None and expected_gap < 0:
            raise ArtifactStoreError("pilot_pair_read_order_invalid")
        if (
            pair.get("outcome") != pair_outcome
            or pair.get("pair_completion_gap_ns") != expected_gap
        ):
            raise ArtifactStoreError("pilot_pair_outcome_or_gap_mismatch")
        if include_artifact_payloads:
            pair = dict(pair)
            pair["_verified_pair_payload"] = pair_payload
            pair["_verified_samples"] = tuple(verified_samples)
        return pair

    @staticmethod
    def _verify_intent_schedule(
        intent: dict[str, Any],
        intent_timing: tuple[int, int],
    ) -> None:
        scheduled_ns = intent.get("scheduled_monotonic_ns")
        offset_ns = intent.get("schedule_offset_ns")
        lateness_ns = intent.get("lateness_ns")
        if (
            not isinstance(scheduled_ns, int)
            or isinstance(scheduled_ns, bool)
            or scheduled_ns < 0
            or not isinstance(offset_ns, int)
            or isinstance(offset_ns, bool)
            or not isinstance(lateness_ns, int)
            or isinstance(lateness_ns, bool)
            or lateness_ns < 0
            or offset_ns != intent_timing[0] - scheduled_ns
            or lateness_ns != max(0, offset_ns)
        ):
            raise ArtifactStoreError("pilot_intent_schedule_invalid")
        scheduled_utc = intent.get("scheduled_utc")
        if not isinstance(scheduled_utc, str) or not scheduled_utc.endswith("Z"):
            raise ArtifactStoreError("pilot_intent_schedule_invalid")
        try:
            datetime.fromisoformat(scheduled_utc[:-1] + "+00:00")
        except ValueError as error:
            raise ArtifactStoreError("pilot_intent_schedule_invalid") from error

    def _verify_series_sample(
        self,
        reference: PilotPlanReference,
        *,
        ordinal: int,
        role: str,
        expected_target: dict[str, Any],
        validity_policy: dict[str, Any],
        include_artifact_payload: bool = False,
    ) -> dict[str, Any]:
        directory = reference.series_directory / "attempts" / f"{ordinal:06d}" / role
        try:
            metadata = directory.lstat()
        except (FileNotFoundError, OSError) as error:
            raise ArtifactStoreError("pilot_sample_directory_invalid") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise ArtifactStoreError("pilot_sample_directory_invalid")
        sample_payload = _read_private_regular_file(
            directory / "sample.json",
            failure_code="pilot_sample_file_invalid",
        )
        try:
            sample = json.loads(sample_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError("pilot_sample_json_invalid") from error
        expected_binding = expected_target["expected_identity_binding_sha256"]
        if (
            not isinstance(sample, dict)
            or sample.get("kind") != "readonly_collector_device_sample"
            or sample.get("plan_artifact_id") != reference.plan_artifact_id
            or sample.get("plan_sha256") != reference.plan_sha256
            or sample.get("series_id") != reference.series_id
            or sample.get("ordinal") != ordinal
            or sample.get("role") != role
            or sample.get("expected_identity_binding_sha256") != expected_binding
            or sample.get("logical_id") != expected_target["logical_id"]
            or sample.get("product_key") != expected_target["product_key"]
            or sample.get("validity_scope") != "acquisition_only_not_q2_boundary"
        ):
            raise ArtifactStoreError("pilot_sample_claim_invalid")
        attempt_timing = _verified_interval(
            sample.get("attempt"), failure_code="pilot_sample_timing_invalid"
        )
        identity = sample.get("identity_check")
        if (
            not isinstance(identity, dict)
            or identity.get("method")
            != "same_exact_udp_binding_before_and_after_tcp_read"
        ):
            raise ArtifactStoreError("pilot_sample_identity_invalid")
        before_timing = _verified_interval(
            identity.get("before"), failure_code="pilot_sample_identity_timing_invalid"
        )
        after_value = identity.get("after")
        after_timing = (
            _verified_interval(
                after_value, failure_code="pilot_sample_identity_timing_invalid"
            )
            if after_value is not None
            else None
        )
        read_value = sample.get("read")
        read_timing = (
            _verified_interval(read_value, failure_code="pilot_sample_read_timing_invalid")
            if read_value is not None
            else None
        )
        if before_timing[0] < attempt_timing[0] or before_timing[1] > attempt_timing[1]:
            raise ArtifactStoreError("pilot_sample_timing_order_invalid")
        if read_timing is not None and (
            read_timing[0] < before_timing[1] or read_timing[1] > attempt_timing[1]
        ):
            raise ArtifactStoreError("pilot_sample_timing_order_invalid")
        if after_timing is not None and (
            read_timing is None
            or after_timing[0] < read_timing[1]
            or after_timing[1] > attempt_timing[1]
        ):
            raise ArtifactStoreError("pilot_sample_timing_order_invalid")

        raw_claim = sample.get("raw")
        raw_payload: bytes | None = None
        expected_files = {"sample.json"}
        if raw_claim is not None:
            if (
                not isinstance(raw_claim, dict)
                or raw_claim.get("path") != "raw.frame"
                or raw_claim.get("format") != RAW_FORMAT
            ):
                raise ArtifactStoreError("pilot_sample_raw_claim_invalid")
            expected_files.add("raw.frame")
            raw_payload = _read_private_regular_file(
                directory / "raw.frame", failure_code="pilot_sample_raw_file_invalid"
            )
            if (
                len(raw_payload) != raw_claim.get("size")
                or hashlib.sha256(raw_payload).hexdigest() != raw_claim.get("sha256")
            ):
                raise ArtifactStoreError("pilot_sample_raw_digest_mismatch")
        try:
            actual_files = {entry.name for entry in directory.iterdir()}
        except OSError as error:
            raise ArtifactStoreError("pilot_sample_directory_unreadable") from error
        if actual_files != expected_files:
            raise ArtifactStoreError("pilot_sample_file_set_mismatch")

        observed_before = sample.get("observed_identity_binding_sha256_before")
        observed_after = sample.get("observed_identity_binding_sha256_after")
        endpoint_before = sample.get("observed_endpoint_token_before")
        endpoint_after = sample.get("observed_endpoint_token_after")
        identity_valid = (
            observed_before == expected_binding
            and observed_after == expected_binding
            and endpoint_before is not None
            and _is_sha256(endpoint_before)
            and endpoint_before == endpoint_after
            and after_timing is not None
        )
        raw_valid = False
        decoded_summary: dict[str, Any] | None = None
        explicit_reply: bool | None = None
        if raw_payload is not None:
            try:
                action, status_payload = _state_frame_parts(raw_payload)
                explicit_reply = action == STATE_REPLY_ACTION
                if explicit_reply:
                    product_key = sample.get("product_key")
                    if not isinstance(product_key, str):
                        raise ValueError("product key must be a string")
                    decoded_summary = _state_summary(
                        product_key,
                        status_payload,
                        validity_policy=validity_policy,
                    )
                    raw_valid = True
            except (CollectorError, KeyError, TypeError, ValueError, ProtocolError):
                raw_valid = False
        expected_outcome = (
            "read_failure"
            if raw_payload is None
            else "accepted"
            if raw_valid
            and identity_valid
            and all(
                sample.get(field) is None
                for field in ("failure_code", "failure_class", "failure_phase")
            )
            else "predicate_rejected"
        )
        if sample.get("outcome") != expected_outcome:
            raise ArtifactStoreError("pilot_sample_outcome_mismatch")
        expected_status = (
            "acquisition_valid" if expected_outcome == "accepted" else "acquisition_invalid"
        )
        if sample.get("status") != expected_status:
            raise ArtifactStoreError("pilot_sample_status_mismatch")
        if sample.get("explicit_reply_observed") is not explicit_reply:
            raise ArtifactStoreError("pilot_sample_reply_observation_mismatch")
        expected_evidence = {
            "raw_wire_frame": {"grade": "a", "available": raw_payload is not None},
            "identity_and_host_timing": {"grade": "b", "available": True},
            "state_summary": {"grade": "b", "available": expected_outcome == "accepted"},
        }
        if sample.get("evidence") != expected_evidence:
            raise ArtifactStoreError("pilot_sample_evidence_taxonomy_mismatch")
        if expected_outcome == "accepted":
            if sample.get("state_summary") != decoded_summary:
                raise ArtifactStoreError("pilot_sample_summary_mismatch")
            if sample.get("state_observation") != _state_observation(decoded_summary):
                raise ArtifactStoreError("pilot_sample_state_observation_mismatch")
            if any(
                sample.get(field) is not None
                for field in ("failure_code", "failure_class", "failure_phase")
            ):
                raise ArtifactStoreError("pilot_sample_failure_claim_invalid")
        elif not all(
            isinstance(sample.get(field), str) and sample.get(field)
            for field in ("failure_code", "failure_class", "failure_phase")
        ):
            raise ArtifactStoreError("pilot_sample_failure_claim_missing")
        elif sample.get("state_observation") is not None:
            raise ArtifactStoreError("pilot_sample_state_observation_invalid")
        verified: dict[str, Any] = {
            "outcome": expected_outcome,
            "sample_sha256": hashlib.sha256(sample_payload).hexdigest(),
            "read_completed_monotonic_ns": (
                read_timing[1] if read_timing is not None else None
            ),
        }
        if include_artifact_payload:
            verified["_verified_sample_manifest"] = sample
            verified["_verified_sample_payload"] = sample_payload
            verified["_verified_raw_wire_frame"] = raw_payload
        return verified


_PAIR_OUTCOMES = ("accepted", "predicate_rejected", "read_failure")


def _pair_outcome(sample_outcomes: tuple[str, str]) -> str:
    if sample_outcomes == ("accepted", "accepted"):
        return "accepted"
    if "read_failure" in sample_outcomes:
        return "read_failure"
    return "predicate_rejected"


__all__ = [
    "ArtifactStoreError",
    "CaptureContext",
    "CaptureTarget",
    "CollectorError",
    "CollectorPreflightError",
    "DeviceSample",
    "DurabilityUnconfirmedError",
    "PairCapture",
    "PilotPlanReference",
    "PilotSeriesStore",
    "PilotTerminalError",
    "PublicArtifactMetadata",
    "PublicPilotMetadata",
    "RawCaptureStore",
    "ReadOnlySession",
    "ResolvedCaptureEndpoint",
    "VerifiedPilotInterval",
    "VerifiedPilotPairArtifact",
    "VerifiedPilotRawSample",
    "collect_device_sample",
    "collect_pair",
    "capture_validity_policy",
    "resolve_exact_endpoint",
    "select_capture_pair",
]
