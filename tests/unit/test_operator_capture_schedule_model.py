from dataclasses import replace

import pytest

from jebao_flow.operator_capture.capability import AppExpressibility, DeviceSupport
from jebao_flow.operator_capture.schedule_model import (
    CommonSlaveFlow,
    NativeLinkageMode,
    NativeSchedulePlan,
    ProScheduleSlot,
    ScheduleClockOwnership,
    SlaveFlowOverride,
    SlaveSchedule,
)
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY


def _slots() -> tuple[ProScheduleSlot, ...]:
    return tuple(
        ProScheduleSlot(
            index=index,
            start_minute=index * 30,
            end_minute=(index + 1) * 30,
            mode="constant",
            flow=40,
            frequency=50,
            feed_time=5,
            custom_frequency=50,
        )
        for index in range(48)
    )


def test_slave_inherits_entire_master_schedule_and_overrides_only_flow() -> None:
    plan = NativeSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        master_slots=_slots(),
        slaves=(
            SlaveSchedule(
                logical_member="slave_a",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                flow_overrides=(
                    SlaveFlowOverride(slot_index=0, flow=35),
                    SlaveFlowOverride(slot_index=47, flow=60),
                ),
            ),
            SlaveSchedule(
                logical_member="slave_b",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                flow_overrides=(SlaveFlowOverride(slot_index=0, flow=45),),
            ),
        ),
    )

    slave = plan.materialize_slave_slots("slave_a")

    assert slave[0].flow == 35
    assert slave[47].flow == 60
    assert slave[1:] != ()
    for master_slot, slave_slot in zip(plan.master_slots, slave, strict=True):
        assert master_slot.index == slave_slot.index
        assert master_slot.start_minute == slave_slot.start_minute
        assert master_slot.end_minute == slave_slot.end_minute
        assert master_slot.mode == slave_slot.mode
        assert master_slot.frequency == slave_slot.frequency
        assert master_slot.feed_time == slave_slot.feed_time
        assert master_slot.custom_frequency == slave_slot.custom_frequency
    assert plan.per_slot_flow_app is AppExpressibility.INFEASIBLE
    assert plan.per_slot_flow_device is DeviceSupport.UNKNOWN
    assert plan.clock_ownership is ScheduleClockOwnership.DEVICE_OWNED
    assert plan.materialize_slave_slots("slave_b")[0].flow == 45


@pytest.mark.parametrize("flow", [0, 29, 101])
def test_schedule_model_keeps_declared_app_range(flow: int) -> None:
    with pytest.raises(ValueError, match="30..100"):
        SlaveFlowOverride(slot_index=0, flow=flow)


def test_schedule_model_accepts_the_app_declared_maximum() -> None:
    assert SlaveFlowOverride(slot_index=0, flow=100).flow == 100


def test_master_schedule_requires_all_ordered_slots() -> None:
    with pytest.raises(ValueError, match="48-position slot vector"):
        NativeSchedulePlan(
            linkage_mode=NativeLinkageMode.SYNC,
            master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            master_slots=_slots()[:-1],
            slaves=(
                SlaveSchedule(
                    logical_member="slave",
                    product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                ),
            ),
        )


def test_schedule_model_cannot_claim_device_support() -> None:
    with pytest.raises(ValueError, match="cannot be promoted"):
        NativeSchedulePlan(
            linkage_mode=NativeLinkageMode.ASYNC,
            master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            master_slots=_slots(),
            slaves=(
                SlaveSchedule(
                    logical_member="slave",
                    product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                ),
            ),
            per_slot_flow_device="qualified",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("linkage_mode", list(NativeLinkageMode))
def test_sync_and_async_support_independent_flow_for_every_slave_slot(
    linkage_mode: NativeLinkageMode,
) -> None:
    overrides = tuple(
        SlaveFlowOverride(slot_index=index, flow=30 + index % 46) for index in range(48)
    )
    plan = NativeSchedulePlan(
        linkage_mode=linkage_mode,
        master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        master_slots=_slots(),
        slaves=(
            SlaveSchedule(
                logical_member="slave_a",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                flow_overrides=overrides,
            ),
            SlaveSchedule(
                logical_member="slave_b",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            ),
        ),
    )

    first = plan.materialize_slave_slots("slave_a")
    second = plan.materialize_slave_slots("slave_b")

    assert [slot.flow for slot in first] == [item.flow for item in overrides]
    assert [slot.flow for slot in second] == [slot.flow for slot in plan.master_slots]
    assert (
        CommonSlaveFlow(
            logical_member="slave_a",
            product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            flow=42,
        ).flow
        == 42
    )


def test_missing_override_and_explicit_master_value_remain_distinct_intent() -> None:
    original = _slots()
    plan = NativeSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        master_slots=original,
        slaves=(
            SlaveSchedule(
                logical_member="inherit",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            ),
            SlaveSchedule(
                logical_member="explicit",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                flow_overrides=(SlaveFlowOverride(slot_index=0, flow=40),),
            ),
        ),
    )
    changed_master = (replace(original[0], flow=55), *original[1:])
    changed = replace(plan, master_slots=changed_master)

    assert changed.materialize_slave_slots("inherit")[0].flow == 55
    assert changed.materialize_slave_slots("explicit")[0].flow == 40
    assert changed.public_dict()["slaves"][1]["flow_overrides"] == [{"slot_index": 0, "flow": 40}]


def test_schedule_clock_is_explicitly_device_owned() -> None:
    with pytest.raises(ValueError, match="device clock"):
        NativeSchedulePlan(
            linkage_mode=NativeLinkageMode.SYNC,
            master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            master_slots=_slots(),
            slaves=(
                SlaveSchedule(
                    logical_member="slave",
                    product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                ),
            ),
            clock_ownership="controller_owned",  # type: ignore[arg-type]
        )


def test_schedule_model_rejects_non_pro_member_scope() -> None:
    with pytest.raises(ValueError, match="Local Wavemaker Pro"):
        SlaveSchedule(logical_member="wrong_family", product_key="1d8c63eaccac4205b92c84d77d5a08fb")


def test_common_slave_flow_is_not_given_schedule_precedence() -> None:
    common = CommonSlaveFlow(
        logical_member="slave",
        product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        flow=45,
    )
    plan = NativeSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        master_slots=_slots(),
        slaves=(
            SlaveSchedule(
                logical_member="slave",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            ),
        ),
    )

    assert common.flow == 45
    assert plan.materialize_slave_slots("slave")[0].flow == 40
    assert "common_flow" not in plan.public_dict()["slaves"][0]


def test_overlap_is_preserved_by_slot_index_without_guessed_precedence() -> None:
    slots = list(_slots())
    slots[1] = replace(slots[1], start_minute=0, end_minute=60)
    plan = NativeSchedulePlan(
        linkage_mode=NativeLinkageMode.SYNC,
        master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        master_slots=tuple(slots),
        slaves=(
            SlaveSchedule(
                logical_member="slave",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            ),
        ),
    )

    assert plan.public_dict()["timing_interpretation"] == "indexed_only_precedence_unknown"
    assert plan.master_slots[0].start_minute == plan.master_slots[1].start_minute


def test_irrelevant_schedule_parameters_accept_wire_domain_zero() -> None:
    slot = ProScheduleSlot(
        index=0,
        start_minute=0,
        end_minute=0,
        mode="feed",
        flow=30,
        frequency=0,
        feed_time=0,
        custom_frequency=0,
    )

    assert slot.start_minute == slot.end_minute == 0
    assert slot.frequency == slot.feed_time == slot.custom_frequency == 0


def test_unused_master_slots_remain_unused_for_every_slave() -> None:
    slots: list[ProScheduleSlot | None] = list(_slots())
    slots[7] = None
    plan = NativeSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        master_slots=tuple(slots),
        slaves=(
            SlaveSchedule(
                logical_member="slave",
                product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            ),
        ),
    )

    assert plan.materialize_slave_slots("slave")[7] is None
    assert plan.public_dict()["master_slots"][7] is None


def test_slave_override_cannot_turn_an_unused_master_slot_into_a_schedule() -> None:
    slots: list[ProScheduleSlot | None] = list(_slots())
    slots[7] = None

    with pytest.raises(ValueError, match="active master schedule slot"):
        NativeSchedulePlan(
            linkage_mode=NativeLinkageMode.SYNC,
            master_product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
            master_slots=tuple(slots),
            slaves=(
                SlaveSchedule(
                    logical_member="slave",
                    product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
                    flow_overrides=(SlaveFlowOverride(slot_index=7, flow=40),),
                ),
            ),
        )
