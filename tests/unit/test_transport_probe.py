import ast
import base64
import hashlib
import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.physical_identity import (
    PhysicalDeviceBinding,
    physical_identity_key,
)
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.errors import ProtocolTimeoutError
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
from jebao_flow.protocol.session import (
    STATE_REPLY_ACTION,
    STATE_REPORT_ACTION,
    RawStateCapture,
)
from jebao_flow.read_only_collector import CaptureTarget
from jebao_flow.transport_probe import (
    TransportProbeError,
    TransportProbeStore,
    run_transport_probe,
)

_FROZEN_IMPORT_PREFIXES = (
    "jebao_flow.devices",
    "jebao_flow.hardware_test",
    "jebao_flow.protocol.connection",
    "jebao_flow.protocol.control_session",
    "jebao_flow.recovery_supervisor",
    "jebao_flow.schedule_flow_experiment_cli",
    "jebao_flow.schedule_linkage_cli",
)


def _target(logical_id: str, suffix: str) -> CaptureTarget:
    vendor_device_id = f"test-device-{suffix}"
    mac_address = f"0200000000{suffix}"
    config_fingerprint = hashlib.sha256(f"config-{suffix}".encode()).hexdigest()
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=vendor_device_id,
        mac_address=mac_address,
        product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        config_fingerprint=config_fingerprint,
    )
    return CaptureTarget(
        logical_id=logical_id,
        product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        identity_binding_sha256=physical_identity_key(binding),
        vendor_device_id=vendor_device_id,
        mac_address=mac_address,
        config_fingerprint=config_fingerprint,
    )


def _discovered(target: CaptureTarget, address: str) -> DiscoveredDevice:
    return DiscoveredDevice(
        address=address,
        device_id=target.vendor_device_id,
        mac_address=target.mac_address,
        product_key=target.product_key,
    )


def _capture(action: int, status: bytes) -> RawStateCapture:
    wire = encode_frame(
        GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
        bytes([action]) + status,
    )
    return RawStateCapture(
        wire_frame=wire,
        action=action,
        status_payload=status,
    )


class _Discovery:
    def __init__(self, devices: list[DiscoveredDevice]) -> None:
        self.devices = devices

    async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
        assert timeout_seconds > 0
        return self.devices


class _Session:
    def __init__(
        self,
        capture: RawStateCapture,
        *,
        timeout_after_report: bool = False,
        emit_frame: bool = True,
    ) -> None:
        self.capture = capture
        self.timeout_after_report = timeout_after_report
        self.emit_frame = emit_frame
        self.read_count = 0
        self.disconnect_count = 0
        self.quarantined = False

    async def connect(self) -> None:
        return None

    async def authenticate(self) -> bytes:
        return b"not-persisted"

    async def read_raw_state_capture(
        self,
        *,
        accept_reports: bool = False,
        state_frame_observer=None,
    ) -> RawStateCapture:
        assert accept_reports is False
        self.read_count += 1
        if self.emit_frame and state_frame_observer is not None:
            state_frame_observer(self.capture, False if self.timeout_after_report else True)
        if self.timeout_after_report:
            raise ProtocolTimeoutError("private endpoint detail")
        return self.capture

    async def disconnect(self) -> None:
        self.disconnect_count += 1

    def quarantine(self) -> None:
        self.quarantined = True


async def test_probe_distinguishes_report_timeout_from_explicit_reply_and_preserves_raw(
    tmp_path: Path,
) -> None:
    first = _target("master", "01")
    second = _target("slave", "02")
    devices = [_discovered(first, "192.0.2.10"), _discovered(second, "192.0.2.11")]
    report = _capture(STATE_REPORT_ACTION, bytes(452))
    reply = _capture(STATE_REPLY_ACTION, bytes(452))
    sessions: list[_Session] = []

    def session_factory(address: str) -> _Session:
        session = (
            _Session(report, timeout_after_report=True)
            if address == "192.0.2.10"
            else _Session(reply)
        )
        sessions.append(session)
        return session

    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    store = TransportProbeStore(root)
    reference = store.prepare(
        (first, second),
        commit_sha="a" * 40,
        collector_source_digest_sha256="b" * 64,
        probe_source_digest_sha256="c" * 64,
        response_timeout_seconds=5.0,
        expected_linkages=("independent", "independent"),
    )
    metadata = await run_transport_probe(
        reference,
        store,
        (first, second),
        discovery_factory=lambda: _Discovery(devices),
        session_factory=session_factory,
        discovery_timeout_seconds=1.0,
    )

    assert metadata.status == "probe_completed_context_valid"
    assert metadata.q2_verdict == "UNKNOWN"
    assert metadata.target_linkage_contexts == (
        "expected_linkage_observed",
        "expected_linkage_observed",
    )
    assert metadata.target_report_frame_counts == (1, 0)
    assert metadata.target_reply_frame_counts == (0, 1)
    assert metadata.target_outcomes == (
        "report_observed_explicit_timeout",
        "explicit_reply_observed",
    )
    assert [session.read_count for session in sessions] == [1, 1]
    assert [session.disconnect_count for session in sessions] == [1, 1]
    assert stat.S_IMODE((reference.directory / "plan.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((reference.directory / "result.json").stat().st_mode) == 0o600

    result_payload = (reference.directory / "result.json").read_bytes()
    result = json.loads(result_payload)
    assert hashlib.sha256(result_payload).hexdigest() == metadata.artifact_sha256
    assert result["q2_verdict"] == "UNKNOWN"
    first_result, second_result = result["results"]
    assert first_result["failure_class"] == "ProtocolTimeoutError"
    assert first_result["failure_phase"] == "strict_state_read"
    assert first_result["preserved_frames"][0]["transport_action"] == STATE_REPORT_ACTION
    assert base64.b64decode(first_result["preserved_frames"][0]["wire_frame_base64"]) == (
        report.wire_frame
    )
    assert second_result["preserved_frames"][0]["transport_action"] == STATE_REPLY_ACTION
    assert second_result["preserved_frames"][0]["decode_status"] == "decoded"
    assert "192.0.2" not in result_payload.decode("ascii")
    assert "not-persisted" not in result_payload.decode("ascii")


async def test_probe_rejects_wrong_linkage_context_without_changing_q2_verdict(
    tmp_path: Path,
) -> None:
    first = _target("master", "01")
    second = _target("slave", "02")
    devices = [_discovered(first, "192.0.2.10"), _discovered(second, "192.0.2.11")]
    independent_reply = _capture(STATE_REPLY_ACTION, bytes(452))
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    store = TransportProbeStore(root)
    reference = store.prepare(
        (first, second),
        commit_sha="a" * 40,
        collector_source_digest_sha256="b" * 64,
        probe_source_digest_sha256="c" * 64,
        response_timeout_seconds=5.0,
        expected_linkages=("master", "async_slave"),
    )

    metadata = await run_transport_probe(
        reference,
        store,
        (first, second),
        discovery_factory=lambda: _Discovery(devices),
        session_factory=lambda _address: _Session(independent_reply),
        discovery_timeout_seconds=1.0,
    )

    assert metadata.status == "probe_completed_linkage_context_invalid"
    assert metadata.q2_verdict == "UNKNOWN"
    assert metadata.target_linkage_contexts == (
        "linkage_mismatch_observed",
        "linkage_mismatch_observed",
    )


async def test_probe_marks_a_frame_free_run_as_information_zero(tmp_path: Path) -> None:
    first = _target("master", "01")
    second = _target("slave", "02")
    devices = [_discovered(first, "192.0.2.10"), _discovered(second, "192.0.2.11")]
    unused = _capture(STATE_REPORT_ACTION, bytes(452))
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    store = TransportProbeStore(root)
    reference = store.prepare(
        (first, second),
        commit_sha="a" * 40,
        collector_source_digest_sha256="b" * 64,
        probe_source_digest_sha256="c" * 64,
        response_timeout_seconds=5.0,
        expected_linkages=("master", "async_slave"),
    )

    metadata = await run_transport_probe(
        reference,
        store,
        (first, second),
        discovery_factory=lambda: _Discovery(devices),
        session_factory=lambda _address: _Session(
            unused,
            timeout_after_report=True,
            emit_frame=False,
        ),
        discovery_timeout_seconds=1.0,
    )

    assert metadata.status == "probe_completed_without_state_frame"
    assert metadata.target_report_frame_counts == (0, 0)
    assert metadata.target_reply_frame_counts == (0, 0)
    assert metadata.q2_verdict == "UNKNOWN"


def test_probe_never_deletes_an_existing_committed_artifact(tmp_path: Path) -> None:
    first = _target("master", "01")
    second = _target("slave", "02")
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    store = TransportProbeStore(root)
    reference = store.prepare(
        (first, second),
        commit_sha="a" * 40,
        collector_source_digest_sha256="b" * 64,
        probe_source_digest_sha256="c" * 64,
        response_timeout_seconds=5.0,
        expected_linkages=("independent", "independent"),
    )
    result_path = reference.directory / "result.json"
    result_path.write_bytes(b"pre-existing")
    result_path.chmod(0o600)
    placeholder = {
        "identity_invariant": True,
        "outcome": "explicit_reply_observed",
    }

    with pytest.raises(TransportProbeError, match="probe_artifact_write_failed"):
        store.commit(
            reference,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            targets=(first, second),
            results=(placeholder, placeholder),
        )

    assert result_path.read_bytes() == b"pre-existing"


def test_probe_modules_have_no_device_control_import_or_write_call() -> None:
    root = Path(__file__).parents[2]
    for relative in (
        "src/jebao_flow/transport_probe.py",
        "src/jebao_flow/transport_probe_cli.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
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
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
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
        assert not {"send_raw_control", "write_target", "write_schedule_slots"} & called

    script = (
        "import sys; import jebao_flow.transport_probe; "
        "import jebao_flow.transport_probe_cli; "
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
