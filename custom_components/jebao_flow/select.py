"""Pattern selector for logical flow groups."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import JebaoFlowGroupEntity, async_setup_group_entities


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
            [JebaoFlowPatternSelect(runtime, group)]
            if not runtime.observer_mode
            and "pattern" in runtime.group_controls(str(group.get("id", "")))
            else []
        ),
    )


class JebaoFlowPatternSelect(JebaoFlowGroupEntity, SelectEntity):
    _attr_name = "패턴"
    _attr_icon = "mdi:sine-wave"

    def __init__(self, runtime, group: dict[str, Any]) -> None:
        super().__init__(runtime, group, "pattern")

    @property
    def available(self) -> bool:
        return (
            self.group_control_available("pattern")
            and bool(self.options)
        )

    @property
    def options(self) -> list[str]:
        return list(self.runtime.group_patterns(self.group_id))

    @property
    def current_option(self) -> str | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get("pattern")
        return str(value) if value is not None else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"unsupported flow pattern {option!r}")
        await self.runtime.async_group_command(self.group_id, pattern=option)
