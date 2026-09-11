# run: 2026-09-11-local-control-qualified-activation

- 목적(한 문장): qualification이 결속된 Pro 한 대의 `manual_flow` 하나만 write 가능한
  Home Assistant/MQTT 표면으로 노출하는 reviewed local-control service를 상시 기동하고, 시작
  device write `0`과 장비 `6/6` fresh 상태를 확인했습니다.
- 이 실행이 답하려던 질문: qualified physical identity·function 쌍 하나를 persistent control
  배포로 전환해도 startup write 없이 단일 authority, exact command surface와 fresh observation을
  유지하는가.
- 사용자 확인 범위: control activation이 Home Assistant Core 업데이트·재부팅·설정 변경 없이도
  write 가능한 retained `number` 엔티티 하나를 상시 노출한다는 사실을 먼저 공개했고, 사용자는
  그 표면을 사용하지 않는 조건으로 진행을 확인했습니다.
- 실행한 source commit SHA: `7ecabbc2422ea5b3875dbed279cb354fa8e9f30c`. 실행 시 저장소 HEAD
  `cf2853af50056d99217e75cca1af18a366ebfa86`까지의 후속 변경은 문서뿐이며 `src/`·`tests/`는
  위 source commit과 동일했습니다.
- source·deployment 결속:
  - 실행 image id의 SHA-256 binding은
    `d80bc094ad1875bf52f61d785d0495c62b4a6c5577882de180e43cdebe381748`이고 직전 reviewed
    observer image와 동일했습니다.
  - control Compose SHA-256은
    `a1bf0a88bfe273a69e1fba9b08bc7316c6859baed5a1d783b899a75c0a62dd1a`, generation은
    `a464c646fd6a786ff6262f9d5408af2a`였습니다.
  - active template SHA-256
    `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`, stable role-binding
    SHA-256 `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`,
    qualification binding SHA-256
    `5ff6f5f0acf14c95d68eab92796b4b3e83d940613cb54fe2c57c2c6a088965ee`를 exact
    확인했습니다.
- 실행 전 조건: observer instance `1`·restart `0`, old `jebao-flowd` `0`, unfinished
  local-control journal `0`, legacy workflows idle, 장비 `6/6` exact binding·fresh,
  absent·ambiguous `0`이었습니다. qualified target은 ON·TimerOFF·`constant`·`independent`·Flow
  `40`이고, write 허용 target·qualified identity·`manual_flow` control은 각각 `1`이었습니다.
- group 경계: configured group과 constructed supervisor는 각각 `1`이지만 armed count는 `0`, group
  control·resume command surface는 각각 `0`이었습니다. 시작과 fresh cycle은 explicit group
  command 전에는 plan을 submit하지 않는 current source 계약을 유지했습니다.
- 사용한 명령·주요 파라미터: current private input에서 rederived한 reviewed control Compose를
  `observer stop -> running instance 0 확인 -> control up --no-build` 순서로 적용했습니다. runtime은
  `mode=control`, `dry_run=false`, explicit `--write-enabled`이고 device command는 publish하지
  않았습니다. activation·broker·discovery·journal 검증 helper는 출력에서 private 주소·identity·경로를
  제외했습니다.
- 종료 지점(stage / failure code): `control_started` 뒤 두 번의 runtime/journal postcheck와 broker·HA
  discovery postcheck가 모두 PASS했습니다. 최종 상태는 control instance `1`, running, restart `0`,
  old daemon `0`, unfinished journal `0`입니다. service는 이 control 상태로 계속 실행 중입니다.
- startup device-write 확인: observer와 control의 overlap은 `0`이었습니다. control 시작 전후와 약
  5분 뒤 journal inventory는 operations `4`, blobs `11`, tree SHA-256
  `4ef050400332d41f0850ab6ffa87de1c37ca15c744a3be6fb4e4807630a22bf4`로 byte 동일했습니다.
  current local-control은 모든 control frame보다 먼저 `FORWARD_ATTEMPTING` journal을 durable
  저장하므로 이 경로의 startup device write는 `0`으로 판정합니다. 이 추론을 frozen/native 또는
  다른 write 하네스에 일반화하지 않습니다.
- 장비·broker가 돌려준 값:
  - 증거 등급: journal operations/blob inventory와 tree digest를 여러 경계에서 fresh process로
    다시 읽은 범위는 **(b) preserved structured/durable daemon artifact**입니다. broker retained
    state·discovery와 fresh device-state 결과는 **(c) reconstructed operator observation**입니다.
    원본 device frame이나 broker frame을 보존하지 않았으므로 (a)로 올리지 않습니다.
  - opaque artifact id: control Compose generation `a464c646fd6a786ff6262f9d5408af2a`.
  - UTC span: control start `2026-09-11T05:58:44.327763Z`, final postcheck
    `2026-09-11T06:03:50.311642Z`. observer stop-first는 이 start 직전에 수행됐습니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private
    physical identity 원문이 아닌 `qualification-target:` domain-separated binding이며, 앞선
    first-write·qualification success·MQTT control 기록과 동일한 물리 개체입니다. manifest 전체의
    stable role-binding digest는 위 source·deployment 결속에 별도로 기록했습니다.
  - artifact digest (SHA-256): 위 journal tree
    `4ef050400332d41f0850ab6ffa87de1c37ca15c744a3be6fb4e4807630a22bf4`.
  - broker postcheck: global online, device `6/6` online, fresh cycle count `>=1`, qualified target Flow
    `40`, device command topic `1`, retained command `0`, group armed `0`이었습니다.
  - retained HA discovery postcheck: platform `number`인 control `1`, projected payload와 broker
    payload exact, `optimistic=false`, command topic 존재, 범위 `30..75`, step `1`이었습니다.
- HA 범위 구분: 위 `30..75`는 current config·encoder가 선언하고 MQTT parser가 수용하는 범위입니다.
  실제 장비 write로 확인한 범위는 이 target의 `39..40`뿐이며, 이번 run이 나머지 값의 firmware
  acceptance나 물리 안전성을 증명하지 않습니다. 사용자는 이 상시 UI control을 사용하지 않기로
  했지만 이는 행위적 통제이며, HA 접근자가 command를 보내지 못하게 하는 기술적 차단은 없습니다.
- 원복 검증 결과: 이 operation은 장비 상태를 바꾸지 않았으므로 device restore frame은 없었습니다.
  시작 전후 target Flow `40`과 장비 `6/6` fresh가 유지됐고 journal tree도 불변입니다. deployment
  rollback용 reviewed observer Compose는 보존했지만 실제 rollback은 실행하지 않았습니다.
- 실행 준비 중 operator wrapper 정정: 첫 rederive용 network-none extractor는 writable temporary
  filesystem이 없어 private input read와 running-service 변경 전에 fail-closed했습니다. explicit
  temporary filesystem을 준 재실행이 PASS했고, 그동안 observer는 계속 실행됐으며 device·broker
  접촉은 `0`이었습니다.
- **이 실행으로 새로 확정된 사실:** reviewed `jebao-flow-local`은 qualified Pro 한 대의
  `manual_flow` 하나만 persistent command surface로 노출하면서 startup·안정화 구간에 device write를
  만들지 않고, 장비 `6/6` fresh와 unfinished journal `0`을 유지할 수 있습니다. HA Core를 변경하지
  않아도 retained MQTT discovery가 write 가능한 non-optimistic `number` 하나를 상시 생성합니다.
- 이 실행으로 확정하지 못한 것: 선언 범위 전체의 device acceptance, 다른 target·function,
  software group actuation, service 또는 broker reconnect 실기, 장시간 안정성, failure 뒤 actual
  recovery frame 송신, Q1·Q2·native ASYNC capability는 증명하지 않습니다. current daemon은
  unfinished transaction을 자동 recovery하지 않으며 operator CLI reconciliation이 필요합니다.
  `restore_on_reconnect` 설정은 current source에서 사용되지 않으므로 reconnect restore를 제공한다고
  주장하지 않습니다. host-local single instance만 확인했으며 cross-host `topic_prefix` 유일성을
  증명하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 먼저 actual recovery write를 bounded 실기로
  검증하고, 그 뒤에만 device·function qualification을 확대합니다. persistent 운영 중 unfinished
  journal을 감시하고 자동 forward retry 없이 reviewed `recover` 절차로 전환하며, 같은
  `topic_prefix`의 다른 authority `0`을 operator invariant로 유지합니다. reconnect 설정의 inert
  의미를 문서 또는 config에서 정리하고, pre-namespace retained discovery inventory·owned tombstone·
  rollback 절차를 별도 slice로 완결합니다.
