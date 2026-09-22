# run: 2026-09-22-detach-split-prewrite-read-shape

## 목적과 승인 범위

기존 Tidal source `kaga_pulse_to_tidal_20260922_02`의 두 UNKNOWN payload를 재전송하지 않고,
별도 role-only와 independent timer-off 프레임으로 exact detach를 이어가려 했다. 사용자는 원격
실행을 승인했고 IoT 전원 차단 수단을 보유했다. 이번 실행은 **두 장비 fresh read 뒤 첫 write
이전에 멈췄다.**

- 실행 commit: `4de79fd`
- runtime image: `sha256:79555406ab8cfce009de698a49f9768163907404dc64627d2244fb6335ab2ae5`
- schema SHA-256: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- operation artifact id: `wm_split_5b478a959103c374e49578f09915103ba4ca296a6cf3a3246edb4db614a53c47`
- operation artifact SHA-256: `1617708833a680482ed251325029838566e7c63073d4e9f5cd933413bc0511af`

MCP-70 Bar, return pump, Aquarium, Dosing에는 명령을 보내지 않았다. 기존 full-state recorder,
raw anchor, packet capture, timer watchdog와 daily monitor는 중단하거나 재시작하지 않았다.

## 보존 증거

등급 (b) durable journal에는 두 fresh capture와 전송 전 action 두 개가 보존돼 있다.

| UTC span | 대상 | identity binding SHA-256 | 수신 action | status SHA-256 |
|---|---|---|---|---|
| 12:52:21.857–12:52:23.053 | 상단 | `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c` | reply `3` | `afc6cabc16eaf280c1ef96225717c95e5a0e2ca2a5fb0cdbfc37e23501ac92fc` |
| 12:52:23.074–12:52:23.664 | 하단 | `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea` | reply `3` | `885be3632c8549f0f18963f4e3cea87c8f60daa0bf422459883c24e9c5980c80` |

operation은 revision 0 / `PREPARED`이고, `action_00 linkage_role`과
`action_01 schedule_enable`도 모두 `PREPARED`다. ACK·FORWARD_ATTEMPTING action은 0개이며
장비 write는 0회다. 기존 detach·assist의 UNKNOWN action도 재전송하지 않았다.

## 중단 원인

하단 read는 `accept_reports=True`였지만 장비가 report `4` 대신 정상 explicit reply `3`을
반환했다. durable capture의 status digest는 split context의 status digest와 일치한다. 그런데
split context 재구성 검사가 하단 action을 `4`로만 고정해 정상 reply를 거부했고, 첫 send 전에
`fresh pair capture` 오류로 끝났다.

이는 펌웨어 가설이나 상태값 불일치가 아니라 코드 내부의 read-frame shape 과제약이다. 기존
session 계약은 report를 허용할 때 reply도 정상으로 받으며, 실제 장비가 그 reply를 보존 raw로
제공했다.

## 이 실행으로 새로 확정된 사실

1. 구 schema hotfix는 기존 schema와 정확히 일치하고 split API를 제공하며, 기동 전후 journal
   파일 집합 digest가 같아 startup write를 만들지 않았다.
2. 이번 장비는 하단 fresh read에 정상 reply `3`을 반환했다. report 허용은 report 강제가 아니다.
3. split action은 둘 다 첫 write 이전 `PREPARED`로 남았으므로 같은 durable operation을 수정된
   validator로 이어갈 수 있고, 시도된 forward를 재전송하는 문제는 없다.

다음 변경은 하단 fresh capture의 action을 `{3, 4}`로 재구성 허용한다. status digest 결속과
상단 strict reply, identity·출력 범위·single-write·durable journal·rollback 조건은 유지한다.
