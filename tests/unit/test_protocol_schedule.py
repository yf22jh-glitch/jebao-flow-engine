from datetime import datetime

import pytest

from jebao_flow.protocol.profiles import DOSING_PUMP
from jebao_flow.protocol.schedule import (
    AQUARIUM_PUMP_PRODUCT_KEY,
    DC_PUMP_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRODUCT_KEY,
    decode_schedule,
)


def _sentinel_status(
    *,
    size: int,
    slot_offset: int,
    slot_size: int,
    ymd_offset: int,
    hms_offset: int,
    local_time: datetime | None = None,
) -> bytearray:
    raw = bytearray(size)
    raw[slot_offset : slot_offset + 48 * slot_size] = bytes([0xEE]) * (48 * slot_size)
    if local_time is not None:
        raw[ymd_offset : ymd_offset + 4] = bytes(
            (
                local_time.year // 100,
                local_time.year % 100,
                local_time.month,
                local_time.day,
            )
        )
        raw[hms_offset : hms_offset + 4] = bytes(
            (0, local_time.hour, local_time.minute, local_time.second)
        )
    return raw


def test_decodes_observed_dc_schedule_and_device_clock() -> None:
    raw = bytearray(402)
    observed_prefix = bytes.fromhex(
        "030051050a04000501"
        "00000801003c0000"
        "08011600040000f0"
        "1600173b003c0000"
    )
    raw[: len(observed_prefix)] = observed_prefix
    raw[len(observed_prefix) : 393] = bytes([0xEE]) * (393 - len(observed_prefix))
    raw[393:401] = bytes((20, 26, 8, 26, 0, 9, 57, 0))

    schedule = decode_schedule(DC_PUMP_PRO_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    assert schedule.enabled is True
    assert schedule.device_local_time == datetime(2026, 8, 26, 9, 57)
    assert schedule.slot_capacity == 48
    assert schedule.invalid_slots == ()
    assert [entry.model_dump() for entry in schedule.entries] == [
        {
            "slot": 0,
            "start": "00:00",
            "end": "08:01",
            "mode": "constant",
            "mode_code": 0,
            "parameters": {"flow": 60, "frequency": 0, "feed_time": 0},
        },
        {
            "slot": 1,
            "start": "08:01",
            "end": "22:00",
            "mode": "feed",
            "mode_code": 4,
            "parameters": {"flow": 0, "frequency": 0, "feed_time": 240},
        },
        {
            "slot": 2,
            "start": "22:00",
            "end": "23:59",
            "mode": "constant",
            "mode_code": 0,
            "parameters": {"flow": 60, "frequency": 0, "feed_time": 0},
        },
    ]


def test_dc_conflicting_mode_codes_are_not_given_guessed_labels() -> None:
    raw = _sentinel_status(
        size=402,
        slot_offset=9,
        slot_size=8,
        ymd_offset=393,
        hms_offset=397,
    )
    raw[9:17] = bytes((0, 0, 1, 0, 1, 50, 20, 0))
    raw[17:25] = bytes((1, 0, 2, 0, 2, 50, 20, 0))

    schedule = decode_schedule(DC_PUMP_PRO_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    assert [(entry.mode_code, entry.mode) for entry in schedule.entries] == [
        (1, "unverified_1"),
        (2, "unverified_2"),
    ]


def test_decodes_local_wavemaker_end_of_day_and_product_parameters() -> None:
    local_time = datetime(2026, 8, 26, 10, 37, 30)
    raw = _sentinel_status(
        size=401,
        slot_offset=8,
        slot_size=8,
        ymd_offset=392,
        hms_offset=396,
        local_time=local_time,
    )
    raw[8:16] = bytes.fromhex("0000180003646400")

    schedule = decode_schedule(LOCAL_WAVEMAKER_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    assert schedule.device_local_time == local_time
    assert len(schedule.entries) == 1
    entry = schedule.entries[0]
    assert (entry.start, entry.end, entry.mode, entry.mode_code) == (
        "00:00",
        "24:00",
        "random",
        3,
    )
    assert entry.parameters == {"flow": 100, "frequency": 100, "pulse_tide": False}


def test_decodes_pro_slot_by_product_key_not_only_slot_length() -> None:
    local_time = datetime(2026, 8, 26, 10, 37, 30)
    raw = _sentinel_status(
        size=452,
        slot_offset=11,
        slot_size=9,
        ymd_offset=443,
        hms_offset=447,
        local_time=local_time,
    )
    raw[11:20] = bytes.fromhex("000002000128280000")
    raw[20:29] = bytes.fromhex("02000837022d000000")
    raw[29:38] = bytes.fromhex("0837090a0700000f00")

    schedule = decode_schedule(LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    assert [entry.mode for entry in schedule.entries] == ["sine", "constant", "feed"]
    assert schedule.entries[0].parameters == {
        "flow": 40,
        "frequency": 40,
        "feed_time": 0,
        "custom_frequency": 0,
    }
    assert schedule.entries[2].parameters["feed_time"] == 15


def test_aquarium_full_day_default_uses_gears_semantics() -> None:
    raw = _sentinel_status(
        size=302,
        slot_offset=13,
        slot_size=6,
        ymd_offset=5,
        hms_offset=9,
        local_time=datetime(2026, 8, 26, 10, 37, 30),
    )
    raw[13:19] = bytes.fromhex("000000000164")

    schedule = decode_schedule(AQUARIUM_PUMP_PRODUCT_KEY, raw, enabled=False)

    assert schedule is not None
    assert schedule.enabled is False
    assert schedule.entries[0].start == schedule.entries[0].end == "00:00"
    assert schedule.entries[0].mode == "auto"
    assert schedule.entries[0].parameters == {"gears": 100}


def test_unused_sentinels_are_ignored_and_malformed_slots_are_reported() -> None:
    raw = _sentinel_status(
        size=452,
        slot_offset=11,
        slot_size=9,
        ymd_offset=443,
        hms_offset=447,
    )
    raw[11:20] = bytes(9)
    raw[20:29] = bytes((0, 0, 24, 1, 2, 30, 0, 0, 0))
    raw[29:38] = bytes((24, 0, 1, 0, 2, 30, 0, 0, 0))
    raw[38:47] = bytes((0, 0, 1, 0, 99, 30, 0, 0, 0))

    schedule = decode_schedule(LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    assert schedule.entries == ()
    assert schedule.invalid_slots == (1, 2, 3)
    assert schedule.device_local_time is None


def test_invalid_device_clock_does_not_discard_valid_entries() -> None:
    raw = _sentinel_status(
        size=302,
        slot_offset=13,
        slot_size=6,
        ymd_offset=5,
        hms_offset=9,
    )
    raw[5:13] = bytes((20, 26, 2, 30, 0, 12, 0, 0))
    raw[13:19] = bytes.fromhex("000001000132")

    schedule = decode_schedule(AQUARIUM_PUMP_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    assert schedule.device_local_time is None
    assert schedule.entries[0].parameters == {"gears": 50}


def test_dosing_and_unknown_products_have_no_decoded_schedule() -> None:
    assert decode_schedule(DOSING_PUMP.product_key, b"", enabled=True) is None
    assert decode_schedule("f" * 32, b"", enabled=True) is None


def test_known_product_requires_its_complete_status_buffer() -> None:
    with pytest.raises(ValueError, match="must be 402 bytes"):
        decode_schedule(DC_PUMP_PRO_PRODUCT_KEY, bytes(401), enabled=True)


def test_schedule_model_dump_never_contains_raw_or_hex_fields() -> None:
    raw = _sentinel_status(
        size=401,
        slot_offset=8,
        slot_size=8,
        ymd_offset=392,
        hms_offset=396,
    )
    raw[8:16] = bytes.fromhex("0100020004320000")
    schedule = decode_schedule(LOCAL_WAVEMAKER_PRODUCT_KEY, raw, enabled=True)

    assert schedule is not None
    payload = schedule.model_dump(mode="json")
    keys = set(payload)
    keys.update(payload["entries"][0]["parameters"])
    assert all("raw" not in key.lower() and "hex" not in key.lower() for key in keys)
