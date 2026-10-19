# run: 2026-09-29-vson-feeder-ha

- 목적: VSON SMART WP8621N 자동 급여기를 Home Assistant Aqua 대시보드에 연결하고, 즉시 급여 명령이 한 번만 나가며 실제 급여 기록으로 확인되는지 검증한다.
- 질문: 앱이 사용하는 상태·기록·급여 경로가 HA에서도 동작하는가? 기기 자체 시간표는 어떤 추가 식별자를 요구하는가?
- 실행 기준 commit: `cac6bb0` + 이 run의 미커밋 신규 custom component. 기존 Jebao `src/` 동결 코드 변경 없음.
- 장비 명령: HA `button.press` → `button.vson_smart_feeder_feed_now` **1회**. 자동 재전송 0회. 앱 시간표 write 0회.
- 종료: 상태·기록 조회 PASS, 버튼 1회 송신 뒤 기록 갱신 PASS, HA 구성 검사 PASS, Aqua 급여기 뷰 저장·재조회 PASS.
- 장비가 돌려준 값:
  - HA 급여 직전 마지막 기록: 2026-09-29 12:18:02 UTC, `frequency=1`.
  - HA 급여 후 마지막 기록: 2026-09-29 12:37:05 UTC, `frequency=1`; 버튼 상태 시각 12:37:04 UTC. 이 기록은 VSON 클라우드 조회 결과이며 모터 움직임의 물리 관측은 아니다.
  - HA 클라우드 상태: online `on`, food low `off`, food reminder `off`; 이때 기기 ID가 없어서 native schedule은 `ID 미등록`.
  - 증거 등급: (c) 당시 HA API·VSON 클라우드 응답을 재구성해 기록. 원본 HTTP 응답은 별도 raw artifact로 보존하지 않았으므로 (a)/(b)로 주장하지 않는다.
- Aqua 대시보드: 기존 5개 뷰 exact 보존, `feeder` 뷰만 추가. 적용 전 private backup opaque id `aqua-before-vson-feeder-20260929T123901Z.json`, digest SHA-256 `e7106bcc55e565ea39dac0cda125fc2f20a208bcdbf1144bc0b15bb477a0d837`. HA `.storage` 직접 수정 0회.
- 원복: 장비 명령은 일회성 급여라 물리적으로 되돌릴 수 없다. 대시보드는 위 백업 구성으로 API 복원할 수 있으며, custom component는 HA에서 config entry 제거 후 파일 제거로 중단할 수 있다. 원복 작업은 이번에 하지 않았다.
- 새로 확정: MAC으로 상태·급여 기록을 조회하고 즉시 급여를 1회 전송하면, 그 직후 클라우드 최근 급여 시각이 갱신된다. 기기 자체 시간표는 앱의 별도 cloud device ID를 요구하며, MAC을 `id`로 사용한 조회는 거절됐다. 이 ID는 현재 확보되지 않아 시간표 읽기·쓰기 실기는 하지 않았다.
- 미확정: 기기 자체 시간표 서비스의 실장 동작, 피더 로터의 물리 움직임, VSON 앱 계정의 장비 ID 확보 방법.
