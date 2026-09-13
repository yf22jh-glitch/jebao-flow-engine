# run: 2026-09-13-local-power-authority-deployment

- 시간 기준: 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다.
- 목적(한 문장): 세 수류모터의 독립 `power` candidate를 current qualification과 결정론적 순서로
  병합하고, direct power switch 세 개와 group switch 하나·member resume 세 개만 추가하는 새
  immutable generation을 device command 없이 배포합니다.
- 이 실행이 답하려던 질문: 세 장비의 실제 ON → OFF → ON evidence를 current manual-flow authority에
  합쳐도 기존 control bytes·identity·journal·receipt를 보존하면서 예상한 direct·group surface만
  정확히 열고, 실패한 start는 exact prior deployment로 되돌릴 수 있는가.
- 사용자 경계:
  - repository maintainer이자 on-site hardware approver인 사용자는 수조를 직접 감시하고 전원 차단
    수단을 가진 상태에서 직접 앱 조작을 제외한 나머지 작업의 계속 진행을 승인했습니다. 사용자는
    Home Assistant를 혼자 사용하므로 새 writable control의 노출 자체는 별도 승인 대상으로 삼지
    말고 진행하라고 확인했습니다.
  - 이 배포로 direct `power` switch 세 개와 세 장비를 함께 조작할 수 있는 group switch 한 개가
    online surface로 열렸습니다. armed group에 개별 member를 복귀시키는 resume control 세 개도
    discovery에 생겼지만 비무장 시작 상태에서는 unavailable입니다. group switch와 resume의 실제
    actuation은 이 run에서 수행하지 않았고 offline projection과 startup만 확인했습니다.
  - direct·group power command의 runtime 경로에는 Timer gate가 없습니다. 따라서 TimerON schedule로
    운전 중인 두 번째 Pro에도 command가 전달되며, power 전환 뒤 schedule ownership 재개는 아직
    실측하지 않았습니다. attended 유량 충격 가이드 `50`과 qualification first-write 상한 `45`도
    Home Assistant power command의 기술적 gate가 아닙니다.
  - 이 operation은 device command, 제바오 앱 조작, group actuation, Home Assistant Core 조작, host
    reboot와 frozen native ASYNC 하네스를 수행하지 않았습니다.
- 실행한 source commit SHA:
  - qualification-set merger는
    `5619ee95a88a31e7f4d1069f0043889a24086fe7`, power producer의 최종 reviewed source는
    `045f086b82c6c9a25a8e22cd892be2f9cf7fdee9`입니다.
  - live evidence record commit은 첫 번째 Pro
    `21370b63148f4630748e95b7c6f23296f39b5fe4`, 두 번째 Pro
    `aa59f5c2386a5358a8ddb304e5b9aa2683b36f0c`, Bar
    `5f439485536157bc73f3249942c7cc1d2de12636`입니다.
  - 실행 image는 exact x86_64 image id
    `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`입니다.
- 실행 전 조건:
  - prior Compose generation `d730650c4d105e8af7bb74fe720ffa46`의 persistent service exactly `1`,
    old daemon `0`, unfinished journal `0`, retained command payload `0`, 장비 authenticated fresh
    `6/6`을 확인했습니다.
  - current qualification은 SHA-256
    `abaa4ad15444d2f9db025cafbfb9811dc9ddb954d45afc0c7da395f806f12803`, bytes `1,231`, binding
    `2`, receipt `6`, qualified physical identity/function pair `3`이었습니다. ignored qualification과
    모든 ignored category는 `0`, write-allowed device는 `3`, direct `manual_flow` control은 `3`,
    direct `power`·group control·resume는 `0`이었습니다.
  - 첫 번째 Pro, 두 번째 Pro, Bar power candidate의 SHA-256은 각각
    `7c9ac354169f8c9dc2727a90e30a87e14149eba23bfb55ca8785879546b9744e`,
    `83e715f5d43c57f9ef8c80d06f634343a45c5a663ac2a07ebbf738a0e3e86643`,
    `0ad7f62b3428d64a15bafd6807413627b2ca0251341f04484a9bf1d1474c7189`입니다. 각 candidate는
    binding `1`·receipt `2`·qualified pair `1`이며 current authority와 격리돼 있었습니다.
  - journal은 operations `13`·blobs `39`·files `52`, sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256
    `0644759bcaa36161a4ad1c0a0874b2c0750e46752b290b177921e2a0aaddce4d`였습니다. 이 값은 Bar
    power qualification terminal inventory와 같아 아래 두 deployment attempt보다 앞선 anchor입니다.
    fixed qualification receipt root는 files `18`, 같은 방식의 tree SHA-256
    `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`였습니다.
- immutable merge 결과:
  - merger CLI가 current set과 candidate를 한 번에 재조립하지 않고 candidate 하나씩 검증해 만든
    no-clobber intermediate 그 자체를 다음 단계의 input으로 사용했습니다. raw physical key·private
    logical member는 argv·stdout·stderr와 이 기록에 싣지 않았고, 각 candidate identity는 current
    manifest에서 exact 한 member로 역해석됐습니다.
  - 첫 merge는 binding `3`·receipt `8`·pair `4`, added pair `1`, SHA-256
    `4552730b47fdfd9378445ad348f6d596f8ce80e1208858f0d49aea50db10cfc9`, bytes `1,763`입니다.
    두 번째 merge는 두 Pro가 같은 `(product_key, power)` binding을 공유하므로 binding `3`·receipt
    `10`·pair `5`, added pair `1`, SHA-256
    `8a74696378f1d9ebf7a42363d75bc7cafd8eb65df861e387d127cd5fd21f3c6b`, bytes `1,897`입니다.
  - final merge는 binding `4`·receipt `12`·pair `6`, added pair `1`, SHA-256
    `b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033`, bytes `2,429`, mode
    `0600`, link count `1`입니다. canonical reload가 exact 일치했고 current config는 write-allowed
    device `3`과 SHA-256
    `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`, bytes `4,645`로
    불변입니다.
- 배포 전 offline projection:
  - binding `4`·receipt `12`·pair `6`, ignored qualification과 category별 count는 모두 `0`이었습니다.
    direct control은 `manual_flow 3 + power 3 = 6`, group switch `1`, member resume `3`이며 command
    topic 총계는 `10`입니다. 기존 manual-flow number 세 개의 payload는 byte 동일했습니다.
  - discovery는 old `86`, new `90`, common `83`은 byte 동일하고 changed `0`이었습니다. old-only
    power diagnostic sensor topic `3`의 sorted set SHA-256은
    `d4b1f969ba903ef0d98361c269bd597f3805df75287113bbd57c16dd98b91fc2`이고, new-only direct
    power switch `3`·group switch `1`·resume `3` topic의 sorted set SHA-256은
    `22169e051c5ca60fc90fdd2f0efe8e343af94683bf182fcfcc741d87c137c94b`입니다.
  - new discovery `90`개의 sorted `(topic, SHA-256(canonical payload))` compact-JSON SHA-256은
    `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`입니다. direct power는
    모두 non-optimistic switch이고 command topic을 가집니다.
  - new tombstone은 `53`개, sorted topic-set SHA-256
    `29c3b1aed2c7092c289ffb5b39baf24b2c6a5d7c1ff191cecdd929d40f02e185`입니다. old-only 세
    topic은 모두 이 set에 포함돼 orphan은 `0`입니다. prior `57 -> 53`은 direct power switch topic
    세 개가 tombstone에서 빠지고 power diagnostic 세 개가 들어와 상쇄된 뒤, group switch 하나와
    resume 세 개가 빠진 결과입니다.
- 사용한 명령·주요 파라미터:
  - network `none`인 reviewed merger image로 current set과 세 candidate를 순서대로 병합했습니다.
    final merger 산출 파일을 손으로 재조립하지 않고 Compose qualification input으로 그대로
    사용했습니다.
  - owner-matched no-clobber input과 기존 config·volume identity·두 read-only template root로
    `prepare-compose --write-enabled`를 실행했습니다. final Compose SHA-256은
    `9e13071c09df9440575200f2d71461614a9879ffa87562f952f503ea10712fa5`, bytes `4,245`, mode
    `0600`, link count `1`, generation `2b4d91a3a662ac2aa85bacf91a03f72b`입니다.
  - prepared manifest SHA-256은
    `5d138cddfec807d7ee99a43682460ff478b63d3ac4502a903f29f3df6d985c82`, bytes `2,914`이고,
    bundle descriptor SHA-256은
    `fa22244e0b2ca558f8ab03f0c96cf102016d411d5d9e068aff4b066dea342a10`, bytes `344`입니다.
    new host bundle은 files `4`, sorted `(relative path, SHA-256(bytes))` tree SHA-256
    `4fc622a0562862b71fde384c520c60fd464b4da58916137e84e33989092f5e19`입니다.
  - stage는 network `none`, runtime은 host network이며 둘 다 root filesystem read-only, capability
    drop `ALL`입니다. stop-first 전에 exact image, host bind source, Compose config, prior Compose
    SHA-256 `3d74461e6925e1db738a56e3be95c56af78534cdca329b1716176f2f6651d6bf`, prior bundle files
    `4`·tree SHA-256
    `0da96e246e6f313134bbd9772b66231034ece63de0ce933e8935b2f681555741`를 재검증했습니다.
- 실행 중 정정과 실제 rollback:
  - 첫 merge wrapper는 current source digest가 durable root 전체에서 유일해야 한다고 잘못
    가정했습니다. 같은 bytes가 current source와 active bundle에 각각 있어 output 생성 전에
    fail-closed했고 mutation은 `0`입니다. corrected wrapper는 명시적으로 결속한 current source만
    읽고 각 output에 새 no-clobber 경로를 사용했습니다.
  - 첫 실제 deployment attempt는 final bytes로 stop-first 뒤 `start_new`가 nonzero로 끝났습니다.
    wrapper는 old build context를 재빌드하지 않고 보존한 exact prior image·Compose·bundle input으로
    자동 rollback했고 prior runtime이 다시 기동했습니다. journal·receipt tree는 byte 불변이고,
    journal-first local-control 경로의 device write는 `0`이었습니다.
  - 첫 실패의 원인은 확정하지 못했습니다. 이후 같은 stage를 isolated disposable volume과 network
    `none`으로 실행해 exit `0`을 관측했지만 이는 별도 환경의 성공일 뿐 첫 실패의 설명이나
    재현이 아닙니다. 같은 final deployment bytes가 두 번째 attempt에서 성공했으므로 이 절차가 두
    시도 사이 결정론적으로 재현됐다고 주장하지 않습니다. 원 stderr는 보존하지 않아 그 내용이나
    digest를 (b) artifact로 주장하지 않습니다.
- 종료 지점(stage / failure code): `power_authority_deployed` PASS. 두 번째 attempt stage exit `0`,
  runtime exactly `1`, exact image·Compose generation, warning·error·traceback·exception log count
  `0`입니다. 첫 attempt는 `start_new_nonzero_then_exact_prior_rollback`, 원인 `UNKNOWN`입니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: immutable merge outputs·Compose bundle·descriptor·runtime manifest와 pre/post
    journal·qualification tree는 **(b) preserved structured/durable daemon artifact**입니다.
    Docker/CLI output, broker retained audit, group runtime introspection과 authenticated fresh device
    read는 raw copy를 별도 보존하지 않은 **(c) reconstructed operator observation**입니다. raw MQTT
    packet·device frame이 없어 (a)는 주장하지 않습니다.
  - opaque artifact id: Compose generation은 `2b4d91a3a662ac2aa85bacf91a03f72b`, runtime이 fresh
    rederive한 install-manifest generation은 `999217603cd51f2c3fc2f71f5d13be45`입니다. 서로 다른
    개념이며 같은 generation이라고 주장하지 않습니다.
  - UTC span: 성공 attempt의 stop request는 `2026-09-13T07:05:03.889612Z`, runtime ready는
    `2026-09-13T07:05:06.082258Z`, final independent fresh read는
    `2026-09-13T07:11:48.315177Z`부터 `2026-09-13T07:11:57.701766Z`까지였습니다. 성공 attempt의
    service 부재는 약 `2.19`초입니다. 첫 실패·rollback의 stop/start timestamp는 보존하지 않아 그
    부재 시간을 유도할 수 없으며, 두 attempt를 합친 실제 service 부재는 `2.19`초보다 깁니다.
  - SHA-256 identity binding: six physical identity sorted-set digest
    `5c0a9b8943832df7f8fc30472a6b57af39a5ad3b997fce34dca56b6a80da4a19`와 stable role binding
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`는 배포 전후 byte
    동일했습니다. raw physical key·private logical member·private path를 기록하지 않습니다.
  - artifact digest (SHA-256): current qualification
    `b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033`, current config
    `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`, runtime manifest
    `63191f6291310c613ec90cdbd6c47f7f1dda71b6147b8e8273755f0b1c8cb349`, descriptor
    `fa22244e0b2ca558f8ab03f0c96cf102016d411d5d9e068aff4b066dea342a10`, discovery
    `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`, journal tree
    `0644759bcaa36161a4ad1c0a0874b2c0750e46752b290b177921e2a0aaddce4d`, qualification receipt
    tree `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`입니다.
  - runtime manifest는 device `6`, template `2`, app-catalog
    `8d5fa7604c1d7e612b6b6b73251c8a8a58d0b63db8b5d34a5459336a77e90be3`, protocol-schema
    `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`입니다. template-set
    SHA-256은 `a2513a45d64db98b25b781f6b9fb24ef857ee5723a71a9fbb6ebcfadf0943eb2`입니다.
  - post-runtime catalog는 binding `4`·receipt `12`·qualified pair `6`, ignored qualification과 모든
    category `0`, write-allowed device `3`, direct `manual_flow 3`·`power 3`, group switch `1`, member
    resume `3`, command topic `10`입니다.
  - broker는 new discovery expected/present `90/90`을 byte exact로 반환했고 missing·mismatch `0`,
    canonical digest는 위 값입니다. old-only topic `3`은 fresh subscriber에서 absent, new-only topic
    `7`은 exact present였습니다. retained command non-empty payload와 audit 중 live command는 `0`입니다.
    Home Assistant entity registry의 반영은 이 run에서 확인하지 않았습니다.
  - global availability와 device availability는 online, independent authenticated fresh read는 장비
    `6/6` available이었습니다. 상태 projector의 catalog `power` descriptor 기반 관측은 `6/6` 모두
    ON이었습니다. 여기에는 control unsupported인 dosing 제품의 read-only `switch` 전원 상태도
    포함됩니다.
  - 별도로 writer-side scalar `power` mapping이 존재하는 장비는 `5/5` 모두 ON이었습니다. dosing
    제품은 app catalog·status schema에는 `power`/`switch` 관측이 있지만 write evidence가 없어
    `_SCALAR_FIELD_BY_PRODUCT`의 control mapping은 비어 있습니다. 따라서 앞선 기록의 writer-mapped
    `5/5`와 이번 state-observed `6/6`은 같은 지표의 분모 변화가 아니며, 장비 modeling이나 배포가
    바뀌었다고 주장하지 않습니다.
  - group runtime은 configured `1`, supervisor `1`, unconstructed `0`, fresh cycle `29`, cycle failure
    `0`, inventory consistent `true`, supervisor constructed `true`, armed `false`, actual output `on`,
    command qualification missing `0`, manual override `0`이었습니다. group control availability는
    online `1`, diagnostic online `1`, resume availability는 비무장 상태와 정합하게 offline `0/3`입니다.
    clean startup은 direct router와 group ownership service가 같은 operation-lock 객체를 써야 한다는
    constructor invariant를 통과한 증거입니다.
  - 종료 journal은 operations `13`·blobs `39`·files `52`, qualification receipt root는 files `18`이고
    두 tree digest가 merge·prepare·첫 실패와 rollback·두 번째 성공·postcheck 전 구간에서 byte exact
    불변입니다. **startup과 audit의 local-control device write는 `0`입니다.** 이 판정은 모든
    local-control frame보다 journal-first record가 선행하는 현재 coordinator·group 경로에 한정하며,
    log count나 fresh read에서 추론하지 않습니다.
  - new runtime은 stage start `2026-09-13T07:05:04.317150210Z`, stage finish
    `2026-09-13T07:05:05.265301385Z`, runtime created `2026-09-13T07:05:04.229526464Z`, started
    `2026-09-13T07:05:05.434849283Z`입니다. restart counter `0`은 stop-first·force-recreate 뒤 새
    counter이며 operation 간 연속성 증거가 아닙니다.
- 원복 검증 결과:
  - 첫 deployment 실패에서는 보존한 exact prior image·Compose·bundle input으로 prior runtime이
    실제 복귀했고 journal·receipt tree가 불변이었습니다. 이는 실행된 rollback의 관측이며 실패
    원인을 설명하지 않습니다. 첫 rollback 직후의 별도 fresh device snapshot은 보존하지 않아 그
    경계의 device state byte 불변은 주장하지 않습니다.
  - 두 번째 deployment는 새 generation으로 성공해 rollback을 실행하지 않았습니다. discovery
    `90/90`, fresh device `6/6`, group non-armed, journal·receipt 불변을 확인했습니다.
- **이 실행으로 새로 확정된 사실:** current manual-flow authority와 세 power candidate는 검증된
  intermediate를 거쳐 binding `4`·receipt `12`·qualified pair `6`인 하나의 immutable set으로 합쳐졌고,
  startup device write 없이 direct power switch `3`, group switch `1`, member resume `3`만 예상대로
  열렸습니다. 첫 start 실패에서는 exact prior deployment rollback이 실제로 작동했고, 같은 final
  bytes의 두 번째 attempt는 성공했습니다.
- 이 실행으로 확정하지 못한 것: 첫 deployment start 실패의 원인과 첫 stop/rollback service 부재
  시간, 동일 bytes의 두 시도가 다른 결과를 낸 이유, raw MQTT packet·device frame, Home Assistant
  registry 반영, direct power switch의 MQTT 실사용, group switch·member resume actual actuation과
  동시 OFF·ON의 물리 영향, TimerON인 두 번째 Pro의 power 전환 뒤 schedule ownership 재개, host reboot·
  future container recreate·HA update 복귀, 장시간 운용과 Q1·Q2는 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 동일 final bytes의 start 실패가 다시 발생하면
  성공한 별도 실행으로 설명하지 말고 원 stderr와 Compose events를 private artifact로 먼저 보존합니다.
  rollback은 old build context를 재빌드하지 않고 exact prior image·Compose·bundle generation을
  사용합니다. M4 group commissioning은 armed `false`에서 시작해 한 operation씩 별도 승인된 출력·
  step·동시 변경 대수를 기록하고, direct/group shared lock·plan-atomic journal·member readback과 exact
  restore를 actual device evidence로 검증합니다.
