"""Fresh, replacement-only installation manifest generation."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

import yaml

from jebao_flow.config import AppConfig
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
)
from jebao_flow.operator_capture import schedule_model
from jebao_flow.operator_capture.app_catalog import public_app_catalog
from jebao_flow.operator_capture.paths import durable_private_path
from jebao_flow.physical_identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    physical_identity_key,
)
from jebao_flow.protocol import codec, profiles, schedule

_MAX_PRIVATE_CONFIG_BYTES = 1024 * 1024


def build_fresh_install_manifest(
    config: AppConfig,
    *,
    role_attested_by: str,
    role_attested_at: datetime,
    template_sha256: tuple[str, ...] = (),
    now: datetime | None = None,
) -> FreshInstallManifest:
    """Derive every install value from current inputs without reading an old manifest."""

    rederived_at = now or datetime.now(UTC)
    role_by_device = _current_roles(config)
    devices: list[LocalDeviceBinding] = []
    for device in config.devices:
        identity = device.identity
        if (
            identity is None
            or identity.device_id is None
            or identity.mac_address is None
            or device.product_key is None
        ):
            raise ValueError(
                f"device {device.id!r} requires current vendor id, MAC and product key"
            )
        fingerprint_source = device.model_dump(
            mode="json", exclude={"address", "discovery", "name"}
        )
        binding = PhysicalDeviceBinding.from_identifiers(
            vendor_device_id=identity.device_id,
            mac_address=identity.mac_address,
            product_key=device.product_key,
            config_fingerprint=configuration_fingerprint(fingerprint_source),
        )
        devices.append(
            LocalDeviceBinding(
                logical_member=device.id,
                product_key=device.product_key,
                physical_identity_key=physical_identity_key(binding),
                logical_role=role_by_device.get(device.id, "standalone"),
                role_attested_by=role_attested_by,
                role_attested_at=role_attested_at,
            )
        )

    app_catalog_bytes = _canonical_json(public_app_catalog())
    protocol_digest = current_protocol_schema_sha256()
    return FreshInstallManifest(
        generation=secrets.token_hex(16),
        rederived_at=rederived_at,
        devices=tuple(devices),
        content=InstallContentBindings(
            app_catalog_sha256=hashlib.sha256(app_catalog_bytes).hexdigest(),
            protocol_schema_sha256=protocol_digest,
            template_sha256=template_sha256,
        ),
    )


def current_protocol_schema_sha256() -> str:
    """Hash the installed encoder, decoder and local composition sources."""

    if schedule.__file__ is None:
        raise RuntimeError("installed schedule module has no content path")
    schedule_path = Path(schedule.__file__)
    local_control_path = Path(__file__).parent
    return _combined_file_digest(
        (
            codec.__file__,
            str(schedule_path.with_name("control.py")),
            profiles.__file__,
            schedule.__file__,
            str(schedule_path.with_name("schedule_wire.py")),
            str(schedule_path.with_name("schema.py")),
            str(local_control_path / "device_writer.py"),
            str(local_control_path / "bar_timer.py"),
            str(local_control_path / "schedule.py"),
            str(local_control_path / "wavemaker.py"),
            str(local_control_path / "pump_preview.py"),
            schedule_model.__file__,
        )
    )


def load_current_private_config(path: Path | str) -> AppConfig:
    """Load the current owner-only config without consulting an install manifest."""

    source = durable_private_path(path, description="local-control private config")
    payload = _read_current_private_file(source, maximum=_MAX_PRIVATE_CONFIG_BYTES)
    try:
        decoded = yaml.safe_load(payload)
    except yaml.YAMLError as error:
        raise ValueError("current private config is invalid YAML") from error
    if not isinstance(decoded, dict):
        raise ValueError("current private config root must be a mapping")
    return AppConfig.model_validate(decoded)


def hash_current_template_contents(roots: tuple[Path | str, ...]) -> tuple[str, ...]:
    """Hash current template bytes; paths and mtimes never become mapping identity."""

    digests: set[str] = set()
    for raw_root in roots:
        root = durable_private_path(raw_root, description="app template audit root")
        if not root.is_dir():
            raise ValueError("app template audit root must be a current directory")
        candidates = tuple(sorted(root.rglob("index.js")))
        if not candidates:
            raise ValueError("app template audit root contains no current index.js")
        for candidate in candidates:
            payload = _read_current_regular_file(candidate, maximum=16 * 1024 * 1024)
            digests.add(hashlib.sha256(payload).hexdigest())
    return tuple(sorted(digests))


def replace_install_manifest(path: Path, manifest: FreshInstallManifest) -> None:
    """Atomically replace the destination with one wholly fresh manifest.

    Existing bytes are deliberately not parsed, merged or used as defaults. They can neither
    veto a current installation nor leak stale values into it.
    """

    destination = durable_private_path(path, description="local-control install manifest")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(destination.parent)
    payload = manifest.model_dump_json(indent=2).encode("ascii") + b"\n"
    temporary = destination.parent / f".{destination.name}.new-{secrets.token_hex(16)}"
    descriptor = -1
    directory_descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise OSError("fresh install manifest temporary has unsafe metadata")
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _current_roles(config: AppConfig) -> dict[str, str]:
    roles: dict[str, str] = {}
    for group in config.groups:
        for member in group.members:
            previous = roles.setdefault(member.device, member.role.value)
            if previous != member.role.value:
                raise ValueError("one logical device has conflicting current role assignments")
    return roles


def _combined_file_digest(paths: tuple[str | None, ...]) -> str:
    digest = hashlib.sha256()
    for raw_path in paths:
        if raw_path is None:
            raise RuntimeError("installed protocol module has no content path")
        path = Path(raw_path)
        data = path.read_bytes()
        digest.update(path.name.encode("ascii"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _require_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise OSError("local-control private directory must be owner-only mode 0700")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while replacing install manifest")
        view = view[written:]


def _read_current_private_file(path: Path, *, maximum: int) -> bytes:
    payload, metadata = _read_current_file(path, maximum=maximum)
    if (
        metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise OSError("local-control private input must be owner-only mode 0600")
    return payload


def _read_current_regular_file(path: Path, *, maximum: int) -> bytes:
    payload, _metadata = _read_current_file(path, maximum=maximum)
    return payload


def _read_current_file(path: Path, *, maximum: int) -> tuple[bytes, os.stat_result]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("O_NOFOLLOW is required for local-control current inputs")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum:
            raise OSError("local-control current input is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise OSError("local-control current input exceeds its size bound")
        named = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("local-control current input changed while reading")
        return payload, opened
    finally:
        os.close(descriptor)


__all__ = [
    "FreshInstallManifest",
    "build_fresh_install_manifest",
    "current_protocol_schema_sha256",
    "hash_current_template_contents",
    "load_current_private_config",
    "replace_install_manifest",
]
