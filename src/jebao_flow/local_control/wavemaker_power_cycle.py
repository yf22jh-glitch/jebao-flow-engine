"""Durable master-only power cycle owned by one pending native attachment.

This operation does not clear, retry, or reinterpret the source Async delivery.  It
captures the currently attached pair as a child baseline, writes only the master's
app-shaped power field, and relies on the native lifecycle for exact role/schedule
comparison after power is restored.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import build_control_payload
from jebao_flow.local_control.journal import LocalControlJournalRecord, TransactionPhase
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.protocol.profiles import get_product_schema

KIND = "master_power_cycle_v1"
POWER_OFF_SECONDS = 10


def _require_verified_link_cycle(owner, source_context):
    from jebao_flow.local_control.wavemaker_lifecycle import native_master_cycle_request_id

    record = owner._journal.load(native_master_cycle_request_id(source_context.attach_request_id))
    if record is None or record.phase is not TransactionPhase.VERIFIED:
        raise ValueError("master 전원 cycle에는 완료된 master 역할 cycle이 필요합니다")
    if owner._context(record).kind != "master_link_cycle_v1":
        raise ValueError("master 전원 cycle의 역할 근거가 다릅니다")
    return record


def validate_power_cycle(owner, record, context):
    """Validate the child's immutable owner evidence and its exact two frames."""

    if (
        context.master_cycle_on_async_failure is not True
        or context.assisted_record_sha256 is None
        or context.power_cycle_basis_sha256 is None
        or context.power_off_seconds != POWER_OFF_SECONDS
        or len(record.actions) != 2
    ):
        raise ValueError("master 전원 cycle의 소유 근거가 불완전합니다")

    source_snapshot = LocalControlJournalRecord.model_validate_json(
        owner._journal.load_blob(context.assisted_record_sha256)
    )
    source_context = owner._context(source_snapshot)
    basis = LocalControlJournalRecord.model_validate_json(
        owner._journal.load_blob(context.power_cycle_basis_sha256)
    )
    from jebao_flow.local_control.wavemaker_lifecycle import native_master_cycle_request_id

    if (
        source_snapshot.request_id != context.attach_request_id
        or source_snapshot.native_context_sha256 != context.source_context_sha256
        or not owner._awaiting_operator(source_snapshot, source_context)
        or basis.request_id != native_master_cycle_request_id(context.attach_request_id)
        or basis.phase is not TransactionPhase.VERIFIED
        or owner._context(basis).kind != "master_link_cycle_v1"
    ):
        raise ValueError("master 전원 cycle은 자기 Async 보류와 검증된 역할 cycle에만 결속됩니다")

    master = context.originals[0]
    for original in context.originals:
        capture = json.loads(owner._journal.load_blob(original.capture_sha256))
        if capture["action"] != 3 or capture["status_sha256"] != original.status_sha256:
            raise ValueError("master 전원 cycle 원본은 fresh reply capture여야 합니다")
        owner._journal.load_blob(capture["wire_sha256"])
        get_product_schema(original.target.product_key).decode_status(
            owner._journal.load_blob(original.status_sha256)
        )
    status = owner._journal.load_blob(master.status_sha256)
    schema = get_product_schema(master.target.product_key)
    if schema.decode_status(status)["SwitchON"] is not True:
        raise ValueError("master 전원 cycle의 포착 원본은 ON이어야 합니다")
    baseline = {"SwitchON": True}
    baseline_payload = build_control_payload(schema, baseline)
    baseline_readback = json.dumps(
        baseline, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    for action, enabled in zip(record.actions, (False, True), strict=True):
        expected = {"SwitchON": enabled}
        expected_readback = json.dumps(
            expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        if (
            action.target != master.target
            or action.function != "power"
            or action.verification_kind != "scalar_fields"
            or owner._journal.load_blob(action.forward_payload_sha256)
            != build_control_payload(schema, expected)
            or owner._journal.load_blob(action.rollback_payload_sha256) != baseline_payload
            or owner._journal.load_blob(action.expected_readback_sha256) != expected_readback
            or owner._journal.load_blob(action.baseline_readback_sha256) != baseline_readback
            or action.restore_on_recovery is not enabled
        ):
            raise ValueError("master 전원 cycle은 OFF/ON 두 소유 프레임뿐입니다")


async def cycle_power_locked(owner, source, source_context, endpoints, permit):
    """Create or resume one source-owned OFF -> 10 s -> ON master cycle."""

    from jebao_flow.local_control.wavemaker_lifecycle import (
        NativeContext,
        NativeOriginal,
        native_master_power_cycle_request_id,
    )

    if source_context.master_cycle_on_async_failure is not True or not owner._awaiting_operator(
        source, source_context
    ):
        raise ValueError("마지막 Async 보류와 명시 cycle 옵션이 필요합니다")
    basis = _require_verified_link_cycle(owner, source_context)
    request_id = native_master_power_cycle_request_id(source.request_id)
    record = owner._journal.load(request_id)
    if record is not None:
        cycle_context = owner._context(record)
        if record.phase in {TransactionPhase.VERIFIED, TransactionPhase.ROLLED_BACK}:
            return owner._public(record, current=False)

    owner._qualify(request_id, source_context.plan, endpoints)
    master = endpoints[0]
    if record is None:
        originals = []
        statuses = {}
        for endpoint in endpoints:
            capture_sha256, status = await owner._read(endpoint, permit, reports=False)
            statuses[endpoint.target.logical_member] = status
            originals.append(
                NativeOriginal(
                    target=endpoint.target,
                    status_sha256=owner._journal.install_blob(status),
                    capture_sha256=capture_sha256,
                )
            )
        master_status = statuses[master.target.logical_member]
        if (
            get_product_schema(master.target.product_key).decode_status(master_status)["SwitchON"]
            is not True
        ):
            raise LocalControlExecutionError("master가 이미 OFF여서 자동 재기동하지 않습니다")

        cycle_context = NativeContext(
            **{
                **source_context.model_dump(),
                "kind": KIND,
                "source_context_sha256": source.native_context_sha256,
                "originals": tuple(originals),
                "assisted_record_sha256": owner._journal.install_blob(
                    source.model_dump_json().encode()
                ),
                "power_cycle_basis_sha256": owner._journal.install_blob(
                    basis.model_dump_json().encode()
                ),
                "power_off_seconds": POWER_OFF_SECONDS,
                "await_operator_observation": None,
            }
        )
        writer = owner._writer_factory(master)
        prepared = []
        for enabled in (False, True):
            intent = LocalControlIntent(
                request_id=request_id,
                target=master.target,
                function="power",
                value=enabled,
            )
            owner._catalog.validate_intent(intent)
            scalar = writer.prepare_scalar(intent, master_status)
            owned = writer.prepare_native_controls({"SwitchON": enabled}, master_status)
            if (
                scalar.forward_payload != owned.forward_payload
                or scalar.rollback_payload != owned.rollback_payload
                or scalar.baseline_status != owned.baseline_status
                or scalar.expected_readback != owned.expected_readback
                or scalar.baseline_readback != owned.baseline_readback
            ):
                raise ValueError("앱 power와 native 소유 payload가 다릅니다")
            prepared.append(replace(scalar, function="power"))
        record = owner._create_record(
            request_id=request_id,
            intent_sha256=hashlib.sha256(cycle_context.model_dump_json().encode()).hexdigest(),
            prepared=tuple(prepared),
            native_context_sha256=owner._journal.install_blob(
                cycle_context.model_dump_json().encode()
            ),
        )

    owner._context(record)
    return await owner._advance(record, cycle_context, endpoints, permit)
