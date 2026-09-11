# run: 2026-09-11-local-control-external-app-drift

- 목적(한 문장): qualified Pro 한 대의 Flow를 on-site Jebao app에서 `40 -> 41 -> 40`으로
  변경하고, persistent local-control이 이 외부 drift를 old cached state로 성공 처리하지 않고
  fresh device truth와 HA/MQTT state에 반영하는지 확인했습니다.
- 이 실행이 답하려던 질문: local-control 이외의 주체가 장비 상태를 바꿔도 다음
  authenticated fresh read가 새 값을 읽고 retained HA/MQTT state를 갱신하며, 그 과정에서
  agent-side device write나 local-control journal 오염을 만들지 않는가.
- 사용자 승인 범위: Jebao 실기와 필요한 상태 변경, Flow 상한 `50` 승인을
  적용했습니다. 이 operation의 외부 변경은 사용자가 Jebao app에서 수행했고, agent는
  device command를 보내지 않았습니다. Home Assistant Core 업데이트·재기동·설정 변경은
  수행하지 않았습니다.
- 실행한 source commit SHA: persistent service는 이전 activation과 같은
  `7ecabbc2422ea5b3875dbed279cb354fa8e9f30c` reviewed source image를 계속 사용했습니다. 이
  run은 source·config·Compose를 변경하지 않았습니다.
- 실행 전 조건:
  - persistent control service exactly `1`, restart `1`, qualified `manual_flow` control exactly `1`,
    unfinished local-control journal `0`, receipt `2`였습니다.
  - 별도 process의 authenticated direct read는 장비 `6/6` available, selected target power ON,
    Flow `40`을 반환했습니다.
  - journal은 operations `5`·blobs `12`·files `17`, qualification directory는 files `4`·receipt
    `2`였습니다.
- 사용한 명령·주요 파라미터:
  - agent는 running control container의 current private input을 읽는 별도 one-shot process로
    exact current manifest·qualification을 재결속했습니다. 각 관측은 fresh discovery 후 장비별
    read-only authenticated session을 새로 열어 한 번 읽고 닫았습니다.
  - 사용자가 Jebao app에서 selected target의 Flow만 `40 -> 41`로 변경한 뒤 agent가
    direct fresh read `41`과 retained MQTT state `41`을 확인했습니다.
  - 중간 관측 뒤 사용자가 같은 target을 `41 -> 40`으로 복원했고, agent가 다시
    direct fresh read와 read-only MQTT subscription으로 각각 `40`을 확인했습니다.
  - 각 경계의 durable tree digest는 file별 `(root 상대경로, SHA-256(file bytes))` pair를
    상대경로로 정렬한 뒤 compact ASCII JSON array로 직렬화해 SHA-256한 값입니다. 이
    run 내의 시작·중간·종료 동일성 판정에만 사용했습니다.
    따라서 직전 reconnect·soak 기록의 journal `33482907…`·qualification `c626ae6e…`와는
    canonicalization이 달라 digest를 직접 비교할 수 없습니다. 기록 간 연속성 근거는
    inventory count `5/12/17`·`4/2`와 unfinished `0`의 불변입니다.
- 종료 지점(stage / failure code): `external_drift_observed_and_reverted` PASS. service는
  exactly `1`, running, restart `1`, global availability `online`이고 retained command는 `0`입니다.
- 장비·broker·daemon이 만든 결과:
  - 증거 등급: 시작·중간·종료에 fresh process로 재독한 local-control journal과
    qualification file inventory·tree digest는 **(b) preserved structured/durable daemon
    artifact**입니다. direct authenticated read, user app 조작, retained MQTT 관측은
    **(c) reconstructed operator observation**입니다. raw device·app·MQTT frame을 파일로
    보존하지 않았으므로 (a)를 주장하지 않습니다.
  - opaque artifact id: control Compose generation `a464c646fd6a786ff6262f9d5408af2a`.
  - UTC span: 실행 전 baseline 확인은 `2026-09-11T08:35:32Z` 이전, 최종 direct
    authenticated read는 `2026-09-11T08:49:12.355481Z` ~ `2026-09-11T08:49:20.022965Z`,
    broker postcheck는 `2026-09-11T08:50:38Z` 이전입니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 이전 여섯 개
    local-control 실기 기록과 같은 물리 개체입니다.
  - artifact digest (SHA-256): canonical journal inventory
    `2803b6ee8361321014c449e5665fbb9772c3520bb3fbb976307ee7401788cb86`, canonical
    qualification inventory
    `36b4c6ed52475565c1bd98dd709ea13d8ef4589fd58ecbb049c38ef36f9b664d`.
  - app이 `41`을 적용한 중간 경계에서 direct fresh read와 retained state가 모두 `41`이었고,
    복원 후 두 관측이 모두 `40`이었습니다. 두 direct cycle 모두 장비 `6/6` available,
    selected target power ON을 반환했습니다.
  - 중간·종료 inventory는 journal operations `5`·blobs `12`·files `17`, qualification
    files `4`·receipt `2`, unfinished journal `0`으로 시작과 같고 두 tree digest도 byte-exact
    불변입니다. local-control command는 `FORWARD_ATTEMPTING` journal을 device frame보다
    먼저 durable 저장하므로, 이 경로의 agent-side device write는 `0`으로 판정합니다.
    이 추론을 Jebao app이나 frozen/native 하네스에 일반화하지 않습니다.
- 원복 검증 결과: selected target은 외부 drift `41`을 중간에 실제로 반환한 뒤
  Jebao app 복원으로 Flow `40`이 됐고, 별도 authenticated direct read와 retained MQTT
  state가 모두 exact `40`을 반환했습니다. agent restore frame은 `0`입니다.
- **이 실행으로 새로 확정된 사실:** current persistent local-control의 fresh poller는
  Jebao app에서 발생한 external Flow drift `40 -> 41`을 다음 fresh device truth로 읽어 retained
  state를 `41`로 갱신했고, app 복원 후에도 `40`을 갱신했습니다. 이 두 경계에서
  old `40` 또는 중간 `41`을 성공으로 재사용하지 않았고 agent-side write·journal change는
  `0`이었습니다.
- 이 실행으로 확정하지 못한 것: raw app request/ACK·device response frame, app 내부의 exact
  physical frame count, 다른 target·function·multi-field drift, HA Core restart 후 automation 동작,
  software group ownership 중 external drift, Q1·Q2·native ASYNC capability는 증명하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: external drift 실기는 agent write와
  섞지 않고 독립적으로 수행하며, 변경·복원 각각에 direct fresh read·retained state·durable
  journal tree를 함께 대조합니다. M2는 이 run record가 랜딩되면 닫고, M3는 immutable
  qualification-set merger 정책·source부터 진행합니다.
