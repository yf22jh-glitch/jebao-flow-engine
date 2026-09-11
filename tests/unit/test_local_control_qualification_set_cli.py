from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from jebao_flow import local_control_qualification_set_cli as cli
from jebao_flow.local_control.qualification_set import QualificationSetMergeResult


class _Merger:
    initialized_with: Path | None = None
    merge_args: tuple[object, object, object] | None = None

    def __init__(self, output: Path) -> None:
        type(self).initialized_with = output

    def merge(self, current, candidate, additions) -> QualificationSetMergeResult:
        type(self).merge_args = (current, candidate, additions)
        return QualificationSetMergeResult(
            output_sha256="9" * 64,
            binding_count=2,
            receipt_count=4,
            qualified_pair_count=2,
            added_pair_count=1,
        )


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        current=tmp_path / "current.json",
        candidate=tmp_path / "candidate.json",
        output=tmp_path / "output.json",
        manifest=tmp_path / "manifest.json",
        expect_add=[["flow_left", "manual_flow"]],
    )


def test_run_derives_private_surface_from_logical_member_and_prints_only_counts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    args = _args(tmp_path)
    current = (object(),)
    candidate = (object(),)
    manifest = object()
    additions = (object(),)
    loaded_paths: list[Path] = []
    requested: list[tuple[object, object]] = []
    activated: list[bool] = []

    def load_qualifications(path):
        loaded_paths.append(path)
        return current if path == args.current else candidate

    def derive(selected_manifest, selected):
        requested.append((selected_manifest, selected))
        return additions

    monkeypatch.setattr(cli, "load_current_qualifications", load_qualifications)
    monkeypatch.setattr(
        cli,
        "_activate_current_hardware_safety_volume_identity",
        lambda: activated.append(True),
    )
    monkeypatch.setattr(cli, "load_current_manifest", lambda path: manifest)
    monkeypatch.setattr(cli, "expected_additions_from_manifest", derive)
    monkeypatch.setattr(cli, "QualificationSetMerger", _Merger)

    assert cli._run(args) == 0

    assert loaded_paths == [args.current, args.candidate]
    assert activated == [True]
    assert requested == [(manifest, (("flow_left", "manual_flow"),))]
    assert _Merger.initialized_with == args.output
    assert _Merger.merge_args == (current, candidate, additions)
    payload = capsys.readouterr().out
    assert json.loads(payload) == {
        "added_pair_count": 1,
        "binding_count": 2,
        "output_sha256": "9" * 64,
        "private_values_exposed": False,
        "qualified_pair_count": 2,
        "receipt_count": 4,
    }
    assert "flow_left" not in payload
    assert str(args.output) not in payload
    assert "a" * 64 not in payload


def test_cli_has_no_raw_physical_identity_argument() -> None:
    parser = cli.build_parser()
    destinations = {action.dest for action in parser._actions}

    assert "expect_add" in destinations
    assert "physical_identity_key" not in destinations
    assert "physical_key" not in destinations
    help_text = parser.format_help()
    for private_option in ("--current", "--candidate", "--output", "--manifest", "--expect-add"):
        assert private_option not in help_text


def test_failure_output_is_generic_and_does_not_echo_private_values(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    private_key = "a" * 64

    def fail(_args) -> int:
        raise ValueError(f"private flow_left {private_key} {tmp_path}")

    monkeypatch.setattr(cli, "_run", fail)

    result = cli.main(
        [
            "--candidate",
            str(tmp_path / "candidate.json"),
            "--output",
            str(tmp_path / "output.json"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--expect-add",
            "flow_left",
            "manual_flow",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == (
        "jebao-flow-local-qualification-set: current_operation_failed_closed\n"
    )
    assert "flow_left" not in captured.err
    assert private_key not in captured.err
    assert str(tmp_path) not in captured.err


def test_parser_failure_does_not_echo_private_argv(capsys) -> None:
    private_key = "a" * 64

    with pytest.raises(SystemExit) as stopped:
        cli.main(["--physical-identity-key", private_key])

    captured = capsys.readouterr()
    assert stopped.value.code == 2
    assert captured.out == ""
    assert captured.err == (
        "jebao-flow-local-qualification-set: current_operation_failed_closed\n"
    )
    assert private_key not in captured.err


def test_current_hardware_safety_identity_file_is_activated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    identity_file = tmp_path / "volume-id"
    monkeypatch.setenv(cli.HARDWARE_SAFETY_VOLUME_ID_FILE_ENV, str(identity_file))
    monkeypatch.delenv(cli.HARDWARE_SAFETY_VOLUME_ID_ENV, raising=False)
    monkeypatch.setattr(
        cli,
        "read_current_hardware_safety_volume_id",
        lambda path: "b" * 32 if path == identity_file else "unexpected",
    )

    cli._activate_current_hardware_safety_volume_identity()

    assert os.environ[cli.HARDWARE_SAFETY_VOLUME_ID_ENV] == "b" * 32
