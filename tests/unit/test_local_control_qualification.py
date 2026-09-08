import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jebao_flow.local_control.models import QualificationBinding
from jebao_flow.local_control.qualification import (
    LocalControlQualificationReceipt,
    QualificationStoreError,
    load_current_qualifications,
    qualification_receipt_bytes,
    qualified_physical_identity_keys,
    verify_qualification_receipts,
)


def _qualification() -> QualificationBinding:
    return QualificationBinding(
        product_key="1" * 32,
        function="manual_flow",
        app_version="3.4.18",
        app_catalog_sha256="0" * 64,
        template_sha256="2" * 64,
        schema_sha256="3" * 64,
        firmware_version="current",
        receipt_sha256=("4" * 64, "5" * 64),
    )


def _install_receipts(
    root: Path,
    binding: QualificationBinding,
    identities_and_requests: tuple[tuple[str, str], ...],
) -> QualificationBinding:
    payloads: list[bytes] = []
    for identity_marker, request_suffix in identities_and_requests:
        receipt = LocalControlQualificationReceipt(
            product_key=binding.product_key,
            function=binding.function,
            physical_identity_key=identity_marker * 64,
            app_version=binding.app_version,
            app_catalog_sha256=binding.app_catalog_sha256,
            template_sha256=binding.template_sha256,
            schema_sha256=binding.schema_sha256,
            firmware_version=binding.firmware_version,
            request_id=f"request_{request_suffix}",
            intent_sha256=hashlib.sha256(f"intent:{request_suffix}".encode()).hexdigest(),
            control_payload_sha256=hashlib.sha256(
                f"control:{request_suffix}".encode()
            ).hexdigest(),
            acknowledgement_sha256=hashlib.sha256(
                f"ack:{request_suffix}".encode()
            ).hexdigest(),
            readback_sha256=hashlib.sha256(f"readback:{request_suffix}".encode()).hexdigest(),
            completed_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        payloads.append(qualification_receipt_bytes(receipt))
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    result = binding.model_copy(update={"receipt_sha256": digests})
    for digest, payload in zip(digests, payloads, strict=True):
        path = root / f"local-control-receipt-{digest}.json"
        path.write_bytes(payload)
        path.chmod(0o600)
    return result


def test_absent_qualification_input_means_no_controls() -> None:
    assert load_current_qualifications(None) == ()


def test_owner_only_current_qualification_file_loads_without_defaults(tmp_path: Path) -> None:
    path = tmp_path / "qualifications.json"
    path.write_text(
        json.dumps({"qualifications": [_qualification().model_dump(mode="json")]}),
        encoding="ascii",
    )
    path.chmod(0o600)

    loaded = load_current_qualifications(path, _allow_ephemeral_for_test=True)

    assert loaded == (_qualification(),)
    path.chmod(0o644)
    with pytest.raises(QualificationStoreError, match="metadata"):
        load_current_qualifications(path, _allow_ephemeral_for_test=True)


def test_duplicate_current_qualification_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "qualifications.json"
    item = _qualification().model_dump(mode="json")
    path.write_text(json.dumps([item, item]), encoding="ascii")
    path.chmod(0o600)

    with pytest.raises(QualificationStoreError, match="duplicate"):
        load_current_qualifications(path, _allow_ephemeral_for_test=True)


def test_receipt_digests_must_resolve_to_exact_owner_only_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    binding = _qualification()
    receipts = tuple(
        LocalControlQualificationReceipt(
            product_key=binding.product_key,
            function=binding.function,
            physical_identity_key=marker * 64,
            app_version=binding.app_version,
            app_catalog_sha256=binding.app_catalog_sha256,
            template_sha256=binding.template_sha256,
            schema_sha256=binding.schema_sha256,
            firmware_version=binding.firmware_version,
            request_id=f"request_{marker * 8}",
            intent_sha256=marker * 64,
            control_payload_sha256=("6" if marker == "a" else "7") * 64,
            acknowledgement_sha256=("8" if marker == "a" else "9") * 64,
            readback_sha256=("a" if marker == "a" else "b") * 64,
            completed_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        for marker in ("a", "b")
    )
    payloads = tuple(qualification_receipt_bytes(receipt) for receipt in receipts)
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    qualification = binding.model_copy(update={"receipt_sha256": digests})
    for digest, payload in zip(digests, payloads, strict=True):
        path = root / f"local-control-receipt-{digest}.json"
        path.write_bytes(payload)
        path.chmod(0o600)

    verify_qualification_receipts(qualification, _receipt_root_for_test=root)

    first = root / f"local-control-receipt-{digests[0]}.json"
    first.write_bytes(b"changed\n")
    with pytest.raises(QualificationStoreError, match="digest mismatch"):
        verify_qualification_receipts(qualification, _receipt_root_for_test=root)


def test_receipt_content_must_match_the_qualified_function(tmp_path: Path) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    binding = _qualification()
    payloads: list[bytes] = []
    for marker in ("a", "b"):
        receipt = LocalControlQualificationReceipt(
            product_key=binding.product_key,
            function="power",
            physical_identity_key=marker * 64,
            app_version=binding.app_version,
            app_catalog_sha256=binding.app_catalog_sha256,
            template_sha256=binding.template_sha256,
            schema_sha256=binding.schema_sha256,
            firmware_version=binding.firmware_version,
            request_id=f"request_{marker * 8}",
            intent_sha256=marker * 64,
            control_payload_sha256="6" * 64,
            acknowledgement_sha256="7" * 64,
            readback_sha256="8" * 64,
            completed_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        payloads.append(qualification_receipt_bytes(receipt))
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    qualification = binding.model_copy(update={"receipt_sha256": digests})
    for digest, payload in zip(digests, payloads, strict=True):
        path = root / f"local-control-receipt-{digest}.json"
        path.write_bytes(payload)
        path.chmod(0o600)

    with pytest.raises(QualificationStoreError, match="capability binding"):
        verify_qualification_receipts(qualification, _receipt_root_for_test=root)


def test_missing_or_symlinked_receipt_never_qualifies(tmp_path: Path) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    qualification = _qualification()
    with pytest.raises(QualificationStoreError, match="unavailable"):
        verify_qualification_receipts(qualification, _receipt_root_for_test=root)

    target = tmp_path / "target"
    target.write_text("not evidence\n", encoding="ascii")
    target.chmod(0o600)
    (root / f"local-control-receipt-{qualification.receipt_sha256[0]}.json").symlink_to(
        target
    )
    with pytest.raises(QualificationStoreError, match="unavailable"):
        verify_qualification_receipts(qualification, _receipt_root_for_test=root)


def test_distinct_receipt_files_must_represent_distinct_requests(tmp_path: Path) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    binding = _qualification()
    payloads: list[bytes] = []
    for marker in ("a", "b"):
        receipt = LocalControlQualificationReceipt(
            product_key=binding.product_key,
            function=binding.function,
            physical_identity_key=marker * 64,
            app_version=binding.app_version,
            app_catalog_sha256=binding.app_catalog_sha256,
            template_sha256=binding.template_sha256,
            schema_sha256=binding.schema_sha256,
            firmware_version=binding.firmware_version,
            request_id="same_request",
            intent_sha256=marker * 64,
            control_payload_sha256=("6" if marker == "a" else "7") * 64,
            acknowledgement_sha256=("8" if marker == "a" else "9") * 64,
            readback_sha256=("a" if marker == "a" else "b") * 64,
            completed_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        payloads.append(qualification_receipt_bytes(receipt))
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    qualification = binding.model_copy(update={"receipt_sha256": digests})
    for digest, payload in zip(digests, payloads, strict=True):
        path = root / f"local-control-receipt-{digest}.json"
        path.write_bytes(payload)
        path.chmod(0o600)

    with pytest.raises(QualificationStoreError, match="distinct requests"):
        verify_qualification_receipts(qualification, _receipt_root_for_test=root)


def test_qualified_device_keys_are_derived_per_device_from_verified_receipts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    qualification = _install_receipts(
        root,
        _qualification(),
        (("a", "a_first"), ("a", "a_second"), ("b", "b_first"), ("b", "b_second")),
    )

    keys = qualified_physical_identity_keys(
        qualification,
        _receipt_root_for_test=root,
    )

    assert keys == frozenset({"a" * 64, "b" * 64})
    assert "qualified_physical_identity_keys" not in qualification.model_dump()


def test_single_receipt_never_qualifies_its_device_or_invalidates_a_complete_pair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    qualification = _install_receipts(
        root,
        _qualification(),
        (("a", "a_first"), ("a", "a_second"), ("b", "b_only")),
    )

    keys = qualified_physical_identity_keys(
        qualification,
        _receipt_root_for_test=root,
    )

    assert keys == frozenset({"a" * 64})
