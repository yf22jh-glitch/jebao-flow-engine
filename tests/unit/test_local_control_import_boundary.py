from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "jebao_flow" / "local_control"
CLI = ROOT / "src" / "jebao_flow" / "local_control_cli.py"

FROZEN_MODULES = {
    "jebao_flow.devices.schedule_linkage",
    "jebao_flow.devices.schedule_flow_experiment",
    "jebao_flow.devices.schedule_transaction",
    "jebao_flow.devices.linkage",
    "jebao_flow.schedule_flow_experiment_cli",
    "jebao_flow.schedule_linkage_cli",
}
WRITE_PROTOCOL_MODULES = {
    "jebao_flow.protocol.control",
    "jebao_flow.protocol.control_session",
    "jebao_flow.protocol.schedule_wire",
}


def _imports(path: Path) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_frozen_harness_is_absent_from_every_local_control_source() -> None:
    for path in (*PACKAGE.glob("*.py"), CLI):
        assert _imports(path).isdisjoint(FROZEN_MODULES), path.name


def test_write_protocol_imports_exist_only_at_the_device_writer_edge() -> None:
    importers = {
        path.name: _imports(path) & WRITE_PROTOCOL_MODULES
        for path in (*PACKAGE.glob("*.py"), CLI)
        if _imports(path) & WRITE_PROTOCOL_MODULES
    }

    assert importers == {
        "device_writer.py": {
            "jebao_flow.protocol.control",
            "jebao_flow.protocol.control_session",
            "jebao_flow.protocol.schedule_wire",
        }
    }


def test_read_only_local_control_import_closure_excludes_write_and_frozen_modules() -> None:
    modules = (
        "jebao_flow.local_control.models",
        "jebao_flow.local_control.catalog",
        "jebao_flow.local_control.compose",
        "jebao_flow.local_control.dosing",
        "jebao_flow.local_control.install",
        "jebao_flow.local_control.journal",
        "jebao_flow.local_control.resolver",
        "jebao_flow.local_control.schedule",
        "jebao_flow.local_control.ha",
        "jebao_flow.local_control.qualification",
        "jebao_flow.local_control.service",
        "jebao_flow.local_control.state",
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
    assert closure.isdisjoint(FROZEN_MODULES)
    assert closure.isdisjoint(WRITE_PROTOCOL_MODULES)
    assert "jebao_flow.hardware_test" not in closure


def test_write_runtime_and_cli_import_closure_still_excludes_the_frozen_harness() -> None:
    modules = (
        "jebao_flow.local_control.coordinator",
        "jebao_flow.local_control.device_writer",
        "jebao_flow.local_control.group",
        "jebao_flow.local_control.group_service",
        "jebao_flow.local_control.group_supervisor",
        "jebao_flow.local_control.lease",
        "jebao_flow.local_control.mqtt",
        "jebao_flow.local_control_cli",
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
    assert closure.isdisjoint(FROZEN_MODULES)
    assert "jebao_flow.hardware_test" not in closure
    assert not any(name.startswith("jebao_flow.devices") for name in closure)


def test_production_group_lifecycle_has_no_command_transport_caller() -> None:
    connector_sources = (
        (PACKAGE / "mqtt.py").read_text(encoding="utf-8"),
        CLI.read_text(encoding="utf-8"),
    )

    assert all("apply_runtime_command" not in source for source in connector_sources)


def test_per_device_lock_uses_the_stable_physical_key_filename() -> None:
    source = (PACKAGE / "lease.py").read_text(encoding="utf-8")

    assert 'directory / f"{key}.lock"' in source
    assert "_probe_hardware_safety_root" not in source
    assert "configuration_fingerprint" not in source
    assert "getpid" not in source
    assert "hostname" not in source
