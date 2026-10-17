# run: 2026-10-18-maintenance-power-off-deployment

- 요청: 세 수류모터의 환수 전원 OFF/복귀 기능을 검토하고 대시보드와 함께 배포.
- 질문: 기존 운전과 시간표를 보존한 채 새 구현을 배포하고 실제 장비 응답으로 확인할 수 있는가?
- 범위: local-control scheduler 네 Python 파일과 HA JS 두 파일. 동결 하네스 변경/실행 없음.
- UTC span: `2026-10-17T22:53:53.400003Z` ~ `2026-10-17T22:57:40.038340Z`.
- source: `17bfcae5e03fc91ca0a6e8eb1f4c3b47e76e8676` (`fix/maintenance-power-off`).
- image: `sha256:8963caf48340983552faf689b922a37551b76d186305a9506b062b4d250cd7d3`.

## 검토와 배포

Feeding 대신 각 장비의 power OFF를 요청하고, durable override와 바형 TimerOFF를
유지한다. 명시적인 종료 요청으로 세 전원을 ON하고 현재 GMP 구간 및 기존 바형 Timer
설정을 복구한다. 정상 시간표의 identity/image/role/출력 검사는 유지한다.

193개 관련 Python 테스트와 Chromium 상태·중복 클릭·390px 화면 검증을 통과했다.
배포 전 canonical JS와 실제 HA release JS가 다름을 발견해, 실제 release의 버튼 DOM
보존 수정을 유지했다. HA 업데이트 중 pointerdown/up 및 focus 유지도 검증했다.
HA 리소스 URL은 WebSocket API로 갱신했고 HTTP 응답 두 파일의 SHA-256을 확인했다.

서비스 설정은 image 외 동일하게 유지했다. 교체 후 active 상태가 paused여서 같은
검증된 현재 행을 `resume_verified_row` 경로로 재개했다. 장비 명령 재전송은 없었다.
배포 전후 active.json 전체가 다시 동일하며 기존 plan digest도 그대로다.
정상 종료 시 read-only 조회의 CancelledError까지 verify_idle의 BaseException 처리로
paused가 되는 코드 경로를 후속 검토 대상으로 확인했다. 과거 정지의 최초 원인으로
확정하지 않는다.

## 결과와 한계

장비 조회 UTC span: `2026-10-17T22:56:32.430766Z` ~ `2026-10-17T22:56:54.062559Z`.

| 대상 | 실제 장비 응답 |
|---|---|
| 아래 GMP | 전원 ON, master, Timer OFF, sine, Flow 40, Frequency 80, fault 없음 |
| 위 GMP | 전원 ON, async_slave, Timer OFF, sine, Flow 35, Frequency 80, fault 없음 |
| 바형 | 전원 ON, independent, Timer ON, Feeding OFF, fault 없음 |

- power OFF/ON 및 복귀에 필요한 기존 qualification/identity 권한 확인 통과.
- GMP 두 native image 불변, 미완료 제어 저널 없음.
- 최종 scheduler active, maintenance OFF, 대시보드 health normal, 컨테이너 재시작 0회.
- 배포 시작 이후 생성/수정된 장비 제어 저널 0개, 추가 장비 제어 송신 0회.
- 새로 확정된 사실: 새 배포본과 대시보드 리소스가 실행 중이며, 기존 현재 구간과 세
  장비 응답을 그대로 유지한 채 시간표가 active로 복귀했다.
- 새 전체 환수 OFF/ON 왕복은 실행하지 않았다. mock/브라우저 검증은 펌웨어 전파나
  물리적 축 정지를 증명하지 않는다. 기존 Q1/Q2 판정은 변경하지 않는다.

## 증거

private artifact id: `maintenance-poweroff-20261018`.
원복 자료, 배포 구성, API 전후 응답, 검증 결과 및 원본 프레임 3개를 실제 보존했다.
manifest SHA-256: `50f5f9c938e5bdf146fc68c950d2190fb4b37521b80b5d4c75fcb727603cda05`.

- (a) `raw/lower.bin`: `df72f181f76b3ebfddcd4cc0ec927b5ccbbc2b52c496930f70827b1578e9a7e7`.
  Identity binding: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- (a) `raw/upper.bin`: `cb514b01104bdbbc853702b493ac147fbedfcec53e046e85c92d541af3ad9666`.
  Identity binding: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- (a) `raw/bar.bin`: `b5ef3a5b0914817730a722a4da4393f13b991c0449dc49a560b4c2ef22ff0c54`.
  Identity binding: `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
- (b) scheduler/API 상태는 daemon persisted claim이며 물리 유량 증명이 아니다.

미사용 시험 이미지 한 개는 제거했다. 원복용 기존 production 이미지 한 개와 배포 증거를
보존하고, 소스는 원래 저장소의 별도 브랜치에 커밋했다.
