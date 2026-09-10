# run: 2026-09-10-local-observer-dhcp-reservations

- 목적(한 문장): current observer가 사용하는 6개 unicast address를 현재 DHCP lease의 stable
  network identity에 예약해, 앞선 target 임시 갱신이 다음 lease에서 다시 drift하지 않게 했습니다.
- 이 실행이 답하려던 질문: 현재 6개 address가 router에 이미 예약돼 있는지, 아니라면 기존 가정
  기기의 DHCP·DNS 설정을 건드리지 않고 그 6개 mapping만 고정할 수 있는가.
- 실행한 commit SHA: `cd9b5a549f09c3b1c90b081a9ad054fe85ebd3ca`
- 승인 범위: maintainer에게 제시한 "6개 address의 DHCP reservation 여부를 확인하고, 없으면
  해당 장비들만 예약한다"는 다음 단계와 그 답변에 결속했습니다. 여기서 "해당 장비들"은
  감사한 observer target 6개이고, "만"은 기존 static host 29개와 나머지 가정 기기, 다른 UCI
  subsystem, network·firewall·wireless·DNS option, network restart와 router reboot를 변경하지
  않는다는 뜻입니다.
- 사용한 명령·주요 파라미터: 기존 OpenWrt exporter와 같은 read-only SSH alias가 DNS 이름이
  아니라 literal router address로 해소됨을 확인했습니다. `uci show/changes dhcp`와 current lease를
  읽고, owner-only exact backup을 `O_EXCL`·mode `0600`·file/parent fsync로 먼저 보존했습니다.
  apply 직전에 pending UCI change 0, target lease 6개가 각각 unique·unexpired, 기존 host의 IP·MAC
  및 새 named section 충돌 0임을 다시 확인했습니다. 한 SSH session 안에서 새 named host section
  6개만 current lease MAC→current IP로 추가하고 `uci commit dhcp` 뒤 dnsmasq만 reload했습니다.
- 종료 지점(stage / failure code): reservation apply와 독립 readback, dnsmasq·DNS health,
  observer 확인이 모두 PASS였습니다. rollback trigger는 발동하지 않았습니다.
- 장비와 router가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. UCI·lease·MQTT 원문을 artifact로 보존하지
    않았으므로 (a)·(b)로 올리지 않습니다. 아래 backup은 관측 증거가 아니라 router config의 exact
    rollback 수단입니다.
  - opaque artifact id 또는 안전한 상대 논리 경로:
    `router-dhcp-pre-reservation-eae9cb047fa68760`
  - UTC span (시작 / 종료): `2026-09-10T05:33:22.149719Z` /
    `2026-09-10T05:41:55.521498Z`
  - SHA-256 identity binding:
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`
    observer target는 이 stable-role digest의 입력이 아니며 current deployment의 binding은
    바뀌지 않았습니다.
  - artifact digest (SHA-256):
    `12adf3b22ff54fd1966e64abefedf632df560842c7c4e22be0de88b6d6e940c2`
    pre-change router DHCP config의 exact bytes이며 owner 일치·mode `0600`·link count 1로
    보존했습니다.
  - pre-apply에는 current target 6개 모두 active unique lease였고 reservation은 0개였습니다.
    post-apply에는 새 reservation 6개가 lease MAC·IP와 각각 exact match했고 pending UCI change는
    0개였습니다. 기존 29개 host section의 UCI 의미는 모두 불변이었습니다.
  - reload 뒤 dnsmasq process·service가 살아 있었고 loopback dnsmasq를 향한 실제 DNS 질의가
    성공해 local responder의 생존·응답성을 확인했습니다. upstream DNS forwarding은 이번 확인
    범위가 아닙니다. current lease 6개도 같은 MAC·IP로 유지됐습니다.
  - current observer는 exactly one instance, restart 0, fatal log 0이었고 `--write-enabled`와 old
    `jebao-flowd`는 없었습니다. device availability와 fresh `observed_at`은 6/6, global
    availability는 online, current discovery config는 86개, `command_topic`은 0개였습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): apply가 전부 통과해 rollback은 실행하지
  않았습니다. 같은 SSH session의 실패 경로는 commit 이후 오류에서 exact pre-change bytes를
  복원하고 dnsmasq reload·process·DNS query를 다시 확인하도록 구성했습니다. 실제 rollback
  실행을 검증했다고 주장하지 않으며, 위 owner-only backup은 보존했습니다.
- **이 실행으로 새로 확정된 사실:** current observer 6개의 address는 이전에는 DHCP reservation이
  아니었고, 이제 current router config에서 stable lease identity에 각각 예약됐습니다. 따라서
  [`target 임시 갱신`](2026-09-10-local-observer-target-refresh.md)의 DHCP reservation 미확정과
  다음 lease drift 위험은 이 router configuration 범위에서 해소됐습니다.
- 이 실행으로 확정하지 못한 것: router config의 향후 교체·factory reset·backup restore 뒤에도
  reservation이 보존되는지, Q1·Q2와 firmware capability, control-mode write는 확인하지
  않았습니다. 장비 command·control frame·device write는 0회였습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 별도 코드나 반복 실기는 필요 없습니다.
  router config를 교체·복원한 경우에만 reservation 6개를 read-only로 재확인하고, 평상시에는
  `current_endpoint_absent`가 다시 나타나는지만 기존 observer diagnostic으로 확인합니다.
