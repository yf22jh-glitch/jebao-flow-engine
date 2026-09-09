"""Fresh read-only device truth for the Home Assistant local-control surface.

Every cycle performs new discovery, opens one non-write-capable authenticated session per current
stable binding, reads once, and closes it. Passive captures remain corroborating evidence; they are
not used as current state because the registered devices have no observed unsolicited status push.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jebao_flow.local_control.catalog import (
    LocalControlCatalog,
    LocalFunctionDescriptor,
    public_function_value,
)
from jebao_flow.local_control.ha import (
    HomeAssistantEntityDescriptor,
    HomeAssistantPlatform,
    HomeAssistantProjection,
)
from jebao_flow.local_control.lease import LocalControlLeaseBusyError, LocalControlReadLease
from jebao_flow.local_control.models import FreshInstallManifest, LocalDeviceBinding
from jebao_flow.local_control.resolver import CurrentEndpointResolver, ResolvedEndpoint
from jebao_flow.protocol.models import DeviceSchedule
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.session import ReadOnlyGizwitsSession


class FreshStateError(RuntimeError):
    """A fresh state cycle could not produce current device truth."""


class RawReadOnlySession(Protocol):
    async def connect(self) -> None: ...

    async def authenticate(self) -> bytes: ...

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes: ...

    async def disconnect(self) -> None: ...

    def quarantine(self) -> None: ...


class SharedReadLease(Protocol):
    def acquire(self, physical_identity_key: str) -> AbstractContextManager[None]: ...


ReadSessionFactory = Callable[[str], RawReadOnlySession]


@dataclass(frozen=True, slots=True)
class StatePublication:
    """One bounded MQTT state update; an empty payload clears a stale retained scalar."""

    topic: str
    payload: str

    def __post_init__(self) -> None:
        if not self.topic.isascii() or not self.payload.isascii():
            raise ValueError("state publications must use ASCII")


@dataclass(frozen=True, slots=True)
class FreshDeviceState:
    """One device's current availability and safe public state projection."""

    logical_member: str
    available: bool
    publications: tuple[StatePublication, ...] = ()
    observed_at: datetime | None = None
    diagnostic_reason: str | None = None
    observed_power: bool | None = None

    def __post_init__(self) -> None:
        if not self.logical_member.isascii():
            raise ValueError("state logical member must use ASCII")
        if self.available != (self.observed_at is not None):
            raise ValueError("available state requires a current observation timestamp")
        if self.available and self.diagnostic_reason is not None:
            raise ValueError("available state cannot carry an unavailability reason")
        if not self.available and self.publications:
            raise ValueError("unavailable state cannot publish unverified values")
        if self.observed_power is not None and type(self.observed_power) is not bool:
            raise TypeError("observed power must be boolean or null")
        if not self.available and self.observed_power is not None:
            raise ValueError("unavailable state cannot carry observed power")
        if self.diagnostic_reason is not None and not self.diagnostic_reason.isascii():
            raise ValueError("state diagnostics must use ASCII")


@dataclass(frozen=True, slots=True)
class FreshStateProjection:
    """One decoded status projection, including qualification-independent power truth."""

    publications: tuple[StatePublication, ...]
    observed_power: bool | None

    def __post_init__(self) -> None:
        if self.observed_power is not None and type(self.observed_power) is not bool:
            raise TypeError("projected power must be boolean or null")


StatePublisher = Callable[[FreshDeviceState], Awaitable[None]]
FreshCycleObserver = Callable[[tuple[FreshDeviceState, ...]], Awaitable[None]]


class LocalControlStateProjector:
    """Project current raw status without importing the local-control write edge."""

    def __init__(
        self,
        *,
        manifest: FreshInstallManifest,
        projection: HomeAssistantProjection,
        catalog: LocalControlCatalog,
    ) -> None:
        known_members = {device.logical_member for device in manifest.devices}
        projected_members = {
            entity.logical_member
            for entity in projection.entities
            if entity.logical_member != "local_system"
        }
        if not projected_members.issubset(known_members):
            raise ValueError("state projection contains a non-current logical member")
        self._catalog = catalog
        self._entities_by_member = {
            member: tuple(
                entity for entity in projection.entities if entity.logical_member == member
            )
            for member in known_members
        }

    def project(
        self,
        target: LocalDeviceBinding,
        raw_status: bytes,
        *,
        observed_at: datetime,
    ) -> tuple[StatePublication, ...]:
        """Preserve the existing publication-only API for direct callers."""

        return self.project_fresh_state(
            target,
            raw_status,
            observed_at=observed_at,
        ).publications

    def project_fresh_state(
        self,
        target: LocalDeviceBinding,
        raw_status: bytes,
        *,
        observed_at: datetime,
    ) -> FreshStateProjection:
        """Decode once and return both public entities and typed physical power truth."""

        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("fresh state timestamp must be timezone-aware")
        schema = get_product_schema(target.product_key)
        decoded = schema.decode_status(bytes(raw_status))
        timer_value = decoded.get("TimerON", False)
        schedule = decode_schedule(
            target.product_key,
            raw_status,
            enabled=timer_value if isinstance(timer_value, bool) else False,
        )
        schedule_entries = (
            {} if schedule is None else {entry.slot: entry for entry in schedule.entries}
        )
        publications: list[StatePublication] = []
        for entity in self._entities_by_member.get(target.logical_member, ()):
            descriptor = self._catalog.find(target.product_key, entity.function)
            if entity.slot_index is not None:
                entry = schedule_entries.get(entity.slot_index)
                flow = None if entry is None else entry.parameters.get("flow")
                publications.append(
                    StatePublication(
                        entity.state_topic,
                        _state_value(entity, flow) or "",
                    )
                )
                continue
            if entity.control_available:
                if entity.platform is HomeAssistantPlatform.BUTTON:
                    continue
                value = _primary_observed_value(descriptor, decoded)
                value = public_function_value(target.product_key, entity.function, value)
                publications.append(
                    StatePublication(
                        entity.state_topic,
                        _state_value(entity, value) or "",
                    )
                )
                continue
            diagnostic: dict[str, object] = {
                "stage": entity.stage,
                "control_available": False,
                "function": entity.function,
                "diagnostic_reason": entity.diagnostic_reason,
                "observed_at": observed_at.isoformat(),
            }
            observed = _diagnostic_observation(
                target.product_key,
                descriptor,
                decoded,
                schedule,
            )
            if observed is not None:
                diagnostic["observed"] = observed
            publications.append(
                StatePublication(entity.state_topic, _canonical_json(diagnostic))
            )
        try:
            power_descriptor = self._catalog.find(target.product_key, "power")
        except KeyError:
            observed_power = None
        else:
            candidate = _primary_observed_value(power_descriptor, decoded)
            observed_power = candidate if type(candidate) is bool else None
        return FreshStateProjection(
            publications=tuple(publications),
            observed_power=observed_power,
        )


class FreshStatePoller:
    """Single-flight periodic state reads with one network attempt per device and cycle."""

    def __init__(
        self,
        *,
        manifest: FreshInstallManifest,
        resolver: CurrentEndpointResolver,
        projector: LocalControlStateProjector,
        poll_interval_seconds: float,
        reconnect_initial_seconds: float,
        reconnect_max_seconds: float,
        read_lease: SharedReadLease | None = None,
        session_factory: ReadSessionFactory = ReadOnlyGizwitsSession,
        operation_lock: asyncio.Lock | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("state poll interval must be positive")
        if reconnect_initial_seconds <= 0 or reconnect_max_seconds <= 0:
            raise ValueError("state reconnect delays must be positive")
        if reconnect_initial_seconds > reconnect_max_seconds:
            raise ValueError("state reconnect initial delay must not exceed its maximum")
        self._targets = manifest.devices
        self._resolver = resolver
        self._projector = projector
        self._poll_interval_seconds = poll_interval_seconds
        self._reconnect_initial_seconds = reconnect_initial_seconds
        self._reconnect_max_seconds = reconnect_max_seconds
        self._read_lease = read_lease or LocalControlReadLease()
        self._session_factory = session_factory
        self._operation_lock = operation_lock or asyncio.Lock()
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def logical_members(self) -> tuple[str, ...]:
        return tuple(target.logical_member for target in self._targets)

    async def poll_once(self) -> tuple[FreshDeviceState, ...]:
        try:
            async with self._operation_lock:
                endpoints = await self._resolver.resolve_many(self._targets)
        except Exception:
            return tuple(
                _unavailable(target, "current_discovery_unavailable")
                for target in self._targets
            )
        by_member = {endpoint.target.logical_member: endpoint for endpoint in endpoints}
        if (
            len(endpoints) != len(self._targets)
            or set(by_member) != {target.logical_member for target in self._targets}
            or any(by_member[target.logical_member].target != target for target in self._targets)
        ):
            return tuple(
                _unavailable(target, "current_resolution_binding_mismatch")
                for target in self._targets
            )

        results: list[FreshDeviceState] = []
        for target in self._targets:
            endpoint = by_member[target.logical_member]
            try:
                async with self._operation_lock:
                    with self._read_lease.acquire(target.physical_identity_key):
                        raw_status = await self._read_once(endpoint)
            except LocalControlLeaseBusyError:
                results.append(_unavailable(target, "device_write_in_progress"))
                continue
            except Exception:
                results.append(_unavailable(target, "fresh_read_unavailable"))
                continue
            observed_at = self._now()
            try:
                projection = self._projector.project_fresh_state(
                    target,
                    raw_status,
                    observed_at=observed_at,
                )
            except Exception:
                results.append(_unavailable(target, "fresh_projection_unavailable"))
                continue
            results.append(
                FreshDeviceState(
                    logical_member=target.logical_member,
                    available=True,
                    publications=projection.publications,
                    observed_at=observed_at,
                    observed_power=projection.observed_power,
                )
            )
        return tuple(results)

    async def run(
        self,
        publish: StatePublisher,
        stop_event: asyncio.Event,
        *,
        observe_cycle: FreshCycleObserver | None = None,
    ) -> None:
        backoff = self._reconnect_initial_seconds
        while not stop_event.is_set():
            results = await self.poll_once()
            for result in results:
                await publish(result)
            if observe_cycle is not None:
                await observe_cycle(results)
            all_available = all(result.available for result in results)
            if all_available:
                delay = self._poll_interval_seconds
                backoff = self._reconnect_initial_seconds
            else:
                delay = max(self._poll_interval_seconds, backoff)
                backoff = min(self._reconnect_max_seconds, backoff * 2)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _read_once(self, endpoint: ResolvedEndpoint) -> bytes:
        session = self._session_factory(endpoint.address)
        try:
            await session.connect()
            await session.authenticate()
            return await session.read_raw_state(accept_reports=True)
        except BaseException:
            session.quarantine()
            raise
        finally:
            await session.disconnect()


def _unavailable(target: LocalDeviceBinding, reason: str) -> FreshDeviceState:
    return FreshDeviceState(
        logical_member=target.logical_member,
        available=False,
        diagnostic_reason=reason,
    )


def _primary_observed_value(
    descriptor: LocalFunctionDescriptor,
    decoded: Mapping[str, object],
) -> object | None:
    for field in descriptor.fields:
        if field in decoded:
            return decoded[field]
    return None


def _diagnostic_observation(
    product_key: str,
    descriptor: LocalFunctionDescriptor,
    decoded: Mapping[str, object],
    schedule: DeviceSchedule | None,
) -> object | None:
    if descriptor.function in {
        "schedule",
        "schedule_reset",
        "slave_per_slot_flow_extension",
    }:
        return _schedule_summary(product_key, schedule)
    if descriptor.function == "device_clock_sync":
        local_time = getattr(schedule, "device_local_time", None)
        return {"device_local_time": None if local_time is None else local_time.isoformat()}
    selected = {
        field: _json_safe(_public_observed_field(product_key, field, decoded[field]))
        for field in descriptor.fields
        if field in decoded
    }
    return selected or None


def _schedule_summary(product_key: str, schedule: DeviceSchedule | None) -> object | None:
    if schedule is None:
        return None
    entries = []
    for entry in schedule.entries:
        entries.append(
            {
                "slot": entry.slot,
                "start": entry.start,
                "end": entry.end,
                "mode": public_function_value(product_key, "manual_mode", entry.mode),
                "mode_code": entry.mode_code,
                "parameters": _json_safe(entry.parameters),
            }
        )
    local_time = schedule.device_local_time
    return {
        "enabled": schedule.enabled,
        "device_local_time": None if local_time is None else local_time.isoformat(),
        "active_slot_count": len(entries),
        "invalid_slot_indexes": list(schedule.invalid_slots),
        "entries": entries,
    }


def _public_observed_field(product_key: str, field: str, value: object) -> object:
    if field in {"Mode", "AutoMode"}:
        return public_function_value(product_key, "manual_mode", value)
    return value


def _state_value(entity: HomeAssistantEntityDescriptor, value: object) -> str | None:
    if entity.platform is HomeAssistantPlatform.SWITCH:
        return "ON" if value is True else "OFF" if value is False else None
    if entity.platform is HomeAssistantPlatform.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            if value.is_integer():
                value = int(value)
        return str(value)
    if entity.platform is HomeAssistantPlatform.SELECT:
        return str(value) if isinstance(value, str) and value in entity.options else None
    return None


def _json_safe(value: object) -> object:
    if isinstance(value, bytes):
        return {"length": len(value), "value_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise FreshStateError("non-finite device state cannot be published")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise FreshStateError("unsupported device state cannot be published")


def _canonical_json(value: object) -> str:
    payload = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if not payload.isascii():
        raise FreshStateError("fresh state JSON must use ASCII")
    return payload


__all__ = [
    "FreshCycleObserver",
    "FreshDeviceState",
    "FreshStateProjection",
    "FreshStateError",
    "FreshStatePoller",
    "LocalControlStateProjector",
    "RawReadOnlySession",
    "ReadSessionFactory",
    "StatePublication",
]
