"""Explicit Aquarium low-output preparation with an owned original-speed restore.

The existing trial ceiling is unchanged. Only the final owned restore may exceed it,
and only to the initial, in-config/app-range speed captured before the first write.
One journal owns preparation, two qualification frames, and original restoration.
Offline tests establish software ordering, not firmware acceptance or physical safety.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from jebao_flow.local_control.aquarium_power_qualification import _require_safe_motor_speed
from jebao_flow.local_control.aquarium_qualification import (
    AquariumScalarQualificationIntent,
    AquariumScalarQualificationProducer,
)
from jebao_flow.local_control.bar_qualification import _bar_phase_intent, _bar_receipt
from jebao_flow.local_control.device_writer import (
    build_control_payload,
    get_product_schema,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalControlIntent, QualificationBinding
from jebao_flow.local_control.qualification import qualification_receipt_bytes
from jebao_flow.local_control.qualification_producer import (
    QualificationProductionError,
    QualificationProductionResult,
    _canonical_json,
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range

_ACTION_IDS = ("prepare_speed", "trial_step", "trial_restore", "original_speed_restore")
_OBSERVED = "aquarium_status_sha256."


class PreparedAquariumQualificationIntent(BaseModel):
    """Separate the captured original from the prospective low-output trial state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["aquarium_prepared_scalar"] = "aquarium_prepared_scalar"
    original_motor_speed: StrictInt
    trial: AquariumScalarQualificationIntent

    @model_validator(mode="after")
    def validate_original(self) -> Self:
        trial = self.trial
        minimum, maximum = declared_app_integer_range(trial.target.product_key, "motor_speed")
        if not (
            minimum <= self.original_motor_speed <= maximum
            and trial.min_power <= self.original_motor_speed <= trial.max_power
            and trial.baseline_motor_speed <= self.original_motor_speed
        ):
            raise ValueError("original speed must be in range and preparation must not raise it")
        if trial.step_request_id != _phase_request_id(trial.operation_id, "step") or (
            trial.restore_request_id != _phase_request_id(trial.operation_id, "restore")
        ):
            raise ValueError("prepared qualification phase ids differ from their operation")
        lower, _upper = declared_app_integer_range(trial.target.product_key, trial.function)
        expected_trial = (
            (trial.baseline_value - 1 if trial.baseline_value > lower else lower + 1)
            if trial.function == "motor_speed"
            else (lower + 1 if trial.baseline_value == lower else lower)
        )
        if trial.trial_value != expected_trial:
            raise ValueError("prepared qualification trial differs from the deterministic step")
        return self


def _intent_bytes(intent: PreparedAquariumQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


def _phase_intents(intent: PreparedAquariumQualificationIntent) -> tuple[LocalControlIntent, ...]:
    trial = intent.trial
    return (
        LocalControlIntent(
            request_id=_phase_request_id(trial.operation_id, "prepare_speed"),
            target=trial.target,
            function="motor_speed",
            value=trial.baseline_motor_speed,
        ),
        _bar_phase_intent(trial, 1),
        _bar_phase_intent(trial, 2),
        LocalControlIntent(
            request_id=_phase_request_id(trial.operation_id, "original_speed_restore"),
            target=trial.target,
            function="motor_speed",
            value=intent.original_motor_speed,
        ),
    )


class PreparedAquariumScalarQualificationProducer(AquariumScalarQualificationProducer):
    """Opt-in four-frame qualification; legacy three-frame admission stays unchanged."""

    async def execute(
        self, operation_id: str, *, preparation_speed: int
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            intent = self._load_prepared_intent(existing)
            if (
                type(preparation_speed) is not int
                or intent.trial.baseline_motor_speed != preparation_speed
            ):
                raise QualificationProductionError(
                    "prepared qualification request has different intent"
                )
            return self._finalize_prepared(existing, intent, physical_write_count=0)

        epoch = self._epoch_provider()
        self._require_bar_target(epoch.target)
        _require_safe_motor_speed(
            preparation_speed,
            minimum=epoch.min_power,
            maximum=epoch.max_power,
            product_key=epoch.target.product_key,
        )
        record = None
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("prepared qualification appeared concurrently")
            try:
                for index in range(4):
                    self._require_epoch(epoch)
                    endpoint, firmware = await self._resolve(
                        epoch, None if index == 0 else intent.trial.firmware_version
                    )
                    writer = self._writer_factory(endpoint)
                    try:
                        current = await writer.connect_and_read()
                        if index == 0:
                            intent = self._prepare_intent(
                                operation_id, preparation_speed, epoch, writer, current, firmware
                            )
                            # Every rollback comes from the same initial raw status, not a
                            # fabricated low-output snapshot or the immediately prior phase.
                            prepared = tuple(
                                writer.prepare_scalar(phase, current)
                                for phase in _phase_intents(intent)
                            )
                            record = self._create_prepared_record(intent, prepared)
                        else:
                            self._require_state_envelope(
                                epoch, self._read_scalar_state(writer, epoch.target, current)
                            )
                        item = self._prepared_from_action(record.actions[index], endpoint)
                        fresh = writer.prepare_scalar(_phase_intents(intent)[index], current)
                        if (
                            fresh.forward_payload != item.forward_payload
                            or fresh.expected_readback != item.expected_readback
                        ):
                            raise QualificationProductionError("prepared phase payload changed")
                        self._require_epoch(epoch)
                        observed = []

                        def validate(
                            readback,
                            *,
                            index=index,
                            intent=intent,
                            writer=writer,
                            observed=observed,
                        ):
                            if index == 3:
                                self._require_original_readback(intent, readback)
                            else:
                                self._require_state_envelope(
                                    epoch,
                                    self._read_scalar_state(writer, epoch.target, readback),
                                )
                            observed.append(self._journal.install_blob(readback))

                        record = await self._execute_action(
                            record, index, writer, item, permit, readback_validator=validate
                        )
                        record = self._save_action(
                            record,
                            index,
                            phase=ActionPhase.VERIFIED,
                            diagnostic=_OBSERVED + observed[0],
                        )
                    finally:
                        await writer.close()
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                if record is not None:
                    self._require_recovery(operation_id)
                raise
        return self._finalize_prepared(record, intent, physical_write_count=4)

    def _prepare_intent(self, operation_id, preparation_speed, epoch, writer, raw, firmware):
        state = self._read_scalar_state(writer, epoch.target, raw)
        if not state.enabled or state.timer_enabled:
            raise QualificationProductionError("Aquarium preparation requires ON and TimerOFF")
        baseline_value = (
            preparation_speed
            if self._spec.function == "motor_speed"
            else self._read_baseline_value(writer, epoch.target, raw, state)
        )
        trial = AquariumScalarQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            function=self._spec.function,
            field=self._spec.field,
            baseline_value=baseline_value,
            trial_value=self._trial_value(epoch.target.product_key, baseline_value),
            baseline_motor_speed=preparation_speed,
            baseline_transport_mode=state.transport_mode,
            step_request_id=_phase_request_id(operation_id, "step"),
            restore_request_id=_phase_request_id(operation_id, "restore"),
            **epoch.model_dump(exclude={"target"}),
            firmware_version=firmware,
            created_at=self._now(),
        )
        return PreparedAquariumQualificationIntent(
            original_motor_speed=state.motor_speed, trial=trial
        )

    def _create_prepared_record(self, intent, prepared):
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
                restore_on_recovery=True,
            )
            for action_id, item in zip(_ACTION_IDS, prepared, strict=True)
        )
        record = LocalControlJournalRecord(
            request_id=intent.trial.operation_id,
            intent_sha256=self._journal.install_blob(_intent_bytes(intent)),
            created_at=self._now(),
            updated_at=self._now(),
            actions=actions,
        )
        self._journal.create(record)
        try:
            if self._journal.load(record.request_id) != record:
                raise QualificationProductionError(
                    "prepared qualification journal did not reload exactly"
                )
        except BaseException:
            self._require_recovery(record.request_id)
            raise
        return record

    def _load_prepared_intent(self, record):
        payload = self._journal.load_blob(record.intent_sha256)
        intent = PreparedAquariumQualificationIntent.model_validate_json(payload)
        if (
            _intent_bytes(intent) != payload
            or intent.trial.operation_id != record.request_id
            or intent.trial.function != self._spec.function
        ):
            raise QualificationProductionError("prepared qualification intent is not canonical")
        self._require_bar_target(intent.trial.target)
        return intent

    @staticmethod
    def _require_original_readback(intent, status):
        values = get_product_schema(intent.trial.target.product_key).decode_status(status)
        expected = {"SwitchON": True, "TimerON": False, "Motor_Speed": intent.original_motor_speed}
        if intent.trial.function == "feeding_duration":
            expected["FeedTime"] = intent.trial.baseline_value
        if any(values[name] != value for name, value in expected.items()):
            raise QualificationProductionError("Aquarium original restore readback did not match")

    def _validate_prepared_evidence(self, record, intent):
        trial = intent.trial
        if (
            record.phase is not TransactionPhase.VERIFIED
            or record.native_context_sha256 is not None
            or tuple(action.action_id for action in record.actions) != _ACTION_IDS
            or len({action.baseline_status_sha256 for action in record.actions}) != 1
        ):
            raise QualificationProductionError(
                "prepared qualification is not terminal with owned evidence"
            )
        schema = get_product_schema(trial.target.product_key)
        raw = self._journal.load_blob(record.actions[0].baseline_status_sha256)
        original = schema.decode_status(raw)
        if (
            original["SwitchON"] is not True
            or original["TimerON"] is not False
            or original["Motor_Speed"] != intent.original_motor_speed
            or original["Mode"] != trial.baseline_transport_mode
            or (
                trial.function == "feeding_duration"
                and original["FeedTime"] != trial.baseline_value
            )
        ):
            raise QualificationProductionError(
                "prepared qualification original differs from captured raw"
            )
        for index, (action, phase) in enumerate(
            zip(record.actions, _phase_intents(intent), strict=True)
        ):
            if (
                action.phase is not ActionPhase.VERIFIED
                or action.target != trial.target
                or action.function != phase.function
                or action.verification_kind != "scalar_fields"
                or not action.restore_on_recovery
                or action.acknowledgement_sha256 is None
                or not action.diagnostic
                or not action.diagnostic.startswith(_OBSERVED)
            ):
                raise QualificationProductionError(
                    "prepared qualification action evidence is incomplete"
                )
            field = "Motor_Speed" if phase.function == "motor_speed" else "FeedTime"
            expected, baseline = {field: phase.value}, {field: original[field]}
            for digest, payload in (
                (action.forward_payload_sha256, build_control_payload(schema, expected)),
                (action.rollback_payload_sha256, build_control_payload(schema, baseline)),
                (action.expected_readback_sha256, _canonical_json(expected)),
                (action.baseline_readback_sha256, _canonical_json(baseline)),
            ):
                if self._journal.load_blob(digest) != payload:
                    raise QualificationProductionError(
                        "prepared qualification payload evidence changed"
                    )
            self._journal.load_blob(action.acknowledgement_sha256)
            observed = self._journal.load_blob(action.diagnostic.removeprefix(_OBSERVED))
            values = schema.decode_status(observed)
            if any(values[name] != value for name, value in expected.items()):
                raise QualificationProductionError("preserved trial readback differs from intent")
            if index == 3:
                self._require_original_readback(intent, observed)
            else:
                if values["SwitchON"] is not True or values["TimerON"] is not False:
                    raise QualificationProductionError(
                        "preserved trial left the low-output envelope"
                    )
                _require_safe_motor_speed(
                    values["Motor_Speed"],
                    minimum=trial.min_power,
                    maximum=trial.max_power,
                    product_key=trial.target.product_key,
                )

    def _finalize_prepared(self, record, intent, *, physical_write_count):
        self._validate_prepared_evidence(record, intent)
        trial = intent.trial
        receipts = tuple(
            _bar_receipt(
                trial, record.actions[index], _bar_phase_intent(trial, index), record.updated_at
            )
            for index in (1, 2)
        )
        binding = QualificationBinding(
            product_key=trial.target.product_key,
            function=trial.function,
            app_version=trial.app_version,
            app_catalog_sha256=trial.app_catalog_sha256,
            template_sha256=trial.template_sha256,
            schema_sha256=trial.schema_sha256,
            firmware_version=trial.firmware_version,
            receipt_sha256=tuple(
                hashlib.sha256(qualification_receipt_bytes(item)).hexdigest() for item in receipts
            ),
        )
        qualification_sha256 = self._artifacts.finalize(receipts, binding)
        return QualificationProductionResult(
            operation_id=record.request_id,
            phase=record.phase,
            journal_sha256=self._journal.record_digest(record),
            target_id=hashlib.sha256(
                f"qualification-target:{trial.target.physical_identity_key}".encode("ascii")
            ).hexdigest(),
            receipt_count=2,
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )
