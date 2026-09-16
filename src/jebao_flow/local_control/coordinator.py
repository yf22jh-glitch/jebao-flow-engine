"""Evidence-gated, journal-first coordination for local-control writes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jebao_flow.local_control.catalog import LocalControlCatalog, LocalFunctionDescriptor
from jebao_flow.local_control.device_writer import (
    AQUARIUM_PUMP_PRODUCT_KEY,
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
    SessionFactory,
    extract_current_pro_schedule_image,
    verify_prepared_baseline,
    verify_prepared_readback,
)
from jebao_flow.local_control.failure_diagnostics import failure_clock_ns, report_action_failure
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.lease import (
    GenerationGuardedLocalControlPermit,
    LocalControlLease,
    LocalControlPreSendSupersededError,
    LocalControlSendPermit,
)
from jebao_flow.local_control.models import (
    CapabilityStage,
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.qualification import qualified_physical_identity_keys
from jebao_flow.local_control.resolver import CurrentEndpointResolver, ResolvedEndpoint
from jebao_flow.local_control.schedule import (
    FreshMasterSchedulePlan,
    compile_fresh_slave_flow_update,
    compile_slave_schedules,
)
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode


class LocalControlExecutionError(RuntimeError):
    """A journalled transaction stopped before verified completion."""


class NativeRecoveryRequiredError(LocalControlExecutionError):
    """비밀 예외 문자열 대신 CLI에 노출할 검증된 요청 id와 복구 경로."""

    def __init__(self, request_id: str, detach_id: str | None) -> None:
        self.request_id, self.detach_id = request_id, detach_id
        super().__init__(
            f"native 역순 복구 금지: recover-wavemaker --request-id {request_id}; "
            f"원본 해제 요청 id: {detach_id}"
        )


class EndpointResolver(Protocol):
    async def resolve(self, target) -> ResolvedEndpoint: ...

    async def resolve_many(self, targets) -> tuple[ResolvedEndpoint, ...]: ...


WriterFactory = Callable[[ResolvedEndpoint], ConnectedDeviceWriter]
QualificationVerifier = Callable[[QualificationBinding], frozenset[str]]


@dataclass(frozen=True, slots=True)
class LocalControlResult:
    request_id: str
    phase: TransactionPhase
    revision: int
    action_count: int
    journal_sha256: str
    physical_write_count: int

    @property
    def accepted(self) -> bool:
        return self.phase is TransactionPhase.VERIFIED

    def public_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "phase": self.phase.value,
            "revision": self.revision,
            "action_count": self.action_count,
            "journal_sha256": self.journal_sha256,
            "physical_write_count": self.physical_write_count,
            "accepted": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class LocalControlBatchTarget:
    """Ordered scalar actions for one stable device inside one transaction."""

    target: LocalDeviceBinding
    intents: tuple[LocalControlIntent, ...]

    def __post_init__(self) -> None:
        if not self.intents:
            raise ValueError("batch targets require at least one scalar intent")
        if any(intent.target != self.target for intent in self.intents):
            raise ValueError("batch scalar intents must use their declared stable target")
        functions = tuple(intent.function for intent in self.intents)
        if len(functions) != len(set(functions)):
            raise ValueError("batch targets cannot repeat one scalar function")


class LocalControlCoordinator:
    """Run scalar and priority schedule transactions under the same durable contract."""

    def __init__(
        self,
        *,
        catalog: LocalControlCatalog,
        resolver: CurrentEndpointResolver,
        journal: LocalControlJournalStore,
        lease: LocalControlLease,
        session_factory: SessionFactory | None = None,
        writer_factory: WriterFactory | None = None,
        qualification_verifier: QualificationVerifier = qualified_physical_identity_keys,
    ) -> None:
        if session_factory is not None and writer_factory is not None:
            raise ValueError("choose either a session factory or a writer factory")
        self._catalog = catalog
        self._resolver: EndpointResolver = resolver
        self._journal = journal
        self._lease = lease
        self._qualification_verifier = qualification_verifier
        if writer_factory is not None:
            self._writer_factory = writer_factory
        elif session_factory is not None:
            self._writer_factory = lambda endpoint: ConnectedDeviceWriter(
                endpoint,
                session_factory=session_factory,
            )
        else:
            self._writer_factory = ConnectedDeviceWriter

    async def execute(self, intent: LocalControlIntent) -> LocalControlResult:
        """Execute one qualified scalar intent exactly once by request id."""

        intent_sha256 = _canonical_sha256(intent.model_dump(mode="json"))
        existing = self._existing_result(intent.request_id, intent_sha256)
        if existing is not None:
            return existing
        descriptor = self._catalog.validate_intent(intent)
        endpoint = await self._resolver.resolve(intent.target)
        self._require_current_qualification(descriptor, endpoint)

        with self._lease.acquire((intent.target.physical_identity_key,)) as active_lease:
            self._require_no_other_unfinished(
                intent.request_id, (intent.target.physical_identity_key,)
            )
            existing = self._existing_result(intent.request_id, intent_sha256)
            if existing is not None:
                return existing
            writer = self._writer_factory(endpoint)
            try:
                current = await writer.connect_and_read()
                prepared = (
                    writer.prepare_aquarium_schedule_save(intent, current)
                    if intent.target.product_key == AQUARIUM_PUMP_PRODUCT_KEY
                    and intent.function == "schedule"
                    else (writer.prepare_scalar(intent, current),)
                )
                record = self._create_record(
                    request_id=intent.request_id,
                    intent_sha256=intent_sha256,
                    prepared=prepared,
                )
                return await self._execute_prepared(
                    record,
                    tuple((writer, item) for item in prepared),
                    active_lease,
                )
            finally:
                await writer.close()

    async def execute_batch(
        self,
        *,
        request_id: str,
        intent_binding: object,
        lease_targets: tuple[LocalDeviceBinding, ...],
        targets: tuple[LocalControlBatchTarget, ...],
        pre_send_guard: Callable[[], bool] | None = None,
    ) -> LocalControlResult:
        """Execute one precomposed multi-device scalar transaction under one lease.

        Every endpoint and every component function is resolved and qualified before the first
        session is opened. All device baselines are then read before the journal is created and
        before any frame is sent. The caller composes only existing scalar intents; this method
        never combines their datapoints into a new control payload.
        """

        if not lease_targets:
            raise ValueError("batch execution requires at least one lease target")
        if not targets:
            raise ValueError("batch execution requires at least one action target")
        if pre_send_guard is not None and not callable(pre_send_guard):
            raise TypeError("batch pre-send guard must be callable")
        lease_logical = tuple(target.logical_member for target in lease_targets)
        lease_physical = tuple(target.physical_identity_key for target in lease_targets)
        if len(lease_logical) != len(set(lease_logical)):
            raise ValueError("batch lease targets must have distinct logical members")
        if len(lease_physical) != len(set(lease_physical)):
            raise ValueError("batch lease targets must have distinct physical identities")
        batch_physical = tuple(item.target.physical_identity_key for item in targets)
        if len(batch_physical) != len(set(batch_physical)):
            raise ValueError("batch action targets must have distinct physical identities")
        if not set(batch_physical) <= set(lease_physical):
            raise ValueError("every batch action target must be covered by the group lease")
        lease_target_by_key = {target.physical_identity_key: target for target in lease_targets}
        if any(
            lease_target_by_key[item.target.physical_identity_key] != item.target
            for item in targets
        ):
            raise ValueError("batch action target does not match its complete group lease binding")
        if any(intent.request_id != request_id for item in targets for intent in item.intents):
            raise ValueError("every batch scalar intent must use the group request id")

        intent_sha256 = _canonical_sha256(intent_binding)
        existing = self._existing_result(request_id, intent_sha256)
        if existing is not None:
            return existing

        endpoints = await self._resolver.resolve_many(tuple(item.target for item in targets))
        endpoint_by_key: dict[str, ResolvedEndpoint] = {}
        for endpoint in endpoints:
            key = endpoint.target.physical_identity_key
            if key in endpoint_by_key:
                raise ValueError("current batch endpoints must have distinct identities")
            endpoint_by_key[key] = endpoint
        if set(endpoint_by_key) != set(batch_physical):
            raise ValueError("current batch endpoints do not match every action target")
        batch_target_by_key = {item.target.physical_identity_key: item.target for item in targets}
        if any(
            endpoint_by_key[key].target != target for key, target in batch_target_by_key.items()
        ):
            raise ValueError("current batch endpoint changed its complete stable binding")

        for item in targets:
            endpoint = endpoint_by_key[item.target.physical_identity_key]
            if endpoint.target != item.target:
                raise ValueError("batch action target does not match the current endpoint")
            for intent in item.intents:
                descriptor = self._catalog.validate_intent(intent)
                self._require_current_qualification(descriptor, endpoint)

        with self._lease.acquire(lease_physical) as active_lease:
            self._require_no_other_unfinished(request_id, lease_physical)
            existing = self._existing_result(request_id, intent_sha256)
            if existing is not None:
                return existing

            writers: dict[str, ConnectedDeviceWriter] = {}
            prepared_pairs: list[tuple[ConnectedDeviceWriter, PreparedDeviceWrite]] = []
            try:
                current_statuses: dict[str, bytes] = {}
                for item in targets:
                    key = item.target.physical_identity_key
                    writer = self._writer_factory(endpoint_by_key[key])
                    writers[key] = writer
                    current_statuses[key] = await writer.connect_and_read()
                for item in targets:
                    key = item.target.physical_identity_key
                    writer = writers[key]
                    current = current_statuses[key]
                    for intent in item.intents:
                        frames = (
                            writer.prepare_aquarium_schedule_save(intent, current)
                            if intent.target.product_key == AQUARIUM_PUMP_PRODUCT_KEY
                            and intent.function == "schedule"
                            else (writer.prepare_scalar(intent, current),)
                        )
                        prepared_pairs.extend((writer, frame) for frame in frames)
                record = self._create_record(
                    request_id=request_id,
                    intent_sha256=intent_sha256,
                    prepared=tuple(prepared for _, prepared in prepared_pairs),
                )
                send_permit = (
                    active_lease
                    if pre_send_guard is None
                    else GenerationGuardedLocalControlPermit(active_lease, pre_send_guard)
                )
                return await self._execute_prepared(
                    record,
                    tuple(prepared_pairs),
                    send_permit,
                )
            finally:
                for writer in reversed(tuple(writers.values())):
                    await writer.close()

    async def execute_schedule(
        self,
        request_id: str,
        plan: FreshMasterSchedulePlan,
    ) -> LocalControlResult:
        """Copy the fresh master image to every slave and apply only explicit slot Flow changes."""

        intent_sha256 = _canonical_sha256(
            {
                "request_id": request_id,
                "function": "slave_per_slot_flow_extension",
                **plan.public_dict(),
            }
        )
        existing = self._existing_result(request_id, intent_sha256)
        if existing is not None:
            return existing
        descriptor = self._catalog.find(
            plan.master.product_key,
            "slave_per_slot_flow_extension",
        )
        if descriptor.stage is not CapabilityStage.QUALIFIED:
            raise ValueError("slave per-slot Flow is not qualified for hardware control")

        targets = (plan.master, *(slave.target for slave in plan.slaves))
        endpoints = await self._resolver.resolve_many(targets)
        master_endpoint, *slave_endpoints = endpoints
        for endpoint in slave_endpoints:
            self._require_current_qualification(descriptor, endpoint)

        physical_keys = tuple(target.physical_identity_key for target in targets)
        with self._lease.acquire(physical_keys) as active_lease:
            self._require_no_other_unfinished(request_id, physical_keys)
            existing = self._existing_result(request_id, intent_sha256)
            if existing is not None:
                return existing

            master_writer = self._writer_factory(master_endpoint)
            slave_writers: dict[str, ConnectedDeviceWriter] = {}
            prepared_pairs: list[tuple[ConnectedDeviceWriter, PreparedDeviceWrite]] = []
            try:
                master_status = await master_writer.connect_and_read()
                master_image = extract_current_pro_schedule_image(master_status)
                current_slave_statuses: dict[str, bytes] = {}
                current_slave_images: dict[str, bytes] = {}
                for endpoint in slave_endpoints:
                    key = endpoint.target.physical_identity_key
                    if key in slave_writers:
                        raise ValueError("current slave endpoints must have distinct identities")
                    writer = self._writer_factory(endpoint)
                    slave_writers[key] = writer
                    current = await writer.connect_and_read()
                    current_slave_statuses[key] = current
                    current_slave_images[key] = extract_current_pro_schedule_image(current)
                compiled = compile_slave_schedules(
                    plan,
                    master_image,
                    current_slave_images,
                )
                for schedule in compiled:
                    key = schedule.physical_identity_key
                    writer = slave_writers[key]
                    current = current_slave_statuses[key]
                    prepared_pairs.append((writer, writer.prepare_pro_schedule(schedule, current)))
                record = self._create_record(
                    request_id=request_id,
                    intent_sha256=intent_sha256,
                    prepared=tuple(prepared for _, prepared in prepared_pairs),
                )
                return await self._execute_prepared(
                    record,
                    tuple(prepared_pairs),
                    active_lease,
                )
            finally:
                for writer in reversed(tuple(slave_writers.values())):
                    await writer.close()
                await master_writer.close()

    async def execute_schedule_flow(
        self,
        *,
        request_id: str,
        linkage_mode: NativeLinkageMode,
        master: LocalDeviceBinding,
        slave: LocalDeviceBinding,
        slot_index: int,
        flow: int,
    ) -> LocalControlResult:
        """Set one slave slot Flow from fresh master and fresh slave schedule images."""

        intent_sha256 = _canonical_sha256(
            {
                "request_id": request_id,
                "function": "slave_per_slot_flow_extension",
                "linkage_mode": linkage_mode.value,
                "master": master.model_dump(mode="json"),
                "slave": slave.model_dump(mode="json"),
                "slot_index": slot_index,
                "flow": flow,
            }
        )
        existing = self._existing_result(request_id, intent_sha256)
        if existing is not None:
            return existing
        descriptor = self._catalog.find(
            master.product_key,
            "slave_per_slot_flow_extension",
        )
        if descriptor.stage is not CapabilityStage.QUALIFIED:
            raise ValueError("slave per-slot Flow is not qualified for hardware control")
        endpoints = await self._resolver.resolve_many((master, slave))
        master_endpoint, slave_endpoint = endpoints
        self._require_current_qualification(descriptor, slave_endpoint)

        with self._lease.acquire(
            (master.physical_identity_key, slave.physical_identity_key)
        ) as active_lease:
            self._require_no_other_unfinished(
                request_id, (master.physical_identity_key, slave.physical_identity_key)
            )
            existing = self._existing_result(request_id, intent_sha256)
            if existing is not None:
                return existing
            master_writer = self._writer_factory(master_endpoint)
            slave_writer = self._writer_factory(slave_endpoint)
            try:
                master_status = await master_writer.connect_and_read()
                slave_status = await slave_writer.connect_and_read()
                compiled = compile_fresh_slave_flow_update(
                    linkage_mode=linkage_mode,
                    master=master,
                    slave=slave,
                    current_master_image=extract_current_pro_schedule_image(master_status),
                    current_slave_image=extract_current_pro_schedule_image(slave_status),
                    slot_index=slot_index,
                    flow=flow,
                )
                prepared = slave_writer.prepare_pro_schedule(compiled, slave_status)
                record = self._create_record(
                    request_id=request_id,
                    intent_sha256=intent_sha256,
                    prepared=(prepared,),
                )
                return await self._execute_prepared(
                    record,
                    ((slave_writer, prepared),),
                    active_lease,
                )
            finally:
                await slave_writer.close()
                await master_writer.close()

    async def recover(self, request_id: str) -> LocalControlResult:
        """Restore fresh pre-write baselines without replaying a forward frame."""

        record = self._journal.load(request_id)
        if record is None:
            raise KeyError("local-control transaction does not exist")
        if record.native_context_sha256 is not None:
            from jebao_flow.local_control.journal import native_detach_request_id

            detach_id = None
            try:
                context = json.loads(self._journal.load_blob(record.native_context_sha256))
                owner = context["attach_request_id"]
                # 손상된 blob의 임의 문자열을 운영자 출력으로 올리지 않는다.
                if (
                    isinstance(owner, str)
                    and 8 <= len(owner) <= 128
                    and all(
                        character
                        in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                        for character in owner
                    )
                ):
                    detach_id = native_detach_request_id(owner)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                pass
            raise NativeRecoveryRequiredError(request_id, detach_id)
        if record.phase is TransactionPhase.VERIFIED:
            raise LocalControlExecutionError(
                "verified transaction is terminal and does not require recovery"
            )
        if record.phase is TransactionPhase.SUPERSEDED:
            raise LocalControlExecutionError(
                "superseded transaction is terminal and must not be rolled back"
            )
        if record.phase is TransactionPhase.ROLLED_BACK:
            return self._result(record, physical_write_count=0)

        if all(action.recovery_policy == "observe_clock" for action in record.actions):
            return await self._observe_clock_recovery(record)

        unique_targets = tuple(
            {
                action.target.physical_identity_key: action.target for action in record.actions
            }.values()
        )
        endpoints = await self._resolver.resolve_many(unique_targets)
        endpoint_by_key = {
            endpoint.target.physical_identity_key: endpoint for endpoint in endpoints
        }
        keys = tuple(endpoint_by_key)
        writes = 0
        with self._lease.acquire(keys) as active_lease:
            self._require_no_other_unfinished(request_id, keys)
            current_record = self._journal.load(request_id)
            if current_record != record:
                raise LocalControlExecutionError("journal changed before recovery")
            started = any(
                action.phase not in {ActionPhase.PREPARED, ActionPhase.NO_CHANGE}
                for action in record.actions
            )
            order = record.recovery_order or tuple(reversed(range(len(record.actions))))
            for index in order:
                action = record.actions[index]
                if action.phase is ActionPhase.NO_CHANGE:
                    continue
                if action.phase is ActionPhase.PREPARED and not (
                    started and index in record.recovery_prerequisites
                ):
                    record = self._save_action(
                        record,
                        index,
                        phase=ActionPhase.ROLLED_BACK,
                        diagnostic="forward_never_attempted",
                    )
                    continue
                endpoint = endpoint_by_key[action.target.physical_identity_key]
                writer = self._writer_factory(endpoint)
                try:
                    if not action.restore_on_recovery:
                        record = await self._retain_safe_state_or_baseline(
                            record,
                            index,
                            action,
                            endpoint,
                            writer,
                        )
                        continue
                    prepared = self._recovery_prepared(action, endpoint)
                    current_status = await writer.connect_and_read()
                    if verify_prepared_readback(prepared, current_status):
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.ROLLED_BACK,
                            diagnostic="baseline_already_present",
                        )
                        continue
                    if action.phase in {
                        ActionPhase.ROLLBACK_ATTEMPTING,
                        ActionPhase.ROLLBACK_UNCERTAIN,
                        ActionPhase.ROLLED_BACK,
                    }:
                        observed_digest = self._journal.install_blob(current_status)
                        record = self._save_action(
                            record,
                            index,
                            phase=action.phase,
                            diagnostic="rollback_replay_refused." + observed_digest,
                            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                        )
                        raise LocalControlExecutionError(
                            f"previous rollback is {action.phase.value}; current state differs; "
                            f"it was not resent (status_sha256={observed_digest})"
                        )
                    record = self._save_action(
                        record,
                        index,
                        phase=ActionPhase.ROLLBACK_ATTEMPTING,
                    )
                    started_ns = failure_clock_ns()
                    try:
                        acknowledgement = await writer.send_once(
                            prepared.forward_payload,
                            active_lease,
                        )
                        writes += 1
                        acknowledgement_sha256 = self._journal.install_blob(acknowledgement)
                        readback = await writer.readback(active_lease)
                    except BaseException as error:
                        failed_ns = failure_clock_ns()
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.ROLLBACK_UNCERTAIN,
                            diagnostic="rollback_outcome_uncertain",
                            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                        )
                        report_action_failure(
                            record, index, error, started_ns=started_ns, failed_ns=failed_ns
                        )
                        raise LocalControlExecutionError(
                            "rollback outcome is uncertain; forward write was not retried"
                        ) from error
                    if not verify_prepared_readback(prepared, readback):
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.ROLLBACK_UNCERTAIN,
                            diagnostic="rollback_readback_mismatch",
                            acknowledgement_sha256=acknowledgement_sha256,
                            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                        )
                        raise LocalControlExecutionError("rollback readback did not match baseline")
                    record = self._save_action(
                        record,
                        index,
                        phase=ActionPhase.ROLLED_BACK,
                        acknowledgement_sha256=acknowledgement_sha256,
                    )
                finally:
                    await writer.close()
            record = self._save_transaction_phase(record, TransactionPhase.ROLLED_BACK)
            return self._result(record, physical_write_count=writes)

    async def _observe_clock_recovery(self, record):
        """Read-only reconciliation: no retry and no historical clock restoration."""
        targets = tuple(
            {
                action.target.physical_identity_key: action.target for action in record.actions
            }.values()
        )
        endpoints = await self._resolver.resolve_many(targets)
        by_key = {endpoint.target.physical_identity_key: endpoint for endpoint in endpoints}
        with self._lease.acquire(tuple(by_key)):
            if self._journal.load(record.request_id) != record:
                raise LocalControlExecutionError("clock operation changed before observation")
            for index, action in enumerate(record.actions):
                if action.phase is ActionPhase.PREPARED:
                    record = self._save_action(
                        record, index, phase=ActionPhase.NO_CHANGE, diagnostic="clock_never_sent"
                    )
                    continue
                if action.phase is ActionPhase.NO_CHANGE:
                    continue
                endpoint = by_key[action.target.physical_identity_key]
                writer = self._writer_factory(endpoint)
                try:
                    status = await writer.connect_and_read()
                    prepared = self._forward_prepared(action, endpoint)
                    if not verify_prepared_readback(prepared, status):
                        digest = self._journal.install_blob(status)
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.UNCERTAIN,
                            diagnostic="clock_observed_" + digest,
                            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                        )
                        raise LocalControlExecutionError(
                            "clock setting remains unverified; no clock command was resent"
                        )
                    record = self._save_action(
                        record,
                        index,
                        phase=ActionPhase.VERIFIED,
                        diagnostic="advancing_clock_observed",
                    )
                finally:
                    await writer.close()
            record = self._save_transaction_phase(record, TransactionPhase.VERIFIED)
            return self._result(record, physical_write_count=0)

    async def _retain_safe_state_or_baseline(
        self,
        record: LocalControlJournalRecord,
        index: int,
        action: JournalAction,
        endpoint: ResolvedEndpoint,
        writer: ConnectedDeviceWriter,
    ) -> LocalControlJournalRecord:
        """Classify a safer forward state without ever sending its rollback payload."""

        prepared = self._forward_prepared(action, endpoint)
        try:
            current_status = await writer.connect_and_read()
            forward_is_present = verify_prepared_readback(prepared, current_status)
            baseline_is_present = (
                False if forward_is_present else verify_prepared_baseline(prepared, current_status)
            )
        except BaseException as error:
            self._save_action(
                record,
                index,
                phase=ActionPhase.UNCERTAIN,
                diagnostic="safe_state_readback_unavailable",
                transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
            )
            raise LocalControlExecutionError(
                "safe-state recovery readback is unavailable; no frame was sent"
            ) from error
        if forward_is_present:
            return self._save_action(
                record,
                index,
                phase=ActionPhase.ROLLED_BACK,
                diagnostic="safe_state_retained",
            )
        if baseline_is_present:
            return self._save_action(
                record,
                index,
                phase=ActionPhase.ROLLED_BACK,
                diagnostic="baseline_already_present",
            )
        self._save_action(
            record,
            index,
            phase=ActionPhase.UNCERTAIN,
            diagnostic="safe_state_readback_mismatch",
            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
        )
        raise LocalControlExecutionError(
            "safe-state recovery readback matched neither intent nor baseline; no frame was sent"
        )

    async def _execute_prepared(
        self,
        record: LocalControlJournalRecord,
        pairs: tuple[tuple[ConnectedDeviceWriter, PreparedDeviceWrite], ...],
        lease: LocalControlSendPermit,
    ) -> LocalControlResult:
        writes = 0
        for index, (writer, prepared) in enumerate(pairs):
            if prepared.no_change:
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.NO_CHANGE,
                    diagnostic="fresh_readback_already_matches",
                    transaction_phase=TransactionPhase.EXECUTING,
                )
                continue
            record = self._save_action(
                record,
                index,
                phase=ActionPhase.FORWARD_ATTEMPTING,
                transaction_phase=TransactionPhase.EXECUTING,
            )
            started_ns = failure_clock_ns()
            try:
                acknowledgement = await writer.send_once(prepared.forward_payload, lease)
                writes += 1
            except LocalControlPreSendSupersededError:
                record = self._terminalize_superseded(record, index)
                return self._result(record, physical_write_count=writes)
            except BaseException as error:
                failed_ns = failure_clock_ns()
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.UNCERTAIN,
                    diagnostic="forward_outcome_uncertain",
                    transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                )
                report_action_failure(
                    record, index, error, started_ns=started_ns, failed_ns=failed_ns
                )
                raise LocalControlExecutionError(
                    "forward outcome is uncertain; the control frame will not be retried"
                ) from error

            acknowledgement_sha256 = self._journal.install_blob(acknowledgement)
            record = self._save_action(
                record,
                index,
                phase=ActionPhase.FORWARD_ACKNOWLEDGED,
                acknowledgement_sha256=acknowledgement_sha256,
            )
            started_ns = failure_clock_ns()
            try:
                readback = await writer.readback(lease)
            except BaseException as error:
                failed_ns = failure_clock_ns()
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.UNCERTAIN,
                    diagnostic="forward_readback_unavailable",
                    transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                )
                report_action_failure(
                    record, index, error, started_ns=started_ns, failed_ns=failed_ns
                )
                raise LocalControlExecutionError("forward readback is unavailable") from error
            if not verify_prepared_readback(prepared, readback):
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.UNCERTAIN,
                    diagnostic="forward_readback_mismatch",
                    transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                )
                raise LocalControlExecutionError("forward readback did not match the exact intent")
            record = self._save_action(
                record,
                index,
                phase=ActionPhase.VERIFIED,
                acknowledgement_sha256=acknowledgement_sha256,
            )
        record = self._save_transaction_phase(record, TransactionPhase.VERIFIED)
        return self._result(record, physical_write_count=writes)

    def _terminalize_superseded(
        self,
        record: LocalControlJournalRecord,
        current_index: int,
    ) -> LocalControlJournalRecord:
        """Persist a known-not-sent remainder without rolling back verified actions."""

        actions = list(record.actions)
        if actions[current_index].phase is not ActionPhase.FORWARD_ATTEMPTING:
            raise LocalControlExecutionError(
                "superseded action was not at the guarded pre-send boundary"
            )
        if any(
            action.phase not in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}
            for action in actions[:current_index]
        ):
            raise LocalControlExecutionError(
                "superseded transaction has an unresolved earlier action"
            )
        if any(action.phase is not ActionPhase.PREPARED for action in actions[current_index + 1 :]):
            raise LocalControlExecutionError("superseded transaction has an advanced later action")
        for index in range(current_index, len(actions)):
            action = actions[index]
            actions[index] = JournalAction.model_validate(
                {
                    **action.model_dump(),
                    "phase": ActionPhase.SUPERSEDED,
                    "diagnostic": (
                        "generation_guard_rejected_before_send"
                        if index == current_index
                        else "generation_superseded_before_attempt"
                    ),
                }
            )
        replacement = record.evolve(
            phase=TransactionPhase.SUPERSEDED,
            actions=tuple(actions),
        )
        self._journal.save(record, replacement)
        return replacement

    def _create_record(
        self,
        *,
        request_id: str,
        intent_sha256: str,
        prepared: tuple[PreparedDeviceWrite, ...],
        native_context_sha256: str | None = None,
    ) -> LocalControlJournalRecord:
        now = datetime.now(UTC)
        actions: list[JournalAction] = []
        for index, item in enumerate(prepared):
            actions.append(
                JournalAction(
                    action_id=f"action_{index:02d}",
                    target=item.target.target,
                    function=item.function,
                    forward_payload_sha256=self._journal.install_blob(item.forward_payload),
                    rollback_payload_sha256=self._journal.install_blob(item.rollback_payload),
                    baseline_status_sha256=self._journal.install_blob(item.baseline_status),
                    expected_readback_sha256=self._journal.install_blob(item.expected_readback),
                    baseline_readback_sha256=self._journal.install_blob(item.baseline_readback),
                    verification_kind=item.verification_kind,
                    restore_on_recovery=item.restore_on_recovery,
                    recovery_policy=item.recovery_policy,
                )
            )
        record = LocalControlJournalRecord(
            request_id=request_id,
            intent_sha256=intent_sha256,
            created_at=now,
            updated_at=now,
            actions=tuple(actions),
            native_context_sha256=native_context_sha256,
        )
        self._journal.create(record)
        return record

    def _save_action(
        self,
        record: LocalControlJournalRecord,
        index: int,
        *,
        phase: ActionPhase,
        diagnostic: str | None = None,
        acknowledgement_sha256: str | None = None,
        transaction_phase: TransactionPhase | None = None,
    ) -> LocalControlJournalRecord:
        actions = list(record.actions)
        action = actions[index]
        actions[index] = JournalAction.model_validate(
            {
                **action.model_dump(),
                "phase": phase,
                "diagnostic": diagnostic,
                "acknowledgement_sha256": (
                    action.acknowledgement_sha256
                    if acknowledgement_sha256 is None
                    else acknowledgement_sha256
                ),
            }
        )
        replacement = record.evolve(
            phase=record.phase if transaction_phase is None else transaction_phase,
            actions=tuple(actions),
        )
        self._journal.save(record, replacement)
        return replacement

    def _save_transaction_phase(
        self,
        record: LocalControlJournalRecord,
        phase: TransactionPhase,
    ) -> LocalControlJournalRecord:
        replacement = record.evolve(phase=phase)
        self._journal.save(record, replacement)
        return replacement

    def _recovery_prepared(
        self,
        action: JournalAction,
        endpoint: ResolvedEndpoint,
    ) -> PreparedDeviceWrite:
        if action.recovery_policy == "observe_clock":
            raise LocalControlExecutionError("clock recovery never constructs a sendable rollback")
        rollback = self._journal.load_blob(action.rollback_payload_sha256)
        return PreparedDeviceWrite(
            target=endpoint,
            function=action.function,
            forward_payload=rollback,
            rollback_payload=rollback,
            baseline_status=self._journal.load_blob(action.baseline_status_sha256),
            expected_readback=self._journal.load_blob(action.baseline_readback_sha256),
            baseline_readback=self._journal.load_blob(action.baseline_readback_sha256),
            verification_kind=action.verification_kind,
            no_change=False,
        )

    def _forward_prepared(
        self,
        action: JournalAction,
        endpoint: ResolvedEndpoint,
    ) -> PreparedDeviceWrite:
        """Rehydrate the original orientation for safe-state and baseline classification."""

        return PreparedDeviceWrite(
            target=endpoint,
            function=action.function,
            forward_payload=self._journal.load_blob(action.forward_payload_sha256),
            rollback_payload=self._journal.load_blob(action.rollback_payload_sha256),
            baseline_status=self._journal.load_blob(action.baseline_status_sha256),
            expected_readback=self._journal.load_blob(action.expected_readback_sha256),
            baseline_readback=self._journal.load_blob(action.baseline_readback_sha256),
            verification_kind=action.verification_kind,
            no_change=False,
            restore_on_recovery=action.restore_on_recovery,
            recovery_policy=action.recovery_policy,
        )

    def _existing_result(self, request_id: str, intent_sha256: str) -> LocalControlResult | None:
        record = self._journal.load(request_id)
        if record is None:
            return None
        if record.intent_sha256 != intent_sha256:
            raise LocalControlExecutionError("request id is already bound to a different intent")
        if record.phase in {
            TransactionPhase.VERIFIED,
            TransactionPhase.ROLLED_BACK,
            TransactionPhase.SUPERSEDED,
        }:
            return self._result(record, physical_write_count=0)
        raise LocalControlExecutionError("request has unfinished durable state; recover it first")

    def request_has_no_durable_state(self, request_id: str) -> bool:
        """Return whether a failed call definitely stopped before journal creation.

        A port adapter may classify only this state as retryable ``NOT_SENT``. Once a journal
        exists, even a PREPARED record must be reconciled through its durable operation rather
        than retried under a new runtime assumption.
        """

        return self._journal.load(request_id) is None

    def _require_no_other_unfinished(self, request_id: str, physical_keys) -> None:
        if any(
            record.request_id != request_id
            for record in self._journal.unfinished_for(physical_keys)
        ):
            raise LocalControlExecutionError(
                "this physical device has an unfinished change requiring terminal recovery"
            )

    def _require_current_qualification(
        self,
        descriptor: LocalFunctionDescriptor,
        endpoint: ResolvedEndpoint,
    ) -> None:
        qualification = descriptor.qualification
        if qualification is None or descriptor.stage is not CapabilityStage.QUALIFIED:
            raise ValueError("hardware control lacks a qualification binding")
        qualified_keys = self._qualification_verifier(qualification)
        if endpoint.target.physical_identity_key not in qualified_keys:
            raise ValueError("qualification receipts do not bind this physical device")
        if qualification.schema_sha256 != current_protocol_schema_sha256():
            raise ValueError("qualification does not bind the currently installed protocol bytes")
        current_versions = {
            value
            for value in (endpoint.wifi_firmware_version, endpoint.gizwits_version)
            if value is not None
        }
        if qualification.firmware_version not in current_versions:
            raise ValueError("qualification does not bind the current discovered firmware")

    def _result(
        self,
        record: LocalControlJournalRecord,
        *,
        physical_write_count: int,
    ) -> LocalControlResult:
        return LocalControlResult(
            request_id=record.request_id,
            phase=record.phase,
            revision=record.revision,
            action_count=len(record.actions),
            journal_sha256=self._journal.record_digest(record),
            physical_write_count=physical_write_count,
        )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "LocalControlBatchTarget",
    "LocalControlCoordinator",
    "LocalControlExecutionError",
    "LocalControlResult",
]
