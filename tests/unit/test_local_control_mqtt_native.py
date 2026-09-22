"""Native MQTT wiring over the existing durable controller and byte-copy transport.

No fake firmware propagation, physical output or new qualification is asserted here.
"""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.config import MqttConfig
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import ActionPhase
from jebao_flow.local_control.models import FreshInstallManifest, InstallContentBindings
from jebao_flow.local_control.mqtt import (
    LocalControlCommandConflictError,
    LocalControlMqttAdapter,
    LocalControlMqttError,
    LocalControlMqttRouter,
)
from jebao_flow.local_control.state import FreshDeviceState, StatePublication
from tests.unit.test_local_control_mqtt import _Client, _OneMessageClient, _OwnershipService
from tests.unit.test_local_control_wavemaker_lifecycle import STAGED_ASYNC_ROLE, Rig, _plan


def _setup(
    rig,
    *,
    native=True,
    control=True,
    qualified=True,
    targets=None,
    ownership=None,
    previews=False,
):
    content = InstallContentBindings(
        app_catalog_sha256="0" * 64,
        protocol_schema_sha256=current_protocol_schema_sha256(),
        template_sha256=("1" * 64,),
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 16, tzinfo=UTC),
        devices=targets or rig.targets,
        content=content,
    )
    catalog = rig.catalog if qualified else build_local_control_catalog()
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        native_lifecycle_enabled=native,
        control_surface_enabled=control,
        preview_requests_enabled=previews,
    )
    lock = asyncio.Lock()
    if ownership is not None:
        ownership.operation_lock = lock
    controller = rig.controller()
    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(),
        coordinator=controller,
        native_controller=controller,
        operation_lock=lock,
        ownership_service=ownership,
    )
    return projection, router


def _request(operation, *, request_id="mqtt_native_0001", **values):
    if operation in {"attach", "preview"}:
        values.setdefault("plan", _plan().model_dump(mode="json"))
    return json.dumps({"operation": operation, "request_id": request_id, **values})


def _topic(projection):
    assert projection.native_request_topic is not None
    return projection.native_request_topic


@pytest.mark.parametrize("native,control", [(False, True), (True, False)])
async def test_native_topic_requires_explicit_control_runtime(tmp_path, native, control):
    rig = Rig(tmp_path)
    projection, router = _setup(rig, native=native, control=control)
    assert projection.native_request_topic is None
    assert projection.public_dict()["native_wavemaker_api"] is None
    with pytest.raises(LocalControlMqttError):
        await router.handle("jebao-flow/main/local/wavemakers/request", _request("attach"))
    assert not rig.sent


async def test_native_preview_is_write_free_and_works_without_attach_qualification(tmp_path):
    rig = Rig(tmp_path, qualified=False)
    ownership = _OwnershipService(
        allowed=False, reason="group_emergency_stop_blocks_direct_control"
    )
    projection, router = _setup(rig, qualified=False, ownership=ownership)
    assert projection.native_attach_members == ()
    result = await router.handle(_topic(projection), _request("preview"))
    assert result.result["hardware_write"] is False
    assert result.result["images"] == _plan().preview()["images"]
    assert rig.reads == rig.sent == ownership.calls == []
    assert rig.store.load("mqtt_native_0001") is None
    with pytest.raises(LocalControlMqttError, match="qualifications"):
        await router.handle(_topic(projection), _request("attach"))
    assert not ownership.calls


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_native_attach_status_replay_detach_keep_existing_controller_contract(tmp_path, mode):
    rig = Rig(tmp_path)
    ownership = _OwnershipService()
    projection, router = _setup(rig, ownership=ownership)
    command = _request("attach", plan=_plan(mode).model_dump(mode="json"))
    first = await router.handle(_topic(projection), command)
    assert first.result["accepted"]
    assert not first.failed
    assert ownership.calls == ["master", "slave"]
    count, reads = len(rig.sent), len(rig.reads)
    replay = await router.handle(_topic(projection), command)
    assert replay.result["current_state_verified"] is False
    assert len(rig.sent) == count and len(rig.reads) == reads
    assert ownership.calls == ["master", "slave"]
    status = await router.handle(
        _topic(projection),
        _request(
            "status",
            request_id="status_query_001",
            record_id="mqtt_native_0001",
        ),
    )
    assert status.request_id == "status_query_001"
    assert status.result["request_id"] == "mqtt_native_0001"
    assert status.result["current_state_verified"] is False
    assert len(rig.sent) == count and len(rig.reads) == reads
    # Own restoration remains available without new attach qualification.
    projection, router = _setup(rig, qualified=False, ownership=ownership)
    detach = await router.handle(
        _topic(projection),
        _request(
            "detach",
            request_id="detach_query_001",
            attachment_id="mqtt_native_0001",
        ),
    )
    assert detach.result["accepted"]
    assert rig.states == rig.originals
    count = len(rig.sent)
    again = await router.handle(
        _topic(projection),
        _request(
            "detach",
            request_id="detach_query_002",
            attachment_id="mqtt_native_0001",
        ),
    )
    assert again.result["current_state_verified"] is False
    assert len(rig.sent) == count
    assert ownership.calls == ["master", "slave"]


async def test_native_recover_uses_owned_detach_not_generic_recovery(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach(request_id="native_existing")
    projection, router = _setup(rig, qualified=False)
    result = await router.handle(
        _topic(projection),
        _request(
            "recover",
            record_id="native_existing",
        ),
    )
    assert not result.failed and result.result["accepted"]
    assert rig.states == rig.originals


async def test_native_observation_pause_is_not_automatically_restored_or_retried(tmp_path):
    rig = Rig(tmp_path)
    projection, router = _setup(rig)
    command = _request(
        "attach",
        plan=_plan("async").model_dump(mode="json"),
        await_operator_observation=True,
    )
    first = await router.handle(_topic(projection), command)
    assert first.result["awaiting_operator_observation"]
    assert not first.result["accepted"]
    assert first.result["automatic_restore"] is False
    source = rig.store.load("mqtt_native_0001")
    writes, reads = len(rig.sent), len(rig.reads)
    replay = await router.handle(_topic(projection), command)
    assert not replay.result["current_state_verified"]
    assert (len(rig.sent), len(rig.reads)) == (writes, reads)
    assert rig.store.load("mqtt_native_0001") == source
    blocked = await router.handle(
        _topic(projection), _request("attach", request_id="unrelated_attach_01")
    )
    assert blocked.failed
    assert len(rig.sent) == writes
    restored = await router.handle(
        _topic(projection), _request("recover", record_id="mqtt_native_0001")
    )
    assert restored.result["accepted"] and rig.states == rig.originals


async def test_native_master_cycle_closes_the_source_without_claiming_delivery(tmp_path):
    from tests.unit.test_local_control_wavemaker_master_cycle import lost_reply

    rig = Rig(tmp_path)
    lost_reply(rig)
    projection, router = _setup(rig)
    result = await router.handle(
        _topic(projection),
        _request(
            "attach",
            plan=_plan("async").model_dump(mode="json"),
            master_cycle_on_async_failure=True,
        ),
    )
    assert result.result["kind"] == "master_link_cycle_v1"
    assert result.result["original_async_delivery"] == "UNKNOWN"
    source = rig.store.load("mqtt_native_0001")
    # The cycle's own released explicit reply closes the stalled role action, so the pair is
    # released for ordinary commands. The lost ACK is still recorded: delivery stays UNKNOWN.
    assert source.phase.value == "verified"
    assert source.actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    assert source.actions[STAGED_ASYNC_ROLE].phase is ActionPhase.VERIFIED
    writes, reads = len(rig.sent), len(rig.reads)
    replay = await router.handle(
        _topic(projection), _request("cycle_master", record_id="mqtt_native_0001")
    )
    assert not replay.result["current_state_verified"]
    assert (len(rig.sent), len(rig.reads)) == (writes, reads)
    assert rig.store.load("mqtt_native_0001") == source


async def test_native_routes_owned_master_power_cycle_without_touching_slave(tmp_path):
    from tests.unit.test_local_control_wavemaker_master_cycle import attach, lost_reply

    rig = Rig(tmp_path)
    lost_reply(rig)
    await attach(rig, await_operator_observation=True)
    writes = len(rig.sent)
    projection, router = _setup(rig)
    assert "cycle_master_power" in projection.native_wavemaker_api["operations"]

    result = await router.handle(
        _topic(projection),
        _request("cycle_master_power", record_id="cycle_source_01"),
    )
    assert not result.failed and result.result["accepted"]
    assert [(row[0], row[1]) for row in rig.sent[writes:]] == [
        ("master", {"SwitchON": False}),
        ("master", {"SwitchON": True}),
    ]


async def test_native_routes_image_free_async_slave_timer_rearm(tmp_path):
    from jebao_flow.local_control.wavemaker_slave_rearm import KIND
    from tests.unit.test_local_control_wavemaker_lifecycle import _put
    from tests.unit.test_local_control_wavemaker_master_cycle import attach, lost_reply

    rig = Rig(tmp_path)
    lost_reply(rig)
    await attach(rig, await_operator_observation=True)
    rig.states["slave"] = _put(
        rig.states["slave"],
        {
            "SwitchON": True,
            "TimerON": False,
            "Linkage": "async_slave",
            "Mode": "feed",
        },
    )
    writes = len(rig.sent)
    projection, router = _setup(rig)
    assert "rearm_async_slave_schedule" in projection.native_wavemaker_api["operations"]

    result = await router.handle(
        _topic(projection),
        _request("rearm_async_slave_schedule", record_id="cycle_source_01"),
    )

    assert not result.failed and result.result["accepted"]
    assert result.result["kind"] == KIND
    assert len(rig.sent) == writes + 5


def test_native_api_advertises_split_detach_assistance(tmp_path):
    projection, _router = _setup(Rig(tmp_path))
    assert "assist_detach_split" in projection.native_wavemaker_api["operations"]


async def test_native_group_conflict_never_reaches_controller(tmp_path):
    rig = Rig(tmp_path)
    ownership = _OwnershipService(
        allowed=False, reason="group_emergency_stop_blocks_direct_control"
    )
    projection, router = _setup(rig, ownership=ownership)
    with pytest.raises(LocalControlCommandConflictError):
        await router.handle(_topic(projection), _request("attach"))
    assert not rig.reads and not rig.sent
    assert rig.store.load("mqtt_native_0001") is None


@pytest.mark.parametrize(
    "operation",
    [
        "status",
        "detach",
        "recover",
        "cycle_master_power",
        "rearm_async_slave_schedule",
    ],
)
async def test_native_record_requires_current_physical_binding(tmp_path, operation):
    rig = Rig(tmp_path)
    await rig.attach(request_id="native_existing")
    count, reads = len(rig.sent), len(rig.reads)
    targets = (
        rig.targets[0].model_copy(update={"physical_identity_key": "c" * 64}),
        rig.targets[1],
    )
    projection, router = _setup(rig, targets=targets)
    field = "attachment_id" if operation == "detach" else "record_id"
    with pytest.raises(LocalControlMqttError, match="stable bindings"):
        await router.handle(_topic(projection), _request(operation, **{field: "native_existing"}))
    assert len(rig.sent) == count and len(rig.reads) == reads


async def test_native_failure_returns_durable_diagnostics_without_exception_or_false_success(
    tmp_path,
):
    rig = Rig(tmp_path)
    rig.fail_ack = 1
    projection, router = _setup(rig)
    result = await router.handle(_topic(projection), _request("attach"))
    assert result.failed
    payload = result.public_result()
    assert payload["status"] == "failed"
    assert "synthetic ACK" not in json.dumps(payload)
    assert result.result["actions"]
    assert rig.states == rig.originals


async def test_native_conflicting_request_keeps_old_result_historical_and_marks_failure(tmp_path):
    rig = Rig(tmp_path)
    ownership = _OwnershipService()
    projection, router = _setup(rig, ownership=ownership)
    await router.handle(_topic(projection), _request("attach"))
    count = len(rig.sent)
    result = await router.handle(
        _topic(projection),
        _request(
            "attach",
            plan=_plan("async").model_dump(mode="json"),
        ),
    )
    assert result.failed
    assert result.result["current_state_verified"] is False
    assert len(rig.sent) == count and ownership.calls == ["master", "slave"]


@pytest.mark.parametrize(
    "extra",
    [
        {"master_cycle_on_async_failure": True},
        {"await_operator_observation": True},
        {"await_operator_observation": "yes"},
        {"unknown": True},
        {"request_id": "../escape"},
    ],
)
async def test_native_invalid_options_reject_before_ownership(tmp_path, extra):
    rig = Rig(tmp_path)
    ownership = _OwnershipService()
    projection, router = _setup(rig, ownership=ownership)
    with pytest.raises(LocalControlMqttError):
        await router.handle(_topic(projection), _request("attach", **extra))
    assert rig.sent == rig.reads == ownership.calls == []


@pytest.mark.parametrize("retain,dup", [(True, False), (False, True), (False, False)])
async def test_native_adapter_no_replay_and_no_optimistic_state(tmp_path, retain, dup):
    rig = Rig(tmp_path)
    projection, router = _setup(rig)
    client = _OneMessageClient(
        SimpleNamespace(
            topic=_topic(projection),
            payload=_request("attach"),
            retain=retain,
            dup=dup,
        )
    )
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=SimpleNamespace(logical_members=("master", "slave")),
    )
    await adapter._consume(client)
    assert all(not retained for _, _, _, retained in client.published)
    if retain or dup:
        assert rig.sent == rig.reads == []
    else:
        [(topic, payload, _, _)] = client.published
        assert topic.endswith("/requests/mqtt_native_0001/result")
        assert json.loads(payload)["result"]["accepted"]


async def test_fresh_diagnostics_preserve_observed_truth_and_all_api_addresses(tmp_path):
    rig = Rig(tmp_path)
    projection, router = _setup(rig, previews=True)
    client = _Client()
    entity = next(
        entity
        for entity in projection.entities
        if entity.logical_member == "master" and entity.function == "schedule"
    )
    observed = {"active_slot_count": 0, "entries": []}
    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=projection,
        router=router,
        state_poller=SimpleNamespace(logical_members=("master", "slave")),
    )
    await adapter._publish_fresh_state(
        client,
        FreshDeviceState(
            logical_member="master",
            available=True,
            observed_at=datetime(2026, 9, 16, tzinfo=UTC),
            publications=(
                StatePublication(entity.state_topic, json.dumps({"observed": observed})),
            ),
        ),
    )
    payload = json.loads(client.published[0][1])
    assert payload["observed"] == observed
    assert payload["native_wavemaker_api"] == projection.native_wavemaker_api
    assert payload["preview_request_topic"] == entity.preview_request_topic
    assert payload["schedule_request_topic"] == entity.schedule_request_topic
    assert rig.reads == rig.sent == []


@pytest.mark.parametrize(
    "dry_run,allow,write_enabled",
    [
        (True, True, True),
        (False, False, True),
        (False, True, False),
    ],
)
def test_native_recovery_surface_without_qualifications_still_requires_write_opt_in(
    tmp_path,
    dry_run,
    allow,
    write_enabled,
):
    rig = Rig(tmp_path, qualified=False)
    projection, _ = _setup(rig, qualified=False)
    assert projection.public_dict()["control_count"] == 0
    config = SimpleNamespace(
        runtime=SimpleNamespace(dry_run=dry_run),
        devices=[
            SimpleNamespace(id=member, control=SimpleNamespace(allow_hardware_writes=allow))
            for member in ("master", "slave")
        ],
    )
    with pytest.raises(ValueError):
        local_control_cli._require_live_opt_in(
            SimpleNamespace(write_enabled=write_enabled),
            config,
            projection,
        )
