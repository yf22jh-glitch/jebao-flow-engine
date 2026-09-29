"""Daily Pro manual-control plan; parsing this module never contacts a device."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Seoul")
_MAX_BYTES = 32 * 1024
_MODES_WITH_FREQUENCY = frozenset({"pulse", "sine"})
_MODES_WITHOUT_FREQUENCY = frozenset({"tidal", "constant"})
_MODES = _MODES_WITH_FREQUENCY | _MODES_WITHOUT_FREQUENCY | {"feed"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manual schedule has duplicate JSON keys")
        result[key] = value
    return result


def local_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("schedule time must include a timezone")
    return value.astimezone(ZONE)


@dataclass(frozen=True, slots=True)
class ManualRow:
    minute: int
    mode: str
    upper_flow: int | None
    lower_flow: int | None
    frequency: int | None

    @property
    def label(self) -> str:
        return f"{self.minute // 60:02d}:{self.minute % 60:02d}"

    @property
    def feeding(self) -> bool:
        return self.mode == "feed"


@dataclass(frozen=True, slots=True)
class ManualSchedule:
    rows: tuple[ManualRow, ...]
    sha256: str
    source_table: str
    upper_identity_sha256: str
    lower_identity_sha256: str
    canonical: bytes

    def current(self, now: datetime) -> ManualRow:
        current = local_time(now)
        minute = current.hour * 60 + current.minute
        index = bisect_right(tuple(row.minute for row in self.rows), minute) - 1
        return self.rows[index]

    def boundary(self, now: datetime) -> datetime:
        current = local_time(now)
        for row in self.rows:
            candidate = datetime.combine(
                current.date(), time(row.minute // 60, row.minute % 60), ZONE
            )
            if candidate > current:
                return candidate
        return datetime.combine(current.date() + timedelta(days=1), time.min, ZONE)

    def event_key(self, now: datetime, row: ManualRow | None = None) -> str:
        current = local_time(now)
        selected = row or self.current(current)
        return f"pro_manual_{current:%Y%m%d}_{selected.minute:04d}_{self.sha256[:16]}"


def parse_schedule(payload: bytes) -> ManualSchedule:
    if not payload or len(payload) > _MAX_BYTES:
        raise ValueError("manual schedule is empty or too large")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("manual schedule must be UTF-8 JSON") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "timezone",
        "source_table",
        "upper_identity_sha256",
        "lower_identity_sha256",
        "rows",
    }:
        raise ValueError("manual schedule has unexpected top-level fields")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("unsupported manual schedule version")
    if document["timezone"] != "Asia/Seoul":
        raise ValueError("manual schedule timezone must be Asia/Seoul")
    source_table = document["source_table"]
    if not isinstance(source_table, str) or not source_table or not source_table.isascii():
        raise ValueError("manual schedule needs a nonempty ASCII source table")
    upper_identity = document["upper_identity_sha256"]
    lower_identity = document["lower_identity_sha256"]
    for identity in (upper_identity, lower_identity):
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or any(character not in "0123456789abcdef" for character in identity)
        ):
            raise ValueError("manual schedule needs exact physical identity SHA-256 bindings")
    if upper_identity == lower_identity:
        raise ValueError("manual schedule upper and lower devices must be distinct")
    raw_rows = document["rows"]
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= 96:
        raise ValueError("manual schedule requires 1..96 rows")
    rows: list[ManualRow] = []
    for entry in raw_rows:
        if not isinstance(entry, dict):
            raise ValueError("manual schedule row must be an object")
        mode = entry.get("mode")
        if not isinstance(mode, str) or mode not in _MODES:
            raise ValueError("manual schedule mode is not supported")
        required = (
            {"time", "mode"} if mode == "feed" else {"time", "mode", "upper_flow", "lower_flow"}
        )
        if mode in _MODES_WITH_FREQUENCY:
            required.add("frequency")
        if set(entry) != required:
            raise ValueError("manual schedule row fields do not match its mode")
        label = entry["time"]
        if (
            not isinstance(label, str)
            or len(label) != 5
            or label[2] != ":"
            or not (label[:2] + label[3:]).isascii()
            or not (label[:2] + label[3:]).isdigit()
        ):
            raise ValueError("manual schedule time must be HH:MM")
        hour, minute = int(label[:2]), int(label[3:])
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError("manual schedule time is outside one day")
        minute_of_day = hour * 60 + minute
        if rows and minute_of_day <= rows[-1].minute:
            raise ValueError("manual schedule rows must have unique increasing times")
        upper = entry.get("upper_flow")
        lower = entry.get("lower_flow")
        frequency = entry.get("frequency")
        if mode != "feed":
            for flow in (upper, lower):
                if type(flow) is not int or not 30 <= flow <= 45:
                    raise ValueError("Pro Flow must be an integer within 30..45")
        if mode in _MODES_WITH_FREQUENCY and (
            type(frequency) is not int or not 5 <= frequency <= 100
        ):
            raise ValueError("pulse and sine require Frequency in 5..100")
        rows.append(ManualRow(minute_of_day, mode, upper, lower, frequency))
    if rows[0].minute != 0 or rows[0].feeding:
        raise ValueError("00:00 must begin with a running row")
    for index, row in enumerate(rows):
        if row.feeding:
            following = rows[(index + 1) % len(rows)]
            following_minute = following.minute + (1440 if index + 1 == len(rows) else 0)
            if following.feeding or following_minute - row.minute != 6:
                raise ValueError("every Feeding row needs a running row exactly six minutes later")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    encoded = canonical.encode("ascii")
    return ManualSchedule(
        tuple(rows),
        hashlib.sha256(encoded).hexdigest(),
        source_table,
        upper_identity,
        lower_identity,
        encoded,
    )


def load_schedule(path: Path) -> ManualSchedule:
    return parse_schedule(path.read_bytes())
