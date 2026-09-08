"""Public, privacy-bounded models for operator packet observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class CaptureDirection(StrEnum):
    CONTROLLER_TO_DEVICE = "controller_to_device"
    DEVICE_TO_CONTROLLER = "device_to_controller"


class CaptureOrigin(StrEnum):
    PASSIVE_MIRROR = "passive_mirror"
    IMPORTED_PCAP = "imported_pcap"
    SIMULATOR = "simulator"


@dataclass(frozen=True, slots=True)
class UnknownRegion:
    """One region that the positive catalog could not attribute."""

    offset: int
    length: int
    reason: str

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("unknown-region offset must be non-negative")
        if self.length <= 0:
            raise ValueError("unknown-region length must be positive")
        if not self.reason:
            raise ValueError("unknown-region reason is required")

    def public_dict(self) -> dict[str, object]:
        return {"offset": self.offset, "length": self.length, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class ProtocolSummary:
    """A bounded semantic description that never contains raw frame bytes."""

    disposition: str
    command: int | None
    command_name: str | None
    action: int | None
    action_name: str | None
    payload_length: int
    semantic: MappingProxyType[str, object] = field(default_factory=lambda: MappingProxyType({}))
    unknown_regions: tuple[UnknownRegion, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition not in {"known", "unknown", "malformed"}:
            raise ValueError("protocol disposition is invalid")
        if self.payload_length < 0:
            raise ValueError("payload length must be non-negative")

    @classmethod
    def create(
        cls,
        *,
        disposition: str,
        command: int | None,
        command_name: str | None,
        action: int | None,
        action_name: str | None,
        payload_length: int,
        semantic: dict[str, Any] | None = None,
        unknown_regions: tuple[UnknownRegion, ...] = (),
    ) -> ProtocolSummary:
        return cls(
            disposition=disposition,
            command=command,
            command_name=command_name,
            action=action,
            action_name=action_name,
            payload_length=payload_length,
            semantic=MappingProxyType(dict(semantic or {})),
            unknown_regions=unknown_regions,
        )

    def public_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "disposition": self.disposition,
            "command": self.command,
            "command_name": self.command_name,
            "action": self.action,
            "action_name": self.action_name,
            "payload_length": self.payload_length,
            "semantic": dict(self.semantic),
            "unknown_regions": [item.public_dict() for item in self.unknown_regions],
        }
        return result
