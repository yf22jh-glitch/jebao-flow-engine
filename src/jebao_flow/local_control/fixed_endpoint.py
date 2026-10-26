"""Current identity checks at explicitly configured fixed IPv4 addresses.

Unlike a network inventory scan, a connected unicast probe completes when its
configured peer responds. MAC/vendor/product binding is still checked on every
call; no endpoint, firmware metadata or device state is cached. Unconfigured
members retain the original full discovery behavior.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable, Mapping
from ipaddress import IPv4Address

from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.resolver import (
    CurrentEndpointObservation,
    CurrentEndpointResolutionError,
    CurrentEndpointResolver,
    ResolvedEndpoint,
    _validate_targets,
    classify_current_observation,
)
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.discovery import DISCOVERY_PORT, parse_discovery_response
from jebao_flow.protocol.errors import ProtocolDecodeError


class FixedAddressEndpointResolver:
    def __init__(
        self,
        fallback: CurrentEndpointResolver,
        addresses: Mapping[str, str],
        *,
        raw_store: Callable[[bytes], str],
        bind_address: str = "0.0.0.0",
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("identity probe timeout must be positive")
        self._addresses = {
            member: str(IPv4Address(address)) for member, address in addresses.items()
        }
        if len(set(self._addresses.values())) != len(self._addresses):
            raise ValueError("fixed device addresses must be distinct")
        self._fallback = fallback
        self._raw_store = raw_store
        self._bind_address = bind_address
        self._timeout_seconds = timeout_seconds

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        return (await self.resolve_many((target,)))[0]

    async def resolve_many(
        self, targets: tuple[LocalDeviceBinding, ...]
    ) -> tuple[ResolvedEndpoint, ...]:
        observations = await self.observe_many(targets)
        if any(o.endpoint is None for o in observations):
            raise CurrentEndpointResolutionError(
                "current identity probe did not resolve every target"
            )
        return tuple(o.endpoint for o in observations)

    async def observe_many(
        self, targets: tuple[LocalDeviceBinding, ...]
    ) -> tuple[CurrentEndpointObservation, ...]:
        _validate_targets(targets)
        fixed = tuple(t for t in targets if t.logical_member in self._addresses)
        others = tuple(t for t in targets if t.logical_member not in self._addresses)
        if not fixed:
            return await self._fallback.observe_many(targets)
        tasks = [asyncio.create_task(self._probe(t)) for t in fixed]
        if others:
            tasks.append(asyncio.create_task(self._fallback.observe_many(others)))
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        by_member = {t.logical_member: o for t, o in zip(fixed, results[: len(fixed)], strict=True)}
        if others:
            by_member.update((o.target.logical_member, o) for o in results[-1])
        return tuple(by_member[t.logical_member] for t in targets)

    async def _probe(self, target: LocalDeviceBinding) -> CurrentEndpointObservation:
        address = self._addresses[target.logical_member]
        loop = asyncio.get_running_loop()
        response = loop.create_future()
        closed = loop.create_future()
        raw_store = self._raw_store

        class Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, peer: tuple[str, int]) -> None:
                try:
                    # Keep unknown/malformed packets as well as decoded replies.
                    raw_store(data)
                    if response.done():
                        return
                    device = parse_discovery_response(peer[0], data)
                    observation = classify_current_observation(target, [device])
                    if observation.endpoint is None:
                        raise CurrentEndpointResolutionError(
                            "fixed-address response does not match registered identity"
                        )
                    response.set_result(observation)
                except ProtocolDecodeError:
                    pass  # Await a valid frame within the existing full timeout.
                except Exception as error:
                    if not response.done():
                        response.set_exception(error)

            def error_received(self, error: Exception) -> None:
                if not response.done():
                    response.set_exception(error)

            def connection_lost(self, error: Exception | None) -> None:
                if not closed.done():
                    closed.set_result(None)

        transport, _ = await loop.create_datagram_endpoint(
            Protocol,
            local_addr=(self._bind_address, 0),
            remote_addr=(address, DISCOVERY_PORT),
            family=socket.AF_INET,
        )
        try:
            transport.sendto(encode_frame(GizwitsCommand.DISCOVER_REQUEST))
            try:
                return await asyncio.wait_for(response, self._timeout_seconds)
            except TimeoutError:
                return CurrentEndpointObservation(
                    target, diagnostic_reason="current_endpoint_absent"
                )
        finally:
            if not response.done():
                response.cancel()
            transport.close()
            await closed
