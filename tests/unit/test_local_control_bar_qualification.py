"""Offline byte-copy tests for the Bar manual-control qualification producer.

These tests make no firmware-behavior or hardware-qualification claims. They exercise the software
contract only: the three-frame same/step/restore journal, single-send, exact self restore, and that
receipts are emitted solely from a durably verified journal.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.bar_qualification import (
    BarScalarQualificationProducer,
    bar_qualification_intent_bytes,
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
from jebao_flow.local_control.resolver import (
    CurrentEndpointResolutionError,
    ResolvedEndpoint,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO

NOW = datetime(2026, 9, 16, 6, tzinfo=UTC)
_CONSTANT = 3  # Bar manual mode codes: classic=0, sine=1, random=2, constant=3.


def _binding(
    *,
    product_key: str = LOCAL_WAVEMAKER.product_key,
    marker: str = "a",
) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="bar_qualification_target",
        product_key=product_key,
        physical_identity_key=marker * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


def _epoch(*, target: LocalDeviceBinding | None = None, maximum: int = 100) -> QualificationEpoch:
    return QualificationEpoch(
        target=target or _binding(),
        config_sha256="1" * 64,
        manifest_sha256="2" * 64,
        manifest_generation="3" * 32,
        min_power=30,
        max_power=maximum,
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


def _bar_status(
    *,
    flow: int = 40,
    frequency: int = 20,
    enabled: bool = True,
    timer_enabled: bool = False,
    linkage_code: int = 0,
    mode_code: int = _CONSTANT,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER.raw_status_size)
    state_bits = int(enabled) | (int(timer_enabled) << 3) | (mode_code << 5) | (linkage_code << 7)
    raw[:2] = state_bits.to_bytes(2, "big")
    raw[2] = flow
    raw[3] = frequency
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
        self.endpoint = endpoint
        self.fail_at = fail_at
        self.calls = 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        if self.fail_at == self.calls:
            raise CurrentEndpointResolutionError("strict resolution failed")
        return self.endpoint

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        return tuple([await self.resolve(target) for target in targets])


class _Session:
    def __init__(
        self,
        statuses: list[bytes | BaseException],
        sent: list[bytes],
        *,
        send_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.sent = sent
        self.send_error = send_error
        self.quarantined = False

    async def connect(self) -> None:
        pass

    async def authenticate(self) -> bytes:
        return b"private-authentication"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
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
        self.sessions = sessions
        self.created = 0

    def __call__(self, endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
        session = self.sessions.pop(0)
        self.created += 1
        return ConnectedDeviceWriter(endpoint, session_factory=lambda _address: session)


def _artifacts(tmp_path: Path) -> QualificationArtifactStore:
    return QualificationArtifactStore.for_test(
        tmp_path / "receipts",
        tmp_path / "output" / "qualifications.json",
    )


def _producer(
    tmp_path: Path,
    *,
    function: str,
    sessions: list[_Session],
    target: LocalDeviceBinding,
    epoch: QualificationEpoch | None = None,
    resolver_fail_at: int | None = None,
    producer_type=BarScalarQualificationProducer,
) -> tuple[
    BarScalarQualificationProducer,
    LocalControlJournalStore,
    _Lease,
    _Resolver,
    _WriterFactory,
]:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint, fail_at=resolver_fail_at)
    writer_factory = _WriterFactory(sessions)
    producer = producer_type(
        function=function,
        epoch_provider=lambda: epoch or _epoch(target=target),
        resolver_factory=lambda: resolver,
        journal=journal,
        lease=lease,
        artifacts=_artifacts(tmp_path),
        writer_factory=writer_factory,
        clock=lambda: NOW,
    )
    return producer, journal, lease, resolver, writer_factory


def _loaded_binding(tmp_path: Path):
    return load_current_qualifications(
        tmp_path / "output" / "qualifications.json",
        _allow_ephemeral_for_test=True,
    )[0]


@pytest.mark.asyncio
async def test_manual_frequency_success_is_one_three_action_journal_and_two_receipts(
    tmp_path: Path,
) -> None:
    target = _binding()
    baseline = _bar_status(frequency=20)
    step = _bar_status(frequency=5)
    sent: list[bytes] = []
    producer, journal, lease, resolver, writer_factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
            _Session([step, baseline], sent),
        ],
    )

    result = await producer.execute("qualification_bar_frequency")

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert result.receipt_count == 2
    assert len(sent) == 3
    assert sent[0] == sent[2]
    assert sent[1] != sent[0]
    assert resolver.calls == 3
    assert writer_factory.created == 3
    assert lease.acquired == [(target.physical_identity_key,)]
    record = journal.load("qualification_bar_frequency")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert tuple(a.action_id for a in record.actions) == (
        "same_value",
        "step_value",
        "planned_restore",
    )
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)
    binding = _loaded_binding(tmp_path)
    assert binding.product_key == LOCAL_WAVEMAKER.product_key
    assert binding.function == "manual_frequency"
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert receipts[0].request_id != receipts[1].request_id
    assert qualified_physical_identity_keys(
        binding, _receipt_root_for_test=tmp_path / "receipts"
    ) == frozenset({target.physical_identity_key})


@pytest.mark.asyncio
async def test_manual_mode_success_restores_constant_baseline(tmp_path: Path) -> None:
    target = _binding()
    baseline = _bar_status(mode_code=_CONSTANT)
    step = _bar_status(mode_code=0)  # trial is the first distinct manual mode (classic)
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        function="manual_mode",
        target=target,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
            _Session([step, baseline], sent),
        ],
    )

    result = await producer.execute("qualification_bar_mode")

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert sent[0] == sent[2]
    assert sent[1] != sent[0]
    binding = _loaded_binding(tmp_path)
    assert binding.function == "manual_mode"
    assert qualified_physical_identity_keys(
        binding, _receipt_root_for_test=tmp_path / "receipts"
    ) == frozenset({target.physical_identity_key})


@pytest.mark.parametrize(
    "status",
    (
        _bar_status(enabled=False),
        _bar_status(timer_enabled=True),
        _bar_status(linkage_code=1),
        _bar_status(mode_code=0),
        _bar_status(flow=91),
    ),
)
@pytest.mark.asyncio
async def test_envelope_rejections_send_nothing(tmp_path: Path, status: bytes) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, journal, _lease, resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[_Session([status], sent)],
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("qualification_bar_bad")

    assert sent == []
    assert journal.load("qualification_bar_bad") is None
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_frequency_outside_app_range_sends_nothing(tmp_path: Path) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[_Session([_bar_status(frequency=0)], sent)],
    )

    with pytest.raises(QualificationProductionError, match="app range"):
        await producer.execute("qualification_bar_freq0")

    assert sent == []
    assert journal.load("qualification_bar_freq0") is None


def test_unsupported_function_is_rejected_at_construction(tmp_path: Path) -> None:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    with pytest.raises(QualificationProductionError, match="producer's scope"):
        BarScalarQualificationProducer(
            function="feed_switch",
            epoch_provider=lambda: _epoch(),
            resolver_factory=lambda: _Resolver(
                ResolvedEndpoint(_binding(), "a", wifi_firmware_version="f")
            ),
            journal=journal,
            lease=_Lease(),
            artifacts=_artifacts(tmp_path),
            writer_factory=_WriterFactory([]),
            clock=lambda: NOW,
        )


@pytest.mark.parametrize("feed_time", [0, 10])
async def test_bar_feeding_duration_requires_an_encodable_exact_baseline(tmp_path, feed_time):
    original = bytearray(_bar_status())
    original[4] = feed_time
    baseline = bytes(original)
    original[4] = 1
    step = bytes(original)
    sent = []
    producer, journal, *_ = _producer(
        tmp_path,
        function="feeding_duration",
        target=_binding(),
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
            _Session([step, baseline], sent),
        ],
    )
    if feed_time == 0:
        with pytest.raises(ValueError):
            await producer.execute("bar_feed_duration")
        assert not sent and journal.load("bar_feed_duration") is None
    else:
        result = await producer.execute("bar_feed_duration")
        assert result.receipt_count == 2 and sent[0] == sent[2] != sent[1]


@pytest.mark.asyncio
async def test_non_bar_target_is_rejected_before_any_write(tmp_path: Path) -> None:
    target = _binding(product_key=LOCAL_WAVEMAKER_PRO.product_key, marker="b")
    sent: list[bytes] = []
    producer, journal, _lease, resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[],
        epoch=_epoch(target=target),
    )

    with pytest.raises(QualificationProductionError, match="target encoder is unavailable"):
        await producer.execute("qualification_bar_wrongfamily")

    assert sent == []
    assert resolver.calls == 0
    assert journal.load("qualification_bar_wrongfamily") is None


@pytest.mark.asyncio
async def test_verified_replay_is_offline_and_emits_no_new_writes(tmp_path: Path) -> None:
    target = _binding()
    baseline = _bar_status(frequency=20)
    step = _bar_status(frequency=5)
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
            _Session([step, baseline], sent),
        ],
    )

    first = await producer.execute("qualification_bar_replay")
    replay = await producer.execute("qualification_bar_replay")

    assert first.qualification_sha256 == replay.qualification_sha256
    assert replay.physical_write_count == 0
    assert len(sent) == 3
    # The durable intent reloads to its exact canonical bytes on the offline replay path.
    record = journal.load("qualification_bar_replay")
    from jebao_flow.local_control.bar_qualification import BarScalarQualificationIntent

    intent_blob = journal.load_blob(record.intent_sha256)
    reparsed = BarScalarQualificationIntent.model_validate_json(intent_blob)
    assert bar_qualification_intent_bytes(reparsed) == intent_blob


# --- Failure and recovery: exercises the crash-safe scaffold, not firmware behavior. ---


@pytest.mark.asyncio
async def test_step_send_fault_is_recovery_required_and_emits_no_receipt(tmp_path: Path) -> None:
    target = _binding()
    baseline = _bar_status(frequency=20)
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline], sent, send_error=LocalDeviceWriteError("step ack failed")),
        ],
    )

    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("qualification_bar_stepfault")

    record = journal.load("qualification_bar_stepfault")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.VERIFIED
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert record.actions[2].phase is ActionPhase.PREPARED
    assert len(sent) == 2  # same baseline write + attempted step; the step is never retried
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_step_readback_fault_is_recovery_required_and_emits_no_receipt(
    tmp_path: Path,
) -> None:
    target = _binding()
    baseline = _bar_status(frequency=20)
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, LocalDeviceWriteError("readback failed")], sent),
        ],
    )

    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("qualification_bar_readbackfault")

    record = journal.load("qualification_bar_readbackfault")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_step_crash_recovers_own_baseline_via_generic_coordinator_without_forward_resend(
    tmp_path: Path,
) -> None:
    target = _binding()
    baseline = _bar_status(frequency=20)
    step = _bar_status(frequency=5)
    sent: list[bytes] = []
    producer, journal, lease, resolver, _factory = _producer(
        tmp_path,
        function="manual_frequency",
        target=target,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
        ],
        resolver_fail_at=3,  # crash while resolving for the planned restore frame
    )

    with pytest.raises(CurrentEndpointResolutionError):
        await producer.execute("qualification_bar_crash")

    record = journal.load("qualification_bar_crash")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(a.phase for a in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
    )
    assert len(sent) == 2
    assert not tuple((tmp_path / "receipts").iterdir())

    # The producer sets no native_context, so the generic coordinator.recover applies and restores
    # each action's own captured baseline (rollback == baseline), never re-sending the trial.
    recovery_sent: list[bytes] = []
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory(
            [
                _Session([step, baseline], recovery_sent),
                _Session([baseline], recovery_sent),
            ]
        ),
    )

    recovered = await coordinator.recover("qualification_bar_crash")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovered.physical_write_count == 1
    assert recovery_sent == [sent[0]]  # baseline restored; the trial (sent[1]) is never re-sent
    assert sent[1] not in recovery_sent
    assert not tuple((tmp_path / "receipts").iterdir())
