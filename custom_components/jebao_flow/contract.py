"""Compatibility helpers for the daemon's additive schema-v1 contract."""

from __future__ import annotations

from collections.abc import Mapping

# Schema v1 originally advertised one global control surface and omitted a per-group
# ``controls`` field. Keep that exact behavior only when the field is absent; an
# explicitly empty list from a newer daemon is a deliberate fail-closed lock.
LEGACY_V1_GROUP_CONTROLS = (
    "enabled",
    "pattern",
    "power",
    "min_power",
    "max_power",
    "period",
    "transition",
    "start_feed",
    "stop_feed",
    "emergency_stop",
    "clear_emergency",
    "resume_all_members",
)

POWER_SEMANTICS_OUTPUT = "output"
POWER_SEMANTICS_REPORTED_FLOW = "reported_flow"


def resolve_group_controls(
    group: Mapping[str, object],
    *,
    observer_mode: bool,
) -> tuple[str, ...]:
    """Return controls advertised for one group, preserving old schema-v1 behavior."""

    if observer_mode:
        return ()
    if "controls" not in group:
        return LEGACY_V1_GROUP_CONTROLS

    controls = group.get("controls")
    if not isinstance(controls, list) or not all(
        isinstance(control, str) and control for control in controls
    ):
        return ()
    return tuple(dict.fromkeys(controls))


def resolve_device_power_semantics(device: Mapping[str, object]) -> str:
    """Resolve additive power semantics with the legacy actual-output default."""

    value = device.get("power_semantics", POWER_SEMANTICS_OUTPUT)
    if value == POWER_SEMANTICS_REPORTED_FLOW:
        return POWER_SEMANTICS_REPORTED_FLOW
    return POWER_SEMANTICS_OUTPUT
