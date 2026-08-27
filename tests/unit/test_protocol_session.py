import asyncio
import struct

import pytest

from jebao_flow.protocol.codec import MAGIC, GizwitsCommand, encode_frame, read_frame
from jebao_flow.protocol.errors import (
    AuthenticationError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION, STATE_REPORT_ACTION, GizwitsSession


async def test_quarantine_drops_logical_connection_without_waiting_for_tcp_close() -> None:
    release_close = asyncio.Event()

    class Transport:
        aborted = False

        def abort(self) -> None:
            self.aborted = True

    class SlowWriter:
        closing = False

        def __init__(self) -> None:
            self.transport = Transport()

        def close(self) -> None:
            self.closing = True

        def is_closing(self) -> bool:
            return self.closing

        async def wait_closed(self) -> None:
            await release_close.wait()

    session = GizwitsSession("127.0.0.1")
    writer = SlowWriter()
    session._reader = asyncio.StreamReader()  # noqa: SLF001
    session._writer = writer  # type: ignore[assignment]  # noqa: SLF001
    session._authenticated = True  # noqa: SLF001

    session.quarantine()

    assert session.connected is False
    assert session.authenticated is False
    assert writer.transport.aborted is True
    assert not session._closing_writers  # noqa: SLF001
    assert len(session._background_close_tasks) == 1  # noqa: SLF001
    release_close.set()
    await asyncio.gather(*tuple(session._background_close_tasks))  # noqa: SLF001
    await asyncio.sleep(0)
    await session.disconnect()
    assert not session._closing_writers  # noqa: SLF001
    assert not session._background_close_tasks  # noqa: SLF001


async def test_session_authenticates_skips_unsolicited_and_reads_state() -> None:
    received: list[tuple[int, bytes]] = []
    completed = asyncio.Event()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_frame(reader)
            received.append((request.command, request.payload))
            passcode = b"abc123"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            writer.write(encode_frame(0x0062, b"unsolicited"))
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(
                encode_frame(GizwitsCommand.SERIAL_TRANSMIT_RESPONSE, b"\x04\x10\x20\x30")
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.HEARTBEAT_RESPONSE))
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            sequence = request.payload[:4]
            writer.write(
                encode_frame(GizwitsCommand.SERIAL_CONTROL_RESPONSE, sequence + b"ack")
            )
            await writer.drain()
            completed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port)
    try:
        await session.connect()
        returned_passcode = await session.authenticate()
        state = await session.read_raw_state()
        await session.heartbeat()
        ack = await session.send_raw_control(b"\x01\x80\x00")
        await asyncio.wait_for(completed.wait(), timeout=1)
    finally:
        await session.disconnect()
        server.close()
        await server.wait_closed()

    assert returned_passcode == b"abc123"
    assert state == b"\x10\x20\x30"
    assert ack == b"ack"
    assert received == [
        (GizwitsCommand.PASSCODE_REQUEST, b""),
        (GizwitsCommand.LOGIN_REQUEST, b"\x00\x06abc123"),
        (GizwitsCommand.SERIAL_TRANSMIT_REQUEST, b"\x02"),
        (GizwitsCommand.HEARTBEAT_REQUEST, b""),
        (GizwitsCommand.SERIAL_CONTROL_REQUEST, b"\x00\x00\x00\x01\x01\x80\x00"),
    ]


async def test_explicit_state_read_skips_reports_and_empty_payload_without_requery() -> None:
    received: list[tuple[int, bytes]] = []

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_frame(reader)
            received.append((request.command, request.payload))
            passcode = b"abc123"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.SERIAL_TRANSMIT_RESPONSE, b""))
            writer.write(
                encode_frame(
                    GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                    bytes([STATE_REPORT_ACTION]) + b"reported-state",
                )
            )
            writer.write(
                encode_frame(
                    GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                    bytes([STATE_REPLY_ACTION]) + b"explicit-state",
                )
            )
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port, max_skipped_frames=2)
    try:
        await session.connect()
        await session.authenticate()
        state = await session.read_raw_state(accept_reports=False)
    finally:
        await session.disconnect()
        server.close()
        await server.wait_closed()

    assert state == b"explicit-state"
    assert received == [
        (GizwitsCommand.PASSCODE_REQUEST, b""),
        (GizwitsCommand.LOGIN_REQUEST, b"\x00\x06abc123"),
        (GizwitsCommand.SERIAL_TRANSMIT_REQUEST, b"\x02"),
    ]


async def test_report_capable_state_read_skips_empty_and_wrong_action_without_requery() -> None:
    received: list[tuple[int, bytes]] = []

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_frame(reader)
            received.append((request.command, request.payload))
            passcode = b"abc123"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.SERIAL_TRANSMIT_RESPONSE, b""))
            writer.write(encode_frame(GizwitsCommand.SERIAL_TRANSMIT_RESPONSE, b"\x7fwrong"))
            writer.write(
                encode_frame(
                    GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                    bytes([STATE_REPORT_ACTION]) + b"reported-state",
                )
            )
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port, max_skipped_frames=2)
    try:
        await session.connect()
        await session.authenticate()
        state = await session.read_raw_state(accept_reports=True)
    finally:
        await session.disconnect()
        server.close()
        await server.wait_closed()

    assert state == b"reported-state"
    assert received == [
        (GizwitsCommand.PASSCODE_REQUEST, b""),
        (GizwitsCommand.LOGIN_REQUEST, b"\x00\x06abc123"),
        (GizwitsCommand.SERIAL_TRANSMIT_REQUEST, b"\x02"),
    ]


async def test_explicit_state_read_exhausts_skip_budget_without_requery() -> None:
    received: list[tuple[int, bytes]] = []

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_frame(reader)
            received.append((request.command, request.payload))
            passcode = b"abc123"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            for state in (b"first-report", b"second-report"):
                writer.write(
                    encode_frame(
                        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                        bytes([STATE_REPORT_ACTION]) + state,
                    )
                )
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port, max_skipped_frames=1)
    try:
        await session.connect()
        await session.authenticate()
        with pytest.raises(UnexpectedResponseError, match="no expected response"):
            await session.read_raw_state(accept_reports=False)
    finally:
        await session.disconnect()
        server.close()
        await server.wait_closed()

    assert received == [
        (GizwitsCommand.PASSCODE_REQUEST, b""),
        (GizwitsCommand.LOGIN_REQUEST, b"\x00\x06abc123"),
        (GizwitsCommand.SERIAL_TRANSMIT_REQUEST, b"\x02"),
    ]


@pytest.mark.parametrize("cancel_read", [False, True])
async def test_incomplete_exchange_quarantines_the_tcp_session(cancel_read: bool) -> None:
    state_request_received = asyncio.Event()
    release_server = asyncio.Event()
    server_completed = asyncio.Event()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_frame(reader)
            assert request.command == GizwitsCommand.PASSCODE_REQUEST
            passcode = b"abc123"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            assert request.command == GizwitsCommand.LOGIN_REQUEST
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            await writer.drain()

            request = await read_frame(reader)
            assert request.command == GizwitsCommand.SERIAL_TRANSMIT_REQUEST
            # Leave read_frame between its magic and length/body so either timeout or external
            # cancellation would poison this connection if it remained reusable.
            writer.write(MAGIC)
            await writer.drain()
            state_request_received.set()
            await release_server.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            server_completed.set()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port, response_timeout_seconds=0.05)
    try:
        await session.connect()
        await session.authenticate()
        read_task = asyncio.create_task(session.read_raw_state())
        await asyncio.wait_for(state_request_received.wait(), timeout=1)
        if cancel_read:
            read_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await read_task
        else:
            with pytest.raises(ProtocolTimeoutError):
                await read_task

        assert session.connected is False
        assert session.authenticated is False
    finally:
        release_server.set()
        await session.disconnect()
        await asyncio.wait_for(server_completed.wait(), timeout=1)
        server.close()
        await server.wait_closed()


async def test_quarantined_session_reconnects_reauthenticates_and_reads_fresh_state() -> None:
    accepted_connections = 0
    completed_connections = 0
    all_connections_completed = asyncio.Event()
    received: list[tuple[int, int]] = []

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted_connections, completed_connections
        accepted_connections += 1
        connection_number = accepted_connections
        try:
            request = await read_frame(reader)
            received.append((connection_number, request.command))
            assert request.command == GizwitsCommand.PASSCODE_REQUEST
            passcode = b"abc123"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((connection_number, request.command))
            assert request.command == GizwitsCommand.LOGIN_REQUEST
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            await writer.drain()

            request = await read_frame(reader)
            received.append((connection_number, request.command))
            assert request.command == GizwitsCommand.SERIAL_TRANSMIT_REQUEST
            if connection_number == 1:
                # Poison the first stream mid-frame. The timeout must quarantine it before the
                # same session object opens and authenticates a second TCP connection.
                writer.write(MAGIC)
                await writer.drain()
            else:
                writer.write(
                    encode_frame(
                        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                        bytes([STATE_REPLY_ACTION]) + b"fresh-state",
                    )
                )
                await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            completed_connections += 1
            if completed_connections == 2:
                all_connections_completed.set()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port, response_timeout_seconds=0.05)
    try:
        await session.connect()
        assert await session.authenticate() == b"abc123"
        with pytest.raises(ProtocolTimeoutError):
            await session.read_raw_state()
        assert session.connected is False
        assert session.authenticated is False
        assert len(session._closing_writers) == 1  # noqa: SLF001

        await session.connect()
        assert not session._closing_writers  # noqa: SLF001
        assert await session.authenticate() == b"abc123"
        assert await session.read_raw_state() == b"fresh-state"
        assert session.connected is True
        assert session.authenticated is True
    finally:
        await session.disconnect()
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(all_connections_completed.wait(), timeout=1)

    assert accepted_connections == 2
    assert received == [
        (1, GizwitsCommand.PASSCODE_REQUEST),
        (1, GizwitsCommand.LOGIN_REQUEST),
        (1, GizwitsCommand.SERIAL_TRANSMIT_REQUEST),
        (2, GizwitsCommand.PASSCODE_REQUEST),
        (2, GizwitsCommand.LOGIN_REQUEST),
        (2, GizwitsCommand.SERIAL_TRANSMIT_REQUEST),
    ]


async def test_stalled_transport_close_blocks_reconnect_for_only_a_bounded_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_close = asyncio.Event()
    open_connection_calls = 0

    class Transport:
        aborted = False

        def abort(self) -> None:
            self.aborted = True

    class StalledWriter:
        def __init__(self) -> None:
            self.transport = Transport()

        def close(self) -> None:
            pass

        def is_closing(self) -> bool:
            return False

        async def wait_closed(self) -> None:
            await release_close.wait()

    class FreshWriter:
        def __init__(self) -> None:
            self.transport = Transport()
            self.closing = False

        def close(self) -> None:
            self.closing = True

        def is_closing(self) -> bool:
            return self.closing

        async def wait_closed(self) -> None:
            return None

    fresh_writer = FreshWriter()

    async def open_connection(
        *args: object,
        **kwargs: object,
    ) -> tuple[asyncio.StreamReader, FreshWriter]:
        nonlocal open_connection_calls
        del args, kwargs
        open_connection_calls += 1
        return asyncio.StreamReader(), fresh_writer

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    session = GizwitsSession("127.0.0.1", connect_timeout_seconds=0.01)
    writer = StalledWriter()
    session._writer = writer  # type: ignore[assignment]  # noqa: SLF001
    session._abort_connection()  # noqa: SLF001

    with pytest.raises(ProtocolTimeoutError, match="timed out closing"):
        await session.connect()

    assert open_connection_calls == 0
    assert session.connected is False
    assert writer.transport.aborted is True
    assert not session._closing_writers  # noqa: SLF001

    # A permanently stalled close waiter cannot consume the reconnect gate forever. The first
    # bounded failure remains visible, while the following explicit retry may open a fresh slot.
    await session.connect()
    assert open_connection_calls == 1
    assert session.connected is True

    release_close.set()
    await asyncio.gather(*tuple(session._background_close_tasks))  # noqa: SLF001
    await asyncio.sleep(0)
    assert not session._background_close_tasks  # noqa: SLF001
    await session.disconnect()


@pytest.mark.parametrize(
    ("failure_mode", "error_pattern"),
    [
        ("short_passcode", "too short"),
        ("empty_passcode", "invalid length"),
        ("truncated_passcode", "invalid length"),
        ("rejected_login", "rejected the local login"),
        ("cancelled", None),
    ],
)
async def test_failed_authentication_discards_session_and_allows_fresh_reauthentication(
    failure_mode: str,
    error_pattern: str | None,
) -> None:
    accepted_connections = 0
    completed_connections = 0
    all_connections_completed = asyncio.Event()
    first_auth_request = asyncio.Event()
    received: list[tuple[int, int]] = []
    server_errors: list[Exception] = []

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted_connections, completed_connections
        accepted_connections += 1
        connection_number = accepted_connections
        try:
            request = await read_frame(reader)
            received.append((connection_number, request.command))
            assert request.command == GizwitsCommand.PASSCODE_REQUEST

            if connection_number == 1 and failure_mode == "cancelled":
                first_auth_request.set()
                await reader.read()
                return

            if connection_number == 1 and failure_mode == "short_passcode":
                passcode_payload = b"\x00"
            elif connection_number == 1 and failure_mode == "empty_passcode":
                passcode_payload = struct.pack(">H", 0)
            elif connection_number == 1 and failure_mode == "truncated_passcode":
                passcode_payload = struct.pack(">H", 6) + b"abc"
            else:
                passcode = b"abc123"
                passcode_payload = struct.pack(">H", len(passcode)) + passcode
            writer.write(
                encode_frame(GizwitsCommand.PASSCODE_RESPONSE, passcode_payload)
            )
            await writer.drain()

            if connection_number == 1 and failure_mode in {
                "short_passcode",
                "empty_passcode",
                "truncated_passcode",
            }:
                await reader.read()
                return

            request = await read_frame(reader)
            received.append((connection_number, request.command))
            assert request.command == GizwitsCommand.LOGIN_REQUEST
            login_status = b"\x01" if connection_number == 1 else b"\x00"
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, login_status))
            await writer.drain()
            await reader.read()
        except Exception as error:  # pragma: no cover - asserted empty below
            server_errors.append(error)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            completed_connections += 1
            if completed_connections == 2:
                all_connections_completed.set()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = GizwitsSession("127.0.0.1", port=port, response_timeout_seconds=0.2)
    try:
        await session.connect()
        if failure_mode == "cancelled":
            authenticate_task = asyncio.create_task(session.authenticate())
            await asyncio.wait_for(first_auth_request.wait(), timeout=1)
            authenticate_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await authenticate_task
        else:
            if error_pattern is None:  # pragma: no cover - parameter table invariant
                raise AssertionError("authentication error case has no expected pattern")
            with pytest.raises(AuthenticationError, match=error_pattern):
                await session.authenticate()

        assert session.connected is False
        assert session.authenticated is False

        await session.connect()
        assert await session.authenticate() == b"abc123"
        assert session.connected is True
        assert session.authenticated is True
    finally:
        await session.disconnect()
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(all_connections_completed.wait(), timeout=1)

    assert accepted_connections == 2
    assert (2, GizwitsCommand.PASSCODE_REQUEST) in received
    assert (2, GizwitsCommand.LOGIN_REQUEST) in received
    assert server_errors == []
