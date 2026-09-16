"""Offline tests for the Aquarium Pump safer-OFF power qualification.

These make no firmware-behavior or hardware-qualification claims. They prove the software contract:
the three-frame ON/OFF/ON journal, single send, receipts only from a durably verified journal, and
that a failed operation converges to OFF without ever re-igniting the original ON.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.aquarium_power_qualification import (
    AquariumPowerQualificationIntent,
    AquariumPowerQualificationProducer,
    aquarium_power_target_validator,
)
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, LocalDeviceWriteError
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.qualification import (
    load_current_qualifications,
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationEpoch,
    QualificationProductionError,
)
from jebao_flow.local_control.resolver import (
    CurrentEndpointResolutionError,
    ResolvedEndpoint,
)
from jebao_flow.protocol.profiles import AQUARIUM_PUMP, LOCAL_WAVEMAKER_PRO

NOW = datetime(2026, 9, 16, 7, tzinfo=UTC)


def _binding(
    *,
    product_key: str = AQUARIUM_PUMP.product_key,
    marker: str = "c",
) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="aquarium_power_target",
        product_key=product_key,
        physical_identity_key=marker * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


def _epoch(
    *,
    target: LocalDeviceBinding | None = None,
    minimum: int = 30,
    maximum: int = 100,
) -> QualificationEpoch:
    return QualificationEpoch(
        target=target or _binding(),
        config_sha256="1" * 64,
        manifest_sha256="2" * 64,
        manifest_generation="3" * 32,
        min_power=minimum,
        max_power=maximum,
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


def _aquarium_status(
    *,
    enabled: bool = True,
    timer_enabled: bool = False,
    transport: bool = False,
    feed: bool = False,
    auto_mode: int = 0,
    motor_speed: int = 40,
) -> bytes:
    raw = bytearray(AQUARIUM_PUMP.raw_status_size)
    raw[0] = (
        int(enabled)
        | (int(transport) << 1)
        | (int(feed) << 2)
        | (int(timer_enabled) << 3)
        | (auto_mode << 4)
    )
    raw[1] = motor_speed
    return bytes(raw)


class _Permit:
    def require_permitted(self) -> None:
        pass

    def require_pre_send_permitted(self) -> None:
        pass


class _Lease:
    def __init__(self) -> None:
        self.acquired: list[tuple[str, ...]] = []

    @contextmanager
    def acquire(self, keys: tuple[str, ...]):
        self.acquired.append(keys)
        yield _Permit()


class _Resolver:
    def __init__(self, endpoint: ResolvedEndpoint, *, fail_at: int | None = None) -> None:
        self.endpoint = endpoint
        self.fail_at = fail_at
        self.calls = 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        if self.fail_at == self.calls:
            raise CurrentEndpointResolutionError("strict resolution failed")
        return self.endpoint

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        return tuple([await self.resolve(target) for target in targets])


class _Session:
    def __init__(
        self,
        statuses: list[bytes | BaseException],
        sent: list[bytes],
        *,
        send_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.sent = sent
        self.send_error = send_error
        self.quarantined = False

    async def connect(self) -> None:
        pass

    async def authenticate(self) -> bytes:
        return b"private-authentication"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
        value = self.statuses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def send_raw_control(self, payload: bytes) -> bytes:
        self.sent.append(bytes(payload))
        if self.send_error is not None:
            raise self.send_error
        return b"acknowledged"

    async def disconnect(self) -> None:
        pass

    def quarantine(self) -> None:
        self.quarantined = True


class _WriterFactory:
    def __init__(self, sessions: list[_Session]) -> None:
        self.sessions = sessions
        self.created = 0

    def __call__(self, endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
        session = self.sessions.pop(0)
        self.created += 1
        return ConnectedDeviceWriter(endpoint, session_factory=lambda _address: session)


def _artifacts(tmp_path: Path) -> QualificationArtifactStore:
    return QualificationArtifactStore.for_test(
        tmp_path / "receipts",
        tmp_path / "output" / "qualifications.json",
    )


def _producer(
    tmp_path: Path,
    *,
    sessions: list[_Session],
    target: LocalDeviceBinding | None = None,
    epoch: QualificationEpoch | None = None,
    resolver_fail_at: int | None = None,
):
    target = target or _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    resolver = _Resolver(endpoint, fail_at=resolver_fail_at)
    writer_factory = _WriterFactory(sessions)
    producer = AquariumPowerQualificationProducer(
        epoch_provider=lambda: epoch or _epoch(target=target),
        resolver_factory=lambda: resolver,
        journal=journal,
        lease=lease,
        artifacts=_artifacts(tmp_path),
        writer_factory=writer_factory,
        clock=lambda: NOW,
    )
    return producer, journal, lease, resolver, writer_factory


def _loaded_binding(tmp_path: Path):
    return load_current_qualifications(
        tmp_path / "output" / "qualifications.json",
        _allow_ephemeral_for_test=True,
    )[0]


def _on_off_on_sessions(sent: list[bytes]) -> list[_Session]:
    on = _aquarium_status()
    off = _aquarium_status(enabled=False)
    return [
        _Session([on, on], sent),
        _Session([on, off], sent),
        _Session([off, on], sent),
    ]


@pytest.mark.asyncio
async def test_success_is_one_three_action_journal_and_two_standard_power_receipts(
    tmp_path: Path,
) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, journal, lease, resolver, factory = _producer(
        tmp_path, sessions=_on_off_on_sessions(sent), target=target
    )

    result = await producer.execute("aquarium_power_0001")

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3
    assert result.receipt_count == 2
    assert len(sent) == 3
    assert sent[0] == sent[2]  # the ON frame is identical; only the OFF frame differs
    assert sent[1] != sent[0]
    assert resolver.calls == 3
    assert factory.created == 3
    assert lease.acquired == [(target.physical_identity_key,)]
    record = journal.load("aquarium_power_0001")
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert tuple(a.action_id for a in record.actions) == ("same_on", "power_off", "planned_on")
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)
    binding = _loaded_binding(tmp_path)
    assert binding.product_key == AQUARIUM_PUMP.product_key
    assert binding.function == "power"
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert qualified_physical_identity_keys(
        binding, _receipt_root_for_test=tmp_path / "receipts"
    ) == frozenset({target.physical_identity_key})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", [{"transport": True}, {"motor_speed": 39}, {"timer_enabled": True}]
)
async def test_power_readback_does_not_require_unowned_historical_values(tmp_path, changed):
    sent = []
    original = _aquarium_status()
    changed_on = _aquarium_status(**changed)
    changed_off = _aquarium_status(enabled=False, **changed)
    producer, journal, *_ = _producer(
        tmp_path,
        sessions=[
            _Session([original, changed_on], sent),
            _Session([changed_on, changed_off], sent),
            _Session([changed_off, changed_on], sent),
        ],
    )

    result = await producer.execute("aquarium_power_unowned")

    assert result.receipt_count == 2 and result.phase is TransactionPhase.VERIFIED
    record = journal.load("aquarium_power_unowned")
    assert record is not None
    for action in record.actions:
        assert set(json.loads(journal.load_blob(action.expected_readback_sha256))) == {"SwitchON"}


@pytest.mark.asyncio
@pytest.mark.parametrize("timer", [False, True])
async def test_power_readback_still_bounds_the_current_output(tmp_path, timer):
    sent = []
    excessive = bytearray(_aquarium_status(timer_enabled=timer, auto_mode=1, motor_speed=46))
    excessive[3] = 46
    producer, journal, *_ = _producer(
        tmp_path, sessions=[_Session([_aquarium_status(), bytes(excessive)], sent)]
    )

    with pytest.raises(QualificationProductionError, match="output is outside its bounds"):
        await producer.execute("aquarium_power_output")

    assert len(sent) == 1
    assert journal.load("aquarium_power_output").phase is TransactionPhase.RECOVERY_REQUIRED
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_off_readback_checks_its_switch_not_an_inactive_output(tmp_path):
    sent = []
    on, off = _aquarium_status(), _aquarium_status(enabled=False)
    producer, *_ = _producer(
        tmp_path,
        sessions=[
            _Session([on, on], sent),
            _Session([on, _aquarium_status(enabled=False, motor_speed=0)], sent),
            _Session([off, on], sent),
        ],
    )
    # Supplied raw frames test validation scope, not a claim that firmware reports zero.
    result = await producer.execute("aquarium_power_off_output")
    assert result.receipt_count == 2 and len(sent) == 3


@pytest.mark.asyncio
async def test_planned_on_still_checks_the_output_before_sending(tmp_path):
    sent = []
    on = _aquarium_status()
    changed_off = _aquarium_status(enabled=False, motor_speed=81)
    producer, journal, *_ = _producer(
        tmp_path,
        sessions=[
            _Session([on, on], sent),
            _Session([on, changed_off], sent),
            _Session([changed_off], sent),
        ],
    )
    with pytest.raises(QualificationProductionError, match="output is outside its bounds"):
        await producer.execute("aquarium_power_before_on")
    assert len(sent) == 2
    record = journal.load("aquarium_power_before_on")
    assert record.actions[1].phase is ActionPhase.VERIFIED
    assert record.actions[2].phase is ActionPhase.PREPARED
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_off_frame_keeps_safer_off_and_intent_records_only_real_fields(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions=_on_off_on_sessions(sent)
    )

    await producer.execute("aquarium_power_0002")

    record = journal.load("aquarium_power_0002")
    assert record is not None
    # Only the OFF frame declines restore, so recovery converges to OFF instead of re-igniting ON.
    assert tuple(a.restore_on_recovery for a in record.actions) == (True, False, True)
    intent = AquariumPowerQualificationIntent.model_validate_json(
        journal.load_blob(record.intent_sha256)
    )
    assert intent.baseline_motor_speed == 40
    assert intent.baseline_transport_mode is False
    fields = set(intent.model_dump(mode="json"))
    assert {"baseline_motor_speed", "baseline_transport_mode"} <= fields
    assert not fields & {"baseline_flow", "baseline_mode", "baseline_linkage"}


@pytest.mark.parametrize(
    "status",
    (
        _aquarium_status(enabled=False),
        _aquarium_status(timer_enabled=True),
        _aquarium_status(motor_speed=46),  # above the retained first-write ceiling of 45
        _aquarium_status(motor_speed=29),  # below the declared app minimum of 30
    ),
)
@pytest.mark.asyncio
async def test_envelope_rejections_send_nothing(tmp_path: Path, status: bytes) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path, sessions=[_Session([status], sent)]
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("aquarium_power_bad")

    assert sent == []
    assert journal.load("aquarium_power_bad") is None
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_motor_speed_outside_configured_device_limits_sends_nothing(tmp_path: Path) -> None:
    target = _binding()
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        sessions=[_Session([_aquarium_status(motor_speed=40)], sent)],
        target=target,
        epoch=_epoch(target=target, minimum=30, maximum=35),
    )

    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("aquarium_power_limits")

    assert sent == []
    assert journal.load("aquarium_power_limits") is None


@pytest.mark.asyncio
async def test_non_aquarium_target_is_rejected_before_any_write(tmp_path: Path) -> None:
    target = _binding(product_key=LOCAL_WAVEMAKER_PRO.product_key, marker="d")
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        sessions=[_Session([_aquarium_status()], sent)],
        target=target,
        epoch=_epoch(target=target),
    )

    with pytest.raises(QualificationProductionError, match="target is not current"):
        await producer.execute("aquarium_power_wrong")

    assert sent == []
    assert journal.load("aquarium_power_wrong") is None


def test_target_validator_accepts_only_the_current_aquarium_pump() -> None:
    assert aquarium_power_target_validator(AQUARIUM_PUMP.product_key) == AQUARIUM_PUMP.product_key
    with pytest.raises(QualificationProductionError, match="target is not current"):
        aquarium_power_target_validator(LOCAL_WAVEMAKER_PRO.product_key)


def test_current_epoch_uses_aquarium_power_validator_without_manual_flow_fields():
    from tests.unit.test_local_control_qualification_cli import _config, _source

    source = _source(_config(product_key=AQUARIUM_PUMP.product_key))
    source = replace(source, target_validator=aquarium_power_target_validator)
    epoch = source()
    assert epoch.target.product_key == AQUARIUM_PUMP.product_key
    assert epoch.min_power == 30 and epoch.max_power == 75


@pytest.mark.asyncio
async def test_off_send_fault_is_recovery_required_and_emits_no_receipt(tmp_path: Path) -> None:
    on = _aquarium_status()
    sent: list[bytes] = []
    producer, journal, _lease, _resolver, _factory = _producer(
        tmp_path,
        sessions=[
            _Session([on, on], sent),
            _Session([on], sent, send_error=LocalDeviceWriteError("off ack failed")),
        ],
    )

    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("aquarium_power_fault")

    record = journal.load("aquarium_power_fault")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.VERIFIED
    assert record.actions[1].phase is ActionPhase.UNCERTAIN
    assert record.actions[2].phase is ActionPhase.PREPARED
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_generic_recover_converges_to_off_and_never_reignites_the_original_on(
    tmp_path: Path,
) -> None:
    on = _aquarium_status()
    off = _aquarium_status(enabled=False)
    sent: list[bytes] = []
    target = _binding()
    producer, journal, lease, _resolver, _factory = _producer(
        tmp_path,
        sessions=[_Session([on, on], sent), _Session([on, off], sent)],
        target=target,
        resolver_fail_at=3,  # crash while resolving for the planned ON restore
    )

    with pytest.raises(CurrentEndpointResolutionError):
        await producer.execute("aquarium_power_crash")

    record = journal.load("aquarium_power_crash")
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(a.phase for a in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.VERIFIED,
        ActionPhase.PREPARED,
    )
    assert len(sent) == 2
    assert not tuple((tmp_path / "receipts").iterdir())

    # Generic recovery must retain the safer OFF state: no frame is sent and ON is not restored.
    recovery_sent: list[bytes] = []
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),  # type: ignore[arg-type]
        journal=journal,
        lease=lease,  # type: ignore[arg-type]
        writer_factory=_WriterFactory(
            [
                _Session([off], recovery_sent),
                _Session([off], recovery_sent),
            ]
        ),
    )

    recovered = await coordinator.recover("aquarium_power_crash")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovered.physical_write_count == 0
    assert recovery_sent == []
    assert sent[0] not in recovery_sent  # the original ON payload is never re-sent
    assert not tuple((tmp_path / "receipts").iterdir())
