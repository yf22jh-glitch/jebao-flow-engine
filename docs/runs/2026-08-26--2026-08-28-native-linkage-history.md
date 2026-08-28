# 네이티브 Linkage 실기 이력 (2026-08-26 ~ 2026-08-28, 재구성)

> **재구성 문서입니다.** 이 실행들은 당시 실행 단위 기록 없이 수행됐고, 아래 내용은
> `docs/native-linkage.md`, `docs/schedule-linkage.md`, `docs/devices/README.md`,
> `docs/hardware-readiness.md`와 git 이력에서 재구성했습니다. **raw run artifact는 남아 있지
> 않으므로, 아래 "장비가 돌려준 값"에 해당하는 기술은 모두 당시 문서에 남은 운영자 관측을
> 재구성한 것이며 원본 프레임으로 재확인할 수 없습니다.**
>
> 이 문서는 `docs/runs/`의 one-run-one-file 규칙의 명시적 예외입니다. 실행 시점에 개별 기록이
> 없었던 legacy 구간을 하나의 집계 문서로 보존하며, 이후 실행부터는 파일 1개씩 만듭니다.

## 질문 구분

이 기간의 실행은 서로 다른 두 질문을 다뤘습니다. 섞어서 세면 안 됩니다.

- **Q1 (manual Flow 독립)**: `async_slave`가 마스터와 다른 **manual `Flow`** 값을 유지하는가.
  **직접 시도는 #1, #2, #7, #8의 4회**입니다. 2026-08-26~27의 hwtest 실행은 총 8회지만
  나머지 4회(#3~#6)는 Sync rollback 경계 검증이라 Q1을 다루지 않습니다.
- **Q2 (슬롯별 `AutoFlow` 독립)**: `async_slave`가 자기 **스케줄 슬롯의 출력(`AutoFlow`)**을
  마스터와 독립적으로 적용하는가. **직접 시도는 2026-08-28의 schedule-flow 실행 5회**입니다.

2026-08-28 종료 시점 판정: **Q1 = UNKNOWN / unqualified, Q2 = UNKNOWN**

## 실행 목록 (13회 = 08-26 1회 + 08-27 7회 + 08-28 5회)

| # | 날짜 | 도구 | 질문 | 조건 | 도달 지점 | 결과 |
|---|---|---|---|---|---|---|
| 1 | 08-26 | hwtest | Q1 | `bootstrap-active-schedule` 안에서 qualification + `master`/`async_slave` Linkage + live Flow | live Flow 변경 | 당시 "async-slave 전용 Flow 변경이 유지되지 않음"으로 기록(`c04b176`). **이 해석은 이후 철회됨** — §철회된 해석 참조. `write_validated`에 `SwitchON, TimerON, Linkage, Mode, Flow, Frequency` 기록 |
| 2 | 08-27 | hwtest | Q1 | Async 35/33, 150초, 60초에 slave 38% | slave 출력 변경 구간 | 안전 실패, 자동 exact restore 미완료. 영수증 0/2 |
| 3 | 08-27 | hwtest | — | Sync 31/31, `--duration 10` | — | `LinkageApplyError`. journal `none`. 0/2 |
| 4 | 08-27 | hwtest | — | Sync 31/31, `--duration 60` | — | 자동 rollback 뒤 `RECOVERY_REQUIRED/restore_failed` + `timer_on`. 0/2 |
| 5 | 08-27 | hwtest | — | Sync 31/31, `--duration 60` | slave detach | 두 장비 `restore_failed`. attended recovery 약 11초. 0/2 |
| 6 | 08-27 | hwtest | — | Sync 31/31, `--duration 60` | 완주 | **성공.** 자동 exact restore, journal `none`, **영수증 2/2** |
| 7 | 08-27 | hwtest | Q1 | Async 35/33 → slave 38%, 150초 | slave 변경 요청 후 | `LinkageRollbackError` + `timer_on_snapshot`. typed primary failure `none`. attended recovery 약 18초 |
| 8 | 08-27 | hwtest | Q1 | 동일 Async, version 2 진단 | LAN write/read-back 진입 | `write_attempted`, `adapter_verified=no`, `full_state_verified=no`, `samples=0`. rollback `restore_failed`. attended recovery 약 14초 |
| 9 | 08-28 | schedule-flow | **Q2** | TimerON Constant→Sine | **두 역할 적용 완료** | 역할 적용 후 slave manual `Frequency`가 원 snapshot과 다르게 유지됨을 확인, A→B 경계 전 fail-closed |
| 10 (`_08`) | 08-28 | schedule-flow | **Q2** | Constant 31/32 → Sine 35/40 | `role_preflight` | Linkage write 전 fail-closed. 세 journal `none` |
| 11 (`_09`) | 08-28 | schedule-flow | **Q2** | 동일 계획 | role run 첫 fresh capture (약 0.43초) | Linkage write·A→B sample 없음. 원인은 generic `fresh_capture`로만 남음 |
| 12 (retry4) | 08-28 | schedule-flow | **Q2** | 동일 계획 | **두 역할 write + A→B 경계 관찰 도달** | monitor가 5개 필드를 원자 전환으로 가정해 fail-closed. 300초 독립 증거 없음 |
| 13 (retry5) | 08-28 | schedule-flow | **Q2** | 동일 계획 | `preflight_clock` | 첫 Linkage write 전. 두 `NowTime` pair skew가 2초 gate 초과 |

Sync 실행(#3~#6)은 rollback 경계 검증이 목적이라 Q1/Q2 어느 쪽도 직접 다루지 않습니다.

### 원복

문서에 기록된 범위에서 각 실행의 원복은 최종적으로 완료됐습니다. 일부는 자동, 일부는 새 확인
토큰의 attended recovery를 거쳤습니다. 물리적 손상이나 영구적 상태 손실 기록은 없습니다.

다만 **"13회 모두 Observer가 시험 전 상태를 재확인했다"고 일반화하지 않습니다.** Observer
재확인이 명시적으로 기록된 실행은 일부이고, 나머지는 원복 성공만 기록돼 있습니다. 실행별로
어느 수준의 확인이 있었는지는 위 표의 근거 문서를 개별 확인해야 합니다.

## 이 기간의 관측과 증거 등급

**"확정"과 "관측"을 구분합니다.** 등급 정의는 [`docs/runs/README.md`](README.md)를 따릅니다 —
(a) preserved raw artifact, (b) preserved structured/durable daemon artifact,
(c) reconstructed operator observation.

**현재 (a)와 (b)에 해당하는 항목은 없습니다.** 이 기간의 모든 항목이 (c)입니다.

### 철회된 해석

**(#1)** 당시 `docs/devices/50dbc92221fd4d33ae69a1fedd43b555.yaml`에
"An async-slave-only Flow change did not persist"로 기록됐습니다. **이 해석은 이후
철회됐습니다.** 같은 파일의 현재 기술은 "delivery and full-state read-back were not proven;
independent Flow remains unqualified"이며, #8에서도 `adapter_verified=no`,
`full_state_verified=no`, `samples=0`으로 남았습니다.

즉 #1은 "슬레이브가 독립 Flow를 유지하지 않는다"를 확정하지 못했습니다. 전달과 read-back
자체가 증명되지 않았으므로 **Q1도 `UNKNOWN` / unqualified입니다.** 이 항목을 Q1의 실측
결과로 인용하지 마십시오.

### 등급 (b) preserved structured/durable daemon artifact — 현재 해당 없음

보존된 구조화 산출물의 **파일·id·digest를 실제로 확인한 항목이 없습니다.** 확인되면 아래
(c) 항목 중 해당하는 것을 (b)로 승격하고 승격 근거를 덧붙입니다.

### 등급 (c) reconstructed operator observation

아래는 모두 당시 문서에 남은 기술만 있고 보존 산출물을 확인하지 못했습니다. 다른 판단의
전제로 쓸 때는 이 등급을 함께 밝혀야 합니다.

- **(#6)** slave-first fresh rollback session 경계에서 저출력 Sync의 자동 exact restore가
  동작했고 두 물리 바인딩에 24시간 자격 **영수증 2/2**가 발급됐다는 기록.
  영수증은 원래 durable artifact로 남는 종류지만, **현재 그 보존 파일·id·digest를 확인하지
  못했습니다.** 따라서 "오프라인 재검증이 가능한 durable artifact가 남아 있다"고 단정하지
  않습니다. 실제 보존 artifact가 확인되면 그때 (b)로 승격합니다. 어느 경우에도 물리 파형
  검증은 아닙니다.

- **(#9)** native 역할 적용 후 슬레이브의 manual `Frequency`가 원 snapshot과 다르게 유지되는
  **역할 유발 side effect**. Q2에 대한 증거가 아닙니다.
- **(#12 retry4)** `Mode`, `Frequency`, `AutoMode`, `AutoFlow`, `AutoFreq`가 하나의 원자
  상태로 바뀌지 않고 서로 다른 report에 걸쳐 순차 수렴한다는 관측. 코드 이력(이후 커밋들이
  이 동작을 전제로 수정됨)과는 정합하지만 원본 프레임으로 재확인할 수 없습니다.
- **(#13 retry5)** 새 세션에서 읽은 두 장비의 `NowTime` **pair skew가 2초 gate를 초과**.
  이것이 말하는 것은 "두 Pro의 `NowTime`을 2초 이내로 동시에 읽는다고 가정할 수 없다"까지이며,
  **약 22~25초 batch 주기를 재확인한 것이 아닙니다.** batch 주기 수치는 별도의 읽기 전용
  capture에서 나온 것으로 기록돼 있으나 **그 capture의 보존 raw 메타데이터도 연결돼 있지
  않습니다.** 따라서 22~25초 역시 등급 (c)이며, 새 collector pilot의 preserved raw로
  재산출되기 전에는 판정 기준의 강제값으로 쓰지 않습니다.

## 확정하지 못한 것

- **Q1**: slave가 마스터와 다른 manual `Flow`를 유지하는가 → **UNKNOWN / unqualified**
  (#1의 "did not persist" 해석은 철회됨)
- **Q2**: slave가 자기 B 슬롯의 출력을 독립 적용하는가 → **UNKNOWN**
- `async_slave`의 물리 유량·파형·위상 → 미검증 (어떤 실행도 이 범위를 증명하지 않음)
- `_09`(#11)의 `fresh_capture` 하위 원인 → 미상. 이후 코드가 우회 경로를 만들어 같은 지점이
  재현되지 않으므로, 새 증거 없이는 확정 불가

## 이 이력에서 읽히는 패턴

- Q2를 직접 시도한 5회(#9~#13) 중 측정 지점(A→B 경계 관찰)에 도달한 것은 **#12 retry4 한 번**
  입니다. #10·#11·#13은 **첫 Linkage write 이전**에 종료됐습니다.
- 같은 기간 `ScheduleLinkageRunFailure`는 0개에서 73개로 늘었고 한 번도 줄지 않았습니다.
  그중 32개는 첫 Linkage write 이전 구간에서 발동합니다.
- `docs/devices/50dbc92221fd4d33ae69a1fedd43b555.yaml`의 async 관련 판정은 #1에서
  "did not persist"(변경이 유지되지 않았다)로 기록됐다가, 이후 증명 기준이 높아지면서
  "delivery and read-back were not proven; independent Flow remains unqualified"(미검증)로
  바뀌었습니다. **판정이 회수되고 질문이 다시 열린 것**이 이 이력의 특징입니다.
- #12의 비원자 수렴 관측은 **관측 방식만 바꾸면 무력화되는 제약**입니다. 평탄 구간을 길게 잡고
  반복 샘플링하면 필드 stagger와 장비별 report 지연은 판정에 영향을 주지 않습니다.

## 이 이력에 따른 결정 (2026-08-28)

- 네이티브 ASYNC write 하네스를 **동결**하고 재실기를 보류합니다.
- Q2는 **읽기 전용 관측**으로 먼저 판정을 시도합니다. 절차·전제조건·안전 순서는
  [실기·설치 환경 준비 기준](../hardware-readiness.md)을 따릅니다.
- Q2의 답이 PASS든 FAIL이든 software-independent 그룹 런타임과 actuator가 필요하므로
  (`groups/calculator.py`와 `mqtt/service.py`가 양쪽 다 `native_linked`를 거부하고,
  `groups/manager.py`는 미구현), Q2를 park하고 actuator 개발을 **병렬로** 진행합니다.
- 근거와 규칙은 [`AGENTS.md`](../../AGENTS.md)에 있습니다.
