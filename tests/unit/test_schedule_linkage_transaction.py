import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jebao_flow.devices import LinkageSafetyInterlock, SimulatedJebaoDevice
from jebao_flow.devices.schedule_flow_experiment import (
    ScheduleFlowExperimentController,
    ScheduleFlowExperimentSpec,
)
from jebao_flow.devices.schedule_linkage import (
    ScheduleActiveLinkageController,
    ScheduleLinkageApplyError,
    ScheduleLinkageDriftDimension,
    ScheduleLinkageExternalDisarmProof,
    ScheduleLinkageExternalDisarmState,
    ScheduleLinkagePhase,
    ScheduleLinkagePreflightError,
    ScheduleLinkageRecord,
    ScheduleLinkageRollbackError,
    ScheduleLinkageRunFailure,
    ScheduleLinkageRunProgressEvent,
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
from jebao_flow.protocol.errors import ProtocolConnectionError
from jebao_flow.protocol.models import DeviceSchedule, DeviceTarget, LinkageRole, ScheduleEntry
from jebao_flow.protocol.schedule_wire import decode_local_wavemaker_pro_slot_wire


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
        self.sine_boundary_time = datetime(2026, 8, 26, 18, 11).time()
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
        self.fail_before_connect_numbers: set[int] = set()
        self.fail_after_apply_roles: set[LinkageRole] = set()
        self.fail_before_roles: set[LinkageRole] = set()
        self.fail_state_reads_for_roles: set[LinkageRole] = set()
        self.reported_state_updates_by_role: dict[LinkageRole, dict[str, object]] = {}
        self.reported_state_update_sequences_by_role: dict[
            LinkageRole,
            list[dict[str, object] | None],
        ] = {}
        self.role_frequency_overrides: dict[LinkageRole, int] = {}
        self.reported_auto_updates_by_role: dict[LinkageRole, dict[str, object]] = {}
        self.reported_auto_update_sequences_by_role: dict[
            LinkageRole,
            list[dict[str, object] | None],
        ] = {}
        self.reported_schedule_drift_roles: set[LinkageRole] = set()
        self.clock_offsets_after_role: dict[LinkageRole, float] = {}
        self.last_written_role = LinkageRole.INDEPENDENT
        self.ordinary_state_read_count = 0
        self.fail_ordinary_state_read_numbers: set[int] = set()
        self.ordinary_state_read_failures: dict[int, BaseException] = {}
        self.ordinary_state_read_time_advances: dict[int, float] = {}
        self.ordinary_state_read_time_advance_seconds = 0.0
        self.pause_ordinary_state_read_numbers: set[int] = set()
        self.ordinary_state_read_paused = asyncio.Event()
        self.resume_ordinary_state_read = asyncio.Event()
        self.ordinary_state_read_cancelled_count = 0
        self.explicit_state_read_count = 0
        self.fail_explicit_state_reads_remaining = 0
        self.fail_explicit_state_read_numbers: set[int] = set()
        self.explicit_state_read_failures: dict[int, BaseException] = {}
        self.explicit_state_read_time_advances: dict[int, float] = {}
        self.explicit_state_read_time_advance_seconds = 0.0
        self.explicit_clock_offsets: list[float] = []
        self.unsolicited_clock_offsets_by_role: dict[LinkageRole, list[float]] = {}
        self._explicit_state_read_active = False
        self.pause_explicit_state_read_numbers: set[int] = set()
        self.explicit_state_read_paused = asyncio.Event()
        self.resume_explicit_state_read = asyncio.Event()
        self.heartbeat_count = 0
        self.fail_heartbeat_numbers: set[int] = set()
        self.heartbeat_started_at: list[float] = []
        self.heartbeat_time_advance_seconds = 0.0
        self.heartbeat_time_advances: dict[int, float] = {}
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
        if connect_number in self.fail_before_connect_numbers:
            self.session_connect_count += 1
            if self.events is not None:
                self.events.append(f"session:{self.device_id}:connect_failed")
            raise RuntimeError("simulated session refresh failure before connect")
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
        if not self._explicit_state_read_active:
            self.ordinary_state_read_count += 1
            read_number = self.ordinary_state_read_count
            self.virtual_time.value += (
                self.ordinary_state_read_time_advance_seconds
                + self.ordinary_state_read_time_advances.pop(read_number, 0.0)
            )
            if read_number in self.pause_ordinary_state_read_numbers:
                self.pause_ordinary_state_read_numbers.discard(read_number)
                self.ordinary_state_read_paused.set()
                try:
                    await self.resume_ordinary_state_read.wait()
                except asyncio.CancelledError:
                    self.ordinary_state_read_cancelled_count += 1
                    raise
            if read_number in self.fail_ordinary_state_read_numbers:
                self.fail_ordinary_state_read_numbers.remove(read_number)
                raise ProtocolConnectionError("simulated ordinary state read failure")
            if read_number in self.ordinary_state_read_failures:
                raise self.ordinary_state_read_failures.pop(read_number)
        state = await super().get_state()
        if state.linkage in self.role_frequency_overrides:
            state = state.model_copy(
                update={"frequency": self.role_frequency_overrides[state.linkage]}
            )
        if state.linkage in self.fail_state_reads_for_roles:
            self.fail_state_reads_for_roles.remove(state.linkage)
            raise RuntimeError("simulated linked state read failure")
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
        elif wall < self.sine_boundary_time:
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
        state_update_sequence = self.reported_state_update_sequences_by_role.get(
            state.linkage
        )
        state_updates = None
        if state_update_sequence:
            state_updates = state_update_sequence.pop(0)
            if not state_update_sequence:
                del self.reported_state_update_sequences_by_role[state.linkage]
        elif state.linkage in self.reported_state_updates_by_role:
            state_updates = self.reported_state_updates_by_role.pop(state.linkage)
        if state_updates is not None:
            result = result.model_copy(update=state_updates)
        if state.linkage in self.reported_schedule_drift_roles:
            self.reported_schedule_drift_roles.remove(state.linkage)
            if result.schedule is None:
                raise AssertionError("simulated schedule drift requires a schedule")
            result = result.model_copy(
                update={
                    "schedule": result.schedule.model_copy(
                        update={"invalid_slots": (47,)}
                    )
                }
            )
        auto_update_sequence = self.reported_auto_update_sequences_by_role.get(
            state.linkage
        )
        auto_updates = None
        if auto_update_sequence:
            auto_updates = auto_update_sequence.pop(0)
            if not auto_update_sequence:
                del self.reported_auto_update_sequences_by_role[state.linkage]
        elif state.linkage in self.reported_auto_updates_by_role:
            auto_updates = self.reported_auto_updates_by_role.pop(state.linkage)
        if auto_updates is not None:
            result = result.model_copy(
                update={
                    "observed_attributes": {
                        **result.observed_attributes,
                        **auto_updates,
                    }
                }
            )
        unsolicited_clock_offsets = self.unsolicited_clock_offsets_by_role.get(
            state.linkage
        )
        if unsolicited_clock_offsets:
            offset = unsolicited_clock_offsets.pop(0)
            if not unsolicited_clock_offsets:
                del self.unsolicited_clock_offsets_by_role[state.linkage]
            if not self._explicit_state_read_active:
                if result.schedule is None or result.schedule.device_local_time is None:
                    raise AssertionError("simulated unsolicited clock offset requires a clock")
                result = result.model_copy(
                    update={
                        "schedule": result.schedule.model_copy(
                            update={
                                "device_local_time": (
                                    result.schedule.device_local_time
                                    + timedelta(seconds=offset)
                                )
                            }
                        )
                    }
                )
        return result

    async def get_explicit_state(self):
        self.explicit_state_read_count += 1
        read_number = self.explicit_state_read_count
        self.virtual_time.value += self.explicit_state_read_time_advances.pop(
            read_number,
            0.0,
        )
        self.virtual_time.value += self.explicit_state_read_time_advance_seconds
        if read_number in self.pause_explicit_state_read_numbers:
            self.pause_explicit_state_read_numbers.discard(read_number)
            self.explicit_state_read_paused.set()
            await self.resume_explicit_state_read.wait()
        if read_number in self.fail_explicit_state_read_numbers:
            self.fail_explicit_state_read_numbers.remove(read_number)
            raise ProtocolConnectionError("simulated explicit state read failure")
        if read_number in self.explicit_state_read_failures:
            raise self.explicit_state_read_failures.pop(read_number)
        if self.fail_explicit_state_reads_remaining:
            self.fail_explicit_state_reads_remaining -= 1
            raise ProtocolConnectionError("simulated explicit state read failure")
        self._explicit_state_read_active = True
        try:
            state = await self.get_state()
        finally:
            self._explicit_state_read_active = False
        if not self.explicit_clock_offsets:
            return state
        offset = self.explicit_clock_offsets.pop(0)
        if state.schedule is None or state.schedule.device_local_time is None:
            raise AssertionError("simulated explicit clock offset requires a clock")
        return state.model_copy(
            update={
                "schedule": state.schedule.model_copy(
                    update={
                        "device_local_time": (
                            state.schedule.device_local_time + timedelta(seconds=offset)
                        )
                    }
                )
            }
        )

    async def heartbeat(self) -> None:
        self.heartbeat_count += 1
        self.heartbeat_started_at.append(self.virtual_time.value)
        self.virtual_time.value += self.heartbeat_time_advance_seconds
        self.virtual_time.value += self.heartbeat_time_advances.pop(
            self.heartbeat_count,
            0.0,
        )
        if self.heartbeat_count in self.fail_heartbeat_numbers:
            self.fail_heartbeat_numbers.remove(self.heartbeat_count)
            raise ProtocolConnectionError("simulated heartbeat failure")
        await super().heartbeat()

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
        self.last_written_role = role
        if role in self.clock_offsets_after_role:
            self.clock_offset_seconds = self.clock_offsets_after_role[role]
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
        device.ordinary_state_read_count = 0
    if events is not None:
        events.clear()
    return master, slave


async def _ready_staged_pair(
    *,
    boundary_time: str = "18:11",
    next_entry_end: str = "18:13",
    linked_clock_step_seconds: float = 1,
    events: list[str] | None = None,
) -> tuple[_ScheduleDevice, _ScheduleDevice]:
    """Return the exact non-wrapping two-entry schedule owned by the field test."""

    master, slave = await _ready_pair(
        clock=datetime(2026, 8, 26, 18, 10, 20),
        linked_clock_step_seconds=linked_clock_step_seconds,
        events=events,
    )
    for device in (master, slave):
        boundary_hour, boundary_minute = (
            int(part) for part in boundary_time.split(":", maxsplit=1)
        )
        device.sine_boundary_time = datetime(
            2026,
            8,
            26,
            boundary_hour,
            boundary_minute,
        ).time()
        device.entries = (
            _entry(
                0,
                "18:10",
                boundary_time,
                "constant",
                flow=device.constant_flow,
                frequency=0,
            ),
            _entry(
                1,
                boundary_time,
                next_entry_end,
                "sine",
                flow=device.sine_flow,
                frequency=device.sine_frequency,
            ),
        )
    return master, slave


async def _external_disarm_proof(
    record: ScheduleLinkageRecord,
    master: _ScheduleDevice,
    slave: _ScheduleDevice,
) -> ScheduleLinkageExternalDisarmProof:
    states: list[ScheduleLinkageExternalDisarmState] = []
    for device in (master, slave):
        physical_binding = device.physical_binding
        assert physical_binding is not None
        states.append(
            ScheduleLinkageExternalDisarmState.from_state(
                device.device_id,
                await device.get_state(),
                physical_binding=physical_binding,
            )
        )
    return ScheduleLinkageExternalDisarmProof(
        operation_id=record.operation_id,
        states=tuple(states),
    )


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


def _staged_spec(**updates: object) -> ScheduleLinkageSpec:
    values: dict[str, object] = {
        "observation_window_seconds": 90,
        "verification_interval_seconds": 1,
        "minimum_lead_seconds": 10,
        "post_boundary_stability_seconds": 2,
        "observe_slave_after_tuple_variance": True,
    }
    values.update(updates)
    return _spec(**values)


def _controller(
    master: _ScheduleDevice,
    slave: _ScheduleDevice,
    store: JsonScheduleLinkageJournalStore,
    *,
    authorizer=None,
    sample_observer=None,
    progress_observer=None,
    refresh_sessions_before_critical_reads: bool = False,
    owned_staged_auto_transition_observation: bool = False,
    monotonic_clock=None,
    safety_interlock=None,
    sleep=None,
) -> ScheduleActiveLinkageController:
    return ScheduleActiveLinkageController(
        {"master": master, "slave": slave},
        store,
        prerequisite_authorizer=authorizer or (lambda _spec, _snapshots: None),
        safety_interlock=safety_interlock
        or LinkageSafetyInterlock(initially_permitted=True),
        monotonic_clock=monotonic_clock or master.virtual_time.monotonic,
        sleep=sleep or master.virtual_time.sleep,
        sample_observer=sample_observer,
        progress_observer=progress_observer,
        refresh_sessions_before_critical_reads=refresh_sessions_before_critical_reads,
        owned_staged_auto_transition_observation=(
            owned_staged_auto_transition_observation
        ),
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


def test_legacy_pair_failure_checkpoint_remains_valid_without_dimensions() -> None:
    event = ScheduleLinkageRunProgressEvent.model_validate(
        {
            "kind": "failed",
            "occurred_at": datetime.now(UTC).isoformat(),
            "failure": "slave_pair_verification",
            "drift_dimensions": [],
        }
    )

    assert event.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_VERIFICATION
    assert event.drift_dimensions == ()


@pytest.mark.parametrize(
    "failure",
    [
        "master_pair_clock",
        "slave_pair_clock",
        "master_pair_clock_skew",
        "master_pair_clock_continuity",
        "slave_pair_clock_skew",
        "slave_pair_clock_continuity",
    ],
)
def test_legacy_and_precise_pair_clock_failures_round_trip(failure: str) -> None:
    event = ScheduleLinkageRunProgressEvent.model_validate(
        {
            "kind": "failed",
            "occurred_at": datetime.now(UTC).isoformat(),
            "failure": failure,
            "drift_dimensions": [],
        }
    )

    assert event.model_dump(mode="json")["failure"] == failure
    assert event.drift_dimensions == ()


def test_pair_failure_checkpoint_rejects_noncanonical_or_unscoped_dimensions() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="require confirmation or pair state"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.MONITOR,
            drift_dimensions=(ScheduleLinkageDriftDimension.POWER,),
        )
    with pytest.raises(ValidationError, match="canonically ordered"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.SLAVE_PAIR_STATE,
            drift_dimensions=(
                ScheduleLinkageDriftDimension.POWER,
                ScheduleLinkageDriftDimension.ENABLED,
            ),
        )
    with pytest.raises(ValidationError, match="non-state dimension"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.SLAVE_PAIR_STATE,
            drift_dimensions=(ScheduleLinkageDriftDimension.AUTO_EVIDENCE,),
        )
    with pytest.raises(ValidationError, match="only the Auto evidence"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.SLAVE_PAIR_AUTO,
            drift_dimensions=(ScheduleLinkageDriftDimension.POWER,),
        )
    with pytest.raises(ValidationError, match="non-confirmation dimension"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH,
            drift_dimensions=(ScheduleLinkageDriftDimension.ONLINE,),
        )


def test_participant_pair_failures_keep_strict_dimension_allowlists() -> None:
    now = datetime.now(UTC)
    state = ScheduleLinkageRunProgressEvent(
        kind=ScheduleLinkageRunProgressKind.FAILED,
        occurred_at=now,
        failure=ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_STATE,
        drift_dimensions=(ScheduleLinkageDriftDimension.FREQUENCY,),
    )
    auto = ScheduleLinkageRunProgressEvent(
        kind=ScheduleLinkageRunProgressKind.FAILED,
        occurred_at=now,
        failure=ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_AUTO,
        drift_dimensions=(ScheduleLinkageDriftDimension.AUTO_EVIDENCE,),
    )

    assert state.model_dump(mode="json")["failure"] == "slave_pair_master_state"
    assert auto.model_dump(mode="json")["failure"] == "master_pair_slave_auto"
    with pytest.raises(ValidationError, match="non-state dimension"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_STATE,
            drift_dimensions=(ScheduleLinkageDriftDimension.AUTO_EVIDENCE,),
        )
    with pytest.raises(ValidationError, match="only the Auto evidence"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_AUTO,
            drift_dimensions=(ScheduleLinkageDriftDimension.FREQUENCY,),
        )
    with pytest.raises(ValidationError, match="at least one drift dimension"):
        ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=now,
            failure=ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE,
        )


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


async def test_preflight_authorization_failure_is_typed_without_raw_text(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()

    def reject(_spec, _snapshots) -> None:
        raise RuntimeError("private-device-id qualification receipt detail")

    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "authorization-rejected.json"),
        authorizer=reject,
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller.preflight(_spec())

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_AUTHORIZATION
    assert "private-device-id" not in str(captured.value)
    assert "receipt detail" not in str(captured.value)
    assert master.calls == slave.calls == []


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
    assert progress[-1].failure is ScheduleLinkageRunFailure.MASTER_PAIR_SESSION_REFRESH
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert slave.calls == []


async def test_slave_pair_session_refresh_failure_is_precise_and_restored(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    # Initial capture, first-write gate and master pair verification consume refreshes 1..3.
    slave.fail_after_connect_numbers.add(4)
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-pair-refresh.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SESSION_REFRESH
    assert failed.drift_dimensions == ()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_slave_pair_state_read_failure_is_precise_and_restored(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    slave.fail_state_reads_for_roles.add(LinkageRole.ASYNC_SLAVE)
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-pair-read.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_STATE_READ
    assert failed.drift_dimensions == ()
    assert (
        ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
        not in {event.kind for event in progress}
    )
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_slave_pair_deadline_failure_is_precise_and_restored(tmp_path: Path) -> None:
    master, slave = await _ready_pair()

    def deadline_clock() -> float:
        return 200.0 if slave.last_written_role is LinkageRole.ASYNC_SLAVE else 0.0

    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-pair-deadline.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        monotonic_clock=deadline_clock,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_DEADLINE
    assert failed.drift_dimensions == ()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_slave_pair_clock_failure_is_precise_and_restored(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    slave.clock_offsets_after_role = {
        LinkageRole.ASYNC_SLAVE: 10,
        LinkageRole.INDEPENDENT: 0,
    }
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-pair-clock.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK_SKEW
    assert failed.drift_dimensions == ()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_master_pair_clock_skew_failure_is_precise_and_restored(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    master.clock_offsets_after_role = {
        LinkageRole.MASTER: 10,
        LinkageRole.INDEPENDENT: 0,
    }
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "master-pair-clock-skew.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.MASTER_PAIR_CLOCK_SKEW
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize(
    ("participant", "expected_failure"),
    [
        ("master", ScheduleLinkageRunFailure.MASTER_PAIR_CLOCK_CONTINUITY),
        ("slave", ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK_CONTINUITY),
    ],
)
async def test_pair_clock_continuity_failure_is_precise_and_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    participant: str,
    expected_failure: ScheduleLinkageRunFailure,
) -> None:
    master, slave = await _ready_pair()
    target = master if participant == "master" else slave
    target_role = (
        LinkageRole.MASTER if participant == "master" else LinkageRole.ASYNC_SLAVE
    )
    original_write_linkage = target.write_linkage

    async def write_linkage_and_jump_both_clocks(role, *, guard=None) -> None:
        await original_write_linkage(role, guard=guard)
        if role is target_role:
            master.clock_offset_seconds = 10
            slave.clock_offset_seconds = 10

    monkeypatch.setattr(target, "write_linkage", write_linkage_and_jump_both_clocks)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"{participant}-pair-clock-continuity.json"
    )
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is expected_failure
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_post_role_verification_uses_explicit_replies_after_fresh_sessions(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    # A freshly authenticated LAN session may receive one queued 0x04 report before the
    # correlated 0x03 query reply.  Reply-only reads must consume and ignore that report for
    # both role transitions; otherwise either offset would be classified as pair-clock drift.
    master.unsolicited_clock_offsets_by_role[LinkageRole.MASTER] = [10]
    slave.unsolicited_clock_offsets_by_role[LinkageRole.ASYNC_SLAVE] = [10]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    activation_samples = []
    slave_pair_verified = False
    store = _RecordingStore(tmp_path / "explicit-post-role-read.json")
    records_at_pair_start: int | None = None
    records_at_activation_sample: list[int] = []

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        nonlocal records_at_pair_start, slave_pair_verified
        progress.append(event)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED:
            records_at_pair_start = len(store.records)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED:
            slave_pair_verified = True

    def observe_sample(sample) -> None:
        if not slave_pair_verified:
            activation_samples.append(sample)
            records_at_activation_sample.append(len(store.records))

    controller = _controller(
        master,
        slave,
        store,
        progress_observer=observe_progress,
        sample_observer=observe_sample,
        refresh_sessions_before_critical_reads=True,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert master.unsolicited_clock_offsets_by_role == {}
    assert slave.unsolicited_clock_offsets_by_role == {}
    assert master.explicit_state_read_count == 3
    assert slave.explicit_state_read_count == 3
    assert not any(event.kind is ScheduleLinkageRunProgressKind.FAILED for event in progress)
    assert len(activation_samples) == 1
    assert activation_samples[0].phase == "before"
    # Explicit verification itself neither persists another record nor publishes a diagnostic
    # sample before the full role topology has passed all strict checks.
    slave_pair_started = next(
        index
        for index, event in enumerate(progress)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED
    )
    slave_pair_verified_index = next(
        index
        for index, event in enumerate(progress)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED
    )
    assert slave_pair_verified_index == slave_pair_started + 1
    assert records_at_pair_start is not None
    assert records_at_activation_sample == [records_at_pair_start]
    _assert_only_linkage_calls(master, slave)


async def test_controller_without_session_refresh_keeps_ordinary_critical_reads(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    master.fail_explicit_state_reads_remaining = 10
    slave.fail_explicit_state_reads_remaining = 10
    store = JsonScheduleLinkageJournalStore(tmp_path / "ordinary-critical-reads.json")
    controller = _controller(master, slave, store)

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    assert master.fail_explicit_state_reads_remaining == 10
    assert slave.fail_explicit_state_reads_remaining == 10
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_first_write_gate_requires_explicit_reply_before_any_role_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.fail_explicit_state_read_numbers.add(1)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "explicit-gate-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.FIRST_WRITE_GATE
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 1
    assert slave.explicit_state_read_count == 1
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert samples == []
    assert store.load() is None


@pytest.mark.parametrize("authority_change", ["stop", "safety", "cancel"])
async def test_first_write_gate_explicit_read_cleans_up_authority_changes(
    tmp_path: Path,
    authority_change: str,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.pause_explicit_state_read_numbers.add(1)
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"explicit-gate-{authority_change}.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        safety_interlock=interlock,
    )
    preflight = await controller.preflight(_spec())
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(slave.explicit_state_read_paused.wait(), timeout=1)

    if authority_change == "stop":
        assert await controller.stop(preflight.spec.operation_id) is True
        result = await asyncio.wait_for(run_task, timeout=1)
        assert result.stop_reason is ScheduleLinkageStopReason.MANUAL
        assert result.schedule_transition_verified is False
        expected_failure = ScheduleLinkageRunFailure.FIRST_WRITE_GATE
    elif authority_change == "safety":
        interlock.trip()
        with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
            await asyncio.wait_for(run_task, timeout=1)
        expected_failure = ScheduleLinkageRunFailure.FIRST_WRITE_GATE
    else:
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=1)
        expected_failure = ScheduleLinkageRunFailure.CANCELLED

    assert progress[-1].failure is expected_failure
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 1
    assert slave.explicit_state_read_count == 1
    assert master.calls == []
    assert slave.calls == []
    assert master.commands == []
    assert slave.commands == []
    assert samples == []
    assert store.load() is None
    assert controller.active_operation_id is None


@pytest.mark.parametrize("authority_change", ["stop", "safety", "cancel"])
async def test_post_master_explicit_read_restores_durable_intent_on_authority_change(
    tmp_path: Path,
    authority_change: str,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    # Read one is the explicit first-write gate; read two is the initial post-master proof.
    slave.pause_explicit_state_read_numbers.add(2)
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"explicit-post-master-{authority_change}.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        safety_interlock=interlock,
    )
    preflight = await controller.preflight(_spec())
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(slave.explicit_state_read_paused.wait(), timeout=1)

    if authority_change == "stop":
        assert await controller.stop(preflight.spec.operation_id) is True
        result = await asyncio.wait_for(run_task, timeout=1)
        assert result.stop_reason is ScheduleLinkageStopReason.MANUAL
        assert result.schedule_transition_verified is False
        expected_failure = ScheduleLinkageRunFailure.MASTER_PAIR_VERIFICATION
    elif authority_change == "safety":
        interlock.trip()
        with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
            await asyncio.wait_for(run_task, timeout=1)
        expected_failure = ScheduleLinkageRunFailure.MASTER_PAIR_VERIFICATION
    else:
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=1)
        expected_failure = ScheduleLinkageRunFailure.CANCELLED

    assert progress[-1].failure is expected_failure
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 2
    assert slave.explicit_state_read_count == 2
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert slave.calls == []
    assert samples == []
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_slave_pair_immutable_failure_reports_only_allowlisted_dimensions(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {
        "online": False,
        "error": "private transport detail",
        "enabled": False,
        "power": 41,
        "mode": "pulse",
        "frequency": 6,
        "timer_enabled": False,
        "linkage": LinkageRole.INDEPENDENT,
    }
    slave.reported_schedule_drift_roles.add(LinkageRole.ASYNC_SLAVE)
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-pair-state.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert failed.drift_dimensions == (
        ScheduleLinkageDriftDimension.ONLINE,
        ScheduleLinkageDriftDimension.ERROR,
        ScheduleLinkageDriftDimension.ENABLED,
        ScheduleLinkageDriftDimension.POWER,
        ScheduleLinkageDriftDimension.MODE,
        ScheduleLinkageDriftDimension.FREQUENCY,
        ScheduleLinkageDriftDimension.TIMER_ENABLED,
        ScheduleLinkageDriftDimension.LINKAGE,
        ScheduleLinkageDriftDimension.SCHEDULE_FINGERPRINT,
    )
    checkpoint = failed.model_dump_json()
    assert set(failed.model_dump(mode="json")) == {
        "kind",
        "occurred_at",
        "failure",
        "drift_dimensions",
    }
    assert "device_id" not in checkpoint
    assert "private transport detail" not in checkpoint
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_slave_pair_auto_failure_is_precise_and_restored(tmp_path: Path) -> None:
    master, slave = await _ready_pair()
    slave.reported_auto_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"AutoFlow": 34}
    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "slave-pair-auto.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    failed = progress[-1]
    assert failed.failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_AUTO
    assert failed.drift_dimensions == (ScheduleLinkageDriftDimension.AUTO_EVIDENCE,)
    assert "AutoFlow" not in failed.model_dump_json()
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT


async def test_unexpected_state_inspection_failure_keeps_valid_coarse_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = await _ready_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "coarse-state-failure.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)
    original = controller._assert_immutable_snapshot  # noqa: SLF001
    failed = False

    def fail_before_dimensions(snapshot, state, expected_role, **kwargs) -> None:
        nonlocal failed
        if expected_role is LinkageRole.ASYNC_SLAVE and not failed:
            failed = True
            raise RuntimeError("private unexpected state inspection failure")
        original(snapshot, state, expected_role, **kwargs)

    monkeypatch.setattr(controller, "_assert_immutable_snapshot", fail_before_dimensions)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_VERIFICATION
    assert progress[-1].drift_dimensions == ()
    assert "private unexpected" not in progress[-1].model_dump_json()
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_frequency_only_role_report_requires_two_explicit_exact_reads(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": 6},
        None,
        None,
    ]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    activation_samples = []
    slave_pair_verified = False
    store = _RecordingStore(tmp_path / "transient-frequency.json")
    records_at_pair_start: int | None = None
    records_at_activation_sample: list[int] = []

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        nonlocal records_at_pair_start, slave_pair_verified
        progress.append(event)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED:
            records_at_pair_start = len(store.records)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED:
            slave_pair_verified = True

    def observe_sample(sample) -> None:
        samples.append(sample)
        if not slave_pair_verified:
            activation_samples.append(sample)
            records_at_activation_sample.append(len(store.records))

    controller = _controller(
        master,
        slave,
        store,
        progress_observer=observe_progress,
        sample_observer=observe_sample,
        refresh_sessions_before_critical_reads=True,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert master.explicit_state_read_count == 5
    assert slave.explicit_state_read_count == 5
    assert master.session_connect_count == master.session_disconnect_count == 7
    assert slave.session_connect_count == slave.session_disconnect_count == 7
    assert LinkageRole.ASYNC_SLAVE not in slave.reported_state_update_sequences_by_role
    pair_started = progress.index(
        next(
            event
            for event in progress
            if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED
        )
    )
    pair_verified = progress.index(
        next(
            event
            for event in progress
            if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED
        )
    )
    assert pair_verified == pair_started + 1
    assert samples
    assert len(activation_samples) == 1
    assert activation_samples[0].phase == "before"
    assert records_at_pair_start is not None
    assert records_at_activation_sample == [records_at_pair_start]
    assert sum(
        record.phase is ScheduleLinkagePhase.APPLYING
        and record.linkage_write_intent_device_ids == ("master", "slave")
        and record.linked_device_ids == ("master",)
        for record in store.records
    ) == 1
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    _assert_only_linkage_calls(master, slave)


async def test_frequency_convergence_resets_after_a_repeated_mismatch(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": 6},
        None,
        {"frequency": 7},
        None,
        None,
    ]
    activation_samples = []
    slave_pair_verified = False
    store = _RecordingStore(tmp_path / "reset-frequency-counter.json")
    records_at_pair_start: int | None = None
    records_at_activation_sample: list[int] = []

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        nonlocal records_at_pair_start, slave_pair_verified
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED:
            records_at_pair_start = len(store.records)
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED:
            slave_pair_verified = True

    def observe_sample(sample) -> None:
        if not slave_pair_verified:
            activation_samples.append(sample)
            records_at_activation_sample.append(len(store.records))

    controller = _controller(
        master,
        slave,
        store,
        progress_observer=observe_progress,
        sample_observer=observe_sample,
        refresh_sessions_before_critical_reads=True,
    )

    result = await controller.run(await controller.preflight(_spec()))

    assert result.schedule_transition_verified is True
    assert master.explicit_state_read_count == 7
    assert slave.explicit_state_read_count == 7
    assert master.session_connect_count == master.session_disconnect_count == 9
    assert slave.session_connect_count == slave.session_disconnect_count == 9
    assert LinkageRole.ASYNC_SLAVE not in slave.reported_state_update_sequences_by_role
    assert len(activation_samples) == 1
    assert activation_samples[0].phase == "before"
    assert records_at_pair_start is not None
    assert records_at_activation_sample == [records_at_pair_start]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    _assert_only_linkage_calls(master, slave)


async def test_persistent_frequency_drift_is_subject_specific_and_never_rewritten(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": value}
        for value in (6, 7, 8, 9, 10)
    ]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "persistent-frequency.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.FREQUENCY,
    )
    assert master.explicit_state_read_count == 7
    assert slave.explicit_state_read_count == 7
    assert samples == []
    assert store.load() is None
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    _assert_only_linkage_calls(master, slave)


async def test_non_frequency_pair_drift_fails_without_convergence_reads(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"power": 41}
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "power-drift.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (ScheduleLinkageDriftDimension.POWER,)
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    assert master.virtual_time.sleep_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_slave_activation_attributes_master_state_drift_to_master_role(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    master.reported_state_update_sequences_by_role[LinkageRole.MASTER] = [
        None,
        {"power": 41},
    ]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "master-state-attribution.json"),
        progress_observer=progress.append,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_STATE
    assert progress[-1].drift_dimensions == (ScheduleLinkageDriftDimension.POWER,)
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_slave_activation_attributes_master_auto_drift_to_master_role(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    master.reported_auto_update_sequences_by_role[LinkageRole.MASTER] = [
        None,
        {"AutoFlow": 34},
    ]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "master-auto-attribution.json"),
        progress_observer=progress.append,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_AUTO
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
    )
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_auto_drift_during_frequency_convergence_fails_immediately(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    slave.reported_auto_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        None,
        {"AutoFlow": 34},
    ]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-auto-drift.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_AUTO
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
    )
    assert master.explicit_state_read_count == 1
    assert slave.explicit_state_read_count == 1
    _assert_only_linkage_calls(master, slave)


async def test_session_refresh_failure_during_frequency_convergence_is_immediate(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    # Fresh capture, first-write gate, master pair and initial slave pair use refreshes 1..4.
    master.fail_after_connect_numbers.add(5)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-refresh.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SESSION_REFRESH
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 3
    assert slave.explicit_state_read_count == 3
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_explicit_read_failure_during_frequency_convergence_is_immediate(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    slave.fail_explicit_state_read_numbers.add(1)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-state-read.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_STATE_READ
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 1
    assert slave.explicit_state_read_count == 1
    _assert_only_linkage_calls(master, slave)


async def test_explicit_clock_drift_during_frequency_convergence_is_immediate(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    slave.explicit_clock_offsets = [10]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-clock.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK_SKEW
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 1
    assert slave.explicit_state_read_count == 1
    _assert_only_linkage_calls(master, slave)


async def test_deadline_during_frequency_settle_preempts_fresh_read(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=120)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-deadline.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_DEADLINE
    assert progress[-1].drift_dimensions == ()
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_frequency_convergence_does_not_open_read_after_local_deadline(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=21)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-local-deadline.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.FREQUENCY,
    )
    assert master.virtual_time.sleep_count == 1
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_frequency_convergence_preserves_full_read_and_rollback_budget(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}

    def clock_near_observation_deadline() -> float:
        return 91.0 if slave.last_written_role is LinkageRole.ASYNC_SLAVE else 0.0

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-admission-budget.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        monotonic_clock=clock_near_observation_deadline,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.FREQUENCY,
    )
    assert master.virtual_time.sleep_count == 0
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_cancel_during_frequency_settle_is_reclassified_and_restored(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    settle_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        settle_started.set()
        await never_resume.wait()

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-cancel.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sleep=blocking_sleep,
    )
    run_task = asyncio.create_task(
        controller.run(await controller.preflight(_spec()))
    )
    await asyncio.wait_for(settle_started.wait(), timeout=1)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_cancel_during_explicit_convergence_read_is_restored(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    slave.pause_explicit_state_read_numbers.add(4)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-read-cancel.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
    )
    run_task = asyncio.create_task(
        controller.run(await controller.preflight(_spec()))
    )
    await asyncio.wait_for(slave.explicit_state_read_paused.wait(), timeout=1)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert master.explicit_state_read_count == 4
    assert slave.explicit_state_read_count == 4
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_safety_trip_during_frequency_settle_preempts_retry_and_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    settle_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        settle_started.set()
        await never_resume.wait()

    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-safety.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        safety_interlock=interlock,
        sleep=blocking_sleep,
    )
    run_task = asyncio.create_task(
        controller.run(await controller.preflight(_spec()))
    )
    await asyncio.wait_for(settle_started.wait(), timeout=1)

    interlock.trip()
    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_VERIFICATION
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    _assert_only_linkage_calls(master, slave)


async def test_safety_trip_during_explicit_convergence_read_cancels_and_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"frequency": 6}
    slave.pause_explicit_state_read_numbers.add(4)
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "retry-read-safety.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        safety_interlock=interlock,
        refresh_sessions_before_critical_reads=True,
    )
    run_task = asyncio.create_task(
        controller.run(await controller.preflight(_spec()))
    )
    await asyncio.wait_for(slave.explicit_state_read_paused.wait(), timeout=1)

    interlock.trip()
    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_VERIFICATION
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert master.explicit_state_read_count == 4
    assert slave.explicit_state_read_count == 4
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_cancel_during_post_master_refresh_reconnects_before_rollback(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_pair(events=events)
    # Refreshes one and two precede all writes. Pause master reconnect three after its
    # durable MASTER intent and adapter write, while the paired slave reconnect can finish.
    master.pause_before_connect_numbers.add(3)
    store = JsonScheduleLinkageJournalStore(tmp_path / "cancel-post-master-refresh.json")
    progress: list[ScheduleLinkageRunProgressEvent] = []
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        progress_observer=progress.append,
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
    assert progress[-1].kind is ScheduleLinkageRunProgressKind.FAILED
    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert progress[-1].drift_dimensions == ()
    _assert_only_linkage_calls(master, slave)


async def test_staged_auto_observation_requires_explicit_critical_reads(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "staged-without-explicit.json"),
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match="requires explicit critical reads",
    ):
        await controller.preflight(_staged_spec())

    assert master.explicit_state_read_count == 0
    assert slave.explicit_state_read_count == 0
    assert master.calls == []
    assert slave.calls == []


async def test_staged_preflight_refreshes_pair_and_uses_explicit_replies(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_staged_pair(events=events)
    # An ordinary report would move only the master's clock and fail the two-second skew gate.
    # The correlated explicit reply deliberately ignores this simulated unsolicited report.
    master.unsolicited_clock_offsets_by_role[LinkageRole.INDEPENDENT] = [10]
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-explicit-preflight.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    preflight = await controller.preflight(_staged_spec())

    assert len(preflight.snapshots) == 2
    assert master.unsolicited_clock_offsets_by_role == {}
    assert master.ordinary_state_read_count == slave.ordinary_state_read_count == 0
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.session_disconnect_count == master.session_connect_count == 1
    assert slave.session_disconnect_count == slave.session_connect_count == 1
    disconnects = [
        events.index(f"session:{device_id}:disconnect")
        for device_id in ("master", "slave")
    ]
    connects = [events.index(f"session:{device_id}:connect") for device_id in ("master", "slave")]
    assert max(disconnects) < min(connects)
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []
    assert store.load() is None


async def test_production_flow_builder_decodes_into_real_staged_preflight(
    tmp_path: Path,
) -> None:
    flow_spec = ScheduleFlowExperimentSpec(
        operation_id="scheduled_flow_production_shape",
        qualification_operation_id="qualified_async_pair",
        master_device_id="master",
        slave_device_id="slave",
        boundary_time="18:15",
        observation_window_seconds=630,
        post_boundary_stability_seconds=300,
    )
    master, slave = await _ready_pair(
        clock=datetime(2026, 8, 26, 18, 10, 20),
        linked_clock_step_seconds=1,
    )
    for device, constant_flow, sine_flow in (
        (master, 31, 35),
        (slave, 32, 40),
    ):
        device.constant_flow = constant_flow
        device.sine_flow = sine_flow
        device.sine_frequency = 30
        await device.set_power(constant_flow)
        await device.set_frequency(20)
        device.calls.clear()
        device.commands.clear()
        patch = next(
            patch
            for patch in flow_spec.temporary_schedule_spec().device_patches
            if patch.device_id == device.device_id
        )
        entries: list[ScheduleEntry] = []
        for slot in patch.slots:
            entry = decode_local_wavemaker_pro_slot_wire(
                slot.wire_bytes,
                slot_index=slot.slot,
            )
            if entry is not None:
                entries.append(entry)
        device.entries = tuple(entries)

    store = JsonScheduleLinkageJournalStore(tmp_path / "production-flow-preflight.json")
    composed = ScheduleFlowExperimentController(
        {"master": master, "slave": slave},
        store,  # type: ignore[arg-type]
        store,  # type: ignore[arg-type]
        store,
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    preflight = await composed._role_controller.preflight(  # noqa: SLF001
        flow_spec.role_observation_spec()
    )

    expectations = {
        snapshot.device_id: snapshot.expectation for snapshot in preflight.snapshots
    }
    assert expectations["master"].before.flow == 31
    assert expectations["master"].after_flow == 35
    assert expectations["slave"].before.flow == 32
    assert expectations["slave"].after_flow == 40
    assert all(
        snapshot.expectation.before.mode == "constant"
        and snapshot.expectation.before.frequency == 5
        and snapshot.expectation.after_mode == "sine"
        and snapshot.expectation.after_frequency == 30
        for snapshot in preflight.snapshots
    )
    assert master.entries[0].parameters["frequency"] == 0
    assert slave.entries[0].parameters["frequency"] == 0
    assert master.ordinary_state_read_count == slave.ordinary_state_read_count == 0
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_preflight_refresh_failure_is_no_read_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    master.fail_after_connect_numbers.add(1)
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-preflight-refresh-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match="session refresh failure",
    ) as captured:
        await controller.preflight(_staged_spec())

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_SESSION_REFRESH
    assert "simulated" not in str(captured.value)
    assert master.session_disconnect_count == slave.session_disconnect_count == 1
    assert master.session_connect_count == slave.session_connect_count == 1
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 0
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []
    assert store.load() is None


async def test_staged_preflight_explicit_read_failure_has_typed_private_reason(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    slave.fail_explicit_state_read_numbers.add(1)
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-preflight-read-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller.preflight(_staged_spec())

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ
    assert "simulated" not in str(captured.value)
    assert "master" not in str(captured.value)
    assert "slave" not in str(captured.value)
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.session_disconnect_count == slave.session_disconnect_count == 2
    assert master.session_connect_count == slave.session_connect_count == 2
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []
    assert store.load() is None


async def test_staged_run_retries_one_transient_refresh_failure_before_writes(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-refresh-retry.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    # Preflight consumed connect #1. Fail run capture connect #2 before the adapter marks the
    # master connected; the one audited retry must recover through connect #3.
    master.fail_before_connect_numbers.add(2)

    result = await controller.run(preflight)

    assert result.schedule_transition_verified is True
    kinds = [event.kind for event in progress]
    assert kinds.count(ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED) == 1
    assert kinds.index(ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED) < kinds.index(
        ScheduleLinkageRunProgressKind.FRESH_CAPTURE_COMPLETED
    )
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_run_retries_one_transient_explicit_read_failure(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-read-retry.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_explicit_state_read_numbers.add(2)

    result = await controller.run(preflight)

    assert result.schedule_transition_verified is True
    assert [event.kind for event in progress].count(
        ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED
    ) == 1
    assert master.explicit_state_read_count >= 3
    assert slave.explicit_state_read_count >= 3
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("failure_kind", ["refresh", "explicit_read"])
async def test_staged_run_exhausts_one_transport_retry_with_typed_no_write_failure(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"staged-run-{failure_kind}-retry-exhausted.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    if failure_kind == "refresh":
        master.fail_before_connect_numbers.update({2, 3})
        expected = ScheduleLinkageRunFailure.FRESH_CAPTURE_SESSION_REFRESH
    else:
        slave.fail_explicit_state_read_numbers.update({2, 3})
        expected = ScheduleLinkageRunFailure.FRESH_CAPTURE_EXPLICIT_STATE_READ

    with pytest.raises(ScheduleLinkagePreflightError):
        await controller.run(preflight)

    assert [event.kind for event in progress].count(
        ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED
    ) == 1
    assert progress[-1].kind is ScheduleLinkageRunProgressKind.FAILED
    assert progress[-1].failure is expected
    assert progress[-1].drift_dimensions == ()
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_does_not_retry_semantic_fresh_capture_drift(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-semantic-drift.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    master.reported_schedule_drift_roles.add(LinkageRole.INDEPENDENT)

    with pytest.raises(ScheduleLinkagePreflightError):
        await controller.run(preflight)

    assert ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED not in {
        event.kind for event in progress
    }
    assert progress[-1].failure is ScheduleLinkageRunFailure.FRESH_CAPTURE_VALIDATION
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_ordinary_run_rejects_disconnect_after_preflight_without_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "ordinary-run-disconnected.json")
    controller = _controller(master, slave, store, progress_observer=progress.append)
    preflight = await controller.preflight(_spec())
    await master.disconnect()

    with pytest.raises(ScheduleLinkagePreflightError, match="disconnected"):
        await controller.run(preflight)

    assert progress[-1].failure is ScheduleLinkageRunFailure.FRESH_CAPTURE_VALIDATION
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_safety_trip_during_fresh_capture_retry_sends_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    settle_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        settle_started.set()
        await never_resume.wait()

    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-retry-safety.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        safety_interlock=interlock,
        sleep=blocking_sleep,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_explicit_state_read_numbers.add(2)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(settle_started.wait(), timeout=1)

    interlock.trip()
    with pytest.raises(ScheduleLinkageApplyError, match="safety authority"):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.FRESH_CAPTURE_SAFETY_INTERLOCK
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_stop_during_fresh_capture_retry_sends_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    settle_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        settle_started.set()
        await never_resume.wait()

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-retry-stop.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=blocking_sleep,
    )
    spec = _staged_spec()
    preflight = await controller.preflight(spec)
    slave.fail_explicit_state_read_numbers.add(2)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(settle_started.wait(), timeout=1)

    assert await controller.stop(spec.operation_id) is True
    with pytest.raises(ScheduleLinkageApplyError, match="stop was requested"):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_safety_trip_during_retry_capture_sends_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-run-retry-capture-safety.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        safety_interlock=interlock,
    )
    preflight = await controller.preflight(_staged_spec())
    # Connect #1 belongs to preflight, #2 fails the first run capture, and #3 is the
    # one permitted retry. Hold that second capture across a safety-epoch change.
    master.fail_before_connect_numbers.add(2)
    master.pause_before_connect_numbers.add(3)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(master.connect_paused.wait(), timeout=1)

    interlock.trip()
    master.resume_connect.set()
    with pytest.raises(ScheduleLinkageApplyError, match="safety authority"):
        await run_task

    kinds = [event.kind for event in progress]
    assert kinds.count(ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED) == 1
    assert ScheduleLinkageRunProgressKind.FRESH_CAPTURE_COMPLETED not in kinds
    assert progress[-1].failure is ScheduleLinkageRunFailure.FRESH_CAPTURE_SAFETY_INTERLOCK
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_stop_during_retry_explicit_read_sends_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-run-retry-capture-stop.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    spec = _staged_spec()
    preflight = await controller.preflight(spec)
    # Explicit read #1 belongs to preflight and #2 fails the first run capture. Hold read #3,
    # from the one permitted retry, across a manual stop request.
    slave.fail_explicit_state_read_numbers.add(2)
    slave.pause_explicit_state_read_numbers.add(3)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(slave.explicit_state_read_paused.wait(), timeout=1)

    assert await controller.stop(spec.operation_id) is True
    slave.resume_explicit_state_read.set()
    with pytest.raises(ScheduleLinkageApplyError, match="stop was requested"):
        await run_task

    kinds = [event.kind for event in progress]
    assert kinds.count(ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED) == 1
    assert ScheduleLinkageRunProgressKind.FRESH_CAPTURE_COMPLETED not in kinds
    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_cancel_during_fresh_capture_retry_sends_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    settle_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        settle_started.set()
        await never_resume.wait()

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-retry-cancel.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=blocking_sleep,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_explicit_state_read_numbers.add(2)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(settle_started.wait(), timeout=1)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_fresh_capture_retry_rechecks_observation_deadline(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()

    async def expire_during_settle(_seconds: float) -> None:
        await asyncio.sleep(0)
        master.virtual_time.value += 60

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-retry-deadline.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=expire_during_settle,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_explicit_state_read_numbers.add(2)

    with pytest.raises(ScheduleLinkagePreflightError, match="session budget"):
        await controller.run(preflight)

    assert progress[-1].failure is ScheduleLinkageRunFailure.FRESH_CAPTURE_DEADLINE
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_run_retry_snapshot_drift_still_fails_confirmation_without_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()

    async def drift_during_settle(seconds: float) -> None:
        await master.virtual_time.sleep(seconds)
        await master.set_frequency(6)
        master.calls.clear()
        master.commands.clear()

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-run-retry-drift.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=drift_during_settle,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_explicit_state_read_numbers.add(2)

    with pytest.raises(ScheduleLinkagePreflightError, match="no role write was sent"):
        await controller.run(preflight)

    assert progress[-1].failure is ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH
    assert progress[-1].drift_dimensions == (ScheduleLinkageDriftDimension.FREQUENCY,)
    assert store.load() is None
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_slave_pair_read_retries_once_without_rewriting_roles(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    timeline: list[str] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-slave-read-retry.json")
    retry_wait_checked = False

    async def inspect_retry_wait(seconds: float) -> None:
        nonlocal retry_wait_checked
        if not retry_wait_checked:
            retry_wait_checked = True
            assert seconds == 2.0
            record = store.load()
            assert record is not None
            assert store.confirms_lease_successor(record)
            assert record.phase is ScheduleLinkagePhase.APPLYING
            assert record.linkage_write_intent_device_ids == ("master", "slave")
            assert record.linked_device_ids == ("master",)
            assert record.detached_device_ids == ()
        await master.virtual_time.sleep(seconds)

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        progress.append(event)
        timeline.append(event.kind.value)

    def observe_sample(sample) -> None:
        samples.append(sample)
        timeline.append("sample")

    controller = _controller(
        master,
        slave,
        store,
        progress_observer=observe_progress,
        sample_observer=observe_sample,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=inspect_retry_wait,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    # Preflight, run capture, first-write gate and master pair verification consume reads 1..4.
    # Fail only the first full-topology read after the async-slave write.
    slave.fail_explicit_state_read_numbers.add(5)

    result = await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
    kinds = [event.kind for event in progress]
    assert result.schedule_transition_verified is True
    assert retry_wait_checked is True
    assert kinds.count(retry_kind) == 1
    assert kinds.index(ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED) < (
        kinds.index(retry_kind)
    ) < kinds.index(ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED)
    assert timeline.index(retry_kind.value) < timeline.index("sample")
    assert samples[0].phase == "before"
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_read_exhausts_exactly_one_retry_and_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-slave-read-retry-exhausted.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.fail_explicit_state_read_numbers.update({5, 6})

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_STATE_READ
    assert master.explicit_state_read_count == 6
    assert slave.explicit_state_read_count == 6
    assert samples == []
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_retry_refresh_failure_is_precise_and_restored(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-slave-read-retry-refresh-failure.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.fail_explicit_state_read_numbers.add(5)
    # Paired connects 1..5 precede the failed full-topology read. Fail only the retry refresh.
    master.fail_before_connect_numbers.add(6)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SESSION_REFRESH
    assert master.explicit_state_read_count == 5
    assert slave.explicit_state_read_count == 5
    assert samples == []
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_master_pair_read_failure_is_not_retried(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-master-read-no-retry.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.fail_explicit_state_read_numbers.add(4)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert progress[-1].failure is ScheduleLinkageRunFailure.MASTER_PAIR_STATE_READ
    assert (
        ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
        not in {event.kind for event in progress}
    )
    assert master.explicit_state_read_count == 4
    assert slave.explicit_state_read_count == 4
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert slave.calls == []
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_retry_does_not_retry_semantic_drift(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-slave-retry-drift.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.fail_explicit_state_read_numbers.add(5)
    slave.reported_state_updates_by_role[LinkageRole.ASYNC_SLAVE] = {"power": 41}

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (ScheduleLinkageDriftDimension.POWER,)
    assert master.explicit_state_read_count == 6
    assert slave.explicit_state_read_count == 6
    assert samples == []
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_does_not_retry_decoded_schema_failure(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-slave-schema-no-retry.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.explicit_state_read_failures[5] = ValueError(
        "simulated decoded schema mismatch"
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert (
        ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
        not in {event.kind for event in progress}
    )
    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_STATE_READ
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_retry_requires_complete_pre_boundary_budget(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    samples = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-slave-retry-budget.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_explicit_state_read_numbers.add(5)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_DEADLINE
    assert master.explicit_state_read_count == 5
    assert slave.explicit_state_read_count == 5
    assert samples == []
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_retry_stop_preempts_second_read_and_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    retry_wait_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def block_retry_wait(seconds: float) -> None:
        assert seconds == 2.0
        retry_wait_started.set()
        await never_resume.wait()

    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-slave-retry-stop.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=block_retry_wait,
    )
    spec = _staged_spec(observation_window_seconds=240)
    preflight = await controller.preflight(spec)
    slave.fail_explicit_state_read_numbers.add(5)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(retry_wait_started.wait(), timeout=1)

    assert await controller.stop(spec.operation_id) is True
    result = await asyncio.wait_for(run_task, timeout=1)

    assert result.stop_reason is ScheduleLinkageStopReason.MANUAL
    assert result.schedule_transition_verified is False
    assert master.explicit_state_read_count == 5
    assert slave.explicit_state_read_count == 5
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_retry_safety_trip_during_refresh_preempts_read(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-slave-retry-refresh-safety.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        safety_interlock=interlock,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.fail_explicit_state_read_numbers.add(5)
    master.pause_before_connect_numbers.add(6)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(master.connect_paused.wait(), timeout=1)

    interlock.trip()
    master.resume_connect.set()
    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await asyncio.wait_for(run_task, timeout=1)

    assert master.explicit_state_read_count == 5
    assert slave.explicit_state_read_count == 5
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_pair_retry_cancel_during_read_restores_before_propagating(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-slave-retry-read-cancel.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(
        _staged_spec(observation_window_seconds=240)
    )
    slave.fail_explicit_state_read_numbers.add(5)
    slave.pause_explicit_state_read_numbers.add(6)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(slave.explicit_state_read_paused.wait(), timeout=1)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=1)

    assert progress[-1].failure is ScheduleLinkageRunFailure.CANCELLED
    assert master.explicit_state_read_count == 6
    assert slave.explicit_state_read_count == 6
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_preflight_cancellation_completes_paired_refresh_without_read_or_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    master.pause_before_connect_numbers.add(1)
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-preflight-refresh-cancel.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    task = asyncio.create_task(controller.preflight(_staged_spec()))
    await master.connect_paused.wait()

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    master.resume_connect.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert master.session_disconnect_count == slave.session_disconnect_count == 1
    assert master.session_connect_count == slave.session_connect_count == 1
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 0
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []
    assert store.load() is None


async def test_staged_preflight_explicit_read_cancellation_is_no_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    slave.pause_explicit_state_read_numbers.add(1)
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-preflight-read-cancel.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    task = asyncio.create_task(controller.preflight(_staged_spec()))
    await slave.explicit_state_read_paused.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert master.session_disconnect_count == slave.session_disconnect_count == 2
    assert master.session_connect_count == slave.session_connect_count == 2
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []
    assert store.load() is None


async def test_staged_first_write_gate_requires_lead_beyond_clock_staleness(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    for device in (master, slave):
        device.base_clock = datetime(2026, 8, 26, 18, 10, 29)
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-short-attribution.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached") as caught:
        await controller.run(preflight)

    cause = caught.value.__cause__
    assert isinstance(cause, ScheduleLinkagePreflightError)
    assert "lead remains for staged Auto transition attribution" in str(cause)
    assert store.load() is None
    assert master.calls == []
    assert slave.calls == []


async def test_staged_role_setup_must_verify_exact_a_before_boundary_window(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    master.advance_monotonic_after_read_seconds = 6
    slave.advance_monotonic_after_read_seconds = 6
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-slow-role-setup.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached") as caught:
        await controller.run(await controller.preflight(_staged_spec()))

    cause = caught.value.__cause__
    assert isinstance(cause, ScheduleLinkageApplyError)
    assert "role verification exceeded the conservative boundary window" in str(cause)
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("shape", ["three_entries", "wrapping_next"])
async def test_staged_auto_observation_requires_exactly_two_nonwrapping_entries(
    tmp_path: Path,
    shape: str,
) -> None:
    master, slave = await _ready_staged_pair(
        next_entry_end="00:00" if shape == "wrapping_next" else "18:13"
    )
    if shape == "three_entries":
        for device in (master, slave):
            device.entries = (
                *device.entries,
                _entry(
                    2,
                    "18:13",
                    "18:14",
                    "pulse",
                    flow=40,
                    frequency=20,
                ),
            )
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / f"staged-{shape}.json"),
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match="non-wrapping two-entry schedule",
    ):
        await controller.preflight(_staged_spec())

    assert master.calls == []
    assert slave.calls == []


async def test_staged_post_role_and_frequency_convergence_ignore_cached_clock_jumps(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    # A role update can expose one participant's batched NowTime five seconds before the other.
    master.clock_offsets_after_role[LinkageRole.MASTER] = 5
    slave.clock_offsets_after_role[LinkageRole.ASYNC_SLAVE] = 5
    # Force frequency convergence, then reproduce independently batched +25s/+22s explicit
    # clock reports. Auto evidence remains the correlated reply's effective tuple.
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": 40},
        None,
        None,
    ]
    master.explicit_clock_offsets = [0, 0, 0, 25, 0]
    slave.explicit_clock_offsets = [0, 0, 0, 0, 22]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-clock-jumps.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(await controller.preflight(_staged_spec()))

    assert result.schedule_transition_verified is True
    assert master.explicit_state_read_count >= 7
    assert slave.explicit_state_read_count == master.explicit_state_read_count
    assert LinkageRole.ASYNC_SLAVE not in slave.reported_state_update_sequences_by_role
    assert all(event.kind is not ScheduleLinkageRunProgressKind.FAILED for event in progress)
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("candidate", [0, 5, 20, 40, 80])
async def test_staged_pins_two_fresh_allowlisted_slave_frequencies_without_writing(
    tmp_path: Path,
    candidate: int,
) -> None:
    master, slave = await _ready_staged_pair()
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    slave.role_frequency_overrides[LinkageRole.ASYNC_SLAVE] = candidate
    reads_when_verified: list[int] = []

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        if event.kind is ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED:
            reads_when_verified.append(slave.explicit_state_read_count)

    store = JsonScheduleLinkageJournalStore(tmp_path / f"staged-pin-{candidate}.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=observe_progress,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(await controller.preflight(_staged_spec()))

    assert result.schedule_transition_verified is True
    # Preflight, run-fresh capture, gate, post-master, initial post-slave, then two independent
    # convergence replies.
    assert reads_when_verified == [7]
    assert store.load() is None
    assert (await slave.get_state()).frequency == 21
    _assert_only_linkage_calls(master, slave)


async def test_staged_pinned_frequency_holds_through_five_minute_after_window(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(next_entry_end="18:20")
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    slave.role_frequency_overrides[LinkageRole.ASYNC_SLAVE] = 5
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-pin-five-minutes.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(
        await controller.preflight(
            _staged_spec(
                observation_window_seconds=600,
                post_boundary_stability_seconds=300,
            )
        )
    )

    assert result.schedule_transition_verified is True
    assert master.virtual_time.value >= 340
    assert store.load() is None
    assert (await slave.get_state()).frequency == 21
    _assert_only_linkage_calls(master, slave)


async def test_staged_rejects_allowlisted_slave_frequency_that_never_stabilizes(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": value}
        for value in (5, 5, 40, 5, 40)
    ]
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-pin-unstable.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_staged_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.FREQUENCY,
    )
    assert slave.explicit_state_read_count == 9
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_rejects_unconfirmed_role_frequency_without_retry(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    slave.role_frequency_overrides[LinkageRole.ASYNC_SLAVE] = 99
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-pin-unconfirmed.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_staged_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.FREQUENCY,
    )
    assert slave.explicit_state_read_count == 5
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_frequency_pin_is_not_available_to_standalone_controller(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair(linked_clock_step_seconds=1)
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": 5}
        for _ in range(5)
    ]
    store = JsonScheduleLinkageJournalStore(tmp_path / "standalone-no-pin.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_spec()))

    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_master_frequency_side_effect_is_never_pinned(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    master.role_frequency_overrides[LinkageRole.MASTER] = 5
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-master-no-pin.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_staged_spec()))

    assert progress[-1].failure is ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_STATE
    assert progress[-1].drift_dimensions == (
        ScheduleLinkageDriftDimension.FREQUENCY,
    )
    assert slave.calls == []
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("changed_frequency", [21, 40])
async def test_staged_frequency_pin_change_during_monitor_fails_and_restores(
    tmp_path: Path,
    changed_frequency: int,
) -> None:
    master, slave = await _ready_staged_pair()
    await master.set_frequency(20)
    await slave.set_frequency(21)
    master.calls.clear()
    slave.calls.clear()
    master.commands.clear()
    slave.commands.clear()
    slave.reported_state_update_sequences_by_role[LinkageRole.ASYNC_SLAVE] = [
        {"frequency": value}
        for value in (5, 5, 5, changed_frequency)
    ]
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"staged-pin-changed-{changed_frequency}.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(await controller.preflight(_staged_spec()))

    assert store.load() is None
    assert (await slave.get_state()).frequency == 21
    _assert_only_linkage_calls(master, slave)


async def test_staged_rejects_nonzero_raw_constant_frequency_at_preflight(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    slave.entries = (
        slave.entries[0].model_copy(
            update={
                "parameters": {
                    **slave.entries[0].parameters,
                    "frequency": 1,
                }
            }
        ),
        slave.entries[1],
    )
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "staged-constant-nonzero.json"),
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match=r"Constant\(0\) to Sine",
    ):
        await controller.preflight(_staged_spec())

    assert master.calls == []
    assert slave.calls == []


async def test_staged_monitor_uses_heartbeat_fenced_reports_after_explicit_role_checks(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    # The slave's effective Auto tuple refreshes one sample after the master's tuple.
    slave.clock_offsets_after_role[LinkageRole.ASYNC_SLAVE] = -1
    samples: list[tuple[float, object]] = []
    read_counts: list[tuple[ScheduleLinkageRunProgressKind, int, int]] = []

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        if event.kind in {
            ScheduleLinkageRunProgressKind.MONITOR_STARTED,
            ScheduleLinkageRunProgressKind.MONITOR_COMPLETED,
        }:
            read_counts.append(
                (
                    event.kind,
                    master.explicit_state_read_count,
                    master.ordinary_state_read_count,
                )
            )

    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-mixed-auto.json")
    controller = _controller(
        master,
        slave,
        store,
        sample_observer=lambda sample: samples.append(
            (master.virtual_time.value, sample)
        ),
        progress_observer=observe_progress,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(await controller.preflight(_staged_spec()))

    assert result.schedule_transition_verified is True
    after = [(observed_at, sample) for observed_at, sample in samples if sample.phase == "after"]
    assert any(
        sample.master.mode == "sine" and sample.slave.mode == "constant"
        for _, sample in after
    )
    assert any(
        sample.master.mode == "sine" and sample.slave.mode == "sine"
        for _, sample in after
    )
    # Preflight, run-fresh capture, the gate and both post-role checks remain reply-only. The
    # active monitor then uses only report-capable reads behind a successful heartbeat fence.
    [
        (started_kind, explicit_at_start, ordinary_at_start),
        (completed_kind, explicit_at_end, ordinary_at_end),
    ] = read_counts
    assert started_kind is ScheduleLinkageRunProgressKind.MONITOR_STARTED
    assert completed_kind is ScheduleLinkageRunProgressKind.MONITOR_COMPLETED
    assert explicit_at_start == explicit_at_end == 5
    assert ordinary_at_start == 0
    assert ordinary_at_end > ordinary_at_start
    assert slave.explicit_state_read_count == master.explicit_state_read_count
    assert master.heartbeat_count == slave.heartbeat_count
    assert master.heartbeat_count > 2
    assert store.load() is None


async def test_staged_monitor_never_requires_explicit_reply_after_active_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = await _ready_staged_pair()
    monitor_started = False
    master_explicit = master.get_explicit_state
    slave_explicit = slave.get_explicit_state

    async def master_explicit_before_monitor_only():
        if monitor_started:
            raise AssertionError("role-active master exposes report-only state")
        return await master_explicit()

    async def slave_explicit_before_monitor_only():
        if monitor_started:
            raise AssertionError("role-active slave exposes report-only state")
        return await slave_explicit()

    def observe_progress(event: ScheduleLinkageRunProgressEvent) -> None:
        nonlocal monitor_started
        if event.kind is ScheduleLinkageRunProgressKind.MONITOR_STARTED:
            monitor_started = True

    monkeypatch.setattr(master, "get_explicit_state", master_explicit_before_monitor_only)
    monkeypatch.setattr(slave, "get_explicit_state", slave_explicit_before_monitor_only)
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-report-only-monitor.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=observe_progress,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(await controller.preflight(_staged_spec()))

    assert result.schedule_transition_verified is True
    assert monitor_started is True
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 5
    assert master.ordinary_state_read_count > 0
    assert slave.ordinary_state_read_count > 0
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("authority", ["stop", "safety", "cancel"])
async def test_staged_monitor_heartbeat_fenced_pair_cleans_up_on_authority_change(
    tmp_path: Path,
    authority: str,
) -> None:
    master, slave = await _ready_staged_pair()
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"staged-fenced-read-{authority}.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        safety_interlock=interlock,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    spec = _staged_spec()
    preflight = await controller.preflight(spec)
    for device in (master, slave):
        device.pause_ordinary_state_read_numbers.add(1)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(master.ordinary_state_read_paused.wait(), timeout=1)
    await asyncio.wait_for(slave.ordinary_state_read_paused.wait(), timeout=1)

    if authority == "stop":
        assert await controller.stop(spec.operation_id) is True
        result = await asyncio.wait_for(run_task, timeout=1)
        assert result.stop_reason is ScheduleLinkageStopReason.MANUAL
        assert result.schedule_transition_verified is False
    elif authority == "safety":
        interlock.trip()
        with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
            await asyncio.wait_for(run_task, timeout=1)
    else:
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=1)

    assert master.ordinary_state_read_cancelled_count == 1
    assert slave.ordinary_state_read_cancelled_count == 1
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_keeps_long_lived_pair_alive_without_control_rewrites(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:15",
        next_entry_end="18:20",
    )
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-long-heartbeat.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(
        await controller.preflight(
            _staged_spec(observation_window_seconds=360)
        )
    )

    assert result.schedule_transition_verified is True
    assert master.heartbeat_count == slave.heartbeat_count
    assert master.heartbeat_count > 250
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_subtracts_slow_acquisition_from_heartbeat_cadence(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair(
        boundary_time="18:13",
        next_entry_end="18:16",
    )
    master.heartbeat_time_advance_seconds = 1.0
    master.ordinary_state_read_time_advance_seconds = 1.0

    async def exact_sleep(seconds: float) -> None:
        master.virtual_time.sleep_count += 1
        await asyncio.sleep(0)
        master.virtual_time.value += seconds

    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-heartbeat-cadence.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=exact_sleep,
    )

    result = await controller.run(
        await controller.preflight(
            _staged_spec(
                observation_window_seconds=240,
                verification_interval_seconds=4,
            )
        )
    )

    monitor_heartbeats = master.heartbeat_started_at[2:]
    assert result.schedule_transition_verified is True
    assert len(monitor_heartbeats) > 10
    assert all(
        later - earlier <= 4.0
        for earlier, later in zip(
            monitor_heartbeats,
            monitor_heartbeats[1:],
            strict=False,
        )
    )
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retries_one_transport_read_on_fresh_pair(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-monitor-read-retry.json")
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_ordinary_state_read_numbers.add(15)

    result = await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
    assert result.schedule_transition_verified is True
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert master.session_connect_count == slave.session_connect_count
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retries_one_heartbeat_failure_on_fresh_pair(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-heartbeat-retry.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_heartbeat_numbers.add(10)

    result = await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
    assert result.schedule_transition_verified is True
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert master.session_connect_count == slave.session_connect_count
    assert store.load() is None
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_second_transport_failure_restores_without_rewrite(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-retry-exhausted.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_ordinary_state_read_numbers.update({15, 16})

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert progress[-1].failure is ScheduleLinkageRunFailure.MONITOR_STATE_READ
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_does_not_retry_schema_or_semantic_read_failure(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-semantic-read.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.ordinary_state_read_failures[1] = ValueError(
        "simulated decoded schema mismatch"
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
    assert retry_kind not in {event.kind for event in progress}
    assert progress[-1].failure is ScheduleLinkageRunFailure.MONITOR_STATE_READ
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_preflight_heartbeat_failure_sends_no_role_write(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    slave.fail_heartbeat_numbers.add(1)
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "staged-preflight-heartbeat.json"),
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkagePreflightError) as caught:
        await controller.preflight(_staged_spec())

    assert caught.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_HEARTBEAT
    assert master.calls == slave.calls == []
    assert master.commands == slave.commands == []


async def test_staged_monitor_persistent_heartbeat_failure_restores_once(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-heartbeat-exhausted.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_heartbeat_numbers.update({10, 11})

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    retry_kind = ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
    assert [event.kind for event in progress].count(retry_kind) == 1
    assert progress[-1].failure is ScheduleLinkageRunFailure.MONITOR_HEARTBEAT
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retry_requires_complete_observation_budget(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-retry-deadline.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.ordinary_state_read_time_advances[15] = 60
    slave.fail_ordinary_state_read_numbers.add(15)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert (
        ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
        not in {event.kind for event in progress}
    )
    assert progress[-1].failure is ScheduleLinkageRunFailure.MONITOR_DEADLINE
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("overrun_stage", ["heartbeat", "state_read"])
async def test_staged_monitor_successful_acquisition_deadline_is_typed(
    tmp_path: Path,
    overrun_stage: str,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / f"staged-monitor-{overrun_stage}-deadline.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    if overrun_stage == "heartbeat":
        master.heartbeat_time_advances[3] = 90
    else:
        master.ordinary_state_read_time_advances[1] = 90

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert progress[-1].failure is ScheduleLinkageRunFailure.MONITOR_DEADLINE
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retry_stop_preempts_refresh_and_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    retry_wait_started = asyncio.Event()
    never_resume = asyncio.Event()

    async def block_retry_wait(seconds: float) -> None:
        if seconds == 2.0:
            retry_wait_started.set()
            await never_resume.wait()
            return
        await master.virtual_time.sleep(seconds)

    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-monitor-retry-stop.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        sleep=block_retry_wait,
    )
    spec = _staged_spec()
    preflight = await controller.preflight(spec)
    slave.fail_ordinary_state_read_numbers.add(15)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(retry_wait_started.wait(), timeout=1)

    assert await controller.stop(spec.operation_id) is True
    result = await asyncio.wait_for(run_task, timeout=1)

    assert result.stop_reason is ScheduleLinkageStopReason.MANUAL
    assert result.schedule_transition_verified is False
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retry_safety_trip_during_pair_refresh_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-retry-safety.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
        safety_interlock=interlock,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_ordinary_state_read_numbers.add(15)
    master.pause_before_connect_numbers.add(6)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(master.connect_paused.wait(), timeout=1)

    interlock.trip()
    master.resume_connect.set()
    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await asyncio.wait_for(run_task, timeout=1)

    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retry_cancellation_during_second_read_restores(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-retry-cancel.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    slave.fail_ordinary_state_read_numbers.add(15)
    slave.pause_ordinary_state_read_numbers.add(16)
    run_task = asyncio.create_task(controller.run(preflight))
    await asyncio.wait_for(slave.ordinary_state_read_paused.wait(), timeout=1)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=1)

    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_monitor_retry_rechecks_exact_durable_active_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = await _ready_staged_pair()
    progress: list[ScheduleLinkageRunProgressEvent] = []
    store = JsonScheduleLinkageJournalStore(
        tmp_path / "staged-monitor-retry-durable.json"
    )
    controller = _controller(
        master,
        slave,
        store,
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await controller.preflight(_staged_spec())
    original_confirms = store.confirms_lease_successor
    confirmation_calls = 0

    def reject_second_confirmation(record: ScheduleLinkageRecord) -> bool:
        nonlocal confirmation_calls
        confirmation_calls += 1
        if confirmation_calls == 2:
            return False
        return original_confirms(record)

    monkeypatch.setattr(store, "confirms_lease_successor", reject_second_confirmation)
    slave.fail_ordinary_state_read_numbers.add(15)

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached"):
        await controller.run(preflight)

    assert confirmation_calls >= 2
    assert progress[-1].failure is ScheduleLinkageRunFailure.MONITOR
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


@pytest.mark.parametrize("failure", ["unknown", "after_then_before"])
async def test_staged_master_invalid_transition_fails_with_exact_linkage_rollback(
    tmp_path: Path,
    failure: str,
) -> None:
    events: list[str] = []
    master, slave = await _ready_staged_pair(
        linked_clock_step_seconds=10 if failure == "after_then_before" else 1,
        events=events,
    )
    before = {"AutoMode": "constant", "AutoFlow": 30, "AutoFreq": 5}
    after = {"AutoMode": "sine", "AutoFlow": 45, "AutoFreq": 40}
    invalid = (
        {"AutoMode": "pulse", "AutoFlow": 31, "AutoFreq": 5}
        if failure == "unknown"
        else after
    )
    master.reported_auto_update_sequences_by_role[LinkageRole.MASTER] = [
        None,
        None,
        *([None] if failure == "after_then_before" else []),
        invalid,
        *([before] if failure == "after_then_before" else []),
    ]
    store = JsonScheduleLinkageJournalStore(tmp_path / f"staged-{failure}.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached") as caught:
        await controller.run(await controller.preflight(_staged_spec()))

    cause = caught.value.__cause__
    assert isinstance(cause, ScheduleLinkageApplyError)
    expected = (
        "reported an unknown staged Auto tuple"
        if failure == "unknown"
        else "returned to its prior entry"
    )
    assert expected in str(cause)
    assert [event for event in events if event.startswith("write:")] == [
        "write:master:master",
        "write:slave:async_slave",
        "write:slave:independent",
        "write:master:independent",
    ]
    assert [call[1] for call in master.calls] == [
        LinkageRole.MASTER,
        LinkageRole.INDEPENDENT,
    ]
    assert [call[1] for call in slave.calls] == [
        LinkageRole.ASYNC_SLAVE,
        LinkageRole.INDEPENDENT,
    ]
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_auto_transition_rejects_role_adjacent_after_evidence(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    master.reported_auto_update_sequences_by_role[LinkageRole.MASTER] = [
        None,
        None,
        {"AutoMode": "sine", "AutoFlow": 45, "AutoFreq": 40},
    ]
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-early-after.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    with pytest.raises(ScheduleLinkageApplyError, match="roles were detached") as caught:
        await controller.run(await controller.preflight(_staged_spec()))

    cause = caught.value.__cause__
    assert isinstance(cause, ScheduleLinkageApplyError)
    assert "before the conservative boundary window" in str(cause)
    assert store.load() is None
    assert (await master.get_state()).linkage is LinkageRole.INDEPENDENT
    assert (await slave.get_state()).linkage is LinkageRole.INDEPENDENT
    _assert_only_linkage_calls(master, slave)


async def test_staged_slave_prior_tuple_stabilizes_while_master_enters_next_entry(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    # Keep the declared B entry intact, but model firmware that holds the exact captured A tuple.
    slave.observed_sine_mode = "constant"
    slave.sine_flow = 35
    slave.sine_frequency = 5
    samples = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "staged-slave-prior.json")
    controller = _controller(
        master,
        slave,
        store,
        sample_observer=samples.append,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(await controller.preflight(_staged_spec()))

    assert result.schedule_transition_verified is True
    terminal = next(sample for sample in reversed(samples) if sample.phase == "after")
    assert (terminal.master.mode, terminal.master.flow, terminal.master.frequency) == (
        "sine",
        45,
        40,
    )
    assert (terminal.slave.mode, terminal.slave.flow, terminal.slave.frequency) == (
        "constant",
        35,
        5,
    )
    assert master.virtual_time.value >= 42
    assert store.load() is None


async def test_staged_transition_requires_two_identical_after_samples(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    samples: list[tuple[float, object]] = []
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "staged-two-after.json"),
        sample_observer=lambda sample: samples.append(
            (master.virtual_time.value, sample)
        ),
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(
        await controller.preflight(
            _staged_spec(post_boundary_stability_seconds=0)
        )
    )

    assert result.schedule_transition_verified is True
    after = [(observed_at, sample) for observed_at, sample in samples if sample.phase == "after"]
    assert len(after) == 2
    assert after[0][1].model_dump(exclude={"observed_at"}) == after[1][1].model_dump(
        exclude={"observed_at"}
    )
    assert after[1][0] - after[0][0] == 1
    assert master.virtual_time.value >= 41


async def test_staged_transition_resets_requested_stability_when_after_tuple_changes(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_staged_pair()
    slave.second_sine_boundary = datetime(2026, 8, 26, 18, 11, 1)
    slave.next_sine_flow = 44
    slave.next_sine_frequency = 80
    samples: list[tuple[float, object]] = []
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "staged-stability-reset.json"),
        sample_observer=lambda sample: samples.append(
            (master.virtual_time.value, sample)
        ),
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    result = await controller.run(
        await controller.preflight(
            _staged_spec(post_boundary_stability_seconds=3)
        )
    )

    assert result.schedule_transition_verified is True
    after = [(observed_at, sample) for observed_at, sample in samples if sample.phase == "after"]
    assert [(observed_at, sample.slave.flow) for observed_at, sample in after[:2]] == [
        (40, 45),
        (41, 44),
    ]
    stable = [observed_at for observed_at, sample in after if sample.slave.flow == 44]
    assert stable[-1] - stable[0] == 3
    assert len(stable) >= 2
    assert master.virtual_time.value >= 44


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
    proof = await _external_disarm_proof(record, master, slave)

    assert (
        await controller.finalize_externally_disarmed(
            record.operation_id,
            proof=proof,
        )
        is True
    )

    assert store.load() is None
    assert master.calls == []
    assert slave.calls == []


async def test_external_disarm_closure_consumes_proof_without_another_session(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "external-disarm-refresh.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
    )
    preflight = await controller.preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.APPLYING,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=preflight.spec.observation_window_seconds),
        linkage_write_intent_device_ids=("master", "slave"),
        linked_device_ids=("master",),
    )
    with store.lease():
        store.create(record)
    for device, snapshot in zip((master, slave), preflight.snapshots, strict=True):
        await device.write_target(
            DeviceTarget(
                enabled=snapshot.enabled,
                power=snapshot.power,
                mode=snapshot.mode,
                frequency=snapshot.frequency,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            )
        )
        device.calls.clear()
        device.commands.clear()
    proof = await _external_disarm_proof(record, master, slave)
    master.fail_after_connect_numbers.add(1)

    assert (
        await controller.finalize_externally_disarmed(
            record.operation_id,
            proof=proof,
        )
        is True
    )

    assert store.load() is None
    assert master.session_disconnect_count == master.session_connect_count == 0
    assert slave.session_disconnect_count == slave.session_connect_count == 0
    assert master.calls == []
    assert slave.calls == []


async def test_external_disarm_proof_mismatches_keep_the_role_journal(
    tmp_path: Path,
) -> None:
    master, slave = await _ready_pair()
    store = JsonScheduleLinkageJournalStore(tmp_path / "external-disarm-refresh-failure.json")
    controller = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
    )
    preflight = await controller.preflight(_spec())
    now = datetime.now(UTC)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.APPLYING,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=preflight.spec.observation_window_seconds),
        linkage_write_intent_device_ids=("master", "slave"),
        linked_device_ids=("master",),
    )
    with store.lease():
        store.create(record)
    for device, snapshot in zip((master, slave), preflight.snapshots, strict=True):
        await device.write_target(
            DeviceTarget(
                enabled=snapshot.enabled,
                power=snapshot.power,
                mode=snapshot.mode,
                frequency=snapshot.frequency,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            )
        )
        device.calls.clear()
        device.commands.clear()
    proof = await _external_disarm_proof(record, master, slave)
    changed_slave = proof.states[1].model_copy(
        update={"power": proof.states[1].power + 1}
    )
    changed_binding = proof.states[0].model_copy(
        update={"physical_binding": proof.states[1].physical_binding}
    )
    mismatches = (
        proof.model_copy(update={"operation_id": "different_operation"}),
        proof.model_copy(update={"states": tuple(reversed(proof.states))}),
        proof.model_copy(update={"states": (changed_binding, proof.states[1])}),
        proof.model_copy(update={"states": (proof.states[0], changed_slave)}),
    )

    for mismatched in mismatches:
        with pytest.raises(ScheduleLinkageRollbackError):
            await controller.finalize_externally_disarmed(
                record.operation_id,
                proof=mismatched,
            )
        assert store.load() == record

    assert master.session_disconnect_count == master.session_connect_count == 0
    assert slave.session_disconnect_count == slave.session_connect_count == 0
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


async def test_staged_restart_recovery_accepts_role_frequency_only_to_detach(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    master, slave = await _ready_staged_pair(events=events)
    await master.set_frequency(20)
    await slave.set_frequency(21)
    store = _RecordingStore(tmp_path / "staged-pin-restart.json", events)
    setup = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )
    preflight = await setup.preflight(_staged_spec())
    slave.role_frequency_overrides[LinkageRole.ASYNC_SLAVE] = 5
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
    restarted = _controller(
        master,
        slave,
        store,
        refresh_sessions_before_critical_reads=True,
        owned_staged_auto_transition_observation=True,
    )

    assert await restarted.recover_pending() is True

    assert store.load() is None
    assert events.index("write:slave:independent") < events.index(
        "write:master:independent"
    )
    assert (await slave.get_state()).frequency == 21
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

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller.preflight(_spec())

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE
    assert master.calls == []
    assert slave.calls == []


async def test_boundary_outside_window_fails_before_write(tmp_path: Path) -> None:
    master, slave = await _ready_pair(clock=datetime(2026, 8, 26, 17, 56))
    controller = _controller(
        master,
        slave,
        JsonScheduleLinkageJournalStore(tmp_path / "far.json"),
    )

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match="outside the observation window",
    ) as captured:
        await controller.preflight(_spec())

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW
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

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match="verification and rollback reserve",
    ) as captured:
        await controller.preflight(_spec(observation_window_seconds=100))

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW
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

    with pytest.raises(
        ScheduleLinkagePreflightError,
        match="must differ from master",
    ) as captured:
        await controller.preflight(_spec())

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_PAIR_EVIDENCE
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

    with pytest.raises(ScheduleLinkagePreflightError, match="pair skew") as captured:
        await controller.preflight(_spec(maximum_clock_skew_seconds=2))

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_CLOCK
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

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller.preflight(_spec())

    expected = (
        ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE
        if defect == "disabled"
        else ScheduleLinkageRunFailure.PREFLIGHT_PAIR_EVIDENCE
    )
    assert captured.value.failure is expected
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
