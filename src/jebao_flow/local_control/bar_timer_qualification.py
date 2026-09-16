"""Crash-safe production of Bar ``schedule_enable`` (timer) qualification receipts.

The audited Bar app does not toggle the timer in isolation. Turning the program tab on also
seeds ``Auto*`` from the schedule slot that covers the app host's current local minute, so a
timer qualification that ran against the device's stored schedule would seed whatever that
schedule happens to hold. This producer therefore owns a bounded four-action operation:

1. ``trial_schedule`` writes a planned 48 x 30-minute Constant image covering 00:00..24:00,
2. ``timer_on`` turns the timer on, whose seed is computed from that planned image,
3. ``timer_off`` turns it back off by copying the fresh manual settings,
4. ``restore_schedule`` writes the operation's own original 384-byte image back verbatim.

Only steps 2 and 3 become receipts; the two image steps are preparation and restoration for
this operation and are never presented as ``schedule`` qualification.

Recovery contract. Every action's rollback is bound to this operation's **initial** snapshot -
TimerOFF, the initial persistent controls owned by each action, and the original raw image - so
that the generic coordinator recovery turns the timer off before it restores the image, and
never re-enables a timer it found on. ``Auto*`` seeds are not owned persistent state: they are
neither restored nor compared against a historical snapshot.

Exactly what the terminal state restores. ``TimerON=false``, the original 384-byte image
verbatim, and the manual controls the audited app OFF command copies: ``Flow``, ``Frequency``,
``Mode`` and ``Linkage`` from the initial snapshot. ``PulseTide`` is **not** among them: the app
writes ``Mode == 'classic' and PulseTide``, so a device whose accepted envelope requires
``Mode='constant'`` has an inactive ``PulseTide=true`` bit normalized to false by the app's own
rule on successful completion. Recovery separately keeps the exact owned initial bits.
That single normalization is reported as a planned diagnostic before the first write instead
of being gated on, so this module must never be described as restoring every initial control
exactly. Re-arming feeding is likewise out of scope and is refused before the first write.

The ON payload must be computed against the planned image rather than the real initial one.
That planned status is a declared expectation used only to build the forward bytes; it is never
recorded as observed evidence. ``baseline_status`` always carries the real initial snapshot.

Offline tests prove the software contract only. They assert nothing about firmware acceptance
or physical safety, and this module issues nothing by itself: the ON step sends a real new seed
payload and requires its own hardware approval.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.local_control.device_writer import (
    _SCHEDULE_GEOMETRY_BY_PRODUCT,
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
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
from jebao_flow.local_control.wavemaker import WavemakerSchedule
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRODUCT_KEY

_LOGGER = logging.getLogger(__name__)

_FUNCTION = "schedule_enable"
_ACTION_IDS = ("trial_schedule", "timer_on", "timer_off", "restore_schedule")
_RECEIPT_INDEXES = (1, 2)
_SLOT_COUNT = 48
_SLOT_MINUTES = 30
_TRIAL_MODE = "constant"
# The release template treats these as running program modes; a generated gap row uses -1.
_RUNNING_AUTO_MODES = frozenset({"classic", "sine", "random", "constant"})


def planned_trial_schedule(flow: int) -> WavemakerSchedule:
    """Build the 48 x 30-minute Constant image that covers the whole day without gaps.

    A full-day cover matters: the app seeds ``AutoFlow`` from the slot covering the current
    minute, and a generated gap row would seed zero instead of the intended output. Constant
    rows also cannot collide with the all-zero pattern the release branch still treats as used.
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
                "mode": _TRIAL_MODE,
                "flow": flow,
                "frequency": 0,
                "pulse_tide": False,
            }
        )
    return WavemakerSchedule.model_validate({"family": "bar", "slots": slots})


class BarTimerQualificationIntent(BaseModel):
    """Durable intent for one bounded Bar timer ON/OFF qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["schedule_enable"] = _FUNCTION
    trial_flow: int
    trial_slot_count: Literal[48] = _SLOT_COUNT
    baseline_timer_enabled: Literal[False] = False
    baseline_enabled: Literal[True] = True
    baseline_linkage: Literal["independent"] = "independent"
    baseline_mode: str = Field(min_length=1, max_length=128)
    baseline_flow: int
    on_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    off_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
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
            raise ValueError("bar timer qualification timestamp must be timezone-aware")
        if self.on_request_id == self.off_request_id:
            raise ValueError("bar timer qualification receipt requests must be distinct")
        if self.target.product_key != LOCAL_WAVEMAKER_PRODUCT_KEY:
            raise ValueError("bar timer qualification target is not the Local Wavemaker")
        if self.min_power > self.max_power:
            raise ValueError("bar timer qualification has an invalid output envelope")
        for value in (self.trial_flow, self.baseline_flow):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("bar timer qualification outputs must be integers")
            if not self.min_power <= value <= self.max_power:
                raise ValueError("bar timer qualification output is outside configured limits")
            if value > _SAFE_FIRST_WRITE_MAXIMUM:
                raise ValueError("bar timer qualification output exceeds the first-write maximum")
        if self.baseline_mode != _TRIAL_MODE:
            raise ValueError("bar timer qualification baseline ownership is invalid")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("bar timer qualification versions must use printable ASCII")
        return self


def _bounded_output(epoch: QualificationEpoch, value: object) -> bool:
    """Keep the existing first-write ceiling even when configured limits are wider."""

    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return epoch.min_power <= value <= epoch.max_power and value <= _SAFE_FIRST_WRITE_MAXIMUM


def bar_timer_qualification_intent_bytes(intent: BarTimerQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


class BarTimerQualificationProducer(ManualFlowQualificationProducer):
    """Execute or offline-finalize one bounded Bar timer qualification."""

    async def execute(  # type: ignore[override]
        self,
        operation_id: str,
        *,
        trial_flow: int,
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        if isinstance(trial_flow, bool) or not isinstance(trial_flow, int):
            raise TypeError("bar timer trial flow must be an integer")
        existing = self._journal.load(operation_id)
        if existing is not None:
            if existing.phase is TransactionPhase.VERIFIED:
                return self._finalize_timer(existing, physical_write_count=0)
            raise QualificationProductionError(
                "bar timer qualification has unfinished or recovered durable state"
            )

        epoch = self._epoch_provider()
        record: LocalControlJournalRecord | None = None
        writes = 0
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("bar timer qualification appeared concurrently")
            self._require_epoch(epoch)
            writer: ConnectedDeviceWriter | None = None
            try:
                endpoint, firmware_version = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                initial_status = await writer.connect_and_read()
                state = _manual_flow_state(writer, epoch.target, initial_status)
                self._require_state_envelope(epoch, state)
                self._require_timer_envelope(
                    epoch, initial_status, trial_flow, operation_id=operation_id
                )
                intent, prepared = self._prepare_timer_operation(
                    operation_id, epoch, writer, initial_status, state, trial_flow, firmware_version
                )
                record = self._create_timer_record(intent, prepared)
                self._require_epoch(epoch)
                for index, item in enumerate(prepared):
                    self._require_epoch(epoch)
                    record = await self._execute_action(
                        record,
                        index,
                        writer,
                        item,
                        permit,
                        readback_validator=self._timer_readback_validator(epoch, writer),
                    )
                    writes += 1
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
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

        return self._finalize_timer(record, physical_write_count=writes)

    def _prepare_timer_operation(
        self,
        operation_id: str,
        epoch: QualificationEpoch,
        writer: ConnectedDeviceWriter,
        initial_status: bytes,
        state: ManualFlowState,
        trial_flow: int,
        firmware_version: str,
    ) -> tuple[BarTimerQualificationIntent, tuple[PreparedDeviceWrite, ...]]:
        product_key = epoch.target.product_key
        original_image = extract_current_schedule_image(product_key, initial_status)
        trial = planned_trial_schedule(trial_flow)
        if trial.product_key != product_key:
            raise QualificationProductionError("bar timer trial image is not this product")
        trial_image = trial.compile_image()
        if trial_image == original_image:
            raise QualificationProductionError(
                "bar timer trial image must differ from the original"
            )

        on_request_id = _phase_request_id(operation_id, "timer_on")
        off_request_id = _phase_request_id(operation_id, "timer_off")

        trial_write = writer.prepare_scalar(
            LocalControlIntent(
                request_id=_phase_request_id(operation_id, "trial_schedule"),
                target=epoch.target,
                function="schedule",
                value=trial.model_dump(mode="json"),
            ),
            initial_status,
        )

        # The ON seed must be computed against the image this operation will have written.
        # This declared status is an expectation used only to build forward bytes; the durable
        # record keeps the real initial snapshot as baseline_status below.
        planned_status = self._planned_status(product_key, initial_status, trial_image)
        on_intent = LocalControlIntent(
            request_id=on_request_id,
            target=epoch.target,
            function=_FUNCTION,
            value=True,
        )
        # The ON persistent fields are identical in the planned and the real initial status -
        # only the schedule region differs - so the rollback this call derives already names the
        # operation's initial state. Preparing ON against the real initial status instead would be
        # refused, because the original image sits outside the approved output bounds.
        on_forward = writer.prepare_scalar(on_intent, planned_status)
        timer_on = replace(on_forward, baseline_status=bytes(initial_status), no_change=False)

        # OFF copies fresh manual settings, which this operation never changes, so the initial
        # snapshot already yields the exact forward bytes. Its rollback stays TimerOFF.
        timer_off = replace(
            writer.prepare_scalar(
                LocalControlIntent(
                    request_id=off_request_id,
                    target=epoch.target,
                    function=_FUNCTION,
                    value=False,
                ),
                initial_status,
            ),
            no_change=False,
        )

        # The original image is restored verbatim from raw bytes; it is never decoded into a
        # declared model, so an original this producer cannot parse is still restored exactly.
        restore_payload = build_exact_schedule_payload(product_key, original_image)
        restore_schedule = PreparedDeviceWrite(
            target=writer.endpoint,
            function="schedule",
            forward_payload=restore_payload,
            rollback_payload=restore_payload,
            baseline_status=bytes(initial_status),
            expected_readback=original_image,
            baseline_readback=original_image,
            verification_kind="schedule_image",
            no_change=False,
        )

        prepared = (trial_write, timer_on, timer_off, restore_schedule)
        self._require_owned_rollback(prepared, original_image)
        intent = BarTimerQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            trial_flow=trial_flow,
            baseline_mode=state.mode,
            baseline_flow=state.flow,
            on_request_id=on_request_id,
            off_request_id=off_request_id,
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
        return intent, prepared

    def _require_timer_envelope(
        self,
        epoch: QualificationEpoch,
        initial_status: bytes,
        trial_flow: int,
        *,
        operation_id: str,
    ) -> None:
        """Bound the trial output and keep feeding outside this operation.

        The ON command writes ``FeedSwitch=false`` while the OFF command does not write
        ``FeedSwitch`` at all, so a device that starts with feeding on would not have it restored
        even on a normal completion. Re-arming feeding is out of scope here, so such a device is
        refused before the first write instead of being silently changed.
        """

        if not _bounded_output(epoch, trial_flow):
            raise QualificationProductionError(
                "bar timer trial output is outside the approved first-write bounds"
            )
        decoded = get_product_schema(epoch.target.product_key).decode_status(initial_status)
        if decoded["FeedSwitch"] is not False:
            raise QualificationProductionError(
                "bar timer qualification requires feeding off before the first write"
            )
        self._report_inactive_pulse_normalization(decoded, operation_id=operation_id)

    @staticmethod
    def _report_inactive_pulse_normalization(
        decoded: dict[str, object],
        *,
        operation_id: str,
    ) -> None:
        """Record the one initial control the app OFF command will not copy back.

        The audited OFF command writes ``PulseTide = Mode == 'classic' and PulseTide``. Inside
        this operation's accepted envelope ``Mode`` is ``constant``, so an initial ``PulseTide``
        of true is cleared by the app's own rule and is not restored. Adding an admission gate
        for an inactive value would only multiply gates, so the fact is reported instead. A
        failing observer must never change the operation's outcome.
        """

        try:
            if decoded.get("PulseTide") is not True or decoded.get("Mode") == "classic":
                return
            _LOGGER.warning(
                "%s",
                json.dumps(
                    {
                        "event": "bar_timer_inactive_pulse_normalization_planned",
                        "request_sha256": hashlib.sha256(operation_id.encode("ascii")).hexdigest(),
                        "initial_mode": decoded.get("Mode"),
                        "initial_pulse_tide": True,
                        "expected_pulse_tide": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        except BaseException:
            pass

    @staticmethod
    def _planned_status(product_key: str, initial_status: bytes, trial_image: bytes) -> bytes:
        geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[product_key]
        if len(trial_image) != geometry.image_size:
            raise QualificationProductionError("bar timer trial image has the wrong size")
        planned = bytearray(initial_status)
        start = geometry.status_offset
        planned[start : start + geometry.image_size] = trial_image
        return bytes(planned)

    @staticmethod
    def _require_owned_rollback(
        prepared: tuple[PreparedDeviceWrite, ...],
        original_image: bytes,
    ) -> None:
        """Every rollback must name this operation's initial state, never an intermediate one.

        The ON and OFF app commands own different persistent field sets, so their rollback bytes
        differ by design and must not be compared for equality. What has to hold is that both
        restore the initial TimerOFF and that neither carries an ``Auto*`` seed, which is not
        owned state and is never replayed.
        """

        if len({item.baseline_status for item in prepared}) != 1:
            raise QualificationProductionError("bar timer actions must share one initial snapshot")
        for index in (1, 2):
            baseline = json.loads(prepared[index].baseline_readback)
            if baseline.get("TimerON") is not False:
                raise QualificationProductionError(
                    "bar timer rollbacks must restore the initial TimerOFF state"
                )
            if any(name.startswith("Auto") for name in baseline):
                raise QualificationProductionError("bar timer rollbacks must not carry Auto seeds")
        for index in (0, 3):
            if prepared[index].baseline_readback != original_image:
                raise QualificationProductionError(
                    "bar timer image rollbacks must restore the original image"
                )

    def _timer_readback_validator(
        self,
        epoch: QualificationEpoch,
        writer: ConnectedDeviceWriter,
    ):
        """Check owned persistent ownership, plus the actual running output bound.

        ``Auto*`` is never compared for equality with the seed we sent: a seed is not owned
        state and the device may legitimately report something else. But once the timer is
        actually on, whatever ``AutoFlow`` the device reports is a real output. If the seed was
        not accepted and a stale value outside the approved bounds is running, that is a safety
        violation, so the operation stops here and recovers to TimerOFF instead of continuing
        or issuing receipts. Reaching the bound check is never treated as proof of acceptance.
        """

        def validate(status: bytes) -> None:
            state = _manual_flow_state(writer, epoch.target, status)
            if (
                state.enabled is not True
                or state.linkage != "independent"
                or not _bounded_output(epoch, state.flow)
            ):
                raise QualificationProductionError(
                    "bar timer qualification phase state left the owned envelope"
                )
            decoded = get_product_schema(epoch.target.product_key).decode_status(status)
            if decoded["TimerON"] and decoded["AutoMode"] in _RUNNING_AUTO_MODES:
                if not _bounded_output(epoch, decoded["AutoFlow"]):
                    raise QualificationProductionError(
                        "bar timer active output is outside the approved bounds"
                    )

        return validate

    def _create_timer_record(
        self,
        intent: BarTimerQualificationIntent,
        prepared: tuple[PreparedDeviceWrite, ...],
    ) -> LocalControlJournalRecord:
        intent_sha256 = self._journal.install_blob(bar_timer_qualification_intent_bytes(intent))
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
                    "bar timer qualification journal did not reload exactly"
                )
            if any(
                action.phase is not ActionPhase.PREPARED
                or action.target != intent.target
                or action.acknowledgement_sha256 is not None
                or action.restore_on_recovery is not True
                for action in reloaded.actions
            ):
                raise QualificationProductionError(
                    "bar timer qualification journal was not safely preinstalled"
                )
            return reloaded
        except BaseException:
            self._require_recovery(record.request_id)
            raise

    def _finalize_timer(
        self,
        record: LocalControlJournalRecord,
        *,
        physical_write_count: int,
    ) -> QualificationProductionResult:
        intent = self._load_timer_intent(record)
        self._validate_timer_terminal_evidence(record, intent)
        receipts = tuple(
            _timer_receipt(
                intent, record.actions[index], _timer_phase_intent(intent, index), record.updated_at
            )
            for index in _RECEIPT_INDEXES
        )
        receipt_digests = tuple(
            hashlib.sha256(qualification_receipt_bytes(item)).hexdigest() for item in receipts
        )
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
            receipt_count=len(receipts),
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )

    def _load_timer_intent(self, record: LocalControlJournalRecord) -> BarTimerQualificationIntent:
        payload = self._journal.load_blob(record.intent_sha256)
        try:
            intent = BarTimerQualificationIntent.model_validate_json(payload)
        except ValueError as error:
            raise QualificationProductionError(
                "bar timer qualification journal intent is invalid"
            ) from error
        if (
            bar_timer_qualification_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
        ):
            raise QualificationProductionError(
                "bar timer qualification journal intent is not canonical"
            )
        return intent

    def _validate_timer_terminal_evidence(
        self,
        record: LocalControlJournalRecord,
        intent: BarTimerQualificationIntent,
    ) -> None:
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError("bar timer qualification journal is not verified")
        if tuple(action.action_id for action in record.actions) != _ACTION_IDS:
            raise QualificationProductionError("bar timer qualification journal actions changed")
        if any(
            action.phase is not ActionPhase.VERIFIED
            or action.target != intent.target
            or action.acknowledgement_sha256 is None
            or action.restore_on_recovery is not True
            for action in record.actions
        ):
            raise QualificationProductionError(
                "bar timer qualification journal evidence is incomplete"
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
                        "bar timer qualification journal evidence is incomplete"
                    )
                self._journal.load_blob(digest)
        functions = tuple(action.function for action in record.actions)
        if functions != ("schedule", _FUNCTION, _FUNCTION, "schedule"):
            raise QualificationProductionError("bar timer qualification action functions changed")
        if len({action.baseline_status_sha256 for action in record.actions}) != 1:
            raise QualificationProductionError(
                "bar timer qualification actions must share one initial snapshot"
            )
        on_action, off_action = (record.actions[index] for index in _RECEIPT_INDEXES)
        for action in (on_action, off_action):
            baseline = json.loads(self._journal.load_blob(action.baseline_readback_sha256))
            if baseline.get("TimerON") is not False or any(
                name.startswith("Auto") for name in baseline
            ):
                raise QualificationProductionError(
                    "bar timer rollbacks must restore TimerOFF without Auto seeds"
                )
        if on_action.forward_payload_sha256 == off_action.forward_payload_sha256:
            raise QualificationProductionError("bar timer ON and OFF payloads must differ")
        trial_action, restore_action = record.actions[0], record.actions[3]
        if trial_action.baseline_readback_sha256 != restore_action.expected_readback_sha256:
            raise QualificationProductionError("bar timer restoration must name the original image")
        if trial_action.expected_readback_sha256 == restore_action.expected_readback_sha256:
            raise QualificationProductionError(
                "bar timer trial image must differ from the original"
            )


def _timer_phase_intent(
    intent: BarTimerQualificationIntent,
    action_index: int,
) -> LocalControlIntent:
    if action_index == 1:
        request_id, value = intent.on_request_id, True
    elif action_index == 2:
        request_id, value = intent.off_request_id, False
    else:
        raise IndexError("bar timer receipt index is invalid")
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=_FUNCTION,
        value=value,
    )


def _timer_receipt(
    intent: BarTimerQualificationIntent,
    action: JournalAction,
    phase_intent: LocalControlIntent,
    completed_at: datetime,
) -> LocalControlQualificationReceipt:
    if action.acknowledgement_sha256 is None:
        raise QualificationProductionError("bar timer qualification action has no acknowledgement")
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
    "BarTimerQualificationIntent",
    "BarTimerQualificationProducer",
    "bar_timer_qualification_intent_bytes",
    "planned_trial_schedule",
]
