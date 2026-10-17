# run: 2026-10-18-scheduler-restart-idle-read

- 요청: 환수 전원 OFF 기능 코드 리뷰와 배포의 후속 검증.
- 질문: 정상 서비스 종료가 read-only idle 조회를 취소해도 검증된 시간표가 유지되는가?
- 선행 기록: [환수 전원 OFF 배포](2026-10-18-maintenance-power-off-deployment.md).
- UTC span: `2026-10-17T23:00:55.929207Z` ~ `2026-10-17T23:03:22.727345Z`.
- 최종 source: `a493533d7cab9ac6ff3be6a538e90ab3225308f0`, `fix/maintenance-power-off`.
- 최종 image: `sha256:1a153fa54bbed3f281383c27a5db5224a53ececafa3e4321ff4deb90d3487765`.

## 변경과 측정

`verify_idle`이 서비스 종료의 CancelledError까지 장비 검증 실패처럼 취급해 paused를
저장하는 내부 결함을 수정했다. 읽기 취소는 기존 active 상태를 유지하고 진단을 남기며,
실제 오류·불일치에는 기존 보류 처리를 유지한다. 제어 송신 경로는 변경하지 않았다.
환수 중 조회 취소는 override를 해제하지 않는다는 별도 테스트도 통과했다.
관련 Python 회귀 테스트는 총 195개 PASS다.

수정본 배포 후 active 상태를 확인했다. 실행기 프로세스가 MQTT 및 두 장비의 TCP
연결을 동시에 보유한 상태에서 `2026-10-17T23:01:49.958815Z`에 정상 재시작했다.
종료 로그에 `idle_read_cancelled`, `device_writes=0`, `schedule_status=active`가 남았다.
새 프로세스 시작은 `2026-10-17T23:01:51.313113989Z`이며 수동 재무장 없이 active가
유지됐다. 최종 active.json은 배포 직전과 전체 동일하다.

최종 장비 조회 UTC span: `2026-10-17T23:02:17.229395Z` ~ `2026-10-17T23:02:29.795214Z`.
아래 GMP는 ON/master/sine/Flow40, 위 GMP는 ON/async_slave/sine/Flow35이고,
양쪽 Frequency80·TimerOFF·fault 없음이다. 바형은 ON/independent/TimerON/FeedingOFF·fault
없음이다. 세 identity 및 native image digest가 선행 조회와 동일하다.

- **새로 확정된 사실:** 실제 서비스의 idle 조회 취소와 재시작 후에도 검증된 시간표가
  active로 유지되고 현재 장비 응답도 일치했다.
- 배포 이후 새 장비 제어 저널 0개, 추가 장비 제어 송신 0회, 미완료 명령 없음.
- 대시보드 health normal, maintenance OFF. 두 JS HTTP 해시는 검토본과 일치한다.
  UI 파일은 선행 배포와 동일하며 URL 버전도 유지했다.
- 한계: 실제 환수 OFF/ON 왕복 및 물리적 유량 측정은 하지 않았다. 기존 Q1/Q2는 변경하지 않는다.

## 보존 증거

private artifact id: `maintenance-poweroff-restart-20261018`.
배포 구성, 재시작 로그, 상태/API 응답, source hash, 원본 프레임 3개를 보존하고 확인했다.
manifest SHA-256: `9257ce732d6d179798a41b27254328585b40f584d718476309edef9de68ce7e4`.

- (a) `raw/lower.bin`: `cade617b5a6409e4444fcfe60746da07f5440929420e4e887607f9f36298ddd6`.
  Identity: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- (a) `raw/upper.bin`: `0ebd13ac66404623acf5d3308b05e85e333f4763dc98aa0655280929a6bb06a4`.
  Identity: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- (a) `raw/bar.bin`: `b5ef3a5b0914817730a722a4da4393f13b991c0449dc49a560b4c2ef22ff0c54`.
  Identity: `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
- (b) 재시작 로그, active 상태, UI/API 결과는 데몬의 persisted claim이다.

중간 시험 이미지 두 개는 제거했으며 최종 이미지와 교체 전 원복용 이미지를 보존한다.
배포 소스와 기록은 원래 저장소의 Git 브랜치에 보존한다.
