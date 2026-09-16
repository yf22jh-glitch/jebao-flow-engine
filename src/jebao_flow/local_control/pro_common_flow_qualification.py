"""Pro common slave-flow qualification: the app's Flow scalar command and its own readback.

The audited Pro linkage screen exposes one common flow slider for the slave role and no
per-slot slave editor. That slider writes the ordinary ``Flow`` datapoint, so this producer
reuses the existing three-frame ``same -> one step down -> own baseline`` scalar contract and
introduces no new encoder, frame shape or admission rule.

**Scope of what a receipt from this producer means.** It attests that this exact app-shaped
``Flow`` command was acknowledged and that the device's own ``Flow`` field read back as written,
inside the existing independent-state envelope. It does **not** establish that the device holds a
slave role, that a slave keeps an independent output while a master drives it, or anything about
firmware timing or content ownership. Those remain the separate open question and are not proved
by running this.

The trial is one step down from the captured baseline and never upward, so the operation cannot
raise the output it found. Every rollback comes from the operation's single initial raw status.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator

from jebao_flow.local_control.bar_qualification import (
    BarScalarQualificationIntent,
    BarScalarQualificationProducer,
)
from jebao_flow.local_control.qualification_producer import (
    _SAFE_FIRST_WRITE_MAXIMUM,
    QualificationProductionError,
)
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY

_FUNCTION = "slave_common_flow"
_FIELD = "Flow"


def require_bounded_common_flow(
    value: object,
    *,
    product_key: str,
    minimum: int,
    maximum: int,
) -> None:
    """Both planned outputs stay inside the app range, the device limits and the trial ceiling."""

    app_minimum, app_maximum = declared_app_integer_range(product_key, _FUNCTION)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("common slave flow must be an integer")
    if not app_minimum <= value <= app_maximum:
        raise ValueError("common slave flow is outside the declared app range")
    if not minimum <= value <= maximum:
        raise ValueError("common slave flow is outside the configured device limits")
    if value > _SAFE_FIRST_WRITE_MAXIMUM:
        raise ValueError("common slave flow exceeds the first-write maximum")


class ProCommonFlowQualificationIntent(BarScalarQualificationIntent):
    """The shared scalar intent narrowed to this product, field and downward step."""

    function: Literal["slave_common_flow"] = _FUNCTION
    field: Literal["Flow"] = _FIELD

    @model_validator(mode="after")
    def validate_common_flow(self) -> Self:
        if self.target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
            raise ValueError("common slave flow qualification target is not the Wavemaker Pro")
        for value in (self.baseline_value, self.trial_value):
            require_bounded_common_flow(
                value,
                product_key=self.target.product_key,
                minimum=self.min_power,
                maximum=self.max_power,
            )
        if self.baseline_value != self.baseline_flow:
            raise ValueError("common slave flow baseline must be the captured Flow field")
        if self.trial_value != self.baseline_value - 1:
            raise ValueError("common slave flow trial must be exactly one step down")
        return self


class ProCommonFlowQualificationProducer(BarScalarQualificationProducer):
    """The audited three-frame scalar operation, scoped to the Pro common slave flow."""

    _family = "local_wavemaker_pro"
    _functions = frozenset({_FUNCTION})
    _intent_class = ProCommonFlowQualificationIntent

    def __init__(self, **kwargs: object) -> None:
        super().__init__(function=_FUNCTION, **kwargs)  # type: ignore[arg-type]

    def _trial_value(self, product_key: str, baseline: int | str) -> int:
        """One step down, never upward: the operation must not raise the output it found."""

        if isinstance(baseline, bool) or not isinstance(baseline, int):
            raise QualificationProductionError("common slave flow baseline is not an integer")
        app_minimum, _app_maximum = declared_app_integer_range(product_key, _FUNCTION)
        if baseline <= app_minimum:
            raise QualificationProductionError(
                "common slave flow baseline is already at the app minimum; no downward step exists"
            )
        return baseline - 1


__all__ = [
    "ProCommonFlowQualificationIntent",
    "ProCommonFlowQualificationProducer",
    "require_bounded_common_flow",
]
