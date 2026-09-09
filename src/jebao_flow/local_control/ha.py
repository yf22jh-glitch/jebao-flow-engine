"""English Home Assistant/MQTT projection for the isolated local-control track.

The projection is deliberately declarative.  A control entity exists only when the matching
product/function has a version-bound qualification.  Everything else remains an explicit
diagnostic instead of becoming a button that merely looks supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.groups.models import GroupConfig
from jebao_flow.local_control.catalog import LocalControlCatalog, LocalFunctionDescriptor
from jebao_flow.local_control.group_command import group_control_qualification
from jebao_flow.local_control.models import FreshInstallManifest, LocalDeviceBinding
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan
from jebao_flow.operator_capture.app_catalog import AppFunctionKind

_TOPIC_PREFIX_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")


class HomeAssistantPlatform(StrEnum):
    """Entity shape emitted by the local-control MQTT adapter."""

    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    NUMBER = "number"
    SELECT = "select"
    SENSOR = "sensor"
    SWITCH = "switch"


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
    options: tuple[str, ...] = ()
    slot_index: int | None = None

    def __post_init__(self) -> None:
        public = self.public_dict()
        _require_ascii(public)
        if self.control_available != (self.command_topic is not None):
            raise ValueError("Home Assistant command topic must match control availability")
        if self.platform is HomeAssistantPlatform.NUMBER:
            if self.native_minimum is None or self.native_maximum is None:
                raise ValueError("Home Assistant number requires an explicit native range")
        if self.platform is HomeAssistantPlatform.SELECT and not self.options:
            raise ValueError("Home Assistant select requires explicit options")

    def public_dict(self) -> dict[str, object]:
        return {
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
            "options": list(self.options),
            "slot_index": self.slot_index,
        }

    def mqtt_discovery_payload(self) -> dict[str, object]:
        """Return a native MQTT-discovery payload without private device identifiers."""

        global_availability = _global_availability_topic(self.state_topic)
        payload: dict[str, object] = {
            "name": self.name,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
            "device": {
                "identifiers": [f"jebao_flow_local_{self.logical_member}"],
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
                    "step": 1,
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

    @property
    def platform(self) -> HomeAssistantPlatform:
        return HomeAssistantPlatform.SENSOR

    @property
    def command_topic(self) -> None:
        return None

    def __post_init__(self) -> None:
        _require_ascii(self.public_dict())

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
                "identifiers": [f"jebao_flow_local_group_{self.group_id}"],
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

    @property
    def platform(self) -> HomeAssistantPlatform:
        return HomeAssistantPlatform.SWITCH

    def __post_init__(self) -> None:
        _require_ascii(self.public_dict())

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
                "identifiers": [f"jebao_flow_local_group_{self.group_id}"],
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

    @property
    def platform(self) -> HomeAssistantPlatform:
        return HomeAssistantPlatform.BUTTON

    def __post_init__(self) -> None:
        _require_ascii(self.public_dict())

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
                "identifiers": [f"jebao_flow_local_group_{self.group_id}"],
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

    def __post_init__(self) -> None:
        _require_topic_prefix(self.discovery_prefix)
        _require_topic_prefix(self.topic_prefix)
        identities = [(entity.platform, entity.unique_id) for entity in self.entities]
        identities.extend(
            (entity.platform, entity.unique_id) for entity in self.group_entities
        )
        identities.extend(
            (entity.platform, entity.unique_id) for entity in self.group_control_entities
        )
        identities.extend(
            (entity.platform, entity.unique_id) for entity in self.group_resume_entities
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Home Assistant projection contains duplicate entities")

    def discovery_records(self) -> tuple[tuple[str, dict[str, object]], ...]:
        entities = (
            *self.entities,
            *self.group_entities,
            *self.group_control_entities,
            *self.group_resume_entities,
        )
        return tuple(
            (
                f"{self.discovery_prefix}/{entity.platform.value}/{entity.unique_id}/config",
                entity.mqtt_discovery_payload(),
            )
            for entity in entities
        )

    def public_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": 1,
            "language": "en",
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
) -> HomeAssistantProjection:
    """Project every registered device capability and qualified per-slot Flow control."""

    _require_topic_prefix(discovery_prefix)
    _require_topic_prefix(topic_prefix)
    _validate_unique_schedule_topology(schedule_plans)
    entities: list[HomeAssistantEntityDescriptor] = []
    for device in manifest.devices:
        descriptors = tuple(
            item for item in catalog.functions if item.product_key == device.product_key
        )
        for descriptor in descriptors:
            entities.append(_project_device_function(device, descriptor, topic_prefix))
    for descriptor in (item for item in catalog.functions if item.product_key is None):
        entities.append(_project_system_function(descriptor, topic_prefix))

    known_members = {device.logical_member: device for device in manifest.devices}
    for plan in schedule_plans:
        _validate_plan_members(plan, known_members)
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
                    )
                )

    qualified_groups = tuple(
        group
        for group in groups
        if group_control_qualification(group, manifest, catalog).qualified
    )
    return HomeAssistantProjection(
        discovery_prefix=discovery_prefix,
        topic_prefix=topic_prefix,
        entities=tuple(entities),
        group_entities=tuple(_project_group_output(group, topic_prefix) for group in groups),
        group_control_entities=tuple(
            _project_group_control(group, topic_prefix)
            for group in qualified_groups
        ),
        group_resume_entities=tuple(
            _project_group_member_resume(group, member.device, topic_prefix)
            for group in qualified_groups
            for member in group.members
        ),
    )


def _project_group_output(
    group: GroupConfig,
    topic_prefix: str,
) -> GroupHomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/groups/{group.id}/actual-output"
    return GroupHomeAssistantEntityDescriptor(
        group_id=group.id,
        unique_id=f"jebao_flow_local_group_{group.id}_actual_output",
        name=f"{_english_member_name(group.id)} Actual Output",
        state_topic=f"{base}/state",
        attributes_topic=f"{base}/attributes",
        availability_topic=f"{base}/availability",
    )


def _project_group_control(
    group: GroupConfig,
    topic_prefix: str,
) -> GroupControlHomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/groups/{group.id}/control"
    return GroupControlHomeAssistantEntityDescriptor(
        group_id=group.id,
        unique_id=f"jebao_flow_local_group_{group.id}_control",
        name=f"{_english_member_name(group.id)} Control",
        state_topic=f"{base}/state",
        command_topic=f"{base}/set",
        availability_topic=f"{base}/availability",
    )


def _project_group_member_resume(
    group: GroupConfig,
    logical_member: str,
    topic_prefix: str,
) -> GroupMemberResumeHomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/groups/{group.id}/members/{logical_member}/resume"
    return GroupMemberResumeHomeAssistantEntityDescriptor(
        group_id=group.id,
        logical_member=logical_member,
        unique_id=f"jebao_flow_local_group_{group.id}_{logical_member}_resume",
        name=f"Resume {_english_member_name(logical_member)} To Group",
        command_topic=f"{base}/set",
        availability_topic=f"{base}/availability",
    )


def _project_device_function(
    device: LocalDeviceBinding,
    descriptor: LocalFunctionDescriptor,
    topic_prefix: str,
) -> HomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/devices/{device.logical_member}/functions/{descriptor.function}"
    control_available = descriptor.control_exposed_for(device.physical_identity_key)
    platform = _platform_for(descriptor, control_available=control_available)
    control_available = control_available and platform is not HomeAssistantPlatform.SENSOR
    suffix = descriptor.function.replace(".", "_")
    return HomeAssistantEntityDescriptor(
        platform=platform,
        unique_id=f"jebao_flow_local_{device.logical_member}_{suffix}",
        name=descriptor.exposed_name,
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
        options=descriptor.choices,
    )


def _project_slave_slot(
    plan: FreshMasterSchedulePlan,
    slave: LocalDeviceBinding,
    slot_index: int,
    topic_prefix: str,
    descriptor: LocalFunctionDescriptor,
) -> HomeAssistantEntityDescriptor:
    control_available = descriptor.control_exposed_for(slave.physical_identity_key)
    group_key = f"{plan.master.logical_member}_{plan.linkage_mode.value}"
    base = (
        f"{topic_prefix}/local/schedules/{group_key}/slaves/"
        f"{slave.logical_member}/slots/{slot_index}/flow"
    )
    return HomeAssistantEntityDescriptor(
        platform=HomeAssistantPlatform.NUMBER,
        unique_id=(
            f"jebao_flow_local_{group_key}_{slave.logical_member}_slot_{slot_index:02d}_flow"
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
    )


def _project_system_function(
    descriptor: LocalFunctionDescriptor,
    topic_prefix: str,
) -> HomeAssistantEntityDescriptor:
    base = f"{topic_prefix}/local/system/functions/{descriptor.function}"
    return HomeAssistantEntityDescriptor(
        platform=HomeAssistantPlatform.SENSOR,
        unique_id=f"jebao_flow_local_system_{descriptor.function}",
        name=descriptor.exposed_name,
        logical_member="local_system",
        function=descriptor.function,
        state_topic=f"{base}/state",
        command_topic=None,
        stage=descriptor.stage.value,
        control_available=False,
        diagnostic_reason=descriptor.diagnostic_reason,
        entity_category="diagnostic",
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
        slave.target.logical_member
        for plan in schedule_plans
        for slave in plan.slaves
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
