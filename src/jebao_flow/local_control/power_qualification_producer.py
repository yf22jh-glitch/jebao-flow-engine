"""Crash-safe production of local-control power qualification receipts.

The power sequence deliberately converges to OFF after any non-terminal failure.  It reuses the
audited manual-flow producer's function-independent lease, resolution, journal transition, and
artifact machinery without invoking its manual-flow execution or finalization paths.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, PreparedDeviceWrite
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    TransactionPhase,
)
from jebao_flow.local_control.lease import LocalControlSendPermit
from jebao_flow.local_control.models import (
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    qualification_receipt_bytes,
)
from jebao_flow.local_control.qualification_producer import (
    _SAFE_FIRST_WRITE_MAXIMUM,
    ManualFlowQualificationProducer,
    ManualFlowState,
    QualificationEpoch,
    QualificationProductionError,
    QualificationProductionResult,
    _canonical_json,
    _canonical_sha256,
    _manual_flow_state,
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint

_FUNCTION = "power"
_ACTION_IDS = ("same_on", "power_off", "planned_on")


def _boolean_scalar_readback(payload: bytes) -> tuple[str, bool]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationProductionError(
            "power qualification scalar readback evidence changed"
        ) from exc
    if (
        type(decoded) is not dict
        or len(decoded) != 1
        or _canonical_json(decoded) != payload
    ):
        raise QualificationProductionError(
            "power qualification scalar readback evidence changed"
        )
    field, value = next(iter(decoded.items()))
    if not field or type(value) is not bool:
        raise QualificationProductionError(
            "power qualification scalar readback evidence changed"
        )
    return field, value


class PowerQualificationIntent(BaseModel):
    """Private durable intent for one conservative ON/OFF/ON qualification operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["power"] = _FUNCTION
    baseline_power: Literal[True] = True
    safe_power: Literal[False] = False
    baseline_timer_enabled: Literal[False] = False
    baseline_mode: str = Field(min_length=1, max_length=128)
    baseline_linkage: str = Field(min_length=1, max_length=128)
    baseline_flow: int
    off_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    restore_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    min_power: int
    max_power: int
    app_version: str = Field(min_length=1, max_length=128)
    app_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    firmware_version: str = Field(min_length=1, max_length=256)
    created_at: datetime

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("power qualification timestamp must be timezone-aware")
        if self.off_request_id == self.restore_request_id:
            raise ValueError("power qualification receipt requests must be distinct")
        if self.min_power > self.max_power:
            raise ValueError("power qualification intent has an invalid flow envelope")
        if not self.min_power <= self.baseline_flow <= self.max_power:
            raise ValueError("power qualification baseline flow is outside config limits")
        if self.baseline_flow > _SAFE_FIRST_WRITE_MAXIMUM:
            raise ValueError("power qualification baseline exceeds the first-write maximum")
        if self.baseline_mode != "constant" or self.baseline_linkage != "independent":
            raise ValueError("power qualification baseline ownership is invalid")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("power qualification versions must use printable ASCII")
        return self


class PowerQualificationProducer(ManualFlowQualificationProducer):
    """Execute or offline-finalize one safer-OFF three-frame power qualification."""

    async def execute(self, operation_id: str) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            if existing.phase is TransactionPhase.VERIFIED:
                return self._finalize_power(existing, physical_write_count=0)
            raise QualificationProductionError(
                "power qualification has unfinished or recovered durable state"
            )

        epoch = self._epoch_provider()
        record: LocalControlJournalRecord | None = None
        writes = 0
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError(
                    "power qualification operation appeared concurrently"
                )
            self._require_epoch(epoch)
            writer: ConnectedDeviceWriter | None = None
            try:
                endpoint, firmware_version = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                baseline_status = await writer.connect_and_read()
                baseline_state = _manual_flow_state(writer, epoch.target, baseline_status)
                self._require_state_envelope(epoch, baseline_state)
                intent, prepared = self._prepare_power_operation(
                    operation_id,
                    epoch,
                    endpoint,
                    writer,
                    baseline_status,
                    baseline_state,
                    firmware_version,
                )
                record = self._create_power_record(intent, prepared)
                self._require_epoch(epoch)
                record = await self._execute_action(
                    record,
                    0,
                    writer,
                    prepared[0],
                    permit,
                    readback_validator=lambda status: self._require_power_readback(
                        epoch,
                        intent,
                        writer,
                        status,
                        expected_enabled=True,
                    ),
                )
                writes += 1
            except BaseException:
                if record is not None:
                    self._require_recovery(record.request_id)
                raise
            finally:
                if writer is not None:
                    try:
                        await writer.close()
                    except BaseException:
                        if record is not None:
                            self._require_recovery(record.request_id)
                        raise

            try:
                self._require_epoch(epoch)
                record = await self._execute_fresh_power_phase(
                    record,
                    intent,
                    epoch,
                    firmware_version,
                    action_index=1,
                    expected_enabled_before=True,
                    expected_enabled_after=False,
                    permit=permit,
                )
                writes += 1
                self._require_epoch(epoch)
                record = await self._execute_fresh_power_phase(
                    record,
                    intent,
                    epoch,
                    firmware_version,
                    action_index=2,
                    expected_enabled_before=False,
                    expected_enabled_after=True,
                    permit=permit,
                )
                writes += 1
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                self._require_recovery(operation_id)
                raise

        return self._finalize_power(record, physical_write_count=writes)

    async def _execute_fresh_power_phase(
        self,
        record: LocalControlJournalRecord,
        intent: PowerQualificationIntent,
        epoch: QualificationEpoch,
        firmware_version: str,
        *,
        action_index: int,
        expected_enabled_before: bool,
        expected_enabled_after: bool,
        permit: LocalControlSendPermit,
    ) -> LocalControlJournalRecord:
        endpoint, _firmware = await self._resolve(epoch, firmware_version)
        writer = self._writer_factory(endpoint)
        try:
            status = await writer.connect_and_read()
            self._require_power_readback(
                epoch,
                intent,
                writer,
                status,
                expected_enabled=expected_enabled_before,
            )
            action = record.actions[action_index]
            prepared = self._prepared_from_action(action, endpoint)
            phase_intent = _power_phase_intent(intent, action_index)
            fresh = writer.prepare_scalar(phase_intent, status)
            if (
                fresh.no_change
                or fresh.forward_payload_sha256 != action.forward_payload_sha256
                or fresh.rollback_payload_sha256 != action.rollback_payload_sha256
                or hashlib.sha256(fresh.expected_readback).hexdigest()
                != action.expected_readback_sha256
                or hashlib.sha256(fresh.baseline_readback).hexdigest()
                != action.baseline_readback_sha256
                or fresh.restore_on_recovery is not action.restore_on_recovery
            ):
                raise QualificationProductionError("power qualification phase payload changed")
            self._require_epoch(epoch)
            return await self._execute_action(
                record,
                action_index,
                writer,
                prepared,
                permit,
                readback_validator=lambda readback: self._require_power_readback(
                    epoch,
                    intent,
                    writer,
                    readback,
                    expected_enabled=expected_enabled_after,
                ),
            )
        finally:
            await writer.close()

    def _prepare_power_operation(
        self,
        operation_id: str,
        epoch: QualificationEpoch,
        endpoint: ResolvedEndpoint,
        writer: ConnectedDeviceWriter,
        baseline_status: bytes,
        baseline_state: ManualFlowState,
        firmware_version: str,
    ) -> tuple[
        PowerQualificationIntent,
        tuple[PreparedDeviceWrite, PreparedDeviceWrite, PreparedDeviceWrite],
    ]:
        off_request_id = _phase_request_id(operation_id, "off")
        restore_request_id = _phase_request_id(operation_id, "restore")
        same_original = writer.prepare_scalar(
            LocalControlIntent(
                request_id=_phase_request_id(operation_id, "same"),
                target=epoch.target,
                function=_FUNCTION,
                value=True,
            ),
            baseline_status,
        )
        power_off = writer.prepare_scalar(
            LocalControlIntent(
                request_id=off_request_id,
                target=epoch.target,
                function=_FUNCTION,
                value=False,
            ),
            baseline_status,
        )
        if (
            not same_original.no_change
            or power_off.no_change
            or same_original.restore_on_recovery is not True
            or power_off.restore_on_recovery is not False
            or same_original.forward_payload != power_off.rollback_payload
            or same_original.expected_readback != power_off.baseline_readback
        ):
            raise QualificationProductionError(
                "power qualification baseline did not form an exact OFF step"
            )
        same_on = PreparedDeviceWrite(
            target=endpoint,
            function=_FUNCTION,
            forward_payload=same_original.forward_payload,
            rollback_payload=power_off.forward_payload,
            baseline_status=baseline_status,
            expected_readback=same_original.expected_readback,
            baseline_readback=power_off.expected_readback,
            verification_kind=same_original.verification_kind,
            no_change=True,
            restore_on_recovery=True,
        )
        planned_on = PreparedDeviceWrite(
            target=endpoint,
            function=_FUNCTION,
            forward_payload=same_original.forward_payload,
            rollback_payload=power_off.forward_payload,
            baseline_status=baseline_status,
            expected_readback=same_original.expected_readback,
            baseline_readback=power_off.expected_readback,
            verification_kind=same_original.verification_kind,
            no_change=False,
            restore_on_recovery=True,
        )
        intent = PowerQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            baseline_mode=baseline_state.mode,
            baseline_linkage=baseline_state.linkage,
            baseline_flow=baseline_state.flow,
            off_request_id=off_request_id,
            restore_request_id=restore_request_id,
            config_sha256=epoch.config_sha256,
            manifest_sha256=epoch.manifest_sha256,
            manifest_generation=epoch.manifest_generation,
            min_power=epoch.min_power,
            max_power=epoch.max_power,
            app_version=epoch.app_version,
            app_catalog_sha256=epoch.app_catalog_sha256,
            template_sha256=epoch.template_sha256,
            schema_sha256=epoch.schema_sha256,
            firmware_version=firmware_version,
            created_at=self._now(),
        )
        return intent, (same_on, power_off, planned_on)

    def _create_power_record(
        self,
        intent: PowerQualificationIntent,
        prepared: tuple[PreparedDeviceWrite, PreparedDeviceWrite, PreparedDeviceWrite],
    ) -> LocalControlJournalRecord:
        intent_payload = power_qualification_intent_bytes(intent)
        intent_sha256 = self._journal.install_blob(intent_payload)
        actions = tuple(
            JournalAction(
                action_id=action_id,
                target=item.target.target,
                function=item.function,
                forward_payload_sha256=self._journal.install_blob(item.forward_payload),
                rollback_payload_sha256=self._journal.install_blob(item.rollback_payload),
                baseline_status_sha256=self._journal.install_blob(item.baseline_status),
                expected_readback_sha256=self._journal.install_blob(item.expected_readback),
                baseline_readback_sha256=self._journal.install_blob(item.baseline_readback),
                verification_kind=item.verification_kind,
                restore_on_recovery=item.restore_on_recovery,
            )
            for action_id, item in zip(_ACTION_IDS, prepared, strict=True)
        )
        now = self._now()
        record = LocalControlJournalRecord(
            request_id=intent.operation_id,
            intent_sha256=intent_sha256,
            created_at=now,
            updated_at=now,
            actions=actions,
        )
        self._journal.create(record)
        try:
            reloaded = self._journal.load(record.request_id)
            if reloaded != record:
                raise QualificationProductionError(
                    "power qualification journal did not reload exactly"
                )
            if (
                reloaded.phase is not TransactionPhase.PREPARED
                or reloaded.revision != 0
                or any(
                    action.phase is not ActionPhase.PREPARED
                    or action.target != intent.target
                    or action.function != _FUNCTION
                    or action.verification_kind != "scalar_fields"
                    or action.acknowledgement_sha256 is not None
                    for action in reloaded.actions
                )
            ):
                raise QualificationProductionError(
                    "power qualification journal was not safely preinstalled"
                )
            self._require_preinstalled_recovery_mapping(reloaded)
            return reloaded
        except BaseException:
            self._require_recovery(record.request_id)
            raise

    def _finalize_power(
        self,
        record: LocalControlJournalRecord,
        *,
        physical_write_count: int,
    ) -> QualificationProductionResult:
        intent = self._load_power_intent(record)
        self._validate_power_terminal_evidence(record, intent)
        off_action, restore_action = record.actions[1:]
        receipts = (
            _power_receipt(intent, off_action, _power_phase_intent(intent, 1), record.updated_at),
            _power_receipt(
                intent,
                restore_action,
                _power_phase_intent(intent, 2),
                record.updated_at,
            ),
        )
        receipt_payloads = tuple(qualification_receipt_bytes(item) for item in receipts)
        receipt_digests = tuple(hashlib.sha256(payload).hexdigest() for payload in receipt_payloads)
        binding = QualificationBinding(
            product_key=intent.target.product_key,
            function=_FUNCTION,
            app_version=intent.app_version,
            app_catalog_sha256=intent.app_catalog_sha256,
            template_sha256=intent.template_sha256,
            schema_sha256=intent.schema_sha256,
            firmware_version=intent.firmware_version,
            receipt_sha256=receipt_digests,
        )
        qualification_sha256 = self._artifacts.finalize(receipts, binding)
        return QualificationProductionResult(
            operation_id=record.request_id,
            phase=record.phase,
            journal_sha256=self._journal.record_digest(record),
            target_id=hashlib.sha256(
                f"qualification-target:{intent.target.physical_identity_key}".encode("ascii")
            ).hexdigest(),
            receipt_count=2,
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )

    def _load_power_intent(
        self,
        record: LocalControlJournalRecord,
    ) -> PowerQualificationIntent:
        payload = self._journal.load_blob(record.intent_sha256)
        try:
            intent = PowerQualificationIntent.model_validate_json(payload)
        except ValueError as error:
            raise QualificationProductionError(
                "power qualification journal intent is invalid"
            ) from error
        if (
            power_qualification_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
        ):
            raise QualificationProductionError(
                "power qualification journal intent is not canonical"
            )
        return intent

    def _validate_power_terminal_evidence(
        self,
        record: LocalControlJournalRecord,
        intent: PowerQualificationIntent,
    ) -> None:
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError("power qualification journal is not verified")
        if tuple(action.action_id for action in record.actions) != _ACTION_IDS:
            raise QualificationProductionError("power qualification journal actions changed")
        if any(
            action.phase is not ActionPhase.VERIFIED
            or action.target != intent.target
            or action.function != _FUNCTION
            or action.acknowledgement_sha256 is None
            for action in record.actions
        ):
            raise QualificationProductionError(
                "power qualification journal evidence is incomplete"
            )
        for action in record.actions:
            for digest in (
                action.forward_payload_sha256,
                action.rollback_payload_sha256,
                action.baseline_status_sha256,
                action.expected_readback_sha256,
                action.baseline_readback_sha256,
                action.acknowledgement_sha256,
            ):
                if digest is None:
                    raise QualificationProductionError(
                        "power qualification journal evidence is incomplete"
                    )
                self._journal.load_blob(digest)

        self._require_preinstalled_recovery_mapping(record)

    def _require_preinstalled_recovery_mapping(
        self,
        record: LocalControlJournalRecord,
    ) -> None:
        if tuple(action.action_id for action in record.actions) != _ACTION_IDS:
            raise QualificationProductionError("power qualification journal actions changed")
        for action in record.actions:
            for digest in (
                action.forward_payload_sha256,
                action.rollback_payload_sha256,
                action.baseline_status_sha256,
                action.expected_readback_sha256,
                action.baseline_readback_sha256,
            ):
                self._journal.load_blob(digest)

        same_on, power_off, planned_on = record.actions
        expected_readbacks = tuple(
            _boolean_scalar_readback(
                self._journal.load_blob(action.expected_readback_sha256)
            )
            for action in record.actions
        )
        baseline_readbacks = tuple(
            _boolean_scalar_readback(
                self._journal.load_blob(action.baseline_readback_sha256)
            )
            for action in record.actions
        )
        if (
            len({field for field, _value in expected_readbacks + baseline_readbacks}) != 1
            or tuple(value for _field, value in expected_readbacks) != (True, False, True)
            or tuple(value for _field, value in baseline_readbacks) != (False, True, False)
            or same_on.expected_readback_sha256 != power_off.baseline_readback_sha256
            or same_on.expected_readback_sha256 != planned_on.expected_readback_sha256
            or same_on.baseline_readback_sha256 != power_off.expected_readback_sha256
            or same_on.baseline_readback_sha256 != planned_on.baseline_readback_sha256
            or same_on.expected_readback_sha256 == same_on.baseline_readback_sha256
            or tuple(action.restore_on_recovery for action in record.actions) != (True, False, True)
            or same_on.forward_payload_sha256 != power_off.rollback_payload_sha256
            or same_on.forward_payload_sha256 != planned_on.forward_payload_sha256
            or same_on.rollback_payload_sha256 != power_off.forward_payload_sha256
            or same_on.rollback_payload_sha256 != planned_on.rollback_payload_sha256
            or same_on.forward_payload_sha256 == same_on.rollback_payload_sha256
            or len({action.baseline_status_sha256 for action in record.actions}) != 1
        ):
            raise QualificationProductionError(
                "power qualification safer-OFF payload mapping changed"
            )

    def _require_power_readback(
        self,
        epoch: QualificationEpoch,
        intent: PowerQualificationIntent,
        writer: ConnectedDeviceWriter,
        status: bytes,
        *,
        expected_enabled: bool,
    ) -> None:
        state = _manual_flow_state(writer, epoch.target, status)
        if (
            state.enabled is not expected_enabled
            or state.timer_enabled is not intent.baseline_timer_enabled
            or state.mode != intent.baseline_mode
            or state.linkage != intent.baseline_linkage
            or state.flow != intent.baseline_flow
        ):
            raise QualificationProductionError("power qualification phase state changed")


def power_qualification_intent_bytes(intent: PowerQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


def _power_phase_intent(
    intent: PowerQualificationIntent,
    action_index: int,
) -> LocalControlIntent:
    if action_index == 0:
        request_id = _phase_request_id(intent.operation_id, "same")
        value = True
    elif action_index == 1:
        request_id = intent.off_request_id
        value = False
    elif action_index == 2:
        request_id = intent.restore_request_id
        value = True
    else:
        raise IndexError("power qualification action index is invalid")
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=_FUNCTION,
        value=value,
    )


def _power_receipt(
    intent: PowerQualificationIntent,
    action: JournalAction,
    phase_intent: LocalControlIntent,
    completed_at: datetime,
) -> LocalControlQualificationReceipt:
    if action.acknowledgement_sha256 is None:
        raise QualificationProductionError("power qualification action has no acknowledgement")
    return LocalControlQualificationReceipt(
        product_key=intent.target.product_key,
        function=_FUNCTION,
        physical_identity_key=intent.target.physical_identity_key,
        app_version=intent.app_version,
        app_catalog_sha256=intent.app_catalog_sha256,
        template_sha256=intent.template_sha256,
        schema_sha256=intent.schema_sha256,
        firmware_version=intent.firmware_version,
        request_id=phase_intent.request_id,
        intent_sha256=_canonical_sha256(phase_intent.model_dump(mode="json")),
        control_payload_sha256=action.forward_payload_sha256,
        acknowledgement_sha256=action.acknowledgement_sha256,
        readback_sha256=action.expected_readback_sha256,
        completed_at=completed_at,
    )


__all__ = [
    "PowerQualificationIntent",
    "PowerQualificationProducer",
    "power_qualification_intent_bytes",
]
