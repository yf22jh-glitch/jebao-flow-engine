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

## 2026-09-04 parked-disposition dry verification addendum

- reviewed source commit `e977a16e4aed115a4b19fd380b15e596c6d51709`의 Linux/amd64 image로
  current journal과 diagnostic strict series의 격리 복사본만 사용해 `park-verify`를 실행했다.
  container network는 `none`이었고 원본 hardware-safety volume은 읽기 전용으로만 대조했다:
  **(c)**.
- disposition manifest SHA-256
  `6410822317d17ec9262bfa44a12f71701c193883ea1245dff4ee30346f934936`은 current
  journal의 operation/baseline/action-plan/authority-chain digest와 diagnostic series reference를
  모두 통과했지만, `disposition_artifact_predates_restore`에서 fail-closed했다.
- 거부 결과는 owner-only private artifact
  `PVR-83bf8652c999d5413e7052f3158f4226`
  (`6aa92d4bf7ac09359d8d0d9e3de62a8ed5081af101b5fa89c29bfac300fa5a55`)에 보존했다.
  원본과 복사본 journal SHA-256은 모두
  `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`였고,
  park archive/index/tombstone 생성은 `0`건이었다. device network와 device write도 `0`이었다:
  **(b)**.
- 거부에 사용된 UTC는 다음과 같다.
  - 마지막 completed action: `2026-09-04T01:44:56.378426Z`
  - diagnostic artifact 시작: `2026-09-04T01:46:39.219543Z`
  - diagnostic artifact 완료: `2026-09-04T01:47:01.230524Z`
  - 두 번째 final-verify reauthorization까지 반영한 current record `updated_at`:
    `2026-09-04T02:14:16.280108Z`
- verifier는 artifact 시작이
  `max(last_completed_action.completed_at, record.updated_at)`보다 늦어야 한다. diagnostic artifact는
  마지막 device write보다 뒤지만 두 번째 write-free reauthorization보다 앞이어서, `updated_at`이
  원래 의도한 awaiting-transition 시각의 proxy가 아니라 가장 최근 journal 갱신 시각이 된 현재
  record에는 적격하지 않다.
- 이 거부는 잘못된 PASS, device write 또는 journal 손실을 만들지 않은 fail-closed 결과라 §3
  P1이 아니다. disposition은 write-free라 §2의 첫-write 이전 gate 규칙도 적용 대상이 아니다.
- 기존 diagnostic artifact는 폐기하지 않고 `20초` policy overload와 byte-exact restore의 근거로
  계속 보존한다. 다만 disposition eligibility에는 **새 explicit-only strict pair 1회**를
  repository maintainer의 별도 read-only 접촉 승인 뒤 수집해 결속한다. source gate는 이번 막힘을
  풀기 위해 바꾸지 않는다.
- 이 결정은 위 §7의 "새 실기를 시작하지 않는다"를 **이 read-only strict pair 1건에 한해
  대체**한다. 이는 final-verify의 세 번째 재시도가 아니며 device write가 없는 별도 관측이다.
  세 번째 final-pair 재시도, current manifest 값 변경, journal 편집·삭제·강제 archive와 recovery
  supervisor 재기동 금지는 그대로 유효하다.
- 새 capture 뒤 park 완료 전에는 exact-restore reauthorization이나 journal을 갱신하는 다른 attended
  상호작용을 끼우지 않는다. `record.updated_at` proxy의 의미를 바로잡는 source 변경은 disposition
  완료 뒤 별도 안건으로 남긴다.

### 승인된 fresh strict read와 park eligibility

- repository maintainer가 위 범위의 explicit-only strict read 1회를 별도로 승인했다. 이는
  final-verify 재시도나 장비 write 승인이 아니다: **(c)**.
- source-attested fresh series `JFS-9e5d6aa28ea2367742865cbdf98979b8`
  (`c6559ba7b2e2fed055b40bc1398f2b090e222eed1caa2933d74057fb9f869384`) ordinal `0`은
  `2026-09-04T05:41:24.021425Z`부터 `05:41:46.512115Z`까지 `1/1` pair를 accepted했다.
  plan SHA-256은 `a32b7380e642250d48e534da0bdde2c95e6ee4f479eec4a5739967d1d17938bd`이고,
  action policy는 explicit-only다. 두 identity binding, outer six-field와 432-byte schedule image는
  current journal baseline과 exact 일치했다: **(a)**.
- pair total attempt는 `22.490690130초`, participant completion gap은 `11.681584751초`였다:
  **(b)**. completion gap은 앞선 세 보존 pair의 최대 `11.562926789초`보다
  `0.118657962초` 큰 새 관측 최대지만, precommitted coherence bound `20초`에는 약 `8.3초`
  여유가 있다. acquisition 최대는 앞선 `22.795387102초`가 그대로이므로 향후 v2의
  `20초 / 30초` 두 bound는 바꾸지 않는다.
- capture 직전과 직후 recovery supervisor는 exited였고 TCP `12416` established connection은
  `0`이었다. collector는 state read만 수행했고 device write는 `0`이었다. Home Assistant는
  재시작·reload·disable·설정 변경·service call하지 않았다: **(c)**.
- 새 evidence reference만 결속한 disposition manifest SHA-256은
  `1a00a96a6dc41fc737d1bd1f94eea46bc1dff8bcbac071ef055ffcd3e9eeb6a2`다. 이전 manifest와
  비교해 바뀐 필드는 evidence의 plan artifact id·plan SHA·series artifact id·series SHA뿐이고,
  operation/journal/baseline/action-plan/authority-chain 결속은 불변이다: **(b)**.
- device-network-free 격리 복사본의 `park-verify`는
  `exact_restore_park_disposition_ready / device_network_access=false / device_write_count=0 /
  qualification_achieved=false`를 반환했다. owner-only 결과 artifact SHA-256은
  `79d6c8279dfbab34062a87efe2776c0ea09159d785d2b734a968b772e0927260`이다. 원본과
  복사본 journal SHA는 계속 `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`이고,
  park archive/index/tombstone 생성은 아직 `0`건이다: **(b)**.
- 이 시점에는 attended park challenge를 아직 발급하지 않았고 live journal도 변경하지 않았다.

### Attended parked disposition 실행과 terminal barrier 확인

- repository maintainer는 manifest SHA-256
  `1a00a96a6dc41fc737d1bd1f94eea46bc1dff8bcbac071ef055ffcd3e9eeb6a2`에 결속된
  write-free park challenge를 검토하고, 이 current operation을 qualification 성공으로 만들지 않는
  `PARKED_UNQUALIFIED` disposition만 승인했다. 승인 문자열 자체는 기록하지 않는다: **(c)**.
- reviewed Linux/amd64 image를 `--network none`으로 실행한 park 결과는 다음과 같다: **(b)**.
  - disposition id: `ERD-26abc5aa31d509a3ddb49f497114e8fd`
  - disposition SHA-256:
    `712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`
  - original journal snapshot SHA-256:
    `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`
  - operation SHA-256:
    `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`
  - outcome/claim: `parked_unqualified`, `qualification_achieved=false`,
    `device_network_access=false`, `device_write_count=0`
- original journal의 canonical bytes는 위 digest로 immutable snapshot에 보존됐다. live journal path에는
  성공 record가 아니라 SHA-256
  `f899c8117106cd5286d764d5c6663246bb7fe7e9a426c7186d86990dba829301`인 tombstone이
  설치됐다. tombstone은 owner 일치, regular file, mode `0600`, link count `1`이었고,
  `ExactRestoreRecord` validation은 거부했지만 parked-disposition loader와 archive↔tombstone exact
  교차검증은 통과했다: **(b)**.
- operation-digest finalization은 version `2`, outcome `parked_unqualified`이며
  `receipt_sha256`을 갖지 않는다. 실제 archive를 다시 읽은 controller의
  `_require_operation_not_parked()`는 같은 operation을 `AUTHORITY`로 거부했고, 그 확인 전후 live
  tombstone digest는 불변이었다: **(b)**.
- 구 source image에서 실제 실행한 `status`는 tombstone을 재개 가능한 record로 읽지 못했다:
  **(c)**. 구 image의 `create`·`arm`·`execute`는 이번 runtime에서 실행하지 않았고, legacy shape의
  write entry가 장비 I/O 전에 fail-closed하는 성질은 단위 테스트에서만 확인했다.
- stopped recovery supervisor image의 configured user는 실행 시 uid/gid `100:101`로 해석됐고,
  tombstone owner와 일치했다. 실제 tombstone은 supervisor의
  `_ExactRestoreArtifactPresence`로 parse됐으므로, supervisor가 다시 scan한다면 corruption
  `ERROR`가 아니라 설계된 `ATTENDED_REQUIRED`로 닫힌다. 이 확인을 위해 supervisor를 기동하지
  않았다: **(b)**.
- postcondition을 서로 다른 fresh process에서 확인하던 중 status, archive audit와 idempotent park를
  동시에 실행해 nonblocking archive claim이 경합했다. status와 archive audit은 통과했고 한
  idempotent call은 `disposition_tombstone_invalid`로 fail-closed했다. 모든 동시 process가 끝난 뒤
  순차 재호출은 exit `0`으로 위와 동일한 disposition id/SHA를 반환했다. 파일·장비 변형은 없었다:
  **(b)**. source trace상 실제 조건은 archive claim 획득 실패이고 tombstone corruption이 아니므로,
  표시된 오류 코드는 오도하는 저심각도 진단 결함이다. claim 보호가 계획 밖 동시 부하에서도 상태를
  보존한 실측이기도 하며 §3 P1은 아니다.
- park 직후 recovery supervisor는 계속 exited였고 TCP `12416` established connection은 `0`이었다.
  park 뒤 별도 장비 read는 수행하지 않았다. Home Assistant는 재시작·reload·disable·설정 변경·service
  call하지 않았다: **(c)**.

### 이 disposition으로 새로 확정된 사실과 남은 조건

- failed qualification의 8개 action과 원본 journal 증거는 삭제되지 않았고, current operation은
  immutable disposition과 operation별 영구 replay barrier로 terminal화됐다. 같은 operation id의
  success receipt와 재실행은 구조적으로 차단된다.
- **qualification이 성공한 것은 아니다.** sentinel receipt와 qualified bundle은 여전히 없으므로
  앱 주도 live-write의 restore 선행조건은 계속 `NO-GO / BLOCKING`이다. protocol-reported baseline
  밖의 물리 유량·파형·위상도 주장하지 않는다.
- 다음 단계는 source 의미를 사후에 current v1 operation에 소급하지 않고, participant completion
  coherence와 전체 acquisition duration을 분리하는 reviewed v2 source commit이다. 그 뒤 fresh
  Phase 0, 서로 다른 operation id의 v2 manifest와 별도 on-site 승인이 있어야 새 qualification을
  준비할 수 있다.
- `record.updated_at`을 awaiting transition proxy로 쓴 eligibility 의미와 archive claim 경합을
  `disposition_tombstone_invalid`로 표시하는 진단은 park 완료 뒤의 별도 source 안건으로 남긴다.
  이번 성공을 만들기 위해 두 규칙을 사후 변경하지 않는다.
- recovery supervisor는 reviewed v2 prepare가 permanent barrier와 backing archive를 exact reload한
  뒤 tombstone을 정당한 successor로 교체할 때까지 exited로 둔다. 새 앱 action, 새 qualification,
  journal 편집·삭제·강제 archive와 Home Assistant 조작은 계속 금지한다.

## 2026-09-05 parked disposition 일반화 source addendum — 이 run에 대한 소급 없음

이 run이 남긴 후속 안건 중 "park 완료 뒤의 별도 source 안건"으로 미뤄 둔 것들이 두 commit으로
착지했습니다 — `1b8d2b900b2a3ab752f7e8a1aaf141424e7145a6`와
`56146bd8b5dae8cf148b14a5ebfe032f9ced02d2`. 이 addendum은 **이 run에 무엇이 소급되지 않는지**를
먼저 적습니다.

- 이 run이 만든 version 1 disposition은 바이트가 변하지 않았습니다. 저장 disposition의 22-key
  canonical shape와 digest 계산이 변경 전과 동일함을 fixture 수준에서 고정했고, 위 두 commit
  이전 source tree로도 같은 값을 재계산해 대조했습니다: **(b)**.
- 이 run의 disposition 의미는 재해석되지 않았습니다. version 1은 계속 sentinel cycle·overload
  reason·전 action `written_verified`만을 서술하며, 새 version 2 vocabulary는 version 1 문서에
  들어갈 수 없도록 모델 validator가 막습니다.
- 이 run의 operation id, tombstone, permanent barrier는 그대로입니다. 이 작업은 이 archive를
  읽지도 쓰지도 않았습니다. 전 과정에서 private journal·archive·recovery supervisor·장비·앱·
  Home Assistant 접촉은 `0`입니다: **(c)**.

새로 확정된 것은 다음입니다.

- park은 이제 sentinel qualification뿐 아니라 baseline restore와 phase-5 staged restore도
  종결할 수 있습니다. phase-5는 baseline과 같은 cycle을 갖기 때문에 embedded predecessor depth
  (sentinel `0` / baseline `1` / phase-5 `2`)가 구분자입니다.
- baseline cycle의 정상 분포(write 4 · skip 2)가 거부되지 않습니다. 이전 규칙은 전 action이
  `written_verified`일 것을 요구했으므로 정상 baseline을 park할 수 없었습니다.
- 끝난 predecessor의 영구 barrier 확인이 finalization index를 설치하는 **같은 claim 안에서**,
  index 이전에 수행됩니다. predecessor가 없거나, 다른 receipt를 가리키거나, barrier 파일이
  디코딩되지 않는 세 경우 모두 fail-closed하며 current finalization과 tombstone 설치는 `0`회입니다.
- 저장될 candidate가 archive 자신의 decoder를 통과하는지 index 이전에 재확인합니다. 실패 시
  live journal은 여전히 journal입니다.
- 이 run이 남긴 진단 안건 중 archive claim 경합이 `disposition_tombstone_invalid`로 표시되던
  건은 앞선 별도 commit에서 이미 정정됐고, 이번 작업은 그 위에 얹혔습니다.

이 run의 후속 조건은 바뀌지 않았습니다. sentinel receipt와 qualified bundle은 여전히 없고,
앱 주도 live-write의 restore 선행조건은 계속 `NO-GO / BLOCKING`입니다. v2 qualification 4~6단계
역시 fresh Phase 0, 새 v2 manifest, 별도 on-site 승인이 있어야 하며 현재 **NO-GO**입니다.

## 2026-09-05 증거등급 정정 — 위 addendum의 (b) 한 건을 (c)로 내림

append-only 규칙에 따라 앞 절을 고치지 않고 여기서 대체합니다. 교차검토가 지적한 등급 오류
한 건입니다.

앞 addendum은 "v1 disposition 바이트 불변"을 fixture 수준 고정과 이전 source tree 재계산으로
확인했다며 등급 **(b)**를 붙였습니다. 이 디렉터리의 `README.md`가 정의하는 **(b)**는 데몬이 남긴
저널·영수증·intent 같은 구조화 산출물이 **실제 파일로 보존된** 경우입니다. fixture 고정과 source
tree 재계산은 그 자체로 daemon artifact가 아니므로 (b)에 해당하지 않습니다. 같은 README의
"보존 파일과 그 id·digest를 실제로 확인하기 전에는 (b)로 올리지 않는다"를 제가 어겼습니다.

정정 후 두 사실을 분리해 적습니다.

- **이 run이 실제로 남긴 stored disposition의 digest**는 이 문서 앞 절
  (`712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`)에서 이미 보존된 산출물로
  확립돼 있고, 그 등급 **(b)**는 그대로 유효합니다. 이번 작업은 그 파일을 읽지도 쓰지도 않았으므로
  그 등급에 영향을 주지 않습니다.
- **새 source가 version 1 canonical 형식과 호환된다는 사실**은 회귀와 이전 source tree 재계산으로
  확인한 **source-level 사실**입니다. 보존된 결과 artifact가 없으므로 등급은 **(c)**입니다.

두 사실을 하나로 묶어 "(b)로 확인됐다"고 쓴 것이 오류였습니다. 등급을 올리기 위해 새 daemon
artifact를 만들지 않으며, 이 정정으로 앞 절의 판정·NO-GO 상태는 달라지지 않습니다.

## 2026-09-06 정정 — 보존됐던 safety artifact의 사후 부재

이 절은 앞의 실행 사실을 지우지 않고 **2026-09-06 현재 상태**를 덧붙입니다. 승인된 범위 한정
read-only forensic에서 daemon이 볼 수 있는 전체 Docker volume, 사용 가능한 Docker
context/profile, 사용자 홈 아래 depth `7` 범위(`Library`·`node_modules`·`.git`·`.Trash` 제외)와
Docker 내부 저장소를 제외한 VM root를 조사했지만 이 run이 참조한 exact-restore journal snapshot,
disposition blob, live tombstone과 operation finalization을 찾지 못했습니다. 외부 매체·다른 머신·
백업은 조사하지 않았습니다. 조사 과정의 volume mount는 모두 read-only였고 write·삭제·복구·
writability probe·device contact는 `0`이었습니다: **(c) reconstructed operator observation**.

따라서 앞에서 기록한 disposition digest
`712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`, journal snapshot digest
`273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`와 tombstone digest
`f899c8117106cd5286d764d5c6663246bb7fe7e9a426c7186d86990dba829301`은 당시 대조 결과의 문서
참조로는 남지만, **현재 원본 파일이 보존돼 있지 않아 다시 (b)로 검증할 수 없습니다.** 이 정정은
그 파일들이 2026-09-04에 생성·대조됐다는 당시 기록을 소급해 부정하지 않습니다.

삭제 시각·주체·명령은 확인할 event history가 없어 `UNKNOWN`입니다. 현재 `/hardware-safety`를
제공하는 유일한 container mount가 빈 Docker 익명 volume이라는 사실은 확인했지만, 그것이 이 run의
exact volume이었는지 또는 어떤 삭제 경로가 실제로 발화했는지는 단정하지 않습니다.

현재 source의 operation-finalization lookup은 backing file을 찾지 못하므로, 이 run의 operation
SHA-256 `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`에 대해 앞 절이 주장한
**영구 replay barrier는 현재 성립하지 않습니다.** 사라진 bytes를 digest에서 재구성하거나 새
terminal success로 가장하지 않습니다. 관측된 익명-volume 생성 경로와 이 operation replay를
별도 source 차단으로 닫고, verified named safety volume을 새로 준비하기 전까지 장비 read·write,
앱 live-write, Home Assistant 조작과 recovery supervisor 기동은 `NO-GO`입니다. 계획과 범위는
[`hardware-safety volume durability incident`](../hardware-safety-volume-durability-plan.md)을
따릅니다.

## 2026-09-06 source containment addendum — 이 run의 성공을 소급 주장하지 않음

incident의 자동 익명 safety-root 경로와 사라진 operation replay는 source commit
`c2a4e8adb425283f94f1d5329d0657c0c705faad`로 차단했습니다. Dockerfile의 자동
`/hardware-safety` volume 선언을 제거하고, 이 run operation의 공개 digest를 current record와
embedded predecessor chain 전체에 적용하는 revocation으로 고정했습니다. 이 source property는
사라진 journal·snapshot·disposition·finalization을 복원하거나 이 run을 qualification success로
바꾸지 않습니다.

조사된 이 deployment에는 live journal이 없었고, 새 source에서는 sanctioned create·promotion·
device-capable continuation이 닫힙니다. 다른 deployment에 revocation 이전의 legacy
`FINAL_VERIFIED` journal이 실제로 남아 있다면 write-free receipt·clear 경로는 좌초 방지를 위해
열려 있고, 그 경로의 finalization은 그 **현재 journal**에 결속됩니다. 다만 archive는 operation
digest로 keying되어 journal provenance를 보존하지 않으므로, 같은 digest의 v1 success 항목 하나만으로
이 2026-09-04 parked operation이 성공했다고 판정하면 안 됩니다. 이 run의 판정은 앞의 실행 사실과
2026-09-06 incident 기록을 함께 따릅니다.

같은 source diff를 두 실행자가 network-none container에서 독립 실행해 표적 `167 passed`, 전체
unit `1899 passed`, 변경 파일 ruff lint·format 통과를 확인했습니다: **(c) console observation**.
diff SHA-256은 commit에서 재계산 가능한 source property이며 증거등급을 붙이지 않습니다. 이
검증에서 실제 safety volume·private durable state·device·앱·Home Assistant 접촉은 `0`이었습니다.

## 2026-09-07 정정 — actual deployment safety root 발견과 byte-exact backup

2026-09-06 정정의 forensic 범위는 그 절에 적은 대로 당시 local machine과 그 Docker daemon·VM에
한정됐고, 외부 매체·다른 머신·backup은 명시적으로 조사하지 않았습니다. 후속 offline 분석에서 과거
성공 collector의 보존 실행 기록을 추적해 별도 remote Linux deployment host를 식별했고, 그 host의
read-only Docker metadata에서 actual deployment용 named volume `jebao-flow-hardware-safety`를
확인했습니다. 따라서 2026-09-06 절의 artifact 부재와 barrier 상실 판정은 local `colima`에만
적용되며 actual deployment에는 적용되지 않습니다.

actual deployment volume은 host report 기준 `1.071MB`이고 `/hardware-safety`에서 이 volume을 쓰는
container는 `9`개 모두 exited, running은 `0`이었습니다. 전체 volume은 `28`개이고 정렬된 name set의
SHA-256은 `da2a46d39e0ea3bbd6ebd70a2f622fb7fe5d0970cdf16fea1b15a6031cd1ddba`였습니다.
기존 exited recovery container를 시작하거나 새 container·mount를 만들지 않고, 해당 container가 보는
root를 read-only byte stream으로 복사해 별도 owner-only private storage에 보존했습니다. 백업 직후 같은
metadata를 다시 읽었을 때 size·사용자 수·running 수·volume count·name-set digest가 모두 같았습니다:
**(c) reconstructed operator observation**.

- private backup id: `JHS-9aff85b2b0a852e2741ef48e`
- archive SHA-256: `741f08233fc36ced3d7458cbd51bcf464b37af97c7f0fea940876db8a7525bcf`
- archive bytes / payload bytes: `1091072` / `1070529`
- regular file / directory / symlink / other: `25` / `5` / `0` / `0`
- member-name set SHA-256:
  `bcc7f698e4e77e1d7076501c089cfdd5b6db99b04a09a38aca7be50eb4c3f505`

backup 안의 daemon artifact를 current production model과 canonical serializer로 offline reload했습니다.
live tombstone, parked disposition, parked journal snapshot과 permanent version `2` finalization이 모두
존재했고 다음 content digest가 이 run에 기록된 값과 exact match했습니다:
**(b) preserved structured/durable daemon artifact**.

- live tombstone:
  `f899c8117106cd5286d764d5c6663246bb7fe7e9a426c7186d86990dba829301`
- parked disposition (`ERD-26abc5aa31d509a3ddb49f497114e8fd`):
  `712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`
- parked journal snapshot:
  `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`
- permanent finalization content:
  `05304ff970df521b64da2c61641e8dc12f2edb7c45dce73f1b3966011fc5d11c`
- finalization operation-key:
  `76880fc3cd8a462bcadcdd1f4231dc0c16f61b0896dbbe00c8e486576fefe154`
- public operation SHA-256:
  `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`

네 artifact는 canonical validation, filename-to-digest binding, tombstone ↔ disposition ↔ journal ↔
finalization의 cross-binding과 current production `_require_disposition_binds_journal`을 통과했습니다.
journal은 `sentinel_qualification` cycle과 completed action `8`개를 담고, finalization은
`outcome=parked_unqualified`, `qualification_achieved=false`, `device_network_access=false`,
`device_write_count=0`입니다. success receipt는 없습니다. 즉 이 operation의 permanent v2 replay
barrier는 **actual deployment root에 존재**하며 qualification 성공을 주장하지 않습니다.

actual deployment backup에는 `.hardware-safety-identity` marker가 없습니다. marker 도입 전부터 쓰던
비어 있지 않은 volume이므로 current marker-enforcing source로 `status`·`park`·provision을 곧바로
실행하지 않습니다. 기존 artifact와 barrier를 보존하는 migration을 별도로 설계·검토하고 actual
deployment 기준 A를 새로 승인받기 전에는 이 root를 더 열거나 수정하지 않습니다. 이 발견과 backup은
remote root mutation·device·앱·Home Assistant 접촉을 만들지 않았고 hardware는 계속
`NO-GO / BLOCKING`입니다.

### 2026-09-07 migration 설계 전 metadata 보존과 offline copy 검증

actual root를 참조하는 exited container `9`개의 full inspect metadata를 console에 출력하지 않고
owner-only private artifact `JHC-3edcb721461feacea6b1fc6b`로 보존했습니다. artifact는 `57855` bytes,
SHA-256 `2ff51baf97436afed056921b733cf3b9ada0cbc56173bb02b3e1afdb0ebdab5e`이고 running·OOM-killed는
`0`입니다. 전체 container-id set SHA-256은
`42b9cbd647b94228a2b13f77deee2aa4ec14bd657c79d42f3d55bb8ee3c81377`입니다. raw id, environment,
host path는 private artifact에만 있습니다.

앞 절의 safety-root backup을 disposable copy로 풀어 current production provisioner를 network-none에서
offline 실행했습니다. 기존 entry `29`개의 content digest·mode·link count·inode는 모두 같았고,
추가된 것은 canonical marker 한 개뿐이며 runtime verify 뒤 temporary residue는 `0`이었습니다:
**(c) reconstructed operator observation**. actual remote volume·container는 이 검증에서 바뀌지
않았습니다. 이 검증은 migration의 실행 승인이 아니며 hardware는 `NO-GO / BLOCKING`입니다.

### 2026-09-07 marker migration 1차 packet 중단

승인 뒤 actual-deployment 전용 identity를 두 owner-only private carrier에 보존하고 무출력 대조했습니다.
backup opaque id는 `JHI-b507f6d0c014f7d3d3d29bd8`, identity SHA-256은
`d72417f1aa03190b4ed2e4bfa16b23a03669926aaed9ab53d2457c0b50028aef`입니다. raw identity와 private
path는 repository에 기록하지 않습니다.

reviewed local image를 한 번 load한 뒤 remote runtime image ID가 precommitted local descriptor와 달라
STOP했습니다. saved archive의 config blob과 remote ID는 모두 `b8f35abb…37484`이고 ordered RootFS와
Config/History projection은 source와 exact match했지만, 이 해석은 사전 조건에 없었으므로 continuation
승인으로 사용하지 않았습니다: **(c) reconstructed operator observation**.

legacy container 삭제·root mount·probe·marker와 device·앱·Home Assistant 접촉은 `0`입니다. root는
계속 `LINKS=9 | 1.071MB`이고 container-id set·volume-set digest도 위 preflight와 같습니다. 새 image
transport 결속 결정 전까지 migration과 hardware는 `NO-GO / BLOCKING`입니다.

### 2026-09-07 current-state backup 보강 — legacy `/data` 보존과 실행 전 정지

actual root user `9`개가 참조하는 `/data` mount를 container를 시작하지 않은 read-only Docker
metadata로 다시 확인했습니다. unique legacy volume은 `6`개이고, 하나는 `LINKS=4 | 78.92kB`, 다섯은
`LINKS=1 | 0B`였습니다. Docker anonymous label은 모두 부재했습니다. name multiset·unique set·
`name|links|size` tuple SHA-256은 각각
`a0d1fcdd7aef63444a88f4c84d21882819458a5243de078251647be16f3a9323`,
`e937b18a50c19095972a1e4055c4e552ae52b94ff96949354933afb93f308c98`,
`3ad84ee51e42b4fa54d5fa8d7144416af4964474edba849c2316819ea79f2de0`입니다.

되돌릴 수 없는 legacy container 삭제 전에 각 volume을 hash 정렬 ordinal로 골라, 그것을 참조하는
stopped container의 `/data`를 container 시작 없이 `docker cp ... -` byte stream으로 읽었습니다.
owner-only private artifact `JHD-375309b20810d49ff8530205`에는 유효한 tar `6`개, inventory, index와
manifest가 있습니다. tar 합계는 `148480` bytes, transfer stderr는 모두 `0` bytes, index SHA-256은
`7107dd96ff2c178c6f6d37ea939a82073429e126d56cde4c402e9229ed355b38`, manifest는 `4439` bytes와
SHA-256 `e1da04cf812a9f9be68f429981ac090786fd2796fb2dc38283b4b572d4e446ac`입니다. raw container ID·volume
name·private path는 출력하거나 repository에 기록하지 않았습니다: **(c) reconstructed operator
observation**.

backup 뒤 actual root user `9`개는 모두 exited, root는 `LINKS=9 | 1.071MB`, 전체 volume은 `28`개와
기존 set SHA-256 `da2a46d3…ddba`, running Jebao와 TCP `12416` established는 각각 `0`으로 실행 전과
같았습니다. 실제 safety-root bytes, marker, container, volume, device·앱·Home Assistant는 변경하지
않았습니다.

actual deployment에 사용할 identity는 `d72417f1…28aef`이고, 앞 local `colima` 검증의
`fa99e3b9…df10`은 **비배포 marker-only root**의 값입니다. 둘을 서로 대체하지 않습니다. image config
digest pin, root holder와 parked-tombstone successor C는 아직 plan·contract 변경일 뿐이고 remote
mutation 승인이 아닙니다. 이 addendum은 새 hardware run이나 Sep-04 qualification success를 주장하지
않으며 marker migration과 hardware는 계속 **`NO-GO / BLOCKING`**입니다.
