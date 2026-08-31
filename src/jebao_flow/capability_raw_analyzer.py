"""Derive product-level tier-(a) claims from verified v2 collector series."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from jebao_flow.capability_matrix import (
    PINNED_CAPABILITY_SOURCES,
    CapabilityClaimError,
    Claim,
    ClaimSet,
    Provenance,
    observation_claim_id,
    observation_output_name,
    render_yaml,
    verify_source_pin,
    write_exclusive,
)
from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import decode_schedule
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LocalWavemakerProScheduleSnapshot,
    ScheduleWireValidationError,
)
from jebao_flow.protocol.schema import Datapoint, DataType
from jebao_flow.read_only_collector import ArtifactStoreError, PilotSeriesStore

PINNED_SOURCES = PINNED_CAPABILITY_SOURCES + (
    ("jebao_flow.protocol.models", "src/jebao_flow/protocol/models.py"),
    ("jebao_flow.protocol.codec", "src/jebao_flow/protocol/codec.py"),
    ("jebao_flow.protocol.schedule_wire", "src/jebao_flow/protocol/schedule_wire.py"),
    ("jebao_flow.read_only_collector", "src/jebao_flow/read_only_collector.py"),
    (
        "jebao_flow.capability_raw_analyzer",
        "src/jebao_flow/capability_raw_analyzer.py",
    ),
)

_STATE_REPLY_ACTION = 0x03
_MANUAL_FIELDS = (
    "SwitchON",
    "TimerON",
    "Linkage",
    "Mode",
    "Flow",
    "Frequency",
    "Cust_Wav_Freq",
    "FeedTime",
    "PulseTide",
    "FeedSwitch",
)
_AUTO_FIELDS = (
    "AutoMode",
    "AutoFlow",
    "AutoFreq",
    "Auto_Cust_Wav_Freq",
    "AutoFeedTime",
    "AutoPulseTide",
)


def _validate_local_wavemaker_pro_schedule(raw_status: bytes) -> bool:
    try:
        LocalWavemakerProScheduleSnapshot.from_status(raw_status).validate()
    except ScheduleWireValidationError:
        return False
    return True


_SCHEDULE_SLOT_VALIDATORS: dict[str, Callable[[bytes], bool]] = {
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY: _validate_local_wavemaker_pro_schedule,
}


def _scalar_sort_key(value: bool | int | str) -> tuple[int, object]:
    if type(value) is bool:
        return (0, int(value))
    if type(value) is int:
        return (1, value)
    return (2, value)


def _valid_decoded_value(attribute: Datapoint, value: Any) -> bool:
    if attribute.data_type is DataType.BOOL:
        return type(value) is bool
    if attribute.enum_values:
        return isinstance(value, str) and value in attribute.enum_values
    if attribute.numeric is not None:
        return (
            type(value) is int
            and attribute.numeric.minimum <= value <= attribute.numeric.maximum
        )
    return False


def _add_value(
    observed: dict[str, list[bool | int | str]],
    validity: dict[str, bool],
    subject: str,
    value: object,
    *,
    valid: bool,
) -> None:
    if type(value) not in {bool, int, str}:
        raise CapabilityClaimError("raw_observed_value_type_invalid")
    observed[subject].append(value)  # type: ignore[arg-type]
    validity[subject] = validity.get(subject, True) and valid


def _validate_plan_product(plan: dict[str, Any], product_key: str, raw_status_size: int) -> None:
    targets = plan.get("ordered_targets")
    acquisition = plan.get("acquisition")
    if (
        not isinstance(targets, list)
        or len(targets) != 2
        or any(
            not isinstance(target, dict) or target.get("product_key") != product_key
            for target in targets
        )
        or not isinstance(acquisition, dict)
        or acquisition.get("status_payload_size_bytes") != raw_status_size
        or acquisition.get("serial_payload_size_bytes") != raw_status_size + 1
    ):
        raise CapabilityClaimError("raw_analyzer_product_binding_mismatch")


def _layer_for_subject(subject: str) -> str:
    if subject in {
        "raw_status_size_bytes",
        "serial_payload_size_bytes",
        "transport_reply_action",
    }:
        return "L0"
    if subject.startswith("schedule.") or subject.split(".", 1)[0] in _AUTO_FIELDS:
        return "L2"
    return "L1"


def _claims_from_observations(
    *,
    series_id: str,
    product_key: str,
    observed: dict[str, list[bool | int | str]],
    validity: dict[str, bool],
) -> tuple[Claim, ...]:
    source = f"artifact:{series_id}#all-records"
    claims: list[Claim] = []
    for subject in sorted(observed):
        unique_values = tuple(sorted(set(observed[subject]), key=_scalar_sort_key))
        if not unique_values:
            continue
        value: bool | int | str | tuple[bool | int | str, ...]
        if subject in {
            "raw_status_size_bytes",
            "serial_payload_size_bytes",
            "transport_reply_action",
        }:
            if len(unique_values) != 1:
                raise CapabilityClaimError("raw_wire_fact_not_constant")
            value = unique_values[0]
        else:
            value = unique_values
        claims.append(
            Claim(
                claim_id=observation_claim_id(series_id, subject),
                product_key=product_key,
                layer=_layer_for_subject(subject),
                goal_tier="wire" if subject in {
                    "raw_status_size_bytes",
                    "serial_payload_size_bytes",
                    "transport_reply_action",
                } else "accepted",
                subject=subject,
                value=value,
                evidence_tier="a",
                source=source,
                status="PASS" if validity.get(subject, False) else "UNKNOWN",
            )
        )
    return tuple(claims)


class _SafeArgumentParser(argparse.ArgumentParser):
    """Never echo private argv values in parse failures."""

    def error(self, _message: str) -> None:
        raise CapabilityClaimError("raw_analyzer_command_line_invalid")


def analyze_series(
    store: PilotSeriesStore,
    reference: Any,
    *,
    expected_series_sha256: str,
    product_key: str,
    analyzer_commit: str,
    analyzer_source_digest_sha256: str,
) -> ClaimSet:
    try:
        schema = get_product_schema(product_key)
        plan = store.verify_plan(reference)
        _validate_plan_product(plan, product_key, schema.raw_status_size)
        series = store.verify_completed_series(
            reference,
            expected_series_sha256=expected_series_sha256,
        )
    except CapabilityClaimError:
        raise
    except (ArtifactStoreError, KeyError, TypeError, ValueError) as error:
        raise CapabilityClaimError("raw_series_verification_failed") from error

    records = series.get("records")
    if not isinstance(records, list):
        raise CapabilityClaimError("raw_series_records_invalid")
    ordinals = [
        record.get("ordinal")
        for record in records
        if isinstance(record, dict) and record.get("outcome") == "accepted"
    ]
    if any(type(ordinal) is not int for ordinal in ordinals):
        raise CapabilityClaimError("raw_series_ordinal_invalid")

    observed: dict[str, list[bool | int | str]] = defaultdict(list)
    validity: dict[str, bool] = {}
    attributes = schema.attributes_by_name
    selected_fields = tuple(
        field for field in _MANUAL_FIELDS + _AUTO_FIELDS if field in attributes
    )

    for ordinal in ordinals:
        try:
            pair = store.extract_verified_accepted_pair(
                reference,
                expected_series_sha256=expected_series_sha256,
                ordinal=ordinal,
            )
        except ArtifactStoreError as error:
            raise CapabilityClaimError("raw_pair_extraction_failed") from error
        for sample in pair.samples:
            try:
                frame = decode_frame(sample.raw_wire_frame)
            except Exception as error:
                raise CapabilityClaimError("raw_frame_decode_failed") from error
            if (
                frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
                or len(frame.payload) != schema.raw_status_size + 1
                or frame.payload[0] != _STATE_REPLY_ACTION
            ):
                raise CapabilityClaimError("raw_frame_transport_invalid")
            raw_status = frame.payload[1:]
            try:
                values = schema.decode_status(raw_status)
            except (KeyError, TypeError, ValueError) as error:
                raise CapabilityClaimError("raw_status_decode_failed") from error

            schedule_validator = _SCHEDULE_SLOT_VALIDATORS.get(product_key)
            decoded_schedule = None
            schedule_valid = False
            if schedule_validator is not None:
                try:
                    decoded_schedule = decode_schedule(
                        product_key,
                        raw_status,
                        enabled=values["TimerON"],
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise CapabilityClaimError("raw_status_decode_failed") from error
                if decoded_schedule is None:
                    raise CapabilityClaimError("raw_schedule_missing")
                schedule_valid = (
                    not decoded_schedule.invalid_slots and schedule_validator(raw_status)
                )

            _add_value(
                observed,
                validity,
                "raw_status_size_bytes",
                len(raw_status),
                valid=len(raw_status) == schema.raw_status_size,
            )
            _add_value(
                observed,
                validity,
                "serial_payload_size_bytes",
                len(frame.payload),
                valid=len(frame.payload) == schema.raw_status_size + 1,
            )
            _add_value(
                observed,
                validity,
                "transport_reply_action",
                "0x03",
                valid=True,
            )

            for field in selected_fields:
                attribute = attributes[field]
                value = values[field]
                _add_value(
                    observed,
                    validity,
                    f"{field}.observed_values",
                    value,
                    valid=_valid_decoded_value(attribute, value),
                )

            if decoded_schedule is not None:
                _add_value(
                    observed,
                    validity,
                    "schedule.active_slot_count.observed_values",
                    len(decoded_schedule.entries),
                    valid=schedule_valid,
                )
                for entry in decoded_schedule.entries:
                    _add_value(
                        observed,
                        validity,
                        "schedule.slot_mode.observed_values",
                        entry.mode,
                        valid=schedule_valid,
                    )
                    for parameter, value in entry.parameters.items():
                        subject = f"schedule.slot_parameter.{parameter}.observed_values"
                        _add_value(
                            observed,
                            validity,
                            subject,
                            value,
                            valid=schedule_valid,
                        )

    claims = _claims_from_observations(
        series_id=reference.series_id,
        product_key=product_key,
        observed=observed,
        validity=validity,
    )
    return ClaimSet(
        kind="observation",
        claims=claims,
        provenance=Provenance(
            raw_analyzer_commit=analyzer_commit,
            raw_analyzer_source_digest_sha256=analyzer_source_digest_sha256,
            input_artifact_digests_sha256=(expected_series_sha256,),
        ),
        reproducible_without_private_artifacts=False,
    )


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--analyzer-commit", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--expected-series-sha256", required=True)
    parser.add_argument("--product-key", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        artifact_root = args.artifact_root.resolve()
        output = args.output.resolve()
        if output == artifact_root or artifact_root in output.parents:
            raise CapabilityClaimError("raw_analyzer_output_inside_artifact_root")
        if output.name != observation_output_name(args.series_id):
            raise CapabilityClaimError("raw_analyzer_output_name_invalid")
        source_digest = verify_source_pin(
            args.repo_root,
            args.analyzer_commit,
            PINNED_SOURCES,
            self_module_name="jebao_flow.capability_raw_analyzer",
            self_module_file=__file__,
        )
        store = PilotSeriesStore(args.artifact_root)
        reference = store.load(args.series_id)
        claim_set = analyze_series(
            store,
            reference,
            expected_series_sha256=args.expected_series_sha256,
            product_key=args.product_key,
            analyzer_commit=args.analyzer_commit,
            analyzer_source_digest_sha256=source_digest,
        )
        write_exclusive(args.output, render_yaml(claim_set))
    except (ArtifactStoreError, CapabilityClaimError) as error:
        print(error.code, file=sys.stderr)
        return 2
    except Exception:
        # Raw exception strings can contain private artifact roots or identifiers.
        print("private_operation_error", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
