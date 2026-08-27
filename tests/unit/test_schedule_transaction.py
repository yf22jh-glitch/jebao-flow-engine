from __future__ import annotations

import asyncio
import json
import stat
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jebao_flow.devices.base import (
    ControlVerificationOutcome,
    SafetyInterlockError,
    WriteGuard,
)
from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.devices.linkage import LinkageSafetyInterlock
from jebao_flow.devices.schedule_transaction import (
    DeviceSchedulePatch,
    ObservationCompletion,
    ScheduleImageSnapshot,
    ScheduleSlotPatch,
    TemporaryScheduleApplyError,
    TemporaryScheduleController,
    TemporaryScheduleErrorCode,
    TemporaryScheduleJournalStore,
    TemporaryScheduleKind,
    TemporarySchedulePhase,
    TemporarySchedulePreflightError,
    TemporaryScheduleRecord,
    TemporaryScheduleRecoveryError,
    TemporaryScheduleRollbackUnsafeError,
    TemporaryScheduleSpec,
    behavior_neutral_unused_slot_patch,
)
from jebao_flow.devices.simulator import SimulatedJebaoDevice
from jebao_flow.persistence.schedule_transaction import JsonTemporaryScheduleJournalStore
from jebao_flow.protocol.models import Capability, DeviceCapabilities, DeviceState, LinkageRole
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    get_local_wavemaker_pro_slot_wire,
    patch_local_wavemaker_pro_schedule_slot,
)
from jebao_flow.safety.limits import PowerLimits


def _capabilities() -> DeviceCapabilities:
    return DeviceCapabilities(
        model="Local Wavemaker Pro",
        product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        readable=frozenset(Capability),
        writable=frozenset(Capability),
        native_modes=frozenset({"constant", "sine"}),
        linkage_roles=frozenset(LinkageRole),
    )


def _original_image(*, invert: bool = False) -> bytes:
    return b"".join(
        LOCAL_WAVEMAKER_PRO_UNUSED_EE
        if (index % 2 == 0) is invert
        else LOCAL_WAVEMAKER_PRO_UNUSED_ZERO
        for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    )


def _active_wire(
    *,
    flow: int,
    mode: int = 2,
    start_hour: int = 0,
    end_hour: int = 24,
) -> bytes:
    return bytes((start_hour, 0, end_hour, 0, mode, flow, 20 if mode == 1 else 0, 0, 0))


def _field_slots(
    *,
    before_flow: int,
    after_flow: int,
    boundary_hour: int = 12,
) -> tuple[ScheduleSlotPatch, ...]:
    wires = (
        _active_wire(flow=before_flow, start_hour=0, end_hour=boundary_hour),
        _active_wire(
            flow=after_flow,
            mode=1,
            start_hour=boundary_hour,
            end_hour=24,
        ),
        *(LOCAL_WAVEMAKER_PRO_UNUSED_EE for _ in range(46)),
    )
    return tuple(ScheduleSlotPatch.from_wire(index, wire) for index, wire in enumerate(wires))


class _ScheduleDevice(SimulatedJebaoDevice):
    def __init__(
        self,
        device_id: str,
        image: bytes,
        *,
        events: list[str] | None = None,
    ) -> None:
        super().__init__(device_id, capabilities=_capabilities())
        self.image = image
        self.binding = super().physical_binding
        self.events = events if events is not None else []
        self.writes: list[dict[int, bytes]] = []
        self.restore_images: list[bytes] = []
        self.fail_selected_write = False
        self.fail_restore_write = False
        self.corrupt_selected_readback = False
        self.before_guard: Any = None
        self.after_write: Any = None
        self.state_reads = 0
        self.schedule_reads = 0
        self.on_state_read: Any = None
        self.on_schedule_read: Any = None
        self.selected_write_started: asyncio.Event | None = None
        self.release_selected_write: asyncio.Event | None = None
        self.disconnect_calls = 0
        self.connect_calls = 0
        self.state_after_reconnect: DeviceState | None = None
        self.state_override_connect_call: int | None = None
        self.reconnected_state_read_started: asyncio.Event | None = None
        self.release_reconnected_state_read: asyncio.Event | None = None
        self.block_state_connect_call: int | None = None
        self.safe_state = DeviceState(
            online=True,
            enabled=True,
            power=30,
            mode="constant",
            frequency=20,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )

    @property
    def physical_binding(self) -> PhysicalDeviceBinding | None:
        return self.binding

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def get_state(self) -> DeviceState:
        self.state_reads += 1
        if self.on_state_read is not None:
            self.on_state_read(self)
        if self.connect_calls == self.block_state_connect_call:
            if self.reconnected_state_read_started is not None:
                self.reconnected_state_read_started.set()
            if self.release_reconnected_state_read is not None:
                await self.release_reconnected_state_read.wait()
        if (
            self.connect_calls == self.state_override_connect_call
            and self.state_after_reconnect is not None
        ):
            return self.state_after_reconnect
        return self.safe_state

    async def read_schedule_image(self) -> bytes:
        self.schedule_reads += 1
        if self.on_schedule_read is not None:
            self.on_schedule_read(self)
        if self.corrupt_selected_readback and self.writes:
            self.corrupt_selected_readback = False
            return self.image[:-1] + bytes((self.image[-1] ^ 1,))
        return self.image

    async def write_schedule_slots(
        self,
        slots: Mapping[int, bytes],
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed=None,
        on_ack_resolution=None,
    ) -> ControlVerificationOutcome:
        del on_ack_unconfirmed, on_ack_resolution
        normalized = {index: bytes(wire) for index, wire in slots.items()}
        self.events.append(f"write:{self.device_id}:{len(normalized)}")
        if self.before_guard is not None:
            self.before_guard()
        if guard is not None and guard() is not True:
            raise SafetyInterlockError("blocked")
        if self.fail_selected_write:
            raise RuntimeError("private transport detail")
        self.writes.append(normalized)
        for index, wire in normalized.items():
            self.image = patch_local_wavemaker_pro_schedule_slot(self.image, index, wire)
        if self.after_write is not None:
            self.after_write()
        if self.selected_write_started is not None:
            self.selected_write_started.set()
        if self.release_selected_write is not None:
            await self.release_selected_write.wait()
        return ControlVerificationOutcome.STATE_VERIFIED

    async def restore_schedule_image(
        self,
        image: bytes,
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed=None,
        on_ack_resolution=None,
    ) -> ControlVerificationOutcome:
        del on_ack_unconfirmed, on_ack_resolution
        exact = bytes(image)
        self.events.append(f"restore:{self.device_id}:48")
        self.restore_images.append(exact)
        if guard is not None and guard() is not True:
            raise SafetyInterlockError("blocked")
        if self.fail_restore_write:
            raise RuntimeError("private restore detail")
        self.image = exact
        return ControlVerificationOutcome.STATE_VERIFIED


class _MemoryStore:
    def __init__(self, events: list[str] | None = None) -> None:
        self.record: TemporaryScheduleRecord | None = None
        self.expected: TemporaryScheduleRecord | None = None
        self.events = events if events is not None else []

    def load(self) -> TemporaryScheduleRecord | None:
        return self.record

    @contextmanager
    def lease(self) -> Iterator[None]:
        self.expected = self.record
        try:
            yield
        finally:
            self.expected = None

    def create(self, record: TemporaryScheduleRecord) -> None:
        assert self.record is None
        self.events.append(f"journal:{record.phase.value}")
        self.record = record
        self.expected = record

    def save(self, record: TemporaryScheduleRecord) -> None:
        assert self.record == self.expected
        self.events.append(f"journal:{record.phase.value}")
        self.record = record
        self.expected = record

    def confirms_lease_successor(self, record: TemporaryScheduleRecord) -> bool:
        return self.record == self.expected == record

    def clear(self) -> None:
        assert self.record == self.expected
        self.events.append("journal:clear")
        self.record = None
        self.expected = None


def _spec() -> TemporaryScheduleSpec:
    return TemporaryScheduleSpec(
        operation_id="schedule-test",
        device_patches=(
            DeviceSchedulePatch(
                device_id="left",
                slots=_field_slots(before_flow=31, after_flow=35),
            ),
            DeviceSchedulePatch(
                device_id="right",
                slots=_field_slots(before_flow=32, after_flow=40),
            ),
        ),
    )


def _controller(
    left: _ScheduleDevice,
    right: _ScheduleDevice,
    store: TemporaryScheduleJournalStore,
    guard: LinkageSafetyInterlock,
    *,
    monotonic_clock=None,
) -> TemporaryScheduleController:
    return TemporaryScheduleController(
        {"left": left, "right": right},
        store,
        safety_interlock=guard,
        monotonic_clock=monotonic_clock,
    )


@pytest.mark.asyncio
async def test_stages_selected_slots_then_restores_all_48_exactly() -> None:
    events: list[str] = []
    originals = (_original_image(), _original_image(invert=True))
    left = _ScheduleDevice("left", originals[0], events=events)
    right = _ScheduleDevice("right", originals[1], events=events)
    store = _MemoryStore(events)
    guard = LinkageSafetyInterlock(initially_permitted=True)
    staged_seen = False

    async def observe(record: TemporaryScheduleRecord) -> ObservationCompletion:
        nonlocal staged_seen
        staged_seen = True
        assert record.phase is TemporarySchedulePhase.OBSERVING
        assert get_local_wavemaker_pro_slot_wire(left.image, 1) == _active_wire(
            flow=35,
            mode=1,
            start_hour=12,
            end_hour=24,
        )
        assert get_local_wavemaker_pro_slot_wire(right.image, 1) == _active_wire(
            flow=40,
            mode=1,
            start_hour=12,
            end_hour=24,
        )
        return ObservationCompletion.DISARM_VERIFIED

    result = await _controller(left, right, store, guard).run(_spec(), observe=observe)

    assert staged_seen
    assert result.observation_completed is True
    assert (left.image, right.image) == originals
    assert [len(write) for write in left.writes] == [48]
    assert [len(write) for write in right.writes] == [48]
    assert left.restore_images == [originals[0]]
    assert right.restore_images == [originals[1]]
    assert store.record is None
    assert events.index("journal:applying") < events.index("write:left:48")
    assert events.index("write:right:48") < events.index("journal:staged")
    assert events.index("journal:rolling_back") < events.index("restore:right:48")


@pytest.mark.asyncio
async def test_nonzero_feed_flow_cannot_bypass_transaction_power_limit() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    limited = PowerLimits(min_power=30, max_power=75)
    left._capabilities = left.capabilities.model_copy(  # noqa: SLF001
        update={"power_limits": limited}
    )
    right._capabilities = right.capabilities.model_copy(  # noqa: SLF001
        update={"power_limits": limited}
    )

    def feed_slots(flow: int) -> tuple[ScheduleSlotPatch, ...]:
        wires = (
            _active_wire(flow=31, start_hour=0, end_hour=12),
            bytes((12, 0, 24, 0, 7, flow, 0, 15, 0)),
            *(LOCAL_WAVEMAKER_PRO_UNUSED_EE for _ in range(46)),
        )
        return tuple(
            ScheduleSlotPatch.from_wire(index, wire) for index, wire in enumerate(wires)
        )

    spec = TemporaryScheduleSpec(
        device_patches=(
            DeviceSchedulePatch(device_id="left", slots=feed_slots(100)),
            DeviceSchedulePatch(device_id="right", slots=feed_slots(40)),
        )
    )
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert captured.value.code is TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
async def test_second_stage_failure_restores_every_device_with_durable_write_intent() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    right.fail_selected_write = True
    store = _MemoryStore()
    guard = LinkageSafetyInterlock(initially_permitted=True)

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(left, right, store, guard).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.STAGE_WRITE_FAILED
    assert left.image == original_left
    assert right.image == original_right
    assert [len(write) for write in left.writes] == [48]
    assert right.writes == []
    assert left.restore_images == [original_left]
    assert right.restore_images == [original_right]
    assert store.record is None
    assert "private transport detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_whole_image_mismatch_is_not_accepted_and_originals_are_restored() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    left.corrupt_selected_readback = True
    store = _MemoryStore()
    guard = LinkageSafetyInterlock(initially_permitted=True)

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(left, right, store, guard).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.STAGE_VERIFY_FAILED
    assert left.image == original_left
    assert right.image == original_right
    assert store.record is None


@pytest.mark.asyncio
async def test_guard_epoch_change_stops_forward_writes_but_does_not_block_restore() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    guard = LinkageSafetyInterlock(initially_permitted=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    left.after_write = guard.trip
    store = _MemoryStore()

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(left, right, store, guard).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.SAFETY_INTERLOCK
    assert left.image == original_left
    assert right.image == original_right
    assert [len(write) for write in left.writes] == [48]
    assert left.restore_images == [original_left]
    assert right.writes == []


@pytest.mark.asyncio
async def test_last_moment_deadline_guard_blocks_send_and_clears_prewrite_journal() -> None:
    original_left = _original_image()
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", _original_image(invert=True))
    store = _MemoryStore()
    guard = LinkageSafetyInterlock(initially_permitted=True)
    now = [0.0]
    left.before_guard = lambda: now.__setitem__(0, 61.0)

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(
            left,
            right,
            store,
            guard,
            monotonic_clock=lambda: now[0],
        ).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.STAGE_WRITE_FAILED
    assert left.writes == []
    assert left.restore_images == [original_left]
    # Intent was durable before entering the transport, so recovery conservatively rewrites all.
    assert get_local_wavemaker_pro_slot_wire(left.image, 0) == get_local_wavemaker_pro_slot_wire(
        original_left,
        0,
    )
    assert store.record is None


@pytest.mark.asyncio
async def test_forward_deadline_bounds_a_stuck_fresh_source_read() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    left.block_state_connect_call = 2
    left.release_reconnected_state_read = asyncio.Event()
    store = _MemoryStore()
    spec = _spec().model_copy(update={"forward_timeout_seconds": 0.2})

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert captured.value.code is TemporaryScheduleErrorCode.FORWARD_DEADLINE
    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
async def test_forward_deadline_cancels_uncertain_write_and_exactly_restores() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    left.release_selected_write = asyncio.Event()
    store = _MemoryStore()
    spec = _spec().model_copy(update={"forward_timeout_seconds": 0.2})

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert captured.value.code is TemporaryScheduleErrorCode.FORWARD_DEADLINE
    assert left.image == original_left
    assert right.image == original_right
    assert left.restore_images == [original_left]
    assert right.restore_images == []
    assert store.record is None


@pytest.mark.asyncio
async def test_cancellation_after_first_send_exactly_restores_then_reraises() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    left.selected_write_started = asyncio.Event()
    left.release_selected_write = asyncio.Event()
    store = _MemoryStore()
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=True),
    )

    operation = asyncio.create_task(controller.run(_spec()))
    await left.selected_write_started.wait()
    operation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await operation

    assert left.image == original_left
    assert right.image == original_right
    assert left.restore_images == [original_left]
    assert right.restore_images == []
    assert store.record is None


@pytest.mark.asyncio
async def test_safe_state_is_rechecked_after_snapshot_before_first_write() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))

    def arm_timer_on_second_read(device: _ScheduleDevice) -> None:
        if device.state_reads == 2:
            device.safe_state = device.safe_state.model_copy(update={"timer_enabled": True})

    left.on_state_read = arm_timer_on_second_read
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
    assert (left.disconnect_calls, left.connect_calls) == (2, 2)
    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
async def test_snapshot_discards_prior_stream_before_accepting_timer_off() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    left.state_after_reconnect = left.safe_state.model_copy(update={"timer_enabled": True})
    left.state_override_connect_call = 1
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
    assert (left.disconnect_calls, left.connect_calls) == (1, 1)
    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
async def test_source_image_is_rechecked_after_snapshot_before_first_write() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))

    def drift_on_second_read(device: _ScheduleDevice) -> None:
        if device.schedule_reads == 2:
            device.image = patch_local_wavemaker_pro_schedule_slot(
                device.image,
                0,
                _active_wire(flow=33),
            )

    left.on_schedule_read = drift_on_second_read
    store = _MemoryStore()

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.SOURCE_CHANGED
    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
async def test_restore_failure_keeps_journal_for_explicit_manual_recovery() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    right.fail_restore_write = True
    store = _MemoryStore()
    guard = LinkageSafetyInterlock(initially_permitted=True)
    controller = _controller(left, right, store, guard)

    with pytest.raises(TemporaryScheduleRecoveryError) as captured:
        await controller.run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED
    assert store.record is not None
    assert store.record.phase is TemporarySchedulePhase.RECOVERY_REQUIRED
    assert store.record.error_code is TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED
    assert str(store.record).find(original_right.hex()) == -1

    right.fail_restore_write = False
    assert await controller.manual_recover() is True
    assert left.image == original_left
    assert right.image == original_right
    assert left.restore_images[-1] == original_left
    assert right.restore_images[-1] == original_right
    assert store.record is None
    assert await controller.recover_pending() is False


@pytest.mark.asyncio
async def test_manual_recovery_refuses_a_remapped_physical_controller() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    right.fail_restore_write = True
    store = _MemoryStore()
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=True),
    )
    with pytest.raises(TemporaryScheduleRecoveryError):
        await controller.run(_spec())
    restore_calls_before = len(right.restore_images)
    assert right.binding is not None
    right.binding = right.binding.model_copy(update={"config_fingerprint": "f" * 64})

    with pytest.raises(TemporaryScheduleRecoveryError) as captured:
        await controller.manual_recover()

    assert captured.value.code is TemporaryScheduleErrorCode.BINDING_MISMATCH
    assert len(right.restore_images) == restore_calls_before
    assert store.record is not None


@pytest.mark.asyncio
async def test_observer_failure_is_redacted_and_requires_disarm_before_restore() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    store = _MemoryStore()

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        raise RuntimeError(original_left.hex())

    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=True),
    )
    with pytest.raises(TemporaryScheduleRollbackUnsafeError) as captured:
        await controller.run(_spec(), observe=observe)

    assert captured.value.code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
    assert original_left.hex() not in str(captured.value)
    assert original_left.hex() not in "".join(traceback.format_exception(captured.value))
    assert (left.image, right.image) != (original_left, original_right)
    assert store.record is not None
    assert store.record.phase is TemporarySchedulePhase.RECOVERY_REQUIRED

    with pytest.raises(TemporaryScheduleRecoveryError) as recovery:
        await controller.manual_recover()
    assert recovery.value.code is TemporaryScheduleErrorCode.MANUAL_RECOVERY_AUTHORITY_REQUIRED

    assert await controller.manual_recover(disarm_verified=True) is True
    assert (left.image, right.image) == (original_left, original_right)


@pytest.mark.asyncio
async def test_observer_typed_error_cannot_bypass_disarm_proof() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    store = _MemoryStore()

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        raise TemporaryScheduleApplyError(TemporaryScheduleErrorCode.STAGE_WRITE_FAILED)

    with pytest.raises(TemporaryScheduleRollbackUnsafeError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec(), observe=observe)

    assert captured.value.code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
    assert left.restore_images == []
    assert right.restore_images == []
    assert store.record is not None
    assert store.record.error_code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED


@pytest.mark.asyncio
async def test_claimed_disarm_is_freshly_verified_before_schedule_restore() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    store = _MemoryStore()
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=True),
    )

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        left.safe_state = left.safe_state.model_copy(update={"timer_enabled": True})
        return ObservationCompletion.DISARM_VERIFIED

    with pytest.raises(TemporaryScheduleRollbackUnsafeError) as captured:
        await controller.run(_spec(), observe=observe)

    assert captured.value.code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
    assert left.restore_images == []
    assert right.restore_images == []
    assert store.record is not None

    left.safe_state = left.safe_state.model_copy(update={"timer_enabled": False})
    assert await controller.manual_recover(disarm_verified=True) is True
    assert (left.image, right.image) == (original_left, original_right)


@pytest.mark.asyncio
async def test_disarm_proof_reconnects_instead_of_accepting_stale_stream_state() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    left.state_after_reconnect = left.safe_state.model_copy(update={"timer_enabled": True})
    left.state_override_connect_call = 3
    store = _MemoryStore()

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        return ObservationCompletion.DISARM_VERIFIED

    with pytest.raises(TemporaryScheduleRollbackUnsafeError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec(), observe=observe)

    assert captured.value.code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
    assert (left.disconnect_calls, left.connect_calls) == (3, 3)
    assert (right.disconnect_calls, right.connect_calls) == (3, 3)
    assert left.restore_images == []
    assert right.restore_images == []
    assert store.record is not None


@pytest.mark.asyncio
async def test_disarm_proof_timeout_retains_schedule_recovery_journal() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    left.release_reconnected_state_read = asyncio.Event()
    left.block_state_connect_call = 3
    store = _MemoryStore()
    spec = _spec().model_copy(update={"disarm_verify_timeout_seconds": 0.01})

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        return ObservationCompletion.DISARM_VERIFIED

    with pytest.raises(TemporaryScheduleRollbackUnsafeError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec, observe=observe)

    assert captured.value.code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
    assert left.restore_images == []
    assert right.restore_images == []
    assert store.record is not None


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_reconnect_disarm_proof_before_restore() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    left.reconnected_state_read_started = asyncio.Event()
    left.release_reconnected_state_read = asyncio.Event()
    left.block_state_connect_call = 3
    observer_started = asyncio.Event()
    store = _MemoryStore()
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=True),
    )

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        observer_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ObservationCompletion.DISARM_VERIFIED

    operation = asyncio.create_task(controller.run(_spec(), observe=observe))
    await observer_started.wait()
    operation.cancel()
    await left.reconnected_state_read_started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    assert left.restore_images == []
    assert right.restore_images == []
    left.release_reconnected_state_read.set()

    with pytest.raises(asyncio.CancelledError):
        await operation

    assert (left.image, right.image) == (original_left, original_right)
    assert left.restore_images == [original_left]
    assert right.restore_images == [original_right]
    assert store.record is None


@pytest.mark.asyncio
async def test_observation_timeout_cancels_and_verifies_disarm_before_restore() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    guard = LinkageSafetyInterlock(initially_permitted=True)
    store = _MemoryStore()
    spec = _spec().model_copy(update={"observation_timeout_seconds": 0.01})

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ObservationCompletion.DISARM_VERIFIED

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(left, right, store, guard).run(spec, observe=observe)

    assert captured.value.code is TemporaryScheduleErrorCode.OBSERVATION_TIMEOUT
    assert guard.permitted is False
    assert (left.image, right.image) == (original_left, original_right)
    assert store.record is None


def test_behavior_neutral_helper_toggles_only_an_unused_sentinel() -> None:
    image = _original_image()

    patch = behavior_neutral_unused_slot_patch(image, preferred_slot=0)

    assert patch.behavior_neutral_unused_toggle is True
    assert patch.wire_bytes != get_local_wavemaker_pro_slot_wire(image, 0)
    assert {
        patch.wire_bytes,
        get_local_wavemaker_pro_slot_wire(image, 0),
    } == {LOCAL_WAVEMAKER_PRO_UNUSED_ZERO, LOCAL_WAVEMAKER_PRO_UNUSED_EE}


@pytest.mark.asyncio
async def test_sentinel_qualification_writes_one_unused_slot_and_exactly_restores() -> None:
    original_left = _original_image()
    original_right = _original_image(invert=True)
    left = _ScheduleDevice("left", original_left)
    right = _ScheduleDevice("right", original_right)
    spec = TemporaryScheduleSpec(
        kind=TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        device_patches=(
            DeviceSchedulePatch(
                device_id="left",
                slots=(behavior_neutral_unused_slot_patch(original_left),),
            ),
            DeviceSchedulePatch(
                device_id="right",
                slots=(behavior_neutral_unused_slot_patch(original_right),),
            ),
        ),
    )

    result = await _controller(
        left,
        right,
        _MemoryStore(),
        LinkageSafetyInterlock(initially_permitted=True),
    ).run(spec)

    assert result.original_images_restored is True
    assert [len(write) for write in left.writes] == [1]
    assert [len(write) for write in right.writes] == [1]
    assert left.image == original_left
    assert right.image == original_right


@pytest.mark.asyncio
async def test_sentinel_qualification_rejects_an_observer_before_journal_or_write() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    store = _MemoryStore()
    spec = TemporaryScheduleSpec(
        kind=TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        device_patches=(
            DeviceSchedulePatch(
                device_id="left",
                slots=(behavior_neutral_unused_slot_patch(left.image),),
            ),
            DeviceSchedulePatch(
                device_id="right",
                slots=(behavior_neutral_unused_slot_patch(right.image),),
            ),
        ),
    )

    async def observe(_: TemporaryScheduleRecord) -> ObservationCompletion:
        return ObservationCompletion.DISARM_VERIFIED

    with pytest.raises(TemporarySchedulePreflightError):
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec, observe=observe)

    assert store.record is None
    assert left.writes == []
    assert right.writes == []


@pytest.mark.asyncio
async def test_field_schedule_rejects_flow_above_configured_device_limit() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    left._capabilities = left.capabilities.model_copy(  # noqa: SLF001 - deliberate fake
        update={"power_limits": PowerLimits(min_power=30, max_power=80)}
    )
    spec = TemporaryScheduleSpec(
        device_patches=(
            DeviceSchedulePatch(
                device_id="left",
                slots=_field_slots(before_flow=31, after_flow=85),
            ),
            DeviceSchedulePatch(
                device_id="right",
                slots=_field_slots(before_flow=32, after_flow=40),
            ),
        )
    )
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError):
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert store.record is None
    assert left.writes == []


@pytest.mark.asyncio
async def test_field_schedule_requires_matching_mode_boundaries_on_both_devices() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    spec = TemporaryScheduleSpec(
        device_patches=(
            DeviceSchedulePatch(
                device_id="left",
                slots=_field_slots(before_flow=31, after_flow=35, boundary_hour=12),
            ),
            DeviceSchedulePatch(
                device_id="right",
                slots=_field_slots(before_flow=32, after_flow=40, boundary_hour=13),
            ),
        )
    )
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError):
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert store.record is None
    assert left.writes == []
    assert right.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("master_after", "slave_before", "slave_after"),
    [
        (35, 40, 40),
        (40, 32, 40),
    ],
)
async def test_field_schedule_requires_distinguishable_slave_a_to_b_evidence(
    master_after: int,
    slave_before: int,
    slave_after: int,
) -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    spec = TemporaryScheduleSpec(
        device_patches=(
            DeviceSchedulePatch(
                device_id="left",
                slots=_field_slots(before_flow=31, after_flow=master_after),
            ),
            DeviceSchedulePatch(
                device_id="right",
                slots=_field_slots(
                    before_flow=slave_before,
                    after_flow=slave_after,
                ),
            ),
        )
    )
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError):
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert store.record is None
    assert left.writes == []
    assert right.writes == []


def test_field_spec_requires_all_48_slots_and_sentinel_kind_for_neutral_patch() -> None:
    with pytest.raises(ValueError, match="all 48"):
        TemporaryScheduleSpec(
            device_patches=(
                DeviceSchedulePatch(
                    device_id="left",
                    slots=(ScheduleSlotPatch.from_wire(0, _active_wire(flow=31)),),
                ),
                DeviceSchedulePatch(
                    device_id="right",
                    slots=(ScheduleSlotPatch.from_wire(0, _active_wire(flow=32)),),
                ),
            )
        )


@pytest.mark.asyncio
async def test_run_revalidates_a_model_copy_that_bypassed_spec_validators() -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    store = _MemoryStore()
    bypassed = _spec().model_copy(update={"device_patches": (_spec().device_patches[0],)})

    with pytest.raises(TemporarySchedulePreflightError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(bypassed)

    assert captured.value.code is TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
async def test_behavior_neutral_patch_fails_closed_if_slot_became_active() -> None:
    active = patch_local_wavemaker_pro_schedule_slot(
        _original_image(),
        0,
        _active_wire(flow=31),
    )
    left = _ScheduleDevice("left", active)
    right = _ScheduleDevice("right", _original_image(invert=True))
    toggle = ScheduleSlotPatch.unused_sentinel_toggle(
        0,
        LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    )
    spec = TemporaryScheduleSpec(
        kind=TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        device_patches=(
            DeviceSchedulePatch(device_id="left", slots=(toggle,)),
            DeviceSchedulePatch(
                device_id="right",
                slots=(behavior_neutral_unused_slot_patch(right.image),),
            ),
        ),
    )
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError):
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(spec)

    assert left.writes == []
    assert right.writes == []
    assert store.record is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_update",
    [
        {"online": False},
        {"error": "fault"},
        {"timer_enabled": True},
        {"linkage": LinkageRole.ASYNC_SLAVE},
    ],
)
async def test_unsafe_initial_state_is_rejected_before_journal_or_write(
    unsafe_update: dict[str, object],
) -> None:
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    left.safe_state = left.safe_state.model_copy(update=unsafe_update)
    store = _MemoryStore()

    with pytest.raises(TemporarySchedulePreflightError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.UNSAFE_INITIAL_STATE
    assert store.record is None
    assert left.writes == []
    assert right.writes == []


def _record() -> TemporaryScheduleRecord:
    spec = _spec()
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))
    assert left.physical_binding is not None
    assert right.physical_binding is not None
    now = datetime.now(UTC)
    return TemporaryScheduleRecord(
        operation_id=spec.operation_id,
        phase=TemporarySchedulePhase.PREPARED,
        spec=spec,
        snapshots=(
            ScheduleImageSnapshot.from_image(
                device_id="left",
                physical_binding=left.physical_binding,
                image=left.image,
            ),
            ScheduleImageSnapshot.from_image(
                device_id="right",
                physical_binding=right.physical_binding,
                image=right.image,
            ),
        ),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=30),
    )


def test_snapshot_journal_is_json_safe_private_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "temporary-schedule.json"
    store = JsonTemporaryScheduleJournalStore(path)
    record = _record()

    with store.lease():
        store.create(record)
        assert store.load() == record
        payload = json.loads(path.read_text())
        assert payload["snapshots"][0]["image_hex"] == record.snapshots[0].image_hex
        assert isinstance(payload["snapshots"][0]["image_hex"], str)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert store.confirms_lease_successor(record)
        store.clear()

    assert not path.exists()


def test_record_rejects_recovery_authority_extended_beyond_approved_spec() -> None:
    record = _record()
    extended = record.model_copy(update={"expires_at": record.expires_at + timedelta(seconds=1)})

    with pytest.raises(ValueError, match="approved recovery authority"):
        TemporaryScheduleRecord.model_validate(extended.model_dump(mode="python"))


@pytest.mark.asyncio
async def test_recovery_revalidates_records_from_non_json_store() -> None:
    record = _record()
    store = _MemoryStore()
    store.record = record.model_copy(update={"snapshots": record.snapshots[:1]})
    left = _ScheduleDevice("left", record.snapshots[0].image_bytes)
    right = _ScheduleDevice("right", record.snapshots[1].image_bytes)

    with pytest.raises(TemporaryScheduleRecoveryError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=False),
        ).recover_pending()

    assert captured.value.code is TemporaryScheduleErrorCode.JOURNAL_FAILED
    assert left.restore_images == []
    assert right.restore_images == []


@pytest.mark.asyncio
async def test_parent_fsync_failure_never_authorizes_a_schedule_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "temporary-schedule.json"
    store = JsonTemporaryScheduleJournalStore(path)
    left = _ScheduleDevice("left", _original_image())
    right = _ScheduleDevice("right", _original_image(invert=True))

    def fail_fsync() -> None:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(store, "_fsync_parent", fail_fsync)

    with pytest.raises(TemporaryScheduleApplyError) as captured:
        await _controller(
            left,
            right,
            store,
            LinkageSafetyInterlock(initially_permitted=True),
        ).run(_spec())

    assert captured.value.code is TemporaryScheduleErrorCode.JOURNAL_FAILED
    assert left.writes == []
    assert right.writes == []
    assert left.restore_images == []
    assert right.restore_images == []
    # Visibility after failed fsync is not accepted as write authority.
    assert store.load() is not None


@pytest.mark.asyncio
async def test_resumed_partial_rollback_rewrites_even_previously_restored_device() -> None:
    prepared = _record()
    expected_ids = tuple(patch.device_id for patch in prepared.spec.device_patches)
    recovery = prepared.model_copy(
        update={
            "phase": TemporarySchedulePhase.RECOVERY_REQUIRED,
            "updated_at": datetime.now(UTC),
            "stage_write_intent_device_ids": expected_ids,
            "staged_device_ids": expected_ids,
            "restored_device_ids": ("right",),
            "error_code": TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED,
        }
    )
    left = _ScheduleDevice(
        "left",
        patch_local_wavemaker_pro_schedule_slot(
            recovery.snapshots[0].image_bytes,
            0,
            _active_wire(flow=34),
        ),
    )
    right = _ScheduleDevice(
        "right",
        patch_local_wavemaker_pro_schedule_slot(
            recovery.snapshots[1].image_bytes,
            0,
            _active_wire(flow=36),
        ),
    )
    left.binding = recovery.snapshots[0].physical_binding
    right.binding = recovery.snapshots[1].physical_binding
    store = _MemoryStore()
    store.record = recovery
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=False),
    )

    assert await controller.manual_recover() is True

    assert left.restore_images == [recovery.snapshots[0].image_bytes]
    assert right.restore_images == [recovery.snapshots[1].image_bytes]
    assert store.record is None


@pytest.mark.asyncio
async def test_crash_recovery_reproves_timer_off_before_any_schedule_restore() -> None:
    prepared = _record()
    expected_ids = tuple(patch.device_id for patch in prepared.spec.device_patches)
    recovery = prepared.model_copy(
        update={
            "phase": TemporarySchedulePhase.RECOVERY_REQUIRED,
            "updated_at": datetime.now(UTC),
            "stage_write_intent_device_ids": expected_ids,
            "staged_device_ids": expected_ids,
            "error_code": TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED,
        }
    )
    left = _ScheduleDevice("left", recovery.snapshots[0].image_bytes)
    right = _ScheduleDevice("right", recovery.snapshots[1].image_bytes)
    left.binding = recovery.snapshots[0].physical_binding
    right.binding = recovery.snapshots[1].physical_binding
    left.state_after_reconnect = left.safe_state.model_copy(update={"timer_enabled": True})
    left.state_override_connect_call = 1
    store = _MemoryStore()
    store.record = recovery
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=False),
    )

    with pytest.raises(TemporaryScheduleRollbackUnsafeError) as captured:
        await controller.recover_pending()

    assert captured.value.code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED
    assert left.restore_images == []
    assert right.restore_images == []
    assert store.record is not None
    assert store.record.phase is TemporarySchedulePhase.RECOVERY_REQUIRED
    assert store.record.error_code is TemporaryScheduleErrorCode.CONTROL_DISARM_UNVERIFIED


@pytest.mark.asyncio
async def test_expired_automatic_recovery_requires_explicit_manual_authority() -> None:
    prepared = _record()
    expected_ids = tuple(patch.device_id for patch in prepared.spec.device_patches)
    now = datetime.now(UTC)
    expired = prepared.model_copy(
        update={
            "phase": TemporarySchedulePhase.RECOVERY_REQUIRED,
            "created_at": now - timedelta(hours=2),
            "updated_at": now - timedelta(hours=2),
            "expires_at": now
            - timedelta(hours=2)
            + timedelta(seconds=prepared.spec.recovery_authority_seconds),
            "stage_write_intent_device_ids": expected_ids,
            "error_code": TemporaryScheduleErrorCode.RESTORE_WRITE_FAILED,
        }
    )
    left = _ScheduleDevice("left", expired.snapshots[0].image_bytes)
    right = _ScheduleDevice("right", expired.snapshots[1].image_bytes)
    left.binding = expired.snapshots[0].physical_binding
    right.binding = expired.snapshots[1].physical_binding
    store = _MemoryStore()
    store.record = expired
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=False),
    )

    with pytest.raises(TemporaryScheduleRecoveryError) as captured:
        await controller.recover_pending()

    assert captured.value.code is TemporaryScheduleErrorCode.RECOVERY_AUTHORITY_EXPIRED
    assert left.restore_images == []
    assert await controller.manual_recover() is True


@pytest.mark.asyncio
async def test_completed_tombstone_clears_without_binding_or_hardware_access() -> None:
    prepared = _record()
    expected_ids = tuple(patch.device_id for patch in prepared.spec.device_patches)
    completed = prepared.model_copy(
        update={
            "phase": TemporarySchedulePhase.COMPLETED,
            "updated_at": datetime.now(UTC),
            "stage_write_intent_device_ids": expected_ids,
            "staged_device_ids": expected_ids,
            "restored_device_ids": tuple(reversed(expected_ids)),
        }
    )
    left = _ScheduleDevice("left", completed.snapshots[0].image_bytes)
    right = _ScheduleDevice("right", completed.snapshots[1].image_bytes)
    assert left.binding is not None
    left.binding = left.binding.model_copy(update={"config_fingerprint": "e" * 64})
    store = _MemoryStore()
    store.record = completed
    controller = _controller(
        left,
        right,
        store,
        LinkageSafetyInterlock(initially_permitted=False),
    )

    assert await controller.recover_pending() is True

    assert left.restore_images == []
    assert right.restore_images == []
    assert store.record is None


@pytest.mark.asyncio
async def test_new_controller_manually_recovers_a_durable_staged_journal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "temporary-schedule.json"
    initial_store = JsonTemporaryScheduleJournalStore(path)
    prepared = _record()
    expected_ids = tuple(patch.device_id for patch in prepared.spec.device_patches)
    staged = prepared.model_copy(
        update={
            "phase": TemporarySchedulePhase.STAGED,
            "updated_at": datetime.now(UTC),
            "stage_write_intent_device_ids": expected_ids,
            "staged_device_ids": expected_ids,
        }
    )
    with initial_store.lease():
        initial_store.create(staged)

    left_original = staged.snapshots[0].image_bytes
    right_original = staged.snapshots[1].image_bytes
    left = _ScheduleDevice(
        "left",
        patch_local_wavemaker_pro_schedule_slot(
            left_original,
            0,
            _active_wire(flow=32),
        ),
    )
    right = _ScheduleDevice(
        "right",
        patch_local_wavemaker_pro_schedule_slot(
            right_original,
            0,
            _active_wire(flow=40, mode=1),
        ),
    )
    left.binding = staged.snapshots[0].physical_binding
    right.binding = staged.snapshots[1].physical_binding
    recovered_store = JsonTemporaryScheduleJournalStore(path)
    controller = _controller(
        left,
        right,
        recovered_store,
        LinkageSafetyInterlock(initially_permitted=False),
    )

    assert await controller.manual_recover() is True

    assert left.image == left_original
    assert right.image == right_original
    assert left.writes == []
    assert right.writes == []
    assert left.restore_images == [left_original]
    assert right.restore_images == [right_original]
    assert recovered_store.load() is None


def test_snapshot_model_rejects_tampered_hex_digest() -> None:
    snapshot = _record().snapshots[0]

    with pytest.raises(ValueError, match="digest"):
        ScheduleImageSnapshot.model_validate(
            snapshot.model_copy(update={"image_sha256": "0" * 64}).model_dump()
        )
