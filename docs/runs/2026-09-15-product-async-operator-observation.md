# run: product-async-operator-observation

**Async 명령1회 뒤 사용자 관찰 대기다. 원복하지 않았고, 실제 전환·물리 동작·배포는 미확인이다.**
사용자는 최대2분 후 자동 원복 제안에 “아니 걍 원복하지말고 기다리게 해”라고 명시했다.
정책 `cd50c61`, 구현 `98601079d1360be91b4d9799347b18370d258386`에 결속된 신규 단회이며
제품 계열 누적6회다. 앞선5회와 [직전 자기 원복](2026-09-15-product-async-staged-activation.md)을
소급 변경하지 않는다. 이번 전환 뒤 자동 원복·재전송·새 전진은0이다.

## 실행과 현재 상태

- operation: `wm_operator_observe_9860107_01`
- 명시 옵션: `attach-wavemaker --await-operator-observation`, 기존 staged9단계·동일 Pro pair/plan
- 컨테이너 UTC: 2026-09-15T15:55:09.140284596Z–15:55:50.702351648Z, exit2
- 전진 action0–7:8개 VERIFIED, 각각 ACK·after 확인. 마지막 action8:UNCERTAIN·ACKNone
- transaction:RECOVERY_REQUIRED, accepted=false, current_state_verified=false
- awaiting_operator_observation=true, observation_reason=operator_requested_no_automatic_restore
- automatic_restore=false, restoration_complete=false, next_action=await_operator_instruction

마지막 Async의 ACK 및 후속 직접reply 확인을 얻지 못했다. 관찰 대기 표시가 그 실패를
성공으로 바꾸지는 않는다. 공개 comparison의 sync_slave는 마지막 action의 BEFORE에서 온 값이며,
그 값을 현재 내부 상태라고 단정하지 않는다. 아직 사용자의 실제 관찰 결과는 받지 않았다.

두 Pro에는 시험 image와 TimerON, 마지막으로 확인된 master/Sync 역할 및 시도한 Async가 남았다.
실제 현재 slave 역할은 미확정이다. 시간표가 동작할 수 있으므로 준비 manual30/31·F20에
출력이 고정됐다고 주장하지 않는다. 앱 표시·물리 동작·보고값·명령 ACK는 별도다.
전환 후 사용자에게 즉시 관찰 가능함과 원복0을 알렸다. 이때 장비 추가 read/write는 하지 않았다.

프로세스는 종료됐지만 사용자 관찰 상태는 유지한다. 이 옵션의 마지막 시도 뒤 취소/프로세스
종료도 자동 원복하지 않는다. 같은 attach id 또는 status 재조회는 보존 기록만 읽는다.
사용자가 명시적으로 원복을 요청할 때 기존 `recover-wavemaker --request-id
wm_operator_observe_9860107_01`를 사용하며, 필요하면 기존 소유 assist로 잇는다. 관찰 완료만으로
원복 승인을 추정하지 않는다. 자기 두432B/8controls 원본은 보존돼 있고 복구 권한을 삭제하지 않았다.
향후 source ROLLED_BACK은 원복 회계이며, 이번 수용 여부는 final/증거 chain으로 따로 읽어야 한다.

## 회계·보존 근거

전진9 send 경계/8 ACK, 마지막 Async payload 재전송0, child0/assist0/Bar write0이다.
9는 호출 경계 계수이지 물리 TCP/UART frame 수가 아니다. ACK body는0B이며 원ACK wire가 아니라
command/sequence 검증을 거친 구현의 수신 주장이다. 명령 전달 UNKNOWN을 유지한다.

`evidence-operator-observation-01`은169 blob+source1+report1=171파일이다.
export는 장비 접촉 없이 안전 볼륨을 read-only로 읽고 모든 참조 blob의 digest를 대조했다.
source만 존재하고 child/assist 기록은 없다. 원본 raw와 구조화 증거를 분리하며 이번 요약은
보존된 source/public 결과(b)를 인용한다. 새 실패 frame census나 상태 생성 시각은 분석하지 않았다.

| 보존물 | SHA-256 |
|---|---|
| source revision79 | `a6b56aac6a98ad3fee6019282f1962ed69dc26d5dd65ca78400debb877749484` |
| context | `1d94fd307c73a77a8de0b6f6361b49e07b42a3d0236f9ce73b8a6efc99356ed3` |
| export report | `8b0b9b636b6a5bc9518ec0f232a1c0a80517d69c014c53be831bc6c05a311f6d` |
| public stdout | `1e1bf5f01dc92a3978a508492bf8e66c76fe2a6d7694cca71a02030cdb390f81` |
| stderr | `8f546b5aeeb1d456baeff81c0289ef6f17207803ebdd8186a8e1d6a431df6a09` |

시작 전 `operator-observation-baseline-01`은15:54:23.353500–15:54:28.370039Z,6개 직접reply,
각2표본의 두 Pro/Bar다. baseline JSONL SHA는
`18e05b6914606a9ae457c8bb3b4ace919287daa3db8951c2fc603da7c4c0bd6b`다.
두 Pro는 independent/ON/TimerON/constant, master30/F32·slave40/F34, Feed10/custom5였다.
두 원432B SHA는`60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`와
`c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`다.
Bar는independent/ON/TimerOFF/classic40/F80, 원384B SHA
`8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea`다. Auto/시각은 고정 목표가 아니다.
identity binding은 master`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`,
slave`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
Bar`d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`다.
fault 보고0은 물리·생물 안전 증명이 아니며, 여섯 표본을 연속 감시로 쓰지 않는다.

## 구현·운영·열린 질문

이미지`e4d32e1adc9f0fffdc197235a30bc000441e8eedf01bc2441a665f2b508044fe`는network-none/
source-only build이며 설치된146 source hash가 작업트리와 일치했다. schema는`81ee699d…`로
불변이다. 기존40receipt·20자격 pair와 동일 plan을 쓰며 CLI의 current 자격 검사를 통과했다.
full2919 PASS/5 FAIL, 신규9 PASS와 독립 리뷰 결과는 [구현 문서](../wavemaker-operator-observation.md)에
결속된다. 전체 pytest 통과나 새 장비 수용으로 인용하지 않는다. 실기 뒤 src/tests 수정0이다.

기존 runtime inputs3개는 직전 원복 export와 hash가 동일하고 원 서비스는 기동하지 않았다.
보류 기록은 공용 gate에 의해 Bar를 포함한 다른 local-control write/qualification을 막는다.
이를 실행 전에 사용자에게 알렸다. 앱 경로의 관찰 가능 여부와 이 gate는 별개다.
배포0, 자기 원복 미실행, first 최종 앱OFF·Bar 앱이전classic91/F80 복귀·원서비스 의무도 별도다.

새로 확정된 것은 최종 Async 시도와 응답 미확인 뒤에도 사용자 명시 옵션으로 전진9에서 멈추고
child/assist를 만들지 않아 관찰 상태를 남겼다는 점이다. 펌웨어 원인이 새로 밝혀진 것은 아니다.
사용자 관찰이 진행 중이므로 기능 질문의 terminal 판정을 선취하지 않는다. 추가 전진 권한은 없으며
관찰 결과를 받거나 명시적 자기 원복을 요청받는 두 경로만 열린다. 아무 지시가 없으면 상태와
원본/미완료 기록을 유지하며 자동 명령·타이머 원복·배포를 하지 않는다.
