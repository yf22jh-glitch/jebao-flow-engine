"""Async virtual device for development without a live aquarium."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jebao_flow.devices.base import (
    AckUnconfirmedHook,
    ControlVerificationOutcome,
    DeviceConnectionError,
    JebaoDevice,
    SafetyInterlockError,
    UnsupportedCapabilityError,
    WriteGuard,
)
from jebao_flow.devices.identity import PhysicalDeviceBinding, configuration_fingerprint
from jebao_flow.protocol.models import (
    Capability,
    DeviceCapabilities,
    DeviceSchedule,
    DeviceState,
    DeviceTarget,
    LinkageRole,
)


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
            product_key="simulator",
            readable=frozenset(Capability),
            writable=frozenset(
                {
                    Capability.POWER,
                    Capability.ENABLED,
                    Capability.MODE,
                    Capability.FREQUENCY,
                    Capability.LINKAGE,
                    Capability.TIMER,
                }
            ),
            native_modes=frozenset({"constant", "pulse", "sine"}),
            linkage_roles=frozenset(LinkageRole),
        )
        product_key = self._capabilities.product_key
        self._physical_binding = (
            PhysicalDeviceBinding.from_identifiers(
                vendor_device_id=f"simulated-{device_id}",
                mac_address=hashlib.sha256(device_id.encode()).hexdigest()[:12],
                product_key=product_key,
                config_fingerprint=configuration_fingerprint(
                    {
                        "device_id": device_id,
                        "model": self._capabilities.model,
                        "product_key": product_key,
                    }
                ),
            )
            if product_key is not None
            else None
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
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
            schedule=DeviceSchedule(enabled=False),
        )
        self.commands: list[SimulatedCommand] = []

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_binding(self) -> PhysicalDeviceBinding | None:
        return self._physical_binding

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
        await self.write_power(power)

    async def write_power(
        self,
        power: int,
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
    ) -> ControlVerificationOutcome:
        del on_ack_unconfirmed
        limits = self._capabilities.power_limits
        if not limits.min_power <= power <= limits.max_power:
            raise ValueError(
                f"power {power} is outside simulated device range "
                f"{limits.min_power}..{limits.max_power}"
            )
        if power % self._capabilities.power_step != 0:
            raise ValueError(f"power {power} does not match step {self._capabilities.power_step}")
        async with self._lock:
            self._require_connection()
            if Capability.POWER not in self._capabilities.writable:
                raise UnsupportedCapabilityError(
                    f"{self._device_id} does not support writing power"
                )
            await self._delay()
            if guard is not None and guard() is not True:
                raise SafetyInterlockError(
                    "simulated device power write was blocked by the safety interlock"
                )
            now = datetime.now(UTC)
            self._state = self._state.model_copy(update={"power": power, "observed_at": now})
            self.commands.append(SimulatedCommand("power", power, now))
            return ControlVerificationOutcome.STATE_VERIFIED

    async def set_mode(self, mode: str) -> None:
        if not mode:
            raise ValueError("mode must not be empty")
        await self._write(Capability.MODE, "mode", mode)

    async def set_frequency(self, value: int) -> None:
        if not 0 <= value <= 100:
            raise ValueError("frequency must be between 0 and 100")
        await self._write(Capability.FREQUENCY, "frequency", value)

    async def set_linkage(self, role: LinkageRole) -> None:
        await self.write_linkage(role)

    async def write_linkage(
        self,
        role: LinkageRole,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        if not isinstance(role, LinkageRole):
            raise TypeError("linkage role must be a LinkageRole")
        if role not in self._capabilities.linkage_roles:
            raise UnsupportedCapabilityError(
                f"{self._device_id} does not support linkage {role.value}"
            )
        async with self._lock:
            self._require_connection()
            if Capability.LINKAGE not in self._capabilities.writable:
                raise UnsupportedCapabilityError(
                    f"{self._device_id} does not support writing linkage"
                )
            await self._delay()
            if guard is not None and guard() is not True:
                raise SafetyInterlockError(
                    "simulated device linkage write was blocked by the safety interlock"
                )
            now = datetime.now(UTC)
            self._state = self._state.model_copy(
                update={"linkage": role, "observed_at": now}
            )
            self.commands.append(SimulatedCommand("linkage", role, now))

    async def set_timer_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("timer enabled must be a boolean")
        await self._write(Capability.TIMER, "timer_enabled", enabled)

    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        """Apply one validated target as a single simulated controller update."""

        limits = self._capabilities.power_limits
        if Capability.ENABLED not in self._capabilities.writable:
            raise UnsupportedCapabilityError(
                f"{self._device_id} does not support writing enabled"
            )
        if target.enabled and Capability.POWER not in self._capabilities.writable:
            raise UnsupportedCapabilityError(
                f"{self._device_id} does not support writing power"
            )
        if target.enabled and not limits.min_power <= target.power <= limits.max_power:
            raise ValueError(
                f"power {target.power} is outside simulated device range "
                f"{limits.min_power}..{limits.max_power}"
            )
        if target.enabled and target.power % self._capabilities.power_step != 0:
            raise ValueError(
                f"power {target.power} does not match step {self._capabilities.power_step}"
            )
        if target.mode is not None and Capability.MODE not in self._capabilities.writable:
            raise UnsupportedCapabilityError(
                f"{self._device_id} does not support writing mode"
            )
        if target.frequency is not None:
            if Capability.FREQUENCY not in self._capabilities.writable:
                raise UnsupportedCapabilityError(
                    f"{self._device_id} does not support writing frequency"
                )
        if target.linkage is not None:
            if Capability.LINKAGE not in self._capabilities.writable:
                raise UnsupportedCapabilityError(
                    f"{self._device_id} does not support writing linkage"
                )
            if target.linkage not in self._capabilities.linkage_roles:
                raise UnsupportedCapabilityError(
                    f"{self._device_id} does not support linkage {target.linkage.value}"
                )
        if (
            target.timer_enabled is not None
            and Capability.TIMER not in self._capabilities.writable
        ):
            raise UnsupportedCapabilityError(
                f"{self._device_id} does not support writing timer"
            )

        async with self._lock:
            self._require_connection()
            await self._delay()
            if guard is not None and guard() is not True:
                raise SafetyInterlockError(
                    "simulated device write was blocked by the safety interlock"
                )
            now = datetime.now(UTC)
            updates: dict[str, Any] = {
                "enabled": target.enabled,
                "observed_at": now,
            }
            command_values: list[tuple[str, Any]] = [("enabled", target.enabled)]
            if target.timer_enabled is not None:
                updates["timer_enabled"] = target.timer_enabled
                if self._state.schedule is not None:
                    updates["schedule"] = self._state.schedule.model_copy(
                        update={"enabled": target.timer_enabled}
                    )
                command_values.append(("timer_enabled", target.timer_enabled))
            if target.linkage is not None:
                updates["linkage"] = target.linkage
                command_values.append(("linkage", target.linkage))
            if target.enabled:
                updates["power"] = target.power
                command_values.append(("power", target.power))
                if target.mode is not None:
                    updates["mode"] = target.mode
                    command_values.append(("mode", target.mode))
                if target.frequency is not None:
                    updates["frequency"] = target.frequency
                    command_values.append(("frequency", target.frequency))
            self._state = self._state.model_copy(update=updates)
            self.commands.extend(
                SimulatedCommand(name, value, now) for name, value in command_values
            )

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
            updates = {field: value, "observed_at": now}
            if field == "timer_enabled" and self._state.schedule is not None:
                updates["schedule"] = self._state.schedule.model_copy(
                    update={"enabled": value}
                )
            self._state = self._state.model_copy(update=updates)
            self.commands.append(SimulatedCommand(field, value, now))

    async def _delay(self) -> None:
        if self._latency_seconds:
            await asyncio.sleep(self._latency_seconds)

    def _require_connection(self) -> None:
        if not self._connected:
            raise DeviceConnectionError(f"simulated device {self._device_id!r} is offline")
