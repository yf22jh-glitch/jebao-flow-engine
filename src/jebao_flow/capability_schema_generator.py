"""Generate tier-(d) capability claims from pinned protocol schema sources."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from jebao_flow.capability_matrix import (
    PINNED_CAPABILITY_SOURCES,
    CapabilityClaimError,
    Claim,
    ClaimSet,
    Provenance,
    render_yaml,
    schema_claim_id,
    verify_source_pin,
    write_atomic,
)
from jebao_flow.protocol import schedule
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schema import Datapoint

PINNED_SOURCES = PINNED_CAPABILITY_SOURCES + (
    (
        "jebao_flow.capability_schema_generator",
        "src/jebao_flow/capability_schema_generator.py",
    ),
)


def classify_datapoint(attribute: Datapoint) -> str | None:
    """Classify one profile datapoint without turning faults into capability claims."""

    if attribute.is_problem:
        return None
    return "L2" if attribute.name.startswith("Auto") else "L1"


def _claim(
    *,
    product_key: str,
    layer: str,
    subject: str,
    value: bool | int | str | tuple[bool | int | str, ...],
    source_module: str,
    commit_sha: str,
) -> Claim:
    return Claim(
        claim_id=schema_claim_id(product_key, subject),
        product_key=product_key,
        layer=layer,
        goal_tier="wire",
        subject=subject,
        value=value,
        evidence_tier="d",
        source=f"schema:jebao_flow.protocol.{source_module}@{commit_sha}",
        status="PASS",
    )


def _profile_claims(product_key: str, commit_sha: str) -> list[Claim]:
    schema = get_product_schema(product_key)
    claims = [
        _claim(
            product_key=product_key,
            layer="L0",
            subject="raw_status_size_bytes",
            value=schema.raw_status_size,
            source_module="profiles",
            commit_sha=commit_sha,
        ),
        _claim(
            product_key=product_key,
            layer="L0",
            subject="attribute_flags_size_bytes",
            value=schema.attribute_flags_size,
            source_module="profiles",
            commit_sha=commit_sha,
        ),
        _claim(
            product_key=product_key,
            layer="L0",
            subject="attribute_values_size_bytes",
            value=schema.attribute_values_size,
            source_module="profiles",
            commit_sha=commit_sha,
        ),
        _claim(
            product_key=product_key,
            layer="L0",
            subject="bit_group_width",
            value=schema.bit_group_width,
            source_module="profiles",
            commit_sha=commit_sha,
        ),
        _claim(
            product_key=product_key,
            layer="L0",
            subject="fault_datapoint_count",
            value=sum(attribute.is_problem for attribute in schema.attributes),
            source_module="profiles",
            commit_sha=commit_sha,
        ),
    ]
    for attribute in schema.attributes:
        layer = classify_datapoint(attribute)
        if layer is None:
            continue
        prefix = attribute.name
        claims.extend(
            (
                _claim(
                    product_key=product_key,
                    layer=layer,
                    subject=f"{prefix}.datapoint_id",
                    value=attribute.id,
                    source_module="profiles",
                    commit_sha=commit_sha,
                ),
                _claim(
                    product_key=product_key,
                    layer=layer,
                    subject=f"{prefix}.data_type",
                    value=attribute.data_type.value,
                    source_module="profiles",
                    commit_sha=commit_sha,
                ),
            )
        )
        if attribute.numeric is not None:
            claims.append(
                _claim(
                    product_key=product_key,
                    layer=layer,
                    subject=f"{prefix}.schema_range",
                    value=(attribute.numeric.minimum, attribute.numeric.maximum),
                    source_module="profiles",
                    commit_sha=commit_sha,
                )
            )
        if attribute.enum_values:
            claims.append(
                _claim(
                    product_key=product_key,
                    layer=layer,
                    subject=f"{prefix}.enum_values",
                    value=attribute.enum_values,
                    source_module="profiles",
                    commit_sha=commit_sha,
                )
            )
    return claims


def _schedule_claims(product_key: str, commit_sha: str) -> list[Claim]:
    spec = schedule._SPECS.get(product_key)
    if spec is None:
        raise CapabilityClaimError("schedule_spec_missing")
    values = (
        ("schedule_slot_capacity", schedule.SLOT_CAPACITY),
        ("schedule_slot_size_bytes", spec.slot_size),
        ("schedule_slot_offset", spec.slot_offset),
        ("schedule_image_size_bytes", schedule.SLOT_CAPACITY * spec.slot_size),
    )
    claims = [
        _claim(
            product_key=product_key,
            layer="L0",
            subject=subject,
            value=value,
            source_module="schedule",
            commit_sha=commit_sha,
        )
        for subject, value in values
    ]
    claims.append(
        _claim(
            product_key=product_key,
            layer="L2",
            subject="AutoMode.code_space",
            value=spec.modes,
            source_module="schedule",
            commit_sha=commit_sha,
        )
    )
    return claims


def generate(
    product_keys: Sequence[str],
    *,
    commit_sha: str,
    source_digest_sha256: str,
) -> ClaimSet:
    unique_products = tuple(sorted(set(product_keys)))
    if not unique_products or len(unique_products) != len(product_keys):
        raise CapabilityClaimError("schema_products_invalid")
    claims: list[Claim] = []
    try:
        for product_key in unique_products:
            claims.extend(_profile_claims(product_key, commit_sha))
            claims.extend(_schedule_claims(product_key, commit_sha))
    except KeyError as error:
        raise CapabilityClaimError("schema_product_unsupported") from error
    return ClaimSet(
        kind="schema",
        claims=tuple(claims),
        provenance=Provenance(
            schema_generator_commit=commit_sha,
            schema_generator_source_digest_sha256=source_digest_sha256,
        ),
        reproducible_without_private_artifacts=True,
    )


def _parse_products(value: str) -> tuple[str, ...]:
    products = tuple(item.strip() for item in value.split(",") if item.strip())
    if not products:
        raise argparse.ArgumentTypeError("products_required")
    return products


class _SafeArgumentParser(argparse.ArgumentParser):
    """Never echo argv values in parse failures."""

    def error(self, _message: str) -> None:
        raise CapabilityClaimError("schema_generator_command_line_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--products", type=_parse_products, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        source_digest = verify_source_pin(
            args.repo_root,
            args.source_commit,
            PINNED_SOURCES,
            self_module_name="jebao_flow.capability_schema_generator",
            self_module_file=__file__,
        )
        claim_set = generate(
            args.products,
            commit_sha=args.source_commit,
            source_digest_sha256=source_digest,
        )
        write_atomic(args.output, render_yaml(claim_set))
    except CapabilityClaimError as error:
        print(error.code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
