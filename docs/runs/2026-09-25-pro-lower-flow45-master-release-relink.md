# run: 2026-09-25-pro-lower-flow45-master-release-relink

## 질문과 범위

상단 Master가 결속된 하단 Async의 별도 `Flow=50`을 앱 없이 로컬 코드로 45로
바꿀 수 있는지 확인했다. 현장 운영자가 장비를 감시하며 승인했다. 직전 결속 상태의
`{Flow:45}` 단독 시도는 ACK가 없고 하단 보고값도 50이었다
([별도 기록](2026-09-25-pro-linked-common-flow-single-attempt.md)). 이번에는 앱에서
값이 바뀌었던 순서와 같이 상단 역할을 잠시 해제→하단 Flow 변경→상단 재결속했다.
두 장비 이외의 명령·시간표 write는 없었다.

UTC 구간: 2026-09-24 16:14:27–16:16경. 상단/하단 physical identity binding SHA-256은
각각 `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
원 주소·ID·raw frame은 저장소에 기록하지 않았다.

## 실제 명령과 보고

세 명령은 모두 현행 `LocalControlCoordinator.execute`의 identity 검증, 단일 송신,
durable journal, 수신 확인·readback 경로로 각각 실행됐다. 개별 요청은 모두
`VERIFIED`, 실제 send는 각 1회였다.

| UTC / 명령 | 결과와 직후 fresh raw | journal digest |
|---|---|---|
| 16:14:27 상단 `{Linkage:independent}` | 상단 action 0x03: independent, PowerON, TimerON; 하단 action 0x03: Async·Flow50 | `14cdf1b5ed41ba170538458a6917d45d742ab5fa4e3ec05e7245876b5be15f34` |
| 16:15:05 하단 `{Flow:45}` | 하단 action 0x03: Async·Flow45·TimerOFF·PowerON | `b5dbe7890bdc0e544201797540b76c18c4eb3c74159a6623481027b1873bb89c` |
| 16:15:38 상단 `{Linkage:master}` | 상단 action 0x03: Master·TimerON·PowerON; 하단 action 0x04: Async·Flow45·TimerOFF·PowerON | `40ab0f7db765eb51a452aaf2e6c5773237cfae087b937539aabb3453a81b56d6` |

재결속 후 별도의 fresh pair에서도 상단 Master·하단 Async가 유지되고, 하단 Flow는
45였다. 마지막 원본 wire blob digest는 상단
`9e9ab21def57844b33f54d795a066e3c9cbe7ee6319f67fc49b9906243cfe186`,
하단 `b45320cc8bd33beb77e9a42276b9fe808405562c7b151f0a25bb312ca39b908c`.
둘 다 보고된 fault는 0. 상단 432B image digest
`118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9`,
하단 all-EE image digest
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`로
실행 전 기록과 동일했다. 상단 Flow40/활성 Auto Sine35/F80도 그대로였다.

## 이 실행으로 새로 확정된 사실

**상단 Master를 해제하면 하단이 직접 상태 응답(0x03)을 주고, 그 상태의 단일
`Flow=45` 명령은 확인·적용된다. 상단을 다시 Master로 묶어도 하단 Async의 별도 보고
Flow45는 유지된다.** 즉 값은 슬롯 432B 안이 아니라 별도 Flow datapoint에 있었고,
앱 화면 제한 때문에 장비 명령 방법이 없는 것은 아니다. 반대로 결속 상태에서 단일
Flow 명령이 수용된다는 증거는 이번에도 없다. 물리 유량과 anti-phase 효과는 raw
datapoint만으로 확정하지 않는다.
