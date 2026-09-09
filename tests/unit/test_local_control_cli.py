import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.config import AppConfig
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
    assert payload["template_binding_complete"] is False
    assert payload["qualification_status"] == (
        "no_current_template_binding_controls_remain_diagnostic"
    )
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


def test_prepare_service_uses_only_current_inputs_and_keeps_write_off_by_default(
    monkeypatch,
    capsys,
) -> None:
    current = SimpleNamespace(
        config=SimpleNamespace(mqtt=SimpleNamespace(password_env=None)),
        manifest=_manifest(),
        config_path=local_control_cli.Path("/current/private/config.yaml"),
        manifest_path=local_control_cli.Path("/current/private/install.json"),
        template_roots=(local_control_cli.Path("/current/private/templates"),),
    )
    captured = {}
    monkeypatch.setenv("JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID", "0" * 32)
    monkeypatch.setenv(
        "JEBAO_FLOW_LOCAL_QUALIFICATIONS",
        "/stale/missing/qualification",
    )
    monkeypatch.setattr(local_control_cli, "_prepare_current", lambda args: current)
    monkeypatch.setattr(
        local_control_cli,
        "load_current_qualifications",
        lambda path: (_ for _ in ()).throw(
            AssertionError("read-only service inspected write qualification")
        ),
    )
    monkeypatch.setattr(
        local_control_cli,
        "resolve_current_executable",
        lambda path: local_control_cli.Path("/current/bin/jebao-flow-local"),
    )

    class _Artifact:
        def public_dict(self):
            return {
                "status": "service_prepared",
                "previous_unit_values_reused": False,
                "private_paths_exposed": False,
            }

    def replace(output, spec, *, hardware_safety_volume_id):
        captured["output"] = output
        captured["spec"] = spec
        captured["hardware_safety_volume_id"] = hardware_safety_volume_id
        return _Artifact()

    monkeypatch.setattr(local_control_cli, "replace_prepared_service", replace)

    result = local_control_cli.main(
        [
            "prepare-service",
            "--config",
            "/current/private/config.yaml",
            "--manifest",
            "/current/private/install.json",
            "--role-attested-by",
            "operator",
            "--output",
            "/current/private/jebao-flow-local.service",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert captured["spec"].write_enabled is False
    assert captured["hardware_safety_volume_id"] == "0" * 32
    assert captured["spec"].hardware_safety_volume_id_file == local_control_cli.Path(
        "/current/private/.jebao-flow-local.service.hardware-safety-volume-id"
    )
    assert captured["spec"].qualifications is None
    assert captured["spec"].template_roots == current.template_roots
    assert captured["spec"].mqtt_password_file is None
    assert captured["spec"].role_attested_at == current.manifest.devices[0].role_attested_at
    assert len(captured["spec"].role_binding_sha256) == 64
    assert payload["previous_unit_values_reused"] is False
    assert "/current/private" not in output.out
    assert output.err == ""


def test_prepare_service_requires_a_current_password_file_when_config_names_password_env(
    monkeypatch,
    capsys,
) -> None:
    current = SimpleNamespace(
        config=SimpleNamespace(mqtt=SimpleNamespace(password_env="CURRENT_MQTT_PASSWORD")),
        manifest=_manifest(),
        config_path=local_control_cli.Path("/current/private/config.yaml"),
        manifest_path=local_control_cli.Path("/current/private/install.json"),
        template_roots=(),
    )
    monkeypatch.setattr(local_control_cli, "_prepare_current", lambda args: current)
    monkeypatch.setattr(local_control_cli, "load_current_qualifications", lambda path: ())

    result = local_control_cli.main(
        [
            "prepare-service",
            "--config",
            "/current/private/config.yaml",
            "--role-attested-by",
            "operator",
            "--output",
            "/current/private/jebao-flow-local.service",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-local: current_operation_failed_closed\n"


def test_prepare_service_ignores_stale_password_path_when_current_config_has_no_password(
    monkeypatch,
    capsys,
) -> None:
    current = SimpleNamespace(
        config=SimpleNamespace(mqtt=SimpleNamespace(password_env=None)),
        manifest=_manifest(),
        config_path=local_control_cli.Path("/current/private/config.yaml"),
        manifest_path=local_control_cli.Path("/current/private/install.json"),
        template_roots=(),
    )
    captured = {}
    monkeypatch.setenv("JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID", "0" * 32)
    monkeypatch.setenv(
        "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE",
        "/stale/missing/password",
    )
    monkeypatch.setattr(local_control_cli, "_prepare_current", lambda args: current)
    monkeypatch.setattr(local_control_cli, "load_current_qualifications", lambda path: ())
    monkeypatch.setattr(
        local_control_cli,
        "resolve_current_executable",
        lambda path: local_control_cli.Path("/current/bin/jebao-flow-local"),
    )
    monkeypatch.setattr(
        local_control_cli,
        "require_current_mqtt_password_file",
        lambda path: (_ for _ in ()).throw(AssertionError("stale path was inspected")),
    )

    class _Artifact:
        def public_dict(self):
            return {"status": "service_prepared"}

    def replace(output, spec, *, hardware_safety_volume_id):
        captured["spec"] = spec
        return _Artifact()

    monkeypatch.setattr(local_control_cli, "replace_prepared_service", replace)

    result = local_control_cli.main(
        [
            "prepare-service",
            "--config",
            "/current/private/config.yaml",
            "--role-attested-by",
            "operator",
            "--output",
            "/current/private/jebao-flow-local.service",
        ]
    )

    assert result == 0
    assert captured["spec"].mqtt_password_file is None
    assert capsys.readouterr().err == ""


def test_prepare_service_requires_the_current_stable_safety_volume_identity(
    monkeypatch,
    capsys,
) -> None:
    current = SimpleNamespace(
        config=SimpleNamespace(mqtt=SimpleNamespace(password_env=None)),
        manifest=_manifest(),
        config_path=local_control_cli.Path("/current/private/config.yaml"),
        manifest_path=local_control_cli.Path("/current/private/install.json"),
        template_roots=(),
    )
    monkeypatch.delenv("JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID", raising=False)
    monkeypatch.setattr(local_control_cli, "_prepare_current", lambda args: current)
    monkeypatch.setattr(
        local_control_cli,
        "resolve_current_executable",
        lambda path: local_control_cli.Path("/current/bin/jebao-flow-local"),
    )

    result = local_control_cli.main(
        [
            "prepare-service",
            "--config",
            "/current/private/config.yaml",
            "--role-attested-by",
            "operator",
            "--output",
            "/current/private/jebao-flow-local.service",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-local: current_operation_failed_closed\n"


def test_prepare_compose_uses_only_current_inputs_and_keeps_write_off_by_default(
    monkeypatch,
    capsys,
) -> None:
    current = SimpleNamespace(
        config=SimpleNamespace(mqtt=SimpleNamespace(password_env=None)),
        manifest=_manifest(),
        template_roots=(local_control_cli.Path("/current/private/templates"),),
    )
    captured = {}
    monkeypatch.setenv("JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID", "5" * 32)
    monkeypatch.setenv(
        "JEBAO_FLOW_LOCAL_QUALIFICATIONS",
        "/stale/missing/qualifications.json",
    )
    monkeypatch.setenv(
        "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE",
        "/stale/missing/mqtt-password",
    )
    monkeypatch.setattr(local_control_cli, "_prepare_current", lambda args: current)
    monkeypatch.setattr(
        local_control_cli,
        "load_current_qualifications",
        lambda path: (_ for _ in ()).throw(
            AssertionError("read-only Compose inspected write qualifications")
        ),
    )
    monkeypatch.setattr(
        local_control_cli,
        "read_current_mqtt_password",
        lambda path: (_ for _ in ()).throw(
            AssertionError("password absent from current config was inspected")
        ),
    )
    monkeypatch.setattr(
        local_control_cli,
        "resolve_current_build_context",
        lambda path: local_control_cli.Path("/current/source"),
    )

    class _Artifact:
        def public_dict(self):
            return {
                "status": "compose_prepared",
                "previous_bundle_values_reused": False,
                "private_paths_exposed": False,
            }

    def prepare(output, bundle_root, spec, **inputs):
        captured.update(
            output=output,
            bundle_root=bundle_root,
            spec=spec,
            inputs=inputs,
        )
        return _Artifact()

    monkeypatch.setattr(local_control_cli, "prepare_compose_deployment", prepare)

    result = local_control_cli.main(
        [
            "prepare-compose",
            "--config",
            "/current/private/config.yaml",
            "--role-attested-by",
            "operator",
            "--output",
            "/current/private/compose.yaml",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert captured["spec"].write_enabled is False
    assert captured["spec"].qualification_configured is False
    assert captured["spec"].mqtt_password_configured is False
    assert captured["inputs"]["qualifications"] is None
    assert captured["inputs"]["mqtt_password"] is None
    assert captured["inputs"]["hardware_safety_volume_id"] == "5" * 32
    assert captured["spec"].input_generation_root == local_control_cli.Path(
        "/current/private/.compose.yaml.inputs"
    ) / ("0" * 32)
    assert payload["previous_bundle_values_reused"] is False
    assert "/current/private" not in output.out
    assert output.err == ""


def test_internal_compose_stage_runs_without_loading_install_inputs(
    monkeypatch,
    capsys,
) -> None:
    seen = []

    class _Staged:
        def public_dict(self):
            return {"status": "compose_inputs_staged", "private_values_exposed": False}

    monkeypatch.setattr(
        local_control_cli,
        "stage_compose_inputs",
        lambda generation: seen.append(generation) or _Staged(),
    )
    monkeypatch.setattr(
        local_control_cli,
        "_prepare_current",
        lambda args: (_ for _ in ()).throw(AssertionError("install path was reached")),
    )

    result = local_control_cli.main(
        ["stage-compose-inputs", "--expected-generation", "6" * 32]
    )

    output = capsys.readouterr()
    assert result == 0
    assert seen == ["6" * 32]
    assert json.loads(output.out)["private_values_exposed"] is False
    assert output.err == ""


def test_compose_generation_is_checked_before_runtime_authority_or_config(
    monkeypatch,
    capsys,
) -> None:
    order = []
    monkeypatch.setattr(
        local_control_cli,
        "_validate_current_compose_generation",
        lambda generation: order.append(("generation", generation)),
    )
    monkeypatch.setattr(
        local_control_cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: order.append("volume_identity"),
    )

    def stop_before_config(args):
        order.append("config")
        raise ValueError("stop after order observation")

    monkeypatch.setattr(local_control_cli, "_prepare_current", stop_before_config)

    result = local_control_cli.main(
        [
            "serve",
            "--config",
            "/current/private/config.yaml",
            "--role-attested-by",
            "operator",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert order == [("generation", None), "volume_identity", "config"]
    assert output.out == ""
    assert output.err == "jebao-flow-local: current_operation_failed_closed\n"


def test_stale_ambient_compose_generation_does_not_gate_a_bare_runtime(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JEBAO_FLOW_LOCAL_COMPOSE_GENERATION", "stale-generation")
    seen = []
    monkeypatch.setattr(
        local_control_cli,
        "validate_current_compose_inputs",
        lambda generation: seen.append(generation),
    )

    local_control_cli._validate_current_compose_generation(None)

    assert seen == []


def test_current_mqtt_password_prefers_the_current_service_password_file(
    monkeypatch,
) -> None:
    config = SimpleNamespace(
        mqtt=SimpleNamespace(
            password_env="MQTT_PASSWORD",
            resolve_password=lambda: "stale-shell-value",
        )
    )
    monkeypatch.setenv(
        "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE",
        "/current/private/mqtt-password",
    )
    monkeypatch.setattr(
        local_control_cli,
        "read_current_mqtt_password",
        lambda path: "current-file-value",
    )

    assert local_control_cli._current_mqtt_password(config) == "current-file-value"


def test_generated_service_identity_file_replaces_a_stale_raw_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID_FILE",
        "/current/private/hardware-safety-volume-id",
    )
    monkeypatch.setenv("JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID", "0" * 32)
    monkeypatch.setattr(
        local_control_cli,
        "read_current_hardware_safety_volume_id",
        lambda path: "1" * 32,
    )

    local_control_cli._activate_current_hardware_safety_volume_identity()

    assert os.environ["JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID"] == "1" * 32


def test_default_runtime_does_not_project_receipts_as_controls(monkeypatch) -> None:
    captured = []

    def build(qualifications, *, current_content):
        captured.append((qualifications, current_content))
        return "catalog"

    monkeypatch.setattr(local_control_cli, "build_local_control_catalog", build)

    assert (
        local_control_cli._runtime_catalog(
            ("current-receipt",),
            "current-content",
            write_enabled=False,
        )
        == "catalog"
    )
    assert captured == [((), "current-content")]


def test_default_runtime_does_not_read_a_retained_qualification_path(monkeypatch) -> None:
    monkeypatch.setattr(
        local_control_cli,
        "load_current_qualifications",
        lambda path: (_ for _ in ()).throw(AssertionError("qualification path was read")),
    )

    assert (
        local_control_cli._runtime_qualifications(
            local_control_cli.Path("/stale/qualification"),
            write_enabled=False,
        )
        == ()
    )


def test_default_runtime_does_not_construct_a_write_coordinator(monkeypatch) -> None:
    monkeypatch.setattr(
        local_control_cli,
        "_coordinator",
        lambda config, catalog: (_ for _ in ()).throw(
            AssertionError("read-only service opened its write runtime")
        ),
    )

    assert (
        local_control_cli._runtime_coordinator(
            "current-config",
            "diagnostic-catalog",
            write_enabled=False,
        )
        is None
    )

def test_write_enabled_runtime_uses_only_current_receipts(monkeypatch) -> None:
    captured = []

    def build(qualifications, *, current_content):
        captured.append((qualifications, current_content))
        return "catalog"

    monkeypatch.setattr(local_control_cli, "build_local_control_catalog", build)

    local_control_cli._runtime_catalog(
        ("current-receipt",),
        "current-content",
        write_enabled=True,
    )

    assert captured == [(('current-receipt',), "current-content")]


def test_service_restart_binding_mismatch_fails_before_replacing_manifest(
    monkeypatch,
) -> None:
    manifest = _manifest()
    replaced = []
    args = SimpleNamespace(
        config=local_control_cli.Path("/current/config"),
        manifest=local_control_cli.Path("/current/manifest"),
        role_attested_by="operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
        role_binding_sha256="f" * 64,
        template_root=(),
    )
    monkeypatch.setattr(
        local_control_cli,
        "current_service_path",
        lambda value, description: local_control_cli.Path(value),
    )
    monkeypatch.setattr(local_control_cli, "load_current_private_config", lambda path: object())
    monkeypatch.setattr(local_control_cli, "hash_current_template_contents", lambda roots: ())
    monkeypatch.setattr(
        local_control_cli,
        "build_fresh_install_manifest",
        lambda *args, **kwargs: manifest,
    )
    monkeypatch.setattr(
        local_control_cli,
        "replace_install_manifest",
        lambda *args: replaced.append(args),
    )

    with pytest.raises(ValueError, match="stable role binding"):
        local_control_cli._prepare_current(args)

    assert replaced == []


def test_read_only_serve_constructs_group_inventory_without_a_supervisor(
    monkeypatch,
) -> None:
    config = AppConfig.model_validate(
        {
            "instance": {"id": "test", "name": "Test"},
            "mqtt": {"host": "broker.invalid", "topic_prefix": "jebao-flow/test"},
            "devices": [
                {
                    "id": member,
                    "name": member.title(),
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "address": f"192.0.2.{index}",
                    "identity": {
                        "device_id": f"synthetic-device-{index}",
                        "mac_address": f"0200000000{index:02x}",
                    },
                }
                for index, member in enumerate(("master", "slave"), start=1)
            ],
            "groups": [
                {
                    "id": "main_flow",
                    "name": "Main flow",
                    "members": [{"device": "master"}, {"device": "slave"}],
                }
            ],
        }
    )
    current = SimpleNamespace(
        config=config,
        manifest=_manifest(),
        config_path=local_control_cli.Path("/current/private/config.yaml"),
        manifest_path=local_control_cli.Path("/current/private/install.json"),
        template_roots=(),
    )
    captured = {}

    class _GroupService:
        supervisor_count = 0

    group_service = _GroupService()

    def construct_group_service(**kwargs):
        captured["group_kwargs"] = kwargs
        return group_service

    async def serve(adapter) -> None:
        captured["adapter"] = adapter

    monkeypatch.setattr(local_control_cli, "_prepare_current", lambda args: current)
    monkeypatch.setattr(
        local_control_cli,
        "LocalControlGroupService",
        construct_group_service,
    )
    monkeypatch.setattr(local_control_cli, "_serve", serve)

    result = local_control_cli.main(
        [
            "serve",
            "--config",
            "/current/private/config.yaml",
            "--role-attested-by",
            "operator",
        ]
    )

    assert result == 0
    assert captured["group_kwargs"]["coordinator"] is None
    assert captured["adapter"]._group_service is group_service  # noqa: SLF001
