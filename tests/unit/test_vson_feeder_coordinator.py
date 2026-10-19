"""Fault injection for HA polling isolation, not claims about device firmware."""

import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import homeassistant  # noqa: F401
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.vson_feeder.api import VsonTransportError
from custom_components.vson_feeder.binary_sensor import VsonFeederBinarySensor
from custom_components.vson_feeder.button import VsonFeedNowButton
from custom_components.vson_feeder.coordinator import VsonFeederCoordinator
from custom_components.vson_feeder.sensor import VsonConnectionSensor, VsonFeederSensor


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.api = Mock(mac="aa:bb:cc:dd:ee:ff", device_id="fixture-device")
        self.api.status = AsyncMock(return_value={"equipmentState": "0", "isMaterial": "0"})
        self.api.history = AsyncMock(return_value=[{"time": "2026-10-08 12:00:00", "frequency": "1"}])
        self.api.settings = AsyncMock(return_value={"bley": [{"number": "0", "openTime": "12:00"}]})
        self.api.feed_once = AsyncMock()
        self.clock = Mock()
        self.clock.monotonic.return_value = 1000
        patcher = patch("custom_components.vson_feeder.coordinator.time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.coordinator = VsonFeederCoordinator(self.hass, self.api)
        self.online = VsonFeederBinarySensor(self.coordinator, "online")
        self.feed = VsonFeedNowButton(self.coordinator)
        self.schedule = VsonFeederSensor(self.coordinator, "native_schedule")
        self.history = VsonFeederSensor(self.coordinator, "last_feed_portions")
        self.cloud = VsonConnectionSensor(self.coordinator, "cloud_status")

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        self.api.feed_once.assert_not_awaited()

    async def refresh(self, seconds=0):
        self.clock.monotonic.return_value = 1000 + seconds
        self.coordinator.data = await self.coordinator._async_update_data()

    def fail(self, *sources):
        for source in sources:
            getattr(self.api, source).side_effect = VsonTransportError("secret must not be logged", kind="timeout")

    async def test_history_failure_preserves_other_reads_and_cached_value(self):
        await self.refresh()
        previous = self.coordinator.data["history"]
        success = self.coordinator.health["history"].last_success
        self.fail("history")
        with self.assertLogs("custom_components.vson_feeder.coordinator", level="WARNING") as logs:
            await self.refresh(30)
        self.assertNotIn("secret", str(logs.output))
        self.assertIs(previous, self.coordinator.data["history"])
        self.assertEqual(success, self.history.extra_state_attributes["last_success"])
        self.assertEqual("retrying", self.history.extra_state_attributes["query_state"])
        self.assertTrue(self.online.available)
        self.assertTrue(self.online.is_on)
        self.assertTrue(self.feed.available)
        self.assertTrue(self.schedule.available)
        self.assertEqual(1, self.history.native_value)
        self.assertEqual("재조회 중", self.cloud.native_value)
        self.assertEqual(2, self.api.settings.await_count)

    async def test_status_90_second_outage_is_not_device_offline(self):
        await self.refresh()
        self.fail("status")
        await self.refresh(30)
        self.assertTrue(self.online.available)
        self.assertFalse(self.feed.available)
        await self.refresh(89.99)
        self.assertTrue(self.online.available)
        await self.refresh(90)
        self.assertFalse(self.online.available)
        self.assertTrue(self.history.available)
        self.assertTrue(self.cloud.available)
        self.assertEqual("클라우드 통신 장애", self.cloud.native_value)
        self.assertEqual("fresh", self.coordinator.query_state("history"))
        self.api.status.side_effect = None
        with self.assertLogs("custom_components.vson_feeder.coordinator", level="INFO") as logs:
            await self.refresh(120)
        self.assertIn("read recovered", str(logs.output))
        self.assertEqual("정상", self.cloud.native_value)
        self.assertTrue(self.feed.available)
        self.assertEqual(0, self.coordinator.health["status"].failures)
        self.assertIsNotNone(self.coordinator.health["status"].recovered_at)

    async def test_actual_offline_response_is_distinct(self):
        self.api.status.return_value = {"equipmentState": "1"}
        await self.refresh()
        self.assertTrue(self.online.available)
        self.assertFalse(self.online.is_on)
        self.assertFalse(self.feed.available)
        self.assertEqual("급여기 오프라인", self.cloud.native_value)

    async def test_schedule_failure_does_not_erase_slots(self):
        await self.refresh()
        self.fail("settings")
        await self.refresh(100)
        self.assertEqual("1 slots", self.schedule.native_value)
        self.assertEqual("0", self.schedule.extra_state_attributes["slots"][0]["number"])
        self.assertEqual("error", self.schedule.extra_state_attributes["query_state"])
        self.assertEqual("일부 조회 장애", self.cloud.native_value)
        self.assertTrue(self.online.available)

    async def test_partial_startup_and_later_recovery(self):
        self.fail("status", "settings")
        await self.refresh()
        self.assertTrue(self.history.available)
        self.assertFalse(self.online.available)
        self.assertFalse(self.schedule.available)
        self.assertEqual("재조회 중", self.cloud.native_value)
        self.assertIsNotNone(self.online.device_info)
        await self.refresh(90)
        self.assertEqual("클라우드 통신 장애", self.cloud.native_value)
        self.api.status.side_effect = self.api.settings.side_effect = None
        await self.refresh(120)
        self.assertEqual("정상", self.cloud.native_value)

    async def test_all_initial_reads_fail_uses_ha_setup_retry(self):
        self.fail("status", "history", "settings")
        with self.assertRaises(UpdateFailed):
            await self.refresh()
        self.assertEqual(1, self.api.status.await_count)
        self.assertEqual(1, self.api.history.await_count)
        self.assertEqual(1, self.api.settings.await_count)

    async def test_all_reads_fail_after_success_preserves_history_and_recovers(self):
        await self.refresh()
        self.fail("status", "history", "settings")
        await self.refresh(100)
        self.assertFalse(self.online.available)
        self.assertTrue(self.schedule.available)
        self.assertTrue(self.history.available)
        self.assertEqual("클라우드 통신 장애", self.cloud.native_value)
        for source in ("status", "history", "settings"):
            getattr(self.api, source).side_effect = None
        await self.refresh(130)
        self.assertEqual("정상", self.cloud.native_value)

    async def test_without_native_id_does_not_query_schedule(self):
        self.api.device_id = None
        await self.refresh()
        self.api.settings.assert_not_awaited()
        self.assertEqual("ID 미등록", self.schedule.native_value)
        self.assertTrue(self.schedule.available)
        self.assertEqual("정상", self.cloud.native_value)

    async def test_slow_status_does_not_prevent_other_queries(self):
        ready = asyncio.Event()
        async def slow():
            await ready.wait()
            return {"equipmentState": "0"}
        self.api.status.side_effect = slow
        task = asyncio.create_task(self.refresh())
        try:
            for _ in range(10):
                await asyncio.sleep(0)
                if self.api.settings.await_count:
                    break
            self.api.history.assert_awaited_once()
            self.api.settings.assert_awaited_once()
        finally:
            ready.set()
            await task

    async def test_polling_calls_only_read_endpoints_once_per_round(self):
        await self.refresh()
        await self.refresh(30)
        self.assertEqual(["status", "history", "settings"] * 2,
                         [call[0] for call in self.api.mock_calls])
        self.assertEqual(30, self.coordinator.update_interval.total_seconds())


if __name__ == "__main__":
    unittest.main()
