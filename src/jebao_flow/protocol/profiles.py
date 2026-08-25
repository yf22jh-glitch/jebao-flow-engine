"""Audited operational profiles for the six observed local devices."""

from __future__ import annotations

from jebao_flow.protocol.schema import (
    Datapoint,
    DatapointKind,
    DataType,
    NumericSpec,
    Position,
    PositionUnit,
    ProductSchema,
)


def _bool(
    id_: int,
    name: str,
    bit_offset: int,
    *,
    byte_offset: int = 0,
    kind: DatapointKind = DatapointKind.WRITABLE,
) -> Datapoint:
    return Datapoint(
        id=id_,
        name=name,
        data_type=DataType.BOOL,
        kind=kind,
        position=Position(
            byte_offset=byte_offset,
            bit_offset=bit_offset,
            unit=PositionUnit.BIT,
        ),
    )


def _enum(id_: int, name: str, bit_offset: int, length: int, values: tuple[str, ...]) -> Datapoint:
    return Datapoint(
        id=id_,
        name=name,
        data_type=DataType.ENUM,
        kind=DatapointKind.WRITABLE,
        position=Position(
            byte_offset=0,
            bit_offset=bit_offset,
            length=length,
            unit=PositionUnit.BIT,
        ),
        enum_values=values,
    )


def _uint8(
    id_: int,
    name: str,
    byte_offset: int,
    minimum: int,
    maximum: int,
    values: tuple[str, ...] = (),
) -> Datapoint:
    return Datapoint(
        id=id_,
        name=name,
        data_type=DataType.UINT8,
        kind=DatapointKind.WRITABLE,
        position=Position(byte_offset=byte_offset),
        enum_values=values,
        numeric=NumericSpec(minimum=minimum, maximum=maximum),
    )


def _faults(start_id: int, byte_offset: int) -> tuple[Datapoint, ...]:
    names = (
        "Fault_Overcurrent",
        "Fault_Overvoltage",
        "Fault_OverTemp",
        "Fault_Undervoltage",
        "Fault_Lockedrotor",
        "Fault_no_liveload",
        "Fault_UART",
    )
    return tuple(
        _bool(
            start_id + bit,
            name,
            bit,
            byte_offset=byte_offset,
            kind=DatapointKind.FAULT,
        )
        for bit, name in enumerate(names)
    )


DC_PUMP_PRO = ProductSchema(
    name="DC Pump Pro (WiFi+BLE)",
    product_key="0696a19599bc484f8e1866f5ccf4ee7e",
    raw_status_size=402,
    attribute_flags_size=8,
    attribute_values_size=401,
    bit_group_width=1,
    attributes=(
        _bool(0, "SwitchON", 0),
        _bool(1, "TimerON", 1),
        _uint8(2, "Mode", 1, 0, 255, ("constant", "pulse", "sine", "random", "feed")),
        _uint8(3, "Flow", 2, 0, 100),
        _uint8(4, "Frequency", 3, 0, 100),
        _uint8(5, "FeedTime", 4, 1, 60),
        _uint8(
            6,
            "AutoMode",
            5,
            0,
            255,
            ("constant", "pulse", "sine", "random", "feed"),
        ),
        _uint8(7, "AutoFlow", 6, 0, 100),
        _uint8(8, "AutoFreq", 7, 0, 100),
        _uint8(9, "AutoFeedTime", 8, 1, 60),
        *_faults(60, 401),
    ),
    enabled_attribute="SwitchON",
    power_attribute="Flow",
    mode_attribute="Mode",
    frequency_attribute="Frequency",
)


DOSING_PUMP = ProductSchema(
    name="Dosing Pump (no AP time-sync)",
    product_key="5b3c136fd4b74f3fb2a366a254c76c9a",
    raw_status_size=394,
    attribute_flags_size=3,
    attribute_values_size=391,
    bit_group_width=2,
    attributes=(
        _bool(0, "switch", 0),
        _bool(1, "channe1", 1),
        _bool(2, "channe2", 2),
        _bool(3, "channe3", 3),
        _bool(4, "channe4", 4),
        _bool(5, "Timer1ON", 5),
        _bool(6, "Timer2ON", 6),
        _bool(7, "Timer3ON", 7),
        _bool(8, "Timer4ON", 8),
        _bool(9, "CALSW", 9),
        _enum(10, "CALSet", 10, 2, ("calibrate_1", "calibrate_2", "calibrate_3", "calibrate_4")),
        _uint8(11, "IntervalT1", 2, 0, 30),
        _uint8(12, "IntervalT2", 3, 0, 30),
        _uint8(13, "IntervalT3", 4, 0, 30),
        _uint8(14, "IntervalT4", 5, 0, 30),
        _uint8(15, "Calib1", 6, 10, 100),
        _bool(21, "OpenCircuit", 0, byte_offset=392, kind=DatapointKind.ALERT),
        _bool(22, "Fault_UART", 0, byte_offset=393, kind=DatapointKind.FAULT),
    ),
    enabled_attribute="switch",
    control_supported=False,
)


LOCAL_WAVEMAKER = ProductSchema(
    name="Local Wavemaker (with AP time-sync)",
    product_key="1d8c63eaccac4205b92c84d77d5a08fb",
    raw_status_size=401,
    attribute_flags_size=8,
    attribute_values_size=400,
    bit_group_width=2,
    attributes=(
        _bool(0, "SwitchON", 0),
        _bool(1, "PulseTide", 1),
        _bool(2, "FeedSwitch", 2),
        _bool(3, "TimerON", 3),
        _bool(4, "AutoPulseTide", 4),
        _enum(5, "Mode", 5, 2, ("classic", "sine", "random", "constant")),
        _enum(6, "Linkage", 7, 2, ("independent", "master", "slave")),
        _enum(7, "AutoMode", 9, 3, ("stopped", "classic", "sine", "random", "constant", "feed")),
        _uint8(8, "Flow", 2, 0, 100),
        _uint8(9, "Frequency", 3, 0, 100),
        _uint8(10, "FeedTime", 4, 1, 60),
        _uint8(11, "AutoFlow", 5, 0, 100),
        _uint8(12, "AutoFreq", 6, 0, 100),
        _uint8(13, "AutoFeedTime", 7, 1, 60),
        *_faults(64, 400),
    ),
    enabled_attribute="SwitchON",
    power_attribute="Flow",
    mode_attribute="Mode",
    frequency_attribute="Frequency",
)


AQUARIUM_PUMP = ProductSchema(
    name="Aquarium Pump (WiFi+BLE)",
    product_key="6a5c47b3ea364ecb841b47f5997a1775",
    raw_status_size=302,
    attribute_flags_size=8,
    attribute_values_size=301,
    bit_group_width=1,
    attributes=(
        _bool(0, "SwitchON", 0),
        _bool(1, "Mode", 1),
        _bool(2, "FeedSwitch", 2),
        _bool(3, "TimerON", 3),
        _enum(4, "AutoMode", 4, 2, ("stopped", "auto", "feed")),
        _uint8(5, "Motor_Speed", 1, 0, 100),
        _uint8(6, "FeedTime", 2, 1, 60),
        _uint8(7, "AutoGears", 3, 0, 100),
        _uint8(8, "AutoFeedTime", 4, 1, 60),
        *_faults(59, 301),
    ),
    enabled_attribute="SwitchON",
    power_attribute="Motor_Speed",
)


LOCAL_WAVEMAKER_PRO = ProductSchema(
    name="Local Wavemaker Pro (WiFi+BLE)",
    product_key="50dbc92221fd4d33ae69a1fedd43b555",
    raw_status_size=452,
    attribute_flags_size=8,
    attribute_values_size=451,
    bit_group_width=1,
    attributes=(
        _bool(0, "SwitchON", 0),
        _bool(1, "TimerON", 1),
        _enum(2, "Linkage", 2, 2, ("independent", "master", "sync_slave", "async_slave")),
        _uint8(
            3,
            "Mode",
            1,
            0,
            255,
            (
                "pulse",
                "sine",
                "constant",
                "random",
                "tidal",
                "nutrient_transport",
                "circulation",
                "feed",
                "custom",
            ),
        ),
        _uint8(4, "Flow", 2, 0, 100),
        _uint8(5, "Frequency", 3, 0, 100),
        _uint8(6, "Cust_Wav_Freq", 4, 0, 100),
        _uint8(7, "FeedTime", 5, 1, 60),
        _uint8(
            8,
            "AutoMode",
            6,
            0,
            255,
            (
                "pulse",
                "sine",
                "constant",
                "random",
                "tidal",
                "nutrient_transport",
                "circulation",
                "feed",
                "custom",
            ),
        ),
        _uint8(9, "AutoFlow", 7, 0, 100),
        _uint8(10, "AutoFreq", 8, 0, 100),
        _uint8(11, "Auto_Cust_Wav_Freq", 9, 0, 100),
        _uint8(12, "AutoFeedTime", 10, 1, 60),
        *_faults(63, 451),
    ),
    enabled_attribute="SwitchON",
    power_attribute="Flow",
    mode_attribute="Mode",
    frequency_attribute="Frequency",
)


KNOWN_SCHEMAS = {
    schema.product_key: schema
    for schema in (
        DC_PUMP_PRO,
        DOSING_PUMP,
        LOCAL_WAVEMAKER,
        AQUARIUM_PUMP,
        LOCAL_WAVEMAKER_PRO,
    )
}


def get_product_schema(product_key: str) -> ProductSchema:
    try:
        return KNOWN_SCHEMAS[product_key]
    except KeyError as error:
        raise KeyError(f"unsupported product key {product_key!r}") from error
