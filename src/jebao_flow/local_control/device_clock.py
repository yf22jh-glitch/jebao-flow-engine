"""Clock-setting evidence, never an admission gate for other controls.

Compare elapsed time from the explicitly requested setting, not an old device snapshot.
Recovery only observes: sending an original timestamp again would reset the clock backwards.
"""

import json
from datetime import UTC, datetime, timedelta

READBACK_TOLERANCE_SECONDS = 5


def clock_expectation(local_time: datetime, prepared_at: datetime) -> bytes:
    for value in (local_time, prepared_at):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock setting requires timezone-aware timestamps")
    return json.dumps(
        {
            "local_time": local_time.replace(microsecond=0).isoformat(),
            "prepared_at": prepared_at.astimezone(UTC).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def clock_readback_matches(expected: bytes, decoded, *, observed_at: datetime) -> bool:
    evidence = json.loads(expected)
    requested = datetime.fromisoformat(evidence["local_time"])
    prepared = datetime.fromisoformat(evidence["prepared_at"])
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("clock readback observation must be timezone-aware")
    elapsed = (observed_at - prepared).total_seconds()
    if elapsed < 0:
        return False
    try:
        century, year, month, day = decoded["YMDData"]
        _prefix, hour, minute, second = decoded["HMSData"]
        observed = datetime(
            century * 100 + year, month, day, hour, minute, second, tzinfo=requested.tzinfo
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        abs((observed - (requested + timedelta(seconds=elapsed))).total_seconds())
        <= READBACK_TOLERANCE_SECONDS
    )
