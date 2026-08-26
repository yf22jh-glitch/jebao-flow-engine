# 읽기 전용 Observer 모드

실제 수조에 제어 명령을 보내기 전에 기존 Jebao 앱·컨트롤러·자동화가 만든 변화를 장기간
관찰하기 위한 안전 모드입니다. `runtime.mode: observer`에서는 다음 불변 조건을 적용합니다.

- MQTT 그룹·장비 명령을 `observer_mode_read_only`로 거부
- 설정의 `allow_hardware_writes` 값과 관계없이 LAN 어댑터의 write gate를 강제로 닫음
- Home Assistant에 switch·number·select·button 제어 엔티티를 만들지 않음
- 장비별 독립 연결과 주기적 상태 read만 수행
- MQTT나 Home Assistant가 끊겨도 LAN 관찰과 로컬 변경 기록을 계속 수행

## 장비 바인딩

동일한 제품키를 쓰는 펌프가 여러 대 있으므로 제품키, 장비 종류, 검색 순서 또는 현재 IP로
좌·우 펌프를 추측하지 않습니다. 실제 식별자는 공개 저장소가 아닌 홈서버의 `config.yaml`에만
넣습니다.

```yaml
devices:
  - id: wavemaker_left
    name: 왼쪽 메인 수류모터
    type: wavemaker
    discovery: auto
    identity:
      device_id: replace_with_gizwits_device_id
      mac_address: replace_with_12_hex_mac
```

`device_id`와 MAC을 모두 지정하면 검색 응답에서 두 값이 함께 일치해야 연결합니다. 매핑되지
않거나 모순되는 장비는 `unmapped`로 표시하며 다른 논리 장비에 임의 할당하지 않습니다. 실제
식별자가 들어 있는 `config.yaml`과 `data/`는 `.gitignore`에 포함되어 있습니다.

정기 검색에서 stable identity를 확인하지 못하면 이전 IP를 다른 장비로 오인하지 않도록 기존
worker도 보수적으로 종료합니다. 일시적인 UDP 누락이면 다음 검색에서 같은 identity가 확인되는
즉시 다시 연결되며, DHCP로 IP가 바뀌어도 config의 과거 주소가 아니라 identity를 기준으로 새
worker를 만듭니다.

격리된 IoT VLAN에서 브로드캐스트가 전달되지 않으면 해당 VLAN의 directed broadcast 주소를
설정합니다.

```yaml
observer:
  targets:
    - 192.168.20.255
  poll_interval_seconds: 5
  publish_heartbeat_seconds: 300
  rediscovery_interval_seconds: 30
```

## 기록되는 값

성공한 각 LAN read는 MQTT 장비 상태의 `last_seen_at`을 갱신합니다. 다음 실제 운전값이 바뀌면
revision과 `last_changed_at`을 갱신합니다.

데몬 시작 뒤 첫 성공 read는 비교 기준선(`first_seen`)으로만 기록하며 변경 시각으로 간주하지
않습니다. 따라서 “언제 바뀌었는지”는 Observer가 연속으로 관찰한 두 상태 사이에서만 판정합니다.

- 전원 상태
- 출력
- 운전 모드
- 주파수

모델 스키마에서 이미 확인된 다음 설정 단서가 바뀌면 `last_configuration_changed_at`을 별도로
갱신합니다.

- `TimerON`
- `AutoMode`, `AutoFlow`, `AutoFreq`
- `FeedSwitch`, `FeedTime`
- `Linkage`, `PulseTide`
- 도징 채널·타이머의 확인된 일부 필드
- 지원 펌프의 48슬롯 로컬 시간표: 시작·종료 시각, 모드와 제품별 출력·주파수·급여 값

변경·오프라인·복구 이벤트는 기본적으로 `/data/observations.jsonl`에 권한 `0600`으로
기록됩니다. 레코드에는 논리 장비 ID와 해석된 상태만 포함하며 MAC, vendor device ID,
passcode, raw frame은 넣지 않습니다.

동일한 값은 내부에서 계속 5초마다 확인하지만 MQTT/HA에는 기본 300초마다 freshness만
재게시합니다. 실제 값·설정 단서·연결·오류 변화는 즉시 게시합니다. 이 제한은 Home Assistant
Recorder에 동일 상태가 과도하게 누적되는 것을 막습니다.

## 알 수 있는 것과 알 수 없는 것

Observer는 “두 번의 poll 사이에 실제 상태 또는 확인된 설정 단서가 바뀌었다”는 사실을
기록합니다. LAN 상태에는 명령 주체가 없으므로 Jebao 앱, 클라우드, 장비 자체 타이머 또는 다른
LAN 클라이언트 중 누가 바꿨는지는 확정할 수 없습니다. 그래서 변경 출처는
`external_or_native`로 표시합니다.

DC Pump Pro, Local Wavemaker, Local Wavemaker Pro, Aquarium Pump의 제품별 슬롯 배치를
구분해 전체 로컬 시간표를 디코딩합니다. 2026-08-26 DC Pump Pro의 Constant Flow 종료
시각을 `08:00`에서 `08:01`로 한 항목만 바꾼 A/B 실측에서, 첫 슬롯 종료와 다음 Feed 슬롯
시작이 함께 `08:01`로 이동한 raw 상태를 확인했습니다. 빈 슬롯은 `00`/`EE` sentinel로
구분하고, 잘못된 시각이나 미확인 모드가 든 슬롯은 추측하지 않고 `invalid_slots`로 표시합니다.

도징 펌프의 4채널 96바이트 시간표는 내부 레코드 배치가 아직 검증되지 않아 현재 디코딩하지
않습니다. 또한 시간표를 읽어도 앱이 보낸 클라우드 요청 자체나 명령 주체는 알 수 없습니다.
장비 시계 변화는 슬롯 변경으로 취급하지 않으며, 시간표 쓰기 기능도 아직 제공하지 않습니다.

## Wi-Fi 재연결과 재등록

신호 단절, 공유기 재부팅 또는 DHCP 주소 변경은 장비가 기존 Wi-Fi에 다시 연결한 뒤 Observer가
stable identity로 새 주소를 찾아 자동 복구합니다. 이 경우 Jebao 앱 재등록은 필요하지 않습니다.

공장 초기화, SSID·비밀번호 변경 또는 장비의 Wi-Fi 설정 소실은 LAN 밖에서 발생하므로 현재
데몬이 복구할 수 없습니다. Jebao 앱은 삭제하지 않고 provisioning·비상복구 용도로 유지합니다.

1. `jebao-flowd`를 중지해 단일 제어권을 보장
2. Jebao 앱의 AP 절차로 장비를 기존 IoT SSID에 다시 등록
3. `jebao-flowctl discover`로 device ID와 MAC 확인
4. private `config.yaml`의 identity가 그대로인지 확인하고 `probe --decode` 실행
5. Observer를 다시 시작하고 Home Assistant의 actual 상태 확인

IoT SSID와 비밀번호를 안정적으로 유지하고 DHCP 예약을 사용하면 복구 빈도를 줄일 수 있습니다.
로컬 AP provisioning 도구는 프로토콜과 credential 저장 방식을 별도 검증한 뒤 후속 단계에서만
검토합니다.

## Home Assistant 표시

Observer 모드의 커스텀 통합은 장비별로 다음을 제공합니다.

- 실제 출력 sensor
- 실제 모드 sensor
- 실제 운전 binary sensor
- 연결 binary sensor
- 장비 오류 binary sensor
- 장비 시간표 sensor: 활성 슬롯 수와 전체 `entries`, `invalid_slots`
- 상태 sensor의 마지막 확인·실제 변경·설정 단서 변경 시각

전용 카드는 실제 출력과 연결 상태를 우선 표시하고 모든 조작 컨트롤을 제거합니다. 여러
`jebao-flowd` 인스턴스가 있으면 카드 설정에 `instance`를 지정할 수 있습니다.

```yaml
type: custom:jebao-flow-card
instance: main
topic_prefix: jebao-flow/main
group: main_flow
title: 메인 수류 관찰
```
