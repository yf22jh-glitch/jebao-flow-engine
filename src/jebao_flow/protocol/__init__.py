"""Lazy public protocol exports that keep write APIs out of diagnostic import graphs."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "Capability": ("jebao_flow.protocol.models", "Capability"),
    "DeviceCapabilities": ("jebao_flow.protocol.models", "DeviceCapabilities"),
    "DeviceSchedule": ("jebao_flow.protocol.models", "DeviceSchedule"),
    "DeviceState": ("jebao_flow.protocol.models", "DeviceState"),
    "DeviceTarget": ("jebao_flow.protocol.models", "DeviceTarget"),
    "DiscoveredDevice": ("jebao_flow.protocol.models", "DiscoveredDevice"),
    "DiscoveryProvider": ("jebao_flow.protocol.discovery", "DiscoveryProvider"),
    "GizwitsCommand": ("jebao_flow.protocol.codec", "GizwitsCommand"),
    "GizwitsDiscovery": ("jebao_flow.protocol.discovery", "GizwitsDiscovery"),
    "GizwitsFrame": ("jebao_flow.protocol.codec", "GizwitsFrame"),
    "GizwitsSession": ("jebao_flow.protocol.control_session", "GizwitsSession"),
    "LinkageRole": ("jebao_flow.protocol.models", "LinkageRole"),
    "ProtocolConnection": ("jebao_flow.protocol.connection", "ProtocolConnection"),
    "ScheduleEntry": ("jebao_flow.protocol.models", "ScheduleEntry"),
    "decode_device_schedule": (
        "jebao_flow.protocol.schedule",
        "decode_device_schedule",
    ),
    "decode_schedule": ("jebao_flow.protocol.schedule", "decode_schedule"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
