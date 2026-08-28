# Jebao Flow Engine

Jebao Flow Engine은 제바오 수류모터와 리턴펌프를 클라우드 없이 로컬에서 제어하고,
여러 펌프를 하나의 논리적인 수류 그룹으로 운전하기 위한 프로젝트입니다. 실행 데몬의
이름은 `jebao-flowd`입니다.

> 현재 상태: 실제 장비의 읽기 전용 검증과 지속 Observer, 오프라인 control 프레임 생성,
> MQTT 상태 계약과 Home Assistant 커스텀 통합 뼈대까지 구현했습니다. 기본 실행 모드는
> `observer`이며 모든 제어 명령과 실제 write를 거부합니다. 현장 전용 경로에서는 Pro 두 대의
> 제한된 저출력 Sync와 자동 exact restore가 성공해 qualification 영수증 2/2를 발급했습니다.
> 다만 Async 슬레이브 출력 변경과 TimerON 슬롯별 출력 전환은 아직 성공으로 인정할 수 없습니다.
> 2026-08-28의 첫 단일 시험은 `master` 적용 뒤 `async_slave`까지 도달했지만, 역할 적용 시
> 슬레이브의 manual Frequency가 지속적으로 바뀌는 펌웨어 동작을 명시적 응답에서 확인하고 A→B
> 시간표 경계 전에 안전 중단했습니다. 자동 rollback, 두 번의 새 연결 원상태 비교, Observer 복귀는
> 모두 성공했습니다. 이어 `da62b73`으로 실행한 새 `_08`은 write 없는 preflight를 통과하고 임시
> Constant 31%/32% → Sine 35%/40% 계획을 staged했지만, native 역할 실행과 Linkage write 및
> A→B 관찰 전 `role_preflight`에서 fail-closed로 종료됐습니다. 자동 outer rollback 완료 뒤 세
> journal은 모두 `none`이었고, 서로 독립적인 두 번의 fresh read-only 확인에서 원래 controls와
> 두 장비의 전체 432-byte schedule image가 정확히 일치했습니다. 설정은 `dry_run: true`로 다시
> 잠겼고 Observer와 recovery 서비스가 정상 복귀했으며 `_08`은 재실행하지 않았습니다. 따라서 핵심
> slave 슬롯별 `AutoFlow` 질문은 계속 미검증입니다. 후속 `_09`는 같은 임시 저출력 계획의
> preflight·schedule stage·TimerON arm·role preflight까지 완료했지만, role run의 첫 fresh capture가
> 약 0.43초 만에 실패해 역할 journal·Linkage write·A→B 관찰 전에 종료됐습니다. 자동 outer
> rollback 뒤 세 journal은 `none`이었고, 독립 세션 두 번에서 원 controls와 두 432-byte schedule
> image의 exact 일치를 확인했습니다. 실패 원인은 당시 generic `fresh_capture`보다 좁혀지지 않았으며
> slave 슬롯별 `AutoFlow` 질문도 여전히 미검증입니다. 네이티브 Linkage는 운영 기능으로 잠겨 있고
> 일반 데몬·MQTT·HA에 시험 기능을 노출하지 않습니다.

## 구조

```text
Home Assistant ── MQTT ──> jebao-flowd ── Jebao LAN protocol ──> pumps
                            ├─ group/pattern engine
                            ├─ safety controller
                            └─ desired/actual state
```

Home Assistant는 UI와 고수준 자동화를 담당하고, `jebao-flowd`는 연결 유지, 출력 계산,
명령 속도 제한과 장애 처리를 담당합니다. 장비를 직접 제어하는 주체는 `jebao-flowd`
하나로 제한합니다.

기본 그룹 전략은 세 펌프를 모두 장비 `independent` 상태로 두는
`software_independent`입니다. 그래서 두 Pro와 바형 보조에 각각 gain/phase/출력을 적용할 수
있습니다. 장비의 native Sync/Async는 동일 Pro 두 대의 장비 내부 Linkage용 별도 페어이며,
소프트웨어 `Sync`/`Anti Phase` 패턴과 같은 기능으로 취급하지 않습니다. 현재 native 페어는
실기 자격 미충족으로 잠겨 있습니다.

`software_independent`용 순수 tick planner와 protocol-neutral 비동기 dispatcher는 구현되어
있습니다. 다만 아직 MQTT·앱·실제 LAN port에 연결하지 않았으므로 데몬의
`command_executor_ready`는 계속 `false`이고 Observer/control fail-closed 경계도 유지됩니다.
불명확한 write 이후 barrier를 해제할 Actual State reconciler도 아직 연결하지 않았습니다.

## 현재 포함된 것

- Pydantic 기반 YAML 설정 모델과 교차 검증
- 프로토콜 계층과 장비 계층 사이의 추상 인터페이스
- Gizwits GAgent 프레임 코덱, 인증 세션과 UDP 장비 검색
- 여러 장비의 인증/raw 상태를 쓰기 없이 확인하는 `jebao-flowctl probe`
- 안정적인 device ID/MAC 바인딩, 장비별 재접속과 5초 poll을 수행하는 읽기 전용 Observer
- 실제 상태, 타이머·Auto 설정 단서와 장비 내장 시간표의 읽기 전용 디코딩 및 안전한
  JSONL 변경 기록
- 보유 제품군 5종의 상태 디코더와 fault 판독
- 전역·장비별 이중 잠금, 출력 제한, 명령 간격과 read-back 검증을 갖춘 LAN 어댑터
- Pro 수류모터의 `master`/`sync_slave`/`async_slave`, `TimerON` typed read/write와
  장비당 원자적 목표 프레임 생성
- 첫 write 전 영속 저널, 별도 마스터/슬레이브 출력 요청, 수동·시간초과·오류·취소 원복,
  재시작 복구를 갖춘 임시 네이티브 Linkage 트랜잭션 코어
- 한 대씩 `동일값 → 1~5%p 하향 → 정확한 원복`을 검증하는 최초 write 자격 절차와 24시간
  물리 장비 바인딩 영수증
- 모든 물리 write workflow가 공유하는 `/hardware-safety` 전역 lease, 비상정지 latch와
  recovery-only supervisor
- TimerON을 유지한 채 `Linkage` 역할만 변경하고 장비별 `Auto*` 컨트롤러 레지스터의
  시간표 경계 전환을 검증하는
  [schedule-linkage 진단](docs/schedule-linkage.md)
- 비동기 가상 장비 시뮬레이터
- Constant, Sync, Anti Phase 및 VorTech 계열에서 영감을 얻은 그룹 패턴 계산기
- 3대 수류모터의 `left`/`right`/`crossflow` 역할과 개별 `gain`/`phase`
- 단일 monotonic 시각에서 세 멤버의 목표를 계산하고 오프라인 정책, 장비별 출력 범위와 절대
  step, 수동 override와 패턴 epoch를 적용하는 write-free 그룹 planner
- 장비별 worker와 한 칸 pending queue로 latest-wins를 보장하고, 멤버별 control epoch와 전체
  target으로 중복을 억제하며, 실패를 장비별로 격리하는 protocol-neutral dispatcher
- 이미 승인된 canonical OFF는 새 일반 명령이나 세대 변경으로 취소하지 않고, 종료 중에도
  끝까지 배수합니다. 전송 전 실패만 한 번 제한적으로 재시도하고 결과가 끝내 불명확하면 정상
  종료로 숨기지 않습니다.
- 그룹 제어와 개별 제어를 함께 지원하는 명시적 `manual_override` 상태
- 그룹 및 장비 출력 제한
- 버전이 있는 MQTT 그룹·장비 상태/명령 계약과 중복 요청 방지
- MQTT만 사용하는 Home Assistant 커스텀 통합 및 전용 Lovelace 카드
- Docker/Compose 실행 뼈대
- 단위 테스트와 GitHub Actions CI

실제 장비 6대의 읽기 전용 검증 결과와 제품군별 Capability는
[검증된 장비 카탈로그](docs/devices/README.md)에 정리했습니다. 제한 실기 절차는
[실기 제어 안전 체크리스트](docs/pre-hardware-test-checklist.md), VorTech 모드 대응은
[VorTech 계열 패턴](docs/vortech-inspired-modes.md)을 참고하세요.
Home Assistant 설치와 카드는
[Home Assistant 연동 가이드](docs/home-assistant.md)를 참고하세요.
기존 스케줄을 write 없이 관찰하는 방법과 한계는
[Observer 모드](docs/observer-mode.md)를 참고하세요.
네이티브 Sync/Async 시험의 경계와 원복 순서는
[네이티브 Linkage 트랜잭션](docs/native-linkage.md)을 참고하세요.

## 로컬 개발

Python 3.12 이상이 필요합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
```

설정만 검증하려면 다음 명령을 사용합니다.

```bash
jebao-flowd --config config.example.yaml --check-config
```

현재 LAN에서 장비를 읽기 전용으로 검색하려면 다음 명령을 사용합니다. IoT VLAN의
브로드캐스트 주소를 여러 번 지정할 수도 있습니다.

```bash
jebao-flowctl discover --timeout 5
jebao-flowctl discover --target 192.0.2.255 --json
```

검색으로 확인한 주소에서 인증과 raw 상태 조회만 수행하려면 `probe`를 사용합니다. passcode는
출력하지 않으며 장비에 control/write 프레임을 보내지 않습니다.

```bash
jebao-flowctl probe 192.0.2.41 192.0.2.42 --json
jebao-flowctl probe 192.0.2.41 --decode --json
```

## Docker

```bash
cp config.example.yaml config.yaml
MQTT_PASSWORD='replace-on-home-server' \
  JEBAO_FLOW_CONFIG=./config.yaml \
  docker compose up --build
```

현재 Compose 파일은 기본적으로 예제 설정을 읽고 Observer 데몬을 실행합니다. Linux 홈서버에서
UDP 브로드캐스트 검색을 사용할 수 있도록 host network를 기본값으로 둡니다. 실제 배포
전에는 `config.example.yaml`을 `config.yaml`로 복사해 장비와 MQTT 주소를 수정하고,
`JEBAO_FLOW_CONFIG`로 그 파일을 마운트합니다. 비밀번호는 저장소 파일이 아니라 홈서버의
환경 변수나 비밀 저장소로 주입합니다.

각 장비의 실제 `device_id`와 MAC은 공개 예제에 넣지 말고 홈서버의 `config.yaml`에서
`devices[].identity`로 지정해야 합니다. 정확히 매핑되지 않은 장비는 관찰 연결을 열지 않습니다.

## 안전 원칙

- 빠른 펄스는 장비 내장 모드를 사용합니다.
- 출력은 그룹과 장비 양쪽 제한을 통과해야 합니다.
- 동일 값 중복 전송과 과도한 명령 전송을 막습니다.
- 전송 전 실패로 증명된 경우만 평상시 명시적으로 재시도하며, 종료 중 이미 승인된 OFF는 한 번만
  제한 재시도합니다. ACK가 불명확하거나 write 도중 외부 상태 무효화가 겹치면 actual-state
  재조정 전까지 같은 명령을 다시 보내지 않습니다.
- 재접속 후에는 저장 상태를 단계적으로 복원합니다.
- MQTT 또는 Home Assistant가 중단돼도 현재 패턴은 계속 실행합니다.
- 비상 정지는 자동으로 해제하지 않습니다.

전체 범위와 개발 기준은 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md), 공개 구현 조사 결과는
[docs/protocol-research.md](docs/protocol-research.md)를 참고하세요. 실제 장비 조사 결과는
[Capability 템플릿](docs/device-capability-template.yaml)을 복사해 모델별로 기록합니다.

## 라이선스

[MIT](LICENSE)
