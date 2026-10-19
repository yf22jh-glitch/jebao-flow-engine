"""Independent, cached cloud reads; no retries of device write operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import VsonApiError, VsonFeederApi
from .const import DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)
STALE_AFTER = 90
ENDPOINTS = {"status": "queryBleyHomepage", "history": "queryBleyFeedRecords", "schedules": "queryBleyEquipment"}


@dataclass
class QueryHealth:
    """Timing and sanitized diagnostics for one read endpoint."""

    last_success: datetime | None = None
    success_clock: float | None = None
    failure_since: datetime | None = None
    failure_clock: float | None = None
    recovered_at: datetime | None = None
    failures: int = 0
    error_kind: str | None = None
    http_status: int | None = None
    elapsed_seconds: float = 0
    outage_logged: bool = False


class VsonFeederCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll each endpoint once per round, preserving other successful reads."""

    def __init__(self, hass: HomeAssistant, api: VsonFeederApi, entry: ConfigEntry | None = None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=POLL_INTERVAL,
            always_update=False,
        )
        self.api = api
        self.health = {key: QueryHealth() for key in ENDPOINTS}
        self._cache: dict[str, Any] = {"status": {}, "history": [], "schedules": None}

    def query_state(self, source: str) -> str:
        if source == "schedules" and not self.api.device_id:
            return "not_configured"
        health = self.health[source]
        if health.error_kind is None and health.last_success is not None:
            return "fresh" if self.source_recent(source) else "error"
        anchor = health.success_clock if health.success_clock is not None else health.failure_clock
        return "error" if anchor is not None and time.monotonic() - anchor >= STALE_AFTER else "retrying"

    def source_recent(self, source: str) -> bool:
        success = self.health[source].success_clock
        return success is not None and time.monotonic() - success < STALE_AFTER

    @property
    def schedule_error(self) -> str | None:
        return self.health["schedules"].error_kind

    @property
    def connection_state(self) -> str:
        status_state = self.query_state("status")
        if status_state == "error":
            return "클라우드 통신 장애"
        if status_state != "fresh":
            return "재조회 중"
        if str(self._cache["status"].get("equipmentState")) == "1":
            return "급여기 오프라인"
        others = [self.query_state(key) for key in ("history", "schedules")]
        if "error" in others:
            return "일부 조회 장애"
        return "재조회 중" if "retrying" in others else "정상"

    def query_attributes(self, source: str) -> dict[str, Any]:
        health = self.health[source]
        return {
            "query_state": self.query_state(source),
            "last_success": health.last_success,
            "failure_since": health.failure_since,
            "recovered_at": health.recovered_at,
            "consecutive_failures": health.failures,
            "error_kind": health.error_kind,
            "http_status": health.http_status,
            "endpoint": ENDPOINTS[source],
            "elapsed_seconds": health.elapsed_seconds,
        }

    async def _read(self, source: str) -> None:
        health = self.health[source]
        started = time.monotonic()
        try:
            if source == "schedules":
                value = (await self.api.settings())["bley"]
            else:
                value = await getattr(self.api, source)()
        except VsonApiError as error:
            now = dt_util.utcnow()
            health.elapsed_seconds = round(time.monotonic() - started, 3)
            health.failures += 1
            health.error_kind = error.kind
            health.http_status = error.http_status
            if health.failure_since is None:
                health.failure_since, health.failure_clock = now, time.monotonic()
                _LOGGER.warning("VSON read failed endpoint=%s kind=%s http=%s elapsed=%.3fs at=%s; read will retry",
                                ENDPOINTS[source], error.kind, error.http_status, health.elapsed_seconds, now.isoformat())
            if self.query_state(source) == "error" and not health.outage_logged:
                health.outage_logged = True
                _LOGGER.warning("VSON read stale endpoint=%s last_success=%s failure_since=%s attempts=%d",
                                ENDPOINTS[source], health.last_success, health.failure_since, health.failures)
            return
        now = dt_util.utcnow()
        health.elapsed_seconds = round(time.monotonic() - started, 3)
        if health.failure_since is not None:
            _LOGGER.info("VSON read recovered endpoint=%s failure_since=%s recovered_at=%s downtime=%.3fs attempts=%d",
                         ENDPOINTS[source], health.failure_since.isoformat(), now.isoformat(),
                         time.monotonic() - health.failure_clock, health.failures)
            health.recovered_at = now
        self._cache[source] = value
        health.last_success, health.success_clock = now, time.monotonic()
        health.failure_since = health.failure_clock = None
        health.failures = 0
        health.error_kind = health.http_status = None
        health.outage_logged = False

    async def _async_update_data(self) -> dict[str, Any]:
        sources = ["status", "history"] + (["schedules"] if self.api.device_id else [])
        await asyncio.gather(*(self._read(source) for source in sources))
        if not any(self.health[source].last_success for source in sources):
            raise UpdateFailed("VSON initial reads failed; retrying setup")
        return {**self._cache, "health": {key: self.query_attributes(key) for key in sources}}
