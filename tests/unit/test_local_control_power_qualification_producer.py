from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import (
    LocalControlCoordinator,
    LocalControlExecutionError,
)
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.power_qualification_producer import (
    PowerQualificationProducer,
    QualificationProductionError,
)
from jebao_flow.local_control.qualification import (
    load_current_qualifications,
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationEpoch,
)
from jebao_flow.local_control.resolver import (
    CurrentEndpointResolutionError,
    ResolvedEndpoint,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO

NOW = datetime(2026, 9, 13, 3, tzinfo=UTC)


def _binding(
    *,
    product_key: str = LOCAL_WAVEMAKER_PRO.product_key,
    marker: str = "a",
) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="power_qualification_target",
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
    maximum: int = 100,
    marker: str = "2",
) -> QualificationEpoch:
    return QualificationEpoch(
        target=target or _binding(),
        config_sha256="1" * 64,
        manifest_sha256=marker * 64,
        manifest_generation="3" * 32,
        min_power=minimum,
        max_power=maximum,
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


def _pro_status(
    *,
    enabled: bool = True,
    timer_enabled: bool = False,
    linkage_code: int = 0,
    mode_code: int = 2,
    flow: int = 40,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = int(enabled) | (int(timer_enabled) << 1) | (linkage_code << 2)
    raw[1] = mode_code
    raw[2] = flow
    return bytes(raw)


def _bar_status(
    *,
    enabled: bool = True,
    timer_enabled: bool = False,
    linkage_code: int = 0,
    mode_code: int = 3,
    flow: int = 40,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER.raw_status_size)
    state_bits = (
        int(enabled)
        | (int(timer_enabled) << 3)
        | (mode_code << 5)
        | (linkage_code << 7)
    )
    raw[:2] = state_bits.to_bytes(2, "big")
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
        firmware_by_call: tuple[str, ...] = (),
    ) -> None:
        self.endpoint = endpoint
        self.fail_at = fail_at
        self.firmware_by_call = firmware_by_call
        self.calls = 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        if self.fail_at == self.calls:
            raise CurrentEndpointResolutionError("strict resolution failed")
        firmware = (
            self.firmware_by_call[self.calls - 1]
            if self.calls <= len(self.firmware_by_call)
            else self.endpoint.wifi_firmware_version
        )
        return ResolvedEndpoint(
            self.endpoint.target,
            self.endpoint.address,
            wifi_firmware_version=firmware,
        )

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
        statuses: list[bytes | BaseException],
        sent: list[bytes],
        *,
        on_send=None,
        acknowledgement: bytes = b"acknowledged",
        send_error: BaseException | None = None,
        disconnect_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.sent = sent
        self.on_send = on_send
        self.acknowledgement = acknowledgement
        self.send_error = send_error
        self.disconnect_error = disconnect_error
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
        if self.on_send is not None:
            self.on_send()
        if self.send_error is not None:
            raise self.send_error
        return self.acknowledgement

    async def disconnect(self) -> None:
        if self.disconnect_error is not None:
            raise self.disconnect_error

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
) -> tuple[PowerQualificationProducer, LocalControlJournalStore, _Lease]:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    producer = PowerQualificationProducer(
        epoch_provider=epoch_provider,
        resolver_factory=lambda: resolver,
        journal=journal,
        lease=lease,
        artifacts=artifacts or _artifacts(tmp_path),
        writer_factory=writer_factory,
        clock=lambda: NOW,
    )
    return producer, journal, lease


def _coordinator(
    journal: LocalControlJournalStore,
    lease: _Lease,
    endpoint: ResolvedEndpoint,
    sessions: list[_Session],
) -> LocalControlCoordinator:
    return LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory(sessions),
    )


def _qualification_binding(tmp_path: Path):
    bindings = load_current_qualifications(
        tmp_path / "output" / "qualifications.json",
        _allow_ephemeral_for_test=True,
    )
    assert len(bindings) == 1
    return bindings[0]


@pytest.mark.parametrize(
    ("product_key", "status_factory"),
    (
        (LOCAL_WAVEMAKER_PRO.product_key, _pro_status),
        (LOCAL_WAVEMAKER.product_key, _bar_status),
    ),
)
@pytest.mark.asyncio
async def test_success_preinstalls_safer_off_mapping_and_sends_exactly_three_frames(
    tmp_path: Path,
    product_key: str,
    status_factory,
) -> None:
    target = _binding(product_key=product_key)
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = status_factory(enabled=True, flow=40)
    off = status_factory(enabled=False, flow=40)
    sent: list[bytes] = []
    preinstalled: list[LocalControlJournalRecord] = []

    def _capture_preinstalled_record() -> None:
        record = journal.load("power_qualification_success")
        assert record is not None
        for action in record.actions:
            for digest in (
                action.forward_payload_sha256,
                action.rollback_payload_sha256,
                action.baseline_status_sha256,
                action.expected_readback_sha256,
                action.baseline_readback_sha256,
            ):
                journal.load_blob(digest)
        preinstalled.append(record)

    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([on, on], sent, on_send=_capture_preinstalled_record),
                _Session([on, off], sent),
                _Session([off, on], sent),
            ]
        ),
    )

    result = await producer.execute("power_qualification_success")

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert result.receipt_count == 2
    assert sent[0] == sent[2]
    assert sent[0] != sent[1]
    assert lease.acquired == [(target.physical_identity_key,)]
    assert len(preinstalled) == 1
    initial = preinstalled[0]
    assert tuple(action.restore_on_recovery for action in initial.actions) == (
        True,
        False,
        True,
    )
    assert initial.actions[0].forward_payload_sha256 == initial.actions[1].rollback_payload_sha256
    assert initial.actions[0].forward_payload_sha256 == initial.actions[2].forward_payload_sha256
    assert initial.actions[0].rollback_payload_sha256 == initial.actions[1].forward_payload_sha256
    assert initial.actions[0].rollback_payload_sha256 == initial.actions[2].rollback_payload_sha256
    assert len({action.baseline_status_sha256 for action in initial.actions}) == 1
    record = journal.load("power_qualification_success")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert record.revision == 10
    assert all(action.phase is ActionPhase.VERIFIED for action in record.actions)
    assert all(action.phase is not ActionPhase.NO_CHANGE for action in record.actions)
    binding = _qualification_binding(tmp_path)
    assert binding.product_key == product_key
    assert binding.function == "power"
    receipts = verify_qualification_receipts(
        binding,
        _receipt_root_for_test=tmp_path / "receipts",
    )
    assert len(receipts) == 2
    assert {receipt.request_id for receipt in receipts} == {
        "q_off_"
        + hashlib.sha256(b"power_qualification_success:off").hexdigest()[:32],
        "q_restore_"
        + hashlib.sha256(b"power_qualification_success:restore").hexdigest()[:32],
    }
    assert qualified_physical_identity_keys(
        binding,
        _receipt_root_for_test=tmp_path / "receipts",
    ) == frozenset({target.physical_identity_key})


@pytest.mark.parametrize(
    "status",
    (
        _bar_status(enabled=False),
        _bar_status(timer_enabled=True),
        _bar_status(mode_code=0),
        _bar_status(linkage_code=1),
        _bar_status(flow=46),
    ),
)
@pytest.mark.asyncio
async def test_admission_rejections_stop_before_journal_and_send(
    tmp_path: Path,
    status: bytes,
) -> None:
    target = _binding(product_key=LOCAL_WAVEMAKER.product_key)
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory([_Session([status], sent)]),
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("power_qualification_rejected")

    assert sent == []
    assert journal.load("power_qualification_rejected") is None
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.parametrize(("minimum", "maximum"), ((41, 100), (30, 39)))
@pytest.mark.asyncio
async def test_config_envelope_rejection_stops_before_journal(
    tmp_path: Path,
    minimum: int,
    maximum: int,
) -> None:
    target = _binding()
    epoch = _epoch(target=target, minimum=minimum, maximum=maximum)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory([_Session([_pro_status()], sent)]),
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("power_qualification_config")

    assert sent == []
    assert journal.load("power_qualification_config") is None


@pytest.mark.asyncio
async def test_app_range_rejection_stops_before_journal(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target, minimum=0, maximum=100)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory([_Session([_pro_status(flow=29)], sent)]),
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("power_qualification_app_range")

    assert sent == []
    assert journal.load("power_qualification_app_range") is None


@pytest.mark.asyncio
async def test_epoch_mismatch_stops_before_resolution_and_journal(tmp_path: Path) -> None:
    target = _binding()
    initial = _epoch(target=target)
    changed = _epoch(target=target, marker="7")
    epochs = iter((initial, changed))
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint)
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: next(epochs),
        resolver=resolver,
        writer_factory=_WriterFactory([]),
    )

    with pytest.raises(QualificationProductionError, match="epoch changed"):
        await producer.execute("power_qualification_epoch_preflight")

    assert resolver.calls == 0
    assert journal.load("power_qualification_epoch_preflight") is None


@pytest.mark.asyncio
async def test_missing_firmware_stops_before_writer_and_journal(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address")
    writer_factory = _WriterFactory([])
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=writer_factory,
    )

    with pytest.raises(QualificationProductionError, match="firmware provenance"):
        await producer.execute("power_qualification_no_firmware")

    assert writer_factory.created == 0
    assert journal.load("power_qualification_no_firmware") is None


@pytest.mark.asyncio
async def test_firmware_change_after_same_on_requires_recovery_without_second_send(
    tmp_path: Path,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status()
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(
            endpoint,
            firmware_by_call=("wifi-current", "wifi-changed"),
        ),
        writer_factory=_WriterFactory([_Session([on, on], sent)]),
    )

    with pytest.raises(QualificationProductionError, match="firmware changed"):
        await producer.execute("power_qualification_firmware")

    record = journal.load("power_qualification_firmware")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(action.phase for action in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
        ActionPhase.PREPARED,
    )
    assert len(sent) == 1


@pytest.mark.parametrize(
    "drifted",
    (
        _pro_status(timer_enabled=True),
        _pro_status(mode_code=1),
        _pro_status(linkage_code=1),
        _pro_status(flow=41),
    ),
)
@pytest.mark.asyncio
async def test_phase_drift_stops_forward_and_marks_recovery_required(
    tmp_path: Path,
    drifted: bytes,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status()
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [_Session([on, on], sent), _Session([drifted], sent)]
        ),
    )

    with pytest.raises(QualificationProductionError, match="phase state changed"):
        await producer.execute("power_qualification_drift")

    record = journal.load("power_qualification_drift")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 1
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_post_journal_connect_fault_marks_recovery_required(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status()
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([on, on], sent),
                _Session([RuntimeError("injected connect/read fault")], sent),
            ]
        ),
    )

    with pytest.raises(RuntimeError, match="connect/read fault"):
        await producer.execute("power_qualification_connect")

    record = journal.load("power_qualification_connect")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(action.phase for action in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
        ActionPhase.PREPARED,
    )
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_close_fault_after_first_send_marks_recovery_required(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status()
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session(
                    [on, on],
                    sent,
                    disconnect_error=RuntimeError("injected close fault"),
                )
            ]
        ),
    )

    with pytest.raises(RuntimeError, match="close fault"):
        await producer.execute("power_qualification_close")

    record = journal.load("power_qualification_close")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.VERIFIED
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_epoch_change_after_same_on_marks_recovery_required(tmp_path: Path) -> None:
    target = _binding()
    initial = _epoch(target=target)
    changed = _epoch(target=target, marker="7")
    current = [initial]
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status()
    sent: list[bytes] = []
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: current[0],
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session(
                    [on, on],
                    sent,
                    on_send=lambda: current.__setitem__(0, changed),
                )
            ]
        ),
    )

    with pytest.raises(QualificationProductionError, match="epoch changed"):
        await producer.execute("power_qualification_epoch")

    record = journal.load("power_qualification_epoch")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_wrong_resolved_target_stops_before_journal_and_writer(tmp_path: Path) -> None:
    target = _binding()
    wrong = _binding(marker="b")
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(wrong, "wrong-address", wifi_firmware_version="wifi-current")
    writer_factory = _WriterFactory([])
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=writer_factory,
    )

    with pytest.raises(QualificationProductionError, match="stable binding"):
        await producer.execute("power_qualification_wrong_target")

    assert journal.load("power_qualification_wrong_target") is None
    assert writer_factory.created == 0


@pytest.mark.parametrize("fault_phase", ("same_on", "power_off", "planned_on"))
@pytest.mark.asyncio
async def test_cancel_at_each_send_is_durable_and_never_retried(
    tmp_path: Path,
    fault_phase: str,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status(enabled=True)
    off = _pro_status(enabled=False)
    sent: list[bytes] = []
    fault = asyncio.CancelledError()
    sessions = {
        "same_on": [_Session([on], sent, send_error=fault)],
        "power_off": [
            _Session([on, on], sent),
            _Session([on], sent, send_error=fault),
        ],
        "planned_on": [
            _Session([on, on], sent),
            _Session([on, off], sent),
            _Session([off], sent, send_error=fault),
        ],
    }[fault_phase]
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(sessions),
    )

    with pytest.raises(asyncio.CancelledError):
        await producer.execute(f"power_cancel_{fault_phase}")

    record = journal.load(f"power_cancel_{fault_phase}")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert sum(action.phase is ActionPhase.UNCERTAIN for action in record.actions) == 1
    assert len(sent) == {"same_on": 1, "power_off": 2, "planned_on": 3}[fault_phase]
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.parametrize("fault_phase", ("same_on", "power_off", "planned_on"))
@pytest.mark.asyncio
async def test_readback_mismatch_at_each_phase_is_durable_without_receipts(
    tmp_path: Path,
    fault_phase: str,
) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status(enabled=True)
    off = _pro_status(enabled=False)
    sent: list[bytes] = []
    sessions = {
        "same_on": [_Session([on, off], sent)],
        "power_off": [
            _Session([on, on], sent),
            _Session([on, on], sent),
        ],
        "planned_on": [
            _Session([on, on], sent),
            _Session([on, off], sent),
            _Session([off, off], sent),
        ],
    }[fault_phase]
    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(sessions),
    )

    with pytest.raises(QualificationProductionError, match="readback did not match"):
        await producer.execute(f"power_readback_{fault_phase}")

    record = journal.load(f"power_readback_{fault_phase}")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert sum(action.phase is ActionPhase.UNCERTAIN for action in record.actions) == 1
    assert len(sent) == {"same_on": 1, "power_off": 2, "planned_on": 3}[fault_phase]
    assert not tuple((tmp_path / "receipts").iterdir())


async def _make_pending_operation(
    tmp_path: Path,
    *,
    operation_id: str,
    fault_phase: str,
    monkeypatch=None,
) -> tuple[
    LocalControlJournalStore,
    _Lease,
    ResolvedEndpoint,
    list[bytes],
]:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status(enabled=True)
    off = _pro_status(enabled=False)
    sent: list[bytes] = []
    if fault_phase == "same_on":
        sessions = [_Session([on], sent, send_error=TimeoutError("same ON fault"))]
    elif fault_phase == "power_off":
        sessions = [
            _Session([on, on], sent),
            _Session([on], sent, send_error=TimeoutError("OFF fault")),
        ]
    elif fault_phase == "planned_on":
        sessions = [
            _Session([on, on], sent),
            _Session([on, off], sent),
            _Session([off], sent, send_error=TimeoutError("planned ON fault")),
        ]
    elif fault_phase == "terminal_save":
        sessions = [
            _Session([on, on], sent),
            _Session([on, off], sent),
            _Session([off, on], sent),
        ]
    else:
        raise AssertionError("unknown test fault phase")
    producer, journal, lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(sessions),
    )
    if fault_phase == "terminal_save":
        assert monkeypatch is not None
        original = producer._save_transaction

        def _fail_terminal_save(record, phase):
            if phase is TransactionPhase.VERIFIED:
                raise RuntimeError("injected terminal save fault")
            return original(record, phase)

        monkeypatch.setattr(producer, "_save_transaction", _fail_terminal_save)

    with pytest.raises((TimeoutError, RuntimeError)):
        await producer.execute(operation_id)
    pending = journal.load(operation_id)
    assert pending is not None
    assert pending.phase is TransactionPhase.RECOVERY_REQUIRED
    assert not tuple((tmp_path / "receipts").iterdir())
    return journal, lease, endpoint, sent


@pytest.mark.parametrize(
    ("fault_phase", "current_enabled", "expected_recovery_writes"),
    (
        ("same_on", True, 1),
        ("same_on", False, 0),
        ("power_off", True, 1),
        ("power_off", False, 0),
        ("planned_on", True, 1),
        ("planned_on", False, 0),
        ("terminal_save", True, 1),
    ),
)
@pytest.mark.asyncio
async def test_every_nonterminal_fault_recovers_without_an_on_frame(
    tmp_path: Path,
    monkeypatch,
    fault_phase: str,
    current_enabled: bool,
    expected_recovery_writes: int,
) -> None:
    operation_id = f"power_fault_{fault_phase}_{int(current_enabled)}"
    journal, lease, endpoint, forward_sent = await _make_pending_operation(
        tmp_path,
        operation_id=operation_id,
        fault_phase=fault_phase,
        monkeypatch=monkeypatch,
    )
    pending = journal.load(operation_id)
    assert pending is not None
    off_payload = journal.load_blob(pending.actions[1].forward_payload_sha256)
    current = _pro_status(enabled=current_enabled)
    off = _pro_status(enabled=False)
    recovery_sent: list[bytes] = []
    if not current_enabled:
        active_actions = sum(
            action.phase is not ActionPhase.PREPARED for action in pending.actions
        )
        sessions = [_Session([off], recovery_sent) for _ in range(active_actions)]
    elif fault_phase == "same_on":
        sessions = [_Session([current, off], recovery_sent)]
    elif fault_phase == "power_off":
        sessions = [
            _Session([current], recovery_sent),
            _Session([current, off], recovery_sent),
        ]
    else:
        sessions = [
            _Session([current, off], recovery_sent),
            _Session([off], recovery_sent),
            _Session([off], recovery_sent),
        ]
    coordinator = _coordinator(journal, lease, endpoint, sessions)

    recovered = await coordinator.recover(operation_id)

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovered.physical_write_count == expected_recovery_writes
    assert recovery_sent == [off_payload] * expected_recovery_writes
    assert all(payload == off_payload for payload in recovery_sent)
    recovered_record = journal.load(operation_id)
    assert recovered_record is not None
    if fault_phase == "power_off":
        assert recovered_record.actions[1].diagnostic == (
            "baseline_already_present" if current_enabled else "safe_state_retained"
        )
    assert len(forward_sent) in {1, 2, 3}
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_same_on_recovery_read_failure_preserves_prior_action_state(tmp_path: Path) -> None:
    operation_id = "power_same_read_failure"
    journal, lease, endpoint, _sent = await _make_pending_operation(
        tmp_path,
        operation_id=operation_id,
        fault_phase="same_on",
    )
    before = journal.load(operation_id)
    assert before is not None
    recovery_sent: list[bytes] = []
    coordinator = _coordinator(
        journal,
        lease,
        endpoint,
        [_Session([RuntimeError("recovery read failed")], recovery_sent)],
    )

    with pytest.raises(RuntimeError, match="recovery read failed"):
        await coordinator.recover(operation_id)

    after = journal.load(operation_id)
    assert after is not None
    assert after.phase is TransactionPhase.RECOVERY_REQUIRED
    assert after.actions[0] == before.actions[0]
    assert after.actions[0].phase is ActionPhase.UNCERTAIN
    assert after.revision == before.revision + 2
    assert recovery_sent == []


@pytest.mark.asyncio
async def test_planned_on_recovery_read_failure_makes_no_new_journal_save(
    tmp_path: Path,
) -> None:
    operation_id = "power_planned_read_failure"
    journal, lease, endpoint, _sent = await _make_pending_operation(
        tmp_path,
        operation_id=operation_id,
        fault_phase="planned_on",
    )
    before = journal.load(operation_id)
    assert before is not None
    recovery_sent: list[bytes] = []
    coordinator = _coordinator(
        journal,
        lease,
        endpoint,
        [_Session([RuntimeError("planned recovery read failed")], recovery_sent)],
    )

    with pytest.raises(RuntimeError, match="planned recovery read failed"):
        await coordinator.recover(operation_id)

    after = journal.load(operation_id)
    assert after == before
    assert after is not None
    assert after.phase is TransactionPhase.RECOVERY_REQUIRED
    assert after.actions[2].phase is ActionPhase.UNCERTAIN
    assert recovery_sent == []


@pytest.mark.asyncio
async def test_power_off_recovery_read_failure_is_durably_uncertain(tmp_path: Path) -> None:
    operation_id = "power_off_read_failure"
    journal, lease, endpoint, _sent = await _make_pending_operation(
        tmp_path,
        operation_id=operation_id,
        fault_phase="power_off",
    )
    before = journal.load(operation_id)
    assert before is not None
    recovery_sent: list[bytes] = []
    coordinator = _coordinator(
        journal,
        lease,
        endpoint,
        [_Session([RuntimeError("OFF read failed")], recovery_sent)],
    )

    with pytest.raises(LocalControlExecutionError, match="no frame was sent"):
        await coordinator.recover(operation_id)

    after = journal.load(operation_id)
    assert after is not None
    assert after.phase is TransactionPhase.RECOVERY_REQUIRED
    assert after.actions[1].phase is ActionPhase.UNCERTAIN
    assert after.actions[1].diagnostic == "safe_state_readback_unavailable"
    assert after.revision == before.revision + 2
    assert recovery_sent == []


@pytest.mark.asyncio
async def test_terminal_finalization_retry_is_offline_and_write_free(tmp_path: Path) -> None:
    target = _binding()
    epoch = _epoch(target=target)
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    on = _pro_status(enabled=True)
    off = _pro_status(enabled=False)
    sent: list[bytes] = []
    delegate = _artifacts(tmp_path)

    class _FailAfterFinalize:
        def finalize(self, receipts, binding):
            delegate.finalize(receipts, binding)
            raise QualificationProductionError("injected post-finalization failure")

    producer, journal, _lease = _producer(
        tmp_path,
        epoch_provider=lambda: epoch,
        resolver=_Resolver(endpoint),
        writer_factory=_WriterFactory(
            [
                _Session([on, on], sent),
                _Session([on, off], sent),
                _Session([off, on], sent),
            ]
        ),
        artifacts=_FailAfterFinalize(),
    )

    with pytest.raises(QualificationProductionError, match="post-finalization"):
        await producer.execute("power_finalization_retry")

    terminal = journal.load("power_finalization_retry")
    assert terminal is not None and terminal.phase is TransactionPhase.VERIFIED

    def _offline_only():
        raise AssertionError("terminal finalization touched current inputs or network")

    retry = PowerQualificationProducer(
        epoch_provider=_offline_only,
        resolver_factory=_offline_only,
        journal=journal,
        lease=_Lease(),
        artifacts=delegate,
        writer_factory=_offline_only,
        clock=lambda: NOW,
    )

    result = await retry.execute("power_finalization_retry")

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 0
    assert result.receipt_count == 2
    assert len(sent) == 3


def test_power_producer_never_uses_the_coordinator_no_change_executor() -> None:
    source = Path(
        "src/jebao_flow/local_control/power_qualification_producer.py"
    ).read_text(encoding="utf-8")

    assert "_execute_prepared" not in source
    assert "self._execute_action(" in source
    assert "self._create_record(" not in source
