"""Exercise durable orchestration and failures, not firmware propagation assumptions.

The backend models explicit scalar acknowledgements only. Each pump's power is
independent; no test assumes a master power command also changes its slave.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from jebao_flow.local_control.external_manual_editor import ScheduleEditor
from jebao_flow.local_control.external_manual_runtime import ExternalManualScheduler, PairState
from tests.unit.test_external_manual_schedule import FakeBackend, _at, _scheduler


class MaintenanceBackend(FakeBackend):
    def __init__(self):
        super().__init__(slave_timer=False)
        self.bar = SimpleNamespace(physical_identity_key="b" * 64)
        self.bar_state = {
            "identity": "b" * 64,
            "image_sha256": "a" * 64,
            "power": True,
            "timer": True,
            "feeding": False,
            "role": "independent",
            "faults": [],
        }
        self.store = None
        self.fail_target = None
        self.pending = False
        self.ack_without_effect = False

    async def fresh_bar(self):
        return dict(self.bar_state)

    async def validate_maintenance(self):
        pass

    async def reconcile_pending_commands(self):
        pass

    def maintenance_unfinished(self):
        return self.pending

    async def execute(self, child, request_id):
        if request_id.startswith("pro_manual_maintenance_"):
            state = json.loads((self.store.root / "maintenance.json").read_text())
            assert state["enabled"]
            if state["phase"] != "resuming":
                assert self.store.active()["status"] == "paused"
            assert self.store.operation(request_id)["phase"] == "attempting"
        if child.function != "power" and child.target != "bar":
            return await super().execute(child, request_id)
        self.sent.append((child, request_id))
        if child.target == self.fail_target:
            self.pending = True
            return False, 1, "f" * 64
        self.child_records[request_id] = ("verified", "f" * 64)
        if self.ack_without_effect:
            return True, 1, "f" * 64
        if child.target == "bar":
            field = {
                "power": "power",
                "schedule_enable": "timer",
                "feed_switch": "feeding",
                "linkage_role": "role",
            }[child.function]
            self.bar_state[field] = child.value
        else:
            member = getattr(self.pair, child.target)
            self.pair = replace(self.pair, **{child.target: replace(member, power=child.value)})
        return True, 1, "f" * 64


@pytest.fixture
async def rig(tmp_path):
    old, _, store = _scheduler(tmp_path, slave_timer=False)
    backend = MaintenanceBackend()
    scheduler = ExternalManualScheduler(plan=old.plan, store=store, backend=backend)
    store.save_plan(old.plan)
    clock = [_at(13, 10)]
    await scheduler.activate(clock[0])
    backend.sent.clear()
    backend.store = store
    editor = ScheduleEditor(store, backend, clock=lambda: clock[0])
    return SimpleNamespace(editor=editor, backend=backend, store=store, clock=clock, plan=old.plan)


async def request(rig, operation, *, request_id=None):
    payload = json.dumps(
        {
            "schema_version": 1,
            "request_id": request_id or secrets.token_hex(16),
            "issued_at": rig.clock[0].isoformat(),
            "operation": operation,
            "expected_active": rig.store.active()["schedule_sha256"],
            "expected_maintenance": rig.editor.maintenance.state()["generation"],
        }
    ).encode()
    return await rig.editor.handle(payload, rig.clock[0])


async def test_start_sends_power_off_to_each_device_and_preserves_modes_and_images(rig):
    pair, bar = rig.backend.pair, dict(rig.backend.bar_state)
    result = await request(rig, "maintenance_on")
    assert result["outcome"] == "maintenance_on"
    commands = [(c.target, c.function, c.value) for c, _ in rig.backend.sent]
    assert commands == [
        ("bar", "schedule_enable", False),
        ("bar", "power", False),
        ("master", "linkage_role", "independent"),
        ("slave", "linkage_role", "independent"),
        ("master", "power", False),
        ("slave", "power", False),
    ]
    assert rig.backend.pair.master.mode == pair.master.mode
    assert rig.backend.pair.slave.mode == pair.slave.mode
    assert rig.backend.bar_state["image_sha256"] == bar["image_sha256"]
    state = rig.editor.maintenance.state()
    assert state["phase"] == "powered_off"
    assert state["mode"] == "power_off"
    assert not any(state["observed"][k] for k in ("master_power", "slave_power", "bar_power"))
    assert rig.store.active()["status"] == "paused"
    assert not any(c.function in {"manual_mode", "feed_switch"} for c, _ in rig.backend.sent)


async def test_explicit_second_stop_does_not_repeat_already_verified_off_commands(rig):
    await request(rig, "maintenance_on")
    before = list(rig.backend.sent)
    await request(rig, "maintenance_on")
    assert rig.backend.sent == before


async def test_acknowledgements_without_power_readback_never_report_stopped(rig):
    rig.backend.ack_without_effect = True
    result = await request(rig, "maintenance_on")
    assert result["outcome"] == "error"
    state = rig.editor.maintenance.state()
    assert state["enabled"] and state["phase"] == "error" and state["verified_at"] is None
    assert state["observed"]["bar_power"] is True
    assert rig.store.active()["status"] == "paused"


@pytest.mark.parametrize("target", ["master", "slave", "bar"])
async def test_uncertain_off_does_not_replay_or_resume_during_poll_or_restart(rig, target):
    rig.backend.fail_target = target
    result = await request(rig, "maintenance_on")
    assert result["outcome"] == "error"
    sent = list(rig.backend.sent)
    await rig.editor.tick(rig.clock[0] + timedelta(hours=2))
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    assert rig.backend.sent == sent
    assert restarted.maintenance.state()["enabled"]
    assert rig.store.active()["status"] == "paused"
    assert not any(c.function == "power" and c.value is True for c, _ in sent)


async def test_restart_and_elapsed_boundaries_cannot_power_the_pumps_back_on(rig):
    await request(rig, "maintenance_on")
    rig.backend.sent.clear()
    last_event = rig.store.active()["last_event"]
    rig.clock[0] += timedelta(hours=3)
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    await restarted.tick(rig.clock[0])
    assert rig.backend.sent == []
    assert rig.store.active()["last_event"] == last_event
    assert restarted.maintenance.state()["phase"] == "powered_off"


async def test_observed_external_power_on_can_only_generate_a_new_off_correction(rig):
    await request(rig, "maintenance_on")
    rig.backend.sent.clear()
    rig.backend.bar_state["power"] = True
    rig.clock[0] += timedelta(seconds=20)
    await rig.editor.tick(rig.clock[0])
    assert [(c.target, c.function, c.value) for c, _ in rig.backend.sent] == [
        ("bar", "power", False),
    ]
    assert rig.editor.maintenance.state()["phase"] == "powered_off"


@pytest.mark.parametrize("bar_timer", [True, False])
async def test_explicit_resume_powers_all_three_and_applies_only_the_current_row(rig, bar_timer):
    rig.backend.bar_state["timer"] = bar_timer
    await request(rig, "maintenance_on")
    rig.backend.sent.clear()
    rig.clock[0] = _at(15, 10)
    result = await request(rig, "maintenance_off")
    assert result["outcome"] == "maintenance_off"
    on = [(c.target, c.value) for c, _ in rig.backend.sent if c.function == "power"]
    assert on == [("master", True), ("slave", True), ("bar", True)]
    row = rig.plan.current(rig.clock[0])
    assert rig.backend.pair.master.flow == row.lower_flow
    assert rig.backend.pair.slave.flow == row.upper_flow
    assert rig.backend.bar_state["timer"] is bar_timer
    active = rig.store.active()
    assert active["status"] == "active"
    plan = rig.store.load_plan(active["schedule_sha256"])
    assert active["last_event"] == plan.event_key(rig.clock[0])
    assert not rig.editor.maintenance.state()["enabled"]
    original, resumed = json.loads(rig.plan.canonical), json.loads(plan.canonical)
    original.pop("source_table")
    resumed.pop("source_table")
    assert original == resumed


async def test_changed_bar_role_with_the_same_image_can_return_to_saved_independent_role(rig):
    await request(rig, "maintenance_on")
    rig.backend.bar_state["role"] = "master"
    result = await request(rig, "maintenance_off")
    assert result["outcome"] == "maintenance_off"
    assert rig.backend.bar_state["role"] == "independent"
    assert rig.backend.bar_state["timer"]


async def test_resume_restores_manual_bar_even_if_timer_was_changed_externally(rig):
    rig.backend.bar_state["timer"] = False
    await request(rig, "maintenance_on")
    rig.backend.bar_state["timer"] = True
    result = await request(rig, "maintenance_off")
    assert result["outcome"] == "maintenance_off"
    assert rig.backend.bar_state["timer"] is False


async def test_crash_after_override_persistence_does_not_resume_or_send_on_restart(rig):
    async def interrupted(_state, **_kwargs):
        raise SystemExit("process interrupted before first power command")

    rig.editor.maintenance.ensure_power_off = interrupted
    with pytest.raises(SystemExit):
        await request(rig, "maintenance_on")
    assert rig.editor.maintenance.state()["enabled"]
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    assert rig.backend.sent == []
    assert rig.store.active()["status"] == "paused"


@pytest.mark.parametrize("field", ["identity", "image_sha256"])
async def test_resume_rejects_changed_bar_binding_or_image_before_power_on(rig, field):
    await request(rig, "maintenance_on")
    rig.backend.sent.clear()
    rig.backend.bar_state[field] = "0" * 64
    result = await request(rig, "maintenance_off")
    assert result["outcome"] == "error"
    assert rig.backend.sent == []
    assert rig.editor.maintenance.state()["enabled"]


async def test_interrupted_resume_stays_visible_and_cannot_resume_on_restart(rig):
    await request(rig, "maintenance_on")
    rig.backend.fail_target = "slave"
    result = await request(rig, "maintenance_off")
    assert result["outcome"] == "error"
    before = list(rig.backend.sent)
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    assert rig.backend.sent == before
    assert rig.store.active()["status"] == "paused"


async def test_ordinary_scheduler_still_rejects_power_off(rig):
    pair = replace(rig.backend.pair, master=replace(rig.backend.pair.master, power=False))
    scheduler = rig.editor._scheduler(rig.plan.sha256)
    images = [pair.master.image_sha256, pair.slave.image_sha256]
    with pytest.raises(ValueError, match="power"):
        scheduler._require_manual_pair(pair, images)
    scheduler._require_manual_pair(pair, images, require_power=False)
    wrong = PairState(pair.master, replace(pair.slave, identity="0" * 64))
    with pytest.raises(ValueError):
        scheduler._require_manual_pair(wrong, images, require_power=False)


async def test_shutdown_during_idle_read_keeps_verified_schedule_for_restart(rig, monkeypatch):
    before = rig.store.active()
    original = rig.backend.fresh_pair

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError("service shutdown")

    monkeypatch.setattr(rig.backend, "fresh_pair", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await rig.editor._scheduler(rig.plan.sha256).verify_idle()
    assert rig.store.active() == before
    monkeypatch.setattr(rig.backend, "fresh_pair", original)
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    assert rig.store.active() == before
    assert rig.backend.sent == []


async def test_shutdown_during_maintenance_read_cannot_clear_override(rig, monkeypatch):
    await request(rig, "maintenance_on")
    rig.backend.sent.clear()
    original = rig.backend.fresh_pair

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError("service shutdown")

    monkeypatch.setattr(rig.backend, "fresh_pair", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await rig.editor.maintenance.hold(force=True)
    monkeypatch.setattr(rig.backend, "fresh_pair", original)
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    assert rig.store.active()["status"] == "paused"
    assert restarted.maintenance.state()["enabled"]
    assert rig.backend.sent == []


@pytest.mark.parametrize("product", ["pro", "bar"])
@pytest.mark.parametrize("applied", [True, False])
async def test_uncertain_power_is_reconciled_by_one_read_without_replaying(
    tmp_path, product, applied
):
    from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
    from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
    from jebao_flow.local_control.models import LocalControlIntent
    from jebao_flow.local_control.resolver import ResolvedEndpoint
    from tests.unit.test_local_control_bar_timer_qualification import (
        _binding as bar_binding,
    )
    from tests.unit.test_local_control_bar_timer_qualification import (
        _status as bar_status,
    )
    from tests.unit.test_local_control_coordinator import (
        _binding,
        _coordinator,
        _Resolver,
        _Session,
        _SessionFactory,
        _status,
    )

    target = bar_binding() if product == "bar" else _binding("wavemaker_master", "a")
    status = bar_status if product == "bar" else _status
    endpoint = ResolvedEndpoint(target, "not-connected", wifi_firmware_version="wifi-current")
    session = _Session([status(enabled=not applied)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"not-connected": [session]}),
    )
    intent = LocalControlIntent(
        request_id="pro_manual_maintenance_power_test", target=target, function="power", value=False
    )
    prepared = ConnectedDeviceWriter(endpoint, control_flow_bounds=(30, 45)).prepare_scalar(
        intent,
        status(enabled=True),
    )
    record = coordinator._create_record(
        request_id=intent.request_id, intent_sha256="f" * 64, prepared=(prepared,)
    )
    uncertain = record.evolve(
        phase=TransactionPhase.RECOVERY_REQUIRED,
        actions=(record.actions[0].model_copy(update={"phase": ActionPhase.UNCERTAIN}),),
    )
    journal.save(record, uncertain)
    result = await coordinator.observe_unacknowledged_scalar(intent.request_id, endpoint=endpoint)
    assert result.physical_write_count == 0 and session.sent == []
    terminal = journal.load(intent.request_id)
    assert terminal.phase is TransactionPhase.ABANDONED
    assert terminal.actions[0].diagnostic == (
        "target_observed_without_ack" if applied else "target_not_observed_drop"
    )
    assert journal.load_blob(terminal.actions[0].external_observation_sha256)
    prior = json.loads(journal.load_blob(terminal.abandoned_prior_record_sha256))
    assert prior["phase"] == "recovery_required"
    assert journal.unfinished_for((target.physical_identity_key,)) == ()
    await coordinator.observe_unacknowledged_scalar(intent.request_id, endpoint=endpoint)
    assert session.sent == []


async def test_stop_preserves_original_roles_before_releasing_pair(rig):
    # Real 2026-10-25 capture: slave-first OFF has no ACK while master is ON.
    # This models that observed failure only; master-first firmware behavior is
    # established separately by the authorized real-device attempt.
    execute = rig.backend.execute

    async def observed_failure(child, request_id):
        if (child.target == "slave" and child.function == "power"
                and child.value is False and rig.backend.pair.master.power):
            return False, 1, "f" * 64
        return await execute(child, request_id)

    rig.backend.execute = observed_failure
    before = rig.backend.pair
    result = await request(rig, "maintenance_on")
    assert result["outcome"] == "maintenance_on"
    assert rig.editor.maintenance.state()["roles_before"] == {
        "master": before.master.role, "slave": before.slave.role,
    }
    assert rig.backend.pair.master.role == rig.backend.pair.slave.role == "independent"
    assert not rig.backend.pair.master.power and not rig.backend.pair.slave.power


async def test_captured_master_first_slave_rejection_stays_failed_without_replay(rig):
    # Actual master-first attempt, 2026-10-25 14:44–14:45 UTC:
    # master ACK/OFF, slave action-4 ON/feed reports without ACK; no role writes.
    # Retain the captured legacy flow as a failure fixture; the new release path
    # is covered separately by test_maintenance_roles.
    async def legacy_linked_pair(state, pair, bar):
        return pair, bar

    rig.editor.maintenance.release_pair = legacy_linked_pair
    execute = rig.backend.execute

    async def captured_behavior(child, request_id):
        if child.target == "slave" and child.function == "power" and child.value is False:
            rig.backend.sent.append((child, request_id))
            return False, 1, "f" * 64
        result = await execute(child, request_id)
        if child.target == "master" and child.function == "power" and child.value is False:
            rig.backend.pair = replace(
                rig.backend.pair, slave=replace(rig.backend.pair.slave, mode="feed")
            )
        return result

    rig.backend.execute = captured_behavior
    result = await request(rig, "maintenance_on")
    assert result["outcome"] == "error"
    assert rig.editor.maintenance.state()["failure_stage"] == "slave_power_off"
    assert not rig.backend.pair.master.power and rig.backend.pair.slave.power
    assert rig.backend.pair.master.role == "master"
    assert rig.backend.pair.slave.role == "async_slave"
    assert rig.backend.pair.slave.mode == "feed"
    sent = list(rig.backend.sent)
    await rig.editor.tick(rig.clock[0] + timedelta(minutes=25))
    restarted = ScheduleEditor(rig.store, rig.backend, clock=lambda: rig.clock[0])
    await restarted.initialize(rig.clock[0])
    assert rig.backend.sent == sent
    assert rig.store.active()["status"] == "paused"
    assert not any(c.function == "linkage_role" for c, _ in sent)
    assert not any(c.function == "power" and c.value is True for c, _ in sent)
