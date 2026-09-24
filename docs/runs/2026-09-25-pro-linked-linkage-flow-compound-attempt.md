# run: 2026-09-25-pro-linked-linkage-flow-compound-attempt

## 질문과 범위

상단 Pro가 Master이고 하단 Pro가 Async로 결속된 상태에서, 하단에
`{Linkage:async_slave, Flow:44}`를 **한 제어 프레임**으로 보내면 하단의 보고
`Flow=45`가 직접 바뀌는지 시험했다. 현장 운영자가 이 단회 시험을 승인했다.
실행 source는 `62b8548`이며, 앱·시간표·상단 장비에 쓰지 않았다.

UTC 구간: 2026-09-24 16:58:20–16:59:21. 상단과 하단의 physical identity
binding SHA-256은 각각
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
원 주소·ID·비밀값·raw frame은 저장소에 넣지 않았다.

## 실행과 보존 증거

첫 준비 시도는 다른 read/write 작업과의 물리 잠금 충돌로 journal 전에 멈췄다.
두 번째 준비 시도는 시험 스크립트가 expected readback의 JSON **바이트**를
dict와 비교한 오류로 journal 전에 멈췄다. 둘 다 장비 송신 0건이었다.
비교를 바로잡은 뒤 같은 승인 범위 안에서 한 번만 송신했다.

상단·하단 identity와 제어 권한을 재확인하고 두 장비를 함께 잠갔다. 직전
fresh 상태는 상단 `master / SwitchON=true / TimerON=true / Flow40`, 하단
`async_slave / SwitchON=true / TimerON=false / Flow45`였고, fault 보고는
양쪽 모두 없었다. 상단 432B 시간표 SHA-256은
`118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9`,
하단 all-EE 시간표는
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`.
설정 출력 범위 안의 `44`와 현행 `async_slave`를 각각 기존 scalar writer로
검증한 뒤 두 필드만 선택한 compound payload를 만들었다.

단일 action의 forward SHA-256은
`0c339bbc68a7926707c634cbcfc7828e9b35832878fc9db0c69c00c7451d02a9`,
초기 raw에서 만든 rollback SHA-256은
`07c10f6facf009492f1eef66216b89d7eefe714526f7d4271b96d62221dc02ea`.
`LocalControlCoordinator`가 durable journal을 먼저 만든 뒤 send를 **한 번**
시도했다. 5초 내 ACK가 없어 `UNCERTAIN / RECOVERY_REQUIRED`가 됐으며,
같은 forward를 재전송하지 않았다. 따라서 장비가 명령을 수용했는지는
확정하지 않는다.

새 연결로 읽은 하단 action 0x04 보고는 `async_slave / Flow45 /
TimerON=false / SwitchON=true`였고, 상단 action 0x03은 Master와 원 시간표를
유지했다. 양쪽 fault 보고는 없고 두 시간표 digest도 직전과 같았다.
기존 `recover`가 또 다른 fresh 하단 보고에서 초기 `Flow45`·`async_slave`를
확인하고 **추가 송신 0건**으로 `ROLLED_BACK` 종결했다. 최종 durable journal
digest는 `16c554f0c53e143cf710a2b20341924df229d5401782d62889da8adc57c178cb`.
보존 증거는 private `local-control` journal의 위 request-id와 digest이며
**등급 (b)**이다. 본 문서의 fresh 상태 요약은 그 자체로 raw 보존을 뜻하지 않는다.

## 이 실행으로 새로 확정된 사실

결속 하단에서 `Linkage=async_slave`와 `Flow=44`를 함께 선택해도 이번
단회 송신은 ACK를 받지 못했고, 후속 fresh 보고의 Flow는 45로 유지됐다.
따라서 **이 compound 방식으로 결속 중 Flow를 직접 바꾸는 방법은 확인되지
않았다.** 이미 확인된 상단 Master 해제→하단 Flow 변경→상단 재결속 경로와
구분한다. 물리 유량 변화 여부와 장비 내부의 거부 이유는 미확정이다.

## 추가 증거: LAN 패킷과 지속 보고

같은 실행의 private owner-only PCAP `pro-router-netcap-20260924-01`에서
원본 패킷을 다시 읽었다(파일 0600, 디렉터리 0700; 반출·커밋 없음).
16:58:20.252703Z의 TCP `SERIAL_CONTROL_REQUEST`(0x0093) 한 건은
Gizwits 전송 헤더 네 바이트 뒤가 위 forward payload SHA-256과 **축자 일치**했다.
해당 PCAP record offset은 `32646032`, 원 record SHA-256은
`ba7ce70697d1a2d2e0c840963facf31391635aa563cbf29a4647fe6d5abc807d`.
이 패킷은 서버에서 장비 방향으로 실제 전송된 바이트의 **등급 (a)** 근거다.

같은 연결에서 16:58:21.233518–16:58:25.432169Z에 받은 다섯
`SERIAL_TRANSMIT_RESPONSE`(0x0091) action 0x04의 Linkage는 모두 3(Async),
Flow는 모두 45였다. 첫 후속 보고의 PCAP record offset은 `32646715`,
원 record SHA-256은
`6dd7da75e2d64806bdbe4b2b168c8bb6c94edf77d29383d2427c13aecb06da42`.
해당 구간에 `SERIAL_CONTROL_RESPONSE`(0x0094)는 0건이었다. 이는 장비가
보고한 값이 바뀌지 않았다는 **등급 (a)** 근거이며, 장비 내부가 요청을
왜 수용하지 않았는지까지 증명하지는 않는다.
