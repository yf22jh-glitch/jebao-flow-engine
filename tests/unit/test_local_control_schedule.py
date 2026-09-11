from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.schedule import (
    FreshMasterSchedulePlan,
    FreshSlaveSchedule,
    compile_fresh_slave_flow_update,
    compile_slave_schedules,
)
from jebao_flow.operator_capture.schedule_model import (
    NativeLinkageMode,
    SlaveFlowOverride,
)
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY


def _binding(member: str, identity_character: str) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key=LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        physical_identity_key=identity_character * 64,
        logical_role=member,
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _slot(*, mode: int = 2, flow: int = 40, feed_time: int = 0) -> bytes:
    return bytes((0, 0, 1, 0, mode, flow, 20, feed_time, 10))


def _master_image(*slots: bytes) -> bytes:
    return b"".join(slots) + bytes([0xEE]) * (432 - 9 * len(slots))


@pytest.mark.parametrize("mode", (NativeLinkageMode.SYNC, NativeLinkageMode.ASYNC))
def test_each_slave_inherits_exact_master_and_changes_only_its_flow_bytes(
    mode: NativeLinkageMode,
) -> None:
    master = _master_image(_slot(flow=41), _slot(flow=42))
    plan = FreshMasterSchedulePlan(
        linkage_mode=mode,
        master=_binding("master", "a"),
        slaves=(
            FreshSlaveSchedule(
                target=_binding("left", "b"),
                flow_overrides=(SlaveFlowOverride(slot_index=0, flow=51),),
            ),
            FreshSlaveSchedule(
                target=_binding("right", "c"),
                flow_overrides=(SlaveFlowOverride(slot_index=1, flow=61),),
            ),
        ),
    )

    left, right = compile_slave_schedules(
        plan,
        master,
        {"b" * 64: master, "c" * 64: master},
    )

    assert left.image[:9] == _slot(flow=51)
    assert left.image[9:18] == _slot(flow=42)
    assert right.image[:9] == _slot(flow=41)
    assert right.image[9:18] == _slot(flow=61)
    assert left.slots_differing_from_master == (0,)
    assert right.slots_differing_from_master == (1,)
    assert left.physical_identity_key == "b" * 64
    assert "image" not in left.public_dict()


def test_compiler_uses_current_image_on_every_call_without_stored_schedule_input() -> None:
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=_binding("master", "a"),
        slaves=(FreshSlaveSchedule(target=_binding("slave", "b")),),
    )

    first_master = _master_image(_slot(flow=40))
    second_master = _master_image(_slot(flow=55))
    [first] = compile_slave_schedules(plan, first_master, {"b" * 64: first_master})
    [second] = compile_slave_schedules(plan, second_master, {"b" * 64: second_master})

    assert first.image[:9] == _slot(flow=40)
    assert second.image[:9] == _slot(flow=55)
    assert first.master_image_sha256 != second.master_image_sha256


def test_feed_slot_zero_flow_is_preserved_as_existing_audited_exception() -> None:
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.SYNC,
        master=_binding("master", "a"),
        slaves=(FreshSlaveSchedule(target=_binding("slave", "b")),),
    )

    [compiled] = compile_slave_schedules(
        plan,
        _master_image(_slot(mode=7, flow=0, feed_time=15)),
        {"b" * 64: _master_image(_slot(mode=7, flow=0, feed_time=15))},
    )

    assert compiled.image[:9] == _slot(mode=7, flow=0, feed_time=15)


def test_untouched_app_feed_duration_above_old_decoder_limit_is_preserved() -> None:
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=_binding("master", "a"),
        slaves=(FreshSlaveSchedule(target=_binding("slave", "b")),),
    )
    current = _master_image(_slot(mode=7, flow=0, feed_time=120))

    [compiled] = compile_slave_schedules(plan, current, {"b" * 64: current})

    assert compiled.image == current


def test_active_non_feed_master_flow_outside_app_range_is_rejected() -> None:
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.SYNC,
        master=_binding("master", "a"),
        slaves=(FreshSlaveSchedule(target=_binding("slave", "b")),),
    )

    with pytest.raises(ValueError, match="30..100"):
        invalid = _master_image(_slot(flow=101))
        compile_slave_schedules(plan, invalid, {"b" * 64: invalid})


def test_override_for_unused_slot_and_duplicate_physical_binding_are_rejected() -> None:
    with pytest.raises(ValueError, match="distinct physical"):
        FreshMasterSchedulePlan(
            linkage_mode=NativeLinkageMode.SYNC,
            master=_binding("master", "a"),
            slaves=(FreshSlaveSchedule(target=_binding("slave", "a")),),
        )
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.SYNC,
        master=_binding("master", "a"),
        slaves=(
            FreshSlaveSchedule(
                target=_binding("slave", "b"),
                flow_overrides=(SlaveFlowOverride(slot_index=1, flow=50),),
            ),
        ),
    )
    with pytest.raises(ValueError, match="active"):
        current = _master_image(_slot())
        compile_slave_schedules(plan, current, {"b" * 64: current})


def test_single_flow_update_uses_current_master_and_preserves_other_slave_flows() -> None:
    compiled = compile_fresh_slave_flow_update(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=_binding("master", "a"),
        slave=_binding("slave", "b"),
        current_master_image=_master_image(_slot(flow=40), _slot(flow=45)),
        current_slave_image=_master_image(
            bytes((0, 0, 1, 0, 2, 50, 1, 2, 3)),
            bytes((0, 0, 1, 0, 2, 60, 4, 5, 6)),
        ),
        slot_index=1,
        flow=55,
    )

    assert compiled.image[:9] == _slot(flow=50)
    assert compiled.image[9:18] == _slot(flow=55)
    assert compiled.slots_differing_from_master == (0, 1)
    assert compiled.slots_changed_from_current == (0, 1)


def test_bulk_compiler_preserves_each_current_slave_flow_when_override_is_unspecified() -> None:
    master = _master_image(_slot(flow=40), _slot(flow=45))
    current_slave = _master_image(
        _slot(flow=50),
        bytes((1, 0, 2, 0, 2, 60, 9, 9, 9)),
    )
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=_binding("master", "a"),
        slaves=(
            FreshSlaveSchedule(
                target=_binding("slave", "b"),
                flow_overrides=(SlaveFlowOverride(slot_index=1, flow=55),),
            ),
        ),
    )

    [compiled] = compile_slave_schedules(plan, master, {"b" * 64: current_slave})

    assert compiled.image[:9] == _slot(flow=50)
    assert compiled.image[9:18] == _slot(flow=55)
    assert compiled.slots_differing_from_master == (0, 1)
    assert compiled.slots_changed_from_current == (1,)


def test_bulk_compiler_binds_current_images_by_physical_identity_not_mapping_order() -> None:
    master = _master_image(_slot(flow=40))
    left_current = _master_image(_slot(flow=50))
    right_current = _master_image(_slot(flow=60))
    plan = FreshMasterSchedulePlan(
        linkage_mode=NativeLinkageMode.SYNC,
        master=_binding("master", "a"),
        slaves=(
            FreshSlaveSchedule(target=_binding("left", "b")),
            FreshSlaveSchedule(target=_binding("right", "c")),
        ),
    )

    left, right = compile_slave_schedules(
        plan,
        master,
        {"c" * 64: right_current, "b" * 64: left_current},
    )

    assert left.image[:9] == _slot(flow=50)
    assert right.image[:9] == _slot(flow=60)
    with pytest.raises(ValueError, match="stable physical identities"):
        compile_slave_schedules(plan, master, {"b" * 64: left_current})


def test_single_flow_update_preserves_zero_flow_on_other_current_feed_slot() -> None:
    compiled = compile_fresh_slave_flow_update(
        linkage_mode=NativeLinkageMode.ASYNC,
        master=_binding("master", "a"),
        slave=_binding("slave", "b"),
        current_master_image=_master_image(
            _slot(mode=7, flow=35, feed_time=15),
            _slot(flow=45),
        ),
        current_slave_image=_master_image(
            _slot(mode=7, flow=0, feed_time=15),
            _slot(flow=60),
        ),
        slot_index=1,
        flow=55,
    )

    assert compiled.image[:9] == _slot(mode=7, flow=0, feed_time=15)
    assert compiled.image[9:18] == _slot(flow=55)


def test_single_flow_update_rejects_inactive_requested_slot() -> None:
    with pytest.raises(ValueError, match="currently active"):
        compile_fresh_slave_flow_update(
            linkage_mode=NativeLinkageMode.SYNC,
            master=_binding("master", "a"),
            slave=_binding("slave", "b"),
            current_master_image=bytes([0xEE]) * 432,
            current_slave_image=bytes([0xEE]) * 432,
            slot_index=1,
            flow=45,
        )


def test_single_flow_update_keeps_master_slave_identity_invariants() -> None:
    with pytest.raises(ValueError, match="distinct physical"):
        compile_fresh_slave_flow_update(
            linkage_mode=NativeLinkageMode.SYNC,
            master=_binding("master", "a"),
            slave=_binding("slave", "a"),
            current_master_image=_master_image(_slot()),
            current_slave_image=_master_image(_slot()),
            slot_index=0,
            flow=45,
        )
