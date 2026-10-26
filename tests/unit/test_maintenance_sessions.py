"""Real TCP stream tests for request-scoped reuse, freshness and failure lifetime.

The peer tests protocol ordering, not inferred motor/firmware behavior. Live
read-only reuse evidence is in 2026-10-26-pair-session-latency-baseline.md.
"""

import asyncio
import struct
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jebao_flow import local_control_external_manual_cli as cli
from jebao_flow.local_control.device_writer import ConnectedDeviceWriter
from jebao_flow.protocol.codec import GizwitsCommand as C
from jebao_flow.protocol.codec import encode_frame, read_frame
from jebao_flow.protocol.control_session import GizwitsSession
from jebao_flow.protocol.errors import ProtocolTimeoutError
from tests.unit.test_fixed_endpoint import rig as rig


@pytest.fixture
async def stream(rig, monkeypatch):
    st = SimpleNamespace(
        auth=0,
        accepts=0,
        reads=0,
        writes=0,
        peers=[],
        tasks=set(),
        ignore_heartbeat=False,
        ignore_write=False,
        partial=False,
        after_heartbeat=False,
        partial_sent=asyncio.Event(),
        raw=[],
        sessions=[],
    )

    async def peer(reader, writer):
        st.accepts += 1
        st.peers.append(writer)
        task = asyncio.current_task()
        st.tasks.add(task)
        try:
            while True:
                frame = await read_frame(reader)
                if frame.command == C.PASSCODE_REQUEST:
                    result = encode_frame(C.PASSCODE_RESPONSE, struct.pack(">H", 4) + b"test")
                elif frame.command == C.LOGIN_REQUEST:
                    st.auth += 1
                    result = encode_frame(C.LOGIN_RESPONSE, b"\0")
                elif frame.command == C.HEARTBEAT_REQUEST:
                    if st.ignore_heartbeat:
                        st.ignore_heartbeat = False
                        continue
                    result = encode_frame(C.HEARTBEAT_RESPONSE)
                    if st.after_heartbeat:
                        result += encode_frame(C.SERIAL_TRANSMIT_RESPONSE, b"\x04late-old")
                elif frame.command == C.SERIAL_CONTROL_REQUEST:
                    st.writes += 1
                    if st.ignore_write:
                        continue
                    result = encode_frame(C.SERIAL_CONTROL_RESPONSE, frame.payload[:4] + b"ok")
                else:
                    assert frame.command == C.SERIAL_TRANSMIT_REQUEST
                    st.reads += 1
                    result = encode_frame(
                        C.SERIAL_TRANSMIT_RESPONSE, b"\x03" + str(st.reads).encode()
                    )
                    if st.partial:
                        st.partial = False
                        writer.write(result[:6])
                        await writer.drain()
                        st.partial_sent.set()
                        continue
                    # Unsolicited old state and an unknown frame queued after the reply.
                    result += encode_frame(C.SERIAL_TRANSMIT_RESPONSE, b"\x04old")
                    result += encode_frame(0x0062, b"unknown")
                writer.write(result)
                await writer.drain()
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            st.tasks.discard(task)

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    def make_session(address):
        s = GizwitsSession(address, port=port, response_timeout_seconds=0.08)
        st.sessions.append(s)
        return s

    monkeypatch.setattr(
        cli,
        "configured_writer_factory",
        lambda _: lambda ep: ConnectedDeviceWriter(ep, session_factory=make_session),
    )
    b = rig.make()
    endpoint = await b.resolver.resolve(rig.targets[0])
    st.backend = b
    st.endpoint = endpoint
    st.scope = getattr(b, "maintenance_session_scope", nullcontext)
    st.new_writer = lambda ep=None: b.writer_factory(ep or endpoint)
    try:
        yield st
    finally:
        server.close()
        await server.wait_closed()
        for w in st.peers:
            w.close()
        for task in tuple(st.tasks):
            task.cancel()
        await asyncio.gather(*tuple(st.tasks), return_exceptions=True)


async def capture(st, *, endpoint=None, reports=False):
    writer = st.new_writer(endpoint)
    try:
        return await writer.connect_and_capture(accept_reports=reports)
    finally:
        await writer.close()


async def test_livebackend_scope_reuses_authentication_without_reusing_old_state(stream, rig):
    st = stream
    async with st.scope():
        assert (await capture(st)).status_payload == b"1"
        assert (await capture(st, reports=True)).status_payload == b"2"
        assert st.auth == 1 and st.accepts == 1, "each sequential writer repeats TCP authentication"
    assert all(not s.connected for s in st.sessions)
    assert encode_frame(C.SERIAL_TRANSMIT_RESPONSE, b"\x04old") in rig.raw
    assert encode_frame(0x0062, b"unknown") in rig.raw


async def test_outside_scope_still_uses_fresh_sessions(stream):
    assert (await capture(stream)).status_payload == b"1"
    assert (await capture(stream)).status_payload == b"2"
    assert stream.auth == 2 and all(not s.connected for s in stream.sessions)


async def test_reuse_heartbeat_timeout_reconnects_before_any_control(stream):
    async with stream.scope():
        await capture(stream)
        stream.ignore_heartbeat = True
        assert (await capture(stream, reports=True)).status_payload == b"2"
        assert stream.auth == 2 and stream.writes == 0
    assert all(not s.connected for s in stream.sessions)


async def test_endpoint_metadata_change_reopens_connection(stream):
    async with stream.scope():
        await capture(stream)
        changed = replace(stream.endpoint, wifi_firmware_version="different-current-version")
        assert (await capture(stream, endpoint=changed)).status_payload == b"2"
        assert stream.auth == 2


async def test_cancel_partial_read_retires_stream_and_next_read_is_fresh(stream):
    async with stream.scope():
        stream.partial = True
        task = asyncio.create_task(capture(stream))
        await asyncio.wait_for(stream.partial_sent.wait(), 0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await capture(stream)).status_payload == b"2"
        assert stream.auth == 2
    assert all(not s.connected for s in stream.sessions)


async def test_control_timeout_is_never_replayed_by_session_reuse(stream):
    async with stream.scope():
        writer = stream.new_writer()
        try:
            await writer.connect_and_capture(accept_reports=False)
            stream.ignore_write = True
            with pytest.raises(ProtocolTimeoutError):
                await writer._session.send_raw_control(b"\x01test")
            assert stream.writes == 1
        finally:
            await writer.close()
        await capture(stream)
        assert stream.auth == 2 and stream.writes == 1


async def test_scope_closes_connections_when_body_fails(stream):
    with pytest.raises(ValueError, match="body failure"):
        async with stream.scope():
            await capture(stream)
            raise ValueError("body failure")
    assert all(not s.connected for s in stream.sessions)


async def test_raw_preservation_failure_stops_before_reused_read(stream):
    async with stream.scope():
        await capture(stream)

        def fail(_):
            raise OSError("raw store failed")

        stream.backend._maintenance_sessions._raw_store = fail
        with pytest.raises(OSError, match="raw store failed"):
            await capture(stream)
        assert stream.reads == 1 and stream.writes == 0
    assert all(not s.connected for s in stream.sessions)


async def test_report_buffered_after_heartbeat_is_not_a_fresh_reply(stream, rig):
    async with stream.scope():
        await capture(stream)
        stream.after_heartbeat = True
        assert (await capture(stream, reports=True)).status_payload == b"2"
        assert stream.auth == 2
    assert encode_frame(C.SERIAL_TRANSMIT_RESPONSE, b"\x04late-old") in rig.raw


async def test_raw_storage_error_survives_simultaneous_heartbeat_timeout(stream):
    async with stream.scope():
        await capture(stream)

        def fail(_):
            raise OSError("raw store failed while peer also timed out")

        stream.backend._maintenance_sessions._raw_store = fail
        stream.ignore_heartbeat = True
        with pytest.raises(OSError, match="raw store failed"):
            await capture(stream)
        assert stream.reads == 1 and stream.writes == 0
    assert all(not s.connected for s in stream.sessions)
