# run: 2026-09-10-local-observer-discovery-target-audit

- 목적(한 문장): D1f에서 absent로 격리된 두 장비가 실제로 꺼진 것인지, 아니면 observer의
  discovery target이 현재 네트워크 위치와 어긋난 것인지 장비 write 없이 판정했습니다.
- 이 실행이 답하려던 질문: HA의 current network inventory와 directed Gizwits discovery를
  교차하면 두 absent 결과를 전원·LAN 부재와 discovery target drift 중 어느 쪽으로 분류할 수
  있는가.
- 실행한 commit SHA: `79b11d492f831c73375448e8c88a725985bb20b2`
- 사용한 명령·주요 파라미터: Home Assistant Recorder의 최신 network inventory를 read-only로
  조회하고, private observer target 집합과 현재 6개 장비 주소를 메모리에서 대조했습니다. 이어
  absent였던 Pro 수류 펌프 1대와 DC 리턴 펌프 1대의 current address에만 Gizwits
  `DISCOVER_REQUEST`를 보내 configured physical identity와 exact match인지 확인했습니다. 같은
  timeout으로 limited broadcast와 current 주소에서 추론한 단일 `/24` directed-broadcast 후보도
  별도로 확인했습니다. private 주소·MAC·vendor device id·payload는 출력하거나 보존하지
  않았습니다.
- 종료 지점(stage / failure code): read-only target audit PASS. config·Compose·broker retained
  state·Home Assistant registry는 변경하지 않았고 observer도 재시작하지 않았습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. 원본 UDP frame이나 Recorder query 결과를
    보존하지 않았으므로 (a)·(b)로 올리지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: n/a (보존된 raw·daemon artifact 없음)
  - UTC span (시작 / 종료): `2026-09-10T04:41:08.264729Z` /
    `2026-09-10T04:41:25.703066Z`
  - SHA-256 identity binding:
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`
    이 값은 running observer가 검증한 current stable-role binding이며 private identity 원문을
    포함하지 않습니다.
  - artifact digest (SHA-256): n/a
  - HA current network inventory에서 configured binding 6개 모두의 current address를 유일하게
    대응시켰습니다. private observer의 device-unicast target은 6개 중 4개만 current address와
    일치했고, stale unicast 2개와 누락된 current address 2개가 정확히 대응했습니다.
  - 누락된 두 current address를 향한 directed discovery는 response 2개, configured physical
    identity exact match 2개, ambiguous match 0개였습니다.
  - limited broadcast와 단일 `/24` directed-broadcast 후보는 각각 response 0개였습니다. 장비망은
    host의 directly connected prefix가 아니라 gateway를 통한 default route에 있었으므로, current
    topology에서 기본 broadcast만으로 unicast target을 대체할 근거는 얻지 못했습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 장비 command·control frame·device write와
  broker publish는 모두 0회였고 private config도 바꾸지 않았으므로 원복 대상은 없습니다. 기존
  observer exactly one instance는 observer mode·`--write-enabled` 없음·restart 0 상태를 유지했습니다.
- **이 실행으로 새로 확정된 사실:** absent였던 두 장비는 꺼진 것이 아닙니다. 둘 다 current LAN
  address에서 configured physical identity로 discovery에 응답하며, 2/6 absent의 직접 원인은
  private `observer.targets`의 stale unicast 2개입니다.
- 이 실행으로 확정하지 못한 것: current address가 router의 DHCP reservation으로 고정돼 있는지,
  다음 lease 이후에도 같은 address가 유지되는지, Q1·Q2와 firmware capability, control-mode write는
  확인하지 못했습니다. broker absence나 broadcast 전달 가능성을 일반화하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: broadcast가 현재 routed topology에서 응답하지
  않으므로 영구 해소에는 router의 DHCP reservation처럼 unicast address를 안정화했다는 별도
  network fact가 필요합니다. 그 확인 전 current address 반영은 6/6 관측을 복구하는 임시
  재고정으로만 취급하고, root cause가 영구 해소됐다고 주장하지 않습니다.
