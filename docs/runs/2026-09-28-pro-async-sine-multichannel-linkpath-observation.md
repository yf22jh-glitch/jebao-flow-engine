# run: 2026-09-28-pro-async-sine-multichannel-linkpath-observation

## 질문과 조작

상단 Pro Master의 수동 파형 변경이 하단 Pro Async Slave에 전달될 때,
어떤 명령과 무선 프레임이 실제로 보이는가? 사용자가 상단 파형을
Pulse→Sine으로 변경했다. 에이전트의 장비 설정·역할·시간표 write는 0이다.
기존 읽기 전용 기록기와 별도로, IoT LAN bridge 전 포트, AP Ethernet,
라우터 802.11 monitor, 독립 802.11 monitor, 호스트 Bluetooth HCI를
동시에 수집했다. Wi-Fi AP 설정과 장비 설정은 바꾸지 않았다.

## 보존 증거

private artifact id는 `pro-linkpath-multichannel-20260928-01`이다. 원본은
사설 주소와 인증 자료를 포함할 수 있으므로 저장소에 넣지 않는다.
상단 identity binding SHA-256은
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
하단은
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`다.
앞선 [Pulse 전환](2026-09-28-pro-async-manual-pulse-rf-observation.md)과
동일한 물리 대상이다. 파일은 `0600`, 상위 private 디렉터리는 `0700`이다.
아래 모든 UTC span의 날짜는 2026-09-28이다.

| 수집 원본 | UTC span | 완전한 패킷 | 크기 | SHA-256 |
|---|---|---:|---:|---|
| 독립 802.11 radiotap | 03:49:36.665119–03:58:50.601289 | 109,287 | 11,141,422 B | `abc0b305d7cb9ca4b782f22fb1a896ba39cad81e527fb343b4d4c358ad772419` |
| Bluetooth 호스트 HCI | 03:49:45.864859–03:58:51.368030 | 2,751 | 169,495 B | `e21fb436dc9f3e28faa171f04dc03aea7987d6d7499660b1ce2202c80bf88fdc` |
| IoT LAN bridge | 03:50:00.474166–03:58:50.381914 | 3,191 | 577,536 B | `e933094b981731aa9abda8443cf5b25d2a400f822877686fd8c7f8aeb8ac8938` |
| 라우터 802.11 radiotap | 03:50:09.885877–03:58:50.696585 | 52,500 | 5,820,416 B | `2da792856faf54cacb371a05e77e27062dfc7518541747e00b983967daf3e058` |
| AP Ethernet | 03:50:20.440927–03:58:46.183127 | 3,173 | 561,152 B | `2c9022fdca4a5554c510e02dc8826bdf17cccc9921afd15810970f7c036fd634` |

세 라우터 측 PCAP는 수집 종료 때 마지막 패킷이 잘려 있었다. 원본을
그대로 보존하고, 완전한 패킷만 추출한 별도 분석 사본을 만들었다.
각 사본은 `capinfos` 오류 0으로 읽혔고, 패킷 수는 위 표와 같다.
사본 SHA-256은 bridge `9b6bd23f8db8b06e5ae3d6b7d76cfe4080cf341443da0608379d9e9f7eb435ad`,
라우터 radiotap `b4cd1da3ca0c430bc26818e76ba9742bc127f30cc2ebf5d9f346c1002af9b799`,
AP Ethernet `8c0ee334a00b19757e3a1169525e26598b1611c7d58c829525a9b7bfca4ddd66`이다.
원본과 사본의 전환 시점은 종료 시점보다 3분 이상 앞서며, 분석기는 bridge
사본의 3,191개 완전한 패킷에서 TCP sequence gap 0, protocol diagnostic 0을 보고했다.

## 변경 시점의 원본 사실

| UTC | 관측 |
|---|---|
| 03:55:15.642257 | MQTT/GAgent network→상단 P0 action `0x01`. 선택 id `[3]` 하나, `Mode=sine`; 로컬 인코더와 byte-exact 일치, sequence 14 |
| 03:55:16.071629 | 상단의 같은 sequence 14 제어 ACK |
| 03:55:16.481366 | 상단 최초 `Mode=sine` 상태 보고 |
| 03:55:16.788555 | 하단 최초 `Mode=sine` 상태 보고. 상단 보고보다 0.307초, 상단 명령보다 1.146초 뒤 |

전체 bridge 캡처에 하단을 대상으로 하는 별도 P0 제어 action은 0개다.
전환 ±수초 구간의 AP Ethernet과 bridge 방향별 패킷도 일치하며,
상단→하단 직접 bridged IP 패킷은 없다. 상·하단 모두 시계 필드를 제외한
70개 datapoint 중 `Mode`만 Pulse→Sine으로 달라졌다. 마지막 상태 보고는
`SwitchON=true`, `TimerON=false`, 상단 `master`·하단 `async_slave`,
양쪽 `Flow=40`·`Frequency=80`, `AutoMode=constant/AutoFlow=30/AutoFreq=5`다.
48슬롯 EE×9 schedule image SHA-256
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`도
양쪽에서 그대로다. 이것은 **장비 보고값**이지 실제 모터 파형·위상 측정은 아니다.

## 무선 관측 범위와 판정

03:55:14–03:55:20Z에 독립 Wi-Fi monitor는 두 Pro 관련 176프레임,
라우터 monitor는 102프레임을 잡았다. 두 수신기 모두 해당 구간의
펌프-origin 실제 data payload frame은 0개다. 잡힌 펌프-origin 프레임은
대부분 Null-data/ACK이고, AP→펌프 암호화 QoS data는 보인다. 독립 수신기는
전체 9분 동안 하단-origin QoS data 12개를 잡았지만 전환 구간에는 0개였다.
상단↔하단을 직접 주소로 하는 프레임도 두 수신기에서 0개이나,
**펌프 송신 본문 자체를 놓친 상황이므로 직접 RF 신호 부재를 뜻하지 않는다.**
수신기 RSSI나 커널 drop 계수만으로 공중파 전량 포착을 주장하지 않는다.

Bluetooth 호스트 HCI에는 총 LE 광고 보고 972건, 전환 근처 두 Pro의
광고 보고 5건이 있다. HCI ACL 패킷과 연결 사건은 0개다. 이는 **그 호스트가
펌프와 연결해 payload를 받은 적이 없다**는 뜻일 뿐, 펌프끼리의 BLE나
다른 무선 통신이 없었다는 증거는 아니다. 호스트 HCI는 제3자 사이의
공중파 패킷 스니퍼가 아니다.

따라서 이번에도 **상단 한 건의 명령 뒤 하단이 약 1초 후 같은 Mode를
보고한다**는 IP·상태 경로는 확인됐지만, master→slave 전달 매체와
프레임은 `UNKNOWN / PARKED`다. 이 질문은 앞선 Sine·Pulse 및 이번 Sine
관측에서 반복했고, 현 수집 장비는 결정적 송신 본문을 포착하지 못했다.
반복 앱 조작 대신 재개 조건은 둘 중 하나다: (1) 펌프 근처의 별도
802.11/BLE 공중파 수신기로 **펌프 송신 payload 포착을 사전 입증**한 뒤
단회 전환을 관측하거나, (2) 제조사/펌웨어의 link-layer 진단을 확보한다.
park 동안 IP 하단 별도 제어가 관측되지 않았다는 좁은 결론만 사용하며,
전달 매체·anti-phase·물리 파형을 단정하지 않는다.
