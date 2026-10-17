"""Persistent operator override for Feeding, using the qualified scalar coordinator.

No firmware timer semantics are invented here. Readback observes Feeding; a later
observed exit is a new correction, never a replay of an uncertain send. This is
software supervision, not electrical isolation or proof of zero shaft movement.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict
from datetime import UTC, datetime

from jebao_flow.local_control.external_manual_runtime import Child, _canonical
from jebao_flow.local_control.external_manual_schedule import parse_schedule


class Maintenance:
    def __init__(self, editor):
        self.editor = editor
        self.store = editor.store
        self.backend = editor.backend
        self.path = self.store.root / "maintenance.json"
        self.last_poll = None

    def state(self) -> dict:
        return json.loads(self.store._read(self.path)) if self.path.exists() else {
            "enabled": False, "phase": "off", "generation": "0" * 64,
        }

    def save(self, state: dict) -> None:
        self.store._replace(self.path, _canonical(state))

    def public(self) -> dict:
        state = self.state()
        return {key: state.get(key) for key in (
            "enabled", "phase", "generation", "started_at", "verified_at", "error_type",
            "observed", "corrections",
            "stage", "failure_stage",
        )} | {"available": getattr(self.backend, "bar", None) is not None,
             "poll_seconds": 15, "physical_isolation": False}

    def pause_schedule(self) -> None:
        active = self.store.active()
        active["status"] = "paused"
        self.store.set_active(active)

    def unfinished(self) -> bool:
        return getattr(self.backend, "maintenance_unfinished", self.backend.unfinished)()

    def fail(self, error: BaseException) -> None:
        state = self.state()
        if state["enabled"]:
            self.pause_schedule()
            state.update(phase="error", error_type=type(error).__name__, verified_at=None,
                         failure_stage=state.get("stage", "state_query"))
            self.save(state)

    async def initialize(self) -> bool:
        state = self.state()
        if not state["enabled"]:
            return False
        self.pause_schedule()  # Includes a crash after activation but before override clearance.
        if state["phase"] != "feeding":
            self.fail(RuntimeError("interrupted maintenance operation"))
        else:
            # No restart replays of any pending child. The editor reconciles reads first.
            await self.hold(force=True)
        return True

    async def read(self, state: dict):
        pair = await self.backend.fresh_pair()
        self.editor._scheduler(state["resume_sha256"])._require_manual_pair(
            pair, state["images"]
        )
        bar = await self.backend.fresh_bar()
        before = state["bar_before"]
        if (bar["identity"] != before["identity"] or bar["image_sha256"] != before["image_sha256"]
                or not bar["power"] or bar["faults"] or bar["role"] != "independent"):
            raise ValueError("maintenance Bar binding, schedule or operating state changed")
        return pair, bar

    async def send(self, target, function, value, state, label):
        # Parent persistence precedes coordinator persistence and its single physical send.
        key = "pro_manual_maintenance_" + secrets.token_hex(16)
        child = Child(target, function, value, label)
        state["stage"] = label
        self.save(state)
        record = {"kind": "maintenance", "phase": "attempting", "child": asdict(child),
                  "generation": state["generation"]}
        self.store.start_operation(key, record)
        try:
            accepted, writes, digest = await self.backend.execute(child, key)
            completion = self.backend.child_record(key)
            readback = completion is not None and completion[0] == "readback_confirmed"
            no_change = completion is not None and completion[0] == "verified" and writes == 0
            if not accepted or not (writes == 1 or (writes == 0 and readback) or no_change):
                raise ValueError("maintenance child was not applied")
            record.update(phase="verified", journal_sha256=digest,
                          confirmation="readback" if readback else (
                              "already_matches" if no_change else "ack_and_readback"))
        except BaseException as error:
            record.update(phase="stopped", error_type=type(error).__name__)
            raise
        finally:
            self.store.set_operation(key, record)

    async def start(self, now: datetime) -> dict:
        state = self.state()
        if state["enabled"]:
            # A new explicit ON can retry a completed failure after reconciliation;
            # an unresolved coordinator journal still forbids a new physical write.
            if state["phase"] == "feeding":
                await self.hold(force=True)
                return {"outcome": "maintenance_on", "device_writes": 0}
            await self.backend.reconcile_pending_commands()
            if self.unfinished():
                raise ValueError("unfinished maintenance command")
        else:
            if getattr(self.backend, "bar", None) is None:
                raise ValueError("maintenance Bar is not configured")
            active = self.store.active()
            if active["status"] not in {"active", "paused"}:
                raise ValueError("schedule transition is incomplete")
            await self.backend.validate_maintenance()
            await self.backend.validate_plan(self.store.load_plan(active["schedule_sha256"]))
            pair = await self.backend.fresh_pair()
            self.editor._scheduler(active["schedule_sha256"])._require_manual_pair(pair, active["images"])
            bar = await self.backend.fresh_bar()
            if not bar["power"] or bar["faults"] or bar["role"] != "independent":
                raise ValueError("maintenance Bar must be powered and independent without faults")
            state = {"enabled": True, "phase": "starting", "generation": secrets.token_hex(32),
                     "started_at": now.astimezone(UTC).isoformat(), "verified_at": None,
                     "resume_sha256": active["schedule_sha256"], "images": active["images"],
                     "bar_before": bar, "corrections": 0,
                     "template_id": self.editor._meta().get("active_template_id"),
                     "template_revision": self.editor._meta().get("active_template_revision")}
        state.update(phase="starting", error_type=None, failure_stage=None, verified_at=None)
        self.save(state)  # Override first: a crash can never resume the ordinary schedule.
        self.pause_schedule()
        await self.ensure_feeding(state)
        return {"outcome": "maintenance_on"}

    async def ensure_feeding(self, state: dict) -> None:
        if self.unfinished():
            raise ValueError("unfinished maintenance command")
        pair, bar = await self.read(state)
        changed = not bar["feeding"] or bar["timer"] or pair.master.mode != "feed" or pair.slave.mode != "feed"
        if not bar["feeding"]:
            await self.send("bar", "feed_switch", True, state, "bar_feed")
        if bar["timer"]:
            # OFF preserves FeedSwitch; it prevents the next native slot taking over.
            await self.send("bar", "schedule_enable", False, state, "bar_timer_off")
        if pair.master.mode != "feed" or pair.slave.mode != "feed":
            await self.send("master", "manual_mode", "feed", state, "pair_feed")
        pair, bar = await self.read(state)
        state["observed"] = {"master": pair.master.mode, "slave": pair.slave.mode,
                             "bar_feeding": bar["feeding"], "bar_timer": bar["timer"]}
        if pair.master.mode != "feed" or pair.slave.mode != "feed" or not bar["feeding"] or bar["timer"]:
            self.save(state)
            raise ValueError("Feeding did not verify on all three pumps")
        state.update(phase="feeding", stage="all_feeding_verified", error_type=None,
                     verified_at=self.editor.clock().astimezone(UTC).isoformat())
        if changed:
            state["corrections"] += 1
        self.save(state)
        self.last_poll = self.editor.clock()

    async def hold(self, *, force=False) -> None:
        state = self.state()
        if state["phase"] != "feeding":
            return  # Interrupted/failed sends are not automatically replayed.
        now = self.editor.clock()
        if not force and self.last_poll is not None and (now - self.last_poll).total_seconds() < 15:
            return
        # Persist the in-flight phase. A crash during a correction needs explicit user recovery.
        state.update(phase="checking", stage="state_query", verified_at=None)
        self.save(state)
        await self.ensure_feeding(state)

    async def stop(self, now: datetime) -> dict:
        state = self.state()
        if not state["enabled"]:
            return {"outcome": "maintenance_off", "device_writes": 0}
        await self.backend.reconcile_pending_commands()
        if self.unfinished():
            raise ValueError("unfinished maintenance command")
        await self.backend.validate_maintenance()
        await self.read(state)  # Check all original identities and images before any resume.
        state.update(phase="resuming", stage="gmp_resume", verified_at=None, error_type=None)
        self.save(state)
        self.pause_schedule()
        document = json.loads(self.store.load_plan(state["resume_sha256"]).canonical)
        document["source_table"] = "aqua_maintenance_resume_" + secrets.token_hex(16)
        run = parse_schedule(json.dumps(document).encode())
        self.store.save_plan(run)
        # This is an explicit new epoch, not an old boundary or uncertain child replay.
        await self.editor._scheduler(run.sha256).activate(
            self.editor.clock(), replace_paused=True, current_time=self.editor.clock)
        bar = await self.backend.fresh_bar()
        if state["bar_before"]["timer"]:
            # Qualified native Timer ON seeds the saved Bar slot for the CURRENT clock.
            if not bar["timer"] or bar["feeding"]:
                await self.send("bar", "schedule_enable", True, state, "bar_resume")
        elif bar["feeding"]:
            await self.send("bar", "feed_switch", False, state, "bar_feed_off")
        _, bar = await self.read(state)
        if bar["timer"] != state["bar_before"]["timer"] or bar["feeding"]:
            raise ValueError("Bar resume did not verify")
        # Bar I/O can cross a GMP boundary. Never label the earlier row as resumed now.
        scheduler = self.editor._scheduler(run.sha256)
        await scheduler.apply_boundary(self.editor.clock(), current_time=self.editor.clock)
        await scheduler.verify_idle()
        meta = self.editor._meta()
        meta.update(active_template_id=state["template_id"],
                    active_template_revision=state["template_revision"],
                    active_run_sha256=run.sha256, last_error=None)
        self.editor._set_meta(meta)
        state.update(enabled=False, phase="off", generation=secrets.token_hex(32),
                     verified_at=self.editor.clock().astimezone(UTC).isoformat())
        self.save(state)  # Clear only after both schedule domains verify.
        return {"outcome": "maintenance_off", "schedule_sha256": run.sha256}
