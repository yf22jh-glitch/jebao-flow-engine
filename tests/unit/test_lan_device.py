from typing import ClassVar

import pytest

from jebao_flow.devices import (
    HardwareWritesDisabledError,
    LanJebaoDevice,
    StateVerificationError,
    UnsupportedCapabilityError,
)
from jebao_flow.protocol.models import DeviceTarget
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO
from jebao_flow.safety.limits import PowerLimits


def _pro_state(*, enabled: bool = True, power: int = 30, fault: int = 0) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = int(enabled)
    raw[1] = 2
    raw[2] = power
    raw[3] = 32
    raw[451] = fault
    return bytes(raw)


class _FakeSession:
    instances: ClassVar[list["_FakeSession"]] = []
    state = _pro_state()

    def __init__(self, address: str) -> None:
        self.address = address
        self.connected = False
        self.sent: list[bytes] = []
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def authenticate(self) -> bytes:
        return b"never-logged"

    async def read_raw_state(self) -> bytes:
        return self.state

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.sent.append(control_payload)
        return b"ack"


@pytest.fixture(autouse=True)
def _reset_fake_session() -> None:
    _FakeSession.instances.clear()
    _FakeSession.state = _pro_state()


def _device(*, allow_writes: bool = False) -> LanJebaoDevice:
    return LanJebaoDevice(
        "right",
        "pump.local",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        allow_hardware_writes=allow_writes,
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
    assert state.mode == "raw_2"
    assert state.frequency == 32
    assert state.error == "Fault_UART"


async def test_preview_builds_atomic_target_without_sending() -> None:
    device = _device()

    plan = device.preview_target(DeviceTarget(enabled=True, power=50))

    assert plan.changes == {"SwitchON": True, "Flow": 50}
    assert plan.payload[0] == 0x01
    assert _FakeSession.instances[0].sent == []


async def test_hardware_write_lock_is_default() -> None:
    device = _device()
    await device.connect()

    with pytest.raises(HardwareWritesDisabledError, match="writes are locked"):
        await device.set_power(50)

    assert _FakeSession.instances[0].sent == []


@pytest.mark.parametrize("power", [0, 29, 76, 100])
def test_preview_enforces_configured_power_limits(power: int) -> None:
    device = _device()

    with pytest.raises(ValueError, match="configured range"):
        device.preview_target(DeviceTarget(enabled=True, power=power))


def test_disabled_target_only_writes_switch_and_never_zero_flow() -> None:
    device = _device()

    plan = device.preview_target(DeviceTarget(enabled=False, power=0))

    assert plan.changes == {"SwitchON": False}


async def test_write_requires_readback_match() -> None:
    device = _device(allow_writes=True)
    await device.connect()

    with pytest.raises(StateVerificationError, match="did not apply control"):
        await device.set_power(50)

    assert len(_FakeSession.instances[0].sent) == 1


async def test_verified_duplicate_write_is_suppressed() -> None:
    _FakeSession.state = _pro_state(power=50)
    device = _device(allow_writes=True)
    await device.connect()

    await device.set_power(50)
    await device.set_power(50)

    assert len(_FakeSession.instances[0].sent) == 1


async def test_unmapped_numeric_mode_cannot_be_written() -> None:
    device = _device()

    with pytest.raises(UnsupportedCapabilityError, match="have not been mapped safely"):
        device.preview_target(DeviceTarget(enabled=True, power=50, mode="constant"))


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
