"""Offline tests for the bounded Aquarium app-control qualifications.

These assert the software contract only. Reaching a verified journal here is not evidence that
the firmware accepted any command, and nothing in this file claims physical safety.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.aquarium_app_qualification import (
    AquariumAppQualificationIntent,
    AquariumAppQualificationProducer,
    planned_trial_schedule,
)
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    build_aquarium_app_payload,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.pump_preview import ReturnPumpSchedule
from jebao_flow.local_control.qualification import (
    load_current_qualifications,
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationEpoch,
    QualificationProductionError,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import AQUARIUM_PUMP

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
BOUNDS = (30, 45)
SPEED = 40
TRIAL_SPEED = 38
FEED_TIME = 5
_AUTO = 1

TRIAL_IMAGE = planned_trial_schedule(TRIAL_SPEED).compile_image()
ORIGINAL_IMAGE = ReturnPumpSchedule.model_validate(
    {
        "family": "aquarium",
        "slots": [
            {
                "slot_index": 0,
                "start": "00:00",
                "end": "24:00",
                "mode": "auto",
                "speed": SPEED,
            }
        ],
    }
).compile_image()
UNBOUNDED_IMAGE = ReturnPumpSchedule.model_validate(
    {
        "family": "aquarium",
        "slots": [
            {
                "slot_index": 0,
                "start": "00:00",
                "end": "24:00",
                "mode": "auto",
                "speed": 90,
            }
        ],
    }
).compile_image()


def _binding(marker: str = "e") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="aquarium_app_target",
        product_key=AQUARIUM_PUMP.product_key,
        physical_identity_key=marker * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


def _epoch(target: LocalDeviceBinding | None = None) -> QualificationEpoch:
    return QualificationEpoch(
        target=target or _binding(),
        config_sha256="1" * 64,
        manifest_sha256="2" * 64,
        manifest_generation="3" * 32,
        min_power=BOUNDS[0],
        max_power=BOUNDS[1],
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


def _status(
    *,
    image: bytes = ORIGINAL_IMAGE,
    enabled: bool = True,
    transport: bool = True,
    feed_switch: bool = False,
    timer: bool = False,
    auto_mode: int = 0,
    speed: int = SPEED,
    feed_time: int = FEED_TIME,
    auto_gears: int = 0,
) -> bytes:
    raw = bytearray(AQUARIUM_PUMP.raw_status_size)
    raw[0] = (
        int(enabled)
        | int(transport) << 1
        | int(feed_switch) << 2
        | int(timer) << 3
        | auto_mode << 4
    )
    raw[1], raw[2], raw[3], raw[4] = speed, feed_time, auto_gears, 1
    raw[13 : 13 + len(image)] = image
    return bytes(raw)


class _Permit:
    def require_permitted(self) -> None:
        pass

    def require_pre_send_permitted(self) -> None:
        pass


class _Lease:
    def __init__(self) -> None:
        self.acquired: list[tuple[str, ...]] = []

    @contextmanager
    def acquire(self, keys: tuple[str, ...]):
        self.acquired.append(keys)
        yield _Permit()


class _Resolver:
    def __init__(self, endpoint: ResolvedEndpoint) -> None:
        self.endpoint, self.calls = endpoint, 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        return self.endpoint

    async def resolve_many(self, targets):
        return tuple([await self.resolve(item) for item in targets])


class _Session:
    def __init__(self, statuses, sent, *, send_error: BaseException | None = None) -> None:
        self.statuses, self.sent, self.send_error = list(statuses), sent, send_error
        self.quarantined = False

    async def connect(self) -> None:
        pass

    async def authenticate(self) -> bytes:
        return b"private-authentication"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        value = self.statuses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def send_raw_control(self, payload: bytes) -> bytes:
        self.sent.append(bytes(payload))
        if self.send_error is not None:
            raise self.send_error
        return b"acknowledged"

    async def disconnect(self) -> None:
        pass

    def quarantine(self) -> None:
        self.quarantined = True


class _WriterFactory:
    def __init__(self, sessions: list[_Session], bounds: tuple[int, int] = BOUNDS) -> None:
        self.sessions, self.created, self.bounds = sessions, 0, bounds

    def __call__(self, endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
        session = self.sessions.pop(0)
        self.created += 1
        return ConnectedDeviceWriter(
            endpoint,
            session_factory=lambda _address: session,
            # The deployment gives the writer and the epoch the same configured range.
            control_flow_bounds=self.bounds,
            device_local_time=lambda: NOW,
        )


def _producer(
    tmp_path: Path,
    sessions: list[_Session],
    *,
    function: str,
    target=None,
    epoch: QualificationEpoch | None = None,
):
    target = target or _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint)
    active = epoch or _epoch(target)
    factory = _WriterFactory(sessions, (active.min_power, active.max_power))
    producer = AquariumAppQualificationProducer(
        function=function,
        epoch_provider=lambda: epoch or _epoch(target),
        resolver_factory=lambda: resolver,
        journal=journal,
        lease=lease,
        artifacts=QualificationArtifactStore.for_test(
            tmp_path / "receipts", tmp_path / "output" / "qualifications.json"
        ),
        writer_factory=factory,
        clock=lambda: NOW,
    )
    return producer, journal, lease, resolver, factory


# The feeding sequence starts with the timer on, so the owned restore has real work to do.
FEED_INITIAL = _status(timer=True, auto_mode=_AUTO, auto_gears=SPEED)
FEED_ON = _status(feed_switch=True, timer=False, auto_mode=_AUTO, auto_gears=SPEED)
FEED_OFF = _status(feed_switch=False, timer=False, auto_mode=_AUTO, auto_gears=SPEED)

TIMER_INITIAL = _status()
TIMER_TRIAL = _status(image=TRIAL_IMAGE)
TIMER_ON = _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED)
TIMER_OFF = _status(image=TRIAL_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED)
TIMER_FINAL = _status(image=ORIGINAL_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED)

_SEQUENCES = {
    "feed_switch": (FEED_INITIAL, FEED_ON, FEED_OFF, FEED_INITIAL),
    "schedule_enable": (
        TIMER_INITIAL,
        TIMER_TRIAL,
        TIMER_ON,
        TIMER_OFF,
        TIMER_OFF,
        TIMER_FINAL,
    ),
}

_EXPECTED_ACTIONS = {
    "feed_switch": ("trial_feed", "restore_feed", "owned_restore"),
    "schedule_enable": (
        "trial_schedule",
        "timer_on",
        "timer_off",
        "owned_restore",
        "restore_schedule",
    ),
}

_EXPECTED_FUNCTIONS = {
    "feed_switch": ("feed_switch", "feed_switch", "aquarium_owned_controls"),
    "schedule_enable": (
        "schedule",
        "schedule_enable",
        "schedule_enable",
        "aquarium_owned_controls",
        "schedule",
    ),
}

_TRIAL_SPEED_BY_FUNCTION = {"feed_switch": None, "schedule_enable": TRIAL_SPEED}
FUNCTIONS = ("feed_switch", "schedule_enable")


def _sessions(function: str, sent: list[bytes], **kwargs) -> list[_Session]:
    states = _SEQUENCES[function]
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent, **kwargs))
    return sessions


async def _run(producer, function, operation_id):
    speed = _TRIAL_SPEED_BY_FUNCTION[function]
    if speed is None:
        return await producer.execute(operation_id)
    return await producer.execute(operation_id, trial_speed=speed)


@pytest.mark.asyncio
@pytest.mark.parametrize("function", FUNCTIONS)
async def test_each_function_runs_its_planned_frames_and_two_receipts(
    tmp_path: Path, function: str
) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, journal, lease, resolver, factory = _producer(
        tmp_path, _sessions(function, sent), function=function, target=target
    )

    result = await _run(producer, function, "aquarium_app_0001")

    frames = len(_EXPECTED_ACTIONS[function])
    assert result.phase is TransactionPhase.VERIFIED
    assert result.receipt_count == 2
    assert result.physical_write_count == frames
    assert len(sent) == frames
    assert resolver.calls == frames + 1
    assert factory.created == frames + 1
    assert lease.acquired == [(target.physical_identity_key,)]
    record = journal.load("aquarium_app_0001")
    assert record is not None
    assert tuple(a.action_id for a in record.actions) == _EXPECTED_ACTIONS[function]
    assert tuple(a.function for a in record.actions) == _EXPECTED_FUNCTIONS[function]
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("function", FUNCTIONS)
async def test_receipts_name_the_function_and_never_the_restore_or_image_frames(
    tmp_path: Path, function: str
) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, _journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function, target=target
    )

    await _run(producer, function, "aquarium_app_0002")

    binding = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )[0]
    assert binding.function == function
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert {item.function for item in receipts} == {function}
    assert qualified_physical_identity_keys(
        binding, _receipt_root_for_test=tmp_path / "receipts"
    ) == frozenset({target.physical_identity_key})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function", "owned"),
    (
        ("feed_switch", ("FeedSwitch", "FeedTime", "Motor_Speed", "TimerON")),
        ("schedule_enable", ("FeedSwitch", "Motor_Speed", "TimerON")),
    ),
)
async def test_the_owned_restore_covers_exactly_what_the_app_frames_wrote(
    tmp_path: Path, function: str, owned: tuple[str, ...]
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function
    )

    await _run(producer, function, "aquarium_app_0003")

    record = journal.load("aquarium_app_0003")
    assert record is not None
    intent = AquariumAppQualificationIntent.model_validate_json(
        journal.load_blob(record.intent_sha256)
    )
    assert intent.owned_fields == owned
    restore = next(a for a in record.actions if a.action_id == "owned_restore")
    restored = json.loads(journal.load_blob(restore.expected_readback_sha256))
    assert tuple(sorted(restored)) == owned
    # Never the transport selector, and never a derived seed.
    assert "Mode" not in restored
    assert not any(name.startswith("Auto") for name in restored)


@pytest.mark.asyncio
@pytest.mark.parametrize("function", FUNCTIONS)
async def test_every_rollback_names_the_operation_initial_state(
    tmp_path: Path, function: str
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function
    )

    await _run(producer, function, "aquarium_app_0004")

    record = journal.load("aquarium_app_0004")
    assert record is not None
    assert len({a.baseline_status_sha256 for a in record.actions}) == 1
    initial = journal.load_blob(record.actions[0].baseline_status_sha256)
    assert initial == _SEQUENCES[function][0]
    for action in record.actions:
        if action.verification_kind == "schedule_image":
            assert journal.load_blob(action.baseline_readback_sha256) == ORIGINAL_IMAGE
            continue
        baseline = json.loads(journal.load_blob(action.baseline_readback_sha256))
        assert baseline
        assert not any(name.startswith("Auto") for name in baseline)


@pytest.mark.asyncio
async def test_the_timer_frames_seed_from_the_planned_image_and_never_own_the_seed(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("schedule_enable", sent), function="schedule_enable"
    )

    await producer.execute("aquarium_app_0005", trial_speed=TRIAL_SPEED)

    record = journal.load("aquarium_app_0005")
    assert record is not None
    on_fields = json.loads(journal.load_blob(record.actions[1].expected_readback_sha256))
    off_fields = json.loads(journal.load_blob(record.actions[2].expected_readback_sha256))
    # The seed is written but not owned, so it never appears in the persistent readback.
    assert on_fields == {"TimerON": True, "FeedSwitch": False}
    assert off_fields == {"TimerON": False, "Motor_Speed": SPEED}

    # It is in the sent bytes, computed from the trial image rather than the stored original.
    planned_status = bytearray(TIMER_INITIAL)
    planned_status[13 : 13 + len(TRIAL_IMAGE)] = TRIAL_IMAGE
    assert sent[1] == build_aquarium_app_payload(
        {
            "TimerON": True,
            "FeedSwitch": False,
            "AutoMode": "auto",
            "AutoGears": TRIAL_SPEED,
        },
        bytes(planned_status),
    )
    assert TRIAL_SPEED != SPEED


@pytest.mark.asyncio
async def test_the_original_image_is_restored_verbatim_after_the_owned_controls(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("schedule_enable", sent), function="schedule_enable"
    )

    await producer.execute("aquarium_app_0006", trial_speed=TRIAL_SPEED)

    record = journal.load("aquarium_app_0006")
    assert record is not None
    assert record.actions[-1].action_id == "restore_schedule"
    assert journal.load_blob(record.actions[-1].expected_readback_sha256) == ORIGINAL_IMAGE
    assert journal.load_blob(record.actions[0].expected_readback_sha256) == TRIAL_IMAGE


@pytest.mark.asyncio
async def test_a_device_whose_timer_is_already_on_is_admitted_for_the_feeding_trial(
    tmp_path: Path,
) -> None:
    """Feeding forces the timer off in both app frames, so only the owned restore returns it."""

    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("feed_switch", sent), function="feed_switch"
    )

    await producer.execute("aquarium_app_0007")

    record = journal.load("aquarium_app_0007")
    assert record is not None
    for index in (0, 1):
        assert (
            json.loads(journal.load_blob(record.actions[index].expected_readback_sha256))["TimerON"]
            is False
        )
    restored = json.loads(journal.load_blob(record.actions[2].expected_readback_sha256))
    assert restored["TimerON"] is True


@pytest.mark.asyncio
async def test_a_timer_run_from_an_out_of_bounds_original_schedule_is_refused(
    tmp_path: Path,
) -> None:
    """A device whose timer is on gets that image back while it runs, so it has to be bounded."""

    sent: list[bytes] = []
    initial = _status(image=UNBOUNDED_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=SPEED)
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([initial], sent)], function="schedule_enable"
    )

    with pytest.raises(QualificationProductionError, match="original schedule is outside"):
        await producer.execute("aquarium_app_0008", trial_speed=TRIAL_SPEED)

    assert sent == []
    assert journal.load("aquarium_app_0008") is None


@pytest.mark.asyncio
async def test_a_timer_off_device_may_keep_an_unbounded_original_image(tmp_path: Path) -> None:
    """With the timer off, that image is restored without this operation ever running it."""

    sent: list[bytes] = []
    states = (
        _status(image=UNBOUNDED_IMAGE),
        _status(image=TRIAL_IMAGE),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=TRIAL_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=TRIAL_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=UNBOUNDED_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
    )
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent))
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="schedule_enable"
    )

    result = await producer.execute("aquarium_app_0009", trial_speed=TRIAL_SPEED)

    assert result.receipt_count == 2
    record = journal.load("aquarium_app_0009")
    assert record is not None
    assert journal.load_blob(record.actions[-1].expected_readback_sha256) == UNBOUNDED_IMAGE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"enabled": False}, "requires the pump on"),
        ({"speed": 46}, "output is outside its bounds"),
        ({"speed": 20}, "output is outside its bounds"),
        ({"timer": True, "auto_mode": _AUTO, "auto_gears": 90}, "output is outside its bounds"),
    ),
)
async def test_a_device_outside_the_admission_envelope_is_refused_before_any_write(
    tmp_path: Path, kwargs: dict, match: str
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([_status(**kwargs)], sent)], function="feed_switch"
    )

    with pytest.raises(QualificationProductionError, match=match):
        await producer.execute("aquarium_app_0010")

    assert sent == []
    assert journal.load("aquarium_app_0010") is None


@pytest.mark.asyncio
async def test_the_trial_speed_belongs_only_to_the_timer_qualification(tmp_path: Path) -> None:
    feed, _j, _l, _r, _f = _producer(tmp_path / "a", [], function="feed_switch")
    with pytest.raises(ValueError, match="requires its own trial speed"):
        await feed.execute("aquarium_app_0011", trial_speed=TRIAL_SPEED)

    timer, _j2, _l2, _r2, _f2 = _producer(tmp_path / "b", [], function="schedule_enable")
    with pytest.raises(ValueError, match="requires its own trial speed"):
        await timer.execute("aquarium_app_0012")


@pytest.mark.asyncio
@pytest.mark.parametrize("speed", (46, 29, 100))
async def test_a_trial_speed_outside_the_first_write_bounds_is_refused(
    tmp_path: Path, speed: int
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([TIMER_INITIAL], sent)], function="schedule_enable"
    )

    with pytest.raises(QualificationProductionError, match="trial speed"):
        await producer.execute("aquarium_app_0013", trial_speed=speed)

    assert sent == []


def test_an_unsupported_function_is_refused_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not supported"):
        _producer(tmp_path, [], function="transport_mode")
    with pytest.raises(ValueError, match="not supported"):
        _producer(tmp_path, [], function="motor_speed")


@pytest.mark.asyncio
async def test_a_readback_that_does_not_match_preserves_uncertainty(tmp_path: Path) -> None:
    sent: list[bytes] = []
    sessions = [
        _Session([FEED_INITIAL], sent),
        _Session([FEED_INITIAL, FEED_INITIAL], sent),
    ]
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="feed_switch"
    )

    with pytest.raises(QualificationProductionError, match="readback did not match"):
        await producer.execute("aquarium_app_0014")

    record = journal.load("aquarium_app_0014")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert not (tmp_path / "output" / "qualifications.json").exists()


@pytest.mark.asyncio
async def test_an_uncertain_trial_recovers_the_owned_union_without_resending_it(
    tmp_path: Path,
) -> None:
    """ACK arrives, the readback faults, and generic recovery puts the owned fields back."""

    target = _binding()
    sent: list[bytes] = []
    sessions = [
        _Session([FEED_INITIAL], sent),
        _Session([FEED_INITIAL, OSError("readback failed")], sent),
    ]
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="feed_switch", target=target
    )

    with pytest.raises(OSError):
        await producer.execute("aquarium_app_recover")

    record = journal.load("aquarium_app_recover")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert record.actions[0].acknowledgement_sha256 is not None
    assert record.actions[1].phase is ActionPhase.PREPARED
    assert record.actions[2].phase is ActionPhase.PREPARED
    trial_payload = sent[0]
    assert not tuple((tmp_path / "receipts").iterdir())

    recovery_sent: list[bytes] = []
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory([_Session([FEED_ON, FEED_INITIAL], recovery_sent)]),
    )

    recovered = await coordinator.recover("aquarium_app_recover")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert len(recovery_sent) == 1
    assert trial_payload not in recovery_sent
    settled = journal.load("aquarium_app_recover")
    assert settled is not None
    assert settled.actions[1].diagnostic == "forward_never_attempted"
    assert settled.actions[2].diagnostic == "forward_never_attempted"
    restored = json.loads(journal.load_blob(record.actions[0].baseline_readback_sha256))
    assert restored["FeedSwitch"] is False
    assert restored["TimerON"] is True
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_a_swapped_readback_blob_refuses_to_finalize(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("feed_switch", sent), function="feed_switch"
    )
    await producer.execute("aquarium_app_0015")
    record = journal.load("aquarium_app_0015")
    assert record is not None

    actions = list(record.actions)
    swapped = journal.install_blob(FEED_OFF)
    actions[0] = actions[0].model_copy(
        update={"diagnostic": "aquarium_app_status_sha256." + swapped}
    )
    journal.save(record, record.evolve(actions=tuple(actions), now=NOW))

    replay, _j, _l, _r, _f = _producer(tmp_path, [], function="feed_switch")
    replay._journal = journal  # noqa: SLF001 - replay the tampered durable state under test
    with pytest.raises(QualificationProductionError, match="preserved aquarium readback differs"):
        await replay.execute("aquarium_app_0015")


def test_the_planned_trial_image_covers_the_whole_day_without_gaps_or_zero_rows() -> None:
    schedule = planned_trial_schedule(TRIAL_SPEED)
    assert len(schedule.slots) == 48
    assert schedule.slots[0].start == "00:00" and schedule.slots[-1].end == "24:00"
    image = schedule.compile_image()
    rows = [image[index * 6 : (index + 1) * 6] for index in range(48)]
    assert not any(row == b"\x00" * 6 for row in rows)
    assert not any(row == b"\xee" * 6 for row in rows)
    assert {row[4] for row in rows} == {1}
    assert {row[5] for row in rows} == {TRIAL_SPEED}


@pytest.mark.asyncio
async def test_a_trial_image_identical_to_the_stored_one_is_not_disqualifying(
    tmp_path: Path,
) -> None:
    """The receipts attest the timer commands, whose payloads differ whatever the image is."""

    same = planned_trial_schedule(TRIAL_SPEED).compile_image()
    states = (
        _status(image=same),
        _status(image=same),
        _status(image=same, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=same, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=same, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=same, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
    )
    sent: list[bytes] = []
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent))
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="schedule_enable"
    )

    result = await producer.execute("aquarium_app_0016", trial_speed=TRIAL_SPEED)

    assert result.receipt_count == 2
    record = journal.load("aquarium_app_0016")
    assert record is not None
    assert record.actions[1].forward_payload_sha256 != record.actions[2].forward_payload_sha256


@pytest.mark.asyncio
async def test_the_original_image_is_bounded_by_the_configured_range_not_the_trial_ceiling(
    tmp_path: Path,
) -> None:
    """Putting the device's own program back is not this operation choosing a new output."""

    target = _binding()
    wide = _epoch(target).model_copy(update={"max_power": 60})
    original = ReturnPumpSchedule.model_validate(
        {
            "family": "aquarium",
            "slots": [
                {
                    "slot_index": 0,
                    "start": "00:00",
                    "end": "24:00",
                    "mode": "auto",
                    "speed": 55,
                }
            ],
        }
    ).compile_image()
    states = (
        _status(image=original, timer=True, auto_mode=_AUTO, auto_gears=SPEED),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=SPEED),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=TRIAL_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=original, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
    )
    sent: list[bytes] = []
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent))
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="schedule_enable", target=target, epoch=wide
    )

    result = await producer.execute("aquarium_app_0017", trial_speed=TRIAL_SPEED)

    assert result.receipt_count == 2
    record = journal.load("aquarium_app_0017")
    assert record is not None
    assert journal.load_blob(record.actions[-1].expected_readback_sha256) == original


@pytest.mark.asyncio
async def test_actions_pointed_at_another_snapshot_refuse_to_finalize(tmp_path: Path) -> None:
    """The recorded baselines tie this operation to the raw it actually started from."""

    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("feed_switch", sent), function="feed_switch"
    )
    await producer.execute("aquarium_app_0018")
    record = journal.load("aquarium_app_0018")
    assert record is not None

    other = journal.install_blob(_status(timer=True, auto_mode=_AUTO, auto_gears=SPEED, speed=41))
    actions = tuple(
        action.model_copy(update={"baseline_status_sha256": other}) for action in record.actions
    )
    journal.save(record, record.evolve(actions=actions, now=NOW))

    replay, _j, _l, _r, _f = _producer(tmp_path, [], function="feed_switch")
    replay._journal = journal  # noqa: SLF001 - replay the tampered durable state under test
    with pytest.raises(QualificationProductionError, match="recorded baselines do not match"):
        await replay.execute("aquarium_app_0018")


@pytest.mark.asyncio
async def test_a_restored_program_above_the_trial_ceiling_is_accepted_at_the_configured_range(
    tmp_path: Path,
) -> None:
    """The last readback really carries the 55 gear, so the restoring branch is the one tested."""

    target = _binding()
    wide = _epoch(target).model_copy(update={"max_power": 60})
    original = ReturnPumpSchedule.model_validate(
        {
            "family": "aquarium",
            "slots": [
                {
                    "slot_index": 0,
                    "start": "00:00",
                    "end": "24:00",
                    "mode": "auto",
                    "speed": 55,
                }
            ],
        }
    ).compile_image()
    states = (
        _status(image=original, timer=True, auto_mode=_AUTO, auto_gears=SPEED),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=SPEED),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=TRIAL_IMAGE, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        # The owned restore turns the timer back on, and the original program then reports 55.
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO, auto_gears=TRIAL_SPEED),
        _status(image=original, timer=True, auto_mode=_AUTO, auto_gears=55),
    )
    sent: list[bytes] = []
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent))
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="schedule_enable", target=target, epoch=wide
    )

    result = await producer.execute("aquarium_app_0019", trial_speed=TRIAL_SPEED)

    assert result.receipt_count == 2
    record = journal.load("aquarium_app_0019")
    assert record is not None
    final = journal.load_blob(
        record.actions[-1].diagnostic.removeprefix("aquarium_app_status_sha256.")
    )
    assert final[3] == 55


@pytest.mark.asyncio
async def test_a_trial_frame_reporting_above_the_ceiling_is_still_refused(tmp_path: Path) -> None:
    """The looser range belongs to the restoring frames only, never to a trial one."""

    target = _binding()
    wide = _epoch(target).model_copy(update={"max_power": 60})
    sent: list[bytes] = []
    sessions = [
        _Session([TIMER_INITIAL], sent),
        _Session([TIMER_INITIAL, _status(image=TRIAL_IMAGE, speed=55)], sent),
    ]
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="schedule_enable", target=target, epoch=wide
    )

    with pytest.raises(QualificationProductionError, match="output is outside its bounds"):
        await producer.execute("aquarium_app_0020", trial_speed=TRIAL_SPEED)

    record = journal.load("aquarium_app_0020")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_an_uncertain_timer_on_recovers_the_timer_before_the_original_image(
    tmp_path: Path,
) -> None:
    """The ON frame acknowledges and its readback faults, so the timer state is unknown.

    Generic recovery must put the initial TimerOFF back before the original image returns, and
    must never replay the seed-bearing forward frame that caused the uncertainty.
    """

    target = _binding()
    sent: list[bytes] = []
    sessions = [
        _Session([TIMER_INITIAL], sent),
        _Session([TIMER_INITIAL, TIMER_TRIAL], sent),
        _Session([TIMER_TRIAL, OSError("readback failed")], sent),
    ]
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="schedule_enable", target=target
    )

    with pytest.raises(OSError):
        await producer.execute("aquarium_timer_recover", trial_speed=TRIAL_SPEED)

    record = journal.load("aquarium_timer_recover")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.VERIFIED
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert record.actions[1].acknowledgement_sha256 is not None
    seed_payload = sent[1]
    assert not tuple((tmp_path / "receipts").iterdir())

    recovery_sent: list[bytes] = []
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory(
            [
                _Session([TIMER_ON, TIMER_TRIAL], recovery_sent),
                _Session([TIMER_TRIAL, TIMER_INITIAL], recovery_sent),
            ]
        ),
    )

    recovered = await coordinator.recover("aquarium_timer_recover")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert len(recovery_sent) == 2
    # The timer is turned off first and the original image restored after it.
    assert recovery_sent[0] == journal.load_blob(record.actions[1].rollback_payload_sha256)
    assert recovery_sent[1] == journal.load_blob(record.actions[0].rollback_payload_sha256)
    assert seed_payload not in recovery_sent
    settled = journal.load("aquarium_timer_recover")
    assert settled is not None
    for index in (2, 3, 4):
        assert settled.actions[index].diagnostic == "forward_never_attempted"
    assert journal.load_blob(record.actions[0].baseline_readback_sha256) == ORIGINAL_IMAGE
