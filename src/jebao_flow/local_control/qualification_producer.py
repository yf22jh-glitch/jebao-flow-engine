"""Crash-safe production of local-control manual-flow qualification receipts.

This module owns no alternate write primitive.  It composes the existing strict endpoint
resolver, exclusive lease, connected writer and durable local-control journal into the audited
three-frame first-write sequence.  Receipt artifacts are created only after that journal is
durably terminal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.config import AppConfig, RuntimeMode
from jebao_flow.hardware_safety import qualification_directory, validate_hardware_safety_root
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    PreparedDeviceWrite,
    get_product_schema,
    verify_prepared_readback,
)
from jebao_flow.local_control.failure_diagnostics import failure_clock_ns, report_action_failure
from jebao_flow.local_control.install import (
    build_fresh_install_manifest,
    current_protocol_schema_sha256,
    hash_current_template_contents,
    load_current_private_config,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.lease import LocalControlSendPermit
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    qualification_receipt_bytes,
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.operator_capture.app_catalog import (
    declared_app_integer_range,
    public_app_catalog,
)
from jebao_flow.operator_capture.paths import durable_private_path

_MAX_PRIVATE_INPUT_BYTES = 1024 * 1024
_MAX_ARTIFACT_BYTES = 1024 * 1024
_SAFE_FIRST_WRITE_MAXIMUM = 45
_FUNCTION = "manual_flow"
_ACTION_IDS = ("same_value", "lower_power", "planned_restore")
_LOGGER = logging.getLogger(__name__)


class QualificationProductionError(RuntimeError):
    """The qualification operation stopped without granting write authority."""


class QualificationEpoch(BaseModel):
    """Current private/config/content facts that must remain fixed for all three phases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: LocalDeviceBinding = Field(repr=False)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    min_power: int
    max_power: int
    app_version: str = Field(min_length=1, max_length=128)
    app_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.min_power > self.max_power:
            raise ValueError("qualification epoch has an invalid power envelope")
        return self


class ManualFlowQualificationIntent(BaseModel):
    """Private durable operation intent used to reconstruct terminal receipt bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["manual_flow"] = _FUNCTION
    baseline_flow: int
    downward_step: int
    lower_flow: int
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
            raise ValueError("qualification intent timestamp must be timezone-aware")
        if not 1 <= self.downward_step <= 5:
            raise ValueError("qualification step must be between one and five")
        if self.lower_flow != self.baseline_flow - self.downward_step:
            raise ValueError("qualification lower flow must be an exact downward step")
        if self.step_request_id == self.restore_request_id:
            raise ValueError("qualification receipt requests must be distinct")
        if self.min_power > self.max_power:
            raise ValueError("qualification intent has an invalid power envelope")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("qualification versions must use printable ASCII")
        return self


@dataclass(frozen=True, slots=True)
class ManualFlowState:
    enabled: bool
    timer_enabled: bool
    mode: str
    linkage: str | None
    flow: int


@dataclass(frozen=True, slots=True)
class _ManualFlowFamilyProfile:
    family: str
    state_probes: tuple[tuple[str, object, tuple[str, ...]], ...]
    decoded_status_fields: tuple[str, ...]
    required_mode: str
    required_linkage: str | None


_MANUAL_FLOW_FAMILY_PROFILES = {
    "local_wavemaker_pro": _ManualFlowFamilyProfile(
        family="local_wavemaker_pro",
        state_probes=(
            ("schedule_enable", False, ("SwitchON", "TimerON")),
            ("linkage_role", "independent", ("Linkage",)),
            ("manual_mode", "constant", ("Mode",)),
        ),
        decoded_status_fields=(),
        required_mode="constant",
        required_linkage="independent",
    ),
    "local_wavemaker": _ManualFlowFamilyProfile(
        family="local_wavemaker",
        # Read state directly; compound app commands are not read probes.
        state_probes=(),
        decoded_status_fields=("SwitchON", "TimerON", "Linkage", "Mode"),
        required_mode="constant",
        required_linkage="independent",
    ),
    "dc_pump_pro": _ManualFlowFamilyProfile(
        family="dc_pump_pro",
        state_probes=(
            ("power", True, ("SwitchON",)),
            ("manual_mode", "constant", ("Mode",)),
        ),
        decoded_status_fields=("TimerON",),
        required_mode="constant",
        # DC has no Linkage datapoint. None is absence, not an observed independent role.
        required_linkage=None,
    ),
}


@dataclass(frozen=True, slots=True)
class QualificationProductionResult:
    operation_id: str
    phase: TransactionPhase
    journal_sha256: str
    target_id: str
    receipt_count: int
    physical_write_count: int
    qualification_sha256: str | None
    completed_at: datetime

    def public_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "phase": self.phase.value,
            "journal_sha256": self.journal_sha256,
            "target_id": self.target_id,
            "receipt_count": self.receipt_count,
            "physical_write_count": self.physical_write_count,
            "qualification_sha256": self.qualification_sha256,
            "completed_at": self.completed_at.isoformat(),
            "private_values_exposed": False,
        }


class EndpointResolver(Protocol):
    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint: ...


class WriteLease(Protocol):
    def acquire(self, physical_identity_keys: tuple[str, ...]): ...


WriterFactory = Callable[[ResolvedEndpoint], ConnectedDeviceWriter]
ResolverFactory = Callable[[], EndpointResolver]
EpochProvider = Callable[[], QualificationEpoch]
Clock = Callable[[], datetime]
ReadbackValidator = Callable[[bytes], None]


@dataclass(frozen=True, slots=True)
class CurrentQualificationEpochSource:
    """Re-read and bind every current private input without retaining its raw values."""

    config_path: Path = field(repr=False)
    manifest_path: Path = field(repr=False)
    template_roots: tuple[Path, ...] = field(repr=False)
    logical_member: str = field(repr=False)
    active_template_sha256: str
    config_loader: Callable[[Path], AppConfig] = field(
        default=load_current_private_config,
        repr=False,
    )
    manifest_loader: Callable[[Path], FreshInstallManifest] = field(
        default=lambda path: load_current_install_manifest(path),
        repr=False,
    )
    template_hasher: Callable[[tuple[Path, ...]], tuple[str, ...]] = field(
        default=hash_current_template_contents,
        repr=False,
    )
    target_validator: Callable[[str], object] | None = field(default=None, repr=False)

    def __call__(self) -> QualificationEpoch:
        config = self.config_loader(self.config_path)
        manifest = self.manifest_loader(self.manifest_path)
        template_digests = self.template_hasher(self.template_roots)
        if template_digests != manifest.content.template_sha256:
            raise QualificationProductionError("current template content differs from the manifest")
        if self.active_template_sha256 not in template_digests:
            raise QualificationProductionError("active template digest is not current")

        catalog_payload = public_app_catalog()
        source_app = catalog_payload.get("source_app")
        if not isinstance(source_app, Mapping):
            raise QualificationProductionError("current app catalog has no source version")
        app_version = source_app.get("version")
        if not isinstance(app_version, str):
            raise QualificationProductionError("current app catalog version is invalid")
        app_catalog_sha256 = hashlib.sha256(_canonical_json(catalog_payload)).hexdigest()
        schema_sha256 = current_protocol_schema_sha256()
        if manifest.content.app_catalog_sha256 != app_catalog_sha256:
            raise QualificationProductionError("current app catalog differs from the manifest")
        if manifest.content.protocol_schema_sha256 != schema_sha256:
            raise QualificationProductionError("current protocol sources differ from the manifest")
        if config.runtime.mode is not RuntimeMode.OBSERVER:
            raise QualificationProductionError("qualification requires the observer runtime mode")

        try:
            attestor = manifest.devices[0]
        except IndexError as error:
            raise QualificationProductionError("current manifest contains no target") from error
        rederived = build_fresh_install_manifest(
            config,
            role_attested_by=attestor.role_attested_by,
            role_attested_at=attestor.role_attested_at,
            template_sha256=template_digests,
            now=manifest.rederived_at,
        )
        if rederived.devices != manifest.devices or rederived.content != manifest.content:
            raise QualificationProductionError("current config does not reproduce the manifest")

        matches = tuple(
            target for target in manifest.devices if target.logical_member == self.logical_member
        )
        configured = tuple(device for device in config.devices if device.id == self.logical_member)
        if len(matches) != 1 or len(configured) != 1:
            raise QualificationProductionError("qualification target is not uniquely current")
        target = matches[0]
        device = configured[0]
        if not device.enabled or device.product_key != target.product_key:
            raise QualificationProductionError("qualification target is not enabled and current")
        validator = self.target_validator or _manual_flow_family_profile
        validator(target.product_key)

        return QualificationEpoch(
            target=target,
            config_sha256=_canonical_sha256(config.model_dump(mode="json")),
            manifest_sha256=_canonical_sha256(manifest.model_dump(mode="json")),
            manifest_generation=manifest.generation,
            min_power=device.limits.min_power,
            max_power=device.limits.max_power,
            app_version=app_version,
            app_catalog_sha256=app_catalog_sha256,
            template_sha256=self.active_template_sha256,
            schema_sha256=schema_sha256,
        )


class QualificationArtifactStore:
    """Install exact receipt and binding bytes without replacing an existing artifact."""

    def __init__(self, qualification_path: Path | str) -> None:
        validate_hardware_safety_root()
        destination = durable_private_path(
            qualification_path,
            description="local-control qualification output",
        )
        self._initialize(qualification_directory(), destination)

    def _initialize(self, receipt_root: Path, qualification_path: Path) -> None:
        self.receipt_root = Path(receipt_root).absolute()
        self.qualification_path = Path(qualification_path).absolute()
        self.receipt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.qualification_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require_private_directory(self.receipt_root)
        _require_private_directory(self.qualification_path.parent)

    @classmethod
    def for_test(
        cls,
        receipt_root: Path,
        qualification_path: Path,
    ) -> QualificationArtifactStore:
        store = cls.__new__(cls)
        store._initialize(Path(receipt_root).resolve(), Path(qualification_path).resolve())
        return store

    def finalize(
        self,
        receipts: tuple[LocalControlQualificationReceipt, ...],
        binding: QualificationBinding,
    ) -> str:
        if len(receipts) != 2:
            raise QualificationProductionError("qualification requires exactly two receipts")
        payloads = tuple(qualification_receipt_bytes(receipt) for receipt in receipts)
        digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
        if binding.receipt_sha256 != digests:
            raise QualificationProductionError("qualification binding does not name exact receipts")
        for digest, payload in zip(digests, payloads, strict=True):
            _install_or_verify_private_file(
                self.receipt_root,
                f"local-control-receipt-{digest}.json",
                payload,
            )
        qualification_payload = _canonical_json(
            {"qualifications": [binding.model_dump(mode="json")]},
            newline=True,
        )
        _install_or_verify_private_file(
            self.qualification_path.parent,
            self.qualification_path.name,
            qualification_payload,
        )
        verified = verify_qualification_receipts(
            binding,
            _receipt_root_for_test=self.receipt_root,
        )
        if verified != receipts:
            raise QualificationProductionError(
                "qualification receipt verification changed evidence"
            )
        if qualified_physical_identity_keys(
            binding,
            _receipt_root_for_test=self.receipt_root,
        ) != frozenset({receipts[0].physical_identity_key}):
            raise QualificationProductionError("qualification receipts do not bind one target")
        if _read_private_path(self.qualification_path) != qualification_payload:
            raise QualificationProductionError("qualification output did not reload exactly")
        return hashlib.sha256(qualification_payload).hexdigest()


class ManualFlowQualificationProducer:
    """Execute or offline-finalize one three-frame manual-flow qualification operation."""

    def __init__(
        self,
        *,
        epoch_provider: EpochProvider,
        resolver_factory: ResolverFactory,
        journal: LocalControlJournalStore,
        lease: WriteLease,
        artifacts: QualificationArtifactStore,
        writer_factory: WriterFactory = ConnectedDeviceWriter,
        clock: Clock | None = None,
    ) -> None:
        self._epoch_provider = epoch_provider
        self._resolver_factory = resolver_factory
        self._journal = journal
        self._lease = lease
        self._artifacts = artifacts
        self._writer_factory = writer_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        operation_id: str,
        *,
        downward_step: int,
    ) -> QualificationProductionResult:
        _require_operation_id(operation_id)
        if isinstance(downward_step, bool) or not isinstance(downward_step, int):
            raise TypeError("qualification step must be an integer")
        if not 1 <= downward_step <= 5:
            raise ValueError("qualification step must be between one and five")

        existing = self._journal.load(operation_id)
        if existing is not None:
            if existing.phase is TransactionPhase.VERIFIED:
                return self._finalize(existing, physical_write_count=0)
            raise QualificationProductionError(
                "qualification operation has unfinished or recovered durable state"
            )

        epoch = self._epoch_provider()
        record: LocalControlJournalRecord | None = None
        writes = 0
        with self._lease.acquire((epoch.target.physical_identity_key,)) as permit:
            self._require_no_other_unfinished(operation_id, target=epoch.target)
            if self._journal.load(operation_id) is not None:
                raise QualificationProductionError("qualification operation appeared concurrently")
            self._require_epoch(epoch)
            writer: ConnectedDeviceWriter | None = None
            try:
                endpoint, firmware_version = await self._resolve(epoch, None)
                writer = self._writer_factory(endpoint)
                baseline_status = await writer.connect_and_read()
                baseline_state = _manual_flow_state(writer, epoch.target, baseline_status)
                self._require_envelope(epoch, baseline_state, downward_step)
                intent, prepared = self._prepare_operation(
                    operation_id,
                    epoch,
                    endpoint,
                    writer,
                    baseline_status,
                    baseline_state.flow,
                    downward_step,
                    firmware_version,
                )
                record = self._create_record(intent, prepared)
                self._require_epoch(epoch)
                record = await self._execute_action(
                    record,
                    0,
                    writer,
                    prepared[0],
                    permit,
                    readback_validator=lambda status: self._require_phase_readback(
                        epoch,
                        writer,
                        status,
                        baseline_state.flow,
                    ),
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
                record = await self._execute_fresh_phase(
                    record,
                    intent,
                    epoch,
                    firmware_version,
                    action_index=1,
                    expected_flow=intent.baseline_flow,
                    permit=permit,
                )
                writes += 1
                self._require_epoch(epoch)
                record = await self._execute_fresh_phase(
                    record,
                    intent,
                    epoch,
                    firmware_version,
                    action_index=2,
                    expected_flow=intent.lower_flow,
                    permit=permit,
                )
                writes += 1
                self._require_epoch(epoch)
                record = self._save_transaction(record, TransactionPhase.VERIFIED)
            except BaseException:
                self._require_recovery(operation_id)
                raise

        return self._finalize(record, physical_write_count=writes)

    async def _execute_fresh_phase(
        self,
        record: LocalControlJournalRecord,
        intent: ManualFlowQualificationIntent,
        epoch: QualificationEpoch,
        firmware_version: str,
        *,
        action_index: int,
        expected_flow: int,
        permit: LocalControlSendPermit,
    ) -> LocalControlJournalRecord:
        endpoint, _firmware = await self._resolve(epoch, firmware_version)
        writer = self._writer_factory(endpoint)
        try:
            status = await writer.connect_and_read()
            state = _manual_flow_state(writer, epoch.target, status)
            self._require_state_envelope(epoch, state)
            if state.flow != expected_flow:
                raise QualificationProductionError("qualification phase baseline changed")
            action = record.actions[action_index]
            prepared = self._prepared_from_action(action, endpoint)
            phase_intent = _phase_intent(intent, action_index)
            fresh = writer.prepare_scalar(phase_intent, status)
            if (
                fresh.forward_payload_sha256 != action.forward_payload_sha256
                or hashlib.sha256(fresh.expected_readback).hexdigest()
                != action.expected_readback_sha256
            ):
                raise QualificationProductionError("qualification phase payload changed")
            self._require_epoch(epoch)
            return await self._execute_action(
                record,
                action_index,
                writer,
                prepared,
                permit,
                readback_validator=lambda readback: self._require_phase_readback(
                    epoch,
                    writer,
                    readback,
                    intent.lower_flow if action_index == 1 else intent.baseline_flow,
                ),
            )
        finally:
            await writer.close()

    def _prepare_operation(
        self,
        operation_id: str,
        epoch: QualificationEpoch,
        endpoint: ResolvedEndpoint,
        writer: ConnectedDeviceWriter,
        baseline_status: bytes,
        baseline_flow: int,
        downward_step: int,
        firmware_version: str,
    ) -> tuple[
        ManualFlowQualificationIntent,
        tuple[PreparedDeviceWrite, PreparedDeviceWrite, PreparedDeviceWrite],
    ]:
        lower_flow = baseline_flow - downward_step
        step_request_id = _phase_request_id(operation_id, "step")
        restore_request_id = _phase_request_id(operation_id, "restore")
        same = writer.prepare_scalar(
            LocalControlIntent(
                request_id=_phase_request_id(operation_id, "same"),
                target=epoch.target,
                function=_FUNCTION,
                value=baseline_flow,
            ),
            baseline_status,
        )
        step = writer.prepare_scalar(
            LocalControlIntent(
                request_id=step_request_id,
                target=epoch.target,
                function=_FUNCTION,
                value=lower_flow,
            ),
            baseline_status,
        )
        if not same.no_change or step.no_change:
            raise QualificationProductionError("qualification baseline did not form a real step")
        restore = PreparedDeviceWrite(
            target=endpoint,
            function=_FUNCTION,
            forward_payload=same.forward_payload,
            rollback_payload=same.rollback_payload,
            baseline_status=same.baseline_status,
            expected_readback=same.expected_readback,
            baseline_readback=same.baseline_readback,
            verification_kind=same.verification_kind,
            no_change=False,
            restore_on_recovery=True,
        )
        intent = ManualFlowQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            baseline_flow=baseline_flow,
            downward_step=downward_step,
            lower_flow=lower_flow,
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

    def _create_record(
        self,
        intent: ManualFlowQualificationIntent,
        prepared: tuple[PreparedDeviceWrite, PreparedDeviceWrite, PreparedDeviceWrite],
    ) -> LocalControlJournalRecord:
        intent_payload = qualification_intent_bytes(intent)
        intent_sha256 = self._journal.install_blob(intent_payload)
        actions: list[JournalAction] = []
        for action_id, item in zip(_ACTION_IDS, prepared, strict=True):
            actions.append(
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
            )
        now = self._now()
        record = LocalControlJournalRecord(
            request_id=intent.operation_id,
            intent_sha256=intent_sha256,
            created_at=now,
            updated_at=now,
            actions=tuple(actions),
        )
        self._journal.create(record)
        return record

    async def _execute_action(
        self,
        record: LocalControlJournalRecord,
        index: int,
        writer: ConnectedDeviceWriter,
        prepared: PreparedDeviceWrite,
        permit: LocalControlSendPermit,
        *,
        readback_validator: ReadbackValidator,
    ) -> LocalControlJournalRecord:
        action = record.actions[index]
        if action.phase is not ActionPhase.PREPARED:
            raise QualificationProductionError("qualification action is not prepared")
        record = self._save_action(
            record,
            index,
            phase=ActionPhase.FORWARD_ATTEMPTING,
            transaction_phase=TransactionPhase.EXECUTING,
        )
        started_ns = failure_clock_ns()
        readback: bytes | None = None
        try:
            acknowledgement = await writer.send_once(prepared.forward_payload, permit)
            acknowledgement_sha256 = self._journal.install_blob(acknowledgement)
            record = self._save_action(
                record,
                index,
                phase=ActionPhase.FORWARD_ACKNOWLEDGED,
                acknowledgement_sha256=acknowledgement_sha256,
            )
            readback = await writer.readback(permit)
            if not verify_prepared_readback(prepared, readback):
                raise QualificationProductionError("qualification readback did not match")
            readback_validator(readback)
            return self._save_action(
                record,
                index,
                phase=ActionPhase.VERIFIED,
                acknowledgement_sha256=acknowledgement_sha256,
            )
        except BaseException as error:
            failed_ns = failure_clock_ns()
            current = self._journal.load(record.request_id)
            if current is not None and current.phase not in {
                TransactionPhase.VERIFIED,
                TransactionPhase.ROLLED_BACK,
                TransactionPhase.SUPERSEDED,
            }:
                try:
                    current = self._save_action(
                        current,
                        index,
                        phase=ActionPhase.UNCERTAIN,
                        diagnostic="qualification_phase_uncertain",
                        transaction_phase=TransactionPhase.RECOVERY_REQUIRED,
                    )
                except Exception:
                    pass
                report_action_failure(
                    current, index, error, started_ns=started_ns, failed_ns=failed_ns
                )
                if readback is not None:
                    self._report_failed_readback(current, index, prepared, readback)
            raise

    def _report_failed_readback(
        self,
        record: LocalControlJournalRecord,
        index: int,
        prepared: PreparedDeviceWrite,
        status: bytes,
    ) -> None:
        """Preserve actual decision values after uncertainty, without changing its outcome."""
        try:
            values = get_product_schema(prepared.target.target.product_key).decode_status(status)
            fields = (
                "SwitchON",
                "TimerON",
                "Linkage",
                "Mode",
                "Flow",
                "Frequency",
                "FeedTime",
                "PulseTide",
                "FeedSwitch",
                "Motor_Speed",
                "AutoMode",
                "AutoFlow",
                "AutoFreq",
            )
            observed = {name: values[name] for name in fields if name in values}
            evidence = {
                "event": "local_qualification_failed_readback",
                "request_sha256": hashlib.sha256(record.request_id.encode("ascii")).hexdigest(),
                "action_index": index,
                "function": prepared.function,
                "expected": (
                    json.loads(prepared.expected_readback)
                    if prepared.verification_kind == "scalar_fields"
                    else {"schedule_sha256": hashlib.sha256(prepared.expected_readback).hexdigest()}
                ),
                "observed_controls": observed,
                "status_sha256": hashlib.sha256(status).hexdigest(),
                "status_blob_preserved": False,
            }
            # This existing private blob store is content-addressed and fsynced. A failed
            # observer must not suppress the original error or turn uncertainty into PASS.
            try:
                self._journal.install_blob(status)
                evidence["status_blob_preserved"] = True
            except BaseException:
                pass
            _LOGGER.error("%s", json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        except BaseException:
            pass

    def _finalize(
        self,
        record: LocalControlJournalRecord,
        *,
        physical_write_count: int,
    ) -> QualificationProductionResult:
        intent = self._load_intent(record)
        self._validate_terminal_evidence(record, intent)
        step_action, restore_action = record.actions[1:]
        receipts = (
            _receipt(intent, step_action, _phase_intent(intent, 1), record.updated_at),
            _receipt(intent, restore_action, _phase_intent(intent, 2), record.updated_at),
        )
        receipt_payloads = tuple(qualification_receipt_bytes(item) for item in receipts)
        receipt_digests = tuple(hashlib.sha256(payload).hexdigest() for payload in receipt_payloads)
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
            receipt_count=2,
            physical_write_count=physical_write_count,
            qualification_sha256=qualification_sha256,
            completed_at=record.updated_at,
        )

    def _load_intent(self, record: LocalControlJournalRecord) -> ManualFlowQualificationIntent:
        payload = self._journal.load_blob(record.intent_sha256)
        try:
            intent = ManualFlowQualificationIntent.model_validate_json(payload)
        except ValueError as error:
            raise QualificationProductionError("qualification journal intent is invalid") from error
        if (
            qualification_intent_bytes(intent) != payload
            or intent.operation_id != record.request_id
        ):
            raise QualificationProductionError("qualification journal intent is not canonical")
        return intent

    def _validate_terminal_evidence(
        self,
        record: LocalControlJournalRecord,
        intent: ManualFlowQualificationIntent,
    ) -> None:
        if record.phase is not TransactionPhase.VERIFIED:
            raise QualificationProductionError("qualification journal is not verified")
        if tuple(action.action_id for action in record.actions) != _ACTION_IDS:
            raise QualificationProductionError("qualification journal actions changed")
        if any(
            action.phase is not ActionPhase.VERIFIED
            or action.target != intent.target
            or action.function != _FUNCTION
            or action.acknowledgement_sha256 is None
            for action in record.actions
        ):
            raise QualificationProductionError("qualification journal evidence is incomplete")
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
                        "qualification journal evidence is incomplete"
                    )
                self._journal.load_blob(digest)
        baseline = _canonical_json({"Flow": intent.baseline_flow})
        lower = _canonical_json({"Flow": intent.lower_flow})
        expected = (baseline, lower, baseline)
        expected_digests = tuple(hashlib.sha256(value).hexdigest() for value in expected)
        if tuple(action.expected_readback_sha256 for action in record.actions) != expected_digests:
            raise QualificationProductionError("qualification readback evidence changed")
        baseline_digest = hashlib.sha256(baseline).hexdigest()
        if any(action.baseline_readback_sha256 != baseline_digest for action in record.actions):
            raise QualificationProductionError("qualification recovery baseline changed")
        same, lower_action, restore = record.actions
        if (
            same.forward_payload_sha256 != same.rollback_payload_sha256
            or lower_action.rollback_payload_sha256 != same.forward_payload_sha256
            or restore.forward_payload_sha256 != same.forward_payload_sha256
            or restore.rollback_payload_sha256 != same.forward_payload_sha256
            or len({action.baseline_status_sha256 for action in record.actions}) != 1
        ):
            raise QualificationProductionError("qualification payload recovery mapping changed")

    def _prepared_from_action(
        self,
        action: JournalAction,
        endpoint: ResolvedEndpoint,
    ) -> PreparedDeviceWrite:
        return PreparedDeviceWrite(
            target=endpoint,
            function=action.function,
            forward_payload=self._journal.load_blob(action.forward_payload_sha256),
            rollback_payload=self._journal.load_blob(action.rollback_payload_sha256),
            baseline_status=self._journal.load_blob(action.baseline_status_sha256),
            expected_readback=self._journal.load_blob(action.expected_readback_sha256),
            baseline_readback=self._journal.load_blob(action.baseline_readback_sha256),
            verification_kind=action.verification_kind,
            no_change=False,
            restore_on_recovery=action.restore_on_recovery,
        )

    async def _resolve(
        self,
        epoch: QualificationEpoch,
        expected_firmware: str | None,
    ) -> tuple[ResolvedEndpoint, str]:
        endpoint = await self._resolver_factory().resolve(epoch.target)
        if endpoint.target != epoch.target:
            raise QualificationProductionError("current endpoint changed its stable binding")
        firmware = _current_firmware(endpoint)
        if expected_firmware is not None and firmware != expected_firmware:
            raise QualificationProductionError("current firmware changed during qualification")
        self._require_epoch(epoch)
        return endpoint, firmware

    def _require_envelope(
        self,
        epoch: QualificationEpoch,
        state: ManualFlowState,
        downward_step: int,
    ) -> None:
        self._require_state_envelope(epoch, state)
        lower = state.flow - downward_step
        encoder_minimum, encoder_maximum = declared_app_integer_range(
            epoch.target.product_key,
            _FUNCTION,
        )
        if (
            not epoch.min_power <= lower <= epoch.max_power
            or not encoder_minimum <= lower <= encoder_maximum
        ):
            raise QualificationProductionError("current state is outside the first-write envelope")

    def _require_state_envelope(
        self,
        epoch: QualificationEpoch,
        state: ManualFlowState,
    ) -> None:
        profile = _manual_flow_family_profile(epoch.target.product_key)
        encoder_minimum, encoder_maximum = declared_app_integer_range(
            epoch.target.product_key,
            _FUNCTION,
        )
        if (
            state.enabled is not True
            or state.timer_enabled is not False
            or state.mode != profile.required_mode
            or state.linkage != profile.required_linkage
            or state.flow > _SAFE_FIRST_WRITE_MAXIMUM
            or not epoch.min_power <= state.flow <= epoch.max_power
            or not encoder_minimum <= state.flow <= encoder_maximum
        ):
            raise QualificationProductionError("current state is outside the first-write envelope")

    def _require_phase_readback(
        self,
        epoch: QualificationEpoch,
        writer: ConnectedDeviceWriter,
        status: bytes,
        expected_flow: int,
    ) -> None:
        state = _manual_flow_state(writer, epoch.target, status)
        self._require_state_envelope(epoch, state)
        if state.flow != expected_flow:
            raise QualificationProductionError("qualification phase readback changed")

    def _require_epoch(self, expected: QualificationEpoch) -> None:
        if self._epoch_provider() != expected:
            raise QualificationProductionError("qualification content epoch changed")

    def _require_no_other_unfinished(
        self, operation_id: str, *, target: LocalDeviceBinding | None = None
    ) -> None:
        if target is None:
            target = self._epoch_provider().target
        if any(
            item.request_id != operation_id
            for item in self._journal.unfinished_for((target.physical_identity_key,))
        ):
            raise QualificationProductionError(
                "this physical device has an unfinished change that requires recovery"
            )

    def _require_recovery(self, operation_id: str) -> None:
        current = self._journal.load(operation_id)
        if current is None or current.phase in {
            TransactionPhase.VERIFIED,
            TransactionPhase.ROLLED_BACK,
            TransactionPhase.SUPERSEDED,
            TransactionPhase.RECOVERY_REQUIRED,
        }:
            return
        replacement = current.evolve(phase=TransactionPhase.RECOVERY_REQUIRED, now=self._now())
        self._journal.save(current, replacement)

    def _save_action(
        self,
        record: LocalControlJournalRecord,
        index: int,
        *,
        phase: ActionPhase,
        diagnostic: str | None = None,
        acknowledgement_sha256: str | None = None,
        transaction_phase: TransactionPhase | None = None,
    ) -> LocalControlJournalRecord:
        actions = list(record.actions)
        action = actions[index]
        actions[index] = JournalAction.model_validate(
            {
                **action.model_dump(),
                "phase": phase,
                "diagnostic": diagnostic,
                "acknowledgement_sha256": (
                    action.acknowledgement_sha256
                    if acknowledgement_sha256 is None
                    else acknowledgement_sha256
                ),
            }
        )
        replacement = record.evolve(
            phase=record.phase if transaction_phase is None else transaction_phase,
            actions=tuple(actions),
            now=self._now(),
        )
        self._journal.save(record, replacement)
        return replacement

    def _save_transaction(
        self,
        record: LocalControlJournalRecord,
        phase: TransactionPhase,
    ) -> LocalControlJournalRecord:
        replacement = record.evolve(phase=phase, now=self._now())
        self._journal.save(record, replacement)
        return replacement

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("qualification clock must be timezone-aware")
        return value


def qualification_intent_bytes(intent: ManualFlowQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


def load_current_install_manifest(path: Path | str) -> FreshInstallManifest:
    source = durable_private_path(path, description="local-control install manifest")
    payload = _read_private_path(source)
    if len(payload) > _MAX_PRIVATE_INPUT_BYTES:
        raise QualificationProductionError("current manifest exceeds its size bound")
    try:
        return FreshInstallManifest.model_validate_json(payload)
    except ValueError as error:
        raise QualificationProductionError("current manifest is invalid") from error


def _manual_flow_state(
    writer: ConnectedDeviceWriter,
    target: LocalDeviceBinding,
    status: bytes,
) -> ManualFlowState:
    values: dict[str, object] = {}
    profile = _manual_flow_family_profile(target.product_key)
    if profile.decoded_status_fields:
        # Keep schema access behind the existing writer edge and read-only: Bar
        # admission decodes TimerON without exposing another protocol encoder.
        try:
            decoded = get_product_schema(target.product_key).decode_status(status)
        except (KeyError, ValueError) as error:
            raise QualificationProductionError("current scalar state is invalid") from error
        for name in profile.decoded_status_fields:
            try:
                values[name] = decoded[name]
            except KeyError as error:
                raise QualificationProductionError("current scalar state is incomplete") from error
    encoder_minimum, _encoder_maximum = declared_app_integer_range(
        target.product_key,
        _FUNCTION,
    )
    probes = profile.state_probes + (("manual_flow", encoder_minimum, ("Flow",)),)
    for index, (function, value, fields) in enumerate(probes):
        prepared = writer.prepare_scalar(
            LocalControlIntent(
                request_id=f"inspect_{index:02d}",
                target=target,
                function=function,
                value=value,
            ),
            status,
        )
        try:
            baseline = json.loads(prepared.baseline_readback)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise QualificationProductionError("current scalar state is invalid") from error
        if not isinstance(baseline, dict):
            raise QualificationProductionError("current scalar state is not a mapping")
        for name in fields:
            if name not in baseline:
                raise QualificationProductionError("current scalar state is incomplete")
            values[name] = baseline[name]
    enabled = values["SwitchON"]
    timer = values["TimerON"]
    mode = values["Mode"]
    linkage = None if profile.required_linkage is None else values["Linkage"]
    flow = values["Flow"]
    if (
        type(enabled) is not bool
        or type(timer) is not bool
        or not isinstance(mode, str)
        or (profile.required_linkage is not None and not isinstance(linkage, str))
        or isinstance(flow, bool)
        or not isinstance(flow, int)
    ):
        raise QualificationProductionError("current scalar state types are invalid")
    return ManualFlowState(enabled, timer, mode, linkage, flow)


def _manual_flow_family_profile(product_key: str) -> _ManualFlowFamilyProfile:
    catalog = build_local_control_catalog()
    try:
        manual_flow = catalog.find(product_key, _FUNCTION)
        profile = _MANUAL_FLOW_FAMILY_PROFILES[manual_flow.family]
    except KeyError as error:
        raise QualificationProductionError("qualification target family is unsupported") from error
    if not manual_flow.encoding_available or manual_flow.fields != ("Flow",):
        raise QualificationProductionError("qualification manual-flow encoder is unavailable")

    probes = profile.state_probes + ((_FUNCTION, None, ("Flow",)),)
    for function, value, required_fields in probes:
        try:
            descriptor = catalog.find(product_key, function)
        except KeyError as error:
            raise QualificationProductionError(
                "qualification family state is incomplete"
            ) from error
        if (
            descriptor.family != profile.family
            or not descriptor.encoding_available
            or not set(required_fields).issubset(descriptor.fields)
        ):
            raise QualificationProductionError("qualification family state is incomplete")
        if isinstance(value, str) and value not in descriptor.choices:
            raise QualificationProductionError("qualification family value is unavailable")
    try:
        schema = get_product_schema(product_key)
    except KeyError as error:
        raise QualificationProductionError("qualification family schema is unavailable") from error
    if any(name not in schema.attributes_by_name for name in profile.decoded_status_fields):
        raise QualificationProductionError("qualification family state is incomplete")
    return profile


def _phase_intent(
    intent: ManualFlowQualificationIntent,
    action_index: int,
) -> LocalControlIntent:
    if action_index == 0:
        request_id = _phase_request_id(intent.operation_id, "same")
        value = intent.baseline_flow
    elif action_index == 1:
        request_id = intent.step_request_id
        value = intent.lower_flow
    elif action_index == 2:
        request_id = intent.restore_request_id
        value = intent.baseline_flow
    else:
        raise IndexError("qualification action index is invalid")
    return LocalControlIntent(
        request_id=request_id,
        target=intent.target,
        function=_FUNCTION,
        value=value,
    )


def _receipt(
    intent: ManualFlowQualificationIntent,
    action: JournalAction,
    phase_intent: LocalControlIntent,
    completed_at: datetime,
) -> LocalControlQualificationReceipt:
    if action.acknowledgement_sha256 is None:
        raise QualificationProductionError("qualification action has no acknowledgement")
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


def _current_firmware(endpoint: ResolvedEndpoint) -> str:
    selected = endpoint.wifi_firmware_version or endpoint.gizwits_version
    if selected is None:
        raise QualificationProductionError("current firmware provenance is unavailable")
    if not selected.isascii() or any(ord(character) < 0x20 for character in selected):
        raise QualificationProductionError("current firmware provenance is invalid")
    return selected


def _phase_request_id(operation_id: str, phase: str) -> str:
    digest = hashlib.sha256(f"{operation_id}:{phase}".encode("ascii")).hexdigest()[:32]
    return f"q_{phase}_{digest}"


def _require_operation_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not 8 <= len(value) <= 128
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in value
        )
    ):
        raise ValueError("qualification operation id is invalid")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object, *, newline: bool = False) -> bytes:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return payload + (b"\n" if newline else b"")


def _require_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise QualificationProductionError("qualification directory metadata is unsafe")


def _open_private_directory(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise QualificationProductionError("O_NOFOLLOW is required for qualification artifacts")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = os.fstat(descriptor)
    named = path.lstat()
    if (
        not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o700
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        os.close(descriptor)
        raise QualificationProductionError("qualification directory changed while opening")
    return descriptor


def _install_or_verify_private_file(directory: Path, name: str, payload: bytes) -> None:
    if not name or "/" in name or name in {".", ".."}:
        raise QualificationProductionError("qualification artifact name is invalid")
    if len(payload) > _MAX_ARTIFACT_BYTES:
        raise QualificationProductionError("qualification artifact exceeds its size bound")
    descriptor = _open_private_directory(directory)
    temporary = f".qualification-{secrets.token_hex(16)}"
    temporary_created = False
    try:
        try:
            existing = _read_private_at(descriptor, name, fsync_after=True)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if existing != payload:
                raise QualificationProductionError(
                    "existing qualification artifact differs from expected bytes"
                )
            os.fsync(descriptor)
            return

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        file_descriptor = os.open(temporary, flags, 0o600, dir_fd=descriptor)
        temporary_created = True
        try:
            os.fchmod(file_descriptor, 0o600)
            _write_all(file_descriptor, payload)
            os.fsync(file_descriptor)
            _require_private_file_metadata(os.fstat(file_descriptor))
        finally:
            os.close(file_descriptor)
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            pass
        else:
            os.fsync(descriptor)
        finally:
            os.unlink(temporary, dir_fd=descriptor)
            temporary_created = False
            os.fsync(descriptor)
        if _read_private_at(descriptor, name, fsync_after=True) != payload:
            raise QualificationProductionError("qualification artifact did not reload exactly")
        os.fsync(descriptor)
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileNotFoundError:
                pass
        os.close(descriptor)


def _read_private_path(path: Path) -> bytes:
    descriptor = _open_private_directory(path.parent)
    try:
        return _read_private_at(descriptor, path.name, fsync_after=False)
    finally:
        os.close(descriptor)


def _read_private_at(directory_descriptor: int, name: str, *, fsync_after: bool) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
        dir_fd=directory_descriptor,
    )
    try:
        opened = os.fstat(descriptor)
        _require_private_file_metadata(opened)
        if opened.st_size > _MAX_ARTIFACT_BYTES:
            raise QualificationProductionError("qualification artifact exceeds its size bound")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
            if len(payload) > _MAX_ARTIFACT_BYTES:
                raise QualificationProductionError("qualification artifact exceeds its size bound")
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        _require_private_file_metadata(named)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise QualificationProductionError("qualification artifact changed while reading")
        if fsync_after:
            os.fsync(descriptor)
        return bytes(payload)
    finally:
        os.close(descriptor)


def _require_private_file_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise QualificationProductionError("qualification artifact metadata is unsafe")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short qualification artifact write")
        view = view[written:]


__all__ = [
    "CurrentQualificationEpochSource",
    "ManualFlowQualificationIntent",
    "ManualFlowQualificationProducer",
    "QualificationArtifactStore",
    "QualificationEpoch",
    "QualificationProductionError",
    "QualificationProductionResult",
    "load_current_install_manifest",
    "qualification_intent_bytes",
]
