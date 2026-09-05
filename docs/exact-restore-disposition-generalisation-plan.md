# Exact-restore parked disposition generalisation

상태: **PLAN ONLY / DEVICE NETWORK 0 / JOURNAL CHANGE 0 / ARCHIVE CHANGE 0 / HA CHANGE 0**

작성일: 2026-09-05

선행: [`Exact-restore version 2 qualification and app-observation unblock`](exact-restore-v2-qualification-plan.md)

그 계획을 쓰는 중에, parked disposition 기구가 **v1 overload로 좌초한 sentinel journal 전용**이고
범용이 아니라는 것이 드러났습니다. 그 계획의 5단계(baseline restore cycle, device write 6회)가
좌초하면 종결 경로가 없습니다. repository maintainer가 **선택지 1(먼저 일반화)**을 택했으므로 이
문서가 그 작업의 계획입니다.

> **검토 상태 고지**: 이 계획은 독립 교차검증을 받지 못했습니다. 평소 이 저장소의 중요한 결정은
> Codex와 Claude가 서로 검증하는데, 지금 Codex의 토큰이 소진됐습니다. 아래 근거는 전부 제가 코드에서
> 확인한 것이고 자기반박도 제가 했으므로, **독립 검증과 같은 강도로 보아서는 안 됩니다.** 특히
> "sentinel 전제는 다섯 지점뿐"이라는 범위 산정이 틀렸다면 이 계획 전체가 과소평가입니다.

## 닫을 질문

`awaiting_final_verify`로 좌초한 journal을, sentinel cycle뿐 아니라 **baseline restore cycle에서도**,
그리고 v1 overload가 아닌 사유로 좌초했을 때도, 증거 손실이나 qualification 오판 없이 종결할 수
있는가?

## 현재 무엇이 좁은가 — 확인한 다섯 지점

하류는 **이미 범용입니다**. `ParkedDispositionRecord.cycle`은 `ExactRestoreCycle`,
`completed_action_count`는 `int(ge=0)`이고, receipts의 version `2` finalization은 validator가 두 cycle을
모두 허용합니다. 좁은 것은 **입력 manifest와 eligibility뿐**입니다.

| # | 위치 | 현재 | 문제 |
|---|---|---|---|
| 1 | `ParkedDispositionManifest.cycle` | `Literal[SENTINEL_QUALIFICATION]` | baseline restore cycle을 표현 불가 |
| 2 | `ParkedDispositionManifest.completed_action_count` | `Literal[8]` | baseline restore cycle은 **6개** |
| 3 | `ParkedDispositionManifest.reason` | `Literal["final_verification_policy_overload"]` | overload가 아닌 사유를 표현 불가 |
| 4 | `ParkedDispositionRecord.reason` | 같은 `Literal` | 같음 |
| 5 | `verify_park_eligibility()` | `record.cycle is not SENTINEL_QUALIFICATION` 하드코딩 | 바로 아래 줄에 `record.cycle is not manifest.cycle` 대조가 **이미 있음** |

action 개수 차이의 출처는 `build_exact_restore_plan()`입니다. 두 cycle 모두 safe fallback 2 + restore
schedule 2 + restore outer 2를 갖고, sentinel cycle만 qualify sentinel 2를 더 갖습니다. 따라서 **8과
6**입니다.

overload 전용 증거도 따로 있습니다 — manifest의 `maximum_pair_completion_gap_seconds`와
`minimum_total_attempt_seconds`, 그리고 eligibility가 그 둘로 거는 두 검사입니다. 이것은 "이 operation은
gap이 bound 이내인데 total이 bound를 넘어서 실패했다"는 **overload 고유의 주장**을 결속하는 것이므로,
다른 사유에는 의미가 없습니다.

## 변경

1. **cycle 확장** — manifest의 `cycle`이 두 cycle을 모두 표현합니다. eligibility의 하드코딩된 sentinel
   검사를 제거하고, **이미 존재하는** `record.cycle is not manifest.cycle` 대조만 남깁니다.
2. **action 개수** — `Literal[8]` 대신 cycle에서 유도한 값과 대조합니다(sentinel `8`, baseline restore
   `6`). manifest가 임의 개수를 주장하지 못하게 validator로 고정합니다.
3. **사유 확장** — `reason`을 닫힌 집합으로 넓혀 기존 `final_verification_policy_overload`와, overload가
   아닌 좌초를 위한 값 하나를 둡니다. 값이 늘어나도 기존 값의 직렬화는 바뀌지 않습니다.
4. **overload 증거를 사유에 종속** — 두 bound 필드를 optional로 만들고, `reason`이 overload일 때만
   존재를 요구하며 eligibility의 두 검사도 그때만 수행합니다. 다른 사유에서는 두 필드가 없어야 합니다.

## legacy 호환 — 이미 설치된 disposition을 깨지 않을 것

실기에 설치된 parked disposition record와 permanent barrier는 그대로 읽혀야 합니다. `Literal`을 **넓히는**
것은 기존 값의 직렬화를 바꾸지 않으므로, 저장된 record의 canonical bytes와 `disposition_sha256`이
불변입니다. 이것은 `ExactRestoreOperationFinalization.version`을 `Literal[1]`에서 `Literal[1, 2]`로
넓힐 때 이미 쓴 기법과 같습니다.

manifest의 두 bound 필드를 optional로 만들 때는 **`None`일 때 그 key만 생략하는 targeted serializer**를
씁니다. 일괄 `exclude_none`은 쓰지 않습니다. 저장된 record는 bound가 아니라 **실측**
(`pair_completion_gap_ns`, `total_attempt_ns`)만 담으므로 이 변경의 영향을 받지 않습니다.

## §3 판정

**P1이 아닙니다.** 이 변경은 좁은 입력 검증을 넓히는 것이고, 넓어진 경로도 기존 eligibility의 나머지
전 항목(journal digest, operation·baseline·action-plan·authority-chain digest, 완전한 verified prefix,
`inflight`·`error_code`·`final_evidence` 부재, artifact 재검증, byte-exact 상태 비교)을 그대로 통과해야
합니다. 잘못된 PASS를 만들지 않고, device write가 없으며, 증거를 지우지 않습니다.

다만 **넓히는 변경이므로** 다음이 회귀로 고정돼야 합니다: 넓힌 뒤에도 cycle과 action 개수의 **불일치**
조합이 거부될 것, 그리고 overload가 아닌 사유에서 overload 증거 필드가 있으면 거부될 것.

## 검증 계약

- 저장된 기존 disposition record가 수정 없이 load되고 `disposition_sha256`이 불변임
- sentinel cycle + `8`, baseline restore cycle + `6`은 수락되고, 두 cycle과 개수의 **교차 조합**은 거부됨
- `reason`이 overload일 때만 두 bound 필드가 요구되고, 다른 사유에서 존재하면 거부됨
- overload 사유의 두 timing 검사는 그대로 유지되고, 다른 사유에서는 수행되지 않음
- eligibility의 나머지 항목은 두 cycle 모두에서 동일하게 강제됨
- baseline restore cycle로 좌초한 journal이 **end-to-end로 park되고** tombstone과 permanent barrier가
  sentinel 때와 같은 형태로 설치됨
- disposition CLI/import graph에서 device network·write와 HA call이 계속 `0`
- legacy policy·baseline·manifest·operation·authority golden digest 무변경
- Linux/amd64 network-none에서 표적 test와 전체 unit suite, 변경 파일 ruff lint 통과

## 하지 않는 것

- 이미 설치된 tombstone·permanent barrier·disposition record를 수정하거나 다시 쓰지 않습니다.
- eligibility의 다른 항목을 완화하지 않습니다. 이 계획은 **표현 가능한 입력의 범위만** 넓힙니다.
- v2 timing 의미와 version `1` canonical identity를 건드리지 않습니다.
- 장비·앱·Home Assistant·private journal·archive를 접촉하지 않습니다.
- frozen native schedule/linkage harness와 `read_only_collector.py`를 수정하지 않습니다.

## 범위와 순서

source 범위: `src/jebao_flow/exact_restore_disposition.py`와 그 직접 test. 하류가 이미 범용이므로
`exact_restore_receipts.py`는 건드리지 않을 것으로 봅니다 — 구현 중 필요해지면 그 사실을 먼저
기록합니다.

단일 commit으로 진행합니다. 네 변경이 하나의 의미("입력이 두 cycle과 두 사유를 표현할 수 있게 한다")를
이루고 서로를 필요로 하므로 나누면 중간 상태가 모순됩니다.

## 실행 경계

이 계획과 그 source commit은 **repository 변경만**입니다. 착지한 뒤에야 v2 qualification 계획의
4~6단계가 승인 대상이 되며, 그 단계는 여전히 on-site 승인·감시·즉시 물리 차단을 별도로 요구합니다.
앱 live-write와 새 qualification은 계속 **NO-GO / BLOCKING**입니다.
