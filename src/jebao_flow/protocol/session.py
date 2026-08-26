"""Low-level authenticated TCP session for one Gizwits GAgent device."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Collection

from jebao_flow.protocol.codec import GizwitsCommand, GizwitsFrame, encode_frame, read_frame
from jebao_flow.protocol.errors import (
    AuthenticationError,
    ProtocolConnectionError,
    ProtocolError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)

CONTROL_ACTION = 0x01
READ_STATE_ACTION = 0x02
STATE_REPLY_ACTION = 0x03
STATE_REPORT_ACTION = 0x04
DEFAULT_CONTROL_PORT = 12416
_LOGGER = logging.getLogger(__name__)


class GizwitsSession:
    """Serializes request/response exchanges over one authenticated TCP stream.

    This class deliberately exposes raw status and control payloads. Product-specific schema
    decoding and all safety checks belong to the device adapter above this layer.
    """

    def __init__(
        self,
        address: str,
        *,
        port: int = DEFAULT_CONTROL_PORT,
        connect_timeout_seconds: float = 5.0,
        response_timeout_seconds: float = 5.0,
        max_skipped_frames: int = 8,
    ) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if connect_timeout_seconds <= 0 or response_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if max_skipped_frames < 0:
            raise ValueError("max_skipped_frames must be non-negative")

        self.address = address
        self.port = port
        self.connect_timeout_seconds = connect_timeout_seconds
        self.response_timeout_seconds = response_timeout_seconds
        self.max_skipped_frames = max_skipped_frames
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._io_lock = asyncio.Lock()
        self._sequence = 0
        self._authenticated = False

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def authenticated(self) -> bool:
        return self.connected and self._authenticated

    async def connect(self) -> None:
        if self.connected:
            return
        try:
            async with asyncio.timeout(self.connect_timeout_seconds):
                self._reader, self._writer = await asyncio.open_connection(self.address, self.port)
        except TimeoutError as error:
            raise ProtocolTimeoutError(
                f"timed out connecting to {self.address}:{self.port}"
            ) from error
        except OSError as error:
            raise ProtocolConnectionError(
                f"could not connect to {self.address}:{self.port}: {error}"
            ) from error
        self._authenticated = False

    async def disconnect(self) -> None:
        writer = self._drop_connection()
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def authenticate(self) -> bytes:
        passcode_response = await self._exchange(
            GizwitsCommand.PASSCODE_REQUEST,
            expected={GizwitsCommand.PASSCODE_RESPONSE},
        )
        if len(passcode_response.payload) < 2:
            raise AuthenticationError("passcode response is too short")
        passcode_size = struct.unpack(">H", passcode_response.payload[:2])[0]
        passcode = passcode_response.payload[2 : 2 + passcode_size]
        if not passcode or len(passcode) != passcode_size:
            raise AuthenticationError("passcode response has an invalid length")

        login_response = await self._exchange(
            GizwitsCommand.LOGIN_REQUEST,
            struct.pack(">H", len(passcode)) + passcode,
            expected={GizwitsCommand.LOGIN_RESPONSE},
        )
        if not login_response.payload or login_response.payload[-1] != 0:
            raise AuthenticationError("device rejected the local login")

        self._authenticated = True
        return passcode

    async def heartbeat(self) -> None:
        self._require_authenticated()
        await self._exchange(
            GizwitsCommand.HEARTBEAT_REQUEST,
            expected={GizwitsCommand.HEARTBEAT_RESPONSE},
        )

    async def read_raw_state(self) -> bytes:
        self._require_authenticated()
        response = await self._exchange(
            GizwitsCommand.SERIAL_TRANSMIT_REQUEST,
            bytes([READ_STATE_ACTION]),
            expected={GizwitsCommand.SERIAL_TRANSMIT_RESPONSE},
        )
        if not response.payload:
            raise UnexpectedResponseError("device returned an empty state payload")
        action = response.payload[0]
        if action not in {STATE_REPLY_ACTION, STATE_REPORT_ACTION}:
            raise UnexpectedResponseError(f"unexpected state action 0x{action:02x}")
        return response.payload[1:]

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        """Send a schema-encoded control payload and return the raw ack body.

        Callers must apply capability and safety validation before invoking this method, then read
        actual state to verify the change. This method is intentionally absent from the diagnostic
        CLI.
        """

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

    async def _exchange(
        self,
        command: int | GizwitsCommand,
        payload: bytes = b"",
        *,
        expected: Collection[int | GizwitsCommand],
    ) -> GizwitsFrame:
        reader, writer = self._require_connection()
        expected_values = {int(value) for value in expected}

        async with self._io_lock:
            try:
                async with asyncio.timeout(self.response_timeout_seconds):
                    writer.write(encode_frame(command, payload))
                    await writer.drain()
                    for _ in range(self.max_skipped_frames + 1):
                        frame = await read_frame(reader)
                        if frame.command in expected_values:
                            return frame
                        _LOGGER.debug(
                            "skipped_unsolicited_frame",
                            extra={
                                "address": self.address,
                                "command": f"0x{frame.command:04x}",
                            },
                        )
            except TimeoutError as error:
                self._abort_connection()
                raise ProtocolTimeoutError(
                    f"timed out waiting for response from {self.address}:{self.port}"
                ) from error
            except asyncio.CancelledError:
                # read_frame consumes a TCP frame in pieces. Cancellation can leave the stream
                # between magic/length/body, so no later request may reuse this connection.
                self._abort_connection()
                raise
            except (ProtocolError, OSError):
                self._abort_connection()
                raise

        expected_text = ", ".join(f"0x{value:04x}" for value in sorted(expected_values))
        self._abort_connection()
        raise UnexpectedResponseError(
            f"no expected response ({expected_text}) after skipping "
            f"{self.max_skipped_frames} frames"
        )

    def _drop_connection(self) -> asyncio.StreamWriter | None:
        writer = self._writer
        self._reader = None
        self._writer = None
        self._authenticated = False
        return writer

    def _abort_connection(self) -> None:
        """Quarantine a stream whose frame boundary or request outcome is uncertain."""

        writer = self._drop_connection()
        if writer is not None:
            writer.close()

    def _require_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._reader is None or self._writer is None or self._writer.is_closing():
            raise ProtocolConnectionError("Gizwits session is not connected")
        return self._reader, self._writer

    def _require_authenticated(self) -> None:
        self._require_connection()
        if not self._authenticated:
            raise AuthenticationError("Gizwits session is not authenticated")
