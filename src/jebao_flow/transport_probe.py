"""Identity-bound, write-free diagnosis of Gizwits state reply versus report behaviour.

The probe sends one explicit state request on one fresh authenticated session per target. It
never accepts an action-0x04 report as the result of that request, but it preserves the first
such report seen while waiting so a timeout can be distinguished from a schema decode failure.
Authentication frames and private endpoint addresses are never stored.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jebao_flow.protocol.codec import decode_frame
from jebao_flow.protocol.errors import (
    AuthenticationError,
    ProtocolConnectionError,
    ProtocolDecodeError,
    ProtocolError,
    ProtocolTimeoutError,
    UnexpectedResponseError,
)
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.session import (
    STATE_REPLY_ACTION,
    STATE_REPORT_ACTION,
    RawStateCapture,
    StateFrameObserver,
)
from jebao_flow.read_only_collector import (
    CaptureTarget,
    CollectorError,
    DiscoveryFactory,
    resolve_exact_endpoint,
)

TRANSPORT_PROBE_SCHEMA_VERSION = 1
_ARTIFACT_PREFIX = "JTP"
_LINKAGE_VALUES = frozenset({"independent", "master", "sync_slave", "async_slave"})


class TransportProbeError(RuntimeError):
    """Privacy-safe probe or artifact failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProbeSession(Protocol):
    async def connect(self) -> None: ...

    async def authenticate(self) -> bytes: ...

    async def read_raw_state_capture(
        self,
        *,
        accept_reports: bool = False,
        state_frame_observer: StateFrameObserver | None = None,
    ) -> RawStateCapture: ...

    async def disconnect(self) -> None: ...

    def quarantine(self) -> None: ...


ProbeSessionFactory = Callable[[str], ProbeSession]
UtcClock = Callable[[], datetime]
MonotonicClock = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ProbeReference:
    artifact_id: str
    plan_sha256: str
    expected_identity_bindings_sha256: tuple[str, str]
    expected_linkages: tuple[str, str]
    directory: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProbePublicMetadata:
    artifact_id: str
    artifact_sha256: str
    plan_sha256: str
    status: str
    q2_verdict: str
    target_outcomes: tuple[str, str]
    target_linkage_contexts: tuple[str, str]
    target_report_frame_counts: tuple[int, int]
    target_reply_frame_counts: tuple[int, int]
    expected_identity_bindings_sha256: tuple[str, str]
    utc_started: str
    utc_completed: str


@dataclass(frozen=True, slots=True)
class _ObservedFrame:
    action: int
    selected: bool
    wire_frame: bytes = field(repr=False)


class _StateFrameRecorder:
    """Keep one rejected report and every selected reply without blocking the socket on fsync."""

    def __init__(self) -> None:
        self.report_count = 0
        self.reply_count = 0
        self._first_report: _ObservedFrame | None = None
        self._selected_replies: list[_ObservedFrame] = []

    def __call__(self, capture: RawStateCapture, selected: bool) -> None:
        observed = _ObservedFrame(
            action=capture.action,
            selected=selected,
            wire_frame=bytes(capture.wire_frame),
        )
        if capture.action == STATE_REPORT_ACTION:
            self.report_count += 1
            if self._first_report is None:
                self._first_report = observed
        elif capture.action == STATE_REPLY_ACTION:
            self.reply_count += 1
            if selected:
                self._selected_replies.append(observed)

    def ensure_selected_reply(self, capture: RawStateCapture) -> None:
        digest = hashlib.sha256(capture.wire_frame).digest()
        if any(
            hashlib.sha256(item.wire_frame).digest() == digest
            for item in self._selected_replies
        ):
            return
        self(capture, True)

    @property
    def preserved(self) -> tuple[_ObservedFrame, ...]:
        return tuple(
            item
            for item in (self._first_report, *self._selected_replies)
            if item is not None
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _valid_commit(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TransportProbeError("probe_clock_not_timezone_aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise OSError("private artifact metadata invalid")
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise TransportProbeError("probe_artifact_write_failed") from error


def _validate_private_root(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as error:
        raise TransportProbeError("probe_artifact_root_invalid") from error
    if (
        not resolved.is_dir()
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise TransportProbeError("probe_artifact_root_invalid")
    return resolved


class TransportProbeStore:
    """Owner-only, append-by-file artifact store with a plan committed before network I/O."""

    def __init__(self, root: Path) -> None:
        self.root = _validate_private_root(root)

    def _validate_reference(self, reference: ProbeReference) -> None:
        if (
            reference.directory.parent != self.root
            or reference.directory.name != reference.artifact_id
            or not reference.artifact_id.startswith(f"{_ARTIFACT_PREFIX}-")
        ):
            raise TransportProbeError("probe_artifact_reference_invalid")
        try:
            plan_path = reference.directory / "plan.json"
            plan_metadata = plan_path.lstat()
            plan_payload = plan_path.read_bytes()
            plan = json.loads(plan_payload)
        except OSError as error:
            raise TransportProbeError("probe_artifact_reference_invalid") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransportProbeError("probe_artifact_reference_invalid") from error
        ordered_targets = plan.get("ordered_targets") if isinstance(plan, dict) else None
        if (
            not stat.S_ISREG(plan_metadata.st_mode)
            or stat.S_ISLNK(plan_metadata.st_mode)
            or plan_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(plan_metadata.st_mode) != 0o600
            or plan_metadata.st_nlink != 1
            or not _valid_sha256(reference.plan_sha256)
            or hashlib.sha256(plan_payload).hexdigest() != reference.plan_sha256
            or not isinstance(plan, dict)
            or plan.get("artifact_id") != reference.artifact_id
            or not isinstance(ordered_targets, list)
            or len(ordered_targets) != 2
            or tuple(
                item.get("expected_identity_binding_sha256")
                for item in ordered_targets
                if isinstance(item, dict)
            )
            != reference.expected_identity_bindings_sha256
            or tuple(
                item.get("expected_linkage")
                for item in ordered_targets
                if isinstance(item, dict)
            )
            != reference.expected_linkages
        ):
            raise TransportProbeError("probe_artifact_reference_invalid")
        try:
            metadata = reference.directory.lstat()
        except OSError as error:
            raise TransportProbeError("probe_artifact_reference_invalid") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise TransportProbeError("probe_artifact_reference_invalid")

    def prepare(
        self,
        targets: tuple[CaptureTarget, CaptureTarget],
        *,
        commit_sha: str,
        collector_source_digest_sha256: str,
        probe_source_digest_sha256: str,
        response_timeout_seconds: float,
        expected_linkages: tuple[str, str],
    ) -> ProbeReference:
        if (
            not _valid_commit(commit_sha)
            or not _valid_sha256(collector_source_digest_sha256)
            or not _valid_sha256(probe_source_digest_sha256)
            or response_timeout_seconds <= 0
            or not math.isfinite(response_timeout_seconds)
            or targets[0].identity_binding_sha256 == targets[1].identity_binding_sha256
            or len(expected_linkages) != 2
            or any(linkage not in _LINKAGE_VALUES for linkage in expected_linkages)
        ):
            raise TransportProbeError("probe_plan_invalid")
        artifact_id = f"{_ARTIFACT_PREFIX}-{secrets.token_hex(16)}"
        directory = self.root / artifact_id
        try:
            directory.mkdir(mode=0o700)
            metadata = directory.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise OSError("private series directory metadata invalid")
        except OSError as error:
            raise TransportProbeError("probe_artifact_directory_invalid") from error
        plan = {
            "schema_version": TRANSPORT_PROBE_SCHEMA_VERSION,
            "kind": "strict_state_transport_probe_plan",
            "artifact_id": artifact_id,
            "commit_sha": commit_sha,
            "collector_source_digest_sha256": collector_source_digest_sha256,
            "probe_source_digest_sha256": probe_source_digest_sha256,
            "accept_reports": False,
            "reads_per_session": 1,
            "response_timeout_seconds": response_timeout_seconds,
            "ordered_targets": [
                {
                    "logical_id": target.logical_id,
                    "product_key": target.product_key,
                    "expected_identity_binding_sha256": target.identity_binding_sha256,
                    "config_fingerprint_sha256": target.config_fingerprint,
                    "expected_linkage": expected_linkage,
                }
                for target, expected_linkage in zip(
                    targets,
                    expected_linkages,
                    strict=True,
                )
            ],
        }
        payload = _canonical_json(plan)
        _write_private_exclusive(directory / "plan.json", payload)
        _fsync_directory(directory)
        _fsync_directory(self.root)
        return ProbeReference(
            artifact_id=artifact_id,
            plan_sha256=hashlib.sha256(payload).hexdigest(),
            expected_identity_bindings_sha256=tuple(
                target.identity_binding_sha256 for target in targets
            ),
            expected_linkages=expected_linkages,
            directory=directory,
        )

    def commit(
        self,
        reference: ProbeReference,
        *,
        started_at: datetime,
        completed_at: datetime,
        targets: tuple[CaptureTarget, CaptureTarget],
        results: tuple[dict[str, Any], dict[str, Any]],
    ) -> ProbePublicMetadata:
        self._validate_reference(reference)
        if (
            tuple(target.identity_binding_sha256 for target in targets)
            != reference.expected_identity_bindings_sha256
        ):
            raise TransportProbeError("probe_artifact_reference_invalid")
        result_document = {
            "schema_version": TRANSPORT_PROBE_SCHEMA_VERSION,
            "kind": "strict_state_transport_probe_result",
            "artifact_id": reference.artifact_id,
            "plan_sha256": reference.plan_sha256,
            "q2_verdict": "UNKNOWN",
            "utc_started": _utc_text(started_at),
            "utc_completed": _utc_text(completed_at),
            "results": list(results),
        }
        result_payload = _canonical_json(result_document)
        artifact_sha256 = hashlib.sha256(result_payload).hexdigest()
        _write_private_exclusive(reference.directory / "result.json", result_payload)
        _fsync_directory(reference.directory)
        marker_payload = _canonical_json(
            {
                "schema_version": TRANSPORT_PROBE_SCHEMA_VERSION,
                "kind": "strict_state_transport_probe_commit",
                "artifact_id": reference.artifact_id,
                "plan_sha256": reference.plan_sha256,
                "artifact_sha256": artifact_sha256,
            }
        )
        _write_private_exclusive(reference.directory / "result.commit.json", marker_payload)
        _fsync_directory(reference.directory)
        _fsync_directory(self.root)
        outcomes = tuple(str(item["outcome"]) for item in results)
        linkage_contexts = tuple(str(item["linkage_context"]) for item in results)
        report_counts = tuple(int(item["report_frame_count"]) for item in results)
        reply_counts = tuple(int(item["reply_frame_count"]) for item in results)
        identity_ok = all(item.get("identity_invariant") is True for item in results)
        observed_any = any(
            report_count + reply_count > 0
            for report_count, reply_count in zip(report_counts, reply_counts, strict=True)
        )
        linkage_ok = all(
            context == "expected_linkage_observed" for context in linkage_contexts
        )
        if not identity_ok:
            status = "probe_completed_identity_invalid"
        elif not observed_any:
            status = "probe_completed_without_state_frame"
        elif not linkage_ok:
            status = "probe_completed_linkage_context_invalid"
        else:
            status = "probe_completed_context_valid"
        return ProbePublicMetadata(
            artifact_id=reference.artifact_id,
            artifact_sha256=artifact_sha256,
            plan_sha256=reference.plan_sha256,
            status=status,
            q2_verdict="UNKNOWN",
            target_outcomes=(outcomes[0], outcomes[1]),
            target_linkage_contexts=(linkage_contexts[0], linkage_contexts[1]),
            target_report_frame_counts=(report_counts[0], report_counts[1]),
            target_reply_frame_counts=(reply_counts[0], reply_counts[1]),
            expected_identity_bindings_sha256=(
                targets[0].identity_binding_sha256,
                targets[1].identity_binding_sha256,
            ),
            utc_started=_utc_text(started_at),
            utc_completed=_utc_text(completed_at),
        )


def _failure_class(error: BaseException) -> str:
    for error_type in (
        ProtocolTimeoutError,
        ProtocolConnectionError,
        AuthenticationError,
        UnexpectedResponseError,
        ProtocolDecodeError,
        ProtocolError,
        CollectorError,
        TransportProbeError,
    ):
        if isinstance(error, error_type):
            return error_type.__name__
    if isinstance(error, TimeoutError):
        return "TimeoutError"
    if isinstance(error, OSError):
        return "OSError"
    if isinstance(error, (KeyError, TypeError, ValueError)):
        return type(error).__name__
    return "Exception"


def _frame_document(
    frame: _ObservedFrame,
    *,
    product_key: str,
    expected_linkage: str,
) -> dict[str, Any]:
    decode_status = "not_attempted"
    observed_linkage: str | None = None
    try:
        capture_action = frame.action
        decoded = decode_frame(frame.wire_frame)
        if not decoded.payload or decoded.payload[0] != capture_action:
            raise ProtocolDecodeError("state action does not match preserved wire frame")
        raw_status = decoded.payload[1:]
        schema = get_product_schema(product_key)
        values = schema.decode_status(raw_status)
        linkage_value = values.get(schema.linkage_attribute)
        if isinstance(linkage_value, str):
            observed_linkage = linkage_value
        decode_schedule(
            product_key,
            raw_status,
            enabled=bool(values.get(schema.timer_attribute, False)),
        )
        decode_status = "decoded"
    except (ProtocolError, KeyError, TypeError, ValueError):
        decode_status = "decode_rejected"
    return {
        "transport_action": frame.action,
        "selected_by_strict_read": frame.selected,
        "wire_frame_length": len(frame.wire_frame),
        "wire_frame_sha256": hashlib.sha256(frame.wire_frame).hexdigest(),
        "wire_frame_base64": base64.b64encode(frame.wire_frame).decode("ascii"),
        "decode_status": decode_status,
        "expected_linkage": expected_linkage,
        "observed_linkage": observed_linkage,
        "linkage_matches_expected": observed_linkage == expected_linkage,
    }


def _classify_outcome(
    recorder: _StateFrameRecorder,
    *,
    returned_capture: RawStateCapture | None,
    error: BaseException | None,
) -> str:
    if returned_capture is not None:
        return "explicit_reply_observed"
    if recorder.reply_count:
        return "explicit_reply_observed_before_failure"
    if isinstance(error, ProtocolTimeoutError) and recorder.report_count:
        return "report_observed_explicit_timeout"
    if isinstance(error, ProtocolTimeoutError):
        return "explicit_timeout_without_recognised_state_frame"
    return "state_read_failed"


async def _probe_target(
    target: CaptureTarget,
    *,
    expected_linkage: str,
    discovery_factory: DiscoveryFactory,
    session_factory: ProbeSessionFactory,
    discovery_timeout_seconds: float,
    utc_clock: UtcClock,
    monotonic_clock: MonotonicClock,
) -> dict[str, Any]:
    started_at = utc_clock()
    monotonic_started_ns = monotonic_clock()
    endpoint_address: str | None = None
    binding_before: str | None = None
    binding_after: str | None = None
    session: ProbeSession | None = None
    recorder = _StateFrameRecorder()
    returned_capture: RawStateCapture | None = None
    failure: BaseException | None = None
    failure_phase: str | None = None
    try:
        failure_phase = "identity_before"
        endpoint = resolve_exact_endpoint(
            target,
            await discovery_factory().discover(timeout_seconds=discovery_timeout_seconds),
        )
        endpoint_address = endpoint.address
        binding_before = endpoint.identity_binding_sha256
        session = session_factory(endpoint.address)
        failure_phase = "connect"
        await session.connect()
        failure_phase = "authenticate"
        await session.authenticate()
        failure_phase = "strict_state_read"
        returned_capture = await session.read_raw_state_capture(
            accept_reports=False,
            state_frame_observer=recorder,
        )
        recorder.ensure_selected_reply(returned_capture)
    except asyncio.CancelledError:
        if session is not None:
            session.quarantine()
        raise
    except Exception as error:
        failure = error
    finally:
        if session is not None:
            try:
                await session.disconnect()
            except asyncio.CancelledError:
                session.quarantine()
                raise
            except Exception as error:
                session.quarantine()
                if failure is None:
                    failure = error
                    failure_phase = "disconnect"
    try:
        failure_phase_after = "identity_after"
        endpoint_after = resolve_exact_endpoint(
            target,
            await discovery_factory().discover(timeout_seconds=discovery_timeout_seconds),
        )
        binding_after = endpoint_after.identity_binding_sha256
        endpoint_stable = (
            endpoint_address is not None and endpoint_after.address == endpoint_address
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        endpoint_stable = False
        if failure is None:
            failure = error
            failure_phase = failure_phase_after

    completed_at = utc_clock()
    monotonic_completed_ns = monotonic_clock()
    identity_invariant = (
        binding_before == target.identity_binding_sha256
        and binding_after == target.identity_binding_sha256
        and endpoint_stable
    )
    frame_documents = [
        _frame_document(
            frame,
            product_key=target.product_key,
            expected_linkage=expected_linkage,
        )
        for frame in recorder.preserved
    ]
    observed_linkage_matches = [
        item["linkage_matches_expected"]
        for item in frame_documents
        if item["observed_linkage"] is not None
    ]
    if observed_linkage_matches and all(observed_linkage_matches):
        linkage_context = "expected_linkage_observed"
    elif observed_linkage_matches:
        linkage_context = "linkage_mismatch_observed"
    else:
        linkage_context = "linkage_unavailable"
    return {
        "logical_id": target.logical_id,
        "product_key": target.product_key,
        "expected_identity_binding_sha256": target.identity_binding_sha256,
        "observed_identity_binding_sha256_before": binding_before,
        "observed_identity_binding_sha256_after": binding_after,
        "identity_invariant": identity_invariant,
        "utc_started": _utc_text(started_at),
        "utc_completed": _utc_text(completed_at),
        "monotonic_started_ns": monotonic_started_ns,
        "monotonic_completed_ns": monotonic_completed_ns,
        "strict_read_accept_reports": False,
        "expected_linkage": expected_linkage,
        "linkage_context": linkage_context,
        "report_frame_count": recorder.report_count,
        "reply_frame_count": recorder.reply_count,
        "preserved_frames": frame_documents,
        "outcome": _classify_outcome(
            recorder,
            returned_capture=returned_capture,
            error=failure,
        ),
        "failure_class": _failure_class(failure) if failure is not None else None,
        "failure_phase": failure_phase if failure is not None else None,
    }


async def run_transport_probe(
    reference: ProbeReference,
    store: TransportProbeStore,
    targets: tuple[CaptureTarget, CaptureTarget],
    *,
    discovery_factory: DiscoveryFactory,
    session_factory: ProbeSessionFactory,
    discovery_timeout_seconds: float,
    utc_clock: UtcClock = lambda: datetime.now(UTC),
    monotonic_clock: MonotonicClock = time.monotonic_ns,
) -> ProbePublicMetadata:
    """Probe two logical targets sequentially and commit every diagnostic result privately."""

    if discovery_timeout_seconds <= 0:
        raise TransportProbeError("probe_discovery_timeout_invalid")
    if (
        tuple(target.identity_binding_sha256 for target in targets)
        != reference.expected_identity_bindings_sha256
    ):
        raise TransportProbeError("probe_plan_target_mismatch")
    started_at = utc_clock()
    results = tuple(
        [
            await _probe_target(
                target,
                expected_linkage=expected_linkage,
                discovery_factory=discovery_factory,
                session_factory=session_factory,
                discovery_timeout_seconds=discovery_timeout_seconds,
                utc_clock=utc_clock,
                monotonic_clock=monotonic_clock,
            )
            for target, expected_linkage in zip(
                targets,
                reference.expected_linkages,
                strict=True,
            )
        ]
    )
    completed_at = utc_clock()
    return store.commit(
        reference,
        started_at=started_at,
        completed_at=completed_at,
        targets=targets,
        results=(results[0], results[1]),
    )


__all__ = [
    "ProbePublicMetadata",
    "ProbeReference",
    "ProbeSession",
    "TransportProbeError",
    "TransportProbeStore",
    "run_transport_probe",
]
