# run: 2026-09-17-pro-async-bar-daily-schedule

## 목적·승인 범위

사용자가 현장에서 감시하며 제시한 31개 변경점의 일일 시간표를 Pro 두 대와 MCP-70 Bar에
실제 적용했다. 사용자가 지정한 Pro 한 대를 master, 다른 Pro를 `async_slave`로 구성했고,
Bar는 독립 시간표를 저장한 뒤 schedule을 켰다. 실제 identity 원문은 private 입력에서만
대조했고 저장소에는 남기지 않았다.

이 실행이 닫으려던 질문은 다음이다.

> 지정한 Pro master/Async 조합과 Bar에 서로 다른 전체 일일 시간표를 적용하고, 장비가 보고한
> 역할·활성 시간표·현재 Auto 값을 요청표와 일치시킬 수 있는가?

Aquarium, DC return, Dosing에는 명령을 보내지 않았다. 동결 대상 native ASYNC 실험 하네스는
수정하거나 실행하지 않았고, 배포된 local-control native API만 사용했다.

- 실행 source commit: `ce4f80f399adce513aa4ff83437765d6341a0a1c`
- runtime image: `sha256:4ece904bf877b178e9b3662037d69fcde048cb3d3e1635cef884b6f8aff3ee1a`
- protocol schema: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- UTC span: `2026-09-17T04:06:14.654021Z`–`2026-09-17T04:13:39.430519Z`

## 적용한 시간표

세 시간표는 00:00–24:00을 빈틈 없이 덮는 31구간이다. `S`는 Sine, `P`는 Pulse,
`C`는 Constant다. Bar의 `P`는 앱 계약의 `classic + PulseTide=false`로 인코딩했다.
급여 구간의 Constant frequency는 0으로 인코딩했다.

| 구분 | 크기 | Flow 범위 | image SHA-256 |
|---|---:|---:|---|
| Pro master | 432 B | 30–45 | `b359562e081fb3bad42209a93dbd9496ad8229bf87eab5365b975212b2d2e3b5` |
| Pro Async | 432 B | 30–40 | `3b5fa8ff2a1eb076a2a62058ca367cb1b01f8518fb8b9708f11b44ea306d72d7` |
| Bar | 384 B | 30–45 | `d6be1dc756d71a9db750396ee1ad35ae6dbaa0e8b7ef0c38ef945b5af02cd0cd` |

Pro 두 이미지는 시간·mode·frequency가 같고 사용자가 지정한 Flow만 다르다. 먼저 native와 Bar
각각 preview를 실행했으며 둘 다 `hardware_write=false`였고, preview가 만든 image SHA가 위
로컬 컴파일 값과 일치한 뒤 apply로 진행했다.

## Pro master / Async 실행 결과

native source request `kaga_daily_async_20260917_01`은 준비 controls 2개, 장비별 image 2개,
TimerON 2개, master/sync 역할 2개까지 여덟 action을 `VERIFIED`로 기록했다. 마지막
`async_slave` action은 명령을 재전송하지 않은 채 ACK·직접 확인을 얻지 못해
`UNCERTAIN / native_stage_unverified`로 남았다. 따라서 source record 자체는 정직하게
`RECOVERY_REQUIRED`이며 원 Async payload의 전달 여부는 **UNKNOWN**이다.

지원되는 master-cycle을 별도 실행해 master 역할을 `independent → master`로 다시 설정했다.
cycle record는 두 action 모두 `VERIFIED`, `accepted=true`, `current_state_verified=true`로
종료했다. 이 cycle은 UNKNOWN인 Async payload를 재전송하지 않았다.

그 뒤 새 MQTT 관측에서 다음이 동시에 일치했다.

- master: role `master`, schedule ON, 31구간, master image exact.
- slave: role `async_slave`, schedule ON, 31구간, Async image exact.
- 13:13 KST 활성 구간: master `Pulse / Flow 40 / Frequency 80`, Async
  `Pulse / Flow 35 / Frequency 80`.

현재 요청 상태는 확인됐지만 원 Async action의 delivery UNKNOWN을 성공으로 바꾸지는 않았다.
그래서 source record 하나가 전역 journal의 유일한 unfinished record로 남아 있고, 겹치는 Pro
operation은 이 소유권이 정리되기 전까지 막힌다. 현재 master/Async 시간표를 유지하기 위해
이번 실행에서는 recover/detach를 하지 않았다.

## Bar 실행 결과

Bar schedule apply request `kaga_bar_schedule_20260917_01`은 한 action을 `VERIFIED`로 끝냈다.
직후 fresh read에서 저장 image는 요청한 31구간과 exact였지만, 장비 시각이 오래된 값이라 그대로
schedule을 켜면 잘못된 구간을 선택할 상태였다. 그래서 schedule OFF 상태에서 clock sync를 먼저
수행했고, `mqtt_b707c6652e399094fa255a31210cd816`이 `VERIFIED`로 끝난 뒤 오늘 장비 시각을
fresh read로 확인했다. 이어 `mqtt_ae5b9f9e52f440c43c8b818e7dc5f6bc`로 schedule을 켰고
이 operation도 `VERIFIED`로 끝났다.

최종 retained 관측은 Bar role `independent`, schedule ON, 31구간 exact이며 13:13 KST 활성 값은
`Sine / Flow 45 / Frequency 70 / PulseTide false`였다.

## 보존 증거

opaque artifact id는 `daily-three-pump-schedule-20260917.01`이다. 원본 wire frame을 보존한
artifact는 아니며, 실제 durable journal과 데몬의 retained MQTT 상태를 대조해 만든 구조화
산출물이므로 증거 등급은 **(b) preserved structured/durable daemon artifact**다.

| artifact | SHA-256 |
|---|---|
| `final-report.json` | `1dc0dbce44a4d8f3b323f372a67ebe17a577b7ba9302899e935572468038ae1f` |
| `schedule_operation.py` | `47400a78080241ce6164630419c1e7e3163d0b2f51a928e4beed2f948e54b18d` |

대상 identity binding SHA-256은 master
`92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`, Async
`7b7be7835ebd264d3d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`, Bar
`6597ee042d85c7a9f438d270e9bff9bf443e6719ef8748fa919359aea892d840`이다.

실행 전 operation은 73개·unfinished 0개였고, 실행 후에는 78개다. 새 record 5개가 정확히
증가했으며 전체 phase는 verified 65, rolled_back 12, recovery_required 1이다. 유일한 unfinished는
위 native source다. 관련 durable record SHA는 다음과 같다.

| record | phase | SHA-256 |
|---|---|---|
| native source | recovery_required | `e32597e0e36b9aec9d9bf6052cd710b57738b897387230f8f3eebeed3f91d5a1` |
| master cycle | verified | `214444e2d47bba6efed36e83565892e81ad3852d2fa684875e70fec41c83a283` |
| Bar schedule | verified | `858666bec0e1899354715bf2ea595ae84e76bbe61f84bd19ba8410c43b135ee4` |
| Bar clock | verified | `9cd3ed407ed0430de4b37fb7c4bda3a984be55c2fcc227ef91adcda1619c1abe` |
| Bar enable | verified | `df408b3fb21a5654d6f6bf3d4adfe3d68e80b3a25124e4b5cad0eb9f64ae3bf6` |

## 판정

원복 대상은 이전 운전 상태가 아니라 사용자가 요청한 새 일일 운전 상태다. 최종 exact image,
schedule ON, 역할, 현재 Auto mode/flow/frequency까지 요청과 일치했다. Aquarium·DC return·Dosing
write는 0이다.

**이 실행으로 새로 확정된 사실:** 배포된 local-control 경로가 한 Pro를 master, 다른 Pro를
`async_slave`로 유지하면서 서로 다른 Flow의 31구간 이미지를 적용했고, master-cycle 뒤 fresh
관측에서 역할·전체 image·현재 Auto 값이 요청표와 함께 일치했다. Bar도 장비 시각을 먼저 바로잡은
뒤 독립 31구간 schedule을 켜고 현재 구간을 요청값으로 보고했다.

이 실행은 원 Async action의 최초 delivery, 실제 물리 유량·파형, 아직 지나지 않은 미래 경계의
전환을 확정하지 않는다. 미래 경계 확인이 필요하면 추가 write 없이 read-only 관측으로 답할 수 있다.
