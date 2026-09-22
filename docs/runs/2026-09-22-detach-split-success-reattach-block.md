# run: 2026-09-22-detach-split-success-reattach-block

## 목적과 승인 범위

선행 무전송 실행에서 보존된 split operation을 이어서 기존 Tidal source를 exact detach하고,
새 Pulse master / constant-carrier Async slave 계획을 적용하려 했다. 사용자는 원격 실행을 승인했고
IoT 전원 차단 수단을 보유했다. 이번 실행은 **exact detach는 완료했고 새 attach는 첫 write 전에
멈췄다.**

- 실행 commit: `6d9fd0a`
- detach hotfix image: `sha256:cb6ab62c3d8f01ffee642f7e04ad0b4f23f4da194ae5652140f9e608205f33bb`
- detach schema SHA-256: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- carrier runtime image: `sha256:0a3c413f0af13e1389a1cb6e665d0a1e5f71dc6139cab1a13c8a96b9747f97ce`
- carrier schema SHA-256: `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27`
- 새 계획 artifact id: `pro-flow-carrier-20260922-01`

MCP-70 Bar, return pump, Aquarium, Dosing에는 명령을 보내지 않았다. 기존 full-state recorder,
raw anchor, packet capture, timer watchdog와 daily monitor는 계속 실행했다.

## split와 exact detach 결과

| artifact id | UTC 종료 | 결과 | artifact SHA-256 |
|---|---|---|---|
| `wm_split_5b478a959103c374e49578f09915103ba4ca296a6cf3a3246edb4db614a53c47` | 12:59:03 | 두 action VERIFIED·ACK | `49151cd1933025ce279df489bf83341910727127368da55b06dc3de6da777663` |
| `wm_assist_5b478a959103c374e49578f09915103ba4ca296a6cf3a3246edb4db614a53c47` | 12:59:15 | VERIFIED | `43961289026abd2c469bc4605813be5b9b6c6ea9e2cc4e9ef5aebb1ae79b9483` |
| `wm_detach_5b478a959103c374e49578f09915103ba4ca296a6cf3a3246edb4db614a53c47` | 12:59:30 | VERIFIED·exact restore | `f0a33f330245905abaa0a8633fded48a721aca013a30cf44d92fc68510910f6d` |

split는 하단 role-only independent와 independent timer-off를 각각 한 번 보내 ACK/readback을
확인했다. 기존 detach action 0과 assist action 1의 UNKNOWN forward는 다시 보내지 않았다.
assist action 1과 detach action 0은 새 상태 관측으로 VERIFIED가 됐고, 남은 scalar/image 원복만
실행했다.

최종 등급 (b) durable final은 양쪽 8 controls와 432-byte image가 attach 전 원본과 같다고
기록했다.

| 대상 | 역할 / power / timer | schedule SHA-256 | final capture SHA-256 |
|---|---|---|---|
| 상단 | independent / ON / ON | `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426` | `1ccf579e690c84a2910cfc5a0e59de25bb300f088d67ae5cddc93a99128e6a5d` |
| 하단 | independent / ON / ON | `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99` | `911cab6cd024e4246c3922adf5461139f910867f615bfb3539f1b0821980841b` |

두 final 모두 manual Mode constant이고 fault를 새로 주장하지 않는다. 이는 daemon의 보존된
structured claim이며 물리 위상 측정은 아니다.

## 새 carrier attach의 write 전 차단

carrier runtime으로 교체한 기동 전후 journal 파일 집합 digest는 동일했다. 이어서
`kaga_flow_carrier_async_20260922_01` attach를 요청했지만 durable operation 파일이 생성되지
않았고 장비 write는 0회였다. 계획 검증과 9개 기능의 두 identity authorization은 모두 통과했다.

차단 원인은 `_require_available`이 이미 VERIFIED인 split history를 다시 읽을 때, split 생성 당시의
현재 child가 아직 `RECOVERY_REQUIRED`여야 한다고 요구한 점이다. 실제 현재 child·assist·split은
모두 VERIFIED라서 과거 기록의 context validation이 실패했다. 이는 장비 상태나 펌웨어 가설이
아니라 terminal history 검증이 이후 정상 상태 전이를 허용하지 않은 코드 내부 결함이다.

## 이 실행으로 새로 확정된 사실

1. 분리된 두 앱 shape는 실제 장비에서 ACK됐고, 기존 UNKNOWN forward 재전송 없이 원 source의
   8 controls와 두 432-byte image를 exact restore할 수 있었다.
2. 복구 후 두 Pro는 independent / power ON / TimerON으로 원본 schedule을 유지한다.
3. VERIFIED split context가 현재 VERIFIED child를 허용하지 않아 새 attach를 first-write 이전에
   막는다. terminal history validation과 split 생성 admission을 분리해야 한다.

다음 변경은 새 split 생성에는 기존 stalled child 조건을 그대로 유지하고, 이미 존재하는 split
record를 검증할 때는 child가 원 stalled shape이거나 정식 VERIFIED detach인 두 정상 상태를 모두
허용한다. identity·single-write·journal·rollback·payload 재구성 조건은 유지한다.
