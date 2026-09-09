import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

import jebao_flow.local_control.state as state_module
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.local_control.state import (
    FreshDeviceState,
    FreshStatePoller,
    LocalControlStateProjector,
)
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _binding(member: str = "slave", marker: str = "a") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=marker * 64,
        logical_role=member,
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="b" * 64,
        template_sha256="c" * 64,
        schema_sha256="d" * 64,
        firmware_version="current",
        receipt_sha256=("e" * 64, "f" * 64),
    )


def _projection_setup():
    master = _binding("master", "1")
    slave = _binding("slave", "2")
    content = InstallContentBindings(
        app_catalog_sha256="b" * 64,
        protocol_schema_sha256="d" * 64,
        template_sha256=("c" * 64,),
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
            _qualification("manual_mode"),
            _qualification("slave_per_slot_flow_extension"),
        ),
        current_content=content,
        _qualification_verifier_for_test=lambda _item: frozenset(
            {master.physical_identity_key, slave.physical_identity_key}
        ),
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
        topic_prefix="jebao-flow/test",
        schedule_plans=(plan,),
    )
    return manifest, catalog, projection


def _status(*, flow: int = 55, slot_flow: int = 47) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = 0b00000011
    raw[1] = 6
    raw[2] = flow
    raw[6] = 6
    raw[11:443] = bytes([0xEE]) * 432
    raw[11:20] = bytes((0, 0, 1, 0, 6, slot_flow, 20, 0, 10))
    return bytes(raw)


def test_projector_publishes_public_enum_and_each_current_slave_slot_truth() -> None:
    manifest, catalog, projection = _projection_setup()
    observed_at = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
    projector = LocalControlStateProjector(
        manifest=manifest,
        projection=projection,
        catalog=catalog,
    )

    publications = projector.project(
        manifest.devices[1],
        _status(),
        observed_at=observed_at,
    )
    by_topic = {item.topic: item.payload for item in publications}
    manual_mode = next(
        item
        for item in projection.entities
        if item.logical_member == "slave" and item.function == "manual_mode"
    )
    manual_flow = next(
        item
        for item in projection.entities
        if item.logical_member == "slave" and item.function == "manual_flow"
    )
    slots = sorted(
        (item for item in projection.entities if item.slot_index is not None),
        key=lambda item: item.slot_index or 0,
    )

    assert by_topic[manual_mode.state_topic] == "Cross-flow"
    assert by_topic[manual_flow.state_topic] == "55"
    assert by_topic[slots[0].state_topic] == "47"
    assert by_topic[slots[1].state_topic] == ""
    diagnostic = next(
        item
        for item in projection.entities
        if item.logical_member == "slave" and item.function == "current_schedule_state"
    )
    payload = json.loads(by_topic[diagnostic.state_topic])
    assert payload["stage"] == "app_static"
    assert payload["observed_at"] == observed_at.isoformat()
    assert payload["observed"]["AutoFlow"] == 0
    assert payload["observed"]["AutoMode"] == "Cross-flow"
    schedule = next(
        item
        for item in projection.entities
        if item.logical_member == "slave" and item.function == "schedule"
    )
    schedule_payload = json.loads(by_topic[schedule.state_topic])
    assert schedule_payload["observed"]["entries"][0]["mode"] == "Cross-flow"
    assert all(item.payload.isascii() for item in publications)


class _Resolver:
    def __init__(self, endpoints: tuple[ResolvedEndpoint, ...], *, fail: bool = False) -> None:
        self.endpoints = endpoints
        self.fail = fail
        self.calls = 0

    async def resolve_many(self, targets):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no current observation")
        assert tuple(endpoint.target for endpoint in self.endpoints) == targets
        return self.endpoints


class _Session:
    def __init__(self, address: str, status: bytes, events: list[str]) -> None:
        self.address = address
        self.status = status
        self.events = events

    async def connect(self) -> None:
        self.events.append("connect")

    async def authenticate(self) -> bytes:
        self.events.append("authenticate")
        return b"private"

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
        assert accept_reports is True
        self.events.append("read_once")
        return self.status

    async def disconnect(self) -> None:
        self.events.append("disconnect")

    def quarantine(self) -> None:
        self.events.append("quarantine")


class _ReadLease:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @contextmanager
    def acquire(self, physical_identity_key: str):
        assert len(physical_identity_key) == 64
        self.events.append("shared_lock")
        try:
            yield
        finally:
            self.events.append("shared_unlock")


@pytest.mark.asyncio
async def test_poller_resolves_now_reads_once_and_closes_without_a_write_session() -> None:
    manifest, catalog, _projection = _projection_setup()
    target = manifest.devices[0]
    single_manifest = manifest.model_copy(update={"devices": (target,)})
    single_projection = project_home_assistant(
        single_manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
    )
    endpoint = ResolvedEndpoint(target, "runtime-address")
    events: list[str] = []
    poller = FreshStatePoller(
        manifest=single_manifest,
        resolver=_Resolver((endpoint,)),  # type: ignore[arg-type]
        projector=LocalControlStateProjector(
            manifest=single_manifest,
            projection=single_projection,
            catalog=catalog,
        ),
        poll_interval_seconds=5,
        reconnect_initial_seconds=2,
        reconnect_max_seconds=60,
        read_lease=_ReadLease(events),
        session_factory=lambda address: _Session(address, _status(), events),
        now=lambda: datetime(2026, 9, 8, 6, 1, tzinfo=UTC),
    )

    [result] = await poller.poll_once()

    assert result.available is True
    assert result.observed_power is True
    assert events == [
        "shared_lock",
        "connect",
        "authenticate",
        "read_once",
        "disconnect",
        "shared_unlock",
    ]
    assert not hasattr(_Session("address", b"", []), "send_raw_control")


def test_unqualified_projection_keeps_typed_power_with_one_status_decode(monkeypatch) -> None:
    manifest, _catalog, _projection = _projection_setup()
    target = manifest.devices[0]
    single_manifest = manifest.model_copy(update={"devices": (target,)})
    unqualified_catalog = build_local_control_catalog(
        (),
        current_content=single_manifest.content,
    )
    projection = project_home_assistant(
        single_manifest,
        unqualified_catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
    )
    real_schema = state_module.get_product_schema(target.product_key)
    decode_calls = 0

    class _CountingSchema:
        def decode_status(self, raw_status: bytes):
            nonlocal decode_calls
            decode_calls += 1
            return real_schema.decode_status(raw_status)

    monkeypatch.setattr(state_module, "get_product_schema", lambda _key: _CountingSchema())
    projector = LocalControlStateProjector(
        manifest=single_manifest,
        projection=projection,
        catalog=unqualified_catalog,
    )

    projected = projector.project_fresh_state(
        target,
        _status(),
        observed_at=datetime(2026, 9, 8, 6, 2, tzinfo=UTC),
    )

    power_entity = next(
        entity
        for entity in projection.entities
        if entity.logical_member == target.logical_member and entity.function == "power"
    )
    assert power_entity.control_available is False
    assert projected.observed_power is True
    assert decode_calls == 1


@pytest.mark.asyncio
async def test_failed_current_discovery_publishes_no_stale_values() -> None:
    manifest, catalog, projection = _projection_setup()
    resolver = _Resolver((), fail=True)
    poller = FreshStatePoller(
        manifest=manifest,
        resolver=resolver,  # type: ignore[arg-type]
        projector=LocalControlStateProjector(
            manifest=manifest,
            projection=projection,
            catalog=catalog,
        ),
        poll_interval_seconds=5,
        reconnect_initial_seconds=2,
        reconnect_max_seconds=60,
        read_lease=_ReadLease([]),
    )

    results = await poller.poll_once()

    assert resolver.calls == 1
    assert all(result.available is False for result in results)
    assert all(result.publications == () for result in results)
    assert all(result.diagnostic_reason == "current_discovery_unavailable" for result in results)


@pytest.mark.asyncio
async def test_resolver_output_must_keep_the_exact_current_stable_binding() -> None:
    manifest, catalog, projection = _projection_setup()
    wrong = manifest.devices[0].model_copy(
        update={"physical_identity_key": "f" * 64}
    )

    class _WrongResolver:
        async def resolve_many(self, targets):
            return (
                ResolvedEndpoint(wrong, "runtime-address"),
                ResolvedEndpoint(targets[1], "runtime-address"),
            )

    poller = FreshStatePoller(
        manifest=manifest,
        resolver=_WrongResolver(),  # type: ignore[arg-type]
        projector=LocalControlStateProjector(
            manifest=manifest,
            projection=projection,
            catalog=catalog,
        ),
        poll_interval_seconds=5,
        reconnect_initial_seconds=2,
        reconnect_max_seconds=60,
        read_lease=_ReadLease([]),
    )

    results = await poller.poll_once()

    assert all(result.available is False for result in results)
    assert all(
        result.diagnostic_reason == "current_resolution_binding_mismatch"
        for result in results
    )


@pytest.mark.asyncio
async def test_run_delivers_one_complete_cycle_after_all_device_publications(
    monkeypatch,
) -> None:
    manifest, catalog, projection = _projection_setup()
    poller = FreshStatePoller(
        manifest=manifest,
        resolver=_Resolver((), fail=True),
        projector=LocalControlStateProjector(
            manifest=manifest,
            projection=projection,
            catalog=catalog,
        ),
        poll_interval_seconds=5,
        reconnect_initial_seconds=2,
        reconnect_max_seconds=60,
        read_lease=_ReadLease([]),
    )
    results = tuple(
        FreshDeviceState(
            logical_member=binding.logical_member,
            available=False,
            diagnostic_reason="current_discovery_unavailable",
        )
        for binding in manifest.devices
    )
    events = []
    stop_event = asyncio.Event()

    async def poll_once():
        return results

    async def publish(result):
        events.append(("publish", result.logical_member))

    async def observe_cycle(cycle):
        events.append(("cycle", cycle))
        stop_event.set()

    monkeypatch.setattr(poller, "poll_once", poll_once)

    await poller.run(publish, stop_event, observe_cycle=observe_cycle)

    assert events == [
        ("publish", "master"),
        ("publish", "slave"),
        ("cycle", results),
    ]
