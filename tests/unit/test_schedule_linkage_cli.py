import asyncio
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from jebao_flow import hardware_safety
from jebao_flow import schedule_linkage_cli as cli
from jebao_flow.config import AppConfig
from jebao_flow.device_verification_cli import (
    DeviceVerificationIntent,
    VerificationIntentOutcome,
    VerificationIntentPhase,
    verification_confirmation_token,
)
from jebao_flow.devices.linkage import DeviceControlSnapshot, LinkageTestSpec
from jebao_flow.devices.schedule_linkage import (
    ScheduleAutoEvidence,
    ScheduleBoundaryExpectation,
    ScheduleLinkagePhase,
    ScheduleLinkagePreflight,
    ScheduleLinkageRecord,
    ScheduleLinkageResult,
    ScheduleLinkageSnapshot,
    ScheduleLinkageSpec,
    ScheduleLinkageStopReason,
    schedule_linkage_confirmation_token,
)
from jebao_flow.devices.simulator import SimulatedJebaoDevice
from jebao_flow.devices.verification import (
    DeviceVerificationSnapshot,
    DeviceVerificationSpec,
)
from jebao_flow.hardware_guard import DeploymentHardwareGuard
from jebao_flow.hardware_test import (
    HardwareTestIntent,
    HardwareTestIntentPhase,
    preview_confirmation_token,
)
from jebao_flow.persistence.qualification import (
    DeviceQualificationReceipt,
    JsonQualificationStore,
)
from jebao_flow.persistence.schedule_linkage import (
    JsonScheduleLinkageJournalStore,
    ScheduleLinkageJournalError,
)
from jebao_flow.protocol.models import Capability, DeviceCapabilities, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.safety.limits import PowerLimits

PRIVATE_MASTER_VENDOR_ID = "private-master-vendor-id"
PRIVATE_SLAVE_VENDOR_ID = "private-slave-vendor-id"
PRIVATE_MASTER_MAC = "001122334455"
PRIVATE_SLAVE_MAC = "001122334466"
PRIVATE_ADDRESS = "192.0.2.77"
FIXED_NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


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
        power_limits=PowerLimits(min_power=30, max_power=80),
        power_step=1,
        native_modes=frozenset({"constant", "pulse", "sine"}),
        linkage_roles=frozenset(LinkageRole),
    )


def _devices() -> dict[str, SimulatedJebaoDevice]:
    return {
        "master": SimulatedJebaoDevice("master", capabilities=_capabilities()),
        "slave": SimulatedJebaoDevice("slave", capabilities=_capabilities()),
    }


def _config(*, instance_id: str = "main") -> AppConfig:
    return AppConfig.model_validate(
        {
            "instance": {"id": instance_id, "name": "Private aquarium"},
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
            "devices": [
                {
                    "id": "master",
                    "name": "Private master name",
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "discovery": "auto",
                    "identity": {
                        "device_id": PRIVATE_MASTER_VENDOR_ID,
                        "mac_address": PRIVATE_MASTER_MAC,
                    },
                    "limits": {"min_power": 30, "max_power": 80},
                    "control": {
                        "allow_hardware_writes": True,
                        "minimum_command_interval_ms": 100,
                        "readback_delay_ms": 0,
                        "readback_attempts": 1,
                    },
                },
                {
                    "id": "slave",
                    "name": "Private slave name",
                    "type": "wavemaker",
                    "product_key": LOCAL_WAVEMAKER_PRO.product_key,
                    "discovery": "auto",
                    "identity": {
                        "device_id": PRIVATE_SLAVE_VENDOR_ID,
                        "mac_address": PRIVATE_SLAVE_MAC,
                    },
                    "limits": {"min_power": 30, "max_power": 80},
                    "control": {
                        "allow_hardware_writes": True,
                        "minimum_command_interval_ms": 100,
                        "readback_delay_ms": 0,
                        "readback_attempts": 1,
                    },
                },
            ],
        }
    )


def _spec(**updates: object) -> ScheduleLinkageSpec:
    values: dict[str, object] = {
        "operation_id": "schedule_boundary_001",
        "qualification_operation_id": "qualification_001",
        "master_device_id": "master",
        "slave_device_id": "slave",
        "observation_window_seconds": 180,
        "verification_interval_seconds": 1,
        "minimum_lead_seconds": 45,
        "ambiguous_band_seconds": 1,
        "maximum_clock_skew_seconds": 2,
        "clock_advance_tolerance_seconds": 2,
    }
    values.update(updates)
    return ScheduleLinkageSpec(**values)


def _snapshots(
    devices: dict[str, SimulatedJebaoDevice],
    *,
    slave_after_flow: int = 65,
    fingerprint_suffix: str = "0",
) -> tuple[ScheduleLinkageSnapshot, ...]:
    boundary = datetime(2026, 8, 26, 18, 11)
    snapshots: list[ScheduleLinkageSnapshot] = []
    for device_id, before_flow, after_flow, frequency in (
        ("master", 30, 45, 40),
        ("slave", 50, slave_after_flow, 80),
    ):
        binding = devices[device_id].physical_binding
        assert binding is not None
        snapshots.append(
            ScheduleLinkageSnapshot(
                device_id=device_id,
                physical_binding=binding,
                enabled=True,
                power=40,
                mode="constant",
                frequency=5,
                timer_enabled=True,
                linkage=LinkageRole.INDEPENDENT,
                schedule_fingerprint=("a" * 63) + fingerprint_suffix,
                expectation=ScheduleBoundaryExpectation(
                    current_slot=2,
                    next_slot=3,
                    boundary_at=boundary,
                    after_valid_until=datetime(2026, 8, 26, 23, 59),
                    before=ScheduleAutoEvidence(
                        mode="constant",
                        flow=before_flow,
                        frequency=5,
                    ),
                    after_mode="sine",
                    after_flow=after_flow,
                    after_frequency=frequency,
                ),
            )
        )
    return tuple(snapshots)


def _preflight(
    devices: dict[str, SimulatedJebaoDevice],
    *,
    spec: ScheduleLinkageSpec | None = None,
    slave_after_flow: int = 65,
    fingerprint_suffix: str = "0",
) -> ScheduleLinkagePreflight:
    selected_spec = spec or _spec()
    snapshots = _snapshots(
        devices,
        slave_after_flow=slave_after_flow,
        fingerprint_suffix=fingerprint_suffix,
    )
    return ScheduleLinkagePreflight(
        spec=selected_spec,
        snapshots=snapshots,
        confirmation_token=schedule_linkage_confirmation_token(selected_spec, snapshots),
    )


def _intent(
    preflight: ScheduleLinkagePreflight,
    *,
    instance_id: str = "main",
    phase: cli.ScheduleLinkageIntentPhase = cli.ScheduleLinkageIntentPhase.ARMED,
    outcome: cli.ScheduleLinkageIntentOutcome | None = None,
) -> cli.ScheduleLinkageIntent:
    return cli.ScheduleLinkageIntent(
        instance_id=instance_id,
        operation_id=preflight.spec.operation_id,
        phase=phase,
        confirmation_token=cli.schedule_confirmation_token(instance_id, preflight),
        preflight=preflight,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        outcome=outcome,
    )


def _record(
    preflight: ScheduleLinkagePreflight,
    *,
    now: datetime = FIXED_NOW,
    expires_at: datetime | None = None,
    error: str = "restore failed",
) -> ScheduleLinkageRecord:
    ids = (
        preflight.spec.master_device_id,
        preflight.spec.slave_device_id,
    )
    return ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.RECOVERY_REQUIRED,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=now,
        updated_at=now,
        expires_at=expires_at or now + timedelta(seconds=30),
        linkage_write_intent_device_ids=ids,
        linked_device_ids=ids,
        error=error,
    )


def _save_receipts(
    root: Path,
    preflight: ScheduleLinkagePreflight,
    *,
    operation_id: str | None = None,
) -> dict[Path, bytes]:
    store = JsonQualificationStore(root / "qualifications")
    for snapshot in preflight.snapshots:
        store.save(
            DeviceQualificationReceipt(
                operation_id=(
                    operation_id or preflight.spec.qualification_operation_id
                ),
                device_id=snapshot.device_id,
                physical_binding=snapshot.physical_binding,
                original_power=40,
                step_power=35,
                completed_at=FIXED_NOW - timedelta(minutes=1),
                valid_until=FIXED_NOW + timedelta(hours=1),
            )
        )
    return {
        path: path.read_bytes()
        for path in sorted((root / "qualifications").glob("*.json"))
    }


def _parse(*arguments: str) -> cli.argparse.Namespace:
    return cli.build_parser().parse_args(list(arguments))


def _run_args(intent: cli.ScheduleLinkageIntent) -> cli.argparse.Namespace:
    spec = intent.preflight.spec
    return _parse(
        "run-schedule-linkage",
        "--operation-id",
        spec.operation_id,
        "--qualification-operation-id",
        spec.qualification_operation_id,
        "--master",
        spec.master_device_id,
        "--slave",
        spec.slave_device_id,
        "--observation-window",
        str(spec.observation_window_seconds),
        "--verification-interval",
        str(spec.verification_interval_seconds),
        "--minimum-lead",
        str(spec.minimum_lead_seconds),
        "--ambiguous-band",
        str(spec.ambiguous_band_seconds),
        "--post-boundary-stability",
        str(spec.post_boundary_stability_seconds),
        "--maximum-clock-skew",
        str(spec.maximum_clock_skew_seconds),
        "--clock-advance-tolerance",
        str(spec.clock_advance_tolerance_seconds),
        "--confirm",
        intent.confirmation_token,
    )


def _preflight_args(spec: ScheduleLinkageSpec) -> cli.argparse.Namespace:
    return _parse(
        "preflight",
        "--operation-id",
        spec.operation_id,
        "--qualification-operation-id",
        spec.qualification_operation_id,
        "--master",
        spec.master_device_id,
        "--slave",
        spec.slave_device_id,
        "--observation-window",
        str(spec.observation_window_seconds),
        "--verification-interval",
        str(spec.verification_interval_seconds),
        "--minimum-lead",
        str(spec.minimum_lead_seconds),
        "--ambiguous-band",
        str(spec.ambiguous_band_seconds),
        "--post-boundary-stability",
        str(spec.post_boundary_stability_seconds),
        "--maximum-clock-skew",
        str(spec.maximum_clock_skew_seconds),
        "--clock-advance-tolerance",
        str(spec.clock_advance_tolerance_seconds),
    )


class _FakeController:
    preflight_value: ScheduleLinkagePreflight | None = None
    instances: list["_FakeController"] = []

    def __init__(
        self,
        devices,
        store,
        *,
        prerequisite_authorizer,
        safety_interlock,
        **_kwargs,
    ) -> None:
        self.devices = devices
        self.store = store
        self.authorize = prerequisite_authorizer
        self.safety_interlock = safety_interlock
        self.stop_calls: list[str | None] = []
        type(self).instances.append(self)

    async def preflight(self, spec: ScheduleLinkageSpec) -> ScheduleLinkagePreflight:
        assert self.preflight_value is not None
        assert self.preflight_value.spec == spec
        self.authorize(spec, self.preflight_value.snapshots)
        return self.preflight_value

    async def run(self, preflight: ScheduleLinkagePreflight) -> ScheduleLinkageResult:
        now = FIXED_NOW
        record = ScheduleLinkageRecord(
            operation_id=preflight.spec.operation_id,
            phase=ScheduleLinkagePhase.PREPARED,
            spec=preflight.spec,
            snapshots=preflight.snapshots,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=preflight.spec.observation_window_seconds),
        )
        with self.store.lease():
            self.store.create(record)
            self.store.clear()
        return ScheduleLinkageResult(
            operation_id=preflight.spec.operation_id,
            stop_reason=ScheduleLinkageStopReason.BOUNDARY_VERIFIED,
            schedule_transition_verified=True,
            completed_at=now,
        )

    async def stop(self, operation_id: str | None = None) -> bool:
        self.stop_calls.append(operation_id)
        return True

    async def recover_pending(self) -> bool:
        with self.store.lease():
            if self.store.load() is None:
                return False
            self.store.clear()
        return True


def _dependencies(
    root: Path,
    devices: dict[str, SimulatedJebaoDevice],
    *,
    counters: dict[str, int] | None = None,
    build_writers=None,
) -> cli.ScheduleCliDependencies:
    async def readers(_config, _selected):
        if counters is not None:
            counters["readers"] = counters.get("readers", 0) + 1
        return devices

    async def writers(_config, _selected):
        if counters is not None:
            counters["writers"] = counters.get("writers", 0) + 1
        return devices

    def guard_factory() -> DeploymentHardwareGuard:
        return DeploymentHardwareGuard(
            operation_lock_path=root / "hardware-operation.lock",
            latch_path=root / "emergency-stop.latch",
            poll_interval_seconds=0.001,
        )

    return cli.ScheduleCliDependencies(
        validate_safety_root=lambda: None,
        build_readers=readers,
        build_writers=build_writers or writers,
        guard_factory=guard_factory,
        clock=lambda: FIXED_NOW,
        sleep=lambda _seconds: asyncio.sleep(0),
    )


@pytest.fixture
def safety_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    _FakeController.instances.clear()
    _FakeController.preflight_value = None
    return root


def _persist_armed(preflight: ScheduleLinkagePreflight) -> cli.ScheduleLinkageIntent:
    intent = _intent(preflight)
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(intent)
    return intent


def _persist_record(record: ScheduleLinkageRecord) -> None:
    store = JsonScheduleLinkageJournalStore(hardware_safety.schedule_linkage_journal_path())
    with store.lease():
        store.create(record)


def test_parser_builds_exact_spec_and_rejects_invalid_window() -> None:
    spec = _spec()
    parsed = _preflight_args(spec)

    assert parsed.command == "preflight"
    assert cli._spec_from_args(parsed) == spec
    assert _parse("status").command == "status"
    assert _parse("recover-schedule-linkage", "--recovery-first").recovery_first is True

    with pytest.raises(ValidationError):
        cli._spec_from_args(
            SimpleNamespace(
                operation_id="op",
                qualification_operation_id="qualification",
                master="same",
                slave="same",
                observation_window=180,
                verification_interval=1,
                minimum_lead=45,
                ambiguous_band=1,
                maximum_clock_skew=2,
                clock_advance_tolerance=2,
            )
        )


def test_confirmation_token_binds_instance_and_full_preflight() -> None:
    devices = _devices()
    preflight = _preflight(devices)
    changed = _preflight(devices, slave_after_flow=66)

    token = cli.schedule_confirmation_token("main", preflight)

    assert token.startswith("JFS-")
    assert len(token) == 24
    assert token != cli.schedule_confirmation_token("other", preflight)
    assert token != cli.schedule_confirmation_token("main", changed)
    assert token == cli.schedule_confirmation_token("main", preflight)


async def test_confirmation_mismatch_uses_constant_time_compare_and_is_zero_write(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _persist_armed(preflight)
    _save_receipts(safety_root, preflight)
    comparisons: list[tuple[str, str]] = []
    original = cli.hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return original(left, right)

    monkeypatch.setattr(cli.hmac, "compare_digest", recording_compare)
    args = _run_args(intent)
    args.confirm = "JFS-" + "0" * 20
    counters: dict[str, int] = {}

    with pytest.raises(cli.ScheduleLinkageConfirmationError):
        await cli.dispatch(
            _config(),
            args,
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )

    # Loading the durable intent also validates its embedded token with compare_digest.
    assert len(comparisons) >= 2
    assert comparisons[-1] == (args.confirm, intent.confirmation_token)
    assert counters == {}
    assert all(device.commands == [] for device in devices.values())


async def test_terminal_operation_id_cannot_be_replayed(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    terminal = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.TERMINAL,
        outcome=cli.ScheduleLinkageIntentOutcome.BOUNDARY_VERIFIED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(terminal)
    _save_receipts(safety_root, preflight)
    counters: dict[str, int] = {}
    _FakeController.preflight_value = preflight
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    with pytest.raises(cli.ScheduleLinkageCliError, match="cannot be replayed"):
        await cli.dispatch(
            _config(),
            _preflight_args(preflight.spec),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )

    assert counters == {}
    assert all(device.commands == [] for device in devices.values())


@pytest.mark.parametrize(
    ("artifact", "content", "expected"),
    [
        ("native-linkage.json", "{}", "another unfinished"),
        ("device-verification.json", "{}", "another unfinished"),
        ("native-linkage-intent.json", "not-json", "unreadable"),
        (
            "device-verification-intent.json",
            '{"phase":"terminal","padding":"' + ("x" * (1024 * 1024)) + '"}',
            "too large",
        ),
    ],
)
async def test_other_workflow_conflicts_and_malformed_or_oversize_files_fail_closed(
    safety_root: Path,
    artifact: str,
    content: str,
    expected: str,
) -> None:
    path = safety_root / artifact
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)
    devices = _devices()
    counters: dict[str, int] = {}

    with pytest.raises(cli.ScheduleLinkageCliError, match=expected):
        await cli.dispatch(
            _config(),
            _preflight_args(_spec()),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )

    assert counters == {}
    assert all(device.commands == [] for device in devices.values())


async def test_terminal_other_workflow_intents_do_not_conflict(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    bindings = tuple(device.physical_binding for device in devices.values())
    assert all(binding is not None for binding in bindings)
    native_spec = LinkageTestSpec(
        operation_id="finished_native",
        master_device_id="master",
        slave_device_id="slave",
        slave_role=LinkageRole.ASYNC_SLAVE,
        mode="constant",
        master_power=35,
        slave_power=35,
        frequency=5,
        duration_seconds=30,
    )
    native_snapshots = tuple(
        DeviceControlSnapshot(
            device_id=device_id,
            physical_binding=binding,
            enabled=True,
            power=40,
            mode="constant",
            frequency=5,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
        for device_id, binding in zip(("master", "slave"), bindings, strict=True)
    )
    native = HardwareTestIntent(
        instance_id="main",
        operation_id=native_spec.operation_id,
        phase=HardwareTestIntentPhase.TERMINAL,
        confirmation_token=preview_confirmation_token(
            "main", native_spec, native_snapshots
        ),
        spec=native_spec,
        snapshots=native_snapshots,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        outcome="restored",
    )
    native_path = safety_root / "native-linkage-intent.json"
    native_path.write_text(native.model_dump_json(), encoding="utf-8")
    os.chmod(native_path, 0o600)

    verification_spec = DeviceVerificationSpec(
        operation_id="finished_verification",
        target_power=35,
        duration_seconds=1,
        verification_interval_seconds=0.25,
    )
    verification_snapshot = DeviceVerificationSnapshot(
        physical_binding=bindings[0],
        enabled=True,
        power=40,
        mode="constant",
        frequency=5,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=False,
    )
    verification = DeviceVerificationIntent(
        instance_id="main",
        operation_id=verification_spec.operation_id,
        device_id="master",
        phase=VerificationIntentPhase.TERMINAL,
        confirmation_token=verification_confirmation_token(
            "main", "master", verification_spec, verification_snapshot
        ),
        spec=verification_spec,
        snapshot=verification_snapshot,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        outcome=VerificationIntentOutcome.RESTORED,
    )
    verification_path = safety_root / "device-verification-intent.json"
    verification_path.write_text(verification.model_dump_json(), encoding="utf-8")
    os.chmod(verification_path, 0o600)

    _save_receipts(safety_root, preflight)
    _FakeController.preflight_value = preflight
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    assert (
        await cli.dispatch(
            _config(),
            _preflight_args(preflight.spec),
            dependencies=_dependencies(safety_root, devices),
        )
        == 0
    )


async def test_preflight_is_read_only_and_persists_private_armed_intent(
    safety_root: Path,
    monkeypatch,
    capsys,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    receipts_before = _save_receipts(safety_root, preflight)
    counters: dict[str, int] = {}
    _FakeController.preflight_value = preflight
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    assert (
        await cli.dispatch(
            _config(),
            _preflight_args(preflight.spec),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )
        == 0
    )

    output = capsys.readouterr().out
    stored = cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).load()
    assert stored is not None
    assert stored.phase is cli.ScheduleLinkageIntentPhase.ARMED
    assert stored.preflight == preflight
    assert stat.S_IMODE(hardware_safety.schedule_linkage_intent_path().stat().st_mode) == 0o600
    assert counters == {"readers": 1}
    assert all(device.commands == [] for device in devices.values())
    assert not hardware_safety.schedule_linkage_journal_path().exists()
    assert {
        path: path.read_bytes() for path in receipts_before
    } == receipts_before
    for private in (
        PRIVATE_MASTER_VENDOR_ID,
        PRIVATE_SLAVE_VENDOR_ID,
        PRIVATE_MASTER_MAC,
        PRIVATE_SLAVE_MAC,
        PRIVATE_ADDRESS,
    ):
        assert private not in output


async def test_exact_qualification_operation_is_required_and_never_rewritten(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _persist_armed(preflight)
    wrong_receipts = _save_receipts(
        safety_root,
        preflight,
        operation_id="different_qualification",
    )
    counters: dict[str, int] = {}
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    with pytest.raises(cli.ScheduleLinkageCliError, match="exact controllers"):
        await cli.dispatch(
            _config(),
            _run_args(intent),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )
    assert counters == {}
    assert {path: path.read_bytes() for path in wrong_receipts} == wrong_receipts

    exact_receipts = _save_receipts(safety_root, preflight)
    assert (
        await cli.dispatch(
            _config(),
            _run_args(intent),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )
        == 0
    )
    assert counters == {"writers": 1}
    assert {path: path.read_bytes() for path in exact_receipts} == exact_receipts
    assert all(device.commands == [] for device in devices.values())
    terminal = cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.outcome is cli.ScheduleLinkageIntentOutcome.BOUNDARY_VERIFIED


async def test_run_persists_started_before_writer_build_and_connect(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _persist_armed(preflight)
    _save_receipts(safety_root, preflight)
    intent_store = cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    )
    connect_phases: list[cli.ScheduleLinkageIntentPhase] = []
    for device in devices.values():
        original_connect = device.connect

        async def checked_connect(original_connect=original_connect) -> None:
            stored = intent_store.load()
            assert stored is not None
            connect_phases.append(stored.phase)
            await original_connect()

        device.connect = checked_connect

    async def writers(_config, _selected):
        stored = intent_store.load()
        assert stored is not None
        assert stored.phase is cli.ScheduleLinkageIntentPhase.STARTED
        return devices

    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    assert (
        await cli.dispatch(
            _config(),
            _run_args(intent),
            dependencies=_dependencies(
                safety_root,
                devices,
                build_writers=writers,
            ),
        )
        == 0
    )
    assert connect_phases == [
        cli.ScheduleLinkageIntentPhase.STARTED,
        cli.ScheduleLinkageIntentPhase.STARTED,
    ]


async def test_writer_connect_failure_leaves_started_and_no_journal(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _persist_armed(preflight)
    _save_receipts(safety_root, preflight)
    connect_count = 0

    async def fail_connect() -> None:
        nonlocal connect_count
        connect_count += 1
        raise RuntimeError("private connection failure")

    devices["master"].connect = fail_connect
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    with pytest.raises(RuntimeError, match="private connection failure"):
        await cli.dispatch(
            _config(),
            _run_args(intent),
            dependencies=_dependencies(safety_root, devices),
        )

    stored = cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).load()
    assert stored is not None
    assert stored.phase is cli.ScheduleLinkageIntentPhase.STARTED
    assert stored.outcome is None
    assert connect_count == 1
    assert not hardware_safety.schedule_linkage_journal_path().exists()
    assert all(device.commands == [] for device in devices.values())


def test_confirming_store_persists_terminal_before_journal_clear(tmp_path: Path) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    record = ScheduleLinkageRecord(
        operation_id=preflight.spec.operation_id,
        phase=ScheduleLinkagePhase.PREPARED,
        spec=preflight.spec,
        snapshots=preflight.snapshots,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        expires_at=FIXED_NOW + timedelta(minutes=3),
    )
    delegate = JsonScheduleLinkageJournalStore(tmp_path / "schedule.json")
    terminal_was_durable = False

    def before_clear() -> None:
        nonlocal terminal_was_durable
        assert delegate.load() == record
        terminal_was_durable = True

    wrapper = cli.ConfirmingScheduleLinkageJournalStore(
        delegate,
        instance_id="main",
        expected_preflight=preflight,
        expected_token=cli.schedule_confirmation_token("main", preflight),
        qualification_store=JsonQualificationStore(tmp_path / "qualifications"),
        before_clear=before_clear,
        now=lambda: FIXED_NOW,
    )
    with delegate.lease():
        delegate.create(record)
        wrapper.clear()

    assert terminal_was_durable is True
    assert delegate.load() is None


def test_recovery_wrapper_accepts_only_its_exact_post_fsync_successor(
    tmp_path: Path,
) -> None:
    class FailAfterFsyncStore(JsonScheduleLinkageJournalStore):
        fail_next_fsync = False

        def _fsync_parent(self) -> None:
            super()._fsync_parent()
            if self.fail_next_fsync:
                self.fail_next_fsync = False
                raise OSError("simulated post-fsync report failure")

    preflight = _preflight(_devices())
    record = _record(preflight)
    delegate = FailAfterFsyncStore(tmp_path / "schedule.json")
    wrapper = cli.ConfirmingScheduleLinkageJournalStore(
        delegate,
        instance_id="main",
        expected_preflight=preflight,
        expected_token=cli.schedule_confirmation_token("main", preflight),
        qualification_store=JsonQualificationStore(tmp_path / "qualifications"),
        before_clear=lambda: None,
        now=lambda: FIXED_NOW,
        expected_loaded_record=record,
        require_loaded_record_match=True,
    )
    successor = record.model_copy(
        update={
            "phase": ScheduleLinkagePhase.ROLLING_BACK,
            "updated_at": FIXED_NOW + timedelta(microseconds=1),
            "error": None,
        }
    )

    with delegate.lease():
        delegate.create(record)
        delegate.fail_next_fsync = True
        with pytest.raises(ScheduleLinkageJournalError):
            wrapper.save(successor)
        assert wrapper.confirms_lease_successor(successor) is True

    assert delegate.load() == successor


async def test_run_with_signal_requests_stop_without_cancelling_controller() -> None:
    finished = asyncio.Event()

    class SignalController:
        def __init__(self) -> None:
            self.stop_calls: list[str | None] = []
            self.cancelled = False

        async def run(self, preflight):
            try:
                await finished.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return "detached"

        async def stop(self, operation_id):
            self.stop_calls.append(operation_id)
            finished.set()
            return True

    controller = SignalController()
    preflight = _preflight(_devices())
    interrupt = asyncio.Event()
    task = asyncio.create_task(
        cli._run_with_signals(controller, preflight, interrupt_event=interrupt)
    )
    await asyncio.sleep(0)
    interrupt.set()

    assert await task == "detached"
    assert controller.stop_calls == [preflight.spec.operation_id]
    assert controller.cancelled is False


async def test_external_task_cancellation_waits_for_normal_controller_stop() -> None:
    finished = asyncio.Event()

    class CancellationController:
        def __init__(self) -> None:
            self.stop_calls: list[str | None] = []
            self.cancelled = False

        async def run(self, preflight):
            try:
                await finished.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return "detached"

        async def stop(self, operation_id):
            self.stop_calls.append(operation_id)
            finished.set()
            return True

    controller = CancellationController()
    preflight = _preflight(_devices())
    task = asyncio.create_task(cli._run_with_signals(controller, preflight))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert controller.stop_calls == [preflight.spec.operation_id]
    assert controller.cancelled is False


async def test_fresh_exact_recovery_is_automatic_and_terminal(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(intent)
    _persist_record(_record(preflight))
    counters: dict[str, int] = {}
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    assert (
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--recovery-first"),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )
        == 0
    )

    assert counters == {"writers": 1}
    assert not hardware_safety.schedule_linkage_journal_path().exists()
    terminal = cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.phase is cli.ScheduleLinkageIntentPhase.TERMINAL
    assert terminal.outcome is cli.ScheduleLinkageIntentOutcome.RECOVERED


async def test_terminal_before_clear_crash_is_automatically_reverified_and_cleared(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    terminal = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.TERMINAL,
        outcome=cli.ScheduleLinkageIntentOutcome.ROLES_DETACHED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(terminal)
    record = _record(preflight).model_copy(
        update={"detached_device_ids": ("slave", "master")}
    )
    _persist_record(record)
    counters: dict[str, int] = {}
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)

    assert (
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--recovery-first"),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )
        == 0
    )
    assert counters == {"writers": 1}
    assert not hardware_safety.schedule_linkage_journal_path().exists()


async def test_terminal_intent_with_incomplete_detach_is_not_automatic(
    safety_root: Path,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    terminal = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.TERMINAL,
        outcome=cli.ScheduleLinkageIntentOutcome.ROLES_DETACHED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(terminal)
    record = _record(preflight).model_copy(update={"detached_device_ids": ("slave",)})
    _persist_record(record)

    with pytest.raises(cli.ScheduleLinkageCliError, match="attended"):
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--recovery-first"),
            dependencies=_dependencies(safety_root, devices),
        )


async def test_recovery_confirmation_corruption_is_not_retried(
    safety_root: Path,
    monkeypatch,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(intent)
    _persist_record(_record(preflight))
    calls = 0
    sleeps = 0

    class CorruptingController(_FakeController):
        async def recover_pending(self) -> bool:
            nonlocal calls
            calls += 1
            raise cli.ScheduleLinkageConfirmationError("changed")

    async def no_sleep(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1

    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", CorruptingController)
    dependencies = _dependencies(safety_root, devices)
    dependencies = cli.ScheduleCliDependencies(
        validate_safety_root=dependencies.validate_safety_root,
        build_readers=dependencies.build_readers,
        build_writers=dependencies.build_writers,
        guard_factory=dependencies.guard_factory,
        clock=dependencies.clock,
        sleep=no_sleep,
    )

    with pytest.raises(cli.ScheduleLinkageConfirmationError):
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--recovery-first"),
            dependencies=dependencies,
        )
    assert calls == 1
    assert sleeps == 0


@pytest.mark.parametrize("reason", ["stale", "mismatch", "safety"])
async def test_stale_mismatched_or_safety_recovery_requires_attended_token(
    safety_root: Path,
    monkeypatch,
    reason: str,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    record = _record(preflight)
    intent_preflight = preflight
    if reason == "stale":
        stale = FIXED_NOW - timedelta(minutes=5)
        record = _record(
            preflight,
            now=stale,
            expires_at=stale + timedelta(seconds=30),
        )
    elif reason == "mismatch":
        intent_preflight = _preflight(devices, fingerprint_suffix="b")
    else:
        record = _record(preflight, error="safety interlock interrupted detach")
    intent = _intent(
        intent_preflight,
        phase=cli.ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(intent)
    _persist_record(record)
    counters: dict[str, int] = {}
    monkeypatch.setattr(cli, "ScheduleActiveLinkageController", _FakeController)
    dependencies = _dependencies(safety_root, devices, counters=counters)

    with pytest.raises(cli.ScheduleLinkageCliError, match="attended"):
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--recovery-first"),
            dependencies=dependencies,
        )
    assert counters == {}
    assert hardware_safety.schedule_linkage_journal_path().exists()

    token = cli.schedule_recovery_token("main", record, intent)
    assert (
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--confirm", token),
            dependencies=dependencies,
        )
        == 0
    )
    assert counters == {"writers": 1}
    assert not hardware_safety.schedule_linkage_journal_path().exists()


async def test_started_without_journal_closes_as_proven_no_write(
    safety_root: Path,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    started = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.STARTED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(started)
    counters: dict[str, int] = {}

    assert (
        await cli.dispatch(
            _config(),
            _parse("recover-schedule-linkage", "--recovery-first"),
            dependencies=_dependencies(safety_root, devices, counters=counters),
        )
        == 0
    )
    assert counters == {}
    assert all(device.commands == [] for device in devices.values())
    terminal = cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).load()
    assert terminal is not None
    assert terminal.outcome is cli.ScheduleLinkageIntentOutcome.CRASHED_BEFORE_FIRST_WRITE


async def test_status_is_sanitized_and_contains_only_safe_summary(
    safety_root: Path,
    capsys,
) -> None:
    devices = _devices()
    preflight = _preflight(devices)
    intent = _intent(
        preflight,
        phase=cli.ScheduleLinkageIntentPhase.RECOVERY_REQUIRED,
    )
    cli.JsonScheduleLinkageIntentStore(
        hardware_safety.schedule_linkage_intent_path()
    ).save(intent)
    _persist_record(_record(preflight, error="private raw device failure"))

    assert (
        await cli.dispatch(
            _config(),
            _parse("status"),
            dependencies=_dependencies(safety_root, devices),
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Schedule intent: recovery_required" in output
    assert "Role-only journal: recovery_required" in output
    assert "Recovery confirmation token: JFSR-" in output
    for private in (
        PRIVATE_MASTER_VENDOR_ID,
        PRIVATE_SLAVE_VENDOR_ID,
        PRIVATE_MASTER_MAC,
        PRIVATE_SLAVE_MAC,
        PRIVATE_ADDRESS,
        "Private aquarium",
        "Private master name",
        "Private slave name",
        preflight.spec.operation_id,
        preflight.spec.qualification_operation_id,
        preflight.spec.master_device_id,
        preflight.spec.slave_device_id,
        intent.confirmation_token,
        "private raw device failure",
    ):
        assert private not in output


def test_intent_store_rejects_unsafe_metadata_and_oversize(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    store = cli.JsonScheduleLinkageIntentStore(root / "intent.json")
    intent = _intent(_preflight(_devices()))

    store.save(intent)
    assert store.load() == intent
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with store.lease():
        assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600

    os.chmod(store.path, 0o644)
    with pytest.raises(cli.ScheduleLinkageCliError, match="unsafe metadata"):
        store.load()
    os.chmod(store.path, 0o600)

    alias = root / "hardlink"
    os.link(store.path, alias)
    with pytest.raises(cli.ScheduleLinkageCliError, match="unsafe metadata"):
        store.load()
    alias.unlink()

    store.path.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    os.chmod(store.path, 0o600)
    with pytest.raises(cli.ScheduleLinkageCliError, match="too large"):
        store.load()

    store.path.unlink()
    outside = tmp_path / "outside"
    outside.write_text(intent.model_dump_json(), encoding="utf-8")
    store.path.symlink_to(outside)
    with pytest.raises(cli.ScheduleLinkageCliError, match="unsafe metadata"):
        store.load()


def test_intent_store_rejects_malformed_json_and_unsafe_lock(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    store = cli.JsonScheduleLinkageIntentStore(root / "intent.json")
    store.path.write_text("not-json", encoding="utf-8")
    os.chmod(store.path, 0o600)
    with pytest.raises(cli.ScheduleLinkageCliError, match="unreadable"):
        store.load()

    store.path.unlink()
    store.lock_path.write_text("lock\n", encoding="utf-8")
    os.chmod(store.lock_path, 0o600)
    os.link(store.lock_path, root / "lock-alias")
    with pytest.raises(cli.ScheduleLinkageCliError, match="unsafe metadata"):
        with store.lease():
            pass


def test_intent_time_cannot_regress_and_write_size_is_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intent = _intent(_preflight(_devices()))
    payload = intent.model_dump(mode="json")
    payload["updated_at"] = (FIXED_NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError, match="cannot precede"):
        cli.ScheduleLinkageIntent.model_validate(payload)

    started = cli._updated_intent(
        intent,
        cli.ScheduleLinkageIntentPhase.STARTED,
        None,
        now=FIXED_NOW - timedelta(days=1),
    )
    assert started.updated_at == intent.updated_at

    store = cli.JsonScheduleLinkageIntentStore(tmp_path / "intent.json")
    monkeypatch.setattr(cli, "_MAX_INTENT_BYTES", 10)
    with pytest.raises(cli.ScheduleLinkageCliError, match="too large"):
        store.save(intent)
    assert not store.path.exists()
