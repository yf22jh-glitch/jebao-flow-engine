"""Momentary group actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    JebaoFlowDeviceEntity,
    JebaoFlowGroupEntity,
    async_setup_device_entities,
    async_setup_group_entities,
)


@dataclass(frozen=True, kw_only=True)
class FlowButtonDescription(ButtonEntityDescription):
    action: str


DESCRIPTIONS = (
    FlowButtonDescription(
        key="start_feed",
        action="start_feed",
        name="급여 시작",
        icon="mdi:fishbowl-outline",
    ),
    FlowButtonDescription(
        key="stop_feed",
        action="stop_feed",
        name="급여 종료",
        icon="mdi:play-circle-outline",
    ),
    FlowButtonDescription(
        key="emergency_stop",
        action="emergency_stop",
        name="비상 정지",
        icon="mdi:alert-octagon",
    ),
    FlowButtonDescription(
        key="clear_emergency",
        action="clear_emergency",
        name="비상 정지 해제",
        icon="mdi:lock-open-check-outline",
    ),
    FlowButtonDescription(
        key="resume_all_members",
        action="resume_all_members",
        name="모든 펌프 그룹 복귀",
        icon="mdi:account-multiple-check-outline",
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
                JebaoFlowActionButton(runtime, group, description)
                for description in DESCRIPTIONS
            ]
        ),
    )
    async_setup_device_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, device: (
            [JebaoFlowResumeGroupButton(runtime, device)]
            if not runtime.observer_mode and "resume_group" in device.get("controls", ())
            else []
        ),
    )


class JebaoFlowActionButton(JebaoFlowGroupEntity, ButtonEntity):
    entity_description: FlowButtonDescription

    def __init__(
        self,
        runtime,
        group: dict[str, Any],
        description: FlowButtonDescription,
    ) -> None:
        super().__init__(runtime, group, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and not self.runtime.observer_mode

    async def async_press(self) -> None:
        await self.runtime.async_group_command(
            self.group_id,
            action=self.entity_description.action,
        )


class JebaoFlowResumeGroupButton(JebaoFlowDeviceEntity, ButtonEntity):
    _attr_name = "그룹 제어로 복귀"
    _attr_icon = "mdi:source-merge"

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "resume_group")

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.runtime.observer_mode
            and self.state_payload is not None
            and self.state_payload.get("control_mode") == "manual_override"
        )

    async def async_press(self) -> None:
        await self.runtime.async_device_command(self.device_id, action="resume_group")
