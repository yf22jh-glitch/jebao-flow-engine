"""Source-module CLI for bounded pre-namespace MQTT discovery observations.

Capture is write-free: the MQTT client has no will, uses a fresh clean-session identifier, and
never publishes.  Inspect and selection are offline artifact operations.  This module deliberately
has no installed console-script entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiomqtt
import yaml

from jebao_flow.legacy_discovery_inventory import (
    InventoryContext,
    InventoryObservation,
    LegacyDiscoveryArtifactStore,
    LegacyDiscoveryInventoryBuilder,
    LegacyDiscoveryInventoryError,
    load_selection_request,
)

_MAX_CONFIG_BYTES = 8 * 1024 * 1024
_MIN_WINDOW_SECONDS = 0.001
_MAX_WINDOW_SECONDS = 300.0
_PRIVATE_BROKER_LOGGER = logging.Logger(f"{__name__}.broker")
_PRIVATE_BROKER_LOGGER.addHandler(logging.NullHandler())
_PRIVATE_BROKER_LOGGER.propagate = False


@dataclass(frozen=True, slots=True, repr=False)
class _InventoryMqttConfig:
    host: str
    port: int
    username: str | None
    password_env: str | None
    discovery_prefix: str
    topic_prefix: str

    def resolve_password(self) -> str | None:
        if self.password_env is None:
            return None
        return os.environ.get(self.password_env)


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject malformed argv without echoing a private argument value."""

    def error(self, _message: str) -> None:
        raise LegacyDiscoveryInventoryError("command_line_invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="python -m jebao_flow.legacy_discovery_inventory_cli")
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_SafeArgumentParser,
    )

    capture = commands.add_parser("capture")
    _add_common_paths(capture)
    capture.add_argument(
        "--window-seconds",
        required=True,
        type=float,
        help="predeclared bounded MQTT observation window",
    )

    inspect = commands.add_parser("inspect")
    _add_common_paths(inspect)
    inspect.add_argument("--inventory-id", required=True, help="opaque inventory artifact id")

    select = commands.add_parser("select")
    _add_common_paths(select)
    select.add_argument("--inventory-id", required=True, help="opaque inventory artifact id")
    select.add_argument(
        "--request", required=True, help="owner-only exact digest selection request"
    )
    return parser


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="private current configuration")
    parser.add_argument("--output-root", required=True, help="existing owner-only artifact root")


async def _run(
    args: argparse.Namespace,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, object]:
    window_seconds = (
        _require_window_seconds(args.window_seconds) if args.command == "capture" else None
    )
    config, context = _load_context(args.config)
    store = LegacyDiscoveryArtifactStore(args.output_root)
    if args.command == "capture":
        assert window_seconds is not None
        observation = await _capture_observation(
            config,
            context,
            window_seconds=window_seconds,
            client_factory=client_factory or _mqtt_client_factory,
        )
        return store.write_inventory(observation).public_dict()
    if args.command == "inspect":
        return store.read_inventory(args.inventory_id, context=context).public_dict()
    if args.command == "select":
        inventory = store.read_inventory(args.inventory_id, context=context)
        request = load_selection_request(args.request)
        return store.write_selection(inventory, request).public_dict()
    raise LegacyDiscoveryInventoryError("command_line_invalid")


async def _capture_observation(
    mqtt: _InventoryMqttConfig,
    context: InventoryContext,
    *,
    window_seconds: float,
    client_factory: Callable[..., Any],
) -> InventoryObservation:
    window_seconds = _require_window_seconds(window_seconds)
    client_id = f"jebao-flow-legacy-inventory-{secrets.token_hex(16)}"
    password = mqtt.resolve_password()
    if password is not None and not isinstance(password, str):
        raise LegacyDiscoveryInventoryError("broker_credential_invalid")
    client_options = {
        "clean_session": True,
        "hostname": mqtt.host,
        "identifier": client_id,
        "logger": _PRIVATE_BROKER_LOGGER,
        "password": password,
        "port": mqtt.port,
        "protocol": aiomqtt.ProtocolVersion.V311,
        "username": mqtt.username,
    }
    try:
        async with client_factory(**client_options) as client:
            started_utc = _utc_now()
            window_milliseconds = max(1, round(window_seconds * 1000))
            builder = LegacyDiscoveryInventoryBuilder(
                context,
                utc_started=started_utc,
                window_milliseconds=window_milliseconds,
            )
            subscription_confirmed = False
            try:
                async with asyncio.timeout(window_seconds):
                    await client.subscribe(context.subscription_filter, qos=1)
                    subscription_confirmed = True
                    async for message in client.messages:
                        payload = message.payload
                        if isinstance(payload, bytearray | memoryview):
                            payload = bytes(payload)
                        builder.observe(
                            topic=str(message.topic),
                            payload=payload,
                            retain=message.retain,
                            qos=int(message.qos),
                        )
                    raise LegacyDiscoveryInventoryError("broker_message_stream_ended")
            except TimeoutError:
                if not subscription_confirmed:
                    raise LegacyDiscoveryInventoryError("broker_subscription_unconfirmed") from None
            return builder.finish(utc_ended=_utc_now())
    except (asyncio.CancelledError, LegacyDiscoveryInventoryError):
        raise
    except Exception:
        raise LegacyDiscoveryInventoryError("broker_observation_failed") from None


def _mqtt_client_factory(**options: object) -> aiomqtt.Client:
    """Create the isolated clean-session client; intentionally no will or persistent session."""

    return aiomqtt.Client(**options)  # type: ignore[arg-type]


def _load_context(path: str) -> tuple[_InventoryMqttConfig, InventoryContext]:
    try:
        config_path = Path(path)
        config_bytes = config_path.read_bytes()
        if len(config_bytes) > _MAX_CONFIG_BYTES:
            raise LegacyDiscoveryInventoryError("configuration_file_too_large")
        raw = yaml.safe_load(config_bytes)
        if not isinstance(raw, dict) or not isinstance(raw.get("mqtt"), dict):
            raise LegacyDiscoveryInventoryError("configuration_invalid")
        raw_mqtt = raw["mqtt"]
        allowed = {
            "discovery_prefix",
            "host",
            "password_env",
            "port",
            "topic_prefix",
            "username",
        }
        if set(raw_mqtt) - allowed:
            raise LegacyDiscoveryInventoryError("configuration_invalid")
        host = raw_mqtt.get("host")
        port = raw_mqtt.get("port", 1883)
        username = raw_mqtt.get("username")
        password_env = raw_mqtt.get("password_env")
        discovery_prefix = raw_mqtt.get("discovery_prefix", "homeassistant")
        topic_prefix = raw_mqtt.get("topic_prefix")
        if (
            not isinstance(host, str)
            or not host
            or type(port) is not int
            or not 1 <= port <= 65535
            or (username is not None and not isinstance(username, str))
            or (
                password_env is not None and (not isinstance(password_env, str) or not password_env)
            )
            or not isinstance(discovery_prefix, str)
            or not isinstance(topic_prefix, str)
        ):
            raise LegacyDiscoveryInventoryError("configuration_invalid")
        config = _InventoryMqttConfig(
            host=host,
            port=port,
            username=username,
            password_env=password_env,
            discovery_prefix=discovery_prefix,
            topic_prefix=topic_prefix,
        )
        context = InventoryContext.build(
            config_bytes=config_bytes,
            discovery_prefix=config.discovery_prefix,
            topic_prefix=config.topic_prefix,
        )
        return config, context
    except LegacyDiscoveryInventoryError:
        raise
    except Exception:
        raise LegacyDiscoveryInventoryError("configuration_invalid") from None


def _require_window_seconds(value: object) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not _MIN_WINDOW_SECONDS <= float(value) <= _MAX_WINDOW_SECONDS
    ):
        raise LegacyDiscoveryInventoryError("observation_window_invalid")
    return float(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        result = asyncio.run(_run(args))
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("legacy discovery inventory aborted: operator_interrupt", file=sys.stderr)
        return 130
    except LegacyDiscoveryInventoryError as error:
        print(f"legacy discovery inventory failed: {error.code}", file=sys.stderr)
        return 2
    except Exception:
        # Lower layers may include broker coordinates, credentials or private paths in messages.
        print("legacy discovery inventory failed: private_operation_error", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - source-module entry point
    raise SystemExit(main())
