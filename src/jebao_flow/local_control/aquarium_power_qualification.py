"""Crash-safe production of Aquarium Pump power qualification receipts.

The Aquarium Pump has no ``manual_flow``, no waveform ``Mode`` and no ``Linkage`` datapoint, so the
shared manual-flow state model cannot describe it. This module supplies only an Aquarium-specific
read-only baseline state and durable intent built from that product's **real** fields
(``SwitchON``, ``TimerON``, ``Motor_Speed`` and the boolean transport ``Mode``). No absent Flow,
waveform mode or Linkage value is ever invented.

Everything else is the audited safer-OFF power contract reused unchanged from
``PowerQualificationProducer``: the same three ``same_on -> power_off -> planned_on`` frames, the
same single-send journal, and the same ``restore_on_recovery=False`` on the OFF frame so a failed
operation converges to OFF and the original ON is never re-ignited. The emitted receipts use the
standard ``power`` phase-intent format, preserving the existing receipt verification contract.

Offline tests prove the software contract only. Device acceptance, the accepted Motor_Speed range
and physical safety are NOT asserted here, and this module issues nothing by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.local_control.device_writer import ConnectedDeviceWriter, get_product_schema
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.power_qualification_producer import PowerQualificationProducer
from jebao_flow.local_control.qualification_producer import (
    _SAFE_FIRST_WRITE_MAXIMUM,
    QualificationEpoch,
    QualificationProductionError,
    _canonical_json,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.schedule import AQUARIUM_PUMP_PRODUCT_KEY

_FUNCTION = "power"
_MOTOR_SPEED = "Motor_Speed"
_REQUIRED_FIELDS = ("SwitchON", "TimerON", _MOTOR_SPEED, "Mode")


@dataclass(frozen=True, slots=True)
class AquariumPowerState:
    """The Aquarium Pump's real baseline safety state; no Flow/Linkage/waveform mode exists."""

    enabled: bool
    timer_enabled: bool
    motor_speed: int
    transport_mode: bool


class AquariumPowerQualificationIntent(BaseModel):
    """Durable intent for one Aquarium safer-OFF ON/OFF/ON qualification.

    Deliberately a separate model from ``PowerQualificationIntent``: it records this product's
    actual fields instead of carrying fabricated Flow/mode/Linkage values. The existing Pro/Bar/DC
    durable intent canonical form is untouched.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["power"] = _FUNCTION
    baseline_power: Literal[True] = True
    safe_power: Literal[False] = False
    baseline_timer_enabled: Literal[False] = False
    baseline_motor_speed: int
    baseline_transport_mode: bool
    off_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    restore_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    min_power: int
    max_power: int
    app_version: str = Field(min_length=1, max_length=128)
    app_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    firmware_version: str = Field(min_length=1, max_length=256)
    created_at: datetime

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("aquarium power qualification timestamp must be timezone-aware")
        if self.off_request_id == self.restore_request_id:
            raise ValueError("aquarium power qualification receipt requests must be distinct")
        if self.target.product_key != AQUARIUM_PUMP_PRODUCT_KEY:
            raise ValueError("aquarium power qualification target is not the Aquarium Pump")
        if self.min_power > self.max_power:
            raise ValueError("aquarium power qualification has an invalid speed envelope")
        _require_safe_motor_speed(
            self.baseline_motor_speed,
            minimum=self.min_power,
            maximum=self.max_power,
            product_key=self.target.product_key,
        )
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("aquarium power qualification versions must use printable ASCII")
        return self


def _require_safe_motor_speed(
    speed: int,
    *,
    minimum: int,
    maximum: int,
    product_key: str,
) -> None:
    """Keep the existing first-write ceiling; the app maximum is not a new hardware limit."""

    app_minimum, app_maximum = declared_app_integer_range(product_key, "motor_speed")
    if isinstance(speed, bool) or not isinstance(speed, int):
        raise ValueError("aquarium motor speed must be an integer")
    if not app_minimum <= speed <= app_maximum:
        raise ValueError("aquarium motor speed is outside the declared app range")
    if not minimum <= speed <= maximum:
        raise ValueError("aquarium motor speed is outside the configured device limits")
    if speed > _SAFE_FIRST_WRITE_MAXIMUM:
        raise ValueError("aquarium motor speed exceeds the first-write maximum")


def aquarium_power_target_validator(product_key: str) -> str:
    """Epoch-source hook: accept only a current Aquarium Pump with its real power datapoints."""

    if product_key != AQUARIUM_PUMP_PRODUCT_KEY:
        raise QualificationProductionError("aquarium power qualification target is not current")
    try:
        schema = get_product_schema(product_key)
    except KeyError as error:
        raise QualificationProductionError("aquarium product schema is unavailable") from error
    if any(name not in schema.attributes_by_name for name in _REQUIRED_FIELDS):
        raise QualificationProductionError("aquarium power state datapoints are incomplete")
    return product_key


def aquarium_power_qualification_intent_bytes(intent: AquariumPowerQualificationIntent) -> bytes:
    return _canonical_json(intent.model_dump(mode="json"), newline=True)


class AquariumPowerQualificationProducer(PowerQualificationProducer):
    """The audited safer-OFF power operation with an Aquarium-only baseline state model."""

    _power_intent_class: type = AquariumPowerQualificationIntent

    def _power_intent_bytes(self, intent) -> bytes:
        return aquarium_power_qualification_intent_bytes(intent)

    @staticmethod
    def _read_baseline_power_state(
        writer: ConnectedDeviceWriter,
        target: LocalDeviceBinding,
        status: bytes,
    ) -> AquariumPowerState:
        aquarium_power_target_validator(target.product_key)
        try:
            decoded = get_product_schema(target.product_key).decode_status(status)
        except (KeyError, ValueError) as error:
            raise QualificationProductionError("current aquarium state is invalid") from error
        try:
            enabled = decoded["SwitchON"]
            timer = decoded["TimerON"]
            speed = decoded[_MOTOR_SPEED]
            transport = decoded["Mode"]
        except KeyError as error:
            raise QualificationProductionError("current aquarium state is incomplete") from error
        if (
            type(enabled) is not bool
            or type(timer) is not bool
            or type(transport) is not bool
            or isinstance(speed, bool)
            or not isinstance(speed, int)
        ):
            raise QualificationProductionError("current aquarium state types are invalid")
        return AquariumPowerState(enabled, timer, speed, transport)

    @staticmethod
    def _require_state_envelope(
        epoch: QualificationEpoch,
        state: AquariumPowerState,
    ) -> None:
        if state.enabled is not True or state.timer_enabled is not False:
            raise QualificationProductionError(
                "current state is outside the first-write envelope"
            )
        try:
            _require_safe_motor_speed(
                state.motor_speed,
                minimum=epoch.min_power,
                maximum=epoch.max_power,
                product_key=epoch.target.product_key,
            )
        except ValueError as error:
            raise QualificationProductionError(
                "current state is outside the first-write envelope"
            ) from error

    def _build_power_intent(
        self,
        *,
        operation_id: str,
        epoch: QualificationEpoch,
        baseline_state: AquariumPowerState,
        firmware_version: str,
        off_request_id: str,
        restore_request_id: str,
    ) -> AquariumPowerQualificationIntent:
        return AquariumPowerQualificationIntent(
            operation_id=operation_id,
            target=epoch.target,
            baseline_motor_speed=baseline_state.motor_speed,
            baseline_transport_mode=baseline_state.transport_mode,
            off_request_id=off_request_id,
            restore_request_id=restore_request_id,
            config_sha256=epoch.config_sha256,
            manifest_sha256=epoch.manifest_sha256,
            manifest_generation=epoch.manifest_generation,
            min_power=epoch.min_power,
            max_power=epoch.max_power,
            app_version=epoch.app_version,
            app_catalog_sha256=epoch.app_catalog_sha256,
            template_sha256=epoch.template_sha256,
            schema_sha256=epoch.schema_sha256,
            firmware_version=firmware_version,
            created_at=self._now(),
        )

    def _require_power_readback(
        self,
        epoch: QualificationEpoch,
        intent: AquariumPowerQualificationIntent,
        writer: ConnectedDeviceWriter,
        status: bytes,
        *,
        expected_enabled: bool,
    ) -> None:
        state = self._read_baseline_power_state(writer, epoch.target, status)
        if (
            state.enabled is not expected_enabled
            or state.timer_enabled is not intent.baseline_timer_enabled
            or state.motor_speed != intent.baseline_motor_speed
            or state.transport_mode is not intent.baseline_transport_mode
        ):
            raise QualificationProductionError("aquarium power qualification phase state changed")


__all__ = [
    "AquariumPowerQualificationIntent",
    "AquariumPowerQualificationProducer",
    "AquariumPowerState",
    "aquarium_power_qualification_intent_bytes",
    "aquarium_power_target_validator",
]
