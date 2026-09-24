# run: 2026-09-25-pro-async-common-flow-50-and-service-timeout

## 질문과 범위

하단 Pro의 앱 Async Flow를 상단 Master와 분리해 50으로 바꿀 수 있는지, 다시 Master를
결속해도 그 값이 남는지 확인했다. 이어 사용자가 하단 `slave_common_flow=45` 단회 원격
명령을 승인했다. 앱 조작은 사용자가 수행했고, 에이전트는 활성 local-control MQTT 경로로만
요청했다. raw 프로토콜 우회 송신, source 변경, 다른 장비 명령은 0이다.

UTC 구간은 2026-09-24 15:29:56–15:50:07이다. 상단/하단 physical identity binding SHA-256은
각각 `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`다. 실제 주소·ID·
MQTT 인증 정보와 raw frame은 저장소에 넣지 않았다.
원본 raw의 opaque series id는 `JFS-24c71d44825cf62b735237d315c42bf2`다.

## 앱에서 Flow 50으로 전환

기존 5분 raw anchor의 ordinal `000167`(15:29:56–15:30:07)은 상단 Master·Auto Sine35/F80,
하단 Async·TimerOFF·manual Sine40/F80·빈 432B image를 보존했다. 사용자는 앱에서 상단
Master를 해제하고 하단 Async Flow를 조정한 뒤 Master를 다시 연결했다고 보고했다.

전량 MQTT 상태 이벤트는 다음을 보존했다. 이벤트 수신 시각은 장비 내부 전이 시각이 아니라
관측 시각이다.

| 보존 event / UTC | 하단 manual Flow | 상단 Linkage |
|---|---:|---|
| `000955-events.json` / 15:33:44 | 40→49 | master→independent |
| `000956-events.json` / 15:33:57 | 49→50 | independent→master |

두 event SHA-256은 각각
`a6a69ca94f260ba0c46cf90850a7d5ef3750272e67d40e933838d1b36e19f4ef`,
`56bb6fbcb2e0211b5bbeeab98d764ed5ac8068338af591b3f8d09e79e032d973`다.
`slave_common_flow` 상태도 같은 관측에서 40→49→50으로 변했다. 앱 화면에서 Flow 50이
보인다는 것은 사용자 관측이며, 물리 유량 측정은 아니다.

raw anchor ordinal `000168`(15:34:56–15:35:07)은 상단 Master·Auto Sine35/F80·manual
Flow40·31슬롯, 하단 Async·TimerOFF·manual Sine50/F80·0슬롯을 원본 frame으로 확인했다.
상단 image SHA는 `118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9`,
하단은 all-EE `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`로
불변이었다. fault는 둘 다 0이다. 하단 raw frame SHA는
`56972e20a93175b43836cdd70ca7a2dca7a32e31d549064d54cd12501e926811`이다.
따라서 **하단 보고 Flow 50은 Master 재결속 뒤에도 유지되며**, 당시 상단의 manual Flow40 및
활성 AutoFlow35와 다르다. linked 상태의 하단 물리 유량이 정확히 50이라는 주장은 아니다.

## 에이전트의 45 요청 결과 — 장비 write 0

현재 배포 config에서 하단 `slave_common_flow`는 identity-bound control로 노출되고,
설정 Flow 범위는 30..100이다. 하단에 겹치는 미완료 journal은 0이었다. 직전 raw ordinal
`000169`(15:39:56–15:40:07)도 Master/Async와 하단 Flow50을 확인했다.

에이전트는 하단 `slave_common_flow/set`에 ASCII `45`, QoS0, non-retained를 **두 번** 발행했다.
첫 요청은 사전 조회 단계에서 실패하고 새 journal이 0인 것을 확인한 뒤에만 한 번 재시도했다.

| 요청/UTC | ingress artifact SHA-256 | 서비스 응답 |
|---|---|---|
| 15:42:26 | `00001156-message.json` `7a4ee0b71169d6fff4ba61216df9ea1642f010ec43813b554ae6ad3ced605ca3` | 15:42:37 `command_rejected_ProtocolTimeoutError` |
| 15:45:27 | `00001171-message.json` `76fa5d4a7d7551e709fb48c9a5eebcee23270b90ac5fc5435697b256819b1440` | 15:45:39 같은 거부 |

거부 결과 artifact `00001157-message.json`과 `00001173-message.json`의 SHA-256은 각각
`031731c0207fe0803ebecaa2713e9bb69c3c7bdcfe10a029bf5cf3f36fb9c293`,
`6a9663e3f15703d1f4315da188288f5c7136e6cd6ef159f808bb254b6814bb9e`다.
local-control journal operation 파일은 요청 전후 **125개로 동일**했고 새 request는 없다.
`coordinator.execute`는 fresh `connect_and_read` 뒤에 journal을 만들고, send는 journal 생성
뒤에만 하므로 두 요청 모두 **장비 control frame 전송 이전에 종료**됐다. 재송신이나 raw 우회는
없었다. ordinal `000171`(15:49:56–15:50:07)에서 하단은 여전히 Async·Flow50·TimerOFF·
0슬롯, 상단은 Master·Auto Sine35이고 fault 0이다. 하단 raw SHA는
`28e91a7113a88d12cc6628292ab633483c452ce3ad280f5520e93daed576ea9c`다.

## 원인 범위와 판정

현재 일반 scalar writer의 `connect_and_read`와 `readback`은 `accept_reports=False`라
`0x03` 직접 상태 응답만 채택한다. 같은 구간의 하단 raw anchor는 유효한 `0x04` 자동 보고를
받았고 `explicit_reply_observed=False`였으며, 상단은 `0x03` 직접 응답을 받았다. 이 불일치는
두 11초 timeout의 **강한 원인 후보**다. 다만 서비스가 실제로 어느 TCP 단계에서 timeout했는지
frame observer로 보존하지 않았으므로 확정으로 올리지 않는다.

**이 실행으로 확정된 사실:** 하단의 별도 Flow50은 앱 해제·재결속 뒤 Master의 Flow40/Auto35로
덮이지 않았다. 반면 우리 원격 scalar 경로는 두 번 모두 첫 write 전에 timeout하여 45를
적용하지 못했다. 보고값의 독립성과 물리 유량·슬롯별 Flow 독립 적용은 다른 질문이다.
다음 코드 작업을 한다면 초기 status·송신 뒤 readback·복구에서 `0x04`를 어떻게 신선한
증거로 소유할지 함께 다뤄야 한다. 이번 실행에서는 변경하지 않았다.
