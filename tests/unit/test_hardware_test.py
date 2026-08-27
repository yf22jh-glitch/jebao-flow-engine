import asyncio
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import hardware_guard, hardware_safety, hardware_test
from jebao_flow.config import AppConfig
from jebao_flow.devices import (
    ControlAcknowledgementError,
    LinkageForwardFailureCategory,
    LinkageRecoveryReason,
    LinkageRollbackError,
    LinkageRollbackFailure,
    LinkageRollbackFailureCategory,
    LinkageRollbackParticipant,
    LinkageRollbackStage,
    LinkageSafetyInterlock,
    LinkageStopReason,
    LinkageTestSpec,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    PowerStateVerificationError,
    SimulatedJebaoDevice,
    TemporaryLinkageController,
    schedule_structure_fingerprint,
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
    ScheduleEntry,
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
    *,
    seed_qualifications: bool = True,
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
    if not seed_qualifications:
        return
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


def _seed_timer_on_recovery(
    config: AppConfig,
    devices: dict[str, SimulatedJebaoDevice],
    capsys: pytest.CaptureFixture[str],
) -> tuple[
    JsonLinkageJournalStore,
    hardware_test.JsonHardwareTestIntentStore,
    LinkageTransactionRecord,
    str,
]:
    """Create one attended TimerON recovery record without emitting a control frame."""

    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    intent = intent_store.load()
    assert intent is not None
    schedule = DeviceSchedule(enabled=True)
    fingerprint = schedule_structure_fingerprint(schedule)
    snapshots = tuple(
        snapshot.model_copy(
            update={
                "timer_enabled": True,
                "schedule_fingerprint": fingerprint,
            }
        )
        for snapshot in intent.snapshots
    )
    intent_store.save(
        intent.model_copy(
            update={
                "phase": hardware_test.HardwareTestIntentPhase.STARTED,
                "snapshots": snapshots,
            }
        )
    )
    now = datetime.now(UTC)
    record = LinkageTransactionRecord(
        operation_id=intent.operation_id,
        phase=LinkageTransactionPhase.ACTIVE,
        spec=intent.spec,
        snapshots=snapshots,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=10),
    )
    journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    journal_store.create(record)
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert all(device.commands == [] for device in devices.values())
    return journal_store, intent_store, record, recovery_token


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
    assert intent.version == 2
    assert intent.evidence == hardware_test.HardwareTestEvidence()
    assert intent.phase is hardware_test.HardwareTestIntentPhase.ARMED
    assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
    raw_intent = intent_path.read_text(encoding="utf-8")
    assert _VENDOR_MASTER_ID not in raw_intent
    assert _VENDOR_SLAVE_ID not in raw_intent
    assert _MASTER_MAC not in raw_intent
    assert _SLAVE_MAC not in raw_intent
    assert hardware_test.canonical_journal_path(config).parent == (
        tmp_path / "shared-hardware-safety"
    )


@pytest.mark.parametrize("progress_kind", ("evidence", "primary_failure", "outcome"))
def test_forged_armed_progress_is_rejected_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    progress_kind: str,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    token = _token(capsys.readouterr().out)
    intent_path = hardware_test.canonical_intent_path(config)
    payload = json.loads(intent_path.read_text(encoding="utf-8"))
    if progress_kind == "evidence":
        payload["evidence"]["active_entered_at"] = datetime.now(UTC).isoformat()
    elif progress_kind == "primary_failure":
        payload["primary_failure"] = "slave_power_change_not_verified"
    else:
        payload["outcome"] = "unexpected_armed_outcome"
    intent_path.write_text(json.dumps(payload), encoding="utf-8")
    intent_path.chmod(0o600)

    assert hardware_test.main(_args("run-native-linkage", confirmation=token)) == 2

    assert "hardware-test intent is unreadable" in capsys.readouterr().err
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    assert hardware_test.canonical_journal_path(config).exists() is False


def test_schedule_bootstrap_skips_prior_receipts_steps_async_slave_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 89), "pro_right": _device("pro_right", 30)}
    left = devices["pro_left"]
    left._capabilities = left.capabilities.model_copy(  # noqa: SLF001
        update={
            "native_modes": left.capabilities.native_modes | {"random"},
            "power_limits": PowerLimits(min_power=30, max_power=100),
        }
    )
    left._state = left._state.model_copy(  # noqa: SLF001
        update={
            "mode": "random",
            "frequency": 34,
            "timer_enabled": True,
            "schedule": DeviceSchedule(enabled=True),
        }
    )
    right = devices["pro_right"]
    right._state = right._state.model_copy(  # noqa: SLF001
        update={
            "frequency": 32,
            "timer_enabled": True,
            "schedule": DeviceSchedule(enabled=True),
        }
    )
    _install_fakes(
        monkeypatch,
        config,
        devices,
        seed_qualifications=False,
    )
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("sine")] = "constant"
    args[args.index("0.02")] = "0.08"
    args.extend(
        (
            "--bootstrap-active-schedule",
            "--slave-power-after",
            "38",
            "--power-change-after",
            "0.02",
        )
    )

    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)
    run_args = [*args]
    run_args[0] = "run-native-linkage"
    assert hardware_test.main([*run_args, "--confirm", token]) == 0

    assert (left._state.power, left._state.mode, left._state.timer_enabled) == (  # noqa: SLF001
        89,
        "random",
        True,
    )
    assert (right._state.power, right._state.mode, right._state.timer_enabled) == (  # noqa: SLF001
        30,
        "constant",
        True,
    )
    assert any(command.name == "power" and command.value == 38 for command in right.commands)
    receipt_store = JsonQualificationStore(hardware_test.canonical_qualification_directory(config))
    for device in devices.values():
        binding = device.physical_binding
        assert binding is not None
        receipt = receipt_store.load(binding)
        assert receipt is not None
        assert (receipt.original_power, receipt.step_power) == (31, 30)
    assert hardware_test.canonical_journal_path(config).exists() is False
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    evidence = intent.evidence
    assert evidence is not None
    assert evidence.active_entered_at is not None
    assert evidence.live_slave_write_attempted_at is not None
    assert evidence.live_slave_adapter_verified_at is not None
    assert evidence.live_slave_full_state_verified_at is not None
    assert evidence.verified_sample_count >= 1
    assert evidence.first_verified_sample is not None
    assert evidence.last_verified_sample is not None
    assert evidence.first_verified_sample.slave_power == 38
    assert evidence.last_verified_sample.slave_power == 38
    assert evidence.last_verified_sample.slave_linkage is LinkageRole.ASYNC_SLAVE
    assert evidence.forward_failure is None
    assert evidence.rollback_started_at is not None
    assert evidence.rollback_completed_at is not None
    assert evidence.rollback_completed_at >= evidence.rollback_started_at
    assert evidence.rollback_failures == ()


def test_schedule_bootstrap_early_stop_restores_but_issues_no_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 89), "pro_right": _device("pro_right", 30)}
    left = devices["pro_left"]
    left._capabilities = left.capabilities.model_copy(  # noqa: SLF001
        update={
            "native_modes": left.capabilities.native_modes | {"random"},
            "power_limits": PowerLimits(min_power=30, max_power=100),
        }
    )
    left._state = left._state.model_copy(  # noqa: SLF001
        update={
            "mode": "random",
            "frequency": 34,
            "timer_enabled": True,
            "schedule": DeviceSchedule(enabled=True),
        }
    )
    right = devices["pro_right"]
    right._state = right._state.model_copy(  # noqa: SLF001
        update={
            "frequency": 32,
            "timer_enabled": True,
            "schedule": DeviceSchedule(enabled=True),
        }
    )
    _install_fakes(monkeypatch, config, devices, seed_qualifications=False)
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("sine")] = "constant"
    args[args.index("0.02")] = "0.5"
    args.extend(
        (
            "--bootstrap-active-schedule",
            "--slave-power-after",
            "38",
            "--power-change-after",
            "0.4",
        )
    )

    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)
    original_run_with_sigint = hardware_test._run_with_sigint

    async def stop_as_soon_as_active(
        controller: TemporaryLinkageController,
        spec: LinkageTestSpec,
        **kwargs: object,
    ) -> object:
        interrupt = asyncio.Event()
        journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))

        async def trigger() -> None:
            while True:
                record = journal_store.load()
                if record is not None and record.phase is LinkageTransactionPhase.ACTIVE:
                    interrupt.set()
                    return
                await asyncio.sleep(0)

        trigger_task = asyncio.create_task(trigger())
        try:
            return await original_run_with_sigint(
                controller,
                spec,
                interrupt_event=interrupt,
                **kwargs,  # type: ignore[arg-type]
            )
        finally:
            await trigger_task

    monkeypatch.setattr(hardware_test, "_run_with_sigint", stop_as_soon_as_active)
    run_args = [*args]
    run_args[0] = "run-native-linkage"

    assert hardware_test.main([*run_args, "--confirm", token]) == 2

    assert "live power change was not verified" in capsys.readouterr().err
    assert (left._state.power, left._state.mode, left._state.timer_enabled) == (  # noqa: SLF001
        89,
        "random",
        True,
    )
    assert (right._state.power, right._state.mode, right._state.timer_enabled) == (  # noqa: SLF001
        30,
        "constant",
        True,
    )
    assert not any(command.name == "power" and command.value == 38 for command in right.commands)
    receipt_store = JsonQualificationStore(hardware_test.canonical_qualification_directory(config))
    for device in devices.values():
        binding = device.physical_binding
        assert binding is not None
        assert receipt_store.load(binding) is None
    assert hardware_test.canonical_journal_path(config).exists() is False
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert intent.outcome == "restored"
    assert (
        intent.primary_failure
        is hardware_test.HardwareTestPrimaryFailure.SLAVE_POWER_CHANGE_NOT_VERIFIED
    )


def test_live_slave_attempt_evidence_precedes_physical_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    # Leave enough wall-clock room for the fsynced evidence assertion on loaded CI hosts.
    args[args.index("0.02")] = "0.30"
    args.extend(("--slave-power-after", "38", "--power-change-after", "0.02"))
    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)

    slave = devices["pro_right"]
    original_write = slave.write_power
    observed_attempt = False

    async def assert_durable_attempt_before_write(
        power: int,
        **kwargs: object,
    ) -> None:
        nonlocal observed_attempt
        if power == 38:
            intent = hardware_test.JsonHardwareTestIntentStore(
                hardware_test.canonical_intent_path(config)
            ).load()
            assert intent is not None
            assert intent.evidence is not None
            assert intent.evidence.live_slave_write_attempted_at is not None
            assert intent.evidence.live_slave_adapter_verified_at is None
            observed_attempt = True
        await original_write(power, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(slave, "write_power", assert_durable_attempt_before_write)
    run_args = [*args]
    run_args[0] = "run-native-linkage"

    assert hardware_test.main([*run_args, "--confirm", token]) == 0
    assert observed_attempt is True


@pytest.mark.parametrize("replace_completed", [False, True])
def test_live_slave_attempt_persistence_failure_prevents_physical_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    replace_completed: bool,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("0.02")] = "0.08"
    args.extend(("--slave-power-after", "38", "--power-change-after", "0.02"))
    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)

    original_save = hardware_test.JsonHardwareTestIntentStore.save
    failed_once = False

    def fail_before_attempt_replace(
        store: hardware_test.JsonHardwareTestIntentStore,
        intent: hardware_test.HardwareTestIntent,
    ) -> None:
        nonlocal failed_once
        evidence = intent.evidence
        if (
            not failed_once
            and evidence is not None
            and evidence.live_slave_write_attempted_at is not None
        ):
            failed_once = True
            if replace_completed:
                original_save(store, intent)
            raise OSError("secret persistence failure must not be exposed")
        original_save(store, intent)

    monkeypatch.setattr(
        hardware_test.JsonHardwareTestIntentStore,
        "save",
        fail_before_attempt_replace,
    )
    run_args = [*args]
    run_args[0] = "run-native-linkage"

    assert hardware_test.main([*run_args, "--confirm", token]) == 2
    assert failed_once is True
    assert not any(
        command.name == "power" and command.value == 38
        for command in devices["pro_right"].commands
    )
    assert "secret persistence failure" not in capsys.readouterr().err
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.evidence is not None
    assert (intent.evidence.live_slave_write_attempted_at is not None) is replace_completed
    assert intent.evidence.live_slave_adapter_verified_at is None
    assert intent.evidence.live_slave_full_state_verified_at is None
    assert intent.evidence.forward_failure is not None
    assert intent.evidence.rollback_completed_at is not None


def test_live_slave_failure_survives_masking_rollback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("0.02")] = "0.08"
    args.extend(
        (
            "--slave-power-after",
            "38",
            "--power-change-after",
            "0.02",
        )
    )
    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)

    slave = devices["pro_right"]
    original_slave_write = slave.write_power

    async def discard_live_slave_power(power: int, **kwargs: object) -> None:
        previous_power = slave._state.power  # noqa: SLF001
        await original_slave_write(power, **kwargs)  # type: ignore[arg-type]
        if power == 38:
            slave._state = slave._state.model_copy(  # noqa: SLF001
                update={"power": previous_power}
            )

    async def fail_rollback(
        self: TemporaryLinkageController,
        record: LinkageTransactionRecord,
    ) -> None:
        pending = self._store.load() or record  # noqa: SLF001
        rollback_failures = self._structured_rollback_failures(  # noqa: SLF001
            pending,
            {
                "pro_left": ["timer_restore_failed"],
                "pro_right": ["detach_failed"],
            },
        )
        self._store.save(  # noqa: SLF001
            pending.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": LinkageRecoveryReason.RESTORE_FAILED,
                    "updated_at": datetime.now(UTC),
                    "error": "restore_failed",
                    "failed_device_ids": ("pro_left", "pro_right"),
                    "restored_device_ids": (),
                    "rollback_failures": rollback_failures,
                }
            )
        )
        raise LinkageRollbackError("secret-device-id: restore failed")

    monkeypatch.setattr(slave, "write_power", discard_live_slave_power)
    monkeypatch.setattr(
        TemporaryLinkageController,
        "_rollback_uninterruptibly",
        fail_rollback,
    )
    run_args = [*args]
    run_args[0] = "run-native-linkage"

    assert hardware_test.main([*run_args, "--confirm", token]) == 2

    error_output = capsys.readouterr().err
    assert "LinkageRollbackError" in error_output
    assert "secret-device-id" not in error_output
    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.RECOVERY_REQUIRED
    assert intent.outcome == "recovery_required"
    assert (
        intent.primary_failure
        is hardware_test.HardwareTestPrimaryFailure.SLAVE_POWER_CHANGE_NOT_VERIFIED
    )
    evidence = intent.evidence
    assert evidence is not None
    assert evidence.live_slave_write_attempted_at is not None
    assert evidence.live_slave_adapter_verified_at is not None
    assert evidence.live_slave_full_state_verified_at is None
    assert evidence.forward_failure is not None
    assert evidence.rollback_started_at is not None
    assert evidence.rollback_completed_at is None
    assert {
        (failure.participant.value, failure.stage.value, failure.category.value)
        for failure in evidence.rollback_failures
    } == {
        ("master", "timer_restore", "timer_restore_failed"),
        ("slave", "detach", "detach_failed"),
    }

    assert hardware_test.main(["status"]) == 0
    status_output = capsys.readouterr().out
    assert "Primary failure: slave_power_change_not_verified" in status_output
    assert "master/timer_restore/timer_restore_failed" in status_output
    assert "slave/detach/detach_failed" in status_output
    assert "secret-device-id" not in status_output


def test_driver_live_slave_power_readback_failure_is_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("0.02")] = "0.08"
    args.extend(
        (
            "--slave-power-after",
            "38",
            "--power-change-after",
            "0.02",
        )
    )
    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)

    slave = devices["pro_right"]
    original_slave_write = slave.write_power

    async def fail_completed_live_power_readback(
        power: int,
        **kwargs: object,
    ) -> None:
        previous_power = slave._state.power  # noqa: SLF001
        await original_slave_write(power, **kwargs)  # type: ignore[arg-type]
        if power == 38:
            slave._state = slave._state.model_copy(  # noqa: SLF001
                update={"power": previous_power}
            )
            raise PowerStateVerificationError(
                "completed control had a power-only read-back mismatch"
            )

    monkeypatch.setattr(slave, "write_power", fail_completed_live_power_readback)
    run_args = [*args]
    run_args[0] = "run-native-linkage"

    assert hardware_test.main([*run_args, "--confirm", token]) == 2

    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert intent.outcome == "restored"
    assert (
        intent.primary_failure
        is hardware_test.HardwareTestPrimaryFailure.SLAVE_POWER_CHANGE_NOT_VERIFIED
    )
    assert intent.evidence is not None
    assert (
        intent.evidence.forward_failure
        is LinkageForwardFailureCategory.POWER_STATE_NOT_VERIFIED
    )
    assert intent.evidence.rollback_completed_at is not None


def test_verified_live_change_and_rollback_failure_survive_attended_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    sensitive_values = (
        _VENDOR_MASTER_ID,
        _VENDOR_SLAVE_ID,
        _MASTER_MAC,
        _SLAVE_MAC,
        "198.51.100.77",
        "MQTT_PASSWORD=must-not-persist",
        "sensitive transport detail",
    )
    for device in devices.values():
        device._state = device._state.model_copy(  # noqa: SLF001
            update={
                "observed_attributes": {
                    "address": "198.51.100.77",
                    "credential": "MQTT_PASSWORD=must-not-persist",
                }
            }
        )
    _install_fakes(monkeypatch, config, devices)
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("0.02")] = "0.08"
    args.extend(("--slave-power-after", "38", "--power-change-after", "0.02"))
    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)

    original_rollback = TemporaryLinkageController._rollback_uninterruptibly

    async def fail_automatic_rollback(
        self: TemporaryLinkageController,
        record: LinkageTransactionRecord,
    ) -> None:
        pending = self._store.load() or record  # noqa: SLF001
        rollback_failures = self._structured_rollback_failures(  # noqa: SLF001
            pending,
            {"pro_right": ["session_refresh_failed"]},
        )
        self._store.save(  # noqa: SLF001
            pending.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": LinkageRecoveryReason.RESTORE_FAILED,
                    "updated_at": datetime.now(UTC),
                    "error": "pro_right: session_refresh_failed",
                    "failed_device_ids": ("pro_right",),
                    "restored_device_ids": (),
                    "rollback_failures": rollback_failures,
                }
            )
        )
        raise LinkageRollbackError("sensitive transport detail")

    monkeypatch.setattr(
        TemporaryLinkageController,
        "_rollback_uninterruptibly",
        fail_automatic_rollback,
    )
    run_args = [*args]
    run_args[0] = "run-native-linkage"
    assert hardware_test.main([*run_args, "--confirm", token]) == 2
    assert "sensitive transport detail" not in capsys.readouterr().err

    intent_store = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    )
    failed_intent = intent_store.load()
    assert failed_intent is not None
    failed_evidence = failed_intent.evidence
    assert failed_evidence is not None
    assert failed_intent.primary_failure is None
    assert failed_evidence.live_slave_adapter_verified_at is not None
    assert failed_evidence.live_slave_full_state_verified_at is not None
    assert failed_evidence.verified_sample_count >= 1
    assert failed_evidence.forward_failure is None
    assert failed_evidence.rollback_completed_at is None
    assert [
        (
            failure.participant.value,
            failure.stage.value,
            failure.category.value,
        )
        for failure in failed_evidence.rollback_failures
    ] == [("slave", "session_refresh", "session_refresh_failed")]
    intent_json = hardware_test.canonical_intent_path(config).read_text(encoding="utf-8")
    journal_json = hardware_test.canonical_journal_path(config).read_text(encoding="utf-8")
    assert hardware_test.main(["status"]) == 0
    status_output = capsys.readouterr().out
    for sensitive in sensitive_values:
        assert sensitive not in intent_json
        assert sensitive not in journal_json
        assert sensitive not in status_output

    monkeypatch.setattr(
        TemporaryLinkageController,
        "_rollback_uninterruptibly",
        original_rollback,
    )
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 0
    capsys.readouterr()

    recovered_intent = intent_store.load()
    assert recovered_intent is not None
    assert recovered_intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert recovered_intent.outcome == "recovered"
    recovered_evidence = recovered_intent.evidence
    assert recovered_evidence is not None
    assert recovered_evidence.live_slave_full_state_verified_at == (
        failed_evidence.live_slave_full_state_verified_at
    )
    assert recovered_evidence.verified_sample_count == failed_evidence.verified_sample_count
    assert recovered_evidence.rollback_failures == failed_evidence.rollback_failures
    assert recovered_evidence.rollback_completed_at is not None
    assert hardware_test.canonical_journal_path(config).exists() is False


@pytest.mark.parametrize(
    "failure_kind",
    (
        "master_readback_mismatch",
        "master_transport_error",
        "driver_power_error_then_converges",
        "driver_power_error_with_slave_error",
        "driver_power_error_with_schedule_change",
        "control_ack_unconfirmed",
        "slave_power_and_linkage_mismatch",
        "slave_write_error",
    ),
)
def test_unrelated_post_change_failure_does_not_set_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_kind: str,
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    bootstrap_schedule = failure_kind == "driver_power_error_with_schedule_change"
    if bootstrap_schedule:
        for device in devices.values():
            device._state = device._state.model_copy(  # noqa: SLF001
                update={
                    "timer_enabled": True,
                    "schedule": DeviceSchedule(enabled=True),
                }
            )
    _install_fakes(
        monkeypatch,
        config,
        devices,
        seed_qualifications=not bootstrap_schedule,
    )
    args = _args("preflight")
    args[args.index("sync_slave")] = "async_slave"
    args[args.index("0.02")] = "0.08"
    if bootstrap_schedule:
        args.append("--bootstrap-active-schedule")
    args.extend(
        (
            "--slave-power-after",
            "38",
            "--power-change-after",
            "0.02",
        )
    )
    assert hardware_test.main(args) == 0
    token = _token(capsys.readouterr().out)

    master = devices["pro_left"]
    slave = devices["pro_right"]
    original_slave_write = slave.write_power
    original_master_get_state = master.get_state
    original_slave_get_state = slave.get_state
    fail_next_master_read = False
    fail_next_slave_error_read = False
    fail_next_slave_schedule_read = False

    async def fail_after_live_slave_write(power: int, **kwargs: object) -> None:
        nonlocal fail_next_master_read, fail_next_slave_error_read
        nonlocal fail_next_slave_schedule_read
        if power == 38 and failure_kind == "slave_write_error":
            raise RuntimeError("live slave write transport failed")
        await original_slave_write(power, **kwargs)  # type: ignore[arg-type]
        if power != 38:
            return
        if failure_kind == "control_ack_unconfirmed":
            raise ControlAcknowledgementError("live slave control ACK was not confirmed")
        if failure_kind == "master_readback_mismatch":
            master._state = master._state.model_copy(update={"power": 34})  # noqa: SLF001
        elif failure_kind == "master_transport_error":
            fail_next_master_read = True
        elif failure_kind == "driver_power_error_then_converges":
            raise PowerStateVerificationError("transient power-only driver mismatch")
        elif failure_kind == "driver_power_error_with_slave_error":
            fail_next_slave_error_read = True
            raise PowerStateVerificationError("power-only driver read-back mismatch")
        elif failure_kind == "driver_power_error_with_schedule_change":
            fail_next_slave_schedule_read = True
            raise PowerStateVerificationError("power-only driver read-back mismatch")
        elif failure_kind == "slave_power_and_linkage_mismatch":
            slave._state = slave._state.model_copy(  # noqa: SLF001
                update={"power": 33, "linkage": LinkageRole.INDEPENDENT}
            )

    async def fail_one_master_read() -> DeviceState:
        nonlocal fail_next_master_read
        if fail_next_master_read:
            fail_next_master_read = False
            raise RuntimeError("master read transport failed")
        return await original_master_get_state()

    async def fail_one_slave_full_state_read() -> DeviceState:
        nonlocal fail_next_slave_error_read, fail_next_slave_schedule_read
        state = await original_slave_get_state()
        if fail_next_slave_error_read:
            fail_next_slave_error_read = False
            return state.model_copy(update={"power": 33, "error": "Fault_UART"})
        if fail_next_slave_schedule_read:
            fail_next_slave_schedule_read = False
            return state.model_copy(
                update={
                    "power": 33,
                    "schedule": DeviceSchedule(
                        enabled=False,
                        entries=(
                            ScheduleEntry(
                                slot=0,
                                start="08:00",
                                end="09:00",
                                mode="constant",
                                mode_code=0,
                                parameters={"flow": 33},
                            ),
                        ),
                    ),
                }
            )
        return state

    monkeypatch.setattr(slave, "write_power", fail_after_live_slave_write)
    monkeypatch.setattr(master, "get_state", fail_one_master_read)
    monkeypatch.setattr(slave, "get_state", fail_one_slave_full_state_read)
    run_args = [*args]
    run_args[0] = "run-native-linkage"

    assert hardware_test.main([*run_args, "--confirm", token]) == 2

    intent = hardware_test.JsonHardwareTestIntentStore(
        hardware_test.canonical_intent_path(config)
    ).load()
    assert intent is not None
    assert intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert intent.outcome == "restored"
    assert intent.primary_failure is None
    evidence = intent.evidence
    assert evidence is not None
    assert evidence.live_slave_write_attempted_at is not None
    assert (evidence.live_slave_adapter_verified_at is not None) is (
        failure_kind
        not in {
            "driver_power_error_then_converges",
            "driver_power_error_with_slave_error",
            "driver_power_error_with_schedule_change",
            "control_ack_unconfirmed",
            "slave_write_error",
        }
    )
    assert (evidence.live_slave_full_state_verified_at is not None) is (
        failure_kind == "driver_power_error_then_converges"
    )
    assert evidence.verified_sample_count == (
        1 if failure_kind == "driver_power_error_then_converges" else 0
    )
    assert evidence.forward_failure is not None
    assert evidence.rollback_completed_at is not None
    assert (
        master._state.power,  # noqa: SLF001
        master._state.mode,  # noqa: SLF001
        master._state.linkage,  # noqa: SLF001
        master._state.timer_enabled,  # noqa: SLF001
    ) == (34, "constant", LinkageRole.INDEPENDENT, bootstrap_schedule)
    assert (
        slave._state.power,  # noqa: SLF001
        slave._state.mode,  # noqa: SLF001
        slave._state.linkage,  # noqa: SLF001
        slave._state.timer_enabled,  # noqa: SLF001
    ) == (36, "constant", LinkageRole.INDEPENDENT, bootstrap_schedule)
    if failure_kind == "control_ack_unconfirmed":
        assert (
            evidence.forward_failure
            is LinkageForwardFailureCategory.CONTROL_ACK_NOT_CONFIRMED
        )
        assert sum(
            command.name == "power" and command.value == 38 for command in slave.commands
        ) == 1
    assert hardware_test.main(["status"]) == 0
    status_output = capsys.readouterr().out
    adapter_verified = failure_kind not in {
        "driver_power_error_then_converges",
        "driver_power_error_with_slave_error",
        "driver_power_error_with_schedule_change",
        "control_ack_unconfirmed",
        "slave_write_error",
    }
    full_state_verified = failure_kind == "driver_power_error_then_converges"
    assert "write_attempted=yes" in status_output
    assert f"adapter_verified={'yes' if adapter_verified else 'no'}" in status_output
    assert f"full_state_verified={'yes' if full_state_verified else 'no'}" in status_output


def test_native_intent_loads_version_one_without_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    assert hardware_test.main(_args("preflight")) == 0
    capsys.readouterr()
    path = hardware_test.canonical_intent_path(config)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = 1
    payload.pop("primary_failure")
    payload.pop("evidence")
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    intent = hardware_test.JsonHardwareTestIntentStore(path).load()

    assert intent is not None
    assert intent.version == 1
    assert intent.primary_failure is None
    assert intent.evidence is None


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


def test_malformed_terminal_schedule_intent_blocks_native_workflow() -> None:
    root = hardware_safety.hardware_safety_root()
    root.mkdir(mode=0o700)
    path = hardware_safety.schedule_linkage_intent_path()
    path.write_text(
        '{"version":1,"phase":"terminal","preflight":{},"outcome":"recovered"}\n',
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(hardware_test.HardwareTestError, match="schedule-linkage"):
        hardware_test._assert_no_verification_conflict()


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


def test_schedule_bootstrap_allows_five_minutes_but_caps_at_ten() -> None:
    values = _args("preflight")
    values[values.index("sync_slave")] = "async_slave"
    values[values.index("0.02")] = "300"
    values.extend(("--bootstrap-active-schedule",))
    args = hardware_test.build_parser().parse_args(values)

    assert hardware_test._spec_from_args(args).duration_seconds == 300

    too_long = ["600.01" if value == "300" else value for value in values]
    too_long_args = hardware_test.build_parser().parse_args(too_long)
    with pytest.raises(hardware_test.HardwareTestError, match="600 seconds"):
        hardware_test._spec_from_args(too_long_args)


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


@pytest.mark.parametrize(
    ("version", "progress_kind"),
    ((2, "evidence"), (2, "primary_failure"), (1, "primary_failure"), (1, "outcome")),
)
def test_started_intent_with_progress_and_no_journal_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: int,
    progress_kind: str,
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
    payload = intent.model_dump(mode="python")
    payload.update(
        {
            "version": version,
            "phase": hardware_test.HardwareTestIntentPhase.STARTED,
            "evidence": (
                hardware_test.HardwareTestEvidence(
                    active_entered_at=datetime.now(UTC)
                )
                if version == 2 and progress_kind == "evidence"
                else hardware_test.HardwareTestEvidence()
                if version == 2
                else None
            ),
            "primary_failure": (
                hardware_test.HardwareTestPrimaryFailure.SLAVE_POWER_CHANGE_NOT_VERIFIED
                if progress_kind == "primary_failure"
                else None
            ),
            "outcome": "unexpected_started_outcome" if progress_kind == "outcome" else None,
        }
    )
    progressed = hardware_test.HardwareTestIntent.model_validate(payload)
    intent_store.save(progressed)

    assert hardware_test.main(["recover-linkage"]) == 2

    error_output = capsys.readouterr().err
    assert "recovery journal is missing" in error_output
    assert "refusing to declare a no-write crash" in error_output
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []
    locked = intent_store.load()
    assert locked is not None
    assert locked.phase is hardware_test.HardwareTestIntentPhase.RECOVERY_REQUIRED
    assert locked.outcome == "recovery_authority_missing"
    assert locked.evidence == progressed.evidence


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


def test_confirming_recovery_store_accepts_only_its_own_saved_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    delegate = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    delegate.create(record)
    confirming = hardware_test.ConfirmingLinkageJournalStore(
        delegate,
        instance_id=config.instance.id,
        expected_token=intent.confirmation_token,
        expected_loaded_record=record,
        require_loaded_record_match=True,
    )

    own_successor = record.model_copy(
        update={
            "phase": LinkageTransactionPhase.ROLLING_BACK,
            "updated_at": now + timedelta(microseconds=1),
        }
    )
    confirming.save(own_successor)
    assert confirming.load() == own_successor

    external_successor = own_successor.model_copy(
        update={"updated_at": now + timedelta(microseconds=2)}
    )
    delegate.save(external_successor)
    with pytest.raises(hardware_test.ConfirmationMismatchError, match="journal changed"):
        confirming.load()


def test_attended_recovery_retries_after_its_own_journal_successor(
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

    async def leave_only_slave_active() -> None:
        slave = devices["pro_right"]
        await slave.connect()
        await slave.write_target(
            hardware_test.DeviceTarget(
                enabled=True,
                power=33,
                mode="sine",
                frequency=20,
                linkage=LinkageRole.SYNC_SLAVE,
                timer_enabled=False,
            )
        )
        await slave.disconnect()

    asyncio.run(leave_only_slave_active())
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")

    slave = devices["pro_right"]
    original_write_target = slave.write_target
    failed_once = False

    async def fail_first_control_restore(target: object, **kwargs: object) -> None:
        nonlocal failed_once
        if (
            not failed_once
            and isinstance(target, hardware_test.DeviceTarget)
            and target.power == 36
            and target.timer_enabled is False
        ):
            failed_once = True
            raise OSError("simulated transient restore failure")
        await original_write_target(target, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(slave, "write_target", fail_first_control_restore)
    original_recover_once = hardware_test._recover_once
    attempts = 0

    async def count_recover_attempts(*args: object, **kwargs: object) -> bool:
        nonlocal attempts
        attempts += 1
        return await original_recover_once(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hardware_test, "_recover_once", count_recover_attempts)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 0

    assert failed_once is True
    assert attempts == 2
    assert devices["pro_left"]._state.power == 34  # noqa: SLF001
    assert devices["pro_right"]._state.power == 36  # noqa: SLF001
    assert journal_store.load() is None
    recovered_intent = intent_store.load()
    assert recovered_intent is not None
    assert recovered_intent.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert recovered_intent.outcome == "recovered"


def test_absent_intent_recovery_preserves_first_attempt_failure_after_success(
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
    hardware_test.canonical_intent_path(config).unlink()

    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    original_recover_once = hardware_test._recover_once
    expected_failure = LinkageRollbackFailure(
        participant=LinkageRollbackParticipant.SLAVE,
        stage=LinkageRollbackStage.CONTROL_RESTORE,
        category=LinkageRollbackFailureCategory.CONTROL_RESTORE_FAILED,
    )
    attempts = 0

    async def fail_with_evidence_then_recover(*args: object, **kwargs: object) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            recovery_store = args[2]
            assert isinstance(
                recovery_store,
                hardware_test.ConfirmingLinkageJournalStore,
            )
            pending = recovery_store.load()
            assert pending is not None
            recovery_store.save(
                pending.model_copy(
                    update={
                        "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                        "recovery_reason": LinkageRecoveryReason.RESTORE_FAILED,
                        "updated_at": datetime.now(UTC),
                        "error": "control_restore_failed",
                        "failed_device_ids": ("pro_right",),
                        "rollback_failures": (expected_failure,),
                    }
                )
            )
            return False
        return await original_recover_once(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hardware_test, "_recover_once", fail_with_evidence_then_recover)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 0

    assert attempts == 2
    assert journal_store.load() is None
    terminal = intent_store.load()
    assert terminal is not None
    assert terminal.version == 2
    assert terminal.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert terminal.outcome == "recovered"
    assert terminal.evidence is not None
    assert terminal.evidence.rollback_completed_at is not None
    assert terminal.evidence.rollback_failures == (expected_failure,)
    assert terminal.evidence.rollback_recovery_reasons == (
        LinkageRecoveryReason.RESTORE_FAILED,
    )


def test_absent_intent_prepared_recovery_records_no_physical_rollback(
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
    now = datetime.now(UTC)
    journal_store = JsonLinkageJournalStore(hardware_test.canonical_journal_path(config))
    journal_store.create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.PREPARED,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=10),
        )
    )
    hardware_test.canonical_intent_path(config).unlink()

    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 0

    assert all(device.commands == [] for device in devices.values())
    assert journal_store.load() is None
    terminal = intent_store.load()
    assert terminal is not None
    assert terminal.version == 2
    assert terminal.phase is hardware_test.HardwareTestIntentPhase.TERMINAL
    assert terminal.evidence is not None
    assert terminal.evidence.rollback_started_at is None
    assert terminal.evidence.rollback_completed_at is None


def test_schedule_change_stops_same_confirmation_retry_and_recovery_first(
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
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")

    attempts = 0

    async def observe_schedule_change(*args: object, **kwargs: object) -> bool:
        del kwargs
        nonlocal attempts
        attempts += 1
        recovery_store = args[2]
        assert isinstance(recovery_store, hardware_test.ConfirmingLinkageJournalStore)
        current = recovery_store.load()
        assert current is not None
        recovery_store.save(
            current.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": LinkageRecoveryReason.SCHEDULE_CHANGED,
                    "updated_at": datetime.now(UTC),
                    "error": "pro_right: control_restore_failed",
                    "failed_device_ids": ("pro_right",),
                }
            )
        )
        raise LinkageRollbackError("simulated transient schedule drift")

    monkeypatch.setattr(hardware_test, "_recover_once", observe_schedule_change)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    assert "schedule changed during recovery" in capsys.readouterr().err
    assert attempts == 1
    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED
    assert intent_store.load().outcome == "recovery_required"

    assert hardware_test.main(["recover-linkage", "--recovery-first"]) == 2
    assert (
        "schedule-changed recovery requires a new attended confirmation"
        in capsys.readouterr().err
    )
    assert attempts == 1
    assert journal_store.load() == pending


def test_safety_interlock_stops_same_confirmation_retry_and_requires_new_token(
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
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")

    attempts = 0

    async def observe_safety_interlock(*args: object, **kwargs: object) -> bool:
        del kwargs
        nonlocal attempts
        attempts += 1
        recovery_store = args[2]
        assert isinstance(recovery_store, hardware_test.ConfirmingLinkageJournalStore)
        current = recovery_store.load()
        assert current is not None
        recovery_store.save(
            current.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": LinkageRecoveryReason.SAFETY_INTERLOCK,
                    "updated_at": datetime.now(UTC),
                    "error": "secret-device-id: safety_stop_required",
                    "failed_device_ids": ("pro_right",),
                }
            )
        )
        raise LinkageRollbackError("secret-device-id: simulated safety transition")

    monkeypatch.setattr(hardware_test, "_recover_once", observe_safety_interlock)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    refusal = capsys.readouterr()
    assert "safety interlock changed during recovery" in refusal.err
    assert "request a new status and attended recovery token" in refusal.err
    assert "secret-device-id" not in refusal.out + refusal.err
    assert "pro_right" not in refusal.out + refusal.err
    assert attempts == 1
    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.error == "recovery deferred by persistent safety interlock"
    assert pending.failed_device_ids == ("pro_left", "pro_right")
    assert pending.restored_device_ids == ()
    assert intent_store.load().outcome == "recovery_required"
    assert all(device._state.enabled is False for device in devices.values())  # noqa: SLF001

    assert hardware_test.main(["status"]) == 0
    status_output = capsys.readouterr().out
    current_token = _token(status_output, "Recovery confirmation token")
    assert current_token != recovery_token
    assert "Automatic recovery blockers: safety_interlock" in status_output
    assert attempts == 1


def test_persistent_latch_during_retry_dwell_is_durably_latched_without_retry(
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
    assert hardware_test.main(["recover-linkage"]) == 0
    recovery_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    latch_path = hardware_test.canonical_safety_latch_path(config)
    attempts = 0

    async def fail_before_retry(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        nonlocal attempts
        attempts += 1
        asyncio.get_running_loop().call_later(
            0.005,
            hardware_test.activate_persistent_safety_latch,
            latch_path,
        )
        raise LinkageRollbackError("secret-device-id: transient restore failure")

    monkeypatch.setattr(hardware_test, "_recover_once", fail_before_retry)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(hardware_test, "_RECOVERY_LATCH_POLL_SECONDS", 0.001)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    refusal = capsys.readouterr()
    assert "safety interlock changed during recovery" in refusal.err
    assert "secret-device-id" not in refusal.out + refusal.err
    assert attempts == 1
    assert latch_path.exists()
    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.error == "recovery deferred by persistent safety interlock"
    assert pending.failed_device_ids == ("pro_left", "pro_right")
    assert pending.restored_device_ids == ()

    assert hardware_test.main(["status"]) == 0
    status_output = capsys.readouterr().out
    assert _token(status_output, "Recovery confirmation token") != recovery_token
    assert "Automatic recovery blockers: safety_interlock" in status_output
    assert "secret-device-id" not in status_output


def test_latch_after_successful_recovery_clear_stops_both_and_invalidates_old_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    journal_store, intent_store, _, recovery_token = _seed_timer_on_recovery(
        config,
        devices,
        capsys,
    )
    latch_path = hardware_test.canonical_safety_latch_path(config)
    attempts = 0

    async def succeed_then_latch(*args: object, **kwargs: object) -> bool:
        del kwargs
        nonlocal attempts
        attempts += 1
        recovery_store = args[2]
        assert isinstance(recovery_store, hardware_test.ConfirmingLinkageJournalStore)
        current = recovery_store.load()
        assert current is not None
        snapshots = {snapshot.device_id: snapshot for snapshot in current.snapshots}
        for device_id in (current.spec.master_device_id, current.spec.slave_device_id):
            snapshot = snapshots[device_id]
            device = devices[device_id]
            await device.connect()
            await device.write_target(
                hardware_test.DeviceTarget(
                    enabled=snapshot.enabled,
                    power=snapshot.power,
                    mode=snapshot.mode,
                    frequency=snapshot.frequency,
                    linkage=snapshot.linkage,
                    timer_enabled=snapshot.timer_enabled,
                )
            )
            await device.disconnect()
        recovery_store.clear()
        hardware_test.activate_persistent_safety_latch(latch_path)
        return True

    monkeypatch.setattr(hardware_test, "_recover_once", succeed_then_latch)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    refusal = capsys.readouterr()
    assert "safety interlock changed during recovery" in refusal.err
    assert attempts == 1
    for device in devices.values():
        assert device.connected is False
        assert device._state.enabled is False  # noqa: SLF001
        assert device._state.timer_enabled is False  # noqa: SLF001
        assert device._state.linkage is LinkageRole.INDEPENDENT  # noqa: SLF001
        timer_on_commands = [
            command
            for command in device.commands
            if command.name == "timer_enabled" and command.value is True
        ]
        assert len(timer_on_commands) == 1

    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.failed_device_ids == ("pro_left", "pro_right")
    assert pending.restored_device_ids == ()
    recovered_intent = intent_store.load()
    assert recovered_intent is not None
    assert recovered_intent.phase is hardware_test.HardwareTestIntentPhase.RECOVERY_REQUIRED
    assert recovered_intent.outcome == "recovery_required"

    assert hardware_test.main(["status"]) == 0
    current_token = _token(capsys.readouterr().out, "Recovery confirmation token")
    assert current_token != recovery_token
    latch_path.unlink()
    command_counts = {device_id: len(device.commands) for device_id, device in devices.items()}
    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    stale_refusal = capsys.readouterr()
    assert "confirmation token does not match" in stale_refusal.err
    assert all(
        len(device.commands) == command_counts[device_id]
        for device_id, device in devices.items()
    )


def test_outer_safety_late_fsync_error_reloads_exact_record_and_still_stops_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    journal_store, _, _, recovery_token = _seed_timer_on_recovery(config, devices, capsys)
    latch_path = hardware_test.canonical_safety_latch_path(config)
    attempts = 0
    late_failure_injected = False
    original_save = JsonLinkageJournalStore.save

    def save_then_report_late_fsync_failure(
        store: JsonLinkageJournalStore,
        record: LinkageTransactionRecord,
    ) -> None:
        nonlocal late_failure_injected
        original_save(store, record)
        if (
            not late_failure_injected
            and record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
            and record.error == "recovery deferred by persistent safety interlock"
        ):
            late_failure_injected = True
            raise OSError("secret-device-id: simulated late directory fsync failure")

    async def succeed_then_latch(*args: object, **kwargs: object) -> bool:
        del kwargs
        nonlocal attempts
        attempts += 1
        recovery_store = args[2]
        assert isinstance(recovery_store, hardware_test.ConfirmingLinkageJournalStore)
        current = recovery_store.load()
        assert current is not None
        for snapshot in current.snapshots:
            device = devices[snapshot.device_id]
            await device.connect()
            await device.write_target(
                hardware_test.DeviceTarget(
                    enabled=snapshot.enabled,
                    power=snapshot.power,
                    mode=snapshot.mode,
                    frequency=snapshot.frequency,
                    linkage=snapshot.linkage,
                    timer_enabled=snapshot.timer_enabled,
                )
            )
            await device.disconnect()
        recovery_store.clear()
        hardware_test.activate_persistent_safety_latch(latch_path)
        return True

    monkeypatch.setattr(JsonLinkageJournalStore, "save", save_then_report_late_fsync_failure)
    monkeypatch.setattr(hardware_test, "_recover_once", succeed_then_latch)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    refusal = capsys.readouterr()
    assert "safety interlock changed during recovery" in refusal.err
    assert "secret-device-id" not in refusal.out + refusal.err
    assert late_failure_injected is True
    assert attempts == 1
    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.failed_device_ids == ("pro_left", "pro_right")
    assert pending.restored_device_ids == ()
    assert pending.error == "recovery deferred by persistent safety interlock"
    for device in devices.values():
        assert device.connected is False
        assert device._state.enabled is False  # noqa: SLF001
        assert device._state.timer_enabled is False  # noqa: SLF001
        timer_on_commands = [
            command
            for command in device.commands
            if command.name == "timer_enabled" and command.value is True
        ]
        assert len(timer_on_commands) == 1


def test_outer_safety_save_failure_without_exact_successor_sends_zero_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    journal_store, _, record, _ = _seed_timer_on_recovery(config, devices, capsys)
    original_save = JsonLinkageJournalStore.save

    def fail_before_atomic_replace(
        store: JsonLinkageJournalStore,
        successor: LinkageTransactionRecord,
    ) -> None:
        if successor.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
            raise OSError("simulated pre-replace safety journal failure")
        original_save(store, successor)

    monkeypatch.setattr(JsonLinkageJournalStore, "save", fail_before_atomic_replace)
    selected = {device.id: device for device in config.devices if device.id in devices}
    interlock = hardware_test.PersistentSafetyInterlock(
        hardware_test.canonical_safety_latch_path(config)
    )

    with pytest.raises(OSError, match="pre-replace safety journal failure"):
        asyncio.run(
            hardware_test._enforce_outer_recovery_safety_stop(
                config,
                selected,
                journal_store,
                interlock,
                record,
            )
        )

    assert journal_store.load() == record
    assert all(device.commands == [] for device in devices.values())


def test_latch_during_retry_dwell_pauses_restored_master_after_durable_safety_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    journal_store, _, _, recovery_token = _seed_timer_on_recovery(config, devices, capsys)
    latch_path = hardware_test.canonical_safety_latch_path(config)
    events: list[str] = []
    attempts = 0

    original_persist = hardware_test._persist_recovery_safety_interlock

    def record_safety_persist(
        store: JsonLinkageJournalStore,
        record: LinkageTransactionRecord,
    ) -> LinkageTransactionRecord:
        successor = original_persist(store, record)
        events.append("journal:safety")
        return successor

    master = devices["pro_left"]
    original_master_write = master.write_target

    async def record_master_off(target: object, **kwargs: object) -> None:
        if isinstance(target, hardware_test.DeviceTarget) and target.enabled is False:
            events.append("master:off")
        await original_master_write(target, **kwargs)  # type: ignore[arg-type]

    async def leave_partial_restore(*args: object, **kwargs: object) -> bool:
        del kwargs
        nonlocal attempts
        attempts += 1
        recovery_store = args[2]
        assert isinstance(recovery_store, hardware_test.ConfirmingLinkageJournalStore)
        current = recovery_store.load()
        assert current is not None
        snapshots = {snapshot.device_id: snapshot for snapshot in current.snapshots}
        master_snapshot = snapshots[current.spec.master_device_id]
        await master.connect()
        await master.write_target(
            hardware_test.DeviceTarget(
                enabled=True,
                power=master_snapshot.power,
                mode=master_snapshot.mode,
                frequency=master_snapshot.frequency,
                linkage=master_snapshot.linkage,
                timer_enabled=True,
            )
        )
        await master.disconnect()
        slave = devices["pro_right"]
        await slave.connect()
        await slave.write_target(
            hardware_test.DeviceTarget(
                enabled=True,
                power=slave.capabilities.power_limits.min_power,
                mode="constant",
                frequency=current.spec.frequency,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            )
        )
        await slave.disconnect()
        recovery_store.save(
            current.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": LinkageRecoveryReason.RESTORE_FAILED,
                    "updated_at": datetime.now(UTC),
                    "error": "transient bounded restore failure",
                    "failed_device_ids": (current.spec.slave_device_id,),
                    "restored_device_ids": (current.spec.master_device_id,),
                }
            )
        )
        events.clear()
        asyncio.get_running_loop().call_later(
            0.005,
            hardware_test.activate_persistent_safety_latch,
            latch_path,
        )
        raise LinkageRollbackError("secret-device-id: simulated partial restore")

    monkeypatch.setattr(hardware_test, "_persist_recovery_safety_interlock", record_safety_persist)
    monkeypatch.setattr(master, "write_target", record_master_off)
    monkeypatch.setattr(hardware_test, "_recover_once", leave_partial_restore)
    monkeypatch.setattr(hardware_test, "_RECOVERY_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(hardware_test, "_RECOVERY_LATCH_POLL_SECONDS", 0.001)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    refusal = capsys.readouterr()
    assert "safety interlock changed during recovery" in refusal.err
    assert "secret-device-id" not in refusal.out + refusal.err
    assert attempts == 1
    assert events.index("journal:safety") < events.index("master:off")
    assert devices["pro_left"]._state.enabled is False  # noqa: SLF001
    assert devices["pro_right"]._state.enabled is False  # noqa: SLF001
    timer_on_commands = [
        command
        for command in devices["pro_left"].commands
        if command.name == "timer_enabled" and command.value is True
    ]
    assert len(timer_on_commands) == 1
    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.failed_device_ids == ("pro_left", "pro_right")
    assert pending.restored_device_ids == ()


def test_outer_latch_off_failure_is_durable_and_never_replays_timer_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    devices = {"pro_left": _device("pro_left", 34), "pro_right": _device("pro_right", 36)}
    _install_fakes(monkeypatch, config, devices)
    journal_store, _, _, recovery_token = _seed_timer_on_recovery(config, devices, capsys)
    latch_path = hardware_test.canonical_safety_latch_path(config)
    master = devices["pro_left"]
    original_master_write = master.write_target
    attempts = 0

    async def fail_master_off(target: object, **kwargs: object) -> None:
        if isinstance(target, hardware_test.DeviceTarget) and target.enabled is False:
            raise OSError("secret-device-id: simulated OFF failure")
        await original_master_write(target, **kwargs)  # type: ignore[arg-type]

    async def succeed_then_latch(*args: object, **kwargs: object) -> bool:
        del kwargs
        nonlocal attempts
        attempts += 1
        recovery_store = args[2]
        assert isinstance(recovery_store, hardware_test.ConfirmingLinkageJournalStore)
        current = recovery_store.load()
        assert current is not None
        for snapshot in current.snapshots:
            device = devices[snapshot.device_id]
            await device.connect()
            await device.write_target(
                hardware_test.DeviceTarget(
                    enabled=snapshot.enabled,
                    power=snapshot.power,
                    mode=snapshot.mode,
                    frequency=snapshot.frequency,
                    linkage=snapshot.linkage,
                    timer_enabled=snapshot.timer_enabled,
                )
            )
            await device.disconnect()
        recovery_store.clear()
        hardware_test.activate_persistent_safety_latch(latch_path)
        return True

    monkeypatch.setattr(master, "write_target", fail_master_off)
    monkeypatch.setattr(hardware_test, "_recover_once", succeed_then_latch)

    assert hardware_test.main(["recover-linkage", "--confirm", recovery_token]) == 2
    refusal = capsys.readouterr()
    assert "safety interlock changed during recovery" in refusal.err
    assert "secret-device-id" not in refusal.out + refusal.err
    assert attempts == 1
    pending = journal_store.load()
    assert pending is not None
    assert pending.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
    assert pending.failed_device_ids == ("pro_left", "pro_right")
    assert pending.restored_device_ids == ()
    assert pending.error is not None
    assert "safe_stop_failed" in pending.error
    assert "secret-device-id" not in pending.error
    for device in devices.values():
        timer_on_commands = [
            command
            for command in device.commands
            if command.name == "timer_enabled" and command.value is True
        ]
        assert len(timer_on_commands) == 1
    assert devices["pro_right"]._state.enabled is False  # noqa: SLF001


@pytest.mark.parametrize(
    "recovery_reason",
    [
        LinkageRecoveryReason.SAFETY_INTERLOCK,
        LinkageRecoveryReason.SCHEDULE_CHANGED,
        LinkageRecoveryReason.RESTORE_FAILED,
    ],
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
    initial_output = capsys.readouterr().out
    old_token = _token(initial_output, "Recovery confirmation token")
    assert "Recovery reason: none" in initial_output
    assert "Automatic recovery blockers: none" in initial_output
    assert "Next action: recover-linkage --recovery-first" in initial_output
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
    revised_output = capsys.readouterr().out
    current_token = _token(revised_output, "Recovery confirmation token")
    assert f"Recovery reason: {recovery_reason.value}" in revised_output
    expected_blocker = {
        LinkageRecoveryReason.SAFETY_INTERLOCK: "safety_interlock",
        LinkageRecoveryReason.SCHEDULE_CHANGED: "schedule_changed",
        LinkageRecoveryReason.RESTORE_FAILED: "none",
    }[recovery_reason]
    assert f"Automatic recovery blockers: {expected_blocker}" in revised_output
    if recovery_reason is LinkageRecoveryReason.RESTORE_FAILED:
        assert "Next action: recover-linkage --recovery-first" in revised_output
    elif recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
        assert "use attended recover-linkage confirmation" in revised_output
        assert "recover-linkage --recovery-first" not in revised_output
    else:
        assert "inspect the schedule" in revised_output
        assert "recover-linkage --recovery-first" not in revised_output
    assert f"simulated {recovery_reason.value}" not in revised_output
    assert "pro_left" not in revised_output
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
            error="secret-device-id: mismatched recovery authority",
        )
    )

    assert hardware_test.main(["status"]) == 2
    status_refusal = capsys.readouterr()
    assert status_refusal.out == ""
    assert "intent and recovery journal disagree" in status_refusal.err
    assert "Next action:" not in status_refusal.out + status_refusal.err
    assert "Recovery confirmation token:" not in status_refusal.out + status_refusal.err
    assert "secret-device-id" not in status_refusal.out + status_refusal.err
    assert "pro_left" not in status_refusal.out + status_refusal.err
    assert _VENDOR_MASTER_ID not in status_refusal.out + status_refusal.err
    assert _MASTER_MAC not in status_refusal.out + status_refusal.err

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
    assert "Recovery reason: none" in output
    assert "Automatic recovery blockers: none" in output
    assert "Persistent safety latch: clear" in output
    assert _VENDOR_MASTER_ID not in output
    assert _MASTER_MAC not in output


def test_status_requires_attended_recovery_for_stale_timer_on_restore_failure(
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
    timer_on_snapshots = tuple(
        snapshot.model_copy(
            update={
                "timer_enabled": True,
                "schedule_fingerprint": "a" * 64,
            }
        )
        for snapshot in intent.snapshots
    )
    intent_store.save(
        intent.model_copy(
            update={
                "phase": hardware_test.HardwareTestIntentPhase.RECOVERY_REQUIRED,
                "snapshots": timer_on_snapshots,
                "outcome": "recovery_required",
            }
        )
    )
    now = datetime.now(UTC)
    JsonLinkageJournalStore(hardware_test.canonical_journal_path(config)).create(
        LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.RECOVERY_REQUIRED,
            recovery_reason=LinkageRecoveryReason.RESTORE_FAILED,
            spec=intent.spec,
            snapshots=timer_on_snapshots,
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
            error="secret-device-id: state_read_failed",
            failed_device_ids=("pro_left",),
        )
    )

    assert hardware_test.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "Recovery reason: restore_failed" in output
    assert (
        "Automatic recovery blockers: timer_on_snapshot, stale_or_clock_invalid" in output
    )
    assert "use attended recover-linkage confirmation" in output
    assert "recover-linkage --recovery-first" not in output
    assert "secret-device-id" not in output
    assert "state_read_failed" not in output
    assert _VENDOR_MASTER_ID not in output
    assert _MASTER_MAC not in output
    assert devices["pro_left"].commands == []
    assert devices["pro_right"].commands == []


def test_prepared_record_has_no_automatic_recovery_blockers() -> None:
    now = datetime.now(UTC)
    record = SimpleNamespace(
        phase=LinkageTransactionPhase.PREPARED,
        recovery_reason=None,
        snapshots=(SimpleNamespace(timer_enabled=True),),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )

    assert hardware_test._automatic_recovery_blockers(record, now=now) == ()


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("timer_on", ("timer_on_snapshot",)),
        ("stale", ("stale_or_clock_invalid",)),
        ("naive_clock", ("stale_or_clock_invalid",)),
        ("deadline_overflow", ("stale_or_clock_invalid",)),
    ],
)
def test_automatic_recovery_blockers_cover_individual_clock_and_timer_boundaries(
    case: str,
    expected: tuple[str, ...],
) -> None:
    now = datetime.now(UTC)
    timer_enabled = case == "timer_on"
    created_at = now
    updated_at = now
    expires_at = now + timedelta(minutes=1)
    if case == "stale":
        expires_at = now - timedelta(
            seconds=hardware_test._MAX_AUTOMATIC_RECOVERY_GRACE_SECONDS + 1
        )
    elif case == "naive_clock":
        created_at = created_at.replace(tzinfo=None)
    elif case == "deadline_overflow":
        expires_at = datetime.max.replace(tzinfo=UTC)
    record = SimpleNamespace(
        phase=LinkageTransactionPhase.ACTIVE,
        recovery_reason=None,
        snapshots=(SimpleNamespace(timer_enabled=timer_enabled),),
        created_at=created_at,
        updated_at=updated_at,
        expires_at=expires_at,
    )

    assert hardware_test._automatic_recovery_blockers(record, now=now) == expected


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


def test_recovery_retry_dwell_covers_audited_maximum_command_interval() -> None:
    assert hardware_test._RECOVERY_RETRY_SECONDS >= (
        hardware_test._MAX_ATTENDED_COMMAND_INTERVAL_MS / 1000
    )


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
