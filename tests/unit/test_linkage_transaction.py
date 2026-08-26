import asyncio
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.devices import (
    DeviceControlSnapshot,
    LinkageApplyError,
    LinkageJournalClaimError,
    LinkagePreflightError,
    LinkageRecoveryAuthority,
    LinkageRecoveryReason,
    LinkageRollbackError,
    LinkageSafetyInterlock,
    LinkageStopReason,
    LinkageTestSpec,
    LinkageTransactionBusyError,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    PhysicalDeviceBinding,
    SimulatedJebaoDevice,
    TemporaryLinkageController,
    schedule_structure_fingerprint,
)
from jebao_flow.persistence import JsonLinkageJournalStore, LinkageJournalError
from jebao_flow.protocol.models import (
    Capability,
    DeviceCapabilities,
    DeviceSchedule,
    DeviceTarget,
    LinkageRole,
    ScheduleEntry,
)


class _RecordingStore(JsonLinkageJournalStore):
    def __init__(self, path: Path, events: list[str] | None = None) -> None:
        super().__init__(path)
        self.events = events
        self.records = []

    def save(self, record):
        self.records.append(record)
        if self.events is not None:
            self.events.append(f"journal:{record.phase.value}")
        super().save(record)

    def create(self, record):
        self.records.append(record)
        if self.events is not None:
            self.events.append(f"journal:{record.phase.value}")
        super().create(record)


class _RecordingDevice(SimulatedJebaoDevice):
    def __init__(self, device_id: str, events: list[str] | None = None, **kwargs) -> None:
        super().__init__(device_id, **kwargs)
        self.events = events

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        if self.events is not None:
            self.events.append(f"write:{self.device_id}:{target.linkage}")
        await super().write_target(target, guard=guard)


class _FailOnceOnRelationshipDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.failed = False

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if (
            target.linkage
            in {
                LinkageRole.SYNC_SLAVE,
                LinkageRole.ASYNC_SLAVE,
            }
            and not self.failed
        ):
            self.failed = True
            raise RuntimeError("simulated ACK loss after apply")


class _FailOnceOnBootstrapStepDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.failed = False

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if (
            target.power == 30
            and target.linkage is LinkageRole.INDEPENDENT
            and target.timer_enabled is False
            and not self.failed
        ):
            self.failed = True
            raise RuntimeError("simulated bootstrap step ACK loss")


class _FailTimerRestoreDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.fail_timer_restore = False

    async def set_timer_enabled(self, enabled: bool) -> None:
        if enabled and self.fail_timer_restore:
            raise RuntimeError("simulated timer restore failure")
        await super().set_timer_enabled(enabled)

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        if target.timer_enabled and self.fail_timer_restore:
            raise RuntimeError("simulated timer restore failure")
        await super().write_target(target, guard=guard)


class _ScheduledDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.schedule = DeviceSchedule(enabled=True)

    async def get_state(self):
        state = await super().get_state()
        return state.model_copy(
            update={
                "schedule": self.schedule.model_copy(update={"enabled": bool(state.timer_enabled)})
            }
        )


class _MissingScheduleDevice(_RecordingDevice):
    async def get_state(self):
        state = await super().get_state()
        return state.model_copy(update={"schedule": None})


class _InvalidScheduleDevice(_RecordingDevice):
    async def get_state(self):
        state = await super().get_state()
        return state.model_copy(
            update={
                "schedule": DeviceSchedule(
                    enabled=bool(state.timer_enabled),
                    invalid_slots=(0,),
                )
            }
        )


class _SlowSnapshotDevice(_RecordingDevice):
    async def get_state(self):
        await asyncio.sleep(0.02)
        return await super().get_state()


class _SlowBootstrapDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id, latency_seconds=0.01)


class _ScheduleAdvancesOnTimerResumeDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.advance_on_resume = False

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        timer_was_disabled = self._state.timer_enabled is False  # noqa: SLF001
        await super().write_target(target, guard=guard)
        if self.advance_on_resume and timer_was_disabled and target.timer_enabled is True:
            self._state = self._state.model_copy(  # noqa: SLF001
                update={"power": 54, "mode": "pulse", "frequency": 11}
            )


class _DriftPeerOnTimerRestoreDevice(_RecordingDevice):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.peer: SimulatedJebaoDevice | None = None
        self.drift_once = False

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if target.timer_enabled is True and self.drift_once and self.peer is not None:
            self.drift_once = False
            self.peer._state = self.peer._state.model_copy(  # noqa: SLF001
                update={"power": self.peer._state.power + 1}  # noqa: SLF001
            )


def _binding(device_id: str, *, product_key: str = "simulator") -> PhysicalDeviceBinding:
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=f"private-vendor-{device_id}",
        mac_address="001122334455" if device_id == "master" else "aabbccddeeff",
        product_key=product_key,
        config_fingerprint="1" * 64 if device_id == "master" else "2" * 64,
    )


async def _ready_device(
    device_id: str,
    *,
    device_class: type[_RecordingDevice] = _RecordingDevice,
    capabilities: DeviceCapabilities | None = None,
    enabled: bool = True,
    power: int = 45,
    frequency: int = 25,
    timer_enabled: bool = True,
) -> _RecordingDevice:
    device = (
        device_class(device_id)
        if capabilities is None
        else device_class(
            device_id,
            capabilities=capabilities,
        )
    )
    await device.connect()
    await device.set_enabled(enabled)
    await device.set_power(power)
    await device.set_mode("constant")
    await device.set_frequency(frequency)
    await device.set_linkage(LinkageRole.INDEPENDENT)
    await device.set_timer_enabled(timer_enabled)
    device.commands.clear()
    return device


def _spec(
    *,
    role: LinkageRole = LinkageRole.SYNC_SLAVE,
    duration: float = 0.02,
    verification_interval: float = 0.005,
) -> LinkageTestSpec:
    return LinkageTestSpec(
        operation_id=f"test_{role.value}",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=role,
        mode="sine",
        master_power=60,
        slave_power=42,
        frequency=30,
        duration_seconds=duration,
        verification_interval_seconds=verification_interval,
    )


def _controller(
    master: SimulatedJebaoDevice,
    slave: SimulatedJebaoDevice,
    store: JsonLinkageJournalStore,
    *,
    interlock: LinkageSafetyInterlock | None = None,
) -> TemporaryLinkageController:
    return TemporaryLinkageController(
        {"master": master, "slave": slave},
        store,
        safety_interlock=interlock or LinkageSafetyInterlock(initially_permitted=True),
    )


async def _wait_until_active(
    controller: TemporaryLinkageController,
    store: JsonLinkageJournalStore,
) -> None:
    for _ in range(1000):
        record = store.load()
        if (
            controller.active_operation_id is not None
            and record is not None
            and record.phase is LinkageTransactionPhase.ACTIVE
        ):
            return
        await asyncio.sleep(0.001)
    raise AssertionError("linkage transaction did not become active")


@pytest.mark.parametrize(
    "role",
    [LinkageRole.SYNC_SLAVE, LinkageRole.ASYNC_SLAVE],
)
async def test_temporary_linkage_applies_distinct_power_and_restores_on_manual_stop(
    tmp_path: Path,
    role: LinkageRole,
) -> None:
    master = await _ready_device("master", power=48, frequency=21)
    slave = await _ready_device("slave", power=52, frequency=27)
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = _spec(role=role, duration=5)

    task = asyncio.create_task(controller.run(spec))
    await _wait_until_active(controller, store)

    master_active = await master.get_state()
    slave_active = await slave.get_state()
    assert (master_active.linkage, master_active.power) == (LinkageRole.MASTER, 60)
    assert (slave_active.linkage, slave_active.power) == (role, 42)
    assert master_active.timer_enabled is False
    assert slave_active.timer_enabled is False

    assert await controller.stop(spec.operation_id) is True
    result = await task

    assert result.stop_reason is LinkageStopReason.MANUAL
    assert store.load() is None
    assert (await master.get_state()).model_dump(exclude={"observed_at"}) == {
        "online": True,
        "enabled": True,
        "power": 48,
        "mode": "constant",
        "frequency": 21,
        "linkage": LinkageRole.INDEPENDENT,
        "timer_enabled": True,
        "error": None,
        "schedule": {
            "enabled": True,
            "device_local_time": None,
            "slot_capacity": 48,
            "entries": (),
            "invalid_slots": (),
        },
        "observed_attributes": {},
    }
    assert (await slave.get_state()).power == 52
    assert (await slave.get_state()).frequency == 27
    for device in (master, slave):
        final_timer = next(
            command for command in reversed(device.commands) if command.name == "timer_enabled"
        )
        assert final_timer.value is True
        assert final_timer.issued_at == device.commands[-1].issued_at


async def test_timeout_restores_and_journal_precedes_first_device_write(tmp_path: Path) -> None:
    events: list[str] = []
    master = _RecordingDevice("master", events)
    slave = _RecordingDevice("slave", events)
    for device in (master, slave):
        await device.connect()
        await device.set_enabled(True)
        await device.set_power(45)
        await device.set_mode("constant")
        await device.set_frequency(20)
        await device.set_linkage(LinkageRole.INDEPENDENT)
        await device.set_timer_enabled(True)
        device.commands.clear()
    events.clear()
    store = _RecordingStore(tmp_path / "linkage.json", events)
    controller = _controller(master, slave, store)

    result = await controller.run(_spec(duration=0.1))

    first_write = next(index for index, value in enumerate(events) if value.startswith("write:"))
    assert events.index("journal:prepared") < first_write
    assert events.index("journal:applying") < first_write
    assert result.stop_reason is LinkageStopReason.TIMEOUT
    assert store.load() is None


async def test_prewrite_stop_during_snapshot_sends_zero_control_frames(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", device_class=_SlowSnapshotDevice)
    slave = await _ready_device("slave")
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    task = asyncio.create_task(controller.run(_spec(duration=1)))
    while controller.active_operation_id is None:  # noqa: ASYNC110
        await asyncio.sleep(0)
    assert await controller.stop() is True

    result = await task

    assert result.stop_reason is LinkageStopReason.MANUAL
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_snapshot_delay_expiry_sends_zero_control_frames(tmp_path: Path) -> None:
    master = await _ready_device("master", device_class=_SlowSnapshotDevice)
    slave = await _ready_device("slave", device_class=_SlowSnapshotDevice)
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkageApplyError, match="expired before its first control frame"):
        await controller.run(_spec(duration=0.005))

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_apply_failure_after_slave_write_restores_both_snapshots(tmp_path: Path) -> None:
    master = await _ready_device("master", power=44, frequency=18)
    slave = await _ready_device(
        "slave",
        device_class=_FailOnceOnRelationshipDevice,
        power=51,
        frequency=24,
    )
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkageApplyError, match="failed and was restored"):
        await controller.run(_spec())

    assert store.load() is None
    assert (await master.get_state()).power == 44
    assert (await slave.get_state()).power == 51
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).timer_enabled is True


async def test_task_cancellation_is_shielded_until_restore_completes(tmp_path: Path) -> None:
    master = await _ready_device("master", power=47)
    slave = await _ready_device("slave", power=53)
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_until_active(controller, store)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.load() is None
    assert (await master.get_state()).power == 47
    assert (await slave.get_state()).power == 53
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_repeated_cancellation_cannot_cancel_the_rollback_child(tmp_path: Path) -> None:
    master = _RecordingDevice("master", latency_seconds=0.002)
    slave = _RecordingDevice("slave", latency_seconds=0.002)
    for device, power in ((master, 47), (slave, 53)):
        await device.connect()
        await device.set_enabled(True)
        await device.set_power(power)
        await device.set_mode("constant")
        await device.set_frequency(25)
        await device.set_linkage(LinkageRole.INDEPENDENT)
        await device.set_timer_enabled(True)
        device.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_until_active(controller, store)

    for _ in range(5):
        task.cancel()
        await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.load() is None
    assert (await master.get_state()).power == 47
    assert (await slave.get_state()).power == 53
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).timer_enabled is True


async def test_active_watchdog_detects_slave_power_being_overwritten(tmp_path: Path) -> None:
    master = await _ready_device("master", power=47)
    slave = await _ready_device("slave", power=53)
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    task = asyncio.create_task(controller.run(_spec(duration=5, verification_interval=0.005)))
    await _wait_until_active(controller, store)

    # Simulate the controller behavior seen in the vendor app: master propagation overwrites
    # the independently requested slave Flow after the initial ACK/read-back passed.
    await slave.set_power(60)

    with pytest.raises(LinkageApplyError, match="failed and was restored"):
        await task

    assert store.load() is None
    assert (await master.get_state()).power == 47
    assert (await slave.get_state()).power == 53
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_schedule_bootstrap_qualifies_changes_async_slave_power_and_restores(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", power=89, frequency=34)
    slave = await _ready_device("slave", power=30, frequency=32)
    master._capabilities = master.capabilities.model_copy(  # noqa: SLF001
        update={"native_modes": master.capabilities.native_modes | {"random"}}
    )
    await master.set_mode("random")
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = LinkageTestSpec(
        operation_id="scheduled_async_power_step",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=0.08,
        verification_interval_seconds=0.005,
        bootstrap_active_schedule=True,
        slave_power_after=38,
        power_change_after_seconds=0.02,
    )

    result = await controller.run(spec)

    assert result.stop_reason is LinkageStopReason.TIMEOUT
    assert set(result.bootstrap_qualified_device_ids) == {"master", "slave"}
    assert result.slave_power_change_verified is True
    assert store.load() is None
    master_state = await master.get_state()
    slave_state = await slave.get_state()
    assert (master_state.power, master_state.mode, master_state.frequency) == (89, "random", 34)
    assert (slave_state.power, slave_state.mode, slave_state.frequency) == (30, "constant", 32)
    assert master_state.linkage is LinkageRole.INDEPENDENT
    assert slave_state.linkage is LinkageRole.INDEPENDENT
    assert master_state.timer_enabled is True
    assert slave_state.timer_enabled is True
    assert any(command.name == "power" and command.value == 38 for command in slave.commands)
    timer_values = [
        command.value for command in slave.commands if command.name == "timer_enabled"
    ]
    assert timer_values[0] is False
    assert timer_values[-1] is True
    assert [
        command.value for command in slave.commands if command.name == "power"
    ][:3] == [31, 30, 31]
    master_frames: dict[datetime, dict[str, object]] = {}
    for command in master.commands:
        master_frames.setdefault(command.issued_at, {})[command.name] = command.value
    timer_off_powers = [
        frame["power"]
        for frame in master_frames.values()
        if frame.get("timer_enabled") is False and "power" in frame
    ]
    assert timer_off_powers
    assert max(timer_off_powers) <= 45
    restored_high_frames = [
        frame
        for frame in master_frames.values()
        if frame.get("power") == 89
    ]
    assert restored_high_frames == [
        {
            "enabled": True,
            "timer_enabled": True,
            "linkage": LinkageRole.INDEPENDENT,
            "power": 89,
            "mode": "random",
            "frequency": 34,
        }
    ]


async def test_schedule_bootstrap_requires_timer_on_before_any_write(tmp_path: Path) -> None:
    master = await _ready_device("master", timer_enabled=False)
    slave = await _ready_device("slave", timer_enabled=True)
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = LinkageTestSpec(
        operation_id="scheduled_requires_timer",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=1,
        bootstrap_active_schedule=True,
    )

    with pytest.raises(LinkagePreflightError, match="active decoded schedule"):
        await controller.run(spec)

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_schedule_bootstrap_step_failure_restores_original_timer_on_snapshots(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", power=70, frequency=34)
    slave = await _ready_device(
        "slave",
        device_class=_FailOnceOnBootstrapStepDevice,
        power=65,
        frequency=32,
    )
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = LinkageTestSpec(
        operation_id="scheduled_step_failure",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=1,
        bootstrap_active_schedule=True,
    )

    with pytest.raises(LinkageApplyError, match="failed and was restored"):
        await controller.run(spec)

    assert store.load() is None
    master_state = await master.get_state()
    slave_state = await slave.get_state()
    assert (master_state.power, master_state.frequency, master_state.timer_enabled) == (
        70,
        34,
        True,
    )
    assert (slave_state.power, slave_state.frequency, slave_state.timer_enabled) == (
        65,
        32,
        True,
    )
    assert master_state.linkage is LinkageRole.INDEPENDENT
    assert slave_state.linkage is LinkageRole.INDEPENDENT


async def test_schedule_bootstrap_manual_stop_before_lower_step_reports_no_qualification(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", device_class=_SlowBootstrapDevice, power=70)
    slave = await _ready_device("slave", device_class=_SlowBootstrapDevice, power=65)
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = LinkageTestSpec(
        operation_id="scheduled_early_stop",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=5,
        bootstrap_active_schedule=True,
        slave_power_after=38,
        power_change_after_seconds=4,
    )

    task = asyncio.create_task(controller.run(spec))
    while not master.commands:  # noqa: ASYNC110 - wait for first safe bootstrap frame
        await asyncio.sleep(0.001)
    assert await controller.stop(spec.operation_id) is True
    result = await task

    assert result.stop_reason is LinkageStopReason.MANUAL
    assert result.bootstrap_qualified_device_ids == ()
    assert result.slave_power_change_verified is False
    assert store.load() is None
    assert (await master.get_state()).power == 70
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).power == 65
    assert (await slave.get_state()).timer_enabled is True


async def test_schedule_bootstrap_fails_closed_if_timer_resume_changes_manual_fallback(
    tmp_path: Path,
) -> None:
    master = await _ready_device(
        "master",
        device_class=_ScheduleAdvancesOnTimerResumeDevice,
        power=70,
        frequency=34,
    )
    slave = await _ready_device("slave", power=65, frequency=32)
    master.advance_on_resume = True
    master.commands.clear()
    slave.commands.clear()
    original_schedule_fingerprint = schedule_structure_fingerprint(
        (await master.get_state()).schedule
    )
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = LinkageTestSpec(
        operation_id="scheduled_timer_boundary",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=0.08,
        verification_interval_seconds=0.005,
        bootstrap_active_schedule=True,
        slave_power_after=38,
        power_change_after_seconds=0.02,
    )

    with pytest.raises(LinkageRollbackError, match="final_verification_failed"):
        await controller.run(spec)

    pending = store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.RESTORE_FAILED
    assert pending.failed_device_ids == ("master",)
    master_state = await master.get_state()
    assert master_state.timer_enabled is False
    assert master_state.linkage is LinkageRole.INDEPENDENT
    assert schedule_structure_fingerprint(master_state.schedule) == original_schedule_fingerprint


async def test_schedule_bootstrap_setup_expiry_never_reports_slave_step(tmp_path: Path) -> None:
    master = await _ready_device("master", device_class=_SlowBootstrapDevice, power=70)
    slave = await _ready_device("slave", device_class=_SlowBootstrapDevice, power=65)
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    spec = LinkageTestSpec(
        operation_id="scheduled_setup_expiry",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=0.05,
        verification_interval_seconds=0.005,
        bootstrap_active_schedule=True,
        slave_power_after=38,
        power_change_after_seconds=0.04,
    )

    with pytest.raises(LinkageApplyError, match="failed and was restored"):
        await controller.run(spec)

    assert store.load() is None
    assert not any(command.name == "power" and command.value == 38 for command in slave.commands)
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).timer_enabled is True


async def test_journal_lease_blocks_second_daemon_recovery_during_active_run(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master")
    slave = await _ready_device("slave")
    path = tmp_path / "linkage.json"
    first_store = JsonLinkageJournalStore(path)
    second_store = JsonLinkageJournalStore(path)
    first = _controller(master, slave, first_store)
    second = _controller(master, slave, second_store)
    task = asyncio.create_task(first.run(_spec(duration=5)))
    await _wait_until_active(first, first_store)

    with pytest.raises(LinkageTransactionBusyError, match="journal lease"):
        await second.recover_pending()

    assert (await master.get_state()).linkage is LinkageRole.MASTER
    assert (await slave.get_state()).linkage is LinkageRole.SYNC_SLAVE
    assert await first.stop() is True
    await task
    assert first_store.load() is None


async def test_failed_restore_latches_journal_and_recovery_retries(tmp_path: Path) -> None:
    master = await _ready_device("master")
    slave = await _ready_device("slave", device_class=_FailTimerRestoreDevice)
    slave.fail_timer_restore = True
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkageRollbackError, match="requires recovery"):
        await controller.run(_spec(duration=0.1))

    pending = store.load()
    assert pending is not None
    assert pending.phase is LinkageTransactionPhase.RECOVERY_REQUIRED
    assert pending.recovery_reason is LinkageRecoveryReason.RESTORE_FAILED
    assert pending.failed_device_ids == ("slave",)
    assert pending.restored_device_ids == ("master",)
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).timer_enabled is False
    with pytest.raises(LinkageTransactionBusyError, match="must complete first"):
        await controller.run(_spec(duration=0.1))

    master_command_count = len(master.commands)
    with pytest.raises(LinkageRollbackError, match="requires recovery"):
        await controller.recover_pending(authority=LinkageRecoveryAuthority.ATTENDED)
    assert len(master.commands) == master_command_count
    assert store.load().restored_device_ids == ("master",)

    slave.fail_timer_restore = False
    assert (
        await controller.recover_pending(authority=LinkageRecoveryAuthority.ATTENDED) is True
    )
    assert store.load() is None
    assert (await slave.get_state()).timer_enabled is True


async def test_stale_linkage_recovery_requires_attended_authority(tmp_path: Path) -> None:
    master = await _ready_device("master", timer_enabled=False)
    slave = await _ready_device("slave", timer_enabled=False)
    spec = _spec(duration=5)
    snapshots = (
        DeviceControlSnapshot.from_state(
            "master",
            await master.get_state(),
            physical_binding=master.physical_binding,
        ),
        DeviceControlSnapshot.from_state(
            "slave",
            await slave.get_state(),
            physical_binding=slave.physical_binding,
        ),
    )
    await master.write_target(
        DeviceTarget(
            enabled=True,
            power=40,
            mode="sine",
            frequency=30,
            linkage=LinkageRole.MASTER,
            timer_enabled=False,
        )
    )
    await slave.write_target(
        DeviceTarget(
            enabled=True,
            power=40,
            mode="sine",
            frequency=30,
            linkage=LinkageRole.SYNC_SLAVE,
            timer_enabled=False,
        )
    )
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    now = datetime.now().astimezone()
    store.create(
        LinkageTransactionRecord(
            operation_id=spec.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=spec,
            snapshots=snapshots,
            created_at=now - timedelta(minutes=2),
            updated_at=now - timedelta(minutes=1),
            expires_at=now - timedelta(minutes=1, seconds=30),
        )
    )
    controller = _controller(master, slave, store)
    master.commands.clear()
    slave.commands.clear()
    command_counts = (len(master.commands), len(slave.commands))

    with pytest.raises(LinkagePreflightError, match="attended authority"):
        await controller.recover_pending()

    assert (len(master.commands), len(slave.commands)) == command_counts
    assert (
        await controller.recover_pending(authority=LinkageRecoveryAuthority.ATTENDED) is True
    )
    assert store.load() is None


async def test_backwards_clock_from_updated_record_blocks_automatic_recovery(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", timer_enabled=False)
    slave = await _ready_device("slave", timer_enabled=False)
    spec = _spec(duration=5)
    snapshots = (
        DeviceControlSnapshot.from_state(
            "master", await master.get_state(), physical_binding=master.physical_binding
        ),
        DeviceControlSnapshot.from_state(
            "slave", await slave.get_state(), physical_binding=slave.physical_binding
        ),
    )
    for device, role in (
        (master, LinkageRole.MASTER),
        (slave, LinkageRole.SYNC_SLAVE),
    ):
        await device.write_target(
            DeviceTarget(
                enabled=True,
                power=40,
                mode="sine",
                frequency=30,
                linkage=role,
                timer_enabled=False,
            )
        )
        device.commands.clear()

    now = datetime.now().astimezone()
    store = JsonLinkageJournalStore(tmp_path / "future-update.json")
    store.create(
        LinkageTransactionRecord(
            operation_id=spec.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=spec,
            snapshots=snapshots,
            created_at=now - timedelta(seconds=1),
            updated_at=now + timedelta(minutes=1),
            expires_at=now + timedelta(seconds=5),
        )
    )

    with pytest.raises(LinkagePreflightError, match="attended authority"):
        await _controller(master, slave, store).recover_pending()

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is not None


async def test_schedule_change_keeps_timer_off_and_requires_recovery(tmp_path: Path) -> None:
    master = await _ready_device("master", device_class=_ScheduledDevice)
    slave = await _ready_device("slave", device_class=_ScheduledDevice)
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_until_active(controller, store)

    slave.schedule = DeviceSchedule(
        enabled=False,
        entries=(
            ScheduleEntry(
                slot=0,
                start="08:00",
                end="09:00",
                mode="sine",
                mode_code=1,
                parameters={"flow": 50},
            ),
        ),
    )
    assert await controller.stop() is True

    with pytest.raises(LinkageRollbackError, match="control_restore_failed"):
        await task

    pending = store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.RESTORE_FAILED
    assert pending.failed_device_ids == ("slave",)
    assert (await slave.get_state()).timer_enabled is False

    slave.schedule = DeviceSchedule(enabled=False)
    assert (
        await controller.recover_pending(authority=LinkageRecoveryAuthority.ATTENDED) is True
    )
    assert store.load() is None
    assert (await slave.get_state()).timer_enabled is True


async def test_safety_interlock_keeps_emergency_stop_authoritative(tmp_path: Path) -> None:
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    master = await _ready_device("master", power=47)
    slave = await _ready_device("slave", power=53)
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store, interlock=interlock)
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_until_active(controller, store)

    interlock.trip()
    # Even an immediate clear cannot make the already-running operation reuse stale authority.
    interlock.clear()
    with pytest.raises(LinkageRollbackError, match="safety interlock"):
        await task

    pending = store.load()
    assert pending is not None
    assert pending.phase is LinkageTransactionPhase.RECOVERY_REQUIRED
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.failed_device_ids == ("master", "slave")
    for device in (master, slave):
        state = await device.get_state()
        assert state.enabled is False
        assert state.linkage is LinkageRole.INDEPENDENT
        assert state.timer_enabled is False

    with pytest.raises(LinkagePreflightError, match="attended authority"):
        await controller.recover_pending()

    assert (
        await controller.recover_pending(authority=LinkageRecoveryAuthority.ATTENDED) is True
    )
    assert store.load() is None
    assert (await master.get_state()).power == 47
    assert (await slave.get_state()).power == 53
    assert (await master.get_state()).enabled is True
    assert (await slave.get_state()).enabled is True


async def test_safety_reason_is_durable_before_any_safe_stop_write(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master = await _ready_device("master")
    slave = await _ready_device("slave")
    master.events = events
    slave.events = events
    store = _RecordingStore(tmp_path / "linkage.json", events)
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    controller = _controller(master, slave, store, interlock=interlock)
    task = asyncio.create_task(controller.run(_spec(duration=5)))
    await _wait_until_active(controller, store)
    events.clear()

    interlock.trip()
    with pytest.raises(LinkageRollbackError, match="safety interlock"):
        await task

    recovery_index = events.index("journal:recovery_required")
    safe_stop_indexes = [index for index, event in enumerate(events) if event.startswith("write:")]
    assert safe_stop_indexes
    assert recovery_index < min(safe_stop_indexes)
    pending = store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK


async def test_safety_interlock_is_fail_closed_by_default(tmp_path: Path) -> None:
    master = await _ready_device("master")
    slave = await _ready_device("slave")
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(
        master,
        slave,
        store,
        interlock=LinkageSafetyInterlock(),
    )

    with pytest.raises(LinkagePreflightError, match="safety interlock"):
        await controller.run(_spec())

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


@pytest.mark.parametrize(
    "phase",
    [
        LinkageTransactionPhase.PREPARED,
        LinkageTransactionPhase.APPLYING,
        LinkageTransactionPhase.ACTIVE,
        LinkageTransactionPhase.ROLLING_BACK,
        LinkageTransactionPhase.RECOVERY_REQUIRED,
    ],
)
async def test_startup_recovery_never_resumes_an_unfinished_transaction(
    tmp_path: Path,
    phase: LinkageTransactionPhase,
) -> None:
    master = await _ready_device("master", power=46, frequency=22)
    slave = await _ready_device("slave", power=54, frequency=28)
    spec = _spec(duration=5)
    snapshots = (
        DeviceControlSnapshot.from_state(
            "master",
            await master.get_state(),
            physical_binding=master.physical_binding,
        ),
        DeviceControlSnapshot.from_state(
            "slave",
            await slave.get_state(),
            physical_binding=slave.physical_binding,
        ),
    )
    now = datetime.now().astimezone()
    record = LinkageTransactionRecord(
        operation_id=spec.operation_id,
        phase=phase,
        recovery_reason=(
            LinkageRecoveryReason.RESTORE_FAILED
            if phase is LinkageTransactionPhase.RECOVERY_REQUIRED
            else None
        ),
        spec=spec,
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=5),
    )
    if phase is not LinkageTransactionPhase.PREPARED:
        await master.write_target(
            DeviceTarget(
                enabled=True,
                power=60,
                mode="sine",
                frequency=30,
                linkage=LinkageRole.MASTER,
                timer_enabled=False,
            )
        )
        await slave.write_target(
            DeviceTarget(
                enabled=True,
                power=42,
                mode="sine",
                frequency=30,
                linkage=LinkageRole.SYNC_SLAVE,
                timer_enabled=False,
            )
        )
    store = JsonLinkageJournalStore(tmp_path / f"{phase.value}.json")
    store.save(record)
    master.commands.clear()
    slave.commands.clear()
    restarted = _controller(master, slave, store)

    assert (
        await restarted.recover_pending(
            authority=(
                LinkageRecoveryAuthority.AUTOMATIC
                if phase is LinkageTransactionPhase.PREPARED
                else LinkageRecoveryAuthority.ATTENDED
            )
        )
        is True
    )

    assert store.load() is None
    assert (await master.get_state()).power == 46
    assert (await slave.get_state()).power == 54
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).timer_enabled is True
    if phase is LinkageTransactionPhase.PREPARED:
        assert master.commands == []
        assert slave.commands == []


async def test_preflight_rejects_bar_style_async_without_writes(tmp_path: Path) -> None:
    capabilities = DeviceCapabilities(
        model="bar",
        product_key="bar-simulator",
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
        native_modes=frozenset({"constant", "sine"}),
        linkage_roles=frozenset({LinkageRole.INDEPENDENT, LinkageRole.MASTER, LinkageRole.SLAVE}),
    )
    master = await _ready_device("master", capabilities=capabilities)
    slave = await _ready_device("slave", capabilities=capabilities)
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkagePreflightError, match="async_slave"):
        await controller.run(_spec(role=LinkageRole.ASYNC_SLAVE))

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_preflight_rejects_off_device_without_turning_it_on(tmp_path: Path) -> None:
    master = await _ready_device("master", enabled=False)
    slave = await _ready_device("slave")
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkagePreflightError, match="must already be running"):
        await controller.run(_spec())

    assert master.commands == []
    assert slave.commands == []
    assert (await master.get_state()).enabled is False
    assert store.load() is None


async def test_preflight_requires_known_matching_product_keys(tmp_path: Path) -> None:
    capabilities = DeviceCapabilities(
        model="unidentified",
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
        native_modes=frozenset({"constant", "pulse", "sine"}),
        linkage_roles=frozenset(LinkageRole),
    )
    master = await _ready_device("master", capabilities=capabilities)
    slave = await _ready_device("slave", capabilities=capabilities)
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkagePreflightError, match="known product keys"):
        await controller.run(_spec())

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_preflight_requires_exact_stable_physical_bindings(tmp_path: Path) -> None:
    master = await _ready_device("master")
    slave = await _ready_device("slave")
    master._physical_binding = None
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")

    with pytest.raises(LinkagePreflightError, match="stable physical binding"):
        await _controller(master, slave, store).run(_spec())

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_preflight_rejects_unaudited_current_mode_without_writes(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master")
    slave = await _ready_device("slave")
    await master.set_mode("random")
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkagePreflightError, match="audited restore modes"):
        await controller.run(_spec())

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


@pytest.mark.parametrize(
    ("device_class", "message"),
    [
        (_MissingScheduleDevice, "without a decoded schedule"),
        (_InvalidScheduleDevice, "invalid slots"),
    ],
)
async def test_preflight_requires_valid_schedule_when_timer_is_enabled(
    tmp_path: Path,
    device_class: type[_RecordingDevice],
    message: str,
) -> None:
    master = await _ready_device("master", device_class=device_class)
    slave = await _ready_device("slave")
    master.commands.clear()
    slave.commands.clear()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    controller = _controller(master, slave, store)

    with pytest.raises(LinkagePreflightError, match=message):
        await controller.run(_spec())

    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_recovery_rejects_physical_binding_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master")
    slave = await _ready_device("slave")
    spec = _spec()
    snapshots = (
        DeviceControlSnapshot.from_state(
            "master",
            await master.get_state(),
            physical_binding=master.physical_binding,
        ),
        DeviceControlSnapshot.from_state(
            "slave",
            await slave.get_state(),
            physical_binding=slave.physical_binding,
        ),
    )
    now = datetime.now().astimezone()
    record = LinkageTransactionRecord(
        operation_id=spec.operation_id,
        phase=LinkageTransactionPhase.ACTIVE,
        spec=spec,
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=5),
    )
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    store.save(record)
    slave._physical_binding = _binding("slave-remapped")
    master.commands.clear()
    slave.commands.clear()

    with pytest.raises(LinkagePreflightError, match="physical binding"):
        await _controller(master, slave, store).recover_pending()

    assert master.commands == []
    assert slave.commands == []
    assert store.load() == record


async def test_recovery_detects_and_durably_skips_an_already_exact_device(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", power=46, frequency=22)
    slave = await _ready_device("slave", power=54, frequency=28)
    spec = _spec()
    snapshots = (
        DeviceControlSnapshot.from_state(
            "master",
            await master.get_state(),
            physical_binding=master.physical_binding,
        ),
        DeviceControlSnapshot.from_state(
            "slave",
            await slave.get_state(),
            physical_binding=slave.physical_binding,
        ),
    )
    await slave.write_target(
        DeviceTarget(
            enabled=True,
            power=42,
            mode="sine",
            frequency=30,
            linkage=LinkageRole.SYNC_SLAVE,
            timer_enabled=False,
        )
    )
    now = datetime.now().astimezone()
    record = LinkageTransactionRecord(
        operation_id=spec.operation_id,
        phase=LinkageTransactionPhase.ACTIVE,
        spec=spec,
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=5),
    )
    store = _RecordingStore(tmp_path / "linkage.json")
    store.save(record)
    master.commands.clear()
    slave.commands.clear()

    assert (
        await _controller(master, slave, store).recover_pending(
            authority=LinkageRecoveryAuthority.ATTENDED
        )
        is True
    )

    assert master.commands == []
    assert slave.commands
    assert any(saved.restored_device_ids == ("master",) for saved in store.records)
    assert store.load() is None
    assert (await slave.get_state()).power == 54
    assert (await slave.get_state()).timer_enabled is True


async def test_final_recovery_readback_reopens_a_stale_restored_marker(
    tmp_path: Path,
) -> None:
    master = await _ready_device("master", power=46, frequency=22)
    slave = await _ready_device(
        "slave",
        power=54,
        frequency=28,
        device_class=_DriftPeerOnTimerRestoreDevice,
    )
    slave.peer = master
    spec = _spec()
    snapshots = (
        DeviceControlSnapshot.from_state(
            "master",
            await master.get_state(),
            physical_binding=master.physical_binding,
        ),
        DeviceControlSnapshot.from_state(
            "slave",
            await slave.get_state(),
            physical_binding=slave.physical_binding,
        ),
    )
    await slave.write_target(
        DeviceTarget(
            enabled=True,
            power=42,
            mode="sine",
            frequency=30,
            linkage=LinkageRole.SYNC_SLAVE,
            timer_enabled=False,
        )
    )
    now = datetime.now().astimezone()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    store.save(
        LinkageTransactionRecord(
            operation_id=spec.operation_id,
            phase=LinkageTransactionPhase.RECOVERY_REQUIRED,
            recovery_reason=LinkageRecoveryReason.RESTORE_FAILED,
            spec=spec,
            snapshots=snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=5),
            failed_device_ids=("slave",),
            restored_device_ids=("master",),
        )
    )
    master.commands.clear()
    slave.commands.clear()
    slave.drift_once = True

    with pytest.raises(LinkageRollbackError, match="final_verification_failed"):
        await _controller(master, slave, store).recover_pending(
            authority=LinkageRecoveryAuthority.ATTENDED
        )

    pending = store.load()
    assert pending is not None
    assert pending.restored_device_ids == ("slave",)
    assert pending.failed_device_ids == ("master",)


def test_schedule_fingerprint_ignores_clock_and_timer_state() -> None:
    entry = ScheduleEntry(
        slot=0,
        start="08:00",
        end="09:00",
        mode="sine",
        mode_code=2,
        parameters={"flow": 45},
    )
    first = DeviceSchedule(
        enabled=True,
        device_local_time=datetime(2026, 8, 26, 8, 0),
        entries=(entry,),
    )
    second = DeviceSchedule(
        enabled=False,
        device_local_time=datetime(2026, 8, 26, 8, 1),
        entries=(entry,),
    )
    changed = second.model_copy(
        update={"entries": (entry.model_copy(update={"parameters": {"flow": 50}}),)}
    )

    assert schedule_structure_fingerprint(first) == schedule_structure_fingerprint(second)
    assert schedule_structure_fingerprint(first) != schedule_structure_fingerprint(changed)


def test_json_journal_round_trip_is_private_and_atomic(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    spec = _spec()
    snapshots = tuple(
        {
            "device_id": device_id,
            "physical_binding": _binding(device_id),
            "enabled": True,
            "power": 45,
            "mode": "constant",
            "frequency": 20,
            "linkage": "independent",
            "timer_enabled": True,
        }
        for device_id in ("master", "slave")
    )
    record_data = {
        "operation_id": spec.operation_id,
        "phase": "prepared",
        "spec": spec,
        "snapshots": snapshots,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(seconds=10),
    }
    record = LinkageTransactionRecord.model_validate(record_data)
    path = tmp_path / "nested" / "linkage.json"
    store = JsonLinkageJournalStore(path)

    store.create(record)

    assert store.load() == record
    assert record.version == 2
    journal_text = path.read_text(encoding="utf-8")
    assert "private-vendor-master" not in journal_text
    assert "001122334455" not in journal_text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.glob("*.tmp")) == []
    with pytest.raises(LinkageJournalClaimError, match="already claimed"):
        JsonLinkageJournalStore(path).create(record)
    assert store.load() == record
    store.clear()
    assert store.load() is None


def test_recovery_reason_is_required_only_for_recovery_required_records() -> None:
    now = datetime.now().astimezone()
    spec = _spec()
    snapshots = tuple(
        {
            "device_id": device_id,
            "physical_binding": _binding(device_id),
            "enabled": True,
            "power": 45,
            "mode": "constant",
            "frequency": 20,
            "linkage": "independent",
            "timer_enabled": True,
        }
        for device_id in ("master", "slave")
    )
    common = {
        "operation_id": spec.operation_id,
        "spec": spec,
        "snapshots": snapshots,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(seconds=10),
    }

    with pytest.raises(ValueError, match="recovery_reason is required"):
        LinkageTransactionRecord.model_validate(
            {**common, "phase": LinkageTransactionPhase.RECOVERY_REQUIRED}
        )
    with pytest.raises(ValueError, match="must be None"):
        LinkageTransactionRecord.model_validate(
            {
                **common,
                "phase": LinkageTransactionPhase.ACTIVE,
                "recovery_reason": LinkageRecoveryReason.SAFETY_INTERLOCK,
            }
        )

    recovery = LinkageTransactionRecord.model_validate(
        {
            **common,
            "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
            "recovery_reason": LinkageRecoveryReason.SAFETY_INTERLOCK,
        }
    )
    assert recovery.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert '"recovery_reason":"safety_interlock"' in recovery.model_dump_json()


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "linkage.json"
    path.write_text('{"phase":', encoding="utf-8")
    path.chmod(0o600)
    store = JsonLinkageJournalStore(path)

    with pytest.raises(LinkageJournalError, match="cannot read"):
        store.load()


@pytest.mark.parametrize("unsafe_kind", ["fifo", "hardlink", "mode"])
def test_journal_rejects_unsafe_files_without_blocking(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    path = tmp_path / "linkage.json"
    if unsafe_kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)
        if unsafe_kind == "hardlink":
            os.link(path, tmp_path / "journal-alias")
        else:
            path.chmod(0o640)

    with pytest.raises(LinkageJournalError, match="unsafe metadata"):
        JsonLinkageJournalStore(path).load()


def test_legacy_journal_without_physical_bindings_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "linkage.json"
    now = datetime.now().astimezone()
    spec = _spec()
    legacy = {
        "version": 1,
        "operation_id": spec.operation_id,
        "phase": "active",
        "spec": spec.model_dump(mode="json"),
        "snapshots": [
            {
                "device_id": device_id,
                "enabled": True,
                "power": 45,
                "mode": "constant",
                "frequency": 20,
                "linkage": "independent",
                "timer_enabled": True,
            }
            for device_id in ("master", "slave")
        ],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=10)).isoformat(),
    }
    path.write_text(
        json.dumps(legacy),
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(LinkageJournalError, match="cannot read"):
        JsonLinkageJournalStore(path).load()
