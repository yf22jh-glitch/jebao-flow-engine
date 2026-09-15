"""Operator-owned pause: journal/control-flow faults only, no firmware model."""

import json
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_cli
from jebao_flow.config import RuntimeMode
from jebao_flow.local_control.coordinator import LocalControlExecutionError
from jebao_flow.local_control.journal import ActionPhase, native_detach_request_id
from tests.unit.test_local_control_wavemaker_lifecycle import Rig, _plan


async def observe(rig, **kwargs):
    return await rig.controller().attach(
        request_id="observe_0001",
        plan=_plan("async"),
        targets=rig.targets,
        await_operator_observation=True,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "ack_applied", "ack_unapplied", "cancel"])
async def test_final_attempt_retains_state_until_explicit_owned_recovery(tmp_path, failure):
    rig = Rig(tmp_path)
    if failure.startswith("ack"):
        rig.fail_ack = 9
    if failure == "ack_unapplied":
        rig.reject_send = 9
    if failure == "cancel":
        rig.cancel_ack = 9
    result = await observe(rig)
    assert len(rig.sent) == 9
    assert not result["accepted"]
    assert result["phase"] == "recovery_required"
    assert result["awaiting_operator_observation"] is True
    assert result["automatic_restore"] is False
    assert result["restoration_complete"] is False
    assert result["next_action"] == "await_operator_instruction"
    assert result["restore_command"] == "recover-wavemaker --request-id observe_0001"
    assert rig.store.load(native_detach_request_id("observe_0001")) is None
    assert rig.states != rig.originals
    source = rig.store.load("observe_0001")
    assert rig.store.unfinished_records() == (source,)
    context = rig.controller()._context(source)
    assert context.await_operator_observation is True
    for original in context.originals:
        assert (
            rig.store.load_blob(original.status_sha256)
            == rig.originals[original.target.logical_member]
        )
    if failure != "none":
        assert result["actions"][8]["acknowledgement_received"] is False
        assert result["actions"][8]["delivery"] == "UNKNOWN"
    if failure in {"ack_unapplied", "cancel"}:
        assert source.actions[8].phase is ActionPhase.UNCERTAIN
        assert source.actions[8].diagnostic == "native_stage_unverified"
    count = len(rig.reads)
    cached = await observe(rig)
    assert cached["awaiting_operator_observation"] and not cached["current_state_verified"]
    assert len(rig.reads) == count and len(rig.sent) == 9
    assert rig.store.load("observe_0001") == source
    with pytest.raises(ValueError, match="다른 결속 계획"):
        await rig.controller().attach(
            request_id="observe_0001", plan=_plan("async"), targets=rig.targets
        )
    with pytest.raises(LocalControlExecutionError):
        await rig.controller().attach(
            request_id="observe_0002", plan=_plan("async"), targets=rig.targets
        )
    assert len(rig.sent) == 9
    # Explicit later instruction, never an automatic timeout/finally restoration.
    restored = await rig.controller().recover_native(request_id="observe_0001", targets=rig.targets)
    assert restored["accepted"] and rig.states == rig.originals
    assert "awaiting_operator_observation" not in restored
    assert "awaiting_operator_observation" not in rig.controller().status("observe_0001")
    assert rig.store.unfinished_records() == ()
    assert sum(s[2] == "observe_0001" for s in rig.sent) == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["earlier_ack", "before_async"])
async def test_pre_async_failures_keep_existing_owned_recovery(tmp_path, failure):
    rig = Rig(tmp_path)
    if failure == "earlier_ack":
        rig.fail_ack = 5
    else:

        def fail_before_async(name, status, reports):
            if len(rig.sent) == 8:
                raise OSError("synthetic pre-Async read failure")
            return status

        rig.read_transform = fail_before_async
    with pytest.raises(LocalControlExecutionError):
        await observe(rig)
    assert not any(s[1] == {"Linkage": "async_slave"} for s in rig.sent)
    assert rig.store.load(native_detach_request_id("observe_0001")) is not None
    assert "awaiting_operator_observation" not in rig.controller().status("observe_0001")


@pytest.mark.asyncio
async def test_observation_is_async_only_and_legacy_context_omits_new_field(tmp_path):
    rig = Rig(tmp_path)
    with pytest.raises(ValueError, match="Async"):
        await rig.controller().attach(
            request_id="observe_0001",
            plan=_plan(),
            targets=rig.targets,
            await_operator_observation=True,
        )
    assert rig.reads == [] and rig.sent == []
    await rig.attach()
    context = rig.controller()._context(rig.store.load("attach_0001"))
    assert "await_operator_observation" not in json.loads(context.model_dump_json())


def test_cli_observation_is_explicit_and_only_on_attach():
    parser = local_control_cli.build_parser()
    args = [
        "attach-wavemaker",
        "--file",
        "plan.json",
        "--qualifications",
        "qual.json",
        "--request-id",
        "observe_0001",
        "--role-attested-by",
        "operator",
    ]
    assert parser.parse_args(args).await_operator_observation is False
    assert parser.parse_args([*args, "--await-operator-observation"]).await_operator_observation
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "recover-wavemaker",
                "--request-id",
                "observe_0001",
                "--role-attested-by",
                "operator",
                "--await-operator-observation",
            ]
        )


def test_cli_routes_observation_and_does_not_report_pending_as_success(
    tmp_path, monkeypatch, capsys
):
    rig = Rig(tmp_path)
    calls = []

    async def attach(**kwargs):
        calls.append(kwargs)
        return {"accepted": False, "awaiting_operator_observation": True}

    config = SimpleNamespace(
        runtime=SimpleNamespace(mode=RuntimeMode.CONTROL, dry_run=False),
        devices=[
            SimpleNamespace(
                id=t.logical_member, control=SimpleNamespace(allow_hardware_writes=True)
            )
            for t in rig.targets
        ],
    )
    monkeypatch.setattr(
        local_control_cli, "_activate_current_hardware_safety_volume_identity", lambda: None
    )
    monkeypatch.setattr(
        local_control_cli,
        "_prepare_current",
        lambda _: SimpleNamespace(
            config=config, manifest=SimpleNamespace(devices=rig.targets, content=None)
        ),
    )
    monkeypatch.setattr(local_control_cli, "load_current_qualifications", lambda _: ())
    monkeypatch.setattr(
        local_control_cli, "_native_coordinator", lambda *_: SimpleNamespace(attach=attach)
    )
    plan = tmp_path / "plan.json"
    plan.write_text(_plan("async").model_dump_json())
    assert (
        local_control_cli.main(
            [
                "attach-wavemaker",
                "--file",
                str(plan),
                "--qualifications",
                "qual.json",
                "--request-id",
                "observe_0001",
                "--role-attested-by",
                "operator",
                "--write-enabled",
                "--await-operator-observation",
            ]
        )
        == 2
    )
    assert calls[0]["await_operator_observation"] is True
    assert calls[0]["targets"] == rig.targets
    assert json.loads(capsys.readouterr().out)["awaiting_operator_observation"] is True
