# Native Sync schedule 소유권 최소 단회 계획

## 상태

이 문서는 계획만 고정합니다. 이 문서의 작성·커밋은 `AGENTS.md` §1 동결을 해제하지 않고,
장비 write 권한도 만들지 않습니다. 실기 재개에는 repository maintainer의 새 명시 결정,
별도 단회 해제 커밋, on-site hardware approver의 operation별 승인이 모두 필요합니다.

Home Assistant는 재시작하거나 설정·서비스를 조작하지 않습니다. 실험 writer와 잠긴 recovery
runtime 외의 로컬 writer도 실행하지 않습니다.

## 닫을 질문

두 Pro가 `master`와 `sync_slave`로 지정되고 각자 서로 다른 432-byte schedule image를 보유할 때,
slave가 runtime에 보고하는 schedule content(`Mode / AutoFlow / AutoFreq`)와 전이 시각은 다음 네
조합 중 어느 것인가?

1. master timing + master content
2. master timing + slave content
3. slave timing + master content
4. slave timing + slave content

content와 timing은 별도 축으로 판정합니다. runtime durable verdict는 경계 전·후의 content
ownership만 기록하고, 두 경계 사이의 timing×content 조합은 보존 raw에 사전 고정 규칙을 적용해
offline으로 도출합니다. 어느 축도 boolean PASS/FAIL로 축약하지 않습니다.

물리 유량·파형·위상, Q1 manual Flow 장기 유지, Async 역할, 다른 firmware·장비 pair는 범위 밖입니다.

## 지금까지의 증거와 write-free 분석 한계

- cached Pro template 세 본에서 role 선택은 현재 화면의 장비에 partial `{Linkage: n}`만
  `sendCmd`합니다. master id, peer id, pairing·handshake, group id, schedule owner/reference,
  `AutoTimeNN`은 role payload에 없습니다.
- 각 `AutoTime00..47`은 현재 화면에 binding된 한 장비의 store에만 저장됩니다. 앱 코드에는
  master image를 slave에 복사하는 경로가 없습니다.
- `TimerON`을 먼저 arm하고 Linkage 화면의 Slave parent를 누르는 순서는 앱에서 도달 가능하며,
  `{Linkage:2}` 한 key만 전송합니다. 이미 slave인 상태에서 schedule timer를 토글할 때
  `Linkage:0`을 합성하는 다른 순서와 혼동하지 않습니다.
- [`2026-09-02 Sync-gate 실행`](runs/2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9.md)의
  fresh raw는 master=`master`, slave=`sync_slave`, 양쪽 `TimerON`, 서로 다른 staged image digest를
  확인했습니다. role write가 slave image를 복사·덮어쓰지 않았습니다.
- 같은 raw에서 slave manual과 Auto tuple이 모두 `Sine/35/F50`이어서 slave A-slot 실행과
  manual-register mirror를 구분할 수 없었습니다. `Flow 35`도 safe manual과 A slot이 우연히
  같았으므로 power mirror의 부재를 증명하지 않습니다.
- 2026-08-30 attempt 05의 비원자 sample은 device-local `NowTime`을 durable schema에 보존하지
  않았습니다. host read 순서와 허용 clock skew를 분리할 수 없으므로 당시 raw에서 timing
  ownership을 복원하는 write-free 경로는 구조적으로 닫혀 있습니다.
- 완료된 나머지 실행은 양쪽 Mode·경계가 같거나 slave raw가 0개라 runtime ownership을 판별할
  sample이 없습니다.

앱 bundle은 앱 command surface에 대한 정적 증거이며 firmware runtime 증거가 아닙니다. datapoint
schema도 runtime host fetch이므로 “pair/owner key가 없다”는 결론은 검사한 template이 읽고 쓰는
범위에 한정합니다. firmware 내부 peer discovery와 native host group 동작은 부정하지 않습니다.

## 앱 wire-equivalent 역할 진입

이번 operation은 다음 순서만 사용합니다.

1. 각 장비에 safe manual과 자기 temporary schedule을 stage하고 exact read-back한다.
2. 양쪽 `TimerON`을 arm한다.
3. master에 partial `Linkage=master`를 정확히 한 번 쓴다.
4. slave에 partial `Linkage=sync_slave`를 정확히 한 번 쓴다.
5. `async_slave` write는 0회다.

4번은 cached Pro 앱의 Slave parent와 같은 wire action입니다. 이 순서로 `TimerON=true`와
`sync_slave`가 함께 유지되는 것도 앞선 fresh raw에서 확인했습니다. 이미 slave인 장비의 timer
toggle을 앱처럼 다시 누르면 `Linkage=0`이 합성돼 측정 대상이 사라지므로 그렇게 “교정”하지
않습니다.

## 고정 값과 provenance

새 값을 발명하지 않고 실제 write·restore 선례가 있는 저출력 값만 씁니다.

| participant | safe manual | A slot | B slot | boundary |
|---|---|---|---|---|
| master | `Constant / Flow 30 / F20` | `Sine / Flow 35 / F30` | `Constant / Flow 30 / wire F0` | `T` |
| slave | `Constant / Flow 31 / F20` | `Constant / Flow 32 / wire F0` | `Sine / Flow 40 / F30` | `T+5분` |

- Flow write 집합은 `{30,31,32,35,40}`이고 guarded maximum은 `41`입니다. 계획 최고값 40보다
  1 높은 진단 여유를 두되 직전 cap 47보다 낮춥니다.
- frequency write 집합은 `{0,20,30}`입니다. 두 Sine entry는 기존 단일 `sine_frequency`
  필드를 함께 쓰므로 새 persisted frequency field를 만들지 않습니다. F30은
  [`2026-08-30 attempt 05`](runs/2026-08-30-q2-attempt-05.md)에 사용·복원된 Sine
  frequency입니다.
- Constant slot의 wire `F0`이 장비 보고에서 `AutoFreq=5`로 보인 선례는
  [`2026-09-01 corrected run`](runs/2026-09-01-q2-target-corrected-a3adb738.md)에 있습니다.
  `0 | 5` 차이는 진단으로 보존하되 content attribution을 뒤집지 않습니다.
- slave safe manual Flow 31은 A slot Flow 32와 다릅니다. Sync 진입이 full tuple을 manual DP에
  mirror하면 `POWER` drift로 관측되고, Mode/Frequency만 mirror하면 power 31은 유지됩니다.
  어느 경우도 검사를 완화해 숨기지 않습니다.

## 경계·epoch 산술

분 단위 schedule과 실제 `NowTime` 보고 양자화를 그대로 사용합니다.

- master boundary `T`: preflight clock의 다음 정시 분 + `7분`
- slave boundary: `T+5분`
- `_next_boundary` lead 범위: `360초 < lead <= 420초`
- execution 직전 minimum lead: `380초`
- `ScheduleFlowExperimentSpec.minimum_lead_seconds`: 기존 `60초` 유지. 위 `380초` CLI
  execution gate와 역할이 다른 필드입니다.
- observation window: outer `915초`, reserve `15초`, complete epoch `900초`
- boundary exclusion: 각 장비 자기 경계의 `[-60초, +60초]`
- W0 runtime baseline: 기존 fixed 계약대로 같은 evidence 10 pair 이상, host monotonic span
  120초 이상, conflict 0
- W1 offline·W2 runtime evidence: 같은 label 2 pair 이상, host monotonic span 30초 이상,
  conflict 0

분류 조건이 strict `< -60` / `> +60`이고 device clock이 분 단위이므로 clean sample은 경계에서
최소 2분 떨어져야 합니다. `T`와 `T+5분`은 다음 clean window를 만듭니다.

- W0: master와 slave가 각각 자기 경계보다 최소 2분 전
- W1: master는 `T+2분` 이후, slave는 `T+5분-2분` 이전. clean minute가 두 개입니다.
- W2: slave의 경계 `T+5분+2분` 이후

W0에는 10 pair의 9개 acquisition interval과 120초 span 때문에 monitor 시작 기준
`120~162초`가 필요합니다. 2026-09-02 실측 staging은 run 시작부터 role write까지 약 80초였고,
run minimum lead 380초이면 role 시점에 약 300초, W0 clean 구간 약 180초가 남습니다. 반대편 W2는
role-anchor remaining이 450초 이하여야 30초 안정까지 900초 안에 들어오며, +7분 선택과 staging은
그 상한 안에 있습니다. 즉 목표 role-anchor 대역은 약 `300~420초`이고 W0 하한과 W2 상한을 함께
만족합니다. 정수 분 설계의 `lead minutes 7 + offset minutes 5 = 12`가 W2 상한을 지키고,
CLI lead floor 380초가 실측 staging 뒤 W0 하한을 지키는 거울 구조입니다.

fresh execution check에서 lead가 380초 미만이면 첫 write 전에 정상 중단하고 새 boundary를
선택합니다. 이것을 장비 결과로 기록하지 않습니다. 최악 initial lead 420초에서도 W2 첫 clean
minute는 epoch 시작 뒤 최대 840초 부근이고 deadline까지 60초, 30초 안정 뒤에도 30초가 남습니다.
별도 role-anchor pre-write gate를 새로 만들지 않습니다.

## raw pair 자격

한 evidence pair는 다음을 모두 만족해야 합니다.

- 같은 acquisition ordinal의 master·slave fresh capture이고 acquisition authority가 8초 이내
- physical binding, online/no-error, `SwitchON`, 양쪽 `TimerON`, master=`master`,
  slave=`sync_slave`, 장비별 exact staged schedule digest가 일치
- active Flow가 존재하는 정수이고 cap 41 이하
- 두 frame의 device-local `NowTime`이 같은 분
- W0에서는 같은 pair의 master가 exact master A(`Sine / AutoFlow 35 / AutoFreq 30`)를 보고,
  W1과 W2에서는 exact master B(`Constant / AutoFlow 30 / AutoFreq 0|5`)를 보고

fixed observation 경로에서는 pair clock-skew assertion이 우회됩니다. 따라서 `NowTime`이 같은
분이 아닌 pair는 timing·content 증거에서 모두 제외하고 raw와 진단만 남깁니다. 이 규칙은
1분 clock offset이 짧은 W1을 조용히 잘못 라벨링하지 못하게 합니다.

action `0x04`는 explicit reply나 ACK로 부르지 않습니다. `0x03`과 `0x04` 모두 exact frame,
participant, ordinal, host read span, device-local `NowTime`, digest·length를 보존하고 최종 기록에
action 구성비를 분리합니다.

## runtime content 판정

W0와 W2를 각각 판정하고 두 결과가 일치해야 content ownership을 확정합니다.

| window | master content | slave content | manual echo | hold/other |
|---|---|---|---|---|
| W0 | slave=`Sine/35/F30` | slave=`Constant/32/F0|5` | slave=`Constant/31/F20` | 그 밖의 whole tuple 또는 field split |
| W2 | slave=`Constant/30/F0|5` | slave=`Sine/40/F30` | slave=`Constant/31/F20` | slave A 유지 또는 그 밖의 tuple |

기존 named `ScheduleFlowOutcome`을 의미에 맞게 재도출해 사용합니다.

- 양 창에서 master tuple: `FULL_MASTER_FOLLOW`
- 양 창에서 slave 자기 tuple: `OWN_SCHEDULE`
- manual Flow 31 유지: `COMMON_MANUAL_FLOW`
- W2에서 slave A 유지: `A_SLOT_HOLD`
- W0/W2 불일치, field split, bounded unexpected tuple: `UNEXPECTED_EFFECTIVE_STATE`
- 자격 pair·안정 span 부족, decode·transport 실패, conflict, 복원 미확인: `UNKNOWN`

이 결과는 content 축만 답합니다. `FULL_MASTER_FOLLOW`를 timing까지 master가 소유한다는 뜻으로
확장하지 않습니다. `OWN_SCHEDULE`도 protocol-reported `Auto` tuple 귀속을 뜻하며 물리 유량·파형
적용을 증명하지 않습니다. Async 성공 신호로 소비되는 `PER_SLOT_POWER_VERIFIED`는 이 Sync-only
실행에서 절대 만들지 않습니다.

durable `schedule_transition_verified`는 `PER_SLOT_POWER_VERIFIED` 전용 legacy success boolean이므로
`OWN_SCHEDULE` 결과에서도 **False**로 기록합니다. 이는 Sync boundary evidence가 없었다는 뜻이
아니며, named `schedule_flow_outcome=own_schedule`과 stable sample이 이 실행의 content 판정입니다.

## offline timing×content 판정

W1의 보존 raw에 아래 매핑을 적용합니다. 이 문서 commit SHA가 실행 전에 규칙을 고정하며,
run 기록은 그 SHA를 인용합니다. 같은 pair의 master exact B가 없으면 W1 증거가 아닙니다.

| slave W1 whole tuple / AutoFlow | label |
|---|---|
| master B `Constant/30/F0|5` | `MASTER_TIMING_MASTER_CONTENT` |
| slave B `Sine/40/F30` | `MASTER_TIMING_SLAVE_CONTENT` |
| master A `Sine/35/F30` | `SLAVE_TIMING_MASTER_CONTENT` |
| slave A `Constant/32/F0|5` | `SLAVE_TIMING_SLAVE_CONTENT` |
| manual tuple `Constant/31/F20` | `MANUAL_ECHO` |
| Mode·Flow·Frequency가 서로 다른 후보를 가리킴 | `FIELD_SPLIT` |
| 그 밖의 bounded tuple | `UNEXPECTED_EFFECTIVE_STATE` |

단일 `sine_frequency`를 쓰므로 Frequency는 Sine/Constant Mode family만 구분하고 master/slave owner
축으로는 쓰이지 않습니다. owner와 field split의 독립 판별자는 Mode와 Flow 두 축입니다.

한 label은 자격 pair 2개 이상, host monotonic span 30초 이상, conflict 0일 때만 확정합니다.
그 외는 `UNKNOWN`입니다. raw frame은 등급 (a)이지만 label은 “(a) raw에 사전 고정 규칙을
적용한 도출”로 분리해 기록합니다.

Sync 진입이 당시 active slave A tuple을 manual DP에 mirror했다면, W1의 slave A
`Constant/32/F0|5`와 mirror된 manual 반향은 같은 값이어서 구조적으로 구분되지 않습니다.
따라서 `SLAVE_TIMING_SLAVE_CONTENT`는 **protocol report가 slave-side A 값에 머물렀다**는 뜻으로만
읽고, firmware schedule engine 구동과 manual 반향을 구분한 것으로 과대 진술하지 않습니다.

## Sync 진입 검증과 manual mirror

role 진입 후에는 새 비교자를 만들지 않고 기존 audited staged-control assertion
(`allow_staged_control_transition=True`)과 attended active cap을 사용합니다.

- identity, online/no-error, `SwitchON`, `TimerON`, exact linkage, exact schedule digest
- saved safe manual power exact 일치
- Mode는 `constant|sine`, Frequency는 fixed allowlist `{0,5,20,30}`
- complete `AutoMode/AutoFlow/AutoFreq`와 active cap 41

Mode/Frequency mirror는 허용된 값 안에서 raw 진단으로 남습니다. power는 exact 31을 유지하므로
full-tuple mirror라면 `POWER` drift에서 Sync 다음 단계 없이 중단됩니다. 이 중단은 정보가 있는
`UNKNOWN`이며 firmware 동작을 PASS/FAIL로 바꾸지 않습니다.

## durable role·recovery 계약

Sync-only 실행을 기존 `DIRECT`나 `SYNC_THEN_ASYNC`로 가장하지 않고
`ScheduleLinkageSlaveRoleSequence.SYNC_ONLY`를 추가합니다.

- slave role write는 한 번뿐이므로 `slave_role_progress`는 빈 tuple을 유지합니다.
- master-first `linkage_write_intent_device_ids`와 `linked_device_ids`가 기존대로 write 전후를
  증명합니다.
- slave intent가 durable하고 detach 전이면 recovery allowed-set은
  `{independent, sync_slave}`입니다. `async_slave`는 어느 prefix에서도 허용하지 않습니다.
- slave detach가 durable하거나 slave intent가 아직 없으면 `{independent}`만 허용합니다.
- intent-before-write, exactly-once, ACK-loss read-only reconciliation, no-resend, fsync 순서는
  기존 계약을 유지합니다.

recovery는 slave detach -> master detach -> safe `TimerOFF + independent` -> 원 schedule images ->
원 outer controls와 `TimerON` 순서를 유지합니다. non-terminal intent가 있으면 이 operation image로만
attended recovery하고 fixed constants나 recovery image를 바꾸지 않습니다.

원 slave snapshot의 latent manual Flow 89는 temporary authority의 cap 41과 섞지 않습니다. 두
장비가 원래 `ON / TimerON`이므로 기존 rollback은 고출력 manual fallback을 `TimerOFF` 상태로 먼저
노출하지 않고, 원 schedule image 뒤 원 manual controls와 `TimerON`을 한 guarded frame으로 한 번
복원합니다. cap 41 검사는 temporary schedule·role authority 동안만 적용되고 정상 복원 후 원
AutoFlow 범위에는 적용하지 않습니다.

## fresh transport와 write·restore 순서

직전 실행에서 검증한 fresh-session transport를 그대로 사용합니다.

- pair마다 두 session disconnect -> 새 connect/auth -> participant별 report-capable capture 1회
- paired acquisition authority 8초, acquisition 사이 pause 10초, 연속 실패 3회면 `UNKNOWN`
- 같은 ordinal 재시도 없음, 성공한 sibling raw 보존, exception class·refresh/connect 수 보존
- 900초 최대 paired refresh 91회와 device connect/auth 182회에 role 검증 refresh를 별도 합산

write와 restore는 다음 순서입니다.

1. fresh explicit baseline과 identity, 원 controls, 원 role/Timer, 두 image를 fsync하고 offline
   round-trip한다.
2. journal 3종·emergency latch 부재, 다른 writer 부재, attended lease 단일 보유를 확인한다.
3. 장비별 safe `TimerOFF + independent + Constant + manual Flow/F20` control frame을 각각 한 번 쓴다.
4. temporary schedule image를 장비별 한 번 쓰고 exact read-back한다.
5. 양쪽 `TimerON`을 각각 한 번 arm한다.
6. master role 한 번, slave Sync role 한 번을 durable intent 순서로 쓴다.
7. 900초 fresh pair epoch와 private raw sink를 완료한다.
8. 결과와 무관하게 기존 ordered rollback을 완료한다.
9. writer 종료 뒤 서로 다른 두 source-attested fresh collector로 원 controls와 두 432-byte image를
   byte-exact 검증한다.

forward intended change는 장비별 safe control·schedule·TimerON 여섯 건과 두 role write를 합쳐
정확히 여덟 건입니다. restore write는 결과와 무관한 기존 ordered recovery 권한으로 별도
계상합니다.

## 조기 ordered recovery

첫 write 뒤 기존 안전 조건만 유지합니다.

1. physical identity binding 불일치
2. temporary schedule·native role authority 아래 실제 또는 보고된 Flow가 41 초과
3. 펌프·수조의 구체적 위험 동작
4. durable journal·rollback 권한·현장 중단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

예상 밖이지만 41 이하인 tuple, `0x04`, 한 번의 read 실패는 write 재전송 사유가 아닙니다.
evidence 자격을 잃으면 진단과 raw를 남기고 `UNKNOWN`으로 복원합니다.

## 최소 구현 범위

별도 동결 해제가 승인할 source 범위는 다음 네 파일과 직접 테스트뿐입니다.

- `src/jebao_flow/devices/schedule_flow_experiment.py`
  - fixed signature, safe manual, cap의 중복 상수를 `schedule_linkage.py`와 동시에 변경
  - 반대 Mode·frequency·장비별 boundary를 하위 spec에 구성
  - Sync-only outer role과 content outcome 의미 재도출
- `src/jebao_flow/devices/schedule_linkage.py`
  - fixed constants·allowlist·cap, unequal fixed boundaries, `SYNC_ONLY` recovery topology
  - `ScheduleLinkageSample.slave_linkage`와 `_assert_pair_sample`의 별도
    `full_linkage_topology` 조건에 fixed `sync_slave`를 함께 허용해 Sync runtime content evidence 생성
- `src/jebao_flow/schedule_flow_experiment_cli.py`
  - fixed 값과 `T`, `T+5분` staging, +7분 boundary와 run minimum lead 380초,
    post-boundary stability 30초
- `src/jebao_flow/hardware_test.py`
  - durable `schedule_flow_sample`의 slave topology를 하드코딩된 `async_slave`가 아니라 이 fixed
    Sync-only spec에서만 expected `sync_slave`와 대조하고, 기존 Async 경로의 요구는 그대로 유지
  - `OWN_SCHEDULE` named outcome과 stable sample을 보존하되 Async 전용 success boolean
    `schedule_transition_verified`는 False로 유지
- 위 source를 직접 검증하는 기존 unit tests

`schedule_transaction.py`, `linkage.py`, `lan.py`, `schedule_linkage_cli.py`, persistence, 일반 daemon·MQTT,
physical binding·single-write·journal·rollback write path는 변경하지 않습니다. fake firmware knob, 새
범용 CLI, 새 pre-write 펌웨어 가설 gate를 만들지 않습니다. timing label은 보존 raw의 offline
분석이므로 mixed window를 runtime PASS로 접는 새 gate도 만들지 않습니다.

## §7 결정 보고

### 지금까지의 증거

앱 command surface와 stored image ownership은 확인됐지만 runtime owner는 미확정입니다. 직전 Sync
raw는 역할·Timer·서로 다른 image 보존을 확인했으나 양쪽 A tuple과 경계가 같고 slave manual과
Auto tuple도 같아 판별력이 없었습니다. 과거 sample은 device clock이 없거나 slave raw가 없습니다.

### 대안

1. **이 Sync-only 단회**: content와 timing을 한 operation에서 답합니다. Async write 0, cap 41이고
   physical write 수는 아래 짧은 안과 같습니다. 대신 900초 fresh monitoring을 수행합니다.
2. **content-only 짧은 Sync**: W0만 확보하고 수 분 안에 복원해 연결 수·노출 시간을 줄입니다.
   timing은 계속 UNKNOWN이고, 나중에 답하려면 같은 physical write를 한 operation 더 요구할 수
   있습니다.
3. **park 유지**: native 최적화를 보류하고 software-independent actuator와 그룹 런타임을
   진행합니다. 제품 경로는 이 답에 막히지 않습니다.
4. **과거 raw 재분석**: 이미 수행했으나 attempt 05 schema에 device-local clock이 없어 timing을
   복원할 수 없었습니다. 새 device write를 대체하지 못합니다.

권고는 1번입니다. 1번과 2번의 장비 write 수는 같고, 한 operation에서 두 축을 닫아 §7상 추가
실기 가능성을 줄이기 때문입니다.

### park 영향과 재개 조건

park해도 software-independent 제품 트랙에는 영향이 없습니다. 실기 재개는 이 계획을 근거로 한
repository maintainer의 새 명시 결정, 별도 §1 단회 해제 커밋, exact source/image 검토와 전체 suite,
write-free baseline, on-site operation 승인이 있을 때만 가능합니다. 한 operation과 terminal restore,
서로 다른 두 fresh collector, append-only run 기록 커밋 뒤 해제는 자동 소진됩니다.
