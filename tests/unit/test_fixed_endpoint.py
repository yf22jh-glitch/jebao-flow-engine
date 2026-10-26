"""Real UDP/decoder latency regression; no device control or firmware guesses.

Wire format and fast fixed-address replies: docs/runs/2026-10-26-fixed-ip-identity-baseline.md.
"""

import asyncio
import hashlib
import struct
import time
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_external_manual_cli as cli
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRODUCT_KEY
from tests.unit.test_local_control_resolver import _device, _target


def packet(device):
    def pref(value):
        return struct.pack(">H", len(value)) + value

    payload = (
        pref(device.device_id.encode())
        + pref(bytes.fromhex(device.mac_address.replace(":", "")))
        + pref(b"wifi-current")
        + pref(device.product_key.encode())
        + bytes(8)
        + b"api\0gagent-current\0"
    )
    return encode_frame(GizwitsCommand.DISCOVER_RESPONSE, payload)


@pytest.fixture
async def rig(monkeypatch):
    devices = [
        _device("127.0.0." + str(i + 1), vendor_id="device-" + str(i)).model_copy(
            update={"mac_address": "02:00:00:00:00:0" + str(i + 1)}
        )
        for i in range(3)
    ]
    devices[2] = devices[2].model_copy(update={"product_key": LOCAL_WAVEMAKER_PRODUCT_KEY})
    targets = [
        _target(d, logical_member=n).model_copy(update={"product_key": d.product_key})
        for d, n in zip(devices, ["master", "slave", "bar"], strict=True)
    ]
    state = SimpleNamespace(
        devices=devices,
        targets=targets,
        raw=[],
        requests=[0, 0, 0],
        tasks=[],
        servers=[],
        silence=set(),
        wrong=set(),
        invalid=set(),
        delay=0.02,
        received=asyncio.Event(),
    )
    loop = asyncio.get_running_loop()
    for index, d in enumerate(devices):

        class Peer(asyncio.DatagramProtocol):
            def __init__(self, i):
                self.i = i

            def connection_made(self, t):
                self.transport = t

            def datagram_received(self, data, addr):
                assert data == encode_frame(GizwitsCommand.DISCOVER_REQUEST)
                state.requests[self.i] += 1
                if state.requests == [1, 1, 1]:
                    state.received.set()

                async def respond():
                    await asyncio.sleep(state.delay)
                    if self.i in state.silence:
                        return
                    if self.i in state.invalid:
                        self.transport.sendto(b"unparsed frame", addr)
                    target = devices[(self.i + 1) % 3] if self.i in state.wrong else devices[self.i]
                    self.transport.sendto(packet(target), addr)

                state.tasks.append(asyncio.create_task(respond()))

        transport, _ = await loop.create_datagram_endpoint(
            lambda i=index: Peer(i), local_addr=(d.address, 12414)
        )
        state.servers.append(transport)
    config = SimpleNamespace(
        devices=[
            SimpleNamespace(
                id=t.logical_member,
                address=d.address,
                limits=SimpleNamespace(min_power=1, max_power=100),
            )
            for t, d in zip(targets, devices, strict=True)
        ],
        observer=SimpleNamespace(
            targets=tuple(d.address for d in devices),
            bind_address="0.0.0.0",
            discovery_timeout_seconds=0.35,
        ),
    )
    monkeypatch.setenv("JEBAO_FLOW_CONFIG", "/unused/config")
    monkeypatch.setenv("JEBAO_FLOW_LOCAL_INSTALL_MANIFEST", "/unused/manifest")
    monkeypatch.setenv("JEBAO_FLOW_MAINTENANCE_BAR_MEMBER", "bar")
    monkeypatch.delenv("JEBAO_FLOW_LOCAL_QUALIFICATIONS", raising=False)
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    monkeypatch.setattr(cli, "load_current_private_config", lambda _: config)
    monkeypatch.setattr(
        cli,
        "load_current_install_manifest",
        lambda _: SimpleNamespace(devices=targets, content=None),
    )
    monkeypatch.setattr(cli, "_runtime_catalog", lambda *a, **k: None)

    def save(raw):
        state.raw.append(raw)
        return hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(cli, "LocalControlJournalStore", lambda: SimpleNamespace(install_blob=save))
    monkeypatch.setattr(cli, "LocalControlLease", lambda: None)
    monkeypatch.setattr(cli, "configured_writer_factory", lambda _: lambda endpoint: None)
    state.make = lambda: cli.LiveBackend(
        master_member="master", slave_member="slave", upper_member="slave", lower_member="master"
    )
    state.config = config
    state.backend = state.make()
    try:
        yield state
    finally:
        for t in state.servers:
            t.close()
        for t in state.tasks:
            t.cancel()
        await asyncio.gather(*state.tasks, return_exceptions=True)
        await asyncio.sleep(0)


async def test_configured_peer_returns_on_identity_reply_without_full_window(rig):
    start = time.monotonic()
    endpoint = await rig.backend.resolver.resolve(rig.targets[0])
    elapsed = time.monotonic() - start
    assert endpoint.target == rig.targets[0] and endpoint.address == "127.0.0.1"
    assert elapsed < 0.20, (
        f"already registered fixed IP still waits full discovery window: {elapsed:.3f}s"
    )
    assert rig.requests == [1, 0, 0]
    assert rig.raw == [packet(rig.devices[0])]
    assert rig.backend.coordinator._resolver is rig.backend.resolver


async def test_three_targets_are_fresh_parallel_and_preserve_firmware_and_raw(rig):
    start = time.monotonic()
    first = await rig.backend.resolver.resolve_many(tuple(rig.targets))
    elapsed = time.monotonic() - start
    assert elapsed < 0.20 and rig.requests == [1, 1, 1] and len(rig.raw) == 3
    assert [e.target for e in first] == rig.targets
    assert all(
        e.wifi_firmware_version == "wifi-current" and e.gizwits_version == "gagent-current"
        for e in first
    )
    await rig.backend.resolver.observe_many(tuple(rig.targets))
    assert rig.requests == [2, 2, 2]


async def test_wrong_identity_rejected_and_raw_kept_without_fallback(rig):
    from jebao_flow.local_control.resolver import CurrentEndpointResolutionError

    rig.wrong.add(0)
    with pytest.raises(CurrentEndpointResolutionError):
        await rig.backend.resolver.resolve(rig.targets[0])
    assert rig.requests == [1, 0, 0] and rig.raw == [packet(rig.devices[1])]


async def test_silent_peer_waits_timeout_and_does_not_reuse_previous_success(rig):
    await rig.backend.resolver.resolve(rig.targets[0])
    rig.silence.add(0)
    start = time.monotonic()
    result = await rig.backend.resolver.observe_many(tuple(rig.targets))
    assert time.monotonic() - start >= 0.30
    assert result[0].endpoint is None and result[0].diagnostic_reason == "current_endpoint_absent"
    assert all(o.endpoint is not None for o in result[1:]) and rig.requests == [2, 1, 1]


async def test_unknown_frame_is_retained_before_valid_reply(rig):
    rig.invalid.add(0)
    result = await rig.backend.resolver.resolve(rig.targets[0])
    assert result.target == rig.targets[0] and b"unparsed frame" in rig.raw


async def test_unconfigured_target_retains_existing_full_discovery(rig):
    rig.config.devices[0].address = None
    backend = rig.make()
    start = time.monotonic()
    result = await backend.resolver.resolve(rig.targets[0])
    assert time.monotonic() - start >= 0.30 and result.target == rig.targets[0]
    assert rig.requests == [1, 1, 1]


async def test_cancel_drains_probes_and_next_call_gets_new_responses(rig):
    rig.delay = 0.15
    task = asyncio.create_task(rig.backend.resolver.resolve_many(tuple(rig.targets)))
    await asyncio.wait_for(rig.received.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Old responders cannot satisfy the new request; their private connected sockets closed.
    rig.delay = 0.02
    result = await rig.backend.resolver.resolve_many(tuple(rig.targets))
    assert len(result) == 3 and rig.requests == [2, 2, 2]


async def test_one_identity_failure_cancels_and_closes_other_probes(rig):
    from jebao_flow.local_control.resolver import CurrentEndpointResolutionError

    rig.wrong.add(0)
    rig.silence.update([1, 2])
    start = time.monotonic()
    with pytest.raises(CurrentEndpointResolutionError):
        await rig.backend.resolver.resolve_many(tuple(rig.targets))
    assert time.monotonic() - start < 0.20
    rig.wrong.clear()
    rig.silence.clear()
    assert len(await rig.backend.resolver.resolve_many(tuple(rig.targets))) == 3


async def test_mixed_fixed_and_unconfigured_members_keep_requested_order(rig):
    rig.config.devices[0].address = None
    backend = rig.make()
    result = await backend.resolver.observe_many(tuple(rig.targets))
    assert [o.target for o in result] == rig.targets
    assert all(o.endpoint is not None for o in result)
    assert rig.requests == [1, 2, 2]


async def test_coordinator_rejects_wrong_fixed_peer_before_any_writer(rig):
    from jebao_flow.local_control.models import LocalControlIntent
    from jebao_flow.local_control.resolver import CurrentEndpointResolutionError

    rig.wrong.add(0)
    coordinator = rig.backend.coordinator
    coordinator._journal.load = lambda _: None
    coordinator._catalog = SimpleNamespace(validate_intent=lambda _: None)
    opened = []
    coordinator._writer_factory = lambda endpoint: opened.append(endpoint)
    intent = LocalControlIntent(
        request_id="fixed_identity_wrong_device_01",
        target=rig.targets[0],
        function="power",
        value=False,
    )
    with pytest.raises(CurrentEndpointResolutionError):
        await coordinator.execute(intent)
    assert opened == [] and rig.requests == [1, 0, 0]
