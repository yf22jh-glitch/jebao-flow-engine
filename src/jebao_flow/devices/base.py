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
    async def disconnect(self) -> None:
        """Close the device session and propagate cancellation promptly.

        Implementations must release device and transport locks when cancelled. Safety rollback
        may interrupt a stuck close before persisting and enforcing an emergency OFF state.
        """

        ...

    @abstractmethod
    async def get_state(self) -> DeviceState:
        """Return one fresh state and propagate cancellation promptly.

        Implementations must not suppress ``CancelledError`` and must release any device or
        transport lock when cancelled. Safety rollback races this read against an interlock and
        waits for cancellation cleanup before issuing a compensating command.
        """

        ...

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

    async def write_linkage(
        self,
        role: LinkageRole,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        """Write only the linkage datapoint under a last-moment safety guard."""

        del role, guard
        raise UnsupportedCapabilityError("guarded linkage-only writes are unsupported")

    @abstractmethod
    async def set_timer_enabled(self, enabled: bool) -> None: ...

    @abstractmethod
    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        """Apply one target and propagate cancellation promptly.

        Implementations must not suppress ``CancelledError`` and must release any device or
        transport lock when cancelled. Exact restore races guarded writes against the safety
        interlock; a cancelled request has an uncertain outcome and its transport must not be
        reused unless the implementation can prove the request boundary is still intact.
        """

        ...
