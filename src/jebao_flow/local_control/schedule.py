"""Fresh master-image inheritance with per-slave, per-slot Flow overrides.

The compiler never reconstructs timing, mode or unused-slot bytes from an old manifest. Each call
starts from the master controller's current exact 432-byte image and changes byte 5 only for an
explicit active-slot override. The result is usable for both Sync and Async linkage modes; device
acceptance remains a separately qualified live fact.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.operator_capture.schedule_model import (
    PRO_SCHEDULE_SLOT_COUNT,
    SAFE_FLOW_MAXIMUM,
    SAFE_FLOW_MINIMUM,
    NativeLinkageMode,
    SlaveFlowOverride,
)
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY

_PRO_SLOT_SIZE = 9
_PRO_IMAGE_SIZE = PRO_SCHEDULE_SLOT_COUNT * _PRO_SLOT_SIZE


@dataclass(frozen=True, slots=True)
class FreshSlaveSchedule:
    """One stable physical slave plus its sparse active-slot Flow overrides."""

    target: LocalDeviceBinding
    flow_overrides: tuple[SlaveFlowOverride, ...] = ()

    def __post_init__(self) -> None:
        if self.target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("per-slot slave Flow is scoped to Local Wavemaker Pro")
        indexes = [item.slot_index for item in self.flow_overrides]
        if len(indexes) != len(set(indexes)):
            raise ValueError("slave Flow overrides must have unique slot indexes")


@dataclass(frozen=True, slots=True)
class FreshMasterSchedulePlan:
    linkage_mode: NativeLinkageMode
    master: LocalDeviceBinding
    slaves: tuple[FreshSlaveSchedule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.linkage_mode, NativeLinkageMode):
            raise TypeError("linkage mode must be sync or async")
        if not self.slaves:
            raise ValueError("fresh master schedule requires at least one slave")
        if self.master.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("fresh master schedule is scoped to Local Wavemaker Pro")
        names = [slave.target.logical_member for slave in self.slaves]
        if len(names) != len(set(names)):
            raise ValueError("fresh master schedule slave members must be unique")
        if self.master.logical_member in set(names):
            raise ValueError("master and slave logical members must be distinct")
        if any(slave.target.product_key != self.master.product_key for slave in self.slaves):
            raise ValueError("master and slaves must use the same product family")
        physical = [self.master.physical_identity_key]
        physical.extend(slave.target.physical_identity_key for slave in self.slaves)
        if len(physical) != len(set(physical)):
            raise ValueError("master and slaves must bind distinct physical devices")

    def public_dict(self) -> dict[str, object]:
        return {
            "linkage_mode": self.linkage_mode.value,
            "master": self.master.model_dump(mode="json"),
            "slaves": [
                {
                    "target": slave.target.model_dump(mode="json"),
                    "flow_overrides": [
                        {"slot_index": item.slot_index, "flow": item.flow}
                        for item in slave.flow_overrides
                    ],
                }
                for slave in self.slaves
            ],
        }


@dataclass(frozen=True, slots=True)
class CompiledSlaveSchedule:
    linkage_mode: NativeLinkageMode
    logical_member: str
    product_key: str
    physical_identity_key: str
    master_image_sha256: str
    image_sha256: str
    slots_differing_from_master: tuple[int, ...]
    slots_changed_from_current: tuple[int, ...]
    image: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.image) != _PRO_IMAGE_SIZE:
            raise ValueError("compiled slave schedule must be exactly 432 bytes")

    def public_dict(self) -> dict[str, object]:
        return {
            "linkage_mode": self.linkage_mode.value,
            "logical_member": self.logical_member,
            "product_key": self.product_key,
            "physical_identity_key": self.physical_identity_key,
            "master_image_sha256": self.master_image_sha256,
            "image_sha256": self.image_sha256,
            "slots_differing_from_master": list(self.slots_differing_from_master),
            "slots_changed_from_current": list(self.slots_changed_from_current),
            "slot_count": PRO_SCHEDULE_SLOT_COUNT,
            "raw_image_exposed": False,
        }


def compile_slave_schedules(
    plan: FreshMasterSchedulePlan,
    current_master_image: bytes | bytearray | memoryview,
    current_slave_images: Mapping[str, bytes | bytearray | memoryview],
) -> tuple[CompiledSlaveSchedule, ...]:
    """Compile all slave images from current master and current slave observations.

    Unspecified active-slot Flow values stay with each current slave; timing, mode and all other
    bytes come from the current master. No stored image or previous manifest is accepted.
    """

    master, active = _validate_current_master_image(current_master_image)
    expected_slave_keys = {
        slave.target.physical_identity_key
        for slave in plan.slaves
    }
    if set(current_slave_images) != expected_slave_keys:
        raise ValueError("current slave images must match the plan's stable physical identities")
    master_digest = hashlib.sha256(master).hexdigest()
    for index in active:
        start = index * _PRO_SLOT_SIZE
        slot = master[start : start + _PRO_SLOT_SIZE]
        mode_code = slot[4]
        flow = slot[5]
        if mode_code == 7 and flow == 0:
            continue
        if not SAFE_FLOW_MINIMUM <= flow <= SAFE_FLOW_MAXIMUM:
            raise ValueError(
                "current master slot Flow is outside the declared "
                f"{SAFE_FLOW_MINIMUM}..{SAFE_FLOW_MAXIMUM} app range"
            )

    compiled: list[CompiledSlaveSchedule] = []
    for slave in plan.slaves:
        current_slave_value = current_slave_images[slave.target.physical_identity_key]
        current_slave, current_slave_active = _validate_current_master_image(current_slave_value)
        overrides = {item.slot_index: item.flow for item in slave.flow_overrides}
        inactive = sorted(set(overrides) - active)
        if inactive:
            raise ValueError("slave Flow overrides require currently active master slots")
        image = bytearray(master)
        for slot_index in sorted(active):
            start = slot_index * _PRO_SLOT_SIZE
            master_slot = master[start : start + _PRO_SLOT_SIZE]
            flow = overrides.get(
                slot_index,
                current_slave[start + 5]
                if slot_index in current_slave_active
                else master_slot[5],
            )
            if not (flow == 0 and master_slot[4] == 7) and not (
                SAFE_FLOW_MINIMUM <= flow <= SAFE_FLOW_MAXIMUM
            ):
                raise ValueError(
                    "current slave slot Flow is outside the declared "
                    f"{SAFE_FLOW_MINIMUM}..{SAFE_FLOW_MAXIMUM} app range"
                )
            image[start + 5] = flow
        exact, _ = _validate_current_master_image(image)
        _assert_only_flow_differs(master, exact)
        compiled.append(
            CompiledSlaveSchedule(
                linkage_mode=plan.linkage_mode,
                logical_member=slave.target.logical_member,
                product_key=slave.target.product_key,
                physical_identity_key=slave.target.physical_identity_key,
                master_image_sha256=master_digest,
                image_sha256=hashlib.sha256(exact).hexdigest(),
                slots_differing_from_master=_slot_differences(master, exact),
                slots_changed_from_current=_slot_differences(current_slave, exact),
                image=exact,
            )
        )
    return tuple(compiled)


def compile_fresh_slave_flow_update(
    *,
    linkage_mode: NativeLinkageMode,
    master: LocalDeviceBinding,
    slave: LocalDeviceBinding,
    current_master_image: bytes | bytearray | memoryview,
    current_slave_image: bytes | bytearray | memoryview,
    slot_index: int,
    flow: int,
) -> CompiledSlaveSchedule:
    """Change one slave slot while deriving every other Flow from current device bytes.

    No previous desired-state file participates. Every call copies the current master timing and
    mode bytes, preserves each currently active slave Flow, and changes only the requested slot.
    """

    FreshMasterSchedulePlan(
        linkage_mode=linkage_mode,
        master=master,
        slaves=(FreshSlaveSchedule(target=slave),),
    )
    if isinstance(slot_index, bool) or not isinstance(slot_index, int):
        raise TypeError("slave Flow slot index must be an integer")
    if not 0 <= slot_index < PRO_SCHEDULE_SLOT_COUNT:
        raise ValueError("slave Flow slot index must be between 0 and 47")
    if isinstance(flow, bool) or not isinstance(flow, int):
        raise TypeError("slave Flow must be an integer")
    if not SAFE_FLOW_MINIMUM <= flow <= SAFE_FLOW_MAXIMUM:
        raise ValueError(
            "slave Flow must stay inside the declared "
            f"{SAFE_FLOW_MINIMUM}..{SAFE_FLOW_MAXIMUM} app range"
        )

    master_image, active = _validate_current_master_image(current_master_image)
    slave_image, slave_active = _validate_current_master_image(current_slave_image)
    if slot_index not in active:
        raise ValueError("slave Flow update requires a currently active master slot")

    image = bytearray(master_image)
    for index in sorted(active):
        start = index * _PRO_SLOT_SIZE
        master_slot = master_image[start : start + _PRO_SLOT_SIZE]
        slave_slot = slave_image[start : start + _PRO_SLOT_SIZE]
        selected_flow = (
            flow
            if index == slot_index
            else slave_slot[5]
            if index in slave_active
            else master_slot[5]
        )
        if not (
            selected_flow == 0 and master_slot[4] == 7
        ) and not SAFE_FLOW_MINIMUM <= selected_flow <= SAFE_FLOW_MAXIMUM:
            raise ValueError(
                "current slave slot Flow is outside the declared "
                f"{SAFE_FLOW_MINIMUM}..{SAFE_FLOW_MAXIMUM} app range"
            )
        image[start + 5] = selected_flow

    exact = bytes(image)
    _assert_only_flow_differs(master_image, exact)
    return CompiledSlaveSchedule(
        linkage_mode=linkage_mode,
        logical_member=slave.logical_member,
        product_key=slave.product_key,
        physical_identity_key=slave.physical_identity_key,
        master_image_sha256=hashlib.sha256(master_image).hexdigest(),
        image_sha256=hashlib.sha256(exact).hexdigest(),
        slots_differing_from_master=_slot_differences(master_image, exact),
        slots_changed_from_current=_slot_differences(slave_image, exact),
        image=exact,
    )


def _assert_only_flow_differs(master: bytes, slave: bytes) -> None:
    for index, (master_byte, slave_byte) in enumerate(zip(master, slave, strict=True)):
        if master_byte == slave_byte:
            continue
        if index % _PRO_SLOT_SIZE != 5:
            raise AssertionError("compiled slave schedule changed a non-Flow byte")


def _slot_differences(left: bytes, right: bytes) -> tuple[int, ...]:
    return tuple(
        index
        for index in range(PRO_SCHEDULE_SLOT_COUNT)
        if left[index * _PRO_SLOT_SIZE : (index + 1) * _PRO_SLOT_SIZE]
        != right[index * _PRO_SLOT_SIZE : (index + 1) * _PRO_SLOT_SIZE]
    )


def _validate_current_master_image(
    value: bytes | bytearray | memoryview,
) -> tuple[bytes, frozenset[int]]:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("current master schedule image must be bytes-like")
    image = bytes(value)
    if len(image) != _PRO_IMAGE_SIZE:
        raise ValueError("current master schedule image must be exactly 432 bytes")
    active = frozenset(
        index
        for index in range(PRO_SCHEDULE_SLOT_COUNT)
        if image[index * _PRO_SLOT_SIZE : (index + 1) * _PRO_SLOT_SIZE]
        not in {bytes(_PRO_SLOT_SIZE), bytes([0xEE]) * _PRO_SLOT_SIZE}
    )
    return image, active


__all__ = [
    "CompiledSlaveSchedule",
    "FreshMasterSchedulePlan",
    "FreshSlaveSchedule",
    "compile_fresh_slave_flow_update",
    "compile_slave_schedules",
]
