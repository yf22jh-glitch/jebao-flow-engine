"""In-memory ownership registry enforcing one device instance per id."""

from __future__ import annotations

from collections.abc import Iterator

from jebao_flow.devices.base import JebaoDevice


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, JebaoDevice] = {}

    def add(self, device: JebaoDevice) -> None:
        if device.device_id in self._devices:
            raise ValueError(f"device {device.device_id!r} is already registered")
        self._devices[device.device_id] = device

    def get(self, device_id: str) -> JebaoDevice:
        try:
            return self._devices[device_id]
        except KeyError as error:
            raise KeyError(f"unknown device {device_id!r}") from error

    def __iter__(self) -> Iterator[JebaoDevice]:
        return iter(self._devices.values())

    def __len__(self) -> int:
        return len(self._devices)

