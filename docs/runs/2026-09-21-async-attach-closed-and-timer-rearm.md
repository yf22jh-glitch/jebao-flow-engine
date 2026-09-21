# run: 2026-09-21-async-attach-closed-and-timer-rearm

- 목적(한 문장): 09-18 정지의 두 결과(22시간 소유 교착, 복구 불가)를 없앤 빌드로 Async 결속을
  다시 세우고, 결속이 정상 종료되어 장비가 해제되는지 확인한다.
- 이 실행이 답하려던 질문: master cycle이 자기 act-3 응답으로 source의 Async 역할 action을
  닫아 장비 소유를 푸는가?
- 실행한 commit SHA: `919bfab9f31047802b1b0cfd594e7985ba464185`
  (runtime image `sha256:a1733a465f4a509960745f621952f9fc70c848c6fd9ae65ad07dded16e5fa911`)
- 사용한 계획: `kaga_daily_async_20260918_01`의 저장된 계획 그대로(35구간, feed 9구간,
  최대 Flow 45). 배포 전 live journal 대상 read-only 전제 검사를 통과시켰다.
- 사용한 명령: MQTT native `attach` `kaga_daily_async_20260921_01`
  (`master_cycle_on_async_failure=true`)
- physical-write UTC span: `2026-09-21T02:0x`–`02:1x`

## 실제 결과 — 결속 성공, 그리고 처음으로 기록이 닫혔다

| | |
|---|---|
| 결과 | `accepted=true`, `current_state_verified=true`, `kind=master_link_cycle_v1` |
| 상단 `flow_pro_d0a4` | `Linkage=master`, `SwitchON`, `TimerON`, 시간표 image 일치 |
| 하단 `flow_pro_d7a8` | `Linkage=async_slave`, `SwitchON`, `TimerON`, 시간표 image 일치 |
| source `kaga_daily_async_20260921_01` | **`verified`**, 9 action 전부 `verified` |
| action 08 (`{"Linkage":"async_slave"}`) | `verified`, **ack 없음**, diagnostic `native_async_role_verified_by_master_cycle_delivery_unknown` |
| `original_async_delivery` | `UNKNOWN` |
| 미완료 기록 | **0건** |

Async 역할 쓰기는 이번에도 ACK를 받지 못했다. 지금까지 모든 실행에서 그랬고, 결속을 완성한
것은 언제나 master-only로 동작하는 master cycle이다. 달라진 것은 그 다음이다. cycle이
released 구간에서 얻은 **act=3 명시 응답**(하단이 `async_slave`·전원·타이머·시간표 바이트
일치임을 증명한 그 프레임)을 source의 action 08에 되돌려 써서 기록을 닫았다. ACK 부재는
지우지 않고 `delivery_unknown`으로 남겼다.

- **이 실행으로 새로 확정된 사실:** Async 결속이 정상 종료되어 **장비가 해제된다.**
  2026-09-18에는 같은 결속이 `recovery_required`로 남아 22시간 동안 그 펌프의 모든 일반
  명령을 거부했고, `feed`를 지울 `manual_mode=constant`조차 보낼 수 없었다. 이제 미완료
  기록이 0건이므로 일반 명령이 바로 나간다.
- 증거 등급: (b) preserved structured·durable daemon artifact.

## 함께 배포한 복구 경로

`slave_timer_rearm`을 장비가 실제로 받아들이는 순서로 교체했다. 기존 4 프레임은 전부 결속된
slave 대상이라 ACK를 받을 수 없었고(2026-09-18 재진입 실패가 이것이다), 새 순서는

1. `상단 → Linkage=independent`
2. `하단 → schedule_enable=True` (앱 shape상 `SwitchON`이 꺼진다)
3. `하단 → power=True`
4. `하단 → Linkage=async_slave`
5. `상단 → Linkage=master`

이다. 1·2는 2026-09-20 03:03:51~03:03:56에, 4·5는 09:30:22.483~09:30:24.477에 ack·적용이
확인된 것과 같은 shape다. manual `Mode`는 쓰지 않는다 — 링크 중에는 장비가 되돌린다. 복구
대상인 `TimerON`은 링크가 강제하지 않으므로(09:30:26.015 `TimerON=true`) 유지된다.
이 자식은 이제 정상 종료된 attachment에서도 쓸 수 있다. 실제 관문은 `_require_pair_state`의
장비 상태 조건(상단 결속 유지, 하단 전원 ON·타이머 OFF·Async 역할, 두 image 바이트 일치)이다.

이 경로는 아직 **실기로 확인되지 않았다.** 조건을 인위적으로 만들 수 없기 때문이다 —
결속된 slave에는 `schedule_enable` 쓰기가 ACK를 받지 못하므로 타이머를 일부러 끌 수 없고,
그 조건은 장비가 스스로 만든다. 다음 발생 때 확인한다.

## 현재 상태

- 상단 `master` / 하단 `async_slave`, 둘 다 power ON·schedule ON, 일일 35구간 계획 적용
- 하단 manual `Mode=feed` — 장비가 결속 시 강제하는 값이며 세 가지 쓰기 순서를 실기로 모두
  배제했다. `TimerON`이 살아 있는 동안은 무해하다.
- 관측 둘 가동 중: `pro-pair-full-state-recorder-20260920.01`(장비 세션 0, 값 변화 전량),
  raw 앵커 series `JFS-7974c178ad369a750f63417429e19f26`(5분, 462바이트 wire frame)
- 확정하지 못한 것: `2026-09-18T11:36:31Z`에 하단이 스스로 `TimerON`을 끈 방아쇠.

## 덧붙임 — 자동 방아쇠

복구 경로가 있어도 누가 보고 있지 않으면 09-18처럼 22시간이 지난다. 그래서 결속된 pair를
감시하다 조건이 서면 위 복구를 스스로 돌리는 프로세스를 붙였다.

- artifact: `slave-timer-watchdog-20260921.01` (`/data/slave-timer-watchdog-20260921-01`)
- script SHA-256: `2e773c5ce70db286e23f69ab32794825679c21fa692a44251bce20e7e3996b9e`
- 감시 대상: source `kaga_daily_async_20260921_01`, master `flow_pro_d0a4`,
  slave `flow_pro_d7a8`
- 발동 조건(전부 동시 성립): 두 장비 `online`, 상단 `linkage_role=master`,
  하단 `linkage_role ∈ {sync_slave, async_slave}`, 하단 `power=ON`,
  하단 `schedule_enable=OFF`
- 발동 전 확인: 15초 주기로 **4회 연속**(약 60초) 유지될 때만 — 09-18 드롭은 첫 표본부터
  연속이었으므로 손해가 없고, 순간 변동이나 우리 자신의 operation 중에 오발동하지 않는다
- 상한: 한 번에 하나, **쿨다운 30분**, **최대 3회 시도**. 초과하면 요청 대신 `repair_declined`를
  기록한다
- publish는 `rearm_async_slave_schedule` 요청 **한 종류뿐**이고, 모든 판단(요청·결과·거부·
  연결오류)을 durable 파일로 남긴다

조건 판정은 실장비 연결 전에 따로 검사했다.

| 상태 | 판정 |
|---|---|
| 정상(타이머 ON) | 거부 |
| **타이머 OFF + 결속 유지** | **발동** |
| 타이머 OFF + 하단 결속 해제 | 거부 |
| 타이머 OFF + 상단 결속 해제 | 거부 |
| 타이머 OFF + 하단 오프라인 | 거부 |
| 타이머 OFF + 하단 전원 OFF | 거부 |

가동 90초 시점에 기록은 `00001-plan.json` 하나이고 요청은 0건이다. 조건이 서지 않았기
때문이며 의도대로다.

- **이 덧붙임으로 새로 확정된 사실:** 09-18 정지의 감지·복구가 사람 개입 없이 한 관측 주기
  안에서 끝나는 경로가 가동됐다. 실제 발동은 장비가 조건을 만들 때 확인한다.
