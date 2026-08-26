import asyncio
import json
import stat
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from jebao_flow.config import AppConfig, DeviceConfig
from jebao_flow.devices.base import (
    DeviceConnectionError,
    HardwareWritesDisabledError,
    JebaoDevice,
)
from jebao_flow.devices.factory import create_read_only_lan_device
from jebao_flow.devices.observer import (
    JsonlObservationJournal,
    ObserverStatus,
    ReadOnlyObserver,
    ResolvedDevice,
    resolve_device_bindings,
)
from jebao_flow.protocol.models import (
    DeviceCapabilities,
    DeviceSchedule,
    DeviceState,
    DeviceTarget,
    DiscoveredDevice,
    LinkageRole,
    ScheduleEntry,
)

PRODUCT_KEY = "50dbc92221fd4d33ae69a1fedd43b555"


class FakeDiscovery:
    def __init__(self, devices):
        self.devices = list(devices)
        self.calls = 0

    async def discover(self, *, timeout_seconds=5.0):
        self.calls += 1
        return list(self.devices)


class MemoryJournal:
    def __init__(self):
        self.records = []

    async def append(self, event, device_id, occurred_at, **values):
        self.records.append((event, device_id, occurred_at, values))


class ScriptedReadOnlyDevice(JebaoDevice):
    def __init__(
        self,
        device_id: str,
        states: list[DeviceState | BaseException],
        *,
        connect_error: BaseException | None = None,
    ):
        self._device_id = device_id
        self.states = deque(states)
        self.connect_error = connect_error
        self._connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.read_calls = 0
        self.write_calls = 0

    @property
    def device_id(self):
        return self._device_id

    @property
    def connected(self):
        return self._connected

    @property
    def capabilities(self):
        return DeviceCapabilities(model="test")

    async def connect(self):
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self._connected = True

    async def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False

    async def get_state(self):
        self.read_calls += 1
        if len(self.states) > 1:
            result = self.states.popleft()
        else:
            result = self.states[0]
        if isinstance(result, BaseException):
            raise result
        return result

    async def set_enabled(self, enabled):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")

    async def set_power(self, power):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")

    async def set_mode(self, mode):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")

    async def set_frequency(self, value):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")

    async def set_linkage(self, role: LinkageRole):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")

    async def set_timer_enabled(self, enabled: bool):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")

    async def write_target(self, target: DeviceTarget):
        self.write_calls += 1
        raise AssertionError("observer attempted a write")


def _app_config() -> AppConfig:
    raw = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    raw["devices"] = [raw["devices"][0]]
    raw["devices"][0]["identity"] = {
        "device_id": "vendor-left",
        "mac_address": "001122334455",
    }
    raw["groups"] = []
    raw["observer"].update(
        discovery_timeout_seconds=0.1,
        rediscovery_interval_seconds=5,
        poll_interval_seconds=1,
        reconnect_initial_seconds=0.1,
        reconnect_max_seconds=1,
    )
    return AppConfig.model_validate(raw)


def test_resolver_uses_stable_identity_not_product_key_or_order() -> None:
    configs = (
        DeviceConfig.model_validate(
            {
                "id": "left",
                "name": "Left",
                "type": "wavemaker",
                "identity": {"device_id": "vendor-left", "mac_address": "001122334455"},
            }
        ),
        DeviceConfig.model_validate(
            {
                "id": "right",
                "name": "Right",
                "type": "wavemaker",
                "identity": {"device_id": "vendor-right", "mac_address": "aabbccddeeff"},
            }
        ),
    )
    discovered = [
        DiscoveredDevice(
            address="192.0.2.12",
            device_id="vendor-right",
            mac_address="aa:bb:cc:dd:ee:ff",
            product_key=PRODUCT_KEY,
        ),
        DiscoveredDevice(
            address="192.0.2.11",
            device_id="vendor-left",
            mac_address="00:11:22:33:44:55",
            product_key=PRODUCT_KEY,
        ),
    ]

    resolved = resolve_device_bindings(configs, discovered)

    assert resolved["left"].address == "192.0.2.11"
    assert resolved["right"].address == "192.0.2.12"


def test_resolver_follows_stable_identity_after_dhcp_address_change() -> None:
    config = DeviceConfig.model_validate(
        {
            "id": "left",
            "name": "Left",
            "type": "wavemaker",
            "address": "192.0.2.11",
            "identity": {
                "device_id": "vendor-left",
                "mac_address": "001122334455",
            },
        }
    )
    discovered = [
        DiscoveredDevice(
            address="192.0.2.99",
            device_id="vendor-left",
            mac_address="00:11:22:33:44:55",
            product_key=PRODUCT_KEY,
        )
    ]

    resolved = resolve_device_bindings((config,), discovered)

    assert resolved["left"].address == "192.0.2.99"


def test_resolver_does_not_guess_from_duplicate_product_key() -> None:
    config = DeviceConfig.model_validate(
        {
            "id": "left",
            "name": "Left",
            "type": "wavemaker",
            "product_key": PRODUCT_KEY,
            "discovery": "auto",
        }
    )
    discovered = [
        DiscoveredDevice(
            address=f"192.0.2.{number}",
            device_id=f"vendor-{number}",
            product_key=PRODUCT_KEY,
        )
        for number in (11, 12)
    ]

    assert resolve_device_bindings((config,), discovered) == {}


def test_resolver_rejects_identity_with_wrong_product_family() -> None:
    config = DeviceConfig.model_validate(
        {
            "id": "left",
            "name": "Left",
            "type": "wavemaker",
            "identity": {"device_id": "vendor-left"},
        }
    )
    discovered = [
        DiscoveredDevice(
            address="192.0.2.11",
            device_id="vendor-left",
            product_key="0696a19599bc484f8e1866f5ccf4ee7e",
        )
    ]

    assert resolve_device_bindings((config,), discovered) == {}


async def test_observer_polls_immediately_and_never_calls_control() -> None:
    config = _app_config()
    first_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    device = ScriptedReadOnlyDevice(
        "wavemaker_left",
        [
            DeviceState(online=True, enabled=True, power=40, observed_at=first_at),
            DeviceState(
                online=True,
                enabled=True,
                power=55,
                observed_at=first_at + timedelta(seconds=5),
            ),
        ],
    )
    discovery = FakeDiscovery(
        [
            DiscoveredDevice(
                address="192.0.2.11",
                device_id="vendor-left",
                mac_address="00:11:22:33:44:55",
                product_key=PRODUCT_KEY,
            )
        ]
    )
    stop_event = asyncio.Event()
    poll_waits = 0

    async def waiter(stop: asyncio.Event, delay: float) -> bool:
        nonlocal poll_waits
        if delay == config.observer.poll_interval_seconds:
            poll_waits += 1
            if poll_waits == 2:
                stop.set()
                return True
            return False
        await stop.wait()
        return True

    events = []
    journal = MemoryJournal()
    observer = ReadOnlyObserver(
        config,
        events.append,
        discovery=discovery,
        device_factory=lambda *_: device,
        waiter=waiter,
        journal=journal,
    )

    await asyncio.wait_for(observer.run(stop_event), timeout=1)

    states = [event.state for event in events if event.status is ObserverStatus.ONLINE]
    assert [state.power for state in states if state is not None] == [40, 55]
    assert device.connect_calls == 1
    assert device.disconnect_calls == 1
    assert device.write_calls == 0
    assert [record[0] for record in journal.records] == ["first_seen", "state_changed"]


async def test_reconnect_backoff_caps_and_resets_after_successful_read() -> None:
    config = _app_config()
    config = config.model_copy(
        update={
            "observer": config.observer.model_copy(
                update={
                    "poll_interval_seconds": 5,
                    "reconnect_initial_seconds": 1,
                    "reconnect_max_seconds": 4,
                }
            )
        }
    )
    observed_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    instances = [
        ScriptedReadOnlyDevice(
            "wavemaker_left",
            [DeviceState(online=True, enabled=True, power=40)],
            connect_error=DeviceConnectionError("connect failed 1"),
        ),
        ScriptedReadOnlyDevice(
            "wavemaker_left",
            [DeviceState(online=True, enabled=True, power=40)],
            connect_error=DeviceConnectionError("connect failed 2"),
        ),
        ScriptedReadOnlyDevice(
            "wavemaker_left",
            [DeviceState(online=True, enabled=True, power=40)],
            connect_error=DeviceConnectionError("connect failed 3"),
        ),
        ScriptedReadOnlyDevice(
            "wavemaker_left",
            [
                DeviceState(
                    online=True,
                    enabled=True,
                    power=40,
                    observed_at=observed_at,
                ),
                DeviceConnectionError("read failed after success"),
            ],
        ),
    ]
    pending = deque(instances)
    delays: list[float] = []
    expected_delays = [1, 2, 4, 5, 1]
    stop_event = asyncio.Event()

    async def waiter(stop: asyncio.Event, delay: float) -> bool:
        delays.append(delay)
        if len(delays) == len(expected_delays):
            stop.set()
            return True
        return False

    events = []
    observer = ReadOnlyObserver(
        config,
        events.append,
        device_factory=lambda *_: pending.popleft(),
        waiter=waiter,
        journal=MemoryJournal(),
    )
    endpoint = ResolvedDevice("wavemaker_left", "192.0.2.11", PRODUCT_KEY)

    await asyncio.wait_for(
        observer._observe_device(config.devices[0], endpoint, stop_event),
        timeout=1,
    )

    assert delays == expected_delays
    assert sum(event.status is ObserverStatus.ONLINE for event in events) == 1
    assert all(device.disconnect_calls == 1 for device in instances)
    assert all(device.write_calls == 0 for device in instances)


async def test_failing_worker_does_not_block_healthy_device_polling() -> None:
    config = _app_config()
    failing_config = DeviceConfig.model_validate(
        {
            "id": "failing",
            "name": "Failing",
            "type": "wavemaker",
            "address": "192.0.2.11",
            "product_key": PRODUCT_KEY,
        }
    )
    healthy_config = DeviceConfig.model_validate(
        {
            "id": "healthy",
            "name": "Healthy",
            "type": "wavemaker",
            "address": "192.0.2.12",
            "product_key": PRODUCT_KEY,
        }
    )
    config = config.model_copy(update={"devices": (failing_config, healthy_config)})
    healthy = ScriptedReadOnlyDevice(
        "healthy",
        [DeviceState(online=True, enabled=True, power=45)],
    )
    failing_instances: list[ScriptedReadOnlyDevice] = []
    stop_event = asyncio.Event()
    events = []

    def sink(event) -> None:
        events.append(event)
        healthy_reads = sum(
            item.device_id == "healthy" and item.status is ObserverStatus.ONLINE
            for item in events
        )
        if healthy_reads >= 3:
            stop_event.set()

    def device_factory(device, *_):
        if device.id == "healthy":
            return healthy
        failing = ScriptedReadOnlyDevice(
            "failing",
            [DeviceState(online=True, enabled=False, power=0)],
            connect_error=DeviceConnectionError("offline"),
        )
        failing_instances.append(failing)
        return failing

    async def waiter(stop: asyncio.Event, _delay: float) -> bool:
        await asyncio.sleep(0)
        return stop.is_set()

    observer = ReadOnlyObserver(
        config,
        sink,
        device_factory=device_factory,
        waiter=waiter,
        journal=MemoryJournal(),
    )
    tasks = (
        asyncio.create_task(
            observer._observe_device(
                failing_config,
                ResolvedDevice("failing", "192.0.2.11", PRODUCT_KEY),
                stop_event,
            )
        ),
        asyncio.create_task(
            observer._observe_device(
                healthy_config,
                ResolvedDevice("healthy", "192.0.2.12", PRODUCT_KEY),
                stop_event,
            )
        ),
    )

    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert healthy.read_calls >= 3
    assert healthy.disconnect_calls == 1
    assert failing_instances
    assert all(device.disconnect_calls == 1 for device in failing_instances)
    assert any(
        event.device_id == "failing" and event.status is ObserverStatus.OFFLINE
        for event in events
    )


async def test_stop_cancels_poll_and_backoff_waiters_and_disconnects_devices() -> None:
    config = _app_config()
    online_config = DeviceConfig.model_validate(
        {
            "id": "online",
            "name": "Online",
            "type": "wavemaker",
            "address": "192.0.2.11",
            "product_key": PRODUCT_KEY,
        }
    )
    offline_config = DeviceConfig.model_validate(
        {
            "id": "offline",
            "name": "Offline",
            "type": "wavemaker",
            "address": "192.0.2.12",
            "product_key": PRODUCT_KEY,
        }
    )
    config = config.model_copy(
        update={
            "devices": (online_config, offline_config),
            "observer": config.observer.model_copy(
                update={
                    "poll_interval_seconds": 5,
                    "rediscovery_interval_seconds": 30,
                    "reconnect_initial_seconds": 1,
                }
            ),
        }
    )
    online = ScriptedReadOnlyDevice(
        "online",
        [DeviceState(online=True, enabled=True, power=45)],
    )
    offline = ScriptedReadOnlyDevice(
        "offline",
        [DeviceState(online=True, enabled=False, power=0)],
        connect_error=DeviceConnectionError("offline"),
    )
    poll_waiting = asyncio.Event()
    backoff_waiting = asyncio.Event()
    blocked = asyncio.Event()

    def device_factory(device, *_):
        return online if device.id == "online" else offline

    async def waiter(stop: asyncio.Event, delay: float) -> bool:
        if delay == config.observer.rediscovery_interval_seconds:
            await stop.wait()
            return True
        if delay == config.observer.poll_interval_seconds:
            poll_waiting.set()
        elif delay == config.observer.reconnect_initial_seconds:
            backoff_waiting.set()
        await blocked.wait()
        return False

    stop_event = asyncio.Event()
    observer = ReadOnlyObserver(
        config,
        lambda _event: None,
        device_factory=device_factory,
        waiter=waiter,
        journal=MemoryJournal(),
    )
    observer_task = asyncio.create_task(observer.run(stop_event))

    await asyncio.wait_for(
        asyncio.gather(poll_waiting.wait(), backoff_waiting.wait()),
        timeout=1,
    )
    stop_event.set()
    await asyncio.wait_for(observer_task, timeout=1)

    assert online.disconnect_calls == 1
    assert offline.disconnect_calls == 1
    assert observer._workers == {}


async def test_rediscovery_replaces_worker_when_stable_identity_moves_address() -> None:
    config = _app_config()
    discovery = FakeDiscovery(
        [
            DiscoveredDevice(
                address="192.0.2.11",
                device_id="vendor-left",
                mac_address="00:11:22:33:44:55",
                product_key=PRODUCT_KEY,
            )
        ]
    )
    created: list[tuple[str, ScriptedReadOnlyDevice]] = []
    creation_events = (asyncio.Event(), asyncio.Event())

    def device_factory(device, address, _product_key):
        instance = ScriptedReadOnlyDevice(
            device.id,
            [DeviceState(online=True, enabled=True, power=45)],
        )
        created.append((address, instance))
        creation_events[len(created) - 1].set()
        return instance

    observer = ReadOnlyObserver(
        config,
        lambda _event: None,
        discovery=discovery,
        device_factory=device_factory,
        journal=MemoryJournal(),
    )
    stop_event = asyncio.Event()

    try:
        await observer._reconcile(stop_event)
        await asyncio.wait_for(creation_events[0].wait(), timeout=1)
        discovery.devices = [
            DiscoveredDevice(
                address="192.0.2.99",
                device_id="vendor-left",
                mac_address="00:11:22:33:44:55",
                product_key=PRODUCT_KEY,
            )
        ]

        await observer._reconcile(stop_event)
        await asyncio.wait_for(creation_events[1].wait(), timeout=1)

        assert [address for address, _ in created] == ["192.0.2.11", "192.0.2.99"]
        assert created[0][1].disconnect_calls == 1
        assert observer._workers["wavemaker_left"][0].address == "192.0.2.99"
    finally:
        tasks = [task for _, task in observer._workers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        observer._workers.clear()


async def test_successful_rediscovery_unmaps_and_disconnects_missing_identity() -> None:
    config = _app_config()
    device = ScriptedReadOnlyDevice(
        "wavemaker_left",
        [DeviceState(online=True, enabled=True, power=40)],
    )
    discovery = FakeDiscovery(
        [
            DiscoveredDevice(
                address="192.0.2.11",
                device_id="vendor-left",
                mac_address="00:11:22:33:44:55",
                product_key=PRODUCT_KEY,
            )
        ]
    )
    events = []
    observer = ReadOnlyObserver(
        config,
        events.append,
        discovery=discovery,
        device_factory=lambda *_: device,
        journal=MemoryJournal(),
    )
    stop_event = asyncio.Event()

    await observer._reconcile(stop_event)
    await asyncio.sleep(0)
    discovery.devices = []
    await observer._reconcile(stop_event)

    assert device.disconnect_calls == 1
    assert events[-1].status is ObserverStatus.UNMAPPED
    assert "wavemaker_left" not in observer._workers


class FakeRawSession:
    def __init__(self, address: str):
        self.address = address
        self.connected = False
        self.control_calls = 0

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def authenticate(self):
        return b"private-passcode"

    async def read_raw_state(self):
        return bytes(452)

    async def send_raw_control(self, control_payload):
        self.control_calls += 1
        return b""


async def test_read_only_factory_forces_write_lock_even_when_config_allows_writes() -> None:
    config = DeviceConfig.model_validate(
        {
            "id": "pump",
            "name": "Pump",
            "type": "wavemaker",
            "address": "192.0.2.11",
            "product_key": PRODUCT_KEY,
            "control": {"allow_hardware_writes": True},
        }
    )
    sessions = []

    def session_factory(address):
        session = FakeRawSession(address)
        sessions.append(session)
        return session

    device = create_read_only_lan_device(
        config,
        "192.0.2.11",
        PRODUCT_KEY,
        session_factory=session_factory,
    )
    await device.connect()
    state = await device.get_state()

    assert state.online is True
    try:
        await device.set_power(30)
    except HardwareWritesDisabledError:
        pass
    else:  # pragma: no cover - explicit safety assertion
        raise AssertionError("observer factory opened the write gate")
    assert sessions[0].control_calls == 0


async def test_journal_records_only_safe_decoded_fields(tmp_path: Path) -> None:
    path = tmp_path / "observations.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)
    journal = JsonlObservationJournal(path)
    observed_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    state = DeviceState(
        online=True,
        enabled=True,
        power=45,
        observed_attributes={"TimerON": True, "AutoFlow": 50},
        observed_at=observed_at,
    )

    await journal.append("first_seen", "logical_pump", observed_at, current=state)

    content = path.read_text(encoding="utf-8")
    record = json.loads(content)
    assert record["current"]["observed_attributes"]["TimerON"] is True
    assert "passcode" not in content.lower()
    assert "state_hex" not in content
    assert "mac_address" not in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_schedule_signature_ignores_device_clock_but_detects_slot_changes() -> None:
    observed_at = datetime(2026, 8, 26, 0, 51, tzinfo=UTC)
    entry = ScheduleEntry(
        slot=0,
        start="00:00",
        end="08:01",
        mode="constant",
        mode_code=0,
        parameters={"flow": 60},
    )
    state = DeviceState(
        online=True,
        enabled=True,
        power=81,
        schedule=DeviceSchedule(
            enabled=True,
            device_local_time=datetime(2026, 8, 26, 9, 51),
            entries=(entry,),
        ),
        observed_at=observed_at,
    )
    clock_only = state.model_copy(
        update={
            "schedule": state.schedule.model_copy(
                update={"device_local_time": datetime(2026, 8, 26, 9, 51, 5)}
            )
        }
    )
    changed = state.model_copy(
        update={
            "schedule": state.schedule.model_copy(
                update={
                    "entries": (
                        entry.model_copy(update={"end": "08:02"}),
                    )
                }
            )
        }
    )

    assert ReadOnlyObserver._state_signature(state) == ReadOnlyObserver._state_signature(
        clock_only
    )
    assert ReadOnlyObserver._state_signature(state) != ReadOnlyObserver._state_signature(
        changed
    )
