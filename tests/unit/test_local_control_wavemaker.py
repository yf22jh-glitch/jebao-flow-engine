import hashlib
import json
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.config import RuntimeMode
from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.device_writer import (
    ConnectedDeviceWriter,
    LocalDeviceWriteError,
    verify_prepared_readback,
)
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan, WavemakerSchedule
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES, decode_schedule
from jebao_flow.protocol.schedule_wire import encode_local_wavemaker_pro_schedule_entry
from tests.unit.test_local_control_device_writer import _binding, _Session, _status


def _slot(**changes):
    return {
        "slot_index": 0,
        "start": "00:00",
        "end": "23:59",
        "mode": "sine",
        "flow": 35,
        "frequency": 30,
        **changes,
    }


@pytest.mark.parametrize("mode", LOCAL_WAVEMAKER_PRO_MODES)
def test_all_pro_modes_round_trip_all_declared_options(mode):
    slot = _slot(mode=mode, flow=0 if mode == "feed" else 35, feed_time=10, custom_frequency=25)
    plan = WavemakerSchedule(family="pro", slots=[slot])
    image = plan.compile_image()
    decoded = decode_schedule(plan.product_key, _status(schedule=image), enabled=False)
    assert len(image) == 432
    assert image[9:] == b"\xee" * 423
    assert decoded.entries[0].mode == mode
    assert decoded.entries[0].parameters == {
        "flow": slot["flow"],
        "frequency": 30,
        "feed_time": 10,
        "custom_frequency": 25,
    }
    assert image[:9] == encode_local_wavemaker_pro_schedule_entry(decoded.entries[0])


@pytest.mark.parametrize("mode", ("stopped", "classic", "sine", "random", "constant", "feed"))
def test_bar_geometry_and_shared_flow_feed_byte(mode):
    slot = _slot(
        mode=mode,
        flow=None if mode == "feed" else 0 if mode == "stopped" else 35,
        feed_time=10 if mode == "feed" else 0,
        pulse_tide=True,
    )
    plan = WavemakerSchedule(family="bar", slots=[slot])
    image = plan.compile_image()
    raw = bytearray(401)
    raw[8:392] = image
    decoded = decode_schedule(plan.product_key, bytes(raw), enabled=False)
    assert len(image) == 384
    assert image[8:] == b"\xee" * 376
    assert decoded.entries[0].mode == mode
    assert decoded.entries[0].parameters["pulse_tide"] is True
    assert decoded.entries[0].parameters["feed_time" if mode == "feed" else "flow"] == image[5]


def _native_plan(mode):
    schedule = WavemakerSchedule(
        family="pro",
        slots=[
            _slot(end="10:00", custom_frequency=23),
            _slot(slot_index=17, start="10:00", mode="custom", custom_frequency=31),
            _slot(slot_index=47, start="12:00", mode="feed", feed_time=20, flow=0),
        ],
    )
    return NativeWavemakerPlan(
        mode=mode,
        master="master",
        schedule=schedule,
        slaves=[
            {
                "logical_member": "slave",
                "flows": [
                    {"slot_index": 0, "flow": 30},
                    {"slot_index": 17, "flow": 40},
                    {"slot_index": 47, "flow": 0},
                ],
            }
        ],
    )


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_legacy_native_image_inherits_every_non_flow_byte(mode):
    plan = _native_plan(mode)
    images = plan.compile_images()
    schedule = plan.schedule
    assert images["master"] == schedule.compile_image()
    assert [
        i for i, (a, b) in enumerate(zip(images["master"], images["slave"], strict=True)) if a != b
    ] == [5, 158]
    assert images["slave"][17 * 9 + 8] == 31


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_new_native_image_keeps_each_slot_flow_without_cloning_waveform(mode):
    plan = _native_plan(mode)
    images = plan.compile_flow_carrier_images()
    master, slave = images["master"], images["slave"]
    constant = LOCAL_WAVEMAKER_PRO_MODES.index("constant")
    feed = LOCAL_WAVEMAKER_PRO_MODES.index("feed")

    assert master == plan.schedule.compile_image()
    assert slave[9 : 17 * 9] == b"\xee" * (16 * 9)
    assert slave[18 * 9 : 47 * 9] == b"\xee" * (29 * 9)
    assert slave[:4] == master[:4]
    assert slave[:9] == bytes((*master[:4], constant, 30, 0, 0, 0))
    assert slave[17 * 9 : 18 * 9] == bytes((*master[17 * 9 : 17 * 9 + 4], constant, 40, 0, 0, 0))
    assert slave[47 * 9 :] == bytes((*master[47 * 9 : 47 * 9 + 4], feed, 0, 0, 20, 0))
    assert plan.preview()["hardware_write"] is False
    assert plan.preview()["slave_image_contract"] == "per_slot_flow_carrier"
    assert plan.preview()["images"]["slave"]["image_sha256"] == hashlib.sha256(slave).hexdigest()


@pytest.mark.parametrize(
    "flows", ([], [{"slot_index": 1, "flow": 40}], [{"slot_index": 0, "flow": 30}] * 2)
)
def test_native_requires_exact_active_slot_flow_set(flows):
    with pytest.raises(ValueError):
        NativeWavemakerPlan(
            mode="sync",
            master="master",
            schedule={"family": "pro", "slots": [_slot()]},
            slaves=[{"logical_member": "slave", "flows": flows}],
        )


@pytest.mark.parametrize(
    "slot",
    (
        _slot(flow=True),
        _slot(flow=29),
        _slot(flow=101),
        _slot(slot_index=True),
        _slot(start="25:00"),
        _slot(end="24:01"),
        _slot(mode="unknown"),
        _slot(mode="feed", flow=0),
        _slot(pulse_tide=True),
    ),
)
def test_invalid_pro_configuration_rejected(slot):
    with pytest.raises(ValueError):
        WavemakerSchedule(family="pro", slots=[slot])


def test_native_rejects_bar_and_slave_parameter_overrides():
    base = {
        "mode": "sync",
        "master": "master",
        "schedule": {"family": "bar", "slots": [_slot()]},
        "slaves": [{"logical_member": "slave", "flows": [{"slot_index": 0, "flow": 30}]}],
    }
    with pytest.raises(ValueError, match="Pro"):
        NativeWavemakerPlan.model_validate(base)
    base["schedule"]["family"] = "pro"
    base["slaves"][0]["mode"] = "constant"
    with pytest.raises(ValueError):
        NativeWavemakerPlan.model_validate(base)


def test_bar_active_slot_cannot_silently_compile_to_unused_sentinel():
    plan = WavemakerSchedule(
        family="bar",
        slots=[
            _slot(
                start="00:00",
                end="00:00",
                mode="stopped",
                flow=0,
                frequency=0,
            )
        ],
    )
    with pytest.raises(ValueError, match="미사용"):
        plan.compile_image()


def test_pro_schedule_prepared_with_exact_full_readback_and_timer_off_baseline():
    plan = WavemakerSchedule(family="pro", slots=[_slot(slot_index=47)])
    baseline = _status(schedule=b"\xee" * 432)
    writer = ConnectedDeviceWriter(ResolvedEndpoint(_binding(), "unused"), session_factory=_Session)
    action = writer.prepare_scalar(
        LocalControlIntent(
            request_id="schedule_0001",
            target=_binding(),
            function="schedule",
            value=plan.model_dump(mode="json"),
        ),
        baseline,
    )
    assert action.baseline_status == baseline
    assert action.baseline_readback == b"\xee" * 432
    assert action.expected_readback == plan.compile_image()
    assert verify_prepared_readback(action, _status(schedule=plan.compile_image()))
    changed = bytearray(plan.compile_image())
    changed[9] = 0
    assert not verify_prepared_readback(action, _status(schedule=bytes(changed)))
    with pytest.raises(LocalDeviceWriteError):
        writer.prepare_scalar(
            LocalControlIntent(
                request_id="schedule_0002",
                target=_binding(),
                function="schedule",
                value=plan.model_dump(mode="json"),
            ),
            _status(flags=2, schedule=b"\xee" * 432),
        )


def test_cli_plan_works_without_private_config_or_device_access(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"family": "bar", "slots": [_slot()]}))
    assert local_control_cli.main(["plan-wavemaker", "--file", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["image_bytes"] == 384
    assert report["hardware_write"] is False
    assert report["device_acceptance"] == "unverified"


def test_apply_cli_does_not_start_a_writer_without_explicit_opt_in(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"family": "pro", "slots": [_slot()]}))
    monkeypatch.setattr(
        local_control_cli, "_prepare_current", lambda args: pytest.fail("접근 금지")
    )
    assert (
        local_control_cli.main(
            [
                "apply-wavemaker-schedule",
                "--file",
                str(path),
                "--member",
                "master",
                "--request-id",
                "schedule_0001",
                "--qualifications",
                str(tmp_path / "absent"),
                "--role-attested-by",
                "test_operator",
            ]
        )
        == 2
    )


@pytest.mark.asyncio
async def test_schedule_transaction_preserves_original_and_never_replays_verified(tmp_path):
    from jebao_flow.local_control.wavemaker_restore import load_operation_baselines
    from tests.unit.test_local_control_coordinator import (
        _coordinator,
        _qualification,
        _Resolver,
        _SessionFactory,
    )
    from tests.unit.test_local_control_coordinator import (
        _Session as Session,
    )

    target = _binding()
    endpoint = ResolvedEndpoint(target, "synthetic-address", wifi_firmware_version="wifi-current")
    original = _status(schedule=b"\xee" * 432)
    plan = WavemakerSchedule(family="pro", slots=[_slot()])
    session = Session([original, _status(schedule=plan.compile_image())])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("schedule"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"synthetic-address": [session]}),
        catalog_qualified_keys=frozenset({target.physical_identity_key}),
        runtime_qualified_keys=frozenset({target.physical_identity_key}),
    )
    intent = LocalControlIntent(
        request_id="schedule_once",
        target=target,
        function="schedule",
        value=plan.model_dump(mode="json"),
    )
    result = await coordinator.execute(intent)
    record = journal.load(intent.request_id)
    assert result.accepted
    assert len(session.sent) == 1
    assert journal.load_blob(record.actions[0].baseline_status_sha256) == original
    assert (await coordinator.execute(intent)).physical_write_count == 0
    assert len(session.sent) == 1
    snapshots = load_operation_baselines(record, journal)
    assert snapshots[0].baseline_status == original
    assert journal.load(intent.request_id) == record
    with pytest.raises(LocalControlExecutionError, match="verified transaction is terminal"):
        await coordinator.recover(intent.request_id)


@pytest.mark.asyncio
async def test_schedule_mismatch_remains_recovery_required_and_restores_exact_image(tmp_path):
    from jebao_flow.local_control.journal import TransactionPhase
    from tests.unit.test_local_control_coordinator import (
        _coordinator,
        _qualification,
        _Resolver,
        _SessionFactory,
    )
    from tests.unit.test_local_control_coordinator import (
        _Session as Session,
    )

    target = _binding()
    endpoint = ResolvedEndpoint(target, "synthetic-address", wifi_firmware_version="wifi-current")
    original = _status(schedule=b"\xee" * 432)
    plan = WavemakerSchedule(family="pro", slots=[_slot()])
    desired = _status(schedule=plan.compile_image())
    first = Session([original, original])
    recovery = Session([desired, original])
    coordinator, journal = _coordinator(
        tmp_path,
        qualifications=(_qualification("schedule"),),
        resolver=_Resolver((endpoint,)),
        sessions=_SessionFactory({"synthetic-address": [first, recovery]}),
        catalog_qualified_keys=frozenset({target.physical_identity_key}),
        runtime_qualified_keys=frozenset({target.physical_identity_key}),
    )
    intent = LocalControlIntent(
        request_id="schedule_mismatch",
        target=target,
        function="schedule",
        value=plan.model_dump(mode="json"),
    )
    with pytest.raises(LocalControlExecutionError):
        await coordinator.execute(intent)
    assert journal.load(intent.request_id).phase is TransactionPhase.RECOVERY_REQUIRED
    assert len(first.sent) == 1
    result = await coordinator.recover(intent.request_id)
    assert result.phase is TransactionPhase.ROLLED_BACK
    assert len(recovery.sent) == 1
    assert (await coordinator.recover(intent.request_id)).physical_write_count == 0


@pytest.mark.parametrize(
    "mode,dry_run,allowed",
    (
        (RuntimeMode.OBSERVER, False, True),
        (RuntimeMode.CONTROL, True, True),
        (RuntimeMode.CONTROL, False, False),
    ),
)
def test_apply_cli_keeps_runtime_and_member_write_locks(
    tmp_path, monkeypatch, mode, dry_run, allowed
):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"family": "pro", "slots": [_slot()]}))
    config = SimpleNamespace(
        runtime=SimpleNamespace(mode=mode, dry_run=dry_run),
        devices=[
            SimpleNamespace(
                id=_binding().logical_member, control=SimpleNamespace(allow_hardware_writes=allowed)
            ),
        ],
    )
    monkeypatch.setattr(
        local_control_cli, "_activate_current_hardware_safety_volume_identity", lambda: None
    )
    monkeypatch.setattr(
        local_control_cli,
        "_prepare_current",
        lambda args: SimpleNamespace(
            config=config,
            manifest=SimpleNamespace(devices=[_binding()]),
        ),
    )
    monkeypatch.setattr(local_control_cli, "_coordinator", lambda *args: pytest.fail("전송 금지"))
    assert (
        local_control_cli.main(
            [
                "apply-wavemaker-schedule",
                "--file",
                str(path),
                "--member",
                _binding().logical_member,
                "--request-id",
                "schedule_0001",
                "--qualifications",
                str(tmp_path / "absent"),
                "--role-attested-by",
                "test_operator",
                "--write-enabled",
            ]
        )
        == 2
    )
