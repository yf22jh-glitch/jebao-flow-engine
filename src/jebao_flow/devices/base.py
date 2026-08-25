"""Physical-device contract exposed to upper layers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from jebao_flow.protocol.models import DeviceCapabilities, DeviceState


class DeviceError(RuntimeError):
    pass


class DeviceConnectionError(DeviceError):
    pass


class UnsupportedCapabilityError(DeviceError):
    pass


class HardwareWritesDisabledError(DeviceError):
    pass


class StateVerificationError(DeviceError):
    pass


class JebaoDevice(ABC):
    @property
    @abstractmethod
    def device_id(self) -> str: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def capabilities(self) -> DeviceCapabilities: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def get_state(self) -> DeviceState: ...

    @abstractmethod
    async def set_enabled(self, enabled: bool) -> None: ...

    @abstractmethod
    async def set_power(self, power: int) -> None: ...

    @abstractmethod
    async def set_mode(self, mode: str) -> None: ...

    @abstractmethod
    async def set_frequency(self, value: int) -> None: ...
