# Native ASYNC slave 슬롯별 Flow 앱-sequence 교정 단회 계획

## 닫을 질문

동일한 temporary schedule과 경계를 유지하되 slave 역할 진입을 cached Pro 앱 UI와 같은
`independent -> sync_slave -> async_slave` 순서로 수행하고, 매 판정 pair를 새 인증 세션에서
취득했을 때 master 경계 뒤 slave가 자기 B slot의 `Flow=47`을 적용하는가?

이 실행은 [2026-09-02 Q2-slotflow 실행](runs/2026-09-02-q2-slotflow-a00f4b1.md)의
두 condition-construction gap만 제거한다.

1. 이전 하네스의 direct `independent -> async_slave`를 앱 UI의 `0 -> 2 -> 3` 순서로 바꾼다.
2. 한 재사용 session에 누적된 action `0x04` backlog 대신, 각 판정 pair를 새 transport에서
   취득한다.

Mode 소유권, Q1 manual Flow, 다른 모델·펌웨어, 물리 유량·파형은 이번 범위가 아니다.

이 operation은 직전 UNKNOWN의 자동 재시도가 아니다. 직전 raw가 same-session backlog를
정량적으로 확인했고, 사후 앱 trace가 role sequence 차이를 확인한 뒤 repository maintainer가
그 두 차이를 제거한 실기와 exact restore를 다시 명시적으로 요청했다. 대안은 Q2-slotflow를
UNKNOWN으로 park하고 software-independent 제품 트랙을 진행하는 것이며, 그 대안은 이번
실행 결과와 무관하게 계속 가능하다. 보존 raw만으로는 경계 후 slave 실제 상태가 없어서 질문을
닫을 수 없으므로, 위 두 조건만 바꾼 단회가 남은 최소 조작이다.

## 고정 schedule과 판정

schedule, safe manual, 경계·epoch와 출력 상한은 직전 실행에서 바꾸지 않는다.

- master safe manual: `Constant / Flow 30 / Frequency 20`
- slave safe manual: `Constant / Flow 35 / Frequency 20`
- master A=`Sine / Flow 40 / Frequency 50`
- master B=`Constant / Flow 35 / wire Frequency 0`
- slave A=`Sine / Flow 35 / Frequency 50`
- slave B=`Constant / Flow 47 / wire Frequency 0`
- 양쪽 A/B 경계: 같은 device-local 절대 시각
- complete observation epoch: `900초`
- boundary exclusion: device-local `T-60초`부터 `T+60초`까지
- post-boundary 안정: 최소 `300초`
- temporary schedule·native role authority 아래 guarded Flow 상한: `47`

### PASS

master의 stable `Sine/40/F50 -> Constant/35` 경계가 확인되고, 같은 boundary side의 fresh
slave frame에서 stable `Sine/35/F50 -> Constant/47`이 300초 이상 유지되며 상충하는 valid
pair가 없고, 종료 뒤 원 controls와 두 schedule image가 byte-exact 복원된다.

### FAIL

master의 위 경계와 유효한 post-boundary slave frame이 모두 있는데 slave가 `Constant/47`이
아닌 stable 상태를 보인다. `35` 고정, master Flow 추종, 다른 stable Flow는 원인 이름과 무관하게
이 질문에는 FAIL이다.

### UNKNOWN

경계 후 fresh slave frame 부재, decode 실패, 유효 pair·안정 시간 부족, 상충 sample, 또는
복원 검증 미완료로 PASS/FAIL을 만들 수 없다. UNKNOWN은 같은 operation을 재시도할 권한이 아니다.

## 앱과 같은 역할 진입

cached Pro template의 정적 `U` 추적에서 independent 상태의 Slave 선택은 partial
`{Linkage: 2}`를 보내고, 그 상태에서 Async 선택은 별도 partial `{Linkage: 3}`을 보낸다.
Flow·Timer·Mode·Frequency·schedule은 두 role payload에 합성되지 않는다. 이 정적 추적은
장비 raw 등급 (a)/(b)/(c)가 아니며 검사한 template 밖으로 일반화하지 않는다.

교정 실행은 다음 순서를 고정한다.

1. master에 `Linkage=master`를 기존 guarded `write_linkage()`로 정확히 한 번 보낸다.
2. slave role mutation intent와 `sync_intent`를 journal에 fsync한 뒤
   `Linkage=sync_slave`를 기존 `write_linkage()`로 정확히 한 번 보낸다.
3. adapter 반환만으로 다음 write 권한을 만들지 않는다. 새 paired session에서 recognised state
   frame을 장비별 한 번씩 읽어 아래 다섯 조건을 모두 적극 확인한 뒤에만 `sync_verified`를
   fsync한다.
   - physical binding이 그대로이고 양쪽 모두 online·no-error·`SwitchON=true`
   - master `Linkage=master`, slave `Linkage=sync_slave`
   - 양쪽 `TimerON=true`
   - 양쪽 temporary schedule digest가 staging read-back 값과 각각 byte-exact 일치
   - 양쪽 active Flow가 guarded cap `47` 이하
4. 위 확인이 하나라도 실패하거나 불명확하면 `sync_verified`를 만들지 않고 async write도 보내지
   않은 채 ordered rollback으로 간다. 성공한 recognised frame의 action과 raw는 보존한다.
5. `async_intent`를 fsync한 뒤 `Linkage=async_slave`를 같은 기존 API로 정확히 한 번 보낸다.
6. adapter 반환을 `async_returned`로 fsync한 뒤 기존 final-role pair 검증과 epoch로 간다.

각 role control frame은 서로 다른 intended change다. 같은 role frame을 반복하지 않는다.
`write_linkage()` 내부의 ACK-loss 해소는 새 session의 read-only 확인만 허용하며 control frame을
재전송하지 않는 기존 계약을 유지한다. 첫 write의 adapter 반환과 durable successor가 확인되지
않으면 다음 role write를 보내지 않고 ordered rollback으로 간다.

role journal은 새 실행을 `sync_then_async`로 표시하고 아래 canonical progress prefix만 허용한다.

`()` -> `sync_intent` -> `sync_verified` -> `async_intent` -> `async_returned`

crash·불확실 반환 시 recovery가 허용하는 slave 상태는 durable prefix에 맞춰 최소화한다.

- `()`: `independent`
- `sync_intent`: `independent | sync_slave`
- `sync_verified`: `sync_slave`
- `async_intent`: `sync_slave | async_slave`
- `async_returned`: `async_slave`
- detach가 기록된 뒤: `independent`

어느 prefix에서도 recovery는 slave를 먼저 `independent`로 되돌린 뒤 master를 detach하는 기존
역순을 유지한다. 이전 direct-role journal은 기본 `direct` 값으로 계속 읽을 수 있게 하고,
현재 non-terminal intent가 하나라도 있으면 새 image를 실행하지 않는다.

`sync_slave`는 이 pair에서 처음 밟는 중간 role이므로 role-induced manual DP 변화가 없다고
가정하지 않는다. 그런 변화나 중간 확인 실패가 있으면 관측값을 남기고 async로 진행하지 않는다.
기존 role-only exact detach가 부수 control drift 때문에 terminal이 되지 못하면 새 복구 write를
만들지 않고, composed owner의 기존 audited external-disarm 경로가 두 장비를 safe
`TimerOFF + independent` 합성 frame으로 만들고 role journal을 그 fresh proof로 닫은 뒤 schedule과
outer controls를 순서대로 복원한다. 이 예외는 이 fixed `sync_then_async` operation의 소유된 outer
journal과 temporary schedule이 함께 있을 때만 사용할 수 있다.

이 slave 세부 progress는 기존 `linkage_write_intent_device_ids`·`linked_device_ids`의 master-first
저널을 대체하지 않고 그 안에 합성된다. master intent·write·검증 progress는 기존 그대로이며,
slave의 첫 physical role write 전에는 기존 slave intent와 `sync_intent`가 함께 durable해야 한다.
`sync_verified`는 `write_linkage(sync_slave)`가 target 일치를 확인하고 반환한 뒤 위 별도 fresh
pair read-back까지 통과한 경우에만 기록한다. 현재 JSON store는 Pydantic record 전체를
`model_dump_json()`/`model_validate_json()`으로 보존하므로 persistence module의 schema나 write
경로는 바꾸지 않고, 새 필드의 기본값으로 기존 direct journal을 읽는다.

## fresh 판정 frame 계약

직전 실행은 slave report 생성률 약 `0.98 frame/s`, 같은 session 소비율 약 `0.46 frame/s`여서
device-local 지연이 `16.7초 -> 491.1초`로 발산했다. 같은 session에서 epoch만 늘리는 방식은
사용하지 않는다.

fixed monitor의 각 pair acquisition은 다음 순서다.

1. stop·safety·deadline authority 아래 master와 slave의 기존 session을 모두 disconnect한다.
2. 두 장비 모두 새 session object로 connect·authenticate가 끝난 뒤에만 읽기를 시작한다.
3. 장비별 `get_report_capable_state_capture()`를 정확히 한 번 호출해 선택된 action `0x03` 또는
   `0x04`의 exact frame과 그 frame에서 decode한 state를 함께 보존한다.
4. 다음 pair는 다시 1번부터 시작한다. 한 session에서 두 번째 판정 read를 하지 않는다.

두 participant read 중 하나가 실패해도 이미 성공한 형제 raw를 버리지 않는다. 둘을
`return_exceptions` 형태로 끝까지 회수해 성공 frame은 sink에 남기고, pair 자체만 판정에서
제외한다. 이것은 추가 read나 재시도가 아니다.

paired refresh 중 cancellation이나 한쪽 실패는 half-refreshed transport를 남기지 않도록 기존
uninterruptible paired-boundary 계약을 사용한다. refresh는 read-only이며 write를 만들지 않는다.
ordered rollback은 in-flight paired refresh가 이 계약에 따라 두 participant의 disconnect/connect
경계를 모두 끝낸 뒤에만 시작한다.

이번 fixed monitor에는 다음 세 상수를 고정한다.

- acquisition이 끝난 뒤 다음 acquisition 시작 전 최소 pause: `10초`
- paired refresh와 두 capture 전체의 acquisition authority deadline: `8초`
- 연속 acquisition failure 상한: `3회`

같은 ordinal 안의 transport retry는 제거한다. 실패한 pair는 그 ordinal의 진단·성공한 형제 raw만
남기고 10초 뒤 다음 scheduled ordinal로 간다. 따라서 900초 동안 첫 즉시 acquisition을 포함해
paired refresh는 최대 91회, device connect/auth는 최대 182회다. 정상 acquisition 사이 active-Flow 점검 간격은 최대 약
`18초`다. 8초 deadline cancellation이 uninterruptible refresh 중 들어오면 기존 component
timeout(connect/close와 두 auth exchange)까지 paired boundary 완료를 기다리므로 한 실패 attempt의
wall time은 보수적으로 최대 `20초`, 다음 시작까지 최대 `30초`다. 세 번 연속 실패하면 마지막
valid sample 뒤 최대 약 `90초` 안에 새 연결 시도를 끝내고 epoch를 `UNKNOWN`으로 종료한 뒤 정상
ordered rollback으로 간다. 이것은 동일 write 재시도나 여섯 번째 safety abort 조건이 아니라
미검증 연결 폭주를 막는 measurement 종료다.

정상 점검 간격은 최대 약 `18초`이고, refresh 중 cancellation·연속 실패가 겹치면 마지막 valid
sample 이후 최대 약 `90초` 동안 소프트웨어 조기 recovery 조건 2의 Flow 상한을 새 sample로
평가하지 못할 수 있다. 그 구간의 방어 주체는 현장 감시자다. repository maintainer 겸 on-site
hardware approver는 물리 차단 수단의 구체적 열거를 면제하고 현장에 수습 가능한 인원이 있다고
확인했다. 이 operation별 결정은 물리 전원 차단 체크박스를 충족으로 바꾸지 않으며, 첫 hardware
write 전 유효한 `docs/hardware-readiness.md`의 물리 차단 확인 조건은 별도로 유지한다.

action `0x04`는 explicit reply나 ACK가 아니다. 다만 새 TCP session이므로 이전 session의 backlog를
물려받을 수 없다. deadline 안에 성공한 acquisition의 첫 recognised frame은 그 새 transport에서
최대 `8초`만 머물 수 있으며, 이는 device-local clock 정확도와 별개인 transport staleness 상한이다.
frame 내부 `NowTime`이 staging/TimerON arming 완료 이후이고, `TimerON`, `Linkage`, temporary schedule
digest가 함께 맞아야 판정 자격을 가진다. master와 slave frame이 모두 같은 boundary exclusion
side일 때만 pair로 묶는다. raw sink에는 participant, pair ordinal, action, exact frame digest·길이,
device-local `NowTime`, host read 구간을 남기고 최종 보고에는 `0x03`/`0x04` 구성비를 분리한다.
sync 확인에 쓰는 첫 raw pair ordinal은 monitor 판정 sample로 세지 않고, 그 다음 ordinal부터
boundary monitor가 시작된다는 규칙을 실행 기록에 남긴다.

refresh·capture 오류의 예외 class는 비밀값 없는 진단 필드로 남긴다. reason만 남겨 회차별 transport
class를 잃었던 2026-09-01 실행을 반복하지 않는다.

## write·복원 순서

1. fresh explicit baseline에서 physical binding, 원 controls, `TimerON / independent`, 두
   432-byte schedule image를 fsync하고 offline round-trip·digest를 확인한다.
2. journal 3종과 emergency latch 부재, 다른 writer·Observer·recovery supervisor 부재,
   attended lease 단일 보유, 현장 차단 수단과 approver를 확인한다.
3. 장비별 `SwitchON + TimerOFF + independent + Constant + safe Flow + Frequency 20`을 한
   control frame으로 정확히 한 번 적용한다.
4. 기존 sentinel qualification 뒤 temporary schedule image를 장비별 정확히 한 번 적용하고
   exact image read-back을 확인한다.
5. safe manual을 유지한 채 `TimerON`을 장비별 기존 합성 frame으로 한 번 적용한다.
6. 위 앱-equivalent role sequence를 수행한다.
7. fresh-session pair로 900초를 관측하고 private raw와 durable sample을 보존한다.
8. 결과와 무관하게 slave role detach -> master role detach -> safe TimerOFF 합성 frame ->
   원 432-byte images -> 원 outer controls/TimerON 순서로 복원한다.
9. writer 종료 뒤 서로 다른 두 source-attested fresh collector에서 physical binding, 원 controls와
   두 image digest의 exact 일치를 확인한다.

자동 rollback이 terminal이 아니면 writer 프로세스 종료를 확인하고 attended hardware lease를
확보한 뒤, **이 실행 image의** 기존 `recover_experiment()`를 한 번만 호출한다. 새 실험·임의
재전송은 하지 않는다. 비-terminal intent가 있는 동안 fixed signature나 recovery image를 바꾸지
않는다.

## 조기 ordered recovery 조건

첫 hardware write 뒤 조기 recovery는 기존 다섯 조건뿐이다.

1. physical identity binding 불일치
2. temporary schedule·native role authority 아래 실제 또는 보고된 Flow가 `47` 초과
3. 펌프·수조의 구체적 위험 동작
4. durable journal·복구 권한·현장 물리 차단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

role 중간 read, diagnostic read timeout, action `0x04`, 안전 범위 안의 예상 밖 tuple은 증거이며
동일 write 재전송 사유가 아니다. 다만 sync 중간의 다섯 가지 적극 확인 실패는 다음 async write를
허용하지 않으며, monitor 연속 acquisition 3회 실패는 위 connection-budget 종료 규칙으로
`UNKNOWN` rollback을 시작한다.

## 최소 구현 범위

고정 schedule·classifier·CLI·LAN read/write API와 rollback 구현은 바꾸지 않는다. 허용할 source는
다음으로 제한한다.

- `src/jebao_flow/devices/schedule_linkage.py`
  - fixed Q2에만 `sync_then_async` durable progress와 recovery topology 추가
  - sync read-back에서 master/slave role과 slave Timer·schedule digest·active cap을 적극 확인
  - fixed monitor pair마다 기존 paired session refresh를 먼저 수행
  - 한쪽 capture 실패에도 성공한 형제 raw 보존
  - fixed monitor에 10초 pause·8초 acquisition deadline·연속 실패 3회 상한을 적용하고 같은
    ordinal transport retry 제거
  - monitor diagnostic에 allow-listed exception class 추가
- 위 변경을 직접 검증하는 `tests/unit/test_schedule_linkage_transaction.py`
- outer persisted event와 CLI serialization의 기존 계약이 enum 추가로 영향을 받는 경우에만
  직접 대응하는 `tests/unit/test_schedule_flow_experiment.py`와
  `tests/unit/test_schedule_flow_experiment_cli.py`

`schedule_flow_experiment.py`, `schedule_transaction.py`, `linkage.py`, `lan.py`, 두 CLI source,
일반 daemon·MQTT 경로, schedule signature·출력 limits, rollback write path는 변경하지 않는다.
새 범용 CLI·새 pre-write 펌웨어 가설 gate·fake firmware knob를 추가하지 않는다.

## 종료

source commit과 exact Linux/amd64 image를 Claude가 read-only 검토하고 전체 suite가 통과한 뒤에도
실제 write는 on-site hardware approver가 구체적 물리 차단 수단을 확인해야만 시작한다.

한 operation의 900초 epoch, terminal restore, 서로 다른 두 fresh collector exact 검증,
append-only `docs/runs/` 기록 커밋이 끝나면 단회 해제는 자동 소진되고 동결이 다시 적용된다.
복구가 남으면 새 실험을 금지하고 그 operation의 ordered recovery와 read-only 검증만 허용한다.
