"""Write-capable Gizwits session kept outside the read-only collector import graph."""

from __future__ import annotations

import struct

from jebao_flow.protocol.codec import GizwitsCommand
from jebao_flow.protocol.errors import UnexpectedResponseError
from jebao_flow.protocol.session import CONTROL_ACTION, ReadOnlyGizwitsSession


class GizwitsSession(ReadOnlyGizwitsSession):
    """Authenticated session that additionally exposes raw hardware control writes."""

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        """Send a schema-encoded control payload and return the raw ack body."""

        self._require_authenticated()
        if not control_payload or control_payload[0] != CONTROL_ACTION:
            raise ValueError("control payload must begin with action 0x01")

        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        sequence = self._sequence
        response = await self._exchange(
            GizwitsCommand.SERIAL_CONTROL_REQUEST,
            struct.pack(">I", sequence) + control_payload,
            expected={GizwitsCommand.SERIAL_CONTROL_RESPONSE},
        )
        if len(response.payload) < 4:
            raise UnexpectedResponseError("control response is missing its sequence number")
        response_sequence = struct.unpack(">I", response.payload[:4])[0]
        if response_sequence != sequence:
            raise UnexpectedResponseError(
                f"control response sequence mismatch: expected {sequence}, got {response_sequence}"
            )
        return response.payload[4:]


__all__ = ["GizwitsSession"]
