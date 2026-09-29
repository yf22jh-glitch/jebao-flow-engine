"""Pure schedule and parent-ledger tests; fake writes model code ordering only."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jebao_flow import local_control_external_manual_cli as scheduler_cli
from jebao_flow.local_control.external_manual_runtime import (
    Child,
    ExternalManualScheduler,
    PairState,
    PumpState,
    SchedulerStore,
)
from jebao_flow.local_control.external_manual_schedule import load_schedule, parse_schedule

_FILE = Path(__file__).resolve().parents[2] / "config/examples/pro-external-manual-daily.json"
_KST = timezone(timedelta(hours=9))
_MASTER = "4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea"
_SLAVE = "97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c"
_IMAGE_MASTER = "c" * 64
_IMAGE_SLAVE = "d" * 64


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, second, tzinfo=_KST)


def _pump(identity: str, role: str, *, timer: bool, mode: str, flow: int, image: str):
    return PumpState(3, identity, role, timer, True, mode, flow, 80, image, "e" * 64, ())


class FakeBackend:
    master_identity = _MASTER
    slave_identity = _SLAVE
    upper_is_master = False
    master_flow_bounds = (30, 45)
    slave_flow_bounds = (30, 45)

    def __init__(self, *, slave_timer: bool = True) -> None:
        self.pair = PairState(
            _pump(_MASTER, "master", timer=False, mode="pulse", flow=40, image=_IMAGE_MASTER),
            _pump(
                _SLAVE, "async_slave", timer=slave_timer, mode="pulse", flow=40, image=_IMAGE_SLAVE
            ),
        )
        self.sent: list[tuple[Child, str]] = []
        self.fail_on: str | None = None
        self.copy_on_relink = True

    async def validate_plan(self, _plan) -> None:
        return None

    def unfinished(self) -> bool:
        return False

    async def fresh_pair(self, *, preserve_raw: bool = True) -> PairState:
        return self.pair

    async def execute(self, child: Child, request_id: str) -> tuple[bool, int, str]:
        self.sent.append((child, request_id))
        if child.suffix == self.fail_on:
            return False, 1, "f" * 64
        master, slave = self.pair.master, self.pair.slave
        if child.target == "master":
            if child.function == "linkage_role":
                master = replace(master, role=child.value)
                if child.value == "master" and self.copy_on_relink:
                    slave = replace(slave, mode=master.mode)
            elif child.function == "manual_flow":
                master = replace(master, flow=child.value)
            elif child.function == "manual_frequency":
                master = replace(master, frequency=child.value)
            elif child.function == "manual_mode":
                master = replace(master, mode=child.value)
                if master.role == "master":
                    slave = replace(slave, mode=child.value)
            elif child.function == "schedule_enable":
                master = replace(master, timer=child.value, power=not child.value)
        elif child.function == "schedule_enable":
            slave = replace(slave, timer=child.value, power=not child.value, role="independent")
        elif child.function == "linkage_role":
            slave = replace(slave, role=child.value, mode=master.mode)
        elif child.function == "slave_common_flow":
            slave = replace(slave, flow=child.value)
        elif child.function == "manual_frequency":
            slave = replace(slave, frequency=child.value)
        self.pair = PairState(master, slave)
        return True, 1, "f" * 64


def _scheduler(tmp_path, *, slave_timer: bool = True):
    store = SchedulerStore(tmp_path / "state")
    backend = FakeBackend(slave_timer=slave_timer)
    plan = load_schedule(_FILE)
    return ExternalManualScheduler(plan=plan, store=store, backend=backend), backend, store


def test_user_table_has_exact_31_rows_nine_six_minute_feeds_and_no_tidal_frequency():
    plan = load_schedule(_FILE)
    assert len(plan.rows) == 31
    assert [row.label for row in plan.rows if row.feeding] == [
        "05:57",
        "06:57",
        "08:57",
        "10:27",
        "11:57",
        "13:27",
        "14:57",
        "16:27",
        "17:57",
    ]
    assert all(row.frequency is None for row in plan.rows if row.mode == "tidal")
    assert plan.current(_at(13, 41)).upper_flow == 42
    assert plan.current(_at(13, 41)).lower_flow == 40
    assert plan.boundary(_at(23, 59)) == _at(0, 0) + timedelta(days=1)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["rows"][1].update({"upper_flow": 0}),
        lambda d: d["rows"][8].update({"frequency": 80}),
        lambda d: d["rows"][2].update({"time": "06:04"}),
        lambda d: d["rows"][18].update({"upper_flow": 46}),
        lambda d: d["rows"][0].update({"mode": "random"}),
        lambda d: d["rows"][0].update({"frequency": True}),
        lambda d: d.update({"upper_identity_sha256": d["lower_identity_sha256"]}),
    ],
)
def test_unsafe_or_incomplete_row_is_rejected(change):
    document = json.loads(_FILE.read_text())
    change(document)
    with pytest.raises(ValueError):
        parse_schedule(json.dumps(document).encode())


def test_duplicate_keys_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        parse_schedule(b'{"schema_version":1,"schema_version":1}')


@pytest.mark.asyncio
async def test_activation_turns_slave_timer_off_before_current_row(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path)
    state = await scheduler.activate(_at(12, 30))
    assert state["status"] == "active"
    assert [child.suffix for child, _ in backend.sent] == ["slave_timer_off", "slave_relink"]
    assert backend.pair.slave.timer is False
    assert backend.pair.slave.role == "async_slave"
    assert backend.pair.master.image_sha256 == _IMAGE_MASTER
    assert backend.pair.slave.image_sha256 == _IMAGE_SLAVE
    assert store.operation(scheduler.plan.event_key(_at(12, 30)))["phase"] == "verified"


@pytest.mark.asyncio
async def test_activation_turns_both_timers_off_before_relink(tmp_path):
    scheduler, backend, _store = _scheduler(tmp_path)
    backend.pair = replace(backend.pair, master=replace(backend.pair.master, timer=True))
    await scheduler.activate(_at(12, 30))
    assert [child.suffix for child, _ in backend.sent] == [
        "slave_timer_off",
        "master_timer_off",
        "slave_relink",
    ]
    assert not backend.pair.master.timer and not backend.pair.slave.timer


@pytest.mark.asyncio
async def test_activation_rejects_current_output_above_configured_bound_before_write(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    backend.slave_flow_bounds = (30, 39)
    with pytest.raises(ValueError, match="configured output range"):
        await scheduler.activate(_at(12, 30))
    assert backend.sent == []
    assert store.active() is None


@pytest.mark.asyncio
async def test_activation_rejects_reversed_physical_upper_lower_binding_before_write(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    backend.upper_is_master = True
    with pytest.raises(ValueError, match="physical identity binding"):
        await scheduler.activate(_at(12, 30))
    assert backend.sent == []
    assert store.active() is None


@pytest.mark.asyncio
async def test_feed_and_six_minute_explicit_exit_then_slave_flow_change(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 20))
    backend.sent.clear()
    feed = await scheduler.apply_boundary(_at(13, 27))
    assert feed["writes"] == 1
    assert [child.function for child, _ in backend.sent] == ["manual_mode"]
    assert backend.pair.master.mode == backend.pair.slave.mode == "feed"
    assert store.active()["feed_exit_at"] == _at(13, 33).isoformat()
    backend.sent.clear()
    exit_result = await scheduler.apply_boundary(_at(13, 33))
    assert exit_result["writes"] == 3
    assert [child.suffix for child, _ in backend.sent] == ["release", "mode", "relink"]
    assert backend.pair.master.mode == backend.pair.slave.mode == "pulse"
    backend.sent.clear()
    change = await scheduler.apply_boundary(_at(13, 40))
    assert change["writes"] == 3
    assert [child.suffix for child, _ in backend.sent] == ["release", "slave_flow", "relink"]
    assert backend.pair.slave.flow == 42
    assert backend.pair.master.flow == 40


@pytest.mark.asyncio
async def test_failed_child_pauses_and_same_row_is_never_resent(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 35))
    backend.sent.clear()
    backend.fail_on = "slave_flow"
    with pytest.raises(ValueError, match="child did not verify"):
        await scheduler.apply_boundary(_at(13, 40))
    assert [child.suffix for child, _ in backend.sent] == ["release", "slave_flow"]
    assert store.active()["status"] == "paused"
    record = store.operation(scheduler.plan.event_key(_at(13, 40)))
    assert record["phase"] == "stopped"
    assert record["attempting_index"] == 1
    with pytest.raises(ValueError, match="not active"):
        await scheduler.apply_boundary(_at(13, 40))
    assert len(backend.sent) == 2


@pytest.mark.asyncio
async def test_duplicate_boundary_never_sends_and_external_drift_pauses(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 35))
    await scheduler.apply_boundary(_at(13, 40))
    before = len(backend.sent)
    duplicate = await scheduler.apply_boundary(_at(13, 40, 2))
    assert duplicate["outcome"] == "already_applied"
    assert len(backend.sent) == before
    backend.pair = replace(backend.pair, slave=replace(backend.pair.slave, mode="feed"))
    with pytest.raises(ValueError, match="external control"):
        await scheduler.verify_idle()
    assert store.active()["status"] == "paused"


@pytest.mark.asyncio
async def test_overdue_feed_exit_catches_up_only_once(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 20))
    await scheduler.apply_boundary(_at(13, 27))
    backend.sent.clear()
    result = await scheduler.apply_boundary(_at(13, 34))
    assert result["event"] == "13:33"
    count = len(backend.sent)
    again = await scheduler.apply_boundary(_at(13, 34, 5))
    assert again["outcome"] == "already_applied"
    assert len(backend.sent) == count
    assert store.active()["feed_exit_at"] is None


@pytest.mark.asyncio
async def test_tidal_row_has_no_frequency_write_and_maps_upper_flow_to_slave(tmp_path):
    scheduler, backend, _store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(8, 55))
    await scheduler.apply_boundary(_at(8, 57))
    backend.sent.clear()
    await scheduler.apply_boundary(_at(9, 3))
    functions = [child.function for child, _ in backend.sent]
    assert functions == ["linkage_role", "slave_common_flow", "manual_mode", "linkage_role"]
    assert backend.pair.slave.flow == 40
    assert backend.pair.master.flow == 40


@pytest.mark.asyncio
async def test_common_frequency_sets_slave_during_release_and_verifies_both(tmp_path):
    scheduler, backend, _store = _scheduler(tmp_path, slave_timer=False)
    backend.pair = replace(backend.pair, slave=replace(backend.pair.slave, frequency=70))
    await scheduler.activate(_at(13, 35))
    assert [child.suffix for child, _ in backend.sent] == [
        "release",
        "slave_frequency",
        "relink",
    ]
    assert backend.pair.master.frequency == backend.pair.slave.frequency == 80


@pytest.mark.asyncio
async def test_slave_feed_after_relink_is_a_stopped_row_not_success(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 35))
    backend.copy_on_relink = False
    backend.pair = replace(backend.pair, slave=replace(backend.pair.slave, mode="feed"))
    with pytest.raises(ValueError, match="external control"):
        await scheduler.apply_boundary(_at(13, 40))
    assert store.active()["status"] == "paused"


@pytest.mark.asyncio
async def test_slave_mode_mismatch_after_verified_children_stops_parent(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 35))
    backend.copy_on_relink = False
    await scheduler.apply_boundary(_at(13, 40))
    await scheduler.apply_boundary(_at(13, 50))
    backend.sent.clear()
    with pytest.raises(ValueError, match="final Pro pair"):
        await scheduler.apply_boundary(_at(14, 0))
    assert [child.suffix for child, _ in backend.sent] == [
        "release",
        "slave_flow",
        "mode",
        "relink",
    ]
    assert store.operation(scheduler.plan.event_key(_at(14, 0)))["phase"] == "stopped"
    assert store.active()["status"] == "paused"


def test_saved_schedule_is_exact_and_has_no_hardware_backend(tmp_path):
    store = SchedulerStore(tmp_path / "state")
    plan = load_schedule(_FILE)
    digest = store.save_plan(plan)
    assert store.save_plan(plan) == digest
    assert store.load_plan(digest) == plan
    assert store.active() is None


@pytest.mark.asyncio
async def test_preview_cli_never_builds_a_live_backend(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("preview must not initialize the safety volume or a device")

    monkeypatch.setattr(scheduler_cli, "SchedulerStore", forbidden)
    monkeypatch.setattr(scheduler_cli, "LiveBackend", forbidden)
    args = type(
        "Args", (), {"command": "preview", "file": _FILE, "at": "2026-09-29T13:41:00+09:00"}
    )()
    result = await scheduler_cli._main_async(args)
    assert result["current"] == "13:40"
    assert result["device_writes"] == 0


@pytest.mark.asyncio
async def test_running_loop_dispatches_one_new_boundary(tmp_path, monkeypatch):
    scheduler, backend, _store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 35))
    backend.sent.clear()
    moments = iter(
        (
            _at(13, 39, 59),
            _at(13, 39, 59),
            _at(13, 40),
            _at(13, 40),
            _at(13, 40, 1),
            _at(13, 40, 1),
        )
    )

    class Clock(datetime):
        @classmethod
        def now(cls, _zone):
            return next(moments)

    class Finished(Exception):
        pass

    sleeps = 0

    async def sleep_once(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise Finished

    monkeypatch.setattr(scheduler_cli, "datetime", Clock)
    monkeypatch.setattr(scheduler_cli.asyncio, "sleep", sleep_once)
    with pytest.raises(Finished):
        await scheduler_cli._run_loop(scheduler)
    assert [child.suffix for child, _ in backend.sent] == ["release", "slave_flow", "relink"]


@pytest.mark.asyncio
async def test_activation_does_not_send_previous_row_after_preflight_crosses_boundary(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    moments = iter((_at(13, 39, 59), _at(13, 40)))
    with pytest.raises(ValueError, match="scheduled row changed"):
        await scheduler.activate(_at(13, 39, 59), current_time=lambda: next(moments))
    assert backend.sent == []
    assert store.active()["status"] == "paused"


@pytest.mark.asyncio
async def test_boundary_does_not_send_stale_row_after_fresh_preflight(tmp_path):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    await scheduler.activate(_at(13, 35))
    backend.sent.clear()
    with pytest.raises(ValueError, match="scheduled row changed"):
        await scheduler.apply_boundary(_at(13, 40), current_time=lambda: _at(13, 50))
    assert backend.sent == []
    assert store.operation(scheduler.plan.event_key(_at(13, 40))) is None
    assert store.active()["status"] == "paused"
