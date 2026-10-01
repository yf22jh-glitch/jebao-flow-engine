# 외부 수동 시간표의 ACK 누락 판정

2026-10-01 사용자가 요청한 계약이다. native ASYNC 하네스와 qualification의 ACK 요구는
변경하지 않으며, 외부 수동 시간표의 단일 scalar child에만 적용한다.

1. 변경 명령은 기존 write-ahead journal과 single-send 경로로 한 번만 시도한다.
2. 현재 ACK 제한시간 5초를 유지한다. ACK가 없으면 원 실패 기록을 먼저 보존한다.
3. 기존 연결을 닫고 exact identity로 재탐색·인증하여 상태를 조회한다.
4. 일시적인 조회/연결 실패는 성공할 때까지 재시도한다. 대기 간격은 1, 2, 5초이며
   이후 5초로 유지한다. 프로세스 종료 요청은 조회 재시도를 끝낼 수 있지만 변경 명령을
   재송신하거나 원 실패 기록을 삭제하지 않는다.
5. 원 명령의 expected scalar fields가 일치하면 `readback_confirmed`로 종결한다.
   ACK 성공·원 명령 전달의 인과를 주장하지 않고, 새 상태가 목표와 같다는 것만 확인한다.
6. 일치하지 않으면 `abandoned`로 종결하여 해당 명령을 드롭한다. 실제 장비 상태와
   원 명령을 보존하며, 이 결과를 ACK 성공이나 현재 시간표 전체 성공으로 표시하지 않는다.
7. 장비가 UDP discovery에 응답하는 것과 로컬 인증/상태 조회가 되는 것은 별도 사실이다.
   조회 재시도와 최근 identity 응답 시각을 표시한다. 응답이 없다는 사실만으로 전원 OFF나
   물리 고장을 단정하지 않는다.
8. 드롭된 명령에 의존하는 나머지 단계는 실행하지 않는다. 예를 들어 Master 해제가
   적용되지 않았는데 Slave Flow 쓰기를 이어가지 않는다. 수동 재활성화 권한도 추가하지 않는다.

원 UNCERTAIN record의 content-addressed 사본, ACK 부재, fresh raw 상태를 함께 보존한다.
`readback_confirmed`는 자격 영수증을 발급하는 `verified`와 다르다. restart와 취소는 기존
single-write 저널을 숨기거나 같은 request id를 다시 송신하는 근거가 아니다.

관측 근거: [ACK 지연과 현재 누락 경로 감사](runs/2026-10-01-control-ack-readonly-audit.md).
