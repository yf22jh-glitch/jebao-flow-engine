import hashlib
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control import service
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
)
from jebao_flow.local_control.service import (
    HARDWARE_SAFETY_VOLUME_ID_FILE_ENV,
    LocalControlServiceSpec,
    read_current_hardware_safety_volume_id,
    read_current_mqtt_password,
    render_systemd_service,
    replace_prepared_service,
    require_current_mqtt_password_file,
    stable_role_binding_sha256,
)


def _spec(tmp_path: Path, *, write_enabled: bool = False) -> LocalControlServiceSpec:
    executable = tmp_path / "bin" / "jebao-flow-local"
    executable.parent.mkdir(mode=0o700, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="ascii")
    executable.chmod(0o700)
    config = tmp_path / "private config.yaml"
    config.write_text("current\n", encoding="ascii")
    config.chmod(0o600)
    manifest = tmp_path / "current manifest.json"
    hardware_safety_volume_id_file = tmp_path / "hardware-safety-volume-id"
    template = tmp_path / "current template"
    template.mkdir(mode=0o700, exist_ok=True)
    return LocalControlServiceSpec(
        executable=executable.resolve(),
        config=config.resolve(),
        manifest=manifest.resolve(),
        hardware_safety_volume_id_file=hardware_safety_volume_id_file.resolve(),
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
        role_binding_sha256="a" * 64,
        template_roots=(template.resolve(),),
        write_enabled=write_enabled,
    )


def test_rendered_service_uses_current_values_and_keeps_write_disabled_by_default(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    payload = render_systemd_service(spec).decode("utf-8")

    assert "ExecStart=" in payload
    assert " serve " in payload
    assert "--template-root" in payload
    assert "--write-enabled" not in payload
    assert 'Environment="JEBAO_FLOW_LOCAL_QUALIFICATIONS="' in payload
    assert 'Environment="JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID="' in payload
    assert f'Environment="{HARDWARE_SAFETY_VOLUME_ID_FILE_ENV}=' in payload
    assert str(spec.hardware_safety_volume_id_file) in payload
    assert "0" * 32 not in payload
    assert 'Environment="JEBAO_FLOW_APP_TEMPLATE_ROOTS="' in payload
    assert 'Environment="JEBAO_FLOW_ROLE_ATTESTED_AT=2026-09-08T00:00:00+00:00"' in payload
    assert 'Environment="JEBAO_FLOW_ROLE_BINDING_SHA256=' + "a" * 64 + '"' in payload
    assert 'Environment="JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE="' in payload
    assert "EnvironmentFile=" not in payload
    assert "systemctl" not in payload
    assert "RequiresMountsFor=/hardware-safety" in payload
    assert str(spec.hardware_safety_volume_id_file) not in repr(spec)


def test_write_opt_in_is_present_only_when_explicitly_selected(tmp_path: Path) -> None:
    default = render_systemd_service(_spec(tmp_path, write_enabled=False))
    selected = render_systemd_service(_spec(tmp_path, write_enabled=True))

    assert b"--write-enabled" not in default
    assert b"--write-enabled" in selected


def test_systemd_environment_preserves_literal_dollar_and_escapes_specifiers(
    tmp_path: Path,
) -> None:
    spec = replace(
        _spec(tmp_path),
        role_attested_by="operator$now%here",
    )

    payload = render_systemd_service(spec).decode("utf-8")

    assert 'Environment="JEBAO_FLOW_ROLE_ATTESTED_BY=operator$now%%here"' in payload
    assert "--role-attested-by ${JEBAO_FLOW_ROLE_ATTESTED_BY}" in payload


def test_service_replacement_never_reads_or_merges_old_unit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "durable_private_path",
        lambda value, description: Path(value).resolve(),
    )
    destination = tmp_path / "staging" / "jebao-flow-local.service"
    destination.parent.mkdir(mode=0o700)
    destination.write_text("stale private path and invalid old unit\n", encoding="ascii")
    destination.chmod(0o600)
    spec = _spec(tmp_path)
    spec.hardware_safety_volume_id_file.write_text("f" * 32 + "\n", encoding="ascii")
    spec.hardware_safety_volume_id_file.chmod(0o600)

    artifact = replace_prepared_service(
        destination,
        spec,
        hardware_safety_volume_id="0" * 32,
    )

    expected = render_systemd_service(spec)
    assert destination.read_bytes() == expected
    assert "stale private path" not in destination.read_text(encoding="utf-8")
    assert artifact.unit_sha256 == hashlib.sha256(expected).hexdigest()
    assert artifact.public_dict()["previous_unit_values_reused"] is False
    assert artifact.public_dict()["private_paths_exposed"] is False
    assert artifact.public_dict()["hardware_safety_identity_file_replaced"] is True
    assert spec.hardware_safety_volume_id_file.read_text(encoding="ascii") == "0" * 32 + "\n"
    assert stat.S_IMODE(spec.hardware_safety_volume_id_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert list(destination.parent.glob(".*.new-*")) == []


def test_service_references_current_password_file_without_embedding_secret(tmp_path: Path) -> None:
    password_file = tmp_path / "mqtt-password"
    password_file.write_text("current-secret\n", encoding="ascii")
    password_file.chmod(0o600)
    spec = replace(_spec(tmp_path), mqtt_password_file=password_file.resolve())

    payload = render_systemd_service(spec).decode("utf-8")

    assert "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE=" in payload
    assert str(password_file.resolve()) in payload
    assert "current-secret" not in payload
    assert "EnvironmentFile=" not in payload


def test_current_password_file_is_owner_only_and_read_exactly_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "durable_private_path",
        lambda value, description: Path(value).resolve(),
    )
    password_file = tmp_path / "mqtt-password"
    password_file.write_bytes(b"current-secret\n")
    password_file.chmod(0o600)

    assert require_current_mqtt_password_file(password_file) == password_file.resolve()
    assert read_current_mqtt_password(password_file) == "current-secret"

    password_file.write_bytes(b"two\nvalues\n")
    with pytest.raises(ValueError, match="one value"):
        read_current_mqtt_password(password_file)

    password_file.write_text("current-secret\n", encoding="ascii")
    password_file.chmod(0o644)
    with pytest.raises(OSError, match="0600"):
        read_current_mqtt_password(password_file)


def test_current_hardware_safety_identity_file_is_owner_only_and_exact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "durable_private_path",
        lambda value, description: Path(value).resolve(),
    )
    identity_file = tmp_path / "hardware-safety-volume-id"
    identity_file.write_text("1" * 32 + "\n", encoding="ascii")
    identity_file.chmod(0o600)

    assert read_current_hardware_safety_volume_id(identity_file) == "1" * 32

    identity_file.write_text("1" * 31 + "\n", encoding="ascii")
    with pytest.raises(ValueError, match="lowercase 32-hex"):
        read_current_hardware_safety_volume_id(identity_file)

    identity_file.write_text("1" * 32 + "\n", encoding="ascii")
    identity_file.chmod(0o644)
    with pytest.raises(OSError, match="0600"):
        read_current_hardware_safety_volume_id(identity_file)


def test_service_destination_cannot_replace_a_current_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service,
        "durable_private_path",
        lambda value, description: Path(value).resolve(),
    )
    spec = _spec(tmp_path)
    original = spec.config.read_bytes()

    with pytest.raises(ValueError, match="must not replace"):
        replace_prepared_service(
            spec.config,
            spec,
            hardware_safety_volume_id="0" * 32,
        )

    assert spec.config.read_bytes() == original


def test_role_binding_uses_only_stable_current_role_facts() -> None:
    first = LocalDeviceBinding(
        logical_member="master",
        product_key="1" * 32,
        physical_identity_key="2" * 64,
        logical_role="master",
        role_attested_by="operator_a",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    content = InstallContentBindings(
        app_catalog_sha256="3" * 64,
        protocol_schema_sha256="4" * 64,
    )
    original = FreshInstallManifest(
        generation="5" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(first,),
        content=content,
    )
    provenance_changed = original.model_copy(
        update={
            "generation": "6" * 32,
            "rederived_at": datetime(2026, 9, 9, tzinfo=UTC),
            "devices": (
                first.model_copy(
                    update={
                        "role_attested_by": "operator_b",
                        "role_attested_at": datetime(2026, 9, 9, tzinfo=UTC),
                    }
                ),
            ),
        }
    )
    role_changed = original.model_copy(
        update={"devices": (first.model_copy(update={"logical_role": "slave"}),)}
    )

    assert stable_role_binding_sha256(original) == stable_role_binding_sha256(provenance_changed)
    assert stable_role_binding_sha256(original) != stable_role_binding_sha256(role_changed)
