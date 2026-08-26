import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.persistence.qualification import (
    DeviceQualificationReceipt,
    JsonQualificationStore,
    QualificationStoreError,
)


def _binding(suffix: str = "a") -> PhysicalDeviceBinding:
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=f"private-vendor-{suffix}",
        mac_address=f"aabbccddee0{suffix}",
        product_key="0" * 32,
        config_fingerprint="1" * 64,
    )


def _receipt(binding: PhysicalDeviceBinding) -> DeviceQualificationReceipt:
    completed_at = datetime.now(UTC)
    return DeviceQualificationReceipt(
        operation_id="first_write_001",
        device_id="pro_left",
        physical_binding=binding,
        original_power=35,
        step_power=32,
        completed_at=completed_at,
        valid_until=completed_at + timedelta(hours=24),
    )


def test_receipt_round_trip_is_private_and_owner_only(tmp_path: Path) -> None:
    binding = _binding()
    receipt = _receipt(binding)
    store = JsonQualificationStore(tmp_path / "qualifications")

    store.save(receipt)

    assert store.load(binding) == receipt
    path = store.path_for(binding)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    serialized = path.read_text(encoding="utf-8")
    assert "private-vendor" not in serialized
    assert "aabbccddee0a" not in serialized


def test_receipt_only_qualifies_the_exact_binding() -> None:
    receipt = _receipt(_binding())

    assert receipt.is_valid_for(receipt.physical_binding, now=receipt.completed_at)
    assert not receipt.is_valid_for(_binding("b"), now=receipt.completed_at)
    assert not receipt.is_valid_for(
        receipt.physical_binding,
        now=receipt.valid_until + timedelta(microseconds=1),
    )


@pytest.mark.parametrize(("original", "step"), [(35, 35), (35, 29), (30, 31)])
def test_receipt_rejects_an_unbounded_or_upward_step(original: int, step: int) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="1..5"):
        DeviceQualificationReceipt(
            operation_id="bad",
            device_id="pro_left",
            physical_binding=_binding(),
            original_power=original,
            step_power=step,
            completed_at=now,
            valid_until=now + timedelta(hours=1),
        )


def test_corrupt_receipt_fails_closed(tmp_path: Path) -> None:
    binding = _binding()
    store = JsonQualificationStore(tmp_path)
    path = store.path_for(binding)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(QualificationStoreError, match="unreadable"):
        store.load(binding)


def test_receipt_validity_cannot_exceed_24_hours() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="24 hours"):
        DeviceQualificationReceipt(
            operation_id="too_long",
            device_id="pro_left",
            physical_binding=_binding(),
            original_power=35,
            step_power=34,
            completed_at=now,
            valid_until=now + timedelta(hours=24, microseconds=1),
        )


def test_symlinked_qualification_directory_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    directory = tmp_path / "qualifications"
    directory.symlink_to(target, target_is_directory=True)
    store = JsonQualificationStore(directory)

    with pytest.raises(QualificationStoreError, match="unsafe"):
        store.save(_receipt(_binding()))

    assert list(target.iterdir()) == []
