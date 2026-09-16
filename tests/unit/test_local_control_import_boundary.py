from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "jebao_flow" / "local_control"
CLI = ROOT / "src" / "jebao_flow" / "local_control_cli.py"
QUALIFICATION_CLI = ROOT / "src" / "jebao_flow" / "local_control_qualification_cli.py"
POWER_QUALIFICATION_CLI = (
    ROOT / "src" / "jebao_flow" / "local_control_power_qualification_cli.py"
)
RECOVERY_DRILL_CLI = ROOT / "src" / "jebao_flow" / "local_control_recovery_drill_cli.py"
QUALIFICATION_SET_CLI = ROOT / "src" / "jebao_flow" / "local_control_qualification_set_cli.py"

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
    for path in (
        *PACKAGE.glob("*.py"),
        CLI,
        QUALIFICATION_CLI,
        POWER_QUALIFICATION_CLI,
        RECOVERY_DRILL_CLI,
        QUALIFICATION_SET_CLI,
    ):
        assert _imports(path).isdisjoint(FROZEN_MODULES), path.name


def test_write_protocol_imports_exist_only_at_the_device_writer_edge() -> None:
    importers = {
        path.name: _imports(path) & WRITE_PROTOCOL_MODULES
        for path in (
            *PACKAGE.glob("*.py"),
            CLI,
            QUALIFICATION_CLI,
            POWER_QUALIFICATION_CLI,
            RECOVERY_DRILL_CLI,
            QUALIFICATION_SET_CLI,
        )
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
        "jebao_flow.local_control.group_command",
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


def test_group_command_import_closure_stays_pure() -> None:
    script = (
        "import importlib,json,sys;"
        "importlib.import_module('jebao_flow.local_control.group_command');"
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
    assert closure.isdisjoint(
        {
            "jebao_flow.groups.manager",
            "jebao_flow.groups.runtime",
            "jebao_flow.local_control.coordinator",
            "jebao_flow.local_control.group_service",
            "jebao_flow.local_control.group_supervisor",
            "jebao_flow.local_control.ha",
            "jebao_flow.local_control.journal",
            "jebao_flow.local_control.mqtt",
        }
    )


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
        "jebao_flow.local_control.qualification_producer",
        "jebao_flow.local_control_qualification_cli",
        "jebao_flow.local_control.power_qualification_producer",
        "jebao_flow.local_control_power_qualification_cli",
        "jebao_flow.local_control.recovery_drill",
        "jebao_flow.local_control_recovery_drill_cli",
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


def test_recovery_drill_is_forward_only_and_uses_only_the_current_write_edge() -> None:
    drill = PACKAGE / "recovery_drill.py"
    imports = _imports(drill)
    source = drill.read_text(encoding="utf-8")

    assert imports.isdisjoint(FROZEN_MODULES)
    assert not any(name.startswith("jebao_flow.devices") for name in imports)
    assert not any(name.startswith("jebao_flow.protocol") for name in imports)
    assert "coordinator.recover" not in source
    assert "ConnectedDeviceWriter" in source
    assert "verify_prepared_readback" in source


def test_qualification_set_import_closure_excludes_device_network_and_write_modules() -> None:
    modules = (
        "jebao_flow.local_control.qualification_set",
        "jebao_flow.local_control_qualification_set_cli",
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
    assert "jebao_flow.local_control.coordinator" not in closure
    assert "jebao_flow.local_control.device_writer" not in closure
    assert "jebao_flow.local_control.qualification_producer" not in closure
    assert not any(name.startswith("jebao_flow.devices") for name in closure)


def test_qualification_producer_uses_only_the_current_write_edge() -> None:
    producer = PACKAGE / "qualification_producer.py"
    imports = _imports(producer)
    source = producer.read_text(encoding="utf-8")

    assert imports.isdisjoint(FROZEN_MODULES)
    assert not any(name.startswith("jebao_flow.devices") for name in imports)
    assert not any(name.startswith("jebao_flow.protocol") for name in imports)
    assert "observe_many" not in source
    assert "ConnectedDeviceWriter" in source
    assert "LocalControlJournalStore" in source


def test_power_qualification_producer_reuses_only_the_current_write_edge() -> None:
    producer = PACKAGE / "power_qualification_producer.py"
    imports = _imports(producer)
    source = producer.read_text(encoding="utf-8")

    assert imports.isdisjoint(FROZEN_MODULES)
    assert not any(name.startswith("jebao_flow.devices") for name in imports)
    assert not any(name.startswith("jebao_flow.protocol") for name in imports)
    assert "ConnectedDeviceWriter" in source
    assert "_execute_prepared" not in source
    assert "global_operation_lock_path" not in source
    assert "physical_lock_directory" not in source


def test_qualification_producer_schema_access_is_read_only_through_writer_edge() -> None:
    producer = PACKAGE / "qualification_producer.py"
    tree = ast.parse(producer.read_text(encoding="utf-8"), filename=str(producer))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    writer_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "jebao_flow.local_control.device_writer"
        for alias in node.names
    }
    schema_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_product_schema"
    ]
    schema_names: set[str] = set()
    direct_members: set[str] = set()
    for call in schema_calls:
        parent = parents[call]
        if isinstance(parent, ast.Attribute):
            direct_members.add(parent.attr)
        elif isinstance(parent, ast.Assign):
            schema_names.update(
                target.id for target in parent.targets if isinstance(target, ast.Name)
            )

    named_members = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in schema_names
    }

    assert "get_product_schema" in writer_imports
    # A diagnostic may add another read-only decode. Protect the access boundary,
    # not a snapshot of how many call sites happened to exist before it was added.
    assert schema_calls
    assert all(isinstance(parents[call], (ast.Attribute, ast.Assign)) for call in schema_calls)
    assert direct_members | named_members == {"attributes_by_name", "decode_status"}


def test_production_group_transport_calls_the_service_not_the_supervisor() -> None:
    connector_sources = (
        (PACKAGE / "mqtt.py").read_text(encoding="utf-8"),
        CLI.read_text(encoding="utf-8"),
    )

    assert all("apply_runtime_command" not in source for source in connector_sources)
    assert "apply_group_switch" in connector_sources[0]
    assert "LocalControlGroupCommandRouter" in connector_sources[1]


def test_per_device_lock_uses_the_stable_physical_key_filename() -> None:
    source = (PACKAGE / "lease.py").read_text(encoding="utf-8")

    assert 'directory / f"{key}.lock"' in source
    assert "_probe_hardware_safety_root" not in source
    assert "configuration_fingerprint" not in source
    assert "getpid" not in source
    assert "hostname" not in source


def test_lease_terminal_parser_does_not_import_exact_restore_or_protocol_modules() -> None:
    imports = _imports(PACKAGE / "lease.py")

    assert "jebao_flow.exact_restore_guard" in imports
    assert not any(
        (name.startswith("jebao_flow.exact_restore") and name != "jebao_flow.exact_restore_guard")
        or name.startswith(("jebao_flow.protocol", "jebao_flow.read_only_collector"))
        for name in imports
    )

    script = (
        "import importlib,json,sys;"
        "importlib.import_module('jebao_flow.local_control');"
        "before=set(sys.modules);"
        "importlib.import_module('jebao_flow.local_control.lease');"
        "print(json.dumps(sorted(name for name in set(sys.modules)-before "
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
    added_closure = set(json.loads(completed.stdout))
    assert "jebao_flow.exact_restore_guard" in added_closure
    assert not any(
        (name.startswith("jebao_flow.exact_restore") and name != "jebao_flow.exact_restore_guard")
        or name.startswith(("jebao_flow.protocol", "jebao_flow.read_only_collector"))
        or name in FROZEN_MODULES
        for name in added_closure
    )
