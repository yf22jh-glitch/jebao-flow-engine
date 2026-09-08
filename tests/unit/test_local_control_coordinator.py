from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import (
    LocalControlCoordinator,
    LocalControlExecutionError,
)
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
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode, SlaveFlowOverride
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET


def _binding(member: str, identity: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=identity * 64,
        logical_role=member,
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="0" * 64,
        template_sha256="1" * 64,
        schema_sha256=current_protocol_schema_sha256(),
        firmware_version="wifi-current",
        receipt_sha256=("2" * 64, "3" * 64),
    )


def _status(*, flow: int = 40, schedule: bytes | None = None) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[2] = flow
    if schedule is not None:
        start = LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
        raw[start : start + len(schedule)] = schedule
    return bytes(raw)


class _Resolver:
    def __init__(self, endpoints: tuple[ResolvedEndpoint, ...]) -> None:
        self.endpoints = endpoints
        self.calls = 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        return next(endpoint for endpoint in self.endpoints if endpoint.target == target)

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        self.calls += 1
        return tuple(
            next(endpoint for endpoint in self.endpoints if endpoint.target == target)
            for target in targets
        )


class _Session:
    def __init__(
        self,
        statuses: list[bytes],
        *,
        acknowledgement: bytes = b"ack",
        send_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.acknowledgement = acknowledgement
        self.send_error = send_error
        self.sent: list[bytes] = []
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
        pass

    def quarantine(self) -> None:
        self.quarantined = True


class _SessionFactory:
    def __init__(self, sessions: dict[str, list[_Session]]) -> None:
        self.sessions = sessions

    def __call__(self, address: str) -> _Session:
        return self.sessions[address].pop(0)


def _lease(tmp_path: Path) -> LocalControlLease:
    return LocalControlLease(
        guard=ExactRestoreGuard._for_test(
            operation_lock_path=tmp_path / "hardware-operation.lock",
            latch_path=tmp_path / "emergency-stop.latch",
        ),
        device_lock_directory=tmp_path / "physical-locks",
        legacy_workflows_idle_probe=lambda: True,
    )


def _coordinator(
    tmp_path: Path,
    *,
    qualifications: tuple[QualificationBinding, ...],
    resolver: _Resolver,
    sessions: _SessionFactory,
    catalog_qualified_keys: frozenset[str] | None = None,
    runtime_qualified_keys: frozenset[str] | None = None,
) -> tuple[LocalControlCoordinator, LocalControlJournalStore]:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    default_keys = frozenset(
        endpoint.target.physical_identity_key for endpoint in resolver.endpoints
    )
    catalog_keys = default_keys if catalog_qualified_keys is None else catalog_qualified_keys
    runtime_keys = default_keys if runtime_qualified_keys is None else runtime_qualified_keys
    return (
        LocalControlCoordinator(
            catalog=build_local_control_catalog(
                qualifications,
                current_content=InstallContentBindings(
                    app_catalog_sha256="0" * 64,
                    protocol_schema_sha256=current_protocol_schema_sha256(),
                    template_sha256=("1" * 64,),
                ),
                _qualification_verifier_for_test=lambda _item: catalog_keys,
            ),
            resolver=resolver,  # type: ignore[arg-type]
            journal=journal,
            lease=_lease(tmp_path),
            session_factory=sessions,
            qualification_verifier=lambda _item: runtime_keys,
        ),
        journal,
    )


@pytest.mark.asyncio
async def test_scalar_transaction_is_journal_first_verified_and_idempotent(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    session = _Session([_status(flow=40), _status(flow=50)])
    resolver = _Resolver((endpoint,))
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=resolver,
        sessions=_SessionFactory({"current-address": [session]}),
    )
    intent = LocalControlIntent(
        request_id="request_0001",
        target=target,
        function="manual_flow",
        value=50,
    )

    first = await coordinator.execute(intent)
    duplicate = await coordinator.execute(intent)

    assert first.phase is TransactionPhase.VERIFIED
    assert first.physical_write_count == 1
    assert duplicate.phase is TransactionPhase.VERIFIED
    assert duplicate.physical_write_count == 0
    assert resolver.calls == 1
    assert len(session.sent) == 1
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.actions[0].phase is ActionPhase.VERIFIED


@pytest.mark.asyncio
async def test_schedule_reset_is_one_journaled_full_image_action(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    baseline = bytes(range(9)) + bytes([0xEE]) * (432 - 9)
    reset = bytes([0xEE]) * 432
    session = _Session([_status(schedule=baseline), _status(schedule=reset)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("schedule_reset"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [session]}),
    )
    intent = LocalControlIntent(
        request_id="schedule_reset_0001",
        target=target,
        function="schedule_reset",
    )

    result = await coordinator.execute(intent)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 1
    assert len(session.sent) == 1
    record = journal.load(intent.request_id)
    assert record is not None
    assert len(record.actions) == 1
    assert record.actions[0].function == "schedule_reset"
    assert record.actions[0].phase is ActionPhase.VERIFIED


@pytest.mark.asyncio
async def test_uncertain_forward_is_never_retried_and_can_restore_fresh_baseline(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    failed = _Session([_status(flow=40)], send_error=TimeoutError("ack lost"))
    recovery = _Session([_status(flow=50), _status(flow=40)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [failed, recovery]}),
    )
    intent = LocalControlIntent(
        request_id="request_0002",
        target=target,
        function="manual_flow",
        value=50,
    )

    with pytest.raises(LocalControlExecutionError, match="will not be retried"):
        await coordinator.execute(intent)
    uncertain = journal.load(intent.request_id)
    assert uncertain is not None
    assert uncertain.phase is TransactionPhase.RECOVERY_REQUIRED
    assert uncertain.actions[0].phase is ActionPhase.UNCERTAIN
    assert len(failed.sent) == 1

    restored = await coordinator.recover(intent.request_id)

    assert restored.phase is TransactionPhase.ROLLED_BACK
    assert restored.physical_write_count == 1
    assert len(failed.sent) == 1
    assert len(recovery.sent) == 1


@pytest.mark.asyncio
async def test_priority_schedule_reads_current_master_then_writes_each_slave_image(
    tmp_path: Path,
) -> None:
    master = _binding("master", "a")
    slave = _binding("slave", "b")
    master_endpoint = ResolvedEndpoint(
        master,
        "master-current",
        wifi_firmware_version="wifi-current",
    )
    slave_endpoint = ResolvedEndpoint(
        slave,
        "slave-current",
        wifi_firmware_version="wifi-current",
    )
    master_slot = bytes((0, 0, 1, 0, 2, 40, 20, 0, 10))
    master_image = master_slot + bytes([0xEE]) * (432 - 9)
    desired_slot = bytes((0, 0, 1, 0, 2, 55, 20, 0, 10))
    desired_image = desired_slot + bytes([0xEE]) * (432 - 9)
    baseline_image = bytes([0xEE]) * 432
    master_session = _Session([_status(schedule=master_image)])
    slave_session = _Session(
        [_status(schedule=baseline_image), _status(schedule=desired_image)]
    )
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("slave_per_slot_flow_extension"),),
        resolver=_Resolver((master_endpoint, slave_endpoint)),
        sessions=_SessionFactory(
            {
                "master-current": [master_session],
                "slave-current": [slave_session],
            }
        ),
        catalog_qualified_keys=frozenset({"b" * 64}),
        runtime_qualified_keys=frozenset({"b" * 64}),
    )
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slaves=(
            FreshSlaveSchedule(
                target=slave,
                flow_overrides=(SlaveFlowOverride(slot_index=0, flow=55),),
            ),
        ),
    )

    result = await coordinator.execute_schedule("schedule_0001", plan)

    assert result.phase is TransactionPhase.VERIFIED
    assert len(slave_session.sent) == 1
    assert master_session.sent == []
    record = journal.load("schedule_0001")
    assert record is not None
    assert record.actions[0].function == "slave_per_slot_flow_extension"
    assert record.actions[0].target.physical_identity_key == "b" * 64


@pytest.mark.asyncio
async def test_single_slot_flow_command_rederives_other_values_from_current_devices(
    tmp_path: Path,
) -> None:
    master = _binding("master", "a")
    slave = _binding("slave", "b")
    master_endpoint = ResolvedEndpoint(
        master,
        "master-current",
        wifi_firmware_version="wifi-current",
    )
    slave_endpoint = ResolvedEndpoint(
        slave,
        "slave-current",
        wifi_firmware_version="wifi-current",
    )
    master_image = (
        bytes((0, 0, 1, 0, 2, 40, 20, 0, 10))
        + bytes((1, 0, 2, 0, 2, 45, 20, 0, 10))
        + bytes([0xEE]) * (432 - 18)
    )
    slave_image = (
        bytes((0, 0, 1, 0, 2, 50, 9, 9, 9))
        + bytes((1, 0, 2, 0, 2, 60, 9, 9, 9))
        + bytes([0xEE]) * (432 - 18)
    )
    desired = (
        bytes((0, 0, 1, 0, 2, 50, 20, 0, 10))
        + bytes((1, 0, 2, 0, 2, 55, 20, 0, 10))
        + bytes([0xEE]) * (432 - 18)
    )
    master_session = _Session([_status(schedule=master_image)])
    slave_session = _Session(
        [_status(schedule=slave_image), _status(schedule=desired)]
    )
    coordinator, _journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("slave_per_slot_flow_extension"),),
        resolver=_Resolver((master_endpoint, slave_endpoint)),
        sessions=_SessionFactory(
            {
                "master-current": [master_session],
                "slave-current": [slave_session],
            }
        ),
        catalog_qualified_keys=frozenset({"b" * 64}),
        runtime_qualified_keys=frozenset({"b" * 64}),
    )

    result = await coordinator.execute_schedule_flow(
        request_id="slot_flow_0001",
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slave=slave,
        slot_index=1,
        flow=55,
    )

    assert result.accepted is True
    assert master_session.sent == []
    assert len(slave_session.sent) == 1


@pytest.mark.asyncio
async def test_receipts_for_one_physical_device_never_authorize_another(
    tmp_path: Path,
) -> None:
    qualified_target = _binding("qualified", "a")
    other_target = _binding("other", "b")
    other_endpoint = ResolvedEndpoint(
        other_target,
        "other-current",
        wifi_firmware_version="wifi-current",
    )
    resolver = _Resolver((other_endpoint,))
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=resolver,
        sessions=_SessionFactory({"other-current": []}),
        catalog_qualified_keys=frozenset({other_target.physical_identity_key}),
        runtime_qualified_keys=frozenset({qualified_target.physical_identity_key}),
    )
    intent = LocalControlIntent(
        request_id="wrong_device_0001",
        target=other_target,
        function="manual_flow",
        value=50,
    )

    with pytest.raises(ValueError, match="physical device"):
        await coordinator.execute(intent)

    assert resolver.calls == 1
    assert journal.load(intent.request_id) is None
