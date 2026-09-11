# run: 2026-09-12-local-manual-flow-requalification-first-pro

- 목적(한 문장): 승인 근거 없이 들어간 Flow `75` 제한을 제거한 D1l source에 대해 첫 번째
  Local Wavemaker Pro의 `manual_flow`를 `40 -> 39 -> 40`으로 다시 자격 검증하고, 새
  protocol schema에 결속된 receipt pair를 running authority와 분리된 candidate로 보존했습니다.
- 이 실행이 답하려던 질문: app-declared Flow `30..100`을 공통 capability source로 사용하는
  D1l scalar writer가 기존 qualified 첫 번째 Pro에서도 exact target·single-write·journal-first·
  exact readback·planned restore 계약을 유지하며, old generation을 바꾸지 않고 새 receipt pair를
  만들 수 있는가.
- 사용자 승인 범위: Jebao 실기와 필요한 장비 상태 변경 승인을 적용했습니다. Flow `50`은
  운전 중인 수조에 갑작스러운 유량 충격을 주지 않기 위한 attended operation 가이드이며 장비
  capability·영구 상한·입력 validation gate가 아닙니다. 실행 전 장비 Flow는 `41`, agent가 보낸
  목적값은 `40`·`40`·`39`·`40`뿐이어서 상승 step은 없고 최대 목적값은 `40`이었습니다. Home
  Assistant Core 업데이트·재기동·설정 변경은 수행하지 않았습니다.
- 실행한 source commit SHA:
  - D1l source는 `fe10bd5706a6c2c8b0e3a0b71be98bf7b7c62fc4`입니다. source archive
    SHA-256은 `82f4986c4e8fc41d3c1943513f96b99ce82ff7c95d2ff5a0f405f8bea5ccbf7f`,
    one-shot image identity binding SHA-256은
    `b3aa221d8d0d2abb5fabe6234bc6690473d827f816d01c15b291a0167efde379`입니다.
  - 실행 manifest의 app-catalog SHA-256은
    `8d5fa7604c1d7e612b6b6b73251c8a8a58d0b63db8b5d34a5459336a77e90be3`,
    새 protocol-schema SHA-256은
    `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`입니다. old
    generation의 schema SHA-256
    `b5bd0c80930846ba19942e99a8c71f6fe68fa9566bea23f609f531fc0085a907`와 다르므로 기존
    receipt를 변환하거나 current로 오인하지 않았습니다.
- 실행 전 조건:
  - old persistent generation은 기존 reviewed image로 실행 중이었고 첫 번째 Pro의
    `manual_flow` command surface `1`, 두 번째 Pro surface `0`, retained command `0`, group armed
    `0`, unfinished local-control journal `0`, legacy write authority idle을 확인했습니다.
  - current manifest의 physical binding과 stable role binding은 D1l manifest 재유도 전후
    불변이었습니다. active Pro template SHA-256은
    `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5` 하나였습니다.
  - authenticated fresh state에서 대상은 power ON·TimerOFF·manual mode `constant`·linkage
    `independent`·Flow `41`이었습니다. qualifier의 고정 baseline `40`을 만들기 위해 이미 qualified된
    current MQTT control로 `41 -> 40`을 먼저 수행하고 exact fresh readback을 확인했습니다.
  - 이 개체의 직전 external-app-drift 기록은 Flow `40`으로 끝났지만 이번 실행 시작 관측은
    `41`이었습니다. 두 기록 사이의 변경 주체·원인은 확인하지 못했습니다. 직전 종료
    operations `6`에서 이번 종료 `8`까지 늘어난 두 record는 아래 precondition과 qualification으로
    모두 설명되므로, 그 `40 -> 41`에 대응하는 별도 local-control journal transaction은 없습니다.
    따라서 이번 `41 -> 40`은 즉시 관측한 `41`을 보존하는 원복이 아니라 시리즈의 마지막 기록값
    `40`으로 복귀시키는 승인된 precondition이었습니다.
  - offline scratch 준비 중 첫 wrapper는 durable 파일 생성 뒤 결과 JSON을 출력하는 부분에서
    import 누락으로 종료됐습니다. 별도 read-only verifier가 exact 파일과 digest를 다시 확인했고,
    이 준비 단계에는 device·broker 접촉과 journal·receipt mutation이 없었습니다.
- 사용한 명령·주요 파라미터:
  - precondition request id `mqtt_c20bdefce5db685d3f4c762ea666d5f6`으로 기존 qualified MQTT
    경로에서 Flow `40`을 한 번 보냈습니다. transaction은 action `1/1` verified, physical write
    `1`, revision `4`로 끝났습니다.
  - persistent control을 stop-first로 정지한 뒤, 새 D1l one-shot image와 opaque qualification
    operation id `LCQ-D1L1-fe10bd5-1453Z`, downward step `1`, 위 active template digest,
    owner-only no-clobber candidate를 사용했습니다.
  - producer는 same-value reachability `40`, lower-power `39`, planned restore `40`의 세 phase를
    각각 새 writer로 실행했습니다. action마다 journal-first 저장 뒤 physical frame 한 번과
    independent exact readback을 수행했습니다.
  - qualifier 종료 뒤 old generation의 persistent service만 다시 시작했습니다. 새 D1l image는
    one-shot qualification에만 사용했고, merge·새 Compose generation·persistent deployment는
    실행하지 않았습니다.
- 종료 지점(stage / failure code): `first_pro_d1l_manual_flow_candidate_verified` PASS.
  qualification transaction은 `VERIFIED` revision `10`, action `3/3` verified, physical write `3`,
  receipt `2`로 끝났고 recovery는 필요하지 않았습니다. precondition을 포함한 이 operation 전체의
  agent physical write는 정확히 `4`입니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal 두 건과 referenced blob, 새-schema candidate binding·receipt 두 장,
    journal·qualification inventory를 fresh process로 재독한 범위는 **(b) preserved
    structured/durable daemon artifact**입니다. authenticated fresh device read, broker retained
    state·discovery와 임시 CLI result는 **(c) reconstructed operator observation**입니다. raw wire
    frame을 별도 보존하지 않았으므로 (a)를 주장하지 않습니다.
  - opaque artifact id: precondition `mqtt_c20bdefce5db685d3f4c762ea666d5f6`, qualification
    `LCQ-D1L1-fe10bd5-1453Z`입니다.
  - UTC span: precondition journal은 `2026-09-11T14:58:09.503774Z` 생성,
    `2026-09-11T14:58:10.302149Z` terminal update였습니다. qualification journal은
    `2026-09-11T14:59:49.563868Z` 생성,
    `2026-09-11T15:00:06.763898Z` terminal update였습니다. old service는
    `2026-09-11T15:00:08.212892Z`에 다시 시작됐고, 종료 broker·device observation과 durable
    artifact 재독은 `2026-09-11T15:05:36Z` 이전에 완료했습니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며, first-write부터
    external-drift 기록까지 이어진 첫 번째 Pro와 같은 물리 개체입니다.
  - artifact digest (SHA-256): precondition terminal journal
    `2998d867bd6b77838277e2584b4de08db1163b72e87b5b5fc1948f195b47bd60`, qualification
    terminal journal
    `f7970c7c211173c8a4ed770ad93f0ad33c58787ef36e6c13b363c86da7081c14`, candidate
    qualification `1e88d71583c38f61e6e7b7adbf26063390648f246d5b64197d2f7743f186fc06`입니다.
  - 새 candidate는 binding `1`, receipt `2`, qualified physical identity `1`이고, binding의 schema
    digest는 실행 manifest의 새 schema digest와 exact 일치했습니다. receipt digest는
    `6d3e71a36b204b59a0812cb4503fbf735ba5fb7ec0f98845e5ed1f793d1c2db0`,
    `33eac6fa6facf2b9e7a2b420ec17c7ab81d2bb55477ab191c7fd0ed0e82599d3`이며 둘 다
    `2026-09-11T15:00:06.763898Z`에 완료됐습니다.
  - qualifier 세 action의 matched control response body는 모두 empty였고 ACK digest는
    SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다.
  - 종료 journal inventory는 operations `8`·blobs `19`·files `27`, 이 run의 canonical tree
    SHA-256은 `f2b942c9a69ec94e48692b9c333a82ef1fa1ed9b6435cee0f4595817ff005931`입니다.
    qualification receipt-root file count는 `8`, canonical tree SHA-256은
    `93e35d22c72701ae775f1359c5a8f678af9dc862c499ab4e1eb42fd7dfd2036b`였습니다.
  - 직전 operations `6`·blobs `14`에서 이번 `8`·`19`로 늘어난 blob `5`개는 precondition의
    Flow `41` rollback payload
    `48c7a9238509556c5acc016c08eac5c6c86d0c8e27bb58d111e88bfb05dfec31` (`460` bytes),
    baseline scalar readback
    `e69e124b04532f87ec5ecc425e2af8708b531ec9cbe91d38ed7bcffbce51ee40` (`11` bytes),
    baseline status
    `296ea16bcb87ee311a5ddd6ad96affe35101baf1259c0fdb6f56a87d2fe9bafc` (`452` bytes),
    qualification intent
    `6cd454b0113a6669a6bb43073bf20092cb40d17db3bbc5f54ccbb7a6ed7a8bfe` (`1,200` bytes),
    qualifier baseline status
    `ee6bae19372c8a8d62447c26d4ab4c16455995f7fafaf1ed36b38c7f8a9c9d20` (`452` bytes)입니다.
    Flow `39`·`40` payload/readback과 empty ACK은 기존 content-addressed blob을 재사용했습니다.
  - 종료 fresh resolution은 장비 `6/6`, absent·ambiguous `0`이었고 대상은 power ON·TimerOFF·
    `constant`·`independent`·Flow `40`이었습니다. persistent service exactly `1`, restart `0`,
    availability online, retained command `0`, group armed `0`, unfinished journal `0`이었습니다.
  - running authority는 old generation을 그대로 사용하므로 command surface는 기존 첫 번째 Pro
    `1`뿐이고, 이 candidate가 running qualification input으로 투입되지는 않았습니다. 따라서 현재
    사람이 보는 HA range도 old generation의 `30..75`이며, D1l의 `30..100`은 새 generation을
    배포한 뒤에만 공개됩니다.
- 원복 검증 결과:
  - precondition write는 fresh Flow `41`을 recovery baseline으로 보존하고 목적값 `40`을 exact
    readback했습니다. 정상 완료 뒤에는 위에서 밝힌 대로 직전 기록값 `40`으로 복귀했으며, 원인을
    확인하지 못한 실행 직전 값 `41`로 최종 원복했다고 주장하지 않습니다.
  - qualification은 Flow `40`을 baseline으로 보존하고 `40 -> 39 -> 40`의 모든 phase를 independent
    exact fresh readback으로 확인했습니다. 최종 Flow는 exact `40`, power·Timer·mode·linkage는 실행
    전 qualifier precondition과 같았습니다.
  - recovery frame은 필요하지 않았습니다. 새 candidate·scratch 외에 old qualification source와
    running generation은 바꾸지 않았으므로 실패 시에도 old persistent authority가 복원 대상 없이
    그대로 남는 구조였습니다.
- **이 실행으로 새로 확정된 사실:** D1l의 app-declared-range scalar writer와 새 protocol schema는
  첫 번째 Pro에서 existing safety contract를 유지하며 `40 -> 39 -> 40` 세 physical write와 exact
  readback으로 새-schema `manual_flow` receipt pair를 만들었습니다. 새 candidate는 running
  authority와 격리됐고 old persistent service·surface `1`은 정상 복귀했습니다.
- 이 실행으로 확정하지 못한 것: Flow `100`의 장비 실수용 여부나 그 값의 live write, 두 번째 Pro의
  새-schema 재자격, 새 candidate merge·Compose generation·HA `30..100` surface, Bar
  `manual_flow`, 세 장비 `power`, group actuation, 장시간 운용, Q1·Q2·native ASYNC capability는
  증명하지 않습니다. app-declared capability `30..100`과 이번 attended 목적값 최대 `40`을 같은
  주장으로 취급하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: source range가 바뀌면 schema-bound receipt가
  stale해지는 현재 설계를 우회하지 않습니다. 이 기록을 먼저 독립 검토·commit한 뒤에만 두 번째
  Pro를 동일 새 schema로 재자격하고, 두 새 candidate만 fresh merge해 한 번의 새 generation을
  배포합니다. old-schema merge는 미배포 obsolete artifact로 보존합니다.
