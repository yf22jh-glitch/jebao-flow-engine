"""외부 설정으로 만드는 Bar/Pro 시간표. 전송·qualification·펌웨어 판정은 하지 않는다."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_serializer,
    model_validator,
)

from jebao_flow.protocol.models import ScheduleEndTime, ScheduleStartTime
from jebao_flow.protocol.schedule import (
    LOCAL_WAVEMAKER_PRO_MODES,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    LOCAL_WAVEMAKER_PRODUCT_KEY,
)

SlotIndex = Annotated[StrictInt, Field(ge=0, lt=48)]
Percent = Annotated[StrictInt, Field(ge=0, le=100)]
Member = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
_BAR_MODES = ("stopped", "classic", "sine", "random", "constant", "feed")


class WavemakerSlot(BaseModel):
    """슬롯 위치는 유지한다. 겹친 시간표의 우선순위는 여기서 발명하지 않는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_index: SlotIndex
    start: ScheduleStartTime
    end: ScheduleEndTime
    mode: str
    flow: Percent | None = None
    frequency: Percent = 0
    feed_time: Annotated[StrictInt, Field(ge=0, le=60)] = 0
    custom_frequency: Percent = 0
    pulse_tide: StrictBool = False


class WavemakerSchedule(BaseModel):
    """한 제품의 완전한 48슬롯 교체 선언. 생략한 위치는 앱의 EE 미사용 슬롯이다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: Literal["pro", "bar"]
    slots: tuple[WavemakerSlot, ...] = Field(max_length=48)

    @model_validator(mode="after")
    def validate_slots(self) -> Self:
        indexes = [item.slot_index for item in self.slots]
        if len(set(indexes)) != len(indexes):
            raise ValueError("시간표 슬롯 위치가 중복되었습니다")
        modes = LOCAL_WAVEMAKER_PRO_MODES if self.family == "pro" else _BAR_MODES
        for item in self.slots:
            if item.mode not in modes:
                raise ValueError("선택한 제품이 지원하는 시간표 모드가 아닙니다")
            if item.mode == "feed" and item.feed_time < 1:
                raise ValueError("먹이 슬롯에는 1..60분의 먹이 시간이 필요합니다")
            if self.family == "bar" and item.custom_frequency:
                raise ValueError("Bar 시간표에는 custom 주파수 필드가 없습니다")
            if self.family == "pro" and item.pulse_tide:
                raise ValueError("Pro 시간표에는 Bar pulse/tide 필드가 없습니다")
            if self.family == "bar" and item.mode == "feed":
                if item.flow is not None:
                    raise ValueError("Bar 먹이 슬롯의 byte 5는 Flow가 아니라 먹이 시간입니다")
                continue
            if self.family == "bar" and item.feed_time:
                raise ValueError("Bar의 먹이 시간은 먹이 슬롯에서만 저장할 수 있습니다")
            if item.flow == 0 and item.mode in {"feed", "stopped"}:
                continue
            if item.flow is None or not 30 <= item.flow <= 100:
                raise ValueError("일반 슬롯 Flow는 앱 범위 30..100으로 명시해야 합니다")
        return self

    @property
    def product_key(self) -> str:
        return (
            LOCAL_WAVEMAKER_PRO_PRODUCT_KEY if self.family == "pro" else LOCAL_WAVEMAKER_PRODUCT_KEY
        )

    def compile_image(self) -> bytes:
        size = 9 if self.family == "pro" else 8
        image = bytearray(b"\xee" * (48 * size))
        for item in self.slots:
            if self.family == "pro":
                # 미리보기는 write 모듈을 가져오지 않는다.
                # 공개 codec과의 일치는 계약 시험으로 묶는다.
                wire = bytes(
                    (
                        *(int(part) for part in item.start.split(":")),
                        *(int(part) for part in item.end.split(":")),
                        LOCAL_WAVEMAKER_PRO_MODES.index(item.mode),
                        item.flow,
                        item.frequency,
                        item.feed_time,
                        item.custom_frequency,
                    )
                )
            else:
                wire = bytes(
                    (
                        *(int(part) for part in item.start.split(":")),
                        *(int(part) for part in item.end.split(":")),
                        _BAR_MODES.index(item.mode),
                        item.feed_time if item.mode == "feed" else item.flow,
                        item.frequency,
                        int(item.pulse_tide),
                    )
                )
                if wire == b"\x00" * 8:
                    raise ValueError("Bar 활성 슬롯이 미사용 슬롯의 0 바이트와 충돌합니다")
            start = item.slot_index * size
            image[start : start + size] = wire
        return bytes(image)

    def preview(self) -> dict[str, object]:
        image = self.compile_image()
        return {
            **self.model_dump(mode="json"),
            "image_bytes": len(image),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "hardware_write": False,
            "device_acceptance": "unverified",
        }


class SlotFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_index: SlotIndex
    flow: Percent


class NativeSlaveDefinition(BaseModel):
    """상속 대상에는 시간·모드·부가값 입력이 없고 슬롯 Flow만 있다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_member: Member
    flows: tuple[SlotFlow, ...] = Field(max_length=48)


class NativeWavemakerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["sync", "async"]
    master: Member
    schedule: WavemakerSchedule
    slaves: tuple[NativeSlaveDefinition, ...] = Field(min_length=1)
    preparation_flow: int = Field(default=30, ge=30, le=100, strict=True)
    preparation_frequency: int = Field(default=20, ge=0, le=100, strict=True)
    preparation_slave_flow: int | None = Field(default=None, ge=30, le=100, strict=True)

    @model_serializer(mode="wrap")
    def _omit_absent_slave_preparation(self, handler):
        result = handler(self)
        if self.preparation_slave_flow is None:
            result.pop("preparation_slave_flow", None)
        return result

    def preparation_flow_for(self, member: str) -> int:
        if member != self.master and self.preparation_slave_flow is not None:
            return self.preparation_slave_flow
        return self.preparation_flow

    @model_validator(mode="after")
    def validate_topology(self) -> Self:
        if self.schedule.family != "pro":
            raise ValueError("이 Sync/Async 계약은 Pro 전용입니다. Bar는 독립 설정을 사용하세요")
        names = [self.master, *(slave.logical_member for slave in self.slaves)]
        if len(set(names)) != len(names):
            raise ValueError("마스터와 슬레이브는 서로 다른 논리 장비여야 합니다")
        active = {item.slot_index: item for item in self.schedule.slots}
        for slave in self.slaves:
            indexes = [item.slot_index for item in slave.flows]
            if len(set(indexes)) != len(indexes) or set(indexes) != set(active):
                raise ValueError(
                    "슬레이브는 마스터의 모든 활성 슬롯 Flow를 한 번씩 명시해야 합니다"
                )
            for item in slave.flows:
                if item.flow == 0 and active[item.slot_index].mode == "feed":
                    continue
                if not 30 <= item.flow <= 100:
                    raise ValueError("슬레이브 일반 슬롯 Flow는 30..100이어야 합니다")
        return self

    def compile_images(self) -> dict[str, bytes]:
        master = self.schedule.compile_image()
        images = {self.master: master}
        for slave in self.slaves:
            image = bytearray(master)
            for item in slave.flows:
                image[item.slot_index * 9 + 5] = item.flow
            images[slave.logical_member] = bytes(image)
        return images

    def preview(self) -> dict[str, object]:
        images = self.compile_images()
        master = images[self.master]
        return {
            "mode": self.mode,
            "master": self.master,
            "preparation_flow": self.preparation_flow,
            "preparation_frequency": self.preparation_frequency,
            "preparation_flows": {member: self.preparation_flow_for(member) for member in images},
            "schedule": self.schedule.model_dump(mode="json"),
            "slaves": [slave.model_dump(mode="json") for slave in self.slaves],
            "images": {
                member: {
                    "image_bytes": len(image),
                    "image_sha256": hashlib.sha256(image).hexdigest(),
                    "changed_offsets_from_master": [
                        index
                        for index, (a, b) in enumerate(zip(master, image, strict=True))
                        if a != b
                    ],
                }
                for member, image in images.items()
            },
            "hardware_write": False,
            "device_acceptance": "unverified",
        }
