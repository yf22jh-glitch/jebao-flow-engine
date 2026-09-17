"""Complete English local-control projection of the audited Jebao app surface."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from jebao_flow.local_control.models import (
    CapabilityStage,
    InstallContentBindings,
    LocalControlIntent,
    QualificationBinding,
)
from jebao_flow.local_control.qualification import (
    QualificationStoreError,
    qualified_physical_identity_keys,
)
from jebao_flow.operator_capture.app_catalog import (
    APP_SHELL_FUNCTIONS,
    REGISTERED_APP_PRODUCTS,
    AppFunction,
    AppFunctionKind,
    CapabilityConfidence,
    declared_app_integer_range,
)

_ENCODABLE_FUNCTIONS_BY_PRODUCT: dict[str, frozenset[str]] = {
    "50dbc92221fd4d33ae69a1fedd43b555": frozenset(
        {
            "power",
            "schedule_enable",
            "linkage_role",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "custom_wave_frequency",
            "feeding_duration",
            "schedule",
            "schedule_reset",
            "device_clock_sync",
            "slave_common_flow",
            "slave_per_slot_flow_extension",
        }
    ),
    "1d8c63eaccac4205b92c84d77d5a08fb": frozenset(
        {
            "power",
            "schedule_enable",
            "feed_switch",
            "pulse_or_tide",
            "linkage_role",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "feeding_duration",
            "schedule",
            "device_clock_sync",
        }
    ),
    "0696a19599bc484f8e1866f5ccf4ee7e": frozenset(
        {
            "power",
            "schedule_enable",
            "manual_enable",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "feeding_duration",
            "schedule",
            "schedule_reset",
            "device_clock_sync",
        }
    ),
    "6a5c47b3ea364ecb841b47f5997a1775": frozenset(
        {
            "power",
            "schedule_enable",
            "transport_mode",
            "feed_switch",
            "motor_speed",
            "feeding_duration",
            "schedule",
            "device_clock_sync",
        }
    ),
    "5b3c136fd4b74f3fb2a366a254c76c9a": frozenset(
        {
            "power",
            "manual_channel",
            "channel_timer_enable",
            "interval_days",
            "calibration_channel",
            "calibration",
            "calibration_amount",
            "channel_schedule",
        }
    ),
}

# A deterministic byte builder and an end-to-end product surface are different claims. Generic
# schedule and clock bytes remain diagnostics until their coordinator/readback contracts exist.
# Pro schedule reset is executable because the audited app supplies the exact 48-slot sentinel
# image and the existing exact-image verifier covers its complete effect.
# Pro의 선언 시간표도 같은 journal-first 실행과 전체 image read-back을 사용한다.
# Bar compiler는 오프라인이며, 384-byte 복원 qualification 없이 실행 기능으로 승격하지 않는다.
_EXECUTABLE_FUNCTIONS_BY_PRODUCT: dict[str, frozenset[str]] = {
    "50dbc92221fd4d33ae69a1fedd43b555": frozenset(
        {
            "power",
            "schedule_enable",
            "linkage_role",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "custom_wave_frequency",
            "feeding_duration",
            "schedule",
            "schedule_reset",
            "device_clock_sync",
            "slave_common_flow",
            "slave_per_slot_flow_extension",
        }
    ),
    "1d8c63eaccac4205b92c84d77d5a08fb": frozenset(
        {
            "power",
            "feed_switch",
            "pulse_or_tide",
            "linkage_role",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "feeding_duration",
            # Attended source-matched ON/OFF and A/B/raw-restore runs, 2026-09-16.
            "schedule_enable",
            "schedule",
            "device_clock_sync",
        }
    ),
    "0696a19599bc484f8e1866f5ccf4ee7e": frozenset(
        {
            "power",
            "schedule_enable",
            "manual_enable",
            "manual_mode",
            "manual_flow",
            "manual_frequency",
            "feeding_duration",
            "schedule_reset",
            "schedule",
            "device_clock_sync",
        }
    ),
    "6a5c47b3ea364ecb841b47f5997a1775": frozenset(
        {
            "power",
            "transport_mode",
            "feed_switch",
            "motor_speed",
            "feeding_duration",
            "device_clock_sync",
            "schedule",
            "schedule_enable",
        }
    ),
    "5b3c136fd4b74f3fb2a366a254c76c9a": frozenset(
        {
            "power",
            "manual_channel",
            "channel_timer_enable",
            "interval_days",
            "calibration_channel",
            "calibration",
            "calibration_amount",
            "channel_schedule",
        }
    ),
}

_ENGLISH_NAME_BY_PRODUCT_FUNCTION = {
    ("6a5c47b3ea364ecb841b47f5997a1775", "motor_speed"): "Motor Speed (Enters Manual)",
    ("6a5c47b3ea364ecb841b47f5997a1775", "feed_switch"): "Feeding (Enters Manual)",
    ("6a5c47b3ea364ecb841b47f5997a1775", "transport_mode"): "Transport Mode (May Disconnect)",
    ("1d8c63eaccac4205b92c84d77d5a08fb", "manual_mode"): "Manual Mode (Enters Independent Manual)",
    ("1d8c63eaccac4205b92c84d77d5a08fb", "linkage_role"): "Linkage Role (App Mode Coupling)",
    ("1d8c63eaccac4205b92c84d77d5a08fb", "pulse_or_tide"): "Cross-flow (On) Or Pulse (Off)",
    ("0696a19599bc484f8e1866f5ccf4ee7e", "schedule_enable"): "Schedule (Controls Power)",
    ("0696a19599bc484f8e1866f5ccf4ee7e", "manual_enable"): "Manual Operation (Controls Power)",
    (
        "50dbc92221fd4d33ae69a1fedd43b555",
        "schedule_enable",
    ): "Schedule Enable (Detaches Slave)",
}

_PUBLIC_VALUE_BY_PRODUCT_FUNCTION = {
    (
        "50dbc92221fd4d33ae69a1fedd43b555",
        "manual_mode",
        "circulation",
    ): "Cross-flow",
    **{
        (
            "5b3c136fd4b74f3fb2a366a254c76c9a",
            "calibration_channel",
            f"calibrate_{channel}",
        ): f"channel_{channel}"
        for channel in range(1, 5)
    },
}
_INTERNAL_VALUE_BY_PRODUCT_FUNCTION = {
    (product_key, function, public): internal
    for (product_key, function, internal), public in _PUBLIC_VALUE_BY_PRODUCT_FUNCTION.items()
}


@dataclass(frozen=True, slots=True)
class LocalFunctionDescriptor:
    product_key: str | None
    family: str
    function: str
    exposed_name: str
    kind: AppFunctionKind
    stage: CapabilityStage
    fields: tuple[str, ...]
    choices: tuple[str, ...]
    native_minimum: int | None
    native_maximum: int | None
    native_step: int | None
    count: int | None
    item_size: int | None
    encoding_available: bool
    control_exposed: bool
    diagnostic_reason: str | None
    qualification: QualificationBinding | None = None
    qualified_physical_identity_keys: frozenset[str] = frozenset()
    identity_authorized_physical_identity_keys: frozenset[str] = frozenset()
    unqualified_stage: CapabilityStage = CapabilityStage.UNKNOWN

    def is_qualified_for(self, physical_identity_key: str) -> bool:
        return (
            self.stage is CapabilityStage.QUALIFIED
            and physical_identity_key in self.qualified_physical_identity_keys
        )

    def control_exposed_for(self, physical_identity_key: str) -> bool:
        return self.control_exposed and (
            self.is_qualified_for(physical_identity_key)
            or self.is_identity_authorized_for(physical_identity_key)
        )

    def is_identity_authorized_for(self, physical_identity_key: str) -> bool:
        return physical_identity_key in self.identity_authorized_physical_identity_keys

    def stage_for(self, physical_identity_key: str) -> CapabilityStage:
        if self.is_qualified_for(physical_identity_key):
            return self.stage
        return self.unqualified_stage

    def diagnostic_reason_for(self, physical_identity_key: str) -> str | None:
        if self.control_exposed and not self.control_exposed_for(physical_identity_key):
            return "physical_device_not_qualified"
        return self.diagnostic_reason

    def public_dict(self) -> dict[str, object]:
        payload = {
            "product_key": self.product_key,
            "family": self.family,
            "function": self.function,
            "exposed_name": self.exposed_name,
            "kind": self.kind.value,
            "stage": self.stage.value,
            "fields": list(self.fields),
            "choices": list(self.choices),
            "native_minimum": self.native_minimum,
            "native_maximum": self.native_maximum,
            "native_step": self.native_step,
            "count": self.count,
            "item_size": self.item_size,
            "encoding_available": self.encoding_available,
            "control_exposed": self.control_exposed,
            "qualified_device_count": len(self.qualified_physical_identity_keys),
            "identity_authorized_device_count": len(
                self.identity_authorized_physical_identity_keys
            ),
            "diagnostic_reason": self.diagnostic_reason,
        }
        if self.qualification is not None:
            payload["qualification"] = self.qualification.model_dump(mode="json")
        return payload


@dataclass(frozen=True, slots=True)
class LocalControlCatalog:
    functions: tuple[LocalFunctionDescriptor, ...]
    ignored_qualification_count: int = 0

    def __post_init__(self) -> None:
        if self.ignored_qualification_count < 0:
            raise ValueError("ignored qualification count cannot be negative")
        keys = [(item.product_key, item.function) for item in self.functions]
        if len(keys) != len(set(keys)):
            raise ValueError("local control catalog contains duplicate functions")
        _require_ascii(self.public_dict())

    def find(self, product_key: str, function: str) -> LocalFunctionDescriptor:
        try:
            return next(
                item
                for item in self.functions
                if item.product_key == product_key and item.function == function
            )
        except StopIteration as error:
            raise KeyError("function is not in the product-scoped app catalog") from error

    def validate_intent(self, intent: LocalControlIntent) -> LocalFunctionDescriptor:
        descriptor = self.find(intent.target.product_key, intent.function)
        _validate_value(descriptor, intent.value)
        if not descriptor.control_exposed_for(intent.target.physical_identity_key):
            raise ValueError("hardware control is not qualified for this content/version binding")
        return descriptor

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "language": "en",
            "ignored_qualification_count": self.ignored_qualification_count,
            "functions": [item.public_dict() for item in self.functions],
        }


def build_local_control_catalog(
    qualifications: Iterable[QualificationBinding] = (),
    *,
    current_content: InstallContentBindings | None = None,
    current_app_version: str = "3.4.18",
    identity_control_authorizations: Mapping[tuple[str, str], Iterable[str]] | None = None,
    _qualification_verifier_for_test: Callable[[QualificationBinding], frozenset[str]]
    | None = None,
) -> LocalControlCatalog:
    qualification_items = tuple(qualifications)
    if qualification_items and current_content is None:
        raise ValueError("qualified controls require current install content bindings")
    qualification_by_key = {(item.product_key, item.function): item for item in qualification_items}
    if len(qualification_by_key) != len(qualification_items):
        raise ValueError("qualification bindings must be unique by product and function")
    qualified: dict[tuple[str, str], tuple[QualificationBinding, frozenset[str]]] = {}
    ignored: dict[tuple[str, str], str] = {}
    identity_authorized = _normalize_identity_control_authorizations(
        identity_control_authorizations
    )
    verifier = _qualification_verifier_for_test or qualified_physical_identity_keys
    for key, item in qualification_by_key.items():
        content_changed = current_content is not None and (
            item.app_catalog_sha256 != current_content.app_catalog_sha256
            or item.schema_sha256 != current_content.protocol_schema_sha256
            or item.template_sha256 not in current_content.template_sha256
        )
        if item.app_version != current_app_version or content_changed:
            ignored[key] = "qualification_not_current"
        else:
            try:
                physical_keys = verifier(item)
            except QualificationStoreError:
                ignored[key] = "qualification_receipt_unavailable"
            else:
                if not physical_keys:
                    ignored[key] = "qualification_receipts_insufficient_per_device"
                    continue
                qualified[key] = (item, physical_keys)

    functions: list[LocalFunctionDescriptor] = []
    for product in REGISTERED_APP_PRODUCTS.values():
        for function in product.functions:
            key = (product.product_key, function.name)
            qualified_item = qualified.get(key)
            binding = None if qualified_item is None else qualified_item[0]
            physical_keys = frozenset() if qualified_item is None else qualified_item[1]
            identity_keys = identity_authorized.get(key, frozenset())
            ignored_reason = ignored.get(key)
            encoding_available = function.name in _ENCODABLE_FUNCTIONS_BY_PRODUCT.get(
                product.product_key, frozenset()
            )
            executable = function.name in _EXECUTABLE_FUNCTIONS_BY_PRODUCT.get(
                product.product_key, frozenset()
            )
            if binding is not None and not encoding_available:
                raise ValueError("qualified hardware control has no deterministic encoder")
            if identity_keys and (
                not encoding_available or not executable or function.kind is AppFunctionKind.STATUS
            ):
                raise ValueError(
                    "identity-bound hardware control is not an executable encoded function"
                )
            unqualified_stage = _initial_stage(function)
            stage = CapabilityStage.QUALIFIED if binding is not None else unqualified_stage
            native_minimum, native_maximum = _native_range(product.product_key, function)
            functions.append(
                LocalFunctionDescriptor(
                    product_key=product.product_key,
                    family=product.family,
                    function=function.name,
                    exposed_name=_english_name(function.name, product.product_key),
                    kind=function.kind,
                    stage=stage,
                    fields=function.fields,
                    choices=function.choices,
                    native_minimum=native_minimum,
                    native_maximum=native_maximum,
                    native_step=function.app_step,
                    count=function.count,
                    item_size=function.item_size,
                    encoding_available=encoding_available,
                    control_exposed=(
                        (binding is not None or bool(identity_keys))
                        and executable
                        and function.kind is not AppFunctionKind.STATUS
                    ),
                    diagnostic_reason=_diagnostic_reason(
                        function,
                        binding=binding,
                        identity_authorized=bool(identity_keys),
                        ignored_reason=ignored_reason,
                        encoding_available=encoding_available,
                        executable=executable,
                    ),
                    qualification=binding,
                    qualified_physical_identity_keys=physical_keys,
                    identity_authorized_physical_identity_keys=identity_keys,
                    unqualified_stage=unqualified_stage,
                )
            )

    for function in APP_SHELL_FUNCTIONS:
        functions.append(
            LocalFunctionDescriptor(
                product_key=None,
                family="local_app_shell",
                function=function.name,
                exposed_name=_english_name(function.name),
                kind=function.kind,
                stage=CapabilityStage.LOCAL_ONLY,
                fields=function.fields,
                choices=function.choices,
                native_minimum=function.app_minimum,
                native_maximum=function.app_maximum,
                native_step=function.app_step,
                count=function.count,
                item_size=function.item_size,
                encoding_available=False,
                control_exposed=False,
                diagnostic_reason=function.caveat or "local_equivalent_not_implemented",
                unqualified_stage=CapabilityStage.LOCAL_ONLY,
            )
        )
    known = {(item.product_key, item.function) for item in functions}
    unused = sorted(set(qualified) - known)
    if unused:
        raise ValueError("qualification binding is not in the product-scoped app catalog")
    unused_identity = sorted(set(identity_authorized) - known)
    if unused_identity:
        raise ValueError("identity-bound control is not in the product-scoped app catalog")
    return LocalControlCatalog(
        tuple(functions),
        ignored_qualification_count=len(ignored),
    )


def _initial_stage(function: AppFunction) -> CapabilityStage:
    if function.confidence is CapabilityConfidence.ACCEPTED_CAPTURE_REQUIRED:
        return CapabilityStage.UNKNOWN
    return CapabilityStage.APP_STATIC


def _diagnostic_reason(
    function: AppFunction,
    *,
    binding: QualificationBinding | None,
    identity_authorized: bool,
    ignored_reason: str | None,
    encoding_available: bool,
    executable: bool,
) -> str | None:
    if (binding is not None or identity_authorized) and executable:
        return None
    if ignored_reason is not None:
        return ignored_reason
    if function.caveat is not None:
        return function.caveat
    if binding is not None and encoding_available:
        return "local_runtime_surface_unavailable"
    if encoding_available:
        return "awaiting_version_bound_ack_and_readback_qualification"
    return "deterministic_encoder_unavailable"


def _normalize_identity_control_authorizations(
    values: Mapping[tuple[str, str], Iterable[str]] | None,
) -> dict[tuple[str, str], frozenset[str]]:
    normalized: dict[tuple[str, str], frozenset[str]] = {}
    if values is None:
        return normalized
    for key, identities in values.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(value, str) and value for value in key)
        ):
            raise ValueError("identity-bound control keys must be product/function pairs")
        selected = frozenset(identities)
        if not selected:
            raise ValueError("identity-bound controls require at least one physical identity")
        if any(
            len(identity) != 64
            or any(character not in "0123456789abcdef" for character in identity)
            for identity in selected
        ):
            raise ValueError("identity-bound controls require lowercase SHA-256 identities")
        normalized[key] = selected
    return normalized


def _native_range(
    product_key: str,
    function: AppFunction,
) -> tuple[int | None, int | None]:
    if function.name == "slave_per_slot_flow_extension":
        return declared_app_integer_range(product_key, "manual_flow")
    return function.app_minimum, function.app_maximum


def _english_name(function: str, product_key: str | None = None) -> str:
    override = _ENGLISH_NAME_BY_PRODUCT_FUNCTION.get((product_key, function))
    if override is not None:
        return override
    return " ".join(part.capitalize() for part in function.split("_"))


def public_function_value(product_key: str, function: str, value: object) -> object:
    """Translate a stable internal enum identity only at the public presentation boundary."""

    if (
        product_key == "6a5c47b3ea364ecb841b47f5997a1775"
        and function == "transport_mode"
        and type(value) is bool
    ):
        return "wireless" if value else "ap"
    if not isinstance(value, str):
        return value
    return _PUBLIC_VALUE_BY_PRODUCT_FUNCTION.get((product_key, function, value), value)


def internal_function_value(product_key: str, function: str, value: object) -> object:
    """Translate an English presentation value back to its durable protocol identity."""

    if product_key == "6a5c47b3ea364ecb841b47f5997a1775" and function == "transport_mode":
        if value not in ("ap", "wireless"):
            raise ValueError("Aquarium transport mode requires ap or wireless")
        return value == "wireless"
    if not isinstance(value, str):
        return value
    return _INTERNAL_VALUE_BY_PRODUCT_FUNCTION.get((product_key, function, value), value)


def _validate_value(descriptor: LocalFunctionDescriptor, value: object) -> None:
    kind = descriptor.kind
    if descriptor.product_key == "5b3c136fd4b74f3fb2a366a254c76c9a" and descriptor.function in {
        "manual_channel",
        "channel_timer_enable",
        "interval_days",
    }:
        if not isinstance(value, dict) or set(value) != {"channel", "value"}:
            raise ValueError("Dosing channel controls require channel and value")
        channel = value["channel"]
        if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 4:
            raise ValueError("Dosing channel must be between 1 and 4")
        value = value["value"]
    if kind is AppFunctionKind.BOOLEAN:
        if not isinstance(value, bool):
            raise TypeError("boolean app controls require true or false")
        return
    if kind is AppFunctionKind.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("integer app controls require an integer")
        if descriptor.native_minimum is not None and value < descriptor.native_minimum:
            raise ValueError("integer app control is below its declared native range")
        if descriptor.native_maximum is not None and value > descriptor.native_maximum:
            raise ValueError("integer app control is above its declared native range")
        if descriptor.native_step is not None:
            origin = descriptor.native_minimum or 0
            if (value - origin) % descriptor.native_step:
                raise ValueError("integer app control does not match its declared step")
        return
    if kind is AppFunctionKind.ENUM:
        if not isinstance(value, str):
            raise TypeError("enum app controls require an ASCII option")
        if value not in descriptor.choices:
            raise ValueError("enum app control is not in the audited app choices")
        return
    if kind is AppFunctionKind.SCHEDULE:
        if not isinstance(value, (dict, list)):
            raise TypeError("schedule controls require a structured JSON value")
        return
    if kind is AppFunctionKind.ACTION:
        if value not in (None, {}):
            raise ValueError("action controls do not accept an implicit payload")
        return
    raise ValueError("status and lifecycle functions are not physical control intents")


def _require_ascii(value: object) -> None:
    if isinstance(value, str):
        if not value.isascii():
            raise ValueError("public local-control output must be English ASCII")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _require_ascii(key)
            _require_ascii(item)
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _require_ascii(item)


__all__ = [
    "LocalControlCatalog",
    "LocalFunctionDescriptor",
    "build_local_control_catalog",
    "internal_function_value",
    "public_function_value",
]
