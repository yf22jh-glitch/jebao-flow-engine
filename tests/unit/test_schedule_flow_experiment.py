from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from jebao_flow.devices.linkage import (
    LinkageDiagnosticEvent,
    LinkageDiagnosticEventKind,
    LinkageForwardFailureCategory,
    LinkageRollbackError,
    LinkageSafetyInterlock,
    LinkageTransactionBusyError,
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
    ScheduleLinkageResult,
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
from jebao_flow.protocol.models import DeviceSchedule, DeviceState, DeviceTarget, LinkageRole
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    decode_local_wavemaker_pro_slot_wire,
)


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
        )
        self.events = events

    async def _arm_temporary_schedule(self, record: LinkageTransactionRecord) -> None:
        del record
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


class _ScheduleReader:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id

    async def read_schedule_image(self) -> bytes:
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

    async def disconnect(self) -> None:
        self.events.append(f"{self.device_id}:disconnect")

    async def connect(self) -> None:
        self.events.append(f"{self.device_id}:connect")

    async def write_target(self, target, *, guard=None) -> None:
        assert guard is None or guard()
        self.events.append(f"{self.device_id}:write")
        self.target = target
        if self.fail_write:
            raise RuntimeError("simulated unconfirmed write")

    async def get_state(self):
        self.events.append(f"{self.device_id}:read")
        target = self.target
        assert target is not None
        return SimpleNamespace(
            online=True,
            error=None,
            enabled=target.enabled,
            power=target.power,
            mode=target.mode,
            frequency=target.frequency,
            linkage=target.linkage,
            timer_enabled=True if self.remain_timer_on else target.timer_enabled,
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

    await controller._disarm_temporary_schedule(  # noqa: SLF001
        cast(LinkageTransactionRecord, _disarm_record())
    )

    assert events == [
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

    async def finalize_externally_disarmed(self, operation_id: str) -> bool:
        assert operation_id
        self.events.append("roles:journal_close")
        self.store.record = None
        return True


class _FailFinalizeRoles:
    async def finalize_externally_disarmed(self, operation_id: str) -> bool:
        assert operation_id
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

    async def disarm(_record) -> None:
        events.append("timer:off_verified")

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
    outer_record = cast(LinkageTransactionRecord, _disarm_record("schedule_only"))
    outer_store = _MutableStore(outer_record)
    schedule_store = _MutableStore(object())
    role_store = _MutableStore(None)
    controller = ScheduleFlowExperimentController(
        {},
        cast(object, outer_store),
        cast(object, schedule_store),
        cast(object, role_store),
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        pause_authorizer=lambda _spec, _snapshots: None,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
        stage_event_observer=stage_events.append,
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

    async def disarm(_record) -> None:
        events.append("timer:off_verified")

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
            cast(LinkageTransactionRecord, _disarm_record())
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
