import struct
from pathlib import Path

import pytest

from jebao_flow.operator_capture.model import CaptureOrigin
from jebao_flow.operator_capture.pcap import PcapIngestError, ingest_pcap_bytes
from jebao_flow.operator_capture.store import (
    DurableCaptureSession,
    read_public_capture_events,
)
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame

CLIENT = bytes([192, 0, 2, 10])
DEVICE = bytes([192, 0, 2, 20])
CLIENT_PORT = 40000
DEVICE_PORT = 12416


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "capture"
    root.mkdir(mode=0o700)
    return root


def _ethernet_tcp(
    payload: bytes,
    *,
    sequence: int,
    direction: str = "to-device",
    flags: int = 0x18,
    fragmented: bool = False,
) -> bytes:
    if direction == "to-device":
        source_address, destination_address = CLIENT, DEVICE
        source_port, destination_port = CLIENT_PORT, DEVICE_PORT
    else:
        source_address, destination_address = DEVICE, CLIENT
        source_port, destination_port = DEVICE_PORT, CLIENT_PORT
    tcp = (
        struct.pack(
            ">HHIIHHHH",
            source_port,
            destination_port,
            sequence,
            0,
            (5 << 12) | flags,
            65535,
            0,
            0,
        )
        + payload
    )
    total_length = 20 + len(tcp)
    ipv4 = (
        bytes([0x45, 0])
        + total_length.to_bytes(2, "big")
        + b"\x00\x00"
        + (b"\x20\x00" if fragmented else b"\x00\x00")
        + bytes([64, 6])
        + b"\x00\x00"
        + source_address
        + destination_address
        + tcp
    )
    return bytes(12) + b"\x08\x00" + ipv4


def _pcap(*packets: bytes) -> bytes:
    return _pcap_with_original_lengths(*((packet, len(packet)) for packet in packets))


def _pcap_with_original_lengths(*packets: tuple[bytes, int]) -> bytes:
    content = bytearray(b"\xd4\xc3\xb2\xa1")
    content.extend(struct.pack("<HHiiII", 2, 4, 0, 0, 65535, 1))
    for index, (packet, original_length) in enumerate(packets):
        content.extend(struct.pack("<IIII", 1_800_000_000 + index, 0, len(packet), original_length))
        content.extend(packet)
    return bytes(content)


def _ingest(tmp_path: Path, content: bytes):
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.IMPORTED_PCAP,
        source_label="pcap-test",
    )
    session_id = session.session_id
    result = ingest_pcap_bytes(content, session)
    verified = session.seal(
        status="desynchronized" if result.desynchronized_direction_count else "completed"
    )
    return result, verified, read_public_capture_events(root, session_id)


def test_pcap_reassembles_split_and_coalesced_frames(tmp_path: Path) -> None:
    first = encode_frame(GizwitsCommand.LOGIN_REQUEST, b"opaque")
    second = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    result, verified, events = _ingest(
        tmp_path,
        _pcap(
            _ethernet_tcp(first[:5], sequence=1000),
            _ethernet_tcp(first[5:] + second, sequence=1005),
        ),
    )

    assert result.target_packet_count == 2
    assert result.decoded_frame_count == 2
    assert result.desynchronized_direction_count == 0
    assert verified.status == "completed"
    assert len([event for event in events if event["kind"] == "frame"]) == 2
    rendered = str(events)
    assert "192.0.2" not in rendered
    assert "flow-000001" in rendered


def test_exact_tcp_retransmission_is_not_decoded_twice(tmp_path: Path) -> None:
    frame = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    packet = _ethernet_tcp(frame, sequence=2000)
    result, _verified, events = _ingest(tmp_path, _pcap(packet, packet))

    assert result.decoded_frame_count == 1
    retransmissions = [
        event
        for event in events
        if event["kind"] == "diagnostic"
        and event["payload"]["diagnostic_kind"] == "tcp_retransmission"
    ]
    assert len(retransmissions) == 1


def test_tcp_gap_marks_direction_desynchronized_and_preserves_later_raw(tmp_path: Path) -> None:
    frame = encode_frame(GizwitsCommand.LOGIN_REQUEST, b"opaque")
    later = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    result, verified, events = _ingest(
        tmp_path,
        _pcap(
            _ethernet_tcp(frame[:4], sequence=3000),
            _ethernet_tcp(frame[4:], sequence=3010),
            _ethernet_tcp(later, sequence=4000),
        ),
    )

    assert result.desynchronized_direction_count == 1
    assert result.decoded_frame_count == 0
    assert verified.status == "desynchronized"
    assert (
        len(
            [
                event
                for event in events
                if event["kind"] == "diagnostic"
                and event["payload"]["diagnostic_kind"] == "pcap_packet"
            ]
        )
        == 3
    )


def test_conflicting_overlap_never_resynchronizes(tmp_path: Path) -> None:
    frame = encode_frame(GizwitsCommand.LOGIN_REQUEST, b"opaque")
    result, _verified, events = _ingest(
        tmp_path,
        _pcap(
            _ethernet_tcp(frame[:8], sequence=5000),
            _ethernet_tcp(b"broken", sequence=5004),
            _ethernet_tcp(frame, sequence=6000),
        ),
    )

    assert result.desynchronized_direction_count == 1
    assert result.decoded_frame_count == 0
    reasons = [event["payload"]["reason"] for event in events if event["kind"] == "diagnostic"]
    assert "tcp_overlap_conflict" in reasons


def test_same_four_tuple_after_new_syn_gets_new_opaque_flow(tmp_path: Path) -> None:
    frame = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    result, _verified, events = _ingest(
        tmp_path,
        _pcap(
            _ethernet_tcp(b"", sequence=100, flags=0x02),
            _ethernet_tcp(frame, sequence=101),
            _ethernet_tcp(b"", sequence=101 + len(frame), flags=0x01),
            _ethernet_tcp(b"", sequence=900, flags=0x02),
            _ethernet_tcp(frame, sequence=901),
        ),
    )

    assert result.flow_count == 2
    frame_flows = [event["payload"]["flow_id"] for event in events if event["kind"] == "frame"]
    assert frame_flows == ["flow-000001", "flow-000002"]


def test_both_tcp_directions_decode_under_one_opaque_flow(tmp_path: Path) -> None:
    request = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    response = encode_frame(GizwitsCommand.HEARTBEAT_RESPONSE)

    result, _verified, events = _ingest(
        tmp_path,
        _pcap(
            _ethernet_tcp(request, sequence=1000),
            _ethernet_tcp(response, sequence=9000, direction="from-device"),
        ),
    )

    frames = [event["payload"] for event in events if event["kind"] == "frame"]
    assert result.flow_count == 1
    assert result.decoded_frame_count == 2
    assert [frame["direction"] for frame in frames] == [
        "controller_to_device",
        "device_to_controller",
    ]
    assert {frame["flow_id"] for frame in frames} == {"flow-000001"}


def test_new_client_syn_after_reverse_only_activity_starts_new_flow(tmp_path: Path) -> None:
    response = encode_frame(GizwitsCommand.HEARTBEAT_RESPONSE)
    request = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    result, _verified, events = _ingest(
        tmp_path,
        _pcap(
            _ethernet_tcp(b"", sequence=500, direction="from-device", flags=0x12),
            _ethernet_tcp(response, sequence=501, direction="from-device"),
            _ethernet_tcp(b"", sequence=900, flags=0x02),
            _ethernet_tcp(request, sequence=901),
        ),
    )

    assert result.flow_count == 2
    assert [event["payload"]["flow_id"] for event in events if event["kind"] == "frame"] == [
        "flow-000001",
        "flow-000002",
    ]


def test_snapshot_truncation_is_preserved_then_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    packet = _ethernet_tcp(encode_frame(GizwitsCommand.HEARTBEAT_REQUEST), sequence=1000)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.IMPORTED_PCAP,
        source_label="truncation-test",
    )
    session_id = session.session_id
    with pytest.raises(PcapIngestError, match="snapshot-truncated"):
        ingest_pcap_bytes(_pcap_with_original_lengths((packet, len(packet) + 20)), session)
    session.close_incomplete()

    events = read_public_capture_events(root, session_id)
    assert events[-1]["payload"]["diagnostic_kind"] == "pcap_truncated_packet"
    assert events[-1]["payload"]["private_blob_retained"] is True


def test_fragmented_ipv4_tcp_is_preserved_then_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    packet = _ethernet_tcp(
        encode_frame(GizwitsCommand.HEARTBEAT_REQUEST),
        sequence=1000,
        fragmented=True,
    )
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.IMPORTED_PCAP,
        source_label="fragment-test",
    )
    session_id = session.session_id
    with pytest.raises(PcapIngestError, match="fragmented IPv4 TCP"):
        ingest_pcap_bytes(_pcap(packet), session)
    session.close_incomplete()

    events = read_public_capture_events(root, session_id)
    assert events[-1]["payload"]["diagnostic_kind"] == "pcap_fragmented_packet"
    assert events[-1]["payload"]["private_blob_retained"] is True
