"""DC manual-mode/frequency/feeding-duration qualification using existing scalar journals.

DC's top-level Mode enum is known; its ambiguous schedule Mode codes are unrelated and remain
unqualified. Linkage is absent, represented as None by the product state reader. No fake native
role, new encoder, hardware acceptance or receipt is introduced by adding this producer class.
"""

from jebao_flow.local_control.bar_qualification import BarScalarQualificationProducer


class DcScalarQualificationProducer(BarScalarQualificationProducer):
    """Reuse same/step/own-baseline restoration; feeding requires an encodable baseline."""

    _family = "dc_pump_pro"
    _functions = frozenset({"manual_mode", "manual_frequency", "feeding_duration"})
