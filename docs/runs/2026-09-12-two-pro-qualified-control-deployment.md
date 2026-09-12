# run: 2026-09-12-two-pro-qualified-control-deployment

- 목적(한 문장): app-declared Flow capability `30..100`에 다시 결속한 두 Pro의 새 schema
  `manual_flow` receipt를 한 immutable qualification set으로 합치고, 정확히 그 두 개체만 persistent
  control surface로 여는 단일 Compose generation을 device command 없이 배포합니다.
- 이 실행이 답하려던 질문: D1l source와 새 schema receipt 네 장을 배포하면 기존 물리 identity·role
  binding과 여섯 장비 fresh observation을 유지하면서 HA write surface가 첫 번째 Pro 하나에서 두 Pro로만
  늘고, 두 number가 제품 선언 범위 `30..100`을 exact retained discovery로 공개하는가.
- 사용자 경계:
  - `30..100`은 장비/app capability와 사람이 HA에서 입력할 수 있는 범위입니다. 사용자가 정정한 대로
    `50`은 돌아가는 수조에 갑작스러운 유량 변화를 주지 않기 위한 attended agent operation 가이드이며,
    capability·영구 설정 상한·input validation gate가 아닙니다.
  - 이번 operation은 device command를 발행하지 않았습니다. HA Core update·restart, 홈서버 reboot,
    frozen native ASYNC 하네스와 group actuation도 수행하지 않았습니다.
- 실행한 commit SHA:
  - qualification-set merger source는
    `5619ee95a88a31e7f4d1069f0043889a24086fe7`, Flow capability와 writer source는
    `fe10bd5706a6c2c8b0e3a0b71be98bf7b7c62fc4`입니다.
  - 두 새-schema qualification의 append-only 근거는
    `8647f8681c8c5875e6b507ba7b847b2525ec17b1`과
    `24b808a7ad72acc8bbc751370dddbddb2d2c7d74`입니다.
- source·deployment 결속:
  - D1l source archive SHA-256은
    `82f4986c4e8fc41d3c1943513f96b99ce82ff7c95d2ff5a0f405f8bea5ccbf7f`, 최종 runtime image id의
    SHA-256 binding은
    `a1edf68a90a2f45a64dffb6994d7c817aedccc7a1e1863148da6bbf0a9820b71`입니다.
  - protocol-schema SHA-256은
    `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`, app-catalog SHA-256은
    `8d5fa7604c1d7e612b6b6b73251c8a8a58d0b63db8b5d34a5459336a77e90be3`, active Pro template
    SHA-256은 `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`입니다.
  - stable role-binding SHA-256
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`와 여섯
    `physical_identity_key` 집합은 config flip과 manifest 재유도 전후 byte 동일했습니다.
- 실행 전 조건:
  - exact prior reviewed runtime image binding
    `d80bc094ad1875bf52f61d785d0495c62b4a6c5577882de180e43cdebe381748`, service exactly `1`,
    restart `0`, old surface `1`, HA range `30..75`, group command·resume surface `0`, retained command
    `0`, global availability `online`, 장비 authenticated fresh `6/6`, unfinished journal `0`을
    확인했습니다.
  - broker의 기존 discovery는 old projection `86/86`과 exact 일치했습니다. 새 projection과의 차이는
    기존 Pro number의 `max 75 -> 100` 한 건과 아직 없는 두 번째 Pro number 한 건뿐이었고, 새 두
    command topic에도 retained payload는 `0`이었습니다.
  - journal inventory는 operations `9`·blobs `21`·files `30`, canonical tree SHA-256
    `b0280138220b7543d25a24fb4e500eb9b1e8d3c5a446d491663ecb0de81f4d6e`이었습니다.
    qualification receipt-root는 files `10`, canonical tree SHA-256
    `4eb2eac50901764c140533652f54475504ad238d14b84901c7a0323fb82824b5`였습니다.
- 사용한 명령·주요 파라미터:
  - network `none`인 D1k merger가 첫 번째·두 번째 Pro의 새 candidate만 읽어 binding `1`, receipt
    `4`, qualified pair `2`인 immutable set을 만들었습니다. expected-add는 private logical member를
    current manifest에서 exact resolution해 선언했고, merged result의 binding·surface union을 다시
    verifier로 유도해 정확히 두 Pro인지 확인했습니다.
  - current config에서 candidate receipt가 가리키는 physical identity를 manifest logical member로
    역해석해 그 한 member만 추가로 `allow_hardware_writes=true`로 만들었습니다. write-allowed는
    `1 -> 2`, 전체 `2`이고 Bar와 다른 네 장비는 false를 유지했습니다. 모든 physical identity와
    stable role binding은 불변이었습니다.
  - 새 source image에서 offline projection을 만들고 qualification binding `1`·receipt `4`·qualified
    surface `2`, control entity `2`, 각 `manual_flow` number range `30..100`, ignored qualification
    `0`, group control·resume `0`을 먼저 확인했습니다.
  - persistent service를 stop-first로 정지하고 stage가 private bundle을 current data volume에 교체한
    뒤 runtime을 시작했습니다. stage는 network `none`·exit `0`이었고 runtime은 exact Compose
    generation을 검증한 뒤 시작했습니다.
  - 첫 Compose 산출물은 one-shot 내부 template mount 표기를 host bind source로 보존해 Docker가
    존재하지 않는 source라고 거부했습니다. operator output상 새 stage와 runtime이 시작되기 전이었고,
    old deployment로 즉시 rollback했습니다. journal·receipt tree는 불변이었습니다.
  - 첫 rollback에서 old Compose build context를 다시 build해 persistent schema는 같지만 직전 reviewed
    image보다 오래된 utility closure인 image가 잠시 시작됐습니다. image binding 불일치를 확인한 뒤
    보존된 exact prior image id를 다시 tag해 강제 재생성했고, old discovery `86/86`, fresh `6/6`,
    retained command `0`, durable tree 불변을 재확인한 뒤에만 재시도했습니다.
  - 위 실패 산출물은 active deployment로 사용하지 않고 보존했습니다. v2는 template의 실제 host source
    존재, build context 존재와 `docker compose config` PASS를 먼저 확인한 별도 no-clobber generation입니다.
  - 부수적인 두 read-only postcheck 초안은 public product family를 product key로 잘못 사용한 호출과
    iterator typo로 각각 예외 종료했습니다. 둘 다 network/device command·journal·receipt mutation
    `0`이었고, corrected invocation만 아래 판정에 사용했습니다.
- 종료 지점(stage / failure code): `two_pro_d1l_qualified_control_deployed` PASS. 최종 stage exit `0`,
  runtime exactly `1`·running·restart `0`, startup 또는 안정화 구간 device command `0`입니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: private immutable merged set·Compose input bundle·descriptor와 pre/post journal·receipt
    tree는 **(b) preserved structured/durable daemon artifact**입니다. Docker/CLI 출력, broker retained
    audit와 authenticated fresh device read는 별도 raw 파일로 보존하지 않은 **(c) reconstructed
    operator observation**입니다. raw wire frame이 없으므로 (a)를 주장하지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: final Compose generation
    `c6bdde96217f64ab914b5f7582b8174c`. 첫 non-starting 산출물 generation은
    `8ef5a6d0c7c09a8f912d13a25cb5e8e7`이며 active로 사용하지 않았습니다.
  - UTC span (시작 / 종료): immutable merge는 `2026-09-12T13:44:07.094905Z`, exact two-Pro config는
    `2026-09-12T13:45:45.059083Z`에 생성됐습니다. 첫 non-starting Compose 산출물은
    `2026-09-12T13:51:55.038329Z`, corrected v2는 `2026-09-12T14:04:11.313206Z`에 생성됐습니다.
    final runtime은 `2026-09-12T14:05:06.334072Z`에 시작됐고 최종 durable 재독은
    `2026-09-12T14:09:20.540124Z` 이전에 끝났습니다.
  - SHA-256 identity binding: 첫 번째 Pro의 공개 `qualification-target:` anchor는
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`, 두 번째 Pro anchor는
    `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa`입니다. raw physical key는
    기록·argv·stdout에 싣지 않았습니다.
  - artifact digest (SHA-256): merged qualification set
    `1e4c549c1cab16a446b41b4e97578268c22310d719a3ba0662395b64b42c2c9e`, exact config
    `682a0b48406a37335561eb8de7096e29d542639a12367da013fe96f4d507f82e`, final Compose
    `96ac610e520c19dafb774cb1de1a2ac7bb15eb9bcb7a6abdf92ff7cd2892a2aa`, deployed bundle descriptor
    `8efa654a7cc13240027fc855635c1051aae61d28ac200d286a5cdc4232cb3974`, runtime manifest
    `e4b6f4b66b56faf13dcf33fa488de1b0d5ca9aff40a297686077f1b593c14f6d`입니다.
  - Compose bundle generation은 `c6bdde96217f64ab914b5f7582b8174c`이고 runtime이 fresh rederive한
    install-manifest generation은 `aad6c0be7de57c88ab15d1fdb7295ca8`입니다. 서로 다른 개념이며 같은
    generation이라고 주장하지 않습니다. runtime bundle의 config·qualification bytes는 descriptor와
    각각 위 `682a0b...`·`1e4c54...` digest로 exact 일치했습니다.
  - runtime catalog는 binding `1`·receipt `4`에서 qualified physical pair `2`를 유도했고 config의
    write-allowed exact 두 member와 일치했습니다. ignored qualification은 `0`입니다.
  - broker retained discovery는 projected `86/86`이 모두 present·byte exact이고 mismatch·missing `0`,
    owned tombstone non-empty `0`이었습니다. 두 `manual_flow` entity는 platform `number`, range
    `30..100`, `optimistic=false`, command topic 존재였고 retained command payload는 두 topic 모두
    `0`, global availability는 `online`이었습니다.
  - independent fresh read는 장비 `6/6` available, absent·ambiguous·fresh-read diagnostic `0`이었습니다.
    안정화 뒤 retained device availability도 `6/6 online`이었습니다.
  - group supervisor는 `1`이지만 armed `0`, command·resume surface `0`, cycle failure `0`이었고 final
    audit 시 fresh cycle `11`이었습니다. runtime log의 warning·error·traceback·exception은 `0`,
    service restart는 `0`이었습니다.
  - 종료 journal·qualification inventory와 두 canonical tree digest는 실행 전 값과 byte exact
    동일했고 unfinished journal은 `0`이었습니다. 모든 local-control physical frame 앞에는 journal-first
    operation 생성이 필요하다는 이 경로의 계약에 한정해 startup·deployment physical write `0`의
    durable 근거로 사용하며, 다른 write 하네스로 일반화하지 않습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 첫 non-starting attempt 뒤 old bundle과 exact prior
  reviewed runtime image를 실제로 다시 올려 discovery `86/86`·fresh `6/6`·journal/receipt 불변을
  확인했습니다. final v2는 성공해 원복하지 않았으며, old Compose bundle과 prior image id는 별도
  rollback 입력으로 남아 있습니다.
- **이 실행으로 새로 확정된 사실:** 새 D1l persistent deployment는 startup write 없이 두 Pro의
  `manual_flow`만 qualified HA number로 노출하고, 두 retained discovery 모두 app-declared
  `30..100`과 exact 일치합니다. 두 번째 Pro surface 개방과 기존 Pro의 승인 없던 `75` 축소 제거를
  한 generation에서 완료해 중간 surface `0` 구간을 만들지 않았습니다.
- 이 실행으로 확정하지 못한 것: Flow `100`의 장비 실수용이나 그 값의 live write, HA Core restart·
  홈서버 reboot 뒤 복귀, 장시간 안정성, power·Bar·group actuation, Q1·Q2, frozen native ASYNC 동작은
  이번 실행으로 판정하지 않습니다. 사람이 HA에서 `50`보다 큰 값을 입력할 수 있다는 capability
  공개는 확인했지만 attended agent operation 가이드와 혼동하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: Compose를 container 안에서 생성하더라도 bind
  source는 최종 Docker host에서 존재하는 absolute path로 결속하고 `docker compose config`뿐 아니라
  모든 bind source의 host 존재를 stop-first 전에 검사합니다. rollback은 old build context를 재빌드하지
  않고 보존한 exact prior image id와 old bundle을 사용합니다. 다음 M3 단계는 Bar template binding을
  write 없이 별도 generation으로 결속한 뒤 Bar `manual_flow`, 마지막으로 three-device `power`
  qualification 순서입니다.

## 정정 (2026-09-12T14:18:30Z)

초판에는 HA write surface 확대의 사용자 공개·수용 경위가 빠졌습니다. 배포 전에 persistent HA
`manual_flow` number가 `1 -> 2`로 늘어난다는 점을 공개했고 사용자는 추가 상시 노출과 진행을
승인했습니다. 이어서 기존 number를 포함한 사람이 입력 가능한 범위는 승인 근거 없던 `30..75`가
아니라 app-declared `30..100`이어야 한다고 사용자가 명시적으로 정정했으며, D1l source 수정과 두 Pro
재자격 뒤 작업 재개를 지시했습니다. 이 경위는 보존된 장비 artifact가 아니라 대화에서 재구성한
**(c) reconstructed operator observation**입니다.
