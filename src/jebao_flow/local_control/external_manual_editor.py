"""Versioned schedule editing; only explicit activation reaches the existing actuator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from jebao_flow.local_control.external_manual_runtime import (
    ExternalManualScheduler,
    SchedulerBackend,
    SchedulerStore,
    _canonical,
)
from jebao_flow.local_control.external_manual_schedule import MANUAL_MODES, ZONE, parse_schedule
from jebao_flow.local_control.maintenance import Maintenance

_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MESSAGE = 40 * 1024
_MAX_TEMPLATES = 32
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])\Z")


class EditorRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EditorRejected("invalid_request")
        result[key] = value
    return result


def companion_rows(value: object) -> list[dict]:
    """Validate template metadata; native Bar apply remains a separate qualified API."""
    if not isinstance(value, list) or len(value) > 96:
        raise EditorRejected("invalid_mcp_schedule")
    minutes = []
    rows = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("time"), str):
            raise EditorRejected("invalid_mcp_schedule")
        time = _TIME.fullmatch(row["time"])
        mode = row.get("mode")
        if time is None or not isinstance(mode, str) or mode not in {
            "pulse", "sine", "random", "tidal", "constant", "feed", "stopped"
        }:
            raise EditorRejected("invalid_mcp_schedule")
        expected = {"time", "mode"} if mode == "feed" else {"time", "mode", "flow"}
        if mode in {"pulse", "sine", "tidal"}:
            expected.add("frequency")
        if set(row) != expected:
            raise EditorRejected("invalid_mcp_schedule")
        for field, lower, upper in (("flow", 0, 100), ("frequency", 5, 100)):
            if field in row and (type(row[field]) is not int or not lower <= row[field] <= upper):
                raise EditorRejected("invalid_mcp_schedule")
        minutes.append(int(time[1]) * 60 + int(time[2]))
        rows.append(dict(row))
    if rows:
        if minutes[0] != 0 or rows[0]["mode"] == "feed":
            raise EditorRejected("invalid_mcp_schedule")
        if any(a >= b for a, b in zip(minutes, minutes[1:], strict=False)):
            raise EditorRejected("invalid_mcp_schedule")
        for index, row in enumerate(rows):
            if row["mode"] == "feed":
                next_index = (index + 1) % len(rows)
                end = minutes[next_index] + (1440 if next_index == 0 else 0)
                if rows[next_index]["mode"] == "feed" or end - minutes[index] != 6:
                    raise EditorRejected("invalid_mcp_schedule")
    return rows


def decode_request(payload: bytes, now: datetime, *, check_age: bool = True) -> dict:
    if len(payload) > _MAX_MESSAGE:
        raise EditorRejected("invalid_request")
    try:
        request = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(request, dict):
            raise ValueError
        if request["schema_version"] != 1 or type(request["schema_version"]) is not int:
            raise ValueError
        if not isinstance(request["request_id"], str) or not _ID.fullmatch(request["request_id"]):
            raise ValueError
        issued = datetime.fromisoformat(request["issued_at"])
        if issued.tzinfo is None:
            raise ValueError
        age = (now.astimezone(UTC) - issued.astimezone(UTC)).total_seconds()
        if check_age and not -10 <= age <= 120:
            raise EditorRejected("expired_request")
        extra = {
            "save": {"rows", "expected_draft"},
            "activate": {"schedule_sha256", "expected_active"},
            "pause": {"expected_active"},
            "maintenance_on": {"expected_active", "expected_maintenance"},
            "maintenance_off": {"expected_active", "expected_maintenance"},
            "template_create": {"name", "rows", "expected_library"},
            "template_select": {"template_id", "expected_library"},
            "template_rename": {"template_id", "name", "expected_library"},
            "template_delete": {"template_id", "expected_library"},
        }[request["operation"]]
        optional = {"template_id"} if request["operation"] in {"save", "activate"} else set()
        if request["operation"] in {"save", "template_create"}:
            optional.add("mcp_rows")
        if request["operation"] == "save":
            optional.update({"expected_library", "expected_mcp"})
        fields = set(request) - {"schema_version", "request_id", "issued_at", "operation"}
        if not extra <= fields or not fields <= extra | optional:
            raise ValueError
        for field in fields - {"rows", "mcp_rows", "name", "template_id"}:
            if not isinstance(request[field], str) or not _DIGEST.fullmatch(request[field]):
                raise ValueError
        if "template_id" in request and (
            not isinstance(request["template_id"], str) or not _ID.fullmatch(request["template_id"])
        ):
            raise ValueError
        if "name" in request:
            name = request["name"]
            if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 48
                    or any(ord(c) < 32 or ord(c) == 127 for c in name)):
                raise EditorRejected("invalid_template_name")
        return request
    except EditorRejected:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise EditorRejected("invalid_request") from error


class ScheduleEditor:
    """Single actor under the runner's lifetime flock; never cancels a physical action."""

    def __init__(
        self,
        store: SchedulerStore,
        backend: SchedulerBackend,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        if hasattr(backend, "read_retry"):
            backend.read_retry.status_sink = store.set_communication
            store.set_communication(None)  # A previous process's retry is not a live query.
        self.clock = clock or (lambda: datetime.now(ZONE))
        self.operation_lock = asyncio.Lock()
        self.busy: str | None = None
        self._last_idle_at: datetime | None = None
        self._last_clock: datetime | None = None
        self._path = store.root / "editor.json"
        active = store.active()
        if active is None:
            raise ValueError("editor requires the installed, physically bound schedule")
        self.binding = store.load_plan(active["schedule_sha256"])
        if not self._path.exists():
            self._set_meta(
                {"draft_sha256": self.binding.sha256, "last_result": None, "last_error": None}
            )
        self._draft()
        self._migrate_templates()
        self.maintenance = Maintenance(self)

    def _meta(self) -> dict:
        return json.loads(self.store._read(self._path))

    def _set_meta(self, value: dict) -> None:
        self.store._replace(self._path, _canonical(value))

    def _draft(self):
        plan = self.store.load_plan(self._meta()["draft_sha256"])
        if (plan.upper_identity_sha256, plan.lower_identity_sha256) != (
            self.binding.upper_identity_sha256,
            self.binding.lower_identity_sha256,
        ):
            raise ValueError("editor physical binding changed")
        return plan

    def _migrate_templates(self) -> None:
        meta = self._meta()
        if "templates" in meta:
            return
        active = self.store.active()
        assert active is not None
        now = self.clock().astimezone(UTC).isoformat()
        original = "0" * 31 + "1"
        meta["templates"] = {original: {
            "name": "기본 시간표", "schedule_sha256": active["schedule_sha256"],
            "created_at": now, "updated_at": now,
        }}
        selected = original
        if meta["draft_sha256"] != active["schedule_sha256"]:
            selected = "0" * 31 + "2"
            meta["templates"][selected] = {
                "name": "기존 저장본", "schedule_sha256": meta["draft_sha256"],
                "created_at": now, "updated_at": now,
            }
        meta.update(selected_template_id=selected, active_template_id=original,
                    active_template_revision=active["schedule_sha256"],
                    active_run_sha256=active["schedule_sha256"])
        self._set_meta(meta)  # Metadata only: active.json and immutable plans stay byte-exact.

    @staticmethod
    def _library_sha256(meta: dict) -> str:
        return hashlib.sha256(_canonical(meta["templates"])).hexdigest()

    def _template(self, meta: dict, template_id: str) -> dict:
        template = meta["templates"].get(template_id)
        if template is None:
            raise EditorRejected("template_missing")
        plan = self.store.load_plan(template["schedule_sha256"])
        if (plan.upper_identity_sha256, plan.lower_identity_sha256) != (
            self.binding.upper_identity_sha256, self.binding.lower_identity_sha256
        ):
            raise ValueError("template physical binding changed")
        return template

    def _plan_for_rows(self, rows):
        document = json.loads(self.binding.canonical)
        document["source_table"] = f"aqua_ui_{secrets.token_hex(16)}"
        document["rows"] = rows
        try:
            return parse_schedule(json.dumps(document).encode())
        except (ValueError, TypeError, KeyError) as error:
            raise EditorRejected("invalid_schedule") from error

    def _scheduler(self, digest: str) -> ExternalManualScheduler:
        return ExternalManualScheduler(
            plan=self.store.load_plan(digest), store=self.store, backend=self.backend
        )

    def snapshot(self, now: datetime, command_topic: str) -> dict:
        active = self.store.active()
        assert active is not None
        plan = self.store.load_plan(active["schedule_sha256"])
        draft = self._draft()
        row = plan.current(now)
        meta = self._meta()
        active_id = meta.get("active_template_id")
        revision = meta.get("active_template_revision")
        if meta.get("active_run_sha256") != plan.sha256:
            active_id = next((key for key, value in meta["templates"].items()
                              if value["schedule_sha256"] == plan.sha256), None)
            revision = plan.sha256
        active_template = meta["templates"].get(active_id, {})
        mcp_rows = meta["templates"][meta["selected_template_id"]].get("mcp_rows", [])
        communication = self.store.communication()
        return {
            "schema_version": 1,
            "sampled_at": now.astimezone(UTC).isoformat(),
            "status": "busy" if self.busy else active["status"],
            "controller_status": active["status"],
            "busy": self.busy,
            "communication": communication,
            "maintenance": self.maintenance.public(),
            "timezone": "Asia/Seoul",
            "command_topic": command_topic,
            "draft_sha256": draft.sha256,
            "draft_rows": json.loads(draft.canonical)["rows"],
            "draft_mcp_rows": mcp_rows,
            "draft_mcp_sha256": hashlib.sha256(_canonical(mcp_rows)).hexdigest(),
            "mcp_schedule_scope": "stored_reference_only",
            "mcp_editor_modes": ["pulse", "sine", "random", "tidal", "constant", "feed", "stopped"],
            "mcp_max_slots": 48,
            "active_sha256": plan.sha256,
            "active_rows": json.loads(plan.canonical)["rows"],
            "current_time": row.label,
            "current_mode": row.mode,
            "next_boundary_at": active.get("next_boundary_at"),
            "current_row_verified": active.get("status") == "active"
            and not self.maintenance.state()["enabled"]
            and active.get("last_event") == plan.event_key(now)
            and not (communication and communication.get("query")),
            "upper_role": "master" if self.backend.upper_is_master else "async_slave",
            "lower_role": "async_slave" if self.backend.upper_is_master else "master",
            "flow_min": max(
                30, self.backend.master_flow_bounds[0], self.backend.slave_flow_bounds[0]
            ),
            "flow_max": min(
                45, self.backend.master_flow_bounds[1], self.backend.slave_flow_bounds[1]
            ),
            "modes": list(MANUAL_MODES),
            "templates": [{"id": key, "name": value["name"],
                           "schedule_sha256": value["schedule_sha256"],
                           "mcp_row_count": len(value.get("mcp_rows", [])),
                           "row_count": len(self.store.load_plan(value["schedule_sha256"]).rows)}
                          for key, value in meta["templates"].items()],
            "templates_sha256": self._library_sha256(meta),
            "selected_template_id": meta["selected_template_id"],
            "active_template_id": active_id,
            "active_template_name": active_template.get("name", "실행 중 시간표"),
            "active_template_revision": revision,
            "max_templates": _MAX_TEMPLATES,
            "last_result": meta.get("last_result"),
            "last_error": meta.get("last_error"),
        }

    async def initialize(self, now: datetime) -> None:
        if hasattr(self.backend, "reconcile_pending_commands"):
            async with self.operation_lock:
                try:
                    await self.backend.reconcile_pending_commands()
                except Exception as error:
                    self._pause_for_error(error)
        active = self.store.active()
        if self.maintenance.state()["enabled"]:
            async with self.operation_lock:
                try:
                    await self.maintenance.initialize()
                except Exception as error:
                    self.maintenance.fail(error)
            return
        if active is not None and active.get("status") == "active":
            async with self.operation_lock:
                scheduler = self._scheduler(active["schedule_sha256"])
                try:
                    await self.backend.validate_plan(scheduler.plan)
                    await scheduler.apply_boundary(now, current_time=self.clock)
                    await scheduler.verify_idle()
                    self._last_idle_at = now
                except Exception as error:
                    self._pause_for_error(error)

    def _pause_for_error(self, error: Exception) -> None:
        active = self.store.active()
        if active is not None and active.get("status") in {"active", "activating"}:
            active["status"] = "paused"
            self.store.set_active(active)
        meta = self._meta()
        meta["last_error"] = {
            "code": "scheduler_paused",
            "error_type": type(error).__name__,
            "at": self.clock().astimezone(UTC).isoformat(),
        }
        self._set_meta(meta)

    async def tick(self, now: datetime) -> None:
        async with self.operation_lock:
            now = self.clock()  # The actor may have waited behind a complete physical row.
            active = self.store.active()
            if self.maintenance.state()["enabled"]:
                try:
                    await self.maintenance.hold()
                except Exception as error:
                    self.maintenance.fail(error)
                return
            if active is None or active.get("status") != "active":
                self._last_clock = now
                return
            try:
                if self._last_clock is not None and now < self._last_clock:
                    raise ValueError("host wall clock moved backward")
                self._last_clock = now
                scheduler = self._scheduler(active["schedule_sha256"])
                boundary = datetime.fromisoformat(active["next_boundary_at"])
                if now >= boundary:
                    self.busy = "boundary"
                    await scheduler.apply_boundary(now, current_time=self.clock)
                    self._last_idle_at = now
                elif (boundary - now).total_seconds() > 30 and (
                    self._last_idle_at is None or (now - self._last_idle_at).total_seconds() >= 15
                ):
                    await scheduler.verify_idle()
                    self._last_idle_at = now
            except Exception as error:
                self._pause_for_error(error)
            finally:
                self.busy = None

    def request_read_pause(self, payload: bytes, now: datetime) -> None:
        """Let a fresh pause reach read backoff even behind a queued activation.

        The actor still journals/processes the request normally. No physical send
        is interrupted, and a completed request cannot pause a later operation.
        """
        request = decode_request(payload, now)
        if request["operation"] in {"pause", "maintenance_on", "maintenance_off"} and hasattr(self.backend, "read_retry"):
            active = self.store.active()
            if (
                active is not None
                and request["expected_active"] == active["schedule_sha256"]
                and (not request["operation"].startswith("maintenance_")
                     or request["expected_maintenance"] == self.maintenance.state()["generation"])
                and self.store.operation(f"ui_{request['request_id']}") is None
            ):
                self.backend.read_retry.interrupt()

    async def handle(self, payload: bytes, now: datetime) -> dict:
        request = decode_request(payload, now)
        self.request_read_pause(payload, now)
        async with self.operation_lock:
            # Recheck TTL after waiting for a boundary; an old UI intent cannot activate later.
            decode_request(payload, self.clock())
            key = f"ui_{request['request_id']}"
            payload_digest = hashlib.sha256(payload).hexdigest()
            prior = self.store.operation(key)
            if prior is not None:
                if prior.get("payload_sha256") != payload_digest:
                    raise EditorRejected("request_id_conflict")
                result = prior.get("result") or {
                    "request_id": request["request_id"],
                    "operation": request["operation"],
                    "outcome": "error",
                    "code": "already_attempted",
                }
                self._remember(result)
                return result
            record = {
                "kind": "editor_request",
                "phase": "attempting",
                "payload_sha256": payload_digest,
                "created_at": now.isoformat(),
                "request": request,
            }
            self.store.start_operation(key, record)  # fsync before any activation.
            self.busy = request["operation"]
            try:
                result = await self._perform(request, self.clock())
            except EditorRejected as error:
                result = {"outcome": "error", "code": error.code}
            except Exception as error:
                if request["operation"] == "activate":
                    self._pause_for_error(error)
                if request["operation"].startswith("maintenance_"):
                    self.maintenance.fail(error)
                result = {
                    "outcome": "error",
                    "code": "operation_failed",
                    "error_type": type(error).__name__,
                }
            finally:
                self.busy = None
            result = {
                "request_id": request["request_id"],
                "operation": request["operation"],
                **result,
            }
            record.update(phase="completed", result=result)
            self.store.set_operation(key, record)
            self._remember(result)
            return result

    def _remember(self, result: dict) -> None:
        meta = self._meta()
        meta["last_result"] = result
        self._set_meta(meta)

    async def _perform(self, request: dict, now: datetime) -> dict:
        operation = request["operation"]
        meta = self._meta()
        if operation.startswith("template_"):
            return self._manage_template(request, now, meta)
        if "template_id" in request and request["template_id"] != meta["selected_template_id"]:
            raise EditorRejected("template_selection_changed")
        if operation == "save":
            if request["expected_draft"] != self._draft().sha256:
                raise EditorRejected("draft_changed")
            if ("expected_library" in request
                    and request["expected_library"] != self._library_sha256(meta)):
                raise EditorRejected("templates_changed")
            plan = self._plan_for_rows(request["rows"])
            draft = self._draft()
            template = meta["templates"][meta["selected_template_id"]]
            if ("expected_mcp" in request and request["expected_mcp"] != hashlib.sha256(
                _canonical(template.get("mcp_rows", []))
            ).hexdigest()):
                raise EditorRejected("templates_changed")
            mcp_rows = (companion_rows(request["mcp_rows"]) if "mcp_rows" in request
                        else template.get("mcp_rows", []))
            changed = plan.rows != draft.rows or mcp_rows != template.get("mcp_rows", [])
            if plan.rows == draft.rows:
                plan = draft
            else:
                self.store.save_plan(plan)
            if changed:
                meta["draft_sha256"] = plan.sha256
                template.update(schedule_sha256=plan.sha256, mcp_rows=mcp_rows,
                                updated_at=now.astimezone(UTC).isoformat())
                self._set_meta(meta)
            return {"outcome": "saved", "schedule_sha256": plan.sha256, "device_writes": 0}
        active = self.store.active()
        assert active is not None
        if request["expected_active"] != active["schedule_sha256"]:
            raise EditorRejected("active_changed")
        if operation.startswith("maintenance_"):
            if request["expected_maintenance"] != self.maintenance.state()["generation"]:
                raise EditorRejected("maintenance_changed")
            if operation == "maintenance_on":
                return await self.maintenance.start(now)
            return await self.maintenance.stop(now)
        if self.maintenance.state()["enabled"]:
            raise EditorRejected("maintenance_enabled")
        if operation == "pause":
            if active["status"] == "activating":
                raise EditorRejected("activation_incomplete")
            active["status"] = "paused"
            self.store.set_active(active)
            return {"outcome": "paused", "device_writes": 0}
        draft = self._draft()
        if request["schedule_sha256"] != draft.sha256:
            raise EditorRejected("draft_changed")
        current_revision = (
            meta.get("active_run_sha256") == active["schedule_sha256"]
            and meta.get("active_template_revision") == draft.sha256
        ) or active["schedule_sha256"] == draft.sha256
        if current_revision and active["status"] == "active":
            meta.update(active_template_id=meta["selected_template_id"],
                        active_template_revision=draft.sha256,
                        active_run_sha256=active["schedule_sha256"])
            self._set_meta(meta)
            return {"outcome": "already_active", "device_writes": 0}
        if active["status"] not in {"active", "paused"}:
            raise EditorRejected("activation_incomplete")
        active["status"] = "paused"
        self.store.set_active(active)
        prior_plan = self.store.load_plan(active["schedule_sha256"])
        if current_revision and active.get("last_event") == prior_plan.event_key(now):
            scheduler = self._scheduler(prior_plan.sha256)
            await scheduler.resume_verified_row(now, current_time=self.clock)
        else:
            run = draft
            if "template_id" in request:
                # A deliberate A→B→A switch is a new execution epoch, not a replay of
                # the historical A row's single-write journal. The saved template stays immutable.
                document = json.loads(draft.canonical)
                document["source_table"] = f"aqua_template_run_{request['request_id']}"
                run = parse_schedule(json.dumps(document).encode())
                self.store.save_plan(run)
            scheduler = self._scheduler(run.sha256)
            await scheduler.activate(now, replace_paused=True, current_time=self.clock)
        self._last_idle_at = self.clock()
        meta = self._meta()
        meta["last_error"] = None
        actual = self.store.active()
        assert actual is not None
        meta.update(active_template_id=meta["selected_template_id"],
                    active_template_revision=draft.sha256,
                    active_run_sha256=actual["schedule_sha256"])
        self._set_meta(meta)
        return {"outcome": "activated", "schedule_sha256": actual["schedule_sha256"],
                "template_sha256": draft.sha256}

    def _manage_template(self, request: dict, now: datetime, meta: dict) -> dict:
        if request["expected_library"] != self._library_sha256(meta):
            raise EditorRejected("templates_changed")
        operation = request["operation"]
        templates = meta["templates"]
        if operation == "template_create":
            if len(templates) >= _MAX_TEMPLATES:
                raise EditorRejected("templates_full")
            template_id = request["request_id"]
            if template_id in templates:
                raise EditorRejected("template_id_exists")
            name = request["name"].strip()
            if any(t["name"] == name for t in templates.values()):
                raise EditorRejected("template_name_exists")
            mcp_rows = companion_rows(request.get("mcp_rows", []))
            plan = self._plan_for_rows(request["rows"])
            if plan.rows == self._draft().rows:
                plan = self._draft()
            self.store.save_plan(plan)
            templates[template_id] = {"name": name, "schedule_sha256": plan.sha256,
                                      "mcp_rows": mcp_rows,
                                      "created_at": now.astimezone(UTC).isoformat(),
                                      "updated_at": now.astimezone(UTC).isoformat()}
            meta.update(selected_template_id=template_id, draft_sha256=plan.sha256)
        else:
            template_id = request["template_id"]
            template = self._template(meta, template_id)
            if operation == "template_select":
                meta.update(selected_template_id=template_id,
                            draft_sha256=template["schedule_sha256"])
            elif operation == "template_rename":
                name = request["name"].strip()
                if any(key != template_id and t["name"] == name for key, t in templates.items()):
                    raise EditorRejected("template_name_exists")
                template.update(name=name, updated_at=now.astimezone(UTC).isoformat())
            elif operation == "template_delete":
                active = self.store.active()
                active_id = meta.get("active_template_id")
                if active and meta.get("active_run_sha256") != active["schedule_sha256"]:
                    active_id = next(
                        (key for key, value in templates.items()
                         if value["schedule_sha256"] == active["schedule_sha256"]), None
                    )
                if template_id == active_id:
                    raise EditorRejected("template_active")
                if len(templates) <= 1:
                    raise EditorRejected("last_template")
                del templates[template_id]
                if meta["selected_template_id"] == template_id:
                    selected = meta.get("active_template_id")
                    if selected not in templates:
                        selected = next(iter(templates))
                    meta.update(selected_template_id=selected,
                                draft_sha256=templates[selected]["schedule_sha256"])
        self._set_meta(meta)
        outcome = {"template_create": "created", "template_select": "selected",
                   "template_rename": "renamed", "template_delete": "deleted"}[operation]
        return {"outcome": outcome,
                "template_id": template_id, "device_writes": 0}
