"""Install, inspect, and run the isolated local-first control service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from jebao_flow.config import RuntimeMode
from jebao_flow.hardware_safety import HARDWARE_SAFETY_VOLUME_ID_ENV
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.compose import (
    LocalControlComposeSpec,
    prepare_compose_deployment,
    resolve_current_build_context,
    stage_compose_inputs,
    validate_current_compose_inputs,
)
from jebao_flow.local_control.coordinator import (
    LocalControlCoordinator,
    NativeRecoveryRequiredError,
)
from jebao_flow.local_control.dosing import (
    build_dosing_channel_schedule_preview,
    plan_average_interval_dosing,
    plan_daily_dosing,
)
from jebao_flow.local_control.group_service import LocalControlGroupService
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.install import (
    build_fresh_install_manifest,
    hash_current_template_contents,
    load_current_private_config,
    replace_install_manifest,
)
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.mqtt import (
    LocalControlGroupCommandRouter,
    LocalControlMqttAdapter,
    LocalControlMqttRouter,
)
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.resolver import CurrentEndpointResolver
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.local_control.service import (
    HARDWARE_SAFETY_VOLUME_ID_FILE_ENV,
    LocalControlServiceSpec,
    current_service_path,
    read_current_hardware_safety_volume_id,
    read_current_mqtt_password,
    replace_prepared_service,
    require_current_mqtt_password_file,
    resolve_current_executable,
    stable_role_binding_sha256,
)
from jebao_flow.local_control.state import FreshStatePoller, LocalControlStateProjector
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan, WavemakerSchedule
from jebao_flow.operator_capture.app_catalog import public_app_catalog
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode
from jebao_flow.protocol.discovery import GizwitsDiscovery

_CONFIG_ENV = "JEBAO_FLOW_CONFIG"
_MANIFEST_ENV = "JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"
_QUALIFICATION_ENV = "JEBAO_FLOW_LOCAL_QUALIFICATIONS"
_TEMPLATE_ROOTS_ENV = "JEBAO_FLOW_APP_TEMPLATE_ROOTS"
_MQTT_PASSWORD_FILE_ENV = "JEBAO_FLOW_LOCAL_MQTT_PASSWORD_FILE"
_ROLE_ATTESTED_AT_ENV = "JEBAO_FLOW_ROLE_ATTESTED_AT"
_ROLE_BINDING_ENV = "JEBAO_FLOW_ROLE_BINDING_SHA256"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-local")
    subparsers = parser.add_subparsers(dest="command", required=True)
    catalog = subparsers.add_parser("catalog", help="show the complete English app catalog")
    catalog.add_argument("--product-key")

    wavemaker = subparsers.add_parser(
        "plan-wavemaker", help="외부 JSON의 Bar/Pro 시간표를 장비 접근 없이 검증합니다"
    )
    wavemaker.add_argument("--file", type=Path, required=True)
    wavemaker.add_argument("--native", action="store_true", help="Pro Sync/Async 상속 계획")

    schedule_apply = subparsers.add_parser(
        "apply-wavemaker-schedule", help="자격이 있는 Pro의 독립·TimerOFF 시간표를 교체합니다"
    )
    _add_install_arguments(schedule_apply)
    schedule_apply.add_argument("--file", type=Path, required=True)
    schedule_apply.add_argument("--member", required=True)
    schedule_apply.add_argument("--request-id", required=True)
    schedule_apply.add_argument("--qualifications", type=Path, required=True)
    schedule_apply.add_argument("--write-enabled", action="store_true")

    for name, description in (
        ("attach-wavemaker", "외부 JSON의 Pro Sync/Async를 결속하고 자기 원본을 보존합니다"),
        ("detach-wavemaker", "자기 attach 원본으로 해제·시간표·제어값을 복원합니다"),
        ("recover-wavemaker", "미완료 native 실행을 재결속 없이 자기 원본 방향으로 복구합니다"),
        ("assist-wavemaker-detach", "미확인 첫 해제를 자격 있는 별도 scalar로 지원합니다"),
        (
            "cycle-wavemaker-master",
            "명시 옵션의 자기 Async 보류를 master 링크 두 단계로 지원합니다",
        ),
    ):
        native = subparsers.add_parser(name, help=description)
        _add_install_arguments(native)
        native.add_argument("--write-enabled", action="store_true")
        if name == "attach-wavemaker":
            native.add_argument("--file", type=Path, required=True)
            native.add_argument("--qualifications", type=Path, required=True)
            native.add_argument("--request-id", required=True)
            native.add_argument(
                "--await-operator-observation",
                action="store_true",
                help="Async 최종 명령 시도 후 자동 원복 없이 사용자 지시를 기다립니다",
            )
            native.add_argument(
                "--master-cycle-on-async-failure",
                action="store_true",
                help="최종 Async 미확인 시 별도 master 해제/재설정 1회를 수행합니다",
            )
        elif name == "detach-wavemaker":
            native.add_argument("--attachment-id", required=True)
        else:
            native.add_argument("--request-id", required=True)
            if name in {"assist-wavemaker-detach", "cycle-wavemaker-master"}:
                native.add_argument("--qualifications", type=Path, required=True)
    native_status = subparsers.add_parser(
        "wavemaker-status", help="장비 접근 없이 native 보존 기록을 봅니다"
    )
    native_status.add_argument("--request-id", required=True)

    dosing = subparsers.add_parser(
        "plan-dosing",
        help="split a daily dosing total into an exact, evenly timed local plan",
    )
    dosing.add_argument("--channel", type=int, required=True)
    dosing.add_argument("--daily-amount", required=True)
    dosing.add_argument("--doses", type=int, required=True)
    dosing.add_argument("--first-dose", default="00:00", metavar="HH:MM")
    dosing.add_argument("--amount-step", default="0.5")
    dosing.add_argument("--unit", default="mL")
    dosing.add_argument(
        "--app-image-preview",
        action="store_true",
        help="include metadata for the exact 96-byte app-shaped image when doses are integers",
    )

    average_dosing = subparsers.add_parser(
        "plan-average-dosing",
        help="find an integer dose interval for an exact smaller daily average",
    )
    average_dosing.add_argument("--channel", type=int, required=True)
    average_dosing.add_argument("--daily-average", required=True)
    average_dosing.add_argument("--unit", default="mL")

    for name, help_text in (
        ("prepare-install", "replace the install manifest from current inputs"),
        ("prepare-service", "replace an owner-only systemd staging unit from current inputs"),
        ("prepare-compose", "replace a standalone Docker Compose deployment from current inputs"),
        ("ha-schema", "show the current Home Assistant projection"),
        ("serve", "run the qualified Home Assistant/MQTT command service"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _add_install_arguments(command)
        if name in {"prepare-service", "prepare-compose", "ha-schema", "serve"}:
            command.add_argument(
                "--qualifications",
                type=Path,
                default=_environment_path(_QUALIFICATION_ENV),
            )
        if name in {"ha-schema", "serve"}:
            command.add_argument(
                "--schedule-pair",
                action="append",
                default=[],
                metavar="MODE:MASTER:SLAVE",
                help="current stable logical topology; repeat for additional slaves",
            )
        if name == "prepare-service":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--executable", type=Path)
            command.add_argument(
                "--hardware-safety-volume-id-file",
                type=Path,
                default=_environment_path(HARDWARE_SAFETY_VOLUME_ID_FILE_ENV),
                help=(
                    "owner-only destination for the current hardware-safety volume identity; "
                    "defaults beside the staged unit"
                ),
            )
            command.add_argument(
                "--mqtt-password-file",
                type=Path,
                default=_environment_path(_MQTT_PASSWORD_FILE_ENV),
                help="current owner-only file containing only the MQTT password",
            )
            command.add_argument(
                "--write-enabled",
                action="store_true",
                help="include the separately gated live-write opt-in in the staged unit",
            )
        if name == "prepare-compose":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument(
                "--bundle-root",
                type=Path,
                help="durable owner-only root for newly generated Compose input generations",
            )
            command.add_argument("--build-context", type=Path)
            command.add_argument(
                "--mqtt-password-file",
                type=Path,
                default=_environment_path(_MQTT_PASSWORD_FILE_ENV),
                help="current owner-only file containing only the MQTT password",
            )
            command.add_argument(
                "--write-enabled",
                action="store_true",
                help="include the separately gated live-write opt-in in the Compose runtime",
            )
        if name == "serve":
            command.add_argument("--compose-generation", help=argparse.SUPPRESS)
            command.add_argument(
                "--write-enabled",
                action="store_true",
                help="expose qualified controls after current config safety locks also allow them",
            )

    recover = subparsers.add_parser(
        "recover",
        help="recover one unfinished local-control request from its fresh baseline",
    )
    _add_install_arguments(recover)
    recover.add_argument("--compose-generation", help=argparse.SUPPRESS)
    recover.add_argument("request_id")
    stage_compose = subparsers.add_parser(
        "stage-compose-inputs",
        help="stage one generated Compose input generation inside its private data volume",
    )
    stage_compose.add_argument("--expected-generation", required=True)
    return parser


def _add_install_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=_environment_path(_CONFIG_ENV))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_environment_path(_MANIFEST_ENV) or _default_manifest_path(),
    )
    parser.add_argument("--role-attested-by", required=True)
    parser.add_argument(
        "--role-attested-at",
        type=_parse_attestation_time,
        default=os.environ.get(_ROLE_ATTESTED_AT_ENV),
    )
    parser.add_argument(
        "--role-binding-sha256",
        default=os.environ.get(_ROLE_BINDING_ENV),
    )
    parser.add_argument(
        "--template-root",
        action="append",
        type=Path,
        default=None,
    )


def _run(args: argparse.Namespace) -> int:
    if args.command in {
        "attach-wavemaker",
        "detach-wavemaker",
        "recover-wavemaker",
        "assist-wavemaker-detach",
        "cycle-wavemaker-master",
        "wavemaker-status",
    }:
        return _run_wavemaker_native(args)
    if args.command == "plan-wavemaker":
        model = NativeWavemakerPlan if args.native else WavemakerSchedule
        plan = model.model_validate_json(args.file.read_bytes())
        _print_json(plan.preview())
        return 0
    if args.command == "apply-wavemaker-schedule":
        schedule = WavemakerSchedule.model_validate_json(args.file.read_bytes())
        if schedule.family != "pro":
            raise ValueError("Bar 시간표는 오프라인 전용이며 실기 복원 자격이 없습니다")
        if not args.write_enabled:
            raise ValueError("시간표 적용에는 명시적인 --write-enabled가 필요합니다")
        _activate_current_hardware_safety_volume_identity()
        prepared = _prepare_current(args)
        if (
            prepared.config.runtime.mode is not RuntimeMode.CONTROL
            or prepared.config.runtime.dry_run
        ):
            raise ValueError("관측 전용 또는 dry-run에서는 시간표를 전송하지 않습니다")
        target = next(
            (item for item in prepared.manifest.devices if item.logical_member == args.member), None
        )
        device = next((item for item in prepared.config.devices if item.id == args.member), None)
        if target is None or device is None or not device.control.allow_hardware_writes:
            raise ValueError("현재 대상 장비가 없거나 write 잠금 상태입니다")
        if target.product_key != schedule.product_key:
            raise ValueError("시간표와 현재 장비의 제품 가족이 다릅니다")
        catalog = build_local_control_catalog(
            load_current_qualifications(args.qualifications),
            current_content=prepared.manifest.content,
        )
        result = asyncio.run(
            _coordinator(prepared.config, catalog).execute(
                LocalControlIntent(
                    request_id=args.request_id,
                    target=target,
                    function="schedule",
                    value=schedule.model_dump(mode="json"),
                )
            )
        )
        _print_json(result.public_dict())
        return 0 if result.accepted else 2
    if args.command == "catalog":
        _print_json(public_app_catalog(args.product_key))
        return 0
    if args.command == "plan-dosing":
        plan = plan_daily_dosing(
            channel=args.channel,
            daily_amount=args.daily_amount,
            dose_count=args.doses,
            first_dose_minute=_parse_clock_minute(args.first_dose),
            amount_step=args.amount_step,
            unit=args.unit,
        )
        payload = plan.public_dict()
        if args.app_image_preview:
            payload["app_image_preview"] = build_dosing_channel_schedule_preview(plan).public_dict()
        _print_json(payload)
        return 0
    if args.command == "plan-average-dosing":
        plan = plan_average_interval_dosing(
            channel=args.channel,
            target_daily_average=args.daily_average,
            unit=args.unit,
        )
        _print_json(plan.public_dict())
        return 0
    if args.command == "stage-compose-inputs":
        _print_json(stage_compose_inputs(args.expected_generation).public_dict())
        return 0
    if args.command in {"serve", "recover"}:
        _validate_current_compose_generation(args.compose_generation)
        _activate_current_hardware_safety_volume_identity()
    service_volume_id = (
        _current_hardware_safety_volume_id()
        if args.command in {"prepare-service", "prepare-compose"}
        else None
    )
    prepared = _prepare_current(args)
    if args.command == "prepare-install":
        _print_json(_public_install_summary(prepared.manifest))
        return 0
    if args.command == "prepare-service":
        if service_volume_id is None:
            raise AssertionError("prepare-service volume identity was not read")
        service_output = current_service_path(
            args.output,
            description="local-control service staging file",
        )
        hardware_safety_volume_id_file = current_service_path(
            args.hardware_safety_volume_id_file
            or service_output.with_name(f".{service_output.name}.hardware-safety-volume-id"),
            description="hardware-safety volume identity file",
        )
        mqtt_password_file = None
        if prepared.config.mqtt.password_env is not None:
            if args.mqtt_password_file is None:
                raise ValueError("current MQTT password requires --mqtt-password-file")
            mqtt_password_file = require_current_mqtt_password_file(args.mqtt_password_file)
        qualifications = None
        if args.write_enabled:
            # Write authority is current input only. A default read-only service does not inspect,
            # retain, or fail because of a qualification path left in the launch environment.
            load_current_qualifications(args.qualifications)
            qualifications = (
                None
                if args.qualifications is None
                else current_service_path(
                    args.qualifications,
                    description="local-control qualification file",
                )
            )
        artifact = replace_prepared_service(
            service_output,
            LocalControlServiceSpec(
                executable=resolve_current_executable(args.executable),
                config=prepared.config_path,
                manifest=prepared.manifest_path,
                hardware_safety_volume_id_file=hardware_safety_volume_id_file,
                role_attested_by=args.role_attested_by,
                role_attested_at=prepared.manifest.devices[0].role_attested_at,
                role_binding_sha256=stable_role_binding_sha256(prepared.manifest),
                qualifications=qualifications,
                template_roots=prepared.template_roots,
                mqtt_password_file=mqtt_password_file,
                write_enabled=args.write_enabled,
            ),
            hardware_safety_volume_id=service_volume_id,
        )
        payload = artifact.public_dict()
        payload["install_generation"] = prepared.manifest.generation
        payload["template_binding_complete"] = bool(prepared.manifest.content.template_sha256)
        _print_json(payload)
        return 0
    if args.command == "prepare-compose":
        if service_volume_id is None:
            raise AssertionError("prepare-compose volume identity was not read")
        compose_output = current_service_path(
            args.output,
            description="local-control compose staging file",
        )
        bundle_root = current_service_path(
            args.bundle_root or compose_output.with_name(f".{compose_output.name}.inputs"),
            description="local-control compose bundle root",
        )
        mqtt_password = None
        if prepared.config.mqtt.password_env is not None:
            if args.mqtt_password_file is None:
                raise ValueError("current MQTT password requires --mqtt-password-file")
            mqtt_password = read_current_mqtt_password(args.mqtt_password_file)
        qualifications = None
        if args.write_enabled and args.qualifications is not None:
            qualifications = load_current_qualifications(args.qualifications)
        artifact = prepare_compose_deployment(
            compose_output,
            bundle_root,
            LocalControlComposeSpec(
                build_context=resolve_current_build_context(args.build_context),
                input_generation_root=bundle_root / prepared.manifest.generation,
                generation=prepared.manifest.generation,
                role_attested_by=args.role_attested_by,
                role_attested_at=prepared.manifest.devices[0].role_attested_at.isoformat(),
                role_binding_sha256=stable_role_binding_sha256(prepared.manifest),
                template_roots=prepared.template_roots,
                mqtt_password_configured=mqtt_password is not None,
                qualification_configured=qualifications is not None,
                write_enabled=args.write_enabled,
            ),
            config=prepared.config,
            hardware_safety_volume_id=service_volume_id,
            mqtt_password=mqtt_password,
            qualifications=qualifications,
        )
        payload = artifact.public_dict()
        payload["template_binding_complete"] = bool(prepared.manifest.content.template_sha256)
        _print_json(payload)
        return 0
    if args.command == "recover":
        coordinator = _coordinator(prepared.config, build_local_control_catalog())
        result = asyncio.run(coordinator.recover(args.request_id))
        _print_json(result.public_dict())
        return 0
    if args.command in {"ha-schema", "serve"}:
        control_surface_enabled = prepared.config.runtime.mode is RuntimeMode.CONTROL
        runtime_write_enabled = control_surface_enabled and (
            args.command == "ha-schema" or args.write_enabled
        )
        qualifications = _runtime_qualifications(
            args.qualifications,
            write_enabled=runtime_write_enabled,
        )
        catalog = _runtime_catalog(
            qualifications,
            prepared.manifest.content,
            write_enabled=runtime_write_enabled,
        )
        schedule_plans = _schedule_plans(
            prepared.manifest,
            prepared.config,
            tuple(args.schedule_pair),
        )
        projection = project_home_assistant(
            prepared.manifest,
            catalog,
            discovery_prefix=prepared.config.mqtt.discovery_prefix,
            topic_prefix=prepared.config.mqtt.topic_prefix,
            schedule_plans=schedule_plans,
            groups=prepared.config.groups,
            control_surface_enabled=control_surface_enabled,
        )
        if args.command == "ha-schema":
            _print_json(projection.public_dict())
            return 0
        _require_live_opt_in(args, prepared.config, projection)
        operation_lock = asyncio.Lock()
        coordinator = _runtime_coordinator(
            prepared.config,
            catalog,
            write_enabled=runtime_write_enabled,
        )
        group_service = LocalControlGroupService(
            config=prepared.config,
            manifest=prepared.manifest,
            catalog=catalog,
            coordinator=coordinator,
            operation_lock=operation_lock,
        )
        router = LocalControlMqttRouter(
            projection=projection,
            manifest=prepared.manifest,
            catalog=catalog,
            schedule_plans=schedule_plans,
            coordinator=coordinator,
            operation_lock=operation_lock,
            ownership_service=group_service,
        )
        group_router = (
            LocalControlGroupCommandRouter(
                projection=projection,
                service=group_service,
            )
            if projection.group_control_entities
            else None
        )
        state_poller = FreshStatePoller(
            manifest=prepared.manifest,
            resolver=_resolver(prepared.config),
            projector=LocalControlStateProjector(
                manifest=prepared.manifest,
                projection=projection,
                catalog=catalog,
            ),
            poll_interval_seconds=prepared.config.observer.poll_interval_seconds,
            reconnect_initial_seconds=prepared.config.observer.reconnect_initial_seconds,
            reconnect_max_seconds=prepared.config.observer.reconnect_max_seconds,
            operation_lock=operation_lock,
        )
        asyncio.run(
            _serve(
                LocalControlMqttAdapter(
                    prepared.config.mqtt,
                    projection=projection,
                    router=router,
                    state_poller=state_poller,
                    password=_current_mqtt_password(prepared.config),
                    group_service=group_service,
                    group_router=group_router,
                )
            )
        )
        return 0
    raise AssertionError("unhandled local-control command")


class _PreparedCurrent:
    def __init__(
        self,
        *,
        config,
        manifest,
        config_path: Path,
        manifest_path: Path,
        template_roots: tuple[Path, ...],
    ) -> None:
        self.config = config
        self.manifest = manifest
        self.config_path = config_path
        self.manifest_path = manifest_path
        self.template_roots = template_roots


def _prepare_current(args: argparse.Namespace) -> _PreparedCurrent:
    if args.config is None:
        raise ValueError(f"--config or {_CONFIG_ENV} is required")
    config_path = current_service_path(
        args.config,
        description="local-control private config",
    )
    manifest_path = current_service_path(
        args.manifest,
        description="local-control install manifest",
    )
    if config_path == manifest_path:
        raise ValueError("current config and generated manifest must use different files")
    config = load_current_private_config(config_path)
    rederived_at = datetime.now(UTC)
    role_attested_at = args.role_attested_at or rederived_at
    selected_template_roots = (
        tuple(args.template_root)
        if args.template_root is not None
        else tuple(_environment_paths(_TEMPLATE_ROOTS_ENV))
    )
    template_roots = tuple(
        current_service_path(root, description="app template audit root")
        for root in selected_template_roots
    )
    template_sha256 = hash_current_template_contents(template_roots)
    manifest = build_fresh_install_manifest(
        config,
        role_attested_by=args.role_attested_by,
        role_attested_at=role_attested_at,
        template_sha256=template_sha256,
        now=rederived_at,
    )
    current_role_binding = stable_role_binding_sha256(manifest)
    if args.role_binding_sha256 is not None and args.role_binding_sha256 != current_role_binding:
        raise ValueError("current stable role binding differs from the recorded attestation")
    replace_install_manifest(manifest_path, manifest)
    return _PreparedCurrent(
        config=config,
        manifest=manifest,
        config_path=config_path,
        manifest_path=manifest_path,
        template_roots=template_roots,
    )


def _coordinator(config, catalog) -> LocalControlCoordinator:
    return LocalControlCoordinator(
        catalog=catalog,
        resolver=_resolver(config),
        journal=LocalControlJournalStore(),
        lease=LocalControlLease(),
    )


def _native_coordinator(config, catalog):
    from jebao_flow.local_control.wavemaker_lifecycle import NativeWavemakerController

    return NativeWavemakerController(
        catalog=catalog,
        resolver=_resolver(config) if config is not None else None,
        journal=LocalControlJournalStore(),
        lease=LocalControlLease(),
    )


def _run_wavemaker_native(args) -> int:
    if args.command == "wavemaker-status":
        _activate_current_hardware_safety_volume_identity()
        result = _native_coordinator(None, build_local_control_catalog()).status(args.request_id)
        _print_json(result)
        return 0
    if not args.write_enabled:
        raise ValueError("native 실행에는 명시적인 --write-enabled가 필요합니다")
    _activate_current_hardware_safety_volume_identity()
    prepared = _prepare_current(args)
    if prepared.config.runtime.mode is not RuntimeMode.CONTROL or prepared.config.runtime.dry_run:
        raise ValueError("관측 전용 또는 dry-run에서는 native 명령을 전송하지 않습니다")
    attach = args.command == "attach-wavemaker"
    assist = args.command == "assist-wavemaker-detach"
    cycle = args.command == "cycle-wavemaker-master"
    catalog = build_local_control_catalog(
        load_current_qualifications(args.qualifications) if attach or assist or cycle else (),
        current_content=prepared.manifest.content,
    )
    controller = _native_coordinator(prepared.config, catalog)
    if attach:
        plan = NativeWavemakerPlan.model_validate_json(args.file.read_bytes())
        names = (plan.master, *(s.logical_member for s in plan.slaves))
    else:
        request_id = args.attachment_id if args.command == "detach-wavemaker" else args.request_id
        names = tuple(t.logical_member for t in controller.targets_for(request_id))
    members = {item.logical_member: item for item in prepared.manifest.devices}
    devices = {item.id: item for item in prepared.config.devices}
    if any(
        name not in members
        or name not in devices
        or not devices[name].control.allow_hardware_writes
        for name in names
    ):
        raise ValueError("현재 native 대상이 없거나 장비 write 잠금 상태입니다")
    targets = tuple(members[name] for name in names)
    if attach:
        observation = (
            {"await_operator_observation": True} if args.await_operator_observation else {}
        )
        if args.master_cycle_on_async_failure:
            observation["master_cycle_on_async_failure"] = True
        task = controller.attach(
            request_id=args.request_id, plan=plan, targets=targets, **observation
        )
    elif args.command == "detach-wavemaker":
        task = controller.detach(attachment_id=args.attachment_id, targets=targets)
    elif assist:
        task = controller.assist_detach(request_id=args.request_id, targets=targets)
    elif cycle:
        task = controller.cycle_master(request_id=args.request_id, targets=targets)
    else:
        task = controller.recover_native(request_id=args.request_id, targets=targets)
    try:
        result = asyncio.run(task)
    except (OSError, RuntimeError, ValueError):
        request_id = args.attachment_id if args.command == "detach-wavemaker" else args.request_id
        records = []
        try:
            source = controller.status(request_id)
            records.append(source)
            if "master_cycle_request_id" in source:
                try:
                    records.append(controller.status(source["master_cycle_request_id"]))
                except KeyError:
                    pass
            if assist:
                try:
                    records.append(controller.status(source["assistance_request_id"]))
                except KeyError:
                    pass
            if source["detach_request_id"] != request_id:
                try:
                    records.append(controller.status(source["detach_request_id"]))
                except KeyError:
                    pass
        except (KeyError, OSError, RuntimeError, ValueError):
            pass
        _print_json(
            {
                "error": "native_operation_unverified",
                "records": records,
                "next_command": (
                    f"assist-wavemaker-detach --request-id {request_id} "
                    "--qualifications <current-file>"
                    if assist
                    else f"recover-wavemaker --request-id {request_id}"
                )
                if records
                else None,
            }
        )
        return 2
    _print_json(result)
    return 0 if result["accepted"] else 2


def _runtime_coordinator(config, catalog, *, write_enabled: bool):
    """Keep a read-only runtime from opening a writer journal or write lease."""

    return _coordinator(config, catalog) if write_enabled else None


def _runtime_catalog(qualifications, current_content, *, write_enabled: bool):
    """Keep a default service read-only even when current write receipts exist."""

    selected = qualifications if write_enabled else ()
    return build_local_control_catalog(selected, current_content=current_content)


def _runtime_qualifications(path, *, write_enabled: bool):
    """A read-only service never depends on retained write-authority files."""

    return load_current_qualifications(path) if write_enabled else ()


def _resolver(config) -> CurrentEndpointResolver:
    return CurrentEndpointResolver(
        GizwitsDiscovery(
            targets=config.observer.targets,
            bind_address=config.observer.bind_address,
        ),
        timeout_seconds=config.observer.discovery_timeout_seconds,
    )


def _current_mqtt_password(config) -> str | None:
    """Resolve the current secret without copying it into generated service bytes."""

    if config.mqtt.password_env is None:
        return None
    password_file = _environment_path(_MQTT_PASSWORD_FILE_ENV)
    if password_file is not None:
        return read_current_mqtt_password(password_file)
    password = config.mqtt.resolve_password()
    if password is None:
        raise ValueError("current MQTT password is unavailable")
    return password


def _current_hardware_safety_volume_id() -> str:
    """Read the current stable volume authority without exposing it in public output."""

    value = os.environ.get(HARDWARE_SAFETY_VOLUME_ID_ENV)
    if (
        value is None
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("current hardware-safety volume identity is unavailable")
    return value


def _activate_current_hardware_safety_volume_identity() -> None:
    """Load a generated service's private authority file after process start."""

    identity_file = _environment_path(HARDWARE_SAFETY_VOLUME_ID_FILE_ENV)
    if identity_file is None:
        return
    os.environ[HARDWARE_SAFETY_VOLUME_ID_ENV] = read_current_hardware_safety_volume_id(
        identity_file
    )


def _validate_current_compose_generation(generation: str | None) -> None:
    """Validate generated container inputs before any safety authority or config is consumed."""

    if generation:
        validate_current_compose_inputs(generation)


def _schedule_plans(manifest, config, explicit: tuple[str, ...]):
    by_member = {device.logical_member: device for device in manifest.devices}
    triples: list[tuple[NativeLinkageMode, str, str]] = []
    if explicit:
        for value in explicit:
            parts = value.split(":")
            if len(parts) != 3:
                raise ValueError("schedule pair must be MODE:MASTER:SLAVE")
            mode, master, slave = parts
            triples.append((NativeLinkageMode(mode), master, slave))
    else:
        for group in config.groups:
            if group.native_pair is None:
                continue
            triples.append(
                (
                    NativeLinkageMode(group.native_pair.relation.value),
                    group.native_pair.master,
                    group.native_pair.slave,
                )
            )
    slave_members = [slave for _mode, _master, slave in triples]
    if len(slave_members) != len(set(slave_members)):
        raise ValueError("one slave cannot belong to multiple current schedule topologies")
    grouped: dict[tuple[NativeLinkageMode, str], list[str]] = {}
    for mode, master, slave in triples:
        grouped.setdefault((mode, master), []).append(slave)
    plans: list[FreshMasterSchedulePlan] = []
    for (mode, master), slaves in grouped.items():
        try:
            master_binding = by_member[master]
            slave_bindings = tuple(by_member[slave] for slave in slaves)
        except KeyError as error:
            raise ValueError("schedule pair references a missing current logical member") from error
        plans.append(
            FreshMasterSchedulePlan(
                linkage_mode=mode,
                master=master_binding,
                slaves=tuple(FreshSlaveSchedule(target=slave) for slave in slave_bindings),
            )
        )
    return tuple(plans)


def _require_live_opt_in(args, config, projection) -> None:
    if projection.public_dict()["control_count"] == 0:
        return
    if not args.write_enabled:
        raise ValueError("qualified controls require the current --write-enabled invocation")
    if config.runtime.dry_run:
        raise ValueError("current private config still has runtime.dry_run enabled")
    controlled_members = {
        entity.logical_member for entity in projection.entities if entity.control_available
    }
    blocked = {
        device.id
        for device in config.devices
        if device.id in controlled_members and not device.control.allow_hardware_writes
    }
    if blocked:
        raise ValueError("current private config keeps one or more controlled devices write-locked")


async def _serve(adapter: LocalControlMqttAdapter) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass
    await adapter.run(stop_event)


def _public_install_summary(manifest) -> dict[str, object]:
    template_binding_complete = bool(manifest.content.template_sha256)
    return {
        "status": "prepared",
        "generation": manifest.generation,
        "rederived_at": manifest.rederived_at.isoformat(),
        "device_count": len(manifest.devices),
        "template_count": len(manifest.content.template_sha256),
        "template_binding_complete": template_binding_complete,
        "qualification_status": (
            "current_template_binding_available"
            if template_binding_complete
            else "no_current_template_binding_controls_remain_diagnostic"
        ),
        "app_catalog_sha256": manifest.content.app_catalog_sha256,
        "protocol_schema_sha256": manifest.content.protocol_schema_sha256,
        "previous_values_reused": False,
        "private_paths_exposed": False,
    }


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _parse_clock_minute(value: str) -> int:
    parts = value.split(":")
    malformed_parts = any(
        len(part) != 2 or not part.isascii() or not part.isdigit() for part in parts
    )
    if len(parts) != 2 or malformed_parts:
        raise ValueError("first dose time must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("first dose time must use a valid 24-hour clock value")
    return hour * 60 + minute


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _environment_paths(name: str) -> list[Path]:
    value = os.environ.get(name)
    return [Path(item) for item in value.split(os.pathsep) if item] if value else []


def _parse_attestation_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("role attestation time must use ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("role attestation time must include a UTC offset")
    return parsed


def _xdg_base(variable: str, fallback: tuple[str, ...]) -> Path:
    configured = os.environ.get(variable)
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return Path.home().joinpath(*fallback)


def _default_manifest_path() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", (".config",)) / "jebao-flow" / "local-install.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        return _run(parser.parse_args(argv))
    except KeyboardInterrupt:
        print("jebao-flow-local: operator_interrupt", file=sys.stderr)
        return 130
    except NativeRecoveryRequiredError as error:
        _print_json(
            {
                "error": "native_reverse_recovery_prohibited",
                "next_command": f"recover-wavemaker --request-id {error.request_id}",
                "detach_request_id": error.detach_id,
            }
        )
        return 2
    except (OSError, RuntimeError, ValueError):
        print("jebao-flow-local: current_operation_failed_closed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
