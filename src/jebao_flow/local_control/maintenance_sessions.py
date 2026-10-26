"""Reuse authenticated connections only within one serialized maintenance request.

Every new writer still resolves identity and reads current state. A heartbeat
response drains preceding stream traffic before a reused writer reads state;
all frames consumed by that barrier are retained. Controls are never retried by
this layer. Scope exit closes every connection, including cancelled/failed work.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from jebao_flow.protocol.codec import GizwitsCommand
from jebao_flow.protocol.errors import AuthenticationError, ProtocolConnectionError


@dataclass
class _Connection:
    endpoint: object
    session: object
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MaintenanceSessions:
    def __init__(self, raw_store):
        self._raw_store = raw_store
        self._entries = None

    @asynccontextmanager
    async def scope(self):
        if self._entries is not None:
            raise RuntimeError("maintenance session scope is already active")
        entries = self._entries = {}
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._entries = None
            # Drain every close even if another peer fails during cleanup.
            results = await asyncio.gather(
                *(entry.session.disconnect() for entry in entries.values()),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException) and not failed:
                    raise result

    def wrap(self, endpoint, writer):
        if self._entries is None:
            return writer
        key = endpoint.target.physical_identity_key
        original = writer._session
        entry = self._entries.setdefault(key, _Connection(endpoint, original))
        writer._session = _BorrowedSession(entry, endpoint, original, self._raw_store)
        return writer


class _BorrowedSession:
    def __init__(self, entry, endpoint, replacement, raw_store):
        self.entry = entry
        self.endpoint = endpoint
        self.replacement = replacement
        self.raw_store = raw_store
        self.acquired = False

    def __getattr__(self, name):
        return getattr(self.entry.session, name)

    async def connect(self):
        await self.entry.lock.acquire()
        self.acquired = True
        session = self.entry.session
        if self.entry.endpoint != self.endpoint or not session.authenticated:
            await self._replace()
            return
        try:
            errors = []

            def preserve(frame, _selected):
                if frame.wire_bytes is not None:
                    try:
                        self.raw_store(frame.wire_bytes)
                    except Exception as error:
                        errors.append(error)

            await session._exchange(
                GizwitsCommand.HEARTBEAT_REQUEST,
                expected={GizwitsCommand.HEARTBEAT_RESPONSE},
                frame_observer=preserve,
            )
            if errors:
                session.quarantine()
                raise errors[0]
            # Frames coalesced AFTER the heartbeat are still older than the next
            # read request. Preserve even partial/unknown bytes and reconnect;
            # they must never satisfy a fresh report-aware read.
            buffered = bytes(session._reader._buffer)
            if buffered:
                self.raw_store(buffered)
                await self._replace()
        except (ProtocolConnectionError, AuthenticationError):
            # Only the read-only reuse barrier may reconnect automatically.
            # Nothing from a send/readback is replayed here.
            await self._replace()

    async def _replace(self):
        await self.entry.session.disconnect()
        self.entry.session = self.replacement
        self.entry.endpoint = self.endpoint
        await self.entry.session.connect()

    async def authenticate(self):
        if self.entry.session.authenticated:
            return b""  # Callers never consume the passcode; no secret is cached here.
        return await self.entry.session.authenticate()

    async def disconnect(self):
        # A failed exchange already quarantines the session. Drain that close now;
        # healthy sessions remain owned by the enclosing maintenance request.
        try:
            if self.acquired and not self.entry.session.authenticated:
                await self.entry.session.disconnect()
        finally:
            if self.acquired:
                self.acquired = False
                self.entry.lock.release()

    def quarantine(self):
        if self.acquired:
            self.entry.session.quarantine()
