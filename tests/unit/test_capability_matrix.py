from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jebao_flow.capability_matrix import (
    OBSERVATION_SUBJECTS,
    SCHEMA_SUBJECTS,
    CapabilityClaimError,
    Claim,
    ClaimSet,
    Provenance,
    aggregate,
    assert_publishable,
    claims_digest,
    load_claim_set,
    observation_claim_id,
    render_yaml,
    schema_claim_id,
    verify_source_pin,
    write_exclusive,
)

PRO_PRODUCT = "50dbc92221fd4d33ae69a1fedd43b555"
BAR_PRODUCT = "1d8c63eaccac4205b92c84d77d5a08fb"
SERIES_ID = "JFS-a2f44ded609b34adab1425c1dcc40c0e"


def _schema_claim(product_key: str, *, subject: str = "raw_status_size_bytes") -> Claim:
    return Claim(
        claim_id=schema_claim_id(product_key, subject),
        product_key=product_key,
        layer="L0",
        goal_tier="wire",
        subject=subject,
        value=452 if product_key == PRO_PRODUCT else 401,
        evidence_tier="d",
        source=f"schema:jebao_flow.protocol.profiles@{'a' * 40}",
        status="PASS",
    )


def _schema_set(*claims: Claim) -> ClaimSet:
    return ClaimSet(
        kind="schema",
        claims=tuple(claims),
        provenance=Provenance(
            schema_generator_commit="a" * 40,
            schema_generator_source_digest_sha256="b" * 64,
        ),
        reproducible_without_private_artifacts=True,
    )


def _observation_set() -> ClaimSet:
    claim = Claim(
        claim_id=observation_claim_id(SERIES_ID, "Flow.observed_values"),
        product_key=PRO_PRODUCT,
        layer="L1",
        goal_tier="accepted",
        subject="Flow.observed_values",
        value=(30, 50),
        evidence_tier="a",
        source=f"artifact:{SERIES_ID}#all-records",
        status="PASS",
    )
    return ClaimSet(
        kind="observation",
        claims=(claim,),
        provenance=Provenance(
            raw_analyzer_commit="c" * 40,
            raw_analyzer_source_digest_sha256="d" * 64,
            input_artifact_digests_sha256=("e" * 64,),
        ),
        reproducible_without_private_artifacts=False,
    )


def test_claim_set_round_trip_and_digest_ignore_order_and_provenance(tmp_path: Path) -> None:
    first = _schema_claim(PRO_PRODUCT)
    second = _schema_claim(BAR_PRODUCT)
    claim_set = _schema_set(first, second)
    path = tmp_path / "claims.yaml"
    payload = render_yaml(claim_set)
    path.write_bytes(payload)

    assert load_claim_set(path) == claim_set
    assert claims_digest((first, second)) == claims_digest((second, first))
    changed_provenance = replace(
        claim_set,
        provenance=replace(
            claim_set.provenance,
            schema_generator_commit="f" * 40,
        ),
    )
    assert claims_digest(changed_provenance.claims) == claims_digest(claim_set.claims)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("claim_id", "bad_id", "claim_id_invalid"),
        ("product_key", "not-a-product", "claim_product_key_invalid"),
        ("layer", "L4", "claim_layer_invalid"),
        ("source", "/private/device", "claim_source_invalid"),
        ("value", {"nested": True}, "claim_value_type_invalid"),
    ),
)
def test_claim_rejects_values_outside_closed_grammar(
    field: str,
    value: object,
    code: str,
) -> None:
    values = {
        "claim_id": "schema-50dbc922-raw-status-size-bytes",
        "product_key": PRO_PRODUCT,
        "layer": "L0",
        "goal_tier": "wire",
        "subject": "raw_status_size_bytes",
        "value": 452,
        "evidence_tier": "d",
        "source": f"schema:jebao_flow.protocol.profiles@{'a' * 40}",
        "status": "PASS",
    }
    values[field] = value
    with pytest.raises(CapabilityClaimError, match=code):
        Claim(**values)


def test_observed_values_must_be_unique_and_canonical() -> None:
    claim_set = _observation_set()
    claim = claim_set.claims[0]
    for invalid in ((50, 30), (30, 30)):
        with pytest.raises(CapabilityClaimError, match="claim_observed_values"):
            render_yaml(replace(claim_set, claims=(replace(claim, value=invalid),)))


def test_claim_sets_reject_tier_cross_contamination_in_both_directions() -> None:
    schema_claim = _schema_claim(PRO_PRODUCT)
    raw_in_schema = replace(
        schema_claim,
        evidence_tier="a",
        source=f"artifact:{SERIES_ID}#all-records",
    )
    with pytest.raises(CapabilityClaimError, match="schema_claim_set_tier_invalid"):
        render_yaml(_schema_set(raw_in_schema))

    observation_set = _observation_set()
    observation_claim = observation_set.claims[0]
    schema_in_observation = replace(
        observation_claim,
        goal_tier="wire",
        evidence_tier="d",
        source=f"schema:jebao_flow.protocol.profiles@{'a' * 40}",
    )
    with pytest.raises(
        CapabilityClaimError,
        match="observation_claim_set_tier_invalid",
    ):
        render_yaml(replace(observation_set, claims=(schema_in_observation,)))


def test_observation_claim_set_rejects_causal_active_slot_subject() -> None:
    claim_set = _observation_set()
    prohibited = replace(
        claim_set.claims[0],
        claim_id="obs-a2f44ded609b-active-slot-matches-auto-fields",
        subject="schedule.active_slot_matches_auto_fields",
        value=(True,),
    )
    with pytest.raises(CapabilityClaimError, match="claim_subject_not_allowed"):
        render_yaml(replace(claim_set, claims=(prohibited,)))


def test_publishability_rejects_payload_not_owned_by_claim_set() -> None:
    claim_set = _observation_set()
    payload = render_yaml(claim_set)
    contaminated = payload.replace(b"Flow.observed_values", b"/Users/private/state")
    with pytest.raises(CapabilityClaimError, match="claim_set_serialization_mismatch"):
        assert_publishable(claim_set, contaminated)


def test_aggregate_enforces_preregistered_product_scope_and_global_ids(
    tmp_path: Path,
) -> None:
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(
        yaml.safe_dump(
            {
                "products": [
                    {"product_key": PRO_PRODUCT},
                    {"product_key": BAR_PRODUCT},
                ],
                "claims": [],
            }
        ),
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_bytes(
        render_yaml(_schema_set(_schema_claim(PRO_PRODUCT), _schema_claim(BAR_PRODUCT)))
    )
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_bytes(render_yaml(_observation_set()))

    combined = aggregate(matrix, (schema_path, observation_path))
    assert len(combined) == 3

    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_bytes(render_yaml(_schema_set(_schema_claim(PRO_PRODUCT))))
    with pytest.raises(CapabilityClaimError, match="schema_claim_set_product_scope_invalid"):
        aggregate(matrix, (incomplete,))


def test_claim_ids_fit_the_closed_grammar_for_every_allowed_subject() -> None:
    for product_key in (PRO_PRODUCT, BAR_PRODUCT):
        for subject in SCHEMA_SUBJECTS:
            claim_id = schema_claim_id(product_key, subject)
            assert len(claim_id) <= 80
            assert "_" not in claim_id
    for subject in OBSERVATION_SUBJECTS:
        claim_id = observation_claim_id(SERIES_ID, subject)
        assert len(claim_id) <= 80
        assert "_" not in claim_id


def test_verify_source_pin_binds_loaded_modules_and_compares_every_named_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    sources = (("test.pin.one", "src/one.py"), ("test.pin.two", "src/two.py"))
    for index, (module_name, relative) in enumerate(sources):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        monkeypatch.setitem(
            sys.modules,
            module_name,
            SimpleNamespace(__file__=str(path)),
        )
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Capability Test",
            "-c",
            "user.email=capability@example.invalid",
            "commit",
            "-qm",
            "test sources",
        ],
        cwd=repository,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    digest = verify_source_pin(repository, commit, sources)
    assert len(digest) == 64

    monkeypatch.delitem(sys.modules, sources[0][0])
    assert (
        verify_source_pin(
            repository,
            commit,
            sources,
            self_module_name=sources[0][0],
            self_module_file=repository / sources[0][1],
        )
        == digest
    )
    monkeypatch.setitem(
        sys.modules,
        sources[0][0],
        SimpleNamespace(__file__=str(repository / sources[0][1])),
    )

    stale = tmp_path / "stale.py"
    stale.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        sources[1][0],
        SimpleNamespace(__file__=str(stale)),
    )
    with pytest.raises(CapabilityClaimError, match="source_pin_module_path_mismatch"):
        verify_source_pin(repository, commit, sources)

    monkeypatch.setitem(
        sys.modules,
        sources[1][0],
        SimpleNamespace(__file__=str(repository / sources[1][1])),
    )
    (repository / sources[1][1]).write_text("VALUE = 99\n", encoding="utf-8")
    with pytest.raises(CapabilityClaimError, match="source_pin_blob_mismatch"):
        verify_source_pin(repository, commit, sources)


def test_exclusive_output_never_overwrites_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "observation.yaml"
    write_exclusive(output, b"first")
    with pytest.raises(CapabilityClaimError, match="claim_set_output_exists"):
        write_exclusive(output, b"second")
    assert output.read_bytes() == b"first"


def test_capability_modules_are_not_added_to_v2_attestation_sources() -> None:
    from jebao_flow import source_attestation

    source_text = Path("src/jebao_flow/source_attestation.py").read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    assert tree is not None
    tracked = "\n".join(
        relative for _, relative in source_attestation._RUNTIME_MODULE_SOURCES
    )
    assert "capability_" not in tracked


def test_claim_digest_is_the_sha256_of_canonical_claim_list() -> None:
    claim = _schema_claim(PRO_PRODUCT)
    expected_document = [
        {
            "claim_id": claim.claim_id,
            "product_key": claim.product_key,
            "layer": claim.layer,
            "goal_tier": claim.goal_tier,
            "subject": claim.subject,
            "value": claim.value,
            "evidence_tier": claim.evidence_tier,
            "source": claim.source,
            "status": claim.status,
        }
    ]
    expected = hashlib.sha256(
        json.dumps(
            expected_document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert claims_digest((claim,)) == expected
