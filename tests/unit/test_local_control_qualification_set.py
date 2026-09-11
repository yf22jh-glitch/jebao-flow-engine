from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    load_current_qualifications,
    qualification_receipt_bytes,
)
from jebao_flow.local_control.qualification_set import (
    ExpectedQualificationAddition,
    QualificationSetMergeError,
    QualificationSetMerger,
    expected_additions_from_manifest,
    load_current_manifest,
)

NOW = datetime(2026, 9, 11, 9, tzinfo=UTC)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700, parents=True)
    return root


def _binding(
    root: Path,
    *,
    product: str = "1",
    function: str = "manual_flow",
    identities_and_requests: tuple[tuple[str, str], ...],
    firmware: str = "firmware-current",
    template: str = "2",
    reverse_receipts: bool = False,
) -> QualificationBinding:
    receipts: list[LocalControlQualificationReceipt] = []
    for identity, request_id in identities_and_requests:
        marker = f"{product}:{function}:{identity}:{request_id}"
        receipts.append(
            LocalControlQualificationReceipt(
                product_key=product * 32,
                function=function,
                physical_identity_key=identity * 64,
                app_version="3.4.18",
                app_catalog_sha256="3" * 64,
                template_sha256=template * 64,
                schema_sha256="4" * 64,
                firmware_version=firmware,
                request_id=request_id,
                intent_sha256=hashlib.sha256(f"intent:{marker}".encode()).hexdigest(),
                control_payload_sha256=hashlib.sha256(
                    f"control:{marker}".encode()
                ).hexdigest(),
                acknowledgement_sha256=hashlib.sha256(f"ack:{marker}".encode()).hexdigest(),
                readback_sha256=hashlib.sha256(f"readback:{marker}".encode()).hexdigest(),
                completed_at=NOW,
            )
        )
    payloads = tuple(qualification_receipt_bytes(item) for item in receipts)
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    for digest, payload in zip(digests, payloads, strict=True):
        path = root / f"local-control-receipt-{digest}.json"
        if not path.exists():
            path.write_bytes(payload)
            path.chmod(0o600)
    if reverse_receipts:
        digests = tuple(reversed(digests))
    return QualificationBinding(
        product_key=product * 32,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="3" * 64,
        template_sha256=template * 64,
        schema_sha256="4" * 64,
        firmware_version=firmware,
        receipt_sha256=digests,
    )


def _addition(
    *,
    product: str = "1",
    function: str = "manual_flow",
    identity: str,
) -> ExpectedQualificationAddition:
    return ExpectedQualificationAddition(
        product_key=product * 32,
        function=function,
        physical_identity_key=identity * 64,
    )


def _merger(tmp_path: Path, root: Path, name: str = "merged.json") -> QualificationSetMerger:
    return QualificationSetMerger.for_test(root, tmp_path / "private" / name)


def test_absent_current_creates_verified_candidate_only_set(tmp_path: Path) -> None:
    root = _root(tmp_path)
    candidate = _binding(
        root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
        reverse_receipts=True,
    )
    merger = _merger(tmp_path, root)

    result = merger.merge((), (candidate,), (_addition(identity="a"),))

    loaded = load_current_qualifications(
        merger.output_path,
        _allow_ephemeral_for_test=True,
    )
    assert len(loaded) == 1
    assert loaded[0].receipt_sha256 == tuple(sorted(candidate.receipt_sha256))
    assert result.binding_count == 1
    assert result.receipt_count == 2
    assert result.qualified_pair_count == 1
    assert result.added_pair_count == 1
    assert result.output_sha256 == hashlib.sha256(merger.output_path.read_bytes()).hexdigest()


def test_same_binding_merges_receipts_and_preserves_existing_surface(tmp_path: Path) -> None:
    root = _root(tmp_path)
    current = _binding(
        root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
    )
    candidate = _binding(
        root,
        identities_and_requests=(("b", "request_b_first"), ("b", "request_b_second")),
        reverse_receipts=True,
    )
    merger = _merger(tmp_path, root)

    result = merger.merge((current,), (candidate,), (_addition(identity="b"),))

    loaded = load_current_qualifications(
        merger.output_path,
        _allow_ephemeral_for_test=True,
    )
    assert loaded[0].receipt_sha256 == tuple(
        sorted((*current.receipt_sha256, *candidate.receipt_sha256))
    )
    assert set(current.receipt_sha256).issubset(loaded[0].receipt_sha256)
    assert result.receipt_count == 4
    assert result.qualified_pair_count == 2


def test_same_binding_with_firmware_drift_is_rejected_before_output(tmp_path: Path) -> None:
    root = _root(tmp_path)
    current = _binding(
        root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
        firmware="firmware-left",
    )
    candidate = _binding(
        root,
        identities_and_requests=(("b", "request_b_first"), ("b", "request_b_second")),
        firmware="firmware-right",
    )
    merger = _merger(tmp_path, root)

    with pytest.raises(QualificationSetMergeError, match="version or content"):
        merger.merge((current,), (candidate,), (_addition(identity="b"),))

    assert not merger.output_path.exists()


def test_declared_surface_rejects_both_extra_and_missing_qualified_pairs(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    current = _binding(
        root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
    )
    extra_candidate = _binding(
        root,
        identities_and_requests=(
            ("b", "request_b_first"),
            ("b", "request_b_second"),
            ("c", "request_c_first"),
            ("c", "request_c_second"),
        ),
    )
    extra_merger = _merger(tmp_path, root, "extra.json")
    with pytest.raises(QualificationSetMergeError, match="surface"):
        extra_merger.merge((current,), (extra_candidate,), (_addition(identity="b"),))
    assert not extra_merger.output_path.exists()

    missing_candidate = _binding(
        root,
        identities_and_requests=(("b", "request_b_first"), ("b", "request_b_second")),
    )
    missing_merger = _merger(tmp_path, root, "missing.json")
    with pytest.raises(QualificationSetMergeError, match="surface"):
        missing_merger.merge(
            (current,),
            (missing_candidate,),
            (_addition(identity="b"), _addition(identity="c")),
        )
    assert not missing_merger.output_path.exists()


def test_new_binding_is_additive_and_existing_evidence_is_preserved(tmp_path: Path) -> None:
    root = _root(tmp_path)
    current = _binding(
        root,
        product="1",
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
        reverse_receipts=True,
    )
    candidate = _binding(
        root,
        product="2",
        identities_and_requests=(("c", "request_c_first"), ("c", "request_c_second")),
        template="5",
    )
    merger = _merger(tmp_path, root)

    merger.merge(
        (current,),
        (candidate,),
        (_addition(product="2", identity="c"),),
    )

    loaded = load_current_qualifications(
        merger.output_path,
        _allow_ephemeral_for_test=True,
    )
    assert tuple((item.product_key, item.function) for item in loaded) == (
        ("1" * 32, "manual_flow"),
        ("2" * 32, "manual_flow"),
    )
    preserved = loaded[0]
    assert preserved.model_dump(exclude={"receipt_sha256"}) == current.model_dump(
        exclude={"receipt_sha256"}
    )
    assert preserved.receipt_sha256 == tuple(sorted(current.receipt_sha256))


def test_request_ids_must_be_distinct_across_the_whole_result(tmp_path: Path) -> None:
    root = _root(tmp_path)
    current = _binding(
        root,
        product="1",
        identities_and_requests=(("a", "request_shared_1"), ("a", "request_shared_2")),
    )
    candidate = _binding(
        root,
        product="2",
        identities_and_requests=(("c", "request_shared_1"), ("c", "request_shared_2")),
        template="5",
    )
    merger = _merger(tmp_path, root)

    with pytest.raises(QualificationSetMergeError, match="globally distinct"):
        merger.merge(
            (current,),
            (candidate,),
            (_addition(product="2", identity="c"),),
        )

    assert not merger.output_path.exists()


def test_input_and_receipt_order_do_not_change_output_bytes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = _binding(
        root,
        product="1",
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
        reverse_receipts=True,
    )
    second = _binding(
        root,
        product="2",
        identities_and_requests=(("c", "request_c_first"), ("c", "request_c_second")),
        template="5",
        reverse_receipts=True,
    )
    additions = (_addition(product="1", identity="a"), _addition(product="2", identity="c"))
    left = _merger(tmp_path, root, "left.json")
    right = _merger(tmp_path, root, "right.json")

    left.merge((), (second, first), tuple(reversed(additions)))
    right.merge(
        (),
        (
            first.model_copy(update={"receipt_sha256": tuple(reversed(first.receipt_sha256))}),
            second.model_copy(update={"receipt_sha256": tuple(reversed(second.receipt_sha256))}),
        ),
        additions,
    )

    assert left.output_path.read_bytes() == right.output_path.read_bytes()


def test_identical_output_is_accepted_but_different_existing_bytes_are_untouched(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    candidate = _binding(
        root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
    )
    merger = _merger(tmp_path, root)
    additions = (_addition(identity="a"),)

    first = merger.merge((), (candidate,), additions)
    before = merger.output_path.read_bytes()
    second = merger.merge((), (candidate,), additions)

    assert first == second
    assert merger.output_path.read_bytes() == before

    different = _merger(tmp_path, root, "different.json")
    different.output_path.write_bytes(b"different\n")
    different.output_path.chmod(0o600)
    with pytest.raises(QualificationSetMergeError, match="differs"):
        different.merge((), (candidate,), additions)
    assert different.output_path.read_bytes() == b"different\n"


def test_missing_receipt_and_unsafe_output_metadata_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    missing = QualificationBinding(
        product_key="1" * 32,
        function="manual_flow",
        app_version="3.4.18",
        app_catalog_sha256="3" * 64,
        template_sha256="2" * 64,
        schema_sha256="4" * 64,
        firmware_version="firmware-current",
        receipt_sha256=("5" * 64, "6" * 64),
    )
    merger = _merger(tmp_path, root, "missing-receipt.json")
    with pytest.raises(QualificationSetMergeError, match="verification failed"):
        merger.merge((), (missing,), (_addition(identity="a"),))
    assert not merger.output_path.exists()

    unsafe_parent = tmp_path / "unsafe"
    unsafe_parent.mkdir(mode=0o755)
    with pytest.raises(QualificationSetMergeError, match="directory metadata"):
        QualificationSetMerger.for_test(root, unsafe_parent / "set.json")


def test_changed_digest_and_noncanonical_receipts_are_rejected(tmp_path: Path) -> None:
    digest_root = _root(tmp_path / "digest")
    digest_candidate = _binding(
        digest_root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
    )
    digest_path = digest_root / (
        f"local-control-receipt-{digest_candidate.receipt_sha256[0]}.json"
    )
    digest_path.write_bytes(b"changed\n")
    with pytest.raises(QualificationSetMergeError, match="verification failed"):
        _merger(tmp_path, digest_root, "changed-digest.json").merge(
            (),
            (digest_candidate,),
            (_addition(identity="a"),),
        )

    canonical_root = _root(tmp_path / "canonical")
    canonical_candidate = _binding(
        canonical_root,
        identities_and_requests=(("b", "request_b_first"), ("b", "request_b_second")),
    )
    original_path = canonical_root / (
        f"local-control-receipt-{canonical_candidate.receipt_sha256[0]}.json"
    )
    noncanonical = json.dumps(
        json.loads(original_path.read_bytes()),
        indent=2,
    ).encode("ascii") + b"\n"
    noncanonical_digest = hashlib.sha256(noncanonical).hexdigest()
    noncanonical_path = canonical_root / (
        f"local-control-receipt-{noncanonical_digest}.json"
    )
    noncanonical_path.write_bytes(noncanonical)
    noncanonical_path.chmod(0o600)
    noncanonical_candidate = canonical_candidate.model_copy(
        update={
            "receipt_sha256": (
                noncanonical_digest,
                canonical_candidate.receipt_sha256[1],
            )
        }
    )
    with pytest.raises(QualificationSetMergeError, match="verification failed"):
        _merger(tmp_path, canonical_root, "noncanonical.json").merge(
            (),
            (noncanonical_candidate,),
            (_addition(identity="b"),),
        )


def test_duplicate_request_inside_candidate_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    candidate = _binding(
        root,
        identities_and_requests=(
            ("a", "request_duplicate"),
            ("b", "request_duplicate"),
        ),
    )
    merger = _merger(tmp_path, root)

    with pytest.raises(QualificationSetMergeError, match="verification failed"):
        merger.merge((), (candidate,), (_addition(identity="a"),))

    assert not merger.output_path.exists()


def test_symlink_hardlink_and_fsync_fault_never_report_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "target.json"
    target.write_bytes(b"target\n")
    target.chmod(0o600)
    symlink = private / "symlink.json"
    symlink.symlink_to(target)
    with pytest.raises(QualificationSetMergeError, match="symlinked"):
        QualificationSetMerger.for_test(root, symlink)

    hardlink = private / "hardlink.json"
    hardlink.hardlink_to(target)
    candidate = _binding(
        root,
        identities_and_requests=(("a", "request_a_first"), ("a", "request_a_second")),
    )
    with pytest.raises(QualificationSetMergeError, match="metadata"):
        QualificationSetMerger.for_test(root, hardlink).merge(
            (),
            (candidate,),
            (_addition(identity="a"),),
        )

    unsafe_file = private / "unsafe-file.json"
    unsafe_file.write_bytes(b"different\n")
    unsafe_file.chmod(0o644)
    with pytest.raises(QualificationSetMergeError, match="metadata"):
        QualificationSetMerger.for_test(root, unsafe_file).merge(
            (),
            (candidate,),
            (_addition(identity="a"),),
        )

    fsync_merger = _merger(tmp_path, root, "fsync.json")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync fault")

    monkeypatch.setattr("jebao_flow.local_control.qualification_set.os.fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync fault"):
        fsync_merger.merge((), (candidate,), (_addition(identity="a"),))
    assert not fsync_merger.output_path.exists()


def test_manifest_resolves_logical_member_without_accepting_a_raw_key(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    manifest_path = private / "manifest.json"
    manifest = FreshInstallManifest(
        generation="7" * 32,
        rederived_at=NOW,
        devices=(
            LocalDeviceBinding(
                logical_member="flow_left",
                product_key="1" * 32,
                physical_identity_key="a" * 64,
                logical_role="left",
                role_attested_by="operator",
                role_attested_at=NOW,
            ),
        ),
        content=InstallContentBindings(
            app_catalog_sha256="3" * 64,
            protocol_schema_sha256="4" * 64,
            template_sha256=("2" * 64,),
        ),
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="ascii")
    manifest_path.chmod(0o600)

    loaded = load_current_manifest(manifest_path, _allow_ephemeral_for_test=True)
    additions = expected_additions_from_manifest(loaded, (("flow_left", "manual_flow"),))

    assert additions == (_addition(identity="a"),)
    with pytest.raises(QualificationSetMergeError, match="exactly resolved"):
        expected_additions_from_manifest(loaded, (("missing", "manual_flow"),))
    with pytest.raises(QualificationSetMergeError, match="duplicates"):
        expected_additions_from_manifest(
            loaded,
            (("flow_left", "manual_flow"), ("flow_left", "manual_flow")),
        )
