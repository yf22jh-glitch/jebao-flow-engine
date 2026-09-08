import json
from pathlib import Path

import pytest

from jebao_flow.operator_capture import install_prep
from jebao_flow.operator_capture.install_prep import (
    CaptureInstallMode,
    InstallPreparationError,
    prepare_fresh_install,
)


def _allow_test_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install_prep,
        "durable_private_path",
        lambda value, _description: Path(value),
    )


def test_prepare_install_replaces_old_bytes_without_reading_or_merging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_paths(monkeypatch)
    config = tmp_path / "private-config"
    config.mkdir(mode=0o700)
    manifest = config / "operator-install.json"
    manifest.write_text('{"stale_interface":"old0","stale_secret":"must-not-survive"}\n')
    manifest.chmod(0o600)
    root = tmp_path / "private-state" / "captures"

    first = prepare_fresh_install(
        manifest_path=manifest,
        capture_root=root,
        capture_mode=CaptureInstallMode.PASSIVE_MIRROR,
        observed_interfaces=("mirror0",),
    )
    first_payload = json.loads(manifest.read_text())
    second = prepare_fresh_install(
        manifest_path=manifest,
        capture_root=root,
        capture_mode=CaptureInstallMode.OFFLINE_PCAP,
        observed_interfaces=("ignored0",),
    )
    second_payload = json.loads(manifest.read_text())

    assert "stale_interface" not in first_payload
    assert "stale_secret" not in first_payload
    assert first_payload["previous_values_reused"] is False
    assert first_payload["capture"]["mirror_interface"]["value"] == "mirror0"
    assert second_payload["capture"]["mirror_interface"]["value"] is None
    assert first.generation != second.generation
    assert first.manifest_sha256 != second.manifest_sha256
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700


def test_only_stable_values_are_declared_as_mapping_basis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_paths(monkeypatch)
    manifest = tmp_path / "config" / "operator-install.json"
    prepare_fresh_install(
        manifest_path=manifest,
        capture_root=tmp_path / "state" / "captures",
        capture_mode=CaptureInstallMode.OFFLINE_PCAP,
        observed_interfaces=("en0",),
    )

    payload = json.loads(manifest.read_text())
    assert payload["stable_binding"] == {
        "capability": "product_key_and_verified_wire_layout",
        "content": "sha256",
        "device": "physical_identity_key",
        "role": "operator_assigned_logical_role",
    }
    assert payload["protocol_constants"]["control_port"] == 12416
    assert "ephemeral_source_port" in payload["volatile_values_are_not_binding"]
    assert "tcp_port" not in payload["volatile_values_are_not_binding"]
    assert payload["language"] == "en"


def test_offline_install_does_not_require_an_unused_network_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_paths(monkeypatch)

    def unexpected_observation() -> tuple[str, ...]:
        raise AssertionError("offline preparation must not inspect an unused interface")

    monkeypatch.setattr(install_prep, "current_interface_names", unexpected_observation)

    prepared = prepare_fresh_install(
        manifest_path=tmp_path / "config" / "operator-install.json",
        capture_root=tmp_path / "state" / "captures",
        capture_mode=CaptureInstallMode.OFFLINE_PCAP,
    )

    assert prepared.mirror_interface is None


def test_passive_mirror_uses_fresh_current_interface_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_paths(monkeypatch)
    prepared = prepare_fresh_install(
        manifest_path=tmp_path / "config" / "operator-install.json",
        capture_root=tmp_path / "state" / "captures",
        capture_mode=CaptureInstallMode.PASSIVE_MIRROR,
        mirror_interface="span1",
        observed_interfaces=("span0", "span1"),
    )

    assert prepared.mirror_interface == "span1"


def test_ambiguous_current_interfaces_require_current_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_paths(monkeypatch)
    with pytest.raises(InstallPreparationError, match="current install-time observation"):
        prepare_fresh_install(
            manifest_path=tmp_path / "config" / "operator-install.json",
            capture_root=tmp_path / "state" / "captures",
            capture_mode=CaptureInstallMode.PASSIVE_MIRROR,
            observed_interfaces=("span0", "span1"),
        )


@pytest.mark.parametrize("interface", ["", "span 0", "mirror\n0", "\ud14c\uc2a4\ud2b8"])
def test_current_interface_names_must_be_safe_english_tokens(
    interface: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_paths(monkeypatch)
    with pytest.raises(InstallPreparationError, match="unsafe public name"):
        prepare_fresh_install(
            manifest_path=tmp_path / "config" / "operator-install.json",
            capture_root=tmp_path / "state" / "captures",
            capture_mode=CaptureInstallMode.PASSIVE_MIRROR,
            observed_interfaces=(interface,),
        )
