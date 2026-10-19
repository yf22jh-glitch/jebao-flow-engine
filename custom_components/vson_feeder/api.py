"""Small client for the WP8621N endpoints used by VSON SMART 3.2.

The endpoint names and form fields come from the app's Device8622Activity path.
No request is retried: a timeout on a feed command must not feed twice.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any

from aiohttp import (
    ClientConnectionError,
    ClientConnectorDNSError,
    ClientError,
    ClientResponseError,
    ClientSession,
)
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, modes

from .const import API_BASE

_LOGIN_URL = "https://apis.myvsoncloud.com:16082/vc-airmas-ws-1.0.2/api/m/login"
# DESedeKeySpec in VSON SMART 3.2 uses the first 24 bytes of these app constants.
# They are vendor protocol constants, not a user's account credentials.
_LOGIN_FIELD_KEY = b"cjm'|I-pE2[8=TM4[1PXRu,_"
_TOKEN_HEADER_KEY = b"*|rX<xO7<6B}7PA])?dN8fgX"
_APP_IV = b"01234567"
_MAX_DEVICE_PAGES = 20


class VsonApiError(Exception):
    """The cloud did not confirm a request."""

    def __init__(self, message: str, *, kind: str = "api", http_status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status


class VsonTransportError(VsonApiError):
    """The cloud response was unavailable or malformed."""


class VsonAuthError(VsonApiError):
    """The VSON account login was rejected."""


class VsonDeviceNotFound(VsonApiError):
    """The authenticated account has no feeder with the configured MAC."""


def _app_encrypt(value: str, key: bytes) -> str:
    """Match the app's 3DES-CBC/PKCS5Padding and unwrapped Base64 fields."""
    padder = padding.PKCS7(64).padder()
    padded = padder.update(value.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(TripleDES(key), modes.CBC(_APP_IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _mac_key(value: str) -> str:
    return re.sub(r"[^0-9a-f]", "", value.casefold())


class VsonFeederApi:
    """Form-POST API for one feeder."""

    def __init__(
        self,
        session: ClientSession,
        mac: str,
        device_id: str | None = None,
        token: str | None = None,
    ) -> None:
        self.session = session
        self.mac = mac
        self.device_id = device_id
        self.token = token
        self._feed_lock = asyncio.Lock()
        self._schedule_lock = asyncio.Lock()
        self._last_feed_request = float("-inf")

    async def _request(
        self,
        url: str,
        fields: dict[str, str],
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        endpoint = url.rsplit("/", 1)[-1]
        headers = {"ost": "2", "apv": "3.2"}
        if token:
            headers["tk"] = _app_encrypt(token, _TOKEN_HEADER_KEY)
        try:
            async with asyncio.timeout(12):
                async with self.session.post(
                    url,
                    data=fields,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
        except TimeoutError as error:
            raise VsonTransportError(f"{endpoint}: timeout", kind="timeout") from error
        except ClientConnectorDNSError as error:
            raise VsonTransportError(f"{endpoint}: DNS failure", kind="dns") from error
        except ClientResponseError as error:
            raise VsonTransportError(
                f"{endpoint}: HTTP {error.status}", kind="http", http_status=error.status
            ) from error
        except ClientConnectionError as error:
            raise VsonTransportError(
                f"{endpoint}: connection failure", kind="connection"
            ) from error
        except ValueError as error:
            raise VsonTransportError(f"{endpoint}: invalid JSON", kind="parse") from error
        except ClientError as error:
            raise VsonTransportError(f"{endpoint}: response failure", kind="transport") from error
        if not isinstance(payload, dict):
            raise VsonApiError(f"{endpoint}: invalid response object", kind="parse")
        if str(payload.get("retCode")) != "0":
            # Never copy arbitrary response text (or request credentials) to HA logs.
            raise VsonApiError(f"{endpoint}: cloud rejected request", kind="rejected")
        return payload

    async def _post(
        self, endpoint: str, fields: dict[str, str], *, authenticated: bool = False
    ) -> dict[str, Any]:
        return await self._request(
            API_BASE + endpoint, fields, token=self.token if authenticated else None
        )

    async def discover_from_account(self, username: str, password: str) -> tuple[str, str]:
        """Find this MAC's internal ID; return ID and token, never the password."""
        fields = {
            "name": _app_encrypt(username, _LOGIN_FIELD_KEY),
            "pwd": _app_encrypt(password, _LOGIN_FIELD_KEY),
            "accountType": "102" if "@" in username else "101",
        }
        try:
            login = await self._request(_LOGIN_URL, fields)
        except VsonTransportError:
            raise
        except VsonApiError as error:
            raise VsonAuthError("VSON account login was not confirmed") from error
        result = login.get("result")
        token = result.get("tk") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise VsonAuthError("VSON login returned no account token")

        target_mac = _mac_key(self.mac)
        for page in range(1, _MAX_DEVICE_PAGES + 1):
            payload = await self._request(
                API_BASE + "queryEquipmentList", {"page": str(page)}, token=token
            )
            listing = payload.get("result")
            if not isinstance(listing, dict) or not isinstance(listing.get("equipmentList"), list):
                raise VsonApiError("queryEquipmentList: missing equipment list")
            for device in listing["equipmentList"]:
                if not isinstance(device, dict):
                    continue
                device_mac = device.get("mac")
                device_id = device.get("id")
                if (
                    isinstance(device_mac, str)
                    and _mac_key(device_mac) == target_mac
                    and device_id is not None
                    and str(device_id).strip()
                ):
                    return str(device_id).strip(), token
            total_pages = listing.get("totalPage")
            if not str(total_pages).isdigit() or page >= int(total_pages):
                break
        raise VsonDeviceNotFound("Configured feeder is not in this VSON account")

    async def status(self) -> dict[str, Any]:
        payload = await self._post("queryBleyHomepage", {"mac": self.mac})
        result = payload.get("result")
        if not isinstance(result, dict) or str(result.get("equipmentState")) not in {"0", "1"}:
            raise VsonApiError("queryBleyHomepage: missing/invalid equipment state", kind="parse")
        return result

    async def history(self) -> list[dict[str, Any]]:
        payload = await self._post("queryBleyFeedRecords", {"page": "1", "mac": self.mac})
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("recordsList"), list):
            raise VsonApiError("queryBleyFeedRecords: missing records", kind="parse")
        if not all(isinstance(item, dict) for item in result["recordsList"]):
            raise VsonApiError("queryBleyFeedRecords: invalid records", kind="parse")
        return result["recordsList"]

    async def feed_once(self) -> None:
        if self._feed_lock.locked():
            raise VsonApiError("A feed request is already in progress")
        async with self._feed_lock:
            if time.monotonic() - self._last_feed_request < 10:
                raise VsonApiError("A feed request was already sent in the last 10 seconds")
            self._last_feed_request = time.monotonic()
            await self._post("updateBleyFeed", {"mac": self.mac})

    def _require_device_id(self) -> str:
        if not self.device_id:
            raise VsonApiError("VSON cloud device ID is required for native schedules")
        return self.device_id

    async def settings(self) -> dict[str, Any]:
        payload = await self._post(
            "queryBleyEquipment", {"id": self._require_device_id()}, authenticated=True
        )
        result = payload.get("result")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("bley"), list)
            or not all(isinstance(item, dict) for item in result["bley"])
        ):
            raise VsonApiError("queryBleyEquipment: missing/invalid schedules", kind="parse")
        return result

    async def upsert_schedule(
        self,
        *,
        number: str | None,
        open_time: str,
        week: str,
        frequency: int,
        enabled: bool,
    ) -> None:
        # Allocation and its write must share one lock with edits/deletions.
        # Do not retry an ambiguous write: the cloud may have accepted it.
        async with self._schedule_lock:
            await self._upsert_schedule(
                number=number, open_time=open_time, week=week,
                frequency=frequency, enabled=enabled,
            )

    async def _upsert_schedule(
        self,
        *,
        number: str | None,
        open_time: str,
        week: str,
        frequency: int,
        enabled: bool,
    ) -> None:
        fields = {
            "id": self._require_device_id(),
            "type": "1" if number is not None else "0",
            "isOpen": "1" if enabled else "0",
            "frequency": str(frequency),
            "openTime": open_time,
            "week": week,
        }
        if number is None:
            settings = await self.settings()
            slots = settings.get("bley") or []
            numbers = [
                int(item["number"])
                for item in slots
                if str(item.get("number", "")).isdigit()
            ]
            fields["number"] = str(max(numbers, default=-1) + 1)
        else:
            fields["number"] = number
        await self._post("updateBleySub", fields, authenticated=True)

    async def delete_schedule(self, number: str) -> None:
        async with self._schedule_lock:
            await self._post(
                "deleteTimeBley",
                {"id": self._require_device_id(), "number": number},
                authenticated=True,
            )
