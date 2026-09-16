from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.models import (
    InstallContentBindings,
    LocalControlIntent,
    LocalDeviceBinding,
    QualificationBinding,
)
from jebao_flow.operator_capture.app_catalog import (
    APP_SHELL_FUNCTIONS,
    AQUARIUM_PUMP_APP,
    DOSING_PUMP_APP,
    LOCAL_WAVEMAKER_PRO_APP,
    REGISTERED_APP_PRODUCTS,
)


def _qualification(function: str) -> QualificationBinding:
    return QualificationBinding(
        product_key=LOCAL_WAVEMAKER_PRO_APP.product_key,
        function=function,
        app_version="3.4.18",
        app_catalog_sha256="0" * 64,
        template_sha256="1" * 64,
        schema_sha256="2" * 64,
        firmware_version="current",
        receipt_sha256=("3" * 64, "4" * 64),
    )


def _content() -> InstallContentBindings:
    return InstallContentBindings(
        app_catalog_sha256="0" * 64,
        protocol_schema_sha256="2" * 64,
        template_sha256=("1" * 64,),
    )


def _intent(function: str, value: object) -> LocalControlIntent:
    return LocalControlIntent(
        request_id="request_0001",
        target=LocalDeviceBinding(
            logical_member="wavemaker_left",
            product_key=LOCAL_WAVEMAKER_PRO_APP.product_key,
            physical_identity_key="5" * 64,
            logical_role="left",
            role_attested_by="on_site_operator",
            role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
        ),
        function=function,
        value=value,
    )


def _verified_keys(*markers: str) -> frozenset[str]:
    return frozenset(marker * 64 for marker in markers)


def test_catalog_covers_every_registered_product_and_app_shell_function() -> None:
    catalog = build_local_control_catalog()
    product_functions = sum(len(product.functions) for product in REGISTERED_APP_PRODUCTS.values())

    assert len(catalog.functions) == product_functions + len(APP_SHELL_FUNCTIONS)
    assert {item.product_key for item in catalog.functions if item.product_key is not None} == set(
        REGISTERED_APP_PRODUCTS
    )
    assert catalog.public_dict()["language"] == "en"
    assert str(catalog.public_dict()).isascii()
    assert catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "manual_mode").choices[6] == (
        "Cross-flow"
    )


@pytest.mark.parametrize("function", ("schedule_enable", "schedule"))
def test_bar_timer_and_schedule_require_current_physical_qualification(function):
    bar = "1d8c63eaccac4205b92c84d77d5a08fb"
    descriptor = build_local_control_catalog().find(bar, function)
    assert descriptor.encoding_available is True
    assert descriptor.control_exposed is False
    if function == "schedule_enable":
        assert "AutoFlow" in descriptor.fields
    # Hardware acceptance is recorded in the two 2026-09-16 Bar qualification runs.
    # This fixture checks only software exposure and physical-identity scoping.
    binding = _qualification(function).model_copy(update={"product_key": bar})
    descriptor = build_local_control_catalog(
        (binding,),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    ).find(bar, function)
    assert descriptor.control_exposed_for("5" * 64) is True
    assert descriptor.control_exposed_for("6" * 64) is False


def test_generator_qualification_is_consumed_once_and_exposes_only_that_control() -> None:
    catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )

    descriptor = catalog.validate_intent(_intent("manual_flow", 45))
    app_function = next(
        function for function in LOCAL_WAVEMAKER_PRO_APP.functions if function.name == "manual_flow"
    )

    assert descriptor.control_exposed is True
    assert (descriptor.native_minimum, descriptor.native_maximum) == (
        app_function.app_minimum,
        app_function.app_maximum,
    )
    with pytest.raises(ValueError, match="not qualified"):
        catalog.validate_intent(_intent("power", True))


def test_unknown_or_duplicate_qualification_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in"):
        build_local_control_catalog(
            (_qualification("not_a_function"),),
            current_content=_content(),
            _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
        )
    qualification = _qualification("power")
    with pytest.raises(ValueError, match="unique"):
        build_local_control_catalog(
            (qualification, qualification),
            current_content=_content(),
            _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
        )


def test_unmapped_or_status_only_app_functions_never_expose_hardware_control() -> None:
    catalog = build_local_control_catalog()

    dosing_calibration_duration = catalog.find(
        DOSING_PUMP_APP.product_key,
        "calibration_duration",
    )
    aquarium_transport = catalog.find(AQUARIUM_PUMP_APP.product_key, "transport_mode")
    aquarium_motor = catalog.find(AQUARIUM_PUMP_APP.product_key, "motor_speed")
    pro_current_state = catalog.find(
        LOCAL_WAVEMAKER_PRO_APP.product_key,
        "current_schedule_state",
    )
    pro_reset = catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "schedule_reset")

    assert dosing_calibration_duration.encoding_available is False
    assert dosing_calibration_duration.kind.value == "status"
    assert dosing_calibration_duration.fields == ("time1",)
    assert aquarium_transport.encoding_available is False
    assert aquarium_motor.encoding_available is True
    assert aquarium_motor.control_exposed is False
    assert (aquarium_motor.native_minimum, aquarium_motor.native_maximum) == (30, 100)
    assert pro_current_state.control_exposed is False
    assert pro_reset.encoding_available is True
    assert pro_reset.stage.value == "app_static"
    assert pro_reset.diagnostic_reason == (
        "app writes one command containing 48 slots of nine 0xee bytes"
    )


@pytest.mark.parametrize(
    "function",
    ("current_schedule_state",),
)
def test_non_control_pro_functions_cannot_be_qualified(function: str) -> None:
    with pytest.raises(ValueError, match="no deterministic encoder"):
        build_local_control_catalog(
            (_qualification(function),),
            current_content=_content(),
            _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
        )


def test_exact_pro_schedule_reset_is_exposed_only_after_device_receipts() -> None:
    catalog = build_local_control_catalog(
        (_qualification("schedule_reset"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )

    descriptor = catalog.validate_intent(_intent("schedule_reset", None))

    assert descriptor.encoding_available is True
    assert descriptor.control_exposed is True
    assert descriptor.kind.value == "action"


def test_clock_builder_does_not_claim_an_end_to_end_control_surface() -> None:
    catalog = build_local_control_catalog(
        (_qualification("device_clock_sync"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )

    descriptor = catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "device_clock_sync")

    assert descriptor.encoding_available is True
    assert descriptor.control_exposed is False
    assert descriptor.diagnostic_reason == "app writes YMD and zero-prefixed HMS in one command"


@pytest.mark.parametrize("value", (29, 101, True, "45"))
def test_flow_control_declares_and_enforces_same_ha_native_range(value: object) -> None:
    catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )
    with pytest.raises((TypeError, ValueError)):
        catalog.validate_intent(_intent("manual_flow", value))


def test_flow_control_accepts_the_app_declared_maximum() -> None:
    catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )

    descriptor = catalog.validate_intent(_intent("manual_flow", 100))

    assert descriptor.native_maximum == 100


def test_qualification_must_bind_current_install_content() -> None:
    with pytest.raises(ValueError, match="current install content"):
        build_local_control_catalog((_qualification("manual_flow"),))
    changed = _content().model_copy(update={"template_sha256": ("9" * 64,)})
    stale_template = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=changed,
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )
    descriptor = stale_template.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "manual_flow")
    assert descriptor.control_exposed is False
    assert descriptor.diagnostic_reason == "qualification_not_current"
    assert stale_template.ignored_qualification_count == 1

    changed_catalog = _content().model_copy(update={"app_catalog_sha256": "8" * 64})
    stale_catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=changed_catalog,
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )
    assert (
        stale_catalog.find(
            LOCAL_WAVEMAKER_PRO_APP.product_key,
            "manual_flow",
        ).control_exposed
        is False
    )
    assert stale_catalog.ignored_qualification_count == 1


def test_pro_schedule_has_qualified_product_execution_and_clock_stays_diagnostic() -> None:
    catalog = build_local_control_catalog(
        (_qualification("schedule"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )
    descriptor = catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "schedule")

    assert descriptor.encoding_available is True
    assert descriptor.stage.value == "qualified"
    assert descriptor.control_exposed is True
    assert descriptor.diagnostic_reason is None
    assert catalog.validate_intent(_intent("schedule", {})) == descriptor

    clock_catalog = build_local_control_catalog(
        (_qualification("device_clock_sync"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )
    clock = clock_catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "device_clock_sync")
    assert clock.encoding_available is True
    assert clock.stage.value == "qualified"
    assert clock.control_exposed is False
    with pytest.raises(ValueError, match="not qualified"):
        clock_catalog.validate_intent(_intent("device_clock_sync", {}))


def test_unverified_receipt_strings_never_expose_a_control() -> None:
    catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=_content(),
    )

    descriptor = catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "manual_flow")
    assert descriptor.control_exposed is False
    assert descriptor.diagnostic_reason == "qualification_receipt_unavailable"


def test_qualification_is_scoped_to_the_receipted_physical_device() -> None:
    catalog = build_local_control_catalog(
        (_qualification("manual_flow"),),
        current_content=_content(),
        _qualification_verifier_for_test=lambda _item: _verified_keys("5"),
    )
    qualified = catalog.find(LOCAL_WAVEMAKER_PRO_APP.product_key, "manual_flow")
    other_intent = _intent("manual_flow", 45).model_copy(
        update={
            "target": _intent("manual_flow", 45).target.model_copy(
                update={"physical_identity_key": "6" * 64}
            )
        }
    )

    assert qualified.control_exposed_for("5" * 64) is True
    assert qualified.control_exposed_for("6" * 64) is False
    assert qualified.public_dict()["qualified_device_count"] == 1
    assert "5" * 64 not in str(qualified.public_dict())
    with pytest.raises(ValueError, match="not qualified"):
        catalog.validate_intent(other_intent)
