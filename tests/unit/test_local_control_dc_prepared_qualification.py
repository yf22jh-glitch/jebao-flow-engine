"""Offline tests for the prepared DC qualification ordering contract.

These assert planning and journal ordering only. Nothing here claims the firmware adopts a
latent value, applies a timer command atomically, or accepts any write.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.dc_prepared_qualification import (
    DcPreparedQualificationIntent,
    DcPreparedScalarQualificationProducer,
    plan_dc_prepared_actions,
)
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.journal import ActionPhase, LocalControlJournalStore, TransactionPhase
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.qualification import (
    load_current_qualifications,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationEpoch,
)
from jebao_flow.local_control.resolver import CurrentEndpointResolutionError, ResolvedEndpoint
from jebao_flow.protocol.profiles import DC_PUMP_PRO
from tests.unit.test_local_control_qualification_producer import _dc_status

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
DC = DC_PUMP_PRO.product_key


def _binding(marker: str = "d") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="dc_target",
        product_key=DC,
        physical_identity_key=marker * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


def _plan(**overrides):
    arguments = {
        "operation_id": "dc_prepared_0001",
        "target": _binding(),
        "function": "manual_flow",
        "original_flow": 81,
        "original_mode": "pulse",
        "original_value": 81,
        "preparation_flow": 40,
        "trial_baseline_value": 40,
        "trial_value": 39,
        "timer_initially_enabled": True,
    }
    arguments.update(overrides)
    return plan_dc_prepared_actions(**arguments)


def test_timer_on_plan_arranges_preparation_trial_and_restoration_in_that_order() -> None:
    plan = _plan()
    assert [action.action_id for action in plan.actions] == [
        "prepare_flow",
        "prepare_mode",
        "enter_manual",
        "trial_step",
        "trial_restore",
        "restore_timer",
        "restore_mode",
        "restore_flow",
    ]
    assert [action.intent.value for action in plan.actions] == [
        40,
        "constant",
        True,
        39,
        40,
        True,
        "pulse",
        81,
    ]
    assert plan.receipt_indexes == (3, 4)


def test_the_trial_restore_returns_to_the_prepared_value_and_never_to_the_original() -> None:
    """Writing the original output while the timer is off is the hazard this ordering avoids."""

    plan = _plan()
    restore = next(a for a in plan.actions if a.action_id == "trial_restore")
    assert restore.intent.value == 40
    original = next(a for a in plan.actions if a.action_id == "restore_flow")
    assert original.intent.value == 81
    assert plan.actions.index(restore) < plan.actions.index(
        next(a for a in plan.actions if a.action_id == "restore_timer")
    )
    assert plan.actions.index(original) > plan.actions.index(
        next(a for a in plan.actions if a.action_id == "restore_timer")
    )


def test_recovery_restores_the_timer_first_and_names_it_a_prerequisite() -> None:
    plan = _plan()
    timer_index = [a.action_id for a in plan.actions].index("restore_timer")
    assert plan.recovery_order[0] == timer_index
    assert plan.recovery_prerequisites == (timer_index,)
    assert sorted(plan.recovery_order) == list(range(len(plan.actions)))
    original_index = [a.action_id for a in plan.actions].index("restore_flow")
    assert plan.recovery_order.index(timer_index) < plan.recovery_order.index(original_index)


def test_no_planned_frame_ever_turns_the_timer_off_except_the_owned_entry() -> None:
    for timer_initially_enabled in (True, False):
        plan = _plan(timer_initially_enabled=timer_initially_enabled)
        timer_frames = [
            action
            for action in plan.actions
            if action.intent.function in {"schedule_enable", "manual_enable"}
        ]
        for action in timer_frames:
            assert action.intent.value is True
        assert [a.action_id for a in timer_frames] == (
            ["enter_manual", "restore_timer"] if timer_initially_enabled else []
        )


def test_a_device_found_with_its_timer_off_needs_no_timer_frames() -> None:
    plan = _plan(timer_initially_enabled=False)
    assert [action.action_id for action in plan.actions] == [
        "prepare_flow",
        "prepare_mode",
        "trial_step",
        "trial_restore",
        "restore_mode",
        "restore_flow",
    ]
    mode_index = [a.action_id for a in plan.actions].index("restore_mode")
    assert plan.recovery_prerequisites == (mode_index,)
    flow_index = [a.action_id for a in plan.actions].index("restore_flow")
    assert plan.recovery_order[0] == mode_index
    assert plan.recovery_order.index(mode_index) < plan.recovery_order.index(flow_index)


def test_preparation_frames_are_omitted_when_the_device_already_matches() -> None:
    plan = _plan(
        function="feeding_duration",
        original_flow=40,
        original_mode="constant",
        original_value=10,
        trial_baseline_value=10,
        trial_value=1,
    )
    assert [action.action_id for action in plan.actions] == [
        "enter_manual",
        "trial_step",
        "trial_restore",
        "restore_timer",
    ]
    assert plan.receipt_indexes == (1, 2)
    assert plan.recovery_order == (3, 2, 1, 0)
    assert plan.recovery_prerequisites == (3,)


def _intent(**overrides):
    plan = _plan()
    arguments = {
        "operation_id": "dc_prepared_0001",
        "target": _binding(),
        "function": "manual_flow",
        "field": "Flow",
        "timer_initially_enabled": True,
        "original_flow": 81,
        "original_mode": "pulse",
        "original_value": 81,
        "preparation_flow": 40,
        "trial_baseline_value": 40,
        "trial_value": 39,
        "action_ids": tuple(a.action_id for a in plan.actions),
        "recovery_order": plan.recovery_order,
        "recovery_prerequisites": plan.recovery_prerequisites,
        "step_request_id": "q_trial_step_" + "a" * 20,
        "restore_request_id": "q_trial_restore_" + "b" * 16,
        "config_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "manifest_generation": "3" * 32,
        "min_power": 30,
        "max_power": 100,
        "app_version": "3.4.18",
        "app_catalog_sha256": "4" * 64,
        "template_sha256": "5" * 64,
        "schema_sha256": "6" * 64,
        "firmware_version": "dc-firmware",
        "created_at": NOW,
    }
    arguments.update(overrides)
    return DcPreparedQualificationIntent(**arguments)


def test_the_durable_intent_accepts_an_owned_plan() -> None:
    intent = _intent()
    assert intent.original_flow == 81 and intent.preparation_flow == 40


@pytest.mark.parametrize(
    "overrides",
    (
        {"preparation_flow": 46},
        {"preparation_flow": 20},
        {"original_flow": 120},
        {"trial_value": 40},
        {"field": "Frequency"},
        {"recovery_order": (0, 1, 2)},
        {"recovery_prerequisites": (99,)},
        {"created_at": datetime(2026, 9, 16, 12)},
    ),
)
def test_the_durable_intent_refuses_an_unowned_or_unbounded_plan(overrides) -> None:
    with pytest.raises(ValueError):
        _intent(**overrides)


class _Permit:
    def require_permitted(self) -> None: ...
    def require_pre_send_permitted(self) -> None: ...


class _Lease:
    @contextmanager
    def acquire(self, keys):
        yield _Permit()


class _Resolver:
    def __init__(self, endpoint, *, fail_at=None):
        self.endpoint, self.fail_at, self.calls = endpoint, fail_at, 0

    async def resolve(self, target):
        self.calls += 1
        if self.fail_at == self.calls:
            raise CurrentEndpointResolutionError("strict resolution failed")
        return self.endpoint

    async def resolve_many(self, targets):
        return tuple([await self.resolve(item) for item in targets])


class _Device:
    """Applies planned control payloads by identity so later readbacks reflect earlier writes."""

    def __init__(self, sent, effects, *, flow=81, mode_code=1, timer=True, fail_on=None):
        self.flow, self.mode_code, self.timer, self.enabled = flow, mode_code, timer, True
        self.sent, self.effects, self.fail_on = sent, effects, fail_on
        self.quarantined = False

    def status(self):
        return _dc_status(
            flow=self.flow, enabled=self.enabled, timer_enabled=self.timer, mode_code=self.mode_code
        )

    async def connect(self): ...
    async def authenticate(self):
        return b"private-authentication"

    async def disconnect(self): ...
    def quarantine(self):
        self.quarantined = True

    async def read_raw_state(self, *, accept_reports: bool = False):
        return self.status()

    async def send_raw_control(self, payload: bytes):
        self.sent.append(bytes(payload))
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise RuntimeError("injected send fault")
        for name, value in self.effects.get(bytes(payload), {}).items():
            setattr(self, name, value)
        return b"acknowledged"


def _effects(*, preparation_flow=40, original_flow=81, original_mode_code=1, trial_flow=39):
    """Map each planned payload to the fake device state it establishes."""

    from jebao_flow.local_control.device_writer import get_product_schema
    from jebao_flow.protocol.control import build_control_payload

    schema = get_product_schema(DC)

    def build(fields):
        return bytes(build_control_payload(schema, fields))

    return {
        build({"Flow": preparation_flow}): {"flow": preparation_flow},
        build({"Flow": trial_flow}): {"flow": trial_flow},
        build({"Flow": original_flow}): {"flow": original_flow},
        build({"Mode": "constant"}): {"mode_code": 0},
        build({"Mode": "pulse"}): {"mode_code": original_mode_code},
        build({"TimerON": False, "SwitchON": True}): {"timer": False, "enabled": True},
        build({"TimerON": True, "SwitchON": True}): {"timer": True, "enabled": True},
    }


class _WriterFactory:
    def __init__(self, device):
        self.device = device

    def __call__(self, endpoint):
        return ConnectedDeviceWriter(
            endpoint,
            session_factory=lambda _address: self.device,
            control_flow_bounds=(30, 100),
        )


def _epoch(target):
    return QualificationEpoch(
        target=target,
        config_sha256="1" * 64,
        manifest_sha256="2" * 64,
        manifest_generation="3" * 32,
        min_power=30,
        max_power=100,
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


def _producer(tmp_path: Path, device, *, function="manual_flow"):
    target = _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    producer = DcPreparedScalarQualificationProducer(
        function=function,
        epoch_provider=lambda: _epoch(target),
        resolver_factory=lambda: _Resolver(endpoint),
        journal=journal,
        lease=lease,
        artifacts=QualificationArtifactStore.for_test(
            tmp_path / "receipts", tmp_path / "output" / "qualifications.json"
        ),
        writer_factory=_WriterFactory(device),
        clock=lambda: NOW,
    )
    return producer, journal, lease, target, endpoint


@pytest.mark.asyncio
async def test_success_writes_eight_owned_frames_and_issues_only_the_two_trial_receipts(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    device = _Device(sent, _effects())
    producer, journal, _lease, target, _endpoint = _producer(tmp_path, device)

    result = await producer.execute("dc_prepared_live", preparation_flow=40)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 8 and result.receipt_count == 2
    record = journal.load("dc_prepared_live")
    assert tuple(a.action_id for a in record.actions) == (
        "prepare_flow",
        "prepare_mode",
        "enter_manual",
        "trial_step",
        "trial_restore",
        "restore_timer",
        "restore_mode",
        "restore_flow",
    )
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)
    # C1: one initial snapshot owns every rollback.
    assert len({a.baseline_status_sha256 for a in record.actions}) == 1
    # The journal carries the ordering contract the coordinator must honour.
    assert record.recovery_order[0] == 5 and record.recovery_prerequisites == (5,)
    # C4: no rollback restores a timer-off state.
    for action in record.actions:
        if action.function in {"schedule_enable", "manual_enable"}:
            baseline = json.loads(journal.load_blob(action.baseline_readback_sha256))
            assert baseline == {"TimerON": True, "SwitchON": True}
    binding = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )[0]
    assert binding.function == "manual_flow"
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert device.flow == 81 and device.timer is True and device.mode_code == 1


@pytest.mark.asyncio
async def test_recovery_restores_the_timer_before_it_ever_writes_the_original_output(
    tmp_path: Path,
) -> None:
    """The safety property: the original manual output is never written while the timer is off."""

    sent: list[bytes] = []
    effects = _effects()
    device = _Device(sent, effects, fail_on=4)
    producer, journal, lease, target, endpoint = _producer(tmp_path, device)

    with pytest.raises(RuntimeError, match="injected send fault"):
        await producer.execute("dc_prepared_fault", preparation_flow=40)

    record = journal.load("dc_prepared_fault")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert device.timer is False and device.flow == 40

    recovery: list[bytes] = []
    recovery_device = _Device(recovery, effects, flow=40, mode_code=0, timer=False)
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),
        journal=journal,
        lease=lease,
        writer_factory=_WriterFactory(recovery_device),
    )
    recovered = await coordinator.recover("dc_prepared_fault")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    from jebao_flow.local_control.device_writer import get_product_schema
    from jebao_flow.protocol.control import build_control_payload

    schema = get_product_schema(DC)
    timer_restore = bytes(build_control_payload(schema, {"TimerON": True, "SwitchON": True}))
    original_flow = bytes(build_control_payload(schema, {"Flow": 81}))
    assert timer_restore in recovery
    if original_flow in recovery:
        assert recovery.index(timer_restore) < recovery.index(original_flow)
    assert recovery_device.timer is True and recovery_device.flow == 81


@pytest.mark.parametrize(
    "function", ("manual_flow", "manual_mode", "manual_frequency", "feeding_duration")
)
@pytest.mark.parametrize("timer", (True, False))
async def test_each_prepared_scalar_uses_owned_initial_fields_and_restores_them(
    tmp_path, function, timer
):
    """Queued schema-layout replies test every software path, not device dynamics."""
    from jebao_flow.local_control.qualification_producer import QualificationProductionError
    from tests.unit.test_local_control_bar_timer_qualification import _Session

    fields = {
        "manual_flow": "Flow",
        "manual_mode": "Mode",
        "manual_frequency": "Frequency",
        "feeding_duration": "FeedTime",
    }
    originals = {
        "Flow": 81,
        "Mode": "pulse",
        "Frequency": 20,
        "FeedTime": 10,
        "TimerON": timer,
        "SwitchON": True,
    }
    baselines = {
        "manual_flow": 40,
        "manual_mode": "constant",
        "manual_frequency": 20,
        "feeding_duration": 10,
    }
    trials = {
        "manual_flow": 39,
        "manual_mode": "pulse",
        "manual_frequency": 5,
        "feeding_duration": 1,
    }
    plan = _plan(
        function=function,
        original_value=originals[fields[function]],
        trial_baseline_value=baselines[function],
        trial_value=trials[function],
        timer_initially_enabled=timer,
    )

    def status(values):
        mode = {"constant": 0, "pulse": 1, "sine": 2, "random": 3, "feed": 4}[values["Mode"]]
        raw = bytearray(
            _dc_status(
                flow=values["Flow"],
                mode_code=mode,
                enabled=values["SwitchON"],
                timer_enabled=values["TimerON"],
            )
        )
        for name in ("Frequency", "FeedTime"):
            raw[DC_PUMP_PRO.by_name(name).position.byte_offset] = values[name]
        return bytes(raw)

    sent = []
    initial = status(originals)
    sessions = [_Session([initial], sent)]
    current = dict(originals)
    for action in plan.actions:
        before = status(current)
        if action.intent.function == "manual_enable":
            current.update(TimerON=False, SwitchON=True)
        elif action.intent.function == "schedule_enable":
            current.update(TimerON=True, SwitchON=True)
        else:
            current[fields[action.intent.function]] = action.intent.value
        sessions.append(_Session([before, status(current)], sent))
    producer, journal, _, _, _ = _producer(tmp_path, None, function=function)

    def factory(endpoint):
        session = sessions.pop(0)
        return ConnectedDeviceWriter(
            endpoint, session_factory=lambda _: session, control_flow_bounds=(30, 100)
        )

    producer._writer_factory = factory
    result = await producer.execute("dc_all_scalars", preparation_flow=40)
    assert result.receipt_count == 2 and result.physical_write_count == len(plan.actions)
    assert len(sent) == len(plan.actions) and sessions == []
    record = journal.load("dc_all_scalars")
    assert (
        journal.load_blob(record.actions[-1].diagnostic.removeprefix("dc_status_sha256."))
        == initial
    )
    (binding,) = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert {r.function for r in receipts} == {function}
    replay = await producer.execute("dc_all_scalars", preparation_flow=40)
    assert replay.physical_write_count == 0
    with pytest.raises(QualificationProductionError, match="different intent"):
        await producer.execute("dc_all_scalars", preparation_flow=41)
