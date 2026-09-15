# 역할 단독 Async 실패 뒤 소유 scalar 복원

**두 Pro는 이번 attach 전 원432B·8controls로 복원됐다. Async 수용은 미확인이고 배포는
미완료다.** [직전 실패](2026-09-15-product-async-role-only-stop.md)를 PASS로 바꾸지 않는다.
이 작업은 새 Async 전진 시험이 아니라 자기 원복 의무의 수습이다.

## 실행·원본·미상 보존

동일 source `be8bfcb3eb352fba2768224dcce9e0f37d631580`, image
`dc6782b19270dc34bccda5315142a58158645a226994f2cc071526319c3bce98`를 사용했다.
추가 코드·writer·schema·qualification 변경0. 자기 attach
`wm_product_async_role_only_be8bfcb_01`의 결정적 assist를 한 번 실행했다.
UTC `12:15:22.245043–12:16:00.278751`, exit0, 최종 child accepted/current_state_verified true.

- assist5단계: master independent·slave schedule_enable=False는 ACK/after VERIFIED2,
  slave Mode/Flow/Frequency는 실제 일치로 NO_CHANGE3. safe 준비값은 constant30/F20이다.
- 기존 child 첫 atomic은 fresh6필드 일치로 전송 없이 VERIFIED. ACK None과
  `assisted_target_observed_delivery_unknown`을 유지하고 assist 출처를 결속했다.
- child 나머지5단계는 ACK/after VERIFIED. 이 복구에서 새 send 경계7/ACK7이며,
  전체 준비11을 전송11로 세지 않는다. 실패한 Async/atomic action 재전송0이다.
- source는 ROLLED_BACK. 마지막 Async ACK None·기존 evidence를 보존했고
  `native_own_baseline_restored_delivery_unknown`을 남겼다. 원 명령 도달 UNKNOWN은 유지한다.
- 별도 최종 reply에서 두 원본 image와 각8controls 전부 exact. master-first 중간 상태를
  거쳤으며 그 구간의 slave 물리 동작은 계측하지 않았다. TimerOFF 중 수동 운전을 거쳐 원복했다.

| 보존 record | phase | SHA-256 |
|---|---|---|
| source | rolled_back | `f385390eec0e4f8f0b23ccbb6affc6ac08dccc099504a59759e4d2a2b347f411` |
| child | verified | `a266630842793b29f8e7125e5a9967fc3192c4b49f57563b696b5be4b5fc3d2c` |
| assist | verified | `fd3fc84ceaf3a002d3b9098f5acbb8dc5d6263be45bf5c02fb42b462da46ab43` |

export `evidence-async-role-only-restored-01`: record3/blob163/report1,
report SHA `e0c9bdc86e6fda24e9ccff5cd8c3c6b57c2a3c42cd414264e5286ed071e1cf1b`.
실행 출력 SHA `89d97ac2d88c8412841670b08f96b6116b4d8a380e8eaa865aac88e3e23474ce`.
별도 offline audit은 previous 체인·capture/status/wire·ACK body·미상 진단·사후 raw 해시를
읽기 전용으로 대조했다. audit SHA `be49f2b5768323b24af613e22ef919187a00a5c9aa244b872c9c14ba0d30481a`.
보호 root/UID/권한 변경0, store 초기화0이며 원 실패 export를 덮어쓰지 않았다.

## 추가 reply 확인과 현재 상태

추가 command0 fresh6 reply(action3)의 UTC span은 `12:16:42.777832–12:16:47.354066`이다.
`post-async-role-only-restored-01`의 result SHA는
`cf16cab97563015dcdc3d266b1058f4de5ab282234bbdf34fb6b7251e938928b`이며, 원 프레임/상태/image
파일을 실제로 해시 대조했다(a). 각 장비2표본 모두 다음 값이고 보고 fault는 모두 false다.

| 대상 | 제어값 | 원 시간표 SHA-256 |
|---|---|---|
| second Pro | ON/TimerON/independent/constant30/F32·Feed10/custom5 | `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99` |
| first Pro | ON/TimerON/independent/constant40/F34·Feed10/custom5 | `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426` |
| Bar | ON/TimerOFF/independent/classic40/F80 | `8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea` |

Bar identity SHA `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
Pro identity는 직전 실패 기록과 같다. Auto/시각은 원복 고정 목표가 아니다. fault 보고로 물리
유량·생물 안전을 단정하지 않는다. 자동 로그 보존 및 수동 조회이지 연속 감시가 아니다.
원 운영 config/manifest/qualification 세 digest는 불변이며 원 서비스는 여전히 stopped다.
기존6pairs를 포함한 새20pairs/40receipts·ignored0 자격 성공은 이번 Async 실패와 별개다.

**새 사실:** 새 Linkage-only 실패 형태도 자기 원본 지원 경로에서 재전송 없이 exact 복원이
실제로 성립했다. 제품 질문 누적3회와 원복 수습 횟수를 분리한다. 이번 제한된 전진 권한은 소진했다.
추가 Async forward/source 수정 없이 §7로 보고한다. 다음 대안은 다음 두 가지다.

1. 기존 성공 경로의 TimerON/역할 분리와 세션·읽기 순서를 전체 대조해 제품 실행기의 변경
   범위를 먼저 결정한다. 원인 추측이나 timeout 확대 대신 실제 차이를 대상으로 하며,
   재개한다면 새로 남길 전송/응답 증거와 원복 계약을 함께 정한다. 지금 승인된 네 번째 실기는 없다.
2. 제품 Async를 park하고 자격 있는 개별 제어를 보존한 운영 배포를 분리 진행한다.
   이 경우 Async 완료를 주장하지 않으며 사용자의 전체 목표를 완료로 닫지 않는다.

park해도 이번 시험 image/역할의 원복 미완료는 남지 않는다. 단 first 앱 이전 TimerOFF,
Bar 앱 이전 classic91/F80, 원 서비스 복귀/대체 의무는 별개로 남는다. 새 generation은 원
서비스의 동일 복귀가 아니라 대체이며 아직 실행하지 않았다. factory reset·무인 반복·
상시 group arming·기존 미상 기록 삭제는 어느 대안에도 자동 포함되지 않는다.
