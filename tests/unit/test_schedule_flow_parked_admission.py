from __future__ import annotations

import json
import os
import socket
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jebao_flow import hardware_safety
from jebao_flow import schedule_flow_experiment_cli as cli
from jebao_flow.exact_restore import (
    ExactRestoreController,
    ExactRestoreErrorCode,
    ExactRestorePreflightError,
    prepare_exact_restore_record,
)
from jebao_flow.exact_restore_receipts import ExactRestoreReceiptArchive
from jebao_flow.hardware_guard import DeploymentHardwareGuard, HardwareOperationBusyError
from tests.unit.hardware_safety_support import install_test_hardware_safety_identity
from tests.unit.test_exact_restore import FakeGuard
from tests.unit.test_exact_restore_disposition import _case
from tests.unit.test_schedule_flow_experiment_cli import _intent, _legacy_intent


def _configured_guard(case, monkeypatch):
    # 실제 reader/archive를 사용합니다. fake firmware나 admission 성공 stub은 없습니다.
    monkeypatch.setattr(cli, "ExactRestoreJournalStore", lambda: case.journal)
    monkeypatch.setattr(
        cli,
        "ExactRestoreReceiptArchive",
        lambda: ExactRestoreReceiptArchive._for_test(case.safety_root),
    )
    return cli._ScheduleFlowHardwareGuard(
        operation_lock_path=case.safety_root / "hardware-operation.lock",
        latch_path=case.safety_root / "emergency-stop.latch",
        exact_restore_journal_path=case.journal.path,
    )


def _snapshot(root):
    return {
        p.name: p.read_bytes()
        for p in root.iterdir()
        if p.is_file() and not p.name.endswith(".lock")
    }


def test_verified_park_is_admitted_without_mutation_or_network(tmp_path, monkeypatch, capsys):
    case = _case(tmp_path)
    case.service.park(case.authority)
    guard = _configured_guard(case, monkeypatch)
    before = _snapshot(case.safety_root)

    def forbidden(*args, **kwargs):
        raise AssertionError("network construction reached")

    monkeypatch.setattr(socket, "socket", forbidden)
    with guard.lease():
        guard.clear()
        assert guard.permitted
        assert _snapshot(case.safety_root) == before
    assert not guard.permitted
    assert _snapshot(case.safety_root) == before
    diagnostic = json.loads(capsys.readouterr().err)
    assert "자격 미취득 상태 유지" in diagnostic["message"]
    assert diagnostic["qualified"] is False
    tombstone = case.journal.load()
    for field in ("disposition_sha256", "operation_sha256", "journal_snapshot_sha256"):
        assert diagnostic[field] == tombstone[field]
    assert case.record.operation_id not in json.dumps(diagnostic)
    assert (
        case.archive.load_operation_finalization(case.record.operation_id)["outcome"]
        == "parked_unqualified"
    )

    # 같은 파일에 대한 공용/다른 CLI의 기존 차단은 그대로입니다.
    legacy = DeploymentHardwareGuard(
        operation_lock_path=case.safety_root / "hardware-operation.lock",
        latch_path=case.safety_root / "emergency-stop.latch",
        exact_restore_journal_path=case.journal.path,
    )
    with pytest.raises(HardwareOperationBusyError):
        with legacy.lease():
            pytest.fail("legacy workflow admitted a tombstone")


def test_nonterminal_journal_remains_blocking(tmp_path, monkeypatch):
    case = _case(tmp_path)
    before = _snapshot(case.safety_root)
    with pytest.raises(HardwareOperationBusyError):
        with _configured_guard(case, monkeypatch).lease():
            pytest.fail("active restore was admitted")
    assert _snapshot(case.safety_root) == before


@pytest.mark.parametrize("component", ["finalized", "parked-disposition", "parked-journal"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_backing_blocks(tmp_path, monkeypatch, component, damage):
    case = _case(tmp_path)
    case.service.park(case.authority)
    path = next(case.safety_root.glob(f"exact-restore-{component}-*.json"))
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"{}\n")
    before = _snapshot(case.safety_root)
    with pytest.raises(HardwareOperationBusyError):
        with _configured_guard(case, monkeypatch).lease():
            pytest.fail("unverified archive was admitted")
    assert _snapshot(case.safety_root) == before


@pytest.mark.parametrize(
    "damage", ["phase", "digest", "truncated", "noncanonical", "mode", "hardlink", "symlink"]
)
def test_invalid_or_unsafe_tombstone_blocks(tmp_path, monkeypatch, damage):
    case = _case(tmp_path)
    case.service.park(case.authority)
    path = case.journal.path
    if damage in {"phase", "digest"}:
        payload = json.loads(path.read_text())
        payload["phase" if damage == "phase" else "disposition_sha256"] = (
            "running" if damage == "phase" else "0" * 64
        )
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    elif damage == "truncated":
        path.write_bytes(b"{")
    elif damage == "noncanonical":
        path.write_text(json.dumps(json.loads(path.read_text()), indent=2))
    elif damage == "mode":
        path.chmod(0o644)
    elif damage == "hardlink":
        os.link(path, case.safety_root / "alias")
    else:
        original = case.safety_root / "original"
        path.rename(original)
        path.symlink_to(original)
    before = _snapshot(case.safety_root)
    with pytest.raises(HardwareOperationBusyError):
        with _configured_guard(case, monkeypatch).lease():
            pytest.fail("unsafe journal was admitted")
    assert _snapshot(case.safety_root) == before


def test_verified_park_does_not_bypass_global_lock_or_latch(tmp_path, monkeypatch):
    case = _case(tmp_path)
    case.service.park(case.authority)
    owner = _configured_guard(case, monkeypatch)
    contender = _configured_guard(case, monkeypatch)
    with owner.lease():
        with pytest.raises(HardwareOperationBusyError):
            with contender.lease():
                pytest.fail("concurrent operation admitted")
        latch = case.safety_root / "emergency-stop.latch"
        latch.touch(mode=0o600)
        owner.clear()
        assert not owner.permitted


def test_absent_journal_does_not_require_archive(tmp_path, monkeypatch):
    case = _case(tmp_path)
    with case.journal.claim():
        case.journal.clear()
    guard = _configured_guard(case, monkeypatch)
    monkeypatch.setattr(
        cli, "ExactRestoreReceiptArchive", lambda: pytest.fail("archive read for absent journal")
    )
    with guard.lease():
        guard.clear()
        assert guard.permitted


def test_journal_change_during_confirmation_blocks(tmp_path, monkeypatch):
    case = _case(tmp_path)
    case.service.park(case.authority)
    guard = _configured_guard(case, monkeypatch)
    original = ExactRestoreReceiptArchive.confirm_parked_tombstone

    def change_after_confirm(archive, payload):
        result = original(archive, payload)
        case.journal.path.write_bytes(b"{}\n")
        return result

    monkeypatch.setattr(
        ExactRestoreReceiptArchive, "confirm_parked_tombstone", change_after_confirm
    )
    with pytest.raises(HardwareOperationBusyError):
        with guard.lease():
            pytest.fail("changed journal was admitted")


@pytest.mark.asyncio
async def test_admission_does_not_reauthorize_parked_operation(tmp_path, monkeypatch):
    case = _case(tmp_path)
    case.service.park(case.authority)
    before = _snapshot(case.safety_root)
    with _configured_guard(case, monkeypatch).lease():
        pass

    async def forbidden_observe(*args):
        pytest.fail("parked operation reached observation")

    def forbidden_resolve(*args):
        pytest.fail("parked operation reached a device")

    controller = ExactRestoreController(
        case.journal,
        FakeGuard(),
        observe=forbidden_observe,
        resolve_device=forbidden_resolve,
        qualification_receipts=case.archive,
    )
    prepared = prepare_exact_restore_record(
        case.record.baseline,
        case.record.safe_targets,
        operation_id=case.record.operation_id,
        now=case.record.created_at + timedelta(minutes=2),
    )
    with pytest.raises(ExactRestorePreflightError) as rejected:
        controller.create(prepared)
    assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    for operation in (controller.execute, controller.finalize):
        with pytest.raises(ExactRestorePreflightError) as rejected:
            await operation()
        assert rejected.value.code is ExactRestoreErrorCode.AUTHORITY
    assert _snapshot(case.safety_root) == before


@pytest.mark.asyncio
async def test_dispatch_status_uses_verified_park_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    case = _case(tmp_path)
    case.service.park(case.authority)
    install_test_hardware_safety_identity(case.safety_root, monkeypatch)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", case.safety_root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda p: p == case.safety_root)
    before = _snapshot(case.safety_root)

    # dispatch와 production constructor, reader/archive를 모두 실제로 실행합니다.
    select_guard = cli._schedule_flow_hardware_guard
    selected = []

    def select_under_intent_lease(intent):
        competing = cli.JsonHardwareTestIntentStore(cli.native_linkage_intent_path())
        with pytest.raises(cli.HardwareTestError, match="already running"):
            with competing.lease():
                pytest.fail("guard selected without the intent lease")
        selected.append(intent)
        return select_guard(intent)

    monkeypatch.setattr(cli, "_schedule_flow_hardware_guard", select_under_intent_lease)
    result = await cli.dispatch(SimpleNamespace(), SimpleNamespace(command="status"))
    assert result == 0
    assert selected == [None]
    output = capsys.readouterr()
    assert "자격 미취득 상태 유지" in output.err
    assert "One-shot intent: none" in output.out
    assert "Outer control journal: none" in output.out
    assert "Temporary schedule journal: none" in output.out
    assert "Role journal: none" in output.out
    assert _snapshot(case.safety_root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["status", "recover"])
async def test_dispatch_keeps_legacy_park_barrier(tmp_path, monkeypatch, command):
    case = _case(tmp_path)
    case.service.park(case.authority)
    install_test_hardware_safety_identity(case.safety_root, monkeypatch)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", case.safety_root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda p: p == case.safety_root)
    store = cli.JsonHardwareTestIntentStore(cli.native_linkage_intent_path())
    store.save(_legacy_intent())
    before = _snapshot(case.safety_root)
    with pytest.raises(HardwareOperationBusyError):
        await cli.dispatch(SimpleNamespace(), SimpleNamespace(command=command, confirm=None))
    assert _snapshot(case.safety_root) == before


def test_mismatched_journal_path_blocks(tmp_path, monkeypatch):
    case = _case(tmp_path)
    case.service.park(case.authority)
    guard = _configured_guard(case, monkeypatch)
    guard._exact_restore_journal_path = case.safety_root / "other.json"
    with pytest.raises(HardwareOperationBusyError):
        with guard.lease():
            pytest.fail("different journal was admitted")


def test_production_archive_rejects_different_root(tmp_path, monkeypatch):
    case = _case(tmp_path)
    case.service.park(case.authority)
    guard = _configured_guard(case, monkeypatch)
    # 생성자는 그대로 두고 객체 root만 오염시킵니다. 생산 코드의 기존 검사를 검증합니다.
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", case.safety_root)
    archive = ExactRestoreReceiptArchive()
    archive.root = tmp_path / "different-root"
    monkeypatch.setattr(cli, "ExactRestoreReceiptArchive", lambda: archive)
    with pytest.raises(HardwareOperationBusyError) as rejected:
        with guard.lease():
            pytest.fail("archive outside the fixed root was admitted")
    assert "left the fixed hardware-safety root" in str(rejected.value.__cause__)


def test_root_change_after_confirmation_blocks(tmp_path, monkeypatch):
    case = _case(tmp_path)
    case.service.park(case.authority)
    install_test_hardware_safety_identity(case.safety_root, monkeypatch)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", case.safety_root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda p: p == case.safety_root)
    original = ExactRestoreReceiptArchive.confirm_parked_tombstone
    detached = tmp_path / "detached-root"

    def move_after_confirm(archive, payload):
        result = original(archive, payload)
        case.safety_root.rename(detached)
        case.safety_root.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(ExactRestoreReceiptArchive, "confirm_parked_tombstone", move_after_confirm)
    with pytest.raises(HardwareOperationBusyError):
        with cli._ScheduleFlowHardwareGuard().lease():
            pytest.fail("replacement root was admitted")
    assert list(case.safety_root.iterdir()) == []
    assert (detached / case.journal.path.name).exists()


@pytest.mark.parametrize("kind", ["absent", "sync", "legacy", "async"])
def test_guard_selection_preserves_legacy_admission(kind):
    intent = None if kind == "absent" else _intent()
    if kind == "legacy":
        intent = _legacy_intent()
    elif kind == "async":
        spec = cli.ScheduleFlowExperimentSpec.model_validate(
            {
                **intent.schedule_flow_spec.model_dump(),
                "master_before_flow": 40,
                "slave_before_flow": 35,
                "master_after_flow": 35,
                "slave_after_flow": 47,
                "sine_frequency": 50,
                "post_boundary_stability_seconds": 300,
            }
        )
        assert not spec.is_fixed_sync_plan
        intent = intent.model_copy(update={"schedule_flow_spec": spec})
    guard = cli._schedule_flow_hardware_guard(intent)
    assert type(guard) is (
        cli._ScheduleFlowHardwareGuard if kind in {"absent", "sync"} else DeploymentHardwareGuard
    )
