"""DC Pump timer and schedule qualification with an owned preparation and restoration.

The two timer functions and the schedule functions all need the same wrapper: the manual output
is brought inside the trial ceiling while it is still safe to move, the trial runs, and then the
device is returned to the exact state the operation found.

**Physical consequence that cannot be designed away.** Both audited DC timer commands couple
power to the timer, and they disagree about the direction, so for either function the ``False``
frame plans ``SwitchON=false``:

* ``schedule_enable=False`` plans ``{TimerON: false, SwitchON: false}``;
* ``manual_enable=False`` plans ``{TimerON: true, SwitchON: false}``.

A two-frame qualification of either function therefore stops the pump between its two frames.
That is the app's own contract, not a choice made here, and it is why this module states it
instead of hiding it. Every rollback restores the initial power state, so a failure anywhere in
the operation converges back to the pump running.

Ordering, in the order it matters:

* every rollback derives from the operation's own initial raw status, never an intermediate one;
* the trial installs a bounded whole-day image before the timer is allowed to run, so the timer
  frames cannot start an unbounded stored schedule;
* restoration is timer/power first, then the original image, then the original manual mode and
  output, and recovery is ordered the same way with the timer restore as a prerequisite.

Offline tests establish this ordering. Nothing here asserts firmware acceptance, atomic
application of any command, or that a latent value is adopted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from jebao_flow.local_control.bar_qualification import _bar_receipt
from jebao_flow.local_control.catalog import build_local_control_catalog, internal_function_value
from jebao_flow.local_control.dc_qualification import DcScalarQualificationProducer
from jebao_flow.local_control.dc_timer import DcTimerScreen, prepare_dc_timer_command
from jebao_flow.local_control.device_writer import (
    _SCHEDULE_GEOMETRY_BY_PRODUCT,
    PreparedDeviceWrite,
    _canonical_values,
    build_control_payload,
    build_exact_schedule_payload,
    extract_current_schedule_image,
    get_product_schema,
)
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
from jebao_flow.local_control.pump_preview import ReturnPumpSchedule
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

_TIMER_FUNCTIONS = frozenset({"schedule_enable", "manual_enable"})
_SCHEDULE_FUNCTIONS = frozenset({"schedule", "schedule_reset"})
_FUNCTIONS = _TIMER_FUNCTIONS | _SCHEDULE_FUNCTIONS
_REQUIRED_MODE = "constant"
_SCALAR_FIELD_BY_FUNCTION = {"manual_flow": "Flow", "manual_mode": "Mode"}
_UNUSED_SLOT = b"\xee" * 8
_OBSERVED = "dc_program_status_sha256."
_RECEIPT_ACTION_IDS = {
    "schedule_enable": ("trial_off", "trial_on"),
    "manual_enable": ("trial_off", "trial_on"),
    "schedule": ("trial_a", "trial_b"),
    "schedule_reset": ("trial_reset_a", "trial_reset_b"),
}


def bounded_trial_schedule(flow: int) -> ReturnPumpSchedule:
    """A whole-day constant image at one bounded output, so a running timer stays inside it."""

    if isinstance(flow, bool) or not isinstance(flow, int):
        raise QualificationProductionError("DC trial image requires an integer flow")
    slots = []
    for index in range(48):
        start = f"{index // 2:02d}:{'30' if index % 2 else '00'}"
        end_index = index + 1
        end = f"{end_index // 2:02d}:{'30' if end_index % 2 else '00'}"
        slots.append(
            {
                "slot_index": index,
                "start": start,
                "end": end,
                "mode": _REQUIRED_MODE,
                "flow": flow,
                "frequency": 0,
                "feed_time": 0,
            }
        )
    return ReturnPumpSchedule.model_validate({"family": "dc_pro", "slots": slots})


def restore_timer_power_intent(
    *,
    operation_id: str,
    action_id: str,
    target: LocalDeviceBinding,
    timer_enabled: bool,
    power_enabled: bool,
) -> LocalControlIntent:
    """Name the one app command that reproduces this exact timer/power pair.

    All four pairs are reachable: the Schedule screen writes power equal to the timer and the
    Manual screen writes it inverted, so each pair has exactly one owning command.
    """

    if timer_enabled == power_enabled:
        function, value = "schedule_enable", timer_enabled
    else:
        function, value = "manual_enable", power_enabled
    return LocalControlIntent(
        request_id=_phase_request_id(operation_id, action_id),
        target=target,
        function=function,
        value=value,
    )


@dataclass(frozen=True, slots=True)
class DcProgramAction:
    action_id: str
    intent: LocalControlIntent


@dataclass(frozen=True, slots=True)
class DcProgramPlan:
    actions: tuple[DcProgramAction, ...]
    recovery_order: tuple[int, ...]
    recovery_prerequisites: tuple[int, ...]
    receipt_indexes: tuple[int, int]


def require_bounded_original_image(image: bytes, *, minimum: int, maximum: int) -> None:
    """The original image becomes live again when the original timer state is restored."""

    geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[DC_PUMP_PRO_PRODUCT_KEY]
    if len(image) != geometry.image_size:
        raise QualificationProductionError("DC original image has the wrong size")
    for index in range(48):
        slot = image[index * 8 : (index + 1) * 8]
        if slot == _UNUSED_SLOT or not any(slot[:4]) or slot[4] == 4:
            continue
        if not minimum <= slot[5] <= maximum:
            raise QualificationProductionError(
                "DC original schedule output is outside the configured limits"
            )


def require_bounded_preparation_flow(flow: int, *, minimum: int, maximum: int) -> None:
    app_minimum, app_maximum = declared_app_integer_range(DC_PUMP_PRO_PRODUCT_KEY, "manual_flow")
    if isinstance(flow, bool) or not isinstance(flow, int):
        raise QualificationProductionError("DC preparation flow must be an integer")
    if not app_minimum <= flow <= app_maximum:
        raise QualificationProductionError("DC preparation flow is outside the declared app range")
    if not minimum <= flow <= maximum:
        raise QualificationProductionError("DC preparation flow is outside the device limits")
    if flow > _SAFE_FIRST_WRITE_MAXIMUM:
        raise QualificationProductionError("DC preparation flow exceeds the first-write maximum")


def plan_dc_program_actions(
    *,
    operation_id: str,
    target: LocalDeviceBinding,
    function: str,
    original_flow: int,
    original_mode: str,
    original_timer_enabled: bool,
    original_power_enabled: bool,
    preparation_flow: int,
    trial_action_ids: tuple[str, ...],
) -> DcProgramPlan:
    """Arrange preparation, the trial frames and the owned restoration."""

    if function not in _FUNCTIONS:
        raise QualificationProductionError("function is outside this DC program producer's scope")

    def action(action_id: str, name: str, value: object) -> DcProgramAction:
        return DcProgramAction(
            action_id=action_id,
            intent=LocalControlIntent(
                request_id=_phase_request_id(operation_id, action_id),
                target=target,
                function=name,
                value=value,
            ),
        )

    def image_action(action_id: str) -> DcProgramAction:
        # The journal keeps the function the frame really writes, never the qualified name.
        return action(action_id, _IMAGE_FUNCTION_BY_ACTION[action_id], action_id)

    prepares_flow = original_flow != preparation_flow
    prepares_mode = original_mode != _REQUIRED_MODE
    actions: list[DcProgramAction] = []
    if prepares_flow:
        actions.append(action("prepare_flow", "manual_flow", preparation_flow))
    if prepares_mode:
        actions.append(action("prepare_mode", "manual_mode", _REQUIRED_MODE))

    if function in _TIMER_FUNCTIONS:
        # The timer is about to run, so a bounded whole-day image is installed first.
        actions.append(image_action(trial_action_ids[0]))
        first = len(actions)
        actions.append(action("trial_off", function, False))
        actions.append(action("trial_on", function, True))
        receipts = (first, first + 1)
    else:
        # Schedule writes stay latent while the device is in manual operation.
        actions.append(action("enter_manual_first", "manual_enable", True))
        first = len(actions)
        for action_id in trial_action_ids:
            actions.append(image_action(action_id))
        receipts = (first, first + len(trial_action_ids) - 1)

    if function in _TIMER_FUNCTIONS:
        # The last trial frame leaves the timer running; the schedule branch never started it.
        actions.append(action("enter_manual", "manual_enable", True))
    image_index = len(actions)
    actions.append(image_action("restore_image"))
    timer_index = len(actions)
    actions.append(
        DcProgramAction(
            action_id="restore_timer_power",
            intent=restore_timer_power_intent(
                operation_id=operation_id,
                action_id="restore_timer_power",
                target=target,
                timer_enabled=original_timer_enabled,
                power_enabled=original_power_enabled,
            ),
        )
    )
    mode_index = flow_index = None
    if prepares_mode:
        mode_index = len(actions)
        actions.append(action("restore_mode", "manual_mode", original_mode))
    if prepares_flow:
        flow_index = len(actions)
        actions.append(action("restore_flow", "manual_flow", original_flow))

    anchors = [timer_index, image_index]
    anchors.extend(index for index in (mode_index, flow_index) if index is not None)
    remaining = [index for index in reversed(range(len(actions))) if index not in anchors]
    return DcProgramPlan(
        actions=tuple(actions),
        recovery_order=tuple(anchors) + tuple(remaining),
        recovery_prerequisites=(timer_index,),
        receipt_indexes=receipts,
    )


class DcProgramQualificationIntent(BaseModel):
    """Durable intent separating the captured original from the bounded trial state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["dc_program"] = "dc_program"
    operation_id: str
    target: LocalDeviceBinding
    function: Literal["schedule_enable", "manual_enable", "schedule", "schedule_reset"]
    original_flow: StrictInt
    original_mode: str
    original_timer_enabled: bool
    original_power_enabled: Literal[True] = True
    original_image_sha256: str
    trial_image_sha256: tuple[str, ...]
    preparation_flow: StrictInt
    trial_flow: StrictInt
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
            raise ValueError("DC program qualification target is not the DC Pump Pro")
        # Pydantic reports a ValueError as a field problem; the producer error type is not one.
        for value in (self.preparation_flow, self.trial_flow):
            try:
                require_bounded_preparation_flow(
                    value, minimum=self.min_power, maximum=self.max_power
                )
            except QualificationProductionError as error:
                raise ValueError(str(error)) from error
        if set(self.recovery_order) != set(range(len(self.action_ids))):
            raise ValueError("DC program recovery order is not an action permutation")
        if len(self.recovery_order) != len(self.action_ids):
            raise ValueError("DC program recovery order repeats an action")
        if any(index >= len(self.action_ids) for index in self.recovery_prerequisites):
            raise ValueError("DC program recovery prerequisite is not an action")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("DC program qualification timestamp must be timezone-aware")
        return self


def dc_program_intent_bytes(intent: DcProgramQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


def original_image_of(status: bytes) -> bytes:
    return extract_current_schedule_image(DC_PUMP_PRO_PRODUCT_KEY, status)


def decode_dc_status(status: bytes) -> dict:
    return get_product_schema(DC_PUMP_PRO_PRODUCT_KEY).decode_status(status)


__all__ = [
    "DcProgramAction",
    "DcProgramPlan",
    "DcProgramQualificationIntent",
    "bounded_trial_schedule",
    "dc_program_intent_bytes",
    "plan_dc_program_actions",
    "require_bounded_original_image",
    "require_bounded_preparation_flow",
    "restore_timer_power_intent",
]


@dataclass(frozen=True, slots=True)
class DcProgramImages:
    """The exact images this operation owns, in the order their trial frames write them."""

    original: bytes
    trial: tuple[bytes, ...]
    trial_action_ids: tuple[str, ...]


def require_bounded_trial_image(image: bytes, *, minimum: int, maximum: int) -> None:
    """A trial image may become live, so every runnable slot obeys the trial ceiling."""

    ceiling = min(maximum, _SAFE_FIRST_WRITE_MAXIMUM)
    geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[DC_PUMP_PRO_PRODUCT_KEY]
    if len(image) != geometry.image_size:
        raise QualificationProductionError("DC trial image has the wrong size")
    for index in range(48):
        slot = image[index * 8 : (index + 1) * 8]
        if slot == _UNUSED_SLOT or not any(slot[:4]) or slot[4] == 4:
            continue
        if not minimum <= slot[5] <= ceiling:
            raise QualificationProductionError(
                "DC trial schedule output is outside the trial limits"
            )


def build_program_images(
    *,
    function: str,
    original: bytes,
    preparation_flow: int,
    schedule_a: bytes | None,
    schedule_b: bytes | None,
    minimum: int,
    maximum: int,
) -> DcProgramImages:
    """Choose the trial images the requested function needs and keep the original exact."""

    geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[DC_PUMP_PRO_PRODUCT_KEY]
    if len(original) != geometry.image_size:
        raise QualificationProductionError("DC original image has the wrong size")
    cleared = _UNUSED_SLOT * 48
    if function in _TIMER_FUNCTIONS:
        if schedule_a is not None or schedule_b is not None:
            raise QualificationProductionError("DC timer qualification takes no schedule images")
        bounded = bounded_trial_schedule(preparation_flow).compile_image()
        require_bounded_trial_image(bounded, minimum=minimum, maximum=maximum)
        return DcProgramImages(
            original=bytes(original), trial=(bounded,), trial_action_ids=("trial_image",)
        )
    if function == "schedule_reset":
        if schedule_a is None or schedule_b is not None:
            raise QualificationProductionError("DC schedule reset takes one explicit image")
        if bytes(schedule_a) == cleared:
            raise QualificationProductionError("DC schedule reset image must differ from cleared")
        require_bounded_trial_image(schedule_a, minimum=minimum, maximum=maximum)
        # The audited reset contract proves clearing twice around a populated image.
        return DcProgramImages(
            original=bytes(original),
            trial=(cleared, bytes(schedule_a), cleared),
            trial_action_ids=("trial_reset_a", "trial_populate", "trial_reset_b"),
        )
    if schedule_a is None or schedule_b is None:
        raise QualificationProductionError("DC schedule qualification requires both images")
    if bytes(schedule_a) == bytes(schedule_b):
        raise QualificationProductionError("DC schedule images must be distinct")
    for image in (schedule_a, schedule_b):
        require_bounded_trial_image(image, minimum=minimum, maximum=maximum)
    return DcProgramImages(
        original=bytes(original),
        trial=(bytes(schedule_a), bytes(schedule_b)),
        trial_action_ids=("trial_a", "trial_b"),
    )


_IMAGE_FUNCTION_BY_ACTION = {
    "trial_image": "schedule",
    "trial_a": "schedule",
    "trial_b": "schedule",
    "trial_reset_a": "schedule_reset",
    "trial_populate": "schedule",
    "trial_reset_b": "schedule_reset",
    "restore_image": "schedule",
}


def image_for_action(action_id: str, images: DcProgramImages) -> bytes:
    """Map an image-writing action to the exact bytes it owns."""

    if action_id == "restore_image":
        return images.original
    try:
        return images.trial[images.trial_action_ids.index(action_id)]
    except ValueError as error:
        raise QualificationProductionError("action does not own a schedule image") from error


class DcProgramQualificationProducer(DcScalarQualificationProducer):
    """Run one DC timer or schedule qualification inside its own preparation and restoration."""

    _functions = _FUNCTIONS

    def __init__(
        self,
        *,
        function: str,
        schedule_a: bytes | None = None,
        schedule_b: bytes | None = None,
        **kwargs: object,
    ) -> None:
        if function not in _FUNCTIONS:
            raise QualificationProductionError(
                "function is outside this DC program producer's scope"
            )
        ManualFlowQualificationProducer.__init__(self, **kwargs)  # type: ignore[arg-type]
        self._function = function
        self._schedule_a = schedule_a
        self._schedule_b = schedule_b

    async def execute(  # type: ignore[override]
        self, operation_id: str, *, preparation_flow: int
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            intent = self._load_program_intent(existing)
            if type(preparation_flow) is not int or intent.preparation_flow != preparation_flow:
                raise QualificationProductionError(
                    "DC program qualification request has different intent"
                )
            self._require_same_trial_images(intent, preparation_flow)
            return self._finalize_program(existing, intent, physical_write_count=0)

        epoch = self._epoch_provider()
        self._require_program_target(epoch.target)
        require_bounded_preparation_flow(
            preparation_flow, minimum=epoch.min_power, maximum=epoch.max_power
        )
        record: LocalControlJournalRecord | None = None
        intent = None
        plan = None
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("DC program qualification appeared concurrently")
            try:
                endpoint, firmware = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                try:
                    initial = await writer.connect_and_read()
                    intent, plan, prepared = self._prepare_program(
                        operation_id, preparation_flow, epoch, writer, initial, firmware
                    )
                    record = self._create_program_record(intent, plan, prepared)
                finally:
                    await writer.close()

                for index in range(len(plan.actions)):
                    self._require_epoch(epoch)
                    endpoint, _firmware = await self._resolve(epoch, firmware)
                    writer = self._writer_factory(endpoint)
                    try:
                        current = await writer.connect_and_read()
                        item = self._prepared_from_action(record.actions[index], endpoint)
                        action = plan.actions[index]
                        if item.verification_kind != "schedule_image":
                            fresh = writer.prepare_scalar(action.intent, current)
                            if fresh.forward_payload != item.forward_payload:
                                raise QualificationProductionError(
                                    "DC program phase payload changed"
                                )
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
        return self._finalize_program(record, intent, physical_write_count=len(plan.actions))

    def _require_same_trial_images(
        self, intent: DcProgramQualificationIntent, preparation_flow: int
    ) -> None:
        """A replay may not silently swap the images the stored intent already bound."""

        images = build_program_images(
            function=intent.function,
            original=bytes(_UNUSED_SLOT * 48),
            preparation_flow=preparation_flow,
            schedule_a=self._schedule_a,
            schedule_b=self._schedule_b,
            minimum=intent.min_power,
            maximum=intent.max_power,
        )
        digests = tuple(hashlib.sha256(image).hexdigest() for image in images.trial)
        if digests != intent.trial_image_sha256:
            raise QualificationProductionError(
                "DC program qualification request has different trial images"
            )

    def _require_program_target(self, target: LocalDeviceBinding) -> None:
        if target.product_key != DC_PUMP_PRO_PRODUCT_KEY:
            raise QualificationProductionError("DC program qualification target is not current")
        descriptor = build_local_control_catalog().find(target.product_key, self._function)
        if not descriptor.encoding_available:
            raise QualificationProductionError("DC program function has no audited encoder")

    def _prepare_program(self, operation_id, preparation_flow, epoch, writer, initial, firmware):
        state = _manual_flow_state(writer, epoch.target, initial)
        if state.enabled is not True:
            raise QualificationProductionError("DC program qualification requires the pump on")
        original_image = original_image_of(initial)
        require_bounded_original_image(
            original_image, minimum=epoch.min_power, maximum=epoch.max_power
        )
        images = build_program_images(
            function=self._function,
            original=original_image,
            preparation_flow=preparation_flow,
            schedule_a=self._schedule_a,
            schedule_b=self._schedule_b,
            minimum=epoch.min_power,
            maximum=epoch.max_power,
        )
        plan = plan_dc_program_actions(
            operation_id=operation_id,
            target=epoch.target,
            function=self._function,
            original_flow=state.flow,
            original_mode=state.mode,
            original_timer_enabled=state.timer_enabled,
            original_power_enabled=True,
            preparation_flow=preparation_flow,
            trial_action_ids=images.trial_action_ids,
        )
        prepared = tuple(
            self._prepare_action(action, writer, epoch, initial, images, original_image)
            for action in plan.actions
        )
        intent = DcProgramQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            function=self._function,
            original_flow=state.flow,
            original_mode=state.mode,
            original_timer_enabled=state.timer_enabled,
            original_image_sha256=hashlib.sha256(original_image).hexdigest(),
            trial_image_sha256=tuple(hashlib.sha256(image).hexdigest() for image in images.trial),
            preparation_flow=preparation_flow,
            trial_flow=preparation_flow,
            action_ids=tuple(action.action_id for action in plan.actions),
            recovery_order=plan.recovery_order,
            recovery_prerequisites=plan.recovery_prerequisites,
            step_request_id=plan.actions[plan.receipt_indexes[0]].intent.request_id,
            restore_request_id=plan.actions[plan.receipt_indexes[1]].intent.request_id,
            **epoch.model_dump(exclude={"target"}),
            firmware_version=firmware,
            created_at=self._now(),
        )
        return intent, plan, prepared

    def _prepare_action(self, action, writer, epoch, initial, images, original_image):
        if action.action_id in _IMAGE_FUNCTION_BY_ACTION:
            image = image_for_action(action.action_id, images)
            payload = build_exact_schedule_payload(epoch.target.product_key, image)
            return PreparedDeviceWrite(
                target=writer.endpoint,
                function=action.intent.function,
                forward_payload=payload,
                rollback_payload=build_exact_schedule_payload(
                    epoch.target.product_key, original_image
                ),
                baseline_status=bytes(initial),
                expected_readback=image,
                baseline_readback=original_image,
                verification_kind="schedule_image",
                no_change=False,
            )
        return writer.prepare_scalar(action.intent, initial)

    def _create_program_record(self, intent, plan, prepared):
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
            intent_sha256=self._journal.install_blob(dc_program_intent_bytes(intent)),
            created_at=now,
            updated_at=now,
            actions=actions,
            recovery_order=plan.recovery_order,
            recovery_prerequisites=plan.recovery_prerequisites,
        )
        self._journal.create(record)
        if self._journal.load(record.request_id) != record:
            self._require_recovery(record.request_id)
            raise QualificationProductionError("DC program journal did not reload exactly")
        return record

    def _load_program_intent(self, record):
        payload = self._journal.load_blob(record.intent_sha256)
        intent = DcProgramQualificationIntent.model_validate_json(payload)
        if (
            dc_program_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
            or intent.function != self._function
        ):
            raise QualificationProductionError("DC program intent is not canonical")
        return intent

    def _validate_program_evidence(self, record, intent) -> None:
        if (
            record.phase is not TransactionPhase.VERIFIED
            or record.native_context_sha256 is not None
            or tuple(action.action_id for action in record.actions) != intent.action_ids
            or len({action.baseline_status_sha256 for action in record.actions}) != 1
            or record.recovery_order != intent.recovery_order
            or record.recovery_prerequisites != intent.recovery_prerequisites
        ):
            raise QualificationProductionError(
                "DC program qualification is not terminal with owned evidence"
            )
        raw = self._journal.load_blob(record.actions[0].baseline_status_sha256)
        original_image = original_image_of(raw)
        if hashlib.sha256(original_image).hexdigest() != intent.original_image_sha256:
            raise QualificationProductionError("DC program original image evidence changed")
        values = decode_dc_status(raw)
        if (
            values["SwitchON"] is not True
            or values["TimerON"] is not intent.original_timer_enabled
            or values["Flow"] != intent.original_flow
            or values["Mode"] != intent.original_mode
        ):
            raise QualificationProductionError("DC program original differs from captured raw")
        images = self._rebuild_images(intent, original_image)
        plan = plan_dc_program_actions(
            operation_id=intent.operation_id,
            target=intent.target,
            function=intent.function,
            original_flow=intent.original_flow,
            original_mode=intent.original_mode,
            original_timer_enabled=intent.original_timer_enabled,
            original_power_enabled=True,
            preparation_flow=intent.preparation_flow,
            trial_action_ids=images.trial_action_ids,
        )
        if tuple(action.action_id for action in plan.actions) != intent.action_ids:
            raise QualificationProductionError("DC program plan no longer matches its intent")
        schema = get_product_schema(intent.target.product_key)
        for action, planned in zip(record.actions, plan.actions, strict=True):
            if (
                action.phase is not ActionPhase.VERIFIED
                or action.acknowledgement_sha256 is None
                or not action.diagnostic
                or not action.diagnostic.startswith(_OBSERVED)
                or not action.restore_on_recovery
                or action.function != planned.intent.function
            ):
                raise QualificationProductionError("DC program action evidence is incomplete")
            self._require_rebuilt_bytes(action, planned, images, original_image, raw, schema)
            observed = self._journal.load_blob(action.diagnostic.removeprefix(_OBSERVED))
            expected = self._journal.load_blob(action.expected_readback_sha256)
            if action.verification_kind == "schedule_image":
                if original_image_of(observed) != expected:
                    raise QualificationProductionError(
                        "preserved DC image readback differs from the planned image"
                    )
            else:
                decoded = decode_dc_status(observed)
                for name, value in json.loads(expected).items():
                    if decoded[name] != value:
                        raise QualificationProductionError(
                            "preserved DC readback differs from the planned fields"
                        )
        self._require_final_original(record, intent, original_image)

    def _require_final_original(self, record, intent, original_image) -> None:
        """The last preserved readback must show the state the operation started from."""

        final = self._journal.load_blob(record.actions[-1].diagnostic.removeprefix(_OBSERVED))
        values = decode_dc_status(final)
        if (
            values["SwitchON"] is not True
            or values["TimerON"] is not intent.original_timer_enabled
            or values["Flow"] != intent.original_flow
            or values["Mode"] != intent.original_mode
            or original_image_of(final) != original_image
        ):
            raise QualificationProductionError(
                "DC program did not end on the captured original state"
            )

    def _rebuild_images(self, intent, original_image) -> DcProgramImages:
        images = build_program_images(
            function=intent.function,
            original=original_image,
            preparation_flow=intent.preparation_flow,
            schedule_a=self._schedule_a,
            schedule_b=self._schedule_b,
            minimum=intent.min_power,
            maximum=intent.max_power,
        )
        digests = tuple(hashlib.sha256(image).hexdigest() for image in images.trial)
        if digests != intent.trial_image_sha256:
            raise QualificationProductionError("DC program trial image evidence changed")
        return images

    def _require_rebuilt_bytes(self, action, planned, images, original_image, raw, schema) -> None:
        """Rebuild this frame from the initial raw and compare every stored blob."""

        product_key = planned.intent.target.product_key
        if planned.action_id in _IMAGE_FUNCTION_BY_ACTION:
            image = image_for_action(planned.action_id, images)
            forward = build_exact_schedule_payload(product_key, image)
            rollback = build_exact_schedule_payload(product_key, original_image)
            expected, baseline = image, original_image
        else:
            fields, baseline_fields = _rebuild_scalar_fields(planned.intent, decode_dc_status(raw))
            forward = build_control_payload(schema, fields)
            rollback = build_control_payload(schema, baseline_fields)
            expected = _canonical_values(fields)
            baseline = _canonical_values(baseline_fields)
        for digest, payload in (
            (action.forward_payload_sha256, forward),
            (action.rollback_payload_sha256, rollback),
            (action.expected_readback_sha256, expected),
            (action.baseline_readback_sha256, baseline),
            (action.baseline_status_sha256, raw),
        ):
            if self._journal.load_blob(digest) != payload:
                raise QualificationProductionError("DC program payload evidence changed")

    def _finalize_program(self, record, intent, *, physical_write_count):
        self._validate_program_evidence(record, intent)
        step_id, restore_id = _RECEIPT_ACTION_IDS[intent.function]
        step_index = intent.action_ids.index(step_id)
        restore_index = intent.action_ids.index(restore_id)
        for index, action_id in ((step_index, step_id), (restore_index, restore_id)):
            action = record.actions[index]
            if action.action_id != action_id or action.function != intent.function:
                raise QualificationProductionError(
                    "DC program receipt action does not name the qualified function"
                )
        receipts = tuple(
            _bar_receipt(
                intent,
                record.actions[index],
                LocalControlIntent(
                    request_id=request_id,
                    target=intent.target,
                    function=intent.function,
                    value=value,
                ),
                record.updated_at,
            )
            for index, request_id, value in (
                (step_index, intent.step_request_id, _trial_value(intent, "step")),
                (restore_index, intent.restore_request_id, _trial_value(intent, "restore")),
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


def _rebuild_scalar_fields(intent: LocalControlIntent, values: dict) -> tuple[dict, dict]:
    """Rebuild one scalar frame's expected and baseline field maps from the initial raw.

    This mirrors the writer's own field selection without reaching into a live writer, so the
    offline evidence check cannot be satisfied by the same object that produced the bytes.
    """

    if intent.function in _TIMER_FUNCTIONS:
        screen = (
            DcTimerScreen.SCHEDULE if intent.function == "schedule_enable" else DcTimerScreen.MANUAL
        )
        desired = intent.value if screen is DcTimerScreen.SCHEDULE else not intent.value
        expected = dict(
            prepare_dc_timer_command(screen=screen, desired_timer_enabled=desired).fields
        )
    else:
        field = _SCALAR_FIELD_BY_FUNCTION[intent.function]
        expected = {
            field: internal_function_value(intent.target.product_key, intent.function, intent.value)
        }
    baseline = {name: values[name] for name in expected}
    return expected, baseline


def _trial_value(intent: DcProgramQualificationIntent, phase: str) -> object:
    if intent.function in _TIMER_FUNCTIONS:
        return phase == "restore"
    return "a" if phase == "step" else "b"
