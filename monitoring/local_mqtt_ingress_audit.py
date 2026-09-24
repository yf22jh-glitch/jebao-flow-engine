"""Passive, durable capture of local-control MQTT inputs and outcomes.

This is deliberately separate from the state recorder: the latter watches what the
daemon published about devices, while this watches requests *to* the daemon and
their public results. It neither publishes to MQTT nor opens a device session.
Payloads can contain private identifiers and must remain on a private volume.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import aiomqtt

from jebao_flow.config import load_config


ROOT = Path("/data") / os.environ["JEBAO_AUDIT_ARTIFACT"]
DEADLINE = datetime.fromisoformat(os.environ["JEBAO_AUDIT_UNTIL"])
CONFIG = Path(os.environ.get("JEBAO_AUDIT_CONFIG", "/data/local-control/config.yaml"))


def _write_exclusive(name: str, record: dict[str, object]) -> None:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    descriptor = os.open(ROOT / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _kind(topic: str, local_prefix: str) -> str | None:
    if not topic.startswith(local_prefix):
        return None
    tail = topic[len(local_prefix) :]
    if tail.endswith("/set"):
        return "command_set"
    if tail.endswith("/request"):
        return "command_request"
    if tail.endswith("/preview"):
        return "preview_request"
    if tail.endswith("/result") and tail.startswith("requests/"):
        return "command_result"
    if tail in {"status", "groups/status"}:
        return "command_status"
    return None


async def _run(config, *, next_sequence: int) -> int:
    local_prefix = f"{config.mqtt.topic_prefix}/local/"
    while datetime.now(UTC) < DEADLINE:
        try:
            async with aiomqtt.Client(
                config.mqtt.host,
                config.mqtt.port,
                username=config.mqtt.username,
                password=config.mqtt.resolve_password(),
                identifier=f"local-ingress-audit-{secrets.token_hex(6)}",
            ) as client:
                await client.subscribe(f"{local_prefix}#", qos=1)
                async with asyncio.timeout(max(1, (DEADLINE - datetime.now(UTC)).total_seconds())):
                    async for message in client.messages:
                        topic = str(message.topic)
                        kind = _kind(topic, local_prefix)
                        if kind is None:
                            continue
                        payload = bytes(message.payload)
                        _write_exclusive(
                            f"{next_sequence:08d}-message.json",
                            {
                                "kind": kind,
                                "received_at": datetime.now(UTC).isoformat(),
                                "topic": topic,
                                "payload_base64": base64.b64encode(payload).decode("ascii"),
                                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                                "payload_bytes": len(payload),
                                "retain": bool(message.retain),
                                "dup": bool(getattr(message, "dup", False)),
                                "qos": int(message.qos),
                                "hardware_write": False,
                                "mqtt_publish_count": 0,
                                "device_session_count": 0,
                            },
                        )
                        next_sequence += 1
        except TimeoutError:
            break
        except Exception as error:
            _write_exclusive(
                f"connection-error-{secrets.token_hex(8)}.json",
                {
                    "kind": "connection_error",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "error_type": type(error).__name__,
                    "hardware_write": False,
                    "mqtt_publish_count": 0,
                    "device_session_count": 0,
                },
            )
            await asyncio.sleep(5)
    return next_sequence


async def main() -> None:
    if DEADLINE.tzinfo is None or DEADLINE.utcoffset() is None:
        raise ValueError("audit deadline must be timezone-aware")
    ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
    if (ROOT / "complete.json").exists():
        return
    config = load_config(CONFIG)
    plan = {
        "kind": "plan",
        "started_at": datetime.now(UTC).isoformat(),
        "deadline_at": DEADLINE.isoformat(),
        "subscription_pattern": f"{config.mqtt.topic_prefix}/local/#",
        "captured_kinds": [
            "command_set",
            "command_request",
            "preview_request",
            "command_result",
            "command_status",
        ],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "hardware_write": False,
        "mqtt_publish_count": 0,
        "device_session_count": 0,
    }
    if (ROOT / "plan.json").exists():
        prior = json.loads((ROOT / "plan.json").read_bytes())
        for key in ("deadline_at", "subscription_pattern", "script_sha256"):
            if prior[key] != plan[key]:
                raise RuntimeError(f"audit resume changed {key}")
    else:
        _write_exclusive("plan.json", plan)
    _write_exclusive(
        f"instance-{secrets.token_hex(8)}.json",
        {"kind": "instance", "started_at": datetime.now(UTC).isoformat()},
    )
    sequences = [
        int(path.name.split("-", 1)[0])
        for path in ROOT.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-message.json")
    ]
    next_sequence = await _run(config, next_sequence=max(sequences, default=0) + 1)
    _write_exclusive(
        "complete.json",
        {
            "kind": "complete",
            "completed_at": datetime.now(UTC).isoformat(),
            "message_count": next_sequence - 1,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
