"""Offline composition of immutable local-control qualification sets.

This module never creates qualification receipts and never contacts a device, broker, or
network.  It verifies already-installed content-addressed receipts and publishes one new
owner-only qualification-set source without replacing any existing input.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from jebao_flow.hardware_safety import qualification_directory, validate_hardware_safety_root
from jebao_flow.local_control.models import FreshInstallManifest, QualificationBinding
from jebao_flow.local_control.qualification import (
    QualificationStoreError,
    load_current_qualifications,
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)
from jebao_flow.operator_capture.paths import durable_private_path

_MAX_PRIVATE_INPUT_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_FUNCTION_RE = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
_PRODUCT_KEY_RE = re.compile(r"[0-9a-f]{32}\Z")
_PHYSICAL_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")


class QualificationSetMergeError(RuntimeError):
    """Qualification inputs could not produce the exact declared result surface."""


@dataclass(frozen=True, slots=True)
class ExpectedQualificationAddition:
    """One operator-declared surface addition, with the physical key kept private."""

    product_key: str
    function: str
    physical_identity_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if _PRODUCT_KEY_RE.fullmatch(self.product_key) is None:
            raise ValueError("expected qualification product key is invalid")
        if _FUNCTION_RE.fullmatch(self.function) is None:
            raise ValueError("expected qualification function is invalid")
        if _PHYSICAL_KEY_RE.fullmatch(self.physical_identity_key) is None:
            raise ValueError("expected physical identity key is invalid")

    def as_surface_key(self) -> tuple[str, str, str]:
        return (self.product_key, self.function, self.physical_identity_key)


@dataclass(frozen=True, slots=True)
class QualificationSetMergeResult:
    output_sha256: str
    binding_count: int
    receipt_count: int
    qualified_pair_count: int
    added_pair_count: int

    def public_dict(self) -> dict[str, object]:
        return {
            "output_sha256": self.output_sha256,
            "binding_count": self.binding_count,
            "receipt_count": self.receipt_count,
            "qualified_pair_count": self.qualified_pair_count,
            "added_pair_count": self.added_pair_count,
            "private_values_exposed": False,
        }


@dataclass(frozen=True, slots=True)
class _VerifiedSet:
    binding_keys: frozenset[tuple[str, str]]
    surface: frozenset[tuple[str, str, str]]
    receipt_count: int


class QualificationSetMerger:
    """Verify two qualification sets and install one exact immutable union."""

    def __init__(self, output_path: Path | str) -> None:
        validate_hardware_safety_root()
        destination = _durable_output_path(output_path)
        self._initialize(qualification_directory(), destination, ephemeral_for_test=False)

    def _initialize(
        self,
        receipt_root: Path,
        output_path: Path,
        *,
        ephemeral_for_test: bool,
    ) -> None:
        self.receipt_root = Path(receipt_root).absolute()
        self.output_path = Path(output_path).absolute()
        self._ephemeral_for_test = ephemeral_for_test
        if not self.output_path.name or self.output_path.name in {".", ".."}:
            raise QualificationSetMergeError("qualification-set output name is invalid")
        if self.output_path.is_symlink() or self.output_path.parent.is_symlink():
            raise QualificationSetMergeError("qualification-set output path must not be symlinked")
        self.output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require_private_directory(self.output_path.parent)

    @classmethod
    def for_test(
        cls,
        receipt_root: Path,
        output_path: Path,
    ) -> QualificationSetMerger:
        merger = cls.__new__(cls)
        merger._initialize(
            Path(receipt_root),
            Path(output_path),
            ephemeral_for_test=True,
        )
        return merger

    def merge(
        self,
        current: tuple[QualificationBinding, ...],
        candidate: tuple[QualificationBinding, ...],
        expected_additions: Iterable[ExpectedQualificationAddition],
    ) -> QualificationSetMergeResult:
        additions = tuple(expected_additions)
        expected_surface_additions = frozenset(item.as_surface_key() for item in additions)
        if not additions or len(expected_surface_additions) != len(additions):
            raise QualificationSetMergeError(
                "qualification-set additions must be non-empty and distinct"
            )

        current_verified = _verify_set(current, self.receipt_root)
        # Candidate verification is intentionally side-effect-only here. The exact authority
        # claim is derived from the merged result below rather than trusting candidate shape.
        _verify_set(candidate, self.receipt_root)
        if current_verified.surface & expected_surface_additions:
            raise QualificationSetMergeError("qualification-set additions are already current")

        merged = _merge_bindings(current, candidate)
        merged_verified = _verify_set(merged, self.receipt_root)
        expected_binding_keys = current_verified.binding_keys | frozenset(
            (item.product_key, item.function) for item in additions
        )
        if merged_verified.binding_keys != expected_binding_keys:
            raise QualificationSetMergeError(
                "qualification-set binding keys differ from the declared union"
            )
        expected_surface = current_verified.surface | expected_surface_additions
        if merged_verified.surface != expected_surface:
            raise QualificationSetMergeError(
                "qualification-set surface differs from the declared union"
            )
        _require_current_bindings_preserved(current, merged)

        payload = qualification_set_bytes(merged)
        _install_or_verify_private_file(self.output_path.parent, self.output_path.name, payload)
        loaded = load_current_qualifications(
            self.output_path,
            _allow_ephemeral_for_test=self._ephemeral_for_test,
        )
        if loaded != merged or _read_private_path(self.output_path) != payload:
            raise QualificationSetMergeError("qualification-set output did not reload exactly")
        reloaded_verified = _verify_set(loaded, self.receipt_root)
        if (
            reloaded_verified.binding_keys != expected_binding_keys
            or reloaded_verified.surface != expected_surface
        ):
            raise QualificationSetMergeError(
                "qualification-set output verification changed the declared union"
            )
        return QualificationSetMergeResult(
            output_sha256=hashlib.sha256(payload).hexdigest(),
            binding_count=len(merged),
            receipt_count=reloaded_verified.receipt_count,
            qualified_pair_count=len(reloaded_verified.surface),
            added_pair_count=len(expected_surface_additions),
        )


def load_current_manifest(
    path: Path | str,
    *,
    _allow_ephemeral_for_test: bool = False,
) -> FreshInstallManifest:
    """Read one current owner-only manifest without rederiving or replacing it."""

    raw = Path(path)
    if raw.is_symlink() or raw.parent.is_symlink():
        raise QualificationSetMergeError("current manifest path must not be symlinked")
    source = (
        raw.absolute()
        if _allow_ephemeral_for_test
        else durable_private_path(raw, description="local-control install manifest")
    )
    payload = _read_private_path(source)
    if len(payload) > _MAX_PRIVATE_INPUT_BYTES:
        raise QualificationSetMergeError("current manifest exceeds its size bound")
    try:
        return FreshInstallManifest.model_validate_json(payload)
    except ValueError as error:
        raise QualificationSetMergeError("current manifest is invalid") from error


def expected_additions_from_manifest(
    manifest: FreshInstallManifest,
    requested: Iterable[tuple[str, str]],
) -> tuple[ExpectedQualificationAddition, ...]:
    """Resolve private logical member requests to exact current physical bindings."""

    requests = tuple(requested)
    if not requests:
        raise QualificationSetMergeError("at least one qualification addition is required")
    additions: list[ExpectedQualificationAddition] = []
    for logical_member, function in requests:
        if _FUNCTION_RE.fullmatch(function) is None:
            raise QualificationSetMergeError("qualification addition function is invalid")
        matches = tuple(
            binding for binding in manifest.devices if binding.logical_member == logical_member
        )
        if len(matches) != 1:
            raise QualificationSetMergeError(
                "qualification addition is not exactly resolved by the current manifest"
            )
        binding = matches[0]
        additions.append(
            ExpectedQualificationAddition(
                product_key=binding.product_key,
                function=function,
                physical_identity_key=binding.physical_identity_key,
            )
        )
    if len({item.as_surface_key() for item in additions}) != len(additions):
        raise QualificationSetMergeError("qualification additions resolve to duplicates")
    return tuple(additions)


def qualification_set_bytes(bindings: tuple[QualificationBinding, ...]) -> bytes:
    """Return the deterministic source bytes consumed by a later Compose preparation."""

    canonical = _canonical_bindings(bindings)
    payload = {"qualifications": [item.model_dump(mode="json") for item in canonical]}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii") + b"\n"
    if len(encoded) > _MAX_OUTPUT_BYTES:
        raise QualificationSetMergeError("qualification-set output exceeds its size bound")
    return encoded


def _merge_bindings(
    current: tuple[QualificationBinding, ...],
    candidate: tuple[QualificationBinding, ...],
) -> tuple[QualificationBinding, ...]:
    current_by_key = _binding_map(current)
    candidate_by_key = _binding_map(candidate)
    merged: list[QualificationBinding] = []
    for key in sorted(set(current_by_key) | set(candidate_by_key)):
        left = current_by_key.get(key)
        right = candidate_by_key.get(key)
        if left is not None and right is not None:
            if _evidence_fields(left) != _evidence_fields(right):
                raise QualificationSetMergeError(
                    "qualification bindings disagree on version or content evidence"
                )
            receipt_sha256 = tuple(sorted(set(left.receipt_sha256) | set(right.receipt_sha256)))
            merged.append(
                QualificationBinding.model_validate(
                    {**left.model_dump(mode="python"), "receipt_sha256": receipt_sha256}
                )
            )
        else:
            merged.append(_canonical_binding(left if left is not None else right))
    return tuple(merged)


def _canonical_bindings(
    bindings: tuple[QualificationBinding, ...],
) -> tuple[QualificationBinding, ...]:
    by_key = _binding_map(bindings)
    return tuple(_canonical_binding(by_key[key]) for key in sorted(by_key))


def _canonical_binding(binding: QualificationBinding | None) -> QualificationBinding:
    if binding is None:
        raise AssertionError("canonical qualification binding is missing")
    return QualificationBinding.model_validate(
        {
            **binding.model_dump(mode="python"),
            "receipt_sha256": tuple(sorted(binding.receipt_sha256)),
        }
    )


def _binding_map(
    bindings: tuple[QualificationBinding, ...],
) -> dict[tuple[str, str], QualificationBinding]:
    result = {(item.product_key, item.function): item for item in bindings}
    if len(result) != len(bindings):
        raise QualificationSetMergeError(
            "qualification bindings must be unique by product and function"
        )
    return result


def _evidence_fields(binding: QualificationBinding) -> tuple[str, ...]:
    return (
        binding.product_key,
        binding.function,
        binding.app_version,
        binding.app_catalog_sha256,
        binding.template_sha256,
        binding.schema_sha256,
        binding.firmware_version,
    )


def _verify_set(
    bindings: tuple[QualificationBinding, ...],
    receipt_root: Path,
) -> _VerifiedSet:
    by_key = _binding_map(bindings)
    surface: set[tuple[str, str, str]] = set()
    request_ids: set[str] = set()
    receipt_count = 0
    try:
        for key in sorted(by_key):
            binding = by_key[key]
            receipts = verify_qualification_receipts(
                binding,
                _receipt_root_for_test=receipt_root,
            )
            qualified_keys = qualified_physical_identity_keys(
                binding,
                _receipt_root_for_test=receipt_root,
            )
            for receipt in receipts:
                if receipt.request_id in request_ids:
                    raise QualificationSetMergeError(
                        "qualification-set request ids must be globally distinct"
                    )
                request_ids.add(receipt.request_id)
            receipt_count += len(receipts)
            surface.update((binding.product_key, binding.function, item) for item in qualified_keys)
    except QualificationStoreError as error:
        raise QualificationSetMergeError("qualification receipt verification failed") from error
    return _VerifiedSet(
        binding_keys=frozenset(by_key),
        surface=frozenset(surface),
        receipt_count=receipt_count,
    )


def _require_current_bindings_preserved(
    current: tuple[QualificationBinding, ...],
    merged: tuple[QualificationBinding, ...],
) -> None:
    merged_by_key = _binding_map(merged)
    for key, binding in _binding_map(current).items():
        replacement = merged_by_key.get(key)
        if replacement is None or _evidence_fields(replacement) != _evidence_fields(binding):
            raise QualificationSetMergeError("current qualification evidence was not preserved")
        if not set(binding.receipt_sha256).issubset(replacement.receipt_sha256):
            raise QualificationSetMergeError("current qualification receipts were not preserved")


def _durable_output_path(path: Path | str) -> Path:
    raw = Path(path)
    if raw.is_symlink() or raw.parent.is_symlink():
        raise QualificationSetMergeError("qualification-set output path must not be symlinked")
    return durable_private_path(raw, description="local-control qualification-set output")


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QualificationSetMergeError(
            "qualification-set output directory is unavailable"
        ) from error
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise QualificationSetMergeError(
            "qualification-set output directory metadata is unsafe"
        )


def _open_private_directory(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise QualificationSetMergeError("O_NOFOLLOW is required for qualification sets")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise QualificationSetMergeError(
                "qualification-set output directory changed while opening"
            )
        return descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _install_or_verify_private_file(directory: Path, name: str, payload: bytes) -> None:
    if not name or "/" in name or name in {".", ".."}:
        raise QualificationSetMergeError("qualification-set output name is invalid")
    if len(payload) > _MAX_OUTPUT_BYTES:
        raise QualificationSetMergeError("qualification-set output exceeds its size bound")
    directory_descriptor = _open_private_directory(directory)
    temporary = f".qualification-set-{secrets.token_hex(16)}"
    temporary_created = False
    try:
        try:
            existing = _read_private_at(directory_descriptor, name, fsync_after=True)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if existing != payload:
                raise QualificationSetMergeError(
                    "existing qualification-set output differs from expected bytes"
                )
            os.fsync(directory_descriptor)
            return

        file_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_created = True
        try:
            os.fchmod(file_descriptor, 0o600)
            _write_all(file_descriptor, payload)
            os.fsync(file_descriptor)
            _require_private_file_metadata(os.fstat(file_descriptor))
        finally:
            os.close(file_descriptor)
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            # A concurrent publisher is safe only because the exact reload below rejects any
            # bytes other than this already-verified payload.
            pass
        else:
            os.fsync(directory_descriptor)
        finally:
            os.unlink(temporary, dir_fd=directory_descriptor)
            temporary_created = False
            os.fsync(directory_descriptor)
        if _read_private_at(directory_descriptor, name, fsync_after=True) != payload:
            raise QualificationSetMergeError("qualification-set output did not reload exactly")
        os.fsync(directory_descriptor)
    finally:
        try:
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=directory_descriptor)
                    os.fsync(directory_descriptor)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory_descriptor)


def _read_private_path(path: Path) -> bytes:
    descriptor = _open_private_directory(path.parent)
    try:
        return _read_private_at(descriptor, path.name, fsync_after=False)
    finally:
        os.close(descriptor)


def _read_private_at(directory_descriptor: int, name: str, *, fsync_after: bool) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=directory_descriptor,
    )
    try:
        opened = os.fstat(descriptor)
        _require_private_file_metadata(opened)
        if opened.st_size > _MAX_OUTPUT_BYTES:
            raise QualificationSetMergeError("qualification-set artifact exceeds its size bound")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
            if len(payload) > _MAX_OUTPUT_BYTES:
                raise QualificationSetMergeError(
                    "qualification-set artifact exceeds its size bound"
                )
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        _require_private_file_metadata(named)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise QualificationSetMergeError(
                "qualification-set artifact changed while reading"
            )
        if fsync_after:
            os.fsync(descriptor)
        return bytes(payload)
    finally:
        os.close(descriptor)


def _require_private_file_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise QualificationSetMergeError("qualification-set artifact metadata is unsafe")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short qualification-set write")
        view = view[written:]


__all__ = [
    "ExpectedQualificationAddition",
    "QualificationSetMergeError",
    "QualificationSetMergeResult",
    "QualificationSetMerger",
    "expected_additions_from_manifest",
    "load_current_manifest",
    "qualification_set_bytes",
]
