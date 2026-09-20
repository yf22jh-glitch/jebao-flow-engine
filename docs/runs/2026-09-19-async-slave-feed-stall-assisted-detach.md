# run: 2026-09-19-async-slave-feed-stall-assisted-detach

- 목적(한 문장): 22시간 동안 `Mode=feed`·`TimerON=false`로 멈춰 있던 하단 Async slave를
  다시 구동시키고, 그 장비를 소유한 미완료 native 기록을 종결한다.
- 이 실행이 답하려던 질문: 보류된 Async source가 장비를 소유한 상태에서 일반 scalar 명령과
  지정 복구가 각각 어떻게 거부되는가, 그리고 어떤 경로가 실제로 펌프를 되살리는가.
- 실행한 commit SHA: `1f221ceaa9f7f1137930ab3987612b041e7c9bdd`
  (runtime image `sha256:b81464ec1eb28d4a49936d462a53067c22c740122b51e390e0d3f2b2006bb6e0`)
- 사용한 명령·주요 파라미터: non-retained MQTT. 순서대로
  (1) scalar `manual_mode=constant`,
  (2) native `recover` 요청 id `kaga_async_recover_20260919_01`,
  (3) 운영자의 물리 전원 재투입,
  (4) native `recover` 요청 id `kaga_async_recover_20260919_02`,
  (5) native `assist_detach` 요청 id `kaga_async_assist_20260919_01`.
  대상 source record `kaga_daily_async_20260918_01`.
- 종료 지점(stage / failure code): `assist_detach`가 6 action 전부 `verified`로 종료했고
  source record는 `rolled_back`이 됐다.

## 실제 결과

1. **scalar `manual_mode=constant` 거부.** 브로커 status에 
   `{"reason":"command_rejected_LocalControlExecutionError","status":"rejected"}`가 발행됐다.
   보류 중인 source record가 그 physical device를 소유하고 있어 일반 명령이 차단된다.
   장비 전송은 0회였다.
2. **첫 `recover` 실패.** action 0의 `send_once`에서 `ProtocolTimeoutError`. 같은 시점에
   observer의 상태 읽기는 계속 성공했다(4초 지연). **읽기는 되고 제어 쓰기만 무응답**인
   구간이 존재했다. 기록과 장비 상태 모두 변하지 않았다.
3. **물리 전원 재투입 뒤 통신 회복.** 재기동 후 제어 쓰기가 다시 동작했다.
   같은 재투입으로 이 장비의 24시간 시계 오차가 15초로 정상화됐다.
4. **두 번째 `recover` 거부.** `_advance`의 단일 쓰기 보호가
   "이미 시도한 명령의 반환값 불일치: 재전송하지 않습니다"로 중단시켰다. 1번의 timeout이
   action 0을 `uncertain`으로 남겼기 때문이며, 재전송하지 않은 것은 계약대로다.
5. **`assist_detach` 성공.** 미확인 첫 해제 전용 경로가 별도 qualified scalar로 진행해
   6 action 전부 `verified`가 됐다.

- 장비가 실제로 돌려준 값:
  - 증거 등급: (b) preserved structured·durable daemon artifact
  - detach record `wm_detach_de6d2756fbca9947197811bcedfcb665ff6e6ba6651a8f617687bb75eccfb624`,
    journal `412110eee2cbe057e0026c3f49ad4d535661fac05c728124292c4551a1561eba`,
    context `7aefc7629aa4b4a1bb340581d5db04f06dd4870f948ec5aac969307df4ed7ce8`
  - source record journal
    `794055d6eaf09b674c0a98c017686590f6ff378df5c78c2e22e17c5484010c02`, phase `rolled_back`
  - action 00 `flow_pro_d7a8` verified(delivery UNKNOWN, 1번 timeout의 잔여),
    01·02 `flow_pro_d0a4`, 03·04 `flow_pro_d7a8`, 05 `flow_pro_d0a4` 모두 verified·ACKNOWLEDGED
  - 최종 하단: `SwitchON=true`, `TimerON=true`, `Linkage=independent`, `Mode=constant`
  - 최종 상단: `SwitchON=true`, `TimerON=true`, `Linkage=independent`
- 원복 검증 결과: 두 Pro 모두 attach 이전 원본 제어값과 원본 schedule image로 복원됐고
  detach record가 6/6 verified로 종결했다. 결속 이전 원본으로의 복원이므로 **Async 결속은
  해제**됐고 두 장비는 각자의 이전 시간표로 구동한다. 일일 Async 계획 image는 적용돼 있지
  않다.

## 정지 원인 조사

- 최초 `TimerON=false` 관측은 `2026-09-18T11:36:31Z` 샘플이다. 직전 샘플(11:36:01Z)은
  `ON`이었고 역할은 전후 모두 `async_slave`로 유지됐다. 마스터는 영향이 없었다.
- **저널 대조**: `2026-09-18T10:48:22Z` 이후 다음 기록은 `2026-09-18T12:45:15Z`다. 정지 시각을
  포함한 2시간 구간에 이 소프트웨어의 오퍼레이션이 **0건**이다. HA 자동화·대시보드 조작도
  명령 토픽을 거치면 기록이 남으므로 함께 배제된다. 데몬 로그의 공백은 근거가 아니다 —
  이 데몬은 실패만 로깅한다.
- **앱 명령 대조**: Pro 템플릿의 타이머 핸들러는 두 개이고(`{TimerON:g,SwitchON:!g}`,
  `{TimerON:!l,SwitchON:!l}`) **둘 다 현재 Linkage가 slave면 `Linkage=0`을 동반**한다.
  관측된 "타이머만 OFF, 역할은 async_slave 유지"는 어떤 앱 경로로도 만들어지지 않는다.
- **기각한 가설**: 정지 샘플에서 clock skew가 31초→1초로 떨어져 시계 보정을 의심했으나,
  전체 구간에 같은 형태의 변화가 311회 있었고 전부 타이머가 유지됐다. 장비 시계가 1분
  해상도라 생기는 샘플링 산물이다.

- **이 실행으로 새로 확정된 사실:** 보류된 Async source가 장비를 소유하면 일반 scalar 명령이
  거부되고, timeout으로 `uncertain`이 된 복구 프레임은 재전송되지 않으며, 그 상태를 푸는
  것은 `assist_detach`다. 그리고 **Async slave가 타이머를 잃으면 결속을 유지한 채 복구할
  수단이 현재 배포본에 없다** — `schedule_enable`은 slave에 대해 항상 `Linkage=independent`를
  동반하기 때문이다.
- 이 실행으로 확정하지 못한 것: `2026-09-18T11:36:31Z` 직전 `TimerON`을 끈 주체, 그 시점에
  제어 쓰기만 무응답이 된 이유, 물리 유량.

## 덧붙임 (2026-09-20) — 보존 프레임 재판독

§4에 따라 위 기록의 사실 기술은 고치지 않고 덧붙인다. 같은 저널의 보존 캡처를 시각순으로
전부 복호해 「확정하지 못한 것」 중 두 가지가 확정됐다. 새 실기는 없었다.

### 1. `Mode=feed`는 장비가 스스로 썼다

`kaga_daily_async_20260918_01`의 evidence chain 41행 중 하단 `flow_pro_d7a8` 구간:

| 시각(UTC) | stage | act | Linkage | manual Mode / Flow / Freq |
|---|---|---|---|---|
| 01:47:08.655 | `staged_post_role_pair` | 3 | independent | constant / 40 / 80 |
| 01:47:10.401 | `staged_after_read` | 4 | **sync_slave** | constant / 40 / 80 |
| 01:47:11.021 | `staged_post_role_pair` | 4 | sync_slave | **feed / 40 / 70** |

두 캡처 사이 이 소프트웨어의 전송은 **0건**이다. action 07은 ACK를 받고 01:47:10.401에
verified가 됐고, action 08은 아직 전송 전이며 그 BEFORE 증거가 같은 01:47:11 pair 캡처다.
452바이트 raw 차이는 네 바이트뿐이고 그중 데이터는 둘이다 — `byte[1] 0x02→0x07`
(constant→feed), `byte[3] 0x50→0x46`(80→70). 나머지 둘은 장비 시계(HMS)다.

같은 재작성이 2026-09-15 attach 세 건에도 있다. `wm_product_async_staged_5ac6e17_01`
14:56:54.552에 `flow_pro_d0a4`가 sync_slave가 되면서 manual이 `constant/20`에서 `sine/30`으로
바뀌었고, 그 값은 그 장비의 `AutoMode=sine`·`AutoFreq=30`과 같다. 09-18에는
`AutoMode=pulse`·`AutoFreq=70`이었는데 manual Mode는 `feed`가 됐다. Frequency는 AutoFreq와
일치하지만 **Mode 값의 선택 규칙은 `UNKNOWN`**이며 펌웨어 가설로 적지 않는다.

확정되는 사실은 하나다. **Pro는 slave 역할을 잡는 순간 자기 manual `Mode`와 `Frequency`를
우리가 보내지 않은 값으로 덮어쓴다.** 관측 4회 중 4회. `TimerON`이 내려가면 펌프가 실제로
도는 것은 이 manual 블록이고 `feed`는 정지값이다. `staged_controls()`는 `Mode="constant"`를
역할 전환 **이전**(timers 단계)에만 쓰고 이후에 다시 확인하지 않았으므로 그 값이 그대로 남았다.

### 2. 22시간 동안 풀리지 않은 것은 장비가 아니라 기록이다

- action 08(`{"Linkage":"async_slave"}`)의 `acknowledgement_sha256`은 `None`이고 AFTER 증거가
  없다 — `send_once`가 ACK 대기에서 실패했다. action 00~07은 전부 ACK가 있다.
- `_reconcile_async_reply`가 `accept_reports=False`로 한 번 읽었고, 그 창에서 보존된 5프레임
  (01:47:21.366~01:47:21.399)은 **전부 act=4, 전부 `selected=false`**다. act=3이 오지 않아
  읽기가 타임아웃했고 action 08은 `uncertain`으로 남았다.
- 그 직후 `wm_cycle_de6d2756…`(01:47:22~01:48:02)가 자동 실행돼 **verified**로 끝났다.
  그 기록의 `released_reply_pair` 01:47:57.706은 **act=3 명시 응답**으로 하단이
  `async_slave`·`SwitchON`·`TimerON`이고 시간표 image가 바이트 동일함을 증명한다.
  모니터 샘플도 01:47:15 `independent` → 01:47:45 `async_slave`로 바뀐다.
- **장비는 40초 만에 복구됐다.** 그러나 `cycle_locked`는 설계상 source를 건드리지 않아
  source는 `recovery_required`로 남았고, 보류 기록이 그 physical device를 소유하므로 이후
  22시간 동안 모든 일반 명령이 거부됐다 — `feed`를 지울 `manual_mode=constant`를 포함해서.
- 부수 확인: 01:47:08.655(하단 independent)에서는 같은 reply-only 읽기가 act=3을 받았고,
  01:47:10.401(역할 획득) 이후 보존된 모든 프레임은 act=4다. `wavemaker_master_cycle`이
  전 구간 `accept_reports=True`를 쓰는 이유가 이것이다.

- 증거 등급: (b) preserved structured·durable daemon artifact. 원본 wire frame이 아니라
  저널이 보존한 캡처(`action`·`status_sha256`·`wire_sha256`)와 그 452바이트 status다.
- **이 재판독으로 새로 확정된 사실:** (a) Pro는 slave 역할 획득 시 자기 manual 블록을
  덮어쓴다, (b) 역할을 가진 slave는 report 프레임(act=4)만 보낸다, (c) master cycle이
  성공해도 source가 닫히지 않아 장비가 계속 소유 상태로 남는다.
- 여전히 확정하지 못한 것: 장비가 manual `Mode`에 쓰는 값의 선택 규칙, `2026-09-18T11:36:31Z`
  직전 `TimerON`을 끈 주체, 그 시점에 제어 쓰기만 무응답이 된 이유, 물리 유량.
