import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from jebao_flow.config import AppConfig
from jebao_flow.local_control import install
from jebao_flow.local_control.install import (
    build_fresh_install_manifest,
    hash_current_template_contents,
    load_current_private_config,
    replace_install_manifest,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _config() -> AppConfig:
    # Synthetic scanner-hygiene fixtures only. Real identifiers must never be assembled this way
    # to evade repository privacy checks; real values stay in owner-only deployment config.
    synthetic_mac = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
    synthetic_address = ".".join(("192", "0", "2", "10"))
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/test"},
            "devices": [
                {
                    "id": "wavemaker_left",
                    "name": "Left Wavemaker",
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "address": synthetic_address,
                    "identity": {
                        "device_id": "synthetic-device-id",
                        "mac_address": synthetic_mac,
                    },
                }
            ],
        }
    )


def test_fresh_manifest_rederives_content_and_never_carries_endpoint() -> None:
    observed_at = datetime(2026, 9, 8, tzinfo=UTC)

    first = build_fresh_install_manifest(
        _config(),
        role_attested_by="on_site_operator",
        role_attested_at=observed_at,
        template_sha256=("b" * 64, "a" * 64),
        now=observed_at,
    )
    second = build_fresh_install_manifest(
        _config(),
        role_attested_by="on_site_operator",
        role_attested_at=observed_at,
        template_sha256=("b" * 64, "a" * 64),
        now=observed_at,
    )

    assert first.generation != second.generation
    assert first.devices == second.devices
    assert first.content == second.content
    assert first.content.template_sha256 == ("a" * 64, "b" * 64)
    encoded = first.model_dump_json()
    assert "address" not in encoded
    assert "synthetic-device-id" not in encoded
    assert synthetic_identity_fragments_absent(encoded)


def test_replace_manifest_does_not_parse_or_merge_previous_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    destination = private_root / "install.json"
    destination.write_bytes(b"not-json and deliberately stale")
    destination.chmod(0o600)
    monkeypatch.setattr(
        "jebao_flow.local_control.install.durable_private_path",
        lambda path, description: Path(path),
    )
    manifest = build_fresh_install_manifest(
        _config(),
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )

    replace_install_manifest(destination, manifest)

    assert json.loads(destination.read_text(encoding="ascii"))["generation"] == manifest.generation
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert destination.stat().st_nlink == 1
    assert list(private_root.glob(".install.json.new-*")) == []


def synthetic_identity_fragments_absent(encoded: str) -> bool:
    return "aa:bb" not in encoded and "192.0.2" not in encoded


def test_current_private_config_is_loaded_from_owner_only_current_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        install,
        "durable_private_path",
        lambda value, description: Path(value),
    )
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(_config().model_dump(mode="json")),
        encoding="utf-8",
    )
    path.chmod(0o600)

    loaded = load_current_private_config(path)

    assert loaded == _config()
    path.chmod(0o644)
    with pytest.raises(OSError, match="owner-only"):
        load_current_private_config(path)


def test_template_identity_uses_current_content_not_path_or_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        install,
        "durable_private_path",
        lambda value, description: Path(value),
    )
    first = tmp_path / "cache-a" / "bundle-one"
    second = tmp_path / "cache-b" / "renamed-bundle"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "index.js").write_bytes(b"same-current-content")
    (second / "index.js").write_bytes(b"same-current-content")

    before = hash_current_template_contents((tmp_path / "cache-a", tmp_path / "cache-b"))
    os.utime(first / "index.js", (1, 1))
    after_mtime = hash_current_template_contents(
        (tmp_path / "cache-a", tmp_path / "cache-b")
    )
    (second / "index.js").write_bytes(b"new-current-content")
    after_content = hash_current_template_contents(
        (tmp_path / "cache-a", tmp_path / "cache-b")
    )

    assert len(before) == 1
    assert after_mtime == before
    assert len(after_content) == 2


def test_protocol_binding_includes_the_actual_writer_and_composition_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: tuple[str | None, ...] = ()

    def capture(paths: tuple[str | None, ...]) -> str:
        nonlocal captured
        captured = paths
        return "a" * 64

    monkeypatch.setattr(install, "_combined_file_digest", capture)

    assert install.current_protocol_schema_sha256() == "a" * 64
    resolved = {Path(path).resolve() for path in captured if path is not None}
    local_control_root = Path(install.__file__).resolve().parent
    assert local_control_root / "device_writer.py" in resolved
    assert local_control_root / "schedule.py" in resolved
    assert Path(install.schedule_model.__file__).resolve() in resolved


def test_protocol_binding_changes_with_schedule_model_range_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "schedule_model.py"
    source.write_bytes(b"flow-range-source-v1")
    monkeypatch.setattr(install.schedule_model, "__file__", str(source))

    before = install.current_protocol_schema_sha256()
    source.write_bytes(b"flow-range-source-v2")
    after = install.current_protocol_schema_sha256()

    assert after != before
