"""Read-only Pro pair incident index for the existing private MQTT evidence volume.

The full-state recorder preserves published values; this small companion immediately fsyncs
the transitions that matter for the reported slave Feed and date anomalies.  Its evidence is
MQTT publication evidence, not a raw device frame or a measurement of motor rotation.  It
never publishes a command, opens a device connection, or changes the control daemon.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FUNCTIONS = (
    "power",
    "schedule_enable",
    "linkage_role",
    "manual_mode",
    "manual_flow",
    "manual_frequency",
    "current_schedule_state",
    "schedule",
    "fault_status",
)
REQUIRED = (
    "power",
    "schedule_enable",
    "linkage_role",
    "manual_mode",
    "current_schedule_state",
    "schedule",
)
MAX_AGE_SECONDS = 60
HEARTBEAT_SECONDS = 60


def _decode(payload: bytes) -> object:
    text = payload.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _observed(value: object, field: str) -> object | None:
    if not isinstance(value, dict):
        return None
    item = value.get("observed")
    return item.get(field) if isinstance(item, dict) else None


def _schedule_view(value: object) -> dict[str, object]:
    entries = _observed(value, "entries")
    return {
        "observed_at": value.get("observed_at") if isinstance(value, dict) else None,
        "device_local_time": _observed(value, "device_local_time"),
        "active_slot_count": _observed(value, "active_slot_count"),
        "invalid_slot_indexes": _observed(value, "invalid_slot_indexes"),
        "entries_sha256": (
            hashlib.sha256(
                json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if isinstance(entries, list)
            else None
        ),
    }


def _source_health(data_root: Path, at: datetime) -> dict[str, object]:
    """Check evidence files, not error-file activity, without reading private payloads."""

    sources = {
        "full_state": (
            os.environ.get("JEBAO_DIAGNOSTIC_STATE_ARTIFACT"),
            "[0-9]" * 6 + "-snapshot.json",
            360,
        ),
        "mqtt_ingress": (
            os.environ.get("JEBAO_DIAGNOSTIC_INGRESS_ARTIFACT"),
            "heartbeat-*.json",
            120,
        ),
    }
    result: dict[str, object] = {}
    for label, (artifact, pattern, maximum_age) in sources.items():
        if not artifact:
            continue
        if "/" in artifact or artifact in {".", ".."}:
            raise ValueError("source artifact must be one directory name")
        path = data_root / artifact
        if not path.is_dir():
            result[label] = {"present": False, "fresh": False}
            continue
        latest = max(path.glob(pattern), key=lambda item: item.name, default=None)
        if latest is None:
            result[label] = {"present": True, "fresh": False, "evidence_file": False}
            continue
        modified = datetime.fromtimestamp(latest.stat().st_mtime, UTC)
        age = max(0.0, (at - modified).total_seconds())
        complete = (path / "complete.json").exists()
        connected = None
        if label == "mqtt_ingress":
            try:
                connected = json.loads(latest.read_bytes()).get("connected") is True
            except (OSError, ValueError, AttributeError):
                # The O_EXCL heartbeat may have been observed before its fsync.
                connected = False
        result[label] = {
            "present": True,
            "evidence_file": True,
            "age_seconds": round(age, 3),
            "fresh": age <= maximum_age and not complete and connected is not False,
            "complete": complete,
            "connected": connected,
        }
    return result


@dataclass(frozen=True)
class Sample:
    value: object
    at: datetime
    sha256: str
    payload_base64: str
    retained: bool


class PairTimeline:
    """Classify published-state transitions without inferring physical output."""

    def __init__(self, prefix: str, master: str, slave: str) -> None:
        if not prefix or not master or not slave or master == slave:
            raise ValueError("one distinct Pro pair and MQTT prefix are required")
        self.prefix = prefix
        self.master = master
        self.slave = slave
        self.samples: dict[tuple[str, str], Sample] = {}
        self.conditions: dict[str, bool | None] = {
            "slave_manual_feed": None,
            "feed_outside_master_feeding": None,
            "reported_date_mismatch": None,
            "telemetry_fresh": None,
        }

    def topic_key(self, topic: str) -> tuple[str, str] | None:
        for member in (self.master, self.slave):
            base = f"{self.prefix}/local/devices/{member}/functions/"
            if not topic.startswith(base) or not topic.endswith("/state"):
                continue
            function = topic[len(base) : -len("/state")]
            if function in FUNCTIONS:
                return member, function
        return None

    def topics(self) -> tuple[str, ...]:
        return tuple(
            f"{self.prefix}/local/devices/{member}/functions/{function}/state"
            for member in (self.master, self.slave)
            for function in FUNCTIONS
        )

    def observe(
        self, topic: str, payload: bytes, *, at: datetime, retained: bool
    ) -> list[dict[str, Any]]:
        key = self.topic_key(topic)
        if key is None:
            return []
        self.samples[key] = Sample(
            _decode(payload),
            at,
            hashlib.sha256(payload).hexdigest(),
            base64.b64encode(payload).decode("ascii"),
            retained,
        )
        return self.evaluate(at, trigger=key)

    def _fresh(self, member: str, function: str, at: datetime) -> bool:
        sample = self.samples.get((member, function))
        if sample is None or sample.retained:
            return False
        if not 0 <= (at - sample.at).total_seconds() <= MAX_AGE_SECONDS:
            return False
        if isinstance(sample.value, dict) and "observed_at" in sample.value:
            observed_at = sample.value["observed_at"]
            if not isinstance(observed_at, str):
                return False
            try:
                source_at = datetime.fromisoformat(observed_at)
                return 0 <= (at - source_at).total_seconds() <= MAX_AGE_SECONDS
            except (ValueError, TypeError):
                return False
        return True

    def _value(self, member: str, function: str) -> object | None:
        sample = self.samples.get((member, function))
        return None if sample is None else sample.value

    def _clock(self, member: str) -> datetime | None:
        raw = _observed(self._value(member, "schedule"), "device_local_time")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def snapshot(self, at: datetime) -> dict[str, object]:
        result: dict[str, object] = {}
        for member in (self.master, self.slave):
            values: dict[str, object] = {}
            for function in FUNCTIONS:
                sample = self.samples.get((member, function))
                if sample is None:
                    continue
                if function == "schedule":
                    displayed: object = _schedule_view(sample.value)
                else:
                    displayed = sample.value
                values[function] = {
                    "received_at": sample.at.isoformat(),
                    "age_seconds": round((at - sample.at).total_seconds(), 3),
                    "fresh": self._fresh(member, function, at),
                    "payload_sha256": sample.sha256,
                    "value": displayed,
                }
            result[member] = values
        return result

    def evaluate(
        self, at: datetime, *, trigger: tuple[str, str] | None = None
    ) -> list[dict[str, Any]]:
        master, slave = self.master, self.slave
        fresh = all(
            self._fresh(member, function, at) for member in (master, slave) for function in REQUIRED
        )
        conditions: dict[str, bool | None] = {name: None for name in self.conditions}
        conditions["telemetry_fresh"] = fresh
        s_mode = self._value(slave, "manual_mode")
        if self._fresh(slave, "manual_mode", at) and isinstance(s_mode, str):
            conditions["slave_manual_feed"] = s_mode == "feed"
        if fresh:
            m_auto = _observed(self._value(master, "current_schedule_state"), "AutoMode")
            linked = (
                self._value(master, "linkage_role") == "master"
                and self._value(slave, "linkage_role") == "async_slave"
                and self._value(master, "power") == "ON"
                and self._value(slave, "power") == "ON"
                and self._value(master, "schedule_enable") == "ON"
                and self._value(slave, "schedule_enable") == "ON"
            )
            if isinstance(m_auto, str) and isinstance(s_mode, str):
                conditions["feed_outside_master_feeding"] = (
                    linked and s_mode == "feed" and m_auto != "feed"
                )
            m_clock, s_clock = self._clock(master), self._clock(slave)
            if m_clock is not None and s_clock is not None:
                conditions["reported_date_mismatch"] = m_clock.date() != s_clock.date()

        changes: list[dict[str, Any]] = []
        for name, current in conditions.items():
            previous = self.conditions[name]
            if current == previous:
                continue
            self.conditions[name] = current
            if previous is None and current is False:
                continue
            if name == "telemetry_fresh" and previous is None and current is True:
                continue
            trigger_sample = None if trigger is None else self.samples[trigger]
            changes.append(
                {
                    "kind": "condition_transition",
                    "condition": name,
                    "previous": previous,
                    "current": current,
                    "at": at.isoformat(),
                    "trigger": None if trigger is None else list(trigger),
                    "trigger_payload_sha256": (
                        None if trigger_sample is None else trigger_sample.sha256
                    ),
                    "trigger_payload_base64": (
                        None if trigger_sample is None else trigger_sample.payload_base64
                    ),
                    "snapshot": self.snapshot(at),
                    "evidence_scope": "mqtt_publication_not_raw_device_frame",
                    "hardware_write": False,
                }
            )
        return changes


def _write_exclusive(root: Path, name: str, payload: object) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _next_event_ordinal(root: Path) -> int:
    event_pattern = "[0-9]" * 8 + "-event.json"
    return max((int(path.name[:8]) for path in root.glob(event_pattern)), default=0) + 1


def _source_digest() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def _run(timeline: PairTimeline, root: Path, deadline: datetime, config: Any) -> None:
    import aiomqtt

    ordinal = _next_event_ordinal(root)

    def record(payload: dict[str, object]) -> None:
        nonlocal ordinal
        _write_exclusive(root, f"{ordinal:08d}-event.json", payload)
        ordinal += 1

    while datetime.now(UTC) < deadline:
        try:
            async with aiomqtt.Client(
                config.mqtt.host,
                config.mqtt.port,
                username=config.mqtt.username,
                password=config.mqtt.resolve_password(),
                identifier=f"pro-diagnostic-{secrets.token_hex(6)}",
            ) as client:
                for topic in timeline.topics():
                    await client.subscribe(topic, qos=1)

                async def receive() -> None:
                    async for message in client.messages:
                        at = datetime.now(UTC)
                        for change in timeline.observe(
                            str(message.topic),
                            bytes(message.payload),
                            at=at,
                            retained=bool(message.retain),
                        ):
                            record(change)

                reader = asyncio.create_task(receive())
                try:
                    last_heartbeat: datetime | None = None
                    while datetime.now(UTC) < deadline:
                        if reader.done():
                            await reader
                            raise RuntimeError("MQTT message stream ended")
                        at = datetime.now(UTC)
                        for change in timeline.evaluate(at):
                            record(change)
                        if (
                            last_heartbeat is None
                            or (at - last_heartbeat).total_seconds() >= HEARTBEAT_SECONDS
                        ):
                            record(
                                {
                                    "kind": "heartbeat",
                                    "at": at.isoformat(),
                                    "conditions": timeline.conditions.copy(),
                                    "snapshot": timeline.snapshot(at),
                                    "source_health": _source_health(root.parent, at),
                                    "hardware_write": False,
                                }
                            )
                            last_heartbeat = at
                        await asyncio.sleep(10)
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
        except Exception as error:
            record(
                {
                    "kind": "connection_error",
                    "at": datetime.now(UTC).isoformat(),
                    "error_type": type(error).__name__,
                    "hardware_write": False,
                }
            )
            await asyncio.sleep(5)


async def main() -> None:
    from jebao_flow.config import load_config

    deadline = datetime.fromisoformat(os.environ["JEBAO_DIAGNOSTIC_UNTIL"])
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise ValueError("diagnostic deadline must be timezone-aware")
    artifact = os.environ["JEBAO_DIAGNOSTIC_ARTIFACT"]
    if not artifact or "/" in artifact or artifact in {".", ".."}:
        raise ValueError("diagnostic artifact must be one directory name")
    root = Path("/data") / artifact
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    config = load_config(
        os.environ.get("JEBAO_DIAGNOSTIC_CONFIG", "/data/local-control/config.yaml")
    )
    timeline = PairTimeline(
        config.mqtt.topic_prefix,
        os.environ["JEBAO_DIAGNOSTIC_MASTER"],
        os.environ["JEBAO_DIAGNOSTIC_SLAVE"],
    )
    plan = root / "plan.json"
    script_digest = _source_digest()
    expected = {
        "kind": "plan",
        "artifact": artifact,
        "master": timeline.master,
        "slave": timeline.slave,
        "deadline": deadline.isoformat(),
        "script_sha256": script_digest,
        "subscriptions": list(timeline.topics()),
        "state_artifact": os.environ.get("JEBAO_DIAGNOSTIC_STATE_ARTIFACT"),
        "ingress_artifact": os.environ.get("JEBAO_DIAGNOSTIC_INGRESS_ARTIFACT"),
        "hardware_write": False,
        "mqtt_publish_count": 0,
        "device_session_count": 0,
    }
    if plan.exists():
        if json.loads(plan.read_bytes()) != expected:
            raise RuntimeError("diagnostic plan changed on resume")
    else:
        _write_exclusive(root, "plan.json", expected)
    if (root / "complete.json").exists():
        return
    _write_exclusive(
        root,
        f"instance-{secrets.token_hex(8)}.json",
        {"kind": "instance", "at": datetime.now(UTC).isoformat()},
    )
    await _run(timeline, root, deadline, config)
    _write_exclusive(
        root,
        "complete.json",
        {"kind": "complete", "at": datetime.now(UTC).isoformat()},
    )


if __name__ == "__main__":
    asyncio.run(main())
