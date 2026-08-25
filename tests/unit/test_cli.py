import json

from jebao_flow import cli
from jebao_flow.protocol.errors import ProtocolConnectionError
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


class _FakeSession:
    def __init__(self, address: str, **kwargs: object) -> None:
        del kwargs
        self.address = address

    async def connect(self) -> None:
        if self.address == "offline.local":
            raise ProtocolConnectionError("offline")

    async def authenticate(self) -> bytes:
        return b"not-printed"

    async def read_raw_state(self) -> bytes:
        if self.address == "known.local":
            state = bytearray(LOCAL_WAVEMAKER_PRO.raw_status_size)
            state[:4] = bytes([1, 2, 55, 32])
            return bytes(state)
        return b"\x01\x02\x03"

    async def disconnect(self) -> None:
        pass


class _FakeDiscovery:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    async def discover(self, *, timeout_seconds: float) -> list[DiscoveredDevice]:
        del timeout_seconds
        return [
            DiscoveredDevice(
                address="known.local",
                device_id="private-not-printed",
                product_key=LOCAL_WAVEMAKER_PRO.product_key,
            )
        ]


def test_probe_outputs_raw_state_without_passcode(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "GizwitsSession", _FakeSession)

    result = cli.main(["probe", "pump.local", "--json"])
    output = capsys.readouterr().out

    assert result == 0
    assert json.loads(output) == [
        {
            "address": "pump.local",
            "success": True,
            "state_size": 3,
            "state_hex": "010203",
            "product_key": None,
            "schema_name": None,
            "decoded_state": None,
            "error": None,
        }
    ]
    assert "not-printed" not in output


def test_probe_returns_failure_when_any_device_is_offline(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "GizwitsSession", _FakeSession)

    result = cli.main(["probe", "pump.local", "offline.local", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output[0]["success"] is True
    assert output[1] == {
        "address": "offline.local",
        "success": False,
        "state_size": None,
        "state_hex": None,
        "product_key": None,
        "schema_name": None,
        "decoded_state": None,
        "error": "offline",
    }


def test_probe_can_decode_known_product_without_printing_device_id(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "GizwitsSession", _FakeSession)
    monkeypatch.setattr(cli, "GizwitsDiscovery", _FakeDiscovery)

    result = cli.main(["probe", "known.local", "--decode", "--json"])
    output_text = capsys.readouterr().out
    output = json.loads(output_text)

    assert result == 0
    assert output[0]["schema_name"] == LOCAL_WAVEMAKER_PRO.name
    assert output[0]["decoded_state"]["SwitchON"] is True
    assert output[0]["decoded_state"]["Flow"] == 55
    assert "private-not-printed" not in output_text
