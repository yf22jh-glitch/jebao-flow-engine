# Exact-restore final verification bound split and parked disposition

상태: **PLAN ONLY / DEVICE NETWORK 0 / CURRENT JOURNAL UNCHANGED / HA CHANGE 0**

작성일: 2026-09-04

근거 실행:
[`2026-09-04 exact-restore qualification`](runs/2026-09-04-exact-restore-qualification-4dcd8364.md)

이 문서는 standalone exact-restore의 final verification이 서로 다른 두 시간 의미를 한 정책
필드로 검사해 구조적으로 실패한 결함을 고치고, 그 실패로 남은 비-terminal journal을 증거
손실이나 qualification 오판 없이 종결하기 위한 범위 한정 계획입니다. 이 계획의 커밋과 source
구현만으로 장비 write, 장비 read, 앱 action, Home Assistant 조작 또는 current journal 변경
권한이 생기지 않습니다.

## 닫을 질문

1. participant 두 관측의 **completion coherence**와 pair 전체의 **acquisition duration**을 서로
   다른 bound로 표현하면서 legacy manifest·journal·authority·archive의 canonical identity를
   그대로 보존할 수 있는가?
2. current `awaiting_final_verify` journal을 성공 receipt로 가장하지 않고, 원문 journal과
   post-restore raw 증거를 불변 보관한 `PARKED_UNQUALIFIED` 결과로 종결해 동일 operation의
   replay를 막을 수 있는가?

첫 질문은 다음 qualification과 Phase 4 final restore가 같은 지점에서 다시 실패하지 않게 합니다.
둘째 질문은 현재 software safety state를 정직하게 terminal화하지만, restore 선행조건을 PASS로
바꾸지 않습니다.

## 확정된 증거와 현재 상태

- source commit은 `1393978b95516a44a250cbca8bb29bc59d970c3f`, 실행 기록 commit은
  `085a6319e89f61d09dd42efb3acf75aae2243a82`입니다.
- current journal은 sentinel qualification, `awaiting_final_verify`, action `8/8`,
  `inflight=false`, `error=null`, `final_evidence=false`입니다.
- operation SHA-256은
  `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`, journal file
  SHA-256은 `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`입니다.
- 여덟 action은 각각 정확히 한 번 `written_verified`로 durable 기록됐고, 그 뒤 strict raw
  series `JFS-0f3123231a54b82c8e130951263e5987`
  (`a9197bdcf366e71ded6713634924005749b8144525d3e24bf845f55bafc9c008`) ordinal `0`이
  두 장비의 identity, outer six-field와 432-byte schedule image가 baseline과 exact함을
  확인했습니다.
- 위 pair의 total attempt는 `22.010981092초`, participant completion gap은
  `11.460858534초`입니다. 두 Phase 0 ordinal에서도 각각
  `21.650386049 / 11.155363784초`, `22.795387102 / 11.562926789초`였습니다.
- `ExactRestoreVerificationPolicy.max_final_pair_gap_seconds=20`은 composition admission에서
  completion gap에 적용되지만 runtime `_capture_final_pair()`에서는 verification 전체 소요와
  first-request-to-last-receive window에도 적용됩니다.
- discovery가 identity-before/after × participant 2개의 각 5초 창을 응답 조기 도착과 무관하게
  모두 소진하므로 total acquisition은 구조적으로 20초보다 큽니다. 장비 raw read 자체는
  `0.341..0.348초`였습니다.
- 따라서 restore data plane은 exact하게 완료됐지만 formal sentinel receipt와 qualified bundle은
  없습니다. current journal이 terminal화돼도 이 두 산출물을 만들거나 대체하지 않습니다.

## 절대 불변 사항

- current journal에 세 번째 final-pair retry를 하지 않습니다.
- current manifest의 `20초` 값을 바꾸거나 기존 operation id 아래에서 새 의미로 재해석하지
  않습니다.
- current journal을 편집·삭제·강제 archive하지 않습니다. 아래 disposition이 durable하게
  설치·재적재·교차확인된 뒤에만 journal store의 정상 `save()` 경로로 재개 불가능한 terminal
  tombstone successor를 설치합니다. journal path를 absent로 만들지 않습니다.
- `PARKED_UNQUALIFIED`를 `FINAL_VERIFIED`, `ExactRestoreReceipt`, sentinel qualification receipt,
  qualified baseline bundle 또는 restore capability로 소비하지 않습니다.
- terminal disposition 준비·실행 경로는 장비 discovery/session/writer를 생성·호출·노출하지
  않습니다. 정본 artifact reader가 inert protocol 상수·ABC·dataclass를 transitive import하는 것은
  허용하지만, 보존된 artifact를 offline으로 재검증하는 범위를 넘지 않습니다.
- frozen native schedule/linkage harness를 수정하거나 실행하지 않습니다.
- Home Assistant를 재시작·reload·disable·설정 변경·service call하지 않습니다.

## 작업 A — 두 verification bound의 의미 분리

### 정책 모델

기존 `max_final_pair_gap_seconds` 이름과 legacy 의미를 삭제하거나 바꾸지 않습니다. 새 정책 필드는
다음과 같이 optional로 추가합니다.

```text
max_final_acquisition_duration_seconds: float | None = None
```

- `max_final_pair_gap_seconds`는 두 participant의 **read completion monotonic timestamp 차이**만
  제한합니다.
- 새 필드는 pair capture 시작부터 완료까지의 전체 소요와 first request부터 last receive까지의
  raw acquisition window를 제한합니다.
- 새 필드가 `None`인 legacy policy는 기존 runtime acquisition 검사 두 곳에서 계속
  `max_final_pair_gap_seconds`를 사용합니다. legacy composition에는 새 total-attempt 검사를,
  legacy runtime에는 새 completion-coherence 검사를 추가하지 않습니다. 즉 기존 journal의
  fail-closed 동작이 완전히 유지되고, source 업데이트만으로 current operation이 갑자기 PASS하지
  않습니다.
- **`None` fallback은 version `1`에 이미 존재하는 검사에 값을 공급할 수만 있고, 검사를 새로
  존재하게 만들 수는 없습니다.** 따라서 fallback은 기존 runtime acquisition 검사 두 곳에만
  적용되고, admission total-attempt와 runtime completion-coherence는 version `2`에서만 존재합니다.
- 새 필드가 non-`None`이면 `max_final_pair_gap_seconds <=
  max_final_acquisition_duration_seconds <= max_observation_age_seconds`를 요구합니다.
- 새 필드가 `None`일 때 그 key 하나만 model-level targeted serializer에서 생략합니다.
  일괄 `exclude_none=True`는 사용하지 않습니다.

`ExactRestoreOperationManifest.version`은 legacy `1`과 split policy `2`를 구분합니다.

- version `1`은 새 필드가 `None`이어야 하고 기존 overloaded 의미를 그대로 유지합니다.
- version `2`는 새 필드가 반드시 non-`None`이어야 합니다.
- 새 qualification manifest는 version `2`만 사용합니다. 같은 nonce·같은 v1 operation을 새
  의미로 재사용하지 않습니다.

### admission과 runtime의 동일 raw-read completion metric

version `2`의 `build_verified_baseline()`은 각 primary/corroborating artifact에서 다음을 독립
검증합니다. version `1`은 기존 첫 번째 검사만 수행합니다.

1. `pair_completion_gap_ns <= max_final_pair_gap_seconds`
2. `artifact.attempt.completed_monotonic_ns - artifact.attempt.started_monotonic_ns <=
   effective acquisition duration bound`

version `2`의 runtime `_capture_final_pair()`도 같은 두 의미를 검사합니다.

1. `max(observed_monotonic_ns) - min(observed_monotonic_ns)`를 completion coherence bound에 대조
2. `completed_monotonic_ns - verification_started_monotonic_ns`와
   `max(received_monotonic_ns) - min(requested_monotonic_ns)`를 acquisition duration bound에 대조

admission의 `pair_completion_gap_ns`는 두 participant의 `read_completed` 차이입니다.
`read_completed`는 `read_raw_state_capture()` 직후, identity-after보다 먼저 찍힙니다. runtime의
`observed_monotonic_ns`도 raw read 직후에 찍히므로 같은 completion-coherence 의미입니다.
identity-after 뒤의 `received_monotonic_ns`는 전체 acquisition window에만 사용합니다. 보존된
explicit-only pair의 raw-read completion gap 최대는 `11.681584751초`이고, `20초` coherence
bound는 그 실측을 기준으로 유지합니다.

admission의 total attempt와 runtime의 두 acquisition span은 같은 start-to-finish 계열이지만
timestamp 경계가 완전히 같은 metric이라고 주장하지 않습니다. 새 `30초` acquisition bound는 보존된
세 종류 span의 최대 실측 `22.795초`를 모두 덮도록 precommit합니다.

**정정 기록:** source 착수 전 static trace에서 이전 문안이 `read_completed`를 identity-after가 끝난
sample completion으로 잘못 해석하고 runtime 대응값도 `received_monotonic_ns`로 뒤집어 적은 것을
찾았습니다. artifact verifier가 보존하는 값과 runtime observer의 실제 stamp 경계에 맞춰 이 자리에서
정정했으며, 이 정정으로 current v1 operation을 재해석하지 않습니다.

runtime completion-coherence 대조는 현재 `_capture_final_pair()`에 없는 **version `2` 전용 신설
post-write 검증**입니다. version `1`에는 이 검사를 소급 적용하지 않습니다.
첫 write 이전 가설 gate가 아니고 composition admission과 runtime 의미를 맞추는 코드 내부 근거가
있지만, final verify 실패 경로 하나를 늘린다는 사실과 실측 여유(`11.681584751초 < 20초`)를 숨기지
않습니다. 실패하면 receipt를 발급하지 않고 metric별 진단을 남깁니다.

두 version 분기의 근거는 site별로 다릅니다.

- admission total-attempt 검사를 version `1`에 fallback `20초`로 적용하면, gap
  `11.155~11.682초`로 통과하던 보존 artifact 네 개의 total attempt `21.650~22.795초`가 전부 새로
  거부됩니다. 따라서 이 검사는 version `2` 전용입니다.
- runtime completion-coherence는 observation validator의
  `requested <= observed <= received`와 기존 request-to-receive window 정의 때문에 version `1`에서
  같은 `20초` bound를 쓸 때는 발화할 수 없습니다. 그래도 legacy 항등성을 이 부등식과 향후 구현
  불변성에 의존시키지 않고 기존 predicate 집합 자체로 고정하기 위해 version `2` 전용으로 둡니다.
- legacy 회귀는 같은 실측 형태(gap 약 `11.5초`, total 약 `22.8초`)에 대해 **version `1`
  admission은 수락하지만 runtime은 기존 acquisition 검사에서 거부하는 비대칭**까지 함께 고정합니다.
  이 비대칭이 current failure를 만든 기존 의미이며, source 업데이트로 고치거나 재해석하지 않습니다.

`_initial_sentinel_baseline_freshness()`가 `capture_age + max_final_pair_gap_seconds`로 older
participant를 보수적으로 보정하는 소비처는 **completion gap bound를 그대로 사용**합니다. 이
padding은 admission에서 강제한 participant completion gap을 모델링하므로 acquisition duration
field로 옮기지 않습니다. 따라서 기존 필드의 전체 소비처 네 곳은 다음처럼 분리됩니다.

| 소비처 | 적용 bound |
|---|---|
| baseline artifact `pair_completion_gap_ns` | completion coherence |
| final pair의 participant raw-read `observed` completion 차이 | completion coherence |
| first-write baseline age의 older-member padding | completion coherence |
| final verify 전체 소요와 request-to-receive window | acquisition duration |

어느 metric이 실패했는지, observed nanoseconds와 두 expected bounds를 privacy-safe diagnostic에
남깁니다. 상태 exact mismatch와 timing mismatch를 같은 `verify_mismatch` 결과로 유지할 수 있지만,
operator 출력에서 실패 predicate와 실측값을 구분해야 합니다. rejected observation의 raw나 private
endpoint를 출력하지 않습니다.

### 다음 operation의 사전 고정 값

current evidence에 근거해 다음 **새** manifest는 completion coherence `20초`, acquisition duration
`30초`, discovery timeout `5초`를 사용합니다.

- `20초`는 이미 통과한 completion gap `11.155..11.682초`의 coherence 보증을 느슨하게 하지
  않습니다.
- `30초`는 보존된 최대 total attempt `22.795초`와 고정 discovery 구조를 포함하면서 같은 값을
  completion coherence에 적용하지 않습니다.
- 이 값은 current manifest를 수정하지 않으며 새 nonce·새 operation id에만 결속합니다.

새 manifest 생성은 current journal의 parked disposition 완료, 새 source/image 검토와 fresh Phase 0
이후입니다. 이전 Phase 0 artifact의 observation age를 늘려 재사용하지 않습니다.

### legacy canonical compatibility

새 optional field는 nested policy를 포함하는 모든 dump 경로에서 legacy bytes를 바꾸지 않아야
합니다.

- `ExactRestoreVerificationPolicy.policy_sha256`
- `ExactRestoreBaseline.baseline_sha256`
- `ExactRestoreOperationManifest.manifest_sha256`와 그 operation ids
- deterministic target의 baseline digest를 통한 `action_plan_sha256`
- `ExactRestoreRecord.authority_context_sha256`
- consumed authority의 `verification_policy_sha256`·journal-context 대조
- `QualifiedBaselineArchive`의 enclosing canonical JSON round-trip

key가 없던 literal legacy manifest·record·qualified bundle fixture를 실제 deserialize한 뒤 위 digest와
operation id가 변경 전 hard-coded 값과 byte-identical인지, `model_validate(model_dump())`가 동등한지,
consumed authority를 가진 legacy recovery 검증이 여전히 통과하는지 테스트합니다. current journal의
canonical SHA도 offline fixture로 대조하되 private 원문을 저장소에 복사하지 않습니다.

## 작업 B — `PARKED_UNQUALIFIED` terminal disposition

### 성공 receipt와 완전히 다른 taxonomy

새 결과는 `ExactRestoreReceipt`나 version `1` success-finalization schema를 재사용하지 않습니다.
parked disposition record는 별도 model/namespace를 쓰되, operation replay의 영구 장벽은 기존
operation-digest finalization **슬롯**에 version `2` parked variant로 설치합니다. 이는 success index
재사용이 아니라 같은 operation id에 success와 park가 공존할 수 없게 하는 terminal-exclusion
namespace의 versioned 확장입니다.

```text
outcome = PARKED_UNQUALIFIED
qualification_achieved = false
device_network_access = false
device_write_count = 0
```

version `2` finalization은 `outcome=PARKED_UNQUALIFIED`, operation digest, cycle과 disposition digest를
담고 `receipt_sha256`을 담지 않습니다. Q2 evidence loader와 `QualifiedBaselineArchive`는 이 결과를
qualification으로 인식하지 않고 기존 `q2_restore_unfinalized`/receipt-absent 경계에서 닫혀야
합니다. 구체적인 기존 refusal은 finalization-mismatch를 포함할 수 있으며, 이 결과를 위해 Q2
production loader의 분기나 성공 의미를 바꾸지 않습니다. 반대로 exact-restore의 operation replay 검사는 success finalization과 parked disposition을
모두 terminal outcome으로 보고 같은 operation id의 create/arm을 거부해야 합니다.

legacy `ExactRestoreOperationFinalization(version: Literal[1])` decoder는 파일이 존재하는 version `2`
entry를 `None`으로 취급하지 않고 typed archive error로 닫아야 합니다. 따라서 구 binary도 `arm`
시 `_require_operation_not_finalized()`에서 장비 I/O 전에 실패합니다. 새 binary는 version `1` success와
version `2` park를 구분해 읽으며, 어느 쪽이 먼저 설치됐든 다른 쪽 설치를 conflict로 거부합니다.

모델·파일·필드 이름에는 `receipt`를 쓰지 않고 **parked disposition record**라고 부릅니다. success
receipt와 같은 명사를 공유하지 않아 사람과 후속 loader가 이를 qualification 증거로 오독하지 않게
합니다.

### 이번 journal에만 적용되는 eligibility

범용 “저널 지우기” 명령을 만들지 않습니다. 이번 terminalizer는 private disposition manifest와
current canonical journal을 대조하고 아래를 모두 만족할 때만 진행합니다.

- exact operation SHA와 journal SHA가 위 확정값과 일치
- cycle `sentinel_qualification`, phase `awaiting_final_verify`
- deterministic action prefix `8/8`, 각 outcome이 durable success이고 중복 action 없음
- `inflight is None`, `error_code is None`, `final_evidence is None`
- baseline/action-plan/authority-chain digest가 manifest의 exact 값과 일치
- 성공 receipt·success finalization·qualified bundle이 없음
- strict post-restore series reference가 원 series/digest/ordinal을 가리킴
- artifact를 `PilotSeriesStore`의 raw 재검증 경로로 다시 읽어 explicit-only action, identity,
  outer six-field와 432-byte schedule bytes가 journal baseline과 exact 일치
- artifact capture가 마지막 completed action과 awaiting transition 뒤에 시작됨
- artifact completion gap은 legacy 20초 이내이고 total attempt는 20초를 초과해 기록된 policy
  overload 근거와 일치

마지막 두 timing 조건은 restore qualification을 새로 PASS시키지 않습니다. 현재 operation을 왜
성공 receipt 없이 park하는지에 대한 evidence binding일 뿐입니다.

### 증거 보존과 durability 순서

park archive는 기존 receipt archive와 **같은 deployment-wide archive claim** 아래 success와 park의
상호 배타성을 강제합니다. 별도 lock을 둬 success/park가 경쟁하는 창을 만들지 않습니다.

1. global hardware-operation lease와 exact journal claim을 얻고 canonical journal bytes를 exact
   snapshot합니다.
2. digest-addressed immutable journal snapshot 파일을 no-clobber로 쓰고 file fsync, namespace fsync,
   exact reload를 수행합니다.
3. snapshot digest, operation/baseline/action-plan/authority-chain digest, action prefix, reason,
   post-restore artifact reference와 고정 false claim을 담은 immutable parked disposition record를
   같은 방식으로 보존합니다.
4. operation id를 parked disposition record에 묶는 immutable version `2` parked-finalization entry를
   기존 operation-digest finalization 슬롯에 설치하고 세 파일을 모두 exact reload·cross-check합니다.
5. 위 확인이 끝난 뒤에만 live journal을 `ExactRestoreJournalStore.save()`로
   `PARKED_UNQUALIFIED` terminal tombstone successor로 교체하고 exact reload·fsync 확인합니다.
   tombstone은 `version=2`, `journal_format=2`, `operation_id`, `phase="parked_unqualified"`, operation
   SHA, disposition id와 snapshot digest만 담고 receipt 형태의 필드는 담지 않습니다. 어떤 version의
   `ExactRestoreRecord`로도 validate되지 않아 구 binary의 `_load()`가 장비 I/O 전에 fail-closed하고,
   새 code의 status/disposition loader만 record validation보다 먼저 명시적으로 인식합니다. 최소
   `version`/`operation_id`/`phase`는 기존 recovery supervisor의 presence parser가 읽어 손상이 아니라
   `ATTENDED_REQUIRED`로 닫히게 합니다. 파일은 owner 일치, regular `0600`, `nlink=1`, symlink 없음의
   기존 journal metadata 계약을 그대로 만족해야 합니다.

crash ordering은 다음처럼 fail-closed여야 합니다.

- disposition index 전 crash: original live journal이 남고 orphan snapshot/disposition record는
  inert합니다.
- index 뒤 tombstone 전 crash: original live journal과 exact park disposition이 함께 남고 동일
  command가 idempotently archive를 확인한 뒤 tombstone 설치만 재개할 수 있습니다.
- tombstone 설치 뒤 output 전 crash: journal path 자체가 구·신 binary의 operation replay를
  차단하며 status 재호출이 같은 terminal result를 반환합니다.
- index가 backing snapshot/disposition record 없이 존재하거나 success와 park index가 동시에 존재하면
  corruption으로 거부하며 자동 수리하거나 journal을 지우지 않습니다. tombstone과 backing
  disposition이 불일치해도 같은 corruption입니다.

original live journal의 raw canonical bytes를 park archive에 보존하고 journal path에는 terminal
tombstone을 남기므로, 증거 삭제나 absent journal을 replay 허용 신호로 쓰지 않습니다. 저장소 run
기록에는 private snapshot 경로가 아니라 opaque disposition id와 digest만 남깁니다.

이 eligibility에 쓰인 단일 post-restore artifact는 current disposition의 사유를 결속하는 데만 쓰며,
향후 exact-restore operation의 baseline evidence나 dual-baseline 한쪽으로 재사용하지 않습니다.

### 두 replay 장벽의 lifetime과 다음 operation

- journal-path tombstone은 **즉시·잠정 장벽**입니다. park 완료부터 다음 새 operation의 정당한
  `prepare`까지 구 binary의 no-clobber `create()`와 모든 `_load()`를 막습니다.
- version `2` parked-finalization entry는 **operation별 영구 장벽**입니다. journal path가 다음
  operation에 재사용되거나 훗날 absent가 되어도 current v1 operation id는 영구히 다시 arm되지
  않습니다.
- 새 binary가 tombstone을 다른 operation의 `PREPARED` record로 교체하려면 global lease와 journal
  claim 아래에서 permanent parked-finalization 및 backing snapshot/disposition을 모두 exact reload한
  뒤, **서로 다른 새 operation id**임을 확인하고 `save()` successor로 한 번에 교체해야 합니다. 같은
  operation id, missing/corrupt permanent barrier 또는 일반 `create()` 우회는 모두 거부합니다.
- park disposition source commit만 설치된 중간 상태에서는 `create`,
  `create_qualified_final_restore`, `promote_to_baseline_restore`의 세 record 설치 경로가 **모든**
  tombstone successor를 fail-closed합니다. 이 구간에는 v2 policy를 증명할 record field가 아직 없기
  때문입니다. timing split commit이 착지한 뒤에만 record의
  `max_final_acquisition_duration_seconds is not None`을 1차 gate로, composition manifest version `2`를
  중복 gate로 삼고, 서로 다른 operation id와 permanent finalization/backing archive exact reload를
  같은 guard 안에서 모두 확인한 sentinel `PREPARED` successor만 엽니다.
- 이 중간 차단은 §3 P1 대응이 아니라 fail-closed 비용 통제입니다. v1 successor를 허용하면 같은
  overloaded final verify에서 두 번째 non-terminal journal이 생기지만, 이번 disposition eligibility는
  첫 operation SHA/journal SHA에 고정돼 그 새 journal을 terminal화할 수 없습니다.
- future v2 operation이 진행 중이면 그 manifest/policy schema 자체도 구 binary에서 fail-closed하지만,
  이것을 current operation의 영구 replay 장벽 대신 쓰지 않습니다.

tombstone이 존재하는 동안 현행 recovery supervisor는 그 fixed journal presence를
`ATTENDED_REQUIRED`로 취급합니다. 현재 supervisor는 이미 exited 상태이며, 이 plan은 이를 재시작하거나
수정하지 않습니다. 다음 operation의 reviewed `prepare`가 tombstone을 교체하고 그 operation이 terminal
완료되기 전까지 supervisor 재기동은 별도 운영 결정으로 남깁니다. 이는 HA 조작이 아닐 뿐 아니라,
Home Assistant에는 여전히 어떤 변경도 하지 않습니다.

parked 구간에 supervisor를 재기동해야 한다면 v1 tombstone을 terminal로 인식하는 supervisor/store
migration이 먼저 필요하며, 이는 이 plan 범위 밖의 별도 blocker입니다. 현재 non-terminal journal도
이미 supervisor를 `ATTENDED_REQUIRED`로 만들므로, tombstone은 이 구간의 supervisor 상태를 새로
악화시키지 않습니다.

### 별도 write-free attended authority

기존 `AttendedGrantIssuer`는 restore write scope를 발급하므로 park에 재사용하지 않습니다. 새
authority issuer와 snapshot/disposition/index writer는 `exact_restore_disposition.py` 안에 자족적으로
두며, 별도 domain과 challenge를 사용해 다음을 결속합니다.

- exact current journal SHA와 operation SHA
- disposition manifest SHA와 post-restore artifact/series digest
- `PARKED_UNQUALIFIED`, `qualification_achieved=false`
- `device_network_access=false`, `device_write_count=0`
- 5분 이하 wall/monotonic lifetime과 boot identity

challenge 화면은 “장비에 접근하지 않음”, “qualification receipt를 만들지 않음”, “불변 archive
확인 후 live recovery journal을 재개 불가능한 tombstone으로 교체함”을 명시합니다. repository
maintainer의 이 exact disposition
승인이 필요합니다. 장비 network를 사용하지 않으므로 on-site hardware write 승인을 대신 요구하거나
향후 Phase 1 승인으로 이월하지 않습니다.

CLI는 private manifest와 existing artifact root만 받고 device config·endpoint를 받지 않습니다.
import-graph 테스트는 writer·frozen native harness·HA/daemon/MQTT 모듈이 terminalizer에서 reachable하지
않음을 고정합니다. `PilotSeriesStore` 정본 재검증 때문에 `protocol.discovery`·`protocol.session`·codec은
허용된 transitive edge입니다. 이 두 protocol 모듈의 top-level은 상수·ABC·dataclass 정의뿐이고,
collector가 session에서 반입하는 것도 state action 상수와 raw capture dataclass뿐이며 실제
`ReadOnlyGizwitsSession`이나 writer를 반입하지 않는다는 현재 집합을 고정합니다.

## 구현 순서와 source scope

두 변경은 한 계획 아래 두 source commit으로 분리해 각각 digest review하며, **current journal을
다루는 park disposition을 먼저** 구현·검토·실행합니다.

1. **park disposition commit** — immutable archive/index, offline evidence verifier, distinct authority/CLI,
   replay exclusion과 crash/fault-injection tests
2. **timing semantics commit** — policy field, manifest version gate, admission/runtime metric routing,
   baseline-age padding routing, diagnostics와 legacy fixtures

park가 timing field에 의존하지 않으므로 이 순서가 가능합니다. timing serializer에 결함이 있어도
current live journal을 load할 수 없게 만들기 전에 disposition을 끝낼 수 있습니다. disposition
실행 결과 addendum을 append-only로 commit한 뒤 timing source를 수정합니다.

예상 source 범위:

- `src/jebao_flow/exact_restore.py`
- `src/jebao_flow/exact_restore_composition.py`
- `src/jebao_flow/exact_restore_receipts.py`
- `src/jebao_flow/exact_restore_cli.py`
- 새 write-free `src/jebao_flow/exact_restore_disposition.py`
- 위 모듈의 직접 unit/fault-injection tests

`exact_restore_q2_evidence.py`는 production 의미를 변경하지 않고 parked disposition이 qualification으로
소비되지 않는 직접 회귀가 꼭 필요할 때 테스트 대상만 추가합니다. `read_only_collector.py`,
`q2_epoch.py`, app verifier, frozen native schedule/linkage source, HA/daemon/MQTT 코드는 수정하지
않습니다.

## 검증 계약

- legacy literal JSON fixture의 canonical bytes/digest/operation id/authority consumption 불변
- v1 policy가 기존 overloaded behavior를 그대로 유지함
- v2에서 `11.681584751초 completion / 22.8초 acquisition / 20초 coherence / 30초 acquisition`이 PASS
- completion `>20초`는 total `<30초`여도 FAIL, total `>30초`는 completion `<20초`여도 FAIL
- v2 primary와 corroborating baseline artifact 모두 두 metric을 독립 통과해야 함
- timing mismatch diagnostic이 observed/expected 두 metric을 남기고 private 값을 내보내지 않음
- park eligibility 한 항목이라도 다르면 archive·tombstone mutation 모두 0회
- parked disposition record가 success receipt나 Q2 qualified evidence로 적재되지 않음
- 같은 operation의 success와 park가 어느 순서에서도 동시에 설치되지 않음
- park index가 설치된 뒤에는 `ExactRestoreController`의 public mutator 아홉 개
  (`create`, `create_qualified_final_restore`, `promote_to_baseline_restore`, `arm`, `reauthorize`,
  `recover`, `execute`, `finalize`, `clear_after_receipt`)가 모두 같은 terminal outcome을 보고 거부함
- archive file/index 각 fsync 경계 fault injection에서 증거 없는 journal tombstone mutation이 없음
- archive 완료 뒤 journal save late error는 exact tombstone successor일 때만 성공으로 수렴
- 같은 disposition 재호출은 idempotent, 다른 manifest/evidence는 conflict
- park CLI 전체 경로를 socket 생성과 asyncio datagram/connection 생성이 raise하는 OS 경계에서 실행해
  정상 완료하고, `DiscoveryProvider` 인스턴스화도 raise하도록 한 별도 실행에서 device network·write 및
  HA call이 모두 0임을 확인
- 구 binary를 모사해 park index를 전혀 모르는 경로로 tombstone 상태에서 `create`, `arm`, `execute`를
  각각 호출해도 모두 장비 I/O 전에 실패하고 device write가 0회임
- legacy version `1` finalization decoder가 present version `2` parked entry를 `None`으로 반환하지 않고
  fail-closed하며, 새 decoder만 backing disposition과 exact 결속해 읽음
- tombstone이 `_ExactRestoreArtifactPresence`에는 유효하지만 `ExactRestoreRecord`에는 유효하지 않고,
  owner/regular `0600`/`nlink=1`/no-symlink metadata가 유지됨. `ExactRestoreRecord`가 tombstone
  discriminator를 정식 필드로 받아들이지 않는 schema 회귀도 고정함
- 다음 서로 다른 v2 operation의 `prepare`는 permanent parked-finalization과 backing archive를 exact
  재검증한 경우에만 tombstone을 교체하고, 같은 operation id·missing/corrupt barrier는 거부함. 다음
  operation 종료 뒤 journal이 absent여도 legacy v1 operation의 `arm`은 permanent barrier에서 실패함

Linux/amd64 직접 unit suite와 전체 suite, format/lint, exact source/image attestation, Claude의 source
diff digest review를 통과하기 전 production image를 만들거나 current journal에 terminalization 명령을
실행하지 않습니다.

## 실행 경계와 다음 단계

1. 이 계획 commit과 Claude read-only review
2. 두 source commit과 각 exact diff review
3. device network가 없는 isolated copy에서 current journal snapshot과 preserved artifact를 사용한
   offline dry verification
4. repository maintainer에게 exact park disposition challenge를 제시
5. 승인 뒤 **device network 0**으로 disposition archive와 live journal tombstone 교체만 수행
6. archive/index/journal-tombstone을 서로 다른 fresh process에서 재검증하고 append-only run addendum
7. permanent version `2` parked-finalization 장벽을 다시 검증한 뒤 fresh Phase 0, 새 v2 manifest와
   별도 Phase 1 hardware approval을 준비하고, reviewed `prepare`만 잠정 tombstone을 새 PREPARED
   successor로 교체

4번 전까지 current hardware-safety volume을 변경하지 않습니다. 5번의 승인은 새 qualification이나
앱 action 권한이 아닙니다. 새 Phase 1은 새 operation id, fresh baseline, on-site 감시와 즉시 물리
차단 수단을 다시 요구합니다.

## §3과 §7 판정

- timing overload는 fail-closed 비용 결함이라 그 자체는 §3 P1이 아닙니다.
- legacy serializer 누락으로 current journal/authority를 못 읽게 만드는 경우는 rollback·journal
  손실 P1입니다.
- parked disposition record를 qualification으로 소비하거나 backing archive 전에 journal tombstone을
  설치하는 경우는
  각각 wrong-PASS와 journal 손실 P1입니다.
- park 뒤 같은 operation을 재실행할 수 있으면 duplicate write 가능성 P1입니다.
- 위 세 범주는 static trace, literal legacy fixture와 filesystem fault injection으로 검증할 수 있어
  §2의 코드 내부 결함 근거 요건을 만족합니다.

계획·구현·offline 검증이 실패하면 current journal과 앱 경로를 계속 park합니다. 장비는 이미 exact
baseline이고 제품의 software-independent actuator/group 트랙에는 영향이 없습니다.

작업 A가 착지하면 `30초` acquisition bound로 current v1 operation의 final verify를 다시 성공시켜
정식 receipt를 얻는 저비용 경로가 산술상 생깁니다. 그러나 그것은 승인 당시 v1 의미를 v2로 소급
재해석하는 일이므로 의도적으로 사용하지 않습니다. 이 결정의 비용은 fresh Phase 0, 새 v2 manifest,
새 operation id와 새 on-site 승인입니다.

## 실행 addendum — diagnostic artifact timing eligibility

reviewed park source의 `park-verify`를 device-network-free 격리 복사본에서 실행한 결과, 기존
diagnostic series는 `disposition_artifact_predates_restore`로 fail-closed했습니다. 마지막 completed
action은 `2026-09-04T01:44:56.378426Z`, artifact 시작과 완료는 각각
`01:46:39.219543Z`와 `01:47:01.230524Z`였지만, 두 번째 write-free final-verify reauthorization까지
반영한 current record의 `updated_at`은 `02:14:16.280108Z`입니다. 구현이 요구하는
`artifact_started_at > max(last_completed_action.completed_at, record.updated_at)`을 만족하지
않습니다.

이 결과는 §3 P1이 아니라 fail-closed evidence-eligibility 거부이고, write-free disposition에는
§2의 첫-write 이전 gate 규칙이 적용되지 않습니다. 거부 뒤 원본 journal SHA는 불변이고 device
network/write 및 park archive/index/tombstone 생성은 모두 `0`이었습니다. 나머지 digest·state
결속을 통과한 뒤 timing 조건에서만 닫혔으므로 current manifest가 두 번째 grant까지 포함한 journal을
정확히 가리킨다는 점도 확인됐습니다.

이번 막힘을 풀기 위해 `record.updated_at` gate를 완화하지 않습니다. 기존 diagnostic artifact는
overload와 byte-exact restore 근거로 계속 보존하되, repository maintainer가 별도로 승인한
**explicit-only strict read 1회**를 current journal의 최종 갱신 뒤 새 private series로 수집합니다.
그 reference만 새 disposition manifest에 결속해 `park-verify`를 다시 수행합니다. 새 capture와 park
사이에는 exact-restore reauthorization 또는 journal을 갱신하는 다른 attended 상호작용을 허용하지
않습니다. proxy 의미의 정정 여부는 park 완료 뒤 별도 안건으로 검토합니다.

## 실행 addendum — parked disposition 완료

fresh explicit-only pair와 새 evidence reference로 `park-verify`를 통과한 뒤 repository maintainer가
이 exact manifest에 결속된 write-free challenge를 승인했고, 작업 B의 disposition 실행이
완료됐습니다. durable 결과는 `PARKED_UNQUALIFIED`, `qualification_achieved=false`, device network와
device write `0`입니다. original journal canonical bytes는 immutable snapshot으로 보존됐고,
live path의 tombstone과 operation별 version `2` parked-finalization이 각각 잠정·영구 barrier로
설치됐습니다.

fresh status, archive↔tombstone 교차검증, supervisor presence parse와 실제 archive를 사용한
controller 거부를 독립 확인했습니다. 구 source image의 runtime 확인 범위는 `status`의 record parse
거부이고, legacy write entry의 장비 I/O 이전 fail-closed는 단위 테스트 범위입니다. disposition은
success receipt가 아니며 Q2 qualified evidence로 소비될 수 없습니다. 상세 opaque id·digest와 UTC span은
[실행 기록](runs/2026-09-04-exact-restore-qualification-4dcd8364.md)에만 둡니다.

동시 postcondition 확인 중 nonblocking archive claim 경합이 한 idempotent call을
`disposition_tombstone_invalid`로 닫았고, 순차 재호출은 동일 disposition으로 성공했습니다. 이는
변형이나 §3 P1이 아닌 fail-closed 결과지만 오류 코드가 실제 claim 경합을 오도합니다.
`record.updated_at` proxy 의미와 함께 작업 B 완료 뒤 별도 진단 안건으로 남깁니다.

이 addendum으로 작업 B와 실행 경계 1~6은 완료됐습니다. 다음은 작업 A의 별도 source commit이며,
current v1 operation을 새 의미로 재개하지 않습니다. fresh Phase 0과 새 v2 operation은 작업 A의
source/image review와 permanent barrier exact reload가 끝난 뒤에만 준비합니다.

## 실행 addendum — 작업 A 완료

작업 A(timing split)의 source commit이 `47866e57909bfd21000f3f41988ce177bb7c6c55`로 landed했습니다.
부모는 `4dcb9df93ac42c5ea03dccdbef81fbb78f1532aa`, staged binary diff SHA-256은
`073b8fc92db54f7df4bd3d705263dbc6daf2ef1bf2a0c2139dbc106bf589e13b`이며 8개 파일 `+636/-41`입니다.
커밋된 트리의 diff digest는 검토·시험한 트리와 동일합니다: **(b)**.

착지한 내용은 이 계획의 네 행 그대로입니다. version `1`의 admission은 pair-gap 검사만, runtime은
기존 total/window 검사만 유지하고, version `2`에서만 admission이 total attempt를, runtime이
raw-read completion coherence를 추가로 검사합니다. `None` fallback은 기존 runtime acquisition 검사
두 곳에만 값을 공급합니다. coherence는 admission `read_completed`와 runtime
`observed_monotonic_ns`에, acquisition은 `completed - verification_started`와
`max(received) - min(requested)`에 결속했습니다. `_initial_sentinel_baseline_freshness()`의 padding은
무변경입니다. tombstone successor는 policy non-`None`, manifest version `2`, 서로 다른 operation id,
permanent parked-finalization과 backing archive exact confirm을 한 guard 안에서 모두 만족할 때만
열립니다.

legacy canonical identity는 두 가지로 확인했습니다: **(b)**.

- legacy policy의 canonical JSON은
  `{"max_final_pair_gap_seconds":20.0,"max_observation_age_seconds":30.0}`이고 그 SHA-256은
  `d5fc156ee858817935ba7dd8a83e7e37e3789f2d785e8166f9a0d962de948f19`입니다. 새 key는 나타나지 않으며
  이 값은 프로젝트 코드 없이 재계산할 수 있습니다.
- 변경 전부터 하드코딩돼 있던 `baseline_sha256`
  `ab6291ea6837afd73efb5d95c35a779bf9762c4153ed5b135c6b30c276625c68` 단언이 수정 없이 통과합니다.
  baseline이 policy를 내포하므로 직렬화가 1바이트라도 달라지면 이 기존 단언이 먼저 깨집니다.

Linux/amd64 network-none 컨테이너에서 표적 5개 test file `152 passed`, 전체 unit `1834 passed`,
변경 8파일 ruff lint 통과를 확인했습니다. 실행 전 컨테이너의 소스·테스트 8파일이 worktree와 바이트
동일함을 대조했습니다: **(c)**.

integration은 첫 전체 실행에서 frozen `test_linkage_restore_transport.py`의 staged-report 개수
단언이 실패했지만, 같은 test 단독 3회 통과, HEAD 소스에서도 통과, 전체 재실행에서 `16 passed`로
재현되지 않았습니다. 에뮬레이션 부하에 의존하는 flake이며 이 커밋과 무관합니다. frozen test를
수정하거나 하드닝하지 않았습니다: **(c)**.

`ruff format --check`은 `exact_restore_composition.py`를 지적하지만 같은 config에서 변경 전 버전도
실패하고 저장소 전체 `73/201` 파일이 실패합니다. 강제 게이트가 아닌 기존 부채이고 지적된 hunk가
전부 이 커밋이 건드리지 않은 줄이므로 무관한 재포맷을 넣지 않았습니다: **(c)**.

이 addendum으로 실행 경계 1~6과 두 source commit이 모두 완료됐습니다. 남은 것은 실행 경계 7이며,
permanent version `2` barrier 재검증 뒤 fresh Phase 0, 새 v2 manifest와 별도 Phase 1 hardware 승인이
있어야 시작합니다. 이 커밋만으로 장비·앱·Home Assistant·private journal 조작 권한이 생기지 않습니다.

작업 B 완료 뒤로 미뤄 둔 두 진단 안건은 이제 착수 가능한 별도 안건입니다. `record.updated_at`을
awaiting-transition proxy로 쓰는 eligibility 의미와, archive claim 경합을
`disposition_tombstone_invalid`로 표시하는 오도가 그것입니다. 작업 A에서는 의도적으로 건드리지
않았고, 각각 자체 계획과 검토를 거쳐야 합니다.

## 2026-09-06 사후 정정 — permanent barrier 부재

이 계획이 만든 source와 당시 park 절차의 기록은 유지하지만, 2026-09-06 범위 한정 read-only
forensic에서 journal snapshot·disposition·tombstone·operation finalization 원본이 발견되지
않았습니다. 따라서 이 계획이 완료 조건으로 삼은 permanent barrier는 **현재 배포 상태에는
존재하지 않으며 재검증할 수도 없습니다.** 삭제 시각·주체와 실제 경로는 `UNKNOWN`입니다.

해당 operation은 source-level revocation으로 다시 차단하고, Dockerfile의 자동 anonymous safety
volume 경로와 deployment mount 계약을 먼저 수정합니다. 자세한 범위와 hardware `NO-GO`는
[`Hardware-safety volume durability incident and remediation`](hardware-safety-volume-durability-plan.md)을
따릅니다.
