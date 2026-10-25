"""Role orchestration with the actual linked-slave rejection from 2026-10-25.

Independent commands model the existing qualified path, not a firmware proof.
The authorized real OFF and user resume remain separate acceptance evidence.
"""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from jebao_flow.local_control.external_manual_editor import ScheduleEditor
from tests.unit.test_maintenance_power_off import rig, request


@pytest.fixture
async def linked(rig):
    execute = rig.backend.execute

    async def observed_linked_rejection(child, request_id):
        if child.target == "slave" and child.function == "power" and rig.backend.pair.slave.role == "async_slave":
            rig.backend.sent.append((child, request_id))
            return False, 1, "f" * 64
        return await execute(child, request_id)

    rig.backend.execute = observed_linked_rejection
    return rig


async def test_detach_off_and_explicit_on_restore_original_pair_and_images(linked):
    r = linked
    before = r.backend.pair
    assert (await request(r, "maintenance_on"))["outcome"] == "maintenance_on"
    assert (r.backend.pair.master.role, r.backend.pair.slave.role) == ("independent", "independent")
    assert not r.backend.pair.master.power and not r.backend.pair.slave.power
    state = r.editor.maintenance.state()
    assert state["roles_before"] == {"master": "master", "slave": "async_slave"}
    assert state["roles_expected"] == {"master": "independent", "slave": "independent"}
    commands = [(c.target, c.function, c.value) for c, _ in r.backend.sent]
    assert commands.index(("master", "linkage_role", "independent")) < commands.index(("slave", "linkage_role", "independent")) < commands.index(("slave", "power", False))
    r.backend.sent.clear()
    assert (await request(r, "maintenance_off"))["outcome"] == "maintenance_off"
    commands = [(c.target, c.function, c.value) for c, _ in r.backend.sent]
    assert commands.index(("slave", "power", True)) < commands.index(("slave", "linkage_role", "async_slave")) < commands.index(("master", "linkage_role", "master"))
    assert (r.backend.pair.master.role, r.backend.pair.slave.role) == (before.master.role, before.slave.role)
    assert [r.backend.pair.master.image_sha256, r.backend.pair.slave.image_sha256] == [before.master.image_sha256, before.slave.image_sha256]
    assert r.store.active()["status"] == "active"


async def test_legacy_partial_off_error_adopts_original_roles_only_after_validation(linked):
    r = linked
    async def interrupted(_state):
        raise RuntimeError("old slave-first failure")
    original = r.editor.maintenance.ensure_power_off
    r.editor.maintenance.ensure_power_off = interrupted
    assert (await request(r, "maintenance_on"))["outcome"] == "error"
    state = r.editor.maintenance.state()
    for key in ("roles_before", "roles_expected", "role_transition"):
        state.pop(key, None)
    r.editor.maintenance.save(state)
    r.backend.pair = replace(r.backend.pair, master=replace(r.backend.pair.master, power=False))
    r.backend.bar_state.update(power=False, timer=False)
    r.editor.maintenance.ensure_power_off = original
    assert (await request(r, "maintenance_on"))["outcome"] == "maintenance_on"
    assert not any(c.function == "power" and c.value is True for c, _ in r.backend.sent)
    assert r.editor.maintenance.state()["roles_before"] == {"master": "master", "slave": "async_slave"}


@pytest.mark.parametrize("target", ["master", "slave"])
@pytest.mark.parametrize("applied", [False, True])
async def test_role_send_interruption_restart_does_not_replay_and_explicit_retry_reconciles(linked, target, applied):
    r = linked
    execute = r.backend.execute
    interrupted = False

    async def fail_role(child, request_id):
        nonlocal interrupted
        if not interrupted and child.target == target and child.function == "linkage_role":
            interrupted = True
            if applied:
                await execute(child, request_id)
            raise asyncio.CancelledError("process interrupted at role send")
        return await execute(child, request_id)

    r.backend.execute = fail_role
    with pytest.raises(asyncio.CancelledError):
        await request(r, "maintenance_on")
    before = list(r.backend.sent)
    restarted = ScheduleEditor(r.store, r.backend, clock=lambda: r.clock[0])
    await restarted.initialize(r.clock[0])
    assert r.backend.sent == before
    assert restarted.maintenance.state()["enabled"]
    r.editor = restarted
    assert (await request(r, "maintenance_on"))["outcome"] == "maintenance_on"
    assert r.editor.maintenance.state()["roles_expected"] == {"master": "independent", "slave": "independent"}
    assert not r.backend.pair.master.power and not r.backend.pair.slave.power


async def test_unowned_role_change_rejected_before_any_new_send(linked):
    r = linked
    await request(r, "maintenance_on")
    r.backend.sent.clear()
    r.backend.pair = replace(r.backend.pair, slave=replace(r.backend.pair.slave, role="sync_slave"))
    assert (await request(r, "maintenance_off"))["outcome"] == "error"
    assert r.backend.sent == []
    assert r.editor.maintenance.state()["enabled"]


async def test_successful_stop_restart_holds_off_without_role_or_power_writes(linked):
    r = linked
    await request(r, "maintenance_on")
    r.backend.sent.clear()
    r.clock[0] += timedelta(hours=3)
    restarted = ScheduleEditor(r.store, r.backend, clock=lambda: r.clock[0])
    await restarted.initialize(r.clock[0])
    await restarted.tick(r.clock[0])
    assert r.backend.sent == []
    assert restarted.maintenance.state()["phase"] == "powered_off"


async def test_owned_scheduler_release_interruption_can_resume_on_explicit_retry(linked):
    r = linked
    await request(r, "maintenance_on")
    execute = r.backend.execute
    r.backend.pair = replace(r.backend.pair, slave=replace(r.backend.pair.slave, flow=30))
    failed = False

    async def fail_scheduler(child, request_id):
        nonlocal failed
        result = await execute(child, request_id)
        if not failed and not request_id.startswith("pro_manual_maintenance_") and child.target == "master" and child.function == "linkage_role" and child.value == "independent":
            failed = True
            raise RuntimeError("scheduler failed after its owned release")
        return result

    r.backend.execute = fail_scheduler
    assert (await request(r, "maintenance_off"))["outcome"] == "error"
    assert r.backend.pair.master.role == "independent"
    assert r.editor.maintenance.state()["enabled"]
    assert (await request(r, "maintenance_off"))["outcome"] == "maintenance_off"
    assert r.backend.pair.master.role == "master"
    assert r.backend.pair.slave.role == "async_slave"


@pytest.mark.parametrize("target", ["master", "slave"])
@pytest.mark.parametrize("applied", [False, True])
async def test_interrupted_role_restore_keeps_override_and_recovers_only_on_explicit_finish(linked, target, applied):
    r = linked
    await request(r, "maintenance_on")
    execute = r.backend.execute
    failed = False

    async def interrupted_restore(child, request_id):
        nonlocal failed
        if not failed and child.target == target and child.function == "linkage_role" and child.value != "independent":
            failed = True
            if applied:
                await execute(child, request_id)
            raise asyncio.CancelledError("interrupted restoring original role")
        return await execute(child, request_id)

    r.backend.execute = interrupted_restore
    with pytest.raises(asyncio.CancelledError):
        await request(r, "maintenance_off")
    assert r.editor.maintenance.state()["enabled"]
    sent = list(r.backend.sent)
    restarted = ScheduleEditor(r.store, r.backend, clock=lambda: r.clock[0])
    await restarted.initialize(r.clock[0])
    assert r.backend.sent == sent
    assert r.store.active()["status"] == "paused"
    r.editor = restarted
    assert (await request(r, "maintenance_off"))["outcome"] == "maintenance_off"
    assert r.backend.pair.master.role == "master" and r.backend.pair.slave.role == "async_slave"
    assert r.store.active()["status"] == "active"


async def test_unresolved_role_journal_blocks_even_an_explicit_retry(linked):
    r = linked
    execute = r.backend.execute

    async def uncertain(child, request_id):
        if child.function == "linkage_role":
            r.backend.pending = True
            r.backend.sent.append((child, request_id))
            return False, 1, "f" * 64
        return await execute(child, request_id)

    r.backend.execute = uncertain
    assert (await request(r, "maintenance_on"))["outcome"] == "error"
    sent = list(r.backend.sent)
    assert (await request(r, "maintenance_on"))["outcome"] == "error"
    assert (await request(r, "maintenance_off"))["outcome"] == "error"
    assert r.backend.sent == sent
