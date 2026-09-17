# run: 2026-09-17-pro-async-bar-35-slot-feeding-schedule

## 목적·승인 범위

사용자가 현장에서 감시하며 수정한 35개 변경점의 일일 시간표를 Pro 두 대와 MCP-70 Bar에
적용했다. Pro의 기존 역할은 유지했다. 지정한 Pro 한 대는 master, 다른 Pro는
`async_slave`, Bar는 `independent`다. DC return 두 대·Aquarium·Dosing에는 명령을 보내지
않았다.

이 실행이 닫으려던 질문은 다음이다.

> 세 수류모터에 Feeding 3분, Constant35 5분 복귀를 포함한 35구간 시간표를 저장하고 기존
> master/Async 역할을 유지한 채 현재 구간까지 일치시킬 수 있는가?

동결된 native ASYNC 실험 하네스는 수정하거나 실행하지 않았다. 배포된 local-control native
API만 사용했다. Docker image를 다시 만들거나 서비스를 재시작하지 않았으며, 같은 runtime이
새 시간표를 Pro 432 B·Bar 384 B schedule image로 직렬화해 전송했다.

- 실행 source commit: `ce4f80f399adce513aa4ff83437765d6341a0a1c`
- runtime image: `sha256:4ece904bf877b178e9b3662037d69fcde048cb3d3e1635cef884b6f8aff3ee1a`
- protocol schema: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- 적용 UTC span: `2026-09-17T05:24:15Z`–`2026-09-17T05:31:29Z`

## 적용한 시간표

세 시간표는 00:00–24:00을 빈틈 없이 덮는 35구간이다. Feeding은 각 장비에 아홉 구간이며
각 구간은 정확히 3분이다. 각 Feeding 뒤에는 Constant35 5분 구간이 있고 그 다음 일반 운전
구간이 시작된다.

| 구분 | 크기 | 구간 | Feeding | image SHA-256 |
|---|---:|---:|---:|---|
| Pro master | 432 B | 35 | 9 | `a7d498a6fc9ba948450873a84c285c9b9676150997e8029f077542959868f10b` |
| Pro Async | 432 B | 35 | 9 | `d2ff37762380e9cc94c6af3bf12c7d1958e1c9debf4899d482e96ac8b6ec7701` |
| Bar | 384 B | 35 | 9 | `563489b723bb60b47af3688c953aaa387f1517cc19ff4e02db3fda5bf7e9f19e` |

Pro Feeding 행은 `mode=feed, Flow=0, FeedTime=3`이다. Bar Feeding 행은
`mode=feed, flow=null, FeedTime=3`으로 디코드된다. Bar의 `flow=null`은 슬롯 부재가 아니라
Feeding 모드가 별도 Flow 값을 사용하지 않는 정상 표현이다. 적용 뒤 retained 전체 시간표를
다시 읽어 세 장비 모두 entry 35개, Feeding 9개, invalid slot 0개, schedule enabled를
확인했다.

## 이전 native source 원복

이전 31구간 source의 마지막 Async 전환은 전달 증거가 없어 `RECOVERY_REQUIRED`로 남아 있어
새 Pro operation을 막았다. 일반 detach의 첫 slave 원복 action도 ACK 없이 `UNCERTAIN`이 됐지만,
그 직후 읽기에서 장비는 여전히 이전 `async_slave` 운전 상태였으므로 해당 atomic payload를
재전송하지 않았다.

이 경우만을 위한 별도 scalar assistance를 실행했다. master를 independent로 두고 slave의
timer·mode·Flow·frequency를 단계별로 준비한 뒤, 기존 detach가 첫 action을 새로 보내지 않고
현재 상태로 확인해 나머지 원본 image·controls를 복원했다. detach는 `VERIFIED`, 기존 source는
`ROLLED_BACK`으로 종료했다. 두 장비 모두 원 schedule image와 controls가 exact였다.

## 새 Pro master / Async 적용

새 source는 준비 controls, 장비별 image, TimerON, master·sync 역할을 확인했다. 마지막
`async_slave` action은 다시 ACK를 얻지 못해 source record에 delivery `UNKNOWN`과
`RECOVERY_REQUIRED`를 정직하게 남겼다. 이 명령을 재전송하지 않고 지원되는 master-cycle을
수행했다.

master-cycle은 `VERIFIED`로 종료했고 fresh pair 관측에서 다음을 동시에 확인했다.

- master: role `master`, schedule ON, 35구간, master image exact.
- Async: role `async_slave`, schedule ON, 35구간, Async image exact.
- 14:31 KST 활성 구간: master `Random / Flow 45 / Frequency 60`, Async
  `Random / Flow 40 / Frequency 60`.

따라서 현재 상태는 확인됐지만 원 Async action의 ACK를 만들어내지는 않았다. source journal은
여전히 unfinished이며, 후속 Pro write 전에 terminal 상태로 정리해야 한다.

## Bar 적용

Bar schedule request는 physical write 1회로 `VERIFIED`됐다. 새 image를 저장한 뒤 기존 schedule
ON과 independent 역할이 유지됐다. 14:31 KST 활성 값은
`classic / Flow 45 / Frequency 80 / PulseTide false`로 요청표와 일치했다.

## master Er03 재발

native durable evidence를 다시 디코드한 결과, master는 마지막 master-cycle 사후 read인
`2026-09-17T05:30:36.380015Z`까지 모든 fault가 clear였다. 새 MQTT-only collector의 첫 표본
`2026-09-17T05:35:24.794583Z`에는 master에서만 `Fault_no_liveload`가 assert됐다. 따라서 이번
재발 시점은 그 4분 48초 구간으로 좁혀졌다.

그때는 14:00–15:00 `Random / Flow 45 / Frequency 60` 구간이 계속되는 중이었고 Feeding이나
schedule boundary가 아니었다. Async ACK `UNKNOWN`은 다른 물리 장비에 대한 기록이므로 master
fault의 직접 원인으로 볼 수 없다. master는 power ON, schedule ON, role master, 35구간 exact와
현재 Auto 값 보고를 계속했다. 실제 원인이 무부하 오검출, 수중부 결합, 전원부, controller 중
무엇인지는 이 구조화 관측으로 확정하지 않는다.

## 24시간 읽기 전용 관측

opaque artifact `daily-three-pump-monitor-20260917.03`을 30초 cadence로 시작했다. collector는
MQTT state topic을 subscribe할 뿐 publish하지 않고 장비 session을 열지 않는다. 별도 finalizer가
완료 파일을 기다린 뒤 보존 sample에서 경계 수렴·역할·전체 image·fault episode를 분석한다.

- 시작: `2026-09-17T05:34:54.688993Z`
- 예정 종료: `2026-09-18T05:34:53Z`
- `hardware_write=false`, `mqtt_publish_count=0`
- collector SHA-256: `8857bbb92c95c5dc3e6f9cb9e80c7054765598e6daf1d61ce94f0fe6adf44954`
- analyzer SHA-256: `3812dbf19958434ad37fb1654ee559e9c43b21c860b9ffb995d2707961ed2a7e`
- `plan.json` SHA-256: `6b505611ef64366a17e8a3cee15951ece576bf226cf2933b0b4f701ad2781eef`
- 첫 sample SHA-256: `75594e3e853dd094f94b3a19f4aa5478275af820129c8bbd4e252762b59206a4`
- identity binding SHA-256:
  - master: `92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`
  - Async: `7b7be7835ebd264d3d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`
  - Bar: `6597ee042d85c7a9f438d270e9bff9bf443e6719ef8748fa919359aea892d840`

증거 등급은 journal과 MQTT state를 구조화·fsync한 **(b) preserved structured/durable daemon
artifact**다. 원본 wire frame이나 물리 유량 측정은 아니다.

## 현재 판정 — 관측 진행 중

**이 실행으로 새로 확정된 사실:** 세 장비가 Feeding 3분을 포함한 35구간 image를 exact로
저장했고, 기존 master/Async/independent 역할과 schedule ON을 유지한 채 현재 일반 운전 구간을
요청값으로 보고했다. Feeding은 빈 구간이 아니라 세 장비 각각 아홉 개의 명시적 mode slot이다.
또한 master의 `Fault_no_liveload` 재발은 native 적용 종료 뒤 fault-clear 사후 read와 첫 collector
표본 사이에서, Feeding·schedule boundary가 아닌 일반 운전 중 발생했다.

24시간 전체 경계 동작, hard power cycle 뒤 fault 해제·재발, 실제 물리 파형은 아직 확정하지
않는다. collector 종료 후 같은 파일에 append-only 결과를 추가한다.

## 추가 관측 — a4 software power cycle은 첫 write 전에 거부

사용자가 현장에서 a4 master 한 대에만 software `OFF → 10초 → ON`을 승인했다. 기존
local-control MQTT power topic으로 먼저 시도했으며, OFF가 `VERIFIED`된 경우에만 10초 뒤 ON을
보내도록 실행기를 구성했다.

- UTC span: `2026-09-17T05:47:42.866897Z`–`2026-09-17T05:47:47.936490Z`
- 초기 관측: power ON, role master, schedule ON, 35 entries, invalid slot 0,
  `Fault_no_liveload` asserted.
- 결과: `command_rejected_LocalControlExecutionError`; OFF 결과가 VERIFIED가 아니어서 ON은 보내지
  않았다.
- 해당 시도 구간과 직후(`05:47Z`–`05:49Z`) shared safety volume에서 생성·갱신된 파일은 0개였다.
  durable record 이전 게이트에서 끝났으므로 device send도 0회다.
- opaque artifact: `daily-three-pump-monitor-20260917.03/a4-power-cycle.json`
- artifact SHA-256: `16fc7b098f386813bd3ec7270d4153e67a56f1d66571fcd4d3c468d5d8f29362`

거부 원인은 같은 두 Pro를 소유하는 새 native source가 마지막 Async delivery UNKNOWN으로
`RECOVERY_REQUIRED`인 상태이기 때문이다. 이 rollback·복구 소유권 게이트를 삭제하거나 우회하지
않았다. 제조사 앱은 경고와 대상 이름 확인까지만 했고 사용자가 앱 조작을 중단시킨 뒤 종료했다.
앱 power control은 누르지 않았다.

`2026-09-17T05:53:36Z` 재확인에서 collector와 finalizer는 모두 running이었고 sample은 37개로
증가했다. 최신 표본의 master는 ON·master·schedule ON·35구간 exact·현재
Random/Flow45/Frequency60을 유지했다. mismatch는 `faults_clear` 하나뿐이었다. 따라서 이번
시도는 fault를 해제하지도, schedule·role·power를 바꾸지도 않았다.
