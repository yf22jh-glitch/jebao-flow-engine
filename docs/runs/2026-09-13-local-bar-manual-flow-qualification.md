# run: 2026-09-13-local-bar-manual-flow-qualification

- 목적(한 문장): Local Wavemaker Bar 한 대를 product-scoped D1n profile로
  `45 -> 44 -> 45` exact scalar qualification하고, receipt pair를 running authority와 분리된
  owner-only candidate에 보존한 뒤 사용자가 앱으로 공통 관측 필드의 원 운전값을 복원했는지
  확인했습니다.
- 이 실행이 답하려던 질문: Bar firmware의 실제 current status가 D1n의 product-specific
  Timer·Mode·Linkage·Flow admission과 3-write journal-first 계약을 통과하고, lower-step exact
  readback과 planned restore를 마쳐 `manual_flow` receipt pair를 만들 수 있는가.
- 사용자 승인 범위:
  - repository maintainer 겸 on-site hardware approver는 현장 감시와 즉시 중단 수단을 갖춘
    상태에서 이 Bar 한 대의 `manual_flow` qualification을 승인했습니다. 사용자가 수행하는 앱
    precondition·복원과 agent의 정확히 세 physical write만 포함하며, `power`, group actuation,
    native ASYNC 하네스, Home Assistant Core update·restart·설정 변경은 포함하지 않았습니다.
  - Flow `50`은 입력 상한이 아니라 운전 중 수조에 급격한 유량 변화를 주지 않기 위한 attended
    operation 가이드입니다. 사용자는 원래 Flow `91`을 앱에서 `45`로 낮추고 Mode를
    `classic`에서 `constant`로 바꿨습니다. 단계별 앱 write 수와 실제 frame 수는 관측하지
    않았습니다. agent 목적값은 `45`·`44`·`45`, 상승 step `1%p`, 최대값 `45`였습니다.
    baseline `45`는 `_SAFE_FIRST_WRITE_MAXIMUM`의 정확한 경계값이며 여유를 둔 통과로 주장하지
    않습니다.
- 실행한 source commit SHA:
  - Bar family profile source는
    `d687739c7045859e838e6122710f90921af36606`, 실행 image에 포함된 최종 reviewed source는
    `045f086b82c6c9a25a8e22cd892be2f9cf7fdee9`입니다.
  - exact x86_64 one-shot image id는
    `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`, 설치 Python tree는
    files `137`, SHA-256
    `db56fca35b02628ea3216f2d7430da569eda99fbafe94935aa2f6932b38b57a5`입니다.
  - current Bar template SHA-256은
    `e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`입니다.
- 실행 전 조건:
  - persistent service exactly `1`, restart `0`, 장비 authenticated fresh `6/6`, firmware provenance
    `6/6`, unfinished journal `0`, legacy authority idle, retained command payload `0`을 확인했습니다.
    current running authority는 qualification binding `1`·receipt `4`, direct control `2`, Bar command
    `0`, group command·resume `0`이었습니다.
  - journal inventory는 operations `9`·blobs `21`·files `30`, 이 run에서 정의한 sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256은
    `88df6b2001afbf3661c5f6e8416269f1ab38080fda2d165ae6c7c6878e2bcd5a`였습니다. fixed
    qualification receipt root는 files `10`, 같은 방식의 tree SHA-256은
    `50614df6032efb95e83bebb96411443fca741a7fa82ab209a9338449eb7b56e5`였습니다. 이
    canonicalization은 이 run의 전후 비교에만 사용합니다.
  - 사용자 앱 변경 전 authenticated fresh read는 대상이 power ON·TimerOFF·Mode `classic`·
    Linkage `independent`·Flow `91`임을 확인했습니다. 사용자 precondition 뒤 새 authenticated
    read는 power ON·TimerOFF·Mode `constant`·Linkage `independent`·Flow `45`, 장비 `6/6`을
    확인했습니다. exact current manifest resolution은 Bar target을 하나로만 선택했습니다.
  - owner-only observer input은 `4,646` bytes, SHA-256
    `f4dec1353a3578a43692e91416835dddb1829b2737f92d6f387c93702c6eeb71`, mode `0600`, link count
    `1`이었고 current epoch·Bar binding·template·config limits `30..100`과 exact 일치했습니다.
    private 절대경로·logical member·physical key는 기록하지 않습니다.
- 사용한 명령·주요 파라미터:
  - persistent service를 stop-first로 정지하고 opaque operation id
    `LCQ-BAR-D1N-045F086-20260912T163617Z`, downward step `1`, current Bar template, 신규
    no-clobber candidate를 사용해 `jebao-flow-local-qualify`를 실행했습니다.
  - producer는 send-free product-scoped probes 뒤 same-value `45`, lower-step `44`, planned restore
    `45`를 각각 새 writer로 실행했습니다. 각 phase는 durable journal 저장 뒤 한 physical frame과
    independent exact readback을 수행했고 forward retry는 없었습니다.
  - qualifier 종료 뒤 기존 persistent generation만 다시 시작했습니다. candidate merge, config
    write-enable flip, 새 Compose generation과 Bar command surface 배포는 실행하지 않았습니다.
  - 사용자는 최종 latent Flow `45`에서 앱으로 Mode `classic`·Flow `91`을 복원했습니다. agent는
    이 복원에 frame을 보내지 않고 authenticated fresh read만 수행했습니다.
- 실행 중 정정:
  - 첫 baseline wrapper는 hardware-safety volume identity 활성화 전에 중단됐고, observer input
    준비 초안은 read-only root에 임시 디렉터리가 없어 source read 전에 중단됐습니다.
  - current control config를 observer input으로 재사용한 offline epoch 검사는 qualifier의 observer
    admission에서 중단됐고, 별도 owner-only observer input을 no-clobber로 준비했습니다.
  - disk-free-space 초안은 container가 아닌 host path를 확인해 판정에 쓰지 않았고, fresh-read
    초안 하나는 Python syntax error로 discovery 전에 끝났습니다. read lease 초안 하나는
    read-only로 mount한 lock volume의 `O_RDWR` open에서 discovery 전에 끝났습니다.
  - 위 초안은 모두 device command·journal operation·receipt·candidate mutation `0`이었고, 수정한
    invocation만 아래 결과에 사용했습니다.
- 종료 지점(stage / failure code): `bar_d1n_manual_flow_candidate_verified` PASS. qualification
  transaction은 `VERIFIED` revision `10`, action `3/3` verified, physical write `3`, receipt `2`로
  끝났고 recovery는 필요하지 않았습니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal·referenced blob·candidate binding·receipt 두 장과 fresh process로
    재독한 inventory는 **(b) preserved structured/durable daemon artifact**입니다. 사용자 앱 조작,
    authenticated fresh device read, Docker·CLI·broker observation과 앱 screenshot은
    **(c) reconstructed operator observation**입니다. raw wire frame을 보존하지 않아 (a)는
    주장하지 않습니다.
  - opaque artifact id: `LCQ-BAR-D1N-045F086-20260912T163617Z`입니다. candidate는 fixed receipt
    root 밖의 별도 owner-only no-clobber qualification source로 설치됐으며 private 경로는
    기록하지 않습니다.
  - 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다. UTC span: journal은
    `2026-09-12T20:35:19.592904Z`에 생성되고
    `2026-09-12T20:35:30.902729Z`에 terminal update됐습니다. persistent service는
    `2026-09-12T20:34:48.304981Z`에 정지되고 `2026-09-12T20:35:37.727841Z`에 다시 시작됐습니다.
    앱 복원·final authenticated read·durable inventory 재독은
    `2026-09-12T20:44:45.386Z` 이전에 끝났습니다. 마지막 값은 전체 조사 시간이 아니라 최종
    Agent Bridge 교차검토 보고 시각을 사용한 외부 상한이며 저장소 artifact만으로 재검증할 수
    없습니다.
  - SHA-256 identity binding:
    `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 이 Bar 한 대에만
    결속합니다.
  - artifact digest (SHA-256): terminal journal
    `6f42ad2f733d99105b0be85a0c8a3c23ef4cdbd864cc3d618cae8c5e74b66e70`, candidate
    qualification `abfb27261e4959baa217ab4ca63dc29be878829e36d66b381e1a42e0d7216431`, final app
    screenshot `37754ea66b28f98f6e5df303a949740c59d695c8423ca94ab92f8bed8846dcd2`입니다. screenshot
    raw file은 보존 artifact로 설치하지 않았으므로 마지막 digest는 (c) 관측 결속일 뿐 (b)나
    byte-exact 원복 증거가 아닙니다.
  - candidate는 binding `1`, function `manual_flow`, receipt `2`, qualified physical identity `1`이고
    receipt digest는
    `e73f6752e83ae00742c8083447c3c0564d6936ee5818234261a64d133031e035`,
    `a37007ef813351f15d09b028910ea529ad75f19005234f26117727ff9e7249fc`입니다. 둘 다
    `2026-09-12T20:35:30.902729Z`에 완료됐습니다.
  - 세 action의 control response body는 모두 empty였고 ACK digest는 SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다. 이 ACK
    bytes는 기존 Pro operation에서 설치된 content-addressed blob과 동일해 세 action의 install이
    journal blob count를 늘리지 않았습니다. action diagnostic은 모두 `null`이고 expected readback은
    각각 Flow `45`·`44`·`45`였습니다.
  - empty ACK은 runtime이 request와 sequence가 결속된 control response를 받은 뒤 4-byte sequence를
    제거한 body가 비어 있었다는 뜻입니다. 그 sequence 판정은 runtime에서만 수행되고 보존되지
    않으므로 동일한 `acknowledgement_sha256` 자체는 이 operation을 판별하는 durable evidence가
    아닙니다. 보존 artifact에서 이 실행을 판별하는 근거는 action별 exact readback과 terminal
    journal입니다.
  - 종료 journal inventory는 operations `10`·blobs `27`·files `37`, tree SHA-256은
    `0d82a3dde445a3d0ded163b213d1d6f1d49aa09d04886043ef8dd5cd526add6a`입니다. operation `+1`과
    blob `+6`은 이 qualification으로 설명됩니다. fresh inventory에서 current record가 참조하지만
    앞선 nine operation이 참조하지 않은 blob을 대조한 결과, 신규 blob은 intent (`1,216` bytes),
    full baseline status (`401` bytes), Flow `45` payload (`409` bytes), Flow `44` payload
    (`409` bytes), Flow `45` readback (`11` bytes), Flow `44` readback (`11` bytes)의 여섯
    종류였습니다. 각 파일은 위 canonical journal-tree digest에 포함됩니다.
  - fixed qualification receipt root는 files `12`, tree SHA-256
    `e1d294897acd0f5c60a18ab0bc53f753c4ec4c411212ce37550784386714ded2`로 `+2`이며, 증가는 위
    receipt 두 장입니다. candidate qualification 파일은 이 count 밖의 별도 no-clobber source입니다.
  - final authenticated fresh read는 장비 `6/6`, firmware provenance `6/6`, 대상 power ON·
    TimerOFF·Mode `classic`·Linkage `independent`·Flow `91`·Frequency `80`을 반환했습니다. 앱
    screenshot의 별도 `Pulse` 선택은 Mode 어휘가 아니라 Bar의 `PulseTide` 계열 UI이며 decoded
    Mode `classic`과 모순되지 않습니다.
  - persistent service exactly `1`, 의도적 stop/start 뒤 새 restart counter `0`, current
    qualification binding `1`·receipt `4`·ignored `0`, direct control `2`, Bar command `0`, group
    command·resume `0`, retained command payload `0`, unfinished journal `0`이었습니다. restart `0`은
    operation 간 연속성 증거로 사용하지 않습니다.
- 원복 검증 결과:
  - agent qualification은 Flow `45` baseline에 대해 `45 -> 44 -> 45` 세 phase의 independent exact
    readback을 확인했고 recovery frame 없이 terminal `VERIFIED`로 끝났습니다.
  - 사용자 앱 precondition 전과 final authenticated read에서 공통으로 확인한 power ON·TimerOFF·
    Mode `classic`·Linkage `independent`·Flow `91`은 복귀했습니다. Frequency `80`과 앱 화면의
    별도 `PulseTide` 선택은 실행 전 동일 필드 집합을 보존하지 않아 원값과의 exact 복원 여부를
    확정하지 않습니다. 앱이 내부적으로 보낸 physical frame 수도 관측하지 않았습니다.
  - candidate와 새 receipt 외에 current qualification source·running generation은 바꾸지
    않았습니다. 따라서 Bar command surface는 계속 `0`이고 실패했더라도 old persistent authority가
    별도 generation rollback 없이 유지되는 구조였습니다.
- **이 실행으로 새로 확정된 사실:** D1n의 Bar product profile과 reviewed scalar writer는 실제 Bar
  firmware에서 exact current admission을 통과하고, `45 -> 44 -> 45` 세 physical write·exact
  readback·terminal journal로 독립 `manual_flow` receipt pair를 만들었습니다. agent가 변경한
  Flow는 exact `45`로 복원됐고, 사용자는 공통 관측 필드의 원 운전 상태
  `ON/TimerOFF/classic/independent/91`로 돌아갔으며, 새 authority는 아직 running surface에 열리지
  않았습니다.
- 이 실행으로 확정하지 못한 것: raw wire frame, 앱의 physical frame 수, Frequency·PulseTide를
  포함한 전체 state의 pre/post byte equality, Flow `100`의 장비 실수용, Bar `power`, 세 장비
  power qualification, group actuation, HA Core restart·host reboot 복귀, 장시간 운용과 Q1·Q2는
  확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 이 기록을 독립 검토·commit한 뒤 candidate를
  current set과 offline immutable merge하고, 병합 결과 binding·surface union을 선언과 양방향 exact
  대조합니다. 그 뒤 새 Compose generation 하나에서만 Bar write-allow와 `manual_flow` authority를
  함께 열고, 기존 두 Pro control payload byte 불변·Bar number `30..100` 하나만 신설·group surface
  `0`·startup device write `0`을 검증합니다. Bar의 상시 write 가능한 HA number 신설은 배포 전에
  사용자에게 별도로 공개합니다. 이후 power qualification은 별도 on-site 확인 뒤 마지막에
  순차 수행하며, OFF 상태에서 비전원 필드가 달라지면 qualifier가 fail-closed하고 safer-OFF로
  남을 수 있음을 사전에 공개합니다.
