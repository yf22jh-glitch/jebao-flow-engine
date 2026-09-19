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
