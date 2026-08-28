"""Runtime provenance attestation for the first write-free collector.

The pilot intentionally runs only from an exact, clean source checkout.  A clean
repository alone is insufficient: an ignored in-repository virtual environment
can contain stale copies of the same modules.  This module binds both the loaded
module paths and the bytes at those paths to the requested ``HEAD`` commit.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

_RUNTIME_MODULE_SOURCES = (
    ("jebao_flow.read_only_collector_cli", "src/jebao_flow/read_only_collector_cli.py"),
    ("jebao_flow.read_only_collector", "src/jebao_flow/read_only_collector.py"),
    ("jebao_flow.protocol.session", "src/jebao_flow/protocol/session.py"),
    ("jebao_flow.protocol.codec", "src/jebao_flow/protocol/codec.py"),
    ("jebao_flow.physical_identity", "src/jebao_flow/physical_identity.py"),
    ("jebao_flow.source_attestation", "src/jebao_flow/source_attestation.py"),
)
_TRACKED_SOURCE_FILES = (
    "pyproject.toml",
    "src/jebao_flow/protocol/__init__.py",
    "src/jebao_flow/protocol/control_session.py",
    *tuple(relative_path for _, relative_path in _RUNTIME_MODULE_SOURCES),
)
_ISSUANCE_KEY = object()
_ATTESTATION_LOCK = threading.RLock()


class SourceAttestationError(RuntimeError):
    """Privacy-safe runtime provenance failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CollectorSourceAttestation:
    """Opaque capability issued only after a complete runtime source verification."""

    __slots__ = ("commit_sha", "runtime_source_digest_sha256", "__weakref__")

    def __init__(
        self,
        commit_sha: str,
        runtime_source_digest_sha256: str,
        *,
        _issuance_key: object | None = None,
    ) -> None:
        if _issuance_key is not _ISSUANCE_KEY:
            raise TypeError("CollectorSourceAttestation cannot be constructed directly")
        self.commit_sha = commit_sha
        self.runtime_source_digest_sha256 = runtime_source_digest_sha256


@dataclass(frozen=True, slots=True)
class _RuntimeSnapshot:
    repository_root: Path
    commit_sha: str
    source_digests: tuple[tuple[str, str], ...]
    runtime_source_digest_sha256: str


_ISSUED_ATTESTATIONS: weakref.WeakKeyDictionary[
    CollectorSourceAttestation, _RuntimeSnapshot
] = weakref.WeakKeyDictionary()


def _valid_commit_sha(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _run_git(*arguments: str, cwd: Path) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SourceAttestationError("collector_repository_unavailable") from error
    return result.stdout


def _loaded_module(canonical_name: str) -> ModuleType:
    candidates: dict[int, ModuleType] = {}
    for name, module in tuple(sys.modules.items()):
        if not isinstance(module, ModuleType):
            continue
        specification = getattr(module, "__spec__", None)
        if name == canonical_name or getattr(specification, "name", None) == canonical_name:
            candidates[id(module)] = module
    if not candidates:
        raise SourceAttestationError("collector_runtime_module_missing")
    if len(candidates) != 1:
        raise SourceAttestationError("collector_runtime_module_ambiguous")
    return next(iter(candidates.values()))


def _reported_source_path(module: ModuleType) -> Path:
    source_file = getattr(module, "__file__", None)
    specification = getattr(module, "__spec__", None)
    origin = getattr(specification, "origin", None)
    if not isinstance(source_file, str) or not isinstance(origin, str):
        raise SourceAttestationError("collector_runtime_source_path_missing")
    source_path = Path(os.path.abspath(source_file))
    origin_path = Path(os.path.abspath(origin))
    if source_path != origin_path:
        raise SourceAttestationError("collector_runtime_source_path_mismatch")
    loader_path = getattr(getattr(module, "__loader__", None), "path", None)
    if loader_path is not None and Path(os.path.abspath(loader_path)) != source_path:
        raise SourceAttestationError("collector_runtime_source_path_mismatch")
    cached_path = getattr(module, "__cached__", None)
    if cached_path is not None:
        if not isinstance(cached_path, str):
            raise SourceAttestationError("collector_runtime_cached_bytecode_invalid")
        try:
            cached_exists = os.path.lexists(Path(os.path.abspath(cached_path)))
        except OSError as error:
            raise SourceAttestationError(
                "collector_runtime_cached_bytecode_invalid"
            ) from error
        if cached_exists:
            raise SourceAttestationError("collector_runtime_cached_bytecode_present")
    return source_path


def _verify_runtime(expected_commit: str, *, cwd: Path | None) -> _RuntimeSnapshot:
    if not _valid_commit_sha(expected_commit):
        raise SourceAttestationError("collector_commit_sha_invalid")
    if sys.dont_write_bytecode is not True:
        raise SourceAttestationError("collector_runtime_bytecode_writes_enabled")

    cli_module = _loaded_module("jebao_flow.read_only_collector_cli")
    base = cwd if cwd is not None else _reported_source_path(cli_module).parent
    root_payload = _run_git("rev-parse", "--show-toplevel", cwd=base)
    try:
        root = Path(root_payload.decode("utf-8").strip()).resolve(strict=True)
    except (OSError, UnicodeDecodeError) as error:
        raise SourceAttestationError("collector_repository_unavailable") from error
    head = _run_git(
        "-C",
        str(root),
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        cwd=root,
    ).decode("ascii").strip()
    if head != expected_commit:
        raise SourceAttestationError("collector_commit_mismatch")
    if _run_git(
        "-C",
        str(root),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        cwd=root,
    ):
        raise SourceAttestationError("collector_tree_not_clean")
    _run_git(
        "-C",
        str(root),
        "ls-files",
        "--error-unmatch",
        "--",
        *_TRACKED_SOURCE_FILES,
        cwd=root,
    )

    runtime_paths = {
        relative_path: _reported_source_path(_loaded_module(module_name))
        for module_name, relative_path in _RUNTIME_MODULE_SOURCES
    }
    source_digests: list[tuple[str, str]] = []
    for relative_path in _TRACKED_SOURCE_FILES:
        expected_path = root / relative_path
        if relative_path in runtime_paths and runtime_paths[relative_path] != expected_path:
            raise SourceAttestationError("collector_runtime_source_path_mismatch")
        try:
            metadata = expected_path.lstat()
            source_bytes = expected_path.read_bytes()
        except OSError as error:
            raise SourceAttestationError("collector_runtime_source_file_invalid") from error
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SourceAttestationError("collector_runtime_source_file_invalid")
        head_bytes = _run_git(
            "-C",
            str(root),
            "cat-file",
            "blob",
            f"{expected_commit}:{relative_path}",
            cwd=root,
        )
        if source_bytes != head_bytes:
            raise SourceAttestationError("collector_runtime_source_blob_mismatch")
        source_digests.append((relative_path, hashlib.sha256(source_bytes).hexdigest()))

    # Recheck cleanliness after reading every source to narrow the verification/write race.
    if _run_git(
        "-C",
        str(root),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        cwd=root,
    ):
        raise SourceAttestationError("collector_tree_not_clean")
    digest_payload = json.dumps(
        source_digests,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return _RuntimeSnapshot(
        repository_root=root,
        commit_sha=expected_commit,
        source_digests=tuple(source_digests),
        runtime_source_digest_sha256=hashlib.sha256(digest_payload).hexdigest(),
    )


def attest_collector_source_tree(
    expected_commit: str,
    *,
    cwd: Path | None = None,
) -> CollectorSourceAttestation:
    """Verify and mint an opaque capability for the exact loaded collector sources."""

    snapshot = _verify_runtime(expected_commit, cwd=cwd)
    attestation = CollectorSourceAttestation(
        commit_sha=snapshot.commit_sha,
        runtime_source_digest_sha256=snapshot.runtime_source_digest_sha256,
        _issuance_key=_ISSUANCE_KEY,
    )
    with _ATTESTATION_LOCK:
        _ISSUED_ATTESTATIONS[attestation] = snapshot
    return attestation


def validate_collector_source_attestation(
    attestation: object,
    *,
    expected_commit: str,
) -> CollectorSourceAttestation:
    """Revalidate one issued capability immediately before plan or network work."""

    if not isinstance(attestation, CollectorSourceAttestation):
        raise SourceAttestationError("collector_source_attestation_invalid")
    with _ATTESTATION_LOCK:
        snapshot = _ISSUED_ATTESTATIONS.get(attestation)
    if (
        snapshot is None
        or snapshot.commit_sha != expected_commit
        or attestation.commit_sha != snapshot.commit_sha
        or attestation.runtime_source_digest_sha256
        != snapshot.runtime_source_digest_sha256
    ):
        raise SourceAttestationError("collector_source_attestation_invalid")
    current = _verify_runtime(expected_commit, cwd=snapshot.repository_root)
    if current != snapshot:
        raise SourceAttestationError("collector_source_attestation_stale")
    return attestation


__all__ = [
    "CollectorSourceAttestation",
    "SourceAttestationError",
    "attest_collector_source_tree",
    "validate_collector_source_attestation",
]
