from __future__ import annotations

import copy
import hashlib
import inspect
import json
import subprocess
import sys
import time
from asyncio import CancelledError
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jebao_flow.exact_restore import (
    ExactRestoreActionKind,
    ExactRestoreActionOutcome,
    ExactRestoreActionResult,
    ExactRestoreAuthority,
    ExactRestoreAuthorityActivation,
    ExactRestoreAuthorityScope,
    ExactRestoreBaseline,
    ExactRestoreController,
    ExactRestoreCycle,
    ExactRestoreDeviceBaseline,
    ExactRestoreErrorCode,
    ExactRestoreEvidenceReference,
    ExactRestoreFinalEvidence,
    ExactRestoreInflightAction,
    ExactRestoreObservation,
    ExactRestorePhase,
    ExactRestorePreflightError,
    ExactRestoreReceipt,
    ExactRestoreRecord,
    ExactRestoreRecoveryRequired,
    ExactRestoreRole,
    ExactRestoreVerificationPolicy,
    ExactScheduleImage,
    OuterControlSnapshot,
    RestorePowerPolicy,
    SafeManualTarget,
    prepare_exact_restore_record,
    prepare_qualified_final_restore_record,
    system_boot_identity_sha256,
    system_boottime_ns,
)
from jebao_flow.physical_identity import PhysicalDeviceBinding
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    get_local_wavemaker_pro_slot_wire,
    patch_local_wavemaker_pro_schedule_slot,
)

NOW = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)
MONOTONIC_NS = 1_000_000_000
BOOT_A_SHA256 = hashlib.sha256(b"boot-a").hexdigest()
BOOT_B_SHA256 = hashlib.sha256(b"boot-b").hexdigest()


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class MutableMonotonicClock:
    def __init__(self, value: int = MONOTONIC_NS) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _binding(label: str) -> PhysicalDeviceBinding:
    return PhysicalDeviceBinding(
        vendor_device_id_digest=_digest(f"{label}-vendor"),
        mac_address_digest=_digest(f"{label}-mac"),
        product_key="local-wavemaker-pro-test",
        config_fingerprint=_digest(f"{label}-config"),
    )


def _schedule_image(
    flow: int,
    *,
    mode: str = "constant",
) -> ExactScheduleImage:
    image = bytearray(LOCAL_WAVEMAKER_PRO_UNUSED_ZERO * LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    if mode == "constant":
        mode_code = 2
        frequency = 20
        feed_time = 0
    elif mode == "feed":
        mode_code = 7
        frequency = 0
        feed_time = 5
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unsupported test mode {mode}")
    image[:9] = bytes((0, 0, 24, 0, mode_code, flow, frequency, feed_time, 0))
    if mode == "feed":
        image[9:18] = bytes((0, 0, 24, 0, 2, 60, 20, 0, 0))
    return ExactScheduleImage.from_bytes(image)


def _device_baseline(
    role: ExactRestoreRole,
    *,
    manual_power: int,
    schedule_flow: int,
    schedule_mode: str = "constant",
    policy_max_power: int = 80,
    attended_max_power: int = 70,
    power_step: int = 10,
) -> ExactRestoreDeviceBaseline:
    label = role.value
    return ExactRestoreDeviceBaseline(
        role=role,
        logical_id=f"logical-{label}",
        physical_binding=_binding(label),
        outer=OuterControlSnapshot(
            enabled=True,
            timer_enabled=True,
            linkage=LinkageRole.INDEPENDENT,
            mode="sine" if role is ExactRestoreRole.MASTER else "constant",
            power=manual_power,
            frequency=20 if role is ExactRestoreRole.MASTER else 30,
        ),
        schedule=_schedule_image(schedule_flow, mode=schedule_mode),
        power_policy=RestorePowerPolicy(
            min_power=30,
            max_power=policy_max_power,
            power_step=power_step,
            attended_max_power=attended_max_power,
        ),
        raw_frame_sha256=_digest(f"{label}-baseline-frame"),
    )


def _baseline(
    *,
    master_manual_power: int = 50,
    slave_manual_power: int = 60,
    master_schedule_flow: int = 60,
    slave_schedule_flow: int = 70,
    master_schedule_mode: str = "constant",
    slave_schedule_mode: str = "constant",
) -> ExactRestoreBaseline:
    return ExactRestoreBaseline(
        devices=(
            _device_baseline(
                ExactRestoreRole.MASTER,
                manual_power=master_manual_power,
                schedule_flow=master_schedule_flow,
                schedule_mode=master_schedule_mode,
            ),
            _device_baseline(
                ExactRestoreRole.SLAVE,
                manual_power=slave_manual_power,
                schedule_flow=slave_schedule_flow,
                schedule_mode=slave_schedule_mode,
            ),
        ),
        evidence=ExactRestoreEvidenceReference(
            plan_artifact_id="JFP-test-plan",
            series_artifact_id="JFS-test-series",
            pair_ordinal=0,
            pair_manifest_sha256=_digest("pair-manifest"),
        ),
        verification_policy=ExactRestoreVerificationPolicy(
            max_observation_age_seconds=30,
            max_final_pair_gap_seconds=20,
        ),
        captured_at=NOW,
    )


def _safe_targets() -> tuple[SafeManualTarget, SafeManualTarget]:
    return (
        SafeManualTarget(
            role=ExactRestoreRole.MASTER,
            power=30,
            frequency=10,
        ),
        SafeManualTarget(
            role=ExactRestoreRole.SLAVE,
            power=40,
            frequency=15,
        ),
    )


class MemoryStore:
    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None
        self.claimed = False
        self.claim_count = 0
        self.load_count = 0
        self.confirm_count = 0
        self.create_count = 0
        self.clear_count = 0
        self.saved: list[dict[str, Any]] = []
        self.on_save: Callable[[dict[str, Any]], None] | None = None
        self.fail_before_save = False

    @contextmanager
    def claim(self) -> Iterator[None]:
        if self.claimed:
            raise AssertionError("nested memory-store claim")
        self.claimed = True
        self.claim_count += 1
        try:
            yield
        finally:
            self.claimed = False

    def create(self, payload: Mapping[str, Any]) -> None:
        self._require_claim()
        self.create_count += 1
        if self.record is not None:
            raise AssertionError("record already exists")
        self.record = copy.deepcopy(dict(payload))

    def load(self) -> dict[str, Any] | None:
        self.load_count += 1
        return copy.deepcopy(self.record)

    def save(self, payload: Mapping[str, Any]) -> None:
        self._require_claim()
        if self.record is None:
            raise AssertionError("record does not exist")
        if self.fail_before_save:
            self.fail_before_save = False
            raise OSError("simulated crash before atomic replacement")
        self.record = copy.deepcopy(dict(payload))
        self.saved.append(copy.deepcopy(self.record))
        if self.on_save is not None:
            self.on_save(copy.deepcopy(self.record))

    def clear(self) -> None:
        self._require_claim()
        self.clear_count += 1
        self.record = None

    def reload_and_confirm_successor(self, expected: Mapping[str, Any] | None) -> bool:
        self._require_claim()
        self.confirm_count += 1
        normalized = None if expected is None else dict(expected)
        return self.record == normalized

    def _require_claim(self) -> None:
        if not self.claimed:
            raise AssertionError("mutation escaped the store claim")


class MemoryQualificationReceiptStore:
    """Fake durable boundary; tests must explicitly persist admitted receipts."""

    def __init__(self) -> None:
        self.receipts: dict[str, dict[str, Any]] = {}
        self.finalizations: dict[str, dict[str, Any]] = {}
        self.fail_persist = False
        self.fail_finalization_confirm = False

    def persist_final_verified_receipt(self, receipt: ExactRestoreReceipt) -> None:
        if self.fail_persist:
            raise OSError("simulated receipt archive fsync failure")
        self.receipts[receipt.receipt_sha256] = copy.deepcopy(receipt.model_dump(mode="json"))
        finalization = {
            "version": 1,
            "operation_sha256": _digest(f"operation:{receipt.operation_id}"),
            "receipt_sha256": receipt.receipt_sha256,
            "cycle": receipt.cycle.value,
        }
        existing = self.finalizations.get(receipt.operation_id)
        if existing is not None and existing != finalization:
            raise OSError("simulated operation finalization conflict")
        self.finalizations[receipt.operation_id] = copy.deepcopy(finalization)

    def load_final_verified_receipt(self, receipt_sha256: str) -> dict[str, Any] | None:
        payload = self.receipts.get(receipt_sha256)
        return copy.deepcopy(payload)

    def load_operation_finalization(self, operation_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.finalizations.get(operation_id))

    def confirm_operation_finalization(
        self,
        receipt: ExactRestoreReceipt,
    ) -> dict[str, Any]:
        if self.fail_finalization_confirm:
            raise OSError("simulated operation finalization confirmation failure")
        finalization = self.load_operation_finalization(receipt.operation_id)
        if (
            finalization is None
            or finalization.get("receipt_sha256") != receipt.receipt_sha256
            or finalization.get("cycle") != receipt.cycle.value
        ):
            raise OSError("simulated operation finalization confirmation failure")
        return finalization


class FakeGuard:
    def __init__(self) -> None:
        self._permitted = False
        self._epoch = 0
        self.trip_count = 0
        self.lease_count = 0
        self.lease_active = False
        self.on_before_release: Callable[[], None] | None = None

    @property
    def permitted(self) -> bool:
        return self._permitted

    @property
    def epoch(self) -> int:
        return self._epoch

    def clear(self) -> None:
        assert self.lease_active
        self._permitted = True

    def trip(self) -> None:
        self._permitted = False
        self._epoch += 1
        self.trip_count += 1

    @contextmanager
    def lease(self) -> Iterator[None]:
        if self.lease_active:
            raise AssertionError("guard lease is not reentrant")
        self.lease_count += 1
        self.lease_active = True
        try:
            yield
        finally:
            try:
                if self.on_before_release is not None:
                    self.on_before_release()
            finally:
                self.lease_active = False
                self.trip()


class FakeDevice:
    def __init__(self, harness: RestoreHarness, role: ExactRestoreRole) -> None:
        self._harness = harness
        self._role = role
        self.connected = False

    @property
    def identity_binding_sha256(self) -> str:
        return self._harness.baseline.for_role(self._role).identity_binding_sha256

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        if self._harness.next_disconnect_behavior == "raise_before_close":
            self._harness.next_disconnect_behavior = None
            raise OSError("simulated ambiguous disconnect")
        self.connected = False

    async def read_connected_identity_binding_sha256(self) -> str:
        assert self.connected
        return self._harness.connected_identity_overrides.get(
            self._role,
            self.identity_binding_sha256,
        )

    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: Callable[[], bool] | None = None,
    ) -> None:
        self._harness.before_write("target", self._role, target, guard)
        behavior = self._harness.consume_write_behavior()
        if behavior == "raise_before_apply":
            raise OSError("simulated uncertain target write")
        self._harness.apply_target(self._role, target)
        if behavior == "cancel_after_apply":
            raise CancelledError
        if behavior == "raise_after_apply":
            raise OSError("simulated lost target acknowledgement")

    async def restore_schedule_image(
        self,
        image: bytes,
        *,
        guard: Callable[[], bool] | None = None,
    ) -> object:
        exact = bytes(image)
        self._harness.before_write("schedule", self._role, exact, guard)
        behavior = self._harness.consume_write_behavior()
        if behavior == "raise_before_apply":
            raise OSError("simulated uncertain schedule write")
        self._harness.schedules[self._role] = ExactScheduleImage.from_bytes(exact)
        if behavior == "cancel_after_apply":
            raise CancelledError
        if behavior == "raise_after_apply":
            raise OSError("simulated lost schedule acknowledgement")
        return None


class RestoreHarness:
    def __init__(
        self,
        baseline: ExactRestoreBaseline,
        store: MemoryStore,
        *,
        clock: Callable[[], datetime] = lambda: NOW,
        monotonic_clock: Callable[[], int] = lambda: MONOTONIC_NS,
    ) -> None:
        self.baseline = baseline
        self.store = store
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.writes: list[tuple[str, ExactRestoreRole, DeviceTarget | bytes]] = []
        self.write_action_indexes: list[int] = []
        self.observed_roles: list[ExactRestoreRole] = []
        self.observation_count = 0
        self.before_observe: Callable[[ExactRestoreRole, int], None] | None = None
        self.observation_wall_offset = timedelta(0)
        self.observation_monotonic_offset_ns = 0
        self.next_write_behavior: str | None = None
        self.next_disconnect_behavior: str | None = None
        self.wrong_identity_role: ExactRestoreRole | None = None
        self.connected_identity_overrides: dict[ExactRestoreRole, str] = {}
        self.outers = {
            ExactRestoreRole.MASTER: OuterControlSnapshot(
                enabled=True,
                timer_enabled=True,
                linkage=LinkageRole.MASTER,
                mode="random",
                power=40,
                frequency=10,
            ),
            ExactRestoreRole.SLAVE: OuterControlSnapshot(
                enabled=True,
                timer_enabled=True,
                linkage=LinkageRole.ASYNC_SLAVE,
                mode="random",
                power=30,
                frequency=10,
            ),
        }
        self.schedules = {
            role: ExactScheduleImage.from_bytes(
                patch_local_wavemaker_pro_schedule_slot(
                    baseline.for_role(role).schedule.image_bytes,
                    LOCAL_WAVEMAKER_PRO_SLOT_COUNT - 1,
                    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
                )
            )
            for role in (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE)
        }
        self.devices = {
            role: FakeDevice(self, role)
            for role in (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE)
        }

    async def observe(self, role: ExactRestoreRole) -> ExactRestoreObservation:
        assert not any(device.connected for device in self.devices.values())
        self.observation_count += 1
        self.observed_roles.append(role)
        if self.before_observe is not None:
            self.before_observe(role, self.observation_count)
        baseline = self.baseline.for_role(role)
        binding = baseline.identity_binding_sha256
        if self.wrong_identity_role is role:
            binding = _digest(f"wrong-{role.value}-identity")
        state_digest = _digest(
            f"{role.value}:{self.observation_count}:"
            f"{self.outers[role].model_dump_json()}:{self.schedules[role].image_sha256}"
        )
        acquired_at = self.clock() + self.observation_wall_offset
        acquired_monotonic_ns = self.monotonic_clock() + self.observation_monotonic_offset_ns
        return ExactRestoreObservation(
            role=role,
            identity_binding_sha256=binding,
            outer=self.outers[role],
            schedule=self.schedules[role],
            raw_frame_sha256=state_digest,
            requested_at=acquired_at,
            observed_at=acquired_at,
            received_at=acquired_at,
            requested_monotonic_ns=acquired_monotonic_ns,
            observed_monotonic_ns=acquired_monotonic_ns,
            received_monotonic_ns=acquired_monotonic_ns,
        )

    def resolve_device(
        self,
        role: ExactRestoreRole,
        observation: ExactRestoreObservation,
    ) -> FakeDevice:
        assert observation.role is role
        return self.devices[role]

    def before_write(
        self,
        kind: str,
        role: ExactRestoreRole,
        payload: DeviceTarget | bytes,
        guard: Callable[[], bool] | None,
    ) -> None:
        assert guard is not None and guard()
        assert self.store.record is not None
        assert self.store.record["phase"] == ExactRestorePhase.RESTORING.value
        inflight = self.store.record["inflight"]
        assert isinstance(inflight, dict)
        assert inflight["index"] == len(self.store.record["completed_actions"])
        self.write_action_indexes.append(inflight["index"])
        if kind == "target":
            assert isinstance(payload, DeviceTarget)
            assert payload.linkage is LinkageRole.INDEPENDENT
            assert payload.timer_enabled is not None
            if self.outers[role].linkage is LinkageRole.ASYNC_SLAVE:
                # The only permitted manual write to a linked async slave includes detach and
                # TimerOFF in that same atomic DeviceTarget frame.
                assert payload.timer_enabled is False
        self.writes.append((kind, role, payload))

    def consume_write_behavior(self) -> str | None:
        behavior = self.next_write_behavior
        self.next_write_behavior = None
        return behavior

    def apply_target(self, role: ExactRestoreRole, target: DeviceTarget) -> None:
        assert target.mode is not None
        assert target.frequency is not None
        assert target.linkage is not None
        assert target.timer_enabled is not None
        self.outers[role] = OuterControlSnapshot(
            enabled=target.enabled,
            timer_enabled=target.timer_enabled,
            linkage=target.linkage,
            mode=target.mode,
            power=target.power,
            frequency=target.frequency,
        )


def _authority(
    record: ExactRestoreRecord,
    *,
    permit_crash_resume: bool = False,
    journal_context_sha256: str | None = None,
    crash_resume_inflight_sha256: str | None = None,
    cycle: ExactRestoreCycle | None = None,
    now: datetime = NOW,
    monotonic_now: int = MONOTONIC_NS,
    boot_identity_sha256: str = BOOT_A_SHA256,
    qualification_receipt_sha256: str | None = None,
    confirmation_label: str = "confirmation-token",
) -> ExactRestoreAuthority:
    selected_cycle = record.cycle if cycle is None else cycle
    scope = (
        ExactRestoreAuthorityScope.EXACT_BASELINE_ONLY
        if selected_cycle is ExactRestoreCycle.BASELINE_RESTORE
        else ExactRestoreAuthorityScope.BOOTSTRAP_QUALIFICATION
    )
    if selected_cycle is ExactRestoreCycle.BASELINE_RESTORE:
        qualification_receipt_sha256 = (
            qualification_receipt_sha256 or record.qualification_receipt_sha256
        )
    return ExactRestoreAuthority(
        operation_id=record.operation_id,
        cycle=selected_cycle,
        baseline_sha256=record.baseline_sha256,
        action_plan_sha256=record.action_plan_sha256,
        verification_policy_sha256=record.baseline.verification_policy.policy_sha256,
        journal_context_sha256=journal_context_sha256 or record.authority_context_sha256,
        scope=scope,
        qualification_receipt_sha256=qualification_receipt_sha256,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
        boot_identity_sha256=boot_identity_sha256,
        issued_monotonic_ns=monotonic_now,
        deadline_monotonic_ns=monotonic_now + 10 * 60 * 1_000_000_000,
        confirmation_token_sha256=_digest(confirmation_label),
        permit_crash_resume=permit_crash_resume,
        crash_resume_inflight_sha256=(
            crash_resume_inflight_sha256
            if crash_resume_inflight_sha256 is not None
            else (
                record.inflight.inflight_sha256
                if permit_crash_resume and record.inflight is not None
                else None
            )
        ),
    )


def _sentinel_final_record(
    *,
    baseline: ExactRestoreBaseline | None = None,
    safe_targets: tuple[SafeManualTarget, SafeManualTarget] | None = None,
    operation_id: str = "operation-sentinel-qualification",
) -> ExactRestoreRecord:
    sentinel = prepare_exact_restore_record(
        baseline or _baseline(),
        safe_targets or _safe_targets(),
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=operation_id,
        now=NOW - timedelta(minutes=2),
    )
    authority = _authority(
        sentinel,
        now=NOW - timedelta(minutes=2),
        monotonic_now=MONOTONIC_NS,
    )
    activation = ExactRestoreAuthorityActivation(
        authority_sha256=authority.authority_sha256,
        boot_identity_sha256=authority.boot_identity_sha256,
        accepted_wall=authority.issued_at,
        accepted_monotonic_ns=authority.issued_monotonic_ns,
        deadline_monotonic_ns=authority.deadline_monotonic_ns,
    )
    completed_actions = tuple(
        ExactRestoreActionResult(
            index=action.index,
            action_id=action.action_id,
            outcome=ExactRestoreActionOutcome.WRITTEN_VERIFIED,
            pre_state_sha256=_digest(f"{operation_id}-{action.index}-pre"),
            post_state_sha256=_digest(f"{operation_id}-{action.index}-post"),
            completed_at=NOW - timedelta(minutes=1),
        )
        for action in sentinel.actions
    )
    awaiting_payload = sentinel.model_dump(mode="json")
    awaiting_payload.update(
        phase=ExactRestorePhase.AWAITING_FINAL_VERIFY.value,
        authority=authority.model_dump(mode="json"),
        authority_activation=activation.model_dump(mode="json"),
        completed_actions=[item.model_dump(mode="json") for item in completed_actions],
        updated_at=NOW - timedelta(minutes=1),
    )
    awaiting = ExactRestoreRecord.model_validate(awaiting_payload)
    acquired_at = NOW - timedelta(minutes=1)
    observations = tuple(
        ExactRestoreObservation(
            role=role,
            identity_binding_sha256=awaiting.baseline.for_role(role).identity_binding_sha256,
            outer=awaiting.baseline.for_role(role).outer,
            schedule=awaiting.baseline.for_role(role).schedule,
            raw_frame_sha256=_digest(f"{operation_id}-{role.value}-final"),
            requested_at=acquired_at,
            observed_at=acquired_at,
            received_at=acquired_at,
            requested_monotonic_ns=MONOTONIC_NS + 1,
            observed_monotonic_ns=MONOTONIC_NS + 1,
            received_monotonic_ns=MONOTONIC_NS + 1,
        )
        for role in (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE)
    )
    receipt = ExactRestoreReceipt(
        operation_id=sentinel.operation_id,
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        baseline_sha256=sentinel.baseline_sha256,
        action_plan_sha256=sentinel.action_plan_sha256,
        authority_sha256=authority.authority_sha256,
        authority_chain_sha256=awaiting.authority_chain_sha256,
        qualification_receipt_sha256=None,
        completed_action_count=8,
        final_raw_frame_sha256=tuple(item.raw_frame_sha256 for item in observations),
        completed_at=acquired_at,
    )
    final_payload = awaiting.model_dump(mode="json")
    final_payload.update(
        phase=ExactRestorePhase.FINAL_VERIFIED.value,
        final_evidence=ExactRestoreFinalEvidence(
            receipt_sha256=receipt.receipt_sha256,
            observations=observations,
            completed_at=acquired_at,
            completed_monotonic_ns=MONOTONIC_NS + 1,
        ).model_dump(mode="json"),
    )
    return ExactRestoreRecord.model_validate(final_payload)


def _promoted_record(
    *,
    baseline: ExactRestoreBaseline | None = None,
    operation_id: str = "operation-baseline-restore",
) -> ExactRestoreRecord:
    qualification = _sentinel_final_record(baseline=baseline)
    store = MemoryStore()
    store.record = qualification.model_dump(mode="json")
    guard = FakeGuard()
    harness = RestoreHarness(qualification.baseline, store)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=MemoryQualificationReceiptStore(),
        clock=lambda: NOW,
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    return controller.promote_to_baseline_restore(operation_id=operation_id)


def _prepared(
    *,
    cycle: ExactRestoreCycle = ExactRestoreCycle.BASELINE_RESTORE,
) -> ExactRestoreRecord:
    if cycle is ExactRestoreCycle.BASELINE_RESTORE:
        return _promoted_record()
    return prepare_exact_restore_record(
        _baseline(),
        _safe_targets(),
        cycle=cycle,
        operation_id=f"operation-{cycle.value}",
        now=NOW,
    )


def _armed_controller(
    *,
    cycle: ExactRestoreCycle = ExactRestoreCycle.BASELINE_RESTORE,
    clock: Callable[[], datetime] = lambda: NOW,
    monotonic_clock: Callable[[], int] = lambda: MONOTONIC_NS,
    boot_identity: Callable[[], str] = lambda: BOOT_A_SHA256,
) -> tuple[
    ExactRestoreController,
    MemoryStore,
    FakeGuard,
    RestoreHarness,
    ExactRestoreRecord,
]:
    prepared = _prepared(cycle=cycle)
    store = MemoryStore()
    guard = FakeGuard()
    qualification_store = MemoryQualificationReceiptStore()
    harness = RestoreHarness(
        prepared.baseline,
        store,
        clock=clock,
        monotonic_clock=monotonic_clock,
    )
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=qualification_store,
        clock=clock,
        monotonic_clock=monotonic_clock,
        boot_identity=boot_identity,
    )
    if cycle is ExactRestoreCycle.BASELINE_RESTORE:
        store.record = prepared.model_dump(mode="json")
    else:
        controller.create(prepared)
    armed = controller.arm(
        _authority(
            prepared,
            monotonic_now=monotonic_clock(),
            boot_identity_sha256=boot_identity(),
        )
    )
    return controller, store, guard, harness, armed


def test_baseline_plan_is_exact_six_action_atomic_order() -> None:
    record = _prepared()

    assert [(action.role, action.kind) for action in record.actions] == [
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.SAFE_FALLBACK),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.SAFE_FALLBACK),
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.RESTORE_SCHEDULE),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.RESTORE_SCHEDULE),
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.RESTORE_OUTER),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.RESTORE_OUTER),
    ]


def test_arm_rejects_authority_for_a_stale_exact_journal_snapshot() -> None:
    prepared = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    stale_authority = _authority(prepared, confirmation_label="stale-prepared-snapshot")
    store = MemoryStore()
    guard = FakeGuard()
    harness = RestoreHarness(prepared.baseline, store)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        clock=lambda: NOW,
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    controller.create(prepared)
    payload = prepared.model_dump(mode="json")
    payload["updated_at"] = (NOW + timedelta(microseconds=1)).isoformat()
    changed = ExactRestoreRecord.model_validate(payload)
    with store.claim():
        store.save(changed.model_dump(mode="json"))

    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.arm(stale_authority)

    assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == changed
    assert harness.writes == []


@pytest.mark.parametrize(
    "current",
    (NOW + timedelta(seconds=11), NOW - timedelta(microseconds=1)),
    ids=("expired-conservative-pair", "clock-regression"),
)
def test_first_sentinel_arm_rejects_nonfresh_baseline_without_write(current: datetime) -> None:
    prepared = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    store = MemoryStore()
    guard = FakeGuard()
    harness = RestoreHarness(prepared.baseline, store)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=MemoryQualificationReceiptStore(),
        clock=lambda: current,
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    controller.create(prepared)

    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.arm(_authority(prepared, now=current))

    assert rejected.value.code is ExactRestoreErrorCode.BASELINE_EXPIRED
    assert rejected.value.diagnostic["maximum_age_ms"] == 30_000
    assert rejected.value.diagnostic["maximum_pair_gap_ms"] == 20_000
    assert ExactRestoreRecord.model_validate(store.load()) == prepared
    assert harness.writes == []


@pytest.mark.asyncio
async def test_sentinel_execute_rechecks_baseline_age_after_fresh_arm() -> None:
    prepared = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    store = MemoryStore()
    guard = FakeGuard()
    current = [NOW]
    harness = RestoreHarness(prepared.baseline, store, clock=lambda: current[0])
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=MemoryQualificationReceiptStore(),
        clock=lambda: current[0],
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    controller.create(prepared)
    armed = controller.arm(_authority(prepared))
    current[0] = NOW + timedelta(seconds=11)

    with pytest.raises(ExactRestorePreflightError) as rejected:
        await controller.execute()

    assert rejected.value.code is ExactRestoreErrorCode.BASELINE_EXPIRED
    assert rejected.value.diagnostic == {
        "reason": "baseline_age_exceeded",
        "capture_age_ms": 11_000,
        "conservative_age_ms": 31_000,
        "maximum_age_ms": 30_000,
        "maximum_pair_gap_ms": 20_000,
    }
    assert ExactRestoreRecord.model_validate(store.load()) == armed
    assert harness.writes == []


def test_baseline_restore_arm_remains_available_after_original_capture_ages() -> None:
    prepared = _prepared(cycle=ExactRestoreCycle.BASELINE_RESTORE)
    store = MemoryStore()
    store.record = prepared.model_dump(mode="json")
    guard = FakeGuard()
    current = NOW + timedelta(days=1)
    harness = RestoreHarness(prepared.baseline, store, clock=lambda: current)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=MemoryQualificationReceiptStore(),
        clock=lambda: current,
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )

    armed = controller.arm(_authority(prepared, now=current))

    assert armed.phase is ExactRestorePhase.ARMED
    assert harness.writes == []


def test_reauthorization_rejects_a_stale_authority_chain_head() -> None:
    controller, store, _guard, harness, armed = _armed_controller()
    accepted_authority = _authority(armed, confirmation_label="accepted-chain-head")
    stale_competing_authority = _authority(armed, confirmation_label="stale-chain-head")

    renewed = controller.reauthorize(accepted_authority)
    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.reauthorize(stale_competing_authority)

    assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == renewed
    assert harness.writes == []


def test_unqualified_baseline_record_cannot_be_prepared_directly() -> None:
    with pytest.raises(ExactRestorePreflightError) as rejected:
        prepare_exact_restore_record(
            _baseline(),
            _safe_targets(),
            cycle=ExactRestoreCycle.BASELINE_RESTORE,
            operation_id="unqualified-baseline",
            now=NOW,
        )

    assert rejected.value.code is ExactRestoreErrorCode.INVALID_BASELINE


@pytest.mark.asyncio
async def test_execute_uses_atomic_targets_and_full_schedule_images_only() -> None:
    controller, _store, guard, harness, _armed = _armed_controller()

    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert len(completed.completed_actions) == 6
    assert [(kind, role) for kind, role, _payload in harness.writes] == [
        ("target", ExactRestoreRole.SLAVE),
        ("target", ExactRestoreRole.MASTER),
        ("schedule", ExactRestoreRole.SLAVE),
        ("schedule", ExactRestoreRole.MASTER),
        ("target", ExactRestoreRole.SLAVE),
        ("target", ExactRestoreRole.MASTER),
    ]
    safe_by_role = {target.role: target for target in completed.safe_targets}
    for _kind, role, payload in harness.writes[:2]:
        assert isinstance(payload, DeviceTarget)
        safe = safe_by_role[role]
        assert payload == DeviceTarget(
            enabled=True,
            power=safe.power,
            mode="constant",
            frequency=safe.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
    for _kind, role, payload in harness.writes[2:4]:
        assert payload == completed.baseline.for_role(role).schedule.image_bytes
    for _kind, role, payload in harness.writes[4:]:
        baseline = completed.baseline.for_role(role)
        assert payload == DeviceTarget(
            enabled=True,
            power=baseline.outer.power,
            mode=baseline.outer.mode,
            frequency=baseline.outer.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
    assert harness.observed_roles == [
        role for action in completed.actions for role in (action.role, action.role)
    ] + [ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE]
    assert guard.trip_count == 1
    assert guard.permitted is False


@pytest.mark.asyncio
async def test_sentinel_plan_inserts_one_unused_slot_toggle_per_role() -> None:
    controller, _store, _guard, harness, _armed = _armed_controller(
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION
    )

    completed = await controller.execute()

    assert [(action.role, action.kind) for action in completed.actions] == [
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.SAFE_FALLBACK),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.SAFE_FALLBACK),
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.QUALIFY_SENTINEL),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.QUALIFY_SENTINEL),
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.RESTORE_SCHEDULE),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.RESTORE_SCHEDULE),
        (ExactRestoreRole.SLAVE, ExactRestoreActionKind.RESTORE_OUTER),
        (ExactRestoreRole.MASTER, ExactRestoreActionKind.RESTORE_OUTER),
    ]
    assert [(kind, role) for kind, role, _payload in harness.writes] == [
        ("target", ExactRestoreRole.SLAVE),
        ("target", ExactRestoreRole.MASTER),
        ("schedule", ExactRestoreRole.SLAVE),
        ("schedule", ExactRestoreRole.MASTER),
        ("schedule", ExactRestoreRole.SLAVE),
        ("schedule", ExactRestoreRole.MASTER),
        ("target", ExactRestoreRole.SLAVE),
        ("target", ExactRestoreRole.MASTER),
    ]
    for offset, role in enumerate((ExactRestoreRole.SLAVE, ExactRestoreRole.MASTER)):
        sentinel = harness.writes[2 + offset][2]
        restored = harness.writes[4 + offset][2]
        baseline_image = completed.baseline.for_role(role).schedule.image_bytes
        assert isinstance(sentinel, bytes)
        assert restored == baseline_image
        differing = [
            index
            for index, pair in enumerate(zip(sentinel, baseline_image, strict=True))
            if pair[0] != pair[1]
        ]
        assert differing == list(range(9, 18))
        assert get_local_wavemaker_pro_slot_wire(sentinel, 1) == LOCAL_WAVEMAKER_PRO_UNUSED_EE


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"master_manual_power": 20}, "manual power"),
        ({"master_manual_power": 80}, "manual power"),
        ({"master_manual_power": 55}, "manual power"),
        ({"master_schedule_flow": 20}, "schedule slot"),
        ({"master_schedule_flow": 90}, "schedule slot"),
        ({"master_schedule_flow": 55}, "schedule slot"),
    ],
)
def test_baseline_rejects_every_out_of_policy_flow_without_clamping(
    overrides: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _baseline(**overrides)


def test_feed_zero_is_the_only_schedule_minimum_exception() -> None:
    baseline = _baseline(
        master_schedule_flow=0,
        master_schedule_mode="feed",
    )

    assert baseline.devices[0].schedule.image_bytes[5] == 0
    with pytest.raises(ValueError, match="schedule slot"):
        _baseline(master_schedule_flow=0)


def test_latent_manual_flow_cannot_exceed_active_non_feed_schedule_ceiling() -> None:
    with pytest.raises(ValueError, match="latent manual power"):
        _device_baseline(
            ExactRestoreRole.SLAVE,
            manual_power=89,
            schedule_flow=80,
            policy_max_power=100,
            attended_max_power=100,
            power_step=1,
        )


@pytest.mark.asyncio
async def test_wrong_observed_identity_stops_before_any_write() -> None:
    controller, store, guard, harness, _armed = _armed_controller()
    harness.wrong_identity_role = ExactRestoreRole.SLAVE

    with pytest.raises(ExactRestoreRecoveryRequired) as raised:
        await controller.execute()

    assert raised.value.code is ExactRestoreErrorCode.BINDING
    assert harness.writes == []
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert latched.error_code is ExactRestoreErrorCode.BINDING
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_endpoint_swap_after_observation_stops_before_write() -> None:
    controller, store, guard, harness, _armed = _armed_controller()
    harness.connected_identity_overrides[ExactRestoreRole.SLAVE] = _digest(
        "swapped-connected-endpoint"
    )

    with pytest.raises(ExactRestoreRecoveryRequired) as raised:
        await controller.execute()

    assert raised.value.code is ExactRestoreErrorCode.BINDING
    assert harness.writes == []
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert latched.error_code is ExactRestoreErrorCode.BINDING
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_uncertain_write_satisfied_by_fresh_read_is_never_resent() -> None:
    controller, _store, guard, harness, _armed = _armed_controller()
    harness.next_write_behavior = "raise_after_apply"

    completed = await controller.execute()

    assert len(harness.writes) == 6
    assert harness.write_action_indexes == list(range(6))
    assert [(kind, role) for kind, role, _payload in harness.writes].count(
        ("target", ExactRestoreRole.SLAVE)
    ) == 2
    assert completed.completed_actions[0].outcome is (
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN
    )
    assert completed.completed_actions[0].action_id == completed.actions[0].action_id
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_uncertain_write_mismatch_latches_recovery_and_never_resends() -> None:
    controller, store, guard, harness, _armed = _armed_controller()
    harness.next_write_behavior = "raise_before_apply"

    with pytest.raises(ExactRestoreRecoveryRequired) as raised:
        await controller.execute()

    assert raised.value.code is ExactRestoreErrorCode.UNCERTAIN_WRITE
    assert len(harness.writes) == 1
    assert harness.write_action_indexes == [0]
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert latched.error_code is ExactRestoreErrorCode.UNCERTAIN_WRITE
    assert latched.inflight is not None
    with pytest.raises(ExactRestorePreflightError):
        await controller.execute()
    assert len(harness.writes) == 1
    assert harness.write_action_indexes == [0]
    assert guard.trip_count == 2


@pytest.mark.asyncio
async def test_recovery_required_uses_separate_authority_and_no_inflight_can_resume() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.wrong_identity_role = ExactRestoreRole.SLAVE

    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()

    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert latched.inflight is None
    with pytest.raises(ExactRestorePreflightError):
        await controller.execute()
    with pytest.raises(ExactRestorePreflightError):
        controller.reauthorize(_authority(latched, confirmation_label="wrong-api"))

    harness.wrong_identity_role = None
    recovered = controller.recover(
        _authority(latched, confirmation_label="attended-no-inflight-recovery")
    )

    assert recovered.phase is ExactRestorePhase.RESTORING
    assert recovered.error_code is None
    assert recovered.completed_actions == latched.completed_actions
    assert recovered.inflight is latched.inflight
    assert recovered.prior_authorities == (latched.authority,)
    completed = await controller.execute()
    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))


@pytest.mark.asyncio
async def test_partial_prefix_recovery_preserves_prefix_and_continues_at_next_action() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()

    def fail_before_second_action(role: ExactRestoreRole, observation_count: int) -> None:
        if observation_count == 3:
            harness.wrong_identity_role = role

    harness.before_observe = fail_before_second_action
    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()

    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert len(latched.completed_actions) == 1
    assert latched.inflight is None
    first_result = latched.completed_actions[0]

    harness.before_observe = None
    harness.wrong_identity_role = None
    recovered = controller.recover(
        _authority(latched, confirmation_label="attended-partial-recovery")
    )
    assert recovered.completed_actions == (first_result,)
    assert recovered.inflight is None

    completed = await controller.execute()
    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert completed.completed_actions[0] == first_result
    assert harness.write_action_indexes == list(range(6))


@pytest.mark.asyncio
async def test_uncertain_inflight_recovery_observes_only_and_never_resends() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.next_write_behavior = "raise_before_apply"

    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.inflight is not None
    writes_before_recovery = list(harness.writes)

    controller.recover(
        _authority(
            latched,
            permit_crash_resume=True,
            confirmation_label="attended-uncertain-recovery",
        )
    )
    with pytest.raises(ExactRestoreRecoveryRequired) as still_uncertain:
        await controller.execute()

    assert still_uncertain.value.code is ExactRestoreErrorCode.UNCERTAIN_WRITE
    assert harness.writes == writes_before_recovery
    relatched = ExactRestoreRecord.model_validate(store.load())
    assert relatched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert relatched.inflight == latched.inflight


@pytest.mark.asyncio
async def test_recover_rejects_crash_permit_bound_to_a_different_inflight() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.next_write_behavior = "raise_before_apply"
    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.inflight is not None
    wrong_inflight_authority = _authority(
        latched,
        permit_crash_resume=True,
        crash_resume_inflight_sha256=_digest("different-inflight"),
        confirmation_label="wrong-inflight-binding",
    )

    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.recover(wrong_inflight_authority)

    assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == latched
    assert harness.write_action_indexes == [0]


@pytest.mark.asyncio
async def test_disconnect_recovery_reconciles_applied_inflight_without_resend() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.next_disconnect_behavior = "raise_before_close"

    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.inflight is not None
    assert harness.write_action_indexes == [0]
    harness.devices[ExactRestoreRole.SLAVE].connected = False

    controller.recover(
        _authority(
            latched,
            permit_crash_resume=True,
            confirmation_label="attended-disconnect-recovery",
        )
    )
    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))
    assert completed.completed_actions[0].outcome is (
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN
    )


@pytest.mark.asyncio
async def test_crash_resume_permit_for_inflight_i_cannot_resume_future_inflight_j() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.next_disconnect_behavior = "raise_before_close"
    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()
    latched_i = ExactRestoreRecord.model_validate(store.load())
    assert latched_i.inflight is not None
    inflight_i_sha256 = latched_i.inflight.inflight_sha256
    harness.devices[ExactRestoreRole.SLAVE].connected = False

    recovered_i = controller.recover(
        _authority(
            latched_i,
            permit_crash_resume=True,
            confirmation_label="resume-only-inflight-i",
        )
    )
    assert recovered_i.authority is not None
    assert recovered_i.authority.crash_resume_inflight_sha256 == inflight_i_sha256

    harness.next_write_behavior = "cancel_after_apply"
    with pytest.raises(CancelledError):
        await controller.execute()
    crashed_j = ExactRestoreRecord.model_validate(store.load())
    assert crashed_j.phase is ExactRestorePhase.RESTORING
    assert crashed_j.inflight is not None
    assert crashed_j.inflight.index == 1
    assert crashed_j.inflight.inflight_sha256 != inflight_i_sha256
    writes_through_j = list(harness.writes)

    with pytest.raises(ExactRestoreRecoveryRequired) as wrong_permit:
        await controller.execute()
    assert wrong_permit.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.writes == writes_through_j
    latched_j = ExactRestoreRecord.model_validate(store.load())
    assert latched_j.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert latched_j.inflight == crashed_j.inflight

    controller.recover(
        _authority(
            latched_j,
            permit_crash_resume=True,
            confirmation_label="resume-only-inflight-j",
        )
    )
    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))


@pytest.mark.asyncio
async def test_recovery_save_failure_preserves_latched_error_and_progress() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.wrong_identity_role = ExactRestoreRole.SLAVE
    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()
    latched = ExactRestoreRecord.model_validate(store.load())
    authority = _authority(latched, confirmation_label="recovery-before-save-failure")
    store.fail_before_save = True

    with pytest.raises(ExactRestorePreflightError) as failed:
        controller.recover(authority)

    assert failed.value.code is ExactRestoreErrorCode.JOURNAL
    assert ExactRestoreRecord.model_validate(store.load()) == latched
    assert harness.write_action_indexes == []


@pytest.mark.asyncio
async def test_recover_rejects_authority_for_stale_latched_error_context() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.wrong_identity_role = ExactRestoreRole.SLAVE
    with pytest.raises(ExactRestoreRecoveryRequired):
        await controller.execute()
    latched = ExactRestoreRecord.model_validate(store.load())
    stale_authority = _authority(latched, confirmation_label="stale-latched-context")
    payload = latched.model_dump(mode="json")
    payload["error_code"] = ExactRestoreErrorCode.DEVICE_IO.value
    changed = ExactRestoreRecord.model_validate(payload)
    with store.claim():
        store.save(changed.model_dump(mode="json"))

    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.recover(stale_authority)

    assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == changed
    assert harness.write_action_indexes == []


@pytest.mark.asyncio
async def test_complete_prefix_recovery_returns_to_final_verify_without_more_writes() -> None:
    controller, store, guard, harness, _armed = _armed_controller()

    def trip_after_complete_prefix(payload: dict[str, Any]) -> None:
        if len(payload["completed_actions"]) == len(payload["actions"]):
            store.on_save = None
            guard.trip()

    store.on_save = trip_after_complete_prefix
    with pytest.raises(ExactRestoreRecoveryRequired) as interrupted:
        await controller.execute()
    assert interrupted.value.code is ExactRestoreErrorCode.SAFETY_INTERLOCK
    latched = ExactRestoreRecord.model_validate(store.load())
    assert latched.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert len(latched.completed_actions) == len(latched.actions)
    assert latched.inflight is None
    writes_before_recovery = list(harness.writes)

    recovered = controller.recover(
        _authority(latched, confirmation_label="attended-final-verify-recovery")
    )
    assert recovered.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY
    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.writes == writes_before_recovery


@pytest.mark.asyncio
async def test_cancellation_after_apply_preserves_inflight_and_sends_no_later_action() -> None:
    controller, store, guard, harness, _armed = _armed_controller()
    harness.next_write_behavior = "cancel_after_apply"

    with pytest.raises(CancelledError):
        await controller.execute()

    assert len(harness.writes) == 1
    assert harness.write_action_indexes == [0]
    assert harness.observation_count == 1
    assert all(device.connected is False for device in harness.devices.values())
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.RESTORING
    assert retained.completed_actions == ()
    assert retained.inflight is not None
    assert retained.inflight.index == 0
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_disconnect_failure_never_starts_independent_verification() -> None:
    controller, store, guard, harness, _armed = _armed_controller()
    harness.next_disconnect_behavior = "raise_before_close"

    with pytest.raises(ExactRestoreRecoveryRequired) as ambiguous:
        await controller.execute()

    assert ambiguous.value.code is ExactRestoreErrorCode.UNCERTAIN_WRITE
    assert len(harness.writes) == 1
    assert harness.write_action_indexes == [0]
    assert harness.observation_count == 1
    assert harness.devices[ExactRestoreRole.SLAVE].connected is True
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert retained.completed_actions == ()
    assert retained.inflight is not None
    assert retained.inflight.index == 0
    assert retained.error_code is ExactRestoreErrorCode.UNCERTAIN_WRITE
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_crash_inflight_is_reconciled_by_fresh_read_without_resend() -> None:
    controller, store, guard, harness, armed = _armed_controller()
    first = armed.actions[0]
    authority = armed.authority
    assert authority is not None
    safe = next(target for target in armed.safe_targets if target.role is first.role)
    harness.apply_target(
        first.role,
        DeviceTarget(
            enabled=True,
            power=safe.power,
            mode="constant",
            frequency=safe.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        ),
    )
    inflight = ExactRestoreInflightAction(
        index=first.index,
        action_id=first.action_id,
        target_sha256=first.target_sha256,
        pre_state_sha256=_digest("pre-crash-state"),
        authority_sha256=authority.authority_sha256,
        intent_at=NOW,
    )
    payload = armed.model_dump(mode="json")
    payload.update(
        phase=ExactRestorePhase.RESTORING,
        inflight=inflight,
        updated_at=NOW,
    )
    crashed = ExactRestoreRecord.model_validate(payload)
    with store.claim():
        store.save(crashed.model_dump(mode="json"))

    controller.reauthorize(
        _authority(
            crashed,
            permit_crash_resume=True,
            confirmation_label="crash-inflight-resume",
        )
    )

    completed = await controller.execute()

    assert len(harness.writes) == 5
    assert harness.write_action_indexes == [1, 2, 3, 4, 5]
    assert harness.writes[0][:2] == ("target", ExactRestoreRole.MASTER)
    assert harness.observed_roles[0] is ExactRestoreRole.SLAVE
    assert completed.completed_actions[0].outcome is (
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN
    )
    assert completed.completed_actions[0].pre_state_sha256 == _digest("pre-crash-state")
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_expired_authority_between_actions_requires_durable_reauthorization() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, guard, harness, _armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )

    def expire_after_first_action(payload: dict[str, Any]) -> None:
        if len(payload["completed_actions"]) == 1 and payload["inflight"] is None:
            store.on_save = None
            wall.value = NOW + timedelta(minutes=11)
            monotonic.value = MONOTONIC_NS + 11 * 60 * 1_000_000_000

    store.on_save = expire_after_first_action

    with pytest.raises(ExactRestorePreflightError) as expired:
        await controller.execute()
    assert expired.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.write_action_indexes == [0]
    interrupted = ExactRestoreRecord.model_validate(store.load())
    assert interrupted.phase is ExactRestorePhase.RESTORING
    assert len(interrupted.completed_actions) == 1
    assert interrupted.inflight is None

    with pytest.raises(ExactRestorePreflightError) as still_expired:
        await controller.execute()
    assert still_expired.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.write_action_indexes == [0]

    resumed = controller.reauthorize(
        _authority(interrupted, now=wall.value, monotonic_now=monotonic.value)
    )
    assert resumed.prior_authorities == (interrupted.authority,)
    assert resumed.authority != interrupted.authority
    assert ExactRestoreRecord.model_validate(store.load()) == resumed

    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))
    assert guard.trip_count == 3


@pytest.mark.asyncio
async def test_frozen_wall_delayed_start_cannot_reset_durable_monotonic_deadline() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, _guard, harness, armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )
    activation = armed.authority_activation
    assert activation is not None
    assert activation.accepted_wall == NOW
    assert activation.accepted_monotonic_ns == MONOTONIC_NS
    assert activation.deadline_monotonic_ns == MONOTONIC_NS + 10 * 60 * 1_000_000_000

    monotonic.value = MONOTONIC_NS + 11 * 60 * 1_000_000_000

    with pytest.raises(ExactRestorePreflightError) as delayed:
        await controller.execute()
    assert delayed.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.observation_count == 0
    assert harness.writes == []
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.ARMED
    assert retained.authority_activation == activation

    renewed = controller.reauthorize(
        _authority(
            retained,
            now=wall.value,
            monotonic_now=monotonic.value,
            confirmation_label="delayed-start-renewal",
        )
    )
    assert renewed.authority_activation is not None
    assert renewed.authority_activation.accepted_monotonic_ns == monotonic.value

    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))


def test_preissued_authority_cannot_be_armed_after_monotonic_expiry_with_frozen_wall() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    prepared = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    store = MemoryStore()
    guard = FakeGuard()
    harness = RestoreHarness(
        prepared.baseline,
        store,
        clock=wall,
        monotonic_clock=monotonic,
    )
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        clock=wall,
        monotonic_clock=monotonic,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    controller.create(prepared)
    preissued = _authority(
        prepared,
        monotonic_now=monotonic.value,
        confirmation_label="preissued-before-delay",
    )
    monotonic.value += 11 * 60 * 1_000_000_000

    with pytest.raises(ExactRestorePreflightError) as expired:
        controller.arm(preissued)

    assert expired.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == prepared
    assert harness.observation_count == 0
    assert harness.writes == []


def test_preissued_reauthorization_cannot_reset_expired_monotonic_window() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, _guard, harness, armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )
    preissued = _authority(
        armed,
        monotonic_now=monotonic.value,
        confirmation_label="preissued-reauthorization-before-delay",
    )
    monotonic.value += 11 * 60 * 1_000_000_000

    with pytest.raises(ExactRestorePreflightError) as expired:
        controller.reauthorize(preissued)

    assert expired.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == armed
    assert harness.observation_count == 0
    assert harness.writes == []


@pytest.mark.asyncio
async def test_same_boot_controller_restart_uses_original_activation_deadline() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    _controller, store, _guard, harness, armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )
    original_activation = armed.authority_activation
    assert original_activation is not None
    wall.value = NOW + timedelta(minutes=2)
    monotonic.value = MONOTONIC_NS + 2 * 60 * 1_000_000_000
    restarted_guard = FakeGuard()
    restarted = ExactRestoreController(
        store,
        restarted_guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        clock=wall,
        monotonic_clock=monotonic,
        boot_identity=lambda: BOOT_A_SHA256,
    )

    completed = await restarted.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert completed.authority_activation == original_activation
    assert completed.authority_activation.deadline_monotonic_ns == (
        MONOTONIC_NS + 10 * 60 * 1_000_000_000
    )
    assert harness.write_action_indexes == list(range(6))
    assert restarted_guard.trip_count == 1


@pytest.mark.asyncio
async def test_boot_change_blocks_all_reads_and_writes_until_reauthorization() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    _controller, store, _guard, harness, armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )
    changed_boot_guard = FakeGuard()
    changed_boot = ExactRestoreController(
        store,
        changed_boot_guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        clock=wall,
        monotonic_clock=monotonic,
        boot_identity=lambda: BOOT_B_SHA256,
    )

    with pytest.raises(ExactRestorePreflightError) as wrong_boot:
        await changed_boot.execute()
    assert wrong_boot.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.observation_count == 0
    assert harness.writes == []
    assert ExactRestoreRecord.model_validate(store.load()) == armed

    renewed = changed_boot.reauthorize(
        _authority(
            armed,
            confirmation_label="changed-boot-renewal",
            boot_identity_sha256=BOOT_B_SHA256,
        )
    )
    assert renewed.authority_activation is not None
    assert renewed.authority_activation.boot_identity_sha256 == BOOT_B_SHA256
    assert renewed.prior_authority_activations == (armed.authority_activation,)

    completed = await changed_boot.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))


@pytest.mark.asyncio
async def test_missing_activation_blocks_until_durable_reauthorization() -> None:
    controller, store, _guard, harness, armed = _armed_controller()
    payload = armed.model_dump(mode="json")
    payload["authority_activation"] = None
    missing_activation = ExactRestoreRecord.model_validate(payload)
    with store.claim():
        store.save(missing_activation.model_dump(mode="json"))

    with pytest.raises(ExactRestorePreflightError) as missing:
        await controller.execute()
    assert missing.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.observation_count == 0
    assert harness.writes == []

    renewed = controller.reauthorize(
        _authority(missing_activation, confirmation_label="missing-activation-renewal")
    )
    assert renewed.authority_activation is not None
    assert renewed.prior_authority_activations == (None,)

    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))


@pytest.mark.asyncio
async def test_mid_plan_frozen_wall_deadline_stops_new_writes_until_reauthorization() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, _guard, harness, _armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )

    def exhaust_deadline_after_first_action(payload: dict[str, Any]) -> None:
        if len(payload["completed_actions"]) == 1 and payload["inflight"] is None:
            store.on_save = None
            monotonic.value = MONOTONIC_NS + 11 * 60 * 1_000_000_000

    store.on_save = exhaust_deadline_after_first_action

    with pytest.raises(ExactRestorePreflightError) as expired:
        await controller.execute()
    assert expired.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.write_action_indexes == [0]
    interrupted = ExactRestoreRecord.model_validate(store.load())
    assert interrupted.phase is ExactRestorePhase.RESTORING
    assert len(interrupted.completed_actions) == 1

    with pytest.raises(ExactRestorePreflightError):
        await controller.execute()
    assert harness.write_action_indexes == [0]

    controller.reauthorize(
        _authority(
            interrupted,
            confirmation_label="mid-plan-deadline-renewal",
            monotonic_now=monotonic.value,
        )
    )
    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == list(range(6))


@pytest.mark.asyncio
async def test_expired_crash_inflight_is_observed_only_after_reauthorization() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, guard, harness, armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )
    first = armed.actions[0]
    original_authority = armed.authority
    assert original_authority is not None
    safe = next(target for target in armed.safe_targets if target.role is first.role)
    harness.apply_target(
        first.role,
        DeviceTarget(
            enabled=True,
            power=safe.power,
            mode="constant",
            frequency=safe.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        ),
    )
    inflight = ExactRestoreInflightAction(
        index=first.index,
        action_id=first.action_id,
        target_sha256=first.target_sha256,
        pre_state_sha256=_digest("expired-inflight-pre-state"),
        authority_sha256=original_authority.authority_sha256,
        intent_at=NOW,
    )
    payload = armed.model_dump(mode="json")
    payload.update(
        phase=ExactRestorePhase.RESTORING,
        inflight=inflight,
        updated_at=NOW,
    )
    crashed = ExactRestoreRecord.model_validate(payload)
    with store.claim():
        store.save(crashed.model_dump(mode="json"))
    wall.value = NOW + timedelta(minutes=11)
    monotonic.value = MONOTONIC_NS + 11 * 60 * 1_000_000_000

    with pytest.raises(ExactRestorePreflightError) as expired:
        await controller.execute()
    assert expired.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.observation_count == 0
    assert harness.writes == []
    assert ExactRestoreRecord.model_validate(store.load()) == crashed

    resumed = controller.reauthorize(
        _authority(
            crashed,
            permit_crash_resume=True,
            now=wall.value,
            monotonic_now=monotonic.value,
        )
    )
    assert resumed.inflight == inflight
    assert resumed.prior_authorities == (original_authority,)

    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert harness.write_action_indexes == [1, 2, 3, 4, 5]
    assert completed.completed_actions[0].outcome is (
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN
    )
    assert guard.trip_count == 2


@pytest.mark.parametrize(
    ("wall_after_first", "monotonic_after_first"),
    [
        (NOW - timedelta(seconds=1), MONOTONIC_NS + 1_000_000_000),
        (NOW + timedelta(seconds=1), MONOTONIC_NS - 1),
    ],
)
@pytest.mark.asyncio
async def test_live_authority_window_rejects_clock_regression_before_next_write(
    wall_after_first: datetime,
    monotonic_after_first: int,
) -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, _guard, harness, _armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )

    def regress_after_first_action(payload: dict[str, Any]) -> None:
        if len(payload["completed_actions"]) == 1 and payload["inflight"] is None:
            store.on_save = None
            wall.value = wall_after_first
            monotonic.value = monotonic_after_first

    store.on_save = regress_after_first_action

    with pytest.raises(ExactRestorePreflightError) as regressed:
        await controller.execute()

    assert regressed.value.code is ExactRestoreErrorCode.AUTHORITY
    assert harness.write_action_indexes == [0]
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.RESTORING
    assert len(retained.completed_actions) == 1


@pytest.mark.asyncio
async def test_guard_latch_during_satisfied_read_cannot_skip_an_action() -> None:
    controller, store, guard, harness, armed = _armed_controller()
    first = armed.actions[0]
    safe = next(target for target in armed.safe_targets if target.role is first.role)
    harness.apply_target(
        first.role,
        DeviceTarget(
            enabled=True,
            power=safe.power,
            mode="constant",
            frequency=safe.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        ),
    )

    def trip_during_first_read(_role: ExactRestoreRole, count: int) -> None:
        if count == 1:
            guard.trip()

    harness.before_observe = trip_during_first_read

    with pytest.raises(ExactRestoreRecoveryRequired) as latched:
        await controller.execute()

    assert latched.value.code is ExactRestoreErrorCode.SAFETY_INTERLOCK
    assert harness.writes == []
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert retained.error_code is ExactRestoreErrorCode.SAFETY_INTERLOCK
    assert retained.completed_actions == ()
    assert guard.trip_count == 2


@pytest.mark.parametrize("offset_ns", [-100_000_000, 100_000_000])
@pytest.mark.asyncio
async def test_replayed_or_future_action_observation_stops_before_write(offset_ns: int) -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    harness.observation_wall_offset = timedelta(microseconds=offset_ns // 1_000)
    harness.observation_monotonic_offset_ns = offset_ns

    with pytest.raises(ExactRestoreRecoveryRequired) as invalid:
        await controller.execute()

    assert invalid.value.code is ExactRestoreErrorCode.DEVICE_IO
    assert harness.writes == []
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.RECOVERY_REQUIRED
    assert retained.error_code is ExactRestoreErrorCode.DEVICE_IO


@pytest.mark.asyncio
async def test_final_pair_is_persisted_only_after_the_operation_guard_is_released() -> None:
    controller, store, guard, _harness, _armed = _armed_controller()
    phases_at_release: list[str] = []

    def contender_at_release() -> None:
        assert guard.lease_active
        assert store.record is not None
        phases_at_release.append(store.record["phase"])
        with pytest.raises(AssertionError, match="not reentrant"):
            with guard.lease():
                pass

    guard.on_before_release = contender_at_release

    completed = await controller.execute()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert phases_at_release == [ExactRestorePhase.AWAITING_FINAL_VERIFY.value]
    assert ExactRestoreRecord.model_validate(store.load()).phase is ExactRestorePhase.FINAL_VERIFIED
    assert guard.trip_count == 1


@pytest.mark.asyncio
async def test_failed_guard_release_cannot_persist_final_verified_or_mint_receipt() -> None:
    controller, store, guard, _harness, _armed = _armed_controller()

    def fail_release() -> None:
        raise RuntimeError("simulated lease-integrity failure")

    guard.on_before_release = fail_release

    with pytest.raises(RuntimeError, match="lease-integrity"):
        await controller.execute()

    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY
    assert retained.final_evidence is None
    with pytest.raises(RuntimeError, match="lease-integrity"):
        await controller.finalize()
    retained_again = ExactRestoreRecord.model_validate(store.load())
    assert retained_again.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY
    assert retained_again.final_evidence is None


@pytest.mark.asyncio
async def test_final_pair_outside_manifest_window_cannot_mint_a_receipt() -> None:
    wall = MutableClock()
    monotonic = MutableMonotonicClock()
    controller, store, guard, harness, _armed = _armed_controller(
        clock=wall,
        monotonic_clock=monotonic,
    )

    def separate_final_pair(role: ExactRestoreRole, _count: int) -> None:
        assert store.record is not None
        if (
            store.record["phase"] == ExactRestorePhase.AWAITING_FINAL_VERIFY.value
            and role is ExactRestoreRole.SLAVE
        ):
            wall.value = NOW + timedelta(seconds=21)
            monotonic.value = MONOTONIC_NS + 21 * 1_000_000_000

    harness.before_observe = separate_final_pair

    with pytest.raises(ExactRestoreRecoveryRequired) as separated:
        await controller.execute()

    assert separated.value.code is ExactRestoreErrorCode.VERIFY_MISMATCH
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY
    assert retained.final_evidence is None
    assert guard.trip_count == 1


@pytest.mark.parametrize("offset_ns", [-100_000_000, 100_000_000])
@pytest.mark.asyncio
async def test_final_reader_rejects_replayed_or_future_frame(offset_ns: int) -> None:
    controller, store, _guard, harness, _armed = _armed_controller()

    def offset_final_frame(_role: ExactRestoreRole, _count: int) -> None:
        assert store.record is not None
        if store.record["phase"] == ExactRestorePhase.AWAITING_FINAL_VERIFY.value:
            harness.observation_wall_offset = timedelta(microseconds=offset_ns // 1_000)
            harness.observation_monotonic_offset_ns = offset_ns

    harness.before_observe = offset_final_frame

    with pytest.raises(ExactRestoreRecoveryRequired) as invalid:
        await controller.execute()

    assert invalid.value.code is ExactRestoreErrorCode.DEVICE_IO
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY
    assert retained.final_evidence is None


@pytest.mark.asyncio
async def test_interrupted_final_capture_requires_guarded_internal_reads_and_exact_receipt() -> (
    None
):
    controller, store, guard, harness, _armed = _armed_controller()

    def interrupt_final_capture(_role: ExactRestoreRole, _count: int) -> None:
        assert store.record is not None
        if store.record["phase"] == ExactRestorePhase.AWAITING_FINAL_VERIFY.value:
            raise OSError("simulated collector crash before final evidence")

    harness.before_observe = interrupt_final_capture
    with pytest.raises(OSError, match="collector crash"):
        await controller.execute()
    awaiting = ExactRestoreRecord.model_validate(store.load())
    assert awaiting.phase is ExactRestorePhase.AWAITING_FINAL_VERIFY
    assert awaiting.final_evidence is None
    authority = awaiting.authority
    assert authority is not None

    forged = ExactRestoreReceipt(
        operation_id=awaiting.operation_id,
        cycle=awaiting.cycle,
        baseline_sha256=awaiting.baseline_sha256,
        action_plan_sha256=awaiting.action_plan_sha256,
        authority_sha256=authority.authority_sha256,
        authority_chain_sha256=awaiting.authority_chain_sha256,
        qualification_receipt_sha256=awaiting.qualification_receipt_sha256,
        completed_action_count=len(awaiting.actions),
        final_raw_frame_sha256=(
            _digest("forged-master-final"),
            _digest("forged-slave-final"),
        ),
        completed_at=NOW,
    )
    with pytest.raises(ExactRestorePreflightError) as premature_clear:
        controller.clear_after_receipt(forged)
    assert premature_clear.value.code is ExactRestoreErrorCode.JOURNAL
    assert store.load() is not None

    with pytest.raises(TypeError):
        await controller.finalize((object(), object()))  # type: ignore[call-arg]
    assert ExactRestoreRecord.model_validate(store.load()).phase is (
        ExactRestorePhase.AWAITING_FINAL_VERIFY
    )

    harness.before_observe = None
    lease_count_before_resume = guard.lease_count
    receipt = await controller.finalize()
    finalized = ExactRestoreRecord.model_validate(store.load())
    assert guard.lease_count == lease_count_before_resume + 1
    assert finalized.phase is ExactRestorePhase.FINAL_VERIFIED
    assert finalized.final_evidence is not None
    assert finalized.final_evidence.receipt_sha256 == receipt.receipt_sha256

    forged_payload = receipt.model_dump(mode="json")
    forged_payload["final_raw_frame_sha256"] = [
        _digest("different-master-final"),
        _digest("different-slave-final"),
    ]
    forged_after_finalize = ExactRestoreReceipt.model_validate(forged_payload)
    with pytest.raises(ExactRestorePreflightError) as forged_clear:
        controller.clear_after_receipt(forged_after_finalize)
    assert forged_clear.value.code is ExactRestoreErrorCode.JOURNAL
    assert store.load() is not None

    assert await controller.finalize() == receipt
    controller.clear_after_receipt(receipt)
    assert store.load() is None


@pytest.mark.asyncio
async def test_exact_completion_yields_receipt_then_clears_only_after_receipt() -> None:
    controller, store, _guard, harness, _armed = _armed_controller()
    completed = await controller.execute()
    receipt = await controller.finalize()
    finalized = ExactRestoreRecord.model_validate(store.load())
    assert finalized.final_evidence is not None
    master_final, slave_final = finalized.final_evidence.observations

    assert store.load() is not None
    assert receipt.outcome == "exact_restored"
    assert receipt.cycle is ExactRestoreCycle.BASELINE_RESTORE
    assert receipt.baseline_sha256 == completed.baseline_sha256
    assert receipt.action_plan_sha256 == completed.action_plan_sha256
    assert receipt.qualification_receipt_sha256 == completed.qualification_receipt_sha256
    assert receipt.completed_action_count == 6
    assert receipt.final_raw_frame_sha256 == (
        master_final.raw_frame_sha256,
        slave_final.raw_frame_sha256,
    )
    assert len(receipt.receipt_sha256) == 64
    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert finalized.final_evidence.receipt_sha256 == receipt.receipt_sha256

    controller.clear_after_receipt(receipt)
    receipt_store = controller._qualification_receipts  # noqa: SLF001 - verify durable boundary
    assert isinstance(receipt_store, MemoryQualificationReceiptStore)
    assert receipt_store.load_final_verified_receipt(receipt.receipt_sha256) == (
        receipt.model_dump(mode="json")
    )
    qualification = finalized.qualification_final_record
    assert qualification is not None
    assert qualification.final_evidence is not None
    archived_qualification = receipt_store.load_final_verified_receipt(
        qualification.final_evidence.receipt_sha256
    )
    assert archived_qualification is not None
    assert archived_qualification["cycle"] == ExactRestoreCycle.SENTINEL_QUALIFICATION.value
    assert store.load() is None


@pytest.mark.asyncio
async def test_receipt_archive_failure_preserves_final_verified_journal() -> None:
    controller, store, _guard, _harness, _armed = _armed_controller()
    await controller.execute()
    receipt = await controller.finalize()
    receipt_store = controller._qualification_receipts  # noqa: SLF001 - fault injection seam
    assert isinstance(receipt_store, MemoryQualificationReceiptStore)
    receipt_store.fail_persist = True

    with pytest.raises(ExactRestorePreflightError) as archive_failed:
        controller.clear_after_receipt(receipt)

    assert archive_failed.value.code is ExactRestoreErrorCode.JOURNAL
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.FINAL_VERIFIED
    assert retained.final_evidence is not None
    assert retained.final_evidence.receipt_sha256 == receipt.receipt_sha256


@pytest.mark.asyncio
async def test_operation_finalization_failure_preserves_final_verified_journal() -> None:
    controller, store, _guard, _harness, _armed = _armed_controller()
    await controller.execute()
    receipt = await controller.finalize()
    receipt_store = controller._qualification_receipts  # noqa: SLF001 - fault injection seam
    assert isinstance(receipt_store, MemoryQualificationReceiptStore)
    receipt_store.fail_finalization_confirm = True

    with pytest.raises(ExactRestorePreflightError) as archive_failed:
        controller.clear_after_receipt(receipt)

    assert archive_failed.value.code is ExactRestoreErrorCode.JOURNAL
    retained = ExactRestoreRecord.model_validate(store.load())
    assert retained.phase is ExactRestorePhase.FINAL_VERIFIED
    assert retained.final_evidence is not None
    assert retained.final_evidence.receipt_sha256 == receipt.receipt_sha256


@pytest.mark.asyncio
async def test_cleared_operation_cannot_reuse_the_same_live_authority_or_write_plan() -> None:
    controller, store, _guard, harness, armed = _armed_controller(
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION
    )
    authority = armed.authority
    assert authority is not None
    await controller.execute()
    receipt = await controller.finalize()
    controller.clear_after_receipt(receipt)
    first_write_count = len(harness.writes)
    assert first_write_count == 8

    replayed = prepare_exact_restore_record(
        armed.baseline,
        armed.safe_targets,
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        operation_id=armed.operation_id,
        now=NOW,
    )
    controller.create(replayed)

    with pytest.raises(ExactRestorePreflightError) as replay:
        controller.arm(authority)

    assert replay.value.code is ExactRestoreErrorCode.AUTHORITY
    assert ExactRestoreRecord.model_validate(store.load()) == replayed
    assert len(harness.writes) == first_write_count


def _promotion_controller(
    qualification: ExactRestoreRecord | None = None,
) -> tuple[ExactRestoreController, MemoryStore, RestoreHarness, ExactRestoreRecord]:
    finalized = qualification or _sentinel_final_record()
    store = MemoryStore()
    store.record = finalized.model_dump(mode="json")
    guard = FakeGuard()
    qualification_store = MemoryQualificationReceiptStore()
    harness = RestoreHarness(finalized.baseline, store)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        qualification_receipts=qualification_store,
        clock=lambda: NOW,
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )
    return controller, store, harness, finalized


def test_final_sentinel_promotes_to_exact_prepared_baseline_in_one_claim() -> None:
    controller, store, _harness, qualification = _promotion_controller()
    claims_before = store.claim_count
    loads_before = store.load_count

    promoted = controller.promote_to_baseline_restore(operation_id="promoted-operation")

    assert store.claim_count == claims_before + 1
    assert store.load_count == loads_before + 1
    assert store.confirm_count == 1
    assert store.create_count == 0
    assert store.clear_count == 0
    assert len(store.saved) == 1
    assert ExactRestoreRecord.model_validate(store.load()) == promoted
    assert promoted.operation_id == "promoted-operation"
    assert promoted.cycle is ExactRestoreCycle.BASELINE_RESTORE
    assert promoted.phase is ExactRestorePhase.PREPARED
    assert promoted.baseline == qualification.baseline
    assert promoted.safe_targets == qualification.safe_targets
    assert promoted.qualification_final_record == qualification
    assert qualification.final_evidence is not None
    assert promoted.qualification_receipt_sha256 == qualification.final_evidence.receipt_sha256
    assert promoted.authority is None
    assert promoted.authority_activation is None
    assert promoted.prior_authorities == ()
    assert promoted.prior_authority_activations == ()
    assert promoted.completed_actions == ()
    assert promoted.inflight is None
    assert promoted.final_evidence is None
    assert promoted.error_code is None


def test_promotion_api_accepts_only_a_new_operation_id() -> None:
    parameters = inspect.signature(ExactRestoreController.promote_to_baseline_restore).parameters
    assert tuple(parameters) == ("self", "operation_id")
    assert parameters["operation_id"].kind is inspect.Parameter.KEYWORD_ONLY

    controller, store, _harness, qualification = _promotion_controller()
    with pytest.raises(TypeError):
        controller.promote_to_baseline_restore(  # type: ignore[call-arg]
            operation_id="forged-input",
            baseline=_baseline(master_manual_power=40),
        )
    assert ExactRestoreRecord.model_validate(store.load()) == qualification


def test_promotion_rejects_same_operation_id_without_replacing_final_sentinel() -> None:
    controller, store, _harness, qualification = _promotion_controller()

    with pytest.raises(ExactRestorePreflightError) as duplicate:
        controller.promote_to_baseline_restore(operation_id=qualification.operation_id)

    assert duplicate.value.code is ExactRestoreErrorCode.JOURNAL
    assert ExactRestoreRecord.model_validate(store.load()) == qualification
    assert store.saved == []


def test_promotion_rejects_nonfinal_or_already_promoted_journal_without_replacement() -> None:
    prepared_sentinel = prepare_exact_restore_record(
        _baseline(),
        _safe_targets(),
        operation_id="not-final-sentinel",
        now=NOW,
    )
    controller, store, _harness, _qualification = _promotion_controller(prepared_sentinel)

    with pytest.raises(ExactRestorePreflightError) as not_final:
        controller.promote_to_baseline_restore(operation_id="must-not-replace")
    assert not_final.value.code is ExactRestoreErrorCode.JOURNAL
    assert ExactRestoreRecord.model_validate(store.load()) == prepared_sentinel

    promoted = _promoted_record(operation_id="already-promoted")
    store.record = promoted.model_dump(mode="json")
    with pytest.raises(ExactRestorePreflightError) as second_promotion:
        controller.promote_to_baseline_restore(operation_id="must-not-repromote")
    assert second_promotion.value.code is ExactRestoreErrorCode.JOURNAL
    assert ExactRestoreRecord.model_validate(store.load()) == promoted


def test_promotion_crash_before_atomic_replace_preserves_final_sentinel() -> None:
    controller, store, _harness, qualification = _promotion_controller()
    store.fail_before_save = True

    with pytest.raises(ExactRestorePreflightError) as crashed:
        controller.promote_to_baseline_restore(operation_id="crash-before-replace")

    assert crashed.value.code is ExactRestoreErrorCode.JOURNAL
    assert ExactRestoreRecord.model_validate(store.load()) == qualification
    assert store.clear_count == 0
    assert store.create_count == 0
    assert store.confirm_count == 1


def test_promotion_late_fsync_error_accepts_only_the_exact_reloaded_successor() -> None:
    controller, store, _harness, qualification = _promotion_controller()
    raised = False

    def raise_after_replace(payload: dict[str, Any]) -> None:
        nonlocal raised
        if not raised and payload["cycle"] == ExactRestoreCycle.BASELINE_RESTORE.value:
            raised = True
            raise OSError("simulated late fsync acknowledgement loss")

    store.on_save = raise_after_replace
    promoted = controller.promote_to_baseline_restore(operation_id="late-fsync-successor")

    assert raised is True
    assert promoted.qualification_final_record == qualification
    assert ExactRestoreRecord.model_validate(store.load()) == promoted
    assert store.clear_count == 0
    assert store.create_count == 0
    assert store.confirm_count == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_provenance",
        "receipt_digest",
        "provenance_final_digest",
        "safe_target",
        "authority",
        "progress",
        "inflight",
    ],
)
def test_promoted_record_model_rejects_forged_provenance_or_prepared_progress(
    mutation: str,
) -> None:
    promoted = _promoted_record()
    payload = promoted.model_dump(mode="json")
    if mutation == "missing_provenance":
        payload["qualification_final_record"] = None
    elif mutation == "receipt_digest":
        payload["qualification_receipt_sha256"] = _digest("forged-receipt")
    elif mutation == "provenance_final_digest":
        payload["qualification_final_record"]["final_evidence"]["receipt_sha256"] = _digest(
            "forged-final-evidence"
        )
    elif mutation == "safe_target":
        payload["safe_targets"][0]["power"] = 40
    elif mutation == "authority":
        payload["authority"] = _authority(promoted).model_dump(mode="json")
    elif mutation == "progress":
        qualification = promoted.qualification_final_record
        assert qualification is not None
        payload["completed_actions"] = [qualification.completed_actions[0].model_dump(mode="json")]
    elif mutation == "inflight":
        authority = _authority(promoted)
        payload["authority"] = authority.model_dump(mode="json")
        payload["authority_activation"] = ExactRestoreAuthorityActivation(
            authority_sha256=authority.authority_sha256,
            boot_identity_sha256=authority.boot_identity_sha256,
            accepted_wall=authority.issued_at,
            accepted_monotonic_ns=authority.issued_monotonic_ns,
            deadline_monotonic_ns=authority.deadline_monotonic_ns,
        ).model_dump(mode="json")
        payload["inflight"] = ExactRestoreInflightAction(
            index=0,
            action_id=promoted.actions[0].action_id,
            target_sha256=promoted.actions[0].target_sha256,
            pre_state_sha256=_digest("forged-pre-state"),
            authority_sha256=authority.authority_sha256,
            intent_at=NOW,
        ).model_dump(mode="json")

    with pytest.raises(ValueError):
        ExactRestoreRecord.model_validate(payload)


def test_promoted_record_rejects_complete_but_cross_baseline_or_cross_target_provenance() -> None:
    promoted = _promoted_record()

    cross_baseline = _sentinel_final_record(
        baseline=_baseline(master_manual_power=40),
        operation_id="cross-baseline-qualification",
    )
    payload = promoted.model_dump(mode="json")
    payload["qualification_final_record"] = cross_baseline.model_dump(mode="json")
    assert cross_baseline.final_evidence is not None
    payload["qualification_receipt_sha256"] = cross_baseline.final_evidence.receipt_sha256
    with pytest.raises(ValueError, match="exact finalized restore"):
        ExactRestoreRecord.model_validate(payload)

    alternate_targets = (
        SafeManualTarget(role=ExactRestoreRole.MASTER, power=40, frequency=10),
        SafeManualTarget(role=ExactRestoreRole.SLAVE, power=40, frequency=15),
    )
    cross_targets = _sentinel_final_record(
        safe_targets=alternate_targets,
        operation_id="cross-target-qualification",
    )
    payload = promoted.model_dump(mode="json")
    payload["qualification_final_record"] = cross_targets.model_dump(mode="json")
    assert cross_targets.final_evidence is not None
    payload["qualification_receipt_sha256"] = cross_targets.final_evidence.receipt_sha256
    with pytest.raises(ValueError, match="exact finalized restore"):
        ExactRestoreRecord.model_validate(payload)


def test_baseline_arm_uses_embedded_provenance_and_rejects_external_receipt() -> None:
    controller, store, _harness, _qualification = _promotion_controller()
    promoted = controller.promote_to_baseline_restore(operation_id="arm-from-provenance")
    authority = _authority(promoted)

    with pytest.raises(TypeError):
        controller.arm(  # type: ignore[call-arg]
            authority,
            qualification_receipt=ExactRestoreReceipt,
        )
    armed = controller.arm(authority)

    assert armed.phase is ExactRestorePhase.ARMED
    assert armed.qualification_final_record == promoted.qualification_final_record
    assert armed.qualification_receipt_sha256 == promoted.qualification_receipt_sha256
    assert ExactRestoreRecord.model_validate(store.load()) == armed


def test_create_rejects_a_baseline_record_even_with_valid_embedded_provenance() -> None:
    prepared = _promoted_record()
    store = MemoryStore()
    guard = FakeGuard()
    harness = RestoreHarness(prepared.baseline, store)
    controller = ExactRestoreController(
        store,
        guard,
        observe=harness.observe,
        resolve_device=harness.resolve_device,
        clock=lambda: NOW,
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )

    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.create(prepared)

    assert rejected.value.code is ExactRestoreErrorCode.INVALID_BASELINE
    assert store.load() is None


@pytest.mark.asyncio
async def test_qualified_baseline_can_stage_and_complete_one_distinct_phase5_restore() -> None:
    qualification_controller, _store, _guard, _harness, _armed = _armed_controller()
    qualified = await qualification_controller.execute()
    qualification_receipt = await qualification_controller.finalize()
    staged = prepare_qualified_final_restore_record(
        qualified,
        operation_id="phase5-final-restore",
        now=NOW + timedelta(seconds=1),
    )

    final_store = MemoryStore()
    final_guard = FakeGuard()
    final_harness = RestoreHarness(
        staged.baseline,
        final_store,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    final_controller = ExactRestoreController(
        final_store,
        final_guard,
        observe=final_harness.observe,
        resolve_device=final_harness.resolve_device,
        qualification_receipts=MemoryQualificationReceiptStore(),
        clock=lambda: NOW + timedelta(seconds=1),
        monotonic_clock=lambda: MONOTONIC_NS,
        boot_identity=lambda: BOOT_A_SHA256,
    )

    final_controller.create_qualified_final_restore(staged)
    final_controller.arm(_authority(staged, now=NOW + timedelta(seconds=1)))
    completed = await final_controller.execute()
    final_receipt = await final_controller.finalize()

    assert completed.phase is ExactRestorePhase.FINAL_VERIFIED
    assert completed.qualification_final_record == qualified
    assert completed.qualification_receipt_sha256 == qualification_receipt.receipt_sha256
    assert final_receipt.qualification_receipt_sha256 == qualification_receipt.receipt_sha256
    assert len(final_harness.writes) == 6


def test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = inspect.signature(ExactRestoreController.__init__).parameters
    assert parameters["boot_identity"].default is system_boot_identity_sha256
    assert parameters["monotonic_clock"].default is system_boottime_ns
    assert len(system_boot_identity_sha256()) == 64
    assert system_boottime_ns() >= 0

    monkeypatch.delattr(time, "CLOCK_BOOTTIME")
    with pytest.raises(RuntimeError, match="suspend-inclusive"):
        system_boottime_ns()


def test_importing_exact_restore_does_not_load_frozen_async_harness_modules() -> None:
    repository = Path(__file__).parents[2]
    source_root = str(repository / "src")
    frozen = [
        "jebao_flow.devices.linkage",
        "jebao_flow.devices.schedule_flow_experiment",
        "jebao_flow.devices.schedule_linkage",
        "jebao_flow.devices.schedule_transaction",
    ]
    script = f"""
import json
import sys
sys.path.insert(0, {source_root!r})
import jebao_flow.exact_restore
frozen = {frozen!r}
print(json.dumps(sorted(name for name in frozen if name in sys.modules)))
"""

    result = subprocess.run(
        [sys.executable, "-P", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []
