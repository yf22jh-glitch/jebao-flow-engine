# run: 2026-10-20-scheduler-reboot-paused

- 요청: 시간표를 실행한 뒤 서버를 재부팅하면 자동 운전이 돌아오지 않는 문제 확인.
- 질문: 컨테이너 자동 시작 실패인가, 시작 후 시간표 실행이 보류되는가?
- 범위: 서비스·부팅 로그 및 보존 상태 파일 조회. 장비 제어 송신 0회.
- 배포 source: `a493533d7cab9ac6ff3be6a538e90ab3225308f0`.
- 배포 image: `sha256:1a153fa54bbed3f281383c27a5db5224a53ececafa3e4321ff4deb90d3487765`.

## 확인한 사실

호스트 부팅은 2026-10-20 00:33:08 KST다. 시간표 컨테이너는
`restart=unless-stopped`이며 `2026-10-19T15:33:19.839568285Z`에 자동 시작했다.
조회 시에도 실행 중이고 재시작 횟수는 0이다.

이전 프로세스는 `2026-10-19T13:55:20.485050244Z`에 정상 종료를 기록했다.
마지막 검증 행은 2026-10-19 21:00 KST이고 다음 경계는 2026-10-20 00:00 KST다.
해당 행의 parent journal은 `verified`다. 현재 상태는 `paused`이고
편집기 오류는 `2026-10-19T15:34:09.771869Z`, `scheduler_paused/ValueError`다.
환수 override는 OFF다.

부팅 직후 아래 GMP discovery 응답이 없어 읽기 재시도 3회가 기록됐다.
통신 상태는 `2026-10-19T15:34:09.761790Z`에 해제됐다. 네트워크 복구 뒤에도
시간표가 paused에 남았다는 것은 파일로 확인했다.

실행 중인 editor/runtime/service 파일의 SHA-256은 배포 source와 일치한다.
static trace에서 `ScheduleEditor.initialize`는 시작 전의 시각을 보존한 채
plan 검증 후 `apply_boundary`를 호출한다. `apply_boundary`는 경계보다 30초
이상 늦으면 paused를 저장하고 ValueError를 발생시킨다. 이번 시작은 자정에서
33분 이상 지난 시각이라 그 조건을 만족한다. 단, 당시 로그에는 예외 메시지가
없으므로 이 분기 귀속은 보존 상태와 코드에 근거한 추론이다.

**이 실행으로 새로 확정된 사실:** 컨테이너 자동 시작은 성공했다. 시간표 실행이
시작 후 paused로 보존돼 있으며, 서버 중단 중 자정 경계를 놓쳤다.
자동 시작 등록 누락이나 Docker 재시작 정책의 문제는 아니다.

## 보존 증거

- 등급 (b): 실제 보존한 컨테이너 inspect/log, active/editor/communication/maintenance,
  시간표와 직전 행 저널. 원본 장비 응답이나 물리 운전 증명은 아니다.
- opaque artifact id: `scheduler-reboot-20261019T160948Z`.
- 로그 UTC span: `2026-10-19T13:55:20.049164723Z` ~ `2026-10-19T15:33:59.740151123Z`.
- 상태 수집: `2026-10-19T16:09:48Z` 시작.
- manifest SHA-256: `66b6352cb17be7e08912c7c41de32436133a42e405a36da94849191bf21bc189`.
- active SHA-256: `f47df97b43d18b3bc7957084fbe3bb3b304892b5c8382a9a0c496d1c9718b994`.
- editor SHA-256: `643dbfbb8d6d7cf93f1f7a39ab51cf76f79fb6a098fb0162e8dbda8ca87faf97`.
- 아래 GMP identity binding: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- 위 GMP identity binding: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.

## 다음 조치와 한계

시작 시 이미 active였던 시간표는 읽기 검증이 끝난 현재 시각의 행부터 이어갈 수 있도록
수정·오프라인 검증한다. 수동 pause·환수 override·미완료 명령·이미 시도된 행은
자동으로 풀거나 재전송하지 않는다. 신원·출력 범위·원 이미지·현재 역할·자격·저널은 유지한다.
시작 시각과 놓친 경계를 진단에 남겨 ValueError만으로 끝나지 않게 한다.

장비 조회·복귀 및 재배포는 이 기록 시점에 아직 실행하지 않았다.
실제 호스트 재부팅 후 수정본 자동 복귀도 미검증이다. Q1/Q2의 판정은 변경하지 않는다.
