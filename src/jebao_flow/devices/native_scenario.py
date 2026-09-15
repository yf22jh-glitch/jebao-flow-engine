"""장비 접근 없는 선언형 Pro 시험 입력과 byte-exact 컴파일.

시나리오는 권한이 아니다. 실제 신원·범위·receipt·journal은 기존 실행기가 검사한다.
기대 반환값은 입력값에서 추측하지 않고 작성자가 별도로 지정한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.protocol.models import ScheduleEntry
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_MODES
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    encode_local_wavemaker_pro_schedule_entry,
)

ProMode = Literal[
    "pulse",
    "sine",
    "constant",
    "random",
    "tidal",
    "nutrient_transport",
    "circulation",
    "feed",
    "custom",
]
Scalar = Annotated[int, Field(strict=True, ge=0, le=100)]
Seconds = Annotated[int, Field(strict=True, ge=0, le=1020)]
Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioStep(_Input):
    """첫 항목은 0초, 이후 항목은 기준 분으로부터의 경계다."""

    at: Seconds
    mode: ProMode
    flow: Annotated[int, Field(strict=True, ge=0, le=41)]
    frequency: Scalar = 0
    feed_time: Annotated[int, Field(strict=True, ge=0, le=60)] = 0
    custom_frequency: Scalar = 0

    @model_validator(mode="after")
    def validate_step(self) -> Self:
        if self.at % 60 or self.at > 900:
            raise ValueError("전환은 분 단위이며 기준 시각 +900초 이내여야 합니다")
        if self.flow < 30 and not (self.mode == "feed" and self.flow == 0):
            raise ValueError("임시 Flow는 30..41, feed 정지는 0만 허용합니다")
        # codec의 실제 모드별 계약을 그대로 검증한다. 사용되지 않는 옵션의 펌웨어
        # 의미를 여기서 새로 추정하지 않는다.
        self.entry(slot=0, start="00:00", end="23:59")
        return self

    def entry(self, *, slot: int, start: str, end: str) -> ScheduleEntry:
        entry = ScheduleEntry(
            slot=slot,
            start=start,
            end=end,
            mode=self.mode,
            mode_code=LOCAL_WAVEMAKER_PRO_MODES.index(self.mode),
            parameters={
                "flow": self.flow,
                "frequency": self.frequency,
                "feed_time": self.feed_time,
                "custom_frequency": self.custom_frequency,
            },
        )
        encode_local_wavemaker_pro_schedule_entry(entry)
        return entry


class ExpectedReply(_Input):
    """exact 비교할 반환 필드. 생략한 필드는 PASS 대상에 포함되지 않는다."""

    auto_mode: ProMode
    auto_flow: Scalar
    auto_frequency: Scalar
    auto_feed_time: Scalar | None = None
    auto_custom_frequency: Scalar | None = None
    reported_mode: ProMode | None = None
    reported_frequency: Scalar | None = None

    def fields(self) -> dict[str, str | int]:
        return self.model_dump(exclude_none=True)


class ReplyWindow(_Input):
    name: Name
    start: Seconds
    end: Seconds
    master: ExpectedReply
    slave: ExpectedReply

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.end - self.start < 60:
            raise ValueError("반환값 관측 창은 최소 60초여야 합니다")
        return self


class NativeScenario(_Input):
    version: Literal[1] = 1
    name: Name
    slave_role: Literal["sync_slave", "async_slave"]
    master: tuple[ScenarioStep, ...] = Field(min_length=2, max_length=8)
    slave: tuple[ScenarioStep, ...] | None = Field(default=None, min_length=2, max_length=8)
    slave_flows: tuple[Annotated[int, Field(strict=True, ge=30, le=41)], ...] | None = Field(
        default=None,
        min_length=2,
        max_length=8,
    )
    windows: tuple[ReplyWindow, ...] = Field(min_length=2, max_length=8)

    @property
    def slave_steps(self) -> tuple[ScenarioStep, ...]:
        if self.slave is not None:
            return self.slave
        assert self.slave_flows is not None
        return tuple(
            ScenarioStep.model_validate({**step.model_dump(), "flow": flow})
            for step, flow in zip(self.master, self.slave_flows, strict=True)
        )

    @property
    def boundaries(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {step.at for steps in (self.master, self.slave_steps) for step in steps if step.at}
            )
        )

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if (self.slave is None) == (self.slave_flows is None):
            raise ValueError("slave 시간표 또는 master를 공유할 slave_flows 중 하나가 필요합니다")
        if self.slave_flows is not None and len(self.slave_flows) != len(self.master):
            raise ValueError("slave_flows 개수는 master 슬롯 개수와 같아야 합니다")
        for steps in (self.master, self.slave_steps):
            positions = tuple(step.at for step in steps)
            if positions[0] != 0 or positions != tuple(sorted(set(positions))):
                raise ValueError("시간표는 0초에서 시작하고 중복 없이 증가해야 합니다")
        if self.master[1].at != self.slave_steps[1].at:
            raise ValueError("첫 경계는 양쪽이 공유해야 기존 역할 준비 계약을 사용합니다")
        if not 360 <= self.boundaries[0] <= 600:
            raise ValueError("첫 경계는 준비 여유를 포함해 +360..600초여야 합니다")
        names = tuple(window.name for window in self.windows)
        if len(set(names)) != len(names):
            raise ValueError("관측 창 이름은 중복될 수 없습니다")
        previous_end = -1
        for window in self.windows:
            if window.start < previous_end:
                raise ValueError("관측 창은 순서대로 겹치지 않아야 합니다")
            if any(
                window.start < boundary + 60 and window.end > boundary - 60
                for boundary in self.boundaries
            ):
                raise ValueError("모든 경계 전후 60초는 반환값 판정에서 제외합니다")
            previous_end = window.end
        if self.windows[0].end > self.boundaries[0] - 60:
            raise ValueError("첫 창은 첫 경계 이전의 대조 구간이어야 합니다")
        if self.windows[0].end - self.windows[0].start < 180:
            raise ValueError("첫 창은 기존 10 pair/120초 판정에 충분한 180초 이상이 필요합니다")
        if self.windows[-1].start < self.boundaries[-1] + 60:
            raise ValueError("마지막 경계 뒤 관측 창이 필요합니다")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self)

    def bind(self, anchor: datetime) -> BoundScenario:
        return BoundScenario(scenario=self, anchor=anchor)


class ScenarioSuite(_Input):
    version: Literal[1] = 1
    cases: tuple[NativeScenario, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        if len({case.name for case in self.cases}) != len(self.cases):
            raise ValueError("시나리오 이름은 중복될 수 없습니다")
        return self

    def select(self, name: str) -> NativeScenario:
        for case in self.cases:
            if case.name == name:
                return case
        raise ValueError("지정한 시나리오가 없습니다")


class BoundScenario(_Input):
    scenario: NativeScenario
    anchor: datetime

    @model_validator(mode="after")
    def same_day(self) -> Self:
        if self.anchor.tzinfo is not None or self.anchor.second or self.anchor.microsecond:
            raise ValueError("기준 시각은 장비-local 날짜를 포함한 분 시작이어야 합니다")
        finish = self.anchor + timedelta(seconds=1080)
        if finish.date() != self.anchor.date() or finish.strftime("%H:%M") >= "23:59":
            raise ValueError("준비·마지막 관측은 같은 날 23:59 이전에 끝나야 합니다")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self)

    @property
    def boundary_time(self) -> str:
        return (self.anchor + timedelta(seconds=self.scenario.boundaries[0])).strftime("%H:%M")

    def entries(self, participant: Literal["master", "slave"]) -> tuple[ScheduleEntry, ...]:
        steps = self.scenario.master if participant == "master" else self.scenario.slave_steps
        starts = [
            "00:00",
            *[(self.anchor + timedelta(seconds=step.at)).strftime("%H:%M") for step in steps[1:]],
        ]
        ends = [*starts[1:], "23:59"]
        return tuple(
            step.entry(slot=index, start=start, end=end)
            for index, (step, start, end) in enumerate(zip(steps, starts, ends, strict=True))
        )

    def image(self, participant: Literal["master", "slave"]) -> bytes:
        entries = self.entries(participant)
        return b"".join(encode_local_wavemaker_pro_schedule_entry(entry) for entry in entries) + (
            LOCAL_WAVEMAKER_PRO_UNUSED_EE * (LOCAL_WAVEMAKER_PRO_SLOT_COUNT - len(entries))
        )


def canonical_digest(value: BaseModel) -> str:
    return hashlib.sha256(
        json.dumps(
            value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def load_scenario_suite(path: Path) -> ScenarioSuite:
    """중복 JSON key도 거부한다. JSON은 실행 가능한 코드나 YAML 태그를 받지 않는다."""

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON에 중복 필드가 있습니다")
            result[key] = value
        return result

    with path.open("rb") as stream:
        payload = stream.read(262145)
    if len(payload) > 262144:
        raise ValueError("시나리오 파일은 256KiB 이하여야 합니다")
    return ScenarioSuite.model_validate(json.loads(payload, object_pairs_hook=unique_pairs))
