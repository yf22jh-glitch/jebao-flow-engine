"""Software order/fault contracts only; byte-copy rig does not predict firmware timing."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase, native_detach_request_id
from jebao_flow.local_control.wavemaker_lifecycle import NativeContext, NativeOriginal
from jebao_flow.local_control.wavemaker_sequence import (
    STAGED_ROLES,
    STAGED_ROLES_V2,
    SequenceDeviceWriter,
    staged_controls,
    staged_layout,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO as SCHEMA
from jebao_flow.protocol.session import RawStateCapture
from tests.unit.test_local_control_wavemaker_lifecycle import (
    STAGED_ASYNC_ROLE,
    MemoryTransport,
    Rig,
    _plan,
    _put,
)


def _chain(rig, record):
    digest = record.native_final_sha256
    while digest:
        row = json.loads(rig.store.load_blob(digest))
        yield row
        digest = row["previous"]


@pytest.mark.asyncio
async def test_staged_commands_pair_barrier_pacing_and_explicit_slave_preparation(tmp_path):
    rig = Rig(tmp_path)
    plan = _plan("async").model_copy(update={"preparation_slave_flow": 31})
    result = await rig.controller().attach(request_id="attach_0001", plan=plan, targets=rig.targets)
    assert result["accepted"]
    assert len(rig.sent) == 9
    assert [s[0] for s in rig.sent] == [
        "master",
        "slave",
        "master",
        "slave",
        "master",
        "slave",
        "master",
        "slave",
        "slave",
    ]
    for offset, timer in ((0, False), (4, True)):
        for relative, flow in enumerate((32, 31)):
            assert rig.sent[offset + relative][1] == {
                "SwitchON": True,
                "TimerON": timer,
                "Linkage": "independent",
                "Mode": "constant",
                "Flow": flow,
                "Frequency": 21,
            }
    assert [s[1] for s in rig.sent[6:]] == [
        {"Linkage": "master"},
        {"Linkage": "sync_slave"},
        {"Linkage": "async_slave"},
    ]
    sends = [(i, e) for i, e in enumerate(rig.events) if e[0] == "send"]
    for name in ("master", "slave"):
        times = [e[2] for _, e in sends if e[1].address == name]
        assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))
    for index, event in sends[6:]:
        current = event[1]
        auth = max(
            i for i, e in enumerate(rig.events[:index]) if e[:2] == ("authenticate", current)
        )
        # Both new writers authenticate before either read; the role uses that read's writer.
        assert rig.events[auth - 1][0] in {"authenticate", "connect"}
        previous_auths = [e for e in rig.events[:index] if e[0] == "authenticate"][-2:]
        first_pair_read = next(i for i in range(auth + 1, index) if rig.events[i][0] == "read")
        assert all(rig.events.index(e) < first_pair_read for e in previous_auths)
        assert [e[0] for e in rig.events[auth + 1 : index] if e[1] is current] == ["read"]
    # Nine post-ACK waits plus the remaining half-second of the Sync→Async interval.
    assert rig.sleeps.count(0.5) == 10 and 1.0 in rig.sleeps
    connected = {e[1] for e in rig.events if e[0] == "connect"}
    assert all(s.closed == 1 for s in connected)
    restored = await rig.detach()
    assert restored["accepted"] and rig.states == rig.originals


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["transport", "mismatch"])
async def test_after_reads_retry_without_control_resend_and_quarantine_is_not_reused(
    tmp_path, monkeypatch, fault
):
    rig = Rig(tmp_path)
    original = MemoryTransport.read_raw_state_capture
    attempts = []

    async def read(self, *, accept_reports, **kwargs):
        if len(rig.sent) == 1 and self.address == "master" and len(attempts) < 3:
            attempts.append(self)
            if len(attempts) < 3:
                if fault == "transport":
                    raise TimeoutError("injected read transport loss")
                raw = await original(self, accept_reports=accept_reports, **kwargs)
                return RawStateCapture(
                    raw.wire_frame, raw.action, _put(raw.status_payload, {"Flow": 33})
                )
        return await original(self, accept_reports=accept_reports, **kwargs)

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    assert (await rig.attach("async"))["accepted"]
    assert len(attempts) == 3
    assert len(set(attempts)) == (3 if fault == "transport" else 1)
    assert len(rig.sent) == 9
    connected = {e[1] for e in rig.events if e[0] == "connect"}
    assert all(s.closed == 1 for s in connected)
    assert len([e for e in rig.events if e[0] == "quarantine"]) == (
        2 if fault == "transport" else 0
    )
    assert (await rig.detach())["accepted"] and rig.states == rig.originals


@pytest.mark.asyncio
async def test_rejected_report_before_reply_timeout_is_preserved_not_accepted(
    tmp_path, monkeypatch
):
    rig = Rig(tmp_path)
    rig.fail_ack = 9
    original = MemoryTransport.read_raw_state_capture

    async def read(self, *, accept_reports, state_frame_observer=None):
        if len(rig.sent) == 9 and self.address == "slave" and not accept_reports:
            report = RawStateCapture(b"injected-report", 4, rig.states["slave"])
            state_frame_observer(report, False)
            raise TimeoutError("injected reply absence after report")
        return await original(
            self, accept_reports=accept_reports, state_frame_observer=state_frame_observer
        )

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    with pytest.raises(LocalControlExecutionError, match="자기 원본 복원 확인"):
        await rig.attach("async")
    source = rig.store.load("attach_0001")
    frames = [
        d
        for row in _chain(rig, source)
        for d in row["devices"]
        if d.get("stage") == "after_ack_error_reply_frames"
    ]
    assert len(frames) == 1 and frames[0]["selected"] is False
    capture = json.loads(rig.store.load_blob(frames[0]["capture_sha256"]))
    assert capture["action"] == 4
    assert source.actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    assert (
        source.actions[STAGED_ASYNC_ROLE].diagnostic
        == "native_own_baseline_restored_delivery_unknown"
    )
    assert sum(s[1] == {"Linkage": "async_slave"} for s in rig.sent) == 1
    assert rig.states == rig.originals


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_staged_sync_stage_failure_has_owned_assistance_and_immutable_context(tmp_path, mode):
    rig = Rig(tmp_path)

    def fail(number, address, changes):
        if number in {8, 9}:
            raise TimeoutError("injected slave Sync and child failure")

    rig.before_send = fail
    with pytest.raises(LocalControlExecutionError, match="원복 미확인"):
        await rig.attach(mode)
    source = rig.store.load("attach_0001")
    controller = rig.controller()
    context = controller._context(source)
    sync_role = STAGED_ASYNC_ROLE - 1
    assert context.activation_sequence == STAGED_ROLES
    assert source.actions[sync_role].phase is ActionPhase.UNCERTAIN
    assert all(a.phase is ActionPhase.PREPARED for a in source.actions[sync_role + 1 :])
    controller._require_assistance_source(source, context)
    with pytest.raises(ValueError):
        controller._require_assistance_source(
            source, context.model_copy(update={"activation_sequence": None})
        )
    rig.before_send = None
    result = await controller.assist_detach(request_id=source.request_id, targets=rig.targets)
    assert result["accepted"] and rig.states == rig.originals
    assert (
        rig.store.load(native_detach_request_id(source.request_id)).phase
        is TransactionPhase.VERIFIED
    )
    assert rig.store.load(source.request_id).native_context_sha256 == source.native_context_sha256


@pytest.mark.asyncio
async def test_legacy_context_round_trip_and_seven_action_execution_remain_legacy(tmp_path):
    rig = Rig(tmp_path)
    controller = rig.controller()
    originals = []
    for target in rig.targets:
        status = rig.originals[target.logical_member]
        raw = RawStateCapture(b"legacy-capture" + status, 3, status)
        originals.append(
            NativeOriginal(
                target=target,
                status_sha256=rig.store.install_blob(status),
                capture_sha256=controller._capture(raw, datetime.now(UTC)),
            )
        )
    context = NativeContext(
        kind="attach",
        attach_request_id="legacy_0001",
        plan=_plan("async"),
        schema_sha256=current_protocol_schema_sha256(),
        originals=tuple(originals),
    )
    raw = context.model_dump_json().encode()
    assert b"activation_sequence" not in raw and b"preparation_slave_flow" not in raw
    assert NativeContext.model_validate_json(raw).model_dump_json().encode() == raw
    record = controller._prepare("legacy_0001", "1" * 64, context, rig.endpoints)
    assert len(record.actions) == 7
    with rig.lease.acquire(tuple(t.physical_identity_key for t in rig.targets)) as permit:
        assert (await controller._advance(record, context, rig.endpoints, permit))["accepted"]
    assert len(rig.sent) == 7 and [s[0] for s in rig.sent[:2]] == ["slave", "master"]
    assert rig.sent[5][1] == {"SwitchON": True, "TimerON": True, "Linkage": "sync_slave"}
    assert (await controller.detach(attachment_id="legacy_0001", targets=rig.targets))["accepted"]
    assert rig.states == rig.originals


@pytest.mark.asyncio
async def test_variable_auto_is_observation_only_during_staged_activation(tmp_path):
    rig = Rig(tmp_path)
    rig.read_transform = lambda name, raw, reports: _put(
        raw,
        {
            "AutoMode": "constant" if len(rig.reads) % 2 else "sine",
            "AutoFlow": len(rig.reads) % 101,
            "AutoFreq": len(rig.reads) % 101,
        },
    )
    result = await rig.attach("async")
    assert result["accepted"]
    assert all(
        set(row["fields"]) == {"SwitchON", "TimerON", "Linkage"}
        for row in result["verification"]["devices"]
    )
    await rig.detach()
    assert rig.states == rig.originals


def test_native_read_extension_inherits_all_send_and_preparation_contracts():
    assert SequenceDeviceWriter.send_once is ConnectedDeviceWriter.send_once
    for name in vars(ConnectedDeviceWriter):
        if name.startswith("prepare_"):
            assert getattr(SequenceDeviceWriter, name) is getattr(ConnectedDeviceWriter, name)


@pytest.mark.asyncio
async def test_diagnostic_sink_failure_does_not_replace_reply_failure_or_prevent_restore(
    tmp_path, monkeypatch, caplog
):
    from jebao_flow.local_control import wavemaker_sequence

    rig = Rig(tmp_path)
    rig.fail_ack = 9
    original_read = MemoryTransport.read_raw_state_capture

    def broken_sink(*args):
        raise OSError("injected diagnostic-only sink failure")

    async def read(self, *, accept_reports, **kwargs):
        if len(rig.sent) == 9 and self.address == "slave" and not accept_reports:
            raise TimeoutError("injected direct reply failure")
        return await original_read(self, accept_reports=accept_reports, **kwargs)

    monkeypatch.setattr(wavemaker_sequence, "_persist_frames", broken_sink)
    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    with pytest.raises(LocalControlExecutionError, match="자기 원본 복원 확인"):
        await rig.attach("async")
    failures = [
        json.loads(r.message) for r in caplog.records if r.message.startswith('{"action_index"')
    ]
    assert failures and all(
        f["exception_chain"][0]["type"] == "builtins.TimeoutError" for f in failures
    )
    assert "native_frame_diagnostic_failed" in caplog.text
    assert rig.states == rig.originals
    connected = {e[1] for e in rig.events if e[0] == "connect"}
    assert all(s.closed == 1 for s in connected)


@pytest.mark.asyncio
async def test_initial_pair_failure_is_not_attributed_to_action_zero(tmp_path, monkeypatch, caplog):
    from jebao_flow.local_control import wavemaker_sequence

    rig = Rig(tmp_path)
    original = MemoryTransport.read_raw_state_capture

    async def read(self, *, accept_reports, **kwargs):
        if not rig.sent and kwargs.get("state_frame_observer") is not None:
            raise TimeoutError("injected initial paired read failure")
        return await original(self, accept_reports=accept_reports, **kwargs)

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    with pytest.raises(LocalControlExecutionError, match="자기 원본 복원 확인"):
        await rig.attach("async")
    assert "native_initial_pair_failed" in caplog.text
    assert all(row[2] != "attach_0001" for row in rig.sent)
    assert rig.states == rig.originals
    assert wavemaker_sequence.STAGED_ROLES == "staged_roles_v1"


@pytest.mark.asyncio
async def test_a_bound_slave_answering_only_with_report_frames_still_completes(
    tmp_path, monkeypatch
):
    """From the instant the slave holds a role the device answers with report frames only:
    every preserved frame from 2026-09-18 01:47:10.401 onward is action 4, while the same
    reply-only read at 01:47:08.655 — slave still independent — returned action 3."""

    rig = Rig(tmp_path)
    original = MemoryTransport.read_raw_state_capture
    bound_reads = []

    async def read(self, *, accept_reports, **kwargs):
        role = SCHEMA.decode_status(rig.states["slave"])["Linkage"]
        if self.address == "slave" and role != "independent":
            bound_reads.append(accept_reports)
            if not accept_reports:
                raise TimeoutError("a bound slave emits report frames only")
        return await original(self, accept_reports=accept_reports, **kwargs)

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    assert (await rig.attach("async"))["accepted"]
    assert bound_reads and all(bound_reads)
    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", original)
    assert (await rig.detach())["accepted"] and rig.states == rig.originals


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_a_stored_v2_record_keeps_its_own_action_layout(mode):
    """A journal is append-only history. v2 was one deployed attempt that inserted a slave
    manual re-assert at position 8; hardware rejected the idea and new attachments use v1
    again, but one 10-action v2 record is stored and must still be read from its own layout.
    Recomputing stored records with the current shape refused every Pro operation twice on
    2026-09-20, both times before a single frame reached a device."""

    plan = _plan(mode)
    members = tuple(
        SimpleNamespace(target=SimpleNamespace(logical_member=name))
        for name in ("master", "slave")
    )
    legacy = SimpleNamespace(activation_sequence=STAGED_ROLES, plan=plan, originals=members)
    stored_v2 = SimpleNamespace(activation_sequence=STAGED_ROLES_V2, plan=plan, originals=members)

    plain = 9 if mode == "async" else 8
    # A new plan, with no stored record to read from, is always the plain role order.
    assert staged_layout(legacy).manual is None
    assert staged_layout(legacy).count == plain
    assert staged_layout(legacy).async_role == (8 if mode == "async" else None)

    # The name alone does not identify the shape: v2 was written with both. The count does.
    assert staged_layout(stored_v2, plain).manual is None
    assert staged_layout(stored_v2, plain + 1).manual == 8
    assert staged_layout(stored_v2, plain + 1).count == plain + 1
    assert staged_layout(stored_v2, plain + 1).async_role == (9 if mode == "async" else None)
    with pytest.raises(ValueError):
        staged_layout(stored_v2, plain + 2)

    manual = {"Mode": "constant", "Frequency": 21}
    assert manual not in [values for _n, values in staged_controls(legacy)]
    assert manual in [
        values for _n, values in staged_controls(stored_v2, staged_layout(stored_v2, plain + 1))
    ]


@pytest.mark.asyncio
async def test_a_pending_v1_source_is_still_recognised_as_awaiting_the_operator(tmp_path):
    """The exact production regression: `validate_cycle` re-reads the snapshot of the stalled
    source held by a historical master-cycle record, and `_require_available` walks every
    stored record. When `_awaiting_operator` demanded the v2 action count, that nine-action v1
    snapshot stopped validating and every later Pro operation was refused before any write."""

    rig = Rig(tmp_path)
    controller = rig.controller()
    originals = []
    for target in rig.targets:
        status = rig.originals[target.logical_member]
        raw = RawStateCapture(b"v1-capture" + status, 3, status)
        originals.append(
            NativeOriginal(
                target=target,
                status_sha256=rig.store.install_blob(status),
                capture_sha256=controller._capture(raw, datetime.now(UTC)),
            )
        )
    context = NativeContext(
        kind="attach",
        attach_request_id="legacy_staged_01",
        plan=_plan("async"),
        schema_sha256=current_protocol_schema_sha256(),
        originals=tuple(originals),
        activation_sequence=STAGED_ROLES,
        master_cycle_on_async_failure=True,
    )
    record = controller._prepare("legacy_staged_01", "2" * 64, context, rig.endpoints)
    assert len(record.actions) == 9
    assert staged_layout(context).count == 9 and staged_layout(context).async_role == 8

    for index in range(8):
        record = controller._save_action(record, index, phase=ActionPhase.NO_CHANGE)
    record = controller._save_action(
        record,
        8,
        phase=ActionPhase.UNCERTAIN,
        diagnostic="native_stage_unverified",
        transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
    )
    assert controller._awaiting_operator(record, controller._context(record)) is True
    controller._require_assistance_source(record, controller._context(record))
