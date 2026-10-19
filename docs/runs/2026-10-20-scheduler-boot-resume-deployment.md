# run: 2026-10-20-scheduler-boot-resume-deployment

- 요청: 서버 재부팅 뒤 자동으로 돌아오지 않는 시간표 수정 및 운전 복귀.
- 질문: 저장된 시간표와 현재 장비 상태를 보존해 복귀하고 서비스 재시작 뒤 active를 유지하는가?
- 선행 진단: [자정 경계 누락으로 paused](2026-10-20-scheduler-reboot-paused.md).
- source: `59084e32ac2889ecad8dc8993091606dcfe75d7f`, `fix/scheduler-boot-resume`.
- image: `sha256:a072883e9f0857606efa4987f98aaa30fa01414e46d312d58751ddd50cda6407`.
- 사전 장비 조회 UTC span: `2026-10-19T16:16:36.466206Z` ~ `2026-10-19T16:16:48.000955Z`.
- 배포·복귀·재시작 검증 UTC span: `2026-10-19T16:21:34.362990Z` ~ `2026-10-19T16:25:08.559333Z`.

## 변경과 오프라인 검증

시작 시 이미 active였던 시간표에 한해 현재 구간부터 이어간다. 장비 검색이 끝난
시각을 다시 읽고, 지난 구간은 건너뛰며 현재 행의 고유 journal로 한 번만 처리한다.
수동 pause·환수 override, 이전 상태·신원·이미지·출력·미완료 명령 검사는 유지한다.
시작 복귀 진단에는 놓친 경계, 선택한 행, 재개 시각과 송신 결과가 남는다.

기존 배포본의 코드로 자정 이후 재시작과 discovery 대기 중 행 변경 두 회귀를
재현했다. 수정본은 Linux의 네트워크 없는 컨테이너에서 관련 테스트 79개를 통과했다.
기존 source Ruff 지적 2개는 동일하며 새 지적은 없고, 새 테스트 파일 검사도 통과했다.
실제 production image의 non-root 사용자로 세 파일의 해시와 import를 검증했다.
동결 하네스·writer·qualification schema 변경은 없다.

## 복귀와 배포

사전 실제 장비 조회는 저장된 이전 검증 값과 현재 자정 행 모두에 일치했다.
미완료 명령은 없고 환수 override는 OFF였다.

기존 서비스만 중지하고 동일 환경·볼륨의 일회성 편집기 활성화를 수행했다.
이 작업은 backend의 모든 `execute` 호출을 예외로 막아 **장비 제어 송신이
불가능한 상태**에서 실행했다. 저장된 시간표의 기존 activation 경로가 현재 행을
`steps=[]`, `verified`로 기록하고 active로 복귀했다. 차단된 execute 호출도 0회다.

저장된 템플릿·draft와 두 장비 image digest는 그대로다. 기존 편집기 계약에 따라
실행 epoch의 `source_table`과 run digest만 새로 만들었다. 시간표 내용은 동일하다.
이후 수정본 상시 서비스를 시작했다. 환경변수·명령·마운트·권한·네트워크·재시작 정책은
동일하다. Docker inspect의 환경변수/Binds 순서와 기본 DNS의 `[]`/`null` 표현 차이는
있으나 유효 설정은 같고, 배포 설정의 실질 변경은 image다.

최초 기동은 `2026-10-19T16:21:56.828864057Z`, 현재 행 확인 완료는
`2026-10-19T16:22:09.947572778Z`다. 추가 제어 없이 `already_applied/writes=0`을 기록했다.
서비스만 정상 재시작한 뒤 새 프로세스 시작은 `2026-10-19T16:23:02.488015579Z`,
현재 행 확인 완료는 `2026-10-19T16:23:15.005474065Z`다. 다시 같은 무송신 결과를 기록했다.
재시작 전후 active.json 전체가 동일하다.

## 결과

| 대상 | 복귀 시 보존한 장비 응답 |
|---|---|
| 아래 GMP | action 3, master, ON, Timer OFF, sine, Flow 40, Frequency 80, fault 없음 |
| 위 GMP | action 4 report, async_slave, ON, Timer OFF, sine, Flow 35, Frequency 80, fault 없음 |

- 최종 MQTT 상태: `active`, `current_row_verified=true`, 통신 재시도 없음, 마지막 오류 없음.
- 청소·환수 OFF, 다음 경계 `2026-10-20 05:57 KST`.
- 배포 시작 이후 새 scheduler 장비 제어 journal 0개, 추가 장비 제어 송신 0회.
- **이 실행으로 새로 확정된 사실:** 실제 장비 보고가 현재 구간과 일치하는 상태에서
  시간표를 무송신으로 복귀했고, 수정본의 서비스 재시작 후 active와 현재 구간 검증이 유지됐다.
- 한계: 서버 전체를 다시 재부팅하지 않았다. 경계를 넘긴 부팅 복귀는 이번에 오프라인으로
  검증했고, 실제 새 시각 경계 전환·물리 유량·전체 서버 재부팅 후 운전은 이번 관측 범위 밖이다.
  slave 원본은 action 4 report이며 explicit reply 증명으로 바꾸지 않는다. Q1/Q2 판정은 유지한다.

## 보존 증거와 복구 수단

- opaque artifact id: `scheduler-boot-resume-20261020`.
- manifest SHA-256: `bf05ec7554df762d88814828f15d3c5324a2dfa8726df672f2ad4f8e33e3c8cb`.
- (a) `resumed-master.bin`: `85baabe898416d1b7b409f7047da4ba72aab0a62ba4bf641c6109770dc386924`.
  Identity binding: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- (a) `resumed-slave.bin`: `d3321ad1fb757143588f951e6f763fc6f9ae52912af75ab28cd939319b384ec5`.
  Identity binding: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- (b) 복귀 결과·active·editor·MQTT·서비스 로그·배포 구성·저널 목록을 실제 보존했다.
  재시작 전후 active SHA-256: `48c45dfc748952afdc17b8bd0a8e3d4afb917c32321446198e2a782f5a257eb6`.
- 사전 조회 artifact: `scheduler-reboot-20261019T160948Z`,
  `preflight-manifest.json` SHA-256 `fa16a1100abd35bd289bfecc9391af33f7d9d70c9c6fb3c2c83f72569a5a4f8b`.

교체 전 이미지와 정확한 resolved Compose 설정을 보존했다. 복귀한 시간표는 계속 실행하며,
이전 paused 상태로 되돌리는 작업은 하지 않았다.
