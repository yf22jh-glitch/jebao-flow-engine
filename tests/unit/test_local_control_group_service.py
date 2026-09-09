import asyncio
import json
from datetime import UTC, datetime

import pytest

from jebao_flow.config import AppConfig
from jebao_flow.groups.models import (
    FailurePolicy,
    GroupConfig,
    GroupDefaults,
    GroupExecutionStrategy,
    GroupMember,
    NativeLinkageRelation,
    NativePairConfig,
    OfflinePolicy,
    PatternKind,
)
from jebao_flow.groups.runtime import UnresolvedGroupSafetyStopError
from jebao_flow.local_control import group_service as group_service_module
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.group_service import (
    GroupLifecycleState,
    LocalControlGroupService,
)
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
)
from jebao_flow.local_control.state import FreshDeviceState
from jebao_flow.protocol.profiles import AQUARIUM_PUMP, LOCAL_WAVEMAKER_PRO

_OBSERVED_AT = datetime(2026, 9, 9, tzinfo=UTC)


def _group(
    group_id: str,
    members: tuple[str, ...],
    *,
    enabled: bool = True,
    first_member_enabled: bool = True,
    pattern: PatternKind = PatternKind.CONSTANT,
    offline_policy: OfflinePolicy = OfflinePolicy.CONTINUE_LIMITED,
) -> GroupConfig:
    return GroupConfig(
        id=group_id,
        name=group_id.replace("_", " ").title(),
        enabled=enabled,
        members=tuple(
            GroupMember(device=member, enabled=first_member_enabled if index == 0 else True)
            for index, member in enumerate(members)
        ),
        default=GroupDefaults(
            pattern=pattern,
            power=50,
            min_power=30,
            max_power=75,
        ),
        failure_policy=FailurePolicy(on_member_offline=offline_policy),
    )


def _native_group(group_id: str, members: tuple[str, str]) -> GroupConfig:
    return GroupConfig(
        id=group_id,
        name="Native group",
        execution_strategy=GroupExecutionStrategy.NATIVE_LINKED,
        native_pair=NativePairConfig(
            master=members[0],
            slave=members[1],
            relation=NativeLinkageRelation.SYNC,
        ),
        members=tuple(GroupMember(device=member) for member in members),
        default=GroupDefaults(pattern=PatternKind.NATIVE),
    )


def _current_inputs(
    groups: tuple[GroupConfig, ...],
    *,
    products: dict[str, str] | None = None,
    limits: dict[str, tuple[int, int]] | None = None,
) -> tuple[AppConfig, FreshInstallManifest]:
    member_ids = tuple(member.device for group in groups for member in group.members)
    product_by_member = products or {}
    limit_by_member = limits or {}
    devices = []
    bindings = []
    for index, member_id in enumerate(member_ids, start=1):
        product_key = product_by_member.get(member_id, LOCAL_WAVEMAKER_PRO.product_key)
        minimum, maximum = limit_by_member.get(member_id, (30, 100))
        devices.append(
            {
                "id": member_id,
                "name": member_id.replace("_", " ").title(),
                "type": "wavemaker",
                "product_key": product_key,
                "address": f"192.0.2.{index}",
                "identity": {
                    "device_id": f"synthetic-device-{index}",
                    "mac_address": f"0200000000{index:02x}",
                },
                "limits": {"min_power": minimum, "max_power": maximum},
            }
        )
        bindings.append(
            LocalDeviceBinding(
                logical_member=member_id,
                product_key=product_key,
                physical_identity_key=f"{index:x}" * 64,
                logical_role="test_member",
                role_attested_by="operator",
                role_attested_at=_OBSERVED_AT,
            )
        )
    config = AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/test"},
            "devices": devices,
            "groups": [group.model_dump(mode="json") for group in groups],
        }
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=_OBSERVED_AT,
        devices=tuple(bindings),
        content=InstallContentBindings(
            app_catalog_sha256="a" * 64,
            protocol_schema_sha256="b" * 64,
        ),
    )
    return config, manifest


def _catalog(manifest: FreshInstallManifest):
    return build_local_control_catalog((), current_content=manifest.content)


def _coordinator_without_io() -> LocalControlCoordinator:
    # No supervisor is armed in this slice, so the coordinator is stored but never invoked.
    return LocalControlCoordinator.__new__(LocalControlCoordinator)


def _fresh(manifest: FreshInstallManifest, *, available: bool = True):
    return tuple(
        FreshDeviceState(
            logical_member=binding.logical_member,
            available=available,
            observed_at=_OBSERVED_AT if available else None,
            diagnostic_reason=None if available else "current_discovery_unavailable",
        )
        for binding in manifest.devices
    )


class _RecordingActuator:
    calls: list[str] = []
    limits: dict[str, object] = {}

    def __init__(self, *, group_id, member_ids, bindings, device_limits, coordinator) -> None:
        del bindings, coordinator
        self.group_id = group_id
        self.member_ids = member_ids
        type(self).limits = dict(device_limits)

    def validate_plan(self, plan) -> None:
        assert plan.group_id == self.group_id

    async def write_plan(self, plan, **kwargs) -> None:
        del plan, kwargs
        type(self).calls.append(self.group_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group_enabled", "first_member_enabled"),
    [(True, True), (False, True), (True, False)],
)
async def test_complete_cycles_never_write_before_a_group_command(
    monkeypatch,
    group_enabled: bool,
    first_member_enabled: bool,
) -> None:
    group = _group(
        "main_flow",
        ("left", "right"),
        enabled=group_enabled,
        first_member_enabled=first_member_enabled,
    )
    config, manifest = _current_inputs((group,))
    _RecordingActuator.calls = []
    monkeypatch.setattr(
        group_service_module,
        "SoftwareIndependentGroupActuator",
        _RecordingActuator,
    )
    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    for _index in range(3):
        await service.observe_fresh_cycle(_fresh(manifest))

    assert _RecordingActuator.calls == []
    item = service.snapshot().groups[0]
    assert item.state is GroupLifecycleState.SUPERVISED
    assert item.cycle_count == 3
    assert item.last_cycle_disposition == "write_disarmed"
    await service.aclose()


@pytest.mark.asyncio
async def test_full_discovery_failure_stays_unknown_and_sends_no_plan(monkeypatch) -> None:
    config, manifest = _current_inputs((_group("main_flow", ("left", "right")),))
    _RecordingActuator.calls = []
    monkeypatch.setattr(
        group_service_module,
        "SoftwareIndependentGroupActuator",
        _RecordingActuator,
    )
    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    snapshot = await service.observe_fresh_cycle(_fresh(manifest, available=False))

    assert snapshot.groups[0].last_cycle_disposition == "write_disarmed"
    assert _RecordingActuator.calls == []
    await service.aclose()


def test_read_only_lifecycle_reports_zero_supervisors_without_a_fake_port() -> None:
    config, manifest = _current_inputs((_group("main_flow", ("left", "right")),))

    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=None,
        operation_lock=asyncio.Lock(),
    )

    snapshot = service.snapshot()
    assert snapshot.supervisor_count == 0
    assert snapshot.groups[0].state is GroupLifecycleState.UNCONSTRUCTED
    assert snapshot.groups[0].diagnostic_reason == "write_coordinator_unavailable"


@pytest.mark.parametrize(
    ("group", "expected_reason"),
    [
        pytest.param(
            _group(
                "fallback_group",
                ("left", "right"),
                offline_policy=OfflinePolicy.FALLBACK_CONSTANT,
            ),
            "fallback_constant_not_supported",
            id="fallback-constant",
        ),
        pytest.param(
            _group(
                "future_pattern",
                ("left", "right"),
                pattern=PatternKind.SINE,
            ),
            "pattern_not_supported",
            id="unimplemented-pattern",
        ),
        pytest.param(
            _native_group("native_group", ("left", "right")),
            "native_linked_not_supported",
            id="native-linked",
        ),
    ],
)
def test_known_unsupported_group_shapes_are_construction_diagnostics(
    group: GroupConfig,
    expected_reason: str,
) -> None:
    config, manifest = _current_inputs((group,))

    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    assert service.supervisor_count == 0
    assert service.snapshot().groups[0].diagnostic_reason == expected_reason


@pytest.mark.asyncio
async def test_one_unconstructable_group_does_not_block_another_group_cycle() -> None:
    supported = _group("supported", ("left", "right"))
    unsupported = _group("unsupported", ("tank", "return_pump"))
    config, manifest = _current_inputs(
        (supported, unsupported),
        products={
            "tank": AQUARIUM_PUMP.product_key,
            "return_pump": AQUARIUM_PUMP.product_key,
        },
    )
    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    snapshot = await service.observe_fresh_cycle(_fresh(manifest))

    by_group = {item.group_id: item for item in snapshot.groups}
    assert snapshot.supervisor_count == 1
    assert by_group["supported"].state is GroupLifecycleState.SUPERVISED
    assert by_group["supported"].cycle_count == 1
    assert by_group["unsupported"].state is GroupLifecycleState.UNCONSTRUCTED
    assert by_group["unsupported"].diagnostic_reason == "unsupported_product"
    await service.aclose()


@pytest.mark.asyncio
async def test_one_group_tick_failure_is_isolated_and_other_groups_continue(monkeypatch) -> None:
    left = _group("left_group", ("left_a", "left_b"))
    right = _group("right_group", ("right_a", "right_b"))
    config, manifest = _current_inputs((left, right))
    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )
    failing = service._groups[0].supervisor  # noqa: SLF001
    assert failing is not None

    async def fail_cycle(states) -> None:
        del states
        raise RuntimeError("injected planning failure")

    monkeypatch.setattr(failing, "observe_fresh_cycle", fail_cycle)

    snapshot = await service.observe_fresh_cycle(_fresh(manifest))

    by_group = {item.group_id: item for item in snapshot.groups}
    assert by_group["left_group"].state is GroupLifecycleState.CYCLE_FAILED
    assert by_group["left_group"].diagnostic_reason == "group_cycle_failed"
    assert by_group["right_group"].state is GroupLifecycleState.SUPERVISED
    assert by_group["right_group"].cycle_count == 1
    await service.aclose()


def test_device_limits_use_the_config_and_declared_flow_intersection(monkeypatch) -> None:
    config, manifest = _current_inputs(
        (_group("main_flow", ("left", "right")),),
        limits={"left": (20, 100), "right": (40, 70)},
    )
    monkeypatch.setattr(
        group_service_module,
        "SoftwareIndependentGroupActuator",
        _RecordingActuator,
    )

    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    assert service.supervisor_count == 1
    assert _RecordingActuator.limits["left"].min_power == 30
    assert _RecordingActuator.limits["left"].max_power == 75
    assert _RecordingActuator.limits["left"].power_step == 1
    assert _RecordingActuator.limits["right"].min_power == 40
    assert _RecordingActuator.limits["right"].max_power == 70


def test_empty_safe_flow_intersection_is_an_isolated_diagnostic() -> None:
    config, manifest = _current_inputs(
        (_group("main_flow", ("left", "right")),),
        limits={"left": (80, 90)},
    )

    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    assert service.supervisor_count == 0
    assert service.snapshot().groups[0].diagnostic_reason == "safe_flow_intersection_empty"


@pytest.mark.asyncio
async def test_group_plan_port_uses_the_shared_operation_lock() -> None:
    calls = []

    class _Delegate:
        group_id = "main_flow"
        member_ids = ("left", "right")

        def validate_plan(self, plan) -> None:
            calls.append(("validate", plan))

        async def write_plan(self, plan, **kwargs) -> None:
            calls.append(("write", plan, kwargs))

    operation_lock = asyncio.Lock()
    await operation_lock.acquire()
    port = group_service_module._OperationLockedGroupPlanPort(  # noqa: SLF001
        _Delegate(),
        operation_lock,
    )
    task = asyncio.create_task(
        port.write_plan(
            "plan",  # type: ignore[arg-type]
            request_id="group_request",
            generation=1,
            plan_identity_sha256="a" * 64,
            guard=lambda: True,
            safety_token=object(),
        )
    )
    await asyncio.sleep(0)

    assert calls == []

    operation_lock.release()
    await task
    assert calls[0][0] == "write"


@pytest.mark.asyncio
async def test_close_attempts_every_supervisor_and_keeps_unresolved_off_sticky(
    monkeypatch,
) -> None:
    groups = (
        _group("left_group", ("left_a", "left_b")),
        _group("right_group", ("right_a", "right_b")),
    )
    config, manifest = _current_inputs(groups)
    created = []

    class _ClosingSupervisor:
        def __init__(self, *, manager, runtime) -> None:
            del runtime
            self.group_id = manager.group.id
            self.close_count = 0
            created.append(self)

        async def aclose(self) -> None:
            self.close_count += 1
            if self.group_id == "left_group":
                raise UnresolvedGroupSafetyStopError(self.group_id)

    monkeypatch.setattr(
        group_service_module,
        "SoftwareIndependentGroupActuator",
        _RecordingActuator,
    )
    monkeypatch.setattr(
        group_service_module,
        "LocalControlGroupSupervisor",
        _ClosingSupervisor,
    )
    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=_coordinator_without_io(),
        operation_lock=asyncio.Lock(),
    )

    with pytest.raises(UnresolvedGroupSafetyStopError):
        await service.aclose()
    assert [item.close_count for item in created] == [1, 1]

    with pytest.raises(UnresolvedGroupSafetyStopError):
        await service.aclose()
    assert [item.close_count for item in created] == [2, 2]


def test_public_inventory_is_bounded_ascii_and_exposes_no_physical_identity() -> None:
    config, manifest = _current_inputs((_group("main_flow", ("left", "right")),))
    service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=_catalog(manifest),
        coordinator=None,
        operation_lock=asyncio.Lock(),
    )

    encoded = json.dumps(service.public_dict(), ensure_ascii=True)

    assert encoded.isascii()
    assert manifest.devices[0].physical_identity_key not in encoded
    assert service.public_dict()["physical_identity_keys_exposed"] is False
    assert service.public_dict()["private_paths_exposed"] is False
