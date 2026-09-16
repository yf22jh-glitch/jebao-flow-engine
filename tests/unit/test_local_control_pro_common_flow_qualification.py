"""Offline tests for the Pro common slave-flow qualification.

These assert the software contract only. Nothing here establishes a slave role, an independent
slave output while a master drives it, or any firmware timing or content ownership.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    LocalDeviceWriteError,
    build_control_payload,
    get_product_schema,
)
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.pro_common_flow_qualification import (
    ProCommonFlowQualificationIntent,
    ProCommonFlowQualificationProducer,
    require_bounded_common_flow,
)
from jebao_flow.local_control.qualification import (
    load_current_qualifications,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationEpoch,
    QualificationProductionError,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from tests.unit.test_local_control_qualification_producer import _status

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
PRO = LOCAL_WAVEMAKER_PRO.product_key


def _binding(marker: str = "e") -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="pro_common_flow_target",
        product_key=PRO,
        physical_identity_key=marker * 64,
        logical_role="standalone",
        role_attested_by="operator",
        role_attested_at=NOW,
    )


def _epoch(target: LocalDeviceBinding | None = None, *, maximum: int = 100) -> QualificationEpoch:
    return QualificationEpoch(
        target=target or _binding(),
        config_sha256="1" * 64,
        manifest_sha256="2" * 64,
        manifest_generation="3" * 32,
        min_power=30,
        max_power=maximum,
        app_version="3.4.18",
        app_catalog_sha256="4" * 64,
        template_sha256="5" * 64,
        schema_sha256="6" * 64,
    )


class _Permit:
    def require_permitted(self) -> None: ...
    def require_pre_send_permitted(self) -> None: ...


class _Lease:
    @contextmanager
    def acquire(self, keys):
        yield _Permit()


class _Resolver:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    async def resolve(self, target):
        return self.endpoint

    async def resolve_many(self, targets):
        return tuple([await self.resolve(item) for item in targets])


class _Session:
    def __init__(self, statuses, sent, *, send_error=None):
        self.statuses, self.sent, self.send_error = statuses, sent, send_error
        self.quarantined = False

    async def connect(self): ...
    async def authenticate(self):
        return b"private-authentication"

    async def disconnect(self): ...
    def quarantine(self):
        self.quarantined = True

    async def read_raw_state(self, *, accept_reports: bool = False):
        value = self.statuses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def send_raw_control(self, payload: bytes):
        self.sent.append(bytes(payload))
        if self.send_error is not None:
            raise self.send_error
        return b"acknowledged"


class _WriterFactory:
    def __init__(self, sessions):
        self.sessions, self.created = sessions, 0

    def __call__(self, endpoint):
        session = self.sessions.pop(0)
        self.created += 1
        return ConnectedDeviceWriter(endpoint, session_factory=lambda _address: session)


def _producer(tmp_path: Path, sessions, *, target=None, epoch=None):
    target = target or _binding()
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    lease = _Lease()
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    producer = ProCommonFlowQualificationProducer(
        epoch_provider=lambda: epoch or _epoch(target),
        resolver_factory=lambda: _Resolver(endpoint),
        journal=journal,
        lease=lease,
        artifacts=QualificationArtifactStore.for_test(
            tmp_path / "receipts", tmp_path / "output" / "qualifications.json"
        ),
        writer_factory=_WriterFactory(sessions),
        clock=lambda: NOW,
    )
    return producer, journal, lease, endpoint


def _happy(flow: int = 40):
    """One status per read: baseline, then a fresh read and a readback for each frame."""

    return [
        _status(flow=flow),  # baseline read
        _status(flow=flow),  # same-frame readback
        _status(flow=flow),  # fresh read before the step
        _status(flow=flow - 1),  # step readback
        _status(flow=flow - 1),  # fresh read before the restore
        _status(flow=flow),  # restore readback
    ]


def _sessions(statuses, sent, *, send_error=None, count=4):
    remaining = list(statuses)
    made = []
    for _ in range(count):
        made.append(_Session(remaining, sent, send_error=send_error))
    return made


@pytest.mark.asyncio
async def test_success_is_three_frames_one_step_down_and_two_receipts(tmp_path: Path) -> None:
    sent: list[bytes] = []
    target = _binding()
    producer, journal, _lease, _endpoint = _producer(
        tmp_path, _sessions(_happy(), sent), target=target
    )

    result = await producer.execute("pro_common_flow_0001")

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3 and result.receipt_count == 2
    record = journal.load("pro_common_flow_0001")
    assert all(action.phase is ActionPhase.VERIFIED for action in record.actions)
    assert all(action.function == "slave_common_flow" for action in record.actions)
    # One captured raw owns every rollback.
    assert len({action.baseline_status_sha256 for action in record.actions}) == 1
    intent = ProCommonFlowQualificationIntent.model_validate_json(
        journal.load_blob(record.intent_sha256)
    )
    assert intent.baseline_value == 40 and intent.trial_value == 39
    assert intent.baseline_value == intent.baseline_flow
    step, restore = record.actions[1], record.actions[2]
    assert json.loads(journal.load_blob(step.expected_readback_sha256)) == {"Flow": 39}
    assert json.loads(journal.load_blob(restore.expected_readback_sha256)) == {"Flow": 40}
    binding = load_current_qualifications(
        tmp_path / "output" / "qualifications.json", _allow_ephemeral_for_test=True
    )[0]
    assert binding.function == "slave_common_flow" and binding.product_key == PRO
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2


@pytest.mark.asyncio
async def test_the_trial_never_rises_above_the_captured_baseline(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _endpoint = _producer(tmp_path, _sessions(_happy(45), sent))

    await producer.execute("pro_common_flow_high")

    record = journal.load("pro_common_flow_high")
    values = [
        json.loads(journal.load_blob(action.expected_readback_sha256))["Flow"]
        for action in record.actions
    ]
    assert values == [45, 44, 45]
    assert max(values) == 45


@pytest.mark.asyncio
async def test_a_baseline_at_the_app_minimum_has_no_downward_step(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _endpoint = _producer(tmp_path, _sessions(_happy(30), sent))

    with pytest.raises(QualificationProductionError, match="no downward step"):
        await producer.execute("pro_common_flow_floor")

    assert sent == []
    assert journal.load("pro_common_flow_floor") is None


@pytest.mark.asyncio
async def test_an_unacknowledged_send_keeps_the_evidence_and_issues_no_receipt(
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _endpoint = _producer(
        tmp_path, _sessions(_happy(), sent, send_error=RuntimeError("injected send fault"))
    )

    with pytest.raises(RuntimeError, match="injected send fault"):
        await producer.execute("pro_common_flow_fault")

    record = journal.load("pro_common_flow_fault")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert len(sent) == 1
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_generic_recovery_restores_the_captured_baseline(tmp_path: Path) -> None:
    sent: list[bytes] = []
    target = _binding()
    producer, journal, lease, endpoint = _producer(
        tmp_path,
        _sessions(_happy(), sent, send_error=RuntimeError("injected send fault")),
        target=target,
    )
    with pytest.raises(RuntimeError, match="injected send fault"):
        await producer.execute("pro_common_flow_recover")

    recovery: list[bytes] = []
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),
        journal=journal,
        lease=lease,
        writer_factory=_WriterFactory([_Session([_status(flow=39), _status(flow=40)], recovery)]),
    )
    recovered = await coordinator.recover("pro_common_flow_recover")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert not tuple((tmp_path / "receipts").iterdir())


@pytest.mark.asyncio
async def test_a_verified_replay_touches_no_device(tmp_path: Path) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _endpoint = _producer(tmp_path, _sessions(_happy(), sent))
    first = await producer.execute("pro_common_flow_replay")
    assert len(sent) == 3

    second = await producer.execute("pro_common_flow_replay")

    assert len(sent) == 3
    assert second.physical_write_count == 0
    assert second.receipt_count == 2
    assert second.journal_sha256 == first.journal_sha256


@pytest.mark.parametrize(
    "status",
    (
        _status(flow=46),
        _status(flow=81),
        _status(enabled=False),
        _status(timer_enabled=True),
        _status(linkage_code=1),
        _status(mode_code=0),
    ),
)
@pytest.mark.asyncio
async def test_the_existing_envelope_still_refuses_before_any_write(tmp_path, status) -> None:
    sent: list[bytes] = []
    producer, journal, _lease, _endpoint = _producer(tmp_path, _sessions([status] * 7, sent))

    with pytest.raises(QualificationProductionError):
        await producer.execute("pro_common_flow_bad")

    assert sent == []
    assert journal.load("pro_common_flow_bad") is None


def _intent(**overrides):
    arguments = {
        "operation_id": "pro_common_flow_0001",
        "target": _binding(),
        "field": "Flow",
        "baseline_value": 40,
        "trial_value": 39,
        "baseline_mode": "constant",
        "baseline_linkage": "independent",
        "baseline_flow": 40,
        "step_request_id": "q_step_" + "a" * 20,
        "restore_request_id": "q_restore_" + "b" * 18,
        "config_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "manifest_generation": "3" * 32,
        "min_power": 30,
        "max_power": 100,
        "app_version": "3.4.18",
        "app_catalog_sha256": "4" * 64,
        "template_sha256": "5" * 64,
        "schema_sha256": "6" * 64,
        "firmware_version": "pro-firmware",
        "created_at": NOW,
    }
    arguments.update(overrides)
    return ProCommonFlowQualificationIntent(**arguments)


def test_the_durable_intent_accepts_one_downward_step_from_the_captured_flow() -> None:
    intent = _intent()
    assert intent.function == "slave_common_flow" and intent.field == "Flow"


@pytest.mark.parametrize(
    "overrides",
    (
        {"trial_value": 41},
        {"trial_value": 38},
        {"baseline_flow": 41},
        {"baseline_value": 46, "trial_value": 45, "baseline_flow": 46},
        {"baseline_value": 20, "trial_value": 19, "baseline_flow": 20},
        {"baseline_value": 40, "trial_value": 39, "max_power": 35},
    ),
)
def test_the_durable_intent_refuses_an_unowned_or_unbounded_step(overrides) -> None:
    with pytest.raises(ValueError):
        _intent(**overrides)


@pytest.mark.parametrize("value", (46, 20, 120, True, 40.0, "40"))
def test_planned_outputs_stay_inside_the_app_config_and_trial_limits(value) -> None:
    with pytest.raises(ValueError):
        require_bounded_common_flow(value, product_key=PRO, minimum=30, maximum=100)


@pytest.mark.asyncio
async def test_an_acknowledged_step_with_a_failed_readback_stays_uncertain_and_recovers(
    tmp_path: Path,
) -> None:
    """The step is acknowledged but its readback faults, so the phase must stay UNCERTAIN.

    The acknowledgement is evidence the device may have taken the write, so it is kept, no
    receipt is issued, and generic recovery returns the device to its own captured Flow without
    ever resending the trial value.
    """

    sent: list[bytes] = []
    target = _binding()
    statuses = [
        _status(flow=40),  # baseline read
        _status(flow=40),  # same-frame readback
        _status(flow=40),  # fresh read before the step
        LocalDeviceWriteError("device readback failed after the acknowledgement"),
    ]
    producer, journal, lease, endpoint = _producer(
        tmp_path, _sessions(statuses, sent), target=target
    )

    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("pro_common_flow_uncertain")

    record = journal.load("pro_common_flow_uncertain")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert tuple(action.phase for action in record.actions) == (
        ActionPhase.VERIFIED,
        ActionPhase.UNCERTAIN,
        ActionPhase.PREPARED,
    )
    step = record.actions[1]
    assert step.acknowledgement_sha256 is not None
    assert journal.load_blob(step.acknowledgement_sha256) == b"acknowledged"
    assert len(sent) == 2
    assert not tuple((tmp_path / "receipts").iterdir())

    schema = get_product_schema(PRO)
    trial_forward = bytes(build_control_payload(schema, {"Flow": 39}))
    own_flow = bytes(build_control_payload(schema, {"Flow": 40}))
    assert sent[1] == trial_forward

    recovery: list[bytes] = []
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver(endpoint),
        journal=journal,
        lease=lease,
        writer_factory=_WriterFactory(
            [
                _Session([_status(flow=39), _status(flow=40)], recovery),
                _Session([_status(flow=40)], recovery),
            ]
        ),
    )
    recovered = await coordinator.recover("pro_common_flow_uncertain")

    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovery == [own_flow]
    assert trial_forward not in recovery
    assert not tuple((tmp_path / "receipts").iterdir())
