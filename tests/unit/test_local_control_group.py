from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.groups.models import GroupState, PatternKind
from jebao_flow.groups.plan import (
    DeviceActuationLimits,
    GroupTickPlan,
    MemberAction,
    MemberDecision,
    PatternEpoch,
)
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import (
    LocalControlCoordinator,
    LocalControlExecutionError,
)
from jebao_flow.local_control.group import (
    GroupActuationOutcome,
    SoftwareIndependentGroupActuator,
)
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import (
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.models import DeviceTarget
from jebao_flow.protocol.profiles import AQUARIUM_PUMP, LOCAL_WAVEMAKER_PRO

LIMITS = DeviceActuationLimits(min_power=30, max_power=75, power_step=1)


def _binding(
    member: str,
    identity: str,
    *,
    product_key: str = LOCAL_WAVEMAKER_PRO.product_key,
) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=product_key,
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


def _status(*, enabled: bool, flow: int) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = int(enabled)
    raw[2] = flow
    return bytes(raw)


def _decision(
    member: str,
    target: DeviceTarget,
    *,
    action: MemberAction = MemberAction.DISPATCH,
    control_epoch: int = 0,
) -> MemberDecision:
    return MemberDecision(
        device=member,
        action=action,
        requested_target=target,
        target=target if action is MemberAction.DISPATCH else None,
        requested_power=target.power,
        normalized_power=target.power if action is MemberAction.DISPATCH else None,
        limits_used=LIMITS,
        effective_max_power=LIMITS.max_power,
        control_epoch=control_epoch,
    )


def _plan(
    decisions: tuple[MemberDecision, ...],
    *,
    generation: int = 1,
    state: GroupState = GroupState.RUNNING,
    failure_policy_stopped: bool = False,
) -> GroupTickPlan:
    return GroupTickPlan(
        group_id="main_flow",
        generation=generation,
        timestamp=float(generation),
        epoch=PatternEpoch(
            generation=1,
            started_at=0.0,
            pattern=PatternKind.CONSTANT,
            period_seconds=10.0,
        ),
        decisions=decisions,
        derived_status=state,
        offline_devices=(),
        unknown_devices=(),
        manual_override_devices=(),
        failure_policy_stopped=failure_policy_stopped,
    )


class _Session:
    def __init__(
        self,
        statuses: list[bytes],
        *,
        send_errors: list[BaseException | None] | None = None,
    ) -> None:
        self.statuses = statuses
        self.send_errors = [] if send_errors is None else send_errors
        self.sent: list[bytes] = []
        self.quarantined = False
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def authenticate(self) -> bytes:
        return b"private"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
        return self.statuses.pop(0)

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.sent.append(bytes(control_payload))
        error = self.send_errors.pop(0) if self.send_errors else None
        if error is not None:
            raise error
        return b"ack"

    async def disconnect(self) -> None:
        self.connected = False

    def quarantine(self) -> None:
        self.quarantined = True


class _SessionFactory:
    def __init__(self, sessions: dict[str, list[_Session]]) -> None:
        self.sessions = sessions
        self.created: list[str] = []

    def __call__(self, address: str) -> _Session:
        self.created.append(address)
        return self.sessions[address].pop(0)


class _Resolver:
    def __init__(self, endpoints: tuple[ResolvedEndpoint, ...]) -> None:
        self.endpoints = endpoints
        self.calls = 0
        self.requested_many: list[tuple[LocalDeviceBinding, ...]] = []

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        return next(endpoint for endpoint in self.endpoints if endpoint.target == target)

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        self.calls += 1
        self.requested_many.append(targets)
        return self.endpoints


class _ActiveLease:
    def __init__(self) -> None:
        self.checks = 0

    def require_permitted(self) -> None:
        self.checks += 1


class _RecordingLease:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.active = _ActiveLease()

    @contextmanager
    def acquire(self, keys: tuple[str, ...]) -> Iterator[_ActiveLease]:
        self.calls.append(keys)
        yield self.active


def _coordinator(
    tmp_path: Path,
    *,
    endpoints: tuple[ResolvedEndpoint, ...],
    sessions: _SessionFactory,
    qualified_keys: frozenset[str],
    functions: tuple[str, ...] = ("manual_flow", "power"),
) -> tuple[LocalControlCoordinator, LocalControlJournalStore, _Resolver, _RecordingLease]:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    resolver = _Resolver(endpoints)
    lease = _RecordingLease()
    catalog = build_local_control_catalog(
        tuple(_qualification(function) for function in functions),
        current_content=InstallContentBindings(
            app_catalog_sha256="0" * 64,
            protocol_schema_sha256=current_protocol_schema_sha256(),
            template_sha256=("1" * 64,),
        ),
        _qualification_verifier_for_test=lambda _item: qualified_keys,
    )
    coordinator = LocalControlCoordinator(
        catalog=catalog,
        resolver=resolver,  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        session_factory=sessions,
        qualification_verifier=lambda _item: qualified_keys,
    )
    return coordinator, journal, resolver, lease


def _actuator(
    coordinator: LocalControlCoordinator,
    left: LocalDeviceBinding,
    right: LocalDeviceBinding,
) -> SoftwareIndependentGroupActuator:
    return SoftwareIndependentGroupActuator(
        group_id="main_flow",
        member_ids=("left", "right"),
        bindings={"right": right, "left": left},
        device_limits={"right": LIMITS, "left": LIMITS},
        coordinator=coordinator,
    )


@pytest.mark.asyncio
async def test_group_plan_uses_one_lease_one_journal_and_single_field_frames(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    left_session = _Session(
        [
            _status(enabled=False, flow=40),
            _status(enabled=False, flow=50),
            _status(enabled=True, flow=50),
        ]
    )
    right_session = _Session(
        [
            _status(enabled=False, flow=45),
            _status(enabled=False, flow=55),
            _status(enabled=True, flow=55),
        ]
    )
    sessions = _SessionFactory({"left-current": [left_session], "right-current": [right_session]})
    # Return discovery results in the opposite order to prove key-based association.
    endpoints = (
        ResolvedEndpoint(right, "right-current", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(left, "left-current", wifi_firmware_version="wifi-current"),
    )
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key, right.physical_identity_key}),
    )
    actuator = _actuator(coordinator, left, right)

    result = await actuator.execute(
        request_id="group_request_0001",
        plan=_plan(
            (
                _decision("right", DeviceTarget(enabled=True, power=55)),
                _decision("left", DeviceTarget(enabled=True, power=50)),
            )
        ),
    )

    schema = LOCAL_WAVEMAKER_PRO
    assert result.outcome is GroupActuationOutcome.VERIFIED
    assert result.accepted is True
    assert result.transaction is not None
    assert result.transaction.physical_write_count == 4
    assert resolver.calls == 1
    assert lease.calls == [(left.physical_identity_key, right.physical_identity_key)]
    assert left_session.sent == [
        build_control_payload(schema, {"Flow": 50}),
        build_control_payload(schema, {"SwitchON": True}),
    ]
    assert right_session.sent == [
        build_control_payload(schema, {"Flow": 55}),
        build_control_payload(schema, {"SwitchON": True}),
    ]
    record = journal.load("group_request_0001")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert [action.function for action in record.actions] == [
        "manual_flow",
        "power",
        "manual_flow",
        "power",
    ]
    assert all(action.phase is ActionPhase.VERIFIED for action in record.actions)


@pytest.mark.asyncio
async def test_canonical_group_off_requires_only_power_and_preserves_flow(tmp_path: Path) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    left_session = _Session([_status(enabled=True, flow=70), _status(enabled=False, flow=70)])
    right_session = _Session([_status(enabled=True, flow=35), _status(enabled=False, flow=35)])
    sessions = _SessionFactory({"left": [left_session], "right": [right_session]})
    endpoints = (
        ResolvedEndpoint(left, "left", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(right, "right", wifi_firmware_version="wifi-current"),
    )
    coordinator, journal, _resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key, right.physical_identity_key}),
        functions=("power",),
    )

    result = await _actuator(coordinator, left, right).execute(
        request_id="group_stop_0001",
        plan=_plan(
            (
                _decision("left", DeviceTarget(enabled=False, power=0)),
                _decision("right", DeviceTarget(enabled=False, power=0)),
            ),
            state=GroupState.STOPPED,
        ),
    )

    expected = build_control_payload(LOCAL_WAVEMAKER_PRO, {"SwitchON": False})
    assert result.accepted is True
    assert left_session.sent == [expected]
    assert right_session.sent == [expected]
    assert len(lease.calls) == 1
    record = journal.load("group_stop_0001")
    assert record is not None
    assert [action.function for action in record.actions] == ["power", "power"]


@pytest.mark.asyncio
async def test_every_member_qualification_is_checked_before_any_group_frame(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    left_session = _Session([_status(enabled=True, flow=50)])
    right_session = _Session([_status(enabled=True, flow=50)])
    sessions = _SessionFactory({"left": [left_session], "right": [right_session]})
    endpoints = (
        ResolvedEndpoint(left, "left", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(right, "right", wifi_firmware_version="wifi-current"),
    )
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key}),
        functions=("power",),
    )

    with pytest.raises(ValueError, match="not qualified"):
        await _actuator(coordinator, left, right).execute(
            request_id="group_stop_0002",
            plan=_plan(
                (
                    _decision("left", DeviceTarget(enabled=False, power=0)),
                    _decision("right", DeviceTarget(enabled=False, power=0)),
                ),
                state=GroupState.STOPPED,
            ),
        )

    assert resolver.calls == 1
    assert sessions.created == []
    assert lease.calls == []
    assert journal.load("group_stop_0002") is None


@pytest.mark.asyncio
async def test_failure_policy_stop_resolves_writes_only_but_locks_the_whole_group(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    offline = _binding("offline", "b")
    left_session = _Session(
        [_status(enabled=True, flow=60), _status(enabled=False, flow=60)]
    )
    sessions = _SessionFactory({"left": [left_session]})
    endpoints = (ResolvedEndpoint(left, "left", wifi_firmware_version="wifi-current"),)
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key}),
        functions=("power",),
    )
    actuator = SoftwareIndependentGroupActuator(
        group_id="main_flow",
        member_ids=("left", "offline"),
        bindings={"left": left, "offline": offline},
        device_limits={"left": LIMITS, "offline": LIMITS},
        coordinator=coordinator,
    )

    result = await actuator.execute(
        request_id="group_failure_stop_0001",
        plan=_plan(
            (
                _decision("left", DeviceTarget(enabled=False, power=0)),
                _decision(
                    "offline",
                    DeviceTarget(enabled=False, power=0),
                    action=MemberAction.SKIP_OFFLINE,
                ),
            ),
            state=GroupState.ERROR,
            failure_policy_stopped=True,
        ),
    )

    assert result.accepted is True
    assert result.dispatched_member_count == 1
    assert resolver.requested_many == [(left,)]
    assert lease.calls == [(left.physical_identity_key, offline.physical_identity_key)]
    assert left_session.sent == [
        build_control_payload(LOCAL_WAVEMAKER_PRO, {"SwitchON": False})
    ]
    record = journal.load("group_failure_stop_0001")
    assert record is not None
    assert [action.target.logical_member for action in record.actions] == ["left"]


@pytest.mark.asyncio
async def test_partial_group_failure_has_one_plan_wide_reverse_recovery_authority(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    left_forward = _Session(
        [
            _status(enabled=False, flow=40),
            _status(enabled=False, flow=50),
            _status(enabled=True, flow=50),
        ]
    )
    right_forward = _Session(
        [_status(enabled=False, flow=45)],
        send_errors=[RuntimeError("ack lost")],
    )
    right_recovery = _Session([_status(enabled=False, flow=55), _status(enabled=False, flow=45)])
    left_power_recovery = _Session(
        [_status(enabled=True, flow=50), _status(enabled=False, flow=50)]
    )
    left_flow_recovery = _Session(
        [_status(enabled=False, flow=50), _status(enabled=False, flow=40)]
    )
    sessions = _SessionFactory(
        {
            "left": [left_forward, left_power_recovery, left_flow_recovery],
            "right": [right_forward, right_recovery],
        }
    )
    endpoints = (
        ResolvedEndpoint(left, "left", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(right, "right", wifi_firmware_version="wifi-current"),
    )
    coordinator, journal, _resolver, _lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key, right.physical_identity_key}),
    )
    actuator = _actuator(coordinator, left, right)

    with pytest.raises(LocalControlExecutionError, match="uncertain"):
        await actuator.execute(
            request_id="group_request_0002",
            plan=_plan(
                (
                    _decision("left", DeviceTarget(enabled=True, power=50)),
                    _decision("right", DeviceTarget(enabled=True, power=55)),
                )
            ),
        )

    record = journal.load("group_request_0002")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert [action.phase for action in record.actions] == [
        ActionPhase.VERIFIED,
        ActionPhase.VERIFIED,
        ActionPhase.UNCERTAIN,
        ActionPhase.PREPARED,
    ]
    assert {action.target.logical_member for action in record.actions} == {"left", "right"}

    restored = await coordinator.recover("group_request_0002")

    assert restored.phase is TransactionPhase.ROLLED_BACK
    assert restored.physical_write_count == 3
    assert right_recovery.sent == [build_control_payload(LOCAL_WAVEMAKER_PRO, {"Flow": 45})]
    assert left_power_recovery.sent == [
        build_control_payload(LOCAL_WAVEMAKER_PRO, {"SwitchON": False})
    ]
    assert left_flow_recovery.sent == [build_control_payload(LOCAL_WAVEMAKER_PRO, {"Flow": 40})]
    restored_record = journal.load("group_request_0002")
    assert restored_record is not None
    assert restored_record.phase is TransactionPhase.ROLLED_BACK
    assert all(action.phase is ActionPhase.ROLLED_BACK for action in restored_record.actions)


@pytest.mark.asyncio
async def test_no_dispatch_plan_creates_no_false_physical_success(tmp_path: Path) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    sessions = _SessionFactory({})
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=(),
        sessions=sessions,
        qualified_keys=frozenset(),
        functions=(),
    )
    target = DeviceTarget(enabled=True, power=50)

    result = await _actuator(coordinator, left, right).execute(
        request_id="group_skip_0001",
        plan=_plan(
            (
                _decision("left", target, action=MemberAction.SKIP_MANUAL_OVERRIDE),
                _decision("right", target, action=MemberAction.SKIP_UNKNOWN),
            ),
            state=GroupState.DEGRADED,
        ),
    )

    assert result.outcome is GroupActuationOutcome.NO_DISPATCH
    assert result.accepted is False
    assert result.public_dict()["hardware_action_requested"] is False
    assert resolver.calls == 0
    assert lease.calls == []
    assert tuple(journal.operations.iterdir()) == ()


@pytest.mark.asyncio
async def test_idempotent_replay_does_not_resolve_or_send_again(tmp_path: Path) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    left_session = _Session(
        [
            _status(enabled=False, flow=40),
            _status(enabled=False, flow=50),
            _status(enabled=True, flow=50),
        ]
    )
    right_session = _Session(
        [
            _status(enabled=False, flow=45),
            _status(enabled=False, flow=55),
            _status(enabled=True, flow=55),
        ]
    )
    sessions = _SessionFactory({"left": [left_session], "right": [right_session]})
    endpoints = (
        ResolvedEndpoint(left, "left", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(right, "right", wifi_firmware_version="wifi-current"),
    )
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key, right.physical_identity_key}),
    )
    actuator = _actuator(coordinator, left, right)
    decisions = (
        _decision("left", DeviceTarget(enabled=True, power=50)),
        _decision("right", DeviceTarget(enabled=True, power=55)),
    )

    first = await actuator.execute(
        request_id="group_replay_0001",
        plan=_plan(decisions, generation=1),
    )
    replay = await actuator.execute(
        request_id="group_replay_0001",
        plan=_plan(decisions, generation=2),
    )

    assert first.accepted is True
    assert replay.accepted is True
    assert replay.transaction is not None
    assert replay.transaction.physical_write_count == 0
    assert resolver.calls == 1
    assert len(lease.calls) == 1
    assert len(left_session.sent) == 2
    assert len(right_session.sent) == 2
    assert journal.load("group_replay_0001") is not None


@pytest.mark.asyncio
async def test_same_request_id_cannot_be_rebound_to_a_different_group_intent(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    left_session = _Session(
        [
            _status(enabled=False, flow=40),
            _status(enabled=False, flow=50),
            _status(enabled=True, flow=50),
        ]
    )
    right_session = _Session(
        [
            _status(enabled=False, flow=45),
            _status(enabled=False, flow=55),
            _status(enabled=True, flow=55),
        ]
    )
    sessions = _SessionFactory({"left": [left_session], "right": [right_session]})
    endpoints = (
        ResolvedEndpoint(left, "left", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(right, "right", wifi_firmware_version="wifi-current"),
    )
    coordinator, _journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key, right.physical_identity_key}),
    )
    actuator = _actuator(coordinator, left, right)
    await actuator.execute(
        request_id="group_rebind_0001",
        plan=_plan(
            (
                _decision("left", DeviceTarget(enabled=True, power=50)),
                _decision("right", DeviceTarget(enabled=True, power=55)),
            )
        ),
    )

    with pytest.raises(LocalControlExecutionError, match="different intent"):
        await actuator.execute(
            request_id="group_rebind_0001",
            plan=_plan(
                (
                    _decision("left", DeviceTarget(enabled=True, power=51)),
                    _decision("right", DeviceTarget(enabled=True, power=55)),
                )
            ),
        )

    assert resolver.calls == 1
    assert len(lease.calls) == 1
    assert len(left_session.sent) == 2
    assert len(right_session.sent) == 2


@pytest.mark.asyncio
async def test_current_complete_binding_mismatch_fails_before_session_or_lease(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    changed_left = left.model_copy(update={"logical_role": "unexpected_role"})
    sessions = _SessionFactory({})
    endpoints = (
        ResolvedEndpoint(changed_left, "left", wifi_firmware_version="wifi-current"),
        ResolvedEndpoint(right, "right", wifi_firmware_version="wifi-current"),
    )
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=endpoints,
        sessions=sessions,
        qualified_keys=frozenset({left.physical_identity_key, right.physical_identity_key}),
        functions=("power",),
    )

    with pytest.raises(ValueError, match="complete stable binding"):
        await _actuator(coordinator, left, right).execute(
            request_id="group_binding_0001",
            plan=_plan(
                (
                    _decision("left", DeviceTarget(enabled=False, power=0)),
                    _decision("right", DeviceTarget(enabled=False, power=0)),
                ),
                state=GroupState.STOPPED,
            ),
        )

    assert resolver.calls == 1
    assert sessions.created == []
    assert lease.calls == []
    assert journal.load("group_binding_0001") is None


@pytest.mark.asyncio
async def test_optional_target_fields_and_unsupported_products_are_rejected(
    tmp_path: Path,
) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    coordinator = object()
    with pytest.raises(TypeError, match="LocalControlCoordinator"):
        SoftwareIndependentGroupActuator(
            group_id="main_flow",
            member_ids=("left", "right"),
            bindings={"left": left, "right": right},
            device_limits={"left": LIMITS, "right": LIMITS},
            coordinator=coordinator,  # type: ignore[arg-type]
        )

    aquarium = _binding("aquarium", "c", product_key=AQUARIUM_PUMP.product_key)
    coordinator, _journal, _resolver, _lease = _coordinator(
        tmp_path,
        endpoints=(),
        sessions=_SessionFactory({}),
        qualified_keys=frozenset(),
        functions=(),
    )
    with pytest.raises(ValueError, match="unavailable for this product"):
        SoftwareIndependentGroupActuator(
            group_id="main_flow",
            member_ids=("aquarium",),
            bindings={"aquarium": aquarium},
            device_limits={"aquarium": LIMITS},
            coordinator=coordinator,
        )


@pytest.mark.asyncio
async def test_optional_mode_is_rejected_before_resolution_or_journalling(tmp_path: Path) -> None:
    left = _binding("left", "a")
    right = _binding("right", "b")
    sessions = _SessionFactory({})
    coordinator, journal, resolver, lease = _coordinator(
        tmp_path,
        endpoints=(),
        sessions=sessions,
        qualified_keys=frozenset(),
        functions=(),
    )
    actuator = _actuator(coordinator, left, right)

    with pytest.raises(ValueError, match="cannot change mode"):
        await actuator.execute(
            request_id="group_invalid_0001",
            plan=_plan(
                (
                    _decision(
                        "left",
                        DeviceTarget(enabled=True, power=50, mode="sine"),
                    ),
                    _decision("right", DeviceTarget(enabled=True, power=50)),
                )
            ),
        )

    assert resolver.calls == 0
    assert lease.calls == []
    assert tuple(journal.operations.iterdir()) == ()
