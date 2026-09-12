# run: 2026-09-12-local-manual-flow-requalification-second-pro

- 목적(한 문장): Flow capability를 app-declared `30..100`에 다시 결속한 D1l source로 두 번째
  Local Wavemaker Pro의 `manual_flow`를 `40 -> 39 -> 40` 재자격하고, 새 schema receipt pair를
  running authority와 분리된 candidate로 보존한 뒤 현재 시간대 Timer schedule 운전 복귀를
  확인했습니다.
- 이 실행이 답하려던 질문: 첫 번째 Pro에서 검증한 동일 D1l scalar writer와 protocol schema가
  두 번째 Pro에서도 exact target·single-write·journal-first·exact readback·planned restore 계약을
  유지하며, 새 generation을 배포하기 전에 두 번째 새-schema receipt pair를 만들 수 있는가.
- 사용자 승인 범위: Jebao 실기와 필요한 앱 상태 변경 승인을 적용했습니다. Flow `50`은 운전
  중인 수조에 갑작스러운 유량 충격을 주지 않기 위한 attended operation 가이드이며 장비
  capability·영구 상한·입력 validation gate가 아닙니다. agent 목적값은 `40`·`39`·`40`뿐이고
  상승 step은 없으며 최대 목적값은 `40`이었습니다. TimerON 복귀 뒤 장비 자체 schedule이 적용한
  Flow `45`는 agent가 보낸 manual-flow 목적값이 아닙니다. Home Assistant Core 업데이트·재기동·
  설정 변경은 수행하지 않았습니다.
- 실행한 source commit SHA:
  - D1l source는 `fe10bd5706a6c2c8b0e3a0b71be98bf7b7c62fc4`입니다. source archive
    SHA-256은 `82f4986c4e8fc41d3c1943513f96b99ce82ff7c95d2ff5a0f405f8bea5ccbf7f`,
    one-shot image identity binding SHA-256은
    `b3aa221d8d0d2abb5fabe6234bc6690473d827f816d01c15b291a0167efde379`입니다.
  - 실행 manifest의 app-catalog SHA-256은
    `8d5fa7604c1d7e612b6b6b73251c8a8a58d0b63db8b5d34a5459336a77e90be3`, 새
    protocol-schema SHA-256은
    `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`입니다. old
    generation의 schema
    `b5bd0c80930846ba19942e99a8c71f6fe68fa9566bea23f609f531fc0085a907`에 결속된 receipt와
    old-schema merged set을 current authority로 재사용하거나 변환하지 않았습니다.
- 실행 전 조건:
  - old persistent generation의 service exactly `1`, restart `0`, 기존 첫 번째 Pro
    `manual_flow` command surface `1`, 두 번째 Pro surface `0`, group command surface `0`,
    retained command `0`, unfinished local-control journal `0`, legacy write authority idle을
    확인했습니다.
  - current manifest의 두 Pro physical binding과 stable role binding, active Pro template
    SHA-256
    `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`는 불변이었습니다.
    두 Pro의 current firmware provenance는 byte 동일했고, 이번 target은 manifest에서 정확히
    하나로 resolved됐습니다.
  - 이 개체의 직전 qualification 기록은 TimerON·`independent`·latent manual Flow `40`, 현재
    적용 slot `sine`·Flow `45`·frequency `40`으로 끝났습니다. 이번 precondition에서 사용자는
    Jebao app으로 TimerON만 OFF로 바꿨고 latent manual Flow `40`은 바꾸지 않았습니다. agent의
    authenticated fresh read는 power ON·TimerOFF·manual mode `constant`·linkage `independent`·
    Flow `40`, 장비 `6/6`, absent·ambiguous `0`을 확인했습니다. 따라서 이전 기록과 이번 실행의
    상태 차이는 승인된 Timer ownership 전환으로 설명되며 manual Flow drift가 아닙니다.
  - 앱 조작은 local-control journal 경로를 거치지 않았고, 실행 전후 journal operations 증가는
    아래 qualification record 하나로 전부 설명됩니다. 앱이 내부적으로 보낸 physical frame 수는
    관측하지 않았습니다.
- 사용한 명령·주요 파라미터:
  - persistent control을 stop-first로 정지한 뒤 새 D1l one-shot image, opaque operation id
    `LCQ-D1L2-fe10bd5-1325Z`, downward step `1`, 위 active template digest, owner-only
    no-clobber candidate를 사용했습니다.
  - producer는 same-value reachability `40`, lower-power `39`, planned restore `40`의 세 phase를
    각각 새 writer로 실행했습니다. 각 phase는 journal-first 저장 뒤 physical frame 한 번과
    independent exact readback을 수행했고 forward retry는 없었습니다.
  - qualifier 종료 뒤 old generation의 persistent service만 다시 시작했습니다. 새 candidate merge,
    config flip, Compose generation과 persistent deployment는 실행하지 않았습니다.
  - 사용자는 qualification 종료 상태 TimerOFF·latent Flow `40`에서 TimerON만 다시 켰습니다.
    agent는 schedule이나 Timer frame을 보내지 않고 corrected read-only wrapper로 장비 `6/6`을 새로
    authenticated read해 current schedule 적용 상태를 확인했습니다.
  - postcheck 중 초기 read-only wrapper는 hardware-safety volume identity 활성화 전에 lease에서
    닫혔고, 다음 selector는 domain-separated 기록 anchor를 raw physical key로 오인해 target 결과를
    비운 채 종료했습니다. 둘 다 device command·journal·receipt mutation `0`이었고, 최종 결과는
    identity 활성화와 private logical-member resolution을 먼저 수행한 새 invocation에서 얻었습니다.
- 종료 지점(stage / failure code): `second_pro_d1l_manual_flow_candidate_verified` PASS.
  qualification transaction은 `VERIFIED` revision `10`, action `3/3` verified, physical write `3`,
  receipt `2`로 끝났고 recovery는 필요하지 않았습니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal과 referenced blob, 새-schema candidate binding·receipt 두 장,
    journal·qualification inventory를 fresh process로 재독한 범위는 **(b) preserved
    structured/durable daemon artifact**입니다. 사용자 app 조작, authenticated fresh device read,
    broker retained 관측과 임시 CLI result는 **(c) reconstructed operator observation**입니다.
    raw wire frame과 raw schedule image를 파일로 보존하지 않았으므로 (a)를 주장하지 않습니다.
  - opaque artifact id: `LCQ-D1L2-fe10bd5-1325Z`입니다.
  - UTC span: qualification journal은 `2026-09-12T13:26:07.024322Z`에 생성되고
    `2026-09-12T13:26:24.272383Z`에 terminal update됐습니다. old service는
    `2026-09-12T13:26:24.577252Z`에 다시 시작됐습니다. TimerON 복귀 뒤 final authenticated
    device observation은 `2026-09-12T13:34:38.800798Z`, broker·service와 durable artifact 최종
    재독은 `2026-09-12T13:38:37.261260Z` 이전에 끝났습니다.
  - SHA-256 identity binding:
    `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며, 이전 두 번째 Pro
    qualification 기록과 같은 물리 개체입니다.
  - artifact digest (SHA-256): terminal journal
    `f69b1fc9c4ad0feba98dc51343f391c29d0c9a4fa7016e4336c4eed8268a1895`, candidate
    qualification `977391689fb8865a341ef381e6c8a7e02bf734823143e309075d94b8cc2233f3`, final observed
    schedule image `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`입니다. 마지막
    값은 raw image를 별도 보존하지 않은 (c) 관측 digest이며 사후 재검증 가능한 (b)가 아닙니다.
  - candidate는 binding `1`, receipt `2`, qualified physical identity `1`이고 binding·receipt의
    schema digest는 위 새 protocol schema와 exact 일치했습니다. receipt digest는
    `1621472519468c8df59adea954e73f9e85e24157063c57d14818f91d81ef6de6`,
    `6fdb78759b597fa41637658d8585aef5f48b3df66817e14703b9beeff04363e6`이며 둘 다
    `2026-09-12T13:26:24.272383Z`에 완료됐습니다.
  - 세 action의 matched control response body는 모두 empty였고 ACK digest는 SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다. action diagnostic은
    모두 `null`이고 forward payload·expected readback은 각각 `40`·`39`·`40`과 일치했습니다.
  - 종료 journal inventory는 operations `9`·blobs `21`·files `30`, 이 run의 canonical tree
    SHA-256은 `b0280138220b7543d25a24fb4e500eb9b1e8d3c5a446d491663ecb0de81f4d6e`입니다.
    qualification receipt-root file count는 `10`, canonical tree SHA-256은
    `4eb2eac50901764c140533652f54475504ad238d14b84901c7a0323fb82824b5`였습니다.
  - 직전 journal operations `8`·blobs `19`·files `27`에서 이번 `9`·`21`·`30`으로 증가한
    operation 하나는 이 qualification으로 설명됩니다. 신규 content-addressed blob 두 개는
    qualification intent
    `23217a0f057187c01e7488ccb3b7fe9d46349c036da096383c87a4cfac2a9896` (`1,200` bytes)와 이
    개체의 full baseline status
    `2bb99cd2f5d12df49babfe9c73c79f67ca25188fccb2f152f901aba547ecf3a5` (`452` bytes)입니다.
    Flow `39`·`40` payload/readback과 empty ACK은 기존 content-addressed blob을 재사용했습니다.
  - TimerON 복귀 뒤 direct fresh state는 장비 `6/6`, absent·ambiguous `0`, 대상 power ON·TimerON·
    `independent`·latent manual Flow `40`을 반환했습니다. decoded schedule은 valid entry `14`,
    invalid slot `0`이고, 장비 local time에 active match는 slot `13` 하나였습니다. 현재 시간대
    `18:11–23:59`의 적용값은 `sine`·Flow `45`·frequency `40`이며 `AutoMode`·`AutoFlow`·
    `AutoFreq`와 일치했습니다. 다른 시간대 slot은 현재 상태 PASS 판정에 사용하지 않았습니다.
  - persistent service exactly `1`, restart `0`, global availability retained `online`, existing device
    command surface `1`, group command surface `0`, retained command payload `0`, unfinished journal
    `0`이었습니다. running authority는 old generation이므로 두 번째 Pro surface는 계속 `0`이고,
    사람이 보는 HA range도 아직 old `30..75`입니다.
- 원복 검증 결과:
  - qualification은 latent manual Flow `40`을 baseline으로 보존하고 `40 -> 39 -> 40`의 모든
    phase를 independent exact fresh readback으로 확인했습니다. 최종 latent manual Flow는 exact
    `40`이고 recovery frame은 필요하지 않았습니다.
  - 사용자의 TimerON 복귀 뒤 현재 시간대 schedule ownership과 적용값 `sine`·Flow `45`·frequency
    `40`을 authenticated fresh state로 확인했습니다. final observed schedule image digest는 앞선
    2026-09-11 두 번째 Pro 기록의 (c) 관측 digest와 같지만 어느 raw image도 보존하지 않았으므로
    schedule 전체의 byte-exact 원복을 주장하지 않습니다.
  - 새 candidate·scratch 외에 old qualification source와 running generation은 바꾸지 않았습니다.
    실패했더라도 old persistent authority와 기존 surface `1`이 별도 복원 동작 없이 남는 구조였습니다.
- **이 실행으로 새로 확정된 사실:** D1l app-declared-range scalar writer와 새 protocol schema는
  두 번째 Pro에서도 `40 -> 39 -> 40` 세 physical write와 exact readback으로 독립 receipt pair를
  만들었습니다. 두 Pro의 새-schema candidate가 각각 존재하지만 둘 다 running authority와
  격리돼 있고, 두 번째 Pro는 current schedule 운전으로 복귀했습니다.
- 이 실행으로 확정하지 못한 것: Flow `100`의 장비 실수용 여부나 그 값의 live write, 두 새
  candidate의 fresh merge·새 Compose generation·HA `30..100` surface, Bar `manual_flow`, 세 장비
  `power`, group actuation, 장시간 운용, Q1·Q2·native ASYNC capability는 증명하지 않습니다. 앱의
  내부 physical frame 수와 schedule 전체의 byte-exact equality도 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: source range 변경 뒤 schema-bound receipt를
  재자격하는 현재 설계를 우회하지 않습니다. 이 기록을 독립 검토·commit한 뒤에만 두 새 candidate를
  fresh immutable set으로 merge하고, exact result union과 새 bundle digest를 확인한 뒤 단 한 번의
  새 generation으로 두 Pro surface를 배포합니다. old-schema merge는 미배포 obsolete artifact로
  보존합니다. 공개된 domain-separated identity anchor를 raw physical key로 사용하지 않고, private
  logical member를 current manifest에서 exact resolve해 도구 입력을 유도합니다.
