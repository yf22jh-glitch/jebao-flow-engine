import asyncio
import json
import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.devices.simulator import SimulatedJebaoDevice
from jebao_flow.devices.verification import (
    AttendedRestoreAuthority,
    DeviceVerificationApplyError,
    DeviceVerificationBusyError,
    DeviceVerificationError,
    DeviceVerificationErrorCode,
    DeviceVerificationPhase,
    DeviceVerificationPreflightError,
    DeviceVerificationRecord,
    DeviceVerificationRecoveryDeferred,
    DeviceVerificationRecoveryReason,
    DeviceVerificationRollbackError,
    DeviceVerificationSnapshot,
    DeviceVerificationSpec,
    DeviceVerificationStopReason,
    FirstPhysicalWriteVerifier,
    JsonDeviceVerificationJournalStore,
)
from jebao_flow.protocol.models import (
    Capability,
    DeviceCapabilities,
    DeviceTarget,
    LinkageRole,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.safety.limits import PowerLimits


class _GlobalGuard:
    def __init__(self, *, permitted: bool = True) -> None:
        self._permitted = permitted
        self._epoch = 0
        self._blocked = asyncio.Event()
        self._leased = False
        if not permitted:
            self._blocked.set()

    @property
    def permitted(self) -> bool:
        return self._permitted

    @property
    def epoch(self) -> int:
        return self._epoch

    @contextmanager
    def lease(self):
        if self._leased:
            raise RuntimeError("global hardware operation is already leased")
        self._leased = True
        try:
            yield
        finally:
            self._leased = False

    async def wait_until_blocked(self) -> None:
        await self._blocked.wait()

    def trip(self) -> None:
        self._permitted = False
        self._epoch += 1
        self._blocked.set()

    def clear(self) -> None:
        self._permitted = True
        self._blocked.clear()


class _RecordingStore(JsonDeviceVerificationJournalStore):
    def __init__(self, path: Path, events: list[str] | None = None) -> None:
        super().__init__(path)
        self.events = events
        self.records: list[DeviceVerificationRecord] = []

    def create(self, record: DeviceVerificationRecord) -> None:
        self.records.append(record)
        if self.events is not None:
            self.events.append(f"journal:{record.phase.value}")
        super().create(record)

    def save(self, record: DeviceVerificationRecord) -> None:
        self.records.append(record)
        if self.events is not None:
            self.events.append(f"journal:{record.phase.value}")
        super().save(record)

    def clear(self) -> None:
        if self.events is not None:
            self.events.append("journal:clear")
        super().clear()


class _RecordingDevice(SimulatedJebaoDevice):
    def __init__(self, *args, events: list[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.events = events
        self.write_targets: list[DeviceTarget] = []

    async def get_state(self):
        if self.events is not None:
            self.events.append("read")
        return await super().get_state()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.write_targets.append(target)
        if self.events is not None:
            self.events.append(f"write:{target.power}")
        await super().write_target(target, guard=guard)


class _SlowReadDevice(_RecordingDevice):
    async def get_state(self):
        await asyncio.sleep(0.02)
        return await super().get_state()


class _BlockedFirstWriteDevice(_RecordingDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.write_entered = asyncio.Event()
        self.release_write = asyncio.Event()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.write_targets.append(target)
        self.write_entered.set()
        await self.release_write.wait()
        await SimulatedJebaoDevice.write_target(self, target, guard=guard)


class _TripAfterFirstWriteDevice(_RecordingDevice):
    def __init__(self, *args, guard: _GlobalGuard, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.guard = guard

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if len(self.write_targets) == 1:
            self.guard.trip()


class _StopAfterFirstWriteDevice(_RecordingDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.controller: FirstPhysicalWriteVerifier | None = None

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if len(self.write_targets) == 1:
            assert self.controller is not None
            assert await self.controller.stop() is True


class _CorruptWriteDevice(_RecordingDevice):
    def __init__(self, *args, corrupt_call: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.corrupt_call = corrupt_call

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if len(self.write_targets) == self.corrupt_call:
            self._state = self._state.model_copy(update={"power": target.power - 1})


class _HangAfterLowerWriteDevice(_RecordingDevice):
    async def get_state(self):
        if len(self.write_targets) >= 2:
            await asyncio.Event().wait()
        return await super().get_state()


class _HangRestoreWriteDevice(_RecordingDevice):
    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.write_targets.append(target)
        await asyncio.Event().wait()


class _HangDisconnectDevice(_RecordingDevice):
    hang_disconnect = False

    async def disconnect(self) -> None:
        if self.hang_disconnect:
            await asyncio.Event().wait()
        await super().disconnect()


class _FailRestoreDevice(_RecordingDevice):
    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        if target.power == 40:
            raise RuntimeError("restore failed at 192.0.2.10 with private-token")
        await super().write_target(target, guard=guard)


class _TripAfterRestoreWriteDevice(_RecordingDevice):
    def __init__(self, *args, guard: _GlobalGuard, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.guard = guard

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        self.guard.trip()


class _TripOnPhaseStore(_RecordingStore):
    def __init__(self, path: Path, guard: _GlobalGuard) -> None:
        super().__init__(path)
        self.guard = guard

    def save(self, record: DeviceVerificationRecord) -> None:
        super().save(record)
        if record.phase is DeviceVerificationPhase.SAME_VALUE_PENDING:
            self.guard.trip()


class _TripAfterClearStore(_RecordingStore):
    def __init__(self, path: Path, guard: _GlobalGuard) -> None:
        super().__init__(path)
        self.guard = guard

    def clear(self) -> None:
        super().clear()
        self.guard.trip()


def _capabilities(
    *,
    product_key: str = LOCAL_WAVEMAKER_PRO.product_key,
    model: str = LOCAL_WAVEMAKER_PRO.name,
    limits: PowerLimits | None = None,
    step: int = 1,
) -> DeviceCapabilities:
    return DeviceCapabilities(
        model=model,
        product_key=product_key,
        readable=frozenset(Capability),
        writable=frozenset(
            {
                Capability.ENABLED,
                Capability.POWER,
                Capability.MODE,
                Capability.FREQUENCY,
                Capability.LINKAGE,
                Capability.TIMER,
            }
        ),
        power_limits=limits or PowerLimits(min_power=30, max_power=45),
        power_step=step,
        native_modes=frozenset({"constant", "pulse", "sine"}),
        linkage_roles=frozenset(LinkageRole),
    )


async def _ready_device(
    *,
    device_class: type[_RecordingDevice] = _RecordingDevice,
    power: int = 40,
    timer_enabled: bool = False,
    mode: str = "constant",
    linkage: LinkageRole = LinkageRole.INDEPENDENT,
    enabled: bool = True,
    capabilities: DeviceCapabilities | None = None,
    events: list[str] | None = None,
    **kwargs,
) -> _RecordingDevice:
    device = device_class(
        "qualification-device",
        capabilities=capabilities or _capabilities(),
        events=events,
        **kwargs,
    )
    await device.connect()
    await device.set_enabled(enabled)
    await device.set_power(power)
    await device.set_mode(mode)
    await device.set_frequency(25)
    await device.set_linkage(linkage)
    await device.set_timer_enabled(timer_enabled)
    await device.disconnect()
    device.commands.clear()
    device.write_targets.clear()
    if events is not None:
        events.clear()
    return device


def _spec(
    *,
    operation_id: str = "first_write",
    target_power: int = 35,
    duration: float = 0.03,
) -> DeviceVerificationSpec:
    return DeviceVerificationSpec(
        operation_id=operation_id,
        target_power=target_power,
        duration_seconds=duration,
        verification_interval_seconds=0.25,
    )


def _snapshot(device: SimulatedJebaoDevice, *, power: int = 40) -> DeviceVerificationSnapshot:
    binding = device.physical_binding
    assert binding is not None
    return DeviceVerificationSnapshot(
        physical_binding=binding,
        enabled=True,
        power=power,
        mode="constant",
        frequency=25,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=False,
    )


def _record(
    device: SimulatedJebaoDevice,
    *,
    phase: DeviceVerificationPhase,
    expired: bool = False,
    reason: DeviceVerificationRecoveryReason | None = None,
) -> DeviceVerificationRecord:
    now = datetime.now(UTC)
    spec = _spec(operation_id=f"crash_{phase.value}", duration=1)
    return DeviceVerificationRecord(
        operation_id=spec.operation_id,
        phase=phase,
        spec=spec,
        snapshot=_snapshot(device),
        created_at=now - timedelta(seconds=2) if expired else now,
        updated_at=now,
        expires_at=now - timedelta(seconds=1) if expired else now + timedelta(seconds=1),
        write_started=phase is not DeviceVerificationPhase.PREPARED,
        recovery_reason=reason,
        error_code=(
            DeviceVerificationErrorCode.SAFETY_INTERLOCK
            if reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
            else DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
            if reason is DeviceVerificationRecoveryReason.RESTORE_FAILED
            else None
        ),
    )


async def _wait_for_phase(
    store: JsonDeviceVerificationJournalStore,
    phase: DeviceVerificationPhase,
) -> None:
    for _ in range(1000):
        record = store.load()
        if record is not None and record.phase is phase:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"journal did not reach {phase.value}")


async def test_success_journals_before_each_write_and_exactly_restores(tmp_path: Path) -> None:
    events: list[str] = []
    device = await _ready_device(events=events)
    store = _RecordingStore(tmp_path / "verify.json", events)
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    result = await controller.run(_spec())

    assert result.stop_reason is DeviceVerificationStopReason.TIMEOUT
    assert result.lower_power_applied is True
    assert [target.power for target in device.write_targets] == [40, 35, 40]
    first_write = events.index("write:40")
    restore_write = len(events) - 1 - events[::-1].index("write:40")
    assert events.index("journal:prepared") < first_write
    assert events.index("journal:same_value_pending") < first_write
    assert events.index("journal:lower_power_pending") < events.index("write:35")
    assert events.index("journal:restore_pending") < restore_write
    assert restore_write < len(events) - 2
    assert events[-2:] == ["read", "journal:clear"]
    assert store.load() is None

    await device.connect()
    final = await device.get_state()
    assert (final.enabled, final.power, final.mode, final.frequency) == (
        True,
        40,
        "constant",
        25,
    )
    assert final.linkage is LinkageRole.INDEPENDENT
    assert final.timer_enabled is False
    await device.disconnect()


async def test_manual_stop_before_first_write_performs_zero_writes(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_SlowReadDevice)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    task = asyncio.create_task(controller.run(_spec(duration=1)))
    await asyncio.sleep(0)
    assert controller.active_operation_id is not None
    assert await controller.stop() is True
    result = await task

    assert result.stop_reason is DeviceVerificationStopReason.STOPPED_BEFORE_WRITE
    assert result.lower_power_applied is False
    assert device.write_targets == []
    assert store.load() is None


async def test_expiry_during_preflight_performs_zero_writes(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_SlowReadDevice)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    result = await controller.run(_spec(duration=0.005))

    assert result.stop_reason is DeviceVerificationStopReason.EXPIRED_BEFORE_WRITE
    assert device.write_targets == []
    assert store.load() is None


async def test_interlock_is_checked_after_pending_marker_and_before_first_write(
    tmp_path: Path,
) -> None:
    guard = _GlobalGuard()
    device = await _ready_device()
    store = _TripOnPhaseStore(tmp_path / "verify.json", guard)
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with pytest.raises(DeviceVerificationApplyError) as raised:
        await controller.run(_spec())

    assert raised.value.code is DeviceVerificationErrorCode.SAFETY_INTERLOCK
    assert device.write_targets == []
    assert store.load() is None


async def test_stop_after_same_value_write_prevents_lower_power_mutation(
    tmp_path: Path,
) -> None:
    device = await _ready_device(device_class=_StopAfterFirstWriteDevice)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    device.controller = controller

    result = await controller.run(_spec(duration=1))

    assert result.stop_reason is DeviceVerificationStopReason.MANUAL
    assert result.lower_power_applied is False
    assert [target.power for target in device.write_targets] == [40, 40]
    assert store.load() is None


async def test_stop_racing_adapter_lock_blocks_wire_mutation(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_BlockedFirstWriteDevice)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    task = asyncio.create_task(controller.run(_spec(duration=1)))
    await device.write_entered.wait()

    assert await controller.stop() is True
    device.release_write.set()
    with pytest.raises(DeviceVerificationApplyError):
        await task

    # The adapter's under-lock guard rejects the qualification frame after the stop request.
    # The controller then conservatively emits only the exact restore frame because an adapter
    # exception is not proof that no bytes reached a real controller.
    assert [target.power for target in device.write_targets] == [40, 40]
    assert [command.value for command in device.commands if command.name == "power"] == [40]
    assert all(target.power != 35 for target in device.write_targets)
    assert store.load() is None


@pytest.mark.parametrize(
    ("changes", "power"),
    [
        ({"enabled": False}, 40),
        ({"timer_enabled": True}, 40),
        ({"mode": "sine"}, 40),
        ({"linkage": LinkageRole.MASTER}, 40),
        ({"error": "private fault at 192.0.2.10"}, 40),
        ({}, 46),
    ],
)
async def test_preflight_rejects_unsafe_initial_state_without_writes(
    tmp_path: Path,
    changes: dict,
    power: int,
) -> None:
    limits = PowerLimits(min_power=30, max_power=50)
    device = await _ready_device(power=power, capabilities=_capabilities(limits=limits))
    device._state = device._state.model_copy(update=changes)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationPreflightError) as raised:
        await controller.run(_spec())

    assert raised.value.code in {
        DeviceVerificationErrorCode.INVALID_INITIAL_STATE,
        DeviceVerificationErrorCode.INVALID_POWER,
    }
    assert "192.0.2.10" not in str(raised.value)
    assert device.write_targets == []
    assert store.load() is None


@pytest.mark.parametrize("target", [34, 40, 41])
async def test_target_must_be_one_to_five_points_lower(tmp_path: Path, target: int) -> None:
    device = await _ready_device()
    controller = FirstPhysicalWriteVerifier(
        device,
        JsonDeviceVerificationJournalStore(tmp_path / "verify.json"),
        global_guard=_GlobalGuard(),
    )

    with pytest.raises(DeviceVerificationPreflightError) as raised:
        await controller.run(_spec(target_power=target))

    assert raised.value.code is DeviceVerificationErrorCode.INVALID_POWER
    assert device.write_targets == []


async def test_target_and_snapshot_must_match_configured_step_and_limits(
    tmp_path: Path,
) -> None:
    device = await _ready_device(
        power=40,
        capabilities=_capabilities(
            limits=PowerLimits(min_power=36, max_power=44),
            step=2,
        ),
    )
    controller = FirstPhysicalWriteVerifier(
        device,
        JsonDeviceVerificationJournalStore(tmp_path / "verify.json"),
        global_guard=_GlobalGuard(),
    )

    with pytest.raises(DeviceVerificationPreflightError) as raised:
        await controller.run(_spec(target_power=37))

    assert raised.value.code is DeviceVerificationErrorCode.INVALID_POWER
    assert device.write_targets == []


def test_spec_hard_caps_target_and_duration() -> None:
    with pytest.raises(ValidationError):
        _spec(target_power=46)
    with pytest.raises(ValidationError):
        _spec(duration=10.001)


async def test_requires_exact_pro_profile_and_a_fresh_adapter(tmp_path: Path) -> None:
    wrong = await _ready_device(
        capabilities=_capabilities(product_key="different-product", model="lookalike")
    )
    controller = FirstPhysicalWriteVerifier(
        wrong,
        JsonDeviceVerificationJournalStore(tmp_path / "wrong.json"),
        global_guard=_GlobalGuard(),
    )
    with pytest.raises(DeviceVerificationPreflightError) as wrong_error:
        await controller.run(_spec())
    assert wrong_error.value.code is DeviceVerificationErrorCode.UNSUPPORTED_DEVICE

    fresh = await _ready_device()
    await fresh.connect()
    controller = FirstPhysicalWriteVerifier(
        fresh,
        JsonDeviceVerificationJournalStore(tmp_path / "connected.json"),
        global_guard=_GlobalGuard(),
    )
    with pytest.raises(DeviceVerificationPreflightError) as fresh_error:
        await controller.run(_spec())
    assert fresh_error.value.code is DeviceVerificationErrorCode.FRESH_SESSION_REQUIRED
    assert fresh.write_targets == []
    await fresh.disconnect()


async def test_initial_safety_interlock_blocks_before_connection_or_journal(
    tmp_path: Path,
) -> None:
    device = await _ready_device()
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(
        device,
        store,
        global_guard=_GlobalGuard(permitted=False),
    )

    with pytest.raises(DeviceVerificationPreflightError) as raised:
        await controller.run(_spec())

    assert raised.value.code is DeviceVerificationErrorCode.SAFETY_INTERLOCK
    assert device.connected is False
    assert device.write_targets == []
    assert store.load() is None


async def test_cancellation_is_shielded_until_exact_restore(tmp_path: Path) -> None:
    device = await _ready_device()
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_for_phase(store, DeviceVerificationPhase.LOWER_POWER_ACTIVE)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.load() is None
    assert [target.power for target in device.write_targets] == [40, 35, 40]
    await device.connect()
    assert (await device.get_state()).power == 40
    await device.disconnect()


async def test_repeated_cancellation_cannot_cancel_restore_child(tmp_path: Path) -> None:
    device = await _ready_device(latency_seconds=0.003)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_for_phase(store, DeviceVerificationPhase.LOWER_POWER_ACTIVE)

    task.cancel()
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.load() is None
    await device.connect()
    assert (await device.get_state()).power == 40
    await device.disconnect()


async def test_trip_after_first_write_latches_typed_safety_recovery(tmp_path: Path) -> None:
    guard = _GlobalGuard()
    device = await _ready_device(device_class=_TripAfterFirstWriteDevice, guard=guard)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with pytest.raises(DeviceVerificationRecoveryDeferred) as raised:
        await controller.run(_spec())

    assert raised.value.code is DeviceVerificationErrorCode.SAFETY_INTERLOCK
    record = store.load()
    assert record is not None
    assert record.phase is DeviceVerificationPhase.RECOVERY_REQUIRED
    assert record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
    assert record.error_code is DeviceVerificationErrorCode.SAFETY_INTERLOCK
    assert [target.power for target in device.write_targets] == [40, 0]
    assert device.write_targets[-1].enabled is False


async def test_safety_recovery_never_restores_on_without_attended_authority(
    tmp_path: Path,
) -> None:
    device = await _ready_device()
    # Model a deployment-wide emergency stop that turned the pump OFF after the test journal
    # was written. Recovery must not undo that OFF state merely because its snapshot was ON.
    device._state = device._state.model_copy(update={"enabled": False, "power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    record = _record(
        device,
        phase=DeviceVerificationPhase.RECOVERY_REQUIRED,
        reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
    )
    store.create(record)
    guard = _GlobalGuard()
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with pytest.raises(DeviceVerificationRecoveryDeferred) as raised:
        await controller.recover_pending()
    assert raised.value.code is DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
    assert device.write_targets == []
    assert device.connected is False
    assert device._state.enabled is False

    now = datetime.now(UTC)
    authority = AttendedRestoreAuthority(
        operation_id=record.operation_id,
        physical_binding=record.snapshot.physical_binding,
        issued_at=now,
        expires_at=now + timedelta(minutes=1),
    )
    assert await controller.recover_pending(attended_authority=authority) is True
    assert [target.power for target in device.write_targets] == [40]
    assert device.write_targets[-1].enabled is True
    assert store.load() is None


async def test_interlock_racing_restore_keeps_attended_recovery_latched(
    tmp_path: Path,
) -> None:
    guard = _GlobalGuard()
    device = await _ready_device(device_class=_TripAfterRestoreWriteDevice, guard=guard)
    device._state = device._state.model_copy(update={"power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with pytest.raises(DeviceVerificationRecoveryDeferred) as raised:
        await controller.recover_pending()

    assert raised.value.code is DeviceVerificationErrorCode.SAFETY_INTERLOCK
    record = store.load()
    assert record is not None
    assert record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
    assert record.error_code is DeviceVerificationErrorCode.SAFETY_INTERLOCK
    assert [target.power for target in device.write_targets] == [40, 0]

    guard.clear()
    with pytest.raises(DeviceVerificationRecoveryDeferred) as unattended:
        await controller.recover_pending()
    assert unattended.value.code is DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
    assert [target.power for target in device.write_targets] == [40, 0]


async def test_interlock_after_journal_clear_recreates_recovery_and_stops(
    tmp_path: Path,
) -> None:
    guard = _GlobalGuard()
    device = await _ready_device()
    device._state = device._state.model_copy(update={"power": 35})
    store = _TripAfterClearStore(tmp_path / "verify.json", guard)
    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with pytest.raises(DeviceVerificationRecoveryDeferred):
        await controller.recover_pending()

    record = store.load()
    assert record is not None
    assert record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
    assert [target.power for target in device.write_targets] == [40, 0]
    assert device.write_targets[-1].enabled is False


async def test_wrong_or_expired_attended_authority_is_zero_write(tmp_path: Path) -> None:
    device = await _ready_device()
    device._state = device._state.model_copy(update={"power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    record = _record(
        device,
        phase=DeviceVerificationPhase.RECOVERY_REQUIRED,
        reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
    )
    store.create(record)
    now = datetime.now(UTC)
    wrong_binding = PhysicalDeviceBinding.model_validate(
        {
            **record.snapshot.physical_binding.model_dump(),
            "config_fingerprint": "f" * 64,
        }
    )
    authority = AttendedRestoreAuthority(
        operation_id=record.operation_id,
        physical_binding=wrong_binding,
        issued_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(minutes=1),
    )
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationRecoveryDeferred):
        await controller.recover_pending(attended_authority=authority)

    assert device.write_targets == []
    assert store.load() is not None


async def test_attended_authority_must_be_issued_after_safety_latch(tmp_path: Path) -> None:
    device = await _ready_device()
    device._state = device._state.model_copy(update={"enabled": False, "power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    record = _record(
        device,
        phase=DeviceVerificationPhase.RECOVERY_REQUIRED,
        reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
    )
    store.create(record)
    authority = AttendedRestoreAuthority(
        operation_id=record.operation_id,
        physical_binding=record.snapshot.physical_binding,
        issued_at=record.updated_at - timedelta(seconds=30),
        expires_at=record.updated_at + timedelta(seconds=30),
    )
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationRecoveryDeferred) as raised:
        await controller.recover_pending(attended_authority=authority)

    assert raised.value.code is DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
    assert device.write_targets == []
    assert device._state.enabled is False


async def test_retripped_interlock_invalidates_previous_attended_authority(
    tmp_path: Path,
) -> None:
    guard = _GlobalGuard()
    device = await _ready_device(device_class=_TripAfterRestoreWriteDevice, guard=guard)
    device._state = device._state.model_copy(update={"enabled": False, "power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    original = _record(
        device,
        phase=DeviceVerificationPhase.RECOVERY_REQUIRED,
        reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
    )
    store.create(original)
    issued_at = datetime.now(UTC)
    authority = AttendedRestoreAuthority(
        operation_id=original.operation_id,
        physical_binding=original.snapshot.physical_binding,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=1),
    )
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with pytest.raises(DeviceVerificationRecoveryDeferred):
        await controller.recover_pending(attended_authority=authority)
    retripped = store.load()
    assert retripped is not None
    assert retripped.updated_at > authority.issued_at

    guard.clear()
    with pytest.raises(DeviceVerificationRecoveryDeferred) as reused:
        await controller.recover_pending(attended_authority=authority)
    assert reused.value.code is DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
    assert [target.power for target in device.write_targets] == [40, 0]


@pytest.mark.parametrize(
    "phase",
    [
        DeviceVerificationPhase.SAME_VALUE_PENDING,
        DeviceVerificationPhase.SAME_VALUE_VERIFIED,
        DeviceVerificationPhase.LOWER_POWER_PENDING,
        DeviceVerificationPhase.LOWER_POWER_ACTIVE,
        DeviceVerificationPhase.RESTORE_PENDING,
        DeviceVerificationPhase.RECOVERY_REQUIRED,
    ],
)
async def test_crash_recovery_at_every_mutating_phase_only_exactly_restores(
    tmp_path: Path,
    phase: DeviceVerificationPhase,
) -> None:
    device = await _ready_device()
    device._state = device._state.model_copy(update={"power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / f"{phase.value}.json")
    reason = (
        DeviceVerificationRecoveryReason.RESTORE_FAILED
        if phase is DeviceVerificationPhase.RECOVERY_REQUIRED
        else None
    )
    store.create(_record(device, phase=phase, reason=reason))
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    assert await controller.recover_pending() is True

    assert [target.power for target in device.write_targets] == [40]
    assert all(target.power != 35 for target in device.write_targets)
    assert store.load() is None


async def test_stale_prepared_marker_is_read_then_cleared_with_zero_writes(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    device = await _ready_device(events=events)
    store = _RecordingStore(tmp_path / "prepared.json", events)
    store.create(_record(device, phase=DeviceVerificationPhase.PREPARED, expired=True))
    events.clear()
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    assert await controller.recover_pending() is True

    assert device.write_targets == []
    assert events[-2:] == ["read", "journal:clear"]
    assert store.load() is None


async def test_stale_mutating_recovery_requires_fresh_attended_authority(
    tmp_path: Path,
) -> None:
    device = await _ready_device()
    device._state = device._state.model_copy(update={"power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "stale.json")
    now = datetime.now(UTC)
    stale = _record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE).model_copy(
        update={
            "created_at": now - timedelta(minutes=2),
            "updated_at": now - timedelta(minutes=1),
            "expires_at": now - timedelta(minutes=1, seconds=30),
        }
    )
    store.create(stale)
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationRecoveryDeferred) as unattended:
        await controller.recover_pending()

    assert unattended.value.code is DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
    assert device.write_targets == []

    issued_at = datetime.now(UTC)
    authority = AttendedRestoreAuthority(
        operation_id=stale.operation_id,
        physical_binding=stale.snapshot.physical_binding,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=1),
    )
    assert await controller.recover_pending(attended_authority=authority) is True
    assert [target.power for target in device.write_targets] == [40]
    assert store.load() is None


async def test_future_updated_record_blocks_automatic_verification_restore(
    tmp_path: Path,
) -> None:
    device = await _ready_device()
    device._state = device._state.model_copy(update={"power": 35})
    now = datetime.now(UTC)
    record = _record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE).model_copy(
        update={
            "created_at": now - timedelta(seconds=1),
            "updated_at": now + timedelta(minutes=1),
            "expires_at": now + timedelta(seconds=1),
        }
    )
    store = JsonDeviceVerificationJournalStore(tmp_path / "future-update.json")
    store.create(record)
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationRecoveryDeferred) as unattended:
        await controller.recover_pending()

    assert unattended.value.code is DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
    assert device.write_targets == []
    assert store.load() == record


async def test_recovery_rejects_physical_binding_mismatch_before_connect_or_write(
    tmp_path: Path,
) -> None:
    device = await _ready_device()
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    record = _record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE)
    different = PhysicalDeviceBinding.model_validate(
        {
            **record.snapshot.physical_binding.model_dump(),
            "mac_address_digest": "a" * 64,
        }
    )
    store.create(
        record.model_copy(
            update={"snapshot": record.snapshot.model_copy(update={"physical_binding": different})}
        )
    )
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationPreflightError) as raised:
        await controller.recover_pending()

    assert raised.value.code is DeviceVerificationErrorCode.BINDING_MISMATCH
    assert device.connected is False
    assert device.write_targets == []


async def test_same_value_readback_drift_is_restored_and_reported(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_CorruptWriteDevice, corrupt_call=1)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationApplyError) as raised:
        await controller.run(_spec())

    assert raised.value.code is DeviceVerificationErrorCode.SAME_VALUE_VERIFY_FAILED
    assert [target.power for target in device.write_targets] == [40, 40]
    assert store.load() is None


async def test_lower_power_readback_drift_is_restored_and_reported(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_CorruptWriteDevice, corrupt_call=2)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationApplyError) as raised:
        await controller.run(_spec())

    assert raised.value.code is DeviceVerificationErrorCode.LOWER_POWER_VERIFY_FAILED
    assert [target.power for target in device.write_targets] == [40, 35, 40]
    assert store.load() is None


async def test_hung_post_lower_read_cannot_delay_restore_attempt_indefinitely(
    tmp_path: Path,
) -> None:
    device = await _ready_device(device_class=_HangAfterLowerWriteDevice)
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    controller._RESTORE_IO_TIMEOUT_SECONDS = 0.02
    started = asyncio.get_running_loop().time()

    with pytest.raises(DeviceVerificationRollbackError):
        await controller.run(_spec(duration=0.03))

    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.2
    assert [target.power for target in device.write_targets] == [40, 35, 40]
    record = store.load()
    assert record is not None
    assert record.phase is DeviceVerificationPhase.RECOVERY_REQUIRED
    assert record.error_code is DeviceVerificationErrorCode.RESTORE_VERIFY_FAILED


async def test_hung_restore_write_is_bounded_and_keeps_recovery_journal(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_HangRestoreWriteDevice)
    device._state = device._state.model_copy(update={"power": 35})
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    controller._RESTORE_IO_TIMEOUT_SECONDS = 0.02
    started = asyncio.get_running_loop().time()

    with pytest.raises(DeviceVerificationRollbackError) as raised:
        await controller.recover_pending()

    assert asyncio.get_running_loop().time() - started < 0.2
    assert raised.value.code is DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
    record = store.load()
    assert record is not None
    assert record.recovery_reason is DeviceVerificationRecoveryReason.RESTORE_FAILED
    assert record.error_code is DeviceVerificationErrorCode.RESTORE_WRITE_FAILED


async def test_hung_disconnect_cannot_hold_global_lease_forever(tmp_path: Path) -> None:
    device = await _ready_device(device_class=_HangDisconnectDevice)
    device.hang_disconnect = True
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())
    controller._RESTORE_IO_TIMEOUT_SECONDS = 0.02
    started = asyncio.get_running_loop().time()

    result = await controller.run(_spec(duration=0.03))

    assert result.lower_power_applied is True
    assert asyncio.get_running_loop().time() - started < 0.2
    assert store.load() is None


async def test_restore_error_record_is_typed_and_contains_no_raw_exception(
    tmp_path: Path,
) -> None:
    device = await _ready_device(device_class=_FailRestoreDevice)
    device._state = device._state.model_copy(update={"power": 35})
    path = tmp_path / "verify.json"
    store = JsonDeviceVerificationJournalStore(path)
    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationRollbackError) as raised:
        await controller.recover_pending()

    assert raised.value.code is DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
    record = store.load()
    assert record is not None
    assert record.recovery_reason is DeviceVerificationRecoveryReason.RESTORE_FAILED
    assert record.error_code is DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
    persisted = path.read_text(encoding="utf-8")
    assert "192.0.2.10" not in persisted
    assert "private-token" not in persisted
    assert "restore failed" not in persisted


async def test_journal_contains_only_digested_identity(tmp_path: Path) -> None:
    device = await _ready_device()
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id="private-vendor-id",
        mac_address="001122334455",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        config_fingerprint="1" * 64,
    )
    device._physical_binding = binding
    path = tmp_path / "verify.json"
    store = JsonDeviceVerificationJournalStore(path)
    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))

    payload = path.read_text(encoding="utf-8")
    assert "private-vendor-id" not in payload
    assert "001122334455" not in payload
    assert binding.vendor_device_id_digest in payload
    assert binding.mac_address_digest in payload
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(payload)["error_code"] is None


@pytest.mark.parametrize("unsafe_kind", ["fifo", "hardlink", "mode"])
def test_verification_journal_rejects_unsafe_files_without_blocking(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    path = tmp_path / "verify.json"
    if unsafe_kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)
        if unsafe_kind == "hardlink":
            os.link(path, tmp_path / "verification-alias")
        else:
            path.chmod(0o640)

    with pytest.raises(DeviceVerificationError) as raised:
        JsonDeviceVerificationJournalStore(path).load()
    assert raised.value.code is DeviceVerificationErrorCode.DEVICE_IO_FAILED


async def test_global_guard_serializes_qualification_and_recovery(tmp_path: Path) -> None:
    guard = _GlobalGuard()
    device = await _ready_device()
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=guard)

    with guard.lease():
        with pytest.raises(DeviceVerificationBusyError) as raised:
            await controller.run(_spec())

    assert raised.value.code is DeviceVerificationErrorCode.OPERATION_BUSY
    assert device.write_targets == []

    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))
    with guard.lease():
        with pytest.raises(DeviceVerificationBusyError) as recovery_error:
            await controller.recover_pending()
    assert recovery_error.value.code is DeviceVerificationErrorCode.OPERATION_BUSY
    assert device.connected is False
    assert device.write_targets == []


async def test_unfinished_journal_blocks_new_run_before_connection(tmp_path: Path) -> None:
    device = await _ready_device()
    store = JsonDeviceVerificationJournalStore(tmp_path / "verify.json")
    store.create(_record(device, phase=DeviceVerificationPhase.LOWER_POWER_ACTIVE))
    controller = FirstPhysicalWriteVerifier(device, store, global_guard=_GlobalGuard())

    with pytest.raises(DeviceVerificationBusyError) as raised:
        await controller.run(_spec(operation_id="must_not_start"))

    assert raised.value.code is DeviceVerificationErrorCode.JOURNAL_BUSY
    assert device.connected is False
    assert device.write_targets == []


def test_record_rejects_untyped_or_misplaced_recovery_error() -> None:
    now = datetime.now(UTC)
    device = SimulatedJebaoDevice("validation", capabilities=_capabilities())
    spec = _spec()
    values = {
        "operation_id": spec.operation_id,
        "phase": DeviceVerificationPhase.RECOVERY_REQUIRED,
        "spec": spec,
        "snapshot": _snapshot(device),
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(seconds=1),
        "write_started": True,
    }
    with pytest.raises(ValidationError):
        DeviceVerificationRecord.model_validate(values)

    values.update(
        {
            "phase": DeviceVerificationPhase.LOWER_POWER_ACTIVE,
            "recovery_reason": DeviceVerificationRecoveryReason.RESTORE_FAILED,
            "error_code": DeviceVerificationErrorCode.RESTORE_WRITE_FAILED,
        }
    )
    with pytest.raises(ValidationError):
        DeviceVerificationRecord.model_validate(values)
