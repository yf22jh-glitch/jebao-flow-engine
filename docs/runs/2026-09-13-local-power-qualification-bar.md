# run: 2026-09-13-local-power-qualification-bar

- 목적(한 문장): Local Wavemaker Bar 한 대에서 safer-OFF `power` qualification의 same-value
  ON → OFF → planned ON 세 physical write와 exact readback을 수행하고, 독립 receipt pair를 running
  authority와 분리된 owner-only candidate에 설치한 뒤 사용자가 앱으로 원 운전 상태를 복원했는지
  확인했습니다.
- 이 실행이 답하려던 질문: 두 Pro에서 실기한 D1o power producer가 Bar의 product-specific status와
  control payload에서도 TimerOFF admission, journal-first ON → OFF → ON, OFF 구간의 필수 비전원
  필드 유지, planned restore와 terminal receipt 계약을 만족하는가.
- 사용자 승인 범위:
  - repository maintainer 겸 on-site hardware approver는 수조를 직접 감시하고 즉시 물리 전원 차단·
    앱 ON 복원 수단을 쥔 상태에서 이 Bar 한 대의 `power` qualification을 승인했습니다. 사용자가
    수행하는 앱 precondition·원복과 agent의 정확히 세 physical write만 포함하며, 다른 장비 write,
    candidate merge, 새 generation 배포, group actuation, native ASYNC 하네스, Home Assistant Core
    update·restart·설정 변경은 포함하지 않았습니다.
  - operation은 same-value ON → OFF → planned ON을 전송합니다. OFF 뒤 어느 nonterminal failure든,
    planned ON이 이미 성공한 뒤 terminal save가 실패한 경우까지, recovery는 무인 ON을 보내지 않고
    OFF로 수렴합니다. `ROLLED_BACK`·recovery write `0`이어도 실제 장비는 OFF일 수 있으므로 durable
    phase보다 실제 power를 먼저 확인하고 필요하면 사용자가 앱이나 물리 수단으로 ON을 복원하기로
    했습니다.
  - 사용자는 원 운전값 Flow `91`을 qualification admission 경계인 `45`로 낮추고 Mode를
    `classic`에서 `constant`로 바꿨습니다. 이는 46%p 유량 변화와 파형 변경이며, 사용자가 수조를
    보면서 직접 결정·수행했습니다. 단계별 앱 write와 실제 frame 수는 관측하지 않았습니다.
    baseline `45`는 `_SAFE_FIRST_WRITE_MAXIMUM = 45`의 정확한 경계값이고 장비 capability
    `30..100`이나 영구 입력 상한이 아닙니다. agent의 power operation은 Flow 값을 쓰지 않았습니다.
- 실행한 source commit SHA:
  - Bar family profile source는 `d687739c7045859e838e6122710f90921af36606`, safer-OFF 정책은
    `135bb2be17c628bdf4905fdfa814e851867286d9`, outer fault 결속은
    `ab495aeac103c88870f29ece8735213ba91a146f`, producer source는
    `c242635f1177783b75c3d06c531f48735a0d2dda`, 실행 image에 포함된 최종 recovery-mapping 결속은
    `045f086b82c6c9a25a8e22cd892be2f9cf7fdee9`입니다.
  - exact x86_64 one-shot image id는
    `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`, 설치 Python tree는
    files `137`, SHA-256
    `db56fca35b02628ea3216f2d7430da569eda99fbafe94935aa2f6932b38b57a5`입니다.
  - active Bar template SHA-256은
    `e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`입니다.
- 실행 전 조건:
  - persistent service exactly `1`, 장비 authenticated fresh `6/6`, unfinished journal `0`, legacy
    authority idle, retained command payload `0`을 확인했습니다. current running authority는
    qualification binding `2`·receipt `6`·qualified pair `3`·ignored `0`, direct `manual_flow`
    control `3`, `power` control `0`, group command·resume `0`이었습니다.
  - journal inventory는 operations `12`·blobs `35`·files `47`, 이 run에서 정의한 sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256은
    `9b0bc7ee911de1edcffb9dedd22cd27987d0b7e082dbc04e68a6f9338d6350ab`였습니다. fixed
    qualification receipt root는 files `16`, 같은 방식의 tree SHA-256은
    `a64d35ba1416a2b0c05211c5b0316f7a36eef795da87bfd13f7b2bd5b41e69fa`였습니다. 이
    canonicalization은 이 run의 전후 비교에만 사용합니다.
  - 사용자 앱 변경 전 마지막 authenticated 상태는 Bar가 power ON·TimerOFF·Mode `classic`·
    Linkage `independent`·Flow `91`·Frequency `80`·PulseTide `false`였습니다. 사용자가 앱에서
    precondition한 뒤 세 public anchor를 함께 fresh read했고, Bar는 power ON·TimerOFF·Mode
    `constant`·Linkage `independent`·Flow `45`·Frequency `80`·PulseTide `false`, 첫 번째 Pro는
    power ON·TimerOFF·Mode `constant`·Linkage `independent`·Flow `40`, 두 번째 Pro는 power
    ON·TimerON·Mode `constant`·Linkage `independent`·latent manual Flow `40`이었습니다.
  - precondition Bar status는 `401` bytes, SHA-256
    `afe8583cea4fc73165f2a2dd840679cbd1d9baa3af9f3f3f65632c92c7cc4c7c`였습니다. exact current
    manifest resolution은 Bar target을 정확히 하나로만 선택했습니다.
  - owner-only observer input은 `4,646` bytes, SHA-256
    `f4dec1353a3578a43692e91416835dddb1829b2737f92d6f387c93702c6eeb71`, mode `0600`, link count
    `1`이었고 current epoch·target binding·template·config limits와 exact 일치했습니다. private
    절대경로·logical member·physical key는 기록하지 않습니다.
- 사용한 명령·주요 파라미터:
  - persistent control을 stop-first로 정지한 뒤 opaque operation id
    `LCPQ-PWR-BAR-D1O-045F086-20260913T055629Z`, current Bar template, 신규 no-clobber candidate,
    explicit write opt-in을 사용해 `jebao-flow-local-power-qualify`를 실행했습니다.
  - producer는 send-free current-state admission 뒤 same-value ON, OFF, planned ON을 각각 새
    writer로 실행했습니다. 각 phase는 durable journal 저장 뒤 physical frame 하나와 independent
    exact readback을 수행했고 forward retry는 없었습니다.
  - qualifier 종료 뒤 독립 read-only one-shot으로 장비 상태를 확인하고 기존 persistent container만
    다시 시작했습니다. candidate merge, config write-enable 변경, 새 Compose generation과 `power`
    command surface 배포는 실행하지 않았습니다.
  - recovery는 필요하지 않았습니다. 실패했다면 install manifest를 재유도하는 일반 local-control
    CLI가 아니라 config-only `jebao-flow-local-qualify recover`만 사용하고, 결과 문구보다 실제
    power를 먼저 확인하는 절차였습니다.
  - 사용자는 qualification 종료 뒤 앱으로 Mode `classic`·Flow `91`을 복원하고 Frequency `80`·
    PulseTide `false`·TimerOFF·Linkage `independent`·power ON을 유지했습니다. agent는 이 복원에
    frame을 보내지 않았고 세 public anchor와 전체 장비를 다시 authenticated fresh read했습니다.
- 실행 중 정정:
  - offline preflight wrapper 두 건은 read-only container의 임시 경로를 writable tmpfs로 제공하지
    않아 current-input 검사 전에 fail-closed됐습니다. corrected wrapper는 tmpfs를 명시하고 같은
    inputs를 재검증했습니다. 두 초안은 persistent service stop·network·device·broker 접촉과 journal·
    receipt mutation 전에 끝났고 physical write는 `0`이었습니다.
  - qualifier 뒤 첫 사용자 복원 시도는 두 번의 authenticated fresh read에서 Bar가 여전히
    `constant`·Flow `45`인 동일 `401`-byte status로 남아 있음을 확인했습니다. 이를 복원 성공으로
    기록하지 않고 사용자에게 앱 확인 동작을 다시 요청했습니다. 두 번째 시도 뒤 fresh read에서
    `classic`·Flow `91`·Frequency `80`·PulseTide `false`가 확인됐습니다. 앱 내부 frame 수와 첫
    시도가 장비에 명령을 보냈는지는 관측하지 않았습니다.
  - 첫 broker audit은 Home Assistant 전체 discovery를 세어 이 service 소유 범위를 넘었습니다.
    corrected audit는 current private topic prefix를 payload에서 유도해 소유 discovery만 집계했고,
    둘 다 subscribe-only로 device command와 durable mutation은 `0`이었습니다.
- 종료 지점(stage / failure code): `bar_d1o_power_candidate_verified` PASS. qualification transaction은
  `VERIFIED` revision `10`, action `3/3` verified, physical write `3`, receipt `2`로 끝났고 recovery
  frame은 필요하지 않았습니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal·referenced blob·candidate binding·receipt 두 장과 fresh process로
    재독한 inventory는 **(b) preserved structured/durable daemon artifact**입니다. 사용자 앱 조작,
    Docker·CLI·broker observation과 authenticated fresh device read는 **(c) reconstructed operator
    observation**입니다. raw wire frame을 별도 보존하지 않아 (a)는 주장하지 않습니다.
  - opaque artifact id: `LCPQ-PWR-BAR-D1O-045F086-20260913T055629Z`입니다. candidate는 fixed receipt
    root 밖의 별도 owner-only no-clobber source이며 private 경로는 기록하지 않습니다.
  - 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다. UTC span: persistent service stop은
    `2026-09-13T05:57:17.320615Z`에 요청됐고 container는
    `2026-09-13T05:57:17.442029208Z`에 정지했습니다. journal은
    `2026-09-13T05:57:23.574653Z`에 생성되어
    `2026-09-13T05:57:35.664810Z`에 terminal update됐습니다. persistent service의 새 process는
    `2026-09-13T05:58:39.144486250Z`에 시작됐습니다. 중간 action revision별 timestamp는 terminal
    artifact에 보존되지 않아 실제 qualification OFF 체류는 정확히 유도하지 못합니다. 전체 journal
    span `12.1`초는 OFF 체류의 상한이고 service process 부재는 약 `81.7`초였습니다. retained
    availability의 실제 offline 구간은 별도로 계측하지 않아 process 부재보다 조금 길 수 있습니다.
    첫 복원 미반영 read는 `2026-09-13T06:05:52.934426Z`에 끝났고 같은 status 재확인은
    `2026-09-13T06:06:30.623904Z`, 성공한 복원 final read는
    `2026-09-13T06:13:31.290429Z`에 끝났습니다. durable·broker 재독은
    `2026-09-13T06:16:42.371710526Z` 이전에 끝났습니다.
    수조가 `constant`·Flow `45`로 운전된 정확한 구간은 앱 precondition 시각을 보존하지 않아
    유도할 수 없습니다. stop 요청부터 복원 확인까지 `16.2`분은 그 구간의 하한이고, 첫 복원
    미반영 read부터 성공한 final read까지의 `7.6`분도 그 구간에 포함됩니다.
  - SHA-256 identity binding:
    `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 이전 Bar
    manual-flow 기록과 같은 물리 개체입니다.
  - artifact digest (SHA-256): terminal journal
    `3bc8346323e1cb5287caeb09bf2e8f8858d8bcc761d3cb2256ca6e18e4f98a66`, candidate qualification
    `0ad7f62b3428d64a15bafd6807413627b2ca0251341f04484a9bf1d1474c7189`입니다.
  - terminal journal의 세 action은 `restore_on_recovery=[true,false,true]`, expected power
    `[ON,OFF,ON]`, action-local baseline/recovery target `[OFF,ON,OFF]`이고 diagnostic은 모두
    `null`이었습니다. Bar ON payload SHA-256은
    `91ecba757a0a3cdbe877003455b659bc15e28a7b62591954f64d6e5237808cc3`, OFF payload SHA-256은
    `5e4d609823276af7878e8b4d4c98f6ef7f32724d0042610e8bccf5e6842d05d9`이고 각각 `409` bytes입니다.
    ON·OFF exact readback SHA-256은 각각
    `2c08ccafb6243e28fda3499daa8385b139474d0b45f63ee57911b11ab3ba87e4` (`17` bytes),
    `7ca2832ca507e84ea79f83bba9098c6299a87842ee6fa526a9e0bd61b354d654` (`18` bytes)입니다.
    operation 시작의 full ON baseline status SHA-256은
    `afe8583cea4fc73165f2a2dd840679cbd1d9baa3af9f3f3f65632c92c7cc4c7c` (`401` bytes)이며,
    action-local `baseline_readback=OFF`를 시작 상태라고 해석하지 않습니다.
  - candidate는 binding `1`, function `power`, receipt `2`, qualified physical identity `1`, `553`
    bytes, mode `0600`, link count `1`이고 current owner와 일치했습니다. receipt digest는
    `4750224b407efdf4b6c3c8d97b1ab2b4e32d153343282a7bef1c28b1ed2dfe80`,
    `a56aa79ede862a87368b56e2c78b1c47f2518abec2d15f76a089877b09e9c8f3`이며 OFF와 planned ON
    action에 대한 것입니다. same-value ON action에는 receipt를 발급하지 않았습니다.
  - 세 action의 control response body는 모두 empty였고 ACK digest는 SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다. empty ACK은
    runtime이 request와 sequence가 결속된 control response를 받은 뒤 4-byte sequence를 제거한
    body가 비어 있었다는 뜻입니다. sequence 판정은 runtime에서만 수행되고 보존되지 않으므로
    동일한 `acknowledgement_sha256` 자체는 이 operation을 판별하는 durable evidence가 아닙니다.
    보존 artifact에서 판별 근거는 action별 exact readback과 terminal journal입니다.
  - 종료 journal inventory는 operations `13`·blobs `39`·files `52`, tree SHA-256은
    `0644759bcaa36161a4ad1c0a0874b2c0750e46752b290b177921e2a0aaddce4d`입니다. 이 operation이
    참조하는 고유 blob은 empty ACK을 포함해 `7`종입니다. 직전 inventory 대비 operation `+1`·blob
    `+4`이고, 신규 blob은 intent (`1,311` bytes,
    `add570468533d9a68b0ffecfd4079d98b2ee6b61f2262868f9204c90ec440dcc`), shared full baseline
    status (`401` bytes, 위 digest), Bar ON·OFF payload 두 개(각 `409` bytes, 위 digest)입니다.
    product-independent ON·OFF readback과 empty ACK은 앞선 power operation의 content-addressed
    blob을 재사용해 순증 `0`입니다.
  - fixed qualification receipt root는 files `18`, tree SHA-256
    `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`로 `+2`이며, 증가는 위
    receipt 두 장입니다. candidate qualification 파일은 이 count 밖의 별도 no-clobber source입니다.
  - qualifier 직후 independent fresh read는 Bar power ON·TimerOFF·Mode `constant`·Linkage
    `independent`·Flow `45`·Frequency `80`·PulseTide `false`와 전체 장비 `6/6`을 반환했습니다.
    사용자 복원 뒤 final authenticated read는 Bar power ON·TimerOFF·Mode `classic`·Linkage
    `independent`·Flow `91`·Frequency `80`·PulseTide `false`, 첫 번째 Pro power ON·TimerOFF·
    `constant`·`independent`·Flow `40`, 두 번째 Pro power ON·TimerON·`constant`·`independent`·
    latent manual Flow `40`, 전체 장비 `6/6`을 반환했습니다. final Bar status는 `401` bytes,
    SHA-256 `41acb0a1f18c2ae432f1f04a21c76d33330177add92f4cd36ded5216f2b98100`입니다. 동적·미검사
    필드를 포함한 pre/post raw full-state equality는 주장하지 않습니다.
  - persistent service exactly `1`, exact reviewed image, host network, `unless-stopped`, 의도적
    stop/start 뒤 새 restart counter `0`, runtime log line·problem `0`이었습니다. runtime manifest
    generation은 `9e6f366a049f0b26a69aebf233403070`, raw manifest SHA-256은
    `87f4c4f928c93dab489456919e3f2547c9ae17de576b5cb6083fc439b3e9db21`입니다. 이 runtime
    manifest generation을 Compose generation `d730650c4d105e8af7bb74fe720ffa46`과 같은 값으로
    해석하지 않습니다. restart `0`은 의도적 stop/start 뒤 새 counter라 operation 간 연속성
    증거로 사용하지 않습니다.
  - running qualification은 `1,231` bytes, SHA-256
    `abaa4ad15444d2f9db025cafbfb9811dc9ddb954d45afc0c7da395f806f12803`으로 불변이며 binding
    `2`·receipt `6`·qualified pair `3`·ignored `0`, discovery `86`, direct `manual_flow` control `3`,
    `power` control `0`, group command·resume `0`입니다. 소유 discovery의 sorted
    `(topic, SHA-256(payload))` digest는
    `0c40cbc208a075a1e0fb2fb5544d5e9758f125a3df7a48460c0d071bedffa688`이고, 그 payload가 참조하는
    unique availability topic은 global `1`·device `6`·group diagnostic `1`의 합계 `8/8` online이었습니다.
    이 `8/8`은 이전 기록의 더 좁은 availability 집계와 직접 비교하지 않습니다. 8초 subscribe-only
    audit에서 command topic expected `3`의 message와 retained non-empty payload는 모두 `0`이었습니다.
  - startup local-control device write는 `0`입니다. 모든 local-control frame보다 journal-first record가
    선행하는 현재 coordinator·producer 경로에 한정해, 종료 operations 증분이 이 qualifier 한 건으로
    전부 설명되고 restart·복원 뒤 journal tree가 위 값으로 불변인 것으로 판정했습니다. 사용자 앱
    precondition·복원은 이 경로와 write count에 포함하지 않습니다.
- 원복 검증 결과:
  - agent qualification은 시작 ON 상태에서 same-value ON → OFF → planned ON 세 phase의
    independent exact readback을 확인했고 recovery frame 없이 terminal `VERIFIED`로 끝났습니다.
    action 1의 OFF 직후와 action 2의 planned ON 직전 fresh 판정은 모두 power OFF 동안 Flow `45`·
    Mode `constant`·Linkage `independent`·TimerOFF가 유지됨을 검사하고 통과했습니다. 이는 Bar의
    현재 product·firmware와 이 네 decoded field에만 한정합니다. Frequency·PulseTide는 producer가
    OFF 구간에 검사하지 않았으므로 그 구간의 불변을 주장하지 않습니다.
  - 사용자의 두 번째 복원 시도 뒤 동일한 일곱 decoded field 집합으로 power ON·TimerOFF·Mode
    `classic`·Linkage `independent`·Flow `91`·Frequency `80`·PulseTide `false`를 확인했습니다.
    이는 decoded 운전 상태 복귀이며 raw full-state byte equality나 앱 내부 frame 수를 주장하지
    않습니다.
  - candidate와 새 receipt·journal operation 외에 current qualification source·running authority는
    바꾸지 않았습니다. 따라서 `power` command surface와 group command·resume는 계속 `0`이고 Bar
    candidate는 running authority와 격리돼 있습니다.
- **이 실행으로 새로 확정된 사실:** 이 Bar의 현재 product·firmware에서도 power OFF인 두 fresh
  판정 지점에 Flow `45`·Mode `constant`·Linkage `independent`·TimerOFF가 유지됐습니다. D1o
  safer-OFF producer는 Bar-specific payload로 ON → OFF → ON 세 physical write·exact readback·terminal
  journal을 마치고 독립 `power` receipt pair를 만들었으며, final ON 뒤 사용자의 앱 조작으로 일곱
  decoded 운전 필드가 원 상태로 복귀했습니다.
- 이 실행으로 확정하지 못한 것: raw wire frame, OFF 구간의 Frequency·PulseTide와 raw full-state
  equality, 사용자 앱 조작의 frame 수·step 수, failure 시 actual recovery, 세 power candidate의
  merge·persistent direct surface, 마지막 group member가 dual-qualified될 때의 group control·resume
  delta와 actual actuation, TimerON인 두 번째 Pro에서 power 전환 뒤 schedule ownership 재개,
  장시간 운용, future HA update·container recreate·host reboot 복귀와 Q1·Q2는 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 이 기록을 독립 검토·commit한 뒤 current
  qualification과 세 power candidate를 deterministic no-clobber intermediate를 거쳐 하나의 fresh
  immutable set으로 합칩니다. 배포 전에 direct `power` control과 group control·member resume의
  정확한 예상 delta, 기존 세 manual-flow number의 byte 불변, ignored category별 `0`, old-only
  discovery tombstone을 offline projection으로 선언합니다. TimerON인 두 번째 Pro에는 runtime power
  command가 별도 Timer gate 없이 전달되고 schedule ownership 재개가 미검증이라는 사실을 사용자에게
  공개한 뒤, device write 없이 stop-first 단일 generation으로만 배포합니다. 요약·중계 메시지의 축약
  digest를 identity 제약으로 쓰지 않고 source of truth에서 full 값을 직접 재유도합니다.
