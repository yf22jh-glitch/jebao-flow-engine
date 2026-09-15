"""인위적 표본은 비교기의 수학적 계약만 시험한다. 펌웨어 기대 동작 시험이 아니다."""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.devices.native_scenario import load_scenario_suite
from jebao_flow.devices.native_scenario_evidence import (
    ScenarioEvidence,
    ScenarioPair,
    ScenarioReply,
)


def evaluator():
    case = load_scenario_suite(Path("docs/native-scenarios/sync-async.json")).cases[0]
    return ScenarioEvidence(case.bind(datetime(2026, 9, 15, 10)))


def pair(evidence, ordinal, second, window, *, change=None, slave_clock=None):
    replies = {}
    for role in ("master", "slave"):
        values = getattr(evidence.bound.scenario.windows[window], role).fields()
        if change and role == "slave":
            values.update(change)
        replies[role] = ScenarioReply(
            device_time=evidence.bound.anchor
            + timedelta(seconds=second if role == "master" or slave_clock is None else slave_clock),
            action=3 if role == "master" else 4,
            frame_sha256="0" * 64,
            image_sha256=hashlib.sha256(evidence.bound.image(role)).hexdigest(),
            role="master" if role == "master" else evidence.bound.scenario.slave_role,
            fields=values,
        )
    return ScenarioPair(ordinal=ordinal, acquired_at=second + 1000.0, **replies)


def feed_complete(evidence, *, change=None):
    ordinal = 0
    for window, seconds in ((0, range(0, 151, 15)), (1, (660, 690)), (2, (960, 990))):
        for second in seconds:
            ordinal += 1
            evidence.add(pair(evidence, ordinal, second, window, change=change))


def test_exact_expected_fields_and_full_image_are_separate():
    evidence = evaluator()
    feed_complete(evidence)
    report = evidence.report()
    assert report.input_readback == "PASS"
    assert report.response == "PASS"
    assert [row["status"] for row in report.windows] == ["PASS"] * 3
    assert report.discrepancies == ()
    assert report.pair_count == 15
    assert report.windows[0]["compared_fields"]["master"] == [
        "auto_flow",
        "auto_frequency",
        "auto_mode",
    ]
    assert "auto_custom_frequency" not in report.windows[0]["compared_fields"]["slave"]


def test_functional_contradiction_is_retained_even_after_later_match():
    evidence = evaluator()
    evidence.add(pair(evidence, 1, 0, 0, change={"auto_flow": 31}))
    for ordinal, second in enumerate(range(15, 181, 15), 2):
        evidence.add(pair(evidence, ordinal, second, 0))
    assert evidence.report().response == "FAIL"
    assert evidence.report().input_readback == "PASS"
    assert evidence.report().discrepancies[0]["expected"] == 30
    assert evidence.report().discrepancies[0]["actual"] == 31


def test_stable_negative_answer_is_observed_not_missing():
    evidence = evaluator()
    feed_complete(evidence, change={"auto_flow": 31})
    report = evidence.report()
    assert report.response == "FAIL"
    assert all(row["stable_evidence"] is not None for row in report.windows)


def test_missing_value_is_unknown_never_filled_from_plan():
    evidence = evaluator()
    feed_complete(evidence, change={"auto_flow": None})
    assert evidence.report().response == "UNKNOWN"
    assert evidence.report().discrepancies[0]["actual"] is None


def test_date_minute_repeat_exclusions_do_not_create_matches():
    evidence = evaluator()
    evidence.add(pair(evidence, 1, 60, 0, slave_clock=-86400 + 60))
    evidence.add(pair(evidence, 2, 90, 0, slave_clock=30))
    evidence.add(pair(evidence, 3, 120, 0))
    evidence.add(pair(evidence, 4, 130, 0, slave_clock=120))
    report = evidence.report()
    assert report.response == "UNKNOWN"
    assert report.exclusions == {"date_mismatch": 1, "minute_mismatch": 1, "clock_repeated": 1}


def test_duplicate_or_backward_acquisition_rejected():
    evidence = evaluator()
    value = pair(evidence, 1, 0, 0)
    evidence.add(value)
    with pytest.raises(ValueError):
        evidence.add(value)


def test_no_samples_never_pass():
    report = evaluator().report()
    assert report.input_readback == "UNKNOWN"
    assert report.response == "UNKNOWN"


def test_changed_unused_image_bytes_cannot_pass_response():
    evidence = evaluator()
    first = pair(evidence, 1, 0, 0)
    first = first.model_copy(
        update={"slave": first.slave.model_copy(update={"image_sha256": "1" * 64})}
    )
    evidence.add(first)
    for ordinal, second in enumerate(range(15, 151, 15), 2):
        evidence.add(pair(evidence, ordinal, second, 0))
    for ordinal, second, window in ((12, 660, 1), (13, 690, 1), (14, 960, 2), (15, 990, 2)):
        evidence.add(pair(evidence, ordinal, second, window))
    assert evidence.report().input_readback == "FAIL"
    assert evidence.report().response == "FAIL"


@pytest.mark.parametrize("corruption", ["empty", "short", "input", "wrong_fields", "wrong_bound"])
def test_persisted_report_cannot_claim_unbound_or_insufficient_pass(corruption):
    from jebao_flow.devices.native_scenario_evidence import ScenarioReport

    evidence = evaluator()
    feed_complete(evidence)
    report = evidence.report()
    assert report.matches_bound(evidence.bound)
    data = report.model_dump(mode="json")
    if corruption == "empty":
        data["windows"][0]["stable_evidence"] = {}
    elif corruption == "short":
        data["windows"][0]["stable_evidence"]["stable_seconds"] = 119
    elif corruption == "input":
        data["input_readback"] = "FAIL"
    elif corruption == "wrong_fields":
        data["windows"][0]["stable_evidence"]["slave"]["fields"]["auto_flow"] = 31
    else:
        data["bound_sha256"] = "1" * 64
    if corruption in {"wrong_fields", "wrong_bound"}:
        assert not ScenarioReport.model_validate(data).matches_bound(evidence.bound)
    else:
        with pytest.raises(ValueError):
            ScenarioReport.model_validate(data)


def test_acquisition_failure_does_not_count_gap_as_stability():
    evidence = evaluator()
    for ordinal, second in enumerate(range(0, 121, 15), 1):
        evidence.add(pair(evidence, ordinal, second, 0))
    evidence.acquisition_failed()
    evidence.add(pair(evidence, 10, 180, 0))
    assert evidence.report().windows[0]["stable_evidence"] is None
    assert evidence.report().exclusions["acquisition_failed"] == 1


def test_boolean_return_cannot_be_coerced_to_numeric_success():
    evidence = evaluator()
    with pytest.raises(ValueError):
        pair(evidence, 1, 0, 0, change={"auto_flow": True})


def test_terminal_classifier_uses_actual_bound_report_not_just_window_names():
    from datetime import UTC

    from jebao_flow.devices.schedule_flow_experiment import (
        ScheduleFlowOutcome,
        classify_schedule_flow_sample,
    )
    from jebao_flow.devices.schedule_linkage import ScheduleAutoEvidence, ScheduleLinkageSample
    from jebao_flow.protocol.models import LinkageRole
    from jebao_flow.schedule_flow_experiment_cli import _fixed_spec

    evidence = evaluator()
    feed_complete(evidence)
    bound = evidence.bound
    spec = _fixed_spec(
        operation_id="scenario",
        qualification_operation_id="qualified",
        master_device_id="master",
        slave_device_id="slave",
        boundary_time=bound.boundary_time,
        scenario=bound,
    )
    report = evidence.report()
    values = report.windows[-1]["stable_evidence"]
    fields = {}
    for role in ("master", "slave"):
        actual = values[role]["fields"]
        fields[role] = ScheduleAutoEvidence(
            mode=actual["auto_mode"], flow=actual["auto_flow"], frequency=actual["auto_frequency"]
        )
        fields[f"{role}_reported_mode"] = actual.get("reported_mode")
        fields[f"{role}_reported_frequency"] = actual.get("reported_frequency")
    sample = ScheduleLinkageSample(
        observed_at=datetime.now(UTC),
        phase="after",
        master_manual_power=30,
        slave_manual_power=31,
        master_linkage=LinkageRole.MASTER,
        slave_linkage=LinkageRole.SYNC_SLAVE,
        scenario_report=report,
        **fields,
    )
    assert (
        classify_schedule_flow_sample(spec, sample)
        is ScheduleFlowOutcome.SCENARIO_RESPONSES_OBSERVED
    )
    changed = sample.model_copy(update={"slave": sample.slave.model_copy(update={"flow": 31})})
    assert (
        classify_schedule_flow_sample(spec, changed)
        is ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE
    )
