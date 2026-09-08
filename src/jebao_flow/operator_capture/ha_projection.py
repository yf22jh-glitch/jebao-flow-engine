"""Read-only Home Assistant projection for unqualified capability observations.

No callable command, service, or write callback is represented here.  A later write-track module
may promote qualified capabilities into controls only after the repository freeze is lifted.
"""

from __future__ import annotations

from dataclasses import dataclass

from jebao_flow.operator_capture.capability import (
    CapabilityClaim,
    HomeAssistantExposure,
)


@dataclass(frozen=True, slots=True)
class DiagnosticEntityDescriptor:
    unique_key: str
    name: str
    native_value: str
    attributes: dict[str, object]
    entity_category: str = "diagnostic"
    read_only: bool = True

    def __post_init__(self) -> None:
        for description, value in (
            ("Home Assistant unique key", self.unique_key),
            ("Home Assistant name", self.name),
            ("Home Assistant native value", self.native_value),
            ("Home Assistant entity category", self.entity_category),
        ):
            if not value or not value.isascii():
                raise ValueError(f"{description} must use non-empty ASCII text")

    def public_dict(self) -> dict[str, object]:
        return {
            "unique_key": self.unique_key,
            "name": self.name,
            "native_value": self.native_value,
            "attributes": dict(self.attributes),
            "entity_category": self.entity_category,
            "read_only": self.read_only,
        }


def project_diagnostic_entities(
    claims: tuple[CapabilityClaim, ...],
) -> tuple[DiagnosticEntityDescriptor, ...]:
    """Project observable claims without turning them into controls."""

    descriptors: list[DiagnosticEntityDescriptor] = []
    seen: set[tuple[str, str]] = set()
    for claim in sorted(claims, key=lambda item: (item.product_key, item.capability)):
        identity = (claim.product_key, claim.capability)
        if identity in seen:
            raise ValueError("Home Assistant capability projection contains a duplicate key")
        seen.add(identity)
        if claim.home_assistant_exposure is HomeAssistantExposure.HIDDEN:
            continue
        descriptors.append(
            DiagnosticEntityDescriptor(
                unique_key=f"{claim.product_key}_{claim.capability.replace('.', '_')}",
                name=claim.exposed_name,
                native_value=claim.device_support.value,
                attributes={
                    "capability": claim.capability,
                    "confirmed_semantic": claim.confirmed_semantic,
                    "observation_maturity": claim.support.value,
                    "app_expressibility": claim.app_expressibility.value,
                    "device_support": claim.device_support.value,
                    "evidence_count": len(claim.evidence),
                    "sources": [source.value for source in claim.sources],
                    "control_available": False,
                },
            )
        )
    return tuple(descriptors)


__all__ = ["DiagnosticEntityDescriptor", "project_diagnostic_entities"]
