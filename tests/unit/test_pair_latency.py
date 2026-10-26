"""Exercise the real pair adapter's remaining sequential wait and cleanup.

Only scheduling/lifetime is simulated, not new firmware behavior. Existing
maintenance fixtures preserve the established role/power/readback semantics.
"""

import asyncio
from types import SimpleNamespace

import pytest

from jebao_flow.protocol.errors import ProtocolTimeoutError
from tests.unit.test_maintenance_latency import live_reads
from tests.unit.test_maintenance_power_off import rig as rig


async def test_pair_reads_overlap_and_preserve_values_and_raw(rig, monkeypatch):
    io, stats = live_reads(rig, monkeypatch)
    stats.slow = True
    result = await io.fresh_pair()
    assert result == rig.backend.pair
    assert stats.peak == 2, "registered master/slave state reads still wait sequentially"
    assert stats.discovery == 1
    assert sorted(stats.opened) == sorted(stats.closed) == ["master", "slave"]
    assert len(stats.raw) == 2 and stats.active == 0


async def test_idle_pair_keeps_preserve_raw_false(rig, monkeypatch):
    io, stats = live_reads(rig, monkeypatch)
    assert await io.fresh_pair(preserve_raw=False) == rig.backend.pair
    assert stats.raw == [] and stats.discovery == 1
    assert sorted(stats.closed) == ["master", "slave"]


def controlled_writers(io):
    state = SimpleNamespace(
        started=[],
        closed=[],
        active=0,
        both=asyncio.Event(),
        release=asyncio.Event(),
        fail=False,
        close_fail=False,
    )

    class Writer:
        def __init__(self, endpoint):
            self.member = endpoint.target.logical_member

        async def connect_and_capture(self, *, accept_reports):
            assert accept_reports == (self.member == "slave")
            state.started.append(self.member)
            state.active += 1
            if len(state.started) % 2 == 0:
                state.both.set()
            try:
                if self.member == "master" and state.fail:
                    await state.both.wait()
                    raise ProtocolTimeoutError("injected timeout after both readers started")
                await state.release.wait()
                return SimpleNamespace(
                    status_payload=self.member.encode(), wire_frame=self.member.encode()
                )
            finally:
                state.active -= 1

        async def close(self):
            await asyncio.sleep(0)
            state.closed.append(self.member)
            if self.member == "master" and state.close_fail:
                raise ValueError("injected close failure")

    io.writer_factory = Writer
    return state


async def test_cancel_pair_drains_both_sessions(rig, monkeypatch):
    io, _ = live_reads(rig, monkeypatch)
    state = controlled_writers(io)
    task = asyncio.create_task(io.fresh_pair())
    try:
        await asyncio.wait_for(state.both.wait(), 0.2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert state.active == 0 and sorted(state.closed) == ["master", "slave"]


async def test_failed_pair_drains_before_retry_and_reads_both_again(rig, monkeypatch):
    io, stats = live_reads(rig, monkeypatch)
    state = controlled_writers(io)
    state.fail = True

    async def retry(_):
        assert state.active == 0 and sorted(state.closed) == ["master", "slave"]
        state.fail = False
        state.release.set()

    io.read_retry.wait = retry
    assert await asyncio.wait_for(io.fresh_pair(), 0.5) == rig.backend.pair
    assert stats.discovery == 2 and state.active == 0
    assert sorted(state.started) == sorted(state.closed) == ["master", "master", "slave", "slave"]


async def test_one_close_failure_does_not_leave_other_reader_running(rig, monkeypatch):
    io, _ = live_reads(rig, monkeypatch)
    state = controlled_writers(io)
    state.release.set()
    state.close_fail = True
    with pytest.raises(ValueError, match="close failure"):
        await io.fresh_pair()
    assert state.active == 0 and sorted(state.closed) == ["master", "slave"]


async def test_invalid_decoded_pair_never_returns_partial_success(rig, monkeypatch):
    io, stats = live_reads(rig, monkeypatch)
    decode = io._decode

    def invalid(capture, target, *, preserve_raw):
        if target == io.master:
            raise ValueError("injected invalid master fields")
        return decode(capture, target, preserve_raw=preserve_raw)

    io._decode = invalid
    with pytest.raises(ValueError, match="invalid master"):
        await io.fresh_pair()
    assert stats.active == 0 and sorted(stats.closed) == ["master", "slave"]


async def test_editor_owns_scope_for_whole_start_finish_and_closes_after_each(rig):
    from contextlib import asynccontextmanager

    from tests.unit.test_maintenance_power_off import request

    active = False
    entered = 0
    exited = 0

    @asynccontextmanager
    async def scope():
        nonlocal active, entered, exited
        assert not active
        active = True
        entered += 1
        try:
            yield
        finally:
            active = False
            exited += 1

    rig.backend.maintenance_session_scope = scope
    execute = rig.backend.execute

    async def checked_execute(*args, **kwargs):
        assert active, "maintenance command escaped its connection ownership scope"
        return await execute(*args, **kwargs)

    rig.backend.execute = checked_execute
    assert (await request(rig, "maintenance_on"))["outcome"] == "maintenance_on"
    assert not active and entered == exited == 1
    assert (await request(rig, "maintenance_off"))["outcome"] == "maintenance_off"
    assert not active and entered == exited == 2
