"""Classic-PCAP ingestion with privacy-bounded TCP reassembly.

The importer never opens a network interface. It consumes an already captured classic-PCAP byte
stream, retains exact matching packet records in the private evidence store, and only decodes a TCP
direction while sequence continuity remains proven.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

from jebao_flow.operator_capture.catalog import summarize_frame
from jebao_flow.operator_capture.model import CaptureDirection
from jebao_flow.operator_capture.store import CaptureStoreError, DurableCaptureSession
from jebao_flow.operator_capture.stream import FrameStreamDecoder

_PCAP_HEADER_SIZE = 24
_PCAP_RECORD_HEADER_SIZE = 16
_MAX_CAPTURED_PACKET_SIZE = 16 * 1024 * 1024
_DLT_EN10MB = 1
_DLT_LINUX_SLL = 113
_DLT_LINUX_SLL2 = 276
_ETHERTYPE_IPV4 = 0x0800
_VLAN_ETHERTYPES = {0x8100, 0x88A8, 0x9100}
_TCP_PROTOCOL = 6
_TCP_FIN = 0x01
_TCP_SYN = 0x02
_TCP_RST = 0x04
_TCP_ACK = 0x10
_SEQUENCE_MODULUS = 1 << 32
_SEQUENCE_HALF = 1 << 31
_MAX_RETRANSMISSION_HISTORY = 1024 * 1024


class PcapIngestError(CaptureStoreError):
    """A PCAP could not be interpreted without guessing at missing bytes."""


@dataclass(frozen=True, slots=True)
class PcapImportResult:
    packet_count: int
    target_packet_count: int
    decoded_frame_count: int
    flow_count: int
    desynchronized_direction_count: int

    def public_dict(self) -> dict[str, int]:
        return {
            "packet_count": self.packet_count,
            "target_packet_count": self.target_packet_count,
            "decoded_frame_count": self.decoded_frame_count,
            "flow_count": self.flow_count,
            "desynchronized_direction_count": self.desynchronized_direction_count,
        }


@dataclass(frozen=True, slots=True)
class _PcapFormat:
    endian: str
    timestamp_scale: int
    snaplen: int
    link_type: int


@dataclass(frozen=True, slots=True)
class _TcpPacket:
    client_address: bytes = field(repr=False)
    client_port: int
    device_address: bytes = field(repr=False)
    device_port: int
    direction: CaptureDirection
    sequence: int
    flags: int
    payload: bytes = field(repr=False)

    @property
    def connection_key(self) -> tuple[bytes, int, bytes, int]:
        return (
            self.client_address,
            self.client_port,
            self.device_address,
            self.device_port,
        )


@dataclass(slots=True)
class _DirectionState:
    decoder: FrameStreamDecoder = field(default_factory=FrameStreamDecoder)
    expected_absolute: int | None = None
    history_start_absolute: int | None = None
    history: bytearray = field(default_factory=bytearray, repr=False)
    desynchronized: bool = False
    closed: bool = False
    initial_payload_sequence: int | None = None


@dataclass(slots=True)
class _FlowState:
    flow_id: str
    directions: dict[CaptureDirection, _DirectionState] = field(
        default_factory=lambda: {
            CaptureDirection.CONTROLLER_TO_DEVICE: _DirectionState(),
            CaptureDirection.DEVICE_TO_CONTROLLER: _DirectionState(),
        }
    )


class PcapImporter:
    def __init__(
        self,
        session: DurableCaptureSession,
        *,
        device_port: int = 12416,
        product_key: str | None = None,
    ) -> None:
        if not 1 <= device_port <= 65535:
            raise ValueError("device port must be between 1 and 65535")
        self._session = session
        self._device_port = device_port
        self._product_key = product_key
        self._flows: dict[tuple[bytes, int, bytes, int], _FlowState] = {}
        self._flow_history: list[_FlowState] = []
        self._next_flow = 1
        self._packet_count = 0
        self._target_packet_count = 0
        self._decoded_frame_count = 0
        self._first_timestamp_ns: int | None = None

    def ingest(self, source: BinaryIO) -> PcapImportResult:
        global_header = _read_exact(source, _PCAP_HEADER_SIZE, "pcap_global_header")
        pcap_format = _parse_global_header(global_header)
        self._session.append_diagnostic(
            kind="pcap_header",
            flow_id=None,
            reason="classic_pcap_header",
            length=len(global_header),
            raw_bytes=global_header,
        )
        while True:
            record_header = source.read(_PCAP_RECORD_HEADER_SIZE)
            if not record_header:
                break
            if len(record_header) != _PCAP_RECORD_HEADER_SIZE:
                raise PcapIngestError("PCAP ends inside a packet-record header")
            seconds, fraction, included, original = struct.unpack(
                f"{pcap_format.endian}IIII", record_header
            )
            if included > pcap_format.snaplen or included > _MAX_CAPTURED_PACKET_SIZE:
                raise PcapIngestError("PCAP packet exceeds its declared capture bound")
            if original < included:
                raise PcapIngestError("PCAP packet original length is smaller than captured length")
            packet_bytes = _read_exact(source, included, "pcap_packet")
            self._packet_count += 1
            timestamp_ns = seconds * 1_000_000_000 + fraction * pcap_format.timestamp_scale
            if fraction >= 1_000_000_000 // pcap_format.timestamp_scale:
                raise PcapIngestError("PCAP packet timestamp fraction is invalid")
            if self._first_timestamp_ns is None:
                self._first_timestamp_ns = timestamp_ns
            elapsed_ns = timestamp_ns - self._first_timestamp_ns
            if elapsed_ns < 0:
                raise PcapIngestError("PCAP packet timestamps move backwards")
            observed_utc = _format_unix_ns(timestamp_ns)
            if original > included:
                self._session.append_diagnostic(
                    kind="pcap_truncated_packet",
                    flow_id=None,
                    reason="snapshot_length_omitted_bytes",
                    length=len(record_header) + len(packet_bytes),
                    raw_bytes=record_header + packet_bytes,
                    observed_utc=observed_utc,
                    capture_elapsed_ns=elapsed_ns,
                )
                raise PcapIngestError("PCAP contains a snapshot-truncated packet")
            if _has_fragmented_ipv4_tcp(packet_bytes, pcap_format.link_type):
                self._session.append_diagnostic(
                    kind="pcap_fragmented_packet",
                    flow_id=None,
                    reason="fragmented_tcp_cannot_be_proven_complete",
                    length=len(record_header) + len(packet_bytes),
                    raw_bytes=record_header + packet_bytes,
                    observed_utc=observed_utc,
                    capture_elapsed_ns=elapsed_ns,
                )
                raise PcapIngestError("PCAP contains fragmented IPv4 TCP traffic")
            decoded = _decode_tcp_packet(packet_bytes, pcap_format.link_type, self._device_port)
            if decoded is None:
                continue
            self._target_packet_count += 1
            flow = self._flow_for(decoded)
            # Preserve the exact original PCAP record header and captured link-layer bytes. The
            # event contains no endpoint address; flow IDs are allocation-order opaque tokens.
            self._session.append_diagnostic(
                kind="pcap_packet",
                flow_id=flow.flow_id,
                reason="target_tcp_packet",
                length=len(record_header) + len(packet_bytes),
                raw_bytes=record_header + packet_bytes,
                observed_utc=observed_utc,
                capture_elapsed_ns=elapsed_ns,
                direction=decoded.direction,
            )
            self._accept_tcp_packet(flow, decoded, observed_utc, elapsed_ns)

        self._finish_open_streams()
        desynchronized = sum(
            direction.desynchronized
            for flow in self._flow_history
            for direction in flow.directions.values()
        )
        return PcapImportResult(
            packet_count=self._packet_count,
            target_packet_count=self._target_packet_count,
            decoded_frame_count=self._decoded_frame_count,
            flow_count=len(self._flow_history),
            desynchronized_direction_count=desynchronized,
        )

    def _flow_for(self, packet: _TcpPacket) -> _FlowState:
        flow = self._flows.get(packet.connection_key)
        if flow is not None and packet.flags & _TCP_SYN and not packet.flags & _TCP_ACK:
            direction = flow.directions[packet.direction]
            payload_sequence = (packet.sequence + 1) % _SEQUENCE_MODULUS
            has_activity = any(
                state.initial_payload_sequence is not None
                or state.expected_absolute is not None
                or state.closed
                or state.desynchronized
                for state in flow.directions.values()
            )
            if direction.initial_payload_sequence is None and has_activity:
                flow = None
            elif direction.initial_payload_sequence not in {None, payload_sequence}:
                flow = None
        if flow is None:
            if self._next_flow > 999999:
                raise PcapIngestError("capture flow id space is exhausted")
            flow = _FlowState(flow_id=f"flow-{self._next_flow:06d}")
            self._flows[packet.connection_key] = flow
            self._flow_history.append(flow)
            self._next_flow += 1
        return flow

    def _accept_tcp_packet(
        self,
        flow: _FlowState,
        packet: _TcpPacket,
        observed_utc: str,
        elapsed_ns: int,
    ) -> None:
        state = flow.directions[packet.direction]
        if state.desynchronized:
            return
        if state.closed:
            if not packet.payload and packet.flags & (_TCP_FIN | _TCP_RST):
                return
            self._desynchronize(flow, packet.direction, state, "tcp_payload_after_close")
            return
        payload_sequence = (packet.sequence + bool(packet.flags & _TCP_SYN)) % _SEQUENCE_MODULUS
        if packet.flags & _TCP_SYN:
            if (
                state.initial_payload_sequence is not None
                and state.initial_payload_sequence != payload_sequence
            ):
                self._desynchronize(flow, packet.direction, state, "tcp_reused_after_syn")
                return
            if state.initial_payload_sequence is None:
                state.initial_payload_sequence = payload_sequence
                state.expected_absolute = payload_sequence
                state.history_start_absolute = payload_sequence
        if not packet.payload:
            if packet.flags & (_TCP_FIN | _TCP_RST):
                self._finish_direction(flow, packet.direction, state)
            return
        if state.expected_absolute is None:
            if not packet.payload.startswith(b"\x00\x00\x00\x03"):
                self._desynchronize(flow, packet.direction, state, "capture_started_midstream")
                return
            state.expected_absolute = payload_sequence
            state.history_start_absolute = payload_sequence

        absolute_sequence = _unwrap_sequence(payload_sequence, state.expected_absolute)
        if absolute_sequence > state.expected_absolute:
            self._desynchronize(flow, packet.direction, state, "tcp_sequence_gap")
            return
        payload = packet.payload
        if absolute_sequence < state.expected_absolute:
            history_start = state.history_start_absolute
            if history_start is None or absolute_sequence < history_start:
                self._desynchronize(flow, packet.direction, state, "tcp_overlap_outside_history")
                return
            overlap = min(state.expected_absolute - absolute_sequence, len(payload))
            history_offset = absolute_sequence - history_start
            if payload[:overlap] != state.history[history_offset : history_offset + overlap]:
                self._desynchronize(flow, packet.direction, state, "tcp_overlap_conflict")
                return
            if overlap == len(payload):
                self._session.append_diagnostic(
                    kind="tcp_retransmission",
                    flow_id=flow.flow_id,
                    reason="wire_identical_duplicate",
                    offset=absolute_sequence,
                    length=len(payload),
                    observed_utc=observed_utc,
                    capture_elapsed_ns=elapsed_ns,
                )
                return
            payload = payload[overlap:]

        frames = state.decoder.feed(payload)
        self._extend_history(state, payload)
        state.expected_absolute += len(payload)
        for frame in frames:
            if frame.wire_bytes is None:
                raise PcapIngestError("decoded frame did not retain its wire bytes")
            self._session.append_frame(
                frame.wire_bytes,
                direction=packet.direction,
                flow_id=flow.flow_id,
                summary=summarize_frame(frame, product_key=self._product_key),
                observed_utc=observed_utc,
                capture_elapsed_ns=elapsed_ns,
            )
            self._decoded_frame_count += 1
        if state.decoder.desynchronized:
            diagnostic = state.decoder.desynchronization
            assert diagnostic is not None
            self._desynchronize(flow, packet.direction, state, diagnostic.reason)
            return
        if packet.flags & (_TCP_FIN | _TCP_RST):
            self._finish_direction(flow, packet.direction, state)

    def _extend_history(self, state: _DirectionState, payload: bytes) -> None:
        state.history.extend(payload)
        if len(state.history) <= _MAX_RETRANSMISSION_HISTORY:
            return
        remove = len(state.history) - _MAX_RETRANSMISSION_HISTORY
        del state.history[:remove]
        assert state.history_start_absolute is not None
        state.history_start_absolute += remove

    def _finish_direction(
        self,
        flow: _FlowState,
        direction: CaptureDirection,
        state: _DirectionState,
    ) -> None:
        state.closed = True
        diagnostic = state.decoder.finish()
        if diagnostic is not None:
            self._desynchronize(flow, direction, state, diagnostic.reason)

    def _finish_open_streams(self) -> None:
        for flow in self._flow_history:
            for direction, state in flow.directions.items():
                if state.expected_absolute is not None and not state.desynchronized:
                    self._finish_direction(flow, direction, state)

    def _desynchronize(
        self,
        flow: _FlowState,
        direction: CaptureDirection,
        state: _DirectionState,
        reason: str,
    ) -> None:
        if state.desynchronized:
            return
        state.desynchronized = True
        self._session.append_diagnostic(
            kind="stream_desynchronized",
            flow_id=flow.flow_id,
            reason=reason,
            offset=state.expected_absolute,
            length=state.decoder.buffered_length,
            direction=direction,
        )


def ingest_pcap_bytes(
    content: bytes,
    session: DurableCaptureSession,
    *,
    device_port: int = 12416,
    product_key: str | None = None,
) -> PcapImportResult:
    """Test/offline convenience wrapper. The content is never rendered or returned."""

    if not isinstance(content, bytes):
        raise TypeError("PCAP content must be bytes")
    return PcapImporter(
        session,
        device_port=device_port,
        product_key=product_key,
    ).ingest(io.BytesIO(content))


def _parse_global_header(header: bytes) -> _PcapFormat:
    variants = {
        b"\xd4\xc3\xb2\xa1": ("<", 1000),
        b"\xa1\xb2\xc3\xd4": (">", 1000),
        b"\x4d\x3c\xb2\xa1": ("<", 1),
        b"\xa1\xb2\x3c\x4d": (">", 1),
    }
    try:
        endian, timestamp_scale = variants[header[:4]]
    except KeyError as error:
        raise PcapIngestError("only classic PCAP input is supported") from error
    major, minor, _zone, _sigfigs, snaplen, link_type = struct.unpack(f"{endian}HHiiII", header[4:])
    if (major, minor) != (2, 4):
        raise PcapIngestError("PCAP version is unsupported")
    if not 1 <= snaplen <= _MAX_CAPTURED_PACKET_SIZE:
        raise PcapIngestError("PCAP snapshot length is invalid")
    if link_type not in {_DLT_EN10MB, _DLT_LINUX_SLL, _DLT_LINUX_SLL2}:
        raise PcapIngestError("PCAP link-layer type is unsupported")
    return _PcapFormat(endian, timestamp_scale, snaplen, link_type)


def _decode_tcp_packet(packet: bytes, link_type: int, device_port: int) -> _TcpPacket | None:
    ipv4 = _extract_ipv4(packet, link_type)
    if ipv4 is None or len(ipv4) < 20:
        return None
    version_ihl = ipv4[0]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ihl < 20 or len(ipv4) < ihl:
        return None
    total_length = int.from_bytes(ipv4[2:4], "big")
    if total_length < ihl or total_length > len(ipv4):
        return None
    fragment = int.from_bytes(ipv4[6:8], "big")
    if fragment & 0x3FFF or ipv4[9] != _TCP_PROTOCOL:
        return None
    source_address = bytes(ipv4[12:16])
    destination_address = bytes(ipv4[16:20])
    tcp = ipv4[ihl:total_length]
    if len(tcp) < 20:
        return None
    source_port, destination_port = struct.unpack(">HH", tcp[:4])
    if source_port != device_port and destination_port != device_port:
        return None
    data_offset = (tcp[12] >> 4) * 4
    if data_offset < 20 or data_offset > len(tcp):
        return None
    sequence = int.from_bytes(tcp[4:8], "big")
    flags = tcp[13]
    payload = bytes(tcp[data_offset:])
    if destination_port == device_port:
        return _TcpPacket(
            client_address=source_address,
            client_port=source_port,
            device_address=destination_address,
            device_port=destination_port,
            direction=CaptureDirection.CONTROLLER_TO_DEVICE,
            sequence=sequence,
            flags=flags,
            payload=payload,
        )
    return _TcpPacket(
        client_address=destination_address,
        client_port=destination_port,
        device_address=source_address,
        device_port=source_port,
        direction=CaptureDirection.DEVICE_TO_CONTROLLER,
        sequence=sequence,
        flags=flags,
        payload=payload,
    )


def _extract_ipv4(packet: bytes, link_type: int) -> bytes | None:
    if link_type == _DLT_EN10MB:
        if len(packet) < 14:
            return None
        offset = 14
        ethertype = int.from_bytes(packet[12:14], "big")
        while ethertype in _VLAN_ETHERTYPES:
            if len(packet) < offset + 4:
                return None
            ethertype = int.from_bytes(packet[offset + 2 : offset + 4], "big")
            offset += 4
        return packet[offset:] if ethertype == _ETHERTYPE_IPV4 else None
    if link_type == _DLT_LINUX_SLL:
        if len(packet) < 16 or int.from_bytes(packet[14:16], "big") != _ETHERTYPE_IPV4:
            return None
        return packet[16:]
    if link_type == _DLT_LINUX_SLL2:
        if len(packet) < 20 or int.from_bytes(packet[:2], "big") != _ETHERTYPE_IPV4:
            return None
        return packet[20:]
    return None


def _has_fragmented_ipv4_tcp(packet: bytes, link_type: int) -> bool:
    ipv4 = _extract_ipv4(packet, link_type)
    if ipv4 is None or len(ipv4) < 20 or ipv4[0] >> 4 != 4:
        return False
    ihl = (ipv4[0] & 0x0F) * 4
    if ihl < 20 or len(ipv4) < ihl or ipv4[9] != _TCP_PROTOCOL:
        return False
    fragment = int.from_bytes(ipv4[6:8], "big")
    return bool(fragment & 0x3FFF)


def _unwrap_sequence(sequence: int, reference_absolute: int) -> int:
    reference_low = reference_absolute % _SEQUENCE_MODULUS
    delta = (sequence - reference_low + _SEQUENCE_HALF) % _SEQUENCE_MODULUS - _SEQUENCE_HALF
    return reference_absolute + delta


def _read_exact(source: BinaryIO, size: int, description: str) -> bytes:
    content = source.read(size)
    if len(content) != size:
        raise PcapIngestError(f"PCAP ends inside {description}")
    return content


def _format_unix_ns(timestamp_ns: int) -> str:
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    value = datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=nanoseconds // 1000)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = ["PcapImportResult", "PcapImporter", "PcapIngestError", "ingest_pcap_bytes"]
