"""Offline tests for the bounded Bar timer (schedule_enable) qualification.

These assert the software contract only. Reaching a verified journal here is not evidence that
the firmware accepted the seed, and nothing in this file claims physical safety.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.bar_timer_qualification import (
    BarTimerQualificationIntent,
    BarTimerQualificationProducer,
    planned_trial_schedule,
)
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, LocalDeviceWriteError
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalDeviceBinding
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
from jebao_flow.local_control.resolver import CurrentEndpointResolutionError, ResolvedEndpoint
from jebao_flow.local_control.wavemaker import WavemakerSchedule
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
LOCAL_NOON = datetime(2026, 9, 16, 12, tzinfo=UTC)
TRIAL_FLOW = 40
BOUNDS = (30, 45)
_CONSTANT_MODE = 3  # Mode enum: classic, sine, random, constant
_AUTO_CONSTANT = 4  # AutoMode enum: stopped, classic, sine, random, constant, feed
_AUTO_RANDOM = 3

TRIAL_IMAGE = planned_trial_schedule(TRIAL_FLOW).compile_image()
ORIGINAL_IMAGE = WavemakerSchedule.model_validate(
    {
        "family": "bar",
        "slots": [
            {
                "slot_index": 0,
                "start": "00:00",
                "end": "24:00",
                "mode": "random",
                "flow": 100,
                "frequency": 50,
                "pulse_tide": False,
            }
        ],
    }
).compile_image()


def _binding(marker: str = "e") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="bar_timer_target",
        product_key=LOCAL_WAVEMAKER.product_key,
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
    timer: bool = False,
    mode: int = _CONSTANT_MODE,
    linkage: int = 0,
    flow: int = 40,
    frequency: int = 80,
    auto_mode: int = 0,
    auto_flow: int = 0,
    pulse: bool = False,
    feed_switch: bool = False,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER.raw_status_size)
    bits = (
        int(enabled)
        | int(pulse) << 1
        | int(feed_switch) << 2
        | int(timer) << 3
        | mode << 5
        | linkage << 7
        | auto_mode << 9
    )
    raw[:2] = bits.to_bytes(2, "big")
    raw[2], raw[3], raw[4] = flow, frequency, 10
    raw[5], raw[6], raw[7] = auto_flow, 0, 1
    raw[8 : 8 + len(image)] = image
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
    def __init__(self, endpoint: ResolvedEndpoint, *, fail_at: int | None = None) -> None:
        self.endpoint, self.fail_at, self.calls = endpoint, fail_at, 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        if self.fail_at == self.calls:
            raise CurrentEndpointResolutionError("strict resolution failed")
        return self.endpoint

    async def resolve_many(self, targets):
        return tuple([await self.resolve(item) for item in targets])


class _Session:
    def __init__(self, statuses, sent, *, send_error: BaseException | None = None) -> None:
        self.statuses, self.sent, self.send_error = statuses, sent, send_error
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
    def __init__(self, sessions: list[_Session]) -> None:
        self.sessions, self.created = sessions, 0

    def __call__(self, endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
        session = self.sessions.pop(0)
        self.created += 1
        return ConnectedDeviceWriter(
            endpoint,
            session_factory=lambda _address: session,
            bar_timer_local_time=lambda: LOCAL_NOON,
            bar_timer_flow_bounds=BOUNDS,
        )


def _producer(tmp_path: Path, sessions: list[_Session], *, target=None, fail_at=None):
    target = target or _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint, fail_at=fail_at)
    factory = _WriterFactory(sessions)
    producer = BarTimerQualificationProducer(
        epoch_provider=lambda: _epoch(target),
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


def _happy_statuses() -> list[bytes]:
    return [
        _status(image=ORIGINAL_IMAGE),
        _status(image=TRIAL_IMAGE),
        _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
        _status(image=TRIAL_IMAGE, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
        _status(image=ORIGINAL_IMAGE, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
    ]


def test_planned_trial_image_covers_the_whole_day_without_gaps_or_zero_rows() -> None:
    schedule = planned_trial_schedule(TRIAL_FLOW)
    assert len(schedule.slots) == 48
    assert schedule.slots[0].start == "00:00" and schedule.slots[-1].end == "24:00"
    image = schedule.compile_image()
    rows = [image[index * 8 : (index + 1) * 8] for index in range(48)]
    assert not any(row == b"\x00" * 8 for row in rows)
    assert not any(row == b"\xee" * 8 for row in rows)


@pytest.mark.asyncio
async def test_success_is_four_actions_and_two_timer_receipts(tmp_path: Path) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path, [_Session(_happy_statuses(), sent)], target=target
    )

    result = await producer.execute("bar_timer_0001", trial_flow=TRIAL_FLOW)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 4
    assert result.receipt_count == 2
    assert len(sent) == 4
    record = journal.load("bar_timer_0001")
    assert record is not None
    assert tuple(a.action_id for a in record.actions) == (
        "trial_schedule",
        "timer_on",
        "timer_off",
        "restore_schedule",
    )
    assert tuple(a.function for a in record.actions) == (
        "schedule",
        "schedule_enable",
        "schedule_enable",
        "schedule",
    )
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)
    binding = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )[0]
    assert binding.function == "schedule_enable"
    assert binding.product_key == LOCAL_WAVEMAKER.product_key
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert qualified_physical_identity_keys(
        binding, _receipt_root_for_test=tmp_path / "receipts"
    ) == frozenset({target.physical_identity_key})


@pytest.mark.asyncio
async def test_every_rollback_names_the_operation_initial_state(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session(_happy_statuses(), sent)]
    )

    await producer.execute("bar_timer_0002", trial_flow=TRIAL_FLOW)

    record = journal.load("bar_timer_0002")
    assert record is not None
    # One initial snapshot owns every action, so recovery can never target a mid-operation state.
    assert len({a.baseline_status_sha256 for a in record.actions}) == 1
    assert all(a.restore_on_recovery is True for a in record.actions)
    trial, on, off, restore = record.actions
    # ON and OFF own different persistent field sets, so their rollback bytes differ by design.
    # What must hold is that both restore the initial TimerOFF and carry no Auto* seed.
    for action in (on, off):
        baseline = json.loads(journal.load_blob(action.baseline_readback_sha256))
        assert baseline["TimerON"] is False
        assert not [name for name in baseline if name.startswith("Auto")]
    assert on.forward_payload_sha256 != off.forward_payload_sha256
    # The image rollbacks restore the original bytes.
    assert journal.load_blob(trial.baseline_readback_sha256) == ORIGINAL_IMAGE
    assert journal.load_blob(restore.expected_readback_sha256) == ORIGINAL_IMAGE
    assert journal.load_blob(trial.expected_readback_sha256) == TRIAL_IMAGE
    intent = BarTimerQualificationIntent.model_validate_json(
        journal.load_blob(record.intent_sha256)
    )
    assert intent.trial_flow == TRIAL_FLOW and intent.baseline_timer_enabled is False


@pytest.mark.asyncio
async def test_stale_running_output_outside_bounds_stops_without_receipts(tmp_path: Path) -> None:
    """The seed is never compared for equality, but a real running output must stay bounded."""

    sent: list[bytes] = []
    statuses = _happy_statuses()
    # The device acknowledges ON but keeps a stale Auto output above the approved bound.
    statuses[2] = _status(image=TRIAL_IMAGE, timer=True, auto_mode=_AUTO_RANDOM, auto_flow=100)
    producer, journal, _lease, _resolver, _factory = _producer(tmp_path, [_Session(statuses, sent)])

    with pytest.raises(QualificationProductionError, match="approved bounds"):
        await producer.execute("bar_timer_stale", trial_flow=TRIAL_FLOW)

    record = journal.load("bar_timer_stale")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert record.actions[2].phase is ActionPhase.PREPARED
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.parametrize(
    "status",
    (
        _status(timer=True),
        _status(enabled=False),
        _status(linkage=1),
        _status(mode=0),
        _status(flow=91),
    ),
)
@pytest.mark.asyncio
async def test_initial_envelope_rejections_send_nothing(tmp_path: Path, status: bytes) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(tmp_path, [_Session([status], sent)])

    with pytest.raises((QualificationProductionError, LocalDeviceWriteError)):
        await producer.execute("bar_timer_bad", trial_flow=TRIAL_FLOW)

    assert sent == []
    assert journal.load("bar_timer_bad") is None
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_feeding_already_on_is_refused_before_the_first_write(tmp_path: Path) -> None:
    """ON writes FeedSwitch=false and OFF never writes it, so feeding would not be restored."""

    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([_status(feed_switch=True)], sent)]
    )

    with pytest.raises(QualificationProductionError, match="feeding off"):
        await producer.execute("bar_timer_feeding", trial_flow=TRIAL_FLOW)

    assert sent == []
    assert journal.load("bar_timer_feeding") is None


@pytest.mark.asyncio
async def test_trial_flow_above_the_first_write_ceiling_is_refused(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([_status()], sent)]
    )

    with pytest.raises(QualificationProductionError, match="first-write bounds"):
        await producer.execute("bar_timer_high", trial_flow=46)

    assert sent == []
    assert journal.load("bar_timer_high") is None


@pytest.mark.asyncio
async def test_trial_image_equal_to_the_original_is_rejected(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([_status(image=TRIAL_IMAGE)], sent)]
    )

    with pytest.raises(QualificationProductionError, match="differ from the original"):
        await producer.execute("bar_timer_same", trial_flow=TRIAL_FLOW)

    assert sent == []
    assert journal.load("bar_timer_same") is None


@pytest.mark.asyncio
async def test_recovery_turns_the_timer_off_before_restoring_and_never_resends_on(
    tmp_path: Path,
) -> None:
    target = _binding()
    sent: list[bytes] = []
    statuses = _happy_statuses()[:3]
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path,
        [_Session(statuses, sent, send_error=None)],
        target=target,
    )
    # Fail the OFF step so the operation stops while the timer is still on.
    producer._writer_factory.sessions.clear()  # type: ignore[attr-defined]
    producer._writer_factory.sessions.append(  # type: ignore[attr-defined]
        _Session(_happy_statuses()[:3], sent)
    )

    class _StopAfterOn(Exception):
        pass

    original_execute = producer._execute_action

    async def guarded(record, index, writer, prepared, permit, *, readback_validator):
        if index == 2:
            raise _StopAfterOn("operator stop before the OFF step")
        return await original_execute(
            record, index, writer, prepared, permit, readback_validator=readback_validator
        )

    producer._execute_action = guarded  # type: ignore[assignment]

    with pytest.raises(_StopAfterOn):
        await producer.execute("bar_timer_recover", trial_flow=TRIAL_FLOW)

    record = journal.load("bar_timer_recover")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(a.phase for a in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
        ActionPhase.PREPARED,
    )
    on_payload = sent[1]
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
                _Session(
                    [
                        _status(
                            image=TRIAL_IMAGE,
                            timer=True,
                            auto_mode=_AUTO_CONSTANT,
                            auto_flow=TRIAL_FLOW,
                        ),
                        _status(image=TRIAL_IMAGE, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
                    ],
                    recovery_sent,
                ),
                _Session(
                    [
                        _status(image=TRIAL_IMAGE, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
                        _status(image=ORIGINAL_IMAGE),
                    ],
                    recovery_sent,
                ),
            ]
        ),
    )

    recovered = await coordinator.recover("bar_timer_recover")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    # The timer is turned off first and the original image restored after it.
    assert len(recovery_sent) == 2
    assert on_payload not in recovery_sent
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_acknowledged_on_with_a_failed_readback_stays_uncertain_and_recovers_off_first(
    tmp_path: Path,
) -> None:
    """The ON frame is acknowledged but its readback faults, so the phase must stay UNCERTAIN.

    The acknowledgement is evidence that the device may have accepted the write, so it is kept,
    no receipt is issued, and generic recovery must still turn the timer off before restoring
    the original image.
    """

    target = _binding()
    sent: list[bytes] = []
    statuses: list[bytes | BaseException] = list(_happy_statuses()[:2])
    statuses.append(LocalDeviceWriteError("device readback failed after the acknowledgement"))
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path, [_Session(statuses, sent)], target=target
    )

    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("bar_timer_uncertain", trial_flow=TRIAL_FLOW)

    record = journal.load("bar_timer_uncertain")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(a.phase for a in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.UNCERTAIN,
        ActionPhase.PREPARED,
        ActionPhase.PREPARED,
    )
    # The acknowledgement survives the failure: uncertainty is not downgraded to "never sent".
    assert record.actions[1].acknowledgement_sha256 is not None
    assert journal.load_blob(record.actions[1].acknowledgement_sha256) == b"acknowledged"
    assert len(sent) == 2
    on_payload = sent[1]
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
                _Session(
                    [
                        _status(
                            image=TRIAL_IMAGE,
                            timer=True,
                            auto_mode=_AUTO_CONSTANT,
                            auto_flow=TRIAL_FLOW,
                        ),
                        _status(image=TRIAL_IMAGE, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
                    ],
                    recovery_sent,
                ),
                _Session(
                    [
                        _status(image=TRIAL_IMAGE, auto_mode=_AUTO_CONSTANT, auto_flow=TRIAL_FLOW),
                        _status(image=ORIGINAL_IMAGE),
                    ],
                    recovery_sent,
                ),
            ]
        ),
    )

    recovered = await coordinator.recover("bar_timer_uncertain")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert len(recovery_sent) == 2
    assert on_payload not in recovery_sent
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_inactive_pulse_tide_is_normalized_by_the_app_rule_and_reported(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The app OFF command writes PulseTide only in classic mode, so an inactive true is cleared.

    The accepted envelope requires constant mode, so this operation cannot restore that bit. It is
    reported rather than gated, and the terminal contract must not be described as restoring every
    initial control exactly.
    """

    sent: list[bytes] = []
    statuses = _happy_statuses()
    statuses[0] = _status(image=ORIGINAL_IMAGE, pulse=True)
    producer, journal, _lease, _resolver, _factory = _producer(tmp_path, [_Session(statuses, sent)])

    with caplog.at_level("WARNING"):
        result = await producer.execute("bar_timer_pulse", trial_flow=TRIAL_FLOW)

    assert result.phase is TransactionPhase.VERIFIED
    record = journal.load("bar_timer_pulse")
    assert record is not None
    off_expected = json.loads(journal.load_blob(record.actions[2].expected_readback_sha256))
    assert off_expected["PulseTide"] is False
    assert off_expected["Mode"] == "constant"
    diagnostics = [
        json.loads(message)
        for message in caplog.messages
        if message.startswith('{"event":"bar_timer_inactive_pulse_normalization_planned"')
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0]["initial_pulse_tide"] is True
    assert diagnostics[0]["expected_pulse_tide"] is False
    assert diagnostics[0]["initial_mode"] == "constant"


@pytest.mark.asyncio
async def test_no_pulse_diagnostic_when_the_initial_bit_is_already_false(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[bytes] = []
    producer, _journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session(_happy_statuses(), sent)]
    )

    with caplog.at_level("WARNING"):
        await producer.execute("bar_timer_nopulse", trial_flow=TRIAL_FLOW)

    assert not [
        message for message in caplog.messages
        if "bar_timer_inactive_pulse_normalization_planned" in message
    ]
