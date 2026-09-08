from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from jebao_flow.config import AppConfig
from jebao_flow.local_control import compose
from jebao_flow.local_control.compose import (
    LocalControlComposeSpec,
    prepare_compose_deployment,
    render_compose,
    resolve_current_build_context,
    stage_compose_inputs,
    validate_current_compose_inputs,
)
from jebao_flow.local_control.models import QualificationBinding
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _config(*, password: bool = False) -> AppConfig:
    # Synthetic scanner-hygiene fixture only. Real identifiers never belong in source.
    synthetic_mac = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {
                "host": "broker.invalid",
                "topic_prefix": "jebao-flow/test",
                "password_env": "CURRENT_MQTT_PASSWORD" if password else None,
            },
            "devices": [
                {
                    "id": "wavemaker_left",
                    "name": "Left Wavemaker",
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "discovery": "auto",
                    "identity": {
                        "device_id": "synthetic-device-id",
                        "mac_address": synthetic_mac,
                    },
                }
            ],
        }
    )


def _spec(
    tmp_path: Path,
    generation: str,
    *,
    password: bool = False,
    write_enabled: bool = False,
    qualification: bool = False,
) -> LocalControlComposeSpec:
    build = tmp_path / "current build"
    build.mkdir(mode=0o700, exist_ok=True)
    (build / "Dockerfile").write_text("FROM scratch\n", encoding="ascii")
    (build / "pyproject.toml").write_text("[project]\n", encoding="ascii")
    template = tmp_path / "current$template"
    template.mkdir(mode=0o700, exist_ok=True)
    return LocalControlComposeSpec(
        build_context=build.resolve(),
        input_generation_root=(tmp_path / "bundles" / generation).resolve(),
        generation=generation,
        role_attested_by="on_site$operator",
        role_attested_at="2026-09-08T00:00:00+00:00",
        role_binding_sha256="b" * 64,
        template_roots=(template.resolve(),),
        mqtt_password_configured=password,
        qualification_configured=qualification,
        write_enabled=write_enabled,
    )


def _qualification() -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function="manual_flow",
        app_version="3.4.18",
        app_catalog_sha256="1" * 64,
        template_sha256="2" * 64,
        schema_sha256="3" * 64,
        firmware_version="current",
        receipt_sha256=("4" * 64, "5" * 64),
    )


def _patch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compose,
        "durable_private_path",
        lambda value, description: Path(value).resolve(),
    )


def test_rendered_compose_is_standalone_nonroot_and_uses_shared_rw_locks(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path, "1" * 32)
    payload = render_compose(spec)
    model = yaml.safe_load(payload)
    stage = model["services"]["jebao-flow-local-stage"]
    runtime = model["services"]["jebao-flow-local"]

    assert set(model["services"]) == {"jebao-flow-local-stage", "jebao-flow-local"}
    assert "user" not in stage
    assert "user" not in runtime
    assert stage["network_mode"] == "none"
    assert stage["cap_drop"] == ["ALL"]
    assert "cap_add" not in stage
    assert stage["volumes"] == [
        {
            "type": "volume",
            "source": "local_control_data",
            "target": "/data",
            "read_only": False,
        }
    ]
    assert all("mode" not in secret for secret in stage["secrets"])
    assert runtime["network_mode"] == "host"
    safety = next(
        mount for mount in runtime["volumes"] if mount["source"] == "hardware_safety"
    )
    data = next(
        mount for mount in runtime["volumes"] if mount["source"] == "local_control_data"
    )
    assert safety == {
        "type": "volume",
        "source": "hardware_safety",
        "target": "/hardware-safety",
        "read_only": False,
    }
    assert data["read_only"] is False
    assert model["volumes"]["hardware_safety"] == {
        "external": True,
        "name": "jebao-flow-hardware-safety",
    }
    assert runtime["depends_on"]["jebao-flow-local-stage"]["condition"] == (
        "service_completed_successfully"
    )
    assert runtime["command"][:3] == [
        "serve",
        "--compose-generation",
        "1" * 32,
    ]
    assert "JEBAO_FLOW_LOCAL_COMPOSE_GENERATION" not in runtime["environment"]
    assert "jebao-flowd" not in payload.decode("ascii")
    assert "receipt" not in payload.decode("ascii")
    assert "on_site$$operator" in payload.decode("ascii")


def test_prepare_compose_replaces_output_and_prunes_only_old_generated_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    output = tmp_path / "private" / "compose.yaml"
    output.parent.mkdir(mode=0o700)
    output.write_text("stale compose bytes\n", encoding="ascii")
    output.chmod(0o600)
    bundle_root = tmp_path / "bundles"
    unowned_name = bundle_root / "notes"

    first_spec = _spec(tmp_path, "1" * 32, password=True)
    first = prepare_compose_deployment(
        output,
        bundle_root,
        first_spec,
        config=_config(password=True),
        hardware_safety_volume_id="2" * 32,
        mqtt_password="current-secret",
        qualifications=None,
    )
    unowned_name.write_text("leave this entry alone\n", encoding="ascii")
    second_spec = _spec(tmp_path, "3" * 32, password=True)
    second = prepare_compose_deployment(
        output,
        bundle_root,
        second_spec,
        config=_config(password=True),
        hardware_safety_volume_id="4" * 32,
        mqtt_password="new-secret",
        qualifications=None,
    )

    assert first.pruned_generation_count == 0
    assert second.pruned_generation_count == 1
    assert not (bundle_root / ("1" * 32)).exists()
    assert (bundle_root / ("3" * 32)).is_dir()
    assert unowned_name.read_text(encoding="ascii") == "leave this entry alone\n"
    generation = bundle_root / ("3" * 32)
    assert (generation / "mqtt-password").read_text(encoding="utf-8") == "new-secret\n"
    assert "new-secret" not in output.read_text(encoding="ascii")
    assert "4" * 32 not in output.read_text(encoding="ascii")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(generation.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o444 for path in generation.iterdir())
    descriptor = json.loads((generation / "bundle.json").read_text(encoding="ascii"))
    assert descriptor["generation"] == "3" * 32
    assert descriptor["files"]["config.yaml"] == hashlib.sha256(
        (generation / "config.yaml").read_bytes()
    ).hexdigest()
    assert second.public_dict()["private_paths_exposed"] is False
    assert second.public_dict()["previous_bundle_values_reused"] is False


@pytest.mark.parametrize("descriptor_present", [True, False])
def test_prepare_compose_recovers_an_interrupted_prior_generation_prune(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor_present: bool,
) -> None:
    _patch_paths(monkeypatch)
    output = tmp_path / "private" / "compose.yaml"
    bundle_root = tmp_path / "bundles"
    first_generation = "1" * 32
    prepare_compose_deployment(
        output,
        bundle_root,
        _spec(tmp_path, first_generation, password=True),
        config=_config(password=True),
        hardware_safety_volume_id="2" * 32,
        mqtt_password="old-secret",
        qualifications=None,
    )
    interrupted = bundle_root / first_generation
    (interrupted / "config.yaml").unlink()
    if not descriptor_present:
        (interrupted / "bundle.json").unlink()

    second = prepare_compose_deployment(
        output,
        bundle_root,
        _spec(tmp_path, "3" * 32),
        config=_config(),
        hardware_safety_volume_id="4" * 32,
        mqtt_password=None,
        qualifications=None,
    )

    assert second.pruned_generation_count == 1
    assert not interrupted.exists()


def test_prepare_compose_rechecks_every_retained_generation_confidentiality_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    output = tmp_path / "private" / "compose.yaml"
    bundle_root = tmp_path / "bundles"
    generation = "5" * 32
    prepare_compose_deployment(
        output,
        bundle_root,
        _spec(tmp_path, generation),
        config=_config(),
        hardware_safety_volume_id="6" * 32,
        mqtt_password=None,
        qualifications=None,
    )
    original_output = output.read_bytes()
    (bundle_root / generation).chmod(0o755)

    with pytest.raises(OSError, match="owner-only mode 0700"):
        prepare_compose_deployment(
            output,
            bundle_root,
            _spec(tmp_path, "7" * 32),
            config=_config(),
            hardware_safety_volume_id="8" * 32,
            mqtt_password=None,
            qualifications=None,
        )

    assert output.read_bytes() == original_output
    assert not (bundle_root / ("7" * 32)).exists()


def test_prepare_compose_serializes_replacement_and_pruning_in_one_bundle_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir(mode=0o700)
    lock = compose._acquire_preparation_lock(bundle_root)
    try:
        with pytest.raises(BlockingIOError):
            prepare_compose_deployment(
                tmp_path / "private" / "compose.yaml",
                bundle_root,
                _spec(tmp_path, "9" * 32),
                config=_config(),
                hardware_safety_volume_id="a" * 32,
                mqtt_password=None,
                qualifications=None,
            )
    finally:
        os.close(lock)

    assert stat.S_IMODE((bundle_root / ".prepare.lock").stat().st_mode) == 0o600
    assert not (bundle_root / ("9" * 32)).exists()


def test_stage_copies_verified_secrets_as_current_user_and_runtime_revalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    generation = "5" * 32
    bundle_root = tmp_path / "bundles"
    spec = _spec(tmp_path, generation, password=True)
    prepare_compose_deployment(
        tmp_path / "private" / "compose.yaml",
        bundle_root,
        spec,
        config=_config(password=True),
        hardware_safety_volume_id="6" * 32,
        mqtt_password="runtime-secret",
        qualifications=None,
    )
    source = bundle_root / generation
    mounted = tmp_path / "mounted-secrets"
    mounted.mkdir()
    secret_paths: dict[str, Path] = {}
    for name in ("bundle.json", "config.yaml", "hardware-safety-volume-id", "mqtt-password"):
        target = mounted / name
        target.write_bytes((source / name).read_bytes())
        target.chmod(0o444)
        secret_paths[name] = target
    data = tmp_path / "data"
    data.mkdir()

    result = stage_compose_inputs(
        generation,
        state_root=data / "local-control",
        allow_unmounted_for_test=True,
        secret_paths_for_test=secret_paths,
    )

    state = data / "local-control"
    assert result.file_count == 3
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in state.iterdir())
    validate_current_compose_inputs(generation, state_root=state)
    (state / "config.yaml").write_bytes(b"changed\n")
    (state / "config.yaml").chmod(0o600)
    with pytest.raises(OSError, match="digest mismatch"):
        validate_current_compose_inputs(generation, state_root=state)


def test_write_bundle_contains_only_receipt_bindings_not_receipt_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    generation = "9" * 32
    bundle_root = tmp_path / "bundles"
    spec = _spec(
        tmp_path,
        generation,
        write_enabled=True,
        qualification=True,
    )

    prepare_compose_deployment(
        tmp_path / "private" / "compose.yaml",
        bundle_root,
        spec,
        config=_config(),
        hardware_safety_volume_id="a" * 32,
        mqtt_password=None,
        qualifications=(_qualification(),),
    )

    names = {path.name for path in (bundle_root / generation).iterdir()}
    assert names == {
        "bundle.json",
        "config.yaml",
        "hardware-safety-volume-id",
        "qualifications.json",
    }
    assert not any(name.startswith("local-control-receipt-") for name in names)
    payload = json.loads(
        (bundle_root / generation / "qualifications.json").read_text(encoding="ascii")
    )
    assert payload["qualifications"][0]["receipt_sha256"] == ["4" * 64, "5" * 64]


def test_new_stage_removes_known_prior_generation_values_instead_of_reusing_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    bundle_root = tmp_path / "bundles"
    state = tmp_path / "data" / "local-control"
    state.parent.mkdir()

    generations = (("a" * 32, True), ("b" * 32, False))
    for generation, password in generations:
        spec = _spec(tmp_path, generation, password=password)
        prepare_compose_deployment(
            tmp_path / "private" / "compose.yaml",
            bundle_root,
            spec,
            config=_config(password=password),
            hardware_safety_volume_id="c" * 32,
            mqtt_password="old-secret" if password else None,
            qualifications=None,
        )
        source = bundle_root / generation
        mounted = tmp_path / f"mounted-{generation[0]}"
        mounted.mkdir()
        secret_paths: dict[str, Path] = {}
        for source_file in source.iterdir():
            target = mounted / source_file.name
            target.write_bytes(source_file.read_bytes())
            target.chmod(0o444)
            secret_paths[source_file.name] = target
        stage_compose_inputs(
            generation,
            state_root=state,
            allow_unmounted_for_test=True,
            secret_paths_for_test=secret_paths,
        )
        if password:
            (state / "install.json").write_text("old manifest bytes\n", encoding="ascii")
            (state / "install.json").chmod(0o600)

    assert not (state / "mqtt-password").exists()
    assert not (state / "qualifications.json").exists()
    assert not (state / "install.json").exists()
    validate_current_compose_inputs("b" * 32, state_root=state)


def test_stage_rejects_changed_secret_before_creating_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    generation = "7" * 32
    bundle_root = tmp_path / "bundles"
    spec = _spec(tmp_path, generation)
    prepare_compose_deployment(
        tmp_path / "private" / "compose.yaml",
        bundle_root,
        spec,
        config=_config(),
        hardware_safety_volume_id="8" * 32,
        mqtt_password=None,
        qualifications=None,
    )
    source = bundle_root / generation
    mounted = tmp_path / "mounted-secrets"
    mounted.mkdir()
    secret_paths = {}
    for name in ("bundle.json", "config.yaml", "hardware-safety-volume-id"):
        target = mounted / name
        target.write_bytes((source / name).read_bytes())
        target.chmod(0o444)
        secret_paths[name] = target
    secret_paths["config.yaml"].chmod(0o544)
    data = tmp_path / "data"
    data.mkdir()

    with pytest.raises(OSError, match="metadata is unsafe"):
        stage_compose_inputs(
            generation,
            state_root=data / "local-control",
            allow_unmounted_for_test=True,
            secret_paths_for_test=secret_paths,
        )

    assert not (data / "local-control").exists()


def test_current_build_context_is_freshly_found_from_current_working_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(monkeypatch)
    root = tmp_path / "checkout"
    child = root / "nested"
    child.mkdir(parents=True)
    (root / "Dockerfile").write_text("FROM scratch\n", encoding="ascii")
    (root / "pyproject.toml").write_text("[project]\n", encoding="ascii")
    monkeypatch.chdir(child)

    assert resolve_current_build_context() == root.resolve()
