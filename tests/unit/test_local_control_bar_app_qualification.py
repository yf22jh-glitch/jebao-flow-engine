"""Offline tests for the bounded Bar app-control qualifications.

These assert the software contract only. Reaching a verified journal here is not evidence that
the firmware accepted any command, and nothing in this file claims physical safety.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.bar_app_qualification import (
    BarAppControlQualificationIntent,
    BarAppControlQualificationProducer,
    bar_app_qualification_intent_bytes,
)
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
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
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
BOUNDS = (30, 45)
FLOW = 40
FREQUENCY = 80
_CLASSIC, _CONSTANT = 0, 3
_INDEPENDENT, _MASTER, _SLAVE = 0, 1, 2


def _binding(marker: str = "e") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="bar_app_target",
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
    enabled: bool = True,
    timer: bool = False,
    mode: int = _CONSTANT,
    linkage: int = _INDEPENDENT,
    flow: int = FLOW,
    frequency: int = FREQUENCY,
    pulse: bool = True,
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
    )
    raw[:2] = bits.to_bytes(2, "big")
    raw[2], raw[3], raw[4] = flow, frequency, 10
    raw[5], raw[6], raw[7] = 0, 0, 1
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
    def __init__(self, sessions: list[_Session]) -> None:
        self.sessions, self.created = sessions, 0

    def __call__(self, endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
        session = self.sessions.pop(0)
        self.created += 1
        return ConnectedDeviceWriter(
            endpoint,
            session_factory=lambda _address: session,
            control_flow_bounds=BOUNDS,
        )


def _producer(tmp_path: Path, sessions: list[_Session], *, function: str, target=None):
    target = target or _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint)
    factory = _WriterFactory(sessions)
    producer = BarAppControlQualificationProducer(
        function=function,
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


INITIAL = _status()
_AFTER_CLASSIC = _status(mode=_CLASSIC, pulse=False)
_AFTER_CONSTANT = _status(mode=_CONSTANT, pulse=False)
_PULSE_ON = _status(mode=_CLASSIC, pulse=True)
_AS_MASTER = _status(linkage=_MASTER, pulse=True)
_AS_SLAVE = _status(linkage=_SLAVE, pulse=False)
_FEEDING = _status(feed_switch=True)

_STATE_SEQUENCES = {
    "manual_mode": (INITIAL, _AFTER_CLASSIC, _AFTER_CONSTANT, INITIAL),
    "pulse_or_tide": (INITIAL, _AFTER_CLASSIC, _PULSE_ON, _AFTER_CLASSIC, INITIAL),
    "linkage_role": (INITIAL, _AS_MASTER, _AS_SLAVE, INITIAL),
    "feed_switch": (INITIAL, _FEEDING, INITIAL),
}

_EXPECTED_ACTIONS = {
    "manual_mode": ("trial_mode", "restore_mode", "owned_restore"),
    "pulse_or_tide": ("prepare_mode", "trial_pulse", "restore_pulse", "owned_restore"),
    "linkage_role": ("trial_master", "trial_slave", "owned_restore"),
    "feed_switch": ("trial_feed", "restore_feed"),
}

_EXPECTED_FUNCTIONS = {
    "manual_mode": ("manual_mode", "manual_mode", "bar_owned_controls"),
    "pulse_or_tide": ("manual_mode", "pulse_or_tide", "pulse_or_tide", "bar_owned_controls"),
    "linkage_role": ("linkage_role", "linkage_role", "bar_owned_controls"),
    "feed_switch": ("feed_switch", "feed_switch"),
}

FUNCTIONS = ("manual_mode", "pulse_or_tide", "linkage_role", "feed_switch")


def _sessions(function: str, sent: list[bytes], **kwargs) -> list[_Session]:
    """One session for preparation, then one per frame that reads before and after its write."""

    states = _STATE_SEQUENCES[function]
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent, **kwargs))
    return sessions


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

    result = await producer.execute("bar_app_0001")

    expected_frames = len(_EXPECTED_ACTIONS[function])
    assert result.phase is TransactionPhase.VERIFIED
    assert result.receipt_count == 2
    assert result.physical_write_count == expected_frames
    assert len(sent) == expected_frames
    # One resolution and one fresh session for preparation, then one of each per frame.
    assert resolver.calls == expected_frames + 1
    assert factory.created == expected_frames + 1
    assert lease.acquired == [(target.physical_identity_key,)]
    record = journal.load("bar_app_0001")
    assert record is not None
    assert tuple(a.action_id for a in record.actions) == _EXPECTED_ACTIONS[function]
    assert tuple(a.function for a in record.actions) == _EXPECTED_FUNCTIONS[function]
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("function", FUNCTIONS)
async def test_receipts_name_the_qualified_function_and_never_the_owned_restore(
    tmp_path: Path, function: str
) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, _journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function, target=target
    )

    await producer.execute("bar_app_0002")

    binding = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )[0]
    assert binding.function == function
    assert binding.product_key == LOCAL_WAVEMAKER.product_key
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert {item.function for item in receipts} == {function}
    assert qualified_physical_identity_keys(
        binding, _receipt_root_for_test=tmp_path / "receipts"
    ) == frozenset({target.physical_identity_key})


@pytest.mark.asyncio
@pytest.mark.parametrize("function", FUNCTIONS)
async def test_every_rollback_names_the_operation_initial_state(
    tmp_path: Path, function: str
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function
    )

    await producer.execute("bar_app_0003")

    record = journal.load("bar_app_0003")
    assert record is not None
    assert len({a.baseline_status_sha256 for a in record.actions}) == 1
    assert journal.load_blob(record.actions[0].baseline_status_sha256) == INITIAL
    for action in record.actions:
        baseline = json.loads(journal.load_blob(action.baseline_readback_sha256))
        assert baseline
        assert not any(name.startswith("Auto") for name in baseline)
        assert baseline.get("TimerON", False) is False
        assert baseline.get("Flow", FLOW) == FLOW
        assert baseline.get("Linkage", "independent") == "independent"


@pytest.mark.asyncio
@pytest.mark.parametrize("function", ("manual_mode", "pulse_or_tide", "linkage_role"))
async def test_the_owned_restore_returns_every_field_the_sequence_wrote(
    tmp_path: Path, function: str
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function
    )

    await producer.execute("bar_app_0004")

    record = journal.load("bar_app_0004")
    assert record is not None
    written = set()
    for action in record.actions[:-1]:
        written |= set(json.loads(journal.load_blob(action.expected_readback_sha256)))
    restored = json.loads(journal.load_blob(record.actions[-1].expected_readback_sha256))
    assert set(restored) == written
    # The initial PulseTide is true, and every sequence clears it, so the restore must return it.
    assert restored["PulseTide"] is True
    assert restored["Mode"] == "constant"
    assert restored["Linkage"] == "independent"
    assert restored["TimerON"] is False


@pytest.mark.asyncio
async def test_feed_switch_needs_no_extra_restore_frame(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("feed_switch", sent), function="feed_switch"
    )

    await producer.execute("bar_app_0005")

    record = journal.load("bar_app_0005")
    assert record is not None
    assert len(record.actions) == 2
    assert all(a.function == "feed_switch" for a in record.actions)
    first = json.loads(journal.load_blob(record.actions[0].expected_readback_sha256))
    second = json.loads(journal.load_blob(record.actions[1].expected_readback_sha256))
    assert first == {"FeedSwitch": True}
    assert second == {"FeedSwitch": False}


@pytest.mark.asyncio
async def test_a_device_that_starts_feeding_is_reported_and_still_restored(
    tmp_path: Path, caplog
) -> None:
    sent: list[bytes] = []
    states = (_FEEDING, INITIAL, _FEEDING)
    sessions = [_Session([states[0]], sent)]
    for index in range(len(states) - 1):
        sessions.append(_Session([states[index], states[index + 1]], sent))
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="feed_switch"
    )

    with caplog.at_level("WARNING"):
        await producer.execute("bar_app_0006")

    record = journal.load("bar_app_0006")
    assert record is not None
    first = json.loads(journal.load_blob(record.actions[0].expected_readback_sha256))
    second = json.loads(journal.load_blob(record.actions[1].expected_readback_sha256))
    assert first == {"FeedSwitch": False}
    assert second == {"FeedSwitch": True}
    assert "bar_app_feeding_progress_not_restorable" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("function", FUNCTIONS)
async def test_the_two_receipt_payloads_are_a_real_step_and_back(
    tmp_path: Path, function: str
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions(function, sent), function=function
    )

    await producer.execute("bar_app_0007")

    record = journal.load("bar_app_0007")
    assert record is not None
    intent = BarAppControlQualificationIntent.model_validate_json(
        journal.load_blob(record.intent_sha256)
    )
    first_index, second_index = (1, 2) if function == "pulse_or_tide" else (0, 1)
    first = record.actions[first_index]
    second = record.actions[second_index]
    assert first.forward_payload_sha256 != second.forward_payload_sha256
    assert intent.first_value != intent.second_value
    assert bar_app_qualification_intent_bytes(intent) == journal.load_blob(record.intent_sha256)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"enabled": False}, "pump off"),
        ({"timer": True}, "timer on"),
        ({"mode": _CLASSIC}, "mode is not the admission value"),
        ({"linkage": _MASTER}, "already linked"),
        ({"flow": 46}, "above the first-write ceiling"),
        ({"flow": 20}, "below the configured minimum"),
    ),
)
async def test_a_device_outside_the_admission_envelope_is_refused_before_any_write(
    tmp_path: Path, kwargs: dict, reason: str
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, [_Session([_status(**kwargs)], sent)], function="manual_mode"
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("bar_app_0008")

    assert sent == []
    assert journal.load("bar_app_0008") is None


@pytest.mark.asyncio
async def test_a_frame_whose_copied_values_drifted_stops_without_receipts(tmp_path: Path) -> None:
    """A fresh value the frame copies is caught by comparing it with the durable payload."""

    sent: list[bytes] = []
    drifted = _status(mode=_CLASSIC, pulse=False, frequency=FREQUENCY - 1)
    sessions = [
        _Session([INITIAL], sent),
        _Session([INITIAL, _AFTER_CLASSIC], sent),
        _Session([drifted, _AFTER_CONSTANT], sent),
    ]
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="manual_mode"
    )

    with pytest.raises(QualificationProductionError, match="frame payload changed"):
        await producer.execute("bar_app_0009")

    record = journal.load("bar_app_0009")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 1
    assert not (tmp_path / "output" / "qualifications.json").exists()


@pytest.mark.asyncio
async def test_a_field_this_operation_does_not_own_may_change_without_refusing(
    tmp_path: Path,
) -> None:
    """Feeding owns only ``FeedSwitch``, so an operator moving the frequency is not its business."""

    sent: list[bytes] = []
    moved_on = _status(feed_switch=True, frequency=55, mode=_CLASSIC)
    moved_off = _status(frequency=55, mode=_CLASSIC)
    sessions = [
        _Session([INITIAL], sent),
        _Session([INITIAL, _FEEDING], sent),
        _Session([moved_on, moved_off], sent),
    ]
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="feed_switch"
    )

    result = await producer.execute("bar_app_0017")

    assert result.receipt_count == 2
    record = journal.load("bar_app_0017")
    assert record is not None
    intent = BarAppControlQualificationIntent.model_validate_json(
        journal.load_blob(record.intent_sha256)
    )
    assert intent.owned_fields == ("FeedSwitch",)


@pytest.mark.asyncio
async def test_a_readback_that_does_not_match_preserves_uncertainty(tmp_path: Path) -> None:
    sent: list[bytes] = []
    sessions = [
        _Session([INITIAL], sent),
        # The device acknowledges but reports the mode unchanged.
        _Session([INITIAL, INITIAL], sent),
    ]
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="manual_mode"
    )

    with pytest.raises(QualificationProductionError, match="readback did not match"):
        await producer.execute("bar_app_0010")

    record = journal.load("bar_app_0010")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert record.actions[0].acknowledgement_sha256 is not None
    assert not (tmp_path / "output" / "qualifications.json").exists()


@pytest.mark.asyncio
async def test_a_send_that_never_acknowledges_issues_no_receipt(tmp_path: Path) -> None:
    sent: list[bytes] = []
    sessions = [
        _Session([INITIAL], sent),
        _Session([INITIAL, _AFTER_CLASSIC], sent, send_error=OSError("link lost")),
    ]
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="manual_mode"
    )

    with pytest.raises(OSError):
        await producer.execute("bar_app_0011")

    record = journal.load("bar_app_0011")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert record.actions[0].acknowledgement_sha256 is None
    assert not (tmp_path / "output" / "qualifications.json").exists()


@pytest.mark.asyncio
async def test_unfinished_durable_state_blocks_a_new_run(tmp_path: Path) -> None:
    sent: list[bytes] = []
    sessions = [
        _Session([INITIAL], sent),
        _Session([INITIAL, INITIAL], sent),
    ]
    producer, _journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="manual_mode"
    )
    with pytest.raises(QualificationProductionError):
        await producer.execute("bar_app_0012")

    producer2, _journal2, _lease2, _resolver2, _factory2 = _producer(
        tmp_path, [_Session([INITIAL], sent)], function="manual_mode"
    )
    with pytest.raises(QualificationProductionError, match="unfinished or recovered"):
        await producer2.execute("bar_app_0012")


def test_an_unsupported_function_is_refused_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not supported"):
        _producer(tmp_path, [], function="manual_flow")


@pytest.mark.asyncio
async def test_a_completed_operation_finalizes_without_writing_again(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("linkage_role", sent), function="linkage_role"
    )
    first = await producer.execute("bar_app_0013")

    producer2, _journal2, _lease2, _resolver2, _factory2 = _producer(
        tmp_path, [], function="linkage_role"
    )
    producer2._journal = journal  # noqa: SLF001 - reuse the durable state under test
    second = await producer2.execute("bar_app_0013")

    assert second.physical_write_count == 0
    assert second.receipt_count == 2
    assert second.qualification_sha256 == first.qualification_sha256
    assert len(sent) == 3


@pytest.mark.asyncio
async def test_the_role_receipts_write_the_two_linked_roles_in_their_app_shapes(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("linkage_role", sent), function="linkage_role"
    )

    await producer.execute("bar_app_0014")

    record = journal.load("bar_app_0014")
    assert record is not None
    master = json.loads(journal.load_blob(record.actions[0].expected_readback_sha256))
    slave = json.loads(journal.load_blob(record.actions[1].expected_readback_sha256))
    assert master["Linkage"] == "master"
    assert slave["Linkage"] == "slave"
    # The role never rides alone: both app shapes carry the manual parameters with it.
    assert master["TimerON"] is False and master["FeedSwitch"] is False
    assert master["Flow"] == FLOW and master["Frequency"] == FREQUENCY
    # edit_link_mode passes the current pulse bit through unconditionally...
    assert master["PulseTide"] is True
    # ...while edit_mode_save derives it from the mode, so a constant device clears it.
    assert slave["PulseTide"] is False


@pytest.mark.asyncio
async def test_the_mode_receipts_step_to_classic_and_back_to_constant(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("manual_mode", sent), function="manual_mode"
    )

    await producer.execute("bar_app_0015")

    record = journal.load("bar_app_0015")
    assert record is not None
    step = json.loads(journal.load_blob(record.actions[0].expected_readback_sha256))
    back = json.loads(journal.load_blob(record.actions[1].expected_readback_sha256))
    assert step["Mode"] == "classic" and back["Mode"] == "constant"
    assert step["Linkage"] == "independent" and step["TimerON"] is False
    # The mode command always clears the pulse bit, which is why the restore has to return it.
    assert step["PulseTide"] is False and back["PulseTide"] is False


@pytest.mark.asyncio
async def test_the_pulse_trial_always_steps_because_the_prepare_frame_clears_the_bit(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("pulse_or_tide", sent), function="pulse_or_tide"
    )

    await producer.execute("bar_app_0016")

    record = journal.load("bar_app_0016")
    assert record is not None
    prepare = json.loads(journal.load_blob(record.actions[0].expected_readback_sha256))
    trial = json.loads(journal.load_blob(record.actions[1].expected_readback_sha256))
    restore = json.loads(journal.load_blob(record.actions[2].expected_readback_sha256))
    assert prepare["Mode"] == "classic" and prepare["PulseTide"] is False
    assert trial == {"Flow": FLOW, "Frequency": FREQUENCY, "PulseTide": True}
    assert restore == {"Flow": FLOW, "Frequency": FREQUENCY, "PulseTide": False}
    # The preparation frame is a real manual_mode command, so it must never be a receipt.
    assert record.actions[0].function == "manual_mode"


@pytest.mark.asyncio
async def test_an_uncertain_trial_recovers_the_owned_union_without_resending_it(
    tmp_path: Path,
) -> None:
    """The pulse boundary: preparation succeeded, the trial acknowledged, its readback faulted.

    The acknowledgement is evidence the device may have taken the write, so it is kept and the
    action stays UNCERTAIN. Generic recovery then restores the fields this operation owns from
    its own initial snapshot, and never replays the forward frame that caused the uncertainty.
    """

    target = _binding()
    # Start with the pulse bit off, so the trial's forward bytes and its rollback really differ.
    initial = _status(pulse=False)
    sent: list[bytes] = []
    sessions = [
        _Session([initial], sent),
        _Session([initial, _AFTER_CLASSIC], sent),
        _Session([_AFTER_CLASSIC, OSError("readback failed")], sent),
    ]
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="pulse_or_tide", target=target
    )

    with pytest.raises(OSError):
        await producer.execute("bar_app_recover_pulse")

    record = journal.load("bar_app_recover_pulse")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.VERIFIED
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert record.actions[1].acknowledgement_sha256 is not None
    assert record.actions[2].phase is ActionPhase.PREPARED
    assert record.actions[3].phase is ActionPhase.PREPARED
    assert len(sent) == 2
    trial_payload = sent[1]
    assert record.actions[1].forward_payload_sha256 != record.actions[1].rollback_payload_sha256
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
                # The uncertain trial did take effect, so its rollback has real work to do.
                _Session([_PULSE_ON, _AFTER_CLASSIC], recovery_sent),
                _Session([_AFTER_CLASSIC, initial], recovery_sent),
            ]
        ),
    )

    recovered = await coordinator.recover("bar_app_recover_pulse")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert len(recovery_sent) == 2
    # The frame whose outcome is unknown is never re-sent.
    assert trial_payload not in recovery_sent
    # The two frames that were never attempted are closed without touching the device.
    settled = journal.load("bar_app_recover_pulse")
    assert settled is not None
    assert settled.actions[2].diagnostic == "forward_never_attempted"
    assert settled.actions[3].diagnostic == "forward_never_attempted"
    # The last rollback is the preparation frame's, which carries the whole owned union back.
    assert recovery_sent[-1] == journal.load_blob(record.actions[0].rollback_payload_sha256)
    restored = json.loads(journal.load_blob(record.actions[0].baseline_readback_sha256))
    assert restored["PulseTide"] is False
    assert restored["Mode"] == "constant"
    assert restored["Linkage"] == "independent"
    assert restored["FeedSwitch"] is False
    assert restored["TimerON"] is False
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_an_uncertain_role_trial_recovers_independence_and_skips_a_matching_rollback(
    tmp_path: Path,
) -> None:
    target = _binding()
    sent: list[bytes] = []
    sessions = [
        _Session([INITIAL], sent),
        _Session([INITIAL, _AS_MASTER], sent),
        _Session([_AS_MASTER, OSError("readback failed")], sent),
    ]
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path, sessions, function="linkage_role", target=target
    )

    with pytest.raises(OSError):
        await producer.execute("bar_app_recover_role")

    record = journal.load("bar_app_recover_role")
    assert record is not None
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert len(sent) == 2

    recovery_sent: list[bytes] = []
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory(
            [
                _Session([_AS_MASTER, INITIAL], recovery_sent),
                # The master frame's baseline is already present once the role is independent
                # again, so recovery reads it and sends nothing more.
                _Session([INITIAL], recovery_sent),
            ]
        ),
    )

    recovered = await coordinator.recover("bar_app_recover_role")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert len(recovery_sent) == 1
    assert sent[0] not in recovery_sent and sent[1] not in recovery_sent
    settled = journal.load("bar_app_recover_role")
    assert settled is not None
    assert settled.actions[0].diagnostic == "baseline_already_present"
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_a_swapped_readback_blob_refuses_to_finalize(tmp_path: Path) -> None:
    """The preserved readbacks are re-decoded on every finalize, not trusted as digests."""

    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, _sessions("feed_switch", sent), function="feed_switch"
    )
    await producer.execute("bar_app_0018")
    record = journal.load("bar_app_0018")
    assert record is not None

    # Re-point the last action at a status that does not show what that frame claimed to write.
    actions = list(record.actions)
    swapped = journal.install_blob(_FEEDING)
    actions[-1] = actions[-1].model_copy(update={"diagnostic": "bar_app_status_sha256." + swapped})
    journal.save(record, record.evolve(actions=tuple(actions), now=NOW))

    replay, _journal2, _lease2, _resolver2, _factory2 = _producer(
        tmp_path, [], function="feed_switch"
    )
    replay._journal = journal  # noqa: SLF001 - replay the tampered durable state under test
    with pytest.raises(QualificationProductionError, match="preserved bar app readback differs"):
        await replay.execute("bar_app_0018")
