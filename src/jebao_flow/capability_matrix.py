"""Deterministic, publishable capability claim sets.

This module deliberately contains no device transport or write path.  It is the shared
serialization and validation boundary for schema-declared claims and preserved-raw
observations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

CLAIM_SCHEMA_VERSION = 1

type Scalar = bool | int | str
type ClaimValue = Scalar | tuple[Scalar, ...]

type SourcePin = tuple[str, str]

PINNED_CAPABILITY_SOURCES: tuple[SourcePin, ...] = (
    ("jebao_flow.protocol.profiles", "src/jebao_flow/protocol/profiles.py"),
    ("jebao_flow.protocol.schema", "src/jebao_flow/protocol/schema.py"),
    ("jebao_flow.protocol.schedule", "src/jebao_flow/protocol/schedule.py"),
    ("jebao_flow.capability_matrix", "src/jebao_flow/capability_matrix.py"),
)

_LAYERS = frozenset({"L0", "L1", "L2", "L3"})
_GOAL_TIERS = frozenset({"wire", "accepted", "physical"})
_EVIDENCE_TIERS = frozenset({"a", "b", "c", "d"})
_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})
_DATA_TYPES = frozenset({"bool", "enum", "uint8", "uint16", "binary"})

_MANUAL_FIELDS = frozenset(
    {
        "SwitchON",
        "TimerON",
        "PulseTide",
        "FeedSwitch",
        "Linkage",
        "Mode",
        "Flow",
        "Frequency",
        "Cust_Wav_Freq",
        "FeedTime",
    }
)
_AUTO_FIELDS = frozenset(
    {
        "AutoMode",
        "AutoFlow",
        "AutoFreq",
        "Auto_Cust_Wav_Freq",
        "AutoFeedTime",
        "AutoPulseTide",
    }
)
_DATAPOINT_SUFFIXES = ("datapoint_id", "data_type", "schema_range", "enum_values")
_SCHEMA_L0_SUBJECTS = frozenset(
    {
        "raw_status_size_bytes",
        "attribute_flags_size_bytes",
        "attribute_values_size_bytes",
        "bit_group_width",
        "fault_datapoint_count",
        "schedule_slot_capacity",
        "schedule_slot_size_bytes",
        "schedule_slot_offset",
        "schedule_image_size_bytes",
    }
)
SCHEMA_SUBJECTS = frozenset(
    _SCHEMA_L0_SUBJECTS
    | {
        f"{field}.{suffix}"
        for field in _MANUAL_FIELDS | _AUTO_FIELDS
        for suffix in _DATAPOINT_SUFFIXES
    }
    | {"AutoMode.code_space"}
)

_OBSERVATION_L0_SUBJECTS = frozenset(
    {"raw_status_size_bytes", "serial_payload_size_bytes", "transport_reply_action"}
)
_OBSERVATION_SCHEDULE_SUBJECTS = frozenset(
    {
        "schedule.slot_mode.observed_values",
        "schedule.active_slot_count.observed_values",
        "schedule.slot_parameter.flow.observed_values",
        "schedule.slot_parameter.frequency.observed_values",
        "schedule.slot_parameter.feed_time.observed_values",
        "schedule.slot_parameter.custom_frequency.observed_values",
        "schedule.slot_parameter.pulse_tide.observed_values",
        "schedule.slot_parameter.gears.observed_values",
    }
)
OBSERVATION_SUBJECTS = frozenset(
    _OBSERVATION_L0_SUBJECTS
    | {f"{field}.observed_values" for field in _MANUAL_FIELDS | _AUTO_FIELDS}
    | _OBSERVATION_SCHEDULE_SUBJECTS
)

ALLOWED_DOCUMENT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "status",
        "absence_means",
        "physical_effect_scope",
        "reproducible_without_private_artifacts",
        "claims_digest_sha256",
        "analysis_provenance",
        "claims",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "schema_generator_commit",
        "schema_generator_source_digest_sha256",
        "raw_analyzer_commit",
        "raw_analyzer_source_digest_sha256",
        "input_artifact_digests_sha256",
    }
)
_CLAIM_REQUIRED_KEYS = frozenset(
    {
        "claim_id",
        "product_key",
        "layer",
        "goal_tier",
        "subject",
        "value",
        "evidence_tier",
        "source",
        "status",
    }
)
_CLAIM_OPTIONAL_KEYS = frozenset({"withdrawn", "use_for_current_capability"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_PRODUCT_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_CLAIM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,79}$")
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SCHEMA_SOURCE_RE = re.compile(
    r"^schema:jebao_flow\.protocol\.(profiles|schedule)@[0-9a-f]{40}$"
)
_ARTIFACT_SOURCE_RE = re.compile(
    r"^artifact:JFS-[0-9a-f]{32}#(?:all-records|[0-9]{1,6})$"
)
_RUN_SOURCE_RE = re.compile(r"^run:docs/runs/[A-Za-z0-9._-]+\.md#\S+$")
_SERIES_ID_RE = re.compile(r"^JFS-[0-9a-f]{32}$")


class CapabilityClaimError(ValueError):
    """A privacy-safe claim validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_commit(value: object) -> bool:
    return isinstance(value, str) and _COMMIT_RE.fullmatch(value) is not None


def _source_kind(value: str) -> str | None:
    if _SCHEMA_SOURCE_RE.fullmatch(value):
        return "schema"
    if _ARTIFACT_SOURCE_RE.fullmatch(value):
        return "artifact"
    if _RUN_SOURCE_RE.fullmatch(value):
        return "run"
    return None


def _normalize_claim_value(value: object) -> ClaimValue:
    if type(value) in {bool, int, str}:
        return value  # type: ignore[return-value]
    if isinstance(value, (list, tuple)):
        normalized: list[Scalar] = []
        for item in value:
            if type(item) not in {bool, int, str}:
                raise CapabilityClaimError("claim_value_type_invalid")
            normalized.append(item)  # type: ignore[arg-type]
        return tuple(normalized)
    raise CapabilityClaimError("claim_value_type_invalid")


def _scalar_sort_key(value: Scalar) -> tuple[int, object]:
    if type(value) is bool:
        return (0, int(value))
    if type(value) is int:
        return (1, value)
    return (2, value)


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    product_key: str
    layer: str
    goal_tier: str
    subject: str
    value: ClaimValue
    evidence_tier: str
    source: str
    status: str
    withdrawn: bool = False
    use_for_current_capability: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _normalize_claim_value(self.value))
        if not isinstance(self.claim_id, str) or not _CLAIM_ID_RE.fullmatch(self.claim_id):
            raise CapabilityClaimError("claim_id_invalid")
        if not isinstance(self.product_key, str) or not _PRODUCT_KEY_RE.fullmatch(
            self.product_key
        ):
            raise CapabilityClaimError("claim_product_key_invalid")
        if self.layer not in _LAYERS:
            raise CapabilityClaimError("claim_layer_invalid")
        if self.goal_tier not in _GOAL_TIERS:
            raise CapabilityClaimError("claim_goal_tier_invalid")
        if self.evidence_tier not in _EVIDENCE_TIERS:
            raise CapabilityClaimError("claim_evidence_tier_invalid")
        if self.status not in _STATUSES:
            raise CapabilityClaimError("claim_status_invalid")
        if not isinstance(self.subject, str) or not _SUBJECT_RE.fullmatch(self.subject):
            raise CapabilityClaimError("claim_subject_invalid")
        if not isinstance(self.source, str) or _source_kind(self.source) is None:
            raise CapabilityClaimError("claim_source_invalid")
        if type(self.withdrawn) is not bool:
            raise CapabilityClaimError("claim_withdrawn_invalid")
        if self.use_for_current_capability is not None and type(
            self.use_for_current_capability
        ) is not bool:
            raise CapabilityClaimError("claim_current_use_invalid")
        if self.evidence_tier == "d" and self.goal_tier != "wire":
            raise CapabilityClaimError("claim_schema_tier_goal_invalid")
        if self.goal_tier == "physical" and self.status != "UNKNOWN":
            raise CapabilityClaimError("claim_physical_status_invalid")
        if self.withdrawn and self.status != "UNKNOWN":
            raise CapabilityClaimError("claim_withdrawn_status_invalid")
        if self.evidence_tier == "a" and _source_kind(self.source) != "artifact":
            raise CapabilityClaimError("claim_raw_source_invalid")
        if self.evidence_tier == "d" and _source_kind(self.source) != "schema":
            raise CapabilityClaimError("claim_schema_source_invalid")


@dataclass(frozen=True, slots=True)
class Provenance:
    schema_generator_commit: str | None = None
    schema_generator_source_digest_sha256: str | None = None
    raw_analyzer_commit: str | None = None
    raw_analyzer_source_digest_sha256: str | None = None
    input_artifact_digests_sha256: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.schema_generator_commit, self.raw_analyzer_commit):
            if value is not None and not _is_commit(value):
                raise CapabilityClaimError("provenance_commit_invalid")
        for value in (
            self.schema_generator_source_digest_sha256,
            self.raw_analyzer_source_digest_sha256,
        ):
            if value is not None and not _is_sha256(value):
                raise CapabilityClaimError("provenance_source_digest_invalid")
        if not isinstance(self.input_artifact_digests_sha256, tuple) or any(
            not _is_sha256(value) for value in self.input_artifact_digests_sha256
        ):
            raise CapabilityClaimError("provenance_artifact_digest_invalid")
        if len(set(self.input_artifact_digests_sha256)) != len(
            self.input_artifact_digests_sha256
        ):
            raise CapabilityClaimError("provenance_artifact_digest_duplicate")


@dataclass(frozen=True, slots=True)
class ClaimSet:
    kind: Literal["schema", "observation"]
    claims: tuple[Claim, ...]
    provenance: Provenance
    reproducible_without_private_artifacts: bool

    def __post_init__(self) -> None:
        if self.kind not in {"schema", "observation"}:
            raise CapabilityClaimError("claim_set_kind_invalid")
        if not isinstance(self.claims, tuple) or any(
            not isinstance(claim, Claim) for claim in self.claims
        ):
            raise CapabilityClaimError("claim_set_claims_invalid")
        object.__setattr__(
            self,
            "claims",
            tuple(sorted(self.claims, key=lambda claim: claim.claim_id)),
        )
        if not isinstance(self.provenance, Provenance):
            raise CapabilityClaimError("claim_set_provenance_invalid")
        if type(self.reproducible_without_private_artifacts) is not bool:
            raise CapabilityClaimError("claim_set_reproducibility_invalid")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _claim_to_document(claim: Claim) -> dict[str, object]:
    document: dict[str, object] = {
        "claim_id": claim.claim_id,
        "product_key": claim.product_key,
        "layer": claim.layer,
        "goal_tier": claim.goal_tier,
        "subject": claim.subject,
        "value": list(claim.value) if isinstance(claim.value, tuple) else claim.value,
        "evidence_tier": claim.evidence_tier,
        "source": claim.source,
        "status": claim.status,
    }
    if claim.withdrawn:
        document["withdrawn"] = True
    if claim.use_for_current_capability is not None:
        document["use_for_current_capability"] = claim.use_for_current_capability
    return document


def claims_digest(claims: Sequence[Claim]) -> str:
    ordered = [
        _claim_to_document(claim)
        for claim in sorted(claims, key=lambda item: item.claim_id)
    ]
    return hashlib.sha256(canonical_json(ordered)).hexdigest()


def _provenance_to_document(provenance: Provenance) -> dict[str, object]:
    return {
        "schema_generator_commit": provenance.schema_generator_commit,
        "schema_generator_source_digest_sha256": (
            provenance.schema_generator_source_digest_sha256
        ),
        "raw_analyzer_commit": provenance.raw_analyzer_commit,
        "raw_analyzer_source_digest_sha256": provenance.raw_analyzer_source_digest_sha256,
        "input_artifact_digests_sha256": list(provenance.input_artifact_digests_sha256),
    }


def claim_set_document(claim_set: ClaimSet) -> dict[str, object]:
    validate_claim_set(claim_set)
    ordered_claims = tuple(sorted(claim_set.claims, key=lambda item: item.claim_id))
    return {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "kind": claim_set.kind,
        "status": "generated",
        "absence_means": "UNKNOWN",
        "physical_effect_scope": "out_of_scope_until_measurement_method_exists",
        "reproducible_without_private_artifacts": (
            claim_set.reproducible_without_private_artifacts
        ),
        "claims_digest_sha256": claims_digest(ordered_claims),
        "analysis_provenance": _provenance_to_document(claim_set.provenance),
        "claims": [_claim_to_document(claim) for claim in ordered_claims],
    }


def _attribute_for_claim(claim: Claim) -> Any | None:
    if "." not in claim.subject:
        return None
    field = claim.subject.split(".", 1)[0]
    if field not in _MANUAL_FIELDS | _AUTO_FIELDS:
        return None
    from jebao_flow.protocol.profiles import get_product_schema

    try:
        return get_product_schema(claim.product_key).attributes_by_name.get(field)
    except KeyError:
        return None


def _schedule_modes(product_key: str) -> tuple[str, ...]:
    from jebao_flow.protocol import schedule

    spec = schedule._SPECS.get(product_key)
    return () if spec is None else spec.modes


def _validate_string_values(claim: Claim, values: tuple[Scalar, ...]) -> None:
    strings = tuple(value for value in values if isinstance(value, str))
    if not strings:
        return
    if claim.subject == "transport_reply_action":
        if strings != ("0x03",):
            raise CapabilityClaimError("claim_transport_action_invalid")
        return
    if claim.subject.endswith(".data_type"):
        if len(strings) != 1 or strings[0] not in _DATA_TYPES:
            raise CapabilityClaimError("claim_data_type_value_invalid")
        return
    attribute = _attribute_for_claim(claim)
    if attribute is not None and attribute.enum_values:
        if any(value not in attribute.enum_values for value in strings):
            raise CapabilityClaimError("claim_enum_value_invalid")
        return
    if claim.subject in {
        "AutoMode.code_space",
        "schedule.slot_mode.observed_values",
    }:
        modes = _schedule_modes(claim.product_key)
        if not modes or any(value not in modes for value in strings):
            raise CapabilityClaimError("claim_schedule_mode_value_invalid")
        return
    raise CapabilityClaimError("claim_string_value_invalid")


def _validate_claim_value_for_subject(claim: Claim) -> None:
    value = claim.value
    values = value if isinstance(value, tuple) else (value,)
    _validate_string_values(claim, values)

    if claim.subject.endswith(".schema_range"):
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or any(type(item) is not int for item in value)
            or value[0] > value[1]
        ):
            raise CapabilityClaimError("claim_schema_range_invalid")
    elif claim.subject.endswith(".enum_values") or claim.subject.endswith(".code_space"):
        if (
            not isinstance(value, tuple)
            or not value
            or any(type(item) is not str for item in value)
            or len(set(value)) != len(value)
        ):
            raise CapabilityClaimError("claim_enum_space_invalid")
    elif claim.subject.endswith(".observed_values"):
        if not isinstance(value, tuple) or not value:
            raise CapabilityClaimError("claim_observed_values_invalid")
        if len(set(value)) != len(value) or tuple(sorted(value, key=_scalar_sort_key)) != value:
            raise CapabilityClaimError("claim_observed_values_not_canonical")
    elif claim.subject.endswith(".data_type"):
        if type(value) is not str:
            raise CapabilityClaimError("claim_data_type_value_invalid")
    elif claim.subject == "transport_reply_action":
        if value != "0x03":
            raise CapabilityClaimError("claim_transport_action_invalid")
    elif type(value) is not int:
        raise CapabilityClaimError("claim_numeric_value_invalid")


def validate_claim_set(claim_set: ClaimSet) -> None:
    claim_ids = [claim.claim_id for claim in claim_set.claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise CapabilityClaimError("claim_id_duplicate")

    provenance = claim_set.provenance
    if claim_set.kind == "schema":
        if not claim_set.reproducible_without_private_artifacts:
            raise CapabilityClaimError("schema_claim_set_reproducibility_invalid")
        if (
            provenance.schema_generator_commit is None
            or provenance.schema_generator_source_digest_sha256 is None
            or provenance.raw_analyzer_commit is not None
            or provenance.raw_analyzer_source_digest_sha256 is not None
            or provenance.input_artifact_digests_sha256
        ):
            raise CapabilityClaimError("schema_claim_set_provenance_invalid")
        allowed_subjects = SCHEMA_SUBJECTS
        for claim in claim_set.claims:
            if claim.evidence_tier != "d" or claim.goal_tier != "wire":
                raise CapabilityClaimError("schema_claim_set_tier_invalid")
    else:
        if claim_set.reproducible_without_private_artifacts:
            raise CapabilityClaimError("observation_claim_set_reproducibility_invalid")
        if (
            provenance.schema_generator_commit is not None
            or provenance.schema_generator_source_digest_sha256 is not None
            or provenance.raw_analyzer_commit is None
            or provenance.raw_analyzer_source_digest_sha256 is None
            or not provenance.input_artifact_digests_sha256
        ):
            raise CapabilityClaimError("observation_claim_set_provenance_invalid")
        allowed_subjects = OBSERVATION_SUBJECTS
        for claim in claim_set.claims:
            if claim.evidence_tier != "a" or _source_kind(claim.source) != "artifact":
                raise CapabilityClaimError("observation_claim_set_tier_invalid")

    for claim in claim_set.claims:
        if claim.subject not in allowed_subjects:
            raise CapabilityClaimError("claim_subject_not_allowed")
        _validate_claim_value_for_subject(claim)


def _document_without_safe_dynamic_strings(document: dict[str, object]) -> dict[str, object]:
    sanitized = json.loads(canonical_json(document).decode("utf-8"))
    sanitized["claims_digest_sha256"] = "<digest>"
    provenance = sanitized["analysis_provenance"]
    for key in _PROVENANCE_KEYS:
        provenance[key] = [] if key == "input_artifact_digests_sha256" else "<provenance>"
    for claim in sanitized["claims"]:
        claim["claim_id"] = "<claim-id>"
        claim["product_key"] = "<product-key>"
        claim["source"] = "<source>"
    return sanitized


def assert_publishable(claim_set: ClaimSet, payload: bytes) -> None:
    validate_claim_set(claim_set)
    try:
        loaded = yaml.safe_load(payload)
    except yaml.YAMLError as error:
        raise CapabilityClaimError("claim_set_yaml_invalid") from error
    expected = claim_set_document(claim_set)
    if loaded != expected:
        raise CapabilityClaimError("claim_set_serialization_mismatch")
    if not isinstance(loaded, dict) or frozenset(loaded) != ALLOWED_DOCUMENT_KEYS:
        raise CapabilityClaimError("claim_set_document_keys_invalid")
    provenance = loaded.get("analysis_provenance")
    if not isinstance(provenance, dict) or frozenset(provenance) != _PROVENANCE_KEYS:
        raise CapabilityClaimError("claim_set_provenance_keys_invalid")
    for claim in loaded.get("claims", []):
        if not isinstance(claim, dict):
            raise CapabilityClaimError("claim_document_invalid")
        keys = frozenset(claim)
        if not _CLAIM_REQUIRED_KEYS <= keys or not keys <= (
            _CLAIM_REQUIRED_KEYS | _CLAIM_OPTIONAL_KEYS
        ):
            raise CapabilityClaimError("claim_document_keys_invalid")

    sanitized = canonical_json(_document_without_safe_dynamic_strings(loaded)).decode("utf-8")
    forbidden_patterns = (
        r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}",
        r"(?<![0-9])(?:10|127|169\.254|172\.(?:1[6-9]|2[0-9]|3[01])|192\.168)"
        r"(?:\.[0-9]{1,3}){2}(?![0-9])",
        r"(?<![A-Za-z0-9])/(?:Users|home|private|srv|var|opt|mnt|data|tmp)/",
        r"[A-Za-z]:\\",
        r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{24,}(?![0-9A-Fa-f])",
    )
    if any(re.search(pattern, sanitized) for pattern in forbidden_patterns):
        raise CapabilityClaimError("claim_set_private_value_detected")


def render_yaml(claim_set: ClaimSet) -> bytes:
    document = claim_set_document(claim_set)
    payload = yaml.safe_dump(
        document,
        sort_keys=True,
        allow_unicode=True,
        width=1_000_000,
    ).encode("utf-8")
    assert_publishable(claim_set, payload)
    return payload


def _claim_from_document(document: object) -> Claim:
    if not isinstance(document, dict):
        raise CapabilityClaimError("claim_document_invalid")
    keys = frozenset(document)
    if not _CLAIM_REQUIRED_KEYS <= keys or not keys <= (
        _CLAIM_REQUIRED_KEYS | _CLAIM_OPTIONAL_KEYS
    ):
        raise CapabilityClaimError("claim_document_keys_invalid")
    return Claim(
        claim_id=document["claim_id"],
        product_key=document["product_key"],
        layer=document["layer"],
        goal_tier=document["goal_tier"],
        subject=document["subject"],
        value=document["value"],
        evidence_tier=document["evidence_tier"],
        source=document["source"],
        status=document["status"],
        withdrawn=document.get("withdrawn", False),
        use_for_current_capability=document.get("use_for_current_capability"),
    )


def load_claim_set(path: Path) -> ClaimSet:
    try:
        payload = path.read_bytes()
        document = yaml.safe_load(payload)
    except (OSError, yaml.YAMLError) as error:
        raise CapabilityClaimError("claim_set_file_invalid") from error
    if not isinstance(document, dict) or frozenset(document) != ALLOWED_DOCUMENT_KEYS:
        raise CapabilityClaimError("claim_set_document_keys_invalid")
    if (
        document.get("schema_version") != CLAIM_SCHEMA_VERSION
        or document.get("status") != "generated"
        or document.get("absence_means") != "UNKNOWN"
        or document.get("physical_effect_scope")
        != "out_of_scope_until_measurement_method_exists"
    ):
        raise CapabilityClaimError("claim_set_header_invalid")
    provenance_document = document.get("analysis_provenance")
    if not isinstance(provenance_document, dict) or frozenset(
        provenance_document
    ) != _PROVENANCE_KEYS:
        raise CapabilityClaimError("claim_set_provenance_keys_invalid")
    input_digests = provenance_document.get("input_artifact_digests_sha256")
    if not isinstance(input_digests, list):
        raise CapabilityClaimError("claim_set_provenance_invalid")
    claims_document = document.get("claims")
    if not isinstance(claims_document, list):
        raise CapabilityClaimError("claim_set_claims_invalid")
    claim_set = ClaimSet(
        kind=document.get("kind"),
        claims=tuple(_claim_from_document(item) for item in claims_document),
        provenance=Provenance(
            schema_generator_commit=provenance_document.get("schema_generator_commit"),
            schema_generator_source_digest_sha256=provenance_document.get(
                "schema_generator_source_digest_sha256"
            ),
            raw_analyzer_commit=provenance_document.get("raw_analyzer_commit"),
            raw_analyzer_source_digest_sha256=provenance_document.get(
                "raw_analyzer_source_digest_sha256"
            ),
            input_artifact_digests_sha256=tuple(input_digests),
        ),
        reproducible_without_private_artifacts=document.get(
            "reproducible_without_private_artifacts"
        ),
    )
    if document.get("claims_digest_sha256") != claims_digest(claim_set.claims):
        raise CapabilityClaimError("claim_set_digest_mismatch")
    assert_publishable(claim_set, payload)
    return claim_set


def _matrix_product_keys(document: object) -> frozenset[str]:
    if not isinstance(document, dict):
        raise CapabilityClaimError("capability_matrix_invalid")
    products = document.get("products")
    if not isinstance(products, list) or not products:
        raise CapabilityClaimError("capability_matrix_products_invalid")
    keys: list[str] = []
    for product in products:
        if not isinstance(product, dict):
            raise CapabilityClaimError("capability_matrix_products_invalid")
        product_key = product.get("product_key")
        if not isinstance(product_key, str) or not _PRODUCT_KEY_RE.fullmatch(product_key):
            raise CapabilityClaimError("capability_matrix_product_key_invalid")
        keys.append(product_key)
    if len(set(keys)) != len(keys):
        raise CapabilityClaimError("capability_matrix_product_duplicate")
    return frozenset(keys)


def aggregate(matrix: Path, generated: Sequence[Path]) -> tuple[Claim, ...]:
    try:
        matrix_document = yaml.safe_load(matrix.read_bytes())
    except (OSError, yaml.YAMLError) as error:
        raise CapabilityClaimError("capability_matrix_invalid") from error
    product_keys = _matrix_product_keys(matrix_document)
    matrix_claims_document = matrix_document.get("claims")
    if not isinstance(matrix_claims_document, list):
        raise CapabilityClaimError("capability_matrix_claims_invalid")
    claims = [_claim_from_document(item) for item in matrix_claims_document]
    claim_sets = [load_claim_set(path) for path in generated]
    schema_sets = [claim_set for claim_set in claim_sets if claim_set.kind == "schema"]
    if len(schema_sets) != 1:
        raise CapabilityClaimError("schema_claim_set_count_invalid")
    if {claim.product_key for claim in schema_sets[0].claims} != product_keys:
        raise CapabilityClaimError("schema_claim_set_product_scope_invalid")
    for claim_set in claim_sets:
        claim_products = {claim.product_key for claim in claim_set.claims}
        if not claim_products <= product_keys:
            raise CapabilityClaimError("claim_set_product_scope_invalid")
        claims.extend(claim_set.claims)
    if any(claim.product_key not in product_keys for claim in claims):
        raise CapabilityClaimError("claim_product_not_preregistered")
    claim_ids = [claim.claim_id for claim in claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise CapabilityClaimError("claim_id_duplicate")
    return tuple(sorted(claims, key=lambda item: item.claim_id))


def verify_source_pin(
    repo_root: Path,
    commit_sha: str,
    sources: Sequence[SourcePin],
    *,
    self_module_name: str | None = None,
    self_module_file: str | Path | None = None,
) -> str:
    if not _is_commit(commit_sha):
        raise CapabilityClaimError("source_pin_commit_invalid")
    try:
        root = repo_root.resolve(strict=True)
    except OSError as error:
        raise CapabilityClaimError("source_pin_input_invalid") from error
    if (
        not root.is_dir()
        or not sources
        or any(
            not isinstance(source, tuple)
            or len(source) != 2
            or not all(isinstance(item, str) and item for item in source)
            for source in sources
        )
        or len({module_name for module_name, _ in sources}) != len(sources)
        or len({relative for _, relative in sources}) != len(sources)
        or (self_module_name is None) != (self_module_file is None)
        or (
            self_module_name is not None
            and self_module_name not in {module_name for module_name, _ in sources}
        )
    ):
        raise CapabilityClaimError("source_pin_input_invalid")
    source_records: list[dict[str, str]] = []
    for module_name, relative in sources:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise CapabilityClaimError("source_pin_path_invalid")
        local_path = root / relative_path
        try:
            metadata = local_path.lstat()
            local_bytes = local_path.read_bytes()
            expected_module_path = local_path.resolve(strict=True)
        except OSError as error:
            raise CapabilityClaimError("source_pin_local_file_invalid") from error
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise CapabilityClaimError("source_pin_local_file_invalid")

        module = sys.modules.get(module_name)
        if module is None:
            if module_name != self_module_name:
                raise CapabilityClaimError("source_pin_module_not_loaded")
            reported_source = self_module_file
        else:
            reported_source = getattr(module, "__file__", None)
        if not isinstance(reported_source, (str, Path)):
            raise CapabilityClaimError("source_pin_module_path_invalid")
        try:
            loaded_module_path = Path(reported_source).resolve(strict=True)
        except OSError as error:
            raise CapabilityClaimError("source_pin_module_path_invalid") from error
        if loaded_module_path != expected_module_path:
            raise CapabilityClaimError("source_pin_module_path_mismatch")

        try:
            result = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", f"{commit_sha}:{relative}"],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise CapabilityClaimError("source_pin_commit_blob_invalid") from error
        if result.stdout != local_bytes:
            raise CapabilityClaimError("source_pin_blob_mismatch")
        source_records.append(
            {
                "module": module_name,
                "path": relative,
                "sha256": hashlib.sha256(local_bytes).hexdigest(),
            }
        )
    return hashlib.sha256(canonical_json(source_records)).hexdigest()


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise CapabilityClaimError("claim_set_write_failed") from error


def write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.exclusive.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        temporary.unlink()
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as error:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise CapabilityClaimError("claim_set_output_exists") from error
    except OSError as error:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise CapabilityClaimError("claim_set_write_failed") from error


def schema_claim_id(product_key: str, subject: str) -> str:
    slug = subject.lower().replace("_", "-")
    claim_id = f"schema-{product_key[:8]}-{slug}"
    if not _CLAIM_ID_RE.fullmatch(claim_id):
        raise CapabilityClaimError("claim_id_invalid")
    return claim_id


def observation_claim_id(series_id: str, subject: str) -> str:
    if not _SERIES_ID_RE.fullmatch(series_id):
        raise CapabilityClaimError("series_id_invalid")
    slug = subject.lower().replace("_", "-")
    claim_id = f"obs-{series_id[4:16]}-{slug}"
    if not _CLAIM_ID_RE.fullmatch(claim_id):
        raise CapabilityClaimError("claim_id_invalid")
    return claim_id


def observation_output_name(series_id: str) -> str:
    if not _SERIES_ID_RE.fullmatch(series_id):
        raise CapabilityClaimError("series_id_invalid")
    return f"observation-claim-set.{series_id}.generated.yaml"


def product_keys_from_matrix(matrix: Path) -> frozenset[str]:
    try:
        document = yaml.safe_load(matrix.read_bytes())
    except (OSError, yaml.YAMLError) as error:
        raise CapabilityClaimError("capability_matrix_invalid") from error
    return _matrix_product_keys(document)


def ensure_unique_claim_ids(claims: Iterable[Claim]) -> None:
    claim_ids = [claim.claim_id for claim in claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise CapabilityClaimError("claim_id_duplicate")
