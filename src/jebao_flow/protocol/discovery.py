"""Network discovery boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod

from jebao_flow.protocol.models import DiscoveredDevice


class DiscoveryProvider(ABC):
    @abstractmethod
    async def discover(self, *, timeout_seconds: float = 5.0) -> list[DiscoveredDevice]: ...
