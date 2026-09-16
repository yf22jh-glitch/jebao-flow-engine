"""Offline tests for the DC timer and schedule qualification wrapper.

These assert planning and ordering only. Nothing here claims firmware acceptance, atomic
application of any command, or that a latent value is adopted.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.dc_program_qualification import (
    DcProgramQualificationIntent,
    bounded_trial_schedule,
    plan_dc_program_actions,
    require_bounded_original_image,
    require_bounded_preparation_flow,
    restore_timer_power_intent,
)
from jebao_flow.local_control.dc_timer import DcTimerScreen, prepare_dc_timer_command
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.qualification_producer import QualificationProductionError
from jebao_flow.protocol.profiles import DC_PUMP_PRO

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def _binding() -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="dc_target",
        product_key=DC_PUMP_PRO.product_key,
        physical_identity_key="d" * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


_TRIAL_IDS = {
    "schedule_enable": ("trial_image",),
    "manual_enable": ("trial_image",),
    "schedule": ("trial_a", "trial_b"),
    "schedule_reset": ("trial_reset_a", "trial_populate", "trial_reset_b"),
}


def _plan(**overrides):
    arguments = {
        "operation_id": "dc_program_0001",
        "target": _binding(),
        "function": "schedule_enable",
        "original_flow": 81,
        "original_mode": "pulse",
        "original_timer_enabled": True,
        "original_power_enabled": True,
        "preparation_flow": 40,
    }
    arguments.update(overrides)
    arguments.setdefault("trial_action_ids", _TRIAL_IDS[arguments["function"]])
    return plan_dc_program_actions(**arguments)


def test_the_trial_image_covers_the_whole_day_at_one_bounded_output() -> None:
    image = bounded_trial_schedule(40).compile_image()
    assert len(image) == 384
    rows = [image[index * 8 : (index + 1) * 8] for index in range(48)]
    assert not any(row == b"\xee" * 8 for row in rows)
    assert rows[0][:4] == bytes((0, 0, 0, 30))
    assert rows[47][:4] == bytes((23, 30, 24, 0))
    assert {row[4] for row in rows} == {0}
    assert {row[5] for row in rows} == {40}


@pytest.mark.parametrize("function", ("schedule_enable", "manual_enable"))
def test_a_timer_trial_installs_a_bounded_image_before_the_timer_may_run(function) -> None:
    plan = _plan(function=function)
    ids = [action.action_id for action in plan.actions]
    assert ids == [
        "prepare_flow",
        "prepare_mode",
        "trial_image",
        "trial_off",
        "trial_on",
        "enter_manual",
        "restore_image",
        "restore_timer_power",
        "restore_mode",
        "restore_flow",
    ]
    assert ids.index("trial_image") < ids.index("trial_off")
    assert plan.receipt_indexes == (3, 4)
    assert [plan.actions[index].intent.function for index in plan.receipt_indexes] == [
        function,
        function,
    ]
    assert [plan.actions[index].intent.value for index in plan.receipt_indexes] == [False, True]


def test_a_schedule_trial_stays_latent_and_needs_no_second_manual_entry() -> None:
    plan = _plan(function="schedule")
    ids = [action.action_id for action in plan.actions]
    assert ids == [
        "prepare_flow",
        "prepare_mode",
        "enter_manual_first",
        "trial_a",
        "trial_b",
        "restore_image",
        "restore_timer_power",
        "restore_mode",
        "restore_flow",
    ]
    assert ids.count("enter_manual") == 0
    assert plan.receipt_indexes == (3, 4)


def test_a_reset_trial_clears_twice_around_a_populated_image() -> None:
    """The reset contract is proved by the two clearing frames, not by the populated one."""

    plan = _plan(function="schedule_reset")
    ids = [action.action_id for action in plan.actions]
    assert ids[2:6] == ["enter_manual_first", "trial_reset_a", "trial_populate", "trial_reset_b"]
    assert [ids[index] for index in plan.receipt_indexes] == ["trial_reset_a", "trial_reset_b"]
    functions = {action.action_id: action.intent.function for action in plan.actions}
    assert functions["trial_reset_a"] == "schedule_reset"
    assert functions["trial_reset_b"] == "schedule_reset"
    assert functions["trial_populate"] == "schedule"


def test_every_action_carries_the_function_it_really_writes() -> None:
    for function in ("schedule_enable", "schedule", "schedule_reset"):
        plan = _plan(function=function)
        by_id = {a.action_id: a.intent.function for a in plan.actions}
        assert by_id["restore_image"] == "schedule"
        assert by_id.get("trial_image", "schedule") == "schedule"
        assert by_id["prepare_flow"] == "manual_flow"
        assert by_id["enter_manual_first" if "enter_manual_first" in by_id else "enter_manual"] == (
            "manual_enable"
        )
        receipts = [plan.actions[index].intent.function for index in plan.receipt_indexes]
        assert receipts == [function, function]


def test_every_timer_trial_plans_a_frame_that_stops_the_pump() -> None:
    """The app couples power to the timer in both screens, so the False frame stops the pump."""

    for function, screen in (
        ("schedule_enable", DcTimerScreen.SCHEDULE),
        ("manual_enable", DcTimerScreen.MANUAL),
    ):
        plan = _plan(function=function)
        off_frame = next(a for a in plan.actions if a.action_id == "trial_off")
        desired = (
            off_frame.intent.value
            if screen is DcTimerScreen.SCHEDULE
            else not off_frame.intent.value
        )
        command = prepare_dc_timer_command(screen=screen, desired_timer_enabled=desired)
        assert command.power_after is False


@pytest.mark.parametrize(
    ("timer_enabled", "power_enabled", "function", "value"),
    (
        (True, True, "schedule_enable", True),
        (False, False, "schedule_enable", False),
        (False, True, "manual_enable", True),
        (True, False, "manual_enable", False),
    ),
)
def test_each_timer_power_pair_has_exactly_one_owning_app_command(
    timer_enabled, power_enabled, function, value
) -> None:
    intent = restore_timer_power_intent(
        operation_id="dc_program_0001",
        action_id="restore_timer_power",
        target=_binding(),
        timer_enabled=timer_enabled,
        power_enabled=power_enabled,
    )
    assert intent.function == function and intent.value is value


@pytest.mark.parametrize("original_timer_enabled", (True, False))
def test_recovery_restores_timer_then_image_then_mode_then_flow(original_timer_enabled) -> None:
    plan = _plan(original_timer_enabled=original_timer_enabled)
    ids = [action.action_id for action in plan.actions]
    order = list(plan.recovery_order)
    assert sorted(order) == list(range(len(ids)))
    assert len(set(order)) == len(order)
    expected_head = [
        ids.index("restore_timer_power"),
        ids.index("restore_image"),
        ids.index("restore_mode"),
        ids.index("restore_flow"),
    ]
    assert order[:4] == expected_head
    assert plan.recovery_prerequisites == (ids.index("restore_timer_power"),)
    assert order[4:] == sorted(
        (index for index in range(len(ids)) if index not in expected_head), reverse=True
    )


def test_preparation_frames_are_omitted_when_the_device_already_matches() -> None:
    plan = _plan(original_flow=40, original_mode="constant")
    ids = [action.action_id for action in plan.actions]
    assert "prepare_flow" not in ids and "prepare_mode" not in ids
    assert "restore_flow" not in ids and "restore_mode" not in ids
    assert plan.recovery_prerequisites == (ids.index("restore_timer_power"),)
    assert sorted(plan.recovery_order) == list(range(len(ids)))


def _image(*, flow: int = 60, mode: int = 0) -> bytes:
    rows = [bytes((0, 0, 12, 0, mode, flow, 0, 0))] + [b"\xee" * 8] * 47
    return b"".join(rows)


def test_the_original_image_must_stay_inside_the_configured_limits() -> None:
    require_bounded_original_image(_image(flow=60), minimum=30, maximum=100)
    with pytest.raises(QualificationProductionError, match="outside the configured limits"):
        require_bounded_original_image(_image(flow=120), minimum=30, maximum=100)
    # Unused, all-zero and feeding rows carry no running output.
    require_bounded_original_image(b"\xee" * 384, minimum=30, maximum=100)
    require_bounded_original_image(bytes(384), minimum=30, maximum=100)
    require_bounded_original_image(_image(flow=120, mode=4), minimum=30, maximum=100)
    with pytest.raises(QualificationProductionError, match="wrong size"):
        require_bounded_original_image(bytes(100), minimum=30, maximum=100)


@pytest.mark.parametrize("flow", (46, 20, 120, True, 40.0))
def test_the_preparation_flow_stays_inside_the_first_write_ceiling(flow) -> None:
    with pytest.raises(QualificationProductionError):
        require_bounded_preparation_flow(flow, minimum=30, maximum=100)


def _intent(**overrides):
    plan = _plan()
    arguments = {
        "operation_id": "dc_program_0001",
        "target": _binding(),
        "function": "schedule_enable",
        "original_flow": 81,
        "original_mode": "pulse",
        "original_timer_enabled": True,
        "original_image_sha256": hashlib.sha256(b"original").hexdigest(),
        "trial_image_sha256": (hashlib.sha256(b"trial").hexdigest(),),
        "preparation_flow": 40,
        "trial_flow": 40,
        "action_ids": tuple(a.action_id for a in plan.actions),
        "recovery_order": plan.recovery_order,
        "recovery_prerequisites": plan.recovery_prerequisites,
        "step_request_id": "q_trial_off_" + "a" * 20,
        "restore_request_id": "q_trial_on_" + "b" * 20,
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
    return DcProgramQualificationIntent(**arguments)


def test_the_durable_intent_accepts_an_owned_plan() -> None:
    assert _intent().preparation_flow == 40


@pytest.mark.parametrize(
    "overrides",
    (
        {"preparation_flow": 46},
        {"trial_flow": 90},
        {"recovery_order": (0, 1)},
        {"recovery_order": (0,) * 10},
        {"created_at": datetime(2026, 9, 16, 12)},
    ),
)
def test_the_durable_intent_refuses_an_unowned_or_unbounded_plan(overrides) -> None:
    with pytest.raises(ValueError):
        _intent(**overrides)


from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from jebao_flow.local_control.dc_program_qualification import (  # noqa: E402
    DcProgramQualificationProducer,
)
from jebao_flow.local_control.device_writer import (  # noqa: E402
    ConnectedDeviceWriter,
    build_control_payload,
    build_exact_schedule_payload,
)
from jebao_flow.local_control.journal import (  # noqa: E402
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.qualification import (  # noqa: E402
    load_current_qualifications,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (  # noqa: E402
    QualificationArtifactStore,
    QualificationEpoch,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint  # noqa: E402
from tests.unit.test_local_control_qualification_producer import _dc_status  # noqa: E402

DC = DC_PUMP_PRO.product_key
ORIGINAL_IMAGE = b"".join([bytes((0, 0, 12, 0, 0, 60, 0, 0))] + [b"\xee" * 8] * 47)


def _status(*, flow=81, mode_code=1, timer=True, enabled=True, image=None):
    raw = bytearray(
        _dc_status(flow=flow, enabled=enabled, timer_enabled=timer, mode_code=mode_code)
    )
    picked = ORIGINAL_IMAGE if image is None else image
    raw[9 : 9 + 384] = picked
    return bytes(raw)


class _Permit:
    def require_permitted(self) -> None: ...
    def require_pre_send_permitted(self) -> None: ...


class _Lease:
    @contextmanager
    def acquire(self, keys):
        yield _Permit()


class _Resolver:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    async def resolve(self, target):
        return self.endpoint

    async def resolve_many(self, targets):
        return tuple([await self.resolve(item) for item in targets])


class _Device:
    """Applies planned payloads by identity so readbacks reflect the frames already sent."""

    def __init__(self, sent, effects):
        self.flow, self.mode_code, self.timer, self.enabled = 81, 1, True, True
        self.image = ORIGINAL_IMAGE
        self.sent, self.effects, self.quarantined = sent, effects, False

    async def connect(self): ...
    async def authenticate(self):
        return b"private-authentication"

    async def disconnect(self): ...
    def quarantine(self):
        self.quarantined = True

    async def read_raw_state(self, *, accept_reports: bool = False):
        return _status(
            flow=self.flow,
            mode_code=self.mode_code,
            timer=self.timer,
            enabled=self.enabled,
            image=self.image,
        )

    async def send_raw_control(self, payload: bytes):
        self.sent.append(bytes(payload))
        for name, value in self.effects.get(bytes(payload), {}).items():
            setattr(self, name, value)
        return b"acknowledged"


def _effects(preparation_flow=40):
    from jebao_flow.local_control.device_writer import get_product_schema

    schema = get_product_schema(DC)

    def scalar(fields):
        return bytes(build_control_payload(schema, fields))

    trial_image = bounded_trial_schedule(preparation_flow).compile_image()
    return {
        scalar({"Flow": preparation_flow}): {"flow": preparation_flow},
        scalar({"Flow": 81}): {"flow": 81},
        scalar({"Mode": "constant"}): {"mode_code": 0},
        scalar({"Mode": "pulse"}): {"mode_code": 1},
        scalar({"TimerON": False, "SwitchON": False}): {"timer": False, "enabled": False},
        scalar({"TimerON": True, "SwitchON": True}): {"timer": True, "enabled": True},
        scalar({"TimerON": False, "SwitchON": True}): {"timer": False, "enabled": True},
        scalar({"TimerON": True, "SwitchON": False}): {"timer": True, "enabled": False},
        bytes(build_exact_schedule_payload(DC, trial_image)): {"image": trial_image},
        bytes(build_exact_schedule_payload(DC, ORIGINAL_IMAGE)): {"image": ORIGINAL_IMAGE},
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


def _producer(tmp_path: Path, device, *, function="schedule_enable"):
    target = _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    epoch = QualificationEpoch(
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
    producer = DcProgramQualificationProducer(
        function=function,
        epoch_provider=lambda: epoch,
        resolver_factory=lambda: _Resolver(endpoint),
        journal=journal,
        lease=_Lease(),
        artifacts=QualificationArtifactStore.for_test(
            tmp_path / "receipts", tmp_path / "output" / "qualifications.json"
        ),
        writer_factory=_WriterFactory(device),
        clock=lambda: NOW,
    )
    return producer, journal


@pytest.mark.asyncio
async def test_a_timer_qualification_runs_bounded_and_returns_the_device_to_its_original_state(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    device = _Device(sent, _effects())
    producer, journal = _producer(tmp_path, device)

    result = await producer.execute("dc_program_live", preparation_flow=40)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.receipt_count == 2 and result.physical_write_count == 10
    record = journal.load("dc_program_live")
    assert all(action.phase is ActionPhase.VERIFIED for action in record.actions)
    assert len({action.baseline_status_sha256 for action in record.actions}) == 1
    ids = [action.action_id for action in record.actions]
    assert record.recovery_order[0] == ids.index("restore_timer_power")
    assert record.recovery_prerequisites == (ids.index("restore_timer_power"),)
    # The device is back to the exact state the operation found.
    assert device.flow == 81 and device.mode_code == 1
    assert device.timer is True and device.enabled is True
    assert device.image == ORIGINAL_IMAGE
    binding = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )[0]
    assert binding.function == "schedule_enable"
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2


@pytest.mark.asyncio
async def test_the_timer_qualification_really_does_stop_the_pump_for_one_frame(
    tmp_path: Path,
) -> None:
    """Recorded end to end so the physical consequence cannot be lost in review."""

    sent: list[bytes] = []
    device = _Device(sent, _effects())
    producer, _journal = _producer(tmp_path, device)

    await producer.execute("dc_program_power", preparation_flow=40)

    from jebao_flow.local_control.device_writer import get_product_schema

    schema = get_product_schema(DC)
    stops_pump = bytes(build_control_payload(schema, {"TimerON": False, "SwitchON": False}))
    assert stops_pump in sent
    # It is momentary: the very next planned frame restores power.
    restores_power = bytes(build_control_payload(schema, {"TimerON": True, "SwitchON": True}))
    assert sent.index(stops_pump) < sent.index(restores_power)


def test_trial_images_are_bounded_before_any_write() -> None:
    from jebao_flow.local_control.dc_program_qualification import build_program_images

    over = bounded_trial_schedule(80).compile_image()
    under = bounded_trial_schedule(40).compile_image()
    with pytest.raises(QualificationProductionError, match="outside the trial limits"):
        build_program_images(
            function="schedule",
            original=ORIGINAL_IMAGE,
            preparation_flow=40,
            schedule_a=over,
            schedule_b=under,
            minimum=30,
            maximum=100,
        )
    with pytest.raises(QualificationProductionError, match="outside the trial limits"):
        build_program_images(
            function="schedule_reset",
            original=ORIGINAL_IMAGE,
            preparation_flow=40,
            schedule_a=over,
            schedule_b=None,
            minimum=30,
            maximum=100,
        )
    images = build_program_images(
        function="schedule_reset",
        original=ORIGINAL_IMAGE,
        preparation_flow=40,
        schedule_a=under,
        schedule_b=None,
        minimum=30,
        maximum=100,
    )
    assert images.trial_action_ids == ("trial_reset_a", "trial_populate", "trial_reset_b")
    assert images.trial[0] == images.trial[2] == b"\xee" * 384
    assert images.trial[1] == under


@pytest.mark.asyncio
async def test_a_reset_qualification_issues_receipts_only_for_the_two_clearing_frames(
    tmp_path: Path,
) -> None:
    populated = bounded_trial_schedule(40).compile_image()
    cleared = b"\xee" * 384
    sent: list[bytes] = []
    effects = _effects()
    effects[bytes(build_exact_schedule_payload(DC, populated))] = {"image": populated}
    effects[bytes(build_exact_schedule_payload(DC, cleared))] = {"image": cleared}
    device = _Device(sent, effects)
    producer, journal = _producer(tmp_path, device, function="schedule_reset")
    producer._schedule_a = populated

    result = await producer.execute("dc_reset_live", preparation_flow=40)

    assert result.receipt_count == 2 and result.phase is TransactionPhase.VERIFIED
    record = journal.load("dc_reset_live")
    by_id = {action.action_id: action for action in record.actions}
    assert by_id["trial_reset_a"].function == "schedule_reset"
    assert by_id["trial_reset_b"].function == "schedule_reset"
    assert by_id["trial_populate"].function == "schedule"
    assert journal.load_blob(by_id["trial_reset_a"].expected_readback_sha256) == cleared
    assert journal.load_blob(by_id["trial_populate"].expected_readback_sha256) == populated
    assert device.image == ORIGINAL_IMAGE


@pytest.mark.asyncio
async def test_a_replay_may_not_swap_the_images_the_stored_intent_bound(tmp_path: Path) -> None:
    first = bounded_trial_schedule(40).compile_image()
    second = bounded_trial_schedule(39).compile_image()
    sent: list[bytes] = []
    effects = _effects()
    for image in (first, second):
        effects[bytes(build_exact_schedule_payload(DC, image))] = {"image": image}
    device = _Device(sent, effects)
    producer, _journal = _producer(tmp_path, device, function="schedule")
    producer._schedule_a, producer._schedule_b = first, second

    await producer.execute("dc_schedule_live", preparation_flow=40)

    producer._schedule_a, producer._schedule_b = second, first
    with pytest.raises(QualificationProductionError, match="different trial images"):
        await producer.execute("dc_schedule_live", preparation_flow=40)


class _FaultDevice(_Device):
    """A device that refuses one chosen send, by payload identity."""

    def __init__(self, sent, effects, *, refuse: bytes | None = None, refuse_index=None):
        super().__init__(sent, effects)
        self.refuse, self.refuse_index = refuse, refuse_index

    async def send_raw_control(self, payload: bytes):
        if self.refuse is not None and bytes(payload) == self.refuse:
            self.sent.append(bytes(payload))
            raise RuntimeError("injected send fault")
        if self.refuse_index is not None and len(self.sent) == self.refuse_index:
            self.sent.append(bytes(payload))
            raise RuntimeError("injected send fault")
        return await super().send_raw_control(payload)


def _coordinator(journal, endpoint, device):
    from jebao_flow.local_control.catalog import build_local_control_catalog
    from jebao_flow.local_control.coordinator import LocalControlCoordinator

    return LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),
        journal=journal,
        lease=_Lease(),
        writer_factory=_WriterFactory(device),
    )


@pytest.mark.asyncio
async def test_recovery_restores_timer_then_image_then_mode_then_flow_on_a_real_coordinator(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    effects = _effects()
    # Stop after the trial has turned the timer on, leaving the bounded image live.
    device = _FaultDevice(sent, effects, refuse_index=5)
    producer, journal = _producer(tmp_path, device)
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")

    with pytest.raises(RuntimeError, match="injected send fault"):
        await producer.execute("dc_program_fault", preparation_flow=40)

    record = journal.load("dc_program_fault")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert device.timer is True and device.image != ORIGINAL_IMAGE

    recovery: list[bytes] = []
    recovery_device = _Device(recovery, effects)
    recovery_device.flow, recovery_device.mode_code = 40, 0
    # enter_manual may have applied before the fault, so the timer is not already original.
    recovery_device.timer, recovery_device.enabled = False, True
    recovery_device.image = bounded_trial_schedule(40).compile_image()
    recovered = await _coordinator(journal, endpoint, recovery_device).recover("dc_program_fault")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    from jebao_flow.local_control.device_writer import get_product_schema

    schema = get_product_schema(DC)
    timer_restore = bytes(build_control_payload(schema, {"TimerON": True, "SwitchON": True}))
    image_restore = bytes(build_exact_schedule_payload(DC, ORIGINAL_IMAGE))
    mode_restore = bytes(build_control_payload(schema, {"Mode": "pulse"}))
    flow_restore = bytes(build_control_payload(schema, {"Flow": 81}))
    written = [payload for payload in recovery]
    order = [
        written.index(item)
        for item in (image_restore, mode_restore, flow_restore)
        if item in written
    ]
    assert timer_restore in written
    assert all(written.index(timer_restore) < index for index in order)
    assert order == sorted(order)
    assert recovery_device.image == ORIGINAL_IMAGE
    assert recovery_device.flow == 81 and recovery_device.mode_code == 1


@pytest.mark.asyncio
async def test_an_uncertain_timer_restore_stops_recovery_before_any_later_write(
    tmp_path: Path,
) -> None:
    """If the timer restore cannot be confirmed, the original output is never written blind."""

    sent: list[bytes] = []
    effects = _effects()
    device = _FaultDevice(sent, effects, refuse_index=5)
    producer, journal = _producer(tmp_path, device)
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")

    with pytest.raises(RuntimeError, match="injected send fault"):
        await producer.execute("dc_program_uncertain", preparation_flow=40)

    from jebao_flow.local_control.device_writer import get_product_schema

    schema = get_product_schema(DC)
    timer_restore = bytes(build_control_payload(schema, {"TimerON": True, "SwitchON": True}))
    recovery: list[bytes] = []
    recovery_device = _FaultDevice(recovery, effects, refuse=timer_restore)
    recovery_device.flow, recovery_device.mode_code = 40, 0
    recovery_device.timer, recovery_device.enabled = False, True
    recovery_device.image = bounded_trial_schedule(40).compile_image()

    from jebao_flow.local_control.coordinator import LocalControlExecutionError

    with pytest.raises(LocalControlExecutionError, match="rollback outcome is uncertain"):
        await _coordinator(journal, endpoint, recovery_device).recover("dc_program_uncertain")

    assert recovery == [timer_restore]
    assert bytes(build_exact_schedule_payload(DC, ORIGINAL_IMAGE)) not in recovery
    assert bytes(build_control_payload(schema, {"Flow": 81})) not in recovery
    assert recovery_device.flow == 40


@pytest.mark.asyncio
async def test_a_second_recovery_never_resends_a_timer_rollback_the_operator_changed(
    tmp_path: Path,
) -> None:
    """A completed timer rollback is not replayed over an external change on a later recover."""

    from jebao_flow.local_control.coordinator import LocalControlExecutionError
    from jebao_flow.local_control.device_writer import get_product_schema

    schema = get_product_schema(DC)
    flow_restore = bytes(build_control_payload(schema, {"Flow": 81}))
    timer_restore = bytes(build_control_payload(schema, {"TimerON": True, "SwitchON": True}))

    sent: list[bytes] = []
    effects = _effects()
    device = _FaultDevice(sent, effects, refuse_index=5)
    producer, journal = _producer(tmp_path, device)
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    with pytest.raises(RuntimeError, match="injected send fault"):
        await producer.execute("dc_program_twice", preparation_flow=40)

    first: list[bytes] = []
    recovery_device = _FaultDevice(first, effects, refuse=flow_restore)
    recovery_device.flow, recovery_device.mode_code = 40, 0
    recovery_device.timer, recovery_device.enabled = False, True
    recovery_device.image = bounded_trial_schedule(40).compile_image()
    with pytest.raises(LocalControlExecutionError):
        await _coordinator(journal, endpoint, recovery_device).recover("dc_program_twice")
    assert timer_restore in first and recovery_device.timer is True

    record = journal.load("dc_program_twice")
    timer_action = record.actions[
        [a.action_id for a in record.actions].index("restore_timer_power")
    ]
    assert timer_action.phase is ActionPhase.ROLLED_BACK

    # The operator moves the timer between the two recoveries.
    second: list[bytes] = []
    later_device = _Device(second, effects)
    later_device.flow, later_device.mode_code = 81, 1
    later_device.timer, later_device.enabled = False, True
    later_device.image = ORIGINAL_IMAGE
    with pytest.raises(LocalControlExecutionError, match="not resent"):
        await _coordinator(journal, endpoint, later_device).recover("dc_program_twice")

    assert timer_restore not in second
    assert later_device.timer is False
    reloaded = journal.load("dc_program_twice")
    refused = reloaded.actions[[a.action_id for a in reloaded.actions].index("restore_timer_power")]
    assert refused.diagnostic.startswith("rollback_replay_refused.")
