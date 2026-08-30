"""Fail-closed production adapters for :mod:`jebao_flow.exact_restore`.

The restore controller deliberately owns no LAN or discovery implementation.  This module is
the narrow composition edge: observations are made from one explicit Pro state frame and every
physical action receives a fresh writer, a post-connect identity check, and a single-use ticket.

No native-linkage experiment module is imported here.  In particular, this adapter cannot write
the scalar ``Auto*`` datapoints; its only write surfaces are ``DeviceTarget`` and one validated
432-byte schedule image.
"""

from __future__ import annotations

import hashlib
import math
from asyncio import CancelledError
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from jebao_flow.exact_restore import (
    ExactRestoreDevice,
    ExactRestoreObservation,
    ExactRestoreRole,
    ExactScheduleImage,
    OuterControlSnapshot,
    system_boottime_ns,
)
from jebao_flow.physical_identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LocalWavemakerProScheduleSnapshot,
    validate_local_wavemaker_pro_schedule_image,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION, RawStateCapture
from jebao_flow.read_only_collector import (
    CaptureTarget,
    DiscoveryFactory,
    ReadOnlySession,
    ResolvedCaptureEndpoint,
    SessionFactory,
    resolve_exact_endpoint,
)


class ExactRestoreRuntimeNotReady(RuntimeError):
    """Privacy-safe refusal before an observation or physical action can be trusted."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RestoreWriter(Protocol):
    """The allow-listed surface a fresh write-enabled LAN device must provide."""

    @property
    def address(self) -> str: ...

    @property
    def physical_binding(self) -> PhysicalDeviceBinding | None: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def connected_session_token(self) -> object: ...

    async def write_target_connected(
        self,
        target: DeviceTarget,
        *,
        connected_session_token: object,
        guard: Callable[[], bool] | None = None,
    ) -> None: ...

    async def restore_schedule_image_connected(
        self,
        image: bytes,
        *,
        connected_session_token: object,
        guard: Callable[[], bool] | None = None,
    ) -> object: ...


RestoreWriterFactory = Callable[[ExactRestoreRole, ResolvedCaptureEndpoint], RestoreWriter]
UtcClock = Callable[[], datetime]
MonotonicClock = Callable[[], int]
_EXACT_OUTER_FIELDS = frozenset(
    {"enabled", "power", "mode", "frequency", "linkage", "timer_enabled"}
)


@dataclass(frozen=True, slots=True)
class _ClockStamp:
    utc: datetime
    monotonic_ns: int


@dataclass(frozen=True, slots=True)
class _EndpointLease:
    observation_key: tuple[object, ...]
    endpoint: ResolvedCaptureEndpoint
    deadline_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class _IdentityTicket:
    deadline_monotonic_ns: int
    connected_session_token: object


def _duration_ns(seconds: float, *, label: str) -> int:
    if not isinstance(seconds, int | float) or isinstance(seconds, bool):
        raise ValueError(f"{label} must be a finite positive number")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    value = int(seconds * 1_000_000_000)
    if value <= 0:
        raise ValueError(f"{label} is below monotonic clock resolution")
    return value


def _observation_key(observation: ExactRestoreObservation) -> tuple[object, ...]:
    return (
        observation.role,
        observation.identity_binding_sha256,
        observation.raw_frame_sha256,
        observation.requested_monotonic_ns,
        observation.observed_monotonic_ns,
        observation.received_monotonic_ns,
    )


def _writer_binding_matches(writer: RestoreWriter, expected_sha256: str) -> bool:
    binding = getattr(writer, "physical_binding", None)
    return (
        isinstance(binding, PhysicalDeviceBinding)
        and physical_identity_key(binding) == expected_sha256
    )


class FreshExplicitRestoreObserver:
    """Create explicit single-frame observations and one-use physical action adapters.

    ``max_identity_age_seconds`` is an operation-manifest value, not a firmware guess.  It bounds
    both the observation-to-resolver lease and the post-connect identity-to-write ticket.
    ``writer_factory=None`` keeps this object useful as a read-only final verifier while every
    attempt to resolve a write device fails closed.
    """

    def __init__(
        self,
        *,
        targets: Mapping[ExactRestoreRole, CaptureTarget],
        discovery_factory: DiscoveryFactory,
        session_factory: SessionFactory,
        max_identity_age_seconds: float,
        discovery_timeout_seconds: float = 5.0,
        writer_factory: RestoreWriterFactory | None = None,
        utc_clock: UtcClock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = system_boottime_ns,
    ) -> None:
        if set(targets) != {ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE}:
            raise ValueError("exact restore targets must contain master and slave exactly once")
        copied = dict(targets)
        if any(target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY for target in copied.values()):
            raise ValueError("exact restore runtime supports Local Wavemaker Pro only")
        if len({target.identity_binding_sha256 for target in copied.values()}) != 2:
            raise ValueError("exact restore physical bindings must be distinct")
        if (
            not isinstance(discovery_timeout_seconds, int | float)
            or isinstance(discovery_timeout_seconds, bool)
            or not math.isfinite(discovery_timeout_seconds)
            or discovery_timeout_seconds <= 0
        ):
            raise ValueError("discovery timeout must be a finite positive number")

        self._targets = copied
        self._discovery_factory = discovery_factory
        self._session_factory = session_factory
        self._writer_factory = writer_factory
        self._identity_age_ns = _duration_ns(
            max_identity_age_seconds,
            label="maximum identity age",
        )
        self._discovery_timeout_seconds = float(discovery_timeout_seconds)
        self._utc_clock = utc_clock
        self._monotonic_clock = monotonic_clock
        self._leases: dict[ExactRestoreRole, _EndpointLease] = {}
        self._issued_read_sessions: list[ReadOnlySession] = []
        self._issued_writers: list[RestoreWriter] = []

    async def observe(self, role: ExactRestoreRole) -> ExactRestoreObservation:
        """Read one fresh explicit 0x03/452 frame and decode both state regions from it."""

        target = self._target(role)
        # A failed refresh must never leave a prior endpoint lease usable.
        self._leases.pop(role, None)
        before = await self._discover_target(target)
        session = self._fresh_read_session(before.address)
        try:
            await session.connect()
            await session.authenticate()
            requested = self._stamp()
            capture = await session.read_raw_state_capture(accept_reports=False)
            observed = self._stamp(after=requested)
        except CancelledError:
            self._quarantine(session)
            await self._discard_session(session)
            raise
        except ExactRestoreRuntimeNotReady:
            self._quarantine(session)
            await self._discard_session(session)
            raise
        except Exception as error:
            self._quarantine(session)
            await self._discard_session(session)
            raise ExactRestoreRuntimeNotReady("explicit_observation_failed") from error
        try:
            await session.disconnect()
        except CancelledError:
            self._quarantine(session)
            raise
        except Exception as error:
            self._quarantine(session)
            raise ExactRestoreRuntimeNotReady("read_session_close_failed") from error

        status = self._validate_capture(capture)
        after = await self._discover_target(target)
        if before != after:
            raise ExactRestoreRuntimeNotReady("identity_endpoint_changed_during_read")
        received = self._stamp(after=observed)
        observation = self._decode_observation(
            role=role,
            target=target,
            capture=capture,
            status=status,
            requested=requested,
            observed=observed,
            received=received,
        )
        self._leases[role] = _EndpointLease(
            observation_key=_observation_key(observation),
            endpoint=after,
            deadline_monotonic_ns=received.monotonic_ns + self._identity_age_ns,
        )
        return observation

    def resolve_device(
        self,
        role: ExactRestoreRole,
        observation: ExactRestoreObservation,
    ) -> ExactRestoreDevice:
        """Consume one observation lease and return a fresh, one-action adapter."""

        target = self._target(role)
        if not isinstance(observation, ExactRestoreObservation):
            raise ExactRestoreRuntimeNotReady("observation_type_invalid")
        lease = self._leases.pop(role, None)
        if (
            observation.role is not role
            or observation.identity_binding_sha256 != target.identity_binding_sha256
            or lease is None
            or lease.observation_key != _observation_key(observation)
        ):
            raise ExactRestoreRuntimeNotReady("observation_lease_invalid")
        if self._monotonic_now() > lease.deadline_monotonic_ns:
            raise ExactRestoreRuntimeNotReady("observation_lease_expired")
        if self._writer_factory is None:
            raise ExactRestoreRuntimeNotReady("writer_factory_unavailable")
        try:
            writer = self._writer_factory(role, lease.endpoint)
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("writer_factory_failed") from error
        if any(writer is issued for issued in self._issued_writers):
            raise ExactRestoreRuntimeNotReady("writer_not_fresh")
        if getattr(
            writer, "address", None
        ) != lease.endpoint.address or not _writer_binding_matches(
            writer, target.identity_binding_sha256
        ):
            raise ExactRestoreRuntimeNotReady("writer_endpoint_mismatch")
        self._issued_writers.append(writer)
        return _IdentityBoundOneActionRestoreAdapter(
            target=target,
            endpoint=lease.endpoint,
            writer=writer,
            resolve_endpoint=self._discover_target,
            identity_age_ns=self._identity_age_ns,
            monotonic_clock=self._monotonic_clock,
        )

    def _target(self, role: ExactRestoreRole) -> CaptureTarget:
        try:
            return self._targets[role]
        except (KeyError, TypeError) as error:
            raise ExactRestoreRuntimeNotReady("restore_role_unknown") from error

    async def _discover_target(self, target: CaptureTarget) -> ResolvedCaptureEndpoint:
        try:
            provider = self._discovery_factory()
            discovered = await provider.discover(
                timeout_seconds=self._discovery_timeout_seconds,
            )
            return resolve_exact_endpoint(target, discovered)
        except CancelledError:
            raise
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("identity_not_exactly_resolved") from error

    def _fresh_read_session(self, address: str) -> ReadOnlySession:
        try:
            session = self._session_factory(address)
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("read_session_factory_failed") from error
        if any(session is issued for issued in self._issued_read_sessions):
            raise ExactRestoreRuntimeNotReady("read_session_not_fresh")
        self._issued_read_sessions.append(session)
        return session

    def _stamp(self, *, after: _ClockStamp | None = None) -> _ClockStamp:
        utc = self._utc_clock()
        monotonic_ns = self._monotonic_now()
        if not isinstance(utc, datetime) or utc.tzinfo is None or utc.utcoffset() != timedelta(0):
            raise ExactRestoreRuntimeNotReady("utc_clock_invalid")
        stamp = _ClockStamp(utc=utc, monotonic_ns=monotonic_ns)
        if after is not None and (stamp.utc < after.utc or stamp.monotonic_ns < after.monotonic_ns):
            raise ExactRestoreRuntimeNotReady("observation_clock_regressed")
        return stamp

    def _monotonic_now(self) -> int:
        value = self._monotonic_clock()
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ExactRestoreRuntimeNotReady("monotonic_clock_invalid")
        return value

    @staticmethod
    def _validate_capture(capture: RawStateCapture) -> bytes:
        if not isinstance(capture, RawStateCapture):
            raise ExactRestoreRuntimeNotReady("raw_capture_type_invalid")
        if capture.action != STATE_REPLY_ACTION:
            raise ExactRestoreRuntimeNotReady("explicit_reply_required")
        status = capture.status_payload
        if not isinstance(status, bytes) or len(status) != LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE:
            raise ExactRestoreRuntimeNotReady("status_payload_size_invalid")
        if not isinstance(capture.wire_frame, bytes):
            raise ExactRestoreRuntimeNotReady("wire_frame_invalid")
        try:
            frame = decode_frame(capture.wire_frame)
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("wire_frame_invalid") from error
        if (
            frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
            or frame.payload != bytes((STATE_REPLY_ACTION,)) + status
        ):
            raise ExactRestoreRuntimeNotReady("wire_frame_provenance_mismatch")
        return status

    @staticmethod
    def _decode_observation(
        *,
        role: ExactRestoreRole,
        target: CaptureTarget,
        capture: RawStateCapture,
        status: bytes,
        requested: _ClockStamp,
        observed: _ClockStamp,
        received: _ClockStamp,
    ) -> ExactRestoreObservation:
        try:
            values = get_product_schema(target.product_key).decode_status(status)
            enabled = values["SwitchON"]
            timer_enabled = values["TimerON"]
            linkage = values["Linkage"]
            mode = values["Mode"]
            power = values["Flow"]
            frequency = values["Frequency"]
            if type(enabled) is not bool or type(timer_enabled) is not bool:
                raise ValueError("outer booleans are invalid")
            if not isinstance(linkage, str) or not isinstance(mode, str):
                raise ValueError("outer enums are invalid")
            if type(power) is not int or type(frequency) is not int:
                raise ValueError("outer numeric values are invalid")
            schedule = LocalWavemakerProScheduleSnapshot.from_status(status).validate()
            outer = OuterControlSnapshot(
                enabled=enabled,
                timer_enabled=timer_enabled,
                linkage=LinkageRole(linkage),
                mode=mode,
                power=power,
                frequency=frequency,
            )
            exact_schedule = ExactScheduleImage.from_bytes(schedule.image)
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("status_decode_invalid") from error
        return ExactRestoreObservation(
            role=role,
            identity_binding_sha256=target.identity_binding_sha256,
            outer=outer,
            schedule=exact_schedule,
            raw_frame_sha256=hashlib.sha256(capture.wire_frame).hexdigest(),
            requested_at=requested.utc,
            observed_at=observed.utc,
            received_at=received.utc,
            requested_monotonic_ns=requested.monotonic_ns,
            observed_monotonic_ns=observed.monotonic_ns,
            received_monotonic_ns=received.monotonic_ns,
        )

    @staticmethod
    def _quarantine(session: ReadOnlySession) -> None:
        try:
            session.quarantine()
        except Exception:
            pass

    @staticmethod
    async def _discard_session(session: ReadOnlySession) -> None:
        try:
            await session.disconnect()
        except BaseException:
            pass


class _IdentityBoundOneActionRestoreAdapter:
    """One connected writer fenced by one post-connect discovery result."""

    def __init__(
        self,
        *,
        target: CaptureTarget,
        endpoint: ResolvedCaptureEndpoint,
        writer: RestoreWriter,
        resolve_endpoint: Callable[[CaptureTarget], Awaitable[ResolvedCaptureEndpoint]],
        identity_age_ns: int,
        monotonic_clock: MonotonicClock,
    ) -> None:
        self._target = target
        self._endpoint = endpoint
        self._writer = writer
        self._resolve_endpoint = resolve_endpoint
        self._identity_age_ns = identity_age_ns
        self._monotonic_clock = monotonic_clock
        self._connect_attempted = False
        self._connected = False
        self._identity_checked = False
        self._action_attempted = False
        self._ticket: _IdentityTicket | None = None

    @property
    def identity_binding_sha256(self) -> str:
        return self._target.identity_binding_sha256

    async def connect(self) -> None:
        if self._connect_attempted:
            raise ExactRestoreRuntimeNotReady("writer_connect_reused")
        self._connect_attempted = True
        if getattr(
            self._writer, "address", None
        ) != self._endpoint.address or not _writer_binding_matches(
            self._writer,
            self._target.identity_binding_sha256,
        ):
            raise ExactRestoreRuntimeNotReady("writer_endpoint_mismatch")
        try:
            await self._writer.connect()
        except CancelledError:
            raise
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("writer_connect_failed") from error
        if getattr(
            self._writer, "address", None
        ) != self._endpoint.address or not _writer_binding_matches(
            self._writer,
            self._target.identity_binding_sha256,
        ):
            raise ExactRestoreRuntimeNotReady("writer_endpoint_mismatch")
        self._connected = True

    async def disconnect(self) -> None:
        self._ticket = None
        self._connected = False
        try:
            await self._writer.disconnect()
        except CancelledError:
            raise
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("writer_disconnect_failed") from error

    async def read_connected_identity_binding_sha256(self) -> str:
        if (
            not self._connected
            or self._identity_checked
            or self._action_attempted
            or getattr(self._writer, "address", None) != self._endpoint.address
            or not _writer_binding_matches(
                self._writer,
                self._target.identity_binding_sha256,
            )
        ):
            raise ExactRestoreRuntimeNotReady("connected_identity_check_invalid")
        self._identity_checked = True
        try:
            current = await self._resolve_endpoint(self._target)
        except CancelledError:
            raise
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("connected_identity_not_exact") from error
        if (
            current != self._endpoint
            or getattr(self._writer, "address", None) != current.address
            or not _writer_binding_matches(
                self._writer,
                self._target.identity_binding_sha256,
            )
        ):
            raise ExactRestoreRuntimeNotReady("connected_identity_endpoint_changed")
        try:
            connected_session_token = self._writer.connected_session_token()
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("connected_session_token_unavailable") from error
        now = self._monotonic_now()
        self._ticket = _IdentityTicket(
            deadline_monotonic_ns=now + self._identity_age_ns,
            connected_session_token=connected_session_token,
        )
        return current.identity_binding_sha256

    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: Callable[[], bool] | None = None,
    ) -> None:
        connected_session_token = self._consume_ticket(guard)
        if (
            not isinstance(target, DeviceTarget)
            or target.model_fields_set != _EXACT_OUTER_FIELDS
            or target.mode is None
            or target.frequency is None
            or target.timer_enabled is None
            or target.linkage is not LinkageRole.INDEPENDENT
        ):
            raise ExactRestoreRuntimeNotReady("outer_target_invalid")
        await self._writer.write_target_connected(
            target,
            connected_session_token=connected_session_token,
            guard=guard,
        )

    async def restore_schedule_image(
        self,
        image: bytes,
        *,
        guard: Callable[[], bool] | None = None,
    ) -> object:
        connected_session_token = self._consume_ticket(guard)
        try:
            exact = validate_local_wavemaker_pro_schedule_image(image)
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("schedule_image_invalid") from error
        return await self._writer.restore_schedule_image_connected(
            exact,
            connected_session_token=connected_session_token,
            guard=guard,
        )

    def _consume_ticket(self, guard: Callable[[], bool] | None) -> object:
        if self._action_attempted:
            raise ExactRestoreRuntimeNotReady("writer_action_reused")
        self._action_attempted = True
        ticket = self._ticket
        self._ticket = None
        if not self._connected or ticket is None:
            raise ExactRestoreRuntimeNotReady("identity_ticket_missing")
        if self._monotonic_now() > ticket.deadline_monotonic_ns:
            raise ExactRestoreRuntimeNotReady("identity_ticket_expired")
        if getattr(
            self._writer, "address", None
        ) != self._endpoint.address or not _writer_binding_matches(
            self._writer,
            self._target.identity_binding_sha256,
        ):
            raise ExactRestoreRuntimeNotReady("writer_endpoint_mismatch")
        if guard is None:
            raise ExactRestoreRuntimeNotReady("write_guard_missing")
        try:
            permitted = guard()
        except Exception as error:
            raise ExactRestoreRuntimeNotReady("write_guard_failed") from error
        if permitted is not True:
            raise ExactRestoreRuntimeNotReady("write_guard_blocked")
        return ticket.connected_session_token

    def _monotonic_now(self) -> int:
        value = self._monotonic_clock()
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ExactRestoreRuntimeNotReady("monotonic_clock_invalid")
        return value


__all__ = [
    "ExactRestoreRuntimeNotReady",
    "FreshExplicitRestoreObserver",
    "RestoreWriter",
    "RestoreWriterFactory",
]
