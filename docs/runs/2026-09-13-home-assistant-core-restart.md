# run: 2026-09-13-home-assistant-core-restart

- 목적(한 문장): 사용자가 Home Assistant Core를 직접 재시작한 뒤 current Jebao discovery·state·
  control surface가 byte exact로 복귀하고, 재시작 창에 local-control device write나 제거된 Bar
  diagnostic entity의 재등장이 없었는지 read-only로 확인했습니다.
- 이 실행이 답하려던 질문: current retained MQTT discovery와 non-optimistic state를 사용하는
  Home Assistant가 Core restart 뒤에도 의도한 `manual_flow` control 세 개만 복구하고, command
  replay·qualification 변화·옛 Bar diagnostic ghost 없이 현재 authority를 다시 구성하는가.
- 사용자 승인 범위:
  - 사용자가 Home Assistant Core/container 재시작을 직접 수행했습니다. agent는 HA update·restart·
    설정 변경·entity 조작·MQTT publish를 수행하지 않았고, 재기동 전후 Docker metadata·broker
    retained state·HA entity registry·Jebao durable tree를 read-only로 확인했습니다.
  - 사용자는 재시작 중 Jebao `number` slider를 조작하지 않았습니다. automation·scene·script의
    command 가능성은 추측으로 배제하지 않고, 모든 current local-control device frame보다
    journal-first record가 선행하는 계약과 journal tree 불변으로 실제 write `0`을 판정했습니다.
- 실행한 commit SHA:
  - current Jebao runtime image에 포함된 최종 reviewed source는
    `045f086b82c6c9a25a8e22cd892be2f9cf7fdee9`, 직전 first-Pro power run record는
    `21370b63148f4630748e95b7c6f23296f39b5fe4`입니다.
  - Jebao runtime image id는
    `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`, restart 뒤 관측한 HA
    container image id는
    `sha256:3ac96161524cf5ce65fdf4922f844f4f30a55fbbdab024b8055f0483d599d413`입니다. HA image의
    pre/post 동일성은 사전 digest를 별도로 보존하지 않아 주장하지 않습니다.
- 실행 전 조건:
  - 직전 first-Pro power run 종료 뒤 persistent Jebao service exactly `1`, 장비 fresh `6/6`·power
    ON `6/6`, unfinished journal `0`, retained command payload `0`이었습니다. current authority는
    qualification binding `2`·receipt `6`·ignored `0`, direct `manual_flow` control `3`, direct
    `power` control `0`, group command·resume `0`이었습니다.
  - pre-restart comparison anchor인 journal inventory는 operations `11`·blobs `33`·files `44`, sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256
    `83cc8ecadb4758426c2e2f5a7dc1c2d91dba2d857ac1a9f8e6edfc706077c72c`였습니다. fixed
    qualification receipt root는 files `14`, 같은 방식의 tree SHA-256
    `7acb417634c63296b4bdd0814ea84d571ff0e27cacc9476f1f8c4135e149f8f8`였습니다.
  - current discovery는 `86`개, sorted `(topic, SHA-256(canonical payload))` compact-JSON SHA-256
    `0c40cbc208a075a1e0fb2fb5544d5e9758f125a3df7a48460c0d071bedffa688`였습니다. 이 세
    canonicalization은 각 원 기록과 같은 정의를 재사용합니다.
- 사용한 명령·주요 파라미터:
  - 사용자가 기존 HA container를 직접 재시작했습니다. agent는 Docker inspect로 기존 container의
    creation·새 process start를 구분하고, current Jebao service가 같은 process·image로 계속 실행
    중인지 확인했습니다.
  - 별도 random-id MQTT client는 discovery prefix와 exact availability·command topics만 subscribe하고
    publish하지 않았습니다. current offline projection의 `86`개 discovery canonical bytes와 fresh
    subscriber의 retained payload를 양방향 exact 비교하고, command topic 세 개의 retained/live
    message와 제거된 Bar diagnostic sensor topic의 재등장을 함께 관측했습니다.
  - current HA entity registry는 read-only로 열어 projection의 세 `manual_flow` unique id에
    해당하는 MQTT entity의 domain·disabled state만 집계했습니다. entity id·logical member·private
    physical key는 출력하거나 기록하지 않았습니다.
  - 마지막으로 current manifest 여섯 target을 한 번의 fresh discovery로 resolution한 뒤, 장비별
    shared read lease와 read-only authenticated session으로 직접 읽었습니다. control send method나
    local-control write lease는 사용하지 않았습니다.
- 종료 지점(stage / failure code): `home_assistant_core_restart_exact_recovery` PASS. HA는 running,
  Jebao service는 재시작되지 않았고, discovery missing·mismatch `0`, retained/live command message
  `0`, local-control journal 변화 `0`, 장비 fresh `6/6`·power ON `6/6`으로 끝났습니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: unchanged terminal journal·blob tree와 immutable qualification receipt tree는
    **(b) preserved structured/durable daemon artifact**입니다. Docker inspect, broker fresh-subscriber
    audit, current HA entity-registry read와 authenticated fresh device read는 별도 raw copy를 보존하지
    않은 **(c) reconstructed operator observation**입니다. raw MQTT packet·wire frame을 보존하지 않아
    (a)는 주장하지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: 별도 raw artifact를 만들지 않았습니다. 비교
    anchor는 위 journal·receipt tree와 discovery canonical digest입니다.
  - 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다. 이 run의 관측은 KST
    `2026-09-13` 범위이며 기존 HA container 생성 시각만 이전 날짜입니다. container는
    `2026-09-10T08:00:27.712077519Z`에 생성됐고 새 process는
    `2026-09-13T00:22:40.073448472Z`에 시작됐습니다. final broker·registry audit와 direct fresh
    read는 `2026-09-13T01:46:07.876851Z` 이전에 끝났습니다. exact stop time과 availability
    outage duration은 보존하지 않아 주장하지 않습니다.
  - SHA-256 identity binding: six physical identity sorted-set digest
    `5c0a9b8943832df7f8fc30472a6b57af39a5ad3b997fce34dca56b6a80da4a19`와 stable role binding
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`의 current deployment를
    관측했습니다. 이 run은 device write가 없어 개별 `qualification-target:` anchor를 새로
    주장하지 않습니다.
  - artifact digest (SHA-256): discovery
    `0c40cbc208a075a1e0fb2fb5544d5e9758f125a3df7a48460c0d071bedffa688`, journal tree
    `83cc8ecadb4758426c2e2f5a7dc1c2d91dba2d857ac1a9f8e6edfc706077c72c`, qualification receipt
    tree `7acb417634c63296b4bdd0814ea84d571ff0e27cacc9476f1f8c4135e149f8f8`입니다.
  - post-restart catalog는 binding `2`·receipt `6`·qualified pair `3`·ignored `0`, direct
    `manual_flow` control `3`, direct `power` control `0`, group control·resume `0`이었습니다.
    discovery는 expected/present `86/86`, missing·mismatch `0`, canonical digest가 pre-restart
    anchor와 exact 일치했습니다.
  - global·device availability는 `7/7 online`이었습니다. 세 command topic에서 8초 동안 받은
    retained/live message는 `0`이고, old Bar diagnostic sensor topic도 fresh subscriber에 message
    `0`으로 absent였습니다. absence는 broker retained record가 없다는 관측이며 zero-length payload가
    보존됐다고 주장하지 않습니다. 이 구독은 Core restart 약 1시간 20분 뒤에 수행돼 재시작 창의
    live command를 직접 관측하지 못하며, 그 창의 device write `0`은 journal tree 불변으로
    판정했습니다.
  - current HA entity registry에서 projection의 세 `manual_flow` unique id와 일치한 MQTT entity는
    정확히 `3/3`, 모두 domain `number`이고 disabled entry는 `0`이었습니다. Bar unique id와 같은
    `sensor` entry는 `0`이므로 앞선 deployment에서 제거한 diagnostic variant가 이번 Core restart
    뒤 되살아나지 않았습니다. 이는 post-restart current registry 관측이며 registry 전체의
    byte-exact pre/post 동일성은 주장하지 않습니다.
  - post-restart journal inventory는 operations `11`·blobs `33`·files `44`, qualification receipt
    root는 files `14`이고 두 tree digest가 pre-restart anchor와 exact 일치했습니다. current
    local-control code-write path에서는 재시작 창의 device write가 `0`이고 qualification mutation도
    `0`이었습니다.
  - independent authenticated fresh read는 장비 `6/6` available·power ON `6/6`이었습니다. 다음
    대상인 두 번째 Pro는 public anchor exact match `1`, power ON·TimerON·Mode `constant`·Linkage
    `independent`·Flow `40`이었습니다.
  - HA container의 manual stop/start 뒤 restart counter `0`과 Jebao service의 counter `0`은 각각
    새 수동 기동·기존 process의 현재 값일 뿐 operation 간 연속성 증거로 사용하지 않습니다.
- 원복 검증 결과:
  - HA Core는 current discovery `86`개와 availability `7/7`을 다시 소비했고, current Jebao control
    surface 세 개가 exact payload로 복귀했습니다. removed Bar diagnostic variant는 broker와 current
    entity registry 양쪽에 없었습니다.
  - Jebao journal·receipt tree는 byte exact 불변이고 direct fresh device `6/6`·power ON `6/6`이라
    이 restart를 위한 device·authority 원복 동작은 필요하지 않았습니다.
- **이 실행으로 새로 확정된 사실:** 현재 HA·MQTT·Jebao deployment 조합은 사용자의 HA Core restart
  뒤 discovery `86/86` byte exact, `manual_flow` control `3`, availability `7/7`로 복귀했고,
  local-control device write·qualification mutation 없이 removed Bar diagnostic variant를 되살리지
  않았습니다. 따라서 앞선 기록의 HA Core restart 복귀와 Bar registry 정리 미확정 항목은 이
  exact deployment 범위에서 닫혔습니다.
- 이 실행으로 확정하지 못한 것: raw MQTT packet·device frame, HA image의 pre/post byte equality,
  exact HA stop time·availability outage duration, future HA update·container recreate·host reboot에서의
  복귀, automation·scene 설정 전체의 정적 부재, direct `power` surface와 group surface, 장시간 운용,
  Bar·두 번째 Pro power-OFF 비전원 필드 유지와 Q1·Q2는 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: HA restart·recreate·update를 서로 다른
  operation으로 구분하고, 매번 current projection의 discovery bytes·removed tombstone topic·entity
  registry domain을 재확인합니다. command replay는 retained payload 관측만으로 단정하지 않고
  journal tree 전후 불변으로 확인합니다. 다음 live 단계는 사용자가 두 번째 Pro의 Timer ownership을
  OFF로 전환하고 agent가 fresh state를 재확인한 뒤에만 시작하며, 실기 뒤 TimerON schedule ownership을
  앱으로 복원합니다.
