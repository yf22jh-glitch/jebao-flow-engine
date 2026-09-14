"""Best-effort, value-free exception evidence; never part of write admission.

D1u preserved an uncertain action but not its original exception. This observer records
exception types and code locations, not messages, locals, source text, or wire data.
Never attach exc_info or extra: a shared formatter could expose unsanitized values.
Keep these observers synchronous so they do not introduce cancellation suspension points.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from jebao_flow.local_control.journal import LocalControlJournalRecord

_LOGGER = logging.getLogger(__name__)
_MAX_CHAIN = 8
_MAX_FRAMES = 32


def failure_clock_ns() -> int | None:
    """Read an observational clock without adding a failure to the control path."""
    try:
        return time.monotonic_ns()
    except BaseException:
        return None


def report_action_failure(
    record: LocalControlJournalRecord,
    index: int,
    error: BaseException,
    *,
    started_ns: int | None,
    failed_ns: int | None,
) -> None:
    """Report after the uncertain save without replacing the original exception.

    The journal diagnostic identifies the catch boundary, not necessarily the physical
    send boundary. In particular rollback_outcome_uncertain also covers its readback.
    The logging sink is best effort; durable preservation belongs to the operator capture.
    """

    try:
        chain: list[dict[str, object]] = []
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen and len(chain) < _MAX_CHAIN:
            seen.add(id(current))
            frames: list[dict[str, object]] = []
            trace = current.__traceback__
            while trace is not None:
                module = trace.tb_frame.f_globals.get("__name__", "")
                if isinstance(module, str) and module.startswith("jebao_flow."):
                    frames.append(
                        {
                            "module": module,
                            "function": trace.tb_frame.f_code.co_name,
                            "line": trace.tb_lineno,
                        }
                    )
                trace = trace.tb_next
            error_type = type(current)
            chain.append(
                {
                    "type": f"{error_type.__module__}.{error_type.__qualname__}",
                    "frames": frames[-_MAX_FRAMES:],
                    "frames_truncated": len(frames) > _MAX_FRAMES,
                }
            )
            current = current.__cause__ or (
                None if current.__suppress_context__ else current.__context__
            )
        action = record.actions[index]
        payload = {
            "event": "local_control_action_failure",
            "event_version": 1,
            "request_sha256": hashlib.sha256(record.request_id.encode("ascii")).hexdigest(),
            "boundary_elapsed_ns": (
                failed_ns - started_ns
                if started_ns is not None and failed_ns is not None and failed_ns >= started_ns
                else None
            ),
            "revision": record.revision,
            "action_index": index,
            "function": action.function,
            "diagnostic": action.diagnostic,
            "forward_payload_sha256": action.forward_payload_sha256,
            "rollback_payload_sha256": action.rollback_payload_sha256,
            "exception_chain": chain,
            "chain_truncated_or_cyclic": current is not None,
        }
        _LOGGER.error("%s", json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except BaseException:
        # An observer, including a broken logging handler, cannot replace the original
        # failure or interfere with the already-persisted recovery-required journal.
        pass
