"""Forward-only drill for proving one real local-control rollback frame.

The drill changes no production coordinator behavior.  It wraps one normal connected writer,
lets the real 40-to-39 write and exact production readback verification complete, and then raises
one designated readback fault.  Recovery is deliberately absent from this module and must run in
a separate process with the existing production recovery CLI.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast

from jebao_flow.local_control.catalog import LocalControlCatalog
from jebao_flow.local_control.coordinator import (
    LocalControlCoordinator,
    LocalControlExecutionError,
    QualificationVerifier,
    WriterFactory,
)
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
    verify_prepared_readback,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.lease import LocalControlLease, LocalControlSendPermit
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.qualification import qualified_physical_identity_keys
from jebao_flow.local_control.resolver import CurrentEndpointResolver, ResolvedEndpoint

_FUNCTION = "manual_flow"
_BASELINE_FLOW = 40
_FORWARD_FLOW = 39
_EXPECTED_FAMILY = "local_wavemaker_pro"


class LocalControlRecoveryDrillError(RuntimeError):
    """The bounded forward phase did not reach its exact planned durable state."""


class DesignatedForwardReadbackFault(RuntimeError):
    """The real forward readback matched before this one deliberate interruption."""


class DeviceWriter(Protocol):
    async def connect_and_read(self) -> bytes: ...

    def prepare_scalar(
        self,
        intent: LocalControlIntent,
        current_status: bytes,
    ) -> PreparedDeviceWrite: ...

    async def send_once(self, payload: bytes, lease: LocalControlSendPermit) -> bytes: ...

    async def readback(self, lease: LocalControlSendPermit) -> bytes: ...

    async def close(self) -> None: ...


DeviceWriterFactory = Callable[[ResolvedEndpoint], DeviceWriter]


@dataclass(slots=True)
class RecoveryReadbackFaultWriter:
    """Instance-local delegate that faults only after the production predicate succeeds."""

    delegate: DeviceWriter = field(repr=False)
    prepared: PreparedDeviceWrite | None = field(default=None, init=False, repr=False)
    acknowledgement: bytes | None = field(default=None, init=False, repr=False)
    verified_readback: bytes | None = field(default=None, init=False, repr=False)
    completed_send_count: int = field(default=0, init=False)
    injected_fault_count: int = field(default=0, init=False)
    close_count: int = field(default=0, init=False)

    async def connect_and_read(self) -> bytes:
        return await self.delegate.connect_and_read()

    def prepare_scalar(
        self,
        intent: LocalControlIntent,
        current_status: bytes,
    ) -> PreparedDeviceWrite:
        if self.prepared is not None:
            raise LocalControlRecoveryDrillError("recovery drill writer was prepared twice")
        if intent.function != _FUNCTION or intent.value != _FORWARD_FLOW:
            raise LocalControlRecoveryDrillError("recovery drill intent is outside 40-to-39")
        prepared = self.delegate.prepare_scalar(intent, current_status)
        _require_exact_prepared_write(prepared, intent.target)
        self.prepared = prepared
        return prepared

    async def send_once(self, payload: bytes, lease: LocalControlSendPermit) -> bytes:
        if self.prepared is None or bytes(payload) != self.prepared.forward_payload:
            raise LocalControlRecoveryDrillError(
                "recovery drill send differs from prepared forward"
            )
        acknowledgement = await self.delegate.send_once(payload, lease)
        self.completed_send_count += 1
        self.acknowledgement = bytes(acknowledgement)
        return acknowledgement

    async def readback(self, lease: LocalControlSendPermit) -> bytes:
        if self.prepared is None:
            raise LocalControlRecoveryDrillError("recovery drill readback has no prepared write")
        status = await self.delegate.readback(lease)
        if self.injected_fault_count == 0 and verify_prepared_readback(self.prepared, status):
            self.verified_readback = bytes(status)
            self.injected_fault_count = 1
            raise DesignatedForwardReadbackFault("designated post-verification readback fault")
        return status

    async def close(self) -> None:
        if self.close_count:
            raise LocalControlRecoveryDrillError("recovery drill writer was closed twice")
        self.close_count = 1
        await self.delegate.close()


@dataclass(frozen=True, slots=True)
class RecoveryDrillForwardResult:
    operation_id: str
    phase: TransactionPhase
    action_phase: ActionPhase
    diagnostic: str
    revision: int
    journal_sha256: str
    target_id: str
    acknowledgement_sha256: str
    forward_readback_sha256: str
    physical_write_count: int = 1
    created_receipt_count: int = 0

    def public_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "phase": self.phase.value,
            "action_phase": self.action_phase.value,
            "diagnostic": self.diagnostic,
            "revision": self.revision,
            "journal_sha256": self.journal_sha256,
            "target_id": self.target_id,
            "acknowledgement_sha256": self.acknowledgement_sha256,
            "forward_readback_sha256": self.forward_readback_sha256,
            "physical_write_count": self.physical_write_count,
            "created_receipt_count": self.created_receipt_count,
            "planned_forward_complete": True,
            "private_values_exposed": False,
        }


class ActualRecoveryForwardDrill:
    """Create exactly one recoverable 40-to-39 transaction, never perform recovery."""

    def __init__(
        self,
        *,
        catalog: LocalControlCatalog,
        resolver: CurrentEndpointResolver,
        journal: LocalControlJournalStore,
        lease: LocalControlLease,
        writer_factory: DeviceWriterFactory = ConnectedDeviceWriter,
        qualification_verifier: QualificationVerifier = qualified_physical_identity_keys,
    ) -> None:
        self._catalog = catalog
        self._resolver = resolver
        self._journal = journal
        self._lease = lease
        self._delegate_writer_factory = writer_factory
        self._qualification_verifier = qualification_verifier

    async def execute(
        self,
        operation_id: str,
        target: LocalDeviceBinding,
    ) -> RecoveryDrillForwardResult:
        descriptor = self._catalog.find(target.product_key, _FUNCTION)
        if (
            descriptor.family != _EXPECTED_FAMILY
            or not descriptor.encoding_available
            or not descriptor.control_exposed_for(target.physical_identity_key)
        ):
            raise LocalControlRecoveryDrillError(
                "recovery drill target lacks exact qualified manual-flow control"
            )

        adapters: list[RecoveryReadbackFaultWriter] = []

        def faulting_writer_factory(endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
            if adapters:
                raise LocalControlRecoveryDrillError(
                    "recovery drill may create only one connected writer"
                )
            adapter = RecoveryReadbackFaultWriter(self._delegate_writer_factory(endpoint))
            adapters.append(adapter)
            return cast(ConnectedDeviceWriter, adapter)

        coordinator = LocalControlCoordinator(
            catalog=self._catalog,
            resolver=self._resolver,
            journal=self._journal,
            lease=self._lease,
            writer_factory=cast(WriterFactory, faulting_writer_factory),
            qualification_verifier=self._qualification_verifier,
        )
        intent = LocalControlIntent(
            request_id=operation_id,
            target=target,
            function=_FUNCTION,
            value=_FORWARD_FLOW,
        )
        try:
            result = await coordinator.execute(intent)
        except LocalControlExecutionError as error:
            if not isinstance(error.__cause__, DesignatedForwardReadbackFault):
                raise LocalControlRecoveryDrillError(
                    "forward phase stopped for an unplanned reason"
                ) from error
        else:
            raise LocalControlRecoveryDrillError(
                f"forward phase unexpectedly terminalized as {result.phase.value}"
            )

        if len(adapters) != 1:
            raise LocalControlRecoveryDrillError("forward phase did not use exactly one writer")
        adapter = adapters[0]
        record = self._journal.load(operation_id)
        if record is None:
            raise LocalControlRecoveryDrillError("forward phase left no durable journal")
        self._require_planned_durable_state(record, target, adapter)
        return RecoveryDrillForwardResult(
            operation_id=operation_id,
            phase=record.phase,
            action_phase=record.actions[0].phase,
            diagnostic=record.actions[0].diagnostic or "",
            revision=record.revision,
            journal_sha256=self._journal.record_digest(record),
            target_id=hashlib.sha256(
                f"local-control-recovery-drill-target:{target.physical_identity_key}".encode(
                    "ascii"
                )
            ).hexdigest(),
            acknowledgement_sha256=record.actions[0].acknowledgement_sha256 or "",
            forward_readback_sha256=hashlib.sha256(adapter.verified_readback or b"").hexdigest(),
        )

    def _require_planned_durable_state(
        self,
        record: LocalControlJournalRecord,
        target: LocalDeviceBinding,
        adapter: RecoveryReadbackFaultWriter,
    ) -> None:
        if (
            record.phase is not TransactionPhase.RECOVERY_REQUIRED
            or len(record.actions) != 1
            or record.actions[0].phase is not ActionPhase.UNCERTAIN
            or record.actions[0].diagnostic != "forward_readback_unavailable"
        ):
            raise LocalControlRecoveryDrillError(
                "forward journal is not the designated recovery-required state"
            )
        action = record.actions[0]
        prepared = adapter.prepared
        acknowledgement = adapter.acknowledgement
        verified_readback = adapter.verified_readback
        if (
            action.target != target
            or action.function != _FUNCTION
            or not action.restore_on_recovery
            or prepared is None
            or acknowledgement is None
            or verified_readback is None
            or adapter.completed_send_count != 1
            or adapter.injected_fault_count != 1
            or adapter.close_count != 1
        ):
            raise LocalControlRecoveryDrillError("forward adapter evidence is incomplete")
        blobs = (
            (action.forward_payload_sha256, prepared.forward_payload),
            (action.rollback_payload_sha256, prepared.rollback_payload),
            (action.baseline_status_sha256, prepared.baseline_status),
            (action.expected_readback_sha256, prepared.expected_readback),
            (action.baseline_readback_sha256, prepared.baseline_readback),
        )
        if any(
            hashlib.sha256(payload).hexdigest() != digest
            or self._journal.load_blob(digest) != payload
            for digest, payload in blobs
        ):
            raise LocalControlRecoveryDrillError("forward journal blobs changed")
        acknowledgement_sha256 = hashlib.sha256(acknowledgement).hexdigest()
        if (
            action.acknowledgement_sha256 != acknowledgement_sha256
            or self._journal.load_blob(acknowledgement_sha256) != acknowledgement
        ):
            raise LocalControlRecoveryDrillError("forward acknowledgement evidence changed")
        unfinished = self._journal.unfinished_records()
        if unfinished != (record,):
            raise LocalControlRecoveryDrillError(
                "forward phase did not leave exactly its one recovery obligation"
            )


def _require_exact_prepared_write(
    prepared: PreparedDeviceWrite,
    target: LocalDeviceBinding,
) -> None:
    try:
        expected = json.loads(prepared.expected_readback.decode("ascii"))
        baseline = json.loads(prepared.baseline_readback.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalControlRecoveryDrillError("recovery drill scalar evidence is invalid") from error
    if (
        prepared.target.target != target
        or prepared.function != _FUNCTION
        or prepared.verification_kind != "scalar_fields"
        or prepared.no_change
        or not prepared.restore_on_recovery
        or expected != {"Flow": _FORWARD_FLOW}
        or baseline != {"Flow": _BASELINE_FLOW}
        or prepared.forward_payload == prepared.rollback_payload
    ):
        raise LocalControlRecoveryDrillError(
            "recovery drill prepared write is not exact 40-to-39 with rollback 40"
        )


__all__ = [
    "ActualRecoveryForwardDrill",
    "DesignatedForwardReadbackFault",
    "LocalControlRecoveryDrillError",
    "RecoveryDrillForwardResult",
    "RecoveryReadbackFaultWriter",
]
