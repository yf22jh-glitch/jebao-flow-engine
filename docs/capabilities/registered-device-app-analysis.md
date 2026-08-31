# Registered device app capability analysis — 2026-08-31

## 결론

등록된 6대는 다섯 제품군으로 모두 분류됐습니다. 두 `Local Wavemaker Pro`만 native
`Master`·`Sync Slave`·`Async Slave`를 지원하고, 바형 수류모터는 `Independent`·`Master`·
`Slave`만 지원합니다. 두 리턴펌프 후보와 도징펌프에는 native linkage datapoint가 없습니다.

앱의 Pro slave 화면은 `Sync`·`Async` 선택과 공통 `Flow` 슬라이더 하나만 노출하고, slave의
slot별 편집기는 노출하지 않습니다. 그래서 앱에서는 slave 출력이 모든 모드에 고정된 것처럼
보입니다. 이는 앱 UI 제약이며 firmware 한계가 아닙니다. 실제 Q2 실기에서는 이 두 Pro의
`async_slave`가 master와 다른 자기 slot `AutoFlow`를 900초 epoch 동안 적용했습니다.

## 조사 범위와 근거

- 조사 대상 앱: Jebao Aqua 3.4.18, build 341800000
- 방식: vendor 제품 정의와 로컬에 내려받아진 plain JavaScript UI 템플릿의 read-only 정적 분석
- 장비·앱 변경: UI click 0회, 장비 write 0회, packet injection 0회
- 제외: SDK log, HTTP storage, crash cache, credential·token 저장소
- 버전 한계: 아래 `U` 결과는 조사 당시 제품별 **숫자상 newest cached template** 기준이며,
  vendor 서버의 전역 최신 또는 앱이 선택한 활성 버전이라는 뜻은 아닙니다.

표기는 capability evidence tier와 별개인 출처 축입니다.

- `V`: vendor 제품 정의의 선언
- `U`: 앱 UI 템플릿의 정적 render·save dataflow
- `L`: 보존된 실제 장비 실행 증거
- `?`: 아직 확인되지 않음

Vendor 원문과 사설 경로는 보존소에 복사하지 않습니다. 이 문서에는 영문 UI 명칭과 상호운용에
필요한 파생 인터페이스 사실만 기록합니다.

## 등록 장비 6대

| 제품군 | 수량 | native grouping | Jebao Flow Engine에서의 역할 |
|---|---:|---|---|
| Local Wavemaker Pro | 2 | Independent / Master / Sync Slave / Async Slave | 메인 native pair와 개별 제어 |
| Local Wavemaker (bar) | 1 | Independent / Master / Slave | software group의 독립 보조 수류 |
| DC Pump Pro | 1 | 없음 | 개별 제어 및 software group |
| Aquarium Pump | 1 | 없음 | 개별 제어 및 software group |
| Dosing Pump | 1 | 없음 | 별도 4채널 도징 UI |

앱의 bound-device 제품군 다중집합과 저장소 `docs/devices/`의 `observed_instances` 합계가
`2 + 1 + 1 + 1 + 1`로 일치합니다. 개별 device id, MAC, token은 읽거나 기록하지 않았습니다.

## Local Wavemaker Pro — 2대

### Mode와 설정

`V`에서 manual `Mode`와 schedule `AutoMode`는 같은 0~8 code space를 선언합니다.

| code | 앱 영문 label | `U`에서 노출되는 논리 필드 |
|---:|---|---|
| 0 | Pulse | `Flow`, `Frequency` |
| 1 | Sine | `Flow`, `Frequency` |
| 2 | Constant | `Flow` |
| 3 | Random | `Flow`, `Frequency` |
| 4 | Tide Rise and Fall | `Flow` |
| 5 | Nutrient Transport | `Flow` |
| 6 | Cross-flow | `Flow`, `Frequency` |
| 7 | Feeding | `FeedTime` |
| 8 | Custom Wave | `Flow`, `Frequency`, `Cust_Wav_Freq` |

두 번째 열만 앱에 실제 표시되는 영문 label입니다. 세 번째 열은 UI dataflow와 vendor datapoint를
대조하기 위한 저장소의 논리 필드명이며, 앱 화면 문자열을 그대로 옮긴 것이 아닙니다.

`V`의 48개 schedule slot은 slot당 9바이트이며 다음 의미를 가집니다.

```text
start hour, start minute, end hour, end minute,
mode, flow, frequency, feed time, custom-wave frequency
```

선언 범위와 앱 편집 범위는 같지 않습니다.

| 필드 | `V` 선언 | `U` 편집 |
|---|---:|---:|
| Flow / Auto Flow | 0..100 | 30..100 |
| Frequency / Auto Frequency | 0..100 | 5..100 |
| Custom Wave Frequency | 0..100 | 5..100 |
| Feed Time / Auto Feed Time | 1..60 | manual 1..60; schedule은 아래 불일치 참조 |

### Linkage와 앱의 slave 제약

`V` Linkage code space는 다음과 같습니다.

| code | role |
|---:|---|
| 0 | Independent |
| 1 | Master |
| 2 | Sync Slave |
| 3 | Async Slave |

`U`에서 manual 설정 컨트롤은 `TimerON=false`이고 role이 `Independent` 또는 `Master`일 때만
활성화됩니다. Linkage 화면의 master 영역에는 `Schedule`과 `Manual` 진입 버튼이 있지만,
slave 영역에는 `Sync`·`Async` 선택과 `Flow` 30..100 슬라이더 하나만 있습니다. slave slot
editor는 없습니다.

따라서 제바오 앱만으로는 `Async Slave`의 slot별 `AutoFlow`를 편집할 수 없습니다. 앱에 보이는
공통 slave `Flow`와 장비 안에 저장되는 slot별 `AutoFlow`를 별도 속성으로 취급해야 합니다.

### 실제 장비에서 확인된 범위

[`Q2 attempt 05`](../runs/2026-08-30-q2-attempt-05.md)의 `L` 증거로 다음을 확정했습니다.

- 경계 전: master `Constant/31`, slave `Constant/32`
- 비원자 첫 sample: master `Constant/31`, slave `Sine/40`
- 수초 뒤: master `Sine/35`, slave `Sine/40`
- 이후 안정 조건 300초와 전체 epoch 900초 동안 35/40 유지, 상충 sample 0
- 종료 후 두 장비 `Independent`, 원 control·TimerON, 두 432-byte schedule image byte-exact 복원

따라서 이 pair와 계획 범위에서 Q2는 **YES/PASS**입니다. `Async Slave`가 master 35를 따라가지
않고 자기 B slot의 40을 적용했습니다.

아직 `?`인 항목은 manual slave `Flow` 독립 유지(Q1), 물리 유량·파형·위상, 다른 firmware와
다른 장비 pair, 장기간 반복 신뢰도입니다.

### 선언·앱·장비 불일치

- `L`: 두 Pro의 36 sample 모두 최상위 `AutoFeedTime=0`을 반환했습니다.
- `V`: `AutoFeedTime`은 1..60으로 선언됩니다.
- `U`: schedule Feeding의 저장값은 비활성 slider 값이 아니라 slot 시작·종료 시각의 차이를
  1..240으로 제한해 만들 수 있습니다.

즉 0과 61..240의 실제 장비 수용 여부는 모두 `?`입니다. profile 범위를 넓히거나 기존
1..60 write guard를 완화하지 않습니다. 특히 앱에서 60분을 넘는 Feeding slot을 만들면 현재
분석기의 schedule claim과 exact-restore admission이 `UNKNOWN` 또는 거부로 내려갈 수 있으므로
live operation 전 점검해야 합니다.

## Local Wavemaker bar — 1대

### Mode와 설정

`V`에서 manual과 schedule code space가 서로 다릅니다.

| manual code | 앱 영문 label | `U` 설정 |
|---:|---|---|
| 0 | Classic | `Flow`, `Frequency`, `PulseTide` (`Pulse` / `Tide`) |
| 1 | Sine | `Flow`, `Frequency` |
| 2 | Random | `Flow` |
| 3 | Constant | `Flow` |

| schedule code | 앱 영문 label | slot 의미 |
|---:|---|---|
| 0 | Stop | 정지 |
| 1 | Classic | `Flow`, `Frequency`, `PulseTide` (`Pulse` / `Tide`) |
| 2 | Sine | `Flow`, `Frequency` |
| 3 | Random | `Flow` |
| 4 | Constant | `Flow` |
| 5 | Feeding | `FeedTime` |

48개 schedule slot은 slot당 8바이트입니다.

```text
start hour, start minute, end hour, end minute,
mode, flow-or-feed-time, frequency, pulse-or-tide
```

`V` Linkage는 `Independent`, `Master`, `Slave` 세 역할뿐이며 `Async Slave`가 없습니다. `U`의
slave 저장 payload도 `TimerON=false`, role `Slave`, 현재 Mode·Frequency·Pulse/Tide와
사용자가 고른 공통 `Flow` 하나를 보냅니다.

현재 등록 product가 앱의 특수 편집 허용 목록에 포함되지 않는다는 정적 대조 기준에서는
schedule `Random`을 선택할 수 있지만 설정 편집은 비활성화되고, `Classic`·`Sine`·`Constant`·
`Feeding`은 편집 대상입니다. 이 항목은 앱 버전별 특수 목록이 바뀔 수 있으므로 `U` 제약으로만
사용합니다.

이 장비에는 targeted `L` 수집과 승인된 384-byte exact restore 수단이 없습니다. native ASYNC에
넣지 않고 daemon의 software group에서 독립 보조 펌프로 제어합니다.

## DC Pump Pro — 1대

- `V`: Linkage datapoint가 없습니다.
- `U`: 남아 있는 Linkage handler는 호출부가 없는 dead route입니다.
- 앱 영문 mode: `Constant`, `Pulse`, `Sine`, `Random`, `Feeding`.
- 앱 설정: Constant=Flow, Pulse/Sine=Flow+Frequency, Random=Flow, Feeding=Feed Time.
- 48개 schedule slot, slot당 8바이트:
  `start hour, start minute, end hour, end minute, mode, flow, frequency, feed time`.

Mode code에는 vendor 내부 모순이 있습니다.

| code | top-level `V` | schedule-slot `V` |
|---:|---|---|
| 0 | Constant | Pulse |
| 1 | Pulse | Sine |
| 2 | Sine | Constant |
| 3 | Random | Random |
| 4 | Feeding | Feeding |

`L`에서는 schedule code 0의 `Constant`만 확인됐고 code 1·2는 `?`입니다. 앱의 schedule Feeding
저장도 Pro와 같은 1..240 interval-derived dataflow를 가지지만 vendor `FeedTime` 선언은
1..60이므로 장비 accepted range는 `?`입니다. native grouping을 노출하지 않고 개별 제어와
daemon software group만 제공합니다.

## Aquarium Pump — 1대

이 제품의 `Mode`는 wave mode가 아닙니다.

- `V` top-level Mode: `AP Control` / `Wireless Control` boolean
- `V` schedule AutoMode: `Stop`, `Auto`, `Feeding`
- `U` manual speed slider: 30..100
- `V` Motor Speed numeric spec: 0..100
- 같은 `V` 설명: 0은 정지, 운전 범위는 30..100
- `V` Feed Time / Auto Feed Time: 1..60
- 48개 schedule slot, slot당 6바이트:
  `start hour, start minute, end hour, end minute, mode, gear-or-pause-time`
- Linkage 없음

Vendor 내부에서 Motor Speed의 기계 범위와 설명이 충돌합니다. 또한 `U` encoder는 `Auto`가
아닌 모든 slot의 마지막 바이트를 0으로 만들므로 `Feeding`도 vendor가 `pause time`이라고 부른
자리에 0을 직렬화합니다. 이는 앱과 vendor 선언의 정적 불일치로 확인됐지만 실제 장비 동작은
`?`입니다. 현재 write 범위를 0..100 또는 30..100 중 하나로 단정하지 않습니다.

## Dosing Pump — 1대

native mode와 Linkage는 없고 네 채널이 고정입니다.

| 기능 | 확인 결과 |
|---|---|
| 채널 | 4개, 각 manual on/off와 timer enable |
| schedule | 채널당 96바이트; `U`는 24개 entry × 4바이트로 해석 |
| entry | hour, minute, volume uint16 big-endian |
| interval | `V` 0..30, 단위 선언 없음; `U` label과 picker는 Days |
| calibration | `V` 10..100; `U`는 10 단위만 허용 |
| calibration time | `V` 10..250 seconds |
| volume | `U`는 숫자 최대 4자리; 장비 accepted·safe range는 `?` |

24×4 내부 layout과 Days·10단위 제약은 `U`에서 파생됐으며 vendor binary 정의 자체가 하위
구조를 선언한 것은 아닙니다. 도징 UI는 수류 group과 분리해 채널 카드, manual dose, timer,
24-entry schedule, interval, calibration을 제공합니다.

## Home Assistant UI에 반영할 결정

1. 표시 문자열은 한자 번역이 아니라 앱의 영문 label을 사용합니다. wire code와 표시 label은
   분리해 저장합니다.
2. 두 Pro는 개별 제어와 논리 group을 모두 노출합니다. group UI는 native Sync/Async와
   daemon software pattern을 구분합니다.
3. Pro slave의 slot별 target output은 capability상 제품 UI 후보가 될 수 있습니다. 다만 현재
   schedule transaction 경로는 `AGENTS.md` §1 동결 대상이므로, 명시적 동결 해제 또는 별도
   software actuator 트랙의 안전 설계가 승인되기 전에는 write control을 활성화하지 않습니다.
4. 바형은 native Async를 노출하지 않고 software group의 보조 member로만 phase·gain을
   적용합니다.
5. 두 리턴펌프 후보는 native linkage가 없는 개별 장비로 노출하되, 실제 장비 분류가 확정된
   뒤 daemon software group으로 묶을 수 있습니다.
6. 도징펌프는 수류 pattern selector를 공유하지 않는 별도 4채널 UI를 사용합니다.
7. `V`·`U` 불일치나 `?` 범위를 임의로 정상화하지 않습니다. capability별로 unsupported control을
   숨기고, unverified 범위는 보수적인 UI와 write guard를 유지합니다.

## 남은 확인 항목

- Pro manual slave `Flow` 독립 유지(Q1)
- 세 수류모터의 물리 모델 라벨, 설치 방향, 실제 유량·파형·위상
- bar의 현재 raw와 schedule 값, 장비 accepted 범위
- DC Pump Pro schedule code 1·2의 실제 의미와 feed time 수용 범위
- Aquarium Pump schedule Feeding의 byte 0 동작과 Motor Speed accepted range
- Dosing Pump volume accepted·safe range
- 제품별 globally latest 또는 실제 활성 UI template 버전

위 항목은 이번 정적 분석으로 닫혔다고 보고하지 않습니다.
