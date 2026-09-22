"""Pro 제품 결속과 자기 원본 해제. 기존 journal/lease 외의 쓰기 namespace는 없다.

실기 하네스를 호출하지 않는다. attempted action은 절대 재전송하지 않으며, 해제는
자기 attach의 원본으로만 진행한다. 보고값 검증이지 물리 유량·위상 계측은 아니다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    _same_stable_target,
    extract_current_pro_schedule_image,
    verify_prepared_readback,
)
from jebao_flow.local_control.failure_diagnostics import failure_clock_ns, report_action_failure
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    TransactionPhase,
    native_detach_request_id,
)
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan
from jebao_flow.local_control.wavemaker_restore import (
    WavemakerRestoreSnapshot,
    compare_restore_readback,
)
from jebao_flow.local_control.wavemaker_sequence import (
    FLOW_CARRIER_ROLES,
    STAGED_SEQUENCES,
    SequenceDeviceWriter,
    advance_staged_roles,
    planned_images,
    staged_controls,
    staged_layout,
    validate_staged_assistance_source,
)
from jebao_flow.protocol.errors import ProtocolError
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES, LOCAL_WAVEMAKER_PRO_PRODUCT_KEY

_TERMINAL = {TransactionPhase.VERIFIED, TransactionPhase.ROLLED_BACK}
_REPLY_WITHOUT_ACK = "native_async_reply_verified_delivery_unknown"
_AWAIT_OPERATOR = "operator_requested_no_automatic_restore"
_DETACH_SPLIT_SETTLE_SECONDS = 5


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def native_assistance_request_id(attach_id: str) -> str:
    return "wm_assist_" + hashlib.sha256(attach_id.encode("ascii")).hexdigest()


def native_split_assistance_request_id(attach_id: str) -> str:
    return "wm_split_" + hashlib.sha256(attach_id.encode("ascii")).hexdigest()


def native_master_cycle_request_id(attach_id: str) -> str:
    return "wm_cycle_" + hashlib.sha256(attach_id.encode("ascii")).hexdigest()


def native_master_power_cycle_request_id(attach_id: str) -> str:
    return "wm_power_" + hashlib.sha256(attach_id.encode("ascii")).hexdigest()


def native_slave_timer_rearm_request_id(attach_id: str) -> str:
    return "wm_rearm_" + hashlib.sha256(attach_id.encode("ascii")).hexdigest()


class NativeOriginal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: LocalDeviceBinding
    status_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NativeContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    kind: Literal[
        "attach",
        "detach",
        "detach_assist",
        "detach_assist_split_v1",
        "master_link_cycle_v1",
        "master_power_cycle_v1",
        "slave_timer_rearm_v1",
    ]
    attach_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    source_context_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: NativeWavemakerPlan
    originals: tuple[NativeOriginal, ...] = Field(min_length=2)
    assisted_record_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Stored records keep their own image/action layout forever. Reading an old record must
    # never reinterpret its owned bytes through the newest attachment contract.
    activation_sequence: (
        Literal["staged_roles_v1", "staged_roles_v2", "flow_carrier_roles_v1"] | None
    ) = None
    await_operator_observation: Literal[True] | None = None
    master_cycle_on_async_failure: Literal[True] | None = None
    power_cycle_basis_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    power_off_seconds: Literal[10] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_assistance(self, handler):
        result = handler(self)
        if self.assisted_record_sha256 is None:
            result.pop("assisted_record_sha256", None)
        if self.activation_sequence is None:
            result.pop("activation_sequence", None)
        if self.await_operator_observation is None:
            result.pop("await_operator_observation", None)
        if self.master_cycle_on_async_failure is None:
            result.pop("master_cycle_on_async_failure", None)
        if self.power_cycle_basis_sha256 is None:
            result.pop("power_cycle_basis_sha256", None)
        if self.power_off_seconds is None:
            result.pop("power_off_seconds", None)
        return result


class NativeWavemakerController(LocalControlCoordinator):
    """기존 qualified writer를 순서 있는 제품 lifecycle에 연결한다."""

    def __init__(self, *, sequence_sleep=asyncio.sleep, sequence_clock=None, **kwargs):
        session_factory = kwargs.pop("session_factory", None)
        if kwargs.get("writer_factory") is not None and session_factory is not None:
            raise ValueError("choose either a session factory or a writer factory")
        if kwargs.get("writer_factory") is None:
            kwargs["writer_factory"] = (
                SequenceDeviceWriter
                if session_factory is None
                else lambda endpoint: SequenceDeviceWriter(
                    endpoint, session_factory=session_factory
                )
            )
        super().__init__(**kwargs)
        self._sequence_sleep = sequence_sleep
        self._sequence_clock = sequence_clock or (lambda: asyncio.get_running_loop().time())

    def targets_for(self, request_id: str) -> tuple[LocalDeviceBinding, ...]:
        record = self._required(request_id)
        return tuple(item.target for item in self._context(record).originals)

    def status(self, request_id: str) -> dict[str, object]:
        return self._public(self._required(request_id), current=False)

    async def attach(
        self,
        *,
        request_id: str,
        plan: NativeWavemakerPlan,
        targets: tuple[LocalDeviceBinding, ...],
        await_operator_observation: bool = False,
        master_cycle_on_async_failure: bool = False,
    ) -> dict[str, object]:
        self._validate_targets(plan, targets)
        if type(master_cycle_on_async_failure) is not bool or (
            master_cycle_on_async_failure and plan.mode != "async"
        ):
            raise ValueError("master 링크 cycle은 명시적인 Async 옵션입니다")
        if type(await_operator_observation) is not bool or (
            await_operator_observation and plan.mode != "async"
        ):
            raise ValueError("사용자 관찰 대기는 명시적인 Async 실행에만 허용됩니다")
        observation_intent = (
            {"await_operator_observation": True} if await_operator_observation else {}
        )
        if master_cycle_on_async_failure:
            observation_intent["master_cycle_on_async_failure"] = True
        # 기존 request와 재생성된 attestation 시각은 다를 수 있다. 신원 4요소는 고정이다.
        intent = hashlib.sha256(
            _json(
                {
                    "plan": plan.model_dump(mode="json"),
                    "targets": [self._stable(item) for item in targets],
                    **observation_intent,
                }
            )
        ).hexdigest()
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            previous = self._journal.load(request_id)
            if previous is not None:
                if previous.intent_sha256 != intent or self._context(previous).kind != "attach":
                    raise ValueError("같은 요청 id에 다른 결속 계획을 사용할 수 없습니다")
                if previous.phase in _TERMINAL:
                    return self._public(previous, current=False)
                if self._awaiting_operator(previous, self._context(previous)):
                    cycle = self._journal.load(native_master_cycle_request_id(request_id))
                    if cycle is not None:
                        return self._public(cycle, current=False)
                    return self._public(previous, current=False)
                raise LocalControlExecutionError(
                    f"미완료 결속입니다: recover-wavemaker --request-id {request_id}"
                )
            self._require_available(targets, allowed=())
            if len(targets) != 2:
                raise ValueError("단계별 native 실행은 Pro master/slave 한 쌍입니다")
            endpoints = await self._resolver.resolve_many(targets)
            self._validate_endpoints(targets, endpoints)
            self._qualify(request_id, plan, endpoints)
            originals = []
            for endpoint in endpoints:
                capture, status = await self._read(endpoint, permit, reports=False)
                WavemakerRestoreSnapshot(request_id, endpoint.target, status)
                originals.append(
                    NativeOriginal(
                        target=endpoint.target,
                        status_sha256=self._journal.install_blob(status),
                        capture_sha256=capture,
                    )
                )
            context = NativeContext(
                kind="attach",
                attach_request_id=request_id,
                plan=plan,
                schema_sha256=current_protocol_schema_sha256(),
                originals=tuple(originals),
                activation_sequence=FLOW_CARRIER_ROLES,
                await_operator_observation=True if await_operator_observation else None,
                master_cycle_on_async_failure=True if master_cycle_on_async_failure else None,
            )
            record = self._prepare(request_id, intent, context, endpoints)
            try:
                return await self._advance(record, context, endpoints, permit)
            except BaseException as original_error:
                current = self._required(request_id)
                if self._awaiting_operator(current, context):
                    # The operator explicitly owns the observation interval. Preserve the
                    # failed action/ACK evidence; do not resend, detach, or start a timer.
                    current = self._save_transaction_phase(
                        current, TransactionPhase.RECOVERY_REQUIRED
                    )
                    if master_cycle_on_async_failure and isinstance(original_error, Exception):
                        from jebao_flow.local_control.wavemaker_master_cycle import cycle_locked

                        return await cycle_locked(self, current, context, endpoints, permit)
                    return self._public(current, current=False)
                # 복구는 caller 취소와 분리해 완료까지 기다린다. 성공 여부는 별도로 보존한다.
                task = asyncio.create_task(self._detach_locked(record, context, endpoints, permit))
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if task.cancelled() or task.exception() is not None:
                    raise LocalControlExecutionError(
                        f"결속 실패 후 원복 미확인: recover-wavemaker --request-id {request_id}"
                    ) from None
                if isinstance(original_error, asyncio.CancelledError):
                    raise original_error
                raise LocalControlExecutionError(
                    f"결속 실패, 자기 원본 복원 확인: {native_detach_request_id(request_id)}"
                ) from None

    def _awaiting_operator(self, record, context):
        if (
            (
                context.await_operator_observation is not True
                and context.master_cycle_on_async_failure is not True
            )
            or context.kind != "attach"
            or context.plan.mode != "async"
            or context.activation_sequence not in STAGED_SEQUENCES
            or record.phase is TransactionPhase.ROLLED_BACK
            or (
                record.phase is TransactionPhase.VERIFIED
                and context.await_operator_observation is not True
            )
        ):
            return False
        try:
            # A stored record is read from its own shape; an unknown one is simply not ours.
            layout = staged_layout(context, len(record.actions))
        except ValueError:
            return False
        if any(
            a.phase not in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}
            for a in record.actions[: layout.async_role]
        ) or record.actions[layout.async_role].phase not in {
            ActionPhase.UNCERTAIN,
            ActionPhase.FORWARD_ATTEMPTING,
            ActionPhase.VERIFIED,
        }:
            return False
        # Reuse the exact owned plan comparison, not just the action count or last field.
        role = layout.async_role
        actions = record.actions
        if actions[role].phase is ActionPhase.FORWARD_ATTEMPTING:
            # A crash at the send boundary also means unknown delivery, not permission
            # to resend. This is a validation view; the stored phase stays untouched.
            actions = (
                *actions[:role],
                actions[role].model_copy(update={"phase": ActionPhase.UNCERTAIN}),
            )
        validate_staged_assistance_source(
            self,
            record.model_copy(
                update={"phase": TransactionPhase.RECOVERY_REQUIRED, "actions": actions}
            ),
            context,
        )
        return True

    async def detach(
        self,
        *,
        attachment_id: str,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> dict[str, object]:
        source = self._required(attachment_id)
        context = self._context(source)
        if context.kind != "attach":
            raise ValueError("해제에는 자기 attach 요청 id가 필요합니다")
        self._require_current_targets(context, targets)
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            source = self._required(attachment_id)
            context = self._context(source)
            done = self._journal.load(native_detach_request_id(attachment_id))
            if done is not None and done.phase is TransactionPhase.VERIFIED:
                self._context(done)
                self._close_failed_source(source)
                return self._public(done, current=False)
            self._require_available(
                targets,
                allowed=(
                    attachment_id,
                    native_detach_request_id(attachment_id),
                    *self._owned_cycle_ids(context),
                ),
            )
            endpoints = await self._resolver.resolve_many(
                tuple(o.target for o in context.originals)
            )
            self._validate_endpoints(tuple(o.target for o in context.originals), endpoints)
            return await self._detach_locked(source, context, endpoints, permit)

    async def recover_native(
        self,
        *,
        request_id: str,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> dict[str, object]:
        context = self._context(self._required(request_id))
        return await self.detach(attachment_id=context.attach_request_id, targets=targets)

    async def assist_detach(self, *, request_id, targets):
        """명시적으로 승인된 별도 scalar 5단계. 기존 atomic 재전송·역순 복구 없음."""
        source = self._required(request_id)
        context = self._context(source)
        if context.kind != "attach":
            raise ValueError("지원에는 자기 attach 원본이 필요합니다")
        self._require_current_targets(context, targets)
        child_id = native_detach_request_id(request_id)
        assist_id = native_assistance_request_id(request_id)
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            child = self._required(child_id)
            self._context(child)
            record = self._journal.load(assist_id)
            if record is not None:
                assist_context = self._context(record)
                if child.phase is TransactionPhase.VERIFIED:
                    return self._public(child, current=False)
            else:
                self._require_stalled_first_slave(child, context)
            source = self._required(request_id)
            self._require_assistance_source(source, context)
            self._require_available(
                targets, allowed=(request_id, child_id, assist_id, *self._owned_cycle_ids(context))
            )
            originals = tuple(o.target for o in context.originals)
            endpoints = await self._resolver.resolve_many(originals)
            self._validate_endpoints(originals, endpoints)
            self._qualify(assist_id, context.plan, endpoints)
            if record is None:
                assist_context = NativeContext(
                    **{
                        **context.model_dump(),
                        "kind": "detach_assist",
                        "source_context_sha256": source.native_context_sha256,
                        "assisted_record_sha256": self._journal.install_blob(
                            child.model_dump_json().encode()
                        ),
                    }
                )
                # 현재 raw는 앱 명령 shape용, rollback 원본은 항상 attach 전 raw다.
                current = {}
                for endpoint in endpoints:
                    _, status = await self._read(endpoint, permit, reports=True)
                    current[endpoint.target.logical_member] = status
                snapshots = self._snapshots(context)
                prepared = []
                for endpoint, function, value in self._assistance_steps(context, endpoints):
                    intent = LocalControlIntent(
                        request_id=assist_id, target=endpoint.target, function=function, value=value
                    )
                    self._catalog.validate_intent(intent)
                    writer = self._writer_factory(endpoint)
                    scalar = writer.prepare_scalar(intent, current[endpoint.target.logical_member])
                    owned = writer.prepare_native_controls(
                        json.loads(scalar.expected_readback),
                        snapshots[endpoint.target.logical_member].baseline_status,
                    )
                    if scalar.forward_payload != owned.forward_payload:
                        raise ValueError("앱 scalar와 소유 복구 payload가 다릅니다")
                    if owned.forward_payload_sha256 == child.actions[0].forward_payload_sha256:
                        raise ValueError("미확인 atomic payload 재사용 금지")
                    prepared.append(replace(owned, function=function))
                record = self._create_record(
                    request_id=assist_id,
                    intent_sha256=hashlib.sha256(
                        _json(assist_context.model_dump(mode="json"))
                    ).hexdigest(),
                    prepared=tuple(prepared),
                    native_context_sha256=self._journal.install_blob(
                        assist_context.model_dump_json().encode()
                    ),
                )
            # 실패는 _advance가 보존하고 정지한다. 일반 rollback/새 id 재시도는 없다.
            if record.phase is not TransactionPhase.VERIFIED:
                await self._advance(record, assist_context, endpoints, permit)
            return await self._detach_locked(source, context, endpoints, permit)

    async def assist_detach_split(self, *, request_id, targets):
        """Split a stalled slave compound into two distinct app-shaped owned frames.

        The original detach and assistance payloads remain immutable and are never resent.
        This child exists only for the observed state where the master role-only release was
        acknowledged but the bound slave did not acknowledge or apply the compound timer-off.
        """

        from jebao_flow.local_control.wavemaker_detach_split import split_detach_locked

        source = self._required(request_id)
        context = self._context(source)
        if context.kind != "attach":
            raise ValueError("분리 지원에는 자기 attach 원본이 필요합니다")
        self._require_current_targets(context, targets)
        child_id = native_detach_request_id(request_id)
        assist_id = native_assistance_request_id(request_id)
        split_id = native_split_assistance_request_id(request_id)
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            child = self._required(child_id)
            support = self._required(assist_id)
            self._context(child)
            self._context(support)
            self._require_available(
                targets,
                allowed=(
                    request_id,
                    child_id,
                    assist_id,
                    split_id,
                    *self._owned_cycle_ids(context),
                ),
            )
            endpoints = await self._resolver.resolve_many(
                tuple(o.target for o in context.originals)
            )
            self._validate_endpoints(tuple(o.target for o in context.originals), endpoints)
            self._qualify(split_id, context.plan, endpoints)
            result = await split_detach_locked(
                self,
                source,
                context,
                child,
                support,
                endpoints,
                permit,
            )
        if result["accepted"]:
            return await self.assist_detach(request_id=request_id, targets=targets)
        return result

    def _owned_cycle_ids(self, context):
        if context.master_cycle_on_async_failure is not True:
            return ()
        request_ids = (
            native_master_cycle_request_id(context.attach_request_id),
            native_master_power_cycle_request_id(context.attach_request_id),
            native_slave_timer_rearm_request_id(context.attach_request_id),
        )
        for request_id in request_ids:
            record = self._journal.load(request_id)
            if record is not None:
                self._context(record)
        return request_ids

    async def cycle_master(self, *, request_id, targets):
        """Resume only this explicitly opted-in source's two master-only actions."""
        from jebao_flow.local_control.wavemaker_master_cycle import cycle_locked

        source = self._required(request_id)
        context = self._context(source)
        self._require_current_targets(context, targets)
        if context.kind != "attach" or context.master_cycle_on_async_failure is not True:
            raise ValueError("이 attach에는 master cycle 승인이 없습니다")
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            cycle = self._journal.load(native_master_cycle_request_id(request_id))
            if cycle is not None and cycle.phase in _TERMINAL:
                return self._public(cycle, current=False)
            self._require_available(targets, allowed=(request_id, *self._owned_cycle_ids(context)))
            if not self._awaiting_operator(source, context):
                raise ValueError("마지막 Async 이후 보류된 자기 source만 지원합니다")
            endpoints = await self._resolver.resolve_many(
                tuple(o.target for o in context.originals)
            )
            self._validate_endpoints(tuple(o.target for o in context.originals), endpoints)
            return await cycle_locked(self, source, context, endpoints, permit)

    async def cycle_master_power(self, *, request_id, targets):
        """Power-cycle only the owned master while preserving the pending Async source."""

        from jebao_flow.local_control.wavemaker_power_cycle import cycle_power_locked

        source = self._required(request_id)
        context = self._context(source)
        self._require_current_targets(context, targets)
        if context.kind != "attach" or context.master_cycle_on_async_failure is not True:
            raise ValueError("이 attach에는 master 전원 cycle 소유권이 없습니다")
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            source = self._required(request_id)
            context = self._context(source)
            child_id = native_master_power_cycle_request_id(request_id)
            child = self._journal.load(child_id)
            if child is not None and child.phase in _TERMINAL:
                return self._public(child, current=False)
            self._require_available(targets, allowed=(request_id, *self._owned_cycle_ids(context)))
            if not self._awaiting_operator(source, context):
                raise ValueError("마지막 Async 이후 보류된 자기 source만 지원합니다")
            endpoints = await self._resolver.resolve_many(
                tuple(o.target for o in context.originals)
            )
            self._validate_endpoints(tuple(o.target for o in context.originals), endpoints)
            return await cycle_power_locked(self, source, context, endpoints, permit)

    async def rearm_async_slave_schedule(self, *, request_id, targets):
        """Re-enable the owned Async slave without sending either schedule image."""

        from jebao_flow.local_control.wavemaker_slave_rearm import rearm_slave_locked

        source = self._required(request_id)
        context = self._context(source)
        self._require_current_targets(context, targets)
        if (
            context.kind != "attach"
            or context.plan.mode != "async"
            or context.master_cycle_on_async_failure is not True
        ):
            raise ValueError("이 attach에는 Async slave 재진입 소유권이 없습니다")
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            source = self._required(request_id)
            context = self._context(source)
            child_id = native_slave_timer_rearm_request_id(request_id)
            child = self._journal.load(child_id)
            if child is not None and child.phase in _TERMINAL:
                return self._public(child, current=False)
            self._require_available(targets, allowed=(request_id, *self._owned_cycle_ids(context)))
            if not self._awaiting_operator(source, context):
                raise ValueError("마지막 Async 이후 보류된 자기 source만 재진입할 수 있습니다")
            endpoints = await self._resolver.resolve_many(
                tuple(o.target for o in context.originals)
            )
            self._validate_endpoints(tuple(o.target for o in context.originals), endpoints)
            return await rearm_slave_locked(self, source, context, endpoints, permit)

    def _require_assistance_source(self, source, context):
        if source.phase is TransactionPhase.VERIFIED:
            return
        if context.activation_sequence in STAGED_SEQUENCES:
            validate_staged_assistance_source(self, source, context)
            return
        # 2026-09-15 product Async: original ownership survives its last-role ACK timeout.
        # This admits recovery only; neither the role command nor the child atomic is resent.
        if (
            source.phase is not TransactionPhase.RECOVERY_REQUIRED
            or context.plan.mode != "async"
            or len(context.originals) != 2
            or len(source.actions) != 7
            or any(a.phase is not ActionPhase.VERIFIED for a in source.actions[:6])
            or not (
                source.actions[6].phase is ActionPhase.UNCERTAIN
                or (
                    source.actions[6].phase is ActionPhase.VERIFIED
                    and source.actions[6].acknowledgement_sha256 is None
                    and source.actions[6].diagnostic == _REPLY_WITHOUT_ACK
                )
            )
            or source.actions[6].target != context.originals[1].target
            or source.actions[6].function != "native_controls"
            or json.loads(self._journal.load_blob(source.actions[6].expected_readback_sha256))
            not in (
                {"SwitchON": True, "TimerON": True, "Linkage": "async_slave"},
                {"Linkage": "async_slave"},
            )
        ):
            raise ValueError(
                "지원에는 완료된 attach 또는 마지막 Async 전환만 미확인인 원본이 필요합니다"
            )

    @staticmethod
    def _assistance_steps(context, endpoints):
        master, slave = endpoints
        return (
            (master, "linkage_role", "independent"),
            (slave, "schedule_enable", False),
            (slave, "manual_mode", "constant"),
            (slave, "manual_flow", context.plan.preparation_flow_for(slave.target.logical_member)),
            (slave, "manual_frequency", context.plan.preparation_frequency),
        )

    def _require_stalled_first_slave(self, child, context):
        if (
            len(context.originals) != 2
            or child.request_id != native_detach_request_id(context.attach_request_id)
            or child.phase is not TransactionPhase.RECOVERY_REQUIRED
            or len(child.actions) != 6
            or child.actions[0].phase is not ActionPhase.UNCERTAIN
            or child.actions[0].function != "native_controls"
            or child.actions[0].target != context.originals[1].target
            or any(a.phase is not ActionPhase.PREPARED for a in child.actions[1:])
            or json.loads(self._journal.load_blob(child.actions[0].expected_readback_sha256))
            != self._safe(context.plan, context.originals[1].target.logical_member)
        ):
            raise ValueError("지원 범위는 첫 slave 해제만 미확인인 자기 Pro pair입니다")

    def _assistance_evidence(self, record, index):
        context = NativeContext.model_validate_json(
            self._journal.load_blob(record.native_context_sha256)
        )
        if context.kind != "detach" or index != 0:
            return None
        support = self._journal.load(native_assistance_request_id(context.attach_request_id))
        if support is None or support.phase is not TransactionPhase.VERIFIED:
            return None
        self._context(support)
        return {
            "request_id": support.request_id,
            "journal_sha256": self._journal.record_digest(support),
            "final_sha256": support.native_final_sha256,
            "original_delivery": "UNKNOWN",
        }

    def _prepare(self, request_id, intent, context, endpoints):
        prepared = []
        snapshots = self._snapshots(context)
        writers = {e.target.logical_member: self._writer_factory(e) for e in endpoints}
        names = [item.target.logical_member for item in context.originals]
        staged = context.kind == "attach" and context.activation_sequence in STAGED_SEQUENCES
        # 전송 전 준비만 한다. 각 action의 실제 no-change와 image 안전 조건은 직전 읽기로 판정한다.
        for name in names if staged else names[1:] + names[:1]:
            prepared.append(
                writers[name].prepare_native_controls(
                    self._safe(context.plan, name), snapshots[name].baseline_status
                )
            )
        images = (
            planned_images(context)
            if context.kind == "attach"
            else {name: snapshot.image for name, snapshot in snapshots.items()}
        )
        for name in names:
            prepared.append(
                writers[name].prepare_native_image(images[name], snapshots[name].baseline_status)
            )
        if staged:
            for name, desired in staged_controls(context):
                prepared.append(
                    writers[name].prepare_native_controls(desired, snapshots[name].baseline_status)
                )
        elif context.kind == "attach":
            for index, name in enumerate(names):
                role = "master" if index == 0 else "sync_slave"
                desired = {"SwitchON": True, "TimerON": True, "Linkage": role}
                prepared.append(
                    writers[name].prepare_native_controls(desired, snapshots[name].baseline_status)
                )
                if index > 0 and context.plan.mode == "async":
                    prepared.append(
                        writers[name].prepare_native_controls(
                            {"Linkage": "async_slave"},
                            snapshots[name].baseline_status,
                        )
                    )
        else:
            for name in names[1:] + names[:1]:
                prepared.append(
                    writers[name].prepare_native_restore_controls(snapshots[name].baseline_status)
                )
        return self._create_record(
            request_id=request_id,
            intent_sha256=intent,
            prepared=tuple(prepared),
            native_context_sha256=self._journal.install_blob(context.model_dump_json().encode()),
        )

    async def _detach_locked(self, source, context, endpoints, permit):
        source = self._required(context.attach_request_id)
        self._context(source)
        request_id = native_detach_request_id(source.request_id)
        record = self._journal.load(request_id)
        if record is None:
            detach_context = NativeContext(
                **{
                    **context.model_dump(),
                    "kind": "detach",
                    "source_context_sha256": source.native_context_sha256,
                }
            )
            record = self._prepare(
                request_id,
                hashlib.sha256(
                    _json({"attach": source.request_id, "context": source.native_context_sha256})
                ).hexdigest(),
                detach_context,
                endpoints,
            )
        detach_context = self._context(record)
        if record.phase is TransactionPhase.VERIFIED:
            self._close_failed_source(source)
            return self._public(record, current=False)
        result = await self._advance(record, detach_context, endpoints, permit)
        self._close_failed_source(source)
        return result

    async def _advance(self, record, context, endpoints, permit):
        if context.kind == "attach" and context.activation_sequence in STAGED_SEQUENCES:
            return await advance_staged_roles(self, record, context, endpoints, permit)
        by_name = {e.target.logical_member: e for e in endpoints}
        for index in range(len(record.actions)):
            action = record.actions[index]
            if action.phase in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}:
                continue
            endpoint = by_name[action.target.logical_member]
            writer = self._writer_factory(endpoint)
            open_writers = {endpoint.target.logical_member: writer}
            started = failure_clock_ns()
            try:
                if (
                    context.kind == "master_power_cycle_v1"
                    and index == 1
                    and action.phase is ActionPhase.PREPARED
                ):
                    previous = record.actions[0]
                    if previous.phase is not ActionPhase.VERIFIED:
                        raise LocalControlExecutionError(
                            "master OFF가 이 작업에서 검증되지 않아 ON을 보내지 않습니다"
                        )
                    elapsed = max(
                        0.0,
                        (datetime.now(UTC) - record.updated_at).total_seconds(),
                    )
                    remaining = max(0.0, context.power_off_seconds - elapsed)
                    if remaining:
                        await self._sequence_sleep(remaining)
                    permit.require_permitted()
                if (
                    context.kind == "detach_assist_split_v1"
                    and index == 1
                    and action.phase is ActionPhase.PREPARED
                ):
                    if record.actions[0].phase not in {
                        ActionPhase.VERIFIED,
                        ActionPhase.NO_CHANGE,
                    }:
                        raise LocalControlExecutionError(
                            "slave role-only 해제가 검증되지 않아 timer-off를 보내지 않습니다"
                        )
                    await self._sequence_sleep(_DETACH_SPLIT_SETTLE_SECONDS)
                    permit.require_permitted()
                if (
                    context.kind == "attach"
                    and context.plan.mode == "async"
                    and action.phase is ActionPhase.PREPARED
                    and action.function == "native_controls"
                    and json.loads(self._journal.load_blob(action.expected_readback_sha256))
                    == {"Linkage": "async_slave"}
                ):
                    for item in endpoints:
                        if item.target.logical_member not in open_writers:
                            open_writers[item.target.logical_member] = self._writer_factory(item)
                    record, captures = await self._observe_before_async(
                        record, endpoints, permit, open_writers
                    )
                    began, capture = captures[endpoint.target.logical_member]
                else:
                    began = datetime.now(UTC)
                    capture = await writer.connect_and_capture(
                        accept_reports=action.verification_kind != "schedule_image"
                    )
                # image 안전 전제는 기존 schedule writer와 같은 reply-only 읽기로 확인한다.
                reports = action.verification_kind != "schedule_image"
                permit.require_permitted()
                prepared = self._forward_prepared(action, endpoint)
                record = self._evidence(record, index, capture, began, prepared, "before")
                if verify_prepared_readback(prepared, capture.status_payload):
                    phase = (
                        ActionPhase.NO_CHANGE
                        if action.phase is ActionPhase.PREPARED
                        else ActionPhase.VERIFIED
                    )
                    record = self._save_action(
                        record,
                        index,
                        phase=phase,
                        transaction_phase=TransactionPhase.EXECUTING,
                        diagnostic=(
                            "assisted_target_observed_delivery_unknown"
                            if self._assistance_evidence(record, index) is not None
                            else None
                        ),
                    )
                    continue
                if action.phase is not ActionPhase.PREPARED:
                    raise LocalControlExecutionError(
                        "이미 시도한 명령의 반환값 불일치: 재전송하지 않습니다"
                    )
                if prepared.verification_kind == "schedule_image":
                    values = get_product_schema(endpoint.target.product_key).decode_status(
                        capture.status_payload
                    )
                    if values["TimerON"] is not False or values["Linkage"] != "independent":
                        raise LocalControlExecutionError(
                            "시간표 전송 전 independent·TimerOFF 미확인"
                        )
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.FORWARD_ATTEMPTING,
                    transaction_phase=TransactionPhase.EXECUTING,
                )
                ack = await writer.send_once(prepared.forward_payload, permit)
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.FORWARD_ACKNOWLEDGED,
                    acknowledgement_sha256=self._journal.install_blob(ack),
                )
                began = datetime.now(UTC)
                capture = await writer.capture_readback(permit, accept_reports=reports)
                record = self._evidence(record, index, capture, began, prepared, "after")
                if not verify_prepared_readback(prepared, capture.status_payload):
                    raise LocalControlExecutionError("전송 후 반환값 불일치")
                record = self._save_action(record, index, phase=ActionPhase.VERIFIED)
            except BaseException as error:
                # PREPARED 읽기 실패는 전송 안 됨을 그대로 남긴다. 시도 상태만 uncertain으로 바꾼다.
                current = self._required(record.request_id)
                phase = current.actions[index].phase
                if phase is not ActionPhase.PREPARED:
                    phase = ActionPhase.UNCERTAIN
                record = self._save_action(
                    current,
                    index,
                    phase=phase,
                    diagnostic=(
                        "master_power_cycle_unverified"
                        if context.kind == "master_power_cycle_v1"
                        else "native_stage_unverified"
                    ),
                    transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                )
                report_action_failure(
                    record, index, error, started_ns=started, failed_ns=failure_clock_ns()
                )
                if (
                    isinstance(error, (ProtocolError, OSError))
                    and current.actions[index].phase is ActionPhase.FORWARD_ATTEMPTING
                    and self._can_reconcile_async(current, context, index)
                ):
                    # The failed exchange is never reused. Persist its failure before one read.
                    open_writers.pop(endpoint.target.logical_member)
                    await writer.close()
                    record = await self._reconcile_async_reply(record, index, endpoint, permit)
                    continue
                raise
            finally:
                await asyncio.gather(*(item.close() for item in open_writers.values()))
        return await self._finish(record, context, endpoints, permit)

    def _can_reconcile_async(self, record, context, index):
        """Only the final role delta after this pair's verified Sync, never qualification."""
        # A detach or child context inherits the attach's activation_sequence, so the kind is
        # part of the question, and an unknown action count simply is not one of ours.
        staged = context.kind == "attach" and context.activation_sequence in STAGED_SEQUENCES
        try:
            layout = staged_layout(context, len(record.actions)) if staged else None
        except ValueError:
            return False
        sync = index - 1
        return (
            context.kind == "attach"
            and context.plan.mode == "async"
            and len(context.originals) == 2
            and len(record.actions) == (layout.count if staged else 7)
            and index == (layout.async_role if staged else 6)
            and record.actions[index].acknowledgement_sha256 is None
            and record.actions[index].function == "native_controls"
            and record.actions[index].target == context.originals[1].target
            and record.actions[sync].target == record.actions[index].target
            and record.actions[sync].phase is ActionPhase.VERIFIED
            and json.loads(self._journal.load_blob(record.actions[index].expected_readback_sha256))
            == {"Linkage": "async_slave"}
            and json.loads(self._journal.load_blob(record.actions[sync].expected_readback_sha256))
            == (
                {"Linkage": "sync_slave"}
                if staged
                else {"SwitchON": True, "TimerON": True, "Linkage": "sync_slave"}
            )
        )

    async def _reconcile_async_reply(self, record, index, endpoint, permit, *, observe=False):
        """One fresh reply, no sends. State evidence cannot manufacture an ACK or delivery proof."""
        writer = self._writer_factory(endpoint)
        began = datetime.now(UTC)
        started = failure_clock_ns()
        try:
            if observe:
                await writer.connect_only()
                capture = await writer.capture_sequence(permit, accept_reports=False)
            else:
                capture = await writer.connect_and_capture(accept_reports=False)
            permit.require_permitted()
            prepared = self._forward_prepared(record.actions[index], endpoint)
            record = self._evidence(
                record, index, capture, began, prepared, "after_ack_error_reply"
            )
            if capture.action != 3 or not verify_prepared_readback(
                prepared, capture.status_payload
            ):
                raise LocalControlExecutionError("ACK 부재 후 직접 응답에서 Async 미확인")
            return self._save_action(
                record,
                index,
                phase=ActionPhase.VERIFIED,
                transaction_phase=TransactionPhase.EXECUTING,
                diagnostic=_REPLY_WITHOUT_ACK,
            )
        except BaseException as error:
            report_action_failure(
                self._required(record.request_id),
                index,
                error,
                started_ns=started,
                failed_ns=failure_clock_ns(),
            )
            raise
        finally:
            try:
                if observe:
                    from jebao_flow.local_control.wavemaker_sequence import persist_frames

                    persist_frames(self, record, writer, "after_ack_error_reply_frames")
            finally:
                await writer.close()

    async def _observe_before_async(self, record, endpoints, permit, writers):
        """Observe both fresh streams, then send on the same slave connection.

        The earlier native-scenario-async run refreshed both streams between Sync and Async.
        No reconnect or duplicate BEFORE read intervenes. Clock/Auto are observations only.
        """

        async def capture_one(endpoint):
            began = datetime.now(UTC)
            capture = await writers[endpoint.target.logical_member].connect_and_capture(
                accept_reports=True
            )
            permit.require_permitted()
            return began, capture

        results = await asyncio.gather(
            *(capture_one(endpoint) for endpoint in endpoints), return_exceptions=True
        )
        rows = []
        captures = {}
        for endpoint, result in zip(endpoints, results, strict=True):
            if isinstance(result, BaseException):
                continue
            began, raw = result
            captures[endpoint.target.logical_member] = result
            capture, status = self._capture(raw, began), raw.status_payload
            values = get_product_schema(endpoint.target.product_key).decode_status(status)
            rows.append(
                {
                    "stage": "before_async_role_only",
                    "logical_member": endpoint.target.logical_member,
                    "capture_sha256": capture,
                    "actual_controls": {
                        name: values[name]
                        for name in (
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
                        extract_current_pro_schedule_image(status)
                    ).hexdigest(),
                    "complete": False,
                    "physical_measurement": False,
                }
            )
            record = self._save_final(record, rows)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return record, captures

    async def _finish(self, record, context, endpoints, permit, *, captures=None):
        rows = []
        try:
            snapshots = (
                None
                if context.kind
                in {
                    "master_power_cycle_v1",
                    "slave_timer_rearm_v1",
                    "detach_assist_split_v1",
                }
                else self._snapshots(context)
            )
            images = planned_images(context)
            for index, endpoint in enumerate(endpoints):
                if captures is None:
                    reports = context.kind == "attach" or (
                        context.kind
                        in {
                            "master_power_cycle_v1",
                            "slave_timer_rearm_v1",
                            "detach_assist_split_v1",
                        }
                        and index != 0
                    )
                    capture, status = await self._read(
                        endpoint,
                        permit,
                        reports=reports,
                    )
                else:
                    began, raw = captures[endpoint.target.logical_member]
                    capture, status = self._capture(raw, began), raw.status_payload
                name = endpoint.target.logical_member
                if context.kind == "detach":
                    if snapshots is None:
                        raise AssertionError("detach requires owned restore snapshots")
                    row = compare_restore_readback(
                        snapshots[name], target=endpoint.target, status=status
                    )
                else:
                    expected = (
                        (
                            {"Linkage": "independent"}
                            if index == 0
                            else self._safe(context.plan, name)
                        )
                        if context.kind == "detach_assist"
                        else (
                            {"Linkage": "independent"}
                            if index == 0
                            else {
                                "SwitchON": True,
                                "TimerON": False,
                                "Linkage": "independent",
                            }
                        )
                        if context.kind == "detach_assist_split_v1"
                        else {
                            "SwitchON": True,
                            "TimerON": True,
                            "Linkage": "master" if index == 0 else context.plan.mode + "_slave",
                        }
                    )
                    # The rearm no longer writes the slave's manual Mode and cannot: while the
                    # pair is linked the device reverts every value we put there. Requiring
                    # `constant` here would fail a repair that did exactly what it should.
                    actual = get_product_schema(endpoint.target.product_key).decode_status(status)
                    fields = {
                        key: {
                            "expected": value,
                            "actual": actual[key],
                            "match": value == actual[key],
                        }
                        for key, value in expected.items()
                    }
                    image = extract_current_pro_schedule_image(status)
                    row = {
                        "logical_member": name,
                        "fields": fields,
                        "schedule_match": image == images[name],
                        "expected_schedule_sha256": hashlib.sha256(images[name]).hexdigest(),
                        "actual_schedule_sha256": hashlib.sha256(image).hexdigest(),
                        "complete": image == images[name]
                        and all(v["match"] for v in fields.values()),
                    }
                    if context.kind == "detach_assist":
                        # 지원은 역할/준비 제어만 증명한다. 전체 원 image는 기존 detach가 검증한다.
                        row.pop("schedule_match")
                        row.pop("expected_schedule_sha256")
                        row["complete"] = all(v["match"] for v in fields.values())
                rows.append({**row, "capture_sha256": capture})
                record = self._save_final(record, rows)
            if not all(row["complete"] for row in rows):
                raise LocalControlExecutionError("최종 대조 불일치")
        except BaseException:
            record = self._save_transaction_phase(record, TransactionPhase.RECOVERY_REQUIRED)
            raise
        record = self._save_transaction_phase(
            record,
            TransactionPhase.RECOVERY_REQUIRED
            if context.await_operator_observation and self._awaiting_operator(record, context)
            else TransactionPhase.VERIFIED,
        )
        return self._public(record, current=True)

    async def _read(self, endpoint, permit, *, reports):
        writer = self._writer_factory(endpoint)
        try:
            began = datetime.now(UTC)
            capture = await writer.connect_and_capture(accept_reports=reports)
            permit.require_permitted()
            return self._capture(capture, began), capture.status_payload
        finally:
            await writer.close()

    def _capture(self, capture, began):
        return self._journal.install_blob(
            _json(
                {
                    "action": capture.action,
                    "started_at": began.isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "wire_sha256": self._journal.install_blob(capture.wire_frame),
                    "status_sha256": self._journal.install_blob(capture.status_payload),
                }
            )
        )

    def _evidence(self, record, index, capture, began, prepared, stage):
        if prepared.verification_kind == "schedule_image":
            actual = extract_current_pro_schedule_image(capture.status_payload)
            comparison = {
                "expected_sha256": hashlib.sha256(prepared.expected_readback).hexdigest(),
                "actual_sha256": hashlib.sha256(actual).hexdigest(),
            }
        else:
            expected = json.loads(prepared.expected_readback)
            actual = get_product_schema(prepared.target.target.product_key).decode_status(
                capture.status_payload
            )
            comparison = {
                key: {"expected": value, "actual": actual[key], "match": value == actual[key]}
                for key, value in expected.items()
            }
        action = record.actions[index]
        digest = self._journal.install_blob(
            _json(
                {
                    "previous": action.native_evidence_sha256,
                    "stage": stage,
                    "assistance": self._assistance_evidence(record, index),
                    "capture_sha256": self._capture(capture, began),
                    "comparison": comparison,
                    "match": verify_prepared_readback(prepared, capture.status_payload),
                }
            )
        )
        actions = list(record.actions)
        actions[index] = JournalAction.model_validate(
            {**action.model_dump(), "native_evidence_sha256": digest}
        )
        replacement = record.evolve(actions=tuple(actions))
        self._journal.save(record, replacement)
        return replacement

    def _save_final(self, record, rows):
        digest = self._journal.install_blob(
            _json({"previous": record.native_final_sha256, "devices": rows})
        )
        replacement = LocalControlJournalRecord.model_validate(
            {
                **record.evolve().model_dump(),
                "native_final_sha256": digest,
            }
        )
        self._journal.save(record, replacement)
        return replacement

    def _close_failed_source(self, source):
        source = self._required(source.request_id)
        context = self._context(source)
        if context.kind == "attach":
            for request_id in self._owned_cycle_ids(context):
                cycle = self._journal.load(request_id)
                if cycle is not None and cycle.phase not in _TERMINAL:
                    self._close_failed_source(cycle)
        if source.phase in _TERMINAL:
            return
        actions = tuple(
            JournalAction.model_validate(
                {
                    **item.model_dump(),
                    "phase": ActionPhase.ROLLED_BACK,
                    "diagnostic": (
                        "native_own_baseline_restored_delivery_unknown"
                        if item.phase is ActionPhase.UNCERTAIN
                        or item.diagnostic == _REPLY_WITHOUT_ACK
                        else "native_own_baseline_restored"
                    ),
                }
            )
            for item in source.actions
        )
        replacement = source.evolve(actions=actions, phase=TransactionPhase.ROLLED_BACK)
        self._journal.save(source, replacement)

    def _context(self, record):
        if record.native_context_sha256 is None:
            raise ValueError("native 소유 원본이 없는 기록입니다")
        context = NativeContext.model_validate_json(
            self._journal.load_blob(record.native_context_sha256)
        )
        targets = tuple(item.target for item in context.originals)
        self._validate_targets(context.plan, targets)
        expected_id = {
            "attach": context.attach_request_id,
            "detach": native_detach_request_id(context.attach_request_id),
            "detach_assist": native_assistance_request_id(context.attach_request_id),
            "detach_assist_split_v1": native_split_assistance_request_id(
                context.attach_request_id
            ),
            "master_link_cycle_v1": native_master_cycle_request_id(context.attach_request_id),
            "master_power_cycle_v1": native_master_power_cycle_request_id(
                context.attach_request_id
            ),
            "slave_timer_rearm_v1": native_slave_timer_rearm_request_id(context.attach_request_id),
        }[context.kind]
        if record.request_id != expected_id:
            raise ValueError("native 원본 소유 요청이 다릅니다")
        if context.kind in {
            "detach",
            "detach_assist",
            "detach_assist_split_v1",
            "master_link_cycle_v1",
            "master_power_cycle_v1",
            "slave_timer_rearm_v1",
        }:
            source = self._required(context.attach_request_id)
            source_context = self._context(source)
            same_originals = source_context.originals == context.originals
            stable_child_originals = len(source_context.originals) == len(
                context.originals
            ) and all(
                _same_stable_target(source_item.target, child_item.target)
                for source_item, child_item in zip(
                    source_context.originals, context.originals, strict=True
                )
            )
            if (
                source_context.kind != "attach"
                or source.native_context_sha256 != context.source_context_sha256
                or source_context.plan != context.plan
                or source_context.master_cycle_on_async_failure
                != context.master_cycle_on_async_failure
                or (
                    context.kind
                    in {
                        "master_power_cycle_v1",
                        "slave_timer_rearm_v1",
                        "detach_assist_split_v1",
                    }
                    and (
                        not stable_child_originals
                        or source_context.schema_sha256 != context.schema_sha256
                        or source_context.activation_sequence != context.activation_sequence
                    )
                )
                or (
                    context.kind
                    not in {
                        "master_power_cycle_v1",
                        "slave_timer_rearm_v1",
                        "detach_assist_split_v1",
                    }
                    and not same_originals
                )
            ):
                raise ValueError("자기 attach가 포착하지 않은 baseline 복원 금지")
        if context.kind != "master_power_cycle_v1" and (
            context.power_cycle_basis_sha256 is not None or context.power_off_seconds is not None
        ):
            raise ValueError("기존 native 기록에는 master 전원 cycle 범위를 넣지 않습니다")
        if context.kind == "detach_assist":
            if context.assisted_record_sha256 is None:
                raise ValueError("지원 전 미완료 기록 결속이 없습니다")
            stalled = LocalControlJournalRecord.model_validate_json(
                self._journal.load_blob(context.assisted_record_sha256)
            )
            if self._context(stalled).kind != "detach":
                raise ValueError("자기 detach 기록만 지원할 수 있습니다")
            self._require_stalled_first_slave(stalled, context)
        elif context.kind == "detach_assist_split_v1":
            from jebao_flow.local_control.wavemaker_detach_split import validate_detach_split

            validate_detach_split(self, record, context)
        elif context.kind == "master_link_cycle_v1":
            from jebao_flow.local_control.wavemaker_master_cycle import validate_cycle

            validate_cycle(self, record, context)
        elif context.kind == "master_power_cycle_v1":
            from jebao_flow.local_control.wavemaker_power_cycle import validate_power_cycle

            validate_power_cycle(self, record, context)
        elif context.kind == "slave_timer_rearm_v1":
            from jebao_flow.local_control.wavemaker_slave_rearm import validate_slave_rearm

            validate_slave_rearm(self, record, context)
        elif context.assisted_record_sha256 is not None:
            raise ValueError("기존 native 기록에는 지원 범위를 넣지 않습니다")
        for action in record.actions:
            original = next((o for o in context.originals if o.target == action.target), None)
            if original is None or action.baseline_status_sha256 != original.status_sha256:
                raise ValueError("native action의 원본 결속이 다릅니다")
        if context.kind not in {
            "master_power_cycle_v1",
            "slave_timer_rearm_v1",
            "detach_assist_split_v1",
        }:
            self._snapshots(context)
        return context

    def _snapshots(self, context):
        snapshots = {}
        for item in context.originals:
            capture = json.loads(self._journal.load_blob(item.capture_sha256))
            if capture["action"] != 3 or capture["status_sha256"] != item.status_sha256:
                raise ValueError("원본은 attach 전 reply capture여야 합니다")
            self._journal.load_blob(capture["wire_sha256"])
            snapshots[item.target.logical_member] = WavemakerRestoreSnapshot(
                context.attach_request_id,
                item.target,
                self._journal.load_blob(item.status_sha256),
            )
            controls = snapshots[item.target.logical_member].controls
            if controls["Mode"] not in LOCAL_WAVEMAKER_PRO_MODES:
                raise ValueError("원본 파형이 알려진 Pro 범위를 벗어납니다")
            if not (
                30 <= controls["Flow"] <= 100
                or (controls["Mode"] == "feed" and controls["Flow"] == 0)
            ):
                raise ValueError("원본 수동 Flow는 제품 범위 안이어야 합니다")
            for field, maximum in (("Frequency", 100), ("FeedTime", 60), ("Cust_Wav_Freq", 100)):
                if not 0 <= controls[field] <= maximum:
                    raise ValueError("원본 부가값이 제품 범위를 벗어납니다")
        return snapshots

    def _require_available(self, targets, *, allowed):
        keys = {target.physical_identity_key for target in targets}
        for record in self._journal.records():
            if record.request_id in allowed:
                continue
            if record.phase not in {
                TransactionPhase.VERIFIED,
                TransactionPhase.ROLLED_BACK,
                TransactionPhase.SUPERSEDED,
            }:
                if keys.intersection(
                    action.target.physical_identity_key for action in record.actions
                ):
                    raise LocalControlExecutionError("이 장비의 미완료 변경을 먼저 복구해야 합니다")
                continue
            if record.native_context_sha256 is not None:
                context = self._context(record)
                if context.kind != "attach" or not keys.intersection(
                    o.target.physical_identity_key for o in context.originals
                ):
                    continue
                detach = self._journal.load(native_detach_request_id(record.request_id))
                if detach is None or detach.phase is not TransactionPhase.VERIFIED:
                    raise LocalControlExecutionError(
                        "이 장비의 이전 결속 원본이 아직 해제되지 않았습니다"
                    )
                self._context(detach)

    def _qualify(self, request_id, plan, endpoints):
        functions = (
            "power",
            "schedule_enable",
            "linkage_role",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "feeding_duration",
            "custom_wave_frequency",
            "schedule",
        )
        for endpoint in endpoints:
            for function in functions:
                descriptor = self._catalog.find(endpoint.target.product_key, function)
                if not descriptor.control_exposed_for(endpoint.target.physical_identity_key):
                    raise ValueError(f"현재 자격이 없는 기능입니다: {function}")
                self._require_current_control_authority(descriptor, endpoint)
            # 준비의 정상 Flow/주파수도 제품 범위·step 검증을 그대로 거친다.
            for function, value in (
                ("manual_flow", plan.preparation_flow_for(endpoint.target.logical_member)),
                ("manual_frequency", plan.preparation_frequency),
            ):
                self._catalog.validate_intent(
                    LocalControlIntent(
                        request_id=request_id,
                        target=endpoint.target,
                        function=function,
                        value=value,
                    )
                )

    @staticmethod
    def _safe(plan, member=None):
        return {
            "SwitchON": True,
            "TimerON": False,
            "Linkage": "independent",
            "Mode": "constant",
            "Flow": plan.preparation_flow_for(member or plan.master),
            "Frequency": plan.preparation_frequency,
        }

    @staticmethod
    def _stable(target):
        return [
            target.logical_member,
            target.product_key,
            target.physical_identity_key,
            target.logical_role,
        ]

    def _validate_targets(self, plan, targets):
        names = (plan.master, *(item.logical_member for item in plan.slaves))
        if tuple(t.logical_member for t in targets) != names:
            raise ValueError("대상 순서는 계획의 master와 slave 목록이어야 합니다")
        if any(t.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY for t in targets):
            raise ValueError("native 실행기는 Pro 전용입니다")
        if len({t.physical_identity_key for t in targets}) != len(targets):
            raise ValueError("native 대상의 물리 신원이 중복됩니다")

    def _require_current_targets(self, context, targets):
        expected = tuple(o.target for o in context.originals)
        if len(targets) != len(expected) or any(
            not _same_stable_target(a, b) for a, b in zip(expected, targets, strict=True)
        ):
            raise ValueError("현재 설정의 신원이 자기 attach 원본과 다릅니다")
        if context.schema_sha256 != current_protocol_schema_sha256():
            raise ValueError(
                "원본 복원 계약의 source/schema가 변경되었습니다. 결속 당시 이미지를 사용하세요"
            )

    @staticmethod
    def _validate_endpoints(targets, endpoints):
        if tuple(e.target for e in endpoints) != targets:
            raise ValueError("실행 시점 discovery 결속이 요청한 대상과 다릅니다")

    def _required(self, request_id):
        record = self._journal.load(request_id)
        if record is None:
            raise KeyError("보존된 요청이 없습니다")
        return record

    def _public(self, record, *, current):
        context = self._context(record)
        awaiting = self._awaiting_operator(record, context)
        final = (
            None
            if record.native_final_sha256 is None
            else json.loads(self._journal.load_blob(record.native_final_sha256))
        )
        return {
            "request_id": record.request_id,
            "kind": context.kind,
            "phase": record.phase.value,
            "attach_request_id": context.attach_request_id,
            "detach_request_id": native_detach_request_id(context.attach_request_id),
            "assistance_request_id": native_assistance_request_id(context.attach_request_id),
            "split_assistance_request_id": native_split_assistance_request_id(
                context.attach_request_id
            ),
            **(
                {
                    "master_cycle_request_id": native_master_cycle_request_id(
                        context.attach_request_id
                    ),
                    "master_power_cycle_request_id": native_master_power_cycle_request_id(
                        context.attach_request_id
                    ),
                    "slave_timer_rearm_request_id": native_slave_timer_rearm_request_id(
                        context.attach_request_id
                    ),
                }
                if context.master_cycle_on_async_failure
                else {}
            ),
            "accepted": record.phase is TransactionPhase.VERIFIED,
            "current_state_verified": current,
            "physical_measurement": False,
            "journal_sha256": self._journal.record_digest(record),
            "context_sha256": record.native_context_sha256,
            "verification": final,
            **(
                {
                    "original_async_delivery": (
                        "ACKNOWLEDGED"
                        if self._required(context.attach_request_id)
                        .actions[-1]
                        .acknowledgement_sha256
                        is not None
                        else "UNKNOWN"
                    ),
                    "source_phase": self._required(context.attach_request_id).phase.value,
                    "acceptance_basis": "master_release_reply_then_rearm_pair_observation",
                    "automatic_restore": False,
                    "restoration_complete": False,
                    "restore_command": (
                        f"recover-wavemaker --request-id {context.attach_request_id}"
                    ),
                }
                if context.kind == "master_link_cycle_v1"
                else {}
            ),
            **(
                {
                    "original_async_delivery": (
                        "ACKNOWLEDGED"
                        if self._required(context.attach_request_id)
                        .actions[-1]
                        .acknowledgement_sha256
                        is not None
                        else "UNKNOWN"
                    ),
                    "source_phase": self._required(context.attach_request_id).phase.value,
                    "acceptance_basis": "owned_master_off_wait_on_then_pair_comparison",
                    "power_off_seconds": context.power_off_seconds,
                }
                if context.kind == "master_power_cycle_v1"
                else {}
            ),
            **(
                {
                    "source_phase": self._required(context.attach_request_id).phase.value,
                    "acceptance_basis": "image_free_slave_timer_manual_power_async_sequence",
                    "schedule_image_written": False,
                    "manual_fallback": "constant",
                }
                if context.kind == "slave_timer_rearm_v1"
                else {}
            ),
            **(
                {
                    "awaiting_operator_observation": True,
                    "observation_reason": _AWAIT_OPERATOR,
                    "automatic_restore": False,
                    "restoration_complete": False,
                    "next_action": "await_operator_instruction",
                    "restore_command": (
                        f"recover-wavemaker --request-id {context.attach_request_id}"
                    ),
                }
                if awaiting
                else {}
            ),
            "actions": [
                {
                    "action_id": a.action_id,
                    "member": a.target.logical_member,
                    "phase": a.phase.value,
                    "diagnostic": a.diagnostic,
                    "acknowledgement_received": a.acknowledgement_sha256 is not None,
                    "delivery": (
                        "ACKNOWLEDGED"
                        if a.acknowledgement_sha256 is not None
                        else "NOT_SENT"
                        if a.phase is ActionPhase.NO_CHANGE
                        else "UNKNOWN"
                    ),
                    "evidence_sha256": a.native_evidence_sha256,
                    "comparison": None
                    if a.native_evidence_sha256 is None
                    else json.loads(self._journal.load_blob(a.native_evidence_sha256))[
                        "comparison"
                    ],
                }
                for a in record.actions
            ],
        }
