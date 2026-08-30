from __future__ import annotations

import hashlib
import inspect
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.devices.base import DeviceConnectionError
from jebao_flow.devices.lan import LanJebaoDevice
from jebao_flow.exact_restore import (
    ExactRestoreObservation,
    ExactRestoreRole,
    system_boottime_ns,
)
from jebao_flow.exact_restore_runtime import (
    ExactRestoreRuntimeNotReady,
    FreshExplicitRestoreObserver,
    RestoreWriterFactory,
)
from jebao_flow.physical_identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.models import DeviceTarget, DiscoveredDevice, LinkageRole
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
)
from jebao_flow.protocol.session import (
    STATE_REPLY_ACTION,
    STATE_REPORT_ACTION,
    RawStateCapture,
)
from jebao_flow.read_only_collector import CaptureTarget, ResolvedCaptureEndpoint
from jebao_flow.safety.limits import PowerLimits

_PRODUCT_KEY = LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
_MASTER_ID = "test-master-device"
_SLAVE_ID = "test-slave-device"
_MASTER_MAC = "001122334455"
_SLAVE_MAC = "66778899aabb"
_MASTER_ADDRESS = "master.test"
_SLAVE_ADDRESS = "slave.test"


class _UtcClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(milliseconds=1)
        return result


class _MonotonicClock:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def __call__(self) -> int:
        result = self.value
        self.value += 1_000_000
        return result


class _Discovery:
    def __init__(self, result: list[DiscoveredDevice] | BaseException) -> None:
        self.result = result

    async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
        assert timeout_seconds == 2.0
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _DiscoveryFactory:
    def __init__(self, *results: list[DiscoveredDevice] | BaseException) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> _Discovery:
        self.calls += 1
        if not self.results:
            raise AssertionError("unexpected discovery")
        return _Discovery(self.results.pop(0))


class _ReadSession:
    def __init__(self, address: str, capture: RawStateCapture | BaseException) -> None:
        self.address = address
        self.capture = capture
        self.accept_reports: list[bool] = []
        self.connect_calls = 0
        self.authenticate_calls = 0
        self.disconnect_calls = 0
        self.quarantined = False

    async def connect(self) -> None:
        self.connect_calls += 1

    async def authenticate(self) -> bytes:
        self.authenticate_calls += 1
        return b"not-preserved"

    async def read_raw_state_capture(self, *, accept_reports: bool = False) -> RawStateCapture:
        self.accept_reports.append(accept_reports)
        if isinstance(self.capture, BaseException):
            raise self.capture
        return self.capture

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    def quarantine(self) -> None:
        self.quarantined = True


class _ReadSessionFactory:
    def __init__(self, *captures: RawStateCapture | BaseException) -> None:
        self.captures = list(captures)
        self.instances: list[_ReadSession] = []
        self.reuse: _ReadSession | None = None

    def __call__(self, address: str) -> _ReadSession:
        if self.reuse is not None:
            return self.reuse
        if not self.captures:
            raise AssertionError("unexpected read session")
        session = _ReadSession(address, self.captures.pop(0))
        self.instances.append(session)
        return session


class _Writer:
    def __init__(self, address: str, physical_binding: PhysicalDeviceBinding) -> None:
        self.address = address
        self.physical_binding = physical_binding
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.target_writes: list[DeviceTarget] = []
        self.schedule_writes: list[bytes] = []
        self._connected_session_token = object()

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    def connected_session_token(self) -> object:
        return self._connected_session_token

    async def write_target_connected(
        self,
        target: DeviceTarget,
        *,
        connected_session_token: object,
        guard: Callable[[], bool] | None = None,
    ) -> None:
        assert connected_session_token is self._connected_session_token
        self.target_writes.append(target)

    async def restore_schedule_image_connected(
        self,
        image: bytes,
        *,
        connected_session_token: object,
        guard: Callable[[], bool] | None = None,
    ) -> object:
        assert connected_session_token is self._connected_session_token
        self.schedule_writes.append(image)
        return object()


class _WriterFactory:
    def __init__(self) -> None:
        self.instances: list[_Writer] = []
        self.reuse: _Writer | None = None

    def __call__(
        self,
        role: ExactRestoreRole,
        endpoint: ResolvedCaptureEndpoint,
    ) -> _Writer:
        writer = self.reuse or _Writer(endpoint.address, _binding(role))
        self.instances.append(writer)
        return writer


class _LanRuntimeSession:
    def __init__(self, address: str, state: bytes) -> None:
        self.address = address
        self.state = state
        self.connected = False
        self.connect_calls = 0
        self.authenticate_calls = 0
        self.disconnect_calls = 0
        self.sent: list[bytes] = []

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    def quarantine(self) -> None:
        self.connected = False

    async def authenticate(self) -> bytes:
        self.authenticate_calls += 1
        return b"not-preserved"

    async def heartbeat(self) -> None:
        return None

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
        return self.state

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.sent.append(control_payload)
        return b"ack"


class _LanSessionFactory:
    def __init__(self, state: bytes) -> None:
        self.state = state
        self.instances: list[_LanRuntimeSession] = []

    def __call__(self, address: str) -> _LanRuntimeSession:
        session = _LanRuntimeSession(address, self.state)
        self.instances.append(session)
        return session


class _LanWriterFactory:
    def __init__(self, sessions: _LanSessionFactory) -> None:
        self.sessions = sessions
        self.instances: list[LanJebaoDevice] = []

    def __call__(
        self,
        role: ExactRestoreRole,
        endpoint: ResolvedCaptureEndpoint,
    ) -> LanJebaoDevice:
        writer = LanJebaoDevice(
            role.value,
            endpoint.address,
            _PRODUCT_KEY,
            power_limits=PowerLimits(min_power=30, max_power=80),
            minimum_command_interval_ms=100,
            readback_delay_ms=0,
            allow_hardware_writes=True,
            physical_binding=_binding(role),
            session_factory=self.sessions,
        )
        self.instances.append(writer)
        return writer


def _binding(role: ExactRestoreRole) -> PhysicalDeviceBinding:
    device_id, mac = (
        (_MASTER_ID, _MASTER_MAC) if role is ExactRestoreRole.MASTER else (_SLAVE_ID, _SLAVE_MAC)
    )
    fingerprint = ("a" if role is ExactRestoreRole.MASTER else "b") * 64
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=device_id,
        mac_address=mac,
        product_key=_PRODUCT_KEY,
        config_fingerprint=fingerprint,
    )


def _target(role: ExactRestoreRole) -> CaptureTarget:
    device_id, mac = (
        (_MASTER_ID, _MASTER_MAC) if role is ExactRestoreRole.MASTER else (_SLAVE_ID, _SLAVE_MAC)
    )
    fingerprint = ("a" if role is ExactRestoreRole.MASTER else "b") * 64
    binding = _binding(role)
    return CaptureTarget(
        logical_id=role.value,
        product_key=_PRODUCT_KEY,
        identity_binding_sha256=physical_identity_key(binding),
        vendor_device_id=device_id,
        mac_address=mac,
        config_fingerprint=fingerprint,
    )


def _discovered(
    *,
    master_address: str = _MASTER_ADDRESS,
    slave_address: str = _SLAVE_ADDRESS,
    duplicate_master_endpoint: bool = False,
) -> list[DiscoveredDevice]:
    devices = [
        DiscoveredDevice(
            address=master_address,
            device_id=_MASTER_ID,
            mac_address=_MASTER_MAC,
            product_key=_PRODUCT_KEY,
        ),
        DiscoveredDevice(
            address=slave_address,
            device_id=_SLAVE_ID,
            mac_address=_SLAVE_MAC,
            product_key=_PRODUCT_KEY,
        ),
    ]
    if duplicate_master_endpoint:
        devices.append(
            DiscoveredDevice(
                address=master_address,
                device_id="endpoint-collision",
                mac_address="abcdefabcdef",
                product_key=_PRODUCT_KEY,
            )
        )
    return devices


def _status() -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE)
    raw[0] = 0b00000011  # SwitchON, TimerON, independent Linkage.
    raw[1] = 2  # constant
    raw[2] = 35
    raw[3] = 40
    image = LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48
    raw[
        LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET : LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
        + LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE
    ] = image
    return bytes(raw)


def _capture(
    *,
    action: int = STATE_REPLY_ACTION,
    status: bytes | None = None,
    wire_action: int | None = None,
) -> RawStateCapture:
    state = _status() if status is None else status
    encoded_action = action if wire_action is None else wire_action
    wire = encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes((encoded_action,)) + state,
    )
    return RawStateCapture(wire_frame=wire, action=action, status_payload=state)


def _runtime(
    *,
    discovery: _DiscoveryFactory,
    sessions: _ReadSessionFactory,
    writers: RestoreWriterFactory | None = None,
    monotonic: _MonotonicClock | None = None,
) -> FreshExplicitRestoreObserver:
    return FreshExplicitRestoreObserver(
        targets={
            ExactRestoreRole.MASTER: _target(ExactRestoreRole.MASTER),
            ExactRestoreRole.SLAVE: _target(ExactRestoreRole.SLAVE),
        },
        discovery_factory=discovery,
        session_factory=sessions,
        writer_factory=writers,
        max_identity_age_seconds=1.0,
        discovery_timeout_seconds=2.0,
        utc_clock=_UtcClock(),
        monotonic_clock=monotonic or _MonotonicClock(),
    )


@pytest.mark.asyncio
async def test_observation_is_one_explicit_frame_with_outer_and_schedule() -> None:
    capture = _capture()
    discovery = _DiscoveryFactory(_discovered(), _discovered())
    sessions = _ReadSessionFactory(capture)
    runtime = _runtime(discovery=discovery, sessions=sessions)

    observed = await runtime.observe(ExactRestoreRole.MASTER)

    assert observed == ExactRestoreObservation(
        role=ExactRestoreRole.MASTER,
        identity_binding_sha256=_target(ExactRestoreRole.MASTER).identity_binding_sha256,
        outer=observed.outer,
        schedule=observed.schedule,
        raw_frame_sha256=hashlib.sha256(capture.wire_frame).hexdigest(),
        requested_at=observed.requested_at,
        observed_at=observed.observed_at,
        received_at=observed.received_at,
        requested_monotonic_ns=observed.requested_monotonic_ns,
        observed_monotonic_ns=observed.observed_monotonic_ns,
        received_monotonic_ns=observed.received_monotonic_ns,
    )
    assert observed.outer.enabled is True
    assert observed.outer.timer_enabled is True
    assert observed.outer.linkage is LinkageRole.INDEPENDENT
    assert observed.outer.mode == "constant"
    assert observed.outer.power == 35
    assert observed.outer.frequency == 40
    assert observed.schedule.image_bytes == LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48
    assert sessions.instances[0].accept_reports == [False]
    assert sessions.instances[0].disconnect_calls == 1
    assert discovery.calls == 2


@pytest.mark.asyncio
async def test_each_observation_uses_a_fresh_session_and_final_verify_reads_again() -> None:
    discovery = _DiscoveryFactory(*([_discovered()] * 4))
    sessions = _ReadSessionFactory(_capture(), _capture())
    runtime = _runtime(discovery=discovery, sessions=sessions)

    first = await runtime.observe(ExactRestoreRole.MASTER)
    second = await runtime.observe(ExactRestoreRole.MASTER)

    assert first.received_monotonic_ns < second.requested_monotonic_ns
    assert len(sessions.instances) == 2
    assert sessions.instances[0] is not sessions.instances[1]
    assert [item.accept_reports for item in sessions.instances] == [[False], [False]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capture", "code"),
    [
        (_capture(action=STATE_REPORT_ACTION), "explicit_reply_required"),
        (
            _capture(wire_action=STATE_REPORT_ACTION),
            "wire_frame_provenance_mismatch",
        ),
        (
            _capture(status=bytes(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE - 1)),
            "status_payload_size_invalid",
        ),
    ],
)
async def test_report_mismatch_and_wrong_length_fail_closed(
    capture: RawStateCapture,
    code: str,
) -> None:
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered()),
        sessions=_ReadSessionFactory(capture),
    )

    with pytest.raises(ExactRestoreRuntimeNotReady, match=code):
        await runtime.observe(ExactRestoreRole.MASTER)


@pytest.mark.asyncio
async def test_endpoint_change_or_ambiguity_leaves_no_resolvable_lease() -> None:
    writers = _WriterFactory()
    runtime = _runtime(
        discovery=_DiscoveryFactory(
            _discovered(),
            _discovered(master_address="changed.test"),
        ),
        sessions=_ReadSessionFactory(_capture()),
        writers=writers,
    )

    with pytest.raises(
        ExactRestoreRuntimeNotReady,
        match="identity_endpoint_changed_during_read",
    ):
        await runtime.observe(ExactRestoreRole.MASTER)
    assert not writers.instances

    ambiguous = _runtime(
        discovery=_DiscoveryFactory(_discovered(duplicate_master_endpoint=True)),
        sessions=_ReadSessionFactory(_capture()),
        writers=writers,
    )
    with pytest.raises(ExactRestoreRuntimeNotReady, match="identity_not_exactly_resolved"):
        await ambiguous.observe(ExactRestoreRole.MASTER)
    assert not writers.instances


@pytest.mark.asyncio
async def test_one_action_writer_requires_post_connect_identity_and_guard() -> None:
    writers = _WriterFactory()
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered(), _discovered()),
        sessions=_ReadSessionFactory(_capture()),
        writers=writers,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, observation)

    with pytest.raises(ExactRestoreRuntimeNotReady, match="observation_lease_invalid"):
        runtime.resolve_device(ExactRestoreRole.MASTER, observation)
    await device.connect()
    assert (
        await device.read_connected_identity_binding_sha256() == observation.identity_binding_sha256
    )
    target = DeviceTarget(
        enabled=True,
        timer_enabled=False,
        linkage=LinkageRole.INDEPENDENT,
        mode="constant",
        power=35,
        frequency=40,
    )
    await device.write_target(target, guard=lambda: True)

    assert writers.instances[0].target_writes == [target]
    with pytest.raises(ExactRestoreRuntimeNotReady, match="writer_action_reused"):
        await device.write_target(target, guard=lambda: True)
    assert writers.instances[0].target_writes == [target]
    await device.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["guard", "timeout", "endpoint", "ambiguity"])
async def test_guard_timeout_and_post_connect_identity_fail_with_zero_writes(
    failure: str,
) -> None:
    monotonic = _MonotonicClock()
    post = _discovered()
    if failure == "endpoint":
        post = _discovered(master_address="changed.test")
    elif failure == "ambiguity":
        post = _discovered(duplicate_master_endpoint=True)
    writers = _WriterFactory()
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered(), post),
        sessions=_ReadSessionFactory(_capture()),
        writers=writers,
        monotonic=monotonic,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, observation)
    await device.connect()

    if failure in {"endpoint", "ambiguity"}:
        with pytest.raises(ExactRestoreRuntimeNotReady):
            await device.read_connected_identity_binding_sha256()
    else:
        await device.read_connected_identity_binding_sha256()
        if failure == "timeout":
            monotonic.value += 2_000_000_000
        with pytest.raises(ExactRestoreRuntimeNotReady):
            await device.write_target(DeviceTarget(power=35), guard=lambda: failure != "guard")

    assert writers.instances[0].target_writes == []
    assert writers.instances[0].schedule_writes == []
    await device.disconnect()


@pytest.mark.asyncio
async def test_schedule_write_is_exact_once_and_writer_instances_cannot_be_reused() -> None:
    shared = _Writer(_MASTER_ADDRESS, _binding(ExactRestoreRole.MASTER))
    writers = _WriterFactory()
    writers.reuse = shared
    runtime = _runtime(
        discovery=_DiscoveryFactory(*([_discovered()] * 5)),
        sessions=_ReadSessionFactory(_capture(), _capture()),
        writers=writers,
    )
    first = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, first)
    await device.connect()
    await device.read_connected_identity_binding_sha256()
    image = LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48
    await device.restore_schedule_image(image, guard=lambda: True)
    assert shared.schedule_writes == [image]
    await device.disconnect()

    second = await runtime.observe(ExactRestoreRole.MASTER)
    with pytest.raises(ExactRestoreRuntimeNotReady, match="writer_not_fresh"):
        runtime.resolve_device(ExactRestoreRole.MASTER, second)
    assert shared.schedule_writes == [image]


@pytest.mark.asyncio
async def test_real_lan_writer_cannot_reconnect_after_identity_ticket() -> None:
    lan_sessions = _LanSessionFactory(_status())
    lan_writers = _LanWriterFactory(lan_sessions)
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered(), _discovered()),
        sessions=_ReadSessionFactory(_capture()),
        writers=lan_writers,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, observation)
    await device.connect()
    await device.read_connected_identity_binding_sha256()
    session = lan_sessions.instances[0]
    await session.disconnect()
    connect_calls = session.connect_calls
    authenticate_calls = session.authenticate_calls

    with pytest.raises(DeviceConnectionError, match="exact restore session"):
        await device.restore_schedule_image(
            LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48,
            guard=lambda: True,
        )

    assert len(lan_sessions.instances) == 1
    assert session.connect_calls == connect_calls
    assert session.authenticate_calls == authenticate_calls
    assert session.sent == []


@pytest.mark.asyncio
async def test_real_lan_writer_uses_identity_checked_session_for_one_schedule_write() -> None:
    lan_sessions = _LanSessionFactory(_status())
    lan_writers = _LanWriterFactory(lan_sessions)
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered(), _discovered()),
        sessions=_ReadSessionFactory(_capture()),
        writers=lan_writers,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, observation)
    await device.connect()
    await device.read_connected_identity_binding_sha256()

    await device.restore_schedule_image(
        LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48,
        guard=lambda: True,
    )

    assert len(lan_sessions.instances) == 1
    session = lan_sessions.instances[0]
    assert session.connect_calls == 1
    assert session.authenticate_calls == 1
    assert len(session.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action_kind", ["schedule", "outer"])
async def test_real_lan_writer_reconnect_after_identity_ticket_sends_no_control(
    action_kind: str,
) -> None:
    lan_sessions = _LanSessionFactory(_status())
    lan_writers = _LanWriterFactory(lan_sessions)
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered(), _discovered()),
        sessions=_ReadSessionFactory(_capture()),
        writers=lan_writers,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, observation)
    await device.connect()
    await device.read_connected_identity_binding_sha256()

    raw_writer = lan_writers.instances[0]
    await raw_writer.disconnect()
    await raw_writer.connect()

    with pytest.raises(DeviceConnectionError, match="session token"):
        if action_kind == "schedule":
            await device.restore_schedule_image(
                LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48,
                guard=lambda: True,
            )
        else:
            await device.write_target(
                DeviceTarget(
                    enabled=True,
                    timer_enabled=False,
                    linkage=LinkageRole.INDEPENDENT,
                    mode="constant",
                    power=35,
                    frequency=40,
                ),
                guard=lambda: True,
            )

    assert len(lan_sessions.instances) == 2
    assert lan_sessions.instances[0].sent == []
    assert lan_sessions.instances[1].connect_calls == 1
    assert lan_sessions.instances[1].authenticate_calls == 1
    assert lan_sessions.instances[1].sent == []


@pytest.mark.asyncio
async def test_missing_writer_factory_and_reused_read_session_fail_closed() -> None:
    sessions = _ReadSessionFactory(_capture())
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered()),
        sessions=sessions,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    with pytest.raises(ExactRestoreRuntimeNotReady, match="writer_factory_unavailable"):
        runtime.resolve_device(ExactRestoreRole.MASTER, observation)

    reused_session = _ReadSession(_MASTER_ADDRESS, _capture())
    sessions = _ReadSessionFactory()
    sessions.reuse = reused_session
    reused_runtime = _runtime(
        discovery=_DiscoveryFactory(*([_discovered()] * 3)),
        sessions=sessions,
    )
    await reused_runtime.observe(ExactRestoreRole.MASTER)
    with pytest.raises(ExactRestoreRuntimeNotReady, match="read_session_not_fresh"):
        await reused_runtime.observe(ExactRestoreRole.MASTER)
    assert reused_session.accept_reports == [False]


@pytest.mark.asyncio
async def test_writer_with_wrong_physical_binding_is_rejected_before_connect() -> None:
    writers = _WriterFactory()
    writers.reuse = _Writer(
        _MASTER_ADDRESS,
        _binding(ExactRestoreRole.SLAVE),
    )
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered()),
        sessions=_ReadSessionFactory(_capture()),
        writers=writers,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)

    with pytest.raises(ExactRestoreRuntimeNotReady, match="writer_endpoint_mismatch"):
        runtime.resolve_device(ExactRestoreRole.MASTER, observation)

    assert writers.instances[0].connect_calls == 0
    assert writers.instances[0].target_writes == []
    assert writers.instances[0].schedule_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_target",
    [
        DeviceTarget(power=35),
        DeviceTarget(
            enabled=True,
            power=35,
            mode="constant",
            frequency=40,
            linkage=LinkageRole.MASTER,
            timer_enabled=False,
        ),
    ],
)
async def test_non_exact_outer_target_consumes_ticket_without_write_or_retry(
    invalid_target: DeviceTarget,
) -> None:
    writers = _WriterFactory()
    runtime = _runtime(
        discovery=_DiscoveryFactory(_discovered(), _discovered(), _discovered()),
        sessions=_ReadSessionFactory(_capture()),
        writers=writers,
    )
    observation = await runtime.observe(ExactRestoreRole.MASTER)
    device = runtime.resolve_device(ExactRestoreRole.MASTER, observation)
    await device.connect()
    await device.read_connected_identity_binding_sha256()

    with pytest.raises(ExactRestoreRuntimeNotReady, match="outer_target_invalid"):
        await device.write_target(invalid_target, guard=lambda: True)
    with pytest.raises(ExactRestoreRuntimeNotReady, match="writer_action_reused"):
        await device.write_target(
            DeviceTarget(
                enabled=True,
                power=35,
                mode="constant",
                frequency=40,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            ),
            guard=lambda: True,
        )

    assert writers.instances[0].target_writes == []
    await device.disconnect()


def test_default_ticket_clock_includes_system_suspend_time() -> None:
    parameter = inspect.signature(FreshExplicitRestoreObserver).parameters["monotonic_clock"]
    assert parameter.default is system_boottime_ns


def test_import_graph_does_not_load_frozen_native_harness() -> None:
    script = """
import sys
import jebao_flow.exact_restore_runtime
frozen = {
    'jebao_flow.devices.schedule_linkage',
    'jebao_flow.devices.schedule_flow_experiment',
    'jebao_flow.devices.schedule_transaction',
    'jebao_flow.devices.linkage',
    'jebao_flow.schedule_flow_experiment_cli',
    'jebao_flow.schedule_linkage_cli',
}
loaded = sorted(frozen.intersection(sys.modules))
if loaded:
    raise SystemExit(','.join(loaded))
"""
    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (source_root, environment.get("PYTHONPATH")))
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
