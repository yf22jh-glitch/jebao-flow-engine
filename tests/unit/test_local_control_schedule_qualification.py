"""Owned raw-image and fault-injection tests; not a firmware simulation or write acceptance."""

import json

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import (
    LocalDeviceWriteError,
    build_exact_schedule_payload,
)
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.models import InstallContentBindings
from jebao_flow.local_control.qualification import (
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)
from jebao_flow.local_control.qualification_producer import QualificationProductionError
from jebao_flow.local_control.schedule_qualification import (
    ScheduleQualificationIntent,
    ScheduleQualificationProducer,
    ScheduleResetQualificationProducer,
    parse_qualification_schedule,
)
from jebao_flow.protocol.profiles import (
    AQUARIUM_PUMP,
    DC_PUMP_PRO,
    LOCAL_WAVEMAKER,
    LOCAL_WAVEMAKER_PRO,
)
from tests.unit.test_local_control_aquarium_power import _aquarium_status
from tests.unit.test_local_control_bar_qualification import (
    _bar_status,
    _binding,
    _loaded_binding,
    _producer,
    _Session,
    _WriterFactory,
)
from tests.unit.test_local_control_qualification_producer import _dc_status
from tests.unit.test_local_control_qualification_producer import _status as _pro_status


def _plans(family):
    a = {"family": family, "slots": []}
    slot = {"slot_index": 0, "start": "06:00", "end": "12:00"}
    slot.update(
        {"mode": "auto", "speed": 35}
        if family == "aquarium"
        else {"mode": "constant", "flow": 35, "frequency": 20}
    )
    return a, {"family": family, "slots": [slot]}


def _parts(family):
    return {
        "bar": (LOCAL_WAVEMAKER, 8, 384, _bar_status()),
        "dc_pro": (DC_PUMP_PRO, 9, 384, _dc_status()),
        "aquarium": (AQUARIUM_PUMP, 13, 288, _aquarium_status()),
        "pro": (LOCAL_WAVEMAKER_PRO, 11, 432, _pro_status()),
    }[family]


def _status(family, image):
    _schema, offset, size, baseline = _parts(family)
    raw = bytearray(baseline)
    assert len(image) == size
    raw[offset : offset + size] = image
    return bytes(raw)


def _fixture(tmp_path, family, sessions, *, reset=False):
    a, b = _plans(family)
    producer_class = ScheduleResetQualificationProducer if reset else ScheduleQualificationProducer
    return _producer(
        tmp_path,
        function="schedule",
        sessions=sessions,
        target=_binding(product_key=_parts(family)[0].product_key),
        producer_type=lambda function, **kwargs: producer_class(
            schedule_a=a, schedule_b=b, **kwargs
        ),
    )


@pytest.mark.parametrize("family", ["bar", "dc_pro", "aquarium"])
async def test_schedule_a_b_restore_preserves_opaque_original_and_respects_runtime_promotion(
    tmp_path,
    family,
):
    schema, _offset, size, _raw = _parts(family)
    # Deliberately not an editable app plan: restoring it must never decode/recompile/normalize it.
    original = bytes((index * 7) % 256 for index in range(size))
    a, b = (parse_qualification_schedule(value).compile_image() for value in _plans(family))
    original_raw, a_raw, b_raw = (_status(family, value) for value in (original, a, b))
    sent = []
    producer, journal, _lease, resolver, _factory = _fixture(
        tmp_path,
        family,
        [
            _Session([original_raw, a_raw], sent),
            _Session([a_raw, b_raw], sent),
            _Session([b_raw, original_raw], sent),
        ],
    )
    result = await producer.execute("schedule_qualify_01")
    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3 and result.receipt_count == 2
    assert sent == [
        build_exact_schedule_payload(schema.product_key, image) for image in (a, b, original)
    ]
    record = journal.load("schedule_qualify_01")
    assert all(
        journal.load_blob(action.rollback_payload_sha256) == sent[2] for action in record.actions
    )
    binding = _loaded_binding(tmp_path)
    catalog = build_local_control_catalog(
        (binding,),
        current_content=InstallContentBindings(
            app_catalog_sha256=binding.app_catalog_sha256,
            protocol_schema_sha256=binding.schema_sha256,
            template_sha256=(binding.template_sha256,),
        ),
        _qualification_verifier_for_test=lambda item: qualified_physical_identity_keys(
            item, _receipt_root_for_test=tmp_path / "receipts"
        ),
    )
    descriptor = catalog.find(schema.product_key, "schedule")
    assert descriptor.stage.value == "qualified"
    # DC is implemented for the next qualified deployment; Aquarium is still preview-only.
    # These fixture receipts test projection, not physical qualification or deployment.
    assert descriptor.control_exposed is True
    replay = await producer.execute("schedule_qualify_01")
    assert replay.physical_write_count == 0 and resolver.calls == 3 and len(sent) == 3
    changed = json.loads(json.dumps(_plans(family)[1]))
    changed["slots"][0]["start"] = "07:00"
    producer._plans = (producer._plans[0], parse_qualification_schedule(changed))
    with pytest.raises(QualificationProductionError, match="intent changed"):
        await producer.execute("schedule_qualify_01")
    assert len(sent) == 3


@pytest.mark.parametrize("family", ["bar", "dc_pro", "aquarium"])
@pytest.mark.parametrize("failure_phase", [0, 1, 2])
async def test_schedule_ack_fault_has_no_receipts_no_resend_and_recovers_own_raw(
    tmp_path,
    family,
    failure_phase,
):
    schema, _offset, size, _raw = _parts(family)
    original = bytes((index * 11) % 256 for index in range(size))
    a, b = (parse_qualification_schedule(value).compile_image() for value in _plans(family))
    phases = (original, a, b, original)
    sent, sessions = [], []
    for index in range(failure_phase + 1):
        if index == failure_phase:
            sessions.append(
                _Session(
                    [_status(family, phases[index])],
                    sent,
                    send_error=LocalDeviceWriteError("uncertain send"),
                )
            )
        else:
            sessions.append(
                _Session([_status(family, phases[index]), _status(family, phases[index + 1])], sent)
            )
    producer, journal, lease, resolver, _factory = _fixture(tmp_path, family, sessions)
    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("schedule_fault_01")
    assert journal.load("schedule_fault_01").phase is TransactionPhase.RECOVERY_REQUIRED
    assert not tuple((tmp_path / "receipts").iterdir())
    with pytest.raises(QualificationProductionError, match="recovery"):
        await producer.execute("schedule_fault_01")
    assert len(sent) == failure_phase + 1
    # Recovery observes the last attempted image, without assuming ACK failure means no write.
    recovery_sent = []
    original_raw = _status(family, original)
    recovery_sessions = [
        _Session([_status(family, phases[failure_phase + 1]), original_raw], recovery_sent)
    ]
    recovery_sessions.extend(_Session([original_raw], recovery_sent) for _ in range(3))
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=resolver,
        journal=journal,
        lease=lease,
        writer_factory=_WriterFactory(recovery_sessions),
    )
    result = await coordinator.recover("schedule_fault_01")
    assert result.phase is TransactionPhase.ROLLED_BACK
    assert recovery_sent in ([], [build_exact_schedule_payload(schema.product_key, original)])
    assert not tuple((tmp_path / "receipts").iterdir())


def test_schedule_qualification_rejects_ambiguous_or_unsupported_images_before_io():
    _a, b = _plans("dc_pro")
    with pytest.raises(ValueError, match="audited app enum"):
        parse_qualification_schedule({**b, "slots": [{**b["slots"][0], "mode": "unknown"}]})
    with pytest.raises(ValueError, match="only Bar"):
        parse_qualification_schedule({"family": "pro", "slots": []})


@pytest.mark.parametrize("family", ["bar", "dc_pro", "aquarium"])
async def test_schedule_qualification_never_turns_off_a_running_timer(tmp_path, family):
    raw = bytearray(_parts(family)[3])
    if family == "bar":
        raw[1] |= 1 << 3
    else:
        raw[0] |= 1 << (1 if family == "dc_pro" else 3)
    sent = []
    producer, journal, *_ = _fixture(tmp_path, family, [_Session([bytes(raw)], sent)])
    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("schedule_timer_on")
    assert not sent and journal.load("schedule_timer_on") is None


@pytest.mark.parametrize("family", ["pro", "dc_pro"])
async def test_app_reset_qualification_issues_only_reset_receipts_after_raw_restore(
    tmp_path, family
):
    schema, _offset, size, _raw = _parts(family)
    original = bytes((index * 13) % 256 for index in range(size))
    a, b = (
        ScheduleResetQualificationProducer._parse_plan(value).compile_image()
        for value in _plans(family)
    )
    phases = (original, a, b, a, original)
    sent = []
    producer, journal, *_ = _fixture(
        tmp_path,
        family,
        [
            _Session([_status(family, phases[index]), _status(family, phases[index + 1])], sent)
            for index in range(4)
        ],
        reset=True,
    )
    result = await producer.execute("reset_qualify_01")
    assert result.receipt_count == 2 and result.physical_write_count == 4
    assert result.phase is TransactionPhase.VERIFIED
    assert sent[0] == sent[2] == build_exact_schedule_payload(schema.product_key, a)
    assert sent[3] == build_exact_schedule_payload(schema.product_key, original)
    binding = _loaded_binding(tmp_path)
    assert binding.function == "schedule_reset"
    receipts = verify_qualification_receipts(binding, _receipt_root_for_test=tmp_path / "receipts")
    intent = ScheduleQualificationIntent.model_validate_json(
        journal.load_blob(journal.load("reset_qualify_01").intent_sha256)
    )
    assert {receipt.request_id for receipt in receipts} == {
        producer._phase_intent(intent, index).request_id for index in (0, 2)
    }
    assert (await producer.execute("reset_qualify_01")).physical_write_count == 0
    assert len(sent) == 4


@pytest.mark.parametrize("failure_phase", [0, 2, 3])
async def test_reset_failure_never_publishes_receipts_or_repeats_reset(tmp_path, failure_phase):
    family = "dc_pro"
    original = bytes((index * 17) % 256 for index in range(384))
    a, b = (parse_qualification_schedule(value).compile_image() for value in _plans(family))
    phases = (original, a, b, a, original)
    sent, sessions = [], []
    for index in range(failure_phase + 1):
        sessions.append(
            _Session(
                [_status(family, phases[index])]
                if index == failure_phase
                else [_status(family, phases[index]), _status(family, phases[index + 1])],
                sent,
                send_error=LocalDeviceWriteError("uncertain") if index == failure_phase else None,
            )
        )
    producer, journal, *_ = _fixture(tmp_path, family, sessions, reset=True)
    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("reset_fault_01")
    assert journal.load("reset_fault_01").phase is TransactionPhase.RECOVERY_REQUIRED
    assert not tuple((tmp_path / "receipts").iterdir())
    with pytest.raises(QualificationProductionError, match="recovery"):
        await producer.execute("reset_fault_01")
    assert len(sent) == failure_phase + 1
