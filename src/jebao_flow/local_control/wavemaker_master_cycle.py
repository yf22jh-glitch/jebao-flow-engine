"""Owned master-only fallback; never retry or rewrite the source Async operation.

The app observation in 2026-09-15-async-master-link-app-rearm.md supplies the
procedure, not a firmware model or proof of the original command's delivery.
"""

from __future__ import annotations

import hashlib
import json
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

KIND = "master_link_cycle_v1"


def validate_cycle(owner, record, context):
    if context.master_cycle_on_async_failure is not True or context.assisted_record_sha256 is None:
        raise ValueError("cycle 원본의 명시 옵션과 시도 기록이 필요합니다")
    stalled = LocalControlJournalRecord.model_validate_json(
        owner._journal.load_blob(context.assisted_record_sha256)
    )
    source_context = owner._context(stalled)
    if (
        stalled.request_id != context.attach_request_id
        or stalled.native_context_sha256 != context.source_context_sha256
        or not owner._awaiting_operator(stalled, source_context)
        or len(record.actions) != 2
    ):
        raise ValueError("cycle은 자기 마지막 Async 보류에만 결속됩니다")
    for action, role in zip(record.actions, ("independent", "master"), strict=True):
        if (
            action.target != context.originals[0].target
            or action.function != "linkage_role"
            or json.loads(owner._journal.load_blob(action.expected_readback_sha256))
            != {"Linkage": role}
        ):
            raise ValueError("cycle은 master Linkage 두 명령뿐입니다")


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
    master = endpoints[0]
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
        _, current = await owner._read(master, permit, reports=True)
        baseline = owner._snapshots(context)[master.target.logical_member].baseline_status
        writer = owner._writer_factory(master)
        prepared = []
        for role in ("independent", "master"):
            intent = LocalControlIntent(
                request_id=request_id, target=master.target, function="linkage_role", value=role
            )
            owner._catalog.validate_intent(intent)
            scalar = writer.prepare_scalar(intent, current)
            owned = writer.prepare_native_controls({"Linkage": role}, baseline)
            if scalar.forward_payload != owned.forward_payload:
                raise ValueError("앱 scalar와 소유 cycle payload가 다릅니다")
            prepared.append(replace(owned, function="linkage_role"))
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
    try:
        for index in range(2):
            action = record.actions[index]
            if index == 1:
                record = await _pair(owner, record, cycle_context, endpoints, permit, released=True)
                await owner._sequence_sleep(config.minimum_command_interval_ms / 1000)
            if action.phase in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}:
                continue
            writer = owner._writer_factory(master)
            started = failure_clock_ns()
            try:
                began = datetime.now(UTC)
                capture = await writer.connect_and_capture(accept_reports=True)
                permit.require_permitted()
                prepared = owner._forward_prepared(action, master)
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
                await owner._sequence_sleep(config.readback_delay_ms / 1000)
                began = datetime.now(UTC)
                capture = await writer.capture_readback(permit, accept_reports=True)
                record = owner._evidence(record, index, capture, began, prepared, "after")
                if not verify_prepared_readback(prepared, capture.status_payload):
                    raise LocalControlExecutionError("cycle 명령 반환값 불일치")
                record = owner._save_action(record, index, phase=ActionPhase.VERIFIED)
            finally:
                await writer.close()
        record = await _pair(owner, record, cycle_context, endpoints, permit, released=False)
        record = owner._save_transaction_phase(record, TransactionPhase.VERIFIED)
        return owner._public(record, current=True)
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
        # No reverse rollback to master, no source mutation, no implicit detach on cancellation.
        raise
