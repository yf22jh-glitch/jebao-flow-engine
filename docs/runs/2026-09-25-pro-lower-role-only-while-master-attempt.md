# run: 2026-09-25-pro-lower-role-only-while-master-attempt

## 질문과 범위

상단 Master를 유지한 채 하단 Async에 `Linkage=independent` 단일 명령을
보내면, 하단만 분리해서 Flow를 변경할 수 있는지 확인했다. 현장 운영자가
가능한 방법의 시험을 승인했다. 실행 source는 `555a3ed`이며 다른 필드·
시간표·상단에는 쓰지 않았다.

UTC 구간: 2026-09-24 17:09:12–17:10:02. 상단과 하단 physical identity
binding SHA-256은 각각
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
원 주소·ID·비밀값·raw frame은 저장소에 넣지 않았다.

## 실행과 결과

fresh preflight에서 상단은 Master·TimerON·PowerON, 하단은
Async·TimerOFF·PowerON·Flow45였다. 양쪽 fault 보고는 0이고 상단/하단
432B 시간표 SHA-256은 각각
`118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9`,
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`.
두 장비를 함께 잠그고 현행 identity 제어 권한을 확인했다. 하단 초기 raw에서
원 Async rollback을 준비하고 durable journal을 첫 send 전에 만들었다.

`{Linkage:independent}` forward payload SHA-256은
`fe38f339faa2ed5e82e282107c2808ba029e8d82a8354201aa7627aab03f7239`.
애플리케이션 send는 한 번만 시도했으나 5초 안에 제어 ACK가 없어
`UNCERTAIN / RECOVERY_REQUIRED`가 됐다. 동일 변경의 애플리케이션
재송신은 없었다. 새 연결의 하단 보고는 여전히 Async·Flow45·TimerOFF·
PowerON, 상단은 Master·원 시간표이며 양쪽 fault는 0이었다. 복구는
또 다른 fresh 하단 보고가 초기 Async와 일치함을 확인하고 **송신 0건**으로
`ROLLED_BACK` 종결했다. 최종 journal digest는
`e5c5cd1ac59b0f6cd4e58db3037569195f6757c6669a0a6bcf99592530dace95`.
private `local-control` journal의 위 request-id와 digest는 증거 등급 (b)이다.

## LAN 원본 패킷 재대조

private owner-only PCAP `pro-router-netcap-20260924-01`(파일 0600,
디렉터리 0700; 반출·커밋 없음)에서 17:09:12.935450Z의
`SERIAL_CONTROL_REQUEST`(0x0093)를 확인했다. 전송 헤더 네 바이트 뒤
payload는 위 forward SHA-256과 일치한다. 첫 PCAP record offset은
`33284690`, 그 record SHA-256은
`94ffeab2b8dc7b634b2752fbb83b72c879c7e5933b681681efda50e8710c1f5f`.
17:09:13.158192Z에 같은 TCP sequence 번호·동일 payload의 재전송이
한 번 보이지만 이는 애플리케이션의 두 번째 send가 아니다. 하단의 TCP ACK는
전송 전 sequence `3251257628`에서 전송 뒤 `3251258101`로 전진해
**473바이트 TCP segment 수신**을 확인한다. 그러나 그 뒤에도 제어 응답
0x0094는 없고 0x0091 보고의 Linkage는 3(Async), Flow는 45였다.
첫 후속 보고(17:09:13.951824Z)의 PCAP record offset은 `33285986`,
SHA-256은 `fde53dad9606a4c30d2e30f3aeb88245ef5764ea6589e000dcea79234804accb`.
이 LAN 패킷은 증거 등급 (a)이다. TCP 수신이 곧 장치 애플리케이션의
명령 수용을 뜻하지는 않는다.

## 이 실행으로 새로 확정된 사실

상단 Master를 둔 상태에서 하단에 `Linkage=independent`만 보내는 방법도
이번에는 ACK를 받지 못하고 하단이 Async·Flow45로 남았다. 두 하단 직결
방법(`Flow` 단독 및 `Linkage+Flow` 복합)과 함께 보아, 현재 LAN 제어에서
상단 결속을 유지한 채 하단 값을 바꾸는 경로는 **확인되지 않았다**.
패킷 미전달이나 우리 필드 비트 조립 문제로만 설명할 수 없지만, 장치
내부의 거부 조건은 아직 확정할 수 없다. 이미 성공한 상단 해제→하단
Flow 변경→상단 재결속은 별도 경로로 유지한다.
