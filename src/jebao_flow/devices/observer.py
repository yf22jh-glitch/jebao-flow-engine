"""Persistent, strictly read-only observation of configured Jebao devices."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from jebao_flow.config import AppConfig, DeviceConfig, DeviceType, ObserverConfig
from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.factory import create_read_only_lan_device
from jebao_flow.protocol.discovery import DiscoveryProvider, GizwitsDiscovery
from jebao_flow.protocol.models import DeviceState, DiscoveredDevice

_LOGGER = logging.getLogger(__name__)
_PRODUCT_TYPES = {
    "0696a19599bc484f8e1866f5ccf4ee7e": DeviceType.RETURN_PUMP,
    "1d8c63eaccac4205b92c84d77d5a08fb": DeviceType.WAVEMAKER,
    "50dbc92221fd4d33ae69a1fedd43b555": DeviceType.WAVEMAKER,
    "5b3c136fd4b74f3fb2a366a254c76c9a": DeviceType.DOSING_PUMP,
    "6a5c47b3ea364ecb841b47f5997a1775": DeviceType.RETURN_PUMP,
}


class ObserverStatus(StrEnum):
    UNMAPPED = "unmapped"
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class ResolvedDevice:
    logical_id: str
    address: str
    product_key: str


@dataclass(frozen=True, slots=True)
class ObserverEvent:
    device_id: str
    status: ObserverStatus
    occurred_at: datetime
    state: DeviceState | None = None
    error: str | None = None


ObserverSink = Callable[[ObserverEvent], None]
DeviceFactory = Callable[[DeviceConfig, str, str], JebaoDevice]
InterruptibleWaiter = Callable[[asyncio.Event, float], Awaitable[bool]]


def _compact_mac(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace(":", "").replace("-", "").lower()


def resolve_device_bindings(
    configs: Sequence[DeviceConfig],
    discovered: Sequence[DiscoveredDevice],
) -> dict[str, ResolvedDevice]:
    """Resolve only exact, stable identities; never infer by model, order, or product key."""

    resolved: dict[str, ResolvedDevice] = {}
    claimed_vendor_ids: set[str] = set()
    claimed_endpoints: set[tuple[str, str]] = set()
    for config in configs:
        identity = config.identity
        if identity is None:
            if config.address is not None and config.product_key is not None:
                endpoint_key = (config.address, config.product_key)
                if (
                    _PRODUCT_TYPES.get(config.product_key) is config.type
                    and endpoint_key not in claimed_endpoints
                ):
                    claimed_endpoints.add(endpoint_key)
                    resolved[config.id] = ResolvedDevice(
                        logical_id=config.id,
                        address=config.address,
                        product_key=config.product_key,
                    )
                continue
            if config.address is not None:
                candidates = [
                    candidate
                    for candidate in discovered
                    if candidate.address == config.address
                    and candidate.product_key is not None
                    and _PRODUCT_TYPES.get(candidate.product_key) is config.type
                ]
                if len(candidates) == 1:
                    candidate = candidates[0]
                    endpoint_key = (candidate.address, candidate.product_key)
                    if endpoint_key in claimed_endpoints:
                        continue
                    claimed_endpoints.add(endpoint_key)
                    resolved[config.id] = ResolvedDevice(
                        logical_id=config.id,
                        address=candidate.address,
                        product_key=candidate.product_key,  # type: ignore[arg-type]
                    )
            continue

        candidates = list(discovered)
        if identity.device_id is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.device_id == identity.device_id
            ]
        if identity.mac_address is not None:
            candidates = [
                candidate
                for candidate in candidates
                if _compact_mac(candidate.mac_address) == identity.mac_address
            ]
        if config.product_key is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.product_key == config.product_key
            ]

        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        if (
            candidate.device_id in claimed_vendor_ids
            or candidate.product_key is None
            or _PRODUCT_TYPES.get(candidate.product_key) is not config.type
        ):
            continue
        claimed_vendor_ids.add(candidate.device_id)
        endpoint_key = (candidate.address, candidate.product_key)
        if endpoint_key in claimed_endpoints:
            continue
        claimed_endpoints.add(endpoint_key)
        resolved[config.id] = ResolvedDevice(
            logical_id=config.id,
            address=candidate.address,
            product_key=candidate.product_key,
        )
    return resolved


async def wait_for_stop(stop_event: asyncio.Event, delay_seconds: float) -> bool:
    """Return true when stop wins, false when the delay expires."""

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
    except TimeoutError:
        return False
    return True


class JsonlObservationJournal:
    """Append a privacy-minimal audit record for meaningful observer transitions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def append(
        self,
        event: str,
        device_id: str,
        occurred_at: datetime,
        *,
        previous: DeviceState | None = None,
        current: DeviceState | None = None,
        error: str | None = None,
    ) -> None:
        record = {
            "schema_version": 1,
            "event": event,
            "device_id": device_id,
            "occurred_at": occurred_at.isoformat(),
            "previous": self._safe_state(previous),
            "current": self._safe_state(current),
            "error": error,
            "observation_source": "lan_poll",
            "change_source": "external_or_native",
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            async with self._lock:
                await asyncio.to_thread(self._append_line, line)
        except OSError as exc:
            _LOGGER.warning(
                "observer_journal_write_failed",
                extra={"error": str(exc), "device_id": device_id},
            )

    def _append_line(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)

    @staticmethod
    def _safe_state(state: DeviceState | None) -> dict[str, object] | None:
        if state is None:
            return None
        return {
            "enabled": state.enabled,
            "power": state.power,
            "mode": state.mode,
            "frequency": state.frequency,
            "error": state.error,
            "observed_attributes": state.observed_attributes,
            "observed_at": state.observed_at.isoformat(),
        }


class ReadOnlyObserver:
    """Discover devices and poll each resolved endpoint in an independent worker."""

    def __init__(
        self,
        config: AppConfig,
        sink: ObserverSink,
        *,
        discovery: DiscoveryProvider | None = None,
        device_factory: DeviceFactory | None = None,
        waiter: InterruptibleWaiter = wait_for_stop,
        journal: JsonlObservationJournal | None = None,
    ) -> None:
        self._settings: ObserverConfig = config.observer
        self._configs = tuple(device for device in config.devices if device.enabled)
        self._sink = sink
        self._discovery = discovery or GizwitsDiscovery(
            targets=self._settings.targets,
            bind_address=self._settings.bind_address,
        )
        self._device_factory = device_factory or (
            lambda device, address, product_key: create_read_only_lan_device(
                device,
                address,
                product_key,
            )
        )
        self._waiter = waiter
        self._journal = journal or JsonlObservationJournal(self._settings.journal_path)
        self._workers: dict[str, tuple[ResolvedDevice, asyncio.Task[None]]] = {}
        self._last_states: dict[str, DeviceState] = {}
        self._offline: set[str] = set()

    async def run(self, stop_event: asyncio.Event) -> None:
        if not self._settings.enabled:
            await stop_event.wait()
            return

        try:
            while not stop_event.is_set():
                await self._reconcile(stop_event)
                if await self._waiter(
                    stop_event,
                    self._settings.rediscovery_interval_seconds,
                ):
                    break
        finally:
            tasks = [worker for _, worker in self._workers.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._workers.clear()

    async def _reconcile(self, stop_event: asyncio.Event) -> None:
        discovered: list[DiscoveredDevice] = []
        requires_discovery = any(
            device.identity is not None
            or device.address is None
            or device.product_key is None
            for device in self._configs
        )
        if requires_discovery:
            try:
                discovered = await self._discovery.discover(
                    timeout_seconds=self._settings.discovery_timeout_seconds
                )
            except (OSError, RuntimeError) as exc:
                _LOGGER.warning("observer_discovery_failed", extra={"error": str(exc)})
                return

        resolved = resolve_device_bindings(self._configs, discovered)
        by_id = {device.id: device for device in self._configs}
        for device_id, device_config in by_id.items():
            endpoint = resolved.get(device_id)
            current = self._workers.get(device_id)
            if endpoint is None:
                if current is not None:
                    current[1].cancel()
                    await asyncio.gather(current[1], return_exceptions=True)
                    self._workers.pop(device_id, None)
                self._sink(
                    ObserverEvent(
                        device_id=device_id,
                        status=ObserverStatus.UNMAPPED,
                        occurred_at=datetime.now(UTC),
                        error="stable_identity_not_resolved",
                    )
                )
                continue
            if current is not None and current[0] == endpoint and not current[1].done():
                continue
            if current is not None:
                current[1].cancel()
                await asyncio.gather(current[1], return_exceptions=True)
            task = asyncio.create_task(
                self._observe_device(device_config, endpoint, stop_event),
                name=f"observer-{device_id}",
            )
            self._workers[device_id] = (endpoint, task)

    async def _observe_device(
        self,
        config: DeviceConfig,
        endpoint: ResolvedDevice,
        stop_event: asyncio.Event,
    ) -> None:
        backoff = self._settings.reconnect_initial_seconds
        while not stop_event.is_set():
            device: JebaoDevice | None = None
            self._sink(
                ObserverEvent(
                    device_id=config.id,
                    status=ObserverStatus.CONNECTING,
                    occurred_at=datetime.now(UTC),
                )
            )
            read_succeeded = False
            try:
                device = self._device_factory(config, endpoint.address, endpoint.product_key)
                await device.connect()
                while not stop_event.is_set():
                    state = await device.get_state()
                    read_succeeded = True
                    backoff = self._settings.reconnect_initial_seconds
                    await self._report_state(config.id, state)
                    if await self._waiter(stop_event, self._settings.poll_interval_seconds):
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # device/protocol implementations expose several error types
                error = f"{type(exc).__name__}: {exc}"[:500]
                await self._report_offline(config.id, error)
            finally:
                try:
                    if device is not None:
                        await device.disconnect()
                except Exception as exc:  # pragma: no cover - best-effort close
                    _LOGGER.debug(
                        "observer_disconnect_failed",
                        extra={"device_id": config.id, "error": str(exc)},
                    )

            if stop_event.is_set() or await self._waiter(stop_event, backoff):
                return
            if not read_succeeded:
                backoff = min(backoff * 2, self._settings.reconnect_max_seconds)

    async def _report_state(self, device_id: str, state: DeviceState) -> None:
        previous = self._last_states.get(device_id)
        recovered = device_id in self._offline
        self._offline.discard(device_id)
        self._last_states[device_id] = state
        self._sink(
            ObserverEvent(
                device_id=device_id,
                status=ObserverStatus.ONLINE,
                occurred_at=state.observed_at,
                state=state,
            )
        )

        if previous is None:
            await self._journal.append(
                "first_seen",
                device_id,
                state.observed_at,
                current=state,
            )
        elif recovered:
            await self._journal.append(
                "recovered",
                device_id,
                state.observed_at,
                previous=previous,
                current=state,
            )
        elif self._state_signature(previous) != self._state_signature(state):
            await self._journal.append(
                "state_changed",
                device_id,
                state.observed_at,
                previous=previous,
                current=state,
            )

    async def _report_offline(self, device_id: str, error: str) -> None:
        occurred_at = datetime.now(UTC)
        first_offline_event = device_id not in self._offline
        self._offline.add(device_id)
        self._sink(
            ObserverEvent(
                device_id=device_id,
                status=ObserverStatus.OFFLINE,
                occurred_at=occurred_at,
                error=error,
            )
        )
        if first_offline_event:
            await self._journal.append(
                "offline",
                device_id,
                occurred_at,
                previous=self._last_states.get(device_id),
                error=error,
            )

    @staticmethod
    def _state_signature(state: DeviceState) -> tuple[object, ...]:
        return (
            state.enabled,
            state.power,
            state.mode,
            state.frequency,
            state.error,
            tuple(sorted(state.observed_attributes.items())),
        )


__all__ = [
    "JsonlObservationJournal",
    "ObserverEvent",
    "ObserverStatus",
    "ReadOnlyObserver",
    "ResolvedDevice",
    "resolve_device_bindings",
    "wait_for_stop",
]
