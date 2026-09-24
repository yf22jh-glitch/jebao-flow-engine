# run: 2026-09-25-pro-linked-flow-cli-45-restore

## 질문과 범위

직전 operator-only CLI 시험으로 하단에 보고된 `Flow44`를 동일한 검증된
세 단계로 `45`로 되돌리고, 상단 Master·하단 Async 및 시간표 이미지가
유지되는지 확인했다. 현장 운영자의 기존 Flow45 목표와 감시 아래 실행했다.

실행 시 source HEAD `426aafa` 및 stdin 주입 CLI 파일 SHA-256
`5e6f489ca85f83007887eb11a86caf78d7906821e33f13001592346099a96761`.
동결된 `devices/*` 하네스는 사용·수정하지 않았다. UTC 구간은
2026-09-24 17:22:38–17:23:06이다. 상단/하단 physical identity binding
SHA-256은 각각
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`이다.

## 실제 실행과 증거

`--flow 45 --write-enabled`와 새 고유 request-id를 사용했다. 각 child는
identity·권한·설정 범위·현재 상태를 확인하고, 첫 송신 전 durable journal을
만들어 한 번씩만 보냈다. private `local-control` journal을 실행 후 다시 읽어
각 생성·완료 시각과 terminal VERIFIED/action VERIFIED 및 digest를 확인했다.

| UTC child 생성 | 장비 명령 | 결과 | private journal SHA-256 |
|---|---|---|---|
| 17:22:38 | 상단 `{Linkage:independent}` | VERIFIED, write 1 | `3d5c0df4c911943e6a11708b190a0371fd77923bca531c4a6f8b1ae665dcc834` |
| 17:22:51 | 하단 `{Flow:45}` | VERIFIED, write 1 | `a998a6ce84fd3081533fa40553a5cd310c40137dbdd318ebf9fe451655b14d1a` |
| 17:23:04 | 상단 `{Linkage:master}` | VERIFIED, write 1 | `42da41d3a8c172ffd9ab4904a073f47878ff9363f9c34ef8ff82d82fa8c5e603` |

private request-id 계열 `pro_linked_flow_cli_45_20260925_01_*`과 위 digest는
등급 (b) 구조화 durable 증거다. 원 주소·ID·비밀값·raw frame은 저장소에
기록하지 않았다.

단계별 fresh pair와 종료 fresh pair에서 상단 Master·하단 Async,
하단 `Flow45`, 양쪽 전원 ON, 상단 TimerON·하단 TimerOFF, fault 0,
두 432B 시간표 digest 전후 동일이 확인됐다. 이어서 `--flow 45`만 준
읽기 전용 최종 조회도 `already_at_target / device_writes=0`으로 끝났다.
fresh 조회 요약은 별도 raw artifact 보존이 아니므로 그 자체는 등급 (c)이며,
물리 유량·위상 증명은 아니다.

## 이 실행으로 새로 확정된 사실

이번 pair에서 같은 터미널 절차로 Flow `44→45`를 다시 적용했고,
Master/Async 결속과 시간표를 유지한 채 최종 보고값 45를 확인했다.
즉 앞선 변경이 일방향 우연이 아니라 적어도 이 인접한 두 값에서 왕복했다.
여전히 세 child는 원자적 한 operation이 아니며, 결속 중 단일 프레임으로
하단 Flow를 바꾸는 경로의 증거는 아니다.
