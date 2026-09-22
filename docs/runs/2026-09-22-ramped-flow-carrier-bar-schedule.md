# run: 2026-09-22-ramped-flow-carrier-bar-schedule

## 목적과 승인 범위

사용자가 현장에서 IoT 전원 차단 수단을 보유한 상태로 승인한 31구간 시간표를 Pro GMP 두 대와
MCP-70 Bar에 적용했다. 상단 Pro는 `master`, 하단 Pro는 `async_slave`, Bar는 `independent`를
유지했다. 하단 Pro는 각 구간의 하단 Flow를 보존하고 non-feeding 구간을 constant carrier로
저장했다. DC return, Aquarium, Dosing에는 명령을 보내지 않았다.

이 실행이 닫으려던 질문은 다음이다.

> Feeding 6분 아홉 구간과 완만한 Flow 전환을 포함한 새 시간표를 세 펌프가 exact 수용하고,
> GMP Master/Async 결속 및 현재 구간을 유지하는가?

- 실행 source commit: `5febb10`
- runtime image: `sha256:8104855832dff72c9b93de0bceadebcaa5a98d551f3f23ed6f7fa194b013daa5`
- protocol schema: `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27`
- 새 Pro source id: `kaga_ramped_flow_carrier_async_20260922_01`
- Bar request id: `kaga_bar_ramped_schedule_20260922_01`

동결된 `devices/*` native ASYNC 실험 하네스는 수정하거나 실행하지 않았다. 배포된
local-control API만 사용했다. 기존 full-state recorder, raw anchor, packet capture와 timer
watchdog는 중단하지 않았다.

## 적용한 31구간

`Tide`는 Pro `tidal`(Tide Rise and Fall)이며 Frequency를 사용하지 않아 wire 값은 0이다.
MCP의 Pulse는 앱 계약의 `classic + PulseTide=false`다. Feeding은 세 펌프 모두 6분이고 Pro의
Flow는 0, Bar의 feed byte는 `FeedTime=6`이다.

| 시작 | 상단 Pro mode / Flow / Freq | 하단 Flow | MCP mode / Flow / Freq |
|---|---|---:|---|
| 00:00 | Sine / 35 / 80 | 40 | Pulse / 40 / 80 |
| 05:57 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 06:03 | Sine / 35 / 80 | 40 | Pulse / 40 / 80 |
| 06:57 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 07:03 | Sine / 35 / 80 | 40 | Pulse / 40 / 80 |
| 08:40 | Sine / 37 / 80 | 40 | Pulse / 40 / 80 |
| 08:50 | Sine / 38 / 80 | 40 | Pulse / 40 / 80 |
| 08:57 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 09:03 | Tide / 40 / 0 | 40 | Sine / 40 / 80 |
| 10:27 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 10:33 | Tide / 40 / 0 | 40 | Sine / 40 / 80 |
| 11:40 | Tide / 40 / 0 | 40 | Sine / 42 / 80 |
| 11:50 | Tide / 40 / 0 | 40 | Sine / 43 / 80 |
| 11:57 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 12:03 | Pulse / 40 / 80 | 40 | Sine / 45 / 80 |
| 13:27 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 13:33 | Pulse / 40 / 80 | 40 | Sine / 45 / 80 |
| 13:40 | Pulse / 42 / 80 | 40 | Sine / 45 / 80 |
| 13:50 | Pulse / 43 / 80 | 40 | Sine / 45 / 80 |
| 14:00 | Tide / 45 / 0 | 40 | Pulse / 45 / 80 |
| 14:57 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 15:03 | Tide / 45 / 0 | 40 | Pulse / 45 / 80 |
| 16:27 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 16:33 | Tide / 45 / 0 | 40 | Pulse / 45 / 80 |
| 17:40 | Tide / 43 / 0 | 40 | Pulse / 43 / 80 |
| 17:50 | Tide / 42 / 0 | 40 | Pulse / 42 / 80 |
| 17:57 | Feeding / 0 / 0 | 0 | Feeding / - / 0 |
| 18:03 | Sine / 40 / 80 | 40 | Pulse / 40 / 80 |
| 20:40 | Sine / 38 / 80 | 40 | Pulse / 40 / 80 |
| 20:50 | Sine / 37 / 80 | 40 | Pulse / 40 / 80 |
| 21:00 | Sine / 35 / 80 | 40 | Pulse / 40 / 80 |

preview는 장비 write 없이 31구간·Feeding 9개와 다음 이미지를 확정했다.

| 대상 | image bytes | image SHA-256 |
|---|---:|---|
| Pro 상단 master | 432 | `118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9` |
| Pro 하단 carrier | 432 | `aaa0f30824d4e2b15e3eeb9e948e665ba5b63562330e8fc0aa20a2a20af22b6f` |
| MCP-70 Bar | 384 | `d76f20c3f6bfeacb6cb163c48234a51088d7d958e9a7adcd2eb9255473725d3c` |

## 기존 결속 exact detach

기존 source `kaga_flow_carrier_async_20260922_01`의 일반 detach 첫 compound action은 ACK와 적용
확인을 얻지 못해 `UNCERTAIN`으로 보존됐다. 같은 payload를 재전송하지 않았다. scalar assist도
master role-only action은 ACK됐지만 slave timer-off compound는 `UNCERTAIN`이 됐다.

기존에 배포한 split continuation으로 slave role-only independent와 independent timer-off를 서로
다른 앱 shape로 한 번씩 보내 ACK/readback을 확인했다. 이어서 assist의 미시도 scalar와 detach의
나머지 exact restore를 완료했다.

| durable record | 결과 | record SHA-256 |
|---|---|---|
| `wm_split_000001f5…` | 2 action VERIFIED·ACK | `ce82a992e20b2100b0ae51262ac08c8ab439d4b2578cde0425380a8b950d149a` |
| `wm_assist_000001f5…` | VERIFIED | `cdb67f396c19e8d955a3aa7d6907bb00f24bac3522b0e9c2f93608258d64dcaf` |
| `wm_detach_000001f5…` | VERIFIED·exact restore | `2b8c642d538dd1326f7912537c2faa1b34b5ce20847cd5e2832e1bca76a6b09d` |

UTC span은 2026-09-22 13:33:09–13:35:51이다. 원 detach action 0과 assist action 1의
UNKNOWN forward 재전송은 0회다.

## 새 Pro Master/Async 적용

새 source는 준비 controls와 두 schedule image, TimerON, master와 sync 역할까지 action 0–7을
ACK·VERIFIED했다. 마지막 Async role action은 ACK가 없어 `UNKNOWN`으로 한 번만 보존했다.
master-cycle의 첫 action은 ACK됐고 둘째 action은 아직 미시도 `PREPARED`로 남아, 같은 child를
재개해 둘째 action만 보냈다. 두 master-cycle action 모두 ACK된 뒤 fresh pair가 상단
`master`, 하단 `async_slave`, power ON, TimerON, 두 image exact를 확인했고 source action 8을
`native_async_role_verified_by_master_cycle_delivery_unknown`으로 VERIFIED 처리했다.

- Pro source: `VERIFIED`, 9 action 모두 VERIFIED,
  record SHA-256 `ad151ec362daa5f6b77f9553e232769b695b9badcb60a72c53d6b260b7d63250`
- master-cycle: `VERIFIED`, 2 action 모두 ACK,
  record SHA-256 `ed52242a097891bafe54c66f10a1030afd1b1159eba5476542dbd9c5389ee8ed`
- UTC span: 2026-09-22 13:36:30–13:38:22

## MCP-70 Bar 적용

Bar는 2026-09-22 13:38:46 UTC에 schedule image physical write 1회로 `VERIFIED`됐다. action의
expected readback은 위 384-byte image와 동일하고 schedule ON·`independent`를 유지했다.

- record SHA-256: `8e0dad44df00c063fa380302ea02755a102856aa12e6150b0721e449dfbdd799`
- retained fresh state observed at 13:41:13 UTC: 31 entries, Feeding 6분 9개,
  invalid slot 0, schedule ON, role independent

## 독립 Pro raw 재확인

raw-anchor attempt `000432`는 2026-09-22 13:39:18–13:39:36 UTC의 등급 (a) raw pair이며
outcome은 `accepted`, action count는 explicit reply 1개와 report 1개다.

| 대상 | identity binding SHA-256 | raw frame SHA-256 | 현재 reported 상태 | image SHA-256 |
|---|---|---|---|---|
| 상단 | `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c` | `62845d1ccb18ce0140f84b41fd23444f4e68ea359ce0bb086282141a588b4704` | master, TimerON, Sine, Flow 35, F80, fault 0 | `118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9` |
| 하단 | `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea` | `b32e77bd1e3a4ba77711f758ce529e651b2fc672a1843a7a5e4e481ddac28014` | async_slave, TimerON, constant carrier, Flow 40, fault 0 | `aaa0f30824d4e2b15e3eeb9e948e665ba5b63562330e8fc0aa20a2a20af22b6f` |

장비 현지 시각 22:39의 21:00–24:00 구간과 정확히 일치했다. 양쪽 모두 active slot 31개,
invalid slot 0개다. 모든 local-control durable record의 최종 미완료 개수는 0개다.

## 이 실행으로 새로 확정된 사실

1. 세 장비가 Feeding 6분 아홉 구간을 포함한 새 31구간 image를 exact 수용했다.
2. 상단은 요청 파형과 Flow를, 하단은 같은 경계의 per-slot Flow constant carrier를 유지한 채
   Master/Async 결속과 TimerON을 유지했다.
3. 현재 21:00–24:00 구간에서 상단은 Sine35/F80, 하단은 feed가 아닌 constant carrier Flow40을
   raw로 보고했고, MCP retained 시간표의 같은 구간은 Pulse40/F80이다.
4. 기존·새 Async role delivery UNKNOWN은 재전송하지 않았고, 별도 ACK와 fresh readback으로
   terminal 처리했다. 미완료 journal은 0개다.

이 증거는 장치가 보고한 역할·시간표·활성 mode/Flow를 증명한다. GMP 두 대의 물리적인 180도
위상은 telemetry 필드가 없으므로 이번 실행으로 주장하지 않는다. 장기 recorder, raw anchor,
packet capture와 watchdog는 계속 실행한다.
