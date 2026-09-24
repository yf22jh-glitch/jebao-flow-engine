"""The passive ingress recorder must remain observable when no commands arrive."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).parents[2] / "monitoring/local_mqtt_ingress_audit.py"


def _recorder(root: Path):
    with patch.dict(
        os.environ,
        {
            "JEBAO_AUDIT_ARTIFACT": "test",
            "JEBAO_AUDIT_UNTIL": "2026-09-30T00:00:00+00:00",
        },
    ):
        spec = importlib.util.spec_from_file_location("ingress_recorder_under_test", SOURCE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.ROOT = root
    return module


def test_command_topics_are_classified_without_publishing():
    with tempfile.TemporaryDirectory() as temporary:
        module = _recorder(Path(temporary))
        prefix = "tank/local/"
        assert module._kind("tank/local/devices/pump/functions/power/set", prefix) == "command_set"
        assert module._kind("tank/local/wavemakers/request", prefix) == "command_request"
        assert module._kind("tank/local/requests/id/result", prefix) == "command_result"
        assert module._kind("tank/local/devices/pump/functions/power/state", prefix) is None


def test_idle_heartbeat_is_fsynced_and_reports_connection_state():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        module = _recorder(root)
        health = {
            "connected": True,
            "message_count": 0,
            "last_message_at": None,
            "last_error_type": None,
        }
        module._write_heartbeat(health)
        [first] = root.glob("heartbeat-*.json")
        initial = json.loads(first.read_bytes())
        assert initial["connected"] is True
        assert initial["message_count"] == 0
        assert initial["last_message_at"] is None
        assert initial["mqtt_publish_count"] == initial["device_session_count"] == 0
        assert first.stat().st_mode & 0o777 == 0o600

        health["connected"] = False
        health["last_error_type"] = "MqttError"
        module._write_heartbeat(health)
        files = sorted(root.glob("heartbeat-*.json"))
        assert len(files) == 2
        assert json.loads(files[-1].read_bytes())["connected"] is False


def test_heartbeat_continues_during_command_idle_time():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        module = _recorder(root)
        module.HEARTBEAT_SECONDS = 0.01
        health = {"connected": True, "message_count": 0}

        async def run() -> None:
            stop = asyncio.Event()
            task = asyncio.create_task(module._heartbeat_loop(health, stop))
            await asyncio.sleep(0.04)
            stop.set()
            await task

        asyncio.run(run())
        files = list(root.glob("heartbeat-*.json"))
        assert len(files) >= 2
        assert not list(root.glob("*-message.json"))
