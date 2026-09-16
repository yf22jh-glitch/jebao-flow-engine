# Aquarium 자동 준비·원복 전 읽기 전용 확인

질문: 현재 Aquarium 출력60에서 기존 저출력 자격 시험과 최초값 복귀를 이미 있는 경로로
수행할 수 있는가. 기존 MQTT publication과 현재 코드의 준비·복구 계약을 대조했다.

`2026-09-16T12:10:19.902652Z–12:10:54.919862Z` 구독에서 Aquarium의 non-retained 상태는
관측시각 `12:10:42.676529Z`, SwitchON=true, TimerON=false, FeedSwitch=false,
Motor_Speed60, FeedTime10이었다. 이는 해당 시점 관측이며 과거값 일치 gate가 아니다.
opaque artifact `aquarium-live-20260916.a6x4lH/current-states.json`, SHA-256
`348272bb098bbb9f2f7637feb6c58354989c9bbc1a633bf0ddad03fe9dbdbada`.
증거 등급은 (b) 보존된 구조화 MQTT 출력이며 원 wire frame이 아니다.

기존 Aquarium producer는 첫 journal/write 전에 현재 speed≤45를 검사한다. 일반 coordinator는
현재 자격이 먼저 필요하고 recover는 이미 소유한 journal만 복구한다. 따라서 자격 없는
Aquarium60을 자동으로40으로 준비한 뒤 최초60으로 복귀하는 기존 public 경로는 없다.
오프라인에서 config30..100으로 기존 함수를 호출해39/40/45 수용,46/60 거부를 확인했고
기존 Aquarium scalar/power tests20개가 통과했다. 실물 수용·물리 안전을 증명한 것은 아니다.

사용자가 임시 변경 후 원복을 승인했고, 이어 필요한 자동 준비·원복 기능 구현을 별도로
승인했다. 이 승인을 기존 상한 삭제나 가짜 자격 발급으로 해석하지 않는다.
Claude와 Agent Bridge를 통한 읽기 전용 정적 검토에서는 기존 보존 template이 Pro/Bar뿐이고
Aquarium의 active JS bytes와 product 귀속이 아직 UNKNOWN임을 확인했다. 설치 앱의 제품
설정 JSON은 template JS 대용이 아니다. 이 보고를 실제 앱 동작 관측으로 승격하지 않는다.

**새로 확정된 사실:** 현재 소프트웨어에는 Aquarium 자동 준비·최초값 복귀 operation이 없으며
실기 자격용 Aquarium template 결속도 미확보다. 장비 write/MQTT publish/직접 장비 세션/
서비스 변경/실기 자격 발급은 모두0이다. 기존 Pro/Bar 운영을 유지했다.

대안은 (1) 소유 원본을 먼저 내구화하는 별도 준비형 producer를 구현하고 실제 앱 template
양성 증거를 확보한 뒤 실기하는 것, (2) 앱에서 사람이 준비·원상복귀하고 기존 producer를
사용하는 것이다. 어느 쪽도 template 근거를 생략할 수 없다. 준비형 구현은 승인됐지만
실기는 template 확보까지 park하며 그동안 Aquarium HA live 제어는 열리지 않는다.
