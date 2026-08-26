# 공개 Jebao 로컬 구현 조사

조사일: 2026-08-25

초기 뼈대에서는 아래 저장소의 코드를 복사하지 않았습니다. 프로토콜 실장 단계에서
재사용 여부를 다시 결정하고, 코드를 가져오면 원 저작권 고지와 MIT 라이선스 조건을 함께
유지합니다.

## 공식 Gizwits GAgent 자료

- 저장소: <https://github.com/gizwits/Gizwits-GAgent>
- 확인 커밋: `72d2db0c386c76224754f350422624255551a737`
- 확인 내용: 프로토콜 버전 magic `0x00000003`, discovery/login/transmit/control 명령 상수와
  LAN 헤더 길이
- 라이선스: 저장소에서 명시적인 라이선스 파일을 찾지 못함
- 적용: 소스 코드를 가져오지 않고 프로토콜의 사실과 wire 상수만 교차 확인했습니다.

## 1. `jrigling/homeassistant-jebao`와 `python-jebao`

- 저장소: <https://github.com/jrigling/homeassistant-jebao>,
  <https://github.com/jrigling/python-jebao>
- 확인 커밋: 통합 `52a45f3454f228b1be12eb3386ef9a6387e6421e`, 라이브러리
  `b69dd809d9abd1aa2c136fcd23bb2821c7c9a5c1`
- 라이선스: 두 저장소 모두 MIT
- 구조: Home Assistant 쪽은 entity/config flow/coordinator에 집중하고 실제 검색, 연결,
  장비 상태와 명령은 `python-jebao` 패키지에 둡니다.
- 특성: 통합 manifest 기준 `local_polling`이며 `python-jebao==0.1.7`을 사용합니다.
- 판단: HA 어댑터와 장비 라이브러리 분리는 좋은 기준입니다. 현재 문서상 지원 중심이
  MDP-20000이므로 수류모터의 다양한 product key 지원 여부는 실제 장비로 다시 확인해야
  합니다.

## 2. `chrisc123/jebao_aqua-homeassistant`

- 저장소: <https://github.com/chrisc123/jebao_aqua-homeassistant>
- 확인 커밋: `eb16041f2c48d8d9ca988703640bdc695f3eb39c`
- 라이선스: MIT
- 구조: HA 통합 안에 `gizwits_lan` 패키지를 포함하며 `connection`, `device`,
  `device_manager`, `device_status`, `models`, `protocol`로 나눕니다.
- 특성: manifest 기준 `local_push`입니다. 연결 감시, keepalive, 지수 백오프가 별도
  connection 객체에 있고 discovery는 UDP 12414, 장비 세션은 TCP 12416을 사용합니다.
- 판단: 여러 모델과 push 상태를 고려할 때 가장 넓은 참고 대상입니다. 다만 HA 생명주기와
  프로토콜 코드가 같은 저장소에 있으므로 독립 데몬에서는 어댑터 경계를 더 분명히 둡니다.

## 3. `markosharknz1/ha-jebao-pumps`

- 저장소: <https://github.com/markosharknz1/ha-jebao-pumps>
- 확인 커밋: `22ac2c2194d4ff7f798d85d35c522e4efb0e9bd6`
- 라이선스: MIT
- 구조: 핵심 `jebao_gizwits` 패키지가 `protocol`, `discovery`, `session`, `schema`,
  `control`로 분리되고 Home Assistant 통합은 별도 디렉터리에 있습니다. 캡처 fixture와
  product schema 카탈로그도 포함합니다.
- 특성: UDP discovery 응답에서 device id/product key를 얻고, TCP 세션에서 passcode와
  login을 거쳐 상태 읽기 및 control payload를 처리합니다.
- 판단: protocol/schema/control의 분리와 캡처 기반 테스트는 이 프로젝트가 채택할 만한
  방향입니다. 최신 커밋에서 지속 연결 방식이 다시 채택된 만큼 연결 수명은 장비 실측을
  통해 결정해야 합니다.

## 초기 결론

1. `jebao-flowd`의 상위 계층은 이 저장소의 `JebaoDevice` 인터페이스만 의존합니다.
2. 실제 드라이버 내부를 discovery/session/codec/schema로 분리합니다.
3. product key별 Capability와 데이터 포인트 스키마를 코드와 분리합니다.
4. 실제 패킷 캡처는 익명화한 binary fixture로 보존하고 codec 테스트의 기준으로 씁니다.
5. 드라이버를 직접 구현할지 기존 MIT 구현을 가져올지는 보유 모델의 호환성 실험 후
   결정합니다.
6. 어떤 선택이든 외부 구현의 라이선스와 출처를 `THIRD_PARTY_NOTICES.md`에 기록합니다.

## 현재 독립 구현 범위

- 최대 크기가 제한된 1~4바이트 LEB128 프레임 길이 파서
- magic, 최소 본문, 선언 길이, trailing bytes를 엄격히 검사하는 codec
- TCP 스트림의 조기 종료와 timeout을 구분하는 raw session
- passcode/login, heartbeat, raw state read와 unsolicited frame 건너뛰기
- UDP 12414 broadcast/unicast discovery와 응답 필드 파싱
- 여러 VLAN broadcast 주소를 반복 지정할 수 있는 읽기 전용 `jebao-flowctl discover`

제품별 schema와 안전 계층이 완성되기 전까지 raw write는 진단 CLI에 노출하지 않습니다.

## 실제 장비 읽기 전용 검증

2026-08-25 홈서버에서 라우터를 경유해 격리된 IoT VLAN의 장비 6대를 확인했습니다. 다섯
product key 모두 UDP discovery, TCP 연결, 로컬 인증과 raw 상태 읽기에 성공했습니다.
`markosharknz1/ha-jebao-pumps`의 MIT 라이선스 제품 스키마와 디코더를 실행 시점의 읽기 전용
교차검증에 사용했으며, 해당 코드나 스키마 파일은 이 저장소로 복사하지 않았습니다.

개별 MAC, device id, 사설 IP와 passcode는 공개 저장소에 기록하지 않습니다. 익명화한
제품군별 결과는 [검증된 장비 카탈로그](devices/README.md)에 있습니다. 실제 장비에는
control/write 프레임을 보내지 않았으므로 쓰기 경로는 아직 미검증입니다.

## 실기 제어 전 오프라인 검증

다섯 제품군 중 초기 제어 범위인 펌프 네 제품군에 대해 자체 스키마와 control payload
생성기를 구현했습니다. 제품별 전체 flags/value buffer 크기를 명시하고, 같은 status buffer의
48개 장비 로컬 시간표 슬롯도 제품키별 배치로 읽기 전용 디코딩합니다.

다음 변경 조합의 완성된 payload를 `markosharknz1/ha-jebao-pumps`의 캡처 기반 구현 결과와
바이트 단위로 비교했으며 모두 일치했습니다.

- DC Pump Pro: 전원, 출력, 주파수
- Local Wavemaker: 전원, 네이티브 모드, 출력, 주파수
- Aquarium Pump: 전원, 모터 속도
- Local Wavemaker Pro: 전원, 출력, 주파수

비교 과정에서도 프레임은 네트워크로 보내지 않았습니다. 실제 장비 검증 전에는 전역
`runtime.dry_run: true`와 장비별 `allow_hardware_writes: false`를 유지합니다.

## 장비 시간표 A/B 실측

2026-08-26 DC Pump Pro에서 Jebao 앱으로 Constant Flow 경계를 `08:00`에서 `08:01`로
변경한 뒤 동일한 LAN read를 비교했습니다. 8바이트 슬롯에서 첫 구간 종료와 다음 Feed 구간
시작이 모두 `08:01`로 이동했고 나머지 구간은 유지되어, 다음 배치를 실기 상태로 확인했습니다.

```text
[start hour, start minute, end hour, end minute, mode, flow, frequency, feed value]
```

Local Wavemaker는 8바이트, Pro는 9바이트, Aquarium Pump는 6바이트 슬롯이므로 길이만으로
공통 해석하지 않고 product key별 모드·파라미터 의미를 적용합니다. 종료 `24:00`과
Aquarium Pump의 `00:00–00:00` 전일 구간도 보존합니다. 비어 있지 않지만 검증 범위를 벗어난
슬롯은 버리거나 추측하지 않고 인덱스를 `invalid_slots`로 게시합니다. 도징 펌프의 채널별
96바이트 레코드는 배치가 확인될 때까지 지원 대상에서 제외합니다.

DC Pump Pro는 공개 슬롯 설명과 같은 제품의 `AutoMode` 정의가 코드 0~2에서 서로 충돌합니다.
이번 A/B로 코드 0=`constant`, 현재 슬롯으로 코드 4=`feed`는 확인했지만 코드 1·2의 의미는
확정하지 않았습니다. 추가 A/B 전에는 각각 `unverified_1`, `unverified_2`로 표시해 펄스·사인
중 하나라고 추측하지 않습니다.
