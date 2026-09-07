# run: 2026-09-07-b1-identity-resolution-stop-7746b28a

- 목적(한 문장): 두 Pro의 현재 상태를 write 없이 독립된 두 series로 보존할 수 있는지 확인하는
  B1 중, 첫 primary series의 identity-resolution 실패를 durable evidence로 남기고 승인된 stop
  rule을 적용합니다.
- 이 실행이 답하려던 질문: 두 Pro 각각의 current outer six-field와 432-byte schedule image를
  `2 series × 2 pair`의 fresh raw frame으로 보존해 exact-restore baseline 후보를 만들 수 있는가?
- 실행한 commit SHA: `c0a8be68e62cf88affb3a918aa0529f8da3fc698`
- collector runtime source digest SHA-256:
  `9402a1ac9bb170fecdada0a0ea487286ba4e545db74ca8d76a483492b54e165a`
- 승인과 현장 전제:
  - repository maintainer는 cadence `30초`, timeout `5초`, retry `0`, `explicit_only`, primary와
    corroborating 각각 predeclared pair `2`개, device/control write `0`, owner-only primary와 별도
    private backup이라는 exact B1 packet에 직접 `ㅇㅇㅇ 진행해`라고 답했습니다. 승인 범위는 짧은
    답문 자체가 아니라 그 직전 packet에 결속됩니다.
  - 같은 답문은 앱이 background·다른 폰·태블릿·client까지 완전 종료됐고 Home Assistant가 detach
    상태라는 직전 현장 확인 조건에도 결속됩니다: **(c) reconstructed operator observation**.
- 장비 접촉 전 검사:
  - clean HEAD, active Docker context `colima`, exact local named safety volume, attested image,
    owner-only 두 private config의 기록된 digest, `runtime.dry_run=true`, 모든 configured device의
    `allow_hardware_writes=false`, exact two-Pro pair와 서로 다른 identity binding을 확인했습니다.
  - `/hardware-safety` mount container, running Jebao container, established TCP `12416`, 실제 Jebao
    writer entrypoint/module은 각각 `0`이었습니다. safety volume은 `LINKS=0 | 61B`로 marker-only
    상태와 정합했습니다.
  - 처음의 광범위한 process-name 검사는 `writer_processes_zero=false`로 device contact·artifact
    생성 전에 fail-closed했습니다. 그 command가 종료된 뒤의 sanitized process inspection에서는
    match가 없었으므로 순간적인 self/command-line match와 정합하지만, 최초 match의 PID·argv를
    보존하지 않아 원인을 확정하지는 않습니다. PID ancestry를 제외하고 실제 entrypoint와
    `python -m` module만 판정하는 검사로 다시 확인했을 때 모든 preflight가 통과했습니다. 첫 실패는
    B1 evidence로 사용하지 않습니다: **(c) reconstructed operator observation**.
- 사용한 명령·주요 파라미터:
  - exact Python `3.12`에서 committed source module
    `python -B -P -m jebao_flow.read_only_collector_cli`를 실행했습니다.
  - private config와 output root는 owner-only durable 경로에서만 읽고 썼으며 절대경로는 기록하지
    않습니다.
  - logical pair `flow_pro_d7a8`, `flow_pro_d0a4`; `--samples 2`;
    `--cadence-seconds 30`; `--timeout 5`; `--transport-policy explicit_only`.
- 종료 지점(stage / failure code):
  - primary series는 예정된 ordinal `0`, `1`을 한 번씩 마치고
    `pilot_completed_with_rejected_or_failed_acquisitions`, process exit code `1`로 종료됐습니다.
  - 두 ordinal의 role `a`, `b`, 합계 네 sample 모두 `failure_phase=identity_before`,
    `failure_class=CollectorError`, `failure_code=identity_not_exactly_resolved`였습니다.
  - accepted pair `0`, predicate-rejected pair `0`, read-failure pair `2`입니다.
  - 승인된 stop rule에 따라 corroborating series, 자동 retry, B2 이후 단계는 실행하지 않았습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급 **(a) preserved raw artifact: 없음**. exact endpoint가 성립하기 전에 중단돼 raw frame,
    decoded state, authenticated TCP state read가 모두 `0`입니다. 따라서 장비 state 값은 주장하지
    않습니다.
  - attested source와 네 durable sample을 함께 대조하면 collector-level identity-before discovery
    invocation은 `4`, identity-after discovery와 authenticated state-read invocation은 각각 `0`,
    device/control write는 `0`입니다. wire-level 응답 수는 보존되지 않아 주장하지 않습니다.
  - 네 sample의 durable manifest는 identity-before 단계가 실행됐지만 observed binding과 endpoint
    token이 모두 부재했음을 기록합니다. 이것은 zero response, duplicate response, 다른 binding 등
    세부 원인 중 무엇인지는 구분하지 않습니다.
- 보존된 구조화 산출물:
  - 증거 등급: **(b) preserved structured/durable collector artifact**. 실제 파일을 reload하고
    `PilotSeriesStore.verify_completed_series`로 검증했습니다.
  - primary artifact root: `JBA-ab042fe801abf0f4c6317238`
  - 별도 private backup: `JBB-57f0c82aedaab1d8b916c25c`
  - plan artifact: `JFP-7746b28ae2b41790b08efbb82e1c0a3a`
  - series: `JFS-0de42ce9d3d7636c9ecc95b1fd1e59ea`
  - UTC span: `2026-09-07T07:38:22.472959Z` / `2026-09-07T07:39:02.489391Z`
  - SHA-256 identity bindings:
    - `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
    - `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
  - plan digest: `89f3db0cc420e58bf7a5a2a97eb85c3976bd5eddcded5f661523c30f84c0fc02`
  - series digest: `4b07bff88f05672583e9e1b94663151ce77a13a556ecbffe1aa3d5bfc6db8d8f`
  - canonical tree digest:
    `fd4e75362b9f80f36bf48adf5cd0dade3594f85165755b80a4294dbc990f2a81`
  - primary와 backup은 file `13`, 하위 directory `8`, file bytes 합계 `14514`, owner-only metadata로
    byte-exact match했습니다. tree digest는 상대경로 순서로 directory의 type·mode와 file의
    type·mode·size·content SHA-256을 결속합니다.
- 종료 후 host 상태:
  - `/hardware-safety` mount container, running Jebao container, established TCP `12416`은 다시 각각
    `0`이었습니다.
  - Docker volume set은 `54`개, SHA-256
    `9575efb771b1f781b8a9ac652e2b4a7a497cefd17b97af6bf680e3f1b14e0d0b`로 A 직후와 같고 safety
    volume도 `LINKS=0 | 61B`였습니다: **(c) reconstructed operator observation**.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): device/control write가 `0`이므로 원복 operation은
  없었습니다. 다만 current baseline raw를 하나도 얻지 못했으므로 exact-restore backup을
  확보했다고 주장하지 않습니다.
- **이 실행으로 새로 확정된 사실:** 승인된 primary의 두 ordinal 모두에서 두 logical role의 exact
  identity resolution이 identity-before 단계에서 실패했고, 수집기는 retry 없이 네 실패 sample과 두
  pair record를 durable하게 보존한 뒤 종료했습니다. B1 stop rule이 작동해 두 번째 series와 이후
  단계는 실행되지 않았습니다.
- 이 실행으로 확정하지 못한 것: 현재 두 장비의 state, 두 series 사이 byte equality, exact-restore
  baseline, identity-resolution 실패의 네트워크·전원·binding 세부 원인, Q2. Q2는 `UNKNOWN`입니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 보존된 artifact와 attested source만으로 원인을
  먼저 오프라인 분석합니다. 추가 device discovery나 새 B1은 자동 retry가 아니며, 분석·보고와 새
  exact B1 승인이 있기 전에는 실행하지 않습니다.

## 2026-09-07 정정 — local runner에 한정된 identity-resolution 결과

위 B1은 macOS local runner와 Docker context `colima`를 기준으로 실행됐습니다. 후속 offline 분석에서
과거 성공 collector의 보존 실행 기록을 다시 추적한 결과, 해당 실행은 별도 remote Linux deployment
host에서 SSH와 `systemd-run`으로 수행됐습니다. 따라서 위 실행 경로는 과거 sanctioned deployment
runner와 같지 않았습니다: **(c) reconstructed operator observation**.

위 primary는 승인된 실제 UDP device contact였고 durable plan·series·pair·sample artifact와 별도
private backup의 bytes·digest·owner-only metadata도 그대로 유효합니다. 다만 네 sample의
`identity_not_exactly_resolved`는 **local runner와 그 network path에서 exact identity를 resolve하지
못했다는 사실에만** 결속됩니다. 이 결과로 actual deployment path에서도 장비가 offline이거나
identity binding이 잘못됐다고 추론하지 않습니다.

같은 분석에서 actual remote deployment의 별도 Docker daemon과 기존 safety volume이 확인됐습니다.
그 root와 2026-09-04 parked barrier의 byte-exact backup·검증은
[`2026-09-04 run의 2026-09-07 정정`](2026-09-04-exact-restore-qualification-4dcd8364.md)에
기록합니다. local `colima`의 marker-only root를 actual deployment root의 대리물로 사용하지 않습니다.

이 정정은 실패한 primary를 재시도하거나 corroborating series를 여는 승인이 아닙니다. 추가 device
contact는 actual deployment topology에서 새 A를 처음부터 통과하고 새 exact B1 승인을 받은 뒤에만
가능합니다. Q2는 계속 `UNKNOWN`입니다.
