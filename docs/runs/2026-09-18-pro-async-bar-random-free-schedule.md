# run: 2026-09-18-pro-async-bar-random-free-schedule

## 목적·승인 범위

사용자가 현장에서 감시하고 물리 전원을 차단할 수 있는 상태에서, Random을 제외한 새
35구간 시간표를 세 수류모터에 적용했다. 상단 Pro는 `master`, 하단 Pro는 `async_slave`,
MCP-70 Bar는 `independent`를 유지한다. DC return 두 대·Aquarium·Dosing에는 명령을 보내지
않았다.

이 실행이 닫으려던 질문은 다음이다.

> 기존 미완 Async source를 같은 명령 재전송 없이 원복한 뒤, 새 Random-free 35구간 image를
> 세 장비에 저장하고 master/Async 결속과 현재 활성 구간을 구조화 readback으로 확인할 수
> 있는가?

동결된 native ASYNC 실험 하네스는 수정하거나 실행하지 않았다. 배포된 local-control native
API만 사용했고 `src/` 변경, Docker image 재빌드, 서비스 재시작은 없었다.

- 실행 source commit: `1f221ceaa9f7f1137930ab3987612b041e7c9bdd`
- runtime image: `sha256:b81464ec1eb28d4a49936d462a53067c22c740122b51e390e0d3f2b2006bb6e0`
- protocol schema: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- operation script SHA-256: `eefa80a810c0c7b0b729270c0e988fb6973b7f66854ec81cedc004a9239a0e8b`
- physical-write UTC span: `2026-09-18T01:46:07.628784Z`–`2026-09-18T01:48:22.821478Z`

## 적용한 시간표

세 시간표는 00:00–24:00을 빈틈 없이 덮는 35구간이다. 각 장비에 Feeding 3분 구간이 아홉
개 있고, 각 Feeding 뒤에는 Constant35 5분 구간이 있다. Pro와 Bar 일반 운전 구간에는
Random이 한 건도 없다.

| 구분 | 역할 | 크기 | 구간 | Feeding | image SHA-256 |
|---|---|---:|---:|---:|---|
| Pro 상단 | master | 432 B | 35 | 9 | `73e0f45b5ce883f15dd89c14b5fe2d3bf8dce55a77bd47ab2285e7c60b7d1fec` |
| Pro 하단 | async_slave | 432 B | 35 | 9 | `2e6a9814c08ff86dc6c5f3a415e1df40c9e878d64d76ebb6fae3b1cae21b9616` |
| MCP-70 Bar | independent | 384 B | 35 | 9 | `ada710cc2257ffed85ec94a02348da137cf887858ea3a6a8cf3b3f447019e234` |

preview는 `hardware_write=false`였고 위 세 image digest와 35구간을 먼저 확정했다. 실제 적용
뒤 retained 전체 시간표를 다시 읽어 세 장비 모두 entry 35개, invalid slot 0개, schedule ON을
확인했다.

## 이전 source 원복

이전 source `kaga_daily_async_20260917_02`는 마지막 Async 전달 증거가 없어
`RECOVERY_REQUIRED`였고 새 Pro operation을 막고 있었다. recovery를 한 번 실행했으며 마지막
Async action을 재전송하지 않았다. 6-action detach child는 `VERIFIED`, 이전 9-action source는
`ROLLED_BACK`으로 종료했고 두 장비의 원 image와 controls가 exact임을 확인했다.

## 새 Pro master / Async 적용

새 source `kaga_daily_async_20260918_01`은 준비 controls, 장비별 image, TimerON, master와 sync
역할까지 ACK·readback으로 확인했다. 마지막 `async_slave` action은 다시 ACK를 얻지 못해
delivery `UNKNOWN`, source `RECOVERY_REQUIRED`를 그대로 보존했다. 그 action은 재전송하지
않았다.

지원되는 단일 master-cycle child를 실행했다. child는 두 action 모두 ACK·readback
`VERIFIED`였고 fresh pair에서 다음을 동시에 확인했다.

- 상단: role `master`, power ON, schedule ON, master image exact.
- 하단: role `async_slave`, power ON, schedule ON, Async image exact.
- 10:53 KST 활성 구간: 양쪽 모두 `Pulse / Flow 40 / Frequency 70`.

원 Async action의 ACK를 새로 만들지는 않았으므로 source record는 unfinished로 남는다. 이는
현재 운전을 원복하지 않고 전달 불확실성을 보존한 결과이며, 다음 Pro write 전에는 다시 terminal
recovery가 필요하다.

## Bar 적용

Bar request `kaga_bar_schedule_20260918_01`은 physical write 1회로 `VERIFIED`됐다. 적용 뒤
power ON, schedule ON, role `independent`, 35구간 image exact를 확인했다. 10:53 KST 활성 값은
`Sine / Flow 45 / Frequency 80 / PulseTide false`였다.

같은 시각 구조화 state에서 세 장비 모두 asserted fault가 0개였다. 이는 fault register의
보고값이며 실제 물리 유량 측정은 아니다.

## 새 24시간 읽기 전용 관측

기존 `daily-three-pump-monitor-20260917.03` collector와 finalizer는 중단·재시작하지 않았다.
새 시간표 전용 opaque artifact `daily-three-pump-monitor-20260918.01`을 별도 container로
30초 cadence에서 시작했다. collector는 MQTT state topic을 subscribe할 뿐 publish하지 않고
장비 session을 열지 않는다. finalizer는 완료 파일을 기다린 뒤 보존 sample을 분석한다.

- 시작: `2026-09-18T01:52:50.413073Z`
- 예정 종료: `2026-09-19T01:52:49Z`
- `hardware_write=false`, `mqtt_publish_count=0`
- wrapper collector SHA-256: `6cf723eeefa94af96d25af3f8a8c8166d31a76a2a291314ad13703a1f870ebcf`
- pinned base collector SHA-256: `8857bbb92c95c5dc3e6f9cb9e80c7054765598e6daf1d61ce94f0fe6adf44954`
- analyzer SHA-256: `3812dbf19958434ad37fb1654ee559e9c43b21c860b9ffb995d2707961ed2a7e`
- `plan.json` SHA-256: `c412ee485f97eda7bd1f12d26252796e6bb55dc1e3a57256086aa0cadd32bb7d`
- 첫 sample SHA-256: `4db5199f50b51233b7e8f074331b7cf354c5dd61528d25b4bf217e05cc224f05`
- identity binding SHA-256:
  - master: `92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`
  - Async: `7b7be7835ebd264d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`
  - Bar: `6597ee042d85c7a9f438d270e9bff9bf443e6719ef8748fa919359aea892d840`

첫 sample은 세 장비 모두 complete·fresh·online·power ON·schedule ON·role·35구간 exact·현재
Auto·fault-clear가 일치해 `all_match=true`였다. collector·finalizer와 전날 collector·finalizer
네 container는 모두 running, restart count 0이었다.

증거 등급은 journal과 MQTT state를 구조화·fsync한 **(b) preserved structured/durable daemon
artifact**다. 원본 wire frame이나 물리 유량 측정은 아니다.

## 현재 판정 — 관측 진행 중

**이 실행으로 새로 확정된 사실:** 이전 미완 source를 같은 Async 명령 재전송 없이 terminal
원복했고, Random이 없는 새 35구간 image 세 개를 exact로 저장했다. fresh pair와 첫 collector
sample에서 상단 master·하단 async_slave·Bar independent, schedule ON, 현재 활성 값, fault 0이
모두 계획과 일치했다.

24시간 전체 경계 수렴, Feeding 종료 후 3분·5분 전환, Er03 재발 여부와 실제 물리 파형은 아직
확정하지 않는다. collector 종료 후 이 파일에 append-only 결과를 추가한다.
