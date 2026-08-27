import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jebao_flow.devices import LinkageSafetyInterlock, SimulatedJebaoDevice
from jebao_flow.devices.schedule_linkage import (
    ScheduleActiveLinkageController,
    ScheduleLinkageApplyError,
    ScheduleLinkageDriftDimension,
    ScheduleLinkagePhase,
    ScheduleLinkagePreflightError,
    ScheduleLinkageRecord,
    ScheduleLinkageRollbackError,
    ScheduleLinkageRunFailure,
    ScheduleLinkageRunProgressKind,
    ScheduleLinkageSpec,
    ScheduleLinkageStopReason,
    schedule_linkage_confirmation_token,
    schedule_linkage_run_progress_rank,
)
from jebao_flow.persistence.schedule_linkage import (
    JsonScheduleLinkageJournalStore,
    ScheduleLinkageJournalError,
)
from jebao_flow.protocol.models import DeviceSchedule, DeviceTarget, LinkageRole, ScheduleEntry


def _entry(
    slot: int,
    start: str,
    end: str,
    mode: str,
    *,
    flow: int,
    frequency: int,
    feed_time: int = 15,
) -> ScheduleEntry:
    return ScheduleEntry(
        slot=slot,
        start=start,
        end=end,
        mode=mode,
        mode_code={
            "pulse": 0,
            "sine": 1,
            "constant": 2,
            "random": 3,
            "feed": 7,
        }.get(mode, 99),
        parameters={
            "flow": flow,
            "frequency": frequency,
            "feed_time": feed_time,
            "custom_frequency": 0,
        },
    )


class _ScheduleDevice(SimulatedJebaoDevice):
    def __init__(
        self,
        device_id: str,
        *,
        constant_flow: int,
        sine_flow: int,
        sine_frequency: int,
        clock: datetime | None = None,
        virtual_time: "_VirtualTime",
        events: list[str] | None = None,
    ) -> None:
        super().__init__(device_id)
        self.constant_flow = constant_flow
        self.sine_flow = sine_flow
        self.sine_frequency = sine_frequency
        self.feed_flow = 30
        self.feed_frequency = 5
        self.alternate_constant_frequency = False
        self.advance_monotonic_after_read_seconds = 0.0
        self._advanced_monotonic_after_read = False
        self.second_sine_boundary: datetime | None = None
        self.next_sine_flow = sine_flow
        self.next_sine_frequency = sine_frequency
        self.observed_sine_mode = "sine"
        self.base_clock = clock or datetime(2026, 8, 26, 18, 9)
        self.virtual_time = virtual_time
        self.clock_offset_seconds = 0.0
        self.events = events
        self.calls: list[tuple[str, object]] = []
        self.session_connect_count = 0
        self.session_disconnect_count = 0
        self.pause_before_connect_numbers: set[int] = set()
        self.connect_paused = asyncio.Event()
        self.resume_connect = asyncio.Event()
        self.fail_after_connect_numbers: set[int] = set()
        self.fail_after_apply_roles: set[LinkageRole] = set()
        self.fail_before_roles: set[LinkageRole] = set()
        self._failed_after: set[LinkageRole] = set()
        self._failed_before: set[LinkageRole] = set()
        self.entries: tuple[ScheduleEntry, ...] = (
            # The real Pro schedule has a harmless distant 23:59 -> 00:00 one-minute gap.
            _entry(0, "00:00", "17:55", "random", flow=40, frequency=20),
            _entry(1, "17:55", "18:10", "feed", flow=0, frequency=0),
            # Constant's encoded frequency is zero, while live AutoFreq is the stable default 5.
            _entry(2, "18:10", "18:11", "constant", flow=constant_flow, frequency=0),
            _entry(
                3,
                "18:11",
                "23:59",
                "sine",
                flow=sine_flow,
                frequency=sine_frequency,
            ),
        )

    async def connect(self) -> None:
        connect_number = self.session_connect_count + 1
        if connect_number in self.pause_before_connect_numbers:
            self.connect_paused.set()
            await self.resume_connect.wait()
        await super().connect()
        self.session_connect_count += 1
        if self.events is not None:
            self.events.append(f"session:{self.device_id}:connect")
        if self.session_connect_count in self.fail_after_connect_numbers:
            raise RuntimeError("simulated session refresh failure after connect")

    async def disconnect(self) -> None:
        self.session_disconnect_count += 1
        if self.events is not None:
            self.events.append(f"session:{self.device_id}:disconnect")
        await super().disconnect()

    async def get_state(self):
        state = await super().get_state()
        self.clock = self.base_clock + timedelta(
            seconds=self.virtual_time.value + self.clock_offset_seconds
        )
        wall = self.clock.time()
        if wall < datetime(2026, 8, 26, 18, 10).time():
            observed = {
                "AutoMode": "feed",
                "AutoFlow": self.feed_flow,
                "AutoFreq": self.feed_frequency,
                "AutoFeedTime": 15,
            }
        elif wall < datetime(2026, 8, 26, 18, 11).time():
            constant_frequency = (
                5 + int(self.virtual_time.value // 10) % 2
                if self.alternate_constant_frequency
                else 5
            )
            observed = {
                "AutoMode": "constant",
                "AutoFlow": self.constant_flow,
                "AutoFreq": constant_frequency,
                "AutoFeedTime": 15,
            }
        else:
            use_next_sine = (
                self.second_sine_boundary is not None
                and self.clock >= self.second_sine_boundary
            )
            observed = {
                "AutoMode": self.observed_sine_mode,
                "AutoFlow": self.next_sine_flow if use_next_sine else self.sine_flow,
                "AutoFreq": (
                    self.next_sine_frequency if use_next_sine else self.sine_frequency
                ),
                "AutoFeedTime": 15,
            }
        result = state.model_copy(
            update={
                "schedule": DeviceSchedule(
                    enabled=state.timer_enabled is True,
                    device_local_time=self.clock,
                    entries=self.entries,
                ),
                "observed_attributes": observed,
            }
        )
        if (
            wall >= datetime(2026, 8, 26, 18, 10).time()
            and self.advance_monotonic_after_read_seconds
            and not self._advanced_monotonic_after_read
            and state.linkage in {LinkageRole.MASTER, LinkageRole.ASYNC_SLAVE}
        ):
            self._advanced_monotonic_after_read = True
            self.virtual_time.value += self.advance_monotonic_after_read_seconds
        return result

    async def set_enabled(self, enabled: bool) -> None:
        self.calls.append(("enabled", enabled))
        await super().set_enabled(enabled)

    async def set_power(self, power: int) -> None:
        self.calls.append(("power", power))
        await super().set_power(power)

    async def set_mode(self, mode: str) -> None:
        self.calls.append(("mode", mode))
        await super().set_mode(mode)

    async def set_frequency(self, value: int) -> None:
        self.calls.append(("frequency", value))
        await super().set_frequency(value)

    async def set_timer_enabled(self, enabled: bool) -> None:
        self.calls.append(("timer_enabled", enabled))
        await super().set_timer_enabled(enabled)

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.calls.append(("write_target", target))
        await super().write_target(target, guard=guard)

    async def write_linkage(self, role: LinkageRole, *, guard=None) -> None:
        self.calls.append(("write_linkage", role))
        if role in self.fail_before_roles and role not in self._failed_before:
            self._failed_before.add(role)
            raise RuntimeError("simulated linkage failure before apply")
        await super().write_linkage(role, guard=guard)
        if self.events is not None:
            self.events.append(f"write:{self.device_id}:{role.value}")
        if role in self.fail_after_apply_roles and role not in self._failed_after:
            self._failed_after.add(role)
            raise RuntimeError("simulated linkage ACK loss after apply")


class _RecordingStore(JsonScheduleLinkageJournalStore):
    def __init__(self, path: Path, events: list[str] | None = None) -> None:
        super().__init__(path)
        self.events = events
        self.records: list[ScheduleLinkageRecord] = []

    def create(self, record: ScheduleLinkageRecord) -> None:
        self.records.append(record)
        if self.events is not None:
            self.events.append("journal:prepared:intents=")
        super().create(record)

    def save(self, record: ScheduleLinkageRecord) -> None:
        self.records.append(record)
        if self.events is not None:
            intents = ",".join(record.linkage_write_intent_device_ids)
            self.events.append(f"journal:{record.phase.value}:intents={intents}")
        super().save(record)


class _VirtualTime:
    def __init__(self, advance_per_sleep: float = 20) -> None:
        self.value = 0.0
        self.advance_per_sleep = advance_per_sleep
        self.sleep_count = 0

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, _seconds: float) -> None:
        self.sleep_count += 1
        await asyncio.sleep(0)
        self.value += self.advance_per_sleep


async def _ready_pair(
    *,
    clock: datetime | None = None,
    linked_clock_step_seconds: float = 10,
    events: list[str] | None = None,
) -> tuple[_ScheduleDevice, _ScheduleDevice]:
    virtual_time = _VirtualTime(linked_clock_step_seconds)
    master = _ScheduleDevice(
        "master",
        constant_flow=30,
        sine_flow=45,
        sine_frequency=40,
        clock=clock,
        virtual_time=virtual_time,
        events=events,
    )
    slave = _ScheduleDevice(
        "slave",
        constant_flow=35,
        sine_flow=45,
        sine_frequency=80,
        clock=clock,
        virtual_time=virtual_time,
        events=events,
    )
    for device in (master, slave):
        await device.connect()
        await device.set_enabled(True)
        await device.set_power(40)
        await device.set_mode("constant")
        await device.set_frequency(5)
        await device.set_linkage(LinkageRole.INDEPENDENT)
        await device.set_timer_enabled(True)
        device.calls.clear()
        device.commands.clear()
        device.session_connect_count = 0
        device.session_disconnect_count = 0
    if events is not None:
        events.clear()
    return master, slave


def _spec(**updates: object) -> ScheduleLinkageSpec:
    values: dict[str, object] = {
        "operation_id": "schedule_boundary_test",
        "qualification_operation_id": "qualified_async_test",
        "master_device_id": "master",
        "slave_device_id": "slave",
        "observation_window_seconds": 130,
        "verification_interval_seconds": 10,
        "minimum_lead_seconds": 45,
        "ambiguous_band_seconds": 0.1,
    }
    values.update(updates)
    return ScheduleLinkageSpec(**values)


def _controller(
    master: _ScheduleDevice,
    slave: _ScheduleDevice,
    store: JsonScheduleLinkageJournalStore,
    *,
    authorizer=None,
    sample_observer=None,
    progress_observer=None,
    refresh_sessions_before_critical_reads: bool = False,
) -> ScheduleActiveLinkageController:
    return ScheduleActiveLinkageController(
        {"master": master, "slave": slave},
        store,
        prerequisite_authorizer=authorizer or (lambda _spec, _snapshots: None),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        monotonic_clock=master.virtual_time.monotonic,
        sleep=master.virtual_time.sleep,
        sample_observer=sample_observer,
        progress_observer=progress_observer,
        refresh_sessions_before_critical_reads=refresh_sessions_before_critical_reads,
    )


def _assert_only_linkage_calls(*devices: _ScheduleDevice) -> None:
    assert all(call[0] == "write_linkage" for device in devices for call in device.calls)
    assert all(command.name == "linkage" for device in devices for command in device.commands)


async def _wait_for_phase(
    store: JsonScheduleLinkageJournalStore,
    phase: ScheduleLinkagePhase,
) -> None:
    for _ in range(1000):
        record = store.load()
        if record is not None and record.phase is phase:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"schedule-linkage did not reach {phase}")


async def _wait_for_monitor_sleep(virtual_time: _VirtualTime) -> None:
    for _ in range(1000):
        if virtual_time.sleep_count:
            return
        await asyncio.sleep(0)
    raise AssertionError("schedule-linkage monitor did not start sampling")


def test_run_progress_rank_follows_allowlisted_declaration_order() -> None:
    assert [
        schedule_linkage_run_progress_rank(kind)
        for kind in ScheduleLinkageRunProgressKind
    ] == list(range(len(ScheduleLinkageRunProgressKind)))


async def test_feed_to_constant_uses_effective_defaults_and_distant_gap_is_allowed(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    calls: list[str] = []

    def authorize(spec, snapshots) -> None:
        assert spec.qualification_operation_id == "qualified_async_test"
        assert {snapshot.device_id for snapshot in snapshots} == {"master", "slave"}
        calls.append("authorized")

    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "schedule.json"),
        authorizer=authorize,
    )

    preflight = await controller.preflight(_spec())

    assert calls == ["authorized"]
    assert master.calls == []
    assert slave.calls == []
    assert preflight.confirmation_token == schedule_linkage_confirmation_token(
        preflight.spec, preflight.snapshots
    )
    master_snapshot, slave_snapshot = preflight.snapshots
    assert master_snapshot.expectation.before.model_dump() == {
        "mode": "feed",
        "flow": 30,
        "frequency": 5,
        "feed_time": 15,
    }
    assert master_snapshot.expectation.after_flow == 30
    assert slave_snapshot.expectation.after_flow == 35
    assert master_snapshot.expectation.after_frequency is None
    assert slave_snapshot.expectation.after_frequency is None
    assert master_snapshot.expectation.boundary_at == datetime(2026, 8, 26, 18, 10)
    assert master_snapshot.expectation.after_valid_until == datetime(2026, 8, 26, 18, 11)


async def test_constant_default_frequency_and_next_sine_tuple_are_mode_aware(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(clock=datetime(2026, 8, 26, 18, 10, 20))
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "schedule.json"),
    )

    preflight = await controller.preflight(
        _spec(minimum_lead_seconds=10, observation_window_seconds=100)
    )

    master_snapshot, slave_snapshot = preflight.snapshots
    # Decoded constant frequency=0 is deliberately not compared with effective AutoFreq=5.
    assert master_snapshot.expectation.before.frequency == 5
    assert master_snapshot.expectation.after_frequency == 40
    assert slave_snapshot.expectation.after_frequency == 80
    assert (master_snapshot.expectation.after_flow, slave_snapshot.expectation.after_flow) == (
        45,
        45,
    )


async def test_sine_to_sine_boundary_binds_overnight_next_slot(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(
        clock=datetime(2026, 8, 26, 18, 11, 30),
        linked_clock_step_seconds=1,
    )
    boundary = datetime(2026, 8, 26, 18, 12)
    for device, next_flow, next_frequency in (
        (master, 42, 45),
        (slave, 44, 85),
    ):
        device.second_sine_boundary = boundary
        device.next_sine_flow = next_flow
        device.next_sine_frequency = next_frequency
        device.entries = (
            _entry(0, "02:00", "18:11", "constant", flow=40, frequency=0),
            _entry(
                1,
                "18:11",
                "18:12",
                "sine",
                flow=device.sine_flow,
                frequency=device.sine_frequency,
            ),
            _entry(
                2,
                "18:12",
                "02:00",
                "sine",
                flow=next_flow,
                frequency=next_frequency,
            ),
        )
    store = JsonScheduleLinkageJournalStore(tmp_path / "sine-overnight.json")
    controller = _controller(master, slave, store)
    spec = _spec(
        minimum_lead_seconds=10,
        observation_window_seconds=60,
        verification_interval_seconds=1,
    )

    preflight = await controller.preflight(spec)
    assert preflight.snapshots[0].expectation.before.model_dump() == {
        "mode": "sine",
        "flow": 45,
        "frequency": 40,
        "feed_time": None,
    }
    assert preflight.snapshots[1].expectation.after_flow == 44
    assert preflight.snapshots[1].expectation.after_frequency == 85
    assert preflight.snapshots[0].expectation.after_valid_until == datetime(
        2026, 8, 27, 2
    )

    result = await controller.run(preflight)

    assert result.schedule_transition_verified is True
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_normal_run_persists_intent_before_each_write_and_detaches_slave_first(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_pair(events=events)
    store = _RecordingStore(tmp_path / "schedule.json", events)
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    result = await controller.run(preflight)

    assert result.stop_reason is ScheduleLinkageStopReason.BOUNDARY_VERIFIED
    assert result.schedule_transition_verified is True
    assert store.load() is None
    master_intent = events.index("journal:applying:intents=master")
    master_write = events.index("write:master:master")
    slave_intent = events.index("journal:applying:intents=master,slave")
    slave_write = events.index("write:slave:async_slave")
    assert master_intent < master_write < slave_intent < slave_write
    assert events.index("write:slave:independent") < events.index(
        "write:master:independent"
    )
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    _assert_only_linkage_calls(master, slave)
    for device in (master, slave):
        state = await device.get_state()
        assert state.timer_enabled is True
        assert state.linkage is LinkageRole.INDEPENDENT
        assert (state.power, state.mode, state.frequency) == (40, "constant", 5)
    assert any(
        record.linkage_write_intent_device_ids == ("master", "slave")
        for record in store.records
    )


async def test_sample_observer_receives_effective_before_and_after_slave_flow(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    samples = []
    topology_ready: list[bool] = []

    def observe(sample) -> None:
        # A durable sample claims master/async-slave topology, so it must never be emitted after
        # only the first (master) role write has completed.
        topology_ready.append(
            ("write_linkage", LinkageRole.MASTER) in master.calls
            and ("write_linkage", LinkageRole.ASYNC_SLAVE) in slave.calls
        )
        samples.append(sample)

    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "sample-evidence.json"),
        sample_observer=observe,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert {sample.phase for sample in samples} == {"before", "after"}
    after = next(sample for sample in reversed(samples) if sample.phase == "after")
    assert (after.master.mode, after.master.flow) == ("constant", 30)
    assert (after.slave.mode, after.slave.flow) == ("constant", 35)
    assert after.master_linkage is LinkageRole.MASTER
    assert after.slave_linkage is LinkageRole.ASYNC_SLAVE
    assert topology_ready and all(topology_ready)
    _assert_only_linkage_calls(master, slave)


async def test_sample_observer_failure_is_best_effort_and_does_not_change_role_run(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()

    def fail_sample_sink(_sample) -> None:
        raise RuntimeError("simulated evidence sink failure")

    store = JsonScheduleLinkageJournalStore(tmp_path / "failed-sample-sink.json")
    controller = _controller(
        master,
        slave,
        store,
        sample_observer=fail_sample_sink,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_run_progress_reports_precise_identity_free_milestones(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    progress = []
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "run-progress.json"),
        progress_observer=progress.append,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert [event.kind for event in progress] == [
        ScheduleLinkageRunProgressKind.FRESH_CAPTURE_STARTED,
        ScheduleLinkageRunProgressKind.FRESH_CAPTURE_COMPLETED,
        ScheduleLinkageRunProgressKind.AUTHORIZATION_STARTED,
        ScheduleLinkageRunProgressKind.AUTHORIZATION_COMPLETED,
        ScheduleLinkageRunProgressKind.CONFIRMATION_STARTED,
        ScheduleLinkageRunProgressKind.CONFIRMATION_VERIFIED,
        ScheduleLinkageRunProgressKind.JOURNAL_STARTED,
        ScheduleLinkageRunProgressKind.JOURNAL_CREATED,
        ScheduleLinkageRunProgressKind.FIRST_WRITE_GATE_STARTED,
        ScheduleLinkageRunProgressKind.FIRST_WRITE_GATE_VERIFIED,
        ScheduleLinkageRunProgressKind.MASTER_INTENT_STARTED,
        ScheduleLinkageRunProgressKind.MASTER_INTENT_PERSISTED,
        ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_STARTED,
        ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_COMPLETED,
        ScheduleLinkageRunProgressKind.MASTER_PAIR_VERIFICATION_STARTED,
        ScheduleLinkageRunProgressKind.MASTER_PAIR_VERIFIED,
        ScheduleLinkageRunProgressKind.SLAVE_INTENT_STARTED,
        ScheduleLinkageRunProgressKind.SLAVE_INTENT_PERSISTED,
        ScheduleLinkageRunProgressKind.SLAVE_ADAPTER_WRITE_STARTED,
        ScheduleLinkageRunProgressKind.SLAVE_ADAPTER_WRITE_COMPLETED,
        ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED,
        ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED,
        ScheduleLinkageRunProgressKind.MONITOR_STARTED,
        ScheduleLinkageRunProgressKind.MONITOR_COMPLETED,
    ]
    assert all(event.failure is None for event in progress)
    assert all(event.drift_dimensions == () for event in progress)
    assert all(
        set(event.model_dump(mode="json"))
        == {"kind", "occurred_at", "failure", "drift_dimensions"}
        for event in progress
    )


async def test_confirmation_mismatch_reports_only_allowlisted_drift_and_writes_nothing(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "confirmation-drift.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
    )
    preflight = await controller.preflight(_spec())
    await master.set_frequency(6)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()

    with pytest.raises(ScheduleLinkagePreflightError, match="no role write was sent"):
        await controller.run(preflight)

    failed = progress[-1]
    assert failed.kind is ScheduleLinkageRunProgressKind.FAILED
    assert failed.failure is ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH
    assert failed.drift_dimensions == (ScheduleLinkageDriftDimension.FREQUENCY,)
    assert set(failed.model_dump(mode="json")) == {
        "kind",
        "occurred_at",
        "failure",
        "drift_dimensions",
    }
    assert "device" not in failed.model_dump_json()
    assert "error" not in failed.model_dump_json()
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_constant_auto_frequency_drift_is_precise_and_sends_no_role_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(clock=datetime(2026, 8, 26, 18, 10))
    master.alternate_constant_frequency = True
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "constant-auto-frequency.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
    )
    preflight = await controller.preflight(_spec())
    master.virtual_time.value = 10

    with pytest.raises(ScheduleLinkagePreflightError, match="no role write was sent"):
        await controller.run(preflight)

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH
    assert failed.drift_dimensions == (
        ScheduleLinkageDriftDimension.BEFORE_AUTO_FREQUENCY,
    )
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_confirmation_token_mismatch_is_classified_without_values_or_writes(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "confirmation-token.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
    )
    preflight = await controller.preflight(_spec())
    preflight = preflight.model_copy(update={"confirmation_token": "0" * 64})

    with pytest.raises(ScheduleLinkagePreflightError, match="no role write was sent"):
        await controller.run(preflight)

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH
    assert failed.drift_dimensions == (
        ScheduleLinkageDriftDimension.CONFIRMATION_TOKEN,
    )
    assert master.calls == []
    assert slave.calls == []
    assert store.load() is None


async def test_progress_observer_failure_never_changes_forward_or_rollback(
    tmp_path: Path,
) -> None:
    def fail_progress_sink(_event) -> None:
        raise RuntimeError("simulated progress sink failure")

    master, slave = await _ready_pair()
    success_store = JsonScheduleLinkageJournalStore(tmp_path / "progress-forward.json")
    success_controller = _controller(
        master,
        slave,
        success_store,
        progress_observer=fail_progress_sink,
    )

    result = await success_controller.run(await success_controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert success_store.load() is None
    _assert_only_linkage_calls(master, slave)

    master, slave = await _ready_pair()
    master.fail_after_apply_roles.add(LinkageRole.MASTER)
    failure_store = JsonScheduleLinkageJournalStore(tmp_path / "progress-rollback.json")
    failure_controller = _controller(
        master,
        slave,
        failure_store,
        progress_observer=fail_progress_sink,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await failure_controller.run(await failure_controller.preflight(_spec()))

    assert failure_store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_opt_in_session_refresh_precedes_each_critical_read_but_not_monitor(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_pair(events=events)
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "session-refresh.json"),
        refresh_sessions_before_critical_reads=True,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert master.session_connect_count == master.session_disconnect_count == 5
    assert slave.session_connect_count == slave.session_disconnect_count == 5
    master_write = events.index("write:master:master")
    slave_write = events.index("write:slave:async_slave")
    for device_id in ("master", "slave"):
        disconnect = f"session:{device_id}:disconnect"
        connect = f"session:{device_id}:connect"
        assert events[:master_write].count(disconnect) == 2
        assert events[:master_write].count(connect) == 2
        assert events[master_write + 1 : slave_write].count(disconnect) == 1
        assert events[master_write + 1 : slave_write].count(connect) == 1
        assert events[slave_write + 1 :].count(disconnect) == 2
        assert events[slave_write + 1 :].count(connect) == 2
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_initial_session_refresh_failure_is_no_write_and_allowlisted(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    master.fail_after_connect_numbers.add(1)
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "initial-refresh-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
    )
    preflight = await controller.preflight(_spec())

    with pytest.raises(RuntimeError, match="session refresh failure"):
        await controller.run(preflight)

    assert progress[-1].kind is ScheduleLinkageRunProgressKind.FAILED
    assert progress[-1].failure is ScheduleLinkageRunFailure.FRESH_CAPTURE
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


async def test_post_write_session_refresh_failure_detaches_from_durable_intent(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    # Initial fresh capture and first-write gate consume refreshes one and two.  Refresh three
    # follows the master adapter write and precedes its pair verification.
    master.fail_after_connect_numbers.add(3)
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "post-write-refresh-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
    )
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert progress[-1].kind is ScheduleLinkageRunProgressKind.FAILED
    assert progress[-1].failure is ScheduleLinkageRunFailure.MASTER_PAIR_VERIFICATION
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert slave.calls == []


async def test_cancel_during_post_master_refresh_reconnects_before_rollback(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_pair(events=events)
    # Refreshes one and two precede all writes. Pause master reconnect three after its
    # durable MASTER intent and adapter write, while the paired slave reconnect can finish.
    master.pause_before_connect_numbers.add(3)
    store = JsonScheduleLinkageJournalStore(tmp_path / "cancel-post-master-refresh.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
    )
    task = asyncio.create_task(controller.run(await controller.preflight(_spec())))
    await asyncio.wait_for(master.connect_paused.wait(), timeout=1)

    assert [call[1] for call in master.calls] == [LinkageRole.MASTER]
    assert slave.calls == []
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    master.resume_connect.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.load() is None
    assert master.session_connect_count == master.session_disconnect_count == 4
    assert slave.session_connect_count == slave.session_disconnect_count == 4
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert slave.calls == []
    assert "write:slave:async_slave" not in events
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_opt_in_slave_tuple_variance_is_observed_for_the_full_stability_window(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(
        clock=datetime(2026, 8, 26, 18, 10, 20),
        linked_clock_step_seconds=1,
    )
    # The staged schedule still declares Sine 45%, but the simulated slave remains on its prior
    # Constant 35% tuple. This is one firmware behavior the field experiment must preserve.
    slave.observed_sine_mode = "constant"
    slave.sine_flow = 35
    slave.sine_frequency = 5
    samples = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-tuple-variance.json")
    controller = _controller(
        master,
        slave,
        store,
        sample_observer=samples.append,
    )
    preflight = await controller.preflight(
        _spec(
            observation_window_seconds=130,
            verification_interval_seconds=1,
            minimum_lead_seconds=10,
            post_boundary_stability_seconds=30,
            observe_slave_after_tuple_variance=True,
        )
    )

    result = await controller.run(preflight)

    assert result.schedule_transition_verified is True
    assert master.virtual_time.value >= 70
    after = next(sample for sample in reversed(samples) if sample.phase == "after")
    assert (after.master.mode, after.master.flow) == ("sine", 45)
    assert (after.slave.mode, after.slave.flow, after.slave.frequency) == (
        "constant",
        35,
        5,
    )
    assert store.load() is None


async def test_post_boundary_stability_window_keeps_roles_linked_until_evidence_is_stable(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(
        clock=datetime(2026, 8, 26, 18, 10, 20),
        linked_clock_step_seconds=1,
    )
    store = JsonScheduleLinkageJournalStore(tmp_path / "stable-boundary.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(
        _spec(
            observation_window_seconds=130,
            verification_interval_seconds=1,
            minimum_lead_seconds=10,
            post_boundary_stability_seconds=30,
        )
    )

    result = await controller.run(preflight)

    assert result.schedule_transition_verified is True
    # A 40-second lead plus the requested 30 stable seconds cannot complete before t=70.
    assert master.virtual_time.value >= 70
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    assert store.load() is None


def test_post_boundary_stability_must_fit_inside_observation_window() -> None:
    with pytest.raises(ValidationError, match="observation window"):
        _spec(
            observation_window_seconds=60,
            verification_interval_seconds=1,
            minimum_lead_seconds=10,
            post_boundary_stability_seconds=58,
        )


async def test_externally_disarmed_roles_can_close_the_exact_journal_without_a_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "external-disarm.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.RECOVERY_REQUIRED,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=preflight.spec.observation_window_seconds),
        linkage_write_intent_device_ids=("master", "slave"),
        linked_device_ids=("master", "slave"),
        error="simulated prior detach uncertainty",
    )
    with store.lease():
        store.create(record)
    for device in (slave, master):
        await device.write_target(
            DeviceTarget(
                enabled=True,
                power=40,
                mode="constant",
                frequency=5,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            )
        )
        device.calls.clear()
        device.commands.clear()

    assert await controller.finalize_externally_disarmed(record.operation_id) is True

    assert store.load() is None
    assert master.calls == []
    assert slave.calls == []


@pytest.mark.parametrize(
    ("device_id", "boundary_side"),
    (
        ("master", "current"),
        ("slave", "current"),
        ("master", "next"),
        ("slave", "next"),
    ),
)
async def test_preflight_rejects_schedule_flow_above_guarded_maximum_without_writes(
    tmp_path: Path,
    device_id: str,
    boundary_side: str,
) -> None:
    master, slave = await _ready_pair()
    device = master if device_id == "master" else slave
    if boundary_side == "current":
        device.feed_flow = 46
    else:
        device.constant_flow = 46
        device.entries = tuple(
            entry.model_copy(update={"parameters": {**entry.parameters, "flow": 46}})
            if entry.slot == 2
            else entry
            for entry in device.entries
        )
    store = JsonScheduleLinkageJournalStore(tmp_path / f"{device_id}-{boundary_side}.json")
    controller = _controller(master, slave, store)

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match=rf"{device_id!r} {boundary_side} AutoFlow exceeds .* maximum of 45",
    ):
        await controller.preflight(_spec())

    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


@pytest.mark.parametrize("device_id", ("master", "slave"))
async def test_preflight_rejects_high_manual_fallback_power_without_writes(
    tmp_path: Path,
    device_id: str,
) -> None:
    master, slave = await _ready_pair()
    device = master if device_id == "master" else slave
    await device.set_power(46)
    device.calls.clear()
    device.commands.clear()
    authorizations: list[str] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / f"{device_id}-fallback.json")
    controller = _controller(
        master,
        slave,
        store,
        authorizer=lambda _spec, _snapshots: authorizations.append("authorized"),
    )

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match=rf"{device_id!r} manual fallback Flow exceeds .* maximum of 45",
    ):
        await controller.preflight(_spec())

    assert authorizations == []
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


@pytest.mark.parametrize("device_id", ("master", "slave"))
async def test_fresh_capture_rejects_high_manual_fallback_power_without_writes(
    tmp_path: Path,
    device_id: str,
) -> None:
    master, slave = await _ready_pair()
    authorizations: list[str] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / f"{device_id}-fresh-fallback.json")
    controller = _controller(
        master,
        slave,
        store,
        authorizer=lambda _spec, _snapshots: authorizations.append("authorized"),
    )
    preflight = await controller.preflight(_spec())
    device = master if device_id == "master" else slave
    await device.set_power(46)
    device.calls.clear()
    device.commands.clear()

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match=rf"{device_id!r} manual fallback Flow exceeds .* maximum of 45",
    ):
        await controller.run(preflight)

    assert authorizations == ["authorized"]
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert store.load() is None


class _AdvanceClockOnApplyingStore(_RecordingStore):
    def __init__(self, path: Path, virtual_time: _VirtualTime) -> None:
        super().__init__(path)
        self.virtual_time = virtual_time

    def save(self, record: ScheduleLinkageRecord) -> None:
        super().save(record)
        if record.phase is ScheduleLinkagePhase.APPLYING:
            self.virtual_time.value += 20


async def test_first_write_gate_rechecks_lead_after_journal_without_role_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = _AdvanceClockOnApplyingStore(tmp_path / "gate-race.json", master.virtual_time)
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert store.load() is None
    assert master.calls == []
    assert slave.calls == []


@pytest.mark.parametrize(
    ("failing_device_id", "failing_role"),
    [
        ("master", LinkageRole.MASTER),
        ("slave", LinkageRole.ASYNC_SLAVE),
    ],
)
async def test_ack_loss_after_apply_reloads_latest_intent_and_detaches(
    tmp_path: Path,
    failing_device_id: str,
    failing_role: LinkageRole,
) -> None:
    events: list[str] = []
    master, slave = await _ready_pair(events=events)
    failing = master if failing_device_id == "master" else slave
    failing.fail_after_apply_roles.add(failing_role)
    store = _RecordingStore(tmp_path / f"{failing_device_id}.json", events)
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    assert ("write_linkage", LinkageRole.INDEPENDENT) in failing.calls
    if failing_device_id == "slave":
        assert events.index("write:slave:independent") < events.index(
            "write:master:independent"
        )
    else:
        assert slave.calls == []
    _assert_only_linkage_calls(master, slave)


async def test_bad_after_tuple_fails_restored_without_non_linkage_writes(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    # The decoded slave next slot remains 35, but the effective post-boundary read lies.
    slave.constant_flow = 36
    # Preserve the decoded value so preflight still binds 35 and the live mismatch appears only
    # after the role setup reaches the boundary.
    slave.entries = tuple(
        entry.model_copy(update={"parameters": {**entry.parameters, "flow": 35}})
        if entry.slot == 2
        else entry
        for entry in slave.entries
    )
    # Preflight would see the current feed, so the inconsistency is intentionally latent.
    store = JsonScheduleLinkageJournalStore(tmp_path / "bad-after.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert store.load() is None
    _assert_only_linkage_calls(master, slave)
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_constant_effective_frequency_needs_two_consecutive_fresh_samples(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    slave.alternate_constant_frequency = True
    store = JsonScheduleLinkageJournalStore(tmp_path / "unstable-default.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached") as raised:
        await controller.run(preflight)

    assert "two consecutive fresh samples" in str(raised.value.__cause__)
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_read_completing_after_hard_deadline_cannot_become_success(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    slave.advance_monotonic_after_read_seconds = 100
    store = JsonScheduleLinkageJournalStore(tmp_path / "post-read-deadline.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached") as raised:
        await controller.run(preflight)

    assert "observation deadline expired" in str(raised.value.__cause__)
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_cancellation_is_shielded_until_role_only_detach(tmp_path: Path) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=0)
    store = JsonScheduleLinkageJournalStore(tmp_path / "cancel.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    task = asyncio.create_task(controller.run(preflight))
    await _wait_for_phase(store, ScheduleLinkagePhase.ACTIVE)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.load() is None
    _assert_only_linkage_calls(master, slave)
    assert [call[1] for call in slave.calls][-1] is LinkageRole.INDEPENDENT
    assert [call[1] for call in master.calls][-1] is LinkageRole.INDEPENDENT


async def test_crash_recovery_detaches_slave_then_master_with_linkage_only(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_pair(events=events)
    store = _RecordingStore(tmp_path / "recover.json", events)
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    await master.write_linkage(LinkageRole.MASTER)
    await slave.write_linkage(LinkageRole.ASYNC_SLAVE)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    events.clear()
    now = datetime.now(UTC)
    with store.lease():
        store.create(
            ScheduleLinkageRecord(
                operation_id=preflight.spec.operation_id,
                phase=ScheduleLinkagePhase.ACTIVE,
                spec=preflight.spec,
                snapshots=preflight.snapshots,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(minutes=2),
                linkage_write_intent_device_ids=("master", "slave"),
                linked_device_ids=("master", "slave"),
            )
        )
    events.clear()

    assert await controller.recover_pending() is True

    assert store.load() is None
    assert events.index("write:slave:independent") < events.index(
        "write:master:independent"
    )
    assert master.calls == [("write_linkage", LinkageRole.INDEPENDENT)]
    assert slave.calls == [("write_linkage", LinkageRole.INDEPENDENT)]
    _assert_only_linkage_calls(master, slave)


async def test_opt_in_recovery_refreshes_disconnected_sessions_before_topology_proof(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "recover-disconnected.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
    )
    preflight = await controller.preflight(_spec())
    await master.write_linkage(LinkageRole.MASTER)
    now = datetime.now(UTC)
    with store.lease():
        store.create(
            ScheduleLinkageRecord(
                operation_id=preflight.spec.operation_id,
                phase=ScheduleLinkagePhase.APPLYING,
                spec=preflight.spec,
                snapshots=preflight.snapshots,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(minutes=2),
                linkage_write_intent_device_ids=("master",),
                linked_device_ids=("master",),
            )
        )
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    await asyncio.gather(master.disconnect(), slave.disconnect())

    assert await controller.recover_pending() is True

    assert store.load() is None
    assert master.connected is True
    assert slave.connected is True
    assert master.calls == [("write_linkage", LinkageRole.INDEPENDENT)]
    assert slave.calls == []
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_opt_in_recovery_refresh_failure_is_no_write_and_keeps_journal(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "recover-refresh-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
    )
    preflight = await controller.preflight(_spec())
    await master.write_linkage(LinkageRole.MASTER)
    now = datetime.now(UTC)
    with store.lease():
        store.create(
            ScheduleLinkageRecord(
                operation_id=preflight.spec.operation_id,
                phase=ScheduleLinkagePhase.APPLYING,
                spec=preflight.spec,
                snapshots=preflight.snapshots,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(minutes=2),
                linkage_write_intent_device_ids=("master",),
                linked_device_ids=("master",),
            )
        )
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    await asyncio.gather(master.disconnect(), slave.disconnect())
    master.fail_after_connect_numbers.add(1)

    with pytest.raises(
        ScheduleLinkageRollbackError,
        match="role topology does not match durable recovery intent",
    ):
        await controller.recover_pending()

    pending = store.load()
    assert pending is not None
    assert pending.phase is ScheduleLinkagePhase.APPLYING
    assert pending.linkage_write_intent_device_ids == ("master",)
    assert master.calls == []
    assert slave.calls == []
    assert master.session_connect_count == slave.session_connect_count == 1


async def test_failed_detach_latches_progress_and_recovery_retries_only_missing_role(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    slave.fail_before_roles.add(LinkageRole.INDEPENDENT)
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(ScheduleLinkageRollbackError, match="recovery is required"):
        await controller.run(preflight)

    pending = store.load()
    assert pending is not None
    assert pending.phase is ScheduleLinkagePhase.RECOVERY_REQUIRED
    assert pending.linkage_write_intent_device_ids == ("master", "slave")
    assert pending.detached_device_ids == ()
    assert (await slave.get_state()).linkage is LinkageRole.ASYNC_SLAVE
    assert (await master.get_state()).linkage is LinkageRole.MASTER
    master_call_count = len(master.calls)
    slave.fail_before_roles.clear()

    assert await controller.recover_pending() is True

    assert store.load() is None
    assert len(master.calls) == master_call_count + 1
    assert slave.calls[-1] == ("write_linkage", LinkageRole.INDEPENDENT)
    _assert_only_linkage_calls(master, slave)


async def test_schedule_change_between_preflight_and_run_fails_before_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "changed.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    slave.entries = tuple(
        entry.model_copy(update={"parameters": {**entry.parameters, "flow": 36}})
        if entry.slot == 2
        else entry
        for entry in slave.entries
    )

    with pytest.raises(ScheduleLinkagePreflightError, match="changed after confirmation"):
        await controller.run(preflight)

    assert store.load() is None
    assert master.calls == []
    assert slave.calls == []


@pytest.mark.parametrize("defect", ["current_gap", "unknown_mode", "duplicate_start"])
async def test_schedule_structure_defects_fail_closed_before_write(
    tmp_path: Path,
    defect: str,
) -> None:
    master, slave = await _ready_pair(clock=datetime(2026, 8, 26, 18, 8, 30))
    if defect == "current_gap":
        replacement = master.entries[1].model_copy(update={"end": "18:09"})
    elif defect == "unknown_mode":
        replacement = master.entries[0].model_copy(update={"mode": "unknown"})
    else:
        replacement = master.entries[2].model_copy(update={"start": "17:55"})
    master.entries = tuple(
        replacement if entry.slot == replacement.slot else entry for entry in master.entries
    )
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / f"{defect}.json"),
    )

    with pytest.raises(ScheduleLinkagePreflightError):
        await controller.preflight(_spec())

    assert master.calls == []
    assert slave.calls == []


async def test_boundary_outside_window_fails_before_write(tmp_path: Path) -> None:
    master, slave = await _ready_pair(clock=datetime(2026, 8, 26, 17, 56))
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "far.json"),
    )

    with pytest.raises(ScheduleLinkagePreflightError, match="outside the observation window"):
        await controller.preflight(_spec())

    assert master.calls == []
    assert slave.calls == []


async def test_window_must_include_post_boundary_samples_and_rollback_reserve(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "short-budget.json"),
    )

    with pytest.raises(ScheduleLinkagePreflightError, match="verification and rollback reserve"):
        await controller.preflight(_spec(observation_window_seconds=100))

    assert master.calls == []
    assert slave.calls == []


async def test_slave_next_tuple_must_differ_from_master(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    slave.feed_flow = 31
    slave.constant_flow = 30
    slave.entries = tuple(
        entry.model_copy(update={"parameters": {**entry.parameters, "flow": 30}})
        if entry.slot == 2
        else entry
        for entry in slave.entries
    )
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "same-next.json"),
    )

    with pytest.raises(ScheduleLinkagePreflightError, match="must differ from master"):
        await controller.preflight(_spec())

    assert master.calls == []
    assert slave.calls == []


async def test_preflight_rejects_device_clock_pair_skew_without_writes(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    slave.clock_offset_seconds = 3
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "clock-skew.json"),
    )

    with pytest.raises(ScheduleLinkagePreflightError, match="pair skew"):
        await controller.preflight(_spec(maximum_clock_skew_seconds=2))

    assert master.calls == []
    assert slave.calls == []


@pytest.mark.parametrize("defect", ["disabled", "same_binding"])
async def test_preflight_requires_enabled_distinct_physical_devices(
    tmp_path: Path,
    defect: str,
) -> None:
    master, slave = await _ready_pair()
    if defect == "disabled":
        await slave.set_enabled(False)
    else:
        slave._physical_binding = master.physical_binding  # noqa: SLF001
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / f"{defect}.json"),
    )

    with pytest.raises(ScheduleLinkagePreflightError):
        await controller.preflight(_spec())

    assert master.calls == []
    assert slave.calls == []


@pytest.mark.parametrize(
    ("clock_adjustment", "message"),
    [(-5, "regressed"), (20, "advanced implausibly")],
)
async def test_monitor_rejects_clock_discontinuity_and_role_only_restores(
    tmp_path: Path,
    clock_adjustment: float,
    message: str,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=0)
    store = JsonScheduleLinkageJournalStore(tmp_path / f"clock-{clock_adjustment}.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    task = asyncio.create_task(controller.run(preflight))
    await _wait_for_phase(store, ScheduleLinkagePhase.ACTIVE)
    await _wait_for_monitor_sleep(master.virtual_time)
    master.clock_offset_seconds = clock_adjustment
    slave.clock_offset_seconds = clock_adjustment

    with pytest.raises(ScheduleLinkageApplyError) as raised:
        await task

    assert message in str(raised.value.__cause__)
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_monitor_rejects_new_pair_skew_and_role_only_restores(tmp_path: Path) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=0)
    store = JsonScheduleLinkageJournalStore(tmp_path / "monitor-skew.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    task = asyncio.create_task(controller.run(preflight))
    await _wait_for_phase(store, ScheduleLinkagePhase.ACTIVE)
    await _wait_for_monitor_sleep(master.virtual_time)
    slave.clock_offset_seconds = 3

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await task

    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


class _FsyncAuditStore(JsonScheduleLinkageJournalStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fsync_observations: list[tuple[int | None, int]] = []

    def _fsync_parent(self) -> None:
        journal_links = self.path.stat().st_nlink if self.path.exists() else None
        temporary_count = len(tuple(self.path.parent.glob(f".{self.path.name}.*.tmp")))
        self.fsync_observations.append((journal_links, temporary_count))
        super()._fsync_parent()


class _FailAfterFsyncStore(JsonScheduleLinkageJournalStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fail_next_fsync = False

    def _fsync_parent(self) -> None:
        super()._fsync_parent()
        if self.fail_next_fsync:
            self.fail_next_fsync = False
            raise OSError("simulated post-fsync failure")


class _ReplaceWithPredecessorOnRollbackStore(_RecordingStore):
    """Simulate an external same-identity CAS replacement at rollback start."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.replaced = False

    def save(self, record: ScheduleLinkageRecord) -> None:
        if record.phase is ScheduleLinkagePhase.ROLLING_BACK and not self.replaced:
            self.replaced = True
            active = self.load()
            assert active is not None
            master_id = active.spec.master_device_id
            predecessor = active.model_copy(
                update={
                    "phase": ScheduleLinkagePhase.APPLYING,
                    "linkage_write_intent_device_ids": (master_id,),
                    "linked_device_ids": (master_id,),
                    "detached_device_ids": (),
                    "updated_at": active.updated_at + timedelta(microseconds=1),
                }
            )
            self.path.write_text(
                predecessor.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
        super().save(record)


class _RegressBeforeRollbackStore(_RecordingStore):
    """Return a same-identity predecessor when rollback first reloads durable progress."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.regress_on_next_load = False

    def load(self) -> ScheduleLinkageRecord | None:
        current = super().load()
        if self.regress_on_next_load:
            self.regress_on_next_load = False
            assert current is not None
            master_id = current.spec.master_device_id
            predecessor = current.model_copy(
                update={
                    "phase": ScheduleLinkagePhase.APPLYING,
                    "linkage_write_intent_device_ids": (master_id,),
                    "linked_device_ids": (master_id,),
                    "detached_device_ids": (),
                    "updated_at": current.updated_at + timedelta(microseconds=1),
                }
            )
            self.path.write_text(
                predecessor.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            return super().load()
        return current

    def save(self, record: ScheduleLinkageRecord) -> None:
        super().save(record)
        if record.phase is ScheduleLinkagePhase.ACTIVE:
            self.regress_on_next_load = True


class _FailRollbackAfterFsyncStore(_FailAfterFsyncStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.failed_rollback_save = False

    def save(self, record: ScheduleLinkageRecord) -> None:
        if (
            record.phase is ScheduleLinkagePhase.ROLLING_BACK
            and not self.failed_rollback_save
        ):
            self.failed_rollback_save = True
            self.fail_next_fsync = True
        super().save(record)


async def test_create_fsyncs_hardlink_then_unlink_and_validates_single_link(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    preflight = await _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "preflight.json"),
    ).preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.PREPARED,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=2),
    )
    store = _FsyncAuditStore(tmp_path / "journal.json")

    with store.lease():
        store.create(record)
        assert store.fsync_observations[:2] == [(2, 1), (1, 0)]
        assert store.path.stat().st_nlink == 1
        assert store.load() == record
        store.clear()


async def test_store_mutations_require_lease_and_reject_external_successor(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    preflight = await _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "preflight.json"),
    ).preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.PREPARED,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=2),
    )
    store = JsonScheduleLinkageJournalStore(tmp_path / "cas.json")
    with pytest.raises(ScheduleLinkageJournalError, match="requires its exclusive lease"):
        store.create(record)

    with store.lease():
        store.create(record)
        external = record.model_copy(update={"updated_at": now + timedelta(seconds=1)})
        store.path.write_text(external.model_dump_json(indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ScheduleLinkageJournalError, match="changed outside"):
            store.save(record.model_copy(update={"phase": ScheduleLinkagePhase.APPLYING}))


async def test_store_accepts_its_own_durable_successor_after_reported_save_failure(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    preflight = await _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "preflight.json"),
    ).preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.PREPARED,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=2),
    )
    store = _FailAfterFsyncStore(tmp_path / "successor.json")

    with store.lease():
        store.create(record)
        successor = record.model_copy(update={"phase": ScheduleLinkagePhase.APPLYING})
        store.fail_next_fsync = True
        with pytest.raises(ScheduleLinkageJournalError):
            store.save(successor)
        assert store.load() == successor
        final = successor.model_copy(update={"phase": ScheduleLinkagePhase.ROLLING_BACK})
        store.save(final)
        assert store.load() == final
        store.clear()


async def test_rollback_cas_predecessor_never_detaches_only_master(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    store = _ReplaceWithPredecessorOnRollbackStore(tmp_path / "rollback-cas.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(
        ScheduleLinkageRollbackError,
        match="journal changed during rollback transition",
    ):
        await controller.run(preflight)

    assert store.load() is not None
    assert (await master.get_state()).linkage is LinkageRole.MASTER
    assert (await slave.get_state()).linkage is LinkageRole.ASYNC_SLAVE
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in master.calls
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in slave.calls


async def test_rollback_rejects_durable_progress_regression_before_any_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = _RegressBeforeRollbackStore(tmp_path / "rollback-regression.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    with pytest.raises(
        ScheduleLinkageRollbackError,
        match="durable progress regressed before rollback",
    ):
        await controller.run(preflight)

    assert store.load() is not None
    assert (await master.get_state()).linkage is LinkageRole.MASTER
    assert (await slave.get_state()).linkage is LinkageRole.ASYNC_SLAVE
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in master.calls
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in slave.calls


async def test_rollback_accepts_exact_durable_successor_after_reported_fsync_failure(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = _FailRollbackAfterFsyncStore(tmp_path / "rollback-fsync.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())

    result = await controller.run(preflight)

    assert result.schedule_transition_verified is True
    assert store.failed_rollback_save is True
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


@pytest.mark.parametrize(
    ("phase", "intents", "linked"),
    [
        (ScheduleLinkagePhase.PREPARED, (), ()),
        (ScheduleLinkagePhase.APPLYING, ("master",), ("master",)),
    ],
)
async def test_restart_recovery_rejects_attached_role_hidden_by_predecessor(
    tmp_path: Path,
    phase: ScheduleLinkagePhase,
    intents: tuple[str, ...],
    linked: tuple[str, ...],
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / f"restart-{phase.value}.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=phase,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=2),
        linkage_write_intent_device_ids=intents,
        linked_device_ids=linked,
    )
    with store.lease():
        store.create(record)
    await master.write_linkage(LinkageRole.MASTER)
    await slave.write_linkage(LinkageRole.ASYNC_SLAVE)
    master.calls.clear()
    slave.calls.clear()

    with pytest.raises(
        ScheduleLinkageRollbackError,
        match="role topology does not match durable recovery intent",
    ):
        await controller.recover_pending()

    assert store.load() == record
    assert (await master.get_state()).linkage is LinkageRole.MASTER
    assert (await slave.get_state()).linkage is LinkageRole.ASYNC_SLAVE
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in master.calls
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in slave.calls


async def test_restart_recovery_rejects_swapped_master_slave_roles_before_any_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "restart-swapped.json")
    controller = _controller(master, slave, store)
    preflight = await controller.preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.ACTIVE,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=2),
        linkage_write_intent_device_ids=("master", "slave"),
        linked_device_ids=("master", "slave"),
    )
    with store.lease():
        store.create(record)
    await master.write_linkage(LinkageRole.ASYNC_SLAVE)
    await slave.write_linkage(LinkageRole.MASTER)
    master.calls.clear()
    slave.calls.clear()

    with pytest.raises(
        ScheduleLinkageRollbackError,
        match="role topology does not match durable recovery intent",
    ):
        await controller.recover_pending()

    assert store.load() == record
    assert (await master.get_state()).linkage is LinkageRole.ASYNC_SLAVE
    assert (await slave.get_state()).linkage is LinkageRole.MASTER
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in master.calls
    assert ("write_linkage", LinkageRole.INDEPENDENT) not in slave.calls


def test_record_rejects_noncanonical_progress_and_scope(tmp_path: Path) -> None:
    del tmp_path
    now = datetime.now(UTC)
    spec = _spec()
    # Build minimal validated snapshots through JSON to keep this a pure model invariant test.
    base_snapshot = {
        "physical_binding": {
            "vendor_device_id_digest": "1" * 64,
            "mac_address_digest": "2" * 64,
            "product_key": "simulator",
            "config_fingerprint": "3" * 64,
        },
        "enabled": True,
        "power": 40,
        "mode": "constant",
        "frequency": 5,
        "timer_enabled": True,
        "linkage": "independent",
        "schedule_fingerprint": "4" * 64,
        "expectation": {
            "current_slot": 1,
            "next_slot": 2,
            "boundary_at": "2026-08-26T18:10:00",
            "after_valid_until": "2026-08-26T18:11:00",
            "before": {"mode": "feed", "flow": 30, "frequency": 5, "feed_time": 15},
            "after_mode": "constant",
            "after_flow": 50,
            "after_frequency": None,
        },
    }
    slave_snapshot = {
        **base_snapshot,
        "physical_binding": {
            **base_snapshot["physical_binding"],
            "vendor_device_id_digest": "5" * 64,
            "mac_address_digest": "6" * 64,
        },
    }
    snapshots = (
        {"device_id": "master", **base_snapshot},
        {"device_id": "slave", **slave_snapshot},
    )
    payload = {
        "operation_id": spec.operation_id,
        "phase": "applying",
        "spec": spec.model_dump(mode="json"),
        "snapshots": snapshots,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=2)).isoformat(),
        "linkage_write_intent_device_ids": ["slave"],
    }
    with pytest.raises(ValidationError, match="master-first prefix"):
        ScheduleLinkageRecord.model_validate(payload)
    with pytest.raises(ValidationError):
        ScheduleLinkageRecord.model_validate({**payload, "mutation_scope": "full_target"})
    with pytest.raises(ValidationError, match="active journal must prove both"):
        ScheduleLinkageRecord.model_validate(
            {
                **payload,
                "phase": "active",
                "linkage_write_intent_device_ids": ["master", "slave"],
                "linked_device_ids": ["master"],
            }
        )
    with pytest.raises(ValidationError, match="needs durable write intent and an error"):
        ScheduleLinkageRecord.model_validate(
            {
                **payload,
                "phase": "recovery_required",
                "linkage_write_intent_device_ids": ["master"],
            }
        )
    with pytest.raises(ValidationError):
        ScheduleLinkageRecord.model_validate(
            {
                **payload,
                "phase": "recovery_required",
                "linkage_write_intent_device_ids": ["master"],
                "error": "x" * 513,
            }
        )
    with pytest.raises(ValidationError, match="timestamps are not monotonic"):
        ScheduleLinkageRecord.model_validate(
            {**payload, "updated_at": (now - timedelta(seconds=1)).isoformat()}
        )


def test_spec_bounds_identifiers_and_record_error() -> None:
    with pytest.raises(ValidationError):
        _spec(master_device_id="x" * 129)
    with pytest.raises(ValidationError):
        _spec(slave_device_id="unsafe/id")
