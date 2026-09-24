"""Pure admission checks for the operator-only Pro linked Flow command."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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
