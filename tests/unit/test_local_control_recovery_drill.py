from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, PreparedDeviceWrite
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.models import (
    InstallContentBindings,
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.recovery_drill import (
    ActualRecoveryForwardDrill,
    DesignatedForwardReadbackFault,
    LocalControlRecoveryDrillError,
    RecoveryReadbackFaultWriter,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _binding(identity: str = "a") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="qualified_wavemaker",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=identity * 64,
        logical_role="qualified_wavemaker",
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 11, tzinfo=UTC),
    )


def _qualification() -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function="manual_flow",
        app_version="3.4.18",
        app_catalog_sha256="0" * 64,
        template_sha256="1" * 64,
        schema_sha256=current_protocol_schema_sha256(),
        firmware_version="wifi-current",
        receipt_sha256=("2" * 64, "3" * 64),
    )


def _catalog(target: LocalDeviceBinding):
    return build_local_control_catalog(
        (_qualification(),),
        current_content=InstallContentBindings(
            app_catalog_sha256="0" * 64,
            protocol_schema_sha256=current_protocol_schema_sha256(),
            template_sha256=("1" * 64,),
        ),
        _qualification_verifier_for_test=lambda _binding: frozenset({target.physical_identity_key}),
    )


def _status(flow: int) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = 1
    raw[2] = flow
    return bytes(raw)


class _Resolver:
    def __init__(self, endpoint: ResolvedEndpoint) -> None:
        self.endpoint = endpoint
        self.calls = 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        if target != self.endpoint.target:
            raise ValueError("target mismatch")
        return self.endpoint

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        self.calls += 1
        if targets != (self.endpoint.target,):
            raise ValueError("target mismatch")
        return (self.endpoint,)


class _Session:
    def __init__(
        self,
        statuses: list[bytes],
        *,
        acknowledgement: bytes = b"",
        send_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.acknowledgement = acknowledgement
        self.send_error = send_error
        self.close_error = close_error
        self.sent: list[bytes] = []
        self.disconnect_calls = 0
        self.quarantined = False

    async def connect(self) -> None:
        pass

    async def authenticate(self) -> bytes:
        return b"private"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
        return self.statuses.pop(0)

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.sent.append(bytes(control_payload))
        if self.send_error is not None:
            raise self.send_error
        return self.acknowledgement

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.close_error is not None:
            raise self.close_error

    def quarantine(self) -> None:
        self.quarantined = True


class _SessionFactory:
    def __init__(self, sessions: list[_Session]) -> None:
        self.sessions = sessions

    def __call__(self, _address: str) -> _Session:
        return self.sessions.pop(0)


def _lease(tmp_path: Path) -> LocalControlLease:
    return LocalControlLease(
        guard=ExactRestoreGuard._for_test(
            operation_lock_path=tmp_path / "hardware-operation.lock",
            latch_path=tmp_path / "emergency-stop.latch",
        ),
        device_lock_directory=tmp_path / "physical-locks",
        legacy_workflows_idle_probe=lambda: True,
    )


def _drill(
    tmp_path: Path,
    target: LocalDeviceBinding,
    endpoint: ResolvedEndpoint,
    sessions: list[_Session],
    *,
    journal: LocalControlJournalStore | None = None,
) -> tuple[ActualRecoveryForwardDrill, LocalControlJournalStore, _Resolver]:
    selected_journal = journal or LocalControlJournalStore.for_test(tmp_path / "journal")
    resolver = _Resolver(endpoint)
    session_factory = _SessionFactory(sessions)
    return (
        ActualRecoveryForwardDrill(
            catalog=_catalog(target),
            resolver=resolver,  # type: ignore[arg-type]
            journal=selected_journal,
            lease=_lease(tmp_path),
            writer_factory=lambda current: ConnectedDeviceWriter(
                current,
                session_factory=session_factory,
            ),
            qualification_verifier=lambda _binding: frozenset({target.physical_identity_key}),
        ),
        selected_journal,
        resolver,
    )


@pytest.mark.asyncio
async def test_designated_fault_leaves_exact_recoverable_journal_then_production_recovers(
    tmp_path: Path,
) -> None:
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    forward = _Session([_status(40), _status(39)])
    drill, journal, _resolver = _drill(tmp_path, target, endpoint, [forward])

    planned = await drill.execute("recovery_drill_0001", target)

    assert planned.phase is TransactionPhase.RECOVERY_REQUIRED
    assert planned.action_phase is ActionPhase.UNCERTAIN
    assert planned.diagnostic == "forward_readback_unavailable"
    assert planned.physical_write_count == 1
    assert planned.created_receipt_count == 0
    assert len(forward.sent) == 1
    uncertain = journal.load(planned.operation_id)
    assert uncertain is not None
    assert uncertain.revision == 3
    assert uncertain.actions[0].acknowledgement_sha256 is not None

    recovery = _Session([_status(39), _status(40)])
    recovery_coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=_lease(tmp_path),
        session_factory=_SessionFactory([recovery]),
    )
    result = await recovery_coordinator.recover(planned.operation_id)

    assert result.phase is TransactionPhase.ROLLED_BACK
    assert result.physical_write_count == 1
    assert len(recovery.sent) == 1
    assert journal.unfinished_records() == ()
    terminal = journal.load(planned.operation_id)
    assert terminal is not None
    assert terminal.actions[0].phase is ActionPhase.ROLLED_BACK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("readback_flow", "send_error", "diagnostic"),
    (
        (38, None, "forward_readback_mismatch"),
        (39, TimeoutError("transport failed"), "forward_outcome_uncertain"),
    ),
)
async def test_non_designated_failure_never_reports_planned_completion(
    tmp_path: Path,
    readback_flow: int,
    send_error: BaseException | None,
    diagnostic: str,
) -> None:
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    statuses = [_status(40)] if send_error is not None else [_status(40), _status(readback_flow)]
    session = _Session(statuses, send_error=send_error)
    drill, journal, _resolver = _drill(tmp_path, target, endpoint, [session])

    with pytest.raises(LocalControlRecoveryDrillError, match="unplanned reason"):
        await drill.execute("recovery_drill_0002", target)

    record = journal.load("recovery_drill_0002")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert record.actions[0].diagnostic == diagnostic
    assert len(session.sent) == 1


@pytest.mark.asyncio
async def test_wrong_baseline_and_existing_unfinished_record_stop_before_another_send(
    tmp_path: Path,
) -> None:
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    wrong_baseline = _Session([_status(41)])
    drill, journal, _resolver = _drill(tmp_path, target, endpoint, [wrong_baseline])

    with pytest.raises(LocalControlRecoveryDrillError, match="not exact 40-to-39"):
        await drill.execute("recovery_drill_0003", target)

    assert wrong_baseline.sent == []
    assert journal.load("recovery_drill_0003") is None

    forward = _Session([_status(40), _status(39)])
    first, _journal, _resolver = _drill(
        tmp_path,
        target,
        endpoint,
        [forward],
        journal=journal,
    )
    await first.execute("recovery_drill_0004", target)
    unused = _Session([_status(40), _status(39)])
    second, _journal, _resolver = _drill(
        tmp_path,
        target,
        endpoint,
        [unused],
        journal=journal,
    )

    with pytest.raises(LocalControlRecoveryDrillError, match="unplanned reason"):
        await second.execute("recovery_drill_0005", target)

    assert unused.sent == []
    assert journal.load("recovery_drill_0005") is None


@pytest.mark.asyncio
async def test_unqualified_target_stops_before_resolve_or_writer(tmp_path: Path) -> None:
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint)
    created: list[ResolvedEndpoint] = []
    drill = ActualRecoveryForwardDrill(
        catalog=build_local_control_catalog(),
        resolver=resolver,  # type: ignore[arg-type]
        journal=LocalControlJournalStore.for_test(tmp_path / "journal"),
        lease=_lease(tmp_path),
        writer_factory=lambda current: created.append(current),  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(LocalControlRecoveryDrillError, match="lacks exact qualified"):
        await drill.execute("recovery_drill_0006", target)

    assert resolver.calls == 0
    assert created == []


class _PreparedDelegate:
    def __init__(
        self,
        prepared: PreparedDeviceWrite,
        readbacks: list[bytes],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.prepared = prepared
        self.readbacks = readbacks
        self.close_error = close_error
        self.close_calls = 0

    async def connect_and_read(self) -> bytes:
        return _status(40)

    def prepare_scalar(
        self,
        _intent: LocalControlIntent,
        _current_status: bytes,
    ) -> PreparedDeviceWrite:
        return self.prepared

    async def send_once(self, _payload: bytes, _lease) -> bytes:
        return b""

    async def readback(self, _lease) -> bytes:
        return self.readbacks.pop(0)

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.mark.asyncio
async def test_adapter_faults_once_only_after_production_predicate_and_close_propagates() -> None:
    target = _binding()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    intent = LocalControlIntent(
        request_id="recovery_drill_0007",
        target=target,
        function="manual_flow",
        value=39,
    )
    prepared = ConnectedDeviceWriter(endpoint).prepare_scalar(intent, _status(40))
    delegate = _PreparedDelegate(
        prepared,
        [_status(39), _status(39)],
        close_error=RuntimeError("close failed"),
    )
    adapter = RecoveryReadbackFaultWriter(delegate)
    assert adapter.prepare_scalar(intent, _status(40)) is prepared

    with pytest.raises(DesignatedForwardReadbackFault):
        await adapter.readback(None)  # type: ignore[arg-type]
    assert await adapter.readback(None) == _status(39)  # type: ignore[arg-type]
    assert adapter.injected_fault_count == 1

    with pytest.raises(RuntimeError, match="close failed"):
        await adapter.close()
    assert delegate.close_calls == 1
    with pytest.raises(LocalControlRecoveryDrillError, match="closed twice"):
        await adapter.close()
    assert delegate.close_calls == 1
