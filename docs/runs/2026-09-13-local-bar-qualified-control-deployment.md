# run: 2026-09-13-local-bar-qualified-control-deployment

- 시간 기준: 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다.
- 목적(한 문장): Bar `manual_flow` candidate를 기존 두 Pro receipt와 immutable set으로 합치고,
  정확히 그 Bar 한 대의 write allow를 추가한 새 Compose generation을 device command 없이 배포해
  persistent HA Flow control을 `2 -> 3`으로 엽니다.
- 이 실행이 답하려던 질문: Bar의 실제 `45 -> 44 -> 45` qualification evidence를 current authority에
  추가해도 기존 두 Pro control과 여섯 장비 identity·role·fresh state가 유지되고, 사라지는 diagnostic
  discovery를 retained tombstone으로 정리하면서 Bar `number` 하나만 정확히 신설되는가.
- 사용자 경계:
  - repository maintainer는 직접 앱 조작이 필요한 단계 외의 작업을 계속 진행하도록 승인했고, 이
    배포로 상시 write 가능한 Bar HA Flow `number` 하나가 추가된다는 점을 사전에 공개했습니다.
    사용자는 Bar qualification과 복원을 마친 뒤 비수동 작업의 계속 진행을 지시했습니다.
  - 이 `number`가 공개·수용하는 범위는 app-declared `30..100`입니다. attended agent operation의
    유량 충격 방지 가이드 `50`과 qualification first-write 상한 `45`는 HA 사람 입력의 기술적 gate가
    아닙니다. 배포 후 Bar의 fresh retained Flow는 `91`이므로 사람은 새 slider에서 `30..100` 어느
    값으로든 직접 변경할 수 있습니다.
  - 이 operation은 device command, 앱 조작, group actuation, `power` qualification, Home Assistant
    Core update·restart·설정 변경, host reboot와 frozen native ASYNC 하네스를 수행하지 않았습니다.
- 실행한 source commit SHA:
  - qualification-set merger는
    `5619ee95a88a31e7f4d1069f0043889a24086fe7`, Bar family profile은
    `d687739c7045859e838e6122710f90921af36606`, Bar live evidence record는
    `1fdb947698c7b6b7bed7e640851b1e8f208f17fa`입니다.
  - 실행 image에 결속한 최종 reviewed source는
    `045f086b82c6c9a25a8e22cd892be2f9cf7fdee9`, exact x86_64 image id는
    `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`입니다.
- 실행 전 조건:
  - prior Compose generation `5d2fe97ef3117d59930a4531295c3dea`의 persistent runtime exactly `1`,
    old daemon `0`, unfinished journal `0`, retained command payload `0`, global·device availability
    `7/7 online`, authenticated fresh device `6/6`·power ON `6/6`을 확인했습니다.
  - running catalog는 qualification binding `1`·receipt `4`·qualified pair `2`·ignored `0`, direct
    control `2`, Bar command `0`, group control·resume `0`이었습니다. existing discovery는 `86/86`
    byte exact였고, 이 run에서 정의한 sorted `(topic, SHA-256(canonical payload))` compact-JSON
    SHA-256은
    `a4b99bff620ad42c9c67a55893bfd1ba53b3b5b0909df164c5b13349cf8aac78`이었습니다.
  - Bar candidate qualification SHA-256은
    `abfb27261e4959baa217ab4ca63dc29be878829e36d66b381e1a42e0d7216431`이며 binding `1`, receipt
    `2`, qualified pair `1`이었습니다. public `qualification-target:` identity anchor는
    `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371`입니다.
  - journal은 operations `10`·blobs `27`·files `37`, sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256
    `0d82a3dde445a3d0ded163b213d1d6f1d49aa09d04886043ef8dd5cd526add6a`였습니다. qualification
    receipt root는 files `12`, 같은 방식의 tree SHA-256
    `e1d294897acd0f5c60a18ab0bc53f753c4ec4c411212ce37550784386714ded2`였습니다.
- 배포 전 예상 delta:
  - merger CLI는 candidate receipt의 physical identity를 raw argv·stdout·stderr에 싣지 않고 current
    manifest의 private logical member exact 한 개로 expected-add를 유도했습니다. verified merged
    result는 binding `2`, receipt `6`, qualified pair `3`, added pair `1`이며 SHA-256
    `abaa4ad15444d2f9db025cafbfb9811dc9ddb954d45afc0c7da395f806f12803`, bytes `1,231`, mode
    `0600`, link count `1`입니다.
  - 같은 physical identity를 current manifest에서 exact 한 logical member로 역해석해 config의 그
    device만 `allow_hardware_writes=false -> true`로 바꿨습니다. write-allowed는 `2 -> 3`, 다른
    세 장비는 false이고, config SHA-256은
    `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`, bytes `4,645`, mode
    `0600`입니다.
  - runtime UID와 같은 verifier context의 offline projection은 device `6`, binding `2`, receipt `6`,
    qualified pair `3`, ignored qualification과 모든 ignored category `0`, write-allowed `3`, direct
    control `3`, group control·resume `0`이었습니다. 세 control은 모두 `manual_flow` number
    `30..100`, `optimistic=false`입니다.
  - old/new discovery는 각각 `86`개이고 common `85`개는 byte 동일했습니다. old-only diagnostic
    sensor topic `1`과 new-only Bar number topic `1`의 sorted topic-set SHA-256은 각각
    `aa4e603e7e820f135abefeb6d76e750b2a96c756985f68f130e312cb65c0d0f5`와
    `d8f1026e19b7e6c5f7ee7b1cd05e781ffada9b5ac603fbd6166a14b12b364d47`입니다. new discovery
    digest는 `0c40cbc208a075a1e0fb2fb5544d5e9758f125a3df7a48460c0d071bedffa688`입니다. 기존 두 Pro
    number payload는 byte 동일했습니다.
  - old-only topic은 새 projection의 owned tombstone에 정확히 포함됐습니다. new tombstone `57`개의
    sorted topic-set SHA-256은
    `be6f9fc6586ed009d98057cf9850a74b1833e398600c591905a4bdcff2eb3e7b`입니다. adapter가 current
    discovery보다 먼저 각 tombstone에 zero-length retained payload를 publish해 broker retained
    record를 삭제하는 기존 구현을 사용했습니다.
- 사용한 명령·주요 파라미터:
  - network `none`인 reviewed merger image로 current set과 Bar candidate를 읽고 새 owner-only
    no-clobber qualification source를 만들었습니다. 이어서 별도 no-clobber config source를 만들고,
    세 source와 두 read-only template root로 `prepare-compose --write-enabled`를 실행했습니다.
  - final Compose는 SHA-256
    `3d74461e6925e1db738a56e3be95c56af78534cdca329b1716176f2f6651d6bf`, generation
    `d730650c4d105e8af7bb74fe720ffa46`입니다. bundle descriptor SHA-256은
    `b0713aac2ca7e5ff6e88e4459648282f5564830f8fc6af5503b3e1c314055719`, prepared manifest
    SHA-256은 `4095d50c2fd872ac3877b5e9e5ac16284e2e15ca6ae36ade6f45e03dbde7010c`입니다. bundle은
    config·volume identity·qualification·descriptor 네 파일이고 MQTT password file은 current config에
    필요하지 않았습니다.
  - stop-first 전에 final host bind source·digest와 `docker compose config`, stage network `none`,
    runtime network `host`, preserved rollback image·bundle을 확인했습니다. prior runtime을 완전히
    정지한 뒤 exact reviewed image를 stage와 runtime에 결속해 `--no-build --force-recreate`로 한
    persistent service만 시작했습니다. device command는 발행하지 않았습니다.
- 실행 중 정정:
  - 첫 merger 시도는 hardware-safety volume을 read-only로 mount해 durable root validation에서 output
    생성 전에 fail-closed했습니다. corrected invocation만 merged output에 사용했습니다.
  - 첫 Compose wrapper는 current owner-only Compose input을 image UID로 읽으려 해 manifest·bundle
    생성 전에 실패했습니다. 다음 source-copy 초안은 heredoc가 stdin을 차지해 source digest 대조에서
    copy 생성 전에 끝났습니다.
  - owner-matched 재시도는 config·qualification source 두 개를 no-clobber로 만들었지만 MQTT password
    secret이 있다고 잘못 가정해 volume-id source와 prepare 전에 끝났습니다. 두 이미 생성된 source는
    exact 재검증해 재사용했고, current config가 password를 요구하지 않음을 확인한 뒤 volume-id source를
    별도로 만들어 corrected prepare를 실행했습니다.
  - compacted 진행 메모의 축약 image 값을 prior exact image id로 오인한 첫 deployment wrapper는
    stop 전에 identity assertion에서 끝났습니다. direct Docker inspect로 prior runtime
    `sha256:61c6d68ca85ee819b802b7215b6549d7b14dc12610ecb4d6dbfbbe049a7e202d`, prior stage
    `sha256:8f9d36c2456ed62239349f15fcad72e89a219a544306de277ac7222058cb3c1e`를 다시 결속한
    corrected wrapper만 stop·deploy를 수행했습니다.
  - 배포 후 Bar surface count 초안은 public `local_system` entity를 device family map에서 제외하지 않아
    projection 뒤 `KeyError`로 끝났습니다. broker·device network와 durable artifact를 건드리지 않았고,
    corrected read-only audit만 아래 결과에 사용했습니다.
  - 위 실패 중 config·qualification source 두 개를 명시적으로 만든 시도 외에는 running service,
    current data volume, device, broker, journal, receipt를 바꾸지 않았습니다. 그 두 source는 active bundle
    준비의 검증된 input으로 그대로 사용했습니다.
- 종료 지점(stage / failure code): `bar_manual_flow_qualified_control_deployed` PASS. stage exit `0`,
  runtime exactly `1`, exact image·Compose generation, warning·error·traceback·exception log count `0`입니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: immutable merged qualification·config·Compose bundle·descriptor, runtime manifest와
    pre/post journal·qualification tree는 **(b) preserved structured/durable daemon artifact**입니다.
    Docker/CLI output, broker retained audit와 authenticated fresh device read는 raw 파일로 별도 보존하지
    않은 **(c) reconstructed operator observation**입니다. raw wire frame이 없어 (a)는 주장하지
    않습니다.
  - opaque artifact id: Compose generation은 `d730650c4d105e8af7bb74fe720ffa46`, runtime이 fresh
    rederive한 install-manifest generation은 `94dfd630d2453662c11e6202bb4d00c1`입니다. 두 generation은
    다른 개념이며 같은 값이라고 주장하지 않습니다.
  - UTC span: first retained corrected input mtime은 `2026-09-12T21:13:06.508528Z`, runtime start는
    `2026-09-12T21:21:28.636182959Z`, final postcheck는
    `2026-09-12T21:33:35.734905673Z` 이전에 끝났습니다. 첫 pre-output 실패 시도의 시작 시각은
    보존하지 않았으므로 첫 값은 전체 operation 시작이 아니라 durable-output span의 하한입니다.
    이 mtime 관측은 (c)이며 저장소의 preserved artifact만으로 재현되지 않습니다.
  - SHA-256 identity binding: six physical identity sorted-set digest
    `5c0a9b8943832df7f8fc30472a6b57af39a5ad3b997fce34dca56b6a80da4a19`와 stable role binding
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`는 배포 전후 byte
    동일했습니다. raw physical key·private logical member·private path를 기록하지 않습니다.
  - artifact digest (SHA-256): runtime manifest
    `962b46b1fa82f53b1357844116d840a7c5b295340ce9964cbf339bc5c0ada227`, current config
    `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`, current qualification
    `abaa4ad15444d2f9db025cafbfb9811dc9ddb954d45afc0c7da395f806f12803`, descriptor
    `b0713aac2ca7e5ff6e88e4459648282f5564830f8fc6af5503b3e1c314055719`입니다. descriptor가
    결속한 config와 qualification digest는 current bytes와 exact 일치했습니다.
  - runtime manifest는 device `6`, template `2`, app-catalog
    `8d5fa7604c1d7e612b6b6b73251c8a8a58d0b63db8b5d34a5459336a77e90be3`, protocol-schema
    `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`입니다. template-set
    SHA-256은 `a2513a45d64db98b25b781f6b9fb24ef857ee5723a71a9fbb6ebcfadf0943eb2`입니다.
  - post-runtime catalog는 binding `2`·receipt `6`·qualified pair `3`, ignored qualification과 category
    모두 `0`, write-allowed `3`, direct `manual_flow` control `3`입니다. Bar는 entity `13`개 중 sensor
    `12`·number `1`, range `30..100`, `optimistic=false`, command topic 존재입니다. power receipt는
    `0`이라 모든 group member가 `manual_flow`와 `power`를 함께 가져야 하는 계약상 group control·resume는
    구조적으로 `0`입니다.
  - broker는 new discovery `86/86`을 byte exact로 반환했고 digest는 위
    `0c40cbc208a075a1e0fb2fb5544d5e9758f125a3df7a48460c0d071bedffa688`, missing·mismatch `0`입니다.
    new-only topic은 exact present이고 old-only topic은 fresh subscriber에 message `0`으로 absent였습니다.
    zero-length retained publish는 retained record를 삭제하므로 “길이 0 payload가 보존돼 있다”고
    주장하지 않습니다. Home Assistant entity registry에서 옛 diagnostic entity가 제거됐는지도 이번
    broker audit으로 확정하지 않습니다.
  - retained command non-empty payload는 세 command topic 모두 `0`, global·device availability는
    `7/7 online`이었습니다. independent authenticated fresh read는 device `6/6` available, power ON
    `6/6`, unavailable diagnostic `0`이고 Bar manual Flow retained state는 `91`이었습니다.
  - group lifecycle은 supervisor `1`, cycle failure `0`, final audit 시 fresh cycle `49`였습니다. runtime
    restart `0`은 stop-first·force-recreate 뒤 새 counter이며 operation 간 연속성 증거로 사용하지
    않습니다.
  - 종료 journal은 operations `10`·blobs `27`·files `37`, qualification root는 files `12`이고 두 tree
    digest와 unfinished `0`이 실행 전후 byte exact 동일했습니다. **startup device write는 `0`입니다.**
    근거는 모든 local-control device frame보다 journal-first record가 선행하는 현재 coordinator 경로에
    한정해 journal tree 불변으로 확인한 것이며, log count나 fresh read에서 추론하지 않습니다.
  - new bundle은 files `4`, tree SHA-256
    `0da96e246e6f313134bbd9772b66231034ece63de0ce933e8935b2f681555741`, current Compose는
    `3d74461e6925e1db738a56e3be95c56af78534cdca329b1716176f2f6651d6bf`로 재독했습니다.
    preserved prior bundle도 files `4`, tree SHA-256
    `e78abc39c494cae9c358d52ef29abf6896053f7f21f24961f5a893133df6a7b0`, prior Compose SHA-256
    `9a19eff92aeb539b355d6ad7249ad7b21543a7ef53deb5fb329b23f0bddf7999`로 남았습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): final deployment가 성공해 rollback을 실행하지
  않았습니다. exact prior runtime·stage image, prior Compose와 private input generation 전체를 재빌드
  없이 사용할 rollback input으로 보존했고, 새 merged source와 generation은 old deployment를
  in-place로 바꾸거나 삭제하지 않았습니다.
- **이 실행으로 새로 확정된 사실:** Bar의 live `manual_flow` receipt pair를 current two-Pro set에
  exact merge해 startup write 없이 persistent direct control을 `2 -> 3`으로 열었습니다. 기존 두 Pro
  payload와 common discovery `85`개는 byte 동일하고, old diagnostic topic은 broker retained state에서
  사라졌으며 새 Bar `30..100` non-optimistic number만 신설됐습니다. 여섯 장비는 fresh·power ON으로
  유지됐고 journal·receipt tree는 불변입니다.
- 이 실행으로 확정하지 못한 것: Home Assistant entity registry의 old diagnostic 정리, Flow `100`의
  장비 실수용, three-device `power` qualification, OFF 상태 비전원 필드 유지, group actuation,
  HA Core restart·host reboot 복귀, 장시간 운용, Q1·Q2와 frozen native ASYNC 동작은 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: current/next projection의 common·old-only·new-only
  topic을 배포 전에 양방향 선언하고, disappearing retained topic은 empty publish의 broker 삭제 의미를
  반영해 배포 후 fresh subscriber의 absence로 확인합니다. rollback은 old build context를 재빌드하지
  않고 exact prior image·Compose·input generation을 사용합니다. 진행 메모·중계 메시지의 축약 digest를
  identity 제약으로 쓰지 않고 source of truth의 direct inspect에서 exact 값을 다시 유도합니다. 다음
  physical step은 한 장비씩 별도
  `power` qualification이며, 첫 write 전에 on-site 감시·즉시 전원 차단과 실패 시 planned ON 성공 뒤를
  포함해 최종 OFF로 남을 수 있다는 복원 책임을 다시 확인합니다.
