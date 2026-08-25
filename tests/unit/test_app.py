import asyncio
from pathlib import Path

import pytest

from jebao_flow import app
from jebao_flow.config import load_config

ROOT = Path(__file__).resolve().parents[2]


class _ExpectedChildFailure(RuntimeError):
    pass


class _ScriptedChild:
    def __init__(
        self,
        *,
        started: asyncio.Event,
        peer_started: asyncio.Event,
        error: BaseException | None = None,
    ) -> None:
        self.peer_started = peer_started
        self.error = error
        self.started = started
        self.stop_observed = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def run(self, stop_event: asyncio.Event) -> None:
        self.started.set()
        await self.peer_started.wait()
        if self.error is not None:
            raise self.error
        try:
            await stop_event.wait()
            self.stop_observed.set()
        finally:
            self.cleaned.set()


@pytest.mark.parametrize("failing_child", ["mqtt", "observer"])
async def test_serve_propagates_child_failure_after_stopping_companion(
    monkeypatch: pytest.MonkeyPatch,
    failing_child: str,
) -> None:
    mqtt_started = asyncio.Event()
    observer_started = asyncio.Event()
    expected = _ExpectedChildFailure(f"{failing_child} failed")
    mqtt = _ScriptedChild(
        started=mqtt_started,
        peer_started=observer_started,
        error=expected if failing_child == "mqtt" else None,
    )
    observer = _ScriptedChild(
        started=observer_started,
        peer_started=mqtt_started,
        error=expected if failing_child == "observer" else None,
    )
    companion = observer if failing_child == "mqtt" else mqtt

    monkeypatch.setattr(app, "MqttAdapter", lambda *_: mqtt)
    monkeypatch.setattr(app, "ReadOnlyObserver", lambda *_: observer)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *_: None)

    with pytest.raises(_ExpectedChildFailure) as raised:
        await asyncio.wait_for(
            app.serve(load_config(ROOT / "config.example.yaml")),
            timeout=1,
        )

    assert raised.value is expected
    assert mqtt.started.is_set()
    assert observer.started.is_set()
    assert companion.stop_observed.is_set()
    assert companion.cleaned.is_set()
