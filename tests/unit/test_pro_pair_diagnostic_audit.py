"""Pure checks for the passive Pro pair incident index."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from monitoring.pro_pair_diagnostic_audit import PairTimeline, _source_health

T0 = datetime(2026, 9, 24, 3, 3, tzinfo=UTC)
MASTER = "upper"
SLAVE = "lower"
PREFIX = "test"


def _topic(member: str, function: str) -> str:
    return f"{PREFIX}/local/devices/{member}/functions/{function}/state"


def _put(
    timeline: PairTimeline,
    member: str,
    function: str,
    value: object,
    *,
    at: datetime = T0,
    retained: bool = False,
):
    payload = json.dumps(value).encode()
    return timeline.observe(_topic(member, function), payload, at=at, retained=retained)


def _ready(
    timeline: PairTimeline,
    *,
    master_auto: str = "feed",
    slave_manual: str = "feed",
    master_clock: str = "2026-09-24T12:03:00",
    slave_clock: str = "2026-09-22T12:03:00",
    retained: bool = False,
):
    events = []
    for member, role, mode, auto, clock in (
        (MASTER, "master", "pulse", master_auto, master_clock),
        (SLAVE, "async_slave", slave_manual, "constant", slave_clock),
    ):
        for function, value in (
            ("power", "ON"),
            ("schedule_enable", "ON"),
            ("linkage_role", role),
            ("manual_mode", mode),
            ("current_schedule_state", {"observed": {"AutoMode": auto, "AutoFlow": 40}}),
            ("schedule", {"observed": {"device_local_time": clock}}),
        ):
            events.extend(_put(timeline, member, function, value, retained=retained))
    return events


def _condition_events(events, name: str):
    return [
        item
        for item in events
        if item["kind"] == "condition_transition" and item["condition"] == name
    ]


def test_retained_state_is_never_treated_as_fresh_evidence():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    assert _ready(timeline, retained=True) == []
    assert timeline.conditions["telemetry_fresh"] is False
    assert timeline.conditions["feed_outside_master_feeding"] is None


def test_republished_old_source_timestamp_is_not_fresh_device_evidence():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    _ready(timeline)
    changes = _put(
        timeline,
        SLAVE,
        "schedule",
        {
            "observed_at": (T0 - timedelta(minutes=10)).isoformat(),
            "observed": {"device_local_time": "2026-09-22T12:03:00"},
        },
        at=T0 + timedelta(seconds=10),
    )
    assert _condition_events(changes, "telemetry_fresh")[0]["current"] is False
    assert timeline.snapshot(T0 + timedelta(seconds=10))[SLAVE]["schedule"]["fresh"] is False


def test_unplanned_feed_starts_after_scheduled_feeding_and_ends_on_mode_change():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    first = _ready(timeline)
    assert _condition_events(first, "slave_manual_feed")[0]["current"] is True
    assert _condition_events(first, "feed_outside_master_feeding") == []

    changed = _put(
        timeline,
        MASTER,
        "current_schedule_state",
        {"observed": {"AutoMode": "pulse", "AutoFlow": 40}},
        at=T0 + timedelta(seconds=15),
    )
    [event] = _condition_events(changed, "feed_outside_master_feeding")
    assert event["previous"] is False and event["current"] is True
    assert (
        event["snapshot"][SLAVE]["current_schedule_state"]["value"]["observed"]["AutoMode"]
        == "constant"
    )
    assert event["trigger_payload_base64"]
    assert event["evidence_scope"] == "mqtt_publication_not_raw_device_frame"

    ended = _put(timeline, SLAVE, "manual_mode", "constant", at=T0 + timedelta(seconds=30))
    assert _condition_events(ended, "feed_outside_master_feeding")[0]["current"] is False
    assert _condition_events(ended, "slave_manual_feed")[0]["current"] is False


def test_second_feeding_window_does_not_falsely_report_manual_feed_recovery():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    _ready(timeline, master_auto="pulse")
    changes = _put(
        timeline,
        MASTER,
        "current_schedule_state",
        {"observed": {"AutoMode": "feed", "AutoFlow": 0}},
        at=T0 + timedelta(seconds=10),
    )
    assert _condition_events(changes, "feed_outside_master_feeding")[0]["current"] is False
    assert _condition_events(changes, "slave_manual_feed") == []
    assert timeline.conditions["slave_manual_feed"] is True


def test_reported_clock_date_mismatch_has_separate_enter_and_clear():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    events = _ready(timeline)
    [entered] = _condition_events(events, "reported_date_mismatch")
    assert entered["current"] is True
    assert (
        entered["snapshot"][SLAVE]["schedule"]["value"]["device_local_time"]
        == "2026-09-22T12:03:00"
    )
    cleared = _put(
        timeline,
        SLAVE,
        "schedule",
        {"observed": {"device_local_time": "2026-09-24T12:04:00"}},
        at=T0 + timedelta(seconds=30),
    )
    assert _condition_events(cleared, "reported_date_mismatch")[0]["current"] is False


def test_stale_telemetry_is_recorded_as_gap_not_as_feed_exit():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    _ready(timeline, master_auto="pulse")
    events = timeline.evaluate(T0 + timedelta(seconds=61))
    assert _condition_events(events, "telemetry_fresh")[0]["current"] is False
    assert _condition_events(events, "feed_outside_master_feeding")[0]["current"] is None
    assert _condition_events(events, "slave_manual_feed")[0]["current"] is None


def test_slave_manual_feed_is_captured_when_master_telemetry_is_missing():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    events = _put(timeline, SLAVE, "manual_mode", "feed")
    assert _condition_events(events, "slave_manual_feed")[0]["current"] is True
    assert _condition_events(events, "feed_outside_master_feeding") == []
    assert timeline.conditions["telemetry_fresh"] is False


def test_other_topics_do_not_enter_the_timeline():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    assert (
        timeline.observe(
            f"{PREFIX}/local/devices/{SLAVE}/functions/manual_mode/set",
            b"feed",
            at=T0,
            retained=False,
        )
        == []
    )
    assert timeline.samples == {}


def test_topic_set_contains_only_the_two_members_read_only_state_topics():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    assert len(timeline.topics()) == 18
    assert all(topic.endswith("/state") for topic in timeline.topics())
    assert not any(topic.endswith("/set") for topic in timeline.topics())


def test_companion_liveness_requires_evidence_and_connected_heartbeat():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "state").mkdir()
        (root / "ingress").mkdir()
        (root / "state" / "000001-snapshot.json").touch()
        (root / "ingress" / "00000001-message.json").touch()
        heartbeat = root / "ingress" / "heartbeat-20260924T030300000000-x.json"
        heartbeat.write_text(json.dumps({"connected": True}))
        fresh = T0.timestamp() - 30
        stale = T0.timestamp() - 500
        os.utime(root / "state" / "000001-snapshot.json", (fresh, fresh))
        os.utime(root / "ingress" / "00000001-message.json", (stale, stale))
        os.utime(heartbeat, (fresh, fresh))
        # A fresh error file must not impersonate a fresh evidence sample.
        (root / "ingress" / "connection-error-new.json").touch()
        with patch.dict(
            os.environ,
            {
                "JEBAO_DIAGNOSTIC_STATE_ARTIFACT": "state",
                "JEBAO_DIAGNOSTIC_INGRESS_ARTIFACT": "ingress",
            },
        ):
            health = _source_health(root, T0)
            assert health["full_state"]["fresh"] is True
            assert health["mqtt_ingress"]["fresh"] is True
            assert health["mqtt_ingress"]["connected"] is True
            heartbeat.write_text(json.dumps({"connected": False}))
            assert _source_health(root, T0)["mqtt_ingress"]["fresh"] is False
            heartbeat.write_text(json.dumps({"connected": True}))
            os.utime(heartbeat, (stale, stale))
            assert _source_health(root, T0)["mqtt_ingress"]["fresh"] is False
            heartbeat.write_text("{")
            os.utime(heartbeat, (fresh, fresh))
            assert _source_health(root, T0)["mqtt_ingress"]["fresh"] is False


def test_manual_feed_is_recorded_even_if_the_slave_is_not_linked():
    timeline = PairTimeline(PREFIX, MASTER, SLAVE)
    _ready(timeline, master_auto="pulse")
    changes = _put(timeline, SLAVE, "linkage_role", "independent", at=T0 + timedelta(seconds=1))
    assert timeline.conditions["slave_manual_feed"] is True
    assert _condition_events(changes, "feed_outside_master_feeding")[0]["current"] is False
