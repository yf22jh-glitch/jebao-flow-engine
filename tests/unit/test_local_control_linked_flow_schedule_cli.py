"""The latest user table drives only manual slave Flow; Feeding never sends Flow 0."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jebao_flow import local_control_linked_flow_schedule_cli as schedule_cli

_SOURCE = Path(__file__).resolve().parents[2] / "config" / "pro-linked-flow-daily.example.json"
_KST = timezone(timedelta(hours=9))


def _args(*, write_enabled: bool = False, apply_current: bool = False, at: str | None = None):
    return argparse.Namespace(
        schedule_file=_SOURCE,
        master_member="flow_pro_d0a4",
        slave_member="flow_pro_d7a8",
        write_enabled=write_enabled,
        apply_current=apply_current,
        at=at,
    )


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 25, hour, minute, tzinfo=_KST)


def test_latest_table_has_all_31_boundaries_and_nine_feeding_rows() -> None:
    plan = schedule_cli.load_schedule(_SOURCE)
    assert len(plan.events) == 31
    expected_times = (
        "00:00 05:57 06:03 06:57 07:03 08:40 08:50 08:57 "
        "09:03 10:27 10:33 11:40 11:50 11:57 12:03 13:27 "
        "13:33 13:40 13:50 14:00 14:57 15:03 16:27 16:33 "
        "17:40 17:50 17:57 18:03 20:40 20:50 21:00"
    ).split()
    expected_feeding = ("05:57 06:57 08:57 10:27 11:57 13:27 14:57 16:27 17:57").split()
    assert [event.label for event in plan.events] == expected_times
    assert [event.label for event in plan.events if event.flow is None] == expected_feeding
    assert {event.flow for event in plan.events if event.flow is not None} == {40}


@pytest.mark.parametrize(
    ("time", "expected_time", "flow"),
    [
        ((5, 56), "00:00", 40),
        ((5, 57), "05:57", None),
        ((6, 2), "05:57", None),
        ((6, 3), "06:03", 40),
        ((8, 40), "08:40", 40),
        ((23, 59), "21:00", 40),
    ],
)
def test_current_row_is_active_until_the_next_boundary(time, expected_time, flow) -> None:
    event = schedule_cli.load_schedule(_SOURCE).current(_at(*time))
    assert (event.label, event.flow) == (expected_time, flow)


def test_next_boundary_wraps_to_next_day() -> None:
    plan = schedule_cli.load_schedule(_SOURCE)
    assert plan.next_boundary(_at(5, 58)) == _at(6, 3)
    assert plan.next_boundary(_at(21, 1)) == datetime(2026, 9, 26, 0, 0, tzinfo=_KST)


@pytest.mark.asyncio
async def test_preview_and_not_due_never_touch_the_device(monkeypatch) -> None:
    async def forbidden(_args):
        raise AssertionError("hardware executor must not run")

    monkeypatch.setattr(schedule_cli.linked_flow, "_run", forbidden)
    preview = await schedule_cli.run_once(_args(), now=_at(8, 40))
    assert preview["outcome"] == "preview_only"
    assert preview["due_now"] is True
    assert preview["device_writes"] == 0
    not_due = await schedule_cli.run_once(_args(write_enabled=True), now=_at(8, 41))
    assert not_due == {"outcome": "not_due", "event": "08:40", "device_writes": 0}


@pytest.mark.asyncio
async def test_feeding_boundary_never_sends_even_with_write_enabled(monkeypatch) -> None:
    async def forbidden(_args):
        raise AssertionError("Feeding must not request Flow")

    monkeypatch.setattr(schedule_cli.linked_flow, "_run", forbidden)
    result = await schedule_cli.run_once(_args(write_enabled=True), now=_at(5, 57))
    assert result == {"outcome": "feeding_no_flow_write", "event": "05:57", "device_writes": 0}


@pytest.mark.asyncio
async def test_due_event_uses_the_existing_three_child_executor_once(monkeypatch) -> None:
    received = []

    async def record(args):
        received.append(args)
        return {"outcome": "verified", "flow": args.flow, "device_writes": 3}

    monkeypatch.setattr(schedule_cli.linked_flow, "_run", record)
    plan = schedule_cli.load_schedule(_SOURCE)
    result = await schedule_cli.run_once(_args(write_enabled=True), now=_at(8, 40))
    assert len(received) == 1
    assert received[0].flow == 40
    assert received[0].master_member == "flow_pro_d0a4"
    assert received[0].slave_member == "flow_pro_d7a8"
    assert received[0].write_enabled is True
    assert received[0].request_id == f"linked_flow_20260925_0520_{plan.sha256[:12]}"
    assert result["outcome"] == "verified"
    assert result["device_writes"] == 3


@pytest.mark.asyncio
async def test_synthetic_time_cannot_authorize_a_write(monkeypatch) -> None:
    async def forbidden(_args):
        raise AssertionError("synthetic time must not reach hardware")

    monkeypatch.setattr(schedule_cli.linked_flow, "_run", forbidden)
    with pytest.raises(ValueError, match="synthetic time"):
        await schedule_cli.run_once(
            _args(write_enabled=True, at="2026-09-25T08:40:00+09:00"), now=_at(8, 40)
        )


@pytest.mark.asyncio
async def test_apply_current_is_an_explicit_one_shot(monkeypatch) -> None:
    received = []

    async def record(args):
        received.append(args)
        return {"outcome": "already_at_target", "flow": args.flow, "device_writes": 0}

    monkeypatch.setattr(schedule_cli.linked_flow, "_run", record)
    result = await schedule_cli.run_once(
        _args(write_enabled=True, apply_current=True), now=_at(8, 24)
    )
    assert len(received) == 1
    assert received[0].flow == 40
    assert result["device_writes"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["events"].insert(0, {"time": "00:00", "flow": 40}),
        lambda data: data["events"].__setitem__(0, {"time": "00:01", "flow": 40}),
        lambda data: data["events"].__setitem__(1, {"time": "00:00", "flow": None}),
        lambda data: data["events"].__setitem__(0, {"time": "00:00", "flow": True}),
        lambda data: data["events"].__setitem__(0, {"time": "00:00", "flow": 46}),
        lambda data: data["events"].__setitem__(0, {"time": "24:00", "flow": 40}),
        lambda data: data["events"].__setitem__(2, {"time": "06:03", "flow": 30}),
    ],
)
def test_invalid_or_unsafe_schedule_stops_before_any_hardware_access(tmp_path, mutation) -> None:
    data = json.loads(_SOURCE.read_text())
    mutation(data)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        schedule_cli.load_schedule(path)


def test_duplicate_json_key_is_rejected(tmp_path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"timezone":"Asia/Seoul",'
        '"source_run":"x","events":[{"time":"00:00","flow":40}]}'
    )
    with pytest.raises(ValueError, match="duplicate"):
        schedule_cli.load_schedule(path)


@pytest.mark.asyncio
async def test_loop_waits_for_the_next_boundary_and_dispatches_once(monkeypatch, capsys) -> None:
    class Finished(Exception):
        pass

    moments = iter((_at(8, 39), _at(8, 39), _at(8, 40)))

    class Clock(datetime):
        @classmethod
        def now(cls, _tz):
            return next(moments)

    dispatched = []

    async def record(_schedule, event, _now, **_kwargs):
        dispatched.append(event.label)
        return {"outcome": "verified", "device_writes": 3}

    sleeps = 0

    async def stop_after_one_boundary(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise Finished

    monkeypatch.setattr(schedule_cli, "datetime", Clock)
    monkeypatch.setattr(schedule_cli, "_apply_event", record)
    monkeypatch.setattr(schedule_cli.asyncio, "sleep", stop_after_one_boundary)
    args = _args(write_enabled=True)
    args.loop = True
    with pytest.raises(Finished):
        await schedule_cli.run_loop(args)
    assert dispatched == ["08:40"]
    assert '"outcome": "armed_for_next_boundary"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_loop_stops_on_backward_host_clock_before_any_write(monkeypatch) -> None:
    moments = iter((_at(8, 40).replace(microsecond=500000), _at(8, 40)))

    class Clock(datetime):
        @classmethod
        def now(cls, _tz):
            return next(moments)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("backward clock must not reach hardware")

    monkeypatch.setattr(schedule_cli, "datetime", Clock)
    monkeypatch.setattr(schedule_cli, "_apply_event", forbidden)
    args = _args(write_enabled=True)
    args.loop = True
    with pytest.raises(ValueError, match="moved backward"):
        await schedule_cli.run_loop(args)
