"""Replacement-only systemd service generation from current installation inputs."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV, hardware_safety_root
from jebao_flow.local_control.models import FreshInstallManifest
from jebao_flow.operator_capture.paths import durable_private_path

HARDWARE_SAFETY_VOLUME_ID_FILE_ENV = "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID_FILE"
_MQTT_PASSWORD_MAX_BYTES = 65536
_HARDWARE_SAFETY_VOLUME_ID_MAX_BYTES = 128


@dataclass(frozen=True, slots=True)
class LocalControlServiceSpec:
    """Current execution settings; none of these paths become device identity or authority."""

    executable: Path
    config: Path
    manifest: Path
    hardware_safety_volume_id_file: Path = field(repr=False)
    role_attested_by: str
    role_attested_at: datetime
    role_binding_sha256: str
    qualifications: Path | None = None
    template_roots: tuple[Path, ...] = ()
    mqtt_password_file: Path | None = None
    write_enabled: bool = False

    def __post_init__(self) -> None:
        paths = (
            self.executable,
            self.config,
            self.manifest,
            self.hardware_safety_volume_id_file,
            *self.template_roots,
        )
        if self.qualifications is not None:
            paths = (*paths, self.qualifications)
        if self.mqtt_password_file is not None:
            paths = (*paths, self.mqtt_password_file)
        if any(not path.is_absolute() for path in paths):
            raise ValueError("service inputs must use current absolute paths")
        if (
            not self.role_attested_by
            or not self.role_attested_by.isascii()
            or any(ord(character) < 0x20 for character in self.role_attested_by)
        ):
            raise ValueError("service role attestation must use printable ASCII")
        if self.role_attested_at.tzinfo is None or self.role_attested_at.utcoffset() is None:
            raise ValueError("service role attestation timestamp must be timezone-aware")
        if len(self.role_binding_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.role_binding_sha256
        ):
            raise ValueError("service role binding must be lowercase SHA-256")
        if len(set(self.template_roots)) != len(self.template_roots):
            raise ValueError("service template roots must be distinct")


@dataclass(frozen=True, slots=True)
class PreparedServiceArtifact:
    unit_sha256: str
    write_enabled: bool
    qualification_configured: bool
    template_root_count: int
    mqtt_password_file_configured: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "service_prepared",
            "unit_sha256": self.unit_sha256,
            "write_enabled": self.write_enabled,
            "qualification_configured": self.qualification_configured,
            "template_root_count": self.template_root_count,
            "mqtt_password_file_configured": self.mqtt_password_file_configured,
            "hardware_safety_identity_configured": True,
            "hardware_safety_identity_file_replaced": True,
            "requires_hardware_safety_mount": True,
            "previous_unit_values_reused": False,
            "private_paths_exposed": False,
            "system_service_installed": False,
        }


def resolve_current_executable(value: Path | str | None = None) -> Path:
    """Resolve the executable now instead of carrying a prior install discovery forward."""

    selected = str(value) if value is not None else shutil.which("jebao-flow-local")
    if not selected:
        raise ValueError("current jebao-flow-local executable was not found")
    executable = durable_private_path(selected, description="local-control executable")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("current local-control executable is not an executable regular file")
    return executable


def current_service_path(value: Path | str, *, description: str) -> Path:
    """Resolve one current service input without assigning it device-identity meaning."""

    return durable_private_path(value, description=description)


def stable_role_binding_sha256(manifest: FreshInstallManifest) -> str:
    """Bind only stable physical/product/logical-role facts, never runtime provenance."""

    records = sorted(
        (
            {
                "logical_member": device.logical_member,
                "logical_role": device.logical_role,
                "physical_identity_key": device.physical_identity_key,
                "product_key": device.product_key,
            }
            for device in manifest.devices
        ),
        key=lambda item: item["logical_member"],
    )
    payload = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def require_current_mqtt_password_file(value: Path | str) -> Path:
    """Validate a password file without copying its secret into the generated unit."""

    return _require_current_private_value_file(
        value,
        description="local-control MQTT password file",
    )


def read_current_mqtt_password(value: Path | str) -> str:
    """Read one current owner-only password file at service start, never at generation time."""

    payload = _read_current_private_value(
        value,
        description="local-control MQTT password file",
        maximum_bytes=_MQTT_PASSWORD_MAX_BYTES,
    )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("local-control MQTT password file must contain UTF-8") from error


def require_current_hardware_safety_volume_id_file(value: Path | str) -> Path:
    """Validate the owner-only authority file named by a generated service."""

    return _require_current_private_value_file(
        value,
        description="hardware-safety volume identity file",
    )


def read_current_hardware_safety_volume_id(value: Path | str) -> str:
    """Load the stable mount authority after service start, outside the unit environment."""

    payload = _read_current_private_value(
        value,
        description="hardware-safety volume identity file",
        maximum_bytes=_HARDWARE_SAFETY_VOLUME_ID_MAX_BYTES,
    )
    try:
        volume_id = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("hardware-safety volume identity must be lowercase 32-hex") from error
    _validate_hardware_safety_volume_id(volume_id)
    return volume_id


def _require_current_private_value_file(
    value: Path | str,
    *,
    description: str,
) -> Path:
    path = current_service_path(value, description=description)
    _validate_secret_file_metadata(path, description=description)
    return path


def _read_current_private_value(
    value: Path | str,
    *,
    description: str,
    maximum_bytes: int,
) -> bytes:
    path = _require_current_private_value_file(value, description=description)
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError(f"O_NOFOLLOW is required for the {description}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(descriptor)
        payload = os.read(descriptor, maximum_bytes + 1)
        if len(payload) > maximum_bytes or os.read(descriptor, 1):
            raise OSError(f"{description} exceeds its size bound")
        named = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError(f"{description} changed while reading")
        _validate_secret_file_metadata(path, description=description)
    finally:
        os.close(descriptor)
    if payload.endswith(b"\r\n"):
        exact = payload[:-2]
    elif payload.endswith(b"\n"):
        exact = payload[:-1]
    else:
        exact = payload
    if not exact:
        raise ValueError(f"{description} must contain one value")
    if b"\0" in exact or b"\n" in exact or b"\r" in exact:
        raise ValueError(f"{description} must contain one value")
    return exact


def _validate_secret_file_metadata(path: Path, *, description: str) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise OSError(f"{description} must be owner-only mode 0600")


def _validate_hardware_safety_volume_id(value: str) -> None:
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("hardware-safety volume identity must be lowercase 32-hex")


def render_systemd_service(spec: LocalControlServiceSpec) -> bytes:
    """Render one complete unit; the previous unit is never an input."""

    environment = [
        _environment_directive("JEBAO_FLOW_CONFIG", str(spec.config)),
        # A manager-level raw value must not enter the service environment. The service loads the
        # current owner-only file after exec and supplies the existing validator in process only.
        _environment_directive(HARDWARE_SAFETY_VOLUME_ID_ENV, ""),
        _environment_directive(
            HARDWARE_SAFETY_VOLUME_ID_FILE_ENV,
            str(spec.hardware_safety_volume_id_file),
        ),
        _environment_directive("JEBAO_FLOW_LOCAL_INSTALL_MANIFEST", str(spec.manifest)),
        _environment_directive(
            "JEBAO_FLOW_LOCAL_QUALIFICATIONS",
            "" if spec.qualifications is None else str(spec.qualifications),
        ),
        # Explicitly neutralize a manager-level stale value. Current roots are passed as repeated
        # command arguments below, so paths containing the platform separator remain unambiguous.
        _environment_directive("JEBAO_FLOW_APP_TEMPLATE_ROOTS", ""),
        _environment_directive("JEBAO_FLOW_ROLE_ATTESTED_BY", spec.role_attested_by),
        _environment_directive(
            "JEBAO_FLOW_ROLE_ATTESTED_AT",
            spec.role_attested_at.isoformat(),
        ),
        _environment_directive(
            "JEBAO_FLOW_ROLE_BINDING_SHA256",
            spec.role_binding_sha256,
        ),
        _environment_directive(
            "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE",
            "" if spec.mqtt_password_file is None else str(spec.mqtt_password_file),
        ),
        _environment_directive("PYTHONUNBUFFERED", "1"),
    ]
    command = [
        _quote_unit_word(str(spec.executable)),
        "serve",
        "--role-attested-by",
        "${JEBAO_FLOW_ROLE_ATTESTED_BY}",
        "--role-attested-at",
        "${JEBAO_FLOW_ROLE_ATTESTED_AT}",
        "--role-binding-sha256",
        "${JEBAO_FLOW_ROLE_BINDING_SHA256}",
    ]
    for root in spec.template_roots:
        command.extend(("--template-root", _quote_unit_word(str(root))))
    if spec.write_enabled:
        command.append("--write-enabled")
    lines = [
        "[Unit]",
        "Description=Jebao Flow local-first controller",
        "Wants=network-online.target",
        "After=network-online.target",
        f"RequiresMountsFor={hardware_safety_root()}",
        "",
        "[Service]",
        "Type=simple",
        *environment,
        f"ExecStart={' '.join(command)}",
        "Restart=on-failure",
        "RestartSec=5s",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def replace_prepared_service(
    destination: Path | str,
    spec: LocalControlServiceSpec,
    *,
    hardware_safety_volume_id: str,
) -> PreparedServiceArtifact:
    """Atomically replace an owner-only staging unit without reading old bytes.

    This prepares a unit for operator inspection and explicit installation. It never writes to a
    systemd system directory or invokes systemctl.
    """

    path = current_service_path(destination, description="local-control service staging file")
    protected_inputs = {
        spec.executable,
        spec.config,
        spec.manifest,
        *spec.template_roots,
    }
    if spec.qualifications is not None:
        protected_inputs.add(spec.qualifications)
    if spec.mqtt_password_file is not None:
        protected_inputs.add(spec.mqtt_password_file)
    identity_path = current_service_path(
        spec.hardware_safety_volume_id_file,
        description="hardware-safety volume identity file",
    )
    if identity_path != spec.hardware_safety_volume_id_file:
        raise ValueError("hardware-safety volume identity file must use its resolved path")
    if path == identity_path or path in protected_inputs or identity_path in protected_inputs:
        raise ValueError("service staging file must not replace a current service input")
    _validate_hardware_safety_volume_id(hardware_safety_volume_id)
    _replace_owner_only_file(
        identity_path,
        hardware_safety_volume_id.encode("ascii") + b"\n",
        description="hardware-safety volume identity",
    )
    payload = render_systemd_service(spec)
    _replace_owner_only_file(path, payload, description="service staging")
    return PreparedServiceArtifact(
        unit_sha256=hashlib.sha256(payload).hexdigest(),
        write_enabled=spec.write_enabled,
        qualification_configured=spec.qualifications is not None,
        template_root_count=len(spec.template_roots),
        mqtt_password_file_configured=spec.mqtt_password_file is not None,
    )


def _replace_owner_only_file(path: Path, payload: bytes, *, description: str) -> None:
    """Replace one private file without parsing or merging the prior bytes."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(path.parent)
    temporary = path.parent / f".{path.name}.new-{secrets.token_hex(16)}"
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
            raise OSError(f"fresh {description} file has unsafe metadata")
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
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


def _environment_directive(name: str, value: str) -> str:
    # Environment= does not expand '$', while '%' specifiers still apply. Preserve a literal
    # dollar exactly here; ExecStart= uses the separate command-word behavior below.
    return f"Environment={_quote_unit_word(f'{name}={value}', escape_dollar=False)}"


def _quote_unit_word(value: str, *, escape_dollar: bool = True) -> str:
    if "\0" in value or "\n" in value or "\r" in value:
        raise ValueError("systemd service values must not contain NUL or newlines")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if escape_dollar:
        escaped = escaped.replace("$", "$$")
    return f'"{escaped}"'


def _require_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise OSError("local-control service staging directory must be owner-only mode 0700")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while replacing local-control service")
        view = view[written:]


__all__ = [
    "HARDWARE_SAFETY_VOLUME_ID_FILE_ENV",
    "LocalControlServiceSpec",
    "PreparedServiceArtifact",
    "current_service_path",
    "read_current_hardware_safety_volume_id",
    "read_current_mqtt_password",
    "render_systemd_service",
    "replace_prepared_service",
    "require_current_hardware_safety_volume_id_file",
    "require_current_mqtt_password_file",
    "resolve_current_executable",
    "stable_role_binding_sha256",
]
