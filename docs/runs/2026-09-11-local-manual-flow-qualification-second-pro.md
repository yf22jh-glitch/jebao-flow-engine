# run: 2026-09-11-local-manual-flow-qualification-second-pro

- 목적(한 문장): 아직 command surface가 없는 두 번째 Local Wavemaker Pro에서 기존
  `manual_flow` qualification producer를 그대로 사용해 `40 -> 39 -> 40`을 검증하고,
  receipt pair를 running authority와 분리된 candidate로 보존한 뒤 사용자의 Timer schedule
  운전 복귀 상태까지 확인했습니다.
- 이 실행이 답하려던 질문: 첫 번째 Pro에서 검증된 동일 producer가 같은 firmware·content
  결속을 가진 두 번째 물리 개체에서도 exact readback과 restore를 만족하는 독립 receipt pair를
  만들며, merge·deploy 전에는 그 개체의 command surface를 열지 않는가.
- 사용자 승인 범위: Jebao 실기와 필요한 장비 상태 변경, agent가 보내는 모든 Flow를 `50`
  이하로 제한한다는 승인을 적용했습니다. 이번 agent write는 `40`·`39`·`40` 세 번뿐입니다.
  Home Assistant Core 업데이트·재기동·설정 변경은 수행하지 않았습니다.
- 실행한 source commit SHA:
  - qualification producer와 그 의존성은 기존 reviewed deployment source
    `7ecabbc2422ea5b3875dbed279cb354fa8e9f30c`의 바이트를 사용했습니다.
  - 실행 전 running image의 installed Python file `131`개는 current source의 대응 파일과
    byte-exact였고, installed tree SHA-256은
    `7c768e57d682a714563c6de6a5458e6bd60791e36b4c9b6dab382126a5c72970`이었습니다. current
    source에만 추가된 D1j·D1k 모듈 네 개는 이 producer 실행에 사용하지 않았습니다. image
    digest는 `855a15c1c86870ac461a6af56502486a2c115646df11455e42f8d01438a50f96`입니다.
- 실행 전 조건:
  - current manifest의 Pro target은 `2`, running `manual_flow` qualified physical target은 `1`,
    이번 target의 command surface는 `0`이었습니다. active template SHA-256은
    `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`, stable role-binding
    SHA-256은 `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`였습니다.
  - fresh resolution은 장비 `6/6`을 exact하게 찾았고 두 Pro의 current firmware provenance는
    byte 동일했습니다. unfinished local-control journal `0`, retained command `0`, legacy write
    authority idle을 확인했습니다.
  - 이 target의 최초 관측은 power ON·TimerON·manual mode `constant`·linkage `independent`·latent
    manual Flow `30`이었습니다. qualification producer의 안전 전제 때문에 사용자가 Jebao app에서
    TimerOFF·Flow `40`으로 바꿨고, agent가 authenticated fresh read로 exact precondition을 다시
    확인했습니다.
  - 시작 inventory는 journal operations `5`·blobs `12`·files `17`, canonical tree SHA-256
    `2803b6ee8361321014c449e5665fbb9772c3520bb3fbb976307ee7401788cb86`; qualification root는
    files `4`·receipt `2`, canonical tree SHA-256
    `36b4c6ed52475565c1bd98dd709ea13d8ef4589fd58ecbb049c38ef36f9b664d`였습니다.
- 사용한 명령·주요 파라미터:
  - persistent control을 stop-first로 정지하고, opaque operation id
    `LCQ-718514863760542a5ee7e3502bdf4267`, downward step `1`, 위 active template digest,
    running authority와 다른 owner-only no-clobber candidate를 사용해 기존 qualification CLI를
    실행했습니다.
  - producer는 same-value reachability `40`, lower-power `39`, planned restore `40`의 세 fresh
    phase를 각각 새 writer로 실행했습니다. merge·Compose generation·deployment는 실행하지
    않았습니다.
  - 첫 operator wrapper는 hardware-safety volume identity를 활성화하기 전에 journal을 읽으려 해
    CLI 호출·journal 생성·device session·frame 전인
    `2026-09-11T09:56:52.890548Z` ~ `2026-09-11T09:56:54.834909Z`에 종료됐습니다. device
    write는 `0`이었고, identity 활성화를 가장 먼저 하도록 순서만 바로잡았습니다.
  - 종료 postcheck wrapper도 availability topic 유도 가정 한 번과 volume identity 활성화 순서 한
    번을 read-only 단계에서 바로잡았습니다. 두 경우 모두 device command와 journal mutation은
    `0`이었고, 최종 postcheck는 fresh process에서 다시 수행했습니다.
  - qualification 뒤 service를 manual stop/start로 복귀시켰습니다. reconnect·soak 기록이
    container 재생성 전까지 기대한다고 적은 restart baseline `1`은 이 manual stop/start에서
    `0`으로 재기준화됐습니다. 이후 같은 container의 기대값은 `0`이며, 재기준화 조건에는
    container 재생성뿐 아니라 관측된 manual stop/start도 포함됩니다.
  - 사용자는 qualification 종료 상태 TimerOFF·latent Flow `40`에서 TimerON으로 복귀시켰습니다.
    agent는 schedule을 쓰지 않고, 별도 read-only authenticated session으로 current applied
    `AutoMode`·`AutoFlow`와 48-slot schedule image를 decode했습니다.
- 종료 지점(stage / failure code): `second_pro_manual_flow_candidate_verified` PASS. qualification
  transaction은 `VERIFIED` revision `10`, action `3/3` verified, physical write `3`, receipt `2`로
  끝났습니다. persistent service는 exactly `1`, restart `0`, availability `online`, retained command
  `0`, group armed `0`, unfinished journal `0`입니다. running command surface는 기존 Pro `1`,
  이번 두 번째 Pro `0`입니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal, referenced baseline·expected·ACK blob, candidate binding과 receipt
    두 장, journal·qualification inventory를 fresh process로 재독한 범위는 **(b) preserved
    structured/durable daemon artifact**입니다. 사용자 app 조작·화면, direct authenticated
    read, MQTT retained 관측과 임시 CLI JSON은 **(c) reconstructed operator observation**입니다.
    raw wire frame과 최종 raw schedule image를 파일로 보존하지 않았으므로 (a)를 주장하지
    않습니다.
  - opaque artifact id: `LCQ-718514863760542a5ee7e3502bdf4267`.
  - UTC span: 성공한 qualification invocation은 `2026-09-11T09:57:21.105738Z` ~
    `2026-09-11T09:57:45.637356Z`, journal create는
    `2026-09-11T09:57:28.176257Z`, terminal update는
    `2026-09-11T09:57:45.409941Z`였습니다. TimerON 복귀 뒤 최종 service·device postcheck는
    `2026-09-11T10:07:34.194353Z` 이전, schedule 상세 fresh read는
    `2026-09-11T10:13:35.007215Z` 이전에 끝났습니다.
  - SHA-256 identity binding:
    `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며, 이 기록에서
    qualification한 두 번째 Pro에만 결속합니다.
  - artifact digest (SHA-256): terminal journal
    `f0e1bbfe6867b7ac17c822e210e7f2e3972e1f6b078e00ae952df463480b4d2b`, candidate qualification
    `4293b4aac05923bcec25cb605c0edc044be7ef492775a25c8fd57b7e48d71ee9`, final observed schedule
    image `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`입니다. 마지막 값은 raw
    image를 별도 보존하지 않은 (c) 관측 digest이므로 사후 재검증 가능한 (b)로 올리지 않습니다.
  - receipt digest는
    `8e0c049656a400ce459819cc9d2fed41d41858125978966907355a50ed709d6d`,
    `1ddbb89d2228f2fce128be74daef11551844ed1881f6cf3287d373012f643250`입니다. 세 action의 matched
    control response body는 모두 empty였고 ACK digest는 SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다.
  - 종료 inventory는 journal operations `6`·blobs `14`·files `20`, canonical tree SHA-256
    `9e2e3f86d5ab9f660ae9658b574102708d8872f80e5589a3f0d5efea5dedf546`; qualification root는
    files `6`·receipt `4`, canonical tree SHA-256
    `4c4ba99f0f9fe89b4fcef478ac0021f1ed1a3b00f765062743113876c883b34a`였습니다. candidate
    binding 자체는 running qualification input과 분리돼 있습니다.
  - journal blob `12 -> 14`의 두 신규 content-addressed blob은 top-level record가 참조하는
    qualification intent `767f840a60afe52927e189ded973e1ebff824c5cb95d7dfffced0f1e0a14309f`
    (`1,214` bytes)와 세 action이 공유하는 이 장비의 full baseline status
    `e385c12cb0b2f6a7c88a9f4656b58fa4a9a644bcc144e7cfbc735efbe49cb827` (`452` bytes)입니다.
    Flow payload·scalar readback·empty ACK blob은 앞선 같은-product `40 <-> 39` 실행의
    content-addressed bytes를 재사용했습니다.
  - TimerON 복귀 뒤 direct fresh state는 power ON·TimerON·`independent`, current applied
    `AutoMode=sine`·`AutoFlow=45`·`AutoFreq=40`을 반환했습니다. 사용자 app 화면의 현재 구간
    `Sine`·Flow `45`·Frequency `40`과 일치합니다.
  - decoded schedule은 유효 entry `14`, invalid slot `0`이고 read 시점의 active match는 slot
    `13` 하나였습니다. 현재 시간대 `18:11–23:59`에 적용되는 값은 `sine`·Flow `45`·frequency
    `40`입니다. 다른 시간대 slot은 현재 상태 PASS 판정에 사용하지 않았습니다.
  - agent는 schedule frame을 보내지 않았고 이번 qualification write의 최대값은 `40`입니다.
- 원복 검증 결과:
  - qualification transaction 자체는 precondition latent manual Flow `40`을 baseline으로 보존하고
    `40 -> 39 -> 40` exact fresh readback으로 복원했습니다. recovery frame은 필요하지 않았습니다.
  - 사용자 후속 조작으로 TimerON schedule ownership은 복귀했고 현재 applied schedule output은
    `sine`·Flow `45`로 확인됐습니다. 따라서 운영상 원래 schedule 제어 상태는 복귀했습니다.
    다만 TimerOFF일 때만 쓰이는 latent manual Flow는 최초 `30`에서 `40`으로 남았으므로 최초
    full state와 byte-exact 원복됐다고 주장하지 않습니다. 이후 이 Pro를 TimerOFF로 바꾸면
    최초 `30`이 아니라 latent manual Flow `40`으로 운전된다는 지연된 차이가 있습니다.
  - 실행 전 raw schedule image를 보존하지 않았으므로 current schedule image가 실행 전과
    byte-exact 동일하다고도 주장하지 않습니다. producer가 schedule field를 쓰지 않았다는 source
    범위와 종료 current observation만 확인했습니다.
- **이 실행으로 새로 확정된 사실:** 동일 reviewed producer는 두 번째 Pro에서도 세 physical
  write와 action별 independent exact readback으로 `manual_flow` receipt pair를 만들었습니다.
  그 authority는 candidate에만 있고 merge·deploy 전인 현재 두 번째 Pro command surface는 `0`입니다.
  TimerON 복귀 뒤 현재 적용된 schedule mode·Flow는 `sine`·`45`이며 user app 화면과 authenticated
  fresh device state가 일치합니다.
- 이 실행으로 확정하지 못한 것: 실행 전후 full schedule image의 byte equality, 최초 latent
  manual Flow `30` 복원, candidate merge·새 Compose generation·두 번째 Pro command surface,
  Bar `manual_flow`, 세 장비의 `power`, group actuation, 장시간 운용, Q1·Q2·native ASYNC capability는
  증명하지 않습니다. 다른 시간대 schedule parameter를 변경하거나 현재 상태 판정에 사용하는
  일도 이 operation에서 하지 않았습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: qualification 전에는 manual scalar뿐 아니라
  Timer ownership과 schedule image digest도 baseline으로 보존해 후속 원복 의미를 분리합니다.
  current operation은 이 기록과 독립 검토·commit으로 닫고, 사용자 지시대로 다음 M3
  merge·generation·deployment·live operation은 시작하지 않은 채 대기합니다.
