# run: 2026-09-10-local-observer-target-refresh

- 목적(한 문장): stale private unicast target 2개를 방금 exact-match로 확인한 current address로
  임시 갱신하고, observer의 6/6 fresh 관측을 장비 write 없이 복구했습니다.
- 이 실행이 답하려던 질문: discovery target drift만 교정하면 stable physical identity·role binding과
  observer-only 경계를 유지하면서 configured 6개 장비가 모두 fresh state를 발행하는가.
- 실행한 commit SHA: `79b11d492f831c73375448e8c88a725985bb20b2`
- 사용한 명령·주요 파라미터: current private config가 running config와 semantic exact match이고
  `runtime.mode=observer`인지 확인했습니다. current HA network inventory와 directed discovery의 exact
  identity match를 다시 결속한 뒤, owner-only exact backup을 먼저 fsync하고 decoded config에서
  `observer.targets`의 stale unicast 2개만 current 2개로 교체했습니다. reviewed image의
  `prepare-compose`를 network-none으로 실행하고 generated Compose를 검증했습니다. 이어 기존 observer를
  먼저 완전히 정지해 중첩 0을 확인한 뒤 새 observer 한 instance를 시작했습니다.
- 종료 지점(stage / failure code): private target refresh, fresh Compose generation, stop-first 재기동,
  MQTT retained state와 Home Assistant registry의 read-only 검증이 모두 PASS였습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. 원본 discovery·state frame이나 daemon의 durable
    state artifact를 보존하지 않았으므로 (a)·(b)로 올리지 않습니다. 아래 backup은 관측 증거가 아니라
    private config의 exact rollback 수단입니다.
  - opaque artifact id 또는 안전한 상대 논리 경로:
    `observer-config-pre-refresh-477692b86a38a1d5`
  - UTC span (시작 / 종료): `2026-09-10T04:49:07.187236Z` /
    `2026-09-10T04:54:20.395505Z`
  - SHA-256 identity binding:
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`
    target는 이 digest의 입력이 아니며, 갱신 전후 값이 byte 동일했습니다.
  - artifact digest (SHA-256):
    `62c98a57e089f5fda89d5676883109793ae8f657c8afa7d9ba0fd811732625ba`
    private backup은 owner 일치·mode `0600`·link count 1로 보존했습니다.
  - configured 6개 장비 모두 device availability `online`과 fresh `observed_at` state를 발행했습니다.
    `current_endpoint_absent`, `current_endpoint_ambiguous` 및 다른 state-unavailable reason은 각각
    0개였습니다.
  - current namespace discovery config 86개, `command_topic` 0개, pre-namespace discovery config
    0개였고 global availability는 online이었습니다.
  - Home Assistant registry에는 current namespace native MQTT entity 86개, pre-namespace entity
    0개, Jebao custom-component config entry 0개가 유지됐습니다.
  - observer runtime은 exactly one instance, old/new overlap 0, restart 0, fatal log 0이었고
    `--write-enabled`와 qualification은 없었습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 장비 command·control frame·device write는
  0회였으므로 장비 원복 대상은 없습니다. target 갱신 전 private config의 exact bytes를 위 digest로
  owner-only 보존했습니다. 새 observer가 6/6 fresh 상태로 정상 동작해 config rollback은 실행하지
  않았습니다.
- **이 실행으로 새로 확정된 사실:** stable identity와 role mapping을 바꾸지 않고 stale unicast
  target 2개만 current exact-match address로 바꾸면 observer의 fresh 관측이 4/6에서 6/6으로
  복구됩니다. D1f의 absent 결과는 장비 전원 상태가 아니라 target drift를 정확히 격리한 것이었습니다.
- 이 실행으로 확정하지 못한 것: current address의 DHCP reservation 여부와 다음 lease 이후의
  지속성, Q1·Q2와 firmware capability, control-mode write는 확인하지 못했습니다. 이번 결과를
  unicast drift의 영구 해소로 승격하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: router에서 6개 unicast address의 DHCP
  reservation 여부를 확인하고, 예약이 없다면 현재 mapping을 reservation으로 고정한 뒤 별도 기록으로
  영구 해소를 결속합니다. 그 전까지 이번 변경은 작동을 복구한 임시 재고정이며, observer의
  `current_endpoint_absent` 재발 여부를 진단으로 유지합니다.
