# run: 2026-09-12-local-bar-active-template-deployment

- 목적(한 문장): current app loader로 재결속한 exact Bar template을 별도 durable root에 설치하고
  새 immutable Compose generation으로 배포하되, 기존 두 Pro `manual_flow` authority와 전체 HA
  discovery·장비 상태를 device command 없이 유지합니다.
- 이 실행이 답하려던 질문: Bar template membership을 `1 -> 2`로 늘려도 기존 Pro receipt 네 장이
  current로 남고, Bar는 command 없는 diagnostic surface만 유지하며, startup write 없이 장비
  `6/6` fresh observation으로 복귀하는가.
- 사용자 경계:
  - 사용자는 직접 앱 조작이 필요한 단계 외의 작업을 계속 진행하도록 승인했습니다. 이 실행에는
    앱 조작과 device command가 없고, Bar `manual_flow`·`power` qualification, group actuation,
    HA Core update·restart, 홈서버 reboot와 frozen native ASYNC 하네스는 수행하지 않았습니다.
  - `30..100`은 app-declared Flow capability이고 `50`은 돌아가는 수조의 급격한 유량 변화를 피하기
    위한 attended operation 가이드입니다. 이번 실행은 Flow 목적값을 만들거나 보내지 않았습니다.
- 실행한 commit SHA:
  - D1l runtime source는 `fe10bd5706a6c2c8b0e3a0b71be98bf7b7c62fc4`, D1m 정책은
    `689ca451d154ebcb35b551e82de44df6845f1374`입니다.
  - Bar static binding 근거와 그 append-only 정정은
    `9327da53d4885548c32a91dd1749288e6fb975b2`와
    `0d345da40db881e2c7a066f1fbac531ae5ba462a`입니다.
- source·deployment 결속:
  - 재사용한 exact reviewed runtime image id의 SHA-256 binding은
    `a1edf68a90a2f45a64dffb6994d7c817aedccc7a1e1863148da6bbf0a9820b71`입니다. 새 image를
    build하지 않았습니다.
  - 기존 Pro template SHA-256은
    `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`, 새로 설치한
    162,553-byte Bar template SHA-256은
    `e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`입니다.
  - Bar file은 별도 새 durable root의 `index.js` 하나로 설치했고 mode `0444`, link count `1`,
    directory mode `0755`, `index.html` `0`을 digest·size 재독 뒤 확인했습니다. file·directory·
    deployment directory·parent directory를 fsync한 뒤에만 Compose input으로 사용했습니다.
- 실행 전 조건:
  - running service exactly `1`, restart `0`, exact prior image, old daemon `0`, global·device
    availability `7/7 online`, independent authenticated fresh read `6/6`, power ON `6/6`, retained
    command payload `0`, unfinished journal `0`, group command·resume surface `0`을 확인했습니다.
  - broker의 current discovery는 expected `86/86` present·byte exact, mismatch `0`이었습니다. 이
    실행에서 정의한 sorted `(topic, SHA-256(payload))` compact-JSON digest는 배포 전
    `a4b99bff620ad42c9c67a55893bfd1ba53b3b5b0909df164c5b13349cf8aac78`이었습니다.
  - journal은 operations `9`·blobs `21`·files `30`, qualification receipt root는 files `10`이고,
    이 실행의 sorted `(relative path, SHA-256(bytes))` compact-JSON tree digest는 각각
    `88df6b2001afbf3661c5f6e8416269f1ab38080fda2d165ae6c7c6878e2bcd5a`와
    `50614df6032efb95e83bebb96411443fca741a7fa82ab209a9338449eb7b56e5`였습니다. 이
    canonicalization은 이 run의 전후 비교에만 쓰며 앞선 기록의 다른 tree digest와 직접 비교하지
    않습니다. count `9/21/30`·`10`과 unfinished `0`은 직전 배포 기록에서 이어졌습니다.
- 배포 전 예상 delta:
  - runtime UID/GID와 같은 verifier context로 새 template set을 offline projection한 결과 manifest
    device `6`, qualification binding `1`·receipt `4`, qualified Pro pair `2`, ignored qualification
    `0`, control entity `2`, group control·resume `0`이었습니다.
  - Bar diagnostic entity는 이미 `13`개였으므로 예상 entity 증감은 `0`, Bar command topic은 계속
    `0`입니다. 전체 discovery record도 `86`개로 byte 동일하고, 기존 두 Pro number payload도
    byte 동일해야 한다고 배포 전에 선언했습니다. owned tombstone은 `57`개이며 digest는
    `d55d7863b278d92b30cc3b39970f6d845a1d8e516518868165cb7b2a88cb6642`입니다.
  - 여섯 `physical_identity_key` 집합 digest
    `5c0a9b8943832df7f8fc30472a6b57af39a5ad3b997fce34dca56b6a80da4a19`와 stable
    role-binding SHA-256
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`는 불변이어야 한다고
    먼저 고정했습니다.
- 사용한 명령·주요 파라미터:
  - 새 template root와 owner-only config·qualification source copy를 만든 뒤
    `jebao-flow-local prepare-compose --write-enabled`로 template root 정확히 `2`개인 새 bundle을
    생성했습니다. current manifest·current qualification source·running generation은 in-place로
    수정하지 않았습니다.
  - stop-first 전에 final Docker host에서 모든 bind·secret source의 존재와 digest, template
    metadata, prior Compose SHA-256, exact image id, `docker compose config`, stage network `none`,
    runtime network `host`·restart `unless-stopped`를 다시 검증했습니다.
  - persistent runtime을 완전히 정지해 같은 service instance가 `0`임을 확인한 뒤 새 Compose를
    `--no-build --force-recreate`로 시작했습니다. stage는 exit `0`, runtime은 exact image와 Compose
    generation label을 확인한 뒤 postcheck로 넘어갔습니다.
- 실행 중 정정:
  - 첫 prepare wrapper는 Docker mount option bundle을 잘못 구성해 container 생성 전에 끝났고,
    두 번째는 one-shot entrypoint를 지정하지 않아 `_prepare_current` 전에 usage error로 끝났습니다.
    세 번째는 generated read-only secret를 owner-only config source로 잘못 사용해 manifest 생성
    전에 metadata 검사에서 끝났습니다. 세 경우 모두 service·broker·device·journal·receipt 접촉과
    device write는 `0`입니다.
  - corrected prepare는 artifact를 정상 생성했지만 wrapper가 Compose generation과 fresh manifest
    generation을 같은 값으로 비교해 사후 판정을 실패했습니다. 두 generation이 다른 개념임을
    반영한 fresh reload가 bundle·descriptor·manifest를 통과한 뒤 그 artifact만 사용했습니다.
  - 첫 offline projection 초안은 SSH 사용자 UID로 receipt를 검증해 container runtime 소유 artifact를
    metadata mismatch로 거부했습니다. durable output·device·broker 접촉 `0`이었고, runtime과 같은
    UID/GID의 read-only rerun만 예상 delta에 사용했습니다.
  - 배포 뒤 첫 postcheck 초안은 entity에 없는 attribute를 참조해 projection 생성 뒤 종료했습니다.
    journal 생성·poll·MQTT 연결 전이었고 corrected postcheck만 아래 판정에 사용했습니다.
- 종료 지점(stage / failure code): `bar_template_membership_deployed` PASS. final stage exit `0`, runtime
  exactly `1`·running·restart `0`, exact reviewed image·Compose generation, startup device write `0`입니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: installed template은 digest로 보존한 deployment input이고 장비 결과의 증거등급으로
    올리지 않습니다. immutable Compose input bundle·descriptor, runtime manifest와 pre/post
    journal·qualification tree는 **(b) preserved structured/durable daemon artifact** 범위로 사용합니다.
    Docker·CLI output, broker audit와 independent authenticated device read는 raw 파일로 보존하지
    않은 **(c) reconstructed operator observation**입니다. raw wire frame이 없어 (a)는 주장하지
    않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: Compose generation
    `5d2fe97ef3117d59930a4531295c3dea`; runtime이 fresh rederive한 install-manifest generation
    `794f40b612782be0eec8f29971247c15`입니다. 두 값을 같은 generation으로 취급하지 않습니다.
  - UTC span (시작 / 종료): 새 durable input 준비 시작
    `2026-09-12T14:38:25.931093000Z`, final runtime 시작
    `2026-09-12T14:52:37.350895378Z`, 마지막 안정화 확인은
    `2026-09-12T14:57:10.224346872Z` 이전에 끝났습니다.
  - SHA-256 identity binding: 여섯 physical identity의 sorted-set digest는 위 `5c0a9b…a19`, stable
    role binding은 위 `901a1c…e8dd`로 배포 전후 byte 동일했습니다. 이번 operation은 단일 물리
    target write가 아니므로 개별 `qualification-target:` anchor를 새로 주장하지 않습니다.
  - artifact digest (SHA-256): Compose
    `9a19eff92aeb539b355d6ad7249ad7b21543a7ef53deb5fb329b23f0bddf7999`, bundle descriptor
    `5d50eea972efcd27c91b0fa0daa222ab8b9a3ee94a6f991cdf6c84e6e75b3444`, exact config
    `682a0b48406a37335561eb8de7096e29d542639a12367da013fe96f4d507f82e`, exact qualification set
    `1e4c549c1cab16a446b41b4e97578268c22310d719a3ba0662395b64b42c2c9e`, runtime manifest
    `3599c62286d53173a506a934e7cb9f44bcaf4f5fc23321e9766f3ab85b484e35`입니다.
  - runtime manifest의 template count는 정확히 `2`, sorted template-set digest는
    `a2513a45d64db98b25b781f6b9fb24ef857ee5723a71a9fbb6ebcfadf0943eb2`이고 두 host template
    mount는 read-only였습니다. qualification binding `1`·receipt `4`, ignored `0`, Pro control
    entity `2`, Bar entity `13`·command `0`, group command·resume `0`으로 예상 delta와 같았습니다.
  - broker retained discovery는 expected `86/86` present·byte exact, missing·mismatch `0`, owned
    tombstone `57`개 중 non-empty `0`이었습니다. 배포 후 같은 canonical digest가 위
    `a4b99b…ac78`로 배포 전과 byte 동일하므로 기존 두 Pro number payload도 함께 불변입니다.
    retained command payload는 `0`, global·device availability는 `7/7 online`이었습니다.
  - independent authenticated fresh read는 장비 `6/6` available·power ON, unavailable diagnostic
    `0`이었습니다. group supervisor `1`, armed `0`, command·resume `0`, cycle failure `0`, final
    fresh cycle `15`, old daemon `0`, runtime warning·error·traceback·exception `0`이었습니다.
  - 종료 journal은 operations `9`·blobs `21`·files `30`, qualification root는 files `10`이고 두
    tree digest가 실행 전과 byte exact 동일하며 unfinished journal은 `0`입니다. 모든 local-control
    physical frame 앞에 journal-first operation 생성이 필요한 이 coordinator 경로에 한정해 startup·
    deployment device write `0`의 durable 근거로 사용하며 다른 write 하네스로 일반화하지 않습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): final deployment가 성공해 rollback을 실행하지
  않았습니다. exact prior Compose bundle·image id는 별도 rollback input으로 유지했고, rollback은
  build 없이 그 두 보존값만 사용하도록 준비했습니다. host-side prior config·qualification source와
  앞선 generation을 in-place로 바꾸지 않아 권한·content rollback 단위도 generation 하나입니다.
- **이 실행으로 새로 확정된 사실:** current Bar template을 manifest membership에 추가해도 기존
  Pro receipt 네 장은 current로 남고, 기존 전체 discovery와 Pro control payload는 byte 불변이며,
  Bar는 기존 diagnostic entity `13`·command `0`을 유지합니다. 배포는 startup write 없이 fresh
  `6/6`으로 복귀했습니다.
- 이 실행으로 확정하지 못한 것: Bar firmware의 `manual_flow`·`power` acceptance와 live write,
  Flow `100`의 실제 수용, HA Core restart·홈서버 reboot 뒤 복귀, group actuation, 장시간 안정성,
  Q1·Q2와 frozen native ASYNC 동작은 판정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: positive static binding은 digest·size를 즉시
  append-only로 보존하고, Compose의 source path가 아니라 final host bind 존재를 stop-first 전에
  검증합니다. offline projection에서 예상 entity·payload delta를 먼저 선언하고, 배포 후 전체
  discovery byte equality로 기존 surface 불변을 대조합니다. 다음은 Bar `manual_flow` 첫 물리 write
  직전까지 준비하되 앱 조작이나 write가 필요해지는 경계에서 현장 감시·전원 차단 수단을 확인합니다.
