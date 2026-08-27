"""Low-level authenticated TCP session for one Gizwits GAgent device."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable, Collection

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
        self._closing_writers: dict[asyncio.StreamWriter, asyncio.Task[None]] = {}
        self._background_close_tasks: set[asyncio.Task[None]] = set()
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
        await self._reap_closing_writers()
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
        if writer is not None:
            self._begin_writer_close(writer)
        await self._reap_closing_writers()

    async def authenticate(self) -> bytes:
        try:
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
        except BaseException:
            # Content validation and login rejection happen after a complete protocol exchange,
            # so ``_exchange`` has no reason to quarantine that stream itself. Authentication is
            # nevertheless an all-or-nothing session boundary: cancellation or any failure must
            # synchronously discard the half-authenticated connection before control returns.
            # A prior ``_exchange`` abort is harmless because ``_drop_connection`` is idempotent.
            self._abort_connection()
            raise

    async def heartbeat(self) -> None:
        self._require_authenticated()
        await self._exchange(
            GizwitsCommand.HEARTBEAT_REQUEST,
            expected={GizwitsCommand.HEARTBEAT_RESPONSE},
        )

    async def read_raw_state(self, *, accept_reports: bool = True) -> bytes:
        self._require_authenticated()

        response_predicate: Callable[[GizwitsFrame], bool] | None = None
        if not accept_reports:

            def is_explicit_state_reply(frame: GizwitsFrame) -> bool:
                return bool(frame.payload) and frame.payload[0] == STATE_REPLY_ACTION

            response_predicate = is_explicit_state_reply

        response = await self._exchange(
            GizwitsCommand.SERIAL_TRANSMIT_REQUEST,
            bytes([READ_STATE_ACTION]),
            expected={GizwitsCommand.SERIAL_TRANSMIT_RESPONSE},
            response_predicate=response_predicate,
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
        response_predicate: Callable[[GizwitsFrame], bool] | None = None,
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
                        if frame.command in expected_values and (
                            response_predicate is None or response_predicate(frame)
                        ):
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
            # ``_abort_connection`` is intentionally synchronous so cancellation can quarantine
            # a partial frame immediately. Keep the closing writer reachable so disconnect or
            # the next connect can await the FIN instead of racing a scarce device socket slot.
            self._begin_writer_close(writer)

    def _begin_writer_close(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        if writer not in self._closing_writers:
            # Keep the task alive across a bounded reap timeout. Cancelling ``wait_closed`` can
            # cancel asyncio's shared close waiter and make later cleanup permanently impossible.
            self._closing_writers[writer] = asyncio.create_task(writer.wait_closed())

    async def _reap_closing_writers(self) -> None:
        """Wait a bounded time for every quarantined transport to actually close."""

        if not self._closing_writers:
            return
        done, _ = await asyncio.wait(
            set(self._closing_writers.values()),
            timeout=self.connect_timeout_seconds,
        )
        for writer, task in tuple(self._closing_writers.items()):
            if task not in done:
                continue
            self._consume_close_task_result(task)
            self._closing_writers.pop(writer, None)
        if self._closing_writers:
            stalled = tuple(self._closing_writers.items())
            for writer, task in stalled:
                # ``close()`` already requested a graceful FIN. If its completion waiter stalls,
                # abort the local transport and remove it from the reconnect gate. Keep consuming
                # the waiter in the background so a broken close cannot permanently block the
                # fresh session needed for rollback.
                transport = getattr(writer, "transport", None)
                abort = getattr(transport, "abort", None)
                if callable(abort):
                    abort()
                self._closing_writers.pop(writer, None)
                self._background_close_tasks.add(task)
                task.add_done_callback(self._finish_background_close)
            raise ProtocolTimeoutError(
                f"timed out closing {len(stalled)} quarantined Gizwits connection(s)"
            )

    def _finish_background_close(self, task: asyncio.Task[None]) -> None:
        self._background_close_tasks.discard(task)
        self._consume_close_task_result(task)

    @staticmethod
    def _consume_close_task_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except OSError:
            pass
        except asyncio.CancelledError:
            pass

    def _require_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._reader is None or self._writer is None or self._writer.is_closing():
            raise ProtocolConnectionError("Gizwits session is not connected")
        return self._reader, self._writer

    def _require_authenticated(self) -> None:
        self._require_connection()
        if not self._authenticated:
            raise AuthenticationError("Gizwits session is not authenticated")
