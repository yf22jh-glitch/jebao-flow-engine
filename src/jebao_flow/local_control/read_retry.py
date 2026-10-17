"""Retry read-only device I/O, without ever replaying a control command."""

from __future__ import annotations

import asyncio
import errno
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from jebao_flow.local_control.lease import LocalControlLeaseBusyError
from jebao_flow.local_control.resolver import CurrentEndpointResolutionError
from jebao_flow.protocol.errors import AuthenticationError, ProtocolConnectionError

T = TypeVar("T")


class ReadUnavailableError(CurrentEndpointResolutionError):
    """An exact device identity did not respond to discovery; this is retryable."""


class ReadRetryInterrupted(RuntimeError):
    """The operator paused read-only work; this never cancels a control send."""


def retryable_read_error(error: Exception) -> bool:
    if isinstance(error, (
        ProtocolConnectionError, AuthenticationError, ConnectionError, TimeoutError,
        LocalControlLeaseBusyError, ReadUnavailableError,
    )):
        return True
    return isinstance(error, OSError) and error.errno in {
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT,
        errno.ECONNRESET, errno.ECONNREFUSED, errno.EPIPE,
    }


class ReadRetry:
    def __init__(self) -> None:
        self.status: dict | None = None
        self.last_identity_reply_at: str | None = None
        self.stop_requested: Callable[[], bool] = lambda: False
        self.status_sink: Callable[[dict | None], None] = lambda _status: None
        self._reading = False
        self._interrupted = False

    def interrupt(self) -> None:
        if self._reading:
            self._interrupted = True

    def _check_stop(self) -> None:
        if self.stop_requested():
            raise asyncio.CancelledError("read-only retries stopped by shutdown")
        if self._interrupted:
            raise ReadRetryInterrupted("read-only retries paused by operator")

    def identity_responded(self) -> None:
        self.last_identity_reply_at = datetime.now(UTC).isoformat()

    async def wait(self, seconds: float) -> None:
        while seconds > 0:
            self._check_stop()
            delay = min(seconds, 0.25)
            await asyncio.sleep(delay)
            seconds -= delay
        self._check_stop()

    async def run(self, operation: Callable[[], Awaitable[T]], *, stage: str) -> T:
        attempt = 0
        self._reading = True
        self._interrupted = False
        try:
            while True:
                self._check_stop()
                try:
                    result = await operation()
                except Exception as error:
                    if not retryable_read_error(error):
                        raise
                    attempt += 1
                    delay = (1, 2, 5)[min(attempt - 1, 2)]
                    self.status = {
                        "state": "communication_error" if isinstance(error, ReadUnavailableError)
                        else "state_retry",
                        "stage": stage, "attempt": attempt, "retry_after_seconds": delay,
                        "error_type": type(error).__name__,
                        "last_identity_reply_at": self.last_identity_reply_at,
                        "at": datetime.now(UTC).isoformat(), "device_writes": 0,
                    }
                    self.status_sink(self.status)
                    print(json.dumps({"outcome": "read_retry", **self.status}), flush=True)
                    await self.wait(delay)
                else:
                    self._check_stop()
                    self.status_sink(None)
                    self.status = None
                    return result
        finally:
            self._reading = False
            if self.status is not None:
                self.status = None
                self.status_sink(None)
