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
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
    SessionFactory,
    extract_current_pro_schedule_image,
    verify_prepared_readback,
)
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.lease import ActiveLocalControlLease, LocalControlLease
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
            self._require_no_other_unfinished(intent.request_id)
            existing = self._existing_result(intent.request_id, intent_sha256)
            if existing is not None:
                return existing
            writer = self._writer_factory(endpoint)
            try:
                current = await writer.connect_and_read()
                prepared = writer.prepare_scalar(intent, current)
                record = self._create_record(
                    request_id=intent.request_id,
                    intent_sha256=intent_sha256,
                    prepared=(prepared,),
                )
                return await self._execute_prepared(
                    record,
                    ((writer, prepared),),
                    active_lease,
                )
            finally:
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
            self._require_no_other_unfinished(request_id)
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
            self._require_no_other_unfinished(request_id)
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
        if record.phase is TransactionPhase.ROLLED_BACK:
            return self._result(record, physical_write_count=0)

        unique_targets = tuple(
            {
                action.target.physical_identity_key: action.target
                for action in record.actions
            }.values()
        )
        endpoints = await self._resolver.resolve_many(unique_targets)
        endpoint_by_key = {
            endpoint.target.physical_identity_key: endpoint for endpoint in endpoints
        }
        keys = tuple(endpoint_by_key)
        writes = 0
        with self._lease.acquire(keys) as active_lease:
            self._require_no_other_unfinished(request_id)
            current_record = self._journal.load(request_id)
            if current_record != record:
                raise LocalControlExecutionError("journal changed before recovery")
            for index in reversed(range(len(record.actions))):
                action = record.actions[index]
                if action.phase is ActionPhase.NO_CHANGE:
                    continue
                if action.phase is ActionPhase.PREPARED:
                    record = self._save_action(
                        record,
                        index,
                        phase=ActionPhase.ROLLED_BACK,
                        diagnostic="forward_never_attempted",
                    )
                    continue
                endpoint = endpoint_by_key[action.target.physical_identity_key]
                writer = self._writer_factory(endpoint)
                prepared = self._recovery_prepared(action, endpoint)
                try:
                    current_status = await writer.connect_and_read()
                    if verify_prepared_readback(prepared, current_status):
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.ROLLED_BACK,
                            diagnostic="baseline_already_present",
                        )
                        continue
                    record = self._save_action(
                        record,
                        index,
                        phase=ActionPhase.ROLLBACK_ATTEMPTING,
                    )
                    try:
                        acknowledgement = await writer.send_once(
                            prepared.forward_payload,
                            active_lease,
                        )
                        writes += 1
                        acknowledgement_sha256 = self._journal.install_blob(acknowledgement)
                        readback = await writer.readback(active_lease)
                    except BaseException as error:
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.ROLLBACK_UNCERTAIN,
                            diagnostic="rollback_outcome_uncertain",
                            transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
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

    async def _execute_prepared(
        self,
        record: LocalControlJournalRecord,
        pairs: tuple[tuple[ConnectedDeviceWriter, PreparedDeviceWrite], ...],
        lease: ActiveLocalControlLease,
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
            try:
                acknowledgement = await writer.send_once(prepared.forward_payload, lease)
                writes += 1
            except BaseException as error:
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.UNCERTAIN,
                    diagnostic="forward_outcome_uncertain",
                    transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
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
            try:
                readback = await writer.readback(lease)
            except BaseException as error:
                record = self._save_action(
                    record,
                    index,
                    phase=ActionPhase.UNCERTAIN,
                    diagnostic="forward_readback_unavailable",
                    transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
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

    def _create_record(
        self,
        *,
        request_id: str,
        intent_sha256: str,
        prepared: tuple[PreparedDeviceWrite, ...],
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
                )
            )
        record = LocalControlJournalRecord(
            request_id=request_id,
            intent_sha256=intent_sha256,
            created_at=now,
            updated_at=now,
            actions=tuple(actions),
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

    def _existing_result(self, request_id: str, intent_sha256: str) -> LocalControlResult | None:
        record = self._journal.load(request_id)
        if record is None:
            return None
        if record.intent_sha256 != intent_sha256:
            raise LocalControlExecutionError("request id is already bound to a different intent")
        if record.phase in {TransactionPhase.VERIFIED, TransactionPhase.ROLLED_BACK}:
            return self._result(record, physical_write_count=0)
        raise LocalControlExecutionError("request has unfinished durable state; recover it first")

    def _require_no_other_unfinished(self, request_id: str) -> None:
        if any(
            record.request_id != request_id
            for record in self._journal.unfinished_records()
        ):
            raise LocalControlExecutionError(
                "another local-control transaction requires terminal recovery"
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
    "LocalControlCoordinator",
    "LocalControlExecutionError",
    "LocalControlResult",
]
