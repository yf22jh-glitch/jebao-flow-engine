"""Low-level authenticated TCP session for one Gizwits GAgent device."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jebao_flow.protocol.codec import (
    GizwitsCommand,
    GizwitsFrame,
    decode_frame,
    encode_frame,
    read_frame,
)
from jebao_flow.protocol.errors import (
    AuthenticationError,
    ProtocolConnectionError,
    ProtocolError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)

if TYPE_CHECKING:
    from jebao_flow.protocol.control_session import GizwitsSession

CONTROL_ACTION = 0x01
READ_STATE_ACTION = 0x02
STATE_REPLY_ACTION = 0x03
STATE_REPORT_ACTION = 0x04
DEFAULT_CONTROL_PORT = 12416
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawStateCapture:
    """One exact state-reply wire frame plus its decoded serial payload."""

    wire_frame: bytes
    action: int
    status_payload: bytes

    @property
    def serial_payload(self) -> bytes:
        """Return the exact action+status payload received inside the Gizwits envelope."""

        return bytes((self.action,)) + self.status_payload


StateFrameObserver = Callable[[RawStateCapture, bool], None]
_ExchangeFrameObserver = Callable[[GizwitsFrame, bool], None]


def _raw_state_capture_from_frame(frame: GizwitsFrame) -> RawStateCapture:
    """Retain one already-decoded state frame without issuing another network read."""

    if not frame.payload:
        raise UnexpectedResponseError("device returned an empty state payload")
    action = frame.payload[0]
    if action not in {STATE_REPLY_ACTION, STATE_REPORT_ACTION}:
        raise UnexpectedResponseError(f"unexpected state action 0x{action:02x}")
    if frame.wire_bytes is None:
        raise UnexpectedResponseError("state response is missing its original wire frame")
    captured = decode_frame(frame.wire_bytes)
    if (
        captured.command != frame.command
        or captured.flag != frame.flag
        or captured.payload != frame.payload
    ):
        raise UnexpectedResponseError("state response wire frame does not match decoded state")
    return RawStateCapture(
        wire_frame=frame.wire_bytes,
        action=action,
        status_payload=frame.payload[1:],
    )


def _is_async_state_report(frame: GizwitsFrame) -> bool:
    """Return whether a frame is one valid unsolicited P0 state report.

    GAgent can broadcast action-0x04 reports to an authenticated LAN client while that client is
    waiting for an unrelated response.  These reports are expected asynchronous traffic, not
    evidence that the stream is desynchronised.  Empty or action-only 0x91 frames and every other
    action remain ordinary unexpected frames and therefore consume the bounded skip budget.
    """

    return (
        frame.command == GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
        and len(frame.payload) > 1
        and frame.payload[0] == STATE_REPORT_ACTION
    )


class ReadOnlyGizwitsSession:
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

    def quarantine(self) -> None:
        """Synchronously retire an uncertain stream without waiting for TCP close.

        Safety interlocks use this when an in-flight resolution is cancelled. The logical
        connection disappears immediately. Every current or already-closing transport is also
        aborted and removed from the reconnect gate; background tasks consume ``wait_closed``
        results without delaying a replacement session.
        """

        writer = self._drop_connection()
        if writer is not None:
            self._begin_writer_close(writer)
        for closing_writer, task in tuple(self._closing_writers.items()):
            self._abort_writer(closing_writer)
            self._closing_writers.pop(closing_writer, None)
            self._background_close_tasks.add(task)
            task.add_done_callback(self._finish_background_close)

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
        capture = await self.read_raw_state_capture(accept_reports=accept_reports)
        return capture.status_payload

    async def read_raw_state_capture(
        self,
        *,
        accept_reports: bool = False,
        state_frame_observer: StateFrameObserver | None = None,
    ) -> RawStateCapture:
        """Read state while retaining whether the serial payload was a reply or report.

        ``state_frame_observer`` is diagnostic only. It sees recognised state frames before the
        acceptance policy or any product-schema decoder can discard them. The boolean is true
        only when that frame satisfies this read. Observer failures never change transport
        selection, timeouts, or connection quarantine.
        """

        self._require_authenticated()

        accepted_actions = {STATE_REPLY_ACTION, STATE_REPORT_ACTION}
        if not accept_reports:
            accepted_actions.remove(STATE_REPORT_ACTION)

        def is_usable_state(frame: GizwitsFrame) -> bool:
            # A GAgent can interleave empty or unrelated 0x91 serial frames with the response to
            # this explicit read request. Keep consuming the bounded skip budget until a frame
            # actually carries a recognised state action. Product-specific length validation is
            # deliberately left to the schema decoder above this transport layer.
            return bool(frame.payload) and frame.payload[0] in accepted_actions

        def observe_state_frame(frame: GizwitsFrame, selected: bool) -> None:
            if (
                state_frame_observer is None
                or frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
                or not frame.payload
                or frame.payload[0] not in {STATE_REPLY_ACTION, STATE_REPORT_ACTION}
            ):
                return
            state_frame_observer(_raw_state_capture_from_frame(frame), selected)

        response = await self._exchange(
            GizwitsCommand.SERIAL_TRANSMIT_REQUEST,
            bytes([READ_STATE_ACTION]),
            expected={GizwitsCommand.SERIAL_TRANSMIT_RESPONSE},
            response_predicate=is_usable_state,
            frame_observer=observe_state_frame,
        )
        return _raw_state_capture_from_frame(response)

    async def _exchange(
        self,
        command: int | GizwitsCommand,
        payload: bytes = b"",
        *,
        expected: Collection[int | GizwitsCommand],
        response_predicate: Callable[[GizwitsFrame], bool] | None = None,
        frame_observer: _ExchangeFrameObserver | None = None,
    ) -> GizwitsFrame:
        reader, writer = self._require_connection()
        expected_values = {int(value) for value in expected}

        async with self._io_lock:
            try:
                async with asyncio.timeout(self.response_timeout_seconds):
                    writer.write(encode_frame(command, payload))
                    await writer.drain()
                    skipped_frames = 0
                    while skipped_frames <= self.max_skipped_frames:
                        frame = await read_frame(reader)
                        selected = frame.command in expected_values and (
                            response_predicate is None or response_predicate(frame)
                        )
                        if frame_observer is not None:
                            try:
                                frame_observer(frame, selected)
                            except Exception:
                                _LOGGER.warning("state_frame_observer_failed")
                        if selected:
                            return frame
                        if _is_async_state_report(frame):
                            _LOGGER.debug(
                                "skipped_async_state_report",
                                extra={"address": self.address},
                            )
                            continue
                        skipped_frames += 1
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
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # A synchronously rejected custom factory can theoretically return a connected
                # writer outside an event loop. Abort it; there is no loop on which wait_closed
                # could be owned or consumed.
                self._abort_writer(writer)
                return
            # Keep the task alive across a bounded reap timeout. Cancelling ``wait_closed`` can
            # cancel asyncio's shared close waiter and make later cleanup permanently impossible.
            self._closing_writers[writer] = loop.create_task(writer.wait_closed())

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
                self._abort_writer(writer)
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

    @staticmethod
    def _abort_writer(writer: asyncio.StreamWriter) -> None:
        transport = getattr(writer, "transport", None)
        abort = getattr(transport, "abort", None)
        if callable(abort):
            abort()

    def _require_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._reader is None or self._writer is None or self._writer.is_closing():
            raise ProtocolConnectionError("Gizwits session is not connected")
        return self._reader, self._writer

    def _require_authenticated(self) -> None:
        self._require_connection()
        if not self._authenticated:
            raise AuthenticationError("Gizwits session is not authenticated")


def __getattr__(name: str) -> object:
    """Resolve the legacy write-capable session only for callers that request it."""

    if name == "GizwitsSession":
        from jebao_flow.protocol.control_session import GizwitsSession

        globals()[name] = GizwitsSession
        return GizwitsSession
    raise AttributeError(name)


__all__ = [
    "CONTROL_ACTION",
    "DEFAULT_CONTROL_PORT",
    "GizwitsSession",
    "READ_STATE_ACTION",
    "RawStateCapture",
    "ReadOnlyGizwitsSession",
    "StateFrameObserver",
    "STATE_REPLY_ACTION",
    "STATE_REPORT_ACTION",
]
