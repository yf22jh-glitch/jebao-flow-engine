import asyncio

import pytest

from jebao_flow.devices import (
    DeviceConnectionError,
    SafetyInterlockError,
    SimulatedJebaoDevice,
)
from jebao_flow.protocol.models import DeviceTarget, LinkageRole


async def test_simulator_tracks_commands_and_state() -> None:
    device = SimulatedJebaoDevice("left")

    await device.connect()
    await device.set_enabled(True)
    await device.set_power(65)
    await device.set_mode("wave")
    await device.set_frequency(40)
    state = await device.get_state()

    assert state.online is True
    assert state.enabled is True
    assert state.power == 65
    assert state.mode == "wave"
    assert state.frequency == 40
    assert [(command.name, command.value) for command in device.commands] == [
        ("enabled", True),
        ("power", 65),
        ("mode", "wave"),
        ("frequency", 40),
    ]


async def test_simulator_rejects_io_while_disconnected() -> None:
    device = SimulatedJebaoDevice("left")

    with pytest.raises(DeviceConnectionError):
        await device.get_state()


async def test_simulator_enforces_capability_power_range() -> None:
    device = SimulatedJebaoDevice("left")
    await device.connect()

    with pytest.raises(ValueError, match="outside simulated device range"):
        await device.set_power(20)


async def test_simulated_connection_loss_changes_availability() -> None:
    device = SimulatedJebaoDevice("left")
    await device.connect()

    await device.simulate_connection_loss("wifi unavailable")

    assert device.connected is False
    with pytest.raises(DeviceConnectionError):
        await device.get_state()


async def test_simulator_applies_atomic_linkage_target_and_timer_while_disabled() -> None:
    device = SimulatedJebaoDevice("left")
    await device.connect()

    await device.write_target(
        DeviceTarget(
            enabled=False,
            power=0,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
    )

    state = await device.get_state()
    assert state.enabled is False
    assert state.linkage is LinkageRole.INDEPENDENT
    assert state.timer_enabled is False
    assert [(command.name, command.value) for command in device.commands] == [
        ("enabled", False),
        ("timer_enabled", False),
        ("linkage", LinkageRole.INDEPENDENT),
    ]


async def test_guard_is_rechecked_inside_device_write_lock() -> None:
    allowed = {"value": True}
    device = SimulatedJebaoDevice("left", latency_seconds=0.01)
    await device.connect()
    before = await device.get_state()
    task = asyncio.create_task(
        device.write_target(
            DeviceTarget(enabled=True, power=50),
            guard=lambda: allowed["value"],
        )
    )

    await asyncio.sleep(0.001)
    allowed["value"] = False

    with pytest.raises(SafetyInterlockError, match="safety interlock"):
        await task
    after = await device.get_state()
    assert after.enabled == before.enabled
    assert after.power == before.power
