"""원본에서 추출한 안전한 반환 필드의 순수 대조. 장비·파일 접근은 없다."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.devices.native_scenario import BoundScenario
from jebao_flow.protocol.models import DeviceState
from jebao_flow.protocol.schedule_wire import extract_local_wavemaker_pro_schedule_image
from jebao_flow.protocol.session import RawStateCapture

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Returned = Annotated[int, Field(strict=True)] | str | None


class ScenarioReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    device_time: datetime | None
    action: int
    frame_sha256: Digest
    image_sha256: Digest
    role: str | None
    fields: dict[str, Returned]

    @classmethod
    def from_capture(cls, state: DeviceState, capture: RawStateCapture) -> ScenarioReply:
        attributes = state.observed_attributes
        return cls(
            device_time=state.schedule.device_local_time if state.schedule else None,
            action=capture.action,
            frame_sha256=hashlib.sha256(capture.wire_frame).hexdigest(),
            image_sha256=hashlib.sha256(
                extract_local_wavemaker_pro_schedule_image(capture.status_payload)
            ).hexdigest(),
            role=state.linkage.value if state.linkage is not None else None,
            fields={
                "auto_mode": attributes.get("AutoMode"),
                "auto_flow": attributes.get("AutoFlow"),
                "auto_frequency": attributes.get("AutoFreq"),
                "auto_feed_time": attributes.get("AutoFeedTime"),
                "auto_custom_frequency": attributes.get("Auto_Cust_Wav_Freq"),
                "reported_mode": state.mode,
                "reported_frequency": state.frequency,
            },
        )


class ScenarioPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ordinal: int = Field(ge=1)
    acquired_at: float = Field(ge=0)
    master: ScenarioReply
    slave: ScenarioReply


class StableEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pairs: Count
    stable_seconds: float = Field(ge=0, allow_inf_nan=False)
    last_ordinal: int = Field(strict=True, ge=1)
    master: ScenarioReply
    slave: ScenarioReply


class WindowEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    compared_fields: dict[Literal["master", "slave"], tuple[str, ...]]
    stable_evidence: StableEvidence | None


class ScenarioReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    scenario_sha256: Digest
    bound_sha256: Digest
    input_readback: Literal["PASS", "FAIL", "UNKNOWN"]
    response: Literal["PASS", "FAIL", "UNKNOWN"]
    windows: tuple[dict, ...]
    pair_count: Count
    exclusions: dict[str, Count]
    discrepancies: tuple[dict, ...]
    discrepancies_truncated: bool
    # 기술 종료·원복 결과는 다른 subsystem 소유이며 이 판정기가 만들어내지 않는다.

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        rows = tuple(WindowEvidence.model_validate(row) for row in self.windows)
        if not rows or len({row.name for row in rows}) != len(rows):
            raise ValueError("관측 창 증거는 비어 있거나 중복될 수 없습니다")
        qualified_count = 0
        for index, row in enumerate(rows):
            stable = row.stable_evidence
            if row.status == "PASS" and stable is None:
                raise ValueError("안정 표본 없는 창은 PASS일 수 없습니다")
            if stable is not None:
                needed_count, needed_span = (10, 120) if index == 0 else (2, 30)
                if (
                    stable.pairs < needed_count
                    or stable.stable_seconds < needed_span
                    or stable.last_ordinal < stable.pairs
                ):
                    raise ValueError("기존 안정 표본 개수·시간 계약을 충족하지 못했습니다")
                qualified_count += stable.pairs
        if qualified_count > self.pair_count:
            raise ValueError("관측 창의 표본 합계가 전체 취득 개수보다 큽니다")
        if self.input_readback == "PASS" and not self.pair_count:
            raise ValueError("실제 표본 없는 입력 read-back은 PASS일 수 없습니다")
        if self.response == "PASS" and (
            self.input_readback != "PASS"
            or any(row.status != "PASS" for row in rows)
            or self.exclusions.get("clock_regressed")
            or self.discrepancies
            or self.discrepancies_truncated
        ):
            raise ValueError("입력·각 창의 실제 증거가 모두 맞아야 반환값 PASS입니다")
        return self

    def matches_bound(self, bound: BoundScenario) -> bool:
        """저장된 보고서의 실제 표본을 해당 시간·시간표·역할·기대값에 다시 대조한다."""
        if (
            self.bound_sha256 != bound.digest
            or self.scenario_sha256 != bound.scenario.digest
            or len(self.windows) != len(bound.scenario.windows)
        ):
            return False
        for row, window in zip(self.windows, bound.scenario.windows, strict=True):
            if row["name"] != window.name or row["stable_evidence"] is None:
                return False
            if row["compared_fields"] != {
                role: sorted(getattr(window, role).fields()) for role in ("master", "slave")
            }:
                return False
            stable = StableEvidence.model_validate(row["stable_evidence"])
            for role in ("master", "slave"):
                reply = getattr(stable, role)
                clock = reply.device_time
                expected_role = "master" if role == "master" else bound.scenario.slave_role
                if (
                    clock is None
                    or clock.tzinfo is not None
                    or not window.start <= (clock - bound.anchor).total_seconds() < window.end
                    or reply.image_sha256 != hashlib.sha256(bound.image(role)).hexdigest()
                    or reply.role != expected_role
                ):
                    return False
                if row["status"] == "PASS" and any(
                    type(reply.fields.get(field)) is not type(wanted)
                    or reply.fields.get(field) != wanted
                    for field, wanted in getattr(window, role).fields().items()
                ):
                    return False
            assert stable.master.device_time is not None and stable.slave.device_time is not None
            if stable.master.device_time.replace(second=0, microsecond=0) != (
                stable.slave.device_time.replace(second=0, microsecond=0)
            ):
                return False
        return True


class ScenarioEvidence:
    """expected/actual 차이는 지우지 않으며 부족한 증거는 UNKNOWN으로 남긴다.

    기존 고정 실험의 판정기를 바꾸지 않는 별도 자료 기반 계약이다. 같은 날짜/분,
    양쪽 시각 증가, 경계 ±60초 제외, W0 10 pair/120초·이후 2 pair/30초를 유지한다.
    제외 뒤 재진입 자체를 값 충돌로 만들지 않고 실제 다른 반환 필드만 차이로 기록한다.
    """

    def __init__(self, bound: BoundScenario) -> None:
        self.bound = bound
        self.count = 0
        self.images_bad = False
        self.roles_bad = False
        self.exclusions: dict[str, int] = {}
        self.differences: list[dict] = []
        self.differences_truncated = False
        self._last_ordinal = 0
        self._last_acquired = -1.0
        self._last_clocks: tuple[datetime, datetime] | None = None
        self._candidate_window: int | None = None
        self._candidate_count = 0
        self._candidate_at = 0.0
        self._candidate_values: tuple | None = None
        self._qualified: dict[int, dict] = {}
        self._bad: set[int] = set()
        self._missing: set[int] = set()
        self._image_hashes = {
            role: hashlib.sha256(bound.image(role)).hexdigest() for role in ("master", "slave")
        }

    def _exclude(self, reason: str, *, reset: bool = True) -> None:
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1
        if reset:
            self._candidate_window = None
            self._candidate_count = 0

    def _difference(self, **row) -> None:
        if len(self.differences) < 512:
            self.differences.append(row)
        else:
            self.differences_truncated = True

    def acquisition_failed(self) -> None:
        self._exclude("acquisition_failed")

    def add(self, pair: ScenarioPair) -> dict:
        if pair.ordinal <= self._last_ordinal or pair.acquired_at <= self._last_acquired:
            raise ValueError("표본 ordinal과 취득 시각은 엄격히 증가해야 합니다")
        self._last_ordinal, self._last_acquired = pair.ordinal, pair.acquired_at
        self.count += 1
        row = {"ordinal": pair.ordinal, "window": None, "checks": [], "eligible": False}
        for participant in ("master", "slave"):
            reply = getattr(pair, participant)
            expected_image = self._image_hashes[participant]
            expected_role = "master" if participant == "master" else self.bound.scenario.slave_role
            for field, expected, actual in (
                ("image_sha256", expected_image, reply.image_sha256),
                ("role", expected_role, reply.role),
            ):
                if actual != expected:
                    self.images_bad |= field == "image_sha256"
                    self.roles_bad |= field == "role"
                    self._difference(
                        ordinal=pair.ordinal,
                        participant=participant,
                        field=field,
                        expected=expected,
                        actual=actual,
                    )
        clocks = (pair.master.device_time, pair.slave.device_time)
        if any(clock is None or clock.tzinfo is not None for clock in clocks):
            self._exclude("clock_missing_or_zoned")
            return row
        assert clocks[0] is not None and clocks[1] is not None
        if any(clock.date() != self.bound.anchor.date() for clock in clocks):
            self._exclude("date_mismatch")
            return row
        if clocks[0].replace(second=0, microsecond=0) != clocks[1].replace(second=0, microsecond=0):
            self._exclude("minute_mismatch")
            return row
        if self._last_clocks is not None:
            if any(now < old for now, old in zip(clocks, self._last_clocks, strict=True)):
                self._exclude("clock_regressed")
                self._qualified.clear()
                return row
            if any(now == old for now, old in zip(clocks, self._last_clocks, strict=True)):
                self._exclude("clock_repeated", reset=False)
                return row
        self._last_clocks = clocks
        offsets = tuple((clock - self.bound.anchor).total_seconds() for clock in clocks)
        matches = [
            index
            for index, window in enumerate(self.bound.scenario.windows)
            if all(window.start <= offset < window.end for offset in offsets)
        ]
        if len(matches) != 1:
            self._exclude("outside_declared_window")
            return row
        index = matches[0]
        window = self.bound.scenario.windows[index]
        row.update(window=window.name, eligible=True)
        missing = False
        mismatch = False
        for participant in ("master", "slave"):
            expected = getattr(window, participant).fields()
            actual = getattr(pair, participant).fields
            for field, wanted in expected.items():
                got = actual.get(field)
                status = (
                    "UNKNOWN"
                    if got is None
                    else "PASS"
                    if (type(got) is type(wanted) and got == wanted)
                    else "FAIL"
                )
                check = {
                    "participant": participant,
                    "field": field,
                    "expected": wanted,
                    "actual": got,
                    "status": status,
                }
                row["checks"].append(check)
                missing |= status == "UNKNOWN"
                mismatch |= status == "FAIL"
                if status != "PASS":
                    self._difference(ordinal=pair.ordinal, window=window.name, **check)
        if missing:
            self._missing.add(index)
        if mismatch:
            self._bad.add(index)
        if missing:
            self._candidate_window = None
            self._candidate_count = 0
            return row
        values = tuple(
            tuple(sorted(getattr(pair, participant).fields.items()))
            for participant in ("master", "slave")
        )
        if self._candidate_window != index or self._candidate_values != values:
            self._candidate_window = index
            self._candidate_count = 1
            self._candidate_at = pair.acquired_at
            self._candidate_values = values
        else:
            self._candidate_count += 1
        span = pair.acquired_at - self._candidate_at
        needed_count, needed_span = (10, 120) if index == 0 else (2, 30)
        if self._candidate_count >= needed_count and span >= needed_span:
            self._qualified[index] = {
                "pairs": self._candidate_count,
                "stable_seconds": span,
                "last_ordinal": pair.ordinal,
                "master": pair.master.model_dump(mode="json"),
                "slave": pair.slave.model_dump(mode="json"),
            }
        return row

    def report(self) -> ScenarioReport:
        windows = tuple(
            {
                "name": window.name,
                "compared_fields": {
                    role: sorted(getattr(window, role).fields()) for role in ("master", "slave")
                },
                "status": "FAIL"
                if index in self._bad
                else "PASS"
                if (index in self._qualified and index not in self._missing)
                else "UNKNOWN",
                "stable_evidence": self._qualified.get(index),
            }
            for index, window in enumerate(self.bound.scenario.windows)
        )
        response = (
            "FAIL"
            if self._bad or self.roles_bad or self.images_bad
            else "PASS"
            if (
                all(window["status"] == "PASS" for window in windows)
                and not self.exclusions.get("clock_regressed")
            )
            else "UNKNOWN"
        )
        return ScenarioReport(
            scenario_sha256=self.bound.scenario.digest,
            bound_sha256=self.bound.digest,
            input_readback="FAIL" if self.images_bad else "PASS" if self.count else "UNKNOWN",
            response=response,
            windows=windows,
            pair_count=self.count,
            exclusions=self.exclusions,
            discrepancies=tuple(self.differences),
            discrepancies_truncated=self.differences_truncated,
        )
