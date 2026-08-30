from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import stat
import struct
import subprocess
import sys
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import read_only_collector
from jebao_flow.config import AppConfig
from jebao_flow.protocol.codec import GizwitsCommand, decode_frame, encode_frame, read_frame
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE
from jebao_flow.protocol.session import (
    STATE_REPLY_ACTION,
    STATE_REPORT_ACTION,
    RawStateCapture,
    ReadOnlyGizwitsSession,
)
from jebao_flow.read_only_collector import (
    ArtifactStoreError,
    CaptureContext,
    CollectorPreflightError,
    DurabilityUnconfirmedError,
    PilotSeriesStore,
    PilotTerminalError,
    RawCaptureStore,
    collect_pair,
    resolve_exact_endpoint,
    select_capture_pair,
)
from jebao_flow.source_attestation import SourceAttestationError

PRODUCT_KEY = "50dbc92221fd4d33ae69a1fedd43b555"
FIRST_DEVICE_ID = "private-first-device"
SECOND_DEVICE_ID = "private-second-device"
FIRST_MAC = "001122334455"
SECOND_MAC = "66778899aabb"
_FROZEN_IMPORT_PREFIXES = (
    "jebao_flow.devices",
    "jebao_flow.hardware_test",
    "jebao_flow.protocol.connection",
    "jebao_flow.protocol.control_session",
    "jebao_flow.recovery_supervisor",
    "jebao_flow.schedule_flow_experiment_cli",
    "jebao_flow.schedule_linkage_cli",
)
_TEST_SOURCE_ATTESTATION = SimpleNamespace(runtime_source_digest_sha256="f" * 64)


def _config(*, dry_run: bool = True, allow_writes: bool = False) -> AppConfig:
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "mqtt.local", "topic_prefix": "jebao-flow/test"},
            "runtime": {"dry_run": dry_run},
            "observer": {"targets": ["iot-broadcast.local"]},
            "devices": [
                {
                    "id": "first",
                    "name": "First",
                    "type": "wavemaker",
                    "product_key": PRODUCT_KEY,
                    "identity": {
                        "device_id": FIRST_DEVICE_ID,
                        "mac_address": FIRST_MAC,
                    },
                    "control": {"allow_hardware_writes": allow_writes},
                },
                {
                    "id": "second",
                    "name": "Second",
                    "type": "wavemaker",
                    "product_key": PRODUCT_KEY,
                    "identity": {
                        "device_id": SECOND_DEVICE_ID,
                        "mac_address": SECOND_MAC,
                    },
                    "control": {"allow_hardware_writes": False},
                },
            ],
        }
    )


def _discovered(
    *,
    first_address: str = "first.private",
    second_address: str = "second.private",
) -> list[DiscoveredDevice]:
    return [
        DiscoveredDevice(
            address=first_address,
            device_id=FIRST_DEVICE_ID,
            mac_address=FIRST_MAC,
            product_key=PRODUCT_KEY,
        ),
        DiscoveredDevice(
            address=second_address,
            device_id=SECOND_DEVICE_ID,
            mac_address=SECOND_MAC,
            product_key=PRODUCT_KEY,
        ),
    ]


class _UtcClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 28, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


class _MonotonicClock:
    def __init__(self) -> None:
        self.current = 1_000_000

    def __call__(self) -> int:
        value = self.current
        self.current += 100
        return value


class _Discovery:
    calls = 0

    def __init__(self, devices: list[DiscoveredDevice]) -> None:
        self.devices = devices

    async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
        assert timeout_seconds == 2
        type(self).calls += 1
        return self.devices


class _Session:
    instances: list[_Session] = []
    raw_by_address: dict[str, bytes | BaseException] = {}

    def __init__(self, address: str) -> None:
        self.address = address
        self.accept_reports: list[bool] = []
        self.connected = False
        self.disconnected = False
        self.quarantined = False
        type(self).instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def authenticate(self) -> bytes:
        return b"private-passcode"

    async def read_raw_state_capture(
        self,
        *,
        accept_reports: bool = True,
    ) -> RawStateCapture:
        self.accept_reports.append(accept_reports)
        result = self.raw_by_address[self.address]
        if isinstance(result, BaseException):
            raise result
        wire_frame = encode_frame(
            GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
            bytes([STATE_REPLY_ACTION]) + result,
        )
        return RawStateCapture(
            wire_frame=wire_frame,
            action=STATE_REPLY_ACTION,
            status_payload=result,
        )

    async def disconnect(self) -> None:
        self.disconnected = True

    def quarantine(self) -> None:
        self.quarantined = True


@pytest.fixture(autouse=True)
def _reset_fakes(monkeypatch) -> None:
    _Discovery.calls = 0
    _Session.instances = []
    raw_value = bytearray(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE)
    raw_value[443:451] = bytes((20, 26, 8, 28, 0, 12, 0, 0))
    raw = bytes(raw_value)
    _Session.raw_by_address = {"first.private": raw, "second.private": raw}

    def validate_source_attestation(attestation: object, *, expected_commit: str) -> object:
        assert expected_commit == "a" * 40
        if attestation is not _TEST_SOURCE_ATTESTATION:
            raise SourceAttestationError("collector_source_attestation_invalid")
        return attestation

    monkeypatch.setattr(
        read_only_collector,
        "validate_collector_source_attestation",
        validate_source_attestation,
    )


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "captures"
    root.mkdir(mode=0o700)
    return root


def _context(*, index: int = 0) -> CaptureContext:
    return CaptureContext(
        plan_artifact_id="JFP-test-plan",
        plan_sha256="c" * 64,
        epoch="pilot",
        sample_index=index,
    )


async def _completed_pilot(tmp_path: Path, *, pair_count: int = 1):
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=pair_count,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    metadata = await store.run(
        plan,
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )
    return store, plan, metadata


def test_capture_pair_requires_fully_locked_private_config() -> None:
    with pytest.raises(CollectorPreflightError, match="runtime_not_dry_run"):
        select_capture_pair(_config(dry_run=False), "first", "second")

    with pytest.raises(CollectorPreflightError, match="hardware_writes_not_fully_locked"):
        select_capture_pair(_config(allow_writes=True), "first", "second")


def test_collector_import_graph_does_not_load_device_or_frozen_modules() -> None:
    module_paths = (
        Path("src/jebao_flow/read_only_collector.py"),
        Path("src/jebao_flow/read_only_collector_cli.py"),
        Path("src/jebao_flow/physical_identity.py"),
    )
    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported
            for prefix in _FROZEN_IMPORT_PREFIXES
        )
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module == "jebao_flow.protocol.session"
            and any(alias.name == "GizwitsSession" for alias in node.names)
            for node in ast.walk(tree)
        )

    script = (
        "import sys; import jebao_flow.read_only_collector; "
        "import jebao_flow.read_only_collector_cli; "
        "print('\\n'.join(sorted(name for name in sys.modules "
        "if name.startswith('jebao_flow.devices') "
        "or name in {'jebao_flow.hardware_test', 'jebao_flow.protocol.connection', "
        "'jebao_flow.protocol.control_session', 'jebao_flow.recovery_supervisor', "
        "'jebao_flow.schedule_flow_experiment_cli', 'jebao_flow.schedule_linkage_cli'})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "\n"


def test_device_independent_identity_copy_matches_existing_contract() -> None:
    from jebao_flow import physical_identity as collector_identity
    from jebao_flow.devices import identity as existing_identity

    configuration = {"id": "logical", "limits": {"min": 30, "max": 75}}
    existing_config_hash = existing_identity.configuration_fingerprint(configuration)
    collector_config_hash = collector_identity.configuration_fingerprint(configuration)
    assert collector_config_hash == existing_config_hash
    values = {
        "vendor_device_id": "private-device",
        "mac_address": "00:11:22:33:44:55",
        "product_key": PRODUCT_KEY,
        "config_fingerprint": existing_config_hash,
    }
    existing = existing_identity.PhysicalDeviceBinding.from_identifiers(**values)
    copied = collector_identity.PhysicalDeviceBinding.from_identifiers(**values)
    assert copied.model_dump() == existing.model_dump()
    assert collector_identity.physical_identity_key(copied) == (
        existing_identity.physical_identity_key(existing)
    )


def test_capture_pair_requires_both_private_identity_fields() -> None:
    raw = _config().model_dump(mode="json")
    raw["devices"][0]["identity"]["mac_address"] = None
    config = AppConfig.model_validate(raw)

    with pytest.raises(CollectorPreflightError, match="capture_identity_incomplete"):
        select_capture_pair(config, "first", "second")


def test_resolver_rejects_partial_identity_and_endpoint_collisions() -> None:
    target = select_capture_pair(_config(), "first", "second")[0]
    conflicted = _discovered() + [
        DiscoveredDevice(
            address="third.private",
            device_id=FIRST_DEVICE_ID,
            mac_address="ffeeddccbbaa",
            product_key=PRODUCT_KEY,
        )
    ]
    with pytest.raises(Exception, match="identity_not_exactly_resolved"):
        resolve_exact_endpoint(target, conflicted)

    endpoint_collision = _discovered() + [
        DiscoveredDevice(
            address="first.private",
            device_id="another-private-device",
            mac_address="123456789abc",
            product_key=PRODUCT_KEY,
        )
    ]
    with pytest.raises(Exception, match="identity_endpoint_ambiguous"):
        resolve_exact_endpoint(target, endpoint_collision)


async def test_pair_uses_fresh_discovery_and_session_with_explicit_reply_only() -> None:
    targets = select_capture_pair(_config(), "first", "second")
    utc_clock = _UtcClock()
    monotonic_clock = _MonotonicClock()

    capture = await collect_pair(
        targets,
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
        utc_clock=utc_clock,
        monotonic_clock=monotonic_clock,
    )

    assert capture.status == "acquisition_valid"
    assert _Discovery.calls == 4
    assert len(_Session.instances) == 2
    assert all(session.accept_reports == [False] for session in _Session.instances)
    assert all(session.connected and session.disconnected for session in _Session.instances)
    action, status = (
        capture.samples[0].raw_wire_frame,
        _Session.raw_by_address["first.private"],
    )
    assert action == encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes([STATE_REPLY_ACTION]) + status,
    )
    assert capture.pair_completion_gap_ns == (
        capture.samples[1].read_completed.monotonic_ns
        - capture.samples[0].read_completed.monotonic_ns
    )


async def test_real_transport_pilot_series_sends_no_control_frame(tmp_path: Path) -> None:
    received: list[tuple[int, bytes]] = []
    completed_connections = 0
    both_completed = asyncio.Event()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal completed_connections
        try:
            request = await read_frame(reader)
            received.append((request.command, request.payload))
            passcode = b"private"
            writer.write(
                encode_frame(
                    GizwitsCommand.PASSCODE_RESPONSE,
                    struct.pack(">H", len(passcode)) + passcode,
                )
            )
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
            await writer.drain()

            request = await read_frame(reader)
            received.append((request.command, request.payload))
            writer.write(
                encode_frame(
                    GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                    bytes([STATE_REPORT_ACTION])
                    + bytes([1]) * LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
                )
            )
            writer.write(
                encode_frame(
                    GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                    bytes([STATE_REPLY_ACTION])
                    + _Session.raw_by_address["first.private"],
                )
            )
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            completed_connections += 1
            if completed_connections == 4:
                both_completed.set()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        targets = select_capture_pair(_config(), "first", "second")
        store = PilotSeriesStore(_private_root(tmp_path))
        plan = store.prepare(
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            planned_pair_count=2,
            requested_cadence_seconds=0.000001,
            collector_commit_sha="a" * 40,
        )
        metadata = await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=lambda _address: ReadOnlyGizwitsSession(
                "127.0.0.1", port=port
            ),
            discovery_timeout_seconds=2,
        )
        await asyncio.wait_for(both_completed.wait(), timeout=1)
    finally:
        server.close()
        await server.wait_closed()

    assert metadata.status == "pilot_completed_all_acquisitions_accepted"
    assert [command for command, _payload in received] == [
        GizwitsCommand.PASSCODE_REQUEST,
        GizwitsCommand.LOGIN_REQUEST,
        GizwitsCommand.SERIAL_TRANSMIT_REQUEST,
    ] * 4
    assert all(
        command != GizwitsCommand.SERIAL_CONTROL_REQUEST for command, _payload in received
    )


async def test_identity_failure_never_connects_to_the_unverified_endpoint() -> None:
    targets = select_capture_pair(_config(), "first", "second")
    discovered = _discovered()
    discovered[0] = discovered[0].model_copy(update={"mac_address": "ffeeddccbbaa"})

    capture = await collect_pair(
        targets,
        discovery_factory=lambda: _Discovery(discovered),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    assert capture.status == "acquisition_invalid"
    assert capture.samples[0].failure_code == "identity_not_exactly_resolved"
    assert [session.address for session in _Session.instances] == ["second.private"]


async def test_invalid_status_keeps_the_explicit_raw_payload_for_offline_review() -> None:
    targets = select_capture_pair(_config(), "first", "second")
    _Session.raw_by_address["second.private"] = b"invalid"

    capture = await collect_pair(
        targets,
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    assert capture.status == "acquisition_invalid"
    assert capture.samples[1].failure_code == "invalid_status_payload"
    assert capture.samples[1].raw_wire_frame == encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes([STATE_REPLY_ACTION]) + b"invalid",
    )


async def test_capture_rejects_invalid_auto_range_as_acquisition_failure() -> None:
    raw = bytearray(_Session.raw_by_address["second.private"])
    raw[7] = 0xEE
    _Session.raw_by_address["second.private"] = bytes(raw)

    capture = await collect_pair(
        select_capture_pair(_config(), "first", "second"),
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    assert capture.samples[1].status == "acquisition_invalid"
    assert capture.samples[1].failure_code == "status_numeric_range_invalid"
    assert capture.samples[1].raw_wire_frame is not None


async def test_capture_records_invalid_schedule_as_nonfatal_state_observation() -> None:
    raw = bytearray(_Session.raw_by_address["second.private"])
    raw[0] |= 0b10  # TimerON
    raw[11:20] = bytes((8, 0, 9, 0, 2, 101, 0, 0, 0))
    _Session.raw_by_address["second.private"] = bytes(raw)

    capture = await collect_pair(
        select_capture_pair(_config(), "first", "second"),
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    sample = capture.samples[1]
    assert sample.status == "acquisition_valid"
    assert sample.failure_code is None
    assert sample.state_observation is not None
    assert sample.state_observation["passed"] is False
    assert sample.state_observation["checks"]["schedule_parameter_ranges_valid"] is False


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (
            lambda raw: raw.__setitem__(slice(443, 451), bytes(8)),
            "device_local_time_present",
        ),
        (lambda raw: raw.__setitem__(451, 1), "active_faults_empty"),
    ],
)
async def test_device_clock_or_fault_is_observed_without_discarding_acquisition(
    mutate,
    failed_check: str,
) -> None:
    raw = bytearray(_Session.raw_by_address["second.private"])
    mutate(raw)
    _Session.raw_by_address["second.private"] = bytes(raw)

    capture = await collect_pair(
        select_capture_pair(_config(), "first", "second"),
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    sample = capture.samples[1]
    assert sample.status == "acquisition_valid"
    assert sample.state_summary is not None
    assert sample.state_observation is not None
    assert sample.state_observation["passed"] is False
    assert sample.state_observation["checks"][failed_check] is False


async def _valid_capture():
    return await collect_pair(
        select_capture_pair(_config(), "first", "second"),
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
        utc_clock=_UtcClock(),
        monotonic_clock=_MonotonicClock(),
    )


async def test_store_commits_private_raw_reply_and_secret_free_manifest(tmp_path: Path) -> None:
    capture = await _valid_capture()
    root = _private_root(tmp_path)
    store = RawCaptureStore(root)

    metadata = store.commit(capture, context=_context(), artifact_id="JFC-test-bundle")
    bundle = root / metadata.artifact_id
    manifest_text = (bundle / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert metadata.status == "acquisition_valid"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    assert stat.S_IMODE((bundle / "manifest.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((bundle / "a.reply.frame.bin").stat().st_mode) == 0o600
    frame = (bundle / "a.reply.frame.bin").read_bytes()
    decoded = decode_frame(frame)
    assert decoded.payload[0] == STATE_REPLY_ACTION
    assert len(decoded.payload[1:]) == LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE
    assert manifest["samples"][0]["evidence"] == {
        "identity_and_host_timing": {"available": True, "grade": "b"},
        "raw_wire_frame": {"available": True, "grade": "a"},
        "state_summary": {"available": True, "grade": "b"},
    }
    for private_value in (
        FIRST_DEVICE_ID,
        SECOND_DEVICE_ID,
        FIRST_MAC,
        SECOND_MAC,
        "first.private",
        "second.private",
        "private-passcode",
        str(root),
    ):
        assert private_value not in manifest_text
    assert store.verify(metadata.artifact_id)["status"] == "acquisition_valid"


async def test_store_rejects_tampered_raw_payload(tmp_path: Path) -> None:
    store = RawCaptureStore(_private_root(tmp_path))
    metadata = store.commit(
        await _valid_capture(), context=_context(), artifact_id="JFC-tamper"
    )
    raw_path = store.root / metadata.artifact_id / "a.reply.frame.bin"
    raw_path.write_bytes(b"tampered")
    raw_path.chmod(0o600)

    with pytest.raises(ArtifactStoreError, match="artifact_raw_digest_mismatch"):
        store.verify(metadata.artifact_id)


async def test_store_rejects_valid_manifest_claim_without_raw(tmp_path: Path) -> None:
    store = RawCaptureStore(_private_root(tmp_path))
    metadata = store.commit(
        await _valid_capture(), context=_context(), artifact_id="JFC-missing-raw"
    )
    manifest_path = store.root / metadata.artifact_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["samples"][0]["raw"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    with pytest.raises(ArtifactStoreError, match="artifact_commit_marker_mismatch"):
        store.verify(metadata.artifact_id)


async def test_store_rejects_summary_not_derived_from_preserved_raw(tmp_path: Path) -> None:
    store = RawCaptureStore(_private_root(tmp_path))
    metadata = store.commit(
        await _valid_capture(), context=_context(), artifact_id="JFC-summary"
    )
    manifest_path = store.root / metadata.artifact_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["samples"][0]["state_summary"]["fields"]["AutoFlow"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    with pytest.raises(ArtifactStoreError, match="artifact_commit_marker_mismatch"):
        store.verify(metadata.artifact_id)


async def test_parent_fsync_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jebao_flow import read_only_collector

    store = RawCaptureStore(_private_root(tmp_path))
    original = read_only_collector._fsync_directory

    def fail_parent(path: Path) -> None:
        if path == store.root:
            raise OSError("simulated parent fsync failure")
        original(path)

    monkeypatch.setattr(read_only_collector, "_fsync_directory", fail_parent)

    with pytest.raises(DurabilityUnconfirmedError, match="artifact_parent_fsync_unconfirmed"):
        store.commit(
            await _valid_capture(), context=_context(), artifact_id="JFC-uncertain"
        )
    assert (store.root / "JFC-uncertain").is_dir()
    with pytest.raises(ArtifactStoreError, match="artifact_commit_marker_invalid"):
        store.verify("JFC-uncertain")


async def test_cancellation_quarantines_and_disconnects_fresh_session() -> None:
    targets = select_capture_pair(_config(), "first", "second")
    started = asyncio.Event()

    class CancelledSession(_Session):
        async def read_raw_state_capture(
            self,
            *,
            accept_reports: bool = True,
        ) -> RawStateCapture:
            self.accept_reports.append(accept_reports)
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    task = asyncio.create_task(
        collect_pair(
            targets,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=CancelledSession,
            discovery_timeout_seconds=2,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert CancelledSession.instances[0].quarantined is True
    assert CancelledSession.instances[0].disconnected is True


async def test_post_read_endpoint_change_rejects_but_preserves_raw() -> None:
    targets = select_capture_pair(_config(), "first", "second")
    responses = [
        _discovered(),
        _discovered(first_address="rebound.private"),
        _discovered(),
        _discovered(),
    ]

    capture = await collect_pair(
        targets,
        discovery_factory=lambda: _Discovery(responses.pop(0)),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    assert capture.status == "acquisition_invalid"
    first = capture.samples[0]
    assert first.failure_code == "identity_endpoint_changed_during_read"
    assert first.raw_wire_frame is not None
    assert first.observed_endpoint_token_before != first.observed_endpoint_token_after


async def test_cancellation_during_disconnect_is_never_returned_as_valid() -> None:
    target = select_capture_pair(_config(), "first", "second")[0]
    disconnect_started = asyncio.Event()

    class DisconnectCancelledSession(_Session):
        async def disconnect(self) -> None:
            disconnect_started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        collect_pair(
            (target, select_capture_pair(_config(), "first", "second")[1]),
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=DisconnectCancelledSession,
            discovery_timeout_seconds=2,
        )
    )
    await disconnect_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert DisconnectCancelledSession.instances[0].quarantined is True


def test_store_requires_preexisting_owner_only_root(tmp_path: Path) -> None:
    with pytest.raises(ArtifactStoreError, match="artifact_root_missing"):
        RawCaptureStore(tmp_path / "missing")


def test_pilot_store_rejects_non_pro_target_before_creating_plan(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    wrong = (replace(targets[0], product_key="not-the-pro-product"), targets[1])

    with pytest.raises(CollectorPreflightError, match="pilot_target_not_local_wavemaker_pro"):
        store.prepare(
            wrong,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            planned_pair_count=1,
            requested_cadence_seconds=1,
            collector_commit_sha="a" * 40,
        )

    assert list(root.iterdir()) == []


def test_pilot_prepare_requires_issued_source_attestation_before_artifact_write(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)

    with pytest.raises(
        CollectorPreflightError,
        match="collector_source_attestation_invalid",
    ):
        store.prepare(
            select_capture_pair(_config(), "first", "second"),
            source_attestation=object(),  # type: ignore[arg-type]
            planned_pair_count=1,
            requested_cadence_seconds=1,
            collector_commit_sha="a" * 40,
        )

    assert list(root.iterdir()) == []


async def test_pilot_series_precommits_plan_and_preserves_every_ordinal(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    utc_clock = _UtcClock()
    monotonic_clock = _MonotonicClock()
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=2,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
        utc_clock=utc_clock,
    )
    plan_document = json.loads((plan.series_directory / "plan.json").read_text())
    assert plan_document["schema_version"] == 2
    assert plan_document["collector_runtime_source_digest_sha256"] == "f" * 64

    metadata = await store.run(
        plan,
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
        utc_clock=utc_clock,
        monotonic_clock=monotonic_clock,
    )

    assert metadata.status == "pilot_completed_all_acquisitions_accepted"
    assert metadata.completed_pair_count == 2
    assert metadata.accepted_pair_count == 2
    assert _Discovery.calls == 8
    attempts = plan.series_directory / "attempts"
    assert {entry.name for entry in attempts.iterdir()} == {"000000", "000001"}
    assert all(
        (attempts / ordinal / role / "raw.frame").is_file()
        for ordinal in ("000000", "000001")
        for role in ("a", "b")
    )
    verified = store.verify_completed_series(
        plan,
        expected_series_sha256=metadata.series_sha256,
    )
    assert verified["q2_boundary_classification"] == "not_authorized"
    assert store.load(plan.series_id) == plan
    public_json = "\n".join(
        path.read_text(encoding="utf-8")
        for path in plan.series_directory.rglob("*.json")
    )
    for private_value in (
        FIRST_DEVICE_ID,
        SECOND_DEVICE_ID,
        FIRST_MAC,
        SECOND_MAC,
        "first.private",
        "second.private",
        "private-passcode",
        str(root),
    ):
        assert private_value not in public_json
    calls_before_reentry = _Discovery.calls
    with pytest.raises(CollectorPreflightError, match="pilot_series_already_started"):
        await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )
    assert _Discovery.calls == calls_before_reentry


async def test_extract_verified_accepted_pair_returns_immutable_safe_raw_evidence(
    tmp_path: Path,
) -> None:
    store, plan, metadata = await _completed_pilot(tmp_path, pair_count=2)

    artifact = store.extract_verified_accepted_pair(
        plan,
        expected_series_sha256=metadata.series_sha256,
        ordinal=1,
    )

    pair_path = plan.series_directory / "attempts/000001/pair.json"
    assert artifact.plan_artifact_id == plan.plan_artifact_id
    assert artifact.plan_sha256 == plan.plan_sha256
    assert artifact.series_id == plan.series_id
    assert artifact.series_sha256 == metadata.series_sha256
    assert artifact.ordinal == 1
    assert artifact.pair_manifest_sha256 == hashlib.sha256(pair_path.read_bytes()).hexdigest()
    assert artifact.pair_completion_gap_ns >= 0
    assert tuple(sample.role for sample in artifact.samples) == ("a", "b")
    assert tuple(sample.identity_binding_sha256 for sample in artifact.samples) == tuple(
        target.identity_binding_sha256
        for target in select_capture_pair(_config(), "first", "second")
    )
    for role, sample in zip(("a", "b"), artifact.samples, strict=True):
        sample_directory = plan.series_directory / f"attempts/000001/{role}"
        assert sample.raw_wire_frame == (sample_directory / "raw.frame").read_bytes()
        assert (
            sample.sample_manifest_sha256
            == hashlib.sha256((sample_directory / "sample.json").read_bytes()).hexdigest()
        )
        assert sample.raw_wire_frame_sha256 == hashlib.sha256(sample.raw_wire_frame).hexdigest()
        assert sample.attempt.started_monotonic_ns <= sample.read.started_monotonic_ns
        assert sample.read.completed_monotonic_ns <= sample.identity_after.started_monotonic_ns
    public_repr = repr(artifact)
    assert "raw_wire_frame=" not in public_repr
    public_serialization = repr(asdict(artifact))
    for forbidden_field in (
        "series_directory",
        "vendor_device_id",
        "mac_address",
        "address",
    ):
        assert forbidden_field not in public_serialization
    for private_value in (
        FIRST_DEVICE_ID,
        SECOND_DEVICE_ID,
        FIRST_MAC,
        SECOND_MAC,
        "first.private",
        "second.private",
        str(plan.series_directory),
    ):
        assert private_value not in public_repr
        assert private_value not in public_serialization
    with pytest.raises(FrozenInstanceError):
        artifact.ordinal = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        artifact.samples[0].role = "b"  # type: ignore[misc]


async def test_extract_verified_pair_rejects_nonaccepted_and_invalid_ordinals(
    tmp_path: Path,
) -> None:
    _Session.raw_by_address["second.private"] = TimeoutError("private endpoint")
    store, plan, metadata = await _completed_pilot(tmp_path)

    with pytest.raises(ArtifactStoreError, match="pilot_artifact_pair_not_accepted"):
        store.extract_verified_accepted_pair(
            plan,
            expected_series_sha256=metadata.series_sha256,
            ordinal=0,
        )
    for invalid_ordinal in (-1, 1, True):
        with pytest.raises(ArtifactStoreError, match="pilot_artifact_ordinal_invalid"):
            store.extract_verified_accepted_pair(
                plan,
                expected_series_sha256=metadata.series_sha256,
                ordinal=invalid_ordinal,
            )


@pytest.mark.parametrize("target", ["pair", "sample", "raw", "missing_raw"])
async def test_extract_verified_pair_reverifies_selected_attempt_after_full_series(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    store, plan, metadata = await _completed_pilot(tmp_path)
    original_verify = store.verify_completed_series

    def verify_then_mutate(reference, *, expected_series_sha256: str):
        verified = original_verify(
            reference,
            expected_series_sha256=expected_series_sha256,
        )
        attempt = plan.series_directory / "attempts/000000"
        if target == "pair":
            (attempt / "pair.json").write_bytes(b"{}")
        elif target == "sample":
            (attempt / "a/sample.json").write_bytes(b"{}")
        elif target == "raw":
            (attempt / "a/raw.frame").write_bytes(b"tampered")
        else:
            (attempt / "a/raw.frame").unlink()
        return verified

    monkeypatch.setattr(store, "verify_completed_series", verify_then_mutate)

    with pytest.raises(ArtifactStoreError, match="pilot_"):
        store.extract_verified_accepted_pair(
            plan,
            expected_series_sha256=metadata.series_sha256,
            ordinal=0,
        )


async def test_pilot_run_revalidates_source_attestation_before_network(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=1,
        collector_commit_sha="a" * 40,
    )

    with pytest.raises(
        CollectorPreflightError,
        match="collector_source_attestation_invalid",
    ):
        await store.run(
            plan,
            targets,
            source_attestation=object(),  # type: ignore[arg-type]
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )

    assert _Discovery.calls == 0
    assert not (plan.series_directory / "started.json").exists()


async def test_pilot_series_persists_read_failure_without_retry(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    _Session.raw_by_address["second.private"] = TimeoutError("private endpoint")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )

    metadata = await store.run(
        plan,
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        discovery_factory=lambda: _Discovery(_discovered()),
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )

    assert metadata.status == "pilot_completed_with_rejected_or_failed_acquisitions"
    assert metadata.read_failure_pair_count == 1
    sample = json.loads(
        (plan.series_directory / "attempts/000000/b/sample.json").read_text()
    )
    assert sample["outcome"] == "read_failure"
    assert sample["failure_phase"] == "read"
    assert sample["raw"] is None
    assert len(_Session.instances) == 2


async def test_pilot_series_keeps_first_device_if_second_is_cancelled(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    second_read_started = asyncio.Event()

    class BlockingSecondSession(_Session):
        async def read_raw_state_capture(
            self,
            *,
            accept_reports: bool = True,
        ) -> RawStateCapture:
            if self.address == "second.private":
                self.accept_reports.append(accept_reports)
                second_read_started.set()
                await asyncio.Event().wait()
            return await super().read_raw_state_capture(accept_reports=accept_reports)

    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    task = asyncio.create_task(
        store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=BlockingSecondSession,
            discovery_timeout_seconds=2,
        )
    )
    await second_read_started.wait()
    task.cancel()
    with pytest.raises(PilotTerminalError) as raised:
        await task
    assert raised.value.code == "capture_cancelled"
    assert raised.value.abort_sha256 is not None
    assert raised.value.durability_unknown is False

    attempt = plan.series_directory / "attempts/000000"
    assert (attempt / "a/sample.json").is_file()
    assert not (attempt / "b").exists()
    assert not (plan.series_directory / "series.json").exists()
    marker = json.loads((plan.series_directory / "aborted.commit.json").read_text())
    aborted = store.verify_partial_series(
        plan,
        expected_aborted_sha256=marker["aborted_sha256"],
    )
    assert aborted["status"] == "pilot_aborted_prefix_only_not_q2_boundary"
    assert aborted["completed_ordinals"] == []
    assert aborted["trailing_attempt"] == {
        "ordinal": 0,
        "committed_roles": ["a"],
        "pair_record_present": False,
    }


async def test_pilot_persists_raw_when_cancelled_during_post_read_identity_check(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    post_read_started = asyncio.Event()

    class BlockingSecondDiscovery(_Discovery):
        calls = 0

        async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
            assert timeout_seconds == 2
            type(self).calls += 1
            if type(self).calls == 2:
                post_read_started.set()
                await asyncio.Event().wait()
            return self.devices

    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    task = asyncio.create_task(
        store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: BlockingSecondDiscovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )
    )
    await post_read_started.wait()
    task.cancel()
    with pytest.raises(PilotTerminalError) as raised:
        await task
    assert raised.value.code == "capture_cancelled_after_read"
    assert raised.value.abort_sha256 is not None

    sample_path = plan.series_directory / "attempts/000000/a/sample.json"
    sample = json.loads(sample_path.read_text())
    assert sample["outcome"] == "predicate_rejected"
    assert sample["failure_code"] == "capture_cancelled_after_read"
    assert (sample_path.parent / "raw.frame").is_file()
    marker = json.loads((plan.series_directory / "aborted.commit.json").read_text())
    store.verify_partial_series(plan, expected_aborted_sha256=marker["aborted_sha256"])


async def test_pilot_never_marks_disconnect_cancellation_as_accepted(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    disconnect_started = asyncio.Event()

    class BlockingDisconnectSession(_Session):
        async def disconnect(self) -> None:
            disconnect_started.set()
            await asyncio.Event().wait()

    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    task = asyncio.create_task(
        store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=BlockingDisconnectSession,
            discovery_timeout_seconds=2,
        )
    )
    await disconnect_started.wait()
    task.cancel()
    with pytest.raises(PilotTerminalError) as raised:
        await task
    assert raised.value.code == "capture_cancelled_after_read"
    assert raised.value.abort_sha256 is not None

    sample_path = plan.series_directory / "attempts/000000/a/sample.json"
    sample = json.loads(sample_path.read_text())
    assert sample["outcome"] == "predicate_rejected"
    assert sample["failure_phase"] == "disconnect"
    assert (sample_path.parent / "raw.frame").is_file()


async def test_pilot_pair_fsync_failure_preserves_pair_as_verified_abort_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    original_fsync = read_only_collector._fsync_directory
    failed = False

    def fail_pair_directory_once(path: Path) -> None:
        nonlocal failed
        if not failed and path.name == "000000" and (path / "pair.json").exists():
            failed = True
            raise OSError("private pair fsync detail")
        original_fsync(path)

    monkeypatch.setattr(
        read_only_collector,
        "_fsync_directory",
        fail_pair_directory_once,
    )
    with pytest.raises(PilotTerminalError) as raised:
        await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )

    terminal = raised.value
    assert terminal.code == "pilot_pair_durability_unconfirmed"
    assert terminal.plan_artifact_id == plan.plan_artifact_id
    assert terminal.series_id == plan.series_id
    assert terminal.plan_sha256 == plan.plan_sha256
    assert terminal.abort_sha256 is not None
    assert terminal.durability_unknown is True
    assert terminal.__cause__ is None
    aborted = store.verify_partial_series(
        plan,
        expected_aborted_sha256=terminal.abort_sha256,
    )
    assert aborted["durability_unknown"] is True
    assert aborted["trailing_attempt"] == {
        "ordinal": 0,
        "committed_roles": ["a", "b"],
        "pair_record_present": True,
    }
    residue = {entry["relative_leaf"]: entry for entry in aborted["residue_inventory"]}
    assert "attempts/000000/pair.json" in residue
    assert residue["attempts/000000/pair.json"]["entry_type"] == "regular_file"
    assert len(residue["attempts/000000/pair.json"]["sha256"]) == 64
    with pytest.raises(ArtifactStoreError, match="pilot_terminal_state_ambiguous"):
        store.verify_completed_series(plan, expected_series_sha256="b" * 64)


async def test_pilot_sample_fsync_failure_preserves_tmp_raw_in_abort_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    original_fsync = read_only_collector._fsync_directory
    failed = False

    def fail_sample_directory_once(path: Path) -> None:
        nonlocal failed
        if (
            not failed
            and path.name.startswith(".a.tmp-")
            and (path / "raw.frame").exists()
        ):
            failed = True
            (path / "residue-link").symlink_to("not-followed")
            raise OSError("private sample fsync detail")
        original_fsync(path)

    monkeypatch.setattr(
        read_only_collector,
        "_fsync_directory",
        fail_sample_directory_once,
    )
    with pytest.raises(PilotTerminalError) as raised:
        await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )

    terminal = raised.value
    assert terminal.code == "pilot_sample_durability_unconfirmed"
    assert terminal.abort_sha256 is not None
    assert terminal.durability_unknown is True
    aborted = store.verify_partial_series(
        plan,
        expected_aborted_sha256=terminal.abort_sha256,
    )
    residue_paths = {
        entry["relative_leaf"] for entry in aborted["residue_inventory"]
    }
    raw_paths = list(plan.series_directory.glob("attempts/000000/.a.tmp-*/raw.frame"))
    assert len(raw_paths) == 1
    assert raw_paths[0].relative_to(plan.series_directory).as_posix() in residue_paths
    assert raw_paths[0].read_bytes()
    link_leaf = next(
        entry
        for entry in aborted["residue_inventory"]
        if entry["relative_leaf"].endswith("/residue-link")
    )
    assert link_leaf["entry_type"] == "symlink"
    assert link_leaf["sha256"] is None


async def test_pilot_terminal_fsync_failure_cannot_be_promoted_to_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    original_fsync = read_only_collector._fsync_directory
    failed = False

    def fail_completed_terminal_once(path: Path) -> None:
        nonlocal failed
        if (
            not failed
            and path == plan.series_directory
            and (path / "series.commit.json").exists()
        ):
            failed = True
            raise OSError("private completed terminal fsync detail")
        original_fsync(path)

    monkeypatch.setattr(
        read_only_collector,
        "_fsync_directory",
        fail_completed_terminal_once,
    )
    with pytest.raises(PilotTerminalError) as raised:
        await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )

    terminal = raised.value
    assert terminal.code == "pilot_series_durability_unconfirmed"
    assert terminal.abort_sha256 is not None
    assert terminal.durability_unknown is True
    aborted = store.verify_partial_series(
        plan,
        expected_aborted_sha256=terminal.abort_sha256,
    )
    residue_paths = {
        entry["relative_leaf"] for entry in aborted["residue_inventory"]
    }
    assert {"series.json", "series.commit.json"} <= residue_paths
    series_marker = json.loads(
        (plan.series_directory / "series.commit.json").read_text()
    )
    with pytest.raises(ArtifactStoreError, match="pilot_terminal_state_ambiguous"):
        store.verify_completed_series(
            plan,
            expected_series_sha256=series_marker["series_sha256"],
        )


async def test_pilot_keyboard_interrupt_is_redacted_to_typed_terminal_error(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")

    class InterruptingSession(_Session):
        async def read_raw_state_capture(
            self,
            *,
            accept_reports: bool = True,
        ) -> RawStateCapture:
            if self.address == "second.private":
                raise KeyboardInterrupt("private interrupt detail")
            return await super().read_raw_state_capture(accept_reports=accept_reports)

    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    with pytest.raises(PilotTerminalError) as raised:
        await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=InterruptingSession,
            discovery_timeout_seconds=2,
        )

    terminal = raised.value
    assert terminal.code == "keyboard_interrupt"
    assert str(terminal) == "keyboard_interrupt"
    assert terminal.abort_sha256 is not None
    assert terminal.durability_unknown is False
    assert terminal.__cause__ is None
    store.verify_partial_series(
        plan,
        expected_aborted_sha256=terminal.abort_sha256,
    )


async def test_pilot_abort_fsync_failure_returns_safe_unknown_terminal_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    store = PilotSeriesStore(root)
    targets = select_capture_pair(_config(), "first", "second")
    plan = store.prepare(
        targets,
        source_attestation=_TEST_SOURCE_ATTESTATION,
        planned_pair_count=1,
        requested_cadence_seconds=0.001,
        collector_commit_sha="a" * 40,
    )
    original_fsync = read_only_collector._fsync_directory

    def fail_series_directory(path: Path) -> None:
        if path == plan.series_directory:
            raise OSError("private abort fsync detail")
        original_fsync(path)

    monkeypatch.setattr(
        read_only_collector,
        "_fsync_directory",
        fail_series_directory,
    )
    with pytest.raises(PilotTerminalError) as raised:
        await store.run(
            plan,
            targets,
            source_attestation=_TEST_SOURCE_ATTESTATION,
            discovery_factory=lambda: _Discovery(_discovered()),
            session_factory=_Session,
            discovery_timeout_seconds=2,
        )

    terminal = raised.value
    assert terminal.code == "pilot_abort_durability_unconfirmed"
    assert terminal.plan_artifact_id == plan.plan_artifact_id
    assert terminal.series_id == plan.series_id
    assert terminal.plan_sha256 == plan.plan_sha256
    assert terminal.abort_sha256 is None
    assert terminal.durability_unknown is True
    assert terminal.__cause__ is None
