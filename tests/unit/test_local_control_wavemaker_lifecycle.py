"""journal 순서·단일 전송 계약 시험. 메모리 datapoint 복사는 펌웨어 가설이 아니다.

수신 값을 그대로 저장하는 transport fixture는 오직 코드의 요청·보존·판정 경로를 검사한다.
실제 Sync 전파, report 지연, 임펠러 응답은 모델링하지 않으며 PASS로 주장하지 않는다.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.config import RuntimeMode
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlExecutionError
from jebao_flow.local_control.install import current_protocol_schema_sha256
from jebao_flow.local_control.journal import (
    ActionPhase,
    LocalControlJournalStore,
    TransactionPhase,
    native_detach_request_id,
)
from jebao_flow.local_control.models import InstallContentBindings
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan
from jebao_flow.local_control.wavemaker_lifecycle import NativeWavemakerController
from jebao_flow.protocol.control import _place_value
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO as SCHEMA
from jebao_flow.protocol.session import RawStateCapture
from tests.unit.test_local_control_coordinator import _binding, _lease, _qualification, _Resolver

# Staged layout, both versions: 0..1 safe controls, 2..3 images, 4..5 timers,
# 6 master role, 7 sync_slave role, 8 async_slave role.
STAGED_ASYNC_ROLE = 8

FUNCTIONS = (
    "power",
    "schedule_enable",
    "linkage_role",
    "manual_mode",
    "manual_flow",
    "manual_frequency",
    "feeding_duration",
    "custom_wave_frequency",
    "schedule",
)


def _plan(mode="sync"):
    return NativeWavemakerPlan.model_validate(
        {
            "mode": mode,
            "master": "master",
            "preparation_flow": 32,
            "preparation_frequency": 21,
            "schedule": {
                "family": "pro",
                "slots": [
                    {
                        "slot_index": 0,
                        "start": "00:00",
                        "end": "12:00",
                        "mode": "sine",
                        "flow": 35,
                        "frequency": 30,
                        "custom_frequency": 20,
                    },
                    {
                        "slot_index": 7,
                        "start": "12:00",
                        "end": "23:59",
                        "mode": "constant",
                        "flow": 40,
                    },
                ],
            },
            "slaves": [
                {
                    "logical_member": "slave",
                    "flows": [
                        {"slot_index": 0, "flow": 40},
                        {"slot_index": 7, "flow": 35},
                    ],
                }
            ],
        }
    )


def _put(raw, changes):
    out = bytearray(raw)
    for name, value in changes.items():
        _place_value(SCHEMA, out, SCHEMA.by_name(name), value)
    return bytes(out)


def _initial(timer=False, flow=40):
    raw = bytearray(SCHEMA.raw_status_size)
    # 비활성 슬롯까지 임의의 원본 bytes. 복원에서 정규화하면 잡힌다.
    raw[11:443] = bytes(range(216)) * 2
    return _put(
        bytes(raw),
        {
            "SwitchON": False,
            "TimerON": timer,
            "Linkage": "independent",
            "Mode": "sine",
            "Flow": flow,
            "Frequency": 34,
            "FeedTime": 12,
            "Cust_Wav_Freq": 27,
        },
    )


class MemoryTransport:
    def __init__(self, address, rig):
        self.address, self.rig = address, rig
        rig.sessions.append(self)
        self.closed = 0

    async def connect(self):
        self.rig.events.append(("connect", self, self.rig.virtual_time))

    async def authenticate(self):
        self.rig.events.append(("authenticate", self, self.rig.virtual_time))
        return b"synthetic-auth"

    async def disconnect(self):
        self.closed += 1
        self.rig.events.append(("close", self, self.rig.virtual_time))

    def quarantine(self):
        self.rig.events.append(("quarantine", self, self.rig.virtual_time))

    async def read_raw_state(self, *, accept_reports=True):
        # Same delegation as protocol.session.GizwitsSession.read_raw_state.
        capture = await self.read_raw_state_capture(accept_reports=accept_reports)
        return capture.status_payload

    async def read_raw_state_capture(self, *, accept_reports, state_frame_observer=None):
        rig = self.rig
        rig.events.append(("read", self, rig.virtual_time))
        rig.reads.append((self.address, accept_reports))
        if (
            rig.reject_final_reply
            and not accept_reports
            and rig.sent
            and rig.states == rig.originals
        ):
            raise TimeoutError("synthetic reply unavailable")
        status = rig.states[self.address]
        if rig.read_transform is not None:
            status = rig.read_transform(self.address, status, accept_reports)
        action = 4 if accept_reports else 3
        capture = RawStateCapture(b"synthetic-wire" + bytes([action]) + status, action, status)
        if state_frame_observer is not None:
            state_frame_observer(capture, True)
        return capture

    async def send_raw_control(self, payload):
        rig = self.rig
        rig.events.append(("send", self, rig.virtual_time))
        # 전송 직전에 durable attempting과 모든 blob이 존재해야 한다.
        matches = [
            (r, a)
            for r in rig.store.unfinished_records()
            for a in r.actions
            if a.phase is ActionPhase.FORWARD_ATTEMPTING
            and rig.store.load_blob(a.forward_payload_sha256) == payload
        ]
        assert len(matches) == 1
        record, action = matches[0]
        # Native plans and their children must have durable context and a BEFORE evidence row
        # before any send. Ordinary scalar commands on the same rig carry neither.
        if action.function in {"native_controls", "native_image"}:
            assert record.native_context_sha256 is not None
        if record.native_context_sha256 is not None:
            assert action.native_evidence_sha256 is not None
        rig.store._require_referenced_blobs(record)
        flags = payload[1 : 1 + SCHEMA.attribute_flags_size][::-1]
        values = payload[1 + SCHEMA.attribute_flags_size :]
        padded = values + bytes(SCHEMA.raw_status_size - len(values))
        decoded = SCHEMA.decode_status(padded)
        changes = {
            a.name: decoded[a.name]
            for a in SCHEMA.attributes
            if a.writable and flags[a.id // 8] & (1 << (a.id % 8))
        }
        rig.sent.append((self.address, changes, record.request_id, action.action_id, payload))
        number = len(rig.sent)
        if rig.before_send is not None:
            rig.before_send(number, self.address, changes)
        if number != rig.reject_send:
            updated = bytearray(rig.states[self.address])
            # 선택된 raw 필드만 복사한다. 원본 inactive zero도 입력값으로 재인코딩하지 않는다.
            for name in changes:
                position = SCHEMA.by_name(name).position
                if position.unit.value == "byte":
                    start = position.byte_offset
                    updated[start : start + position.length] = padded[
                        start : start + position.length
                    ]
                else:
                    _place_value(SCHEMA, updated, SCHEMA.by_name(name), changes[name])
            rig.states[self.address] = bytes(updated)
        if number == rig.fail_ack:
            raise TimeoutError("synthetic ACK loss after application")
        if number == rig.cancel_ack:
            raise asyncio.CancelledError()
        return b"synthetic-ack"


class Rig:
    def __init__(self, tmp_path, *, qualified=True):
        self.targets = (_binding("master", "a"), _binding("slave", "b"))
        self.endpoints = tuple(
            ResolvedEndpoint(t, t.logical_member, wifi_firmware_version="wifi-current")
            for t in self.targets
        )
        self.states = {"master": _initial(True, 45), "slave": _initial(False, 40)}
        self.originals = dict(self.states)
        self.store = LocalControlJournalStore.for_test(tmp_path / "journal")
        self.lease = _lease(tmp_path)
        self.resolver = _Resolver(self.endpoints)
        self.keys = frozenset(t.physical_identity_key for t in self.targets)
        self.catalog = build_local_control_catalog(
            tuple(_qualification(f) for f in FUNCTIONS) if qualified else (),
            current_content=InstallContentBindings(
                app_catalog_sha256="0" * 64,
                protocol_schema_sha256=current_protocol_schema_sha256(),
                template_sha256=("1" * 64,),
            ),
            _qualification_verifier_for_test=lambda _: self.keys,
        )
        self.sent, self.reads = [], []
        self.fail_ack = self.cancel_ack = self.reject_send = None
        self.reject_final_reply = False
        self.before_send = None
        self.read_transform = None
        self.virtual_time = 0.0
        self.sleeps = []
        self.events, self.sessions = [], []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.virtual_time += seconds

    def controller(self):
        return NativeWavemakerController(
            catalog=self.catalog,
            resolver=self.resolver,
            journal=self.store,
            lease=self.lease,
            session_factory=lambda address: MemoryTransport(address, self),
            qualification_verifier=lambda _: self.keys,
            sequence_sleep=self.sleep,
            sequence_clock=lambda: self.virtual_time,
        )

    async def attach(self, mode="sync", request_id="attach_0001"):
        return await self.controller().attach(
            request_id=request_id, plan=_plan(mode), targets=self.targets
        )

    async def detach(self, targets=None):
        return await self.controller().detach(
            attachment_id="attach_0001", targets=targets or self.targets
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_both_roles_wire_journal_original_restore_and_cached_requests(tmp_path, mode):
    rig = Rig(tmp_path)
    result = await rig.attach(mode)
    assert result["accepted"] and result["current_state_verified"]
    assert all(d["complete"] for d in result["verification"]["devices"])
    assert SCHEMA.decode_status(rig.states["slave"])["Linkage"] == mode + "_slave"
    expected_images = _plan(mode).compile_flow_carrier_images()
    assert rig.states["master"][11:443] == expected_images["master"]
    assert rig.states["slave"][11:443] == expected_images["slave"]
    assert [s[0] for s in rig.sent[:2]] == ["master", "slave"]
    assert all(s[1]["TimerON"] is False and s[1]["Linkage"] == "independent" for s in rig.sent[:2])
    assert rig.sent[0][1]["Flow"] == 32 and rig.sent[0][1]["Frequency"] == 21
    if mode == "async":
        roles = [s[1]["Linkage"] for s in rig.sent if s[0] == "slave" and "Linkage" in s[1]]
        assert roles == ["independent", "independent", "sync_slave", "async_slave"]
    count = len(rig.sent)
    cached = await rig.attach(mode)
    assert cached["accepted"] and not cached["current_state_verified"]
    assert len(rig.sent) == count
    source = rig.store.load("attach_0001")
    later_targets = tuple(
        t.model_copy(update={"role_attested_at": t.role_attested_at + timedelta(hours=1)})
        for t in rig.targets
    )
    restored = await rig.detach(later_targets)
    assert restored["accepted"] and restored["current_state_verified"]
    assert rig.states == rig.originals
    assert all(
        d["schedule_match"] and len(d["fields"]) == 8 for d in restored["verification"]["devices"]
    )
    assert rig.reads[-2:] == [("master", False), ("slave", False)]
    assert rig.store.load("attach_0001") == source
    count = len(rig.sent)
    assert not (await rig.detach())["current_state_verified"]
    assert len(rig.sent) == count
    assert rig.store.unfinished_records() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", range(1, 10))
async def test_attach_ack_loss_at_each_async_write_restores_without_replaying_attach(
    tmp_path, failure
):
    rig = Rig(tmp_path)
    rig.fail_ack = failure
    if failure == 9:
        # The last role alone may be confirmed by one explicit reply, without an ACK.
        result = await rig.attach("async")
        assert result["accepted"]
        assert result["actions"][-1]["delivery"] == "UNKNOWN"
        assert result["actions"][-1]["acknowledgement_received"] is False
        await rig.detach()
    else:
        with pytest.raises(LocalControlExecutionError, match="자기 원본 복원 확인"):
            await rig.attach("async")
    assert rig.states == rig.originals
    assert rig.store.load("attach_0001").phase is (
        TransactionPhase.VERIFIED if failure == 9 else TransactionPhase.ROLLED_BACK
    )
    assert (
        rig.store.load(native_detach_request_id("attach_0001")).phase is TransactionPhase.VERIFIED
    )
    keys = [(s[2], s[3]) for s in rig.sent]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_async_uses_observed_slave_connection_without_second_before_read(
    tmp_path, monkeypatch
):
    rig = Rig(tmp_path)
    events = []
    original_read = MemoryTransport.read_raw_state_capture
    original_send = MemoryTransport.send_raw_control

    async def read(self, *, accept_reports, **kwargs):
        events.append(("read", self, self.address))
        return await original_read(self, accept_reports=accept_reports, **kwargs)

    async def send(self, payload):
        events.append(("send", self, self.address))
        return await original_send(self, payload)

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    monkeypatch.setattr(MemoryTransport, "send_raw_control", send)
    await rig.attach("async")
    last_send = max(i for i, event in enumerate(events) if event[0] == "send")
    connection = events[last_send][1]
    prior = [e for e in events[:last_send] if e[1] == connection]
    assert prior == [("read", connection, "slave")]
    assert len(rig.sent) == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["mismatch", "timeout", "report", "cancel"])
async def test_async_ack_error_never_accepts_missing_mismatched_or_report_only_reply(
    tmp_path, monkeypatch, failure
):
    rig = Rig(tmp_path)
    rig.fail_ack = 9
    original_read = MemoryTransport.read_raw_state_capture
    reads = []

    async def read(self, *, accept_reports, **kwargs):
        if len(rig.sent) == 9 and self.address == "slave" and not accept_reports:
            reads.append(accept_reports)
            if failure == "timeout":
                raise TimeoutError("synthetic reply loss")
            if failure == "cancel":
                raise asyncio.CancelledError()
            raw = await original_read(self, accept_reports=accept_reports, **kwargs)
            status = (
                _put(raw.status_payload, {"Linkage": "sync_slave"})
                if failure == "mismatch"
                else raw.status_payload
            )
            return RawStateCapture(raw.wire_frame, 4 if failure == "report" else 3, status)
        return await original_read(self, accept_reports=accept_reports, **kwargs)

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    with pytest.raises(
        asyncio.CancelledError if failure == "cancel" else LocalControlExecutionError
    ):
        await rig.attach("async")
    assert reads == [False]
    assert rig.states == rig.originals
    source = rig.store.load("attach_0001")
    assert source.actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    assert (
        source.actions[STAGED_ASYNC_ROLE].diagnostic
        == "native_own_baseline_restored_delivery_unknown"
    )
    assert sum(s[2] == "attach_0001" and s[1] == {"Linkage": "async_slave"} for s in rig.sent) == 1


@pytest.mark.asyncio
async def test_reply_confirmed_async_final_failure_can_use_owned_assistance(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.fail_ack = 9
    original_read = MemoryTransport.read_raw_state_capture
    original_send = MemoryTransport.send_raw_control

    async def read(self, *, accept_reports, **kwargs):
        raw = await original_read(self, accept_reports=accept_reports, **kwargs)
        # Final attach comparison fails after the reply-confirmed role. No firmware model.
        if len(rig.sent) == 9 and self.address == "master":
            return RawStateCapture(
                raw.wire_frame,
                raw.action,
                _put(raw.status_payload, {"Flow": 40, "Linkage": "independent"}),
            )
        return raw

    async def send(self, payload):
        if len(rig.sent) == 9:
            rig.fail_ack = rig.reject_send = 10
        return await original_send(self, payload)

    monkeypatch.setattr(MemoryTransport, "read_raw_state_capture", read)
    monkeypatch.setattr(MemoryTransport, "send_raw_control", send)
    with pytest.raises(LocalControlExecutionError, match="원복 미확인"):
        await rig.attach("async")
    source = rig.store.load("attach_0001")
    assert source.phase is TransactionPhase.RECOVERY_REQUIRED
    assert source.actions[STAGED_ASYNC_ROLE].phase is ActionPhase.VERIFIED
    assert source.actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    monkeypatch.setattr(MemoryTransport, "send_raw_control", original_send)
    rig.fail_ack = rig.reject_send = None
    result = await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert result["accepted"] and rig.states == rig.originals
    assert (
        rig.store.load("attach_0001").actions[STAGED_ASYNC_ROLE].diagnostic
        == "native_own_baseline_restored_delivery_unknown"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", range(1, 7))
async def test_detach_ack_loss_restart_verifies_attempt_without_duplicate_send(tmp_path, failure):
    rig = Rig(tmp_path)
    await rig.attach()
    start = len(rig.sent)
    rig.fail_ack = start + failure
    with pytest.raises(TimeoutError):
        await rig.detach()
    uncertain = rig.store.load(native_detach_request_id("attach_0001"))
    assert uncertain.phase is TransactionPhase.RECOVERY_REQUIRED
    assert any(a.phase is ActionPhase.UNCERTAIN for a in uncertain.actions)
    result = await rig.controller().recover_native(
        request_id=uncertain.request_id, targets=rig.targets
    )
    assert result["accepted"]
    assert rig.states == rig.originals
    keys = [(s[2], s[3]) for s in rig.sent]
    assert len(keys) == len(set(keys))
    assert len(rig.sent) == start + 6


@pytest.mark.asyncio
async def test_failed_detach_mismatch_stops_no_repeat_and_retains_field_evidence(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    rig.reject_send = len(rig.sent) + 1
    with pytest.raises(LocalControlExecutionError, match="반환값 불일치"):
        await rig.detach()
    count = len(rig.sent)
    with pytest.raises(LocalControlExecutionError, match="재전송"):
        await rig.detach()
    assert len(rig.sent) == count
    record = rig.store.load(native_detach_request_id("attach_0001"))
    evidence = json.loads(rig.store.load_blob(record.actions[0].native_evidence_sha256))
    assert evidence["comparison"]["Linkage"]["actual"] == "sync_slave"
    assert evidence["comparison"]["Linkage"]["expected"] == "independent"
    assert evidence["match"] is False and evidence["previous"]


@pytest.mark.asyncio
async def test_final_reply_unavailable_keeps_partial_evidence_and_read_only_recovery(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    rig.reject_final_reply = True
    with pytest.raises(TimeoutError):
        await rig.detach()
    assert rig.states == rig.originals
    record = rig.store.load(native_detach_request_id("attach_0001"))
    assert record.phase is TransactionPhase.RECOVERY_REQUIRED
    assert all(a.phase is ActionPhase.VERIFIED for a in record.actions)
    count = len(rig.sent)
    rig.reject_final_reply = False
    result = await rig.detach()
    assert result["accepted"] and len(rig.sent) == count


@pytest.mark.asyncio
async def test_cancellation_waits_for_original_restore_then_preserves_cancellation(tmp_path):
    rig = Rig(tmp_path)
    rig.cancel_ack = 5
    with pytest.raises(asyncio.CancelledError):
        await rig.attach()
    assert rig.states == rig.originals
    assert (
        rig.store.load(native_detach_request_id("attach_0001")).phase is TransactionPhase.VERIFIED
    )


@pytest.mark.asyncio
async def test_no_qualification_or_duplicate_identity_starts_no_write(tmp_path):
    rig = Rig(tmp_path, qualified=False)
    with pytest.raises(ValueError, match="자격"):
        await rig.attach()
    assert rig.sent == [] and rig.store.records() == ()
    bad = rig.targets[1].model_copy(
        update={"physical_identity_key": rig.targets[0].physical_identity_key}
    )
    with pytest.raises(ValueError, match="중복"):
        await rig.controller().attach(
            request_id="attach_0001", plan=_plan(), targets=(rig.targets[0], bad)
        )
    assert rig.sent == []


@pytest.mark.asyncio
async def test_owned_attachment_blocks_new_baseline_until_detached_and_old_detach_is_cached(
    tmp_path,
):
    rig = Rig(tmp_path)
    await rig.attach()
    with pytest.raises(LocalControlExecutionError, match="이전 결속"):
        await rig.attach(request_id="attach_0002")
    await rig.detach()
    await rig.attach(request_id="attach_0002")
    count = len(rig.sent)
    cached = await rig.detach()
    assert not cached["current_state_verified"]
    assert len(rig.sent) == count
    assert SCHEMA.decode_status(rig.states["slave"])["Linkage"] == "sync_slave"


@pytest.mark.asyncio
async def test_generic_recovery_never_reverses_native_and_supplies_exact_detach_command(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    count = len(rig.sent)
    with pytest.raises(LocalControlExecutionError) as caught:
        await LocalControlCoordinator.recover(rig.controller(), "attach_0001")
    assert "recover-wavemaker --request-id attach_0001" in str(caught.value)
    assert native_detach_request_id("attach_0001") in str(caught.value)
    assert len(rig.sent) == count


@pytest.mark.asyncio
async def test_current_identity_and_context_immutable_before_detach(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    count = len(rig.sent)
    bad = rig.targets[1].model_copy(update={"physical_identity_key": "c" * 64})
    with pytest.raises(ValueError, match="신원"):
        await rig.detach((rig.targets[0], bad))
    record = rig.store.load("attach_0001")
    replacement = record.evolve().model_copy(
        update={"native_context_sha256": rig.store.install_blob(b"{}")}
    )
    with pytest.raises(RuntimeError, match="ownership"):
        rig.store.save(record, replacement)
    assert len(rig.sent) == count


@pytest.mark.parametrize("command", ["attach-wavemaker", "detach-wavemaker", "recover-wavemaker"])
def test_native_cli_requires_explicit_write_and_routes_all_commands(command, monkeypatch, tmp_path):
    parser = local_control_cli.build_parser()
    common = [command, "--role-attested-by", "operator", "--manifest", str(tmp_path / "manifest")]
    if command == "attach-wavemaker":
        common += [
            "--file",
            str(tmp_path / "plan"),
            "--qualifications",
            str(tmp_path / "qualification"),
            "--request-id",
            "attach_0001",
        ]
    elif command == "detach-wavemaker":
        common += ["--attachment-id", "attach_0001"]
    else:
        common += ["--request-id", "attach_0001"]
    with pytest.raises(ValueError, match="write-enabled"):
        local_control_cli._run(parser.parse_args(common))


def test_native_cli_offline_status_does_not_require_config_or_hardware(monkeypatch, capsys):
    monkeypatch.setattr(
        local_control_cli, "_activate_current_hardware_safety_volume_identity", lambda: None
    )
    monkeypatch.setattr(
        local_control_cli,
        "_native_coordinator",
        lambda config, catalog: SimpleNamespace(
            status=lambda request: {"request_id": request, "current_state_verified": False}
        ),
    )
    assert (
        local_control_cli._run(
            local_control_cli.build_parser().parse_args(
                ["wavemaker-status", "--request-id", "attach_0001"]
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["current_state_verified"] is False


@pytest.mark.asyncio
async def test_inactive_zero_options_restore_exact_without_normalization(tmp_path):
    rig = Rig(tmp_path)
    for name in rig.states:
        raw = bytearray(rig.states[name])
        raw[SCHEMA.by_name("FeedTime").position.byte_offset] = 0
        raw[SCHEMA.by_name("Cust_Wav_Freq").position.byte_offset] = 0
        rig.states[name] = bytes(raw)
    rig.originals = dict(rig.states)
    await rig.attach()
    await rig.detach()
    assert rig.states == rig.originals
    for name in rig.states:
        assert SCHEMA.decode_status(rig.states[name])["FeedTime"] == 0
    assert all("FeedTime" not in s[1] for s in rig.sent[:2])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("SwitchON", True),
        ("TimerON", False),
        ("Mode", "constant"),
        ("Flow", 46),
        ("Frequency", 33),
        ("FeedTime", 13),
        ("Cust_Wav_Freq", 28),
        ("Linkage", "master"),
        ("image", None),
    ],
)
async def test_final_each_control_and_full_image_mismatch_is_not_complete(tmp_path, field, value):
    rig = Rig(tmp_path)
    await rig.attach()

    def transform(name, status, reports):
        if rig.states == rig.originals and name == "master" and not reports:
            if field == "image":
                return status[:442] + bytes([status[442] ^ 1]) + status[443:]
            return _put(status, {field: value})
        return status

    rig.read_transform = transform
    with pytest.raises(LocalControlExecutionError, match="최종"):
        await rig.detach()
    report = rig.controller().status(native_detach_request_id("attach_0001"))
    assert report["accepted"] is False
    row = report["verification"]["devices"][0]
    assert row["complete"] is False
    if field == "image":
        assert row["schedule_match"] is False
    else:
        assert row["fields"][field]["match"] is False


@pytest.mark.asyncio
async def test_image_precondition_recheck_failure_never_sends_image(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    count = len(rig.sent)
    rig.read_transform = lambda name, status, reports: (
        _put(status, {"TimerON": True}) if not reports else status
    )
    with pytest.raises(LocalControlExecutionError, match="시간표 전송 전"):
        await rig.detach()
    assert len(rig.sent) == count + 2
    assert all("AutoTime00" not in s[1] for s in rig.sent[count:])
    record = rig.store.load(native_detach_request_id("attach_0001"))
    assert record.actions[2].phase is ActionPhase.PREPARED


@pytest.mark.asyncio
async def test_crash_after_record_fsync_before_return_can_recover_without_attach_replay(
    tmp_path, monkeypatch
):
    rig = Rig(tmp_path)
    create = rig.store.create

    def persist_then_fail(record):
        create(record)
        raise OSError("synthetic crash after fsync")

    monkeypatch.setattr(rig.store, "create", persist_then_fail)
    with pytest.raises(OSError):
        await rig.attach()
    assert rig.sent == [] and rig.store.load("attach_0001") is not None
    monkeypatch.setattr(rig.store, "create", create)
    result = await rig.controller().recover_native(request_id="attach_0001", targets=rig.targets)
    assert result["accepted"] and rig.states == rig.originals
    assert all(s[2] == native_detach_request_id("attach_0001") for s in rig.sent)


@pytest.mark.parametrize("command", ["attach-wavemaker", "detach-wavemaker", "recover-wavemaker"])
def test_native_cli_routes_config_targets_plan_and_returns_public_result(
    tmp_path, monkeypatch, capsys, command
):
    targets = (_binding("master", "a"), _binding("slave", "b"))
    calls = []

    class Controller:
        def targets_for(self, request_id):
            return targets

        async def attach(self, **kwargs):
            calls.append(("attach", kwargs))
            return {"accepted": True, "current_state_verified": True}

        async def detach(self, **kwargs):
            calls.append(("detach", kwargs))
            return {"accepted": True, "current_state_verified": True}

        async def recover_native(self, **kwargs):
            calls.append(("recover", kwargs))
            return {"accepted": True, "current_state_verified": True}

    config = SimpleNamespace(
        runtime=SimpleNamespace(mode=RuntimeMode.CONTROL, dry_run=False),
        devices=[
            SimpleNamespace(
                id=t.logical_member, control=SimpleNamespace(allow_hardware_writes=True)
            )
            for t in targets
        ],
    )
    monkeypatch.setattr(
        local_control_cli, "_activate_current_hardware_safety_volume_identity", lambda: None
    )
    monkeypatch.setattr(
        local_control_cli,
        "_prepare_current",
        lambda args: SimpleNamespace(
            config=config, manifest=SimpleNamespace(devices=targets, content=None)
        ),
    )
    monkeypatch.setattr(local_control_cli, "load_current_qualifications", lambda path: ())
    monkeypatch.setattr(local_control_cli, "_native_coordinator", lambda *args: Controller())
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(_plan().model_dump_json())
    argv = [command, "--write-enabled", "--role-attested-by", "operator"]
    if command == "attach-wavemaker":
        argv += [
            "--request-id",
            "attach_0001",
            "--file",
            str(plan_file),
            "--qualifications",
            str(tmp_path / "qual"),
        ]
    else:
        argv += [
            "--attachment-id" if command == "detach-wavemaker" else "--request-id",
            "attach_0001",
        ]
    assert local_control_cli.main(argv) == 0
    assert calls[0][1]["targets"] == targets
    assert calls[0][0] == command.split("-")[0]
    assert json.loads(capsys.readouterr().out)["current_state_verified"] is True
    config.runtime.dry_run = True
    assert local_control_cli.main(argv) == 2
    assert len(calls) == 1
    config.runtime.dry_run = False
    config.devices[1].control.allow_hardware_writes = False
    assert local_control_cli.main(argv) == 2
    assert len(calls) == 1


def test_generic_native_recovery_hint_is_printed_without_raw_exception(monkeypatch, capsys):
    from jebao_flow.local_control.coordinator import NativeRecoveryRequiredError

    def fail(args):
        raise NativeRecoveryRequiredError("attach_0001", None)

    monkeypatch.setattr(local_control_cli, "_run", fail)
    assert local_control_cli.main(["catalog"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["next_command"] == "recover-wavemaker --request-id attach_0001"
    assert result["detach_request_id"] is None


@pytest.mark.asyncio
async def test_corrupt_context_still_directs_generic_recover_to_dedicated_path(
    tmp_path, monkeypatch
):
    from jebao_flow.local_control.coordinator import NativeRecoveryRequiredError

    rig = Rig(tmp_path)
    await rig.attach()
    record = rig.store.load("attach_0001")
    load = rig.store.load_blob
    monkeypatch.setattr(
        rig.store,
        "load_blob",
        lambda digest: b"{}" if digest == record.native_context_sha256 else load(digest),
    )
    with pytest.raises(NativeRecoveryRequiredError) as caught:
        await LocalControlCoordinator.recover(rig.controller(), "attach_0001")
    assert caught.value.detach_id is None


async def _stalled_detach(rig):
    await rig.attach()
    rig.fail_ack = rig.reject_send = len(rig.sent) + 1
    with pytest.raises(TimeoutError):
        await rig.detach()
    child = rig.store.load(native_detach_request_id("attach_0001"))
    assert child.actions[0].phase is ActionPhase.UNCERTAIN
    assert all(a.phase is ActionPhase.PREPARED for a in child.actions[1:])
    rig.fail_ack = rig.reject_send = None
    return child


@pytest.mark.asyncio
async def test_owned_assistance_retains_unknown_delivery_and_restores_without_resend(tmp_path):
    from jebao_flow.local_control.wavemaker_lifecycle import native_assistance_request_id

    rig = Rig(tmp_path)
    child = await _stalled_detach(rig)
    old_context = rig.store.load("attach_0001").native_context_sha256
    count = len(rig.sent)
    result = await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert result["accepted"] and result["current_state_verified"]
    assert rig.states == rig.originals
    assert rig.store.load("attach_0001").native_context_sha256 == old_context
    support = rig.store.load(native_assistance_request_id("attach_0001"))
    assert support.phase is TransactionPhase.VERIFIED
    assert [a.function for a in support.actions] == [
        "linkage_role",
        "schedule_enable",
        "manual_mode",
        "manual_flow",
        "manual_frequency",
    ]
    assert all(
        a.baseline_status_sha256
        in {child.actions[0].baseline_status_sha256, child.actions[1].baseline_status_sha256}
        for a in support.actions
    )
    new = rig.sent[count:]
    assert new[0][0:2] == ("master", {"Linkage": "independent"})
    assert new[1][0:2] == ("slave", {"SwitchON": True, "TimerON": False, "Linkage": "independent"})
    assert all(not (s[2] == child.request_id and s[3] == child.actions[0].action_id) for s in new)
    recovered = rig.store.load(child.request_id)
    action = recovered.actions[0]
    assert action.phase is ActionPhase.VERIFIED and action.acknowledgement_sha256 is None
    assert action.forward_payload_sha256 == child.actions[0].forward_payload_sha256
    assert action.diagnostic == "assisted_target_observed_delivery_unknown"
    evidence = json.loads(rig.store.load_blob(action.native_evidence_sha256))
    assert evidence["assistance"]["request_id"] == support.request_id
    assert evidence["assistance"]["original_delivery"] == "UNKNOWN"
    context = json.loads(rig.store.load_blob(support.native_context_sha256))
    before = json.loads(rig.store.load_blob(context["assisted_record_sha256"]))
    assert before["actions"][0]["phase"] == ActionPhase.UNCERTAIN.value
    reads, sends = len(rig.reads), len(rig.sent)
    replay = await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert replay["accepted"] and replay["current_state_verified"] is False
    assert (len(rig.reads), len(rig.sent)) == (reads, sends)


@pytest.mark.asyncio
async def test_assistance_failure_never_rolls_back_or_resends_attempted_scalar(tmp_path):
    from jebao_flow.local_control.coordinator import NativeRecoveryRequiredError
    from jebao_flow.local_control.wavemaker_lifecycle import native_assistance_request_id

    rig = Rig(tmp_path)
    await _stalled_detach(rig)
    count = len(rig.sent)
    rig.fail_ack = rig.reject_send = count + 2
    with pytest.raises(TimeoutError):
        await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert len(rig.sent) == count + 2
    assert SCHEMA.decode_status(rig.states["master"])["Linkage"] == "independent"
    support = rig.store.load(native_assistance_request_id("attach_0001"))
    assert support.actions[1].phase is ActionPhase.UNCERTAIN
    assert all(a.phase is ActionPhase.PREPARED for a in support.actions[2:])
    with pytest.raises(LocalControlExecutionError, match="재전송하지"):
        await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert len(rig.sent) == count + 2
    with pytest.raises(NativeRecoveryRequiredError):
        await LocalControlCoordinator.recover(rig.controller(), support.request_id)
    assert len(rig.sent) == count + 2


@pytest.mark.asyncio
async def test_assistance_requires_exact_stalled_shape_and_current_qualifications(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach()
    count = len(rig.sent)
    with pytest.raises(KeyError):
        await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert len(rig.sent) == count
    rig.fail_ack = rig.reject_send = count + 1
    with pytest.raises(TimeoutError):
        await rig.detach()
    rig.catalog = build_local_control_catalog()
    with pytest.raises(ValueError, match="자격"):
        await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert len(rig.sent) == count + 1


@pytest.mark.asyncio
async def test_assistance_rejects_other_operation_on_the_same_physical_devices(tmp_path):
    rig = Rig(tmp_path)
    child = await _stalled_detach(rig)
    unrelated = child.model_copy(
        update={
            "request_id": "unrelated_0001",
            "revision": 0,
            "phase": TransactionPhase.PREPARED,
        }
    )
    rig.store.create(unrelated)
    count, reads = len(rig.sent), len(rig.reads)
    with pytest.raises(LocalControlExecutionError, match="이 장비의 미완료 변경"):
        await rig.controller().assist_detach(request_id="attach_0001", targets=rig.targets)
    assert (len(rig.sent), len(rig.reads)) == (count, reads)


@pytest.mark.asyncio
async def test_legacy_native_context_serialization_omits_new_field(tmp_path):
    from jebao_flow.local_control.wavemaker_lifecycle import NativeContext

    rig = Rig(tmp_path)
    await rig.attach()
    raw = rig.store.load_blob(rig.store.load("attach_0001").native_context_sha256)
    assert b"assisted_record_sha256" not in raw
    assert NativeContext.model_validate_json(raw).model_dump_json().encode() == raw


@pytest.mark.asyncio
async def test_failed_last_async_and_detach_can_restore_owned_original_without_resend(tmp_path):
    rig = Rig(tmp_path)

    def timeout_before_copy(number, address, changes):
        if number in {9, 10}:
            raise TimeoutError("synthetic final role and first detach ACK loss")

    rig.before_send = timeout_before_copy
    with pytest.raises(LocalControlExecutionError, match="원복 미확인"):
        await rig.attach("async")
    source = rig.store.load("attach_0001")
    child = rig.store.load(native_detach_request_id(source.request_id))
    assert source.phase is TransactionPhase.RECOVERY_REQUIRED
    assert [a.phase for a in source.actions[:STAGED_ASYNC_ROLE]] == [ActionPhase.VERIFIED] * 8
    assert source.actions[STAGED_ASYNC_ROLE].phase is ActionPhase.UNCERTAIN
    assert child.actions[0].phase is ActionPhase.UNCERTAIN
    assert all(a.phase is ActionPhase.PREPARED for a in child.actions[1:])
    failed = source.actions[STAGED_ASYNC_ROLE]
    rig.before_send = None
    sent = len(rig.sent)
    result = await rig.controller().assist_detach(request_id=source.request_id, targets=rig.targets)
    assert result["accepted"] and result["current_state_verified"]
    assert rig.states == rig.originals
    recovered = rig.store.load(source.request_id)
    assert recovered.phase is TransactionPhase.ROLLED_BACK
    assert recovered.native_context_sha256 == source.native_context_sha256
    assert recovered.actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    assert (
        recovered.actions[STAGED_ASYNC_ROLE].native_evidence_sha256 == failed.native_evidence_sha256
    )
    assert (
        recovered.actions[STAGED_ASYNC_ROLE].diagnostic
        == "native_own_baseline_restored_delivery_unknown"
    )
    assert all(
        row[0] != "slave"
        or (
            row[4] != rig.store.load_blob(failed.forward_payload_sha256)
            and row[4] != rig.store.load_blob(child.actions[0].forward_payload_sha256)
        )
        for row in rig.sent[sent:]
    )
    assert rig.store.load(child.request_id).phase is TransactionPhase.VERIFIED
    reads, sends = len(rig.reads), len(rig.sent)
    replay = await rig.controller().assist_detach(request_id=source.request_id, targets=rig.targets)
    assert replay["accepted"] and replay["current_state_verified"] is False
    assert (len(rig.reads), len(rig.sent)) == (reads, sends)


@pytest.mark.asyncio
async def test_failed_source_outside_last_async_shape_is_not_assisted(tmp_path):
    rig = Rig(tmp_path)

    def timeout_before_copy(number, address, changes):
        if number in {7, 8}:
            raise TimeoutError("synthetic earlier role and first detach ACK loss")

    rig.before_send = timeout_before_copy
    with pytest.raises(LocalControlExecutionError, match="원복 미확인"):
        await rig.attach("async")
    rig.before_send = None
    reads, sends = len(rig.reads), len(rig.sent)
    controller = rig.controller()
    source = rig.store.load("attach_0001")
    with pytest.raises(ValueError, match="slave 역할 또는 최종 대조"):
        controller._require_assistance_source(source, controller._context(source))
    with pytest.raises(ValueError, match="slave 역할 또는 최종 대조"):
        await controller.assist_detach(request_id="attach_0001", targets=rig.targets)
    assert (len(rig.reads), len(rig.sent)) == (reads, sends)


@pytest.mark.asyncio
async def test_async_transition_only_writes_linkage_and_preserves_pair_observation(tmp_path):
    rig = Rig(tmp_path)
    await rig.attach("async")
    assert rig.sent[8][0:2] == ("slave", {"Linkage": "async_slave"})
    source = rig.store.load("attach_0001")
    final = json.loads(rig.store.load_blob(source.native_final_sha256))
    observed = None
    while final["previous"] is not None:
        final = json.loads(rig.store.load_blob(final["previous"]))
        if final["devices"][0].get("stage") == "before_async_role_only":
            observed = final
            break
    assert observed is not None
    assert [d["logical_member"] for d in observed["devices"]] == ["master", "slave"]
    assert [d["actual_controls"]["Linkage"] for d in observed["devices"]] == [
        "master",
        "sync_slave",
    ]
    assert all(d["complete"] is False for d in observed["devices"])
    for row in observed["devices"]:
        capture = json.loads(rig.store.load_blob(row["capture_sha256"]))
        assert capture["action"] == 4  # fixture reports only, not a firmware assertion
        assert rig.store.load_blob(capture["wire_sha256"])
    await rig.detach()
    assert rig.states == rig.originals


@pytest.mark.asyncio
async def test_pre_async_pair_read_failure_sends_no_async_and_restores(tmp_path):
    rig = Rig(tmp_path)

    def fail_pair(address, status, reports):
        if len(rig.sent) == 8 and address == "master":
            raise TimeoutError("synthetic paired observation read failure")
        return status

    rig.read_transform = fail_pair
    with pytest.raises(LocalControlExecutionError, match="자기 원본 복원 확인"):
        await rig.attach("async")
    assert all(row[1].get("Linkage") != "async_slave" for row in rig.sent)
    assert rig.store.load("attach_0001").actions[STAGED_ASYNC_ROLE].acknowledgement_sha256 is None
    assert rig.states == rig.originals
