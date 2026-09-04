# run: 2026-09-04-exact-restore-qualification-4dcd8364

- 목적(한 문장): 앱 주도 ASYNC 관측 전에 standalone exact-restore가 두 Pro의 현재 controls와
  432-byte schedule image를 byte-exact하게 복원하는지, 사용하지 않는 schedule slot의
  `ZERO ↔ EE` 표현만 바꾸는 sentinel cycle로 qualification한다.
- 이 실행이 답하려던 질문: "이중 fresh baseline에 결속된 standalone 경로가 bounded temporary
  state를 만든 뒤 역할별 원 schedule bytes와 outer controls를 exactly-once로 복원하고 정식
  qualification receipt를 발급할 수 있는가?"
- 실행한 commit SHA: `1393978b95516a44a250cbca8bb29bc59d970c3f`
- 사용한 명령·주요 파라미터:
  - exact source를 bind한 Linux/amd64 image
    `sha256:00da2febc043b7e3edf8c3922bf57ce99647ab03749771d8d6f519a29ffad6ba`
  - private manifest SHA-256
    `feebbd0bf279c0189a6365b7f19e21d528e8284584470e6e22b132b257da9cb8`
  - sentinel operation SHA-256
    `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`
  - baseline SHA-256
    `d4887072a4eda36d9674fb55df01b89512382c04123803bb0f445c70d1affa4d`
  - action-plan SHA-256
    `597042b56701a3796206dd7bd98f1bf98c3435622789cca95c7504b3824e2226`
  - 두 장비 모두 safe fallback `Constant / Flow 30 / Frequency 20`; 역할별 latent manual
    `30`·`89`를 exact 값으로 지목
  - 역할별 attended maximum `60`·`89`, configured range `30..100`, step `1`
  - `max_observation_age_seconds=1800`, `max_final_pair_gap_seconds=20`,
    network/discovery timeout `5초`, identity age `5초`
  - 두 schedule에서 사용하지 않는 slot 14의 byte 표현만 `ZERO ↔ EE`로 바꾸고 active slot
    내용은 바꾸지 않는 sentinel
  - repository maintainer 겸 on-site hardware approver가 이 exact operation을 승인하고 현장에서
    감시했으며 즉시 물리 전원 차단 가능 상태를 확인했다. 역할 B의 latent manual
    `Random / Flow 89`가 복원 중 잠시 active가 될 가능성도 이 operation에 한해 수용했다: **(c)**.
  - Home Assistant는 실행 전후 재시작·reload·disable·설정 변경·service call하지 않았다: **(c)**.
- 종료 지점(stage / failure code):
  - 첫 attended `qualify`는 아래 8개 action을 각각 한 번 완료하고 모두
    `written_verified`로 durable 기록했다.
    1. slave safe fallback
    2. master safe fallback
    3. slave sentinel schedule
    4. master sentinel schedule
    5. slave original schedule restore
    6. master original schedule restore
    7. slave original outer-control restore
    8. master original outer-control restore
  - action 완료 UTC는 `01:41:34.966170Z`부터 `01:44:56.378426Z`까지다. 같은 action의
    재전송은 없었다: **(b)**.
  - 이어진 final pair에서 `verify_mismatch`로 종료했다. 저널은
    `awaiting_final_verify / 8-of-8 / inflight=false / error=null`을 유지했다.
  - 별도 strict collector가 두 장비의 baseline exact 일치를 보존한 뒤, 코드가 명시한
    `AWAITING_FINAL_VERIFY` 재개 경로를 한 번만 수행했다. 첫 challenge는 만료돼 장비 read/write
    전에 `attended_record`로 끝났으며 재시도로 세지 않는다. 새 challenge로 수행한 실제 재개는
    completed action을 재전송하지 않고 final pair read만 수행했지만 다시 `verify_mismatch`로
    종료했다.
  - 두 번째 실제 final-pair 실패 뒤 세 번째 시도는 하지 않았다. 현재 durable 상태는 여전히
    `awaiting_final_verify / 8-of-8 / inflight=false / error=null`; exact-restore journal SHA-256은
    `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`다: **(b)**.
  - 이 저널은 비-terminal이고 `RECOVERY_REQUIRED`가 아니므로 현행 `recover` 명령 대상이 아니다.
    journal을 편집·삭제하거나 다른 operation으로 우회하지 않는다.
- 장비가 실제로 돌려준 값:
  - Phase 0 primary strict series `JFS-3c5ed409f82ae441f66fcc417bdc84e9`
    (`63320ee18d6def7eb80bae1c49b23748eb1cfbde38a6ecf1c0645dd87412cb67`)은
    `2026-09-04T01:20:19.165612Z`부터 `01:21:10.832179Z`까지 `2/2` pair를 accepted했다.
  - Phase 0 corroborating strict series `JFS-bef710c324ed105ea4b473932a4a31e7`
    (`f85d5f27cdbdca5f80cac090d0daf4545a4b0963a536fd3e07cd7f7a4b97a91a`)은
    `2026-09-04T01:21:27.527523Z`부터 `01:22:20.345466Z`까지 `2/2` pair를 accepted했다.
  - 두 series는 서로 다른 plan·series이고 다음 baseline에서 byte-exact하게 일치했다: **(a)**.
    - master: `ON / TimerON / independent / Constant / Flow 30 / Frequency 32`, schedule SHA-256
      `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`
    - slave: `ON / TimerON / independent / Random / Flow 89 / Frequency 34`, schedule SHA-256
      `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`
  - 첫 final mismatch 뒤 diagnostic strict series
    `JFS-0f3123231a54b82c8e130951263e5987`
    (`a9197bdcf366e71ded6713634924005749b8144525d3e24bf845f55bafc9c008`)은
    `2026-09-04T01:46:39.206205Z`부터 `01:47:01.232168Z`까지 `1/1` pair를 accepted했다.
    두 역할의 outer six-field와 schedule bytes는 위 baseline과 exact 일치했고 identity도
    일치했다: **(a)**.
  - SHA-256 identity binding(세 series 공통):
    - `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
    - `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
  - diagnostic pair의 host monotonic provenance는 다음을 보존했다: **(b)**.
    - 전체 pair attempt `22.010981092초`; participant completion gap `11.460858534초`;
      두 participant read overlap `0.0초`
    - 역할 A attempt `10.540284406초`: identity-before `5.008854852초`, raw read
      `0.348096751초`, identity-after `5.005861601초`
    - 역할 B attempt `11.458050016초`: identity-before `5.005362307초`, raw read
      `0.340956868초`, identity-after `5.008003143초`
  - Phase 0에서 manifest에 채택한 각 ordinal 1도 같은 차이를 보였다: **(b)**.
    - primary: 전체 attempt `21.650386049초`, completion gap `11.155363784초`
    - corroborating: 전체 attempt `22.795387102초`, completion gap `11.562926789초`
  - 두 번째 final-pair 재개는 challenge 입력부터 프로세스 종료까지 `22.268240042초`였고
    `{"code":"verify_mismatch","status":"exact_restore_refused"}`를 반환했다. 거부된 두
    observation 자체는 durable하게 남지 않았으므로 이 wall time과 console 결과는 **(c)**다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시):
  - journal의 8개 action result는 sentinel write, original schedule restore와 original outer
    restore가 모두 `written_verified`였다고 기록한다: **(b)**.
  - writer 종료 뒤 독립 strict raw pair에서 두 outer six-field와 두 432-byte schedule image가
    Phase 0 baseline과 byte-exact하게 일치했다: **(a)**.
  - 따라서 **복원 data plane은 실패하지 않았다.** standalone 경로가 exactly-once로 temporary
    sentinel을 쓰고 원 baseline을 복원했다는 실질 질문은 충족됐다. 실패한 것은 그 사실에 대한
    terminal receipt 발급이며 sentinel receipt·qualified bundle은 생성되지 않았다.
- **이 실행으로 새로 확정된 사실:**
  - standalone exact-restore의 8개 bounded action은 exactly-once로 수행됐고, 독립 raw에서 두
    장비 controls와 schedule image의 byte-exact 원복이 확인됐다.
  - `max_final_pair_gap_seconds` 한 필드가 서로 다른 두 양에 적용된다. baseline admission은
    participant completion gap(실측 `11.155..11.563초`)만 검사하지만 runtime final verification은
    전체 acquisition duration과 최초 request부터 최후 receive까지의 window(실측
    `21.650..22.795초`)에도 같은 `20초`를 적용한다. `completion gap < 20초 < total window`인
    이번 operation은 admission을 통과해도 final receipt에서 구조적으로 실패한다.
  - 전체 window가 22초대인 이유는 discovery가 장비 응답 시점과 무관하게 고정 5초 청취 창을
    소진하고, participant별 identity-before/read/identity-after를 두 역할에 순차 수행하기
    때문이다. 네 identity 구간은 `5.005..5.009초`, 실제 raw read는 `0.341..0.348초`였다.
  - 이 결함은 잘못된 PASS가 아니라 fail-closed 비용 결함이며 §3의 세 P1 범주에는 해당하지
    않는다. 그러나 Phase 1 receipt와 같은 verifier를 쓰는 Phase 4 final receipt를 모두 막는다.
- 이 실행으로 확정하지 못한 것:
  - 정식 sentinel qualification receipt와 qualified baseline bundle은 없으므로 앱 주도 ASYNC
    operation의 restore 선행조건은 계속 **BLOCKING**이다.
  - 앱 schedule·role action은 시작하지 않았고 ASYNC content/timing ownership 질문은 그대로
    `UNKNOWN`이다.
  - rejected final pair의 두 observation은 보존되지 않아 그 frame-local timing을 재구성하지
    않는다. 다만 고정 청취창과 세 보존 pair의 total window만으로 `20초` 초과가 독립적으로
    성립한다.
  - protocol-reported 값 밖의 물리 유량·파형·위상은 측정하지 않았다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것:
  - 이 operation은 두 실제 final-pair 실패로 park한다. **세 번째 재시도, current manifest 값
    변경, journal 편집·삭제·강제 archive를 하지 않는다.** recovery supervisor는 종료 상태로
    두고 새 앱 action·새 실기를 시작하지 않는다.
  - 현재 비-terminal journal을 증거를 잃지 않고 terminal화하는 방법은 현행 코드에 없다.
    repository maintainer가 별도 계획과 검토를 거쳐 결정해야 한다.
  - 대안 1(권고): participant completion coherence bound와 전체 acquisition-duration bound를
    별도 필드로 분리한다. legacy journal·manifest hash·authority 결속과 현재 비-terminal
    journal 처리까지 포함한 범위 한정 계획을 먼저 작성·검토한다.
  - 대안 2: 새 manifest에서 단일 bound를 실측 최대보다 큰 값으로 올릴 수는 있지만, completion
    coherence 보증까지 함께 느슨해져 의미 불일치를 가릴 뿐이므로 차선이다.
  - 대안 3: exact-restore qualification과 앱 관측을 park한다. 이 경우 앱 ASYNC 질문은
    `UNKNOWN`으로 남지만 software-independent actuator·group 제품 트랙에는 영향이 없다.

## §7 park 영향

- 현재 장비에는 미결 물리 복원 조치가 없다. 마지막 preserved raw는 두 장비가 Phase 0
  baseline과 byte-exact임을 보여 주고, 두 번째 final 시도는 device write를 수행하지 않았다.
- software state에는 non-terminal exact-restore journal이 남아 있다. 이 journal이 terminal화될
  때까지 앱 action, 새 hardware experiment와 다른 exact-restore operation을 시작하지 않는다.
- app manifest가 요구하는 sentinel receipt가 없으므로 앱 주도 관측은 절차상으로도 진행할 수
  없다. 오늘 앱을 열어 schedule이나 role을 바꾸지 않는다.
- status 확인은 장비 network를 끈 상태였고 장비 write는 `0`이었다. hardware-safety store는
  자체 계약에 따른 임시 writability probe를 수행했으므로 host store write와 device write를
  구분한다.
- Home Assistant는 계속 건드리지 않는다.
