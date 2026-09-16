from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow import local_control_qualification_cli as cli
from jebao_flow.config import AppConfig
from jebao_flow.local_control.install import build_fresh_install_manifest
from jebao_flow.local_control.qualification_producer import (
    CurrentQualificationEpochSource,
    QualificationProductionError,
    _install_or_verify_private_file,
)
from jebao_flow.protocol.profiles import (
    AQUARIUM_PUMP,
    DC_PUMP_PRO,
    LOCAL_WAVEMAKER,
    LOCAL_WAVEMAKER_PRO,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def _config(
    *,
    mode: str = "observer",
    product_key: str = LOCAL_WAVEMAKER_PRO.product_key,
) -> AppConfig:
    return AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/test"},
            "runtime": {"mode": mode, "dry_run": True},
            "devices": [
                {
                    "id": "private_member",
                    "name": "Private Member",
                    "type": "wavemaker",
                    "product_key": product_key,
                    "discovery": "auto",
                    "identity": {
                        "device_id": "private-test-device",
                        "mac_address": "020000000001",
                    },
                    "limits": {"min_power": 30, "max_power": 75},
                }
            ],
        }
    )


def _source(
    config: AppConfig,
    *,
    template_digest: str = "a" * 64,
) -> CurrentQualificationEpochSource:
    manifest = build_fresh_install_manifest(
        config,
        role_attested_by="operator",
        role_attested_at=NOW,
        template_sha256=(template_digest,),
        now=NOW,
    )
    return CurrentQualificationEpochSource(
        config_path=Path("/private/config"),
        manifest_path=Path("/private/manifest"),
        template_roots=(Path("/private/template"),),
        logical_member="private_member",
        active_template_sha256=template_digest,
        config_loader=lambda _path: config,
        manifest_loader=lambda _path: manifest,
        template_hasher=lambda _roots: (template_digest,),
    )


def test_current_epoch_rederives_exact_config_manifest_and_content() -> None:
    source = _source(_config())

    epoch = source()

    assert epoch.target.logical_member == "private_member"
    assert epoch.min_power == 30
    assert epoch.max_power == 75
    assert epoch.app_version == "3.4.18"
    assert epoch.template_sha256 == "a" * 64
    assert len(epoch.config_sha256) == 64
    assert len(epoch.manifest_sha256) == 64
    assert "private_member" not in repr(source)


def test_current_epoch_accepts_the_product_scoped_bar_profile() -> None:
    source = _source(_config(product_key=LOCAL_WAVEMAKER.product_key))

    epoch = source()

    assert epoch.target.product_key == LOCAL_WAVEMAKER.product_key
    assert epoch.min_power == 30
    assert epoch.max_power == 75


def test_current_epoch_accepts_dc_without_inventing_a_linkage_role() -> None:
    source = _source(_config(product_key=DC_PUMP_PRO.product_key))
    assert source().target.product_key == DC_PUMP_PRO.product_key


def test_current_epoch_rejects_an_unsupported_manual_flow_family() -> None:
    source = _source(_config(product_key=AQUARIUM_PUMP.product_key))

    with pytest.raises(QualificationProductionError, match="family is unsupported"):
        source()


def test_current_epoch_rejects_control_mode_and_unbound_template() -> None:
    with pytest.raises(QualificationProductionError, match="observer runtime"):
        _source(_config(mode="control"))()

    source = _source(_config(), template_digest="a" * 64)
    mismatched = CurrentQualificationEpochSource(
        config_path=source.config_path,
        manifest_path=source.manifest_path,
        template_roots=source.template_roots,
        logical_member=source.logical_member,
        active_template_sha256="b" * 64,
        config_loader=source.config_loader,
        manifest_loader=source.manifest_loader,
        template_hasher=source.template_hasher,
    )
    with pytest.raises(QualificationProductionError, match="active template"):
        mismatched()


def test_private_file_install_is_idempotent_and_never_clobbers(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    payload = b"exact qualification evidence\n"

    _install_or_verify_private_file(root, "receipt.json", payload)
    _install_or_verify_private_file(root, "receipt.json", payload)

    target = root / "receipt.json"
    assert target.read_bytes() == payload
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(QualificationProductionError, match="differs"):
        _install_or_verify_private_file(root, "receipt.json", b"different\n")
    assert target.read_bytes() == payload


def test_private_file_install_rejects_symlinks_and_unsafe_metadata(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside\n")
    outside.chmod(0o600)
    (root / "receipt.json").symlink_to(outside)

    with pytest.raises(OSError):
        _install_or_verify_private_file(root, "receipt.json", b"expected\n")
    assert outside.read_bytes() == b"outside\n"

    (root / "receipt.json").unlink()
    target = root / "receipt.json"
    target.write_bytes(b"expected\n")
    target.chmod(0o644)
    with pytest.raises(QualificationProductionError, match="metadata"):
        _install_or_verify_private_file(root, "receipt.json", b"expected\n")


def test_parent_fsync_fault_is_not_success_and_exact_retry_can_finish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    payload = b"durable evidence\n"
    real_fsync = __import__("os").fsync
    calls = 0

    def _fault_on_parent(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected parent fsync fault")
        real_fsync(descriptor)

    monkeypatch.setattr(
        "jebao_flow.local_control.qualification_producer.os.fsync",
        _fault_on_parent,
    )
    with pytest.raises(OSError, match="injected"):
        _install_or_verify_private_file(root, "receipt.json", payload)

    monkeypatch.setattr(
        "jebao_flow.local_control.qualification_producer.os.fsync",
        real_fsync,
    )
    _install_or_verify_private_file(root, "receipt.json", payload)
    assert (root / "receipt.json").read_bytes() == payload


def test_cli_without_write_opt_in_stops_before_any_current_or_network_access(
    monkeypatch,
    capsys,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: touched.append("safety"),
    )
    private_values = (
        "/private/deployment/config.yaml",
        "/private/deployment/manifest.json",
        "/private/deployment/template",
        "/private/deployment/qualifications.json",
        "private_member",
    )

    result = cli.main(
        [
            "run",
            "--config",
            private_values[0],
            "--manifest",
            private_values[1],
            "--template-root",
            private_values[2],
            "--qualification-output",
            private_values[3],
            "--target",
            private_values[4],
            "--operation-id",
            "qualification_0001",
            "--downward-step",
            "1",
            "--active-template-sha256",
            hashlib.sha256(b"template").hexdigest(),
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert touched == []
    assert output.out == ""
    assert output.err == "jebao-flow-local-qualify: current_operation_failed_closed\n"
    assert all(value not in output.err for value in private_values)


def test_cli_redacts_unexpected_operation_errors(monkeypatch, capsys) -> None:
    private_value = "private_member_must_not_escape"

    def _fail_closed(_args) -> int:
        raise KeyError(private_value)

    monkeypatch.setattr(cli, "_run", _fail_closed)

    result = cli.main(
        [
            "recover",
            "--operation-id",
            "qualification_missing",
            "--write-enabled",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-local-qualify: current_operation_failed_closed\n"
    assert private_value not in output.err


@pytest.mark.parametrize("function", ["manual_frequency", "manual_mode"])
def test_bar_cli_without_opt_in_never_opens_runtime(function, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("no live or durable access before explicit opt-in")

    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", forbidden)
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", forbidden)
    assert cli.main([
        "run", "--function", function, "--config", "private.yaml",
        "--target", "bar", "--operation-id", "bar_cli_0001",
        "--active-template-sha256", "a" * 64,
        "--qualification-output", "private.json",
    ]) == 2
    assert "current_operation_failed_closed" in capsys.readouterr().err


@pytest.mark.parametrize("product,producer_name", [
    (LOCAL_WAVEMAKER.product_key, "BarScalarQualificationProducer"),
    (DC_PUMP_PRO.product_key, "DcScalarQualificationProducer"),
])
@pytest.mark.parametrize("function", ["manual_frequency", "manual_mode"])
def test_scalar_cli_delegates_to_product_producer(
    function, product, producer_name, monkeypatch, capsys,
):
    from types import SimpleNamespace

    calls = []

    class Producer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def execute(self, operation_id):
            calls.append(operation_id)
            return SimpleNamespace(public_dict=lambda: {"offline_wiring_test": True})

    monkeypatch.setattr(cli, producer_name, Producer)
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", lambda **kwargs: (
        lambda: SimpleNamespace(target=SimpleNamespace(product_key=product))
    ))
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for name in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: object())
    assert cli.main([
        "run", "--function", function, "--config", "private.yaml",
        "--target", "bar", "--operation-id", "bar_cli_0001",
        "--active-template-sha256", "a" * 64,
        "--template-root", "private-template", "--qualification-output", "private.json",
        "--write-enabled",
    ]) == 0
    assert calls[0]["function"] == function
    assert calls[1] == "bar_cli_0001"
    assert "offline_wiring_test" in capsys.readouterr().out
