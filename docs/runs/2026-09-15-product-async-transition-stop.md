# 제품 Async 최종 역할 전환과 자동 해제의 ACK timeout

**제품 Async 결속은 미확인, 이번 자기 원복은 미완료다.** 앞선 Sync 원복 성공과 구분한다.
자격40개·schema·코드·이미지는 재사용했고 새 qualification은 수행하지 않았다.

source `5fbf58df1fc0312797cd06cf01c4f13f93f6e564`, image `187c1bd1…6bd24`로
`wm_product_async_5fbf58d_01` 한 번을 실행했다. UTC `09:43:19.834954–09:43:55.089021`, exit2.
두 Pro의 직전 원복을 reply6개로 확인한 후 시작했고 외부 JSON은 기존 async 계획 그대로다.

준비2·image2·master/Sync role2, 첫6action은 ACK/after VERIFIED다. 마지막7번째 action의
`SwitchON=true/TimerON=true/Linkage=async_slave` 명령은 `send_once → _exchange`에서
ACK timeout, UNCERTAIN이다. 첫6개 검증을 Async 최종 검증으로 승격하지 않는다.
실제 Async 명령이 적용됐는지는 UNKNOWN이며 전달 실패라고도 단정하지 않는다.

자동 자기 detach의 첫 slave atomic도 ACK timeout으로 UNCERTAIN, 나머지5개 PREPARED다.
시도8/ACK6이며, 7개 attach+6개 detach 준비수를13회 전송으로 세지 않는다. 같은 payload를
다시 보내지 않았고 Async 재시험0이다. source 최종 readback은 없으며 child before 표본은
sync_slave/TimerON/sine/F30을 보고했다. 오래된 report 가능성을 배제하지 못하므로 현재 물리
역할로 단정하지 않는다. 오류는 Auto/clock 고정 대조로 발생한 것이 아니다.

- source record SHA `f2b7686fb9555eb0e110d11613e76a7eccfd1ff3ccb0e4c6ba28b31e1b352eae`.
- child record SHA `08055f3c0c85c952a37b9db801bea40372972eb15349f1e00adacca4f3ee941f`.
- source/child 모두 recovery_required이고 자기 시작 전 reply 원본·전체432B·8controls를 보존한다.
- 원 서비스·운영 inputs 미변경. first 앱 이전 OFF·Bar classic91/F80 의무는 별도다.

**새 사실:** 이 제품 실행기의 Sync 이후3필드 Async 전환은 ACK를 얻지 못했고 같은 atomic
해제도 ACK를 얻지 못했다. 이전 실험 하네스의 Async 응답 PASS를 부정하거나 대체하지 않는다.
서로 다른 실행기/명령 구성이다. 원인은 아직 UNKNOWN이며 새 코드로 펌웨어 원인을 추정하지 않는다.

복구 범위 결손도 정적으로 확인됐다. 현재 assist는 source가 VERIFIED일 때만 허용하지만,
이번 source는 자기 원본을 온전히 소유한 채 마지막 전환만 UNCERTAIN이다. 소유 원본이 없는
것이 아니라 성공 attach 전용이라는 진입 제한에 걸린 것이다.
대안1은 이 정확한 실패 형태를 같은 자기 원본 지원에 포함하여 이미 검증된5scalar로 원복하는 것,
대안2는 사용자 앱으로 원복 준비 상태를 만들고 기존 recover를 이어가는 것이다. park하면 시험
image·역할 미확인·원복 미완료와 서비스 중단이 남는다. 재전송/초기화/timeout 변경은 어느 쪽에도 없다.
사용자 최신 완료 지시에 따라 대안1의 좁은 정정을 별도 정책에 명시하며 Async 재시험은 추가하지 않는다.

## 2026-09-15 추가 — 원복 당시 Async 보고 오프라인 확인

기존 `evidence-async-assistance-01`을 장비 접근0으로 재대조했다. master independent AFTER
12:02:43.754914Z 뒤, slave schedule_enable=False 전 BEFORE 12:02:45.994766Z에 action4
`async_slave`/ON/TimerON/sine30/F30 보고가 있다. wire SHA
`67f5673d45bf9c63faa90f91432fc425f6f869af0846afa65243bef595a79909`, status SHA
`bec5839d71e74aaf31df472fb18545071f89750157c3689874ab47fe3aedfdb5`를 원 blob·codec으로 확인했다.
slave 해제 AFTER 12:02:49.374139Z는 independent/TimerOFF다. 이 BEFORE까지 slave에 대한
assist 전송0이며 assist에 Async 설정은 없다. 다만 master 해제 뒤의 report이므로 이전
Async 명령 전달이나 내부 전이 시점은 여전히 UNKNOWN이다. ACK 부재·exit2는 소급 불변이다.
동일 순서가 역할단독 실행에서도 보존됐다. [대조와 해석 한계](2026-09-15-product-async-role-only-stop.md#2026-09-15-추가--보존-원복-프레임의-async-보고-재발견)를 함께 본다.
