"""Crash-safe production of Local Wavemaker (Bar) manual-control qualification receipts.

This module owns no alternate write primitive. It reuses the audited manual-flow producer's
function-independent lease, resolution, journal transition, artifact machinery and safe first-write
envelope, applying the same three-frame ``same -> step -> restore`` contract to two additional Bar
manual-control scalars: ``manual_frequency`` and ``manual_mode``. Each is a single-field partial
write whose exact baseline is restored by the ordinary scalar rollback, so no receipt is emitted
until the durable journal is terminal and the own baseline has been written back and verified.

It deliberately covers only the manual controls the app writes as single-field partials while the
device is Independent/TimerOFF/Constant, reusing the existing scalar restore contract. Absence of
firmware side effects is NOT asserted here: these offline tests prove the software contract (single
send, exact own-baseline restore, receipt-only-after-verified), not device acceptance or physical
safety. Bar ``feed_switch``, ``pulse_or_tide``, ``linkage_role``, ``feeding_duration`` and
whole-``schedule`` qualification are intentionally not produced here;
``docs/bar-qualification-implementation.md`` records the exact restore-safety blocker for each.

The DC subclass reuses this scalar journal implementation with an explicitly different product
family and additionally permits feeding_duration when the existing writer can encode its baseline.
The Bar class's own accepted functions remain the two listed above.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.local_control.catalog import (
    build_local_control_catalog,
    internal_function_value,
)
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
    _manual_flow_family_profile,
    _manual_flow_state,
    _phase_request_id,
    _require_operation_id,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range

_BAR_FAMILY = "local_wavemaker"
_ACTION_IDS = ("same_value", "step_value", "planned_restore")
_SUPPORTED = {
    "manual_frequency": "Frequency",
    "manual_mode": "Mode",
    "feeding_duration": "FeedTime",
}


@dataclass(frozen=True, slots=True)
class _BarScalarSpec:
    """One supported Bar manual-control scalar and its exact single-field encoder."""

    function: str
    field: str
    value_kind: Literal["integer", "enum"]

    @property
    def pins_constant_mode(self) -> bool:
        # A frequency trial keeps Mode at the constant baseline; a mode trial varies it.
        return self.function != "manual_mode"


def _spec(function: str) -> _BarScalarSpec:
    try:
        field = _SUPPORTED[function]
    except KeyError as error:
        raise QualificationProductionError(
            "scalar qualification function is unsupported"
        ) from error
    return _BarScalarSpec(
        function=function,
        field=field,
        value_kind="enum" if function == "manual_mode" else "integer",
    )


def _bar_manual_mode_choices(product_key: str) -> tuple[str, ...]:
    catalog = build_local_control_catalog()
    try:
        descriptor = catalog.find(product_key, "manual_mode")
    except KeyError as error:
        raise QualificationProductionError("bar manual mode is not in the app catalog") from error
    if descriptor.family not in {_BAR_FAMILY, "dc_pump_pro"} or not descriptor.encoding_available:
        raise QualificationProductionError("bar manual mode encoder is unavailable")
    return descriptor.choices


class BarScalarQualificationIntent(BaseModel):
    """Private durable intent for one Bar manual-control ``same/step/restore`` qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["manual_frequency", "manual_mode", "feeding_duration"]
    field: str = Field(min_length=1, max_length=64)
    baseline_value: int | str
    trial_value: int | str
    baseline_mode: str = Field(min_length=1, max_length=128)
    baseline_linkage: str | None = Field(min_length=1, max_length=128)
    baseline_flow: int
    baseline_enabled: Literal[True] = True
    baseline_timer_enabled: Literal[False] = False
    step_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
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
            raise ValueError("bar qualification timestamp must be timezone-aware")
        if self.field != _SUPPORTED.get(self.function):
            raise ValueError("bar qualification field does not match its function")
        if self.step_request_id == self.restore_request_id:
            raise ValueError("bar qualification receipt requests must be distinct")
        if self.baseline_value == self.trial_value:
            raise ValueError("bar qualification step must change the value")
        if self.min_power > self.max_power:
            raise ValueError("bar qualification intent has an invalid flow envelope")
        if not self.min_power <= self.baseline_flow <= self.max_power:
            raise ValueError("bar qualification baseline flow is outside config limits")
        if self.baseline_flow > _SAFE_FIRST_WRITE_MAXIMUM:
            raise ValueError("bar qualification baseline exceeds the first-write maximum")
        profile = _manual_flow_family_profile(self.target.product_key)
        if (
            self.baseline_mode != profile.required_mode
            or self.baseline_linkage != profile.required_linkage
        ):
            raise ValueError("bar qualification baseline ownership is invalid")
        if self.function != "manual_mode":
            if not isinstance(self.baseline_value, int) or isinstance(self.baseline_value, bool):
                raise ValueError("integer scalar values must be integers")
            if not isinstance(self.trial_value, int) or isinstance(self.trial_value, bool):
                raise ValueError("integer scalar values must be integers")
        else:
            if not isinstance(self.baseline_value, str) or not isinstance(self.trial_value, str):
                raise ValueError("manual_mode values must be app mode names")
            if self.baseline_value != "constant":
                raise ValueError("manual_mode baseline must be the constant manual mode")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("bar qualification versions must use printable ASCII")
        return self


class BarScalarQualificationProducer(ManualFlowQualificationProducer):
    """Execute or offline-finalize one Bar manual-control ``same/step/restore`` qualification."""

    _family = _BAR_FAMILY
    _functions = frozenset({"manual_frequency", "manual_mode"})

    def __init__(self, *, function: str, **kwargs: object) -> None:
        if function not in self._functions:
            raise QualificationProductionError("function is outside this product producer's scope")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._spec = _spec(function)

    async def execute(self, operation_id: str) -> QualificationProductionResult:  # type: ignore[override]
        _require_operation_id(operation_id)
        existing = self._journal.load(operation_id)
        if existing is not None:
            if existing.phase is TransactionPhase.VERIFIED:
                return self._finalize_bar(existing, physical_write_count=0)
            raise QualificationProductionError(
                "bar qualification has unfinished or recovered durable state"
            )

        epoch = self._epoch_provider()
        self._require_bar_target(epoch.target)
        record: LocalControlJournalRecord | None = None
        writes = 0
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("bar qualification appeared concurrently")
            self._require_epoch(epoch)
            writer: ConnectedDeviceWriter | None = None
            try:
                endpoint, firmware_version = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                baseline_status = await writer.connect_and_read()
                baseline_state = _manual_flow_state(writer, epoch.target, baseline_status)
                self._require_state_envelope(epoch, baseline_state)
                intent, prepared = self._prepare_bar_operation(
                    operation_id,
                    epoch,
                    endpoint,
                    writer,
                    baseline_status,
                    baseline_state,
                    firmware_version,
                )
                record = self._create_bar_record(intent, prepared)
                self._require_epoch(epoch)
                record = await self._execute_action(
                    record,
                    0,
                    writer,
                    prepared[0],
                    permit,
                    readback_validator=self._readback_validator(epoch, writer, phase="baseline"),
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
                record = await self._execute_fresh_bar_phase(
                    record, intent, epoch, firmware_version, action_index=1, permit=permit
                )
                writes += 1
                self._require_epoch(epoch)
                record = await self._execute_fresh_bar_phase(
                    record, intent, epoch, firmware_version, action_index=2, permit=permit
                )
                writes += 1
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                self._require_recovery(operation_id)
                raise

        return self._finalize_bar(record, physical_write_count=writes)

    async def _execute_fresh_bar_phase(
        self,
        record: LocalControlJournalRecord,
        intent: BarScalarQualificationIntent,
        epoch: QualificationEpoch,
        firmware_version: str,
        *,
        action_index: int,
        permit: LocalControlSendPermit,
    ) -> LocalControlJournalRecord:
        endpoint, _firmware = await self._resolve(epoch, firmware_version)
        writer = self._writer_factory(endpoint)
        try:
            status = await writer.connect_and_read()
            before_phase = "baseline" if action_index == 1 else "step"
            self._readback_validator(epoch, writer, phase=before_phase)(status)
            action = record.actions[action_index]
            prepared = self._prepared_from_action(action, endpoint)
            phase_intent = _bar_phase_intent(intent, action_index)
            fresh = writer.prepare_scalar(phase_intent, status)
            if (
                fresh.no_change
                or fresh.forward_payload_sha256 != action.forward_payload_sha256
                or hashlib.sha256(fresh.expected_readback).hexdigest()
                != action.expected_readback_sha256
                or fresh.restore_on_recovery is not action.restore_on_recovery
            ):
                raise QualificationProductionError("bar qualification phase payload changed")
            self._require_epoch(epoch)
            after_phase = "step" if action_index == 1 else "restore"
            return await self._execute_action(
                record,
                action_index,
                writer,
                prepared,
                permit,
                readback_validator=self._readback_validator(epoch, writer, phase=after_phase),
            )
        finally:
            await writer.close()

    def _prepare_bar_operation(
        self,
        operation_id: str,
        epoch: QualificationEpoch,
        endpoint,
        writer: ConnectedDeviceWriter,
        baseline_status: bytes,
        baseline_state: ManualFlowState,
        firmware_version: str,
    ) -> tuple[
        BarScalarQualificationIntent,
        tuple[PreparedDeviceWrite, PreparedDeviceWrite, PreparedDeviceWrite],
    ]:
        baseline_value = self._read_baseline_value(
            writer, epoch.target, baseline_status, baseline_state
        )
        trial_value = self._trial_value(epoch.target.product_key, baseline_value)
        step_request_id = _phase_request_id(operation_id, "step")
        restore_request_id = _phase_request_id(operation_id, "restore")
        same = writer.prepare_scalar(
            LocalControlIntent(
                request_id=_phase_request_id(operation_id, "same"),
                target=epoch.target,
                function=self._spec.function,
                value=baseline_value,
            ),
            baseline_status,
        )
        step = writer.prepare_scalar(
            LocalControlIntent(
                request_id=step_request_id,
                target=epoch.target,
                function=self._spec.function,
                value=trial_value,
            ),
            baseline_status,
        )
        if (
            not same.no_change
            or step.no_change
            or same.forward_payload != same.rollback_payload
            or step.rollback_payload != same.forward_payload
        ):
            raise QualificationProductionError(
                "bar qualification baseline did not form a real step"
            )
        restore = PreparedDeviceWrite(
            target=endpoint,
            function=self._spec.function,
            forward_payload=same.forward_payload,
            rollback_payload=same.forward_payload,
            baseline_status=same.baseline_status,
            expected_readback=same.expected_readback,
            baseline_readback=same.baseline_readback,
            verification_kind=same.verification_kind,
            no_change=False,
            restore_on_recovery=True,
        )
        intent = BarScalarQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            function=self._spec.function,  # type: ignore[arg-type]
            field=self._spec.field,
            baseline_value=baseline_value,
            trial_value=trial_value,
            baseline_mode=baseline_state.mode,
            baseline_linkage=baseline_state.linkage,
            baseline_flow=baseline_state.flow,
            step_request_id=step_request_id,
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
        return intent, (same, step, restore)

    def _create_bar_record(
        self,
        intent: BarScalarQualificationIntent,
        prepared: tuple[PreparedDeviceWrite, PreparedDeviceWrite, PreparedDeviceWrite],
    ) -> LocalControlJournalRecord:
        intent_sha256 = self._journal.install_blob(bar_qualification_intent_bytes(intent))
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
                    "bar qualification journal did not reload exactly"
                )
            if reloaded.phase is not TransactionPhase.PREPARED or any(
                action.phase is not ActionPhase.PREPARED
                or action.target != intent.target
                or action.function != intent.function
                or action.verification_kind != "scalar_fields"
                or action.acknowledgement_sha256 is not None
                for action in reloaded.actions
            ):
                raise QualificationProductionError("bar qualification journal was not preinstalled")
            return reloaded
        except BaseException:
            self._require_recovery(record.request_id)
            raise

    def _finalize_bar(
        self,
        record: LocalControlJournalRecord,
        *,
        physical_write_count: int,
    ) -> QualificationProductionResult:
        intent = self._load_bar_intent(record)
        self._validate_bar_terminal_evidence(record, intent)
        step_action, restore_action = record.actions[1:]
        receipts = (
            _bar_receipt(intent, step_action, _bar_phase_intent(intent, 1), record.updated_at),
            _bar_receipt(intent, restore_action, _bar_phase_intent(intent, 2), record.updated_at),
        )
        receipt_payloads = tuple(qualification_receipt_bytes(item) for item in receipts)
        receipt_digests = tuple(hashlib.sha256(payload).hexdigest() for payload in receipt_payloads)
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
            receipt_count=2,
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )

    def _load_bar_intent(self, record: LocalControlJournalRecord) -> BarScalarQualificationIntent:
        payload = self._journal.load_blob(record.intent_sha256)
        try:
            intent = BarScalarQualificationIntent.model_validate_json(payload)
        except ValueError as error:
            raise QualificationProductionError(
                "bar qualification journal intent is invalid"
            ) from error
        if (
            bar_qualification_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
            or intent.function != self._spec.function
        ):
            raise QualificationProductionError("bar qualification journal intent is not canonical")
        self._require_bar_target(intent.target)
        return intent

    def _validate_bar_terminal_evidence(
        self,
        record: LocalControlJournalRecord,
        intent: BarScalarQualificationIntent,
    ) -> None:
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError("bar qualification journal is not verified")
        if tuple(action.action_id for action in record.actions) != _ACTION_IDS:
            raise QualificationProductionError("bar qualification journal actions changed")
        if any(
            action.phase is not ActionPhase.VERIFIED
            or action.target != intent.target
            or action.function != intent.function
            or action.acknowledgement_sha256 is None
            for action in record.actions
        ):
            raise QualificationProductionError("bar qualification journal evidence is incomplete")
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
                        "bar qualification journal evidence is incomplete"
                    )
                self._journal.load_blob(digest)
        baseline_bytes = _canonical_json(
            {intent.field: internal_function_value(
                intent.target.product_key, intent.function, intent.baseline_value
            )}
        )
        trial_bytes = _canonical_json(
            {intent.field: internal_function_value(
                intent.target.product_key, intent.function, intent.trial_value
            )}
        )
        expected = (baseline_bytes, trial_bytes, baseline_bytes)
        expected_digests = tuple(hashlib.sha256(value).hexdigest() for value in expected)
        if tuple(action.expected_readback_sha256 for action in record.actions) != expected_digests:
            raise QualificationProductionError("bar qualification readback evidence changed")
        baseline_digest = hashlib.sha256(baseline_bytes).hexdigest()
        if any(action.baseline_readback_sha256 != baseline_digest for action in record.actions):
            raise QualificationProductionError("bar qualification recovery baseline changed")
        same, step, restore = record.actions
        if (
            same.forward_payload_sha256 != same.rollback_payload_sha256
            or step.rollback_payload_sha256 != same.forward_payload_sha256
            or restore.forward_payload_sha256 != same.forward_payload_sha256
            or restore.rollback_payload_sha256 != same.forward_payload_sha256
            or same.forward_payload_sha256 == step.forward_payload_sha256
            or len({action.baseline_status_sha256 for action in record.actions}) != 1
            or tuple(action.restore_on_recovery for action in record.actions) != (True, True, True)
        ):
            raise QualificationProductionError("bar qualification payload recovery mapping changed")

    def _readback_validator(
        self, epoch: QualificationEpoch, writer: ConnectedDeviceWriter, *, phase: str
    ):
        relaxed_mode = self._spec.function == "manual_mode" and phase == "step"

        def validate(status: bytes) -> None:
            state = _manual_flow_state(writer, epoch.target, status)
            if relaxed_mode:
                self._require_bar_mode_envelope(epoch, state)
            else:
                self._require_state_envelope(epoch, state)

        return validate

    def _require_bar_mode_envelope(self, epoch: QualificationEpoch, state: ManualFlowState) -> None:
        profile = _manual_flow_family_profile(epoch.target.product_key)
        encoder_minimum, encoder_maximum = declared_app_integer_range(
            epoch.target.product_key, "manual_flow"
        )
        if (
            state.enabled is not True
            or state.timer_enabled is not False
            or state.linkage != profile.required_linkage
            or state.mode not in _bar_manual_mode_choices(epoch.target.product_key)
            or state.flow > _SAFE_FIRST_WRITE_MAXIMUM
            or not epoch.min_power <= state.flow <= epoch.max_power
            or not encoder_minimum <= state.flow <= encoder_maximum
        ):
            raise QualificationProductionError(
                "bar qualification is outside the first-write envelope"
            )

    def _read_baseline_value(
        self,
        writer: ConnectedDeviceWriter,
        target: LocalDeviceBinding,
        status: bytes,
        state: ManualFlowState,
    ) -> int | str:
        if self._spec.function == "manual_mode":
            return state.mode
        # Inspect the selected scalar through the writer without sending or adding an encoder.
        # In particular, an unencodable FeedTime=0 baseline cannot acquire restore authority.
        minimum, _maximum = declared_app_integer_range(target.product_key, self._spec.function)
        probe = writer.prepare_scalar(
            LocalControlIntent(
                request_id="inspect_scalar",
                target=target,
                function=self._spec.function,
                value=minimum,
            ),
            status,
        )
        try:
            decoded = json.loads(probe.baseline_readback)
        except (UnicodeDecodeError, ValueError) as error:
            raise QualificationProductionError("current scalar state is invalid") from error
        value = decoded.get(self._spec.field) if isinstance(decoded, dict) else None
        if isinstance(value, bool) or not isinstance(value, int):
            raise QualificationProductionError("current scalar state is invalid")
        if not minimum <= value <= _maximum:
            raise QualificationProductionError(
                "current scalar is outside the app range for a first write"
            )
        return value

    def _trial_value(self, product_key: str, baseline: int | str) -> int | str:
        if self._spec.function == "manual_mode":
            for choice in _bar_manual_mode_choices(product_key):
                if choice != baseline:
                    return choice
            raise QualificationProductionError("bar manual mode has no distinct trial")
        minimum, maximum = declared_app_integer_range(product_key, self._spec.function)
        if minimum + 1 > maximum:
            raise QualificationProductionError("scalar app range has no two distinct trials")
        return minimum + 1 if baseline == minimum else minimum

    def _require_bar_target(self, target: LocalDeviceBinding) -> None:
        catalog = build_local_control_catalog()
        try:
            descriptor = catalog.find(target.product_key, self._spec.function)
        except KeyError as error:
            raise QualificationProductionError(
                "bar qualification target is not in the catalog"
            ) from error
        if (
            descriptor.family != self._family
            or not descriptor.encoding_available
            or descriptor.fields != (self._spec.field,)
        ):
            raise QualificationProductionError("bar qualification target encoder is unavailable")


def bar_qualification_intent_bytes(intent: BarScalarQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


def _bar_phase_intent(
    intent: BarScalarQualificationIntent, action_index: int
) -> LocalControlIntent:
    if action_index == 0:
        request_id = _phase_request_id(intent.operation_id, "same")
        value = intent.baseline_value
    elif action_index == 1:
        request_id = intent.step_request_id
        value = intent.trial_value
    elif action_index == 2:
        request_id = intent.restore_request_id
        value = intent.baseline_value
    else:
        raise IndexError("bar qualification action index is invalid")
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=intent.function,
        value=value,
    )


def _bar_receipt(
    intent: BarScalarQualificationIntent,
    action: JournalAction,
    phase_intent: LocalControlIntent,
    completed_at: datetime,
) -> LocalControlQualificationReceipt:
    if action.acknowledgement_sha256 is None:
        raise QualificationProductionError("bar qualification action has no acknowledgement")
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
    "BarScalarQualificationIntent",
    "BarScalarQualificationProducer",
    "bar_qualification_intent_bytes",
]
