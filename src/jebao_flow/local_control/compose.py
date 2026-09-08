"""Fresh, replacement-only Docker Compose deployment artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import TypeAdapter, ValidationError

from jebao_flow.config import AppConfig
from jebao_flow.local_control.models import QualificationBinding
from jebao_flow.operator_capture.paths import durable_private_path

COMPOSE_STATE_ROOT = Path("/data/local-control")
COMPOSE_CONFIG_PATH = COMPOSE_STATE_ROOT / "config.yaml"
COMPOSE_MANIFEST_PATH = COMPOSE_STATE_ROOT / "install.json"
COMPOSE_VOLUME_ID_PATH = COMPOSE_STATE_ROOT / "hardware-safety-volume-id"
COMPOSE_MQTT_PASSWORD_PATH = COMPOSE_STATE_ROOT / "mqtt-password"
COMPOSE_QUALIFICATIONS_PATH = COMPOSE_STATE_ROOT / "qualifications.json"
COMPOSE_DESCRIPTOR_PATH = COMPOSE_STATE_ROOT / "bundle.json"

_SECRET_ROOT = Path("/run/secrets")
_SECRET_PATH_BY_FILENAME = {
    "bundle.json": _SECRET_ROOT / "jebao_flow_local_bundle",
    "config.yaml": _SECRET_ROOT / "jebao_flow_local_config",
    "hardware-safety-volume-id": _SECRET_ROOT / "jebao_flow_local_volume_id",
    "mqtt-password": _SECRET_ROOT / "jebao_flow_local_mqtt_password",
    "qualifications.json": _SECRET_ROOT / "jebao_flow_local_qualifications",
}

_BUNDLE_DESCRIPTOR = "bundle.json"
_CONFIG_FILENAME = "config.yaml"
_VOLUME_ID_FILENAME = "hardware-safety-volume-id"
_MQTT_PASSWORD_FILENAME = "mqtt-password"
_QUALIFICATIONS_FILENAME = "qualifications.json"
_RUNTIME_ONLY_FILENAME = COMPOSE_MANIFEST_PATH.name
_GENERATION_FILENAMES = {
    _BUNDLE_DESCRIPTOR,
    _CONFIG_FILENAME,
    _VOLUME_ID_FILENAME,
    _MQTT_PASSWORD_FILENAME,
    _QUALIFICATIONS_FILENAME,
}
_GENERATION_RE = re.compile(r"[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_VOLUME_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_QUALIFICATIONS = TypeAdapter(tuple[QualificationBinding, ...])
_MAX_INPUT_BYTES = {
    _CONFIG_FILENAME: 1024 * 1024,
    _VOLUME_ID_FILENAME: 128,
    _MQTT_PASSWORD_FILENAME: 65536,
    _QUALIFICATIONS_FILENAME: 1024 * 1024,
    _BUNDLE_DESCRIPTOR: 65536,
}


@dataclass(frozen=True, slots=True)
class LocalControlComposeSpec:
    """Current host execution settings; paths are provenance, never device identity."""

    build_context: Path = field(repr=False)
    input_generation_root: Path = field(repr=False)
    generation: str
    role_attested_by: str
    role_attested_at: str
    role_binding_sha256: str
    template_roots: tuple[Path, ...] = field(default=(), repr=False)
    mqtt_password_configured: bool = False
    qualification_configured: bool = False
    write_enabled: bool = False

    def __post_init__(self) -> None:
        paths = (self.build_context, self.input_generation_root, *self.template_roots)
        if any(not path.is_absolute() for path in paths):
            raise ValueError("compose inputs must use current absolute paths")
        if _GENERATION_RE.fullmatch(self.generation) is None:
            raise ValueError("compose generation must be lowercase 128-bit hex")
        if _SHA256_RE.fullmatch(self.role_binding_sha256) is None:
            raise ValueError("compose role binding must be lowercase SHA-256")
        for value in (self.role_attested_by, self.role_attested_at):
            if (
                not value
                or not value.isascii()
                or any(ord(character) < 0x20 for character in value)
            ):
                raise ValueError("compose role attestation must use printable ASCII")
        if len(set(self.template_roots)) != len(self.template_roots):
            raise ValueError("compose template roots must be distinct")
        if self.qualification_configured and not self.write_enabled:
            raise ValueError("read-only compose cannot retain write qualifications")


@dataclass(frozen=True, slots=True)
class PreparedComposeArtifact:
    compose_sha256: str
    generation: str
    write_enabled: bool
    qualification_configured: bool
    mqtt_password_configured: bool
    template_root_count: int
    pruned_generation_count: int

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "compose_prepared",
            "compose_sha256": self.compose_sha256,
            "install_generation": self.generation,
            "write_enabled": self.write_enabled,
            "qualification_configured": self.qualification_configured,
            "mqtt_password_file_configured": self.mqtt_password_configured,
            "template_root_count": self.template_root_count,
            "input_generation_created": True,
            "pruned_generation_count": self.pruned_generation_count,
            "previous_compose_values_reused": False,
            "previous_bundle_values_reused": False,
            "private_paths_exposed": False,
            "hardware_safety_volume_external": True,
            "hardware_safety_volume_access": "read_write_for_coordination_locks",
            "device_write_enabled_by_volume_mode": False,
            "compose_started": False,
        }


@dataclass(frozen=True, slots=True)
class StagedComposeInputs:
    generation: str
    file_count: int

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "compose_inputs_staged",
            "install_generation": self.generation,
            "file_count": self.file_count,
            "previous_runtime_values_reused": False,
            "private_values_exposed": False,
        }


def resolve_current_build_context(value: Path | str | None = None) -> Path:
    """Find the source checkout now; no prior install discovery is consulted."""

    if value is not None:
        candidates = (Path(value),)
    else:
        current = Path.cwd().resolve()
        candidates = (current, *current.parents)
    for candidate in candidates:
        try:
            root = durable_private_path(candidate, description="local-control build context")
        except ValueError:
            continue
        if (root / "Dockerfile").is_file() and (root / "pyproject.toml").is_file():
            return root
    raise ValueError("current local-control Docker build context was not found")


def prepare_compose_deployment(
    destination: Path | str,
    bundle_root: Path | str,
    spec: LocalControlComposeSpec,
    *,
    config: AppConfig,
    hardware_safety_volume_id: str,
    mqtt_password: str | None,
    qualifications: tuple[QualificationBinding, ...] | None,
) -> PreparedComposeArtifact:
    """Create one new private input generation, then replace the standalone Compose file."""

    output = durable_private_path(destination, description="local-control compose staging file")
    parent = durable_private_path(bundle_root, description="local-control compose bundle root")
    if output == parent or parent in output.parents:
        raise ValueError("compose output must be outside its generated input bundle root")
    if spec.input_generation_root != parent / spec.generation:
        raise ValueError("compose input root must name the current install generation")
    if _VOLUME_ID_RE.fullmatch(hardware_safety_volume_id) is None:
        raise ValueError("hardware-safety volume identity must be lowercase 32-hex")
    if (mqtt_password is not None) != spec.mqtt_password_configured:
        raise ValueError("compose MQTT password input does not match the current config")
    if (qualifications is not None) != spec.qualification_configured:
        raise ValueError("compose qualification input does not match write configuration")

    files: dict[str, bytes] = {
        _CONFIG_FILENAME: _canonical_config(config),
        _VOLUME_ID_FILENAME: hardware_safety_volume_id.encode("ascii") + b"\n",
    }
    if mqtt_password is not None:
        _validate_one_value(mqtt_password.encode("utf-8"), description="MQTT password")
        files[_MQTT_PASSWORD_FILENAME] = mqtt_password.encode("utf-8") + b"\n"
    if qualifications is not None:
        files[_QUALIFICATIONS_FILENAME] = _canonical_qualifications(qualifications)

    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(parent, owner=os.geteuid())
    preparation_lock = _acquire_preparation_lock(parent)
    try:
        _require_retained_generation_directories_private(parent)
        generation_root = _create_generation_directory(parent, spec.generation)
        compose_payload = b""
        try:
            digests: dict[str, str] = {}
            for name, payload in files.items():
                _write_new_generation_file(generation_root, name, payload)
                digests[name] = hashlib.sha256(payload).hexdigest()
            descriptor = _canonical_descriptor(spec.generation, digests)
            _write_new_generation_file(generation_root, _BUNDLE_DESCRIPTOR, descriptor)
            _fsync_directory(generation_root)
            _fsync_directory(parent)
            _require_private_directory(generation_root, owner=os.geteuid())

            compose_payload = render_compose(spec)
            _replace_private_file(output, compose_payload)
        except Exception:
            # The output is replaced last, so an incomplete unreferenced generation is never active.
            _remove_exact_generation(parent, spec.generation, expected_names=None)
            raise
        pruned = _prune_prior_generations(parent, keep=spec.generation)
        return PreparedComposeArtifact(
            compose_sha256=hashlib.sha256(compose_payload).hexdigest(),
            generation=spec.generation,
            write_enabled=spec.write_enabled,
            qualification_configured=spec.qualification_configured,
            mqtt_password_configured=spec.mqtt_password_configured,
            template_root_count=len(spec.template_roots),
            pruned_generation_count=pruned,
        )
    finally:
        os.close(preparation_lock)


def render_compose(spec: LocalControlComposeSpec) -> bytes:
    """Render a standalone two-service Compose model without secret values."""

    build = {"context": _compose_literal(str(spec.build_context))}
    data_mount = {
        "type": "volume",
        "source": "local_control_data",
        "target": "/data",
        # The non-root stage replaces current inputs inside this private volume.
        "read_only": False,
    }
    safety_mount = {
        "type": "volume",
        "source": "hardware_safety",
        "target": "/hardware-safety",
        # Read-only device polling still creates and flocks coordination files here.
        "read_only": False,
    }
    runtime_mounts: list[dict[str, object]] = [dict(data_mount), dict(safety_mount)]
    runtime_command = [
        "serve",
        "--compose-generation",
        spec.generation,
        "--role-attested-by",
        _compose_literal(spec.role_attested_by),
        "--role-attested-at",
        _compose_literal(spec.role_attested_at),
        "--role-binding-sha256",
        spec.role_binding_sha256,
    ]
    for index, root in enumerate(spec.template_roots):
        target = f"/run/jebao-flow-local-templates/{index}"
        runtime_mounts.append(
            {
                "type": "bind",
                "source": _compose_literal(str(root)),
                "target": target,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        )
        runtime_command.extend(("--template-root", target))
    if spec.write_enabled:
        runtime_command.append("--write-enabled")

    environment = {
        "JEBAO_FLOW_CONFIG": str(COMPOSE_CONFIG_PATH),
        "JEBAO_FLOW_LOCAL_INSTALL_MANIFEST": str(COMPOSE_MANIFEST_PATH),
        "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID": "",
        "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID_FILE": str(COMPOSE_VOLUME_ID_PATH),
        "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE": (
            str(COMPOSE_MQTT_PASSWORD_PATH) if spec.mqtt_password_configured else ""
        ),
        "JEBAO_FLOW_LOCAL_QUALIFICATIONS": (
            str(COMPOSE_QUALIFICATIONS_PATH) if spec.qualification_configured else ""
        ),
        "JEBAO_FLOW_APP_TEMPLATE_ROOTS": "",
        "PYTHONUNBUFFERED": "1",
    }
    secret_sources = {
        "local_control_bundle": spec.input_generation_root / _BUNDLE_DESCRIPTOR,
        "local_control_config": spec.input_generation_root / _CONFIG_FILENAME,
        "local_control_volume_id": spec.input_generation_root / _VOLUME_ID_FILENAME,
    }
    if spec.mqtt_password_configured:
        secret_sources["local_control_mqtt_password"] = (
            spec.input_generation_root / _MQTT_PASSWORD_FILENAME
        )
    if spec.qualification_configured:
        secret_sources["local_control_qualifications"] = (
            spec.input_generation_root / _QUALIFICATIONS_FILENAME
        )
    selected_secrets = [
        ("local_control_bundle", _BUNDLE_DESCRIPTOR),
        ("local_control_config", _CONFIG_FILENAME),
        ("local_control_volume_id", _VOLUME_ID_FILENAME),
    ]
    if spec.mqtt_password_configured:
        selected_secrets.append(("local_control_mqtt_password", _MQTT_PASSWORD_FILENAME))
    if spec.qualification_configured:
        selected_secrets.append(("local_control_qualifications", _QUALIFICATIONS_FILENAME))
    stage_secrets = [
        {
            "source": source,
            "target": _SECRET_PATH_BY_FILENAME[filename].name,
        }
        for source, filename in selected_secrets
    ]
    model = {
        "name": "jebao-flow-local",
        "services": {
            "jebao-flow-local-stage": {
                "build": dict(build),
                "network_mode": "none",
                "entrypoint": ["jebao-flow-local"],
                "command": [
                    "stage-compose-inputs",
                    "--expected-generation",
                    spec.generation,
                ],
                "volumes": [dict(data_mount)],
                "secrets": stage_secrets,
                "read_only": True,
                "tmpfs": ["/tmp:rw,nosuid,nodev,noexec"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "restart": "no",
                "labels": {"org.jebao-flow.install-generation": spec.generation},
            },
            "jebao-flow-local": {
                "build": dict(build),
                "network_mode": "host",
                "entrypoint": ["jebao-flow-local"],
                "command": runtime_command,
                "environment": environment,
                "volumes": runtime_mounts,
                "read_only": True,
                "tmpfs": ["/tmp:rw,nosuid,nodev,noexec"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "depends_on": {
                    "jebao-flow-local-stage": {
                        "condition": "service_completed_successfully",
                        "restart": True,
                    }
                },
                "restart": "unless-stopped",
                "stop_grace_period": "30s",
                "labels": {"org.jebao-flow.install-generation": spec.generation},
            },
        },
        "volumes": {
            "local_control_data": {"name": "jebao-flow-local-data"},
            "hardware_safety": {
                "external": True,
                "name": "jebao-flow-hardware-safety",
            },
        },
        "secrets": {
            name: {"file": _compose_literal(str(path))}
            for name, path in secret_sources.items()
        },
    }
    return yaml.safe_dump(
        model,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).encode("ascii")


def stage_compose_inputs(
    expected_generation: str,
    *,
    state_root: Path = COMPOSE_STATE_ROOT,
    allow_unmounted_for_test: bool = False,
    secret_paths_for_test: dict[str, Path] | None = None,
) -> StagedComposeInputs:
    """Copy one verified input generation into the private named volume, never merging it."""

    if _GENERATION_RE.fullmatch(expected_generation) is None:
        raise ValueError("compose generation must be lowercase 128-bit hex")
    state_root = Path(state_root)
    data_root = state_root.parent
    secret_paths = secret_paths_for_test or _SECRET_PATH_BY_FILENAME
    if not allow_unmounted_for_test:
        if secret_paths_for_test is not None or state_root != COMPOSE_STATE_ROOT:
            raise OSError("compose staging paths are fixed container mounts")
        if not os.path.ismount(data_root):
            raise OSError("compose data root must be a mounted volume")

    descriptor_payload = _read_secret_file(
        Path(secret_paths[_BUNDLE_DESCRIPTOR]),
        maximum=_MAX_INPUT_BYTES[_BUNDLE_DESCRIPTOR],
        require_read_only_mount=not allow_unmounted_for_test,
    )
    descriptor = _decode_descriptor(descriptor_payload, expected_generation)
    if set(descriptor["files"]) - set(secret_paths):
        raise OSError("compose input generation is missing a mounted secret")
    payloads = {
        name: _read_secret_file(
            Path(secret_paths[name]),
            maximum=_MAX_INPUT_BYTES[name],
            require_read_only_mount=not allow_unmounted_for_test,
        )
        for name in descriptor["files"]
    }
    for name, payload in payloads.items():
        if hashlib.sha256(payload).hexdigest() != descriptor["files"][name]:
            raise OSError("compose input content digest mismatch")
    _validate_staged_payloads(payloads)

    state_descriptor = _open_or_create_state_directory(
        data_root,
        state_root.name,
        allow_unmounted_for_test=allow_unmounted_for_test,
    )
    try:
        for name in (
            _MQTT_PASSWORD_FILENAME,
            _QUALIFICATIONS_FILENAME,
            _RUNTIME_ONLY_FILENAME,
        ):
            if name not in payloads:
                _unlink_stale_runtime_file(state_descriptor, name)
        for name, payload in payloads.items():
            _replace_relative_runtime_file(
                state_descriptor,
                name,
                payload,
            )
        _replace_relative_runtime_file(
            state_descriptor,
            _BUNDLE_DESCRIPTOR,
            descriptor_payload,
        )
        os.fsync(state_descriptor)
    finally:
        os.close(state_descriptor)
    return StagedComposeInputs(generation=expected_generation, file_count=len(payloads))


def validate_current_compose_inputs(
    expected_generation: str,
    *,
    state_root: Path = COMPOSE_STATE_ROOT,
) -> None:
    """Bind runtime startup to the exact staged generation before any current input is read."""

    if _GENERATION_RE.fullmatch(expected_generation) is None:
        raise ValueError("compose generation must be lowercase 128-bit hex")
    descriptor_fd = _open_directory(Path(state_root))
    try:
        metadata = os.fstat(descriptor_fd)
        _require_private_directory_metadata(metadata, owner=os.geteuid())
        payload = _read_relative_file(
            descriptor_fd,
            _BUNDLE_DESCRIPTOR,
            maximum=_MAX_INPUT_BYTES[_BUNDLE_DESCRIPTOR],
            owner=os.geteuid(),
        )
        descriptor = _decode_descriptor(payload, expected_generation)
        for name, digest in descriptor["files"].items():
            current = _read_relative_file(
                descriptor_fd,
                name,
                maximum=_MAX_INPUT_BYTES[name],
                owner=os.geteuid(),
            )
            if hashlib.sha256(current).hexdigest() != digest:
                raise OSError("current compose runtime input digest mismatch")
    finally:
        os.close(descriptor_fd)


def _canonical_config(config: AppConfig) -> bytes:
    return yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=True,
        allow_unicode=False,
        default_flow_style=False,
    ).encode("ascii")


def _canonical_qualifications(values: tuple[QualificationBinding, ...]) -> bytes:
    payload = {"qualifications": [value.model_dump(mode="json") for value in values]}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii") + b"\n"


def _canonical_descriptor(generation: str, digests: dict[str, str]) -> bytes:
    payload = {
        "schema_version": 1,
        "generation": generation,
        "files": dict(sorted(digests.items())),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii") + b"\n"


def _decode_descriptor(payload: bytes, expected_generation: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("compose input descriptor is invalid JSON") from error
    if not isinstance(decoded, dict) or set(decoded) != {"schema_version", "generation", "files"}:
        raise ValueError("compose input descriptor has an invalid shape")
    files = decoded.get("files")
    if (
        decoded.get("schema_version") != 1
        or decoded.get("generation") != expected_generation
        or not isinstance(files, dict)
        or not files
    ):
        raise ValueError("compose input descriptor does not match this generation")
    allowed = {
        _CONFIG_FILENAME,
        _VOLUME_ID_FILENAME,
        _MQTT_PASSWORD_FILENAME,
        _QUALIFICATIONS_FILENAME,
    }
    if set(files) - allowed or _CONFIG_FILENAME not in files or _VOLUME_ID_FILENAME not in files:
        raise ValueError("compose input descriptor names invalid files")
    if any(
        not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
        for value in files.values()
    ):
        raise ValueError("compose input descriptor contains an invalid digest")
    return decoded


def _validate_staged_payloads(payloads: dict[str, bytes]) -> None:
    try:
        config_raw = yaml.safe_load(payloads[_CONFIG_FILENAME])
        if not isinstance(config_raw, dict):
            raise ValueError("compose config root must be a mapping")
        config = AppConfig.model_validate(config_raw)
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError("compose config is invalid") from error
    volume_id = payloads[_VOLUME_ID_FILENAME]
    _validate_one_value(volume_id, description="hardware-safety volume identity")
    if _VOLUME_ID_RE.fullmatch(volume_id.rstrip(b"\n").decode("ascii", "strict")) is None:
        raise ValueError("hardware-safety volume identity must be lowercase 32-hex")
    password_present = _MQTT_PASSWORD_FILENAME in payloads
    if password_present != (config.mqtt.password_env is not None):
        raise ValueError("compose MQTT password does not match the current config")
    if password_present:
        _validate_one_value(payloads[_MQTT_PASSWORD_FILENAME], description="MQTT password")
        payloads[_MQTT_PASSWORD_FILENAME].rstrip(b"\n").decode("utf-8", "strict")
    qualification_present = _QUALIFICATIONS_FILENAME in payloads
    if qualification_present:
        try:
            raw = json.loads(payloads[_QUALIFICATIONS_FILENAME])
            selected = raw.get("qualifications") if isinstance(raw, dict) else raw
            _QUALIFICATIONS.validate_python(selected)
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            raise ValueError("compose qualification input is invalid") from error


def _validate_one_value(payload: bytes, *, description: str) -> None:
    exact = payload[:-1] if payload.endswith(b"\n") else payload
    if not exact or b"\0" in exact or b"\n" in exact or b"\r" in exact:
        raise ValueError(f"{description} must contain one value")


def _compose_literal(value: str) -> str:
    if "\0" in value or "\n" in value or "\r" in value:
        raise ValueError("compose values must not contain NUL or newlines")
    return value.replace("$", "$$")


def _create_generation_directory(parent: Path, generation: str) -> Path:
    root = parent / generation
    try:
        os.mkdir(root, 0o700)
    except FileExistsError as error:
        raise OSError("fresh compose input generation already exists") from error
    _require_private_directory(root, owner=os.geteuid())
    return root


def _acquire_preparation_lock(parent: Path) -> int:
    """Serialize generation replacement and pruning inside one dedicated bundle root."""

    parent_fd = _open_directory(parent)
    descriptor = -1
    created = False
    try:
        try:
            descriptor = os.open(
                ".prepare.lock",
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                ".prepare.lock",
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
        _require_private_file_metadata(os.fstat(descriptor), owner=os.geteuid())
        if created:
            os.fsync(parent_fd)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        os.close(parent_fd)


def _write_new_generation_file(root: Path, name: str, payload: bytes) -> None:
    """Write a Compose source secret protected by its owner-only generation directory.

    Local Docker Compose bind-mounts file-backed secrets and does not implement secret uid/gid/mode
    remapping. Mode 0444 therefore lets the image's fixed non-root user read the mount on Linux;
    the mode-0700 parent directory is the host-side confidentiality boundary.
    """

    descriptor = os.open(
        root / name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o444,
    )
    try:
        os.fchmod(descriptor, 0o444)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        _require_generation_file_metadata(os.fstat(descriptor), owner=os.geteuid())
    finally:
        os.close(descriptor)


def _replace_private_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(path.parent, owner=os.geteuid())
    temporary = path.parent / f".{path.name}.new-{secrets.token_hex(16)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        _require_private_file_metadata(os.fstat(descriptor), owner=os.geteuid())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _prune_prior_generations(parent: Path, *, keep: str) -> int:
    """Remove only prior generations that validate as artifacts created by this module."""

    count = 0
    parent_fd = _open_directory(parent)
    try:
        candidate_names = tuple(os.listdir(parent_fd))
    finally:
        os.close(parent_fd)
    for name in candidate_names:
        if name == keep or _GENERATION_RE.fullmatch(name) is None:
            continue
        parent_fd = _open_directory(parent)
        try:
            generation_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError:
            os.close(parent_fd)
            continue
        os.close(parent_fd)
        expected: set[str] | None = None
        recognized = False
        try:
            metadata = os.fstat(generation_fd)
            _require_private_directory_metadata(metadata, owner=os.geteuid())
            child_names = set(os.listdir(generation_fd))
            if _BUNDLE_DESCRIPTOR not in child_names:
                recognized = child_names.issubset(
                    _GENERATION_FILENAMES - {_BUNDLE_DESCRIPTOR}
                )
            else:
                descriptor = _read_generation_file(
                    generation_fd,
                    _BUNDLE_DESCRIPTOR,
                    maximum=_MAX_INPUT_BYTES[_BUNDLE_DESCRIPTOR],
                    owner=os.geteuid(),
                )
                decoded = _decode_descriptor(descriptor, name)
                expected = {_BUNDLE_DESCRIPTOR, *decoded["files"]}
                recognized = child_names.issubset(expected)
        except (OSError, ValueError):
            recognized = False
        finally:
            os.close(generation_fd)
        if recognized and _remove_exact_generation(parent, name, expected_names=expected):
            count += 1
    return count


def _remove_exact_generation(
    parent: Path,
    generation: str,
    *,
    expected_names: set[str] | None,
) -> bool:
    """Delete one exact generated directory without following names outside that directory."""

    parent_fd = _open_directory(parent)
    generation_fd = -1
    try:
        generation_fd = os.open(
            generation,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        _require_private_directory_metadata(os.fstat(generation_fd), owner=os.geteuid())
        names = set(os.listdir(generation_fd))
        if expected_names is not None and (
            _BUNDLE_DESCRIPTOR not in names or not names.issubset(expected_names)
        ):
            return False
        if not names.issubset(_GENERATION_FILENAMES):
            return False
        for name in names:
            metadata = os.stat(name, dir_fd=generation_fd, follow_symlinks=False)
            _require_generation_file_metadata(metadata, owner=os.geteuid())
        for name in sorted(names - {_BUNDLE_DESCRIPTOR}):
            os.unlink(name, dir_fd=generation_fd)
        if _BUNDLE_DESCRIPTOR in names:
            os.unlink(_BUNDLE_DESCRIPTOR, dir_fd=generation_fd)
        os.fsync(generation_fd)
        os.close(generation_fd)
        generation_fd = -1
        os.rmdir(generation, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except OSError:
        return False
    finally:
        if generation_fd >= 0:
            os.close(generation_fd)
        os.close(parent_fd)


def _open_directory(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _require_retained_generation_directories_private(parent: Path) -> None:
    """Recheck the host confidentiality boundary before creating another generation."""

    parent_fd = _open_directory(parent)
    try:
        for name in os.listdir(parent_fd):
            if _GENERATION_RE.fullmatch(name) is None:
                continue
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            try:
                _require_private_directory_metadata(os.fstat(descriptor), owner=os.geteuid())
            finally:
                os.close(descriptor)
    finally:
        os.close(parent_fd)


def _read_secret_file(
    path: Path,
    *,
    maximum: int,
    require_read_only_mount: bool,
) -> bytes:
    """Read one Compose-mounted secret; the stage service receives no other host mount."""

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or mode & 0o111
            or (require_read_only_mount and not os.fstatvfs(descriptor).f_flag & os.ST_RDONLY)
        ):
            raise OSError("compose-mounted secret metadata is unsafe")
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum or os.read(descriptor, 1):
            raise OSError("compose-mounted secret exceeds its size bound")
        named = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("compose-mounted secret changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _read_relative_file(root_fd: int, name: str, *, maximum: int, owner: int) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=root_fd,
    )
    try:
        opened = os.fstat(descriptor)
        _require_private_file_metadata(opened, owner=owner)
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum or os.read(descriptor, 1):
            raise OSError("compose private input exceeds its size bound")
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("compose private input changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _read_generation_file(root_fd: int, name: str, *, maximum: int, owner: int) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=root_fd,
    )
    try:
        opened = os.fstat(descriptor)
        _require_generation_file_metadata(opened, owner=owner)
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum or os.read(descriptor, 1):
            raise OSError("compose source secret exceeds its size bound")
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("compose source secret changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _open_or_create_state_directory(
    data_root: Path,
    name: str,
    *,
    allow_unmounted_for_test: bool,
) -> int:
    data_fd = _open_directory(data_root)
    try:
        if not allow_unmounted_for_test and not os.path.ismount(data_root):
            raise OSError("compose data root must be a mounted volume")
        try:
            os.mkdir(name, 0o700, dir_fd=data_fd)
        except FileExistsError:
            pass
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=data_fd,
        )
        _require_private_directory_metadata(os.fstat(descriptor), owner=os.geteuid())
        return descriptor
    finally:
        os.close(data_fd)


def _replace_relative_runtime_file(
    root_fd: int,
    name: str,
    payload: bytes,
) -> None:
    temporary = f".{name}.new-{secrets.token_hex(16)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=root_fd,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        _require_private_file_metadata(os.fstat(descriptor), owner=os.geteuid())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass


def _unlink_stale_runtime_file(root_fd: int, name: str) -> None:
    """Remove one known prior-generation file without reading or following its contents."""

    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    _require_private_file_metadata(metadata, owner=os.geteuid())
    os.unlink(name, dir_fd=root_fd)
    os.fsync(root_fd)


def _require_private_directory(path: Path, *, owner: int) -> None:
    metadata = path.lstat()
    if path.is_symlink():
        raise OSError("compose private directory must not be a symlink")
    _require_private_directory_metadata(metadata, owner=owner)


def _require_private_directory_metadata(metadata: os.stat_result, *, owner: int) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise OSError("compose private directory must be owner-only mode 0700")


def _require_private_file_metadata(metadata: os.stat_result, *, owner: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise OSError("compose private input must be owner-only mode 0600")


def _require_generation_file_metadata(metadata: os.stat_result, *, owner: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o444
        or metadata.st_nlink != 1
    ):
        raise OSError("compose source secret must be mode 0444 inside its owner-only directory")


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while preparing compose deployment")
        view = view[written:]


__all__ = [
    "COMPOSE_CONFIG_PATH",
    "COMPOSE_DESCRIPTOR_PATH",
    "COMPOSE_MANIFEST_PATH",
    "COMPOSE_MQTT_PASSWORD_PATH",
    "COMPOSE_QUALIFICATIONS_PATH",
    "COMPOSE_STATE_ROOT",
    "COMPOSE_VOLUME_ID_PATH",
    "LocalControlComposeSpec",
    "PreparedComposeArtifact",
    "StagedComposeInputs",
    "prepare_compose_deployment",
    "render_compose",
    "resolve_current_build_context",
    "stage_compose_inputs",
    "validate_current_compose_inputs",
]
