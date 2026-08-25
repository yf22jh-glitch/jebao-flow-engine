"""Server-owned desired state and command validation for MQTT clients."""

from __future__ import annotations

import time
from collections import OrderedDict

from jebao_flow.config import AppConfig
from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.models import GroupRuntime, GroupState
from jebao_flow.mqtt.models import (
    DeviceAction,
    DeviceCommand,
    DeviceCommandResult,
    DeviceControlMode,
    DeviceDescriptor,
    DeviceStatePayload,
    GroupAction,
    GroupCommand,
    GroupCommandResult,
    GroupDescriptor,
    GroupMemberState,
    GroupStatePayload,
    SystemConfigPayload,
)

_MAX_DEDUPLICATION_ENTRIES = 512


class GroupControlService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._groups = {group.id: group for group in config.groups}
        self._calculator = PatternCalculator()
        self._states = {
            group.id: self._initial_state(group.id)
            for group in config.groups
        }
        self._results: OrderedDict[str, GroupCommandResult] = OrderedDict()
        self._device_states = {
            device.id: self._initial_device_state(device.id)
            for device in config.devices
        }
        self._device_results: OrderedDict[str, DeviceCommandResult] = OrderedDict()
        for group_id in self._groups:
            self._refresh_group_members(group_id, increment_revision=False)

    @property
    def system_config(self) -> SystemConfigPayload:
        return SystemConfigPayload(
            instance_id=self._config.instance.id,
            name=self._config.instance.name,
            groups=tuple(
                GroupDescriptor(id=group.id, name=group.name)
                for group in self._config.groups
            ),
            devices=tuple(
                DeviceDescriptor(
                    id=device.id,
                    name=device.name,
                    type=device.type,
                    grouped=any(
                        device.id == member.device
                        for group in self._config.groups
                        for member in group.members
                    ),
                    ui=(
                        "flow_member"
                        if device.type.value == "wavemaker"
                        else "simple_equipment"
                    ),
                    controls=self._device_controls(device.id),
                    min_power=device.limits.min_power,
                    max_power=device.limits.max_power,
                )
                for device in self._config.devices
            ),
            patterns=tuple(sorted(self._calculator.supported_patterns(), key=str)),
        )

    def snapshots(self) -> tuple[GroupStatePayload, ...]:
        return tuple(self._states.values())

    def device_snapshots(self) -> tuple[DeviceStatePayload, ...]:
        return tuple(self._device_states.values())

    def snapshot(self, group_id: str) -> GroupStatePayload:
        try:
            return self._states[group_id]
        except KeyError as error:
            raise KeyError(f"unknown group {group_id!r}") from error

    def device_snapshot(self, device_id: str) -> DeviceStatePayload:
        try:
            return self._device_states[device_id]
        except KeyError as error:
            raise KeyError(f"unknown device {device_id!r}") from error

    def apply(self, group_id: str, command: GroupCommand) -> GroupCommandResult:
        previous_result = self._results.get(command.request_id)
        if previous_result is not None:
            return previous_result

        if group_id not in self._groups:
            return self._remember(
                GroupCommandResult(
                    request_id=command.request_id,
                    group_id=group_id,
                    accepted=False,
                    revision=0,
                    reason="unknown_group",
                )
            )

        current = self._states[group_id]
        if (
            current.status is GroupState.EMERGENCY_STOP
            and command.action is not GroupAction.CLEAR_EMERGENCY
        ):
            return self._remember(
                GroupCommandResult(
                    request_id=command.request_id,
                    group_id=group_id,
                    accepted=False,
                    revision=current.revision,
                    reason="emergency_stop_locked",
                )
            )

        updated_values = current.model_dump()
        for field in (
            "enabled",
            "pattern",
            "power",
            "min_power",
            "max_power",
            "period_seconds",
            "transition_seconds",
        ):
            value = getattr(command, field)
            if value is not None:
                updated_values[field] = value

        minimum = updated_values["min_power"]
        maximum = updated_values["max_power"]
        power = updated_values["power"]
        if minimum > maximum:
            return self._reject(current, command, "min_power_exceeds_max_power")
        if updated_values["enabled"] and not minimum <= power <= maximum:
            return self._reject(current, command, "power_outside_active_limits")

        status = GroupState.RUNNING if updated_values["enabled"] else GroupState.STOPPED
        if command.action is GroupAction.START_FEED:
            status = GroupState.FEEDING
        elif command.action is GroupAction.STOP_FEED:
            status = GroupState.RUNNING if updated_values["enabled"] else GroupState.STOPPED
        elif command.action is GroupAction.EMERGENCY_STOP:
            updated_values["enabled"] = False
            status = GroupState.EMERGENCY_STOP
        elif command.action is GroupAction.CLEAR_EMERGENCY:
            status = GroupState.STOPPED
        elif command.action is GroupAction.RESUME_ALL_MEMBERS:
            status = current.status

        updated_values.update(
            revision=current.revision + 1,
            status=status,
            last_request_id=command.request_id,
        )
        updated = GroupStatePayload.model_validate(updated_values)
        updated = updated.model_copy(update={"members": self._calculate_members(updated)})
        self._states[group_id] = updated
        force_group_control = (
            command.enabled is not None
            or command.action
            in {
                GroupAction.START_FEED,
                GroupAction.EMERGENCY_STOP,
                GroupAction.RESUME_ALL_MEMBERS,
            }
        )
        self._sync_group_devices(group_id, force_group_control=force_group_control)
        self._refresh_group_members(group_id, increment_revision=False)
        return self._remember(
            GroupCommandResult(
                request_id=command.request_id,
                group_id=group_id,
                accepted=True,
                revision=updated.revision,
            )
        )

    def apply_device(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> DeviceCommandResult:
        previous_result = self._device_results.get(command.request_id)
        if previous_result is not None:
            return previous_result
        current = self._device_states.get(device_id)
        if current is None:
            return self._remember_device(
                DeviceCommandResult(
                    request_id=command.request_id,
                    device_id=device_id,
                    accepted=False,
                    revision=0,
                    reason="unknown_device",
                )
            )

        if any(
            self._states[group_id].status is GroupState.EMERGENCY_STOP
            for group_id in current.group_ids
        ):
            return self._reject_device(
                current,
                command,
                "group_emergency_stop_locked",
            )

        controls = self._device_controls(device_id)
        if command.action is DeviceAction.RESUME_GROUP:
            if "resume_group" not in controls:
                return self._reject_device(current, command, "device_is_not_grouped")
            group_id = current.group_ids[0]
            group_state = self._states[group_id]
            group_target = self._calculate_targets(group_state)[device_id]
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "enabled": group_target.enabled,
                    "power": group_target.power,
                    "status": "group_control",
                    "control_mode": DeviceControlMode.GROUP,
                    "last_request_id": command.request_id,
                }
            )
        else:
            if command.enabled is not None and "enabled" not in controls:
                return self._reject_device(current, command, "enabled_control_unsupported")
            if command.power is not None and "power" not in controls:
                return self._reject_device(current, command, "power_control_unsupported")
            device = self._device_config(device_id)
            if command.power is not None and not (
                device.limits.min_power <= command.power <= device.limits.max_power
            ):
                return self._reject_device(current, command, "power_outside_device_limits")
            updates = {
                "revision": current.revision + 1,
                "last_request_id": command.request_id,
            }
            if command.enabled is not None:
                updates["enabled"] = command.enabled
            if command.power is not None:
                updates["power"] = command.power
            if current.group_ids:
                updates["control_mode"] = DeviceControlMode.MANUAL_OVERRIDE
                updates["status"] = "manual_override"
            else:
                updates["status"] = (
                    "running" if updates.get("enabled", current.enabled) else "stopped"
                )
            updated = current.model_copy(update=updates)

        self._device_states[device_id] = updated
        for group_id in updated.group_ids:
            self._refresh_group_members(group_id, increment_revision=True)
        return self._remember_device(
            DeviceCommandResult(
                request_id=command.request_id,
                device_id=device_id,
                accepted=True,
                revision=updated.revision,
            )
        )

    def _initial_state(self, group_id: str) -> GroupStatePayload:
        group = self._groups[group_id]
        hardware_writes_locked = self._config.runtime.dry_run or any(
            not self._device_config(member.device).control.allow_hardware_writes
            for member in group.members
        )
        state = GroupStatePayload(
            revision=0,
            group_id=group.id,
            name=group.name,
            status=GroupState.RUNNING if group.enabled else GroupState.STOPPED,
            enabled=group.enabled,
            pattern=group.default.pattern,
            power=group.default.power,
            min_power=group.default.min_power,
            max_power=group.default.max_power,
            period_seconds=group.default.period_seconds,
            transition_seconds=group.default.transition_seconds,
            hardware_writes_locked=hardware_writes_locked,
            members={},
        )
        return state.model_copy(update={"members": self._calculate_members(state)})

    def _calculate_members(self, state: GroupStatePayload) -> dict[str, GroupMemberState]:
        group = self._groups[state.group_id]
        targets = self._calculate_targets(state)
        calculated: dict[str, GroupMemberState] = {}
        device_states = getattr(self, "_device_states", {})
        for member in group.members:
            if member.device not in targets:
                continue
            device_state = device_states.get(member.device)
            manual = (
                device_state is not None
                and device_state.control_mode is DeviceControlMode.MANUAL_OVERRIDE
            )
            calculated[member.device] = GroupMemberState(
                name=self._device_config(member.device).name,
                role=member.role,
                gain=member.gain,
                phase=member.phase,
                control_mode=(
                    DeviceControlMode.MANUAL_OVERRIDE if manual else DeviceControlMode.GROUP
                ),
                enabled=(device_state.enabled if manual else targets[member.device].enabled),
                target_power=(device_state.power if manual else targets[member.device].power),
                actual_enabled=(
                    device_state.actual_enabled if device_state is not None else None
                ),
                actual_power=(device_state.actual_power if device_state is not None else None),
                online=(device_state.online if device_state is not None else None),
            )
        return calculated

    def _calculate_targets(self, state: GroupStatePayload):
        group = self._groups[state.group_id]
        runtime = GroupRuntime(
            state=state.status,
            enabled=state.enabled,
            pattern=state.pattern,
            power=state.power,
            min_power=state.min_power,
            max_power=state.max_power,
            period_seconds=state.period_seconds,
            started_at=0,
        )
        return self._calculator.calculate(time.monotonic(), group, runtime)

    def _initial_device_state(self, device_id: str) -> DeviceStatePayload:
        device = self._device_config(device_id)
        group_ids = tuple(
            group.id
            for group in self._config.groups
            if any(member.device == device_id for member in group.members)
        )
        target_power = device.limits.min_power
        enabled = False
        if group_ids:
            group_state = self._states[group_ids[0]]
            enabled = group_state.enabled
            target_power = group_state.members[device_id].target_power
        return DeviceStatePayload(
            revision=0,
            device_id=device.id,
            name=device.name,
            type=device.type,
            enabled=enabled,
            power=target_power,
            status="group_control" if group_ids else "stopped",
            control_mode=(
                DeviceControlMode.GROUP if group_ids else DeviceControlMode.STANDALONE
            ),
            group_ids=group_ids,
            hardware_writes_locked=(
                self._config.runtime.dry_run or not device.control.allow_hardware_writes
            ),
        )

    def _device_controls(self, device_id: str) -> tuple[str, ...]:
        device = self._device_config(device_id)
        if device.type.value == "dosing_pump":
            return ("status",)
        grouped = any(
            member.device == device_id
            for group in self._config.groups
            for member in group.members
        )
        controls = ["enabled", "power"]
        if grouped:
            controls.append("resume_group")
        return tuple(controls)

    def _sync_group_devices(self, group_id: str, *, force_group_control: bool) -> None:
        group_state = self._states[group_id]
        raw_targets = self._calculate_targets(group_state)
        for device_id, member_state in group_state.members.items():
            current = self._device_states[device_id]
            if (
                current.control_mode is DeviceControlMode.MANUAL_OVERRIDE
                and not force_group_control
            ):
                continue
            self._device_states[device_id] = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "enabled": group_state.enabled,
                    "power": (
                        raw_targets[device_id].power
                        if force_group_control
                        else member_state.target_power
                    ),
                    "status": "group_control",
                    "control_mode": DeviceControlMode.GROUP,
                }
            )

    def _refresh_group_members(self, group_id: str, *, increment_revision: bool) -> None:
        state = self._states[group_id]
        revision = state.revision + 1 if increment_revision else state.revision
        self._states[group_id] = state.model_copy(
            update={"revision": revision, "members": self._calculate_members(state)}
        )

    def _device_config(self, device_id: str):
        return next(device for device in self._config.devices if device.id == device_id)

    def _reject(
        self,
        current: GroupStatePayload,
        command: GroupCommand,
        reason: str,
    ) -> GroupCommandResult:
        return self._remember(
            GroupCommandResult(
                request_id=command.request_id,
                group_id=current.group_id,
                accepted=False,
                revision=current.revision,
                reason=reason,
            )
        )

    def _remember(self, result: GroupCommandResult) -> GroupCommandResult:
        self._results[result.request_id] = result
        self._results.move_to_end(result.request_id)
        while len(self._results) > _MAX_DEDUPLICATION_ENTRIES:
            self._results.popitem(last=False)
        return result

    def _reject_device(
        self,
        current: DeviceStatePayload,
        command: DeviceCommand,
        reason: str,
    ) -> DeviceCommandResult:
        return self._remember_device(
            DeviceCommandResult(
                request_id=command.request_id,
                device_id=current.device_id,
                accepted=False,
                revision=current.revision,
                reason=reason,
            )
        )

    def _remember_device(self, result: DeviceCommandResult) -> DeviceCommandResult:
        self._device_results[result.request_id] = result
        self._device_results.move_to_end(result.request_id)
        while len(self._device_results) > _MAX_DEDUPLICATION_ENTRIES:
            self._device_results.popitem(last=False)
        return result
