import asyncio
import struct

from jebao_flow.protocol.codec import GizwitsCommand, encode_frame, read_frame
from jebao_flow.protocol.session import GizwitsSession


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
