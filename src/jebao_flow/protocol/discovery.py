"""UDP discovery for Gizwits GAgent devices."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from abc import ABC, abstractmethod
from collections.abc import Sequence

from jebao_flow.protocol.codec import GizwitsCommand, decode_frame, encode_frame
from jebao_flow.protocol.errors import ProtocolDecodeError
from jebao_flow.protocol.models import DiscoveredDevice

DISCOVERY_PORT = 12414
DEFAULT_DISCOVERY_TARGET = "255.255.255.255"
_LOGGER = logging.getLogger(__name__)


class DiscoveryProvider(ABC):
    @abstractmethod
    async def discover(self, *, timeout_seconds: float = 5.0) -> list[DiscoveredDevice]: ...


class _PayloadCursor:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.position = 0

    def read_exactly(self, size: int) -> bytes:
        end = self.position + size
        if end > len(self.payload):
            raise ProtocolDecodeError("discovery payload is truncated")
        value = self.payload[self.position:end]
        self.position = end
        return value

    def read_length_prefixed(self) -> bytes:
        size = struct.unpack(">H", self.read_exactly(2))[0]
        return self.read_exactly(size)

    def read_ascii(self, field_name: str) -> str:
        raw = self.read_length_prefixed()
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError as error:
            raise ProtocolDecodeError(f"discovery {field_name} is not ASCII") from error

    def read_c_string(self, field_name: str) -> str:
        try:
            end = self.payload.index(0, self.position)
        except ValueError as error:
            raise ProtocolDecodeError(f"discovery {field_name} is not null-terminated") from error
        raw = self.payload[self.position:end]
        self.position = end + 1
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError as error:
            raise ProtocolDecodeError(f"discovery {field_name} is not ASCII") from error

    def remaining(self) -> bytes:
        value = self.payload[self.position :]
        self.position = len(self.payload)
        return value


def parse_discovery_response(address: str, data: bytes) -> DiscoveredDevice:
    frame = decode_frame(data)
    if frame.command != GizwitsCommand.DISCOVER_RESPONSE:
        raise ProtocolDecodeError(
            f"expected discovery response command 0x0004, got 0x{frame.command:04x}"
        )

    cursor = _PayloadCursor(frame.payload)
    device_id = cursor.read_ascii("device id")
    if not device_id:
        raise ProtocolDecodeError("discovery device id is empty")
    mac = cursor.read_length_prefixed()
    wifi_firmware = cursor.read_ascii("Wi-Fi firmware")
    product_key = cursor.read_ascii("product key")
    mcu_attributes = cursor.read_exactly(8)
    api_server = cursor.read_c_string("API server")
    gizwits_version = cursor.read_c_string("Gizwits version")

    return DiscoveredDevice(
        address=address,
        device_id=device_id,
        mac_address=mac.hex(":"),
        product_key=product_key or None,
        wifi_firmware_version=wifi_firmware or None,
        api_server=api_server or None,
        gizwits_version=gizwits_version or None,
        mcu_attributes_hex=mcu_attributes.hex(),
        extra_hex=cursor.remaining().hex(),
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.devices: dict[tuple[str, str], DiscoveredDevice] = {}

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            device = parse_discovery_response(address[0], data)
        except ProtocolDecodeError as error:
            _LOGGER.debug(
                "ignored_invalid_discovery_response",
                extra={"source_address": address[0], "error": str(error)},
            )
            return
        # Keep conflicting addresses visible so the binding resolver can reject ambiguity.
        self.devices[(device.device_id, device.address)] = device

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("discovery_socket_error", extra={"error": str(exc)})


class GizwitsDiscovery(DiscoveryProvider):
    def __init__(
        self,
        *,
        targets: Sequence[str] = (DEFAULT_DISCOVERY_TARGET,),
        bind_address: str = "0.0.0.0",
        port: int = DISCOVERY_PORT,
    ) -> None:
        if not targets:
            raise ValueError("at least one discovery target is required")
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.targets = tuple(targets)
        self.bind_address = bind_address
        self.port = port

    async def discover(self, *, timeout_seconds: float = 5.0) -> list[DiscoveredDevice]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            _DiscoveryProtocol,
            local_addr=(self.bind_address, 0),
            family=socket.AF_INET,
            allow_broadcast=True,
        )
        try:
            request = encode_frame(GizwitsCommand.DISCOVER_REQUEST)
            for target in self.targets:
                transport.sendto(request, (target, self.port))
            await asyncio.sleep(timeout_seconds)
        finally:
            transport.close()

        return sorted(
            protocol.devices.values(),
            key=lambda device: (device.address, device.device_id),
        )
