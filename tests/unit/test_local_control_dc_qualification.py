"""DC product wiring over existing journal/transport tests, not firmware qualification."""

import json

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.dc_qualification import DcScalarQualificationProducer
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, LocalDeviceWriteError
from jebao_flow.local_control.ha import project_home_assistant
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.models import FreshInstallManifest, InstallContentBindings
from jebao_flow.local_control.qualification import qualified_physical_identity_keys
from jebao_flow.local_control.qualification_producer import (
    QualificationProductionError,
    _manual_flow_state,
)
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.protocol.profiles import DC_PUMP_PRO, LOCAL_WAVEMAKER
from tests.unit.test_local_control_bar_qualification import (
    _binding,
    _loaded_binding,
    _producer,
    _Session,
)
from tests.unit.test_local_control_qualification_producer import _dc_status


def _status(*, mode=0, frequency=20, feed_time=10, **kwargs):
    raw = bytearray(_dc_status(mode_code=mode, **kwargs))
    raw[3] = frequency
    raw[4] = feed_time
    return bytes(raw)


def test_dc_state_does_not_fabricate_or_probe_linkage():
    target = _binding(product_key=DC_PUMP_PRO.product_key)
    writer = ConnectedDeviceWriter(ResolvedEndpoint(target, "not-connected"))
    state = _manual_flow_state(writer, target, _status())
    assert state.linkage is None
    assert state.mode == "constant"
    assert state.flow == 40


@pytest.mark.parametrize(
    "function,step",
    [
        ("manual_frequency", _status(frequency=5)),
        ("manual_mode", _status(mode=1)),
        ("feeding_duration", _status(feed_time=1)),
    ],
)
async def test_dc_scalar_restores_own_field_and_replay_is_offline(tmp_path, function, step):
    target = _binding(product_key=DC_PUMP_PRO.product_key)
    baseline = _status()
    sent = []
    producer, journal, _lease, resolver, _factory = _producer(
        tmp_path,
        function=function,
        target=target,
        producer_type=DcScalarQualificationProducer,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline, step], sent),
            _Session([step, baseline], sent),
        ],
    )
    result = await producer.execute("dc_scalar_0001")
    assert result.phase is TransactionPhase.VERIFIED
    assert result.physical_write_count == 3 and result.receipt_count == 2
    assert len(sent) == 3 and sent[0] == sent[2] != sent[1]
    record = journal.load("dc_scalar_0001")
    intent = json.loads(journal.load_blob(record.intent_sha256))
    assert intent["baseline_linkage"] is None
    binding = _loaded_binding(tmp_path)
    assert binding.function == function and binding.product_key == target.product_key
    content = InstallContentBindings(
        app_catalog_sha256=binding.app_catalog_sha256,
        protocol_schema_sha256=binding.schema_sha256,
        template_sha256=(binding.template_sha256,),
    )
    catalog = build_local_control_catalog(
        (binding,),
        current_content=content,
        _qualification_verifier_for_test=lambda item: qualified_physical_identity_keys(
            item,
            _receipt_root_for_test=tmp_path / "receipts",
        ),
    )
    other = target.model_copy(
        update={"logical_member": "other_dc", "physical_identity_key": "b" * 64}
    )
    manifest = FreshInstallManifest(
        generation="0" * 32,
        rederived_at=record.updated_at,
        devices=(target, other),
        content=content,
    )
    projection = project_home_assistant(
        manifest,
        catalog,
        discovery_prefix="homeassistant",
        topic_prefix="jebao-flow/test",
    )
    controls = [entity for entity in projection.entities if entity.control_available]
    assert [(entity.logical_member, entity.function) for entity in controls] == [
        (target.logical_member, function)
    ]
    replay = await producer.execute("dc_scalar_0001")
    assert replay.physical_write_count == 0
    assert len(sent) == 3 and resolver.calls == 3


@pytest.mark.parametrize(
    "status",
    [
        _status(enabled=False),
        _status(timer_enabled=True),
        _status(mode=1),
        _status(flow=46),
    ],
)
async def test_dc_scalar_preserves_existing_first_write_envelope(tmp_path, status):
    sent = []
    producer, journal, *_ = _producer(
        tmp_path,
        function="manual_frequency",
        target=_binding(product_key=DC_PUMP_PRO.product_key),
        producer_type=DcScalarQualificationProducer,
        sessions=[_Session([status], sent)],
    )
    with pytest.raises(QualificationProductionError, match="first-write envelope"):
        await producer.execute("dc_rejected_0001")
    assert not sent and journal.load("dc_rejected_0001") is None


async def test_dc_scalar_failure_never_issues_receipts_or_retries(tmp_path):
    baseline = _status()
    sent = []
    producer, journal, *_ = _producer(
        tmp_path,
        function="manual_mode",
        target=_binding(product_key=DC_PUMP_PRO.product_key),
        producer_type=DcScalarQualificationProducer,
        sessions=[
            _Session([baseline, baseline], sent),
            _Session([baseline], sent, send_error=LocalDeviceWriteError("transport fault")),
        ],
    )
    with pytest.raises(LocalDeviceWriteError):
        await producer.execute("dc_failed_0001")
    assert journal.load("dc_failed_0001").phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(sent) == 2 and not tuple((tmp_path / "receipts").iterdir())
    with pytest.raises(QualificationProductionError, match="unfinished"):
        await producer.execute("dc_failed_0001")
    assert len(sent) == 2


async def test_dc_producer_does_not_accept_bar_by_family_similarity(tmp_path):
    producer, *_ = _producer(
        tmp_path,
        function="manual_mode",
        target=_binding(product_key=LOCAL_WAVEMAKER.product_key),
        producer_type=DcScalarQualificationProducer,
        sessions=[],
    )
    with pytest.raises(QualificationProductionError, match="encoder"):
        await producer.execute("dc_wrong_0001")


async def test_dc_feeding_duration_zero_baseline_is_not_fabricated_or_sent(tmp_path):
    sent = []
    producer, journal, *_ = _producer(
        tmp_path,
        function="feeding_duration",
        target=_binding(product_key=DC_PUMP_PRO.product_key),
        producer_type=DcScalarQualificationProducer,
        sessions=[_Session([_status(feed_time=0)], sent)],
    )
    with pytest.raises(ValueError):
        await producer.execute("dc_feed_zero_0001")
    assert not sent and journal.load("dc_feed_zero_0001") is None
