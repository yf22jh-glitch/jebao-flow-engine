"""Attended one-shot CLI for the native async-slave schedule-flow experiment.

The command deliberately reuses the native-linkage intent and outer journal.  A preflight is
read-only; a run is authorized by one JFE token bound to the full experiment, both control
snapshots, and SHA-256 digests of both exact 48-slot images.  Recovery always delegates to the
composed controller so roles, TimerOFF, schedule bytes, and original controls unwind in order.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import signal
import sys
from collections.abc import Awaitable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jebao_flow.config import AppConfig, load_config
from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.linkage import (
    DeviceControlSnapshot,
    LinkageDiagnosticEvent,
    LinkageDiagnosticEventKind,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
)
from jebao_flow.devices.schedule_flow_experiment import (
    SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT,
    SCHEDULE_FLOW_STAGE_EVENT_LIMIT,
    ScheduleFlowExperimentController,
    ScheduleFlowExperimentSpec,
    ScheduleFlowOutcome,
    ScheduleFlowStage,
    ScheduleFlowStageEvent,
    classify_schedule_flow_sample,
    schedule_flow_stage_rank,
)
from jebao_flow.devices.schedule_linkage import (
    ScheduleLinkageRecord,
    ScheduleLinkageSample,
    ScheduleLinkageSnapshot,
    ScheduleLinkageSpec,
)
from jebao_flow.devices.schedule_transaction import (
    ScheduleImageSnapshot,
    TemporaryScheduleKind,
    TemporaryScheduleRecord,
    TemporaryScheduleSpec,
)
from jebao_flow.hardware_guard import (
    DeploymentHardwareGuard,
    HardwareOperationLockError,
)
from jebao_flow.hardware_safety import (
    emergency_stop_latch_path,
    native_linkage_intent_path,
    native_linkage_journal_path,
    qualification_directory,
    schedule_linkage_journal_path,
    temporary_schedule_journal_path,
    validate_hardware_safety_root,
)
from jebao_flow.hardware_test import (
    TERMINAL_SCHEDULE_FLOW_OUTCOMES,
    ConfirmationMismatchError,
    ConfirmingLinkageJournalStore,
    HardwareTestError,
    HardwareTestEvidence,
    HardwareTestIntent,
    HardwareTestIntentPhase,
    HardwareTestScheduleImageDigest,
    JsonHardwareTestIntentStore,
    PhysicalDeviceLease,
    _assert_no_verification_conflict,
    _build_devices,
    _capture_preview,
    _connected,
    _evidence_after_event,
    _evidence_with_rollback_completed,
    _evidence_with_rollback_failures,
    _safety_latch_present,
    _updated_intent,
    _validate_config,
    hardware_test_intent_confirmation_token,
    schedule_flow_confirmation_token,
)
from jebao_flow.logging import configure_logging
from jebao_flow.persistence import (
    JsonLinkageJournalStore,
    JsonQualificationStore,
    JsonScheduleLinkageJournalStore,
    JsonTemporaryScheduleJournalStore,
)

_TOKEN_VERSION = 1
_MAX_POWER = 45
_BOUNDARY_MIN_LEAD_SECONDS = 180
_BOUNDARY_MAX_LEAD_SECONDS = 240
_RUN_MIN_LEAD_SECONDS = 120
_LEGACY_TERMINAL_OUTCOMES = frozenset(
    {
        "armed_preview_cancelled",
        "crashed_before_first_write",
        "recovered",
        "restored",
        "stopped_before_first_write",
    }
)


class ScheduleFlowCliError(HardwareTestError):
    """Sanitized, fail-closed refusal from the attended schedule-flow CLI."""


def _fixed_spec(
    *,
    operation_id: str,
    qualification_operation_id: str,
    master_device_id: str,
    slave_device_id: str,
    boundary_time: str,
) -> ScheduleFlowExperimentSpec:
    """Return the only field-test plan currently audited for physical execution."""

    spec = ScheduleFlowExperimentSpec(
        operation_id=operation_id,
        qualification_operation_id=qualification_operation_id,
        master_device_id=master_device_id,
        slave_device_id=slave_device_id,
        boundary_time=boundary_time,
        master_before_flow=31,
        slave_before_flow=32,
        master_after_flow=35,
        slave_after_flow=40,
        sine_frequency=30,
        safe_frequency=20,
        observation_window_seconds=600,
        post_boundary_stability_seconds=300,
        verification_interval_seconds=2,
        minimum_lead_seconds=60,
        ambiguous_band_seconds=1,
        maximum_clock_skew_seconds=2,
        clock_advance_tolerance_seconds=2,
        sentinel_qualification=True,
    )
    requested = (
        spec.master_before_flow,
        spec.slave_before_flow,
        spec.master_after_flow,
        spec.slave_after_flow,
    )
    if max(requested) > _MAX_POWER:
        raise ScheduleFlowCliError("schedule-flow targets exceed the attended 45% cap")
    return spec


def _next_boundary(clocks: Sequence[datetime]) -> str:
    """Choose the next minute boundary 3-4 minutes after the freshest device clock."""

    if len(clocks) != 2 or any(clock.tzinfo is not None for clock in clocks):
        raise ScheduleFlowCliError("both device-local clocks must be available and timezone-naive")
    earliest, latest = min(clocks), max(clocks)
    if (latest - earliest).total_seconds() > 2:
        raise ScheduleFlowCliError("device-local clocks exceed the audited two-second skew")
    boundary = latest.replace(second=0, microsecond=0) + timedelta(minutes=4)
    lead = (boundary - latest).total_seconds()
    if not _BOUNDARY_MIN_LEAD_SECONDS <= lead <= _BOUNDARY_MAX_LEAD_SECONDS:
        raise ScheduleFlowCliError("cannot choose a safe three-to-four-minute boundary")
    if boundary.date() != latest.date() or (boundary.hour == 0 and boundary.minute == 0):
        raise ScheduleFlowCliError("schedule-flow preflight cannot cross midnight")
    return boundary.strftime("%H:%M")


def _require_boundary_still_fresh(
    boundary_time: str,
    clocks: Sequence[datetime],
) -> None:
    if len(clocks) != 2:
        raise ScheduleFlowCliError("both fresh device clocks are required before execution")
    for clock in clocks:
        hour, minute = (int(part) for part in boundary_time.split(":"))
        boundary = clock.replace(hour=hour, minute=minute, second=0, microsecond=0)
        lead = (boundary - clock).total_seconds()
        if not _RUN_MIN_LEAD_SECONDS <= lead <= _BOUNDARY_MAX_LEAD_SECONDS:
            raise ScheduleFlowCliError(
                "confirmed boundary is no longer within the audited execution lead window"
            )


def _require_plan_supported(
    devices: Mapping[str, JebaoDevice],
    spec: ScheduleFlowExperimentSpec,
) -> None:
    requested = {
        spec.master_device_id: (spec.master_before_flow, spec.master_after_flow),
        spec.slave_device_id: (spec.slave_before_flow, spec.slave_after_flow),
    }
    for device_id, flows in requested.items():
        capabilities = devices[device_id].capabilities
        limits = capabilities.power_limits
        step = capabilities.power_step
        if any(
            flow < limits.min_power or flow > limits.max_power or flow % step
            for flow in flows
        ):
            raise ScheduleFlowCliError(
                "the fixed schedule-flow plan is outside a selected controller's limits"
            )


def _schedule_digest(
    device_id: str,
    device: JebaoDevice,
    image: bytes,
) -> HardwareTestScheduleImageDigest:
    binding = device.physical_binding
    if binding is None:
        raise ScheduleFlowCliError("a selected controller has no stable physical binding")
    snapshot = ScheduleImageSnapshot.from_image(
        device_id=device_id,
        physical_binding=binding,
        image=image,
    )
    return HardwareTestScheduleImageDigest(
        device_id=device_id,
        physical_binding=binding,
        image_sha256=snapshot.image_sha256,
    )


async def _capture_schedule_context(
    devices: Mapping[str, JebaoDevice],
    device_ids: Sequence[str],
) -> tuple[tuple[HardwareTestScheduleImageDigest, ...], tuple[datetime, ...]]:
    digests: list[HardwareTestScheduleImageDigest] = []
    clocks: list[datetime] = []
    for device_id in device_ids:
        device = devices[device_id]
        state = await device.get_state()
        schedule = state.schedule
        if (
            not state.online
            or state.error is not None
            or state.timer_enabled is not True
            or schedule is None
            or schedule.enabled is not True
            or schedule.device_local_time is None
        ):
            raise ScheduleFlowCliError(
                "both controllers require fresh TimerON schedule state and device clocks"
            )
        image = await device.read_schedule_image()
        digests.append(_schedule_digest(device_id, device, image))
        clocks.append(schedule.device_local_time)
    return tuple(digests), tuple(clocks)


def _require_receipts(
    store: JsonQualificationStore,
    qualification_operation_id: str,
    snapshots: Sequence[DeviceControlSnapshot],
) -> None:
    now = datetime.now(UTC)
    for snapshot in snapshots:
        receipt = store.load(snapshot.physical_binding)
        if (
            receipt is None
            or receipt.device_id != snapshot.device_id
            or receipt.operation_id != qualification_operation_id
            or not receipt.is_valid_for(snapshot.physical_binding, now=now)
        ):
            raise ScheduleFlowCliError(
                "both exact controllers require current receipts from the named qualification"
            )


def _qualification_authorizer(
    store: JsonQualificationStore,
):
    def authorize(
        spec: ScheduleLinkageSpec,
        snapshots: tuple[ScheduleLinkageSnapshot, ...],
    ) -> None:
        now = datetime.now(UTC)
        for snapshot in snapshots:
            receipt = store.load(snapshot.physical_binding)
            if (
                receipt is None
                or receipt.device_id != snapshot.device_id
                or receipt.operation_id != spec.qualification_operation_id
                or not receipt.is_valid_for(snapshot.physical_binding, now=now)
            ):
                raise ScheduleFlowCliError(
                    "schedule role activation requires both current qualification receipts"
                )

    return authorize


def _assert_intent_authentic(intent: HardwareTestIntent, instance_id: str) -> None:
    if intent.version != 3 or intent.schedule_flow_spec is None:
        raise ScheduleFlowCliError("native intent is not a schedule-flow preflight")
    if intent.instance_id != instance_id:
        raise ScheduleFlowCliError("schedule-flow intent belongs to another instance")
    expected = hardware_test_intent_confirmation_token(intent)
    if not hmac.compare_digest(expected, intent.confirmation_token):
        raise ScheduleFlowCliError("schedule-flow intent confirmation is invalid")
    if (
        intent.phase is HardwareTestIntentPhase.TERMINAL
        and intent.outcome not in TERMINAL_SCHEDULE_FLOW_OUTCOMES
    ):
        raise ScheduleFlowCliError("terminal schedule-flow intent has an invalid outcome")


def _assert_existing_allows_preflight(
    existing: HardwareTestIntent | None,
    *,
    instance_id: str,
    operation_id: str,
) -> None:
    if existing is None:
        return
    if existing.phase is not HardwareTestIntentPhase.TERMINAL:
        raise ScheduleFlowCliError("an unfinished native one-shot intent already exists")
    if existing.instance_id != instance_id:
        raise ScheduleFlowCliError("the terminal native intent belongs to another instance")
    if not hmac.compare_digest(
        existing.confirmation_token,
        hardware_test_intent_confirmation_token(existing),
    ):
        raise ScheduleFlowCliError("the terminal native intent is not authentic")
    if existing.version < 3 and existing.outcome not in _LEGACY_TERMINAL_OUTCOMES:
        raise ScheduleFlowCliError("the terminal native intent has an invalid outcome")
    if existing.version == 3 and existing.outcome not in TERMINAL_SCHEDULE_FLOW_OUTCOMES:
        raise ScheduleFlowCliError("the terminal schedule-flow intent has an invalid outcome")
    if (
        existing.created_at.tzinfo is None
        or existing.created_at.utcoffset() is None
        or existing.updated_at.tzinfo is None
        or existing.updated_at.utcoffset() is None
        or existing.updated_at < existing.created_at
    ):
        raise ScheduleFlowCliError("the terminal native intent has invalid timestamps")
    if existing.operation_id == operation_id:
        raise ScheduleFlowCliError("terminal operation IDs cannot be replayed; choose a new ID")


def _persist_successor(
    store: JsonHardwareTestIntentStore,
    current: HardwareTestIntent,
    successor: HardwareTestIntent,
) -> HardwareTestIntent:
    try:
        store.save(successor)
    except BaseException:
        if store.load() == successor:
            return successor
        raise
    return successor


def _append_schedule_stage_event(
    events: tuple[ScheduleFlowStageEvent, ...],
    event: ScheduleFlowStageEvent,
) -> tuple[ScheduleFlowStageEvent, ...]:
    """Append one bounded monotonic event before the atomic/fsynced intent save."""

    terminal_event = (
        event.stage is ScheduleFlowStage.OUTER_RESTORED
        and event.temporary_error_code is None
        and event.failure_category is None
    )
    if (
        terminal_event
        and events
        and events[-1].stage is ScheduleFlowStage.OUTER_RESTORED
        and events[-1].temporary_error_code is None
        and events[-1].failure_category is None
    ):
        # A failed journal clear can replay the before-clear hook. Coalesce that one
        # identity-free terminal fact instead of consuming its reserved slot twice.
        return events
    limit = (
        SCHEDULE_FLOW_STAGE_EVENT_LIMIT
        if terminal_event
        else SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT
    )
    if len(events) >= limit:
        raise ScheduleFlowCliError("schedule-flow stage evidence is full")
    if events:
        previous = events[-1]
        previous_rank = schedule_flow_stage_rank(previous.stage)
        current_rank = schedule_flow_stage_rank(event.stage)
        if event.occurred_at < previous.occurred_at or current_rank < previous_rank:
            raise ScheduleFlowCliError("schedule-flow stage evidence regressed")
        if (
            current_rank == previous_rank
            and event.completed_participants is not None
            and previous.completed_participants is not None
            and event.completed_participants < previous.completed_participants
        ):
            raise ScheduleFlowCliError("schedule-flow participant evidence regressed")
    return (*events, event)


async def _preflight(
    config: AppConfig,
    args: argparse.Namespace,
    intent_store: JsonHardwareTestIntentStore,
    outer_store: JsonLinkageJournalStore,
    qualification_store: JsonQualificationStore,
) -> int:
    _assert_no_verification_conflict()
    selected = _validate_config(config, frozenset({args.master, args.slave}))
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        if _safety_latch_present(emergency_stop_latch_path()):
            raise ScheduleFlowCliError("persistent safety latch is active")
        if outer_store.load() is not None:
            raise ScheduleFlowCliError("unfinished schedule-flow recovery exists")
        existing = intent_store.load()
        _assert_existing_allows_preflight(
            existing,
            instance_id=config.instance.id,
            operation_id=args.operation_id,
        )
        devices = await _build_devices(config, selected, writable=False)
        async with _connected(devices):
            provisional = _fixed_spec(
                operation_id=args.operation_id,
                qualification_operation_id=args.qualification_operation_id,
                master_device_id=args.master,
                slave_device_id=args.slave,
                boundary_time="12:00",
            )
            _require_plan_supported(devices, provisional)
            snapshots = await _capture_preview(devices, provisional.outer_linkage_spec())
            digests, clocks = await _capture_schedule_context(
                devices,
                (args.master, args.slave),
            )
        _require_receipts(qualification_store, args.qualification_operation_id, snapshots)
        spec = _fixed_spec(
            operation_id=args.operation_id,
            qualification_operation_id=args.qualification_operation_id,
            master_device_id=args.master,
            slave_device_id=args.slave,
            boundary_time=_next_boundary(clocks),
        )
        token = schedule_flow_confirmation_token(
            config.instance.id,
            spec,
            snapshots,
            digests,
        )
        now = datetime.now(UTC)
        intent = HardwareTestIntent(
            version=3,
            instance_id=config.instance.id,
            operation_id=spec.operation_id,
            phase=HardwareTestIntentPhase.ARMED,
            confirmation_token=token,
            spec=spec.outer_linkage_spec(),
            snapshots=snapshots,
            created_at=now,
            updated_at=now,
            evidence=HardwareTestEvidence(),
            schedule_flow_spec=spec,
            schedule_image_digests=digests,
        )
        intent_store.save(intent)

    print("Schedule-flow preflight passed; no control or schedule frame was sent.")
    print(f"Boundary: {spec.boundary_time} (device-local time)")
    print("Plan: Constant master 31% / slave 32% -> Sine master 35% / slave 40%.")
    print("Observation: 300s stable evidence inside a 600s bounded window.")
    print(f"Confirmation token: {token}")
    return 0


def _spec_from_run_args(args: argparse.Namespace) -> ScheduleFlowExperimentSpec:
    return _fixed_spec(
        operation_id=args.operation_id,
        qualification_operation_id=args.qualification_operation_id,
        master_device_id=args.master,
        slave_device_id=args.slave,
        boundary_time=args.boundary_time,
    )


def _assert_digest_match(
    expected: Sequence[HardwareTestScheduleImageDigest],
    actual: Sequence[HardwareTestScheduleImageDigest | ScheduleImageSnapshot],
) -> None:
    expected_by_id = {value.device_id: value for value in expected}
    if tuple(value.device_id for value in actual) != tuple(value.device_id for value in expected):
        raise ConfirmationMismatchError("schedule snapshot device order changed")
    for value in actual:
        approved = expected_by_id[value.device_id]
        if (
            value.physical_binding != approved.physical_binding
            or not hmac.compare_digest(value.image_sha256, approved.image_sha256)
        ):
            raise ConfirmationMismatchError("exact schedule image changed after preflight")


def _snapshot_authorizer(
    intent_store: JsonHardwareTestIntentStore,
    expected_intent: HardwareTestIntent,
):
    expected_operation_ids = {
        f"{expected_intent.operation_id}_sentinel",
        f"{expected_intent.operation_id}_schedule",
    }

    def authorize(
        spec: TemporaryScheduleSpec,
        snapshots: tuple[ScheduleImageSnapshot, ...],
    ) -> None:
        current = intent_store.load()
        immutable_authority = (
            "version",
            "instance_id",
            "operation_id",
            "confirmation_token",
            "spec",
            "snapshots",
            "created_at",
            "schedule_flow_spec",
            "schedule_image_digests",
        )
        if (
            current is None
            or current.phase is not HardwareTestIntentPhase.STARTED
            or any(
                getattr(current, field) != getattr(expected_intent, field)
                for field in immutable_authority
            )
        ):
            raise ConfirmationMismatchError("schedule-flow intent changed during execution")
        if spec.operation_id not in expected_operation_ids:
            raise ConfirmationMismatchError("temporary schedule operation identity changed")
        _assert_digest_match(expected_intent.schedule_image_digests, snapshots)

    return authorize


def _outer_token_factory(intent: HardwareTestIntent):
    flow_spec = intent.schedule_flow_spec
    if flow_spec is None:
        raise ScheduleFlowCliError("schedule-flow intent has no full experiment spec")

    def token(
        instance_id: str,
        outer_spec: Any,
        snapshots: Sequence[DeviceControlSnapshot],
    ) -> str:
        if outer_spec != flow_spec.outer_linkage_spec():
            raise ConfirmationMismatchError("outer linkage plan changed after preflight")
        return schedule_flow_confirmation_token(
            instance_id,
            flow_spec,
            snapshots,
            intent.schedule_image_digests,
        )

    return token


def _print_sample(sample: ScheduleLinkageSample | None) -> None:
    if sample is None:
        print("Effective sample: none")
        return
    print(
        "Effective sample: "
        f"phase={sample.phase}, "
        f"master={sample.master.mode}/{sample.master.flow}%/freq{sample.master.frequency}, "
        f"slave={sample.slave.mode}/{sample.slave.flow}%/freq{sample.slave.frequency}"
    )


async def _await_with_stop_signals(
    operation: Awaitable[Any],
    *,
    task_name: str,
    interrupt_event: asyncio.Event | None = None,
) -> Any:
    """Convert SIGINT/SIGTERM or caller cancellation into one shielded core cancellation.

    The composed controllers own their inverse-order compensation.  This wrapper prevents a
    routine Docker stop from bypassing that compensation while still preserving cancellation as
    the command's final result after every shielded rollback child has completed.
    """

    loop = asyncio.get_running_loop()
    stop_event = interrupt_event or asyncio.Event()
    installed: list[signal.Signals] = []

    def request_stop() -> None:
        stop_event.set()

    if interrupt_event is None:
        for event in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(event, request_stop)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - platform fallback
                continue
            installed.append(event)

    operation_task = asyncio.create_task(operation, name=task_name)
    signal_task = asyncio.create_task(stop_event.wait(), name=f"{task_name}-stop")
    cancellation_received = False
    try:
        try:
            done, _ = await asyncio.wait(
                {operation_task, signal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            cancellation_received = True
            done = set()
        if (cancellation_received or signal_task in done) and not operation_task.done():
            operation_task.cancel()
        while not operation_task.done():
            try:
                await asyncio.shield(operation_task)
            except asyncio.CancelledError:
                cancellation_received = True
                operation_task.cancel()
        result = operation_task.result()
        if cancellation_received:
            raise asyncio.CancelledError
        return result
    finally:
        for event in installed:
            loop.remove_signal_handler(event)
        if not signal_task.done():
            signal_task.cancel()
        await asyncio.gather(signal_task, return_exceptions=True)


async def _run(
    config: AppConfig,
    args: argparse.Namespace,
    intent_store: JsonHardwareTestIntentStore,
    outer_store: JsonLinkageJournalStore,
    schedule_store: JsonTemporaryScheduleJournalStore,
    role_store: JsonScheduleLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    guard: DeploymentHardwareGuard,
) -> int:
    _assert_no_verification_conflict()
    spec = _spec_from_run_args(args)
    selected = _validate_config(config, frozenset({spec.master_device_id, spec.slave_device_id}))
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        if _safety_latch_present(emergency_stop_latch_path()):
            raise ScheduleFlowCliError("persistent safety latch is active")
        if any(store.load() is not None for store in (outer_store, schedule_store, role_store)):
            raise ScheduleFlowCliError("unfinished recovery blocks a new schedule-flow run")
        intent = intent_store.load()
        if intent is None or intent.phase is not HardwareTestIntentPhase.ARMED:
            raise ScheduleFlowCliError("run requires an armed schedule-flow preflight")
        _assert_intent_authentic(intent, config.instance.id)
        if intent.schedule_flow_spec != spec or intent.spec != spec.outer_linkage_spec():
            raise ScheduleFlowCliError("run arguments do not match the armed full experiment")
        if not hmac.compare_digest(args.confirm, intent.confirmation_token):
            raise ConfirmationMismatchError("confirmation token does not match")
        _require_receipts(qualification_store, spec.qualification_operation_id, intent.snapshots)

        devices = await _build_devices(config, selected, writable=True)
        async with _connected(devices):
            _require_plan_supported(devices, spec)
            fresh_digests, fresh_clocks = await _capture_schedule_context(
                devices,
                (spec.master_device_id, spec.slave_device_id),
            )
            _assert_digest_match(intent.schedule_image_digests, fresh_digests)
            _require_boundary_still_fresh(spec.boundary_time, fresh_clocks)
            intent = _persist_successor(
                intent_store,
                intent,
                _updated_intent(intent, HardwareTestIntentPhase.STARTED, None),
            )
            pending_evidence = intent.evidence
            if pending_evidence is None:
                raise ScheduleFlowCliError("diagnostic intent evidence is unavailable")
            pending_stage_events = intent.schedule_flow_stage_events

            def persist_stage_event(event: ScheduleFlowStageEvent) -> None:
                nonlocal intent, pending_stage_events
                pending_stage_events = _append_schedule_stage_event(
                    pending_stage_events,
                    event,
                )
                successor = intent.model_copy(
                    update={
                        "schedule_flow_stage_events": pending_stage_events,
                        "updated_at": max(datetime.now(UTC), intent.updated_at),
                    }
                )
                intent = _persist_successor(intent_store, intent, successor)

            def persist_diagnostic_event(event: LinkageDiagnosticEvent) -> None:
                """Persist the outer controller's allow-listed event without raw errors."""

                nonlocal intent, pending_evidence
                if pending_evidence is None:
                    raise ScheduleFlowCliError("diagnostic intent evidence is unavailable")
                successor_evidence = _evidence_after_event(
                    pending_evidence,
                    event,
                    spec.outer_linkage_spec(),
                )
                if successor_evidence == pending_evidence:
                    return
                # Retain the sanitized in-memory successor even if persistence fails. The core
                # treats forward/rollback evidence as best-effort so compensation must continue;
                # the outer before-clear hook gets one final durable retry before authority is
                # removed.
                pending_evidence = successor_evidence
                successor = intent.model_copy(
                    update={
                        "evidence": successor_evidence,
                        "schedule_flow_stage_events": pending_stage_events,
                        "updated_at": max(datetime.now(UTC), intent.updated_at),
                    }
                )
                intent = _persist_successor(intent_store, intent, successor)

            def persist_sample(sample: ScheduleLinkageSample) -> None:
                nonlocal intent
                previous = intent.schedule_flow_sample
                if (
                    previous is not None
                    and previous.phase == sample.phase
                    and previous.master == sample.master
                    and previous.slave == sample.slave
                    and previous.master_manual_power == sample.master_manual_power
                    and previous.slave_manual_power == sample.slave_manual_power
                    and previous.master_linkage is sample.master_linkage
                    and previous.slave_linkage is sample.slave_linkage
                ):
                    return
                successor = intent.model_copy(
                    update={
                        "schedule_flow_sample": sample,
                        "schedule_flow_stage_events": pending_stage_events,
                        "updated_at": max(datetime.now(UTC), intent.updated_at),
                    }
                )
                try:
                    intent = _persist_successor(intent_store, intent, successor)
                except Exception:
                    # Evidence persistence is retried by the outer terminal hook. It must not
                    # perturb the physical observer or prevent its ordered rollback.
                    return

            def mark_terminal_before_outer_clear() -> None:
                nonlocal intent, pending_evidence
                if schedule_store.load() is not None or role_store.load() is not None:
                    raise ScheduleFlowCliError(
                        "nested schedule recovery remains before outer journal clear"
                    )
                persist_stage_event(
                    ScheduleFlowStageEvent(
                        stage=ScheduleFlowStage.OUTER_RESTORED,
                        occurred_at=datetime.now(UTC),
                    )
                )
                latest = controller.last_role_sample
                role_result = controller.last_role_result
                result_updates: dict[str, Any] = {}
                if latest is not None:
                    result_updates["schedule_flow_sample"] = latest
                if role_result is not None and latest is not None and latest.phase == "after":
                    outcome = classify_schedule_flow_sample(spec, latest)
                    result_updates.update(
                        {
                            "schedule_flow_outcome": outcome,
                            "schedule_transition_verified": (
                                outcome is ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED
                            ),
                            "stable_slave_tuple_observed": True,
                            "stable_observation_seconds": (
                                spec.post_boundary_stability_seconds
                            ),
                        }
                    )
                outer_record = outer_store.load()
                if outer_record is not None:
                    pending_evidence = _evidence_with_rollback_failures(
                        pending_evidence,
                        outer_record,
                    )
                completed_at = max(
                    datetime.now(UTC),
                    pending_evidence.rollback_started_at or intent.updated_at,
                )
                current = intent.model_copy(
                    update={
                        **result_updates,
                        "schedule_flow_stage_events": pending_stage_events,
                        "evidence": _evidence_with_rollback_completed(
                            pending_evidence,
                            completed_at=completed_at,
                        ),
                        "updated_at": max(datetime.now(UTC), intent.updated_at),
                    }
                )
                successor = _updated_intent(
                    current,
                    HardwareTestIntentPhase.TERMINAL,
                    (
                        current.schedule_flow_outcome.value
                        if current.schedule_flow_outcome is not None
                        else "restored"
                    ),
                )
                intent = _persist_successor(intent_store, intent, successor)

            confirming_outer = ConfirmingLinkageJournalStore(
                outer_store,
                instance_id=config.instance.id,
                expected_token=intent.confirmation_token,
                before_clear=mark_terminal_before_outer_clear,
                confirmation_token_factory=_outer_token_factory(intent),
            )
            controller = ScheduleFlowExperimentController(
                devices,
                confirming_outer,
                schedule_store,
                role_store,
                safety_interlock=guard,
                prerequisite_authorizer=_qualification_authorizer(qualification_store),
                role_sample_observer=persist_sample,
                diagnostic_event_observer=persist_diagnostic_event,
                stage_event_observer=persist_stage_event,
                schedule_snapshot_authorizer=_snapshot_authorizer(intent_store, intent),
            )
            guard.clear()
            if not guard.permitted:
                raise ScheduleFlowCliError("persistent safety latch became active")
            try:
                result = await _await_with_stop_signals(
                    controller.run_experiment(spec),
                    task_name="schedule-flow-experiment",
                )
            except BaseException:
                pending_outer = outer_store.load()
                pending = pending_outer is not None or any(
                    store.load() is not None for store in (schedule_store, role_store)
                )
                current = intent_store.load() or intent
                if pending_outer is not None:
                    pending_evidence = _evidence_with_rollback_failures(
                        pending_evidence,
                        pending_outer,
                    )
                    current = current.model_copy(
                        update={
                            "evidence": pending_evidence,
                            "schedule_flow_stage_events": pending_stage_events,
                            "updated_at": max(datetime.now(UTC), current.updated_at),
                        }
                    )
                elif current.schedule_flow_stage_events != pending_stage_events:
                    current = current.model_copy(
                        update={
                            "schedule_flow_stage_events": pending_stage_events,
                            "updated_at": max(datetime.now(UTC), current.updated_at),
                        }
                    )
                last_sample = controller.last_role_sample
                if last_sample is not None:
                    current = current.model_copy(
                        update={
                            "schedule_flow_sample": last_sample,
                            "updated_at": max(datetime.now(UTC), current.updated_at),
                        }
                    )
                successor = _updated_intent(
                    current,
                    (
                        HardwareTestIntentPhase.RECOVERY_REQUIRED
                        if pending
                        else HardwareTestIntentPhase.TERMINAL
                    ),
                    "recovery_required" if pending else "experiment_failed_restored",
                )
                try:
                    intent_store.save(successor)
                except Exception:
                    # The composed controller has already completed or durably journaled its
                    # rollback. Preserve the physical failure instead of masking it with an
                    # evidence-only persistence error.
                    pass
                raise

        current = intent_store.load() or intent
        result_already_durable = (
            current.phase is HardwareTestIntentPhase.TERMINAL
            and current.outcome == result.outcome.value
            and current.schedule_flow_outcome is result.outcome
            and current.schedule_flow_sample == result.last_after_sample
            and current.schedule_transition_verified is result.schedule_transition_verified
            and current.stable_slave_tuple_observed is result.stable_slave_tuple_observed
            and current.stable_observation_seconds == result.stable_observation_seconds
        )
        if not result_already_durable:
            current = current.model_copy(
                update={
                    "schedule_flow_outcome": result.outcome,
                    "schedule_flow_sample": result.last_after_sample,
                    "schedule_transition_verified": result.schedule_transition_verified,
                    "stable_slave_tuple_observed": result.stable_slave_tuple_observed,
                    "stable_observation_seconds": result.stable_observation_seconds,
                    "updated_at": max(datetime.now(UTC), current.updated_at),
                }
            )
            current = _updated_intent(
                current,
                HardwareTestIntentPhase.TERMINAL,
                result.outcome.value,
            )
            intent_store.save(current)

    print(f"Schedule-flow result: {result.outcome.value}")
    _print_sample(result.last_after_sample)
    print(f"Stable observation: {result.stable_observation_seconds:g}s")
    print("Exact schedules and original control states were restored.")
    return 0


def schedule_flow_recovery_token(
    instance_id: str,
    intent: HardwareTestIntent,
    outer: LinkageTransactionRecord | None,
    temporary: TemporaryScheduleRecord | None,
    role: ScheduleLinkageRecord | None,
) -> str:
    """Hash every recovery authority without rendering private schedule bytes."""

    canonical = {
        "version": _TOKEN_VERSION,
        "instance_id": instance_id,
        "intent": intent.model_dump(mode="json"),
        "outer": outer.model_dump(mode="json") if outer is not None else None,
        "temporary": temporary.model_dump(mode="json") if temporary is not None else None,
        "role": role.model_dump(mode="json") if role is not None else None,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFER-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _load_recovery_state(
    config: AppConfig,
    intent_store: JsonHardwareTestIntentStore,
    outer_store: JsonLinkageJournalStore,
    schedule_store: JsonTemporaryScheduleJournalStore,
    role_store: JsonScheduleLinkageJournalStore,
) -> tuple[
    HardwareTestIntent | None,
    LinkageTransactionRecord | None,
    TemporaryScheduleRecord | None,
    ScheduleLinkageRecord | None,
]:
    intent = intent_store.load()
    outer = outer_store.load()
    temporary = schedule_store.load()
    role = role_store.load()
    if intent is None:
        if any(value is not None for value in (outer, temporary, role)):
            raise ScheduleFlowCliError("schedule-flow journals have no owning one-shot intent")
        return None, None, None, None
    if intent.version < 3:
        if (
            intent.phase is HardwareTestIntentPhase.TERMINAL
            and all(value is None for value in (outer, temporary, role))
        ):
            _assert_existing_allows_preflight(
                intent,
                instance_id=config.instance.id,
                operation_id=f"{intent.operation_id}_status_only",
            )
            return None, None, None, None
        raise ScheduleFlowCliError(
            "an unrelated native-linkage operation or journal blocks schedule-flow"
        )
    _assert_intent_authentic(intent, config.instance.id)
    if outer is not None and (
        outer.operation_id != intent.operation_id
        or outer.spec != intent.spec
        or outer.snapshots != intent.snapshots
    ):
        raise ScheduleFlowCliError("schedule-flow intent and outer recovery journal disagree")
    if (temporary is not None or role is not None) and outer is None:
        raise ScheduleFlowCliError("nested schedule journal has no owning outer recovery journal")
    if outer is not None and (temporary is not None or role is not None):
        try:
            ScheduleFlowExperimentController._validate_nested_recovery_ownership(  # noqa: SLF001
                outer,
                temporary,
                role,
            )
        except Exception:
            raise ScheduleFlowCliError(
                "nested schedule journal does not match its outer recovery owner"
            ) from None

    flow_spec = intent.schedule_flow_spec
    if flow_spec is None:
        raise ScheduleFlowCliError("schedule-flow recovery spec is unavailable")
    if temporary is not None:
        try:
            _assert_digest_match(intent.schedule_image_digests, temporary.snapshots)
        except ConfirmationMismatchError:
            raise ScheduleFlowCliError(
                "temporary schedule snapshots do not match the confirmed original images"
            ) from None
        if (
            temporary.spec.kind is TemporaryScheduleKind.FIELD_OBSERVATION
            and temporary.spec != flow_spec.temporary_schedule_spec()
        ):
            raise ScheduleFlowCliError(
                "temporary field schedule does not match the confirmed experiment"
            )
    if role is not None and role.spec != flow_spec.role_observation_spec():
        raise ScheduleFlowCliError(
            "schedule-linkage recovery does not match the confirmed experiment"
        )
    return intent, outer, temporary, role


def _status(
    config: AppConfig,
    intent_store: JsonHardwareTestIntentStore,
    outer_store: JsonLinkageJournalStore,
    schedule_store: JsonTemporaryScheduleJournalStore,
    role_store: JsonScheduleLinkageJournalStore,
) -> int:
    intent, outer, temporary, role = _load_recovery_state(
        config,
        intent_store,
        outer_store,
        schedule_store,
        role_store,
    )
    print(f"One-shot intent: {intent.phase.value if intent is not None else 'none'}")
    print(f"Outer control journal: {outer.phase.value if outer is not None else 'none'}")
    temporary_status = temporary.phase.value if temporary is not None else "none"
    print(f"Temporary schedule journal: {temporary_status}")
    print(f"Role journal: {role.phase.value if role is not None else 'none'}")
    if intent is not None:
        print(f"Outcome: {intent.outcome or 'none'}")
        if intent.schedule_flow_outcome is not None:
            print(f"Schedule-flow classification: {intent.schedule_flow_outcome.value}")
            print(
                "Schedule transition verified: "
                + ("yes" if intent.schedule_transition_verified else "no")
            )
            print(
                "Stable slave tuple observed: "
                + ("yes" if intent.stable_slave_tuple_observed else "no")
            )
            if intent.stable_observation_seconds is not None:
                print(f"Stable observation: {intent.stable_observation_seconds:g}s")
        evidence = intent.evidence
        if evidence is not None:
            print(
                "Outer forward failure: "
                + (
                    evidence.forward_failure.value
                    if evidence.forward_failure is not None
                    else "none"
                )
            )
            rollback_status = "none"
            if evidence.rollback_completed_at is not None:
                rollback_status = "completed"
            elif evidence.rollback_started_at is not None:
                rollback_status = "started"
            print(f"Outer rollback: {rollback_status}")
        latest_stage = (
            intent.schedule_flow_stage_events[-1]
            if intent.schedule_flow_stage_events
            else None
        )
        print(
            "Schedule-flow stage: "
            + (latest_stage.stage.value if latest_stage is not None else "none")
        )
        if latest_stage is not None and latest_stage.completed_participants is not None:
            print(
                "Stage participants completed: "
                f"{latest_stage.completed_participants}/2"
            )
        latest_failure = next(
            (
                event
                for event in reversed(intent.schedule_flow_stage_events)
                if event.temporary_error_code is not None
                or event.failure_category is not None
            ),
            None,
        )
        failure_text = "none"
        if latest_failure is not None:
            classification = (
                latest_failure.temporary_error_code.value
                if latest_failure.temporary_error_code is not None
                else latest_failure.failure_category.value
                if latest_failure.failure_category is not None
                else "none"
            )
            failure_text = f"{latest_failure.stage.value}/{classification}"
        print(f"Schedule-flow failure: {failure_text}")
        _print_sample(intent.schedule_flow_sample)
        if intent.phase is not HardwareTestIntentPhase.TERMINAL or any(
            value is not None for value in (outer, temporary, role)
        ):
            print(
                "Recovery confirmation token: "
                + schedule_flow_recovery_token(
                    config.instance.id,
                    intent,
                    outer,
                    temporary,
                    role,
                )
            )
    return 0


async def _recover(
    config: AppConfig,
    confirmation: str | None,
    intent_store: JsonHardwareTestIntentStore,
    outer_store: JsonLinkageJournalStore,
    schedule_store: JsonTemporaryScheduleJournalStore,
    role_store: JsonScheduleLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    guard: DeploymentHardwareGuard,
) -> int:
    _assert_no_verification_conflict(
        allow_temporary_schedule=True,
        allow_schedule_linkage_journal=True,
    )
    intent, outer, temporary, role = _load_recovery_state(
        config,
        intent_store,
        outer_store,
        schedule_store,
        role_store,
    )
    if intent is None:
        print("No schedule-flow operation needs recovery.")
        return 0
    token = schedule_flow_recovery_token(
        config.instance.id,
        intent,
        outer,
        temporary,
        role,
    )
    if confirmation is None:
        print("Recovery is fail-closed; no control or schedule frame was sent.")
        print(f"Recovery confirmation token: {token}")
        return 0
    if not hmac.compare_digest(confirmation, token):
        raise ConfirmationMismatchError("recovery confirmation token does not match")
    spec = intent.schedule_flow_spec
    if spec is None:
        raise ScheduleFlowCliError("schedule-flow recovery spec is unavailable")
    selected = _validate_config(config, frozenset({spec.master_device_id, spec.slave_device_id}))
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        current = _load_recovery_state(
            config,
            intent_store,
            outer_store,
            schedule_store,
            role_store,
        )
        if current != (intent, outer, temporary, role):
            raise ConfirmationMismatchError("recovery state changed; request a new token")
        if intent.phase is HardwareTestIntentPhase.TERMINAL and all(
            value is None for value in (outer, temporary, role)
        ):
            print("The schedule-flow operation is already terminal; no frame was sent.")
            return 0
        if outer is None:
            if intent.has_diagnostic_progress:
                raise ScheduleFlowCliError(
                    "diagnostic evidence exists without a recovery journal; "
                    "manual inspection is required"
                )
            outcome = (
                "armed_preview_cancelled"
                if intent.phase is HardwareTestIntentPhase.ARMED
                else "crashed_before_first_write"
            )
            intent_store.save(
                _updated_intent(intent, HardwareTestIntentPhase.TERMINAL, outcome)
            )
            print("The no-write schedule-flow intent was closed; no frame was sent.")
            return 0
        if _safety_latch_present(emergency_stop_latch_path()):
            raise ScheduleFlowCliError("persistent safety latch blocks exact ON-state recovery")

        evidence = intent.evidence
        if evidence is None:
            raise ScheduleFlowCliError("diagnostic intent evidence is unavailable")
        compensation_required = (
            outer.phase is not LinkageTransactionPhase.PREPARED
            or temporary is not None
            or role is not None
        )
        if compensation_required:
            evidence = _evidence_after_event(
                evidence,
                LinkageDiagnosticEvent(
                    kind=LinkageDiagnosticEventKind.ROLLBACK_STARTED,
                    occurred_at=datetime.now(UTC),
                ),
                spec.outer_linkage_spec(),
            )
        recovery_intent = _updated_intent(
            intent.model_copy(update={"evidence": evidence}),
            HardwareTestIntentPhase.RECOVERY_REQUIRED,
            "recovery_started",
        )
        intent_store.save(recovery_intent)
        intent = recovery_intent
        pending_stage_events = intent.schedule_flow_stage_events

        def persist_stage_event(event: ScheduleFlowStageEvent) -> None:
            nonlocal intent, pending_stage_events
            pending_stage_events = _append_schedule_stage_event(
                pending_stage_events,
                event,
            )
            successor = intent.model_copy(
                update={
                    "schedule_flow_stage_events": pending_stage_events,
                    "updated_at": max(datetime.now(UTC), intent.updated_at),
                }
            )
            intent = _persist_successor(intent_store, intent, successor)

        def before_load() -> None:
            if intent_store.load() != intent:
                raise ConfirmationMismatchError("schedule-flow intent changed during recovery")

        def before_clear() -> None:
            nonlocal intent
            if schedule_store.load() is not None or role_store.load() is not None:
                raise ScheduleFlowCliError(
                    "nested schedule recovery remains before outer journal clear"
                )
            persist_stage_event(
                ScheduleFlowStageEvent(
                    stage=ScheduleFlowStage.OUTER_RESTORED,
                    occurred_at=datetime.now(UTC),
                )
            )
            evidence = intent.evidence
            if evidence is None:
                raise ScheduleFlowCliError("diagnostic intent evidence is unavailable")
            current_outer = outer_store.load()
            if current_outer is not None:
                evidence = _evidence_with_rollback_failures(evidence, current_outer)
            completed_at = max(
                datetime.now(UTC),
                evidence.rollback_started_at or intent.updated_at,
            )
            successor = _updated_intent(
                intent.model_copy(
                    update={
                        "evidence": _evidence_with_rollback_completed(
                            evidence,
                            completed_at=completed_at,
                        ),
                        "schedule_flow_stage_events": pending_stage_events,
                    }
                ),
                HardwareTestIntentPhase.TERMINAL,
                "recovered",
            )
            intent_store.save(successor)
            intent = successor

        confirming_outer = ConfirmingLinkageJournalStore(
            outer_store,
            instance_id=config.instance.id,
            expected_token=intent.confirmation_token,
            before_clear=before_clear,
            before_load=before_load,
            expected_loaded_record=outer,
            require_loaded_record_match=True,
            confirmation_token_factory=_outer_token_factory(intent),
        )
        devices = await _build_devices(config, selected, writable=True)
        controller = ScheduleFlowExperimentController(
            devices,
            confirming_outer,
            schedule_store,
            role_store,
            safety_interlock=guard,
            prerequisite_authorizer=_qualification_authorizer(qualification_store),
            stage_event_observer=persist_stage_event,
        )
        guard.clear()
        if not guard.permitted:
            raise ScheduleFlowCliError("persistent safety latch became active")
        async with _connected(devices):
            try:
                recovered = await _await_with_stop_signals(
                    controller.recover_experiment(),
                    task_name="schedule-flow-recovery",
                )
            except BaseException:
                latest = intent_store.load() or intent
                pending_outer = outer_store.load()
                latest_evidence = latest.evidence
                if pending_outer is not None and latest_evidence is not None:
                    latest = latest.model_copy(
                        update={
                            "evidence": _evidence_with_rollback_failures(
                                latest_evidence,
                                pending_outer,
                            ),
                            "schedule_flow_stage_events": pending_stage_events,
                            "updated_at": max(datetime.now(UTC), latest.updated_at),
                        }
                    )
                elif latest.schedule_flow_stage_events != pending_stage_events:
                    latest = latest.model_copy(
                        update={
                            "schedule_flow_stage_events": pending_stage_events,
                            "updated_at": max(datetime.now(UTC), latest.updated_at),
                        }
                    )
                intent_store.save(
                    _updated_intent(
                        latest,
                        HardwareTestIntentPhase.RECOVERY_REQUIRED,
                        "recovery_required",
                    )
                )
                raise
        if not recovered or any(
            store.load() is not None for store in (outer_store, schedule_store, role_store)
        ):
            raise ScheduleFlowCliError("ordered schedule-flow recovery remains incomplete")
        terminal = intent_store.load() or intent
        intent_store.save(
            _updated_intent(terminal, HardwareTestIntentPhase.TERMINAL, "recovered")
        )
    print("Roles, TimerOFF, exact schedules, and original controls were restored in order.")
    return 0


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--qualification-operation-id", required=True)
    parser.add_argument("--master", required=True, help="configured logical master name")
    parser.add_argument("--slave", required=True, help="configured logical slave name")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-schedule-flow-test")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="read and arm the exact experiment")
    _add_identity_arguments(preflight)
    run = commands.add_parser("run", help="run one exactly confirmed experiment")
    _add_identity_arguments(run)
    run.add_argument("--boundary-time", required=True, help="HH:MM printed by preflight")
    run.add_argument("--confirm", required=True, help="JFE token printed by preflight")
    recover = commands.add_parser("recover", help="preview or confirm ordered recovery")
    recover.add_argument("--confirm", help="JFER token printed by status/recover preview")
    commands.add_parser("status", help="show only sanitized durable state")
    return parser


async def dispatch(config: AppConfig, args: argparse.Namespace) -> int:
    validate_hardware_safety_root()
    intent_store = JsonHardwareTestIntentStore(native_linkage_intent_path())
    outer_store = JsonLinkageJournalStore(native_linkage_journal_path())
    schedule_store = JsonTemporaryScheduleJournalStore(temporary_schedule_journal_path())
    role_store = JsonScheduleLinkageJournalStore(schedule_linkage_journal_path())
    qualification_store = JsonQualificationStore(qualification_directory())
    guard = DeploymentHardwareGuard()
    with intent_store.lease(), guard.lease():
        if args.command == "status":
            return _status(config, intent_store, outer_store, schedule_store, role_store)
        if args.command == "preflight":
            return await _preflight(
                config,
                args,
                intent_store,
                outer_store,
                qualification_store,
            )
        if args.command == "run":
            return await _run(
                config,
                args,
                intent_store,
                outer_store,
                schedule_store,
                role_store,
                qualification_store,
                guard,
            )
        if args.command == "recover":
            return await _recover(
                config,
                args.confirm,
                intent_store,
                outer_store,
                schedule_store,
                role_store,
                qualification_store,
                guard,
            )
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        config = load_config(args.config)
        return asyncio.run(dispatch(config, args))
    except (ScheduleFlowCliError, ConfirmationMismatchError) as error:
        print(f"schedule-flow test refused: {error}", file=sys.stderr)
        return 2
    except HardwareOperationLockError:
        print("schedule-flow test refused: hardware workflow is busy", file=sys.stderr)
        return 2
    except asyncio.CancelledError:
        print("schedule-flow test interrupted after ordered rollback", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        # Never render protocol frames, raw schedule bytes, or stable hardware identifiers.
        print(f"schedule-flow test failed safely ({type(error).__name__})", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("schedule-flow test interrupted after rollback", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ScheduleFlowCliError",
    "build_parser",
    "dispatch",
    "main",
    "schedule_flow_recovery_token",
]
