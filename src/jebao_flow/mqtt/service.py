"""Server-owned desired state and command validation for MQTT clients."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from jebao_flow.config import AppConfig, RuntimeMode
from jebao_flow.devices.observer import ObserverEvent, ObserverStatus
from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.models import GroupRuntime, GroupState, PatternKind
from jebao_flow.mqtt.models import (
    ChangeSource,
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
    ObservationSource,
    SystemConfigPayload,
)
from jebao_flow.protocol.models import DeviceSchedule

_MAX_DEDUPLICATION_ENTRIES = 512
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StateUpdate:
    device_ids: tuple[str, ...]
    group_ids: tuple[str, ...]


class GroupControlService:
    def __init__(
        self,
        config: AppConfig,
        *,
        command_executor_ready: bool = False,
    ) -> None:
        """Build the MQTT state service.

        The daemon has no command executor yet, so the service deliberately fails closed.
        Application wiring must set ``command_executor_ready`` only after a real actuator and
        reconciliation worker have started successfully.
        """

        self._config = config
        self._command_executor_ready = command_executor_ready
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
        self._dirty_devices: set[str] = set()
        self._dirty_groups: set[str] = set()
        self._last_published_seen_at = {device.id: None for device in config.devices}
        self._update_event = asyncio.Event()
        for group_id in self._groups:
            self._refresh_group_members(group_id, increment_revision=False)

    @property
    def system_config(self) -> SystemConfigPayload:
        runtime_control_ready = self._runtime_control_rejection_reason() is None
        group_control_ready = any(
            self._group_control_rejection_reason(group_id) is None
            for group_id in self._groups
        )
        device_control_ready = any(
            self._device_control_rejection_reason(device.id) is None
            and device.type.value != "dosing_pump"
            for device in self._config.devices
        )
        features = ["hardware_write_lock"]
        if not runtime_control_ready:
            features.insert(0, "observer")
        else:
            if group_control_ready:
                features[:0] = ["feed", "emergency_stop"]
            if device_control_ready:
                features.append("individual_override")
        return SystemConfigPayload(
            instance_id=self._config.instance.id,
            name=self._config.instance.name,
            # This is the effective externally available mode. Advertising configured
            # control mode without an executor makes Home Assistant expose unsafe controls.
            runtime_mode=(
                self._config.runtime.mode
                if runtime_control_ready
                else RuntimeMode.OBSERVER
            ),
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
                    observables=self._device_observables(device.id),
                    min_power=device.limits.min_power,
                    max_power=device.limits.max_power,
                )
                for device in self._config.devices
            ),
            patterns=tuple(sorted(self._calculator.supported_patterns(), key=str)),
            features=tuple(features),
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

    async def wait_for_updates(self) -> StateUpdate:
        """Wait for observer-produced state and coalesce it to the latest snapshots."""

        await self._update_event.wait()
        self._update_event.clear()
        update = StateUpdate(
            device_ids=tuple(sorted(self._dirty_devices)),
            group_ids=tuple(sorted(self._dirty_groups)),
        )
        self._dirty_devices.clear()
        self._dirty_groups.clear()
        return update

    def record_observer_event(self, event: ObserverEvent) -> None:
        """Merge LAN actual state without changing any desired target."""

        current = self._device_states.get(event.device_id)
        if current is None:
            _LOGGER.warning(
                "ignored_observation_for_unknown_device",
                extra={"device_id": event.device_id},
            )
            return

        operational_changed = False
        configuration_changed = False
        semantic_changed = False
        successful_observation = event.state is not None
        updates: dict[str, object] = {}
        if event.state is not None:
            state = event.state
            if current.last_seen_at is not None and state.observed_at <= current.last_seen_at:
                return
            has_baseline = current.last_seen_at is not None
            operational_changed = has_baseline and (
                current.actual_enabled,
                current.actual_power,
                current.actual_mode,
                current.actual_frequency,
            ) != (state.enabled, state.power, state.mode, state.frequency)
            configuration_changed = has_baseline and (
                current.observed_attributes != state.observed_attributes
                or self._schedule_configuration(current.schedule)
                != self._schedule_configuration(state.schedule)
            )
            status = "error" if state.error else ("running" if state.enabled else "stopped")
            semantic_changed = (
                not has_baseline
                or operational_changed
                or configuration_changed
                or (
                    current.online,
                    current.error,
                    current.status,
                )
                != (True, state.error, status)
            )
            updates.update(
                actual_enabled=state.enabled,
                actual_power=state.power,
                actual_mode=state.mode,
                actual_frequency=state.frequency,
                online=True,
                error=state.error,
                last_seen_at=state.observed_at,
                observed_attributes=state.observed_attributes,
                schedule=state.schedule,
                observation_source=ObservationSource.LAN_POLL,
                status=status,
            )
            if operational_changed:
                updates.update(
                    last_changed_at=state.observed_at,
                    change_source=ChangeSource.EXTERNAL_OR_NATIVE,
                )
                _LOGGER.info(
                    "observed_device_state_changed",
                    extra={
                        "device_id": event.device_id,
                        "previous_enabled": current.actual_enabled,
                        "previous_power": current.actual_power,
                        "previous_mode": current.actual_mode,
                        "enabled": state.enabled,
                        "power": state.power,
                        "mode": state.mode,
                        "frequency": state.frequency,
                        "observed_at": state.observed_at.isoformat(),
                        "change_source": ChangeSource.EXTERNAL_OR_NATIVE,
                    },
                )
            if configuration_changed:
                updates.update(
                    last_configuration_changed_at=state.observed_at,
                    change_source=ChangeSource.EXTERNAL_OR_NATIVE,
                )
                _LOGGER.info(
                    "observed_device_configuration_changed",
                    extra={
                        "device_id": event.device_id,
                        "previous_attributes": current.observed_attributes,
                        "attributes": state.observed_attributes,
                        "previous_schedule_entries": (
                            len(current.schedule.entries) if current.schedule else 0
                        ),
                        "schedule_entries": (
                            len(state.schedule.entries) if state.schedule else 0
                        ),
                        "observed_at": state.observed_at.isoformat(),
                        "change_source": ChangeSource.EXTERNAL_OR_NATIVE,
                    },
                )
        else:
            if event.status is ObserverStatus.UNMAPPED:
                online: bool | None = None
                status = ObserverStatus.UNMAPPED
            elif event.status is ObserverStatus.CONNECTING:
                online = False if current.online is False else current.online
                status = ObserverStatus.CONNECTING
            else:
                online = False
                status = ObserverStatus.OFFLINE
            semantic_changed = (
                current.online,
                current.error,
                current.status,
            ) != (online, event.error, status)
            updates.update(online=online, error=event.error, status=status)

        if semantic_changed:
            updates["revision"] = current.revision + 1
        updated = current.model_copy(update=updates)
        self._device_states[event.device_id] = updated
        group_ids = tuple(updated.group_ids)
        publish_heartbeat = False
        if successful_observation and event.state is not None:
            last_published = self._last_published_seen_at[event.device_id]
            publish_heartbeat = (
                last_published is None
                or (event.state.observed_at - last_published).total_seconds()
                >= self._config.observer.publish_heartbeat_seconds
            )
            if semantic_changed or publish_heartbeat:
                self._last_published_seen_at[event.device_id] = event.state.observed_at

        if semantic_changed or publish_heartbeat:
            self._mark_dirty(device_ids=(event.device_id,))

        if successful_observation or semantic_changed:
            for group_id in group_ids:
                self._refresh_group_observation(
                    group_id,
                    increment_revision=semantic_changed,
                )
        if semantic_changed or publish_heartbeat:
            self._mark_dirty(group_ids=group_ids)

    @staticmethod
    def _schedule_configuration(
        schedule: DeviceSchedule | None,
    ) -> dict[str, object] | None:
        """Return schedule data that excludes the continuously advancing device clock."""

        if schedule is None:
            return None
        return schedule.model_dump(mode="json", exclude={"device_local_time"})

    def _mark_dirty(
        self,
        *,
        device_ids: tuple[str, ...] = (),
        group_ids: tuple[str, ...] = (),
    ) -> None:
        self._dirty_devices.update(device_ids)
        self._dirty_groups.update(group_ids)
        self._update_event.set()

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

        if self._config.runtime.mode is RuntimeMode.OBSERVER:
            return self._remember(
                GroupCommandResult(
                    request_id=command.request_id,
                    group_id=group_id,
                    accepted=False,
                    revision=self._states[group_id].revision,
                    reason="observer_mode_read_only",
                )
            )

        rejection_reason = self._group_control_rejection_reason(group_id)
        if rejection_reason is not None:
            return self._reject(self._states[group_id], command, rejection_reason)

        current = self._states[group_id]
        if (
            command.pattern is not None
            and command.pattern not in self._calculator.supported_patterns()
        ):
            return self._reject(current, command, "unsupported_pattern")
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

        if self._config.runtime.mode is RuntimeMode.OBSERVER:
            return self._reject_device(current, command, "observer_mode_read_only")

        rejection_reason = self._device_control_rejection_reason(device_id)
        if rejection_reason is not None:
            return self._reject_device(current, command, rejection_reason)

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
        hardware_writes_locked = (
            self._runtime_control_rejection_reason() is not None
            or any(
                not self._device_config(member.device).control.allow_hardware_writes
                for member in group.members
            )
        )
        state = GroupStatePayload(
            revision=0,
            group_id=group.id,
            name=group.name,
            status=(
                GroupState.STARTING
                if self._config.runtime.mode is RuntimeMode.OBSERVER
                else GroupState.RUNNING if group.enabled else GroupState.STOPPED
            ),
            enabled=group.enabled,
            pattern=group.default.pattern,
            power=group.default.power,
            min_power=group.default.min_power,
            max_power=group.default.max_power,
            period_seconds=group.default.period_seconds,
            transition_seconds=group.default.transition_seconds,
            hardware_writes_locked=hardware_writes_locked,
            members={},
            member_count=len(group.members),
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
                actual_mode=(device_state.actual_mode if device_state is not None else None),
                actual_frequency=(
                    device_state.actual_frequency if device_state is not None else None
                ),
                online=(device_state.online if device_state is not None else None),
                error=(device_state.error if device_state is not None else None),
                last_seen_at=(device_state.last_seen_at if device_state is not None else None),
                last_changed_at=(
                    device_state.last_changed_at if device_state is not None else None
                ),
                last_configuration_changed_at=(
                    device_state.last_configuration_changed_at
                    if device_state is not None
                    else None
                ),
                observed_attributes=(
                    device_state.observed_attributes if device_state is not None else {}
                ),
            )
        return calculated

    def _calculate_targets(self, state: GroupStatePayload):
        group = self._groups[state.group_id]
        pattern = state.pattern
        if (
            self._config.runtime.mode is RuntimeMode.OBSERVER
            and pattern not in self._calculator.supported_patterns()
        ):
            # Targets are informational in observer mode. This fallback keeps a future/native
            # desired value observable without trying to execute unimplemented control code.
            pattern = PatternKind.CONSTANT
        runtime = GroupRuntime(
            state=state.status,
            enabled=state.enabled,
            pattern=pattern,
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
            status="unobserved",
            control_mode=(
                DeviceControlMode.GROUP if group_ids else DeviceControlMode.STANDALONE
            ),
            group_ids=group_ids,
            hardware_writes_locked=(
                self._runtime_control_rejection_reason() is not None
                or not device.control.allow_hardware_writes
            ),
        )

    def _device_controls(self, device_id: str) -> tuple[str, ...]:
        device = self._device_config(device_id)
        if self._device_control_rejection_reason(device_id) is not None:
            return ()
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

    def _runtime_control_rejection_reason(self) -> str | None:
        if self._config.runtime.mode is RuntimeMode.OBSERVER:
            return "observer_mode_read_only"
        if not self._command_executor_ready:
            return "control_executor_unavailable"
        if self._config.runtime.dry_run:
            return "hardware_writes_locked"
        return None

    def _device_control_rejection_reason(self, device_id: str) -> str | None:
        runtime_reason = self._runtime_control_rejection_reason()
        if runtime_reason is not None:
            return runtime_reason
        if not self._device_config(device_id).control.allow_hardware_writes:
            return "hardware_writes_locked"
        return None

    def _group_control_rejection_reason(self, group_id: str) -> str | None:
        runtime_reason = self._runtime_control_rejection_reason()
        if runtime_reason is not None:
            return runtime_reason
        if any(
            not self._device_config(member.device).control.allow_hardware_writes
            for member in self._groups[group_id].members
        ):
            return "hardware_writes_locked"
        return None

    def _device_observables(self, device_id: str) -> tuple[str, ...]:
        device = self._device_config(device_id)
        if device.type.value == "dosing_pump":
            return ("enabled", "error", "schedule")
        return ("enabled", "power", "mode", "frequency", "error", "schedule")

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

    def _refresh_group_observation(self, group_id: str, *, increment_revision: bool) -> None:
        state = self._states[group_id]
        members = self._calculate_members(state)
        observed = [member for member in members.values() if member.online is not None]
        online = [member for member in members.values() if member.online is True]
        errors = [member for member in members.values() if member.error]
        if state.status is GroupState.EMERGENCY_STOP:
            status = GroupState.EMERGENCY_STOP
        elif errors and len(errors) == len(members):
            status = GroupState.ERROR
        elif errors:
            status = GroupState.DEGRADED
        elif len(online) == len(members) and members:
            status = (
                GroupState.RUNNING
                if any(member.actual_enabled is True for member in online)
                else GroupState.STOPPED
            )
        elif online:
            status = GroupState.DEGRADED
        elif observed:
            status = GroupState.ERROR
        else:
            status = GroupState.STARTING

        seen_values = [member.last_seen_at for member in members.values() if member.last_seen_at]
        changed_values = [
            member.last_changed_at for member in members.values() if member.last_changed_at
        ]
        configuration_changed_values = [
            member.last_configuration_changed_at
            for member in members.values()
            if member.last_configuration_changed_at
        ]
        self._states[group_id] = state.model_copy(
            update={
                "revision": state.revision + 1 if increment_revision else state.revision,
                "members": members,
                "status": status,
                "actual_enabled": (
                    any(member.actual_enabled is True for member in online)
                    if online
                    else None
                ),
                "online_member_count": len(online),
                "member_count": len(members),
                "last_seen_at": max(seen_values) if seen_values else None,
                "last_changed_at": max(changed_values) if changed_values else None,
                "last_configuration_changed_at": (
                    max(configuration_changed_values)
                    if configuration_changed_values
                    else None
                ),
            }
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
