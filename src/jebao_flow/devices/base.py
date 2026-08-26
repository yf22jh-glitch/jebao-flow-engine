"""Physical-device contract exposed to upper layers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.protocol.models import DeviceCapabilities, DeviceState, DeviceTarget, LinkageRole


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


class SafetyInterlockError(DeviceError):
    pass


WriteGuard = Callable[[], bool]


class JebaoDevice(ABC):
    @property
    @abstractmethod
    def device_id(self) -> str: ...

    @property
    @abstractmethod
    def physical_binding(self) -> PhysicalDeviceBinding | None: ...

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

    @abstractmethod
    async def set_linkage(self, role: LinkageRole) -> None: ...

    @abstractmethod
    async def set_timer_enabled(self, enabled: bool) -> None: ...

    @abstractmethod
    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: WriteGuard | None = None,
    ) -> None: ...
