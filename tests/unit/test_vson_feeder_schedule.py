"""Reproduce reservation allocation races without sending feeder requests."""

import asyncio
import copy
import unittest

import homeassistant  # noqa: F401 - initialize HA voluptuous compatibility

from custom_components.vson_feeder.api import VsonApiError, VsonFeederApi


class FeederTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = VsonFeederApi(None, "FAKE", "FAKE")
        self.slots = {"0": {"number": "0", "openTime": "09:00"}}
        self.writes = []

        async def post(endpoint, fields, **kwargs):
            if endpoint == "queryBleyEquipment":
                snapshot = copy.deepcopy(list(self.slots.values()))
                await asyncio.sleep(0.002)
                return {"result": {"bley": snapshot}}
            self.writes.append((endpoint, dict(fields)))
            await asyncio.sleep(0.003)
            if endpoint == "updateBleySub":
                self.slots[fields["number"]] = dict(fields)
            elif endpoint == "deleteTimeBley":
                self.slots.pop(fields["number"], None)
            return {"retCode": "0"}

        self.api._post = post

    async def create(self, time="10:00", number=None):
        await self.api.upsert_schedule(
            number=number, open_time=time, week="0", frequency=1, enabled=True
        )

    async def test_parallel_creations_preserve_every_reservation(self):
        await asyncio.gather(*(self.create(f"{hour:02}:00") for hour in range(10, 16)))
        self.assertEqual(len(self.slots), 7)
        self.assertEqual(
            {x["openTime"] for x in self.slots.values()},
            {"09:00", *[f"{h:02}:00" for h in range(10, 16)]},
        )

    async def test_edit_delete_and_create_share_order(self):
        await asyncio.gather(
            self.create("10:00"),
            self.api.delete_schedule("0"),
            self.create("11:00", "1"),
            self.create("12:00"),
        )
        self.assertEqual(
            [x[0] for x in self.writes],
            ["updateBleySub", "deleteTimeBley", "updateBleySub", "updateBleySub"],
        )
        self.assertEqual(
            {k: v["openTime"] for k, v in self.slots.items()}, {"1": "11:00", "2": "12:00"}
        )

    async def test_uncertain_schedule_is_not_retried_and_lock_recovers(self):
        original = self.api._post
        count = 0

        async def fail(endpoint, fields, **kwargs):
            nonlocal count
            if endpoint == "updateBleySub":
                count += 1
                raise VsonApiError("ambiguous result")
            return await original(endpoint, fields, **kwargs)

        self.api._post = fail
        with self.assertRaises(VsonApiError):
            await self.create()
        self.assertEqual(count, 1)
        self.api._post = original
        await asyncio.wait_for(self.create("12:00"), 1)

    async def test_cancelled_allocation_releases_lock_without_write(self):
        started = asyncio.Event()
        original = self.api._post

        async def hold(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.api._post = hold
        task = asyncio.create_task(self.create())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.writes, [])
        self.api._post = original
        await asyncio.wait_for(self.create(), 1)

    async def test_ambiguous_feed_remains_single_request(self):
        calls = []

        async def fail(endpoint, fields, **kwargs):
            calls.append(endpoint)
            raise VsonApiError("timeout")

        self.api._post = fail
        for _ in range(2):
            with self.assertRaises(VsonApiError):
                await self.api.feed_once()
        self.assertEqual(calls, ["updateBleyFeed"])
