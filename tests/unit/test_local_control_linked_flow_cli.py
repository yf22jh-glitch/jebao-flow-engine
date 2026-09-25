"""Pure admission checks for the operator-only Pro linked Flow command."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from jebao_flow.local_control.lease import LocalControlLeaseBusyError

from jebao_flow import local_control_linked_flow_cli as cli


def _state(
    *,
    action: int = 3,
    linkage: str = "master",
    flow: int = 40,
    power: bool = True,
    timer: bool = True,
    image: str = "a" * 64,
    faults: tuple[str, ...] = (),
) -> cli.PumpSnapshot:
    return cli.PumpSnapshot(
        action=action,
        linkage=linkage,
        flow=flow,
        mode="constant",
        power=power,
        timer=timer,
        image_sha256=image,
        faults=faults,
    )


def test_pair_accepts_observed_master_and_async_report() -> None:
    master = _state()
    slave = _state(action=4, linkage="async_slave", flow=45, timer=False, image="b" * 64)
    cli._require_pair(
        master,
        slave,
        master_role="master",
        expected_flow=45,
        expected_images=("a" * 64, "b" * 64),
    )


def test_pair_accepts_direct_reply_after_release() -> None:
    master = _state(linkage="independent")
    slave = _state(linkage="async_slave", flow=44, timer=False)
    cli._require_pair(master, slave, master_role="independent", expected_flow=44)


@pytest.mark.parametrize(
    ("master", "slave"),
    [
        (_state(action=4), _state(linkage="async_slave", timer=False)),
        (_state(linkage="independent"), _state(linkage="async_slave", timer=False)),
        (_state(power=False), _state(linkage="async_slave", timer=False)),
        (_state(timer=False), _state(linkage="async_slave", timer=False)),
        (_state(), _state(linkage="sync_slave", timer=False)),
        (_state(), _state(linkage="async_slave", timer=True)),
        (_state(), _state(linkage="async_slave", timer=False, power=False)),
        (_state(faults=("Fault_no_liveload",)), _state(linkage="async_slave", timer=False)),
        (_state(), _state(linkage="async_slave", timer=False, faults=("Fault_no_liveload",))),
    ],
)
def test_pair_rejects_wrong_role_power_timer_or_fault(
    master: cli.PumpSnapshot, slave: cli.PumpSnapshot
) -> None:
    with pytest.raises(ValueError):
        cli._require_pair(master, slave, master_role="master")


def test_pair_rejects_flow_or_image_change() -> None:
    master = _state()
    slave = _state(linkage="async_slave", flow=44, timer=False)
    with pytest.raises(ValueError, match="Flow"):
        cli._require_pair(master, slave, master_role="master", expected_flow=45)
    with pytest.raises(ValueError, match="schedule image"):
        cli._require_pair(master, slave, master_role="master", expected_images=("a" * 64, "b" * 64))


def test_child_id_is_stable_and_bounded() -> None:
    assert cli._child_id("linked_20260925", "flow") == "linked_20260925_flow"
    with pytest.raises(ValueError, match="too long"):
        cli._child_id("x" * 128, "flow")


def test_main_hides_library_exception_text(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    async def fail(_args: object) -> None:
        raise RuntimeError("private-endpoint-marker")

    monkeypatch.setattr(cli, "_run", fail)
    monkeypatch.setattr(cli, "_parser", lambda: SimpleNamespace(parse_args=lambda: object()))
    assert cli.main() == 2
    output = capsys.readouterr().out
    assert "private-endpoint-marker" not in output
    assert '"error": "RuntimeError"' in output


@pytest.mark.asyncio
async def test_read_lock_collision_rechecks_pair_before_first_send(monkeypatch) -> None:
    calls = []

    class Coordinator:
        async def execute(self, _intent):
            calls.append("execute")
            if len([call for call in calls if call == "execute"]) == 1:
                raise LocalControlLeaseBusyError("device read lock")
            return "verified"

    class Journal:
        def load(self, _request_id):
            calls.append("journal")
            return None

    async def fresh(*_args):
        calls.append("fresh")
        return _state(linkage="independent"), _state(
            linkage="async_slave", flow=45, timer=False, image="b" * 64
        )

    async def no_wait(_seconds):
        calls.append("wait")

    monkeypatch.setattr(cli, "_fresh_pair", fresh)
    monkeypatch.setattr(cli.asyncio, "sleep", no_wait)
    result = await cli._execute_after_read_lock(
        Coordinator(),
        Journal(),
        SimpleNamespace(request_id="linked_20260925_flow"),
        config=object(),
        resolver=object(),
        master=object(),
        slave=object(),
        master_role="independent",
        expected_flow=45,
        expected_images=("a" * 64, "b" * 64),
    )
    assert result == "verified"
    assert calls == ["journal", "execute", "journal", "fresh", "wait", "journal", "execute"]


@pytest.mark.asyncio
async def test_existing_journal_after_lock_error_is_never_retried(monkeypatch) -> None:
    attempts = 0
    reads = 0

    class Coordinator:
        async def execute(self, _intent):
            nonlocal attempts
            attempts += 1
            raise LocalControlLeaseBusyError("device read lock")

    class Journal:
        def load(self, _request_id):
            nonlocal reads
            reads += 1
            return None if reads == 1 else object()

    async def forbidden(*_args):
        raise AssertionError("a journaled child must not be recaptured or resent")

    monkeypatch.setattr(cli, "_fresh_pair", forbidden)
    with pytest.raises(LocalControlLeaseBusyError):
        await cli._execute_after_read_lock(
            Coordinator(),
            Journal(),
            SimpleNamespace(request_id="linked_20260925_flow"),
            config=object(),
            resolver=object(),
            master=object(),
            slave=object(),
            master_role="independent",
            expected_flow=45,
            expected_images=("a" * 64, "b" * 64),
        )
    assert attempts == 1


@pytest.mark.asyncio
async def test_read_lock_wait_stops_if_pair_changed(monkeypatch) -> None:
    attempts = 0

    class Coordinator:
        async def execute(self, _intent):
            nonlocal attempts
            attempts += 1
            raise LocalControlLeaseBusyError("device read lock")

    class Journal:
        def load(self, _request_id):
            return None

    async def changed(*_args):
        return _state(linkage="master"), _state(
            linkage="async_slave", flow=45, timer=False, image="b" * 64
        )

    monkeypatch.setattr(cli, "_fresh_pair", changed)
    with pytest.raises(ValueError, match="required powered scheduled role"):
        await cli._execute_after_read_lock(
            Coordinator(),
            Journal(),
            SimpleNamespace(request_id="linked_20260925_flow"),
            config=object(),
            resolver=object(),
            master=object(),
            slave=object(),
            master_role="independent",
            expected_flow=45,
            expected_images=("a" * 64, "b" * 64),
        )
    assert attempts == 1
