from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.journal import (
    ActionPhase,
    JournalAction,
    LocalControlJournalError,
    LocalControlJournalRecord,
    LocalControlJournalStore,
    TransactionPhase,
)
from jebao_flow.local_control.models import LocalDeviceBinding


def _binding() -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="pump_one",
        product_key="1" * 32,
        physical_identity_key="2" * 64,
        logical_role="return",
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _record(store: LocalControlJournalStore) -> LocalControlJournalRecord:
    payloads = (b"forward", b"rollback", b"status", b"expected", b"baseline")
    digests = [store.install_blob(value) for value in payloads]
    now = datetime(2026, 9, 8, tzinfo=UTC)
    return LocalControlJournalRecord(
        request_id="request_0001",
        intent_sha256="3" * 64,
        created_at=now,
        updated_at=now,
        actions=(
            JournalAction(
                action_id="action_00",
                target=_binding(),
                function="power",
                forward_payload_sha256=digests[0],
                rollback_payload_sha256=digests[1],
                baseline_status_sha256=digests[2],
                expected_readback_sha256=digests[3],
                baseline_readback_sha256=digests[4],
                verification_kind="scalar_fields",
            ),
        ),
    )


def test_blob_install_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = LocalControlJournalStore.for_test(tmp_path / "journal")

    first = store.install_blob(b"same")
    second = store.install_blob(b"same")

    assert first == second
    assert store.load_blob(first) == b"same"
    assert len(list(store.blobs.iterdir())) == 1


def test_record_create_and_consecutive_atomic_transition(tmp_path: Path) -> None:
    store = LocalControlJournalStore.for_test(tmp_path / "journal")
    record = _record(store)
    store.create(record)
    action = record.actions[0].model_copy(update={"phase": ActionPhase.VERIFIED})
    replacement = record.evolve(
        phase=TransactionPhase.VERIFIED,
        actions=(action,),
    )

    store.save(record, replacement)

    assert store.load(record.request_id) == replacement
    assert store.unfinished_records() == ()
    assert len(store.record_digest(replacement)) == 64
    with pytest.raises(LocalControlJournalError, match="changed"):
        store.save(record, replacement)


def test_unfinished_inventory_exposes_recovery_requirement(tmp_path: Path) -> None:
    store = LocalControlJournalStore.for_test(tmp_path / "journal")
    record = _record(store)
    store.create(record)

    assert store.unfinished_records() == (record,)

    (store.operations / "unexpected-residue").write_text("residue", encoding="ascii")
    with pytest.raises(LocalControlJournalError, match="residue"):
        store.unfinished_records()


def test_request_id_is_no_clobber_and_raw_record_has_no_runtime_endpoint(tmp_path: Path) -> None:
    store = LocalControlJournalStore.for_test(tmp_path / "journal")
    record = _record(store)
    store.create(record)

    with pytest.raises(LocalControlJournalError, match="already exists"):
        store.create(record)

    encoded = (store.operations / "request_0001.json").read_text(encoding="ascii")
    assert "address" not in encoded
    assert "hostname" not in encoded
    assert "pid" not in encoded


def test_missing_referenced_blob_blocks_record_creation(tmp_path: Path) -> None:
    store = LocalControlJournalStore.for_test(tmp_path / "journal")
    record = _record(store)
    broken = record.model_copy(
        update={
            "actions": (
                record.actions[0].model_copy(
                    update={"forward_payload_sha256": "f" * 64}
                ),
            )
        }
    )

    with pytest.raises(FileNotFoundError):
        store.create(broken)
