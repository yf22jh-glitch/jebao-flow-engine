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

---

## 2026-09-05 supersede — 현재 source 기준 재검증 (PLAN ONLY, 여전히 hardware NO-GO)

위 본문의 역사 서술은 삭제하지 않습니다. 그것은 작성 시점의 사실이었고, 아래는 그 뒤 두 작업이
착지한 뒤의 상태로 해당 부분을 **대체**합니다. 이 절 자체는 장비·앱·Home Assistant 접촉 권한을
만들지 않으며, 아래 어느 문장도 4~6단계를 승인 대상으로 올리지 않습니다.

### 1. "baseline restore cycle 좌초에는 종결 경로가 없다"는 이제 사실이 아닙니다

본문 `실패 시 처리` 절의 다음 서술은 **대체됩니다**.

- `cycle: Literal[SENTINEL_QUALIFICATION]` → 현재는 `cycle: ExactRestoreCycle`이며 sentinel과
  baseline restore 모두를 담습니다.
- `completed_action_count: Literal[8]` → 현재는 cycle별 결정론 계획과 대조되는 정수이고,
  sentinel `8` / baseline restore `6`입니다.
- `reason: Literal["final_verification_policy_overload"]` → version 2는
  `final_verification_unresolved`를 씁니다.
- `verify_park_eligibility()`의 `record.cycle is not SENTINEL_QUALIFICATION` 하드코딩 → 제거됐고
  manifest와 대조합니다.

두 선행 작업이 모두 착지했습니다 — predecessor success finalization 불변식(작업 A)과 disposition
일반화(작업 B). 현재 park은 embedded predecessor depth `0`(sentinel) / `1`(baseline restore) /
`2`(phase-5 staged restore) 셋 모두에서 동작하고, baseline cycle의 정상 분포(write 4 · skip 2)도
거부되지 않습니다. 5단계 좌초 시 write-free 종결 수단이 없다는 본문의 노출 서술은 해소됐습니다.

**그러나 이것은 hardware GO가 아닙니다.** 본문이 4~6단계를 보류한 이유는 두 가지였고, 해소된 것은
그중 하나뿐입니다. 나머지 — fresh Phase 0, 새 v2 manifest, 두 operation 각각의 on-site 승인 — 은
그대로 남아 있고, 아래 2번에서 **새 보류 사유가 하나 더 생깁니다**.

### 2. `max_observation_age_seconds`에 숫자를 넣지 못했습니다 — 이 필드도 overload입니다

본문 2단계는 이 필드를 이름만 적고 값을 비워 뒀습니다. 값을 정하려고 현재 source의 소비처를 전수
추적한 결과, 이 필드는 **성격이 다른 네 종류의 창을 동시에 재고 있습니다.** version 1의
`max_final_pair_gap_seconds`가 그랬던 것과 같은 구조입니다.

| # | 소비처 | 재는 것 | 방향 |
|---|---|---|---|
| A | `ExactRestoreVerificationPolicy` 검증 | `pair_gap ≤ age`, `acquisition ≤ age` | 하한 `≥ 30` |
| B | `ExactRestoreOperationManifest.validate_pair` | `identity_age(5) ≤ age` | 하한 `≥ 5` |
| C | `build_verified_baseline` | Phase 0 각 sample 완료 → `prepare` 실행까지의 경과 | **작을수록 강함** |
| D | `_initial_sentinel_baseline_freshness` | `(첫 write 시각 − captured_at) + pair_gap ≤ age` | **작을수록 강함** |
| E | action별 observation 신선도 | 단일 read의 `received − observed` | 비구속(초 미만) |
| F | final verify의 observation age | `completed − observed`, acquisition(30)이 이미 더 좁음 | 비구속 |
| G | **Q2 handoff 유효기간** | qualification 종료 → 앱 ASYNC manifest 생성까지의 경과 | **클수록 필요** |

C·D는 **작은 값**을 원하고 G는 **큰 값**을 요구합니다. G가 존재한다는 것은 이 필드가 baseline
신선도 정책이 아니라 **qualified bundle의 수명**이기도 하다는 뜻입니다. 값은 bundle에
`maximum_handoff_age_seconds`로 복사되어 보존되고, 앱 관측 manifest 생성 시점에
`(cutoff − baseline receipt 완료) ≤ 그 값`으로 다시 쓰입니다.

**이 절의 초안은 여기서 "한 숫자가 둘을 동시에 만족시킬 수 없다"고 썼습니다. 그것은 과장이었고
아래에서 철회합니다.** 보존된 실측을 넣어 보면 D의 하한이 이미 충분히 커서, D를 만족시키는 값은
G도 자동으로 넉넉히 만족시킵니다. 실현 불가능한 것이 아니라, **두 창이 한 숫자에 묶여 있어 어느
쪽도 독립적으로 조일 수 없다**는 것이 이 overload의 실제 비용입니다.

#### 실측에 근거한 재도출

이 저장소의 run 기록에 보존된 값으로 D를 계산합니다.

- primary strict series의 accepted pair 수집 종료: `2026-09-04T01:21:10.832179Z`
- 첫 action 완료: `2026-09-04T01:41:34.966170Z`
- 경과: `1224.134초` (약 `20.40분`)
- 게이트 요구: `경과 + pair_gap(20) = 1244.134초`

이 구간에는 manifest 검토, `prepare`, 현장 승인 낭독과 입력, 그리고 한 번 만료된 challenge의
재발급이 모두 들어 있습니다. 즉 이것은 이상적인 tempo가 아니라 **실제로 관측된 tempo**입니다.

따라서 `max_observation_age_seconds = 1800`은 **유지하되, 이월이 아니라 이 실측에서 재도출된
값으로** 적습니다. 근거는 다음과 같습니다.

- 실측 요구 `1244.134초`에 대해 여유 `555.866초`(약 `9.26분`)를 남깁니다. 약 `45%`입니다.
- first-wire deadline은 `captured_at + age − pair_gap = captured_at + 1780초`(약 `29.67분`)이며,
  이 값을 Phase 0 직후에 계산해 표시합니다.
- 같은 값이 handoff 창이므로, **baseline receipt 완료 뒤 앱 ASYNC manifest를 `1800초` 안에
  만들어야 합니다.** 이것은 부수 효과가 아니라 이 선택이 만드는 **절차 요구**이며 계획과 승인
  문구에 명시합니다.

더 작은 값은 권하지 않습니다. 예컨대 `600초`면 실측 tempo에서 first write에 도달하지 못하고,
도달하더라도 handoff 창이 `10분`으로 줄어 앱 경로가 같은 자리에서 이어지지 않으면 닫힙니다.
더 큰 값도 권하지 않습니다. C·D의 신선도 의미가 그만큼 희석되고, 얻는 것은 이미 확보된 여유뿐입니다.

**여전히 권고하는 후속 작업**: 작업 A가 `max_final_pair_gap_seconds`에 대해 한 것과 같은 분리입니다.
capture freshness bound와 handoff validity bound가 별도 필드가 되면, 운영 tempo가 빨라졌을 때
freshness를 조이면서도 handoff 창을 줄이지 않을 수 있습니다. 지금은 tempo를 개선할수록 freshness를
조일 수 있게 되지만 **그렇게 하면 handoff 창이 함께 줄어드는** 구조입니다. 이것은 4~6단계를 막는
사유가 아니라 별도 안건입니다.

### 2b. 만료된 `PREPARED`/`ARMED`는 종결 경로가 없습니다 — 새로 공개하는 실패 경계

교차검토가 찾아 준 것이고 저는 놓쳤습니다. 코드로 확인했습니다.

`build_verified_baseline`은 artifact age만 보고 `PREPARED`를 설치합니다. 그 뒤 freshness 게이트는
`arm`, `execute` 진입, 각 action 앞, 그리고 실제 첫 wire 직전의 lock 안에서까지 다시 작동합니다.
첫 possible write 전에 창을 넘기면 `baseline_expired_before_first_write`로 fail-closed하는데,
**journal은 `PREPARED` 또는 `ARMED`인 채로 남습니다.** 이 상태에서 빠져나갈 길이 없습니다.

- park 불가 — eligibility가 `awaiting_final_verify`와 완전한 action prefix를 요구합니다.
- 새 record로 교체 불가 — `create`는 기존 record가 있으면 거부하고, 교체 분기는 **parked
  tombstone일 때만** 열립니다. 살아 있는 `PREPARED`/`ARMED`는 그 분기에 들어가지 않습니다.
- receipt 없는 clear 불가 — journal을 지우는 유일한 경로는 `clear_after_receipt`이고
  `FINAL_VERIFIED`와 정확히 일치하는 receipt를 요구합니다.
- `recover` 대상 아님 — 이 실패는 preflight 거부이지 latch가 아니므로 `RECOVERY_REQUIRED`로
  가지 않습니다.

device write `0`이고 §3 P1도 아니지만, **write 없이 좌초하는 비-terminal journal**입니다. 작업 B가
연 종결 경로는 `awaiting_final_verify` 좌초를 위한 것이지 이 경계를 덮지 않습니다.

따라서 4~6단계 절차에 다음을 넣습니다.

1. exact manifest 검토와 operation별 repository·on-site readiness를 **`prepare` 이전에** 끝냅니다.
   승인 대기 시간이 창을 먹는 구간은 `prepare` 이후이지 이전이 아닙니다.
2. Phase 0 직후 `captured_at + max_observation_age − max_final_pair_gap`으로 **first-wire deadline을
   계산해 표시**합니다. 현재 값에서는 `captured_at + 1780초`입니다.
3. 남은 여유가 충분하지 않으면 **tombstone을 교체하지 않고 Phase 0부터 재수집**합니다. 이미
   `prepare`한 뒤에 모자란 것을 알면 늦습니다.
4. `prepare` 이후에는 challenge·arm·execute를 **즉시 연속으로** 진행합니다. 이 구간에 검토나
   상의를 넣지 않습니다.
5. 그럼에도 `BASELINE_EXPIRED`가 나면 **clear·편집·새 prepare를 시도하지 않고** maintainer에게
   보고하고 멈춥니다. 이 상태를 정리하는 것은 별도 안건입니다.

### 3. 교차검토의 전제 하나를 정정합니다 — approval delay는 재검사됩니다

요청서는 "`prepare`가 window 안에 끝나면 이후 approval delay가 baseline age를 재검사하지 않는
성질"을 확인해 달라고 했습니다. 추적 결과 **sentinel cycle에서는 그 전제가 성립하지 않습니다.**

freshness 게이트는 `arm()`에서, `execute()` 진입에서, 그리고 첫 write 가능성이 생기기 전까지는
**모든 action 앞에서** 다시 평가됩니다. 즉 승인 대기로 시간이 흘렀다면 첫 write 직전에 다시
막히고 `baseline_expired_before_first_write`로 fail-closed합니다. 게이트가 비활성화되는 시점은
`prepare` 성공이 아니라 **첫 possible write가 기록된 뒤**이며, 그 이후로는 복원 가능성을 유지하기
위해 의도적으로 열려 있습니다.

전제가 성립하는 곳은 따로 있습니다. 같은 게이트는 `record.cycle is not SENTINEL_QUALIFICATION`이면
즉시 통과하므로, **baseline restore cycle과 phase-5에는 capture-age 재검사가 없습니다.** 이는
결함이 아니라 설계입니다 — 그 시점에는 직전 sentinel cycle의 final verify가 장비 상태를 방금
재확인했기 때문입니다. 다만 승인 문구에서 "매 단계 age가 재검사된다"고 일반화하면 틀립니다.

### 4. sentinel slot은 manifest에 고정하지 않습니다 — 고정하면 오히려 틀립니다

본문 4번 항목의 승인 설명은 **다시 넣어야 합니다.** 다만 slot 번호를 이월하거나 manifest에 적는
방식은 안 됩니다. 현재 코드가 slot을 고르는 방식은 다음과 같습니다.

- fresh baseline의 432-byte schedule image를 검증한 뒤 index `0`부터 훑어 **wire 형태가 unused
  ZERO 또는 unused EE인 첫 slot**을 고릅니다. ZERO면 EE로, EE면 ZERO로 **뒤집습니다**.
- 둘 중 어느 형태도 아닌 slot은 건너뜁니다. 즉 **사용 중인 schedule 항목은 후보가 아닙니다.**
- 후보가 하나도 없으면 계획 수립 자체가 `INVALID_BASELINE`으로 실패합니다.
- 실행 시점에도 같은 함수로 slot과 기대 image를 다시 계산해 계획에 기록된 값과 대조하므로,
  장비가 그사이 drift했다면 다른 slot에 쓰는 대신 fail-closed합니다.

따라서 승인 설명은 "**이번 fresh capture에서 결정되는, 사용되지 않은 한 slot의 표현만 ZERO↔EE로
바꾼다. active schedule과 출력은 바뀌지 않으며, slot 번호는 이전 run에서 이월되지 않고 write
직전에 다시 검증된다**"가 정확합니다. 구체적 slot 번호는 fresh Phase 0 뒤에야 알 수 있으므로
승인 시점에 화면의 challenge에서 확인합니다.

### 5. 세 operation id는 nonce가 아니라 manifest digest에서 파생됩니다

본문 2단계는 "새 nonce 하나에서 파생되는 세 operation id"라고 적었습니다. 현재 코드는 nonce가
아니라 **manifest 전체의 SHA-256**에 도메인 라벨(`sentinel` / `baseline` / `final`)을 붙여
파생합니다. 실질적으로 더 강한 성질입니다 — manifest의 어떤 필드든 바뀌면 세 id가 모두 자동으로
새것이 되고, 기존 id를 재현하려면 manifest가 바이트 단위로 동일해야 합니다. fresh artifact 결속과
새 timing 값이 들어가는 이상 재사용은 구조적으로 불가능합니다. 문구를 그렇게 정정합니다.

`prepare` 직전 precheck는 sentinel과 baseline 두 id에 대해 기존 finalization 부재를 확인합니다.
phase-5 id는 그 시점에 아직 staging 전이므로 여기 포함되지 않고, staging하는 create 경로에서
같은 barrier로 확인됩니다. 두 확인을 하나로 뭉뚱그려 적지 않습니다.

### 6. 종료 조건과 두 grant는 현재 source에서 그대로 성립합니다

- `AppRestoreClaim`이 요구하는 네 값(sentinel receipt, baseline receipt, phase-5 operation id와
  record digest)은 변함이 없습니다. 종료 조건은 여전히 "두 cycle을 모두 `FINAL_VERIFIED`로 닫고
  qualified bundle과 staged phase-5 record까지 만드는 것"입니다.
- 한 `qualify` 명령 안에서 sentinel이 끝나고 baseline restore로 promote한 직후 **이미 받은 승인
  플래그가 명시적으로 해제되며**, baseline cycle은 `/dev/tty`에서 새 attended grant를 다시
  받습니다. 두 operation·두 grant는 코드에 실재합니다.
- phase-5 staging은 qualified bundle의 불변 timestamp에 결속된 journal write일 뿐이며 **device
  write는 `0`**입니다. staging 경로에 장비 관측 호출이 없습니다.
- 각 후속 cycle 전에 predecessor success barrier가 존재해야 한다는 요구는 작업 A로 코드에
  들어갔습니다. baseline cycle의 `arm`은 embedded chain 전체를 archive와 대조하고, park 경로도
  index 설치와 같은 claim 안에서 같은 chain을 확인합니다.

### 7. 실패 처리 순서 — park이 가능해졌다고 순서가 바뀌지는 않습니다

작업 B가 write-free 종결을 가능하게 했지만, 본문의 순서는 **그대로 유지합니다.**

1. 실패한 predicate와 실측값을 diagnostic에서 읽어 기록한다. **원인 분석이 먼저입니다.**
2. 그 다음에 좌초한 journal에 결속된 **새 disposition manifest**를 만든다. 기존 manifest는
   특정 journal digest에 결속되므로 재사용할 수 없습니다.
3. 별도 attended park 승인을 받는다. qualification 승인은 park 승인이 아닙니다.

**자동·즉시 재시도는 계속 `0`회입니다.** park 기구가 생겼다는 것은 좌초에서 안전하게 빠져나올
수단이 생겼다는 뜻이지, 재시도 문턱이 낮아졌다는 뜻이 아닙니다. v2에서의 실패는 이 문서의 여유
표가 틀렸다는 **새 정보**이므로 재시도가 아니라 분석으로 갑니다.

### 8. 승인 경계 — 이 요청으로 확장되지 않습니다

현재 사용자가 승인한 범위는 **plan 검토와 static source 추적까지**입니다. 이 addendum을 쓰는 동안
장비·앱·Home Assistant·private journal·archive·recovery supervisor 접촉은 `0`이며, 아래 세 경계는
서로를 함축하지 않습니다.

- **0단계** — private barrier·tombstone·readiness 확인. device network `0`이지만 private state를
  읽으므로 **별도 승인**이 필요합니다. 이 addendum은 그 승인을 갖고 있지 않습니다.
- **1단계** — fresh Phase 0. device **read**를 포함하므로 **별도 승인**이 필요합니다.
- **4~6단계** — device **write**. on-site 승인·감시·즉시 물리 차단을 요구하며, 위 두 승인 중
  어느 것으로도 대체되지 않습니다. 두 exact action plan을 모두 명시적으로 포함해야 하고, 두 번째
  challenge가 첫 승인의 범위를 넓히지 않습니다.

작업 A와 작업 B가 착지했다는 사실은 위 어느 승인도 만들지 않습니다. 그 둘은 **좌초 시 종결
수단**을 만든 것이지 **좌초하지 않는다는 근거**를 만든 것이 아닙니다. 여유 표는 여전히 네 pair
실측에 기반한 예측이고, v1에서 예측은 이미 한 번 틀렸습니다.

### 이 재검증으로 새로 확정된 것

- baseline restore cycle 좌초의 종결 경로 부재는 해소됐습니다. 본문의 그 노출 서술은 대체됩니다.
- `max_observation_age_seconds`는 capture freshness와 handoff validity를 겸용하는 overload입니다.
  실현 불가능한 것은 아니고, 실측에서 `1800초`로 재도출했습니다. 비용은 두 창을 독립적으로 조일
  수 없다는 것이며, 분리는 별도 후속 안건입니다.
- **만료된 `PREPARED`/`ARMED` journal에는 종결 경로가 없습니다.** device write `0`이지만
  비-terminal dead-end이며, 이것이 값을 좁힐 수 없는 직접 근거입니다.
- sentinel freshness 게이트는 첫 possible write까지 반복 평가되고, baseline·phase-5에는 적용되지
  않습니다.
- 세 operation id는 manifest digest 파생이므로 재사용이 구조적으로 불가능합니다.
- sentinel slot은 fresh capture에서 결정되고 write 직전에 재검증되므로 manifest에 고정하지
  않습니다.

---

## 2026-09-05 addendum 정정 (2차) — 교차검토 R1~R5

앞 addendum을 고치지 않고 여기서 대체합니다. R1·R2는 이 검토가 도착하기 전에 같은 결론으로
이미 반영됐으므로 아래에는 그 사실만 적고, 새로 정정하는 것은 R3·R4·R5와 비차단 한 건입니다.

### R1·R2 — 이미 반영됨 (교차 확인 일치)

- **R1**: "한 숫자로 C/D와 G를 만족시킬 수 없다"는 제 결론은 과장이었습니다. 보존 실측을 넣어
  철회했고 `max_observation_age_seconds = 1800`을 실측에서 재도출했습니다. "C·D를 무의미하게
  만든다"는 문장도 함께 사라졌습니다. 검토가 지적한 근거와 제 재도출이 같은 값에 도달했습니다.
  덧붙여 이 검토가 정확히 짚은 것 — C/D와 G는 **같은 절대 budget을 나눠 쓰지 않습니다.** D는
  `captured_at`부터 sentinel 첫 wire까지이고, G는 **새로 찍힌 baseline receipt의 완료 시각부터**
  Q2/App manifest 생성까지의 별도 창입니다. 두 창은 이어 붙지 않습니다. source split을 선행
  작업으로 올린 것도 함께 내렸습니다 — 증거 없는 범위 확장이었습니다.
- **R2**: 만료된 `PREPARED`/`ARMED`의 비-terminal dead-end와 다섯 가지 절차 요구를 이미
  넣었습니다. `arm`에서 만료되면 `PREPARED`가, `execute`에서 만료되면 `ARMED`가 그대로 남는다는
  구분도 포함돼 있습니다.

### R3 — 표의 E 행이 틀렸습니다

표에서 action별 observation 신선도를 "초 미만"이라고 적었습니다. **틀립니다.** runtime의 stamp
순서를 다시 읽으면 `observed`는 raw read 직후에 찍히지만 `received`는 **disconnect와 identity-after
재탐색이 끝난 뒤**에 찍힙니다. discovery는 고정 창을 쓰므로 `received − observed`는 초 미만이
아니라 **약 5초에 local overhead를 더한 값**입니다. 보존된 identity-after 실측도 그 범위였습니다.

E가 비구속이라는 **결론 자체는 유지됩니다** — acquisition `30초`가 이미 더 좁기 때문입니다. 그러나
근거 수치를 틀리게 적으면 다음 사람이 이 필드를 초 단위로 조여도 된다고 오해합니다. 표의 E 행은
"약 `5초` + local overhead, `30초` 미만이므로 비구속"으로 읽어야 합니다.

### R4 — baseline restore와 phase-5의 age 우회는 이유가 다릅니다

앞 addendum은 둘을 한 문장으로 묶어 "직전 sentinel cycle의 final verify가 장비 상태를 방금
재확인했기 때문"이라고 적었습니다. **baseline restore에는 맞고 phase-5에는 틀립니다.**

- **baseline restore** — sentinel cycle의 final verify 직후에 이어지므로 "방금 확인했다"가 실제
  이유입니다.
- **phase-5** — 앱 실험이 도는 **긴 시간 동안 staged된 채로 있다가** 나중에 exact restore에
  쓰입니다. 이때 capture age를 다시 검사하면 시간이 지났다는 이유로 복원 자체를 막게 됩니다.
  phase-5가 age를 무시하는 이유는 "방금 확인"이 아니라 **오래 지나도 rollback과 restore를
  strand시키지 않기 위해서**입니다. 이것은 sentinel 게이트가 첫 possible write 이후 스스로 열리는
  것과 같은 원리입니다.

### R5 — sentinel slot은 attended challenge에 표시되지 않습니다

앞 addendum은 "구체적 slot 번호는 승인 시점에 화면의 challenge에서 확인한다"고 적었습니다.
**틀립니다.** 코드를 확인하면 challenge가 보여 주는 것은 operation id, cycle, phase,
`baseline_sha256`, `action_plan_sha256`, `journal_context_sha256`, `authority_chain_sha256`,
완료/전체 action 수, inflight digest, 그리고 다음 action id뿐입니다. action id는
`{index:02d}-{role}-{kind}` 형식이라 slot 번호를 담지 않습니다. `sentinel_slot`은 `PREPARED`
record의 action 안에만 있습니다.

따라서 그 문장을 삭제하고 다음 절차로 대체합니다. 이 순서는 R2의 "prepare 이후에 검토를 넣지
말라"와도 맞물립니다.

1. fresh Phase 0 artifact로 **network-none offline에서 record와 action plan을 먼저 도출**합니다.
   baseline 합성과 계획 수립은 장비 접촉 없이 결정론적으로 재현됩니다.
2. 그 offline 산출물에서 **sentinel slot 번호와 target digest를 검토**합니다. 승인자가 실제로 볼
   것을 여기서 봅니다.
3. 그 다음에 live `prepare`를 실행합니다.
4. live `PREPARED`의 `action_plan_sha256`이 offline에서 검토한 값과 **일치하는지 대조**합니다.
5. 일치할 때만 challenge token을 제시합니다.

승인 설명의 내용 자체는 유지됩니다 — "이번 fresh capture에서 결정되는, 사용되지 않은 한 slot의
표현만 ZERO↔EE로 바꾸고 active schedule과 출력은 바꾸지 않는다". 바뀌는 것은 그 slot을 **언제
어디서 확인하는가**이며, 답은 challenge 화면이 아니라 위 2단계의 offline 검토입니다.

### 비차단 정정 — sentinel cycle의 write 수는 상한이지 확정값이 아닙니다

본문 4~6단계는 "sentinel cycle은 8 action 전부가 write"라고 적었습니다. 정확하지 않습니다.
`_action_satisfied`는 safe fallback과 qualify sentinel에도 적용되므로, 장비가 이미 그 상태이면
해당 action은 `ALREADY_SATISFIED`로 건너뜁니다. 따라서 승인 문구는 **"exact 8-action plan, 최대
8 device writes"**가 정확합니다.

과거 baseline과 동일한 상태에서 시작한다면 8 write를 예상할 수 있지만, 그것은 **예상이지
확정이 아니며 fresh Phase 0 결과 이전에는 확정할 수 없습니다.** 예상과 상한을 분리해 적습니다.
baseline restore cycle의 "exact 6-action plan, 최대 6 write"는 이미 같은 형식이었습니다.

### 이 정정으로 달라지지 않는 것

hardware는 계속 **NO-GO**입니다. 이 절은 승인 경계를 넓히지 않으며, 0단계(private state read),
1단계(device read), 4~6단계(device write)의 분리도 그대로입니다. 이 정정을 쓰는 동안 장비·앱·
Home Assistant·private journal·archive·recovery supervisor 접촉은 `0`입니다.

---

## 2026-09-05 addendum 정정 (3차) — 교차검토 R6~R11

앞 두 addendum을 고치지 않고 여기서 대체합니다. 이 절 끝에 **운영자가 앞뒤를 합성하지 않아도
되도록 최종 실행 checklist를 한 곳에 모았습니다.**

### R6 — age 산술의 timestamp가 게이트가 쓰는 것과 달랐습니다

2차 addendum은 `1244.134초` 요구와 `555.866초` 여유를 적었습니다. **그 숫자를 철회합니다.**
게이트가 쓰는 `captured_at`은 제가 대입한 값이 아닙니다.

- artifact별 `captured_at` = **채택된 pair의 두 read completion 중 max**입니다. series 전체의 종료
  시각이 아닙니다.
- baseline의 `captured_at` = `min(primary, corroborating)`입니다.

즉 제가 넣은 `01:21:10.832179Z`(primary series 수집 종료)은 채택 pair의 raw-read completion보다
뒤일 수 있어 capture age를 **작게** 보이게 합니다. 반대편에서는 첫 action의 completion이 실제
first wire보다 뒤이므로 age를 **크게** 보이게 합니다. 두 오차의 순효과는 보존된 산출물을 읽지
않고는 확정할 수 없고, **이 작업은 private 산출물을 읽지 않았습니다.**

저장소 안의 값만으로 안전하게 말할 수 있는 것은 **보수적 봉투**뿐입니다.

- `captured_at ≥ primary series 시작 01:20:19.165612Z`
- `first wire ≤ 첫 action completion 01:41:34.966170Z` (그 action은 `written_verified`이므로
  wire는 completion보다 앞섭니다)
- 따라서 이전 run의 실제 게이트 요구 `≤ (01:41:34.966170 − 01:20:19.165612) + 20 =`
  **`1295.800558초`**
- `1800`의 **증명 가능한 최소 여유** = **`504.199442초` (약 `8분 24초`)**

`1800` 결론은 유지됩니다. 바뀌는 것은 근거의 정직성입니다 — 이것은 "실제 요구와 여유"가 아니라
**"요구의 상한과 여유의 하한"**입니다. 실제 `captured_at`과 first-wire 시각은 보존 산출물에서
읽지 않았습니다. 앞선 commit 본문의 `1244.134` / `555.866`도 이 봉투로 대체합니다.

### R7 — 표 E와 acquisition `30`의 관계가 여전히 틀렸습니다

2차 addendum은 E를 "약 5초 + local overhead이고 acquisition `30`이 더 좁아 비구속"이라고
고쳤습니다. 앞부분은 맞고 **뒷부분이 틀립니다.** action별 `_fresh()`는
`received − observed ≤ max_observation_age`만 검사합니다. `max_final_acquisition_duration`은
baseline admission과 final pair capture에만 적용되고 이 site에는 **적용되지 않습니다.**

정확한 문언은 이렇습니다 — **"보존 실측상 약 `5초` + local overhead로 `1800`보다 매우 작다.
network·identity 경로가 정상 종료하는 관측이며, `30초` acquisition 게이트가 이 site를 직접
구속하지는 않는다."** 표의 F는 순서 부등식상 final total `≤ 30`이 `completed − observed`도
덮으므로 그대로 둡니다.

### R8 — offline 도출은 production store와 명시적으로 격리해야 합니다

R5의 "network-none offline 도출"을 그대로 읽으면 production `prepare` 경로나 CLI를 부를 수
있습니다. **그러면 고정 journal의 tombstone이 즉시 교체됩니다.** 도출은 순수 경로로 한정합니다.

- `build_verified_baseline`과 계획 수립 함수 같은 **결정론적 순수 경로만** 사용하고, 고정
  journal·archive·guard에는 접근하지 않습니다. 필요하면 완전히 격리된 복사본에서 수행합니다.
- **두 write plan을 모두 미리 도출·검토합니다** — sentinel 8-action과 baseline 6-action.
  baseline plan은 promotion 뒤에야 live에 나타나므로 그때 처음 보면 늦습니다.

대조 실패의 경계도 공개합니다. live `prepare` 뒤 sentinel의 `action_plan_sha256`이 offline
검토값과 다르거나, promotion 뒤 baseline digest가 다르면 **`PREPARED` journal이 남고 이는 Work B
park 대상이 아닙니다**(R2와 같은 dead-end). 따라서 mismatch 시 **token을 제시하지 않고 즉시
중단·보고하며, clear·편집·재prepare를 시도하지 않습니다.** 이 상황 자체를 줄이기 위해 `prepare`
직전에 source·config·manifest·artifact digest를 exact로 재확인합니다.

### R9 — Phase 0 device-read 범위를 숫자로 고정합니다

"primary와 corroborating 두 series"만으로는 승인 범위가 되지 않습니다. 다음을 고정합니다.

| 항목 | 값 | 근거 |
|---|---|---|
| series 수 | `2` (primary, corroborating) | 서로 다른 plan·series의 독립 확증 |
| series당 predeclared pair 수 | `2` | 이전 run과 동일하며 채택 여지를 남기는 최소값 |
| transport policy | `explicit_only` | exact-restore 증거의 유일한 허용 정책 |
| discovery/control timeout | `5초` | manifest의 identity age와 정합 |
| cadence | manifest에 고정한 단일 값 | 수집기가 사전 선언 없이는 실행하지 않음 |
| 채택 ordinal | **마지막**(pair 수 `2`이면 ordinal `1`) | 아래 참조 |
| retry | **`0`** | 수집기는 각 ordinal을 한 번만 잡고 invalid read를 재시도가 아니라 증거로 남깁니다 |
| 실패 조건 | 두 series 중 하나라도 `2/2` accepted가 아니면 **`NO-GO`, 재수집** | 완화 금지 |

**채택 ordinal을 마지막으로 두는 이유**는 예산 때문입니다. artifact의 `captured_at`은 채택된 pair
두 read completion의 max이므로, 늦은 ordinal을 채택할수록 `captured_at`이 늦어지고 first-wire
예산이 그만큼 늘어납니다. baseline의 `captured_at`은 두 series의 min이므로, **두 series를 서로
가깝게 수집하고 양쪽 모두 늦은 ordinal을 채택**하는 것이 예산 측면에서 최선입니다. 이전 run도
각 series에서 ordinal `1`을 채택했습니다.

### R10 — "충분한 여유"를 숫자로 고정합니다

사람의 즉석 판단에 남기면 R2의 dead-end를 그대로 재현합니다. 다음을 고정합니다.

**`prepare` 시점의 최소 잔여 first-wire 예산 = `720초` (12분).**

도출 근거입니다.

- attended challenge의 최대 허용 수명 `300초` — 낭독과 입력이 이 안에 끝나야 합니다.
- 발급된 authority의 수명 `300초` — `arm` 이후 `execute`가 첫 write에 이 안에 도달해야 합니다.
- 첫 action 직전의 fresh read 비용 — discovery `5초`와 identity-after `5초`의 고정 창에
  connect·authenticate·read가 더해집니다. 보수적으로 `60초`를 둡니다.
- 합 `660초`에 여유를 더해 `720초`로 올립니다.

따라서 **`prepare`는 `captured_at + 1060초`(약 `17분 40초`)보다 늦게 시작하지 않습니다.**
`captured_at + 1780초`가 first-wire deadline이고 거기서 `720초`를 뺀 값입니다. Phase 0 직후 이 두
시각을 계산해 표시하고, 남은 예산이 `720초` 미만이면 **tombstone을 건드리지 않고 Phase 0부터
재수집합니다.**

**challenge 재발급은 최대 `1회`**입니다. 재발급 뒤에도 잔여 예산이 `720초` 이상일 때만
계속하고, 아니면 중단하고 재수집합니다. 이전 run에서 challenge가 한 번 만료됐으므로 이것은
가상의 경우가 아니며, 무제한 재발급을 허용하면 예산이 조용히 소모되어 dead-end로 갑니다.

이 숫자들은 maintainer 확인 대상이지만 **비어 있지는 않습니다.** hardware 승인 전에 maintainer가
다른 값을 정하면 그 값과 근거로 이 절을 대체합니다.

### R11 — 0단계는 순수 read가 아닙니다

앞 addendum은 0단계를 "private state read"라고 적었습니다. 정확하지 않습니다. production journal
store·receipt archive·guard가 고정 root를 열 때 `_probe_hardware_safety_root()`가 `0600`
`.write-probe.<hex>` 파일을 **생성·기록·fsync·검증**합니다. 즉 host store에 일시적 write가
발생합니다.

승인 경계 문구를 다음으로 고칩니다 — **"private state read + 정상적인 일시 host-store writability
probe. durable journal·archive의 semantic mutation은 `0`, device write는 `0`."** 이전 run 기록도
같은 구분을 했습니다.

### 비차단 두 건

- **G는 D를 만족하면 자동으로 충족되는 수학적 관계가 아닙니다.** 앞 addendum의 "자동으로 넉넉히
  만족시킨다"는 표현을 정정합니다. 정확히는 **절차적 precommit**입니다 — phase-5 staging 직후
  device-network `0` 상태에서 곧바로 Q2/App manifest를 만들기로 미리 약속하기 때문에 `1800`이
  충분한 것입니다. 이때 control과 ASYNC 두 Q2 manifest를 **함께** 만들고 cutoff를 고정합니다.
- addendum 이전 본문은 역사로 남아 있으므로 아래에 최종 순서를 한 곳에 모읍니다.

### 최종 실행 checklist — 이 절만 보고 실행할 수 있어야 합니다

승인은 세 경계로 나뉘며 서로를 함축하지 않습니다: **0단계**(private read + 일시 probe),
**1단계**(device read), **4~6단계**(device write).

1. **0단계** — permanent barrier·tombstone·archive 교차확인, `_require_operation_not_parked()`
   거부 확인, supervisor exited, Home Assistant 미연결, 앱 완전 종료, 모든 code writer 부재,
   미완 journal·intent·latch 부재, UID/GID와 store owner·mode 정합, 실제 import된 `jebao_flow`의
   경로·bytes attestation. **별도 승인 필요.**
2. **1단계** — fresh Phase 0. series `2`개, 각 predeclared pair `2`, `explicit_only`, timeout
   `5초`, retry `0`, 채택 ordinal은 마지막. 두 series 모두 `2/2` accepted가 아니면 `NO-GO`.
   각 series가 coherence `≤20초`·total acquisition `≤30초`를 통과해야 합니다. **별도 승인 필요.**
3. **2단계** — v2 manifest. `version: 2`, `max_final_pair_gap_seconds: 20`,
   `max_final_acquisition_duration_seconds: 30`, `max_observation_age_seconds: 1800`,
   discovery/control timeout `5초`, identity age `5초`, 역할별 accepted latent manual `30`·`89`,
   configured `30..100` step `1`, attended maximum `60`·`89`, safe fallback
   `Constant / Flow 30 / Frequency 20`. 세 operation id는 manifest digest에서 파생되므로 별도로
   적지 않습니다. **sentinel slot은 적지 않습니다.**
4. **offline 도출** — 순수 경로로 sentinel 8-action과 baseline 6-action plan을 도출하고 slot·
   target digest·두 `action_plan_sha256`을 검토합니다. 고정 journal·archive·guard 접근 `0`.
5. **예산 계산** — `captured_at + 1780초`(first-wire deadline)와 `captured_at + 1060초`
   (`prepare` 최종 시각)를 계산해 표시합니다. 잔여 예산 `< 720초`이면 tombstone을 건드리지 않고
   1단계로 돌아갑니다.
6. **승인자 대기 완료** — 검토와 상의는 여기까지입니다. `prepare` 이후에는 넣지 않습니다.
7. **3단계** — `prepare` 직전 source·config·manifest·artifact digest exact 재확인, 그 다음
   `prepare`. live `action_plan_sha256`을 4번의 offline 값과 대조. 불일치면 **token 없이 중단·
   보고**, clear·편집·재prepare 금지.
8. **4~6단계** — challenge → arm → execute를 **즉시 연속으로**. challenge 재발급 최대 `1회`이며
   그 뒤에도 잔여 예산 `≥ 720초`일 때만 계속. sentinel은 **8-action plan, 최대 8 device writes**.
   promotion 뒤 baseline digest를 offline 값과 대조하고, 그 cycle은 **새 attended grant**를 받습니다.
   baseline은 **6-action plan, 최대 6 device writes**. phase-5 staging은 device write `0`.
   **on-site 승인·감시·즉시 물리 차단 필요.**
9. **직후** — device-network `0` 상태에서 control과 ASYNC 두 Q2 manifest를 함께 만들고 cutoff를
   고정합니다. baseline receipt 완료로부터 `1800초` 안입니다.
10. **7단계** — receipt·bundle digest, 두 cycle의 실측 coherence/acquisition span, 선행조건 2의
    상태 변화를 append-only로 기록합니다.

**실패 시**: 원인 분석 → 새 disposition manifest → 별도 attended park. 자동 재시도 `0`회.
`BASELINE_EXPIRED`나 digest mismatch로 `PREPARED`/`ARMED`가 남으면 **clear·편집·재prepare 없이
maintainer 보고 후 중단**합니다.

hardware는 이 절 이후로도 **NO-GO**입니다. 이 정정을 쓰는 동안 장비·앱·Home Assistant·private
journal·archive·recovery supervisor 접촉은 `0`입니다.

---

## 2026-09-05 addendum 정정 (4차) — 교차검토 R12~R13

앞 절들을 고치지 않고 여기서 대체합니다. 두 건 모두 제가 **코드가 실제로 하는 일보다 강하게**
쓴 것입니다.

### R12 — nonce 정정이 반대 방향으로 과했습니다

1차 addendum은 "현재 코드는 nonce가 아니라 manifest 전체의 SHA-256에서 파생한다"고 썼습니다.
preimage에 대해서는 맞지만, 그렇게만 읽으면 **새 nonce를 만들라는 요구가 사라집니다.** 실제로는
`ExactRestoreOperationManifest`에 `operation_nonce`(32 hex)가 **필수 필드로 남아 있고**
`manifest_sha256`에 포함됩니다.

정확한 문언으로 대체합니다 — **"세 operation id는 nonce 하나만으로 파생되지 않는다. 새
`operation_nonce`를 포함한 full manifest digest에 cycle label을 붙여 파생한다. fresh artifact,
policy, nonce 중 어느 하나라도 바뀌면 세 id가 모두 바뀐다."**

**2단계의 새 nonce 생성 요구는 그대로 유지됩니다.** 본문 2단계가 요구한 그대로입니다. §3 P1은
아니지만 manifest completeness 오류였고, 그대로 뒀다면 새 manifest에서 nonce를 이월해도 된다고
읽힐 수 있었습니다.

### R13 — sentinel drift가 fail-closed한다는 서술은 source와 반대입니다

1차 addendum은 "실행 시점에도 같은 함수로 slot과 기대 image를 다시 계산해 계획에 기록된 값과
대조하므로, 장비가 그사이 drift했다면 다른 slot에 쓰는 대신 fail-closed합니다"라고 썼습니다.
**틀렸습니다.** 그 재계산의 입력은 현재 장비 상태가 아니라 **record에 박제된 baseline의 schedule
image**입니다. 즉 이것은 immutable plan의 **내부 일관성 검사**이지 device drift 검사가 아닙니다.

wire 직전 경로를 다시 읽으면 이렇습니다.

- pre-write `_fresh()`는 identity binding이 baseline과 같은지, 시간 창이 유효한지를 봅니다.
  schedule 내용의 drift는 여기서 걸리지 않습니다.
- `_action_satisfied()`는 "이미 목표 상태인가"만 판정합니다. 아니면 skip하지 않고 write합니다.
- 그 write는 **baseline에서 파생된 planned image 전체**를 씁니다.

따라서 Phase 0 이후 제3자가 schedule을 바꿔 놓았다면, 그 변경은 fail-closed로 막히는 것이 아니라
**baseline 파생 image로 덮일 수 있습니다.**

**새 gate를 만들라는 뜻이 아닙니다.** 이 저장소는 §2에서 pre-write gate를 더 추가하지 않기로
했고, 그 방향을 지킵니다. 바꾸는 것은 문언입니다.

- slot과 target은 **fresh baseline에서 결정**되고, wire 직전에는 immutable action이 **그
  baseline 파생 target과 일치하는지만** 재검증됩니다.
- Phase 0부터 첫 wire까지의 device drift를 막는 것은 **코드 predicate가 아니라 운영 전제**입니다 —
  앱·Home Assistant·모든 writer의 부재와 단일 제어권. 0단계 preflight가 이것을 확인하는 이유가
  바로 여기 있고, R10의 예산 상한도 이 창을 짧게 유지하려는 것입니다.

### R13 (계속) — 승인 설명의 범위도 좁힙니다

"active schedule과 출력은 바뀌지 않는다"는 문장을 **sentinel schedule write 자체로 한정**합니다.
cycle 전체는 그렇지 않습니다. sentinel 앞의 safe fallback 두 action이 outer control frame을
`TimerOFF + independent + Constant / Flow 30 / Frequency 20`으로 **의도적으로 바꿉니다.** 출력이
바뀌는 것은 이 계획이 감수하기로 한 것이지 일어나지 않는 일이 아닙니다.

on-site approver가 받아야 하는 문장으로 대체합니다.

> 이 cycle은 먼저 두 장비를 bounded safe fallback(`TimerOFF + independent + Constant / Flow 30 /
> Frequency 20`)으로 바꿔 **출력 상태를 실제로 변경**합니다. 그 다음 sentinel write는 이번 fresh
> capture에서 결정된, **사용되지 않는 한 slot의 표현만** `ZERO ↔ EE`로 바꾸며 active schedule
> 항목은 건드리지 않습니다. 마지막 두 쌍의 action이 원래 schedule과 원래 outer control을
> 되돌립니다. 실패가 남길 수 있는 dwell 상태는 그 safe fallback이거나 부분 복원 상태입니다.

앞 절의 최종 checklist 8번에 있는 승인 문구도 이 문장으로 읽습니다.

### 이 정정으로 달라지지 않는 것

hardware는 계속 **NO-GO**입니다. R13은 새 코드 gate를 요구하지 않으며, 이 절은 어떤 승인도
넓히지 않습니다. 이 정정을 쓰는 동안 장비·앱·Home Assistant·private journal·archive·recovery
supervisor 접촉은 `0`입니다.

---

## 2026-09-05 addendum 정정 (5차) — 교차검토 R14~R18과 **최종 실행 문서**

앞 절들을 고치지 않고 여기서 대체합니다. **이 절의 checklist가 유일한 실행 문서입니다.** 앞의
어떤 checklist도 이것으로 대체되며, 운영자는 이 절 하나만 읽고 실행할 수 있어야 합니다.

### R14 — `prepare` 이후에는 "재수집"으로 돌아갈 수 없습니다

3차 addendum의 R10은 "challenge 재발급 뒤에도 잔여 예산이 부족하면 중단하고 재수집한다"고
적었습니다. **불가능한 복구를 지시한 것입니다.** challenge는 `PREPARED`가 설치된 **뒤에**
발급됩니다. 그 시점에는 tombstone이 이미 교체됐으므로, 새 Phase 0을 얻어도 `create`/`prepare`가
live `PREPARED`에 막히고 Work B park 대상도 아닙니다(R2의 dead-end와 같은 상태).

시간 경계를 둘로 나눕니다.

- **`prepare` 이전에 잔여 `< 720초`** — tombstone 무변경. 이번 device-read operation을 종료하고
  보고합니다. Phase 0 재수집은 **새 별도 device-read 승인**을 받은 뒤에만 가능합니다.
- **`prepare` 이후의 challenge 실패·만료 또는 잔여 부족** — **재수집·clear·편집·재prepare 모두
  금지**입니다. `PREPARED`(arm 뒤라면 `ARMED`) 그대로 두고 중단·보고합니다. 재발급은 **같은
  record에서 1회**, 그리고 **발급 시점 잔여가 `720초` 이상일 때만** 허용합니다. 아니면 dead-end를
  인정하고 멈춥니다.

**"최대 1회"는 보장된 재시도가 아니라 조건부 가능성입니다.** 첫 challenge가 실제로 만료된 뒤에도
`720초`가 남으려면 `prepare` 시작 시 잔여가 `720 + 300 = 1020초`를 넘었어야 합니다. 그 조건을
만족하지 못하면 재발급 기회는 존재하지 않습니다.

### R15 — cadence를 숫자로 고정합니다: `30초`

3차 addendum의 표는 cadence를 "manifest에 고정한 단일 값"이라고만 적었습니다. 그것은 승인
범위가 아닙니다. **`30초`로 고정합니다.**

근거는 이전 run의 보존된 span입니다. 2-pair series의 span은 primary `51.666567초`,
corroborating `52.817943초`였고, 채택된 ordinal 1의 attempt는 각각 약 `21.7초`와 `22.795초`였습니다.
cadence `30초`를 가정하면 `30 + 21.666 = 51.666`, `30 + 22.795 = 52.795`로 관측 span과 각각
1밀리초·23밀리초 수준에서 일치합니다. 즉 이전 run은 cadence `30초`로 수집했고, 그 값을 바꿀
별도 근거가 없습니다.

**2/2 실패나 `prepare` 이전 예산 부족 뒤의 "재수집"은 자동 retry가 아닙니다.** 그 Phase 0의 승인
범위는 거기서 종료되고, 새 2×2 series는 **새 device-read 승인**을 받아야 합니다.

### R16 — 승인 축은 셋이 아니라 **넷**입니다

3차 checklist는 0단계(private read + probe), 1단계(device read), 4~6단계(device write) 셋만
열거했습니다. **`prepare`가 빠졌습니다.** 3단계는 permanent tombstone을 `PREPARED`로 교체하는
**durable semantic mutation**이고, 이 문서 스스로 §3 판정에서 journal 표면에 도달한다고 적었습니다.
2단계의 manifest 저장, qualify 중 생성되는 receipt·archive·bundle·phase-5 staged record, 9단계의
두 Q2 manifest도 모두 host-private durable write입니다.

따라서 **repository-maintainer의 host-state mutation 승인**을 별도 축으로 둡니다. 사전에 열거할
정확한 범위는 다음입니다.

- 새 v2 manifest 파일의 저장
- **tombstone → `PREPARED` 교체** (이 항목은 독립 명시 승인 없이는 실행하지 않습니다)
- 성공 시 생성될 sentinel receipt, baseline receipt, operation finalization, qualified bundle,
  staged phase-5 record
- 직후 생성할 control·ASYNC 두 Q2 manifest

**0단계 probe 승인도, device-read 승인도, on-site write 승인도 이 승인을 함축하지 않습니다.**

### R17 · R18 — 최종 실행 checklist (이 절이 유일한 실행 문서)

앞의 모든 checklist를 대체합니다. R6~R16을 흡수했고 외부 합성이 필요 없습니다.

**승인 축 넷** — 서로를 함축하지 않으며, 각각 별도로 받아야 합니다.

| 축 | 범위 | 주체 |
|---|---|---|
| A | private state read + 일시 host-store writability probe. durable semantic mutation `0`, device write `0` | repository maintainer |
| B | device **read** (Phase 0 수집) | repository maintainer |
| C | **host-state mutation** — manifest 저장, tombstone→`PREPARED`, receipt·archive·bundle·staged record, 두 Q2 manifest | repository maintainer |
| D | device **write** (4~6단계). 현장 감시와 즉시 물리 차단 포함 | on-site hardware approver |

---

**0. 사전 재검증 — 승인 A**

permanent barrier·tombstone·archive 교차확인, `_require_operation_not_parked()` 거부 확인,
recovery supervisor exited, **Home Assistant가 장비에 붙어 있지 않음**, 앱 완전 종료(background와
다른 클라이언트 포함), 모든 code writer 부재(`jebao-flowd`·one-shot writer 포함), 미완 journal·
비terminal intent·emergency latch 부재, UID/GID와 store owner·mode 정합, **실제로 import된
`jebao_flow`의 경로와 bytes가 reviewed commit과 일치**(마운트 digest만으로 부족), reviewed
source/image attestation.

**1. fresh Phase 0 — 승인 B**

series `2`개(primary, corroborating), 서로 다른 plan·series, 각 predeclared pair `2`,
**cadence `30초`**, transport policy `explicit_only`, discovery/control timeout `5초`,
**retry `0`**(수집기는 각 ordinal을 한 번만 잡고 invalid read를 증거로 남깁니다),
**채택 ordinal은 마지막(ordinal `1`)**.

- 두 장비의 outer six-field와 432-byte schedule image가 byte-exact 일치해야 합니다.
- 각 series가 coherence `≤20초`와 total acquisition `≤30초`를 통과해야 합니다.
- 하나라도 `2/2` accepted가 아니면 **`NO-GO`**. 재수집은 자동 retry가 아니며 **새 승인 B**가
  필요합니다.
- 기존 parked operation의 artifact를 재사용하지 않습니다.

**2. v2 manifest 작성 — 장비 접촉 `0`, 저장은 승인 C**

`version: 2`, **새 `operation_nonce`(32 hex, 이월 금지)**, `max_final_pair_gap_seconds: 20`,
`max_final_acquisition_duration_seconds: 30`, `max_observation_age_seconds: 1800`,
discovery/control timeout `5초`, identity age `5초`, 역할별 accepted latent manual `30`·`89`,
configured range `30..100` step `1`, attended maximum `60`·`89`, safe fallback
`Constant / Flow 30 / Frequency 20`, 1단계 fresh artifact 결속.

세 operation id(sentinel·baseline·final)는 **새 nonce를 포함한 full manifest digest + cycle
label**에서 파생되므로 따로 적지 않습니다. **sentinel slot도 적지 않습니다.**

**3. offline 도출 — 장비·고정 store 접촉 `0`**

순수 경로(`build_verified_baseline`과 계획 수립 함수)만 사용하고 고정 journal·archive·guard에는
접근하지 않습니다. 필요하면 완전히 격리된 복사본에서 수행합니다.
**두 write plan을 모두 도출·검토합니다** — sentinel 8-action과 baseline 6-action. 각 plan의
sentinel slot, target digest, `action_plan_sha256`을 여기서 검토합니다.

**4. 예산 계산 — 여기가 되돌아갈 수 있는 마지막 지점**

- first-wire deadline = `captured_at + 1780초`
- `prepare` 최종 시각 = `captured_at + 1060초`
- 잔여 `< 720초`이면 **tombstone을 건드리지 않고 종료·보고**합니다. 재수집은 새 승인 B.
- 잔여가 `1020초`를 넘지 않으면 challenge 재발급 기회는 **존재하지 않습니다.** 그 사실을 알고
  진행할지 여기서 결정합니다.

`captured_at`은 채택 pair의 두 read completion 중 max이며, 두 series의 min입니다.

**5. 승인 대기 완료** — 검토·상의·승인 확보는 여기까지입니다. 이 뒤에는 넣지 않습니다.

**6. `prepare` — 승인 C (tombstone 교체는 독립 명시 승인)**

직전에 source·config·manifest·artifact digest를 exact로 재확인합니다. `prepare` 뒤 live
`action_plan_sha256`을 3단계 offline 값과 대조합니다. **불일치하면 token을 제시하지 않고 즉시
중단·보고하며, clear·편집·재prepare를 시도하지 않습니다.**

**7. sentinel cycle — 승인 D**

**arm 직전에 장비 TCP connection과 writer 존재를 다시 확인합니다.** 하나라도 보이면 integration을
끄거나 밀어붙이지 않고 **token 미입력 · `NO-GO` · 보고**로 닫습니다.

challenge → arm → execute를 **즉시 연속**으로 진행합니다. **exact 8-action plan, 최대 8 device
writes**(장비가 이미 그 상태이면 skip될 수 있으므로 8은 상한이지 확정값이 아닙니다).

on-site approver가 받아야 하는 문장:

> 이 cycle은 먼저 두 장비를 bounded safe fallback(`TimerOFF + independent + Constant / Flow 30 /
> Frequency 20`)으로 바꿔 **출력 상태를 실제로 변경**합니다. 그 다음 sentinel write는 이번 fresh
> capture에서 결정된 **사용되지 않는 한 slot의 표현만** `ZERO ↔ EE`로 바꾸며 active schedule
> 항목은 건드리지 않습니다. 마지막 두 쌍의 action이 원래 schedule과 원래 outer control을
> 되돌립니다. 실패가 남길 수 있는 dwell 상태는 그 safe fallback이거나 부분 복원 상태이며, 역할
> B의 latent manual `Random / Flow 89`가 복원 중 잠시 active가 될 수 있습니다.

Phase 0부터 첫 wire까지 device drift를 막는 것은 **코드 predicate가 아니라 단일 제어권이라는
운영 전제**입니다. 그래서 0단계와 각 arm 직전의 확인이 실제 방어입니다.

**8. baseline restore cycle — 승인 D (별도 grant)**

promotion 뒤 baseline `action_plan_sha256`을 3단계 offline 값과 대조합니다. 불일치면 6단계와 같은
경계입니다 — token 미제시, 중단·보고, clear·편집 금지.

**baseline arm 직전에도 TCP connection과 writer 부재를 다시 확인합니다.** sentinel arm에서 한
번, 여기서 한 번, **두 번 모두 필요합니다.**

**exact 6-action plan, 최대 6 device writes**(정상 경로는 write 4 + skip 2). 이 cycle은 sentinel의
승인과 **다른 operation id, 다른 attended grant**이며, 두 번째 challenge가 첫 승인의 범위를 넓히지
않습니다. 승인 문구에 **두 exact action plan이 모두 명시**돼야 합니다.

**9. phase-5 staging — device write `0`, host write는 승인 C**

qualified bundle의 불변 timestamp에 결속된 journal write입니다.

**10. 두 Q2 manifest — 장비 접촉 `0`, host write는 승인 C**

phase-5 staging 직후 device-network `0` 상태에서 **control과 ASYNC 두 Q2 manifest를 함께** 만들고
cutoff를 고정합니다. **baseline receipt 완료로부터 `1800초` 안**이어야 합니다. 이것이 `1800`이
충분하다는 근거이며, 수학적 함의가 아니라 **절차적 precommit**입니다.

**11. 기록** — receipt·bundle digest, 두 cycle의 실측 coherence/acquisition span, 선행조건 2의
상태 변화를 append-only로 기록합니다.

---

**실패 시 — 어느 phase에서 좌초했는지에 따라 경로가 다릅니다.** 원인 분석이 항상 먼저이고,
자동·즉시 재시도는 어느 분기에서도 `0`회입니다. **park은 만능 탈출구가 아닙니다** — Work B가
연 범위는 아래 첫 줄뿐이며, 다른 상태에서 park을 시도하면 필요한 recovery를 하지 않은 채 부분
복원 dwell만 길어집니다.

| 좌초 상태 | 경로 |
|---|---|
| `AWAITING_FINAL_VERIFY`이고 action prefix가 완전하며 inflight·error·final evidence가 모두 없음 | **park 가능.** diagnostic 기록과 precommit 뒤 그 journal digest에 결속된 **새 disposition manifest**를 만들고(기존 manifest는 재사용 불가) **별도 attended park 승인**을 받습니다 |
| `RECOVERY_REQUIRED`, 불완전한 prefix, inflight 존재, write 도중 실패 | **park 대상이 아닙니다.** 기존 attended recovery·rollback 경로만 사용하며, 별도 승인 없이 자동 resume `0`회입니다 |
| `PREPARED` 또는 `ARMED`에서 freshness·digest·challenge 문제 | **park·recover·clear·재prepare 모두 불가.** 그대로 두고 중단·보고합니다 |
| `FINAL_VERIFIED`에서 archive·ACK 문제 | 기존 idempotent finalization·clear 경계로 처리합니다 |

write가 끼인 채 실패한 경우는 두 번째 줄입니다. 이 계획은 그 recovery 경로를 바꾸지 않습니다.

hardware는 이 절 이후로도 **NO-GO**입니다. 위 네 승인이 각각 명시적으로 이뤄지기 전에는 어떤
단계도 실행하지 않습니다. 이 문서를 쓰는 동안 장비·앱·Home Assistant·private journal·archive·
recovery supervisor 접촉은 `0`입니다.

---

## 2026-09-05 addendum 정정 (6차) — 교차검토 R20~R23

앞 절의 최종 checklist를 고치지 않고 여기서 **네 항목을 대체**합니다. 나머지는 그대로 유효하며,
실행 시 이 절의 대체 내용을 반영해 읽습니다.

### R20 — cadence 근거가 순환 계산이었습니다

5차 addendum은 채택 attempt를 "약 `21.666`초"라고 적고 cadence 30과의 차이를 "1밀리초"라고
했습니다. **그 `21.666`은 span에서 30을 뺀 값이므로, 30을 가정해 얻은 수로 30을 입증한
순환입니다.** 보존된 실측 attempt는 다르고, 그것을 써야 합니다.

| series | 채택 attempt (보존값) | `30 + attempt` | series span (보존값) | 차이 |
|---|---|---|---|---|
| primary | `21.650386049초` | `51.650386049초` | `51.666567초` | **`16.180951` ms** |
| corroborating | `22.795387102초` | `52.795387102초` | `52.817943초` | **`22.555898` ms** |

**cadence `30초` 결론은 그대로 방어됩니다.** 두 series 모두 20밀리초 안팎의 잔차만 남고, 그
잔차는 pair 시작 스케줄링의 지터로 설명됩니다. 바뀌는 것은 근거의 독립성입니다 — 이제 span과
attempt라는 **서로 다른 두 보존값**에서 cadence를 역산합니다.

### R21 — 승인 C의 시점이 모순이었고 Phase 0 host write가 어디에도 없었습니다

5차 checklist는 2단계에서 "manifest 작성·저장은 승인 C"라고 하면서 5단계에 가서야 "승인 대기
완료"라고 했습니다. **승인 C 없이 먼저 저장하거나, 정확한 plan을 보기 전에 C를 받아야 하는
순서 모순입니다.** 그리고 Phase 0 수집기가 필수로 남기는 owner-only plan·series·raw artifact의
durable host write는 **어느 승인 축에도 들어 있지 않았습니다.** 실제 수집기 동작과 불일치합니다.

다음으로 대체합니다.

- **승인 B(device read)의 범위에 그 operation이 반드시 남기는 owner-only collector plan·series·
  raw artifact의 durable host write를 명시적으로 포함**합니다. Phase 0은 읽기만 하는 단계가
  아닙니다.
- **2단계**는 메모리 또는 격리 경로에서 v2 manifest **candidate**를 만드는 데 그칩니다.
  production·private 고정 경로 저장은 `0`입니다.
- **3단계 offline plan 도출**과 **4단계 예산 계산**을 마친 뒤, exact candidate·두 action plan·
  digest를 **놓고** 승인 C와 D를 확보합니다.
- 그 다음 **승인 C 아래에서** manifest를 owner-only 경로에 persist하고 exact reload한 뒤,
  **별도로** tombstone→`PREPARED` prepare를 실행합니다.

승인 C는 성공 시 뒤따를 receipt·finalization·bundle·staged record·두 Q2 manifest까지 미리 승인할
수 있지만, **candidate를 보기 전의 포괄 승인으로 써서는 안 됩니다.**

### R22 — `1020초` 문장을 조건부로 좁힙니다

5차 checklist는 "잔여가 `1020초`를 넘지 않으면 재발급 기회가 존재하지 않는다"고 절대화했습니다.
그것은 **첫 challenge가 `300초`를 전부 소모하고 만료된 경우에만** 참입니다. 빠른 거부나 TTY
실패로 끝났다면 `prepare` 잔여가 `900초`여도 재발급 시점에 `720초` 이상이 남을 수 있습니다.

정확한 문언으로 대체합니다 — **"첫 challenge가 `300초`를 전부 소모해 만료된 뒤에도 재발급 여유를
보장하려면 `prepare` 시점 잔여가 `1020초`를 넘어야 한다. 그 밖의 실패는 보장이 아니라 실제
재발급 시점의 잔여가 `720초` 이상인지로 판정한다."**

### R23 — 실패 표가 서로 다른 controller entry를 한 줄에 섞었습니다

5차 표는 `RECOVERY_REQUIRED`·불완전 prefix·inflight·mid-write 실패를 모두 "attended
recovery/rollback"으로 보냈습니다. **틀린 운영 지시입니다.** 불완전 prefix를 `recover`로 보내면
CLI가 `recovery_not_required`로 거부합니다. phase와 사유로 나눕니다.

| 상태 | 올바른 entry |
|---|---|
| `RESTORING`, prefix 불완전, inflight 없음 | `recover`가 **아닙니다.** qualify 재개 시 attended reauthorize를 거쳐 resume합니다 |
| `RESTORING`, inflight 있음 | 역시 attended reauthorize가 먼저입니다. 재개된 execute는 그 inflight를 **관측으로만 조정하고 재전송하지 않습니다.** 불일치하거나 관측에 실패할 때만 latch되어 `RECOVERY_REQUIRED`가 됩니다 |
| `RECOVERY_REQUIRED` | **이때만** `recover` entry입니다 |
| `ARMED`, authority 만료 | fresh baseline 예산이 남아 있으면 reauthorize로 진행 가능합니다. 다만 `BASELINE_EXPIRED`라면 reauthorize로 해결되지 않는 **dead-end**입니다 |
| `PREPARED`/`ARMED`, freshness·digest·challenge 문제 | park·recover·clear·재prepare 모두 불가. 중단·보고 |
| `AWAITING_FINAL_VERIFY`, prefix 완전, inflight·error·final evidence 없음 | **park 가능.** 새 disposition manifest와 별도 attended park 승인 |
| `FINAL_VERIFIED`, archive·ACK 문제 | 기존 idempotent finalization·clear 경계 |

**모든 분기에 공통**입니다 — 첫 실패 뒤 **중단·보고·새 precommit·새 attended 승인**이며 자동
resume은 `0`회입니다. 위 표의 "reauthorize 후 resume"은 자동 재개가 아니라 **사람이 새 grant를
발급한 뒤의 재개**를 뜻합니다.

### 이 정정으로 달라지지 않는 것

hardware는 계속 **NO-GO**입니다. 승인 축 넷과 5차 checklist의 나머지 구조는 유효하며, 이 절은
그중 네 항목만 대체합니다. 이 정정을 쓰는 동안 장비·앱·Home Assistant·private journal·archive·
recovery supervisor 접촉은 `0`입니다.

### R21 보강 — 승인 C는 중간 journal transition까지 덮어야 합니다

위 R21은 승인 C의 범위를 "시작(manifest 저장, tombstone→`PREPARED`)과 최종 산출물"로 적었습니다.
**그 사이의 live journal semantic transition이 빠져 있습니다.** 그것들이야말로 exactly-once와
rollback을 보존하는 핵심 durable write입니다.

승인 C의 범위를 다음으로 닫습니다 — **qualification 동안 controller가 계약상 수행하는 journal·
archive semantic write 전체**. 구체적으로 최소한 다음을 포함합니다.

- 새 v2 manifest의 owner-only 경로 persist와 exact reload
- **tombstone → `PREPARED` 교체** (독립 명시 승인 없이는 실행 금지)
- attended authority와 그 activation의 기록, `ARMED` 전이
- **action별 inflight intent의 durable 기록**(wire 경계 이전) 과 그 해소
- completed-action prefix의 누적 기록
- `RESTORING` / `AWAITING_FINAL_VERIFY` / `FINAL_VERIFIED` 전이
- sentinel → baseline **promotion**(직전 predecessor의 영구 barrier 기록 포함)
- sentinel receipt, baseline receipt, operation finalization, qualified bundle, staged phase-5
  record
- 직후의 control·ASYNC 두 Q2 manifest

`ExactRestoreGuard`의 epoch 자체는 process-local이지만 deployment lock·latch·고정 root는 접근·
검증되며, 고정 root를 여는 것만으로 일시 writability probe가 발생합니다(승인 A). 따라서 C 문구는
경로 열거가 아니라 **"계약상 수행하는 write set 전체"**로 닫고, **예상 밖 경로나 범위 밖 파일
write가 관측되면 즉시 중단·보고**합니다.

### R20 보강 — cadence는 재구성이지 이전 값의 증명이 아닙니다

위 R20은 잔차 표를 제시한 뒤 "**이전 run은 cadence `30초`로 수집했고**"라고 적었습니다. 그렇게
쓸 수 없습니다. 저장소 문서에는 **`requested_cadence` 필드 자체가 없고** series span과 ordinal
attempt만 있습니다. 두 값의 차이가 `30.016`·`30.023`초라는 것은 강한 **재구성**이지 이전 요청값이
정확히 `30.000`이었다는 직접 증명이 아니며, 저는 그 값이 들어 있는 private plan을 읽지 않았습니다.

정확한 문언으로 대체합니다 — **"보존된 span과 attempt가 `30초` cadence precommit과 `16`·`23`
밀리초 안에서 정합하고, 다른 값을 고를 근거가 없다. 따라서 새 실행의 cadence를 `30초`로
**선택**한다."** 이것은 새 실행에 대한 선택이지 과거에 대한 확정이 아닙니다.

같은 규율이 이 문서 전체에 적용됩니다. **산술이 맞아떨어진다는 것은 그 값이 실제로 그렇게
기록돼 있었다는 증명이 아닙니다.** 보존 산출물에서 읽지 않은 값은 재구성으로 적고, 재구성에
기대어 새 값을 고를 때는 그 선택이 무엇에 근거했는지를 함께 적습니다.

### R24 · R25 보강 — 최종 checklist의 두 문구를 대체합니다

**R24 — "각 plan의 sentinel slot"은 존재하지 않습니다.** 계획 생성기는 `SENTINEL_QUALIFICATION`
cycle에서만 두 개의 `QUALIFY_SENTINEL` action과 그 `sentinel_slot`·image digest를 넣습니다.
**baseline 6-action plan에는 sentinel action도 slot도 없습니다.** 3단계 offline 검토 항목을 다음으로
대체합니다.

> sentinel plan의 **두 sentinel slot과 image target digest**, 그리고 **두 plan 각각의 전체
> action·target과 `action_plan_sha256`**.

**R25 — operation id preimage의 suffix를 "cycle label"이라고 부르면 phase-5에서 틀립니다.**
sentinel은 `sentinel`, baseline restore는 `baseline` 라벨을 쓰지만, phase-5 final restore는 같은
`BASELINE_RESTORE` cycle을 가지면서도 별개의 `final` 라벨을 씁니다. 이 문서는 이미 "baseline과
phase-5는 cycle이 같아 depth로 구분한다"고 확정했으므로, cycle label이라는 표현을 남기면 문서
내부 모순입니다. 2단계 문구를 다음으로 대체합니다.

> 세 operation id는 새 `operation_nonce`를 포함한 **full manifest digest**에 **서로 다른 domain
> label(`sentinel` / `baseline` / `final`)**을 붙여 파생됩니다. 라벨은 cycle이 아니라 역할을
> 가리키며, 그래서 cycle이 같은 baseline과 phase-5도 서로 다른 id를 갖습니다.

두 정정 모두 대상과 id 결속에 대한 설명 오류이며, 나머지 checklist 구조는 그대로 유효합니다.
hardware는 계속 **NO-GO**이고 이 절을 쓰는 동안 장비·앱·Home Assistant·private state 접촉은 `0`입니다.

---

# 최종 실행 문서 (2026-09-05, R26 반영) — **이 절만이 실행에 사용됩니다**

> **앞의 모든 checklist와 addendum은 역사로 남습니다. 실행에 사용하지 않습니다.** 5차 checklist,
> 6차 addendum의 대체 지시, R20·R21 보강, R24·R25 정정은 모두 **이 절에 이미 반영돼 있습니다.**
> 운영자는 다른 절을 참조하거나 치환을 합성할 필요가 없으며, 그렇게 해서도 안 됩니다.
>
> 이 문서는 **PLAN ONLY**입니다. **각 단계는 아래 표가 그 단계에 요구하는 승인 축을 실행 전에
> 명시적으로 받아야 하며, 어느 축도 다른 축을 함축하지 않습니다.** 승인은 한 번에 모아 받는 것이
> 아니라 단계에 맞춰 나뉩니다 — A는 0단계 전에, B1은 1단계 전에, **B2·C·D는 exact candidate와 두
> action plan을 본 뒤 5단계에서**, 그리고 **B3와 park host·park consent는 실패 후 park을 실제로
> 선택한 경우에만** 요구합니다. hardware는 **NO-GO**입니다.

## 승인 축 — 여섯이며 서로를 함축하지 않습니다

| 축 | 범위 | 주체 |
|---|---|---|
| **A** | private state read + 고정 root를 여는 것만으로 발생하는 **일시 host-store writability probe**. durable semantic mutation `0`, device write `0`. **이 축은 0단계에 한정되지 않고 전체 승인 sequence 동안의 모든 expected fixed-root open에 수반되는 probe를 포괄합니다** — 아래 참조 | repository maintainer |
| **B1** | 1단계 Phase 0의 device **read**, 그리고 그 operation이 **반드시 남기는** owner-only collector plan·series·raw artifact의 **durable host write**. Phase 0은 읽기만 하는 단계가 아닙니다 | repository maintainer |
| **B2** | **7·8단계의 최초 실행과, 아래 재개표로 승인된 continuation에서** controller가 계약상 수행하는 모든 device read — 각 action 이전의 fresh read, already-satisfied 분기의 두 번째 read, write 이후의 explicit verification·reconciliation 관측, 그리고 마지막 final pair capture. 여기에 딸린 discovery·identity 트래픽을 포함합니다. 재개용 B2는 **이름만 같은 별도 권한이 아니라 같은 축**이며, 재개할 때마다 그 범위로 다시 확인·승인합니다 | repository maintainer |
| **B3** | 실패 후 park을 선택했을 때에 한해, 마지막 completion 뒤의 새 strict artifact 수집(device read)과 그 collector durable write | repository maintainer |
| **C** | **host-state mutation** — 아래 "승인 C의 범위" 전체 | repository maintainer |
| **D** | 두 cycle의 bounded device **write** (7·8단계). 현장 감시와 즉시 물리 차단 포함 | on-site hardware approver |

**B2는 D를 함축하지 않고 D도 B2를 함축하지 않습니다.** qualification cycle의 device 접촉은 write만이
아닙니다 — `execute()`는 각 action 앞뒤로 장비를 읽고 마지막에 final pair를 잡습니다. 그 read들은
1단계 collector read(B1)가 아니며 D의 "write"에도 들어가지 않으므로, **B2 없이는 승인되지 않은
장비 접촉**이 됩니다. B2는 exact candidate와 두 action plan을 본 뒤 5단계에서 확보합니다.

### 승인 C의 범위

경로 열거가 아니라 **qualification 동안 controller가 계약상 수행하는 journal·archive semantic
write set 전체**입니다. 최소한 다음을 포함합니다.

- 새 v2 manifest의 owner-only 경로 persist와 exact reload
- **tombstone → `PREPARED` 교체** — 이 항목은 **독립 명시 승인 없이는 실행하지 않습니다**
- attended authority와 그 activation의 기록, `ARMED` 전이
- **action별 inflight intent의 durable 기록**(wire 경계 이전)과 그 해소
- completed-action prefix의 누적 기록
- `RESTORING` / `AWAITING_FINAL_VERIFY` / `FINAL_VERIFIED` 전이
- sentinel → baseline **promotion**(직전 predecessor의 영구 barrier 기록 포함)
- sentinel receipt, baseline receipt, operation finalization, qualified bundle, staged phase-5 record
- **receipt에 결속된 live journal `clear`** — destructive semantic mutation이므로 "전체 write set"에
  묻어 두지 않고 별도로 승인합니다
- **그 clear 직후의 phase-5 successor 설치**와, clear와 설치 사이의 **journal 부재 구간**. 그 구간에서
  중단되면 재개는 기존 idempotent 경계를 따르며 새로 prepare하지 않습니다
- 직후의 control·ASYNC 두 Q2 manifest

**예상 밖 경로나 이 범위 밖의 파일 write가 관측되면 즉시 중단·보고합니다.** C는 candidate와 두
action plan과 digest를 **보고 난 뒤에** 받으며, 그 전의 포괄 승인으로 쓰지 않습니다.

### 승인 A의 probe는 0단계에만 있지 않습니다

고정 root는 **열릴 때마다** 일시 writability probe를 수행합니다. journal store의 부모 open, receipt
archive의 root open, guard의 lease 획득이 각각 그 경로를 지나므로, probe는 0단계에서 한 번이
아니라 **`prepare`와 qualify가 잡는 모든 claim·lease에서 반복**됩니다.

따라서 **승인 A는 전체 승인 sequence 동안 expected store·guard·archive open에 수반되는 probe
전부를 포괄합니다.** 6~10단계에서 발생하는 이 일시 write들도 A의 범위이며 C로 다시 세지 않습니다.
**횟수를 미리 고정하지 않습니다** — 범위는 "expected open에 수반되는 probe"로 닫고, 그 밖의 경로나
그 밖의 write가 관측되면 위의 중단·보고 규칙이 그대로 적용됩니다.

---

## 0단계 — 사전 재검증 · **승인 A**

permanent barrier·tombstone·archive 교차확인, `_require_operation_not_parked()`가 parked
operation id를 계속 거부하는지, recovery supervisor exited, **Home Assistant가 장비에 붙어 있지
않음**, 앱 완전 종료(background와 다른 폰·태블릿·클라이언트 포함), 모든 code writer 부재
(`jebao-flowd`·one-shot writer 포함), 미완 recovery journal·비terminal intent·emergency latch 부재,
실행 UID/GID가 store owner·mode와 호환되고 owner-only store가 writable한지, **실제로 import된
`jebao_flow`의 경로와 bytes가 reviewed commit과 일치**(마운트 digest만으로는 부족), reviewed
source/image attestation.

## 1단계 — fresh Phase 0 · **승인 B1**

| 항목 | 값 |
|---|---|
| series 수 | `2` (primary, corroborating), 서로 다른 plan·series |
| series당 predeclared pair | `2` |
| **cadence** | **`30초`** |
| transport policy | `explicit_only` |
| discovery/control timeout | `5초` |
| **retry** | **`0`** — 수집기는 각 ordinal을 한 번만 잡고 invalid read를 재시도가 아니라 증거로 남깁니다 |
| **채택 ordinal** | **마지막(pair 수 `2`이면 ordinal `1`)** |

- 두 장비의 outer six-field와 432-byte schedule image가 **byte-exact 일치**해야 합니다.
- **각 series가** coherence `≤20초`와 total acquisition `≤30초`를 통과해야 합니다.
- 하나라도 `2/2` accepted가 아니면 **`NO-GO`**. 재수집은 자동 retry가 아니며 그 Phase 0의 승인
  범위는 종료됩니다. **새 승인 B1**을 받아야 새 2×2 series를 수집할 수 있습니다.
- 기존 parked operation의 artifact를 재사용하지 않습니다.
- observation age가 만료되면 완화하지 말고 재수집합니다.

## 2단계 — v2 manifest **candidate** 작성 · 장비 접촉 `0` · **고정 경로 저장 `0`**

**메모리에서만** candidate 모델·canonical bytes·digest를 도출합니다. **persistent file write `0`**
입니다 — 고정 경로가 아니더라도 파일로 쓰면 그것은 host-state write이고 private artifact 참조를
담습니다. 승인 C 이전에 그런 write를 만들지 않습니다. 저장은 6단계, 승인 C 아래에서만 합니다.

- `version: 2`
- **새 `operation_nonce`(32 hex) — 이월 금지**
- `max_final_pair_gap_seconds: 20`, `max_final_acquisition_duration_seconds: 30`
- `max_observation_age_seconds: 1800`
- discovery/control timeout `5초`, identity age `5초`
- 역할별 accepted latent manual `30`·`89`
- configured range `30..100` step `1`
- 역할별 attended maximum `60`·`89`
- safe fallback `Constant / Flow 30 / Frequency 20`
- 1단계 fresh artifact 결속(primary + corroborating)

세 operation id는 **새 `operation_nonce`를 포함한 full manifest digest**에 **서로 다른 domain
label(`sentinel` / `baseline` / `final`)**을 붙여 파생됩니다. 라벨은 cycle이 아니라 역할을
가리키며, 그래서 cycle이 같은 baseline과 phase-5도 서로 다른 id를 갖습니다. **id를 manifest에 따로
적지 않습니다. sentinel slot도 적지 않습니다.**

## 3단계 — offline plan 도출 · 장비 접촉 `0` · **고정 store 접촉 `0`**

승인 B1 아래 이미 보존된 raw artifact를 **read-only로 읽고**, 순수 경로(baseline 합성과 계획 수립
함수)를 **현재 reviewed image·process 안에서** 실행합니다. 고정 journal·archive·guard에는 접근하지
않으며, **새 persistent scratch·복사본 write는 `0`**입니다 — 2단계와 같은 이유입니다. 복사본이
꼭 필요하다면 그 생성 자체가 별도 host-write 승인 범위이므로, 여기서는 memory·process only가
최소이자 기본입니다.

**두 write plan을 모두 도출합니다** — sentinel 8-action과 baseline 6-action. 검토 항목은:

- **sentinel plan의 두 sentinel slot과 image target digest** (baseline plan에는 sentinel action도
  slot도 없습니다)
- **두 plan 각각의 전체 action·target과 `action_plan_sha256`**

## 4단계 — 예산 계산 (1차) · 승인 상의 이전

`captured_at`은 **채택 pair의 두 read completion 중 max**이며, **두 series의 min**입니다.

- first-wire deadline = **`captured_at + 1780초`**
- `prepare` 최종 시각 = **`captured_at + 1060초`**
- 최소 잔여 first-wire 예산 = **`720초`**
- 잔여 `< 720초`이면 **tombstone을 건드리지 않고 종료·보고**합니다. 재수집은 **새 승인 B1**.
- 이 계산은 **1차**입니다. 승인 상의에 시간이 걸리므로 **6단계에서 `prepare` 직전에 다시
  계산합니다.** 여기서 통과했다는 것이 그때도 통과한다는 뜻이 아닙니다.
- **첫 challenge가 `300초`를 전부 소모해 만료된 뒤에도 재발급 여유를 *보장*하려면** `prepare`
  시점 잔여가 **`1020초`를 넘어야** 합니다. 그 밖의 실패(빠른 거부, TTY 실패 등)는 보장이 아니라
  **실제 재발급 시점의 잔여가 `720초` 이상인지**로 판정합니다. 잔여가 `1020초` 이하라면 재발급이
  보장되지 않는다는 것을 **알고** 진행할지 여기서 결정합니다.

## 5단계 — 승인 B2·C·D 확보

**exact candidate manifest, 두 action plan, 그 digest를 놓고** **B2**(7·8단계 controller device
read), **C**(host-state mutation), **D**(두 cycle의 bounded device write)를 받습니다. 셋은 서로를
함축하지 않으므로 **각각** 확보합니다. 검토·상의·승인 확보는 **여기까지**입니다. 이 뒤에는 넣지
않습니다.

## 6단계 — manifest persist와 `prepare` · **승인 C**

1. 직전에 source·config·manifest·artifact digest를 **exact로 재확인**합니다.
2. **예산을 다시 계산합니다.** 4단계에서 충분했더라도 5단계의 승인 상의가 길어졌다면 지금은
   부족할 수 있습니다. `now < captured_at + 1060초`이고 first-wire 잔여가 `≥ 720초`인지 **다시
   계산해 표시**합니다. **하나라도 실패하면 manifest save `0`, `prepare` `0`, tombstone 불변으로
   중단·보고**합니다. **되돌아갈 수 있는 진짜 마지막 predicate가 여기입니다** — `prepare` 뒤에는
   recapture·clear·재prepare가 모두 불가능합니다.

   재개하려면 **1단계로 돌아갑니다.** 새 승인 **B1**으로 fresh artifact를 다시 수집하고, 새
   `operation_nonce`로 새 candidate와 두 plan을 만든 뒤, **5단계의 B2·C·D를 전부 다시 받습니다.**
   새 artifact와 새 nonce는 manifest digest와 **세 operation id를 모두 바꾸므로**, 이전 exact
   candidate를 보고 받은 B2·C·D는 **더 이상 같은 대상에 대한 승인이 아닙니다.**
3. 승인 C 아래에서 manifest를 owner-only 경로에 **persist하고 exact reload**합니다.
4. **별도의 행위로** tombstone → `PREPARED` prepare를 실행합니다(독립 명시 승인 항목).
5. **위 2번의 예산 확인 시점부터 첫 challenge 표시까지 전체가 `60초`를 넘지 않습니다.** cap의
   시작점은 `prepare` 완료가 아니라 **2번의 최종 예산 확인**입니다 — 그 확인 이후의
   manifest persist·exact reload, `prepare`, qualify process 시작, 첫 challenge 표시가 모두 예산을
   소비하는데 **코드에는 그 구간의 bound가 없습니다.** 그래야 `720 = 60 + 300 + 300 + 60`이 실제
   검사 시점과 맞습니다.

   cap을 넘겼다면 **token `0` · 중단 · 보고**하고, **live journal과 manifest 경로를 모두 exact
   reload해** 어디까지 진행됐는지 기록합니다. cap의 시작점이 persist보다 앞이므로 **manifest가
   이미 durable하게 저장돼 있을 수 있습니다** — journal semantic mutation이 `0`인 것과 host durable
   mutation이 `0`인 것은 다릅니다. 다음 두 가지로 갈립니다.
   - **journal이 아직 tombstone**이면 journal·tombstone은 불변이므로 재개가 가능합니다. 다만
     **1단계로 돌아가야 합니다** — 새 승인 **B1**으로 fresh artifact를 다시 수집하고, 새
     `operation_nonce`로 새 candidate와 두 plan을 만든 뒤, **5단계의 B2·C·D를 전부 다시
     받습니다.** 새 artifact와 새 nonce는 manifest digest와 **세 operation id를 모두 바꾸므로**,
     이전 exact candidate를 보고 받은 B2·C·D는 **더 이상 같은 대상에 대한 승인이 아닙니다.**
     **방금 저장된 manifest는 stale candidate로 보존하고 재사용은 `0`**이며, 새 C가 새 persist와
     **필요하다면 기존 경로의 overwrite까지** 포함해야 합니다.
   - 이미 **`PREPARED`**면 **recapture·clear·재prepare 모두 불가**입니다 — 실패표의 dead-end 행
     그대로입니다.

**live `action_plan_sha256`은 어디서 읽는가** — `prepare`의 public status는 이 digest를 내보내지
않습니다(phase·cycle·action count 등만 있습니다). 그것을 표시하는 표면은 **attended challenge
summary**이며, 거기에 `baseline_sha256`·`action_plan_sha256`·`journal_context_sha256`·
`authority_chain_sha256`이 함께 출력됩니다. 따라서 대조는 challenge가 **발급된 뒤, token을 입력하기
전에** 이뤄집니다. 순서는 7·8단계에 그대로 반영돼 있습니다. 이 경로를 쓰는 이유는 기존 진단
표면이라 source 변경이 필요 없기 때문입니다. 승인 A 아래에서 canonical live journal을 read-only로
model-validate해 같은 값을 얻는 방법도 가능하지만, 그 경우 사용한 exact 방법을 기록에 남깁니다.

## 7~9단계는 **하나의 `qualify` 실행**입니다

7·8·9단계는 별도 명령이 아닙니다. **동일한 reviewed·attested `qualify` process 한 번**이 sentinel
`FINAL_VERIFIED` → predecessor barrier 기록 → baseline promotion → 두 번째 challenge와 grant →
baseline `FINAL_VERIFIED` → bundle persist → receipt 결속 clear → phase-5 successor staging까지
이어서 수행합니다. **sentinel만 끝내는 외부 entry는 없습니다.** 두 cycle 사이에 이 process를
종료하거나 **다른 exact-restore·device-touch command를 시작하지 않습니다** — 그렇게 하면
`PREPARED`/`FINAL_VERIFIED` 중간 경계를 만들게 됩니다.

**승인 A 범위의 read-only host inspection은 예외이며, 오히려 필수입니다.** 아래 7·8단계가 token
직전에 요구하는 TCP connection·process 확인이 바로 그것이고, 그것까지 금지하면 이 문서가 요구하는
검사를 수행할 수 없습니다. 금지 대상은 장비를 건드리거나 journal을 바꾸는 command입니다.

운영자가 개입하는 지점은 **두 attended challenge에서 token을 보류하고 있는 동안**뿐입니다. 아래
7·8단계의 검사는 모두 그 두 pause 안에서 수행합니다.

## 7단계 — sentinel cycle · **승인 B2 + D**

**token 직전 순서를 이대로 지킵니다.** challenge를 띄운 뒤 approver가 검토하는 동안에도 다른
writer가 붙을 수 있으므로, 확인은 challenge **이전이 아니라 token 직전**에 해야 합니다.

1. challenge summary를 표시합니다. **아직 token은 입력하지 않습니다.**
2. summary의 `action_plan_sha256`을 3단계 offline **sentinel** plan 값과 대조합니다.
3. **token을 계속 보류한 상태에서** 장비 TCP connection 부재와 앱·Home Assistant·daemon·다른
   one-shot/qualifier process 부재를 **다시 확인**합니다.
4. 둘 중 하나라도 실패하면 **token 미입력 · `NO-GO` · 보고**입니다. integration을 끄거나 밀어붙이지
   않으며, clear·편집·재prepare도 하지 않습니다.
5. **둘 다 통과했을 때만** token → arm → execute를 즉시 연속으로 진행합니다.

이 검사들은 challenge 안에서 이뤄지므로 **전체가 challenge의 `300초` 제한 안에 끝나야 합니다.**
challenge 이전에 같은 확인을 추가로 해도 되지만, 그것이 **token 직전 확인을 대체하지는
않습니다.**

**writer 부재 검사의 예외는 정확히 하나입니다** — 이 시점에는 challenge를 발급한 바로 그
`qualify` process가 살아 있고 write-capable entry를 갖고 있습니다. 따라서 **현재 challenge를
발급한 단 하나의 reviewed·attested exact-restore process만 expected writer로 허용**합니다.

식별 방법을 정확히 나눕니다. 0단계에는 qualifier PID가 **존재할 수 없습니다** — 그 단계는 예외
없이 모든 writer process 부재를 요구하기 때문입니다. 따라서 PID를 0단계와 대조할 수 없습니다.
0단계가 attestation하는 것은 **reviewed source·image·config와 command shape**이고, token 시점의
그 하나뿐인 qualifier가 거기에 **exact match**하는지를 봅니다. **PID 동일성은 첫 challenge부터
두 번째 challenge까지의 단일 invocation 안에서만** 요구합니다(위 "하나의 `qualify` 실행").
조건에 맞는 process가 둘 이상이거나 식별 불가능하면 **token `0` · `NO-GO`**입니다. 그 하나를 제외한 앱·Home Assistant·
daemon·다른 one-shot/qualifier process는 부재여야 하고, **기존 device TCP connection은 `0`**이며,
현재 qualifier도 token 이전에는 device TCP connection이 `0`이어야 합니다. (0단계에서는 예외 없이
**모든** writer process 부재가 맞습니다. 예외는 challenge가 떠 있는 이 시점에만 적용됩니다.)

**exact 8-action plan, 최대 8 device writes** — 장비가 이미 그 상태이면 action이 skip될 수 있으므로
`8`은 상한이지 확정값이 아닙니다.

on-site approver가 받아야 하는 문장:

> 이 cycle은 먼저 두 장비를 bounded safe fallback(`TimerOFF + independent + Constant / Flow 30 /
> Frequency 20`)으로 바꿔 **출력 상태를 실제로 변경**합니다. 그 다음 sentinel write는 이번 fresh
> capture에서 결정된 **사용되지 않는 한 slot의 표현만** `ZERO ↔ EE`로 바꾸며 active schedule
> 항목은 건드리지 않습니다. 마지막 두 쌍의 action이 원래 schedule과 원래 outer control을
> 되돌립니다. 실패가 남길 수 있는 dwell 상태는 그 safe fallback이거나 부분 복원 상태이며, 역할
> B의 latent manual `Random / Flow 89`가 복원 중 잠시 active가 될 수 있습니다.

Phase 0부터 첫 wire까지 device drift를 막는 것은 **코드 predicate가 아니라 단일 제어권이라는 운영
전제**입니다. wire 직전 검사는 immutable action이 baseline 파생 target과 일치하는지만 보며, 제3자가
바꿔 놓은 schedule은 거부되지 않고 덮입니다. 그래서 0단계와 각 arm 직전의 확인이 실제 방어입니다.

## 8단계 — baseline restore cycle · **승인 B2 + D (D는 별도 grant)**

같은 `qualify` 실행이 promotion을 마치고 **두 번째 challenge**를 띄웁니다. 7단계와 **동일한 순서**를
그대로 반복합니다.

1. 두 번째 challenge summary 표시, **token 보류**.
2. summary의 `action_plan_sha256`을 3단계 offline **baseline** plan 값과 대조.
3. **token 보류 상태에서** 장비 TCP connection과 writer 부재를 **다시 확인**(예외는 7단계와 같이
   현재 challenge를 발급한 그 하나의 qualifier process뿐).
4. 하나라도 실패하면 **token 미입력 · `NO-GO` · 보고**. clear·편집·재prepare 금지.
5. 둘 다 통과했을 때만 token → arm → execute.

**sentinel arm에서 한 번, 여기서 한 번 — 두 번 모두 필요하며, 두 번째가 첫 번째로 대체되지
않습니다.** 이 검사들도 두 번째 challenge의 `300초` 안에 끝나야 합니다.

**exact 6-action plan, 최대 6 device writes**(정상 경로는 write `4` + `ALREADY_SATISFIED` skip `2`).
이 cycle은 sentinel과 **다른 operation id, 다른 attended grant**이며, 두 번째 challenge가 첫
승인의 범위를 암묵적으로 넓히지 않습니다. 현장 승인 문구에 **두 exact action plan이 모두 명시**돼야
합니다.

## 9단계 — phase-5 staging · device write `0` · host write는 **승인 C**

qualified bundle의 불변 timestamp에 결속된 journal write입니다. **이 단계는 운영자가 따로
실행하는 것이 아니라**, baseline cycle이 성공한 뒤 **같은 `qualify` 실행**이 승인 C의 범위 안에서
이어서 수행합니다 — bundle persist, receipt 결속 clear, journal 부재 구간, phase-5 successor
설치가 한 흐름입니다.

## 10단계 — 두 Q2 manifest · 장비 접촉 `0` · host write는 **승인 C**

phase-5 staging 직후 device-network `0` 상태에서 **control과 ASYNC 두 Q2 manifest를 함께** 만들고
cutoff를 고정합니다. **baseline receipt 완료로부터 `1800초` 안**이어야 합니다. 이것이 `1800`이
충분하다는 근거이며, 수학적 함의가 아니라 **절차적 precommit**입니다.

## 11단계 — 기록

receipt·bundle digest, 두 cycle의 실측 coherence/acquisition span, 선행조건 2의 상태 변화를
append-only로 기록합니다. 계획과 실행을 나중에 대조할 수 있도록 다음도 함께 남깁니다 —
**각 cycle의 실제 action outcome vector**(write / `ALREADY_SATISFIED` skip /
`VERIFIED_AFTER_UNCERTAIN`), **device read·write 횟수를 B1 / B2 /(발생했다면) B3로 구분해**,
**여섯 승인 각각의 실제 범위**, 그리고 **실패 분기에 진입했는지와 어느 행이었는지**.

---

## 실패 시 — phase와 사유로 갈립니다

**원인 분석이 항상 먼저**이고, **자동·즉시 재시도는 어느 분기에서도 `0`회**입니다. **park은 만능
탈출구가 아닙니다** — 아래 한 줄에만 열려 있으며, 다른 상태에서 park을 시도하면 필요한 recovery를
하지 않은 채 부분 복원 dwell만 길어집니다.

| 상태 | 올바른 entry |
|---|---|
| `RESTORING`, prefix 불완전, inflight 없음 | `recover`가 **아닙니다.** attended reauthorize를 거쳐 resume합니다 |
| `RESTORING`, inflight 있음 | 역시 attended reauthorize가 먼저입니다. 재개된 execute는 그 inflight를 **관측으로만 조정하고 재전송하지 않습니다.** 불일치하거나 관측에 실패할 때만 latch되어 `RECOVERY_REQUIRED`가 됩니다 |
| `RECOVERY_REQUIRED` | **이때만** `recover` entry입니다 |
| `ARMED`, authority 만료 | fresh baseline 예산이 남아 있으면 reauthorize로 진행 가능합니다. 다만 `BASELINE_EXPIRED`라면 reauthorize로 해결되지 않는 **dead-end**입니다 |
| `PREPARED`/`ARMED`, **challenge 실패·만료** | 최초 1회에 한해, **재발급 시점 잔여가 `720초` 이상이면 같은 record에서 1회 재발급** 가능합니다. 잔여가 부족하거나 이미 1회 재발급했다면 **dead-end**이므로 중단·보고합니다 |
| `PREPARED`/`ARMED`, **`BASELINE_EXPIRED` 또는 digest mismatch** | **사용할 수 있는 attended entry가 없습니다.** park·recover·clear·재prepare 모두 불가하며 그대로 두고 중단·보고합니다 |
| `AWAITING_FINAL_VERIFY`, prefix 완전, inflight·error·final evidence 없음 | **park 가능하지만 manifest만으로는 안 됩니다.** 아래 "park을 선택했을 때"를 그대로 따릅니다 |
| `FINAL_VERIFIED`, archive·ACK 문제 | **cycle과 depth에 맞는 기존 idempotent continuation을 같은 command family로 재개합니다. 임의의 `clear`를 호출하지 않습니다** — 아래 세 경로 참조. 셋 다 **새 device/restore attended grant는 필요하지 않지만 host mutation 승인 범위는 필요합니다** |

**모든 분기에 공통인 것은 여기까지입니다 — 첫 실패 즉시 중단·보고, 자동·즉시 재시도 `0`회.**
새 precommit과 새 attended 승인은 **실제로 resume·recover·park을 수행하는 분기에만** 해당합니다.
`FINAL_VERIFIED`의 archive 정리에는 새 grant가 필요 없고, dead-end 분기에는 받을 grant 자체가
없습니다. 공통 규칙을 그보다 넓게 쓰면 존재하지 않는 승인을 찾게 만듭니다.

위 표의 "reauthorize 후 resume"은 자동 재개가 아니라 **사람이 새 grant를 발급한 뒤의 재개**입니다.

`prepare` 이후에는 **재수집으로 돌아갈 수 없습니다.** challenge는 `PREPARED` 설치 뒤에 발급되므로
그 시점에는 tombstone이 이미 교체됐고, 새 Phase 0을 얻어도 live record에 막히며 park 대상도
아닙니다. 위 표의 challenge 행이 그 유일한 예외이며, 그것도 조건부 1회입니다.

### 재개 경로의 승인 결속 — "reauthorize"는 세 축을 대신하지 않습니다

R42로 성공 경로의 device read에 승인 축(B2)을 붙였습니다. **실패 재개 경로도 같은 read와 남은
write를 수행합니다.** 위 표가 그것을 "attended reauthorize" 또는 "recover"라고만 부르면, 성공
경로에서 닫은 무승인 접촉이 실패 경로로 그대로 남습니다. attended grant는 **B2·C·D를 함축하지
않습니다.**

| 재개 경로 | 다시 확인·승인해야 하는 축 |
|---|---|
| `RESTORING` resume (inflight 유무 무관) | **A** + **B2**(재개 범위의 device read) + **C**(host journal transition) + **D**(남은 bounded write와 현장 감시·즉시 차단) |
| `RECOVERY_REQUIRED` → `recover` 후 continuation | **A + B2 + C + D** |
| `ARMED` reauthorize 후 execute | **A + B2 + C + D** |
| `AWAITING_FINAL_VERIFY`에서 **final verification 수동 재시도**를 선택 | **A + B2 + C**(authority·journal 갱신) + attended grant. **device write `0`이므로 D는 불필요합니다** |
| `AWAITING_FINAL_VERIFY`에서 **park**을 선택 | **A + B3** + 별도 park host-state 승인 + attended write-free park consent (위 park 소절) |
| `FINAL_VERIFIED` continuation | device I/O `0`이므로 **A + host mutation 승인**. B2·D·restore grant는 불필요 |
| dead-end (`PREPARED`/`ARMED`의 `BASELINE_EXPIRED`·digest mismatch) | **승인 경로 자체가 없습니다.** 중단·보고 |

**A가 모든 실행 가능한 행에 들어가는 이유** — A는 고정 root를 열 때마다 발생하는 private read와
일시 writability probe이고, 이 문서는 **어느 축도 다른 축을 함축하지 않는다**고 선언했습니다. 재개는
새 command와 새 fixed-root open을 수반하므로, 0단계의 A가 시간 제한 없이 이후 모든 재개까지
암묵적으로 존속한다고 쓰지 않는 한 그 probe들은 무승인이 됩니다. A의 lifetime을 따로 정의하는
대신 **재개할 때마다 A를 다시 확인**하는 쪽을 택했습니다 — "첫 실패 즉시 중단·보고 → 새
precommit → 해당 축 재승인" 구조에서는 그편이 단순하고, 예외를 만들지 않습니다.

순서는 어느 경로든 같습니다 — **원인 분석 → 새 precommit → 해당 축들을 다시 확인·승인 →
그 다음에 attended grant 발급**입니다. **이전 grant나 "reauthorize"라는 단어가 이 순서를 건너뛰게
하지 않습니다.**

`AWAITING_FINAL_VERIFY`에서 재시도와 park 중 무엇을 고를지는 **원인 분석 결과에 따라 결정**하며,
기본값으로 어느 한쪽을 고르지 않습니다.

### `FINAL_VERIFIED`의 세 경로 — `clear`가 정답인 것은 둘뿐입니다

`FINAL_VERIFIED`는 한 상태가 아닙니다. 어느 cycle이 끝난 것인지에 따라 이어지는 행위가 다르고,
**sentinel에서 `clear`로 읽으면 이미 검증된 8-action predecessor를 promotion 없이 없애는 잘못된
지시**가 됩니다.

| 끝난 cycle | 이어지는 것 |
|---|---|
| **sentinel** (depth `0`) | receipt를 얻은 뒤 **promotion**입니다. predecessor success barrier를 persist하고 baseline `PREPARED` successor로 교체합니다. **`clear`하지 않습니다** |
| **baseline restore** (depth `1`) | baseline receipt와 bundle을 persist하고, **receipt에 결속된 `clear`** 뒤 phase-5 successor를 stage합니다 |
| **phase-5** (depth `2`, `restore` 흐름) | final receipt와 finalization 뒤 **receipt에 결속된 `clear`**입니다 |

세 경로 모두 **새 device·restore attended grant는 필요하지 않습니다.** 다만 durable write가
발생하므로 **host mutation 승인 범위 안**에서 수행합니다. 공개 CLI에 독립 `clear` 명령이 없더라도
문서의 계약은 위와 같아야 하며, 재개는 항상 **현재 record의 cycle·depth에 맞는 continuation**입니다.

### park을 선택했을 때 — 새 artifact와 새 승인이 필요합니다

park eligibility는 manifest가 가리키는 accepted pair를 **다시 읽어** 여러 조건을 한꺼번에
요구합니다. 시간 조건은 그중 하나일 뿐입니다.

- accepted pair 정본 재독과 plan·series·ordinal·digest 일치
- pair completion gap `≤` manifest가 선언한 bound
- 두 역할의 **identity binding**과 **raw-frame hash** 일치
- **outer six-field와 432-byte schedule image가 좌초한 journal의 baseline과 exact 일치**
- attempt 시작이 **완료된 action들의 최대 completion보다 뒤**

**1단계의 Phase 0 artifact는 write보다 앞서므로 마지막 조건에서 반드시 거부됩니다.** 그리고 네
번째 조건 때문에, **artifact가 "장비가 baseline 상태가 아님"을 보여 준다면 park은 허용되지
않습니다** — 그 경우는 park이 아니라 복원 문제입니다.

**park-verify는 network-none·device I/O `0`으로 실행하며, park 본체도 마찬가지입니다.** 위 조건
중 하나라도 실패하면 **finalization `0` · tombstone `0` · live journal 불변**으로 중단·보고합니다.

#### artifact 이후의 drift 창 — 코드가 아니라 운영으로 닫습니다

park은 artifact를 수집한 뒤 **현재 장비를 다시 읽지 않으며, artifact의 age 상한도 없습니다.**
따라서 capture 이후 앱·Home Assistant·다른 code writer가 장비를 바꿔 놓으면, **stale이지만
유효한 artifact로 live journal을 terminalize**해 recovery context를 잃을 수 있습니다. 이는 §3의
rollback·durable journal 범주에 직접 닿습니다. 새 code gate를 만들지 않고 운영으로 닫습니다.

- **capture 직전** 앱·Home Assistant·장비 TCP connection·모든 code writer 부재를 다시 확인합니다.
- **capture부터 park 완료까지** device contact와 write는 `0`이고 단일 제어권을 유지합니다.
- 그 사이에 **상태 불변에 의심을 만드는 사건**이 하나라도 생기면 그 artifact 재사용은 `0`이며 새
  승인 B3부터 다시 시작합니다. 해당하는 것은 **device contact 또는 write, 다른 writer의 등장,
  단일 제어권 상실, 예기치 않은 pause·interruption, power·reboot** 등입니다.
- **연속적으로 진행되는 정상 처리 시간 자체는 금지 대상이 아닙니다.** artifact → candidate →
  host 승인 → persist·exact reload → `park-verify` → consent → park 에는 필연적으로 시간이
  흐릅니다. 코드에 artifact age 상한이 없다는 사실은 그대로 두되, 근거 없는 숫자 상한을 새로
  만들지도 않습니다. 구분 기준은 **경과 시간이 아니라 위에 열거한 사건의 유무**입니다.

#### 순서와 승인

1. **먼저 중단하고 원인을 분석합니다.** 자동·즉시 재시도는 `0`회입니다.
2. park을 선택했다면, **마지막 completion보다 뒤에** 새 explicit-only strict artifact를
   수집합니다. device read이므로 **승인 B3**(새 device read + collector durable write)가 필요합니다.
3. 그 artifact와 **좌초한 journal의 exact digest**에 결속된 **새 v2 disposition manifest
   candidate**를 만들고, 위 eligibility 조건 전부를 **offline에서 재검증**합니다. 이 시점의
   candidate는 메모리에만 둡니다. **기존 Phase 0 artifact와 기존 disposition manifest 재사용은
   `0`입니다.**
4. **별도 park host-state mutation 승인**을 받습니다. qualification용으로 받은 승인 C가 이것을
   포괄한다고 보지 않습니다. 범위는 **owner-only disposition manifest의 persist와 exact reload**,
   그리고 park archive·journal snapshot·v2 finalization·tombstone mutation입니다.
5. 그 승인 아래에서 **disposition manifest를 owner-only 경로에 persist하고 exact reload**합니다.
   park CLI는 그 파일을 입력으로 요구하므로 이 단계 없이는 park을 실행할 수 없습니다.
6. **network-none으로 `park-verify`를 실행**합니다. 실패하면 **consent 요청 `0`, archive·index·
   tombstone mutation `0`**으로 중단·보고합니다.
7. **별도의 attended write-free park consent**를 받습니다. park의 device write는 `0`이므로 이것은
   승인 D가 아닙니다.
8. **network-none으로 park을 실행**합니다.

---

## 이 실행값들이 왜 이 값인지 (설명부 — 실행 시 참조 불필요)

- **cadence `30초`** — 보존된 series span(`51.666567`·`52.817943초`)과 채택 ordinal의 attempt
  (`21.650386049`·`22.795387102초`)가 `30초` precommit과 각각 `16.180951`·`22.555898` 밀리초 안에서
  정합합니다. 저장소에는 `requested_cadence` 필드가 없고 그 값이 든 private plan을 읽지 않았으므로
  이것은 **재구성**이며 이전 값의 증명이 아닙니다. 다른 값을 고를 근거가 없어 **새 실행의
  cadence로 `30초`를 선택**합니다.
- **`max_observation_age_seconds: 1800`** — 이 필드는 capture freshness(첫 wire 게이트)와 qualified
  bundle의 handoff 유효기간을 겸용합니다. 저장소 값만으로 말할 수 있는 것은 보수적 봉투뿐입니다 —
  이전 run의 게이트 요구는 **`≤ 1295.800558초`**, `1800`의 **증명 가능한 최소 여유는
  `504.199442초`**입니다. 실제 `captured_at`과 first-wire 시각은 보존 산출물에서 읽지 않았습니다.
- **최소 잔여 `720초`** — 다음 네 항의 합입니다.
  - **`60초`** — **6단계 2번의 최종 예산 확인부터 첫 challenge 표시까지**의 command transition
    상한. manifest persist·exact reload, `prepare`, qualify 시작, challenge 표시가 모두 여기
    들어갑니다. 코드에는 이 구간의 bound가 없으므로 6단계의 **운영 predicate**로 둡니다.
  - **`300초`** — attended challenge의 최대 수명. digest 대조와 writer 재확인이 그 안에 들어갑니다.
  - **`300초`** — 발급된 authority의 수명. token 이후 controller가 하는 일은 — 여러 번의 fresh
    read를 포함해 — **이 안에 들어갑니다.** 따로 더하지 않습니다.
  - **`60초`** — 잔여 margin.

  초안은 "첫 action 직전 fresh read 보수값 `60초`"를 따로 더했습니다. **그렇게 쓰면 안 됩니다.**
  한 번의 관측은 discovery-before, connect, 두 번의 authenticate 교환, raw read, discovery-after를
  포함하고, 첫 wire 전에 safe fallback 두 action이 skip되면 fresh 관측이 최대 다섯 번까지 올 수
  있습니다. **"한 fresh read = `60초`"는 코드에서 증명되지 않습니다.** authority `300초`가 그
  구간을 이미 덮으므로 별도 합산이 필요 없고, 그 자리에는 대신 bound가 없던 command transition
  구간이 들어갑니다.
- **채택 ordinal을 마지막으로** — artifact의 `captured_at`이 채택 pair에서 나오므로, 늦은 ordinal이
  `captured_at`을 늦추고 first-wire 예산을 늘립니다. baseline은 두 series의 min이므로 **두 series를
  가깝게 수집하고 양쪽 모두 늦은 ordinal을 채택**하는 것이 최선입니다.

**규율**: 산술이 맞아떨어진다는 것은 그 값이 실제로 그렇게 기록돼 있었다는 증명이 아닙니다.
보존 산출물에서 읽지 않은 값은 재구성으로 적고, 재구성에 기대어 새 값을 고를 때는 그 선택이
무엇에 근거했는지를 함께 적습니다.
