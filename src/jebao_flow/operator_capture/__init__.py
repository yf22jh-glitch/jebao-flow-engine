"""Write-free packet observation and durable evidence primitives.

This package intentionally has no control encoder, write-capable session, or device actuator in
its import graph.  It can describe and preserve traffic, but it cannot originate device writes.
"""

from jebao_flow.operator_capture.capability import (
    AppExpressibility,
    CapabilityClaim,
    CapabilityEvidenceSource,
    CapabilitySupport,
    CaptureEvidenceRef,
    DeviceSupport,
    HomeAssistantExposure,
)
from jebao_flow.operator_capture.catalog import summarize_frame
from jebao_flow.operator_capture.model import (
    CaptureDirection,
    CaptureOrigin,
    ProtocolSummary,
    UnknownRegion,
)
from jebao_flow.operator_capture.store import (
    CaptureStoreError,
    DurableCaptureSession,
    VerifiedCapture,
    list_capture_sessions,
    verify_capture_session,
)
from jebao_flow.operator_capture.stream import FrameStreamDecoder, StreamDesynchronization

__all__ = [
    "AppExpressibility",
    "CaptureDirection",
    "CapabilityClaim",
    "CapabilityEvidenceSource",
    "CapabilitySupport",
    "CaptureEvidenceRef",
    "CaptureOrigin",
    "CaptureStoreError",
    "DurableCaptureSession",
    "DeviceSupport",
    "FrameStreamDecoder",
    "HomeAssistantExposure",
    "ProtocolSummary",
    "StreamDesynchronization",
    "UnknownRegion",
    "VerifiedCapture",
    "list_capture_sessions",
    "summarize_frame",
    "verify_capture_session",
]
