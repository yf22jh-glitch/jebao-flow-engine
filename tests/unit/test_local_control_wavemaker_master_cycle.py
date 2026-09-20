"""Durable two-command sequencing, not a firmware propagation model."""

import asyncio
import json
from pathlib import Path

import pytest

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalRecord,
    native_detach_request_id,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan
from jebao_flow.local_control.wavemaker_lifecycle import native_master_cycle_request_id
from jebao_flow.local_control.wavemaker_sequence import STAGED_ASYNC_ROLE
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO as SCHEMA
from tests.unit.test_local_control_wavemaker_lifecycle import Rig, _plan, _put


async def attach(rig, **kwargs):
    return await rig.controller().attach(
        request_id="cycle_source_01",
        plan=_plan("async"),
        targets=rig.targets,
        master_cycle_on_async_failure=True,
        **kwargs,
    )


def lost_reply(rig):
    rig.fail_ack = 9

    def transform(name, raw, reports):
        # Transport fault only: no mode changes or master-induced slave behavior.
        if len(rig.sent) == 9 and name == "slave" and not reports:
            raise TimeoutError("synthetic final reply unavailable")
        return raw

    rig.read_transform = transform


@pytest.mark.asyncio
async def test_cycle_preserves_source_delivery_and_replays_without_contact(tmp_path):
    rig = Rig(tmp_path)
    lost_reply(rig)
    result = await attach(rig)
    source = rig.store.load("cycle_source_01")
    assert result["accepted"] and result["current_state_verified"]
    assert result["kind"] == "master_link_cycle_v1"
    assert result["original_async_delivery"] == "UNKNOWN"
    assert source.actions[STAGED_ASYNC_ROLE].phase is ActionPhase.VERIFIED
    assert source.actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    assert (
        source.actions[STAGED_ASYNC_ROLE].diagnostic
        == "native_async_role_verified_by_master_cycle_delivery_unknown"
    )
    assert [(s[0], s[1]) for s in rig.sent[9:]] == [
        ("master", {"Linkage": "independent"}),
        ("master", {"Linkage": "master"}),
    ]
    assert sum(s[1] == {"Linkage": "async_slave"} for s in rig.sent) == 1
    final = result["verification"]
    assert all(row["complete"] for row in final["devices"])
    released = json.loads(rig.store.load_blob(final["previous"]))
    released = json.loads(rig.store.load_blob(released["previous"]))
    assert all(row["action"] == 3 for row in released["devices"])
    cycle = rig.store.load(result["request_id"])
    context = rig.controller()._context(cycle)
    # The snapshot keeps the stalled source exactly as the cycle found it. Closing the source
    # out adds the cycle's own explicit reply as evidence; it never rewrites that snapshot.
    stalled = LocalControlJournalRecord.model_validate_json(
        rig.store.load_blob(context.assisted_record_sha256)
    )
    assert stalled.request_id == source.request_id
    assert stalled.actions[STAGED_ASYNC_ROLE].phase is ActionPhase.UNCERTAIN
    assert stalled.actions[STAGED_ASYNC_ROLE].diagnostic == "native_stage_unverified"
    reads = len(rig.reads)
    assert not (await attach(rig))["current_state_verified"]
    assert len(rig.reads) == reads and len(rig.sent) == 11
    assert rig.store.load("cycle_source_01") == source
    # Cycle must not block the same source's ordinary owned restoration.
    restored = await rig.controller().recover_native(
        request_id="cycle_source_01", targets=rig.targets
    )
    assert restored["accepted"] and rig.states == rig.originals
    assert rig.store.unfinished_records() == ()
    status = rig.controller().status("cycle_source_01")["actions"][STAGED_ASYNC_ROLE]
    assert status["delivery"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_verified_cycle_releases_the_pair_for_ordinary_commands(tmp_path):
    """2026-09-18: the cycle put the pair back together in 40 s and verified the slave's role
    with its own explicit reply at 01:47:57.706, but the source stayed in recovery_required.
    A pending source owns the physical pump, so `manual_mode` — the one command that would
    have cleared the latent `feed` — was refused for the next 22 hours."""

    rig = Rig(tmp_path)
    lost_reply(rig)
    assert (await attach(rig))["kind"] == "master_link_cycle_v1"
    assert rig.store.unfinished_records() == ()
    # The 2026-09-18 state: the device left `feed` in the slave's manual block when it took
    # the role, and that value stops the pump the moment TimerON drops.
    rig.states["slave"] = _put(rig.states["slave"], {"Mode": "feed"})
    writes = len(rig.sent)

    result = await rig.controller().execute(
        LocalControlIntent(
            request_id="ordinary_after_cycle_01",
            target=rig.targets[1],
            function="manual_mode",
            value="constant",
        )
    )

    assert result.accepted
    assert [(row[0], row[1]) for row in rig.sent[writes:]] == [("slave", {"Mode": "constant"})]
    assert SCHEMA.decode_status(rig.states["slave"])["Mode"] == "constant"


@pytest.mark.asyncio
async def test_normal_success_does_not_create_cycle(tmp_path):
    rig = Rig(tmp_path)
    assert (await attach(rig))["accepted"]
    assert len(rig.sent) == 9
    assert rig.store.load(native_master_cycle_request_id("cycle_source_01")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cycle_ack", "released_reply", "cancel"])
async def test_cycle_failure_retains_state_and_owned_restore_is_not_blocked(tmp_path, failure):
    rig = Rig(tmp_path)
    lost_reply(rig)
    initial = rig.read_transform

    def transform(name, raw, reports):
        if len(rig.sent) == 10 and failure == "released_reply" and name == "slave":
            raise TimeoutError("synthetic released reply unavailable")
        return initial(name, raw, reports)

    rig.read_transform = transform

    def before(number, name, changes):
        if number == 10 and failure == "cycle_ack":
            rig.fail_ack = 10
        if number == 10 and failure == "cancel":
            rig.cancel_ack = 10

    rig.before_send = before
    expected_error = asyncio.CancelledError if failure == "cancel" else TimeoutError
    with pytest.raises(expected_error):
        await attach(rig)
    source = rig.store.load("cycle_source_01")
    cycle = rig.store.load(native_master_cycle_request_id(source.request_id))
    assert len(rig.sent) == 10
    assert cycle.phase.value == "recovery_required"
    assert rig.store.load(native_detach_request_id(source.request_id)) is None
    assert source.actions[STAGED_ASYNC_ROLE].phase is ActionPhase.UNCERTAIN
    count = len(rig.reads)
    assert not (await attach(rig))["accepted"]
    assert len(rig.sent) == 10 and len(rig.reads) == count
    rig.read_transform = None
    rig.fail_ack = rig.cancel_ack = None
    rig.before_send = None
    restored = await rig.controller().recover_native(
        request_id=source.request_id, targets=rig.targets
    )
    assert restored["accepted"] and rig.states == rig.originals
    assert rig.store.unfinished_records() == ()


@pytest.mark.asyncio
async def test_no_opt_in_and_other_unfinished_are_not_bypassed(tmp_path):
    rig = Rig(tmp_path)
    await rig.controller().attach(
        request_id="cycle_source_01",
        plan=_plan("async"),
        targets=rig.targets,
        await_operator_observation=True,
    )
    with pytest.raises(ValueError, match="승인"):
        await rig.controller().cycle_master(request_id="cycle_source_01", targets=rig.targets)
    assert len(rig.sent) == 9
    with pytest.raises(LocalControlExecutionError):
        await rig.attach(request_id="unrelated_01")


def test_user_daily_plan_exact_16_slots_and_only_flow_differs():
    plan = NativeWavemakerPlan.model_validate_json(
        Path("config/examples/pro-pair-daily-async.json").read_bytes()
    )
    slots = plan.schedule.slots
    assert len(slots) == 16
    assert slots[0].start == "00:00" and slots[-1].end == "24:00"
    assert all(a.end == b.start for a, b in zip(slots[:-1], slots[1:], strict=True))
    assert [s.flow for s in slots] == [
        35,
        40,
        30,
        45,
        45,
        40,
        45,
        30,
        40,
        45,
        35,
        30,
        45,
        40,
        40,
        35,
    ]
    assert [s.flow for s in plan.slaves[0].flows] == [
        30,
        30,
        30,
        35,
        35,
        30,
        35,
        30,
        30,
        35,
        30,
        30,
        35,
        35,
        35,
        30,
    ]
    assert [s.frequency for s in slots] == [70, 70, 0, 50, 0, 0, 50, 0, 30, 50, 0, 0, 0, 60, 70, 70]
    assert slots[8].mode == "circulation"
    images = plan.compile_images()
    master, slave = images.values()
    assert len(master) == len(slave) == 432
    assert master[144:] == slave[144:] == b"\xee" * 288
    assert all(i % 9 == 5 for i, (a, b) in enumerate(zip(master, slave, strict=True)) if a != b)


@pytest.mark.asyncio
async def test_unfinished_cycle_allows_only_its_owned_detach_and_scalar_assistance(tmp_path):
    rig = Rig(tmp_path)
    lost_reply(rig)
    prior = rig.read_transform

    def transform(name, raw, reports):
        if len(rig.sent) == 10 and name == "slave":
            raise TimeoutError("synthetic interrupted intermediate read")
        return prior(name, raw, reports)

    rig.read_transform = transform
    with pytest.raises(TimeoutError):
        await attach(rig)
    rig.read_transform = None
    rig.fail_ack = rig.reject_send = 11
    with pytest.raises(TimeoutError):
        await rig.controller().recover_native(request_id="cycle_source_01", targets=rig.targets)
    child = rig.store.load(native_detach_request_id("cycle_source_01"))
    assert child.actions[0].phase is ActionPhase.UNCERTAIN
    assert all(a.phase is ActionPhase.PREPARED for a in child.actions[1:])
    with pytest.raises(LocalControlExecutionError):
        await rig.attach(request_id="unrelated_02")
    result = await rig.controller().assist_detach(request_id="cycle_source_01", targets=rig.targets)
    assert result["accepted"] and rig.states == rig.originals
    assert rig.store.unfinished_records() == ()
    assert sum(s[1] == {"Linkage": "async_slave"} for s in rig.sent) == 1


@pytest.mark.asyncio
async def test_default_context_bytes_omit_cycle_and_cancel_does_not_cycle(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    context = rig.controller()._context(rig.store.load("attach_0001"))
    assert "master_cycle_on_async_failure" not in json.loads(context.model_dump_json())
    await rig.detach()
    rig.cancel_ack = len(rig.sent) + 9
    result = await attach(rig)
    assert not result["accepted"] and result["awaiting_operator_observation"]
    assert rig.store.load(native_master_cycle_request_id("cycle_source_01")) is None
