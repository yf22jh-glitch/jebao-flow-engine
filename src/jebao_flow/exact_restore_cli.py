"""Installed attended CLI for the standalone exact-restore qualification path."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from jebao_flow.exact_restore import (
    ExactRestoreError,
    ExactRestorePhase,
    ExactRestoreReceipt,
    ExactRestoreRecord,
)
from jebao_flow.exact_restore_authority import AttendedAuthorityError, AttendedGrantIssuer
from jebao_flow.exact_restore_composition import (
    ExactRestoreComposition,
    ExactRestoreCompositionError,
    _build_attended_production_composition,
    load_bound_record,
    load_locked_config,
    load_operation_manifest,
    prepare_operation,
    public_record_status,
    stage_qualified_final_restore,
)
from jebao_flow.exact_restore_q2_evidence import (
    ExactRestoreQ2EvidenceError,
    ExactRestoreQ2EvidenceVerifier,
    QualifiedBaselineArchive,
    QualifiedBaselineBundle,
    build_qualified_baseline_bundle,
)
from jebao_flow.exact_restore_store import ExactRestoreJournalStore


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject bad argv without reflecting private paths or values."""

    def error(self, _message: str) -> None:
        raise ExactRestoreCompositionError("command_line_invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="jebao-flow-exact-restore")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show privacy-safe durable state")

    prepare = commands.add_parser("prepare", help="verify preserved raw and create PREPARED")
    _add_private_inputs(prepare)
    prepare.add_argument("--artifact-root", required=True)

    qualify = commands.add_parser("qualify", help="attended sentinel and exact baseline cycles")
    _add_private_inputs(qualify)

    stage = commands.add_parser(
        "stage-final",
        help="retry write-free staging of the already-qualified phase-5 restore",
    )
    _add_private_inputs(stage)

    restore = commands.add_parser("restore", help="attended phase-5 exact baseline restore")
    _add_private_inputs(restore)

    recover = commands.add_parser("recover", help="attended recovery of the current inflight cycle")
    _add_private_inputs(recover)

    restore_recover = commands.add_parser(
        "restore-recover",
        help="attended recovery of an inflight phase-5 restore",
    )
    _add_private_inputs(restore_recover)
    return parser


def _add_private_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="owner-only locked deployment config")
    parser.add_argument("--operation-manifest", required=True, help="owner-only operation manifest")


def _composition(args: argparse.Namespace) -> ExactRestoreComposition:
    config = load_locked_config(args.config)
    manifest = load_operation_manifest(args.operation_manifest)
    return _build_attended_production_composition(config=config, manifest=manifest)


def _load_current(composition: ExactRestoreComposition) -> ExactRestoreRecord:
    return load_bound_record(composition)


def _persist_q2_bundle(
    composition: ExactRestoreComposition,
    record: ExactRestoreRecord,
    receipt: ExactRestoreReceipt,
) -> QualifiedBaselineBundle:
    bundle = build_qualified_baseline_bundle(
        manifest=composition.manifest,
        record=record,
        baseline_receipt=receipt,
    )
    QualifiedBaselineArchive().persist(bundle)
    return bundle


def _is_final_restore_record(record: object) -> bool:
    parent = getattr(record, "qualification_final_record", None)
    parent_cycle = getattr(getattr(parent, "cycle", None), "value", None)
    return parent_cycle == "baseline_restore"


def _stage_final_restore(
    composition: ExactRestoreComposition,
    bundle: QualifiedBaselineBundle,
) -> ExactRestoreRecord:
    # Bind the staged record to the immutable bundle timestamp.  This lets the precommitted Q2
    # manifest prove the exact phase-5 plan after a successful restore has cleared the live
    # journal, without weakening the prepare-time requirement that the journal really exists.
    return stage_qualified_final_restore(
        composition,
        bundle.qualified_record,
        now=bundle.persisted_at,
    )


async def _continue_qualification(
    composition: ExactRestoreComposition,
    *,
    recover_first: bool,
    issuer: AttendedGrantIssuer | None = None,
) -> ExactRestoreRecord:
    """Advance only the journal's current safe state; every new grant comes from /dev/tty."""

    authority_issuer = issuer or AttendedGrantIssuer()
    record = _load_current(composition)
    if _is_final_restore_record(record):
        raise ExactRestoreCompositionError("final_restore_staged_use_restore")
    if recover_first:
        if record.phase is not ExactRestorePhase.RECOVERY_REQUIRED:
            raise ExactRestoreCompositionError("recovery_not_required")
        record = authority_issuer.confirm_and_recover(composition.controller, record)
        already_authorized = True
    else:
        if record.phase is ExactRestorePhase.RECOVERY_REQUIRED:
            raise ExactRestoreCompositionError("attended_recovery_required")
        already_authorized = False

    # At most two completed cycles exist: sentinel qualification, then baseline restore.
    # Iteration bounds make malformed or non-progressing controller behavior fail closed.
    for _ in range(8):
        record = _load_current(composition)
        if record.phase is ExactRestorePhase.RECOVERY_REQUIRED:
            raise ExactRestoreCompositionError("attended_recovery_required")

        if record.phase is ExactRestorePhase.FINAL_VERIFIED:
            receipt = await composition.controller.finalize()
            if record.cycle.value == "sentinel_qualification":
                record = composition.controller.promote_to_baseline_restore(
                    operation_id=composition.manifest.baseline_operation_id
                )
                already_authorized = False
                continue
            bundle = _persist_q2_bundle(composition, record, receipt)
            composition.controller.clear_after_receipt(receipt)
            return _stage_final_restore(composition, bundle)

        if record.phase is ExactRestorePhase.PREPARED:
            record = authority_issuer.confirm_and_arm(composition.controller, record)
            already_authorized = True
        elif record.phase in {
            ExactRestorePhase.ARMED,
            ExactRestorePhase.RESTORING,
            ExactRestorePhase.AWAITING_FINAL_VERIFY,
        }:
            if not already_authorized:
                record = authority_issuer.confirm_and_reauthorize(composition.controller, record)
            already_authorized = True
        else:
            raise ExactRestoreCompositionError("exact_restore_phase_invalid")

        record = await composition.controller.execute()
        already_authorized = True
        if record.phase is not ExactRestorePhase.FINAL_VERIFIED:
            raise ExactRestoreCompositionError("exact_restore_did_not_finalize")
        # The next iteration archives/promotes under the controller's exact journal checks.

    raise ExactRestoreCompositionError("exact_restore_state_machine_stalled")


async def _continue_final_restore(
    composition: ExactRestoreComposition,
    *,
    recover_first: bool,
    issuer: AttendedGrantIssuer | None = None,
) -> ExactRestoreRecord:
    """Advance only the pre-staged phase-5 restore; never start qualification here."""

    authority_issuer = issuer or AttendedGrantIssuer()
    record = _load_current(composition)
    if not _is_final_restore_record(record):
        raise ExactRestoreCompositionError("final_restore_not_staged")
    if recover_first:
        if record.phase is not ExactRestorePhase.RECOVERY_REQUIRED:
            raise ExactRestoreCompositionError("recovery_not_required")
        authority_issuer.confirm_and_recover(composition.controller, record)
        already_authorized = True
    else:
        if record.phase is ExactRestorePhase.RECOVERY_REQUIRED:
            raise ExactRestoreCompositionError("attended_recovery_required")
        already_authorized = False

    for _ in range(6):
        record = _load_current(composition)
        if record.phase is ExactRestorePhase.RECOVERY_REQUIRED:
            raise ExactRestoreCompositionError("attended_recovery_required")
        if record.phase is ExactRestorePhase.FINAL_VERIFIED:
            receipt = await composition.controller.finalize()
            composition.controller.clear_after_receipt(receipt)
            return record
        if record.phase is ExactRestorePhase.PREPARED:
            authority_issuer.confirm_and_arm(composition.controller, record)
            already_authorized = True
        elif record.phase in {
            ExactRestorePhase.ARMED,
            ExactRestorePhase.RESTORING,
            ExactRestorePhase.AWAITING_FINAL_VERIFY,
        }:
            if not already_authorized:
                authority_issuer.confirm_and_reauthorize(composition.controller, record)
            already_authorized = True
        else:
            raise ExactRestoreCompositionError("exact_restore_phase_invalid")
        record = await composition.controller.execute()
        if record.phase is not ExactRestorePhase.FINAL_VERIFIED:
            raise ExactRestoreCompositionError("exact_restore_did_not_finalize")
    raise ExactRestoreCompositionError("exact_restore_state_machine_stalled")


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "status":
        payload = ExactRestoreJournalStore().load()
        record = ExactRestoreRecord.model_validate(payload) if payload is not None else None
        return public_record_status(record)
    if args.command == "prepare":
        config = load_locked_config(args.config)
        manifest = load_operation_manifest(args.operation_manifest)
        record = prepare_operation(
            config=config,
            manifest=manifest,
            artifact_root=args.artifact_root,
        )
        return public_record_status(record)
    if args.command == "qualify":
        record = await _continue_qualification(_composition(args), recover_first=False)
        result = public_record_status(record)
        result["status"] = "exact_restore_qualified_final_restore_staged"
        return result
    if args.command == "stage-final":
        composition = _composition(args)
        bundle = ExactRestoreQ2EvidenceVerifier(
            composition.manifest
        ).load_verified_qualified_bundle()
        record = _stage_final_restore(composition, bundle)
        result = public_record_status(record)
        result["status"] = "exact_restore_final_restore_staged"
        return result
    if args.command == "restore":
        record = await _continue_final_restore(_composition(args), recover_first=False)
        result = public_record_status(record)
        result["status"] = "exact_restore_phase5_completed_and_archived"
        return result
    if args.command == "recover":
        record = await _continue_qualification(_composition(args), recover_first=True)
        result = public_record_status(record)
        result["status"] = "exact_restore_recovered_qualified_final_restore_staged"
        return result
    if args.command == "restore-recover":
        record = await _continue_final_restore(_composition(args), recover_first=True)
        result = public_record_status(record)
        result["status"] = "exact_restore_phase5_recovered_completed_and_archived"
        return result
    raise ExactRestoreCompositionError("command_line_invalid")


def _failure(
    code: str,
    *,
    exit_code: int = 2,
    diagnostic: dict[str, object] | None = None,
) -> int:
    payload: dict[str, object] = {
        "code": code,
        "status": "exact_restore_refused",
    }
    if diagnostic:
        payload["diagnostic"] = diagnostic
    print(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        file=sys.stderr,
    )
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        result = asyncio.run(_run(args))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return _failure("operator_interrupt", exit_code=130)
    except ExactRestoreCompositionError as error:
        return _failure(error.code)
    except ExactRestoreQ2EvidenceError as error:
        return _failure(error.code)
    except ExactRestoreError as error:
        return _failure(error.code.value, diagnostic=error.diagnostic)
    except AttendedAuthorityError as error:
        return _failure(f"attended_{error.code.value}")
    except Exception:
        # Network errors and validation details may contain private paths or endpoints.
        return _failure("private_operation_error")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
