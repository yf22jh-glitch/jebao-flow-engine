# run: 2026-09-13-local-power-qualification-first-pro

- 목적(한 문장): 첫 번째 Local Wavemaker Pro 한 대에서 safer-OFF `power` qualification의
  same-value ON → OFF → planned ON 세 physical write와 exact readback을 수행하고, 첫 frame 전에
  durable recovery mapping이 설치됐음을 보존한 뒤 receipt pair를 running authority와 분리된
  owner-only candidate에 설치했습니다.
- 이 실행이 답하려던 질문: D1o power producer가 실제 Pro firmware에서 현재 ON 상태와 비전원
  필드를 admission하고, OFF 구간을 포함한 세 phase를 journal-first로 실행하면서 어떤
  nonterminal failure에서도 무인 ON 대신 OFF로 수렴하는 durable mapping을 첫 send 전에
  설치한 채 terminal `VERIFIED` receipt pair를 만들 수 있는가.
- 사용자 승인 범위:
  - repository maintainer 겸 on-site hardware approver는 현장 감시와 즉시 전원 차단·앱 ON 복원
    수단을 갖춘 상태에서 이 첫 번째 Pro 한 대의 `power` qualification을 승인했습니다. agent의
    정확히 세 physical write만 포함하며, 다른 장비 write, candidate merge, 새 generation 배포,
    group actuation, native ASYNC 하네스, Home Assistant Core update·restart·설정 변경은 포함하지
    않았습니다.
  - operation은 same-value ON → OFF → planned ON을 전송합니다. OFF 뒤 어느 nonterminal failure든,
    planned ON이 이미 성공한 뒤 terminal save가 실패한 경우까지, recovery는 무인 ON을 보내지
    않고 OFF로 수렴합니다. `ROLLED_BACK`·recovery write `0`이어도 실제 장비는 OFF일 수 있으므로
    durable phase보다 실제 power를 먼저 확인하고 필요하면 사용자가 앱이나 물리 수단으로 ON을
    복원하기로 했습니다.
  - baseline Flow `40`은 app-declared capability `30..100`과 다른 attended admission 값이며 기존
    `_SAFE_FIRST_WRITE_MAXIMUM = 45` 아래입니다. 이번 power operation은 Flow 값을 쓰지 않았고
    ON/OFF 전환만 수행했습니다.
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
  - persistent service exactly `1`, 장비 authenticated fresh `6/6`, power ON `6/6`, firmware
    provenance `6/6`, unfinished journal `0`, legacy authority idle, retained command payload `0`을
    확인했습니다. current running authority는 qualification binding `2`·receipt `6`·ignored `0`,
    direct `manual_flow` control `3`, `power` control `0`, group command·resume `0`이었습니다.
  - journal inventory는 operations `10`·blobs `27`·files `37`, 이 run에서 정의한 sorted
    `(relative path, SHA-256(bytes))` compact-JSON tree SHA-256은
    `0d82a3dde445a3d0ded163b213d1d6f1d49aa09d04886043ef8dd5cd526add6a`였습니다. fixed
    qualification receipt root는 files `12`, 같은 방식의 tree SHA-256은
    `e1d294897acd0f5c60a18ab0bc53f753c4ec4c411212ce37550784386714ded2`였습니다. 이
    canonicalization은 이 run의 전후 비교에만 사용합니다.
  - exact current manifest resolution은 public identity anchor와 current manual-flow binding에서
    첫 번째 Pro target을 각각 정확히 하나로만 선택했습니다. authenticated fresh read는 대상이
    power ON·TimerOFF·Mode `constant`·Linkage `independent`·Flow `40`임을 확인했습니다.
  - owner-only observer input은 `4,646` bytes, SHA-256
    `f4dec1353a3578a43692e91416835dddb1829b2737f92d6f387c93702c6eeb71`, mode `0600`, link count
    `1`이었고 current epoch·target binding·template·config limits와 exact 일치했습니다. private
    절대경로·logical member·physical key는 기록하지 않습니다.
- 사용한 명령·주요 파라미터:
  - persistent service를 stop-first로 정지하고 opaque operation id
    `LCPQ-PWR-PRO1-D1O-045F086-20260912T234538Z`, current Pro template, 신규 no-clobber candidate,
    explicit write opt-in을 사용해 `jebao-flow-local-power-qualify`를 실행했습니다.
  - producer는 send-free current-state admission 뒤 same-value ON, OFF, planned ON을 각각 새
    writer로 실행했습니다. 각 phase는 durable journal 저장 뒤 physical frame 하나와 independent
    exact readback을 수행했고 forward retry는 없었습니다.
  - qualifier 종료 뒤 기존 persistent generation만 다시 시작했습니다. candidate merge, config
    write-enable 변경, 새 Compose generation과 `power` command surface 배포는 실행하지 않았습니다.
  - recovery는 필요하지 않았습니다. 실패했다면 install manifest를 재유도하는 일반 local-control
    CLI가 아니라 config-only `jebao-flow-local-qualify recover`만 사용하고, 결과 문구보다 실제
    power를 먼저 확인하는 절차였습니다.
- 실행 중 정정:
  - 첫 preflight wrapper는 현재 service의 두 explicit template root 대신 낡은 단일-root 환경값을
    사용해 current-input 검증에서 중단됐습니다. corrected root wrapper는 Docker exec process에
    hardware-safety volume identity를 먼저 활성화하지 않아 journal validation에서 중단됐습니다.
    둘 다 journal 생성·device·broker 접촉 전이었고 mutation·physical write `0`이었습니다.
  - 배포 후 full postcheck 초안 하나는 모든 product의 power field가 같은 이름이라고 가정했습니다.
    broker read-only audit 뒤 다른 product의 fresh decode에서 중단됐고, product catalog의 실제
    scalar field를 유도하는 wrapper로 다시 확인했습니다. 이 초안의 device write와 durable
    mutation은 `0`이었습니다. 이는 producer의 field-agnostic boolean mapping 판정이 배제한
    하드코딩과 같은 종류의 가정이 audit 초안에서 실제로 실패한 사례입니다.
- 종료 지점(stage / failure code): `first_pro_d1o_power_candidate_verified` PASS. qualification
  transaction은 `VERIFIED` revision `10`, action `3/3` verified, physical write `3`, receipt `2`로
  끝났고 recovery frame은 필요하지 않았습니다.
- 장비·daemon이 만든 결과:
  - 증거 등급: terminal journal·referenced blob·candidate binding·receipt 두 장, 첫 send 전
    reload된 revision-zero mapping capture와 fresh process로 재독한 inventory는 **(b) preserved
    structured/durable daemon artifact**입니다. Docker·CLI·broker observation과 authenticated fresh
    device read는 **(c) reconstructed operator observation**입니다. raw wire frame을 보존하지 않아
    (a)는 주장하지 않습니다.
  - opaque artifact id: `LCPQ-PWR-PRO1-D1O-045F086-20260912T234538Z`입니다. candidate와 first-send
    mapping capture는 fixed receipt root 밖의 별도 owner-only no-clobber artifact로 보존됐으며
    private 경로는 기록하지 않습니다.
  - 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다. UTC span: persistent service는
    `2026-09-12T23:48:40.568815328Z`에 정지됐고, journal은
    `2026-09-12T23:48:47.939367Z`에 생성되어
    `2026-09-12T23:49:06.867171Z`에 terminal update됐습니다. persistent service의 새 process는
    `2026-09-12T23:49:07.110843769Z`에 시작됐습니다. 중간 action revision별 timestamp는 terminal
    artifact에 보존되지 않아 실제 OFF 체류는 정확히 유도하지 못합니다. 전체 qualification
    `18.9`초는 OFF 체류의 상한이고, service 부재·availability 공백은 약 `26.5`초였습니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 첫 번째 Pro 한 대에만
    결속합니다.
  - artifact digest (SHA-256): terminal journal
    `1644c0456253defe86fb103c0a5b97370aa3f011d494b2777ada1c1341264d54`, candidate qualification
    `7c9ac354169f8c9dc2727a90e30a87e14149eba23bfb55ca8785879546b9744e`, first-send durable mapping
    capture `1cf33de7c9df52edf4911facddcc5f810e8594de6a50f411f93f6d6bdc6938ed`입니다. candidate는
    `553` bytes, mode `0600`, link count `1`입니다.
  - first-send watcher는 첫 physical frame 전에 reload된 journal revision `0`을 보존했습니다. 세
    action의 `restore_on_recovery`는 `[true,false,true]`, expected power는 `[ON,OFF,ON]`, action-local
    baseline/recovery target은 `[OFF,ON,OFF]`였습니다. ON payload SHA-256은
    `edc29d213dbc9d8ac6e3ffb7da7e917f3ab4e0be1dc81cebd81da71e17bda672`, OFF payload SHA-256은
    `05b07b76bdb14d4a4001e79f4637a5e12ccd4f62c47f55adf57fde2dfa185194`이고, ON·OFF exact readback
    SHA-256은 각각
    `2c08ccafb6243e28fda3499daa8385b139474d0b45f63ee57911b11ab3ba87e4`,
    `7ca2832ca507e84ea79f83bba9098c6299a87842ee6fa526a9e0bd61b354d654`입니다. operation 시작의
    full ON baseline status SHA-256은
    `a2452726a9037a073e492632035ffcda6700bbf13df815d64eddd4797ee0e140`이며, action-local
    `baseline_readback=OFF`를 시작 상태라고 해석하지 않습니다.
  - candidate는 binding `1`, function `power`, receipt `2`, qualified physical identity `1`이고
    receipt digest는
    `c036219c5e17ca084c6c231fa95af776202508c875a65b627a5c2b43f6776865`,
    `b0eee020c8e12d092758646ad4d0ada4b8e949562efe1bb45a7f94295ae9c14c`입니다. 두 receipt는 OFF와
    planned ON action에 대한 것이며 same-value ON action에는 receipt를 발급하지 않았습니다.
  - 세 action의 control response body는 모두 empty였고 ACK digest는 SHA-256(empty)
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다. empty ACK은
    runtime이 request와 sequence가 결속된 control response를 받은 뒤 4-byte sequence를 제거한
    body가 비어 있었다는 뜻입니다. sequence 판정은 runtime에서만 수행되고 보존되지 않으므로
    동일한 `acknowledgement_sha256` 자체는 이 operation을 판별하는 durable evidence가 아닙니다.
    보존 artifact에서 판별 근거는 action별 exact readback과 terminal journal입니다.
  - 종료 journal inventory는 operations `11`·blobs `33`·files `44`, tree SHA-256은
    `83cc8ecadb4758426c2e2f5a7dc1c2d91dba2d857ac1a9f8e6edfc706077c72c`입니다. operation `+1`과
    blob `+6`은 intent (`1,310` bytes,
    `2c97e6b6e633489ddebb37b6875b3ba4b4f0bb8ec6fd039bc38da7b1331ee703`), shared full baseline
    status (`452` bytes), ON·OFF payload 두 개(각 `460` bytes), ON scalar readback (`17` bytes),
    OFF scalar readback (`18` bytes)으로 설명됩니다. 위 payload·status·readback digest는
    first-send mapping 단락의 값과 일치합니다. 세 empty ACK은 앞선 operation에 설치된
    content-addressed blob을 재사용해 순증 `0`입니다.
  - fixed qualification receipt root는 files `14`, tree SHA-256
    `7acb417634c63296b4bdd0814ea84d571ff0e27cacc9476f1f8c4135e149f8f8`로 `+2`이며, 증가는 위
    receipt 두 장입니다. candidate qualification 파일은 이 count 밖의 별도 no-clobber source입니다.
  - final authenticated fresh read는 장비 `6/6`, power ON `6/6`, 대상 power ON·TimerOFF·Mode
    `constant`·Linkage `independent`·Flow `40`을 반환했습니다. availability retained `7/7` online,
    command topic expected `3`의 retained non-empty payload는 `0`이었습니다.
  - persistent service exactly `1`, 의도적 stop/start 뒤 새 restart counter `0`, runtime log problem
    `0`, current qualification binding `2`·receipt `6`·ignored `0`, direct `manual_flow` control `3`,
    `power` control `0`, group command·resume `0`, unfinished journal `0`이었습니다. restart `0`은
    operation 간 연속성 증거로 사용하지 않습니다.
- 원복 검증 결과:
  - agent qualification은 시작 ON 상태에서 same-value ON → OFF → planned ON 세 phase의
    independent exact readback을 확인했고 recovery frame 없이 terminal `VERIFIED`로 끝났습니다.
    final authenticated fresh read도 대상의 power ON·TimerOFF·Mode `constant`·Linkage
    `independent`·Flow `40`과 전체 장비 `6/6`을 확인했습니다.
  - action 1의 OFF 직후 readback과 action 2의 planned ON 직전 fresh pre-read는 모두 power OFF인
    동안 Flow `40`·Mode `constant`·Linkage `independent`·TimerOFF가 유지됨을 검사하고 통과했습니다.
    이는 아래 확정 범위에서만 말하며 raw full-state byte equality는 주장하지 않습니다.
  - candidate와 새 receipt·journal operation 외에 current qualification source·running generation은
    바꾸지 않았습니다. 따라서 `power` command surface는 계속 `0`이고 first Pro candidate는
    running authority와 격리돼 있습니다.
- **이 실행으로 새로 확정된 사실:** 이 첫 번째 Pro의 현재 product·firmware에서는 power OFF인
  두 fresh 판정 지점에서도 Flow `40`·Mode `constant`·Linkage `independent`·TimerOFF를 시작값과
  같게 보고했습니다. 또한 D1o safer-OFF producer는 실제 장비 첫 frame 전에 recovery mapping을
  durable하게 설치하고 ON → OFF → ON 세 physical write·exact readback·terminal journal로 독립
  `power` receipt pair를 만들었으며, 장비는 최종 ON으로 돌아왔습니다.
- 이 실행으로 확정하지 못한 것: raw wire frame, Bar와 두 번째 Pro의 power-OFF 비전원 필드 유지,
  실패 시 실제 recovery 경로, Bar·두 번째 Pro `power` qualification, 세 candidate의 merge·persistent
  `power` surface, 마지막 group member가 dual-qualified될 때의 group control·resume delta, 장시간
  운용, HA Core restart·host reboot 복귀와 Q1·Q2는 확정하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 이 기록을 독립 검토·commit한 뒤 동일한
  on-site 경계에서 두 번째 Pro와 Bar의 `power`를 한 장비씩 수행합니다. 각 candidate는 running
  authority와 격리하고 세 개가 모두 준비된 뒤에만 current set과 offline immutable merge합니다.
  merge·deploy 전에 direct `power` control뿐 아니라 group control·resume의 예상 delta도 이름으로
  선언하고, 마지막 group member가 `manual_flow`·`power`를 모두 얻으며 group surface가 조용히
  열리는 경로를 양방향 exact projection으로 확인합니다. 요약·중계 메시지의 축약 digest를 identity
  제약으로 쓰지 않고 source of truth에서 full 값을 직접 재유도합니다.
