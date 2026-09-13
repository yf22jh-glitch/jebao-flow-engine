# run: 2026-09-13-local-power-qualification-second-pro

- 목적(한 문장): 두 번째 Local Wavemaker Pro 한 대에서 safer-OFF `power` qualification의
  same-value ON → OFF → planned ON 세 physical write와 exact readback을 수행하고, 독립 receipt
  pair를 running authority와 분리된 owner-only candidate에 설치한 뒤 사용자가 Timer schedule
  운전을 복원했는지 확인했습니다.
- 이 실행이 답하려던 질문: 첫 번째 Pro에서 실기한 D1o power producer가 두 번째 Pro의 현재
  product·firmware에서도 TimerOFF admission, journal-first ON → OFF → ON, OFF 구간의 비전원 필드
  유지, planned restore와 terminal receipt 계약을 그대로 만족하는가.
- 사용자 승인 범위:
  - repository maintainer 겸 on-site hardware approver는 현장 감시와 즉시 전원 차단·앱 ON 복원
    수단을 갖춘 상태에서 이 두 번째 Pro 한 대의 `power` qualification을 승인했습니다. agent의
    정확히 세 physical write와 사용자가 수행하는 Timer ownership 변경·복원만 포함하며, 다른
    장비 write, candidate merge, 새 generation 배포, group actuation, native ASYNC 하네스,
    Home Assistant Core update·restart·설정 변경은 포함하지 않았습니다.
  - operation은 same-value ON → OFF → planned ON을 전송합니다. OFF 뒤 어느 nonterminal failure든,
    planned ON이 이미 성공한 뒤 terminal save가 실패한 경우까지, recovery는 무인 ON을 보내지
    않고 OFF로 수렴합니다. `ROLLED_BACK`·recovery write `0`이어도 실제 장비는 OFF일 수 있으므로
    durable phase보다 실제 power를 먼저 확인하고 필요하면 사용자가 앱이나 물리 수단으로 ON을
    복원하기로 했습니다.
  - baseline Flow `40`은 app-declared capability `30..100`과 다른 attended admission 값이며 기존
    `_SAFE_FIRST_WRITE_MAXIMUM = 45` 아래입니다. 이번 power operation은 Flow 값을 쓰지 않았고
    ON/OFF 전환만 수행했습니다. TimerON 복원 뒤 schedule이 적용한 Flow `55`는 사용자가 이미
    구성한 운전값이며 agent의 attended Flow 목적값이 아닙니다.
- 실행한 source commit SHA:
  - safer-OFF 정책은 `135bb2be17c628bdf4905fdfa814e851867286d9`, outer fault 결속은
    `ab495aeac103c88870f29ece8735213ba91a146f`, producer source는
    `c242635f1177783b75c3d06c531f48735a0d2dda`, 실행 image에 포함된 최종 recovery-mapping 결속은
    `045f086b82c6c9a25a8e22cd892be2f9cf7fdee9`입니다.
  - exact x86_64 one-shot image id는
    `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`, 설치 Python tree는
    files `137`, SHA-256
    `db56fca35b02628ea3216f2d7430da569eda99fbafe94935aa2f6932b38b57a5`입니다.
  - active Pro template SHA-256은
    `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`입니다.
- 실행 전 조건:
  - persistent service exactly `1`, 장비 authenticated fresh `6/6`, unfinished journal `0`, legacy
    authority idle, retained command payload `0`을 확인했습니다. current running authority는
    qualification binding `2`·receipt `6`·ignored `0`, direct `manual_flow` control `3`, `power`
    control `0`, group command·resume `0`이었습니다.
  - journal inventory는 operations `11`·blobs `33`·files `44`, 이 run에서 정의한 sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256은
    `83cc8ecadb4758426c2e2f5a7dc1c2d91dba2d857ac1a9f8e6edfc706077c72c`였습니다. fixed
    qualification receipt root는 files `14`, 같은 방식의 tree SHA-256은
    `7acb417634c63296b4bdd0814ea84d571ff0e27cacc9476f1f8c4135e149f8f8`였습니다. 이
    canonicalization은 이 run의 전후 비교에만 사용합니다.
  - 두 번째 Pro의 직전 보존 상태는 power ON·TimerON·Mode `constant`·Linkage `independent`·latent
    manual Flow `40`이었습니다. 첫 앱 시도에서 사용자는 Timer 대신 이 대상의 power를 OFF로
    바꾸었고 Timer는 ON으로 남았습니다. target 자체의 fresh admission 불일치가 이를 agent write
    전에 검출했고, 동시에 첫 번째 Pro의 TimerOFF 불변을 읽어 반대 장비 조작이 아니었음을
    확인했습니다. 사용자는 대상 power를 ON으로 복원한 뒤 Timer만 OFF로 바꿨습니다. 앱이 내부적으로
    보낸 frame 수와 accidental OFF 체류 시간은 관측하지 않았습니다.
  - corrected dual-anchor authenticated read는 첫 번째 Pro가 power ON·TimerOFF·Mode `constant`·
    Linkage `independent`·Flow `40`으로 불변이고, 두 번째 Pro가 power ON·TimerOFF·Mode `constant`·
    Linkage `independent`·Flow `40`임을 확인했습니다. 장비는 fresh `6/6`이었고 current manifest의
    public target anchor는 두 번째 Pro를 정확히 하나로만 선택했습니다.
  - 위 사용자 OFF → ON과 곧 이어진 qualification OFF → ON은 같은 장비에서 별개의 물리 사건입니다.
    앞의 사건은 agent operation이 아니고 local-control journal을 통하지 않았으며, qualification
    시작 전 journal operations 증가는 없었습니다.
  - owner-only observer input은 `4,646` bytes, SHA-256
    `f4dec1353a3578a43692e91416835dddb1829b2737f92d6f387c93702c6eeb71`, mode `0600`, link count
    `1`이었고 current epoch·target binding·template·config limits와 exact 일치했습니다. private
    절대경로·logical member·physical key는 기록하지 않습니다.
- 사용한 명령·주요 파라미터:
  - persistent control을 stop-first로 정지한 뒤 opaque operation id
    `LCPQ-PWR-PRO2-D1O-045F086-20260913T042752Z`, current Pro template, 신규 no-clobber candidate,
    explicit write opt-in을 사용해 `jebao-flow-local-power-qualify`를 실행했습니다.
  - producer는 send-free current-state admission 뒤 same-value ON, OFF, planned ON을 각각 새
    writer로 실행했습니다. 각 phase는 durable journal 저장 뒤 physical frame 하나와 independent
    exact readback을 수행했고 forward retry는 없었습니다.
  - qualifier 종료 뒤 기존 persistent generation만 다시 시작했습니다. candidate merge, config
    write-enable 변경, 새 Compose generation과 `power` command surface 배포는 실행하지 않았습니다.
  - recovery는 필요하지 않았습니다. 실패했다면 install manifest를 재유도하는 일반 local-control
    CLI가 아니라 config-only `jebao-flow-local-qualify recover`만 사용하고, 결과 문구보다 실제
    power를 먼저 확인하는 절차였습니다.
  - 사용자는 qualification 종료 상태 power ON·TimerOFF·latent Flow `40`에서 Timer만 다시 ON으로
    바꿨습니다. agent는 이 복원에 frame을 보내지 않고 두 Pro와 전체 장비를 authenticated fresh
    read했습니다.
- 실행 중 정정:
  - 첫 inventory wrapper는 hardware-safety volume identity 활성화를 빠뜨려 JournalStore 생성 전에
    닫혔습니다. compose image 검사 초안은 service image가 하나라고 가정해 둘인 현재 compose를
    거부했고, 다음 초안은 `build:` service의 config JSON에 `image` key가 있다고 가정해 중단됐습니다.
    corrected wrapper는 running container가 실제 사용하는 image reference에서 exact image를
    유도했습니다. 세 초안은 persistent service stop과 device·broker 접촉 전에 끝났고 durable
    mutation·physical write는 `0`이었습니다.
  - Timer 복원 postcheck 초안 하나는 local Docker context에 remote container 이름을 적용해 absent
    container에서 끝났습니다. artifact audit 초안 하나는 모든 등록 product에 `manual_flow`
    descriptor가 있다고 가정해 catalog 조회에서 중단됐고, 실제 product descriptor가 있는 경우만
    세는 wrapper로 다시 확인했습니다. 전자는 remote 접촉 전, 후자는 broker subscription 전이며
    둘 다 device command와 durable mutation은 `0`이었습니다.
- 종료 지점(stage / failure code): `second_pro_d1o_power_candidate_verified` PASS. qualification
  transaction은 `VERIFIED` revision `10`, action `3/3` verified, physical write `3`, receipt `2`로
  끝났고 recovery frame은 필요하지 않았습니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal·referenced blob·candidate binding·receipt 두 장과 fresh process로
    재독한 inventory는 **(b) preserved structured/durable daemon artifact**입니다. 사용자 앱 조작,
    Docker·CLI·broker observation과 authenticated fresh device read·schedule image digest는
    **(c) reconstructed operator observation**입니다. raw wire frame과 raw schedule image를 별도
    보존하지 않아 (a)는 주장하지 않습니다.
  - opaque artifact id: `LCPQ-PWR-PRO2-D1O-045F086-20260913T042752Z`입니다. candidate는 fixed receipt
    root 밖의 별도 owner-only no-clobber source이며 private 경로는 기록하지 않습니다.
  - 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다. UTC span: persistent service는
    `2026-09-13T04:30:19.579525Z`에 정지됐고, journal은
    `2026-09-13T04:30:26.710642Z`에 생성되어
    `2026-09-13T04:30:44.613667Z`에 terminal update됐습니다. persistent service의 새 process는
    `2026-09-13T04:30:44.732010Z`에 시작됐습니다. 중간 action revision별 timestamp는 terminal
    artifact에 보존되지 않아 실제 qualification OFF 체류는 정확히 유도하지 못합니다. 전체 journal
    span `17.9`초는 OFF 체류의 상한이고 service process 부재는 약 `25.2`초였습니다. retained
    availability의 실제 offline 구간은 별도로 계측하지 않아 process 부재보다 조금 길 수 있습니다.
    사용자 TimerON 복원 뒤 final authenticated read와 durable·broker inventory 재독은
    `2026-09-13T04:48:14.097428Z` 이전에 끝났습니다.
  - SHA-256 identity binding:
    `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 이전 두 번째 Pro
    manual-flow 기록과 같은 물리 개체입니다.
  - artifact digest (SHA-256): terminal journal
    `40c4ab0bff4fdadc9f20ab0e5af3a7e1509a4ae18e512d121d531fe11a3efe43`, candidate qualification
    `83e715f5d43c57f9ef8c80d06f634343a45c5a663ac2a07ebbf738a0e3e86643`, final observed schedule
    image `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`입니다. 마지막 값은 raw
    image를 별도 보존하지 않은 (c) 관측 digest이며 사후 재검증 가능한 (b)가 아닙니다.
  - terminal journal의 세 action은 `restore_on_recovery=[true,false,true]`, expected power
    `[ON,OFF,ON]`, action-local baseline/recovery target `[OFF,ON,OFF]`였습니다. ON payload SHA-256은
    `edc29d213dbc9d8ac6e3ffb7da7e917f3ab4e0be1dc81cebd81da71e17bda672`, OFF payload SHA-256은
    `05b07b76bdb14d4a4001e79f4637a5e12ccd4f62c47f55adf57fde2dfa185194`이고 각각 `460` bytes입니다.
    ON·OFF exact readback SHA-256은 각각
    `2c08ccafb6243e28fda3499daa8385b139474d0b45f63ee57911b11ab3ba87e4` (`17` bytes),
    `7ca2832ca507e84ea79f83bba9098c6299a87842ee6fa526a9e0bd61b354d654` (`18` bytes)입니다.
    operation 시작의 full ON baseline status SHA-256은
    `0caaeed312879b4417281dfef94db391f499ea0fdede1ec26ffbe2c370027192` (`452` bytes)이며,
    action-local `baseline_readback=OFF`를 시작 상태라고 해석하지 않습니다.
  - 이 run은 첫 번째 Pro run과 달리 first-send watcher artifact를 별도로 만들지 않았습니다. 따라서
    first-send 전 mapping 설치를 새 (b) capture로 주장하지 않고, terminal journal의 durable mapping과
    reviewed source의 journal-first 순서만 기록합니다.
  - candidate는 binding `1`, function `power`, receipt `2`, qualified physical identity `1`, `553`
    bytes, mode `0600`, link count `1`이고 current owner와 일치했습니다. receipt digest는
    `b97fd1040bdf6d080d91ff6895a274dfbce3611bb75c00d24d400d54777a4e5b`,
    `3bdd319c96081a68504f54a210b7eac258f2677ba0be31b78d30bf6ecad8a517`이며 OFF와 planned ON action에
    대한 것입니다. same-value ON action에는 receipt를 발급하지 않았습니다.
  - 세 action의 control response body는 모두 empty였고 ACK digest는 SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다. empty ACK은
    runtime이 request와 sequence가 결속된 control response를 받은 뒤 4-byte sequence를 제거한
    body가 비어 있었다는 뜻입니다. sequence 판정은 runtime에서만 수행되고 보존되지 않으므로
    동일한 `acknowledgement_sha256` 자체는 이 operation을 판별하는 durable evidence가 아닙니다.
    보존 artifact에서 판별 근거는 action별 exact readback과 terminal journal입니다.
  - 종료 journal inventory는 operations `12`·blobs `35`·files `47`, tree SHA-256은
    `9b0bc7ee911de1edcffb9dedd22cd27987d0b7e082dbc04e68a6f9338d6350ab`입니다. 이 operation이
    참조하는 고유 blob은 empty ACK을 포함해 `7`종입니다. 직전 inventory 대비 operation `+1`·blob
    `+2`이고, 앞선 모든 operation의 참조 집합과 대조한 신규 blob은 intent (`1,310` bytes,
    `517ba930cec8d92f8d4ab5cedc7f8572617b57b3c2b69ec56bdbd736524be156`)와 이 개체의 full baseline
    status (`452` bytes, 위 digest)입니다. ON·OFF payload/readback 네 개와 empty ACK은 첫 번째 Pro
    power operation의 content-addressed blob을 재사용했습니다.
  - fixed qualification receipt root는 files `16`, tree SHA-256
    `a64d35ba1416a2b0c05211c5b0316f7a36eef795da87bfd13f7b2bd5b41e69fa`로 `+2`이며, 증가는 위
    receipt 두 장입니다. candidate qualification 파일은 이 count 밖의 별도 no-clobber source입니다.
  - TimerON 복원 뒤 dual-anchor final read는 장비 fresh `6/6`, modeled power `5/5` ON, 첫 번째
    Pro power ON·TimerOFF·Mode `constant`·Linkage `independent`·Flow `40` 불변, 두 번째 Pro power
    ON·TimerON·Mode `constant`·Linkage `independent`·latent manual Flow `40`을 반환했습니다.
    modeled power는 product catalog에 `power` function mapping이 있는 장비만 세며, 한 제품군은
    전원 성격 datapoint 이름과 모델이 달라 이 mapping이 없으므로 분모가 `5`입니다.
    decoded schedule은 valid entry `14`, invalid slot `0`, device local time `13:43`에 active match
    slot `7` 하나였습니다. 해당 slot은 `12:11–14:55`·`tidal`·Flow `55`이고 current `AutoMode=tidal`·
    `AutoFlow=55`와 일치했습니다. `AutoFreq=5`도 관측했지만 decoded slot frequency와의 의미
    동일성은 이 run에서 판정하지 않습니다.
  - final schedule image는 `432` bytes이고 위 digest는 Timer 변경 전 관측과 같았습니다. 두 관측
    모두 raw image를 보존하지 않은 (c)이므로 schedule 전체의 byte-exact 원복을 (b)로 주장하지
    않습니다.
  - persistent service exactly `1`, exact reviewed image, `unless-stopped`, 의도적 stop/start 뒤 새
    restart counter `0`, runtime log problem `0`이었습니다. runtime manifest generation은
    `b022b3fe64b42f31f03d8d1d8db6346c`, raw manifest SHA-256은
    `5647b4d3f753743c24d02ad83007f58fa318d3e0a1c0a2d26bc189da7cc3dfe0`입니다. 이 runtime
    manifest generation을 Compose generation `d730650c4d105e8af7bb74fe720ffa46`과 같은 값으로
    해석하지 않습니다.
  - running qualification은 `1,231` bytes, SHA-256
    `abaa4ad15444d2f9db025cafbfb9811dc9ddb954d45afc0c7da395f806f12803`으로 불변이며 binding
    `2`·receipt `6`·ignored `0`, discovery `86`, direct `manual_flow` control `3`, `power` control `0`,
    group command·resume `0`입니다. broker retained availability는 `7/7` online이고 command topic
    expected `3`의 retained non-empty payload는 `0`, unfinished journal은 `0`이었습니다. restart
    `0`은 operation 간 연속성 증거로 사용하지 않습니다.
- 원복 검증 결과:
  - agent qualification은 시작 ON 상태에서 same-value ON → OFF → planned ON 세 phase의
    independent exact readback을 확인했고 recovery frame 없이 terminal `VERIFIED`로 끝났습니다.
    action 1의 OFF 직후와 action 2의 planned ON 직전 fresh 판정은 모두 power OFF 동안 Flow `40`·
    Mode `constant`·Linkage `independent`·TimerOFF가 유지됨을 검사하고 통과했습니다. 이 판정은 이
    두 번째 Pro의 현재 product·firmware와 qualification 시점의 TimerOFF 상태에만 한정하며 raw
    full-state byte equality는 주장하지 않습니다.
  - 사용자의 TimerON 복원 뒤 final read는 대상 power ON·TimerON·latent Flow `40`과 current active
    schedule slot의 `tidal`·Flow `55`를 확인했습니다. 이는 TimerOFF에서 만든 receipt를 무효화하지
    않지만, TimerON 상태에서 power를 전환하고 schedule ownership이 재개되는 동작을 검증한 것은
    아닙니다.
  - candidate와 새 receipt·journal operation 외에 current qualification source·running generation은
    바꾸지 않았습니다. 따라서 `power` command surface는 계속 `0`이고 candidate는 running authority와
    격리돼 있습니다.
- **이 실행으로 새로 확정된 사실:** qualification 시점 TimerOFF인 이 두 번째 Pro의 현재
  product·firmware에서도 power OFF인 두 fresh 판정 지점에 Flow `40`·Mode `constant`·Linkage
  `independent`·TimerOFF가 유지됐습니다. D1o safer-OFF producer는 ON → OFF → ON 세 physical
  write·exact readback·terminal journal로 독립 `power` receipt pair를 만들었고, 장비는 final ON 뒤
  사용자의 TimerON schedule 운전으로 복귀했습니다.
- 이 실행으로 확정하지 못한 것: raw wire frame, 사용자의 앱 조작 frame 수와 accidental OFF 체류,
  failure 시 actual recovery, TimerON·schedule 활성 상태의 power 전환과 slot ownership 재개, Bar의
  power-OFF 비전원 필드 유지와 `power` qualification, 세 power candidate의 merge·persistent direct
  surface·group control/resume delta, 장시간 운용, host reboot 복귀와 Q1·Q2는 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 이 기록을 독립 검토·commit한 뒤 Bar를 사용자
  precondition과 같은 on-site 경계에서 한 대만 qualification합니다. 세 candidate를 모두 확보한
  뒤에만 current set과 offline immutable merge하고, 배포 전에 TimerON인 두 번째 Pro에도 runtime
  power command가 별도 Timer gate 없이 전달된다는 사실과 미검증 schedule 재개 조건을 사용자에게
  공개합니다. merge·deploy 예상 delta에는 direct `power`뿐 아니라 마지막 dual-qualified group
  member에서 열릴 수 있는 group control·resume를 이름으로 선언하고 양방향 exact projection으로
  확인합니다.
