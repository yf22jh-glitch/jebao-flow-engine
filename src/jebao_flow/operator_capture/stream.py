"""Incremental, fail-closed GAgent frame boundary recognition."""

from __future__ import annotations

from dataclasses import dataclass

from jebao_flow.protocol.codec import (
    DEFAULT_MAX_FRAME_BODY_SIZE,
    MAGIC,
    MAX_VARINT_BYTES,
    MIN_FRAME_BODY_SIZE,
    GizwitsFrame,
    decode_frame,
)


@dataclass(frozen=True, slots=True)
class StreamDesynchronization:
    reason: str
    offset: int
    buffered_length: int


class FrameStreamDecoder:
    """Split arbitrary TCP chunks without scanning forward after protocol damage."""

    def __init__(self, *, max_body_size: int = DEFAULT_MAX_FRAME_BODY_SIZE) -> None:
        if max_body_size < MIN_FRAME_BODY_SIZE:
            raise ValueError("maximum frame body size is too small")
        self.max_body_size = max_body_size
        self._buffer = bytearray()
        self._stream_offset = 0
        self._desynchronization: StreamDesynchronization | None = None

    @property
    def desynchronized(self) -> bool:
        return self._desynchronization is not None

    @property
    def desynchronization(self) -> StreamDesynchronization | None:
        return self._desynchronization

    @property
    def buffered_length(self) -> int:
        return len(self._buffer)

    def feed(self, chunk: bytes) -> tuple[GizwitsFrame, ...]:
        if not isinstance(chunk, bytes):
            raise TypeError("stream chunks must be bytes")
        if not chunk:
            return ()
        if self.desynchronized:
            self._buffer.extend(chunk)
            return ()
        self._buffer.extend(chunk)
        frames: list[GizwitsFrame] = []
        while self._buffer:
            if len(self._buffer) < len(MAGIC):
                break
            if bytes(self._buffer[: len(MAGIC)]) != MAGIC:
                self._mark_desynchronized("invalid_magic")
                break
            decoded_length = self._peek_body_length()
            if decoded_length is None:
                break
            body_size, varint_size = decoded_length
            if body_size < MIN_FRAME_BODY_SIZE:
                self._mark_desynchronized("body_too_short")
                break
            if body_size > self.max_body_size:
                self._mark_desynchronized("body_too_large")
                break
            frame_size = len(MAGIC) + varint_size + body_size
            if len(self._buffer) < frame_size:
                break
            wire = bytes(self._buffer[:frame_size])
            try:
                frame = decode_frame(wire, max_body_size=self.max_body_size)
            except Exception:
                self._mark_desynchronized("frame_decode_failed")
                break
            del self._buffer[:frame_size]
            self._stream_offset += frame_size
            frames.append(frame)
        return tuple(frames)

    def finish(self) -> StreamDesynchronization | None:
        if not self.desynchronized and self._buffer:
            self._mark_desynchronized("truncated_frame")
        return self._desynchronization

    def _peek_body_length(self) -> tuple[int, int] | None:
        value = 0
        start = len(MAGIC)
        for index in range(MAX_VARINT_BYTES):
            position = start + index
            if position >= len(self._buffer):
                return None
            byte = self._buffer[position]
            value |= (byte & 0x7F) << (7 * index)
            if not byte & 0x80:
                return value, index + 1
        self._mark_desynchronized("length_varint_too_long")
        return None

    def _mark_desynchronized(self, reason: str) -> None:
        if self._desynchronization is None:
            self._desynchronization = StreamDesynchronization(
                reason=reason,
                offset=self._stream_offset,
                buffered_length=len(self._buffer),
            )
