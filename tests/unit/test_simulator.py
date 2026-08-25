import pytest

from jebao_flow.devices import DeviceConnectionError, SimulatedJebaoDevice


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

