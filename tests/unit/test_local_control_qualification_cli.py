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


@pytest.mark.parametrize("function", ["manual_frequency", "manual_mode", "motor_speed", "schedule"])
def test_bar_cli_without_opt_in_never_opens_runtime(function, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("no live or durable access before explicit opt-in")

    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", forbidden)
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", forbidden)
    assert (
        cli.main(
            [
                "run",
                "--function",
                function,
                "--config",
                "private.yaml",
                "--target",
                "bar",
                "--operation-id",
                "bar_cli_0001",
                "--active-template-sha256",
                "a" * 64,
                "--qualification-output",
                "private.json",
            ]
        )
        == 2
    )
    assert "current_operation_failed_closed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "product,producer_name,function",
    [
        (LOCAL_WAVEMAKER.product_key, "BarScalarQualificationProducer", "manual_frequency"),
        (LOCAL_WAVEMAKER.product_key, "BarScalarQualificationProducer", "feeding_duration"),
        (DC_PUMP_PRO.product_key, "DcScalarQualificationProducer", "manual_frequency"),
        (DC_PUMP_PRO.product_key, "DcScalarQualificationProducer", "manual_mode"),
        (DC_PUMP_PRO.product_key, "DcScalarQualificationProducer", "feeding_duration"),
        (AQUARIUM_PUMP.product_key, "AquariumScalarQualificationProducer", "motor_speed"),
        (AQUARIUM_PUMP.product_key, "AquariumScalarQualificationProducer", "feeding_duration"),
    ],
)
def test_scalar_cli_delegates_to_product_producer(
    function,
    product,
    producer_name,
    monkeypatch,
    capsys,
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
    monkeypatch.setattr(
        cli,
        "CurrentQualificationEpochSource",
        lambda **kwargs: lambda: SimpleNamespace(target=SimpleNamespace(product_key=product)),
    )
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for name in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: object())
    assert (
        cli.main(
            [
                "run",
                "--function",
                function,
                "--config",
                "private.yaml",
                "--target",
                "bar",
                "--operation-id",
                "bar_cli_0001",
                "--active-template-sha256",
                "a" * 64,
                "--template-root",
                "private-template",
                "--qualification-output",
                "private.json",
                "--write-enabled",
            ]
        )
        == 0
    )
    assert calls[0]["function"] == function
    assert calls[1] == "bar_cli_0001"
    assert "offline_wiring_test" in capsys.readouterr().out


@pytest.mark.parametrize("function", ["motor_speed", "feeding_duration"])
@pytest.mark.parametrize("product", [AQUARIUM_PUMP.product_key, LOCAL_WAVEMAKER.product_key])
def test_prepared_aquarium_cli_is_explicit_and_product_scoped(function, product, monkeypatch):
    from types import SimpleNamespace

    calls = []

    class Producer:
        def __init__(self, **kwargs):
            calls.append(kwargs["function"])

        async def execute(self, operation_id, *, preparation_speed):
            calls.append((operation_id, preparation_speed))
            return SimpleNamespace(public_dict=lambda: {"hardware_access": False})

    monkeypatch.setattr(cli, "PreparedAquariumScalarQualificationProducer", Producer)
    monkeypatch.setattr(
        cli,
        "CurrentQualificationEpochSource",
        lambda **kwargs: lambda: SimpleNamespace(target=SimpleNamespace(product_key=product)),
    )
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for name in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: object())
    args = [
        "run",
        "--function",
        function,
        "--config",
        "private.yaml",
        "--target",
        "aquarium",
        "--operation-id",
        "prepared_cli_01",
        "--active-template-sha256",
        "a" * 64,
        "--template-root",
        "private-template",
        "--qualification-output",
        "private.json",
        "--preparation-speed",
        "40",
    ]
    assert cli.main(args) == 2 and calls == []
    result = cli.main([*args, "--write-enabled"])
    if product == AQUARIUM_PUMP.product_key:
        assert result == 0 and calls == [function, ("prepared_cli_01", 40)]
    else:
        assert result == 2 and calls == []


@pytest.mark.parametrize("function", ["manual_frequency", "manual_mode"])
def test_preparation_speed_cannot_select_other_functions(function, monkeypatch):
    def forbidden():
        raise AssertionError("invalid option reached runtime activation")

    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", forbidden)
    with pytest.raises(ValueError, match="preparation-speed"):
        cli._run(
            cli.build_parser().parse_args(
                [
                    "run",
                    "--function",
                    function,
                    "--config",
                    "private.yaml",
                    "--target",
                    "aquarium",
                    "--operation-id",
                    "prepared_cli_01",
                    "--active-template-sha256",
                    "a" * 64,
                    "--qualification-output",
                    "private.json",
                    "--preparation-speed",
                    "40",
                    "--write-enabled",
                ]
            )
        )


@pytest.mark.parametrize(
    "function,producer_name",
    [
        ("schedule", "ScheduleQualificationProducer"),
        ("schedule_reset", "ScheduleResetQualificationProducer"),
    ],
)
def test_schedule_cli_passes_explicit_a_b_and_existing_guarded_inputs(
    tmp_path, monkeypatch, capsys, function, producer_name
):
    import json
    from types import SimpleNamespace

    calls = []
    a, b = {"family": "bar", "slots": []}, {"family": "bar", "slots": [{"fixture": True}]}
    path_a, path_b = tmp_path / "a.json", tmp_path / "b.json"
    path_a.write_text(json.dumps(a))
    path_b.write_text(json.dumps(b))

    class Producer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def execute(self, operation_id):
            calls.append(operation_id)
            return SimpleNamespace(public_dict=lambda: {"offline_wiring_test": True})

    monkeypatch.setattr(cli, producer_name, Producer)
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", lambda **kwargs: object())
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for name in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: object())
    assert (
        cli.main(
            [
                "run",
                "--function",
                function,
                "--config",
                "private.yaml",
                "--target",
                "pump",
                "--operation-id",
                "schedule_cli_01",
                "--active-template-sha256",
                "a" * 64,
                "--template-root",
                "private-template",
                "--qualification-output",
                "private.json",
                "--schedule-a",
                str(path_a),
                "--schedule-b",
                str(path_b),
                "--write-enabled",
            ]
        )
        == 0
    )
    assert calls[0]["schedule_a"] == a and calls[0]["schedule_b"] == b
    assert calls[1] == "schedule_cli_01"
    assert "offline_wiring_test" in capsys.readouterr().out


def test_schedule_cli_never_guesses_missing_trial_images(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("missing input must be rejected before runtime preparation")

    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", forbidden)
    assert (
        cli.main(
            [
                "run",
                "--function",
                "schedule",
                "--config",
                "private.yaml",
                "--target",
                "pump",
                "--operation-id",
                "schedule_cli_bad",
                "--active-template-sha256",
                "a" * 64,
                "--qualification-output",
                "private.json",
                "--write-enabled",
            ]
        )
        == 2
    )
    assert "current_operation_failed_closed" in capsys.readouterr().err


def test_timer_cli_requires_explicit_trial_output_before_runtime_preparation(monkeypatch):
    def forbidden():
        raise AssertionError("missing timer input must be rejected before hardware preparation")

    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", forbidden)
    assert (
        cli.main(
            [
                "run",
                "--function",
                "schedule_enable",
                "--config",
                "private.yaml",
                "--target",
                "bar",
                "--operation-id",
                "bar_timer_cli_missing",
                "--active-template-sha256",
                "a" * 64,
                "--qualification-output",
                "private.json",
                "--write-enabled",
            ]
        )
        == 2
    )


def test_timer_cli_wires_explicit_trial_and_configured_writer(monkeypatch, capsys):
    from types import SimpleNamespace

    calls = []
    config = _config(product_key=LOCAL_WAVEMAKER.product_key)
    factory = object()

    class Producer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def execute(self, operation_id, *, trial_flow):
            calls.append((operation_id, trial_flow))
            return SimpleNamespace(public_dict=lambda: {"offline_timer_wiring_test": True})

    monkeypatch.setattr(cli, "BarTimerQualificationProducer", Producer)
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", lambda **kwargs: object())
    monkeypatch.setattr(cli, "load_current_private_config", lambda path: config)
    monkeypatch.setattr(
        cli, "configured_writer_factory", lambda value: factory if value is config else None
    )
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for name in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: object())
    assert (
        cli.main(
            [
                "run",
                "--function",
                "schedule_enable",
                "--trial-flow",
                "40",
                "--config",
                "private.yaml",
                "--target",
                "bar",
                "--operation-id",
                "bar_timer_cli_01",
                "--active-template-sha256",
                "a" * 64,
                "--template-root",
                "private-template",
                "--qualification-output",
                "private.json",
                "--write-enabled",
            ]
        )
        == 0
    )
    assert calls[0]["writer_factory"] is factory
    assert calls[1] == ("bar_timer_cli_01", 40)
    assert "offline_timer_wiring_test" in capsys.readouterr().out


@pytest.mark.parametrize(
    "function",
    (
        "manual_flow",
        "manual_mode",
        "manual_frequency",
        "feeding_duration",
        "schedule_enable",
        "manual_enable",
        "schedule",
        "schedule_reset",
        "device_clock_sync",
    ),
)
def test_dc_cli_routes_each_function_with_only_its_required_inputs(tmp_path, monkeypatch, function):
    import json
    from types import SimpleNamespace

    from jebao_flow.local_control import (
        clock_qualification,
        dc_prepared_qualification,
        dc_program_qualification,
    )
    from tests.unit.test_local_control_dc_prepared_qualification import _binding, _epoch

    calls = []

    class Producer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def execute(self, operation_id, **kwargs):
            calls.append((operation_id, kwargs))
            return SimpleNamespace(public_dict=lambda: {"offline": True})

    program = function in {"schedule_enable", "manual_enable", "schedule", "schedule_reset"}
    module, name = (
        (clock_qualification, "DcClockQualificationProducer")
        if function == "device_clock_sync"
        else (dc_program_qualification, "DcProgramQualificationProducer")
        if program
        else (dc_prepared_qualification, "DcPreparedScalarQualificationProducer")
    )
    monkeypatch.setattr(module, name, Producer)
    monkeypatch.setattr(
        cli, "CurrentQualificationEpochSource", lambda **_: lambda: _epoch(_binding())
    )
    monkeypatch.setattr(
        cli, "load_current_private_config", lambda _: _config(product_key=DC_PUMP_PRO.product_key)
    )
    factory = object()
    monkeypatch.setattr(cli, "configured_writer_factory", lambda _: factory)
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for item in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, item, lambda *args, **kwargs: object())
    arguments = [
        "run",
        "--function",
        function,
        "--config",
        "private.yaml",
        "--target",
        "dc",
        "--operation-id",
        "dc_cli_0001",
        "--active-template-sha256",
        "a" * 64,
        "--template-root",
        "template",
        "--qualification-output",
        "private.json",
        "--write-enabled",
    ]
    if function != "device_clock_sync":
        arguments.extend(["--preparation-flow", "40"])
    if function in {"schedule", "schedule_reset"}:
        for letter, flow in (("a", 40), ("b", 39)):
            if letter == "b" and function == "schedule_reset":
                continue
            path = tmp_path / f"{letter}.json"
            path.write_text(
                json.dumps(
                    {
                        "family": "dc_pro",
                        "slots": [
                            {
                                "slot_index": 0,
                                "start": "00:00",
                                "end": "24:00",
                                "mode": "constant",
                                "flow": flow,
                            }
                        ],
                    }
                )
            )
            arguments.extend([f"--schedule-{letter}", str(path)])
    assert cli.main(arguments) == 0
    assert calls[0]["writer_factory"] is factory
    assert calls[1] == (
        "dc_cli_0001",
        {} if function == "device_clock_sync" else {"preparation_flow": 40},
    )
    if function in {"schedule", "schedule_reset"}:
        assert len(calls[0]["schedule_a"]) == 384
        assert calls[0]["schedule_a"][5] == 40
        if function == "schedule_reset":
            assert calls[0]["schedule_b"] is None


def test_dc_image_cli_rejects_another_return_pump_family(tmp_path):
    import json

    path = tmp_path / "aquarium.json"
    path.write_text(json.dumps({"family": "aquarium", "slots": []}))
    with pytest.raises(ValueError, match="another product"):
        cli._read_dc_image(path)


@pytest.mark.parametrize(
    ("function", "product"),
    [
        ("device_clock_sync", LOCAL_WAVEMAKER_PRO),
        ("slave_common_flow", LOCAL_WAVEMAKER_PRO),
        ("device_clock_sync", LOCAL_WAVEMAKER),
        ("manual_mode", LOCAL_WAVEMAKER),
        ("feed_switch", LOCAL_WAVEMAKER),
        ("pulse_or_tide", LOCAL_WAVEMAKER),
        ("linkage_role", LOCAL_WAVEMAKER),
    ],
)
def test_new_app_controls_route_without_dc_preparation(monkeypatch, function, product):
    from types import SimpleNamespace

    from jebao_flow.local_control import (
        bar_app_qualification,
        clock_qualification,
        pro_common_flow_qualification,
    )
    from tests.unit.test_local_control_dc_prepared_qualification import _binding, _epoch

    calls = []

    class Producer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def execute(self, operation_id):
            calls.append(operation_id)
            return SimpleNamespace(public_dict=lambda: {"offline": True})

    module, name = (
        (bar_app_qualification, "BarAppControlQualificationProducer")
        if product is LOCAL_WAVEMAKER and function != "device_clock_sync"
        else (clock_qualification, "BarClockQualificationProducer")
        if product is LOCAL_WAVEMAKER
        else (clock_qualification, "ProClockQualificationProducer")
        if function == "device_clock_sync"
        else (pro_common_flow_qualification, "ProCommonFlowQualificationProducer")
    )
    monkeypatch.setattr(module, name, Producer)
    target = _binding().model_copy(update={"product_key": product.product_key})
    monkeypatch.setattr(cli, "CurrentQualificationEpochSource", lambda **_: lambda: _epoch(target))
    monkeypatch.setattr(
        cli, "load_current_private_config", lambda _: _config(product_key=product.product_key)
    )
    factory = object()
    monkeypatch.setattr(cli, "configured_writer_factory", lambda _: factory)
    monkeypatch.setattr(cli, "_activate_current_hardware_safety_volume_identity", lambda: None)
    for item in ("LocalControlJournalStore", "LocalControlLease", "QualificationArtifactStore"):
        monkeypatch.setattr(cli, item, lambda *args, **kwargs: object())
    assert (
        cli.main(
            [
                "run",
                "--function",
                function,
                "--config",
                "private.yaml",
                "--target",
                "pro",
                "--operation-id",
                "pro_cli_0001",
                "--active-template-sha256",
                "a" * 64,
                "--template-root",
                "template",
                "--qualification-output",
                "private.json",
                "--write-enabled",
            ]
        )
        == 0
    )
    assert calls[1] == "pro_cli_0001"
    if function == "device_clock_sync":
        assert calls[0]["writer_factory"] is factory
    assert "preparation_flow" not in calls[0]
