"""App-source command fixtures only; no fake firmware or hardware acceptance claims."""

import pytest

from jebao_flow.local_control.bar_timer import prepare_bar_timer_command


def _image(*rows: tuple[int, ...]) -> bytes:
    return b"".join(bytes(row) for row in rows) + b"\xee" * (384 - len(rows) * 8)


def _plan(enabled=True, *, image=None, minute=600, current=None):
    return prepare_bar_timer_command(
        enabled,
        current or {"Flow": 40, "Mode": "constant", "Frequency": 80, "PulseTide": True},
        schedule_image=image if image is not None else _image((0, 0, 24, 0, 4, 40, 0, 0)),
        local_minute=minute,
        minimum_flow=30,
        maximum_flow=45,
    )


def test_on_uses_schedule_not_stale_auto_and_never_changes_power():
    command = _plan(current={"AutoFlow": 100, "AutoMode": "random"})
    assert command.fields == {
        "TimerON": True, "FeedSwitch": False, "Linkage": "independent",
        "AutoMode": "constant", "AutoFlow": 40, "AutoFreq": 0, "AutoPulseTide": False,
    }
    assert command.persistent_readback == {
        "TimerON": True, "FeedSwitch": False, "Linkage": "independent",
    }
    assert command.selected_slot == 0
    assert "SwitchON" not in command.fields


def test_off_copies_fresh_manual_values_and_classic_pulse_only():
    assert _plan(False).fields == {
        "TimerON": False, "Flow": 40, "Mode": "constant", "Frequency": 80,
        "PulseTide": False, "Linkage": "independent",
    }
    current = {"Flow": 39, "Mode": "classic", "Frequency": 50, "PulseTide": True}
    command = _plan(False, current=current)
    assert command.fields["PulseTide"] is True
    assert command.fields["Flow"] == 39
    assert command.persistent_readback == command.fields
    assert not any(key.startswith("Auto") for key in command.fields)


def test_all_future_running_slots_are_bounded_not_only_the_current_slot():
    with pytest.raises(ValueError, match="slot 1 flow 100"):
        _plan(image=_image((0, 0, 12, 0, 4, 40, 0, 0), (12, 0, 24, 0, 3, 100, 0, 0)))


def test_off_does_not_activate_unbounded_schedule_but_checks_latent_manual_output():
    assert _plan(False, image=_image((0, 0, 24, 0, 3, 100, 0, 0))).fields["TimerON"] is False
    with pytest.raises(ValueError, match="manual flow"):
        _plan(False, current={"Flow": 100})


def test_release_app_zero_rows_are_not_silently_treated_as_empty():
    image = bytes((0, 0, 24, 0, 4, 40, 0, 0)) + bytes(376)
    command = _plan(image=image)
    assert command.fields["AutoMode"] == "stopped"
    assert command.fields["AutoFlow"] == 0
    assert command.selected_slot == 47


def test_generated_gap_can_override_real_slot_at_inclusive_boundary():
    image = _image((1, 0, 2, 0, 4, 40, 0, 0))
    assert _plan(image=image, minute=90).fields["AutoMode"] == "constant"
    assert _plan(image=image, minute=120).fields["AutoMode"] == "stopped"
    assert _plan(image=image, minute=180).fields["AutoFlow"] == 0


def test_adjacent_rows_use_app_last_match_at_shared_boundary():
    image = _image((0, 0, 12, 0, 4, 40, 0, 0), (12, 0, 24, 0, 1, 39, 5, 1))
    assert _plan(image=image, minute=719).fields["AutoFlow"] == 40
    assert _plan(image=image, minute=720).fields["AutoFlow"] == 39


@pytest.mark.parametrize("minute", [-1, 1440, True])
def test_invalid_clock_input_is_not_invented(minute):
    with pytest.raises(ValueError, match="local minute"):
        _plan(minute=minute)


def test_incomplete_image_is_not_used_for_on():
    with pytest.raises(ValueError, match="384-byte"):
        _plan(image=b"short")
