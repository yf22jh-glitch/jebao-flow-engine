"""Owner-aware master power cycle; no firmware fault-clear behavior is modeled."""

import json

import pytest

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.local_control.wavemaker_lifecycle import (
    native_master_power_cycle_request_id,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO as SCHEMA
from tests.unit.test_local_control_wavemaker_lifecycle import Rig, _put
from tests.unit.test_local_control_wavemaker_master_cycle import attach, lost_reply


async def _pending_owned_pair(rig):
    lost_reply(rig)
    # The operator explicitly owns the interval, so a verified cycle still leaves the source
    # pending. Without that flag the cycle now closes the source out with its own explicit
    # reply and the pair is released, which is what this child exists to avoid needing.
    result = await attach(rig, await_operator_observation=True)
    assert result["kind"] == "master_link_cycle_v1"
    assert result["accepted"]
    source = rig.store.load("cycle_source_01")
    assert source.phase is TransactionPhase.RECOVERY_REQUIRED
    return source


@pytest.mark.asyncio
async def test_owned_master_power_cycle_writes_only_off_wait_on_and_preserves_source(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_owned_pair(rig)
    writes_before = len(rig.sent)
    reads_before = len(rig.reads)

    result = await rig.controller().cycle_master_power(
        request_id=source.request_id, targets=rig.targets
    )

    assert result["accepted"] and result["current_state_verified"]
    assert result["kind"] == "master_power_cycle_v1"
    assert result["power_off_seconds"] == 10
    assert result["original_async_delivery"] == "UNKNOWN"
    assert [(row[0], row[1]) for row in rig.sent[writes_before:]] == [
        ("master", {"SwitchON": False}),
        ("master", {"SwitchON": True}),
    ]
    cycle_reads = rig.reads[reads_before:]
    assert cycle_reads[:2] == [("master", False), ("slave", True)]
    assert cycle_reads[-2:] == [("master", False), ("slave", True)]
    assert any(9.9 <= seconds <= 10 for seconds in rig.sleeps)
    assert SCHEMA.decode_status(rig.states["master"])["SwitchON"] is True
    assert SCHEMA.decode_status(rig.states["master"])["Linkage"] == "master"
    assert SCHEMA.decode_status(rig.states["slave"])["Linkage"] == "async_slave"
    assert all(row["complete"] for row in result["verification"]["devices"])
    assert rig.store.load(source.request_id) == source

    child = rig.store.load(native_master_power_cycle_request_id(source.request_id))
    assert child.phase is TransactionPhase.VERIFIED
    assert [action.restore_on_recovery for action in child.actions] == [False, True]
    assert all(action.phase is ActionPhase.VERIFIED for action in child.actions)
    context = rig.controller()._context(child)
    assert context.originals != rig.controller()._context(source).originals
    assert json.loads(rig.store.load_blob(context.power_cycle_basis_sha256))["phase"] == "verified"

    reads = len(rig.reads)
    replay = await rig.controller().cycle_master_power(
        request_id=source.request_id, targets=rig.targets
    )
    assert not replay["current_state_verified"]
    assert len(rig.sent) == writes_before + 2 and len(rig.reads) == reads


@pytest.mark.asyncio
async def test_on_ack_loss_is_observed_without_resending_and_finishes(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_owned_pair(rig)
    rig.fail_ack = len(rig.sent) + 2

    with pytest.raises(TimeoutError, match="synthetic ACK"):
        await rig.controller().cycle_master_power(request_id=source.request_id, targets=rig.targets)
    writes = len(rig.sent)
    child_id = native_master_power_cycle_request_id(source.request_id)
    child = rig.store.load(child_id)
    assert child.actions[0].phase is ActionPhase.VERIFIED
    assert child.actions[1].phase is ActionPhase.UNCERTAIN
    assert SCHEMA.decode_status(rig.states["master"])["SwitchON"] is True

    rig.fail_ack = None
    result = await rig.controller().cycle_master_power(
        request_id=source.request_id, targets=rig.targets
    )
    assert result["accepted"]
    assert len(rig.sent) == writes
    assert rig.store.load(child_id).actions[1].phase is ActionPhase.VERIFIED


@pytest.mark.asyncio
async def test_rejected_on_is_never_resent_and_master_remains_off(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_owned_pair(rig)
    rig.reject_send = rig.fail_ack = len(rig.sent) + 2

    with pytest.raises(TimeoutError, match="synthetic ACK"):
        await rig.controller().cycle_master_power(request_id=source.request_id, targets=rig.targets)
    writes = len(rig.sent)
    assert SCHEMA.decode_status(rig.states["master"])["SwitchON"] is False

    rig.reject_send = rig.fail_ack = None
    with pytest.raises(LocalControlExecutionError, match="재전송"):
        await rig.controller().cycle_master_power(request_id=source.request_id, targets=rig.targets)
    assert len(rig.sent) == writes
    assert SCHEMA.decode_status(rig.states["master"])["SwitchON"] is False


@pytest.mark.asyncio
async def test_already_off_master_is_not_automatically_started(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_owned_pair(rig)
    rig.states["master"] = _put(rig.states["master"], {"SwitchON": False})
    writes = len(rig.sent)

    with pytest.raises(LocalControlExecutionError, match="이미 OFF"):
        await rig.controller().cycle_master_power(request_id=source.request_id, targets=rig.targets)
    assert len(rig.sent) == writes
    assert rig.store.load(native_master_power_cycle_request_id(source.request_id)) is None


@pytest.mark.asyncio
async def test_power_cycle_requires_the_owned_pending_async_source(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach("async", request_id="normal_source_01")
    with pytest.raises(ValueError, match="소유권"):
        await rig.controller().cycle_master_power(
            request_id="normal_source_01", targets=rig.targets
        )


@pytest.mark.asyncio
async def test_source_detach_remains_available_after_power_cycle(tmp_path):
    rig = Rig(tmp_path)
    source = await _pending_owned_pair(rig)
    result = await rig.controller().cycle_master_power(
        request_id=source.request_id, targets=rig.targets
    )
    assert result["accepted"]
    restored = await rig.controller().recover_native(
        request_id=source.request_id, targets=rig.targets
    )
    assert restored["accepted"] and rig.states == rig.originals
    assert rig.store.unfinished_records() == ()
