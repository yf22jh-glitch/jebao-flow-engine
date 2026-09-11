# run: 2026-09-11-local-control-mqtt-manual-flow

- 목적(한 문장): qualification이 결속된 Local Wavemaker Pro 한 대의 `manual_flow`만 current
  local-control MQTT command surface에 열고 `40 -> 39 -> 40`을 각각 exact readback과 durable
  journal로 검증한 뒤 reviewed observer-only 배포로 복귀했습니다.
- 이 실행이 답하려던 질문: 이미 qualification된 physical identity·function 쌍 하나가 MQTT
  command 경유 두 scalar write를 정확히 적용하며, control service의 시작·종료와 observer 복귀가
  추가 device write 없이 완결되는가.
- 실행한 source commit SHA: `7ecabbc2422ea5b3875dbed279cb354fa8e9f30c`.
  실행 준비 시 저장소 HEAD `533a3e03cf255cb97eecf0a72242e0c9d065f506`까지의 후속 변경은
  문서뿐이었고 `src/`는 위 source commit과 동일했습니다.
- source 결속: 실행 image와 local reviewed source의 installed Python file count는 각각 `131`,
  같은 path+content canonicalization으로 계산한 SHA-256은 각각
  `7c768e57d682a714563c6de6a5458e6bd60791e36b4c9b6dab382126a5c72970`로 exact
  일치했습니다.
- 실행 전 조건: observer instance `1`, old `jebao-flowd` `0`, unfinished local-control journal
  `0`, legacy write workflow idle, configured·matched·fresh device `6/6`, absent·ambiguous `0`을
  fresh process에서 확인했습니다. 선택 target은 ON·`constant`·TimerOFF·`independent`·Flow
  `40`이었습니다.
- content·identity 조건: manifest template count `1`과 active template SHA-256
  `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`, stable
  role-binding SHA-256
  `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`, qualification
  binding SHA-256
  `5ff6f5f0acf14c95d68eab92796b4b3e83d940613cb54fe2c57c2c6a088965ee`를 exact
  확인했습니다. qualified target과 `manual_flow` control은 각각 `1`, write 허용 target도
  `1`이었습니다.
- group 경계: configured group과 supervisor는 각각 `1`이었지만 supervisor armed count는 `0`,
  group control·resume entity는 각각 `0`이었습니다. 시작 전후 group command surface가 없고
  local-control journal tree가 불변임을 함께 확인했습니다. group runtime의 종료는 이미 accepted된
  mandatory OFF만 drain하고 shutdown에서 새 plan을 만들지 않으며, 이번 armed count `0`에서는 그
  drain 조건도 성립하지 않았습니다.
- 배포 순서: observer를 먼저 완전히 정지해 중첩을 `0`으로 만든 뒤, 동일 data volume과 동일
  hardware-safety lock volume을 쓰는 reviewed control Compose instance 하나를 시작했습니다.
  runtime은 `mode=control`, `dry_run=false`, explicit `--write-enabled`, write 허용 target `1`이었고
  instance `1`, restart `0`, old daemon `0`이었습니다.
- startup device-write 확인: control 시작 전후 journal은 operations `2`, blobs `9`, tree
  SHA-256 `075391dbf71407ef26c5fc173175673ebe88f26f58bcdf0ff90d4eef9f12f631`로
  byte 동일했습니다. broker read-only 확인에서는 global online, device `6/6` online, target
  Flow `40`, command topic `1`, retained command `0`, group armed `0`이었습니다. 이 write `0` 판정은
  current local-control command 경로가 모든 control frame 전에 `FORWARD_ATTEMPTING` journal을
  durable 저장한다는 코드 계약에 한정하며, 다른 write 하네스에 일반화하지 않습니다.
- 실행한 명령·주요 파라미터:
  1. MQTT request `mqtt_f0779728da1eb10df54ce915c0dc2731`, `manual_flow=39`.
     baseline `40`, 실패 시 authorized recovery 기대값 `40`.
  2. MQTT request `mqtt_e02a5b5c96983040b7701ee4cb09d4bc`, `manual_flow=40`.
     baseline `39`, 실패 시 authorized recovery 기대값 `39`; 계획 밖의 추가 forward write는
     허용하지 않았습니다.
- 종료 지점(stage / failure code): 두 request 모두 transaction·action `verified`, revision `4`로
  종료했습니다. 각 request physical write count는 `1`, recovery 실행은 `0`, 최종 unfinished
  journal count는 `0`이었습니다.
- 장비와 daemon이 만든 결과:
  - 증거 등급: 두 terminal journal과 referenced baseline·expected·ACK blobs를 fresh process에서
    다시 읽고 digest를 확인했고, control 시작 전후와 종료·observer 복귀 전후의 durable journal
    tree count·digest도 각 경계에서 다시 확인했으므로 **(b) preserved structured/durable daemon
    artifact**입니다. 원본 request/response frame은 보존하지 않았으므로 (a)로 올리지 않습니다.
    독립 fresh read와 최종 observer·broker 상태는 **(c) reconstructed operator observation**입니다.
  - opaque artifact id: 위 두 MQTT request id.
  - UTC span: 첫 journal created `2026-09-11T04:56:24.035031Z`, 둘째 journal updated
    `2026-09-11T04:58:02.726501Z`. 이는 두 device command의 durable journal 구간입니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private
    physical identity 원문이 아니라 qualification producer와 같은 `qualification-target:`
    domain-separated 결속이며, qualification run의 값과 동일합니다.
  - 첫 journal SHA-256:
    `db33236e910c579601ff6c1cb1dc341144d37299c66ec551182f7cfff7bd11c6`.
    baseline·expected·fresh Flow는 `40 / 39 / 39`였습니다.
  - 둘째 journal SHA-256:
    `f1a95ed15319b9bd031b45fad8b6719eb8a9337601d6812515e5d6baac56b7cc`.
    baseline·expected·fresh Flow는 `39 / 40 / 40`이었습니다.
  - 두 journal 결속 SHA-256:
    `c8d5c27be22e8d4edd558a78581969b9b9759c2a57faf9bbd063eb0fbf86c0ca`.
    preimage는 request 순서대로 `request_id`와 `journal_sha256`만 담은 JSON array를
    `sort_keys=True`, compact separators, ASCII, trailing newline 없이 직렬화한 bytes입니다.
  - 두 ACK body는 모두 길이 `0`, SHA-256
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`였습니다.
    protocol session이 matching response command와 exact request sequence를 먼저 확인한 뒤 반환한
    body이며, action 성공은 ACK뿐 아니라 각 expected Flow의 independent fresh exact readback까지
    함께 만족해야 했습니다.
- 원복·observer 검증: 둘째 command 자체가 Flow `40`으로 계획된 복귀였고 recovery frame은
  실행하지 않았습니다. control 종료 전 Flow `40`과 device `6/6` online을 다시 확인했습니다.
  control 종료와 reviewed observer-only 시작 전후 journal tree는 operations `4`, blobs `11`,
  SHA-256 `36c05d0abb4253f2f95af2111856e96b7afd5ea3713c6bb339a732056f2e5050`로
  byte 동일했습니다. 최종 runtime은 observer instance `1`, restart `0`, old daemon `0`,
  `dry_run=true`, write flag·qualification input·write 허용 target `0`이었습니다.
- 최종 상태: observer broker read-only 확인은 global online, device `6/6` online·fresh, control
  entity·command topic·group control·group resume 각각 `0`이었습니다. 별도 strict fresh read는
  `6/6`, absent·ambiguous `0`, 선택 target ON·TimerOFF·`constant`·`independent`·Flow `40`,
  unfinished journal `0`, template·role·qualification binding exact를 확인했습니다.
- 실행 중 operator wrapper 정정:
  - live read·observer stop·write 전에 undefined 값, service별 image 선택, Compose JSON image key,
    schema import, projection field 가정을 차례로 바로잡고 고정된 read-only preflight를 사용했습니다.
  - control 시작 뒤 과거 package digest 상수의 canonicalization 차이를 발견했고, 같은 알고리즘으로
    local/runtime `131` files를 다시 계산해 exact 일치를 확인했습니다. 이때 command는 아직 없고
    journal tree도 불변이었습니다.
  - 첫 command 뒤 fresh verifier가 hardware-safety volume identity를 활성화하지 않은 채 시작해
    journal·network read 전에 종료됐고, 공식 loader를 사용해 같은 terminal artifact를 다시
    확인했습니다. 추가 write는 없었습니다.
  - observer 복귀 시 current working directory만 검색한 선택과 MQTT secret 존재 가정을 버리고,
    common root에서 operational input·volume·role binding을 의미 비교했습니다. 두 candidate가
    operationally exact였고 최신 attestation의 reviewed observer generation을 선택했습니다.
- **이 실행으로 새로 확정된 사실:** current local-control의 MQTT 경로는 qualification이 결속된
  Pro 한 대의 `manual_flow` 하나에 대해 `40 -> 39 -> 40` 두 command를 각 한 번만 전송하고,
  sequence-confirmed ACK와 independent fresh exact readback을 거쳐 각각 terminal `verified`로
  완결할 수 있습니다. control service 시작·종료와 observer 복귀는 journal tree 불변으로 별도
  device write를 만들지 않았습니다.
- 이 실행으로 확정하지 못한 것: 다른 device·product·function, group actuation, 장기 control
  운용, Home Assistant command entity·UI, Q1·Q2, native ASYNC capability 또는 firmware 일반
  동작을 증명하지 않습니다. failure/crash 뒤 coordinator가 실제 recovery control frame을 송신하는
  경로도 이번에는 실행하지 않았습니다. 앞선 qualification의 `planned_restore`는 forward action이고
  이번 두 request의 recovery count도 `0`이므로, 이를 recovery write의 실기 검증으로 승격하지
  않습니다. Home Assistant 업데이트·재부팅·설정 변경은 수행하지 않았습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 고정된 operator-local preflight·broker·command
  verifier를 재사용하고, service별 image 선택, 동일 package-hash 알고리즘, 공식 hardware-safety
  identity activation, common-root 기반 semantic rollback selection을 유지해야 합니다. 현재 배포는
  observer-only로 유지하며, 다음 Jebao-only 확장은 별도 목표와 증거 범위를 먼저 정합니다.
