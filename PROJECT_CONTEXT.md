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
- 네이티브 master/slave 시험은 일반 그룹 패턴과 분리한 bounded transaction으로 실행하고,
  첫 write 전 snapshot journal을 저장하며 모든 종료 경로에서 원복합니다.
- 운영 그룹은 각 펌프를 `independent`로 두고 소프트웨어가 멤버별 `gain`/`phase`를 계산하는
  방식을 기본으로 합니다. 네이티브 Sync/Async는 동일 Pro 두 대의 장비 내부 Linkage를 나타내는
  별도 페어이며, 바형 펌프는 독립 보조 멤버로 남습니다.
- 네이티브 slave의 개별 `Flow`는 지원되는 것으로 가정하지 않습니다. 이를 사용하는 UI와
  명령 계약은 현장 qualification 전까지 잠그고, 개별 출력이 필요하면 독립 소프트웨어 그룹을
  사용합니다.

## 현재 안전 단계: Observer

실제 write 실험 전에는 `runtime.mode: observer`만 사용합니다. 이 모드에서는 MQTT 명령을
거부하고, 장비 설정에 write 허용값이 잘못 들어 있어도 observer 전용 factory가 하드웨어
write gate를 강제로 닫습니다. 장비는 공개 설정의 product key나 검색 순서가 아니라 비공개
설정의 정확한 Gizwits device ID/MAC으로만 논리 장비에 매핑합니다.

Observer는 5초 기본 주기로 실제 전원·출력·모드·주파수와 확인된 Timer/Auto 설정 단서,
지원 제품군의 48슬롯 장비 내장 시간표를 읽어 MQTT와 로컬 JSONL 변경 기록에 반영합니다.
시간표 슬롯의 시각·모드·파라미터 변화는 추적하지만, 명령 출처는 프로토콜에 없으므로
앱·클라우드·장비 자체 중 하나로 단정하지 않고 `external_or_native`로 기록합니다.

네이티브 Linkage 트랜잭션 코어와 제한된 현장 실행·attended recovery까지 완료됐습니다.
현재 데몬에는 실제 actuator가 없으므로 `control`로 설정해도 MQTT 명령을 fail-closed로
거부하며, Sync/Async 시험 API를 Home Assistant에 광고하지 않습니다. 실기 write 검증과
startup recovery wiring이 끝나기 전에는 이 경계를 해제하지 않습니다.
2026-08-27 Async 장기 시험은 슬레이브 출력 변경 시점 직후 안전 실패했고 자동 exact restore가
완료되지 않아 영수증 0/2 상태로 끝났습니다. 새 토큰의 attended recovery는 성공했으며 Observer가
시험 전 TimerON 상태를 다시 확인했습니다. 이 결과는 Linkage 레지스터 관계 자체를 부정하지 않지만,
slave별 gain/출력 지원을 입증하지도 않습니다.
후속 저출력 Sync 세 건도 영수증 0/2였습니다. 마지막 실행은 TimerON convergence 수정 뒤에도
slave detach에서 자동 rollback이 실패했지만, attended recovery와 Observer 확인으로 두 장비를
시험 전 상태로 복원했습니다. 현재 코드는 rollback 첫 reconciliation보다 앞서 slave→master 새
인증 세션을 강제하고 refresh/detach 실패 시 fresh 안전 fallback을 사용하도록 수정됐으며 테스트
571개를 통과했습니다. 수정 배포 뒤 저출력 Sync 31%/31%는 자동 exact restore와 qualification
영수증 2/2까지 성공했습니다. 그러나 후속 Async 35%/33%→slave 38% 진단은 자동 rollback이
실패했고, attended recovery와 Observer 교차 확인으로만 원복을 완료했습니다. typed primary
failure가 없어 Async 독립 출력은 여전히 미검증이며 네이티브 Linkage 잠금은 유지합니다.
2026-08-28의 TimerON Constant→Sine 단일 시험은 두 역할 적용까지 진행했지만, `async_slave`
적용 직후 슬레이브 manual Frequency가 여러 fresh explicit reply에서 원 snapshot과 다르게 유지되어
A→B 관찰 전에 fail-closed로 종료됐습니다. 자동 rollback과 두 번의 새 연결 control/schedule
비교는 exact였고 Observer도 복귀했습니다. 이 결과는 역할 유발 Frequency side effect의 증거이지
슬롯별 slave `AutoFlow` 전환 증거가 아닙니다. 후속 코드는 token-bound Constant/Sine 값 중 하나가
서로 다른 새 세션에서 2회 연속 같을 때만 그 값을 run-local로 고정하며 Frequency write는 보내지
않습니다. 실기 재검증 전까지 네이티브 Linkage 잠금은 그대로 유지합니다.
같은 날 `da62b73`으로 새 `_08`을 한 번 실행했습니다. write 없는 preflight는 통과했고 임시
Constant 31%/32% → Sine 35%/40% 계획도 staged됐지만, native 역할 실행·Linkage write와 A→B
관찰 전에 `role_preflight`에서 fail-closed로 끝났습니다. 자동 outer rollback은 완료됐고
outer-control·temporary-schedule·role journal은 모두 `none`이었습니다. 서로 독립적인 fresh
read-only 확인 두 번에서 원래 controls와 두 장비의 완전한 432-byte schedule image가 exact임을
확인했습니다. private 설정은 `dry_run: true`이고 Observer와 recovery 서비스도 정상 복귀했습니다.
이 실행은 재시도하지 않았으며, slave의 슬롯별 `AutoFlow` 적용 여부는 계속 미검증입니다.
실제 wiring에서는 fail-closed Linkage safety interlock을 비상정지·정비 latch와 공유해야
하며, journal lease를 획득하지 못한 다른 daemon은 시험이나 startup recovery를 실행하지
않습니다.

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
