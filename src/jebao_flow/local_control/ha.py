"""English Home Assistant/MQTT projection for the isolated local-control track.

The projection is deliberately declarative.  A control entity exists only when the matching
product/function has a version-bound qualification.  Everything else remains an explicit
diagnostic instead of becoming a button that merely looks supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256

from jebao_flow.groups.models import GroupConfig
from jebao_flow.local_control.catalog import LocalControlCatalog, LocalFunctionDescriptor
from jebao_flow.local_control.group_command import group_control_qualification
from jebao_flow.local_control.models import FreshInstallManifest, LocalDeviceBinding
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan
from jebao_flow.operator_capture.app_catalog import DOSING_PUMP_APP, AppFunctionKind
from jebao_flow.protocol.schedule import (
    AQUARIUM_PUMP_PRODUCT_KEY,
    DC_PUMP_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRODUCT_KEY,
)

_TOPIC_PREFIX_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
_INSTANCE_NAMESPACE_RE = re.compile(r"instance_[0-9a-f]{64}\Z")
_RUNTIME_MODE_OBSERVER_REASON = "runtime_mode_observer"
NATIVE_WAVEMAKER_FUNCTIONS = (
    "power",
    "schedule_enable",
    "linkage_role",
    "manual_mode",
    "manual_flow",
    "manual_frequency",
    "feeding_duration",
    "custom_wave_frequency",
    "schedule",
)
_PREVIEW_PRODUCTS = {
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRODUCT_KEY,
    DC_PUMP_PRO_PRODUCT_KEY,
    AQUARIUM_PUMP_PRODUCT_KEY,
}
_DOSING_CHANNEL_FUNCTIONS = frozenset(
    {"manual_channel", "channel_timer_enable", "interval_days", "channel_schedule"}
)


class HomeAssistantPlatform(StrEnum):
    """Entity shape emitted by the local-control MQTT adapter."""

    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    NUMBER = "number"
    SELECT = "select"
    SENSOR = "sensor"
    SWITCH = "switch"


_DiscoveryIdentity = tuple[HomeAssistantPlatform, str]


@dataclass(frozen=True, slots=True)
class HomeAssistantEntityDescriptor:
    """One stable, English-only Home Assistant entity contract."""

    platform: HomeAssistantPlatform
    unique_id: str
    name: str
    logical_member: str
    function: str
    state_topic: str
    command_topic: str | None
    stage: str
    control_available: bool
    diagnostic_reason: str | None = None
    entity_category: str | None = None
    native_minimum: int | None = None
    native_maximum: int | None = None
    native_step: int | None = None
    options: tuple[str, ...] = ()
    channel_index: int | None = None
    slot_index: int | None = None
    schedule_request_topic: str | None = None
    preview_request_topic: str | None = None
    instance_namespace: str = field(kw_only=True)

    def __post_init__(self) -> None:
        public = self.public_dict()
        _require_ascii(public)
        _require_discovery_identity(self.unique_id, self.instance_namespace)
        if self.control_available != (self.command_topic is not None):
            raise ValueError("Home Assistant command topic must match control availability")
        if self.schedule_request_topic is not None and (
            self.function not in {"schedule", "channel_schedule"}
            or self.platform is not HomeAssistantPlatform.SENSOR
        ):
            raise ValueError("schedule API belongs to the schedule diagnostic sensor")
        if self.channel_index is not None and not 1 <= self.channel_index <= 4:
            raise ValueError("Home Assistant channel index must be between 1 and 4")
        if self.platform is HomeAssistantPlatform.NUMBER:
            if self.native_minimum is None or self.native_maximum is None:
                raise ValueError("Home Assistant number requires an explicit native range")
            if self.native_step is not None and self.native_step < 1:
                raise ValueError("Home Assistant number step must be positive")
        if self.platform is HomeAssistantPlatform.SELECT and not self.options:
            raise ValueError("Home Assistant select requires explicit options")

    def public_dict(self) -> dict[str, object]:
        payload = {
            "platform": self.platform.value,
            "unique_id": self.unique_id,
            "name": self.name,
            "logical_member": self.logical_member,
            "function": self.function,
            "state_topic": self.state_topic,
            "command_topic": self.command_topic,
            "stage": self.stage,
            "control_available": self.control_available,
            "diagnostic_reason": self.diagnostic_reason,
            "entity_category": self.entity_category,
            "native_minimum": self.native_minimum,
            "native_maximum": self.native_maximum,
            "native_step": self.native_step,
            "options": list(self.options),
            "channel_index": self.channel_index,
            "slot_index": self.slot_index,
        }
        if self.schedule_request_topic is not None:
            payload["schedule_request_topic"] = self.schedule_request_topic
        if self.preview_request_topic is not None:
            payload["preview_request_topic"] = self.preview_request_topic
        return payload

    def mqtt_discovery_payload(self) -> dict[str, object]:
        """Return a native MQTT-discovery payload without private device identifiers."""

        global_availability = _global_availability_topic(self.state_topic)
        payload: dict[str, object] = {
            "name": self.name,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
            "device": {
                "identifiers": [_device_identifier(self.instance_namespace, self.logical_member)],
                "name": _english_member_name(self.logical_member),
                "manufacturer": "Jebao",
                "model": "Local Control",
            },
        }
        if self.logical_member == "local_system":
            payload.update(
                {
                    "availability_topic": global_availability,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                }
            )
        else:
            payload.update(
                {
                    "availability": [
                        {
                            "topic": global_availability,
                            "payload_available": "online",
                            "payload_not_available": "offline",
                        },
                        {
                            "topic": _device_availability_topic(
                                self.state_topic,
                                self.logical_member,
                            ),
                            "payload_available": "online",
                            "payload_not_available": "offline",
                        },
                    ],
                    "availability_mode": "all",
                }
            )
        if self.command_topic is not None:
            payload["command_topic"] = self.command_topic
            payload["qos"] = 0
            if self.platform is HomeAssistantPlatform.BUTTON:
                payload["payload_press"] = "PRESS"
            if self.platform in {
                HomeAssistantPlatform.NUMBER,
                HomeAssistantPlatform.SELECT,
                HomeAssistantPlatform.SWITCH,
            }:
                # A state is published only after exact device readback; never mirror the command
                # optimistically while that verification is pending.
                payload["optimistic"] = False
        if self.entity_category is not None:
            payload["entity_category"] = self.entity_category
        if self.platform is HomeAssistantPlatform.NUMBER:
            payload.update(
                {
                    "min": self.native_minimum,
                    "max": self.native_maximum,
                    "step": self.native_step or 1,
                    "mode": "slider",
                }
            )
        if self.platform is HomeAssistantPlatform.SELECT:
            payload["options"] = list(self.options)
        if not self.control_available:
            payload["json_attributes_topic"] = self.state_topic
            payload["value_template"] = "{{ value_json.stage }}"
        _require_ascii(payload)
        return payload


@dataclass(frozen=True, slots=True)
class GroupHomeAssistantEntityDescriptor:
    """Command-free HA sensor for fresh config-relative group power aggregation."""

    group_id: str
    unique_id: str
    name: str
    state_topic: str
    attributes_topic: str
    availability_topic: str
    instance_namespace: str = field(kw_only=True)

    @property
    def platform(self) -> HomeAssistantPlatform:
        return HomeAssistantPlatform.SENSOR

    @property
    def command_topic(self) -> None:
        return None

    def __post_init__(self) -> None:
        _require_ascii(self.public_dict())
        _require_discovery_identity(self.unique_id, self.instance_namespace)

    def public_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform.value,
            "group_id": self.group_id,
            "unique_id": self.unique_id,
            "name": self.name,
            "state_topic": self.state_topic,
            "attributes_topic": self.attributes_topic,
            "availability_topic": self.availability_topic,
            "command_topic": None,
            "entity_category": "diagnostic",
        }

    def mqtt_discovery_payload(self) -> dict[str, object]:
        global_availability = _global_availability_topic(self.state_topic)
        payload: dict[str, object] = {
            "name": self.name,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
            "json_attributes_topic": self.attributes_topic,
            "entity_category": "diagnostic",
            "availability": [
                {
                    "topic": global_availability,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
                {
                    "topic": self.availability_topic,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
            ],
            "availability_mode": "all",
            "device": {
                "identifiers": [_group_device_identifier(self.instance_namespace, self.group_id)],
                "name": f"{_english_member_name(self.group_id)} Group",
                "manufacturer": "Jebao",
                "model": "Local Control Group",
            },
        }
        _require_ascii(payload)
        return payload


@dataclass(frozen=True, slots=True)
class GroupControlHomeAssistantEntityDescriptor:
    """Non-optimistic ON/OFF switch for one fully qualified logical group."""

    group_id: str
    unique_id: str
    name: str
    state_topic: str
    command_topic: str
    availability_topic: str
    instance_namespace: str = field(kw_only=True)

    @property
    def platform(self) -> HomeAssistantPlatform:
        return HomeAssistantPlatform.SWITCH

    def __post_init__(self) -> None:
        _require_ascii(self.public_dict())
        _require_discovery_identity(self.unique_id, self.instance_namespace)

    def public_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform.value,
            "group_id": self.group_id,
            "unique_id": self.unique_id,
            "name": self.name,
            "state_topic": self.state_topic,
            "command_topic": self.command_topic,
            "availability_topic": self.availability_topic,
            "optimistic": False,
        }

    def mqtt_discovery_payload(self) -> dict[str, object]:
        global_availability = _global_availability_topic(self.state_topic)
        payload: dict[str, object] = {
            "name": self.name,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
            "command_topic": self.command_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "optimistic": False,
            "qos": 0,
            "availability": [
                {
                    "topic": global_availability,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
                {
                    "topic": self.availability_topic,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
            ],
            "availability_mode": "all",
            "device": {
                "identifiers": [_group_device_identifier(self.instance_namespace, self.group_id)],
                "name": f"{_english_member_name(self.group_id)} Group",
                "manufacturer": "Jebao",
                "model": "Local Control Group",
            },
        }
        _require_ascii(payload)
        return payload


@dataclass(frozen=True, slots=True)
class GroupMemberResumeHomeAssistantEntityDescriptor:
    """State-less ownership-return button for one qualified logical group member."""

    group_id: str
    logical_member: str
    unique_id: str
    name: str
    command_topic: str
    availability_topic: str
    instance_namespace: str = field(kw_only=True)

    @property
    def platform(self) -> HomeAssistantPlatform:
        return HomeAssistantPlatform.BUTTON

    def __post_init__(self) -> None:
        _require_ascii(self.public_dict())
        _require_discovery_identity(self.unique_id, self.instance_namespace)

    def public_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform.value,
            "group_id": self.group_id,
            "logical_member": self.logical_member,
            "unique_id": self.unique_id,
            "name": self.name,
            "command_topic": self.command_topic,
            "availability_topic": self.availability_topic,
        }

    def mqtt_discovery_payload(self) -> dict[str, object]:
        global_availability = _global_availability_topic(self.command_topic)
        payload: dict[str, object] = {
            "name": self.name,
            "unique_id": self.unique_id,
            "command_topic": self.command_topic,
            "payload_press": "PRESS",
            "qos": 0,
            "availability": [
                {
                    "topic": global_availability,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
                {
                    "topic": self.availability_topic,
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
            ],
            "availability_mode": "all",
            "device": {
                "identifiers": [_group_device_identifier(self.instance_namespace, self.group_id)],
                "name": f"{_english_member_name(self.group_id)} Group",
                "manufacturer": "Jebao",
                "model": "Local Control Group",
            },
        }
        _require_ascii(payload)
        return payload


@dataclass(frozen=True, slots=True)
class HomeAssistantProjection:
    discovery_prefix: str
    topic_prefix: str
    entities: tuple[HomeAssistantEntityDescriptor, ...]
    group_entities: tuple[GroupHomeAssistantEntityDescriptor, ...] = ()
    group_control_entities: tuple[GroupControlHomeAssistantEntityDescriptor, ...] = ()
    group_resume_entities: tuple[GroupMemberResumeHomeAssistantEntityDescriptor, ...] = ()
    control_surface_diagnostic_reason: str | None = None
    native_wavemaker_members: tuple[str, ...] = ()
    native_attach_members: tuple[str, ...] = ()
    _potential_discovery_identities: tuple[_DiscoveryIdentity, ...] = field(
        kw_only=True,
        repr=False,
    )

    def __post_init__(self) -> None:
        _require_topic_prefix(self.discovery_prefix)
        _require_topic_prefix(self.topic_prefix)
        expected_namespace = _discovery_instance_namespace(self.topic_prefix)
        if self.control_surface_diagnostic_reason not in {
            None,
            _RUNTIME_MODE_OBSERVER_REASON,
        }:
            raise ValueError("unknown Home Assistant control-surface diagnostic reason")
        entities = (
            *self.entities,
            *self.group_entities,
            *self.group_control_entities,
            *self.group_resume_entities,
        )
        if any(entity.instance_namespace != expected_namespace for entity in entities):
            raise ValueError("Home Assistant entity uses a different instance namespace")
        identities = [(entity.platform, entity.unique_id) for entity in entities]
        if len(identities) != len(set(identities)):
            raise ValueError("Home Assistant projection contains duplicate entities")
        potential_identities = set(self._potential_discovery_identities)
        if len(potential_identities) != len(self._potential_discovery_identities):
            raise ValueError("Home Assistant potential discovery identities must be unique")
        for _platform, unique_id in potential_identities:
            _require_discovery_identity(unique_id, expected_namespace)
        if not set(identities).issubset(potential_identities):
            raise ValueError("current Home Assistant discovery is outside its potential variants")

    def discovery_records(self) -> tuple[tuple[str, dict[str, object]], ...]:
        entities = (
            *self.entities,
            *self.group_entities,
            *self.group_control_entities,
            *self.group_resume_entities,
        )
        return tuple(
            (
                _discovery_topic(
                    self.discovery_prefix,
                    entity.platform,
                    entity.unique_id,
                ),
                entity.mqtt_discovery_payload(),
            )
            for entity in entities
        )

    def discovery_tombstone_topics(self) -> tuple[str, ...]:
        """Return owned current-topology variants not published by this projection."""

        current_identities = {
            (entity.platform, entity.unique_id)
            for entity in (
                *self.entities,
                *self.group_entities,
                *self.group_control_entities,
                *self.group_resume_entities,
            )
        }
        return tuple(
            sorted(
                _discovery_topic(self.discovery_prefix, platform, unique_id)
                for platform, unique_id in self._potential_discovery_identities
                if (platform, unique_id) not in current_identities
            )
        )

    def public_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": 1,
            "language": "en",
            "control_surface_diagnostic_reason": self.control_surface_diagnostic_reason,
            "native_wavemaker_api": self.native_wavemaker_api,
            "entity_count": len(self.entities),
            "control_count": sum(entity.control_available for entity in self.entities),
            "entities": [entity.public_dict() for entity in self.entities],
            "group_entity_count": len(self.group_entities),
            "group_entities": [entity.public_dict() for entity in self.group_entities],
            "group_control_entity_count": len(self.group_control_entities),
            "group_control_entities": [
                entity.public_dict() for entity in self.group_control_entities
            ],
            "group_resume_entity_count": len(self.group_resume_entities),
            "group_resume_entities": [
                entity.public_dict() for entity in self.group_resume_entities
            ],
        }
        _require_ascii(payload)
        return payload

    @property
    def native_request_topic(self) -> str | None:
        if len(self.native_wavemaker_members) < 2:
            return None
        return f"{self.topic_prefix}/local/wavemakers/request"

    @property
    def native_wavemaker_api(self) -> dict[str, object] | None:
        if self.native_request_topic is None:
            return None
        return {
            "request_topic": self.native_request_topic,
            "members": list(self.native_wavemaker_members),
            "attach_qualified_members": list(self.native_attach_members),
            "operations": [
                "preview",
                "attach",
                "detach",
                "recover",
                "status",
                "assist_detach",
                "cycle_master",
                "cycle_master_power",
            ],
        }

    @property
    def global_availability_topic(self) -> str:
        return f"{self.topic_prefix}/local/availability"

    def device_availability_topic(self, logical_member: str) -> str:
        if not any(
            entity.logical_member == logical_member and logical_member != "local_system"
            for entity in self.entities
        ):
            raise KeyError("logical member is not in the Home Assistant device projection")
        return f"{self.topic_prefix}/local/devices/{logical_member}/availability"

    def group_availability_topic(self, group_id: str) -> str:
        try:
            return next(
                entity.availability_topic
                for entity in self.group_entities
                if entity.group_id == group_id
            )
        except StopIteration as error:
            raise KeyError("logical group is not in the Home Assistant projection") from error

    def group_control_availability_topic(self, group_id: str) -> str:
        try:
            return next(
                entity.availability_topic
                for entity in self.group_control_entities
                if entity.group_id == group_id
            )
        except StopIteration as error:
            raise KeyError(
                "logical group control is not in the Home Assistant projection"
            ) from error


def project_home_assistant(
    manifest: FreshInstallManifest,
    catalog: LocalControlCatalog,
    *,
    discovery_prefix: str,
    topic_prefix: str,
    schedule_plans: tuple[FreshMasterSchedulePlan, ...] = (),
    groups: tuple[GroupConfig, ...] = (),
    control_surface_enabled: bool = True,
    native_lifecycle_enabled: bool = False,
    preview_requests_enabled: bool = False,
) -> HomeAssistantProjection:
    """Project every registered device capability and qualified per-slot Flow control."""

    _require_topic_prefix(discovery_prefix)
    _require_topic_prefix(topic_prefix)
    instance_namespace = _discovery_instance_namespace(topic_prefix)
    _validate_unique_schedule_topology(schedule_plans)
    potential_identities: set[_DiscoveryIdentity] = set()
    entities: list[HomeAssistantEntityDescriptor] = []
    for device in manifest.devices:
        descriptors = tuple(
            item for item in catalog.functions if item.product_key == device.product_key
        )
        for descriptor in descriptors:
            channel_indexes: tuple[int | None, ...] = (
                (1, 2, 3, 4)
                if device.product_key == DOSING_PUMP_APP.product_key
                and descriptor.function in _DOSING_CHANNEL_FUNCTIONS
                else (None,)
            )
            for channel_index in channel_indexes:
                unique_id = _device_function_unique_id(
                    instance_namespace,
                    device.logical_member,
                    descriptor.function,
                    channel_index=channel_index,
                )
                potential_identities.add((HomeAssistantPlatform.SENSOR, unique_id))
                control_platform = _platform_for(descriptor, control_available=True)
                if control_platform is not HomeAssistantPlatform.SENSOR:
                    potential_identities.add((control_platform, unique_id))
                entities.append(
                    _project_device_function(
                        device,
                        descriptor,
                        topic_prefix,
                        instance_namespace=instance_namespace,
                        control_surface_enabled=control_surface_enabled,
                        preview_requests_enabled=preview_requests_enabled,
                        channel_index=channel_index,
                    )
                )
    for descriptor in (item for item in catalog.functions if item.product_key is None):
        potential_identities.add(
            (
                HomeAssistantPlatform.SENSOR,
                _system_function_unique_id(instance_namespace, descriptor.function),
            )
        )
        entities.append(
            _project_system_function(
                descriptor,
                topic_prefix,
                instance_namespace=instance_namespace,
            )
        )

    known_members = {device.logical_member: device for device in manifest.devices}
    for plan in schedule_plans:
        _validate_plan_members(plan, known_members)
        for slave in plan.slaves:
            for slot_index in range(48):
                potential_identities.add(
                    (
                        HomeAssistantPlatform.NUMBER,
                        _slave_slot_unique_id(
                            instance_namespace,
                            plan,
                            slave.target.logical_member,
                            slot_index,
                        ),
                    )
                )
        if not control_surface_enabled:
            continue
        descriptor = catalog.find(plan.master.product_key, "slave_per_slot_flow_extension")
        if not descriptor.control_exposed:
            continue
        for slave in plan.slaves:
            if not descriptor.control_exposed_for(slave.target.physical_identity_key):
                continue
            for slot_index in range(48):
                entities.append(
                    _project_slave_slot(
                        plan,
                        slave.target,
                        slot_index,
                        topic_prefix,
                        descriptor,
                        instance_namespace=instance_namespace,
                    )
                )

    for group in groups:
        potential_identities.add(
            (
                HomeAssistantPlatform.SENSOR,
                _group_output_unique_id(instance_namespace, group.id),
            )
        )
        potential_identities.add(
            (
                HomeAssistantPlatform.SWITCH,
                _group_control_unique_id(instance_namespace, group.id),
            )
        )
        potential_identities.update(
            (
                HomeAssistantPlatform.BUTTON,
                _group_member_resume_unique_id(
                    instance_namespace,
                    group.id,
                    member.device,
                ),
            )
            for member in group.members
        )

    qualified_groups = (
        tuple(
            group
            for group in groups
            if group_control_qualification(group, manifest, catalog).qualified
        )
        if control_surface_enabled
        else ()
    )
    return HomeAssistantProjection(
        discovery_prefix=discovery_prefix,
        topic_prefix=topic_prefix,
        entities=tuple(entities),
        native_wavemaker_members=tuple(
            device.logical_member
            for device in manifest.devices
            if native_lifecycle_enabled
            and control_surface_enabled
            and device.product_key == LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
        ),
        native_attach_members=tuple(
            device.logical_member
            for device in manifest.devices
            if native_lifecycle_enabled
            and control_surface_enabled
            and device.product_key == LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            and all(
                catalog.find(device.product_key, function).control_exposed_for(
                    device.physical_identity_key
                )
                for function in NATIVE_WAVEMAKER_FUNCTIONS
            )
        ),
        group_entities=tuple(
            _project_group_output(
                group,
                topic_prefix,
                instance_namespace=instance_namespace,
            )
            for group in groups
        ),
        group_control_entities=tuple(
            _project_group_control(
                group,
                topic_prefix,
                instance_namespace=instance_namespace,
            )
            for group in qualified_groups
        ),
        group_resume_entities=tuple(
            _project_group_member_resume(
                group,
                member.device,
                topic_prefix,
                instance_namespace=instance_namespace,
            )
            for group in qualified_groups
            for member in group.members
        ),
        control_surface_diagnostic_reason=(
            None if control_surface_enabled else _RUNTIME_MODE_OBSERVER_REASON
        ),
        _potential_discovery_identities=tuple(
            sorted(
                potential_identities,
                key=lambda item: (item[0].value, item[1]),
            )
        ),
    )


def _project_group_output(
    group: GroupConfig,
    topic_prefix: str,
    *,
    instance_namespace: str,
) -> GroupHomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/groups/{group.id}/actual-output"
    return GroupHomeAssistantEntityDescriptor(
        group_id=group.id,
        unique_id=_group_output_unique_id(instance_namespace, group.id),
        name=f"{_english_member_name(group.id)} Actual Output",
        state_topic=f"{base}/state",
        attributes_topic=f"{base}/attributes",
        availability_topic=f"{base}/availability",
        instance_namespace=instance_namespace,
    )


def _project_group_control(
    group: GroupConfig,
    topic_prefix: str,
    *,
    instance_namespace: str,
) -> GroupControlHomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/groups/{group.id}/control"
    return GroupControlHomeAssistantEntityDescriptor(
        group_id=group.id,
        unique_id=_group_control_unique_id(instance_namespace, group.id),
        name=f"{_english_member_name(group.id)} Control",
        state_topic=f"{base}/state",
        command_topic=f"{base}/set",
        availability_topic=f"{base}/availability",
        instance_namespace=instance_namespace,
    )


def _project_group_member_resume(
    group: GroupConfig,
    logical_member: str,
    topic_prefix: str,
    *,
    instance_namespace: str,
) -> GroupMemberResumeHomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/groups/{group.id}/members/{logical_member}/resume"
    return GroupMemberResumeHomeAssistantEntityDescriptor(
        group_id=group.id,
        logical_member=logical_member,
        unique_id=_group_member_resume_unique_id(
            instance_namespace,
            group.id,
            logical_member,
        ),
        name=f"Resume {_english_member_name(logical_member)} To Group",
        command_topic=f"{base}/set",
        availability_topic=f"{base}/availability",
        instance_namespace=instance_namespace,
    )


def _project_device_function(
    device: LocalDeviceBinding,
    descriptor: LocalFunctionDescriptor,
    topic_prefix: str,
    *,
    instance_namespace: str,
    control_surface_enabled: bool,
    preview_requests_enabled: bool = False,
    channel_index: int | None = None,
) -> HomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/devices/{device.logical_member}/functions/{descriptor.function}"
    if channel_index is not None:
        base = f"{base}/channels/{channel_index}"
    control_available = control_surface_enabled and descriptor.control_exposed_for(
        device.physical_identity_key
    )
    platform = _platform_for(descriptor, control_available=control_available)
    schedule_request_topic = (
        f"{base}/request"
        if control_available and descriptor.function in {"schedule", "channel_schedule"}
        else None
    )
    control_available = control_available and platform is not HomeAssistantPlatform.SENSOR
    return HomeAssistantEntityDescriptor(
        platform=platform,
        unique_id=_device_function_unique_id(
            instance_namespace,
            device.logical_member,
            descriptor.function,
            channel_index=channel_index,
        ),
        name=(
            descriptor.exposed_name
            if channel_index is None
            else f"Channel {channel_index} {descriptor.exposed_name}"
        ),
        logical_member=device.logical_member,
        function=descriptor.function,
        state_topic=f"{base}/state",
        command_topic=f"{base}/set" if control_available else None,
        stage=descriptor.stage_for(device.physical_identity_key).value,
        control_available=control_available,
        diagnostic_reason=descriptor.diagnostic_reason_for(device.physical_identity_key),
        entity_category=None if control_available else "diagnostic",
        native_minimum=descriptor.native_minimum,
        native_maximum=descriptor.native_maximum,
        native_step=descriptor.native_step,
        options=descriptor.choices,
        channel_index=channel_index,
        schedule_request_topic=schedule_request_topic,
        preview_request_topic=(
            f"{base}/preview"
            if preview_requests_enabled
            and (
                device.product_key in _PREVIEW_PRODUCTS
                and descriptor.function in {"schedule", "device_clock_sync"}
                or device.product_key == DOSING_PUMP_APP.product_key
                and descriptor.function == "channel_schedule"
            )
            else None
        ),
        instance_namespace=instance_namespace,
    )


def _project_slave_slot(
    plan: FreshMasterSchedulePlan,
    slave: LocalDeviceBinding,
    slot_index: int,
    topic_prefix: str,
    descriptor: LocalFunctionDescriptor,
    *,
    instance_namespace: str,
) -> HomeAssistantEntityDescriptor:
    control_available = descriptor.control_exposed_for(slave.physical_identity_key)
    group_key = f"{plan.master.logical_member}_{plan.linkage_mode.value}"
    base = (
        f"{topic_prefix}/local/schedules/{group_key}/slaves/"
        f"{slave.logical_member}/slots/{slot_index}/flow"
    )
    return HomeAssistantEntityDescriptor(
        platform=HomeAssistantPlatform.NUMBER,
        unique_id=_slave_slot_unique_id(
            instance_namespace,
            plan,
            slave.logical_member,
            slot_index,
        ),
        name=f"Schedule Slot {slot_index + 1} Flow",
        logical_member=slave.logical_member,
        function="slave_per_slot_flow_extension",
        state_topic=f"{base}/state",
        command_topic=f"{base}/set" if control_available else None,
        stage=descriptor.stage_for(slave.physical_identity_key).value,
        control_available=control_available,
        diagnostic_reason=descriptor.diagnostic_reason_for(slave.physical_identity_key),
        native_minimum=descriptor.native_minimum,
        native_maximum=descriptor.native_maximum,
        slot_index=slot_index,
        instance_namespace=instance_namespace,
    )


def _project_system_function(
    descriptor: LocalFunctionDescriptor,
    topic_prefix: str,
    *,
    instance_namespace: str,
) -> HomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/system/functions/{descriptor.function}"
    return HomeAssistantEntityDescriptor(
        platform=HomeAssistantPlatform.SENSOR,
        unique_id=_system_function_unique_id(instance_namespace, descriptor.function),
        name=descriptor.exposed_name,
        logical_member="local_system",
        function=descriptor.function,
        state_topic=f"{base}/state",
        command_topic=None,
        stage=descriptor.stage.value,
        control_available=False,
        diagnostic_reason=descriptor.diagnostic_reason,
        entity_category="diagnostic",
        instance_namespace=instance_namespace,
    )


def _platform_for(
    descriptor: LocalFunctionDescriptor,
    *,
    control_available: bool,
) -> HomeAssistantPlatform:
    if not control_available:
        return HomeAssistantPlatform.SENSOR
    return {
        AppFunctionKind.BOOLEAN: HomeAssistantPlatform.SWITCH,
        AppFunctionKind.INTEGER: HomeAssistantPlatform.NUMBER,
        AppFunctionKind.ENUM: HomeAssistantPlatform.SELECT,
        AppFunctionKind.ACTION: HomeAssistantPlatform.BUTTON,
    }.get(descriptor.kind, HomeAssistantPlatform.SENSOR)


def _discovery_instance_namespace(topic_prefix: str) -> str:
    return f"instance_{sha256(topic_prefix.encode('ascii')).hexdigest()}"


def _unique_id(instance_namespace: str, suffix: str) -> str:
    _require_instance_namespace(instance_namespace)
    return f"jebao_flow_local_{instance_namespace}_{suffix}"


def _device_function_unique_id(
    instance_namespace: str,
    logical_member: str,
    function: str,
    *,
    channel_index: int | None = None,
) -> str:
    suffix = f"{logical_member}_{function.replace('.', '_')}"
    if channel_index is not None:
        suffix = f"{suffix}_channel_{channel_index}"
    return _unique_id(
        instance_namespace,
        suffix,
    )


def _system_function_unique_id(instance_namespace: str, function: str) -> str:
    return _unique_id(instance_namespace, f"system_{function}")


def _slave_slot_unique_id(
    instance_namespace: str,
    plan: FreshMasterSchedulePlan,
    logical_member: str,
    slot_index: int,
) -> str:
    group_key = f"{plan.master.logical_member}_{plan.linkage_mode.value}"
    return _unique_id(
        instance_namespace,
        f"{group_key}_{logical_member}_slot_{slot_index:02d}_flow",
    )


def _group_output_unique_id(instance_namespace: str, group_id: str) -> str:
    return _unique_id(instance_namespace, f"group_{group_id}_actual_output")


def _group_control_unique_id(instance_namespace: str, group_id: str) -> str:
    return _unique_id(instance_namespace, f"group_{group_id}_control")


def _group_member_resume_unique_id(
    instance_namespace: str,
    group_id: str,
    logical_member: str,
) -> str:
    return _unique_id(
        instance_namespace,
        f"group_{group_id}_{logical_member}_resume",
    )


def _discovery_topic(
    discovery_prefix: str,
    platform: HomeAssistantPlatform,
    unique_id: str,
) -> str:
    return f"{discovery_prefix}/{platform.value}/{unique_id}/config"


def _device_identifier(instance_namespace: str, logical_member: str) -> str:
    _require_instance_namespace(instance_namespace)
    return f"jebao_flow_local_{instance_namespace}_{logical_member}"


def _group_device_identifier(instance_namespace: str, group_id: str) -> str:
    _require_instance_namespace(instance_namespace)
    return f"jebao_flow_local_{instance_namespace}_group_{group_id}"


def _validate_plan_members(
    plan: FreshMasterSchedulePlan,
    known_members: dict[str, LocalDeviceBinding],
) -> None:
    for target in (plan.master, *(slave.target for slave in plan.slaves)):
        if known_members.get(target.logical_member) != target:
            raise ValueError("schedule plan is not bound to the current install manifest")


def _validate_unique_schedule_topology(
    schedule_plans: tuple[FreshMasterSchedulePlan, ...],
) -> None:
    slave_members = [
        slave.target.logical_member for plan in schedule_plans for slave in plan.slaves
    ]
    if len(slave_members) != len(set(slave_members)):
        raise ValueError("one slave cannot belong to multiple current schedule topologies")


def _global_availability_topic(state_topic: str) -> str:
    prefix, _separator, _tail = state_topic.partition("/local/")
    return f"{prefix}/local/availability"


def _device_availability_topic(state_topic: str, logical_member: str) -> str:
    prefix, _separator, _tail = state_topic.partition("/local/")
    return f"{prefix}/local/devices/{logical_member}/availability"


def _english_member_name(logical_member: str) -> str:
    return " ".join(part.capitalize() for part in logical_member.split("_"))


def _require_topic_prefix(value: str) -> None:
    if _TOPIC_PREFIX_RE.fullmatch(value.strip("/")) is None:
        raise ValueError("MQTT topic prefix must use bounded printable ASCII segments")


def _require_instance_namespace(value: str) -> None:
    if _INSTANCE_NAMESPACE_RE.fullmatch(value) is None:
        raise ValueError("Home Assistant instance namespace must be a full SHA-256 binding")


def _require_discovery_identity(unique_id: str, instance_namespace: str) -> None:
    _require_instance_namespace(instance_namespace)
    if not unique_id.startswith(f"jebao_flow_local_{instance_namespace}_"):
        raise ValueError("Home Assistant unique id is not bound to its instance namespace")


def _require_ascii(value: object) -> None:
    if isinstance(value, str):
        if not value.isascii() or any(ord(character) < 0x20 for character in value):
            raise ValueError("Home Assistant public output must use printable English ASCII")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require_ascii(key)
            _require_ascii(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _require_ascii(item)


__all__ = [
    "GroupControlHomeAssistantEntityDescriptor",
    "GroupHomeAssistantEntityDescriptor",
    "GroupMemberResumeHomeAssistantEntityDescriptor",
    "HomeAssistantEntityDescriptor",
    "HomeAssistantPlatform",
    "HomeAssistantProjection",
    "project_home_assistant",
]
