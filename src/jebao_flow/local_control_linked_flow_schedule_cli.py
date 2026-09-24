"""Daily operator schedule for the observed Pro master-release/Flow/relink command.

The device's stored waveform schedule remains independent. Feeding rows intentionally have
no manual Flow write. Starting a loop never applies the already-active row retroactively;
only a new boundary or an explicit attended ``--apply-current`` can request a change.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jebao_flow import local_control_linked_flow_cli as linked_flow

_LOCAL_ZONE = ZoneInfo("Asia/Seoul")
_MAX_SCHEDULE_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class FlowEvent:
    minute: int
    flow: int | None

    @property
    def label(self) -> str:
        return f"{self.minute // 60:02d}:{self.minute % 60:02d}"


@dataclass(frozen=True, slots=True)
class DailyFlowSchedule:
    events: tuple[FlowEvent, ...]
    sha256: str
    source_run: str

    def current(self, now: datetime) -> FlowEvent:
        local = _require_local_time(now)
        minute = local.hour * 60 + local.minute
        index = bisect_right(tuple(event.minute for event in self.events), minute) - 1
        return self.events[index]

    def next_boundary(self, now: datetime) -> datetime:
        local = _require_local_time(now)
        for event in self.events:
            candidate = datetime.combine(
                local.date(), time(event.minute // 60, event.minute % 60), _LOCAL_ZONE
            )
            if candidate > local:
                return candidate
        return datetime.combine(local.date() + timedelta(days=1), time.min, _LOCAL_ZONE)


def _require_local_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("schedule time must have a timezone")
    return value.astimezone(_LOCAL_ZONE)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("daily Flow schedule contains duplicate JSON keys")
        result[key] = value
    return result


def load_schedule(path: Path) -> DailyFlowSchedule:
    source = path.read_bytes()
    if not source or len(source) > _MAX_SCHEDULE_BYTES:
        raise ValueError("daily Flow schedule is empty or too large")
    try:
        document = json.loads(source.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("daily Flow schedule is not valid UTF-8 JSON") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "timezone",
        "source_run",
        "events",
    }:
        raise ValueError("daily Flow schedule has unexpected top-level fields")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("unsupported daily Flow schedule version")
    if document["timezone"] != "Asia/Seoul":
        raise ValueError("daily Flow schedule must use Asia/Seoul")
    if not isinstance(document["source_run"], str) or not document["source_run"].isascii():
        raise ValueError("daily Flow schedule needs an ASCII source run label")
    raw_events = document["events"]
    if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 48:
        raise ValueError("daily Flow schedule requires 1..48 events")
    events: list[FlowEvent] = []
    for row in raw_events:
        if not isinstance(row, dict) or set(row) != {"time", "flow"}:
            raise ValueError("daily Flow event must contain only time and flow")
        label = row["time"]
        if (
            not isinstance(label, str)
            or len(label) != 5
            or label[2] != ":"
            or not (label[:2] + label[3:]).isascii()
            or not (label[:2] + label[3:]).isdigit()
        ):
            raise ValueError("daily Flow event time must be HH:MM")
        hour, minute = int(label[:2]), int(label[3:])
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError("daily Flow event time is outside one day")
        minute_of_day = 60 * hour + minute
        if events and minute_of_day <= events[-1].minute:
            raise ValueError("daily Flow event times must increase without duplicates")
        flow = row["flow"]
        if flow is not None and (type(flow) is not int or not 30 <= flow <= 45):
            raise ValueError("manual Flow must be 30..45 or null for Feeding")
        events.append(FlowEvent(minute_of_day, flow))
    if events[0].minute != 0 or all(event.flow is None for event in events):
        raise ValueError("daily Flow schedule needs 00:00 and a running interval")
    prior_flow: int | None = None
    for event in events:
        if event.flow is None:
            continue
        if prior_flow is not None and abs(event.flow - prior_flow) > 5:
            raise ValueError("adjacent running Flow targets differ by more than five")
        prior_flow = event.flow
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return DailyFlowSchedule(
        events=tuple(events),
        sha256=hashlib.sha256(canonical.encode("ascii")).hexdigest(),
        source_run=document["source_run"],
    )


def _event_key(schedule: DailyFlowSchedule, now: datetime) -> str:
    local = _require_local_time(now)
    event = schedule.current(local)
    return f"{local:%Y%m%d}_{event.minute:04d}_{schedule.sha256[:12]}"


async def _apply_event(
    schedule: DailyFlowSchedule,
    event: FlowEvent,
    now: datetime,
    *,
    master_member: str,
    slave_member: str,
) -> dict[str, object]:
    if event.flow is None:
        return {"outcome": "feeding_no_flow_write", "event": event.label, "device_writes": 0}
    request_id = "linked_flow_" + _event_key(schedule, now)
    result = await linked_flow._run(
        argparse.Namespace(
            master_member=master_member,
            slave_member=slave_member,
            flow=event.flow,
            request_id=request_id,
            write_enabled=True,
        )
    )
    return {"event": event.label, "request_id": request_id, **result}


async def run_once(args: argparse.Namespace, *, now: datetime | None = None) -> dict[str, object]:
    schedule = load_schedule(args.schedule_file)
    local = _require_local_time(now or datetime.now(_LOCAL_ZONE))
    event = schedule.current(local)
    due = event.minute == local.hour * 60 + local.minute
    if not args.write_enabled:
        return {
            "outcome": "preview_only",
            "event": event.label,
            "flow": event.flow,
            "due_now": due,
            "next_event": schedule.next_boundary(local).isoformat(),
            "schedule_sha256": schedule.sha256,
            "device_writes": 0,
        }
    if args.at is not None:
        raise ValueError("synthetic time cannot authorize a hardware write")
    if not due and not args.apply_current:
        return {"outcome": "not_due", "event": event.label, "device_writes": 0}
    return await _apply_event(
        schedule,
        event,
        local,
        master_member=args.master_member,
        slave_member=args.slave_member,
    )


async def run_loop(args: argparse.Namespace) -> None:
    if args.at is not None or args.apply_current:
        raise ValueError("loop mode cannot use synthetic time or apply-current")
    schedule = load_schedule(args.schedule_file)
    previous = datetime.now(_LOCAL_ZONE)
    seen = _event_key(schedule, previous)
    print(
        json.dumps(
            {
                "outcome": "armed_for_next_boundary",
                "next_event": schedule.next_boundary(previous).isoformat(),
                "write_enabled": args.write_enabled,
                "schedule_sha256": schedule.sha256,
            }
        ),
        flush=True,
    )
    while True:
        current = datetime.now(_LOCAL_ZONE)
        if current < previous:
            raise ValueError("host wall clock moved backward; stop instead of replaying a row")
        previous = current
        key = _event_key(schedule, current)
        if key != seen:
            seen = key
            event = schedule.current(current)
            if not args.write_enabled:
                result: dict[str, object] = {
                    "outcome": "preview_boundary",
                    "event": event.label,
                    "flow": event.flow,
                    "device_writes": 0,
                }
            else:
                result = await _apply_event(
                    schedule,
                    event,
                    current,
                    master_member=args.master_member,
                    slave_member=args.slave_member,
                )
            print(json.dumps(result, sort_keys=True), flush=True)
        delay = min(30.0, max(0.1, (schedule.next_boundary(current) - current).total_seconds()))
        await asyncio.sleep(delay)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule-file", required=True, type=Path)
    parser.add_argument("--master-member", required=True)
    parser.add_argument("--slave-member", required=True)
    parser.add_argument("--write-enabled", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--apply-current", action="store_true")
    parser.add_argument("--at", help="read-only ISO-8601 preview time")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        preview_time = datetime.fromisoformat(args.at) if args.at is not None else None
        if args.loop:
            asyncio.run(run_loop(args))
            return 0
        result = asyncio.run(run_once(args, now=preview_time))
    except (Exception, KeyboardInterrupt) as error:
        # An endpoint error can contain a private address; expose only the error class.
        print(
            json.dumps(
                {
                    "outcome": "stopped",
                    "error": type(error).__name__,
                    "next_step": "inspect_child_journals_and_fresh_pair_before_retry",
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
