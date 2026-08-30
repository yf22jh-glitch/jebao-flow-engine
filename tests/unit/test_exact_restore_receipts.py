import ast
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock, Thread

import pytest

from jebao_flow import hardware_safety
from jebao_flow.exact_restore import ExactRestoreCycle, ExactRestoreReceipt
from jebao_flow.exact_restore_receipts import (
    ExactRestoreOperationFinalizationConflictError,
    ExactRestoreReceiptArchive,
    ExactRestoreReceiptArchiveClaimError,
    ExactRestoreReceiptArchiveError,
)


def _receipt(*, operation_id: str = "qualification_001") -> ExactRestoreReceipt:
    return ExactRestoreReceipt(
        operation_id=operation_id,
        cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION,
        baseline_sha256="a" * 64,
        action_plan_sha256="b" * 64,
        authority_sha256="c" * 64,
        authority_chain_sha256="d" * 64,
        qualification_receipt_sha256=None,
        completed_action_count=8,
        final_raw_frame_sha256=("e" * 64, "f" * 64),
        completed_at=datetime(2026, 8, 30, 1, 2, 3, tzinfo=UTC),
    )


def _canonical(receipt: ExactRestoreReceipt) -> bytes:
    return (
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


def _conflicting_receipt(receipt: ExactRestoreReceipt) -> ExactRestoreReceipt:
    payload = receipt.model_dump(mode="json")
    payload["authority_sha256"] = "1" * 64
    return ExactRestoreReceipt.model_validate(payload)


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "hardware-safety"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _archive(root: Path, *, max_bytes: int = 64 * 1024) -> ExactRestoreReceiptArchive:
    return ExactRestoreReceiptArchive._for_test(root, max_bytes=max_bytes)


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def test_default_archive_uses_fixed_hardware_safety_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)
    archive = ExactRestoreReceiptArchive()
    receipt = _receipt()

    archive.persist_final_verified_receipt(receipt)

    assert archive.path_for_receipt(receipt.receipt_sha256).parent == root
    assert archive.load_final_verified_receipt(receipt.receipt_sha256) == receipt.model_dump(
        mode="json"
    )


def test_round_trip_is_canonical_private_and_idempotent(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()

    archive.persist_final_verified_receipt(receipt)
    path = archive.path_for_receipt(receipt.receipt_sha256)
    first_identity = (path.stat().st_dev, path.stat().st_ino)
    archive.persist_final_verified_receipt(receipt)

    assert path.read_bytes() == _canonical(receipt)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1
    assert (path.stat().st_dev, path.stat().st_ino) == first_identity
    assert archive.load_final_verified_receipt(receipt.receipt_sha256) == receipt.model_dump(
        mode="json"
    )
    assert not tuple(root.glob("*.tmp"))


def test_persist_also_creates_private_opaque_finalization_and_confirms_bundle(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt(operation_id="private_operation_name_001")

    archive.persist_final_verified_receipt(receipt)
    finalization_path = archive.path_for_operation_finalization(receipt.operation_id)
    first_identity = (finalization_path.stat().st_dev, finalization_path.stat().st_ino)
    confirmation = archive.confirm_operation_finalization(receipt)
    archive.persist_final_verified_receipt(receipt)

    assert receipt.operation_id not in finalization_path.name
    assert receipt.operation_id.encode("ascii") not in finalization_path.read_bytes()
    assert stat.S_IMODE(finalization_path.stat().st_mode) == 0o600
    assert finalization_path.stat().st_nlink == 1
    assert (finalization_path.stat().st_dev, finalization_path.stat().st_ino) == first_identity
    assert confirmation == {
        "version": 1,
        "operation_sha256": confirmation["operation_sha256"],
        "receipt_sha256": receipt.receipt_sha256,
        "cycle": receipt.cycle.value,
    }
    assert archive.load_operation_finalization(receipt.operation_id) == confirmation


def test_operation_lookup_distinguishes_absent_and_invalid_id(tmp_path: Path) -> None:
    archive = _archive(_private_root(tmp_path))

    assert archive.load_operation_finalization("absent_operation") is None
    for invalid in ("", "../escape", "contains space", "x" * 129):
        with pytest.raises(ExactRestoreReceiptArchiveError, match="operation id"):
            archive.load_operation_finalization(invalid)


def test_same_operation_conflicting_receipt_fails_before_archiving_contender(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    winner = _receipt(operation_id="one_operation")
    contender = _conflicting_receipt(winner)
    archive.persist_final_verified_receipt(winner)
    finalization_path = archive.path_for_operation_finalization(winner.operation_id)
    original = finalization_path.read_bytes()

    with pytest.raises(ExactRestoreOperationFinalizationConflictError):
        archive.persist_final_verified_receipt(contender)
    with pytest.raises(ExactRestoreOperationFinalizationConflictError):
        archive.confirm_operation_finalization(contender)

    assert finalization_path.read_bytes() == original
    assert not archive.path_for_receipt(contender.receipt_sha256).exists()
    assert archive.confirm_operation_finalization(winner)["receipt_sha256"] == (
        winner.receipt_sha256
    )


def test_finalization_without_exact_backing_receipt_is_corruption(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    archive.persist_final_verified_receipt(receipt)
    archive.path_for_receipt(receipt.receipt_sha256).unlink()

    with pytest.raises(ExactRestoreReceiptArchiveError, match="receipt|disappeared"):
        archive.load_operation_finalization(receipt.operation_id)


@pytest.mark.parametrize(
    "unsafe_kind",
    [
        "symlink",
        "fifo",
        "hardlink",
        "mode",
        "oversize",
        "noncanonical",
        "duplicate-key",
        "wrong-operation-digest",
        "wrong-cycle",
    ],
)
def test_operation_lookup_rejects_unsafe_or_corrupt_finalization(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    archive.persist_final_verified_receipt(receipt)
    path = archive.path_for_operation_finalization(receipt.operation_id)
    canonical = path.read_bytes()
    path.unlink()

    if unsafe_kind == "symlink":
        target = tmp_path / "finalization-target"
        _write_private(target, canonical)
        path.symlink_to(target)
    elif unsafe_kind == "fifo":
        os.mkfifo(path, mode=0o600)
    elif unsafe_kind == "hardlink":
        _write_private(path, canonical)
        os.link(path, root / "finalization-alias")
    elif unsafe_kind == "mode":
        _write_private(path, canonical)
        path.chmod(0o640)
    elif unsafe_kind == "oversize":
        _write_private(path, b"x" * (archive._max_bytes + 1))
    elif unsafe_kind == "noncanonical":
        _write_private(path, json.dumps(json.loads(canonical), indent=2).encode("ascii") + b"\n")
    elif unsafe_kind == "duplicate-key":
        _write_private(path, b'{"version":1,"version":1}\n')
    else:
        payload = json.loads(canonical)
        if unsafe_kind == "wrong-operation-digest":
            payload["operation_sha256"] = "0" * 64
        else:
            payload["cycle"] = ExactRestoreCycle.BASELINE_RESTORE.value
        _write_private(
            path,
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
                "ascii"
            )
            + b"\n",
        )

    with pytest.raises(ExactRestoreReceiptArchiveError):
        archive.load_operation_finalization(receipt.operation_id)


def test_missing_receipt_returns_none_and_digest_cannot_escape_root(tmp_path: Path) -> None:
    archive = _archive(_private_root(tmp_path))

    assert archive.load_final_verified_receipt("0" * 64) is None
    for invalid in ("A" * 64, "0" * 63, "../" + "0" * 64, "0" * 64 + ".json"):
        with pytest.raises(ExactRestoreReceiptArchiveError, match="digest"):
            archive.load_final_verified_receipt(invalid)


def test_load_rejects_receipt_digest_content_mismatch(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    expected = _receipt()
    conflicting = _receipt(operation_id="qualification_002")
    _write_private(archive.path_for_receipt(expected.receipt_sha256), _canonical(conflicting))

    with pytest.raises(ExactRestoreReceiptArchiveError, match="digest"):
        archive.load_final_verified_receipt(expected.receipt_sha256)


def test_persist_rejects_conflicting_duplicate_without_overwrite(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    expected = _receipt()
    conflicting = _receipt(operation_id="qualification_002")
    path = archive.path_for_receipt(expected.receipt_sha256)
    original = _canonical(conflicting)
    _write_private(path, original)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="digest|conflicting"):
        archive.persist_final_verified_receipt(expected)

    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "unsafe_kind",
    ["symlink", "fifo", "hardlink", "mode", "oversize", "noncanonical", "duplicate-key"],
)
def test_load_rejects_unsafe_or_noncanonical_receipt(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root, max_bytes=2048)
    receipt = _receipt()
    path = archive.path_for_receipt(receipt.receipt_sha256)
    canonical = _canonical(receipt)

    if unsafe_kind == "symlink":
        target = tmp_path / "target"
        _write_private(target, canonical)
        path.symlink_to(target)
    elif unsafe_kind == "fifo":
        os.mkfifo(path, mode=0o600)
    elif unsafe_kind == "hardlink":
        _write_private(path, canonical)
        os.link(path, root / "receipt-alias")
    elif unsafe_kind == "mode":
        _write_private(path, canonical)
        path.chmod(0o640)
    elif unsafe_kind == "oversize":
        _write_private(path, b"x" * 2049)
    elif unsafe_kind == "noncanonical":
        _write_private(
            path,
            json.dumps(receipt.model_dump(mode="json"), indent=2).encode("ascii") + b"\n",
        )
    else:
        _write_private(path, b'{"version":1,"version":1}\n')

    with pytest.raises(ExactRestoreReceiptArchiveError):
        archive.load_final_verified_receipt(receipt.receipt_sha256)


def test_symlink_receipt_never_touches_target(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    target = tmp_path / "target"
    original = _canonical(receipt)
    _write_private(target, original)
    archive.path_for_receipt(receipt.receipt_sha256).symlink_to(target)

    with pytest.raises(ExactRestoreReceiptArchiveError):
        archive.persist_final_verified_receipt(receipt)

    assert target.read_bytes() == original


def test_wrong_owner_metadata_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    archive.persist_final_verified_receipt(receipt)
    actual_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: actual_euid + 1)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="owner|root"):
        archive.load_final_verified_receipt(receipt.receipt_sha256)
    with pytest.raises(ExactRestoreReceiptArchiveError, match="owner|root"):
        archive.load_operation_finalization(receipt.operation_id)


def test_inode_replacement_between_stat_and_open_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    archive.persist_final_verified_receipt(receipt)
    path = archive.path_for_receipt(receipt.receipt_sha256)
    replacement = root / "replacement.json"
    _write_private(replacement, _canonical(receipt))
    real_open = os.open
    armed = True

    def replacing_open(
        candidate: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal armed
        if armed and candidate == path.name and dir_fd is not None:
            armed = False
            os.replace(replacement, path)
        return real_open(candidate, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replacing_open)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="changed while opening"):
        archive.load_final_verified_receipt(receipt.receipt_sha256)


def test_destination_creation_race_never_overwrites_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    expected = _receipt()
    conflicting = _receipt(operation_id="qualification_002")
    destination = archive.path_for_receipt(expected.receipt_sha256)
    conflict_bytes = _canonical(conflicting)
    real_link = os.link
    armed = True

    def race_link(*args: object, **kwargs: object) -> None:
        nonlocal armed
        if armed:
            armed = False
            _write_private(destination, conflict_bytes)
        real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", race_link)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="digest|conflicting"):
        archive.persist_final_verified_receipt(expected)

    assert destination.read_bytes() == conflict_bytes


def test_replacement_before_exact_confirmation_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    destination = archive.path_for_receipt(receipt.receipt_sha256)
    real_confirm = archive._durably_confirm_snapshot
    armed = True

    def replace_then_confirm(*args: object, **kwargs: object) -> None:
        nonlocal armed
        if armed:
            armed = False
            replacement = root / "replacement.json"
            _write_private(replacement, _canonical(receipt))
            os.replace(replacement, destination)
        real_confirm(*args, **kwargs)

    monkeypatch.setattr(archive, "_durably_confirm_snapshot", replace_then_confirm)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="replaced"):
        archive.persist_final_verified_receipt(receipt)


def test_file_fsync_failure_does_not_install_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    destination = archive.path_for_receipt(receipt.receipt_sha256)
    real_fsync = os.fsync

    def fail_nonempty_regular_file(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0:
            raise OSError("injected receipt file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_nonempty_regular_file)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="persist"):
        archive.persist_final_verified_receipt(receipt)

    assert not destination.exists()
    assert not tuple(root.glob("*.tmp"))


def test_late_directory_fsync_failure_is_recoverable_by_exact_idempotent_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    destination = archive.path_for_receipt(receipt.receipt_sha256)
    real_fsync_root = archive._fsync_root
    armed = True

    def fail_after_directory_fsync(descriptor: int) -> None:
        nonlocal armed
        real_fsync_root(descriptor)
        if armed:
            armed = False
            raise OSError("injected late directory fsync failure")

    monkeypatch.setattr(archive, "_fsync_root", fail_after_directory_fsync)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="confirm"):
        archive.persist_final_verified_receipt(receipt)

    monkeypatch.setattr(archive, "_fsync_root", real_fsync_root)
    assert destination.exists()
    assert destination.stat().st_nlink == 1
    archive.persist_final_verified_receipt(receipt)
    assert archive.load_final_verified_receipt(receipt.receipt_sha256) == receipt.model_dump(
        mode="json"
    )


def test_finalization_file_fsync_failure_never_reports_operation_finalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    # Model a crash orphan: receipt persistence is allowed to precede the authoritative index.
    _write_private(archive.path_for_receipt(receipt.receipt_sha256), _canonical(receipt))
    real_fsync = os.fsync
    regular_file_fsyncs = 0

    def fail_second_regular_file_fsync(descriptor: int) -> None:
        nonlocal regular_file_fsyncs
        metadata = os.fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0:
            regular_file_fsyncs += 1
            if regular_file_fsyncs == 2:
                raise OSError("injected finalization file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_second_regular_file_fsync)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="persist|finalization"):
        archive.persist_final_verified_receipt(receipt)

    assert not archive.path_for_operation_finalization(receipt.operation_id).exists()


def test_finalization_directory_fsync_failure_requires_exact_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    _write_private(archive.path_for_receipt(receipt.receipt_sha256), _canonical(receipt))
    real_fsync_root = archive._fsync_root
    directory_fsyncs = 0

    def fail_first_index_namespace_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        directory_fsyncs += 1
        real_fsync_root(descriptor)
        if directory_fsyncs == 2:
            raise OSError("injected finalization namespace fsync failure")

    monkeypatch.setattr(archive, "_fsync_root", fail_first_index_namespace_fsync)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="confirm.*finalization"):
        archive.persist_final_verified_receipt(receipt)

    monkeypatch.setattr(archive, "_fsync_root", real_fsync_root)
    archive.persist_final_verified_receipt(receipt)
    assert archive.confirm_operation_finalization(receipt)["receipt_sha256"] == (
        receipt.receipt_sha256
    )


def test_finalization_link_failure_leaves_no_authoritative_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    real_link = os.link

    def fail_finalization_link(*args: object, **kwargs: object) -> None:
        destination = args[1]
        if isinstance(destination, str) and destination.startswith("exact-restore-finalized-"):
            raise OSError("injected finalization install failure")
        real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", fail_finalization_link)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="persist.*finalization"):
        archive.persist_final_verified_receipt(receipt)

    assert archive.path_for_receipt(receipt.receipt_sha256).exists()
    assert not archive.path_for_operation_finalization(receipt.operation_id).exists()


def test_finalization_reload_confirmation_failure_cannot_precede_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _private_root(tmp_path)
    archive = _archive(root)
    receipt = _receipt()
    _write_private(archive.path_for_receipt(receipt.receipt_sha256), _canonical(receipt))
    real_load = archive._load_finalization_snapshot
    loads = 0

    def lose_installed_reload(*args: object, **kwargs: object) -> object:
        nonlocal loads
        loads += 1
        result = real_load(*args, **kwargs)
        if loads == 3:
            return None
        return result

    monkeypatch.setattr(archive, "_load_finalization_snapshot", lose_installed_reload)

    with pytest.raises(ExactRestoreReceiptArchiveError, match="installed.*disappeared"):
        archive.persist_final_verified_receipt(receipt)

    monkeypatch.setattr(archive, "_load_finalization_snapshot", real_load)
    archive.persist_final_verified_receipt(receipt)
    assert archive.confirm_operation_finalization(receipt)["receipt_sha256"] == (
        receipt.receipt_sha256
    )


def test_concurrent_same_receipt_contenders_converge_to_same_exact_success(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    receipt = _receipt()
    barrier = Barrier(2)
    result_lock = Lock()
    results: list[str] = []

    def contender() -> None:
        archive = _archive(root)
        barrier.wait(timeout=1)
        for _ in range(1000):
            try:
                archive.persist_final_verified_receipt(receipt)
            except ExactRestoreReceiptArchiveClaimError:
                time.sleep(0.001)
                continue
            with result_lock:
                results.append("success")
            return
        with result_lock:
            results.append("claim-timeout")

    threads = [Thread(target=contender), Thread(target=contender)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert results == ["success", "success"]
    assert _archive(root).confirm_operation_finalization(receipt)["receipt_sha256"] == (
        receipt.receipt_sha256
    )


def test_concurrent_conflicting_receipts_have_exactly_one_winner(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    first = _receipt(operation_id="contended_operation")
    second = _conflicting_receipt(first)
    barrier = Barrier(2)
    result_lock = Lock()
    results: list[tuple[str, str]] = []

    def contender(receipt: ExactRestoreReceipt) -> None:
        archive = _archive(root)
        barrier.wait(timeout=1)
        for _ in range(1000):
            try:
                archive.persist_final_verified_receipt(receipt)
            except ExactRestoreReceiptArchiveClaimError:
                time.sleep(0.001)
                continue
            except ExactRestoreOperationFinalizationConflictError:
                outcome = "conflict"
            else:
                outcome = "success"
            with result_lock:
                results.append((receipt.receipt_sha256, outcome))
            return
        with result_lock:
            results.append((receipt.receipt_sha256, "claim-timeout"))

    threads = [Thread(target=contender, args=(receipt,)) for receipt in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcome for _, outcome in results) == ["conflict", "success"]
    winner_digest = next(digest for digest, outcome in results if outcome == "success")
    finalization = _archive(root).load_operation_finalization(first.operation_id)
    assert finalization is not None
    assert finalization["receipt_sha256"] == winner_digest


def test_claim_is_nonblocking_and_rejects_nested_owner(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    owner = _archive(root)
    contender = _archive(root)

    with owner._claim():
        with pytest.raises(ExactRestoreReceiptArchiveClaimError, match="another process"):
            contender.load_final_verified_receipt("0" * 64)
        with pytest.raises(ExactRestoreReceiptArchiveClaimError, match="already active"):
            with owner._claim():
                raise AssertionError("nested receipt archive claim was acquired")

    assert contender.load_final_verified_receipt("0" * 64) is None


def test_wrong_thread_context_exit_leaves_owner_claim_intact(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    owner = _archive(root)
    contender = _archive(root)
    context = owner._claim()
    context.__enter__()
    failures: list[BaseException] = []

    def wrong_thread_release() -> None:
        try:
            context.__exit__(None, None, None)
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=wrong_thread_release)
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], ExactRestoreReceiptArchiveClaimError)
    with pytest.raises(ExactRestoreReceiptArchiveClaimError, match="another process"):
        contender.load_final_verified_receipt("0" * 64)

    context.__exit__(None, None, None)
    assert contender.load_final_verified_receipt("0" * 64) is None


def test_archive_instance_is_process_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(_private_root(tmp_path))
    original_pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: original_pid + 1)

    with pytest.raises(ExactRestoreReceiptArchiveClaimError, match="forked process"):
        archive.load_final_verified_receipt("0" * 64)


def test_archive_import_graph_excludes_devices_persistence_and_frozen_harness() -> None:
    source_path = Path(__file__).parents[2] / "src" / "jebao_flow" / "exact_restore_receipts.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    assert not any(name.startswith("jebao_flow.devices") for name in imported)
    assert not any(name.startswith("jebao_flow.persistence") for name in imported)
    assert "jebao_flow.schedule_linkage_cli" not in imported
    assert "jebao_flow.schedule_flow_experiment_cli" not in imported
