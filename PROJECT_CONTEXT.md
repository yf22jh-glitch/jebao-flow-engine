# Jebao Flow Engine 개발 컨텍스트

## 목표

Jebao Flow Engine은 제바오 수류모터와 리턴펌프를 로컬 네트워크에서 직접 제어하고,
Home Assistant에 MQTT로 물리 장비와 논리 그룹을 제공합니다. 현재 배포 경로는
`jebao-flow-local`의 native MQTT Discovery입니다. `jebao-flowd` aggregate MQTT와
`custom_components/jebao_flow`는 동결된 legacy 경로이며 현재 경로와 함께 실행하지 않습니다.

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
       │ native MQTT Discovery / state / command
       ▼
jebao-flow-local
  desired state, 그룹/패턴, 안전 제한, 연결/복구, actual state
       │ local LAN
       ▼
Jebao devices
```

Home Assistant가 짧은 주기로 펌프별 출력을 계산하지 않습니다. 실제 장비에 명령하는
프로세스는 current 배포의 `jebao-flow-local` 하나이며 기존 daemon·직접 제어 통합과 동시에
운용하지 않습니다. Home Assistant의 built-in MQTT integration은 장비 LAN 주소나
Jebao/Gizwits 프로토콜을 알지 못하고 native discovery entity와 데몬의 MQTT 계약만 사용합니다.

## 설계 불변 조건

- 프로토콜 구현과 패턴 계산을 직접 결합하지 않습니다.
- Desired State와 Actual State를 분리합니다.
- 빠른 파형은 장비 내장 mode/frequency를 사용합니다.
- 장비별 최소 명령 간격을 적용하고 동일 값은 재전송하지 않습니다.
- 그룹 실행기는 장비별 단일 worker와 한 칸 pending queue를 사용하고, 멤버별 control epoch와
  전체 target이 모두 같을 때만 중복으로 판단합니다.
- 전송 전 실패가 증명된 경우만 평상시 명시적으로 재시도합니다. 종료 중 이미 승인된 OFF에
  한해 한 번의 제한 재시도를 허용합니다. ACK timeout처럼 실제 적용 여부가 불명확한 실패는
  자동·수동 재전송을 모두 막고 Actual State 재조정을 요구합니다.
- 중복 제거되지 않은 canonical OFF는 장비별 safety barrier를 소유합니다. 새 일반 명령과 세대
  변경은 이를 취소할 수 없고, 데몬 종료 시에도 배수하며 미확정 결과는 오류로 노출합니다.
- 모든 값은 그룹과 물리 장비 제한을 모두 통과합니다.
- 연결 복구 직후 최대 출력으로 점프하지 않고 램프를 적용합니다.
- MQTT 연결 중단만으로 운전을 멈추지 않습니다.
- 설정과 외부 명령은 엄격하게 검증합니다.
- 인증정보는 환경 변수나 secret으로 주입하고 로그/저장 상태에 남기지 않습니다.
- 그룹 소속 펌프를 개별 조작하면 `manual_override`로 전환하고 명시적으로 그룹에 복귀시킵니다.
- 네이티브 master/slave 시험은 일반 그룹 패턴과 분리한 bounded transaction으로 실행하고,
  첫 write 전 snapshot journal을 저장하며 모든 종료 경로에서 원복합니다.
- 운영 그룹은 각 펌프를 `independent`로 두고 소프트웨어가 멤버별 `gain`/`phase`를 계산하는
  방식을 기본으로 합니다. 네이티브 Sync/Async는 동일 Pro 두 대의 장비 내부 Linkage를 나타내는
  별도 페어이며, 바형 펌프는 독립 보조 멤버로 남습니다.
- 네이티브 slave의 개별 `Flow` 의미는 확정된 것으로 가정하지 않습니다. direct Pro 제어와
  native API는 exact identity로 노출하지만, per-slot extension은 명시적 topology가 있을 때만
  entity를 만들며 Q1/Q2의 답으로 간주하지 않습니다.

## 현재 배포 단계: Exact-identity control

현재 운영 배포 상태와 검증 범위는
[2026-09-17 전 기종 exact-identity 제어 활성화](docs/runs/2026-09-17-exact-identity-all-device-activation.md)를
기준으로 합니다. 과거 Observer·native Linkage 실행과 미확정 질문은
[append-only 실행 기록](docs/runs/README.md)에 보존합니다.

## 상태 모델

그룹 상태는 `STOPPED`, `STARTING`, `RUNNING`, `FEEDING`, `MAINTENANCE`, `DEGRADED`,
`ERROR`, `EMERGENCY_STOP` 중 하나입니다. 비상 정지는 명시적인 사용자 동작으로만
해제합니다.

`STOPPED`와 `EMERGENCY_STOP`은 파형 계산과 무관하게 모든 멤버의 canonical OFF 목표를
생성합니다. 사용자가 요청한 `STARTING`과 `MAINTENANCE`는 hold 상태로서 일반 운전 write를
만들지 않습니다. 가용성 판정으로 파생된 `STARTING`에서는 설정상 비활성 멤버의 canonical OFF만
허용합니다. `STOP_GROUP` 장애 정책도 파형 계산을 거치지 않고 온라인 멤버의 OFF 목표를 만듭니다.

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
