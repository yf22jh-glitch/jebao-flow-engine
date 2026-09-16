"""Clock evidence/replay tests using queued raw responses, not firmware simulation."""

from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.clock_qualification import DcClockQualificationProducer
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.local_control.journal import ActionPhase, LocalControlJournalStore, TransactionPhase
from jebao_flow.local_control.qualification import (
    load_current_qualifications,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import (
    QualificationArtifactStore,
    QualificationProductionError,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import DC_PUMP_PRO
from tests.unit.test_local_control_bar_timer_qualification import _Session
from tests.unit.test_local_control_dc_prepared_qualification import (
    _binding,
    _epoch,
    _Lease,
    _Resolver,
)
from tests.unit.test_local_control_qualification_producer import _dc_status


def _raw_clock(value):
    raw = bytearray(_dc_status())
    for name, data in (
        ("YMDData", [value.year // 100, value.year % 100, value.month, value.day]),
        ("HMSData", [0, value.hour, value.minute, value.second]),
    ):
        offset = DC_PUMP_PRO.by_name(name).position.byte_offset
        raw[offset : offset + 4] = bytes(data)
    return bytes(raw)


def _producer(tmp_path, *, fail_read=False):
    target = _binding()
    endpoint = ResolvedEndpoint(target, "test-address", wifi_firmware_version="test-wifi")
    journal = LocalControlJournalStore.for_test(tmp_path / "journal")
    sent = []
    writers = []

    def factory(endpoint):
        now = datetime.now(UTC)
        after = RuntimeError("injected readback fault") if fail_read else _raw_clock(now)
        session = _Session([_dc_status(), after], sent)
        writer = ConnectedDeviceWriter(
            endpoint, session_factory=lambda _: session, device_local_time=lambda: now
        )
        writers.append(writer)
        return writer

    producer = DcClockQualificationProducer(
        epoch_provider=lambda: _epoch(target),
        resolver_factory=lambda: _Resolver(endpoint),
        journal=journal,
        lease=_Lease(),
        writer_factory=factory,
        artifacts=QualificationArtifactStore.for_test(
            tmp_path / "receipts", tmp_path / "out" / "clock.json"
        ),
    )
    return producer, journal, sent, writers


async def test_two_explicit_clock_commands_issue_only_clock_receipts_and_replay_has_no_io(tmp_path):
    producer, journal, sent, writers = _producer(tmp_path)
    result = await producer.execute("dc_clock_0001")
    assert result.phase is TransactionPhase.VERIFIED
    assert result.receipt_count == 2 and result.physical_write_count == len(sent) == 2
    record = journal.load("dc_clock_0001")
    assert len(record.actions) == 2
    assert all(a.recovery_policy == "observe_clock" for a in record.actions)
    (binding,) = load_current_qualifications(
        tmp_path / "out" / "clock.json", _allow_ephemeral_for_test=True
    )
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    assert len(receipts) == 2
    assert {r.verification_kind for r in receipts} == {"advancing_clock"}
    assert {r.function for r in receipts} == {"device_clock_sync"}
    assert len({r.request_id for r in receipts}) == 2
    replay = await producer.execute("dc_clock_0001")
    assert replay.physical_write_count == 0 and len(writers) == len(sent) == 2


async def test_clock_ack_with_failed_readback_is_uncertain_and_never_issues_receipts(tmp_path):
    producer, journal, sent, writers = _producer(tmp_path, fail_read=True)
    with pytest.raises(RuntimeError, match="injected readback"):
        await producer.execute("dc_clock_fault")
    record = journal.load("dc_clock_fault")
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert record.actions[0].phase is ActionPhase.UNCERTAIN
    assert record.actions[0].acknowledgement_sha256 is not None
    assert len(sent) == 1
    assert not (tmp_path / "out" / "clock.json").exists()
    assert list((tmp_path / "receipts").glob("*.json")) == []
    with pytest.raises(QualificationProductionError):
        await producer.execute("dc_clock_fault")
    assert len(sent) == len(writers) == 1


async def test_clock_finalize_rechecks_preserved_raw_instead_of_trusting_verified_label(tmp_path):
    producer, journal, _, _ = _producer(tmp_path)
    await producer.execute("dc_clock_evidence")
    record = journal.load("dc_clock_evidence")
    import json

    from jebao_flow.local_control.clock_qualification import _OBSERVED

    action = record.actions[0]
    evidence = json.loads(journal.load_blob(action.diagnostic[len(_OBSERVED) :]))
    evidence["status_sha256"] = journal.install_blob(bytes(len(_dc_status())))
    digest = journal.install_blob(json.dumps(evidence).encode())
    bad_action = action.model_copy(update={"diagnostic": _OBSERVED + digest})
    replacement = record.evolve(actions=(bad_action, record.actions[1]))
    journal.save(record, replacement)
    with pytest.raises(QualificationProductionError, match="advancing time"):
        await producer.execute("dc_clock_evidence")
