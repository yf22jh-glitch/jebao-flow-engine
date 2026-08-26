import asyncio
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.devices import (
    DeviceControlSnapshot,
    LinkageApplyError,
    LinkageJournalClaimError,
    LinkagePreflightError,
    LinkageRollbackError,
    LinkageSafetyInterlock,
    LinkageStopReason,
    LinkageTestSpec,
    LinkageTransactionBusyError,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
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

    def save(self, record):
        if self.events is not None:
            self.events.append(f"journal:{record.phase.value}")
        super().save(record)

    def create(self, record):
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
        if target.linkage in {
            LinkageRole.SYNC_SLAVE,
            LinkageRole.ASYNC_SLAVE,
        } and not self.failed:
            self.failed = True
            raise RuntimeError("simulated ACK loss after apply")


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
                "schedule": self.schedule.model_copy(
                    update={"enabled": bool(state.timer_enabled)}
                )
            }
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
    device = device_class(device_id) if capabilities is None else device_class(
        device_id,
        capabilities=capabilities,
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
        safety_interlock=interlock
        or LinkageSafetyInterlock(initially_permitted=True),
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
        "schedule": None,
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

    result = await controller.run(_spec(duration=0.01))

    first_write = next(index for index, value in enumerate(events) if value.startswith("write:"))
    assert events.index("journal:prepared") < first_write
    assert events.index("journal:applying") < first_write
    assert result.stop_reason is LinkageStopReason.TIMEOUT
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
    task = asyncio.create_task(
        controller.run(_spec(duration=5, verification_interval=0.005))
    )
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
        await controller.run(_spec(duration=0.01))

    pending = store.load()
    assert pending is not None
    assert pending.phase is LinkageTransactionPhase.RECOVERY_REQUIRED
    assert pending.failed_device_ids == ("slave",)
    assert (await master.get_state()).timer_enabled is True
    assert (await slave.get_state()).timer_enabled is False
    with pytest.raises(LinkageTransactionBusyError, match="must complete first"):
        await controller.run(_spec(duration=0.01))

    slave.fail_timer_restore = False
    assert await controller.recover_pending() is True
    assert store.load() is None
    assert (await slave.get_state()).timer_enabled is True


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

    with pytest.raises(LinkageRollbackError, match="schedule changed"):
        await task

    pending = store.load()
    assert pending is not None
    assert pending.failed_device_ids == ("slave",)
    assert (await slave.get_state()).timer_enabled is False

    slave.schedule = DeviceSchedule(enabled=False)
    assert await controller.recover_pending() is True
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
    assert pending.failed_device_ids == ("master", "slave")
    for device in (master, slave):
        state = await device.get_state()
        assert state.enabled is False
        assert state.linkage is LinkageRole.INDEPENDENT
        assert state.timer_enabled is False

    assert await controller.recover_pending() is True
    assert store.load() is None
    assert (await master.get_state()).power == 47
    assert (await slave.get_state()).power == 53
    assert (await master.get_state()).enabled is True
    assert (await slave.get_state()).enabled is True


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
        DeviceControlSnapshot.from_state("master", await master.get_state()),
        DeviceControlSnapshot.from_state("slave", await slave.get_state()),
    )
    now = datetime.now().astimezone()
    record = LinkageTransactionRecord(
        operation_id=spec.operation_id,
        phase=phase,
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

    assert await restarted.recover_pending() is True

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
        linkage_roles=frozenset(
            {LinkageRole.INDEPENDENT, LinkageRole.MASTER, LinkageRole.SLAVE}
        ),
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
        update={
            "entries": (
                entry.model_copy(update={"parameters": {"flow": 50}}),
            )
        }
    )

    assert schedule_structure_fingerprint(first) == schedule_structure_fingerprint(second)
    assert schedule_structure_fingerprint(first) != schedule_structure_fingerprint(changed)


def test_json_journal_round_trip_is_private_and_atomic(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    spec = _spec()
    snapshots = tuple(
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
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.glob("*.tmp")) == []
    with pytest.raises(LinkageJournalClaimError, match="already claimed"):
        JsonLinkageJournalStore(path).create(record)
    assert store.load() == record
    store.clear()
    assert store.load() is None


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "linkage.json"
    path.write_text('{"phase":', encoding="utf-8")
    store = JsonLinkageJournalStore(path)

    with pytest.raises(LinkageJournalError, match="cannot read"):
        store.load()
