"""Fresh, private installation-variable preparation for the packet observatory.

The preparer never merges an earlier manifest. Runtime transport values are re-observed for every
invocation and are explicitly separated from stable device/capability binding rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from jebao_flow.operator_capture.paths import durable_private_path
from jebao_flow.protocol.codec import MAGIC
from jebao_flow.protocol.session import (
    CONTROL_ACTION,
    DEFAULT_CONTROL_PORT,
    READ_STATE_ACTION,
    STATE_REPLY_ACTION,
    STATE_REPORT_ACTION,
)

_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class CaptureInstallMode(StrEnum):
    OFFLINE_PCAP = "offline_pcap"
    PASSIVE_MIRROR = "passive_mirror"


class InstallPreparationError(RuntimeError):
    """Current installation variables cannot be prepared without guessing."""


@dataclass(frozen=True, slots=True)
class PreparedInstall:
    generation: str
    manifest_sha256: str
    capture_mode: CaptureInstallMode
    mirror_interface: str | None

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "prepared",
            "generation": self.generation,
            "manifest_sha256": self.manifest_sha256,
            "capture_mode": self.capture_mode.value,
            "mirror_interface": self.mirror_interface,
            "previous_values_reused": False,
        }


def current_interface_names() -> tuple[str, ...]:
    """Return current interface names only; addresses and process state are never collected."""

    try:
        names = tuple(name for _index, name in socket.if_nameindex() if name not in {"lo", "lo0"})
    except OSError as error:
        raise InstallPreparationError("network interfaces could not be observed") from error
    return _normalize_current_interfaces(names)


def prepare_fresh_install(
    *,
    manifest_path: Path | str,
    capture_root: Path | str,
    capture_mode: CaptureInstallMode,
    mirror_interface: str | None = None,
    observed_interfaces: tuple[str, ...] | None = None,
) -> PreparedInstall:
    """Replace a private manifest from fresh inputs without reading or merging its old bytes."""

    if not isinstance(capture_mode, CaptureInstallMode):
        raise TypeError("capture install mode must be a CaptureInstallMode")
    manifest = Path(manifest_path)
    root = Path(capture_root)
    try:
        manifest = durable_private_path(manifest, "install manifest")
        root = durable_private_path(root, "operator capture root")
    except ValueError as error:
        raise InstallPreparationError(str(error)) from error
    if manifest == root or root in manifest.parents:
        raise InstallPreparationError("install manifest must not be stored inside the capture root")

    if capture_mode is CaptureInstallMode.OFFLINE_PCAP:
        interfaces: tuple[str, ...] = ()
    else:
        interfaces = (
            observed_interfaces if observed_interfaces is not None else current_interface_names()
        )
    selected_interface = _select_current_interface(
        capture_mode,
        mirror_interface=mirror_interface,
        observed_interfaces=interfaces,
    )
    _ensure_private_directory(manifest.parent)
    _ensure_private_directory(root)

    generation = secrets.token_hex(16)
    prepared_utc = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    content = _canonical_json(
        {
            "schema_version": 1,
            "generation": generation,
            "prepared_utc": prepared_utc,
            "language": "en",
            "capture": {
                "mode": {
                    "value": capture_mode.value,
                    "method": "fresh_install_selection",
                    "rederived_at": prepared_utc,
                },
                "private_root": {
                    "value": str(root),
                    "method": "fresh_install_target",
                    "rederived_at": prepared_utc,
                },
                "mirror_interface": {
                    "value": selected_interface,
                    "method": (
                        "current_interface_observation" if selected_interface is not None else None
                    ),
                    "rederived_at": prepared_utc,
                },
            },
            "stable_binding": {
                "capability": "product_key_and_verified_wire_layout",
                "device": "physical_identity_key",
                "role": "operator_assigned_logical_role",
                "content": "sha256",
            },
            "protocol_constants": {
                "control_port": DEFAULT_CONTROL_PORT,
                "frame_magic": MAGIC.hex(),
                "actions": {
                    "control": CONTROL_ACTION,
                    "read_state": READ_STATE_ACTION,
                    "state_reply": STATE_REPLY_ACTION,
                    "state_report": STATE_REPORT_ACTION,
                },
                "method": "installed_source_constants",
            },
            "volatile_values_are_not_binding": [
                "process_id",
                "ip_address",
                "ephemeral_source_port",
                "operator_ui_listen_port",
                "container_id",
                "capture_session_id",
                "cache_path",
                "file_mtime",
                "host_name",
                "template_cache_directory_name",
            ],
            "previous_values_reused": False,
        }
    )
    _atomic_replace_private(manifest, content)
    return PreparedInstall(
        generation=generation,
        manifest_sha256=hashlib.sha256(content).hexdigest(),
        capture_mode=capture_mode,
        mirror_interface=selected_interface,
    )


def _select_current_interface(
    capture_mode: CaptureInstallMode,
    *,
    mirror_interface: str | None,
    observed_interfaces: tuple[str, ...],
) -> str | None:
    if capture_mode is CaptureInstallMode.OFFLINE_PCAP:
        if mirror_interface is not None:
            raise InstallPreparationError("offline PCAP installation does not use an interface")
        return None
    unique = _normalize_current_interfaces(observed_interfaces)
    if mirror_interface is not None:
        if mirror_interface not in unique:
            raise InstallPreparationError("selected mirror interface is not present now")
        return mirror_interface
    if len(unique) != 1:
        raise InstallPreparationError(
            "mirror interface is ambiguous; select one from the current install-time observation"
        )
    return unique[0]


def _normalize_current_interfaces(observed_interfaces: tuple[str, ...]) -> tuple[str, ...]:
    names: set[str] = set()
    for name in observed_interfaces:
        if (
            not isinstance(name, str)
            or not name
            or not name.isascii()
            or any(ord(character) < 0x21 or character.isspace() for character in name)
        ):
            raise InstallPreparationError("a network interface has an unsafe public name")
        names.add(name)
    return tuple(sorted(names))


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
    except OSError as error:
        raise InstallPreparationError("private installation directory is unavailable") from error
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE
        ):
            raise InstallPreparationError(
                "private installation directory must be owner-only mode 0700"
            )
    finally:
        os.close(fd)


def _atomic_replace_private(path: Path, content: bytes) -> None:
    parent_fd = -1
    temporary_fd = -1
    temporary_name = f".{path.name}.{secrets.token_hex(12)}.tmp"
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            _PRIVATE_FILE_MODE,
            dir_fd=parent_fd,
        )
        _write_all(temporary_fd, content)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        installed_fd = os.open(
            path.name,
            os.O_RDONLY | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(installed_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
                or metadata.st_nlink != 1
            ):
                raise InstallPreparationError("fresh install manifest metadata is unsafe")
            if _read_all(installed_fd, len(content) + 1) != content:
                raise InstallPreparationError("fresh install manifest did not reload exactly")
        finally:
            os.close(installed_fd)
    except InstallPreparationError:
        raise
    except OSError as error:
        raise InstallPreparationError("fresh install manifest could not be committed") from error
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if parent_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
            os.close(parent_fd)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise InstallPreparationError("fresh install manifest write made no progress")
        view = view[written:]


def _read_all(fd: int, maximum: int) -> bytes:
    metadata = os.fstat(fd)
    if metadata.st_size > maximum:
        raise InstallPreparationError("fresh install manifest exceeds its expected size")
    chunks: list[bytes] = []
    remaining = maximum
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


__all__ = [
    "CaptureInstallMode",
    "InstallPreparationError",
    "PreparedInstall",
    "current_interface_names",
    "prepare_fresh_install",
]
