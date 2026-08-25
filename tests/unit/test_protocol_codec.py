import asyncio

import pytest

from jebao_flow.protocol.codec import (
    MAGIC,
    GizwitsCommand,
    GizwitsFrame,
    decode_frame,
    decode_varint,
    encode_frame,
    encode_varint,
    read_frame,
)
from jebao_flow.protocol.errors import (
    IncompleteFrameError,
    ProtocolConnectionError,
    ProtocolDecodeError,
)


@pytest.mark.parametrize(
    ("value", "encoded"),
    [(0, b"\x00"), (127, b"\x7f"), (128, b"\x80\x01"), (300, b"\xac\x02")],
)
def test_varint_known_vectors(value: int, encoded: bytes) -> None:
    assert encode_varint(value) == encoded
    assert decode_varint(encoded) == (value, len(encoded))


def test_discovery_request_matches_documented_wire_bytes() -> None:
    assert encode_frame(GizwitsCommand.DISCOVER_REQUEST) == bytes.fromhex(
        "0000000303000003"
    )


def test_frame_round_trip_preserves_nonzero_flag() -> None:
    payload = bytes(range(200))

    frame = decode_frame(encode_frame(0x0094, payload, flag=1))

    assert frame == GizwitsFrame(command=0x0094, payload=payload, flag=1)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        MAGIC,
        MAGIC + b"\x80\x80\x80\x80",
        MAGIC + b"\x02\x00\x00",
        MAGIC + b"\x04\x00\x00\x03",
        MAGIC + b"\x03\x00\x00\x03\xff",
    ],
)
def test_malformed_frames_are_rejected(data: bytes) -> None:
    with pytest.raises((IncompleteFrameError, ProtocolDecodeError)):
        decode_frame(data)


async def test_read_frame_reads_one_tcp_frame() -> None:
    reader = asyncio.StreamReader()
    raw = encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00")
    reader.feed_data(raw[:3])
    reader.feed_data(raw[3:])

    assert await read_frame(reader) == GizwitsFrame(
        command=GizwitsCommand.LOGIN_RESPONSE,
        payload=b"\x00",
    )


async def test_read_frame_reports_early_connection_close() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(MAGIC + b"\x04\x00")
    reader.feed_eof()

    with pytest.raises(ProtocolConnectionError, match="connection closed"):
        await read_frame(reader)

