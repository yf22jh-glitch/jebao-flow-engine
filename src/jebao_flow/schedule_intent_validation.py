"""Cycle-free validation for terminal schedule-linkage conflict tombstones."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from jebao_flow.devices.schedule_linkage import (
    ScheduleLinkagePreflight,
    schedule_linkage_confirmation_token,
)

TERMINAL_SCHEDULE_OUTCOMES = frozenset(
    {
        "roles_detached",
        "boundary_verified",
        "recovered",
        "crashed_before_first_write",
        "preview_cancelled",
        "refused_before_first_write",
    }
)


class TerminalScheduleIntentError(ValueError):
    """The payload is not a complete, authentic terminal schedule intent."""


def _cli_token(instance_id: str, preflight: ScheduleLinkagePreflight) -> str:
    canonical = {
        "version": 1,
        "instance_id": instance_id,
        "preflight": preflight.model_dump(mode="json"),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFS-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def validate_terminal_schedule_intent_payload(payload: Any) -> None:
    """Validate enough of another workflow's tombstone to safely allow concurrency."""

    required = {
        "version",
        "instance_id",
        "operation_id",
        "phase",
        "confirmation_token",
        "preflight",
        "created_at",
        "updated_at",
        "outcome",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise TerminalScheduleIntentError("invalid schedule intent schema")
    instance_id = payload.get("instance_id")
    operation_id = payload.get("operation_id")
    token = payload.get("confirmation_token")
    if (
        payload.get("version") != 1
        or payload.get("phase") != "terminal"
        or payload.get("outcome") not in TERMINAL_SCHEDULE_OUTCOMES
        or not isinstance(instance_id, str)
        or not instance_id
        or not isinstance(operation_id, str)
        or not operation_id
        or not isinstance(token, str)
        or len(token) != 24
        or not token.startswith("JFS-")
        or any(character not in "0123456789ABCDEF" for character in token[4:])
    ):
        raise TerminalScheduleIntentError("invalid terminal schedule intent")
    try:
        preflight = ScheduleLinkagePreflight.model_validate(payload["preflight"])
        created_at = datetime.fromisoformat(payload["created_at"])
        updated_at = datetime.fromisoformat(payload["updated_at"])
    except (TypeError, ValueError, ValidationError) as error:
        raise TerminalScheduleIntentError("invalid terminal schedule evidence") from error
    if (
        preflight.spec.operation_id != operation_id
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
        or updated_at.tzinfo is None
        or updated_at.utcoffset() is None
        or updated_at < created_at
    ):
        raise TerminalScheduleIntentError("inconsistent terminal schedule evidence")
    expected_core = schedule_linkage_confirmation_token(
        preflight.spec, preflight.snapshots
    )
    expected_cli = _cli_token(instance_id, preflight)
    if not hmac.compare_digest(
        preflight.confirmation_token, expected_core
    ) or not hmac.compare_digest(token, expected_cli):
        raise TerminalScheduleIntentError("invalid terminal schedule token")


__all__ = [
    "TERMINAL_SCHEDULE_OUTCOMES",
    "TerminalScheduleIntentError",
    "validate_terminal_schedule_intent_payload",
]
