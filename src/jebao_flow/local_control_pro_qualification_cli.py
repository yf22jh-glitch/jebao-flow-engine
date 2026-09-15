"""Explicitly approved, fixed 2-Pro scalar qualification; no default live invocation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.compose import validate_current_compose_inputs
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.pro_qualification import ProQualificationProducer
from jebao_flow.local_control.qualification_producer import (
    CurrentQualificationEpochSource,
    QualificationArtifactStore,
)
from jebao_flow.local_control_qualification_cli import (
    _TEMPLATE_ROOTS_ENV,
    _activate_current_hardware_safety_volume_identity,
    _add_current_inputs,
    _environment_paths,
    _resolver,
)
from jebao_flow.operator_capture.paths import durable_private_path


def build_parser():
    parser = argparse.ArgumentParser(prog="jebao-flow-local-pro-qualify")
    _add_current_inputs(parser)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--target", action="append", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--active-template-sha256", required=True)
    parser.add_argument("--qualification-output-root", required=True, type=Path)
    parser.add_argument("--write-enabled", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    controller = None
    try:
        if not args.write_enabled or args.config is None or len(args.target) != 2:
            raise ValueError("explicit write opt-in, current config and two targets required")
        roots = tuple(args.template_root or _environment_paths(_TEMPLATE_ROOTS_ENV))
        if not roots:
            raise ValueError("current template roots required")
        if args.compose_generation:
            validate_current_compose_inputs(args.compose_generation)
        _activate_current_hardware_safety_volume_identity()
        output = durable_private_path(args.qualification_output_root, "qualification candidates")
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        # QualificationArtifactStore checks current ownership, 0700 and no-clobber files.
        controller = ProQualificationProducer(
            catalog=build_local_control_catalog(),
            resolver=_resolver(args.config),
            journal=LocalControlJournalStore(),
            lease=LocalControlLease(),
            epoch_providers=tuple(
                CurrentQualificationEpochSource(
                    config_path=args.config,
                    manifest_path=args.manifest,
                    template_roots=roots,
                    logical_member=target,
                    active_template_sha256=args.active_template_sha256,
                )
                for target in args.target
            ),
            artifact_factory=lambda target, function: QualificationArtifactStore(
                output / f"{target.physical_identity_key}-{function}.json"
            ),
        )
        result = asyncio.run(controller.execute(args.operation_id))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        result = {
            "operation_id": args.operation_id,
            "error_type": type(error).__name__,
            "next_command": f"jebao-flow-local recover-wavemaker --request-id {args.operation_id}",
            "accepted": False,
            "record": None,
        }
        if controller is not None:
            try:
                result["record"] = controller.status(args.operation_id)
            except Exception:
                pass
        print(json.dumps(result, sort_keys=True))
        return 130 if isinstance(error, KeyboardInterrupt) else 2


if __name__ == "__main__":
    raise SystemExit(main())
