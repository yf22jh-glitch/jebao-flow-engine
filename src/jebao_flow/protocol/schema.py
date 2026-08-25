"""Product datapoint schemas and strict status decoding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class DataType(StrEnum):
    BOOL = "bool"
    ENUM = "enum"
    UINT8 = "uint8"
    UINT16 = "uint16"
    BINARY = "binary"


class DatapointKind(StrEnum):
    WRITABLE = "status_writable"
    READ_ONLY = "status_readonly"
    FAULT = "fault"
    ALERT = "alert"


class PositionUnit(StrEnum):
    BIT = "bit"
    BYTE = "byte"


@dataclass(frozen=True, slots=True)
class Position:
    byte_offset: int
    bit_offset: int = 0
    length: int = 1
    unit: PositionUnit = PositionUnit.BYTE

    def __post_init__(self) -> None:
        if self.byte_offset < 0 or self.bit_offset < 0:
            raise ValueError("datapoint offsets must be non-negative")
        if self.length <= 0:
            raise ValueError("datapoint length must be positive")


@dataclass(frozen=True, slots=True)
class NumericSpec:
    minimum: int
    maximum: int
    addition: float = 0
    ratio: float = 1

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            raise ValueError("numeric minimum must not exceed maximum")
        if self.ratio == 0:
            raise ValueError("numeric ratio must not be zero")


@dataclass(frozen=True, slots=True)
class Datapoint:
    id: int
    name: str
    data_type: DataType
    kind: DatapointKind
    position: Position
    enum_values: tuple[str, ...] = ()
    numeric: NumericSpec | None = None

    def __post_init__(self) -> None:
        if self.id < 0:
            raise ValueError("datapoint id must be non-negative")
        if not self.name:
            raise ValueError("datapoint name must not be empty")
        if self.data_type is DataType.ENUM and not self.enum_values:
            raise ValueError(f"enum datapoint {self.name!r} requires values")
        if self.data_type in {DataType.UINT8, DataType.UINT16} and self.numeric is None:
            raise ValueError(f"numeric datapoint {self.name!r} requires a numeric spec")

    @property
    def writable(self) -> bool:
        return self.kind is DatapointKind.WRITABLE

    @property
    def is_problem(self) -> bool:
        return self.kind in {DatapointKind.FAULT, DatapointKind.ALERT}


@dataclass(frozen=True, slots=True)
class ProductSchema:
    """The selected operational datapoints for one known product family.

    ``attribute_flags_size`` and ``attribute_values_size`` describe the complete vendor schema,
    including schedule fields that the engine deliberately does not expose. Keeping those wire
    sizes explicit lets a small audited set of operational attributes produce correctly sized
    frames without copying hundreds of schedule definitions into this project.
    """

    name: str
    product_key: str
    raw_status_size: int
    attribute_flags_size: int
    attribute_values_size: int
    bit_group_width: int
    attributes: tuple[Datapoint, ...]
    enabled_attribute: str | None = None
    power_attribute: str | None = None
    mode_attribute: str | None = None
    frequency_attribute: str | None = None
    control_supported: bool = True

    def __post_init__(self) -> None:
        if not self.name or not self.product_key:
            raise ValueError("schema name and product key are required")
        if self.raw_status_size <= 0:
            raise ValueError("raw status size must be positive")
        if self.attribute_flags_size <= 0 or self.attribute_values_size <= 0:
            raise ValueError("control buffer sizes must be positive")
        if self.bit_group_width < 0:
            raise ValueError("bit group width must be non-negative")

        ids = [attribute.id for attribute in self.attributes]
        names = [attribute.name for attribute in self.attributes]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("schema datapoint ids and names must be unique")

        known = set(names)
        for logical_name in (
            self.enabled_attribute,
            self.power_attribute,
            self.mode_attribute,
            self.frequency_attribute,
        ):
            if logical_name is not None and logical_name not in known:
                raise ValueError(f"logical datapoint {logical_name!r} is not defined")

        for attribute in self.attributes:
            position = attribute.position
            end = position.byte_offset + (
                position.length
                if position.unit is PositionUnit.BYTE
                else (position.bit_offset + position.length + 7) // 8
            )
            if end > self.raw_status_size:
                raise ValueError(f"datapoint {attribute.name!r} exceeds the status buffer")
            if attribute.writable:
                if attribute.id >= self.attribute_flags_size * 8:
                    raise ValueError(f"datapoint {attribute.name!r} exceeds the flags buffer")
                if end > self.attribute_values_size:
                    raise ValueError(f"datapoint {attribute.name!r} exceeds the values buffer")

    @property
    def attributes_by_name(self) -> MappingProxyType[str, Datapoint]:
        return MappingProxyType({attribute.name: attribute for attribute in self.attributes})

    def by_name(self, name: str) -> Datapoint:
        try:
            return self.attributes_by_name[name]
        except KeyError as error:
            raise KeyError(f"product {self.product_key} has no datapoint {name!r}") from error

    def decode_status(self, raw: bytes) -> dict[str, Any]:
        if len(raw) != self.raw_status_size:
            raise ValueError(
                f"{self.name} status must be {self.raw_status_size} bytes, got {len(raw)}"
            )
        return {
            attribute.name: self._decode_attribute(raw, attribute)
            for attribute in self.attributes
        }

    def active_problems(self, values: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            attribute.name
            for attribute in self.attributes
            if attribute.is_problem and bool(values.get(attribute.name))
        )

    def _decode_attribute(self, raw: bytes, attribute: Datapoint) -> Any:
        position = attribute.position
        if position.unit is PositionUnit.BIT:
            value = self._read_bits(raw, position)
            if attribute.data_type is DataType.BOOL:
                return bool(value)
            if attribute.data_type is DataType.ENUM:
                if value < len(attribute.enum_values):
                    return attribute.enum_values[value]
                return value
            return value

        chunk = raw[position.byte_offset : position.byte_offset + position.length]
        if attribute.data_type is DataType.BINARY:
            return bytes(chunk)
        if attribute.data_type not in {DataType.UINT8, DataType.UINT16}:
            raise ValueError(f"unsupported byte datapoint type {attribute.data_type!r}")
        numeric = attribute.numeric
        if numeric is None:  # guarded by Datapoint validation
            raise AssertionError("numeric datapoint is missing its specification")
        raw_value = int.from_bytes(chunk, "big")
        if attribute.enum_values and raw_value < len(attribute.enum_values):
            return attribute.enum_values[raw_value]
        return raw_value * numeric.ratio + numeric.addition

    def _read_bits(self, raw: bytes, position: Position) -> int:
        if position.byte_offset == 0 and self.bit_group_width > 1:
            group = int.from_bytes(raw[: self.bit_group_width], "big")
            return (group >> position.bit_offset) & ((1 << position.length) - 1)

        value = 0
        start = position.byte_offset * 8 + position.bit_offset
        for index in range(position.length):
            absolute_bit = start + index
            bit = (raw[absolute_bit // 8] >> (absolute_bit % 8)) & 1
            value |= bit << index
        return value
