# Wavemaker capability evidence

이 디렉터리는 세 수류모터의 모드·설정값·범위를 조사할 때 사용하는 판정 규칙과 파생
capability 자료를 보관합니다. `docs/runs/`는 실제 장비 실행 기록만 보관하므로, 장비 접촉이
없는 오프라인 재분석 결과는 이 디렉터리에 둡니다.

## 승인 범위와 안전 경계

2026-08-31 repository maintainer가 capability 트랙의 다음 read-only 범위를 승인했습니다.

- 기존 preserved raw의 오프라인 재분석
- 기존 v2 artifact 검증을 그대로 보존하는 가산적 read-only collector 확장
- 두 Local Wavemaker Pro와 바형 Local Wavemaker 한 대의 read-only 수집
- schema 선언, 장비 read-back, 물리 효과를 분리한 capability 문서화

이 승인은 hardware write 승인이 아니며 `AGENTS.md` §1의 native ASYNC write 하네스 동결을
해제하지 않습니다. 다음은 이 트랙에서 승인되지 않았습니다.

- 바형 수류모터에 대한 write: 384-byte schedule image를 복원할 승인된 수단이 없습니다.
- `Flow`·`Frequency` 등의 0/100 endpoint 탐침: `SwitchON=false`가 물리 정지를 보장한다는
  증거가 없고, latent manual 값이 활성화될 수 있습니다. 값 복원과 `TimerON`·`SwitchON`
  복원을 별도 frame으로 나누면 latent 값 노출 구간이 생깁니다. 또한 write 100 뒤 read-back
  100은 실제 수용과 펌웨어의 echo 후 내부 clamp를 구분하지 못해 FAIL을 PASS로 오판할 수
  있습니다.
- 범위를 벗어난 값을 장비에 보내 장비의 거부 동작을 시험하는 것
- Q2가 사용한 동결 CLI 또는 native ASYNC write 하네스의 재실행

Pro write 실기는 manual `Flow` 하향 정규화, 새 baseline 보존, standalone exact-restore의
attended qualification, 현장 감시와 물리 차단 확인을 모두 마친 뒤 별도 run으로만 수행합니다.
바형 write와 exact restore는 이번 capability 트랙에서 새 전용 하네스로 만들지 않고 일반
actuator 트랙에 포함합니다.

오프라인 재분석과 3장비 read-only 수집이 각각 새로 확정된 사실을 만들지 못하면 두 번 연속
정보 0으로 보고 `AGENTS.md` §7에 따라 코드 수정 없이 중단·보고합니다.

## 현재 read-only 조사의 질문과 완료선

이 트랙은 "지원한다"를 한 가지 뜻으로 합치지 않습니다. read-only raw가 답할 수 있는 것은
현재 저장돼 있거나 활성화된 **관측값 집합**까지이며, 관측된 최솟값과 최댓값을 장비의 허용
범위로 승격하지 않습니다.

| 질문 | 현재 read-only로 가능한 판정 |
|---|---|
| profile이 선언한 L1/L2 mode·필드·범위 | `(d)` `wire` claim으로 기록 가능. 장비 수용 증거는 아님 |
| raw frame의 크기·layout과 현재 L1 manual 값 | 해당 sample의 `(a)` 관측값으로 기록 가능 |
| 현재 schedule에 실제 들어 있는 L2 mode·설정값 | 감사된 모델별 slot validator가 있을 때만 `(a)` 관측값으로 기록 가능 |
| 장비가 수용하는 **전체** mode 열거 | 현재 값과 기존 schedule에 나타나지 않은 mode는 `UNKNOWN` |
| 장비가 수용하는 최소·최대·step | endpoint·전이 write 없이는 `UNKNOWN`; 관측 min/max로 대체 금지 |
| mode별 물리 유량·파형·위상 | 계측 수단이 없으므로 `UNKNOWN` |

Local Wavemaker Pro는 감사된 `schedule_wire` 검증기가 있으므로 보존 raw의 유효 슬롯에서
관측된 mode·설정값을 claim으로 만들 수 있습니다. 바형 Local Wavemaker는 현재 최상위 L0와
L1/L2 필드의 관측값까지만 공개 claim으로 만들며, slot의 `flow`·`frequency`를 검증하는 감사된
validator가 없으므로 `schedule.*` accepted claim은 발행하지 않습니다. private raw는 그대로
보존하고 그 빈 칸은 `UNKNOWN`으로 남깁니다. schema 선언을 그대로 validator로 옮겨 스스로
검증하는 구현은 만들지 않습니다.

따라서 다음 3장비 collector의 완료 조건은 전체 mode·범위 확정이 아니라, 두 Pro와 바형의
write-free explicit raw를 같은 provenance·durability 기준으로 보존하고 위 표의 답할 수 있는
칸만 채우는 것입니다. 전체 mode 수용과 유효 endpoint·step은 별도 승인된 app/code write,
복원 수단, 물리 계측 계획이 생길 때까지 후속 질문으로 남깁니다.

## `AutoFeedTime=0` 관측의 처리

[`observation-claim-set.JFS-a2f44ded609b34adab1425c1dcc40c0e.generated.yaml`](observation-claim-set.JFS-a2f44ded609b34adab1425c1dcc40c0e.generated.yaml)은
두 Pro의 36개 sample 모두에서 최상위 `AutoFeedTime=0`을 보존했습니다. 현재 profile 선언은
`1..60`이므로 이 필드만 `UNKNOWN`이며, 값 자체는 버리지 않았습니다. 같은 raw의 slot 수준
`feed_time=0,15`는 별도의 mode-aware 규칙 아래 `PASS`입니다. 장비가 최상위 값 `0`을 돌려준
이유는 아직 `UNKNOWN`입니다.

profile numeric range는 문서만이 아니라 control payload encoder의 write admission에도
사용됩니다. 따라서 이 관측만으로 profile 최소값을 `0`으로 낮추면 기존 write 범위를 넓히고,
동일 raw의 판정을 소급해 `UNKNOWN`에서 `PASS`로 바꾸게 됩니다. capability read-only 트랙에서는
`profiles.py`나 write validator를 수정하지 않습니다. 변경이 필요하면 별도 safety 검토와
repository maintainer 승인을 먼저 받습니다.

## 서로 다른 다섯 층

| 층 | 의미 | capability 판정 대상 |
|---|---|---|
| `L0` protocol | 제품 profile, frame 크기, packet·slot layout | 예 |
| `L1` native | 즉시 제어의 `Mode`와 manual 설정 필드 | 예 |
| `L2` schedule | 48개 슬롯의 `AutoMode`와 `Auto*` 설정 필드 | 예 |
| `L3` app preset | 앱이 여러 슬롯·필드를 조합한 이름 붙은 프로그램 | 앱 동작 증거가 있을 때만 |
| `L4` group pattern | `jebao-flowd`가 만드는 software pattern | 장비 capability가 아님 |

같은 숫자 코드라도 L1과 L2에서 같은 의미라고 가정하지 않습니다. 바형 장비는 이미 두 코드
공간이 다르고, Pro의 코드 공간 동일성도 실기 증거 전에는 schema 선언일 뿐입니다.

## 주장 목표 세 단계

각 claim은 다음 목표 중 하나만 가집니다.

- `wire`: schema나 wire layout이 어떤 값·코드를 선언하는가
- `accepted`: 장비가 어떤 값을 상태나 `Auto*`로 저장·되읽기 했는가
- `physical`: 해당 값이 실제 유량·파형·위상에 영향을 주었는가

read-back은 `accepted` 증거일 수 있지만 `physical` 증거가 아닙니다. 현재 저장소에는 물리
파형을 계측하는 수단이 없으므로 `physical`은 이번 조사 범위 밖이며 `UNKNOWN`을 유지합니다.
현장 육안 관측만 추가될 경우에도 그 사실은 evidence `(c)`로만 기록합니다.

## 증거 등급

기존 `docs/runs/README.md`의 세 등급에 capability 전용 `(d)`를 추가합니다.

- `(a)` preserved raw artifact: 원본 프레임에서 오프라인으로 재도출 가능
- `(b)` preserved structured/durable daemon artifact: 데몬이 영구 저장한 주장까지만 증명
- `(c)` reconstructed operator observation: 보존 raw나 durable artifact 없이 재구성한 관측
- `(d)` schema-declared, unverified: schema 또는 profile이 선언했지만 장비 관측으로 검증되지 않음

등급은 자동 승격되지 않습니다. 특히 `(d)` 범위가 곧 장비 수용 범위라는 뜻이 아니고,
`(a)` read-back도 물리 효과를 증명하지 않습니다.

## Vendor 앱 정적 근거

설치된 Jebao 앱의 제품 정의와 UI 템플릿은 저장소의 Python schema와 출처가 다른 상류 자료입니다.
따라서 앱 조사 결과를 기존 `(d) schema-declared` claim으로 가장하거나 장비가 값을 수용했다는
근거로 사용하지 않습니다. 사람이 읽는 조사 문서에서는 다음 **출처 축**을 evidence tier와
별도로 표기합니다.

- `V` — 앱이 내려받은 vendor 제품 정의가 선언한 datapoint, layout, code space 또는 범위
- `U` — 앱 UI 템플릿의 정적 render·save dataflow에서 확인한 노출·제약·직렬화 동작
- `L` — `docs/runs/` 또는 보존 artifact가 뒷받침하는 실제 장비 관측
- `?` — 위 자료로 닫히지 않은 항목

`V`와 `U`는 출처 분류이지 새 evidence tier가 아닙니다. 현재 claim validator는 `a`~`d`만
허용하므로 vendor-app 결과를 generated claim-set에 넣지 않습니다. 향후 기계 claim으로
채택하려면 다음 source 문법과 별도 소유권·validator 규칙을 먼저 코드와 문서에 함께 추가해야
합니다.

```text
vendor-app:<app-version>:product-definition:<safe-product-family>#<subject>
vendor-ui:<app-version>:template:<safe-product-family>:newest-cached#<subject>
```

source에는 로컬 절대경로, device id, MAC, token, passcode 또는 공개 저장소에 없던 product key를
넣지 않습니다. 원본 vendor JSON·JavaScript는 저장소에 복사하지 않고 상호운용에 필요한 파생
인터페이스 사실만 기록합니다. `newest-cached`는 조사 당시 로컬 캐시에서 숫자상 가장 최신인
템플릿이라는 뜻이며 서버의 전역 최신 버전이나 실제 활성 선택을 증명하지 않습니다.

2026-08-31에 등록된 6대를 다섯 제품군으로 대조한 결과와 영문 UI 명칭은
[`registered-device-app-analysis.md`](registered-device-app-analysis.md)에 있습니다.

## claim 형식과 판정

모든 claim은 아래 필드를 가져야 합니다. 누락은 검증 실패입니다.

- `claim_id`
- `product_key`
- `layer`: `L0`, `L1`, `L2`, `L3` 중 하나
- `goal_tier`: `wire`, `accepted`, `physical` 중 하나
- `subject`
- `value`
- `evidence_tier`: `a`, `b`, `c`, `d` 중 하나
- `source`
- `status`: `PASS`, `FAIL`, `UNKNOWN` 중 하나

판정 규칙은 장비 데이터를 보기 전에 고정합니다. 목표별 최소 증거는 다릅니다.

- `wire`: schema 선언 claim은 `(d)` source가 실제 선언과 일치하면 `PASS`; 관측 wire layout
  claim은 `(a)` raw에서 재도출돼야 `PASS`
- `accepted`: `(a)` raw 또는 claim 범위를 명확히 제한한 `(b)` durable artifact에서 값이
  재도출되고, 같은 series 안에 상충 sample이 0개여야 `PASS`
- `physical`: 측정 수단이 없는 현재 범위에서는 항상 `UNKNOWN`
- `FAIL`: 부정 claim이 위와 같은 최소 증거를 충족
- `UNKNOWN`: 그 외 전부. claim 레코드가 없는 칸도 `UNKNOWN`
- 관측값이 다른 그럴듯한 출처와 우연히 같아 구분할 수 없으면 `UNKNOWN`
- rejected sample과 실패한 read도 버리지 않고 이유와 함께 보존

다음 불변식은 validator가 강제합니다.

- `evidence_tier: d`이면 `goal_tier: wire`
- `goal_tier: physical`이면 `status: UNKNOWN`
- `withdrawn: true`이면 `status: UNKNOWN`이며 현재 capability 필터에서 제외

## 파일 소유권과 집계

capability 판정은 다음 세 소유권 영역의 합집합입니다.

- [`wavemaker-capability-matrix.yaml`](wavemaker-capability-matrix.yaml): 사람이 사전 등록한
  claim과 예시의 소유자입니다. generator와 analyzer는 이 파일을 수정하지 않습니다.
- [`schema-claim-set.generated.yaml`](schema-claim-set.generated.yaml): 고정된 source commit의
  `profiles.py`와 `schedule.py`에서 generator가 만든 `(d)` schema claim의 소유자입니다.
- `observation-claim-set.<safe-series-id>.generated.yaml`: 검증된 preserved raw series 하나에서
  analyzer가 만든 불변 `(a)` observation claim-set의 소유자입니다. series마다 새 파일을 만들며
  기존 파일을 덮어쓰지 않습니다.

한 series는 집계에 기여하는 canonical observation claim-set을 **정확히 하나만** 가집니다.
파일명은 series id만으로 고정하고, analyzer commit이 바뀌어도 같은 series의 두 번째 aggregate
claim-set을 만들거나 기존 파일을 덮어쓰지 않습니다. 향후 같은 raw를 새 규칙으로 다시 해석할
필요가 생기면 그 결과는 우선 aggregate 밖의 진단으로만 남깁니다. canonical 판정을 교체하려면
이전 판정을 보존하는 별도 adoption·supersession 형식과 validator 규칙을 먼저 설계·검토해야
합니다. analyzer commit 축약만 파일명에 덧붙여 두 판정을 함께 집계하는 방식은 사용하지
않습니다.

최종 판정은 matrix의 `claims`와 schema claim-set, 존재하는 모든 observation claim-set을
validator로 읽고 집계한 결과입니다. matrix의 빈 `claims`만 보고 schema claim이 없다고 해석하면
안 됩니다. 특정 claim의 부재가 `UNKNOWN`이라는 규칙도 이 전체 집계가 완전할 때만 적용합니다.
matrix 자체는 기계 생성 claim을 기여하지 않으므로 `analysis_provenance`를 모두 `null` 또는 빈
목록으로 유지하고, 각 생성 파일이 자신의 source commit·source digest·artifact digest를
소유합니다.

현재 `schema-declared` 행은 generator가
[`schema-claim-set.generated.yaml`](schema-claim-set.generated.yaml)에 생성합니다. 사람이 수백
행을 복사하지 않습니다. generator는 같은 commit·입력에서 byte-identical 출력을 내야 하고
`(d)` 행만 생성할 수 있습니다. 관측 `(a)` 행은 별도 analyzer만 추가하며, `(b)`·`(c)`를
이관할 경우에도 별도 소유권과 validator 규칙을 먼저 정의해야 합니다.

`source`는 파생 출처를 드러내야 합니다. 관측 claim을 생성한 analyzer의 commit SHA와 source
digest, 입력 artifact digest는 해당 observation claim-set의 `analysis_provenance`에 기록해야
합니다. 예:

```text
schema:jebao_flow.protocol.profiles@<commit-sha>
artifact:JFS-<opaque-id>#<ordinal>
run:docs/runs/<safe-relative-path>#<section>
```

실제 MAC, vendor device id, private IP, passcode, 사설 절대경로는 기록하지 않습니다.
`mcu_attributes_hex`와 `extra_hex`는 per-unit 식별값일 수 있으므로 두 Pro가 byte-identical인지
확인하기 전에는 원문을 공개하지 않습니다. 다르면 digest와 차이 byte 위치만 기록합니다.

## legacy 장비 문서 취급

`docs/devices/*.yaml`의 기존 `observed_readable`, `schema_declared_writable`,
`write_validated` 목록은 evidence tier가 없는 legacy 자료입니다. generator의 입력으로 사용하지
않고, 이 디렉터리의 claim 형식으로 이관되기 전에는 현재 capability 판정 근거로 삼지 않습니다.
특히 Pro의 과거 `write_validated` 목록은 보존 artifact가 없는 등급 `(c)` 미이관 legacy
자료이므로 현재 capability 근거로 사용하지 않습니다. 철회된 것은 같은 파일에 남아 있던
"async-slave-only Flow change did not persist" 해석이며, 그 문장은 증명되지 않은 것으로
정정했습니다. 나머지 네 장비 YAML의 전면 이관은 generator가 준비된 뒤 수행해 수기 schema
drift를 피합니다.
