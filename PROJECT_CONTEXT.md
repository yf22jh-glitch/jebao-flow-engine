# Jebao Flow Engine 개발 컨텍스트

## 목표

Jebao Flow Engine(`jebao-flowd`)은 제바오 수류모터와 리턴펌프를 로컬 네트워크에서 직접
제어하고, Home Assistant 커스텀 통합에 MQTT로 물리 장비와 논리 그룹을 제공합니다.

첫 MVP는 동일 모델 메인 수류모터 두 대와 바형 크로스플로우 한 대에 집중합니다.

1. 장비 검색, 연결, 전원과 출력 읽기/쓰기
2. MQTT 상태/명령 및 Home Assistant Discovery
3. 세 펌프 그룹과 펌프별 `role`/`gain`/`phase`
4. Constant, Sync, Anti Phase 패턴
5. 장비/그룹별 출력 제한과 명령 속도 제한
6. 급여 모드와 이전 상태 복원
7. 오프라인 감지와 `DEGRADED` 처리
8. Docker 기반 홈서버 배포

## 책임 경계

```text
Home Assistant
  UI, 자동화 조건, 패턴/출력/주기 선택, 경고 표시
       │ MQTT
       ▼
jebao-flowd
  desired state, 그룹/패턴, 안전 제한, 연결/복구, actual state
       │ local LAN
       ▼
Jebao devices
```

Home Assistant가 짧은 주기로 펌프별 출력을 계산하지 않습니다. 실제 장비에 명령하는
프로세스는 `jebao-flowd` 하나이며 기존 직접 제어 통합과 동시에 운용하지 않습니다.
커스텀 통합과 Lovelace 카드는 장비 LAN 주소나 Jebao/Gizwits 프로토콜을 알지 못하며,
Home Assistant 엔티티와 데몬의 MQTT 계약만 사용합니다.

## 설계 불변 조건

- 프로토콜 구현과 패턴 계산을 직접 결합하지 않습니다.
- Desired State와 Actual State를 분리합니다.
- 빠른 파형은 장비 내장 mode/frequency를 사용합니다.
- 장비별 최소 명령 간격을 적용하고 동일 값은 재전송하지 않습니다.
- 모든 값은 그룹과 물리 장비 제한을 모두 통과합니다.
- 연결 복구 직후 최대 출력으로 점프하지 않고 램프를 적용합니다.
- MQTT 연결 중단만으로 운전을 멈추지 않습니다.
- 설정과 외부 명령은 엄격하게 검증합니다.
- 인증정보는 환경 변수나 secret으로 주입하고 로그/저장 상태에 남기지 않습니다.
- 그룹 소속 펌프를 개별 조작하면 `manual_override`로 전환하고 명시적으로 그룹에 복귀시킵니다.

## 현재 안전 단계: Observer

실제 write 실험 전에는 `runtime.mode: observer`만 사용합니다. 이 모드에서는 MQTT 명령을
거부하고, 장비 설정에 write 허용값이 잘못 들어 있어도 observer 전용 factory가 하드웨어
write gate를 강제로 닫습니다. 장비는 공개 설정의 product key나 검색 순서가 아니라 비공개
설정의 정확한 Gizwits device ID/MAC으로만 논리 장비에 매핑합니다.

Observer는 5초 기본 주기로 실제 전원·출력·모드·주파수와 확인된 Timer/Auto 설정 단서를
읽어 MQTT와 로컬 JSONL 변경 기록에 반영합니다. 이 값으로 기존 스케줄의 결과와 변경 시간은
추적할 수 있지만, 명령 출처는 프로토콜에 없으므로 앱·클라우드·장비 자체 중 하나로 단정하지
않고 `external_or_native`로 기록합니다.

## 상태 모델

그룹 상태는 `STOPPED`, `STARTING`, `RUNNING`, `FEEDING`, `MAINTENANCE`, `DEGRADED`,
`ERROR`, `EMERGENCY_STOP` 중 하나입니다. 비상 정지는 명시적인 사용자 동작으로만
해제합니다.

멤버 오프라인 기본 정책은 `continue_limited`이며 남은 장비의 최대 출력을 별도로
제한합니다. 이후 `stop_group`, `continue`, `fallback_constant`도 지원합니다.

## 패턴 계산 규약

- `period_seconds`는 한 번의 완전한 파형 주기입니다.
- Constant는 기준 출력에 멤버 `gain`을 곱합니다.
- Sync는 그룹 최소/최대 출력을 같은 위상으로 교대합니다.
- Anti Phase는 멤버의 `phase`를 적용해 최소/최대 출력을 교대합니다.
- `phase=90`은 기준 파형보다 1/4주기 늦고, `phase=180`은 반대 위상입니다.
- `gain` 적용 후 그룹 제한으로 자르고 반올림합니다.
- 물리 장비 제한은 장비 명령 직전 안전 계층에서 한 번 더 적용합니다.

## 개발 순서

1. 프로젝트/모델/인터페이스/시뮬레이터/기본 패턴
2. 실제 보유 장비 Capability 조사와 LAN 프로토콜 드라이버
3. MQTT 및 Home Assistant Discovery
4. 그룹 런타임, 급여 모드, 장애 정책과 영속화
5. Docker 운영 안정화 및 Home Assistant 애드온
6. Sine Envelope, Random Reef, Tide와 다중 그룹

실제 LAN 드라이버를 구현하기 전 모델명, 펌웨어, product key, 속성 스키마, 출력 범위와
단계, push 지원, 재부팅 동작, 동시 로컬 클라이언트 지원 여부를 장비별 Capability 문서로
확정해야 합니다.
