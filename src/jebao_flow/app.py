"""Command-line entry point for ``jebao-flowd``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Sequence
from pathlib import Path

from jebao_flow.config import AppConfig, load_config
from jebao_flow.logging import configure_logging

_LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flowd")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="YAML configuration path (default: config.yaml)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate the configuration and exit",
    )
    return parser


async def serve(config: AppConfig) -> None:
    """Run the daemon shell until a termination signal is received.

    Protocol and MQTT workers are intentionally not started in the initial scaffold. Keeping the
    process lifecycle real makes the same container usable as those workers are added.
    """

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - Windows event loops
            pass

    _LOGGER.info(
        "daemon_started",
        extra={
            "instance_id": config.instance.id,
            "dry_run": config.runtime.dry_run,
            "device_count": len(config.devices),
            "group_count": len(config.groups),
        },
    )
    _LOGGER.warning("protocol_and_mqtt_workers_not_implemented")
    await stop_event.wait()
    _LOGGER.info("daemon_stopped", extra={"instance_id": config.instance.id})


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    configure_logging(config.runtime.log_level)

    if args.check_config:
        logging.getLogger(__name__).info(
            "configuration_valid",
            extra={"config_path": str(args.config), "instance_id": config.instance.id},
        )
        return 0

    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:  # pragma: no cover - terminal convenience
        pass
    return 0

