import asyncio
from typing import ClassVar

import pytest

from jebao_flow.config import DeviceConfig, DeviceControlConfig, DeviceType, RuntimeConfig
from jebao_flow.devices import (
    ControlAcknowledgementError,
    ControlAckPowerMismatchError,
    ControlAckReadbackError,
    ControlReadbackError,
    ControlStateMismatchError,
    ControlVerificationOutcome,
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
from jebao_flow.protocol.errors import ProtocolTimeoutError
from jebao_flow.protocol.models import Capability, DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO
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


class _FakeSession:
    instances: ClassVar[list["_FakeSession"]] = []
    state = _pro_state()
    read_failures_remaining = 0
    read_failures_disconnect = False
    send_failure: Exception | None = None

    def __init__(self, address: str) -> None:
        self.address = address
        self.connected = False
        self.connect_calls = 0
        self.authenticate_calls = 0
        self.sent: list[bytes] = []
        self.read_accept_reports: list[bool] = []
        self.events: list[str] = []
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        self.connect_calls += 1
        self.events.append("connect")
        self.connected = True

    async def disconnect(self) -> None:
        self.events.append("disconnect")
        self.connected = False

    async def authenticate(self) -> bytes:
        self.authenticate_calls += 1
        self.events.append("authenticate")
        return b"never-logged"

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
        self.read_accept_reports.append(accept_reports)
        self.events.append(f"read:{'reports' if accept_reports else 'reply-only'}")
        if self.__class__.read_failures_remaining:
            self.__class__.read_failures_remaining -= 1
            if self.__class__.read_failures_disconnect:
                self.connected = False
            raise ProtocolTimeoutError("simulated transient read timeout")
        return self.state

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.events.append("send-control")
        self.sent.append(control_payload)
        if self.__class__.send_failure is not None:
            raise self.__class__.send_failure
        return b"ack"


@pytest.fixture(autouse=True)
def _reset_fake_session() -> None:
    _FakeSession.instances.clear()
    _FakeSession.state = _pro_state()
    _FakeSession.read_failures_remaining = 0
    _FakeSession.read_failures_disconnect = False
    _FakeSession.send_failure = None


def _device(
    *,
    allow_writes: bool = False,
    minimum_command_interval_ms: int = 1000,
) -> LanJebaoDevice:
    return LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=allow_writes,
        minimum_command_interval_ms=minimum_command_interval_ms,
        readback_delay_ms=0,
        session_factory=_FakeSession,
    )


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


async def test_unconfirmed_ack_hook_precedes_strict_fresh_read_and_returns_verified_outcome(
) -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0

    def persist_ack_unconfirmed() -> None:
        nonlocal hook_calls
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")

    outcome = await device.write_power(
        50,
        on_ack_unconfirmed=persist_ack_unconfirmed,
    )

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK
    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 2
    assert session.authenticate_calls == 2
    assert session.read_accept_reports == [False]
    hook_index = session.events.index("ack-unconfirmed-hook")
    assert hook_index > session.events.index("send-control")
    assert hook_index < session.events.index("connect", 1)
    assert hook_index < session.events.index("read:reply-only")


async def test_unconfirmed_ack_fresh_mismatch_is_typed_without_resending() -> None:
    _FakeSession.state = _pro_state(power=34)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0

    def persist_ack_unconfirmed() -> None:
        nonlocal hook_calls
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")

    with pytest.raises(ControlAckPowerMismatchError, match="did not apply control"):
        await device.write_power(
            50,
            on_ack_unconfirmed=persist_ack_unconfirmed,
        )

    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 2
    assert session.authenticate_calls == 2
    assert session.read_accept_reports == [False, False, False]
    assert session.events.index("ack-unconfirmed-hook") < session.events.index(
        "read:reply-only"
    )


async def test_unconfirmed_ack_fresh_read_unavailable_is_typed_without_resending() -> None:
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    _FakeSession.read_failures_remaining = 3
    _FakeSession.read_failures_disconnect = True
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0

    def persist_ack_unconfirmed() -> None:
        nonlocal hook_calls
        hook_calls += 1
        session.events.append("ack-unconfirmed-hook")

    with pytest.raises(ControlAckReadbackError, match="3 readback attempts"):
        await device.write_power(
            50,
            on_ack_unconfirmed=persist_ack_unconfirmed,
        )

    assert hook_calls == 1
    assert len(session.sent) == 1
    assert session.connect_calls == 4
    assert session.authenticate_calls == 4
    assert session.read_accept_reports == [False, False, False]
    assert session.events.index("ack-unconfirmed-hook") < session.events.index(
        "read:reply-only"
    )


async def test_unconfirmed_ack_hook_failure_prevents_fresh_read_and_reconnect() -> None:
    class EvidencePersistenceError(RuntimeError):
        pass

    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    hook_calls = 0

    def fail_to_persist_ack_unconfirmed() -> None:
        nonlocal hook_calls
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
    assert "read:reply-only" not in session.events


async def test_unconfirmed_ack_safety_trip_during_reconnect_prevents_resolution_read() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    original_connect = session.connect
    allowed = True

    async def reconnect_then_trip_guard() -> None:
        nonlocal allowed
        await original_connect()
        if session.connect_calls == 2:
            allowed = False

    session.connect = reconnect_then_trip_guard  # type: ignore[method-assign]

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_power(
            50,
            guard=lambda: allowed,
            on_ack_unconfirmed=lambda: None,
        )

    assert len(session.sent) == 1
    assert session.connect_calls == 2
    assert session.authenticate_calls == 1
    assert session.read_accept_reports == []


async def test_unconfirmed_ack_safety_trip_during_read_cannot_become_success() -> None:
    _FakeSession.state = _pro_state(power=50)
    _FakeSession.send_failure = ProtocolTimeoutError("simulated missing control ACK")
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    original_read = session.read_raw_state
    allowed = True

    async def read_then_trip_guard(*, accept_reports: bool = True) -> bytes:
        nonlocal allowed
        raw = await original_read(accept_reports=accept_reports)
        allowed = False
        return raw

    session.read_raw_state = read_then_trip_guard  # type: ignore[method-assign]

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await device.write_power(
            50,
            guard=lambda: allowed,
            on_ack_unconfirmed=lambda: None,
        )

    assert len(session.sent) == 1
    assert session.read_accept_reports == [False]


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
    device = _device(allow_writes=True, minimum_command_interval_ms=100)
    await device.connect()
    session = _FakeSession.instances[0]
    sent_at: list[float] = []
    original_send = session.send_raw_control

    async def timed_send(payload: bytes) -> bytes:
        sent_at.append(asyncio.get_running_loop().time())
        return await original_send(payload)

    session.send_raw_control = timed_send  # type: ignore[method-assign]

    with pytest.raises(ControlAcknowledgementError):
        await device.write_power(38)

    _FakeSession.send_failure = None
    await device.disconnect()
    await device.connect()
    await device.write_power(34)

    assert len(session.sent) == 2
    assert len(sent_at) == 2
    assert sent_at[1] - sent_at[0] >= 0.09
    assert session.sent[0][9 + 2] == 38
    assert session.sent[1][9 + 2] == 34


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
