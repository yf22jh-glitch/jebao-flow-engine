import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow.config import AppConfig, MqttConfig
from jebao_flow.groups.models import GroupConfig, GroupMember, GroupState
from jebao_flow.groups.runtime import (
    DeliveryCertainty,
    GroupPlanSupersededError,
    PreWriteDispatchError,
    UnresolvedGroupSafetyStopError,
)
from jebao_flow.local_control import group_service as group_service_module
from jebao_flow.local_control import mqtt as mqtt_module
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.group_service import (
    GroupActualOutput,
    GroupCommandHeadline,
    LocalControlGroupCommandRejected,
    LocalControlGroupService,
)
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.mqtt import (
    LocalControlGroupCommandError,
    LocalControlGroupCommandRouter,
    LocalControlMqttAdapter,
)
from jebao_flow.local_control.state import FreshDeviceState
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO

_NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _group() -> GroupConfig:
    return GroupConfig(
        id="main_flow",
        name="Main Flow",
        members=(GroupMember(device="left"), GroupMember(device="right")),
    )


def _binding(member: str, index: int) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=f"{index:x}" * 64,
        logical_role=member,
        role_attested_by="operator",
        role_attested_at=_NOW,
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="a" * 64,
        template_sha256="c" * 64,
        schema_sha256="b" * 64,
        firmware_version="current",
        receipt_sha256=("d" * 64, "e" * 64),
    )


def _inputs(*, qualified: bool = True):
    group = _group()
    content = InstallContentBindings(
        app_catalog_sha256="a" * 64,
        protocol_schema_sha256="b" * 64,
        template_sha256=("c" * 64,),
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=_NOW,
        devices=(_binding("left", 1), _binding("right", 2)),
        content=content,
    )
    config = AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/test"},
            "devices": [
                {
                    "id": member,
                    "name": member.title(),
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "address": f"192.0.2.{index}",
                    "identity": {
                        "device_id": f"synthetic-{index}",
                        "mac_address": f"0200000000{index:02x}",
                    },
                    "limits": {"min_power": 30, "max_power": 75},
                }
                for index, member in enumerate(("left", "right"), start=1)
            ],
            "groups": [group.model_dump(mode="json")],
        }
    )
    qualifications = (
        (_qualification("power"), _qualification("manual_flow"))
        if qualified
        else ()
    )
    catalog = build_local_control_catalog(
        qualifications,
        current_content=content,
        _qualification_verifier_for_test=lambda _item: frozenset(
            binding.physical_identity_key for binding in manifest.devices
        ),
    )
    return config, manifest, catalog


def _fresh(*, right_available: bool = True):
    return (
        FreshDeviceState(logical_member="left", available=True, observed_at=_NOW),
        FreshDeviceState(
            logical_member="right",
            available=right_available,
            observed_at=_NOW if right_available else None,
            diagnostic_reason=None if right_available else "fresh_read_unavailable",
        ),
    )


def _fresh_power(left: bool, right: bool | None):
    return (
        FreshDeviceState(
            logical_member="left",
            available=True,
            observed_at=_NOW,
            observed_power=left,
        ),
        FreshDeviceState(
            logical_member="right",
            available=right is not None,
            observed_at=_NOW if right is not None else None,
            diagnostic_reason=None if right is not None else "fresh_read_unavailable",
            observed_power=right,
        ),
    )


class _DirectRouter:
    def __init__(self, command_topics=()) -> None:
        self.command_topics = tuple(command_topics)
        self.calls = []

    async def handle(self, topic, payload):
        self.calls.append((topic, payload))
        raise AssertionError("group commands must not enter the direct-device router")


class _Poller:
    logical_members = ("left", "right")


class _Client:
    def __init__(self, messages=()) -> None:
        self._messages = tuple(messages)
        self.published = []

    @property
    def messages(self):
        async def iterate():
            for message in self._messages:
                yield message

        return iterate()

    async def publish(self, topic, payload, *, qos, retain) -> None:
        self.published.append((topic, payload, qos, retain))


class _RunClient(_Client):
    def __init__(self, messages=()) -> None:
        super().__init__(messages)
        self.subscribed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    async def subscribe(self, topic, *, qos) -> None:
        self.subscribed.append((topic, qos))


class _RecordingActuator:
    instances: list["_RecordingActuator"] = []

    def __init__(self, *, group_id, member_ids, bindings, device_limits, coordinator) -> None:
        del bindings, device_limits, coordinator
        self.group_id = group_id
        self.member_ids = tuple(member_ids)
        self.plans = []
        self.write_plan_call_count = 0
        type(self).instances.append(self)

    def validate_plan(self, plan) -> None:
        assert plan.group_id == self.group_id

    async def write_plan(self, plan, *, guard, **kwargs) -> None:
        del kwargs
        self.write_plan_call_count += 1
        assert guard() is True
        self.plans.append(plan)


class _FailingActuator(_RecordingActuator):
    error_factory: Callable[[], Exception] = lambda: RuntimeError("injected failure")

    async def write_plan(self, plan, *, guard, **kwargs) -> None:
        del plan, kwargs
        self.write_plan_call_count += 1
        assert guard() is True
        raise type(self).error_factory()


class _DelayedActuator(_RecordingActuator):
    entered: asyncio.Event
    release: asyncio.Event

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        type(self).entered = asyncio.Event()
        type(self).release = asyncio.Event()

    async def write_plan(self, plan, *, guard, **kwargs) -> None:
        del kwargs
        self.write_plan_call_count += 1
        type(self).entered.set()
        await type(self).release.wait()
        if guard() is not True:
            raise GroupPlanSupersededError("injected supersede before frame")
        self.plans.append(plan)


def _service(
    monkeypatch,
    *,
    actuator_type=_RecordingActuator,
    qualified: bool = True,
    coordinator: bool = True,
    timeout: float = 0.25,
) -> LocalControlGroupService:
    config, manifest, catalog = _inputs(qualified=qualified)
    actuator_type.instances = []
    monkeypatch.setattr(
        group_service_module,
        "SoftwareIndependentGroupActuator",
        actuator_type,
    )
    return LocalControlGroupService(
        config=config,
        manifest=manifest,
        catalog=catalog,
        coordinator=(
            LocalControlCoordinator.__new__(LocalControlCoordinator)
            if coordinator
            else None
        ),
        operation_lock=asyncio.Lock(),
        command_completion_timeout_seconds=timeout,
    )


@pytest.mark.asyncio
async def test_verified_deduplicated_and_explicit_off_results_are_distinct(monkeypatch) -> None:
    service = _service(monkeypatch)
    await service.observe_fresh_cycle(_fresh())
    actuator = _RecordingActuator.instances[0]

    first_on = await service.apply_group_switch("main_flow", True)
    repeated_on = await service.apply_group_switch("main_flow", True)
    first_off = await service.apply_group_switch("main_flow", False)
    repeated_off = await service.apply_group_switch("main_flow", False)

    assert first_on.headline is GroupCommandHeadline.VERIFIED
    assert first_on.ownership_accepted
    assert first_on.applied_identity_matches
    assert first_on.latest_identity_matches
    assert not first_on.deduplicated
    assert repeated_on.headline is GroupCommandHeadline.DEDUPLICATED
    assert repeated_on.deduplicated
    assert repeated_on.applied_identity_matches
    assert first_off.headline is GroupCommandHeadline.VERIFIED
    assert repeated_off.headline is GroupCommandHeadline.VERIFIED
    assert not repeated_off.deduplicated
    assert actuator.write_plan_call_count == 3
    await service.aclose()


@pytest.mark.asyncio
async def test_completion_headline_uses_identity_not_unrelated_later_counters(
    monkeypatch,
) -> None:
    service = _service(monkeypatch)
    await service.observe_fresh_cycle(_fresh())
    supervisor = service._groups[0].supervisor  # noqa: SLF001
    assert supervisor is not None
    desired = supervisor.runtime.model_copy(
        update={"state": GroupState.RUNNING, "enabled": True}
    )
    cycle = await supervisor.apply_runtime_command(desired)
    final_dispatch = await supervisor.wait_idle()
    unrelated_activity = replace(
        final_dispatch,
        succeeded_count=final_dispatch.succeeded_count + 7,
        deduplicated_count=final_dispatch.deduplicated_count + 11,
        no_dispatch_count=final_dispatch.no_dispatch_count + 13,
    )

    result = group_service_module._group_command_result(  # noqa: SLF001
        group_id="main_flow",
        requested_on=True,
        cycle=cycle,
        final_dispatch=unrelated_activity,
        timed_out=True,
    )

    assert result.headline is GroupCommandHeadline.VERIFIED
    assert result.applied_identity_matches
    assert result.latest_identity_matches
    assert not result.deduplicated
    assert not result.completion_pending
    await service.aclose()


@pytest.mark.asyncio
async def test_on_without_fresh_availability_reports_awaiting_fresh_state(monkeypatch) -> None:
    service = _service(monkeypatch)

    result = await service.apply_group_switch("main_flow", True)

    assert result.headline is GroupCommandHeadline.AWAITING_FRESH_STATE
    assert result.ownership_accepted
    assert result.runtime_submit_called
    assert result.dispatch_decision_count == 0
    assert _RecordingActuator.instances[0].write_plan_call_count == 0
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "expected"),
    [
        pytest.param(
            lambda: PreWriteDispatchError("known not sent"),
            GroupCommandHeadline.NOT_SENT,
            id="not-sent",
        ),
        pytest.param(
            lambda: RuntimeError("delivery uncertain"),
            GroupCommandHeadline.UNCERTAIN,
            id="uncertain",
        ),
    ],
)
async def test_identity_matching_failure_has_authoritative_headline(
    monkeypatch,
    error_factory: Callable[[], Exception],
    expected: GroupCommandHeadline,
) -> None:
    _FailingActuator.error_factory = error_factory
    service = _service(monkeypatch, actuator_type=_FailingActuator)
    await service.observe_fresh_cycle(_fresh())

    result = await service.apply_group_switch("main_flow", True)

    assert result.headline is expected
    assert result.delivery_certainty is (
        DeliveryCertainty.NOT_SENT
        if expected is GroupCommandHeadline.NOT_SENT
        else DeliveryCertainty.UNCERTAIN
    )
    assert not result.applied_identity_matches
    await service.aclose()


@pytest.mark.asyncio
async def test_same_identity_stale_failure_is_reported_as_suppressed(monkeypatch) -> None:
    _FailingActuator.error_factory = lambda: RuntimeError("delivery uncertain")
    service = _service(monkeypatch, actuator_type=_FailingActuator)
    await service.observe_fresh_cycle(_fresh())
    first = await service.apply_group_switch("main_flow", True)

    repeated = await service.apply_group_switch("main_flow", True)

    assert first.headline is GroupCommandHeadline.UNCERTAIN
    assert repeated.headline is GroupCommandHeadline.SUPPRESSED_AFTER_FAILURE
    assert repeated.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert _FailingActuator.instances[0].write_plan_call_count == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_same_identity_unresolved_off_is_reported_as_deferred(monkeypatch) -> None:
    _FailingActuator.error_factory = lambda: RuntimeError("delivery uncertain")
    service = _service(monkeypatch, actuator_type=_FailingActuator)
    await service.observe_fresh_cycle(_fresh())
    first = await service.apply_group_switch("main_flow", False)

    repeated = await service.apply_group_switch("main_flow", False)

    assert first.headline is GroupCommandHeadline.UNCERTAIN
    assert repeated.headline is GroupCommandHeadline.DEFERRED_SAFETY_OFF
    assert repeated.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert _FailingActuator.instances[0].write_plan_call_count == 1
    with pytest.raises(UnresolvedGroupSafetyStopError):
        await service.aclose()


@pytest.mark.asyncio
async def test_bounded_timeout_reports_pending_without_cancelling_worker(monkeypatch) -> None:
    service = _service(monkeypatch, actuator_type=_DelayedActuator, timeout=0.01)
    await service.observe_fresh_cycle(_fresh())

    result = await service.apply_group_switch("main_flow", True)

    assert result.headline is GroupCommandHeadline.PENDING
    assert result.completion_pending
    assert _DelayedActuator.instances[0].write_plan_call_count == 1
    _DelayedActuator.release.set()
    supervisor = service._groups[0].supervisor  # noqa: SLF001
    assert supervisor is not None
    settled = await supervisor.wait_idle()
    assert settled.succeeded_count == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_wait_observer_does_not_cancel_group_worker(monkeypatch) -> None:
    service = _service(monkeypatch, actuator_type=_DelayedActuator, timeout=1.0)
    await service.observe_fresh_cycle(_fresh())
    task = asyncio.create_task(service.apply_group_switch("main_flow", True))
    await _DelayedActuator.entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    _DelayedActuator.release.set()
    supervisor = service._groups[0].supervisor  # noqa: SLF001
    assert supervisor is not None
    settled = await supervisor.wait_idle()

    assert settled.succeeded_count == 1
    assert _DelayedActuator.instances[0].write_plan_call_count == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_fresh_cycle_can_supersede_command_while_bounded_wait_is_outside_locks(
    monkeypatch,
) -> None:
    service = _service(monkeypatch, actuator_type=_DelayedActuator, timeout=1.0)
    await service.observe_fresh_cycle(_fresh())
    command = asyncio.create_task(service.apply_group_switch("main_flow", True))
    await _DelayedActuator.entered.wait()

    await service.observe_fresh_cycle(_fresh(right_available=False))
    _DelayedActuator.release.set()
    result = await command

    assert result.headline is GroupCommandHeadline.SUPERSEDED
    assert not result.applied_identity_matches
    assert not result.latest_identity_matches
    assert not result.completion_pending
    await service.aclose()


@pytest.mark.asyncio
async def test_unqualified_and_unconstructed_groups_reject_before_runtime_mutation(
    monkeypatch,
) -> None:
    unqualified = _service(monkeypatch, qualified=False)
    supervisor = unqualified._groups[0].supervisor  # noqa: SLF001
    assert supervisor is not None
    with pytest.raises(
        LocalControlGroupCommandRejected,
        match="physical_device_not_qualified",
    ):
        await unqualified.apply_group_switch("main_flow", True)
    assert not supervisor.armed
    assert _RecordingActuator.instances[0].write_plan_call_count == 0
    await unqualified.aclose()

    unconstructed = _service(monkeypatch, coordinator=False)
    with pytest.raises(
        LocalControlGroupCommandRejected,
        match="write_coordinator_unavailable",
    ):
        await unconstructed.apply_group_switch("main_flow", False)
    assert unconstructed.supervisor_count == 0


def _projection(config, manifest, catalog):
    return project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
        groups=config.groups,
    )


@pytest.mark.asyncio
async def test_group_switch_state_and_availability_use_only_fresh_device_truth(
    monkeypatch,
) -> None:
    config, manifest, catalog = _inputs()
    service = _service(monkeypatch)
    projection = _projection(config, manifest, catalog)
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/test"),
        projection=projection,
        router=_DirectRouter(),  # type: ignore[arg-type]
        state_poller=_Poller(),  # type: ignore[arg-type]
        group_service=service,
        group_router=group_router,
    )
    [control] = projection.group_control_entities
    client = _Client()

    await service.observe_fresh_cycle(_fresh_power(True, True))
    await adapter._publish_group_outputs(client)  # noqa: SLF001
    assert (control.state_topic, "ON", 1, True) in client.published
    assert (control.availability_topic, "online", 1, True) in client.published

    before = len(client.published)
    await service.observe_fresh_cycle(_fresh_power(True, False))
    await adapter._publish_group_outputs(client)  # noqa: SLF001
    mixed = client.published[before:]
    assert all(topic != control.state_topic for topic, *_rest in mixed)
    assert (control.availability_topic, "offline", 1, True) in mixed

    before = len(client.published)
    await service.observe_fresh_cycle(_fresh_power(False, None))
    await adapter._publish_group_outputs(client)  # noqa: SLF001
    unknown = client.published[before:]
    assert all(topic != control.state_topic for topic, *_rest in unknown)
    assert (control.availability_topic, "offline", 1, True) in unknown

    await service.observe_fresh_cycle(_fresh_power(False, False))
    await adapter._publish_group_outputs(client)  # noqa: SLF001
    assert (control.state_topic, "OFF", 1, True) in client.published
    assert service.group_output_states()[0].actual_output is GroupActualOutput.OFF
    await service.aclose()


@pytest.mark.asyncio
async def test_constructed_qualification_is_separate_from_switch_availability(
    monkeypatch,
) -> None:
    config, manifest, catalog = _inputs()
    service = _service(monkeypatch, coordinator=False)
    projection = _projection(config, manifest, catalog)
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/test"),
        projection=projection,
        router=_DirectRouter(),  # type: ignore[arg-type]
        state_poller=_Poller(),  # type: ignore[arg-type]
        group_service=service,
        group_router=group_router,
    )
    await service.observe_fresh_cycle(_fresh_power(True, True))
    output = service.group_output_states()[0]
    client = _Client()

    await adapter._publish_group_outputs(client)  # noqa: SLF001

    [control] = projection.group_control_entities
    assert output.command_qualified
    assert not output.supervisor_constructed
    assert output.command_diagnostic_reason == "write_coordinator_unavailable"
    assert not output.command_available
    assert (control.state_topic, "ON", 1, True) in client.published
    assert (control.availability_topic, "offline", 1, True) in client.published
    with pytest.raises(
        LocalControlGroupCommandError,
        match="write_coordinator_unavailable",
    ):
        await group_router.handle(control.command_topic, "OFF")


@pytest.mark.asyncio
async def test_group_consumer_bypasses_direct_router_and_never_publishes_optimistic_state(
    monkeypatch,
) -> None:
    config, manifest, catalog = _inputs()
    service = _service(monkeypatch)
    await service.observe_fresh_cycle(_fresh_power(False, False))
    projection = _projection(config, manifest, catalog)
    direct_router = _DirectRouter()
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/test"),
        projection=projection,
        router=direct_router,  # type: ignore[arg-type]
        state_poller=_Poller(),  # type: ignore[arg-type]
        group_service=service,
        group_router=group_router,
    )
    [control] = projection.group_control_entities
    message = SimpleNamespace(
        topic=control.command_topic,
        payload=b"ON",
        retain=False,
        dup=False,
    )
    client = _Client((message,))

    await adapter._consume(client)  # noqa: SLF001

    assert direct_router.calls == []
    assert _RecordingActuator.instances[0].write_plan_call_count == 1
    result_payload = next(
        json.loads(payload)
        for topic, payload, qos, retain in client.published
        if topic.endswith("/result") and qos == 1 and not retain
    )
    assert result_payload["headline"] == "verified"
    assert all(topic != control.state_topic for topic, *_rest in client.published)
    await service.aclose()


class _NeverCalledGroupService:
    configured_group_ids = ("main_flow",)
    qualified_group_ids = ("main_flow",)

    def __init__(self) -> None:
        self.calls = []

    async def apply_group_switch(self, group_id, requested_on):
        self.calls.append((group_id, requested_on))
        raise AssertionError("rejected command reached the group service")


class _BlockingCommandLifecycle:
    configured_group_ids = ("main_flow",)
    qualified_group_ids = ("main_flow",)

    def __init__(self) -> None:
        self.command_started = asyncio.Event()
        self.command_cancelled = asyncio.Event()
        self.close_count = 0

    async def apply_group_switch(self, group_id, requested_on):
        assert (group_id, requested_on) == ("main_flow", True)
        self.command_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.command_cancelled.set()
            raise

    async def observe_fresh_cycle(self, states) -> None:
        del states

    async def aclose(self) -> None:
        self.close_count += 1
        raise UnresolvedGroupSafetyStopError("main_flow")

    def public_dict(self):
        return {
            "schema_version": 1,
            "status": "group_lifecycle",
            "configured_group_count": 1,
            "supervisor_count": 1,
            "groups": [],
        }

    def group_output_states(self):
        return ()


class _BlockingPoller(_Poller):
    async def run(self, publish, stop_event, *, observe_cycle=None) -> None:
        del publish, observe_cycle
        await stop_event.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retain", "dup", "expected_reason"),
    [
        (True, False, "retained_command_rejected"),
        (False, True, "duplicate_command_rejected"),
    ],
)
async def test_retained_and_duplicate_group_commands_stop_before_the_service(
    retain: bool,
    dup: bool,
    expected_reason: str,
) -> None:
    config, manifest, catalog = _inputs()
    projection = _projection(config, manifest, catalog)
    service = _NeverCalledGroupService()
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,  # type: ignore[arg-type]
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/test"),
        projection=projection,
        router=_DirectRouter(),  # type: ignore[arg-type]
        state_poller=_Poller(),  # type: ignore[arg-type]
        group_router=group_router,
    )
    [control] = projection.group_control_entities
    client = _Client(
        (
            SimpleNamespace(
                topic=control.command_topic,
                payload=b"ON",
                retain=retain,
                dup=dup,
            ),
        )
    )

    await adapter._consume(client)  # noqa: SLF001

    assert service.calls == []
    assert json.loads(client.published[-1][1]) == {
        "reason": expected_reason,
        "status": "rejected",
    }


@pytest.mark.asyncio
async def test_invalid_payload_and_inventory_mismatch_stop_before_the_service() -> None:
    config, manifest, catalog = _inputs()
    projection = _projection(config, manifest, catalog)
    service = _NeverCalledGroupService()
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,  # type: ignore[arg-type]
    )
    [control] = projection.group_control_entities

    with pytest.raises(LocalControlGroupCommandError, match="group_command_payload_invalid"):
        await group_router.handle(control.command_topic, "MAYBE")
    assert service.calls == []

    service.qualified_group_ids = ()
    mismatched = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,  # type: ignore[arg-type]
    )
    with pytest.raises(LocalControlGroupCommandError, match="group_command_inventory_mismatch"):
        await mismatched.handle(control.command_topic, "ON")
    assert service.calls == []


def test_group_and_device_command_topics_must_have_exactly_one_owner() -> None:
    config, manifest, catalog = _inputs()
    projection = _projection(config, manifest, catalog)
    service = _NeverCalledGroupService()
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,  # type: ignore[arg-type]
    )
    [control] = projection.group_control_entities

    with pytest.raises(ValueError, match="must be disjoint"):
        LocalControlMqttAdapter(
            MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/test"),
            projection=projection,
            router=_DirectRouter((control.command_topic,)),  # type: ignore[arg-type]
            state_poller=_Poller(),  # type: ignore[arg-type]
            group_router=group_router,
        )


@pytest.mark.asyncio
async def test_group_router_does_not_take_the_shared_operation_lock(monkeypatch) -> None:
    config, manifest, catalog = _inputs()
    service = _service(monkeypatch, timeout=0.01)
    await service.observe_fresh_cycle(_fresh())
    projection = _projection(config, manifest, catalog)
    router = LocalControlGroupCommandRouter(projection=projection, service=service)
    [control] = projection.group_control_entities
    await service.operation_lock.acquire()

    routed = await asyncio.wait_for(
        router.handle(control.command_topic, "ON"),
        timeout=0.25,
    )

    assert routed.result.headline is GroupCommandHeadline.PENDING
    service.operation_lock.release()
    supervisor = service._groups[0].supervisor  # noqa: SLF001
    assert supervisor is not None
    await supervisor.wait_idle()
    await service.aclose()


@pytest.mark.asyncio
async def test_command_task_cancellation_cannot_swallow_sticky_group_close(
    monkeypatch,
) -> None:
    config, manifest, catalog = _inputs()
    projection = _projection(config, manifest, catalog)
    service = _BlockingCommandLifecycle()
    group_router = LocalControlGroupCommandRouter(
        projection=projection,
        service=service,  # type: ignore[arg-type]
    )
    [control] = projection.group_control_entities
    message = SimpleNamespace(
        topic=control.command_topic,
        payload=b"ON",
        retain=False,
        dup=False,
    )
    client = _RunClient((message,))
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/test"),
        projection=projection,
        router=_DirectRouter(),  # type: ignore[arg-type]
        state_poller=_BlockingPoller(),  # type: ignore[arg-type]
        group_service=service,  # type: ignore[arg-type]
        group_router=group_router,
    )
    stop_event = asyncio.Event()
    run_task = asyncio.create_task(adapter.run(stop_event))
    await asyncio.wait_for(service.command_started.wait(), timeout=0.5)

    stop_event.set()
    with pytest.raises(UnresolvedGroupSafetyStopError):
        await asyncio.wait_for(run_task, timeout=0.5)

    assert service.command_cancelled.is_set()
    assert service.close_count == 1
