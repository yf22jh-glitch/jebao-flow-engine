"""DC Pump qualification that prepares a bounded manual state and restores the original.

The DC device this exists for runs its timer with a latent manual output well above the
first-write ceiling. The audited app reaches that situation with ordinary single-field writes
plus the two timer commands planned in :mod:`jebao_flow.local_control.dc_timer`, so this
producer arranges those existing frames and invents no compound.

Ordering contract, in the order it matters:

* every rollback is derived from the operation's own initial raw status, never from an
  intermediate snapshot, so recovery always aims at the state the operator started from;
* the trial restore returns the field to the **prepared** value, not to the original one,
  because the original manual output is only safe to write while the timer owns the pump;
* the original values are written after the timer is restored, and recovery is ordered so the
  timer restore is attempted first and verified before any original value is written.

When the device is found with its timer already off there is no timer frame to arrange: the
preparation, the trial and the restoration are the same owned writes without the two transitions.
The plan therefore branches on the operation's own initial reading and never on journal history.

Offline tests establish this software ordering. Nothing here asserts that the firmware adopts a
latent value, applies either timer command atomically, or accepts any write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from jebao_flow.local_control.bar_qualification import _bar_receipt, _BarScalarSpec
from jebao_flow.local_control.dc_qualification import DcScalarQualificationProducer
from jebao_flow.local_control.dc_timer import DcTimerScreen, prepare_dc_timer_command
from jebao_flow.local_control.device_writer import build_control_payload, get_product_schema
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    TransactionPhase,
)
from jebao_flow.local_control.models import (
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.qualification import qualification_receipt_bytes
from jebao_flow.local_control.qualification_producer import (
    _SAFE_FIRST_WRITE_MAXIMUM,
    ManualFlowQualificationProducer,
    QualificationProductionError,
    QualificationProductionResult,
    _canonical_json,
    _manual_flow_state,
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.schedule import DC_PUMP_PRO_PRODUCT_KEY

_FUNCTIONS = frozenset({"manual_flow", "manual_mode", "manual_frequency", "feeding_duration"})
_FIELD_BY_FUNCTION = {
    "manual_flow": "Flow",
    "manual_mode": "Mode",
    "manual_frequency": "Frequency",
    "feeding_duration": "FeedTime",
}
_REQUIRED_MODE = "constant"
_OBSERVED = "dc_status_sha256."


@dataclass(frozen=True, slots=True)
class DcPreparedAction:
    """One planned frame: its stable id and the intent that produces its forward payload."""

    action_id: str
    intent: LocalControlIntent


@dataclass(frozen=True, slots=True)
class DcPreparedPlan:
    """The arranged frames plus the recovery order their ordering contract requires."""

    actions: tuple[DcPreparedAction, ...]
    recovery_order: tuple[int, ...]
    recovery_prerequisites: tuple[int, ...]
    receipt_indexes: tuple[int, int]
    timer_initially_enabled: bool


class DcPreparedQualificationIntent(BaseModel):
    """Durable intent separating the captured original from the prepared trial state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["dc_prepared_scalar"] = "dc_prepared_scalar"
    operation_id: str
    target: LocalDeviceBinding
    function: Literal["manual_flow", "manual_mode", "manual_frequency", "feeding_duration"]
    field: str
    timer_initially_enabled: bool
    original_flow: StrictInt
    original_mode: str
    original_value: StrictInt | str
    preparation_flow: StrictInt
    trial_baseline_value: StrictInt | str
    trial_value: StrictInt | str
    action_ids: tuple[str, ...]
    recovery_order: tuple[int, ...]
    recovery_prerequisites: tuple[int, ...]
    step_request_id: str
    restore_request_id: str
    config_sha256: str
    manifest_sha256: str
    manifest_generation: str
    min_power: int
    max_power: int
    app_version: str
    app_catalog_sha256: str
    template_sha256: str
    schema_sha256: str
    firmware_version: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if self.target.product_key != DC_PUMP_PRO_PRODUCT_KEY:
            raise ValueError("prepared DC qualification target is not the DC Pump Pro")
        if self.field != _FIELD_BY_FUNCTION[self.function]:
            raise ValueError("prepared DC qualification field does not match its function")
        if self.trial_value == self.trial_baseline_value:
            raise ValueError("prepared DC qualification trial must change the value")
        _require_prepared_flow(
            self.preparation_flow,
            self.target.product_key,
            minimum=self.min_power,
            maximum=self.max_power,
        )
        _require_original_flow(
            self.original_flow,
            self.target.product_key,
            minimum=self.min_power,
            maximum=self.max_power,
        )
        if len(set(self.recovery_order)) != len(self.action_ids) or set(self.recovery_order) != set(
            range(len(self.action_ids))
        ):
            raise ValueError("prepared DC recovery order is not an action permutation")
        if any(index not in set(self.recovery_order) for index in self.recovery_prerequisites):
            raise ValueError("prepared DC recovery prerequisite is not an action")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("prepared DC qualification timestamp must be timezone-aware")
        return self


def _require_prepared_flow(flow: int, product_key: str, *, minimum: int, maximum: int) -> None:
    """The prepared output is a first write: the existing ceiling applies to it."""

    app_minimum, app_maximum = declared_app_integer_range(product_key, "manual_flow")
    if isinstance(flow, bool) or not isinstance(flow, int):
        raise ValueError("prepared DC flow must be an integer")
    if not app_minimum <= flow <= app_maximum:
        raise ValueError("prepared DC flow is outside the declared app range")
    if not minimum <= flow <= maximum:
        raise ValueError("prepared DC flow is outside the configured device limits")
    if flow > _SAFE_FIRST_WRITE_MAXIMUM:
        raise ValueError("prepared DC flow exceeds the first-write maximum")


def _require_original_flow(flow: int, product_key: str, *, minimum: int, maximum: int) -> None:
    """The captured original may exceed the trial ceiling; it may not leave app/config range."""

    app_minimum, app_maximum = declared_app_integer_range(product_key, "manual_flow")
    if isinstance(flow, bool) or not isinstance(flow, int):
        raise ValueError("original DC flow must be an integer")
    if not app_minimum <= flow <= app_maximum:
        raise ValueError("original DC flow is outside the declared app range")
    if not minimum <= flow <= maximum:
        raise ValueError("original DC flow is outside the configured device limits")


def prepared_dc_intent_bytes(intent: DcPreparedQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


def plan_dc_prepared_actions(
    *,
    operation_id: str,
    target: LocalDeviceBinding,
    function: str,
    original_flow: int,
    original_mode: str,
    original_value: int | str,
    preparation_flow: int,
    trial_baseline_value: int | str,
    trial_value: int | str,
    timer_initially_enabled: bool,
) -> DcPreparedPlan:
    """Arrange the owned frames and the recovery order their safety ordering requires."""

    def intent(action_id: str, name: str, value: object) -> DcPreparedAction:
        return DcPreparedAction(
            action_id=action_id,
            intent=LocalControlIntent(
                request_id=_phase_request_id(operation_id, action_id),
                target=target,
                function=name,
                value=value,
            ),
        )

    prepares_flow = original_flow != preparation_flow
    prepares_mode = original_mode != _REQUIRED_MODE
    actions: list[DcPreparedAction] = []
    if prepares_flow:
        actions.append(intent("prepare_flow", "manual_flow", preparation_flow))
    if prepares_mode:
        actions.append(intent("prepare_mode", "manual_mode", _REQUIRED_MODE))
    if timer_initially_enabled:
        actions.append(intent("enter_manual", "manual_enable", True))
    step_index = len(actions)
    actions.append(intent("trial_step", function, trial_value))
    actions.append(intent("trial_restore", function, trial_baseline_value))
    restore_index = step_index + 1
    timer_index: int | None = None
    if timer_initially_enabled:
        timer_index = len(actions)
        actions.append(intent("restore_timer", "schedule_enable", True))
    anchor = timer_index
    if prepares_mode:
        mode_restore_index = len(actions)
        actions.append(intent("restore_mode", "manual_mode", original_mode))
        if anchor is None:
            anchor = mode_restore_index
    if prepares_flow:
        actions.append(intent("restore_flow", "manual_flow", original_flow))

    indexes = list(range(len(actions)))
    if anchor is None:
        order = tuple(reversed(indexes))
    else:
        order = (anchor, *(index for index in reversed(indexes) if index != anchor))
    return DcPreparedPlan(
        actions=tuple(actions),
        recovery_order=order,
        recovery_prerequisites=() if anchor is None else (anchor,),
        receipt_indexes=(step_index, restore_index),
        timer_initially_enabled=timer_initially_enabled,
    )


class DcPreparedScalarQualificationProducer(DcScalarQualificationProducer):
    """Opt-in prepared qualification; the legacy three-frame DC admission is unchanged."""

    _functions = _FUNCTIONS

    def __init__(self, *, function: str, **kwargs: object) -> None:
        # The shared three-frame table has no manual_flow entry, so the spec is built here
        # instead of widening a file this producer does not own.
        if function not in _FUNCTIONS:
            raise QualificationProductionError("function is outside this product producer's scope")
        ManualFlowQualificationProducer.__init__(self, **kwargs)  # type: ignore[arg-type]
        self._spec = _BarScalarSpec(
            function=function,
            field=_FIELD_BY_FUNCTION[function],
            value_kind="enum" if function == "manual_mode" else "integer",
        )

    async def execute(  # type: ignore[override]
        self, operation_id: str, *, preparation_flow: int
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            intent = self._load_prepared_intent(existing)
            if type(preparation_flow) is not int or intent.preparation_flow != preparation_flow:
                raise QualificationProductionError(
                    "prepared DC qualification request has different intent"
                )
            return self._finalize_prepared(existing, intent, physical_write_count=0)

        epoch = self._epoch_provider()
        self._require_bar_target(epoch.target)
        _require_prepared_flow(
            preparation_flow,
            epoch.target.product_key,
            minimum=epoch.min_power,
            maximum=epoch.max_power,
        )
        record: LocalControlJournalRecord | None = None
        intent = None
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError(
                    "prepared DC qualification appeared concurrently"
                )
            try:
                endpoint, firmware = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                try:
                    initial = await writer.connect_and_read()
                    intent, plan = self._prepare_plan(
                        operation_id, preparation_flow, epoch, writer, initial, firmware
                    )
                    prepared = tuple(
                        writer.prepare_scalar(action.intent, initial) for action in plan.actions
                    )
                    record = self._create_prepared_record(intent, plan, prepared)
                finally:
                    await writer.close()

                for index, action in enumerate(plan.actions):
                    self._require_epoch(epoch)
                    endpoint, _firmware = await self._resolve(epoch, firmware)
                    writer = self._writer_factory(endpoint)
                    try:
                        current = await writer.connect_and_read()
                        values = get_product_schema(epoch.target.product_key).decode_status(current)
                        if (
                            action.action_id == "restore_flow"
                            and intent.timer_initially_enabled
                            and values["TimerON"] is not True
                        ):
                            raise QualificationProductionError(
                                "original latent output requires the owned timer restoration first"
                            )
                        fresh = writer.prepare_scalar(action.intent, current)
                        item = self._prepared_from_action(record.actions[index], endpoint)
                        if fresh.forward_payload != item.forward_payload:
                            raise QualificationProductionError("prepared DC phase payload changed")
                        observed: list[str] = []

                        def validate(readback, *, observed=observed):
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
        return self._finalize_prepared(record, intent, physical_write_count=len(plan.actions))

    def _prepare_plan(self, operation_id, preparation_flow, epoch, writer, initial, firmware):
        state = _manual_flow_state(writer, epoch.target, initial)
        if state.enabled is not True:
            raise QualificationProductionError("prepared DC qualification requires the pump on")
        values = get_product_schema(epoch.target.product_key).decode_status(initial)
        field = _FIELD_BY_FUNCTION[self._spec.function]
        original_value = values[field]
        trial_baseline = self._trial_baseline_value(preparation_flow, values)
        trial_value = self._prepared_trial_value(epoch.target.product_key, trial_baseline)
        plan = plan_dc_prepared_actions(
            operation_id=operation_id,
            target=epoch.target,
            function=self._spec.function,
            original_flow=state.flow,
            original_mode=state.mode,
            original_value=original_value,
            preparation_flow=preparation_flow,
            trial_baseline_value=trial_baseline,
            trial_value=trial_value,
            timer_initially_enabled=state.timer_enabled,
        )
        intent = DcPreparedQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            function=self._spec.function,
            field=field,
            timer_initially_enabled=state.timer_enabled,
            original_flow=state.flow,
            original_mode=state.mode,
            original_value=original_value,
            preparation_flow=preparation_flow,
            trial_baseline_value=trial_baseline,
            trial_value=trial_value,
            action_ids=tuple(action.action_id for action in plan.actions),
            recovery_order=plan.recovery_order,
            recovery_prerequisites=plan.recovery_prerequisites,
            step_request_id=plan.actions[plan.receipt_indexes[0]].intent.request_id,
            restore_request_id=plan.actions[plan.receipt_indexes[1]].intent.request_id,
            **epoch.model_dump(exclude={"target"}),
            firmware_version=firmware,
            created_at=self._now(),
        )
        return intent, plan

    def _trial_baseline_value(self, preparation_flow: int, values) -> int | str:
        """The state the trial pair starts from, not the operator's original state."""

        function = self._spec.function
        if function == "manual_flow":
            return preparation_flow
        if function == "manual_mode":
            return _REQUIRED_MODE
        return values[_FIELD_BY_FUNCTION[function]]

    def _prepared_trial_value(self, product_key: str, baseline: int | str) -> int | str:
        if self._spec.function == "manual_flow":
            minimum, _maximum = declared_app_integer_range(product_key, "manual_flow")
            return baseline - 1 if baseline > minimum else baseline + 1
        return self._trial_value(product_key, baseline)

    def _create_prepared_record(self, intent, plan, prepared):
        actions = tuple(
            JournalAction(
                action_id=action.action_id,
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
            for action, item in zip(plan.actions, prepared, strict=True)
        )
        now = self._now()
        record = LocalControlJournalRecord(
            request_id=intent.operation_id,
            intent_sha256=self._journal.install_blob(prepared_dc_intent_bytes(intent)),
            created_at=now,
            updated_at=now,
            actions=actions,
            recovery_order=plan.recovery_order,
            recovery_prerequisites=plan.recovery_prerequisites,
        )
        self._journal.create(record)
        if self._journal.load(record.request_id) != record:
            self._require_recovery(record.request_id)
            raise QualificationProductionError("prepared DC journal did not reload exactly")
        return record

    def _load_prepared_intent(self, record):
        payload = self._journal.load_blob(record.intent_sha256)
        intent = DcPreparedQualificationIntent.model_validate_json(payload)
        if (
            prepared_dc_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
            or intent.function != self._spec.function
        ):
            raise QualificationProductionError("prepared DC intent is not canonical")
        self._require_bar_target(intent.target)
        return intent

    def _validate_prepared_evidence(self, record, intent) -> None:
        if (
            record.phase is not TransactionPhase.VERIFIED
            or record.native_context_sha256 is not None
            or tuple(action.action_id for action in record.actions) != intent.action_ids
            or len({action.baseline_status_sha256 for action in record.actions}) != 1
            or record.recovery_order != intent.recovery_order
            or record.recovery_prerequisites != intent.recovery_prerequisites
        ):
            raise QualificationProductionError(
                "prepared DC qualification is not terminal with owned evidence"
            )
        schema = get_product_schema(intent.target.product_key)
        initial = self._journal.load_blob(record.actions[0].baseline_status_sha256)
        original = schema.decode_status(initial)
        if (
            original["Flow"] != intent.original_flow
            or original["Mode"] != intent.original_mode
            or original["TimerON"] != intent.timer_initially_enabled
            or original[intent.field] != intent.original_value
            or original["SwitchON"] is not True
        ):
            raise QualificationProductionError("prepared DC original differs from owned raw status")
        trial_baseline = self._trial_baseline_value(intent.preparation_flow, original)
        if (
            intent.trial_baseline_value != trial_baseline
            or intent.trial_value
            != self._prepared_trial_value(intent.target.product_key, trial_baseline)
        ):
            raise QualificationProductionError(
                "prepared DC trial differs from the declared app step"
            )
        plan = plan_dc_prepared_actions(
            operation_id=intent.operation_id,
            target=intent.target,
            function=intent.function,
            original_flow=intent.original_flow,
            original_mode=intent.original_mode,
            original_value=intent.original_value,
            preparation_flow=intent.preparation_flow,
            trial_baseline_value=trial_baseline,
            trial_value=intent.trial_value,
            timer_initially_enabled=intent.timer_initially_enabled,
        )
        if (
            plan.recovery_order != record.recovery_order
            or plan.recovery_prerequisites != record.recovery_prerequisites
            or tuple(item.action_id for item in plan.actions) != intent.action_ids
        ):
            raise QualificationProductionError("prepared DC owned plan changed")
        for action, planned in zip(record.actions, plan.actions, strict=True):
            if (
                action.phase is not ActionPhase.VERIFIED
                or action.acknowledgement_sha256 is None
                or not action.diagnostic
                or not action.diagnostic.startswith(_OBSERVED)
                or not action.restore_on_recovery
                or action.target != intent.target
                or action.function != planned.intent.function
                or action.verification_kind != "scalar_fields"
            ):
                raise QualificationProductionError("prepared DC action evidence is incomplete")
            self._journal.load_blob(action.acknowledgement_sha256)
            if action.function in {"schedule_enable", "manual_enable"}:
                screen = (
                    DcTimerScreen.SCHEDULE
                    if action.function == "schedule_enable"
                    else DcTimerScreen.MANUAL
                )
                fields = dict(
                    prepare_dc_timer_command(
                        screen=screen,
                        desired_timer_enabled=(
                            planned.intent.value
                            if screen is DcTimerScreen.SCHEDULE
                            else not planned.intent.value
                        ),
                    ).fields
                )
            else:
                fields = {_FIELD_BY_FUNCTION[action.function]: planned.intent.value}
            baseline = {name: original[name] for name in fields}
            for digest, expected in (
                (action.forward_payload_sha256, build_control_payload(schema, fields)),
                (action.rollback_payload_sha256, build_control_payload(schema, baseline)),
                (action.expected_readback_sha256, _canonical_json(fields)),
                (action.baseline_readback_sha256, _canonical_json(baseline)),
            ):
                if self._journal.load_blob(digest) != expected:
                    raise QualificationProductionError("prepared DC owned command evidence changed")
            observed = schema.decode_status(
                self._journal.load_blob(action.diagnostic[len(_OBSERVED) :])
            )
            if any(observed[name] != value for name, value in fields.items()):
                raise QualificationProductionError("prepared DC sent field was not verified")
        final = schema.decode_status(
            self._journal.load_blob(record.actions[-1].diagnostic[len(_OBSERVED) :])
        )
        owned = {"Flow", "Mode", "TimerON", "SwitchON", intent.field}
        if any(final[name] != original[name] for name in owned):
            raise QualificationProductionError("prepared DC owned original was not restored")

    def _finalize_prepared(self, record, intent, *, physical_write_count):
        self._validate_prepared_evidence(record, intent)
        step_index = intent.action_ids.index("trial_step")
        restore_index = intent.action_ids.index("trial_restore")
        receipts = tuple(
            _bar_receipt(intent, record.actions[index], phase_intent, record.updated_at)
            for index, phase_intent in (
                (step_index, _prepared_phase_intent(intent, "trial_step")),
                (restore_index, _prepared_phase_intent(intent, "trial_restore")),
            )
        )
        binding = QualificationBinding(
            product_key=intent.target.product_key,
            function=intent.function,
            app_version=intent.app_version,
            app_catalog_sha256=intent.app_catalog_sha256,
            template_sha256=intent.template_sha256,
            schema_sha256=intent.schema_sha256,
            firmware_version=intent.firmware_version,
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
                f"qualification-target:{intent.target.physical_identity_key}".encode("ascii")
            ).hexdigest(),
            receipt_count=2,
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )


def _prepared_phase_intent(
    intent: DcPreparedQualificationIntent, action_id: str
) -> LocalControlIntent:
    value = intent.trial_value if action_id == "trial_step" else intent.trial_baseline_value
    request_id = intent.step_request_id if action_id == "trial_step" else intent.restore_request_id
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=intent.function,
        value=value,
    )


def _canonical_loads(payload: bytes) -> dict:
    import json

    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


__all__ = [
    "DcPreparedQualificationIntent",
    "DcPreparedScalarQualificationProducer",
    "plan_dc_prepared_actions",
    "prepared_dc_intent_bytes",
]
