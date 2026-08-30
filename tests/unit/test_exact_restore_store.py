import ast
import json
import os
import stat
from pathlib import Path
from threading import Thread

import pytest

from jebao_flow import hardware_safety
from jebao_flow.exact_restore_store import (
    ExactRestoreJournalClaimError,
    ExactRestoreJournalError,
    ExactRestoreJournalStore,
)


def _record(phase: str = "captured") -> dict[str, object]:
    return {
        "operation_id": "ERJ-test",
        "phase": phase,
        "devices": [
            {"binding": "sha256:role-a", "schedule": [31, 32, 33]},
            {"binding": "sha256:role-b", "schedule": [35, 36, 37]},
        ],
        "authority": {"approved": True, "attempt": 1},
    }


def _canonical(record: dict[str, object]) -> bytes:
    return (
        json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _store(path: Path, *, max_bytes: int = 1024 * 1024) -> ExactRestoreJournalStore:
    return ExactRestoreJournalStore._for_test(path, max_bytes=max_bytes)


def test_default_path_uses_hardware_safety_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)

    store = ExactRestoreJournalStore()

    assert store.path == root / "exact-restore.json"
    with store.claim():
        pass
    assert {path.name for path in root.iterdir()} == {".exact-restore.json.lock"}


@pytest.mark.parametrize("operation", ["claim", "load"])
def test_fixed_root_swap_after_validation_never_uses_replacement_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    root = tmp_path / "hardware-safety"
    detached = tmp_path / "detached-hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)
    store = ExactRestoreJournalStore()
    real_open_root = hardware_safety._open_hardware_safety_root

    def open_then_swap_root() -> int:
        descriptor = real_open_root()
        root.rename(detached)
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        _write_private(root / "exact-restore.json", _canonical(_record("replacement")))
        return descriptor

    monkeypatch.setattr(
        "jebao_flow.exact_restore_store._open_hardware_safety_root",
        open_then_swap_root,
    )

    with pytest.raises(ExactRestoreJournalError, match="changed|unavailable"):
        if operation == "claim":
            with store.claim():
                raise AssertionError("claim escaped into a replacement safety root")
        else:
            store.load()

    assert {path.name for path in root.iterdir()} == {"exact-restore.json"}
    assert json.loads((root / "exact-restore.json").read_text(encoding="utf-8")) == _record(
        "replacement"
    )


def test_create_save_load_and_clear_canonical_successors(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    first = _record()
    second = _record("restoring")

    assert store.load() is None
    with store.claim():
        store.create(first)
        assert store.load() == first
        assert path.read_bytes() == _canonical(first)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.stat().st_nlink == 1

        store.save(second)
        assert store.load() == second
        assert path.read_bytes() == _canonical(second)

        store.clear()
        assert store.load() is None

    assert not path.exists()


def test_loaded_record_is_a_deep_copy_of_claim_state(tmp_path: Path) -> None:
    store = _store(tmp_path / "exact-restore.json")
    expected = _record()

    with store.claim():
        store.create(expected)
        loaded = store.load()
        assert loaded is not None
        devices = loaded["devices"]
        assert isinstance(devices, list)
        devices.clear()

        assert store.load() == expected
        store.save(_record("restoring"))


def test_mutations_require_claim_and_save_requires_first_create(tmp_path: Path) -> None:
    store = _store(tmp_path / "exact-restore.json")

    with pytest.raises(ExactRestoreJournalClaimError, match="requires"):
        store.create(_record())
    with pytest.raises(ExactRestoreJournalClaimError, match="requires"):
        store.save(_record())
    with pytest.raises(ExactRestoreJournalClaimError, match="requires"):
        store.clear()

    with store.claim():
        with pytest.raises(ExactRestoreJournalError, match="created"):
            store.save(_record())


def test_first_create_is_exclusive(tmp_path: Path) -> None:
    store = _store(tmp_path / "exact-restore.json")

    with store.claim():
        store.create(_record())
        with pytest.raises(ExactRestoreJournalClaimError, match="first record"):
            store.create(_record("second-create"))


def test_claim_is_nonblocking_and_rejects_nested_owner(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    owner = _store(path)
    contender = _store(path)

    with owner.claim():
        with pytest.raises(ExactRestoreJournalClaimError, match="another process"):
            with contender.claim():
                raise AssertionError("contending claim was acquired")
        with pytest.raises(ExactRestoreJournalClaimError, match="already active"):
            with owner.claim():
                raise AssertionError("nested claim was acquired")

    with contender.claim():
        pass


def test_claim_rejects_symlink_lock_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("unchanged", encoding="utf-8")
    (tmp_path / ".exact-restore.json.lock").symlink_to(target)
    store = _store(tmp_path / "exact-restore.json")

    with pytest.raises(ExactRestoreJournalError):
        with store.claim():
            raise AssertionError("unsafe lock was acquired")

    assert target.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize(
    "unsafe_kind",
    ["symlink", "fifo", "hardlink", "mode", "oversize", "truncated", "duplicate", "noncanonical"],
)
def test_load_rejects_unsafe_or_invalid_journal(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path, max_bytes=256)
    valid = _canonical({"phase": "captured"})

    if unsafe_kind == "symlink":
        target = tmp_path / "target"
        _write_private(target, valid)
        path.symlink_to(target)
    elif unsafe_kind == "fifo":
        os.mkfifo(path, mode=0o600)
    elif unsafe_kind == "hardlink":
        _write_private(path, valid)
        os.link(path, tmp_path / "journal-alias")
    elif unsafe_kind == "mode":
        _write_private(path, valid)
        path.chmod(0o640)
    elif unsafe_kind == "oversize":
        _write_private(path, b"x" * 257)
    elif unsafe_kind == "truncated":
        _write_private(path, b'{"phase":"captured"\n')
    elif unsafe_kind == "duplicate":
        _write_private(path, b'{"phase":"captured","phase":"restoring"}\n')
    else:
        _write_private(path, b'{"phase": "captured"}\n')

    with pytest.raises(ExactRestoreJournalError):
        store.load()


def test_symlink_journal_does_not_touch_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    original = _canonical({"phase": "unchanged"})
    _write_private(target, original)
    (tmp_path / "exact-restore.json").symlink_to(target)
    store = _store(tmp_path / "exact-restore.json")

    with pytest.raises(ExactRestoreJournalError):
        store.load()

    assert target.read_bytes() == original


def test_load_rejects_wrong_owner_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    _write_private(path, _canonical(_record()))
    actual_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: actual_euid + 1)

    with pytest.raises(ExactRestoreJournalError, match="ownership"):
        _store(path).load()


def test_store_fails_closed_without_o_nofollow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "O_NOFOLLOW")

    with pytest.raises(ExactRestoreJournalError, match="O_NOFOLLOW"):
        _store(tmp_path / "exact-restore.json").load()


def test_load_detects_inode_replacement_between_stat_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    replacement = tmp_path / "replacement.json"
    _write_private(path, _canonical(_record()))
    _write_private(replacement, _canonical(_record("replacement")))
    store = _store(path)
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

    with pytest.raises(ExactRestoreJournalError, match="changed while opening"):
        store.load()


def test_claim_rejects_external_atomic_successor_even_with_same_content(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    replacement = tmp_path / "replacement.json"
    store = _store(path)
    expected = _record()

    with store.claim():
        store.create(expected)
        _write_private(replacement, _canonical(expected))
        os.replace(replacement, path)

        with pytest.raises(ExactRestoreJournalClaimError, match="outside"):
            store.save(_record("restoring"))


def test_active_claim_is_bound_to_its_thread(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    failures: list[BaseException] = []

    with store.claim():
        store.create(_record())

        def clear_from_other_thread() -> None:
            try:
                store.clear()
            except BaseException as error:
                failures.append(error)

        thread = Thread(target=clear_from_other_thread)
        thread.start()
        thread.join(timeout=1)

        assert not thread.is_alive()
        assert len(failures) == 1
        assert isinstance(failures[0], ExactRestoreJournalClaimError)
        assert path.exists()


def test_active_claim_rejects_inherited_or_changed_process_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "exact-restore.json")

    with store.claim():
        store.create(_record())
        with monkeypatch.context() as identity_patch:
            original_pid = os.getpid()
            identity_patch.setattr(os, "getpid", lambda: original_pid + 1)

            with pytest.raises(ExactRestoreJournalClaimError, match="requires"):
                store.clear()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork semantics")
def test_fork_child_context_unwind_does_not_unlock_parent_claim(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    owner = _store(path)
    contender = _store(path)
    read_descriptor, write_descriptor = os.pipe()

    context = owner.claim()
    context.__enter__()
    owner.create(_record())
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(read_descriptor)
            with pytest.raises(ExactRestoreJournalClaimError, match="owner process and thread"):
                context.__exit__(None, None, None)
            os.write(write_descriptor, b"x")
            os.close(write_descriptor)
        finally:
            os._exit(0)

    os.close(write_descriptor)
    assert os.read(read_descriptor, 1) == b"x"
    with pytest.raises(ExactRestoreJournalClaimError, match="another process"):
        with contender.claim():
            raise AssertionError("fork child released the parent claim")
    waited_pid, status = os.waitpid(child_pid, 0)
    assert waited_pid == child_pid
    assert os.waitstatus_to_exitcode(status) == 0
    context.__exit__(None, None, None)
    os.close(read_descriptor)


def test_wrong_thread_context_exit_leaves_owner_claim_intact(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    owner = _store(path)
    contender = _store(path)
    context = owner.claim()
    context.__enter__()
    owner.create(_record())
    failures: list[BaseException] = []

    def release_from_other_thread() -> None:
        try:
            context.__exit__(None, None, None)
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=release_from_other_thread)
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], ExactRestoreJournalClaimError)
    with pytest.raises(ExactRestoreJournalClaimError, match="another process"):
        with contender.claim():
            raise AssertionError("wrong-thread cleanup released the owner claim")
    assert owner.load() == _record()

    context.__exit__(None, None, None)
    with contender.claim():
        contender.save(_record("successor"))
    assert contender.load() == _record("successor")


def test_parent_directory_swap_during_load_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "safety"
    detached = tmp_path / "detached-safety"
    parent.mkdir(mode=0o700)
    path = parent / "exact-restore.json"
    original = _record()
    _write_private(path, _canonical(original))
    store = _store(path)
    real_read_bounded = store._read_bounded

    def swap_parent_after_read(descriptor: int) -> bytes:
        payload = real_read_bounded(descriptor)
        parent.rename(detached)
        parent.mkdir(mode=0o700)
        _write_private(path, _canonical(_record("replacement-authority")))
        return payload

    monkeypatch.setattr(store, "_read_bounded", swap_parent_after_read)

    with pytest.raises(ExactRestoreJournalError, match="parent changed"):
        store.load()


def test_replaced_claim_file_blocks_mutation_before_namespace_change(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    original = _record()
    lock_path = tmp_path / ".exact-restore.json.lock"

    with store.claim():
        store.create(original)
        lock_path.unlink()
        lock_path.touch(mode=0o600)
        lock_path.chmod(0o600)

        with pytest.raises(ExactRestoreJournalClaimError, match="changed"):
            store.save(_record("must-not-replace"))

    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_claim_replacement_after_temp_fsync_blocks_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    original = _record()
    lock_path = tmp_path / ".exact-restore.json.lock"

    with store.claim():
        store.create(original)
        real_write_temporary = store._write_temporary

        def replace_claim_after_temp(parent_descriptor: int, payload: bytes) -> str:
            temporary = real_write_temporary(parent_descriptor, payload)
            lock_path.unlink()
            lock_path.touch(mode=0o600)
            lock_path.chmod(0o600)
            return temporary

        monkeypatch.setattr(store, "_write_temporary", replace_claim_after_temp)
        with pytest.raises(ExactRestoreJournalClaimError, match="changed"):
            store.save(_record("must-not-replace"))

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert not tuple(tmp_path.glob(".exact-restore.json.*.tmp"))


def test_claim_replacement_after_atomic_replace_never_accepts_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    successor = _record("uncertain-successor")
    lock_path = tmp_path / ".exact-restore.json.lock"

    with store.claim():
        store.create(_record())
        real_replace = os.replace

        def replace_then_split_claim(*args: object, **kwargs: object) -> None:
            real_replace(*args, **kwargs)
            lock_path.unlink()
            lock_path.touch(mode=0o600)
            lock_path.chmod(0o600)

        monkeypatch.setattr(os, "replace", replace_then_split_claim)
        with pytest.raises(ExactRestoreJournalClaimError, match="changed"):
            store.save(successor)

    assert _store(path).load() == successor


def test_exact_successor_confirmation_rejects_bool_integer_type_confusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "exact-restore.json")
    actual = {"operation_id": "ERJ-test", "approved": 1}
    confused = {"operation_id": "ERJ-test", "approved": True}

    with store.claim():
        store.create(_record())
        real_fsync_parent = store._fsync_parent
        armed = True

        def fail_after_directory_fsync(descriptor: int) -> None:
            nonlocal armed
            real_fsync_parent(descriptor)
            if armed:
                armed = False
                raise OSError("injected late directory fsync failure")

        monkeypatch.setattr(store, "_fsync_parent", fail_after_directory_fsync)
        with pytest.raises(ExactRestoreJournalError, match="save"):
            store.save(actual)

        monkeypatch.setattr(store, "_fsync_parent", real_fsync_parent)
        assert not store.reload_and_confirm_successor(confused)
        assert store.reload_and_confirm_successor(actual)


@pytest.mark.parametrize(
    "bad_record",
    [
        {"not_json": (1, 2)},
        {"not_json": float("nan")},
        {1: "non-string-key"},
        {"not_json": b"bytes"},
    ],
)
def test_create_rejects_non_json_compatible_records(
    tmp_path: Path,
    bad_record: dict[object, object],
) -> None:
    store = _store(tmp_path / "exact-restore.json")

    with store.claim():
        with pytest.raises(ExactRestoreJournalError, match="JSON-compatible"):
            store.create(bad_record)  # type: ignore[arg-type]


def test_create_rejects_payload_above_bound_before_install(tmp_path: Path) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path, max_bytes=32)

    with store.claim():
        with pytest.raises(ExactRestoreJournalError, match="too large"):
            store.create({"schedule": "x" * 64})

    assert not path.exists()


@pytest.mark.parametrize("invalid", [True, 1.5, 0, -1])
def test_max_bytes_requires_a_positive_integer(tmp_path: Path, invalid: object) -> None:
    with pytest.raises(ValueError, match="positive"):
        ExactRestoreJournalStore._for_test(
            tmp_path / "exact-restore.json",
            max_bytes=invalid,  # type: ignore[arg-type]
        )


def test_file_fsync_error_fails_before_first_record_is_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    real_fsync = os.fsync

    def fail_regular_file_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0:
            raise OSError("injected file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_regular_file_fsync)

    with store.claim():
        with pytest.raises(ExactRestoreJournalError, match="create"):
            store.create(_record())

    assert not path.exists()


def test_update_file_fsync_error_preserves_previous_atomic_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)
    first = _record()

    with store.claim():
        store.create(first)
        real_fsync = os.fsync

        def fail_regular_file_fsync(descriptor: int) -> None:
            metadata = os.fstat(descriptor)
            if stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0:
                raise OSError("injected update file fsync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_regular_file_fsync)
        with pytest.raises(ExactRestoreJournalError, match="save"):
            store.save(_record("uncommitted"))
        monkeypatch.setattr(os, "fsync", real_fsync)

        assert store.load() == first
        store.save(_record("committed"))


def test_late_directory_fsync_error_can_confirm_exact_save_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "exact-restore.json")
    first = _record()
    successor = _record("restoring")
    final = _record("verified")

    with store.claim():
        store.create(first)
        real_fsync_parent = store._fsync_parent
        armed = True

        def fail_after_directory_fsync(descriptor: int) -> None:
            nonlocal armed
            real_fsync_parent(descriptor)
            if armed:
                armed = False
                raise OSError("injected late directory fsync failure")

        monkeypatch.setattr(store, "_fsync_parent", fail_after_directory_fsync)
        with pytest.raises(ExactRestoreJournalError, match="save"):
            store.save(successor)

        monkeypatch.setattr(store, "_fsync_parent", real_fsync_parent)
        assert store.load() == successor
        assert store.reload_and_confirm_successor(successor)
        store.save(final)
        assert store.load() == final


def test_late_directory_fsync_error_rejects_wrong_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "exact-restore.json")
    first = _record()
    actual = _record("actual")

    with store.claim():
        store.create(first)
        real_fsync_parent = store._fsync_parent

        def fail_after_directory_fsync(descriptor: int) -> None:
            real_fsync_parent(descriptor)
            raise OSError("injected late directory fsync failure")

        monkeypatch.setattr(store, "_fsync_parent", fail_after_directory_fsync)
        with pytest.raises(ExactRestoreJournalError):
            store.save(actual)

        monkeypatch.setattr(store, "_fsync_parent", real_fsync_parent)
        assert not store.reload_and_confirm_successor(_record("different"))
        with pytest.raises(ExactRestoreJournalClaimError, match="outside"):
            store.save(_record("next"))


def test_late_directory_fsync_error_can_confirm_clear_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    store = _store(path)

    with store.claim():
        store.create(_record())
        real_fsync_parent = store._fsync_parent
        armed = True

        def fail_after_directory_fsync(descriptor: int) -> None:
            nonlocal armed
            real_fsync_parent(descriptor)
            if armed:
                armed = False
                raise OSError("injected late directory fsync failure")

        monkeypatch.setattr(store, "_fsync_parent", fail_after_directory_fsync)
        with pytest.raises(ExactRestoreJournalError, match="clear"):
            store.clear()

        monkeypatch.setattr(store, "_fsync_parent", real_fsync_parent)
        assert not path.exists()
        assert store.reload_and_confirm_successor(None)
        store.create(_record("new-operation"))


def test_create_save_and_clear_fsync_expected_filesystem_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "exact-restore.json")
    real_fsync = os.fsync
    regular_calls = 0
    directory_calls = 0

    def count_fsync(descriptor: int) -> None:
        nonlocal regular_calls, directory_calls
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            directory_calls += 1
        elif stat.S_ISREG(metadata.st_mode):
            regular_calls += 1
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", count_fsync)

    with store.claim():
        store.create(_record())
        store.save(_record("restoring"))
        store.clear()

    assert regular_calls == 2
    assert directory_calls == 4


def test_store_import_graph_excludes_devices_persistence_and_frozen_harness() -> None:
    source_path = Path(__file__).parents[2] / "src" / "jebao_flow" / "exact_restore_store.py"
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
