"""Read-only diagnostics for local Jebao/Gizwits devices."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from jebao_flow.logging import configure_logging
from jebao_flow.protocol.discovery import DEFAULT_DISCOVERY_TARGET, GizwitsDiscovery
from jebao_flow.protocol.errors import ProtocolError
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.session import DEFAULT_CONTROL_PORT, GizwitsSession


@dataclass(frozen=True, slots=True)
class ProbeResult:
    address: str
    success: bool
    state_size: int | None = None
    state_hex: str | None = None
    error: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flowctl")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="find Gizwits devices using UDP")
    discover.add_argument(
        "--target",
        action="append",
        help="broadcast or unicast target; repeat for multiple VLANs",
    )
    discover.add_argument("--bind", default="0.0.0.0", help="local IPv4 address to bind")
    discover.add_argument("--port", type=int, default=12414, help="destination UDP port")
    discover.add_argument("--timeout", type=float, default=5.0, help="response window in seconds")
    discover.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    probe = subparsers.add_parser("probe", help="authenticate and read raw state without writes")
    probe.add_argument("address", nargs="+", help="one or more device IPv4 addresses")
    probe.add_argument("--port", type=int, default=DEFAULT_CONTROL_PORT, help="device TCP port")
    probe.add_argument("--timeout", type=float, default=5.0, help="connect/response timeout")
    probe.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _print_devices(devices: list[DiscoveredDevice], *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                [device.model_dump(mode="json") for device in devices],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not devices:
        print("No Gizwits/Jebao devices responded.")
        return

    for device in devices:
        print(f"{device.address}  {device.device_id}")
        print(f"  product_key: {device.product_key or '-'}")
        print(f"  mac: {device.mac_address or '-'}")
        print(f"  wifi_firmware: {device.wifi_firmware_version or '-'}")
        print(f"  gizwits_version: {device.gizwits_version or '-'}")


async def _discover(args: argparse.Namespace) -> int:
    discovery = GizwitsDiscovery(
        targets=args.target or (DEFAULT_DISCOVERY_TARGET,),
        bind_address=args.bind,
        port=args.port,
    )
    devices = await discovery.discover(timeout_seconds=args.timeout)
    _print_devices(devices, as_json=args.json)
    return 0


def _print_probe_results(results: list[ProbeResult], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
        return

    for result in results:
        if result.success:
            print(f"{result.address}  state_size={result.state_size}")
            print(f"  state_hex: {result.state_hex}")
        else:
            print(f"{result.address}  probe failed: {result.error}")


async def _probe_one(address: str, *, port: int, timeout_seconds: float) -> ProbeResult:
    session = GizwitsSession(
        address,
        port=port,
        connect_timeout_seconds=timeout_seconds,
        response_timeout_seconds=timeout_seconds,
    )
    try:
        await session.connect()
        await session.authenticate()
        state = await session.read_raw_state()
        return ProbeResult(
            address=address,
            success=True,
            state_size=len(state),
            state_hex=state.hex(),
        )
    except ProtocolError as error:
        return ProbeResult(address=address, success=False, error=str(error))
    finally:
        await session.disconnect()


async def _probe(args: argparse.Namespace) -> int:
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    results = [
        await _probe_one(address, port=args.port, timeout_seconds=args.timeout)
        for address in args.address
    ]
    _print_probe_results(results, as_json=args.json)
    return 0 if all(result.success for result in results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        if args.command == "discover":
            return asyncio.run(_discover(args))
        if args.command == "probe":
            return asyncio.run(_probe(args))
    except (OSError, ValueError) as error:
        print(f"command failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":  # pragma: no cover - exercised by the installed console script
    raise SystemExit(main())
