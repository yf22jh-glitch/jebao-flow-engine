from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import capability_raw_analyzer, read_only_collector
from jebao_flow.capability_matrix import CapabilityClaimError, render_yaml
from jebao_flow.capability_raw_analyzer import analyze_series, main
from jebao_flow.config import AppConfig
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.session import RawStateCapture
from jebao_flow.read_only_collector import PilotSeriesStore, select_capture_pair
from jebao_flow.source_attestation import SourceAttestationError

PRODUCT_KEY = "50dbc92221fd4d33ae69a1fedd43b555"
BAR_PRODUCT_KEY = "1d8c63eaccac4205b92c84d77d5a08fb"
FIRST_DEVICE_ID = "private-first-device"
SECOND_DEVICE_ID = "private-second-device"
FIRST_MAC = "001122334455"
SECOND_MAC = "66778899aabb"
_TEST_SOURCE_ATTESTATION = SimpleNamespace(runtime_source_digest_sha256="f" * 64)


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "mqtt.local", "topic_prefix": "jebao-flow/test"},
            "runtime": {"dry_run": True},
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
                    "control": {"allow_hardware_writes": False},
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


def _discovered() -> list[DiscoveredDevice]:
    return [
        DiscoveredDevice(
            address="first.private",
            device_id=FIRST_DEVICE_ID,
            mac_address=FIRST_MAC,
            product_key=PRODUCT_KEY,
        ),
        DiscoveredDevice(
            address="second.private",
            device_id=SECOND_DEVICE_ID,
            mac_address=SECOND_MAC,
            product_key=PRODUCT_KEY,
        ),
    ]


def _pro_status(*, flow: int, auto_flow: int) -> bytes:
    raw = bytearray(452)
    raw[0] = 0b00000011  # SwitchON + TimerON + independent linkage.
    raw[1:11] = bytes((2, flow, 32, 5, 10, 1, auto_flow, 15, 5, 10))
    raw[11:20] = bytes((0, 0, 24, 0, 2, 40, 20, 10, 5))
    raw[443:451] = bytes((20, 26, 8, 31, 0, 12, 0, 0))
    return bytes(raw)


def _bar_status(*, slot_flow: int) -> bytes:
    raw = bytearray(401)
    raw[8:16] = bytes((0, 0, 24, 0, 4, slot_flow, 20, 0))
    return bytes(raw)


class _Discovery:
    async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
        assert timeout_seconds == 2
        return _discovered()


class _Session:
    raw_by_address = {
        "first.private": _pro_status(flow=30, auto_flow=50),
        "second.private": _pro_status(flow=45, auto_flow=70),
    }

    def __init__(self, address: str) -> None:
        self.address = address

    async def connect(self) -> None:
        return None

    async def authenticate(self) -> bytes:
        return b"private-passcode"

    async def read_raw_state_capture(
        self,
        *,
        accept_reports: bool = True,
    ) -> RawStateCapture:
        assert accept_reports is False
        status = self.raw_by_address[self.address]
        wire = encode_frame(
            GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
            b"\x03" + status,
        )
        return RawStateCapture(wire_frame=wire, action=0x03, status_payload=status)

    async def disconnect(self) -> None:
        return None

    def quarantine(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _source_attestation(monkeypatch: pytest.MonkeyPatch) -> None:
    def validate(attestation: object, *, expected_commit: str) -> object:
        assert expected_commit == "a" * 40
        if attestation is not _TEST_SOURCE_ATTESTATION:
            raise SourceAttestationError("collector_source_attestation_invalid")
        return attestation

    monkeypatch.setattr(
        read_only_collector,
        "validate_collector_source_attestation",
        validate,
    )


async def _completed_pilot(tmp_path: Path, *, pair_count: int = 2):
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
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
        discovery_factory=_Discovery,
        session_factory=_Session,
        discovery_timeout_seconds=2,
    )
    return root, store, plan, metadata


def _content_set_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.asyncio
async def test_analyzer_uses_verified_pair_extraction_and_never_mutates_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, store, plan, metadata = await _completed_pilot(tmp_path)
    before = _content_set_digest(root)
    extracted: list[int] = []
    original = store.extract_verified_accepted_pair

    def extract(reference, *, expected_series_sha256: str, ordinal: int):
        extracted.append(ordinal)
        return original(
            reference,
            expected_series_sha256=expected_series_sha256,
            ordinal=ordinal,
        )

    monkeypatch.setattr(store, "extract_verified_accepted_pair", extract)
    claim_set = analyze_series(
        store,
        plan,
        expected_series_sha256=metadata.series_sha256,
        product_key=PRODUCT_KEY,
        analyzer_commit="c" * 40,
        analyzer_source_digest_sha256="d" * 64,
    )

    assert extracted == [0, 1]
    assert _content_set_digest(root) == before
    claims = {claim.subject: claim for claim in claim_set.claims}
    assert claims["Flow.observed_values"].value == (30, 45)
    assert claims["AutoFlow.observed_values"].value == (50, 70)
    assert claims["schedule.slot_mode.observed_values"].value == ("constant",)
    assert claims["schedule.slot_parameter.flow.observed_values"].value == (40,)
    assert claims["transport_reply_action"].value == "0x03"
    assert all(claim.status == "PASS" for claim in claim_set.claims)
    render_yaml(claim_set)


class _OutOfRangeStore:
    def __init__(self, raw_status: bytes, *, product_key: str = PRODUCT_KEY) -> None:
        wire = encode_frame(
            GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
            b"\x03" + raw_status,
        )
        self.sample = SimpleNamespace(raw_wire_frame=wire)
        self.product_key = product_key
        self.raw_status_size = len(raw_status)

    def verify_plan(self, reference):
        return {
            "ordered_targets": [
                {"product_key": self.product_key},
                {"product_key": self.product_key},
            ],
            "acquisition": {
                "status_payload_size_bytes": self.raw_status_size,
                "serial_payload_size_bytes": self.raw_status_size + 1,
            },
        }

    def verify_completed_series(self, reference, *, expected_series_sha256: str):
        return {"records": [{"ordinal": 0, "outcome": "accepted"}]}

    def extract_verified_accepted_pair(
        self,
        reference,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ):
        return SimpleNamespace(samples=(self.sample, self.sample))


def test_out_of_range_decoded_value_is_unknown_not_pass() -> None:
    raw = _pro_status(flow=150, auto_flow=50)
    reference = SimpleNamespace(series_id="JFS-" + "a" * 32)
    claim_set = analyze_series(
        _OutOfRangeStore(raw),
        reference,
        expected_series_sha256="b" * 64,
        product_key=PRODUCT_KEY,
        analyzer_commit="c" * 40,
        analyzer_source_digest_sha256="d" * 64,
    )
    claims = {claim.subject: claim for claim in claim_set.claims}
    assert claims["Flow.observed_values"].value == (150,)
    assert claims["Flow.observed_values"].status == "UNKNOWN"


def test_out_of_range_pro_schedule_parameter_is_unknown_not_pass() -> None:
    raw = bytearray(_pro_status(flow=30, auto_flow=50))
    raw[16] = 200  # slot 0 flow
    reference = SimpleNamespace(series_id="JFS-" + "a" * 32)
    claim_set = analyze_series(
        _OutOfRangeStore(bytes(raw)),
        reference,
        expected_series_sha256="b" * 64,
        product_key=PRODUCT_KEY,
        analyzer_commit="c" * 40,
        analyzer_source_digest_sha256="d" * 64,
    )
    claims = {claim.subject: claim for claim in claim_set.claims}
    assert claims["schedule.slot_parameter.flow.observed_values"].value == (200,)
    assert claims["schedule.slot_parameter.flow.observed_values"].status == "UNKNOWN"
    assert claims["schedule.slot_mode.observed_values"].status == "UNKNOWN"
    assert claims["schedule.active_slot_count.observed_values"].status == "UNKNOWN"


def test_unvalidated_bar_schedule_claims_are_omitted_deny_by_default() -> None:
    reference = SimpleNamespace(series_id="JFS-" + "a" * 32)
    claim_set = analyze_series(
        _OutOfRangeStore(
            _bar_status(slot_flow=200),
            product_key=BAR_PRODUCT_KEY,
        ),
        reference,
        expected_series_sha256="b" * 64,
        product_key=BAR_PRODUCT_KEY,
        analyzer_commit="c" * 40,
        analyzer_source_digest_sha256="d" * 64,
    )
    subjects = {claim.subject for claim in claim_set.claims}
    assert "raw_status_size_bytes" in subjects
    assert "Flow.observed_values" in subjects
    assert not any(subject.startswith("schedule.") for subject in subjects)


def test_analyzer_rejects_cli_product_not_bound_by_verified_plan() -> None:
    raw = _pro_status(flow=30, auto_flow=50)
    store = _OutOfRangeStore(raw)
    reference = SimpleNamespace(series_id="JFS-" + "a" * 32)
    with pytest.raises(
        CapabilityClaimError,
        match="raw_analyzer_product_binding_mismatch",
    ):
        analyze_series(
            store,
            reference,
            expected_series_sha256="b" * 64,
            product_key="1d8c63eaccac4205b92c84d77d5a08fb",
            analyzer_commit="c" * 40,
            analyzer_source_digest_sha256="d" * 64,
        )


def test_cli_rejects_output_inside_private_artifact_root_before_source_work(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    series_id = "JFS-" + "a" * 32
    output = root / f"observation-claim-set.{series_id}.generated.yaml"
    assert main(
        (
            "--repo-root",
            str(tmp_path),
            "--analyzer-commit",
            "b" * 40,
            "--artifact-root",
            str(root),
            "--series-id",
            series_id,
            "--expected-series-sha256",
            "c" * 64,
            "--product-key",
            PRODUCT_KEY,
            "--output",
            str(output),
        )
    ) == 2
    assert capsys.readouterr().err.strip() == "raw_analyzer_output_inside_artifact_root"
    assert not output.exists()


def test_cli_parse_failure_never_echoes_private_argv(
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_value = "/private/home/selian/secret-artifacts/JFS-deadbeef/plan.json"
    assert main(("--oops", private_value)) == 2
    stderr = capsys.readouterr().err.strip()
    assert stderr == "raw_analyzer_command_line_invalid"
    assert private_value not in stderr


def test_cli_redacts_unexpected_exception_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    series_id = "JFS-" + "a" * 32
    output = tmp_path / f"observation-claim-set.{series_id}.generated.yaml"

    def fail(*args: object, **kwargs: object) -> str:
        raise RuntimeError("/private/artifact-root/device-id")

    monkeypatch.setattr(capability_raw_analyzer, "verify_source_pin", fail)
    assert main(
        (
            "--repo-root",
            str(tmp_path),
            "--analyzer-commit",
            "b" * 40,
            "--artifact-root",
            str(artifact_root),
            "--series-id",
            series_id,
            "--expected-series-sha256",
            "c" * 64,
            "--product-key",
            PRODUCT_KEY,
            "--output",
            str(output),
        )
    ) == 2
    assert capsys.readouterr().err.strip() == "private_operation_error"
    assert not output.exists()


def test_analyzer_import_graph_excludes_write_and_live_session_symbols() -> None:
    module_path = Path("src/jebao_flow/capability_raw_analyzer.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    forbidden_modules = (
        "jebao_flow.devices",
        "jebao_flow.protocol.control",
        "jebao_flow.schedule_linkage_cli",
        "jebao_flow.schedule_flow_experiment_cli",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_modules
    )
    forbidden_symbols = {
        "build_control_payload",
        "encode_frame",
        "encode_local_wavemaker_pro_schedule_entry",
        "patch_local_wavemaker_pro_schedule_slot",
        "GizwitsSession",
    }
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name in forbidden_symbols for alias in node.names)
        for node in ast.walk(tree)
    )

    script = (
        "import sys; import jebao_flow.capability_raw_analyzer; "
        "print('\\n'.join(sorted(name for name in sys.modules "
        "if name.startswith('jebao_flow.devices') "
        "or name == 'jebao_flow.protocol.control_session')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "\n"


def test_analyzer_import_closure_change_forces_source_pin_review() -> None:
    expected = {
        "jebao_flow",
        "jebao_flow.capability_matrix",
        "jebao_flow.capability_raw_analyzer",
        "jebao_flow.config",
        "jebao_flow.groups",
        "jebao_flow.groups.calculator",
        "jebao_flow.groups.models",
        "jebao_flow.patterns",
        "jebao_flow.patterns.anti_phase",
        "jebao_flow.patterns.base",
        "jebao_flow.patterns.constant",
        "jebao_flow.patterns.gyre",
        "jebao_flow.patterns.nutrient_transport",
        "jebao_flow.patterns.randomized",
        "jebao_flow.patterns.sync",
        "jebao_flow.patterns.tidal_swell",
        "jebao_flow.physical_identity",
        "jebao_flow.protocol",
        "jebao_flow.protocol.codec",
        "jebao_flow.protocol.control",
        "jebao_flow.protocol.discovery",
        "jebao_flow.protocol.errors",
        "jebao_flow.protocol.models",
        "jebao_flow.protocol.profiles",
        "jebao_flow.protocol.schedule",
        "jebao_flow.protocol.schedule_wire",
        "jebao_flow.protocol.schema",
        "jebao_flow.protocol.session",
        "jebao_flow.read_only_collector",
        "jebao_flow.safety",
        "jebao_flow.safety.limits",
        "jebao_flow.source_attestation",
    }
    script = (
        "import sys; import jebao_flow.capability_raw_analyzer; "
        "print('\\n'.join(sorted(name for name in sys.modules "
        "if name == 'jebao_flow' or name.startswith('jebao_flow.'))))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-P", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(result.stdout.splitlines()) == expected


def test_analyzer_source_pin_scope_contains_exact_claim_affecting_modules() -> None:
    assert dict(capability_raw_analyzer.PINNED_SOURCES) == {
        "jebao_flow.capability_matrix": "src/jebao_flow/capability_matrix.py",
        "jebao_flow.capability_raw_analyzer": (
            "src/jebao_flow/capability_raw_analyzer.py"
        ),
        "jebao_flow.protocol.codec": "src/jebao_flow/protocol/codec.py",
        "jebao_flow.protocol.models": "src/jebao_flow/protocol/models.py",
        "jebao_flow.protocol.profiles": "src/jebao_flow/protocol/profiles.py",
        "jebao_flow.protocol.schedule": "src/jebao_flow/protocol/schedule.py",
        "jebao_flow.protocol.schedule_wire": (
            "src/jebao_flow/protocol/schedule_wire.py"
        ),
        "jebao_flow.protocol.schema": "src/jebao_flow/protocol/schema.py",
        "jebao_flow.read_only_collector": "src/jebao_flow/read_only_collector.py",
    }
