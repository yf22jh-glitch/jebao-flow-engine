"""Attended, one-shot authority issuance for the supported exact-restore path."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from jebao_flow.exact_restore import (
    ExactRestoreAuthority,
    ExactRestoreAuthorityScope,
    ExactRestoreCycle,
    ExactRestorePhase,
    ExactRestoreRecord,
    system_boot_identity_sha256,
    system_boottime_ns,
)

_AUTHORITY_LIFETIME = timedelta(minutes=5)
_MAX_CHALLENGE_AGE = timedelta(minutes=5)
_MAX_CONFIRMATION_BYTES = 160


class AttendedAuthorityErrorCode(StrEnum):
    TTY = "tty"
    DECLINED = "declined"
    RECORD = "record"


class AttendedAuthorityError(RuntimeError):
    def __init__(self, code: AttendedAuthorityErrorCode) -> None:
        self.code = code
        super().__init__(f"attended exact-restore authority failed: {code.value}")


class ConfirmationChannel(Protocol):
    def write(self, value: str) -> None: ...

    def read_line(self, max_bytes: int) -> str: ...


class ArmController(Protocol):
    def arm(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord: ...


class ReauthorizeController(Protocol):
    def reauthorize(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord: ...


class RecoverController(Protocol):
    def recover(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord: ...


ChannelFactory = Callable[[], AbstractContextManager[ConfirmationChannel]]
WallClock = Callable[[], datetime]
MonotonicClock = Callable[[], int]
BootIdentity = Callable[[], str]
Entropy = Callable[[int], bytes]


class _ControllingTtyChannel:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def write(self, value: str) -> None:
        payload = value.encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(self._descriptor, view)
            if written <= 0:
                raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY)
            view = view[written:]

    def read_line(self, max_bytes: int) -> str:
        collected = bytearray()
        while len(collected) <= max_bytes:
            chunk = os.read(self._descriptor, 1)
            if not chunk:
                raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY)
            if chunk in {b"\n", b"\r"}:
                break
            collected.extend(chunk)
        if len(collected) > max_bytes:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY)
        try:
            return collected.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY) from error


@contextmanager
def controlling_tty_channel() -> ConfirmationChannel:
    """Open the controlling TTY directly; stdin and environment are never authority sources."""

    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOCTTY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open("/dev/tty", flags)
    except OSError as error:
        raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISCHR(metadata.st_mode) or not os.isatty(descriptor):
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY)
        yield _ControllingTtyChannel(descriptor)
    finally:
        os.close(descriptor)


class AttendedGrantIssuer:
    """Confirm one immutable journal state and immediately consume the resulting grant."""

    def __init__(
        self,
        *,
        channel_factory: ChannelFactory = controlling_tty_channel,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = system_boottime_ns,
        boot_identity: BootIdentity = system_boot_identity_sha256,
        entropy: Entropy = secrets.token_bytes,
    ) -> None:
        self._channel_factory = channel_factory
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._boot_identity = boot_identity
        self._entropy = entropy

    def confirm_and_arm(
        self,
        controller: ArmController,
        record: ExactRestoreRecord,
    ) -> ExactRestoreRecord:
        if record.phase is not ExactRestorePhase.PREPARED or record.inflight is not None:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.RECORD)
        authority = self._confirm(record, permit_crash_resume=False)
        return controller.arm(authority)

    def confirm_and_reauthorize(
        self,
        controller: ReauthorizeController,
        record: ExactRestoreRecord,
    ) -> ExactRestoreRecord:
        if record.phase not in {
            ExactRestorePhase.ARMED,
            ExactRestorePhase.RESTORING,
            ExactRestorePhase.AWAITING_FINAL_VERIFY,
        }:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.RECORD)
        authority = self._confirm(record, permit_crash_resume=record.inflight is not None)
        return controller.reauthorize(authority)

    def confirm_and_recover(
        self,
        controller: RecoverController,
        record: ExactRestoreRecord,
    ) -> ExactRestoreRecord:
        if record.phase is not ExactRestorePhase.RECOVERY_REQUIRED:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.RECORD)
        authority = self._confirm(record, permit_crash_resume=record.inflight is not None)
        return controller.recover(authority)

    def _confirm(
        self,
        record: ExactRestoreRecord,
        *,
        permit_crash_resume: bool,
    ) -> ExactRestoreAuthority:
        next_action = (
            record.actions[len(record.completed_actions)].action_id
            if len(record.completed_actions) < len(record.actions)
            else "final-explicit-verification"
        )
        nonce = self._entropy(32)
        if len(nonce) != 32:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.TTY)
        challenge_digest = hashlib.sha256(
            b"jebao-flow-exact-restore-authority-v2\0"
            + record.operation_id.encode("utf-8")
            + bytes.fromhex(record.baseline_sha256)
            + bytes.fromhex(record.action_plan_sha256)
            + bytes.fromhex(record.authority_context_sha256)
            + nonce
        ).hexdigest()
        challenge = f"AUTHORIZE-{challenge_digest[:20].upper()}"
        warning = (
            "\nWARNING: an uncertain inflight action will be observed, never resent."
            if permit_crash_resume
            else ""
        )
        summary = (
            "Jebao exact restore attended authorization\n"
            f"operation: {record.operation_id}\n"
            f"cycle: {record.cycle.value}\n"
            f"phase: {record.phase.value}\n"
            f"baseline_sha256: {record.baseline_sha256}\n"
            f"action_plan_sha256: {record.action_plan_sha256}\n"
            f"journal_context_sha256: {record.authority_context_sha256}\n"
            f"authority_chain_sha256: {record.authority_chain_sha256}\n"
            f"completed_actions: {len(record.completed_actions)}/{len(record.actions)}\n"
            "inflight_sha256: "
            f"{record.inflight.inflight_sha256 if record.inflight is not None else 'none'}\n"
            f"next_action: {next_action}{warning}\n"
            f"Type exactly: {challenge}\n> "
        )
        with self._channel_factory() as channel:
            before_wall, before_monotonic_ns, before_boot = self._sample_clock()
            channel.write(summary)
            supplied = channel.read_line(_MAX_CONFIRMATION_BYTES)
            after_wall, after_monotonic_ns, after_boot = self._sample_clock()
        if not secrets.compare_digest(supplied, challenge):
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.DECLINED)
        max_age_ns = int(_MAX_CHALLENGE_AGE.total_seconds() * 1_000_000_000)
        if (
            after_boot != before_boot
            or after_wall < before_wall
            or after_monotonic_ns < before_monotonic_ns
            or after_wall - before_wall > _MAX_CHALLENGE_AGE
            or after_monotonic_ns - before_monotonic_ns > max_age_ns
        ):
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.RECORD)
        issued_at = after_wall
        issued_monotonic_ns = after_monotonic_ns
        boot_identity_sha256 = after_boot
        lifetime_ns = int(_AUTHORITY_LIFETIME.total_seconds() * 1_000_000_000)
        scope = (
            ExactRestoreAuthorityScope.EXACT_BASELINE_ONLY
            if record.cycle is ExactRestoreCycle.BASELINE_RESTORE
            else ExactRestoreAuthorityScope.BOOTSTRAP_QUALIFICATION
        )
        return ExactRestoreAuthority(
            operation_id=record.operation_id,
            cycle=record.cycle,
            baseline_sha256=record.baseline_sha256,
            action_plan_sha256=record.action_plan_sha256,
            verification_policy_sha256=record.baseline.verification_policy.policy_sha256,
            journal_context_sha256=record.authority_context_sha256,
            scope=scope,
            qualification_receipt_sha256=record.qualification_receipt_sha256,
            issued_at=issued_at,
            expires_at=issued_at + _AUTHORITY_LIFETIME,
            boot_identity_sha256=boot_identity_sha256,
            issued_monotonic_ns=issued_monotonic_ns,
            deadline_monotonic_ns=issued_monotonic_ns + lifetime_ns,
            confirmation_token_sha256=hashlib.sha256(challenge.encode("ascii")).hexdigest(),
            permit_crash_resume=permit_crash_resume,
            crash_resume_inflight_sha256=(
                record.inflight.inflight_sha256 if record.inflight is not None else None
            ),
        )

    def _sample_clock(self) -> tuple[datetime, int, str]:
        try:
            wall = self._wall_clock()
            monotonic_ns = self._monotonic_clock()
            boot_identity_sha256 = self._boot_identity()
        except Exception as error:
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.RECORD) from error
        if (
            wall.tzinfo is None
            or wall.utcoffset() is None
            or isinstance(monotonic_ns, bool)
            or not isinstance(monotonic_ns, int)
            or monotonic_ns < 0
            or not isinstance(boot_identity_sha256, str)
            or len(boot_identity_sha256) != 64
            or any(character not in "0123456789abcdef" for character in boot_identity_sha256)
        ):
            raise AttendedAuthorityError(AttendedAuthorityErrorCode.RECORD)
        return wall, monotonic_ns, boot_identity_sha256


__all__ = [
    "AttendedAuthorityError",
    "AttendedAuthorityErrorCode",
    "AttendedGrantIssuer",
    "ConfirmationChannel",
    "controlling_tty_channel",
]
