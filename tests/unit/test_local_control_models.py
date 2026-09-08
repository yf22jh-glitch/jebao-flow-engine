from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jebao_flow.local_control.models import (
    FreshInstallManifest,
    InstallContentBindings,
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)


def _binding(*, member: str = "wavemaker_left", identity: str = "a" * 64) -> LocalDeviceBinding:
    return LocalDeviceBinding(
        logical_member=member,
        product_key="1" * 32,
        physical_identity_key=identity,
        logical_role="left",
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def test_stable_binding_contains_no_runtime_locator() -> None:
    binding = _binding()

    assert set(binding.model_dump()) == {
        "logical_member",
        "product_key",
        "physical_identity_key",
        "logical_role",
        "role_evidence_grade",
        "role_attested_by",
        "role_attested_at",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        LocalDeviceBinding.model_validate({**binding.model_dump(), "address": "dynamic"})


def test_public_binding_rejects_non_ascii_role_evidence() -> None:
    with pytest.raises(ValidationError, match="printable ASCII"):
        LocalDeviceBinding.model_validate(
            {**_binding().model_dump(), "logical_role": "\ud55c\uae00"}
        )


def test_qualification_requires_two_distinct_content_bound_receipts() -> None:
    payload = {
        "product_key": "1" * 32,
        "function": "power",
        "app_version": "3.4.18",
        "app_catalog_sha256": "0" * 64,
        "template_sha256": "2" * 64,
        "schema_sha256": "3" * 64,
        "firmware_version": "current",
        "receipt_sha256": ("4" * 64, "5" * 64),
    }

    binding = QualificationBinding.model_validate(payload)

    assert len(binding.receipt_sha256) == 2
    with pytest.raises(ValidationError, match="distinct"):
        QualificationBinding.model_validate(
            {**payload, "receipt_sha256": ("4" * 64, "4" * 64)}
        )


def test_control_intent_is_stable_device_bound() -> None:
    intent = LocalControlIntent(
        request_id="request_0001",
        target=_binding(),
        function="manual_flow",
        value=45,
    )

    assert intent.target.physical_identity_key == "a" * 64
    assert "address" not in intent.model_dump_json()


def test_install_manifest_rejects_duplicate_physical_identity() -> None:
    with pytest.raises(ValidationError, match="physical identities must be unique"):
        FreshInstallManifest(
            generation="1" * 32,
            rederived_at=datetime(2026, 9, 8, tzinfo=UTC),
            devices=(
                _binding(member="left"),
                _binding(member="right"),
            ),
            content=InstallContentBindings(
                app_catalog_sha256="2" * 64,
                protocol_schema_sha256="3" * 64,
            ),
        )
