# Home Assistant 연동

## 제어 경계

```text
Lovelace card
    │ Home Assistant entity service
    ▼
custom_components/jebao_flow
    │ MQTT
    ▼
jebao-flowd
    │ Jebao LAN protocol
    ▼
physical pumps
```

커스텀 통합과 카드는 펌프 IP, MAC, product key 또는 Gizwits/Jebao LAN 프로토콜을 알지
못합니다. 그룹 계산과 개별 장비 제어권은 항상 `jebao-flowd`에 있습니다.

## 설치

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

## 메인 3대 수류 그룹 카드

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

## 리턴·도징 간단 카드

```yaml
type: custom:jebao-equipment-card
instance: main
topic_prefix: jebao-flow/main
title: 리턴 및 도징
```

카드는 서버가 노출한 리턴·도징 엔티티를 자동으로 찾습니다. 리턴펌프는 단순 ON/OFF와 출력,
도징기는 상태 중심으로 표시합니다. 도징 동작은 실제 모델의 용량·채널·보정 단위를 검증한 뒤에만
쓰기 기능을 열며, 현재 안전 계약에서는 읽기 전용입니다.

## 쓰기 잠금

`runtime.dry_run: true`이거나 장비의 `allow_hardware_writes: false`이면 카드 상단에 하드웨어
쓰기 잠금 배너가 표시됩니다. 이때 Home Assistant 명령과 서버의 패턴 계산은 확인할 수 있지만
실제 장비 write는 허용되지 않습니다.

`runtime.mode: observer`에서는 `--write-enabled`와 current qualification이 있어도 현재 데몬이
그룹·장비 제어 엔티티를 새로 projection·publish하거나 그 command topic을 subscribe하지
않습니다. `ha-schema`도 같은 mode를 존중합니다. public projection의
`control_surface_diagnostic_reason`은 `runtime_mode_observer`이고, 각 entity의 기존 capability·
qualification `diagnostic_reason`은 대체하지 않고 그대로 보존합니다. 카드는 목표값 대신 실제 LAN
관찰값, 마지막 확인 시각, `TimerON`과 Auto 설정 단서를 표시합니다. 장비별 장비 보고
출력 sensor는 Home Assistant Recorder의 이력 그래프에서 기존 스케줄의 출력 변화를 확인하는 데
사용할 수 있습니다.

새로 projection하는 Home Assistant discovery object id·`unique_id`·device identifier는 exact
`topic_prefix`의 full SHA-256에서 유도한 stable instance namespace를 공통으로 사용합니다.
같은 instance의 device function·schedule slot은 같은 logical device로, group actual-output·switch·
member resume는 같은 logical group으로 계속 묶입니다. state·command·availability topic은 기존
exact `topic_prefix` 형식을 그대로 유지합니다.

이 identity 변경은 retained-state migration이 아니며 empty retained deletion payload를 보내지 않습니다.
pre-namespace legacy config가 broker·Home Assistant에 남아 있는지와 그 소유권은 `UNKNOWN`입니다.
control mode에서 legacy entity와 namespaced replacement는 같은 command topic을 가리켜 둘 다
조작 가능할 수 있으므로, 소유 namespace 내 variant cleanup과 legacy exact inventory·제거·복구
절차가 별도로 검토·승인되기 전에는 실제 daemon을 이 schema로 재기동하지 않습니다.
pre-namespace legacy config는 자동 삭제하지 않고, Recorder history·device/entity customization·area·
dashboard reference가 새 identity로 자동 이전된다고 주장하지 않습니다.

장비가 로컬 시간표를 제공하면 별도 `장비 시간표` sensor가 만들어집니다. sensor 상태는
해석에 성공한 시간 구간 수이며, `enabled`, `slot_capacity`, `entries`, `invalid_slots` 속성을
제공합니다.
초마다 바뀌는 `device_local_time`은 Recorder 이력 증가를 막기 위해 의도적으로 노출하지
않습니다. 메인 수류 카드와 리턴·도징 카드 모두 제어·관찰 모드에서 시간표를 접이식으로
표시하며, 해석하지 못한 슬롯은 경고만 보여 줍니다. 이 시간표는 현재 읽기 전용입니다.

`topic_prefix`는 여러 데몬 인스턴스의 state·command topic을 구분하고, 그 exact 문자열의
full SHA-256 namespace가 Home Assistant discovery object id·`unique_id`·device identifier도 구분합니다.
따라서 같은 `discovery_prefix`와 논리 ID를 쓰더라도 `topic_prefix`가 다른 새 projection은
discovery identity를 공유하지 않습니다. 단, 이 보장은 pre-namespace retained config를 이전·삭제하지
않습니다. 같은 `instance`가 둘 이상인데 `topic_prefix`를 생략하면 카드는 임의 선택하지 않고
명시적인 선택을 요청합니다.
