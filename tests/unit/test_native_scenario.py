"""입력·컴파일 계약 시험. 가상 펌웨어 실행이나 기능 성공을 만들지 않는다."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from jebao_flow.devices.native_scenario import NativeScenario, ScenarioStep, load_scenario_suite
from jebao_flow.devices.schedule_flow_experiment import ScheduleFlowExperimentSpec
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES
from jebao_flow.protocol.schedule_wire import decode_local_wavemaker_pro_slot_wire
from jebao_flow.schedule_flow_experiment_cli import _fixed_spec


def scenario_data():
    return json.loads(Path("docs/native-scenarios/sync-async.json").read_text())["cases"][0]


def test_example_compiles_both_roles_without_code_or_image_changes():
    suite = load_scenario_suite(Path("docs/native-scenarios/sync-async.json"))
    assert tuple(case.slave_role for case in suite.cases) == ("sync_slave", "async_slave")
    anchor = datetime(2026, 9, 15, 10)
    for case in suite.cases:
        bound = case.bind(anchor)
        spec = _fixed_spec(
            operation_id="scenario",
            qualification_operation_id="qualification",
            master_device_id="master",
            slave_device_id="slave",
            boundary_time=bound.boundary_time,
            scenario=bound,
        )
        assert spec.outer_linkage_spec().slave_role.value == case.slave_role
        assert spec.outer_linkage_spec().master_power == 30
        assert spec.outer_linkage_spec().slave_power == 31
        assert spec.role_observation_spec().scenario == bound
        temporary = spec.temporary_schedule_spec()
        assert temporary.scenario == bound
        for patch, role in zip(temporary.device_patches, ("master", "slave"), strict=True):
            assert b"".join(slot.wire_bytes for slot in patch.slots) == bound.image(role)
            assert len(bound.image(role)) == 432
        assert ScheduleFlowExperimentSpec.model_validate_json(spec.model_dump_json()) == spec


def test_master_template_changes_only_flow_bytes():
    data = scenario_data()
    data["master"].append({"at": 900, "mode": "sine", "flow": 38, "frequency": 30})
    del data["slave"]
    data["slave_flows"] = [30, 40, 35]
    bound = NativeScenario.model_validate(data).bind(datetime(2026, 9, 15, 10))
    master, slave = bound.image("master"), bound.image("slave")
    assert [
        index
        for index, (left, right) in enumerate(zip(master, slave, strict=True))
        if left != right
    ] == [5, 14, 23]
    assert bound.scenario.slave_steps[2].mode == "sine"


@pytest.mark.parametrize("mode", LOCAL_WAVEMAKER_PRO_MODES)
def test_existing_codec_modes_and_options_round_trip(mode):
    step = ScenarioStep(
        at=0,
        mode=mode,
        flow=30,
        frequency=27,
        feed_time=10 if mode == "feed" else 0,
        custom_frequency=41 if mode == "custom" else 0,
    )
    from jebao_flow.protocol.schedule_wire import encode_local_wavemaker_pro_schedule_entry

    entry = step.entry(slot=0, start="00:00", end="12:00")
    assert (
        decode_local_wavemaker_pro_slot_wire(encode_local_wavemaker_pro_schedule_entry(entry))
        == entry
    )


@pytest.mark.parametrize(
    "change",
    [
        {"flow": True},
        {"flow": 42},
        {"flow": 29},
        {"frequency": 101},
        {"at": 601},
        {"mode": "shell"},
        {"exec": "command"},
        {"mode": "feed", "feed_time": 0},
    ],
)
def test_rejects_invalid_slots(change):
    with pytest.raises(ValueError):
        ScenarioStep.model_validate({"at": 0, "mode": "sine", "flow": 35, **change})


def test_rejects_missing_slave_and_mutation_of_exact_image():
    data = scenario_data()
    del data["slave"]
    with pytest.raises(ValueError):
        NativeScenario.model_validate(data)
    spec = NativeScenario.model_validate(scenario_data()).bind(datetime(2026, 9, 15, 10))
    flow = _fixed_spec(
        operation_id="one",
        qualification_operation_id="q",
        master_device_id="a",
        slave_device_id="b",
        boundary_time=spec.boundary_time,
        scenario=spec,
    )
    temporary = flow.temporary_schedule_spec()
    payload = temporary.model_dump(mode="json")
    wire = bytearray.fromhex(payload["device_patches"][0]["slots"][0]["wire_hex"])
    wire[5] = 36
    payload["device_patches"][0]["slots"][0]["wire_hex"] = wire.hex()
    with pytest.raises(ValueError):
        type(temporary).model_validate(payload)


def test_input_digest_includes_expectations_and_time_and_role():
    original = NativeScenario.model_validate(scenario_data())
    data = scenario_data()
    data["windows"][1]["slave"]["auto_flow"] = 39
    changed = NativeScenario.model_validate(data)
    assert changed.digest != original.digest
    assert (
        original.bind(datetime(2026, 9, 15, 10)).digest
        != original.bind(datetime(2026, 9, 15, 11)).digest
    )
    data = scenario_data()
    data["slave_role"] = "async_slave"
    assert NativeScenario.model_validate(data).digest != original.digest


def test_legacy_serialization_omits_scenario():
    spec = _fixed_spec(
        operation_id="legacy",
        qualification_operation_id="q",
        master_device_id="a",
        slave_device_id="b",
        boundary_time="12:00",
    )
    assert "scenario" not in spec.model_dump()
    assert "scenario" not in spec.temporary_schedule_spec().model_dump()
    assert "scenario" not in spec.role_observation_spec().model_dump()


def test_rejects_midnight_and_duplicate_json_keys(tmp_path):
    case = NativeScenario.model_validate(scenario_data())
    with pytest.raises(ValueError):
        case.bind(datetime(2026, 9, 15, 23, 50))
    path = tmp_path / "duplicate.json"
    path.write_text('{"version":1,"version":1,"cases":[]}')
    with pytest.raises(ValueError, match="중복"):
        load_scenario_suite(path)
