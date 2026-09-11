# run: 2026-09-11-local-manual-flow-qualification-success

- 목적(한 문장): sequence가 확인된 `SERIAL_CONTROL_RESPONSE`의 empty body를 유효한 ACK
  evidence로 보존하는 reviewed producer가 선택한 Local Wavemaker Pro 한 대에서
  `40 -> 39 -> 40`을 exact readback과 함께 완결하고 qualification receipt 두 장을 만들 수 있는지
  확인했습니다.
- 이 실행이 답하려던 질문: same-value transport reachability, 1%p 하향 변화와 planned restore가
  strict identity·first-write envelope·단일 durable operation 아래에서 모두 검증되는가.
- 실행한 commit SHA: `7ecabbc2422ea5b3875dbed279cb354fa8e9f30c`
- source 결속: remote one-shot image의 Python file count는 `131`, local commit과 remote image에서
  독립 계산한 path+content manifest SHA-256은 각각
  `a067ae33b5f69e137a0745da7871379431d011391e8ed6209cd29b76580bb6fb`로 일치했습니다. image id의
  SHA-256은 `c92a2478093f3184fe53953b93efa346bf0b6334b4284aabf56ffccf440fc456`이었습니다.
- 판단 근거: source 변경은 이전 실행의 직접 원인을 empty body로 단정해서 한 것이 아닙니다.
  `send_raw_control()`이 expected response command, 최소 4-byte sequence와 exact request sequence를
  먼저 검증하고 그 뒤의 body만 반환하는데 producer만 그 body의 truthiness를 다시 gate한 **static
  unsoundness**를 제거한 것입니다. timeout, wrong command, truncated or mismatched sequence와 exact
  fresh-readback mismatch는 계속 fail-closed입니다.
- 실행 전 조건: observer instance 1개, old `jebao-flowd` 0개, unfinished local-control journal 0개,
  D1i terminal tombstone idle, current manifest template count 1과 reviewed digest
  `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5` exact, stable
  role-binding digest `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`
  exact, 선택 target의 ON·`constant`·TimerOFF·`independent`·Flow `40`을 확인했습니다. 다른
  장비·기능은 선택하지 않았습니다.
- 실행 전 정정: 첫 read-only one-shot preflight는 환경변수의 template-root 집합이 current manifest와
  다르게 해석되어 observer stop과 write 전에 종료됐고, mount된 reviewed 단일 root를 명시했습니다.
  다음 read-only 확인에서 빈 qualification-output 환경값을 `Path('.')`로 해석한 assertion도 observer
  stop과 write 전에 종료됐습니다.
- 실행 전 정정(계속): observer를 stop-first한 뒤 첫 wrapper는 위 빈 경로에 `with_name()`을 적용해
  qualification CLI·journal·device frame 전에 종료됐습니다. durable no-clobber output을 명시한 다음
  시도는 manifest generation을 Compose runtime generation으로 잘못 넘겨 descriptor 검사에서 journal과
  device frame 전에 종료됐습니다. 두 경우 모두 별도 fresh read로 Flow `40`, unfinished journal 0을
  확인했고 observer는 경쟁 방지를 위해 정지 상태로 유지했습니다. 최종 시도는 running observer의
  actual Compose runtime generation을 사용했습니다.
- 실행한 operation: opaque id `LCQ-af18e58a7d2a789c26df46a9c4475f06`, `downward_step=1`, explicit
  write opt-in, one target·one `manual_flow` function. observer가 완전히 정지한 상태에서 같은
  operation journal의 `same_value`, `lower_power`, `planned_restore` 세 action을 실행했습니다.
- UTC span: durable journal created/updated는 `2026-09-11T02:45:07.364910Z` /
  `2026-09-11T02:45:24.612648Z`였습니다.
- 장비와 daemon이 만든 결과:
  - 증거 등급: journal·referenced ACK blobs·qualification binding·receipt 두 장을 fresh process에서
    다시 읽어 digest와 verifier 결과를 확인했으므로 **(b) preserved structured/durable daemon
    artifact**입니다. 원본 request/response frame은 보존하지 않았으므로 (a)로 올리지 않습니다.
    종료 후 target exact state와 observer 6/6은 **(c) reconstructed operator observation**입니다.
  - opaque artifact id 또는 안전한 상대 논리 경로:
    `LCQ-af18e58a7d2a789c26df46a9c4475f06`.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. 이는 private physical
    identity key 원문이 아니라 producer와 같은 `qualification-target:` domain-separated SHA-256
    결속입니다.
  - transaction phase `verified`, revision `10`, action phase는 순서대로
    `verified / verified / verified`, physical write count `3`, recovery 실행 `0`이었습니다.
  - 세 response body는 모두 길이 `0`이고 SHA-256
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`로 보존됐습니다. 각 body는
    matching response command와 exact request sequence를 protocol session이 먼저 확인한 뒤 반환한
    값입니다. 따라서 이번 실행은 empty body가 이 target의 세 control response에서 정상 ACK
    evidence였다는 positive evidence를 새로 남겼습니다.
  - same-value의 exact baseline readback, lower-power의 exact `39` readback, planned-restore의 exact
    `40` readback이 각각 확인되어 세 action이 terminal `verified`가 됐습니다.
  - terminal journal SHA-256:
    `c87366c875c6423520b13e9d5845ed03b4f1498e6eea2c5c02092fec534fa590`.
  - receipt count `2`; receipt SHA-256은
    `936ff162b9cf597eb27c517bf29fb37b530df9e26a11460074982fdd9af19639`,
    `5dd388c31e09a1d312e10b36ccbde4ce4c6ecb193093f11e716874f729ccfbcc`였습니다. unchanged verifier가
    두 receipt를 같은 physical identity의 distinct step·restore request로 다시 검증했습니다.
  - qualification binding SHA-256:
    `5ff6f5f0acf14c95d68eab92796b4b3e83d940613cb54fe2c57c2c6a088965ee`. 파일은 current owner,
    mode `0600`, link count 1로 다시 확인했습니다.
- 원복과 observer 결과: 별도 recovery frame은 필요하지 않았고 planned restore action 자체가 exact
  `40` readback으로 verified됐습니다. 종료 뒤 선택 target은 ON, `constant`, TimerOFF,
  `independent`, Flow `40`이었습니다. Compose dependency stage는 이미 pruning된 generation path 때문에
  observer start를 거부했으나 장비 operation은 이미 terminal verified·40%였습니다. 기존 observer
  container를 직접 시작했고, 그 뒤 instance 1개, runtime observer, `--write-enabled` 없음, old daemon
  0개, configured·matched·fresh device 6개, absent·ambiguous 0개와 stable role-binding 불변을
  확인했습니다. 첫 post-check의 잘못된 diagnostic field 이름은 read-only one-shot에서만 실패했고,
  올바른 field로 다시 확인했습니다.
- **이 실행으로 새로 확정된 사실:** 선택 Pro의 `manual_flow`는 current encoder와 strict binding 아래
  `40 -> 39 -> 40`을 적용하고 exact readback할 수 있습니다. sequence-confirmed empty response body를
  raw ACK evidence로 보존하면 producer는 write 3회와 receipt 2장을 완결하고 기존 verifier가 해당
  physical identity를 qualification 대상으로 인정합니다.
- 이 실행으로 확정하지 못한 것: 이 결과는 다른 product·function·firmware, regular control mode,
  장기 운용, Q1·Q2 또는 native ASYNC capability를 증명하지 않습니다. Home Assistant command surface도
  활성화하지 않았습니다.
- 다음에 필요한 것: 현재 observer-only 배포는 그대로 유지합니다. regular control activation이나
  Home Assistant command entity 노출은 이 qualification artifact를 current deployment input으로
  명시적으로 결속하고 별도 승인·stop-first 절차를 거치는 후속 operation입니다.
