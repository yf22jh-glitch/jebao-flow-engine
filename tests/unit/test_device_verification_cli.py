import asyncio
import fcntl
import os
import signal
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from jebao_flow import device_verification_cli as cli
from jebao_flow import hardware_safety
from jebao_flow.config import AppConfig
from jebao_flow.devices.simulator import SimulatedJebaoDevice
from jebao_flow.devices.verification import (
    DeviceVerificationApplyError,
    DeviceVerificationErrorCode,
    DeviceVerificationPhase,
    DeviceVerificationRecord,
    DeviceVerificationRecoveryDeferred,
    DeviceVerificationRecoveryReason,
    JsonDeviceVerificationJournalStore,
)
from jebao_flow.hardware_guard import DeploymentHardwareGuard
from jebao_flow.persistence.qualification import JsonQualificationStore
from jebao_flow.protocol.models import (
    Capability,
    DeviceCapabilities,
    DeviceTarget,
    DiscoveredDevice,
    LinkageRole,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.safety.limits import PowerLimits

PRIVATE_VENDOR_ID = "private-vendor-device-id"
PRIVATE_MAC = "001122334455"
PRIVATE_ADDRESS = "192.0.2.10"


class _CorruptLowerDevice(SimulatedJebaoDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.write_targets: list[DeviceTarget] = []

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.write_targets.append(target)
        await super().write_target(target, guard=guard)
        if len(self.write_targets) == 2:
            self._state = self._state.model_copy(update={"power": target.power - 1})


class _RecordingDevice(SimulatedJebaoDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.write_targets: list[DeviceTarget] = []

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.write_targets.append(target)
        await super().write_target(target, guard=guard)


class _FailRestoreDevice(_RecordingDevice):
    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.write_targets.append(target)
        if target.power == 40:
            raise RuntimeError("private restore failure")
        await SimulatedJebaoDevice.write_target(self, target, guard=guard)


class _LatePostClearSignalDevice(_RecordingDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.late_signal_callback = None
        self._schedule_signal_on_read = False

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        await super().write_target(target, guard=guard)
        if len(self.write_targets) == 3:
            self._schedule_signal_on_read = True

    async def get_state(self):
        state = await super().get_state()
        if self._schedule_signal_on_read and self.late_signal_callback is not None:
            self._schedule_signal_on_read = False
            loop = asyncio.get_running_loop()
            loop.call_soon(self.late_signal_callback)
            loop.call_soon(self.late_signal_callback)
        return state


def _capabilities() -> DeviceCapabilities:
    return DeviceCapabilities(
        model=LOCAL_WAVEMAKER_PRO.name,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        readable=frozenset(Capability),
        writable=frozenset(
            {
                Capability.ENABLED,
                Capability.POWER,
                Capability.MODE,
                Capability.FREQUENCY,
                Capability.LINKAGE,
                Capability.TIMER,
            }
        ),
        power_limits=PowerLimits(min_power=30, max_power=45),
        power_step=1,
        native_modes=frozenset({"constant", "pulse", "sine"}),
        linkage_roles=frozenset(LinkageRole),
    )


async def _ready_device(
    device_class: type[SimulatedJebaoDevice] = _RecordingDevice,
    *,
    power: int = 40,
) -> SimulatedJebaoDevice:
    device = device_class("pro_left", capabilities=_capabilities())
    await device.connect()
    await device.set_enabled(True)
    await device.set_power(power)
    await device.set_mode("constant")
    await device.set_frequency(25)
    await device.set_linkage(LinkageRole.INDEPENDENT)
    await device.set_timer_enabled(False)
    await device.disconnect()
    device.commands.clear()
    if hasattr(device, "write_targets"):
        device.write_targets.clear()
    return device


def _config(*, extra_writers: tuple[dict, ...] = ()) -> AppConfig:
    devices = [
        {
            "id": "pro_left",
            "name": "Private left name",
            "type": "wavemaker",
            "product_key": LOCAL_WAVEMAKER_PRO.product_key,
            "address": None,
            "discovery": "auto",
            "identity": {
                "device_id": PRIVATE_VENDOR_ID,
                "mac_address": PRIVATE_MAC,
            },
            "limits": {"min_power": 30, "max_power": 45},
            "control": {
                "allow_hardware_writes": True,
                "minimum_command_interval_ms": 100,
                "readback_delay_ms": 0,
                "readback_attempts": 1,
            },
        },
        *extra_writers,
    ]
    return AppConfig.model_validate(
        {
            "instance": {"id": "main", "name": "Private aquarium"},
            "mqtt": {"host": "broker", "topic_prefix": "jebao-flow/test"},
            "runtime": {
                "state_path": "/data/state.json",
                "mode": "control",
                "dry_run": False,
            },
            "observer": {
                "enabled": False,
                "targets": ["255.255.255.255"],
                "discovery_timeout_seconds": 1,
            },
            "devices": devices,
        }
    )


def _extra_pro(index: int) -> dict:
    return {
        "id": f"pro_{index}",
        "name": f"Private Pro {index}",
        "type": "wavemaker",
        "product_key": LOCAL_WAVEMAKER_PRO.product_key,
        "discovery": "auto",
        "identity": {
            "device_id": f"private-vendor-{index}",
            "mac_address": f"0011223344{index:02x}",
        },
        "control": {
            "allow_hardware_writes": True,
            "minimum_command_interval_ms": 100,
            "readback_delay_ms": 0,
            "readback_attempts": 1,
        },
    }


def _discovered() -> list[DiscoveredDevice]:
    return [
        DiscoveredDevice(
            address=PRIVATE_ADDRESS,
            device_id=PRIVATE_VENDOR_ID,
            mac_address=PRIVATE_MAC,
            product_key=LOCAL_WAVEMAKER_PRO.product_key,
            model=LOCAL_WAVEMAKER_PRO.name,
        )
    ]


def _dependencies(
    root: Path,
    device: SimulatedJebaoDevice,
    *,
    clock=lambda: datetime.now(UTC),
    counters: dict[str, int] | None = None,
) -> cli.VerificationCliDependencies:
    async def discover(_config: AppConfig) -> list[DiscoveredDevice]:
        if counters is not None:
            counters["discover"] = counters.get("discover", 0) + 1
        return _discovered()

    def read_factory(_config, _address, _product_key):
        if counters is not None:
            counters["read_factory"] = counters.get("read_factory", 0) + 1
        return device

    def write_factory(_config, _app_config):
        if counters is not None:
            counters["write_factory"] = counters.get("write_factory", 0) + 1
        return device

    def guard_factory() -> DeploymentHardwareGuard:
        return DeploymentHardwareGuard(
            operation_lock_path=root / "hardware-operation.lock",
            latch_path=root / "emergency-stop.latch",
            poll_interval_seconds=0.001,
        )

    return cli.VerificationCliDependencies(
        discover=discover,
        read_only_device_factory=read_factory,
        writable_device_factory=write_factory,
        guard_factory=guard_factory,
        clock=clock,
        validate_safety_root=lambda: None,
    )


@pytest.fixture
def safety_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    return root


def _parse(*arguments: str):
    return cli.build_parser().parse_args(list(arguments))


async def _preflight(
    config: AppConfig,
    dependencies: cli.VerificationCliDependencies,
) -> cli.DeviceVerificationIntent:
    result = await cli.dispatch(
        config,
        _parse(
            "preflight-device",
            "--operation-id",
            "qualify_001",
            "--device",
            "pro_left",
            "--target-power",
            "35",
            "--duration",
            "0.3",
            "--verification-interval",
            "0.25",
        ),
        dependencies=dependencies,
    )
    assert result == 0
    intent = cli.JsonDeviceVerificationIntentStore(
        hardware_safety.verification_intent_path()
    ).load()
    assert intent is not None
    return intent


def _run_args(intent: cli.DeviceVerificationIntent):
    return _parse(
        "run-device-verification",
        "--operation-id",
        intent.operation_id,
        "--device",
        intent.device_id,
        "--target-power",
        str(intent.spec.target_power),
        "--duration",
        str(intent.spec.duration_seconds),
        "--verification-interval",
        str(intent.spec.verification_interval_seconds),
        "--confirm",
        intent.confirmation_token,
    )


def _recovery_record(
    intent: cli.DeviceVerificationIntent,
    *,
    phase: DeviceVerificationPhase = DeviceVerificationPhase.LOWER_POWER_ACTIVE,
    reason: DeviceVerificationRecoveryReason | None = None,
    now: datetime | None = None,
    expires_at: datetime | None = None,
) -> DeviceVerificationRecord:
    created = now or datetime.now(UTC)
    if reason is not None:
        phase = DeviceVerificationPhase.RECOVERY_REQUIRED
    return DeviceVerificationRecord(
        operation_id=intent.operation_id,
        phase=phase,
        spec=intent.spec,
        snapshot=intent.snapshot,
        created_at=created,
        updated_at=created,
        expires_at=expires_at or created + timedelta(seconds=1),
        write_started=phase is not DeviceVerificationPhase.PREPARED,
        recovery_reason=reason,
        error_code=(
            DeviceVerificationErrorCode.SAFETY_INTERLOCK
            if reason
            in {
                DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
                DeviceVerificationRecoveryReason.SAFETY_STOP_FAILED,
            }
            else DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
            if reason is DeviceVerificationRecoveryReason.RESTORE_FAILED
            else None
        ),
    )


def _persist_recovery_intent(intent: cli.DeviceVerificationIntent) -> None:
    cli.JsonDeviceVerificationIntentStore(hardware_safety.verification_intent_path()).save(
        intent.model_copy(
            update={
                "phase": cli.VerificationIntentPhase.RECOVERY_REQUIRED,
                "outcome": None,
                "updated_at": datetime.now(UTC),
            }
        )
    )


def _receipt_files(root: Path) -> list[Path]:
    directory = root / "qualifications"
    return [] if not directory.exists() else list(directory.glob("*.json"))


def test_parser_exposes_all_commands_and_caps_spec() -> None:
    parser = cli.build_parser(prog="delegated")
    assert parser.prog == "delegated"
    for command in (
        "preflight-device",
        "run-device-verification",
        "recover-device-verification",
        "verification-status",
    ):
        if command == "verification-status":
            assert parser.parse_args([command]).command == command
        elif command == "recover-device-verification":
            assert parser.parse_args([command, "--recovery-first"]).command == command
        else:
            arguments = [
                command,
                "--operation-id",
                "op",
                "--device",
                "pro_left",
                "--target-power",
                "35",
            ]
            if command == "run-device-verification":
                arguments.extend(["--confirm", "JFV-" + "A" * 20])
            assert parser.parse_args(arguments).command == command

    with pytest.raises(ValidationError):
        cli._spec_from_args(
            SimpleNamespace(
                operation_id="op",
                target_power=35,
                duration=10.01,
                verification_interval=0.1,
            )
        )


async def test_preflight_is_strictly_read_only_private_and_durable(
    safety_root: Path,
    capsys,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)

    intent = await _preflight(_config(), dependencies)

    output = capsys.readouterr().out
    assert intent.phase is cli.VerificationIntentPhase.ARMED
    assert intent.confirmation_token.startswith("JFV-")
    assert device.commands == []
    assert device.write_targets == []
    assert counters == {"discover": 1, "read_factory": 1}
    intent_path = hardware_safety.verification_intent_path()
    assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
    serialized = intent_path.read_text(encoding="utf-8")
    for private in (PRIVATE_VENDOR_ID, PRIVATE_MAC, PRIVATE_ADDRESS, "Private left name"):
        assert private not in output
        assert private not in serialized


async def test_preflight_holds_global_lease_before_discovery(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    lock_path = safety_root / "hardware-operation.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(cli.HardwareOperationBusyError):
            await _preflight(_config(), dependencies)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert counters == {}
    assert device.write_targets == []


async def test_only_selected_adapter_is_created_when_two_pro_writers_are_allowed(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    config = _config(extra_writers=(_extra_pro(2),))

    await _preflight(config, dependencies)

    assert counters == {"discover": 1, "read_factory": 1}


async def test_config_rejects_third_writer_and_non_pro_writer_before_discovery(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)

    with pytest.raises(cli.DeviceVerificationCliError, match="at most two"):
        await _preflight(
            _config(extra_writers=(_extra_pro(2), _extra_pro(3))),
            dependencies,
        )
    assert counters == {}

    other = _extra_pro(2)
    other.update(
        {
            "type": "return_pump",
            "product_key": "0696a19599bc484f8e1866f5ccf4ee7e",
        }
    )
    with pytest.raises(cli.DeviceVerificationCliError, match="only be enabled"):
        await _preflight(_config(extra_writers=(other,)), dependencies)
    assert counters == {}


async def test_fresh_snapshot_drift_is_zero_write_and_issues_no_receipt(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"power": 39})

    with pytest.raises(cli.DeviceVerificationConfirmationError):
        await cli.dispatch(config, _run_args(intent), dependencies=dependencies)

    assert device.write_targets == []
    assert device.commands == []
    assert not hardware_safety.verification_journal_path().exists()
    assert _receipt_files(safety_root) == []


async def test_success_issues_one_exact_24_hour_receipt(safety_root: Path) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)

    assert await cli.dispatch(config, _run_args(intent), dependencies=dependencies) == 0

    assert [target.power for target in device.write_targets] == [40, 35, 40]
    assert not hardware_safety.verification_journal_path().exists()
    terminal = cli.JsonDeviceVerificationIntentStore(
        hardware_safety.verification_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.phase is cli.VerificationIntentPhase.TERMINAL
    assert terminal.outcome is cli.VerificationIntentOutcome.QUALIFIED
    receipt = JsonQualificationStore(hardware_safety.qualification_directory()).load(
        intent.snapshot.physical_binding
    )
    assert receipt is not None
    assert receipt.operation_id == intent.operation_id
    assert receipt.original_power == 40
    assert receipt.step_power == 35
    assert receipt.valid_until - receipt.completed_at == timedelta(hours=24)


async def test_abort_before_first_write_issues_no_receipt(
    safety_root: Path,
    monkeypatch,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)

    original_connect = device.connect
    connect_started = asyncio.Event()
    permit_connect = asyncio.Event()

    async def blocked_connect() -> None:
        connect_started.set()
        await permit_connect.wait()
        await original_connect()

    device.connect = blocked_connect

    async def stop_immediately(controller, _device, spec, *_signal_context):
        task = asyncio.create_task(controller.run(spec))
        await connect_started.wait()
        assert await controller.stop(spec.operation_id) is True
        permit_connect.set()
        return await task

    monkeypatch.setattr(cli, "_run_with_signals", stop_immediately)
    assert await cli.dispatch(config, _run_args(intent), dependencies=dependencies) == 0

    assert device.commands == []
    assert _receipt_files(safety_root) == []
    terminal = cli.JsonDeviceVerificationIntentStore(
        hardware_safety.verification_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.outcome is cli.VerificationIntentOutcome.ABORTED


async def test_apply_failure_restores_but_issues_no_receipt(safety_root: Path) -> None:
    device = await _ready_device(_CorruptLowerDevice)
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)

    with pytest.raises(DeviceVerificationApplyError):
        await cli.dispatch(config, _run_args(intent), dependencies=dependencies)

    assert [target.power for target in device.write_targets] == [40, 35, 40]
    assert _receipt_files(safety_root) == []
    assert not hardware_safety.verification_journal_path().exists()


async def test_second_signal_durably_latches_recreates_journal_and_sends_off(
    safety_root: Path,
    monkeypatch,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"power": 39})
    original_connect = device.connect
    connect_started = asyncio.Event()
    permit_connect = asyncio.Event()

    async def blocked_connect() -> None:
        connect_started.set()
        await permit_connect.wait()
        await original_connect()

    device.connect = blocked_connect
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, tuple[object, tuple[object, ...]]] = {}

    def add_signal_handler(candidate, callback, *arguments) -> None:
        handlers[candidate] = (callback, arguments)

    def remove_signal_handler(candidate) -> bool:
        return handlers.pop(candidate, None) is not None

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)
    task = asyncio.create_task(cli.dispatch(config, _run_args(intent), dependencies=dependencies))
    await connect_started.wait()
    callback, arguments = handlers[signal.SIGINT]
    callback(*arguments)
    callback(*arguments)
    permit_connect.set()

    with pytest.raises(DeviceVerificationRecoveryDeferred):
        await task

    latch = hardware_safety.emergency_stop_latch_path()
    assert latch.exists()
    assert stat.S_IMODE(latch.stat().st_mode) == 0o600
    record = JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).load()
    assert record is not None
    assert record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
    assert record.snapshot.power == 39
    persisted_intent = cli.JsonDeviceVerificationIntentStore(
        hardware_safety.verification_intent_path()
    ).load()
    assert persisted_intent is not None
    assert persisted_intent.snapshot.power == 39
    assert device.write_targets[-1].enabled is False
    assert device.write_targets[-1].power == 0
    assert _receipt_files(safety_root) == []


async def test_queued_second_signal_after_journal_clear_recreates_and_sends_off(
    safety_root: Path,
    monkeypatch,
) -> None:
    device = await _ready_device(_LatePostClearSignalDevice)
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    original_connect = device.connect
    connect_started = asyncio.Event()
    permit_connect = asyncio.Event()

    async def blocked_connect() -> None:
        connect_started.set()
        await permit_connect.wait()
        await original_connect()

    device.connect = blocked_connect
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, tuple[object, tuple[object, ...]]] = {}

    def add_signal_handler(candidate, callback, *arguments) -> None:
        handlers[candidate] = (callback, arguments)

    def remove_signal_handler(candidate) -> bool:
        return handlers.pop(candidate, None) is not None

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)
    task = asyncio.create_task(cli.dispatch(config, _run_args(intent), dependencies=dependencies))
    await connect_started.wait()
    callback, arguments = handlers[signal.SIGTERM]
    device.late_signal_callback = lambda: callback(*arguments)
    permit_connect.set()

    with pytest.raises(DeviceVerificationRecoveryDeferred):
        await task

    record = JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).load()
    assert record is not None
    assert record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
    assert [target.power for target in device.write_targets] == [40, 35, 40, 0]
    assert _receipt_files(safety_root) == []


async def test_recent_automatic_recovery_restores_without_receipt(safety_root: Path) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"power": 35})
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(
        _recovery_record(intent)
    )

    assert (
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )
        == 0
    )

    assert [target.power for target in device.write_targets] == [40]
    assert _receipt_files(safety_root) == []
    assert not hardware_safety.verification_journal_path().exists()


async def test_explicit_attended_recovery_restores_stale_non_safety_record(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"power": 35})
    stale_time = datetime.now(UTC) - timedelta(minutes=2)
    record = _recovery_record(
        intent,
        now=stale_time,
        expires_at=stale_time + timedelta(seconds=1),
    )
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(record)
    token = cli.verification_recovery_token(
        config.instance.id,
        intent.device_id,
        intent.spec,
        intent.snapshot,
        record,
    )

    assert (
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--confirm", token),
            dependencies=dependencies,
        )
        == 0
    )

    assert [target.power for target in device.write_targets] == [40]
    assert _receipt_files(safety_root) == []
    assert not hardware_safety.verification_journal_path().exists()


async def test_recovery_failure_retains_journal_and_issues_no_receipt(
    safety_root: Path,
) -> None:
    device = await _ready_device(_FailRestoreDevice)
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"power": 35})
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(
        _recovery_record(intent)
    )

    with pytest.raises(cli.DeviceVerificationError):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )

    assert hardware_safety.verification_journal_path().exists()
    assert _receipt_files(safety_root) == []


async def test_stale_or_backwards_clock_blocks_automatic_recovery_zero_write(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    config = _config()
    dependencies = _dependencies(safety_root, device)
    intent = await _preflight(config, dependencies)
    base = datetime.now(UTC) - timedelta(minutes=2)
    record = _recovery_record(
        intent,
        now=base,
        expires_at=base + timedelta(seconds=1),
    )
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(record)

    with pytest.raises(cli.DeviceVerificationCliError, match="window expired"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )
    assert device.write_targets == []

    # Recreate with a future clock in the durable record and a caller clock behind it.
    hardware_safety.verification_journal_path().unlink()
    future = datetime.now(UTC) + timedelta(minutes=1)
    future_record = _recovery_record(intent, now=future)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(
        future_record
    )
    backwards = _dependencies(
        safety_root,
        device,
        clock=lambda: future - timedelta(seconds=1),
    )
    with pytest.raises(cli.DeviceVerificationCliError, match="backwards"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=backwards,
        )
    assert device.write_targets == []


async def test_stale_prepared_marker_is_read_and_closed_without_write(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    stale_time = datetime.now(UTC) - timedelta(days=1)
    record = _recovery_record(
        intent,
        phase=DeviceVerificationPhase.PREPARED,
        now=stale_time,
        expires_at=stale_time + timedelta(seconds=1),
    )
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(record)

    assert (
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )
        == 0
    )

    assert device.write_targets == []
    assert device.commands == []
    assert not hardware_safety.verification_journal_path().exists()
    assert _receipt_files(safety_root) == []


async def test_safety_recovery_requires_attended_jvr_and_latch_clear(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"enabled": False, "power": 35})
    record = _recovery_record(
        intent,
        reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
    )
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(record)

    with pytest.raises(cli.DeviceVerificationCliError, match="attended"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )
    assert device.write_targets == []

    token = cli.verification_recovery_token(
        config.instance.id,
        intent.device_id,
        intent.spec,
        intent.snapshot,
        record,
    )
    latch = safety_root / "emergency-stop.latch"
    latch.write_text("emergency_stop\n", encoding="utf-8")
    os.chmod(latch, 0o600)
    with pytest.raises(cli.DeviceVerificationCliError, match="latch"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--confirm", token),
            dependencies=dependencies,
        )
    assert device.write_targets == []

    latch.unlink()
    assert (
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--confirm", token),
            dependencies=dependencies,
        )
        == 0
    )
    assert [target.power for target in device.write_targets] == [40]
    assert device.write_targets[-1].enabled is True
    assert _receipt_files(safety_root) == []


async def test_safety_stop_failed_blocks_automatic_recovery_zero_write(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    config = _config()
    intent = await _preflight(config, dependencies)
    record = _recovery_record(
        intent,
        reason=DeviceVerificationRecoveryReason.SAFETY_STOP_FAILED,
    )
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(record)
    counters.clear()

    with pytest.raises(cli.DeviceVerificationCliError, match="attended"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )

    assert counters == {}
    assert device.write_targets == []


async def test_safety_retrip_invalidates_previous_jvr_before_adapter_creation(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    config = _config()
    intent = await _preflight(config, dependencies)
    first_record = _recovery_record(
        intent,
        reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
    )
    _persist_recovery_intent(intent)
    store = JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path())
    store.create(first_record)
    old_token = cli.verification_recovery_token(
        config.instance.id,
        intent.device_id,
        intent.spec,
        intent.snapshot,
        first_record,
    )
    retripped = first_record.model_copy(
        update={
            "recovery_reason": DeviceVerificationRecoveryReason.SAFETY_STOP_FAILED,
            "updated_at": first_record.updated_at + timedelta(microseconds=1),
        }
    )
    store.save(retripped)
    counters.clear()

    with pytest.raises(cli.DeviceVerificationConfirmationError, match="does not match"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--confirm", old_token),
            dependencies=dependencies,
        )

    assert counters == {}
    assert device.write_targets == []


async def test_native_nonterminal_intent_blocks_before_discovery_or_connection(
    safety_root: Path,
    monkeypatch,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    monkeypatch.setattr(
        cli.JsonHardwareTestIntentStore,
        "load",
        lambda _self: SimpleNamespace(phase=cli.HardwareTestIntentPhase.ARMED),
    )

    with pytest.raises(cli.DeviceVerificationCliError, match="native-linkage intent"):
        await _preflight(_config(), dependencies)

    assert counters == {}
    assert device.commands == []


async def test_native_journal_blocks_before_discovery_or_connection(
    safety_root: Path,
    monkeypatch,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    monkeypatch.setattr(cli.JsonLinkageJournalStore, "load", lambda _self: object())

    with pytest.raises(cli.DeviceVerificationCliError, match="native-linkage operation"):
        await _preflight(_config(), dependencies)

    assert counters == {}
    assert device.write_targets == []


async def test_recovery_rechecks_native_conflict_inside_global_lease_before_connect(
    safety_root: Path,
    monkeypatch,
) -> None:
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)
    config = _config()
    intent = await _preflight(config, dependencies)
    device._state = device._state.model_copy(update={"power": 35})
    _persist_recovery_intent(intent)
    JsonDeviceVerificationJournalStore(hardware_safety.verification_journal_path()).create(
        _recovery_record(intent)
    )
    native_reads = 0

    def load_native_intent(_store):
        nonlocal native_reads
        native_reads += 1
        if native_reads == 1:
            return None
        return SimpleNamespace(phase=cli.HardwareTestIntentPhase.ARMED)

    monkeypatch.setattr(cli.JsonHardwareTestIntentStore, "load", load_native_intent)
    counters.clear()

    with pytest.raises(cli.DeviceVerificationCliError, match="native-linkage intent"):
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )

    assert counters == {"discover": 1, "write_factory": 1}
    assert device.connected is False
    assert device.write_targets == []


@pytest.mark.parametrize(
    "relative_path",
    [
        "device-verification-intent.json",
        "device-verification.json",
        "native-linkage-intent.json",
        "native-linkage.json",
        "qualifications",
    ],
)
async def test_symlinked_safety_artifact_fails_before_discovery(
    safety_root: Path,
    tmp_path: Path,
    relative_path: str,
) -> None:
    target = tmp_path / f"outside-{relative_path.replace('/', '-')}"
    if relative_path == "qualifications":
        target.mkdir()
    else:
        target.write_text("{}", encoding="utf-8")
    (safety_root / relative_path).symlink_to(target)
    device = await _ready_device()
    counters: dict[str, int] = {}
    dependencies = _dependencies(safety_root, device, counters=counters)

    with pytest.raises(cli.DeviceVerificationCliError, match="unsafe"):
        await cli.dispatch(
            _config(),
            _parse("verification-status"),
            dependencies=dependencies,
        )

    assert counters == {}
    assert device.commands == []


async def test_status_is_sanitized_and_prints_jvr_not_private_identity(
    safety_root: Path,
    capsys,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    await _preflight(config, dependencies)
    capsys.readouterr()

    assert (
        await cli.dispatch(
            config,
            _parse("verification-status"),
            dependencies=dependencies,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "JVR-" in output
    assert "Persistent safety latch: clear" in output
    for private in (PRIVATE_VENDOR_ID, PRIVATE_MAC, PRIVATE_ADDRESS, "Private left name"):
        assert private not in output


async def test_status_remains_available_with_write_runtime_relocked(
    safety_root: Path,
    capsys,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    await _preflight(config, dependencies)
    capsys.readouterr()
    relocked = config.model_copy(
        update={"runtime": config.runtime.model_copy(update={"dry_run": True})}
    )

    assert (
        await cli.dispatch(
            relocked,
            _parse("verification-status"),
            dependencies=dependencies,
        )
        == 0
    )
    assert "Verification intent: armed" in capsys.readouterr().out


async def test_started_without_journal_closes_as_proven_no_write_and_no_receipt(
    safety_root: Path,
) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    intent = await _preflight(config, dependencies)
    cli.JsonDeviceVerificationIntentStore(hardware_safety.verification_intent_path()).save(
        intent.model_copy(
            update={"phase": cli.VerificationIntentPhase.STARTED, "updated_at": datetime.now(UTC)}
        )
    )

    assert (
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )
        == 0
    )
    assert device.write_targets == []
    assert _receipt_files(safety_root) == []
    terminal = cli.JsonDeviceVerificationIntentStore(
        hardware_safety.verification_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.outcome is cli.VerificationIntentOutcome.CRASHED_BEFORE_FIRST_WRITE


async def test_recovery_first_closes_armed_no_write_marker(safety_root: Path) -> None:
    device = await _ready_device()
    dependencies = _dependencies(safety_root, device)
    config = _config()
    await _preflight(config, dependencies)

    assert (
        await cli.dispatch(
            config,
            _parse("recover-device-verification", "--recovery-first"),
            dependencies=dependencies,
        )
        == 0
    )
    terminal = cli.JsonDeviceVerificationIntentStore(
        hardware_safety.verification_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.outcome is cli.VerificationIntentOutcome.PREVIEW_CANCELLED
    assert device.write_targets == []
    assert _receipt_files(safety_root) == []


def test_intent_store_rejects_symlink_and_uses_owner_only_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    path = root / "intent.json"
    store = cli.JsonDeviceVerificationIntentStore(path)
    now = datetime.now(UTC)
    device = SimulatedJebaoDevice("pro", capabilities=_capabilities())
    binding = device.physical_binding
    assert binding is not None
    spec = cli.DeviceVerificationSpec(
        operation_id="op",
        target_power=35,
        duration_seconds=1,
    )
    snapshot = cli.DeviceVerificationSnapshot(
        physical_binding=binding,
        enabled=True,
        power=40,
        mode="constant",
        frequency=25,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=False,
    )
    intent = cli.DeviceVerificationIntent(
        instance_id="main",
        operation_id="op",
        device_id="pro",
        phase=cli.VerificationIntentPhase.ARMED,
        confirmation_token=cli.verification_confirmation_token("main", "pro", spec, snapshot),
        spec=spec,
        snapshot=snapshot,
        created_at=now,
        updated_at=now,
    )

    store.save(intent)
    assert store.load() == intent
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with store.lease():
        lock_metadata = store.lock_path.stat()
        assert stat.S_IMODE(lock_metadata.st_mode) == 0o600
        assert lock_metadata.st_nlink == 1

    alias = root / "intent-alias"
    os.link(path, alias)
    with pytest.raises(cli.DeviceVerificationCliError, match="unsafe"):
        store.load()
    alias.unlink()

    path.unlink()
    outside = tmp_path / "outside"
    outside.write_text(intent.model_dump_json(), encoding="utf-8")
    path.symlink_to(outside)
    with pytest.raises(cli.DeviceVerificationCliError, match="unsafe"):
        store.load()


def test_intent_lease_rejects_hardlinked_lock(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    store = cli.JsonDeviceVerificationIntentStore(root / "intent.json")
    store.lock_path.write_text("lock\n", encoding="utf-8")
    os.chmod(store.lock_path, 0o600)
    os.link(store.lock_path, root / "lock-alias")

    with pytest.raises(cli.DeviceVerificationCliError, match="changed"):
        with store.lease():
            pass


def test_terminal_callback_precedes_journal_clear(tmp_path: Path) -> None:
    delegate = JsonDeviceVerificationJournalStore(tmp_path / "journal.json")
    now = datetime.now(UTC)
    device = SimulatedJebaoDevice("pro", capabilities=_capabilities())
    binding = device.physical_binding
    assert binding is not None
    spec = cli.DeviceVerificationSpec(operation_id="op", target_power=35, duration_seconds=1)
    snapshot = cli.DeviceVerificationSnapshot(
        physical_binding=binding,
        enabled=True,
        power=40,
        mode="constant",
        frequency=25,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=False,
    )
    record = DeviceVerificationRecord(
        operation_id="op",
        phase=DeviceVerificationPhase.PREPARED,
        spec=spec,
        snapshot=snapshot,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=1),
    )
    delegate.create(record)
    terminal_was_persisted = False

    def before_clear() -> None:
        nonlocal terminal_was_persisted
        assert delegate.load() == record
        terminal_was_persisted = True

    wrapper = cli.ConfirmingDeviceVerificationStore(
        delegate,
        instance_id="main",
        device_id="pro",
        expected_token=cli.verification_confirmation_token("main", "pro", spec, snapshot),
        before_create=lambda: None,
        before_clear=before_clear,
    )
    wrapper.clear()

    assert terminal_was_persisted is True
    assert delegate.load() is None
