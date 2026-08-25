import asyncio
import struct

import pytest

from jebao_flow.protocol.codec import GizwitsCommand, decode_frame, encode_frame
from jebao_flow.protocol.discovery import GizwitsDiscovery, parse_discovery_response
from jebao_flow.protocol.errors import ProtocolDecodeError


def _length_prefixed(value: bytes) -> bytes:
    return struct.pack(">H", len(value)) + value


def _discovery_response() -> bytes:
    payload = b"".join(
        (
            _length_prefixed(b"device-id-123"),
            _length_prefixed(bytes.fromhex("d8f15b112a65")),
            _length_prefixed(b"04020006"),
            _length_prefixed(b"product-key-abc"),
            bytes.fromhex("0102030405060708"),
            b"api.gizwits.com\x00",
            b"4.0.8\x00",
            bytes.fromhex("aabb"),
        )
    )
    return encode_frame(GizwitsCommand.DISCOVER_RESPONSE, payload)


def test_parse_discovery_response_extracts_identity() -> None:
    device = parse_discovery_response("192.168.20.42", _discovery_response())

    assert device.address == "192.168.20.42"
    assert device.device_id == "device-id-123"
    assert device.mac_address == "d8:f1:5b:11:2a:65"
    assert device.product_key == "product-key-abc"
    assert device.wifi_firmware_version == "04020006"
    assert device.api_server == "api.gizwits.com"
    assert device.gizwits_version == "4.0.8"
    assert device.mcu_attributes_hex == "0102030405060708"
    assert device.extra_hex == "aabb"


def test_parse_discovery_response_rejects_wrong_command() -> None:
    with pytest.raises(ProtocolDecodeError, match="expected discovery response"):
        parse_discovery_response("192.168.20.42", encode_frame(0x0003))


def test_parse_discovery_response_rejects_truncated_fields() -> None:
    frame = decode_frame(_discovery_response())
    broken = encode_frame(frame.command, frame.payload[:-7])

    with pytest.raises(ProtocolDecodeError):
        parse_discovery_response("192.168.20.42", broken)


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        assert data == bytes.fromhex("0000000303000003")
        assert self.transport is not None
        self.transport.sendto(self.response, address)
        self.transport.sendto(self.response, address)


async def test_directed_discovery_collects_and_deduplicates_devices() -> None:
    loop = asyncio.get_running_loop()
    responder_transport, _ = await loop.create_datagram_endpoint(
        lambda: _Responder(_discovery_response()),
        local_addr=("127.0.0.1", 0),
    )
    port = responder_transport.get_extra_info("sockname")[1]
    try:
        discovery = GizwitsDiscovery(
            targets=("127.0.0.1",),
            bind_address="127.0.0.1",
            port=port,
        )

        devices = await discovery.discover(timeout_seconds=0.05)
    finally:
        responder_transport.close()

    assert len(devices) == 1
    assert devices[0].device_id == "device-id-123"

