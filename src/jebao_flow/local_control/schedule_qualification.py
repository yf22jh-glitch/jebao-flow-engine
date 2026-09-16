"""Bar/return-pump schedule A/B/owned-raw-restore qualification.

An arbitrary captured baseline need not decode as an editable schedule. Only the requested A/B
images pass the app model; recovery uses the exact owned baseline bytes. This implements the
software contract, not accepted firmware behavior. Runtime schedule controls remain unpromoted.
"""

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jebao_flow.local_control.aquarium_power_qualification import AquariumPowerQualificationProducer
from jebao_flow.local_control.device_writer import (
    build_exact_schedule_payload,
    extract_current_schedule_image,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalControlIntent, QualificationBinding
from jebao_flow.local_control.pump_preview import ReturnPumpSchedule
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    qualification_receipt_bytes,
)
from jebao_flow.local_control.qualification_producer import (
    _SAFE_FIRST_WRITE_MAXIMUM,
    ManualFlowQualificationProducer,
    QualificationEpoch,
    QualificationProductionError,
    QualificationProductionResult,
    _canonical_json,
    _canonical_sha256,
    _manual_flow_state,
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.local_control.wavemaker import WavemakerSchedule
from jebao_flow.protocol.schedule import (
    AQUARIUM_PUMP_PRODUCT_KEY,
    DC_PUMP_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
)

_ACTIONS = ("schedule_a", "schedule_b", "owned_raw_restore")


def parse_qualification_schedule(value):
    if not isinstance(value, dict):
        raise ValueError("schedule qualification requires a declared schedule object")
    family = value.get("family")
    if family not in {"bar", "dc_pro", "aquarium"}:
        raise ValueError("this producer supports only Bar/DC/Aquarium schedules")
    return (WavemakerSchedule if family == "bar" else ReturnPumpSchedule).model_validate(value)


class ScheduleQualificationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    function: Literal["schedule", "schedule_reset"] = "schedule"
    epoch: QualificationEpoch = Field(repr=False)
    schedule_a: dict[str, object]
    schedule_b: dict[str, object]
    firmware_version: str
    created_at: datetime


class ScheduleQualificationProducer(ManualFlowQualificationProducer):
    _actions = _ACTIONS
    _trial_indices = (0, 1)
    _trial_functions = ("schedule", "schedule")
    _receipt_indices = (0, 1)
    _qualification_function = "schedule"
    _parse_plan = staticmethod(parse_qualification_schedule)

    def __init__(self, *, schedule_a, schedule_b, **kwargs):
        super().__init__(**kwargs)
        self._plans = tuple(self._parse_plan(value) for value in (schedule_a, schedule_b))
        if self._plans[0].product_key != self._plans[1].product_key:
            raise ValueError("qualification images must address the same product")
        if self._plans[0].compile_image() == self._plans[1].compile_image():
            raise ValueError("qualification requires two distinct app images")

    async def execute(self, operation_id):
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            return self._finalize_schedule(existing, writes=0)
        epoch = self._epoch_provider()
        if epoch.target.product_key != self._plans[0].product_key:
            raise QualificationProductionError("schedule family differs from current target")
        # Apply the existing qualification output cap even though these images remain inactive.
        for plan in self._plans:
            for slot in plan.slots:
                output = (
                    slot.flow
                    if isinstance(plan, WavemakerSchedule)
                    else (slot.speed if plan.family == "aquarium" else slot.flow)
                )
                if output is not None and not epoch.min_power <= output <= min(
                    epoch.max_power, _SAFE_FIRST_WRITE_MAXIMUM
                ):
                    raise QualificationProductionError(
                        "trial schedule is outside qualification limits"
                    )
        record = None
        expected_firmware = None
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("schedule qualification appeared concurrently")
            for index in range(len(self._actions)):
                writer = None
                try:
                    endpoint, firmware = await self._resolve(epoch, expected_firmware)
                    expected_firmware = firmware
                    writer = self._writer_factory(endpoint)
                    status = await writer.connect_and_read()
                    self._require_schedule_state(epoch, writer, status)
                    if record is None:
                        intent = ScheduleQualificationIntent(
                            operation_id=operation_id,
                            function=self._qualification_function,
                            epoch=epoch,
                            schedule_a=self._plans[0].model_dump(mode="json"),
                            schedule_b=self._plans[1].model_dump(mode="json"),
                            firmware_version=firmware,
                            created_at=self._now(),
                        )
                        trials = tuple(
                            writer.prepare_scalar(self._phase_intent(intent, phase), status)
                            for phase in range(len(self._trial_indices))
                        )
                        restore = replace(
                            trials[0],
                            function="schedule",
                            forward_payload=trials[0].rollback_payload,
                            expected_readback=trials[0].baseline_readback,
                            no_change=False,
                        )
                        record = self._create_schedule_record(intent, (*trials, restore))
                    else:
                        actual = extract_current_schedule_image(epoch.target.product_key, status)
                        previous = self._journal.load_blob(
                            record.actions[index - 1].expected_readback_sha256
                        )
                        if actual != previous:
                            raise QualificationProductionError("schedule changed between phases")
                    prepared = self._prepared_from_action(record.actions[index], endpoint)
                    self._require_epoch(epoch)
                    record = await self._execute_action(
                        record,
                        index,
                        writer,
                        prepared,
                        permit,
                        readback_validator=lambda raw, writer=writer: self._require_schedule_state(
                            epoch, writer, raw
                        ),
                    )
                except BaseException:
                    if record is not None:
                        self._require_recovery(operation_id)
                    raise
                finally:
                    if writer is not None:
                        try:
                            await writer.close()
                        except BaseException:
                            if record is not None:
                                self._require_recovery(operation_id)
                            raise
            try:
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                self._require_recovery(operation_id)
                raise
        return self._finalize_schedule(record, writes=len(self._actions))

    def _require_schedule_state(self, epoch, writer, status):
        if epoch.target.product_key == AQUARIUM_PUMP_PRODUCT_KEY:
            state = AquariumPowerQualificationProducer._read_baseline_power_state(
                writer, epoch.target, status
            )
            AquariumPowerQualificationProducer._require_state_envelope(epoch, state)
        else:
            self._require_state_envelope(epoch, _manual_flow_state(writer, epoch.target, status))

    def _phase_intent(self, intent, index):
        function = self._trial_functions[index]
        return LocalControlIntent(
            request_id=_phase_request_id(intent.operation_id, self._actions[index]),
            target=intent.epoch.target,
            function=function,
            value=(
                None
                if function == "schedule_reset"
                else (intent.schedule_a, intent.schedule_b)[self._trial_indices[index]]
            ),
        )

    def _create_schedule_record(self, intent, prepared):
        actions = tuple(
            JournalAction(
                action_id=name,
                target=item.target.target,
                function=item.function,
                forward_payload_sha256=self._journal.install_blob(item.forward_payload),
                rollback_payload_sha256=self._journal.install_blob(item.rollback_payload),
                baseline_status_sha256=self._journal.install_blob(item.baseline_status),
                expected_readback_sha256=self._journal.install_blob(item.expected_readback),
                baseline_readback_sha256=self._journal.install_blob(item.baseline_readback),
                verification_kind="schedule_image",
                restore_on_recovery=True,
            )
            for name, item in zip(self._actions, prepared, strict=True)
        )
        record = LocalControlJournalRecord(
            request_id=intent.operation_id,
            intent_sha256=self._journal.install_blob(
                _canonical_json(intent.model_dump(mode="json"))
            ),
            created_at=self._now(),
            updated_at=self._now(),
            actions=actions,
        )
        self._journal.create(record)
        return record

    def _finalize_schedule(self, record, *, writes):
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError(
                "schedule qualification requires recovery, not replay"
            )
        payload = self._journal.load_blob(record.intent_sha256)
        intent = ScheduleQualificationIntent.model_validate_json(payload)
        if (
            _canonical_json(intent.model_dump(mode="json")) != payload
            or intent.operation_id != record.request_id
            or intent.function != self._qualification_function
            or (intent.schedule_a, intent.schedule_b)
            != tuple(plan.model_dump(mode="json") for plan in self._plans)
        ):
            raise QualificationProductionError("schedule qualification intent changed")
        epoch = intent.epoch
        if epoch.target.product_key != self._plans[0].product_key:
            raise QualificationProductionError("schedule qualification target changed")
        if tuple(action.action_id for action in record.actions) != self._actions:
            raise QualificationProductionError("schedule qualification actions changed")
        raw = self._journal.load_blob(record.actions[0].baseline_status_sha256)
        baseline = extract_current_schedule_image(epoch.target.product_key, raw)
        restore_payload = build_exact_schedule_payload(epoch.target.product_key, baseline)
        expected_images = (
            *[self._plans[index].compile_image() for index in self._trial_indices],
            baseline,
        )
        expected_functions = (*self._trial_functions, "schedule")
        for action, expected, function in zip(
            record.actions, expected_images, expected_functions, strict=True
        ):
            if (
                action.phase is not ActionPhase.VERIFIED
                or action.target != epoch.target
                or action.function != function
                or action.verification_kind != "schedule_image"
                or not action.restore_on_recovery
                or action.acknowledgement_sha256 is None
            ):
                raise QualificationProductionError("schedule evidence is incomplete")
            self._journal.load_blob(action.acknowledgement_sha256)
            checks = (
                (
                    action.forward_payload_sha256,
                    build_exact_schedule_payload(epoch.target.product_key, expected),
                ),
                (action.rollback_payload_sha256, restore_payload),
                (action.baseline_status_sha256, raw),
                (action.expected_readback_sha256, expected),
                (action.baseline_readback_sha256, baseline),
            )
            if any(self._journal.load_blob(digest) != value for digest, value in checks):
                raise QualificationProductionError(
                    "schedule image or owned recovery evidence changed"
                )
        receipts = tuple(
            LocalControlQualificationReceipt(
                product_key=epoch.target.product_key,
                function=self._qualification_function,
                physical_identity_key=epoch.target.physical_identity_key,
                app_version=epoch.app_version,
                app_catalog_sha256=epoch.app_catalog_sha256,
                template_sha256=epoch.template_sha256,
                schema_sha256=epoch.schema_sha256,
                firmware_version=intent.firmware_version,
                request_id=self._phase_intent(intent, index).request_id,
                intent_sha256=_canonical_sha256(
                    self._phase_intent(intent, index).model_dump(mode="json")
                ),
                control_payload_sha256=record.actions[index].forward_payload_sha256,
                acknowledgement_sha256=record.actions[index].acknowledgement_sha256,
                readback_sha256=record.actions[index].expected_readback_sha256,
                completed_at=record.updated_at,
            )
            for index in self._receipt_indices
        )
        binding = QualificationBinding(
            product_key=epoch.target.product_key,
            function=self._qualification_function,
            app_version=epoch.app_version,
            app_catalog_sha256=epoch.app_catalog_sha256,
            template_sha256=epoch.template_sha256,
            schema_sha256=epoch.schema_sha256,
            firmware_version=intent.firmware_version,
            receipt_sha256=tuple(
                hashlib.sha256(qualification_receipt_bytes(item)).hexdigest() for item in receipts
            ),
        )
        digest = self._artifacts.finalize(receipts, binding)
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


class ScheduleResetQualificationProducer(ScheduleQualificationProducer):
    """Two app reset requests separated by an explicit test image, then own-raw restore.

    Only the two actual reset requests receive reset receipts. The intervening image write is
    not mislabelled as a reset, and no receipt is issued before the original image is restored.
    """

    _actions = ("reset_a", "schedule_b", "reset_b", "owned_raw_restore")
    _trial_indices = (0, 1, 0)
    _trial_functions = ("schedule_reset", "schedule", "schedule_reset")
    _receipt_indices = (0, 2)
    _qualification_function = "schedule_reset"

    @staticmethod
    def _parse_plan(value):
        if isinstance(value, dict) and value.get("family") == "pro":
            return WavemakerSchedule.model_validate(value)
        return parse_qualification_schedule(value)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if (
            self._plans[0].product_key
            not in {LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, DC_PUMP_PRO_PRODUCT_KEY}
            or self._plans[0].slots
        ):
            raise ValueError("reset qualification requires Pro/DC and an empty first image")
