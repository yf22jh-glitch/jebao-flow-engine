from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    ManualFlowQualificationProducer,
    QualificationArtifactStore,
    QualificationEpoch,
    QualificationProductionError,
    _manual_flow_state,
)
from jebao_flow.local_control.resolver import (
    CurrentEndpointResolutionError,
    ResolvedEndpoint,
)
from jebao_flow.protocol.profiles import DC_PUMP_PRO, LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def _binding(
    marker: str = "a",
    *,
    product_key: str = LOCAL_WAVEMAKER_PRO.product_key,
) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="qualification_target",
        product_key=product_key,
        physical_identity_key=marker * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


def _epoch(
    *,
    target: LocalDeviceBinding | None = None,
    minimum: int = 30,
    maximum: int = 75,
    manifest_marker: str = "2",
) -> QualificationEpoch:
    return QualificationEpoch(
        target=target or _binding(),
        config_sha256="1" * 64,
        manifest_sha256=manifest_marker * 64,
        manifest_generation="3" * 32,
        min_power=minimum,
        max_power=maximum,
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


def _status(
    *,
    flow: int = 40,
    enabled: bool = True,
    timer_enabled: bool = False,
    linkage_code: int = 0,
    mode_code: int = 2,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = int(enabled) | (int(timer_enabled) << 1) | (linkage_code << 2)
    raw[1] = mode_code
    raw[2] = flow
    return bytes(raw)


def _bar_status(
    *,
    flow: int = 40,
    enabled: bool = True,
    timer_enabled: bool = False,
    linkage_code: int = 0,
    mode_code: int = 3,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER.raw_status_size)
    state_bits = int(enabled) | (int(timer_enabled) << 3) | (mode_code << 5) | (linkage_code << 7)
    raw[:2] = state_bits.to_bytes(2, "big")
    raw[2] = flow
    return bytes(raw)


def _dc_status(*, flow=40, enabled=True, timer_enabled=False, mode_code=0):
    # Product schema layout only; this fixture does not model firmware state transitions.
    raw = bytearray(DC_PUMP_PRO.raw_status_size)
    raw[0] = int(enabled) | (int(timer_enabled) << 1)
    raw[1] = mode_code
    raw[2] = flow
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
    def __init__(
        self,
        endpoint: ResolvedEndpoint,
        *,
        fail_at: int | None = None,
    ) -> None:
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
        endpoints: list[ResolvedEndpoint] = []
        for target in targets:
            endpoints.append(await self.resolve(target))
        return tuple(endpoints)


class _Session:
    def __init__(
        self,
        statuses: list[bytes],
        sent: list[bytes],
        *,
        on_send=None,
        acknowledgement: bytes = b"acknowledged",
        send_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.sent = sent
        self.on_send = on_send
        self.acknowledgement = acknowledgement
        self.send_error = send_error
        self.quarantined = False

    async def connect(self) -> None:
        pass

    async def authenticate(self) -> bytes:
        return b"private-authentication"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
        return self.statuses.pop(0)

    async def send_raw_control(self, payload: bytes) -> bytes:
        self.sent.append(bytes(payload))
        if self.on_send is not None:
            self.on_send()
        if self.send_error is not None:
            raise self.send_error
        return self.acknowledgement

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
    epoch_provider,
    resolver: _Resolver,
    writer_factory: _WriterFactory,
    artifacts=None,
) -> tuple[ManualFlowQualificationProducer, LocalControlJournalStore, _Lease]:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    return (
        ManualFlowQualificationProducer(
            epoch_provider=epoch_provider,
            resolver_factory=lambda: resolver,
            journal=journal,
            lease=lease,
            artifacts=artifacts or _artifacts(tmp_path),
            writer_factory=writer_factory,
            clock=lambda: NOW,
        ),
        journal,
        lease,
    )


@pytest.mark.asyncio
async def test_success_uses_one_three_action_journal_and_two_receipts(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=40)
    lower = _status(flow=39)
    sent: list[bytes] = []
    journal_seen_before_send: list[bool] = []

    def _assert_journal_is_complete() -> None:
        record = journal.load("qualification_0001")
        assert record is not None
        journal.load_blob(record.intent_sha256)
        for action in record.actions:
            for digest in (
                action.forward_payload_sha256,
                action.rollback_payload_sha256,
                action.baseline_status_sha256,
                action.expected_readback_sha256,
                action.baseline_readback_sha256,
            ):
                journal.load_blob(digest)
        journal_seen_before_send.append(True)

    sessions = [
        _Session(
            [baseline, baseline],
            sent,
            on_send=_assert_journal_is_complete,
        ),
        _Session([baseline, lower], sent),
        _Session([lower, baseline], sent),
    ]
    resolver = _Resolver(endpoint)
    writer_factory = _WriterFactory(sessions)
    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=writer_factory,
    )

    result = await producer.execute("qualification_0001", downward_step=1)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert result.receipt_count == 2
    assert len(sent) == 3
    assert sent[0] == sent[2]
    assert sent[1] != sent[0]
    assert journal_seen_before_send == [True]
    assert resolver.calls == 3
    assert writer_factory.created == 3
    assert lease.acquired == [(target.physical_identity_key,)]
    record = journal.load("qualification_0001")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert tuple(action.action_id for action in record.actions) == (
        "same_value",
        "lower_power",
        "planned_restore",
    )
    assert all(action.phase is ActionPhase.VERIFIED for action in record.actions)
    binding = _qualification_binding(tmp_path)
    receipts = verify_qualification_receipts(
        binding,
        _receipt_root_for_test=tmp_path / "receipts",
    )
    assert len(receipts) == 2
    assert receipts[0].request_id != receipts[1].request_id
    assert qualified_physical_identity_keys(
        binding,
        _receipt_root_for_test=tmp_path / "receipts",
    ) == frozenset({target.physical_identity_key})
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (tmp_path / "receipts").iterdir())
    assert (tmp_path / "output").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "output" / "qualifications.json").stat().st_mode & 0o777 == 0o600


def test_family_profiles_preserve_pro_probes_and_use_the_bar_timer_decoder(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    original = ConnectedDeviceWriter.prepare_scalar

    def _record(self, intent, current_status):
        calls.append((intent.function, intent.value))
        return original(self, intent, current_status)

    monkeypatch.setattr(ConnectedDeviceWriter, "prepare_scalar", _record)

    pro_target = _binding()
    pro_writer = ConnectedDeviceWriter(
        ResolvedEndpoint(pro_target, "current-address", wifi_firmware_version="wifi-current"),
        session_factory=lambda _address: _Session([], []),
    )
    pro_state = _manual_flow_state(pro_writer, pro_target, _status())
    assert (
        pro_state.enabled,
        pro_state.timer_enabled,
        pro_state.mode,
        pro_state.linkage,
        pro_state.flow,
    ) == (True, False, "constant", "independent", 40)
    assert calls == [
        ("schedule_enable", False),
        ("linkage_role", "independent"),
        ("manual_mode", "constant"),
        ("manual_flow", 30),
    ]

    calls.clear()
    bar_target = _binding(product_key=LOCAL_WAVEMAKER.product_key)
    bar_writer = ConnectedDeviceWriter(
        ResolvedEndpoint(bar_target, "current-address", wifi_firmware_version="wifi-current"),
        session_factory=lambda _address: _Session([], []),
    )
    bar_state = _manual_flow_state(bar_writer, bar_target, _bar_status())
    assert (
        bar_state.enabled,
        bar_state.timer_enabled,
        bar_state.mode,
        bar_state.linkage,
        bar_state.flow,
    ) == (True, False, "constant", "independent", 40)
    # Compound Bar mode/role commands are not needed to decode the current state.
    assert calls == [("manual_flow", 30)]


@pytest.mark.parametrize(
    "product,status_factory",
    [
        (LOCAL_WAVEMAKER.product_key, _bar_status),
        (DC_PUMP_PRO.product_key, _dc_status),
    ],
)
@pytest.mark.asyncio
async def test_product_success_uses_the_existing_three_action_contract(
    tmp_path: Path,
    product,
    status_factory,
) -> None:
    target = _binding(product_key=product)
    epoch = _epoch(target=target, maximum=100)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = status_factory(flow=40)
    lower = status_factory(flow=39)
    sent: list[bytes] = []
    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([baseline, baseline], sent),
                _Session([baseline, lower], sent),
                _Session([lower, baseline], sent),
            ]
        ),
    )

    result = await producer.execute("qualification_bar", downward_step=1)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert result.receipt_count == 2
    assert len(sent) == 3
    assert sent[0] == sent[2]
    assert sent[1] != sent[0]
    assert lease.acquired == [(target.physical_identity_key,)]
    record = journal.load("qualification_bar")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert tuple(action.action_id for action in record.actions) == (
        "same_value",
        "lower_power",
        "planned_restore",
    )
    assert all(action.phase is ActionPhase.VERIFIED for action in record.actions)
    binding = _qualification_binding(tmp_path)
    assert binding.product_key == product
    assert binding.function == "manual_flow"
    assert qualified_physical_identity_keys(
        binding,
        _receipt_root_for_test=tmp_path / "receipts",
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
async def test_bar_envelope_rejections_send_nothing(
    tmp_path: Path,
    status: bytes,
) -> None:
    target = _binding(product_key=LOCAL_WAVEMAKER.product_key)
    epoch = _epoch(target=target, maximum=100)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory([_Session([status], sent)]),
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("qualification_bar_bad", downward_step=1)

    assert sent == []
    assert journal.load("qualification_bar_bad") is None
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_downward_landing_may_equal_the_inclusive_minimum(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target, minimum=30)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=31)
    lower = _status(flow=30)
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([baseline, baseline], sent),
                _Session([baseline, lower], sent),
                _Session([lower, baseline], sent),
            ]
        ),
    )

    result = await producer.execute("qualification_minimum", downward_step=1)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert len(sent) == 3
    record = journal.load("qualification_minimum")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED


@pytest.mark.parametrize(
    ("status", "epoch", "step"),
    (
        (_status(enabled=False), _epoch(), 1),
        (_status(timer_enabled=True), _epoch(), 1),
        (_status(linkage_code=1), _epoch(), 1),
        (_status(mode_code=3), _epoch(), 1),
        (_status(flow=46), _epoch(), 1),
        (_status(flow=40), _epoch(minimum=40), 1),
        (_status(flow=40), _epoch(maximum=39), 1),
    ),
)
@pytest.mark.asyncio
async def test_envelope_rejections_send_nothing(
    tmp_path: Path,
    status: bytes,
    epoch: QualificationEpoch,
    step: int,
) -> None:
    endpoint = ResolvedEndpoint(
        epoch.target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    sent: list[bytes] = []
    writer_factory = _WriterFactory([_Session([status], sent)])
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=writer_factory,
    )

    with pytest.raises((QualificationProductionError, ValueError)):
        await producer.execute("qualification_bad", downward_step=step)

    assert sent == []
    assert journal.load("qualification_bad") is None
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.parametrize(
    ("session", "expected_error"),
    (
        (
            _Session(
                [_status(flow=40)],
                [],
                send_error=RuntimeError("injected send failure"),
            ),
            RuntimeError,
        ),
        (
            _Session([_status(flow=40), _status(flow=39)], []),
            QualificationProductionError,
        ),
        (
            _Session(
                [_status(flow=40)],
                [],
                send_error=asyncio.CancelledError(),
            ),
            asyncio.CancelledError,
        ),
    ),
)
@pytest.mark.asyncio
async def test_first_phase_faults_never_retry_or_issue_receipts(
    tmp_path: Path,
    session: _Session,
    expected_error: type[BaseException],
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory([session]),
    )

    with pytest.raises(expected_error):
        await producer.execute("qualification_fault", downward_step=1)

    record = journal.load("qualification_fault")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert len(session.sent) == 1
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_empty_ack_body_is_preserved_after_the_session_confirms_the_response(
    tmp_path: Path,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=40)
    lower = _status(flow=39)
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([baseline, baseline], sent, acknowledgement=b""),
                _Session([baseline, lower], sent, acknowledgement=b""),
                _Session([lower, baseline], sent, acknowledgement=b""),
            ]
        ),
    )

    result = await producer.execute("qualification_empty_ack", downward_step=1)

    empty_sha256 = hashlib.sha256(b"").hexdigest()
    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert result.receipt_count == 2
    assert len(sent) == 3
    record = journal.load("qualification_empty_ack")
    assert record is not None
    assert all(action.acknowledgement_sha256 == empty_sha256 for action in record.actions)
    assert journal.load_blob(empty_sha256) == b""
    binding = _qualification_binding(tmp_path)
    receipts = verify_qualification_receipts(
        binding,
        _receipt_root_for_test=tmp_path / "receipts",
    )
    assert len(receipts) == 2
    assert all(receipt.acknowledgement_sha256 == empty_sha256 for receipt in receipts)


@pytest.mark.asyncio
async def test_firmware_change_stops_before_the_next_send(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint)
    baseline = _status(flow=40)
    sent: list[bytes] = []

    def _change_firmware() -> None:
        resolver.endpoint = ResolvedEndpoint(
            target,
            "current-address",
            wifi_firmware_version="wifi-changed",
        )

    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=_WriterFactory(
            [_Session([baseline, baseline], sent, on_send=_change_firmware)]
        ),
    )

    with pytest.raises(QualificationProductionError, match="firmware changed"):
        await producer.execute("qualification_firmware", downward_step=1)

    record = journal.load("qualification_firmware")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 1
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.parametrize("step", (0, 6, -1))
@pytest.mark.asyncio
async def test_invalid_step_stops_before_resolution(tmp_path: Path, step: int) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    resolver = _Resolver(
        ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    )
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=_WriterFactory([]),
    )

    with pytest.raises(ValueError):
        await producer.execute("qualification_step", downward_step=step)

    assert resolver.calls == 0
    assert journal.load("qualification_step") is None


@pytest.mark.asyncio
async def test_strict_resolution_and_binding_mismatch_send_nothing(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    wrong = _binding("b")
    resolver = _Resolver(
        ResolvedEndpoint(wrong, "wrong-address", wifi_firmware_version="wifi-current")
    )
    writer_factory = _WriterFactory([])
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=writer_factory,
    )

    with pytest.raises(QualificationProductionError, match="stable binding"):
        await producer.execute("qualification_wrong", downward_step=1)

    assert writer_factory.created == 0
    assert journal.load("qualification_wrong") is None


@pytest.mark.parametrize("reason", ("absent", "ambiguous"))
@pytest.mark.asyncio
async def test_strict_absence_or_ambiguity_stops_before_a_writer(
    tmp_path: Path,
    reason: str,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)

    class _UnavailableResolver(_Resolver):
        async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
            self.calls += 1
            raise CurrentEndpointResolutionError(f"current endpoint {reason}")

    resolver = _UnavailableResolver(
        ResolvedEndpoint(target, "unused", wifi_firmware_version="wifi-current")
    )
    writer_factory = _WriterFactory([])
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=writer_factory,
    )

    with pytest.raises(CurrentEndpointResolutionError, match=reason):
        await producer.execute("qualification_unavailable", downward_step=1)

    assert resolver.calls == 1
    assert writer_factory.created == 0
    assert journal.load("qualification_unavailable") is None


@pytest.mark.asyncio
async def test_final_restore_readback_must_keep_the_complete_envelope(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=40)
    lower = _status(flow=39)
    final_with_timer = _status(flow=40, timer_enabled=True)
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([baseline, baseline], sent),
                _Session([baseline, lower], sent),
                _Session([lower, final_with_timer], sent),
            ]
        ),
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("qualification_final", downward_step=1)

    record = journal.load("qualification_final")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[2].phase is ActionPhase.UNCERTAIN
    assert len(sent) == 3
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_step_active_crash_requires_exact_recovery_without_receipts(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=40)
    lower = _status(flow=39)
    sent: list[bytes] = []
    resolver = _Resolver(endpoint, fail_at=3)
    writer_factory = _WriterFactory(
        [_Session([baseline, baseline], sent), _Session([baseline, lower], sent)]
    )
    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=writer_factory,
    )

    with pytest.raises(CurrentEndpointResolutionError):
        await producer.execute("qualification_crash", downward_step=1)

    pending = journal.load("qualification_crash")
    assert pending is not None
    assert pending.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(action.phase for action in pending.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
    )
    assert len(sent) == 2
    assert not tuple((tmp_path / "receipts").iterdir())

    recovery_sent: list[bytes] = []
    recovery_writer = _WriterFactory(
        [
            _Session([lower, baseline], recovery_sent),
            _Session([baseline], recovery_sent),
        ]
    )
    recovery_resolver = _Resolver(endpoint)
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=recovery_resolver,  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=recovery_writer,
    )

    recovered = await coordinator.recover("qualification_crash")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovered.physical_write_count == 1
    assert len(recovery_sent) == 1
    assert recovery_sent[0] == sent[0]
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.parametrize(
    "product,status_factory",
    [
        (LOCAL_WAVEMAKER.product_key, _bar_status),
        (DC_PUMP_PRO.product_key, _dc_status),
    ],
)
@pytest.mark.asyncio
async def test_product_step_crash_uses_the_same_exact_recovery_contract(
    tmp_path: Path,
    product,
    status_factory,
) -> None:
    target = _binding(product_key=product)
    epoch = _epoch(target=target, maximum=100)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = status_factory(flow=40)
    lower = status_factory(flow=39)
    sent: list[bytes] = []
    resolver = _Resolver(endpoint, fail_at=3)
    writer_factory = _WriterFactory(
        [_Session([baseline, baseline], sent), _Session([baseline, lower], sent)]
    )
    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=resolver,
        writer_factory=writer_factory,
    )

    with pytest.raises(CurrentEndpointResolutionError):
        await producer.execute("qualification_bar_crash", downward_step=1)

    pending = journal.load("qualification_bar_crash")
    assert pending is not None
    assert pending.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(action.phase for action in pending.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
    )
    assert len(sent) == 2
    assert not tuple((tmp_path / "receipts").iterdir())

    recovery_sent: list[bytes] = []
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory(
            [
                _Session([lower, baseline], recovery_sent),
                _Session([baseline], recovery_sent),
            ]
        ),
    )

    recovered = await coordinator.recover("qualification_bar_crash")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovered.physical_write_count == 1
    assert recovery_sent == [sent[0]]
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_epoch_change_after_step_emits_no_receipts(tmp_path: Path) -> None:
    target = _binding()
    initial = _epoch(target=target)
    changed = _epoch(target=target, manifest_marker="7")
    current = [initial]
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=40)
    lower = _status(flow=39)
    sent: list[bytes] = []
    sessions = [
        _Session([baseline, baseline], sent),
        _Session([baseline, lower], sent, on_send=lambda: current.__setitem__(0, changed)),
    ]
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: current[0],
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(sessions),
    )

    with pytest.raises(QualificationProductionError, match="epoch changed"):
        await producer.execute("qualification_epoch", downward_step=1)

    record = journal.load("qualification_epoch")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 2
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_terminal_finalization_retry_is_offline_and_accepts_exact_files(
    tmp_path: Path,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    baseline = _status(flow=40)
    lower = _status(flow=39)
    sent: list[bytes] = []
    delegate = _artifacts(tmp_path)

    class _RaiseAfterFinalization:
        def finalize(self, receipts, binding):
            delegate.finalize(receipts, binding)
            raise QualificationProductionError("injected post-finalization failure")

    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([baseline, baseline], sent),
                _Session([baseline, lower], sent),
                _Session([lower, baseline], sent),
            ]
        ),
        artifacts=_RaiseAfterFinalization(),
    )
    with pytest.raises(QualificationProductionError, match="injected"):
        await producer.execute("qualification_retry", downward_step=1)
    terminal = journal.load("qualification_retry")
    assert terminal is not None and terminal.phase is TransactionPhase.VERIFIED

    def _must_not_run():
        raise AssertionError("terminal finalization touched the current epoch or network")

    retry = ManualFlowQualificationProducer(
        epoch_provider=_must_not_run,
        resolver_factory=_must_not_run,
        journal=journal,
        lease=lease,
        artifacts=delegate,
        writer_factory=_must_not_run,
        clock=lambda: NOW,
    )
    result = await retry.execute("qualification_retry", downward_step=1)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 0
    assert result.receipt_count == 2
    assert len(sent) == 3


def _qualification_binding(tmp_path: Path):
    return load_current_qualifications(
        tmp_path / "output" / "qualifications.json",
        _allow_ephemeral_for_test=True,
    )[0]
