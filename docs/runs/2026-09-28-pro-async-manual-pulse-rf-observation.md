# run: 2026-09-28-pro-async-manual-pulse-rf-observation

## 질문과 조작

상단 Pro Master의 수동 파형을 바꿀 때 하단 Pro Async Slave가 어떤 입력을
받고 바뀌는지, 기존 IP 캡처 밖의 무선 프레임이 보이는지 확인했다.
사용자가 휴대폰 Bluetooth를 끄고 제바오 앱으로 **상단만** Sine→Pulse로
변경했다. Bluetooth OFF와 앱 조작은 사용자 진술인 (c) 관찰이며,
앱 명령의 실제 수신 대상은 아래 (a) 원본 캡처로 별도 확인했다.
에이전트의 장비 제어·시간표·역할 write는 **0**이다.

기존 full-state·raw-anchor·IP PCAP 기록기를 유지하면서, 두 Pro가 접속한
라우터의 기존 2.4 GHz 채널에서 802.11 radiotap 모니터 두 종류와 IoT LAN
bridge 전 포트 PCAP를 수집했다. AP 설정·채널은 변경하지 않았다.
추가 `otherbss` 모니터는 **전환 후**에만 가동했으므로 전환 순간의 부재
판정에는 사용하지 않는다. 수집기는 지정된 15분/90초 timeout에 종료됐다.

## 보존 증거

PCAP 네 파일은 (a) preserved raw artifact다. private artifact id는
`pro-linkpath-rf-20260928-02`이며, 파일·상위 디렉터리 권한은 각각
`0600`·`0700`이다. 주소·인증 자료가 포함될 수 있어 저장소에는 넣지 않는다.
아래 SHA-256은 수집 종료 후 파일 전체를 다시 계산한 값이다.

| 원본 | UTC span | 패킷 | 크기 | SHA-256 |
|---|---|---:|---:|---|
| 802.11 radiotap, 두 Pro·action 전체 프레임 | 2026-09-28T03:20:14.910554Z–03:35:13.931878Z | 11,247 | 1,211,729 B | `a018d4a11deec1ba0983f5101d0ac246f0e0b380c9ac3d5eaa68c175f8b68943` |
| 802.11 radiotap, 전 data/action 128 B header | 2026-09-28T03:20:14.902744Z–03:35:14.315854Z | 77,003 | 8,330,956 B | `1580e6bf299c63d0a43db1cd427894f3ebf77c9acf68038ec1c51db62ae84ecf` |
| IoT LAN bridge, 두 Pro 전 포트·broadcast/multicast | 2026-09-28T03:20:23.717824Z–03:35:23.111526Z | 5,467 | 982,822 B | `3233185154850c0f8d174e5c8a57eaacdd13ffcd8078d4708a991493f01d93fc` |
| 802.11 `otherbss` 128 B header, 전환 후 보조 대조 | 2026-09-28T03:30:47.459669Z–03:32:16.836215Z | 7,700 | 833,219 B | `696d70e3b8f7a3affe4c28999a08e5a77babbeaf1159c548db0967cb37fabb9d` |

상단 identity binding SHA-256은
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
하단은 `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`다.
이전 [Sine 전환](2026-09-28-pro-ap-bridge-sine-linkpath-observation.md)과
동일 물리 대상이다. bridge 원문은 GAgent/P0 분석기로 재해독했으며
완전한 5,467패킷에서 TCP sequence gap 0, protocol boundary diagnostic 0이다.

## 실제 명령과 상태 보고

| UTC | 보존 원본의 사실 |
|---|---|
| 03:27:23.477368 | cloud→상단 MQTT/GAgent 제어 action `0x01`, 선택 datapoint id `[3]` 하나, `Mode=pulse`. 현재 로컬 인코더와 byte-exact 일치 |
| 03:27:23.671 | 하단의 직전 자동 보고는 아직 `Mode=sine` |
| 03:27:24.282634 | 상단의 최초 `Mode=pulse` 보고 |
| 03:27:24.725088 | 하단의 최초 `Mode=pulse` 보고. 상단 보고보다 약 0.44초 뒤, 상단 제어보다 약 1.25초 뒤 |
| 03:27:25.199691 | 상단 제어 ACK, sequence 13 |

하단으로 들어간 별도 P0 action `0x01`은 전체 bridge 캡처에 **0개**다.
상단 제어 시각부터 하단의 첫 Pulse 보고까지, 하단으로 향한 IP 패킷은
payload 0바이트 TCP ACK 한 건뿐이었다. 이 구간의 broadcast는 주기적으로
반복되는 비-Pro 발신 UDP이며 Mode 제어 payload가 아니었다.
상·하단 모두 시계 필드를 제외한 70개 datapoint 중 `Mode`만
Sine→Pulse로 변했다. `SwitchON=true`, `TimerON=false`, 상단 `master`·
하단 `async_slave`, 양쪽 `Flow=40`·`Frequency=80`, 48슬롯 EE×9 image SHA
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`는
유지됐다. 이 값은 **장비 보고**이며 실제 모터 파형·위상을 직접 계측한 것은 아니다.

03:27:22–03:27:26Z의 802.11 전 data/action header 355개에서는
상단↔하단 직접 주소 프레임 0개, action 프레임 0개였다. 다만 이
모니터는 같은 순간 bridge가 수신한 펌프의 일반 uplink data도 누락했다.
355개 중 341개가 AP로 향한 Null-data, 14개가 AP에서 내려온 암호화
QoS data였다. 따라서 이 모니터의 `0개`는 **직접 무선 신호 부재의
증거가 아니다**. 보조 `otherbss` 역시 전환 후 구간에 한정된다.

## 이번에 닫힌 질문과 남은 질문

서로 반대 방향의 [앞선 Sine 전환](2026-09-28-pro-ap-bridge-sine-linkpath-observation.md)과
이번 Pulse 전환에서, 앱 명령은 상단에만 들어갔고 하단 Mode 보고가
약 1초 뒤 같은 방향으로 따라왔다. **하단에 별도 cloud/LAN P0 제어를
보내서 바꾼 경로는 이번 두 사건에서 관측되지 않았다.** 휴대폰 Bluetooth
OFF 진술은 휴대폰→하단 Bluetooth 제어 가설도 약화한다.

상단과 하단 사이의 실제 전달 매체·프레임은 여전히 `UNKNOWN`이다.
현재 기록은 IP bridge와 AP 채널 802.11의 불완전한 모니터만 포함한다.
펌프 간 Bluetooth/BLE, 다른 무선 채널, 라우터 모니터가 누락한 프레임,
장비 내부 신호는 원본 패킷으로 확인하지 못했다. 특정 RF 기술을
확정하거나 `모든 신호를 로깅했다`고 주장하지 않는다. 이를 닫으려면
양쪽 펌프 근처의 독립 무선 수집 지점 또는 제조사/펌웨어의 link-layer
증거가 필요하다. 장비가 보고한 Mode 일치도 실제 anti-phase의 증거는 아니다.
