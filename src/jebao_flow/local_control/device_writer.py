"""The sole local-control edge that can construct and send device-control frames.

All callers prepare a rollback payload from a fresh explicit state read before the first send.
This module never retries a control frame. An exception after ``send_once`` begins is an uncertain
physical outcome and the caller must preserve it in the separate local-control journal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from jebao_flow.config import AppConfig
from jebao_flow.local_control.bar_timer import prepare_bar_timer_command
from jebao_flow.local_control.catalog import internal_function_value
from jebao_flow.local_control.dc_timer import DcTimerScreen, prepare_dc_timer_command
from jebao_flow.local_control.device_clock import clock_expectation, clock_readback_matches
from jebao_flow.local_control.lease import LocalControlSendPermit
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.resolver import ResolvedEndpoint
from jebao_flow.local_control.schedule import CompiledSlaveSchedule
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.control import CONTROL_ACTION, build_control_payload
from jebao_flow.protocol.control_session import GizwitsSession
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import DC_PUMP_PRO_PRODUCT_KEY
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE,
    LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE,
    LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE,
    LOCAL_WAVEMAKER_PRO_FIRST_SCHEDULE_DP_ID,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET,
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
)
from jebao_flow.protocol.session import RawStateCapture


class LocalDeviceWriteError(RuntimeError):
    """A local-control frame could not be safely prepared, sent, or verified."""


class RawWriterSession(Protocol):
    async def connect(self) -> None: ...

    async def authenticate(self) -> bytes: ...

    async def read_raw_state(self, *, accept_reports: bool = False) -> bytes: ...

    async def read_raw_state_capture(self, *, accept_reports: bool = False) -> RawStateCapture: ...

    async def send_raw_control(self, control_payload: bytes) -> bytes: ...

    async def disconnect(self) -> None: ...

    def quarantine(self) -> None: ...


SessionFactory = Callable[[str], RawWriterSession]


def configured_writer_factory(
    config: AppConfig,
) -> Callable[[ResolvedEndpoint], ConnectedDeviceWriter]:
    """Bind app-local timer time and per-device output limits from current private config."""
    timezone = ZoneInfo(config.instance.timezone)
    limits = {device.id: device.limits for device in config.devices}

    def create(endpoint: ResolvedEndpoint) -> ConnectedDeviceWriter:
        bounds = limits[endpoint.target.logical_member]
        return ConnectedDeviceWriter(
            endpoint,
            bar_timer_local_time=lambda: datetime.now(timezone),
            bar_timer_flow_bounds=(bounds.min_power, bounds.max_power),
            control_flow_bounds=(bounds.min_power, bounds.max_power),
            device_local_time=lambda: datetime.now(timezone),
        )

    return create


@dataclass(frozen=True, slots=True)
class _ScheduleGeometry:
    raw_status_size: int
    status_offset: int
    slot_size: int

    @property
    def image_size(self) -> int:
        return 48 * self.slot_size


_SCHEDULE_GEOMETRY_BY_PRODUCT = {
    "50dbc92221fd4d33ae69a1fedd43b555": _ScheduleGeometry(452, 11, 9),
    "1d8c63eaccac4205b92c84d77d5a08fb": _ScheduleGeometry(401, 8, 8),
    "0696a19599bc484f8e1866f5ccf4ee7e": _ScheduleGeometry(402, 9, 8),
    "6a5c47b3ea364ecb841b47f5997a1775": _ScheduleGeometry(302, 13, 6),
}
_RESETTABLE_SCHEDULE_PRODUCTS = frozenset(
    {
        LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        "0696a19599bc484f8e1866f5ccf4ee7e",
    }
)
_SINGLE_SLOT_SAVE_PRODUCTS = _RESETTABLE_SCHEDULE_PRODUCTS
_FULL_IMAGE_SAVE_PRODUCTS = frozenset(
    {
        "1d8c63eaccac4205b92c84d77d5a08fb",
        "6a5c47b3ea364ecb841b47f5997a1775",
    }
)

_SCALAR_FIELD_BY_PRODUCT: dict[str, dict[str, str]] = {
    "50dbc92221fd4d33ae69a1fedd43b555": {
        "power": "SwitchON",
        "schedule_enable": "TimerON",
        "linkage_role": "Linkage",
        "manual_mode": "Mode",
        "manual_flow": "Flow",
        "manual_frequency": "Frequency",
        "custom_wave_frequency": "Cust_Wav_Freq",
        "feeding_duration": "FeedTime",
        "slave_common_flow": "Flow",
    },
    "1d8c63eaccac4205b92c84d77d5a08fb": {
        "power": "SwitchON",
        "feed_switch": "FeedSwitch",
        "pulse_or_tide": "PulseTide",
        "linkage_role": "Linkage",
        "manual_mode": "Mode",
        "manual_flow": "Flow",
        "manual_frequency": "Frequency",
        "feeding_duration": "FeedTime",
    },
    "0696a19599bc484f8e1866f5ccf4ee7e": {
        "power": "SwitchON",
        "manual_mode": "Mode",
        "manual_flow": "Flow",
        "manual_frequency": "Frequency",
        "feeding_duration": "FeedTime",
    },
    "6a5c47b3ea364ecb841b47f5997a1775": {
        "power": "SwitchON",
        "feed_switch": "FeedSwitch",
        "motor_speed": "Motor_Speed",
        "feeding_duration": "FeedTime",
    },
    # Dosing control remains unavailable because its product schema intentionally has
    # control_supported=False and no accepted write evidence exists.
    "5b3c136fd4b74f3fb2a366a254c76c9a": {},
}
_FLOW_FUNCTIONS = frozenset({"manual_flow", "slave_common_flow", "motor_speed"})


@dataclass(frozen=True, slots=True)
class PreparedDeviceWrite:
    """One exact forward frame plus its freshly captured recovery authority."""

    target: ResolvedEndpoint
    function: str
    forward_payload: bytes = field(repr=False)
    rollback_payload: bytes = field(repr=False)
    baseline_status: bytes = field(repr=False)
    expected_readback: bytes = field(repr=False)
    baseline_readback: bytes = field(repr=False)
    verification_kind: str
    no_change: bool
    restore_on_recovery: bool = True
    recovery_policy: str = "restore_baseline"

    def __post_init__(self) -> None:
        if not self.forward_payload or self.forward_payload[0] != CONTROL_ACTION:
            raise ValueError("forward payload must be one control action")
        if not self.rollback_payload or self.rollback_payload[0] != CONTROL_ACTION:
            raise ValueError("rollback payload must be one control action")
        if self.verification_kind not in {"scalar_fields", "schedule_image", "device_clock"}:
            raise ValueError("prepared write verification kind is unsupported")
        if self.recovery_policy not in {"restore_baseline", "observe_clock"}:
            raise ValueError("unsupported recovery policy")
        if (self.recovery_policy == "observe_clock") != (self.verification_kind == "device_clock"):
            raise ValueError("clock writes permit observation-only recovery")
        if self.verification_kind == "device_clock" and self.function != "device_clock_sync":
            raise ValueError("clock verification belongs only to clock setting")
        if type(self.restore_on_recovery) is not bool:
            raise TypeError("prepared write recovery policy must be boolean")
        power_field = _SCALAR_FIELD_BY_PRODUCT.get(
            self.target.target.product_key,
            {},
        ).get("power")
        if not self.restore_on_recovery and (
            self.function != "power"
            or self.verification_kind != "scalar_fields"
            or json.loads(self.expected_readback.decode("ascii")) != {power_field: False}
        ):
            raise ValueError("only an exact power-off action may retain its forward state")

    @property
    def forward_payload_sha256(self) -> str:
        return hashlib.sha256(self.forward_payload).hexdigest()

    @property
    def rollback_payload_sha256(self) -> str:
        return hashlib.sha256(self.rollback_payload).hexdigest()


class ConnectedDeviceWriter:
    """One current resolved endpoint and one non-reusable authenticated session."""

    def __init__(
        self,
        endpoint: ResolvedEndpoint,
        *,
        session_factory: SessionFactory = GizwitsSession,
        bar_timer_local_time: Callable[[], datetime] | None = None,
        bar_timer_flow_bounds: tuple[int, int] | None = None,
        control_flow_bounds: tuple[int, int] | None = None,
        device_local_time: Callable[[], datetime] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self._session = session_factory(endpoint.address)
        self._connected = False
        self._attempted_payloads: set[str] = set()
        self._bar_timer_local_time = bar_timer_local_time
        self._bar_timer_flow_bounds = bar_timer_flow_bounds
        self._control_flow_bounds = control_flow_bounds
        self._device_local_time = device_local_time

    async def connect_and_read(self) -> bytes:
        if self._connected:
            raise LocalDeviceWriteError("writer session is already connected")
        try:
            await self._session.connect()
            await self._session.authenticate()
            self._connected = True
            return await self._session.read_raw_state(accept_reports=False)
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise

    async def connect_and_capture(self, *, accept_reports: bool) -> RawStateCapture:
        """Native lifecycle만 보고 프레임을 명시적으로 허용한다. 기존 읽기는 불변이다."""
        if self._connected:
            raise LocalDeviceWriteError("writer session is already connected")
        try:
            await self._session.connect()
            await self._session.authenticate()
            self._connected = True
            return await self._session.read_raw_state_capture(accept_reports=accept_reports)
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise

    async def capture_readback(
        self,
        lease: LocalControlSendPermit,
        *,
        accept_reports: bool,
    ) -> RawStateCapture:
        if not self._connected:
            raise LocalDeviceWriteError("writer session is not connected")
        lease.require_permitted()
        try:
            capture = await self._session.read_raw_state_capture(accept_reports=accept_reports)
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise
        lease.require_permitted()
        return capture

    def prepare_native_controls(
        self,
        expected: Mapping[str, Any],
        original_status: bytes,
    ) -> PreparedDeviceWrite:
        """한 원자적 제어 frame. 실행·원복 방향은 native journal 소유자만 결정한다."""
        product = self.endpoint.target.product_key
        allowed = {
            "SwitchON",
            "TimerON",
            "Linkage",
            "Mode",
            "Flow",
            "Frequency",
            "FeedTime",
            "Cust_Wav_Freq",
        }
        if (
            product != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            or not expected
            or not set(expected) <= allowed
        ):
            raise ValueError("native controls require explicit Pro control fields")
        schema = get_product_schema(product)
        values = schema.decode_status(original_status)
        baseline = {name: values[name] for name in expected}
        return PreparedDeviceWrite(
            target=self.endpoint,
            function="native_controls",
            forward_payload=build_control_payload(schema, expected),
            rollback_payload=build_control_payload(schema, baseline),
            baseline_status=original_status,
            expected_readback=_canonical_values(expected),
            baseline_readback=_canonical_values(baseline),
            verification_kind="scalar_fields",
            no_change=baseline == expected,
        )

    def prepare_native_image(self, image: bytes, original_status: bytes) -> PreparedDeviceWrite:
        product = self.endpoint.target.product_key
        if product != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("native image write is Pro-only")
        baseline = extract_current_pro_schedule_image(original_status)
        return PreparedDeviceWrite(
            target=self.endpoint,
            function="native_image",
            forward_payload=build_exact_schedule_payload(product, image),
            rollback_payload=build_exact_schedule_payload(product, baseline),
            baseline_status=original_status,
            expected_readback=image,
            baseline_readback=baseline,
            verification_kind="schedule_image",
            no_change=image == baseline,
        )

    def prepare_native_restore_controls(self, original_status: bytes) -> PreparedDeviceWrite:
        """자기 원본 제어 bytes 복원. inactive FeedTime=0도 앱 최솟값으로 정상화하지 않는다.

        flags는 알려진 8개만 켠다. 원본 소유권·출력 범위는 lifecycle이 첫 write 전에 검사한다.
        임의 새 제어값 입력을 받지 않는다.
        """
        if self.endpoint.target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("native control restore is Pro-only")
        schema = get_product_schema(self.endpoint.target.product_key)
        values = schema.decode_status(original_status)
        fields = (
            "SwitchON",
            "TimerON",
            "Linkage",
            "Mode",
            "Flow",
            "Frequency",
            "FeedTime",
            "Cust_Wav_Freq",
        )
        flags = bytearray(schema.attribute_flags_size)
        for name in fields:
            attribute = schema.by_name(name)
            flags[attribute.id // 8] |= 1 << (attribute.id % 8)
        payload = (
            bytes([CONTROL_ACTION])
            + bytes(reversed(flags))
            + original_status[: schema.attribute_values_size]
        )
        expected = _canonical_values({name: values[name] for name in fields})
        return PreparedDeviceWrite(
            target=self.endpoint,
            function="native_controls",
            forward_payload=payload,
            rollback_payload=payload,
            baseline_status=original_status,
            expected_readback=expected,
            baseline_readback=expected,
            verification_kind="scalar_fields",
            no_change=True,
        )

    def prepare_scalar(
        self,
        intent: LocalControlIntent,
        current_status: bytes,
    ) -> PreparedDeviceWrite:
        if not _same_stable_target(intent.target, self.endpoint.target):
            raise ValueError("control intent target does not match the current endpoint")
        if intent.function == "schedule":
            return self._prepare_wavemaker_schedule(intent, current_status)
        if intent.function == "schedule_reset":
            return self._prepare_schedule_reset(intent, current_status)
        if intent.function == "device_clock_sync":
            return self._prepare_clock(intent, current_status)
        if intent.target.product_key == DC_PUMP_PRO_PRODUCT_KEY and intent.function in {
            "schedule_enable",
            "manual_enable",
        }:
            return self._prepare_dc_timer(intent, current_status)
        if (
            intent.target.product_key == "1d8c63eaccac4205b92c84d77d5a08fb"
            and intent.function == "schedule_enable"
        ):
            return self._prepare_bar_timer(intent, current_status)
        product_fields = _SCALAR_FIELD_BY_PRODUCT.get(intent.target.product_key)
        if product_fields is None:
            raise LocalDeviceWriteError("product is not in the local-control encoder catalog")
        try:
            field_name = product_fields[intent.function]
        except KeyError as error:
            raise LocalDeviceWriteError(
                "function has no audited scalar encoder for this product"
            ) from error
        if field_name.startswith("Auto"):
            raise AssertionError("current Auto* status must never become a scalar write target")
        if intent.function in _FLOW_FUNCTIONS:
            if isinstance(intent.value, bool) or not isinstance(intent.value, int):
                raise TypeError("Flow/speed control requires an integer")
            minimum, maximum = declared_app_integer_range(
                intent.target.product_key,
                intent.function,
            )
            if not minimum <= intent.value <= maximum:
                raise ValueError(
                    "Flow/speed control must stay inside the declared "
                    f"{minimum}..{maximum} app range"
                )
            if self._control_flow_bounds is not None:
                lower, upper = self._control_flow_bounds
                if not lower <= intent.value <= upper:
                    raise LocalDeviceWriteError(
                        f"requested output {intent.value} exceeds configured {lower}..{upper}"
                    )

        schema = get_product_schema(intent.target.product_key)
        values = schema.decode_status(current_status)
        if (
            intent.target.product_key == DC_PUMP_PRO_PRODUCT_KEY
            and intent.function == "power"
            and intent.value is True
            and self._control_flow_bounds is not None
        ):
            self._require_dc_output_bounds(values, timer_enabled=values["TimerON"])
        internal_value = internal_function_value(
            intent.target.product_key,
            intent.function,
            intent.value,
        )
        expected: dict[str, Any] = {field_name: internal_value}
        if (
            intent.target.product_key == LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            and intent.function == "schedule_enable"
        ):
            if not isinstance(intent.value, bool):
                raise TypeError("schedule enable control requires true or false")
            # The audited app always couples TimerON with the inverse SwitchON state. When the
            # current device is a slave, that same app command also returns Linkage to independent.
            # Keeping all three fields in the expected readback prevents an app-impossible partial
            # TimerON write from being reported as verified.
            expected["SwitchON"] = not intent.value
            if values["Linkage"] in {"sync_slave", "async_slave"}:
                expected["Linkage"] = "independent"
        baseline = {name: values[name] for name in expected}
        forward = build_control_payload(schema, expected)
        if (
            intent.target.product_key == LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
            and intent.function == "feeding_duration"
            and baseline[field_name] == 0
        ):
            # 실제 원본의 inactive 0은 새 입력 0과 다르다. native 원본 복원과 같은
            # raw-copy 계약이지만 이 scalar가 소유한 FeedTime flag 하나만 선택한다.
            flags = bytearray(schema.attribute_flags_size)
            attribute = schema.by_name(field_name)
            flags[attribute.id // 8] |= 1 << (attribute.id % 8)
            rollback = (
                bytes([CONTROL_ACTION])
                + bytes(reversed(flags))
                + current_status[: schema.attribute_values_size]
            )
        else:
            rollback = build_control_payload(schema, baseline)
        return PreparedDeviceWrite(
            target=self.endpoint,
            function=intent.function,
            forward_payload=forward,
            rollback_payload=rollback,
            baseline_status=bytes(current_status),
            expected_readback=_canonical_values(expected),
            baseline_readback=_canonical_values(baseline),
            verification_kind="scalar_fields",
            no_change=expected == baseline,
            # Turning a pump off is already the safer terminal state. Recovery may classify a
            # fresh read as OFF or as the original baseline, but must never re-enable it merely
            # to recreate a less-safe pre-write state.
            restore_on_recovery=not (intent.function == "power" and intent.value is False),
        )

    def _prepare_clock(
        self, intent: LocalControlIntent, current_status: bytes
    ) -> PreparedDeviceWrite:
        if intent.target.product_key not in {
            DC_PUMP_PRO_PRODUCT_KEY,
            LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        }:
            raise LocalDeviceWriteError("clock execution is currently scoped to Pro/DC pumps")
        if intent.value is not None or self._device_local_time is None:
            raise ValueError("clock sync uses the configured local time, with no implicit payload")
        local_time = self._device_local_time()
        evidence = clock_expectation(local_time, datetime.now(UTC))
        payload = build_device_clock_payload(intent.target.product_key, local_time)
        return PreparedDeviceWrite(
            target=self.endpoint,
            function=intent.function,
            forward_payload=payload,
            # Storage compatibility only, not authority to resend this timestamp.
            # observe_clock recovery never enters the rollback sender.
            rollback_payload=payload,
            baseline_status=bytes(current_status),
            expected_readback=evidence,
            baseline_readback=evidence,
            verification_kind="device_clock",
            recovery_policy="observe_clock",
            no_change=False,
        )

    def _prepare_dc_timer(
        self, intent: LocalControlIntent, current_status: bytes
    ) -> PreparedDeviceWrite:
        if type(intent.value) is not bool:
            raise TypeError("DC operation control requires true or false")
        screen = (
            DcTimerScreen.SCHEDULE if intent.function == "schedule_enable" else DcTimerScreen.MANUAL
        )
        command = prepare_dc_timer_command(
            screen=screen,
            desired_timer_enabled=(
                intent.value if screen is DcTimerScreen.SCHEDULE else not intent.value
            ),
        )
        schema = get_product_schema(intent.target.product_key)
        values = schema.decode_status(current_status)
        expected = dict(command.fields)
        baseline = {name: values[name] for name in expected}
        if expected != baseline:
            # Bounds concern outputs that this command or its owned recovery can enable.
            # No Mode/Auto/clock equality, fixed initial value, or toggle-only admission.
            for state in (expected, baseline):
                if state["SwitchON"]:
                    self._require_dc_output_bounds(values, timer_enabled=state["TimerON"])
        return PreparedDeviceWrite(
            target=self.endpoint,
            function=intent.function,
            forward_payload=build_control_payload(schema, expected),
            rollback_payload=build_control_payload(schema, baseline),
            baseline_status=bytes(current_status),
            expected_readback=_canonical_values(expected),
            baseline_readback=_canonical_values(baseline),
            verification_kind="scalar_fields",
            no_change=expected == baseline,
        )

    def _require_dc_output_bounds(self, values: Mapping[str, Any], *, timer_enabled: bool) -> None:
        if self._control_flow_bounds is None:
            raise LocalDeviceWriteError("DC operation control requires configured output bounds")
        minimum, maximum = self._control_flow_bounds
        if timer_enabled:
            image = b"".join(values[f"AutoTime{index:02d}"] for index in range(48))
            self._require_dc_image_bounds(image)
        elif not minimum <= values["Flow"] <= maximum:
            raise LocalDeviceWriteError(
                f"DC manual flow {values['Flow']} exceeds {minimum}..{maximum}"
            )

    def _require_dc_image_bounds(self, image: bytes) -> None:
        minimum, maximum = self._control_flow_bounds or declared_app_integer_range(
            DC_PUMP_PRO_PRODUCT_KEY, "manual_flow"
        )
        for index in range(48):
            slot = image[index * 8 : (index + 1) * 8]
            # Audited app exclusions; no historical Auto or clock equality.
            if slot == b"\xee" * 8 or not any(slot[:4]) or slot[4] == 4:
                continue
            if not minimum <= slot[5] <= maximum:
                raise LocalDeviceWriteError(
                    f"DC schedule slot {index} flow {slot[5]} exceeds {minimum}..{maximum}"
                )

    def _prepare_bar_timer(
        self, intent: LocalControlIntent, current_status: bytes
    ) -> PreparedDeviceWrite:
        if self._bar_timer_local_time is None or self._bar_timer_flow_bounds is None:
            raise LocalDeviceWriteError(
                "Bar timer requires configured local time and output bounds"
            )
        local_time = self._bar_timer_local_time()
        if local_time.tzinfo is None or local_time.utcoffset() is None:
            raise ValueError("Bar timer local time must be timezone-aware")
        schema = get_product_schema(intent.target.product_key)
        values = schema.decode_status(current_status)
        if values["Linkage"] != "independent":
            raise LocalDeviceWriteError("Bar timer control requires independent ownership")
        minimum, maximum = self._bar_timer_flow_bounds
        if not minimum <= values["Flow"] <= maximum:
            raise LocalDeviceWriteError("Bar timer manual recovery flow is outside output bounds")
        image = extract_current_schedule_image(intent.target.product_key, current_status)
        arguments = dict(
            schedule_image=image,
            local_minute=local_time.hour * 60 + local_time.minute,
            minimum_flow=minimum,
            maximum_flow=maximum,
        )
        command = prepare_bar_timer_command(intent.value, values, **arguments)
        # A rollback that restores TimerON is authorized only inside the same output
        # envelope, including future slots. This is a range check, not an Auto snapshot
        # equality check. No claim that schedule bytes determine current Auto is made.
        if values["TimerON"]:
            prepare_bar_timer_command(True, values, **arguments)
            if values["AutoMode"] in {"classic", "sine", "random", "constant"} and not (
                minimum <= values["AutoFlow"] <= maximum
            ):
                raise LocalDeviceWriteError(
                    "Bar timer active recovery flow is outside output bounds"
                )
        expected = dict(command.persistent_readback)
        baseline = {name: values[name] for name in expected}
        return PreparedDeviceWrite(
            target=self.endpoint,
            function=intent.function,
            forward_payload=build_control_payload(schema, command.fields),
            # Auto* seeds are not owned persistent state. Neither restoration nor
            # verification replays or compares them against a historical snapshot.
            rollback_payload=build_control_payload(schema, baseline),
            baseline_status=bytes(current_status),
            expected_readback=_canonical_values(expected),
            baseline_readback=_canonical_values(baseline),
            verification_kind="scalar_fields",
            no_change=expected == baseline,
        )

    def _prepare_wavemaker_schedule(
        self,
        intent: LocalControlIntent,
        current_status: bytes,
    ) -> PreparedDeviceWrite:
        from jebao_flow.local_control.pump_preview import ReturnPumpSchedule
        from jebao_flow.local_control.wavemaker import WavemakerSchedule

        if not isinstance(intent.value, dict):
            raise ValueError("schedule must be a declared product object")
        model = (
            ReturnPumpSchedule
            if intent.value.get("family") in {"dc_pro", "aquarium"}
            else WavemakerSchedule
        )
        schedule = model.model_validate(intent.value)
        if schedule.product_key != intent.target.product_key:
            raise ValueError("시간표 가족과 실제 대상 장비가 다릅니다")
        values = get_product_schema(intent.target.product_key).decode_status(current_status)
        is_dc = intent.target.product_key == DC_PUMP_PRO_PRODUCT_KEY
        if not is_dc and (
            values["TimerON"]
            or (isinstance(schedule, WavemakerSchedule) and values["Linkage"] != "independent")
        ):
            raise LocalDeviceWriteError("시간표 교체는 독립 상태·TimerOFF에서만 준비합니다")
        baseline = extract_current_schedule_image(intent.target.product_key, current_status)
        desired = schedule.compile_image()
        if is_dc:
            # dcPro ScheduleDetail saves slots without turning the timer off. Validate
            # outputs that can run, rather than imposing a manual-mode prerequisite.
            self._require_dc_image_bounds(desired)
            if values["TimerON"] and values["SwitchON"]:
                self._require_dc_image_bounds(baseline)
        return PreparedDeviceWrite(
            target=self.endpoint,
            function="schedule",
            forward_payload=build_exact_schedule_payload(intent.target.product_key, desired),
            rollback_payload=build_exact_schedule_payload(intent.target.product_key, baseline),
            baseline_status=bytes(current_status),
            expected_readback=desired,
            baseline_readback=baseline,
            verification_kind="schedule_image",
            no_change=desired == baseline,
        )

    def _prepare_schedule_reset(
        self,
        intent: LocalControlIntent,
        current_status: bytes,
    ) -> PreparedDeviceWrite:
        if intent.target.product_key not in _RESETTABLE_SCHEDULE_PRODUCTS:
            raise LocalDeviceWriteError("product has no separate audited schedule reset action")
        if intent.value not in (None, {}):
            raise ValueError("schedule reset does not accept an implicit payload")
        baseline = extract_current_schedule_image(intent.target.product_key, current_status)
        geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[intent.target.product_key]
        desired = bytes([0xEE]) * geometry.image_size
        if intent.target.product_key == DC_PUMP_PRO_PRODUCT_KEY:
            values = get_product_schema(intent.target.product_key).decode_status(current_status)
            if values["TimerON"] and values["SwitchON"]:
                self._require_dc_image_bounds(baseline)
        return PreparedDeviceWrite(
            target=self.endpoint,
            function="schedule_reset",
            forward_payload=build_exact_schedule_payload(intent.target.product_key, desired),
            rollback_payload=build_exact_schedule_payload(intent.target.product_key, baseline),
            baseline_status=bytes(current_status),
            expected_readback=desired,
            baseline_readback=baseline,
            verification_kind="schedule_image",
            no_change=desired == baseline,
        )

    def prepare_pro_schedule(
        self,
        compiled: CompiledSlaveSchedule,
        current_status: bytes,
    ) -> PreparedDeviceWrite:
        if self.endpoint.target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise LocalDeviceWriteError("schedule composition is scoped to Local Wavemaker Pro")
        if compiled.physical_identity_key != self.endpoint.target.physical_identity_key:
            raise ValueError("compiled schedule target does not match the current endpoint")
        baseline = extract_current_pro_schedule_image(current_status)
        desired = bytes(compiled.image)
        forward = _build_exact_pro_schedule_payload(desired)
        rollback = _build_exact_pro_schedule_payload(baseline)
        return PreparedDeviceWrite(
            target=self.endpoint,
            function="slave_per_slot_flow_extension",
            forward_payload=forward,
            rollback_payload=rollback,
            baseline_status=bytes(current_status),
            expected_readback=desired,
            baseline_readback=baseline,
            verification_kind="schedule_image",
            no_change=desired == baseline,
        )

    async def send_once(
        self,
        payload: bytes,
        lease: LocalControlSendPermit,
    ) -> bytes:
        if not self._connected:
            raise LocalDeviceWriteError("writer session is not connected")
        exact = bytes(payload)
        digest = hashlib.sha256(exact).hexdigest()
        if digest in self._attempted_payloads:
            raise LocalDeviceWriteError("one connected writer never resends a control payload")
        lease.require_pre_send_permitted()
        self._attempted_payloads.add(digest)
        try:
            acknowledgement = await self._session.send_raw_control(exact)
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise
        lease.require_permitted()
        return acknowledgement

    async def readback(self, lease: LocalControlSendPermit) -> bytes:
        if not self._connected:
            raise LocalDeviceWriteError("writer session is not connected")
        lease.require_permitted()
        try:
            status = await self._session.read_raw_state(accept_reports=False)
        except BaseException:
            self._session.quarantine()
            self._connected = False
            raise
        lease.require_permitted()
        return status

    async def close(self) -> None:
        try:
            await self._session.disconnect()
        finally:
            self._connected = False


def verify_prepared_readback(prepared: PreparedDeviceWrite, status: bytes) -> bool:
    if prepared.verification_kind == "schedule_image":
        return (
            extract_current_schedule_image(prepared.target.target.product_key, status)
            == prepared.expected_readback
        )
    schema = get_product_schema(prepared.target.target.product_key)
    decoded = schema.decode_status(status)
    if prepared.verification_kind == "device_clock":
        return clock_readback_matches(
            prepared.expected_readback, decoded, observed_at=datetime.now(UTC)
        )
    expected = json.loads(prepared.expected_readback.decode("ascii"))
    return all(decoded.get(name) == value for name, value in expected.items())


def verify_prepared_baseline(prepared: PreparedDeviceWrite, status: bytes) -> bool:
    if prepared.verification_kind == "device_clock":
        raise LocalDeviceWriteError("clock settings have no historical baseline to restore")
    if prepared.verification_kind == "schedule_image":
        return (
            extract_current_schedule_image(prepared.target.target.product_key, status)
            == prepared.baseline_readback
        )
    schema = get_product_schema(prepared.target.target.product_key)
    decoded = schema.decode_status(status)
    expected = json.loads(prepared.baseline_readback.decode("ascii"))
    return all(decoded.get(name) == value for name, value in expected.items())


def _build_exact_pro_schedule_payload(image: bytes) -> bytes:
    """Encode all 48 current bytes without reinterpreting untouched app-produced slots."""

    exact = bytes(image)
    if len(exact) != LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE:
        raise ValueError("Pro schedule image must be exactly 432 bytes")
    flags = bytearray(LOCAL_WAVEMAKER_PRO_CONTROL_FLAGS_SIZE)
    values = bytearray(LOCAL_WAVEMAKER_PRO_CONTROL_VALUES_SIZE)
    for index in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT):
        datapoint_id = LOCAL_WAVEMAKER_PRO_FIRST_SCHEDULE_DP_ID + index
        flags[datapoint_id // 8] |= 1 << (datapoint_id % 8)
        source_start = index * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
        target_start = LOCAL_WAVEMAKER_PRO_SCHEDULE_STATUS_OFFSET + source_start
        values[target_start : target_start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE] = exact[
            source_start : source_start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE
        ]
    payload = bytes((CONTROL_ACTION,)) + bytes(reversed(flags)) + bytes(values)
    if len(payload) != LOCAL_WAVEMAKER_PRO_CONTROL_PAYLOAD_SIZE:
        raise AssertionError("Pro schedule control payload size invariant failed")
    return payload


def build_exact_schedule_payload(product_key: str, image: bytes) -> bytes:
    """Build a full 48-slot payload from content-audited vendor binary datapoints.

    This function only constructs bytes. It does not establish device acceptance and it does not
    send. Unknown slot contents are deliberately copied without reinterpretation.
    """

    try:
        geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[product_key]
    except KeyError as error:
        raise ValueError("product does not have an audited 48-slot schedule geometry") from error
    exact = bytes(image)
    if len(exact) != geometry.image_size:
        raise ValueError(f"schedule image must be exactly {geometry.image_size} bytes")
    changes = {
        f"AutoTime{index:02d}": exact[index * geometry.slot_size : (index + 1) * geometry.slot_size]
        for index in range(48)
    }
    return build_control_payload(get_product_schema(product_key), changes)


def build_app_schedule_slot_payload(
    product_key: str,
    slot_index: int,
    slot_wire: bytes,
) -> bytes:
    """Build the selected-slot save shape used by the Pro and DC Pro app screens."""

    if product_key not in _SINGLE_SLOT_SAVE_PRODUCTS:
        raise ValueError("this product app saves schedules as a complete 48-slot image")
    geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[product_key]
    index = _require_schedule_slot_index(slot_index)
    exact = bytes(slot_wire)
    if len(exact) != geometry.slot_size:
        raise ValueError(f"schedule slot must be exactly {geometry.slot_size} bytes")
    return build_control_payload(
        get_product_schema(product_key),
        {f"AutoTime{index:02d}": exact},
    )


def build_app_full_schedule_payload(product_key: str, image: bytes) -> bytes:
    """Build the full-image save shape used by the Bar and Aquarium app screens."""

    if product_key not in _FULL_IMAGE_SAVE_PRODUCTS:
        raise ValueError("this product app saves one selected slot at a time")
    return build_exact_schedule_payload(product_key, image)


def build_app_schedule_clear_payload(
    product_key: str,
    current_image: bytes,
    slot_index: int,
) -> tuple[bytes, bytes]:
    """Return ``(desired_image, payload)`` for an app-equivalent single-slot clear.

    All audited production templates use ``0xee`` for unused slots. Pro and DC Pro send one
    selected datapoint; Bar and Aquarium replace the complete image while preserving other slots.
    """

    geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT.get(product_key)
    if geometry is None:
        raise ValueError("product does not have an audited 48-slot schedule geometry")
    source = bytes(current_image)
    if len(source) != geometry.image_size:
        raise ValueError(f"schedule image must be exactly {geometry.image_size} bytes")
    index = _require_schedule_slot_index(slot_index)
    start = index * geometry.slot_size
    sentinel = bytes([0xEE]) * geometry.slot_size
    desired = source[:start] + sentinel + source[start + geometry.slot_size :]
    if product_key in _SINGLE_SLOT_SAVE_PRODUCTS:
        payload = build_app_schedule_slot_payload(product_key, index, sentinel)
    else:
        payload = build_app_full_schedule_payload(product_key, desired)
    return desired, payload


def build_device_clock_payload(product_key: str, local_time: datetime) -> bytes:
    """Build the exact YMD/HMS command shape used by the audited app."""

    if not isinstance(local_time, datetime):
        raise TypeError("device local time must be a datetime")
    if product_key not in _SCHEDULE_GEOMETRY_BY_PRODUCT:
        raise ValueError("product does not have an audited device clock")
    year = local_time.year
    if not 0 <= year <= 0xFFFF:
        raise ValueError("device local year is outside the two-byte app representation")
    ymd = bytes((year // 100, year % 100, local_time.month, local_time.day))
    hms = bytes((0, local_time.hour, local_time.minute, local_time.second))
    return build_control_payload(
        get_product_schema(product_key),
        {"YMDData": ymd, "HMSData": hms},
    )


def extract_current_schedule_image(product_key: str, status: bytes) -> bytes:
    """Extract one product's full schedule region without decoding or normalizing it."""

    try:
        geometry = _SCHEDULE_GEOMETRY_BY_PRODUCT[product_key]
    except KeyError as error:
        raise ValueError("product does not have an audited 48-slot schedule geometry") from error
    exact = bytes(status)
    if len(exact) != geometry.raw_status_size:
        raise ValueError(f"schedule status must be exactly {geometry.raw_status_size} bytes")
    end = geometry.status_offset + geometry.image_size
    return exact[geometry.status_offset : end]


def extract_current_pro_schedule_image(status: bytes) -> bytes:
    return extract_current_schedule_image(LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, status)


def _require_schedule_slot_index(slot_index: int) -> int:
    if isinstance(slot_index, bool) or not isinstance(slot_index, int):
        raise TypeError("schedule slot index must be an integer")
    if not 0 <= slot_index < 48:
        raise ValueError("schedule slot index must be in 0..47")
    return slot_index


def _canonical_values(values: Mapping[str, Any]) -> bytes:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _same_stable_target(left: LocalDeviceBinding, right: LocalDeviceBinding) -> bool:
    return (
        left.logical_member,
        left.product_key,
        left.physical_identity_key,
        left.logical_role,
    ) == (
        right.logical_member,
        right.product_key,
        right.physical_identity_key,
        right.logical_role,
    )


__all__ = [
    "ConnectedDeviceWriter",
    "LocalDeviceWriteError",
    "PreparedDeviceWrite",
    "RawWriterSession",
    "SessionFactory",
    "build_app_full_schedule_payload",
    "build_app_schedule_clear_payload",
    "build_app_schedule_slot_payload",
    "build_device_clock_payload",
    "build_exact_schedule_payload",
    "extract_current_schedule_image",
    "extract_current_pro_schedule_image",
    "verify_prepared_baseline",
    "verify_prepared_readback",
]
