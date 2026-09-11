import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow.config import AppConfig, MqttConfig
from jebao_flow.groups.models import GroupConfig, GroupMember
from jebao_flow.groups.runtime import UnresolvedGroupSafetyStopError
from jebao_flow.local_control import mqtt as mqtt_module
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlResult
from jebao_flow.local_control.group_service import (
    DirectControlOwnershipDecision,
    GroupActualOutput,
    GroupOutputState,
    LocalControlGroupService,
)
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.mqtt import (
    COMMAND_QOS,
    LocalControlCommandConflictError,
    LocalControlGroupCommandRouter,
    LocalControlMqttAdapter,
    LocalControlMqttError,
    LocalControlMqttRouter,
)
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.local_control.state import FreshDeviceState, StatePublication
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _binding(member: str, identity: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=identity * 64,
        logical_role=member,
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="f" * 64,
        template_sha256="a" * 64,
        schema_sha256="b" * 64,
        firmware_version="current",
        receipt_sha256=("c" * 64, "d" * 64),
    )


@dataclass
class _Coordinator:
    scalar: list
    schedule: list

    async def execute(self, intent):
        self.scalar.append(intent)
        return _result(intent.request_id)

    async def execute_schedule_flow(self, **kwargs):
        self.schedule.append(kwargs)
        return _result(kwargs["request_id"])


def _result(request_id: str) -> LocalControlResult:
    return LocalControlResult(
        request_id=request_id,
        phase=TransactionPhase.VERIFIED,
        revision=2,
        action_count=1,
        journal_sha256="e" * 64,
        physical_write_count=1,
    )


def _rejected_result(request_id: str) -> LocalControlResult:
    return LocalControlResult(
        request_id=request_id,
        phase=TransactionPhase.ROLLED_BACK,
        revision=3,
        action_count=1,
        journal_sha256="e" * 64,
        physical_write_count=0,
    )


def _setup(
    *,
    ownership_service=None,
    groups=(),
    mismatch_ownership_lock=False,
    control_surface_enabled=True,
):
    master = _binding("master", "1")
    slave = _binding("slave", "2")
    content = InstallContentBindings(
        app_catalog_sha256="f" * 64,
        protocol_schema_sha256="b" * 64,
        template_sha256=("a" * 64,),
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(master, slave),
        content=content,
    )
    catalog = build_local_control_catalog(
        (
            _qualification("power"),
            _qualification("manual_flow"),
            _qualification("schedule_reset"),
            _qualification("slave_per_slot_flow_extension"),
        ),
        current_content=content,
        _qualification_verifier_for_test=lambda _item: frozenset({"1" * 64, "2" * 64}),
    )
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slaves=(FreshSlaveSchedule(target=slave),),
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        schedule_plans=(plan,),
        groups=groups,
        control_surface_enabled=control_surface_enabled,
    )
    coordinator = _Coordinator([], [])
    operation_lock = asyncio.Lock() if ownership_service is not None else None
    if ownership_service is not None:
        ownership_service.operation_lock = (
            asyncio.Lock() if mismatch_ownership_lock else operation_lock
        )
    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(plan,),
        coordinator=coordinator,  # type: ignore[arg-type]
        operation_lock=operation_lock,
        ownership_service=ownership_service,
    )
    return projection, coordinator, router


@pytest.mark.asyncio
async def test_scalar_command_creates_internal_request_and_stable_target() -> None:
    projection, coordinator, router = _setup()
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "manual_flow"
    )

    routed = await router.handle(entity.command_topic or "", b"55")

    assert routed.result.accepted is True
    assert routed.state_value == "55"
    assert routed.request_id.startswith("mqtt_")
    assert coordinator.scalar[0].target.logical_member == "master"
    assert coordinator.scalar[0].value == 55


@pytest.mark.asyncio
async def test_schedule_slot_topic_routes_one_fresh_slave_flow_operation() -> None:
    projection, coordinator, router = _setup()
    entity = next(item for item in projection.entities if item.slot_index == 17)

    routed = await router.handle(entity.command_topic or "", "63")

    assert routed.state_value == "63"
    assert coordinator.schedule[0]["slot_index"] == 17
    assert coordinator.schedule[0]["flow"] == 63
    assert coordinator.schedule[0]["master"].logical_member == "master"
    assert coordinator.schedule[0]["slave"].logical_member == "slave"


class _OwnershipService:
    def __init__(self, *, allowed: bool = True, reason: str | None = None) -> None:
        self.allowed = allowed
        self.reason = reason
        self.calls: list[str] = []
        self.power_on_requests: list[bool] = []
        self.verified_power_on_calls: list[str] = []
        self.coordinator: _Coordinator | None = None
        self.operation_lock: asyncio.Lock | None = None

    async def claim_direct_ownership(
        self,
        logical_member: str,
        *,
        power_on_requested: bool = False,
    ):
        assert self.operation_lock is not None
        assert self.operation_lock.locked()
        self.calls.append(logical_member)
        self.power_on_requests.append(power_on_requested)
        if self.coordinator is not None:
            assert self.coordinator.scalar == []
            assert self.coordinator.schedule == []
        return DirectControlOwnershipDecision(
            logical_member=logical_member,
            group_id="main_flow",
            allowed=self.allowed,
            manual_override_recorded=self.allowed,
            diagnostic_reason=(
                self.reason if not self.allowed or power_on_requested else None
            ),
        )

    async def note_verified_direct_power_on(self, logical_member: str) -> None:
        assert self.operation_lock is not None
        assert self.operation_lock.locked()
        self.verified_power_on_calls.append(logical_member)


@pytest.mark.asyncio
@pytest.mark.parametrize("route_kind", ["scalar", "schedule"])
async def test_valid_direct_routes_claim_manual_ownership_before_the_coordinator(
    route_kind: str,
) -> None:
    ownership = _OwnershipService()
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(
        item
        for item in projection.entities
        if (
            item.logical_member == "master" and item.function == "manual_flow"
            if route_kind == "scalar"
            else item.slot_index == 17
        )
    )

    routed = await router.handle(entity.command_topic or "", "55")

    assert ownership.calls == ["master" if route_kind == "scalar" else "slave"]
    assert ownership.power_on_requests == [False]
    assert ownership.verified_power_on_calls == []
    assert routed.ownership is not None
    assert routed.ownership.manual_override_recorded
    assert routed.public_result()["direct_control_ownership"]["group_id"] == "main_flow"


@pytest.mark.asyncio
async def test_manual_ownership_remains_declared_when_the_coordinator_fails() -> None:
    ownership = _OwnershipService()
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "manual_flow"
    )

    async def fail_after_ownership(_intent) -> None:
        raise RuntimeError("injected coordinator preflight failure")

    coordinator.execute = fail_after_ownership

    with pytest.raises(RuntimeError, match="preflight failure"):
        await router.handle(entity.command_topic or "", "55")

    assert ownership.calls == ["master"]
    assert ownership.verified_power_on_calls == []


@pytest.mark.asyncio
async def test_invalid_direct_payload_never_claims_ownership_or_calls_the_coordinator() -> None:
    ownership = _OwnershipService()
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(item for item in projection.entities if item.slot_index == 0)

    with pytest.raises(LocalControlMqttError, match="native range"):
        await router.handle(entity.command_topic or "", "101")

    assert ownership.calls == []
    assert coordinator.scalar == []
    assert coordinator.schedule == []


@pytest.mark.asyncio
async def test_emergency_ownership_conflict_is_pre_coordinator_and_uses_stable_reason() -> None:
    reason = "group_emergency_stop_blocks_direct_control"
    ownership = _OwnershipService(allowed=False, reason=reason)
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "manual_flow"
    )

    with pytest.raises(LocalControlCommandConflictError, match=reason):
        await router.handle(entity.command_topic or "", "55")

    assert ownership.calls == ["master"]
    assert coordinator.scalar == []
    assert coordinator.schedule == []


def test_direct_ownership_requires_the_exact_device_write_lock() -> None:
    ownership = _OwnershipService()

    with pytest.raises(ValueError, match="one shared operation lock"):
        _setup(ownership_service=ownership, mismatch_ownership_lock=True)


@pytest.mark.asyncio
async def test_schedule_reset_button_routes_an_action_without_optimistic_state() -> None:
    projection, coordinator, router = _setup()
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "schedule_reset"
    )

    routed = await router.handle(entity.command_topic or "", "PRESS")

    assert routed.result.accepted is True
    assert routed.state_value is None
    assert coordinator.scalar[0].function == "schedule_reset"
    assert coordinator.scalar[0].value is None


@pytest.mark.asyncio
async def test_router_rejects_unpublished_and_out_of_range_commands() -> None:
    projection, _coordinator, router = _setup()
    entity = next(item for item in projection.entities if item.slot_index == 0)

    with pytest.raises(LocalControlMqttError, match="published"):
        await router.handle("jebao-flow/main/local/not-published", "45")
    with pytest.raises(LocalControlMqttError, match="native range"):
        await router.handle(entity.command_topic or "", "101")


@pytest.mark.asyncio
async def test_nonaccepted_result_never_becomes_retained_command_state() -> None:
    projection, coordinator, router = _setup()
    coordinator.execute = lambda intent: _async_result(_rejected_result(intent.request_id))
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "manual_flow"
    )

    routed = await router.handle(entity.command_topic or "", "55")

    assert routed.result.accepted is False
    assert routed.state_value is None


@pytest.mark.asyncio
async def test_only_verified_power_on_records_reconciliation_and_keeps_warning() -> None:
    reason = "group_forced_off_will_revert_next_cycle"
    ownership = _OwnershipService(allowed=True, reason=reason)
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "power"
    )

    routed = await router.handle(entity.command_topic or "", "ON")

    assert ownership.power_on_requests == [True]
    assert ownership.verified_power_on_calls == ["master"]
    assert routed.ownership is not None
    assert routed.ownership.diagnostic_reason == reason


@pytest.mark.asyncio
async def test_nonverified_power_on_has_no_reconciliation_or_revert_warning() -> None:
    reason = "group_forced_off_will_revert_next_cycle"
    ownership = _OwnershipService(allowed=True, reason=reason)
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    coordinator.execute = lambda intent: _async_result(_rejected_result(intent.request_id))
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "power"
    )

    routed = await router.handle(entity.command_topic or "", "ON")

    assert ownership.power_on_requests == [True]
    assert ownership.verified_power_on_calls == []
    assert routed.ownership is not None
    assert routed.ownership.diagnostic_reason is None


@pytest.mark.asyncio
async def test_power_off_and_non_power_commands_do_not_record_reconciliation() -> None:
    reason = "group_forced_off_will_revert_next_cycle"
    ownership = _OwnershipService(allowed=True, reason=reason)
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    power = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "power"
    )
    flow = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "manual_flow"
    )

    power_result = await router.handle(power.command_topic or "", "OFF")
    coordinator.scalar.clear()
    flow_result = await router.handle(flow.command_topic or "", "55")

    assert ownership.power_on_requests == [False, False]
    assert ownership.verified_power_on_calls == []
    assert power_result.ownership is not None
    assert power_result.ownership.diagnostic_reason is None
    assert flow_result.ownership is not None
    assert flow_result.ownership.diagnostic_reason is None


async def _async_result(result: LocalControlResult) -> LocalControlResult:
    return result


def test_command_transport_is_explicitly_at_most_once() -> None:
    assert COMMAND_QOS == 0


def test_read_only_router_needs_no_write_coordinator() -> None:
    master = _binding("master", "1")
    content = InstallContentBindings(
        app_catalog_sha256="f" * 64,
        protocol_schema_sha256="b" * 64,
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(master,),
        content=content,
    )
    catalog = build_local_control_catalog((), current_content=content)
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )

    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(),
        coordinator=None,
    )

    assert router.command_topics == ()


def test_qualified_router_requires_a_write_coordinator() -> None:
    master = _binding("master", "1")
    content = InstallContentBindings(
        app_catalog_sha256="f" * 64,
        protocol_schema_sha256="b" * 64,
        template_sha256=("a" * 64,),
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(master,),
        content=content,
    )
    catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=content,
        _qualification_verifier_for_test=lambda _item: frozenset({"1" * 64}),
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
    )

    with pytest.raises(ValueError, match="write coordinator"):
        LocalControlMqttRouter(
            projection=projection,
            manifest=manifest,
            catalog=catalog,
            schedule_plans=(),
            coordinator=None,
        )


class _Client:
    def __init__(self) -> None:
        self.published: list[tuple[str, object, int, bool]] = []

    async def publish(self, topic, payload, *, qos, retain) -> None:
        self.published.append((str(topic), payload, qos, retain))


class _OneMessageClient(_Client):
    def __init__(self, message) -> None:
        super().__init__()
        self.messages = self._messages(message)

    @staticmethod
    async def _messages(message):
        yield message


class _IdleMessages:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()
        raise StopAsyncIteration


class _RunClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.subscribed: list[tuple[str, int]] = []
        self.events: list[tuple[str, str, object, int, bool]] = []
        self.messages = _IdleMessages()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def publish(self, topic, payload, *, qos, retain) -> None:
        await super().publish(topic, payload, qos=qos, retain=retain)
        self.events.append(("publish", str(topic), payload, qos, retain))

    async def subscribe(self, topic, *, qos) -> None:
        self.subscribed.append((str(topic), qos))
        self.events.append(("subscribe", str(topic), None, qos, False))


@pytest.mark.asyncio
async def test_observer_adapter_does_not_publish_discovery_deletions(monkeypatch) -> None:
    projection, _coordinator, router = _setup(control_surface_enabled=False)

    class _Poller:
        logical_members = ("master", "slave")

        async def run(self, publish, stop_event) -> None:
            await stop_event.wait()

    client = _RunClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_Poller(),  # type: ignore[arg-type]
    )
    stop_event = asyncio.Event()
    stop_event.set()

    await adapter.run(stop_event)

    discovery = [
        (topic, payload)
        for topic, payload, qos, retain in client.published
        if topic.endswith("/config") and qos == 1 and retain
    ]
    current_topics = {topic for topic, _payload in projection.discovery_records()}
    tombstone_topics = set(projection.discovery_tombstone_topics())
    assert discovery
    assert {topic for topic, payload in discovery if payload != b""} == current_topics
    assert {topic for topic, payload in discovery if payload == b""} == tombstone_topics
    assert current_topics.isdisjoint(tombstone_topics)
    assert sum(payload == b"" for _topic, payload, _qos, _retain in client.published) == len(
        tombstone_topics
    )
    assert client.subscribed == []


@pytest.mark.asyncio
async def test_owned_tombstones_precede_subscriptions_and_current_discovery(monkeypatch) -> None:
    projection, _coordinator, router = _setup()

    class _Poller:
        logical_members = ("master", "slave")

        async def run(self, publish, stop_event) -> None:
            await stop_event.wait()

    client = _RunClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_Poller(),  # type: ignore[arg-type]
    )
    stop_event = asyncio.Event()
    stop_event.set()

    await adapter.run(stop_event)

    expected_tombstones = projection.discovery_tombstone_topics()
    tombstone_indices = [
        index
        for index, event in enumerate(client.events)
        if event[0] == "publish" and event[2] == b""
    ]
    offline_indices = [
        index
        for index, event in enumerate(client.events)
        if event[0] == "publish" and event[2] == "offline"
    ]
    subscribe_indices = [
        index for index, event in enumerate(client.events) if event[0] == "subscribe"
    ]
    current_topics = {topic for topic, _payload in projection.discovery_records()}
    current_discovery_indices = [
        index
        for index, event in enumerate(client.events)
        if event[0] == "publish" and event[1] in current_topics and event[2] != b""
    ]

    assert expected_tombstones
    assert [client.events[index][1] for index in tombstone_indices] == list(
        expected_tombstones
    )
    assert len(tombstone_indices) == len(expected_tombstones)
    startup_offline_indices = [
        index for index in offline_indices if index < min(tombstone_indices)
    ]
    assert startup_offline_indices
    assert max(startup_offline_indices) < min(tombstone_indices)
    assert not [
        index
        for index in offline_indices
        if min(tombstone_indices) < index < min(subscribe_indices)
    ]
    assert max(tombstone_indices) < min(subscribe_indices)
    assert max(tombstone_indices) < min(current_discovery_indices)
    assert min(subscribe_indices) > max(tombstone_indices)
    assert all(
        client.events[index][3:] == (1, True) for index in tombstone_indices
    )


@pytest.mark.asyncio
async def test_owned_tombstone_sequence_is_bounded_and_repeatable_per_connection(
    monkeypatch,
) -> None:
    projection, _coordinator, router = _setup()

    class _Poller:
        logical_members = ("master", "slave")

        async def run(self, publish, stop_event) -> None:
            await stop_event.wait()

    clients = (_RunClient(), _RunClient())
    pending_clients = iter(clients)
    monkeypatch.setattr(
        mqtt_module.aiomqtt,
        "Client",
        lambda *args, **kwargs: next(pending_clients),
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_Poller(),  # type: ignore[arg-type]
    )

    for _client in clients:
        stop_event = asyncio.Event()
        stop_event.set()
        await adapter.run(stop_event)

    tombstone_sequences = tuple(
        tuple(topic for topic, payload, _qos, _retain in client.published if payload == b"")
        for client in clients
    )
    assert tombstone_sequences == (
        projection.discovery_tombstone_topics(),
        projection.discovery_tombstone_topics(),
    )


@pytest.mark.asyncio
async def test_tombstone_publish_failure_stops_subscription_discovery_and_online(
    monkeypatch,
) -> None:
    projection, _coordinator, router = _setup()

    class _Poller:
        logical_members = ("master", "slave")

        async def run(self, publish, stop_event) -> None:
            await stop_event.wait()

    class _FailingClient(_RunClient):
        async def publish(self, topic, payload, *, qos, retain) -> None:
            await super().publish(topic, payload, qos=qos, retain=retain)
            if payload == b"":
                raise RuntimeError("owned discovery tombstone rejected")

    client = _FailingClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_Poller(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="owned discovery tombstone rejected"):
        await adapter.run(asyncio.Event())

    current_topics = {topic for topic, _payload in projection.discovery_records()}
    assert client.subscribed == []
    assert not any(
        topic in current_topics and payload != b""
        for topic, payload, _qos, _retain in client.published
    )
    assert not any(payload == "online" for _topic, payload, _qos, _retain in client.published)


@pytest.mark.asyncio
async def test_fresh_state_failure_marks_only_that_device_unavailable() -> None:
    projection, _coordinator, router = _setup()
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=type("Poller", (), {"logical_members": ("master", "slave")})(),
    )
    client = _Client()

    await adapter._publish_fresh_state(
        client,  # type: ignore[arg-type]
        FreshDeviceState(
            logical_member="slave",
            available=False,
            diagnostic_reason="fresh_state_unavailable",
        ),
    )

    assert client.published[0] == (
        "jebao-flow/main/local/devices/slave/availability",
        "offline",
        1,
        True,
    )
    assert all("/state" not in topic for topic, _payload, _qos, _retain in client.published)


@pytest.mark.asyncio
async def test_fresh_values_are_published_before_device_becomes_available() -> None:
    projection, _coordinator, router = _setup()
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=type("Poller", (), {"logical_members": ("master", "slave")})(),
    )
    client = _Client()
    state_topic = "jebao-flow/main/local/devices/slave/functions/manual_flow/state"

    await adapter._publish_fresh_state(
        client,  # type: ignore[arg-type]
        FreshDeviceState(
            logical_member="slave",
            available=True,
            publications=(StatePublication(state_topic, "55"),),
            observed_at=datetime(2026, 9, 8, 6, 0, tzinfo=UTC),
        ),
    )

    assert client.published == [
        (state_topic, "55", 1, True),
        (
            "jebao-flow/main/local/devices/slave/availability",
            "online",
            1,
            True,
        ),
    ]


@pytest.mark.asyncio
async def test_runtime_clears_device_availability_before_discovery_and_uses_current_password(
    monkeypatch,
) -> None:
    projection, _coordinator, router = _setup()

    class _Poller:
        logical_members = ("master", "slave")

        async def run(self, publish, stop_event) -> None:
            await stop_event.wait()

    client = _RunClient()
    captured = {}

    def client_factory(*args, **kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", client_factory)
    monkeypatch.setenv("IGNORED_MQTT_PASSWORD", "stale-environment-secret")
    adapter = LocalControlMqttAdapter(
        MqttConfig(
            host="broker.invalid",
            topic_prefix="jebao-flow/main",
            password_env="IGNORED_MQTT_PASSWORD",
        ),
        projection=projection,
        router=router,
        state_poller=_Poller(),  # type: ignore[arg-type]
        password="current-file-secret",
    )
    stop_event = asyncio.Event()
    stop_event.set()

    await adapter.run(stop_event)

    assert captured["password"] == "current-file-secret"
    first_discovery = next(
        index for index, item in enumerate(client.published) if item[0].endswith("/config")
    )
    assert [item[:2] for item in client.published[:first_discovery]] == [
        ("jebao-flow/main/local/availability", "offline"),
        ("jebao-flow/main/local/devices/master/availability", "offline"),
        ("jebao-flow/main/local/devices/slave/availability", "offline"),
    ]


class _GroupLifecycle:
    def __init__(
        self,
        *,
        close_error: Exception | None = None,
        outputs: tuple[GroupOutputState, ...] = (),
    ) -> None:
        self.cycles = []
        self.close_count = 0
        self.close_error = close_error
        self.outputs = outputs

    @property
    def configured_group_ids(self):
        return ("main_flow",)

    @property
    def qualified_group_ids(self):
        return ("main_flow",)

    @property
    def qualified_group_members(self):
        return (("main_flow", "master"), ("main_flow", "slave"))

    async def observe_fresh_cycle(self, states) -> None:
        self.cycles.append(states)

    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error

    def public_dict(self):
        return {
            "schema_version": 1,
            "status": "group_lifecycle",
            "configured_group_count": 1,
            "supervisor_count": 0,
            "groups": [
                {
                    "group_id": "main_flow",
                    "state": "unconstructed",
                    "diagnostic_reason": "unsupported_product",
                }
            ],
            "physical_identity_keys_exposed": False,
        }

    def group_output_states(self):
        return self.outputs


class _OneCyclePoller:
    logical_members = ("master", "slave")

    def __init__(self, results) -> None:
        self.results = results

    async def run(self, publish, stop_event, *, observe_cycle=None) -> None:
        assert observe_cycle is not None
        for result in self.results:
            await publish(result)
        await observe_cycle(self.results)
        stop_event.set()


@pytest.mark.asyncio
async def test_group_inventory_and_complete_cycle_are_additive_to_device_state(
    monkeypatch,
) -> None:
    projection, _coordinator, router = _setup()
    results = (
        FreshDeviceState(
            logical_member="master",
            available=False,
            diagnostic_reason="current_discovery_unavailable",
        ),
        FreshDeviceState(
            logical_member="slave",
            available=False,
            diagnostic_reason="current_discovery_unavailable",
        ),
    )
    group_service = _GroupLifecycle()
    client = _RunClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_OneCyclePoller(results),  # type: ignore[arg-type]
        group_service=group_service,
    )

    await adapter.run(asyncio.Event())

    assert group_service.cycles == [results]
    assert group_service.close_count == 1
    group_status = [
        json.loads(payload)
        for topic, payload, qos, retain in client.published
        if topic == "jebao-flow/main/local/groups/status" and qos == 1 and retain
    ]
    assert len(group_status) == 2
    assert group_status[-1]["groups"][0]["diagnostic_reason"] == "unsupported_product"
    assert (
        "jebao-flow/main/local/devices/master/availability",
        "offline",
        1,
        True,
    ) in client.published


@pytest.mark.asyncio
async def test_fresh_group_output_publishes_command_free_state_and_truth_attributes() -> None:
    group = GroupConfig(
        id="main_flow",
        name="Main Flow",
        members=(GroupMember(device="master"), GroupMember(device="slave")),
    )
    projection, _coordinator, router = _setup(groups=(group,))
    output = GroupOutputState(
        group_id="main_flow",
        actual_output=GroupActualOutput.ON,
        install_generation="0" * 32,
        manual_override_members=("master",),
        ownership_diagnostic_reason="group_forced_off_will_revert_next_cycle",
        command_qualified=True,
        supervisor_constructed=True,
    )
    group_service = _GroupLifecycle(outputs=(output,))
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=group_service,  # type: ignore[arg-type]
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_OneCyclePoller(()),  # type: ignore[arg-type]
        group_service=group_service,
        group_router=group_router,
    )
    client = _Client()

    await adapter._observe_group_cycle(client, ())  # noqa: SLF001

    [entity] = projection.group_entities
    published = {topic: payload for topic, payload, _qos, _retain in client.published}
    assert published[entity.state_topic] == "on"
    assert published[entity.availability_topic] == "online"
    attributes = json.loads(published[entity.attributes_topic])
    assert attributes["install_generation"] == "0" * 32
    assert attributes["manual_override_members"] == ["master"]
    assert attributes["pattern_conformance"] == "unknown"
    assert entity.command_topic is None

    group_service.outputs = (
        GroupOutputState(
            group_id="main_flow",
            actual_output=GroupActualOutput.UNKNOWN,
            install_generation="0" * 32,
            manual_override_members=("master",),
            ownership_diagnostic_reason=None,
            command_qualified=True,
            supervisor_constructed=True,
        ),
    )
    await adapter._observe_group_cycle(client, ())  # noqa: SLF001
    assert client.published[-1][:2] == (entity.availability_topic, "offline")


@pytest.mark.asyncio
async def test_group_output_inventory_mismatch_is_isolated_from_device_state_sync() -> None:
    group = GroupConfig(
        id="main_flow",
        name="Main Flow",
        members=(GroupMember(device="master"), GroupMember(device="slave")),
    )
    projection, _coordinator, router = _setup(groups=(group,))
    group_service = _GroupLifecycle(outputs=())
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=group_service,  # type: ignore[arg-type]
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_OneCyclePoller(()),  # type: ignore[arg-type]
        group_service=group_service,
        group_router=group_router,
    )
    client = _Client()

    await adapter._observe_group_cycle(client, ())  # noqa: SLF001

    [entity] = projection.group_entities
    assert (
        entity.availability_topic,
        "offline",
        1,
        True,
    ) in client.published
    assert all(topic != entity.state_topic for topic, _payload, _qos, _retain in client.published)
    status = json.loads(client.published[-1][1])
    assert status["status"] == "group_lifecycle_unavailable"
    assert status["diagnostic_reason"] == "group_output_inventory_mismatch"


@pytest.mark.asyncio
async def test_stopped_warning_is_in_the_direct_command_result() -> None:
    reason = "group_forced_off_will_revert_next_cycle"
    ownership = _OwnershipService(allowed=True, reason=reason)
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "power"
    )

    routed = await router.handle(entity.command_topic or "", "ON")

    ownership_result = routed.public_result()["direct_control_ownership"]
    assert ownership_result["diagnostic_reason"] == reason
    assert ownership.verified_power_on_calls == ["master"]


@pytest.mark.asyncio
async def test_emergency_conflict_reason_is_published_verbatim_not_from_exception_name(
) -> None:
    reason = "group_emergency_stop_blocks_direct_control"
    ownership = _OwnershipService(allowed=False, reason=reason)
    projection, coordinator, router = _setup(ownership_service=ownership)
    ownership.coordinator = coordinator
    entity = next(
        item
        for item in projection.entities
        if item.logical_member == "master" and item.function == "manual_flow"
    )
    message = SimpleNamespace(
        topic=entity.command_topic,
        payload=b"55",
        retain=False,
        dup=False,
    )
    client = _OneMessageClient(message)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=type("Poller", (), {"logical_members": ("master", "slave")})(),
    )

    await adapter._consume(client)  # type: ignore[arg-type]  # noqa: SLF001

    rejection = next(
        json.loads(payload)
        for topic, payload, _qos, _retain in client.published
        if topic == "jebao-flow/main/local/status"
    )
    assert rejection == {"reason": reason, "status": "rejected"}
    assert coordinator.scalar == []


@pytest.mark.asyncio
async def test_unresolved_group_close_error_escapes_the_mqtt_adapter(monkeypatch) -> None:
    projection, _coordinator, router = _setup()
    client = _RunClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    error = UnresolvedGroupSafetyStopError("main_flow")
    group_service = _GroupLifecycle(close_error=error)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_OneCyclePoller(()),  # type: ignore[arg-type]
        group_service=group_service,
    )
    stop_event = asyncio.Event()
    stop_event.set()

    with pytest.raises(UnresolvedGroupSafetyStopError):
        await adapter.run(stop_event)

    assert group_service.close_count == 1


@pytest.mark.asyncio
async def test_group_cycle_callback_failure_becomes_diagnostic_without_escaping() -> None:
    projection, _coordinator, router = _setup()

    class _FailingGroupLifecycle(_GroupLifecycle):
        async def observe_fresh_cycle(self, states) -> None:
            del states
            raise RuntimeError("injected group cycle failure")

    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_OneCyclePoller(()),  # type: ignore[arg-type]
        group_service=_FailingGroupLifecycle(),
    )
    client = _Client()

    await adapter._observe_group_cycle(client, ())  # type: ignore[arg-type]  # noqa: SLF001

    topic, payload, qos, retain = client.published[-1]
    assert topic == "jebao-flow/main/local/groups/status"
    assert json.loads(payload)["diagnostic_reason"] == "group_cycle_callback_failed"
    assert (qos, retain) == (1, True)


@pytest.mark.asyncio
async def test_unconstructable_group_does_not_block_existing_device_state_service(
    monkeypatch,
) -> None:
    projection, _coordinator, router = _setup()
    master = _binding("master", "1")
    slave = _binding("slave", "2")
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(master, slave),
        content=InstallContentBindings(
            app_catalog_sha256="f" * 64,
            protocol_schema_sha256="b" * 64,
        ),
    )
    config = AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/main"},
            "devices": [
                {
                    "id": member,
                    "name": member.title(),
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "address": f"192.0.2.{index}",
                    "identity": {
                        "device_id": f"synthetic-device-{index}",
                        "mac_address": f"0200000000{index:02x}",
                    },
                }
                for index, member in enumerate(("master", "slave"), start=1)
            ],
            "groups": [
                {
                    "id": "main_flow",
                    "name": "Main flow",
                    "members": [{"device": "master"}, {"device": "slave"}],
                    "failure_policy": {"on_member_offline": "fallback_constant"},
                }
            ],
        }
    )
    group_service = LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=build_local_control_catalog((), current_content=manifest.content),
        coordinator=LocalControlCoordinator.__new__(LocalControlCoordinator),
        operation_lock=asyncio.Lock(),
    )
    results = (
        FreshDeviceState(
            logical_member="master",
            available=False,
            diagnostic_reason="current_discovery_unavailable",
        ),
        FreshDeviceState(
            logical_member="slave",
            available=False,
            diagnostic_reason="current_discovery_unavailable",
        ),
    )
    client = _RunClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=_OneCyclePoller(results),  # type: ignore[arg-type]
        group_service=group_service,
    )

    await adapter.run(asyncio.Event())

    assert group_service.supervisor_count == 0
    assert (
        "jebao-flow/main/local/devices/master/availability",
        "offline",
        1,
        True,
    ) in client.published
    status_payload = next(
        json.loads(payload)
        for topic, payload, _qos, _retain in reversed(client.published)
        if topic == "jebao-flow/main/local/groups/status"
    )
    assert status_payload["groups"][0]["diagnostic_reason"] == (
        "fallback_constant_not_supported"
    )
