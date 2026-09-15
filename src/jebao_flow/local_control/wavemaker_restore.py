"""연동 전 원본 보존과 해제 반환값 비교. 이 모듈은 장비 명령을 보내지 않는다.

원본은 기존 journal의 첫 baseline에서 가져온다. 성공한 기록을 recover로 재실행하거나,
이전 성공을 현재 상태로 간주하지 않는다. 실제 해제 실행기는 이 계약을 소비해야 한다.
보존 데이터가 있다는 사실은 전체 복원 권한이 아니다. 실제 실행은 별도의 범위·자격을 검증해야 한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from jebao_flow.local_control.device_writer import extract_current_schedule_image
from jebao_flow.local_control.journal import LocalControlJournalRecord, LocalControlJournalStore
from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import (
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRODUCT_KEY,
)

_CONTROL_FIELDS = {
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY: (
        "SwitchON",
        "TimerON",
        "Mode",
        "Flow",
        "Frequency",
        "FeedTime",
        "Cust_Wav_Freq",
        "Linkage",
    ),
    LOCAL_WAVEMAKER_PRODUCT_KEY: (
        "SwitchON",
        "TimerON",
        "Mode",
        "Flow",
        "Frequency",
        "FeedTime",
        "FeedSwitch",
        "PulseTide",
        "Linkage",
    ),
}


@dataclass(frozen=True, slots=True)
class WavemakerRestoreSnapshot:
    """내용 불변의 원본. 시각·현재 Auto 출력은 원복 대상이 아니다."""

    owner_request_id: str
    target: LocalDeviceBinding
    baseline_status: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.target.product_key not in _CONTROL_FIELDS:
            raise ValueError("수류모터 원복 snapshot은 Bar/Pro 전용입니다")
        if type(self.baseline_status) is not bytes:
            raise TypeError("원본 상태는 불변 bytes여야 합니다")
        values = get_product_schema(self.target.product_key).decode_status(self.baseline_status)
        if values["Linkage"] != "independent":
            raise ValueError("결속 해제 원본은 연동 전 independent 상태여야 합니다")
        extract_current_schedule_image(self.target.product_key, self.baseline_status)

    @property
    def image(self) -> bytes:
        return extract_current_schedule_image(self.target.product_key, self.baseline_status)

    @property
    def controls(self) -> dict[str, bool | int | str]:
        values = get_product_schema(self.target.product_key).decode_status(self.baseline_status)
        return {name: values[name] for name in _CONTROL_FIELDS[self.target.product_key]}

    def public_dict(self) -> dict[str, object]:
        return {
            "owner_request_id": self.owner_request_id,
            "logical_member": self.target.logical_member,
            "baseline_status_sha256": hashlib.sha256(self.baseline_status).hexdigest(),
            "schedule_sha256": hashlib.sha256(self.image).hexdigest(),
            "schedule_bytes": len(self.image),
            "controls": self.controls,
            "current_state_verified": False,
        }


def load_operation_baselines(
    record: LocalControlJournalRecord,
    store: LocalControlJournalStore,
) -> tuple[WavemakerRestoreSnapshot, ...]:
    """각 장비의 최초 상태만 선택한다. 후속 단계의 safe 상태로 원본을 덮지 않는다."""

    if store.load(record.request_id) != record:
        raise ValueError("보존된 원본 operation과 요청한 record가 다릅니다")
    first = {}
    for action in record.actions:
        key = action.target.physical_identity_key
        if key in first:
            if first[key].target != action.target:
                raise ValueError("operation 안에서 장비 결속이 바뀌었습니다")
            continue
        first[key] = WavemakerRestoreSnapshot(
            owner_request_id=record.request_id,
            target=action.target,
            baseline_status=store.load_blob(action.baseline_status_sha256),
        )
    return tuple(first.values())


def compare_restore_readback(
    snapshot: WavemakerRestoreSnapshot,
    *,
    target: LocalDeviceBinding,
    status: bytes,
) -> dict[str, object]:
    """현재 authenticated 반환값을 비교한다. 보고서 자체가 전송·현재 identity 확인은 아니다."""

    if target != snapshot.target:
        raise ValueError("원복 반환값의 장비 결속이 원본과 다릅니다")
    image = extract_current_schedule_image(target.product_key, status)
    actual = get_product_schema(target.product_key).decode_status(status)
    fields = {
        name: {"expected": expected, "actual": actual[name], "match": actual[name] == expected}
        for name, expected in snapshot.controls.items()
    }
    image_matches = image == snapshot.image
    return {
        "logical_member": target.logical_member,
        "owner_request_id": snapshot.owner_request_id,
        "schedule_match": image_matches,
        "expected_schedule_sha256": hashlib.sha256(snapshot.image).hexdigest(),
        "actual_schedule_sha256": hashlib.sha256(image).hexdigest(),
        "fields": fields,
        "complete": image_matches and all(item["match"] for item in fields.values()),
        "physical_measurement": False,
    }


def detach_sequence(
    snapshot: WavemakerRestoreSnapshot,
    *,
    preparation_flow: int = 30,
    preparation_frequency: int = 20,
) -> tuple[dict[str, object], ...]:
    """장비 한 대의 원복 순서. pair 전체 실행 순서나 전송 권한을 만들지 않는다.

    atomic safe 해제는 SwitchON=True도 함께 쓴다. 원래 OFF인 장비에도 중간 ON 상태가
    생기므로 실제 실행기의 운용 계획에서 공개해야 하며, 무출력 해제로 취급하면 안 된다.
    """

    if snapshot.target.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY:
        raise ValueError("Bar의 timer 결합 명령은 미확정이므로 native 해제 순서를 만들 수 없습니다")
    if type(preparation_flow) is not int or not 30 <= preparation_flow <= 100:
        raise ValueError("준비 Flow는 30..100 정수여야 합니다")
    if type(preparation_frequency) is not int or not 0 <= preparation_frequency <= 100:
        raise ValueError("준비 주파수는 0..100 정수여야 합니다")
    stages = (
        {
            "stage": "detach_atomic",
            "expected": {
                "SwitchON": True,
                "TimerON": False,
                "Linkage": "independent",
                "Mode": "constant",
                "Flow": preparation_flow,
                "Frequency": preparation_frequency,
            },
        },
        {
            "stage": "verify_disarmed",
            "expected": {
                "SwitchON": True,
                "TimerON": False,
                "Linkage": "independent",
            },
        },
        {"stage": "restore_image", "image_sha256": hashlib.sha256(snapshot.image).hexdigest()},
        {"stage": "restore_controls", "expected": snapshot.controls},
        {"stage": "verify_complete", "image_and_all_controls": True},
    )
    return tuple({"logical_member": snapshot.target.logical_member, **stage} for stage in stages)
