import json
import os
from pathlib import Path

import pytest

from jebao_flow.operator_capture.model import (
    CaptureDirection,
    CaptureOrigin,
    ProtocolSummary,
)
from jebao_flow.operator_capture.store import (
    CaptureStoreError,
    DurableCaptureSession,
    list_capture_sessions,
    read_public_capture_events,
    verify_capture_session,
)
from jebao_flow.protocol.codec import GizwitsCommand, encode_frame


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "private-captures"
    root.mkdir(mode=0o700)
    return root


def _summary() -> ProtocolSummary:
    return ProtocolSummary.create(
        disposition="known",
        command=GizwitsCommand.HEARTBEAT_REQUEST,
        command_name="heartbeat_request",
        action=None,
        action_name=None,
        payload_length=0,
    )


def test_repeated_frames_share_idempotent_blob_but_keep_occurrences(tmp_path: Path) -> None:
    root = _root(tmp_path)
    frame = encode_frame(GizwitsCommand.HEARTBEAT_REQUEST)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="repeat-test",
    )
    session_id = session.session_id
    session.append_frame(
        frame,
        direction=CaptureDirection.CONTROLLER_TO_DEVICE,
        flow_id="flow-000001",
        summary=_summary(),
    )
    session.append_frame(
        frame,
        direction=CaptureDirection.CONTROLLER_TO_DEVICE,
        flow_id="flow-000001",
        summary=_summary(),
    )
    verified = session.seal(status="completed")

    assert verified.frame_event_count == 2
    assert verified.unique_blob_count == 1
    assert verified.orphan_blob_count == 0
    events = read_public_capture_events(root, session_id)
    frame_events = [event for event in events if event["kind"] == "frame"]
    assert frame_events[0]["payload"]["wire_sha256"] == frame_events[1]["payload"]["wire_sha256"]
    assert frame.hex() not in json.dumps(events)


def test_public_events_redact_credential_and_packet_blob_digests(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.IMPORTED_PCAP,
        source_label="redaction",
    )
    session_id = session.session_id
    login = encode_frame(GizwitsCommand.LOGIN_REQUEST, b"\x00\x06secret")
    session.append_frame(
        login,
        direction=CaptureDirection.CONTROLLER_TO_DEVICE,
        flow_id="flow-000001",
        summary=ProtocolSummary.create(
            disposition="known",
            command=GizwitsCommand.LOGIN_REQUEST,
            command_name="login_request",
            action=None,
            action_name=None,
            payload_length=8,
            semantic={"opaque_payload": True},
        ),
    )
    session.append_diagnostic(
        kind="pcap_packet",
        flow_id="flow-000001",
        reason="target_tcp_packet",
        raw_bytes=b"private-link-and-network-header",
    )
    session.seal(status="completed")

    events = read_public_capture_events(root, session_id)
    protected = [event["payload"] for event in events if "wire_length" in event["payload"]]
    assert len(protected) == 2
    assert all("wire_sha256" not in payload for payload in protected)
    assert all(payload["private_blob_retained"] is True for payload in protected)
    assert "secret" not in json.dumps(events)


def test_interrupted_session_preserves_verified_complete_prefix(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.IMPORTED_PCAP,
        source_label="crash-test",
    )
    session_id = session.session_id
    session.append_note(label="before-crash", detail="operator note")
    session.close_incomplete()
    events_path = root / session_id / "events.jsonl"
    with events_path.open("ab") as stream:
        stream.write(b'{"truncated":')
        stream.flush()
        os.fsync(stream.fileno())

    verified = verify_capture_session(root, session_id)

    assert verified.status == "incomplete"
    assert verified.valid_event_count == 2
    assert [row["kind"] for row in read_public_capture_events(root, session_id)] == [
        "session_started",
        "operator_note",
    ]


def test_capture_root_lease_rejects_second_writer(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="first",
    )
    try:
        with pytest.raises(CaptureStoreError, match="another capture process"):
            DurableCaptureSession(
                root,
                origin=CaptureOrigin.SIMULATOR,
                source_label="second",
            )
    finally:
        first.close_incomplete()


def test_generation_never_reuses_interrupted_number(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="first",
    )
    first.close_incomplete()
    second = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="second",
    )
    second.close_incomplete()

    assert first.generation == 1
    assert second.generation == 2
    assert [item.generation for item in list_capture_sessions(root)] == [1, 2]


@pytest.mark.parametrize(
    "detail",
    [
        # These are deliberately synthetic scanner-hygiene fixtures. Real identifiers must never
        # be split or assembled to evade repository privacy scans.
        "device " + ":".join(("aa", "bb", "cc", "dd", "ee", "ff")),
        "device 0123456789abcdef0123456789abcdef",
        "device at " + ".".join(("10", "0", "0", "15")),
    ],
)
def test_operator_note_rejects_private_identifiers(tmp_path: Path, detail: str) -> None:
    session = DurableCaptureSession(
        _root(tmp_path),
        origin=CaptureOrigin.SIMULATOR,
        source_label="privacy",
    )
    try:
        with pytest.raises(ValueError, match="private identifier"):
            session.append_note(label="device-check", detail=detail)
    finally:
        session.close_incomplete()


def test_verifier_rejects_tampered_complete_event(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="tamper",
    )
    session_id = session.session_id
    session.seal(status="completed")
    events = root / session_id / "events.jsonl"
    content = events.read_bytes().replace(b"session_started", b"session_changed", 1)
    events.write_bytes(content)

    with pytest.raises(CaptureStoreError, match="hash chain"):
        verify_capture_session(root, session_id)


def test_private_directory_mode_is_required(tmp_path: Path) -> None:
    root = tmp_path / "too-open"
    root.mkdir(mode=0o755)
    root.chmod(0o755)  # Exercise unsafe metadata even when the process umask is 077.

    with pytest.raises(CaptureStoreError, match="mode 0700"):
        DurableCaptureSession(
            root,
            origin=CaptureOrigin.SIMULATOR,
            source_label="mode",
        )


def test_interrupted_blob_link_residue_is_preserved_and_verified(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="link-residue",
    )
    session_id = session.session_id
    session.append_frame(
        encode_frame(GizwitsCommand.HEARTBEAT_REQUEST),
        direction=CaptureDirection.CONTROLLER_TO_DEVICE,
        flow_id="flow-000001",
        summary=_summary(),
    )
    session.close_incomplete()
    blobs = root / session_id / "blobs"
    installed = next(blobs.glob("*.blob"))
    os.link(installed, blobs / f".blob.{'a' * 24}.tmp")

    verified = verify_capture_session(root, session_id)

    assert verified.status == "incomplete"
    assert verified.temporary_blob_count == 1
    assert installed.stat().st_nlink == 2


def test_standalone_interrupted_blob_is_not_promoted_to_evidence(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="temp-residue",
    )
    session_id = session.session_id
    session.close_incomplete()
    temporary = root / session_id / "blobs" / f".blob.{'b' * 24}.tmp"
    temporary.write_bytes(b"incomplete private bytes")
    temporary.chmod(0o600)

    verified = verify_capture_session(root, session_id)

    assert verified.status == "incomplete"
    assert verified.unique_blob_count == 0
    assert verified.temporary_blob_count == 1


def test_sealed_capture_rejects_temporary_blob_residue(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="sealed-residue",
    )
    session_id = session.session_id
    session.seal(status="completed")
    temporary = root / session_id / "blobs" / f".blob.{'c' * 24}.tmp"
    temporary.write_bytes(b"unexpected")
    temporary.chmod(0o600)

    with pytest.raises(CaptureStoreError, match="unfinished blob"):
        verify_capture_session(root, session_id)


def test_unreferenced_blob_name_must_match_its_content(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = DurableCaptureSession(
        root,
        origin=CaptureOrigin.SIMULATOR,
        source_label="orphan-check",
    )
    session_id = session.session_id
    session.close_incomplete()
    orphan = root / session_id / "blobs" / f"{'0' * 64}.blob"
    orphan.write_bytes(b"not the named digest")
    orphan.chmod(0o600)

    with pytest.raises(CaptureStoreError, match="digest does not match"):
        verify_capture_session(root, session_id)


def test_operator_note_rejects_non_english_public_text(tmp_path: Path) -> None:
    session = DurableCaptureSession(
        _root(tmp_path),
        origin=CaptureOrigin.SIMULATOR,
        source_label="english-output",
    )
    try:
        with pytest.raises(ValueError, match="ASCII"):
            session.append_note(label="operator-note", detail="\u88c5\u7f6e")
    finally:
        session.close_incomplete()
