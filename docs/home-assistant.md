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
`그룹 복귀` 또는 카드의 `전체 그룹 복귀`로 다시 패턴에 합류시킵니다. 그룹 전원 ON/OFF와
비상 정지는 명시적인 전체 명령이므로 모든 멤버에 적용됩니다.

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

`runtime.mode: observer`에서는 잠금 배너만 표시하는 것이 아니라 그룹·장비의 모든 제어
엔티티를 생성하지 않습니다. 카드는 목표값 대신 실제 LAN 관찰값, 마지막 확인 시각,
`TimerON`과 Auto 설정 단서를 표시합니다. 장비별 실제 출력 sensor는 Home Assistant Recorder의
이력 그래프에서 기존 스케줄의 출력 변화를 확인하는 데 사용할 수 있습니다.

장비가 로컬 시간표를 제공하면 별도 `장비 시간표` sensor가 만들어집니다. sensor 상태는
해석에 성공한 시간 구간 수이며, `enabled`, `slot_capacity`, `entries`, `invalid_slots` 속성을
제공합니다.
초마다 바뀌는 `device_local_time`은 Recorder 이력 증가를 막기 위해 의도적으로 노출하지
않습니다. 메인 수류 카드와 리턴·도징 카드 모두 제어·관찰 모드에서 시간표를 접이식으로
표시하며, 해석하지 못한 슬롯은 경고만 보여 줍니다. 이 시간표는 현재 읽기 전용입니다.

여러 데몬 인스턴스에서 같은 논리 ID를 사용하는 경우 `topic_prefix`가 Home Assistant config
entry까지 구분해 같은 데몬의 그룹·장비 엔티티만 묶습니다. 같은 `instance`가 둘 이상인데
`topic_prefix`를 생략하면 카드는 임의 선택하지 않고 명시적인 선택을 요청합니다.
