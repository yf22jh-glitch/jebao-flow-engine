from dataclasses import replace

import pytest

from jebao_flow.local_control.wavemaker_restore import (
    WavemakerRestoreSnapshot,
    compare_restore_readback,
    detach_sequence,
)
from tests.unit.test_local_control_device_writer import _binding, _status


def _snapshot():
    return WavemakerRestoreSnapshot("attach_0001", _binding(), _status(schedule=b"\xee" * 432))


def test_timer_off_schedule_is_still_owned_and_clock_auto_are_not_restore_targets():
    snapshot = _snapshot()
    assert snapshot.controls["TimerON"] is False
    assert snapshot.image == b"\xee" * 432
    assert len(snapshot.controls) == 8
    actual = bytearray(snapshot.baseline_status)
    actual[443:451] = bytes((20, 26, 9, 15, 0, 12, 34, 56))
    assert compare_restore_readback(snapshot, target=_binding(), status=bytes(actual))["complete"]
    assert snapshot.public_dict()["current_state_verified"] is False
    assert b"\xee" * 432 not in repr(snapshot).encode()


@pytest.mark.parametrize("offset,value", ((2, 41), (3, 34), (0, 2), (11, 0)))
def test_restore_requires_both_exact_image_and_all_controls(offset, value):
    snapshot = _snapshot()
    actual = bytearray(snapshot.baseline_status)
    actual[offset] = value
    report = compare_restore_readback(snapshot, target=_binding(), status=bytes(actual))
    assert report["complete"] is False
    assert report["schedule_match"] is (offset != 11)


def test_snapshot_is_immutable_and_identity_cannot_be_substituted():
    snapshot = _snapshot()
    returned = snapshot.controls
    returned["Flow"] = 70
    assert snapshot.controls["Flow"] == 40
    with pytest.raises(TypeError):
        replace(snapshot, baseline_status=bytearray(snapshot.baseline_status))
    with pytest.raises(ValueError, match="결속"):
        compare_restore_readback(
            snapshot,
            target=_binding().model_copy(update={"physical_identity_key": "b" * 64}),
            status=snapshot.baseline_status,
        )


def test_detach_orders_safe_controls_before_original_image_and_complete_readback():
    stages = detach_sequence(_snapshot())
    assert [stage["stage"] for stage in stages] == [
        "detach_atomic",
        "verify_disarmed",
        "restore_image",
        "restore_controls",
        "verify_complete",
    ]
    assert stages[0]["expected"]["TimerON"] is False
    assert stages[0]["expected"]["Linkage"] == "independent"
    assert stages[0]["expected"]["Mode"] == "constant"
    assert stages[0]["expected"]["Flow"] == 30
    assert stages[0]["expected"]["Frequency"] == 20
    assert stages[1]["expected"]["SwitchON"] is True
    assert all(stage["logical_member"] == _binding().logical_member for stage in stages)
    assert stages[-2]["expected"] == _snapshot().controls
