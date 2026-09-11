"""Derived Jebao Aqua app function inventory used as an observation catalog.

The catalog is not a write implementation and is not firmware truth. It records the app surface
seen in Jebao Aqua 3.4.18 plus the vendor wire declarations described in the repository's static
analysis. Accepted and physical behavior remain UNKNOWN until captured evidence promotes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_PRODUCT_KEY_RE = re.compile(r"[0-9a-f]{32}\Z")
_PUBLIC_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")


def _validate_ascii(value: str, description: str) -> None:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError(f"{description} must use non-empty ASCII text")
    if any(ord(character) < 0x20 for character in value):
        raise ValueError(f"{description} must not contain control characters")


class AppFunctionKind(StrEnum):
    BOOLEAN = "boolean"
    ENUM = "enum"
    INTEGER = "integer"
    SCHEDULE = "schedule"
    ACTION = "action"
    DEVICE_LIFECYCLE = "device_lifecycle"
    STATUS = "status"


class CapabilityConfidence(StrEnum):
    APP_STATIC = "app_static"
    VENDOR_DECLARED = "vendor_declared"
    ACCEPTED_CAPTURE_REQUIRED = "accepted_capture_required"


@dataclass(frozen=True, slots=True)
class AppFunction:
    name: str
    kind: AppFunctionKind
    fields: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    app_minimum: int | None = None
    app_maximum: int | None = None
    count: int | None = None
    item_size: int | None = None
    confidence: CapabilityConfidence = CapabilityConfidence.APP_STATIC
    caveat: str | None = None

    def __post_init__(self) -> None:
        if _PUBLIC_TOKEN_RE.fullmatch(self.name) is None:
            raise ValueError("app function name must be a lowercase ASCII token")
        if not isinstance(self.kind, AppFunctionKind):
            raise TypeError("app function kind must be an AppFunctionKind")
        if not isinstance(self.confidence, CapabilityConfidence):
            raise TypeError("app function confidence must be a CapabilityConfidence")
        for description, values in (("field", self.fields), ("choice", self.choices)):
            for value in values:
                _validate_ascii(value, f"app function {description}")
        if self.caveat is not None:
            _validate_ascii(self.caveat, "app function caveat")
        for description, value in (
            ("minimum", self.app_minimum),
            ("maximum", self.app_maximum),
            ("count", self.count),
            ("item size", self.item_size),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError(f"app function {description} must be an integer")
        if (
            self.app_minimum is not None
            and self.app_maximum is not None
            and self.app_minimum > self.app_maximum
        ):
            raise ValueError("app function minimum exceeds its maximum")
        if self.count is not None and self.count < 1:
            raise ValueError("app function count must be positive")
        if self.item_size is not None and self.item_size < 1:
            raise ValueError("app function item size must be positive")

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "fields": list(self.fields),
            "choices": list(self.choices),
            "app_minimum": self.app_minimum,
            "app_maximum": self.app_maximum,
            "count": self.count,
            "item_size": self.item_size,
            "confidence": self.confidence.value,
            "caveat": self.caveat,
        }


@dataclass(frozen=True, slots=True)
class AppProductCatalog:
    product_key: str
    family: str
    registered_instances: int
    functions: tuple[AppFunction, ...]

    def __post_init__(self) -> None:
        if _PRODUCT_KEY_RE.fullmatch(self.product_key) is None:
            raise ValueError("app product key must be lowercase 32-hex")
        if _PUBLIC_TOKEN_RE.fullmatch(self.family) is None:
            raise ValueError("app product family must be a lowercase ASCII token")
        if self.registered_instances < 1:
            raise ValueError("registered instance count must be positive")
        names = [function.name for function in self.functions]
        if len(names) != len(set(names)):
            raise ValueError("app product functions must be unique")

    def public_dict(self) -> dict[str, object]:
        return {
            "product_key": self.product_key,
            "family": self.family,
            "registered_instances": self.registered_instances,
            "functions": [function.public_dict() for function in self.functions],
        }


_POWER = AppFunction("power", AppFunctionKind.BOOLEAN, ("SwitchON",))
_TIMER = AppFunction("schedule_enable", AppFunctionKind.BOOLEAN, ("TimerON",))
_CLOCK = AppFunction(
    "device_clock_sync",
    AppFunctionKind.ACTION,
    ("YMDData", "HMSData"),
    caveat="app writes YMD and zero-prefixed HMS in one command",
)
_PUMP_FAULTS = AppFunction(
    "fault_status",
    AppFunctionKind.STATUS,
    (
        "Fault_Overcurrent",
        "Fault_Overvoltage",
        "Fault_OverTemp",
        "Fault_Undervoltage",
        "Fault_Lockedrotor",
        "Fault_no_liveload",
        "Fault_UART",
    ),
    confidence=CapabilityConfidence.VENDOR_DECLARED,
)

LOCAL_WAVEMAKER_PRO_APP = AppProductCatalog(
    product_key="50dbc92221fd4d33ae69a1fedd43b555",
    family="local_wavemaker_pro",
    registered_instances=2,
    functions=(
        _POWER,
        AppFunction(
            "schedule_enable",
            AppFunctionKind.BOOLEAN,
            ("TimerON", "SwitchON", "Linkage"),
            caveat=(
                "app writes inverse SwitchON and returns a current slave Linkage to independent"
            ),
        ),
        AppFunction(
            "linkage_role",
            AppFunctionKind.ENUM,
            ("Linkage",),
            ("independent", "master", "sync_slave", "async_slave"),
        ),
        AppFunction(
            "manual_mode",
            AppFunctionKind.ENUM,
            ("Mode",),
            (
                "pulse",
                "sine",
                "constant",
                "random",
                "tidal",
                "nutrient_transport",
                "Cross-flow",
                "feed",
                "custom",
            ),
        ),
        AppFunction(
            "manual_flow", AppFunctionKind.INTEGER, ("Flow",), app_minimum=30, app_maximum=100
        ),
        AppFunction(
            "manual_frequency",
            AppFunctionKind.INTEGER,
            ("Frequency",),
            app_minimum=5,
            app_maximum=100,
        ),
        AppFunction(
            "custom_wave_frequency",
            AppFunctionKind.INTEGER,
            ("Cust_Wav_Freq",),
            app_minimum=5,
            app_maximum=100,
        ),
        AppFunction(
            "feeding_duration",
            AppFunctionKind.INTEGER,
            ("FeedTime",),
            app_minimum=1,
            app_maximum=60,
        ),
        AppFunction(
            "schedule",
            AppFunctionKind.SCHEDULE,
            (
                "start_hour",
                "start_minute",
                "end_hour",
                "end_minute",
                "mode",
                "flow",
                "frequency",
                "feed_time",
                "custom_frequency",
            ),
            count=48,
            item_size=9,
            caveat="schedule feeding duration above vendor 1..60 remains unverified",
        ),
        AppFunction(
            "slave_common_flow",
            AppFunctionKind.INTEGER,
            ("Flow",),
            app_minimum=30,
            app_maximum=100,
            caveat="the app exposes one common slave slider, not a slave slot editor",
        ),
        AppFunction(
            "slave_per_slot_flow_extension",
            AppFunctionKind.SCHEDULE,
            ("master_slot_copy", "slave_flow_override"),
            count=48,
            item_size=9,
            confidence=CapabilityConfidence.ACCEPTED_CAPTURE_REQUIRED,
            caveat="engine extension; firmware content/timing ownership is UNKNOWN",
        ),
        AppFunction(
            "current_schedule_state",
            AppFunctionKind.STATUS,
            ("AutoMode", "AutoFlow", "AutoFreq", "Auto_Cust_Wav_Freq", "AutoFeedTime"),
            confidence=CapabilityConfidence.VENDOR_DECLARED,
        ),
        AppFunction(
            "schedule_reset",
            AppFunctionKind.ACTION,
            ("AutoTime00..47",),
            caveat="app writes one command containing 48 slots of nine 0xee bytes",
        ),
        AppFunction(
            "device_clock_sync",
            AppFunctionKind.ACTION,
            ("YMDData", "HMSData"),
            caveat="app writes YMD and zero-prefixed HMS in one command",
        ),
        _PUMP_FAULTS,
    ),
)

LOCAL_WAVEMAKER_APP = AppProductCatalog(
    product_key="1d8c63eaccac4205b92c84d77d5a08fb",
    family="local_wavemaker",
    registered_instances=1,
    functions=(
        _POWER,
        AppFunction(
            "schedule_enable",
            AppFunctionKind.BOOLEAN,
            ("TimerON", "Flow", "Frequency", "Mode", "PulseTide", "Linkage", "FeedSwitch"),
            caveat="app command shape depends on the selected timer screen and current auto state",
        ),
        AppFunction("feed_switch", AppFunctionKind.BOOLEAN, ("FeedSwitch",)),
        AppFunction("pulse_or_tide", AppFunctionKind.BOOLEAN, ("PulseTide",)),
        AppFunction(
            "linkage_role",
            AppFunctionKind.ENUM,
            ("Linkage",),
            ("independent", "master", "slave"),
        ),
        AppFunction(
            "manual_mode",
            AppFunctionKind.ENUM,
            ("Mode",),
            ("classic", "sine", "random", "constant"),
        ),
        AppFunction(
            "manual_flow", AppFunctionKind.INTEGER, ("Flow",), app_minimum=30, app_maximum=100
        ),
        AppFunction(
            "manual_frequency",
            AppFunctionKind.INTEGER,
            ("Frequency",),
            app_minimum=5,
            app_maximum=100,
        ),
        AppFunction(
            "feeding_duration",
            AppFunctionKind.INTEGER,
            ("FeedTime",),
            app_minimum=1,
            app_maximum=60,
        ),
        AppFunction(
            "schedule",
            AppFunctionKind.SCHEDULE,
            (
                "start_hour",
                "start_minute",
                "end_hour",
                "end_minute",
                "mode",
                "flow_or_feed_time",
                "frequency",
                "pulse_or_tide",
            ),
            count=48,
            item_size=8,
            caveat="app replaces all 48 slots and writes 0xee for unused positions",
        ),
        AppFunction(
            "current_schedule_state",
            AppFunctionKind.STATUS,
            ("AutoPulseTide", "AutoMode", "AutoFlow", "AutoFreq", "AutoFeedTime"),
            confidence=CapabilityConfidence.VENDOR_DECLARED,
        ),
        _CLOCK,
        _PUMP_FAULTS,
    ),
)

DC_PUMP_PRO_APP = AppProductCatalog(
    product_key="0696a19599bc484f8e1866f5ccf4ee7e",
    family="dc_pump_pro",
    registered_instances=1,
    functions=(
        _POWER,
        AppFunction(
            "schedule_enable",
            AppFunctionKind.BOOLEAN,
            ("TimerON", "SwitchON"),
            confidence=CapabilityConfidence.ACCEPTED_CAPTURE_REQUIRED,
            caveat="two audited app screens use conflicting SwitchON coupling",
        ),
        AppFunction(
            "manual_mode",
            AppFunctionKind.ENUM,
            ("Mode",),
            ("constant", "pulse", "sine", "random", "feed"),
        ),
        AppFunction(
            "manual_flow", AppFunctionKind.INTEGER, ("Flow",), app_minimum=30, app_maximum=100
        ),
        AppFunction(
            "manual_frequency",
            AppFunctionKind.INTEGER,
            ("Frequency",),
            app_minimum=5,
            app_maximum=100,
        ),
        AppFunction(
            "feeding_duration",
            AppFunctionKind.INTEGER,
            ("FeedTime",),
            app_minimum=1,
            app_maximum=60,
        ),
        AppFunction(
            "schedule",
            AppFunctionKind.SCHEDULE,
            (
                "start_hour",
                "start_minute",
                "end_hour",
                "end_minute",
                "mode",
                "flow",
                "frequency",
                "feed_time",
            ),
            count=48,
            item_size=8,
            caveat="schedule mode codes 1 and 2 conflict across vendor declarations",
        ),
        AppFunction(
            "current_schedule_state",
            AppFunctionKind.STATUS,
            ("AutoMode", "AutoFlow", "AutoFreq", "AutoFeedTime"),
            confidence=CapabilityConfidence.VENDOR_DECLARED,
        ),
        AppFunction(
            "schedule_reset",
            AppFunctionKind.ACTION,
            ("AutoTime00..47",),
            caveat="app writes one command containing 48 slots of eight 0xee bytes",
        ),
        _CLOCK,
        _PUMP_FAULTS,
    ),
)

AQUARIUM_PUMP_APP = AppProductCatalog(
    product_key="6a5c47b3ea364ecb841b47f5997a1775",
    family="aquarium_pump",
    registered_instances=1,
    functions=(
        _POWER,
        AppFunction(
            "schedule_enable",
            AppFunctionKind.BOOLEAN,
            ("TimerON", "Motor_Speed", "FeedSwitch", "AutoGears", "AutoMode"),
            caveat="app command shape depends on current mode and current auto state",
        ),
        AppFunction(
            "transport_mode",
            AppFunctionKind.ENUM,
            ("Mode",),
            ("ap", "wireless"),
            confidence=CapabilityConfidence.ACCEPTED_CAPTURE_REQUIRED,
            caveat="boolean-to-label direction requires an accepted capture",
        ),
        AppFunction("feed_switch", AppFunctionKind.BOOLEAN, ("FeedSwitch",)),
        AppFunction(
            "motor_speed",
            AppFunctionKind.INTEGER,
            ("Motor_Speed",),
            app_minimum=30,
            app_maximum=100,
        ),
        AppFunction(
            "feeding_duration",
            AppFunctionKind.INTEGER,
            ("FeedTime",),
            app_minimum=1,
            app_maximum=60,
        ),
        AppFunction(
            "schedule",
            AppFunctionKind.SCHEDULE,
            ("start_hour", "start_minute", "end_hour", "end_minute", "mode", "gear_or_pause"),
            count=48,
            item_size=6,
            caveat=(
                "app replaces all 48 slots and writes 0xee for unused positions; accepted zero "
                "speed remains unverified"
            ),
        ),
        AppFunction(
            "current_schedule_state",
            AppFunctionKind.STATUS,
            ("AutoMode", "AutoGears", "AutoFeedTime"),
            confidence=CapabilityConfidence.VENDOR_DECLARED,
        ),
        _CLOCK,
        _PUMP_FAULTS,
    ),
)

DOSING_PUMP_APP = AppProductCatalog(
    product_key="5b3c136fd4b74f3fb2a366a254c76c9a",
    family="dosing_pump_four_channel",
    registered_instances=1,
    functions=(
        AppFunction("power", AppFunctionKind.BOOLEAN, ("switch",)),
        AppFunction(
            "manual_channel", AppFunctionKind.BOOLEAN, ("channe1", "channe2", "channe3", "channe4")
        ),
        AppFunction(
            "channel_timer_enable",
            AppFunctionKind.BOOLEAN,
            ("Timer1ON", "Timer2ON", "Timer3ON", "Timer4ON"),
        ),
        AppFunction(
            "interval_days",
            AppFunctionKind.INTEGER,
            ("IntervalT1", "IntervalT2", "IntervalT3", "IntervalT4"),
            app_minimum=0,
            app_maximum=30,
        ),
        AppFunction(
            "calibration_channel",
            AppFunctionKind.ENUM,
            ("CALSet",),
            ("channel_1", "channel_2", "channel_3", "channel_4"),
        ),
        AppFunction(
            "calibration",
            AppFunctionKind.ACTION,
            ("CALSW", "Calib1"),
            app_minimum=10,
            app_maximum=100,
            caveat="accepted calibration and dose ranges remain unverified",
        ),
        AppFunction(
            "channel_schedule",
            AppFunctionKind.SCHEDULE,
            ("hour", "minute", "volume_uint16_be"),
            count=24,
            item_size=4,
            caveat=(
                "app replaces one complete 96-byte channel image; device acceptance and volume "
                "unit remain unverified"
            ),
        ),
        AppFunction(
            "calibration_duration",
            AppFunctionKind.STATUS,
            ("time1",),
            app_minimum=10,
            app_maximum=250,
            confidence=CapabilityConfidence.VENDOR_DECLARED,
            caveat="vendor declares no unit; app English label is Calibration duration",
        ),
        AppFunction(
            "fault_status",
            AppFunctionKind.STATUS,
            ("OpenCircuit", "Fault_UART"),
            confidence=CapabilityConfidence.VENDOR_DECLARED,
        ),
    ),
)

REGISTERED_APP_PRODUCTS = {
    item.product_key: item
    for item in (
        LOCAL_WAVEMAKER_PRO_APP,
        LOCAL_WAVEMAKER_APP,
        DC_PUMP_PRO_APP,
        AQUARIUM_PUMP_APP,
        DOSING_PUMP_APP,
    )
}


def declared_app_integer_range(product_key: str, function_name: str) -> tuple[int, int]:
    """Return one product function's audited integer range without copying numeric policy."""

    try:
        product = REGISTERED_APP_PRODUCTS[product_key]
    except KeyError as error:
        raise KeyError("product is not in the registered app catalog") from error
    matches = tuple(function for function in product.functions if function.name == function_name)
    if len(matches) != 1:
        raise KeyError("function is not unique in the registered app catalog")
    function = matches[0]
    if (
        function.kind is not AppFunctionKind.INTEGER
        or function.app_minimum is None
        or function.app_maximum is None
    ):
        raise ValueError("app function does not declare a complete integer range")
    return function.app_minimum, function.app_maximum

APP_SHELL_FUNCTIONS = (
    AppFunction("device_discovery_and_binding", AppFunctionKind.DEVICE_LIFECYCLE),
    AppFunction(
        "ownership_permission_check",
        AppFunctionKind.STATUS,
        ("checkHomeOwner",),
        caveat="account authorization fact; no local control equivalent is implied",
    ),
    AppFunction("device_rename", AppFunctionKind.DEVICE_LIFECYCLE, ("renameDevice",)),
    AppFunction("room_assignment", AppFunctionKind.DEVICE_LIFECYCLE, ("goEditRooms",)),
    AppFunction("device_delete", AppFunctionKind.DEVICE_LIFECYCLE, ("deleteDevice",)),
    AppFunction("automation_scene_entry", AppFunctionKind.DEVICE_LIFECYCLE, ("goToDeviceScenes",)),
    AppFunction("device_group_view", AppFunctionKind.DEVICE_LIFECYCLE, ("deviceGroup",)),
    AppFunction("group_edit", AppFunctionKind.DEVICE_LIFECYCLE, ("editGroup",)),
    AppFunction("device_status_refresh", AppFunctionKind.STATUS, ("getDeviceStatus",)),
    AppFunction("channel_name_lookup", AppFunctionKind.STATUS, ("getSwitchNames",)),
    AppFunction(
        "universal_menu",
        AppFunctionKind.STATUS,
        ("isSupportUniversalMenu", "getUniversalMenu"),
    ),
    AppFunction(
        "firmware_update_entry",
        AppFunctionKind.DEVICE_LIFECYCLE,
        ("goToUpdate",),
        caveat="vendor firmware service; a local replacement is not implied",
    ),
)

APP_HOST_BRIDGE_CALLS = tuple(
    sorted(
        {
            "checkHomeOwner",
            "deleteDevice",
            "deviceGroup",
            "editGroup",
            "getDeviceStatus",
            "getSwitchNames",
            "getUniversalMenu",
            "goEditRooms",
            "goToDeviceScenes",
            "goToUpdate",
            "isSupportUniversalMenu",
            "renameDevice",
            "sendCmd",
            "toast",
        }
    )
)


def public_app_catalog(product_key: str | None = None) -> dict[str, object]:
    if product_key is not None:
        try:
            products = (REGISTERED_APP_PRODUCTS[product_key],)
        except KeyError as error:
            raise KeyError("product is not in the registered app catalog") from error
    else:
        products = tuple(REGISTERED_APP_PRODUCTS.values())
    return {
        "schema_version": 1,
        "language": "en",
        "source_app": {"name": "Jebao Aqua", "version": "3.4.18", "build": "341800000"},
        "active_template_version": "unresolved",
        "semantic_boundary": "static_app_and_vendor_declaration_not_firmware_acceptance",
        "host_bridge_calls": list(APP_HOST_BRIDGE_CALLS),
        "products": [product.public_dict() for product in products],
        "app_shell_functions": [function.public_dict() for function in APP_SHELL_FUNCTIONS],
    }


__all__ = [
    "APP_HOST_BRIDGE_CALLS",
    "APP_SHELL_FUNCTIONS",
    "REGISTERED_APP_PRODUCTS",
    "AppFunction",
    "AppFunctionKind",
    "AppProductCatalog",
    "CapabilityConfidence",
    "declared_app_integer_range",
    "public_app_catalog",
]
