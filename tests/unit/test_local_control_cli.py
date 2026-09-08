import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _binding(member: str, identity: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=identity * 64,
        logical_role=member,
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _manifest() -> FreshInstallManifest:
    return FreshInstallManifest(
        generation="0" * 32,
        rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
        devices=(_binding("master", "1"), _binding("slave", "2")),
        content=InstallContentBindings(
            app_catalog_sha256="3" * 64,
            protocol_schema_sha256="4" * 64,
        ),
    )


def test_catalog_command_is_ascii_and_covers_all_products(capsys) -> None:
    result = local_control_cli.main(["catalog"])

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert len(payload["products"]) == 5
    assert output.out.isascii()
    assert output.err == ""


def test_daily_dosing_plan_command_needs_no_old_install_or_device_state(capsys) -> None:
    result = local_control_cli.main(
        [
            "plan-dosing",
            "--channel",
            "3",
            "--daily-amount",
            "12",
            "--doses",
            "4",
            "--first-dose",
            "01:30",
            "--amount-step",
            "0.1",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert [entry["local_time"] for entry in payload["entries"]] == [
        "01:30",
        "07:30",
        "13:30",
        "19:30",
    ]
    assert [entry["amount"] for entry in payload["entries"]] == ["3", "3", "3", "3"]
    assert payload["device_acceptance"] == "unknown"
    assert output.out.isascii()


def test_daily_dosing_command_can_publish_digest_only_app_image_metadata(capsys) -> None:
    result = local_control_cli.main(
        [
            "plan-dosing",
            "--channel",
            "2",
            "--daily-amount",
            "12",
            "--doses",
            "4",
            "--amount-step",
            "1",
            "--app-image-preview",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    preview = payload["app_image_preview"]
    assert result == 0
    assert preview["datapoint"] == "CH2SWTime"
    assert preview["image_size"] == 96
    assert len(preview["image_sha256"]) == 64
    assert preview["wire_send_available"] is False
    assert "image" not in preview
    assert output.out.isascii()


def test_daily_dosing_command_rejects_fractional_app_image_without_rounding(capsys) -> None:
    result = local_control_cli.main(
        [
            "plan-dosing",
            "--channel",
            "1",
            "--daily-amount",
            "7.5",
            "--doses",
            "3",
            "--app-image-preview",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-local: current_operation_failed_closed\n"


def test_average_dosing_command_reports_half_ml_as_one_ml_every_two_days(capsys) -> None:
    result = local_control_cli.main(
        [
            "plan-average-dosing",
            "--channel",
            "1",
            "--daily-average",
            "0.5",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert payload["dose_amount"] == "1"
    assert payload["interval_days"] == 2
    assert payload["device_acceptance"] == "unknown"
    assert output.out.isascii()


def test_prepare_install_prints_only_fresh_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        local_control_cli,
        "_prepare_current",
        lambda args: SimpleNamespace(config=None, manifest=_manifest()),
    )

    result = local_control_cli.main(
        [
            "prepare-install",
            "--config",
            "/private/config",
            "--manifest",
            "/private/manifest",
            "--role-attested-by",
            "operator",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert payload["previous_values_reused"] is False
    assert payload["private_paths_exposed"] is False
    assert "/private" not in output.out
    assert output.err == ""


def test_explicit_schedule_pairs_bind_only_current_logical_members() -> None:
    manifest = _manifest()
    config = SimpleNamespace(groups=())

    [plan] = local_control_cli._schedule_plans(
        manifest,
        config,
        ("async:master:slave",),
    )

    assert plan.linkage_mode.value == "async"
    assert plan.master == manifest.devices[0]
    assert plan.slaves[0].target == manifest.devices[1]


def test_explicit_schedule_pairs_reject_two_roles_for_one_current_slave() -> None:
    manifest = _manifest()
    config = SimpleNamespace(groups=())

    try:
        local_control_cli._schedule_plans(
            manifest,
            config,
            ("sync:master:slave", "async:master:slave"),
        )
    except ValueError as error:
        assert "multiple current schedule topologies" in str(error)
    else:
        raise AssertionError("one slave was accepted in two current schedule topologies")


def test_missing_private_config_fails_without_echoing_a_path(monkeypatch, capsys) -> None:
    monkeypatch.delenv("JEBAO_FLOW_CONFIG", raising=False)
    result = local_control_cli.main(
        [
            "prepare-install",
            "--role-attested-by",
            "operator",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-local: current_operation_failed_closed\n"


def test_explicit_template_roots_replace_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("JEBAO_FLOW_APP_TEMPLATE_ROOTS", "/stale/template")
    parser = local_control_cli.build_parser()

    args = parser.parse_args(
        [
            "prepare-install",
            "--config",
            "/current/config",
            "--role-attested-by",
            "operator",
            "--template-root",
            "/current/template",
        ]
    )

    assert args.template_root == [local_control_cli.Path("/current/template")]


def test_production_cli_has_no_journal_root_override() -> None:
    parser = local_control_cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "serve",
                "--role-attested-by",
                "operator",
                "--journal-root",
                "/stale/root",
            ]
        )
