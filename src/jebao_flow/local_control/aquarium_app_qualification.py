"""Crash-safe production of Aquarium Pump app-control qualification receipts.

Two functions are qualified here, each as a short bounded sequence that issues receipts for
exactly the two real app commands inside it and then restores the persistent fields the sequence
actually wrote from its own initial raw snapshot.

``feed_switch``
    ``trial_feed`` writes the opposite of the initial feeding state, ``restore_feed`` writes it
    back, and ``owned_restore`` returns the union. The third frame is not redundant: both app
    frames force ``TimerON`` false, so a device whose timer was on when the operation started is
    returned to it only by that frame.

``schedule_enable``
    ``trial_schedule`` writes a planned 48 x 30-minute auto image at the caller's trial speed,
    ``timer_on`` turns the timer on and seeds ``Auto*`` from that planned image, ``timer_off``
    turns it back off, ``owned_restore`` returns the initial persistent controls, and
    ``restore_schedule`` writes the original 288-byte image back verbatim. Only the two timer
    frames become receipts.

    The trial image is uniform on purpose. Every slot carries the same mode and speed, so the
    seed the app computes does not depend on which minute the frame runs in, and a preparation
    and an execution that straddle a slot boundary still produce identical bytes.

What this operation does to a running device. It is admitted with the timer either on or off. If
the timer is on, the very first frame replaces the running program while it runs, and the
restoring frames put the initial timer state back while the trial image is still loaded before
the original image returns. Those transitions are real and are stated here rather than avoided by
an admission rule. The trial speed and, when the timer starts on, every auto row of the original
image are held inside the caller's configured bounds; that is a bound on requested output, not a
demonstration of physical safety.

Recovery contract. Every action's rollback is derived from this operation's initial raw snapshot
and names only that action's own fields; no intermediate snapshot is ever recorded as recovery
authority, and ``baseline_status`` carries the real initial bytes on every action. The planned
status used to compute the timer frame's forward bytes is a declared expectation only and is
never recorded as observed evidence. ``Auto*`` seeds are not owned state: they are neither
restored nor compared against a historical value.

Feeding. ``FeedSwitch`` and ``FeedTime`` are restored as datapoint values. The product has no
elapsed or remaining feeding datapoint, so feeding progress is neither observed nor restored, and
no receipt here claims otherwise.

``Mode`` is not written, restored or qualified by this module. The bundled product configuration
declares it as a control-transport selector, the cached template screens carry no handler for it,
and this repository has no path that re-finds or hands over a device after such a change, so its
round trip is not attempted.

Offline tests prove the software contract only. They assert nothing about firmware acceptance or
physical safety, and this module issues nothing by itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.local_control.aquarium_controls import prepare_aquarium_schedule_seed
from jebao_flow.local_control.device_writer import (
    _SCHEDULE_GEOMETRY_BY_PRODUCT,
    CONTROL_ACTION,
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
    _canonical_values,
    build_aquarium_app_payload,
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
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.protocol.schedule import AQUARIUM_PUMP_PRODUCT_KEY

_LOGGER = logging.getLogger(__name__)

_OWNED_RESTORE_FUNCTION = "aquarium_owned_controls"
_OWNED_RESTORE_ACTION = "owned_restore"
_SCHEDULE_FUNCTION = "schedule"
# Each action's actual raw readback is preserved and named here, so finalization can re-check
# what the device really reported instead of trusting the phase transition alone.
_OBSERVED = "aquarium_app_status_sha256."
# These frames put the device's own controls and program back rather than choosing a value.
_RESTORING_ACTIONS = frozenset({_OWNED_RESTORE_ACTION, "restore_schedule"})
_SLOT_COUNT = 48
_SLOT_MINUTES = 30
_RUNNING_AUTO_MODE = "auto"
# The shared writer allows Mode among owned controls, but nothing here writes it, so an
# operation of this module can never own it.
_RESTORABLE_FIELDS = frozenset({"TimerON", "FeedSwitch", "FeedTime", "Motor_Speed"})


def planned_trial_schedule(trial_speed: int) -> ReturnPumpSchedule:
    """Build the 48 x 30-minute auto image that covers the whole day without gaps.

    A full-day cover matters twice over: the app seeds from the slot covering the current minute,
    so a generated gap row would seed a stopped program instead of the intended one, and a uniform
    image makes the seed independent of when the frame runs.
    """

    slots = []
    for index in range(_SLOT_COUNT):
        start = index * _SLOT_MINUTES
        end = start + _SLOT_MINUTES
        slots.append(
            {
                "slot_index": index,
                "start": f"{start // 60:02d}:{start % 60:02d}",
                "end": f"{end // 60:02d}:{end % 60:02d}",
                "mode": _RUNNING_AUTO_MODE,
                "speed": trial_speed,
            }
        )
    return ReturnPumpSchedule.model_validate({"family": "aquarium", "slots": slots})


@dataclass(frozen=True, slots=True)
class _AquariumState:
    """The product's real control state; it has no Flow, waveform mode or Linkage."""

    enabled: bool
    timer_enabled: bool
    feed_switch: bool
    feed_time: int
    motor_speed: int
    auto_mode: str
    auto_gears: int


@dataclass(frozen=True, slots=True)
class _AquariumPlan:
    function: str
    action_ids: tuple[str, ...]
    receipt_indexes: tuple[int, int]
    needs_trial_speed: bool


_PLANS = {
    "feed_switch": _AquariumPlan(
        function="feed_switch",
        action_ids=("trial_feed", "restore_feed", _OWNED_RESTORE_ACTION),
        receipt_indexes=(0, 1),
        needs_trial_speed=False,
    ),
    "schedule_enable": _AquariumPlan(
        function="schedule_enable",
        action_ids=(
            "trial_schedule",
            "timer_on",
            "timer_off",
            _OWNED_RESTORE_ACTION,
            "restore_schedule",
        ),
        receipt_indexes=(1, 2),
        needs_trial_speed=True,
    ),
}

_SUPPORTED_FUNCTIONS = tuple(sorted(_PLANS))


def _frame_values(function: str, *, initial_feed_switch: bool) -> tuple[tuple[str, Any], ...]:
    """The (app function, value) of every app frame, excluding the two image/restore frames."""

    if function == "feed_switch":
        return (
            ("feed_switch", not initial_feed_switch),
            ("feed_switch", initial_feed_switch),
        )
    return (("schedule_enable", True), ("schedule_enable", False))


class AquariumAppQualificationIntent(BaseModel):
    """Durable intent for one bounded Aquarium app-control qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["feed_switch", "schedule_enable"]
    baseline_enabled: Literal[True] = True
    baseline_timer_enabled: bool
    baseline_feed_switch: bool
    baseline_feed_time: int
    baseline_motor_speed: int
    trial_speed: int | None = None
    first_value: bool
    second_value: bool
    owned_fields: tuple[str, ...]
    first_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    second_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
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
            raise ValueError("aquarium app qualification timestamp must be timezone-aware")
        if self.first_request_id == self.second_request_id:
            raise ValueError("aquarium app qualification receipt requests must be distinct")
        if self.target.product_key != AQUARIUM_PUMP_PRODUCT_KEY:
            raise ValueError("aquarium app qualification target is not the Aquarium Pump")
        if self.min_power > self.max_power:
            raise ValueError("aquarium app qualification has an invalid output envelope")
        plan = _PLANS[self.function]
        expected = _frame_values(self.function, initial_feed_switch=self.baseline_feed_switch)
        if (self.first_value, self.second_value) != tuple(value for _name, value in expected):
            raise ValueError("aquarium app qualification values are not the planned commands")
        if self.first_value == self.second_value:
            raise ValueError("aquarium app qualification receipts must be a real step and back")
        if (self.trial_speed is None) is plan.needs_trial_speed:
            raise ValueError("aquarium app qualification trial speed does not match the plan")
        if self.trial_speed is not None:
            _require_bounded_request(
                self.trial_speed,
                minimum=self.min_power,
                maximum=self.max_power,
                description="trial speed",
            )
        for value in (self.baseline_feed_time, self.baseline_motor_speed):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("aquarium app qualification baselines must be integers")
        if (
            not self.owned_fields
            or len(set(self.owned_fields)) != len(self.owned_fields)
            or tuple(sorted(self.owned_fields)) != self.owned_fields
            or not set(self.owned_fields) <= set(_RESTORABLE_FIELDS)
        ):
            raise ValueError("aquarium app qualification owned fields are invalid")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("aquarium app qualification versions must use printable ASCII")
        return self


def _require_bounded_request(
    value: object,
    *,
    minimum: int,
    maximum: int,
    description: str,
) -> int:
    """Keep the existing first-write ceiling; a configured maximum is not a hardware limit."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"aquarium {description} must be an integer")
    if not minimum <= value <= maximum or value > _SAFE_FIRST_WRITE_MAXIMUM:
        raise ValueError(f"aquarium {description} is outside the approved first-write bounds")
    return value


def aquarium_app_qualification_intent_bytes(intent: AquariumAppQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


class AquariumAppQualificationProducer(ManualFlowQualificationProducer):
    """Execute or offline-finalize one bounded Aquarium app-control qualification."""

    def __init__(self, *, function: str, **kwargs: Any) -> None:
        if function not in _PLANS:
            raise ValueError("aquarium app qualification function is not supported")
        super().__init__(**kwargs)
        self._plan = _PLANS[function]

    async def execute(  # type: ignore[override]
        self,
        operation_id: str,
        *,
        trial_speed: int | None = None,
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        plan = self._plan
        if (trial_speed is None) is plan.needs_trial_speed:
            raise ValueError("aquarium timer qualification requires its own trial speed")
        existing = self._journal.load(operation_id)
        if existing is not None:
            if existing.phase is TransactionPhase.VERIFIED:
                return self._finalize_app(existing, physical_write_count=0)
            raise QualificationProductionError(
                "aquarium app qualification has unfinished or recovered durable state"
            )

        epoch = self._epoch_provider()
        record: LocalControlJournalRecord | None = None
        writes = 0
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError(
                    "aquarium app qualification appeared concurrently"
                )
            self._require_epoch(epoch)
            writer: ConnectedDeviceWriter | None = None
            try:
                endpoint, firmware_version = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                initial_status = await writer.connect_and_read()
                state = self._read_state(epoch, initial_status)
                self._require_admission(epoch, state, initial_status, trial_speed)
                intent, prepared = self._prepare_operation(
                    operation_id,
                    epoch,
                    writer,
                    initial_status,
                    state,
                    trial_speed,
                    firmware_version,
                )
                record = self._create_record(intent, prepared)
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
                for index in range(len(plan.action_ids)):
                    self._require_epoch(epoch)
                    record = await self._execute_frame(
                        record, index, epoch, intent, firmware_version, initial_status, permit
                    )
                    writes += 1
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                self._require_recovery(operation_id)
                raise

        return self._finalize_app(record, physical_write_count=writes)

    # -- admission --------------------------------------------------------------------

    @staticmethod
    def _read_state(epoch: QualificationEpoch, status: bytes) -> _AquariumState:
        if epoch.target.product_key != AQUARIUM_PUMP_PRODUCT_KEY:
            raise QualificationProductionError("aquarium app qualification target is not current")
        try:
            decoded = get_product_schema(epoch.target.product_key).decode_status(status)
        except (KeyError, ValueError) as error:
            raise QualificationProductionError("current aquarium state is invalid") from error
        try:
            values = (
                decoded["SwitchON"],
                decoded["TimerON"],
                decoded["FeedSwitch"],
                decoded["FeedTime"],
                decoded["Motor_Speed"],
                decoded["AutoMode"],
                decoded["AutoGears"],
            )
        except KeyError as error:
            raise QualificationProductionError("current aquarium state is incomplete") from error
        enabled, timer, feed, feed_time, speed, auto_mode, auto_gears = values
        if (
            type(enabled) is not bool
            or type(timer) is not bool
            or type(feed) is not bool
            or not isinstance(auto_mode, str)
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in (feed_time, speed, auto_gears)
            )
        ):
            raise QualificationProductionError("current aquarium state types are invalid")
        return _AquariumState(enabled, timer, feed, feed_time, speed, auto_mode, auto_gears)

    def _require_admission(
        self,
        epoch: QualificationEpoch,
        state: _AquariumState,
        initial_status: bytes,
        trial_speed: int | None,
    ) -> None:
        """Admit a device whose timer is on or off, bounding only what this operation drives."""

        if state.enabled is not True:
            raise QualificationProductionError("aquarium app qualification requires the pump on")
        self._require_bounded_output(epoch, state)
        if trial_speed is not None:
            try:
                _require_bounded_request(
                    trial_speed,
                    minimum=epoch.min_power,
                    maximum=epoch.max_power,
                    description="trial speed",
                )
            except ValueError as error:
                raise QualificationProductionError(str(error)) from error
        if self._plan.function == "schedule_enable" and state.timer_enabled:
            # The original image is put back while the timer is on again, so every auto row of
            # it has to stay inside the same bounds. A device that starts with the timer off
            # gets its image back with the timer off and needs no such check.
            self._require_original_image_bounds(epoch, initial_status)

    @staticmethod
    def _require_bounded_output(
        epoch: QualificationEpoch,
        state: _AquariumState,
        *,
        restoring: bool = False,
    ) -> None:
        """Bound the output the device is actually running, which the timer decides.

        The first-write ceiling belongs to values this operation chooses: admission and the trial
        frames. The frames that put the device's own controls and program back are bounded by the
        configured range instead, so a legitimate original program above that ceiling is restored
        rather than refused by us.
        """

        if state.timer_enabled:
            if state.auto_mode != _RUNNING_AUTO_MODE:
                return
            running = state.auto_gears
        else:
            running = state.motor_speed
        ceiling = epoch.max_power if restoring else min(epoch.max_power, _SAFE_FIRST_WRITE_MAXIMUM)
        if not epoch.min_power <= running <= ceiling:
            raise QualificationProductionError("current aquarium output is outside its bounds")

    def _require_original_image_bounds(
        self, epoch: QualificationEpoch, initial_status: bytes
    ) -> None:
        image = extract_current_schedule_image(epoch.target.product_key, initial_status)
        try:
            # The configured range, not the first-write ceiling: this image is the device's own
            # program being put back, not a new value this operation is choosing.
            prepare_aquarium_schedule_seed(
                image,
                local_minute=0,
                minimum_speed=epoch.min_power,
                maximum_speed=epoch.max_power,
            )
        except ValueError as error:
            raise QualificationProductionError(
                "aquarium original schedule is outside the approved bounds"
            ) from error

    # -- preparation ------------------------------------------------------------------

    def _prepare_operation(
        self,
        operation_id: str,
        epoch: QualificationEpoch,
        writer: ConnectedDeviceWriter,
        initial_status: bytes,
        state: _AquariumState,
        trial_speed: int | None,
        firmware_version: str,
    ) -> tuple[AquariumAppQualificationIntent, tuple[PreparedDeviceWrite, ...]]:
        plan = self._plan
        product_key = epoch.target.product_key
        frames = _frame_values(plan.function, initial_feed_switch=state.feed_switch)
        prepared: list[PreparedDeviceWrite] = []
        trial_image = b""
        original_image = extract_current_schedule_image(product_key, initial_status)

        if plan.function == "schedule_enable":
            trial = planned_trial_schedule(trial_speed)
            trial_image = trial.compile_image()
            # An image identical to the stored one is not disqualifying: what these receipts
            # attest is the timer ON and OFF commands, whose payloads differ regardless.
            prepared.append(
                writer.prepare_scalar(
                    LocalControlIntent(
                        request_id=_phase_request_id(operation_id, plan.action_ids[0]),
                        target=epoch.target,
                        function=_SCHEDULE_FUNCTION,
                        value=trial.model_dump(mode="json"),
                    ),
                    initial_status,
                )
            )

        # The ON seed must be computed against the image this operation will have written. That
        # declared status only builds forward bytes; the durable record keeps the real initial
        # snapshot as baseline_status below.
        planned_status = (
            self._planned_status(product_key, initial_status, trial_image)
            if plan.function == "schedule_enable"
            else initial_status
        )
        for offset, (function, value) in enumerate(frames):
            action_id = plan.action_ids[len(prepared)]
            source = (
                planned_status
                if plan.function == "schedule_enable" and offset == 0
                else (initial_status)
            )
            frame = writer.prepare_scalar(
                LocalControlIntent(
                    request_id=_phase_request_id(operation_id, action_id),
                    target=epoch.target,
                    function=function,
                    value=value,
                ),
                source,
            )
            prepared.append(replace(frame, baseline_status=bytes(initial_status), no_change=False))

        owned_fields = self._owned_field_union(tuple(prepared[-len(frames) :]))
        prepared.append(writer.prepare_aquarium_restore_controls(initial_status, owned_fields))

        if plan.function == "schedule_enable":
            restore_payload = build_exact_schedule_payload(product_key, original_image)
            prepared.append(
                PreparedDeviceWrite(
                    target=writer.endpoint,
                    function=_SCHEDULE_FUNCTION,
                    forward_payload=restore_payload,
                    rollback_payload=restore_payload,
                    baseline_status=bytes(initial_status),
                    expected_readback=original_image,
                    baseline_readback=original_image,
                    verification_kind="schedule_image",
                    no_change=False,
                )
            )

        frozen = tuple(prepared)
        self._require_owned_rollback(frozen, initial_status, original_image)
        first_index, second_index = plan.receipt_indexes
        intent = AquariumAppQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            function=plan.function,
            baseline_timer_enabled=state.timer_enabled,
            baseline_feed_switch=state.feed_switch,
            baseline_feed_time=state.feed_time,
            baseline_motor_speed=state.motor_speed,
            trial_speed=trial_speed,
            first_value=frames[0][1],
            second_value=frames[1][1],
            owned_fields=owned_fields,
            first_request_id=_phase_request_id(operation_id, plan.action_ids[first_index]),
            second_request_id=_phase_request_id(operation_id, plan.action_ids[second_index]),
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
        return intent, frozen

    @staticmethod
    def _planned_status(product_key: str, initial_status: bytes, trial_image: bytes) -> bytes:
        geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[product_key]
        if len(trial_image) != geometry.image_size:
            raise QualificationProductionError("aquarium trial image has the wrong size")
        planned = bytearray(initial_status)
        start = geometry.status_offset
        planned[start : start + geometry.image_size] = trial_image
        return bytes(planned)

    @staticmethod
    def _owned_field_union(prepared: tuple[PreparedDeviceWrite, ...]) -> tuple[str, ...]:
        """The persistent fields the app frames actually write, in a stable order."""

        names: set[str] = set()
        for item in prepared:
            if item.verification_kind != "scalar_fields":
                raise QualificationProductionError("aquarium app frame is not a scalar write")
            try:
                written = json.loads(item.expected_readback)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise QualificationProductionError(
                    "aquarium app frame readback is invalid"
                ) from error
            if not isinstance(written, dict) or not written:
                raise QualificationProductionError("aquarium app frame wrote no field")
            if not set(written) <= _RESTORABLE_FIELDS:
                raise QualificationProductionError(
                    "aquarium app qualification wrote a field it cannot restore"
                )
            names |= set(written)
        return tuple(sorted(names))

    def _require_owned_rollback(
        self,
        prepared: tuple[PreparedDeviceWrite, ...],
        initial_status: bytes,
        original_image: bytes,
    ) -> None:
        """Every rollback must name this operation's initial state, never an intermediate one."""

        plan = self._plan
        decoded = get_product_schema(AQUARIUM_PUMP_PRODUCT_KEY).decode_status(initial_status)
        if len({item.baseline_status for item in prepared}) != 1:
            raise QualificationProductionError("aquarium actions must share one initial snapshot")
        if prepared[0].baseline_status != bytes(initial_status):
            raise QualificationProductionError("aquarium actions must carry the initial snapshot")
        for item in prepared:
            if item.verification_kind == "schedule_image":
                if item.baseline_readback != original_image:
                    raise QualificationProductionError(
                        "aquarium image rollbacks must restore the original image"
                    )
                continue
            baseline = json.loads(item.baseline_readback)
            if any(name.startswith("Auto") for name in baseline):
                raise QualificationProductionError("aquarium rollbacks must not carry Auto seeds")
            if any(decoded[name] != value for name, value in baseline.items()):
                raise QualificationProductionError(
                    "aquarium rollbacks must restore the initial values"
                )
        first, second = (prepared[index] for index in plan.receipt_indexes)
        if first.forward_payload == second.forward_payload:
            raise QualificationProductionError("aquarium receipt payloads must differ")
        restore = prepared[len(plan.action_ids) - (2 if plan.function == "schedule_enable" else 1)]
        if restore.function != _OWNED_RESTORE_FUNCTION:
            raise QualificationProductionError("aquarium restore must be its own function")

    # -- execution --------------------------------------------------------------------

    async def _execute_frame(
        self,
        record: LocalControlJournalRecord,
        index: int,
        epoch: QualificationEpoch,
        intent: AquariumAppQualificationIntent,
        firmware_version: str,
        initial_status: bytes,
        permit,
    ) -> LocalControlJournalRecord:
        """Resolve, connect and re-derive this frame before sending the durable bytes."""

        endpoint, _firmware = await self._resolve(epoch, firmware_version)
        writer = self._writer_factory(endpoint)
        try:
            status = await writer.connect_and_read()
            restoring = self._plan.action_ids[index] in _RESTORING_ACTIONS
            self._require_frame_envelope(epoch, status, restoring=restoring)
            action = record.actions[index]
            prepared = self._prepared_from_action(action, endpoint)
            fresh = self._fresh_frame(writer, epoch, intent, index, status, initial_status)
            if (
                fresh.forward_payload_sha256 != action.forward_payload_sha256
                or hashlib.sha256(fresh.expected_readback).hexdigest()
                != action.expected_readback_sha256
            ):
                raise QualificationProductionError("aquarium app frame payload changed")
            self._require_epoch(epoch)
            observed: list[str] = []

            def validate(readback: bytes, *, observed: list[str] = observed) -> None:
                # Preserve the real bytes first: an envelope refusal must still leave the
                # device's own report behind for the finalizer and for recovery.
                observed.append(self._journal.install_blob(readback))
                self._require_frame_envelope(epoch, readback, restoring=restoring)

            record = await self._execute_action(
                record, index, writer, prepared, permit, readback_validator=validate
            )
            return self._save_action(
                record, index, phase=ActionPhase.VERIFIED, diagnostic=_OBSERVED + observed[0]
            )
        finally:
            await writer.close()

    def _fresh_frame(
        self,
        writer: ConnectedDeviceWriter,
        epoch: QualificationEpoch,
        intent: AquariumAppQualificationIntent,
        index: int,
        status: bytes,
        initial_status: bytes,
    ) -> PreparedDeviceWrite:
        plan = self._plan
        action_id = plan.action_ids[index]
        if action_id == _OWNED_RESTORE_ACTION:
            # The restore is defined by the initial bytes alone, never by what is there now.
            return writer.prepare_aquarium_restore_controls(
                initial_status, tuple(intent.owned_fields)
            )
        if action_id == "restore_schedule":
            original = extract_current_schedule_image(epoch.target.product_key, initial_status)
            payload = build_exact_schedule_payload(epoch.target.product_key, original)
            return PreparedDeviceWrite(
                target=writer.endpoint,
                function=_SCHEDULE_FUNCTION,
                forward_payload=payload,
                rollback_payload=payload,
                baseline_status=bytes(initial_status),
                expected_readback=original,
                baseline_readback=original,
                verification_kind="schedule_image",
                no_change=False,
            )
        if action_id == "trial_schedule":
            trial = planned_trial_schedule(intent.trial_speed)
            return writer.prepare_scalar(
                LocalControlIntent(
                    request_id=_phase_request_id(intent.operation_id, action_id),
                    target=epoch.target,
                    function=_SCHEDULE_FUNCTION,
                    value=trial.model_dump(mode="json"),
                ),
                status,
            )
        function, value = _frame_values(
            plan.function, initial_feed_switch=intent.baseline_feed_switch
        )[plan.action_ids.index(action_id) - (1 if plan.function == "schedule_enable" else 0)]
        return writer.prepare_scalar(
            LocalControlIntent(
                request_id=_phase_request_id(intent.operation_id, action_id),
                target=epoch.target,
                function=function,
                value=value,
            ),
            status,
        )

    def _require_frame_envelope(
        self, epoch: QualificationEpoch, status: bytes, *, restoring: bool = False
    ) -> None:
        """Bound the states this operation is allowed to pass through.

        The timer, the feeding switch and the schedule all move on purpose here, so none of them
        is held to its initial value. What is checked is that the pump stays on and that the
        output the device is actually running stays inside the caller's bounds.
        """

        state = self._read_state(epoch, status)
        if state.enabled is not True:
            raise QualificationProductionError("aquarium app qualification requires the pump on")
        self._require_bounded_output(epoch, state, restoring=restoring)

    # -- durable record ---------------------------------------------------------------

    def _create_record(
        self,
        intent: AquariumAppQualificationIntent,
        prepared: tuple[PreparedDeviceWrite, ...],
    ) -> LocalControlJournalRecord:
        intent_sha256 = self._journal.install_blob(aquarium_app_qualification_intent_bytes(intent))
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
            for action_id, item in zip(self._plan.action_ids, prepared, strict=True)
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
            if reloaded != record or reloaded.phase is not TransactionPhase.PREPARED:
                raise QualificationProductionError(
                    "aquarium app qualification journal did not reload exactly"
                )
            if any(
                action.phase is not ActionPhase.PREPARED
                or action.target != intent.target
                or action.acknowledgement_sha256 is not None
                or action.restore_on_recovery is not True
                for action in reloaded.actions
            ):
                raise QualificationProductionError(
                    "aquarium app qualification journal was not safely preinstalled"
                )
            return reloaded
        except BaseException:
            self._require_recovery(record.request_id)
            raise

    # -- finalization -----------------------------------------------------------------

    def _finalize_app(
        self,
        record: LocalControlJournalRecord,
        *,
        physical_write_count: int,
    ) -> QualificationProductionResult:
        intent = self._load_intent(record)
        self._validate_terminal_evidence(record, intent)
        receipts = tuple(
            _app_receipt(
                intent,
                record.actions[index],
                _app_phase_intent(intent, position),
                record.updated_at,
            )
            for position, index in enumerate(self._plan.receipt_indexes)
        )
        receipt_digests = tuple(
            hashlib.sha256(qualification_receipt_bytes(item)).hexdigest() for item in receipts
        )
        binding = QualificationBinding(
            product_key=intent.target.product_key,
            function=intent.function,
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
            receipt_count=len(receipts),
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )

    def _load_intent(self, record: LocalControlJournalRecord) -> AquariumAppQualificationIntent:
        payload = self._journal.load_blob(record.intent_sha256)
        try:
            intent = AquariumAppQualificationIntent.model_validate_json(payload)
        except ValueError as error:
            raise QualificationProductionError(
                "aquarium app qualification journal intent is invalid"
            ) from error
        if (
            aquarium_app_qualification_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
            or intent.function != self._plan.function
        ):
            raise QualificationProductionError(
                "aquarium app qualification journal intent is not canonical"
            )
        return intent

    def _validate_terminal_evidence(
        self,
        record: LocalControlJournalRecord,
        intent: AquariumAppQualificationIntent,
    ) -> None:
        plan = self._plan
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError("aquarium app qualification journal is not verified")
        if tuple(action.action_id for action in record.actions) != plan.action_ids:
            raise QualificationProductionError("aquarium app qualification actions changed")
        if any(
            action.phase is not ActionPhase.VERIFIED
            or action.target != intent.target
            or action.acknowledgement_sha256 is None
            or action.restore_on_recovery is not True
            or not action.diagnostic
            or not action.diagnostic.startswith(_OBSERVED)
            for action in record.actions
        ):
            raise QualificationProductionError(
                "aquarium app qualification journal evidence is incomplete"
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
                        "aquarium app qualification journal evidence is incomplete"
                    )
                self._journal.load_blob(digest)
        if len({action.baseline_status_sha256 for action in record.actions}) != 1:
            raise QualificationProductionError(
                "aquarium app qualification actions must share one initial snapshot"
            )
        first, second = (record.actions[index] for index in plan.receipt_indexes)
        for action in (first, second):
            if action.function != plan.function:
                raise QualificationProductionError(
                    "aquarium receipts must name the qualified function"
                )
        if first.forward_payload_sha256 == second.forward_payload_sha256:
            raise QualificationProductionError("aquarium receipt payloads must differ")
        initial_status = self._journal.load_blob(record.actions[0].baseline_status_sha256)
        self._require_rebuilt_bytes(intent, record, initial_status)

    def _require_rebuilt_bytes(
        self,
        intent: AquariumAppQualificationIntent,
        record: LocalControlJournalRecord,
        initial_status: bytes,
    ) -> None:
        """Rebuild every frame from the initial raw and re-check what the device reported."""

        product_key = intent.target.product_key
        schema = get_product_schema(product_key)
        decoded = schema.decode_status(initial_status)
        # The recorded baselines are this operation's own binding to the raw it started from.
        # Without this, a record whose actions all pointed at some other snapshot would rebuild
        # self-consistently from the wrong bytes; the intent digest itself cannot be re-pointed.
        if (
            decoded["FeedSwitch"] is not intent.baseline_feed_switch
            or decoded["TimerON"] is not intent.baseline_timer_enabled
            or decoded["FeedTime"] != intent.baseline_feed_time
            or decoded["Motor_Speed"] != intent.baseline_motor_speed
        ):
            raise QualificationProductionError(
                "aquarium recorded baselines do not match the initial raw"
            )
        original_image = extract_current_schedule_image(product_key, initial_status)
        planned = self._rebuilt_frames(intent, initial_status, decoded, original_image)
        for action, frame in zip(record.actions, planned, strict=True):
            if action.function != frame.function:
                raise QualificationProductionError("aquarium action functions changed")
            observed = self._journal.load_blob(action.diagnostic.removeprefix(_OBSERVED))
            if self._journal.load_blob(action.baseline_status_sha256) != initial_status:
                raise QualificationProductionError(
                    "aquarium action does not carry this operation's initial snapshot"
                )
            if frame.kind == "image":
                if (
                    _digest(build_exact_schedule_payload(product_key, frame.image))
                    != action.forward_payload_sha256
                    or _digest(build_exact_schedule_payload(product_key, frame.rollback_image))
                    != action.rollback_payload_sha256
                    or self._journal.load_blob(action.expected_readback_sha256) != frame.image
                    or self._journal.load_blob(action.baseline_readback_sha256)
                    != frame.rollback_image
                ):
                    raise QualificationProductionError(
                        "aquarium image bytes do not rebuild from the initial raw"
                    )
                if extract_current_schedule_image(product_key, observed) != frame.image:
                    raise QualificationProductionError(
                        "preserved aquarium image readback differs from the planned image"
                    )
                continue
            rollback = {name: decoded[name] for name in frame.readback}
            if frame.kind == "owned":
                # The owned restore forwards and rolls back to the same captured bytes.
                forward = _rebuilt_owned_payload(schema, initial_status, tuple(frame.readback))
                rollback_payload = forward
            else:
                forward = build_aquarium_app_payload(frame.written, frame.source)
                rollback_payload = build_aquarium_app_payload(rollback, frame.source)
            if (
                _digest(forward) != action.forward_payload_sha256
                or _digest(rollback_payload) != action.rollback_payload_sha256
                or _digest(_canonical_values(frame.readback)) != action.expected_readback_sha256
                or _digest(_canonical_values(rollback)) != action.baseline_readback_sha256
            ):
                raise QualificationProductionError(
                    "aquarium app bytes do not rebuild from the initial raw"
                )
            reported = schema.decode_status(observed)
            if any(reported[name] != value for name, value in frame.readback.items()):
                raise QualificationProductionError(
                    "preserved aquarium readback differs from the planned fields"
                )

    def _rebuilt_frames(
        self,
        intent: AquariumAppQualificationIntent,
        initial_status: bytes,
        decoded: dict[str, Any],
        original_image: bytes,
    ) -> list[_RebuiltFrame]:
        """Rebuild each frame's bytes from the initial raw alone.

        A frame's written fields and its persistent readback are not always the same set: the
        timer-on command carries a seed it does not own, so the two are tracked separately.
        """

        plan = self._plan
        frames: list[_RebuiltFrame] = []
        owned = {name: decoded[name] for name in intent.owned_fields}
        if plan.function == "schedule_enable":
            trial_image = planned_trial_schedule(intent.trial_speed).compile_image()
            # The timer frame was prepared against the image this operation writes first, so it
            # has to be rebuilt against that same declared status rather than the initial raw.
            planned_status = self._planned_status(
                intent.target.product_key, initial_status, trial_image
            )
            persistent = {"TimerON": True, "FeedSwitch": False}
            written = _schedule_enable_fields(
                planned_status, minimum=intent.min_power, maximum=intent.max_power
            )
            timer_off = {"TimerON": False, "Motor_Speed": decoded["Motor_Speed"]}
            return [
                _RebuiltFrame(
                    _SCHEDULE_FUNCTION,
                    "image",
                    image=trial_image,
                    rollback_image=original_image,
                ),
                _RebuiltFrame(
                    plan.function,
                    "scalar",
                    written=written,
                    readback=persistent,
                    source=planned_status,
                ),
                _RebuiltFrame(
                    plan.function,
                    "scalar",
                    written=timer_off,
                    readback=timer_off,
                    source=initial_status,
                ),
                _RebuiltFrame(
                    _OWNED_RESTORE_FUNCTION,
                    "owned",
                    written=owned,
                    readback=owned,
                    source=initial_status,
                ),
                _RebuiltFrame(
                    _SCHEDULE_FUNCTION,
                    "image",
                    image=original_image,
                    rollback_image=original_image,
                ),
            ]
        for value in (intent.first_value, intent.second_value):
            fields: dict[str, Any] = {
                "FeedSwitch": value,
                "TimerON": False,
                "FeedTime": decoded["FeedTime"],
            }
            if not value:
                fields["Motor_Speed"] = decoded["Motor_Speed"]
            frames.append(
                _RebuiltFrame(
                    plan.function,
                    "scalar",
                    written=fields,
                    readback=fields,
                    source=initial_status,
                )
            )
        frames.append(
            _RebuiltFrame(
                _OWNED_RESTORE_FUNCTION,
                "owned",
                written=owned,
                readback=owned,
                source=initial_status,
            )
        )
        return frames


@dataclass(frozen=True, slots=True)
class _RebuiltFrame:
    function: str
    kind: str
    written: Mapping[str, Any] = MappingProxyType({})
    readback: Mapping[str, Any] = MappingProxyType({})
    source: bytes = b""
    image: bytes = b""
    rollback_image: bytes = b""


def _schedule_enable_fields(
    planned_status: bytes,
    *,
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    """Rebuild the timer-on frame, including the seed computed from the planned image."""

    image = extract_current_schedule_image(AQUARIUM_PUMP_PRODUCT_KEY, planned_status)
    # The planned trial image is uniform, so the seed does not depend on the minute chosen here.
    seed = prepare_aquarium_schedule_seed(
        image, local_minute=0, minimum_speed=minimum, maximum_speed=maximum
    )
    return {"TimerON": True, "FeedSwitch": False, **dict(seed.fields)}


def _rebuilt_owned_payload(schema, original_status: bytes, owned_fields: tuple[str, ...]) -> bytes:
    """Rebuild the owned-restore frame the way the shared writer builds it.

    It is not an encoded field set but the original value block with only the owned datapoints
    flagged, so rebuilding it means reproducing that construction rather than re-encoding values.
    """

    flags = bytearray(schema.attribute_flags_size)
    for name in owned_fields:
        attribute = schema.by_name(name)
        flags[attribute.id // 8] |= 1 << (attribute.id % 8)
    return (
        bytes([CONTROL_ACTION])
        + bytes(reversed(flags))
        + original_status[: schema.attribute_values_size]
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _app_phase_intent(
    intent: AquariumAppQualificationIntent,
    position: int,
) -> LocalControlIntent:
    if position == 0:
        request_id, value = intent.first_request_id, intent.first_value
    elif position == 1:
        request_id, value = intent.second_request_id, intent.second_value
    else:
        raise IndexError("aquarium receipt position is invalid")
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=intent.function,
        value=value,
    )


def _app_receipt(
    intent: AquariumAppQualificationIntent,
    action: JournalAction,
    phase_intent: LocalControlIntent,
    completed_at: datetime,
) -> LocalControlQualificationReceipt:
    if action.acknowledgement_sha256 is None:
        raise QualificationProductionError(
            "aquarium app qualification action has no acknowledgement"
        )
    return LocalControlQualificationReceipt(
        product_key=intent.target.product_key,
        function=intent.function,
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
    "AquariumAppQualificationIntent",
    "AquariumAppQualificationProducer",
    "aquarium_app_qualification_intent_bytes",
    "planned_trial_schedule",
]
