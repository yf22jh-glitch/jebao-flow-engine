import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import (
    LocalControlCoordinator,
    LocalControlExecutionError,
)
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.models import (
    InstallContentBindings,
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan, FreshSlaveSchedule
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode, SlaveFlowOverride
from jebao_flow.protocol.errors import ProtocolTimeoutError
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
from jebao_flow.protocol.session import RawStateCapture


def _binding(member: str, identity: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=identity * 64,
        logical_role=member,
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="0" * 64,
        template_sha256="1" * 64,
        schema_sha256=current_protocol_schema_sha256(),
        firmware_version="wifi-current",
        receipt_sha256=("2" * 64, "3" * 64),
    )


def _status(
    *,
    enabled: bool = False,
    flow: int = 40,
    schedule: bytes | None = None,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = int(enabled)
    raw[2] = flow
    if schedule is not None:
        start = LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
        raw[start : start + len(schedule)] = schedule
    return bytes(raw)


class _Resolver:
    def __init__(self, endpoints: tuple[ResolvedEndpoint, ...]) -> None:
        self.endpoints = endpoints
        self.calls = 0

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        self.calls += 1
        return next(endpoint for endpoint in self.endpoints if endpoint.target == target)

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        self.calls += 1
        return tuple(
            next(endpoint for endpoint in self.endpoints if endpoint.target == target)
            for target in targets
        )


class _Session:
    def __init__(
        self,
        statuses: list[bytes],
        *,
        acknowledgement: bytes = b"ack",
        send_error: BaseException | None = None,
    ) -> None:
        self.statuses = statuses
        self.acknowledgement = acknowledgement
        self.send_error = send_error
        self.sent: list[bytes] = []
        self.quarantined = False

    async def connect(self) -> None:
        pass

    async def authenticate(self) -> bytes:
        return b"private"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
        return self.statuses.pop(0)

    async def read_raw_state_capture(self, *, accept_reports: bool = False) -> RawStateCapture:
        assert accept_reports is True
        status = self.statuses.pop(0)
        return RawStateCapture(
            wire_frame=b"captured-raw-" + status, action=4, status_payload=status
        )

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.sent.append(bytes(control_payload))
        if self.send_error is not None:
            raise self.send_error
        return self.acknowledgement

    async def disconnect(self) -> None:
        pass

    def quarantine(self) -> None:
        self.quarantined = True


class _SessionFactory:
    def __init__(self, sessions: dict[str, list[_Session]]) -> None:
        self.sessions = sessions

    def __call__(self, address: str) -> _Session:
        return self.sessions[address].pop(0)


def _lease(tmp_path: Path) -> LocalControlLease:
    return LocalControlLease(
        guard=ExactRestoreGuard._for_test(
            operation_lock_path=tmp_path / "hardware-operation.lock",
            latch_path=tmp_path / "emergency-stop.latch",
        ),
        device_lock_directory=tmp_path / "physical-locks",
        legacy_workflows_idle_probe=lambda: True,
    )


def _uncertain_slave_flow(tmp_path: Path, *, current_flow: int):
    target = _binding("wavemaker_slave", "a")
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    session = _Session([_status(flow=current_flow)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [session]}),
    )
    writer = ConnectedDeviceWriter(endpoint, control_flow_bounds=(30, 100))
    intent = LocalControlIntent(
        request_id="uncertain_slave_flow_01",
        target=target,
        function="slave_common_flow",
        value=30,
    )
    prepared = writer.prepare_scalar(intent, _status(flow=37))
    record = coordinator._create_record(
        request_id=intent.request_id,
        intent_sha256="f" * 64,
        prepared=(prepared,),
    )
    uncertain = record.evolve(
        phase=TransactionPhase.RECOVERY_REQUIRED,
        actions=(record.actions[0].model_copy(update={"phase": ActionPhase.UNCERTAIN}),),
    )
    journal.save(record, uncertain)
    return coordinator, journal, uncertain, session


@pytest.mark.asyncio
async def test_operator_can_abandon_one_uncertain_slave_flow_after_other_value_is_observed(
    tmp_path: Path,
) -> None:
    coordinator, journal, uncertain, session = _uncertain_slave_flow(tmp_path, current_flow=38)

    result = await coordinator.abandon_uncertain_slave_flow(
        uncertain.request_id,
        observed_flow=38,
        operator_confirmed_not_applied=True,
    )

    abandoned = journal.load(uncertain.request_id)
    assert abandoned is not None
    assert result.phase is TransactionPhase.ABANDONED
    assert result.physical_write_count == 0
    assert result.accepted is False
    assert session.sent == []
    assert journal.unfinished_for((uncertain.actions[0].target.physical_identity_key,)) == ()
    assert abandoned.actions[0].phase is ActionPhase.ABANDONED
    assert abandoned.actions[0].diagnostic == "operator_adopted_current_flow"
    assert journal.load_blob(abandoned.actions[0].external_observation_sha256 or "")
    prior = json.loads(journal.load_blob(abandoned.abandoned_prior_record_sha256 or ""))
    assert prior["phase"] == "recovery_required"
    assert prior["actions"][0]["phase"] == "uncertain"
    with pytest.raises(LocalControlExecutionError, match="must not be rolled back"):
        await coordinator.recover(uncertain.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_flow", [30, 37])
async def test_uncertain_flow_matching_forward_or_baseline_cannot_be_abandoned(
    tmp_path: Path, current_flow: int
) -> None:
    coordinator, journal, uncertain, session = _uncertain_slave_flow(
        tmp_path, current_flow=current_flow
    )

    with pytest.raises(LocalControlExecutionError, match="current Flow must differ"):
        await coordinator.abandon_uncertain_slave_flow(
            uncertain.request_id,
            observed_flow=current_flow,
            operator_confirmed_not_applied=True,
        )

    assert session.sent == []
    assert journal.load(uncertain.request_id) == uncertain


@pytest.mark.asyncio
async def test_uncertain_flow_requires_operator_confirmation_and_exact_value(
    tmp_path: Path,
) -> None:
    coordinator, journal, uncertain, session = _uncertain_slave_flow(tmp_path, current_flow=38)

    with pytest.raises(ValueError, match="operator confirmation"):
        await coordinator.abandon_uncertain_slave_flow(
            uncertain.request_id,
            observed_flow=38,
            operator_confirmed_not_applied=False,
        )
    with pytest.raises(LocalControlExecutionError, match="fresh Flow"):
        await coordinator.abandon_uncertain_slave_flow(
            uncertain.request_id,
            observed_flow=39,
            operator_confirmed_not_applied=True,
        )

    assert session.sent == []
    assert journal.load(uncertain.request_id) == uncertain


def _coordinator(
    tmp_path: Path,
    *,
    qualifications: tuple[QualificationBinding, ...],
    resolver: _Resolver,
    sessions: _SessionFactory,
    catalog_qualified_keys: frozenset[str] | None = None,
    runtime_qualified_keys: frozenset[str] | None = None,
    identity_control_authorizations: dict[tuple[str, str], frozenset[str]] | None = None,
    runtime_qualification_verifier=None,
) -> tuple[LocalControlCoordinator, LocalControlJournalStore]:
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    default_keys = frozenset(
        endpoint.target.physical_identity_key for endpoint in resolver.endpoints
    )
    catalog_keys = default_keys if catalog_qualified_keys is None else catalog_qualified_keys
    runtime_keys = default_keys if runtime_qualified_keys is None else runtime_qualified_keys
    return (
        LocalControlCoordinator(
            catalog=build_local_control_catalog(
                qualifications,
                current_content=InstallContentBindings(
                    app_catalog_sha256="0" * 64,
                    protocol_schema_sha256=current_protocol_schema_sha256(),
                    template_sha256=("1" * 64,),
                ),
                identity_control_authorizations=identity_control_authorizations,
                _qualification_verifier_for_test=lambda _item: catalog_keys,
            ),
            resolver=resolver,  # type: ignore[arg-type]
            journal=journal,
            lease=_lease(tmp_path),
            session_factory=sessions,
            qualification_verifier=(
                runtime_qualification_verifier
                if runtime_qualification_verifier is not None
                else lambda _item: runtime_keys
            ),
        ),
        journal,
    )


@pytest.mark.asyncio
async def test_exact_identity_authority_executes_without_legacy_artifacts(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    session = _Session([_status(flow=40), _status(flow=41)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [session]}),
        identity_control_authorizations={
            (LOCAL_WAVEMAKER_PRO.product_key, "manual_flow"): frozenset(
                {target.physical_identity_key}
            )
        },
        runtime_qualification_verifier=lambda _item: (_ for _ in ()).throw(
            AssertionError("legacy authority was consulted")
        ),
    )
    intent = LocalControlIntent(
        request_id="identity_request_0001",
        target=target,
        function="manual_flow",
        value=41,
    )

    result = await coordinator.execute(intent)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 1
    assert len(session.sent) == 1
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.actions[0].target.physical_identity_key == target.physical_identity_key


@pytest.mark.asyncio
async def test_identity_authority_never_applies_to_another_physical_device(
    tmp_path: Path,
) -> None:
    selected = _binding("selected", "a")
    other = _binding("other", "b")
    endpoint = ResolvedEndpoint(other, "other-current", wifi_firmware_version="wifi-current")
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"other-current": []}),
        identity_control_authorizations={
            (LOCAL_WAVEMAKER_PRO.product_key, "manual_flow"): frozenset(
                {selected.physical_identity_key}
            )
        },
    )
    intent = LocalControlIntent(
        request_id="identity_wrong_device_0001",
        target=other,
        function="manual_flow",
        value=41,
    )

    with pytest.raises(ValueError, match="not qualified"):
        await coordinator.execute(intent)

    assert journal.load(intent.request_id) is None


@pytest.mark.asyncio
async def test_scalar_transaction_is_journal_first_verified_and_idempotent(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    session = _Session([_status(flow=40), _status(flow=50)])
    resolver = _Resolver((endpoint,))
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=resolver,
        sessions=_SessionFactory({"current-address": [session]}),
    )
    intent = LocalControlIntent(
        request_id="request_0001",
        target=target,
        function="manual_flow",
        value=50,
    )

    first = await coordinator.execute(intent)
    duplicate = await coordinator.execute(intent)

    assert first.phase is TransactionPhase.VERIFIED
    assert first.physical_write_count == 1
    assert duplicate.phase is TransactionPhase.VERIFIED
    assert duplicate.physical_write_count == 0
    assert resolver.calls == 1
    assert len(session.sent) == 1
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.actions[0].phase is ActionPhase.VERIFIED
    assert record.actions[0].restore_on_recovery is True


@pytest.mark.asyncio
async def test_schedule_reset_is_one_journaled_full_image_action(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    baseline = bytes(range(9)) + bytes([0xEE]) * (432 - 9)
    reset = bytes([0xEE]) * 432
    session = _Session([_status(schedule=baseline), _status(schedule=reset)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("schedule_reset"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [session]}),
    )
    intent = LocalControlIntent(
        request_id="schedule_reset_0001",
        target=target,
        function="schedule_reset",
    )

    result = await coordinator.execute(intent)

    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 1
    assert len(session.sent) == 1
    record = journal.load(intent.request_id)
    assert record is not None
    assert len(record.actions) == 1
    assert record.actions[0].function == "schedule_reset"
    assert record.actions[0].phase is ActionPhase.VERIFIED


@pytest.mark.asyncio
async def test_uncertain_forward_is_never_retried_and_can_restore_fresh_baseline(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    failed = _Session([_status(flow=40)], send_error=TimeoutError("ack lost"))
    recovery = _Session([_status(flow=50), _status(flow=40)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [failed, recovery]}),
    )
    intent = LocalControlIntent(
        request_id="request_0002",
        target=target,
        function="manual_flow",
        value=50,
    )

    with pytest.raises(LocalControlExecutionError, match="will not be retried"):
        await coordinator.execute(intent)
    uncertain = journal.load(intent.request_id)
    assert uncertain is not None
    assert uncertain.phase is TransactionPhase.RECOVERY_REQUIRED
    assert uncertain.actions[0].phase is ActionPhase.UNCERTAIN
    assert len(failed.sent) == 1
    event = json.loads(caplog.records[-1].getMessage())
    assert caplog.records[-1].exc_info is None
    assert caplog.records[-1].levelname == "ERROR"
    assert event["function"] == "manual_flow"
    assert event["diagnostic"] == "forward_outcome_uncertain"
    assert event["revision"] == uncertain.revision == 2
    assert event["action_index"] == 0
    assert event["request_sha256"] == hashlib.sha256(intent.request_id.encode("ascii")).hexdigest()
    assert event["exception_chain"][0]["type"] == "builtins.TimeoutError"
    assert any(frame["function"] == "send_once" for frame in event["exception_chain"][0]["frames"])
    assert "ack lost" not in caplog.text

    restored = await coordinator.recover(intent.request_id)

    assert restored.phase is TransactionPhase.ROLLED_BACK
    assert restored.physical_write_count == 1
    assert len(failed.sent) == 1
    assert len(recovery.sent) == 1


@pytest.mark.parametrize(
    "error_kind",
    ["chained", "cancelled", "broken_logger", "cyclic", "long_chain", "broken_clock"],
)
@pytest.mark.asyncio
async def test_failure_diagnostics_preserve_cause_and_journal_before_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_kind: str,
) -> None:
    # Code fault injection after D1u's missing exception evidence; no firmware model.
    from jebao_flow.local_control import failure_diagnostics

    ticks = iter((1_000_000_000, 1_083_564_000))

    def clock() -> int:
        if error_kind == "broken_clock":
            raise RuntimeError("clock unavailable")
        return next(ticks)

    monkeypatch.setattr(failure_diagnostics.time, "monotonic_ns", clock)
    private_value = "secret-passcode-and-endpoint"
    original: BaseException
    if error_kind == "cancelled":
        original = asyncio.CancelledError(private_value)
    else:
        original = ProtocolTimeoutError(private_value)
        original.__cause__ = TimeoutError(private_value)
        if error_kind == "cyclic":
            original.__cause__ = original
        elif error_kind == "long_chain":
            tail = original
            for _ in range(12):
                tail.__cause__ = TimeoutError(private_value)
                tail = tail.__cause__
    target = _binding("private_member", "a")
    endpoint = ResolvedEndpoint(target, "private-endpoint", wifi_firmware_version="wifi-current")
    session = _Session([_status(flow=40)], send_error=original)
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"private-endpoint": [session]}),
    )
    intent = LocalControlIntent(
        request_id="private_request", target=target, function="manual_flow", value=50
    )
    events: list[dict] = []

    def log(format_string: str, encoded: str) -> None:
        stored = journal.load(intent.request_id)
        assert stored is not None
        assert stored.revision == 2
        assert stored.phase is TransactionPhase.RECOVERY_REQUIRED
        assert stored.actions[0].phase is ActionPhase.UNCERTAIN
        events.append(json.loads(encoded))
        for secret in (private_value, "private_member", "private-endpoint", "private_request"):
            assert secret not in encoded
        if error_kind == "broken_logger":
            raise RuntimeError("diagnostic sink unavailable")

    monkeypatch.setattr(failure_diagnostics._LOGGER, "error", log)
    with pytest.raises(LocalControlExecutionError) as raised:
        await coordinator.execute(intent)
    assert raised.value.__cause__ is original
    assert len(session.sent) == 1
    assert session.quarantined
    assert len(events) == 1
    assert events[0]["event_version"] == 1
    assert events[0]["request_sha256"] == hashlib.sha256(b"private_request").hexdigest()
    assert events[0]["boundary_elapsed_ns"] == (
        None if error_kind == "broken_clock" else 83_564_000
    )
    encoded = json.dumps(events[0])
    for secret in (private_value, "private_member", "private-endpoint", "private_request"):
        assert secret not in encoded
    chain = events[0]["exception_chain"]
    if error_kind == "cancelled":
        assert chain[0]["type"] == "asyncio.exceptions.CancelledError"
    elif error_kind == "cyclic":
        assert len(chain) == 1
    else:
        assert chain[0]["type"] == "jebao_flow.protocol.errors.ProtocolTimeoutError"
        assert chain[1]["type"] == "builtins.TimeoutError"
    assert events[0]["chain_truncated_or_cyclic"] is (error_kind in {"cyclic", "long_chain"})
    if error_kind == "long_chain":
        assert len(chain) == 8


@pytest.mark.parametrize("failure_stage", ["readback", "recovery_send", "recovery_readback"])
@pytest.mark.asyncio
async def test_failure_diagnostics_do_not_mislabel_readback_as_send(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failure_stage: str,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(target, "current-address", wifi_firmware_version="wifi-current")
    forward = _Session(
        [_status(flow=40)],
        send_error=None if failure_stage == "readback" else TimeoutError("private cause"),
    )
    recovery = _Session(
        [_status(flow=50)],
        send_error=OSError("private cause") if failure_stage == "recovery_send" else None,
    )
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [forward, recovery]}),
    )
    intent = LocalControlIntent(
        request_id="request_diagnostic", target=target, function="manual_flow", value=50
    )
    with pytest.raises(LocalControlExecutionError):
        await coordinator.execute(intent)
    if failure_stage != "readback":
        with pytest.raises(LocalControlExecutionError):
            await coordinator.recover(intent.request_id)
    event = json.loads(caplog.records[-1].getMessage())
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert event["revision"] == record.revision
    assert isinstance(event["boundary_elapsed_ns"], int)
    assert event["boundary_elapsed_ns"] >= 0
    assert event["diagnostic"] == (
        "forward_readback_unavailable"
        if failure_stage == "readback"
        else "rollback_outcome_uncertain"
    )
    assert event["exception_chain"][0]["type"] == (
        "builtins.OSError" if failure_stage == "recovery_send" else "builtins.IndexError"
    )
    assert len(forward.sent) == 1
    assert len(recovery.sent) == (0 if failure_stage == "readback" else 1)
    assert "private cause" not in caplog.text


@pytest.mark.parametrize("root_level", ["default", "ERROR"])
def test_failure_event_reaches_stderr_without_logging_configuration(root_level: str) -> None:
    # Separate interpreter: pytest's capture handler must not supply the delivery route.
    script = """
import logging
import sys
from types import SimpleNamespace
from jebao_flow.local_control.failure_diagnostics import report_action_failure
assert not logging.getLogger().handlers
if sys.argv[1] != "default":
    logging.getLogger().setLevel(sys.argv[1])
action = SimpleNamespace(
    function="manual_flow", diagnostic="forward_outcome_uncertain",
    forward_payload_sha256="a" * 64, rollback_payload_sha256="b" * 64,
)
record = SimpleNamespace(request_id="private_request", revision=2, actions=(action,))
report_action_failure(
    record, 0, TimeoutError("secret-message"), started_ns=100, failed_ns=200,
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, root_level],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1
    event = json.loads(result.stderr)
    assert event["event_version"] == 1
    assert event["function"] == "manual_flow"
    assert event["boundary_elapsed_ns"] == 100
    assert event["exception_chain"][0]["type"] == "builtins.TimeoutError"
    assert "secret-message" not in result.stderr
    assert "private_request" not in result.stderr


@pytest.mark.parametrize(
    ("recovery_enabled", "diagnostic"),
    (
        (False, "safe_state_retained"),
        (True, "baseline_already_present"),
    ),
)
@pytest.mark.asyncio
async def test_power_off_recovery_classifies_fresh_state_without_reenabling(
    tmp_path: Path,
    recovery_enabled: bool,
    diagnostic: str,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    forward = _Session(
        [_status(enabled=True)],
        send_error=TimeoutError("ack lost"),
    )
    recovery = _Session([_status(enabled=recovery_enabled)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("power"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [forward, recovery]}),
    )
    intent = LocalControlIntent(
        request_id="power_off_0001",
        target=target,
        function="power",
        value=False,
    )

    with pytest.raises(LocalControlExecutionError, match="will not be retried"):
        await coordinator.execute(intent)
    restored = await coordinator.recover(intent.request_id)

    assert restored.phase is TransactionPhase.ROLLED_BACK
    assert restored.physical_write_count == 0
    assert len(forward.sent) == 1
    assert recovery.sent == []
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.actions[0].restore_on_recovery is False
    assert record.actions[0].phase is ActionPhase.ROLLED_BACK
    assert record.actions[0].diagnostic == diagnostic


@pytest.mark.asyncio
async def test_power_off_recovery_keeps_unreadable_state_nonterminal_without_a_send(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    forward = _Session(
        [_status(enabled=True)],
        send_error=TimeoutError("ack lost"),
    )
    recovery = _Session([b""])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("power"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [forward, recovery]}),
    )
    intent = LocalControlIntent(
        request_id="power_off_0002",
        target=target,
        function="power",
        value=False,
    )
    with pytest.raises(LocalControlExecutionError, match="will not be retried"):
        await coordinator.execute(intent)

    with pytest.raises(LocalControlExecutionError, match="no frame was sent"):
        await coordinator.recover(intent.request_id)

    assert recovery.sent == []
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert record.actions[0].diagnostic == "safe_state_readback_unavailable"


@pytest.mark.asyncio
async def test_verified_power_off_is_terminal_and_cannot_be_recovered(
    tmp_path: Path,
) -> None:
    target = _binding("wavemaker_left", "a")
    endpoint = ResolvedEndpoint(
        target,
        "current-address",
        wifi_firmware_version="wifi-current",
    )
    session = _Session([_status(enabled=True), _status(enabled=False)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("power"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"current-address": [session]}),
    )
    intent = LocalControlIntent(
        request_id="power_off_0003",
        target=target,
        function="power",
        value=False,
    )
    applied = await coordinator.execute(intent)

    with pytest.raises(LocalControlExecutionError, match="terminal"):
        await coordinator.recover(intent.request_id)

    assert applied.phase is TransactionPhase.VERIFIED
    assert len(session.sent) == 1
    record = journal.load(intent.request_id)
    assert record is not None
    assert record.phase is TransactionPhase.VERIFIED
    assert record.actions[0].phase is ActionPhase.VERIFIED


@pytest.mark.asyncio
async def test_priority_schedule_reads_current_master_then_writes_each_slave_image(
    tmp_path: Path,
) -> None:
    master = _binding("master", "a")
    slave = _binding("slave", "b")
    master_endpoint = ResolvedEndpoint(
        master,
        "master-current",
        wifi_firmware_version="wifi-current",
    )
    slave_endpoint = ResolvedEndpoint(
        slave,
        "slave-current",
        wifi_firmware_version="wifi-current",
    )
    master_slot = bytes((0, 0, 1, 0, 2, 40, 20, 0, 10))
    master_image = master_slot + bytes([0xEE]) * (432 - 9)
    desired_slot = bytes((0, 0, 1, 0, 2, 55, 20, 0, 10))
    desired_image = desired_slot + bytes([0xEE]) * (432 - 9)
    baseline_image = bytes([0xEE]) * 432
    master_session = _Session([_status(schedule=master_image)])
    slave_session = _Session([_status(schedule=baseline_image), _status(schedule=desired_image)])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("slave_per_slot_flow_extension"),),
        resolver=_Resolver((master_endpoint, slave_endpoint)),
        sessions=_SessionFactory(
            {
                "master-current": [master_session],
                "slave-current": [slave_session],
            }
        ),
        catalog_qualified_keys=frozenset({"b" * 64}),
        runtime_qualified_keys=frozenset({"b" * 64}),
    )
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slaves=(
            FreshSlaveSchedule(
                target=slave,
                flow_overrides=(SlaveFlowOverride(slot_index=0, flow=55),),
            ),
        ),
    )

    result = await coordinator.execute_schedule("schedule_0001", plan)

    assert result.phase is TransactionPhase.VERIFIED
    assert len(slave_session.sent) == 1
    assert master_session.sent == []
    record = journal.load("schedule_0001")
    assert record is not None
    assert record.actions[0].function == "slave_per_slot_flow_extension"
    assert record.actions[0].target.physical_identity_key == "b" * 64


@pytest.mark.asyncio
async def test_single_slot_flow_command_rederives_other_values_from_current_devices(
    tmp_path: Path,
) -> None:
    master = _binding("master", "a")
    slave = _binding("slave", "b")
    master_endpoint = ResolvedEndpoint(
        master,
        "master-current",
        wifi_firmware_version="wifi-current",
    )
    slave_endpoint = ResolvedEndpoint(
        slave,
        "slave-current",
        wifi_firmware_version="wifi-current",
    )
    master_image = (
        bytes((0, 0, 1, 0, 2, 40, 20, 0, 10))
        + bytes((1, 0, 2, 0, 2, 45, 20, 0, 10))
        + bytes([0xEE]) * (432 - 18)
    )
    slave_image = (
        bytes((0, 0, 1, 0, 2, 50, 9, 9, 9))
        + bytes((1, 0, 2, 0, 2, 60, 9, 9, 9))
        + bytes([0xEE]) * (432 - 18)
    )
    desired = (
        bytes((0, 0, 1, 0, 2, 50, 20, 0, 10))
        + bytes((1, 0, 2, 0, 2, 55, 20, 0, 10))
        + bytes([0xEE]) * (432 - 18)
    )
    master_session = _Session([_status(schedule=master_image)])
    slave_session = _Session([_status(schedule=slave_image), _status(schedule=desired)])
    coordinator, _journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("slave_per_slot_flow_extension"),),
        resolver=_Resolver((master_endpoint, slave_endpoint)),
        sessions=_SessionFactory(
            {
                "master-current": [master_session],
                "slave-current": [slave_session],
            }
        ),
        catalog_qualified_keys=frozenset({"b" * 64}),
        runtime_qualified_keys=frozenset({"b" * 64}),
    )

    result = await coordinator.execute_schedule_flow(
        request_id="slot_flow_0001",
        linkage_mode=NativeLinkageMode.ASYNC,
        master=master,
        slave=slave,
        slot_index=1,
        flow=55,
    )

    assert result.accepted is True
    assert master_session.sent == []
    assert len(slave_session.sent) == 1


@pytest.mark.asyncio
async def test_receipts_for_one_physical_device_never_authorize_another(
    tmp_path: Path,
) -> None:
    qualified_target = _binding("qualified", "a")
    other_target = _binding("other", "b")
    other_endpoint = ResolvedEndpoint(
        other_target,
        "other-current",
        wifi_firmware_version="wifi-current",
    )
    resolver = _Resolver((other_endpoint,))
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("manual_flow"),),
        resolver=resolver,
        sessions=_SessionFactory({"other-current": []}),
        catalog_qualified_keys=frozenset({other_target.physical_identity_key}),
        runtime_qualified_keys=frozenset({qualified_target.physical_identity_key}),
    )
    intent = LocalControlIntent(
        request_id="wrong_device_0001",
        target=other_target,
        function="manual_flow",
        value=50,
    )

    with pytest.raises(ValueError, match="physical device"):
        await coordinator.execute(intent)

    assert resolver.calls == 1
    assert journal.load(intent.request_id) is None
