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
