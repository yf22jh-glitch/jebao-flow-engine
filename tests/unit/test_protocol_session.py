import asyncio
import struct

import pytest

from jebao_flow.protocol.codec import MAGIC, GizwitsCommand, encode_frame, read_frame
from jebao_flow.protocol.errors import ProtocolTimeoutError
from jebao_flow.protocol.session import STATE_REPLY_ACTION, GizwitsSession


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

        await session.connect()
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
