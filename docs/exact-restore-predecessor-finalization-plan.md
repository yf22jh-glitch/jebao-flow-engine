# Exact-restore predecessor success finalization invariant

상태: **착지 완료 (commit `8856b13`) / DEVICE NETWORK 0 / JOURNAL CHANGE 0 / ARCHIVE CHANGE 0 / HA CHANGE 0** — 말미의 실행 addendum을 보십시오.

작성일: 2026-09-05

선행: [`Exact-restore version 2 qualification and app-observation unblock`](exact-restore-v2-qualification-plan.md)

철회된 일반화 계획의 세 결함 중 **blocker 3**을 원인에서 제거합니다. 이것이 재설계된 disposition
일반화보다 **먼저** 착지해야 하는 작업입니다.

> **검토 상태 고지**: 이 계획의 **계약**(두 경계와 crash ordering 9항목)은 peer 교차검증에서 나왔고
> 그 삽입 지점·안전 근거는 제가 코드에서 확인했습니다. 그러나 **이 문서 자체와 이후 구현은 독립
> 교차검증을 받지 못합니다** — peer의 예산이 소진됐습니다. 같은 세션에서 제 범위 산정이 세 번
> 틀렸고 셋 다 peer가 잡았으므로, 아래 범위 추정은 그만큼 낮은 신뢰도로 읽어야 합니다. 보완으로,
> 구현 시 모든 범위 주장에 대해 **저장 시점 decoder·실행 시점 분기·archive 시점 순서 세 계층을
> 각각** 확인하고 그 결과를 실행 addendum에 기록합니다. 세 번의 실패가 모두 "한 계층만 보고 세
> 계층을 판단한 것"이었기 때문입니다.

## 닫을 질문

sentinel cycle을 `FINAL_VERIFIED`로 끝내고 baseline cycle로 넘어간 뒤 그 baseline이 좌초했을 때,
**이미 8회 verified device write를 마친 sentinel operation**이 영구 replay 장벽 없이 남는 것을 막을 수
있는가?

## 결함 — 확인한 사실

`finalize()`는 receipt를 **반환만** 합니다. sentinel과 baseline의 receipt·finalization은 두 cycle이 모두
끝난 뒤 `clear_after_receipt()`에서야 함께 persist됩니다 — 그 함수가 `receipts = [expected]`를 만든 뒤
`qualification_final_record`가 있으면 sentinel receipt를 앞에 끼워 둘을 함께 저장합니다.

그 사이 구간, 즉 **promotion 뒤 baseline 완료 전**에는 sentinel operation id에 영구 finalization이
하나도 없습니다. baseline이 그 구간에서 좌초하고 이후 tombstone이 새 operation으로 교체되면, 그
sentinel id를 다시 만들고 arm할 수 있는 **replay 표면**이 남습니다. §3 duplicate-write입니다.

`arm()`은 이 구간을 막지 못합니다. baseline cycle에서 `arm()`은 embedded
`qualification_final_record`로부터 receipt를 재도출해 **record 자신의 `qualification_receipt_sha256`과만**
대조하고, archive를 보지 않습니다. embedded record가 자기일관적이면서 archive에는 아무것도 없는
상태를 구별하지 못합니다.

## 변경 — 두 겹의 경계

교차검증에서 나온 계약이며, 아래 삽입 지점과 안전 근거는 제가 코드에서 확인했습니다.

### 경계 1 — promotion이 record를 교체하기 **전**

`promote_to_baseline_restore()`는 이미 journal claim을 잡은 채
`qualification_receipt = _receipt_from_final_verified_record(qualification)`으로 **receipt를 손에 쥔
상태**로 `promoted`를 만들고 `_save_exact(promoted)`로 교체합니다. 그 교체가 replay 표면이 생기는
순간이므로, **receipt 도출과 `_save_exact` 사이**에서 sentinel receipt와 version `1` success
finalization을 persist하고 exact reload·confirm합니다. 확인 실패면 교체하지 않고 fail-closed합니다.

`finalize()`만 고치는 것으로는 부족합니다 — `promote_to_baseline_restore()`를 직접 부르는 caller가
우회할 수 있으므로, **강제는 promotion 경계에 있어야 합니다.** 두 곳이 공통 helper를 쓰는 것은
무방하나 promotion 쪽 강제는 빠질 수 없습니다.

### 경계 2 — baseline `arm()`의 write 전 재확인

baseline cycle의 `arm()`은 embedded sentinel receipt가 **archive의 receipt·finalization과 exact
일치**하는지 다시 확인합니다. 없거나 손상됐거나 불일치하면 **device I/O 이전에** 닫습니다.

이 두 겹이면 promotion fault 뒤나 이 변경 이전에 만들어진 promoted record에서도, predecessor
operation이 영구 장벽 없이 baseline write로 넘어가지 못합니다.

## 왜 이 배치가 안전한가 — 확인한 근거

- `promote_to_baseline_restore()`가 **이미 journal claim을 보유**하고 receipt도 이미 도출돼 있어,
  추가 load나 재도출 없이 최소 삽입으로 끝납니다.
- `_require_operation_not_parked()`는 version `2` parked만 거부하고 **version `1` success는 거부하지
  않습니다.** 따라서 조기 sentinel finalization이 promotion과 충돌하지 않습니다.
- `promote_to_baseline_restore()`와 `arm()`의 `_require_operation_not_finalized()`는 **promoted(baseline)
  operation id**를 검사합니다. sentinel id의 조기 finalization과 의미가 섞이지 않습니다.
- `persist_final_verified_receipt()`는 기존 항목이 있으면 `_require_exact_finalization_snapshot()`으로
  **exact 재확인**합니다. 따라서 나중 `clear_after_receipt()`가 sentinel을 다시 persist해도 충돌하지
  않고 멱등입니다.
- `recover()`는 현재 live operation id만 검사하므로 predecessor의 success finalization과 부딪히지
  않습니다.
- **Q2/App wrong-PASS가 열리지 않습니다.** `ExactRestoreQ2EvidenceVerifier`는 sentinel과 baseline
  양쪽 receipt·finalization, qualified bundle, staged phase-5 record를 **모두** 요구합니다. sentinel
  receipt 하나가 일찍 생겨도 PASS가 되지 않습니다.
- lock 순서는 현재와 같이 **journal → receipt archive** 단방향을 유지합니다.

## 이 변경 **이전에** 만들어진 promoted record

경계 2는 그런 record를 fail-closed로 거부합니다. **현재 그런 record는 없습니다** — live journal path에는
parked tombstone이 있고 promoted baseline record는 존재하지 않습니다. 따라서 마이그레이션이 필요
없으며, 이 사실을 실행 전 preflight에서 재확인합니다.

## crash ordering 검증 계약

1. receipt blob 설치 전 crash → sentinel `FINAL_VERIFIED` journal 유지
2. receipt blob 설치 후 finalization 설치 전 crash → inert orphan을 재시도가 멱등 복구
3. finalization exact 설치 후 promotion save 전 crash → sentinel `FINAL_VERIFIED` journal + replay
   장벽 유지, promotion 재시도 가능
4. baseline successor 교체 후 fsync ACK 유실 → exact reload된 successor만 수락, sentinel 장벽 유지
5. baseline arm 시 predecessor receipt·finalization이 부재·손상·불일치 → device observe/resolve/write
   **모두 `0`**
6. promotion 뒤 같은 sentinel operation id의 create·arm replay 거부
7. baseline 성공의 `clear_after_receipt()`가 이미 설치된 sentinel success bundle을 **exact 멱등**으로
   수락
8. sentinel만 archived되고 baseline·bundle·staged record가 없을 때 Q2/App verifier는 계속 **FAIL**
9. lock 순서가 journal → receipt archive 단방향으로 유지됨

## §3 판정

이 변경 자체는 device write를 하지 않습니다. 그러나 다루는 대상은 **§3 duplicate-write P1 방어**이고,
경계 1이 durable archive에 쓰므로 journal·rollback 범주도 살아 있습니다. 방어는 위 crash ordering
계약과, 확인 실패 시 교체하지 않는 fail-closed 성질입니다.

## 이 계획이 **해결하지 않는 것**

blocker 1(receipts decoder의 하드코딩된 사유·key 집합)과 blocker 2(baseline cycle의 정상
`ALREADY_SATISFIED` skip)는 그대로 남습니다. 재설계된 disposition 일반화가 따로 필요하며, 그 계획은
이것이 착지한 뒤에 씁니다.

또한 방금 착지한 eligibility 완화(`bdfaf5c`)의 근거가 **"전 outcome이 `WRITTEN_VERIFIED`"**에
의존하므로, 일반화에서 outcome 집합을 넓힐 때 그 근거를 재도출해야 한다는 항목도 그대로입니다.

## 하지 않는 것

- `finalize()`를 mutating API로 바꾸는 것을 **강제 수단으로 삼지 않습니다.** 강제는 promotion 경계에
  둡니다.
- 자동 retry loop를 넣지 않습니다.
- 이미 설치된 tombstone·permanent barrier·parked disposition을 수정하지 않습니다.
- 장비·앱·Home Assistant·private journal·archive를 접촉하지 않습니다.
- frozen native schedule/linkage harness를 수정하지 않습니다.

## 범위와 순서

예상 source 범위: `src/jebao_flow/exact_restore.py`(promotion 경계와 `arm` 재확인)와 그 직접 test.
archive 쪽은 기존 `persist_final_verified_receipt()`·`confirm_operation_finalization()`을 **호출만**
하므로 `exact_restore_receipts.py` 수정은 예상하지 않습니다 — 구현 중 필요해지면 그 사실을 먼저
기록합니다.

> 지난 계획에서 "하류는 이미 범용"이라는 범위 산정이 세 계층에서 틀렸습니다. 위 예상도 **구현 착수
> 시 각 계층(저장 시점 decoder, 실행 시점 분기, archive 시점)을 다시 확인한 뒤** 확정합니다.

단일 commit으로 진행합니다. 두 경계가 하나의 불변식을 이루고 한쪽만 있으면 우회가 남습니다.

## 검증 계약

- 위 crash ordering 9항목
- promotion이 sentinel finalization 확인 실패 시 **record를 교체하지 않음**
- baseline `arm()`이 archive 부재·손상·불일치에서 device I/O 전에 닫힘
- 기존 golden digest(policy·baseline·manifest·operation·authority) 무변경
- 이미 설치된 parked disposition record가 수정 없이 load되고 digest 불변
- Linux/amd64 network-none에서 표적 test와 전체 unit suite, 변경 파일 ruff lint 통과
- import attestation: 실행된 `jebao_flow`의 경로와 bytes가 reviewed commit과 일치

## 실행 경계

이 계획과 그 source commit은 **repository 변경만**입니다. v2 qualification의 4~6단계는 이것과 재설계된
disposition 일반화가 **모두** 착지하고 그 문서의 정정·preflight가 닫힌 뒤에만 승인 대상이 됩니다. 앱
live-write와 새 qualification은 계속 **NO-GO / BLOCKING**입니다.

## 실행 addendum — 착지

착지 사실은 다음과 같습니다: **(b)**.

- commit `8856b13`, 2파일 `+156/-3`, diff SHA-256
  `36f8910e96543c857617d314f99eb0ea6cc8093592ebcac767d1424a626d20f5`

두 경계가 계약대로 들어갔습니다. 경계 1은 `promote_to_baseline_restore()`에서
`_require_operation_not_finalized(promoted.operation_id)` 직후, `_save_exact(promoted)` **직전**에
`_persist_predecessor_success(qualification_receipt)`를 호출합니다. receipt는 그 자리에서 이미 도출돼
있어 추가 load가 없습니다. 경계 2는 `arm()`의 baseline 분기에서 embedded receipt digest 대조 **직후**
`_confirm_archived_success(...)`를 호출합니다. 두 helper 모두 receipt store가 없으면 `JOURNAL`로
닫습니다.

### 세 계층 확인 결과

범위 산정이 세 번 틀렸던 전례 때문에 구현 전에 세 계층을 각각 확인했습니다: **(c)**.

- **archive 시점**: `persist_final_verified_receipt()`가 receipt blob과 finalization index의 순서·fsync를
  내부에서 처리하고, 기존 항목이 있으면 `_require_exact_finalization_snapshot()`으로 exact 재확인하므로
  멱등입니다. 따라서 나중 `clear_after_receipt()`가 sentinel을 다시 저장해도 충돌하지 않습니다.
- **실행 시점**: `promote_to_baseline_restore()`가 이미 journal claim을 보유하고 receipt를 도출한
  상태이며, `_require_operation_not_finalized()`는 promoted(baseline) id를 검사하므로 sentinel id의 조기
  finalization과 의미가 섞이지 않습니다. `_require_operation_not_parked()`는 version `2` parked만
  거부하므로 version `1` success와 충돌하지 않습니다.
- **저장 시점 decoder**: version `1` 경로의 `_require_receipt_backing_finalization()`은 backing receipt
  존재만 요구하고 사유를 하드코딩하지 않습니다. 하드코딩은 version `2` parked 경로에만 있었고, 이
  변경은 그 경로를 지나지 않습니다.

### 회귀

- promotion이 predecessor를 archive한 뒤에야 journal을 교체함
- archive confirm이 실패하면 **journal을 교체하지 않고** 원래 sentinel record가 그대로 남음
- baseline `arm`이 archive 부재에서 `AUTHORITY`로 닫히고 **device write `0`**
- baseline `arm`이 archive가 다른 receipt에 결속돼 있을 때도 닫히고 device write `0`

기존 baseline fixture는 promotion을 건너뛰고 record를 직접 설치했으므로, 그 단계를 반영하도록
`_receipt_store_with_predecessor()`로 공통화했습니다. 이것은 테스트를 통과시키려는 우회가 아니라
**실제 흐름과 fixture의 어긋남을 이 변경이 드러낸 것**입니다.

전체 unit suite `1848 passed`, 변경 파일 ruff lint 통과. 실행 전 컨테이너 파일이 worktree와 바이트
동일함을 대조했고 import 경로도 `/work/src`로 확인했습니다: **(c)**.

### 남은 것

blocker 1(receipts decoder의 하드코딩된 사유·key 집합)과 blocker 2(baseline cycle의 정상
`ALREADY_SATISFIED` skip)는 그대로입니다. 재설계된 disposition 일반화가 다음 작업이며, 거기서
`bdfaf5c`의 완화 근거도 새 outcome 집합에 대해 재도출해야 합니다. v2 qualification 4~6단계는 계속
승인 대상이 아닙니다.

**이 구현은 독립 교차검증을 받지 못했습니다.**

## 실행 addendum 2 — 교차검증 재개, P1 발견, 계약 이행 완료

**앞 addendum의 "이 구현은 독립 교차검증을 받지 못했습니다"를 대체합니다.** peer의 예산이 복구돼
착지본을 독립 검토했고, 그 결과 **실제 §3 duplicate-write P1이 발견됐습니다.** 이 절이 그 경위와
최종 상태의 정확한 기록입니다.

### 발견된 P1 — immediate parent만 확인한 것

`prepare_qualified_final_restore_record()`는 phase-5 record의 `qualification_final_record`에 **baseline
final record**를 넣고, 그 baseline record 안에 다시 sentinel이 중첩됩니다. 첫 구현(`8856b13`)의
`arm()` 검사는 **직속 부모 하나만** archive와 대조했으므로, phase-5 record에서는 baseline만 확인되고
**중첩된 sentinel은 확인되지 않았습니다.**

따라서 baseline은 archive됐지만 nested sentinel은 archive되지 않은 상태가 public API로 구성 가능했고,
그 상태에서 phase-5 device write에 도달한 뒤 journal이 정리되면 그 sentinel operation id는 영구 장벽
없이 남아 **이미 수행된 8회 write가 중복될 수 있었습니다.**

**그리고 첫 구현에 함께 넣은 fixture가 이 경로를 녹색으로 고정하고 있었습니다.**
`_receipt_store_with_predecessor()`가 직속 부모 한 단계만 archive했고, 기존 phase-5 테스트가 그
상태에서 6 writes를 정상으로 단언했습니다. 전이적 확인으로 바꾸자마자 그 테스트가 즉시 실패한 것이
구멍의 존재를 증명했습니다: **(b)**.

### 수정 — `e0ca527`

diff SHA-256 `87d8f25d0f69a868eaf13f847c00e40c4a593b95476cac94e9b486e61a73f1e3`, 2파일 `+127/-11`.
peer가 같은 바이트를 독립 실행해 `tests/unit/test_exact_restore.py` **90 passed**를 확인했고
**SOURCE_COMMIT_GO**를 냈습니다: **(b)**.

- `arm()`이 embedded provenance **chain 전체**를 archive와 exact 대조합니다.
- 설치 경계는 `_create_prepared()`의 공통 journal-claim 지점에 두어 `create`와
  `create_qualified_final_restore` 양쪽을 한 곳에서 닫습니다. sentinel record는 chain이 비어 있어
  no-op입니다.
- `clear_after_receipt()`가 1단계 insert 대신 oldest→current 순서로 **전 chain**을 persist합니다.
- 배치는 tombstone 분기가 return한 **직후·설치 직전**입니다. 그보다 앞에 두면 parked operation의 거부
  코드가 `AUTHORITY`에서 `INVALID_BASELINE`으로 바뀌어 기존 계약을 깨뜨립니다. 구현 중 이 순서 결함을
  두 번 만들었고 두 번 다 회귀가 잡았습니다.

### 9항목 계약의 실제 이행 — `ccff2b5`

첫 착지 시점에는 **9항목 중 2개만** 이행돼 있었습니다. 그 사실을 스스로 보고한 뒤 나머지를
작성했습니다. diff SHA-256 `abd8c3865e8829e8fbcdb5f1d9f40217befd6c457eb3e7fb1ee173d43581339b`,
test 전용 `+165/-0`.

| # | 항목 | 이행 |
|---|---|---|
| 1 | receipt blob 이전 crash | ✓ `fail_persist` 주입 |
| 2 | blob 후 index 전 orphan → 재시도 멱등 복구 | ✓ fake에 분리 주입점 추가 |
| 3 | finalization 후 promotion save 전 crash | ✓ `fail_before_save` |
| 4 | successor 교체 후 fsync ACK 유실 | ✓ `on_save`에서 사후 raise, exact reload만 수락 |
| 5 | arm 부재·손상·불일치 → device I/O `0` | ✓ (기존 2개 + `observation_count` 단언 추가) |
| 6 | promotion 뒤 같은 sentinel id replay 거부 | ✓ |
| 7 | `clear_after_receipt()` 멱등 | ✓ 전 chain 흐름과 phase-5 end-to-end로 |
| 8 | sentinel만 archived → Q2 FAIL | ✓ Q2 verifier에 전용 회귀 |
| 9 | lock order journal→archive | ✓ archive 호출마다 journal claim 보유 단언 |

여기에 nested-chain 회귀 둘이 더해집니다 — baseline만 archive된 상태에서 staging은
`INVALID_BASELINE`·arm은 `AUTHORITY`로 거부되고 write와 observation이 모두 `0`, 그리고 clear 뒤 세
operation의 finalization이 모두 존재하며 같은 sentinel id의 `create`가 거부됨.

### 검증

exact-restore 계열 **332 passed / 0 failed**, 전체 unit **1855 passed / 1 failed**, 변경 파일 ruff lint
통과: **(c)**. 유일한 실패는 frozen linkage 영역(`test_linkage_transaction.py`)이고 이 변경과
무관합니다 — HEAD 소스로 되돌려도 실패하며 **실행마다 실패 집합이 달라집니다**(같은 조건에서 5개→4개,
이름도 상이). frozen test는 수정하거나 하드닝하지 않았습니다.

### 남은 비차단 관찰

`execute()` 자체는 chain을 재확인하지 않습니다. 현재는 live state가 tombstone이고 `create`·`arm`이
강제하며 archive가 no-clobber·fsync라 migration P1이 아니라는 데 peer와 판단이 일치합니다. 다만 이
안전은 **현재 상태에 의존**하므로, 이후 상태가 달라지면 다시 볼 항목으로 남깁니다.

blocker 1(receipts decoder의 하드코딩된 사유·key 집합)과 blocker 2(baseline cycle의 정상
`ALREADY_SATISFIED` skip)는 그대로이며, 재설계된 disposition 일반화가 다음 작업입니다.
