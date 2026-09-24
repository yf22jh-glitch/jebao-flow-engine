# run: 2026-09-25-pro-linked-common-flow-single-attempt

## 질문과 범위

하단 Pro가 Master에 Async 결속된 상태에서, 로컬 코드의 `slave_common_flow` 단일
`Flow=45` 명령으로 현재 보고값 50을 바꿀 수 있는지 확인했다. 현장 운영자가 그 단회
명령을 승인·감시했다. 슬롯 이미지와 상단 장비에는 쓰지 않았다.

UTC 구간: 2026-09-24 16:11:15–16:11:56. 하단 physical identity binding SHA-256:
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
비밀값과 raw frame은 저장소에 넣지 않았다.

## 보존 증거와 순서

기존 일반 scalar 경로는 0x03 직접 조회만 기다리느라 첫 write 전에 timeout했다.
이번에는 하단 Flow 단일 작업에서만 0x04 보고 프레임도 읽는 writer를 사용했다.
장비 identity·설정 범위 확인, 단회 송신, 기존 lease·durable journal·복구 규칙은
`LocalControlCoordinator` 그대로 사용했다. 이 실기에 source/image 재배포는 없었다.

1. 첫 보고 프레임 action 0x04에서 하단 `Linkage=async_slave`, `TimerON=false`,
   `SwitchON=true`, `Flow=50`을 읽었다. 원본 wire blob SHA-256은
   `a0d3b04efbf43dedde0ef6a6479f7f3184f34f377c5bd177e43be1f5f482a5c3`.
2. 저널을 먼저 만든 뒤 정확히 한 번 `{Flow:45}` 송신을 시도했다. forward payload
   SHA-256은 `f3f85a1c7499c247b6d5b6f72f34827b9c34256b204ae378fafcc430de315af6`.
   5초 내 ACK가 없어 action은 `UNCERTAIN`, transaction은 `RECOVERY_REQUIRED`로 남았다.
   ACK가 없어 장비 수용은 확정하지 않는다. 동일 forward를 재송신하지 않았다.
3. 새 연결의 보고 프레임은 여전히 `Flow=50`, Async/TimerOFF/PowerON이었다. 그 원본
   wire blob SHA-256은
   `a0d3b04efbf43dedde0ef6a6479f7f3184f34f377c5bd177e43be1f5f482a5c3`.
4. 복구는 새 연결에서 별도 action 0x04 raw를 읽어 baseline Flow50을 확인한 뒤
   **추가 송신 0회**로 `ROLLED_BACK` 종결했다. 그 원본 wire blob SHA-256은
   `c13881c9e52c91b29bbf75d76754ff3d6ede6b0e600da9aa6c5d5beb65debff4`.
   최종 journal digest는
   `a7ec7985a73d13ea2f97878a86e4b430446079abe382b11a53fefdef56c52352`.

## 이 실행으로 새로 확정된 사실

하단은 report-capable fresh read로 즉시 Flow50을 돌려주므로 이전의 *첫 write 전*
timeout은 우회할 수 있다. 그러나 결속 상태의 `Flow=45` 단일 송신은 ACK를 받지
못했고, 후속 두 fresh report에도 Flow50이었다. 따라서 **이번 방법으로 45 적용은
확인되지 않았다.** wire를 장비가 실제 수용했는지와 물리 유량은 미확정이다.
다음 시도는 같은 프레임 반복이 아니라 Master 해제 상태에서 Flow를 바꿔 재결속하는
별도 경로로 분리한다.
