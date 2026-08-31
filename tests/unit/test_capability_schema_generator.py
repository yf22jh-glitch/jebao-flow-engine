from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow.capability_matrix import render_yaml
from jebao_flow.capability_schema_generator import (
    PINNED_SOURCES,
    classify_datapoint,
    generate,
    main,
)
from jebao_flow.protocol.profiles import KNOWN_SCHEMAS

PRO_PRODUCT = "50dbc92221fd4d33ae69a1fedd43b555"
BAR_PRODUCT = "1d8c63eaccac4205b92c84d77d5a08fb"


def _claims_by_subject(product_key: str):
    claim_set = generate(
        (PRO_PRODUCT, BAR_PRODUCT),
        commit_sha="a" * 40,
        source_digest_sha256="b" * 64,
    )
    return {
        claim.subject: claim
        for claim in claim_set.claims
        if claim.product_key == product_key
    }


def test_generator_emits_only_wire_schema_claims_for_preregistered_products() -> None:
    claim_set = generate(
        (PRO_PRODUCT, BAR_PRODUCT),
        commit_sha="a" * 40,
        source_digest_sha256="b" * 64,
    )

    assert {claim.product_key for claim in claim_set.claims} == {
        PRO_PRODUCT,
        BAR_PRODUCT,
    }
    assert {claim.layer for claim in claim_set.claims} <= {"L0", "L1", "L2"}
    assert {claim.goal_tier for claim in claim_set.claims} == {"wire"}
    assert {claim.evidence_tier for claim in claim_set.claims} == {"d"}
    assert {claim.status for claim in claim_set.claims} == {"PASS"}


def test_generator_source_pin_scope_remains_the_five_schema_modules() -> None:
    assert dict(PINNED_SOURCES) == {
        "jebao_flow.capability_matrix": "src/jebao_flow/capability_matrix.py",
        "jebao_flow.capability_schema_generator": (
            "src/jebao_flow/capability_schema_generator.py"
        ),
        "jebao_flow.protocol.profiles": "src/jebao_flow/protocol/profiles.py",
        "jebao_flow.protocol.schedule": "src/jebao_flow/protocol/schedule.py",
        "jebao_flow.protocol.schema": "src/jebao_flow/protocol/schema.py",
    }


def test_generator_preserves_distinct_native_and_schedule_code_spaces() -> None:
    pro = _claims_by_subject(PRO_PRODUCT)
    bar = _claims_by_subject(BAR_PRODUCT)

    assert pro["Mode.enum_values"].value == (
        "pulse",
        "sine",
        "constant",
        "random",
        "tidal",
        "nutrient_transport",
        "circulation",
        "feed",
        "custom",
    )
    assert bar["Mode.enum_values"].value == ("classic", "sine", "random", "constant")
    assert bar["AutoMode.enum_values"].value == (
        "stopped",
        "classic",
        "sine",
        "random",
        "constant",
        "feed",
    )
    assert bar["AutoMode.code_space"].value == bar["AutoMode.enum_values"].value


def test_generator_derives_schedule_sizes_and_distinguishes_sources() -> None:
    pro = _claims_by_subject(PRO_PRODUCT)
    bar = _claims_by_subject(BAR_PRODUCT)

    assert pro["schedule_image_size_bytes"].value == 432
    assert bar["schedule_image_size_bytes"].value == 384
    assert pro["schedule_slot_size_bytes"].value == 9
    assert bar["schedule_slot_size_bytes"].value == 8
    assert ".protocol.schedule@" in pro["schedule_image_size_bytes"].source
    assert ".protocol.profiles@" in pro["Flow.schema_range"].source


def test_generator_output_is_deterministic_for_product_order() -> None:
    first = generate(
        (PRO_PRODUCT, BAR_PRODUCT),
        commit_sha="a" * 40,
        source_digest_sha256="b" * 64,
    )
    second = generate(
        (BAR_PRODUCT, PRO_PRODUCT),
        commit_sha="a" * 40,
        source_digest_sha256="b" * 64,
    )
    assert render_yaml(first) == render_yaml(second)


def test_every_known_profile_datapoint_has_a_total_layer_classification() -> None:
    for schema in KNOWN_SCHEMAS.values():
        for attribute in schema.attributes:
            layer = classify_datapoint(attribute)
            if attribute.is_problem:
                assert layer is None
            else:
                assert layer in {"L1", "L2"}


def _pinned_repository(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    repository = tmp_path / "repo"
    repository.mkdir()
    originals: dict[str, bytes] = {}
    for _, relative in PINNED_SOURCES:
        source = Path(relative)
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        originals[relative] = source.read_bytes()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Capability Test",
            "-c",
            "user.email=capability@example.invalid",
            "commit",
            "-qm",
            "pinned sources",
        ],
        cwd=repository,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, commit, originals


def test_cli_checks_every_pinned_source_before_creating_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit, originals = _pinned_repository(tmp_path)
    for module_name, relative in PINNED_SOURCES:
        monkeypatch.setitem(
            sys.modules,
            module_name,
            SimpleNamespace(__file__=str(repository / relative)),
        )

    for index, (_, relative) in enumerate(PINNED_SOURCES):
        target = repository / relative
        target.write_bytes(originals[relative] + b"\n# changed\n")
        output = tmp_path / f"claims-{index}.yaml"
        assert main(
            (
                "--repo-root",
                str(repository),
                "--source-commit",
                commit,
                "--products",
                f"{PRO_PRODUCT},{BAR_PRODUCT}",
                "--output",
                str(output),
            )
        ) == 2
        assert not output.exists()
        assert capsys.readouterr().err.strip() == "source_pin_blob_mismatch"
        target.write_bytes(originals[relative])


def test_cli_parse_failure_never_echoes_private_argv(
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_value = "/private/home/selian/secret-repository/source.py"
    assert main(("--oops", private_value)) == 2
    stderr = capsys.readouterr().err.strip()
    assert stderr == "schema_generator_command_line_invalid"
    assert private_value not in stderr


def test_generator_import_graph_excludes_transport_and_frozen_modules() -> None:
    module_path = Path("src/jebao_flow/capability_schema_generator.py")
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
    forbidden = (
        "jebao_flow.devices",
        "jebao_flow.protocol.control",
        "jebao_flow.protocol.session",
        "jebao_flow.read_only_collector",
        "jebao_flow.schedule_linkage_cli",
        "jebao_flow.schedule_flow_experiment_cli",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in forbidden
    )

    script = (
        "import sys; import jebao_flow.capability_schema_generator; "
        "print('\\n'.join(sorted(name for name in sys.modules "
        "if name.startswith('jebao_flow.devices') "
        "or name in {'jebao_flow.protocol.control', 'jebao_flow.protocol.session'})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "\n"
