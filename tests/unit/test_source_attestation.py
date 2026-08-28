from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from jebao_flow import source_attestation
from jebao_flow.source_attestation import (
    CollectorSourceAttestation,
    SourceAttestationError,
    attest_collector_source_tree,
    validate_collector_source_attestation,
)


def _clean_runtime_repository(
    tmp_path: Path,
    monkeypatch,
) -> tuple[Path, str, dict[str, bytes]]:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    root = tmp_path / "collector-source"
    root.mkdir()
    commit = "a" * 40
    head_blobs: dict[str, bytes] = {}
    for index, relative_path in enumerate(source_attestation._TRACKED_SOURCE_FILES):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"# source {index}\n".encode()
        path.write_bytes(payload)
        head_blobs[relative_path] = payload

    for module_name, relative_path in source_attestation._RUNTIME_MODULE_SOURCES:
        path = root / relative_path
        specification = importlib.util.spec_from_file_location(module_name, path)
        assert specification is not None and specification.loader is not None
        module = ModuleType(module_name)
        module.__file__ = str(path)
        module.__spec__ = specification
        module.__loader__ = specification.loader
        monkeypatch.setitem(sys.modules, module_name, module)

    def git(command, **_kwargs):
        arguments = command[1:]
        if arguments[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(stdout=f"{root}\n".encode())
        if "rev-parse" in arguments:
            return SimpleNamespace(stdout=f"{commit}\n".encode())
        if "status" in arguments or "ls-files" in arguments:
            return SimpleNamespace(stdout=b"")
        if "cat-file" in arguments:
            relative_path = arguments[-1].split(":", 1)[1]
            return SimpleNamespace(stdout=head_blobs[relative_path])
        raise AssertionError(arguments)

    monkeypatch.setattr(source_attestation.subprocess, "run", git)
    return root, commit, head_blobs


def test_attestation_binds_loaded_paths_and_exact_head_blobs(tmp_path: Path, monkeypatch) -> None:
    root, commit, _head_blobs = _clean_runtime_repository(tmp_path, monkeypatch)

    attestation = attest_collector_source_tree(commit, cwd=root)

    assert attestation.commit_sha == commit
    assert len(attestation.runtime_source_digest_sha256) == 64
    assert (
        validate_collector_source_attestation(attestation, expected_commit=commit)
        is attestation
    )
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        CollectorSourceAttestation(commit, "a" * 64)
    with pytest.raises(SourceAttestationError, match="collector_source_attestation_invalid"):
        validate_collector_source_attestation(object(), expected_commit=commit)

    attestation.runtime_source_digest_sha256 = "b" * 64
    with pytest.raises(SourceAttestationError, match="collector_source_attestation_invalid"):
        validate_collector_source_attestation(attestation, expected_commit=commit)


def test_attestation_rejects_stale_ignored_virtualenv_module(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, commit, _head_blobs = _clean_runtime_repository(tmp_path, monkeypatch)
    stale_path = root / ".venv/lib/python3.12/site-packages/jebao_flow/read_only_collector_cli.py"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text("# stale ignored copy\n", encoding="utf-8")
    stale_specification = importlib.util.spec_from_file_location(
        "jebao_flow.read_only_collector_cli",
        stale_path,
    )
    assert stale_specification is not None and stale_specification.loader is not None
    stale_module = ModuleType("jebao_flow.read_only_collector_cli")
    stale_module.__file__ = str(stale_path)
    stale_module.__spec__ = stale_specification
    stale_module.__loader__ = stale_specification.loader
    monkeypatch.setitem(sys.modules, "jebao_flow.read_only_collector_cli", stale_module)
    with pytest.raises(
        SourceAttestationError,
        match="collector_runtime_source_path_mismatch",
    ):
        attest_collector_source_tree(commit, cwd=root)


def test_attestation_compares_bytes_even_when_git_status_is_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, commit, _head_blobs = _clean_runtime_repository(tmp_path, monkeypatch)
    relative_path = "src/jebao_flow/protocol/codec.py"
    (root / relative_path).write_text("# changed but hidden from status\n", encoding="utf-8")

    with pytest.raises(
        SourceAttestationError,
        match="collector_runtime_source_blob_mismatch",
    ):
        attest_collector_source_tree(commit, cwd=root)


def test_attestation_is_revalidated_after_issuance(tmp_path: Path, monkeypatch) -> None:
    root, commit, _head_blobs = _clean_runtime_repository(tmp_path, monkeypatch)
    attestation = attest_collector_source_tree(commit, cwd=root)
    relative_path = "src/jebao_flow/read_only_collector.py"
    (root / relative_path).write_text("# changed after issuance\n", encoding="utf-8")

    with pytest.raises(
        SourceAttestationError,
        match="collector_runtime_source_blob_mismatch",
    ):
        validate_collector_source_attestation(attestation, expected_commit=commit)


def test_attestation_rejects_existing_cached_bytecode(tmp_path: Path, monkeypatch) -> None:
    root, commit, _head_blobs = _clean_runtime_repository(tmp_path, monkeypatch)
    module = sys.modules["jebao_flow.read_only_collector_cli"]
    cached = root / "external-cache/read_only_collector_cli.pyc"
    cached.parent.mkdir()
    cached.write_bytes(b"stale-bytecode")
    module.__cached__ = str(cached)

    with pytest.raises(
        SourceAttestationError,
        match="collector_runtime_cached_bytecode_present",
    ):
        attest_collector_source_tree(commit, cwd=root)


def test_attestation_requires_bytecode_writes_disabled(tmp_path: Path, monkeypatch) -> None:
    root, commit, _head_blobs = _clean_runtime_repository(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    with pytest.raises(
        SourceAttestationError,
        match="collector_runtime_bytecode_writes_enabled",
    ):
        attest_collector_source_tree(commit, cwd=root)
