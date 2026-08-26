"""Safety-gated physical Jebao device adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from jebao_flow.devices.base import (
    HardwareWritesDisabledError,
    JebaoDevice,
    SafetyInterlockError,
    StateVerificationError,
    UnsupportedCapabilityError,
    WriteGuard,
)
from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.models import (
    Capability,
    DeviceCapabilities,
    DeviceSchedule,
    DeviceState,
    DeviceTarget,
    LinkageRole,
)
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.schema import DataType
from jebao_flow.protocol.session import GizwitsSession
from jebao_flow.safety.limits import PowerLimits


class RawSession(Protocol):
    @property
    def connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def authenticate(self) -> bytes: ...

    async def read_raw_state(self) -> bytes: ...

    async def send_raw_control(self, control_payload: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ControlPlan:
    product_key: str
    changes: Mapping[str, Any]
    payload: bytes


SessionFactory = Callable[[str], RawSession]
_LINKAGE_ROLES_BY_VALUE = {role.value: role for role in LinkageRole}


class LanJebaoDevice(JebaoDevice):
    """Turns protocol-neutral targets into product-specific, verified writes.

    Hardware writes are locked by default. A caller must explicitly opt in after reviewing a
    ``ControlPlan``; the public diagnostic CLI never opts in.
    """

    def __init__(
        self,
        device_id: str,
        address: str,
        product_key: str,
        *,
        power_limits: PowerLimits | None = None,
        power_step: int = 1,
        minimum_command_interval_ms: int = 1000,
        readback_delay_ms: int = 500,
        readback_attempts: int = 3,
        allow_hardware_writes: bool = False,
        physical_binding: PhysicalDeviceBinding | None = None,
        session_factory: SessionFactory = GizwitsSession,
    ) -> None:
        if not device_id or not address:
            raise ValueError("device id and address are required")
        if not 1 <= power_step <= 100:
            raise ValueError("power step must be between 1 and 100")
        if minimum_command_interval_ms < 100:
            raise ValueError("minimum command interval must be at least 100ms")
        if readback_delay_ms < 0:
            raise ValueError("read-back delay must be non-negative")
        if readback_attempts < 1:
            raise ValueError("read-back attempts must be positive")

        self._device_id = device_id
        self.address = address
        self.schema = get_product_schema(product_key)
        if physical_binding is not None and physical_binding.product_key != product_key:
            raise ValueError("physical binding product key does not match the device schema")
        self._physical_binding = physical_binding
        self._power_limits = power_limits or PowerLimits()
        self._power_step = power_step
        self._minimum_command_interval = minimum_command_interval_ms / 1000
        self._readback_delay = readback_delay_ms / 1000
        self._readback_attempts = readback_attempts
        self._allow_hardware_writes = allow_hardware_writes
        self._session = session_factory(address)
        self._io_lock = asyncio.Lock()
        self._last_command_at: float | None = None
        self._last_sent_values: dict[str, Any] = {}

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_binding(self) -> PhysicalDeviceBinding | None:
        return self._physical_binding

    @property
    def connected(self) -> bool:
        return self._session.connected

    @property
    def capabilities(self) -> DeviceCapabilities:
        readable: set[Capability] = {Capability.ERROR}
        writable: set[Capability] = set()
        native_modes: frozenset[str] = frozenset()
        linkage_roles: frozenset[LinkageRole] = frozenset()
        if self.schema.enabled_attribute:
            readable.add(Capability.ENABLED)
            if self.schema.control_supported:
                writable.add(Capability.ENABLED)
        if self.schema.power_attribute:
            readable.add(Capability.POWER)
            if self.schema.control_supported:
                writable.add(Capability.POWER)
        if self.schema.mode_attribute:
            readable.add(Capability.MODE)
            mode = self.schema.by_name(self.schema.mode_attribute)
            native_modes = frozenset(mode.enum_values)
            if self.schema.control_supported and (
                mode.data_type is DataType.ENUM or mode.enum_values
            ):
                writable.add(Capability.MODE)
        if self.schema.frequency_attribute:
            readable.add(Capability.FREQUENCY)
            if self.schema.control_supported:
                writable.add(Capability.FREQUENCY)
        if self.schema.linkage_attribute:
            readable.add(Capability.LINKAGE)
            linkage = self.schema.by_name(self.schema.linkage_attribute)
            linkage_roles = frozenset(
                _LINKAGE_ROLES_BY_VALUE[value]
                for value in linkage.enum_values
                if value in _LINKAGE_ROLES_BY_VALUE
            )
            if self.schema.control_supported and linkage.data_type is DataType.ENUM:
                writable.add(Capability.LINKAGE)
        if self.schema.timer_attribute:
            readable.add(Capability.TIMER)
            if self.schema.control_supported:
                writable.add(Capability.TIMER)
        return DeviceCapabilities(
            model=self.schema.name,
            product_key=self.schema.product_key,
            readable=frozenset(readable),
            writable=frozenset(writable),
            power_limits=self._power_limits,
            power_step=self._power_step,
            native_modes=native_modes,
            linkage_roles=linkage_roles,
        )

    async def connect(self) -> None:
        async with self._io_lock:
            await self._session.connect()
            await self._session.authenticate()
            self._last_command_at = None
            self._last_sent_values.clear()

    async def disconnect(self) -> None:
        async with self._io_lock:
            await self._session.disconnect()
            self._last_command_at = None
            self._last_sent_values.clear()

    async def get_state(self) -> DeviceState:
        async with self._io_lock:
            raw = await self._session.read_raw_state()
            values = self.schema.decode_status(raw)
            schedule = decode_schedule(
                self.schema.product_key,
                raw,
                enabled=bool(values.get(self.schema.timer_attribute, False)),
            )
            return self._to_device_state(values, schedule=schedule)

    async def set_enabled(self, enabled: bool) -> None:
        attribute = self._require_logical_attribute(Capability.ENABLED)
        await self._apply_changes({attribute: enabled})

    async def set_power(self, power: int) -> None:
        self._validate_power(power)
        attribute = self._require_logical_attribute(Capability.POWER)
        await self._apply_changes({attribute: power})

    async def set_mode(self, mode: str) -> None:
        attribute_name = self._require_logical_attribute(Capability.MODE)
        attribute = self.schema.by_name(attribute_name)
        if attribute.data_type is not DataType.ENUM and not attribute.enum_values:
            raise UnsupportedCapabilityError(
                f"{self.schema.name} mode numbers have not been mapped safely"
            )
        await self._apply_changes({attribute_name: mode})

    async def set_frequency(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError("frequency must be an integer between 0 and 100")
        attribute = self._require_logical_attribute(Capability.FREQUENCY)
        await self._apply_changes({attribute: value})

    async def set_linkage(self, role: LinkageRole) -> None:
        if not isinstance(role, LinkageRole):
            raise TypeError("linkage role must be a LinkageRole")
        attribute_name = self._require_logical_attribute(Capability.LINKAGE)
        if role not in self.capabilities.linkage_roles:
            choices = ", ".join(sorted(value.value for value in self.capabilities.linkage_roles))
            raise UnsupportedCapabilityError(
                f"{self.schema.name} linkage {role.value!r} is unsupported; expected {choices}"
            )
        await self._apply_changes({attribute_name: role})

    async def set_timer_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("timer enabled must be a boolean")
        attribute = self._require_logical_attribute(Capability.TIMER)
        await self._apply_changes({attribute: enabled})

    def preview_target(self, target: DeviceTarget) -> ControlPlan:
        changes = self._target_changes(target)
        return ControlPlan(
            product_key=self.schema.product_key,
            changes=changes,
            payload=build_control_payload(self.schema, changes),
        )

    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        await self._apply_changes(self._target_changes(target), guard=guard)

    def _target_changes(self, target: DeviceTarget) -> dict[str, Any]:
        enabled_attribute = self._require_logical_attribute(Capability.ENABLED)
        changes: dict[str, Any] = {enabled_attribute: target.enabled}
        if target.timer_enabled is not None:
            timer_attribute = self._require_logical_attribute(Capability.TIMER)
            changes[timer_attribute] = target.timer_enabled
        if target.linkage is not None:
            linkage_attribute = self._require_logical_attribute(Capability.LINKAGE)
            if target.linkage not in self.capabilities.linkage_roles:
                choices = ", ".join(
                    sorted(value.value for value in self.capabilities.linkage_roles)
                )
                raise UnsupportedCapabilityError(
                    f"{self.schema.name} linkage {target.linkage.value!r} is unsupported; "
                    f"expected {choices}"
                )
            changes[linkage_attribute] = target.linkage
        if not target.enabled:
            return changes

        self._validate_power(target.power)
        power_attribute = self._require_logical_attribute(Capability.POWER)
        changes[power_attribute] = target.power
        if target.mode is not None:
            mode_attribute = self._require_logical_attribute(Capability.MODE)
            mode_datapoint = self.schema.by_name(mode_attribute)
            if mode_datapoint.data_type is not DataType.ENUM and not mode_datapoint.enum_values:
                raise UnsupportedCapabilityError(
                    f"{self.schema.name} mode numbers have not been mapped safely"
                )
            changes[mode_attribute] = target.mode
        if target.frequency is not None:
            if not 0 <= target.frequency <= 100:
                raise ValueError("frequency must be between 0 and 100")
            frequency_attribute = self._require_logical_attribute(Capability.FREQUENCY)
            changes[frequency_attribute] = target.frequency
        return changes

    async def _apply_changes(
        self,
        changes: dict[str, Any],
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        if not self._allow_hardware_writes:
            raise HardwareWritesDisabledError(
                f"hardware writes are locked for {self._device_id}; review preview_target first"
            )
        payload = build_control_payload(self.schema, changes)

        async with self._io_lock:
            self._require_write_guard(guard)
            if all(self._last_sent_values.get(name) == value for name, value in changes.items()):
                # The app, native schedules or master broadcasts may have changed the device
                # after our last verified write. Never let the duplicate cache hide that drift,
                # especially while a linkage transaction is being rolled back.
                values = await self._read_values()
                if all(values.get(name) == expected for name, expected in changes.items()):
                    self._require_write_guard(guard)
                    return
            await self._respect_command_interval()
            # The guard is intentionally checked under the same device I/O lock and immediately
            # before send. An emergency-stop writer that trips the guard while waiting cannot be
            # followed by this stale ON target.
            self._require_write_guard(guard)
            await self._session.send_raw_control(payload)
            self._last_command_at = asyncio.get_running_loop().time()
            self._require_write_guard(guard)

            for attempt in range(self._readback_attempts):
                if self._readback_delay:
                    await asyncio.sleep(self._readback_delay)
                self._require_write_guard(guard)
                values = await self._read_values()
                if all(values.get(name) == expected for name, expected in changes.items()):
                    self._last_sent_values.update(changes)
                    return
                if attempt + 1 == self._readback_attempts:
                    mismatches = {
                        name: {"expected": expected, "actual": values.get(name)}
                        for name, expected in changes.items()
                        if values.get(name) != expected
                    }
                    raise StateVerificationError(
                        f"device {self._device_id!r} did not apply control: {mismatches}"
                    )

    @staticmethod
    def _require_write_guard(guard: WriteGuard | None) -> None:
        if guard is not None and guard() is not True:
            raise SafetyInterlockError("device write was blocked by the safety interlock")

    async def _read_values(self) -> dict[str, Any]:
        raw = await self._session.read_raw_state()
        return self.schema.decode_status(raw)

    async def _respect_command_interval(self) -> None:
        if self._last_command_at is None:
            return
        elapsed = asyncio.get_running_loop().time() - self._last_command_at
        remaining = self._minimum_command_interval - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _to_device_state(
        self,
        values: dict[str, Any],
        *,
        schedule: DeviceSchedule | None = None,
    ) -> DeviceState:
        enabled = bool(values.get(self.schema.enabled_attribute, False))
        power_value = values.get(self.schema.power_attribute, 0)
        mode_value = values.get(self.schema.mode_attribute, "unknown")
        frequency_value = values.get(self.schema.frequency_attribute)
        linkage_value = values.get(self.schema.linkage_attribute)
        timer_value = values.get(self.schema.timer_attribute)
        linkage = (
            _LINKAGE_ROLES_BY_VALUE[linkage_value]
            if isinstance(linkage_value, str) and linkage_value in _LINKAGE_ROLES_BY_VALUE
            else None
        )
        problems = self.schema.active_problems(values)
        observed_attributes = {
            name: value
            for name, value in values.items()
            if name.startswith(("Timer", "Auto", "Feed", "Interval"))
            or name
            in {
                "PulseTide",
                "Linkage",
                "Cust_Wav_Freq",
                "channe1",
                "channe2",
                "channe3",
                "channe4",
            }
        }
        return DeviceState(
            online=True,
            enabled=enabled,
            power=round(float(power_value)),
            mode=mode_value if isinstance(mode_value, str) else f"raw_{mode_value}",
            frequency=None if frequency_value is None else round(float(frequency_value)),
            linkage=linkage,
            timer_enabled=bool(timer_value) if timer_value is not None else None,
            error=", ".join(problems) if problems else None,
            schedule=schedule,
            observed_attributes=observed_attributes,
            observed_at=datetime.now(UTC),
        )

    def _require_logical_attribute(self, capability: Capability) -> str:
        names = {
            Capability.ENABLED: self.schema.enabled_attribute,
            Capability.POWER: self.schema.power_attribute,
            Capability.MODE: self.schema.mode_attribute,
            Capability.FREQUENCY: self.schema.frequency_attribute,
            Capability.LINKAGE: self.schema.linkage_attribute,
            Capability.TIMER: self.schema.timer_attribute,
        }
        attribute = names.get(capability)
        if attribute is None:
            raise UnsupportedCapabilityError(
                f"{self.schema.name} does not expose {capability.value}"
            )
        return attribute

    def _validate_power(self, power: int) -> None:
        if isinstance(power, bool) or not isinstance(power, int):
            raise TypeError("power must be an integer")
        if not self._power_limits.min_power <= power <= self._power_limits.max_power:
            raise ValueError(
                f"power {power} is outside configured range "
                f"{self._power_limits.min_power}..{self._power_limits.max_power}"
            )
        if power % self._power_step:
            raise ValueError(f"power {power} does not match step {self._power_step}")
