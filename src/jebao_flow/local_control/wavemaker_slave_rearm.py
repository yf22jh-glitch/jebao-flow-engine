"""Source-owned Async slave timer rearm without a schedule-image write.

The operation exists only for an explicitly pending two-device Pro Async attachment.  It
captures the current pair, proves that both installed schedule images still match that
attachment, journals every scalar frame, then sends the app-shaped timer transition followed
by a latent manual fallback, power restore, and direct Async role restore.

No schedule payload is constructed by this module.  A failed child is never replayed under a
new id; the owning attachment's exact native restore remains the recovery authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    build_control_payload,
    extract_current_pro_schedule_image,
)
from jebao_flow.local_control.journal import LocalControlJournalRecord, TransactionPhase
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES

KIND = "slave_timer_rearm_v1"
_MIN_FLOW = 30
_MAX_FLOW = 45
_STEPS = (
    ("schedule_enable", True),
    ("manual_mode", "constant"),
    ("power", True),
    ("linkage_role", "async_slave"),
)


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _require_bounded_plan(context) -> dict[str, bytes]:
    images = context.plan.compile_images()
    for member, image in images.items():
        for index in range(48):
            slot = image[index * 9 : (index + 1) * 9]
            if slot == b"\xee" * 9:
                continue
            mode = LOCAL_WAVEMAKER_PRO_MODES[slot[4]]
            flow = slot[5]
            if mode in {"stopped", "feed"} and flow == 0:
                continue
            if not _MIN_FLOW <= flow <= _MAX_FLOW:
                raise LocalControlExecutionError(
                    f"Async 재진입 시간표 {member} 슬롯 {index} Flow {flow}가 "
                    f"{_MIN_FLOW}..{_MAX_FLOW} 밖입니다"
                )
    return images


def _require_pair_state(context, statuses: dict[str, bytes]) -> None:
    images = _require_bounded_plan(context)
    schema = get_product_schema(context.originals[0].target.product_key)
    master_name = context.originals[0].target.logical_member
    slave_name = context.originals[1].target.logical_member
    master = schema.decode_status(statuses[master_name])
    slave = schema.decode_status(statuses[slave_name])
    if {
        "SwitchON": master["SwitchON"],
        "TimerON": master["TimerON"],
        "Linkage": master["Linkage"],
    } != {"SwitchON": True, "TimerON": True, "Linkage": "master"}:
        raise LocalControlExecutionError("Async 재진입 전 master ON·TimerON·master 역할이 아닙니다")
    if {
        "SwitchON": slave["SwitchON"],
        "TimerON": slave["TimerON"],
        "Linkage": slave["Linkage"],
    } != {"SwitchON": True, "TimerON": False, "Linkage": "async_slave"}:
        raise LocalControlExecutionError(
            "Async 재진입은 ON·TimerOFF·async_slave인 하단 장비만 대상으로 합니다"
        )
    for name in (master_name, slave_name):
        if extract_current_pro_schedule_image(statuses[name]) != images[name]:
            raise LocalControlExecutionError(
                "Async 재진입 전 현재 시간표가 소유 계획과 다릅니다; image는 쓰지 않았습니다"
            )


def _expected_fields(function: str) -> set[str]:
    return {
        "schedule_enable": {"TimerON", "SwitchON", "Linkage"},
        "manual_mode": {"Mode"},
        "power": {"SwitchON"},
        "linkage_role": {"Linkage"},
    }[function]


def validate_slave_rearm(owner, record, context) -> None:
    """Validate immutable owner evidence and all four app-shaped scalar frames."""

    if (
        context.master_cycle_on_async_failure is not True
        or context.assisted_record_sha256 is None
        or len(context.originals) != 2
        or len(record.actions) != len(_STEPS)
    ):
        raise ValueError("Async slave 재진입의 소유 근거가 불완전합니다")
    source_snapshot = LocalControlJournalRecord.model_validate_json(
        owner._journal.load_blob(context.assisted_record_sha256)
    )
    source = owner._required(context.attach_request_id)
    source_context = owner._context(source)
    if (
        source_snapshot.request_id != source.request_id
        or source_snapshot.native_context_sha256 != source.native_context_sha256
        or source_context.kind != "attach"
        or source_context.plan.mode != "async"
        or not owner._awaiting_operator(source, source_context)
    ):
        raise ValueError("Async slave 재진입은 자기 보류 source에만 결속됩니다")

    statuses: dict[str, bytes] = {}
    for index, original in enumerate(context.originals):
        capture = json.loads(owner._journal.load_blob(original.capture_sha256))
        expected_action = 3 if index == 0 else 4
        if (
            capture["action"] != expected_action
            or capture["status_sha256"] != original.status_sha256
        ):
            raise ValueError("Async slave 재진입 원본은 fresh pair capture여야 합니다")
        owner._journal.load_blob(capture["wire_sha256"])
        statuses[original.target.logical_member] = owner._journal.load_blob(original.status_sha256)
    _require_pair_state(context, statuses)

    slave = context.originals[1]
    schema = get_product_schema(slave.target.product_key)
    baseline_values = schema.decode_status(statuses[slave.target.logical_member])
    expected_by_function = {
        "schedule_enable": {
            "TimerON": True,
            "SwitchON": False,
            "Linkage": "independent",
        },
        "manual_mode": {"Mode": "constant"},
        "power": {"SwitchON": True},
        "linkage_role": {"Linkage": "async_slave"},
    }
    for action, (function, _value) in zip(record.actions, _STEPS, strict=True):
        expected = expected_by_function[function]
        baseline = {name: baseline_values[name] for name in expected}
        if (
            action.target != slave.target
            or action.function != function
            or action.verification_kind != "scalar_fields"
            or action.baseline_status_sha256 != slave.status_sha256
            or action.restore_on_recovery is not True
            or owner._journal.load_blob(action.forward_payload_sha256)
            != build_control_payload(schema, expected)
            or owner._journal.load_blob(action.rollback_payload_sha256)
            != build_control_payload(schema, baseline)
            or owner._journal.load_blob(action.expected_readback_sha256) != _json(expected)
            or owner._journal.load_blob(action.baseline_readback_sha256) != _json(baseline)
        ):
            raise ValueError("Async slave 재진입 action 결속이 다릅니다")


async def rearm_slave_locked(owner, source, source_context, endpoints, permit):
    """Create or resume the one source-owned, image-free slave rearm child."""

    from jebao_flow.local_control.wavemaker_lifecycle import (
        NativeContext,
        NativeOriginal,
        native_slave_timer_rearm_request_id,
    )

    if (
        source_context.kind != "attach"
        or source_context.plan.mode != "async"
        or source_context.master_cycle_on_async_failure is not True
        or not owner._awaiting_operator(source, source_context)
    ):
        raise ValueError("Async slave 재진입에는 보류된 자기 Async source가 필요합니다")
    request_id = native_slave_timer_rearm_request_id(source.request_id)
    record = owner._journal.load(request_id)
    if record is not None:
        child_context = owner._context(record)
        if record.phase in {TransactionPhase.VERIFIED, TransactionPhase.ROLLED_BACK}:
            return owner._public(record, current=False)
    else:
        owner._qualify(request_id, source_context.plan, endpoints)
        originals = []
        statuses: dict[str, bytes] = {}
        for index, endpoint in enumerate(endpoints):
            capture_sha256, status = await owner._read(
                endpoint,
                permit,
                reports=index != 0,
            )
            statuses[endpoint.target.logical_member] = status
            originals.append(
                NativeOriginal(
                    target=endpoint.target,
                    status_sha256=owner._journal.install_blob(status),
                    capture_sha256=capture_sha256,
                )
            )
        child_context = NativeContext(
            **{
                **source_context.model_dump(),
                "kind": KIND,
                "source_context_sha256": source.native_context_sha256,
                "originals": tuple(originals),
                "assisted_record_sha256": owner._journal.install_blob(
                    source.model_dump_json().encode()
                ),
                "await_operator_observation": None,
            }
        )
        _require_pair_state(child_context, statuses)
        slave = endpoints[1]
        slave_status = statuses[slave.target.logical_member]
        writer = owner._writer_factory(slave)
        prepared = []
        try:
            for function, value in _STEPS:
                intent = LocalControlIntent(
                    request_id=request_id,
                    target=slave.target,
                    function=function,
                    value=value,
                )
                owner._catalog.validate_intent(intent)
                scalar = writer.prepare_scalar(intent, slave_status)
                expected = json.loads(scalar.expected_readback)
                if set(expected) != _expected_fields(function):
                    raise ValueError("Async slave 재진입 scalar 필드 shape가 다릅니다")
                owned = writer.prepare_native_controls(expected, slave_status)
                if (
                    scalar.forward_payload != owned.forward_payload
                    or scalar.rollback_payload != owned.rollback_payload
                    or scalar.baseline_status != owned.baseline_status
                    or scalar.expected_readback != owned.expected_readback
                    or scalar.baseline_readback != owned.baseline_readback
                ):
                    raise ValueError("앱 scalar와 Async slave 재진입 payload가 다릅니다")
                # Every frame is prepared against the initial slave raw, so the power and
                # role frames restore values the device already held before frame one ran:
                # their ``no_change`` is True at preparation time.  ``_advance`` decides what
                # to send from a fresh capture taken immediately before each frame, not from
                # this flag, which is why both still reach the device.  Routing this child
                # through the ordinary coordinator instead would skip them and leave the pump
                # switched off and detached from its master.
                prepared.append(replace(scalar, function=function))
        finally:
            await writer.close()
        record = owner._create_record(
            request_id=request_id,
            intent_sha256=hashlib.sha256(child_context.model_dump_json().encode()).hexdigest(),
            prepared=tuple(prepared),
            native_context_sha256=owner._journal.install_blob(
                child_context.model_dump_json().encode()
            ),
        )

    owner._context(record)
    return await owner._advance(record, child_context, endpoints, permit)
