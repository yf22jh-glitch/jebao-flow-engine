"""Codec for the Gizwits GAgent LAN frame envelope."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum

from jebao_flow.protocol.errors import (
    FrameTooLargeError,
    IncompleteFrameError,
    ProtocolConnectionError,
    ProtocolDecodeError,
)

MAGIC = b"\x00\x00\x00\x03"
MAX_VARINT_BYTES = 4
DEFAULT_MAX_FRAME_BODY_SIZE = 1024 * 1024
MIN_FRAME_BODY_SIZE = 3  # flag + 16-bit command


class GizwitsCommand(IntEnum):
    ONBOARD_REQUEST = 0x0001
    ONBOARD_RESPONSE = 0x0002
    DISCOVER_REQUEST = 0x0003
    DISCOVER_RESPONSE = 0x0004
    STARTUP_BROADCAST = 0x0005
    PASSCODE_REQUEST = 0x0006
    PASSCODE_RESPONSE = 0x0007
    LOGIN_REQUEST = 0x0008
    LOGIN_RESPONSE = 0x0009
    HEARTBEAT_REQUEST = 0x0015
    HEARTBEAT_RESPONSE = 0x0016
    SERIAL_TRANSMIT_REQUEST = 0x0090
    SERIAL_TRANSMIT_RESPONSE = 0x0091
    SERIAL_CONTROL_REQUEST = 0x0093
    SERIAL_CONTROL_RESPONSE = 0x0094


@dataclass(frozen=True, slots=True)
class GizwitsFrame:
    command: int
    payload: bytes = b""
    flag: int = 0


def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer using the GAgent LEB128 length format."""

    if value < 0:
        raise ValueError("varint value must be non-negative")
    if value >= 1 << (7 * MAX_VARINT_BYTES):
        raise ValueError("varint value exceeds the four-byte GAgent limit")

    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        encoded.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(encoded)


def decode_varint(data: bytes | bytearray | memoryview, offset: int = 0) -> tuple[int, int]:
    """Return ``(value, consumed_bytes)`` for a bounded GAgent length varint."""

    if offset < 0:
        raise ValueError("offset must be non-negative")

    value = 0
    for index in range(MAX_VARINT_BYTES):
        position = offset + index
        if position >= len(data):
            raise IncompleteFrameError("incomplete frame length varint")
        byte = data[position]
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return value, index + 1
    raise ProtocolDecodeError("frame length varint exceeds four bytes")


def encode_frame(
    command: int | GizwitsCommand,
    payload: bytes = b"",
    *,
    flag: int = 0,
    max_body_size: int = DEFAULT_MAX_FRAME_BODY_SIZE,
) -> bytes:
    command_value = int(command)
    if not 0 <= command_value <= 0xFFFF:
        raise ValueError("command must fit in 16 bits")
    if not 0 <= flag <= 0xFF:
        raise ValueError("flag must fit in one byte")
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")

    body = bytes([flag]) + struct.pack(">H", command_value) + payload
    if len(body) > max_body_size:
        raise FrameTooLargeError(f"frame body exceeds {max_body_size} bytes")
    return MAGIC + encode_varint(len(body)) + body


def decode_frame(
    data: bytes,
    *,
    max_body_size: int = DEFAULT_MAX_FRAME_BODY_SIZE,
) -> GizwitsFrame:
    if not data.startswith(MAGIC):
        raise ProtocolDecodeError("frame has invalid GAgent magic")

    body_size, varint_size = decode_varint(data, len(MAGIC))
    if body_size < MIN_FRAME_BODY_SIZE:
        raise ProtocolDecodeError("frame body is too short for flag and command")
    if body_size > max_body_size:
        raise FrameTooLargeError(f"frame body exceeds {max_body_size} bytes")

    body_start = len(MAGIC) + varint_size
    body_end = body_start + body_size
    if len(data) < body_end:
        raise IncompleteFrameError(
            f"frame body declares {body_size} bytes but only {len(data) - body_start} are present"
        )
    if len(data) > body_end:
        raise ProtocolDecodeError("frame contains trailing bytes")

    body = data[body_start:body_end]
    command = struct.unpack(">H", body[1:3])[0]
    return GizwitsFrame(command=command, payload=body[3:], flag=body[0])


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_body_size: int = DEFAULT_MAX_FRAME_BODY_SIZE,
) -> GizwitsFrame:
    """Read exactly one bounded GAgent frame from a TCP stream."""

    try:
        magic = await reader.readexactly(len(MAGIC))
        if magic != MAGIC:
            raise ProtocolDecodeError("stream has invalid GAgent magic")

        encoded_length = bytearray()
        for _ in range(MAX_VARINT_BYTES):
            byte = await reader.readexactly(1)
            encoded_length.extend(byte)
            if not byte[0] & 0x80:
                break
        else:
            raise ProtocolDecodeError("frame length varint exceeds four bytes")

        body_size, _ = decode_varint(encoded_length)
        if body_size < MIN_FRAME_BODY_SIZE:
            raise ProtocolDecodeError("frame body is too short for flag and command")
        if body_size > max_body_size:
            raise FrameTooLargeError(f"frame body exceeds {max_body_size} bytes")

        body = await reader.readexactly(body_size)
    except asyncio.IncompleteReadError as error:
        raise ProtocolConnectionError("connection closed while reading a frame") from error

    return decode_frame(magic + bytes(encoded_length) + body, max_body_size=max_body_size)
