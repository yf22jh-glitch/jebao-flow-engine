"""Contract tests for the VSON feeder's one-shot cloud client."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from aiohttp import ClientConnectionError, ClientConnectorDNSError, ClientResponseError

import homeassistant  # noqa: F401 - initialize HA's voluptuous compatibility first

from custom_components.vson_feeder.api import (
    VsonApiError,
    VsonAuthError,
    VsonDeviceNotFound,
    VsonFeederApi,
    VsonTransportError,
    _LOGIN_FIELD_KEY,
    _app_encrypt,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def json(self, **_kwargs):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, *, data, headers):
        self.calls.append((url.rsplit("/", 1)[-1], data, headers))
        return FakeResponse(next(self.responses))


class VsonApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_error_categories_are_sanitized_and_not_retried(self):
        errors = [
            (TimeoutError("secret"), "timeout", None),
            (ClientConnectorDNSError(Mock(), OSError("secret")), "dns", None),
            (ClientConnectionError("secret"), "connection", None),
            (ClientResponseError(Mock(), (), status=503, message="secret"), "http", 503),
            (ValueError("secret"), "parse", None),
        ]
        for error, kind, status in errors:
            with self.subTest(kind=kind):
                session = FakeSession([error])
                api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff")
                with self.assertRaises(VsonTransportError) as raised:
                    await api.status()
                self.assertEqual(kind, raised.exception.kind)
                self.assertEqual(status, raised.exception.http_status)
                self.assertNotIn("secret", str(raised.exception))
                self.assertEqual(1, len(session.calls))

    async def test_timeout_feed_is_sent_once_and_cooldown_is_preserved(self):
        session = FakeSession([TimeoutError()])
        api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff")
        with self.assertRaises(VsonTransportError):
            await api.feed_once()
        with self.assertRaises(VsonApiError):
            await api.feed_once()
        self.assertEqual(1, len(session.calls))

    async def test_timeout_schedule_writes_are_not_retried(self):
        for delete in (True, False):
            session = FakeSession([TimeoutError()])
            api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff", "fixture")
            with self.assertRaises(VsonTransportError):
                if delete:
                    await api.delete_schedule("0")
                else:
                    await api.upsert_schedule(number="0", open_time="12:00", week="0", frequency=1, enabled=True)
            self.assertEqual(1, len(session.calls))

    async def test_malformed_read_payloads_are_not_success(self):
        for method, result in (("status", {}), ("history", {"recordsList": [None]}),
                               ("settings", {}), ("settings", {"bley": [None]})):
            with self.subTest(method=method, result=result):
                api = VsonFeederApi(FakeSession([{"retCode": "0", "result": result}]),
                                    "aa:bb:cc:dd:ee:ff", "fixture")
                with self.assertRaises(VsonApiError) as raised:
                    await getattr(api, method)()
                self.assertEqual("parse", raised.exception.kind)

    async def test_login_field_matches_independent_3des_fixture(self):
        # OpenSSL DES-EDE3-CBC with app's first 24 key bytes and IV 01234567.
        self.assertEqual(
            "Zp4N7J2VDLRQYg+R2ZlFZuUyCES6jjtd",
            _app_encrypt("fixture@example.com", _LOGIN_FIELD_KEY),
        )

    async def test_account_discovery_matches_mac_and_does_not_store_password(self):
        session = FakeSession(
            [
                {"retCode": "0", "result": {"tk": "temporary-token"}},
                {"retCode": "0", "result": {"equipmentList": [
                    {"mac": "11:22:33:44:55:66", "id": "other"}
                ], "totalPage": "2"}},
                {"retCode": "0", "result": {"equipmentList": [
                    {"mac": "aa:bb:cc:dd:ee:ff", "id": "internal-id"}
                ], "totalPage": "2"}},
            ]
        )
        api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(
            ("internal-id", "temporary-token"),
            await api.discover_from_account("fixture@example.com", "fixture-password"),
        )
        self.assertEqual(
            ["login", "queryEquipmentList", "queryEquipmentList"],
            [call[0] for call in session.calls],
        )
        self.assertEqual("102", session.calls[0][1]["accountType"])
        self.assertNotIn("fixture-password", str(session.calls))
        self.assertNotIn("tk", session.calls[0][2])
        self.assertIn("tk", session.calls[1][2])

    async def test_account_failure_and_missing_device_are_distinct(self):
        failed = VsonFeederApi(FakeSession([{"retCode": "1001"}]), "aa:bb:cc:dd:ee:ff")
        with self.assertRaises(VsonAuthError):
            await failed.discover_from_account("fixture@example.com", "fixture-password")

        missing = VsonFeederApi(
            FakeSession([{"retCode": "0", "result": {"tk": "token"}},
                         {"retCode": "0", "result": {"equipmentList": [], "totalPage": "1"}}]),
            "aa:bb:cc:dd:ee:ff",
        )
        with self.assertRaises(VsonDeviceNotFound):
            await missing.discover_from_account("fixture@example.com", "fixture-password")

    async def test_feed_is_one_post_and_never_immediately_repeated(self):
        session = FakeSession([{"retCode": "0"}])
        api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff")
        await api.feed_once()
        with self.assertRaises(VsonApiError):
            await api.feed_once()
        self.assertEqual(1, len(session.calls))
        self.assertEqual("updateBleyFeed", session.calls[0][0])
        self.assertEqual({"mac": "aa:bb:cc:dd:ee:ff"}, session.calls[0][1])

    async def test_rejected_feed_does_not_retry(self):
        session = FakeSession([{"retCode": "1001"}])
        api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff")
        with self.assertRaises(VsonApiError):
            await api.feed_once()
        self.assertEqual(1, len(session.calls))

    async def test_status_and_history_are_read_only_endpoints(self):
        session = FakeSession(
            [
                {"retCode": "0", "result": {"equipmentState": "0"}},
                {"retCode": "0", "result": {"recordsList": [{"frequency": "1"}]}},
            ]
        )
        api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff")
        self.assertEqual("0", (await api.status())["equipmentState"])
        self.assertEqual("1", (await api.history())[0]["frequency"])
        self.assertEqual(
            ["queryBleyHomepage", "queryBleyFeedRecords"],
            [call[0] for call in session.calls],
        )

    async def test_new_native_slot_reads_ids_then_writes_once(self):
        session = FakeSession(
            [
                {"retCode": "0", "result": {"bley": [{"number": "0"}, {"number": "2"}]}},
                {"retCode": "0"},
            ]
        )
        api = VsonFeederApi(session, "aa:bb:cc:dd:ee:ff", "opaque-device-id")
        await api.upsert_schedule(
            number=None, open_time="18:00", week="1&2&3&4&5&6&7", frequency=1,
            enabled=True,
        )
        self.assertEqual(
            ["queryBleyEquipment", "updateBleySub"],
            [call[0] for call in session.calls],
        )
        self.assertEqual(
            {"id": "opaque-device-id", "type": "0", "isOpen": "1", "frequency": "1",
             "openTime": "18:00", "week": "1&2&3&4&5&6&7", "number": "3"},
            session.calls[-1][1],
        )


if __name__ == "__main__":
    unittest.main()
