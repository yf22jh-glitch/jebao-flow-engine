import json

from jebao_flow import cli
from jebao_flow.protocol.errors import ProtocolConnectionError


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
        return b"\x01\x02\x03"

    async def disconnect(self) -> None:
        pass


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
        "error": "offline",
    }
