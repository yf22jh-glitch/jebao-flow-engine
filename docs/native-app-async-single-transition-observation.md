# Native app-driven ASYNC single-transition observation

상태: **PLAN ONLY / DEVICE WRITE 0 / HA CHANGE 0**
작성일: 2026-09-03
대상: 현재 물리적으로 binding된 Local Wavemaker Pro 두 대

이 문서는 제바오 앱이 만든 `master`/`async_slave` 상태를 read-only raw로 관측하고, 앱 조작
이전에 보존한 **각 장비의 현재 432-byte schedule image를 byte-exact하게 복원**하기 위한 단회
계획입니다. 이 문서의 커밋만으로 앱이나 `jebao-flow`의 hardware write 권한이 생기지 않습니다.

## 닫을 질문

> 앱에서 장비별 schedule을 저장하고 `master`/`async_slave`를 지정했을 때, slave가 보고하는
> runtime schedule content와 전이 시각은 master와 slave 중 누구에게 귀속되며, operation 종료
> 뒤 두 장비의 시작 schedule image를 장비별 원본 바이트로 복원할 수 있는가?

`AutoMode`/`AutoFlow`/`AutoFreq`는 protocol-reported runtime state입니다. 이 관측은 물리 유량,
파형, 위상을 증명하지 않습니다. 앱 정적 bundle trace는 UI action과 예상 partial payload의
근거이며 firmware runtime 판정 근거로 승격하지 않습니다.

## 새 운영자 사실과 범위

- 역할 B에서 반복 관측된 manual `Random / Flow 89 / Frequency 34` 값 자체는 preserved raw
  evidence 등급 (a)입니다.
- repository maintainer 겸 on-site hardware approver는 이 값이 과거 standalone Random 89
  시험에서 남은 미사용 manual register로 기억한다고 확인했습니다. 발생 원인에 관한 이 기억은
  등급 (c) reconstructed operator observation이며 raw 값의 등급을 대신하지 않습니다.
- 같은 approver는 이번 operation의 복원 중 manual `Random / Flow 89`가 잠시 active가 될 수
  있음을 명시적으로 수용했습니다. 이 결정은 **이번 operation에만** 적용하고 이후 operation에
  이월하지 않습니다.
- `89`를 current schedule의 상한이나 의도된 runtime output으로 해석하지 않습니다. 현재
  schedule의 non-feed Flow 최대 80과 비교한 `89 > 80`은 device limit가 아니라 standalone
  exact-restore v1의 보수적 admission proxy입니다.
- `89`를 정규화하거나 baseline에서 제거하지 않습니다. 앱 조작 전 raw에 있는 값을 그대로
  baseline에 포함하되, 이번 operation의 가장 중요한 복원 대상은 두 장비의 current 432-byte
  schedule image입니다.

## 절대 불변 사항

- **Home Assistant를 재시작·reload·disable·설정 변경·service call하지 않습니다.** HA에 직접
  Jebao connection이 관측되면 에이전트가 끄지 않고 first write 전 `NO-GO`로 멈춰 보고합니다.
- 앱과 collector를 동시에 실행하지 않습니다. 앱 phase가 끝날 때마다 사용자가 앱을 background가
  아닌 완전 종료한 뒤 collector를 시작합니다.
- raw artifact에는 사설 주소·MAC·device id가 있으므로 gitignored owner-only private 경로에만
  둡니다. 저장소에는 opaque artifact id, UTC span, identity binding SHA-256과 artifact digest만
  기록합니다.
- 앱 UI action은 precommitted manifest에 순서와 횟수를 적고 각각 정확히 한 번만 수행합니다.
  결과가 불명확해도 반복 tap하지 않습니다.
- current schedule은 사람이 앱으로 재입력한 결과로 복원 판정을 대신하지 않습니다. 시작 시
  보존한 exact bytes를 코드 복원 경로가 다시 쓰고, 별도 collector가 byte-exact 일치를 확인해야
  합니다.

## 선행 작업 A — operation-bound exact restore

현행 standalone exact-restore 후보는 절대 schema/policy 범위와 별개로 manual Flow가 그 장비의
active non-feed schedule 최대 Flow를 넘으면 거부합니다. 이번 baseline의 `89 > 80`은 그 proxy에
걸립니다. 단순 boolean 우회나 전역 기본값 완화는 만들지 않습니다.

최소 변경은 다음 사실을 manifest와 attended authority에 결속합니다.

1. 각 device operation이 허용할 exact latent manual power를 숫자로 지목합니다. 이번 operation
   manifest에는 두 장비 모두 명시하고 raw baseline의 manual Flow와 exact 일치해야 합니다.
   다만 기존 persisted manifest·비-terminal journal의 attended recovery를 깨지 않도록 model
   필드는 `accepted_latent_manual_power: int | None = None`으로 추가합니다. legacy `None`은 기존
   admission과 완전히 같고, 아래 ceiling 초과 예외를 열지 않습니다.
2. raw manual Flow가 schedule 최대값을 넘는 경우에도 그 값이 manifest에서 지목한 값과 exact
   일치할 때만 상대 proxy를 통과합니다. 다른 값은 계속 거부합니다.
3. 기존 `RestorePowerPolicy.permits()`의 configured min/max, attended maximum과 step 검사는
   유지합니다. 역할 B는 이번 manifest에만 `attended_max_power=89`, `power_step=1`을 명시하고
   configured device maximum을 넘지 않는지 계속 검사합니다. 89보다 큰 attended maximum은
   허용하지 않고, 이 두 값을 새 전역 기본값이나 장비 capability 주장으로 만들지 않습니다.
4. operation manifest에서 baseline을 만들 때 이 값을 baseline에 복사하고, `load_bound_record()`가
   manifest와 baseline의 exact 일치를 확인합니다. 이 필드는 manifest hash와 그 operation id,
   `baseline_sha256`, `authority_context_sha256`에 포함되고, 대면 attended challenge가 baseline과
   journal-context digest를 인쇄·hash해 결속합니다. action plan은 baseline digest를 넣은
   deterministic target에서 파생되고 발급된 authority는 그 digest들을 담지만, action-plan 또는
   authority-chain payload가 이 필드를 직접 hash한다고 과대 진술하지 않습니다. source 변경 전에
   비-terminal exact-restore journal 부재도 read-only로 확인해 기록하되, 부재 확인을 serialization
   호환성의 대체물로 삼지는 않습니다.
   새 호환 필드가 모두 `None`인 legacy manifest·baseline의 canonical hash와 operation id는 변경
   전 값과 byte-identical해야 합니다. 각 affected model의 targeted serializer에서 각 device의
   `accepted_latent_manual_power`와 top-level `corroborating_evidence`, 이 **두 이름의 새 key만**
   값이 `None`이면 제거합니다. 일괄 `exclude_none=True`로 다른 nested field를 바꾸지 않습니다.
   non-None 값은 새 canonical payload에 포함합니다. 이 serializer는
   `ExactRestoreBaseline.baseline_sha256`, `ExactRestoreOperationManifest.manifest_sha256`과 그
   `operation_id`, `ExactRestoreRecord.authority_context_sha256`뿐 아니라 record를 embed한
   qualified-baseline archive의 canonical 재검증에도 동일하게 적용돼야 합니다. 공용 hash
   helper 한 곳만 고치는 방식은 쓰지 않아 다른 dump 경로도 빠뜨리지 않습니다. 대표 legacy
   persisted record·manifest·qualified bundle을 실제 deserialize·binding 검증하고, 기존
   `baseline_sha256`·`manifest_sha256`·operation id·`action_plan_sha256`·`authority_context_sha256`가
   byte-identical임과 이미 소비된 authority가 있는 legacy
   recovery 검증이 통과함을 회귀 테스트로 고정합니다. 새 serializer를 거친
   `model_validate(model_dump())`도 원 모델과 동등해야 합니다.
5. 앱 operation 전에 별도 qualification operation을 수행합니다. 우리가 만든 bounded temporary
   state를 standalone 경로로 시작 snapshot에 복원하고, 서로 다른 두 fresh collector에서
   controls와 두 schedule image가 byte-exact임을 확인합니다. qualification을 앱 operation과
   동시에 수행하지 않습니다.

복원 순서는 기존 후보 계약을 유지합니다.

1. slave, master 순으로 `SwitchON=true + TimerON=false + independent + Constant + safe Flow/Frequency`
   atomic fallback frame을 각각 정확히 한 번
2. slave와 master의 exact 432-byte schedule image 복원
3. slave, master 순으로 원 `SwitchON + TimerON + Linkage + Mode + Flow + Frequency`를 각각 한
   atomic frame으로 정확히 한 번
4. writer 완전 종료 뒤 fresh read-only convergence와 두 독립 collector의 exact 검증

ACK나 적용이 불명확해도 같은 frame을 재전송하지 않습니다. final exact 검증이 끝나지 않으면
journal을 유지하고 `RECOVERY_REQUIRED`로 종료합니다.

## 선행 작업 B — report-capable fresh-session collector

현재 standalone collector는 `accept_reports=false`, action `0x03`만 허용합니다. 2026-09-01
ASYNC 관측은 이 경로에서 slave raw 0개로 끝났으므로 앱 ASYNC epoch에 그대로 사용하지 않습니다.

새 read-only transport policy는 기존 strict mode를 변경하지 않고 별도 명시 값으로 추가합니다.

- `explicit_only`: 기존 `accept_reports=false`, accepted action `{0x03}`
- `reply_or_report`: `accept_reports=true`, accepted actions `{0x03,0x04}`

두 policy 모두 장비별 sample마다 fresh discovery, fresh authenticated TCP session, session당 read
한 번, identity before/after 검증, raw-before-decode와 no retry를 유지합니다. report-capable mode는
다음을 추가로 보존·검증합니다.

- 실제 action `0x03` 또는 `0x04`와 action별 count
- participant, pair ordinal, host UTC/monotonic read span
- frame-local device `NowTime`
- decoded controls, `AutoMode`/`AutoFlow`/`AutoFreq`, exact schedule image digest
- 실패 phase와 allow-listed exception class

`0x04`를 explicit reply나 request ACK라고 부르지 않습니다. 각 session이 fresh이므로 이전
session의 report backlog를 물려받지 않지만, frame-local `NowTime`으로 각 관측창 귀속을 다시
판정합니다. 기존 strict baseline/exact-restore evidence는 계속 `explicit_only`만 사용합니다.

현행 고정 지점도 함께 바꿉니다. `read_only_collector.py`의
`read_raw_state_capture(accept_reports=False)` 호출과 durable `accept_reports_policy=False`를
policy별 값으로 만들고, `q2_epoch.py`에서 `accept_reports is not False`를 거부하는 plan
validator는 **새 `reply_or_report` plan에서만** 두 action을 허용하도록 좁혀 확장합니다. 기존
`explicit_only` plan의 `False`·`0x03` 요구는 그대로 유지합니다.

## 앱에 입력할 판별 schedule

각 장비는 하루 중 한 번만 전이합니다. 전이 시각을 같게 두지 않아 content와 timing을 함께
구분합니다.

| participant | before | after | boundary |
|---|---|---|---|
| master | `Sine / Flow 35 / F30` | `Constant / Flow 30 / wire F0` | `T` |
| slave | `Constant / Flow 32 / wire F0` | `Sine / Flow 40 / F30` | `T+5분` |

Flow `{30,32,35,40}`은 서로 다르고 Mode family도 반대입니다. 앱에서 schedule을 저장할 때 두
장비는 `independent`여야 합니다. schedule과 `TimerON` 설정이 끝난 뒤 master를 `{Linkage:1}`로,
slave를 parent Slave `{Linkage:2}` 다음 Async `{Linkage:3}`으로 전환합니다. slave role 진입 뒤
timer switch나 slave Flow slider를 건드리지 않습니다.

실제 UI action 이름·대상·순서·횟수는 앱을 열기 전에 private operation manifest에 고정합니다.
각 logical role과 앱 화면의 장비를 identity binding과 사람이 대조한 뒤에만 첫 action을 합니다.

## 관측과 사전 고정 해석

앱 설정·role 전환 뒤 앱을 완전히 종료하고, 경계 전부터 slave 경계 뒤까지 report-capable
fresh-session pair series를 수집합니다. 모든 raw는 판정 전에 보존합니다.

master가 같은 pair에서 실제 master B `Constant/30`을 보고한 T 이후와 slave가 자기 경계
T+5분을 넘기 전의 mixed window에서 slave tuple을 다음처럼 해석합니다.

| slave protocol-reported tuple | 사전 고정 해석 |
|---|---|
| `Constant / 30 / F0|5` | master timing + master content |
| `Sine / 40 / F30` | master timing + slave content |
| `Sine / 35 / F30` | slave timing + master content |
| `Constant / 32 / F0|5` | slave timing + slave content 또는 동일 tuple의 mirrored manual 반향 |
| 시작 baseline의 exact manual tuple | manual echo |
| Mode와 Flow가 서로 다른 owner를 가리킴 | field split |
| 그 밖의 bounded tuple | unexpected effective state |

master가 같은 pair에서 exact master B를 보고하지 않거나, participant frame의 `NowTime`이 해당
창을 확정하지 못하면 timing evidence로 사용하지 않습니다. T 이전과 T+5분 이후 창에서도 master
content, slave content, manual echo를 각각 다시 분류합니다. 창 사이 결과가 상충하거나 유효
sample이 없으면 `UNKNOWN`입니다. 어떤 라벨도 물리 유량·파형 적용을 뜻하지 않습니다.

## 실행 phase

### Phase 0 — write-free baseline 이중 보존

1. private config는 `dry_run=true`, 모든 device code write는 disabled 상태로 확인합니다.
2. unfinished recovery journal, writer process와 TCP 12416 established connection을 read-only로
   확인합니다. HA connection이 보이면 HA를 조작하지 않고 `NO-GO`로 보고합니다.
3. 서로 다른 두 process/fresh session의 strict collector에서 controls, role, `TimerON`, 두
   432-byte image를 각각 보존합니다.
4. 두 baseline artifact가 physical identity, outer controls와 schedule bytes에서 exact 일치해야
   합니다. 두 artifact reference와 digest를 restore/app manifest에 함께 결속합니다.

현행 exact-restore model의 단일 `evidence`는 primary reference로 유지하고,
`corroborating_evidence: ... | None = None`을 manifest와 durable baseline에 호환 필드로 추가합니다.
legacy `None`은 기존 단일-evidence record를 읽기 위한 것일 뿐이며, **이번 operation manifest와
baseline에서는 non-None을 필수로** 합니다. composition은 두 artifact를 각각 독립적으로 load·digest
검증·decode하고 physical identity, outer controls 여섯 필드와 432-byte schedule bytes의 exact
일치를 확인한 뒤에만 primary snapshot으로 restore record를 만듭니다. 두 reference는 서로 다른
plan/series와 pair manifest를 가리켜야 하며 같은 reference 재사용을 독립 evidence로 세지
않습니다. 둘 다 observation age와 pair-gap policy를 독립 통과해야 합니다. 두 evidence reference는
manifest hash와 그 operation id, baseline hash와 journal-context hash에 함께 포함됩니다. action-plan
payload가 reference 원문을 직접 embed하지는 않지만, 각 deterministic action target payload가
`baseline_sha256`를 포함하므로 target digest를 거쳐 action-plan digest에도 간접 결속됩니다. 대면
challenge는 baseline·action-plan·journal-context digest를 인쇄·hash합니다. legacy `None`일 때는 위
호환 canonicalization을 적용해 기존 baseline hash와 operation id를 그대로 유지합니다.

manifest와 durable baseline validator는 어느 device든 `accepted_latent_manual_power`가 non-None이면
`corroborating_evidence`도 반드시 non-None이도록 강제합니다. 따라서 ceiling 예외를 이중 baseline
교차확인 없이 취하는 조용한 downgrade가 없고, legacy의 둘 다 `None`인 경로는 기존 거부 의미를
그대로 유지합니다.

### Phase 1 — standalone exact-restore qualification

앱을 사용하지 않는 별도 attended operation입니다. exact source/image, identity, approved
manifest와 physical stop authority를 확인한 뒤 bounded temporary state를 만들고 standalone
경로로 Phase 0 snapshot에 복원합니다. 두 fresh collector exact 검증이 끝나야 선행 작업 A가
완료됩니다. 실패하면 앱 operation으로 넘어가지 않습니다.

### Phase 2 — 앱 schedule·role 설정

collector와 모든 code writer가 완전히 종료된 상태에서 on-site approver가 precommitted UI
action을 정확히 한 번씩 수행합니다. 각 checkpoint에서는 앱을 완전히 종료한 뒤 read-only
collector가 schedule digest, `TimerON`, role과 controls를 보존하고 다시 disconnect합니다.

### Phase 3 — read-only ASYNC epoch

앱을 완전히 종료한 상태에서 report-capable collector를 실행합니다. 경계 전, T 이후 mixed
window, T+5분 이후를 모두 덮고 raw action·device clock·schedule digest·Auto tuple을 보존합니다.
collector는 장비에 control frame을 보내지 않습니다.

### Phase 4 — exact restore와 검증

pre-staged standalone record만 사용해 Phase 0 baseline으로 복원합니다. 코드 writer 종료 뒤 서로
다른 두 strict fresh collector가 다음을 모두 exact 검증해야 완료입니다.

- 역할별 432-byte schedule image bytes와 digest
- `SwitchON`, `TimerON`, `Linkage`, `Mode`, `Flow`, `Frequency`
- physical identity binding

하나라도 불일치하면 완료로 기록하지 않고 journal을 유지한 채 escalation합니다. 앱 수동 재입력은
primary restore로 사용하지 않습니다.

## 구현·검토 경계

- frozen native schedule/linkage harness 파일은 이 계획에서 수정하거나 실행하지 않습니다.
- exact-restore 변경은 `exact_restore*` composition/model/authority와 직접 테스트에 한정합니다.
- read-only 변경은 `read_only_collector.py`, `q2_epoch.py`, source module CLI, 필요한 offline
  verifier와 직접 테스트에 한정합니다. 특히 collector의 고정 `accept_reports=False` 호출과
  durable `accept_reports_policy=False`, `q2_epoch.py`의 `accept_reports is not False` assertion을
  policy 조건부로만 확장합니다. write adapter를 import하거나 노출하지 않습니다.
- HA integration, daemon/MQTT, 일반 actuator, `devices/linkage.py`, `devices/schedule_linkage.py`,
  `devices/schedule_flow_experiment.py`와 그 CLI는 변경하지 않습니다.
- 계획 커밋 → Claude read-only review → source/tests → Linux/amd64 전체 suite와 image attestation
  → Claude exact diff review → Phase 0 순서를 지킵니다.

## 중단·소진

qualification과 앱 operation은 서로 다른 operation id와 승인을 사용합니다. 실제 첫 write마다
on-site hardware approver의 그 operation 승인, 현장 감시와 즉시 중단 수단 확인이 필요합니다.
사용자가 앱 action을 수행하는 동안 software cap은 없으므로 unexpected output은 현장 감시자가
즉시 중단합니다.

terminal restore와 두 fresh collector exact 검증, append-only `docs/runs/` 기록이 끝나면 해당
operation은 소진됩니다. non-terminal journal이 있으면 새 앱 action·새 실험·상수 변경을 금지하고
동일 exact source와 staged record의 attended recovery만 수행합니다.
