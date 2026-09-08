import ast
import json
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "jebao_flow.devices",
    "jebao_flow.protocol.control",
    "jebao_flow.protocol.control_session",
    "jebao_flow.operator_capture.device_writer",
)


def test_operator_capture_import_graph_has_no_write_or_frozen_edge() -> None:
    sources = [
        *Path("src/jebao_flow/operator_capture").glob("*.py"),
        Path("src/jebao_flow/operator_capture_cli.py"),
    ]
    violations: list[str] = []
    for source_path in sorted(sources):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                if any(
                    name == prefix or name.startswith(f"{prefix}.") for prefix in FORBIDDEN_PREFIXES
                ):
                    violations.append(f"{source_path}:{node.lineno}:{name}")

    assert violations == []


def test_runtime_import_closure_does_not_load_write_or_frozen_modules() -> None:
    script = """
import json
import sys
import jebao_flow.operator_capture
import jebao_flow.operator_capture.app_catalog
import jebao_flow.operator_capture.capability
import jebao_flow.operator_capture.ha_projection
import jebao_flow.operator_capture.pcap
import jebao_flow.operator_capture.schedule_model
import jebao_flow.operator_capture_cli
forbidden = [
    name for name in sys.modules
    if name.startswith('jebao_flow.devices')
    or name in {'jebao_flow.protocol.control', 'jebao_flow.protocol.control_session'}
    or name.startswith('jebao_flow.operator_capture.device_writer')
]
print(json.dumps(sorted(forbidden)))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
    assert completed.stderr == ""
