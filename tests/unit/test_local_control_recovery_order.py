"""Ordering and ownership tests; no claims about simulated firmware behavior."""

from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlExecutionError
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import DC_PUMP_PRO
from tests.unit.test_local_control_bar_qualification import _binding
from tests.unit.test_local_control_coordinator import _lease, _Resolver, _Session, _SessionFactory
from tests.unit.test_local_control_dc_qualification import _status


def _fixture(tmp_path, sessions, *, started=True):
    target = _binding(product_key=DC_PUMP_PRO.product_key)
    endpoint = ResolvedEndpoint(target, "test-address")
    writer = ConnectedDeviceWriter(endpoint, control_flow_bounds=(30, 100))
    raw = _status(timer_enabled=True, enabled=True, flow=81)
    prepared = tuple(
        writer.prepare_scalar(
            LocalControlIntent(
                request_id="prepare_order_01", target=target, function=function, value=value
            ),
            raw,
        )
        for function, value in (("manual_flow", 40), ("schedule_enable", True))
    )
    store = LocalControlJournalStore.for_test(tmp_path / "journal")
    actions = tuple(
        JournalAction(
            action_id=f"action_{index:02}",
            target=target,
            function=item.function,
            forward_payload_sha256=store.install_blob(item.forward_payload),
            rollback_payload_sha256=store.install_blob(item.rollback_payload),
            baseline_status_sha256=store.install_blob(item.baseline_status),
            expected_readback_sha256=store.install_blob(item.expected_readback),
            baseline_readback_sha256=store.install_blob(item.baseline_readback),
            verification_kind=item.verification_kind,
        )
        for index, item in enumerate(prepared)
    )
    now = datetime.now(UTC)
    record = LocalControlJournalRecord(
        request_id="ordered_recover_01",
        intent_sha256=store.install_blob(b"owned-intent"),
        created_at=now,
        updated_at=now,
        actions=actions,
        recovery_order=(1, 0),
        recovery_prerequisites=(1,),
    )
    store.create(record)
    if started:
        replacement = record.evolve(
            phase=TransactionPhase.RECOVERY_REQUIRED,
            actions=(actions[0].model_copy(update={"phase": ActionPhase.UNCERTAIN}), actions[1]),
        )
        store.save(record, replacement)
        record = replacement
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=_Resolver((endpoint,)),
        journal=store,
        lease=_lease(tmp_path),
        session_factory=_SessionFactory({"test-address": sessions}),
    )
    return coordinator, store, record, prepared


async def test_prerequisite_is_restored_before_high_owned_flow_even_if_prepared(tmp_path):
    timer = _Session(
        [_status(timer_enabled=False, flow=40), _status(timer_enabled=True, enabled=True, flow=40)]
    )
    flow = _Session([_status(timer_enabled=True, flow=40), _status(timer_enabled=True, flow=81)])
    coordinator, store, record, prepared = _fixture(tmp_path, [timer, flow])
    result = await coordinator.recover(record.request_id)
    assert result.phase is TransactionPhase.ROLLED_BACK
    assert timer.sent == [prepared[1].rollback_payload]
    assert flow.sent == [prepared[0].rollback_payload]
    assert result.physical_write_count == 2
    assert store.load(record.request_id).recovery_order == (1, 0)


async def test_unverified_timer_restore_never_reaches_high_flow_and_is_not_resent(tmp_path):
    timer = _Session([_status(timer_enabled=False, flow=40)], send_error=TimeoutError())
    reread = _Session([_status(timer_enabled=False, flow=40)])
    coordinator, store, record, _ = _fixture(tmp_path, [timer, reread])
    with pytest.raises(LocalControlExecutionError, match="uncertain"):
        await coordinator.recover(record.request_id)
    assert store.load(record.request_id).actions[0].phase is ActionPhase.UNCERTAIN
    with pytest.raises(LocalControlExecutionError, match="not resent"):
        await coordinator.recover(record.request_id)
    assert len(timer.sent) == 1 and reread.sent == []


async def test_completed_prerequisite_is_not_sent_again_after_a_later_recovery_failure(tmp_path):
    timer = _Session(
        [_status(timer_enabled=False, flow=40), _status(timer_enabled=True, enabled=True, flow=40)]
    )
    flow = _Session([_status(timer_enabled=True, flow=40)], send_error=TimeoutError())
    changed_timer = _Session([_status(timer_enabled=False, flow=40)])
    coordinator, store, record, _ = _fixture(tmp_path, [timer, flow, changed_timer])
    with pytest.raises(LocalControlExecutionError, match="uncertain"):
        await coordinator.recover(record.request_id)
    assert store.load(record.request_id).actions[1].phase is ActionPhase.ROLLED_BACK
    # A later external change does not authorize replaying a completed restoration.
    with pytest.raises(LocalControlExecutionError, match="not resent"):
        await coordinator.recover(record.request_id)
    assert len(timer.sent) == 1 and changed_timer.sent == []


async def test_wholly_unstarted_operation_has_no_recovery_io(tmp_path):
    coordinator, _, record, _ = _fixture(tmp_path, [], started=False)
    assert (await coordinator.recover(record.request_id)).physical_write_count == 0


def test_legacy_record_serialization_has_no_new_default_fields(tmp_path):
    _, _, record, _ = _fixture(tmp_path, [], started=False)
    payload = record.model_dump(mode="json")
    payload.pop("recovery_order")
    payload.pop("recovery_prerequisites")
    loaded = LocalControlJournalRecord.model_validate(payload)
    assert loaded.model_dump(mode="json") == payload


@pytest.mark.parametrize("order,prerequisites", [((0, 0), ()), ((1,), ()), ((1, 0), (0,))])
def test_recovery_order_cannot_omit_repeat_or_bypass_prerequisites(tmp_path, order, prerequisites):
    _, _, record, _ = _fixture(tmp_path, [], started=False)
    with pytest.raises(ValueError):
        LocalControlJournalRecord.model_validate(
            {
                **record.model_dump(),
                "recovery_order": order,
                "recovery_prerequisites": prerequisites,
            }
        )


def test_unrelated_device_history_does_not_gate_the_current_device(tmp_path):
    coordinator, store, record, _ = _fixture(tmp_path, [])
    unrelated_key = "e" * 64
    assert store.unfinished_for((unrelated_key,)) == ()
    coordinator._require_no_other_unfinished("new_operation_01", (unrelated_key,))
    owned_key = record.actions[0].target.physical_identity_key
    with pytest.raises(LocalControlExecutionError, match="this physical device"):
        coordinator._require_no_other_unfinished("new_operation_01", (owned_key,))
