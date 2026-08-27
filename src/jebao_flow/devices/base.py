"""Physical-device contract exposed to upper layers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.protocol.models import DeviceCapabilities, DeviceState, DeviceTarget, LinkageRole


class DeviceError(RuntimeError):
    pass


class DeviceConnectionError(DeviceError):
    pass


class UnsupportedCapabilityError(DeviceError):
    pass


class HardwareWritesDisabledError(DeviceError):
    pass


class StateVerificationError(DeviceError):
    pass


class ControlAcknowledgementError(DeviceError):
    """A control request may have been sent, but its matching ACK was not confirmed."""


class ControlReadbackError(StateVerificationError):
    """A control ACK was received, but fresh decoded state could not be read."""


class ControlStateMismatchError(StateVerificationError):
    """Fresh decoded state did not match one or more requested control fields."""


class PowerStateVerificationError(ControlStateMismatchError):
    """A decoded control read-back differed only in the requested power field."""


class ControlAckResolutionStage(StrEnum):
    """Redacted stage at which read-only ACK-loss resolution stopped."""

    QUARANTINE = "quarantine"
    CONNECT = "connect"
    AUTHENTICATE = "authenticate"
    QUERY = "query"
    DECODE = "decode"


class ControlAckFailureKind(StrEnum):
    """Allow-listed cause of a missing or invalid control acknowledgement."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    UNEXPECTED_RESPONSE = "unexpected_response"
    PROTOCOL = "protocol"
    OS_ERROR = "os_error"


class ControlAckResolutionState(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ControlAckResolutionUpdate:
    stage: ControlAckResolutionStage
    attempt: int
    state: ControlAckResolutionState


class ControlAckReadbackError(ControlAcknowledgementError, ControlReadbackError):
    """Neither a control ACK nor a fresh decoded state could confirm the write."""

    def __init__(
        self,
        message: str,
        *,
        stage: ControlAckResolutionStage | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.attempts = attempts


class ControlAckStateMismatchError(ControlAcknowledgementError, ControlStateMismatchError):
    """The control ACK was absent and fresh decoded state differed from the request."""


class ControlAckPowerMismatchError(ControlAcknowledgementError, PowerStateVerificationError):
    """The control ACK was absent and fresh decoded power differed from the request."""


class SafetyInterlockError(DeviceError):
    pass


WriteGuard = Callable[[], bool]
AckUnconfirmedHook = Callable[[ControlAckFailureKind], None]
AckResolutionHook = Callable[[ControlAckResolutionUpdate], None]


class ControlVerificationOutcome(StrEnum):
    """How a control write was proven without implying that its ACK means applied."""

    STATE_VERIFIED = "state_verified"
    STATE_VERIFIED_WITHOUT_ACK = "state_verified_without_ack"


class JebaoDevice(ABC):
    @property
    @abstractmethod
    def device_id(self) -> str: ...

    @property
    @abstractmethod
    def physical_binding(self) -> PhysicalDeviceBinding | None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def capabilities(self) -> DeviceCapabilities: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the device session and propagate cancellation promptly.

        Implementations must release device and transport locks when cancelled. Safety rollback
        may interrupt a stuck close before persisting and enforcing an emergency OFF state.
        """

        ...

    @abstractmethod
    async def get_state(self) -> DeviceState:
        """Return one fresh state and propagate cancellation promptly.

        Implementations must not suppress ``CancelledError`` and must release any device or
        transport lock when cancelled. Safety rollback races this read against an interlock and
        waits for cancellation cleanup before issuing a compensating command.
        """

        ...

    async def get_explicit_state(self) -> DeviceState:
        """Return an explicitly queried state when the transport supports correlation.

        Drivers that cannot distinguish a query reply from an unsolicited state report retain
        the ordinary fresh-state contract.  The LAN driver narrows this for guarded convergence
        reads without changing normal monitoring compatibility.
        """

        return await self.get_state()

    @abstractmethod
    async def set_enabled(self, enabled: bool) -> None: ...

    @abstractmethod
    async def set_power(self, power: int) -> None: ...

    async def write_power(
        self,
        power: int,
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
    ) -> ControlVerificationOutcome:
        """Write only the power datapoint under a last-moment safety guard.

        Native linkage diagnostics use this narrower contract so changing a slave's Flow does
        not re-assert its mode, linkage role, timer authority or power switch in the same frame.
        """

        del power, guard, on_ack_unconfirmed, on_ack_resolution
        raise UnsupportedCapabilityError("guarded power-only writes are unsupported")

    @abstractmethod
    async def set_mode(self, mode: str) -> None: ...

    @abstractmethod
    async def set_frequency(self, value: int) -> None: ...

    @abstractmethod
    async def set_linkage(self, role: LinkageRole) -> None: ...

    async def write_linkage(
        self,
        role: LinkageRole,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        """Write only the linkage datapoint under a last-moment safety guard."""

        del role, guard
        raise UnsupportedCapabilityError("guarded linkage-only writes are unsupported")

    async def read_schedule_image(self) -> bytes:
        """Return a product-specific byte-exact schedule image.

        The image intentionally excludes the device clock. Implementations must not expose it
        through normal observed attributes or guess a layout for unknown products.
        """

        raise UnsupportedCapabilityError("byte-exact schedule reads are unsupported")

    async def read_schedule_image_explicit(self) -> bytes:
        """Return a schedule image from an explicit state reply.

        Safety-critical callers use this narrower contract to reject unsolicited state reports.
        Drivers that cannot distinguish an explicit reply must fail closed instead of falling back
        to :meth:`read_schedule_image`.
        """

        raise UnsupportedCapabilityError("explicit byte-exact schedule reads are unsupported")

    async def write_schedule_slots(
        self,
        slots: Mapping[int, bytes],
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
    ) -> ControlVerificationOutcome:
        """Write selected raw schedule slots once and verify their exact bytes."""

        del slots, guard, on_ack_unconfirmed, on_ack_resolution
        raise UnsupportedCapabilityError("guarded schedule writes are unsupported")

    async def restore_schedule_image(
        self,
        image: bytes,
        *,
        guard: WriteGuard | None = None,
        on_ack_unconfirmed: AckUnconfirmedHook | None = None,
        on_ack_resolution: AckResolutionHook | None = None,
    ) -> ControlVerificationOutcome:
        """Restore one previously captured schedule image exactly.

        This recovery-only operation deliberately does not reinterpret the snapshot through current
        forward-write power limits. Callers must bind ``image`` to a durable, verified snapshot of
        the same physical controller before invoking it.
        """

        del image, guard, on_ack_unconfirmed, on_ack_resolution
        raise UnsupportedCapabilityError("byte-exact schedule restore is unsupported")

    @abstractmethod
    async def set_timer_enabled(self, enabled: bool) -> None: ...

    @abstractmethod
    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        """Apply one target and propagate cancellation promptly.

        Implementations must not suppress ``CancelledError`` and must release any device or
        transport lock when cancelled. Exact restore races guarded writes against the safety
        interlock; a cancelled request has an uncertain outcome and its transport must not be
        reused unless the implementation can prove the request boundary is still intact.
        """

        ...
