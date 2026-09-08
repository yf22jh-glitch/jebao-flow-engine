import pytest

from jebao_flow.operator_capture.capability import (
    AppExpressibility,
    CapabilityClaim,
    CapabilitySupport,
    CaptureEvidenceRef,
    DeviceSupport,
    HomeAssistantExposure,
)

PRODUCT = "50dbc92221fd4d33ae69a1fedd43b555"


def _evidence(seq: int, byte: str) -> CaptureEvidenceRef:
    return CaptureEvidenceRef(
        session_id="capture-00000000000000000001-0123456789abcdef",
        event_seq=seq,
        frame_sha256=byte * 64,
        response_frame_sha256="f" * 64,
        app_version="3.4.18",
        template_version="1786953828",
        template_sha256="e" * 64,
        firmware_version="observed-version",
    )


def test_capability_stays_diagnostic_through_offline_promotion_states() -> None:
    claim = CapabilityClaim(
        product_key=PRODUCT,
        capability="schedule.slave_per_slot_flow",
        source_label="AutoFlow",
        exposed_name="Per-slot slave flow",
    )
    assert claim.home_assistant_exposure is HomeAssistantExposure.HIDDEN

    observed = claim.observe(_evidence(2, "a"))
    reproduced = observed.observe(_evidence(4, "b"))
    differential = reproduced.confirm_differential(
        confirmed_semantic="slot-local slave flow override",
        comparison=_evidence(6, "c"),
    )

    assert observed.support is CapabilitySupport.OBSERVED
    assert reproduced.support is CapabilitySupport.REPRODUCED
    assert differential.support is CapabilitySupport.DIFFERENTIAL
    assert differential.home_assistant_exposure is HomeAssistantExposure.DIAGNOSTIC
    assert "control" not in differential.public_dict()["home_assistant_exposure"]


def test_reproduced_requires_distinct_retained_observations() -> None:
    claim = CapabilityClaim(
        product_key=PRODUCT,
        capability="manual.flow",
        source_label="Flow",
        exposed_name="Manual flow",
    )
    evidence = _evidence(2, "a")

    assert claim.observe(evidence).observe(evidence).support is CapabilitySupport.OBSERVED


def test_static_app_absence_can_close_app_question_without_firmware_claim() -> None:
    claim = CapabilityClaim(
        product_key=PRODUCT,
        capability="schedule.slave_per_slot_flow",
        source_label="AutoFlow",
        exposed_name="Per-slot slave flow",
    ).mark_infeasible_by_app()

    assert claim.support is CapabilitySupport.UNKNOWN
    assert claim.app_expressibility is AppExpressibility.INFEASIBLE
    assert claim.device_support is DeviceSupport.UNKNOWN
    assert claim.confirmed_semantic is None
    assert claim.home_assistant_exposure is HomeAssistantExposure.DIAGNOSTIC


def test_wire_states_cannot_be_constructed_without_evidence() -> None:
    with pytest.raises(ValueError, match="retained capture evidence"):
        CapabilityClaim(
            product_key=PRODUCT,
            capability="manual.flow",
            source_label="Flow",
            exposed_name="Manual flow",
            support=CapabilitySupport.OBSERVED,
        )


def test_app_infeasibility_and_wire_maturity_are_independent_axes() -> None:
    claim = CapabilityClaim(
        product_key=PRODUCT,
        capability="schedule.slave_per_slot_flow",
        source_label="AutoFlow",
        exposed_name="Per-slot slave flow",
    ).mark_infeasible_by_app()

    observed = claim.observe(_evidence(2, "a")).observe(_evidence(4, "b"))

    assert observed.support is CapabilitySupport.REPRODUCED
    assert observed.app_expressibility is AppExpressibility.INFEASIBLE
    assert observed.device_support is DeviceSupport.UNKNOWN


def test_app_evidence_requires_complete_version_tuple() -> None:
    with pytest.raises(ValueError, match="active template"):
        CaptureEvidenceRef(
            session_id="capture-00000000000000000001-0123456789abcdef",
            event_seq=2,
            frame_sha256="a" * 64,
            app_version="3.4.18",
        )


@pytest.mark.parametrize("value", ["\u6d41\u91cf", "Flow\ncontrol"])
def test_public_capability_names_are_english_ascii(value: str) -> None:
    with pytest.raises(ValueError, match="ASCII"):
        CapabilityClaim(
            product_key=PRODUCT,
            capability="manual.flow",
            source_label="Flow",
            exposed_name=value,
        )
