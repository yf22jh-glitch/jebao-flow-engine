# run: 2026-09-28-pro-ap-bridge-sine-linkpath-observation

## 질문과 조작

상단 Pro가 Master, 하단 Pro가 Async Slave인 수동 운전에서 사용자가 앱으로
상단 파형을 Pulse→Sine으로 바꿨다. 하단이 따라 변할 때 상단→하단 신호가
라우터의 AP 인터페이스 또는 IoT LAN 브리지에 보이는지 확인했다.
에이전트의 장비 명령·시간표·역할 write는 **0**이다. 기존 상태 기록기는 유지했다.

두 장비가 연결된 2.4 GHz AP의 기존 채널을 읽기만 확인하고,
`phy0-ap0`와 `br-lan.30`에서 두 장비의 MAC만 필터로 전 포트를 각각 수집했다.
AP 설정·채널·인터페이스 모드는 변경하지 않았다. 두 캡처 모두 Ethernet
link-type이며 **802.11 무선 원본이나 Bluetooth 캡처가 아니다.** 처음 두 수집
시도는 저장 옵션·호스트 볼륨 권한 오류로 파일을 만들지 못해 그 수집기만
중지했고, 수정해 시작한 세 번째 두 수집기가 정상 파일을 남겼다.

## 보존 증거

원본 PCAP는 **(a) preserved raw artifact**이며 주소·인증 프레임이 있을 수 있어
저장소에 넣지 않는다. private artifact id는
`pro-linkpath-ap-bridge-20260928-01`이다. 두 파일은 `0600`, 상위 디렉터리는
`0700`이며 수집 종료 후 파일을 다시 해시했다.

| 원본 | UTC span | 완전한 패킷 | 크기 | SHA-256 |
|---|---|---:|---:|---|
| AP Ethernet | 2026-09-28T01:46:13.953769Z–02:16:12.534015Z | 10,055 | 1,765,089 B | `c3cad71ff48e4b07cd247a8f321f4898a079eb49fa156f57f2e6b196f55fa807` |
| LAN bridge | 2026-09-28T01:46:31.676196Z–02:16:29.848585Z | 9,826 | 1,740,389 B | `e7e231970d551360d019352d0f8b314faa0173c32410d48408d980ef58a57d5f` |

물리 identity binding SHA-256은 상단
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
하단 `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`다.
이전 [Mode-only Pulse 캡처](2026-09-28-pro-manual-pulse-cloud-command-capture.md)의
두 대상 MAC을 원본에서 다시 식별하고, 두 MAC이 이번 AP의 연결 station임을
read-only로 확인해 동일 대상을 결속했다. 보존 원문을 기존 private
GAgent/P0 분석기로 재해독했고, 두 캡처 모두 TCP sequence gap 0,
protocol boundary diagnostic 0이다.

## 실제 명령·보고

| UTC | AP에서 확인된 사실 |
|---|---|
| 02:04:13.323580 | cloud→상단 MQTT PUBLISH 안의 GAgent 제어: 선택 id `[3]`, `Mode=sine`만. 현재 로컬 인코더와 byte-exact 일치 |
| 02:04:13.775963 | 상단 제어 ACK, sequence 11 |
| 02:04:14.185631 | 상단 자동 보고 최초 `Mode=sine` |
| 02:04:14.390495 | 하단 자동 보고 최초 `Mode=sine` |

하단은 02:04:13.366293Z까지 `Mode=pulse`를 보고했다.
전후 상태의 시계 필드를 제외한 70개 datapoint 비교에서 양쪽 모두
**Mode만 pulse→sine**으로 바뀌었다. `SwitchON=true`, `TimerON=false`,
상단 `master`·하단 `async_slave`, 양쪽 `Flow=40`·`Frequency=80`,
48슬롯 EE×9 image SHA
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`
는 유지됐다. 상태 보고는 물리 파형·위상을 직접 측정한 것이 아니다.

02:04:10–02:04:20Z의 AP·브리지 프레임은 각각 **71개이며**, 시각을 제외한
Ethernet/IP/TCP/UDP 프레임 식별자로 양방향 차집합 0이다. 완전한 두 수집
구간에서 상단 MAC→하단 MAC 또는 역방향의 직접 Ethernet 프레임은 둘 다 0개다.
하단으로 들어간 별도 P0 action `0x01` 제어도 두 캡처에서 0개다.
하단의 cloud 조회 action `0x02`는 제어로 세지 않았다.

## 이번에 닫힌 질문과 남은 질문

이번 조작에서 **상단에만 Mode 단일 필드 명령이 들어갔고, 하단은 약 1.07초
뒤 Sine을 보고했다.** AP의 일반 Ethernet 인터페이스는 기존 LAN 브리지보다
전달 경로를 더 보여주지 않았다. 따라서 `상단→하단 전달이 관측한
Ethernet/P0 제어 경로에 있었다`는 가설은 이번 구간에서 지지되지 않는다.

이것은 **신호가 없었다는 증거가 아니다.** AP 인터페이스는 raw 802.11
프레임을 제공하지 않고, Bluetooth·다른 RF·라우터에 드러나지 않는
무선 칩/드라이버 경로도 기록하지 않는다. 실제 Master→Slave 물리 매체와
메시지 내용은 `UNKNOWN`이며, 별도 무선 수집 지점 또는 장비 측 원본이
있어야 닫힌다. 두 기기의 실제 반대 위상도 이번 패킷·상태만으로 판정하지 않는다.
