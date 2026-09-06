from __future__ import annotations

from pathlib import Path

import pytest

from jebao_flow import hardware_safety

TEST_HARDWARE_SAFETY_VOLUME_ID = "0123456789abcdef0123456789abcdef"
TEST_HARDWARE_SAFETY_IDENTITY_BYTES = (
    b'{"version":1,"volume_id":"0123456789abcdef0123456789abcdef"}\n'
)


def install_test_hardware_safety_identity(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    monkeypatch.setenv(
        hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV,
        TEST_HARDWARE_SAFETY_VOLUME_ID,
    )
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    marker.write_bytes(TEST_HARDWARE_SAFETY_IDENTITY_BYTES)
    marker.chmod(0o600)
    return marker
