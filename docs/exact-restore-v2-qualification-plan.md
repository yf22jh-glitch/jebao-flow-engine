# Exact-restore version 2 qualification and app-observation unblock

상태: **PLAN ONLY** — 이 문서와 그 commit은 장비·앱·Home Assistant 접촉 권한을 만들지 않습니다.

작성일: 2026-09-05

선행:
[`Exact-restore final verification bound split and parked disposition`](exact-restore-final-verification-and-park-plan.md),
[`Exact-restore disposition eligibility invariant and claim diagnostics`](exact-restore-disposition-diagnostics-plan.md)

앞 두 계획의 실행 경계 1~6과 두 진단 안건이 모두 완료됐습니다. 남은 것은 **실행 경계 7**이고, 이
문서가 그 단계의 계획입니다.

## 목표와 정확한 종료 조건

앱 주도 ASYNC 관측을 절차상 가능하게 만드는 것입니다. 그 경로의 `AppRestoreClaim`은 **네 가지**를
요구하며, 하나라도 없으면 manifest 자체를 만들 수 없습니다.

- `sentinel_receipt_id` / `sentinel_receipt_sha256`
- `baseline_receipt_id` / `baseline_receipt_sha256`
- `final_restore_operation_id`
- `final_restore_record_sha256`

따라서 종료 조건은 "qualification 1회 성공"이 아니라 **sentinel cycle과 baseline restore cycle을 모두
`FINAL_VERIFIED`로 닫고 qualified bundle과 staged phase-5 record까지 만드는 것**입니다. 이것이
충족돼야 선행조건 2가 닫힙니다.

## 왜 지금 가능하다고 보는가 — 그리고 이것이 보증이 아닌 이유

version `1`은 하나의 policy 필드가 completion coherence와 전체 acquisition을 동시에 재는 overload
때문에 final verify에서 **구조적으로** 실패했습니다. version `2`는 두 bound를 분리했고 precommit
값은 coherence `20초`, acquisition `30초`입니다.

보존된 실측 대비 여유는 다음과 같습니다.

| 지표 | 실측 최대 | v2 bound | 여유 |
|---|---|---|---|
| raw-read completion coherence | `11.681584751초` | `20초` | 약 `8.3초` |
| 전체 acquisition span | `22.795초` | `30초` | 약 `7.2초` |

**이 표는 예측이지 보증이 아닙니다.** 실측은 네 pair에서만 나왔고, discovery의 고정 5초 창 네 번이
바닥을 만들지만 connect/authenticate 구간은 역할별로 `0.18초`와 `1.10초`처럼 흔들렸습니다. v2에서도
실패하면 그것은 **새 정보**이며, 예측이 틀렸다는 뜻이므로 재시도가 아니라 원인 분석으로 갑니다.

## 단계 — 각 단계의 장비 접촉 등급을 명시

### 0단계 — 사전 재검증 (device network `0`)

- permanent version `2` parked-finalization barrier가 존재하고 backing snapshot·disposition과 exact
  reload로 일치하는지
- live journal path의 tombstone이 그대로이고 archive와 교차확인되는지
- controller의 `_require_operation_not_parked()`가 parked operation id를 계속 거부하는지
- recovery supervisor가 계속 exited인지
- **Home Assistant가 장비에 붙어 있지 않은지** — read-only preflight에서 Jebao TCP connection이
  보이면 integration을 끄지 않고 `NO-GO`로 중단해 보고합니다. fresh capture 직전과 **각 attended arm
  직전에 다시** 확인합니다
- **앱이 완전히 종료됐는지** — background 포함. 같은 Pro를 쓰는 다른 폰·태블릿·클라이언트가 없어야
  합니다
- supervisor뿐 아니라 **모든 code writer 부재** — `jebao-flowd`, one-shot writer 포함
- 미완 recovery journal·비terminal intent·emergency latch 부재. `prepare`의
  `require_cross_workflow_quiescent()` 결과를 기록합니다
- 실행 UID/GID가 store의 owner·mode와 호환되고 owner-only store가 writable한지
- **stale-image 함정 방지**: mounted source digest만이 아니라 **실제로 import된 `jebao_flow`의 경로와
  bytes**가 reviewed commit과 일치하는지. 이 컨테이너에서 `/work`로 복사하고도 이미지의
  `/workspace/src`가 먼저 import된 선례가 있으므로, `PYTHONPATH`와 import attestation을 실기 전에
  고정합니다
- reviewed source/image attestation

### 1단계 — fresh Phase 0 (device **read**, write `0`)

- explicit-only strict pair를 **primary와 corroborating 두 series**로 수집
- 두 series는 서로 다른 plan·series여야 하고, 두 장비의 outer six-field와 432-byte schedule image가
  byte-exact하게 일치해야 함
- 기존 parked operation의 artifact를 재사용하지 않음
- **primary와 corroborating 각각이** admission에서 coherence `≤20초`와 total acquisition `≤30초`를
  통과해야 합니다. 하나라도 실패하면 write 이전에 `NO-GO`입니다. 이것이 위 여유 표를 현재 환경의
  사전 증거로 보강하는 유일한 지점입니다
- observation age가 만료되면 **완화하지 말고 재수집**합니다

### 2단계 — v2 manifest 작성 (장비 접촉 `0`)

- **새 nonce 하나에서 파생되는 세 operation id 전부가 새것이어야 합니다** — sentinel qualification,
  baseline restore, phase-5 final restore. parked operation id 재사용은 permanent barrier가 거부하며
  시도해서도 안 됩니다
- `version: 2`, `max_final_pair_gap_seconds: 20`, `max_final_acquisition_duration_seconds: 30`,
  discovery timeout `5초`, identity age `5초`
- **timing 외 안전 정책도 manifest에 명시**합니다: 역할별 accepted latent manual `30`·`89`,
  configured range `30..100` step `1`, attended maximum `60`·`89`, safe fallback
  `Constant / Flow 30 / Frequency 20`, observation age
- 1단계의 fresh artifact에 결속

### 3단계 — `prepare` (device write `0`)

tombstone을 정당한 successor로 교체하는 **유일한** 지점입니다. R9 연언을 전부 만족해야 통과합니다:
policy의 acquisition 필드가 non-`None`, manifest version `2`, parked operation id와 **다른** id,
permanent barrier와 backing archive exact confirm, guard 허용, journal claim 하의 단일 `save()`.

### 4~6단계 — attended qualification (**device write**)

attended `qualify`는 한 명령 안에서 **두 write cycle**을 수행합니다.

1. sentinel qualification cycle → `FINAL_VERIFIED` → **sentinel receipt**
2. baseline restore cycle로 promote → `FINAL_VERIFIED` → **baseline receipt** → qualified bundle
3. phase-5 final restore record staging

승인 주체가 둘이고 역할이 다릅니다. **repository maintainer**는 code·plan과 operation의 범위를
승인하고, **on-site hardware approver**는 각 exact operation의 write와 현장 감시·즉시 물리 차단을
승인합니다. 앞 단계의 어떤 승인도 이 단계를 대신하지 않습니다.

한 `qualify` 명령이라도 **sentinel과 baseline은 서로 다른 operation id와 서로 다른 attended
grant**입니다. 현장 승인은 **두 exact action plan을 모두 명시적으로 포함**해야 하며, 두 번째
challenge가 첫 승인의 범위를 암묵적으로 넓히지 않습니다.

**write 등급**: sentinel cycle은 8 action 전부가 write입니다. baseline restore cycle은 **6 action**이고,
장비가 exact baseline인 정상 경로에서는 safe fallback 2 write + restore schedule 2 `ALREADY_SATISFIED`
skip + restore outer 2 write = **write 4 + skip 2**입니다. drift가 있으면 최대 6 write까지 가능하므로
승인 문구는 **"exact 6-action plan, 최대 6 write"**로 씁니다. phase-5 staging은 **host journal write이고
device write는 `0`**입니다.

### 7단계 — 기록

산출된 receipt·bundle digest, 두 cycle의 실측 coherence/acquisition span, 선행조건 2의 상태 변화를
append-only로 기록합니다.

## 실패 시 처리 — 두 cycle의 노출도가 **다릅니다**

재시도 규칙은 하나입니다: **첫 실패 뒤 자동·즉시 재시도는 `0`회이며, diagnostic 기록과 새 precommit과
새 승인 없이는 resume하지 않습니다.** 그러나
좌초한 journal을 종결할 수 있는지는 **어느 cycle에서 좌초했는지에 따라 갈립니다.**

**sentinel cycle(4단계) 좌초 — park 가능합니다.** disposition 기구는 재사용 가능하고 manifest만
특정 journal에 결속되므로, 새 operation·journal digest로 **새 disposition manifest**를 만들어 park할
수 있습니다. 기존 manifest로는 불가능하다는 앞선 서술과 모순되지 않습니다 — 그때 말한 것은 *기존*
manifest의 재사용 불가였습니다.

**baseline restore cycle(5단계) 좌초 — 현재 park 경로가 존재하지 않습니다.** `ParkedDispositionManifest`가
구조적으로 그 상태를 표현할 수 없습니다.

- `cycle: Literal[ExactRestoreCycle.SENTINEL_QUALIFICATION]` — baseline restore cycle을 담을 수 없음
- `completed_action_count: Literal[8]` — sentinel cycle은 8개(safe fallback 2 + sentinel 2 +
  schedule 2 + outer 2)지만 baseline restore cycle은 **6개**(safe fallback 2 + schedule 2 + outer 2)
- `reason: Literal["final_verification_policy_overload"]` — v2에서의 실패는 overload가 아니므로
  사유 자체를 표현할 수 없음
- `verify_park_eligibility()`도 `record.cycle is not SENTINEL_QUALIFICATION`을 거부

즉 disposition 기구는 **v1 overload로 좌초한 sentinel journal 전용**으로 만들어졌고 범용이 아닙니다.
5단계는 6-action plan(정상 경로 write 4 + skip 2, 최대 write 6)을 포함하는데, 좌초하면 **`awaiting_final_verify`에서 나갈 write-free 경로가
없는 상태로 되돌아갑니다.** 그것이 이 프로젝트가 park 기구를 만들어 탈출한 바로 그 dead-end입니다.

### 따라서 이 계획은 4~6단계를 **아직 승인 가능한 상태로 제시하지 않습니다**

두 선택지가 있고, 결정은 repository maintainer의 것입니다.

1. **(권고) 먼저 disposition 기구를 일반화한다.** cycle과 action count와 reason을 두 cycle 모두
   표현할 수 있게 넓히고, overload 전용 timing 조건을 사유별로 분리하며, eligibility의 cycle 검사를
   manifest와 대조하도록 바꿉니다. 별도 계획·검토·회귀가 필요한 범위 한정 작업입니다. 그 뒤에
   4~6단계를 승인합니다.
2. 노출을 **알고** 수용하고 4~6단계를 진행한다. 이 경우 5단계 좌초 시 종결 수단이 없다는 것을
   기록에 남긴 상태로 시작하는 것입니다.

제 권고는 1번입니다. 예측(위 여유 표)이 맞다면 어느 쪽이든 좌초하지 않겠지만, **예측이 틀렸을 때의
비용이 두 선택지에서 크게 다릅니다.** 그리고 v1에서 예측이 이미 한 번 틀렸습니다.

**결정(2026-09-05): repository maintainer가 선택지 1을 택했습니다.** 따라서 일반화가 4~6단계보다
**먼저** 진행됩니다.

**그 뒤 갱신(2026-09-05)**: 첫 일반화 계획은 범위 산정이 세 번 틀려
[철회됐습니다](exact-restore-disposition-generalisation-plan.md). 교차검증이 찾은 세 결함 중 둘은 §3
P1 경로입니다. 따라서 선행 작업은 **두 개**로 나뉘고 순서가 있습니다.

1. **predecessor success finalization 불변식** — baseline 좌초 시 embedded sentinel operation이 영구
   장벽 없이 남는 replay 표면을 원인에서 제거합니다.
2. **재설계된 disposition 일반화** — receipts decoder, outcome taxonomy, legacy golden bytes까지 포함.

둘이 모두 검토·착지하고 이 문서의 정정과 preflight가 닫히기 전에는 4~6단계를 승인 대상으로 올리지
않습니다.

### 그 외 공통

- park보다 **원인 분석이 먼저**입니다. v2에서의 실패는 위 예측이 틀렸다는 새 정보이므로, 실패한
  predicate와 실측값을 diagnostic에서 읽어 기록한 뒤 판단합니다.
- write가 끼인 채 실패하면 기존 restore/recovery 경로가 그대로 적용되며, 이 계획은 그 경로를 바꾸지
  않습니다.

## §3 판정

0~2단계는 device write `0`이고 durable journal도 바꾸지 않습니다. **3단계(`prepare`)는 device write가
`0`이지만 §3에 도달할 수 있습니다** — live durable journal의 tombstone을 successor로 교체하므로 두 번째
범주(journal·rollback 손실)가 살아 있습니다. 방어는 R9 연언과 archive confirmation의 fail-closed
성질입니다. **4~6단계는 실제 device write를
포함하므로 세 범주가 모두 살아 있습니다.** 이 단계의 방어는 새로 만들지 않고 기존 것을 그대로
씁니다 — attended authority, guard lease, exactly-once action prefix, wire 경계 이전의 durable
inflight, 그리고 실패 시 fail-closed latch입니다.

## 하지 않는 것

- parked operation id·manifest·tombstone·permanent barrier를 재사용하거나 수정하지 않습니다.
- version `1` 의미를 새 의미로 소급 재해석하지 않습니다.
- frozen native schedule/linkage harness를 수정하거나 실행하지 않습니다.
- Home Assistant를 재시작·reload·disable·설정 변경·service call하지 않습니다.
- 앱을 열어 schedule이나 role을 바꾸지 않습니다. 이 계획은 앱 관측의 **선행조건**만 다룹니다.

## 승인 경계

- 이 계획의 commit은 하드웨어 승인이 **아닙니다.**
- **1단계는 device read를 포함하므로 별도 승인이 필요합니다.**
- **4~6단계는 on-site 승인·감시·즉시 물리 차단을 요구하며, 다른 어떤 승인으로도 대체되지
  않습니다.** 그리고 위 "실패 시 처리"의 선택지 1 또는 2 중 하나를 maintainer가 **명시적으로**
  고른 뒤에만 승인 대상이 됩니다.
- 앱 live-write는 이 계획의 범위 밖이며, 선행조건 2가 닫힌 뒤 별도 계획과 승인을 다시 요구합니다.
- **on-site approver에게 미리 알릴 것**: 실패가 남길 수 있는 dwell 상태(`ON + TimerOFF + independent +
  Constant 30 / Frequency 20`, 또는 부분 복원 상태)와, 그 경우 attended recovery가 필요하다는 점.

## 이 계획을 쓰면서 발견한 것 (기록)

`AppRestoreClaim`이 sentinel receipt만이 아니라 baseline receipt와 staged phase-5 record까지
요구한다는 점, 그리고 disposition 기구가 sentinel cycle 전용이라 **baseline restore cycle 좌초에는
종결 경로가 없다**는 점은 이 계획을 작성하는 과정에서 드러났습니다. 둘 다 실기 이전에 알아야 할
사실이었고, 후자는 4~6단계의 승인 조건 자체를 바꿉니다.
