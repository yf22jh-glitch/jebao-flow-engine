# Exact-restore disposition eligibility invariant and claim diagnostics

상태: **PLAN ONLY / DEVICE NETWORK 0 / JOURNAL CHANGE 0 / HA CHANGE 0**

작성일: 2026-09-05

선행:
[`Exact-restore final verification bound split and parked disposition`](exact-restore-final-verification-and-park-plan.md)

그 계획은 두 결함을 "park 완료 뒤 별도 안건"으로 남겼습니다. park와 timing split이 모두 착지했으므로
이제 각각을 자체 근거로 다룹니다. 이 계획은 **이미 끝난 일을 되돌리거나 막힘을 푸는 것이 아닙니다** —
두 결함 모두 현재 소비자가 없고, 다음에 같은 지점을 다시 만나지 않기 위한 수정입니다.

## 닫을 질문

1. parked disposition eligibility가 artifact 순서를 검사할 때, 실제로 필요한 불변식이 아니라 가장
   최근 journal 갱신 시각을 대리로 쓰는 것을 정확한 불변식으로 바꿀 수 있는가?
2. nonblocking archive claim 경합이 tombstone 무효로 보고되는 것을 retryable 조건으로 분리할 수
   있는가?

## 안건 1 — artifact 순서 불변식

현재 `src/jebao_flow/exact_restore_disposition.py`의 gate는 다음입니다.

```text
if artifact_started_at <= max(last_completed_at, record.updated_at):
    raise ExactRestoreDispositionError("disposition_artifact_predates_restore")
```

실제로 필요한 불변식은 **"artifact 시작 시점 이후에 device write가 없었다"**입니다. 그 성질은 이미
같은 함수 상류에서 강제됩니다: `completed_actions`가 manifest가 고정한 개수의 연속 prefix이고, 모든
outcome이 `WRITTEN_VERIFIED`이며, `inflight`·`error_code`·`final_evidence`가 모두 `None`이어야
합니다. 따라서 write 경계는 `last_completed_at`이고, `record.updated_at`은 **write가 아닌 journal
접촉**까지 경계로 삼습니다.

이 차이는 가설이 아니라 실측으로 드러났습니다. 2026-09-04 dry verification에서 artifact 시작
`01:46:39.219543Z`는 마지막 `WRITTEN_VERIFIED` action의 post-write verified completion 시각
`01:44:56.378426Z`보다 뒤였지만, 두 번째 **write-free** reauthorization이 `updated_at`을
`02:14:16.280108Z`로 밀어 유효한 증거가 거부됐습니다. 그 completion 시각은 wire-send 시각보다 뒤에
찍히는 **보수적** 경계입니다.

**변경**: `max(...)`에서 `record.updated_at`을 제거하고 `artifact_started_at > last_completed_at`만
요구합니다.

이것은 **gate 완화**이므로 편의가 아니라 근거로 정당화해야 합니다. 완화된 조건이 곧 불변식이고,
제거되는 항은 write가 아닌 사건을 경계로 삼던 항입니다. 그리고 이 수정은 **막힘을 풀기 위한 것이
아닙니다** — 해당 operation은 이미 새 capture로 park됐고, 현재 park 대상 journal은 존재하지 않습니다.

**의존 관계와 방어**: 불변식은 "write가 일어나면 `completed_actions`가 늘거나 `inflight`가 설정된다"에
의존합니다. **이 결합을 증명하는 것은 disposition 쪽 테스트가 아닙니다.** 새로 넣을 disposition
테스트는 이미 만들어진 record shape의 수락·거부만 고정하므로, 결합 자체는 wire 경계를 실제로 지나는
기존 exact-restore 테스트가 계속 지켜야 합니다.

결합을 지키는 기존 자산(계속 통과해야 함):

- `RestoreHarness.before_write`(`tests/unit/test_exact_restore.py:551-564`)가 **wire 경계에서**
  durable store의 `phase == RESTORING`과 `inflight["index"] == len(completed_actions)`를 단언
- cancellation-after-apply, disconnect 실패, uncertain write, inflight reconciliation,
  complete-prefix recovery 테스트가 "잠재 write 뒤 inflight 보존 또는 latch, post-read completion,
  재전송 0"을 계속 고정

이 계획이 새로 고정하는 것(record shape 한정):

- prefix가 짧은 record, `inflight`가 있는 record, `WRITTEN_VERIFIED`가 아닌 outcome을 가진 record는
  artifact gate에 도달하기 전에 상류에서 거부돼야 함
- artifact 이후의 **write-free** journal 갱신은 더 이상 거부하지 않아야 함
- artifact가 마지막 completed action보다 앞서거나 같으면 계속 거부돼야 함

**장래 위험 명시**: 위 결합이 나중에 깨지면 stale evidence로 recovery journal을 terminal화할 수 있고,
그 시점에는 §3의 journal/rollback 손실이 됩니다. 지금 source에는 그 결함이 없지만, 완화가 의존한다고
선언한 성질을 record-shape 테스트만으로 증명했다고 적지 않습니다.

## 안건 2 — archive claim 경합의 분리

`disposition_tombstone_invalid`는 같은 모듈의 네 지점에서 발생하고, 그중 셋은
`confirm_parked_tombstone` 호출을 `except Exception`으로 감쌉니다. archive claim은 nonblocking
exclusive이므로 다른 프로세스가 claim을 쥐고 있으면 즉시 claim 오류가 나고, 그것이 **"tombstone이
무효"**로 표시됩니다.

2026-09-04 park 직후 postcondition을 동시 실행할 때 실제로 발생했습니다. 파일·장비 변형은 없었고
순차 재호출은 동일 disposition으로 성공했지만, 표시된 코드는 운영자가 tombstone 손상으로 오판해
파괴적 조치로 기울 수 있는 문구였습니다.

**"claim 오류 타입만 재분류"로는 부족합니다.** `ExactRestoreReceiptArchiveClaimError`는 transient
contention 전용이 아니고, `exact_restore_receipts.py`의 **열 곳**에서 발생하며 그중 실제 nonblocking
contention은 `fcntl.flock(... LOCK_NB)`의 `BlockingIOError` **한 곳**뿐입니다. 나머지는
nested/already-active context, wrong·inactive owner release, active 중 archive/lock identity 변경,
owner process·thread 불일치와 forked instance로, 같은 객체를 다시 불러 해소되는 "잠깐 바쁨"이
아니고 일부는 ownership·integrity 위반입니다. 이들을 전부 retryable로 내리면 방향만 바뀐 같은
오분류가 됩니다.

**변경 1 — contention만 구조적으로 구분**: `ExactRestoreReceiptArchiveClaimError`의 좁은 subtype을
만들고 **`LOCK_NB`의 `BlockingIOError` 한 지점에서만** 그것을 올립니다. 나머지 아홉 지점은 기존 base
타입과 기존 fail-closed 진단을 그대로 유지합니다. 따라서
`src/jebao_flow/exact_restore_receipts.py`는 조건부가 아니라 **필수 범위**입니다.

**변경 2 — busy 신호가 내부 wrapper에 삼켜지지 않게 할 것**: disposition 모듈에는 broad wrapper가
겹쳐 있어 좁은 subtype이 public 경계까지 살아 오지 못합니다. `load_parked_disposition()`은 자체
`except Exception`으로 claim 오류까지 `disposition_archive_invalid`로 감싸고, 그 위에도
`disposition_archive_failed` wrapper가 있습니다. 그리고 `load_parked_disposition()`은 **여섯 곳**에서
호출됩니다 — `verify()`, `resume_if_archived()`, `park()`의 direct load 둘, `_load_exact_existing()`,
`public_parked_tombstone_status()`. confirm이 성공한 뒤 load 단계에서 경합하면 outer에서 subtype을
잡는 것만으로는 새 코드에 도달하지 않습니다.

⇒ subtype을 중앙에서 번역해 pass-through하고, 다음 public 경계를 **모두** 같은
`disposition_archive_claim_unavailable`로 수렴시킵니다.

- live tombstone의 direct confirm
- confirm 성공 뒤 parked-disposition load에서 생긴 contention
- `status`
- `park-verify`
- idempotent `park`과 resume

반대로 명시적 model·binding mismatch로 나는 네 번째 `disposition_tombstone_invalid`와 실제
archive·tombstone 손상은 **그대로 유지**합니다. **자동 retry loop는 넣지 않습니다** — operator가
순차 재호출할 수 있도록 정확한 코드만 반환합니다.

## §3 판정

둘 다 **P1이 아닙니다.**

- 안건 1은 유효한 증거를 거부한 **fail-closed 마찰** 결함입니다. 잘못된 PASS, device write, journal
  손실을 만들지 않습니다.
- 안건 2는 **진단 정확성** 결함입니다. 동작은 fail-closed이고 상태 변형이 없습니다.

## 하지 않는 것

- 작업 A의 네 행 timing 의미와 version `1` canonical identity는 건드리지 않습니다. 이 계획은 model
  필드나 직렬화를 바꾸지 않으므로 legacy digest에 영향이 없습니다.
- 장비 network·write, 앱 action, Home Assistant 조작, private journal·archive 변경을 하지 않습니다.
- frozen native schedule/linkage harness와 `read_only_collector.py`를 수정하지 않습니다.
- 이미 설치된 tombstone과 permanent version `2` barrier를 건드리지 않습니다.

## 범위와 순서

두 안건은 **서로 다른 위험 등급**입니다(gate 완화 대 진단 분리). 독립적이고 작으므로 각각 별도
commit으로 나누어 각자의 근거로 검토합니다.

1. **claim 진단 분리 commit** — 위험이 낮고 완화가 아니므로 먼저 착지시킵니다.
2. **eligibility 불변식 commit** — gate 완화이므로 결합 회귀와 함께 별도 검토합니다.

source 범위: `src/jebao_flow/exact_restore_disposition.py`,
`src/jebao_flow/exact_restore_receipts.py`(안건 2의 subtype 때문에 **필수**)와 두 모듈의 직접 test.

## 검증 계약

안건 1:

- 상류 거부, write-free 갱신 수락, artifact 선행 거부 세 회귀가 record shape 수준에서 고정됨
- 결합을 지키는 **기존** 테스트가 계속 통과함 — 특히 `RestoreHarness.before_write`의 wire 경계
  durable inflight 단언과, cancellation·disconnect·uncertain·reconciliation·complete-prefix recovery
  경로의 "재전송 0, inflight 보존 또는 latch" 단언

안건 2:

- 다른 process·instance가 lock을 보유한 경우만 Busy subtype으로 올라감
- nested owner, forked archive, wrong-thread·release, active 중 claim 변경은 Busy subtype이 **아님**
- confirm 단계 경합과 confirm 성공 뒤 load 단계 경합을 **각각 주입**해도 public 경계가 모두 같은
  `disposition_archive_claim_unavailable`로 수렴함
- 명시적 model·binding mismatch와 실제 손상은 계속 `disposition_tombstone_invalid`로 닫힘
- 자동 retry loop가 추가되지 않음

공통:

- disposition CLI/import graph에서 device network·write와 HA call이 계속 `0`
- legacy policy·baseline·manifest·operation·authority golden digest 무변경
- Linux/amd64 network-none에서 표적 test와 전체 unit suite, 변경 파일 ruff lint 통과

## 실행 경계

이 계획과 두 source commit은 **repository 변경만**입니다. 실행 경계 7(permanent barrier 재검증,
fresh Phase 0, 새 v2 manifest, 별도 Phase 1 hardware 승인)은 이 계획의 범위가 아니며, 앱 live-write와
새 qualification은 계속 **NO-GO / BLOCKING**입니다.
