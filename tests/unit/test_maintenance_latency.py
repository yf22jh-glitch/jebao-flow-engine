"""Exercise real maintenance/read adapters with delayed I/O, never live control.

The preserved 144.761s finish and five-second discovery are documented in
2026-10-26-maintenance-latency-baseline.md. Virtual discovery cost measures
orchestration, not a claim about firmware or new live OFF/ON duration.
"""
import asyncio
import hashlib
from types import SimpleNamespace
from datetime import timedelta

import pytest

from jebao_flow import local_control_external_manual_cli as cli
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.resolver import CurrentEndpointObservation, ResolvedEndpoint, CurrentEndpointResolutionError
from jebao_flow.local_control.read_retry import ReadRetryInterrupted
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRODUCT_KEY, LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
from tests.unit.test_maintenance_power_off import rig, request


def live_reads(r, monkeypatch):
    io = cli.LiveBackend.__new__(cli.LiveBackend)
    for name, product, identity in [("master", LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, r.backend.pair.master.identity), ("slave", LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, r.backend.pair.slave.identity), ("bar", LOCAL_WAVEMAKER_PRODUCT_KEY, r.backend.bar.physical_identity_key)]:
        setattr(io, name, LocalDeviceBinding(logical_member=name, product_key=product, physical_identity_key=identity, logical_role=name, role_attested_by="test", role_attested_at=r.clock[0]))
    stats = SimpleNamespace(discovery=0, active=0, peak=0, opened=[], closed=[], raw=[], authority=[], fail=None, reason=None, slow=False)
    async def observe(targets):
        stats.discovery += 1
        await asyncio.sleep(0.002)
        return tuple(CurrentEndpointObservation(t, diagnostic_reason=stats.reason) if stats.reason and t==io.slave else CurrentEndpointObservation(t, endpoint=ResolvedEndpoint(t, "192.0.2."+str(i+1))) for i,t in enumerate(targets))
    io.resolver = SimpleNamespace(observe_many=observe)
    io.catalog = SimpleNamespace(validate_intent=lambda i:i)
    io.coordinator = SimpleNamespace(_require_current_control_authority=lambda intent, endpoint:stats.authority.append((intent.function, endpoint.target.logical_member)))
    io.maintenance_unfinished = r.backend.maintenance_unfinished
    def blob(wire):
        stats.raw.append(wire)
        return hashlib.sha256(wire).hexdigest()
    io.journal = SimpleNamespace(install_blob=blob)
    class Writer:
        def __init__(self, endpoint): self.name=endpoint.target.logical_member
        async def connect_and_capture(self, *, accept_reports):
            stats.opened.append(self.name);stats.active+=1;stats.peak=max(stats.peak,stats.active)
            try:
                await asyncio.sleep(0.03 if stats.slow else 0.005)
                if stats.fail==self.name: raise ValueError("invalid state payload")
                return SimpleNamespace(status_payload=self.name.encode(),wire_frame=self.name.encode())
            finally: stats.active-=1
        async def close(self): stats.closed.append(self.name)
    io.writer_factory=Writer
    def decode(capture,target,*,preserve_raw):
        if preserve_raw:blob(capture.wire_frame)
        return getattr(r.backend.pair,target.logical_member)
    io._decode=decode
    def bar_values(_):
        v=r.backend.bar_state
        return {"TimerON":v["timer"],"FeedSwitch":v["feeding"],"Linkage":v["role"],"SwitchON":v["power"]}
    monkeypatch.setattr(cli,"get_product_schema",lambda _:SimpleNamespace(decode_status=bar_values))
    monkeypatch.setattr(cli,"extract_current_schedule_image",lambda *_:b"bar-image")
    r.backend.bar_state["image_sha256"]=hashlib.sha256(b"bar-image").hexdigest()
    r.backend.fresh_pair=io.fresh_pair;r.backend.fresh_bar=io.fresh_bar;r.backend.validate_maintenance=io.validate_maintenance
    if hasattr(io,"fresh_maintenance"):r.backend.fresh_maintenance=io.fresh_maintenance
    return io,stats


async def test_unchanged_hold_uses_one_full_discovery_and_fresh_snapshot(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch)
    assert (await request(rig,"maintenance_on"))["outcome"]=="maintenance_on"
    s.discovery=0;s.opened.clear();s.closed.clear();s.raw.clear();rig.backend.sent.clear()
    await rig.editor.maintenance.hold(force=True)
    assert s.discovery==1, "unchanged hold repeats five-second discovery"
    assert sorted(s.opened)==["bar","master","slave"]
    assert sorted(s.closed)==sorted(s.opened) and len(s.raw)==3
    assert rig.backend.sent==[] and rig.editor.maintenance.state()["phase"]=="powered_off"


async def test_real_adapter_start_finish_discovery_budget_and_command_order(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch)
    assert (await request(rig,"maintenance_on"))["outcome"]=="maintenance_on"
    start=s.discovery;commands=[(c.target,c.function,c.value) for c,_ in rig.backend.sent]
    assert commands==[("bar","schedule_enable",False),("bar","power",False),("master","linkage_role","independent"),("slave","linkage_role","independent"),("master","power",False),("slave","power",False)]
    rig.backend.sent.clear();s.discovery=0
    assert (await request(rig,"maintenance_off"))["outcome"]=="maintenance_off"
    finish=s.discovery;commands=[(c.target,c.function,c.value) for c,_ in rig.backend.sent]
    assert commands==[("master","power",True),("slave","power",True),("bar","power",True),("slave","linkage_role","async_slave"),("master","linkage_role","master"),("bar","schedule_enable",True)]
    print({"start_discoveries":start,"finish_discoveries":finish,"discovery_seconds":5*(start+finish),"max_parallel_reads":s.peak})
    assert start<=4 and finish<=10
    assert rig.store.active()["status"]=="active" and not rig.editor.maintenance.state()["enabled"]
    assert s.peak==3 and s.active==0


async def test_batched_read_keeps_full_snapshot_and_does_not_cache(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch)
    pair,bar=await io.fresh_maintenance(validate=True)
    assert pair==rig.backend.pair and bar["power"]
    assert s.discovery==1 and len(s.authority)==10 and s.peak==3 and len(s.raw)==3
    rig.backend.bar_state["power"]=False
    _,bar=await io.fresh_maintenance()
    assert not bar["power"] and s.discovery==2 and s.active==0


async def test_ambiguous_identity_opens_no_session(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch);s.reason="current_endpoint_ambiguous"
    with pytest.raises(CurrentEndpointResolutionError):await io.fresh_maintenance()
    assert s.discovery==1 and s.opened==[]


async def test_missing_identity_retries_new_full_observation(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch);s.reason="current_endpoint_absent"
    async def next_observation(_):s.reason=None
    io.read_retry.wait=next_observation
    pair,_=await io.fresh_maintenance()
    assert s.discovery==2 and pair==rig.backend.pair and sorted(s.opened)==["bar","master","slave"]


async def test_one_invalid_capture_cannot_return_partial_success(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch);s.fail="slave"
    with pytest.raises(ValueError,match="invalid state"):await io.fresh_maintenance()
    assert sorted(s.opened)==sorted(s.closed) and s.active==0


async def test_cancel_closes_all_parallel_read_sessions(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch);s.slow=True
    task=asyncio.create_task(io.fresh_maintenance())
    while s.active<3:await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert s.active==0 and sorted(s.opened)==sorted(s.closed)


async def test_operator_interrupt_never_returns_snapshot_or_leaves_readers(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch);s.slow=True
    task=asyncio.create_task(io.fresh_maintenance())
    while s.active<3:await asyncio.sleep(0)
    io.read_retry.interrupt()
    with pytest.raises(ReadRetryInterrupted):await task
    assert s.active==0 and sorted(s.opened)==sorted(s.closed)


async def test_unqualified_snapshot_opens_no_read_session(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch)
    def reject(*_):raise ValueError("qualification mismatch")
    io.coordinator._require_current_control_authority=reject
    with pytest.raises(ValueError,match="qualification"):await io.fresh_maintenance(validate=True)
    assert s.discovery==1 and s.opened==[]


async def test_read_timeout_drains_parallel_sessions_before_full_retry(rig,monkeypatch):
    io,s=live_reads(rig,monkeypatch);original=io.writer_factory
    first=True
    def factory(endpoint):
        writer=original(endpoint);capture=writer.connect_and_capture
        async def timed_capture(**kwargs):
            nonlocal first
            if endpoint.target==io.slave and first:
                first=False
                raise TimeoutError("delayed device reply")
            return await capture(**kwargs)
        writer.connect_and_capture=timed_capture
        return writer
    io.writer_factory=factory
    async def next_attempt(_):
        assert s.active==0
    io.read_retry.wait=next_attempt
    pair,_=await io.fresh_maintenance()
    assert pair==rig.backend.pair and s.discovery==2 and s.active==0
    assert len(s.closed)==6
