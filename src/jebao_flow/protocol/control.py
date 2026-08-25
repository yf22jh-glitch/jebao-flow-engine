"""Offline construction of schema-validated Gizwits control payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jebao_flow.protocol.schema import Datapoint, DataType, PositionUnit, ProductSchema

CONTROL_ACTION = 0x01


def build_control_payload(schema: ProductSchema, changes: Mapping[str, Any]) -> bytes:
    """Build action + reversed attribute flags + attribute values.

    The function only creates bytes. Sending them remains the responsibility of the guarded device
    adapter, which applies operational limits, command spacing and read-back verification.
    """

    if not schema.control_supported:
        raise ValueError(f"control is not enabled for product {schema.product_key}")
    if not changes:
        raise ValueError("at least one datapoint change is required")

    flags = bytearray(schema.attribute_flags_size)
    values = bytearray(schema.attribute_values_size)

    for name, requested in changes.items():
        attribute = schema.by_name(name)
        if not attribute.writable:
            raise ValueError(f"datapoint {name!r} is not writable")
        flags[attribute.id // 8] |= 1 << (attribute.id % 8)
        _place_value(schema, values, attribute, requested)

    return bytes([CONTROL_ACTION]) + bytes(reversed(flags)) + bytes(values)


def _place_value(
    schema: ProductSchema,
    destination: bytearray,
    attribute: Datapoint,
    requested: Any,
) -> None:
    position = attribute.position
    if position.unit is PositionUnit.BIT:
        raw_value = _encode_bit_value(attribute, requested)
        maximum = (1 << position.length) - 1
        if not 0 <= raw_value <= maximum:
            raise ValueError(f"{attribute.name}: value {raw_value} is outside 0..{maximum}")
        _place_bits(
            schema,
            destination,
            position.byte_offset,
            position.bit_offset,
            position.length,
            raw_value,
        )
        return

    if attribute.data_type is DataType.BINARY:
        raw_bytes = bytes(requested)
        if len(raw_bytes) != position.length:
            raise ValueError(
                f"{attribute.name}: expected {position.length} bytes, got {len(raw_bytes)}"
            )
        destination[position.byte_offset : position.byte_offset + position.length] = raw_bytes
        return

    if attribute.data_type not in {DataType.UINT8, DataType.UINT16}:
        raise ValueError(f"writing {attribute.data_type.value!r} is not implemented")
    numeric = attribute.numeric
    if numeric is None:
        raise AssertionError("numeric datapoint is missing its specification")
    if isinstance(requested, str) and attribute.enum_values:
        try:
            numeric_requested = float(attribute.enum_values.index(requested))
        except ValueError as error:
            choices = ", ".join(attribute.enum_values)
            raise ValueError(f"{attribute.name}: expected one of {choices}") from error
    else:
        numeric_requested = float(requested)
    unrounded = (numeric_requested - numeric.addition) / numeric.ratio
    raw_value = round(unrounded)
    if abs(unrounded - raw_value) > 1e-9:
        raise ValueError(f"{attribute.name}: value {requested!r} does not match its wire step")
    if not numeric.minimum <= raw_value <= numeric.maximum:
        raise ValueError(
            f"{attribute.name}: value {requested!r} is outside "
            f"{numeric.minimum}..{numeric.maximum}"
        )
    destination[position.byte_offset : position.byte_offset + position.length] = raw_value.to_bytes(
        position.length, "big"
    )


def _encode_bit_value(attribute: Datapoint, requested: Any) -> int:
    if attribute.data_type is DataType.BOOL:
        if not isinstance(requested, bool):
            raise TypeError(f"{attribute.name}: expected a boolean")
        return int(requested)
    if attribute.data_type is DataType.ENUM:
        if isinstance(requested, str):
            try:
                return attribute.enum_values.index(requested)
            except ValueError as error:
                choices = ", ".join(attribute.enum_values)
                raise ValueError(f"{attribute.name}: expected one of {choices}") from error
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise TypeError(f"{attribute.name}: expected an enum name or integer index")
        return requested
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise TypeError(f"{attribute.name}: expected an integer")
    return requested


def _place_bits(
    schema: ProductSchema,
    destination: bytearray,
    byte_offset: int,
    bit_offset: int,
    length: int,
    value: int,
) -> None:
    if byte_offset == 0 and schema.bit_group_width > 1:
        width = schema.bit_group_width
        group = int.from_bytes(destination[:width], "big")
        mask = ((1 << length) - 1) << bit_offset
        group = (group & ~mask) | ((value << bit_offset) & mask)
        destination[:width] = group.to_bytes(width, "big")
        return

    for index in range(length):
        local_bit = bit_offset + index
        target_byte = byte_offset + (local_bit // 8)
        target_bit = local_bit % 8
        mask = 1 << target_bit
        if (value >> index) & 1:
            destination[target_byte] |= mask
        else:
            destination[target_byte] &= ~mask & 0xFF
