# run: 2026-09-10-local-observer-first-activation

- 목적(한 문장): current native MQTT Discovery 경로를 observer로 처음 활성화하고 실제 장비의
  fresh read-only state가 Home Assistant까지 도달하는지 확인했습니다.
- 이 실행이 답하려던 질문: old aggregate daemon과 custom component를 기동하지 않고 정확히 한
  `jebao-flow-local` observer가 native entity와 현재 상태를 제공할 수 있는가.
- 실행한 commit SHA: `a4a61601bbd24a59490ed5699508170f4efb8396`
- 사용한 명령·주요 파라미터: reviewed source archive의 digest를 홈서버에서 재확인하고 current
  private config를 owner-only 사본으로 고정한 뒤, `prepare-compose`와 generated Compose의
  `jebao-flow-local` service를 `runtime.mode=observer`, `dry_run=true`, `--write-enabled` 없음으로
  실행했습니다. 이어 MQTT와 Home Assistant registry를 읽기 전용으로 확인하고 같은 source의
  Gizwits discovery를 한 번 더 수행했습니다.
- 종료 지점(stage / failure code): Compose stage와 runtime 기동은 PASS였습니다. Home Assistant
  native discovery도 PASS였지만 fresh state 단계는 `current_discovery_unavailable`로 종료됐습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. 원본 discovery frame이나 reviewed daemon의
    device-state artifact를 보존하지 않았으므로 (a)·(b)로 올리지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: n/a (보존된 raw·daemon artifact 없음)
  - UTC span (시작 / 종료): `2026-09-09T19:20:34.623449078Z` /
    `2026-09-09T19:28:39Z`
  - SHA-256 identity binding:
    `75ec8f5edc790ceb5ac621248dcee756f548c15797c6cbc38caa10a8338326a0`
  - artifact digest (SHA-256): n/a
  - current discovery 응답은 4개였고 configured physical binding 6개 중 4개가 정확히 한 번씩
    일치했습니다. 2개는 이번 관측에 없었고 ambiguous match는 0개였습니다.
  - broker에는 current namespace discovery config 86개가 있었고 `command_topic`은 0개였습니다.
    global availability는 online이었지만 device availability는 online 0 / offline 6이었고 fresh
    `observed_at` payload는 0개였습니다.
  - Home Assistant registry에는 current native MQTT entity 86개, legacy entity 0개, control-domain
    entity 0개, Jebao custom-component config entry 0개가 확인됐습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 장비 write와 control frame은 0회였으므로 장비
  원복 대상은 없습니다. observer service는 이 기록 시점에 한 instance로 계속 실행 중이며 old
  `jebao-flowd`는 0개였습니다.
- **이 실행으로 새로 확정된 사실:** current native discovery와 Home Assistant 등록은 observer
  계약대로 동작합니다. 그러나 read-only poller가 write coordinator와 같은 strict
  `resolve_many()`를 사용해, 한 번의 discovery에서 4대가 정확히 식별됐어도 나머지 2대 중 하나의
  resolution 실패가 배치 전체를 `current_discovery_unavailable`로 접습니다. 즉 이번 fresh-state
  실패는 여섯 장비 모두의 부재를 뜻하지 않고 observer의 per-device mismatch isolation 결함입니다.
- 이 실행으로 확정하지 못한 것: 관측되지 않은 두 장비가 전원·네트워크·현재 discovery 중 어느
  이유로 응답하지 않았는지, 네 장비의 decoded fresh state, Q1·Q2와 firmware capability는 확인하지
  못했습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: writer가 쓰는 strict all-or-nothing
  `resolve_many()` 의미는 유지하고, observer가 한 번의 discovery 결과를 target별로 분리해 정확히
  식별된 장비는 계속 읽고 누락·ambiguous 장비만 unavailable로 내보내야 합니다. 그 source와
  회귀를 검토·커밋한 뒤 같은 observer deployment를 교체하고 fresh state를 다시 확인합니다.
