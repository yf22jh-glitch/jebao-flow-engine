"""Two explicit DC clock syncs with advancing-time evidence and no clock rollback.

Both requests use current configured local time. No pump control or historical clock
value is restored. Offline tests do not establish hardware clock acceptance.
"""

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jebao_flow.local_control.device_clock import clock_readback_matches
from jebao_flow.local_control.device_writer import build_device_clock_payload, get_product_schema
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalControlIntent, QualificationBinding
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    qualification_receipt_bytes,
)
from jebao_flow.local_control.qualification_producer import (
    ManualFlowQualificationProducer,
    QualificationEpoch,
    QualificationProductionError,
    QualificationProductionResult,
    _canonical_json,
    _canonical_sha256,
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.protocol.schedule import DC_PUMP_PRO_PRODUCT_KEY

_OBSERVED = "clock_evidence_"


class ClockQualificationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    epoch: QualificationEpoch
    firmware_version: str


class DcClockQualificationProducer(ManualFlowQualificationProducer):
    async def execute(self, operation_id):
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            return self._finalize_clock(existing, writes=0)
        epoch = self._epoch_provider()
        if epoch.target.product_key != DC_PUMP_PRO_PRODUCT_KEY:
            raise ValueError("this clock qualification is DC-only")
        record = None
        firmware = None
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            try:
                for index in range(2):
                    self._require_epoch(epoch)
                    endpoint, firmware = await self._resolve(epoch, firmware)
                    writer = self._writer_factory(endpoint)
                    try:
                        raw = await writer.connect_and_read()
                        phase_intent = self._phase_intent(operation_id, epoch, index)
                        prepared = writer.prepare_scalar(phase_intent, raw)
                        action = JournalAction(
                            action_id=f"clock_{index}",
                            target=epoch.target,
                            function="device_clock_sync",
                            forward_payload_sha256=self._journal.install_blob(
                                prepared.forward_payload
                            ),
                            rollback_payload_sha256=self._journal.install_blob(
                                prepared.rollback_payload
                            ),
                            baseline_status_sha256=self._journal.install_blob(raw),
                            expected_readback_sha256=self._journal.install_blob(
                                prepared.expected_readback
                            ),
                            baseline_readback_sha256=self._journal.install_blob(
                                prepared.baseline_readback
                            ),
                            verification_kind="device_clock",
                            recovery_policy="observe_clock",
                        )
                        if record is None:
                            intent = ClockQualificationIntent(
                                operation_id=operation_id, epoch=epoch, firmware_version=firmware
                            )
                            record = LocalControlJournalRecord(
                                request_id=operation_id,
                                intent_sha256=self._journal.install_blob(
                                    _canonical_json(intent.model_dump(mode="json"))
                                ),
                                created_at=self._now(),
                                updated_at=self._now(),
                                actions=(action,),
                            )
                            self._journal.create(record)
                        else:
                            replacement = record.evolve(
                                actions=(*record.actions, action), now=self._now()
                            )
                            self._journal.save(record, replacement)
                            record = replacement
                        observations = []

                        def capture(status, *, observations=observations):
                            observations.append(
                                self._journal.install_blob(
                                    _canonical_json(
                                        {
                                            "status_sha256": self._journal.install_blob(status),
                                            "observed_at": self._now().isoformat(),
                                        }
                                    )
                                )
                            )

                        record = await self._execute_action(
                            record,
                            index,
                            writer,
                            prepared,
                            permit,
                            readback_validator=capture,
                        )
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.VERIFIED,
                            diagnostic=_OBSERVED + observations[0],
                        )
                    finally:
                        await writer.close()
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                if record is not None:
                    self._require_recovery(operation_id)
                raise
        return self._finalize_clock(record, writes=2)

    @staticmethod
    def _phase_intent(operation_id, epoch, index):
        return LocalControlIntent(
            request_id=_phase_request_id(operation_id, f"clock_{index}"),
            target=epoch.target,
            function="device_clock_sync",
            value=None,
        )

    def _finalize_clock(self, record, *, writes):
        payload = self._journal.load_blob(record.intent_sha256)
        intent = ClockQualificationIntent.model_validate_json(payload)
        epoch = intent.epoch
        if (
            payload != _canonical_json(intent.model_dump(mode="json"))
            or epoch.target.product_key != DC_PUMP_PRO_PRODUCT_KEY
            or intent.operation_id != record.request_id
            or record.phase is not TransactionPhase.VERIFIED
            or len(record.actions) != 2
        ):
            raise QualificationProductionError("clock operation is not fully verified")
        receipts = []
        for index, action in enumerate(record.actions):
            if (
                action.action_id != f"clock_{index}"
                or action.target != epoch.target
                or action.phase is not ActionPhase.VERIFIED
                or action.function != "device_clock_sync"
                or action.recovery_policy != "observe_clock"
                or not action.acknowledgement_sha256
                or not action.diagnostic
                or not action.diagnostic.startswith(_OBSERVED)
            ):
                raise QualificationProductionError("clock evidence is incomplete")
            expected_bytes = self._journal.load_blob(action.expected_readback_sha256)
            expected = json.loads(expected_bytes)
            command = build_device_clock_payload(
                epoch.target.product_key, datetime.fromisoformat(expected["local_time"])
            )
            if (
                self._journal.load_blob(action.forward_payload_sha256) != command
                or action.forward_payload_sha256 != action.rollback_payload_sha256
                or action.expected_readback_sha256 != action.baseline_readback_sha256
            ):
                raise QualificationProductionError("clock command evidence changed")
            evidence = json.loads(self._journal.load_blob(action.diagnostic[len(_OBSERVED) :]))
            status = self._journal.load_blob(evidence["status_sha256"])
            if not clock_readback_matches(
                expected_bytes,
                get_product_schema(epoch.target.product_key).decode_status(status),
                observed_at=datetime.fromisoformat(evidence["observed_at"]),
            ):
                raise QualificationProductionError(
                    "clock readback did not establish advancing time"
                )
            self._journal.load_blob(action.acknowledgement_sha256)
            phase_intent = self._phase_intent(record.request_id, epoch, index)
            receipts.append(
                LocalControlQualificationReceipt(
                    product_key=epoch.target.product_key,
                    function="device_clock_sync",
                    physical_identity_key=epoch.target.physical_identity_key,
                    app_version=epoch.app_version,
                    app_catalog_sha256=epoch.app_catalog_sha256,
                    template_sha256=epoch.template_sha256,
                    schema_sha256=epoch.schema_sha256,
                    firmware_version=intent.firmware_version,
                    request_id=phase_intent.request_id,
                    intent_sha256=_canonical_sha256(phase_intent.model_dump(mode="json")),
                    control_payload_sha256=action.forward_payload_sha256,
                    acknowledgement_sha256=action.acknowledgement_sha256,
                    readback_sha256=evidence["status_sha256"],
                    completed_at=record.updated_at,
                    verification_kind="advancing_clock",
                )
            )
        binding = QualificationBinding(
            product_key=epoch.target.product_key,
            function="device_clock_sync",
            app_version=epoch.app_version,
            app_catalog_sha256=epoch.app_catalog_sha256,
            template_sha256=epoch.template_sha256,
            schema_sha256=epoch.schema_sha256,
            firmware_version=intent.firmware_version,
            receipt_sha256=tuple(
                hashlib.sha256(qualification_receipt_bytes(item)).hexdigest() for item in receipts
            ),
        )
        digest = self._artifacts.finalize(tuple(receipts), binding)
        return QualificationProductionResult(
            operation_id=record.request_id,
            phase=record.phase,
            journal_sha256=self._journal.record_digest(record),
            target_id=hashlib.sha256(
                f"qualification-target:{epoch.target.physical_identity_key}".encode("ascii")
            ).hexdigest(),
            receipt_count=2,
            physical_write_count=writes,
            qualification_sha256=digest,
            completed_at=record.updated_at,
        )
