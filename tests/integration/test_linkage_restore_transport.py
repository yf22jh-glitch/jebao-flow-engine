import asyncio
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.devices import (
    ControlAckPowerMismatchError,
    ControlAckReadbackError,
    ControlAckResolutionStage,
    ControlVerificationOutcome,
    DeviceControlSnapshot,
    LanJebaoDevice,
    LinkageApplyError,
    LinkageRecoveryAuthority,
    LinkageSafetyInterlock,
    LinkageTestSpec,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    PhysicalDeviceBinding,
    ScheduleActiveLinkageController,
    ScheduleLinkagePhase,
    ScheduleLinkageRunProgressKind,
    ScheduleLinkageSpec,
    ScheduleLinkageStopReason,
    TemporaryLinkageController,
    schedule_structure_fingerprint,
)
from jebao_flow.persistence import JsonLinkageJournalStore, JsonScheduleLinkageJournalStore
from jebao_flow.protocol.codec import MAGIC, GizwitsCommand, encode_frame, read_frame
from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.errors import ProtocolConnectionError
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.schedule_wire import (
    build_local_wavemaker_pro_schedule_control_payload,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION, STATE_REPORT_ACTION, GizwitsSession
from jebao_flow.safety.limits import PowerLimits


def _raw_state(
    *,
    power: int,
    frequency: int,
    linkage: LinkageRole,
    timer_enabled: bool,
) -> bytes:
    raw = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
    linkage_index = LOCAL_WAVEMAKER_PRO.by_name("Linkage").enum_values.index(linkage.value)
    raw[0] = 1 | (int(timer_enabled) << 1) | (linkage_index << 2)
    raw[1] = LOCAL_WAVEMAKER_PRO.by_name("Mode").enum_values.index("constant")
    raw[2] = power
    raw[3] = frequency
    return bytes(raw)


def _scheduled_role_state(
    *,
    power: int,
    before_flow: int,
    after_flow: int,
    after_frequency: int,
    linkage: LinkageRole = LinkageRole.INDEPENDENT,
) -> bytes:
    """Build the two-slot TimerON image used by the role controller field path."""

    raw = bytearray(
        _raw_state(
            power=power,
            frequency=20,
            linkage=linkage,
            timer_enabled=True,
        )
    )
    raw[6] = LOCAL_WAVEMAKER_PRO.by_name("AutoMode").enum_values.index("constant")
    raw[7] = before_flow
    raw[8] = 5
    raw[10] = 15
    raw[11:443] = bytes([0xEE]) * 432
    raw[11:20] = bytes((0, 0, 18, 11, 2, before_flow, 0, 0, 0))
    raw[20:29] = bytes(
        (18, 11, 23, 59, 1, after_flow, after_frequency, 0, 0)
    )
    raw[443:451] = bytes((20, 26, 8, 27, 0, 18, 10, 0))
    return bytes(raw)


def _with_linkage(raw_state: bytes, linkage: LinkageRole) -> bytes:
    changed = bytearray(raw_state)
    role_index = LOCAL_WAVEMAKER_PRO.by_name("Linkage").enum_values.index(
        linkage.value
    )
    changed[0] = (changed[0] & ~0x0C) | (role_index << 2)
    return bytes(changed)


def _binding(device_id: str) -> PhysicalDeviceBinding:
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=f"transport-test-{device_id}",
        mac_address="001122334455" if device_id == "master" else "aabbccddeeff",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        config_fingerprint=("1" if device_id == "master" else "2") * 64,
    )


class _LocalGizwitsPump:
    """Small stateful GAgent peer with deterministic connection-level fault injection."""

    def __init__(
        self,
        initial_state: bytes,
        *,
        lose_timer_on_reply: bool = False,
        fail_first_fresh_state: bool = False,
        state_action: int = STATE_REPLY_ACTION,
        pair_report_with_reply: bool = False,
    ) -> None:
        self.current_state = initial_state
        self.lose_timer_on_reply = lose_timer_on_reply
        self.fail_first_fresh_state = fail_first_fresh_state
        self.state_action = state_action
        self.pair_report_with_reply = pair_report_with_reply
        self.control_states: dict[bytes, bytes] = {}
        self.timer_on_payload: bytes | None = None
        self.accepted_connections = 0
        self.passcode_requests = 0
        self.login_requests = 0
        self.control_sequences: list[int] = []
        self.timer_on_control_sequences: list[int] = []
        self.control_events: list[tuple[int, int, bool, bool]] = []
        self.control_payload_events: list[tuple[int, bytes, float]] = []
        self.state_requests_by_connection: dict[int, int] = {}
        self.state_request_events: list[tuple[int, float]] = []
        self.errors: list[Exception] = []
        self._post_timer_fault_origin: int | None = None
        self._ack_loss_payload: bytes | None = None
        self._ack_loss_origin: int | None = None
        self._ack_resolution_failures_remaining = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._handlers: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("test server is not listening")
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def state_request_count(self) -> int:
        return sum(self.state_requests_by_connection.values())

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)

    def register_control(self, payload: bytes, state: bytes, *, timer_on: bool = False) -> None:
        self.control_states[payload] = state
        if timer_on:
            self.timer_on_payload = payload

    def register_ack_loss_control(
        self,
        payload: bytes,
        state: bytes,
        *,
        failed_resolution_reads: int,
    ) -> None:
        if failed_resolution_reads < 0:
            raise ValueError("failed resolution reads must be non-negative")
        self.register_control(payload, state)
        self._ack_loss_payload = payload
        self._ack_resolution_failures_remaining = failed_resolution_reads

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for writer in tuple(self._writers):
            writer.close()
        if self._writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in tuple(self._writers)),
                return_exceptions=True,
            )
        if self._handlers:
            await asyncio.wait_for(
                asyncio.gather(*tuple(self._handlers), return_exceptions=True),
                timeout=1,
            )

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always owns a server handler task
            raise AssertionError("server handler has no asyncio task")
        self._handlers.add(task)
        self._writers.add(writer)
        self.accepted_connections += 1
        connection_number = self.accepted_connections
        self.state_requests_by_connection[connection_number] = 0
        authenticated = False
        try:
            while True:
                request = await read_frame(reader)
                if request.command == GizwitsCommand.PASSCODE_REQUEST:
                    self.passcode_requests += 1
                    passcode = b"local-test"
                    writer.write(
                        encode_frame(
                            GizwitsCommand.PASSCODE_RESPONSE,
                            struct.pack(">H", len(passcode)) + passcode,
                        )
                    )
                    await writer.drain()
                    continue
                if request.command == GizwitsCommand.LOGIN_REQUEST:
                    self.login_requests += 1
                    writer.write(encode_frame(GizwitsCommand.LOGIN_RESPONSE, b"\x00"))
                    await writer.drain()
                    authenticated = True
                    continue
                if request.command == GizwitsCommand.SERIAL_CONTROL_REQUEST:
                    should_close = await self._handle_control(
                        connection_number,
                        authenticated,
                        request.payload,
                        reader,
                        writer,
                    )
                    if should_close:
                        return
                    continue
                if request.command == GizwitsCommand.SERIAL_TRANSMIT_REQUEST:
                    should_close = await self._handle_state_read(
                        connection_number,
                        reader,
                        writer,
                    )
                    if should_close:
                        return
                    continue
                raise AssertionError(f"unexpected test command 0x{request.command:04x}")
        except ProtocolConnectionError:
            pass
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as error:  # pragma: no cover - asserted empty by the test
            self.errors.append(error)
        finally:
            self._writers.discard(writer)
            self._handlers.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _handle_control(
        self,
        connection_number: int,
        authenticated: bool,
        request_payload: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        if len(request_payload) < 5:
            raise AssertionError("control request is missing sequence or action")
        sequence_bytes = request_payload[:4]
        sequence = int.from_bytes(sequence_bytes, "big")
        control_payload = request_payload[4:]
        try:
            self.current_state = self.control_states[control_payload]
        except KeyError as error:
            raise AssertionError("unexpected control payload") from error
        self.control_sequences.append(sequence)

        is_timer_on = control_payload == self.timer_on_payload
        self.control_events.append(
            (connection_number, sequence, is_timer_on, authenticated)
        )
        self.control_payload_events.append(
            (connection_number, control_payload, asyncio.get_running_loop().time())
        )

        if control_payload == self._ack_loss_payload:
            self._ack_loss_payload = None
            self._ack_loss_origin = connection_number
            # Apply the Flow frame but leave its 0x94 response between magic and length. The
            # production session must classify this as an uncertain ACK and never replay it.
            writer.write(MAGIC)
            await writer.drain()
            try:
                await reader.read()
            except ConnectionResetError:
                pass
            return True

        if is_timer_on:
            self.timer_on_control_sequences.append(sequence)
            if self.fail_first_fresh_state:
                # Bind the injected read fault to the TimerON event, not an absolute connection
                # number. Proactive rollback refresh now owns connection 2; verification must
                # fail only on a later read-only session.
                self._post_timer_fault_origin = connection_number
            if self.lose_timer_on_reply:
                self.lose_timer_on_reply = False
                # Apply the target but strand the client between magic and frame length. The
                # response timeout must quarantine this stream without replaying the control.
                writer.write(MAGIC)
                await writer.drain()
                try:
                    await reader.read()
                except ConnectionResetError:
                    pass
                return True

        writer.write(
            encode_frame(GizwitsCommand.SERIAL_CONTROL_RESPONSE, sequence_bytes + b"ack")
        )
        await writer.drain()
        return False

    async def _handle_state_read(
        self,
        connection_number: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        self.state_requests_by_connection[connection_number] += 1
        self.state_request_events.append(
            (connection_number, asyncio.get_running_loop().time())
        )
        if (
            self._ack_resolution_failures_remaining
            and self._ack_loss_origin is not None
            and connection_number != self._ack_loss_origin
        ):
            self._ack_resolution_failures_remaining -= 1
            # Close without a frame. Each failure therefore belongs to one explicit read on one
            # newly authenticated TCP session, while keeping the integration test fast enough to
            # expose command pacing before rollback.
            return True
        if (
            self.fail_first_fresh_state
            and self._post_timer_fault_origin is not None
            and connection_number != self._post_timer_fault_origin
        ):
            self.fail_first_fresh_state = False
            self._post_timer_fault_origin = None
            # The first read-only verification session also loses its frame boundary. The
            # controller may authenticate one more fresh session, but must not resend TimerON.
            writer.write(MAGIC)
            await writer.drain()
            try:
                await reader.read()
            except ConnectionResetError:
                pass
            return True

        writer.write(
            encode_frame(
                GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                bytes([self.state_action]) + self.current_state,
            )
        )
        if self.pair_report_with_reply and self.state_action == STATE_REPORT_ACTION:
            writer.write(
                encode_frame(
                    GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
                    bytes([STATE_REPLY_ACTION]) + self.current_state,
                )
            )
        await writer.drain()
        return False


def _session_factory(
    port: int,
    *,
    created_sessions: list[GizwitsSession] | None = None,
):
    def create(address: str) -> GizwitsSession:
        session = GizwitsSession(
            address,
            port=port,
            connect_timeout_seconds=0.2,
            response_timeout_seconds=0.05,
        )
        if created_sessions is not None:
            created_sessions.append(session)
        return session

    return create


def _device(
    device_id: str,
    server: _LocalGizwitsPump,
    *,
    minimum_command_interval_ms: int = 100,
    readback_delay_ms: int = 0,
    ack_loss_retry_delay_seconds: float = 0.5,
    created_sessions: list[GizwitsSession] | None = None,
) -> LanJebaoDevice:
    return LanJebaoDevice(
        device_id,
        "127.0.0.1",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        minimum_command_interval_ms=minimum_command_interval_ms,
        readback_delay_ms=readback_delay_ms,
        readback_attempts=1,
        allow_hardware_writes=True,
        physical_binding=_binding(device_id),
        session_factory=_session_factory(
            server.port,
            created_sessions=created_sessions,
        ),
        ack_loss_retry_delay_seconds=ack_loss_retry_delay_seconds,
    )


def _snapshot(device: LanJebaoDevice, *, power: int, frequency: int) -> DeviceControlSnapshot:
    binding = device.physical_binding
    if binding is None:  # pragma: no cover - test device always has an exact binding
        raise AssertionError("test device has no physical binding")
    raw = _raw_state(
        power=power,
        frequency=frequency,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=True,
    )
    schedule = decode_schedule(
        LOCAL_WAVEMAKER_PRO.product_key,
        raw,
        enabled=True,
    )
    return DeviceControlSnapshot(
        device_id=device.device_id,
        physical_binding=binding,
        enabled=True,
        power=power,
        mode="constant",
        frequency=frequency,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=True,
        schedule_fingerprint=schedule_structure_fingerprint(schedule),
    )


def _scheduled_raw_state(
    *,
    power: int,
    frequency: int,
    linkage: LinkageRole,
    timer_enabled: bool,
) -> bytes:
    """Build a status frame whose 14-entry schedule survives every temporary target."""

    raw = bytearray(
        _raw_state(
            power=power,
            frequency=frequency,
            linkage=linkage,
            timer_enabled=timer_enabled,
        )
    )
    raw[11:443] = bytes([0xEE]) * 432
    for slot in range(14):
        offset = 11 + (slot * 9)
        raw[offset : offset + 9] = bytes(
            (slot, 0, slot + 1, 0, 2, 35 + slot, 20, 0, 0)
        )
    raw[443:451] = bytes((20, 26, 8, 27, 0, 12, 0, 0))
    return bytes(raw)


async def test_ack_loss_report_reply_pair_is_retired_before_next_fresh_read_without_replay(
) -> None:
    initial_state = _raw_state(
        power=33,
        frequency=20,
        linkage=LinkageRole.ASYNC_SLAVE,
        timer_enabled=True,
    )
    applied_state = _raw_state(
        power=38,
        frequency=20,
        linkage=LinkageRole.ASYNC_SLAVE,
        timer_enabled=True,
    )
    server = _LocalGizwitsPump(
        initial_state,
        state_action=STATE_REPORT_ACTION,
        pair_report_with_reply=True,
    )
    await server.start()
    device = _device("slave", server, ack_loss_retry_delay_seconds=0)
    power_attribute = device.schema.power_attribute
    if power_attribute is None:  # pragma: no cover - the Pro profile always exposes Flow
        raise AssertionError("test profile has no power attribute")
    live_power_payload = build_control_payload(device.schema, {power_attribute: 38})
    server.register_ack_loss_control(
        live_power_payload,
        applied_state,
        failed_resolution_reads=0,
    )

    try:
        await device.connect()
        outcome = await device.write_power(38, guard=lambda: True)
        server.current_state = _raw_state(
            power=42,
            frequency=20,
            linkage=LinkageRole.ASYNC_SLAVE,
            timer_enabled=True,
        )
        next_state = await device.get_state()
    finally:
        await device.disconnect()
        await server.close()

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK
    assert next_state.power == 42
    assert server.accepted_connections == 3
    assert server.state_requests_by_connection == {1: 0, 2: 1, 3: 1}
    assert len(server.control_events) == 1
    assert server.control_events[0][0] == 1
    assert sum(
        payload == live_power_payload
        for _connection, payload, _sent_at in server.control_payload_events
    ) == 1
    assert server.errors == []


async def test_schedule_role_activation_refreshes_report_reply_streams_and_detaches(
    tmp_path: Path,
) -> None:
    master_independent = _scheduled_role_state(
        power=31,
        before_flow=31,
        after_flow=35,
        after_frequency=30,
    )
    slave_independent = _scheduled_role_state(
        power=32,
        before_flow=32,
        after_flow=40,
        after_frequency=30,
    )
    master_server = _LocalGizwitsPump(
        master_independent,
        state_action=STATE_REPORT_ACTION,
        pair_report_with_reply=True,
    )
    slave_server = _LocalGizwitsPump(
        slave_independent,
        state_action=STATE_REPORT_ACTION,
        pair_report_with_reply=True,
    )
    await master_server.start()
    await slave_server.start()
    master = _device(
        "master",
        master_server,
        minimum_command_interval_ms=1000,
        readback_delay_ms=500,
    )
    slave = _device(
        "slave",
        slave_server,
        minimum_command_interval_ms=1000,
        readback_delay_ms=500,
    )
    linkage_attribute = LOCAL_WAVEMAKER_PRO.linkage_attribute
    if linkage_attribute is None:  # pragma: no cover - Pro always exposes Linkage
        raise AssertionError("test profile has no linkage attribute")
    for server, independent, role in (
        (master_server, master_independent, LinkageRole.MASTER),
        (slave_server, slave_independent, LinkageRole.ASYNC_SLAVE),
    ):
        linked = _with_linkage(independent, role)
        server.register_control(
            build_control_payload(
                LOCAL_WAVEMAKER_PRO,
                {linkage_attribute: role.value},
            ),
            linked,
        )
        server.register_control(
            build_control_payload(
                LOCAL_WAVEMAKER_PRO,
                {linkage_attribute: LinkageRole.INDEPENDENT.value},
            ),
            independent,
        )

    progress = []
    store = JsonScheduleLinkageJournalStore(tmp_path / "role-report-reply.json")
    controller = ScheduleActiveLinkageController(
        {"master": master, "slave": slave},
        store,
        prerequisite_authorizer=lambda _spec, _snapshots: None,
        safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
        progress_observer=progress.append,
        refresh_sessions_before_critical_reads=True,
    )
    spec = ScheduleLinkageSpec(
        operation_id="role_report_reply",
        qualification_operation_id="qualified_pair",
        master_device_id="master",
        slave_device_id="slave",
        observation_window_seconds=130,
        verification_interval_seconds=0.1,
        minimum_lead_seconds=45,
        ambiguous_band_seconds=0.1,
    )

    try:
        await master.connect()
        await slave.connect()
        preflight = await controller.preflight(spec)
        run = asyncio.create_task(controller.run(preflight))
        async with asyncio.timeout(5):
            while True:
                record = store.load()
                if record is not None and record.phase is ScheduleLinkagePhase.ACTIVE:
                    break
                await asyncio.sleep(0.005)
        assert await controller.stop(spec.operation_id) is True
        result = await run

        assert result.stop_reason is ScheduleLinkageStopReason.MANUAL
        assert store.load() is None
        assert progress[-2].kind is ScheduleLinkageRunProgressKind.MONITOR_STARTED
        assert progress[-1].kind is ScheduleLinkageRunProgressKind.MONITOR_COMPLETED
        assert all(
            event.failure is None and event.drift_dimensions == ()
            for event in progress
        )
        assert master_server.current_state == master_independent
        assert slave_server.current_state == slave_independent
        assert len(master_server.control_payload_events) == 2
        assert len(slave_server.control_payload_events) == 2
        assert master_server.accepted_connections >= 5
        assert slave_server.accepted_connections >= 5
        assert master_server.errors == []
        assert slave_server.errors == []
    finally:
        await master.disconnect()
        await slave.disconnect()
        await master_server.close()
        await slave_server.close()


async def test_schedule_ack_loss_accepts_fresh_state_report_without_control_replay() -> None:
    initial_state = _scheduled_raw_state(
        power=35,
        frequency=20,
        linkage=LinkageRole.ASYNC_SLAVE,
        timer_enabled=True,
    )
    changed_slot = bytes((7, 0, 8, 0, 2, 38, 20, 0, 0))
    applied = bytearray(initial_state)
    applied[11 + 7 * 9 : 11 + 8 * 9] = changed_slot
    applied_state = bytes(applied)
    schedule_payload = build_local_wavemaker_pro_schedule_control_payload({7: changed_slot})
    server = _LocalGizwitsPump(initial_state, state_action=STATE_REPORT_ACTION)
    await server.start()
    server.register_ack_loss_control(
        schedule_payload,
        applied_state,
        failed_resolution_reads=0,
    )
    device = _device("slave", server, ack_loss_retry_delay_seconds=0)

    try:
        await device.connect()
        outcome = await device.write_schedule_slots({7: changed_slot}, guard=lambda: True)
    finally:
        await device.disconnect()
        await server.close()

    assert outcome is ControlVerificationOutcome.STATE_VERIFIED_WITHOUT_ACK
    assert server.current_state == applied_state
    assert server.accepted_connections == 3
    assert server.state_requests_by_connection == {1: 0, 2: 1, 3: 0}
    assert len(server.control_payload_events) == 1
    connection, observed_payload, _sent_at = server.control_payload_events[0]
    assert connection == 1
    assert observed_payload == schedule_payload
    assert server.errors == []


@pytest.mark.parametrize(
    ("applied_state", "expected_error", "expected_stage"),
    [
        (
            _raw_state(
                power=34,
                frequency=20,
                linkage=LinkageRole.ASYNC_SLAVE,
                timer_enabled=True,
            ),
            ControlAckPowerMismatchError,
            None,
        ),
        (
            _raw_state(
                power=38,
                frequency=20,
                linkage=LinkageRole.ASYNC_SLAVE,
                timer_enabled=True,
            )[:-1],
            ControlAckReadbackError,
            ControlAckResolutionStage.DECODE,
        ),
    ],
)
async def test_ack_loss_resolver_rejects_invalid_fresh_state_reports_without_control_replay(
    applied_state: bytes,
    expected_error: type[Exception],
    expected_stage: ControlAckResolutionStage | None,
) -> None:
    initial_state = _raw_state(
        power=33,
        frequency=20,
        linkage=LinkageRole.ASYNC_SLAVE,
        timer_enabled=True,
    )
    server = _LocalGizwitsPump(initial_state, state_action=STATE_REPORT_ACTION)
    await server.start()
    device = _device("slave", server, ack_loss_retry_delay_seconds=0)
    power_attribute = device.schema.power_attribute
    if power_attribute is None:  # pragma: no cover - the Pro profile always exposes Flow
        raise AssertionError("test profile has no power attribute")
    live_power_payload = build_control_payload(device.schema, {power_attribute: 38})
    server.register_ack_loss_control(
        live_power_payload,
        applied_state,
        failed_resolution_reads=0,
    )

    try:
        await device.connect()
        with pytest.raises(expected_error) as captured:
            await device.write_power(38, guard=lambda: True)
    finally:
        await device.disconnect()
        await server.close()

    if expected_stage is not None:
        assert isinstance(captured.value, ControlAckReadbackError)
        assert captured.value.stage is expected_stage
        assert captured.value.attempts == 8
    assert server.current_state == applied_state
    assert server.accepted_connections == 9
    assert server.state_requests_by_connection == {1: 0, **dict.fromkeys(range(2, 10), 1)}
    assert len(server.control_events) == 1
    assert server.control_events[0][0] == 1
    assert sum(
        payload == live_power_payload
        for _connection, payload, _sent_at in server.control_payload_events
    ) == 1
    assert server.errors == []


async def test_timer_on_restore_survives_two_quarantined_streams_without_replay(
    tmp_path: Path,
) -> None:
    master_server = _LocalGizwitsPump(
        _raw_state(
            power=60,
            frequency=30,
            linkage=LinkageRole.MASTER,
            timer_enabled=False,
        )
    )
    slave_server = _LocalGizwitsPump(
        _raw_state(
            power=42,
            frequency=30,
            linkage=LinkageRole.ASYNC_SLAVE,
            timer_enabled=False,
        ),
        lose_timer_on_reply=True,
        fail_first_fresh_state=True,
    )
    await asyncio.gather(master_server.start(), slave_server.start())
    master = _device("master", master_server)
    slave = _device("slave", slave_server)
    devices = (master, slave)

    try:
        await asyncio.gather(*(device.connect() for device in devices))
        spec = LinkageTestSpec(
            operation_id="transport_restore_once",
            master_device_id="master",
            slave_device_id="slave",
            slave_role=LinkageRole.ASYNC_SLAVE,
            mode="constant",
            master_power=60,
            slave_power=42,
            frequency=30,
            duration_seconds=5,
            verification_interval_seconds=1,
        )
        snapshots = (
            _snapshot(master, power=46, frequency=22),
            _snapshot(slave, power=54, frequency=28),
        )
        detach_target = DeviceTarget(
            enabled=True,
            power=30,
            mode="constant",
            frequency=spec.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
        master_final_target = DeviceTarget(
            enabled=True,
            power=46,
            mode="constant",
            frequency=22,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
        slave_final_target = DeviceTarget(
            enabled=True,
            power=54,
            mode="constant",
            frequency=28,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
        master_server.register_control(
            master.preview_target(detach_target).payload,
            _raw_state(
                power=30,
                frequency=30,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            ),
        )
        master_server.register_control(
            master.preview_target(master_final_target).payload,
            _raw_state(
                power=46,
                frequency=22,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=True,
            ),
            timer_on=True,
        )
        slave_server.register_control(
            slave.preview_target(detach_target).payload,
            _raw_state(
                power=30,
                frequency=30,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            ),
        )
        slave_server.register_control(
            slave.preview_target(slave_final_target).payload,
            _raw_state(
                power=54,
                frequency=28,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=True,
            ),
            timer_on=True,
        )

        now = datetime.now(UTC)
        store = JsonLinkageJournalStore(tmp_path / "transport-linkage.json")
        store.create(
            LinkageTransactionRecord(
                operation_id=spec.operation_id,
                phase=LinkageTransactionPhase.ACTIVE,
                spec=spec,
                snapshots=snapshots,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=5),
            )
        )
        controller = TemporaryLinkageController(
            {"master": master, "slave": slave},
            store,
            safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
            restore_verification_backoff_seconds=0,
            restore_verification_read_timeout_seconds=0.15,
            restore_write_timeout_seconds=0.6,
            restore_connection_timeout_seconds=0.5,
            restore_verification_convergence_timeout_seconds=1.2,
        )

        assert (
            await controller.recover_pending(authority=LinkageRecoveryAuthority.ATTENDED)
            is True
        )

        assert store.load() is None
        assert master_server.current_state == _raw_state(
            power=46,
            frequency=22,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
        assert slave_server.current_state == _raw_state(
            power=54,
            frequency=28,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
        assert master_server.accepted_connections == 3
        assert master_server.passcode_requests == 3
        assert master_server.login_requests == 3
        assert master_server.control_sequences == [1, 2]
        assert master_server.timer_on_control_sequences == [2]
        assert master_server.control_events == [
            (2, 1, False, True),
            (2, 2, True, True),
        ]
        assert master_server.state_requests_by_connection == {1: 0, 2: 4, 3: 4}
        assert master_server.state_request_count == 8

        assert slave_server.accepted_connections == 4
        assert slave_server.passcode_requests == 4
        assert slave_server.login_requests == 4
        assert slave_server.control_sequences == [1, 2]
        assert slave_server.timer_on_control_sequences == [2]
        assert slave_server.control_events == [
            (2, 1, False, True),
            (2, 2, True, True),
        ]
        assert slave_server.state_requests_by_connection == {1: 0, 2: 3, 3: 1, 4: 4}
        assert slave_server.state_request_count == 8
        assert master_server.errors == []
        assert slave_server.errors == []
    finally:
        await asyncio.gather(*(device.disconnect() for device in devices), return_exceptions=True)
        await asyncio.gather(master_server.close(), slave_server.close())


async def test_live_ack_loss_exhausts_eight_fresh_queries_then_uses_paced_fresh_rollback(
    tmp_path: Path,
) -> None:
    master_initial = _scheduled_raw_state(
        power=46,
        frequency=22,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=True,
    )
    slave_initial = _scheduled_raw_state(
        power=54,
        frequency=28,
        linkage=LinkageRole.INDEPENDENT,
        timer_enabled=True,
    )
    master_server = _LocalGizwitsPump(master_initial)
    slave_server = _LocalGizwitsPump(slave_initial)
    await asyncio.gather(master_server.start(), slave_server.start())
    master_sessions: list[GizwitsSession] = []
    slave_sessions: list[GizwitsSession] = []
    command_interval_ms = 500
    master = _device(
        "master",
        master_server,
        minimum_command_interval_ms=command_interval_ms,
        ack_loss_retry_delay_seconds=0,
        created_sessions=master_sessions,
    )
    slave = _device(
        "slave",
        slave_server,
        minimum_command_interval_ms=command_interval_ms,
        ack_loss_retry_delay_seconds=0,
        created_sessions=slave_sessions,
    )
    devices = (master, slave)

    try:
        await asyncio.gather(*(device.connect() for device in devices))
        original_states = await asyncio.gather(*(device.get_state() for device in devices))
        original_snapshots = tuple(
            DeviceControlSnapshot.from_state(
                device.device_id,
                state,
                physical_binding=device.physical_binding,
            )
            for device, state in zip(devices, original_states, strict=True)
            if device.physical_binding is not None
        )
        assert len(original_snapshots) == 2
        assert all(
            state.schedule is not None
            and len(state.schedule.entries) == 14
            and state.schedule.invalid_slots == ()
            for state in original_states
        )

        stage_target = DeviceTarget(
            enabled=True,
            power=30,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
        master_active_target = DeviceTarget(
            enabled=True,
            power=35,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.MASTER,
            timer_enabled=False,
        )
        slave_active_target = DeviceTarget(
            enabled=True,
            power=33,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.ASYNC_SLAVE,
            timer_enabled=False,
        )
        master_restore_target = DeviceTarget(
            enabled=True,
            power=46,
            mode="constant",
            frequency=22,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )
        slave_restore_target = DeviceTarget(
            enabled=True,
            power=54,
            mode="constant",
            frequency=28,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=True,
        )

        stage_state = _scheduled_raw_state(
            power=30,
            frequency=20,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )
        master_server.register_control(master.preview_target(stage_target).payload, stage_state)
        master_server.register_control(
            master.preview_target(master_active_target).payload,
            _scheduled_raw_state(
                power=35,
                frequency=20,
                linkage=LinkageRole.MASTER,
                timer_enabled=False,
            ),
        )
        master_server.register_control(
            master.preview_target(master_restore_target).payload,
            master_initial,
            timer_on=True,
        )
        slave_server.register_control(slave.preview_target(stage_target).payload, stage_state)
        slave_server.register_control(
            slave.preview_target(slave_active_target).payload,
            _scheduled_raw_state(
                power=33,
                frequency=20,
                linkage=LinkageRole.ASYNC_SLAVE,
                timer_enabled=False,
            ),
        )
        slave_server.register_control(
            slave.preview_target(slave_restore_target).payload,
            slave_initial,
            timer_on=True,
        )
        power_attribute = slave.schema.power_attribute
        if power_attribute is None:  # pragma: no cover - the Pro profile always exposes Flow
            raise AssertionError("test profile has no power attribute")
        live_power_payload = build_control_payload(
            slave.schema,
            {power_attribute: 38},
        )
        slave_server.register_ack_loss_control(
            live_power_payload,
            _scheduled_raw_state(
                power=38,
                frequency=20,
                linkage=LinkageRole.ASYNC_SLAVE,
                timer_enabled=False,
            ),
            failed_resolution_reads=8,
        )

        store = JsonLinkageJournalStore(tmp_path / "live-ack-loss-eight-fresh.json")
        controller = TemporaryLinkageController(
            {"master": master, "slave": slave},
            store,
            safety_interlock=LinkageSafetyInterlock(initially_permitted=True),
            restore_verification_backoff_seconds=0,
            restore_verification_read_timeout_seconds=0.2,
            restore_write_timeout_seconds=1.2,
            restore_connection_timeout_seconds=0.5,
            restore_verification_convergence_timeout_seconds=1,
        )
        spec = LinkageTestSpec(
            operation_id="live_ack_loss_eight_fresh",
            master_device_id="master",
            slave_device_id="slave",
            slave_role=LinkageRole.ASYNC_SLAVE,
            mode="constant",
            master_power=35,
            slave_power=33,
            frequency=20,
            duration_seconds=5,
            verification_interval_seconds=0.01,
            slave_power_after=38,
            power_change_after_seconds=0.05,
        )

        with pytest.raises(LinkageApplyError) as captured:
            async with asyncio.timeout(10):
                await controller.run(spec)

        ack_error = captured.value.__cause__
        assert isinstance(ack_error, ControlAckReadbackError)
        assert ack_error.stage is ControlAckResolutionStage.QUERY
        assert ack_error.attempts == 8
        assert store.load() is None

        resolver_connections = set(range(2, 10))
        assert slave_server.accepted_connections == 11
        assert slave_server.passcode_requests == 11
        assert slave_server.login_requests == 11
        assert {
            connection
            for connection, count in slave_server.state_requests_by_connection.items()
            if connection in resolver_connections and count == 1
        } == resolver_connections
        assert all(
            connection not in resolver_connections
            for connection, _sequence, _timer_on, _authenticated in slave_server.control_events
        )
        assert slave_server.control_sequences == [1, 2, 3, 1, 2]
        assert [event[0] for event in slave_server.control_events] == [1, 1, 1, 10, 10]
        assert slave_server.timer_on_control_sequences == [2]
        assert sum(
            payload == live_power_payload
            for _connection, payload, _sent_at in slave_server.control_payload_events
        ) == 1

        live_sent_at = next(
            sent_at
            for _connection, payload, sent_at in slave_server.control_payload_events
            if payload == live_power_payload
        )
        rollback_sent_at = next(
            sent_at
            for connection, _payload, sent_at in slave_server.control_payload_events
            if connection == 10
        )
        assert rollback_sent_at - live_sent_at >= 0.45

        # Eight resolver objects, one never-connected clean handoff, one rollback object and one
        # final TimerON verification object follow the original control session.
        assert len(slave_sessions) == 12
        assert len({id(session) for session in slave_sessions}) == 12
        assert slave_sessions[9].connected is False
        assert len(master_sessions) == 3
        assert len({id(session) for session in master_sessions}) == 3
        assert master_server.control_sequences == [1, 2, 1, 2]
        assert [event[0] for event in master_server.control_events] == [1, 1, 2, 2]

        restored_states = await asyncio.gather(*(device.get_state() for device in devices))
        restored_snapshots = tuple(
            DeviceControlSnapshot.from_state(
                device.device_id,
                state,
                physical_binding=device.physical_binding,
            )
            for device, state in zip(devices, restored_states, strict=True)
            if device.physical_binding is not None
        )
        assert restored_snapshots == original_snapshots
        assert master_server.current_state == master_initial
        assert slave_server.current_state == slave_initial
        assert all(
            state.schedule is not None
            and len(state.schedule.entries) == 14
            and state.schedule.invalid_slots == ()
            for state in restored_states
        )
        assert master_server.errors == []
        assert slave_server.errors == []
    finally:
        await asyncio.gather(*(device.disconnect() for device in devices), return_exceptions=True)
        await asyncio.gather(master_server.close(), slave_server.close())
