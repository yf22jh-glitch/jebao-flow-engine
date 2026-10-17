# 청소·환수 전원 OFF 운용

GMP 두 대와 바형의 `power=false`를 각각 요청하고 세 장비의 fresh 전원 응답과
바형 TimerOFF를 확인한다. 장치 명령이며 전기 공급 차단은 아니다.

시작 전에 원래 시간표, native image digest, 장비 identity와 바형 Timer 설정을
보존한다. durable maintenance override를 먼저 저장하고 외부 시간표를 보류한 뒤
바형 TimerOFF → 바형 OFF → 상단 OFF → 하단 OFF 순서로 처리한다.
저장된 시간표 내용이나 GMP의 Mode/Flow를 정지 수단으로 바꾸지 않는다.

종료 버튼만 전원 ON과 복귀를 요청한다. 원래 identity/image를 확인한 뒤 각 장비를
켜고 GMP의 현재 시간 구간과 바형의 기존 Timer 설정을 복구한다. 지나간 구간을
재생하지 않는다. 바형 role만 달라진 경우 같은 image를 확인하고 원래 independent로
복구한다. 근거 관측은 [10월 17일 복구 기록](runs/2026-10-17-schedule-maintenance-resume.md)이다.

재시작, 통신 실패, 부분 송신 중단으로 override를 자동 해제하지 않는다. 미확정 송신은
재전송하지 않고 기존 coordinator의 관측 또는 복구 절차를 따른다. ACK가 있는 미완료
저널 등 자동 관측으로 종결할 수 없는 상태는 오류와 단계가 표시되고 시간표가 보류된다.
완료되지 않은 조작을 성공으로 표시하지 않는다.

## 코드 리뷰와 검증

- 기존 Feeding 정지 경로와 전원 ON을 강제하던 유지/복귀 전제의 충돌을 제거했다.
- 정상 시간표에는 기존 power ON, identity, role, image, 출력 범위 검사를 유지했다.
- 바형의 원래 TimerOFF도 복구하며, 오류 후 재시작에 자동 ON이 발생하지 않는다.
- ACK 누락 후 상태 조회는 원래 저널과 raw 관측을 보존하고 추가 제어 송신 없이 종결한다.
- 대시보드는 최신 세 장비 OFF + 바형 TimerOFF 확인에만 정지 확인 문구를 표시한다.
  중복 클릭, stale 상태, 일부 ON, 구형 Feeding 상태도 브라우저에서 확인했다.
- 195개 관련 Python 회귀 테스트, Chromium 데스크톱/390px 화면 검사,
  변경 유지 로직/신규 테스트 Ruff 및 JS 구문 검사를 통과했다.
- 두 기존 테스트는 배포본의 Random 지원과 단계마다 현재 시간을 재확인하는 계약에 맞췄다.
- 서비스 종료로 read-only idle 조회가 취소되면 검증된 시간표 상태를 유지하고 재시작 때
  검증한다. 통신 오류·상태 불일치는 계속 보류하며 환수 override는 취소로 해제하지 않는다.

이 검증은 코드의 orchestration과 실패 처리를 검증하며 펌웨어의 전원 전파 동작을
가정하지 않는다. 새로운 전체 환수 OFF/ON 실기 왕복은 이 문서의 테스트 결과에 포함하지 않는다.
배포 결과와 운전 관측은 별도 실행 기록으로 남긴다.

## 소스와 배포

삭제된 작업 폴더 대신 실제 scheduler 이미지의 Python과 두 대시보드 JS를
`35cc537`에 보존했다. 최초 JS 회수 대상은 canonical 파일이었다. 배포 전 API 대조에서
실제로 사용 중인 별도 release 파일의 DOM 보존 수정도 확인해 유지했다. HA 갱신이
pointerdown/up 사이에 발생하는 클릭과 키보드 focus 유지도 브라우저에서 검증했다.
변경은 `fix/maintenance-power-off` 브랜치로 보존한다.
기존 controller/dosing 이미지와 동결된 native ASYNC 하네스는 배포 대상이 아니다.

`deploy/maintenance-power-off.Dockerfile`은 검증한 기존 scheduler 이미지에 변경된
네 Python 파일만 덮는다. BASE_IMAGE를 정확한 기존 이미지에 묶고 기존 compose 설정과
volume을 유지해야 한다. HA JS 리소스 버전은 Lovelace API로 갱신한다.
