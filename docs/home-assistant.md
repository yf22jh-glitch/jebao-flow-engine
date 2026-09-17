# Home Assistant 연동

## 현재 배포 경계

```text
Home Assistant built-in MQTT integration
                │ native discovery / state / command
                ▼
        jebao-flow-local
                │ Jebao LAN protocol
                ▼
          physical pumps
```

현재 Home Assistant 경로는 built-in MQTT integration이 `jebao-flow-local`의 native MQTT
Discovery를 소비하는 방식입니다. 펌프 IP, MAC, product key 또는 Gizwits/Jebao LAN 프로토콜은
Home Assistant에 전달하지 않습니다. 그룹 계산과 장비 제어권은 `jebao-flow-local` 하나에만
있습니다.

Home Assistant의 MQTT integration이 먼저 구성돼 있으면 custom component 설치나 Home Assistant
재시작 없이 daemon이 discovery config를 publish할 때 entity가 만들어집니다. daemon 설치·기동은
[`docs/local-control.md`](local-control.md)의 standalone Compose 절차를 따릅니다. 첫 observer
배포에서는 control entity와 command subscription이 0개이고 진단·actual state만 나타납니다.

## 동결된 legacy custom component와 카드

저장소의 `custom_components/jebao_flow`는 old `jebao-flowd`가 publish하는 aggregate
`system/config`, `groups/+/state`, `devices/+/state`, `requests/+/result` 계약 전용입니다. current
`topic_prefix/local/...` native-discovery 계약을 소비하지 않으며 config entry를 만들면 별도 group·
device command publisher가 됩니다. old daemon은 local-control journal·per-device lease에 참여하지
않으므로 두 경로를 동시에 구성·실행하지 않습니다.

따라서 아래 설치·카드 예시는 **legacy 계약 참고용이며 현재 배포 절차가 아닙니다.** 홈서버에
파일이 남아 있어도 config entry·entity가 0이면 inert합니다. current 배포를 위해 파일을 sync하거나
통합을 추가하거나 카드 resource를 등록하지 않습니다. native discovery를 읽는 card-only 개조는
별도 제품 작업입니다.

### Legacy 설치 참고

저장소의 `custom_components/jebao_flow` 디렉터리를 Home Assistant 설정 디렉터리 아래에
복사합니다.

```text
/config/custom_components/jebao_flow/
```

Home Assistant를 재시작한 뒤 `설정 → 장치 및 서비스 → 통합 추가 → Jebao Flow Engine`에서
데몬과 동일한 MQTT 토픽 접두사(기본값 `jebao-flow/main`)를 입력합니다. Home Assistant의
MQTT 통합이 먼저 구성되어 있어야 합니다.

통합은 다음 프런트엔드 모듈을 제공합니다.

```text
/jebao-flow/jebao-flow-card.js
```

대시보드의 리소스 관리에서 위 URL을 `JavaScript Module`로 한 번 등록합니다.

### Legacy 메인 3대 수류 그룹 카드

```yaml
type: custom:jebao-flow-card
instance: main
topic_prefix: jebao-flow/main
group: main_flow
title: 메인 수류
```

카드는 `jebao_flow_group_id` 속성이 있는 엔티티를 자동으로 찾습니다. 두 메인 수류모터와
바형 크로스플로우의 역할, 목표 출력, actual 출력, gain, phase와 개별 제어 상태를 한 화면에
표시합니다.

그룹 소속 장비의 개별 ON/OFF 또는 출력을 바꾸면 해당 장비는 `manual_override` 상태가 됩니다.
그룹 패턴은 다른 멤버에서 계속되며 수동 장비는 다음 패턴 tick에 덮어쓰이지 않습니다. 장비별
`그룹 복귀` 버튼은 그 장비 하나만 다시 패턴에 합류시킵니다. 별도 `전체 그룹 복귀` action은
없으며, 실행 중인 그룹에 같은 ON 명령을 다시 보내면 기존 검증 경로가 모든 override를 해제해
같은 역할을 합니다. 정지된 그룹은 다음 ON에서 모든 override를 해제하고 함께 시작합니다. 그룹
전원 ON/OFF와 비상 정지는 명시적인 전체 명령이므로 모든 멤버에 적용됩니다.

그룹의 기본 `execution_strategy`는 `software_independent`입니다. 이때 세 펌프는 장비 Linkage를
`independent`로 유지하고 서버가 각 멤버의 gain/phase/출력을 계산하므로 그룹 제어와 개별
`manual_override`를 함께 사용할 수 있습니다.

예약된 `native_linked` 전략은 동일 Pro 두 대만 master/slave 페어로 두고 바형 펌프는 독립 보조로
남기는 하이브리드 구조입니다. 현재 서버는 이를 `experimental`, `available=false`,
`hardware_not_qualified`로만 게시합니다. HA 패턴 선택지와 제어 엔티티에는 노출하지 않으며,
pair 멤버의 개별 전원·출력도 잠급니다. 특히 native slave의 `Flow`는 물리적인 개별 출력으로
보장되지 않으므로 카드와 진단 센서 모두 `장비 보고 Flow`로 표시합니다.

### Legacy 리턴·도징 간단 카드

```yaml
type: custom:jebao-equipment-card
instance: main
topic_prefix: jebao-flow/main
title: 리턴 및 도징
```

카드는 서버가 노출한 리턴·도징 엔티티를 자동으로 찾습니다. exact-identity control 배포에서는
리턴펌프의 앱 대응 제어와 도징기의 전원, 채널별 수동/타이머/interval/시간표, calibration 제어가
노출됩니다. 도징 schedule은 채널마다 별도 request topic을 사용하고 optimistic state를 쓰지
않습니다. 물리 투여 단위와 장비 수용은 현장 실기로 별도 판정합니다.

## Current native discovery observer

Legacy card의 `runtime.dry_run` 배너는 current native-discovery 표면의 gate가 아닙니다. current
경로는 아래처럼 observer에서 control entity 자체를 만들지 않습니다.

`runtime.mode: observer`에서는 `--write-enabled`와 current qualification이 있어도 현재 데몬이
그룹·장비 제어 엔티티를 새로 projection·publish하거나 그 command topic을 subscribe하지
않습니다. `ha-schema`도 같은 mode를 존중합니다. public projection의
`control_surface_diagnostic_reason`은 `runtime_mode_observer`이고, 각 entity의 기존 capability·
qualification `diagnostic_reason`은 대체하지 않고 그대로 보존합니다. native diagnostic entity는
목표값 대신 실제 LAN 관찰값, 마지막 확인 시각, `TimerON`과 Auto 설정 단서를 표시합니다. 장비별 장비 보고
출력 sensor는 Home Assistant Recorder의 이력 그래프에서 기존 스케줄의 출력 변화를 확인하는 데
사용할 수 있습니다.

새로 projection하는 Home Assistant discovery object id·`unique_id`·device identifier는 exact
`topic_prefix`의 full SHA-256에서 유도한 stable instance namespace를 공통으로 사용합니다.
같은 instance의 device function·schedule slot은 같은 logical device로, group actual-output·switch·
member resume는 같은 logical group으로 계속 묶입니다. state·command·availability topic은 기존
exact `topic_prefix` 형식을 그대로 유지합니다.

MQTT 연결 때마다 현재 config·manifest·catalog·group·schedule topology에서 유도한 이 instance
namespace의 platform variant 중, 이번 projection이 publish하지 않는 discovery config만 exact
zero-byte/QoS 1/retained payload로 제거합니다. availability를 먼저 offline으로 만들고 command topic
구독과 current discovery publish 전에 삭제하며, state·command·availability와 current discovery topic은
삭제하지 않습니다. mode·qualification이 바뀌어도 같은 bounded variant 집합을 둘로 나누므로 이전
sensor·number·switch·select·button 형상이 함께 남는 것을 줄입니다.

이 cleanup은 complete retained-state migration이 아닙니다. 제거된 topology, 과거 function kind·
`discovery_prefix`, 다른 namespace, pre-namespace legacy config는 추측해 삭제하지 않으며 존재와
소유권을 `UNKNOWN`으로 둡니다. exact `topic_prefix`는 daemon마다 유일해야 하지만 code-enforced
namespace lease는 없어서, 이를 공유한 두 daemon은 서로의 current variant를 삭제할 수 있습니다.
pre-namespace legacy entity도 namespaced replacement와 같은 command topic을 가리켜 계속 조작 가능할
수 있으므로 **관측된 legacy record가 있는 배포**는 exact inventory·제거·복구 절차가 별도로
검토·승인되기 전에는 daemon을 이 schema로 재기동하지 않습니다. 2026-09-10 첫 배포 감사에서는
bounded broker record와 HA registry·recorder·config entry가 모두 0이고 과거 배포 이력도 없어 실제
migration 대상이 없었습니다. 이는 broker 부재 증명이 아니며, 이 한 observer activation에서만
legacy migration block이 적용되지 않는 근거입니다. Recorder history·device/entity customization·
area·dashboard reference가 새 identity로 자동 이전된다고 주장하지 않습니다.

## 기존 discovery 관측 inventory

`python -m jebao_flow.legacy_discovery_inventory_cli`는 pre-namespace discovery를 바로 지우지 않고
먼저 증거를 남기는 source-only 도구입니다. `capture`는 매 실행 random client id의 MQTT 3.1.1 clean
session을 만들고 will이나 publish 없이 `{discovery_prefix}/+/+/config` 하나만 구독합니다. 정해 둔
monotonic 관측 창에 실제로 전달된 메시지 중 retained·non-empty·exact 3-level
`jebao_flow_local_` config만 private artifact에 기록합니다. 프로토콜에는 retained replay 종료 표식이
없으므로 이 결과는 관측 창에 전달된 항목만 뜻하며, 보이지 않은 항목의 broker 부재를 뜻하지 않습니다.

artifact root는 미리 만든 owner-only `0700` 디렉터리여야 합니다. generation은 `0700`, 파일은
`0600`이고 exact topic·payload 원문은 private artifact 밖으로 출력하지 않습니다. `inspect`의 public
출력에는 opaque id·개수·UTC 구간·유계 분류·SHA-256 결속만 나옵니다. 같은 topic에서 서로 다른
retained payload가 관측되면 둘 다 보존하되 ambiguous·selection 불가로 표시합니다.

```text
python -m jebao_flow.legacy_discovery_inventory_cli capture \
  --config <private-config> --output-root <private-owner-only-root> \
  --window-seconds <bounded-seconds>

python -m jebao_flow.legacy_discovery_inventory_cli inspect \
  --config <private-config> --output-root <private-owner-only-root> \
  --inventory-id <opaque-inventory-id>
```

selection은 대화형 전체 선택이 아니라 maintainer가 작성한 `0600` exact digest 목록을 입력으로 받는
별도 offline command입니다. request의 inventory·current config·discovery/topic prefix 결속과 각
record의 opaque id·topic SHA-256·payload SHA-256이 모두 맞아야 owner-only selection artifact가
생깁니다. 이 artifact의 `mutation_authorized`는 `false`이며 삭제 승인이나 현재 retained 존재를
주장하지 않습니다. request JSON의 `schema_version`은 `1`, `request_kind`는
`maintainer_exact_observed_record_selection`이고, `bindings` 네 값과 `records`의 세 digest 값은
`inspect` 출력에서 옮깁니다. raw record를 private artifact에서 검토한 뒤 필요한 항목만 적으며
전체 선택 shortcut은 없습니다.

```text
python -m jebao_flow.legacy_discovery_inventory_cli select \
  --config <private-config> --output-root <private-owner-only-root> \
  --inventory-id <opaque-inventory-id> --request <private-selection-request>
```

source를 설치하거나 테스트하는 것만으로 broker에 연결하지 않습니다. 첫 `capture` 실행은 별도의
명시적 broker 접촉 결정이지만 Home Assistant·daemon·펌프 write가 전혀 없고 현장 물리 차단 조건을
소프트웨어 gate로 추가하지 않습니다. apply·restore는 아직 구현하지 않았으며 실제 artifact를 검토한
뒤 별도 단계로 설계합니다. bounded 재관측에서 보이지 않았다는 사실을 삭제 성공이나 broker 부재로
판정하지 않습니다.

장비가 로컬 시간표를 제공하면 별도 `장비 시간표` sensor가 만들어집니다. sensor 상태는
해석에 성공한 시간 구간 수이며, `enabled`, `slot_capacity`, `entries`, `invalid_slots` 속성을
제공합니다.
초마다 바뀌는 `device_local_time`은 Recorder 이력 증가를 막기 위해 의도적으로 노출하지
않습니다. native discovery entity는 해석하지 못한 슬롯을 진단으로 남기며 이 시간표는 현재
읽기 전용입니다. 위 legacy card의 접이식 표시는 current 배포 기능으로 주장하지 않습니다.

`topic_prefix`는 여러 데몬 인스턴스의 state·command topic을 구분하고, 그 exact 문자열의
full SHA-256 namespace가 Home Assistant discovery object id·`unique_id`·device identifier도 구분합니다.
따라서 같은 `discovery_prefix`와 논리 ID를 쓰더라도 `topic_prefix`가 다른 새 projection은
discovery identity를 공유하지 않습니다. 단, 이 보장은 pre-namespace retained config를 이전·삭제하지
않습니다. legacy card의 `instance`·`topic_prefix` 선택 규칙도 current native-discovery entity의
선택 규칙으로 사용하지 않습니다.
