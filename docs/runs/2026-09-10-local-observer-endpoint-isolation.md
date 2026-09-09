# run: 2026-09-10-local-observer-endpoint-isolation

- 목적(한 문장): D1f가 정확히 식별된 장비의 fresh read를 살리고, 관측되지 않은 장비만
  unavailable로 격리하는지 current native observer 배포에서 확인했습니다.
- 이 실행이 답하려던 질문: 한 번의 current discovery에서 정확히 식별된 장비의 fresh state를
  계속 발행하면서 writer의 strict all-or-nothing resolution과 wrong-device 방어를 바꾸지 않을 수
  있는가.
- 실행한 commit SHA: `79b11d492f831c73375448e8c88a725985bb20b2`
- 사용한 명령·주요 파라미터: reviewed source archive와 새 이미지의 D1f symbol을 확인하고,
  generated Compose가 `runtime.mode=observer`, `--write-enabled` 없음, qualification secret 없음임을
  재검증했습니다. 새 입력을 network-disabled stage로 먼저 검증한 뒤 기존 observer를 완전히
  정지하고 새 observer 한 instance를 시작했습니다. 이어 MQTT와 Home Assistant registry를
  read-only로 확인했습니다.
- 종료 지점(stage / failure code): Compose stage와 새 runtime 기동, per-device fresh-state 격리,
  Home Assistant native entity 확인이 모두 PASS였습니다. runtime restart와 fatal log는 0개였습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. 원본 discovery·state frame이나 reviewed
    daemon의 durable state artifact를 보존하지 않았으므로 (a)·(b)로 올리지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: n/a (보존된 raw·daemon artifact 없음)
  - UTC span (시작 / 종료): `2026-09-09T20:20:28.805684141Z` /
    `2026-09-09T20:24:54Z`
    이 span은 새 runtime 시작부터 최종 확인까지의 전체 절차 구간이고, 그 안의 read-only MQTT
    관측 창은 18초였습니다.
  - SHA-256 identity binding:
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`
    이 값은 running Compose의 `--role-binding-sha256`이며,
    `stable_role_binding_sha256(current install manifest)`가 logical member·logical role·physical
    identity key·product key의 정렬된 canonical JSON으로 계산한 current binding입니다.
  - artifact digest (SHA-256): n/a
  - configured physical binding 6개 중 4개가 online availability와 fresh `observed_at` payload를
    발행했고, 나머지 2개만 `current_endpoint_absent`였습니다.
  - `current_endpoint_ambiguous`, `current_discovery_unavailable`,
    `current_resolution_binding_mismatch`, `fresh_read_unavailable`은 각각 0개였습니다.
  - broker에는 current namespace discovery config 86개, `command_topic` 0개,
    pre-namespace discovery config 0개가 확인됐고 global availability는 online이었습니다.
  - Home Assistant registry에는 current namespace native MQTT entity 86개, pre-namespace entity
    0개, Jebao custom-component config entry 0개가 확인됐습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 장비 write와 control frame은 0회였으므로 장비
  원복 대상은 없습니다. 기존 observer를 먼저 정지해 runtime 중첩은 0이었고, 새 observer는 이 기록
  시점에 exactly one instance, restart 0으로 실행 중이며 old `jebao-flowd`는 0개였습니다.
- **이 실행으로 새로 확정된 사실:** D1f의 target별 endpoint 분리가 실제 current deployment에서도
  동작합니다. 한 target의 부재가 batch 전체를 가리지 않고, exact-matched 4개는 fresh state와 online
  availability를 계속 발행하며 absent 2개만 독립적으로 unavailable이 됩니다.
- 이 실행으로 확정하지 못한 것: 관측되지 않은 두 장비가 전원·LAN·stale config 중 어느 이유로
  응답하지 않았는지, Q1·Q2와 firmware capability, control-mode write 동작은 확인하지 못했습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: absent 두 장비의 실제 배포 상태는 config와
  현재 전원·LAN inventory를 대조해 별도로 판정합니다. 그 사실을 추측해 resolver나 poller를 다시
  수정하지 않으며, control mode나 장비 write는 별도 concrete operation으로만 진행합니다.
