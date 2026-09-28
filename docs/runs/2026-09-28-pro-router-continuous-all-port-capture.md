# run: 2026-09-28-pro-router-continuous-all-port-capture

- 목적: 세 수류모터의 장비측 네트워크 패킷을 공유기 AP와 IoT 브리지에서 전 포트로 상시 보존한다.
- 이 실행이 답하려던 질문: 기존 홈서버 캡처가 놓치는 cloud→장비 명령 경로를 공유기에서 지속적으로 기록할 수 있는가?
- 실행한 commit SHA: `ca55e47` (수집기 추가 전 HEAD).
- 조작: 공유기의 `phy0-ap0`, `br-lan.30`에서 읽기 전용 `tcpdump`를 각각 시작하고, 원본 스트림을 홈서버의 기존 LG 대용량 캡처 마운트로 전송했다. 50 MB × 80파일 순환 보존은 인터페이스별이다. 두 systemd 서비스는 활성화했으나 AP 설정·라우팅·장비 설정·기존 제어 서비스는 변경하지 않았다. 장비 write는 0회다.
- 종료 지점: 상시 서비스는 계속 실행 중. 2026-09-28T09:13Z 검사에서 양쪽 `ActiveState=active`, `NRestarts=0`.

## 보존 증거

진행 중인 pcap의 마지막 부분은 쓰기 중이므로 길이가 불완전할 수 있다. 아래는 완전한 프레임만 별도 고정 사본으로 만든 **(a) preserved raw artifact**다. 이 사본은 원본을 재해독할 수 있지만, 진행 중인 순환 파일 전체의 digest라고 주장하지 않는다. 주소·인증 정보가 포함될 수 있어 private 저장소에만 `0600`으로 보존했고 이 저장소에는 넣지 않았다. private artifact id는 `jebao-router-allports-20260928-01/initial`이다.

| 인터페이스 | UTC span | 패킷 | 크기 | SHA-256 |
|---|---|---:|---:|---|
| AP Ethernet | 2026-09-28T09:01:41Z–09:11:28Z | 6,831 | 1,039,052 B | `bc6b3b7508d73d17ced3b0280677d3d4ade92a12c5b5c951772da0be6cae05fa` |
| IoT bridge | 2026-09-28T09:01:58Z–09:11:45Z | 7,262 | 1,074,700 B | `bd2f3d90e3d61214981ced07a28acf7c401519643e3f7d9e0214b07f9191a796` |

물리 identity binding SHA-256은 상단 Pro `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`, 하단 Pro `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`, Bar `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`이다. 현재 캡처 필터는 기존 수집기의 세 장비 IP·MAC 바인딩을 읽어 구성하고 포트 12416·12414 및 ARP도 포함한다. 공유기 bridge FDB에서는 세 MAC 모두 `phy0-ap0`에 있었다. 두 사본 모두 세 바인딩의 IP 패킷이 각각 관측됐다.

| 고정 사본 | TCP/1883 | TCP/12416 | UDP/12414 | ARP | Wireshark `tcp.analysis.lost_segment` |
|---|---:|---:|---:|---:|---:|
| AP | 1,233 | 4,379 | 584 | 626 | 0 |
| IoT bridge | 1,240 | 4,355 | 594 | 1,064 | 0 |

숫자는 각 사본의 독립 수집 구간에 한정된다. 두 시작·종료 시각이 달라 행별 패킷 수를 일대일 전달률로 해석하지 않는다. 인터페이스 통계 조회 시 두 인터페이스 RX/TX errors·dropped는 0이었다. 이것도 무선 원본까지 손실 0임을 증명하지 않는다.

## 이번에 새로 확정한 것과 한계

공유기 AP와 IoT 브리지에서 세 장비가 포함된 전 포트 IP/Ethernet 스트림을 기존 LG 대용량 볼륨에 지속 기록할 수 있고, 위 고정 구간에는 실제 TCP/1883·12416과 UDP/12414 패킷이 있다. 이전 [앱 Pulse cloud 제어 원문](2026-09-28-pro-manual-pulse-cloud-command-capture.md)은 장비측 TCP/1883의 MQTT/P0 명령이 복호화 없이 해독된다는 별도 직접 근거다.

이 수집기는 라우터·장비 사이의 **Ethernet/IP 관측**이지 투명 제어 프록시가 아니다. 앱→cloud 상행의 원문, AP 드라이버에 드러나지 않는 802.11 원본·Bluetooth·장비 내부 신호, Master→Slave 전달 매체와 실제 물리 위상은 이 사본으로 확정하지 못했다. 순환 보존이므로 각 인터페이스에서 약 4 GB 이후 오래된 원본은 덮인다. DHCP 바인딩이 바뀌면 실행 중인 IP 필터는 자동 재계산되지 않지만, AP의 장비 MAC 필터는 남는다. 다음 단계는 제어 조작의 정확한 UTC 창을 이 원본과 MQTT ingress/journal 기록에 함께 대조하는 것이다.
