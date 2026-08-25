# Jebao Flow Engine

Jebao Flow Engine은 제바오 수류모터와 리턴펌프를 클라우드 없이 로컬에서 제어하고,
여러 펌프를 하나의 논리적인 수류 그룹으로 운전하기 위한 프로젝트입니다. 실행 데몬의
이름은 `jebao-flowd`입니다.

> 현재 상태: 초기 설계와 시뮬레이터 단계입니다. 실제 수조 장비 제어에는 아직 사용하면
> 안 됩니다.

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

## 현재 포함된 것

- Pydantic 기반 YAML 설정 모델과 교차 검증
- 프로토콜 계층과 장비 계층 사이의 추상 인터페이스
- Gizwits GAgent 프레임 코덱, 인증 세션과 UDP 장비 검색
- 여러 장비의 인증/raw 상태를 쓰기 없이 확인하는 `jebao-flowctl probe`
- 비동기 가상 장비 시뮬레이터
- Constant, Sync, Anti Phase 패턴 계산기
- 그룹 및 장비 출력 제한
- Docker/Compose 실행 뼈대
- 단위 테스트와 GitHub Actions CI

제품별 데이터 포인트 스키마를 적용하는 실제 장비 어댑터, MQTT 및 Home Assistant
Discovery는 다음 개발 단계에서 구현합니다. 실제 장비 6대의 읽기 전용 검증 결과와 제품군별
Capability는 [검증된 장비 카탈로그](docs/devices/README.md)에 정리했습니다.

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
jebao-flowctl discover --target 192.168.20.255 --json
```

검색으로 확인한 주소에서 인증과 raw 상태 조회만 수행하려면 `probe`를 사용합니다. passcode는
출력하지 않으며 장비에 control/write 프레임을 보내지 않습니다.

```bash
jebao-flowctl probe 192.168.20.41 192.168.20.42 --json
```

## Docker

```bash
cp config.example.yaml config.yaml
MQTT_PASSWORD='replace-on-home-server' \
  JEBAO_FLOW_CONFIG=./config.yaml \
  docker compose up --build
```

현재 Compose 파일은 기본적으로 예제 설정을 읽고 데몬 뼈대를 실행합니다. Linux 홈서버에서
UDP 브로드캐스트 검색을 사용할 수 있도록 host network를 기본값으로 둡니다. 실제 배포
전에는 `config.example.yaml`을 `config.yaml`로 복사해 장비와 MQTT 주소를 수정하고,
`JEBAO_FLOW_CONFIG`로 그 파일을 마운트합니다. 비밀번호는 저장소 파일이 아니라 홈서버의
환경 변수나 비밀 저장소로 주입합니다.

## 안전 원칙

- 빠른 펄스는 장비 내장 모드를 사용합니다.
- 출력은 그룹과 장비 양쪽 제한을 통과해야 합니다.
- 동일 값 중복 전송과 과도한 명령 전송을 막습니다.
- 재접속 후에는 저장 상태를 단계적으로 복원합니다.
- MQTT 또는 Home Assistant가 중단돼도 현재 패턴은 계속 실행합니다.
- 비상 정지는 자동으로 해제하지 않습니다.

전체 범위와 개발 기준은 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md), 공개 구현 조사 결과는
[docs/protocol-research.md](docs/protocol-research.md)를 참고하세요. 실제 장비 조사 결과는
[Capability 템플릿](docs/device-capability-template.yaml)을 복사해 모델별로 기록합니다.

## 라이선스

[MIT](LICENSE)
