import json
from pathlib import Path

from jebao_flow import operator_capture_cli
from jebao_flow.operator_capture.install_prep import CaptureInstallMode, PreparedInstall


def test_catalog_cli_exposes_static_boundary_without_capture_root(capsys) -> None:
    result = operator_capture_cli.main(
        ["catalog", "--product-key", "50dbc92221fd4d33ae69a1fedd43b555"]
    )

    output = capsys.readouterr()
    assert result == 0
    catalog = json.loads(output.out)
    assert catalog["source_app"] == {
        "build": "341800000",
        "name": "Jebao Aqua",
        "version": "3.4.18",
    }
    assert catalog["semantic_boundary"] == (
        "static_app_and_vendor_declaration_not_firmware_acceptance"
    )
    assert len(catalog["products"]) == 1
    assert output.err == ""


def test_private_capture_root_is_required_for_verification(monkeypatch, capsys) -> None:
    monkeypatch.delenv("JEBAO_FLOW_OPERATOR_CAPTURE_ROOT", raising=False)

    result = operator_capture_cli.main(["verify", "capture-00000000000000000001-0123456789abcdef"])

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == ("jebao-flow-observe: JEBAO_FLOW_OPERATOR_CAPTURE_ROOT is required\n")


def test_ephemeral_capture_root_is_rejected_before_access(monkeypatch, capsys) -> None:
    monkeypatch.setenv("JEBAO_FLOW_OPERATOR_CAPTURE_ROOT", "/private/tmp/capture")

    result = operator_capture_cli.main(["list"])

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == (
        "jebao-flow-observe: operator capture root must not use an ephemeral temp path\n"
    )


def test_unexpected_private_exception_text_is_not_rendered(monkeypatch, capsys) -> None:
    private_text = (
        ".".join(("10", "0", "0", "7"))
        + " /private/capture "
        + ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
    )

    def fail(_args):
        raise OSError(private_text)

    monkeypatch.setattr(operator_capture_cli, "_run", fail)

    result = operator_capture_cli.main(["catalog"])

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "jebao-flow-observe: private_operation_error\n"
    assert private_text not in output.err


def test_prepare_install_reports_only_fresh_public_summary(monkeypatch, capsys) -> None:
    observed: dict[str, object] = {}

    def prepare(**kwargs):
        observed.update(kwargs)
        return PreparedInstall(
            generation="a" * 32,
            manifest_sha256="b" * 64,
            capture_mode=CaptureInstallMode.OFFLINE_PCAP,
            mirror_interface=None,
        )

    monkeypatch.setattr(operator_capture_cli, "prepare_fresh_install", prepare)

    result = operator_capture_cli.main(
        [
            "prepare-install",
            "--manifest",
            "/durable/private/install.json",
            "--capture-root",
            "/durable/private/captures",
        ]
    )

    output = capsys.readouterr()
    public = json.loads(output.out)
    assert result == 0
    assert public["previous_values_reused"] is False
    assert public["capture_mode"] == "offline_pcap"
    assert "/durable" not in output.out
    assert observed["manifest_path"] == Path("/durable/private/install.json")
    assert output.err == ""
