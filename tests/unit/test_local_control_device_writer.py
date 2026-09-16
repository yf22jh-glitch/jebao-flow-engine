import json
from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    LocalDeviceWriteError,
    build_app_full_schedule_payload,
    build_app_schedule_clear_payload,
    build_app_schedule_slot_payload,
    build_device_clock_payload,
    build_exact_schedule_payload,
    extract_current_schedule_image,
    verify_prepared_readback,
)
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.schedule import CompiledSlaveSchedule
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.operator_capture.schedule_model import NativeLinkageMode
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.profiles import (
    AQUARIUM_PUMP,
    DC_PUMP_PRO,
    LOCAL_WAVEMAKER,
    LOCAL_WAVEMAKER_PRO,
)
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET,
    build_local_wavemaker_pro_schedule_control_payload,
)


def _binding() -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member="wavemaker_left",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key="a" * 64,
        logical_role="left",
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


class _Lease:
    def require_permitted(self) -> None:
        pass

    def require_pre_send_permitted(self) -> None:
        self.require_permitted()


class _Session:
    def __init__(self, address: str) -> None:
        self.address = address
        self.statuses: list[bytes] = []
        self.sent: list[bytes] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def authenticate(self) -> bytes:
        return b"private"

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes:
        assert accept_reports is False
        return self.statuses.pop(0)

    async def send_raw_control(self, control_payload: bytes) -> bytes:
        self.sent.append(bytes(control_payload))
        return b"ack"

    async def disconnect(self) -> None:
        self.connected = False

    def quarantine(self) -> None:
        self.connected = False


def _status(*, flow: int = 40, schedule: bytes | None = None, flags: int = 0) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    raw[0] = flags
    raw[2] = flow
    if schedule is not None:
        start = LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET
        raw[start : start + len(schedule)] = schedule
    return bytes(raw)


@pytest.mark.asyncio
async def test_scalar_writer_prepares_fresh_rollback_and_sends_once() -> None:
    session = _Session("runtime-address")
    session.statuses = [_status(flow=40), _status(flow=50)]
    endpoint = ResolvedEndpoint(_binding(), "runtime-address")
    writer = ConnectedDeviceWriter(endpoint, session_factory=lambda address: session)
    current = await writer.connect_and_read()
    intent = LocalControlIntent(
        request_id="request_0001",
        target=_binding(),
        function="manual_flow",
        value=50,
    )

    prepared = writer.prepare_scalar(intent, current)
    await writer.send_once(prepared.forward_payload, _Lease())
    readback = await writer.readback(_Lease())

    assert prepared.no_change is False
    assert prepared.rollback_payload != prepared.forward_payload
    assert verify_prepared_readback(prepared, readback)
    assert len(session.sent) == 1
    with pytest.raises(LocalDeviceWriteError, match="never resends"):
        await writer.send_once(prepared.forward_payload, _Lease())


def test_scalar_writer_uses_the_same_app_declared_flow_range() -> None:
    minimum, maximum = declared_app_integer_range(
        LOCAL_WAVEMAKER_PRO.product_key,
        "manual_flow",
    )
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=_Session,
    )

    prepared = writer.prepare_scalar(
        LocalControlIntent(
            request_id="request_app_maximum",
            target=_binding(),
            function="manual_flow",
            value=maximum,
        ),
        _status(flow=40),
    )

    assert json.loads(prepared.expected_readback) == {"Flow": maximum}
    with pytest.raises(ValueError, match=rf"{minimum}..{maximum}"):
        writer.prepare_scalar(
            LocalControlIntent(
                request_id="request_above_app_maximum",
                target=_binding(),
                function="manual_flow",
                value=maximum + 1,
            ),
            _status(flow=40),
        )


@pytest.mark.parametrize("speed", [30, 35, 100])
def test_aquarium_motor_speed_uses_existing_scalar_rollback_and_readback(speed):
    target = _binding().model_copy(update={"product_key": AQUARIUM_PUMP.product_key})
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(target, "runtime-address"), session_factory=_Session
    )
    baseline = bytearray(AQUARIUM_PUMP.raw_status_size)
    baseline[0] = 1
    baseline[1] = 40
    intent = LocalControlIntent(
        request_id="aquarium_speed_01", target=target, function="motor_speed", value=speed
    )
    prepared = writer.prepare_scalar(intent, bytes(baseline))
    assert prepared.forward_payload == build_control_payload(AQUARIUM_PUMP, {"Motor_Speed": speed})
    assert prepared.rollback_payload == build_control_payload(AQUARIUM_PUMP, {"Motor_Speed": 40})
    assert json.loads(prepared.expected_readback) == {"Motor_Speed": speed}
    assert verify_prepared_readback(prepared, bytes(baseline)) is (speed == 40)
    baseline[1] = speed
    assert verify_prepared_readback(prepared, bytes(baseline))


@pytest.mark.parametrize("speed", [0, 29, 101, True, 30.5])
def test_aquarium_motor_speed_never_uses_vendor_zero_to_expand_app_range(speed):
    target = _binding().model_copy(update={"product_key": AQUARIUM_PUMP.product_key})
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(target, "runtime-address"), session_factory=_Session
    )
    with pytest.raises((TypeError, ValueError)):
        writer.prepare_scalar(
            LocalControlIntent(
                request_id="aquarium_speed_bad", target=target, function="motor_speed", value=speed
            ),
            bytes(AQUARIUM_PUMP.raw_status_size),
        )


@pytest.mark.asyncio
async def test_scalar_target_does_not_gate_on_a_new_attestation_timestamp() -> None:
    session = _Session("runtime-address")
    session.statuses = [_status(flow=40)]
    current_binding = _binding()
    reattested = current_binding.model_copy(
        update={
            "role_attested_by": "replacement_operator",
            "role_attested_at": datetime(2026, 9, 9, tzinfo=UTC),
        }
    )
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(current_binding, "runtime-address"),
        session_factory=lambda address: session,
    )
    current = await writer.connect_and_read()

    prepared = writer.prepare_scalar(
        LocalControlIntent(
            request_id="request_reattested",
            target=reattested,
            function="manual_flow",
            value=50,
        ),
        current,
    )

    assert prepared.target.target.physical_identity_key == current_binding.physical_identity_key


@pytest.mark.asyncio
async def test_scalar_writer_never_exposes_current_auto_fields_as_controls() -> None:
    session = _Session("runtime-address")
    session.statuses = [_status()]
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=lambda address: session,
    )
    current = await writer.connect_and_read()

    with pytest.raises(LocalDeviceWriteError, match="no audited scalar encoder"):
        writer.prepare_scalar(
            LocalControlIntent(
                request_id="request_0002",
                target=_binding(),
                function="current_schedule_state",
            ),
            current,
        )


def test_schedule_writer_preserves_app_feed_bytes_beyond_old_decoder_range() -> None:
    feed_slot = bytes((0, 0, 1, 0, 7, 0, 0, 120, 0))
    desired = feed_slot + bytes([0xEE]) * (432 - len(feed_slot))
    baseline = bytes([0xEE]) * 432
    session = _Session("runtime-address")
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=lambda address: session,
    )
    compiled = CompiledSlaveSchedule(
        linkage_mode=NativeLinkageMode.ASYNC,
        logical_member=_binding().logical_member,
        product_key=_binding().product_key,
        physical_identity_key=_binding().physical_identity_key,
        master_image_sha256="b" * 64,
        image_sha256="c" * 64,
        slots_differing_from_master=(),
        slots_changed_from_current=(),
        image=desired,
    )

    prepared = writer.prepare_pro_schedule(compiled, _status(schedule=baseline))

    assert prepared.expected_readback == desired
    assert prepared.baseline_readback == baseline
    assert len(prepared.forward_payload) == 460


def test_exact_schedule_encoder_matches_existing_builder_for_valid_slots() -> None:
    slot = bytes((0, 0, 1, 0, 2, 40, 20, 0, 10))
    image = slot + bytes([0xEE]) * (432 - len(slot))
    session = _Session("runtime-address")
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=lambda address: session,
    )
    compiled = CompiledSlaveSchedule(
        linkage_mode=NativeLinkageMode.SYNC,
        logical_member="wavemaker_left",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key="a" * 64,
        master_image_sha256="b" * 64,
        image_sha256="c" * 64,
        slots_differing_from_master=(),
        slots_changed_from_current=(),
        image=image,
    )

    prepared = writer.prepare_pro_schedule(compiled, _status(schedule=image))
    expected = build_local_wavemaker_pro_schedule_control_payload(
        {index: image[index * 9 : (index + 1) * 9] for index in range(48)}
    )

    assert prepared.forward_payload == expected


def test_pro_schedule_enable_matches_the_audited_multi_field_app_command() -> None:
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=_Session,
    )
    # SwitchON=true, TimerON=false, Linkage=async_slave.
    current = _status(flags=0b00001101)
    intent = LocalControlIntent(
        request_id="request_timer_app_equivalent",
        target=_binding(),
        function="schedule_enable",
        value=True,
    )

    prepared = writer.prepare_scalar(intent, current)

    assert json.loads(prepared.expected_readback) == {
        "Linkage": "independent",
        "SwitchON": False,
        "TimerON": True,
    }
    assert prepared.forward_payload == build_control_payload(
        LOCAL_WAVEMAKER_PRO,
        {"TimerON": True, "SwitchON": False, "Linkage": "independent"},
    )
    assert prepared.rollback_payload == build_control_payload(
        LOCAL_WAVEMAKER_PRO,
        {"TimerON": False, "SwitchON": True, "Linkage": "async_slave"},
    )


def test_pro_cross_flow_app_label_maps_to_the_stable_protocol_identity() -> None:
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=_Session,
    )
    current = bytearray(_status())
    current[1] = 2
    intent = LocalControlIntent(
        request_id="request_cross_flow_label",
        target=_binding(),
        function="manual_mode",
        value="Cross-flow",
    )

    prepared = writer.prepare_scalar(intent, bytes(current))

    assert json.loads(prepared.expected_readback) == {"Mode": "circulation"}
    assert prepared.forward_payload == build_control_payload(
        LOCAL_WAVEMAKER_PRO,
        {"Mode": "circulation"},
    )


def test_pro_schedule_reset_uses_one_full_0xee_image_and_exact_rollback() -> None:
    baseline = bytes(range(9)) + bytes([0xEE]) * (432 - 9)
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(_binding(), "runtime-address"),
        session_factory=_Session,
    )
    intent = LocalControlIntent(
        request_id="request_schedule_reset",
        target=_binding(),
        function="schedule_reset",
    )

    prepared = writer.prepare_scalar(intent, _status(schedule=baseline))

    reset = bytes([0xEE]) * 432
    assert prepared.function == "schedule_reset"
    assert prepared.expected_readback == reset
    assert prepared.baseline_readback == baseline
    assert prepared.forward_payload == build_exact_schedule_payload(
        LOCAL_WAVEMAKER_PRO.product_key,
        reset,
    )
    assert prepared.rollback_payload == build_exact_schedule_payload(
        LOCAL_WAVEMAKER_PRO.product_key,
        baseline,
    )


def test_dc_pro_schedule_reset_uses_the_vendor_eight_byte_sentinel() -> None:
    target = _binding().model_copy(update={"product_key": DC_PUMP_PRO.product_key})
    current = bytearray(DC_PUMP_PRO.raw_status_size)
    baseline = bytes(range(8)) + bytes([0xEE]) * (48 * 8 - 8)
    current[9 : 9 + len(baseline)] = baseline
    writer = ConnectedDeviceWriter(
        ResolvedEndpoint(target, "runtime-address"),
        session_factory=_Session,
    )

    prepared = writer.prepare_scalar(
        LocalControlIntent(
            request_id="request_dc_schedule_reset",
            target=target,
            function="schedule_reset",
        ),
        bytes(current),
    )

    reset = bytes([0xEE]) * (48 * 8)
    assert prepared.expected_readback == reset
    assert prepared.forward_payload == build_exact_schedule_payload(
        DC_PUMP_PRO.product_key,
        reset,
    )


@pytest.mark.parametrize(
    ("schema", "offset", "slot_size"),
    (
        (LOCAL_WAVEMAKER_PRO, 11, 9),
        (LOCAL_WAVEMAKER, 8, 8),
        (DC_PUMP_PRO, 9, 8),
        (AQUARIUM_PUMP, 13, 6),
    ),
)
def test_all_schedule_products_build_and_extract_full_exact_images(
    schema,
    offset: int,
    slot_size: int,
) -> None:
    image = bytes((index % 251) + 1 for index in range(48 * slot_size))
    status = bytearray(schema.raw_status_size)
    status[offset : offset + len(image)] = image

    payload = build_exact_schedule_payload(schema.product_key, image)

    expected = build_control_payload(
        schema,
        {
            f"AutoTime{index:02d}": image[index * slot_size : (index + 1) * slot_size]
            for index in range(48)
        },
    )
    assert payload == expected
    assert extract_current_schedule_image(schema.product_key, status) == image


@pytest.mark.parametrize(
    "schema",
    (LOCAL_WAVEMAKER_PRO, LOCAL_WAVEMAKER, DC_PUMP_PRO, AQUARIUM_PUMP),
)
def test_all_schedule_products_build_the_static_confirmed_clock_shape(schema) -> None:
    moment = datetime(2026, 9, 8, 13, 14, 15, tzinfo=UTC)

    payload = build_device_clock_payload(schema.product_key, moment)

    assert payload == build_control_payload(
        schema,
        {"YMDData": bytes((20, 26, 9, 8)), "HMSData": bytes((0, 13, 14, 15))},
    )


@pytest.mark.parametrize("schema", (LOCAL_WAVEMAKER_PRO, DC_PUMP_PRO))
def test_pro_families_save_and_clear_one_selected_schedule_slot(schema) -> None:
    slot_size = 9 if schema is LOCAL_WAVEMAKER_PRO else 8
    image = bytes(range(slot_size)) + bytes([0xEE]) * (47 * slot_size)

    desired, clear_payload = build_app_schedule_clear_payload(
        schema.product_key,
        image,
        0,
    )

    sentinel = bytes([0xEE]) * slot_size
    assert desired[:slot_size] == sentinel
    assert clear_payload == build_app_schedule_slot_payload(schema.product_key, 0, sentinel)
    assert clear_payload == build_control_payload(schema, {"AutoTime00": sentinel})


@pytest.mark.parametrize("schema", (LOCAL_WAVEMAKER, AQUARIUM_PUMP))
def test_bar_and_aquarium_clear_by_saving_the_complete_schedule_image(schema) -> None:
    slot_size = 8 if schema is LOCAL_WAVEMAKER else 6
    image = bytes(range(slot_size)) + bytes([0xEE]) * (47 * slot_size)

    desired, clear_payload = build_app_schedule_clear_payload(
        schema.product_key,
        image,
        0,
    )

    assert desired == bytes([0xEE]) * (48 * slot_size)
    assert clear_payload == build_app_full_schedule_payload(schema.product_key, desired)
    assert clear_payload == build_exact_schedule_payload(schema.product_key, desired)
