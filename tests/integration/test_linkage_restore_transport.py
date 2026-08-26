import asyncio
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jebao_flow.devices import (
    DeviceControlSnapshot,
    LanJebaoDevice,
    LinkageRecoveryAuthority,
    LinkageSafetyInterlock,
    LinkageTestSpec,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    PhysicalDeviceBinding,
    TemporaryLinkageController,
    schedule_structure_fingerprint,
)
from jebao_flow.persistence import JsonLinkageJournalStore
from jebao_flow.protocol.codec import MAGIC, GizwitsCommand, encode_frame, read_frame
from jebao_flow.protocol.errors import ProtocolConnectionError
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.session import STATE_REPLY_ACTION, GizwitsSession
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
    ) -> None:
        self.current_state = initial_state
        self.lose_timer_on_reply = lose_timer_on_reply
        self.fail_first_fresh_state = fail_first_fresh_state
        self.control_states: dict[bytes, bytes] = {}
        self.timer_on_payload: bytes | None = None
        self.accepted_connections = 0
        self.passcode_requests = 0
        self.login_requests = 0
        self.control_sequences: list[int] = []
        self.timer_on_control_sequences: list[int] = []
        self.state_requests_by_connection: dict[int, int] = {}
        self.errors: list[Exception] = []
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
                    continue
                if request.command == GizwitsCommand.SERIAL_CONTROL_REQUEST:
                    should_close = await self._handle_control(request.payload, reader, writer)
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

        if control_payload == self.timer_on_payload:
            self.timer_on_control_sequences.append(sequence)
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
        if connection_number == 2 and self.fail_first_fresh_state:
            self.fail_first_fresh_state = False
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
                bytes([STATE_REPLY_ACTION]) + self.current_state,
            )
        )
        await writer.drain()
        return False


def _session_factory(port: int):
    def create(address: str) -> GizwitsSession:
        return GizwitsSession(
            address,
            port=port,
            connect_timeout_seconds=0.2,
            response_timeout_seconds=0.05,
        )

    return create


def _device(device_id: str, server: _LocalGizwitsPump) -> LanJebaoDevice:
    return LanJebaoDevice(
        device_id,
        "127.0.0.1",
        LOCAL_WAVEMAKER_PRO.product_key,
        power_limits=PowerLimits(min_power=30, max_power=75),
        minimum_command_interval_ms=100,
        readback_delay_ms=0,
        readback_attempts=1,
        allow_hardware_writes=True,
        physical_binding=_binding(device_id),
        session_factory=_session_factory(server.port),
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
        assert master_server.accepted_connections == 1
        assert master_server.passcode_requests == 1
        assert master_server.login_requests == 1
        assert master_server.control_sequences == [1, 2]
        assert master_server.timer_on_control_sequences == [2]
        assert master_server.state_request_count == 9

        assert slave_server.accepted_connections == 3
        assert slave_server.passcode_requests == 3
        assert slave_server.login_requests == 3
        assert slave_server.control_sequences == [1, 2]
        assert slave_server.timer_on_control_sequences == [2]
        assert slave_server.state_requests_by_connection == {1: 4, 2: 1, 3: 4}
        assert slave_server.state_request_count == 9
        assert master_server.errors == []
        assert slave_server.errors == []
    finally:
        await asyncio.gather(*(device.disconnect() for device in devices), return_exceptions=True)
        await asyncio.gather(master_server.close(), slave_server.close())
