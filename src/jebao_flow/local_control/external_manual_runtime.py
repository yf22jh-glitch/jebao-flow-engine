"""Journal-first host scheduler for one fixed Pro Master/Async pair.

Every physical child is executed by LocalControlCoordinator.  This parent ledger only
orders children and prevents replay across restarts; it is not a replacement write path.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from jebao_flow.hardware_safety import hardware_safety_root, validate_hardware_safety_root
from jebao_flow.local_control.external_manual_schedule import (
    ManualRow,
    ManualSchedule,
    local_time,
    parse_schedule,
)
from jebao_flow.protocol.errors import ProtocolTimeoutError

_MAX_RECORD = 64 * 1024


@dataclass(frozen=True, slots=True)
class PumpState:
    action: int
    identity: str
    role: str
    timer: bool
    power: bool
    mode: str
    flow: int
    frequency: int
    image_sha256: str
    raw_sha256: str
    faults: tuple[str, ...]
    custom_frequency: int | None = None


@dataclass(frozen=True, slots=True)
class PairState:
    master: PumpState
    slave: PumpState


@dataclass(frozen=True, slots=True)
class Child:
    target: str
    function: str
    value: str | int | bool
    suffix: str


class SchedulerBackend(Protocol):
    master_identity: str
    slave_identity: str
    upper_is_master: bool
    master_flow_bounds: tuple[int, int]
    slave_flow_bounds: tuple[int, int]

    async def fresh_pair(self, *, preserve_raw: bool = True) -> PairState: ...

    async def execute(self, child: Child, request_id: str) -> tuple[bool, int, str]: ...

    async def validate_plan(self, plan: ManualSchedule) -> None: ...

    def unfinished(self) -> bool: ...

    def child_record(self, request_id: str) -> tuple[str, str] | None: ...


def _canonical(value: dict[str, object]) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return encoded.encode("ascii") + b"\n"


def _write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SchedulerStore:
    """Owner-only plan, active-state and per-row ledgers on the shared safety volume."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            validate_hardware_safety_root()
            root = hardware_safety_root() / "pro-external-manual-scheduler"
        self.root = Path(root)
        self.plans = self.root / "plans"
        self.operations = self.root / "operations"
        self.history = self.root / "history"
        for directory in (self.root, self.plans, self.operations, self.history):
            created = not directory.exists()
            directory.mkdir(mode=0o700, exist_ok=True)
            if created:
                _fsync_dir(directory.parent)
            metadata = directory.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise ValueError("scheduler state directory must be owner-only")

    @contextmanager
    def lock(self) -> Iterator[None]:
        path = self.root / ".runner.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("scheduler lock must be owner-only")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def runner_running(self) -> bool:
        try:
            with self.lock():
                return False
        except BlockingIOError:
            return True

    def save_plan(self, plan: ManualSchedule) -> str:
        path = self.plans / f"{plan.sha256}.json"
        if path.exists():
            if self.load_plan(plan.sha256).canonical != plan.canonical:
                raise ValueError("saved schedule digest collision")
            return plan.sha256
        _write_new(path, plan.canonical + b"\n")
        _fsync_dir(self.plans)
        return plan.sha256

    def load_plan(self, digest: str) -> ManualSchedule:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid schedule digest")
        plan = parse_schedule(self._read(self.plans / f"{digest}.json"))
        if plan.sha256 != digest:
            raise ValueError("saved schedule content changed")
        return plan

    def active(self) -> dict[str, object] | None:
        path = self.root / "active.json"
        if not path.exists():
            return None
        return json.loads(self._read(path))

    def set_active(self, record: dict[str, object]) -> None:
        self._replace(self.root / "active.json", _canonical(record))

    def archive_active(self) -> None:
        active = self.active()
        if active is None:
            raise ValueError("there is no active scheduler state to archive")
        label = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        path = self.history / f"{label}_{secrets.token_hex(8)}.json"
        _write_new(path, _canonical(active))
        _fsync_dir(self.history)

    def operation(self, key: str) -> dict[str, object] | None:
        path = self._operation_path(key)
        if not path.exists():
            return None
        return json.loads(self._read(path))

    def start_operation(self, key: str, record: dict[str, object]) -> None:
        _write_new(self._operation_path(key), _canonical(record))
        _fsync_dir(self.operations)

    def set_operation(self, key: str, record: dict[str, object]) -> None:
        if self.operation(key) is None:
            raise ValueError("parent operation was not durably created")
        self._replace(self._operation_path(key), _canonical(record))

    def set_communication(self, status: dict | None) -> None:
        self._replace(self.root / "communication.json", _canonical({
            "sampled_at": datetime.now(UTC).isoformat(), "query": status,
        }))

    def communication(self) -> dict | None:
        path = self.root / "communication.json"
        return json.loads(self._read(path)) if path.exists() else None

    def _operation_path(self, key: str) -> Path:
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
        if not key or len(key) > 128 or any(char not in allowed for char in key):
            raise ValueError("invalid scheduler operation key")
        return self.operations / f"{key}.json"

    @staticmethod
    def _read(path: Path) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > _MAX_RECORD
            ):
                raise ValueError("scheduler record is not an owner-only regular file")
            chunks: list[bytes] = []
            remaining = _MAX_RECORD + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != metadata.st_size:
                raise ValueError("scheduler record changed while being read")
            return payload
        finally:
            os.close(descriptor)

    @staticmethod
    def _replace(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}")
        _write_new(temporary, payload)
        try:
            os.replace(temporary, path)
            _fsync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


class ExternalManualScheduler:
    def __init__(
        self, *, plan: ManualSchedule, store: SchedulerStore, backend: SchedulerBackend
    ) -> None:
        self.plan = plan
        self.store = store
        self.backend = backend

    def _require_plan_binding(self) -> None:
        upper = (
            self.backend.master_identity
            if self.backend.upper_is_master
            else self.backend.slave_identity
        )
        lower = (
            self.backend.slave_identity
            if self.backend.upper_is_master
            else self.backend.master_identity
        )
        if self.plan.upper_identity_sha256 != upper or self.plan.lower_identity_sha256 != lower:
            raise ValueError("scheduled upper/lower physical identity binding changed")

    def _images(self, pair: PairState) -> list[str]:
        return [pair.master.image_sha256, pair.slave.image_sha256]

    def _active_state(self) -> dict[str, object]:
        self._require_plan_binding()
        state = self.store.active()
        if state is None or state.get("status") != "active":
            raise ValueError("external manual schedule is not active")
        if (
            state.get("schedule_sha256") != self.plan.sha256
            or state.get("master_identity") != self.backend.master_identity
            or state.get("slave_identity") != self.backend.slave_identity
            or state.get("upper_is_master") is not self.backend.upper_is_master
        ):
            raise ValueError("active schedule or physical binding changed")
        return state

    def _require_pair(
        self,
        pair: PairState,
        *,
        master_role: str,
        images: list[str],
        slave_role: str = "async_slave",
        require_power: bool = True,
    ) -> None:
        if (
            pair.master.identity != self.backend.master_identity
            or pair.slave.identity != self.backend.slave_identity
            or pair.master.action != 3
            or pair.slave.action not in (3, 4)
            or pair.master.role != master_role
            or pair.slave.role != slave_role
            or (require_power and (not pair.master.power or not pair.slave.power))
            or pair.master.faults
            or pair.slave.faults
            or self._images(pair) != images
        ):
            raise ValueError("Pro identity, roles, power, fault or image changed")
        for pump, bounds in (
            (pair.master, self.backend.master_flow_bounds),
            (pair.slave, self.backend.slave_flow_bounds),
        ):
            lower, upper = bounds
            allowed = (
                0 <= pump.flow <= min(upper, 45)
                if pump.mode == "feed"
                else max(lower, 30) <= pump.flow <= min(upper, 45)
            )
            if not allowed:
                raise ValueError("reported manual Flow is outside its configured output range")

    def _require_manual_pair(
        self, pair: PairState, images: list[str], *, require_power: bool = True
    ) -> None:
        self._require_pair(pair, master_role="master", images=images, require_power=require_power)
        if pair.master.timer or pair.slave.timer:
            raise ValueError("external manual schedule requires TimerOFF on both Pro devices")

    def _row_flows(self, row: ManualRow) -> tuple[int, int]:
        if row.upper_flow is None or row.lower_flow is None:
            raise ValueError("Feeding has no Flow target")
        return (
            (row.upper_flow, row.lower_flow)
            if self.backend.upper_is_master
            else (row.lower_flow, row.upper_flow)
        )

    async def _execute_steps(
        self,
        key: str,
        *,
        kind: str,
        steps: tuple[Child, ...],
        before: PairState,
        images: list[str],
        master_role: str = "master",
        current_time: Callable[[], datetime] | None = None,
    ) -> PairState:
        record: dict[str, object] = {
            "key": key,
            "kind": kind,
            "phase": "planned",
            "created_at": datetime.now(UTC).isoformat(),
            "before": asdict(before),
            "steps": [asdict(step) for step in steps],
            "verified": [],
        }
        self.store.start_operation(key, record)
        current = before
        slave_role = "async_slave"
        try:
            for index, child in enumerate(steps):
                if current_time is not None and self.plan.event_key(current_time()) != key:
                    raise ValueError("scheduled row changed while querying; no stale child write")
                self._require_pair(
                    current, master_role=master_role, slave_role=slave_role, images=images
                )
                if kind == "row" and (current.master.timer or current.slave.timer):
                    raise ValueError("TimerON appeared during a manual row")
                record["phase"] = "attempting"
                record["attempting_index"] = index
                self.store.set_operation(key, record)
                accepted, writes, journal_sha256 = await self.backend.execute(
                    child, f"{key}_{child.suffix}"
                )
                child_record = self.backend.child_record(f"{key}_{child.suffix}")
                readback_confirmed = child_record is not None and child_record[0] == (
                    "readback_confirmed"
                )
                dropped = child_record is not None and child_record[0] == "abandoned"
                record["last_result"] = {
                    "index": index,
                    "accepted": accepted,
                    "physical_write_count": None if readback_confirmed or dropped else writes,
                    "additional_device_writes": writes,
                    "confirmation": "readback" if readback_confirmed else (
                        "dropped" if dropped else "ack_and_readback"
                    ),
                    "journal_sha256": journal_sha256,
                }
                self.store.set_operation(key, record)
                if dropped:
                    raise ValueError("child was dropped: fresh state did not match its target")
                if not accepted or not (writes == 1 or (writes == 0 and readback_confirmed)):
                    raise ValueError("child did not verify as exactly one physical send")
                current = await self.backend.fresh_pair()
                master_role = (
                    "independent"
                    if child.target == "master"
                    and child.function == "linkage_role"
                    and child.value == "independent"
                    else "master"
                    if child.target == "master"
                    and child.function == "linkage_role"
                    and child.value == "master"
                    else master_role
                )
                if child.target == "slave" and child.function == "schedule_enable":
                    slave_role = "independent"
                elif child.target == "slave" and child.function == "linkage_role":
                    slave_role = str(child.value)
                self._require_pair(
                    current, master_role=master_role, slave_role=slave_role, images=images
                )
                if child.function in {"manual_flow", "slave_common_flow"}:
                    pump = current.master if child.target == "master" else current.slave
                    if pump.flow != child.value:
                        raise ValueError("Flow child did not persist in fresh pair")
                if child.function == "manual_mode" and current.master.mode != child.value:
                    raise ValueError("Mode child did not persist in fresh pair")
                if child.function == "manual_frequency":
                    pump = current.master if child.target == "master" else current.slave
                    if pump.frequency != child.value:
                        raise ValueError("Frequency child did not persist in fresh pair")
                if child.function == "custom_wave_frequency":
                    pump = current.master if child.target == "master" else current.slave
                    if pump.custom_frequency != child.value:
                        raise ValueError(
                            "Custom Wave Frequency child did not persist in fresh pair"
                        )
                if child.function == "schedule_enable":
                    pump = current.master if child.target == "master" else current.slave
                    if pump.timer:
                        raise ValueError("TimerOFF child did not persist in fresh pair")
                verified = record["verified"]
                assert isinstance(verified, list)
                verified.append(
                    {
                        "index": index,
                        "request_id": f"{key}_{child.suffix}",
                        "journal_sha256": journal_sha256,
                        "confirmation": "readback" if readback_confirmed else "ack_and_readback",
                        "after": asdict(current),
                    }
                )
                record["phase"] = "executing"
                record.pop("attempting_index", None)
                self.store.set_operation(key, record)
            record["phase"] = "children_verified"
            record["after"] = asdict(current)
            self.store.set_operation(key, record)
            return current
        except BaseException as error:
            record["phase"] = "stopped"
            record["failure_type"] = type(error).__name__
            self.store.set_operation(key, record)
            raise

    async def activate(
        self,
        now: datetime,
        *,
        replace_paused: bool = False,
        current_time: Callable[[], datetime] | None = None,
    ) -> dict[str, object]:
        """One explicit activation. Caller holds the scheduler process lock."""
        local = local_time(now)
        self._require_plan_binding()
        prior = self.store.active()
        if prior is not None and not (replace_paused and prior.get("status") == "paused"):
            raise ValueError("a scheduler activation already exists; inspect it before replacing")
        await self.backend.validate_plan(self.plan)
        if self.backend.unfinished():
            raise ValueError("a Pro child journal is unfinished")
        pair = await self.backend.fresh_pair()
        images = self._images(pair)
        self._require_pair(pair, master_role="master", images=images)
        if prior is not None:
            self.store.archive_active()
        state: dict[str, object] = {
            "status": "activating",
            "schedule_sha256": self.plan.sha256,
            "master_identity": self.backend.master_identity,
            "slave_identity": self.backend.slave_identity,
            "upper_is_master": self.backend.upper_is_master,
            "images": images,
            "last_event": None,
            "next_boundary_at": None,
            "feed_exit_at": None,
        }
        self.store.set_active(state)
        try:
            transition: list[Child] = []
            if pair.slave.timer or pair.master.timer:
                transition.append(Child("master", "linkage_role", "independent", "release"))
            if pair.slave.timer:
                transition.append(Child("slave", "linkage_role", "independent", "slave_release"))
                transition.append(Child("slave", "schedule_enable", False, "slave_timer_off"))
            if pair.master.timer:
                transition.append(Child("master", "schedule_enable", False, "master_timer_off"))
            if pair.slave.timer:
                transition.append(Child("slave", "linkage_role", "async_slave", "slave_relink"))
            if pair.slave.timer or pair.master.timer:
                transition.append(Child("master", "linkage_role", "master", "master_relink"))
            if transition:
                key = f"pro_prep_{local:%Y%m%d%H%M%S}_{self.plan.sha256[:16]}"
                pair = await self._execute_steps(
                    key, kind="prepare", steps=tuple(transition), before=pair, images=images
                )
                record = self.store.operation(key)
                assert record is not None
                record["phase"] = "verified"
                self.store.set_operation(key, record)
            self._require_manual_pair(pair, images)
            row_time = local_time(current_time()) if current_time is not None else local
            await self._apply_row(row_time, state, previous_check=False, current_time=current_time)
            state["status"] = "active"
            self.store.set_active(state)
            return state
        except BaseException:
            state["status"] = "paused"
            self.store.set_active(state)
            raise

    def _require_previous(self, pair: PairState, state: dict[str, object]) -> None:
        expected = state.get("expected")
        if not isinstance(expected, dict):
            raise ValueError("active scheduler has no prior verified state")
        prior_mode = expected.get("mode")
        if prior_mode == "feed":
            # The six-minute exit is explicit even if firmware ended Feeding sooner.
            return
        master_flow, slave_flow = expected.get("flows", [None, None])
        if (
            pair.master.mode != prior_mode
            or pair.slave.mode != prior_mode
            or pair.master.flow != master_flow
            or pair.slave.flow != slave_flow
        ):
            raise ValueError("external control changed the previous manual row")
        if expected.get("frequency") is not None and (
            pair.master.frequency != expected["frequency"]
            or pair.slave.frequency != expected["frequency"]
        ):
            raise ValueError("external control changed the previous manual Frequency")
        if expected.get("custom_frequency") is not None and (
            pair.master.custom_frequency != expected["custom_frequency"]
            or pair.slave.custom_frequency != expected["custom_frequency"]
        ):
            raise ValueError("external control changed the previous Custom Wave Frequency")

    async def _apply_row(
        self,
        now: datetime,
        state: dict[str, object],
        *,
        previous_check: bool = True,
        current_time: Callable[[], datetime] | None = None,
    ) -> dict[str, object]:
        row = self.plan.current(now)
        key = self.plan.event_key(now, row)
        if self.store.operation(key) is not None:
            raise ValueError("row was already attempted; never replay a child")
        if self.backend.unfinished():
            raise ValueError("a Pro child journal is unfinished")
        pair = await self.backend.fresh_pair()
        images = state["images"]
        if not isinstance(images, list):
            raise ValueError("scheduler image baseline is invalid")
        self._require_manual_pair(pair, images)
        if previous_check:
            self._require_previous(pair, state)
        if current_time is not None and self.plan.event_key(current_time()) != key:
            raise ValueError("scheduled row changed during fresh preflight; no stale write")
        steps: list[Child] = []
        if row.feeding:
            if pair.master.mode != "feed":
                steps.append(Child("master", "manual_mode", "feed", "feed"))
            next_boundary = self.plan.boundary(now)
            state["feed_exit_at"] = next_boundary.isoformat()
            self.store.set_active(state)
        else:
            target_master, target_slave = self._row_flows(row)
            slave_change = pair.slave.flow != target_slave
            master_flow_change = pair.master.flow != target_master
            master_frequency_change = (
                row.frequency is not None and pair.master.frequency != row.frequency
            )
            slave_frequency_change = (
                row.frequency is not None and pair.slave.frequency != row.frequency
            )
            master_custom_change = (
                row.custom_frequency is not None
                and pair.master.custom_frequency != row.custom_frequency
            )
            slave_custom_change = (
                row.custom_frequency is not None
                and pair.slave.custom_frequency != row.custom_frequency
            )
            mode_change = pair.master.mode != row.mode
            if (
                slave_change
                or master_flow_change
                or slave_frequency_change
                or master_frequency_change
                or slave_custom_change
                or master_custom_change
                or mode_change
            ):
                steps.append(Child("master", "linkage_role", "independent", "release"))
                if slave_change:
                    steps.append(Child("slave", "slave_common_flow", target_slave, "slave_flow"))
                if slave_frequency_change:
                    assert row.frequency is not None
                    steps.append(
                        Child("slave", "manual_frequency", row.frequency, "slave_frequency")
                    )
                if slave_custom_change:
                    assert row.custom_frequency is not None
                    steps.append(Child("slave", "custom_wave_frequency", row.custom_frequency,
                                       "slave_custom_frequency"))
                if master_flow_change:
                    steps.append(Child("master", "manual_flow", target_master, "master_flow"))
                if master_frequency_change:
                    assert row.frequency is not None
                    steps.append(Child("master", "manual_frequency", row.frequency, "frequency"))
                if master_custom_change:
                    assert row.custom_frequency is not None
                    steps.append(Child("master", "custom_wave_frequency", row.custom_frequency,
                                       "custom_frequency"))
                if mode_change:
                    steps.append(Child("master", "manual_mode", row.mode, "mode"))
                steps.append(Child("master", "linkage_role", "master", "relink"))
        final = await self._execute_steps(
            key, kind="row", steps=tuple(steps), before=pair, images=images,
            current_time=current_time,
        )
        record = self.store.operation(key)
        assert record is not None
        try:
            self._require_manual_pair(final, images)
            if row.feeding:
                if final.master.mode != "feed" or final.slave.mode != "feed":
                    raise ValueError("Feeding did not reach both Pro reports")
                expected_flows = [final.master.flow, final.slave.flow]
            else:
                target_master, target_slave = self._row_flows(row)
                if (
                    final.master.mode != row.mode
                    or final.slave.mode != row.mode
                    or final.master.flow != target_master
                    or final.slave.flow != target_slave
                    or (
                        row.frequency is not None
                        and (
                            final.master.frequency != row.frequency
                            or final.slave.frequency != row.frequency
                        )
                    )
                    or (
                        row.custom_frequency is not None
                        and (
                            final.master.custom_frequency != row.custom_frequency
                            or final.slave.custom_frequency != row.custom_frequency
                        )
                    )
                ):
                    raise ValueError("final Pro pair did not match the scheduled manual row")
                expected_flows = [target_master, target_slave]
                state["feed_exit_at"] = None
        except BaseException as error:
            record["phase"] = "stopped"
            record["failure_type"] = type(error).__name__
            self.store.set_operation(key, record)
            raise
        record["phase"] = "verified"
        self.store.set_operation(key, record)
        state["last_event"] = key
        state["next_boundary_at"] = self.plan.boundary(now).isoformat()
        state["expected"] = {
            "mode": row.mode,
            "flows": expected_flows,
            "frequency": row.frequency,
            **(
                {"custom_frequency": row.custom_frequency}
                if row.custom_frequency is not None else {}
            ),
        }
        self.store.set_active(state)
        observed = any(item.get("confirmation") == "readback" for item in record["verified"])
        return {
            "event": row.label, "operation": key,
            "writes": None if observed else len(steps),
            "send_attempts": len(steps), "mode": row.mode,
        }

    async def apply_boundary(
        self, now: datetime, *, current_time: Callable[[], datetime] | None = None
    ) -> dict[str, object]:
        """Apply only a new boundary or a missed six-minute Feeding exit."""
        local = local_time(now)
        state = self._active_state()
        row = self.plan.current(local)
        key = self.plan.event_key(local, row)
        if key == state.get("last_event"):
            return {"event": row.label, "outcome": "already_applied", "writes": 0}
        if self.store.operation(key) is not None:
            raise ValueError("row was previously attempted and must not be replayed")
        scheduled = local.replace(
            hour=row.minute // 60, minute=row.minute % 60, second=0, microsecond=0
        )
        delay = (local - scheduled).total_seconds()
        feed_exit = state.get("feed_exit_at")
        rescue = (
            isinstance(feed_exit, str)
            and local >= datetime.fromisoformat(feed_exit)
            and not row.feeding
        )
        next_boundary = state.get("next_boundary_at")
        if not isinstance(next_boundary, str):
            raise ValueError("active scheduler has no next boundary")
        next_due = datetime.fromisoformat(next_boundary)
        if local < next_due:
            raise ValueError("next scheduler boundary has not begun")
        if scheduled != next_due and not rescue:
            state["status"] = "paused"
            self.store.set_active(state)
            raise ValueError("one or more schedule boundaries were missed")
        if delay > 30 and not rescue:
            state["status"] = "paused"
            self.store.set_active(state)
            raise ValueError("schedule boundary was missed; no stale row replay")
        try:
            result = await self._apply_row(local, state, current_time=current_time)
            state["status"] = "active"
            self.store.set_active(state)
            return result
        except BaseException:
            state["status"] = "paused"
            self.store.set_active(state)
            raise

    async def resume_unattempted_relink(
        self, now: datetime, *, current_time: Callable[[], datetime] | None = None
    ) -> dict[str, object]:
        """Finish only a pre-send-busy final Master relink; never replay a verified child."""

        local = local_time(now)
        self._require_plan_binding()
        state = self.store.active()
        if (
            state is None
            or state.get("status") != "paused"
            or state.get("schedule_sha256") != self.plan.sha256
            or state.get("master_identity") != self.backend.master_identity
            or state.get("slave_identity") != self.backend.slave_identity
            or state.get("upper_is_master") is not self.backend.upper_is_master
        ):
            raise ValueError("there is no matching paused schedule to complete")
        row = self.plan.current(local)
        if row.feeding:
            raise ValueError("a Feeding row cannot resume a Master relink")
        key = self.plan.event_key(local, row)
        record = self.store.operation(key)
        if record is None or record.get("kind") != "row":
            raise ValueError("the current row has no durable parent operation")
        steps = record.get("steps")
        verified = record.get("verified")
        final_step = {
            "target": "master",
            "function": "linkage_role",
            "value": "master",
            "suffix": "relink",
        }
        if (
            record.get("phase") != "stopped"
            or record.get("failure_type") != "LocalControlLeaseBusyError"
            or not isinstance(steps, list)
            or not isinstance(verified, list)
            or len(steps) < 2
            or len(verified) != len(steps) - 1
            or record.get("attempting_index") != len(steps) - 1
            or steps[-1] != final_step
        ):
            raise ValueError("only an unattempted final Master relink can be completed")
        if self.backend.unfinished():
            raise ValueError("a Pro child journal is unfinished")
        await self.backend.validate_plan(self.plan)
        for index, item in enumerate(verified):
            if not isinstance(item, dict) or item.get("index") != index:
                raise ValueError("verified child ordering changed")
            request_id = f"{key}_{steps[index]['suffix']}"
            if item.get("request_id") != request_id or self.backend.child_record(request_id) != (
                "readback_confirmed" if item.get("confirmation") == "readback" else "verified",
                item.get("journal_sha256"),
            ):
                raise ValueError("verified child journal changed")
        images = state.get("images")
        if not isinstance(images, list):
            raise ValueError("scheduled image baseline is invalid")
        if current_time is not None and self.plan.event_key(current_time()) != key:
            raise ValueError("current row changed before final Master relink")

        request_id = f"{key}_relink"
        prior_child = self.backend.child_record(request_id)
        if prior_child is None:
            previous_pair = verified[-1].get("after")
            if not isinstance(previous_pair, dict):
                raise ValueError("last verified pair is missing")
            pair = await self.backend.fresh_pair()
            for name, pump in (("master", pair.master), ("slave", pair.slave)):
                expected = previous_pair.get(name)
                if not isinstance(expected, dict):
                    raise ValueError("fresh pair changed since the last verified child")
                for field in (
                    "identity",
                    "role",
                    "timer",
                    "power",
                    "mode",
                    "flow",
                    "frequency",
                    "image_sha256",
                    "faults",
                ):
                    expected_value = expected[field]
                    if field == "faults":
                        expected_value = tuple(expected_value)
                    if getattr(pump, field) != expected_value:
                        raise ValueError("fresh pair changed since the last verified child")
                if row.custom_frequency is not None and (
                    pump.custom_frequency != expected.get("custom_frequency")
                ):
                    raise ValueError("Custom Wave Frequency changed since the last verified child")
            self._require_pair(pair, master_role="independent", images=images)
            if pair.master.timer or pair.slave.timer:
                raise ValueError("TimerON appeared before final Master relink")
            accepted, writes, digest = await self.backend.execute(
                Child("master", "linkage_role", "master", "relink"), request_id
            )
            observed_child = self.backend.child_record(request_id)
            if not accepted or not (writes == 1 or (
                writes == 0 and observed_child is not None
                and observed_child[0] == "readback_confirmed"
            )):
                raise ValueError("final Master relink did not verify as one send")
        elif prior_child[0] in {"verified", "readback_confirmed"}:
            digest = prior_child[1]
            writes = 0
        else:
            raise ValueError("final Master relink is unfinished and must not be replayed")
        final = await self.backend.fresh_pair()
        self._require_manual_pair(final, images)
        master_flow, slave_flow = self._row_flows(row)
        if (
            final.master.mode != row.mode
            or final.slave.mode != row.mode
            or final.master.flow != master_flow
            or final.slave.flow != slave_flow
            or (
                row.frequency is not None
                and (
                    final.master.frequency != row.frequency
                    or final.slave.frequency != row.frequency
                )
            )
            or (
                row.custom_frequency is not None
                and (
                    final.master.custom_frequency != row.custom_frequency
                    or final.slave.custom_frequency != row.custom_frequency
                )
            )
        ):
            raise ValueError("final Pro pair did not match the current scheduled row")
        if current_time is not None and self.plan.event_key(current_time()) != key:
            raise ValueError("current row changed before final verification")
        verified.append(
            {
                "index": len(steps) - 1,
                "request_id": request_id,
                "journal_sha256": digest,
                "confirmation": "readback" if self.backend.child_record(request_id)[0]
                == "readback_confirmed" else "ack_and_readback",
                "after": asdict(final),
            }
        )
        record["phase"] = "verified"
        record["after"] = asdict(final)
        record["last_result"] = {
            "index": len(steps) - 1,
            "accepted": True,
            "physical_write_count": writes,
            "journal_sha256": digest,
        }
        record.pop("attempting_index", None)
        record.pop("failure_type", None)
        self.store.set_operation(key, record)
        state["last_event"] = key
        state["next_boundary_at"] = self.plan.boundary(local).isoformat()
        state["feed_exit_at"] = None
        state["expected"] = {
            "mode": row.mode,
            "flows": [master_flow, slave_flow],
            "frequency": row.frequency,
            **(
                {"custom_frequency": row.custom_frequency}
                if row.custom_frequency is not None else {}
            ),
        }
        state["status"] = "active"
        self.store.set_active(state)
        return {"event": row.label, "operation": key, "writes": writes, "mode": row.mode}

    async def resume_verified_row(
        self, now: datetime, *, current_time: Callable[[], datetime] | None = None
    ) -> dict[str, object]:
        """Re-arm an already verified current row without replaying any device write."""
        local = local_time(now)
        self._require_plan_binding()
        state = self.store.active()
        if (
            state is None
            or state.get("status") != "paused"
            or state.get("schedule_sha256") != self.plan.sha256
            or state.get("master_identity") != self.backend.master_identity
            or state.get("slave_identity") != self.backend.slave_identity
            or state.get("upper_is_master") is not self.backend.upper_is_master
        ):
            raise ValueError("there is no matching paused schedule to re-arm")
        row = self.plan.current(local)
        key = self.plan.event_key(local, row)
        record = self.store.operation(key)
        steps = record.get("steps") if record is not None else None
        verified = record.get("verified") if record is not None else None
        if (
            state.get("last_event") != key
            or record is None
            or record.get("kind") != "row"
            or record.get("phase") != "verified"
            or not isinstance(record.get("after"), dict)
            or not isinstance(steps, list)
            or not isinstance(verified, list)
            or len(steps) != len(verified)
            or state.get("next_boundary_at") != self.plan.boundary(local).isoformat()
            or state.get("expected", {}).get("mode") != row.mode
            or (row.feeding and state.get("feed_exit_at") != self.plan.boundary(local).isoformat())
        ):
            raise ValueError("the current row was not completely verified")
        if self.backend.unfinished():
            raise ValueError("a Pro child journal is unfinished")
        for index, (step, item) in enumerate(zip(steps, verified, strict=True)):
            if not isinstance(step, dict) or not isinstance(item, dict):
                raise ValueError("verified child record is invalid")
            request_id = f"{key}_{step.get('suffix')}"
            if (
                item.get("index") != index
                or item.get("request_id") != request_id
                or self.backend.child_record(request_id) != (
                    "readback_confirmed" if item.get("confirmation") == "readback" else "verified",
                    item.get("journal_sha256"),
                )
            ):
                raise ValueError("verified child journal changed")
        await self.backend.validate_plan(self.plan)
        pair = await self.backend.fresh_pair()
        images = state.get("images")
        if not isinstance(images, list):
            raise ValueError("scheduled image baseline is invalid")
        self._require_manual_pair(pair, images)
        if row.feeding:
            if pair.master.mode != "feed" or pair.slave.mode != "feed":
                raise ValueError("current Feeding row changed")
        else:
            self._require_previous(pair, state)
            master_flow, slave_flow = self._row_flows(row)
            if (
                pair.master.mode != row.mode
                or pair.slave.mode != row.mode
                or pair.master.flow != master_flow
                or pair.slave.flow != slave_flow
                or (
                    row.frequency is not None
                    and (
                        pair.master.frequency != row.frequency
                        or pair.slave.frequency != row.frequency
                    )
                )
                or (
                    row.custom_frequency is not None
                    and (
                        pair.master.custom_frequency != row.custom_frequency
                        or pair.slave.custom_frequency != row.custom_frequency
                    )
                )
            ):
                raise ValueError("fresh pair does not match the current row")
        if current_time is not None and self.plan.event_key(current_time()) != key:
            raise ValueError("scheduled row changed during no-write resume")
        state["status"] = "active"
        self.store.set_active(state)
        return {"event": row.label, "operation": key, "writes": 0, "mode": row.mode}

    async def verify_idle(self) -> None:
        """Detect app/HA changes between boundaries without issuing a control frame."""
        state = self._active_state()
        try:
            for attempt in (1, 2):
                try:
                    pair = await self.backend.fresh_pair(preserve_raw=False)
                    break
                except ProtocolTimeoutError:
                    if attempt == 2:
                        raise
                    print(
                        json.dumps(
                            {"outcome": "idle_read_timeout", "attempt": attempt, "device_writes": 0}
                        ),
                        flush=True,
                    )
            images = state["images"]
            if not isinstance(images, list):
                raise ValueError("scheduler image baseline is invalid")
            self._require_manual_pair(pair, images)
            if state.get("expected", {}).get("mode") != "feed":
                self._require_previous(pair, state)
        except asyncio.CancelledError:
            # Service shutdown cancels read-only observation, not an in-flight
            # device command. Preserve the verified row for startup validation.
            print(json.dumps({"outcome": "idle_read_cancelled", "device_writes": 0,
                              "schedule_status": state["status"]}), flush=True)
            raise
        except BaseException:
            state["status"] = "paused"
            self.store.set_active(state)
            raise


def feed_exit_overdue(state: dict[str, object], now: datetime) -> bool:
    value = state.get("feed_exit_at")
    return isinstance(value, str) and local_time(now) >= datetime.fromisoformat(value)
