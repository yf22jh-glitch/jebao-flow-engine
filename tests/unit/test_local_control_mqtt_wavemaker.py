"""MQTT contracts over the real compiler/coordinator and byte-copy transport fixture.

These tests make no firmware-behavior or hardware-qualification claims.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.config import MqttConfig
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.ha import HomeAssistantPlatform, project_home_assistant
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.models import FreshInstallManifest, InstallContentBindings
from jebao_flow.local_control.mqtt import (
    LocalControlCommandConflictError,
    LocalControlMqttAdapter,
    LocalControlMqttError,
    LocalControlMqttRouter,
    RoutedSchedulePreview,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.state import FreshDeviceState, StatePublication
from jebao_flow.local_control.wavemaker import WavemakerSchedule
from tests.unit.test_local_control_coordinator import (
    _binding,
    _coordinator,
    _qualification,
    _Resolver,
    _Session,
    _SessionFactory,
    _status,
)
from tests.unit.test_local_control_mqtt import (
    _Client,
    _OneMessageClient,
    _OwnershipService,
    _RunClient,
)
from tests.unit.test_local_control_wavemaker import _slot


def _schedule(*, empty=False):
    return WavemakerSchedule(family="pro", slots=[] if empty else [_slot()])


def _request(operation="apply", *, request_id="schedule_mqtt_001", schedule=None):
    return json.dumps(
        {
            "operation": operation,
            "request_id": request_id,
            "schedule": (schedule or _schedule()).model_dump(mode="json"),
        }
    )


def _setup(tmp_path, *, statuses=None, qualified=True, enabled=True, ownership=None):
    import asyncio

    target = _binding("master", "a")
    other = _binding("slave", "b")
    content = InstallContentBindings(
        app_catalog_sha256="0" * 64,
        protocol_schema_sha256=current_protocol_schema_sha256(),
        template_sha256=("1" * 64,),
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 16, tzinfo=UTC),
        devices=(target, other),
        content=content,
    )
    qualifications = (_qualification("schedule"),) if qualified else ()
    keys = frozenset({target.physical_identity_key})
    catalog = build_local_control_catalog(
        qualifications,
        current_content=content,
        _qualification_verifier_for_test=lambda _item: keys,
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/main",
        control_surface_enabled=enabled,
    )
    resolver = _Resolver(
        (
            ResolvedEndpoint(
                target,
                "synthetic-address",
                wifi_firmware_version="wifi-current",
            ),
        )
    )
    session = _Session(
        statuses
        if statuses is not None
        else [
            _status(schedule=b"\xee" * 432),
            _status(schedule=_schedule().compile_image()),
        ]
    )
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=qualifications,
        resolver=resolver,
        sessions=_SessionFactory({"synthetic-address": [session]}),
        catalog_qualified_keys=keys,
        runtime_qualified_keys=keys,
    )
    lock = asyncio.Lock() if ownership is not None else None
    if ownership is not None:
        ownership.operation_lock = lock
    router = LocalControlMqttRouter(
        projection=projection,
        manifest=manifest,
        catalog=catalog,
        schedule_plans=(),
        coordinator=coordinator,
        operation_lock=lock,
        ownership_service=ownership,
    )
    return SimpleNamespace(
        projection=projection,
        router=router,
        coordinator=coordinator,
        journal=journal,
        resolver=resolver,
        session=session,
        topic="jebao-flow/main/local/devices/master/functions/schedule/request",
    )


def _adapter(setup):
    return LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=setup.projection,
        router=setup.router,
        state_poller=SimpleNamespace(logical_members=("master", "slave")),
    )


def test_only_qualified_physical_device_gets_schedule_api_not_a_fake_ha_control(tmp_path):
    setup = _setup(tmp_path)
    assert setup.router.command_topics == (setup.topic,)
    master, slave = [e for e in setup.projection.entities if e.function == "schedule"]
    assert master.schedule_request_topic == setup.topic
    assert master.public_dict()["schedule_request_topic"] == setup.topic
    assert master.platform is HomeAssistantPlatform.SENSOR
    assert not master.control_available
    assert "command_topic" not in master.mqtt_discovery_payload()
    assert slave.schedule_request_topic is None


@pytest.mark.parametrize("qualified,enabled", [(False, True), (True, False)])
async def test_unqualified_or_observer_has_no_schedule_route(tmp_path, qualified, enabled):
    setup = _setup(tmp_path, qualified=qualified, enabled=enabled)
    assert setup.router.command_topics == ()
    with pytest.raises(LocalControlMqttError):
        await setup.router.handle(setup.topic, _request())
    assert not setup.session.sent
    assert setup.resolver.calls == 0


async def test_preview_does_not_resolve_connect_journal_or_claim_group_ownership(tmp_path):
    ownership = _OwnershipService(
        allowed=False, reason="group_emergency_stop_blocks_direct_control"
    )
    setup = _setup(tmp_path, ownership=ownership)
    preview = await setup.router.handle(setup.topic, _request("preview"))
    assert isinstance(preview, RoutedSchedulePreview)
    assert preview.public_result()["image_sha256"] == _schedule().preview()["image_sha256"]
    assert preview.public_result()["hardware_write"] is False
    assert preview.public_result()["device_acceptance"] == "unverified"
    assert setup.resolver.calls == 0
    assert not setup.session.sent
    assert not ownership.calls
    assert setup.journal.load("schedule_mqtt_001") is None


async def test_apply_and_replay_use_real_journal_and_never_publish_requested_image(tmp_path):
    ownership = _OwnershipService()
    setup = _setup(tmp_path, ownership=ownership)
    first = await setup.router.handle(setup.topic, _request())
    record = setup.journal.load(first.request_id)
    assert first.result.accepted
    assert first.result.physical_write_count == 1
    assert record.phase is TransactionPhase.VERIFIED
    assert setup.journal.load_blob(record.actions[0].baseline_status_sha256) == _status(
        schedule=b"\xee" * 432,
    )
    assert first.state_topic is first.state_value is None
    replay = await setup.router.handle(setup.topic, _request())
    assert replay.result.accepted
    assert replay.result.physical_write_count == 0
    assert replay.state_topic is replay.state_value is None
    assert ownership.calls == ["master"]
    assert setup.resolver.calls == 1
    assert len(setup.session.sent) == 1
    with pytest.raises(LocalControlExecutionError, match="different intent"):
        await setup.router.handle(setup.topic, _request(schedule=_schedule(empty=True)))
    assert ownership.calls == ["master"]
    assert len(setup.session.sent) == 1


async def test_empty_schedule_replaces_all_slots_and_keeps_original_for_restore(tmp_path):
    original = _status(schedule=_schedule().compile_image())
    setup = _setup(tmp_path, statuses=[original, _status(schedule=b"\xee" * 432)])
    result = await setup.router.handle(setup.topic, _request(schedule=_schedule(empty=True)))
    assert result.result.accepted
    record = setup.journal.load(result.request_id)
    assert setup.journal.load_blob(record.actions[0].baseline_status_sha256) == original
    assert len(setup.session.sent) == 1


async def test_group_conflict_blocks_apply_before_journal_or_io(tmp_path):
    ownership = _OwnershipService(
        allowed=False, reason="group_emergency_stop_blocks_direct_control"
    )
    setup = _setup(tmp_path, ownership=ownership)
    with pytest.raises(LocalControlCommandConflictError, match="group_emergency_stop"):
        await setup.router.handle(setup.topic, _request())
    assert setup.journal.load("schedule_mqtt_001") is None
    assert setup.resolver.calls == 0
    assert not setup.session.sent


async def test_mismatched_readback_blocks_new_apply_without_retry_or_false_pass(tmp_path):
    original = _status(schedule=b"\xee" * 432)
    setup = _setup(tmp_path, statuses=[original, original])
    with pytest.raises(LocalControlExecutionError):
        await setup.router.handle(setup.topic, _request())
    record = setup.journal.load("schedule_mqtt_001")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    with pytest.raises(LocalControlExecutionError, match="unfinished durable state"):
        await setup.router.handle(setup.topic, _request())
    with pytest.raises(LocalControlExecutionError, match="terminal recovery"):
        await setup.router.handle(setup.topic, _request(request_id="schedule_mqtt_002"))
    preview = await setup.router.handle(setup.topic, _request("preview"))
    assert preview.public_result()["device_acceptance"] == "unverified"
    assert setup.journal.load("schedule_mqtt_001") == record
    assert setup.journal.load("schedule_mqtt_002") is None
    assert len(setup.session.sent) == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        "{}",
        "[]",
        "null",
        "{",
        "x" * 32769,
        '{"operation":"preview","operation":"apply"}',
        _request(request_id="../../bad"),
        _request(operation="restore"),
        _request(schedule=WavemakerSchedule(family="bar", slots=[])),
        _request().replace('"flow": 35', '"flow": 29'),
        _request().replace('"flow": 35', '"flow": true'),
        _request().replace('"flow": 35', '"flow": NaN'),
        _request().replace('"slots": [', '"extra": 1, "slots": ['),
    ],
)
async def test_invalid_schedule_is_rejected_before_ownership_and_io(tmp_path, payload):
    ownership = _OwnershipService()
    setup = _setup(tmp_path, ownership=ownership)
    with pytest.raises(LocalControlMqttError):
        await setup.router.handle(setup.topic, payload)
    assert not ownership.calls
    assert not setup.session.sent
    assert setup.resolver.calls == 0


@pytest.mark.parametrize("retain,dup", [(True, False), (False, True)])
async def test_adapter_rejects_retained_and_duplicate_schedule_commands(tmp_path, retain, dup):
    setup = _setup(tmp_path)
    client = _OneMessageClient(
        SimpleNamespace(
            topic=setup.topic,
            payload=_request().encode(),
            retain=retain,
            dup=dup,
        )
    )
    await _adapter(setup)._consume(client)
    assert not setup.session.sent
    assert setup.resolver.calls == 0
    assert json.loads(client.published[0][1])["reason"] == (
        "retained_command_rejected" if retain else "duplicate_command_rejected"
    )


@pytest.mark.parametrize("operation", ["preview", "apply"])
async def test_adapter_publishes_correlated_nonretained_result_only(tmp_path, operation):
    setup = _setup(tmp_path)
    client = _OneMessageClient(
        SimpleNamespace(
            topic=setup.topic,
            payload=_request(operation).encode(),
            retain=False,
            dup=False,
        )
    )
    await _adapter(setup)._consume(client)
    [(topic, payload, qos, retained)] = client.published
    assert topic == "jebao-flow/main/local/requests/schedule_mqtt_001/result"
    assert json.loads(payload)["request_id"] == "schedule_mqtt_001"
    assert qos == 1 and not retained
    assert len(setup.session.sent) == (1 if operation == "apply" else 0)


async def test_fresh_schedule_diagnostics_preserve_observations_and_api_topic(tmp_path):
    setup = _setup(tmp_path)
    client = _Client()
    state_topic = setup.topic.removesuffix("/request") + "/state"
    observed = {"active_slot_count": 1, "entries": [{"slot": 0, "mode": "sine"}]}
    state = FreshDeviceState(
        logical_member="master",
        available=True,
        observed_at=datetime(2026, 9, 16, tzinfo=UTC),
        publications=(
            StatePublication(
                state_topic,
                json.dumps(
                    {
                        "stage": "qualified",
                        "observed": observed,
                    }
                ),
            ),
        ),
    )
    await _adapter(setup)._publish_fresh_state(client, state)
    payload = json.loads(client.published[0][1])
    assert payload["observed"] == observed
    assert payload["schedule_request_topic"] == setup.topic


async def test_startup_subscribes_schedule_qos_zero_and_advertises_api(tmp_path, monkeypatch):
    import asyncio

    from jebao_flow.local_control import mqtt as mqtt_module

    setup = _setup(tmp_path)
    client = _RunClient()
    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", lambda *args, **kwargs: client)

    class Poller:
        logical_members = ("master", "slave")

        async def run(self, publish, stop_event):
            await stop_event.wait()

    adapter = LocalControlMqttAdapter(
        MqttConfig(host="broker.invalid", topic_prefix="jebao-flow/main"),
        projection=setup.projection,
        router=setup.router,
        state_poller=Poller(),
    )
    stop = asyncio.Event()
    stop.set()
    await adapter.run(stop)
    assert client.subscribed == [(setup.topic, 0)]
    state_topic = setup.topic.removesuffix("/request") + "/state"
    [diagnostic] = [json.loads(p) for t, p, _, _ in client.published if t == state_topic]
    assert diagnostic["schedule_request_topic"] == setup.topic


@pytest.mark.parametrize(
    "write_enabled,dry_run,master_allowed,slave_allowed,error",
    [
        (False, False, True, True, "--write-enabled"),
        (True, True, True, True, "runtime.dry_run"),
        (True, False, False, True, "write-locked"),
        (True, False, True, False, None),
    ],
)
def test_schedule_only_api_keeps_runtime_and_member_write_locks(
    tmp_path, write_enabled, dry_run, master_allowed, slave_allowed, error
):
    setup = _setup(tmp_path)
    assert setup.projection.public_dict()["control_count"] == 0
    assert setup.router.command_topics == (setup.topic,)
    args = SimpleNamespace(write_enabled=write_enabled)
    config = SimpleNamespace(
        runtime=SimpleNamespace(dry_run=dry_run),
        devices=[
            SimpleNamespace(id=member, control=SimpleNamespace(allow_hardware_writes=allowed))
            for member, allowed in [("master", master_allowed), ("slave", slave_allowed)]
        ],
    )
    if error is not None:
        with pytest.raises(ValueError, match=error):
            local_control_cli._require_live_opt_in(args, config, setup.projection)
    else:
        local_control_cli._require_live_opt_in(args, config, setup.projection)
