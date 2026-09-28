# run: 2026-09-28-pro-upper-linked-flow30-direct-attempt

## 질문과 범위

하단 Pro가 Master, 상단 Pro가 Async Slave인 상태에서 상단의 `Flow` 필드만
단일 명령 `{Flow:30}`으로 직접 변경할 수 있는가. 사용자가 명시적으로 단독
필드 명령을 요청했다. Linkage·전원·시간표와 하단 장비에 대한 제어 명령은
범위 밖이었다.

- UTC 실행·관측 구간: 2026-09-28 14:56:51–14:58:00.
- 상단 physical identity binding SHA-256:
  `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- request id: `pro_upper_linked_flow30_20260928_01`.
- 사용자는 직전 변경을 직접 확인한 뒤 이번 단독 송신을 지시했다. 이번
  operation 직전의 현장 감시·즉시 전원 차단 가능 여부를 별도로 재확인하지
  않았으므로 그 조건을 확인했다고 주장하지 않는다.
- 운영 source/image를 변경하지 않았다. 결속 Slave의 action 0x04 보고를
  수용하는 writer adapter를 기존 `LocalControlCoordinator.execute`에 전달했다.
  identity 권한·설정 출력 범위·lease·durable journal·단일 송신·rollback
  권한은 기존 경로를 사용했다. raw 프레임·사설 주소는 저장소에 넣지 않았다.

## 전송과 응답

송신 전 fresh pair에서 하단은 직접 reply action 3·Master·power ON·TimerON,
상단은 report action 4·Async Slave·power ON·TimerOFF·`Flow=37`이었다.
양쪽 fault 보고는 없었다. 상단 baseline status SHA-256은
`362516095c49a7faebdcae10b51c8ab60b99afee1d71594cca64b08939d12bce`다.

상단 `slave_common_flow=30` intent를 실행해 **`{Flow:30}` 한 프레임만**
송신 시도했다. forward payload SHA-256은
`60187dbde28080ad3d869307be3bb0ce984d9660982f9a2c7fd9ef579771cbd0`이다.
5초 동안 ACK를 받지 못해 action은 `UNCERTAIN`, transaction은
`RECOVERY_REQUIRED`가 됐다. 해당 record의 SHA-256은
`6f2c5fda60f3afdd4de80c3d44ab36189626a2d53bc44b6e7b254c957c4bb583`이다.
같은 frame은 재송신하지 않았고 복구/rollback 명령도 보내지 않았다.

송신 직후 fresh pair는 하단 Master·상단 Async Slave를 유지했고, 상단
`Flow=37`이었다. 그보다 뒤의 별도 fresh report action 4에서도 상단은
Async Slave·power ON·TimerOFF·`Flow=37`·fault 0을 보고했다.
후속 status payload SHA-256은
`9cfd10eff63e27b42372d40beeeee37f8f998a4bca15a3db85688c2abcd52e01`이다.
baseline과 status 전체 digest가 다른 이유는 이 실행만으로 특정하지 않는다.
forward ACK가 없으므로 장비가 제어 frame을 수용했는지는 미확정이다.

저널 파일의 request id·두 시각(14:56:51.372737Z, 14:56:56.401700Z)·
phase·payload digest를 다시 읽어 확인했다(등급 b). 위 fresh raw는 실행 중
직접 디코드했으나 이 실행용 별도 raw artifact로 고정하지 않았으므로 상태
요약은 등급 c이다. 물리 유량은 측정하지 않았다.

## 이 실행으로 새로 확인한 사실

이번 결속 상태에서 상단 `Flow 37→30` 단일 명령은 ACK를 받지 못했고,
두 차례 후속 보고값도 37이었다. 따라서 **이 방법으로 30 적용은 확인되지
않았다.** 역할 해제→Flow 변경→재결속의 성공과 직접 명령의 성공은
서로 다른 계약이다. 이번 미완료 저널은 그대로 보존되므로 상단의 다음
local-control write 전에 별도 진단·복구 판단이 필요하다.
