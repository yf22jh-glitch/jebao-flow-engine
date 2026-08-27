from __future__ import annotations

from collections.abc import Iterable

import pytest

from jebao_flow.devices.lan import LanJebaoDevice
from jebao_flow.protocol.models import ScheduleEntry
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE,
    LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE,
    LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE,
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_END,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET,
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    LocalWavemakerProScheduleSnapshot,
    ScheduleWireValidationError,
    build_local_wavemaker_pro_schedule_control_payload,
    build_schedule_control_payload,
    decode_local_wavemaker_pro_slot_wire,
    encode_local_wavemaker_pro_schedule_entry,
    extract_local_wavemaker_pro_schedule_image,
    get_local_wavemaker_pro_slot_wire,
    local_wavemaker_pro_schedule_datapoint_id,
    local_wavemaker_pro_schedule_status_offset,
    patch_local_wavemaker_pro_schedule_slot,
    validate_local_wavemaker_pro_schedule_image,
    validate_local_wavemaker_pro_slot_wire,
)


def _entry(
    *,
    slot: int = 0,
    start: str = "08:01",
    end: str = "22:00",
    mode: str = "sine",
    mode_code: int = 1,
    flow: int | bool = 38,
    frequency: int | bool = 27,
    feed_time: int | bool = 0,
    custom_frequency: int | bool = 12,
    extra_parameters: dict[str, int | bool] | None = None,
) -> ScheduleEntry:
    parameters: dict[str, int | bool] = {
        "flow": flow,
        "frequency": frequency,
        "feed_time": feed_time,
        "custom_frequency": custom_frequency,
    }
    if extra_parameters:
        parameters.update(extra_parameters)
    return ScheduleEntry(
        slot=slot,
        start=start,
        end=end,
        mode=mode,
        mode_code=mode_code,
        parameters=parameters,
    )


def _valid_wire(*, slot: int = 0, flow: int = 38) -> bytes:
    return encode_local_wavemaker_pro_schedule_entry(_entry(slot=slot, flow=flow))


def _alternating_sentinel_image() -> bytes:
    return b"".join(
        LOCAL_WAVEMAKER_PRO_UNUSED_ZERO
        if index % 2 == 0
        else LOCAL_WAVEMAKER_PRO_UNUSED_EE
        for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    )


def test_wire_geometry_matches_all_48_vendor_autotime_datapoints() -> None:
    assert LOCAL_WAVEMAKER_PRO_SLOT_COUNT == 48
    assert LOCAL_WAVEMAKER_PRO_SLOT_SIZE == 9
    assert LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE == 432
    assert LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET == 11
    assert LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_END == 443

    assert [local_wavemaker_pro_schedule_datapoint_id(index) for index in range(48)] == list(
        range(13, 61)
    )
    assert local_wavemaker_pro_schedule_status_offset(0) == 11
    assert local_wavemaker_pro_schedule_status_offset(47) == 434


@pytest.mark.parametrize("slot_index", [-1, 48])
def test_wire_geometry_rejects_out_of_range_slot_indices(slot_index: int) -> None:
    with pytest.raises(ScheduleWireValidationError, match="0..47"):
        local_wavemaker_pro_schedule_datapoint_id(slot_index)


@pytest.mark.parametrize("slot_index", [True, 1.0, "1"])
def test_wire_geometry_rejects_non_integer_slot_indices(slot_index: object) -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        local_wavemaker_pro_schedule_status_offset(slot_index)  # type: ignore[arg-type]


def test_extracts_exact_schedule_image_without_device_clock_bytes() -> None:
    raw_status = bytearray(index % 256 for index in range(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE))
    raw_status[443:451] = bytes.fromhex("141a081b000c2238")

    image = extract_local_wavemaker_pro_schedule_image(memoryview(raw_status))

    assert isinstance(image, bytes)
    assert len(image) == 432
    assert image == bytes(raw_status[11:443])
    assert bytes.fromhex("141a081b000c2238") not in image

    raw_status[11] ^= 0xFF
    assert image != bytes(raw_status[11:443])


@pytest.mark.parametrize("size", [451, 453])
def test_schedule_image_extraction_requires_complete_status(size: int) -> None:
    with pytest.raises(ScheduleWireValidationError, match="exactly 452 bytes"):
        extract_local_wavemaker_pro_schedule_image(bytes(size))


def test_schedule_image_extraction_rejects_non_bytes_like_input() -> None:
    with pytest.raises(TypeError, match="must be bytes-like"):
        extract_local_wavemaker_pro_schedule_image("not-wire-bytes")  # type: ignore[arg-type]


def test_encodes_and_decodes_entry_with_existing_schedule_semantics() -> None:
    entry = _entry(slot=5)

    wire = encode_local_wavemaker_pro_schedule_entry(entry)
    decoded = decode_local_wavemaker_pro_slot_wire(wire, slot_index=5)

    assert wire == bytes((8, 1, 22, 0, 1, 38, 27, 0, 12))
    assert decoded is not None
    assert decoded.model_dump() == entry.model_dump()
    assert validate_local_wavemaker_pro_slot_wire(bytearray(wire)) == wire


def test_encodes_feed_slot_with_24_hour_end() -> None:
    entry = _entry(
        start="22:00",
        end="24:00",
        mode="feed",
        mode_code=7,
        flow=0,
        frequency=0,
        feed_time=15,
        custom_frequency=0,
    )

    assert encode_local_wavemaker_pro_schedule_entry(entry) == bytes(
        (22, 0, 24, 0, 7, 0, 0, 15, 0)
    )


@pytest.mark.parametrize(
    "sentinel",
    [LOCAL_WAVEMAKER_PRO_UNUSED_ZERO, LOCAL_WAVEMAKER_PRO_UNUSED_EE],
)
def test_both_unused_slot_sentinels_are_valid_and_remain_distinct(sentinel: bytes) -> None:
    assert validate_local_wavemaker_pro_slot_wire(sentinel) == sentinel
    assert decode_local_wavemaker_pro_slot_wire(sentinel) is None


@pytest.mark.parametrize(
    ("wire", "message"),
    [
        (bytes((24, 0, 1, 0, 2, 30, 0, 0, 0)), "invalid time or mode"),
        (bytes((0, 0, 24, 1, 2, 30, 0, 0, 0)), "invalid time or mode"),
        (bytes((0, 0, 1, 0, 9, 30, 0, 0, 0)), "invalid time or mode"),
        (bytes((0, 0, 1, 0, 2, 101, 0, 0, 0)), "flow must be in 0..100"),
        (bytes((0, 0, 1, 0, 2, 30, 101, 0, 0)), "frequency must be in 0..100"),
        (bytes((0, 0, 1, 0, 2, 30, 0, 61, 0)), "feed_time must be in 0..60"),
        (bytes((0, 0, 1, 0, 2, 30, 0, 0, 101)), "custom_frequency must be"),
        (bytes((0, 0, 1, 0, 7, 0, 0, 0, 0)), "feed schedule slots require"),
    ],
)
def test_slot_validation_rejects_invalid_wire_values(wire: bytes, message: str) -> None:
    with pytest.raises(ScheduleWireValidationError, match=message):
        validate_local_wavemaker_pro_slot_wire(wire)


def test_slot_validation_requires_exact_nine_bytes() -> None:
    for wire in (bytes(8), bytes(10)):
        with pytest.raises(ScheduleWireValidationError, match="exactly 9 bytes"):
            validate_local_wavemaker_pro_slot_wire(wire)


def test_entry_encoder_rejects_mode_code_mismatch() -> None:
    with pytest.raises(ScheduleWireValidationError, match="mode/code mismatch"):
        encode_local_wavemaker_pro_schedule_entry(_entry(mode="constant", mode_code=1))


def test_entry_encoder_rejects_unknown_mode() -> None:
    with pytest.raises(ScheduleWireValidationError, match="mode must be one of"):
        encode_local_wavemaker_pro_schedule_entry(_entry(mode="future_mode", mode_code=1))


def test_entry_encoder_rejects_missing_and_extra_parameters() -> None:
    missing = _entry()
    missing_parameters = dict(missing.parameters)
    del missing_parameters["frequency"]
    missing = missing.model_copy(update={"parameters": missing_parameters})
    with pytest.raises(ScheduleWireValidationError, match="missing frequency"):
        encode_local_wavemaker_pro_schedule_entry(missing)

    with pytest.raises(ScheduleWireValidationError, match="unexpected vendor_private"):
        encode_local_wavemaker_pro_schedule_entry(
            _entry(extra_parameters={"vendor_private": 1})
        )


def test_entry_encoder_rejects_boolean_numeric_parameter() -> None:
    with pytest.raises(TypeError, match="flow must be an integer"):
        encode_local_wavemaker_pro_schedule_entry(_entry(flow=True))


def test_entry_encoder_rejects_active_value_that_collides_with_zero_sentinel() -> None:
    entry = _entry(
        start="00:00",
        end="00:00",
        mode="pulse",
        mode_code=0,
        flow=0,
        frequency=0,
        feed_time=0,
        custom_frequency=0,
    )

    with pytest.raises(ScheduleWireValidationError, match="unused-slot sentinel"):
        encode_local_wavemaker_pro_schedule_entry(entry)


def test_validates_complete_image_with_mixed_sentinels_and_active_slots() -> None:
    image = _alternating_sentinel_image()
    image = patch_local_wavemaker_pro_schedule_slot(image, 2, _valid_wire(slot=2))
    image = patch_local_wavemaker_pro_schedule_slot(image, 47, _valid_wire(slot=47, flow=55))

    assert validate_local_wavemaker_pro_schedule_image(memoryview(image)) == image

    malformed = bytearray(image)
    malformed[10 * 9 : 11 * 9] = bytes((25, 0, 1, 0, 2, 30, 0, 0, 0))
    with pytest.raises(ScheduleWireValidationError, match="schedule slot 10"):
        validate_local_wavemaker_pro_schedule_image(malformed)


def test_complete_image_validation_requires_exact_432_bytes() -> None:
    for image in (bytes(431), bytes(433)):
        with pytest.raises(ScheduleWireValidationError, match="exactly 432 bytes"):
            validate_local_wavemaker_pro_schedule_image(image)


def test_slot_patch_changes_only_selected_nine_bytes_and_does_not_mutate_source() -> None:
    source = bytearray(_alternating_sentinel_image())
    original = bytes(source)
    replacement = _valid_wire(slot=17, flow=44)

    patched = patch_local_wavemaker_pro_schedule_slot(source, 17, replacement)
    start = 17 * 9

    assert bytes(source) == original
    assert patched[:start] == original[:start]
    assert patched[start : start + 9] == replacement
    assert patched[start + 9 :] == original[start + 9 :]
    assert get_local_wavemaker_pro_slot_wire(patched, 0) == LOCAL_WAVEMAKER_PRO_UNUSED_ZERO
    assert get_local_wavemaker_pro_slot_wire(patched, 1) == LOCAL_WAVEMAKER_PRO_UNUSED_EE


def test_slot_patch_preserves_requested_unused_sentinel_byte_for_byte() -> None:
    image = patch_local_wavemaker_pro_schedule_slot(
        _alternating_sentinel_image(),
        4,
        _valid_wire(slot=4),
    )

    zero_cleared = patch_local_wavemaker_pro_schedule_slot(
        image, 4, LOCAL_WAVEMAKER_PRO_UNUSED_ZERO
    )
    ee_cleared = patch_local_wavemaker_pro_schedule_slot(
        image, 4, LOCAL_WAVEMAKER_PRO_UNUSED_EE
    )

    assert get_local_wavemaker_pro_slot_wire(zero_cleared, 4) == bytes(9)
    assert get_local_wavemaker_pro_slot_wire(ee_cleared, 4) == bytes([0xEE]) * 9


def test_snapshot_is_immutable_and_can_patch_entry_without_clock_bytes() -> None:
    raw_status = bytearray(452)
    raw_status[11:443] = _alternating_sentinel_image()
    raw_status[443:451] = bytes.fromhex("141a081b000c2238")
    snapshot = LocalWavemakerProScheduleSnapshot.from_status(raw_status)

    patched = snapshot.with_entry(_entry(slot=9, flow=51))

    assert len(snapshot.image) == 432
    assert snapshot.slot_wire(9) == LOCAL_WAVEMAKER_PRO_UNUSED_EE
    assert patched.slot_wire(9) == _valid_wire(slot=9, flow=51)
    assert snapshot.image != patched.image
    assert bytes.fromhex("141a081b000c2238") not in patched.image
    assert snapshot.image.hex() not in repr(snapshot)
    assert patched.image.hex() not in repr(patched)
    assert patched.validate() is patched


def test_single_slot_control_payload_sets_only_dp13_and_its_value_span() -> None:
    wire = _valid_wire(slot=0)

    payload = build_schedule_control_payload({0: wire})

    assert len(payload) == LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE == 460
    assert payload[0] == 1
    reversed_flags = payload[1 : 1 + LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE]
    natural_flags = reversed_flags[::-1]
    assert natural_flags == bytes((0, 0x20, 0, 0, 0, 0, 0, 0))

    values = payload[1 + LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE :]
    assert len(values) == LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE == 451
    assert values[:11] == bytes(11)
    assert values[11:20] == wire
    assert values[20:] == bytes(451 - 20)


def test_multi_slot_control_payload_sets_first_and_last_schedule_datapoints_only() -> None:
    first = _valid_wire(slot=0, flow=31)
    last = _valid_wire(slot=47, flow=47)

    payload = build_local_wavemaker_pro_schedule_control_payload([(47, last), (0, first)])

    natural_flags = payload[1:9][::-1]
    assert natural_flags == bytes((0, 0x20, 0, 0, 0, 0, 0, 0x10))
    values = payload[9:]
    assert values[:11] == bytes(11)
    assert values[11:20] == first
    assert values[20:434] == bytes(414)
    assert values[434:443] == last
    # Device YMD/HMS occupies offsets 443..450 and must never enter this write.
    assert values[443:451] == bytes(8)


def test_control_payload_does_not_add_schedule_attributes_to_operational_schema() -> None:
    assert all(
        not attribute.name.startswith("AutoTime")
        for attribute in LOCAL_WAVEMAKER_PRO.attributes
    )
    assert LOCAL_WAVEMAKER_PRO.by_name("AutoFlow").position.byte_offset == 7


def test_actual_device_state_never_exposes_raw_schedule_bytes_as_observed_attributes() -> None:
    raw_status = bytearray(LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE)
    raw_status[0] = 0b11  # SwitchON and TimerON
    raw_status[1] = 2  # constant
    raw_status[2] = 35
    raw_status[3] = 20
    raw_status[11:443] = bytes([0xEE]) * 432
    raw_status[11:20] = _valid_wire(slot=0)
    values = LOCAL_WAVEMAKER_PRO.decode_status(bytes(raw_status))
    schedule = decode_schedule(
        LOCAL_WAVEMAKER_PRO.product_key,
        bytes(raw_status),
        enabled=True,
    )
    device = LanJebaoDevice("test-device", "pump.invalid", LOCAL_WAVEMAKER_PRO.product_key)

    state = device._to_device_state(values, schedule=schedule)  # noqa: SLF001

    assert state.schedule is not None
    assert state.schedule.entries[0].parameters["flow"] == 38
    assert not any(name.startswith("AutoTime") for name in state.observed_attributes)
    assert not any(
        isinstance(value, (bytes, bytearray, memoryview))
        for value in state.observed_attributes.values()
    )


@pytest.mark.parametrize("patches", [{}, []])
def test_control_payload_rejects_empty_patch_set(
    patches: dict[int, bytes] | list[tuple[int, bytes]],
) -> None:
    with pytest.raises(ScheduleWireValidationError, match="at least one"):
        build_schedule_control_payload(patches)


def test_control_payload_rejects_duplicate_slot_in_iterable() -> None:
    wire = _valid_wire()
    patches: Iterable[tuple[int, bytes]] = iter(((3, wire), (3, wire)))

    with pytest.raises(ScheduleWireValidationError, match="duplicate schedule slot 3"):
        build_schedule_control_payload(patches)


def test_control_payload_reports_the_actual_invalid_slot_index() -> None:
    malformed = bytes((24, 0, 1, 0, 2, 30, 0, 0, 0))

    with pytest.raises(ScheduleWireValidationError, match="schedule slot 47"):
        build_schedule_control_payload({47: malformed})


@pytest.mark.parametrize(
    ("patches", "error_type", "message"),
    [
        ([(48, bytes(9))], ScheduleWireValidationError, "0..47"),
        ([(True, bytes(9))], TypeError, "must be an integer"),
        ([(1, bytes(8))], ScheduleWireValidationError, "exactly 9 bytes"),
        ([(1, bytes((0, 0, 1, 0, 9, 30, 0, 0, 0)))], ScheduleWireValidationError, "invalid"),
        ([bytes(9)], TypeError, "index/value pair"),
        (b"not pairs", TypeError, "mapping or iterable of pairs"),
    ],
)
def test_control_payload_rejects_malformed_patch_input(
    patches: object,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        build_schedule_control_payload(patches)  # type: ignore[arg-type]
