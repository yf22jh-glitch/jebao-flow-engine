"""Aquarium scalar qualification over the existing own-baseline three-frame journal.

The fresh FeedSwitch copied by a duration command is owned along with FeedTime.
The transport bit is recorded, not a condition on unrelated later changes. This is
an offline software contract, not evidence of firmware acceptance or physical effects.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from jebao_flow.local_control.aquarium_controls import prepare_aquarium_control_command
from jebao_flow.local_control.aquarium_power_qualification import (
    AquariumPowerQualificationProducer,
    _require_safe_motor_speed,
)
from jebao_flow.local_control.bar_qualification import BarScalarQualificationProducer
from jebao_flow.local_control.catalog import build_local_control_catalog
from jebao_flow.local_control.device_writer import get_product_schema
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.qualification_producer import (
    QualificationProductionError,
    _canonical_json,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.schedule import AQUARIUM_PUMP_PRODUCT_KEY


class AquariumScalarQualificationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    target: LocalDeviceBinding = Field(repr=False)
    function: Literal["motor_speed", "feeding_duration"]
    field: Literal["Motor_Speed", "FeedTime"]
    baseline_value: StrictInt
    trial_value: StrictInt
    baseline_motor_speed: StrictInt
    baseline_transport_mode: StrictBool
    baseline_enabled: Literal[True] = True
    baseline_timer_enabled: Literal[False] = False
    step_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
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
        if self.target.product_key != AQUARIUM_PUMP_PRODUCT_KEY:
            raise ValueError("Aquarium scalar target has a different product")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("qualification timestamp must be timezone-aware")
        if self.step_request_id == self.restore_request_id:
            raise ValueError("qualification phase requests must be distinct")
        if (
            self.field
            != {"motor_speed": "Motor_Speed", "feeding_duration": "FeedTime"}[self.function]
        ):
            raise ValueError("qualification field differs from function")
        minimum, maximum = declared_app_integer_range(self.target.product_key, self.function)
        if not (
            minimum <= self.baseline_value <= maximum
            and minimum <= self.trial_value <= maximum
            and self.baseline_value != self.trial_value
        ):
            raise ValueError("qualification values require a distinct in-range step")
        _require_safe_motor_speed(
            self.baseline_motor_speed,
            minimum=self.min_power,
            maximum=self.max_power,
            product_key=self.target.product_key,
        )
        if self.function == "motor_speed":
            if self.baseline_value != self.baseline_motor_speed:
                raise ValueError("speed baseline differs from current state")
            if abs(self.trial_value - self.baseline_value) != 1:
                raise ValueError("speed qualification uses exactly one app step")
            _require_safe_motor_speed(
                self.trial_value,
                minimum=self.min_power,
                maximum=self.max_power,
                product_key=self.target.product_key,
            )
        for version in (self.app_version, self.firmware_version):
            if not version.isascii() or any(ord(character) < 0x20 for character in version):
                raise ValueError("qualification versions must use printable ASCII")
        return self


class AquariumScalarQualificationProducer(BarScalarQualificationProducer):
    _family = "aquarium_pump"
    _functions = frozenset({"motor_speed", "feeding_duration"})
    _intent_class = AquariumScalarQualificationIntent
    _read_scalar_state = staticmethod(AquariumPowerQualificationProducer._read_baseline_power_state)
    _require_state_envelope = staticmethod(
        AquariumPowerQualificationProducer._require_state_envelope
    )

    def _require_bar_target(self, target):
        if target.product_key != AQUARIUM_PUMP_PRODUCT_KEY:
            raise QualificationProductionError("Aquarium qualification requires its actual product")
        descriptor = build_local_control_catalog().find(target.product_key, self._spec.function)
        if not descriptor.encoding_available or descriptor.fields[0] != self._spec.field:
            raise QualificationProductionError(
                "Aquarium qualification target encoder is unavailable"
            )

    def _intent_baseline_fields(self, state):
        return {
            "baseline_motor_speed": state.motor_speed,
            "baseline_transport_mode": state.transport_mode,
        }

    def _expected_phase_bytes(self, intent, value, baseline_raw):
        current = get_product_schema(intent.target.product_key).decode_status(baseline_raw)
        command = prepare_aquarium_control_command(
            intent.function,
            value,
            current,
            minimum_speed=intent.min_power,
            maximum_speed=intent.max_power,
        )
        return _canonical_json(dict(command.persistent_readback))

    def _trial_value(self, product_key, baseline):
        if self._spec.function == "motor_speed":
            minimum, _maximum = declared_app_integer_range(product_key, "motor_speed")
            return baseline - 1 if baseline > minimum else baseline + 1
        return super()._trial_value(product_key, baseline)
