"""Boot timing and journal recovery; no new firmware behavior is simulated."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from jebao_flow import local_control_external_manual_cli as scheduler_cli
from jebao_flow.local_control.external_manual_editor import ScheduleEditor
from tests.unit.test_external_manual_schedule import _at, _scheduler


async def _active(tmp_path, started, resumed):
    scheduler, backend, store = _scheduler(tmp_path, slave_timer=False)
    store.save_plan(scheduler.plan)
    await scheduler.activate(started)
    backend.sent.clear()
    clock = [resumed]
    editor = ScheduleEditor(store, backend, clock=lambda: clock[0])
    return scheduler, backend, store, editor, clock


@pytest.mark.asyncio
async def test_reboot_after_midnight_resumes_identical_row_without_a_write(tmp_path, capsys):
    # The preserved 2026-10-20 reboot state missed midnight by over 33 minutes.
    now = _at(0, 34) + timedelta(days=1)
    scheduler, backend, store, editor, _ = await _active(tmp_path, _at(21, 10), now)
    before = store.active()
    await editor.initialize(now)
    active = store.active()
    assert active["status"] == "active"
    assert active["last_event"] == scheduler.plan.event_key(now)
    assert active["next_boundary_at"] == scheduler.plan.boundary(now).isoformat()
    assert active["images"] == before["images"]
    assert active["schedule_sha256"] == before["schedule_sha256"]
    assert backend.sent == []
    assert store.operation(active["last_event"])["phase"] == "verified"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    attempt = next(e for e in events if e["outcome"] == "startup_catch_up_attempt")
    assert attempt["current_row_delay_seconds"] == 34 * 60
    assert attempt["missed_boundary_at"] == before["next_boundary_at"]
    assert events[-1]["row_result"]["writes"] == 0
    await editor.initialize(now)
    assert backend.sent == []


@pytest.mark.asyncio
async def test_startup_skips_elapsed_rows_and_applies_only_current_row_once(tmp_path):
    scheduler, backend, store, editor, clock = await _active(
        tmp_path, _at(13, 20), _at(13, 45)
    )
    await editor.initialize(clock[0])
    key = scheduler.plan.event_key(clock[0])
    assert store.active()["status"] == "active"
    assert store.active()["last_event"] == key
    assert [child.suffix for child, _ in backend.sent] == ["release", "slave_flow", "relink"]
    assert all(request_id.startswith(key) for _, request_id in backend.sent)
    assert store.operation(scheduler.plan.event_key(_at(13, 27))) is None
    assert store.operation(scheduler.plan.event_key(_at(13, 33))) is None
    sent = list(backend.sent)
    await editor.initialize(clock[0])
    assert backend.sent == sent


@pytest.mark.asyncio
async def test_startup_resamples_clock_after_discovery_wait(tmp_path, monkeypatch):
    scheduler, backend, store, editor, clock = await _active(
        tmp_path, _at(13, 20), _at(13, 26)
    )
    async def discovery_recovers(_plan):
        clock[0] = _at(13, 45)

    monkeypatch.setattr(backend, "validate_plan", discovery_recovers)
    await editor.initialize(_at(13, 26))
    assert store.active()["status"] == "active"
    assert store.active()["last_event"] == scheduler.plan.event_key(clock[0])
    assert store.operation(scheduler.plan.event_key(_at(13, 27))) is None
    assert all(child.value != "feed" for child, _ in backend.sent)


@pytest.mark.asyncio
async def test_startup_during_current_feeding_keeps_original_exit_time(tmp_path):
    _, backend, store, editor, clock = await _active(tmp_path, _at(13, 20), _at(13, 29))
    await editor.initialize(clock[0])
    assert store.active()["status"] == "active"
    assert store.active()["feed_exit_at"] == _at(13, 33).isoformat()
    assert [(c.function, c.value) for c, _ in backend.sent] == [("manual_mode", "feed")]


@pytest.mark.asyncio
async def test_operator_paused_schedule_stays_paused_on_boot(tmp_path):
    _, backend, store, editor, clock = await _active(tmp_path, _at(13, 20), _at(13, 45))
    state = store.active()
    state["status"] = "paused"
    store.set_active(state)
    await editor.initialize(clock[0])
    assert store.active() == state
    assert backend.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unfinished", [False, True])
async def test_startup_never_replays_attempted_or_unfinished_commands(
    tmp_path, monkeypatch, unfinished
):
    scheduler, backend, store, editor, clock = await _active(
        tmp_path, _at(13, 20), _at(13, 45)
    )
    if unfinished:
        monkeypatch.setattr(backend, "unfinished", lambda: True)
    else:
        store.start_operation(scheduler.plan.event_key(clock[0]), {"phase": "executing"})
    await editor.initialize(clock[0])
    assert store.active()["status"] == "paused"
    assert backend.sent == []
    error = editor.snapshot(clock[0], "unused")["last_error"]
    assert error["last_event"] == scheduler.plan.event_key(_at(13, 20))
    assert error["next_boundary_at"] == _at(13, 27).isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"flow": 41}, {"identity": "0" * 64}, {"image_sha256": "0" * 64},
    {"role": "independent"}, {"flow": 46}, {"timer": True}, {"power": False},
])
async def test_catch_up_preserves_device_and_previous_state_checks(tmp_path, change):
    _, backend, store, editor, clock = await _active(tmp_path, _at(13, 20), _at(13, 45))
    backend.pair = replace(backend.pair, slave=replace(backend.pair.slave, **change))
    await editor.initialize(clock[0])
    assert store.active()["status"] == "paused"
    assert backend.sent == []


@pytest.mark.asyncio
async def test_late_running_tick_does_not_use_startup_catch_up(tmp_path):
    _, backend, store, editor, clock = await _active(tmp_path, _at(13, 35), _at(13, 41))
    await editor.tick(clock[0])
    assert store.active()["status"] == "paused"
    assert backend.sent == []


@pytest.mark.asyncio
async def test_cli_run_also_resumes_current_row_after_downtime(tmp_path, monkeypatch):
    now = _at(0, 34) + timedelta(days=1)
    scheduler, backend, store, _, _ = await _active(tmp_path, _at(21, 10), now)

    class Clock(datetime):
        @classmethod
        def now(cls, _zone):
            return now

    class Finished(Exception):
        pass

    async def stop_after_startup(_seconds):
        raise Finished

    monkeypatch.setattr(scheduler_cli, "datetime", Clock)
    monkeypatch.setattr(scheduler_cli.asyncio, "sleep", stop_after_startup)
    with pytest.raises(Finished):
        await scheduler_cli._run_loop(scheduler)
    assert store.active()["status"] == "active"
    assert store.active()["last_event"] == scheduler.plan.event_key(now)
    assert backend.sent == []
