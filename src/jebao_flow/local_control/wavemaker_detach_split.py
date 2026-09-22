"""Source-owned continuation for a slave compound detach that did not apply.

The hardware observation recorded in
``docs/runs/2026-09-22-flow-carrier-deploy-detach-block.md`` left two immutable UNKNOWN
payloads: the original atomic slave detach and the first assistance timer-off compound.  Neither
may be resent.  The master role-only release did apply, however, and earlier preserved hardware
evidence showed slave writes returning after that release had settled.  This continuation uses
two different app-shaped frames: slave role-only independent, then the independent timer-off
shape after five seconds.  The existing assistance and exact detach own everything after that.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from jebao_flow.local_control.device_writer import (
    build_control_payload,
    extract_current_pro_schedule_image,
    get_product_schema,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalRecord,
    TransactionPhase,
    native_detach_request_id,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.wavemaker_sequence import planned_images

KIND = "detach_assist_split_v1"


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _planned_independent_status(schema, status: bytes) -> bytes:
    """Change only the Linkage bits in a planning copy; never manufacture a device readback."""

    attribute = schema.by_name("Linkage")
    position = attribute.position
    encoded = build_control_payload(schema, {"Linkage": "independent"})[
        1 + schema.attribute_flags_size :
    ]
    planned = bytearray(status)
    if position.unit.value == "byte":
        start = position.byte_offset
        planned[start : start + position.length] = encoded[start : start + position.length]
    else:
        for index in range(position.length):
            local_bit = position.bit_offset + index
            target_byte = position.byte_offset + local_bit // 8
            target_bit = local_bit % 8
            mask = 1 << target_bit
            planned[target_byte] = (planned[target_byte] & ~mask) | (encoded[target_byte] & mask)
    before = schema.decode_status(status)
    after = schema.decode_status(bytes(planned))
    if after["Linkage"] != "independent" or any(
        before[name] != value for name, value in after.items() if name != "Linkage"
    ):
        raise ValueError("planned independent status changed an unowned field")
    return bytes(planned)


def _require_stalled_records(owner, child, support, source_context) -> None:
    owner._require_stalled_first_slave(child, source_context)
    if (
        support.phase is not TransactionPhase.RECOVERY_REQUIRED
        or len(support.actions) != 5
        or [action.phase for action in support.actions]
        != [
            ActionPhase.VERIFIED,
            ActionPhase.UNCERTAIN,
            ActionPhase.PREPARED,
            ActionPhase.PREPARED,
            ActionPhase.PREPARED,
        ]
        or [action.function for action in support.actions]
        != [
            "linkage_role",
            "schedule_enable",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
        ]
    ):
        raise ValueError("분리 지원은 master 해제만 확인된 stalled assist 전용입니다")
    master, slave = source_context.originals
    if support.actions[0].target != master.target or any(
        action.target != slave.target for action in support.actions[1:]
    ):
        raise ValueError("stalled assist 대상이 source pair와 다릅니다")
    if json.loads(owner._journal.load_blob(support.actions[0].expected_readback_sha256)) != {
        "Linkage": "independent"
    } or json.loads(owner._journal.load_blob(support.actions[1].expected_readback_sha256)) != {
        "Linkage": "independent",
        "SwitchON": True,
        "TimerON": False,
    }:
        raise ValueError("stalled assist payload shape가 분리 가능한 계약이 아닙니다")


def _require_pre_split_state(source_context, statuses: dict[str, bytes]) -> None:
    images = planned_images(source_context)
    schema = get_product_schema(source_context.originals[0].target.product_key)
    master_name = source_context.originals[0].target.logical_member
    slave_name = source_context.originals[1].target.logical_member
    master = schema.decode_status(statuses[master_name])
    slave = schema.decode_status(statuses[slave_name])
    if master["Linkage"] != "independent":
        raise ValueError("분리 지원 전 master role-only 해제가 확인되지 않았습니다")
    if {
        "SwitchON": slave["SwitchON"],
        "TimerON": slave["TimerON"],
        "Linkage": slave["Linkage"],
    } != {"SwitchON": True, "TimerON": True, "Linkage": "async_slave"}:
        raise ValueError("분리 지원은 ON·TimerON·async_slave 하단 전용입니다")
    for name, status in statuses.items():
        if extract_current_pro_schedule_image(status) != images[name]:
            raise ValueError("분리 지원 전 source 시간표가 바뀌었습니다")


def validate_detach_split(owner, record, context) -> None:
    """Rebuild both distinct frames and bind them to the preserved stalled assistance."""

    if context.assisted_record_sha256 is None or len(record.actions) != 2:
        raise ValueError("분리 지원 소유 근거가 불완전합니다")
    support = LocalControlJournalRecord.model_validate_json(
        owner._journal.load_blob(context.assisted_record_sha256)
    )
    source = owner._required(context.attach_request_id)
    source_context = owner._context(source)
    child = owner._required(native_detach_request_id(context.attach_request_id))
    _require_stalled_records(owner, child, support, source_context)

    statuses: dict[str, bytes] = {}
    for index, original in enumerate(context.originals):
        capture = json.loads(owner._journal.load_blob(original.capture_sha256))
        if (
            capture["action"] != (3 if index == 0 else 4)
            or capture["status_sha256"] != original.status_sha256
        ):
            raise ValueError("분리 지원 원본은 fresh pair capture여야 합니다")
        owner._journal.load_blob(capture["wire_sha256"])
        statuses[original.target.logical_member] = owner._journal.load_blob(
            original.status_sha256
        )
    _require_pre_split_state(source_context, statuses)

    slave = context.originals[1]
    status = statuses[slave.target.logical_member]
    schema = get_product_schema(slave.target.product_key)
    values = schema.decode_status(status)
    expected = (
        ({"Linkage": "independent"}, {"Linkage": values["Linkage"]}),
        (
            {"SwitchON": True, "TimerON": False},
            {"SwitchON": values["SwitchON"], "TimerON": values["TimerON"]},
        ),
    )
    for action, function, (forward, rollback) in zip(
        record.actions,
        ("linkage_role", "schedule_enable"),
        expected,
        strict=True,
    ):
        if (
            action.target != slave.target
            or action.function != function
            or action.verification_kind != "scalar_fields"
            or action.baseline_status_sha256 != slave.status_sha256
            or action.restore_on_recovery is not True
            or owner._journal.load_blob(action.forward_payload_sha256)
            != build_control_payload(schema, forward)
            or owner._journal.load_blob(action.rollback_payload_sha256)
            != build_control_payload(schema, rollback)
            or owner._journal.load_blob(action.expected_readback_sha256) != _json(forward)
            or owner._journal.load_blob(action.baseline_readback_sha256) != _json(rollback)
        ):
            raise ValueError("분리 지원 action 결속이 다릅니다")

    attempted = {
        owner._journal.load_blob(child.actions[0].forward_payload_sha256),
        owner._journal.load_blob(support.actions[1].forward_payload_sha256),
    }
    if any(
        owner._journal.load_blob(action.forward_payload_sha256) in attempted
        for action in record.actions
    ):
        raise ValueError("분리 지원은 기존 UNKNOWN payload를 재사용할 수 없습니다")


async def split_detach_locked(
    owner,
    source,
    source_context,
    child,
    support,
    endpoints,
    permit,
):
    """Create/resume the two-frame continuation while the caller owns the pair lease."""

    from jebao_flow.local_control.wavemaker_lifecycle import (
        NativeContext,
        NativeOriginal,
        native_split_assistance_request_id,
    )

    if source_context.kind != "attach" or source_context.plan.mode != "async":
        raise ValueError("분리 지원은 자기 Async source 전용입니다")
    _require_stalled_records(owner, child, support, source_context)
    request_id = native_split_assistance_request_id(source.request_id)
    record = owner._journal.load(request_id)
    if record is not None:
        split_context = owner._context(record)
        if record.phase is TransactionPhase.VERIFIED:
            return owner._public(record, current=False)
    else:
        originals = []
        statuses: dict[str, bytes] = {}
        for index, endpoint in enumerate(endpoints):
            capture_sha256, status = await owner._read(endpoint, permit, reports=index != 0)
            statuses[endpoint.target.logical_member] = status
            originals.append(
                NativeOriginal(
                    target=endpoint.target,
                    status_sha256=owner._journal.install_blob(status),
                    capture_sha256=capture_sha256,
                )
            )
        _require_pre_split_state(source_context, statuses)
        split_context = NativeContext(
            **{
                **source_context.model_dump(),
                "kind": KIND,
                "source_context_sha256": source.native_context_sha256,
                "originals": tuple(originals),
                "assisted_record_sha256": owner._journal.install_blob(
                    support.model_dump_json().encode()
                ),
                "await_operator_observation": None,
            }
        )
        slave = endpoints[1]
        status = statuses[slave.target.logical_member]
        schema = get_product_schema(slave.target.product_key)
        writer = owner._writer_factory(slave)
        try:
            role_intent = LocalControlIntent(
                request_id=request_id,
                target=slave.target,
                function="linkage_role",
                value="independent",
            )
            owner._catalog.validate_intent(role_intent)
            role_scalar = writer.prepare_scalar(role_intent, status)
            role_owned = writer.prepare_native_controls({"Linkage": "independent"}, status)
            if (
                role_scalar.forward_payload != role_owned.forward_payload
                or role_scalar.rollback_payload != role_owned.rollback_payload
                or role_scalar.expected_readback != role_owned.expected_readback
                or role_scalar.baseline_readback != role_owned.baseline_readback
            ):
                raise ValueError("role-only app payload와 분리 지원 payload가 다릅니다")

            planned = _planned_independent_status(schema, status)
            timer_intent = LocalControlIntent(
                request_id=request_id,
                target=slave.target,
                function="schedule_enable",
                value=False,
            )
            owner._catalog.validate_intent(timer_intent)
            timer_scalar = writer.prepare_scalar(timer_intent, planned)
            timer_expected = {"SwitchON": True, "TimerON": False}
            if json.loads(timer_scalar.expected_readback) != timer_expected:
                raise ValueError("independent timer-off app payload shape가 다릅니다")
            timer_owned = writer.prepare_native_controls(timer_expected, status)
            if (
                timer_scalar.forward_payload != timer_owned.forward_payload
                or timer_scalar.rollback_payload != timer_owned.rollback_payload
                or timer_scalar.expected_readback != timer_owned.expected_readback
                or timer_scalar.baseline_readback != timer_owned.baseline_readback
            ):
                raise ValueError("timer-off app payload와 분리 지원 payload가 다릅니다")
            prepared = (
                replace(role_owned, function="linkage_role"),
                replace(timer_owned, function="schedule_enable"),
            )
        finally:
            await writer.close()
        attempted = {
            owner._journal.load_blob(child.actions[0].forward_payload_sha256),
            owner._journal.load_blob(support.actions[1].forward_payload_sha256),
        }
        if any(item.forward_payload in attempted for item in prepared):
            raise ValueError("분리 지원은 기존 UNKNOWN payload를 재전송하지 않습니다")
        record = owner._create_record(
            request_id=request_id,
            intent_sha256=hashlib.sha256(split_context.model_dump_json().encode()).hexdigest(),
            prepared=prepared,
            native_context_sha256=owner._journal.install_blob(
                split_context.model_dump_json().encode()
            ),
        )

    owner._context(record)
    return await owner._advance(record, split_context, endpoints, permit)
