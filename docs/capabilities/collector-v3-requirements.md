# Read-only collector v3 requirements

이 문서는 세 수류모터 capability 조사에 필요한 read-only collector 확장의 호환성과 증거
요건을 고정합니다. 구현 전 요구사항이며 native ASYNC write 하네스 동결을 해제하지 않습니다.

## 0. 이번 확장의 판정 범위

v3의 첫 목표는 두 Pro와 바형 한 대의 write-free raw를 같은 기준으로 보존하는 것입니다.
**전체 지원 mode 목록이나 장비 허용 범위를 확정하는 실험이 아닙니다.**

- L0 frame·layout과 최상위 L1/L2 필드는 모델별 predicate를 통과한 현재 관측값만 claim으로
  발행
- Pro schedule slot은 기존 감사된 mode-aware validator를 통과한 값만 발행
- 바형 schedule slot은 `flow`·`frequency`를 감사하는 validator가 없으므로 accepted claim
  발행 0건. raw와 decode 결과는 private artifact에 보존하고 공개 판정은 `UNKNOWN`
- sample에서 관측된 mode 집합은 전체 지원 mode 열거가 아니며, 관측 min/max는 accepted range가
  아님
- endpoint·step과 물리 유량·파형·위상은 이번 collector의 판정 대상이 아님

pilot은 3장비 transport·timing과 artifact 완전성을 확인하고, 후속 evidence series가 위 범위의
claim을 만듭니다. 바형 slot validator나 mode 전이 write를 이 단계의 누락 기능으로 추가하지
않습니다.

## 1. v2 artifact 호환성

v3는 기존 v2를 수정·대체하지 않고 병존하는 가산적 형식이어야 합니다.

- `local-wavemaker-pro-acquisition-v2` predicate와 v2 plan 검증 결과를 바꾸지 않음
- 기존 artifact 파일은 read-only이며 수정하거나 재생성하지 않음
- private artifact host에서 확장 전후 같은 v2 verifier를 수동 실행하고, 다음 다섯 series의
  판정과 재계산 digest가 동일함을 결과 문서에 기록
- 다음 preserved series 다섯 건의 기존 offline verification이 확장 전후 모두 PASS해야 함
  - `JFS-6b376f051804caf542ed8469c49ff868`
  - `JFS-a9e4086683e60d859faca6a04d80729a`
  - `JFS-a2f44ded609b34adab1425c1dcc40c0e`
  - `JFS-8e4204d2092e6ef017d09c7fd39d90e1`
  - `JFS-6d8d87542b82c6a8201bb671778452cf`
- v3 parser가 v2를 읽더라도 v2 verifier의 판정을 대신하지 않음

## 2. 대상과 import 경계

- 두 `Local Wavemaker Pro`와 한 `Local Wavemaker (with AP time-sync)`를 논리 target으로 수집
- 실제 MAC·vendor device id·private IP는 raw private artifact 밖으로 출력하지 않음
- expected physical identity binding을 fresh session과 각 sample에서 검증
- 장비 write API, control encoder, frozen native ASYNC 모듈을 import·호출·노출하지 않음
- static import-graph 검사와 transport 테스트로 control frame 0회를 증명
- 기존 `read_only_collector.py`, `capability_raw_analyzer.py`, `capability_matrix.py`,
  `profiles.py`, `schema.py`, `schedule.py`, `models.py`, `codec.py`, `schedule_wire.py`의 아홉
  analyzer pin source는 수정하지 않음. v3 collector·CLI·verifier·analyzer는 신규 모듈로만
  추가해 v2와 기존 observation provenance를 보존

## 3. 수집 프레임

- 장비별 sample마다 fresh authenticated session과 explicit state request 한 번만 사용
- unsolicited report를 허용하지 않고 transport action `0x03`을 포함한 전체 frame을 보존
- decode 전에 관측된 raw 길이 그대로 저장하며 padding 절단·정규화를 하지 않음
- 성공 sample에는 UTC와 monotonic start/end, 장비별 completion time, sequence number를 기록
- 실패 read도 UTC·monotonic 구간, 예외 class, sequence number와 함께 artifact에 기록
- accepted/rejected 여부와 상관없이 모든 sample을 보존
- raw와 manifest는 atomic write와 fsync 후에만 성공으로 보고 digest를 계산

passcode 교환 frame은 절대 보존하지 않습니다.

## 4. 3장비 timing 모델

v2의 pair gap을 3장비에 그대로 재사용하지 않습니다. v3 pilot에서 다음을 raw와 함께 측정한 뒤,
evidence epoch 전 manifest에 상한·허용 범위를 고정합니다.

- 장비별 request duration
- 한 cycle의 첫 request 시작부터 마지막 completion까지의 `cycle_span`
- 장비별 sample age와 freshness
- 인접 target completion gap과 최대 target gap
- cadence와 timeout 분포

pilot은 orientation 자료이며 capability PASS 근거가 아닙니다. pilot에서 유도한 값과 artifact의
opaque id·digest를 evidence manifest에 연결하고, epoch가 시작된 뒤 기준을 바꾸지 않습니다.

## 5. 모델별 acquisition predicate

Pro v2 predicate는 그대로 둡니다. 바형 predicate는 최소한 다음을 별도로 정의하고 테스트합니다.

- expected product profile과 identity binding
- raw status length 401 bytes
- `Mode`의 4-value code space와 `AutoMode`의 6-value code space를 분리
- `PulseTide`와 `AutoPulseTide`를 서로 다른 필드로 디코딩
- `Linkage`, `Flow`, `Frequency`, `FeedTime`, schedule layout을 profile 기준으로 디코딩
- unknown 또는 schema 밖 값은 raw를 버리지 않고 claim별 rejected reason으로 기록

`decode_status()`가 numeric schema range를 강제하지 않는다는 사실을 전제로 합니다. 값이 decode된
것만으로 sample을 capability PASS로 올리지 않습니다.

바형의 최상위 field predicate는 raw 길이, field 존재, enum code space, numeric schema 범위를
각각 기록하되, numeric 범위 밖 read-back을 sample 전체에서 삭제하지 않습니다. 해당 값은
그대로 보존하고 그 claim만 `UNKNOWN`으로 낮춥니다. `schedule.py`의 decode 성공이나 schema
선언만으로 바형 slot의 `flow`·`frequency`를 `PASS`로 올리지 않습니다.

## 6. provenance와 공개 기록

- collector source digest와 정확한 commit SHA를 manifest에 기록
- private artifact는 opaque id, UTC span, identity binding SHA-256, artifact digest만 공개
- `mcu_attributes_hex`·`extra_hex`는 두 Pro 값이 byte-identical인지 private 비교하기 전 공개 금지
- capability generator와 raw analyzer는 collector와 별도 모듈로 유지
- series 하나가 aggregate에 기여하는 canonical observation claim-set은 하나뿐이며 파일명은
  `observation-claim-set.<safe-series-id>.generated.yaml`로 유지
- analyzer source가 바뀐 뒤 같은 series를 다시 분석해 기존 파일을 덮어쓰거나, commit suffix만
  다른 두 번째 claim-set을 aggregate에 추가하지 않음. 재해석은 별도 adoption·supersession
  형식과 validator가 승인되기 전까지 aggregate 밖 진단으로만 취급
