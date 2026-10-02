# run: 2026-10-02-ha-bar-schedule-editor

## 사전 확인 · 구현 범위

사용자는 GMP 전용 편집기에서 MCP는 보관용이라는 설명 뒤 “연결하자”라고 요청했다.
질문은 바형 시간표의 편집·저장을 기존 qualified native schedule API에 연결하면서 GMP
실행표를 유지할 수 있는가이다. frozen ASYNC 하네스·writer·protocol·qualification은 변경하지
않는다. 저장은 metadata-only이고 바형 적용은 별도의 명시 버튼이다.

2026-10-02 04:35 UTC HA API 조회에서 Bar 시간표 센서는 qualified schedule request topic과
preview topic, 31개 native 슬롯과 TimerON을 공개했다. GMP 편집기는 active/busy 없음,
기본 템플릿·기존 실행 epoch를 유지했다. UI 조회는 새로운 장비 제어 요청이 아니다.
이 표본은 **재구성된 API 관측(c)**이며 raw 장비 프레임이나 새 write 실기 증거가 아니다.
장비가 보고한 시계 필드는 과거 값이므로 호스트 시각과 동일하다고 주장하지 않는다.

기존 실장 적용 근거는 [Bar schedule qualification](2026-09-16-bar-schedule-qualification.md),
[Bar timer HA activation](2026-09-16-bar-timer-ha-activation.md)이다. 전용 대시보드 편집기가
없었던 것과 native API가 없었던 것을 구분한다.

GMP active epoch: `8a4268b0b495294d1dc07be807fd7d99860d472cccead91087c15e6e05f26f2f`.
현재 구현·배포·UI 검증 결과는 아래에 append한다. 새 장비 시간표를 임의 적용하지 않는다.

## 구현·배포 결과 · 2026-10-02 04:58–05:11 UTC

편집기에서 MCP 보관 행을 수정하고 저장한 뒤, 별도의 바형 적용 버튼으로 기존 qualified
native schedule request API를 호출하도록 연결했다. GMP 템플릿 활성화는 MCP를 변경하지
않는다. Bar는 장치 자체 시간표를 실행하며 새로운 외부 MCP 실행기를 만들지 않았다.

- 앱 정의와 기존 codec에 따라 `Classic · Pulse`, `Classic · Tide`, `Sine`, `Random`,
  `Constant`, `Feeding`, `Stop`을 표시한다. Classic/Sine만 Frequency를 사용한다.
- 템플릿 저장·복제는 metadata-only이다. Bar는 최대 48구간, 현재 승인된 Flow 30–50,
  Feeding 6분으로 편집하며 Stop은 Flow 0이다. 물리 writer의 범위 검증은 변경하지 않았다.
- 장치 표 불러오기, 구간 편집, compile-only 미리보기, 명시적인 Bar 적용 및 Timer 제어를
  제공한다. 적용은 기존 durable writer와 full-image readback을 그대로 사용한다.
- 결과 채널을 먼저 구독하고 한 번만 요청한다. 전달 결과가 모호하면 자동 재송신하지 않는다.
  accepted/verified 응답만으로 UI 성공을 표시하지 않고 이후 최신 HA 장치 표를 대조한다.
  이미 장치 표와 일치하면 중복 적용하지 않는다. 처리 중 편집 템플릿도 교체하지 않는다.

GMP의 정확한 설치 이미지 위에 editor metadata 파일 하나만 교체했다. 나머지 Python source
354개가 동일한 digest임을 비교했다. native writer·protocol·actuator·동결 하네스는 그대로다.
배포 직전 검증된 구간에서 교체했고 active/plan 파일이 byte-exact, 새 operation 0건이었다.
기존 세 템플릿과 실행 epoch를 유지했으며 14:00 KST 이후 HA도 active/current verified였다.

배포 산출물은 private 저장소에 보존했다. 아래는 실제 존재와 digest를 확인한 안전한 ID다.
제어기 backup/release는 structured deployment artifact이며 raw 장비 프레임은 아니다.

| 산출물 ID | 내용·SHA-256 |
|---|---|
| `pro-schedule-editor-20261002T045809Z` | metadata-only 이미지 `8c8058c5097ed5a724e90952df1ff566690ac5ce9b6ca46591d92dd0d95cbce5`; active backup `38953e3156ab1dca5453b1e05cec3fcc71201b4afa1d4c60411c276e2c158e93`; editor backup `432aad0794f5ca43ce2da726489c74e6b4d69c2c4086e83d49e5a202f2772322` |
| `aqua-schedule-templates-20261002T045910Z` | Bar 엔티티 연결. release `8ad31c69883db107144ec9a945525467b9e0b3ea96410868ad222d50960fe501`; 다른 6개 view 보존 |
| `aqua-schedule-templates-20261002T050715Z` | 최종 editor asset `121288c6665a379e7923d76f0bc190e13fdd0b50f0e5be86e0d078c884198725`; 연결된 설정과 7개 view 모두 보존 |
| `bar-editor-preview-20261002T050143Z` | HA/MQTT 응답 `4c7fe403e7d298b645433a5b3f51fa5059cbbdc3ddf90c0cd0b38c1be0ae86b1`; 검사 보고서 `7a154f54d30cfcb396ee848330b048b3176c7658a030286a8b434f7d09cc6476` |

실제 HA의 현재 Bar 31구간으로 **preview만** 한 번 요청했다. 응답 image는 384바이트,
SHA-256 `d76f20c3f6bfeacb6cb163c48234a51088d7d958e9a7adcd2eb9255473725d3c`로
독립 계산과 일치했다. `hardware_write:false`를 확인했고 Timer·Bar 보고 표·GMP epoch는
유지됐다. 이것은 보존한 API 응답 관측이며 새로운 물리 apply 성공이나 펌웨어 동작 증명이
아니다. 새 Bar 계획·Timer·Power·Flow 변경 요청은 **0회**다.

검증:

- 기존 editor/schedule/read-retry Python 테스트 **125 passed**, 변경 파일 Ruff 통과.
- 기존 JS 테스트와 신규 Bar codec/transport/lifecycle 테스트 통과. byte 5 Feeding union,
  Classic Pulse/Tide 플래그, 저장 시 native 요청 0회, 구독 선행, 중복 차단, 응답 오류,
  전달 결과 모호 시 비재송신, 최신 장치 표 일치 조건을 검사했다. 펌웨어 fake를 추가하지 않았다.
- Orca에서 실제 custom card를 사용하는 송신 금지 fixture의 preview 버튼을 실행했다.
  31구간 미리보기 확인, JS 오류 0개, 1440px 및 390px 가로 넘침 없음과 screenshot 확인.
  fixture 결과는 실제 장비 제어 검증과 구분한다.

### 이번 작업에서 확정한 것과 남은 범위

HA 편집기와 기존 Bar native API 연결 및 실제 preview 왕복은 확인했다. 저장과 GMP 운전
분리도 코드·배포 상태·테스트로 확인했다. **새 시간표의 물리 적용 실기는 수행하지 않았다.**
사용자가 선택한 저장 표를 명시적으로 적용할 때 기존 qualification 경로가 담당한다.
Q1/Q2 판정과 frozen ASYNC 하네스 상태는 변경하지 않았다.

최종 API 재확인: Bar 연결=true, 31슬롯·TimerON, 템플릿 3개와 선택/실행 템플릿 동일,
GMP active/current verified, 다음 경계 14:57 KST, 최종 asset HTTP SHA-256 일치.
7개 view·19개 resource였다. 이번 업데이트는 기존 editor resource만 교체했으며 다른
카드·view 및 기존 실기 기록·격리된 source 변경은 병합하거나 되돌리지 않았다.
