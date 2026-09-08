import pytest

from jebao_flow.operator_capture.stream import FrameStreamDecoder
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame


def test_stream_decoder_accepts_partial_and_coalesced_frames() -> None:
    first = encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00")
    second = encode_frame(GizwitsCommand.HEARTBEAT_RESPONSE)
    decoder = FrameStreamDecoder()

    assert decoder.feed(first[:3]) == ()
    frames = decoder.feed(first[3:] + second)

    assert [frame.command for frame in frames] == [
        GizwitsCommand.LOGIN_RESPONSE,
        GizwitsCommand.HEARTBEAT_RESPONSE,
    ]
    assert [frame.wire_bytes for frame in frames] == [first, second]
    assert decoder.finish() is None


def test_stream_decoder_never_scans_forward_after_invalid_magic() -> None:
    valid = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    decoder = FrameStreamDecoder()

    assert decoder.feed(b"invalid" + valid) == ()
    assert decoder.desynchronized
    assert decoder.desynchronization is not None
    assert decoder.desynchronization.reason == "invalid_magic"
    assert decoder.feed(valid) == ()


def test_stream_decoder_reports_truncated_terminal_frame() -> None:
    decoder = FrameStreamDecoder()
    decoder.feed(encode_frame(GizwitsCommand.LOGIN_REQUEST, b"token")[:-1])

    diagnostic = decoder.finish()

    assert diagnostic is not None
    assert diagnostic.reason == "truncated_frame"


def test_stream_decoder_rejects_non_bytes_chunks() -> None:
    decoder = FrameStreamDecoder()

    with pytest.raises(TypeError, match="chunks must be bytes"):
        decoder.feed(bytearray(b"x"))  # type: ignore[arg-type]
