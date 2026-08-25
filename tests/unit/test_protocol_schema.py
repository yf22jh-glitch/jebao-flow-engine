import pytest

from jebao_flow.protocol.profiles import (
    AQUARIUM_PUMP,
    DC_PUMP_PRO,
    DOSING_PUMP,
    LOCAL_WAVEMAKER,
    LOCAL_WAVEMAKER_PRO,
    get_product_schema,
)


def test_dc_pump_status_decodes_operational_fields_and_faults() -> None:
    raw = bytearray(DC_PUMP_PRO.raw_status_size)
    raw[:9] = bytes([0b00000011, 0, 81, 5, 10, 4, 0, 5, 30])
    raw[401] = 0b00010000

    values = DC_PUMP_PRO.decode_status(bytes(raw))

    assert values["SwitchON"] is True
    assert values["TimerON"] is True
    assert values["Flow"] == 81
    assert values["Frequency"] == 5
    assert DC_PUMP_PRO.active_problems(values) == ("Fault_Lockedrotor",)


def test_multibyte_bit_group_uses_big_endian_integer_layout() -> None:
    raw = bytearray(LOCAL_WAVEMAKER.raw_status_size)
    raw[:9] = bytes.fromhex("06015b170a64640000")

    values = LOCAL_WAVEMAKER.decode_status(bytes(raw))

    assert values["SwitchON"] is True
    assert values["Mode"] == "classic"
    assert values["Linkage"] == "independent"
    assert values["AutoMode"] == "random"
    assert values["Flow"] == 91
    assert values["Frequency"] == 23


def test_known_live_profile_shapes_decode_without_copying_schedule_fields() -> None:
    aquarium = bytearray(AQUARIUM_PUMP.raw_status_size)
    aquarium[:5] = bytes.fromhex("113c0a6400")
    pro = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    pro[:11] = bytes.fromhex("03021e20050a0437050500")
    dosing = bytearray(DOSING_PUMP.raw_status_size)

    assert AQUARIUM_PUMP.decode_status(bytes(aquarium))["Motor_Speed"] == 60
    pro_values = LOCAL_WAVEMAKER_PRO.decode_status(bytes(pro))
    assert pro_values["Linkage"] == "independent"
    assert pro_values["Flow"] == 30
    assert DOSING_PUMP.decode_status(bytes(dosing))["switch"] is False


def test_status_size_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be 401 bytes"):
        LOCAL_WAVEMAKER.decode_status(bytes(400))


def test_unknown_product_key_is_rejected() -> None:
    with pytest.raises(KeyError, match="unsupported product key"):
        get_product_schema("unknown")
