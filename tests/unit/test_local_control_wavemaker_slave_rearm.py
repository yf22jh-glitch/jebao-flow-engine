"""Image-free Async slave timer rearm over one source-owned durable child."""

import hashlib

import pytest

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import extract_current_pro_schedule_image
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.wavemaker_lifecycle import (
    native_slave_timer_rearm_request_id,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO as SCHEMA
from tests.unit.test_local_control_wavemaker_lifecycle import Rig, _put
from tests.unit.test_local_control_wavemaker_master_cycle import attach, lost_reply


async def _pending_source(rig):
    lost_reply(rig)
    # Operator-owned interval: a verified cycle keeps this source pending. See the same note
    # in test_local_control_wavemaker_power_cycle.
    result = await attach(rig, await_operator_observation=True)
    assert result["kind"] == "master_link_cycle_v1"
    source = rig.store.load("cycle_source_01")
    assert source.phase is TransactionPhase.RECOVERY_REQUIRED
    return source


def _stuck_feed_slave(rig):
    rig.states["slave"] = _put(
        rig.states["slave"],
        {
            "SwitchON": True,
            "TimerON": False,
            "Linkage": "async_slave",
            "Mode": "feed",
        },
    )


@pytest.mark.asyncio
async def test_rearm_releases_the_master_then_repairs_the_timer_and_rebinds(tmp_path):
    """The order is the one hardware accepted on 2026-09-20: a bound slave never acknowledges
    a control write (03:00:33 no ACK), an acknowledged one comes straight back once the master
    is released (03:03:56), the slave's own role restore does not disturb it (09:30:22.483),
    and the master taking the link back leaves TimerON alone (09:30:26.015)."""

    rig = Rig(tmp_path)
    source = await _pending_source(rig)
    _stuck_feed_slave(rig)
    master_image = extract_current_pro_schedule_image(rig.states["master"])
    slave_image = extract_current_pro_schedule_image(rig.states["slave"])
    writes = len(rig.sent)

    result = await rig.controller().rearm_async_slave_schedule(
        request_id=source.request_id,
        targets=rig.targets,
    )

    assert result["accepted"] and result["current_state_verified"]
    assert result["kind"] == "slave_timer_rearm_v1"
    assert result["schedule_image_written"] is False
    assert [(row[0], row[1]) for row in rig.sent[writes:]] == [
        ("master", {"Linkage": "independent"}),
        ("slave", {"SwitchON": False, "TimerON": True, "Linkage": "independent"}),
        ("slave", {"SwitchON": True}),
        ("slave", {"Linkage": "async_slave"}),
        ("master", {"Linkage": "master"}),
    ]
    assert all(not any(name.startswith("AutoTime") for name in row[1]) for row in rig.sent[writes:])
    assert extract_current_pro_schedule_image(rig.states["master"]) == master_image
    assert extract_current_pro_schedule_image(rig.states["slave"]) == slave_image
    values = SCHEMA.decode_status(rig.states["slave"])
    assert values["SwitchON"] is True
    assert values["TimerON"] is True
    assert values["Linkage"] == "async_slave"
    # The manual Mode is deliberately left alone: the link reverts anything written there.
    assert values["Mode"] == "feed"
    assert SCHEMA.decode_status(rig.states["master"])["Linkage"] == "master"
    assert all(row["complete"] for row in result["verification"]["devices"])

    child = rig.store.load(native_slave_timer_rearm_request_id(source.request_id))
    assert child.phase is TransactionPhase.VERIFIED
    assert all(action.phase is ActionPhase.VERIFIED for action in child.actions)
    # Each frame is bound to its own device's initial raw from the one fresh pair capture.
    master_actions = [a for a in child.actions if a.target.logical_member == "master"]
    slave_actions = [a for a in child.actions if a.target.logical_member == "slave"]
    assert len(master_actions) == 2 and len(slave_actions) == 3
    for group in (master_actions, slave_actions):
        assert {a.baseline_status_sha256 for a in group} == {group[0].baseline_status_sha256}
    assert master_actions[0].baseline_status_sha256 != slave_actions[0].baseline_status_sha256

    sent = len(rig.sent)
    replay = await rig.controller().rearm_async_slave_schedule(
        request_id=source.request_id,
        targets=rig.targets,
    )
    assert replay["current_state_verified"] is False
    assert len(rig.sent) == sent


@pytest.mark.asyncio
async def test_ack_loss_is_observed_without_replaying_the_timer_frame(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_source(rig)
    _stuck_feed_slave(rig)
    rig.fail_ack = len(rig.sent) + 1

    with pytest.raises(TimeoutError, match="synthetic ACK"):
        await rig.controller().rearm_async_slave_schedule(
            request_id=source.request_id,
            targets=rig.targets,
        )
    count = len(rig.sent)
    child_id = native_slave_timer_rearm_request_id(source.request_id)
    child = rig.store.load(child_id)
    assert child.actions[0].phase is ActionPhase.UNCERTAIN
    assert child.actions[0].acknowledgement_sha256 is None

    rig.fail_ack = None
    result = await rig.controller().rearm_async_slave_schedule(
        request_id=source.request_id,
        targets=rig.targets,
    )
    assert result["accepted"]
    assert len(rig.sent) == count + 4
    timer_payloads = [
        row for row in rig.sent if row[2] == child_id and row[1].get("TimerON") is True
    ]
    assert len(timer_payloads) == 1


@pytest.mark.asyncio
async def test_rearm_refuses_a_different_installed_image_before_journal_or_write(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_source(rig)
    _stuck_feed_slave(rig)
    raw = bytearray(rig.states["slave"])
    raw[11] ^= 1
    rig.states["slave"] = bytes(raw)
    writes = len(rig.sent)

    with pytest.raises(LocalControlExecutionError, match="시간표"):
        await rig.controller().rearm_async_slave_schedule(
            request_id=source.request_id,
            targets=rig.targets,
        )

    assert len(rig.sent) == writes
    assert rig.store.load(native_slave_timer_rearm_request_id(source.request_id)) is None


@pytest.mark.asyncio
async def test_rearm_child_is_bound_to_fresh_source_and_exact_initial_slave_raw(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_source(rig)
    _stuck_feed_slave(rig)
    initial = rig.states["slave"]

    await rig.controller().rearm_async_slave_schedule(
        request_id=source.request_id,
        targets=rig.targets,
    )

    child = rig.store.load(native_slave_timer_rearm_request_id(source.request_id))
    context = rig.controller()._context(child)
    assert context.attach_request_id == source.request_id
    assert context.source_context_sha256 == source.native_context_sha256
    assert rig.store.load_blob(context.originals[1].status_sha256) == initial
    assert hashlib.sha256(initial).hexdigest() == context.originals[1].status_sha256


@pytest.mark.asyncio
async def test_failed_rearm_keeps_source_exact_restore_available(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_source(rig)
    _stuck_feed_slave(rig)
    rig.reject_send = rig.fail_ack = len(rig.sent) + 3

    with pytest.raises(TimeoutError, match="synthetic ACK"):
        await rig.controller().rearm_async_slave_schedule(
            request_id=source.request_id,
            targets=rig.targets,
        )
    writes = len(rig.sent)
    rig.reject_send = rig.fail_ack = None
    restored = await rig.controller().recover_native(
        request_id=source.request_id,
        targets=rig.targets,
    )

    assert restored["accepted"]
    assert rig.states == rig.originals
    # Recovery may write the source-owned exact restore, but never resends a child forward frame.
    child_forwards = {
        rig.store.load_blob(action.forward_payload_sha256)
        for action in rig.store.load(native_slave_timer_rearm_request_id(source.request_id)).actions
    }
    assert not any(row[4] in child_forwards for row in rig.sent[writes:])


@pytest.mark.asyncio
async def test_power_and_role_frames_are_sent_although_prepared_as_no_change(tmp_path):
    """The restoring frames must not be gated on the preparation-time ``no_change`` flag.

    Both are prepared against the initial slave raw, where the device already holds the value
    they restore, so each carries ``no_change=True``.  Sending is decided from a fresh capture
    taken before the frame instead, and skipping them would leave the pump off and detached.
    """

    rig = Rig(tmp_path)
    source = await _pending_source(rig)
    _stuck_feed_slave(rig)
    slave_status = rig.states["slave"]
    writer = rig.controller()._writer_factory(rig.endpoints[1])
    try:
        prepared = {
            function: writer.prepare_scalar(
                LocalControlIntent(
                    request_id="rearm_no_change_probe",
                    target=rig.targets[1],
                    function=function,
                    value=value,
                ),
                slave_status,
            )
            for function, value in (("power", True), ("linkage_role", "async_slave"))
        }
    finally:
        await writer.close()
    assert prepared["power"].no_change is True
    assert prepared["linkage_role"].no_change is True

    writes = len(rig.sent)
    result = await rig.controller().rearm_async_slave_schedule(
        request_id=source.request_id,
        targets=rig.targets,
    )

    assert result["accepted"]
    functions = [row[1] for row in rig.sent[writes:]]
    assert {"SwitchON": True} in functions
    assert {"Linkage": "async_slave"} in functions
    values = SCHEMA.decode_status(rig.states["slave"])
    assert values["SwitchON"] is True
    assert values["Linkage"] == "async_slave"
