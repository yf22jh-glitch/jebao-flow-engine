# run: 2026-09-25-pro-linked-flow-cli-44

## 질문과 범위

이미 실측 성공한 상단 Master 해제 → 하단 `Flow` 변경 → 상단 재결속을
터미널 명령 한 번으로 묶어도, 하단 Async 역할과 두 시간표를 유지하며
`45→44`를 완료하는가. 현장 운영자가 승인하고 감시했다. 결속을 유지한
직접 쓰기 방법의 재시험이 아니라, 검증된 세 단계의 운용 시험이다.

실행 시 source HEAD `426aafa`에 미커밋된 operator-only CLI를 stdin으로
주입했다. CLI 파일 SHA-256은
`5e6f489ca85f83007887eb11a86caf78d7906821e33f13001592346099a96761`이다.
동결된 `devices/*` 하네스는 사용하거나 수정하지 않았다. UTC 구간은
2026-09-24 17:21:24–17:21:55이다. 상단/하단 physical identity binding
SHA-256은 각각
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`이다.

## 실제 실행과 증거

실행 전 읽기 전용 preview는 하단 `Flow45`와 실행 권한·출력 범위를 확인하고
장비 송신 0건으로 끝났다. 실제 명령은 `--flow 44 --write-enabled`와 고유
request-id를 사용했다. 각각의 child는 기존 `LocalControlCoordinator.execute`를
통해 최초 송신 전 durable journal 생성, identity·권한 검사, 단일 송신과
ACK/readback 검증을 수행했다. 세 child는 다음과 같다.

| UTC child 생성 | 장비 명령 | 결과 | private journal SHA-256 |
|---|---|---|---|
| 17:21:24 | 상단 `{Linkage:independent}` | VERIFIED, write 1 | `5a5a90497290fbcfd99fa2dc698ce4ca71c491e0a7b46ebefd753e40fb5492a3` |
| 17:21:39 | 하단 `{Flow:44}` | VERIFIED, write 1 | `d43215abb5c95efa1d4b00868460e74f9033dc0b76a272a80536280d78ae2146` |
| 17:21:53 | 상단 `{Linkage:master}` | VERIFIED, write 1 | `12b32f8aeea9817e209c0d014eb61692e5bc1ee91eee5600e7ee7cc4e651471c` |

private `local-control` journal에서 여섯 값(세 child의 생성·완료 UTC,
VERIFIED phase, action VERIFIED, SHA-256)을 재조회했다. request-id 계열
`pro_linked_flow_cli_44_20260925_01_*`은 등급 (b) 구조화 durable 증거이며,
원 주소·ID·비밀값·raw frame은 저장소에 넣지 않았다.

단계 사이 fresh pair와 종료 fresh pair에서 상단은 Master로 재결속되고
하단은 Async·`Flow44`·전원 ON·TimerOFF로 보고됐다. 상단도 전원 ON·TimerON,
양쪽 fault 0이었다. 두 432B 시간표 이미지 digest는 시작/종료 동일했다.
이 fresh 조회 요약은 별도 raw artifact로 보존한 것이 아니므로 그 자체의
증거 등급은 (c)이고, 물리 유량·위상까지 증명하지 않는다.

## 이 실행으로 새로 확정된 사실

검증된 세 명령을 한 번의 터미널 실행으로 순서대로 수행하면, 이 Pro pair에서
하단 Flow `45→44`가 보고값으로 확인되고 상단 Master·하단 Async 역할 및
두 시간표 이미지를 유지했다. 세 개는 각각 별도 durable operation이며,
한 개의 원자적 장비 명령은 아니다. 중간 실패 시 CLI는 남은 child를 보내지
않고 멈춘다. 결속 중 하단에 직접 쓰는 방법의 성립 여부는 이 실행으로
확정하지 않는다.
