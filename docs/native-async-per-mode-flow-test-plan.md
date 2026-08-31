# Native ASYNC 모드별 slave Flow 실기 계획서

> 상태: **REVIEWED DRAFT — repository maintainer 실행 승인, 동결 해제·현장 preflight 전,
> 장비 write 0회**
>
> 이 문서는 기존 운전 스케줄을 exact snapshot한 뒤 정지하고, 판별용 임시 스케줄을 새로
> 적용해 관측한 다음 원래 상태로 byte-exact 복원하는 단회 실기 계획입니다.

## 1. 이번에 답할 질문

기존 Jebao 앱의 `Async Slave` 화면에는 공통 `Flow` 입력이 하나뿐입니다. 예를 들어 master의
스케줄이 다음과 같아도 slave는 처음 입력한 `45`를 모든 모드에서 계속 사용합니다.

```text
Master: Sine(Frequency 40, Flow 50) -> Constant(Flow 60)
Slave:  Sine(Flow 45)               -> Constant(Flow 45)
```

최종 제품 목표는 다음과 같습니다.

```text
Master: Sine(Frequency 40, Flow 50) -> Constant(Flow 60)
Slave:  Sine(Flow 45)               -> Constant(Flow 60)
```

이번 실기는 아래 질문을 순서대로 한 번에 판별합니다.

1. `async_slave`의 실제 Mode와 전환 시각은 master 스케줄을 따르는가?
2. 그 상태에서 slave의 Flow만 자기 스케줄 슬롯 값 `45 -> 60`을 적용하는가?
3. 아니라면 slave는 자기 전체 스케줄, master 전체 값, 공통 slave Flow 중 무엇을 따르는가?

## 2. 기존 Q2 결과의 한계

`2026-08-30 q2-attempt-05`는 master와 slave 양쪽에 같은 경계 시각과 같은 Mode 순서
`Constant -> Sine`을 넣었습니다. Flow만 master `31 -> 35`, slave `32 -> 40`으로 달랐습니다.

이 실행은 `async_slave` 상태에서도 slave 자신의 TimerON 스케줄과 `AutoFlow`가 유지됐다는
사실과 exact restore를 확인했습니다. 하지만 아래 두 가설이 같은 결과를 내므로 이번 질문에는
판별력이 없습니다.

- slave가 자기 Mode와 Flow 스케줄을 독립 실행했다.
- slave가 master Mode를 따르면서 자기 Flow 스케줄만 적용했다.

따라서 기존 `YES/PASS`는 위의 좁은 사실로 정정하고, **master Mode + slave per-mode Flow**는
이번 신규 실기 전까지 `UNKNOWN`으로 둡니다.

## 3. 판별용 임시 스케줄

두 장비의 슬롯 경계 시각은 같게 두되, Mode는 일부러 반대로 넣습니다. master Flow는
`50 -> 55`, slave Flow는 `45 -> 60`으로 바꿉니다.

| 구간 | Master 임시 슬롯 | Slave 임시 슬롯 | 목표한 실제 slave 동작 |
|---|---|---|---|
| A, 경계 전 | `Sine`, Flow `50`, Frequency `40` | `Constant`, Flow `45` | master Mode를 따라 `Sine`, Flow는 `45` |
| B, 경계 후 | `Constant`, Flow `55` | `Sine`, Flow `60`, Frequency `35` | master Mode를 따라 `Constant`, Flow는 `60` |

이 값은 진단용입니다. 실제 운용 예시에서 master와 slave의 B 출력이 둘 다 `60`이면 slave의
`60`이 master 추종인지 독립 설정인지 구분할 수 없습니다. 그래서 실기에서는 master를
`50 -> 55`, slave를 `45 -> 60`으로 분리합니다. 기능이 확인된 뒤 실제 UI에서는 원하는
`master 50 -> 60`, `slave 45 -> 60`을 사용할 수 있습니다.

슬레이브의 latent manual/common Flow는 슬롯 값과 겹치지 않는 안전값 `35`로 둡니다.

```text
slave manual/common Flow = 35
slave A slot Flow        = 45
master A slot Flow       = 50
master B slot Flow       = 55
slave B slot Flow        = 60
```

master safe manual Flow `30`까지 포함한 `30, 35, 45, 50, 55, 60`은 모두 서로 다릅니다.
그래야 공통 manual 고정, A슬롯 유지, master 전면 추종, slave per-slot 적용을 서로 구분할 수
있습니다.

두 슬롯은 다음 조건을 만족해야 합니다.

- 동일한 A/B 경계 시각
- 15분 observation epoch 시작 뒤 약 6분에 A/B 경계 배치
- 경계 뒤 관측 가능 시간 약 9분
- `Constant` 슬롯의 wire Frequency는 `0`; 장비가 보고하는 기본 `AutoFreq`와 동일하다고 가정 금지
- non-feed 슬롯의 나머지 값은 감사된 안전 기본값 사용
- 이 operation의 승인된 출력 범위는 `30..60`; 기존 45% 단회 승인과 별개로 명시 승인 필요

## 4. 실행 전에 필요한 하네스 변경

현재 동결된 하네스는 master와 slave 모두 `Constant -> Sine`, 동일 Frequency로만 스케줄을
만들고 master의 B Mode가 `Sine`이 아니면 모든 결과를 `UNEXPECTED`로 분류합니다. 따라서 값만
바꿔 실행하면 이전과 똑같이 비판별적이며 이번 계획을 실행할 수 없습니다.

새 동결 해제 커밋은 다음 변경 범위를 명시적으로 열어야 합니다.

- master와 slave 각각에 독립적인 A/B `Mode`, `Flow`, `Frequency`를 주는 spec
- master safe manual Flow `30`, slave safe manual/common Flow `35`를 슬롯 Flow와 분리
- 함수 시그니처만 장비별로 일반화하고 값은 승인된 상수로 고정하는 2-slot encoder
- Mode 소유권과 Flow 소유권을 함께 분류하는 §7의 배타 classifier
- 900초 동안 진단 오류를 기록하면서 계속 관측하는 completion rule
- action byte를 포함한 원본 frame과 장비별 `NowTime` 보존
- 위 고정 계획에 직접 대응하는 단위 테스트와 fault-injection 테스트
- `schedule_linkage.py`의 기존 same-mode 전제를 이번 고정 계획에 한해서만 지정 축소

마지막 항목은 새 게이트나 범용 topology 지원이 아닙니다. 기존 staged observation의 안전·시간
전제는 유지한 채 다음 조건만 이번 고정 계획으로 교체합니다.

- `_snapshots_from_states()`에서는 양쪽 `boundary_at` 동일, 서로 다른 physical binding,
  `slave before.flow != slave after_flow`, `slave_after != master_after`를 유지하고, 두 장비의
  `before.mode` 동일 및 `after_mode` 동일 요구만 제거
- `_assert_staged_auto_transition_preconditions()`에서는 장비별 `Constant -> Sine` 고정과
  `snapshot.mode == before.mode` 요구만 §3의 장비별 A/B mode 쌍으로 교체
- 2-entry 비순환 schedule, `after_valid_until` 단일 창, 경계 후 안정 예산과 role-side
  frequency allowlist는 유지하며, master A=`Sine/40`과 두 Constant wire frequency=`0`을
  근거로 allowlist를 다시 고정
- `owned_staged_auto_transition_observation`은 계속 `True`여야 합니다. 이를 `False`로 내려 위
  전제 블록 전체를 우회하는 구현은 승인하지 않습니다.

해제 커밋은 코드 변경 범위와 함께 다음 통제 완화 3건을 조항으로 명시합니다.

1. 지정된 qualification operation의 기존 2/2 영수증에서 만료 시각만 이번 단회에 한해 무시
   — 영수증 부재·identity·operation 불일치는 우회 금지
2. 보존 raw의 장비 시계 차이를 반영한 clock-skew 게이트 완화(`<=30초`)
3. 출력 상한을 기존 단회 승인 `45`에서 이번 고정 계획의 `60`으로 재설정

세 번째 항목은 기존 물리 상한을 일반적으로 완화하는 것이 아니라 **서로 다른 여섯 값으로
판별력을 확보한 이번 고정 계획 전용 상한**입니다. `60`은 두 장비의 보존 baseline active
schedule 범위(role A `30..60`, role B `50..80`) 안이며 새 물리 출력 영역을 열지 않습니다.

이 셋 외에 identity·single-write·durable journal·rollback 권한은 완화하지 않습니다.

허용 파일은 다음으로 한정합니다.

- `src/jebao_flow/devices/schedule_flow_experiment.py`
- `src/jebao_flow/devices/schedule_linkage.py`의 raw/NowTime 진단 추가와 위에 지정한
  same-mode 전제 축소만
- 장비별 patch 전달에 실제 변경이 필요한 경우의 `schedule_transaction.py`
- qualification 만료 방침에 필요한 최소 `schedule_flow_experiment_cli.py` 변경
- 위 변경에 직접 대응하는 기존 단위 테스트

`devices/linkage.py`, `schedule_linkage_cli.py`, 일반 데몬과 MQTT 경로는 이번 해제에서 계속
닫아 둡니다. 허용 실행은 §3 고정 계획의 native ASYNC 실기 1회뿐입니다. `sync_slave`나
`independent` 대조, 동일 실기 반복은 별도 승인 전에는 실행하지 않습니다.

CLI에서 임의 값을 받는 범용 실험기로 넓히지 않습니다. 이번 승인 plan을 코드 상수 또는
서명된 manifest로 고정하고, 계획 밖 Mode·Flow 조합은 기존 static validation에서 첫 write 전
거부합니다. 새 장비 read 기반 pre-write gate나 새 실패 계층은 추가하지 않습니다.

현재 outer budget은 `observation 900 + reserve 285 = 1185초`로 1200초 상한 안에 들어갑니다.
고정 예산은 setup 최대 150초, write-side restore 최대 135초입니다. schedule image 2장 write와
검증의 forward timeout은 기존 90초, recovery authority는 기존 2400초를 유지합니다. window가
915초를 넘으면 reserve를 잠식하므로 900초에서 늘리지 않습니다.

## 5. 실행 전 필수 조건

다음 중 하나라도 충족하지 못하면 첫 write 전에 `NO-GO`로 종료합니다.

- repository maintainer가 이 **새 단회 실기**를 명시 승인
- `AGENTS.md`의 동결 범위를 이 계획에 한해 해제한 별도 커밋 존재
- 현장 감시자와 즉시 전원 차단 수단 확보
- 기존 Home Assistant 직접 통합과 다른 write controller 중지
- recovery supervisor, operation lease, durable journal 정상
- 미완료 outer-control, temporary-schedule, schedule-linkage journal이 모두 없음
- emergency-stop latch가 없음
- 두 대상의 fresh physical identity binding 일치
- 시작 상태가 두 장비 모두 `TimerON + independent`이고 active schedule이 유효함
- 두 장비의 원 outer control과 전체 432-byte schedule image를 fsync 완료
- 원본 schedule digest, operation id, identity binding, 복구 권한을 manifest에 고정
- 모든 planned Flow가 `30..60`이고 범위 밖 값이나 Feed 슬롯이 없음
- 두 장비 `NowTime` 차이를 first write 전에 측정하고, 승인된 clock-skew gate `<=30초`와
  device-local `T±60초` 제외창이 실제 차이를 덮음
- 앱과 HA 직접 통합은 operation 전체 구간에 미실행
- 설정 파일 값만 보지 않고 실제 write-enabled 프로세스와 controller 부재를 확인
- qualification은 같은 physical binding과 restore plan의 최신 2/2 영수증에서 **만료 시각만**
  이번 단회에 한해 무시; 사용할 qualification operation의 opaque id를 manifest에 사전 고정하고
  2/2 존재를 확인하며, 영수증 부재·identity·operation 불일치는 우회 금지
- `boundary_time + 540초 < 23:59`를 만족하지 않는 늦은 시간대에는 시작하지 않음. 코드
  validator의 `+310초`는 필요조건일 뿐이며, 이 계획의 전체 post 관측창이 B 슬롯 종료를 넘지
  않아야 함

현재 baseline에 보존된 latent manual `Flow=89`는 `TimerON=false`에서 노출될 수 있습니다.
따라서 `TimerOFF`만 단독 전송하지 않습니다. 기존 스케줄 정지는 아래 값을 **하나의 승인된
control frame**으로 적용하는 합성 pause여야 합니다.

```text
TimerON=false + independent + Constant + safe Flow + safe Frequency
```

이 합성 pause가 불가능하면 실행하지 않습니다.

## 6. 단회 실행 순서

### 첫 write 이후 관측 완료 원칙

**장비에 첫 write가 한 번이라도 전송된 뒤에는 예정된 15분 관측이 끝나기 전에 진단 결과만으로
원복하지 않습니다.** 다음 현상은 **실제·보고 출력이 승인된 `30..60` 안에 있는 한** 실패 또는
이상 sample로 보존하되 조기 종료 사유가 아닙니다.

- `Mode`, `Flow`, `Frequency`, `Linkage`, `TimerON`, Auto tuple이 예상과 다름
- 역할이나 여러 필드가 비원자적으로 늦게 수렴함
- 단일 또는 반복 read timeout, pair gap 증가, stale/invalid sample
- master와 slave의 경계 시각이 어긋남
- 임시 스케줄이나 역할이 일부만 적용됐지만 실제 출력은 승인된 `30..60` 안에 있음

아래 다섯 중단 조건은 이 원칙보다 항상 우선합니다. 첫 write 이후 즉시 ordered recovery로
전환할 수 있는 조건은 다음 다섯 가지뿐입니다.

1. physical identity binding 불일치
2. 실제 또는 보고된 출력이 승인 상한 `60`을 초과
3. 펌프·수조에서 위험한 물리 동작이 관찰됨
4. 복구 권한·journal·현장 차단 수단을 잃어 계속 운전하는 것이 더 위험함
5. 현장 감시자 또는 사용자의 명시적 비상 정지

실험 가설과 맞지 않는다는 이유, 오류 코드가 발생했다는 이유, PASS 가능성이 낮아졌다는 이유로는
중간 원복하지 않습니다. 예정된 종료 시각까지 실제 상태를 계속 수집한 뒤 Phase 4에서 한 번만
원복합니다.

### Phase 0 — exact snapshot과 journal

1. fresh session으로 두 장비의 identity를 다시 확인합니다.
2. `SwitchON`, `TimerON`, `Linkage`, `Mode`, `Flow`, `Frequency`와 active Auto tuple을 보존합니다.
3. 두 452-byte raw state와 두 432-byte schedule image를 보존하고 digest를 계산합니다.
4. 복구 순서와 원본 값을 durable journal에 fsync합니다.
5. journal 3종과 emergency latch 부재, 두 장비 `NowTime` 차이를 확인합니다.
6. observer와 일반 데몬을 중지하고 operation lease를 획득합니다.

### Phase 1 — 기존 스케줄 정지

1. slave에는 `TimerOFF + independent + Constant + Flow 35 + safe Frequency`, master에는
   `TimerOFF + independent + Constant + Flow 30 + safe Frequency`인 합성 pause frame을 각각
   정확히 한 번 보냅니다.
2. 두 장비가 `TimerOFF + independent + safe Constant`인지 fresh reply로 확인합니다.
3. 이 단계에서 latent `Flow=89`가 활성값으로 나타나거나 승인 범위 `60`을 넘으면 즉시 중단하고
   ordered recovery만 수행합니다.

### Phase 2 — 판별용 스케줄 stage

1. §3의 master 2-slot image를 한 번 씁니다.
2. §3의 slave 2-slot image를 한 번 씁니다.
3. fresh explicit reply에서 두 image와 각 digest를 확인합니다.
4. 두 장비의 `TimerON`을 arm하고 A 슬롯이 활성 상태인지 확인합니다.
5. master에 `master`, slave에 `async_slave` 역할을 각각 한 번 적용합니다.
6. 역할, TimerON, schedule digest, active A tuple이 모두 고정 계획과 일치하는지 확인합니다.

`boundary_time`은 단순히 프로세스 시작 6분 뒤가 아닙니다. run 시작 뒤 setup 예산 최대 150초와
역할 관측 시작 뒤 약 360초 lead를 함께 확보하도록 절대 device-local 시각으로 산정합니다.

여기서 생긴 진단 read 오류나 예상 상태 불일치는 곧바로 원복할 이유가 아닙니다. 첫 write 이후
관측 완료 원칙에 따라 실제 출력이 승인 범위 안이면 계획된 종료까지 계속 관측합니다.

### Phase 3 — 15분 경계 관측

- 기존 write 하네스의 best-effort verification interval: 2초
- 전체 observation epoch: 900초
- A/B 경계: epoch 시작 뒤 약 6분
- host 시각만으로 동시 경계를 가정하지 않고 매 sample의 두 장비 `NowTime`을 사용
- 한 장비라도 `T-60초` 안에 들어오면 pre 판정 종료
- 두 장비 모두 자기 `NowTime` 기준 `T+60초`를 지난 뒤에만 post 판정 시작
- 유효한 경계 전 sample: 최소 10 pair이면서 최소 120초
- 유효한 경계 후 sample: 최소 10 pair이면서 300초 이상 안정
- 상충하는 유효 sample: 0
- read timeout, 역할 불일치, 부분 적용, 예상 밖 Mode·Flow를 모두 기록하되 §첫 write 이후
  관측 완료 원칙의 다섯 중단 조건이 없으면 900초 끝까지 관측

read가 지속적으로 실패하면 소프트웨어의 출력 상한 감시도 불가능해집니다. 그 구간은 operation
전체에 배치된 현장 감시자와 즉시 물리 차단 수단이 통제하며, 둘 중 하나라도 사라지면 중단 조건
4 또는 5로 ordered recovery를 시작합니다.

모든 sample에서 다음을 함께 보존합니다.

```text
각 장비의 UTC/monotonic 시작·종료 시각과 pair gap
Linkage, TimerON, SwitchON
Mode, Flow, Frequency
AutoMode, AutoFlow, AutoFreq
전체 schedule image digest와 device NowTime
explicit reply action을 포함한 원본 frame
```

`Mode`/`AutoMode`가 실제 출력 Mode를 뜻한다고 미리 가정하지 않습니다. master의 `Sine`과
`Constant`를 slave가 실제로 따르는지는 protocol 판정과 별개의 물리 claim입니다. 현장 영상이나
육안 관측은 보조 증거로 남기되 물리 계측의 대체물로 사용하지 않습니다.

### Phase 4 — 무조건 exact restore

관측 성공·실패와 관계없이 다음 순서를 사용합니다.

1. slave role detach
2. master role detach
3. Phase 1과 같은 안전 manual 값과 `TimerOFF + independent + Constant`를 한 frame으로 적용
4. slave, master의 원본 432-byte schedule image 복원
5. 각 장비의 원 `SwitchON`, `TimerON`, `Linkage`, `Mode`, `Flow`, `Frequency` 복원
6. writer 프로세스를 완전히 종료한 뒤, 서로 독립된 fresh session에서 outer controls와
   schedule digest를 두 번 확인
7. journal이 terminal 상태인지 확인한 뒤 lease 해제
8. recovery supervisor와 read-only observer 재기동 및 최종 상태 확인

durable journal은 6단계 독립 검증이 통과할 때까지 유지합니다. writer는 write-side restore 완료
후 terminal evidence를 fsync한 뒤 종료하되 journal을 지우지 않으며, journal 정리와 lease
해제는 6단계 통과를 확인한 7단계에서만 수행합니다. 6단계에서 불일치가 나오면 journal을
유지한 채 `RECOVERY_REQUIRED`로 중단하고, 추가 write·재시도 없이 on-site hardware approver와
repository maintainer 모두에게 보고합니다. 에이전트가 복원 성공으로 승격하거나 journal을
임의로 정리하지 않습니다.

복구 중 응답이 불명확하다고 같은 write를 반복하지 않습니다. ordered recovery의 다음 승인된
단계만 수행하고, terminal 복구가 확인되지 않으면 신규 실험을 금지합니다.

setup이 예산 150초를 넘더라도 900초 observation epoch는 줄이지 않습니다. outer 1200초 창의
restore 여유가 줄어드는 경우 Phase 4의 write-side restore를 먼저 완료하고, 6단계의 독립
fresh-session 2회 확인은 outer 창 밖에서 계속 수행합니다.

write-free collector의 30초 cadence와 약 11초 pair gap은 이 write 하네스의 수치로 재사용하지
않습니다. 이번 artifact에 실제 장비별 read 시작·완료 시각과 pair gap을 남기고 그 값으로만
실행 후 timing claim을 판정합니다.

유효 sample은 fresh explicit reply, identity·liveness, role, TimerON, schedule digest 불변,
사전 고정 freshness와 pair-gap 조건을 모두 충족해야 합니다. 거부된 sample도 원본과 거부 사유를
버리지 않고 보존합니다. `AutoFreq`는 결과 Mode가 `Sine`인 경우에만 보조 판별에 사용하고,
`Constant` 구간에서는 배타 판정에 사용하지 않습니다.

## 7. 배타 판정표

### 7.1 protocol 층 무효 게이트

아래 조건은 위에서부터 평가하고, 하나라도 해당하면 아래 분류표를 보지 않습니다.

이 무효 게이트들은 **실행 후 판정 결과**이지 조기 원복 사유가 아닙니다. 게이트에 걸려도
§첫 write 이후 관측 완료 원칙의 다섯 중단 조건이 없으면 900초 관측을 완주한 뒤 Phase 4에서
한 번만 원복합니다.

| 조건 | 판정 |
|---|---|
| identity 불일치, explicit reply 아님, decode 실패, 역할 불변식 손실, epoch 중 schedule digest 변경, 복구 미완 | `UNKNOWN — invalid run` |
| 역할 write 후 slave 432-byte image에 staged 슬롯이 없음 | `UNKNOWN — 시험 조건 구성 불가` |
| master Auto tuple이 `Sine/50 -> Constant/55`로 전환되지 않음 | `UNKNOWN — 경계 미발생` |
| slave Auto tuple이 자기 `NowTime T+60초`까지 수렴하지 않거나 post 창 상충 sample 존재 | `UNKNOWN — timing failure` |

### 7.2 protocol 층 배타 분류

이 표는 `goal_tier: accepted`에 한정하며 장비가 보고한 `AutoMode`와 `AutoFlow`의 출처를
판정합니다.

| Slave `AutoMode` | Slave `AutoFlow` | 분류 | 판정 |
|---|---|---|---|
| `Sine -> Constant` (master) | `45 -> 60` (자기 슬롯) | `MASTER_MODE_OWN_FLOW` | **YES — native split 지원** |
| `Constant -> Sine` (자기 슬롯) | `45 -> 60` | `OWN_SCHEDULE` | **NO — 독립 실행** |
| `Sine -> Constant` | `50 -> 55` (master) | `FULL_MASTER_FOLLOW` | **NO — 전면 추종** |
| 임의 | `35 -> 35` (manual/common) | `COMMON_MANUAL_FLOW` | **NO — 공통 Flow 고정** |
| 임의 | `45 -> 45` | `A_SLOT_HOLD` | **NO — B슬롯 비적용** |
| `Constant -> Sine` | `50 -> 55` | `REVERSE_SPLIT` | `UNKNOWN — 예상 밖` |
| 그 외 | 그 외 | `UNEXPECTED` | `UNKNOWN` |

### 7.3 물리 층

protocol 판정은 실제 모터 파형·유량을 증명하지 않습니다. slave가 실제로 master의
`Sine/Constant` 파형을 냈는지와 실제 유량·위상은 현재 계측 수단으로 항상 `UNKNOWN`입니다.
현장 영상·육안은 등급 (c) 보조 증거로만 남깁니다. 물리 관측이 없어도 §7.1~7.2 판정은
성립하지만, protocol과 정면으로 모순되는 물리 관측이 있으면 실행 전체를 `UNKNOWN`으로 내립니다.

첫 실기가 명확한 `YES`가 아니면 같은 ASYNC 실기를 반복하지 않습니다. 여기서 반복은 첫 write
뒤 900초 epoch에 진입한 operation을 뜻합니다. 첫 write 전 `NO-GO`도 정보 0 실행으로 기록하며,
그 실행이 2회 연속이면 `AGENTS.md` §7에 따라 즉시 정지·보고합니다. 결과를 먼저 append-only
기록으로 커밋한 뒤 조건부 대조를 최대 하나만 별도 승인·별도 run으로 설계합니다.

- `OWN_SCHEDULE`이면 `sync_slave` 대조
- `FULL_MASTER_FOLLOW`, `COMMON_MANUAL_FLOW`, `A_SLOT_HOLD`이면 `independent` 대조

## 8. 결과별 구현 방향

### YES — native split 지원

`jebao-flowd`는 master와 slave에 같은 경계 시각을 배포하되 master 슬롯은 Mode/timing을,
slave 슬롯은 per-mode Flow를 소유하도록 컴파일할 수 있습니다. 경계마다 실시간 Flow write를
보내지 않아도 되므로 명령 횟수가 가장 적습니다.

### NO — common slave Flow 또는 full master follow

master의 active schedule 전환을 데몬이 감지해 slave `Flow`를 `45 -> 60`으로 정확히 한 번
바꾸는 software actuator가 필요합니다. 이 후속 시험은 native 판정과 섞지 않고 별도 트랙에서
다룹니다. 이 경로는 parked Q1(manual Flow 독립 유지)이 참이어야 성립합니다. Q1을 재개하지
않으려면 모든 펌프를 `independent`로 두는 software group이 기본 대안입니다. 어느 경우든 durable
journal, 중복 억제, 최소 명령 간격, 전환 지연 한계를 먼저 정의해야 합니다.

### NO — slave own schedule 실행

선택지는 두 가지입니다.

1. 두 장비에 정렬된 독립 스케줄을 배포하고 clock drift를 감시
2. native ASYNC를 쓰지 않고 데몬이 slave Mode와 Flow를 함께 제어

물리 위상 정렬과 장기 clock drift를 측정하기 전에는 첫 번째 방식을 native sync로 부르지
않습니다.

## 9. 실행 기록과 완료 조건

- 이 계획과 판정표를 실기 전에 커밋합니다.
- 실기 1회는 새 `docs/runs/YYYY-MM-DD-<run-id>.md` 하나로 기록합니다.
- 기존 `q2-attempt-05`는 삭제하거나 덮어쓰지 않고 파일 하단 `## 정정`에 해석 범위 축소를
  append합니다.
- `AGENTS.md`, `docs/hardware-readiness.md`, 앱 분석 문서의 기존 Q2 `YES` 표현도 신규 사실과
  모순되지 않게 함께 정정합니다.
- raw artifact와 구조화 artifact는 private 보존하고 공개 문서에는 opaque id, UTC span,
  identity binding digest, artifact digest만 기록합니다.
- **900초 관측 완료 + 배타 판정 + byte-exact restore + terminal journal**을 모두 확인해야 한
  번의 실기가 완료됩니다.
- 결과가 `UNKNOWN`이어도 새 raw가 가설을 줄였으면 정보 있는 실행으로 기록합니다. 정보 0 실행이
  2회 연속이면 코드를 더 고치거나 반복하지 않고 `AGENTS.md` §7에 따라 정지·보고합니다.
- 이번 run 기록이 커밋되기 전에는 결과별 후속 actuator나 대조를 위한 `src/` 변경을 시작하지
  않습니다.
