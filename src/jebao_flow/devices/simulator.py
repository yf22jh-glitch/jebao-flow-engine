"""Async virtual device for development without a live aquarium."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jebao_flow.devices.base import (
    DeviceConnectionError,
    JebaoDevice,
    UnsupportedCapabilityError,
)
from jebao_flow.protocol.models import Capability, DeviceCapabilities, DeviceState


@dataclass(frozen=True, slots=True)
class SimulatedCommand:
    name: str
    value: Any
    issued_at: datetime


class SimulatedJebaoDevice(JebaoDevice):
    def __init__(
        self,
        device_id: str,
        *,
        capabilities: DeviceCapabilities | None = None,
        latency_seconds: float = 0,
    ) -> None:
        self._device_id = device_id
        self._capabilities = capabilities or DeviceCapabilities(
            model="simulator",
            readable=frozenset(Capability),
            writable=frozenset(
                {Capability.POWER, Capability.ENABLED, Capability.MODE, Capability.FREQUENCY}
            ),
        )
        self._latency_seconds = latency_seconds
        self._connected = False
        self._lock = asyncio.Lock()
        self._state = DeviceState(
            online=False,
            enabled=False,
            power=self._capabilities.power_limits.min_power,
            mode="constant",
            frequency=None,
        )
        self.commands: list[SimulatedCommand] = []

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def capabilities(self) -> DeviceCapabilities:
        return self._capabilities

    async def connect(self) -> None:
        async with self._lock:
            await self._delay()
            self._connected = True
            self._state = self._state.model_copy(
                update={"online": True, "observed_at": datetime.now(UTC)}
            )

    async def disconnect(self) -> None:
        async with self._lock:
            await self._delay()
            self._connected = False
            self._state = self._state.model_copy(
                update={"online": False, "observed_at": datetime.now(UTC)}
            )

    async def get_state(self) -> DeviceState:
        async with self._lock:
            self._require_connection()
            await self._delay()
            return self._state.model_copy(update={"observed_at": datetime.now(UTC)})

    async def set_enabled(self, enabled: bool) -> None:
        await self._write(Capability.ENABLED, "enabled", enabled)

    async def set_power(self, power: int) -> None:
        limits = self._capabilities.power_limits
        if not limits.min_power <= power <= limits.max_power:
            raise ValueError(
                f"power {power} is outside simulated device range "
                f"{limits.min_power}..{limits.max_power}"
            )
        if power % self._capabilities.power_step != 0:
            raise ValueError(f"power {power} does not match step {self._capabilities.power_step}")
        await self._write(Capability.POWER, "power", power)

    async def set_mode(self, mode: str) -> None:
        if not mode:
            raise ValueError("mode must not be empty")
        await self._write(Capability.MODE, "mode", mode)

    async def set_frequency(self, value: int) -> None:
        if not 0 <= value <= 100:
            raise ValueError("frequency must be between 0 and 100")
        await self._write(Capability.FREQUENCY, "frequency", value)

    async def simulate_connection_loss(self, error: str | None = None) -> None:
        async with self._lock:
            self._connected = False
            self._state = self._state.model_copy(
                update={"online": False, "error": error, "observed_at": datetime.now(UTC)}
            )

    async def _write(self, capability: Capability, field: str, value: Any) -> None:
        async with self._lock:
            self._require_connection()
            if capability not in self._capabilities.writable:
                raise UnsupportedCapabilityError(
                    f"{self._device_id} does not support writing {capability.value}"
                )
            await self._delay()
            now = datetime.now(UTC)
            self._state = self._state.model_copy(update={field: value, "observed_at": now})
            self.commands.append(SimulatedCommand(field, value, now))

    async def _delay(self) -> None:
        if self._latency_seconds:
            await asyncio.sleep(self._latency_seconds)

    def _require_connection(self) -> None:
        if not self._connected:
            raise DeviceConnectionError(f"simulated device {self._device_id!r} is offline")

