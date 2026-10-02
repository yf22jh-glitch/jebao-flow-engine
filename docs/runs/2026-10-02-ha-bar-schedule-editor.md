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
