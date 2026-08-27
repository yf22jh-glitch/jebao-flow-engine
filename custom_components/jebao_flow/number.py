"""Numeric controls for logical flow groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    JebaoFlowDeviceEntity,
    JebaoFlowGroupEntity,
    async_setup_device_entities,
    async_setup_group_entities,
)


@dataclass(frozen=True, kw_only=True)
class FlowNumberDescription(NumberEntityDescription):
    field: str


DESCRIPTIONS = (
    FlowNumberDescription(
        key="power",
        field="power",
        name="기준 출력",
        icon="mdi:gauge",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    FlowNumberDescription(
        key="min_power",
        field="min_power",
        name="최소 출력",
        icon="mdi:arrow-collapse-down",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    FlowNumberDescription(
        key="max_power",
        field="max_power",
        name="최대 출력",
        icon="mdi:arrow-collapse-up",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    FlowNumberDescription(
        key="period",
        field="period_seconds",
        name="패턴 주기",
        icon="mdi:timer-outline",
        native_min_value=1,
        native_max_value=3600,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    FlowNumberDescription(
        key="transition",
        field="transition_seconds",
        name="전환 시간",
        icon="mdi:transition",
        native_min_value=0,
        native_max_value=600,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_setup_group_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, group: (
            []
            if runtime.observer_mode
            else [
                JebaoFlowNumber(runtime, group, description)
                for description in DESCRIPTIONS
                if description.key
                in runtime.group_controls(str(group.get("id", "")))
            ]
        ),
    )
    async_setup_device_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, device: (
            [JebaoFlowDevicePowerNumber(runtime, device)]
            if not runtime.observer_mode and "power" in device.get("controls", ())
            else []
        ),
    )


class JebaoFlowNumber(JebaoFlowGroupEntity, NumberEntity):
    entity_description: FlowNumberDescription

    def __init__(
        self,
        runtime,
        group: dict[str, Any],
        description: FlowNumberDescription,
    ) -> None:
        super().__init__(runtime, group, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.group_control_available(self.entity_description.key)

    @property
    def native_value(self) -> float | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get(self.entity_description.field)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        field = self.entity_description.field
        command_value: int | float = value
        if field in {"power", "min_power", "max_power"}:
            command_value = round(value)
        await self.runtime.async_group_command(
            self.group_id,
            **{field: command_value},
        )


class JebaoFlowDevicePowerNumber(JebaoFlowDeviceEntity, NumberEntity):
    _attr_name = "개별 출력"
    _attr_icon = "mdi:gauge"
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "device_power")
        self._attr_native_min_value = float(device.get("min_power", 0))
        self._attr_native_max_value = float(device.get("max_power", 100))

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.runtime.observer_mode
            and self.advertises_control("power")
        )

    @property
    def native_value(self) -> float | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get("power")
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.async_device_command(self.device_id, power=round(value))
