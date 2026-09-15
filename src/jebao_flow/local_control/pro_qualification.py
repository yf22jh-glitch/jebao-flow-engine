"""Explicit Pro first-write qualification with existing native-owned recovery.

This does not authorize the ordinary attach API, mutate a catalog, or turn compound writes
into scalar receipts. Each scalar receipt requires its own sent payload, ACK and captured
readback; no receipt is emitted until the separately journaled own-original restore completes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, verify_prepared_readback
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import ActionPhase, TransactionPhase, native_detach_request_id
from jebao_flow.local_control.models import LocalControlIntent, QualificationBinding
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    qualification_receipt_bytes,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationEpoch,
    QualificationProductionError,
    _current_firmware,
    _require_operation_id,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan, WavemakerSchedule
from jebao_flow.local_control.wavemaker_lifecycle import (
    NativeContext,
    NativeOriginal,
    NativeWavemakerController,
)
from jebao_flow.local_control.wavemaker_restore import (
    WavemakerRestoreSnapshot,
    compare_restore_readback,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range

_FUNCTIONS = (
    "schedule",
    "schedule_enable",
    "manual_mode",
    "manual_frequency",
    "feeding_duration",
    "custom_wave_frequency",
    "manual_flow",
    "power",
    "linkage_role",
)


class ProQualificationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["pro_scalar_qualification_v1"] = "pro_scalar_qualification_v1"
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    epochs: tuple[QualificationEpoch, ...] = Field(min_length=2, max_length=2)
    firmware: tuple[str, ...] = Field(min_length=2, max_length=2)
    plan: NativeWavemakerPlan


def _schedule(flow):
    return WavemakerSchedule.model_validate(
        {
            "family": "pro",
            "slots": [
                {
                    "slot_index": 0,
                    "start": "00:00",
                    "end": "23:59",
                    "mode": "constant",
                    "flow": flow,
                }
            ],
        }
    )


def _app_trial_values(product_key, function, baseline):
    """Two distinct app-declared values; avoid a first-step NO_CHANGE.

    The 2026-09-15 custom-frequency run sent 1 below the declared minimum 5.
    This chooses from the existing app contract, not an inferred firmware clamp.
    """
    minimum, maximum = declared_app_integer_range(product_key, function)
    if minimum + 1 > maximum:
        raise QualificationProductionError("app range has no two distinct trial values")
    return (minimum + 1, minimum) if baseline == minimum else (minimum, minimum + 1)


def _plan(targets):
    return NativeWavemakerPlan.model_validate(
        {
            "mode": "sync",
            "master": targets[0].logical_member,
            "schedule": _schedule(39).model_dump(),
            "slaves": [
                {
                    "logical_member": targets[1].logical_member,
                    "flows": [{"slot_index": 0, "flow": 39}],
                }
            ],
            "preparation_flow": 40,
            "preparation_frequency": 20,
        }
    )


class _EpochPermit:
    def __init__(self, permit, check):
        self.permit, self.check = permit, check

    def require_permitted(self):
        self.check()
        self.permit.require_permitted()

    def require_pre_send_permitted(self):
        self.check()
        self.permit.require_pre_send_permitted()


class _QualificationWriter(ConnectedDeviceWriter):
    """Scalar first-write evidence uses the ordinary writer's reply-only contract."""

    async def connect_and_capture(self, *, accept_reports):
        return await super().connect_and_capture(accept_reports=False)

    async def capture_readback(self, lease, *, accept_reports):
        return await super().capture_readback(lease, accept_reports=False)


class ProQualificationProducer(NativeWavemakerController):
    """A fixed 2-Pro qualification sequence, not a general unqualified command API."""

    def __init__(self, *, epoch_providers, artifact_factory, **kwargs):
        if "writer_factory" not in kwargs:
            session = kwargs.pop("session_factory", None)
            kwargs["writer_factory"] = (
                _QualificationWriter
                if session is None
                else lambda endpoint: _QualificationWriter(endpoint, session_factory=session)
            )
        super().__init__(**kwargs)
        self._epoch_providers = tuple(epoch_providers)
        self._artifact_factory = artifact_factory

    def _epochs(self):
        epochs = tuple(provider() for provider in self._epoch_providers)
        if len(epochs) != 2:
            raise QualificationProductionError("Pro qualification requires exactly two targets")
        targets = tuple(epoch.target for epoch in epochs)
        self._validate_targets(_plan(targets), targets)
        for epoch in epochs:
            if epoch.schema_sha256 != current_protocol_schema_sha256():
                raise QualificationProductionError("qualification schema is not current")
            if not epoch.min_power <= 39 < 40 <= epoch.max_power:
                raise QualificationProductionError("qualification 39/40 exceeds configured limits")
        return epochs

    def _check_epochs(self, epochs):
        if self._epochs() != epochs:
            raise QualificationProductionError("qualification content epoch changed")

    async def execute(self, operation_id):
        _require_operation_id(operation_id)
        epochs = self._epochs()
        targets = tuple(epoch.target for epoch in epochs)
        # Check durable output ownership before touching hardware, as the existing producers do.
        for target in targets:
            for function in _FUNCTIONS:
                self._artifact_factory(target, function)
        with self._lease.acquire(tuple(t.physical_identity_key for t in targets)) as permit:
            existing = self._journal.load(operation_id)
            if existing is not None:
                return self._finalize(existing, epochs, current=False)
            self._require_available(targets, allowed=())
            endpoints = await self._resolver.resolve_many(targets)
            self._validate_endpoints(targets, endpoints)
            intent = ProQualificationIntent(
                operation_id=operation_id,
                epochs=epochs,
                firmware=tuple(_current_firmware(e) for e in endpoints),
                plan=_plan(targets),
            )
            originals = []
            for endpoint in endpoints:
                capture, status = await self._read(endpoint, permit, reports=False)
                WavemakerRestoreSnapshot(operation_id, endpoint.target, status)
                originals.append(
                    NativeOriginal(
                        target=endpoint.target,
                        status_sha256=self._journal.install_blob(status),
                        capture_sha256=capture,
                    )
                )
            context = NativeContext(
                kind="attach",
                attach_request_id=operation_id,
                schema_sha256=current_protocol_schema_sha256(),
                plan=intent.plan,
                originals=tuple(originals),
            )
            prepared = self._qualification_writes(context, endpoints)
            self._check_epochs(epochs)
            record = self._create_record(
                request_id=operation_id,
                intent_sha256=self._journal.install_blob(intent.model_dump_json().encode()),
                prepared=prepared,
                native_context_sha256=self._journal.install_blob(
                    context.model_dump_json().encode()
                ),
            )
            try:
                await self._advance(
                    record,
                    context,
                    endpoints,
                    _EpochPermit(permit, lambda: self._check_epochs(epochs)),
                )
            finally:
                # Same existing own-original recovery even after cancellation/forward failure.
                # Its authority is the owned context, not a possibly changed forward epoch.
                task = asyncio.create_task(self._detach_locked(record, context, endpoints, permit))
                cancelled = False
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:
                        break
                task.result()
                if cancelled:
                    raise asyncio.CancelledError()
            return self._finalize(self._required(operation_id), epochs, current=True)

    def _qualification_writes(self, context, endpoints):
        snapshots = self._snapshots(context)
        prepared = []
        # All devices enter the known independent/TimerOFF preparation before any test image.
        for endpoint in reversed(endpoints):
            original = snapshots[endpoint.target.logical_member].baseline_status
            prepared.append(
                self._writer_factory(endpoint).prepare_native_controls(
                    self._safe(context.plan), original
                )
            )
        for endpoint in endpoints:
            writer = self._writer_factory(endpoint)
            snapshot = snapshots[endpoint.target.logical_member]
            original, controls = snapshot.baseline_status, snapshot.controls
            for flow in (40, 39):
                # Identical exact-image builder and full-image verifier as scalar schedule.
                # Its TimerOFF admission is enforced on the actual before capture by _advance.
                prepared.append(
                    replace(
                        writer.prepare_native_image(_schedule(flow).compile_image(), original),
                        function="schedule",
                    )
                )
            choices = (
                ("schedule_enable", (True, False)),
                ("manual_mode", ("sine", "constant")),
                ("manual_frequency", (21, 20)),
                (
                    "feeding_duration",
                    _app_trial_values(
                        endpoint.target.product_key, "feeding_duration", controls["FeedTime"]
                    ),
                ),
                (
                    "custom_wave_frequency",
                    _app_trial_values(
                        endpoint.target.product_key,
                        "custom_wave_frequency",
                        controls["Cust_Wav_Freq"],
                    ),
                ),
                ("manual_flow", (39, 40)),
                ("power", (False, True)),
                ("linkage_role", ("master", "independent")),
            )
            for function, values in choices:
                for ordinal, value in enumerate(values):
                    prepared.append(
                        writer.prepare_scalar(
                            LocalControlIntent(
                                request_id=self._receipt_id(
                                    context.attach_request_id, endpoint.target, function, ordinal
                                ),
                                target=endpoint.target,
                                function=function,
                                value=value,
                            ),
                            original,
                        )
                    )
        return tuple(prepared)

    async def _finish(self, record, context, endpoints, permit):
        if context.kind == "detach":
            return await super()._finish(record, context, endpoints, permit)
        # A completed qualification is not a successful native attach. The next operation is
        # always own-original detach, and candidates are not emitted here.
        # _epochs, intent lengths and endpoint equality fix exactly two preparation actions.
        if any(a.phase is not ActionPhase.VERIFIED for a in record.actions[2:]):
            raise QualificationProductionError(
                "two actually sent requests are required per function"
            )
        return self._save_transaction_phase(record, TransactionPhase.VERIFIED)

    @staticmethod
    def _receipt_id(operation, target, function, ordinal):
        raw = f"{operation}:{target.physical_identity_key}:{function}:{ordinal}"
        return "proq_" + hashlib.sha256(raw.encode()).hexdigest()

    def _capture_status(self, digest, *, reply_only=False):
        capture = json.loads(self._journal.load_blob(digest))
        if capture["action"] not in ((3,) if reply_only else (3, 4)):
            raise QualificationProductionError("capture action does not satisfy evidence policy")
        self._journal.load_blob(capture["wire_sha256"])
        return capture, self._journal.load_blob(capture["status_sha256"])

    def _finalize(self, record, epochs, *, current):
        self._check_epochs(epochs)
        intent = ProQualificationIntent.model_validate_json(
            self._journal.load_blob(record.intent_sha256)
        )
        context = self._context(record)
        if (
            intent.operation_id != record.request_id
            or intent.epochs != epochs
            or intent.plan != _plan(tuple(e.target for e in epochs))
            or intent.plan != context.plan
            or context.schema_sha256 != epochs[0].schema_sha256
            or context.kind != "attach"
            or record.phase is not TransactionPhase.VERIFIED
        ):
            raise QualificationProductionError(
                "qualification is not complete in this current epoch"
            )
        restored = self._journal.load(native_detach_request_id(record.request_id))
        if restored is None or restored.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError(
                "own-original restore required; use recover-wavemaker"
            )
        self._context(restored)
        final = json.loads(self._journal.load_blob(restored.native_final_sha256))
        rows = final["devices"]
        if tuple(r["logical_member"] for r in rows) != tuple(
            e.target.logical_member for e in epochs
        ):
            raise QualificationProductionError("final restoration target set differs")
        snapshots = self._snapshots(context)
        for row, epoch in zip(rows, epochs, strict=True):
            _, raw = self._capture_status(row["capture_sha256"], reply_only=True)
            if not compare_restore_readback(
                snapshots[epoch.target.logical_member], target=epoch.target, status=raw
            )["complete"]:
                raise QualificationProductionError("preserved final restoration differs")
        endpoints = tuple(
            ResolvedEndpoint(e.target, "offline", wifi_firmware_version=fw)
            for e, fw in zip(epochs, intent.firmware, strict=True)
        )
        planned = self._qualification_writes(context, endpoints)
        if len(planned) != len(record.actions):
            raise QualificationProductionError("qualification action count differs")
        evidence = {}
        for index, (prepared, action) in enumerate(zip(planned, record.actions, strict=True)):
            if (
                action.target != prepared.target.target
                or action.action_id != f"action_{index:02d}"
                or action.function != prepared.function
                or action.forward_payload_sha256 != prepared.forward_payload_sha256
                or action.rollback_payload_sha256
                != hashlib.sha256(prepared.rollback_payload).hexdigest()
                or action.expected_readback_sha256
                != hashlib.sha256(prepared.expected_readback).hexdigest()
                or action.baseline_readback_sha256
                != hashlib.sha256(prepared.baseline_readback).hexdigest()
                or action.baseline_status_sha256
                != hashlib.sha256(prepared.baseline_status).hexdigest()
                or action.verification_kind != prepared.verification_kind
                or action.restore_on_recovery is not prepared.restore_on_recovery
            ):
                raise QualificationProductionError("qualification declared action differs")
            if index < 2:
                continue  # atomic preparations never become scalar receipts
            if action.phase is not ActionPhase.VERIFIED or action.acknowledgement_sha256 is None:
                raise QualificationProductionError(
                    "qualification has no actual ACK/verified action"
                )
            # send_raw_control validates command/sequence before returning the body.
            # Empty is valid (2026-09-11 and 2026-09-15 runs); missing/corrupt is not.
            self._journal.load_blob(action.acknowledgement_sha256)
            entry = json.loads(self._journal.load_blob(action.native_evidence_sha256))
            if entry["stage"] != "after" or entry["match"] is not True:
                raise QualificationProductionError("qualification lacks post-send raw evidence")
            capture, raw = self._capture_status(entry["capture_sha256"], reply_only=True)
            if not verify_prepared_readback(prepared, raw):
                raise QualificationProductionError("actual qualification readback differs")
            key = (action.target.physical_identity_key, action.function)
            evidence.setdefault(key, []).append((action, capture["status_sha256"]))
        if len(evidence) != 18 or any(len(items) != 2 for items in evidence.values()):
            raise QualificationProductionError(
                "qualification does not cover nine functions twice per Pro"
            )
        # Validate ALL evidence before installing even the first candidate.
        result = []
        for epoch, firmware in zip(epochs, intent.firmware, strict=True):
            for (physical_key, function), items in evidence.items():
                if physical_key != epoch.target.physical_identity_key:
                    continue
                common = dict(
                    product_key=epoch.target.product_key,
                    function=function,
                    app_version=epoch.app_version,
                    app_catalog_sha256=epoch.app_catalog_sha256,
                    template_sha256=epoch.template_sha256,
                    schema_sha256=epoch.schema_sha256,
                    firmware_version=firmware,
                )
                receipts = tuple(
                    LocalControlQualificationReceipt(
                        **common,
                        physical_identity_key=physical_key,
                        request_id=self._receipt_id(
                            record.request_id, epoch.target, function, ordinal
                        ),
                        intent_sha256=record.intent_sha256,
                        control_payload_sha256=a.forward_payload_sha256,
                        acknowledgement_sha256=a.acknowledgement_sha256,
                        readback_sha256=readback,
                        completed_at=record.updated_at,
                    )
                    for ordinal, (a, readback) in enumerate(items)
                )
                binding = QualificationBinding(
                    **common,
                    receipt_sha256=tuple(
                        hashlib.sha256(qualification_receipt_bytes(r)).hexdigest() for r in receipts
                    ),
                )
                digest = self._artifact_factory(epoch.target, function).finalize(receipts, binding)
                result.append(
                    {
                        "target_sha256": physical_key,
                        "function": function,
                        "candidate_sha256": digest,
                    }
                )
        return {
            "operation_id": record.request_id,
            "qualification_pairs": len(result),
            "receipt_count": 36,
            "candidates": result,
            "restored": True,
            "current_state_verified": current,
            "new_device_commands_on_replay": 0,
        }
