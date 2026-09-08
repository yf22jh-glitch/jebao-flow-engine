import pytest

from jebao_flow.operator_capture.capability import CapabilityClaim, CaptureEvidenceRef
from jebao_flow.operator_capture.ha_projection import project_diagnostic_entities

PRODUCT = "50dbc92221fd4d33ae69a1fedd43b555"


def _observed(capability: str) -> CapabilityClaim:
    return CapabilityClaim(
        product_key=PRODUCT,
        capability=capability,
        source_label="AutoFlow",
        exposed_name="Per-slot slave flow",
    ).observe(
        CaptureEvidenceRef(
            session_id="capture-00000000000000000001-0123456789abcdef",
            event_seq=2,
            frame_sha256="a" * 64,
        )
    )


def test_home_assistant_projection_is_diagnostic_only() -> None:
    descriptor = project_diagnostic_entities((_observed("schedule.slave_per_slot_flow"),))[0]

    public = descriptor.public_dict()
    assert public["entity_category"] == "diagnostic"
    assert public["read_only"] is True
    assert public["attributes"]["control_available"] is False
    assert public["native_value"] == "unknown_on_device"
    assert public["attributes"]["observation_maturity"] == "observed"
    assert "command" not in public
    assert "service" not in public


def test_unknown_capability_is_not_exposed_to_home_assistant() -> None:
    claim = CapabilityClaim(
        product_key=PRODUCT,
        capability="schedule.unobserved",
        source_label="Unknown",
        exposed_name="Unknown capability",
    )

    assert project_diagnostic_entities((claim,)) == ()


def test_duplicate_capability_keys_are_rejected() -> None:
    claim = _observed("manual.flow")

    with pytest.raises(ValueError, match="duplicate"):
        project_diagnostic_entities((claim, claim))
