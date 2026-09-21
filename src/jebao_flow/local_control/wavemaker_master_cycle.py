"""Owned master-link cycle; never retry or rewrite the source Async operation.

The master release and rearm are the recovery. The released window is also the only point
where the slave accepts an acknowledged control write, so a v2 source repairs its manual
Mode there; see the layout note in wavemaker_sequence.

The app observation in 2026-09-15-async-master-link-app-rearm.md supplies the
procedure, not a firmware model or proof of the original command's delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime

from jebao_flow.config import DeviceControlConfig
from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    extract_current_pro_schedule_image,
    verify_prepared_readback,
)
from jebao_flow.local_control.failure_diagnostics import failure_clock_ns, report_action_failure
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalRecord,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.session import RawStateCapture

KIND = "master_link_cycle_v1"
# This operation stays master-only. Three placements for repairing the slave's manual Mode
# were tried on hardware and all three were rejected by the device:
#   * between the role writes, a bound slave never acknowledges a control write
#     (2026-09-20 03:00:33 send, no ACK, still unapplied at 03:00:40);
#   * in the released window, an ``async_slave`` acknowledges but does not apply it
#     (09:08:26 ack, still ``feed`` at 09:09:33);
#   * stepping the slave out, repairing it and stepping it back does apply
#     (09:30:20.694 ``feed``->``constant``, still ``constant`` at 09:30:22.483 after the
#     slave rebound), but the master taking the link back reverts it 1.5 s later
#     (09:30:24.477 master rearmed, 09:30:26.015 ``feed`` again).
# So while the pair is linked the manual Mode is the device's to set and no write order keeps
# another value there. The pump only stops because that block becomes live when the slave
# loses ``TimerON``, and ``TimerON`` is not forced by the link - that is what the slave timer
# rearm repairs. The 3- and 5-action shapes below are stored records from those attempts.
_LOGGER = logging.getLogger(__name__)
_CLOSED_BY_CYCLE = "native_async_role_verified_by_master_cycle_delivery_unknown"


def cycle_steps(context, endpoints):
    """The master release and rearm; the slave is never written by this operation."""

    master = endpoints[0]
    return (
        (master, "linkage_role", "independent"),
        (master, "linkage_role", "master"),
    )


def validate_cycle(owner, record, context):
    if context.master_cycle_on_async_failure is not True or context.assisted_record_sha256 is None:
        raise ValueError("cycle 원본의 명시 옵션과 시도 기록이 필요합니다")
    stalled = LocalControlJournalRecord.model_validate_json(
        owner._journal.load_blob(context.assisted_record_sha256)
    )
    source_context = owner._context(stalled)
    expected = _CYCLE_SHAPES.get(len(record.actions))
    if (
        stalled.request_id != context.attach_request_id
        or stalled.native_context_sha256 != context.source_context_sha256
        or not owner._awaiting_operator(stalled, source_context)
        or expected is None
    ):
        raise ValueError("cycle은 자기 마지막 Async 보류에만 결속됩니다")
    for action, (index, function, values) in zip(record.actions, expected, strict=True):
        if (
            action.target != context.originals[index].target
            or action.function != function
            or json.loads(owner._journal.load_blob(action.expected_readback_sha256)) != values
        ):
            raise ValueError("cycle은 master Linkage 두 명령과 released 구간 manual 복구뿐입니다")


# Every shape this operation has ever written to the journal. Validation reads a stored
# record from its own shape; only creation uses the current one. Recomputing old records with
# the newest shape made `_require_available` refuse every Pro operation twice on 2026-09-20.
_RELEASE = (0, "linkage_role", {"Linkage": "independent"})
_REARM = (0, "linkage_role", {"Linkage": "master"})
_REPAIR = (1, "manual_mode", {"Mode": "constant"})
_STEP_OUT = (1, "linkage_role", {"Linkage": "independent"})
_STEP_BACK = (1, "linkage_role", {"Linkage": "async_slave"})
_CYCLE_SHAPES = {
    2: (_RELEASE, _REARM),
    3: (_RELEASE, _REPAIR, _REARM),
    5: (_RELEASE, _STEP_OUT, _REPAIR, _STEP_BACK, _REARM),
}
def _expected_cycle_steps(_context):
    return _CYCLE_SHAPES[2]


async def _pair(owner, record, context, endpoints, permit, *, released):
    rows = []
    images = context.plan.compile_images()
    for index, endpoint in enumerate(endpoints):
        # Reply-only while independent; after rearm, retain selected frame provenance.
        capture_id, status = await owner._read(endpoint, permit, reports=not released)
        capture = json.loads(owner._journal.load_blob(capture_id))
        name = endpoint.target.logical_member
        actual = get_product_schema(endpoint.target.product_key).decode_status(status)
        expected = {
            "SwitchON": True,
            "TimerON": True,
            "Linkage": ("independent" if released else "master") if index == 0 else "async_slave",
        }
        fields = {
            k: {"expected": v, "actual": actual[k], "match": actual[k] == v}
            for k, v in expected.items()
        }
        image = extract_current_pro_schedule_image(status)
        rows.append(
            {
                "stage": "released_reply_pair" if released else "rearmed_pair",
                "logical_member": name,
                "capture_sha256": capture_id,
                "action": capture["action"],
                "fields": fields,
                "expected_schedule_sha256": hashlib.sha256(images[name]).hexdigest(),
                "actual_schedule_sha256": hashlib.sha256(image).hexdigest(),
                "schedule_match": image == images[name],
                "complete": image == images[name]
                and all(f["match"] for f in fields.values())
                and (not released or capture["action"] == 3),
            }
        )
        record = owner._save_final(record, rows)
    if not all(r["complete"] for r in rows):
        raise LocalControlExecutionError("master cycle의 역할/전체 시간표 대조 불일치")
    return record


async def cycle_locked(owner, source, context, endpoints, permit):
    from jebao_flow.local_control.wavemaker_lifecycle import (
        NativeContext,
        native_master_cycle_request_id,
    )

    if context.master_cycle_on_async_failure is not True or not owner._awaiting_operator(
        source, context
    ):
        raise ValueError("마지막 Async 보류와 명시 cycle 옵션이 필요합니다")
    request_id = native_master_cycle_request_id(source.request_id)
    record = owner._journal.load(request_id)
    if record is not None:
        cycle_context = owner._context(record)
        if record.phase in {TransactionPhase.VERIFIED, TransactionPhase.ROLLED_BACK}:
            return owner._public(record, current=False)
        if any(
            a.phase not in {ActionPhase.PREPARED, ActionPhase.NO_CHANGE, ActionPhase.VERIFIED}
            for a in record.actions
        ):
            raise LocalControlExecutionError(
                "cycle 미확인 명령 재전송 금지; 자기 원복이 필요합니다"
            )
    owner._qualify(request_id, context.plan, endpoints)
    if record is None:
        cycle_context = NativeContext(
            **{
                **context.model_dump(),
                "kind": KIND,
                "source_context_sha256": source.native_context_sha256,
                "assisted_record_sha256": owner._journal.install_blob(
                    source.model_dump_json().encode()
                ),
            }
        )
        snapshots = owner._snapshots(context)
        current = {}
        for endpoint in endpoints:
            _, status = await owner._read(endpoint, permit, reports=True)
            current[endpoint.target.logical_member] = status
        prepared = []
        writers = {e.target.logical_member: owner._writer_factory(e) for e in endpoints}
        try:
            for endpoint, function, value in cycle_steps(cycle_context, endpoints):
                name = endpoint.target.logical_member
                intent = LocalControlIntent(
                    request_id=request_id, target=endpoint.target, function=function, value=value
                )
                owner._catalog.validate_intent(intent)
                scalar = writers[name].prepare_scalar(intent, current[name])
                owned = writers[name].prepare_native_controls(
                    json.loads(scalar.expected_readback), snapshots[name].baseline_status
                )
                if scalar.forward_payload != owned.forward_payload:
                    raise ValueError("앱 scalar와 소유 cycle payload가 다릅니다")
                prepared.append(replace(owned, function=function))
        finally:
            await asyncio.gather(*(w.close() for w in writers.values()))
        # Both owned payloads + source snapshot are durable before the first cycle send.
        record = owner._create_record(
            request_id=request_id,
            intent_sha256=hashlib.sha256(cycle_context.model_dump_json().encode()).hexdigest(),
            prepared=tuple(prepared),
            native_context_sha256=owner._journal.install_blob(
                cycle_context.model_dump_json().encode()
            ),
        )
    owner._context(record)
    config = DeviceControlConfig()
    index = 0
    started = failure_clock_ns()
    by_name = {e.target.logical_member: e for e in endpoints}
    try:
        for index in range(len(record.actions)):
            action = record.actions[index]
            if index == 1:
                record = await _pair(owner, record, cycle_context, endpoints, permit, released=True)
                await owner._sequence_sleep(config.minimum_command_interval_ms / 1000)
            if action.phase in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}:
                continue
            endpoint = by_name[action.target.logical_member]
            writer = owner._writer_factory(endpoint)
            started = failure_clock_ns()
            try:
                began = datetime.now(UTC)
                capture = await writer.connect_and_capture(accept_reports=True)
                permit.require_permitted()
                prepared = owner._forward_prepared(action, endpoint)
                record = owner._evidence(record, index, capture, began, prepared, "before")
                if verify_prepared_readback(prepared, capture.status_payload):
                    record = owner._save_action(record, index, phase=ActionPhase.NO_CHANGE)
                    continue
                if action.phase is not ActionPhase.PREPARED:
                    raise LocalControlExecutionError("cycle 명령 재전송 금지")
                record = owner._save_action(
                    record,
                    index,
                    phase=ActionPhase.FORWARD_ATTEMPTING,
                    transaction_phase=TransactionPhase.EXECUTING,
                )
                ack = await writer.send_once(prepared.forward_payload, permit)
                record = owner._save_action(
                    record,
                    index,
                    phase=ActionPhase.FORWARD_ACKNOWLEDGED,
                    acknowledgement_sha256=owner._journal.install_blob(ack),
                )
                for attempt in range(config.readback_attempts):
                    await owner._sequence_sleep(config.readback_delay_ms / 1000)
                    began = datetime.now(UTC)
                    capture = await writer.capture_readback(permit, accept_reports=True)
                    record = owner._evidence(record, index, capture, began, prepared, "after")
                    if verify_prepared_readback(prepared, capture.status_payload):
                        break
                    if attempt == config.readback_attempts - 1:
                        raise LocalControlExecutionError("cycle 명령 반환값 불일치")
                record = owner._save_action(record, index, phase=ActionPhase.VERIFIED)
            finally:
                await writer.close()
        record = await _pair(owner, record, cycle_context, endpoints, permit, released=False)
        record = owner._save_transaction_phase(record, TransactionPhase.VERIFIED)
    except BaseException as error:
        record = owner._required(request_id)
        phase = record.actions[index].phase
        if phase in {ActionPhase.FORWARD_ATTEMPTING, ActionPhase.FORWARD_ACKNOWLEDGED}:
            phase = ActionPhase.UNCERTAIN
        record = owner._save_action(
            record,
            index,
            phase=phase,
            diagnostic="master_cycle_unverified",
            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
        )
        report_action_failure(
            record, index, error, started_ns=started, failed_ns=failure_clock_ns()
        )
        # No reverse rollback to master, no implicit detach on cancellation. The source is
        # closed out only after this cycle is durably verified, never from inside this handler.
        raise
    # Outside the handler on purpose: a verified cycle is never downgraded by the close-out.
    await _close_source_async_role(owner, source, endpoints, permit, record)
    return owner._public(record, current=True)


async def _close_source_async_role(owner, source, endpoints, permit, cycle):
    """Write this cycle's own explicit slave reply back onto the stalled Async role action.

    The verified cycle already holds exactly the evidence ``_reconcile_async_reply`` asked for
    and could not get at the time: a fresh ``action 3`` reply showing the slave holding
    ``async_slave`` with its power, timer and byte-exact schedule image, captured while the
    master was released. Leaving that proof only in the cycle record is what pinned the source
    at ``recovery_required``; a pending source owns the physical pump, so every ordinary
    command on it — including the ``manual_mode`` write that clears a ``feed`` fallback — was
    refused for the 22 hours after 2026-09-18 01:47. Nothing is sent here, the absent ACK is
    preserved, and a source that cannot be closed is left exactly as it is today.
    """

    from jebao_flow.local_control.wavemaker_sequence import staged_layout

    try:
        record = owner._required(source.request_id)
        context = owner._context(record)
        if not owner._awaiting_operator(record, context):
            return
        role = staged_layout(context).async_role
        slave = endpoints[1]
        action = record.actions[role]
        if action.target != slave.target or action.phase is ActionPhase.VERIFIED:
            return
        row = _released_slave_reply(owner, cycle, slave.target.logical_member)
        if row is None:
            return
        capture_id, began = row
        capture = json.loads(owner._journal.load_blob(capture_id))
        reply = RawStateCapture(
            owner._journal.load_blob(capture["wire_sha256"]),
            capture["action"],
            owner._journal.load_blob(capture["status_sha256"]),
        )
        prepared = owner._forward_prepared(action, slave)
        record = owner._evidence(
            record, role, reply, began, prepared, "master_cycle_released_reply"
        )
        if reply.action != 3 or not verify_prepared_readback(prepared, reply.status_payload):
            return
        record = owner._save_action(
            record,
            role,
            phase=ActionPhase.VERIFIED,
            transaction_phase=TransactionPhase.EXECUTING,
            diagnostic=_CLOSED_BY_CYCLE,
        )
        await owner._finish(record, context, endpoints, permit)
    except Exception as error:
        # The cycle itself stays verified. An unclosed source is today's behaviour, not a
        # reason to fail the recovery that already put the pair back together.
        _LOGGER.error("native_source_closeout_failed type=%s", type(error).__name__)


def _released_slave_reply(owner, cycle, name):
    """Latest complete ``released_reply_pair`` row for the slave, newest chain entry first."""

    digest = cycle.native_final_sha256
    while digest:
        blob = json.loads(owner._journal.load_blob(digest))
        for row in reversed(blob["devices"]):
            if (
                row.get("stage") == "released_reply_pair"
                and row.get("logical_member") == name
                and row.get("action") == 3
                and row.get("complete") is True
            ):
                capture = json.loads(owner._journal.load_blob(row["capture_sha256"]))
                return row["capture_sha256"], datetime.fromisoformat(capture["started_at"])
        digest = blob["previous"]
    return None
