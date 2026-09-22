# run: 2026-09-22-flow-carrier-deploy-detach-block

## 목적과 승인 범위

`344605a`의 per-slot Flow carrier를 배포하고, 기존 Tidal 완화 source를 exact 해제한 뒤
Pulse master / constant-carrier Async slave 계획을 적용하려 했다. 사용자는 원격 실행을 승인했고
IoT 전원 차단 수단을 보유했다. 이번 기록은 **새 계획 적용 전 기존 source 해제에서 멈춘 실행**이다.

- 실행 commit: `344605a`
- 새 runtime image: `sha256:d11c69e45a433214bd31ddecae59e2a4d348242044593cbb321df36286ee0c2d`
- 새 schema SHA-256: `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27`
- 기존 runtime image: `sha256:a1733a465f4a509960745f621952f9fc70c848c6fd9ae65ad07dded16e5fa911`
- 기존 schema SHA-256: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- private 계획 artifact id: `pro-flow-carrier-20260922-01`
- 장비 identity binding SHA-256:
  - 상단: `92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`
  - 하단: `7b7be7835ebd264d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`

MCP-70 Bar, return pump, Aquarium, Dosing에는 명령을 보내지 않았다. 기존 full-state recorder,
raw anchor, packet capture, timer watchdog와 daily monitor는 중단하거나 재시작하지 않았다.

## 배포와 write-free 확인

새 image로 runtime을 교체한 뒤 config digest와 journal operation 파일 114개의 집합 digest가
기동 전후 동일함을 확인했다. startup·HA discovery에서 장비 operation은 생기지 않았다. 새 runtime의
preview는 기존 35-slot Pulse 계획으로 다음 이미지를 만들었다.

| 대상 | image SHA-256 | 계약 |
|---|---|---|
| 상단 | `73e0f45b5ce883f15dd89c14b5fe2d3bf8dce55a77bd47ab2285e7c60b7d1fec` | 요청 파형·상단 Flow 원형 |
| 하단 | `eb239c93241a175c188c7959955f6e08713caa340df1ea68d18f638a314cafdc` | 같은 경계·하단 Flow, non-feed constant carrier |

하단 image의 35개 Flow는 요청된 하단 표와 일치하고 상단 Flow를 복사하지 않는다. 이 시점에는
preview만 수행했으며 두 image 모두 장비에 보내지 않았다.

## 해제 시도

새 runtime에서 기존 source `kaga_pulse_to_tidal_20260922_02`를 detach하려 한 두 요청은
durable detach child 생성 전에 거부됐다. 원인은 source schema가 기존 `b6ac93d8...`인데 현재
runtime schema가 `8863840...`인 점이었다. child journal과 장비 write는 0이었다.

기존 image로 runtime만 되돌린 뒤 같은 source를 detach했다. 이때 처음으로 durable child가 먼저
생겼고 실제 전송이 시작됐다.

| record | UTC span | 결과 | journal SHA-256 |
|---|---|---|---|
| `wm_detach_5b478a...` | 11:56:13–11:56:19 | action 0 `UNCERTAIN`, 나머지 5 `PREPARED` | `7818a3fe3e33875dc0e9d13fadd386d812d88053221ce269f89d56754e8ca84f` |
| `wm_assist_5b478a...` | 11:57:10–11:58:16 | master role-only ACK, slave schedule-off `UNCERTAIN`, 나머지 3 `PREPARED` | `9aa04463bec8cdab521463ae79b8cb8d2753348197731c4ed01d1291209ad276` |

첫 detach의 하단 compound 해제 프레임은 ACK가 없었고 적용도 확인되지 않았다. 기존
`assist_detach`는 상단 `Linkage=independent` role-only 프레임을 ACK로 확인한 뒤 하단에
`schedule_enable=False`를 한 번 보냈으나, 그 프레임도 ACK가 없고 fresh readback이 달랐다.
두 프레임은 모두 `UNCERTAIN`으로 보존됐으며 재전송하지 않았다. 같은 assist를 다시 호출한 것은
fresh read만 수행했고 하단 명령을 재전송하지 않았다.

## 종료 상태와 보존 증거

raw anchor attempt `000410`은 실행 전 등급 (a) raw pair이고, attempt `000412`는 실행 후 등급
(a) raw pair다.

| UTC | 대상 | 역할 | power / timer | Auto | schedule SHA-256 | fault |
|---|---|---|---|---|---|---|
| 11:49:18–11:49:41 | 상단 | master | ON / ON | Tidal 40 | `4005971159c8e817a0e80c8c0f59b220b5d13151a51979958d5808ff17b97d74` | 0 |
| 11:49:18–11:49:41 | 하단 | async_slave | ON / ON | Tidal 40 | `b9735369a792bcc9e6187aadf5e647529afed4d2f6a34d2eaa1df29fc9bb7667` | 0 |
| 11:59:18–11:59:40 | 상단 | independent | ON / ON | Tidal 40 | 위와 동일 | 0 |
| 11:59:18–11:59:40 | 하단 | async_slave | ON / ON | Tidal 40 | 위와 동일 | 0 |

따라서 두 펌프는 계속 Tidal 계획으로 구동하며 새 Pulse/carrier image는 아직 적용되지 않았다.
중간 상태는 숨기지 않는다. 상단만 링크에서 해제됐고, 하단은 기존 Async 역할·TimerON을 유지한다.

## 이 실행으로 새로 확정된 사실

1. 새 carrier compiler는 상·하단의 서로 다른 per-slot Flow를 보존한 두 image를 만든다. 장비
   수용은 아직 검증하지 않았다.
2. 기존 attach source는 다른 global schema runtime에서 detach를 시작하지 못한다. 거부는 첫
   write 이전이며 기존 image로 복귀하면 durable detach가 시작된다.
3. 현재 하단은 active Async 상태에서 compound schedule-off write에 ACK하지 않았고, master를
   먼저 role-only independent로 만든 직후의 assist schedule-off도 ACK하지 않았다. 두 번째 것은
   11:59 raw에서도 미적용이었다.
4. 기존 assist는 불확실 프레임을 재전송하지 않아 single-write 계약을 지켰지만, 이 상태를
   종결할 다음 source-owned 단계가 없다.

다음 변경은 새 가설 게이트가 아니라 위 실제 raw가 드러낸 복구 공백을 닫는다. 하단에 앱과 같은
role-only `independent`를 별도 durable action으로 먼저 보내고, 과거 실기에서 slave write가
회복된 간격을 둔 뒤 independent용 schedule-off 프레임을 별도 action으로 보낸다. 기존
`UNCERTAIN` payload는 재전송하지 않고, 성공하면 원래 assist와 exact detach가 남은 원복을 계속한다.

