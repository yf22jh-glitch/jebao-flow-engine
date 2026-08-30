from __future__ import annotations

import hashlib
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jebao_flow.exact_restore import (
    ExactRestoreAuthority,
    ExactRestoreAuthorityActivation,
    ExactRestoreBaseline,
    ExactRestoreCycle,
    ExactRestoreDeviceBaseline,
    ExactRestoreEvidenceReference,
    ExactRestoreInflightAction,
    ExactRestorePhase,
    ExactRestoreRecord,
    ExactRestoreRole,
    ExactRestoreVerificationPolicy,
    ExactScheduleImage,
    OuterControlSnapshot,
    RestorePowerPolicy,
    SafeManualTarget,
    prepare_exact_restore_record,
)
from jebao_flow.exact_restore_authority import (
    AttendedAuthorityError,
    AttendedAuthorityErrorCode,
    AttendedGrantIssuer,
)
from jebao_flow.physical_identity import PhysicalDeviceBinding
from jebao_flow.protocol.models import LinkageRole
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
)

NOW = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)
MONOTONIC_NS = 1_000_000_000
BOOT_A_SHA256 = hashlib.sha256(b"boot-a").hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _prepared(
    *,
    cycle: ExactRestoreCycle = ExactRestoreCycle.SENTINEL_QUALIFICATION,
) -> ExactRestoreRecord:
    image = bytearray(LOCAL_WAVEMAKER_PRO_UNUSED_ZERO * LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    image[:9] = bytes((0, 0, 24, 0, 2, 60, 20, 0, 0))
    devices: list[ExactRestoreDeviceBaseline] = []
    targets: list[SafeManualTarget] = []
    for role, power in ((ExactRestoreRole.MASTER, 40), (ExactRestoreRole.SLAVE, 50)):
        label = role.value
        devices.append(
            ExactRestoreDeviceBaseline(
                role=role,
                logical_id=f"logical-{label}",
                physical_binding=PhysicalDeviceBinding(
                    vendor_device_id_digest=_digest(f"{label}-vendor"),
                    mac_address_digest=_digest(f"{label}-mac"),
                    product_key="local-wavemaker-pro-test",
                    config_fingerprint=_digest(f"{label}-config"),
                ),
                outer=OuterControlSnapshot(
                    enabled=True,
                    timer_enabled=True,
                    linkage=LinkageRole.INDEPENDENT,
                    mode="constant",
                    power=power,
                    frequency=20,
                ),
                schedule=ExactScheduleImage.from_bytes(image),
                power_policy=RestorePowerPolicy(
                    min_power=30,
                    max_power=80,
                    power_step=10,
                    attended_max_power=70,
                ),
                raw_frame_sha256=_digest(f"{label}-raw"),
            )
        )
        targets.append(
            SafeManualTarget(
                role=role,
                power=40,
                mode="constant",
                frequency=20,
            )
        )
    baseline = ExactRestoreBaseline(
        devices=tuple(devices),
        evidence=ExactRestoreEvidenceReference(
            plan_artifact_id="JFP-authority-test",
            series_artifact_id="JFS-authority-test",
            pair_ordinal=0,
            pair_manifest_sha256=_digest("pair-manifest"),
        ),
        verification_policy=ExactRestoreVerificationPolicy(
            max_observation_age_seconds=30,
            max_final_pair_gap_seconds=20,
        ),
        captured_at=NOW,
    )
    return prepare_exact_restore_record(
        baseline,
        tuple(targets),
        cycle=cycle,
        operation_id=f"authority-{cycle.value}",
        now=NOW,
    )


class FakeChannel:
    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept
        self.summary = ""

    def write(self, value: str) -> None:
        self.summary += value

    def read_line(self, _max_bytes: int) -> str:
        challenge = self.summary.rsplit("Type exactly: ", maxsplit=1)[1].splitlines()[0]
        return challenge if self.accept else "DECLINE"


class FakeController:
    def __init__(self, result: ExactRestoreRecord) -> None:
        self.result = result
        self.armed: list[ExactRestoreAuthority] = []
        self.reauthorized: list[ExactRestoreAuthority] = []
        self.recovered: list[ExactRestoreAuthority] = []

    def arm(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord:
        self.armed.append(authority)
        return self.result

    def reauthorize(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord:
        self.reauthorized.append(authority)
        return self.result

    def recover(self, authority: ExactRestoreAuthority) -> ExactRestoreRecord:
        self.recovered.append(authority)
        return self.result


def _issuer(
    channel: FakeChannel,
    *,
    wall_clock=lambda: NOW,
    monotonic_clock=lambda: MONOTONIC_NS,
    boot_identity=lambda: BOOT_A_SHA256,
) -> AttendedGrantIssuer:
    @contextmanager
    def channel_factory():
        yield channel

    return AttendedGrantIssuer(
        channel_factory=channel_factory,
        wall_clock=wall_clock,
        monotonic_clock=monotonic_clock,
        boot_identity=boot_identity,
        entropy=lambda size: b"a" * size,
    )


def test_confirmation_issues_and_immediately_consumes_boot_bound_authority() -> None:
    record = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    channel = FakeChannel()
    controller = FakeController(record)

    assert _issuer(channel).confirm_and_arm(controller, record) is record

    assert len(controller.armed) == 1
    authority = controller.armed[0]
    assert authority.operation_id == record.operation_id
    assert authority.baseline_sha256 == record.baseline_sha256
    assert authority.action_plan_sha256 == record.action_plan_sha256
    assert authority.journal_context_sha256 == record.authority_context_sha256
    assert authority.boot_identity_sha256 == BOOT_A_SHA256
    assert authority.issued_at == NOW
    assert authority.expires_at == NOW + timedelta(minutes=5)
    assert authority.issued_monotonic_ns == MONOTONIC_NS
    assert authority.deadline_monotonic_ns == MONOTONIC_NS + 5 * 60 * 1_000_000_000
    assert authority.permit_crash_resume is False
    assert "next_action: 00-slave-safe_fallback" in channel.summary
    assert f"journal_context_sha256: {record.authority_context_sha256}" in channel.summary
    assert "vendor" not in channel.summary.lower()


def test_declined_confirmation_sends_no_authority_to_controller() -> None:
    record = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    controller = FakeController(record)

    with pytest.raises(AttendedAuthorityError) as declined:
        _issuer(FakeChannel(accept=False)).confirm_and_arm(controller, record)

    assert declined.value.code is AttendedAuthorityErrorCode.DECLINED
    assert controller.armed == []


def test_stale_tty_confirmation_mints_zero_authority() -> None:
    record = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    wall = [NOW]
    monotonic = [MONOTONIC_NS]

    class AdvancingChannel(FakeChannel):
        def read_line(self, max_bytes: int) -> str:
            value = super().read_line(max_bytes)
            wall[0] += timedelta(hours=2)
            monotonic[0] += 2 * 60 * 60 * 1_000_000_000
            return value

    controller = FakeController(record)
    issuer = _issuer(
        AdvancingChannel(),
        wall_clock=lambda: wall[0],
        monotonic_clock=lambda: monotonic[0],
    )

    with pytest.raises(AttendedAuthorityError) as stale:
        issuer.confirm_and_arm(controller, record)

    assert stale.value.code is AttendedAuthorityErrorCode.RECORD
    assert controller.armed == []


def test_inflight_reauthorization_is_the_only_path_that_permits_crash_resume() -> None:
    prepared = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    initial_controller = FakeController(prepared)
    _issuer(FakeChannel()).confirm_and_arm(initial_controller, prepared)
    authority = initial_controller.armed[0]
    activation = ExactRestoreAuthorityActivation(
        authority_sha256=authority.authority_sha256,
        boot_identity_sha256=BOOT_A_SHA256,
        accepted_wall=NOW,
        accepted_monotonic_ns=MONOTONIC_NS,
        deadline_monotonic_ns=authority.deadline_monotonic_ns,
    )
    armed_payload = prepared.model_dump(mode="json")
    armed_payload.update(
        phase=ExactRestorePhase.ARMED,
        authority=authority.model_dump(mode="json"),
        authority_activation=activation.model_dump(mode="json"),
    )
    armed = ExactRestoreRecord.model_validate(armed_payload)
    first = armed.actions[0]
    payload = armed.model_dump(mode="json")
    payload.update(
        phase=ExactRestorePhase.RESTORING,
        inflight=ExactRestoreInflightAction(
            index=first.index,
            action_id=first.action_id,
            target_sha256=first.target_sha256,
            pre_state_sha256=hashlib.sha256(b"pre-state").hexdigest(),
            authority_sha256=authority.authority_sha256,
            intent_at=NOW,
        ).model_dump(mode="json"),
    )
    inflight = ExactRestoreRecord.model_validate(payload)
    controller = FakeController(inflight)
    channel = FakeChannel()

    _issuer(channel).confirm_and_reauthorize(controller, inflight)

    assert inflight.inflight is not None
    assert controller.reauthorized[0].permit_crash_resume is True
    assert (
        controller.reauthorized[0].crash_resume_inflight_sha256 == inflight.inflight.inflight_sha256
    )
    assert "uncertain inflight action" in channel.summary

    payload = armed.model_dump(mode="json")
    payload["phase"] = ExactRestorePhase.ARMED.value
    armed_without_inflight = ExactRestoreRecord.model_validate(payload)
    controller = FakeController(armed_without_inflight)
    _issuer(FakeChannel()).confirm_and_reauthorize(controller, armed_without_inflight)
    assert controller.reauthorized[0].permit_crash_resume is False
    assert controller.reauthorized[0].crash_resume_inflight_sha256 is None


def test_recovery_confirmation_is_separate_and_binds_latched_context() -> None:
    prepared = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    initial_controller = FakeController(prepared)
    _issuer(FakeChannel()).confirm_and_arm(initial_controller, prepared)
    authority = initial_controller.armed[0]
    activation = ExactRestoreAuthorityActivation(
        authority_sha256=authority.authority_sha256,
        boot_identity_sha256=BOOT_A_SHA256,
        accepted_wall=NOW,
        accepted_monotonic_ns=MONOTONIC_NS,
        deadline_monotonic_ns=authority.deadline_monotonic_ns,
    )
    payload = prepared.model_dump(mode="json")
    payload.update(
        phase=ExactRestorePhase.RECOVERY_REQUIRED,
        authority=authority.model_dump(mode="json"),
        authority_activation=activation.model_dump(mode="json"),
        error_code="device_io",
    )
    latched = ExactRestoreRecord.model_validate(payload)
    controller = FakeController(latched)

    assert _issuer(FakeChannel()).confirm_and_recover(controller, latched) is latched

    assert controller.armed == []
    assert controller.reauthorized == []
    assert len(controller.recovered) == 1
    recovery = controller.recovered[0]
    assert recovery.journal_context_sha256 == latched.authority_context_sha256
    assert recovery.permit_crash_resume is False


@pytest.mark.parametrize("change", ["suspend", "boot", "wall_regression", "mono_regression"])
def test_prompt_clock_or_boot_change_mints_zero_authority(change: str) -> None:
    record = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    wall = [NOW]
    monotonic = [MONOTONIC_NS]
    boot = [BOOT_A_SHA256]

    class ChangingChannel(FakeChannel):
        def read_line(self, max_bytes: int) -> str:
            value = super().read_line(max_bytes)
            if change == "suspend":
                monotonic[0] += 6 * 60 * 1_000_000_000
            elif change == "boot":
                boot[0] = hashlib.sha256(b"boot-b").hexdigest()
            elif change == "wall_regression":
                wall[0] -= timedelta(seconds=1)
            elif change == "mono_regression":
                monotonic[0] -= 1
            return value

    controller = FakeController(record)
    issuer = _issuer(
        ChangingChannel(),
        wall_clock=lambda: wall[0],
        monotonic_clock=lambda: monotonic[0],
        boot_identity=lambda: boot[0],
    )

    with pytest.raises(AttendedAuthorityError) as rejected:
        issuer.confirm_and_arm(controller, record)

    assert rejected.value.code is AttendedAuthorityErrorCode.RECORD
    assert controller.armed == []


def test_prepared_record_is_rejected_by_reauthorization_without_prompt() -> None:
    record = _prepared(cycle=ExactRestoreCycle.SENTINEL_QUALIFICATION)
    channel = FakeChannel()
    controller = FakeController(record)

    with pytest.raises(AttendedAuthorityError) as invalid:
        _issuer(channel).confirm_and_reauthorize(controller, record)

    assert invalid.value.code is AttendedAuthorityErrorCode.RECORD
    assert channel.summary == ""
    assert controller.reauthorized == []


def test_importing_authority_boundary_does_not_load_frozen_async_modules() -> None:
    repository = Path(__file__).parents[2]
    source_root = str(repository / "src")
    frozen = [
        "jebao_flow.devices.linkage",
        "jebao_flow.devices.schedule_flow_experiment",
        "jebao_flow.devices.schedule_linkage",
        "jebao_flow.devices.schedule_transaction",
    ]
    script = (
        "import sys; "
        f"sys.path.insert(0, {source_root!r}); "
        "import jebao_flow.exact_restore_authority; "
        f"frozen = {frozen!r}; "
        "loaded = sorted(name for name in frozen if name in sys.modules); "
        "assert not loaded, loaded"
    )

    subprocess.run([sys.executable, "-c", script], check=True)
