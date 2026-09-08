import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from jebao_flow.config import MqttConfig
from jebao_flow.local_control import mqtt as mqtt_module
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlResult
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


def _setup():
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
    )
    coordinator = _Coordinator([], [])
    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(plan,),
        coordinator=coordinator,  # type: ignore[arg-type]
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
        await router.handle(entity.command_topic or "", "99")


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
        self.published: list[tuple[str, str, int, bool]] = []

    async def publish(self, topic, payload, *, qos, retain) -> None:
        self.published.append((str(topic), str(payload), qos, retain))


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
        self.messages = _IdleMessages()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def subscribe(self, topic, *, qos) -> None:
        self.subscribed.append((str(topic), qos))


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
