"""Byte/layout and durable software tests, not a fake Aquarium firmware acceptance claim."""

import json

import pytest

from jebao_flow.local_control.aquarium_qualification import AquariumScalarQualificationProducer
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import LocalDeviceWriteError
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.models import FreshInstallManifest, InstallContentBindings
from jebao_flow.local_control.qualification import qualified_physical_identity_keys
from jebao_flow.local_control.qualification_producer import QualificationProductionError
from jebao_flow.protocol.profiles import AQUARIUM_PUMP
from tests.unit.test_local_control_aquarium_power import _aquarium_status
from tests.unit.test_local_control_bar_qualification import (
    _binding,
    _loaded_binding,
    _producer,
    _Session,
    _WriterFactory,
)


def _status(*, speed=40, feed_time=10, **changes):
    raw = bytearray(_aquarium_status(motor_speed=speed, **changes))
    raw[2] = feed_time
    return bytes(raw)


@pytest.mark.parametrize(
    "function,step",
    [
        ("motor_speed", _status(speed=39)),
        ("feeding_duration", _status(feed_time=1)),
    ],
)
async def test_aquarium_scalar_restores_own_baseline_and_exposes_only_qualified_device(
    tmp_path,
    function,
    step,
):
    target = _binding(product_key=AQUARIUM_PUMP.product_key)
    baseline, sent = _status(), []
    producer, journal, _lease, resolver, _factory = _producer(
        tmp_path,
        function=function,
        target=target,
        producer_type=AquariumScalarQualificationProducer,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
            _Session([step, baseline], sent),
        ],
    )
    result = await producer.execute("aquarium_scalar_ok")
    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3 and result.receipt_count == 2
    assert sent[0] == sent[2] != sent[1]
    record = journal.load("aquarium_scalar_ok")
    intent = json.loads(journal.load_blob(record.intent_sha256))
    assert not {"baseline_flow", "baseline_linkage", "baseline_mode"} & intent.keys()
    assert intent["baseline_motor_speed"] == 40
    binding = _loaded_binding(tmp_path)
    content = InstallContentBindings(
        app_catalog_sha256=binding.app_catalog_sha256,
        protocol_schema_sha256=binding.schema_sha256,
        template_sha256=(binding.template_sha256,),
    )
    catalog = build_local_control_catalog(
        (binding,),
        current_content=content,
        _qualification_verifier_for_test=lambda item: qualified_physical_identity_keys(
            item, _receipt_root_for_test=tmp_path / "receipts"
        ),
    )
    other = target.model_copy(update={"logical_member": "other", "physical_identity_key": "b" * 64})
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=record.updated_at,
        devices=(target, other),
        content=content,
    )
    projection = project_home_assistant(
        manifest, catalog, discovery_prefix="homeassistant", topic_prefix="jebao-flow/test"
    )
    controls = [entity for entity in projection.entities if entity.control_available]
    assert [(entity.logical_member, entity.function) for entity in controls] == [
        (target.logical_member, function)
    ]
    replay = await producer.execute("aquarium_scalar_ok")
    assert replay.physical_write_count == 0 and resolver.calls == 3 and len(sent) == 3
    changed = content.model_copy(update={"protocol_schema_sha256": "f" * 64})
    stale = build_local_control_catalog((binding,), current_content=changed)
    assert not stale.find(target.product_key, function).control_exposed


@pytest.mark.parametrize(
    "status,function",
    [
        (_status(speed=29), "motor_speed"),
        (_status(speed=46), "motor_speed"),
        (_status(enabled=False), "motor_speed"),
        (_status(timer_enabled=True), "motor_speed"),
        (_status(feed_time=0), "feeding_duration"),
    ],
)
async def test_aquarium_scalar_invalid_baseline_has_no_journal_or_write(tmp_path, status, function):
    sent = []
    producer, journal, *_ = _producer(
        tmp_path,
        function=function,
        target=_binding(product_key=AQUARIUM_PUMP.product_key),
        producer_type=AquariumScalarQualificationProducer,
        sessions=[_Session([status], sent)],
    )
    with pytest.raises((ValueError, QualificationProductionError)):
        await producer.execute("aquarium_scalar_bad")
    assert not sent and journal.load("aquarium_scalar_bad") is None


async def test_aquarium_speed_fault_preserves_recovery_and_recovers_only_own_baseline(tmp_path):
    baseline, step, sent = _status(), _status(speed=39), []
    target = _binding(product_key=AQUARIUM_PUMP.product_key)
    producer, journal, lease, resolver, _factory = _producer(
        tmp_path,
        function="motor_speed",
        target=target,
        producer_type=AquariumScalarQualificationProducer,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline], sent, send_error=LocalDeviceWriteError("uncertain")),
        ],
    )
    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("aquarium_scalar_fault")
    assert journal.load("aquarium_scalar_fault").phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 2 and not tuple((tmp_path / "receipts").iterdir())
    recovery_sent = []
    coordinator = LocalControlCoordinator(
        catalog=build_local_control_catalog(),
        resolver=resolver,
        journal=journal,
        lease=lease,
        writer_factory=_WriterFactory(
            [_Session([step, baseline], recovery_sent), _Session([baseline], recovery_sent)]
        ),
    )
    result = await coordinator.recover("aquarium_scalar_fault")
    assert result.phase is TransactionPhase.ROLLED_BACK
    assert recovery_sent == [sent[0]] and sent[1] not in recovery_sent
    assert not tuple((tmp_path / "receipts").iterdir())
