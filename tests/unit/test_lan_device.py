import asyncio
from typing import ClassVar

import pytest

from jebao_flow.config import DeviceConfig, DeviceControlConfig, DeviceType, RuntimeConfig
from jebao_flow.devices import (
    ControlAckFailureKind,
    ControlAcknowledgementError,
    ControlAckPowerMismatchError,
    ControlAckReadbackError,
    ControlAckResolutionStage,
    ControlAckResolutionState,
    ControlAckStateMismatchError,
    ControlReadbackError,
    ControlStateMismatchError,
    ControlVerificationOutcome,
    DeviceConnectionError,
    HardwareWritesDisabledError,
    LanJebaoDevice,
    PhysicalDeviceBinding,
    PowerStateVerificationError,
    SafetyInterlockError,
    StateVerificationError,
    UnsupportedCapabilityError,
    create_lan_device,
    create_read_only_lan_device,
)
from jebao_flow.protocol.errors import (
    ProtocolConnectionError,
    ProtocolError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)
from jebao_flow.protocol.models import Capability, DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    build_local_wavemaker_pro_schedule_control_payload,
    extract_local_wavemaker_pro_schedule_image,
)
from jebao_flow.safety.limits import PowerLimits


def _pro_state(
    *,
    enabled: bool = True,
    timer_enabled: bool = False,
    linkage: LinkageRole = LinkageRole.INDEPENDENT,
    power: int = 30,
    fault: int = 0,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    linkage_index = LOCAL_WAVEMAKER_PRO.by_name("Linkage").enum_values.index(linkage.value)
    raw[0] = int(enabled) | (int(timer_enabled) << 1) | (linkage_index << 2)
    raw[1] = 2
    raw[2] = power
    raw[3] = 32
    raw[451] = fault
    return bytes(raw)


def _schedule_slot(*, flow: int, start_hour: int = 8, end_hour: int = 9) -> bytes:
    return bytes((start_hour, 0, end_hour, 0, 2, flow, 20, 0, 0))


def _pro_schedule_state(slots: dict[int, bytes], *, clock_marker: bytes | None = None) -> bytes:
    raw = bytearray(_pro_state(enabled=True, timer_enabled=True, power=35))
    raw[11:443] = LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48
    for slot_index, slot_wire in slots.items():
        start = 11 + slot_index * 9
        raw[start : start + 9] = slot_wire
    if clock_marker is not None:
        if len(clock_marker) != 8:
            raise ValueError("clock marker must be eight bytes")
        raw[443:451] = clock_marker
    return bytes(raw)


class _FakeSession:
    instances: ClassVar[list["_FakeSession"]] = []
    timeline: ClassVar[list[str]] = []
    state = _pro_state()
    read_failures_remaining = 0
    read_failures_disconnect = False
    send_failure: Exception | None = None

    def __init__(self, address: str) -> None:
        self.instance_id = len(self.__class__.instances)
        self.address = address
        self.connected = False
        self.connect_calls = 0
        self.authenticate_calls = 0
        self.sent: list[bytes] = []
        self.read_accept_reports: list[bool] = []
        self.events: list[str] = []
        self.__class__.instances.append(self)
        self.__class__.timeline.append(f"{self.instance_id}:create")

    async def connect(self) -> None:
        self.connect_calls += 1
        self.events.append("connect")
        self.__class__.timeline.append(f"{self.instance_id}:connect")
        self.connected = True

    async def disconnect(self) -> None:
        self.events.append("disconnect")
        self.__class__.timeline.append(f"{self.instance_id}:disconnect")
        self.connected = False

    def quarantine(self) -> None:
        self.events.append("quarantine")
        self.__class__.timeline.append(f"{self.instance_id}:quarantine")
        self.connected = False

    async def authenticate(self) -> bytes:
        self.authenticate_calls += 1
        self.events.append("authenticate")
        self.__class__.timeline.append(f"{self.instance_id}:authenticate")
        return b"never-logged"

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
        self.read_accept_reports.append(accept_reports)
        self.events.append(f"read:{'reports' if accept_reports else 'reply-only'}")
        self.__class__.timeline.append(
            f"{self.instance_id}:read:{'reports' if accept_reports else 'reply-only'}"
        )
        if self.__class__.read_failures_remaining:
            self.__class__.read_failures_remaining -= 1
            if self.__class__.read_failures_disconnect:
                self.connected = False
            raise ProtocolTimeoutError("simulated transient read timeout")
        return self.state

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.events.append("send-control")
        self.__class__.timeline.append(f"{self.instance_id}:send-control")
        self.sent.append(control_payload)
        if self.__class__.send_failure is not None:
            raise self.__class__.send_failure
        return b"ack"


@pytest.fixture(autouse=True)
def _reset_fake_session() -> None:
    _FakeSession.instances.clear()
    _FakeSession.timeline.clear()
    _FakeSession.state = _pro_state()
    _FakeSession.read_failures_remaining = 0
    _FakeSession.read_failures_disconnect = False
    _FakeSession.send_failure = None


def _device(
    *,
    allow_writes: bool = False,
    minimum_command_interval_ms: int = 1000,
    ack_loss_resolution_attempts: int = 8,
    ack_loss_resolution_timeout_seconds: float = 55.0,
) -> LanJebaoDevice:
    return LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=allow_writes,
        minimum_command_interval_ms=minimum_command_interval_ms,
        readback_delay_ms=0,
        ack_loss_resolution_attempts=ack_loss_resolution_attempts,
        ack_loss_resolution_timeout_seconds=ack_loss_resolution_timeout_seconds,
        ack_loss_retry_delay_seconds=0,
        session_factory=_FakeSession,
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (ProtocolTimeoutError("private"), ControlAckFailureKind.TIMEOUT),
        (UnexpectedResponseError("private"), ControlAckFailureKind.UNEXPECTED_RESPONSE),
        (ProtocolConnectionError("private"), ControlAckFailureKind.CONNECTION),
        (ProtocolError("private"), ControlAckFailureKind.PROTOCOL),
        (OSError("private"), ControlAckFailureKind.OS_ERROR),
    ),
)
def test_ack_failure_classification_is_allow_listed(
    error: BaseException,
    expected: ControlAckFailureKind,
) -> None:
    assert LanJebaoDevice._classify_ack_failure(error) is expected  # noqa: SLF001


async def test_adapter_reads_protocol_neutral_state_and_faults() -> None:
    _FakeSession.state = _pro_state(enabled=True, power=55, fault=0b01000000)
    device = _device()
    await device.connect()

    state = await device.get_state()

    assert state.online is True
    assert state.enabled is True
    assert state.power == 55
    assert state.mode == "constant"
    assert state.frequency == 32
    assert state.linkage is LinkageRole.INDEPENDENT
    assert state.timer_enabled is False
    assert state.error == "Fault_UART"


async def test_explicit_state_read_rejects_unsolicited_reports() -> None:
    device = _device()
    await device.connect()

    ordinary = await device.get_state()
    explicit = await device.get_explicit_state()

    assert ordinary == explicit.model_copy(update={"observed_at": ordinary.observed_at})
    assert _FakeSession.instances[0].read_accept_reports == [True, False]


async def test_explicit_disconnect_replaces_the_session_object_on_reconnect() -> None:
    device = _device()
    await device.connect()
    original = _FakeSession.instances[0]

    await device.disconnect()
    await device.connect()

    replacement = _FakeSession.instances[1]
    assert replacement is not original
    assert original.connected is False
    assert original.connect_calls == 1
    assert replacement.connect_calls == 1
    assert replacement.authenticate_calls == 1


def test_pro_profile_exposes_native_linkage_and_timer_capabilities() -> None:
    capabilities = _device().capabilities

    assert Capability.LINKAGE in capabilities.writable
    assert Capability.TIMER in capabilities.writable
    assert capabilities.linkage_roles == frozenset(
        {
            LinkageRole.INDEPENDENT,
            LinkageRole.MASTER,
            LinkageRole.SYNC_SLAVE,
            LinkageRole.ASYNC_SLAVE,
        }
    )
    assert {"pulse", "sine", "constant"} <= capabilities.native_modes


async def test_adapter_attaches_read_only_schedule_to_device_state() -> None:
    raw = bytearray(_pro_state(enabled=True, power=55))
    raw[0] |= 0b10  # TimerON
    raw[11:443] = bytes([0xEE]) * 432
    raw[11:20] = bytes.fromhex("000002000128280000")
    raw[443:451] = bytes((20, 26, 8, 26, 0, 10, 37, 30))
    _FakeSession.state = bytes(raw)
    device = _device()
    await device.connect()

    state = await device.get_state()

    assert state.schedule is not None
    assert state.schedule.enabled is True
    assert state.schedule.entries[0].mode == "sine"
    assert state.schedule.entries[0].parameters["flow"] == 40
    assert state.model_dump(mode="json")["schedule"]["device_local_time"] == (
        "2026-08-26T10:37:30"
    )


async def test_preview_builds_atomic_target_without_sending() -> None:
    device = _device()

    plan = device.preview_target(DeviceTarget(enabled=True, power=50))

    assert plan.changes == {"SwitchON": True, "Flow": 50}
    assert plan.payload[0] == 0x01
    assert _FakeSession.instances[0].sent == []


def test_preview_combines_timer_linkage_mode_frequency_and_flow_in_one_payload() -> None:
    device = _device()

    plan = device.preview_target(
        DeviceTarget(
            enabled=True,
            power=42,
            mode="sine",
            frequency=30,
            linkage=LinkageRole.ASYNC_SLAVE,
            timer_enabled=False,
        )
    )

    assert plan.changes == {
        "SwitchON": True,
        "TimerON": False,
        "Linkage": LinkageRole.ASYNC_SLAVE,
        "Flow": 42,
        "Mode": "sine",
        "Frequency": 30,
    }
    assert plan.payload[0] == 0x01
    assert plan.payload[1:9] == bytes(7) + bytes([0x3F])
    assert plan.payload[9:13] == bytes([0x0D, 0x01, 42, 30])


def test_preview_restores_manual_fallback_and_timer_on_in_one_payload() -> None:
    device = _device()

    plan = device.preview_target(
        DeviceTarget(
            enabled=True,
            power=70,
            mode="random",
            frequency=34,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
    )

    assert plan.changes == {
        "SwitchON": True,
        "TimerON": True,
        "Linkage": LinkageRole.INDEPENDENT,
        "Flow": 70,
        "Mode": "random",
        "Frequency": 34,
    }


def test_preview_linkage_sets_only_the_linkage_datapoint_flag() -> None:
    device = _device()

    plan = device.preview_linkage(LinkageRole.ASYNC_SLAVE)

    assert plan.changes == {"Linkage": LinkageRole.ASYNC_SLAVE}
    assert plan.payload[0] == 0x01
    assert plan.payload[1:9] == bytes(7) + bytes([0x04])
    assert plan.payload[9] == 0x0C
    assert plan.payload[10:] == bytes(len(plan.payload) - 10)


async def test_hardware_write_lock_is_default() -> None:
    device = _device()
    await device.connect()

    with pytest.raises(HardwareWritesDisabledError, match="writes are locked"):
        await device.set_power(50)

    assert _FakeSession.instances[0].sent == []


async def test_safety_guard_blocks_target_before_lan_send() -> None:
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_target(
            DeviceTarget(enabled=True, power=50),
            guard=lambda: False,
        )

    assert _FakeSession.instances[0].sent == []


async def test_safety_guard_blocks_linkage_only_write_before_lan_send() -> None:
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_linkage(LinkageRole.ASYNC_SLAVE, guard=lambda: False)

    assert _FakeSession.instances[0].sent == []


async def test_guarded_power_write_sets_only_the_flow_datapoint_flag() -> None:
    _FakeSession.state = _pro_state(
        timer_enabled=False,
        linkage=LinkageRole.ASYNC_SLAVE,
        power=38,
    )
    device = _device(allow_writes=True)
    await device.connect()

    await device.write_power(38, guard=lambda: True)

    [payload] = _FakeSession.instances[0].sent
    assert payload[0] == 0x01
    assert payload[1:9] == bytes(7) + bytes([0x10])
    assert payload[9 + 2] == 38
    assert payload[9 : 9 + 2] == bytes(2)
    assert payload[9 + 3 :] == bytes(len(payload) - 12)


async def test_safety_guard_blocks_power_only_write_before_lan_send() -> None:
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_power(38, guard=lambda: False)

    assert _FakeSession.instances[0].sent == []


async def test_linkage_only_write_preserves_timer_and_manual_control_fields() -> None:
    _FakeSession.state = _pro_state(
        timer_enabled=True,
        linkage=LinkageRole.ASYNC_SLAVE,
        power=55,
    )
    device = _device(allow_writes=True)
    await device.connect()

    await device.write_linkage(LinkageRole.ASYNC_SLAVE, guard=lambda: True)

    expected_payload = device.preview_linkage(LinkageRole.ASYNC_SLAVE).payload
    assert _FakeSession.instances[0].sent == [expected_payload]
    state = await device.get_state()
    assert state.timer_enabled is True
    assert state.power == 55
    assert state.mode == "constant"
    assert state.frequency == 32
    assert state.linkage is LinkageRole.ASYNC_SLAVE


@pytest.mark.parametrize("power", [0, 29, 76, 100])
def test_preview_enforces_configured_power_limits(power: int) -> None:
    device = _device()

    with pytest.raises(ValueError, match="configured range"):
        device.preview_target(DeviceTarget(enabled=True, power=power))


def test_disabled_target_only_writes_switch_and_never_zero_flow() -> None:
    device = _device()

    plan = device.preview_target(DeviceTarget(enabled=False, power=0))

    assert plan.changes == {"SwitchON": False}


def test_disabled_target_can_still_unlink_and_disable_timer() -> None:
    device = _device()

    plan = device.preview_target(
        DeviceTarget(
            enabled=False,
            power=0,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
    )

    assert plan.changes == {
        "SwitchON": False,
        "TimerON": False,
        "Linkage": LinkageRole.INDEPENDENT,
    }


def test_bar_wavemaker_rejects_async_linkage() -> None:
    device = LanJebaoDevice(
        "bar",
        "pump.local",
        LOCAL_WAVEMAKER.product_key,
        session_factory=_FakeSession,
    )

    with pytest.raises(UnsupportedCapabilityError, match="async_slave"):
        device.preview_target(
            DeviceTarget(
                enabled=True,
                power=50,
                linkage=LinkageRole.ASYNC_SLAVE,
            )
        )


async def test_write_requires_readback_match() -> None:
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(PowerStateVerificationError, match="did not apply control"):
        await device.set_power(50)

    assert len(_FakeSession.instances[0].sent) == 1


async def test_multi_field_readback_mismatch_is_not_power_specific() -> None:
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(StateVerificationError, match="did not apply control") as captured:
        await device.write_target(
            DeviceTarget(
                enabled=True,
                power=50,
                mode="sine",
                frequency=20,
                linkage=LinkageRole.ASYNC_SLAVE,
                timer_enabled=False,
            )
        )

    assert type(captured.value) is ControlStateMismatchError
    assert len(_FakeSession.instances[0].sent) == 1


async def test_write_retries_transient_readback_timeout_without_resending_control() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.read_failures_remaining = 1
    device = _device(allow_writes=True)
    await device.connect()

    await device.set_power(50)

    assert _FakeSession.read_failures_remaining == 0
    assert len(_FakeSession.instances[0].sent) == 1


async def test_write_reauthenticates_only_for_readback_after_transport_failure() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.read_failures_remaining = 1
    _FakeSession.read_failures_disconnect = True
    device = _device(allow_writes=True)
    await device.connect()

    await device.write_power(50)

    session = _FakeSession.instances[0]
    assert session.sent and len(session.sent) == 1
    assert session.connect_calls == 2
    assert session.authenticate_calls == 2


async def test_write_fails_after_bounded_transient_readback_timeouts() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.read_failures_remaining = 3
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(ControlReadbackError, match="3 readback attempts") as captured:
        await device.set_power(50)

    assert not isinstance(captured.value, PowerStateVerificationError)
    assert isinstance(captured.value.__cause__, ProtocolTimeoutError)
    assert len(_FakeSession.instances[0].sent) == 1


async def test_unconfirmed_ack_fresh_state_report_exact_match_is_evidence_not_ack(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0
    resolution_updates = []

    def persist_ack_unconfirmed(kind: ControlAckFailureKind) -> None:
        nonlocal hook_calls
        assert kind is ControlAckFailureKind.TIMEOUT
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")
        _FakeSession.timeline.append("ack-unconfirmed-hook")

    outcome = await device.write_power(
        50,
        on_ack_unconfirmed=persist_ack_unconfirmed,
        on_ack_resolution=resolution_updates.append,
    )

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK
    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 1
    assert session.authenticate_calls == 1
    assert session.read_accept_reports == []
    evidence = _FakeSession.instances[1]
    replacement = _FakeSession.instances[2]
    assert evidence is not session
    assert evidence.sent == []
    assert evidence.connect_calls == 1
    assert evidence.authenticate_calls == 1
    assert evidence.read_accept_reports == [True]
    assert evidence.connected is False
    assert replacement.sent == []
    assert replacement.connect_calls == 1
    assert replacement.authenticate_calls == 1
    assert replacement.read_accept_reports == []
    assert replacement.connected is True
    assert _FakeSession.timeline.index("0:send-control") < _FakeSession.timeline.index(
        "ack-unconfirmed-hook"
    )
    assert _FakeSession.timeline.index("ack-unconfirmed-hook") < _FakeSession.timeline.index(
        "0:disconnect"
    )
    assert _FakeSession.timeline.index("ack-unconfirmed-hook") < _FakeSession.timeline.index(
        "1:create"
    )
    assert _FakeSession.timeline.index("ack-unconfirmed-hook") < _FakeSession.timeline.index(
        "1:read:reports"
    )
    assert _FakeSession.timeline.index("1:read:reports") < _FakeSession.timeline.index(
        "1:disconnect"
    )
    assert _FakeSession.timeline.index("1:disconnect") < _FakeSession.timeline.index("2:create")
    assert [
        (update.stage, update.attempt, update.state) for update in resolution_updates
    ] == [
        (ControlAckResolutionStage.QUARANTINE, 0, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.QUARANTINE, 0, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.CONNECT, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.CONNECT, 1, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.AUTHENTICATE, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.AUTHENTICATE, 1, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.QUERY, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.QUERY, 1, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.DECODE, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.DECODE, 1, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.QUARANTINE, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.QUARANTINE, 1, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.CONNECT, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.CONNECT, 1, ControlAckResolutionState.SUCCEEDED),
        (ControlAckResolutionStage.AUTHENTICATE, 1, ControlAckResolutionState.STARTED),
        (ControlAckResolutionStage.AUTHENTICATE, 1, ControlAckResolutionState.SUCCEEDED),
    ]


async def test_unconfirmed_ack_fresh_state_report_mismatch_is_typed_without_resending() -> None:
    _FakeSession.state = _pro_state(power=34)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0
    resolution_updates = []

    def persist_ack_unconfirmed(kind: ControlAckFailureKind) -> None:
        nonlocal hook_calls
        assert kind is ControlAckFailureKind.TIMEOUT
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")
        _FakeSession.timeline.append("ack-unconfirmed-hook")

    with pytest.raises(ControlAckPowerMismatchError, match="did not apply control"):
        await device.write_power(
            50,
            on_ack_unconfirmed=persist_ack_unconfirmed,
            on_ack_resolution=resolution_updates.append,
        )

    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 1
    assert session.authenticate_calls == 1
    assert session.read_accept_reports == []
    resolution_sessions = _FakeSession.instances[1:9]
    assert len(resolution_sessions) == 8
    assert len({id(candidate) for candidate in resolution_sessions}) == 8
    assert all(candidate.sent == [] for candidate in resolution_sessions)
    assert all(candidate.read_accept_reports == [True] for candidate in resolution_sessions)
    assert _FakeSession.timeline.index("ack-unconfirmed-hook") < _FakeSession.timeline.index(
        "1:read:reports"
    )
    decode_updates = [
        update
        for update in resolution_updates
        if update.stage is ControlAckResolutionStage.DECODE
    ]
    assert len(decode_updates) == 16
    assert all(
        update.state
        in {ControlAckResolutionState.STARTED, ControlAckResolutionState.FAILED}
        for update in decode_updates
    )
    assert decode_updates[-1].attempt == 8
    assert decode_updates[-1].state is ControlAckResolutionState.FAILED


async def test_unconfirmed_ack_fresh_read_unavailable_is_typed_without_resending() -> None:
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    _FakeSession.read_failures_remaining = 8
    _FakeSession.read_failures_disconnect = True
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0

    def persist_ack_unconfirmed(kind: ControlAckFailureKind) -> None:
        nonlocal hook_calls
        assert kind is ControlAckFailureKind.TIMEOUT
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(
            50,
            on_ack_unconfirmed=persist_ack_unconfirmed,
        )

    assert captured.value.stage is ControlAckResolutionStage.QUERY
    assert captured.value.attempts == 8
    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 1
    assert session.authenticate_calls == 1
    assert session.read_accept_reports == []
    resolution_sessions = _FakeSession.instances[1:9]
    assert len(resolution_sessions) == 8
    assert all(candidate.sent == [] for candidate in resolution_sessions)
    assert all(candidate.read_accept_reports == [True] for candidate in resolution_sessions)


async def test_unconfirmed_ack_retires_each_failed_object_then_succeeds_on_fourth_read(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    _FakeSession.read_failures_remaining = 3
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    outcome = await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK
    assert len(_FakeSession.instances) == 6
    assert len({id(candidate) for candidate in _FakeSession.instances}) == 6
    assert len(_FakeSession.instances[0].sent) == 1
    assert all(candidate.sent == [] for candidate in _FakeSession.instances[1:])
    for failed_id in range(1, 4):
        assert _FakeSession.timeline.index(f"{failed_id}:disconnect") < (
            _FakeSession.timeline.index(f"{failed_id + 1}:create")
        )
    evidence = _FakeSession.instances[4]
    replacement = _FakeSession.instances[5]
    assert evidence.read_accept_reports == [True]
    assert evidence.connected is False
    assert replacement.read_accept_reports == []
    assert replacement.connected is True
    assert _FakeSession.timeline.index("4:disconnect") < _FakeSession.timeline.index("5:create")

    await device.get_state()

    assert device._session is replacement  # noqa: SLF001
    assert evidence.read_accept_reports == [True]
    assert replacement.read_accept_reports == [True]


async def test_unconfirmed_ack_rejects_factory_reusing_retired_object() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    shared = _FakeSession("pump.local")

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_resolution_attempts=2,
        ack_loss_retry_delay_seconds=0,
        session_factory=lambda _address: shared,
    )
    await device.connect()

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    assert captured.value.stage is ControlAckResolutionStage.CONNECT
    assert captured.value.attempts == 2
    assert len(shared.sent) == 1
    assert shared.read_accept_reports == []

    with pytest.raises(DeviceConnectionError, match="replace retired session"):
        await device.connect()

    assert shared.connect_calls == 1


async def test_unconfirmed_ack_resolution_obeys_hard_deadline_before_next_session(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")

    class SlowFreshSession(_FakeSession):
        async def connect(self) -> None:
            if self.instance_id > 0:
                await asyncio.sleep(1)
            await super().connect()

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_resolution_timeout_seconds=0.05,
        ack_loss_resolution_attempts=8,
        ack_loss_retry_delay_seconds=0,
        session_factory=SlowFreshSession,
    )
    await device.connect()
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    elapsed = asyncio.get_running_loop().time() - started
    assert captured.value.stage is ControlAckResolutionStage.CONNECT
    assert captured.value.attempts == 1
    assert elapsed < 0.3
    assert sum(len(candidate.sent) for candidate in SlowFreshSession.instances) == 1
    assert all(candidate.read_accept_reports == [] for candidate in SlowFreshSession.instances)
    assert sum(candidate.connect_calls for candidate in SlowFreshSession.instances[1:]) == 0


async def test_unconfirmed_ack_requires_original_session_quarantine_before_resolution(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")

    class UnquarantinableSession(_FakeSession):
        async def disconnect(self) -> None:
            self.events.append("disconnect-stuck")

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_retry_delay_seconds=0,
        session_factory=UnquarantinableSession,
    )
    await device.connect()
    original = UnquarantinableSession.instances[0]

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    assert captured.value.stage is ControlAckResolutionStage.QUARANTINE
    assert len(original.sent) == 1
    assert original.connected is True
    assert all(candidate.connect_calls == 0 for candidate in UnquarantinableSession.instances[1:])


async def test_unconfirmed_ack_malformed_state_report_length_is_decode_stage_failure() -> None:
    _FakeSession.state = b""
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(
        allow_writes=True,
        minimum_command_interval_ms=100,
        ack_loss_resolution_attempts=2,
    )
    await device.connect()

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    assert captured.value.stage is ControlAckResolutionStage.DECODE
    assert captured.value.attempts == 2
    assert len(_FakeSession.instances[0].sent) == 1
    assert all(candidate.sent == [] for candidate in _FakeSession.instances[1:])
    assert all(
        candidate.read_accept_reports == [True] for candidate in _FakeSession.instances[1:3]
    )


@pytest.mark.parametrize(
    ("failure_stage", "expected_stage"),
    [
        ("connect", ControlAckResolutionStage.CONNECT),
        ("authenticate", ControlAckResolutionStage.AUTHENTICATE),
        ("query", ControlAckResolutionStage.QUERY),
    ],
)
async def test_unconfirmed_ack_unexpected_stage_failure_retires_connected_candidate(
    failure_stage: str,
    expected_stage: ControlAckResolutionStage,
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")

    class UnexpectedFailureSession(_FakeSession):
        async def connect(self) -> None:
            await super().connect()
            if self.instance_id in {1, 2} and failure_stage == "connect":
                raise RuntimeError("private unexpected connect failure")

        async def authenticate(self) -> bytes:
            result = await super().authenticate()
            if self.instance_id in {1, 2} and failure_stage == "authenticate":
                raise RuntimeError("private unexpected authentication failure")
            return result

        async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
            if self.instance_id in {1, 2} and failure_stage == "query":
                raise RuntimeError("private unexpected query failure")
            return await super().read_raw_state(accept_reports=accept_reports)

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_resolution_attempts=2,
        ack_loss_retry_delay_seconds=0,
        session_factory=UnexpectedFailureSession,
    )
    await device.connect()

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    assert captured.value.stage is expected_stage
    assert captured.value.attempts == 2
    assert sum(len(candidate.sent) for candidate in UnexpectedFailureSession.instances) == 1
    failed_candidates = UnexpectedFailureSession.instances[1:3]
    assert all(candidate.connected is False for candidate in failed_candidates)
    clean = UnexpectedFailureSession.instances[3]
    assert clean not in failed_candidates
    await device.connect()
    assert device._session is clean  # noqa: SLF001
    assert clean.connected is True
    assert clean.sent == []


async def test_unconfirmed_ack_resolution_cancellation_quarantines_without_close_wait(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    query_started = asyncio.Event()

    class SlowQuerySession(_FakeSession):
        async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
            if self.instance_id > 0:
                query_started.set()
                await asyncio.sleep(10)
            return await super().read_raw_state(accept_reports=accept_reports)

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_retry_delay_seconds=0,
        session_factory=SlowQuerySession,
    )
    await device.connect()
    task = asyncio.create_task(
        device.write_power(50, on_ack_unconfirmed=lambda _kind: None)
    )
    await asyncio.wait_for(query_started.wait(), timeout=1)
    cancelled_at = asyncio.get_running_loop().time()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert asyncio.get_running_loop().time() - cancelled_at < 0.2
    assert len(SlowQuerySession.instances[0].sent) == 1
    assert sum(len(candidate.sent) for candidate in SlowQuerySession.instances) == 1
    assert SlowQuerySession.instances[1].connected is False
    assert device.connected is False
    await device.connect()
    assert device.connected is True
    assert all(candidate.sent == [] for candidate in SlowQuerySession.instances[1:])


async def test_unconfirmed_ack_hook_failure_prevents_fresh_read_and_reconnect() -> None:
    class EvidencePersistenceError(RuntimeError):
        pass

    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0

    def fail_to_persist_ack_unconfirmed(kind: ControlAckFailureKind) -> None:
        nonlocal hook_calls
        assert kind is ControlAckFailureKind.TIMEOUT
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")
        raise EvidencePersistenceError("simulated durable evidence failure")

    with pytest.raises(EvidencePersistenceError, match="durable evidence failure"):
        await device.write_power(
            50,
            on_ack_unconfirmed=fail_to_persist_ack_unconfirmed,
        )

    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 1
    assert session.authenticate_calls == 1
    assert session.read_accept_reports == []
    assert not any(event.startswith("read:") for event in session.events)
    assert all(candidate.connect_calls == 0 for candidate in _FakeSession.instances[1:])


async def test_unconfirmed_ack_resolution_hook_failure_quarantines_before_rollback() -> None:
    class EvidencePersistenceError(RuntimeError):
        pass

    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    original = _FakeSession.instances[0]
    updates = []

    def fail_to_persist_resolution(update) -> None:
        updates.append(update)
        raise EvidencePersistenceError("simulated resolution evidence failure")

    with pytest.raises(EvidencePersistenceError, match="resolution evidence failure"):
        await device.write_power(
            50,
            on_ack_unconfirmed=lambda _kind: None,
            on_ack_resolution=fail_to_persist_resolution,
        )

    assert len(updates) == 1
    assert updates[0].stage is ControlAckResolutionStage.QUARANTINE
    assert updates[0].attempt == 0
    assert updates[0].state is ControlAckResolutionState.STARTED
    assert original.connected is False
    assert original.sent and len(original.sent) == 1
    assert "quarantine" in original.events
    assert all(candidate.connect_calls == 0 for candidate in _FakeSession.instances[1:])

    await device.connect()

    assert device.connected is True
    assert device._session is _FakeSession.instances[1]  # noqa: SLF001
    assert _FakeSession.instances[1].sent == []


async def test_unconfirmed_ack_safety_trip_during_reconnect_prevents_resolution_read() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    allowed = True

    class GuardTripSession(_FakeSession):
        async def connect(self) -> None:
            nonlocal allowed
            await super().connect()
            if self.instance_id == 1:
                allowed = False

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_retry_delay_seconds=0,
        session_factory=GuardTripSession,
    )
    await device.connect()
    session = GuardTripSession.instances[0]

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_power(
            50,
            guard=lambda: allowed,
            on_ack_unconfirmed=lambda _kind: None,
        )

    assert len(session.sent) == 1
    assert session.connect_calls == 1
    assert session.authenticate_calls == 1
    assert session.read_accept_reports == []


async def test_unconfirmed_ack_safety_trip_after_state_report_cannot_become_success() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    allowed = True

    class GuardTripReadSession(_FakeSession):
        async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
            nonlocal allowed
            raw = await super().read_raw_state(accept_reports=accept_reports)
            if self.instance_id == 1:
                allowed = False
            return raw

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_retry_delay_seconds=0,
        session_factory=GuardTripReadSession,
    )
    await device.connect()
    session = GuardTripReadSession.instances[0]

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_power(
            50,
            guard=lambda: allowed,
            on_ack_unconfirmed=lambda _kind: None,
        )

    assert len(session.sent) == 1
    assert session.read_accept_reports == []
    assert GuardTripReadSession.instances[1].read_accept_reports == [True]


async def test_unconfirmed_ack_safety_trip_during_clean_session_replacement_cannot_succeed(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    allowed = True

    class GuardTripReplacementSession(_FakeSession):
        async def connect(self) -> None:
            nonlocal allowed
            await super().connect()
            if self.instance_id == 2:
                allowed = False

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_retry_delay_seconds=0,
        session_factory=GuardTripReplacementSession,
    )
    await device.connect()

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_power(
            50,
            guard=lambda: allowed,
            on_ack_unconfirmed=lambda _kind: None,
        )

    evidence = GuardTripReplacementSession.instances[1]
    replacement = GuardTripReplacementSession.instances[2]
    assert evidence.read_accept_reports == [True]
    assert evidence.connected is False
    assert replacement.connect_calls == 1
    assert replacement.authenticate_calls == 0
    assert replacement.read_accept_reports == []
    assert replacement.connected is False
    assert sum(len(candidate.sent) for candidate in GuardTripReplacementSession.instances) == 1


async def test_unconfirmed_ack_clean_session_replacement_obeys_hard_deadline() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")

    class SlowReplacementSession(_FakeSession):
        async def connect(self) -> None:
            if self.instance_id == 2:
                await asyncio.sleep(1)
            await super().connect()

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_resolution_timeout_seconds=0.05,
        ack_loss_retry_delay_seconds=0,
        session_factory=SlowReplacementSession,
    )
    await device.connect()
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlAckReadbackError) as captured:
        await device.write_power(50, on_ack_unconfirmed=lambda _kind: None)

    elapsed = asyncio.get_running_loop().time() - started
    assert captured.value.stage is ControlAckResolutionStage.CONNECT
    assert captured.value.attempts == 1
    assert elapsed < 0.3
    assert SlowReplacementSession.instances[1].read_accept_reports == [True]
    assert SlowReplacementSession.instances[1].connected is False
    assert SlowReplacementSession.instances[2].read_accept_reports == []
    assert SlowReplacementSession.instances[2].connected is False
    assert sum(len(candidate.sent) for candidate in SlowReplacementSession.instances) == 1


async def test_unconfirmed_ack_mismatch_preserves_command_pacing_across_reconnect() -> None:
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    with pytest.raises(ControlAckPowerMismatchError, match="did not apply control"):
        await device.write_power(50)

    attempted_at = device._last_command_at  # noqa: SLF001
    assert attempted_at is not None
    await device.disconnect()
    await device.connect()
    assert device._last_command_at == attempted_at  # noqa: SLF001


async def test_unconfirmed_live_write_spaces_one_compensating_frame_after_reconnect() -> None:
    _FakeSession.state = _pro_state(power=34)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    sent_at: list[float] = []

    class TimedSession(_FakeSession):
        async def send_raw_control(self, control_payload: bytes) -> bytes:
            sent_at.append(asyncio.get_running_loop().time())
            return await super().send_raw_control(control_payload)

    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=True,
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        ack_loss_retry_delay_seconds=0,
        session_factory=TimedSession,
    )
    await device.connect()
    original_session = TimedSession.instances[0]

    with pytest.raises(ControlAcknowledgementError):
        await device.write_power(38)

    _FakeSession.send_failure = None
    await device.disconnect()
    await device.connect()
    await device.write_power(34)

    assert len(original_session.sent) == 1
    assert sum(len(candidate.sent) for candidate in TimedSession.instances) == 2
    assert len(sent_at) == 2
    assert sent_at[1] - sent_at[0] >= 0.09
    sent_payloads = [
        payload for candidate in TimedSession.instances for payload in candidate.sent
    ]
    assert sent_payloads[0][9 + 2] == 38
    assert sent_payloads[1][9 + 2] == 34


async def test_verified_duplicate_write_is_suppressed() -> None:
    _FakeSession.state = _pro_state(power=50)
    device = _device(allow_writes=True)
    await device.connect()

    await device.set_power(50)
    await device.set_power(50)

    assert len(_FakeSession.instances[0].sent) == 1


async def test_duplicate_cache_does_not_hide_external_or_schedule_drift() -> None:
    _FakeSession.state = _pro_state(power=50)
    device = _device(allow_writes=True)
    await device.connect()
    await device.set_power(50)
    _FakeSession.state = _pro_state(power=40)
    device._last_command_at = None

    with pytest.raises(StateVerificationError, match="did not apply control"):
        await device.set_power(50)

    assert len(_FakeSession.instances[0].sent) == 2


async def test_numeric_mode_with_audited_labels_can_be_previewed() -> None:
    device = _device()

    plan = device.preview_target(DeviceTarget(enabled=True, power=50, mode="tidal"))

    assert plan.changes["Mode"] == "tidal"


async def test_named_mode_profile_can_preview_mode_safely() -> None:
    device = LanJebaoDevice(
        "left",
        "pump.local",
        LOCAL_WAVEMAKER.product_key,
        session_factory=_FakeSession,
    )

    plan = device.preview_target(
        DeviceTarget(enabled=True, power=50, mode="constant", frequency=20)
    )

    assert plan.changes == {
        "SwitchON": True,
        "Flow": 50,
        "Mode": "constant",
        "Frequency": 20,
    }


async def test_runtime_dry_run_overrides_per_device_write_opt_in() -> None:
    config = DeviceConfig(
        id="right",
        name="Right",
        type=DeviceType.WAVEMAKER,
        address="pump.local",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        limits=PowerLimits(min_power=30, max_power=75),
        control=DeviceControlConfig(allow_hardware_writes=True),
    )
    device = create_lan_device(
        config,
        RuntimeConfig(dry_run=True),
        session_factory=_FakeSession,
    )
    await device.connect()

    with pytest.raises(HardwareWritesDisabledError):
        await device.set_power(50)


def test_factory_only_exposes_binding_when_both_stable_identifiers_exist() -> None:
    complete = DeviceConfig(
        id="right",
        name="Right",
        type=DeviceType.WAVEMAKER,
        address="pump.local",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        identity={
            "device_id": "private-vendor-id",
            "mac_address": "00:11:22:33:44:55",
        },
    )
    incomplete_data = complete.model_dump(mode="json")
    incomplete_data["identity"] = {"device_id": "private-vendor-id"}
    incomplete = DeviceConfig.model_validate(incomplete_data)
    discovery_config = complete.model_copy(update={"product_key": None})
    changed_limits = complete.model_copy(
        update={"limits": PowerLimits(min_power=30, max_power=74)}
    )

    bound = create_lan_device(complete, RuntimeConfig(), session_factory=_FakeSession)
    read_only_bound = create_read_only_lan_device(
        discovery_config,
        "new-dhcp-address.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        session_factory=_FakeSession,
    )
    unbound = create_lan_device(incomplete, RuntimeConfig(), session_factory=_FakeSession)
    changed = create_lan_device(changed_limits, RuntimeConfig(), session_factory=_FakeSession)

    assert bound.physical_binding is not None
    assert bound.physical_binding.product_key == LOCAL_WAVEMAKER_PRO.product_key
    serialized = bound.physical_binding.model_dump_json()
    assert "private-vendor-id" not in serialized
    assert "001122334455" not in serialized
    assert read_only_bound.physical_binding == bound.physical_binding
    assert changed.physical_binding != bound.physical_binding
    assert unbound.physical_binding is None


def test_lan_adapter_rejects_binding_for_a_different_product() -> None:
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id="private-vendor-id",
        mac_address="001122334455",
        product_key=LOCAL_WAVEMAKER.product_key,
        config_fingerprint="1" * 64,
    )

    with pytest.raises(ValueError, match="binding product key"):
        LanJebaoDevice(
            "right",
            "pump.local",
            LOCAL_WAVEMAKER_PRO.product_key,
            physical_binding=binding,
            session_factory=_FakeSession,
        )


@pytest.mark.parametrize("timeout", [0.0, -1.0, 55.0001, float("inf"), float("nan")])
def test_lan_adapter_rejects_unbounded_ack_loss_resolution_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="ACK-loss resolution timeout"):
        LanJebaoDevice(
            "right",
            "pump.local",
            LOCAL_WAVEMAKER_PRO.product_key,
            ack_loss_resolution_timeout_seconds=timeout,
            session_factory=_FakeSession,
        )


async def test_pro_schedule_read_returns_exact_image_without_device_clock() -> None:
    first = _schedule_slot(flow=31, start_hour=0, end_hour=8)
    last = _schedule_slot(flow=47, start_hour=22, end_hour=24)
    clock = bytes.fromhex("141a081b000c2238")
    _FakeSession.state = _pro_schedule_state({0: first, 47: last}, clock_marker=clock)
    device = _device()
    await device.connect()

    image = await device.read_schedule_image()

    assert image == extract_local_wavemaker_pro_schedule_image(_FakeSession.state)
    assert len(image) == 432
    assert image[:9] == first
    assert image[-9:] == last
    assert clock not in image
    assert _FakeSession.instances[0].sent == []


async def test_pro_schedule_write_uses_standalone_payload_and_exact_readback() -> None:
    slot_5 = _schedule_slot(flow=32)
    slot_23 = _schedule_slot(flow=48, start_hour=17, end_hour=18)
    _FakeSession.state = _pro_schedule_state({5: slot_5, 23: slot_23})
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    outcome = await device.write_schedule_slots(
        {5: slot_5, 23: slot_23},
        guard=lambda: True,
    )

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED
    assert _FakeSession.instances[0].sent == [
        build_local_wavemaker_pro_schedule_control_payload({5: slot_5, 23: slot_23})
    ]
    assert _FakeSession.instances[0].read_accept_reports == [True]


@pytest.mark.parametrize("flow", [29, 76])
async def test_pro_forward_schedule_write_enforces_configured_power_limits(flow: int) -> None:
    slot = _schedule_slot(flow=flow)
    _FakeSession.state = _pro_schedule_state({6: slot})
    device = _device(allow_writes=True)

    with pytest.raises(ValueError, match="outside configured range"):
        await device.write_schedule_slots({6: slot}, guard=lambda: True)

    assert _FakeSession.instances[0].connect_calls == 0
    assert _FakeSession.instances[0].sent == []


async def test_pro_forward_schedule_write_enforces_configured_power_step() -> None:
    slot = _schedule_slot(flow=32)
    _FakeSession.state = _pro_schedule_state({6: slot})
    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        power_step=5,
        allow_hardware_writes=True,
        session_factory=_FakeSession,
    )

    with pytest.raises(ValueError, match="does not match step 5"):
        await device.write_schedule_slots({6: slot}, guard=lambda: True)

    assert _FakeSession.instances[0].connect_calls == 0
    assert _FakeSession.instances[0].sent == []


async def test_pro_forward_feed_schedule_allows_zero_flow() -> None:
    feed = bytes((8, 0, 9, 0, 7, 0, 0, 15, 0))
    _FakeSession.state = _pro_schedule_state({6: feed})
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    outcome = await device.write_schedule_slots({6: feed}, guard=lambda: True)

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED
    assert _FakeSession.instances[0].sent == [
        build_local_wavemaker_pro_schedule_control_payload({6: feed})
    ]


@pytest.mark.parametrize("flow", [20, 76, 100])
async def test_pro_forward_feed_schedule_rejects_nonzero_flow_outside_limits(
    flow: int,
) -> None:
    feed = bytes((8, 0, 9, 0, 7, flow, 0, 15, 0))
    _FakeSession.state = _pro_schedule_state({6: feed})
    device = _device(allow_writes=True)

    with pytest.raises(ValueError, match="outside configured range"):
        await device.write_schedule_slots({6: feed}, guard=lambda: True)

    assert _FakeSession.instances[0].connect_calls == 0
    assert _FakeSession.instances[0].sent == []


async def test_pro_forward_feed_schedule_enforces_step_for_nonzero_flow() -> None:
    feed = bytes((8, 0, 9, 0, 7, 32, 0, 15, 0))
    _FakeSession.state = _pro_schedule_state({6: feed})
    device = LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        power_step=5,
        allow_hardware_writes=True,
        session_factory=_FakeSession,
    )

    with pytest.raises(ValueError, match="does not match step 5"):
        await device.write_schedule_slots({6: feed}, guard=lambda: True)

    assert _FakeSession.instances[0].connect_calls == 0
    assert _FakeSession.instances[0].sent == []


async def test_pro_exact_schedule_restore_bypasses_forward_limits_and_verifies_full_image() -> None:
    outside_current_limit = _schedule_slot(flow=89)
    raw_state = _pro_schedule_state({0: outside_current_limit})
    image = extract_local_wavemaker_pro_schedule_image(raw_state)
    _FakeSession.state = raw_state
    device = _device(allow_writes=True, minimum_command_interval_ms=100)

    outcome = await device.restore_schedule_image(image, guard=lambda: True)

    expected_slots = {
        index: image[index * 9 : (index + 1) * 9]
        for index in range(48)
    }
    assert outcome is ControlVerificationOutcome.STATE_VERIFIED
    assert _FakeSession.instances[0].sent == [
        build_local_wavemaker_pro_schedule_control_payload(expected_slots)
    ]
    assert _FakeSession.instances[0].connect_calls == 1
    assert _FakeSession.instances[0].authenticate_calls == 1
    assert _FakeSession.instances[0].read_accept_reports == [True]


async def test_cancelled_schedule_send_retires_transport_before_exact_restore() -> None:
    original_state = _pro_schedule_state({0: _schedule_slot(flow=89)})
    original_image = extract_local_wavemaker_pro_schedule_image(original_state)
    temporary = _schedule_slot(flow=38)
    _FakeSession.state = original_state
    _FakeSession.send_failure = asyncio.CancelledError()
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    uncertain_session = _FakeSession.instances[0]

    with pytest.raises(asyncio.CancelledError):
        await device.write_schedule_slots({0: temporary}, guard=lambda: True)

    assert uncertain_session.connected is False
    assert uncertain_session.events[-1] == "quarantine"
    assert uncertain_session.sent == [
        build_local_wavemaker_pro_schedule_control_payload({0: temporary})
    ]

    _FakeSession.send_failure = None
    outcome = await device.restore_schedule_image(original_image, guard=lambda: True)

    replacement = _FakeSession.instances[1]
    expected_slots = {
        index: original_image[index * 9 : (index + 1) * 9]
        for index in range(48)
    }
    assert outcome is ControlVerificationOutcome.STATE_VERIFIED
    assert device._session is replacement  # noqa: SLF001
    assert replacement.connect_calls == 1
    assert replacement.authenticate_calls == 1
    assert replacement.sent == [
        build_local_wavemaker_pro_schedule_control_payload(expected_slots)
    ]
    assert sum(len(session.sent) for session in _FakeSession.instances) == 2


async def test_pro_exact_schedule_restore_mismatch_redacts_both_full_images() -> None:
    requested_state = _pro_schedule_state({0: _schedule_slot(flow=89)})
    actual_state = _pro_schedule_state({0: _schedule_slot(flow=88)})
    requested = extract_local_wavemaker_pro_schedule_image(requested_state)
    actual = extract_local_wavemaker_pro_schedule_image(actual_state)
    _FakeSession.state = actual_state
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    with pytest.raises(ControlStateMismatchError) as captured:
        await device.restore_schedule_image(requested, guard=lambda: True)

    message = str(captured.value)
    assert "ScheduleImage" in message
    assert message.count("<binary>") == 2
    assert requested.hex() not in message
    assert actual.hex() not in message
    assert len(_FakeSession.instances[0].sent) == 1


async def test_pro_schedule_write_is_hardware_locked_and_guarded_before_send() -> None:
    slot = _schedule_slot(flow=38)
    _FakeSession.state = _pro_schedule_state({6: slot})
    image = extract_local_wavemaker_pro_schedule_image(_FakeSession.state)

    locked = _device(allow_writes=False)
    with pytest.raises(HardwareWritesDisabledError):
        await locked.write_schedule_slots({6: slot}, guard=lambda: True)
    with pytest.raises(HardwareWritesDisabledError):
        await locked.restore_schedule_image(image, guard=lambda: True)
    assert _FakeSession.instances[0].connect_calls == 0
    assert _FakeSession.instances[0].sent == []

    guarded = _device(allow_writes=True)
    with pytest.raises(SafetyInterlockError):
        await guarded.write_schedule_slots({6: slot}, guard=lambda: False)
    with pytest.raises(SafetyInterlockError):
        await guarded.restore_schedule_image(image, guard=lambda: False)
    assert _FakeSession.instances[1].connect_calls == 0
    assert _FakeSession.instances[1].sent == []


async def test_pro_schedule_ack_loss_accepts_fresh_report_without_control_replay() -> None:
    slot = _schedule_slot(flow=38)
    _FakeSession.state = _pro_schedule_state({7: slot})
    _FakeSession.send_failure = ProtocolTimeoutError("private transport endpoint")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    outcome = await device.write_schedule_slots({7: slot}, guard=lambda: True)

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK
    assert len(_FakeSession.instances[0].sent) == 1
    assert _FakeSession.instances[0].sent[0] == (
        build_local_wavemaker_pro_schedule_control_payload({7: slot})
    )
    assert len(_FakeSession.instances) == 3
    assert _FakeSession.instances[1].sent == []
    assert _FakeSession.instances[1].read_accept_reports == [True]
    assert _FakeSession.instances[1].connected is False
    assert _FakeSession.instances[2].sent == []
    assert _FakeSession.instances[2].read_accept_reports == []
    assert _FakeSession.instances[2].connected is True


async def test_pro_schedule_ack_loss_mismatch_is_typed_and_redacts_raw_bytes() -> None:
    actual = _schedule_slot(flow=33)
    requested = _schedule_slot(flow=38)
    _FakeSession.state = _pro_schedule_state({7: actual})
    _FakeSession.send_failure = ProtocolTimeoutError("private transport endpoint")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()

    with pytest.raises(ControlAckStateMismatchError) as captured:
        await device.write_schedule_slots({7: requested}, guard=lambda: True)

    message = str(captured.value)
    assert "AutoTime07" in message
    assert message.count("<binary>") == 2
    assert repr(requested) not in message
    assert repr(actual) not in message
    assert requested.hex() not in message
    assert actual.hex() not in message
    assert len(_FakeSession.instances[0].sent) == 1
    assert all(session.sent == [] for session in _FakeSession.instances[1:])
    assert all(
        session.read_accept_reports == [True] for session in _FakeSession.instances[1:9]
    )


async def test_non_pro_product_rejects_schedule_read_and_write_without_io() -> None:
    device = LanJebaoDevice(
        "legacy",
        "pump.local",
        LOCAL_WAVEMAKER.product_key,
        allow_hardware_writes=True,
        session_factory=_FakeSession,
    )
    slot = _schedule_slot(flow=38)

    with pytest.raises(UnsupportedCapabilityError, match="writable schedule image"):
        await device.read_schedule_image()
    with pytest.raises(UnsupportedCapabilityError, match="writable schedule image"):
        await device.write_schedule_slots({0: slot}, guard=lambda: True)
    with pytest.raises(UnsupportedCapabilityError, match="writable schedule image"):
        await device.restore_schedule_image(LOCAL_WAVEMAKER_PRO_UNUSED_EE * 48)

    assert _FakeSession.instances[0].connect_calls == 0
    assert _FakeSession.instances[0].read_accept_reports == []
    assert _FakeSession.instances[0].sent == []
