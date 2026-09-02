# run: 2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9

- 목적(한 문장): 앱과 같은 `independent -> sync_slave -> async_slave` 순서를 단회 적용하되,
  Sync 직후 fresh pair에서 master·sync-slave 지정, `TimerON`, 장비별 temporary schedule image
  보존과 bounded active state를 확인한 경우에만
  ASYNC로 진행해 slave의 슬롯별 `Flow 35 -> 47`을 관측하고 원 상태를 exact restore한다.
- 이 실행이 답하려던 질문: "master가 `Sine/F50/Flow40 -> Constant/Flow35`로 바뀔 때,
  앱과 같은 role 진입을 거친 slave가 같은 Mode·경계를 따르면서 자기
  `Sine/F50/Flow35 -> Constant/Flow47`을 적용하는가?"
- 실행한 commit SHA: `ced43a97dcbcc85f98b3438d551fcc4f9d6bd3fd`
- 사용한 명령·주요 파라미터:
  - exact commit의 Linux/amd64 image
    `sha256:a9fcab3e0d90b2bf8d2760c737f8ccd0eb58922501cdee0514424a8e52192ec5`
  - image label revision과 fixed signature `(40, 35, 35, 47, 50)`, guarded Flow 상한 `47`,
    fresh monitor pause `10초`, acquisition authority `8초`, 연속 실패 상한 `3`을 실행 전에 대조
  - Linux exact tree 전체 suite `1780/1780 PASS`; source diff와 직접 테스트는 Claude가
    digest `d953ba7cc5282545c216f965c0f462e22d3ed6e62d0f71d7c4835aab1ab5efed`에 결속해
    `COMMIT_OK` 판정
  - 첫 전체 suite 시도에서 timing-sensitive test 한 건이 실패한 뒤 단독 재실행과 후속 전체
    suite가 통과했다. 최초 실패의 exact test id는 durable하게 보존하지 못했으므로 이 기록은
    `1780 PASS`를 그 최초 실패가 없었다는 주장으로 사용하지 않는다.
  - master safe manual `Constant/30/F20`, slave safe manual `Constant/35/F20`
  - master A=`Sine/40/F50`, B=`Constant/35/wire F0`
  - slave A=`Sine/35/F50`, B=`Constant/47/wire F0`
  - device-local 경계 `2026-09-02T17:53:00`, master=`master`, slave role sequence
    `sync_slave -> async_slave`, 계획 observation epoch `900초`
  - preflight는 control·schedule frame `0`으로 통과했다. repository maintainer 겸 on-site
    hardware approver는 두 Pro의 전원을 즉시 끌 수 있는 상태임을 확인하고, Home Assistant를
    재시작하거나 건드리지 않는 조건으로 실행을 승인했다.
- 종료 지점(stage / failure code):
  - temporary schedule write·read-back과 양쪽 `TimerON` arm을 완료했다.
  - master role write는 `08:45:11.284436Z -> 08:45:12.266417Z`, 이어진 fresh pair 검증은
    `08:45:12.266504Z -> 08:45:14.560152Z`에 완료됐다.
  - slave의 첫 role write인 `Linkage=sync_slave`는
    `08:45:14.563975Z -> 08:45:15.533042Z`에 한 번 수행됐다. fresh pair는
    `master=master`, `slave=sync_slave`, 양쪽 `TimerON`, temporary schedule과 active Flow
    상한을 확인했지만, slave의 inactive manual `Mode`·`Frequency`가 saved safe manual과
    달라 `slave_pair_slave_state / mode,frequency`로 `08:45:16.374161Z`에 중단됐다.
  - `sync_verified`가 fsync되지 않았으므로 `async_intent`와 두 번째 slave adapter write는
    생성되지 않았다. **`Linkage=async_slave` write는 0회**이고 observation epoch도 시작하지
    않았다. 이 주장은 outbound packet capture가 아니라 durable event 순서와 구현의 canonical
    progress 계약에 근거한 **(b)** 증거다.
  - ordered rollback은 `role_disarmed -> field_restored -> outer_restored`까지 진행돼 terminal
    `experiment_failed_restored`로 끝났다. durable verdict는 `verified_sample_count=0`,
    `schedule_transition_verified=null`이므로 Q2-slotflow 판정은 계속 **`UNKNOWN`**이다.
- 장비가 실제로 돌려준 값:
  - Sync 직후 private raw sink에는 fresh pair의 exact state frame 두 개가 보존됐다.
    - master action `0x03`: `ON / TimerON / master`, manual `Constant/30/F20`, active
      `Sine/AutoFlow40/AutoFreq50`, temporary schedule digest
      `433f0881e6a77899c6740a2137a09ef12ac0e67d890314efc7ae2a1d8097fcfa`
    - slave action `0x04`: `ON / TimerON / sync_slave`, manual `Sine/35/F50`, active
      `Sine/AutoFlow35/AutoFreq50`, temporary schedule digest
      `7b78a6a514601e99ff2cbd6fe6b937b0dae8a59175c5b14e01204d1e10360373`
  - 두 frame의 device-local `NowTime`은 모두 `17:45:00`으로 planned boundary 전이다.
    slave frame은 action `0x04`이므로 explicit reply나 요청 상관 ACK로 부르지 않는다.
  - slave는 실제로 `sync_slave`, `TimerON`, master와 다른 temporary schedule digest,
    `Sine/35/F50`과 active Flow `35`를 보고했다. 따라서 **Sync 역할 지정, `TimerON`, 장비별
    schedule image 보존과 bounded active state는 확인됐다.** 다만 master와 slave의 A
    mode·frequency·boundary가 같고 Flow만 다르므로 이 한 pair는 master schedule 추종과 slave
    로컬 schedule 실행을 구분하지 않으며, `A-slot 동작`을 확정하지 않는다.
  - field stage의 saved safe manual은 slave `Constant/35/F20`이었지만 Sync 직후 raw의 manual과
    Auto tuple은 모두 `Sine/35/F50`이었다. 이 차이가 exact snapshot-control assertion의
    `mode,frequency` drift를 만들었다. role 진입이 active slot을 manual DP에 mirror한 것인지
    다른 firmware side effect인지는 이 실행만으로 인과를 확정하지 않는다.
  - 증거 등급:
    - 위 exact state frame 두 개와 post-restore collector의 exact reply frame, 그 frame에서
      재디코딩한 state·control·schedule: **(a) preserved raw artifact**
    - terminal intent, stage event, role failure, rollback evidence, collector plan·series·source
      attestation: **(b) preserved structured/durable daemon artifact**
    - 현장 전원 차단 가능 확인, Home Assistant 무변경, terminal intent archive와 recovery runtime
      stop/start: **(c) reconstructed operator observation**
  - opaque artifact id와 digest:
    - Sync fresh pair raw `JFR-8e9a7f4a9381`:
      `47abc01f42c8af5f2fefcd60f449604699c7fe0bc03c3e883ddb275c6daa22bd`
    - terminal intent logical id `native-linkage-intent`:
      `0d19ab7ccbb0455f4f7963c0cdb0d5cc2ec4bbbbe9e701690891af5715e465d2`
    - pre-write baseline plan `JFP-9d3f967511ac2d8e29e706cc6875900e`:
      `2d4f027e829a0f907f10c4d17b289e28a1b1badae46ffd2339a38f89fe3c9e00`
    - pre-write baseline series `JFS-4756cd1b9b395436ae87f2713b53ffda`:
      `aff0ab9824837ecf3204b63d6872641d6f9b0b06c2787fb8a4d0cf787aa829c4`
    - post-restore series 1 `JFS-0131d62549e645b629afa5ee05c4ca05`:
      `06b4973bf4312cfaa862e8a6de4925909fe5c1f1b6c0f4480fc389250f46fb08`
    - post-restore series 2 `JFS-3155c7916e31f6ce6ffbd91565fb670c`:
      `01ee6d263bdde257d882be17d4d2bfddaaa5a88248093c1d34e7643fd5524df2`
    - collector source attestation:
      `7df3c4419a6e5bcaa9e2c1b538baff8b286aec9c74480bcfcba27d6e9641a3f6`
  - UTC span (시작 / 종료):
    - terminal intent: `2026-09-02T08:43:51.099295Z` /
      `2026-09-02T08:46:29.118617Z`
    - Sync fresh pair raw: `2026-09-02T08:45:15.878130Z` /
      `2026-09-02T08:45:16.366750Z`
    - pre-write baseline: `2026-09-02T06:33:36.497418Z` /
      `2026-09-02T06:34:28.584438Z`
    - post-restore verify 1: `2026-09-02T08:48:03.067613Z` /
      `2026-09-02T08:48:55.092813Z`
    - post-restore verify 2: `2026-09-02T08:49:08.733568Z` /
      `2026-09-02T08:50:00.739764Z`
  - SHA-256 identity binding(세 collector 공통):
    - `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
    - `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
- 원복 검증 결과(어떤 수준의 확인이었는지 명시):
  - terminal intent는 rollback failure·recovery reason 없이 `outer_restored`를 기록했다: **(b)**.
    `control_disarm_unverified` 진단이 한 번 있었지만 뒤이어 `role_disarmed`, `field_restored`,
    `outer_restored`가 모두 완료됐다.
  - writer 종료 뒤 서로 다른 두 source-attested fresh collector를 실행했다. 두 회 모두
    `2/2 pair`, `4/4 action 0x03`, read failure·rejected `0`, offline verify PASS였고 모든
    ordinal에서 pre-write baseline과 다음 값이 byte-exact하게 일치했다: **(a)**.
    - 역할 A: `ON / TimerON / independent / constant / Flow30 / Frequency32`, schedule digest
      `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`
    - 역할 B: `ON / TimerON / independent / random / Flow89 / Frequency34`, schedule digest
      `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`
  - terminal intent는 동일 safety volume의 loader 비대상 archive로 이동했고 이동 전후 digest,
    mode `0600`, uid `100`, gid `101`, link count `1`이 같았다. 그 뒤 locked recovery runtime은
    정상 재기동됐다. Home Assistant는 실행 전후 재시작·설정 변경·조작하지 않았다: **(c)**.
  - 첫 post-restore collector 준비 명령 한 번은 host에 해당 mount path가 없어 network call 전에
    종료됐다. mounted volume 안에서 새 private root를 만든 뒤 성공한 두 collector만 위
    device-contact 검증으로 센다.
- **이 실행으로 새로 확정된 사실:**
  - 앱과 같은 첫 단계인 `Linkage=sync_slave`를 한 번 적용한 뒤 fresh raw에서 두 장비가
    `master`·`sync_slave`로 지정됐고, 양쪽 `TimerON`, 서로 다른 exact temporary schedule image와
    bounded active Flow `40`·`35`가 유지됐다. 앱 sequence의 Sync 지정, TimerON, 장비별 stored
    schedule image 보존은 실제 장비에서 성립한다. runtime schedule 소유권은 이 pair로 판정하지
    않는다.
  - Sync 진입 뒤 slave의 inactive manual `Mode/Frequency`가 saved safe manual
    `Constant/F20`이 아니라 active A와 같은 `Sine/F50`으로 보고됐다. 기존 exact snapshot-control
    gate는 이를 `mode,frequency` drift로 거부했고, 그 결과 ASYNC write를 보내지 않는 계약이
    실제로 작동했다.
  - previous run의 direct `0 -> 3` 명령 오류 가능성을 교정했지만, 이번에는 그 다음 단계 전에
    gate가 멈췄다. 따라서 이전 run의 결과를 capability `FAIL`로 바꿀 근거도, 이번 run으로
    per-slot Flow를 답할 근거도 없다.
  - automatic ordered rollback과 서로 다른 두 fresh collector에서 원 controls와 두 432-byte
    schedule image의 exact restore가 다시 확인됐다.
- 이 실행으로 확정하지 못한 것:
  - `Linkage=async_slave`가 전송되지 않았으므로 slave가 경계에서 자기 B slot
    `Constant/Flow47`을 적용하는지는 계속 `UNKNOWN`이다.
  - Sync raw 한 pair는 master와 slave의 A mode·frequency가 같아 Sync 추종 동작과 slave 자기
    schedule 실행을 구분하지 않는다. 확인한 범위는 역할 지정, `TimerON`, 장비별 stored schedule
    image 보존과 bounded active 상태다.
  - slave manual tuple 변화가 firmware의 active-slot mirror인지, role side effect인지,
    일시적 report ordering인지는 이 실행만으로 확정하지 않는다.
  - protocol-reported 값 밖의 물리 유량·파형·위상과 장기간 반복 신뢰도는 측정하지 않았다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것:
  - 이 실행은 같은 Q2-slotflow 질문에 대한 2026-09-02의 두 번째 live operation이다. §7에 따라
    오늘 세 번째 실기나 즉시 하네스 수정을 하지 않고 질문을 다시 park한다.
  - 대안 1: native 질문을 park한 채 원래 제품 경로인 software-independent actuator와 그룹
    런타임을 진행한다. Q2-slotflow 답은 제품 구현의 선행조건이 아니므로 영향은 없다.
  - 대안 2: native 최적화의 가치가 여전히 있다고 maintainer가 다시 판단할 때만, 먼저
    master/slave staged mode·frequency 또는 boundary를 다르게 한 최소 Sync 관측으로 runtime
    schedule 소유권을 구분한다. 그 결과 위에서만 이번 raw를 근거로 Sync 중간 gate의 manual Flow
    exact/bounded 검사와 core 조건(identity·online/no-error·SwitchON·roles·TimerON·exact
    schedule·active cap)은 유지하되 inactive manual `Mode/Frequency` drift만 진단으로 보존하는
    ASYNC 계획·새 승인·새 단회 해제를 만든다.
  - 대안 3: 새 write 전에 preserved raw와 app dataflow만으로 manual/Auto DP mirror 가설을
    오프라인 분석한다. 이 분석은 다음 gate 범위를 정할 근거일 뿐 Q2-slotflow 자체의 PASS/FAIL을
    만들지는 않는다.
  - `9a51786`의 단회 해제는 이 terminal restore, 두 fresh collector와 본 append-only 기록으로
    소진된다. 자동 재시도하지 않고 §1 동결을 다시 적용한다.

## 실행 전·후 terminal intent archive

실행 전에는 직전 `a00f4b1` 실행의 terminal intent가 fixed signature 변경 뒤 현행 loader와 맞지
않아 loader 비대상 archive로 이동됐다. 대상은 terminal이고 journal 3종·emergency latch가
없었으며, 이동 전후 digest
`a72e403bab9321fcedc1a23b24df9705eb0657b12f12a427b840d031e01819c3`, mode `0600`, uid `100`,
gid `101`, link count `1`이 같았다. 실행 뒤 이번 terminal intent도 같은 규칙으로 archive했고
digest는 위 artifact 절의 `0d19ab7c…`와 일치했다. 두 이동 모두 volume을 잡은 process가 없음을
먼저 확인하고 파일과 directory를 fsync했다. 하네스가 만든 raw가 아닌
**(c) reconstructed operator observation**이다.

이번 실행도 persisted terminal intent의 유효성이 fixed signature 상수에 결합된 운영 위험을
재확인했다. 비-terminal intent가 하나라도 있으면 fixed signature를 바꾸지 않으며, terminal
archive는 exact restore와 별도 fresh raw 검증을 모두 마친 뒤에만 수행한다.
