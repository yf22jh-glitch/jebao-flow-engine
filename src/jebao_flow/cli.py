"""Read-only diagnostics for discovering local Jebao/Gizwits devices."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from jebao_flow.logging import configure_logging
from jebao_flow.protocol.discovery import DEFAULT_DISCOVERY_TARGET, GizwitsDiscovery
from jebao_flow.protocol.models import DiscoveredDevice


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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        if args.command == "discover":
            return asyncio.run(_discover(args))
    except (OSError, ValueError) as error:
        print(f"discovery failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":  # pragma: no cover - exercised by the installed console script
    raise SystemExit(main())
