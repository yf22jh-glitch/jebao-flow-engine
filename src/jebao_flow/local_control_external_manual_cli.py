"""Save, activate and run the fixed-role Pro external manual schedule.

Save/preview/status never transmit a device command. Activation is explicit and
applies the current row once; run resumes at future boundaries without replaying it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import (
    configured_writer_factory,
    extract_current_pro_schedule_image,
)
from jebao_flow.local_control.external_manual_runtime import (
    Child,
    ExternalManualScheduler,
    PairState,
    PumpState,
    SchedulerStore,
    feed_exit_overdue,
)
from jebao_flow.local_control.external_manual_schedule import ZONE, ManualSchedule, load_schedule
from jebao_flow.local_control.install import load_current_private_config
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease, LocalControlLeaseBusyError
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.qualification_producer import load_current_install_manifest
from jebao_flow.local_control_cli import (
    _activate_current_hardware_safety_volume_identity,
    _resolver,
    _runtime_catalog,
)
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY


class _ReportAwareWriter:
    """The normal writer, with the linked slave's observed action-4 read path."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def connect_and_read(self):
        return (await self.inner.connect_and_capture(accept_reports=True)).status_payload

    async def readback(self, lease):
        return (await self.inner.capture_readback(lease, accept_reports=True)).status_payload


class LiveBackend:
    def __init__(
        self, *, master_member: str, slave_member: str, upper_member: str, lower_member: str
    ) -> None:
        _activate_current_hardware_safety_volume_identity()
        config = load_current_private_config(Path(os.environ["JEBAO_FLOW_CONFIG"]))
        manifest = load_current_install_manifest(
            Path(os.environ["JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"])
        )
        qualification_path = os.environ.get("JEBAO_FLOW_LOCAL_QUALIFICATIONS", "").strip()
        qualifications = (
            load_current_qualifications(Path(qualification_path)) if qualification_path else ()
        )
        catalog = _runtime_catalog(
            qualifications, manifest.content, config=config, manifest=manifest, write_enabled=True
        )
        members = {item.logical_member: item for item in manifest.devices}
        if master_member == slave_member or {upper_member, lower_member} != {
            master_member,
            slave_member,
        }:
            raise ValueError("upper/lower must name the two distinct fixed Master/Async members")
        self.master = members[master_member]
        self.slave = members[slave_member]
        if (
            self.master.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            or self.slave.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            or self.master.physical_identity_key == self.slave.physical_identity_key
        ):
            raise ValueError("scheduler requires two distinct physical Pro bindings")
        self.master_identity = self.master.physical_identity_key
        self.slave_identity = self.slave.physical_identity_key
        self.upper_is_master = upper_member == master_member
        self.config = config
        config_by_member = {item.id: item for item in config.devices}
        self.master_flow_bounds = (
            config_by_member[master_member].limits.min_power,
            config_by_member[master_member].limits.max_power,
        )
        self.slave_flow_bounds = (
            config_by_member[slave_member].limits.min_power,
            config_by_member[slave_member].limits.max_power,
        )
        self.catalog = catalog
        self.journal = LocalControlJournalStore()
        self.resolver = _resolver(config)
        normal_factory = configured_writer_factory(config)
        self.writer_factory = lambda endpoint: (
            _ReportAwareWriter(normal_factory(endpoint))
            if endpoint.target == self.slave
            else normal_factory(endpoint)
        )
        self.coordinator = LocalControlCoordinator(
            catalog=catalog,
            resolver=self.resolver,
            journal=self.journal,
            lease=LocalControlLease(),
            writer_factory=self.writer_factory,
        )

    def unfinished(self) -> bool:
        return bool(self.journal.unfinished_for((self.master_identity, self.slave_identity)))

    def child_record(self, request_id: str) -> tuple[str, str] | None:
        record = self.journal.load(request_id)
        return None if record is None else (record.phase.value, self.journal.record_digest(record))

    async def validate_plan(self, plan: ManualSchedule) -> None:
        bounds = {item.id: item.limits for item in self.config.devices}
        upper_member = (
            self.master.logical_member if self.upper_is_master else self.slave.logical_member
        )
        lower_member = (
            self.slave.logical_member if self.upper_is_master else self.master.logical_member
        )
        for row in plan.rows:
            if row.feeding:
                continue
            assert row.upper_flow is not None and row.lower_flow is not None
            for member, flow in ((upper_member, row.upper_flow), (lower_member, row.lower_flow)):
                limits = bounds[member]
                if not limits.min_power <= flow <= min(limits.max_power, 45):
                    raise ValueError("a scheduled Flow exceeds its physical device bounds")
        endpoints = await self.resolver.resolve_many((self.master, self.slave))
        endpoint_by_identity = {
            endpoint.target.physical_identity_key: endpoint for endpoint in endpoints
        }
        values: set[tuple[str, str, str | int | bool]] = {
            ("master", "linkage_role", "independent"),
            ("master", "linkage_role", "master"),
            ("master", "schedule_enable", False),
            ("slave", "schedule_enable", False),
            ("slave", "linkage_role", "independent"),
            ("slave", "linkage_role", "async_slave"),
            ("master", "manual_mode", "feed"),
        }
        for row in plan.rows:
            if row.feeding:
                continue
            master_flow, slave_flow = (
                (row.upper_flow, row.lower_flow)
                if self.upper_is_master
                else (row.lower_flow, row.upper_flow)
            )
            assert master_flow is not None and slave_flow is not None
            values.update(
                {
                    ("master", "manual_mode", row.mode),
                    ("master", "manual_flow", master_flow),
                    ("slave", "slave_common_flow", slave_flow),
                }
            )
            if row.frequency is not None:
                values.add(("master", "manual_frequency", row.frequency))
                values.add(("slave", "manual_frequency", row.frequency))
        for target_name, function, value in values:
            target = self.master if target_name == "master" else self.slave
            intent = LocalControlIntent(
                request_id="pro_manual_preflight_00000000",
                target=target,
                function=function,
                value=value,
            )
            descriptor = self.catalog.validate_intent(intent)
            self.coordinator._require_current_control_authority(
                descriptor, endpoint_by_identity[target.physical_identity_key]
            )

    def _decode(self, capture, target: LocalDeviceBinding, *, preserve_raw: bool) -> PumpState:
        values = get_product_schema(target.product_key).decode_status(capture.status_payload)
        return PumpState(
            action=capture.action,
            identity=target.physical_identity_key,
            role=values["Linkage"],
            timer=values["TimerON"],
            power=values["SwitchON"],
            mode=values["Mode"],
            flow=values["Flow"],
            frequency=values["Frequency"],
            image_sha256=hashlib.sha256(
                extract_current_pro_schedule_image(capture.status_payload)
            ).hexdigest(),
            raw_sha256=(
                self.journal.install_blob(capture.wire_frame)
                if preserve_raw
                else hashlib.sha256(capture.wire_frame).hexdigest()
            ),
            faults=tuple(
                sorted(
                    name for name, value in values.items() if name.startswith("Fault_") and value
                )
            ),
        )

    async def fresh_pair(self, *, preserve_raw: bool = True) -> PairState:
        endpoints = await self.resolver.resolve_many((self.master, self.slave))
        master_writer = self.writer_factory(endpoints[0])
        slave_writer = self.writer_factory(endpoints[1])
        try:
            master_capture = await master_writer.connect_and_capture(accept_reports=False)
            slave_capture = await slave_writer.connect_and_capture(accept_reports=True)
            return PairState(
                self._decode(master_capture, self.master, preserve_raw=preserve_raw),
                self._decode(slave_capture, self.slave, preserve_raw=preserve_raw),
            )
        finally:
            await slave_writer.close()
            await master_writer.close()

    async def execute(self, child: Child, request_id: str) -> tuple[bool, int, str]:
        target = self.master if child.target == "master" else self.slave
        intent = LocalControlIntent(
            request_id=request_id, target=target, function=child.function, value=child.value
        )
        for attempt in range(10):
            try:
                result = await self.coordinator.execute(intent)
                break
            except LocalControlLeaseBusyError:
                # The lease was refused before journal creation and before a send.  An
                # existing child record, including an uncertain one, must never replay.
                if self.journal.load(request_id) is not None or attempt == 9:
                    raise
                await asyncio.sleep(0.5)
        return result.accepted, result.physical_write_count, result.journal_sha256


async def _run_loop(scheduler: ExternalManualScheduler) -> None:
    state = scheduler._active_state()
    await scheduler.backend.validate_plan(scheduler.plan)
    now = datetime.now(ZONE)
    current_key = scheduler.plan.event_key(now)
    if current_key != state.get("last_event"):
        row = scheduler.plan.current(now)
        scheduled = now.replace(
            hour=row.minute // 60, minute=row.minute % 60, second=0, microsecond=0
        )
        if feed_exit_overdue(state, now) or (now - scheduled).total_seconds() <= 30:
            result = await scheduler.apply_boundary(now, current_time=lambda: datetime.now(ZONE))
            print(json.dumps({"outcome": "startup_boundary", **result}), flush=True)
        else:
            state["status"] = "paused"
            scheduler.store.set_active(state)
            raise ValueError("a boundary was missed while the runner was offline")
    await scheduler.verify_idle()
    previous = now
    while True:
        boundary = scheduler.plan.boundary(previous)
        current = datetime.now(ZONE)
        if current < previous:
            raise ValueError("host wall clock moved backward")
        if current >= boundary:
            result = await scheduler.apply_boundary(
                current, current_time=lambda: datetime.now(ZONE)
            )
            print(json.dumps({"outcome": "boundary", **result}), flush=True)
            previous = boundary
            continue
        if (boundary - current).total_seconds() > 30:
            await scheduler.verify_idle()
            current = datetime.now(ZONE)
        if current >= boundary:
            continue
        previous = current
        remaining = (boundary - current).total_seconds()
        if remaining > 0:
            await asyncio.sleep(min(15.0, remaining))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "save"):
        command = sub.add_parser(name)
        command.add_argument("--file", type=Path, required=True)
        if name == "preview":
            command.add_argument("--at", help="timezone-aware ISO-8601 time")
    sub.add_parser("status")
    sub.add_parser("pause")
    for name in ("activate", "activate-and-run", "resume-relink", "run"):
        command = sub.add_parser(name)
        if name != "run":
            command.add_argument("--schedule-sha256", required=True)
            command.add_argument("--replace-paused", action="store_true")
        command.add_argument("--master-member", required=True)
        command.add_argument("--slave-member", required=True)
        command.add_argument("--upper-member", required=True)
        command.add_argument("--lower-member", required=True)
        command.add_argument("--write-enabled", action="store_true")
    return parser


async def _main_async(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "preview":
        plan = load_schedule(args.file)
        now = datetime.fromisoformat(args.at) if args.at else datetime.now(ZONE)
        row = plan.current(now)
        return {
            "outcome": "preview_only",
            "schedule_sha256": plan.sha256,
            "rows": len(plan.rows),
            "current": row.label,
            "mode": row.mode,
            "next_boundary": plan.boundary(now).isoformat(),
            "device_writes": 0,
        }
    _activate_current_hardware_safety_volume_identity()
    store = SchedulerStore()
    if args.command == "save":
        plan = load_schedule(args.file)
        return {
            "outcome": "saved",
            "schedule_sha256": store.save_plan(plan),
            "rows": len(plan.rows),
            "device_writes": 0,
        }
    if args.command == "status":
        return {
            "outcome": "status",
            "active": store.active(),
            "runner_running": store.runner_running(),
            "device_writes": 0,
        }
    if args.command == "pause":
        with store.lock():
            active = store.active()
            if active is None or active.get("status") != "active":
                raise ValueError("there is no active manual schedule to pause")
            active["status"] = "paused"
            store.set_active(active)
        return {"outcome": "paused", "device_writes": 0}
    if not args.write_enabled:
        raise ValueError("live scheduler requires explicit --write-enabled")
    if args.command == "run":
        active = store.active()
        if active is None or not isinstance(active.get("schedule_sha256"), str):
            raise ValueError("no saved active schedule")
        digest = active["schedule_sha256"]
    else:
        digest = args.schedule_sha256
    plan = store.load_plan(digest)
    backend = LiveBackend(
        master_member=args.master_member,
        slave_member=args.slave_member,
        upper_member=args.upper_member,
        lower_member=args.lower_member,
    )
    scheduler = ExternalManualScheduler(plan=plan, store=store, backend=backend)
    with store.lock():
        if args.command == "resume-relink":
            result = await scheduler.resume_unattempted_relink(
                datetime.now(ZONE), current_time=lambda: datetime.now(ZONE)
            )
            return {"outcome": "resumed", "schedule_sha256": plan.sha256, **result}
        if args.command != "run":
            result = await scheduler.activate(
                datetime.now(ZONE),
                replace_paused=args.replace_paused,
                current_time=lambda: datetime.now(ZONE),
            )
            print(json.dumps({"outcome": "activated", "active": result}), flush=True)
            if args.command == "activate":
                return {"outcome": "activated", "schedule_sha256": plan.sha256}
        try:
            await _run_loop(scheduler)
        except Exception:
            active = store.active()
            if active is not None and active.get("status") == "active":
                active["status"] = "paused"
                store.set_active(active)
            raise
    raise AssertionError("scheduler loop must run until interrupted")


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_main_async(args))
    except (Exception, KeyboardInterrupt) as error:
        # Endpoint errors can contain private addresses: never print their string.
        print(
            json.dumps(
                {
                    "outcome": "stopped",
                    "error_type": type(error).__name__,
                    "next_step": "inspect_parent_and_child_journals_and_fresh_pair",
                }
            ),
            flush=True,
        )
        return 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
