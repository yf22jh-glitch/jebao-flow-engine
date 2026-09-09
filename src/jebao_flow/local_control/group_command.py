"""Pure qualification fold for the local-control group command surface.

This module deliberately knows nothing about coordinators, dispatch runtimes, MQTT, or Home
Assistant.  It only folds the existing per-device qualification authority into one bounded group
result shared by projection and lifecycle code.
"""

from __future__ import annotations

from dataclasses import dataclass

from jebao_flow.groups.models import GroupConfig
from jebao_flow.local_control.catalog import LocalControlCatalog
from jebao_flow.local_control.models import FreshInstallManifest

GROUP_CONTROL_NOT_QUALIFIED = "physical_device_not_qualified"
REQUIRED_GROUP_CONTROL_FUNCTIONS = ("manual_flow", "power")


@dataclass(frozen=True, slots=True)
class GroupControlQualification:
    """Current per-member authority required by one group ON/OFF switch."""

    group_id: str
    missing: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.group_id or not self.group_id.isascii():
            raise ValueError("group qualification id must be non-empty ASCII")
        if self.missing != tuple(sorted(self.missing)):
            raise ValueError("missing group qualifications must use stable sorted order")
        if len(self.missing) != len(set(self.missing)):
            raise ValueError("missing group qualifications must be unique")
        for logical_member, function in self.missing:
            if not logical_member or not logical_member.isascii():
                raise ValueError("missing qualification member must be non-empty ASCII")
            if function not in REQUIRED_GROUP_CONTROL_FUNCTIONS:
                raise ValueError("missing qualification function is not required by group control")

    @property
    def qualified(self) -> bool:
        return not self.missing

    @property
    def diagnostic_reason(self) -> str | None:
        return None if self.qualified else GROUP_CONTROL_NOT_QUALIFIED

    def public_dict(self) -> dict[str, object]:
        return {
            "qualified": self.qualified,
            "diagnostic_reason": self.diagnostic_reason,
            "missing": [
                {
                    "logical_member": logical_member,
                    "function": function,
                }
                for logical_member, function in self.missing
            ],
        }


def group_control_qualification(
    group: GroupConfig,
    manifest: FreshInstallManifest,
    catalog: LocalControlCatalog,
) -> GroupControlQualification:
    """Fold the existing exact-device qualification predicate for all group members."""

    if not isinstance(group, GroupConfig):
        raise TypeError("group command qualification requires GroupConfig")
    if not isinstance(manifest, FreshInstallManifest):
        raise TypeError("group command qualification requires a current install manifest")
    if not isinstance(catalog, LocalControlCatalog):
        raise TypeError("group command qualification requires the current catalog")

    binding_by_member = {device.logical_member: device for device in manifest.devices}
    missing: list[tuple[str, str]] = []
    for member in group.members:
        binding = binding_by_member.get(member.device)
        for function in REQUIRED_GROUP_CONTROL_FUNCTIONS:
            if binding is None:
                missing.append((member.device, function))
                continue
            try:
                descriptor = catalog.find(binding.product_key, function)
            except KeyError:
                missing.append((member.device, function))
                continue
            if not descriptor.control_exposed_for(binding.physical_identity_key):
                missing.append((member.device, function))
    return GroupControlQualification(
        group_id=group.id,
        missing=tuple(sorted(missing)),
    )


__all__ = [
    "GROUP_CONTROL_NOT_QUALIFIED",
    "GroupControlQualification",
    "REQUIRED_GROUP_CONTROL_FUNCTIONS",
    "group_control_qualification",
]
