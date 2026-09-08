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
    assert values["Mode"] == "constant"
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
    assert pro_values["Mode"] == "constant"
    assert pro_values["Flow"] == 30
    assert DOSING_PUMP.decode_status(bytes(dosing))["switch"] is False


@pytest.mark.parametrize(
    ("schema", "first_id", "slot_offset", "slot_size", "ymd_id", "hms_id"),
    (
        (LOCAL_WAVEMAKER_PRO, 13, 11, 9, 61, 62),
        (LOCAL_WAVEMAKER, 14, 8, 8, 62, 63),
        (DC_PUMP_PRO, 10, 9, 8, 58, 59),
        (AQUARIUM_PUMP, 11, 13, 6, 9, 10),
    ),
)
def test_schedule_products_include_complete_vendor_binary_layout(
    schema,
    first_id: int,
    slot_offset: int,
    slot_size: int,
    ymd_id: int,
    hms_id: int,
) -> None:
    slots = tuple(schema.by_name(f"AutoTime{index:02d}") for index in range(48))

    assert [item.id for item in slots] == list(range(first_id, first_id + 48))
    assert [item.position.byte_offset for item in slots] == [
        slot_offset + index * slot_size for index in range(48)
    ]
    assert all(item.position.length == slot_size for item in slots)
    assert schema.by_name("YMDData").id == ymd_id
    assert schema.by_name("HMSData").id == hms_id
    assert schema.by_name("YMDData").position.length == 4
    assert schema.by_name("HMSData").position.length == 4


def test_dosing_profile_decodes_all_vendor_declared_channel_images_and_duration() -> None:
    raw = bytearray(DOSING_PUMP.raw_status_size)
    channel_images = tuple(bytes([index]) * 96 for index in range(1, 5))
    for offset, image in zip((7, 103, 199, 295), channel_images, strict=True):
        raw[offset : offset + len(image)] = image
    raw[391] = 42

    values = DOSING_PUMP.decode_status(bytes(raw))

    assert tuple(values[f"CH{index}SWTime"] for index in range(1, 5)) == channel_images
    assert values["time1"] == 42


def test_status_size_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be 401 bytes"):
        LOCAL_WAVEMAKER.decode_status(bytes(400))


def test_unknown_product_key_is_rejected() -> None:
    with pytest.raises(KeyError, match="unsupported product key"):
        get_product_schema("unknown")
