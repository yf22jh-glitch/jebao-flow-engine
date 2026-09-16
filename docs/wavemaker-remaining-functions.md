# 남은 수류모터 기능 — 2026-09-16 재개 범위

사용자는 HA 대시보드를 조작 화면으로 사용하며, 기존 Sync/Async 구현을 다시 만들지 않고
남은 기능을 구현하라고 요청했다. 별도 frontend는 만들지 않는다. 이번 범위는 소프트웨어와
오프라인 검증이며 장비·앱 조작, 자격 발급, 운영 배포 또는 기존 미완료 source 종료가 아니다.

닫을 질문: 기존 native 실행기를 HA의 MQTT 요청에서 현재 대상·쓰기 잠금·원본 소유·멱등성·
전용 복구 의미를 유지하며 호출할 수 있는가. 근거는 기존 실행기·앱 정적 계약과 transport/
durable artifact 계약 시험이다. Bar/clock은 별도로 앱 근거와 구현 가능 계약을 조사한다.

## 구현 범위와 유지할 계약

- `local_control/ha.py`, `mqtt.py`, `local_control_cli.py`와 직접 tests·문서에서 기존
  `NativeWavemakerController`의 preview/attach/detach/status/recover 호출을 연결한다.
- 명시적인 write-enabled runtime에만 native 요청 경로를 노출한다. 현재 manifest의 Pro
  대상만 허용하고 runtime dry-run 및 대상별 write 잠금을 적용한다. attach 자격은 실제
  실행기에서 현재 신원별로 다시 확인한다. 자기 원본 복구를 새 attach 자격에 종속시키지 않는다.
- 모든 실행은 scalar·그룹·poller와 같은 operation lock을 사용한다. 새 attach는 두 장비의
  직접 소유권을 요청하며 replay는 소유권을 다시 바꾸지 않는다. 해제/복구에는 보존된 원본
  대상을 사용하고 현재 manifest의 stable identity/role과 일치시킨다.
- native recover만 호출한다. generic recover를 성공 attach의 undo로 바꾸지 않는다.
  기존 자동 원복/명시 관찰 보류/master cycle 옵션의 의미는 유지하고 자동 재시도하지 않는다.
- retained/DUP 요청 거부, 결과 비retained, 예외 원문 비공개, 역사 결과를 현재 상태로 출판하지
  않기, 미완료 source의 전역 차단을 유지한다. 미리보기와 상태 조회는 장비에 접속하지 않는다.
- frozen native 여섯 파일·protocol·공용 guard·writer/schema 입력·기존 자격은 수정하지 않는다.
  Bar timer/clock에 근거 없는 wire·readback·복원 동작을 추가하지 않는다.

Bar schedule의 384B 복원 자격과 추가 scalar producer, timer 복합 명령, clock의 진행하는 시각
검증 계약은 독립 정적 조사 결과로 판단한다. 확정 가능한 소프트웨어와 미확정/실장 필요를
구분하며 남은 것을 완료로 표시하지 않는다. 이번 구현 결과 커밋은 구현한 범위만 소진한다.
