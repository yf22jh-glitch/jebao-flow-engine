from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from jebao_flow.devices.identity import PhysicalDeviceBinding, configuration_fingerprint
from jebao_flow.devices.linkage import (
    LinkageDiagnosticEvent,
    LinkageDiagnosticEventKind,
    LinkageForwardFailureCategory,
    LinkageRollbackError,
    LinkageSafetyInterlock,
    LinkageTransactionBusyError,
    LinkageTransactionError,
    LinkageTransactionRecord,
    TemporaryLinkageController,
    schedule_structure_fingerprint,
)
from jebao_flow.devices.schedule_flow_experiment import (
    ScheduleFlowExperimentController,
    ScheduleFlowExperimentSpec,
    ScheduleFlowFailureCategory,
    ScheduleFlowOutcome,
    ScheduleFlowStage,
    ScheduleFlowStageEvent,
    classify_schedule_flow_sample,
)
from jebao_flow.devices.schedule_linkage import (
    ScheduleAutoEvidence,
    ScheduleLinkageBusyError,
    ScheduleLinkageDriftDimension,
    ScheduleLinkageExternalDisarmProof,
    ScheduleLinkageExternalDisarmState,
    ScheduleLinkagePreflightError,
    ScheduleLinkageResult,
    ScheduleLinkageRunFailure,
    ScheduleLinkageRunProgressEvent,
    ScheduleLinkageRunProgressKind,
    ScheduleLinkageSample,
    ScheduleLinkageStopReason,
)
from jebao_flow.devices.schedule_transaction import (
    TemporaryScheduleErrorCode,
    TemporaryScheduleKind,
    TemporaryScheduleObserverUnstoppableError,
    TemporaryScheduleRecord,
    TemporaryScheduleResult,
    TemporaryScheduleSpec,
)
from jebao_flow.protocol.models import (
    DeviceCapabilities,
    DeviceSchedule,
    DeviceState,
    DeviceTarget,
    LinkageRole,
    ScheduleEntry,
)
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    decode_local_wavemaker_pro_slot_wire,
)
from jebao_flow.safety.limits import PowerLimits


def _spec(**updates: object) -> ScheduleFlowExperimentSpec:
    values: dict[str, object] = {
        "operation_id": "scheduled_slave_flow",
        "qualification_operation_id": "qualified_async_pair",
        "master_device_id": "master",
        "slave_device_id": "slave",
        "boundary_time": "12:34",
    }
    values.update(updates)
    return ScheduleFlowExperimentSpec(**values)


def _after_sample(
    *,
    master_flow: int = 35,
    slave_flow: int = 40,
    master_mode: str = "sine",
    slave_mode: str = "sine",
    master_frequency: int = 30,
    slave_frequency: int = 30,
    phase: str = "after",
) -> ScheduleLinkageSample:
    return ScheduleLinkageSample(
        observed_at=datetime.now(UTC),
        phase=phase,
        master=ScheduleAutoEvidence(
            mode=master_mode,
            flow=master_flow,
            frequency=master_frequency,
        ),
        slave=ScheduleAutoEvidence(
            mode=slave_mode,
            flow=slave_flow,
            frequency=slave_frequency,
        ),
        master_manual_power=31,
        slave_manual_power=32,
        master_linkage=LinkageRole.MASTER,
        slave_linkage=LinkageRole.ASYNC_SLAVE,
    )


def test_outer_diagnostic_events_are_forwarded_without_exception_payloads() -> None:
    events: list[LinkageDiagnosticEvent] = []
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
        diagnostic_event_observer=events.append,
    )
    event = LinkageDiagnosticEvent(
        kind=LinkageDiagnosticEventKind.FORWARD_FAILED,
        occurred_at=datetime.now(UTC),
        forward_failure=LinkageForwardFailureCategory.CONTROL_STATE_MISMATCH,
    )

    controller._on_diagnostic_event(event)  # noqa: SLF001

    assert events == [event]
    assert set(event.model_dump(exclude_none=True)) == {
        "kind",
        "occurred_at",
        "forward_failure",
    }


def test_role_progress_is_embedded_without_identity_or_raw_value_fields() -> None:
    now = datetime.now(UTC)
    progress = ScheduleLinkageRunProgressEvent(
        kind=ScheduleLinkageRunProgressKind.FAILED,
        occurred_at=now,
        failure=ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH,
        drift_dimensions=(ScheduleLinkageDriftDimension.BEFORE_AUTO_FREQUENCY,),
    )
    event = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
        occurred_at=now,
        role_progress=progress,
    )

    encoded = event.model_dump_json()

    assert event.role_progress == progress
    assert "confirmation_mismatch" in encoded
    assert "before_auto_frequency" in encoded
    assert "device_id" not in encoded
    assert "requested_value" not in encoded


def test_role_progress_cannot_be_attached_to_an_unrelated_outer_stage() -> None:
    now = datetime.now(UTC)
    progress = ScheduleLinkageRunProgressEvent(
        kind=ScheduleLinkageRunProgressKind.FRESH_CAPTURE_STARTED,
        occurred_at=now,
    )

    with pytest.raises(ValidationError, match="restricted to the role observation"):
        ScheduleFlowStageEvent(
            stage=ScheduleFlowStage.TIMER_ON_ARMED,
            occurred_at=now,
            role_progress=progress,
        )


def test_inner_role_progress_is_forwarded_with_its_exact_timestamp() -> None:
    events: list[ScheduleFlowStageEvent] = []
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
        stage_event_observer=events.append,
    )
    progress = ScheduleLinkageRunProgressEvent(
        kind=ScheduleLinkageRunProgressKind.FRESH_CAPTURE_STARTED,
        occurred_at=datetime.now(UTC),
    )

    controller._observe_role_progress(progress)  # noqa: SLF001

    assert events == [
        ScheduleFlowStageEvent(
            stage=ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
            occurred_at=progress.occurred_at,
            role_progress=progress,
        )
    ]


def test_plan_builds_two_distinguishable_segments_and_clears_every_other_slot() -> None:
    spec = _spec()

    temporary = spec.temporary_schedule_spec()
    master, slave = temporary.device_patches

    assert (master.device_id, slave.device_id) == ("master", "slave")
    assert len(master.slots) == len(slave.slots) == 48
    master_before = decode_local_wavemaker_pro_slot_wire(master.slots[0].wire_bytes)
    master_after = decode_local_wavemaker_pro_slot_wire(master.slots[1].wire_bytes, slot_index=1)
    slave_before = decode_local_wavemaker_pro_slot_wire(slave.slots[0].wire_bytes)
    slave_after = decode_local_wavemaker_pro_slot_wire(slave.slots[1].wire_bytes, slot_index=1)
    assert master_before is not None and master_after is not None
    assert slave_before is not None and slave_after is not None
    assert (master_before.start, master_before.end, master_before.mode) == (
        "00:00",
        "12:34",
        "constant",
    )
    assert (master_after.start, master_after.end, master_after.mode) == (
        "12:34",
        "23:59",
        "sine",
    )
    assert (
        master_before.parameters["flow"],
        slave_before.parameters["flow"],
        master_after.parameters["flow"],
        slave_after.parameters["flow"],
    ) == (31, 32, 35, 40)
    assert all(slot.wire_bytes == LOCAL_WAVEMAKER_PRO_UNUSED_EE for slot in master.slots[2:])
    assert all(slot.wire_bytes == LOCAL_WAVEMAKER_PRO_UNUSED_EE for slot in slave.slots[2:])


def test_plan_binds_five_minute_stability_to_role_observation() -> None:
    spec = _spec(post_boundary_stability_seconds=300)

    role = spec.role_observation_spec()
    outer = spec.outer_linkage_spec()
    temporary = spec.temporary_schedule_spec()

    assert role.post_boundary_stability_seconds == 300
    assert role.observe_slave_after_tuple_variance is True
    assert role.observation_window_seconds == 600
    assert outer.bootstrap_active_schedule is True
    assert outer.duration_seconds == 840
    assert temporary.observation_timeout_seconds == 720
    assert temporary.recovery_authority_seconds == 2100


def test_plan_requires_stable_evidence_to_finish_before_2359() -> None:
    assert _spec(boundary_time="23:53").boundary_time == "23:53"

    with pytest.raises(ValidationError, match="before the 23:59 field end"):
        _spec(boundary_time="23:54")


def test_pause_stage_names_keep_the_deployed_v3_wire_values() -> None:
    assert ScheduleFlowStage("outer_bootstrap_started") is ScheduleFlowStage.OUTER_PAUSE_STARTED
    assert (
        ScheduleFlowStage("outer_bootstrap_completed")
        is ScheduleFlowStage.OUTER_PAUSE_COMPLETED
    )
    assert (
        ScheduleFlowFailureCategory("outer_bootstrap")
        is ScheduleFlowFailureCategory.OUTER_PAUSE
    )


class _PauseDevice:
    def __init__(self, device_id: str, *, power: int, events: list[str]) -> None:
        self.device_id = device_id
        self.events = events
        self.targets: list[DeviceTarget] = []
        self.capabilities = DeviceCapabilities()
        self.schedule = DeviceSchedule(enabled=True)
        self.state = DeviceState(
            online=True,
            enabled=True,
            power=power,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
            schedule=self.schedule,
        )

    async def get_state(self) -> DeviceState:
        self.events.append(f"{self.device_id}:read")
        return self.state

    async def get_explicit_state(self) -> DeviceState:
        return await self.get_state()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        assert guard is None or guard() is True
        self.events.append(f"{self.device_id}:write:{target.power}")
        self.targets.append(target)
        self.state = DeviceState(
            online=True,
            enabled=target.enabled,
            power=target.power,
            mode=target.mode or self.state.mode,
            frequency=target.frequency,
            linkage=target.linkage,
            timer_enabled=target.timer_enabled,
            schedule=self.schedule,
        )

    async def disconnect(self) -> None:
        self.events.append(f"{self.device_id}:disconnect")

    async def connect(self) -> None:
        self.events.append(f"{self.device_id}:connect")


class _ExplicitSequenceDevice(_PauseDevice):
    def __init__(
        self,
        device_id: str,
        *,
        states: list[DeviceState],
        events: list[str],
    ) -> None:
        super().__init__(device_id, power=states[0].power, events=events)
        self._explicit_states = states
        self.state = states[0]
        assert states[0].schedule is not None
        self.schedule = states[0].schedule
        self.explicit_state_read_count = 0

    async def get_explicit_state(self) -> DeviceState:
        self.events.append(f"{self.device_id}:explicit")
        index = min(self.explicit_state_read_count, len(self._explicit_states) - 1)
        self.explicit_state_read_count += 1
        self.state = self._explicit_states[index]
        return self.state


def _staged_entries(
    spec: ScheduleFlowExperimentSpec,
    device_id: str,
) -> tuple[ScheduleEntry, ...]:
    patch = next(
        patch
        for patch in spec.temporary_schedule_spec().device_patches
        if patch.device_id == device_id
    )
    entries = []
    for slot in patch.slots:
        entry = decode_local_wavemaker_pro_slot_wire(
            slot.wire_bytes,
            slot_index=slot.slot,
        )
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _staged_state(
    spec: ScheduleFlowExperimentSpec,
    device_id: str,
    *,
    clock: datetime,
    timer_enabled: bool,
    auto_mode: str = "constant",
    auto_flow: int | None = None,
    auto_frequency: int = 5,
) -> DeviceState:
    is_master = device_id == spec.master_device_id
    power = spec.master_before_flow if is_master else spec.slave_before_flow
    if auto_flow is None:
        auto_flow = power
    return DeviceState(
        online=True,
        enabled=True,
        power=power,
        mode="constant",
        frequency=spec.safe_frequency,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=timer_enabled,
        schedule=DeviceSchedule(
            enabled=timer_enabled,
            device_local_time=clock,
            entries=_staged_entries(spec, device_id),
        ),
        observed_attributes={
            "AutoMode": auto_mode,
            "AutoFlow": auto_flow,
            "AutoFreq": auto_frequency,
            "AutoFeedTime": 15,
        },
    )


def _staged_controller(
    master: _PauseDevice,
    slave: _PauseDevice,
) -> ScheduleFlowExperimentController:
    store = cast(object, _UnusedStore())
    return ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )


def _pause_record(
    spec: ScheduleFlowExperimentSpec,
    master: _PauseDevice,
    slave: _PauseDevice,
) -> LinkageTransactionRecord:
    fingerprint = schedule_structure_fingerprint(master.schedule)
    snapshots = tuple(
        SimpleNamespace(
            device_id=device.device_id,
            enabled=True,
            power=device.state.power,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
            schedule_fingerprint=fingerprint,
        )
        for device in (master, slave)
    )
    return cast(
        LinkageTransactionRecord,
        SimpleNamespace(
            operation_id=spec.operation_id,
            spec=spec.outer_linkage_spec(),
            snapshots=snapshots,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
    )


def _arm_pause_controller(
    controller: ScheduleFlowExperimentController,
    spec: ScheduleFlowExperimentSpec,
) -> None:
    controller._experiment_spec = spec  # noqa: SLF001
    controller._safety_epoch = controller._safety_interlock.epoch  # noqa: SLF001
    controller._stop_event = asyncio.Event()  # noqa: SLF001
    controller._operation_monotonic_deadline = (  # noqa: SLF001
        asyncio.get_running_loop().time() + 60
    )


def _future_deadline(seconds: float = 60) -> float:
    return asyncio.get_running_loop().time() + seconds


async def test_prequalified_pause_writes_one_safe_frame_and_verifies_fresh_sessions() -> None:
    events: list[str] = []
    authorizations: list[tuple[object, ...]] = []
    master = _PauseDevice("master", power=44, events=events)
    slave = _PauseDevice("slave", power=43, events=events)
    store = cast(object, _UnusedStore())

    def authorize(_spec, snapshots) -> None:
        events.append("authorize")
        authorizations.append(snapshots)

    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=authorize,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    spec = _spec()
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    staged = await controller._stage_devices(record)  # noqa: SLF001

    assert staged is record
    assert len(authorizations) == 5
    assert [target.power for target in master.targets] == [31]
    assert [target.power for target in slave.targets] == [32]
    assert all(
        target.mode == "constant"
        and target.frequency == 20
        and target.linkage is LinkageRole.INDEPENDENT
        and target.timer_enabled is False
        for target in (*master.targets, *slave.targets)
    )
    assert events == [
        "master:read",
        "slave:read",
        "authorize",
        "authorize",
        "authorize",
        "master:write:31",
        "master:disconnect",
        "master:connect",
        "master:read",
        "authorize",
        "authorize",
        "slave:write:32",
        "slave:disconnect",
        "slave:connect",
        "slave:read",
    ]


async def test_pause_receipt_expiry_before_slave_frame_fails_without_requalification() -> None:
    events: list[str] = []
    authorization_count = 0
    master = _PauseDevice("master", power=44, events=events)
    slave = _PauseDevice("slave", power=43, events=events)
    store = cast(object, _UnusedStore())

    def authorize(_spec, _snapshots) -> None:
        nonlocal authorization_count
        authorization_count += 1
        if authorization_count == 4:
            raise RuntimeError("receipt expired")

    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=authorize,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    spec = _spec()
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(RuntimeError, match="receipt expired"):
        await controller._stage_devices(record)  # noqa: SLF001

    assert [target.power for target in master.targets] == [31]
    assert slave.targets == []
    assert all(target.power != 30 for target in master.targets)


async def test_timer_on_arm_is_proven_on_fresh_streams_and_leaves_clean_sessions() -> None:
    events: list[str] = []
    master = _PauseDevice("master", power=31, events=events)
    slave = _PauseDevice("slave", power=32, events=events)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    spec = _spec()
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    await controller._arm_temporary_schedule(  # noqa: SLF001
        record,
        monotonic_deadline=_future_deadline(),
    )

    assert [target.timer_enabled for target in master.targets] == [True]
    assert [target.timer_enabled for target in slave.targets] == [True]
    assert events == [
        "master:write:31",
        "slave:write:32",
        "master:disconnect",
        "slave:disconnect",
        "master:connect",
        "slave:connect",
        "master:read",
        "slave:read",
        "master:disconnect",
        "slave:disconnect",
        "master:connect",
        "slave:connect",
    ]


@pytest.mark.parametrize(
    ("clock", "allowed"),
    (
        (datetime(2026, 8, 26, 12, 30, 59, 999000), True),
        (datetime(2026, 8, 26, 12, 31), False),
    ),
)
async def test_timer_arm_budget_requires_strictly_more_than_180_fresh_seconds(
    clock: datetime,
    allowed: bool,
) -> None:
    events: list[str] = []
    spec = _spec()
    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=False,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=False,
    )
    master = _ExplicitSequenceDevice("master", states=[master_state], events=events)
    slave = _ExplicitSequenceDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    if allowed:
        await controller._assert_timer_arm_budget(record)  # noqa: SLF001
    else:
        with pytest.raises(ScheduleLinkagePreflightError) as captured:
            await controller._assert_timer_arm_budget(record)  # noqa: SLF001
        assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW

    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.targets == slave.targets == []
    assert events == [
        "master:disconnect",
        "slave:disconnect",
        "master:connect",
        "slave:connect",
        "master:explicit",
        "slave:explicit",
    ]


@pytest.mark.parametrize(
    "unsafe_flow",
    (
        {"master_before_flow": 46},
        {"slave_before_flow": 46},
        {"master_after_flow": 46},
        {"slave_after_flow": 46},
    ),
)
async def test_all_owned_schedule_flows_are_guarded_before_timer_on(
    unsafe_flow: dict[str, int],
) -> None:
    events: list[str] = []
    spec = _spec(**unsafe_flow)
    clock = datetime(2026, 8, 26, 12, 30)
    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=False,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=False,
    )
    master = _ExplicitSequenceDevice("master", states=[master_state], events=events)
    slave = _ExplicitSequenceDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._assert_timer_arm_budget(record)  # noqa: SLF001

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.targets == slave.targets == []


async def test_staged_a_convergence_retries_only_well_formed_auto_mismatch(
    monkeypatch,
) -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)
    master_mismatch = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
        auto_mode="sine",
        auto_flow=35,
        auto_frequency=30,
    )
    master_exact = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    )
    slave_exact = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = _ExplicitSequenceDevice(
        "master",
        states=[master_mismatch, master_exact],
        events=events,
    )
    slave = _ExplicitSequenceDevice(
        "slave",
        states=[slave_exact, slave_exact],
        events=events,
    )
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)
    monkeypatch.setattr(
        "jebao_flow.devices.schedule_flow_experiment._STAGED_A_CONVERGENCE_RETRY_SECONDS",
        0,
    )

    await controller._await_staged_current_a(  # noqa: SLF001
        record,
        monotonic_deadline=_future_deadline(),
    )

    assert master.explicit_state_read_count == slave.explicit_state_read_count == 2
    assert master.targets == slave.targets == []
    assert events.count("master:disconnect") == events.count("slave:disconnect") == 2
    assert events.count("master:connect") == events.count("slave:connect") == 2


@pytest.mark.parametrize(
    ("auto_flow", "limits", "power_step"),
    (
        (46, PowerLimits(), 1),
        (29, PowerLimits(), 1),
        (34, PowerLimits(), 5),
    ),
)
async def test_unsafe_auto_flow_fails_without_convergence_retry(
    auto_flow: int,
    limits: PowerLimits,
    power_step: int,
) -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)
    unsafe = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
        auto_mode="sine",
        auto_flow=auto_flow,
        auto_frequency=30,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = _ExplicitSequenceDevice(
        "master",
        states=[unsafe, unsafe],
        events=events,
    )
    master.capabilities = DeviceCapabilities(
        power_limits=limits,
        power_step=power_step,
    )
    slave = _ExplicitSequenceDevice(
        "slave",
        states=[slave_state, slave_state],
        events=events,
    )
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._await_staged_current_a(  # noqa: SLF001
            record,
            monotonic_deadline=_future_deadline(),
        )

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.targets == slave.targets == []


@pytest.mark.parametrize(
    ("drift", "expected_failure"),
    (
        ("timer", ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE),
        ("linkage", ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE),
        ("schedule", ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN),
        ("clock", ScheduleLinkageRunFailure.PREFLIGHT_CLOCK),
    ),
)
async def test_fatal_slave_drift_preempts_master_auto_convergence_retry(
    monkeypatch,
    drift: str,
    expected_failure: ScheduleLinkageRunFailure,
) -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)
    master_mismatch = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
        auto_mode="sine",
        auto_flow=35,
        auto_frequency=30,
    )
    slave_exact = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    if drift == "timer":
        slave_drift = slave_exact.model_copy(update={"timer_enabled": False})
    elif drift == "linkage":
        slave_drift = slave_exact.model_copy(update={"linkage": LinkageRole.MASTER})
    elif drift == "schedule":
        assert slave_exact.schedule is not None
        slave_drift = slave_exact.model_copy(
            update={"schedule": slave_exact.schedule.model_copy(update={"entries": ()})}
        )
    else:
        assert drift == "clock"
        assert slave_exact.schedule is not None
        slave_drift = slave_exact.model_copy(
            update={
                "schedule": slave_exact.schedule.model_copy(
                    update={"device_local_time": clock + timedelta(seconds=3)}
                )
            }
        )
    master = _ExplicitSequenceDevice(
        "master",
        states=[master_mismatch, master_mismatch],
        events=events,
    )
    slave = _ExplicitSequenceDevice(
        "slave",
        states=[slave_drift, slave_drift],
        events=events,
    )
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)
    monkeypatch.setattr(
        "jebao_flow.devices.schedule_flow_experiment._STAGED_A_CONVERGENCE_RETRY_SECONDS",
        0,
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._await_staged_current_a(  # noqa: SLF001
            record,
            monotonic_deadline=_future_deadline(),
        )

    assert captured.value.failure is expected_failure
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.targets == slave.targets == []


@pytest.mark.parametrize(
    "observed_attributes",
    (
        {"AutoMode": "sine", "AutoFlow": "35", "AutoFreq": 30},
        {"AutoMode": "feed", "AutoFlow": 30, "AutoFreq": 5},
    ),
)
async def test_malformed_auto_evidence_fails_without_convergence_retry(
    observed_attributes: dict[str, object],
) -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)
    malformed = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    ).model_copy(update={"observed_attributes": observed_attributes})
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = _ExplicitSequenceDevice(
        "master",
        states=[malformed, malformed],
        events=events,
    )
    slave = _ExplicitSequenceDevice(
        "slave",
        states=[slave_state, slave_state],
        events=events,
    )
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._await_staged_current_a(  # noqa: SLF001
            record,
            monotonic_deadline=_future_deadline(),
        )

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.targets == slave.targets == []


async def test_explicit_transport_failure_is_not_retried_or_converted_to_auto_lag() -> None:
    events: list[str] = []
    peer_read_started = asyncio.Event()
    peer_read_cancelled = asyncio.Event()
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)

    class FailingDevice(_ExplicitSequenceDevice):
        async def get_explicit_state(self) -> DeviceState:
            self.explicit_state_read_count += 1
            await asyncio.sleep(0)
            raise RuntimeError("simulated authentication failure")

    class BlockingDevice(_ExplicitSequenceDevice):
        async def get_explicit_state(self) -> DeviceState:
            self.explicit_state_read_count += 1
            peer_read_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                peer_read_cancelled.set()
                raise
            raise AssertionError("unreachable")

    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = FailingDevice("master", states=[master_state], events=events)
    slave = BlockingDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(RuntimeError, match="authentication failure"):
        await controller._await_staged_current_a(  # noqa: SLF001
            record,
            monotonic_deadline=_future_deadline(),
        )

    assert peer_read_started.is_set()
    assert peer_read_cancelled.is_set()
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert events.count("master:disconnect") == events.count("slave:disconnect") == 2
    assert events.count("master:connect") == events.count("slave:connect") == 2
    assert master.targets == slave.targets == []


async def test_cancelled_explicit_pair_read_refreshes_both_before_propagating() -> None:
    events: list[str] = []
    reads_started = {device_id: asyncio.Event() for device_id in ("master", "slave")}
    reads_cancelled = {device_id: asyncio.Event() for device_id in ("master", "slave")}
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)

    class BlockingDevice(_ExplicitSequenceDevice):
        async def get_explicit_state(self) -> DeviceState:
            self.explicit_state_read_count += 1
            reads_started[self.device_id].set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                reads_cancelled[self.device_id].set()
                raise
            raise AssertionError("unreachable")

    states = {
        device_id: _staged_state(
            spec,
            device_id,
            clock=clock,
            timer_enabled=True,
        )
        for device_id in ("master", "slave")
    }
    master = BlockingDevice("master", states=[states["master"]], events=events)
    slave = BlockingDevice("slave", states=[states["slave"]], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)
    task = asyncio.create_task(
        controller._read_staged_pair_explicit(record)  # noqa: SLF001
    )
    await asyncio.gather(*(event.wait() for event in reads_started.values()))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert all(event.is_set() for event in reads_cancelled.values())
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert events.count("master:disconnect") == events.count("slave:disconnect") == 2
    assert events.count("master:connect") == events.count("slave:connect") == 2
    assert master.targets == slave.targets == []


async def test_transport_timeout_is_not_reclassified_as_convergence_timeout() -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)

    class TimeoutDevice(_ExplicitSequenceDevice):
        async def get_explicit_state(self) -> DeviceState:
            self.explicit_state_read_count += 1
            await asyncio.sleep(0)
            raise TimeoutError("simulated transport timeout")

    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = TimeoutDevice("master", states=[master_state], events=events)
    slave = _ExplicitSequenceDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(TimeoutError, match="transport timeout"):
        await controller._await_staged_current_a(  # noqa: SLF001
            record,
            monotonic_deadline=_future_deadline(),
        )

    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert events.count("master:disconnect") == events.count("slave:disconnect") == 2
    assert events.count("master:connect") == events.count("slave:connect") == 2
    assert master.targets == slave.targets == []


async def test_staged_a_convergence_uses_one_absolute_timeout_without_writes(
    monkeypatch,
) -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)
    mismatch = {
        device_id: _staged_state(
            spec,
            device_id,
            clock=clock,
            timer_enabled=True,
            auto_mode="sine",
            auto_flow=35 if device_id == "master" else 40,
            auto_frequency=30,
        )
        for device_id in ("master", "slave")
    }
    master = _ExplicitSequenceDevice(
        "master",
        states=[mismatch["master"]],
        events=events,
    )
    slave = _ExplicitSequenceDevice(
        "slave",
        states=[mismatch["slave"]],
        events=events,
    )
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)
    monkeypatch.setattr(
        "jebao_flow.devices.schedule_flow_experiment._STAGED_A_CONVERGENCE_RETRY_SECONDS",
        1,
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._await_staged_current_a(  # noqa: SLF001
            record,
            monotonic_deadline=_future_deadline(0.01),
        )

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_SETTLE
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 1
    assert master.targets == slave.targets == []


async def test_cancelled_fresh_pair_refresh_reconnects_both_before_propagating() -> None:
    events: list[str] = []
    disconnect_started = asyncio.Event()
    release_disconnect = asyncio.Event()
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)

    class PausingDevice(_ExplicitSequenceDevice):
        async def disconnect(self) -> None:
            self.events.append(f"{self.device_id}:disconnect")
            disconnect_started.set()
            await release_disconnect.wait()

    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = PausingDevice("master", states=[master_state], events=events)
    slave = _ExplicitSequenceDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)
    task = asyncio.create_task(
        controller._read_staged_pair_explicit(record)  # noqa: SLF001
    )
    await disconnect_started.wait()

    task.cancel()
    release_disconnect.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events.count("master:connect") == events.count("slave:connect") == 1
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 0
    assert master.targets == slave.targets == []


async def test_pair_connect_failure_reconnects_both_before_forward_failure() -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)

    class TrackingDevice(_ExplicitSequenceDevice):
        def __init__(self, *args, fail_first_connect: bool = False, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.connected = True
            self.connect_attempts = 0
            self.fail_first_connect = fail_first_connect

        async def disconnect(self) -> None:
            self.connected = False
            await super().disconnect()

        async def connect(self) -> None:
            self.connect_attempts += 1
            self.events.append(f"{self.device_id}:connect")
            if self.fail_first_connect and self.connect_attempts == 1:
                raise RuntimeError("simulated connect failure")
            self.connected = True

    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = TrackingDevice(
        "master",
        states=[master_state],
        events=events,
        fail_first_connect=True,
    )
    slave = TrackingDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(RuntimeError, match="connect failure"):
        await controller._read_staged_pair_explicit(record)  # noqa: SLF001

    assert master.connected is slave.connected is True
    assert master.connect_attempts == slave.connect_attempts == 2
    assert master.explicit_state_read_count == slave.explicit_state_read_count == 0
    assert master.targets == slave.targets == []


async def test_stop_wins_when_convergence_deadline_is_already_expired() -> None:
    events: list[str] = []
    spec = _spec()
    clock = datetime(2026, 8, 26, 12, 30)
    master_state = _staged_state(
        spec,
        "master",
        clock=clock,
        timer_enabled=True,
    )
    slave_state = _staged_state(
        spec,
        "slave",
        clock=clock,
        timer_enabled=True,
    )
    master = _ExplicitSequenceDevice("master", states=[master_state], events=events)
    slave = _ExplicitSequenceDevice("slave", states=[slave_state], events=events)
    controller = _staged_controller(master, slave)
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)
    assert controller._stop_event is not None  # noqa: SLF001
    controller._stop_event.set()  # noqa: SLF001

    with pytest.raises(LinkageTransactionError, match="stopped"):
        await controller._read_staged_pair_explicit(  # noqa: SLF001
            record,
            monotonic_deadline=asyncio.get_running_loop().time(),
        )

    assert master.explicit_state_read_count == slave.explicit_state_read_count == 0
    assert master.targets == slave.targets == []


async def test_pause_receipt_expiry_in_last_moment_guard_prevents_frame_send() -> None:
    events: list[str] = []
    authorization_count = 0
    master = _PauseDevice("master", power=44, events=events)
    slave = _PauseDevice("slave", power=43, events=events)
    store = cast(object, _UnusedStore())

    def authorize(_spec, _snapshots) -> None:
        nonlocal authorization_count
        authorization_count += 1
        if authorization_count == 3:
            raise RuntimeError("receipt expired while queued")

    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=authorize,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    spec = _spec()
    record = _pause_record(spec, master, slave)
    _arm_pause_controller(controller, spec)

    with pytest.raises(RuntimeError, match="expired while queued"):
        await controller._stage_devices(record)  # noqa: SLF001

    assert master.targets == []
    assert slave.targets == []


async def test_pause_authorizer_failure_precedes_outer_journal_create(monkeypatch) -> None:
    events: list[str] = []
    master = _PauseDevice("master", power=44, events=events)
    slave = _PauseDevice("slave", power=43, events=events)
    spec = _spec()
    record = _pause_record(spec, master, slave)

    class Store:
        create_calls = 0

        def load(self):
            return None

        def create(self, _record) -> None:
            self.create_calls += 1

    store = Store()

    async def capture_only(self, outer_spec, *, created_at, expires_at):
        del self, outer_spec, created_at, expires_at
        return record

    monkeypatch.setattr(TemporaryLinkageController, "_prepare", capture_only)

    def reject(_spec, _snapshots) -> None:
        raise RuntimeError("qualification receipt unavailable")

    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, _UnusedStore()),
        cast(object, _UnusedStore()),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=reject,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    controller._experiment_spec = spec  # noqa: SLF001

    with pytest.raises(RuntimeError, match="qualification receipt unavailable"):
        await controller._run_owned(spec.outer_linkage_spec())  # noqa: SLF001

    assert store.create_calls == 0


@pytest.mark.parametrize(
    "unsafe_flow",
    (
        {"master_before_flow": 46},
        {"slave_before_flow": 46},
        {"master_after_flow": 46},
        {"slave_after_flow": 46},
    ),
)
async def test_private_api_flow_guard_precedes_outer_journal_and_control_write(
    monkeypatch,
    unsafe_flow: dict[str, int],
) -> None:
    events: list[str] = []
    master = _PauseDevice("master", power=44, events=events)
    slave = _PauseDevice("slave", power=43, events=events)
    spec = _spec(**unsafe_flow)
    record = _pause_record(spec, master, slave)

    class Store:
        create_calls = 0

        def load(self):
            return None

        def create(self, _record) -> None:
            self.create_calls += 1

    store = Store()

    async def capture_only(self, outer_spec, *, created_at, expires_at):
        del self, outer_spec, created_at, expires_at
        return record

    monkeypatch.setattr(TemporaryLinkageController, "_prepare", capture_only)
    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, _UnusedStore()),
        cast(object, _UnusedStore()),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    controller._experiment_spec = spec  # noqa: SLF001

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._run_owned(spec.outer_linkage_spec())  # noqa: SLF001

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD
    assert store.create_calls == 0
    assert master.targets == slave.targets == []


@pytest.mark.parametrize(
    ("master_flow", "slave_flow", "expected"),
    (
        (35, 40, ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED),
        (35, 32, ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS),
        (35, 35, ScheduleFlowOutcome.SLAVE_FLOW_FOLLOWED_MASTER),
        (35, 37, ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE),
    ),
)
def test_after_sample_classifies_the_slave_schedule_behavior(
    master_flow: int,
    slave_flow: int,
    expected: ScheduleFlowOutcome,
) -> None:
    assert (
        classify_schedule_flow_sample(
            _spec(),
            _after_sample(master_flow=master_flow, slave_flow=slave_flow),
        )
        is expected
    )


@pytest.mark.parametrize(
    "sample",
    (
        _after_sample(phase="before", slave_flow=32),
        _after_sample(master_flow=34, slave_flow=32),
        _after_sample(master_mode="constant", slave_flow=32),
        _after_sample(master_frequency=29, slave_flow=32),
        _after_sample(slave_flow=32).model_copy(update={"master_manual_power": 30}),
    ),
)
def test_classification_requires_a_strict_expected_master_transition(
    sample: ScheduleLinkageSample,
) -> None:
    assert (
        classify_schedule_flow_sample(_spec(), sample)
        is ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE
    )


def test_slave_remaining_constant_with_its_prior_flow_is_preserved_as_fixed_flow() -> None:
    sample = _after_sample(
        slave_mode="constant",
        slave_flow=32,
        slave_frequency=5,
    )

    assert (
        classify_schedule_flow_sample(_spec(), sample)
        is ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"boundary_time": "00:00"},
        {"slave_before_flow": 40, "slave_after_flow": 40},
        {"slave_before_flow": 35, "master_after_flow": 35},
        {"master_after_flow": 40, "slave_after_flow": 40},
        {"observation_window_seconds": 360, "post_boundary_stability_seconds": 300},
    ),
)
def test_plan_rejects_an_experiment_that_cannot_prove_slave_independence(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _spec(**updates)


def test_plan_accepts_the_fixed_630_second_window_but_no_larger_value() -> None:
    assert _spec(observation_window_seconds=630).observation_window_seconds == 630
    with pytest.raises(ValidationError):
        _spec(observation_window_seconds=630.001)


def test_sentinel_only_requires_sentinel_qualification() -> None:
    with pytest.raises(ValidationError, match="requires the sentinel transaction"):
        _spec(sentinel_only=True, sentinel_qualification=False)


class _UnusedStore:
    def load(self):
        return None


class _FakeScheduleController:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def run(self, spec: TemporaryScheduleSpec, *, observe=None) -> TemporaryScheduleResult:
        self.calls += 1
        label = "sentinel" if spec.operation_id.endswith("_sentinel") else "temporary"
        self.events.append(f"{label}:stage")
        try:
            if observe is not None:
                await observe(cast(TemporaryScheduleRecord, SimpleNamespace()))
        finally:
            self.events.append(f"{label}:restore")
        return cast(TemporaryScheduleResult, SimpleNamespace())


class _FakeRoleController:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def preflight(self, spec):
        self.events.append("roles:preflight")
        return SimpleNamespace(spec=spec)

    async def run(self, _preflight) -> ScheduleLinkageResult:
        self.events.append("roles:run")
        if self.fail:
            raise RuntimeError("simulated role observation failure")
        return cast(
            ScheduleLinkageResult,
            SimpleNamespace(
                schedule_transition_verified=True,
                stop_reason=ScheduleLinkageStopReason.BOUNDARY_VERIFIED,
            ),
        )


class _SequenceController(ScheduleFlowExperimentController):
    def __init__(self, events: list[str]) -> None:
        store = cast(object, _UnusedStore())
        super().__init__(
            {},
            cast(object, store),
            cast(object, store),
            cast(object, store),
            safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
            pause_authorizer=lambda _spec, _snapshots: None,
            prerequisite_authorizer=lambda _spec, _snapshots: None,
            role_preflight_settle_seconds=0,
        )
        self.events = events

    async def _assert_timer_arm_budget(self, record: LinkageTransactionRecord) -> None:
        del record

    async def _await_staged_current_a(
        self,
        record: LinkageTransactionRecord,
        *,
        monotonic_deadline: float,
    ) -> None:
        del record, monotonic_deadline

    async def _run_forward_operation(self, record, operation):
        del record
        return await operation

    async def _arm_temporary_schedule(
        self,
        record: LinkageTransactionRecord,
        *,
        monotonic_deadline: float,
    ) -> None:
        del record, monotonic_deadline
        self.events.append("timer:on")

    async def _disarm_temporary_schedule_uninterruptibly(
        self,
        record: LinkageTransactionRecord,
    ) -> None:
        del record
        self.events.append("timer:off")

    async def _sentinel_spec(
        self,
        spec: ScheduleFlowExperimentSpec,
    ) -> TemporaryScheduleSpec:
        return spec.temporary_schedule_spec().model_copy(
            update={"operation_id": f"{spec.operation_id}_sentinel"}
        )


async def test_nested_sequence_disarms_before_temporary_schedule_restore() -> None:
    events: list[str] = []
    stage_events: list[ScheduleFlowStageEvent] = []
    controller = _SequenceController(events)
    controller._external_stage_event_observer = stage_events.append  # noqa: SLF001
    spec = _spec(sentinel_qualification=True)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = _FakeRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    await controller._activate_relationship(record)  # noqa: SLF001
    assert events == [
        "sentinel:stage",
        "sentinel:restore",
        "temporary:stage",
        "timer:on",
        "roles:preflight",
        "roles:run",
        "timer:off",
        "temporary:restore",
    ]
    assert [event.stage for event in stage_events] == [
        ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED,
        ScheduleFlowStage.TIMER_ON_ARM_STARTED,
        ScheduleFlowStage.TIMER_ON_ARMED,
        ScheduleFlowStage.ROLE_PREFLIGHT_STARTED,
        ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
        ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
        ScheduleFlowStage.ROLE_OBSERVATION_COMPLETED,
        ScheduleFlowStage.ROLE_DISARM_STARTED,
        ScheduleFlowStage.ROLE_DISARMED,
    ]


async def test_one_absolute_timer_deadline_is_shared_by_arm_and_convergence() -> None:
    events: list[str] = []
    deadlines: list[float] = []

    class DeadlineController(_SequenceController):
        async def _arm_temporary_schedule(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record
            deadlines.append(monotonic_deadline)
            self.events.append("timer:on")

        async def _await_staged_current_a(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record
            deadlines.append(monotonic_deadline)

    controller = DeadlineController(events)
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = _FakeRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    await controller._activate_relationship(record)  # noqa: SLF001

    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]
    assert deadlines[0] > asyncio.get_running_loop().time()


async def test_timer_deadline_includes_slow_arm_and_disarms_before_restore(
    monkeypatch,
) -> None:
    events: list[str] = []

    class SlowArmController(_SequenceController):
        async def _arm_temporary_schedule(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record, monotonic_deadline
            self.events.append("timer:on")
            await asyncio.Event().wait()

        async def _await_staged_current_a(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record, monotonic_deadline
            self.events.append("unexpected:convergence")

    monkeypatch.setattr(
        "jebao_flow.devices.schedule_flow_experiment._STAGED_A_CONVERGENCE_TIMEOUT_SECONDS",
        0.01,
    )
    controller = SlowArmController(events)
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._safety_epoch = controller._safety_interlock.epoch  # noqa: SLF001
    controller._stop_event = asyncio.Event()  # noqa: SLF001
    controller._operation_monotonic_deadline = _future_deadline()  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = _FakeRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(
            operation_id=spec.operation_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        ),
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._activate_relationship(record)  # noqa: SLF001

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_SETTLE
    assert events == [
        "temporary:stage",
        "timer:on",
        "timer:off",
        "temporary:restore",
    ]


async def test_typed_role_preflight_failure_is_retained_only_as_private_checkpoint() -> None:
    events: list[str] = []
    delivered: list[ScheduleFlowStageEvent] = []
    controller = _SequenceController(events)
    controller._external_stage_event_observer = delivered.append  # noqa: SLF001
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001

    class FailingPreflightRoleController(_FakeRoleController):
        async def preflight(self, _spec):
            self.events.append("roles:preflight")
            raise ScheduleLinkagePreflightError(
                "private-device-id explicit reply contained private transport detail",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ,
            )

    controller._role_controller = FailingPreflightRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    with pytest.raises(ScheduleLinkagePreflightError, match="private transport detail"):
        await controller._activate_relationship(record)  # noqa: SLF001

    checkpoint = controller.last_role_failure
    assert checkpoint is not None
    assert checkpoint.kind is ScheduleLinkageRunProgressKind.FAILED
    assert checkpoint.failure is ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ
    encoded = checkpoint.model_dump_json()
    assert "private-device-id" not in encoded
    assert "private transport detail" not in encoded
    assert events == [
        "temporary:stage",
        "timer:on",
        "roles:preflight",
        "timer:off",
        "temporary:restore",
    ]
    preflight_failure = next(
        event for event in delivered if event.failure_category is not None
    )
    assert preflight_failure.stage is ScheduleFlowStage.ROLE_PREFLIGHT_STARTED
    assert preflight_failure.failure_category is ScheduleFlowFailureCategory.ROLE_PREFLIGHT
    assert preflight_failure.role_progress is None


@pytest.mark.parametrize(
    ("error", "settled", "expected"),
    (
        (
            ScheduleLinkageBusyError("private-device-id owns a private journal"),
            False,
            ScheduleLinkageRunFailure.PREFLIGHT_BUSY,
        ),
        (
            RuntimeError("private-device-id raised a private exception"),
            False,
            ScheduleLinkageRunFailure.PREFLIGHT_UNEXPECTED,
        ),
        (
            RuntimeError("private-device-id failed during a private quiet interval"),
            True,
            ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
        ),
    ),
)
def test_role_preflight_fallback_reasons_are_allowlisted_and_private(
    error: BaseException,
    settled: bool,
    expected: ScheduleLinkageRunFailure,
) -> None:
    controller = _SequenceController([])

    controller._remember_role_preflight_failure(error, settled=settled)  # noqa: SLF001

    checkpoint = controller.last_role_failure
    assert checkpoint is not None
    assert checkpoint.kind is ScheduleLinkageRunProgressKind.FAILED
    assert checkpoint.failure is expected
    encoded = checkpoint.model_dump_json()
    assert "private-device-id" not in encoded
    assert "private journal" not in encoded
    assert "private exception" not in encoded
    assert "private quiet interval" not in encoded


async def test_role_window_stage_persistence_waits_for_exact_disarm() -> None:
    events: list[str] = []
    delivered: list[tuple[str, bool]] = []
    controller = _SequenceController(events)
    physically_disarmed = False

    def persist_stage(event: ScheduleFlowStageEvent) -> None:
        label = (
            f"role:{event.role_progress.kind}"
            if event.role_progress is not None
            else f"stage:{event.stage}"
        )
        delivered.append((label, physically_disarmed))
        if event.role_progress is not None:
            # A durable observer may fail or stall.  Once disarmed this remains diagnostic-only
            # and must not prevent restoration of the temporary schedule.
            raise RuntimeError("simulated diagnostic persistence failure")

    def persist_sample(_sample: ScheduleLinkageSample) -> None:
        delivered.append(("sample", physically_disarmed))
        raise RuntimeError("simulated sample persistence failure")

    def persist_diagnostic(_event: LinkageDiagnosticEvent) -> None:
        delivered.append(("diagnostic", physically_disarmed))
        raise RuntimeError("simulated diagnostic evidence failure")

    class ProgressRoleController(_FakeRoleController):
        async def run(self, _preflight) -> ScheduleLinkageResult:
            self.events.append("roles:run")
            controller._observe_role_progress(  # noqa: SLF001
                ScheduleLinkageRunProgressEvent(
                    kind=ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_STARTED,
                    occurred_at=datetime.now(UTC),
                )
            )
            self.events.append("roles:master-write")
            controller._observe_role_progress(  # noqa: SLF001
                ScheduleLinkageRunProgressEvent(
                    kind=ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_COMPLETED,
                    occurred_at=datetime.now(UTC),
                )
            )
            controller._observe_role_sample(_after_sample(phase="before"))  # noqa: SLF001
            controller._on_diagnostic_event(  # noqa: SLF001
                LinkageDiagnosticEvent(
                    kind=LinkageDiagnosticEventKind.ACTIVE_ENTERED,
                    occurred_at=datetime.now(UTC),
                )
            )
            assert not any(
                label.startswith("role:") or label in {"sample", "diagnostic"}
                for label, _safe in delivered
            )
            return cast(
                ScheduleLinkageResult,
                SimpleNamespace(
                    schedule_transition_verified=True,
                    stop_reason=ScheduleLinkageStopReason.BOUNDARY_VERIFIED,
                ),
            )

    async def prove_disarmed(_record: LinkageTransactionRecord) -> None:
        nonlocal physically_disarmed
        events.append("timer:off")
        assert not any(
            label.startswith("role:") or label in {"sample", "diagnostic"}
            for label, _safe in delivered
        )
        physically_disarmed = True

    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._external_stage_event_observer = persist_stage  # noqa: SLF001
    controller._external_role_sample_observer = persist_sample  # noqa: SLF001
    controller._external_diagnostic_event_observer = persist_diagnostic  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = ProgressRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._disarm_temporary_schedule_uninterruptibly = prove_disarmed  # type: ignore[method-assign]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    await controller._activate_relationship(record)  # noqa: SLF001

    deferred = [
        (label, safe)
        for label, safe in delivered
        if label.startswith("role:")
        or label
        in {
            "sample",
            "diagnostic",
            f"stage:{ScheduleFlowStage.ROLE_OBSERVATION_COMPLETED}",
            f"stage:{ScheduleFlowStage.ROLE_DISARM_STARTED}",
        }
    ]
    assert deferred
    assert all(safe for _event, safe in deferred)
    assert events[-1] == "temporary:restore"


async def test_role_failure_evidence_is_delivered_only_after_exact_disarm() -> None:
    events: list[str] = []
    delivered: list[tuple[ScheduleFlowStageEvent, bool]] = []
    controller = _SequenceController(events)
    physically_disarmed = False

    class FailingRoleController(_FakeRoleController):
        async def run(self, _preflight) -> ScheduleLinkageResult:
            self.events.append("roles:run")
            controller._observe_role_progress(  # noqa: SLF001
                ScheduleLinkageRunProgressEvent(
                    kind=ScheduleLinkageRunProgressKind.FAILED,
                    occurred_at=datetime.now(UTC),
                    failure=ScheduleLinkageRunFailure.MASTER_ADAPTER_WRITE,
                )
            )
            raise RuntimeError("simulated role failure")

    async def prove_disarmed(_record: LinkageTransactionRecord) -> None:
        nonlocal physically_disarmed
        events.append("timer:off")
        physically_disarmed = True

    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._external_stage_event_observer = (  # noqa: SLF001
        lambda event: delivered.append((event, physically_disarmed))
    )
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = FailingRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._disarm_temporary_schedule_uninterruptibly = prove_disarmed  # type: ignore[method-assign]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    with pytest.raises(RuntimeError, match="simulated role failure"):
        await controller._activate_relationship(record)  # noqa: SLF001

    post_arm = [
        safe
        for event, safe in delivered
        if event.stage is not ScheduleFlowStage.TIMER_ON_ARM_STARTED
    ]
    assert post_arm
    assert all(post_arm)
    assert events[-1] == "temporary:restore"


async def test_role_cancellation_evidence_waits_for_disarm_and_schedule_restore() -> None:
    events: list[str] = []
    delivered: list[tuple[ScheduleFlowStageEvent, bool]] = []
    entered = asyncio.Event()
    controller = _SequenceController(events)
    physically_disarmed = False
    cancellation_failure: ScheduleLinkageRunProgressEvent | None = None

    class BlockingRoleController(_FakeRoleController):
        async def run(self, _preflight) -> ScheduleLinkageResult:
            nonlocal cancellation_failure
            self.events.append("roles:run")
            controller._observe_role_progress(  # noqa: SLF001
                ScheduleLinkageRunProgressEvent(
                    kind=ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_STARTED,
                    occurred_at=datetime.now(UTC),
                )
            )
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_failure = ScheduleLinkageRunProgressEvent(
                    kind=ScheduleLinkageRunProgressKind.FAILED,
                    occurred_at=datetime.now(UTC),
                    failure=ScheduleLinkageRunFailure.MONITOR,
                )
                controller._observe_role_progress(cancellation_failure)  # noqa: SLF001
                raise
            raise AssertionError("unreachable")

    async def prove_disarmed(_record: LinkageTransactionRecord) -> None:
        nonlocal physically_disarmed
        events.append("timer:off")
        physically_disarmed = True

    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._external_stage_event_observer = (  # noqa: SLF001
        lambda event: delivered.append((event, physically_disarmed))
    )
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = BlockingRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._disarm_temporary_schedule_uninterruptibly = prove_disarmed  # type: ignore[method-assign]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )
    task = asyncio.create_task(controller._activate_relationship(record))  # noqa: SLF001
    await asyncio.wait_for(entered.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    post_arm = [
        safe
        for event, safe in delivered
        if event.stage is not ScheduleFlowStage.TIMER_ON_ARM_STARTED
    ]
    assert post_arm
    assert all(post_arm)
    assert events[-2:] == ["timer:off", "temporary:restore"]
    assert controller.last_role_failure == cancellation_failure


async def test_disarm_failure_never_flushes_queued_external_evidence() -> None:
    events: list[str] = []
    delivered: list[ScheduleFlowStageEvent] = []
    controller = _SequenceController(events)
    role_failure = ScheduleLinkageRunProgressEvent(
        kind=ScheduleLinkageRunProgressKind.FAILED,
        occurred_at=datetime.now(UTC),
        failure=ScheduleLinkageRunFailure.MASTER_ADAPTER_WRITE,
    )

    class ProgressRoleController(_FakeRoleController):
        async def run(self, _preflight) -> ScheduleLinkageResult:
            self.events.append("roles:run")
            controller._observe_role_progress(role_failure)  # noqa: SLF001
            raise RuntimeError("simulated role failure")

    async def fail_disarm(_record: LinkageTransactionRecord) -> None:
        events.append("timer:off:failed")
        raise RuntimeError("simulated exact disarm failure")

    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._external_stage_event_observer = delivered.append  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = ProgressRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._disarm_temporary_schedule_uninterruptibly = fail_disarm  # type: ignore[method-assign]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    with pytest.raises(RuntimeError, match="exact disarm failure"):
        await controller._activate_relationship(record)  # noqa: SLF001

    assert [event.stage for event in delivered] == [
        ScheduleFlowStage.TIMER_ON_ARM_STARTED
    ]
    assert controller._defer_external_evidence_delivery is True  # noqa: SLF001
    assert controller._deferred_stage_events  # noqa: SLF001
    assert controller.last_role_failure == role_failure


async def test_sentinel_only_never_enters_field_timer_or_role_paths() -> None:
    events: list[str] = []
    controller = _SequenceController(events)
    spec = _spec(sentinel_only=True)
    controller._experiment_spec = spec  # noqa: SLF001
    schedule = _FakeScheduleController(events)
    controller._schedule_controller = schedule  # type: ignore[assignment]  # noqa: SLF001

    class RejectRoleController:
        async def preflight(self, _spec):
            raise AssertionError("sentinel-only must not enter role preflight")

        async def run(self, _preflight):
            raise AssertionError("sentinel-only must not enter role observation")

    controller._role_controller = RejectRoleController()  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    await controller._activate_relationship(record)  # noqa: SLF001

    assert schedule.calls == 1
    assert events == ["sentinel:stage", "sentinel:restore"]
    assert controller._temporary_result is None  # noqa: SLF001
    assert controller._role_result is None  # noqa: SLF001


async def test_role_failure_still_disarms_then_restores_temporary_schedule() -> None:
    events: list[str] = []
    controller = _SequenceController(events)
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = _FakeRoleController(events, fail=True)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    with pytest.raises(RuntimeError, match="simulated role"):
        await controller._activate_relationship(record)  # noqa: SLF001

    assert events == [
        "temporary:stage",
        "timer:on",
        "roles:preflight",
        "roles:run",
        "timer:off",
        "temporary:restore",
    ]


async def test_staged_a_timeout_disarms_before_temporary_schedule_restore() -> None:
    events: list[str] = []

    class TimeoutController(_SequenceController):
        async def _await_staged_current_a(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record, monotonic_deadline
            raise ScheduleLinkagePreflightError(
                "staged current-A evidence did not converge before its deadline",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
            )

    controller = TimeoutController(events)
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = _FakeRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._activate_relationship(record)  # noqa: SLF001

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_SETTLE
    assert events == [
        "temporary:stage",
        "timer:on",
        "timer:off",
        "temporary:restore",
    ]
    assert controller.last_role_failure is not None
    assert (
        controller.last_role_failure.failure
        is ScheduleLinkageRunFailure.PREFLIGHT_SETTLE
    )


async def test_timer_arm_budget_failure_restores_schedule_without_control_write() -> None:
    events: list[str] = []

    class GateFailureController(_SequenceController):
        async def _assert_timer_arm_budget(
            self,
            record: LinkageTransactionRecord,
        ) -> None:
            del record
            raise ScheduleLinkagePreflightError(
                "staged schedule lacks the required pre-boundary reserve",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
            )

    controller = GateFailureController(events)
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = _FakeRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    with pytest.raises(ScheduleLinkagePreflightError) as captured:
        await controller._activate_relationship(record)  # noqa: SLF001

    assert captured.value.failure is ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW
    assert events == ["temporary:stage", "temporary:restore"]


class _ScheduleReader:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id

    async def read_schedule_image_explicit(self) -> bytes:
        return bytes(48 * 9)


async def test_real_sentinel_spec_is_explicitly_behavior_neutral() -> None:
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {
            "master": cast(object, _ScheduleReader("master")),
            "slave": cast(object, _ScheduleReader("slave")),
        },
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    sentinel = await controller._sentinel_spec(_spec())  # noqa: SLF001

    assert sentinel.kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION
    assert all(len(patch.slots) == 1 for patch in sentinel.device_patches)
    assert all(
        patch.slots[0].behavior_neutral_unused_toggle for patch in sentinel.device_patches
    )


async def test_concurrent_call_cannot_replace_active_experiment_state(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_parent_run(self, _outer_spec):
        started.set()
        await release.wait()
        self._temporary_result = cast(TemporaryScheduleResult, SimpleNamespace())
        self._role_result = cast(
            ScheduleLinkageResult,
            SimpleNamespace(schedule_transition_verified=True),
        )
        self._last_role_sample = _after_sample()
        return SimpleNamespace(completed_at=datetime.now(UTC))

    monkeypatch.setattr(TemporaryLinkageController, "run", fake_parent_run)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    first = _spec(operation_id="first", sentinel_qualification=False)
    second = _spec(operation_id="second", sentinel_qualification=False)
    task = asyncio.create_task(controller.run_experiment(first))
    await started.wait()

    with pytest.raises(LinkageTransactionBusyError):
        await controller.run_experiment(second)
    assert controller._experiment_spec == first  # noqa: SLF001

    release.set()
    result = await task
    assert result.operation_id == "first"
    assert controller._experiment_spec is None  # noqa: SLF001


async def test_completed_sentinel_only_returns_wire_qualified_without_field_sample(
    monkeypatch,
) -> None:
    async def fake_parent_run(self, _outer_spec):
        self._sentinel_result = cast(TemporaryScheduleResult, SimpleNamespace())
        self._wire_qualification_verified = True
        return SimpleNamespace(completed_at=datetime.now(UTC))

    monkeypatch.setattr(TemporaryLinkageController, "run", fake_parent_run)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    result = await controller.run_experiment(_spec(sentinel_only=True))

    assert result.outcome == "wire_qualified"
    assert result.sentinel_qualified is True
    assert result.last_after_sample is None
    assert result.schedule_transition_verified is False
    assert result.stable_slave_tuple_observed is False
    assert result.stable_observation_seconds == 0


@pytest.mark.parametrize(
    ("sample", "expected"),
    (
        (
            _after_sample(slave_mode="constant", slave_flow=32, slave_frequency=5),
            ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS,
        ),
        (
            _after_sample(slave_flow=35),
            ScheduleFlowOutcome.SLAVE_FLOW_FOLLOWED_MASTER,
        ),
        (
            _after_sample(slave_flow=37),
            ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE,
        ),
    ),
)
async def test_completed_experiment_returns_each_stable_slave_outcome(
    monkeypatch,
    sample: ScheduleLinkageSample,
    expected: ScheduleFlowOutcome,
) -> None:
    async def fake_parent_run(self, _outer_spec):
        self._temporary_result = cast(TemporaryScheduleResult, SimpleNamespace())
        self._role_result = cast(
            ScheduleLinkageResult,
            SimpleNamespace(schedule_transition_verified=True),
        )
        self._last_role_sample = sample
        return SimpleNamespace(completed_at=datetime.now(UTC))

    monkeypatch.setattr(TemporaryLinkageController, "run", fake_parent_run)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    result = await controller.run_experiment(_spec(sentinel_qualification=False))

    assert result.outcome is expected
    assert result.last_after_sample == sample
    assert result.schedule_transition_verified is False
    assert result.stable_slave_tuple_observed is True
    assert result.temporary_schedule_restored is True
    assert result.original_controls_restored is True


async def test_last_actual_sample_survives_a_later_experiment_failure(monkeypatch) -> None:
    sample = _after_sample(slave_mode="constant", slave_flow=32, slave_frequency=5)

    async def fake_parent_run(self, _outer_spec):
        self._last_role_sample = sample
        raise RuntimeError("simulated later failure")

    monkeypatch.setattr(TemporaryLinkageController, "run", fake_parent_run)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    with pytest.raises(RuntimeError, match="later failure"):
        await controller.run_experiment(_spec(sentinel_qualification=False))

    assert controller.last_role_sample == sample


class _DisarmDevice:
    def __init__(
        self,
        device_id: str,
        *,
        events: list[str],
        fail_write: bool = False,
        remain_timer_on: bool = False,
    ) -> None:
        self.device_id = device_id
        self.events = events
        self.fail_write = fail_write
        self.remain_timer_on = remain_timer_on
        self.target = None
        self.retired = False
        mac_address = "020000000001" if device_id == "master" else "020000000002"
        self.physical_binding = PhysicalDeviceBinding.from_identifiers(
            vendor_device_id=f"test-{device_id}",
            mac_address=mac_address,
            product_key="test-product",
            config_fingerprint=configuration_fingerprint(
                {"device_id": device_id, "address": f"test-{device_id}"}
            ),
        )
        self.schedule = DeviceSchedule(
            enabled=False,
            entries=(
                ScheduleEntry(
                    slot=0,
                    start="00:00",
                    end="23:59",
                    mode="constant",
                    mode_code=2,
                    parameters={
                        "flow": 31,
                        "frequency": 0,
                        "feed_time": 0,
                        "custom_frequency": 0,
                    },
                ),
            ),
        )

    async def disconnect(self) -> None:
        self.events.append(f"{self.device_id}:disconnect")
        self.retired = True

    async def connect(self) -> None:
        self.events.append(f"{self.device_id}:connect")
        self.retired = False

    async def write_target(self, target, *, guard=None) -> None:
        assert guard is None or guard()
        assert not self.retired
        self.events.append(f"{self.device_id}:write")
        self.target = target
        if self.fail_write:
            raise RuntimeError("simulated unconfirmed write")

    async def get_state(self):
        self.events.append(f"{self.device_id}:read")
        target = self.target
        assert target is not None
        return DeviceState(
            online=True,
            error=None,
            enabled=target.enabled,
            power=target.power,
            mode=target.mode,
            frequency=target.frequency,
            linkage=target.linkage,
            timer_enabled=True if self.remain_timer_on else target.timer_enabled,
            schedule=self.schedule,
        )


def _disarm_record(operation_id: str = "scheduled_slave_flow"):
    return SimpleNamespace(
        operation_id=operation_id,
        spec=SimpleNamespace(
            master_device_id="master",
            slave_device_id="slave",
            master_power=31,
            slave_power=32,
            mode="constant",
            frequency=20,
        ),
    )


def _recovery_disarm_proof(
    operation_id: str = "scheduled_slave_flow_roles",
) -> ScheduleLinkageExternalDisarmProof:
    schedule = DeviceSchedule(
        enabled=False,
        entries=(
            ScheduleEntry(
                slot=0,
                start="00:00",
                end="23:59",
                mode="constant",
                mode_code=2,
                parameters={
                    "flow": 31,
                    "frequency": 0,
                    "feed_time": 0,
                    "custom_frequency": 0,
                },
            ),
        ),
    )
    fingerprint = schedule_structure_fingerprint(schedule)
    assert fingerprint is not None
    states: list[ScheduleLinkageExternalDisarmState] = []
    for device_id, power, mac_address in (
        ("master", 31, "020000000001"),
        ("slave", 32, "020000000002"),
    ):
        states.append(
            ScheduleLinkageExternalDisarmState(
                device_id=device_id,
                physical_binding=PhysicalDeviceBinding.from_identifiers(
                    vendor_device_id=f"test-{device_id}",
                    mac_address=mac_address,
                    product_key="test-product",
                    config_fingerprint=configuration_fingerprint(
                        {"device_id": device_id, "address": f"test-{device_id}"}
                    ),
                ),
                observed_at=datetime.now(UTC),
                online=True,
                error=None,
                enabled=True,
                power=power,
                mode="constant",
                frequency=20,
                timer_enabled=False,
                linkage=LinkageRole.INDEPENDENT,
                schedule_fingerprint=fingerprint,
            )
        )
    return ScheduleLinkageExternalDisarmProof(
        operation_id=operation_id,
        states=tuple(states),
    )


async def test_normal_unwind_hands_exact_disarm_proof_to_role_close_before_restore(
    monkeypatch,
) -> None:
    events: list[str] = []
    received: list[ScheduleLinkageExternalDisarmProof] = []
    controller = _SequenceController(events)
    spec = _spec(sentinel_qualification=False)
    expected_proof = _recovery_disarm_proof(f"{spec.operation_id}_roles")

    class RoleStore:
        record = SimpleNamespace(operation_id=f"{spec.operation_id}_roles")

        def load(self):
            return self.record

    role_store = RoleStore()

    class ClosingRoleController(_FakeRoleController):
        async def finalize_externally_disarmed(
            self,
            operation_id: str,
            *,
            proof: ScheduleLinkageExternalDisarmProof,
        ) -> bool:
            assert operation_id == f"{spec.operation_id}_roles"
            received.append(proof)
            events.append("roles:journal_close")
            role_store.record = None
            return True

    async def prove_disarmed(
        _record: LinkageTransactionRecord,
    ) -> ScheduleLinkageExternalDisarmProof:
        events.append("timer:off")
        return expected_proof

    controller._experiment_spec = spec  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = ClosingRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_store = role_store  # type: ignore[assignment]  # noqa: SLF001
    controller._disarm_temporary_schedule_uninterruptibly = prove_disarmed  # type: ignore[method-assign]  # noqa: SLF001
    monkeypatch.setattr(
        controller,
        "_validate_nested_recovery_ownership",
        lambda _outer, _schedule, _role: None,
    )
    record = cast(
        LinkageTransactionRecord,
        SimpleNamespace(operation_id=spec.operation_id),
    )

    await controller._activate_relationship(record)  # noqa: SLF001

    assert received == [expected_proof]
    assert events.index("timer:off") < events.index("roles:journal_close")
    assert events.index("roles:journal_close") < events.index("temporary:restore")
    assert role_store.record is None


async def test_disarm_attempts_both_devices_and_accepts_verified_ack_loss() -> None:
    events: list[str] = []
    slave = _DisarmDevice("slave", events=events, fail_write=True)
    master = _DisarmDevice("master", events=events)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    controller._active_operation_id = "scheduled_slave_flow"  # noqa: SLF001

    proof = await controller._disarm_temporary_schedule(  # noqa: SLF001
        cast(LinkageTransactionRecord, _disarm_record())
    )

    assert events == [
        "slave:disconnect",
        "master:disconnect",
        "slave:connect",
        "master:connect",
        "slave:write",
        "master:write",
        "slave:disconnect",
        "slave:connect",
        "slave:read",
        "master:disconnect",
        "master:connect",
        "master:read",
    ]
    assert slave.target.linkage is LinkageRole.INDEPENDENT
    assert slave.target.timer_enabled is False
    assert proof.operation_id == "scheduled_slave_flow_roles"
    assert tuple(state.device_id for state in proof.states) == ("master", "slave")


async def test_retired_role_preflight_sessions_refresh_before_timer_off_and_restore() -> None:
    events: list[str] = []
    slave = _DisarmDevice("slave", events=events)
    master = _DisarmDevice("master", events=events)
    store = cast(object, _UnusedStore())

    class RealDisarmController(ScheduleFlowExperimentController):
        async def _assert_timer_arm_budget(
            self,
            record: LinkageTransactionRecord,
        ) -> None:
            del record

        async def _arm_temporary_schedule(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record, monotonic_deadline
            events.append("timer:on")

        async def _await_staged_current_a(
            self,
            record: LinkageTransactionRecord,
            *,
            monotonic_deadline: float,
        ) -> None:
            del record, monotonic_deadline

        async def _run_forward_operation(self, record, operation):
            del record
            return await operation

    class RetiringPreflightRoleController(_FakeRoleController):
        async def preflight(self, _spec):
            self.events.append("roles:preflight")
            master.retired = slave.retired = True
            raise ScheduleLinkagePreflightError(
                "simulated explicit read failure",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ,
            )

    controller = RealDisarmController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
        role_preflight_settle_seconds=0,
    )
    spec = _spec(sentinel_qualification=False)
    controller._experiment_spec = spec  # noqa: SLF001
    controller._active_operation_id = spec.operation_id  # noqa: SLF001
    controller._schedule_controller = _FakeScheduleController(events)  # type: ignore[assignment]  # noqa: SLF001
    controller._role_controller = RetiringPreflightRoleController(events)  # type: ignore[assignment]  # noqa: SLF001
    record = cast(LinkageTransactionRecord, _disarm_record())

    with pytest.raises(ScheduleLinkagePreflightError):
        await controller._activate_relationship(record)  # noqa: SLF001

    assert events == [
        "temporary:stage",
        "timer:on",
        "roles:preflight",
        "slave:disconnect",
        "master:disconnect",
        "slave:connect",
        "master:connect",
        "slave:write",
        "master:write",
        "slave:disconnect",
        "slave:connect",
        "slave:read",
        "master:disconnect",
        "master:connect",
        "master:read",
        "temporary:restore",
    ]
    assert slave.target.timer_enabled is master.target.timer_enabled is False
    assert slave.target.linkage is master.target.linkage is LinkageRole.INDEPENDENT


async def test_unproven_timer_off_blocks_schedule_restore() -> None:
    events: list[str] = []
    slave = _DisarmDevice(
        "slave",
        events=events,
        fail_write=True,
        remain_timer_on=True,
    )
    master = _DisarmDevice("master", events=events)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {"master": cast(object, master), "slave": cast(object, slave)},
        cast(object, store),
        cast(object, store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    controller._active_operation_id = "scheduled_slave_flow"  # noqa: SLF001

    with pytest.raises(TemporaryScheduleObserverUnstoppableError) as captured:
        await controller._disarm_temporary_schedule(  # noqa: SLF001
            cast(LinkageTransactionRecord, _disarm_record())
        )

    assert captured.value.code is TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
    assert "master:write" in events
    assert events[-3:] == ["master:disconnect", "master:connect", "master:read"]


class _MutableStore:
    def __init__(self, record=None) -> None:
        self.record = record

    def load(self):
        return self.record


class _RecoverRoles:
    def __init__(self, events: list[str], store: _MutableStore) -> None:
        self.events = events
        self.store = store

    async def recover_pending(self) -> bool:
        self.events.append("roles:detach")
        self.store.record = None
        return True

    async def finalize_externally_disarmed(
        self,
        operation_id: str,
        *,
        proof: ScheduleLinkageExternalDisarmProof,
    ) -> bool:
        assert operation_id
        assert proof.operation_id == operation_id
        self.events.append("roles:journal_close")
        self.store.record = None
        return True


class _FailFinalizeRoles:
    async def finalize_externally_disarmed(
        self,
        operation_id: str,
        *,
        proof: ScheduleLinkageExternalDisarmProof,
    ) -> bool:
        assert operation_id
        assert proof.operation_id == operation_id
        raise RuntimeError("simulated role journal close failure")


class _RecoverSchedule:
    def __init__(self, events: list[str], store: _MutableStore) -> None:
        self.events = events
        self.store = store

    async def manual_recover(
        self,
        *,
        disarm_verified: bool,
        observer_stopped: bool,
    ) -> bool:
        assert disarm_verified is True
        assert observer_stopped is True
        self.events.append("schedule:restore")
        self.store.record = None
        return True


async def test_attended_recovery_orders_roles_timer_schedule_then_outer(monkeypatch) -> None:
    events: list[str] = []
    outer_record = cast(LinkageTransactionRecord, _disarm_record("recover_order"))
    outer_store = _MutableStore(outer_record)
    schedule_store = _MutableStore(object())
    role_store = _MutableStore(SimpleNamespace(operation_id="recover_order_roles"))

    def reject_requalification(_spec, _snapshots) -> None:
        raise AssertionError("recovery must not require a fresh qualification receipt")

    controller = ScheduleFlowExperimentController(
        {},
        cast(object, outer_store),
        cast(object, schedule_store),
        cast(object, role_store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=reject_requalification,
        prerequisite_authorizer=reject_requalification,
    )
    controller._role_controller = _RecoverRoles(  # type: ignore[assignment]  # noqa: SLF001
        events,
        role_store,
    )
    controller._schedule_controller = _RecoverSchedule(  # type: ignore[assignment]  # noqa: SLF001
        events,
        schedule_store,
    )
    monkeypatch.setattr(controller, "_validate_recovery_bindings", lambda _record: None)
    monkeypatch.setattr(
        controller,
        "_validate_nested_recovery_ownership",
        lambda _outer, _schedule, _role: None,
    )

    async def disarm(_record) -> ScheduleLinkageExternalDisarmProof:
        events.append("timer:off_verified")
        return _recovery_disarm_proof("recover_order_roles")

    monkeypatch.setattr(controller, "_disarm_temporary_schedule_uninterruptibly", disarm)

    async def recover_outer(self, *, authority) -> bool:
        del self, authority
        events.append("outer:restore")
        outer_store.record = None
        return True

    monkeypatch.setattr(TemporaryLinkageController, "recover_pending", recover_outer)

    assert await controller.recover_experiment() is True
    assert events == [
        "timer:off_verified",
        "roles:journal_close",
        "schedule:restore",
        "outer:restore",
    ]


async def test_schedule_only_recovery_reports_its_actual_disarm(monkeypatch) -> None:
    events: list[str] = []
    stage_events: list[ScheduleFlowStageEvent] = []
    physically_disarmed = False
    outer_record = cast(LinkageTransactionRecord, _disarm_record("schedule_only"))
    outer_store = _MutableStore(outer_record)
    schedule_store = _MutableStore(object())
    role_store = _MutableStore(None)
    def persist_stage(event: ScheduleFlowStageEvent) -> None:
        assert physically_disarmed is True
        stage_events.append(event)

    controller = ScheduleFlowExperimentController(
        {},
        cast(object, outer_store),
        cast(object, schedule_store),
        cast(object, role_store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
        stage_event_observer=persist_stage,
    )
    controller._schedule_controller = _RecoverSchedule(  # type: ignore[assignment]  # noqa: SLF001
        events,
        schedule_store,
    )
    monkeypatch.setattr(controller, "_validate_recovery_bindings", lambda _record: None)
    monkeypatch.setattr(
        controller,
        "_validate_nested_recovery_ownership",
        lambda _outer, _schedule, _role: None,
    )

    async def disarm(_record) -> ScheduleLinkageExternalDisarmProof:
        nonlocal physically_disarmed
        events.append("timer:off_verified")
        physically_disarmed = True
        return _recovery_disarm_proof("schedule_only_roles")

    async def recover_outer(self, *, authority) -> bool:
        del self, authority
        outer_store.record = None
        return True

    monkeypatch.setattr(controller, "_disarm_temporary_schedule_uninterruptibly", disarm)
    monkeypatch.setattr(TemporaryLinkageController, "recover_pending", recover_outer)

    assert await controller.recover_experiment() is True
    assert events == ["timer:off_verified", "schedule:restore"]
    assert [event.stage for event in stage_events[:2]] == [
        ScheduleFlowStage.ROLE_DISARM_STARTED,
        ScheduleFlowStage.ROLE_DISARMED,
    ]


async def test_failed_role_journal_close_retains_the_temporary_schedule_authority(
    monkeypatch,
) -> None:
    role_record = SimpleNamespace(operation_id="scheduled_slave_flow_roles")
    schedule_record = object()
    schedule_store = _MutableStore(schedule_record)
    role_store = _MutableStore(role_record)
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, schedule_store),
        cast(object, role_store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    controller._role_controller = _FailFinalizeRoles()  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(
        controller,
        "_validate_nested_recovery_ownership",
        lambda _outer, _schedule, _role: None,
    )

    with pytest.raises(TemporaryScheduleObserverUnstoppableError) as captured:
        await controller._clear_role_journal_before_schedule_restore(  # noqa: SLF001
            cast(LinkageTransactionRecord, _disarm_record()),
            proof=_recovery_disarm_proof(),
        )

    assert captured.value.code is TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
    assert schedule_store.record is schedule_record
    assert role_store.record is role_record


async def test_pending_schedule_failure_uses_outer_safe_stop_not_timer_on_rollback(
    monkeypatch,
) -> None:
    events: list[str] = []
    schedule_store = _MutableStore(object())
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, schedule_store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    async def fail_nested(_record) -> bool:
        events.append("nested:failed")
        raise LinkageRollbackError("simulated nested failure")

    async def safe_stop(_record) -> None:
        events.append("outer:safe_stop")
        raise LinkageRollbackError("safe stop latched")

    monkeypatch.setattr(controller, "_recover_nested_before_outer", fail_nested)
    monkeypatch.setattr(controller, "_defer_restore_for_safety", safe_stop)

    with pytest.raises(LinkageRollbackError, match="safe stop"):
        await controller._rollback_uninterruptibly(  # noqa: SLF001
            cast(LinkageTransactionRecord, _disarm_record())
        )
    assert events == ["nested:failed", "outer:safe_stop"]
    assert controller._safety_interlock.permitted is False  # noqa: SLF001


async def test_repeated_cancellation_cannot_interrupt_composed_safe_stop(monkeypatch) -> None:
    events: list[str] = []
    safe_stop_started = asyncio.Event()
    allow_safe_stop = asyncio.Event()
    schedule_store = _MutableStore(object())
    store = cast(object, _UnusedStore())
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, store),
        cast(object, schedule_store),
        cast(object, store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )

    async def fail_nested(_record) -> bool:
        raise LinkageRollbackError("simulated nested failure")

    async def safe_stop(_record) -> None:
        events.append("safe_stop:started")
        safe_stop_started.set()
        await allow_safe_stop.wait()
        events.append("safe_stop:completed")
        raise LinkageRollbackError("safe stop latched")

    monkeypatch.setattr(controller, "_recover_nested_before_outer", fail_nested)
    monkeypatch.setattr(controller, "_defer_restore_for_safety", safe_stop)
    rollback = asyncio.create_task(
        controller._rollback_uninterruptibly(  # noqa: SLF001
            cast(LinkageTransactionRecord, _disarm_record())
        )
    )
    await safe_stop_started.wait()

    rollback.cancel()
    await asyncio.sleep(0)
    rollback.cancel()
    await asyncio.sleep(0)
    assert not rollback.done()

    allow_safe_stop.set()
    with pytest.raises(LinkageRollbackError, match="safe stop"):
        await rollback
    assert events == ["safe_stop:started", "safe_stop:completed"]


async def test_mismatched_nested_journal_is_rejected_before_any_recovery_write() -> None:
    events: list[str] = []
    master_binding = object()
    slave_binding = object()
    outer = SimpleNamespace(
        operation_id="owned",
        spec=SimpleNamespace(
            master_device_id="master",
            slave_device_id="slave",
            bootstrap_active_schedule=True,
            slave_role=LinkageRole.ASYNC_SLAVE,
        ),
        snapshots=(
            SimpleNamespace(device_id="master", physical_binding=master_binding),
            SimpleNamespace(device_id="slave", physical_binding=slave_binding),
        ),
    )
    mismatched_schedule = SimpleNamespace(
        operation_id="different_schedule",
        spec=SimpleNamespace(
            kind=TemporaryScheduleKind.FIELD_OBSERVATION,
            device_patches=(
                SimpleNamespace(device_id="master"),
                SimpleNamespace(device_id="slave"),
            ),
        ),
        snapshots=(
            SimpleNamespace(device_id="master", physical_binding=master_binding),
            SimpleNamespace(device_id="slave", physical_binding=slave_binding),
        ),
    )
    outer_store = _MutableStore(outer)
    schedule_store = _MutableStore(mismatched_schedule)
    role_store = _MutableStore(None)
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, outer_store),
        cast(object, schedule_store),
        cast(object, role_store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
    )
    controller._role_controller = _RecoverRoles(  # type: ignore[assignment]  # noqa: SLF001
        events,
        role_store,
    )
    controller._schedule_controller = _RecoverSchedule(  # type: ignore[assignment]  # noqa: SLF001
        events,
        schedule_store,
    )

    with pytest.raises(LinkageRollbackError, match="owner mismatch"):
        await controller.recover_experiment()

    assert events == []
    assert schedule_store.record is mismatched_schedule
