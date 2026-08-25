"""Binary codec boundary for the future Gizwits/Jebao driver."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProtocolCodec(ABC):
    @abstractmethod
    def encode_command(self, command: str, value: Any) -> bytes: ...

    @abstractmethod
    def decode_state(self, payload: bytes) -> dict[str, Any]: ...
