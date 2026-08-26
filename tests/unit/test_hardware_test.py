import asyncio
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow import hardware_guard, hardware_safety, hardware_test
from jebao_flow.config import AppConfig
from jebao_flow.devices import (
    LinkageRecoveryReason,
    LinkageRollbackError,
    LinkageSafetyInterlock,
    LinkageStopReason,
    LinkageTestSpec,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    SimulatedJebaoDevice,
    TemporaryLinkageController,
)
from jebao_flow.persistence import (
    DeviceQualificationReceipt,
    JsonLinkageJournalStore,
    JsonQualificationStore,
)
from jebao_flow.protocol.models import (
    Capability,
    DeviceCapabilities,
    DeviceSchedule,
    DeviceState,
    DiscoveredDevice,
    LinkageRole,
)
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.safety.limits import PowerLimits

_VENDOR_MASTER_ID = "vendor-master-must-not-print"
_VENDOR_SLAVE_ID = "vendor-slave-must-not-print"
_MASTER_MAC = "aabbccddee01"
_SLAVE_MAC = "aabbccddee02"


@pytest.fixture(autouse=True)
def _shared_hardware_safety_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hardware_safety,
        "_HARDWARE_SAFETY_ROOT",
        tmp_path / "shared-hardware-safety",
    )
    monkeypatch.setattr(hardware_test, "validate_hardware_safety_root", lambda: None)
    monkeypatch.setattr(hardware_guard, "validate_hardware_safety_root", lambda: None)


def _config(tmp_path: Path, **runtime_overrides: object) -> AppConfig:
    runtime = {
        "state_path": str(tmp_path / "state.json"),
        "mode": "control",
        "dry_run": False,
    }
    runtime.update(runtime_overrides)
    return AppConfig.model_validate(
        {
            "instance": {"id": "main", "name": "Aquarium"},
            "mqtt": {"host": "mqtt.local", "topic_prefix": "jebao-flow/main"},
            "runtime": runtime,
            "observer": {
                "enabled": False,
                "targets": ["192.0.2.255"],
                "discovery_timeout_seconds": 0.1,
            },
            "devices": [
                {
                    "id": "pro_left",
                    "name": "Left Pro",
                    "type": "wavemaker",
                    "identity": {
                        "device_id": _VENDOR_MASTER_ID,
                        "mac_address": _MASTER_MAC,
                    },
                    "limits": {"min_power": 30, "max_power": 75},
                    "control": {"allow_hardware_writes": True},
                },
                {
                    "id": "pro_right",
                    "name": "Right Pro",
                    "type": "wavemaker",
                    "identity": {
                        "device_id": _VENDOR_SLAVE_ID,
                        "mac_address": _SLAVE_MAC,
                    },
                    "limits": {"min_power": 30, "max_power": 75},
                    "control": {"allow_hardware_writes": True},
                },
                {
                    "id": "return_main",
                    "name": "Return",
                    "type": "return_pump",
                    "address": "return.local",
                    "product_key": "0696a19599bc484f8e1866f5ccf4ee7e",
                    "control": {"allow_hardware_writes": False},
                },
            ],
        }
    )


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
        power_limits=PowerLimits(min_power=30, max_power=75),
        native_modes=frozenset({"constant", "pulse", "sine"}),
        linkage_roles=frozenset(
            {
                LinkageRole.INDEPENDENT,
                LinkageRole.MASTER,
                LinkageRole.SYNC_SLAVE,
                LinkageRole.ASYNC_SLAVE,
            }
        ),
    )


def _device(device_id: str, power: int) -> SimulatedJebaoDevice:
    device = SimulatedJebaoDevice(device_id, capabilities=_capabilities())
    device._state = DeviceState(  # noqa: SLF001 - deterministic simulator fixture
        online=False,
        enabled=True,
        power=power,
        mode="constant",
        frequency=20,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=False,
        schedule=DeviceSchedule(enabled=False),
    )
    return device


class _Discovery:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
        del timeout_seconds
        return [
            DiscoveredDevice(
                address="left.local",
                device_id=_VENDOR_MASTER_ID,
                mac_address=_MASTER_MAC,
                product_key=LOCAL_WAVEMAKER_PRO.product_key,
            ),
            DiscoveredDevice(
                address="right.local",
                device_id=_VENDOR_SLAVE_ID,
                mac_address=_SLAVE_MAC,
                product_key=LOCAL_WAVEMAKER_PRO.product_key,
            ),
        ]


class _EmergencyAfterClearStore(JsonLinkageJournalStore):
    def __init__(self, path: Path, emergency_event: asyncio.Event) -> None:
        super().__init__(path)
        self.emergency_event = emergency_event

    def clear(self) -> None:
        super().clear()
        self.emergency_event.set()


def _args(command: str, *, confirmation: str | None = None) -> list[str]:
    values = [
        command,
        "--operation-id",
        "attended_test_001",
        "--master",
        "pro_left",
        "--slave",
        "pro_right",
        "--slave-role",
        "sync_slave",
        "--mode",
        "sine",
        "--master-power",
        "35",
        "--slave-power",
        "33",
        "--frequency",
        "20",
        "--duration",
        "0.02",
        "--verification-interval",
        "0.005",
    ]
    if confirmation is not None:
        values.extend(("--confirm", confirmation))
    return values


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    devices: dict[str, SimulatedJebaoDevice],
) -> None:
    monkeypatch.setattr(hardware_test, "load_config", lambda _: config)
    monkeypatch.setattr(hardware_test, "GizwitsDiscovery", _Discovery)
    monkeypatch.setattr(
        hardware_test,
        "create_read_only_lan_device",
        lambda device, address, product_key: devices[device.id],
    )
    monkeypatch.setattr(
        hardware_test,
        "create_lan_device",
        lambda device, runtime: devices[device.id],
    )
    qualification_store = JsonQualificationStore(
        hardware_test.canonical_qualification_directory(config)
    )
    completed_at = datetime.now(UTC)
    for device_id in ("pro_left", "pro_right"):
        device = devices[device_id]
        binding = device.physical_binding
        assert binding is not None
        qualification_store.save(
            DeviceQualificationReceipt(
                operation_id=f"qualify_{device_id}",
                device_id=device_id,
                physical_binding=binding,
                original_power=device._state.power,  # noqa: SLF001 - deterministic fixture
                step_power=device._state.power - 1,  # noqa: SLF001 - deterministic fixture
                completed_at=completed_at,
                valid_until=completed_at + timedelta(hours=24),
            )
        )


def _token(output: str, label: str = "Confirmation token") -> str:
    match = re.search(rf"{label}: (JF[LR]-[A-F0-9]+)", output)
    assert match is not None
    return match.group(1)


def test_preflight_is_read_only_private_and_arms_exact_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)

    assert hardware_test.main(_args("preflight")) == 0

    output = capsys.readouterr().out
    assert _token(output).startswith("JFL-")
    assert "no control frame was sent" in output
    assert _VENDOR_MASTER_ID not in output
    assert _VENDOR_SLAVE_ID not in output
    assert _MASTER_MAC not in output
    assert _SLAVE_MAC not in output
    assert "passcode" not in output.lower()
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []

    intent_path = hardware_test.canonical_intent_path(config)
    intent = hardware_test.JsonHardwareTestIntentStore(intent_path).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.ARMED
    assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
    assert hardware_test.canonical_journal_path(config).parent == (
        tmp_path / "shared-hardware-safety"
    )


@pytest.mark.parametrize("unsafe_kind", ["fifo", "hardlink", "mode"])
def test_native_intent_store_rejects_unsafe_files_without_blocking(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    path = tmp_path / "native-intent.json"
    if unsafe_kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)
        if unsafe_kind == "hardlink":
            os.link(path, tmp_path / "intent-alias")
        else:
            path.chmod(0o640)

    with pytest.raises(hardware_test.HardwareTestError, match="unsafe metadata"):
        hardware_test.JsonHardwareTestIntentStore(path).load()


def test_preflight_requires_current_single_device_qualifications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    binding = devices["pro_right"].physical_binding
    assert binding is not None
    store = JsonQualificationStore(hardware_test.canonical_qualification_directory(config))
    store.path_for(binding).unlink()

    assert hardware_test.main(_args("preflight")) == 2

    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


def test_receipt_deleted_after_preflight_blocks_run_before_first_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    token = _token(capsys.readouterr().out)
    binding = devices["pro_left"].physical_binding
    assert binding is not None
    store = JsonQualificationStore(hardware_test.canonical_qualification_directory(config))
    store.path_for(binding).unlink()

    assert hardware_test.main(_args("run-native-linkage", confirmation=token)) == 2

    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert hardware_test.canonical_journal_path(config).exists() is False


def test_nonterminal_device_verification_blocks_native_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    path = hardware_safety.verification_intent_path()
    path.write_text('{"phase":"armed"}\n', encoding="utf-8")
    path.chmod(0o600)

    assert hardware_test.main(_args("preflight")) == 2

    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


@pytest.mark.parametrize(
    ("runtime", "observer_enabled", "allowed_right"),
    [
        ({"mode": "observer", "dry_run": False}, False, True),
        ({"mode": "control", "dry_run": True}, False, True),
        ({"mode": "control", "dry_run": False}, True, True),
        ({"mode": "control", "dry_run": False}, False, False),
    ],
)
def test_environment_interlocks_fail_before_discovery_or_write(
    tmp_path: Path,
    runtime: dict[str, object],
    observer_enabled: bool,
    allowed_right: bool,
) -> None:
    config = _config(tmp_path, **runtime)
    raw = config.model_dump(mode="python")
    raw["observer"]["enabled"] = observer_enabled
    raw["devices"][1]["control"]["allow_hardware_writes"] = allowed_right
    changed = AppConfig.model_validate(raw)

    with pytest.raises(hardware_test.HardwareTestError):
        hardware_test._validate_config(
            changed,
            frozenset({"pro_left", "pro_right"}),
        )


def test_write_identity_requires_both_vendor_id_and_mac(tmp_path: Path) -> None:
    raw = _config(tmp_path).model_dump(mode="python")
    raw["devices"][1]["identity"]["device_id"] = None
    config = AppConfig.model_validate(raw)

    with pytest.raises(hardware_test.HardwareTestError, match="both vendor ID and MAC"):
        hardware_test._validate_config(config, frozenset({"pro_left", "pro_right"}))


def test_attended_duration_is_capped_at_ten_seconds() -> None:
    args = hardware_test.build_parser().parse_args(
        [value if value != "0.02" else "10.01" for value in _args("preflight")]
    )

    with pytest.raises(hardware_test.HardwareTestError, match="10 seconds"):
        hardware_test._spec_from_args(args)


def test_physical_lease_is_cross_instance_and_privacy_preserving(tmp_path: Path) -> None:
    config = _config(tmp_path)
    selected = hardware_test._validate_config(
        config,
        frozenset({"pro_left", "pro_right"}),
    )
    first = hardware_test.PhysicalDeviceLease.from_selected(config, selected)
    second = hardware_test.PhysicalDeviceLease.from_selected(config, selected)

    with first.acquire():
        with pytest.raises(hardware_test.HardwareTestError, match="physical device"):
            with second.acquire():
                pass

    lock_names = " ".join(
        path.name for path in hardware_test.canonical_hardware_lock_directory(config).iterdir()
    )
    assert _VENDOR_MASTER_ID not in lock_names
    assert _VENDOR_SLAVE_ID not in lock_names
    assert _MASTER_MAC not in lock_names
    assert _SLAVE_MAC not in lock_names


def test_wrong_confirmation_sends_zero_writes_and_keeps_preview_armed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()

    assert hardware_test.main(_args("run-native-linkage", confirmation="JFL-WRONG")) == 2

    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert not hardware_test.canonical_journal_path(config).exists()
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.ARMED


def test_changed_state_invalidates_preview_before_first_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    token = _token(capsys.readouterr().out)
    devices["pro_right"]._state = devices["pro_right"]._state.model_copy(  # noqa: SLF001
        update={"power": 37}
    )

    assert hardware_test.main(_args("run-native-linkage", confirmation=token)) == 2

    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert not hardware_test.canonical_journal_path(config).exists()
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL


def test_confirmed_run_restores_and_terminal_intent_prevents_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    token = _token(capsys.readouterr().out)

    assert hardware_test.main(_args("run-native-linkage", confirmation=token)) == 0
    capsys.readouterr()

    assert devices["pro_left"]._state.power == 34  # noqa: SLF001
    assert devices["pro_right"]._state.power == 36  # noqa: SLF001
    assert devices["pro_left"]._state.linkage is LinkageRole.INDEPENDENT  # noqa: SLF001
    assert devices["pro_right"]._state.linkage is LinkageRole.INDEPENDENT  # noqa: SLF001
    assert devices["pro_left"]._state.timer_enabled is False  # noqa: SLF001
    assert devices["pro_right"]._state.timer_enabled is False  # noqa: SLF001
    assert not hardware_test.canonical_journal_path(config).exists()
    first_command_count = sum(len(device.commands) for device in devices.values())
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert intent.outcome == "restored"

    assert hardware_test.main(_args("run-native-linkage", confirmation=token)) == 2
    assert sum(len(device.commands) for device in devices.values()) == first_command_count


async def test_sigint_event_requests_normal_stop_and_waits_for_restore(tmp_path: Path) -> None:
    master = _device("pro_left", 34)
    slave = _device("pro_right", 36)
    await master.connect()
    await slave.connect()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    interlock = LinkageSafetyInterlock(initially_permitted=True)
    controller = TemporaryLinkageController(
        {"pro_left": master, "pro_right": slave},
        store,
        safety_interlock=interlock,
    )
    spec = LinkageTestSpec(
        operation_id="sigint_test",
        master_device_id="pro_left",
        slave_device_id="pro_right",
        slave_role=LinkageRole.SYNC_SLAVE,
        mode="sine",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=5,
        verification_interval_seconds=0.01,
    )
    interrupt = asyncio.Event()

    async def trigger() -> None:
        while controller.active_operation_id is None:  # noqa: ASYNC110
            await asyncio.sleep(0)
        interrupt.set()

    trigger_task = asyncio.create_task(trigger())
    result = await hardware_test._run_with_sigint(
        controller,
        spec,
        interrupt_event=interrupt,
    )
    await trigger_task

    assert result.stop_reason is LinkageStopReason.MANUAL
    assert store.load() is None
    assert (await master.get_state()).power == 34
    assert (await slave.get_state()).power == 36


async def test_second_stop_signal_persists_latch_and_defers_on_state_restore(
    tmp_path: Path,
) -> None:
    master = _device("pro_left", 34)
    slave = _device("pro_right", 36)
    await master.connect()
    await slave.connect()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    latch_path = tmp_path / "main-emergency-stop.latch"
    interlock = hardware_test.PersistentSafetyInterlock(latch_path)
    interlock.clear()
    controller = TemporaryLinkageController(
        {"pro_left": master, "pro_right": slave},
        store,
        safety_interlock=interlock,
    )
    spec = LinkageTestSpec(
        operation_id="double_signal_test",
        master_device_id="pro_left",
        slave_device_id="pro_right",
        slave_role=LinkageRole.SYNC_SLAVE,
        mode="sine",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=5,
        verification_interval_seconds=0.01,
    )
    first_signal = asyncio.Event()
    second_signal = asyncio.Event()

    async def trigger() -> None:
        while controller.active_operation_id is None:  # noqa: ASYNC110
            await asyncio.sleep(0)
        first_signal.set()
        second_signal.set()

    trigger_task = asyncio.create_task(trigger())
    with pytest.raises(LinkageRollbackError):
        await hardware_test._run_with_sigint(
            controller,
            spec,
            interrupt_event=first_signal,
            emergency_event=second_signal,
            safety_interlock=interlock,
            safety_latch_path=latch_path,
        )
    await trigger_task

    record = store.load()
    assert record is not None
    assert record.phase is LinkageTransactionPhase.RECOVERY_REQUIRED
    assert record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert latch_path.read_text(encoding="utf-8") == "emergency_stop\n"
    assert (await master.get_state()).enabled is False
    assert (await slave.get_state()).enabled is False


async def test_latch_persist_failure_still_durably_defers_before_safe_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master = _device("pro_left", 34)
    slave = _device("pro_right", 36)
    await master.connect()
    await slave.connect()
    store = JsonLinkageJournalStore(tmp_path / "linkage.json")
    latch_path = tmp_path / "main-emergency-stop.latch"
    interlock = hardware_test.PersistentSafetyInterlock(latch_path)
    interlock.clear()
    controller = TemporaryLinkageController(
        {"pro_left": master, "pro_right": slave},
        store,
        safety_interlock=interlock,
    )
    spec = LinkageTestSpec(
        operation_id="failed_latch_test",
        master_device_id="pro_left",
        slave_device_id="pro_right",
        slave_role=LinkageRole.SYNC_SLAVE,
        mode="sine",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=5,
        verification_interval_seconds=0.01,
    )
    emergency = asyncio.Event()

    def fail_latch(_path: Path) -> None:
        raise hardware_test.HardwareTestError("cannot persist the emergency-stop safety latch")

    monkeypatch.setattr(hardware_test, "activate_persistent_safety_latch", fail_latch)

    async def trigger() -> None:
        while True:
            record = store.load()
            if record is not None and record.phase is LinkageTransactionPhase.ACTIVE:
                emergency.set()
                return
            await asyncio.sleep(0)

    trigger_task = asyncio.create_task(trigger())
    with pytest.raises(hardware_test.HardwareTestError, match="cannot persist"):
        await hardware_test._run_with_sigint(
            controller,
            spec,
            emergency_event=emergency,
            safety_interlock=interlock,
            safety_latch_path=latch_path,
        )
    await trigger_task

    pending = store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert not latch_path.exists()
    assert (await master.get_state()).enabled is False
    assert (await slave.get_state()).enabled is False


async def test_emergency_arriving_after_journal_clear_recreates_safety_record_and_stops(
    tmp_path: Path,
) -> None:
    master = _device("pro_left", 34)
    slave = _device("pro_right", 36)
    await master.connect()
    await slave.connect()
    emergency = asyncio.Event()
    store = _EmergencyAfterClearStore(tmp_path / "linkage.json", emergency)
    latch_path = tmp_path / "main-emergency-stop.latch"
    interlock = hardware_test.PersistentSafetyInterlock(latch_path)
    interlock.clear()
    controller = TemporaryLinkageController(
        {"pro_left": master, "pro_right": slave},
        store,
        safety_interlock=interlock,
    )
    spec = LinkageTestSpec(
        operation_id="late_emergency_test",
        master_device_id="pro_left",
        slave_device_id="pro_right",
        slave_role=LinkageRole.SYNC_SLAVE,
        mode="sine",
        master_power=35,
        slave_power=33,
        frequency=20,
        duration_seconds=5,
        verification_interval_seconds=0.01,
    )
    interrupt = asyncio.Event()

    async def trigger_normal_stop() -> None:
        while True:
            record = store.load()
            if record is not None and record.phase is LinkageTransactionPhase.ACTIVE:
                interrupt.set()
                return
            await asyncio.sleep(0)

    trigger_task = asyncio.create_task(trigger_normal_stop())
    with pytest.raises(LinkageRollbackError, match="safety interlock"):
        await hardware_test._run_with_sigint(
            controller,
            spec,
            interrupt_event=interrupt,
            emergency_event=emergency,
            safety_interlock=interlock,
            safety_latch_path=latch_path,
            late_emergency_cleanup=controller.enforce_safety_stop,
        )
    await trigger_task

    pending = store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert latch_path.exists()
    assert (await master.get_state()).enabled is False
    assert (await slave.get_state()).enabled is False


def test_started_intent_without_journal_closes_terminal_with_zero_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )

    assert hardware_test.main(["recover-linkage"]) == 0
    assert "proven no-write" in capsys.readouterr().out
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert not hardware_test.canonical_journal_path(config).exists()
    terminal = intent_store.load()
    assert terminal is not None
    assert terminal.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert terminal.outcome == "crashed_before_first_write"


def test_pending_journal_recovers_exact_snapshot_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )
    now = datetime.now(UTC)
    JsonLinkageJournalStore(hardware_test.canonical_journal_path(config)).create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=10),
        )
    )

    async def leave_active_state() -> None:
        for device_id, role, power in (
            ("pro_left", LinkageRole.MASTER, 35),
            ("pro_right", LinkageRole.SYNC_SLAVE, 33),
        ):
            device = devices[device_id]
            await device.connect()
            await device.write_target(
                hardware_test.DeviceTarget(
                    enabled=True,
                    power=power,
                    mode="sine",
                    frequency=20,
                    linkage=role,
                    timer_enabled=False,
                )
            )
            await device.disconnect()

    asyncio.run(leave_active_state())
    before_preview = sum(len(device.commands) for device in devices.values())
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert sum(len(device.commands) for device in devices.values()) == before_preview

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 0
    assert devices["pro_left"]._state.power == 34  # noqa: SLF001
    assert devices["pro_right"]._state.power == 36  # noqa: SLF001
    assert devices["pro_left"]._state.timer_enabled is False  # noqa: SLF001
    assert devices["pro_right"]._state.timer_enabled is False  # noqa: SLF001
    assert not hardware_test.canonical_journal_path(config).exists()
    recovered_intent = intent_store.load()
    assert recovered_intent is not None
    assert recovered_intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert recovered_intent.outcome == "recovered"


@pytest.mark.parametrize(
    "recovery_reason",
    [LinkageRecoveryReason.SAFETY_INTERLOCK, LinkageRecoveryReason.RESTORE_FAILED],
)
def test_status_reports_current_jfr_and_old_revision_token_sends_zero_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recovery_reason: LinkageRecoveryReason,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )
    now = datetime.now(UTC)
    record = LinkageTransactionRecord(
        operation_id=intent.operation_id,
        phase=LinkageTransactionPhase.ACTIVE,
        spec=intent.spec,
        snapshots=intent.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=10),
    )
    journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    journal_store.create(record)

    assert hardware_test.main(["status"]) == 0
    old_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert old_token == hardware_test.recovery_confirmation_token(
        config.instance.id,
        record.spec,
        record.snapshots,
        record,
    )

    revised = record.model_copy(
        update={
            "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
            "recovery_reason": recovery_reason,
            "updated_at": now + timedelta(microseconds=1),
            "error": f"simulated {recovery_reason.value}",
            "failed_device_ids": ("pro_left",),
        }
    )
    journal_store.save(revised)
    assert hardware_test.main(["status"]) == 0
    current_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert current_token == hardware_test.recovery_confirmation_token(
        config.instance.id,
        revised.spec,
        revised.snapshots,
        revised,
    )
    assert current_token != old_token

    assert hardware_test.main(["recover-linkage", "--confirm", old_token]) == 2
    assert "token does not match" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert journal_store.load() == revised


@pytest.mark.parametrize("mismatch", ["spec", "snapshots"])
def test_intent_and_journal_payload_mismatch_sends_zero_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mismatch: str,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    spec = intent.spec
    snapshots = intent.snapshots
    if mismatch == "spec":
        spec = spec.model_copy(update={"master_power": spec.master_power + 1})
    else:
        snapshots = (
            snapshots[0].model_copy(update={"power": snapshots[0].power + 1}),
            snapshots[1],
        )
    now = datetime.now(UTC)
    journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    journal_store.create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=spec,
            snapshots=snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=10),
        )
    )

    assert hardware_test.main(["recover-linkage"]) == 2
    assert "intent and recovery journal disagree" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert journal_store.load() is not None


def test_recovery_first_reconnects_boundedly_then_restores_exact_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )
    now = datetime.now(UTC)
    JsonLinkageJournalStore(hardware_test.canonical_journal_path(config)).create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=10),
        )
    )

    async def leave_active_state() -> None:
        for device_id, role, power in (
            ("pro_left", LinkageRole.MASTER, 35),
            ("pro_right", LinkageRole.SYNC_SLAVE, 33),
        ):
            device = devices[device_id]
            await device.connect()
            await device.write_target(
                hardware_test.DeviceTarget(
                    enabled=True,
                    power=power,
                    mode="sine",
                    frequency=20,
                    linkage=role,
                    timer_enabled=False,
                )
            )
            await device.disconnect()

    asyncio.run(leave_active_state())
    original_recover_once = hardware_test._recover_once
    attempts = 0

    async def flaky_recover_once(*args: object, **kwargs: object) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("simulated reconnect failure")
        return await original_recover_once(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hardware_test, "_recover_once", flaky_recover_once)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0)

    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 0
    assert attempts == 3
    assert devices["pro_left"]._state.power == 34  # noqa: SLF001
    assert devices["pro_right"]._state.power == 36  # noqa: SLF001
    assert not hardware_test.canonical_journal_path(config).exists()


def test_recovery_first_with_persistent_latch_sends_zero_on_restore_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )
    now = datetime.now(UTC)
    journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    journal_store.create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=10),
        )
    )
    hardware_test.activate_persistent_safety_latch(
        hardware_test.canonical_safety_latch_path(config)
    )

    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 2
    stderr = capsys.readouterr().err
    assert "persistent safety latch" in stderr
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert journal_store.load() is not None


def test_safety_recovery_reason_blocks_automatic_restore_without_latch_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.RECOVERY_REQUIRED})
    )
    now = datetime.now(UTC)
    journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    journal_store.create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.RECOVERY_REQUIRED,
            recovery_reason=LinkageRecoveryReason.SAFETY_INTERLOCK,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=10),
            failed_device_ids=("pro_left", "pro_right"),
        )
    )

    assert not hardware_test.canonical_safety_latch_path(config).exists()
    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 2
    assert "attended confirmation" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert journal_store.load() is not None

    assert hardware_test.main(["recover-linkage"]) == 0
    assert "Recovery confirmation token" in capsys.readouterr().out
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


def test_status_is_sanitized_for_fresh_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)

    assert hardware_test.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "One-shot intent: none" in output
    assert "Recovery journal: none" in output
    assert "Persistent safety latch: clear" in output
    assert _VENDOR_MASTER_ID not in output
    assert _MASTER_MAC not in output


def test_timer_on_preflight_refuses_before_any_control_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    devices["pro_left"]._state = devices["pro_left"]._state.model_copy(  # noqa: SLF001
        update={"timer_enabled": True, "schedule": DeviceSchedule(enabled=True)}
    )
    _install_fakes(monkeypatch, config, devices)

    assert hardware_test.main(_args("preflight")) == 2

    assert "disable TimerON" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


@pytest.mark.parametrize(
    ("control_update", "message"),
    [
        ({"minimum_command_interval_ms": 2001}, "command interval"),
        ({"readback_delay_ms": 1001}, "read-back delay"),
        ({"readback_attempts": 4}, "read-back attempts"),
    ],
)
def test_hardware_timing_tuning_is_bounded(
    tmp_path: Path,
    control_update: dict[str, int],
    message: str,
) -> None:
    raw = _config(tmp_path).model_dump(mode="python")
    raw["devices"][0]["control"].update(control_update)
    config = AppConfig.model_validate(raw)

    with pytest.raises(hardware_test.HardwareTestError, match=message):
        hardware_test._validate_config(config, frozenset({"pro_left", "pro_right"}))


def test_recovery_first_is_a_clean_noop_without_pending_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)

    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 0

    assert "No unfinished" in capsys.readouterr().out
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


def test_stale_automatic_recovery_is_attended_only_and_sends_zero_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    JsonLinkageJournalStore(hardware_test.canonical_journal_path(config)).create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=created_at,
            updated_at=created_at,
            expires_at=created_at + timedelta(seconds=10),
        )
    )

    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 2

    assert "automatic recovery window expired" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


def test_future_updated_linkage_record_blocks_automatic_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    intent_store.save(
        intent.model_copy(update={"phase": hardware_test.HardwareTestIntentPhase.STARTED})
    )
    now = datetime.now(UTC)
    JsonLinkageJournalStore(hardware_test.canonical_journal_path(config)).create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.ACTIVE,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=now - timedelta(seconds=1),
            updated_at=now + timedelta(minutes=1),
            expires_at=now + timedelta(seconds=10),
        )
    )

    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 2

    assert "wall clock moved" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


def test_nonterminal_intent_blocks_another_instance_on_the_shared_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, first, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()

    raw = first.model_dump(mode="python")
    raw["instance"]["id"] = "other"
    raw["runtime"]["state_path"] = tmp_path / "other" / "state.json"
    second = AppConfig.model_validate(raw)
    _install_fakes(monkeypatch, second, devices)

    assert hardware_test.canonical_intent_path(first) == hardware_test.canonical_intent_path(second)
    assert hardware_test.main(_args("preflight")) == 2
    assert "another instance owns" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
