"""Normal activation ordering observed in the native-scenario-async run.

No frozen harness is executed. The existing writer owns every payload and send boundary.
Only the native read surface separates authentication from capture, allowing a pair barrier.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime

from jebao_flow.config import DeviceControlConfig
from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    extract_current_pro_schedule_image,
    verify_prepared_readback,
)
from jebao_flow.local_control.failure_diagnostics import failure_clock_ns, report_action_failure
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase
from jebao_flow.protocol.errors import ProtocolError
from jebao_flow.protocol.profiles import get_product_schema

STAGED_ROLES = "staged_roles_v1"
_CONTROL = DeviceControlConfig()
# Existing schedule_flow_experiment._ROLE_PREFLIGHT_SETTLE_SECONDS; no harness import.
_ROLE_SETTLE_SECONDS = 1.0
_LOGGER = logging.getLogger(__name__)


class SequenceDeviceWriter(ConnectedDeviceWriter):
    """Inherited send_once/prepare_* unchanged; native-only authenticated read boundary."""

    async def connect_only(self):
        if self._connected:
            raise LocalControlExecutionError("이미 연결된 native writer입니다")
        self._frames = []
        self._omitted_frames = 0
        try:
            await self._session.connect()
            await self._session.authenticate()
            self._connected = True
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise

    async def capture_sequence(self, permit, *, accept_reports):
        if not self._connected:
            raise LocalControlExecutionError("연결되지 않은 native writer입니다")
        permit.require_permitted()

        def observe(raw, selected):
            # Buffer only: no fsync or schema work within the transport timeout.
            if len(self._frames) < 256:
                self._frames.append((datetime.now(UTC), raw, selected))
            else:
                self._omitted_frames += 1

        try:
            raw = await self._session.read_raw_state_capture(
                accept_reports=accept_reports, state_frame_observer=observe
            )
            permit.require_permitted()
            return raw
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise

    def drain_frames(self):
        frames, omitted = getattr(self, "_frames", []), getattr(self, "_omitted_frames", 0)
        self._frames, self._omitted_frames = [], 0
        return frames, omitted


def persist_frames(controller, record, writer, stage):
    """Diagnostic-only sink; a broken sink never replaces an active transport error."""
    try:
        return _persist_frames(controller, record, writer, stage)
    except Exception as error:
        _LOGGER.error(
            "native_frame_diagnostic_failed stage=%s type=%s", stage, type(error).__name__
        )
        return record


def _persist_frames(controller, record, writer, stage):
    frames, omitted = writer.drain_frames()
    if not frames and not omitted:
        return record
    rows = [
        {
            "stage": stage,
            "logical_member": writer.endpoint.target.logical_member,
            "capture_sha256": controller._capture(raw, at),
            "selected": selected,
            "timestamp_kind": "frame_observed_at",
            "complete": False,
            "physical_measurement": False,
        }
        for at, raw, selected in frames
    ]
    if omitted:
        rows.append({"stage": stage, "omitted_frames": omitted, "complete": False})
    return controller._save_final(controller._required(record.request_id), rows)


def staged_controls(context):
    names = [item.target.logical_member for item in context.originals]
    plan = context.plan
    timers = [
        (
            name,
            {
                "SwitchON": True,
                "TimerON": True,
                "Linkage": "independent",
                "Mode": "constant",
                "Flow": plan.preparation_flow_for(name),
                "Frequency": plan.preparation_frequency,
            },
        )
        for name in names
    ]
    roles = [(names[0], {"Linkage": "master"})]
    for name in names[1:]:
        roles.append((name, {"Linkage": "sync_slave"}))
        if plan.mode == "async":
            roles.append((name, {"Linkage": "async_slave"}))
    return (*timers, *roles)


def validate_staged_assistance_source(controller, source, context):
    """Recognize this owned staged plan, not an arbitrary eight/nine-action record."""
    if context.kind != "attach" or context.activation_sequence != STAGED_ROLES:
        raise ValueError("단계별 활성화 원본이 아닙니다")
    if len(context.originals) != 2 or source.phase is not TransactionPhase.RECOVERY_REQUIRED:
        raise ValueError("지원할 단계별 Pro pair 원본이 아닙니다")
    names = [o.target.logical_member for o in context.originals]
    images = context.plan.compile_images()
    targets = {o.target.logical_member: o.target for o in context.originals}
    expected = [(name, "native_controls", controller._safe(context.plan, name)) for name in names]
    expected += [(name, "native_image", images[name]) for name in names]
    expected += [(name, "native_controls", values) for name, values in staged_controls(context)]
    if len(source.actions) != len(expected):
        raise ValueError("단계별 원본 action 수가 다릅니다")
    for action, (name, function, values) in zip(source.actions, expected, strict=True):
        raw = controller._journal.load_blob(action.expected_readback_sha256)
        actual = raw if isinstance(values, bytes) else json.loads(raw)
        if action.target != targets[name] or action.function != function or actual != values:
            raise ValueError("소유 단계별 원본 계획과 action이 다릅니다")
    done = {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}
    phases = [a.phase for a in source.actions]
    if all(p in done for p in phases):
        return  # Final comparison can fail after acknowledged or reply-confirmed actions.
    first = next(i for i, phase in enumerate(phases) if phase not in done)
    if (
        first not in (7, 8)
        or phases[first] not in {ActionPhase.PREPARED, ActionPhase.UNCERTAIN}
        or any(p is not ActionPhase.PREPARED for p in phases[first + 1 :])
    ):
        raise ValueError("지원 대상은 단계별 slave 역할 또는 최종 대조 실패입니다")


async def advance_staged_roles(controller, record, context, endpoints, permit):
    """Journaled writes, staged TimerON, then role deltas on verified pair connections."""
    writers, captures, last_send = {}, {}, {}
    by_name = {e.target.logical_member: e for e in endpoints}
    index = None
    started = failure_clock_ns()

    async def close_all():
        pending = tuple(writers.values())
        writers.clear()
        await asyncio.gather(*(writer.close() for writer in pending))

    async def read_one(endpoint, reports, stage):
        nonlocal record
        name = endpoint.target.logical_member
        began = datetime.now(UTC)
        try:
            raw = await writers[name].capture_sequence(permit, accept_reports=reports)
            captures[name] = (began, raw)
            return began, raw
        finally:
            record = persist_frames(controller, record, writers[name], stage)

    async def refresh_pair(reports, stage):
        nonlocal record
        await close_all()
        captures.clear()
        for endpoint in endpoints:
            writers[endpoint.target.logical_member] = controller._writer_factory(endpoint)
        # Complete BOTH authentications before either read, as in the successful harness.
        connected = await asyncio.gather(
            *(w.connect_only() for w in writers.values()), return_exceptions=True
        )
        for result in connected:
            if isinstance(result, BaseException):
                raise result
        results = await asyncio.gather(
            *(read_one(e, reports, stage) for e in endpoints), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        record = controller._required(record.request_id)

    def verify_pair_roles():
        for endpoint in endpoints:
            name = endpoint.target.logical_member
            applied = [
                a
                for a in record.actions
                if a.target == endpoint.target
                and a.phase in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}
            ]
            wanted = {"SwitchON": True, "TimerON": True, "Linkage": "independent"}
            for action in applied:
                if action.function == "native_controls":
                    fields = json.loads(
                        controller._journal.load_blob(action.expected_readback_sha256)
                    )
                    wanted.update({k: v for k, v in fields.items() if k in wanted})
            actual = get_product_schema(endpoint.target.product_key).decode_status(
                captures[name][1].status_payload
            )
            if any(actual[k] != v for k, v in wanted.items()):
                raise LocalControlExecutionError("단계별 pair의 명시 역할/전원 반환값 불일치")

    def observe_before_async():
        nonlocal record
        rows = []
        for endpoint in endpoints:
            name = endpoint.target.logical_member
            began, raw = captures[name]
            values = get_product_schema(endpoint.target.product_key).decode_status(
                raw.status_payload
            )
            rows.append(
                {
                    "stage": "before_async_role_only",
                    "logical_member": name,
                    "capture_sha256": controller._capture(raw, began),
                    "actual_controls": {
                        key: values[key]
                        for key in (
                            "SwitchON",
                            "TimerON",
                            "Linkage",
                            "Mode",
                            "Flow",
                            "Frequency",
                            "AutoMode",
                            "AutoFlow",
                            "AutoFreq",
                        )
                    },
                    "actual_schedule_sha256": hashlib.sha256(
                        extract_current_pro_schedule_image(raw.status_payload)
                    ).hexdigest(),
                    "complete": False,
                    "physical_measurement": False,
                }
            )
        record = controller._save_final(record, rows)

    async def post_read(endpoint, prepared, reports):
        nonlocal record
        name = endpoint.target.logical_member
        last_error = None
        for attempt in range(_CONTROL.readback_attempts):
            await controller._sequence_sleep(_CONTROL.readback_delay_ms / 1000)
            permit.require_permitted()
            try:
                if attempt and last_error is not None:
                    # Failed streams stay quarantined. This is a read, NEVER another send.
                    await writers[name].close()
                    writers[name] = controller._writer_factory(endpoint)
                    await writers[name].connect_only()
                began, raw = await read_one(endpoint, reports, "staged_after_read")
                record = controller._evidence(
                    controller._required(record.request_id), index, raw, began, prepared, "after"
                )
                last_error = None
                if verify_prepared_readback(prepared, raw.status_payload):
                    return
            except (ProtocolError, OSError) as error:
                last_error = error
                report_action_failure(
                    controller._required(record.request_id),
                    index,
                    error,
                    started_ns=started,
                    failed_ns=failure_clock_ns(),
                )
        if last_error is not None:
            raise last_error
        raise LocalControlExecutionError("전송 후 반환값 불일치")

    try:
        await refresh_pair(False, "staged_initial_pair")
        for index, action in enumerate(record.actions):
            if action.phase in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}:
                continue
            started = failure_clock_ns()
            endpoint = by_name[action.target.logical_member]
            name = endpoint.target.logical_member
            prepared = controller._forward_prepared(action, endpoint)
            if index == 8:
                observe_before_async()  # Same pair captures and connection, no extra read.
            reports = action.verification_kind != "schedule_image"
            if action.verification_kind == "schedule_image":
                # The existing reply-only image precondition remains adjacent to each send.
                await refresh_pair(False, "staged_before_image")
            elif index in (4, 5):
                # Keep the existing connection, but don't reuse a BEFORE from the peer's write.
                await read_one(endpoint, True, "staged_before_timer")
                record = controller._required(record.request_id)
            began, raw = captures[name]
            record = controller._evidence(record, index, raw, began, prepared, "before")
            if verify_prepared_readback(prepared, raw.status_payload):
                record = controller._save_action(
                    record,
                    index,
                    phase=(
                        ActionPhase.NO_CHANGE
                        if action.phase is ActionPhase.PREPARED
                        else ActionPhase.VERIFIED
                    ),
                    transaction_phase=TransactionPhase.EXECUTING,
                )
            else:
                if action.phase is not ActionPhase.PREPARED:
                    raise LocalControlExecutionError(
                        "이미 시도한 명령의 반환값 불일치: 재전송하지 않습니다"
                    )
                if not reports:
                    values = get_product_schema(endpoint.target.product_key).decode_status(
                        raw.status_payload
                    )
                    if values["TimerON"] is not False or values["Linkage"] != "independent":
                        raise LocalControlExecutionError(
                            "시간표 전송 전 independent·TimerOFF 미확인"
                        )
                remaining = (
                    last_send.get(name, -float("inf"))
                    + _CONTROL.minimum_command_interval_ms / 1000
                    - controller._sequence_clock()
                )
                if remaining > 0:
                    await controller._sequence_sleep(remaining)
                permit.require_permitted()
                record = controller._save_action(
                    record,
                    index,
                    phase=ActionPhase.FORWARD_ATTEMPTING,
                    transaction_phase=TransactionPhase.EXECUTING,
                )
                last_send[name] = controller._sequence_clock()
                ack = await writers[name].send_once(prepared.forward_payload, permit)
                record = controller._save_action(
                    record,
                    index,
                    phase=ActionPhase.FORWARD_ACKNOWLEDGED,
                    acknowledgement_sha256=controller._journal.install_blob(ack),
                )
                await post_read(endpoint, prepared, reports)
                record = controller._save_action(record, index, phase=ActionPhase.VERIFIED)
            if index < 2:
                # Pause proof on newly authenticated streams, before any image mutation.
                await refresh_pair(True, "staged_pause_fresh")
                if not verify_prepared_readback(prepared, captures[name][1].status_payload):
                    raise LocalControlExecutionError("새 연결의 준비값 반환 불일치")
            elif index == 5:
                await refresh_pair(False, "staged_timer_pair")
                verify_pair_roles()
                # Observe current Auto without importing the old clock/Auto refusal gates.
                await refresh_pair(True, "staged_auto_observation")
                await refresh_pair(True, "staged_role_preflight")
                await controller._sequence_sleep(_ROLE_SETTLE_SECONDS)
                await refresh_pair(False, "staged_before_master")
                verify_pair_roles()
            elif index >= 6:
                # Old fixed-Q2 Sync intermediate uses raw report-capable capture. Other role
                # barriers use explicit replies; do not flatten the two old paths into one.
                reports = context.plan.mode == "async" and index == 7
                await refresh_pair(reports, "staged_post_role_pair")
                verify_pair_roles()
        return await controller._finish(record, context, endpoints, permit, captures=captures)
    except BaseException as error:
        current = controller._required(record.request_id)
        if index is None:
            controller._save_transaction_phase(current, TransactionPhase.RECOVERY_REQUIRED)
            _LOGGER.error("native_initial_pair_failed type=%s", type(error).__name__)
            raise
        phase = current.actions[index].phase
        if phase in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}:
            # Later pair/final reads do not erase the action's existing evidence or ACK.
            record = controller._save_transaction_phase(current, TransactionPhase.RECOVERY_REQUIRED)
        else:
            record = controller._save_action(
                current,
                index,
                phase=ActionPhase.PREPARED
                if phase is ActionPhase.PREPARED
                else ActionPhase.UNCERTAIN,
                diagnostic="native_stage_unverified",
                transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
            )
        report_action_failure(
            record, index, error, started_ns=started, failed_ns=failure_clock_ns()
        )
        if (
            isinstance(error, (ProtocolError, OSError))
            and phase is ActionPhase.FORWARD_ATTEMPTING
            and controller._can_reconcile_async(current, context, index)
        ):
            await close_all()
            try:
                record = await controller._reconcile_async_reply(
                    record,
                    index,
                    by_name[current.actions[index].target.logical_member],
                    permit,
                    observe=True,
                )
                await refresh_pair(False, "staged_post_reconciled_role")
                verify_pair_roles()
                return await controller._finish(
                    record, context, endpoints, permit, captures=captures
                )
            except BaseException:
                controller._save_transaction_phase(
                    controller._required(record.request_id), TransactionPhase.RECOVERY_REQUIRED
                )
                raise
        raise
    finally:
        await close_all()
