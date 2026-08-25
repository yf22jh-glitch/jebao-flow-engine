"""Interface implemented by a concrete LAN protocol connection."""

from __future__ import annotations

from abc import ABC, abstractmethod

from jebao_flow.protocol.models import DeviceState, DeviceTarget


class ProtocolConnection(ABC):
    """One authenticated connection to one physical controller."""

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def read_state(self) -> DeviceState: ...

    @abstractmethod
    async def write_target(self, target: DeviceTarget) -> None: ...

