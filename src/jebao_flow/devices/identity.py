"""Backward-compatible exports for the canonical physical identity model.

Identity bindings must have one runtime class. Keeping the implementation in
``jebao_flow.physical_identity`` also lets read-only and exact-restore composition import it
without executing the :mod:`jebao_flow.devices` package's optional write paths.
"""

from jebao_flow.physical_identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    normalize_mac_address,
    physical_identity_key,
)

__all__ = [
    "PhysicalDeviceBinding",
    "configuration_fingerprint",
    "normalize_mac_address",
    "physical_identity_key",
]
