# 제품 Async 연결 유지·직접 응답 확인 실패와 자기 원복

**제품 Async는 미확인이고 운영 배포는 하지 않았다. 이번 시작 전 두 Pro의 원432B·8controls는
소유 지원으로 복원했으며 별도 직접 응답6개로 다시 확인했다.** Async ACK timeout 뒤 새
reply-only 읽기도 timeout이었다. 이를 변동 Auto/시각 비교 실패나 장비 고장으로 쓰지 않는다.

## 질문·범위·실행 결속

질문은 “마지막 Async 직전 pair read의 연결을 유지하면 역할 전송이 완료되는가, ACK가 없을 때
추가 직접 응답1회로 역할 상태를 확인할 수 있는가”다. 사용자의 “수정해서 배포해” 이후 별도
정책 `1ac5405`/`16dd723`에 따른 단회이며, **제품 질문 계열 누적4회**다. 앞선 세 결과를 지우거나
질문을 좁힌 것으로 카운터를 초기화하지 않는다. 원복 수습은 새 제품 전진 시험과 별도 계상한다.

- source: `37175c0240202ba3433540f70c5753f56578c2c9`.
- image: `sha256:a9cd196695e3dfd45584d9c8e8543be63fd329ed32aa607b3ec085eb81adad83`.
- schema: `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`.
  writer·fingerprint9개·frozen native·공용 timeout/retry는 불변, 기존40receipt를 재발급하지 않았다.
- operation: `wm_product_async_reconcile_37175c0_01`.
  결정적 child/assist suffix는
  `65b30cac8166b368d6a57aebee38b16f3eb7ffbe1e412fc2bde1264a4835e117`이다.
- 실행은 기존 `attach-wavemaker` 한 번과 실패 후 자기 `assist-wavemaker-detach` 한 번이다.
  같은 config/install/Async plan/current qualifications를 사용했으며 실패 payload 재전송0이다.
- operator script SHA: `26ec431d417e0dc550767cbcde7ba9194f9be15e38a41ca50cafdd160e31668f`.
  immutable image의 source145파일 exact와 schema를 별도 확인했다. source-only build의
  RUN network-none은 실행 컨테이너의 네트워크 격리를 뜻하지 않는다.
- 이미지 검증 v1은 stdin 전달 옵션 누락으로 검사 script가 실행되지 않았다. 원 로그를 보존하고
  v2에서 실제145파일 검사를 수행했다. v2 SHA:
  `88618fd8a3b1bb9339e8b4e6ffa105b66e0e65140b3c1e1ed848d2351ce5453e`.

사전 explicit reply6개 UTC `13:56:30.215662–13:56:36.257016`에서 두 Pro independent,
원432B/8controls와 Bar 원384B를 확인했다. 앞선 Sync 상태에서 바로 새 Async를 시작한 것이
아니다. 다만 이 Async operation **내부** 순서는 독립 준비→image→master/Sync→Async다.
과거 하네스와 전 절차가 동일하다고 쓰지 않는다. 초기 TimerON/역할 복합 쓰기는 여전히 다르다.
고정 시간표의 시각은 하루 절대 시각이며 이번에는 10/15분 경계 전체를 시험하지 않았다.

## 전진 종료와 직접 관측

attach container UTC `13:59:29.116624669–14:00:08.235444952`, **exit2**.
앞6개 action은 ACK/after 일치 VERIFIED였다. 안전 준비2·image2·master/Sync 역할2에 해당한다.
두 시험 image SHA는 다음과 같다.

- master: `612f0aa8b3f6292ff9395ef2b0cbaa00d61c38de893a81f0fab9b10404a1fee1`.
- slave: `e1caad069c02015d3db883887442592989d09121f8f455a3709b5dc76423edcf`.

| 단계 | 보존된 결과 |
|---|---|
| slave Sync after, 13:59:51.187504 | action3 reply, ON/TimerON/sync_slave/sine/Flow30/F30; 명시3필드 일치 |
| Async BEFORE, 13:59:52.512134 | action4 report, sync_slave/sine/Flow30/F30; 같은 연결로 다음 전송 |
| 마지막 Linkage-only send_once | ProtocolTimeoutError, ACK 없음; source action6 UNCERTAIN |
| 새 연결의 reply-only 확인1회 | `_reconcile_async_reply`→`connect_and_capture`→read에서 ProtocolTimeoutError; 유효 reply0 |
| 자동 child 첫 slave atomic | send_once ProtocolTimeoutError, ACK 없음; 첫 action UNCERTAIN·나머지5 PREPARED |

Async payload는460B, SHA
`c02514f9291162f4b02a76df76188ddcb68d3099b289cdd97e4fd2dc21a3f2c7`로 앞선 역할 단독과 같다.
최초 실패는 **ACK 수신 timeout**이다. 이후 공개 comparison의 sync_slave는 위 BEFORE 보고이며
실패 뒤의 새 현재 상태 응답이 아니다. 원 오류와 reply 확인 오류를 각각 보존했다.
새 reply가 없어 `after_ack_error_reply` capture나 reply-confirmed VERIFIED를 만들지 않았다.

reply-only timeout은 “장비가 아무 프레임도 보내지 않음”과 “report만 보냄”을 구분하지 못한다.
기존 state-frame observer를 전달하지 않아 읽기 중 제외한 report는 보존되지 않았다. report는
skip budget8을 차감하지 않으며, 이 결과로 report0·8개 포화·펌웨어 거부를 주장하지 않는다.
AutoMode/AutoFlow/AutoFreq/장비시각은 관측으로 남았고 고정 expected에 넣지 않았다.

## 자기 소유 지원·복원과 다시 나타난 보고

실패 source/child를 먼저 export한 뒤 기존5scalar 지원을 실행했다. assist container UTC
`14:01:17.598918582–14:01:54.421836099`, **exit0**, 최종 child VERIFIED.

| 단계 | 실제 취득 완료 UTC | action | 반환 |
|---|---|---|---|
| assist master linkage after | 14:01:27.885443 | 3 | independent |
| assist slave schedule BEFORE | 14:01:29.906601 | 4 | async_slave/TimerON/sine/Flow30/F30 |
| slave schedule_enable=False after | 14:01:32.875271 | 3 | independent/TimerOFF/sine/Flow30/F30 |
| slave manual_mode after | 14:01:35.126118 | 3 | constant |
| slave manual_flow before | 14:01:35.453907 | 4 | 30, NO_CHANGE |
| slave manual_frequency after | 14:01:38.009227 | 3 | 20 |
| child 첫 atomic 재확인 | 14:01:42.101181 | 3 | 명시6필드 exact; 재전송 없이 해소 |

앞선 두 Async 실패와 마찬가지로 **master independent 이후·assist slave 전송 이전에
async_slave 보고가 보존됐다.** 이번에도 assist는 slave Async를 쓰지 않는다. 이는 보고 순서의
세 번째 사례이지 원 Async 전달/전이 시각/원인 증명이 아니다. 지연 적용·master 해제 영향·
낡거나 전이 중인 report는 미분리다. ACK와 후속 직접 reply 미확인을 이 보고로 소급 PASS로
바꾸지 않는다. master-first 중간 구간의 물리 동작은 계측하지 않았다.

지원은 ACK4/NO_CHANGE1이었다. child 첫 atomic은 fresh 일치로만 닫혔고 ACKNone 및
`assisted_target_observed_delivery_unknown`을 보존했다. 나머지5개는 ACK/after VERIFIED다.
source는 ROLLED_BACK, 마지막 Async의 ACKNone·기존 evidence 및
`native_own_baseline_restored_delivery_unknown`을 유지했다.

회계: 전진 source7 send 경계/ACK6 + 자동 child1/ACK0 + 지원4/ACK4 + 남은 child5/ACK5 =
**총 send 경계17/ACK15**다. 지원·잔여 복원만 보면 새 send9이며 준비11을 send11로 세지 않는다.
이는 실행기 송신 경계 계수이고 wire 전체를 수집해 물리 frame 수를 센 값은 아니다.

## 원복 직접 확인·증거 결속

child 최종 별도 reply에서 두 원432B와 각8controls 전부 exact였다. 추가 독립 취득은
`post-async-reconcile-01`, UTC **14:02:34.353788–14:02:39.264138**, **6/6 action3 reply**다.
실행 전과 전체 image 및 명명된 controls를 대조해 불일치0. Auto/시각은 원복 고정 목표가 아니다.

| 논리 대상 | 원복 확인 제어값 | 전체 image SHA256 | identity SHA256 |
|---|---|---|---|
| second Pro | ON/TimerON/independent/constant30/F32/Feed10/custom5 | `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99` | `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea` |
| first Pro | ON/TimerON/independent/constant40/F34/Feed10/custom5 | `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426` | `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c` |
| Bar（write0） | ON/TimerOFF/independent/classic40/F80/Feed10/FeedSwitch false/PulseTide false | `8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea` | `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1` |

증거 등급은 raw frame/status가(a), record/public JSON이(b), 당시 화면만의 정보가(c)다.
아래 private export는 실제 파일과 digest를 확인했다. 원raw는 커밋하지 않는다.

| opaque artifact | SHA256·내용 |
|---|---|
| evidence-async-reconcile-stop-01/report | `791833d209b7a6dd7fd6e934e6fe523779ee171ba6c849865f376455a4c6d15d`; 2record/83blob |
| failure source record | `18a8addbdd44e84b4baef109e26cbf29851cde938fd8e15e0a5ab9b953c24c4e` |
| failure child record | `2f0951d93a2c251ef606a8fefc4e73c6fbf6ae79fde101b66fb05f2eab356152` |
| evidence-async-reconcile-restored-01/report | `a84c255522dcedc0d7ba8c0d6874ed6c78e52d0389a858df1f29897d5773fafd`; 3record/172blob |
| restored source record | `9a1d1344a0f041aa04b84b0fb55477f4147b6ace3ecc7d169e38cfa595ea905b` |
| verified child record | `c910d9ddc39797fdb427f23158eebca548e0ef6f12607fb4e465338bbbafc07c` |
| verified assist record | `172748a560c36551cb000c081fc1375a5c1d750dc9ccde3424ccf85069e93303` |
| post-async-reconcile-01/result | `c7fd4fa7f868ee5f062aaec74e33024991b469cd759366780eb3c648da11b36b` |
| local attach terminal log | `fbf3a4284b353af37e09e47f6906b26742d5f6e21624dce064250f9fedef493d` |
| local assist terminal log | `1bf26d066bfffc832e9ce10273551fa5e4a5a223d6942456cd3722dc769b1c90` |
| offline raw audit | `861f89ffd957cfbb8a0494ba534ea9f55fe940a487fa23d676d8cb8fbc4e0c91` |

export에서 참조blob SHA를 검증하고 network-none/read-only 별도 분석으로 각action의 evidence
previous chain을 따라 capture wire를 decode해 status/action 일치를 재대조했다. 원record 불변,
권한 완화0. final의 누적master 행은 추가 표본으로 중복 계상하지 않는다. 위 취득 구간의 관측이며
연속 감시를 주장하지 않는다.

## 이번 실행으로 새로 확정된 사실·한계

1. 마지막 read 연결을 유지해도 이 조건에서 Async ACK를 받지 못했다. 그 변경만으로 제품의
   완료가 성립하지 않음은 관측됐지만 연결 수명이 원인과 무관하다는 인과 소거는 아니다.
2. ACK 오류 직후 새 인증 연결의 직접 상태 읽기1회가 이번에는 성립하지 않았다. 보고만으로
   성공을 만드는 대신 정지했다. 위 두 timeout 밑의 실제 frame 흐름은 미보존이다.
3. 복구 중 master 해제 뒤 slave Async 보고가 재현됐고, 기존 지원으로 자기 원본을 완전히
   복원할 수 있었다. 새로운 정상 Async 결속·해제 완료나 과거 실패 취소를 뜻하지 않는다.

이번은 정보0은 아니지만 제품 누적4회다. 과거 실험 하네스의 응답 PASS와 제품 미확인은
서로 다른 계약으로 유지한다. 물리 출력·역위상·파형 소유·생물 안전은 이 자료로 단정하지 않는다.

## §7 정지·보고와 배포 경계

추가5번째 전진·source/test 정정·timeout/retry 변경·새 값 탐색은 수행하지 않는다. 이번 구현/
실장 권한은 소진됐다. 다음은 별도 범위 결정이 필요한 대안이며 이번에 실행한 절차가 아니다.

1. **통신 관측을 먼저 설계**: 이미 있는 state-frame observer 등으로 같은 교환에서 reply/report·
   연결·응답 대상을 보존해 “무수신인가, 비대상 frame만 수신인가”를 구분한다. 먼저 source-only
   관측 계약·비밀값 처리·새로 확인할 질문을 검토하고, 새 실기 없이 확인 불가한 부분만 남긴다.
   기존 timer/gate를 추측으로 완화하거나 이번 id/payload를 재전송하지 않는다.
2. **Async 제품화를 park하고 서비스부터 복구/분리 배포**: 기존 정확한 서비스 복귀 또는 검증된
   개별 제어만의 새 generation을 따로 선택한다. Async 완료나 앱 전체 동등으로 표시하지 않는다.
   기존6개 제어 보존을 확인하고, 부분 배포는 사용자 목적을 조용히 축소한 것으로 처리하지 않는다.

park하면 Pro/Bar 자격40receipt·기존 제어 보존 후보와 이번 원복 증거는 남지만 제품 Async
end-to-end와 신규 운영 배포는 열린다. 이번에는 운영 generation 교체0, 기존 서비스 stopped다.
운영 config/install/qualifications의 전후 SHA3개는 불변이다. first 앱 최종OFF와 Bar 앱 준비
이전classic91/F80 복귀는 자기 attach baseline 복원과 다른 의무이며 완료됐다고 쓰지 않는다.
새 앱 완료 회신·현장 선언은 만들지 않았고, 배포 미완료와 장비 자기 원복 완료를 분리해 보고한다.
