"""Safety-gated physical Jebao device adapter."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from jebao_flow.devices.base import (
    AckResolutionHook,
    AckUnconfirmedHook,
    ControlAckFailureKind,
    ControlAckPowerMismatchError,
    ControlAckReadbackError,
    ControlAckResolutionStage,
    ControlAckResolutionState,
    ControlAckResolutionUpdate,
    ControlAckStateMismatchError,
    ControlReadbackError,
    ControlStateMismatchError,
    ControlVerificationOutcome,
    DeviceConnectionError,
    HardwareWritesDisabledError,
    JebaoDevice,
    PowerStateVerificationError,
    SafetyInterlockError,
    UnsupportedCapabilityError,
    WriteGuard,
)
from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.errors import (
    ProtocolConnectionError,
    ProtocolError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)
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
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LocalWavemakerProScheduleSnapshot,
    build_local_wavemaker_pro_schedule_control_payload,
    decode_local_wavemaker_pro_slot_wire,
    get_local_wavemaker_pro_slot_wire,
    local_wavemaker_pro_schedule_datapoint_id,
    validate_local_wavemaker_pro_schedule_image,
    validate_local_wavemaker_pro_slot_wire,
)
from jebao_flow.protocol.schema import DataType
from jebao_flow.protocol.session import GizwitsSession
from jebao_flow.safety.limits import PowerLimits


class RawSession(Protocol):
    @property
    def connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def quarantine(self) -> None: ...

    async def authenticate(self) -> bytes: ...

    async def heartbeat(self) -> None: ...

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes: ...

    async def send_raw_control(self, control_payload: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ControlPlan:
    product_key: str
    changes: Mapping[str, Any]
    payload: bytes


SessionFactory = Callable[[str], RawSession]
StateDecoder = Callable[[bytes], dict[str, Any]]
_LINKAGE_ROLES_BY_VALUE = {role.value: role for role in LinkageRole}
_ACK_LOSS_RESOLUTION_TIMEOUT_SECONDS = 55.0
_ACK_LOSS_RESOLUTION_ATTEMPTS = 8
_ACK_LOSS_RETRY_DELAY_SECONDS = 0.5
_ACK_LOSS_STAGE_TIMEOUT_SECONDS = {
    ControlAckResolutionStage.QUARANTINE: 6.0,
    ControlAckResolutionStage.CONNECT: 5.0,
    ControlAckResolutionStage.AUTHENTICATE: 10.0,
    ControlAckResolutionStage.QUERY: 5.0,
}


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
        ack_loss_resolution_timeout_seconds: float = _ACK_LOSS_RESOLUTION_TIMEOUT_SECONDS,
        ack_loss_resolution_attempts: int = _ACK_LOSS_RESOLUTION_ATTEMPTS,
        ack_loss_retry_delay_seconds: float = _ACK_LOSS_RETRY_DELAY_SECONDS,
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
        if (
            not math.isfinite(ack_loss_resolution_timeout_seconds)
            or not 0
            < ack_loss_resolution_timeout_seconds
            <= _ACK_LOSS_RESOLUTION_TIMEOUT_SECONDS
        ):
            raise ValueError("ACK-loss resolution timeout must be finite and at most 55 seconds")
        if not 1 <= ack_loss_resolution_attempts <= _ACK_LOSS_RESOLUTION_ATTEMPTS:
            raise ValueError(
                f"ACK-loss resolution attempts must be between 1 and "
                f"{_ACK_LOSS_RESOLUTION_ATTEMPTS}"
            )
        if not math.isfinite(ack_loss_retry_delay_seconds) or ack_loss_retry_delay_seconds < 0:
            raise ValueError("ACK-loss retry delay must be finite and non-negative")

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
        self._ack_loss_resolution_timeout = ack_loss_resolution_timeout_seconds
        self._ack_loss_resolution_attempts = ack_loss_resolution_attempts
        self._ack_loss_retry_delay = ack_loss_retry_delay_seconds
        self._allow_hardware_writes = allow_hardware_writes
        self._session_factory = session_factory
        self._session = self._new_session(exclude=())
        self._session_retired = False
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
            if self._session_retired:
                if self._session.connected:
                    raise DeviceConnectionError(
                        f"retired session for {self._device_id!r} is still connected"
                    )
                try:
                    self._session = self._new_session(exclude=(self._session,))
                except RuntimeError as error:
                    raise DeviceConnectionError(
                        f"could not replace retired session for {self._device_id!r}"
                    ) from error
            try:
                await self._session.connect()
                await self._session.authenticate()
            except asyncio.CancelledError:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            except Exception:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            self._session_retired = False
            self._last_sent_values.clear()

    async def disconnect(self) -> None:
        async with self._io_lock:
            try:
                await self._session.disconnect()
            finally:
                # A caller asking for disconnect is asking for a transport boundary. Retire the
                # object as well, so rollback and reconnect never inherit its sequence, buffered
                # frames or pending writer-close bookkeeping.
                self._session_retired = True
                self._last_sent_values.clear()

    async def get_state(self) -> DeviceState:
        return await self._get_state(accept_reports=None)

    async def get_explicit_state(self) -> DeviceState:
        return await self._get_state(accept_reports=False)

    async def heartbeat(self) -> None:
        """Exchange one GAgent heartbeat without emitting a device-control frame."""

        async with self._io_lock:
            if self._session_retired:
                raise DeviceConnectionError(
                    f"retired session for {self._device_id!r} must be replaced before heartbeat"
                )
            try:
                await self._session.heartbeat()
            except asyncio.CancelledError:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            except Exception:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise

    async def _get_state(self, *, accept_reports: bool | None) -> DeviceState:
        async with self._io_lock:
            if self._session_retired:
                raise DeviceConnectionError(
                    f"retired session for {self._device_id!r} must be replaced before reading"
                )
            try:
                raw = (
                    await self._session.read_raw_state()
                    if accept_reports is None
                    else await self._session.read_raw_state(accept_reports=accept_reports)
                )
                values = self.schema.decode_status(raw)
                schedule = decode_schedule(
                    self.schema.product_key,
                    raw,
                    enabled=bool(values.get(self.schema.timer_attribute, False)),
                )
                state = self._to_device_state(values, schedule=schedule)
            except asyncio.CancelledError:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            except Exception:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            return state

    async def set_enabled(self, enabled: bool) -> None:
        attribute = self._require_logical_attribute(Capability.ENABLED)
        await self._apply_changes({attribute: enabled})

    async def set_power(self, power: int) -> None:
        await self.write_power(power)

    async def write_power(
        self,
        power: int,
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
    ) -> ControlVerificationOutcome:
        self._validate_power(power)
        attribute = self._require_logical_attribute(Capability.POWER)
        return await self._apply_changes(
            {attribute: power},
            guard=guard,
            on_ack_unconfirmed=on_ack_unconfirmed,
            on_ack_resolution=on_ack_resolution,
        )

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
        await self.write_linkage(role)

    async def write_linkage(
        self,
        role: LinkageRole,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        if not isinstance(role, LinkageRole):
            raise TypeError("linkage role must be a LinkageRole")
        attribute_name = self._require_logical_attribute(Capability.LINKAGE)
        if role not in self.capabilities.linkage_roles:
            choices = ", ".join(sorted(value.value for value in self.capabilities.linkage_roles))
            raise UnsupportedCapabilityError(
                f"{self.schema.name} linkage {role.value!r} is unsupported; expected {choices}"
            )
        await self._apply_changes({attribute_name: role}, guard=guard)

    async def read_schedule_image(self) -> bytes:
        return await self._read_schedule_image(accept_reports=None)

    async def read_schedule_image_explicit(self) -> bytes:
        return await self._read_schedule_image(accept_reports=False)

    async def _read_schedule_image(self, *, accept_reports: bool | None) -> bytes:
        self._require_local_wavemaker_pro_schedule()
        async with self._io_lock:
            if self._session_retired:
                raise DeviceConnectionError(
                    f"retired session for {self._device_id!r} must be replaced before reading"
                )
            try:
                raw = (
                    await self._session.read_raw_state()
                    if accept_reports is None
                    else await self._session.read_raw_state(accept_reports=accept_reports)
                )
                snapshot = LocalWavemakerProScheduleSnapshot.from_status(raw)
            except asyncio.CancelledError:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            except Exception:
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            return snapshot.image

    async def write_schedule_slots(
        self,
        slots: Mapping[int, bytes],
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
    ) -> ControlVerificationOutcome:
        """Write one or more Pro AutoTime slots in one guarded control frame."""

        self._require_local_wavemaker_pro_schedule()
        if not isinstance(slots, Mapping):
            raise TypeError("schedule slots must be a mapping")
        changes: dict[str, bytes] = {}
        normalized: dict[int, bytes] = {}
        names_by_index: dict[int, str] = {}
        for slot_index, slot_wire in slots.items():
            datapoint_id = local_wavemaker_pro_schedule_datapoint_id(slot_index)
            # DP 13 is AutoTime00 and the range is contiguous through DP 60.
            attribute_name = f"AutoTime{datapoint_id - 13:02d}"
            wire = validate_local_wavemaker_pro_slot_wire(
                slot_wire,
                slot_index=slot_index,
            )
            entry = decode_local_wavemaker_pro_slot_wire(wire, slot_index=slot_index)
            if entry is not None:
                flow = entry.parameters["flow"]
                # A feed slot may deliberately stop the pump, so zero is the sole exception to
                # the configured operating minimum.  Every non-zero value still passes the same
                # min/max/step gate as another scheduled mode; otherwise a nominal feed slot
                # carrying (for example) 100 could bypass a device's 75% safety ceiling.
                if entry.mode != "feed" or flow != 0:
                    self._validate_power(flow)
            normalized[slot_index] = wire
            names_by_index[slot_index] = attribute_name
            changes[attribute_name] = wire

        payload = build_local_wavemaker_pro_schedule_control_payload(normalized)

        def decode_selected_schedule_slots(raw_status: bytes) -> dict[str, Any]:
            image = LocalWavemakerProScheduleSnapshot.from_status(raw_status).image
            return {
                names_by_index[index]: get_local_wavemaker_pro_slot_wire(image, index)
                for index in normalized
            }

        return await self._apply_changes(
            changes,
            guard=guard,
            on_ack_unconfirmed=on_ack_unconfirmed,
            on_ack_resolution=on_ack_resolution,
            payload=payload,
            decoder=decode_selected_schedule_slots,
        )

    async def restore_schedule_image(
        self,
        image: bytes,
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
    ) -> ControlVerificationOutcome:
        """Restore all 48 Pro AutoTime slots from one exact recovery snapshot."""

        self._require_local_wavemaker_pro_schedule()
        exact = validate_local_wavemaker_pro_schedule_image(image)
        slots = {
            slot_index: get_local_wavemaker_pro_slot_wire(exact, slot_index)
            for slot_index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
        }
        payload = build_local_wavemaker_pro_schedule_control_payload(slots)

        def decode_schedule_image(raw_status: bytes) -> dict[str, Any]:
            return {
                "ScheduleImage": LocalWavemakerProScheduleSnapshot.from_status(raw_status).image
            }

        # Recovery can begin after an uncertain forward exchange retired its transport. Establish
        # a clean authenticated boundary before entering the guarded write path; no control frame
        # is emitted by ``connect`` and the guard is checked on both sides of that await.
        self._require_hardware_writes_enabled()
        self._require_write_guard(guard)
        if not self.connected:
            await self.connect()
        self._require_write_guard(guard)
        return await self._apply_changes(
            {"ScheduleImage": exact},
            guard=guard,
            on_ack_unconfirmed=on_ack_unconfirmed,
            on_ack_resolution=on_ack_resolution,
            payload=payload,
            decoder=decode_schedule_image,
        )

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

    def preview_linkage(self, role: LinkageRole) -> ControlPlan:
        if not isinstance(role, LinkageRole):
            raise TypeError("linkage role must be a LinkageRole")
        attribute_name = self._require_logical_attribute(Capability.LINKAGE)
        if role not in self.capabilities.linkage_roles:
            choices = ", ".join(sorted(value.value for value in self.capabilities.linkage_roles))
            raise UnsupportedCapabilityError(
                f"{self.schema.name} linkage {role.value!r} is unsupported; expected {choices}"
            )
        changes = {attribute_name: role}
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
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
        payload: bytes | None = None,
        decoder: StateDecoder | None = None,
    ) -> ControlVerificationOutcome:
        self._require_hardware_writes_enabled()
        payload = build_control_payload(self.schema, changes) if payload is None else bytes(payload)
        if not payload or payload[0] != 0x01:
            raise ValueError("control payload must begin with action 0x01")

        async with self._io_lock:
            if self._session_retired:
                raise DeviceConnectionError(
                    f"retired session for {self._device_id!r} must be replaced before writing"
                )
            self._require_write_guard(guard)
            if all(self._last_sent_values.get(name) == value for name, value in changes.items()):
                # The app, native schedules or master broadcasts may have changed the device
                # after our last verified write. Never let the duplicate cache hide that drift,
                # especially while a linkage transaction is being rolled back.
                values = await self._read_values(decoder=decoder)
                if all(values.get(name) == expected for name, expected in changes.items()):
                    self._require_write_guard(guard)
                    return ControlVerificationOutcome.STATE_VERIFIED
            await self._respect_command_interval()
            # The guard is intentionally checked under the same device I/O lock and immediately
            # before send. An emergency-stop writer that trips the guard while waiting cannot be
            # followed by this stale ON target.
            self._require_write_guard(guard)
            # Record the physical command boundary before awaiting the ACK. A timeout or lost ACK
            # leaves the write outcome uncertain, so rollback must still respect command pacing.
            self._last_command_at = asyncio.get_running_loop().time()
            try:
                await self._session.send_raw_control(payload)
            except asyncio.CancelledError:
                # Cancellation after the physical send boundary leaves both the write outcome and
                # stream framing uncertain. Recovery must replace this session object before its
                # compensating exact-image write.
                self._session_retired = True
                self._quarantine_session_now(self._session)
                raise
            except (ProtocolError, OSError) as acknowledgement_error:
                return await self._resolve_unacknowledged_control(
                    changes,
                    acknowledgement_error=acknowledgement_error,
                    guard=guard,
                    on_ack_unconfirmed=on_ack_unconfirmed,
                    on_ack_resolution=on_ack_resolution,
                    decoder=decoder,
                )
            self._require_write_guard(guard)

            for attempt in range(self._readback_attempts):
                if self._readback_delay:
                    await asyncio.sleep(self._readback_delay)
                self._require_write_guard(guard)
                try:
                    if not self._session.connected:
                        try:
                            await self._session.connect()
                            self._require_write_guard(guard)
                            await self._session.authenticate()
                        except asyncio.CancelledError:
                            self._session_retired = True
                            self._quarantine_session_now(self._session)
                            raise
                        self._last_sent_values.clear()
                    # Connecting and authenticating may take several seconds. Re-check the
                    # attended operation deadline/interlock before issuing even a read-only
                    # query, and again before accepting its result as forward progress.
                    self._require_write_guard(guard)
                    values = await self._read_values(decoder=decoder)
                    self._require_write_guard(guard)
                except (ProtocolError, OSError, ValueError) as error:
                    if attempt + 1 == self._readback_attempts:
                        raise ControlReadbackError(
                            f"device {self._device_id!r} could not verify control after "
                            f"{self._readback_attempts} readback attempts"
                        ) from error
                    # A failed framed exchange quarantines its stream. The following bounded
                    # attempt may re-authenticate for readback only; control is never retransmitted.
                    continue
                if all(values.get(name) == expected for name, expected in changes.items()):
                    self._last_sent_values.update(changes)
                    return ControlVerificationOutcome.STATE_VERIFIED
                if attempt + 1 == self._readback_attempts:
                    mismatches = self._control_mismatches(changes, values)
                    power_only = (
                        self.schema.power_attribute is not None
                        and set(mismatches) == {self.schema.power_attribute}
                    )
                    error_type = (
                        PowerStateVerificationError
                        if power_only
                        else ControlStateMismatchError
                    )
                    raise error_type(
                        f"device {self._device_id!r} did not apply control: {mismatches}"
                    )

    async def _resolve_unacknowledged_control(
        self,
        changes: Mapping[str, Any],
        *,
        acknowledgement_error: BaseException,
        guard: WriteGuard | None,
        on_ack_unconfirmed: AckUnconfirmedHook | None,
        on_ack_resolution: AckResolutionHook | None,
        decoder: StateDecoder | None = None,
    ) -> ControlVerificationOutcome:
        """Resolve one uncertain control frame using fresh, read-only sessions only.

        A missing or malformed 0x94 response does not prove whether the MCU applied the frame.
        Retrying that frame could double-apply a non-idempotent control, so this path creates a
        new authenticated session object for every read-only state query. A 0x03 reply or 0x04
        report received on that fresh session is actual-state evidence, never an acknowledgement;
        the adapter and its command timestamp remain intact for correctly paced rollback.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._ack_loss_resolution_timeout
        retired_sessions: list[RawSession] = [self._session]
        self._session_retired = True
        self._last_sent_values.clear()

        # Persist the ACK-loss fact before transport cleanup. A crash or cancellation during a
        # slow FIN must not erase the fact that the control frame already crossed the send
        # boundary with an uncertain result.
        if on_ack_unconfirmed is not None:
            try:
                on_ack_unconfirmed(self._classify_ack_failure(acknowledgement_error))
            except BaseException:
                self._quarantine_session_now(self._session)
                self._install_clean_session_best_effort(retired_sessions)
                raise
        try:
            quarantine_failure = await self._quarantine_ack_session(
                self._session,
                deadline=deadline,
                attempts=0,
                on_ack_resolution=on_ack_resolution,
                emit_progress=True,
            )
        except BaseException:
            self._quarantine_session_now(self._session)
            self._install_clean_session_best_effort(retired_sessions)
            raise

        if quarantine_failure is not None:
            self._install_clean_session_best_effort(retired_sessions)
            raise quarantine_failure from acknowledgement_error
        try:
            self._require_write_guard(guard)
        except SafetyInterlockError:
            self._install_clean_session_best_effort(retired_sessions)
            raise

        last_failure: ControlAckReadbackError | None = None
        last_mismatches: dict[str, dict[str, Any]] | None = None
        attempts_completed = 0
        try:
            for attempt in range(1, self._ack_loss_resolution_attempts + 1):
                if loop.time() >= deadline:
                    break
                self._require_write_guard(guard)
                if attempt > 1:
                    if not await self._ack_resolution_backoff(
                        deadline,
                        attempt=attempt,
                        guard=guard,
                    ):
                        break
                attempts_completed = attempt

                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=ControlAckResolutionStage.CONNECT,
                    attempt=attempt,
                    state=ControlAckResolutionState.STARTED,
                )
                try:
                    session = self._new_session(exclude=retired_sessions)
                except (TypeError, ValueError, RuntimeError) as error:
                    self._emit_ack_resolution_update(
                        on_ack_resolution,
                        stage=ControlAckResolutionStage.CONNECT,
                        attempt=attempt,
                        state=ControlAckResolutionState.FAILED,
                    )
                    last_failure = ControlAckReadbackError(
                        f"device {self._device_id!r} could not create a fresh ACK-loss "
                        f"resolution session on attempt {attempt}",
                        stage=ControlAckResolutionStage.CONNECT,
                        attempts=attempt,
                    )
                    last_failure.__cause__ = error
                    continue
                retired_sessions.append(session)
                self._session = session

                try:
                    await self._run_ack_resolution_stage(
                        session.connect,
                        stage=ControlAckResolutionStage.CONNECT,
                        deadline=deadline,
                        attempts=attempt,
                        on_ack_resolution=on_ack_resolution,
                        emit_started=False,
                    )
                    self._require_write_guard(guard)
                    await self._run_ack_resolution_stage(
                        session.authenticate,
                        stage=ControlAckResolutionStage.AUTHENTICATE,
                        deadline=deadline,
                        attempts=attempt,
                        on_ack_resolution=on_ack_resolution,
                    )
                    self._last_sent_values.clear()
                    self._require_write_guard(guard)
                    raw = await self._run_ack_resolution_stage(
                        lambda session=session: session.read_raw_state(accept_reports=True),
                        stage=ControlAckResolutionStage.QUERY,
                        deadline=deadline,
                        attempts=attempt,
                        on_ack_resolution=on_ack_resolution,
                    )
                    self._require_write_guard(guard)
                    self._emit_ack_resolution_update(
                        on_ack_resolution,
                        stage=ControlAckResolutionStage.DECODE,
                        attempt=attempt,
                        state=ControlAckResolutionState.STARTED,
                    )
                    try:
                        values = (decoder or self.schema.decode_status)(raw)
                    except Exception as error:
                        self._emit_ack_resolution_update(
                            on_ack_resolution,
                            stage=ControlAckResolutionStage.DECODE,
                            attempt=attempt,
                            state=ControlAckResolutionState.FAILED,
                        )
                        raise ControlAckReadbackError(
                            f"device {self._device_id!r} could not decode the fresh ACK-loss "
                            f"state on attempt {attempt}",
                            stage=ControlAckResolutionStage.DECODE,
                            attempts=attempt,
                        ) from error
                    self._require_ack_resolution_time(
                        deadline,
                        stage=ControlAckResolutionStage.DECODE,
                        attempts=attempt,
                    )
                    self._require_write_guard(guard)
                except ControlAckReadbackError as error:
                    last_failure = error
                    last_mismatches = None
                    self._session_retired = True
                    cleanup_failure = await self._quarantine_ack_session(
                        session,
                        deadline=deadline,
                        attempts=attempt,
                        on_ack_resolution=on_ack_resolution,
                        emit_progress=False,
                    )
                    if cleanup_failure is not None:
                        last_failure = cleanup_failure
                        break
                    continue

                mismatches = self._control_mismatches(changes, values)
                if not mismatches:
                    self._emit_ack_resolution_update(
                        on_ack_resolution,
                        stage=ControlAckResolutionStage.DECODE,
                        attempt=attempt,
                        state=ControlAckResolutionState.SUCCEEDED,
                    )
                    try:
                        await self._replace_verified_ack_resolution_session(
                            session,
                            retired_sessions=retired_sessions,
                            deadline=deadline,
                            attempt=attempt,
                            guard=guard,
                            on_ack_resolution=on_ack_resolution,
                        )
                    except ControlAckReadbackError as error:
                        last_failure = error
                        last_mismatches = None
                        break
                    self._last_sent_values.update(changes)
                    return ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK

                last_failure = None
                last_mismatches = mismatches
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=ControlAckResolutionStage.DECODE,
                    attempt=attempt,
                    state=ControlAckResolutionState.FAILED,
                )
                self._session_retired = True
                cleanup_failure = await self._quarantine_ack_session(
                    session,
                    deadline=deadline,
                    attempts=attempt,
                    on_ack_resolution=on_ack_resolution,
                    emit_progress=False,
                )
                if cleanup_failure is not None:
                    last_failure = cleanup_failure
                    last_mismatches = None
                    break
        except ControlAckReadbackError as error:
            last_failure = error
            last_mismatches = None
        except (asyncio.CancelledError, SafetyInterlockError):
            self._session_retired = True
            self._quarantine_session_now(self._session)
            self._install_clean_session_best_effort(retired_sessions)
            raise
        except BaseException:
            self._session_retired = True
            self._quarantine_session_now(self._session)
            self._install_clean_session_best_effort(retired_sessions)
            raise

        self._install_clean_session_best_effort(retired_sessions)
        if last_mismatches is not None:
            power_only = (
                self.schema.power_attribute is not None
                and set(last_mismatches) == {self.schema.power_attribute}
            )
            error_type = (
                ControlAckPowerMismatchError
                if power_only
                else ControlAckStateMismatchError
            )
            raise error_type(
                f"device {self._device_id!r} did not apply control with an "
                f"unacknowledged response: "
                f"{last_mismatches}"
            ) from acknowledgement_error
        if last_failure is None:
            last_failure = ControlAckReadbackError(
                f"device {self._device_id!r} exhausted its bounded ACK-loss resolution",
                stage=ControlAckResolutionStage.QUERY,
                attempts=attempts_completed,
            )
        raise last_failure from acknowledgement_error

    async def _replace_verified_ack_resolution_session(
        self,
        verified_session: RawSession,
        *,
        retired_sessions: list[RawSession],
        deadline: float,
        attempt: int,
        guard: WriteGuard | None,
        on_ack_resolution: AckResolutionHook | None,
    ) -> None:
        """Retire state-evidence transport and install one empty authenticated stream.

        A GAgent may emit a 0x04 report immediately before the 0x03 response to our explicit
        query. Once the report proves state, that paired response can remain unread. Reusing the
        stream would let a later request consume stale state, so every successful ACK-loss evidence
        session is closed and replaced without issuing another control or state query.
        """

        self._session_retired = True
        cleanup_failure = await self._quarantine_ack_session(
            verified_session,
            deadline=deadline,
            attempts=attempt,
            on_ack_resolution=on_ack_resolution,
            emit_progress=True,
        )
        if cleanup_failure is not None:
            raise cleanup_failure
        self._require_write_guard(guard)

        self._emit_ack_resolution_update(
            on_ack_resolution,
            stage=ControlAckResolutionStage.CONNECT,
            attempt=attempt,
            state=ControlAckResolutionState.STARTED,
        )
        try:
            replacement = self._new_session(exclude=retired_sessions)
        except (TypeError, ValueError, RuntimeError) as error:
            self._emit_ack_resolution_update(
                on_ack_resolution,
                stage=ControlAckResolutionStage.CONNECT,
                attempt=attempt,
                state=ControlAckResolutionState.FAILED,
            )
            raise ControlAckReadbackError(
                f"device {self._device_id!r} could not create a clean post-verification "
                "session",
                stage=ControlAckResolutionStage.CONNECT,
                attempts=attempt,
            ) from error

        retired_sessions.append(replacement)
        self._session = replacement
        try:
            await self._run_ack_resolution_stage(
                replacement.connect,
                stage=ControlAckResolutionStage.CONNECT,
                deadline=deadline,
                attempts=attempt,
                on_ack_resolution=on_ack_resolution,
                emit_started=False,
            )
            self._require_write_guard(guard)
            await self._run_ack_resolution_stage(
                replacement.authenticate,
                stage=ControlAckResolutionStage.AUTHENTICATE,
                deadline=deadline,
                attempts=attempt,
                on_ack_resolution=on_ack_resolution,
            )
            self._require_ack_resolution_time(
                deadline,
                stage=ControlAckResolutionStage.AUTHENTICATE,
                attempts=attempt,
            )
            self._require_write_guard(guard)
        except BaseException:
            self._session_retired = True
            self._quarantine_session_now(replacement)
            raise

        self._last_sent_values.clear()
        self._session_retired = False

    def _new_session(self, *, exclude: Collection[RawSession]) -> RawSession:
        try:
            session = self._session_factory(self.address)
        except Exception as error:
            raise RuntimeError("session factory could not create a session") from error
        if any(session is retired for retired in exclude):
            raise RuntimeError("session factory returned a retired session object")
        if session.connected:
            self._quarantine_session_now(session)
            raise RuntimeError("session factory returned an already-connected session")
        return session

    def _install_clean_session_best_effort(
        self,
        retired_sessions: Collection[RawSession],
    ) -> None:
        """Leave rollback a never-connected object without masking the original failure."""

        # Never drop the sole reference to a live transport. Doing so could let rollback open a
        # second session while an unquarantined control stream still exists.
        if self._session.connected:
            return
        try:
            session = self._new_session(exclude=retired_sessions)
        except RuntimeError:
            return
        self._session = session
        self._session_retired = False

    @staticmethod
    def _classify_ack_failure(error: BaseException) -> ControlAckFailureKind:
        """Reduce a transport exception to an allow-listed, persistence-safe category."""

        # ProtocolTimeoutError inherits ProtocolConnectionError, so the most specific classes
        # must be checked first. No exception text is allowed across the diagnostic hook.
        if isinstance(error, ProtocolTimeoutError):
            return ControlAckFailureKind.TIMEOUT
        if isinstance(error, UnexpectedResponseError):
            return ControlAckFailureKind.UNEXPECTED_RESPONSE
        if isinstance(error, ProtocolConnectionError):
            return ControlAckFailureKind.CONNECTION
        if isinstance(error, ProtocolError):
            return ControlAckFailureKind.PROTOCOL
        if isinstance(error, OSError):
            return ControlAckFailureKind.OS_ERROR
        raise TypeError("unsupported ACK failure type")

    @staticmethod
    def _emit_ack_resolution_update(
        hook: AckResolutionHook | None,
        *,
        stage: ControlAckResolutionStage,
        attempt: int,
        state: ControlAckResolutionState,
    ) -> None:
        """Synchronously persist one redacted resolution transition when requested."""

        if hook is None:
            return
        hook(
            ControlAckResolutionUpdate(
                stage=stage,
                attempt=attempt,
                state=state,
            )
        )

    @staticmethod
    def _quarantine_session_now(session: RawSession) -> None:
        """Logically drop a transport synchronously so safety rollback is never delayed."""

        quarantine = getattr(session, "quarantine", None)
        if callable(quarantine):
            try:
                quarantine()
            except Exception:
                pass

    async def _quarantine_ack_session(
        self,
        session: RawSession,
        *,
        deadline: float,
        attempts: int,
        on_ack_resolution: AckResolutionHook | None,
        emit_progress: bool,
    ) -> ControlAckReadbackError | None:
        if asyncio.get_running_loop().time() >= deadline:
            if emit_progress:
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=ControlAckResolutionStage.QUARANTINE,
                    attempt=attempts,
                    state=ControlAckResolutionState.STARTED,
                )
            self._quarantine_session_now(session)
            if emit_progress or session.connected:
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=ControlAckResolutionStage.QUARANTINE,
                    attempt=attempts,
                    state=ControlAckResolutionState.FAILED,
                )
                return ControlAckReadbackError(
                    f"device {self._device_id!r} retained an unacknowledged control session",
                    stage=ControlAckResolutionStage.QUARANTINE,
                    attempts=attempts,
                )
            return None
        failure: ControlAckReadbackError | None = None
        try:
            await self._run_ack_resolution_stage(
                session.disconnect,
                stage=ControlAckResolutionStage.QUARANTINE,
                deadline=deadline,
                attempts=attempts,
                on_ack_resolution=on_ack_resolution,
                emit_started=emit_progress,
                emit_completed=emit_progress,
            )
        except ControlAckReadbackError as error:
            failure = error
            self._quarantine_session_now(session)
        if session.connected:
            if not emit_progress:
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=ControlAckResolutionStage.QUARANTINE,
                    attempt=attempts,
                    state=ControlAckResolutionState.FAILED,
                )
            return failure or ControlAckReadbackError(
                f"device {self._device_id!r} retained an unacknowledged control session",
                stage=ControlAckResolutionStage.QUARANTINE,
                attempts=attempts,
            )
        return None

    async def _run_ack_resolution_stage(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        stage: ControlAckResolutionStage,
        deadline: float,
        attempts: int,
        on_ack_resolution: AckResolutionHook | None,
        emit_started: bool = True,
        emit_completed: bool = True,
    ) -> Any:
        remaining = deadline - asyncio.get_running_loop().time()
        timeout = min(_ACK_LOSS_STAGE_TIMEOUT_SECONDS[stage], remaining)
        if timeout <= 0:
            if emit_completed:
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=stage,
                    attempt=attempts,
                    state=ControlAckResolutionState.FAILED,
                )
            raise ControlAckReadbackError(
                f"device {self._device_id!r} ACK-loss resolution deadline expired",
                stage=stage,
                attempts=attempts,
            )
        if emit_started:
            self._emit_ack_resolution_update(
                on_ack_resolution,
                stage=stage,
                attempt=attempts,
                state=ControlAckResolutionState.STARTED,
            )
        try:
            async with asyncio.timeout(timeout):
                result = await operation()
        except TimeoutError as error:
            if emit_completed:
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=stage,
                    attempt=attempts,
                    state=ControlAckResolutionState.FAILED,
                )
            raise ControlAckReadbackError(
                f"device {self._device_id!r} ACK-loss {stage.value} timed out",
                stage=stage,
                attempts=attempts,
            ) from error
        except Exception as error:
            if emit_completed:
                self._emit_ack_resolution_update(
                    on_ack_resolution,
                    stage=stage,
                    attempt=attempts,
                    state=ControlAckResolutionState.FAILED,
                )
            raise ControlAckReadbackError(
                f"device {self._device_id!r} ACK-loss {stage.value} failed",
                stage=stage,
                attempts=attempts,
            ) from error
        if emit_completed:
            self._emit_ack_resolution_update(
                on_ack_resolution,
                stage=stage,
                attempt=attempts,
                state=ControlAckResolutionState.SUCCEEDED,
            )
        return result

    async def _ack_resolution_backoff(
        self,
        deadline: float,
        *,
        attempt: int,
        guard: WriteGuard | None,
    ) -> bool:
        delay = min(self._ack_loss_retry_delay * (attempt - 1), 1.5)
        if delay:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= delay:
                return False
            await asyncio.sleep(delay)
        self._require_write_guard(guard)
        return True

    @staticmethod
    def _require_ack_resolution_time(
        deadline: float,
        *,
        stage: ControlAckResolutionStage,
        attempts: int,
    ) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise ControlAckReadbackError(
                "bounded ACK-loss resolution deadline expired",
                stage=stage,
                attempts=attempts,
            )

    @staticmethod
    def _require_write_guard(guard: WriteGuard | None) -> None:
        if guard is not None and guard() is not True:
            raise SafetyInterlockError("device write was blocked by the safety interlock")

    def _require_hardware_writes_enabled(self) -> None:
        if not self._allow_hardware_writes:
            raise HardwareWritesDisabledError(
                f"hardware writes are locked for {self._device_id}; review preview_target first"
            )

    async def _read_values(
        self,
        *,
        accept_reports: bool = True,
        decoder: StateDecoder | None = None,
    ) -> dict[str, Any]:
        try:
            raw = await self._session.read_raw_state(accept_reports=accept_reports)
        except asyncio.CancelledError:
            self._session_retired = True
            self._quarantine_session_now(self._session)
            raise
        return (decoder or self.schema.decode_status)(raw)

    @staticmethod
    def _control_mismatches(
        changes: Mapping[str, Any],
        values: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return useful scalar differences without logging raw binary schedules."""

        def evidence(value: Any) -> Any:
            return "<binary>" if isinstance(value, (bytes, bytearray, memoryview)) else value

        return {
            name: {
                "expected": evidence(expected),
                "actual": evidence(values.get(name)),
            }
            for name, expected in changes.items()
            if values.get(name) != expected
        }

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

    def _require_local_wavemaker_pro_schedule(self) -> None:
        if self.schema.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise UnsupportedCapabilityError(
                f"{self.schema.name} does not expose an audited writable schedule image"
            )

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
