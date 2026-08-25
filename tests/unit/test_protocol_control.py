import pytest

from jebao_flow.protocol.control import build_control_payload
from jebao_flow.protocol.profiles import DOSING_PUMP, LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO


def test_control_payload_reverses_flags_and_places_multibyte_bit_group() -> None:
    payload = build_control_payload(
        LOCAL_WAVEMAKER,
        {"SwitchON": True, "Mode": "constant", "Flow": 55},
    )

    flags = payload[1:9]
    values = payload[9:]
    assert payload[0] == 0x01
    assert flags == bytes.fromhex("0000000000000121")
    assert values[:3] == bytes.fromhex("006137")
    assert len(values) == 400


def test_control_payload_uses_complete_vendor_buffer_sizes() -> None:
    payload = build_control_payload(LOCAL_WAVEMAKER_PRO, {"Mode": "tidal", "Flow": 30})

    assert len(payload) == 1 + 8 + 451
    assert payload[1:9] == bytes.fromhex("0000000000000018")
    assert payload[9 + 1] == 4
    assert payload[9 + 2] == 30


@pytest.mark.parametrize("value", [-1, 101, 50.5])
def test_control_payload_rejects_invalid_numeric_values(value: object) -> None:
    with pytest.raises(ValueError):
        build_control_payload(LOCAL_WAVEMAKER_PRO, {"Flow": value})


def test_control_payload_rejects_non_boolean_switch() -> None:
    with pytest.raises(TypeError, match="expected a boolean"):
        build_control_payload(LOCAL_WAVEMAKER_PRO, {"SwitchON": "on"})


def test_control_payload_rejects_fault_and_unknown_datapoints() -> None:
    with pytest.raises(ValueError, match="not writable"):
        build_control_payload(LOCAL_WAVEMAKER_PRO, {"Fault_UART": True})
    with pytest.raises(KeyError, match="no datapoint"):
        build_control_payload(LOCAL_WAVEMAKER_PRO, {"missing": 1})


def test_control_is_disabled_for_out_of_scope_dosing_pump() -> None:
    with pytest.raises(ValueError, match="control is not enabled"):
        build_control_payload(DOSING_PUMP, {"switch": True})
