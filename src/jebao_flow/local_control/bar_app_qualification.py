"""Crash-safe production of Bar app-control qualification receipts.

The Bar app writes ``Mode``, ``PulseTide`` and ``Linkage`` only inside compound commands that
also carry the other manual parameters, so a qualification for those functions cannot be the
usual single-field three-frame scalar operation. This producer instead owns a short bounded
sequence per function, issues receipts for exactly the two real app commands in it, and then
restores the persistent fields the sequence actually wrote from its own initial raw snapshot.

Sequences, all starting from the audited scalar envelope (on, timer off, independent, constant,
output within the configured bounds and the first-write ceiling):

``manual_mode``
    ``trial_mode`` writes Classic, ``restore_mode`` writes Constant back, then ``owned_restore``.

``pulse_or_tide``
    ``prepare_mode`` writes Classic, because the app exposes ``PulseTide`` only there. That frame
    is preparation, not evidence, and it is the reason the trial is always a real step: the mode
    command forces ``PulseTide`` to false, so ``trial_pulse`` (true) and ``restore_pulse``
    (false) change the bit whatever the initial value was. ``owned_restore`` then returns both
    the mode and the initial bit.

``linkage_role``
    ``trial_master`` and ``trial_slave`` use the manual linked-mode and slave-edit app commands.
    ``owned_restore`` returns the initial independent role and all other fields those commands
    wrote. Asking for ``independent`` instead would use ``switchToManual``, which omits FeedSwitch
    and may normalize PulseTide rather than restore their captured values.

``feed_switch``
    ``trial_feed`` writes the opposite of the initial value and ``restore_feed`` writes it back.
    That second frame already returns the only field this function owns, so no separate restore
    frame is added.

What the receipts are. Only the two real app commands per sequence become receipts. The trailing
restore is a distinct ``bar_owned_controls`` action -- recovery authority, not an app command --
and is never presented as a qualification of anything, so it cannot forge a receipt for a
function it did not exercise.

Recovery contract. Every action's rollback is derived from this operation's **initial** raw
snapshot and names only that action's own fields; no intermediate snapshot is ever recorded as
recovery authority, and ``baseline_status`` carries the real initial bytes on every action. The
trailing restore writes the union of the persistent fields the sequence actually wrote, taken
from those same initial bytes, and its expected readback is checked against them exactly before
any receipt is issued.

Feeding. ``FeedSwitch`` is restored as a datapoint value. The Bar schema has no elapsed or
remaining feeding datapoint, so feeding progress is neither observed nor restored here, and no
receipt in this module claims otherwise. A device that starts with feeding already on is
reported before the first write rather than refused, because ``feed_switch`` is exactly the
function whose ON state has to be reachable.

Offline tests prove the software contract only. They assert nothing about firmware acceptance or
physical safety, and this module issues nothing by itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.local_control.bar_controls import prepare_bar_control_command
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
    _canonical_values,
    build_control_payload,
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
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRODUCT_KEY

_LOGGER = logging.getLogger(__name__)

_OWNED_RESTORE_FUNCTION = "bar_owned_controls"
# Each action's actual raw readback is preserved and named here, so finalization can re-check
# what the device really reported instead of trusting the phase transition alone.
_OBSERVED = "bar_app_status_sha256."
# The release template treats these as running program modes; a generated gap row uses -1.
_RUNNING_AUTO_MODES = frozenset({"classic", "sine", "random", "constant"})
_OWNED_RESTORE_ACTION = "owned_restore"
_ENVELOPE_MODE = "constant"
_TRIAL_MODE = "classic"
_ENVELOPE_LINKAGE = "independent"
# prepare_bar_restore_controls accepts exactly these persistent Bar controls.
_RESTORABLE_FIELDS = frozenset(
    {"TimerON", "Flow", "Frequency", "Mode", "PulseTide", "Linkage", "FeedSwitch"}
)


@dataclass(frozen=True, slots=True)
class _BarAppPlan:
    """One function's fixed frame sequence and the envelope it is allowed to move through."""

    function: str
    action_ids: tuple[str, ...]
    receipt_indexes: tuple[int, int]
    allowed_modes: frozenset[str]
    allowed_linkages: frozenset[str]
    owned_restore: bool


_PLANS = {
    "manual_mode": _BarAppPlan(
        function="manual_mode",
        action_ids=("trial_mode", "restore_mode", _OWNED_RESTORE_ACTION),
        receipt_indexes=(0, 1),
        allowed_modes=frozenset({_ENVELOPE_MODE, _TRIAL_MODE}),
        allowed_linkages=frozenset({_ENVELOPE_LINKAGE}),
        owned_restore=True,
    ),
    "pulse_or_tide": _BarAppPlan(
        function="pulse_or_tide",
        action_ids=("prepare_mode", "trial_pulse", "restore_pulse", _OWNED_RESTORE_ACTION),
        receipt_indexes=(1, 2),
        allowed_modes=frozenset({_ENVELOPE_MODE, _TRIAL_MODE}),
        allowed_linkages=frozenset({_ENVELOPE_LINKAGE}),
        owned_restore=True,
    ),
    "linkage_role": _BarAppPlan(
        function="linkage_role",
        action_ids=("trial_master", "trial_slave", _OWNED_RESTORE_ACTION),
        receipt_indexes=(0, 1),
        allowed_modes=frozenset({_ENVELOPE_MODE}),
        allowed_linkages=frozenset({_ENVELOPE_LINKAGE, "master", "slave"}),
        owned_restore=True,
    ),
    "feed_switch": _BarAppPlan(
        function="feed_switch",
        action_ids=("trial_feed", "restore_feed"),
        receipt_indexes=(0, 1),
        allowed_modes=frozenset({_ENVELOPE_MODE}),
        allowed_linkages=frozenset({_ENVELOPE_LINKAGE}),
        owned_restore=False,
    ),
}

_SUPPORTED_FUNCTIONS = tuple(sorted(_PLANS))


def _frame_values(function: str, *, initial_feed_switch: bool) -> tuple[tuple[str, Any], ...]:
    """The (app function, value) of every sent frame except the trailing owned restore."""

    if function == "manual_mode":
        return (("manual_mode", _TRIAL_MODE), ("manual_mode", _ENVELOPE_MODE))
    if function == "pulse_or_tide":
        return (
            ("manual_mode", _TRIAL_MODE),
            ("pulse_or_tide", True),
            ("pulse_or_tide", False),
        )
    if function == "linkage_role":
        return (("linkage_role", "master"), ("linkage_role", "slave"))
    return (
        ("feed_switch", not initial_feed_switch),
        ("feed_switch", initial_feed_switch),
    )


class BarAppControlQualificationIntent(BaseModel):
    """Durable intent for one bounded Bar app-control qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["manual_mode", "pulse_or_tide", "linkage_role", "feed_switch"]
    baseline_enabled: Literal[True] = True
    baseline_timer_enabled: Literal[False] = False
    baseline_mode: Literal["constant"] = _ENVELOPE_MODE
    baseline_linkage: Literal["independent"] = _ENVELOPE_LINKAGE
    baseline_flow: int
    baseline_frequency: int
    baseline_pulse_tide: bool
    baseline_feed_switch: bool
    first_value: bool | str
    second_value: bool | str
    # Every persistent field the sequence writes, whether or not a restore frame follows.
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
            raise ValueError("bar app qualification timestamp must be timezone-aware")
        if self.first_request_id == self.second_request_id:
            raise ValueError("bar app qualification receipt requests must be distinct")
        if self.target.product_key != LOCAL_WAVEMAKER_PRODUCT_KEY:
            raise ValueError("bar app qualification target is not the Local Wavemaker")
        if self.min_power > self.max_power:
            raise ValueError("bar app qualification has an invalid output envelope")
        plan = _PLANS[self.function]
        expected = _frame_values(self.function, initial_feed_switch=self.baseline_feed_switch)
        first, second = (expected[index][1] for index in plan.receipt_indexes)
        if type(self.first_value) is not type(first) or self.first_value != first:
            raise ValueError("bar app qualification first value is not the planned command")
        if type(self.second_value) is not type(second) or self.second_value != second:
            raise ValueError("bar app qualification second value is not the planned command")
        if self.first_value == self.second_value:
            raise ValueError("bar app qualification receipts must be a real step and back")
        if (
            not self.owned_fields
            or len(set(self.owned_fields)) != len(self.owned_fields)
            or tuple(sorted(self.owned_fields)) != self.owned_fields
            or not set(self.owned_fields) <= set(_RESTORABLE_FIELDS)
        ):
            raise ValueError("bar app qualification owned fields are invalid")
        for value in (self.baseline_flow, self.baseline_frequency):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("bar app qualification outputs must be integers")
        if (
            not self.min_power <= self.baseline_flow <= self.max_power
            or self.baseline_flow > _SAFE_FIRST_WRITE_MAXIMUM
        ):
            raise ValueError("bar app qualification output is outside configured limits")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("bar app qualification versions must use printable ASCII")
        return self


def bar_app_qualification_intent_bytes(intent: BarAppControlQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


class BarAppControlQualificationProducer(ManualFlowQualificationProducer):
    """Execute or offline-finalize one bounded Bar app-control qualification."""

    def __init__(self, *, function: str, **kwargs: Any) -> None:
        if function not in _PLANS:
            raise ValueError("bar app qualification function is not supported")
        super().__init__(**kwargs)
        self._plan = _PLANS[function]

    async def execute(  # type: ignore[override]
        self,
        operation_id: str,
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        plan = self._plan
        existing = self._journal.load(operation_id)
        if existing is not None:
            if existing.phase is TransactionPhase.VERIFIED:
                return self._finalize_app(existing, physical_write_count=0)
            raise QualificationProductionError(
                "bar app qualification has unfinished or recovered durable state"
            )

        epoch = self._epoch_provider()
        record: LocalControlJournalRecord | None = None
        writes = 0
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("bar app qualification appeared concurrently")
            self._require_epoch(epoch)
            writer: ConnectedDeviceWriter | None = None
            try:
                endpoint, firmware_version = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                initial_status = await writer.connect_and_read()
                state = _manual_flow_state(writer, epoch.target, initial_status)
                self._require_state_envelope(epoch, state)
                decoded = self._decoded(epoch, initial_status)
                self._report_unrestorable_feeding(decoded, operation_id=operation_id)
                intent, prepared = self._prepare_app_operation(
                    operation_id, epoch, writer, endpoint, initial_status, state, firmware_version
                )
                record = self._create_app_record(intent, prepared)
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
                    record = await self._execute_app_frame(
                        record,
                        index,
                        epoch,
                        intent,
                        firmware_version,
                        initial_status,
                        permit,
                    )
                    writes += 1
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                self._require_recovery(operation_id)
                raise

        return self._finalize_app(record, physical_write_count=writes)

    # -- preparation ------------------------------------------------------------------

    def _prepare_app_operation(
        self,
        operation_id: str,
        epoch: QualificationEpoch,
        writer: ConnectedDeviceWriter,
        endpoint: ResolvedEndpoint,
        initial_status: bytes,
        state: ManualFlowState,
        firmware_version: str,
    ) -> tuple[BarAppControlQualificationIntent, tuple[PreparedDeviceWrite, ...]]:
        plan = self._plan
        decoded = self._decoded(epoch, initial_status)
        frames = _frame_values(plan.function, initial_feed_switch=decoded["FeedSwitch"])
        if len(frames) + int(plan.owned_restore) != len(plan.action_ids):
            raise QualificationProductionError("bar app qualification plan is inconsistent")

        prepared: list[PreparedDeviceWrite] = []
        for action_id, (function, value) in zip(plan.action_ids, frames, strict=False):
            # Every frame is prepared against the initial snapshot, so its rollback names this
            # operation's own starting state and never an intermediate one. The sequences never
            # change Flow or Frequency, so the bytes a fresh status would produce are identical.
            prepared.append(
                writer.prepare_scalar(
                    LocalControlIntent(
                        request_id=_phase_request_id(operation_id, action_id),
                        target=epoch.target,
                        function=function,
                        value=value,
                    ),
                    initial_status,
                )
            )
        owned_fields = self._owned_field_union(tuple(prepared))
        if plan.owned_restore:
            prepared.append(
                writer.prepare_bar_restore_controls(initial_status, fields=owned_fields)
            )
        frozen = tuple(prepared)
        self._require_owned_rollback(frozen, initial_status, decoded)

        first_index, second_index = plan.receipt_indexes
        intent = BarAppControlQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            function=plan.function,
            baseline_flow=state.flow,
            baseline_frequency=decoded["Frequency"],
            baseline_pulse_tide=decoded["PulseTide"],
            baseline_feed_switch=decoded["FeedSwitch"],
            first_value=frames[first_index][1],
            second_value=frames[second_index][1],
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
    def _owned_field_union(prepared: tuple[PreparedDeviceWrite, ...]) -> tuple[str, ...]:
        """The persistent fields the sequence actually writes, in a stable order."""

        names: set[str] = set()
        for item in prepared:
            if item.verification_kind != "scalar_fields":
                raise QualificationProductionError("bar app qualification frame is not scalar")
            try:
                written = json.loads(item.expected_readback)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise QualificationProductionError(
                    "bar app qualification frame readback is invalid"
                ) from error
            if not isinstance(written, dict) or not written:
                raise QualificationProductionError("bar app qualification frame wrote no field")
            if not set(written) <= _RESTORABLE_FIELDS:
                raise QualificationProductionError(
                    "bar app qualification wrote a field it cannot restore"
                )
            names |= set(written)
        return tuple(sorted(names))

    def _require_owned_rollback(
        self,
        prepared: tuple[PreparedDeviceWrite, ...],
        initial_status: bytes,
        decoded: dict[str, Any],
    ) -> None:
        """Every rollback must name this operation's initial state, never an intermediate one."""

        plan = self._plan
        if len({item.baseline_status for item in prepared}) != 1:
            raise QualificationProductionError("bar app actions must share one initial snapshot")
        if prepared[0].baseline_status != bytes(initial_status):
            raise QualificationProductionError("bar app actions must carry the initial snapshot")
        for item in prepared:
            baseline = json.loads(item.baseline_readback)
            if any(name.startswith("Auto") for name in baseline):
                raise QualificationProductionError("bar app rollbacks must not carry Auto seeds")
            if any(decoded[name] != value for name, value in baseline.items()):
                raise QualificationProductionError(
                    "bar app rollbacks must restore the initial values"
                )
            if baseline.get("TimerON", False) is not False:
                raise QualificationProductionError(
                    "bar app rollbacks must restore the initial TimerOFF state"
                )
        first, second = (prepared[index] for index in plan.receipt_indexes)
        if first.forward_payload == second.forward_payload:
            raise QualificationProductionError("bar app receipt payloads must differ")
        if plan.owned_restore:
            restore = prepared[-1]
            if restore.function != _OWNED_RESTORE_FUNCTION:
                raise QualificationProductionError("bar app restore must be its own function")
            written = json.loads(restore.expected_readback)
            if any(decoded[name] != value for name, value in written.items()):
                raise QualificationProductionError(
                    "bar app restore must write the exact initial values"
                )

    # -- execution --------------------------------------------------------------------

    async def _execute_app_frame(
        self,
        record: LocalControlJournalRecord,
        index: int,
        epoch: QualificationEpoch,
        intent: BarAppControlQualificationIntent,
        firmware_version: str,
        initial_status: bytes,
        permit,
    ) -> LocalControlJournalRecord:
        """Resolve, connect and re-derive this frame before sending the durable bytes."""

        endpoint, _firmware = await self._resolve(epoch, firmware_version)
        writer = self._writer_factory(endpoint)
        try:
            status = await writer.connect_and_read()
            self._require_frame_envelope(epoch, intent, status)
            action = record.actions[index]
            prepared = self._prepared_from_action(action, endpoint)
            fresh = self._fresh_frame(writer, epoch, intent, index, status, initial_status)
            if (
                fresh.forward_payload_sha256 != action.forward_payload_sha256
                or hashlib.sha256(fresh.expected_readback).hexdigest()
                != action.expected_readback_sha256
            ):
                raise QualificationProductionError("bar app qualification frame payload changed")
            self._require_epoch(epoch)
            observed: list[str] = []

            def validate(readback: bytes, *, observed: list[str] = observed) -> None:
                # Preserve the real bytes first: an envelope refusal must still leave the
                # device's own report behind for the finalizer and for recovery.
                observed.append(self._journal.install_blob(readback))
                self._require_frame_envelope(epoch, intent, readback)

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
        intent: BarAppControlQualificationIntent,
        index: int,
        status: bytes,
        initial_status: bytes,
    ) -> PreparedDeviceWrite:
        plan = self._plan
        if plan.owned_restore and index == len(plan.action_ids) - 1:
            # The restore is defined by the initial bytes alone, never by what is there now.
            return writer.prepare_bar_restore_controls(
                initial_status, fields=tuple(intent.owned_fields)
            )
        function, value = _frame_values(
            plan.function, initial_feed_switch=intent.baseline_feed_switch
        )[index]
        return writer.prepare_scalar(
            LocalControlIntent(
                request_id=_phase_request_id(intent.operation_id, plan.action_ids[index]),
                target=epoch.target,
                function=function,
                value=value,
            ),
            status,
        )

    def _require_frame_envelope(
        self,
        epoch: QualificationEpoch,
        intent: BarAppControlQualificationIntent,
        status: bytes,
    ) -> None:
        """Bound the states this operation is allowed to pass through.

        The base scalar envelope cannot be reused: these sequences deliberately move ``Mode`` and
        ``Linkage`` away from the admission values. Only the fields the operation actually writes
        are held to the owned envelope, so a field it never touches -- the frequency during a
        feeding qualification, for example -- may change under an operator's hand without this
        operation refusing. Refusing there would be a gate on state this operation does not own,
        and a value the writer freshly copies is already covered by comparing the freshly derived
        frame against the durable one before anything is sent.

        The existing trial ON condition and the output reported for the selected manual/program
        path remain bounded. This checks datapoints, not the physical water flow.
        """

        plan = self._plan
        decoded = self._decoded(epoch, status)
        owned = set(intent.owned_fields)
        if decoded["SwitchON"] is not True:
            raise QualificationProductionError("bar app qualification requires the pump to stay on")
        if (
            ("TimerON" in owned and decoded["TimerON"] is not False)
            or ("Mode" in owned and decoded["Mode"] not in plan.allowed_modes)
            or ("Linkage" in owned and decoded["Linkage"] not in plan.allowed_linkages)
        ):
            raise QualificationProductionError(
                "bar app qualification state left the owned envelope"
            )
        self._require_bounded_output(epoch, decoded)

    @staticmethod
    def _require_bounded_output(epoch: QualificationEpoch, decoded: dict[str, Any]) -> None:
        """Bound the reported output for the selected manual/program path.

        TimerOFF selects manual ``Flow``. TimerON selects ``AutoFlow`` for a reported waveform
        mode; stopped/feeding modes do not report a waveform output to check here. This is not
        a measurement of circulation or a claim about firmware handling of feeding counters.
        """

        if decoded["TimerON"]:
            if decoded["AutoMode"] not in _RUNNING_AUTO_MODES:
                return
            running = decoded["AutoFlow"]
        else:
            running = decoded["Flow"]
        if (
            isinstance(running, bool)
            or not isinstance(running, int)
            or running > _SAFE_FIRST_WRITE_MAXIMUM
            or not epoch.min_power <= running <= epoch.max_power
        ):
            raise QualificationProductionError("bar app qualification output left its bounds")

    @staticmethod
    def _decoded(epoch: QualificationEpoch, status: bytes) -> dict[str, Any]:
        try:
            return get_product_schema(epoch.target.product_key).decode_status(status)
        except (KeyError, ValueError) as error:
            raise QualificationProductionError("current bar state is invalid") from error

    @staticmethod
    def _report_unrestorable_feeding(decoded: dict[str, Any], *, operation_id: str) -> None:
        """Record that an already-running feed's progress is not observable or restorable."""

        try:
            if decoded.get("FeedSwitch") is not True:
                return
            _LOGGER.warning(
                "%s",
                json.dumps(
                    {
                        "event": "bar_app_feeding_progress_not_restorable",
                        "request_sha256": hashlib.sha256(operation_id.encode("ascii")).hexdigest(),
                        "initial_feed_switch": True,
                        "initial_feed_time": decoded.get("FeedTime"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        except BaseException:
            pass

    # -- durable record ---------------------------------------------------------------

    def _create_app_record(
        self,
        intent: BarAppControlQualificationIntent,
        prepared: tuple[PreparedDeviceWrite, ...],
    ) -> LocalControlJournalRecord:
        intent_sha256 = self._journal.install_blob(bar_app_qualification_intent_bytes(intent))
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
                    "bar app qualification journal did not reload exactly"
                )
            if any(
                action.phase is not ActionPhase.PREPARED
                or action.target != intent.target
                or action.acknowledgement_sha256 is not None
                or action.restore_on_recovery is not True
                for action in reloaded.actions
            ):
                raise QualificationProductionError(
                    "bar app qualification journal was not safely preinstalled"
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
        intent = self._load_app_intent(record)
        self._validate_app_terminal_evidence(record, intent)
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

    def _load_app_intent(
        self, record: LocalControlJournalRecord
    ) -> BarAppControlQualificationIntent:
        payload = self._journal.load_blob(record.intent_sha256)
        try:
            intent = BarAppControlQualificationIntent.model_validate_json(payload)
        except ValueError as error:
            raise QualificationProductionError(
                "bar app qualification journal intent is invalid"
            ) from error
        if (
            bar_app_qualification_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
            or intent.function != self._plan.function
        ):
            raise QualificationProductionError(
                "bar app qualification journal intent is not canonical"
            )
        return intent

    def _validate_app_terminal_evidence(
        self,
        record: LocalControlJournalRecord,
        intent: BarAppControlQualificationIntent,
    ) -> None:
        plan = self._plan
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError("bar app qualification journal is not verified")
        if tuple(action.action_id for action in record.actions) != plan.action_ids:
            raise QualificationProductionError("bar app qualification journal actions changed")
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
                "bar app qualification journal evidence is incomplete"
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
                        "bar app qualification journal evidence is incomplete"
                    )
                self._journal.load_blob(digest)
        frames = _frame_values(plan.function, initial_feed_switch=intent.baseline_feed_switch)
        expected_functions = tuple(function for function, _value in frames)
        if plan.owned_restore:
            expected_functions += (_OWNED_RESTORE_FUNCTION,)
        if tuple(action.function for action in record.actions) != expected_functions:
            raise QualificationProductionError("bar app qualification action functions changed")
        if len({action.baseline_status_sha256 for action in record.actions}) != 1:
            raise QualificationProductionError(
                "bar app qualification actions must share one initial snapshot"
            )
        initial_status = self._journal.load_blob(record.actions[0].baseline_status_sha256)
        decoded = get_product_schema(intent.target.product_key).decode_status(initial_status)
        for action in record.actions:
            baseline = json.loads(self._journal.load_blob(action.baseline_readback_sha256))
            if any(name.startswith("Auto") for name in baseline):
                raise QualificationProductionError("bar app rollbacks must not carry Auto seeds")
            if any(decoded[name] != value for name, value in baseline.items()):
                raise QualificationProductionError(
                    "bar app rollbacks must restore the initial values"
                )
        first, second = (record.actions[index] for index in plan.receipt_indexes)
        for action in (first, second):
            if action.function != plan.function:
                raise QualificationProductionError(
                    "bar app receipts must name the qualified function"
                )
        if first.forward_payload_sha256 == second.forward_payload_sha256:
            raise QualificationProductionError("bar app receipt payloads must differ")
        if plan.owned_restore:
            restore = record.actions[-1]
            if restore.function != _OWNED_RESTORE_FUNCTION or restore.action_id != (
                _OWNED_RESTORE_ACTION
            ):
                raise QualificationProductionError(
                    "bar app restore must stay a separate owned-control action"
                )
            written = json.loads(self._journal.load_blob(restore.expected_readback_sha256))
            if tuple(sorted(written)) != tuple(intent.owned_fields):
                raise QualificationProductionError(
                    "bar app restore did not cover the fields the operation wrote"
                )
            if any(decoded[name] != value for name, value in written.items()):
                raise QualificationProductionError(
                    "bar app restore did not return the exact initial values"
                )
        self._require_rebuilt_bytes(intent, record, initial_status, decoded)

    def _require_rebuilt_bytes(
        self,
        intent: BarAppControlQualificationIntent,
        record: LocalControlJournalRecord,
        initial_status: bytes,
        decoded: dict[str, Any],
    ) -> None:
        """Rebuild every frame from the initial raw and re-check what the device reported.

        Nothing here trusts a stored digest on its own. Each frame's forward bytes, rollback
        bytes and both readback expectations are recomputed from the operation's own initial
        snapshot, and each action's preserved raw readback is decoded again and compared with
        the fields that frame claimed to write.
        """

        schema = get_product_schema(intent.target.product_key)
        # The recorded feeding value is this operation's own binding, not a live admission
        # value, so it has to come back out of the initial raw rather than be trusted.
        if decoded["FeedSwitch"] is not intent.baseline_feed_switch:
            raise QualificationProductionError(
                "bar app qualification feeding baseline does not match the initial raw"
            )
        frames = _frame_values(intent.function, initial_feed_switch=intent.baseline_feed_switch)
        planned = [self._rebuilt_fields(function, value, decoded) for function, value in frames]
        if self._plan.owned_restore:
            planned.append({name: decoded[name] for name in intent.owned_fields})
        for action, fields in zip(record.actions, planned, strict=True):
            rollback = {name: decoded[name] for name in fields}
            if (
                self._journal.load_blob(action.baseline_status_sha256) != initial_status
                or _digest(build_control_payload(schema, fields)) != action.forward_payload_sha256
                or _digest(build_control_payload(schema, rollback))
                != action.rollback_payload_sha256
                or _digest(_canonical_values(fields)) != action.expected_readback_sha256
                or _digest(_canonical_values(rollback)) != action.baseline_readback_sha256
            ):
                raise QualificationProductionError(
                    "bar app qualification bytes do not rebuild from the initial raw"
                )
            observed = self._journal.load_blob(action.diagnostic.removeprefix(_OBSERVED))
            reported = schema.decode_status(observed)
            if any(reported[name] != value for name, value in fields.items()):
                raise QualificationProductionError(
                    "preserved bar app readback differs from the planned fields"
                )
        # No separate "ended on the initial state" check follows. The last frame of every
        # sequence writes exactly the owned union, so the comparison above already proves it,
        # and a guard that can never fire would imply protection it does not add.

    @staticmethod
    def _rebuilt_fields(function: str, value: Any, decoded: dict[str, Any]) -> dict[str, Any]:
        """The exact fields one app frame writes, rebuilt from the initial values alone."""

        if function == "feed_switch":
            return {"FeedSwitch": value}
        return dict(prepare_bar_control_command(function, value, current=decoded).fields)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _app_phase_intent(
    intent: BarAppControlQualificationIntent,
    position: int,
) -> LocalControlIntent:
    if position == 0:
        request_id, value = intent.first_request_id, intent.first_value
    elif position == 1:
        request_id, value = intent.second_request_id, intent.second_value
    else:
        raise IndexError("bar app receipt position is invalid")
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=intent.function,
        value=value,
    )


def _app_receipt(
    intent: BarAppControlQualificationIntent,
    action: JournalAction,
    phase_intent: LocalControlIntent,
    completed_at: datetime,
) -> LocalControlQualificationReceipt:
    if action.acknowledgement_sha256 is None:
        raise QualificationProductionError("bar app qualification action has no acknowledgement")
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
    "BarAppControlQualificationIntent",
    "BarAppControlQualificationProducer",
    "bar_app_qualification_intent_bytes",
]
