"""Evidence-gated capability claims for the local-first product track.

This module can describe observations and offline deductions.  It deliberately stops before the
live-test and write-promotion states: those belong to the separately frozen write track.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

_PRODUCT_KEY_RE = re.compile(r"[0-9a-f]{32}\Z")
_SESSION_RE = re.compile(r"capture-[0-9]{20}-[0-9a-f]{16}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CAPABILITY_RE = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")


class CapabilityEvidenceSource(StrEnum):
    APP_STATIC = "app_static"
    VENDOR_DECLARED = "vendor_declared"
    WIRE_OBSERVED = "wire_observed"
    DEVICE_CONFIRMED = "device_confirmed"


class CapabilitySupport(StrEnum):
    UNKNOWN = "unknown"
    OBSERVED = "observed"
    REPRODUCED = "reproduced"
    DIFFERENTIAL = "differential"


class AppExpressibility(StrEnum):
    UNKNOWN = "unknown_in_app"
    EXPRESSIBLE = "expressible_in_app"
    INFEASIBLE = "infeasible_by_app"


class DeviceSupport(StrEnum):
    UNKNOWN = "unknown_on_device"


class HomeAssistantExposure(StrEnum):
    HIDDEN = "hidden"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, slots=True)
class CaptureEvidenceRef:
    """Privacy-bounded pointer to one retained frame and its response context."""

    session_id: str
    event_seq: int
    frame_sha256: str
    response_frame_sha256: str | None = None
    app_version: str | None = None
    template_version: str | None = None
    template_sha256: str | None = None
    firmware_version: str | None = None

    def __post_init__(self) -> None:
        if not _SESSION_RE.fullmatch(self.session_id):
            raise ValueError("capture evidence session id is invalid")
        if self.event_seq < 1:
            raise ValueError("capture evidence sequence must be positive")
        for description, digest in (
            ("frame", self.frame_sha256),
            ("response frame", self.response_frame_sha256),
            ("template", self.template_sha256),
        ):
            if digest is not None and not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"capture evidence {description} digest is invalid")
        version_fields = (
            self.app_version,
            self.template_version,
            self.template_sha256,
            self.firmware_version,
        )
        if any(value is not None for value in version_fields) and not all(
            value is not None for value in version_fields
        ):
            raise ValueError(
                "app evidence must bind app, active template, template digest, and firmware"
            )
        for description, value in (
            ("app version", self.app_version),
            ("template version", self.template_version),
            ("firmware version", self.firmware_version),
        ):
            if value is not None:
                _validate_ascii_text(value, description)

    def public_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "event_seq": self.event_seq,
            "frame_sha256": self.frame_sha256,
            "response_frame_sha256": self.response_frame_sha256,
            "app_version": self.app_version,
            "template_version": self.template_version,
            "template_sha256": self.template_sha256,
            "firmware_version": self.firmware_version,
        }


@dataclass(frozen=True, slots=True)
class CapabilityClaim:
    """One product-scoped claim whose semantics are never inferred from a UI name alone."""

    product_key: str
    capability: str
    source_label: str
    exposed_name: str
    confirmed_semantic: str | None = None
    support: CapabilitySupport = CapabilitySupport.UNKNOWN
    app_expressibility: AppExpressibility = AppExpressibility.UNKNOWN
    device_support: DeviceSupport = DeviceSupport.UNKNOWN
    sources: tuple[CapabilityEvidenceSource, ...] = ()
    evidence: tuple[CaptureEvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not _PRODUCT_KEY_RE.fullmatch(self.product_key):
            raise ValueError("capability product key must be lowercase 32-hex")
        if not _CAPABILITY_RE.fullmatch(self.capability):
            raise ValueError("capability key must be a bounded public token")
        _validate_ascii_text(self.source_label, "capability source label")
        _validate_ascii_text(self.exposed_name, "capability exposed name")
        if self.confirmed_semantic is not None:
            _validate_ascii_text(self.confirmed_semantic, "confirmed capability semantic")
        if not isinstance(self.support, CapabilitySupport):
            raise TypeError("capability support must be a CapabilitySupport")
        if not isinstance(self.app_expressibility, AppExpressibility):
            raise TypeError("app expressibility must be an AppExpressibility")
        if not isinstance(self.device_support, DeviceSupport):
            raise TypeError("device support must be a DeviceSupport")
        if (
            self.support
            in {
                CapabilitySupport.OBSERVED,
                CapabilitySupport.REPRODUCED,
                CapabilitySupport.DIFFERENTIAL,
            }
            and not self.evidence
        ):
            raise ValueError("wire-supported capability states require retained capture evidence")
        if self.support is CapabilitySupport.REPRODUCED and len(self.evidence) < 2:
            raise ValueError("reproduced capability requires at least two retained observations")
        if self.support is CapabilitySupport.DIFFERENTIAL:
            if len(self.evidence) < 2 or self.confirmed_semantic is None:
                raise ValueError(
                    "differential capability requires evidence and a confirmed semantic"
                )
        if self.device_support is not DeviceSupport.UNKNOWN:
            raise ValueError("device support cannot be promoted by the offline capability model")

    @property
    def home_assistant_exposure(self) -> HomeAssistantExposure:
        if (
            self.support
            in {
                CapabilitySupport.OBSERVED,
                CapabilitySupport.REPRODUCED,
                CapabilitySupport.DIFFERENTIAL,
            }
            or self.app_expressibility is not AppExpressibility.UNKNOWN
        ):
            return HomeAssistantExposure.DIAGNOSTIC
        return HomeAssistantExposure.HIDDEN

    def observe(
        self,
        evidence: CaptureEvidenceRef,
        *,
        source: CapabilityEvidenceSource = CapabilityEvidenceSource.WIRE_OBSERVED,
    ) -> CapabilityClaim:
        if self.support not in {CapabilitySupport.UNKNOWN, CapabilitySupport.OBSERVED}:
            raise ValueError("observation cannot move a settled capability backwards")
        combined = _append_unique_evidence(self.evidence, evidence)
        support = CapabilitySupport.REPRODUCED if len(combined) >= 2 else CapabilitySupport.OBSERVED
        return replace(
            self,
            support=support,
            sources=_append_unique(self.sources, source),
            evidence=combined,
        )

    def confirm_differential(
        self,
        *,
        confirmed_semantic: str,
        comparison: CaptureEvidenceRef,
    ) -> CapabilityClaim:
        if self.support is not CapabilitySupport.REPRODUCED:
            raise ValueError("differential confirmation requires reproduced evidence first")
        if not confirmed_semantic:
            raise ValueError("confirmed semantic is required")
        return replace(
            self,
            support=CapabilitySupport.DIFFERENTIAL,
            confirmed_semantic=confirmed_semantic,
            evidence=_append_unique_evidence(self.evidence, comparison),
        )

    def mark_infeasible_by_app(self) -> CapabilityClaim:
        if self.app_expressibility is AppExpressibility.EXPRESSIBLE:
            raise ValueError("app infeasibility cannot overwrite app expressibility evidence")
        return replace(
            self,
            app_expressibility=AppExpressibility.INFEASIBLE,
            sources=_append_unique(self.sources, CapabilityEvidenceSource.APP_STATIC),
        )

    def mark_app_expressible(self) -> CapabilityClaim:
        if self.app_expressibility is AppExpressibility.INFEASIBLE:
            raise ValueError("app expressibility cannot overwrite an app-infeasible conclusion")
        return replace(
            self,
            app_expressibility=AppExpressibility.EXPRESSIBLE,
            sources=_append_unique(self.sources, CapabilityEvidenceSource.APP_STATIC),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "product_key": self.product_key,
            "capability": self.capability,
            "source_label": self.source_label,
            "exposed_name": self.exposed_name,
            "confirmed_semantic": self.confirmed_semantic,
            "support": self.support.value,
            "app_expressibility": self.app_expressibility.value,
            "device_support": self.device_support.value,
            "sources": [source.value for source in self.sources],
            "evidence": [item.public_dict() for item in self.evidence],
            "home_assistant_exposure": self.home_assistant_exposure.value,
        }


def _append_unique[T](items: tuple[T, ...], item: T) -> tuple[T, ...]:
    return items if item in items else (*items, item)


def _append_unique_evidence(
    items: tuple[CaptureEvidenceRef, ...],
    item: CaptureEvidenceRef,
) -> tuple[CaptureEvidenceRef, ...]:
    key = (item.session_id, item.event_seq, item.frame_sha256)
    if any((entry.session_id, entry.event_seq, entry.frame_sha256) == key for entry in items):
        return items
    return (*items, item)


def _validate_ascii_text(value: str, description: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise ValueError(f"{description} must contain 1..160 characters")
    if not value.isascii() or any(ord(character) < 0x20 for character in value):
        raise ValueError(f"{description} must use printable ASCII only")


__all__ = [
    "AppExpressibility",
    "CapabilityClaim",
    "CapabilityEvidenceSource",
    "CapabilitySupport",
    "CaptureEvidenceRef",
    "DeviceSupport",
    "HomeAssistantExposure",
]
