from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from jebao_flow import schedule_flow_experiment_cli as cli
from jebao_flow import schedule_linkage_cli
from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.devices.linkage import (
    DeviceControlSnapshot,
    LinkageDiagnosticEvent,
    LinkageDiagnosticEventKind,
    LinkageForwardFailureCategory,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
)
from jebao_flow.devices.schedule_flow_experiment import (
    SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT,
    SCHEDULE_FLOW_STAGE_EVENT_LIMIT,
    ScheduleFlowExperimentResult,
    ScheduleFlowFailureCategory,
    ScheduleFlowOutcome,
    ScheduleFlowStage,
    ScheduleFlowStageEvent,
    classify_schedule_flow_sample,
)
from jebao_flow.devices.schedule_linkage import (
    ScheduleAutoEvidence,
    ScheduleLinkageRunProgressEvent,
    ScheduleLinkageRunProgressKind,
    ScheduleLinkageSample,
)
from jebao_flow.devices.schedule_transaction import (
    DeviceSchedulePatch,
    ScheduleImageSnapshot,
    TemporaryScheduleErrorCode,
    TemporaryScheduleKind,
    TemporarySchedulePhase,
    TemporaryScheduleRecord,
    TemporaryScheduleSpec,
    behavior_neutral_unused_slot_patch,
)
from jebao_flow.hardware_test import (
    ConfirmingLinkageJournalStore,
    HardwareTestEvidence,
    HardwareTestIntent,
    HardwareTestIntentPhase,
    HardwareTestScheduleImageDigest,
    hardware_test_intent_confirmation_token,
    preview_confirmation_token,
    schedule_flow_confirmation_token,
)
from jebao_flow.persistence.qualification import (
    DeviceQualificationReceipt,
    JsonQualificationStore,
)
from jebao_flow.protocol.models import LinkageRole


def _binding(seed: str) -> PhysicalDeviceBinding:
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=f"vendor-{seed}",
        mac_address=f"0011223344{seed}",
        product_key="local-pro",
        config_fingerprint=hashlib.sha256(seed.encode()).hexdigest(),
    )


def _snapshots() -> tuple[DeviceControlSnapshot, ...]:
    return tuple(
        DeviceControlSnapshot(
            device_id=device_id,
            physical_binding=_binding(seed),
            enabled=True,
            power=power,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
            schedule_fingerprint=f"schedule-{seed}",
        )
        for device_id, seed, power in (
            ("master", "01", 31),
            ("slave", "02", 32),
        )
    )


def _digests() -> tuple[HardwareTestScheduleImageDigest, ...]:
    snapshots = _snapshots()
    return tuple(
        HardwareTestScheduleImageDigest(
            device_id=snapshot.device_id,
            physical_binding=snapshot.physical_binding,
            image_sha256=hashlib.sha256(snapshot.device_id.encode()).hexdigest(),
        )
        for snapshot in snapshots
    )


def _spec(boundary: str = "12:34", *, sentinel_only: bool = False):
    return cli._fixed_spec(  # noqa: SLF001
        operation_id="scheduled_flow_001",
        qualification_operation_id="qualified_pair_001",
        master_device_id="master",
        slave_device_id="slave",
        boundary_time=boundary,
        sentinel_only=sentinel_only,
    )


def _intent(
    *,
    phase: HardwareTestIntentPhase = HardwareTestIntentPhase.ARMED,
    outcome: str | None = None,
    sample: ScheduleLinkageSample | None = None,
    include_result: bool = False,
    sentinel_only: bool = False,
) -> HardwareTestIntent:
    spec = _spec(sentinel_only=sentinel_only)
    snapshots = _snapshots()
    digests = _digests()
    token = schedule_flow_confirmation_token("main", spec, snapshots, digests)
    now = datetime.now(UTC)
    classified = classify_schedule_flow_sample(spec, sample) if include_result and sample else None
    return HardwareTestIntent(
        version=3,
        instance_id="main",
        operation_id=spec.operation_id,
        phase=phase,
        confirmation_token=token,
        spec=spec.outer_linkage_spec(),
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        outcome=outcome,
        evidence=HardwareTestEvidence(),
        schedule_flow_spec=spec,
        schedule_image_digests=digests,
        schedule_flow_outcome=classified,
        schedule_flow_sample=sample,
        schedule_transition_verified=(
            classified is ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED
            if classified is not None
            else None
        ),
        stable_slave_tuple_observed=True if classified is not None else None,
        stable_observation_seconds=(
            spec.post_boundary_stability_seconds if classified is not None else None
        ),
    )


def _legacy_intent(
    *,
    phase: HardwareTestIntentPhase = HardwareTestIntentPhase.TERMINAL,
    outcome: str | None = "recovered",
    operation_id: str = "scheduled_flow_001",
) -> HardwareTestIntent:
    spec = _spec().outer_linkage_spec().model_copy(update={"operation_id": operation_id})
    snapshots = _snapshots()
    now = datetime.now(UTC)
    return HardwareTestIntent(
        version=2,
        instance_id="main",
        operation_id=spec.operation_id,
        phase=phase,
        confirmation_token=preview_confirmation_token("main", spec, snapshots),
        spec=spec,
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        outcome=outcome,
        evidence=HardwareTestEvidence(),
    )


def _after_sample(*, master_flow: int = 35, slave_flow: int = 40) -> ScheduleLinkageSample:
    return ScheduleLinkageSample(
        observed_at=datetime.now(UTC),
        phase="after",
        master=ScheduleAutoEvidence(mode="sine", flow=master_flow, frequency=30),
        slave=ScheduleAutoEvidence(mode="sine", flow=slave_flow, frequency=30),
        master_manual_power=31,
        slave_manual_power=32,
        master_linkage=LinkageRole.MASTER,
        slave_linkage=LinkageRole.ASYNC_SLAVE,
    )


def test_fixed_plan_is_the_only_audited_field_shape() -> None:
    spec = _spec()

    assert (
        spec.master_before_flow,
        spec.slave_before_flow,
        spec.master_after_flow,
        spec.slave_after_flow,
    ) == (31, 32, 35, 40)
    assert spec.sine_frequency == 30
    assert spec.safe_frequency == 20
    assert spec.post_boundary_stability_seconds == 300
    assert spec.observation_window_seconds == 600
    assert spec.sentinel_qualification is True


def test_sentinel_only_is_cli_parsed_and_confirmation_token_bound() -> None:
    parser = cli.build_parser()
    identity = (
        "--operation-id",
        "scheduled_flow_001",
        "--qualification-operation-id",
        "qualified_pair_001",
        "--master",
        "master",
        "--slave",
        "slave",
    )
    preflight = parser.parse_args(("preflight", *identity, "--sentinel-only"))
    run = parser.parse_args(
        (
            "run",
            *identity,
            "--boundary-time",
            "12:34",
            "--confirm",
            "JFE-placeholder",
            "--sentinel-only",
        )
    )
    wire_spec = _spec(sentinel_only=True)

    assert preflight.sentinel_only is True
    assert run.sentinel_only is True
    assert schedule_flow_confirmation_token(
        "main", wire_spec, _snapshots(), _digests()
    ) != schedule_flow_confirmation_token(
        "main", _spec(), _snapshots(), _digests()
    )


def test_default_spec_keeps_pre_sentinel_only_v3_confirmation_token() -> None:
    spec = _spec()
    legacy_spec = spec.model_dump(mode="json")
    legacy_spec.pop("sentinel_only")
    canonical = {
        "version": 1,
        "instance_id": "main",
        "schedule_flow_spec": legacy_spec,
        "snapshots": [
            snapshot.model_dump(mode="json")
            for snapshot in sorted(_snapshots(), key=lambda value: value.device_id)
        ],
        "schedule_image_digests": [
            digest.model_dump(mode="json")
            for digest in sorted(_digests(), key=lambda value: value.device_id)
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    legacy_token = f"JFE-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"

    assert schedule_flow_confirmation_token(
        "main", spec, _snapshots(), _digests()
    ) == legacy_token


def _wire_terminal_payload() -> dict[str, object]:
    armed = _intent(sentinel_only=True)
    stages = tuple(
        ScheduleFlowStageEvent(
            stage=stage,
            occurred_at=armed.created_at + timedelta(microseconds=index),
            completed_participants=(
                2 if stage is not ScheduleFlowStage.OUTER_RESTORED else None
            ),
        )
        for index, stage in enumerate(
            (
                ScheduleFlowStage.SENTINEL_VERIFIED,
                ScheduleFlowStage.SENTINEL_RESTORED,
                ScheduleFlowStage.OUTER_RESTORED,
            )
        )
    )
    return armed.model_dump(mode="python") | {
        "phase": HardwareTestIntentPhase.TERMINAL,
        "outcome": "wire_qualified",
        "schedule_flow_stage_events": stages,
    }


def test_wire_qualified_intent_requires_complete_sentinel_only_proof() -> None:
    intent = HardwareTestIntent.model_validate(_wire_terminal_payload())

    assert intent.phase is HardwareTestIntentPhase.TERMINAL
    assert intent.schedule_flow_spec is not None
    assert intent.schedule_flow_spec.sentinel_only is True
    assert intent.outcome == "wire_qualified"


def test_wire_qualified_intent_rejects_a_full_flow_spec() -> None:
    with pytest.raises(ValidationError, match="sentinel-only spec"):
        HardwareTestIntent.model_validate(
            _wire_terminal_payload()
            | {"schedule_flow_spec": _spec(sentinel_only=False)}
        )


@pytest.mark.parametrize("missing_stage", tuple(ScheduleFlowStage(stage) for stage in (
    "sentinel_verified",
    "sentinel_restored",
    "outer_restored",
)))
def test_wire_qualified_intent_rejects_missing_required_stage(
    missing_stage: ScheduleFlowStage,
) -> None:
    payload = _wire_terminal_payload()
    payload["schedule_flow_stage_events"] = tuple(
        event
        for event in payload["schedule_flow_stage_events"]
        if event.stage is not missing_stage
    )

    with pytest.raises(ValidationError, match="ordered durable stage evidence"):
        HardwareTestIntent.model_validate(payload)


@pytest.mark.parametrize(
    "partial_stage",
    (
        ScheduleFlowStage.SENTINEL_VERIFIED,
        ScheduleFlowStage.SENTINEL_RESTORED,
    ),
)
def test_wire_qualified_intent_requires_both_sentinel_participants(
    partial_stage: ScheduleFlowStage,
) -> None:
    payload = _wire_terminal_payload()
    payload["schedule_flow_stage_events"] = tuple(
        event.model_copy(update={"completed_participants": 1})
        if event.stage is partial_stage
        else event
        for event in payload["schedule_flow_stage_events"]
    )

    with pytest.raises(ValidationError, match="both sentinel participants"):
        HardwareTestIntent.model_validate(payload)


@pytest.mark.parametrize(
    ("failure_field", "failure"),
    (
        (
            "temporary_error_code",
            TemporaryScheduleErrorCode.STAGE_WRITE_FAILED,
        ),
        ("failure_category", ScheduleFlowFailureCategory.UNEXPECTED),
    ),
)
def test_wire_qualified_intent_rejects_any_failure_event(
    failure_field: str,
    failure: object,
) -> None:
    payload = _wire_terminal_payload()
    first, *remaining = payload["schedule_flow_stage_events"]
    payload["schedule_flow_stage_events"] = (
        first.model_copy(update={failure_field: failure}),
        *remaining,
    )

    with pytest.raises(ValidationError, match="failure evidence"):
        HardwareTestIntent.model_validate(payload)


@pytest.mark.parametrize(
    "forbidden_stage",
    (
        ScheduleFlowStage.FIELD_WRITE_STARTED,
        ScheduleFlowStage.TIMER_ON_ARMED,
        ScheduleFlowStage.ROLE_PREFLIGHT_STARTED,
    ),
)
def test_wire_qualified_intent_rejects_field_timer_or_role_stage(
    forbidden_stage: ScheduleFlowStage,
) -> None:
    payload = _wire_terminal_payload()
    sentinel_verified, sentinel_restored, outer_restored = payload[
        "schedule_flow_stage_events"
    ]
    payload["schedule_flow_stage_events"] = (
        sentinel_verified,
        sentinel_restored,
        ScheduleFlowStageEvent(
            stage=forbidden_stage,
            occurred_at=sentinel_restored.occurred_at + timedelta(microseconds=1),
        ),
        outer_restored.model_copy(
            update={"occurred_at": outer_restored.occurred_at + timedelta(microseconds=1)}
        ),
    )

    with pytest.raises(ValidationError, match="field or role stages"):
        HardwareTestIntent.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schedule_flow_outcome", ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED),
        ("schedule_flow_sample", _after_sample()),
        ("schedule_transition_verified", False),
        ("stable_slave_tuple_observed", False),
        ("stable_observation_seconds", 0),
    ),
)
def test_wire_qualified_intent_rejects_field_result_metadata(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="field result metadata"):
        HardwareTestIntent.model_validate(_wire_terminal_payload() | {field: value})


def test_wire_qualified_intent_rejects_nonterminal_phase() -> None:
    with pytest.raises(ValidationError, match="must be terminal"):
        HardwareTestIntent.model_validate(
            _wire_terminal_payload() | {"phase": HardwareTestIntentPhase.STARTED}
        )


def test_sentinel_only_failure_intent_rejects_field_metadata() -> None:
    intent = _intent(sentinel_only=True)

    with pytest.raises(ValidationError, match="sentinel-only intents"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {
                "phase": HardwareTestIntentPhase.TERMINAL,
                "outcome": "experiment_failed_restored",
                "schedule_flow_sample": _after_sample(),
            }
        )


def test_sentinel_only_terminal_rejects_schedule_flow_classification() -> None:
    intent = _intent(sentinel_only=True)

    with pytest.raises(ValidationError, match="cannot use a schedule-flow classification"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {
                "phase": HardwareTestIntentPhase.TERMINAL,
                "outcome": ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED.value,
            }
        )


def test_full_flow_classified_terminal_requires_matching_top_level_outcome() -> None:
    intent = _intent(
        phase=HardwareTestIntentPhase.TERMINAL,
        outcome=ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED.value,
        sample=_after_sample(),
        include_result=True,
    )

    with pytest.raises(ValidationError, match="classified evidence"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {"outcome": ScheduleFlowOutcome.SLAVE_FLOW_FOLLOWED_MASTER.value}
        )

    retained_failure = HardwareTestIntent.model_validate(
        intent.model_dump(mode="python") | {"outcome": "experiment_failed_restored"}
    )
    assert retained_failure.schedule_flow_outcome is ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED


def test_pause_authorizer_requires_current_receipts_from_named_qualification(tmp_path) -> None:
    store = JsonQualificationStore(tmp_path / "qualifications")
    now = datetime.now(UTC)
    for snapshot in _snapshots():
        store.save(
            DeviceQualificationReceipt(
                operation_id="qualified_pair_001",
                device_id=snapshot.device_id,
                physical_binding=snapshot.physical_binding,
                original_power=40,
                step_power=35,
                completed_at=now - timedelta(minutes=1),
                valid_until=now + timedelta(hours=1),
            )
        )
    authorize = cli._pause_authorizer(store)  # noqa: SLF001

    authorize(_spec(), _snapshots())

    invalid = _snapshots()[1]
    store.save(
        DeviceQualificationReceipt(
            operation_id="another_qualification",
            device_id=invalid.device_id,
            physical_binding=invalid.physical_binding,
            original_power=40,
            step_power=35,
            completed_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1),
        )
    )
    with pytest.raises(cli.ScheduleFlowCliError, match="named qualification"):
        authorize(_spec(), _snapshots())


def test_boundary_uses_freshest_device_clock_and_refuses_skew_or_midnight() -> None:
    first = datetime(2026, 8, 27, 12, 10, 1)
    second = datetime(2026, 8, 27, 12, 10, 2)

    assert cli._next_boundary((first, second)) == "12:14"  # noqa: SLF001
    with pytest.raises(cli.ScheduleFlowCliError, match="two-second skew"):
        cli._next_boundary((first, second + timedelta(seconds=3)))  # noqa: SLF001
    with pytest.raises(cli.ScheduleFlowCliError, match="midnight"):
        cli._next_boundary(  # noqa: SLF001
            (
                datetime(2026, 8, 27, 23, 57, 1),
                datetime(2026, 8, 27, 23, 57, 2),
            )
        )


async def test_schedule_context_starts_both_device_reads_concurrently() -> None:
    started: list[str] = []
    both_started = asyncio.Event()
    clock = datetime(2026, 8, 27, 12, 10, 1)

    class Device:
        def __init__(self, device_id: str, seed: str, local_clock: datetime) -> None:
            self.device_id = device_id
            self.physical_binding = _binding(seed)
            self.local_clock = local_clock

        async def get_state(self):
            started.append(self.device_id)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            return SimpleNamespace(
                online=True,
                error=None,
                timer_enabled=True,
                schedule=SimpleNamespace(
                    enabled=True,
                    device_local_time=self.local_clock,
                ),
            )

        async def read_schedule_image(self) -> bytes:
            return bytes(48 * 9)

    digests, clocks = await asyncio.wait_for(
        cli._capture_schedule_context(  # noqa: SLF001
            {
                "master": Device("master", "01", clock),
                "slave": Device("slave", "02", clock),
            },
            ("master", "slave"),
        ),
        timeout=1,
    )

    assert started == ["master", "slave"]
    assert tuple(digest.device_id for digest in digests) == ("master", "slave")
    assert clocks == (clock, clock)

    started.clear()
    both_started = asyncio.Event()
    _digests, skewed = await cli._capture_schedule_context(  # noqa: SLF001
        {
            "master": Device("master", "01", clock),
            "slave": Device("slave", "02", clock + timedelta(seconds=3)),
        },
        ("master", "slave"),
    )
    with pytest.raises(cli.ScheduleFlowCliError, match="two-second skew"):
        cli._next_boundary(skewed)  # noqa: SLF001


async def test_schedule_context_waits_for_the_sibling_read_before_raising() -> None:
    sibling_started = asyncio.Event()
    sibling_completed = asyncio.Event()
    clock = datetime(2026, 8, 27, 12, 10, 1)

    class FailingDevice:
        device_id = "master"
        physical_binding = _binding("01")

        async def get_state(self):
            await sibling_started.wait()
            raise cli.ScheduleFlowCliError("simulated read failure")

        async def read_schedule_image(self) -> bytes:
            raise AssertionError("failed state must not continue to schedule read")

    class CompletingDevice:
        device_id = "slave"
        physical_binding = _binding("02")

        async def get_state(self):
            sibling_started.set()
            return SimpleNamespace(
                online=True,
                error=None,
                timer_enabled=True,
                schedule=SimpleNamespace(enabled=True, device_local_time=clock),
            )

        async def read_schedule_image(self) -> bytes:
            await asyncio.sleep(0)
            sibling_completed.set()
            return bytes(48 * 9)

    with pytest.raises(cli.ScheduleFlowCliError, match="simulated read failure"):
        await cli._capture_schedule_context(  # noqa: SLF001
            {"master": FailingDevice(), "slave": CompletingDevice()},
            ("master", "slave"),
        )

    assert sibling_completed.is_set()


def test_run_boundary_must_still_have_one_to_three_minutes_lead() -> None:
    cli._require_boundary_still_fresh(  # noqa: SLF001
        "12:14",
        (datetime(2026, 8, 27, 12, 11, 30), datetime(2026, 8, 27, 12, 11, 31)),
    )
    with pytest.raises(cli.ScheduleFlowCliError, match="no longer"):
        cli._require_boundary_still_fresh(  # noqa: SLF001
            "12:14",
            (datetime(2026, 8, 27, 12, 12, 1), datetime(2026, 8, 27, 12, 12, 2)),
        )


def test_v3_token_binds_full_spec_controls_and_exact_schedule_digests() -> None:
    intent = _intent()
    original = intent.confirmation_token
    changed_boundary = schedule_flow_confirmation_token(
        "main",
        _spec("12:35"),
        intent.snapshots,
        intent.schedule_image_digests,
    )
    changed_digests = list(intent.schedule_image_digests)
    changed_digests[1] = changed_digests[1].model_copy(
        update={"image_sha256": hashlib.sha256(b"changed").hexdigest()}
    )
    changed_image = schedule_flow_confirmation_token(
        "main",
        _spec(),
        intent.snapshots,
        changed_digests,
    )

    assert original.startswith("JFE-")
    assert original != changed_boundary != changed_image
    assert hardware_test_intent_confirmation_token(intent) == original
    encoded = intent.model_dump_json()
    assert "image_hex" not in encoded
    assert (b"\xee" * 432).hex() not in encoded


@pytest.mark.asyncio
async def test_stop_signal_cancels_core_but_waits_for_ordered_compensation() -> None:
    interrupt = asyncio.Event()
    entered = asyncio.Event()
    compensated = asyncio.Event()

    async def operation() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Model the controller's shielded inverse-order rollback.
            await asyncio.sleep(0)
            compensated.set()
            raise

    running = asyncio.create_task(
        cli._await_with_stop_signals(  # noqa: SLF001
            operation(),
            task_name="test-schedule-flow-stop",
            interrupt_event=interrupt,
        )
    )
    await entered.wait()
    interrupt.set()

    with pytest.raises(asyncio.CancelledError):
        await running

    assert compensated.is_set()


def test_v1_v2_preview_token_semantics_remain_unchanged() -> None:
    spec = _spec().outer_linkage_spec()
    snapshots = _snapshots()
    now = datetime.now(UTC)
    intent = HardwareTestIntent(
        version=2,
        instance_id="main",
        operation_id=spec.operation_id,
        phase=HardwareTestIntentPhase.ARMED,
        confirmation_token=preview_confirmation_token("main", spec, snapshots),
        spec=spec,
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        evidence=HardwareTestEvidence(),
    )

    assert hardware_test_intent_confirmation_token(intent) == preview_confirmation_token(
        "main", spec, snapshots
    )


def test_v3_intent_requires_full_spec_two_ordered_bound_digests() -> None:
    payload = _intent().model_dump(mode="python")
    payload["schedule_image_digests"] = payload["schedule_image_digests"][:1]

    with pytest.raises(ValidationError, match="spec and digests"):
        HardwareTestIntent.model_validate(payload)


def test_armed_v3_intent_rejects_outer_diagnostic_progress() -> None:
    payload = _intent().model_dump(mode="python")
    payload["evidence"] = HardwareTestEvidence(
        forward_failure=LinkageForwardFailureCategory.TRANSACTION_FAILED
    )

    with pytest.raises(ValidationError, match="armed intents cannot contain"):
        HardwareTestIntent.model_validate(payload)


def test_v3_stage_events_are_monotonic_bounded_and_identity_free() -> None:
    intent = _intent(phase=HardwareTestIntentPhase.STARTED)
    started = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.OUTER_PAUSE_STARTED,
        occurred_at=intent.created_at,
    )
    first_write = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.SENTINEL_WRITE_STARTED,
        occurred_at=intent.created_at + timedelta(microseconds=1),
        completed_participants=0,
    )
    first_verified = first_write.model_copy(
        update={
            "occurred_at": intent.created_at + timedelta(microseconds=2),
            "completed_participants": 1,
        }
    )
    validated = HardwareTestIntent.model_validate(
        intent.model_dump(mode="python")
        | {"schedule_flow_stage_events": (started, first_write, first_verified)}
    )

    assert validated.schedule_flow_stage_events[-1].completed_participants == 1
    encoded = validated.schedule_flow_stage_events[-1].model_dump_json()
    assert "device_id" not in encoded
    assert "physical_binding" not in encoded
    with pytest.raises(ValidationError):
        ScheduleFlowStageEvent.model_validate(
            started.model_dump(mode="python")
            | {"raw_exception": "secret 198.51.100.77"}
        )
    with pytest.raises(ValidationError, match="stages must be monotonic"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {
                "schedule_flow_stage_events": (
                    first_write,
                    started.model_copy(
                        update={
                            "occurred_at": intent.created_at
                            + timedelta(microseconds=4)
                        }
                    ),
                )
            }
        )

    failed = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.FIELD_WRITE_STARTED,
        occurred_at=intent.created_at + timedelta(microseconds=5),
        completed_participants=1,
        temporary_error_code=TemporaryScheduleErrorCode.STAGE_WRITE_FAILED,
    )
    restored = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.OUTER_RESTORED,
        occurred_at=intent.created_at + timedelta(microseconds=6),
    )
    terminal = HardwareTestIntent.model_validate(
        intent.model_dump(mode="python")
        | {
            "phase": HardwareTestIntentPhase.TERMINAL,
            "outcome": "experiment_failed_restored",
            "schedule_flow_stage_events": (failed, restored),
        }
    )
    assert terminal.schedule_flow_stage_events[-1].stage is ScheduleFlowStage.OUTER_RESTORED
    assert (
        terminal.schedule_flow_stage_events[-2].temporary_error_code
        is TemporaryScheduleErrorCode.STAGE_WRITE_FAILED
    )
    with pytest.raises(ValidationError, match="participant progress"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {
                "schedule_flow_stage_events": (
                    first_verified,
                    first_write.model_copy(
                        update={
                            "occurred_at": intent.created_at
                            + timedelta(microseconds=3)
                        }
                    ),
                )
            }
        )


def test_v3_role_progress_is_monotonic_in_model_and_append_path() -> None:
    intent = _intent(phase=HardwareTestIntentPhase.STARTED)
    first_at = intent.created_at + timedelta(microseconds=1)
    later_at = intent.created_at + timedelta(microseconds=2)
    later_progress = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
        occurred_at=first_at,
        role_progress=ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.AUTHORIZATION_COMPLETED,
            occurred_at=first_at,
        ),
    )
    regressed_progress = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
        occurred_at=later_at,
        role_progress=ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FRESH_CAPTURE_STARTED,
            occurred_at=later_at,
        ),
    )

    with pytest.raises(ValidationError, match="role progress must be monotonic"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {"schedule_flow_stage_events": (later_progress, regressed_progress)}
        )
    with pytest.raises(cli.ScheduleFlowCliError, match="role evidence regressed"):
        cli._append_schedule_stage_event(  # noqa: SLF001
            (later_progress,),
            regressed_progress,
        )


def test_terminal_stage_has_a_reserved_slot_and_coalesces_replay() -> None:
    intent = _intent(phase=HardwareTestIntentPhase.STARTED)
    failures = tuple(
        ScheduleFlowStageEvent(
            stage=ScheduleFlowStage.OUTER_RESTORE_STARTED,
            occurred_at=intent.created_at + timedelta(microseconds=index),
            failure_category=ScheduleFlowFailureCategory.OUTER_RESTORE,
        )
        for index in range(SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT)
    )
    terminal_event = ScheduleFlowStageEvent(
        stage=ScheduleFlowStage.OUTER_RESTORED,
        occurred_at=intent.created_at
        + timedelta(microseconds=SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT),
    )

    completed = cli._append_schedule_stage_event(failures, terminal_event)  # noqa: SLF001

    assert len(completed) == SCHEDULE_FLOW_STAGE_EVENT_LIMIT
    assert completed[:-1] == failures
    assert completed[-1] == terminal_event
    assert cli._append_schedule_stage_event(completed, terminal_event) == completed  # noqa: SLF001
    validated = HardwareTestIntent.model_validate(
        intent.model_dump(mode="python")
        | {
            "phase": HardwareTestIntentPhase.TERMINAL,
            "outcome": "experiment_failed_restored",
            "schedule_flow_stage_events": completed,
        }
    )
    assert validated.schedule_flow_stage_events[:-1] == failures
    with pytest.raises(cli.ScheduleFlowCliError, match="evidence is full"):
        cli._append_schedule_stage_event(failures, failures[-1])  # noqa: SLF001
    with pytest.raises(ValidationError, match="reserved schedule-flow event slot"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {"schedule_flow_stage_events": (*failures, failures[-1])}
        )


@pytest.mark.parametrize(
    ("slave_flow", "expected", "transition_verified"),
    (
        (40, ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED, True),
        (32, ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS, False),
        (35, ScheduleFlowOutcome.SLAVE_FLOW_FOLLOWED_MASTER, False),
        (37, ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE, False),
    ),
)
def test_v3_result_evidence_serializes_all_stable_classifications(
    slave_flow: int,
    expected: ScheduleFlowOutcome,
    transition_verified: bool,
) -> None:
    sample = _after_sample(slave_flow=slave_flow)
    intent = _intent(
        phase=HardwareTestIntentPhase.TERMINAL,
        outcome=expected.value,
        sample=sample,
        include_result=True,
    )

    assert intent.schedule_flow_outcome is expected
    assert intent.schedule_transition_verified is transition_verified
    assert intent.stable_slave_tuple_observed is True
    assert intent.stable_observation_seconds == 300
    assert HardwareTestIntent.model_validate_json(intent.model_dump_json()) == intent


def test_v3_result_evidence_rejects_classification_or_transition_mismatch() -> None:
    intent = _intent(
        phase=HardwareTestIntentPhase.TERMINAL,
        outcome=ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED.value,
        sample=_after_sample(),
        include_result=True,
    )

    with pytest.raises(ValidationError, match="transition flag"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {"schedule_transition_verified": False}
        )
    with pytest.raises(ValidationError, match="outcome disagrees"):
        HardwareTestIntent.model_validate(
            intent.model_dump(mode="python")
            | {"schedule_flow_outcome": ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS}
        )


class _IntentStore:
    def __init__(self, intent: HardwareTestIntent) -> None:
        self.intent = intent

    def load(self) -> HardwareTestIntent:
        return self.intent


def test_schedule_snapshot_authorizer_accepts_exact_images_and_rejects_one_byte_change() -> None:
    intent = _intent(phase=HardwareTestIntentPhase.STARTED)
    authorizer = cli._snapshot_authorizer(_IntentStore(intent), intent)  # noqa: SLF001
    images = tuple(
        ScheduleImageSnapshot.from_image(
            device_id=digest.device_id,
            physical_binding=digest.physical_binding,
            image=(b"\xee" * 432 if digest.device_id == "master" else b"\x00" * 432),
        )
        for digest in intent.schedule_image_digests
    )
    corrected = tuple(
        digest.model_copy(update={"image_sha256": image.image_sha256})
        for digest, image in zip(intent.schedule_image_digests, images, strict=True)
    )
    corrected_intent = intent.model_copy(update={"schedule_image_digests": corrected})
    authorizer = cli._snapshot_authorizer(  # noqa: SLF001
        _IntentStore(corrected_intent), corrected_intent
    )

    authorizer(_spec().temporary_schedule_spec(), images)
    changed = list(images)
    changed[1] = ScheduleImageSnapshot.from_image(
        device_id="slave",
        physical_binding=changed[1].physical_binding,
        image=b"\xee" * 432,
    )
    with pytest.raises(Exception, match="exact schedule image changed"):
        authorizer(_spec().temporary_schedule_spec(), tuple(changed))


class _OuterStore:
    def __init__(self) -> None:
        self.record = None

    def load(self):
        return self.record

    def lease(self):
        return nullcontext()

    def create(self, record) -> None:
        self.record = record

    def save(self, record) -> None:
        self.record = record

    def clear(self) -> None:
        self.record = None


def test_confirming_outer_store_uses_jfe_factory_for_fresh_control_snapshot() -> None:
    intent = _intent()
    delegate = _OuterStore()
    wrapper = ConfirmingLinkageJournalStore(
        delegate,  # type: ignore[arg-type]
        instance_id="main",
        expected_token=intent.confirmation_token,
        confirmation_token_factory=cli._outer_token_factory(intent),  # noqa: SLF001
    )
    now = datetime.now(UTC)
    record = LinkageTransactionRecord(
        operation_id=intent.operation_id,
        phase=LinkageTransactionPhase.PREPARED,
        spec=intent.spec,
        snapshots=intent.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=intent.spec.duration_seconds),
    )

    wrapper.create(record)
    assert delegate.record == record


def test_recovery_token_changes_with_sample_evidence_and_never_renders_identifiers() -> None:
    armed = _intent()
    started = _intent(
        phase=HardwareTestIntentPhase.STARTED,
        sample=_after_sample(),
    )

    first = cli.schedule_flow_recovery_token("main", armed, None, None, None)
    second = cli.schedule_flow_recovery_token("main", started, None, None, None)

    assert first.startswith("JFER-")
    assert first != second
    assert "master" not in first
    assert "slave" not in first


def test_status_is_sanitized_but_prints_effective_sample(capsys) -> None:
    intent = _intent(
        phase=HardwareTestIntentPhase.TERMINAL,
        outcome="per_slot_power_verified",
        sample=_after_sample(),
        include_result=True,
    )
    store = SimpleNamespace(load=lambda: intent)
    empty = SimpleNamespace(load=lambda: None)

    assert cli._status(  # noqa: SLF001
        SimpleNamespace(instance=SimpleNamespace(id="main")),
        store,
        empty,
        empty,
        empty,
    ) == 0
    output = capsys.readouterr().out
    assert "master=sine/35%" in output
    assert "slave=sine/40%" in output
    assert "Schedule transition verified: yes" in output
    assert "Stable slave tuple observed: yes" in output
    assert "Stable observation: 300s" in output
    assert "vendor-" not in output
    assert intent.schedule_image_digests[0].image_sha256 not in output


def test_status_treats_authentic_terminal_v2_as_no_schedule_flow(capsys) -> None:
    legacy = SimpleNamespace(load=lambda: _legacy_intent())
    empty = SimpleNamespace(load=lambda: None)

    assert cli._status(  # noqa: SLF001
        SimpleNamespace(instance=SimpleNamespace(id="main")),
        legacy,
        empty,
        empty,
        empty,
    ) == 0
    output = capsys.readouterr().out
    assert "One-shot intent: none" in output
    assert "Recovery confirmation token" not in output


def test_terminal_v2_can_be_replaced_by_new_preflight_but_nonterminal_cannot() -> None:
    cli._assert_existing_allows_preflight(  # noqa: SLF001
        _legacy_intent(),
        instance_id="main",
        operation_id="new_schedule_flow_operation",
    )
    with pytest.raises(cli.ScheduleFlowCliError, match="unfinished"):
        cli._assert_existing_allows_preflight(  # noqa: SLF001
            _legacy_intent(phase=HardwareTestIntentPhase.STARTED, outcome=None),
            instance_id="main",
            operation_id="new_schedule_flow_operation",
        )


def test_legacy_native_intent_with_any_schedule_flow_journal_fails_closed() -> None:
    intent_store = SimpleNamespace(load=lambda: _legacy_intent())
    outer = SimpleNamespace(load=lambda: object())
    empty = SimpleNamespace(load=lambda: None)

    with pytest.raises(cli.ScheduleFlowCliError, match="unrelated native-linkage"):
        cli._load_recovery_state(  # noqa: SLF001
            SimpleNamespace(instance=SimpleNamespace(id="main")),
            intent_store,
            outer,
            empty,
            empty,
        )


def test_nonterminal_legacy_native_intent_without_journal_fails_closed() -> None:
    intent_store = SimpleNamespace(
        load=lambda: _legacy_intent(phase=HardwareTestIntentPhase.STARTED, outcome=None)
    )
    empty = SimpleNamespace(load=lambda: None)

    with pytest.raises(cli.ScheduleFlowCliError, match="unrelated native-linkage"):
        cli._load_recovery_state(  # noqa: SLF001
            SimpleNamespace(instance=SimpleNamespace(id="main")),
            intent_store,
            empty,
            empty,
            empty,
        )


def _sentinel_only_recovery_state(
    kind: TemporaryScheduleKind,
) -> tuple[HardwareTestIntent, LinkageTransactionRecord, TemporaryScheduleRecord]:
    armed = _intent(
        phase=HardwareTestIntentPhase.STARTED,
        sentinel_only=True,
    )
    images = (
        ScheduleImageSnapshot.from_image(
            device_id="master",
            physical_binding=armed.snapshots[0].physical_binding,
            image=bytes(432),
        ),
        ScheduleImageSnapshot.from_image(
            device_id="slave",
            physical_binding=armed.snapshots[1].physical_binding,
            image=b"\xee" * 432,
        ),
    )
    digests = tuple(
        digest.model_copy(update={"image_sha256": image.image_sha256})
        for digest, image in zip(armed.schedule_image_digests, images, strict=True)
    )
    intent = armed.model_copy(update={"schedule_image_digests": digests})
    intent = intent.model_copy(
        update={"confirmation_token": hardware_test_intent_confirmation_token(intent)}
    )
    intent = HardwareTestIntent.model_validate(intent.model_dump(mode="python"))
    now = datetime.now(UTC)
    outer = LinkageTransactionRecord(
        operation_id=intent.operation_id,
        phase=LinkageTransactionPhase.APPLYING,
        spec=intent.spec,
        snapshots=intent.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=intent.spec.duration_seconds),
    )
    flow_spec = intent.schedule_flow_spec
    assert flow_spec is not None
    if kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION:
        temporary_spec = TemporaryScheduleSpec(
            operation_id=f"{intent.operation_id}_sentinel",
            kind=kind,
            device_patches=tuple(
                DeviceSchedulePatch(
                    device_id=image.device_id,
                    slots=(behavior_neutral_unused_slot_patch(image.image_bytes),),
                )
                for image in images
            ),
        )
    else:
        temporary_spec = flow_spec.temporary_schedule_spec()
    temporary = TemporaryScheduleRecord(
        operation_id=temporary_spec.operation_id,
        phase=TemporarySchedulePhase.PREPARED,
        spec=temporary_spec,
        snapshots=images,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=temporary_spec.recovery_authority_seconds),
    )
    return intent, outer, temporary


def test_sentinel_only_recovery_rejects_field_schedule_journal() -> None:
    intent, outer, temporary = _sentinel_only_recovery_state(
        TemporaryScheduleKind.FIELD_OBSERVATION
    )

    with pytest.raises(cli.ScheduleFlowCliError, match="field schedule journal"):
        cli._load_recovery_state(  # noqa: SLF001
            SimpleNamespace(instance=SimpleNamespace(id="main")),
            SimpleNamespace(load=lambda: intent),
            SimpleNamespace(load=lambda: outer),
            SimpleNamespace(load=lambda: temporary),
            SimpleNamespace(load=lambda: None),
        )


def test_sentinel_only_recovery_rejects_field_and_role_journals() -> None:
    intent, outer, temporary = _sentinel_only_recovery_state(
        TemporaryScheduleKind.FIELD_OBSERVATION
    )
    flow_spec = intent.schedule_flow_spec
    assert flow_spec is not None
    role = SimpleNamespace(
        operation_id=f"{intent.operation_id}_roles",
        spec=flow_spec.role_observation_spec(),
        snapshots=tuple(
            SimpleNamespace(
                device_id=snapshot.device_id,
                physical_binding=snapshot.physical_binding,
            )
            for snapshot in intent.snapshots
        ),
    )

    with pytest.raises(cli.ScheduleFlowCliError, match="role journal"):
        cli._load_recovery_state(  # noqa: SLF001
            SimpleNamespace(instance=SimpleNamespace(id="main")),
            SimpleNamespace(load=lambda: intent),
            SimpleNamespace(load=lambda: outer),
            SimpleNamespace(load=lambda: temporary),
            SimpleNamespace(load=lambda: role),
        )


def test_sentinel_only_recovery_accepts_its_legitimate_sentinel_journal() -> None:
    intent, outer, temporary = _sentinel_only_recovery_state(
        TemporaryScheduleKind.SENTINEL_QUALIFICATION
    )

    loaded = cli._load_recovery_state(  # noqa: SLF001
        SimpleNamespace(instance=SimpleNamespace(id="main")),
        SimpleNamespace(load=lambda: intent),
        SimpleNamespace(load=lambda: outer),
        SimpleNamespace(load=lambda: temporary),
        SimpleNamespace(load=lambda: None),
    )

    assert loaded == (intent, outer, temporary, None)


@pytest.mark.parametrize("mutation", ("original_image", "field_spec"))
def test_v3_recovery_rejects_nested_authority_not_bound_to_confirmed_intent(
    mutation: str,
) -> None:
    armed = _intent(phase=HardwareTestIntentPhase.STARTED)
    images = tuple(
        ScheduleImageSnapshot.from_image(
            device_id=digest.device_id,
            physical_binding=digest.physical_binding,
            image=(b"\xee" * 432 if digest.device_id == "master" else b"\x00" * 432),
        )
        for digest in armed.schedule_image_digests
    )
    digests = tuple(
        digest.model_copy(update={"image_sha256": image.image_sha256})
        for digest, image in zip(armed.schedule_image_digests, images, strict=True)
    )
    intent = armed.model_copy(update={"schedule_image_digests": digests})
    intent = intent.model_copy(
        update={"confirmation_token": hardware_test_intent_confirmation_token(intent)}
    )
    intent = HardwareTestIntent.model_validate(intent.model_dump(mode="python"))
    now = datetime.now(UTC)
    outer = LinkageTransactionRecord(
        operation_id=intent.operation_id,
        phase=LinkageTransactionPhase.PREPARED,
        spec=intent.spec,
        snapshots=intent.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=intent.spec.duration_seconds),
    )
    schedule_spec = _spec().temporary_schedule_spec()
    if mutation == "field_spec":
        schedule_spec = schedule_spec.model_copy(update={"restore_timeout_seconds": 91})
    nested_images = images
    if mutation == "original_image":
        nested_images = (
            images[0],
            ScheduleImageSnapshot.from_image(
                device_id=images[1].device_id,
                physical_binding=images[1].physical_binding,
                image=b"\xee" * 432,
            ),
        )
    temporary = TemporaryScheduleRecord(
        operation_id=schedule_spec.operation_id,
        phase=TemporarySchedulePhase.PREPARED,
        spec=schedule_spec,
        snapshots=nested_images,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=schedule_spec.recovery_authority_seconds),
    )
    stores = (
        SimpleNamespace(load=lambda: intent),
        SimpleNamespace(load=lambda: outer),
        SimpleNamespace(load=lambda: temporary),
        SimpleNamespace(load=lambda: None),
    )

    with pytest.raises(cli.ScheduleFlowCliError, match="confirmed"):
        cli._load_recovery_state(  # noqa: SLF001
            SimpleNamespace(instance=SimpleNamespace(id="main")),
            *stores,
        )


def test_schedule_linkage_conflict_reader_accepts_authentic_v3_terminal(tmp_path) -> None:
    intent = _intent(
        phase=HardwareTestIntentPhase.TERMINAL,
        outcome="per_slot_power_verified",
        sample=_after_sample(),
        include_result=True,
    )
    path = tmp_path / "native-linkage-intent.json"
    path.write_text(intent.model_dump_json(), encoding="utf-8")
    os.chmod(path, 0o600)

    assert (
        schedule_linkage_cli._read_other_intent_phase(  # noqa: SLF001
            path,
            label="native intent",
            workflow="native",
        )
        == "terminal"
    )


def test_parser_requires_boundary_and_confirmation_for_run() -> None:
    args = cli.build_parser().parse_args(
        [
            "run",
            "--operation-id",
            "scheduled_flow_001",
            "--qualification-operation-id",
            "qualified_pair_001",
            "--master",
            "master",
            "--slave",
            "slave",
            "--boundary-time",
            "12:34",
            "--confirm",
            "JFE-0123456789ABCDEF0123",
        ]
    )

    assert args.command == "run"
    assert cli._spec_from_run_args(args) == _spec()  # noqa: SLF001


class _MutableIntentStore:
    def __init__(self, intent: HardwareTestIntent | None = None) -> None:
        self.intent = intent

    def load(self):
        return self.intent

    def save(self, intent) -> None:
        self.intent = intent


class _LeaseFactory:
    @classmethod
    def from_selected(cls, _config, _selected):
        return cls()

    def acquire(self):
        return nullcontext()


@asynccontextmanager
async def _connected(_devices):
    yield


@pytest.mark.asyncio
async def test_preflight_persists_full_v3_intent_without_writes(monkeypatch, capsys) -> None:
    intent_store = _MutableIntentStore(_legacy_intent(operation_id="old_native_operation"))
    outer_store = _OuterStore()
    config = SimpleNamespace(instance=SimpleNamespace(id="main"))
    args = SimpleNamespace(
        operation_id="scheduled_flow_001",
        qualification_operation_id="qualified_pair_001",
        master="master",
        slave="slave",
    )
    monkeypatch.setattr(cli, "_assert_no_verification_conflict", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_validate_config",
        lambda _config, _ids: {"master": object(), "slave": object()},
    )
    monkeypatch.setattr(cli, "PhysicalDeviceLease", _LeaseFactory)
    monkeypatch.setattr(cli, "_safety_latch_present", lambda _path: False)

    async def build(_config, _selected, *, writable):
        assert writable is False
        return {"master": object(), "slave": object()}

    async def capture_preview(_devices, spec):
        assert spec == _spec().outer_linkage_spec()
        return _snapshots()

    async def capture_context(_devices, _device_ids):
        return _digests(), (
            datetime(2026, 8, 27, 12, 10, 1),
            datetime(2026, 8, 27, 12, 10, 2),
        )

    monkeypatch.setattr(cli, "_build_devices", build)
    monkeypatch.setattr(cli, "_connected", _connected)
    monkeypatch.setattr(cli, "_require_plan_supported", lambda _devices, _spec: None)
    monkeypatch.setattr(cli, "_capture_preview", capture_preview)
    monkeypatch.setattr(cli, "_capture_schedule_context", capture_context)
    monkeypatch.setattr(cli, "_require_receipts", lambda *_args: None)

    assert await cli._preflight(  # noqa: SLF001
        config,
        args,
        intent_store,
        outer_store,
        SimpleNamespace(),
    ) == 0
    saved = intent_store.load()
    assert saved is not None
    assert saved.version == 3
    assert saved.phase is HardwareTestIntentPhase.ARMED
    assert saved.schedule_flow_spec.boundary_time == "12:14"
    assert saved.schedule_image_digests == _digests()
    output = capsys.readouterr().out
    assert "no control or schedule frame was sent" in output
    assert _digests()[0].image_sha256 not in output


class _Guard:
    permitted = True

    def clear(self) -> None:
        self.permitted = True


@pytest.mark.asyncio
async def test_sentinel_only_terminal_is_durable_before_outer_clear(
    monkeypatch,
    capsys,
) -> None:
    armed = _intent(sentinel_only=True)
    intent_store = _MutableIntentStore(armed)
    clear_observations: list[str] = []

    class TerminalCheckingOuterStore(_OuterStore):
        def clear(self) -> None:
            terminal = intent_store.load()
            assert terminal.phase is HardwareTestIntentPhase.TERMINAL
            assert terminal.outcome == "wire_qualified"
            assert terminal.schedule_flow_outcome is None
            assert terminal.schedule_flow_sample is None
            clear_observations.append("terminal-before-outer-clear")
            super().clear()

    outer_store = TerminalCheckingOuterStore()
    schedule_store = _OuterStore()
    role_store = _OuterStore()
    config = SimpleNamespace(instance=SimpleNamespace(id="main"))
    args = SimpleNamespace(
        operation_id="scheduled_flow_001",
        qualification_operation_id="qualified_pair_001",
        master="master",
        slave="slave",
        boundary_time="12:34",
        confirm=armed.confirmation_token,
        sentinel_only=True,
    )
    monkeypatch.setattr(cli, "_assert_no_verification_conflict", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_validate_config",
        lambda _config, _ids: {"master": object(), "slave": object()},
    )
    monkeypatch.setattr(cli, "PhysicalDeviceLease", _LeaseFactory)
    monkeypatch.setattr(cli, "_safety_latch_present", lambda _path: False)
    monkeypatch.setattr(cli, "_require_receipts", lambda *_args: None)
    monkeypatch.setattr(cli, "_require_plan_supported", lambda *_args: None)

    async def build(_config, _selected, *, writable):
        assert writable is True
        return {"master": object(), "slave": object()}

    async def capture_context(_devices, _device_ids):
        return armed.schedule_image_digests, (
            datetime(2026, 8, 27, 12, 31, 1),
            datetime(2026, 8, 27, 12, 31, 2),
        )

    monkeypatch.setattr(cli, "_build_devices", build)
    monkeypatch.setattr(cli, "_connected", _connected)
    monkeypatch.setattr(cli, "_capture_schedule_context", capture_context)

    class Controller:
        def __init__(self, _devices, outer, *_args, stage_event_observer, **_kwargs):
            self.outer = outer
            self.observe_stage = stage_event_observer
            self.last_role_sample = None
            self.last_role_result = None
            self.wire_qualification_verified = True

        async def run_experiment(self, spec):
            now = datetime.now(UTC)
            for index, stage in enumerate(
                (
                    ScheduleFlowStage.OUTER_PAUSE_STARTED,
                    ScheduleFlowStage.OUTER_PAUSE_COMPLETED,
                    ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED,
                    ScheduleFlowStage.SENTINEL_SNAPSHOT_COMPLETED,
                    ScheduleFlowStage.SENTINEL_WRITE_STARTED,
                    ScheduleFlowStage.SENTINEL_VERIFIED,
                    ScheduleFlowStage.SENTINEL_RESTORE_STARTED,
                    ScheduleFlowStage.SENTINEL_RESTORED,
                    ScheduleFlowStage.OUTER_RESTORE_STARTED,
                )
            ):
                self.observe_stage(
                    ScheduleFlowStageEvent(
                        stage=stage,
                        occurred_at=now + timedelta(microseconds=index),
                        completed_participants=(
                            2
                            if stage
                            in {
                                ScheduleFlowStage.SENTINEL_VERIFIED,
                                ScheduleFlowStage.SENTINEL_RESTORED,
                            }
                            else None
                        ),
                    )
                )
            self.outer.clear()
            return ScheduleFlowExperimentResult(
                operation_id=spec.operation_id,
                sentinel_qualified=True,
                outcome="wire_qualified",
                last_after_sample=None,
                schedule_transition_verified=False,
                stable_slave_tuple_observed=False,
                stable_observation_seconds=0,
                completed_at=datetime.now(UTC),
            )

    monkeypatch.setattr(cli, "ScheduleFlowExperimentController", Controller)

    assert await cli._run(  # noqa: SLF001
        config,
        args,
        intent_store,
        outer_store,
        schedule_store,
        role_store,
        SimpleNamespace(),
        _Guard(),
    ) == 0

    terminal = intent_store.load()
    terminal = HardwareTestIntent.model_validate(terminal.model_dump(mode="python"))
    assert clear_observations == ["terminal-before-outer-clear"]
    assert terminal.outcome == "wire_qualified"
    stages = tuple(event.stage for event in terminal.schedule_flow_stage_events)
    assert ScheduleFlowStage.SENTINEL_WRITE_STARTED in stages
    assert ScheduleFlowStage.SENTINEL_RESTORED in stages
    assert ScheduleFlowStage.OUTER_RESTORE_STARTED in stages
    assert stages[-1] is ScheduleFlowStage.OUTER_RESTORED
    assert not any(
        stage.value.startswith(("field_", "timer_on_", "role_")) for stage in stages
    )
    assert "wire_qualified" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_durably_records_negative_stable_outcome_before_outer_clear(
    monkeypatch,
    capsys,
) -> None:
    armed = _intent()
    intent_store = _MutableIntentStore(armed)
    outer_store = _OuterStore()
    schedule_store = _OuterStore()
    role_store = _OuterStore()
    config = SimpleNamespace(instance=SimpleNamespace(id="main"))
    args = SimpleNamespace(
        operation_id="scheduled_flow_001",
        qualification_operation_id="qualified_pair_001",
        master="master",
        slave="slave",
        boundary_time="12:34",
        confirm=armed.confirmation_token,
    )
    monkeypatch.setattr(cli, "_assert_no_verification_conflict", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_validate_config",
        lambda _config, _ids: {"master": object(), "slave": object()},
    )
    monkeypatch.setattr(cli, "PhysicalDeviceLease", _LeaseFactory)
    monkeypatch.setattr(cli, "_safety_latch_present", lambda _path: False)
    monkeypatch.setattr(cli, "_require_receipts", lambda *_args: None)
    monkeypatch.setattr(cli, "_require_plan_supported", lambda *_args: None)

    async def build(_config, _selected, *, writable):
        assert writable is True
        return {"master": object(), "slave": object()}

    async def capture_context(_devices, _device_ids):
        return armed.schedule_image_digests, (
            datetime(2026, 8, 27, 12, 31, 1),
            datetime(2026, 8, 27, 12, 31, 2),
        )

    monkeypatch.setattr(cli, "_build_devices", build)
    monkeypatch.setattr(cli, "_connected", _connected)
    monkeypatch.setattr(cli, "_capture_schedule_context", capture_context)

    class Controller:
        def __init__(
            self,
            _devices,
            outer,
            *_args,
            role_sample_observer,
            stage_event_observer,
            **_kwargs,
        ):
            self.outer = outer
            self.observe = role_sample_observer
            self.observe_stage = stage_event_observer
            self.last_role_sample = None
            self.last_role_result = None

        async def run_experiment(self, spec):
            now = datetime.now(UTC)
            for index, stage in enumerate(
                (
                    ScheduleFlowStage.OUTER_PAUSE_STARTED,
                    ScheduleFlowStage.OUTER_PAUSE_COMPLETED,
                    ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED,
                    ScheduleFlowStage.SENTINEL_SNAPSHOT_COMPLETED,
                    ScheduleFlowStage.SENTINEL_WRITE_STARTED,
                    ScheduleFlowStage.SENTINEL_VERIFIED,
                    ScheduleFlowStage.SENTINEL_RESTORE_STARTED,
                    ScheduleFlowStage.SENTINEL_RESTORED,
                    ScheduleFlowStage.FIELD_SNAPSHOT_STARTED,
                    ScheduleFlowStage.FIELD_SNAPSHOT_COMPLETED,
                    ScheduleFlowStage.FIELD_WRITE_STARTED,
                    ScheduleFlowStage.FIELD_VERIFIED,
                    ScheduleFlowStage.TIMER_ON_ARM_STARTED,
                    ScheduleFlowStage.TIMER_ON_ARMED,
                    ScheduleFlowStage.ROLE_PREFLIGHT_STARTED,
                    ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
                    ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
                    ScheduleFlowStage.ROLE_OBSERVATION_COMPLETED,
                    ScheduleFlowStage.ROLE_DISARM_STARTED,
                    ScheduleFlowStage.ROLE_DISARMED,
                    ScheduleFlowStage.FIELD_RESTORE_STARTED,
                    ScheduleFlowStage.FIELD_RESTORED,
                    ScheduleFlowStage.OUTER_RESTORE_STARTED,
                )
            ):
                self.observe_stage(
                    ScheduleFlowStageEvent(
                        stage=stage,
                        occurred_at=now + timedelta(microseconds=index),
                    )
                )
            sample = _after_sample(slave_flow=32)
            self.last_role_sample = sample
            self.last_role_result = SimpleNamespace(schedule_transition_verified=True)
            self.observe(sample)
            self.outer.clear()
            return ScheduleFlowExperimentResult(
                operation_id=spec.operation_id,
                sentinel_qualified=True,
                outcome=ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS,
                last_after_sample=sample,
                schedule_transition_verified=False,
                stable_slave_tuple_observed=True,
                stable_observation_seconds=300,
                completed_at=datetime.now(UTC),
            )

    monkeypatch.setattr(cli, "ScheduleFlowExperimentController", Controller)

    assert await cli._run(  # noqa: SLF001
        config,
        args,
        intent_store,
        outer_store,
        schedule_store,
        role_store,
        SimpleNamespace(),
        _Guard(),
    ) == 0
    terminal = intent_store.load()
    assert terminal.phase is HardwareTestIntentPhase.TERMINAL
    assert terminal.schedule_flow_outcome is ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS
    assert terminal.schedule_transition_verified is False
    assert terminal.stable_slave_tuple_observed is True
    assert terminal.stable_observation_seconds == 300
    assert terminal.schedule_flow_sample.slave.flow == 32
    assert terminal.schedule_flow_stage_events[-1].stage is ScheduleFlowStage.OUTER_RESTORED
    retained_stages = {event.stage for event in terminal.schedule_flow_stage_events}
    assert {
        ScheduleFlowStage.OUTER_PAUSE_COMPLETED,
        ScheduleFlowStage.TIMER_ON_ARMED,
        ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
        ScheduleFlowStage.ROLE_OBSERVATION_COMPLETED,
        ScheduleFlowStage.ROLE_DISARMED,
    } <= retained_stages
    output = capsys.readouterr().out
    assert "slave_flow_fixed_at_previous" in output
    assert "Stable observation: 300s" in output


@pytest.mark.asyncio
async def test_pause_failure_durably_records_outer_category_and_completed_restore(
    monkeypatch,
) -> None:
    armed = _intent()

    class FailFirstDiagnosticSave(_MutableIntentStore):
        failed_once = False

        def save(self, intent) -> None:
            evidence = intent.evidence
            if (
                not self.failed_once
                and evidence is not None
                and evidence.forward_failure is not None
                and evidence.rollback_started_at is None
            ):
                self.failed_once = True
                raise OSError("raw evidence persistence error")
            super().save(intent)

    intent_store = FailFirstDiagnosticSave(armed)
    outer_store = _OuterStore()
    schedule_store = _OuterStore()
    role_store = _OuterStore()
    config = SimpleNamespace(instance=SimpleNamespace(id="main"))
    args = SimpleNamespace(
        operation_id="scheduled_flow_001",
        qualification_operation_id="qualified_pair_001",
        master="master",
        slave="slave",
        boundary_time="12:34",
        confirm=armed.confirmation_token,
    )
    monkeypatch.setattr(cli, "_assert_no_verification_conflict", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_validate_config",
        lambda _config, _ids: {"master": object(), "slave": object()},
    )
    monkeypatch.setattr(cli, "PhysicalDeviceLease", _LeaseFactory)
    monkeypatch.setattr(cli, "_safety_latch_present", lambda _path: False)
    monkeypatch.setattr(cli, "_require_receipts", lambda *_args: None)
    monkeypatch.setattr(cli, "_require_plan_supported", lambda *_args: None)

    async def build(_config, _selected, *, writable):
        assert writable is True
        return {"master": object(), "slave": object()}

    async def capture_context(_devices, _device_ids):
        return armed.schedule_image_digests, (
            datetime(2026, 8, 27, 12, 31, 1),
            datetime(2026, 8, 27, 12, 31, 2),
        )

    monkeypatch.setattr(cli, "_build_devices", build)
    monkeypatch.setattr(cli, "_connected", _connected)
    monkeypatch.setattr(cli, "_capture_schedule_context", capture_context)

    class Controller:
        def __init__(
            self,
            _devices,
            outer,
            *_args,
            diagnostic_event_observer,
            stage_event_observer,
            **_kwargs,
        ):
            self.outer = outer
            self.diagnostic_event_observer = diagnostic_event_observer
            self.stage_event_observer = stage_event_observer
            self.last_role_sample = None
            self.last_role_result = None

        async def run_experiment(self, _spec):
            now = datetime.now(UTC)
            self.stage_event_observer(
                ScheduleFlowStageEvent(
                    stage=ScheduleFlowStage.OUTER_PAUSE_STARTED,
                    occurred_at=now,
                    failure_category=ScheduleFlowFailureCategory.OUTER_PAUSE,
                )
            )
            try:
                self.diagnostic_event_observer(
                    LinkageDiagnosticEvent(
                        kind=LinkageDiagnosticEventKind.FORWARD_FAILED,
                        occurred_at=now,
                        forward_failure=(
                            LinkageForwardFailureCategory.CONTROL_STATE_MISMATCH
                        ),
                    )
                )
            except OSError:
                # The real core's forward diagnostic hook is best-effort so rollback continues.
                pass
            self.diagnostic_event_observer(
                LinkageDiagnosticEvent(
                    kind=LinkageDiagnosticEventKind.ROLLBACK_STARTED,
                    occurred_at=now + timedelta(microseconds=1),
                )
            )
            self.outer.clear()
            raise RuntimeError(
                "vendor-master-secret 198.51.100.77 raw pause exception"
            )

    monkeypatch.setattr(cli, "ScheduleFlowExperimentController", Controller)

    with pytest.raises(RuntimeError, match="raw pause exception"):
        await cli._run(  # noqa: SLF001
            config,
            args,
            intent_store,
            outer_store,
            schedule_store,
            role_store,
            SimpleNamespace(),
            _Guard(),
        )

    terminal = intent_store.load()
    assert terminal is not None
    assert terminal.phase is HardwareTestIntentPhase.TERMINAL
    assert terminal.outcome == "experiment_failed_restored"
    assert terminal.evidence is not None
    assert (
        terminal.evidence.forward_failure
        is LinkageForwardFailureCategory.CONTROL_STATE_MISMATCH
    )
    assert terminal.evidence.rollback_started_at is not None
    assert terminal.evidence.rollback_completed_at is not None
    assert terminal.schedule_flow_stage_events[-1].stage is ScheduleFlowStage.OUTER_RESTORED
    assert any(
        event.failure_category is ScheduleFlowFailureCategory.OUTER_PAUSE
        for event in terminal.schedule_flow_stage_events
    )
    assert intent_store.failed_once is True
    encoded = terminal.model_dump_json()
    assert "vendor-master-secret" not in encoded
    assert "198.51.100.77" not in encoded
    assert "raw pause exception" not in encoded


@pytest.mark.asyncio
async def test_outer_absent_v3_diagnostics_cannot_be_closed_as_a_no_write_crash(
    monkeypatch,
) -> None:
    started = _intent(phase=HardwareTestIntentPhase.STARTED).model_copy(
        update={
            "evidence": HardwareTestEvidence(
                forward_failure=LinkageForwardFailureCategory.TRANSACTION_FAILED
            )
        }
    )
    intent_store = _MutableIntentStore(started)
    empty_stores = (_OuterStore(), _OuterStore(), _OuterStore())
    token = cli.schedule_flow_recovery_token("main", started, None, None, None)
    config = SimpleNamespace(instance=SimpleNamespace(id="main"))
    monkeypatch.setattr(cli, "_assert_no_verification_conflict", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_validate_config",
        lambda _config, _ids: {"master": object(), "slave": object()},
    )
    monkeypatch.setattr(cli, "PhysicalDeviceLease", _LeaseFactory)

    with pytest.raises(cli.ScheduleFlowCliError, match="diagnostic evidence"):
        await cli._recover(  # noqa: SLF001
            config,
            token,
            intent_store,
            *empty_stores,
            SimpleNamespace(),
            _Guard(),
        )

    assert intent_store.load() == started


@pytest.mark.asyncio
async def test_recover_uses_composed_order_and_terminalizes_before_outer_clear(
    monkeypatch,
    capsys,
) -> None:
    base = _intent(phase=HardwareTestIntentPhase.STARTED)
    retained_failures = tuple(
        ScheduleFlowStageEvent(
            stage=ScheduleFlowStage.OUTER_RESTORE_STARTED,
            occurred_at=base.created_at,
            failure_category=ScheduleFlowFailureCategory.OUTER_RESTORE,
        )
        for _ in range(SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT)
    )
    started = HardwareTestIntent.model_validate(
        base.model_dump(mode="python")
        | {"schedule_flow_stage_events": retained_failures}
    )
    now = datetime.now(UTC)
    outer_record = LinkageTransactionRecord(
        operation_id=started.operation_id,
        phase=LinkageTransactionPhase.PREPARED,
        spec=started.spec,
        snapshots=started.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=started.spec.duration_seconds),
    )
    intent_store = _MutableIntentStore(started)
    outer_store = _OuterStore()
    outer_store.record = outer_record
    schedule_store = _OuterStore()
    role_store = _OuterStore()
    token = cli.schedule_flow_recovery_token(
        "main", started, outer_record, None, None
    )
    config = SimpleNamespace(instance=SimpleNamespace(id="main"))
    monkeypatch.setattr(cli, "_assert_no_verification_conflict", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_validate_config",
        lambda _config, _ids: {"master": object(), "slave": object()},
    )
    monkeypatch.setattr(cli, "PhysicalDeviceLease", _LeaseFactory)
    monkeypatch.setattr(cli, "_safety_latch_present", lambda _path: False)

    async def build(_config, _selected, *, writable):
        assert writable is True
        return {"master": object(), "slave": object()}

    monkeypatch.setattr(cli, "_build_devices", build)
    monkeypatch.setattr(cli, "_connected", _connected)

    class RecoverController:
        def __init__(self, _devices, outer, *_args, **_kwargs):
            self.outer = outer

        async def recover_experiment(self):
            self.outer.clear()
            return True

    monkeypatch.setattr(cli, "ScheduleFlowExperimentController", RecoverController)

    assert await cli._recover(  # noqa: SLF001
        config,
        token,
        intent_store,
        outer_store,
        schedule_store,
        role_store,
        SimpleNamespace(),
        _Guard(),
    ) == 0
    terminal = intent_store.load()
    assert terminal.phase is HardwareTestIntentPhase.TERMINAL
    assert terminal.outcome == "recovered"
    assert terminal.schedule_flow_stage_events[:-1] == retained_failures
    assert terminal.schedule_flow_stage_events[-1].stage is ScheduleFlowStage.OUTER_RESTORED
    assert len(terminal.schedule_flow_stage_events) == SCHEDULE_FLOW_STAGE_EVENT_LIMIT
    assert outer_store.load() is None
    assert "restored in order" in capsys.readouterr().out
