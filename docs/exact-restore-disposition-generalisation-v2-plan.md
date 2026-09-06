# Exact-restore parked disposition generalisation (redesign)

상태: **PLAN ONLY / DEVICE NETWORK 0 / JOURNAL CHANGE 0 / ARCHIVE CHANGE 0 / HA CHANGE 0**

작성일: 2026-09-06

선행:
[`Exact-restore predecessor success finalization invariant`](exact-restore-predecessor-finalization-plan.md)(작업 A, 착지·CONTRACT_DISCHARGED),
[철회된 초판](exact-restore-disposition-generalisation-plan.md)

작업 A가 blocker 3(predecessor replay 표면)을 원인에서 닫았습니다. 남은 두 blocker와, 교차검증이
추가로 지적한 경계를 이 계획이 다룹니다. **철회된 초판을 재활용하지 않고 새로 씁니다** — 그 문서의
범위 산정은 세 계층에서 틀렸습니다.

## 이 계획이 정정하는 제 이전 설계 두 가지

교차검증에서 나왔고 제가 코드로 확인했습니다.

1. **phase-5와 baseline restore는 `cycle`이 같습니다.** 둘 다
   `ExactRestoreCycle.BASELINE_RESTORE`이고 action plan도 6개로 동일합니다. 따라서 **cycle로 구분할 수
   없고**, 판별자는 embedded **predecessor depth**입니다 — sentinel `0`, baseline `1`, phase-5 `2`.
   초판은 cycle만 넓히면 된다고 적었는데 그것으로는 baseline과 phase-5를 구분하지 못합니다.
2. **`maximum_pair_completion_gap_seconds`는 overload 전용이 아닙니다.** 초판은 두 timing 필드를 모두
   overload 전용으로 optional화하려 했습니다. 그러나 pair bound는 어떤 사유의 disposition에서도 의미가
   있고, 현재 disposition manifest는 이 값을 **record의 policy pair bound와 결속조차 하지 않습니다**
   (`exact_restore_disposition.py`에 `max_final_pair_gap_seconds` 참조 없음). overload 전용은
   `minimum_total_attempt_seconds` **하나뿐**입니다.

## 닫을 질문

`awaiting_final_verify`로 좌초한 journal을, sentinel·baseline·phase-5 어느 cycle에서든, v1 overload가
아닌 사유로도, **legacy canonical identity와 Q2 의미를 보존하면서** 종결할 수 있는가?

---

## 1. version 1 byte identity

기존 overload disposition의 manifest·record·authority·finalization·tombstone은 **수정 없이 load되고
canonical bytes와 digest가 불변**이어야 합니다.

- 확장은 **명시적 v2 + targeted serializer**로만 합니다. `None`일 때 그 key만 생략하고 일괄
  `exclude_none`은 쓰지 않습니다.
- golden은 **양쪽 재계산이 아니라 하드코딩된 legacy bytes/hash**로 고정합니다. 이 규율은 timing split
  때 `d5fc156e…`를 프로젝트 코드 없이 재계산해 확인한 것과 같습니다.

## 2. operation policy version 결속

- **v1 disposition**은 record policy의 `max_final_acquisition_duration_seconds`가 `None`인 legacy
  journal에만 적용합니다.
- **v2 disposition**은 그 필드가 non-`None`인 journal에만 적용합니다.
- 기존 v1 operation을 새 generic 의미로 **소급 해석하지 않습니다.**
- 이 결속은 live eligibility뿐 아니라 **archived reload와 `status` 경로에서도 재검증**합니다. permanent
  artifact는 manifest 없이 읽히는 경우가 있으므로 그때도 성립해야 합니다. **단, 그러려면 record가 검증에
  필요한 값을 스스로 담아야 합니다** — 아래 §5의 pair bound 항목을 함께 보십시오.

## 3. cycle · action count · outcome vector

- action count는 manifest가 주장하는 값이 아니라 **cycle에서 재도출**해 대조합니다 — sentinel `8`,
  baseline·phase-5 `6`. 표를 두되 `build_exact_restore_plan()`의 실제 산출과 대조하는 회귀로 고정해
  drift를 막습니다.
- **phase-5는 cycle이 아니라 predecessor depth로 구분**합니다(위 정정 1). manifest가 depth를 선언하고
  record의 embedded chain 길이와 exact 일치해야 합니다.
- v2는 **순서 있는 `completed_action_outcomes` vector**를 manifest↔journal↔record↔decoder에 결속합니다.
  개수 요약이 아니라 순서까지 고정해야 "4 write + 2 skip"이 어느 action에서 났는지가 보존됩니다.
- 허용 outcome은 **현재의 닫힌 집합**뿐입니다 — `ALREADY_SATISFIED`, `WRITTEN_VERIFIED`,
  `VERIFIED_AFTER_UNCERTAIN`. **미래 enum 값을 자동 수용하지 않고 UNKNOWN은 fail-closed**입니다.
- baseline 정상 분포(safe fallback 2 write + schedule 2 skip + outer 2 write)를 표현할 수 있어야
  합니다.

## 4. artifact-after-write 불변식 재도출

작업 A 직전에 착지한 eligibility 완화(`bdfaf5c`)의 근거는 **"전 outcome이 `WRITTEN_VERIFIED`"**였습니다.
집합을 넓히면 그 문장이 성립하지 않으므로 **재도출하고 그 근거를 supersede**합니다.

세 outcome의 성질:

- `ALREADY_SATISFIED` — **write 없음**, completion은 관측 뒤에 찍힘
- `WRITTEN_VERIFIED` — completion은 **post-write verify 뒤**
- `VERIFIED_AFTER_UNCERTAIN` — possible write 뒤 **fresh reconciliation**, 재전송 없음

세 경우 모두 completion 시각이 **해당 action의 device 접촉보다 뒤**에 찍힙니다. **이 도출을 source
trace와 회귀로 직접 증명**하고 논증만으로 넘기지 않습니다. 닫힌 집합 밖의 outcome은 fail입니다.

### 그러나 "마지막 completion"은 안전 경계가 아닙니다 — 착지한 코드의 결함

`_complete()`는 `completed_at=self._now()`를 **이전 completion과 비교 없이** 찍고,
`ExactRestoreRecord`는 prefix index 순서만 강제할 뿐 **`completed_at`의 단조성을 강제하지 않습니다**
(validator는 UTC 여부만 검사). 따라서 clock이 후퇴하면 `completed_actions[-1].completed_at`이 **앞선
write action의 completion보다 이를 수** 있습니다.

구체적으로: action 6 write completion이 wall `100`, clock 후퇴 뒤 action 7 skip completion이 `90`,
artifact 시작이 `95`인 경우 — 현재 gate는 통과하지만 그 artifact는 **action 6의 write 이전 증거**일 수
있습니다. 그러면 post-write baseline 증거 없이 live recovery journal을 tombstone으로 바꾸게 되어 **§3의
rollback·recovery journal 손실**입니다.

**이것은 계획의 문제가 아니라 이미 착지한 `bdfaf5c`의 결함입니다.** 그 gate는 현재 `[-1]`을 씁니다.
현재는 park 대상 journal이 없어 노출이 없지만, 이 작업에서 반드시 고칩니다.

**변경**: 경계를 `max(item.completed_at for item in record.completed_actions)`로 바꿉니다.

**legacy record validator에 단조성 게이트를 새로 넣지 않습니다** — 기존 journal의 load를 깨뜨릴 수
있습니다. disposition 안에서 보수적으로 `max`를 취하는 것이 맞습니다.

**회귀**: non-monotonic completion fixture에서 artifact가 last-index completion보다 뒤이지만
max-completion보다 앞이면 **거부**되어야 합니다.

## 5. reason · timing

- **v1**: reason `final_verification_policy_overload`와 overload 증거를 **그대로** 둡니다.
- **v2 generic**: durable 실패 frame이 없으므로 과대진단하지 않는 `final_verification_unresolved`
  계열을 씁니다.
- **`maximum_pair_completion_gap_seconds`는 모든 disposition에서 유지**하고, record policy의
  `max_final_pair_gap_seconds`와 **exact equality**로 결속합니다(현재 결속 없음 — 위 정정 2).
- **`minimum_total_attempt_seconds`만 v1 overload 전용**이며 generic v2에는 **없어야** 합니다.
- generic 실패를 overload로 분류하거나 `total > pair`를 강제하지 않습니다.
- 다만 overload threshold를 제거해도 **verified interval의 nonnegative total span 검사는 유지**합니다.
  정본 collector가 이미 검증하지만 경계 로컬 방어를 버릴 이유가 없습니다.

### v2 record는 승인된 pair bound를 스스로 보존해야 합니다

archive에는 manifest 본문이 아니라 `manifest_sha256`만 남고, `ParkedDispositionRecord`에는 **관측값**
`pair_completion_gap_ns`만 있을 뿐 **승인된 bound는 없습니다**(bound는 manifest `:155-156`에만 존재).
따라서 live eligibility에서 manifest↔record policy equality를 검사해도, tombstone·`status`의 archived
reload에서는 **그 equality를 재검증할 자료 자체가 없습니다.**

- **v2 record**에 승인된 `maximum_pair_completion_gap_seconds`를 명시적으로 저장하고, journal snapshot
  policy와의 exact equality를 **pre-index cross-validator와 final loader 양쪽에서** 검사합니다.
- **v1 record bytes는 바꿀 수 없습니다.** 따라서 새 v1 park의 live eligibility에서는 equality를
  강제하되, **이미 archived된 legacy v1 record는 이 필드가 없어 사후 equality를 재구성할 수 없다는
  한계를 문서에 명시**하고 golden과 load는 그대로 둡니다. durable self-contained 검증은 **v2부터**
  보장됩니다.
- 이름과 public 진단에서 **관측치(`pair_completion_gap_ns`)와 policy bound
  (`maximum_pair_completion_gap_seconds`)를 혼동하지 않게** 분리합니다.
- **회귀**: v2에서 bound 누락·불일치가 **index 이전에** 거부되고 current finalization path가 생기지
  않음.

이 한계를 숨긴 채 "모든 archived disposition에서 exact equality 재검증"이라고 쓰면 구현 불가능하거나
legacy bytes를 깨뜨립니다.

## 6. predecessor replay barrier — §3 duplicate-write

baseline·phase-5 journal을 park하기 전에 **embedded predecessor chain 전체**의 archived success
finalization·receipt를 exact-confirm합니다. 하나라도 absent·damaged·mismatched면 **current parked
finalization과 tombstone을 설치하기 전에** 거부합니다.

작업 A가 normal path에서 이를 보장하지만 **disposition이 자체적으로 검사**합니다 — park은 비정상 경로를
다루는 절차이므로 정상 경로의 보장에 기대면 안 됩니다.

**확인 위치가 결정적입니다.** dry verify에서도 chain을 확인하되, `verify_park_eligibility()`의 별도
archive 호출만으로는 부족합니다 — 나중 `persist_parked_bundle()`이 **새 claim을 잡으면 check와 install
사이에 TOCTOU가 남습니다.** 따라서 `persist_parked_bundle()`이 current parked finalization을 설치하는
**동일 `_claim()` 안에서, index 설치 직전에** oldest→newest 순으로 predecessor receipt·finalization을
exact-confirm합니다.

receipt는 복제 계산하지 말고 canonical `_receipt_from_final_verified_record()`로 도출합니다. claim 안
확인이 실패하면 current index와 tombstone 설치는 `0`회입니다. **테스트가 "그 확인 호출이 같은 active
archive claim 안에서 일어난다"는 것을 직접 단언**합니다.

## 7. decoder-before-index ordering — §3 durable journal

blob을 쓴 뒤 **current parked finalization index를 설치하기 전에** candidate를 검증합니다. 그러지
않으면 incompatible schema가 index를 선점한 뒤 confirmation이 실패해 **live journal이 stranded되고 모든
controller mutator가 막히는** 상태가 만들어집니다(철회 문서의 blocker 1). index 설치 **후** 재검증도
그대로 유지합니다.

**schema 검증만으로는 부족합니다.** 현재 `_require_parked_backing_finalization()`은 exact key·reason·
authority·snapshot 존재만 보고, journal을 parse해 count 등 **cross-binding을 확인하는 것은 나중
`load_parked_disposition()`**입니다. 새 depth·outcome·policy 필드에서 base decoder만 index 전에 부르면,
**잘못 만들었지만 schema는 유효한** candidate가 index를 선점하고 post-index cross-binding에서 처음
실패해 다시 stranded됩니다.

따라서 index 전 candidate 검증은 exact key·reason decoder에 더해 **journal snapshot parse와
disposition version↔policy(`None`/non-`None`), predecessor depth, ordered outcome vector, action count,
pair-bound equality**까지 포함합니다. **새 필드만이 아니라 기존 결속도 전부 포함**합니다 —
operation·cycle·baseline·action-plan·authority chain·evidence·journal digest. 이 로직을 **pre-index와 final load가 공유**해 drift하지 않게
합니다. 각 cross-binding mismatch fault에서 **current finalization path 부재**를 단언합니다.

crash 표에 blob·orphan·**decoder failure**·index·tombstone 경계를 모두 포함합니다.

## 8. legacy / new binary 동작

- receipt archive decoder가 **v1과 v2의 exact key set·reason·outcome 규칙을 명시적으로 분기**합니다.
  현재는 v1 shape와 overload reason이 하드코딩돼 있습니다.
- **old binary는 new parked data에서 fail-closed**여야 합니다.
- **Q2/App verifier는 어떤 parked variant도 receipt로 소비하지 않습니다.**
- `ExactRestoreOperationFinalization`은 **version `2`를 유지**합니다(확정). terminal-exclusion의 의미는
  여전히 `parked_unqualified`이고 finalization의 exact key set도 바뀌지 않으며, backing disposition의
  schema 버전은 **참조되는 payload 내부가 소유**합니다. v3를 신설하면 controller·tombstone·legacy 표면만
  늘고 새로운 terminal 의미는 생기지 않습니다. 현 binary는 v2 finalization을 읽은 뒤 새 backing
  decoder에서 fail-closed되고, `Literal[1]`만 아는 더 옛 binary는 그보다 일찍 fail-closed됩니다.

## 9. 사람이 읽는 진단

challenge와 public eligibility·result에 **cycle, predecessor depth, reason, 순서 있는 outcome vector
또는 outcome별 개수**를 노출합니다.

`device_write_count=0`은 **park operation 자체**의 write가 `0`이라는 뜻임을 명시하고, **과거 restore
action에 definite·possible write outcome이 있었다는 사실을 숨기지 않습니다.**

## 10. 검증 계약

- legacy golden bytes·hash, v1 end-to-end와 `status`
- v2 sentinel·baseline·phase-5 park end-to-end
- `ALREADY_SATISFIED`/`WRITTEN_VERIFIED`/`VERIFIED_AFTER_UNCERTAIN` vector, **미래 outcome 거부**
- version·cycle·count·policy·reason·depth **교차 불일치 거부**
- generic reason에 `minimum_total_attempt_seconds` 부재, v1 overload predicate 무변경
- v2 record의 pair bound 누락·불일치가 **index 이전에** 거부되고 current finalization path 부재
- legacy v1 archived record는 bound 필드 없이도 load되고 digest 불변
- predecessor absent·corrupt·mismatch → current finalization·tombstone **설치 0회**
- **candidate decoder 실패가 finalization index보다 먼저** 발생
- crash ordering 전 경계 + old binary fail-closed
- Q2/App이 어떤 parked variant도 수용하지 않음
- network-none, import graph, device I/O `0`

## 11. 범위와 원자성

두 모듈을 **한 source commit으로** 착지시킵니다. 다만 근거를 정확히 적습니다 — "어느 순서든
dead-end"는 사실이 아닙니다.

- **writer-before-decoder는 확실히 unsafe**합니다. 새 shape를 쓰는 writer가 먼저 들어가면 decoder가
  거부해 blocker 1의 stranded 상태가 재현됩니다.
- **backward-compatible decoder-first는 inert**합니다. 새 payload를 쓰는 writer가 없으므로 dead-end를
  만들지 않습니다.
- 따라서 원자성의 근거는 "양방향 dead-end"가 아니라 **(a) writer가 decoder보다 먼저 착지하는 것을
  금지**하고, **(b) schema·writer·crash 테스트를 한 review 단위로 유지**하며, **(c) 분할 시 중간
  commit이 미사용 decoder surface이거나 unsafe writer surface가 된다**는 것입니다.

source 범위: `src/jebao_flow/exact_restore_disposition.py`와
`src/jebao_flow/exact_restore_receipts.py`. `src/jebao_flow/exact_restore.py`는 canonical receipt
helper를 **기존 private import로 재사용**하면 수정이 불필요합니다.

test 범위는 §10과 일치하도록 열거합니다 — `tests/unit/test_exact_restore_disposition.py`,
`test_exact_restore_receipts.py`, `test_exact_restore_q2_evidence.py`,
`test_app_async_observation_cli.py`, 그리고 outcome semantics를 직접 인용할 `test_exact_restore.py`.

`read_only_collector.py`, frozen native schedule/linkage harness, HA·daemon·MQTT는 수정하지 않습니다.

모듈 docstring의 "failed exact-restore **qualification**"은 이제 baseline·phase-5도 포함하므로 "failed
exact-restore **operation / final verification**"으로 정정합니다.

**§3 판정**: 이 작업은 write-free이지만 durable journal과 replay 표면을 다루므로 **§3 P1 가능성이 살아
있습니다** — 6번(duplicate write)과 7번(durable journal)이 그 경로입니다.

## 12. hardware gate

이 작업이 착지해도 v2 qualification의 4~6단계는 **별도**입니다. 그 계획의 명시적
`max_observation_age_seconds` 정책값, fresh Phase 0, **두 operation 각각의 on-site 승인**이 여전히
필요하며 hardware는 계속 **NO-GO**입니다.

## 착수 전 확인 규율

철회된 초판의 실패가 모두 "한 계층만 보고 세 계층을 판단한 것"이었으므로, 구현 착수 시 모든 범위
주장에 대해 **저장 시점 decoder · 실행 시점 분기 · archive 시점 순서** 세 계층을 각각 확인하고 그
결과를 실행 addendum에 기록합니다.

## 실행 addendum (2026-09-05) — source 착지와 §10 이행

이 계획의 source는 두 commit으로 착지했습니다.

- `1b8d2b900b2a3ab752f7e8a1aaf141424e7145a6` — 본체. `exact_restore_disposition.py`,
  `exact_restore_receipts.py`와 회귀를 §11의 요구대로 한 commit에 묶었습니다.
- `56146bd8b5dae8cf148b14a5ebfe032f9ced02d2` — 교차검토 후속. 아래에서 설명하는 canonical
  round-trip 결함과 문언 정정, 추가 회귀.

network-none reviewed image에서 disposition 파일 `51 passed`,
`tests/unit -k "exact_restore or app_async"` `386 passed`, 전체 unit `1888 passed`,
변경 파일 ruff lint·format 통과를 확인했습니다. 두 commit 모두 독립 재계산·적대 검토를 거쳐
diff digest가 일치했고 §3 P1은 발견되지 않았습니다.

### 착수 전 확인 규율의 결과

철회된 초판이 "한 계층만 보고 세 계층을 판단"해 실패했으므로, 이 계획은 모든 범위 주장에 대해
**저장 시점 decoder · 실행 시점 분기 · archive 시점 순서** 세 계층을 각각 확인하도록 요구했습니다.
실제로 그 규율이 잡아낸 것들입니다.

- 저장 시점 decoder — receipt archive의 parked backing 검사는 고정 key set과 단일 reason을
  갖고 있었습니다. version별로 분기하지 않으면 v2 blob이 저장은 되고 읽히지 않습니다.
- 실행 시점 분기 — eligibility gate가 sentinel cycle을 하드코딩하고 있었습니다. 이것을 그대로
  두면 v2 park은 **전부** 거부됩니다.
- archive 시점 순서 — predecessor 확인과 candidate 재검증을 index 설치와 같은 claim 안,
  index 이전으로 옮겨야 검사와 설치 사이의 창이 사라집니다.

### 교차검토가 프로덕션에서 확인한 canonical round-trip 결함

구현 중 저는 test helper에서 "dump한 뒤 다시 validate하면 optional key의 기본값이 되살아난다"는
현상을 만났고, 이를 **의심으로만** 보고했습니다. 교차검토는 같은 현상이 helper가 아니라 production
model에서 실제로 성립함을 확인했습니다.

`ParkedDispositionManifest`의 직렬화기는 `required_action_outcome`이 `None`이면 key를 제거하므로
version 2 manifest의 canonical JSON에는 그 key가 없습니다. 그런데 그 필드의 기본값이
`"written_verified"`였으므로, owner-only loader(`load_parked_disposition_manifest`는 곧바로
`model_validate_json`입니다)가 그 문서를 다시 읽으면 version 1 값이 되살아나 "version 2
disposition must be a generic unresolved park"로 거부됐습니다. **모델이 스스로 쓴 v2 manifest를
로더가 읽지 못하는 상태**였고, test helper가 absent key를 `None`으로 채우고 있었기 때문에 회귀에
잡히지 않았습니다.

수정은 기본값을 `None`으로 내리고 `model_validator(mode="before")`에서 payload가 version 1이며
key가 없을 때만 채우는 version-aware 처리입니다. version 1은 입력 관용성, 18-key shape,
manifest digest `1e6b0a060c5431b0273819577d13468d523eb33db51d52989aebee19b91403b3`을 모두
유지합니다. helper의 우회는 제거했고, round-trip 자체를 in-memory와 mode `0600` 파일 양쪽에서
고정했습니다.

이 항목은 §3 P1이 아닙니다. 결과는 fail-closed이고 durable journal이나 장비 write에 영향을 주지
않습니다. 다만 v2 park을 사실상 사용 불가로 만드는 비용 결함이었고, **의심을 확인으로 바꾸지 않고
전달한 것이 제 쪽의 절차 실패**였습니다.

### §10 검증 계약 이행

증명 출처를 셋으로 구분해 적습니다 — **직접**(이 계획이 추가한 회귀), **하위 계층**(이미 있는
production archive/모델 자신의 테스트), **재구성**(옛 동작을 shape 수준으로 재현).

| §10 항목 | 이행 | 출처 |
| --- | --- | --- |
| legacy golden bytes·hash, v1 end-to-end와 `status` | 이행 | 직접 — manifest 18-key/digest, stored disposition 22-key/digest `0552d597…`, 두 shape 모두 v2 key 부재 |
| v2 sentinel·baseline·phase-5 park end-to-end | 이행 | 직접 — depth 0/1/2 parametrize, phase-5는 cycle이 baseline과 같아 depth로만 구분됨을 단언 |
| 세 outcome vector와 미래 outcome 거부 | 이행 | 직접 — 정상 skip vector와 uncertain vector를 분리, 허용 집합이 정확히 셋임을 고정 |
| version·cycle·count·policy·reason·depth 교차 불일치 거부 | 이행 | 직접 — 생성 시점(ValidationError)과 실행 시점(`disposition_journal_ineligible`)을 나눠 고정 |
| generic reason의 overload bound 부재, v1 predicate 무변경 | 이행 | 직접 + 하위 계층 — 모델 validator 회귀와 v1 golden |
| v2 bound 누락·불일치가 index 이전에 거부 | 이행 | 직접 — 공유 binder 주입으로 순서 고정, current finalization 부재 확인 |
| legacy v1 archived record가 bound 없이 load, digest 불변 | 이행 | 직접 — v1 park 후 archive 재적재 동일성 |
| predecessor absent·corrupt·mismatch → 설치 0회 | 이행 | 직접 — 세 경우 모두 `disposition_predecessor_unbarriered`, finalization 부재, live journal 보존 |
| candidate decoder 실패가 index보다 먼저 | 이행 | 직접 — cross-binding seam과 backing decoder seam을 **각각** 고정 |
| crash ordering 전 경계 + old binary fail-closed | 부분 이행 | 하위 계층 + 재구성 — crash ordering은 기존 tombstone/archive 회귀, old binary는 아래 참조 |
| Q2/App이 어떤 parked variant도 수용하지 않음 | 이행 | 하위 계층 — finalization shape가 version 간 동일하므로 기존 parked 거부가 variant-independent |
| network-none, import graph, device I/O `0` | 이행 | 직접 — v1에 더해 depth-2 v2에서도 socket 생성 차단하에 verify·park·resume |

### composite proof의 범위 — 정직하게 좁힌 부분

"old binary fail-closed" 항목은 두 절반으로 이루어져 있고, 저장소가 지킬 수 있는 것은 한쪽뿐입니다.

- 저장소 안: 직전 binary가 갖고 있던 두 수용 규칙(archive decoder의 고정 key set + 단일 reason,
  `Literal[1]` record model)을 **재구성**해 실제 v2 disposition이 둘 다 위반함을 보입니다. 테스트
  이름과 docstring에 "이 테스트는 그 binary를 실행하지 않는다"를 명시했습니다.
- 저장소 밖: 실제 직전 source tree를 별도 경로에 풀어 새 v2 depth-2 backing을 열어보는 실행은
  교차검토 과정에서 수행됐고 fail-closed를 확인했습니다. 이것은 durable artifact가 아니므로
  등급 **(c)**로 남기고, 저장소 회귀로 대체하지 않았습니다.

같은 정직성 규율을 두 곳에 더 적용했습니다. active claim 안의 predecessor 확인 테스트는 공개
dry 검사를 끄고 안쪽 gate만 격리하므로 그 사실을 docstring에 적었고, claim 해제 후 같은 단언이
실패함을 함께 고정해 단언이 공허하지 않음을 보였습니다. cross-binding 테스트는 공유 binder에
주입하므로 "decoder가 스스로 거부하는 경로가 아니다"를 적고, 그 seam은 별도 테스트로 덮었습니다.

### 함께 정정한 세 문언

착지한 코드의 주석이 코드보다 뒤처져 있던 곳입니다. 셋 다 동작이 아니라 근거 서술의 오류입니다.

- eligibility 경계 근거가 아직 "every outcome is WRITTEN_VERIFIED"라고 적고 있었습니다. 이 규칙은
  이번 작업이 폐기한 것이고 baseline의 정상 skip을 부정합니다. closed outcome set 논거로 교체하고,
  "latest는 last가 아니다 — 둘은 completion stamp가 역행할 때 정확히 갈리며 그래서 경계가 마지막
  index가 아니라 prefix 최대값"이라고 `max()` 논증과 이어 붙였습니다.
- 허용 outcome 집합 주석이 "field type 자체가 미래 값을 파싱조차 못 한다"고 주장하고 있었습니다.
  틀립니다. enum이 넓어지면 field는 새 멤버를 파싱하고, 막는 것은 membership gate뿐입니다.
- 직전 binary 회귀의 이름과 docstring이 실행 증명처럼 읽혔습니다. 위와 같이 좁혔습니다.

### 비차단 개선

archived public view가 관측 gap(`pair_completion_gap_ns`)과 승인 bound
(`maximum_pair_completion_gap_seconds`)를 나란히 노출합니다. version 1 record는 bound를 저장한
적이 없으므로 `null`로 드러내고 재구성하지 않습니다. §5의 "관측치와 정책값을 분리한다"가
eligibility 이후에도 유지됩니다.

### hardware gate 무변경

이 source 착지는 hardware 승인이 아닙니다. v2 qualification 4~6단계는 계속 **NO-GO**이며 fresh
Phase 0, 새 v2 manifest, 별도 on-site 승인이 선행 조건입니다. 두 commit을 만드는 동안 장비·앱·
Home Assistant 접촉은 `0`이었고 private journal·archive·recovery supervisor도 건드리지 않았습니다.

### 독립 재실행 확인

위 결과는 한쪽 실행이 아닙니다. 교차검토 측이 같은 network-none reviewed image에서 전체 unit
suite를 독립 재실행해 `1888 passed, 0 failed`로 같은 수치를 얻었고, 표적 회귀 `386 passed`,
변경 파일 ruff lint·format 통과, host와 container 간 파일 바이트 일치도 함께 확인했습니다.

증거 등급을 정확히 적습니다. 이 test·lint 실행 결과는 **console 관측**이며 보존된 결과 artifact의
id·digest가 없으므로 **(c)**입니다. `docs/runs/README.md`의 **(b)**는 데몬이 남긴 저널·영수증 같은
구조화 산출물이 실제 파일로 보존된 경우이고, 여기에는 그런 산출물이 없습니다. 이 등급을 올리려고
새 daemon artifact를 만들지 않습니다.

두 commit의 diff digest와 blob digest가 양쪽에서 독립 계산해 일치한 것은 등급 문제가 아니라
**언제든 재계산 가능한 source property**입니다. commit id만 있으면 누구든 같은 값을 다시 얻으므로
보존 여부에 의존하지 않고, 그래서 등급을 붙이지 않습니다.

그 재실행에서도 장비·앱·Home Assistant·private state 접촉은 `0`입니다: **(c)**.

## 2026-09-06 사후 확인 — run artifact는 현재 재검증 불가

이 계획의 source-level canonical compatibility와 unit 결과는 그대로입니다. 다만 실행 addendum이
참조한 2026-09-04 v1 disposition 원본, backing journal snapshot, tombstone과 finalization은
2026-09-06 범위 한정 read-only forensic에서 발견되지 않았습니다. 따라서 그 run의 stored bytes를
현재 다시 load해 대조할 수 없으며, “현재도 보존된 (b) artifact”라는 전제로 사용하지 않습니다.

이 사실은 v1 bytes를 source fixture로 재계산할 수 있다는 주장과 구분합니다. 재계산은 source
property이고 사라진 run artifact를 복원하지 않습니다. 삭제 원인은 `UNKNOWN`이며, 자세한 incident
처리와 hardware `NO-GO`는
[`Hardware-safety volume durability incident and remediation`](hardware-safety-volume-durability-plan.md)을
따릅니다.
