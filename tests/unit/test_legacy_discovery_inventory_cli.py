from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import aiomqtt
import pytest

from jebao_flow import legacy_discovery_inventory_cli as inventory_cli
from jebao_flow.legacy_discovery_inventory import (
    InventoryContext,
    LegacyDiscoveryArtifactStore,
    LegacyDiscoveryInventoryBuilder,
    LegacyDiscoveryInventoryError,
    SelectionRequest,
    SelectionRequestEntry,
    selection_request_bytes,
)

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DISCOVERY_PREFIX = "private-discovery"
PRIVATE_TOPIC_PREFIX = "private-site/main"
PRIVATE_HOST = "private-broker-host"
PRIVATE_USER = "private-broker-user"
PRIVATE_PASSWORD = "private-broker-password"
UTC_START = "2026-09-09T00:00:00.000000Z"
UTC_END = "2026-09-09T00:00:01.000000Z"


class _Messages:
    def __init__(self, messages: tuple[object, ...], *, end_early: bool = False) -> None:
        self._items = messages
        self._end_early = end_early

    async def __aiter__(self):
        for message in self._items:
            yield message
        if not self._end_early:
            await asyncio.Event().wait()


class _FakeClient:
    def __init__(self, messages: tuple[object, ...], *, end_early: bool = False) -> None:
        self.messages = _Messages(messages, end_early=end_early)
        self.subscribed: list[tuple[str, int]] = []
        self.published: list[object] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def subscribe(self, topic: str, *, qos: int) -> None:
        self.subscribed.append((str(topic), qos))

    async def publish(self, *args, **kwargs) -> None:
        self.published.append((args, kwargs))
        raise AssertionError("write-free inventory must never publish")


class _ClientFactory:
    def __init__(self, messages: tuple[object, ...] = (), *, end_early: bool = False) -> None:
        self._messages = messages
        self._end_early = end_early
        self.options: list[dict[str, object]] = []
        self.clients: list[_FakeClient] = []

    def __call__(self, **options: object) -> _FakeClient:
        self.options.append(options)
        client = _FakeClient(self._messages, end_early=self._end_early)
        self.clients.append(client)
        return client


def _message(
    object_id: str,
    *,
    platform: str = "number",
    retain: bool = True,
    payload: bytes | None = None,
    topic: str | None = None,
):
    selected_payload = (
        json.dumps(
            {
                "state_topic": f"{PRIVATE_TOPIC_PREFIX}/local/state",
                "unique_id": object_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if payload is None
        else payload
    )
    return SimpleNamespace(
        payload=selected_payload,
        qos=1,
        retain=retain,
        topic=(topic or f"{PRIVATE_DISCOVERY_PREFIX}/{platform}/{object_id}/config"),
    )


def _mqtt() -> inventory_cli._InventoryMqttConfig:
    return inventory_cli._InventoryMqttConfig(
        host=PRIVATE_HOST,
        port=1883,
        username=PRIVATE_USER,
        password_env="PRIVATE_MQTT_PASSWORD",
        discovery_prefix=PRIVATE_DISCOVERY_PREFIX,
        topic_prefix=PRIVATE_TOPIC_PREFIX,
    )


def _context() -> InventoryContext:
    return InventoryContext.build(
        config_bytes=b"private config bytes",
        discovery_prefix=PRIVATE_DISCOVERY_PREFIX,
        topic_prefix=PRIVATE_TOPIC_PREFIX,
    )


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def test_private_config_loader_binds_exact_bytes_without_protocol_imports(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_bytes = (
        "instance:\n"
        "  id: main\n"
        "mqtt:\n"
        f"  host: {PRIVATE_HOST}\n"
        "  port: 1884\n"
        f"  username: {PRIVATE_USER}\n"
        "  password_env: PRIVATE_MQTT_PASSWORD\n"
        f"  discovery_prefix: {PRIVATE_DISCOVERY_PREFIX}\n"
        f"  topic_prefix: {PRIVATE_TOPIC_PREFIX}\n"
    ).encode("ascii")
    config_path.write_bytes(config_bytes)

    mqtt, context = inventory_cli._load_context(str(config_path))

    assert mqtt.host == PRIVATE_HOST
    assert mqtt.port == 1884
    assert mqtt.username == PRIVATE_USER
    assert mqtt.password_env == "PRIVATE_MQTT_PASSWORD"
    assert context.config_sha256 == hashlib.sha256(config_bytes).hexdigest()
    assert context.subscription_filter == f"{PRIVATE_DISCOVERY_PREFIX}/+/+/config"


@pytest.mark.asyncio
async def test_capture_has_no_publish_or_will_and_uses_fresh_clean_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRIVATE_MQTT_PASSWORD", PRIVATE_PASSWORD)
    factory = _ClientFactory((_message("jebao_flow_local_pump_manual_flow"),))

    first = await inventory_cli._capture_observation(
        _mqtt(),
        _context(),
        window_seconds=0.01,
        client_factory=factory,
    )
    second = await inventory_cli._capture_observation(
        _mqtt(),
        _context(),
        window_seconds=0.01,
        client_factory=factory,
    )

    assert len(first.records) == len(second.records) == 1
    assert [client.published for client in factory.clients] == [[], []]
    assert [client.subscribed for client in factory.clients] == [
        [(f"{PRIVATE_DISCOVERY_PREFIX}/+/+/config", 1)],
        [(f"{PRIVATE_DISCOVERY_PREFIX}/+/+/config", 1)],
    ]
    assert factory.options[0]["identifier"] != factory.options[1]["identifier"]
    for options in factory.options:
        assert options["clean_session"] is True
        assert options["protocol"] is aiomqtt.ProtocolVersion.V311
        assert options["hostname"] == PRIVATE_HOST
        assert options["username"] == PRIVATE_USER
        assert options["password"] == PRIVATE_PASSWORD
        assert options["logger"] is inventory_cli._PRIVATE_BROKER_LOGGER
        assert options["logger"].propagate is False
        assert "will" not in options
        assert "clean_start" not in options


@pytest.mark.asyncio
async def test_capture_filters_live_deeper_third_party_and_empty_before_store() -> None:
    eligible = _message("jebao_flow_local_pump_manual_flow")
    live = _message("jebao_flow_local_live", retain=False)
    deeper = _message(
        "jebao_flow_local_deeper",
        topic=(f"{PRIVATE_DISCOVERY_PREFIX}/number/node/jebao_flow_local_deeper/config"),
    )
    third_party = _message(
        "third_party",
        topic=f"{PRIVATE_DISCOVERY_PREFIX}/sensor/third_party/config",
    )
    empty = _message("jebao_flow_local_empty", payload=b"")
    factory = _ClientFactory((eligible, live, deeper, third_party, empty))

    observation = await inventory_cli._capture_observation(
        _mqtt(),
        _context(),
        window_seconds=0.01,
        client_factory=factory,
    )

    assert observation.message_count == 5
    assert observation.ignored_message_count == 4
    assert len(observation.records) == 1
    private_bytes = b"".join((record.topic + record.payload) for record in observation.records)
    assert b"third_party" not in private_bytes
    assert b"deeper" not in private_bytes
    assert b"live" not in private_bytes


@pytest.mark.asyncio
async def test_subscription_timeout_and_early_stream_end_are_not_inventory_successes() -> None:
    class NeverSubscribes(_FakeClient):
        async def subscribe(self, topic: str, *, qos: int) -> None:
            del topic, qos
            await asyncio.Event().wait()

    class NeverFactory:
        def __call__(self, **_options: object) -> NeverSubscribes:
            return NeverSubscribes(())

    with pytest.raises(
        LegacyDiscoveryInventoryError,
        match="broker_subscription_unconfirmed",
    ):
        await inventory_cli._capture_observation(
            _mqtt(),
            _context(),
            window_seconds=0.005,
            client_factory=NeverFactory(),
        )

    with pytest.raises(
        LegacyDiscoveryInventoryError,
        match="broker_message_stream_ended",
    ):
        await inventory_cli._capture_observation(
            _mqtt(),
            _context(),
            window_seconds=0.05,
            client_factory=_ClientFactory(end_early=True),
        )


@pytest.mark.asyncio
async def test_broker_exception_text_is_redacted_at_the_capture_boundary() -> None:
    class FailingClient(_FakeClient):
        async def __aenter__(self):
            raise RuntimeError(
                f"{PRIVATE_HOST} {PRIVATE_USER} {PRIVATE_PASSWORD} {PRIVATE_TOPIC_PREFIX}"
            )

    class FailingFactory:
        def __call__(self, **_options: object) -> FailingClient:
            return FailingClient(())

    with pytest.raises(LegacyDiscoveryInventoryError) as raised:
        await inventory_cli._capture_observation(
            _mqtt(),
            _context(),
            window_seconds=0.01,
            client_factory=FailingFactory(),
        )

    assert raised.value.code == "broker_observation_failed"
    assert all(
        private not in str(raised.value)
        for private in (PRIVATE_HOST, PRIVATE_USER, PRIVATE_PASSWORD, PRIVATE_TOPIC_PREFIX)
    )


@pytest.mark.asyncio
async def test_external_cancellation_propagates() -> None:
    task = asyncio.create_task(
        inventory_cli._capture_observation(
            _mqtt(),
            _context(),
            window_seconds=30,
            client_factory=_ClientFactory(),
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_cli_redacts_private_exception_argv_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_values = (
        PRIVATE_HOST,
        PRIVATE_USER,
        PRIVATE_PASSWORD,
        "/private/config.yaml",
        "/private/artifacts",
        PRIVATE_DISCOVERY_PREFIX,
        PRIVATE_TOPIC_PREFIX,
    )

    async def fail(_args, *, client_factory=None):
        del client_factory
        raise RuntimeError(" ".join(private_values))

    monkeypatch.setattr(inventory_cli, "_run", fail)
    result = inventory_cli.main(
        [
            "capture",
            "--config",
            "/private/config.yaml",
            "--output-root",
            "/private/artifacts",
            "--window-seconds",
            "1",
        ]
    )
    output = capsys.readouterr()

    assert result == 2
    assert output.out == ""
    assert output.err == "legacy discovery inventory failed: private_operation_error\n"
    assert all(value not in output.err for value in private_values)
    assert "Traceback" not in output.err

    result = inventory_cli.main(
        [
            "capture",
            "--config",
            "/private/config.yaml",
            "--output-root",
            "/private/artifacts",
            "--window-seconds",
            "/private/secret-window",
        ]
    )
    output = capsys.readouterr()
    assert result == 2
    assert output.err == "legacy discovery inventory failed: command_line_invalid\n"
    assert "/private" not in output.err


@pytest.mark.parametrize("value", ["nan", "inf", "0", "301"])
def test_cli_rejects_invalid_window_before_loading_private_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    loaded = False

    def load(_path: str):
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(inventory_cli, "_load_context", load)

    result = inventory_cli.main(
        [
            "capture",
            "--config",
            "/private/config.yaml",
            "--output-root",
            "/private/artifacts",
            "--window-seconds",
            value,
        ]
    )

    assert result == 2
    assert loaded is False
    assert capsys.readouterr().err == (
        "legacy discovery inventory failed: observation_window_invalid\n"
    )


def test_cli_success_prints_only_ascii_public_metadata(monkeypatch, capsys) -> None:
    private_values = (PRIVATE_HOST, PRIVATE_TOPIC_PREFIX, PRIVATE_DISCOVERY_PREFIX)

    async def succeed(_args, *, client_factory=None):
        del client_factory
        return {
            "artifact_id": "legacy-inventory-" + "a" * 32,
            "artifact_sha256": "b" * 64,
            "coverage": "bounded_window_observation_only",
            "record_count": 1,
        }

    monkeypatch.setattr(inventory_cli, "_run", succeed)

    result = inventory_cli.main(
        [
            "inspect",
            "--config",
            "/private/config.yaml",
            "--output-root",
            "/private/artifacts",
            "--inventory-id",
            "legacy-inventory-" + "a" * 32,
        ]
    )
    output = capsys.readouterr()

    assert result == 0
    assert output.err == ""
    assert output.out.isascii()
    assert all(value not in output.out for value in private_values)
    assert json.loads(output.out)["coverage"] == "bounded_window_observation_only"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), asyncio.CancelledError()])
def test_cli_interrupt_exits_130_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    interruption: BaseException,
) -> None:
    async def interrupted(_args, *, client_factory=None):
        del client_factory
        raise interruption

    monkeypatch.setattr(inventory_cli, "_run", interrupted)

    result = inventory_cli.main(
        [
            "inspect",
            "--config",
            "/private/config.yaml",
            "--output-root",
            "/private/artifacts",
            "--inventory-id",
            "legacy-inventory-" + "a" * 32,
        ]
    )

    assert result == 130
    assert capsys.readouterr().err == ("legacy discovery inventory aborted: operator_interrupt\n")


@pytest.mark.asyncio
async def test_offline_selection_mismatch_never_calls_network_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    builder = LegacyDiscoveryInventoryBuilder(
        context,
        utc_started=UTC_START,
        window_milliseconds=1000,
    )
    object_id = "jebao_flow_local_pump_manual_flow"
    builder.observe(
        topic=f"{PRIVATE_DISCOVERY_PREFIX}/number/{object_id}/config",
        payload=_message(object_id).payload,
        retain=True,
        qos=1,
    )
    root = _private_root(tmp_path)
    store = LegacyDiscoveryArtifactStore(root)
    inventory = store.write_inventory(builder.finish(utc_ended=UTC_END))
    record = inventory.records[0]
    request = SelectionRequest(
        inventory_artifact_id=inventory.artifact_id,
        inventory_sha256="f" * 64,
        bindings=context.binding_dict(),
        records=(
            SelectionRequestEntry(
                record_id=record.record_id,
                topic_sha256=record.topic_sha256,
                payload_sha256=record.payload_sha256,
            ),
        ),
    )
    request_path = tmp_path / "request.json"
    request_path.write_bytes(selection_request_bytes(request))
    request_path.chmod(0o600)
    config = _mqtt()
    monkeypatch.setattr(inventory_cli, "_load_context", lambda _path: (config, context))
    network_calls = 0

    def forbidden_factory(**_options):
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("offline selection touched a network factory")

    args = SimpleNamespace(
        command="select",
        config="/private/config.yaml",
        output_root=str(root),
        inventory_id=inventory.artifact_id,
        request=str(request_path),
    )
    with pytest.raises(
        LegacyDiscoveryInventoryError,
        match="selection_inventory_digest_mismatch",
    ):
        await inventory_cli._run(args, client_factory=forbidden_factory)

    assert network_calls == 0


def test_new_source_import_closure_excludes_frozen_write_and_protocol_modules() -> None:
    modules = (
        "jebao_flow.legacy_discovery_inventory",
        "jebao_flow.legacy_discovery_inventory_cli",
    )
    script = (
        "import importlib,json,sys;"
        f"[importlib.import_module(name) for name in {modules!r}];"
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name.startswith('jebao_flow.'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    closure = set(json.loads(completed.stdout))
    forbidden_exact = {
        "jebao_flow.devices.linkage",
        "jebao_flow.devices.schedule_flow_experiment",
        "jebao_flow.devices.schedule_linkage",
        "jebao_flow.devices.schedule_transaction",
        "jebao_flow.local_control.coordinator",
        "jebao_flow.local_control.device_writer",
        "jebao_flow.local_control.group_service",
        "jebao_flow.local_control.group_supervisor",
        "jebao_flow.local_control.mqtt",
        "jebao_flow.read_only_collector",
        "jebao_flow.schedule_flow_experiment_cli",
        "jebao_flow.schedule_linkage_cli",
    }
    assert closure.isdisjoint(forbidden_exact)
    assert not any(name.startswith("jebao_flow.protocol") for name in closure)
