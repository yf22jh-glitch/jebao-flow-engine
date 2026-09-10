# run: 2026-09-10-local-observer-active-template-binding

- 목적(한 문장): D1g가 positive evidence로 특정한 Local Wavemaker Pro template bytes 하나를
  current manifest에 결속하고 기존 observer를 device-write 없이 교체했습니다.
- 이 실행이 답하려던 질문: cache 최신성 추측 없이 특정한 active template SHA-256을 기존 install
  pipeline이 exact 단일 content binding으로 받아들이고, device identity·role이나 observer-only
  Home Assistant 표면을 바꾸지 않은 채 재배포할 수 있는가.
- 실행한 commit SHA: source는 `79b11d492f831c73375448e8c88a725985bb20b2`와 byte 동일하고,
  governing D1g 정책은 `f9135bb1ce0365625ad796e910f33765feade824`입니다. D1g 이후 source
  변경은 없습니다.
- 사용한 명령·주요 파라미터: D1g에 기록한 app `3.4.18` / build `341800000`의 exact loader
  evidence에서 선택된 182,637-byte `index.js`를 private durable template root에 한 파일로
  보존했습니다. file SHA-256은
  `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`이고
  같은 root의 `index.html`은 없었습니다. private root와 파일은 각각 mode `0755` / `0444`,
  link count 1이고 file·directory·private parent fsync 뒤 사용했습니다. 이 template은 container가
  읽어야 하는 비밀이 아닌 vendor content라 read-only `0755` / `0444`를 쓰고, private current config는
  owner-only `0600`을 요구하는 서로 다른 계층입니다. 원문 product key, template URL과 private 경로는
  출력하거나 기록하지 않았습니다.
- 사용한 명령·주요 파라미터(계속): current image를 `--network none`, host current uid/gid로
  실행해 `prepare-compose`를 재유도했습니다. 첫 시도는 runtime stage용 `0444` config 사본을
  current input으로 잘못 준 탓에 owner-only `0600` 입력 검사에서
  `current_operation_failed_closed`로, Compose 교체·service stop 전에 종료됐습니다. 같은 current
  config bytes를 별도 owner-only `0600`, link count 1 파일로 O_EXCL 생성하고 file·parent fsync한
  뒤 다시 실행해 새 input generation을 만들었습니다. `docker compose config --quiet`와
  network-disabled stage를 통과한 뒤 기존 observer를 완전히 정지하고 host-local local/old daemon
  0개를 확인한 다음 새 observer 한 instance만 시작했습니다. 첫 실패 시도가 남긴 이전 generation
  하나는 두 번째 재유도의 정상 pruning으로 제거됐고 fresh generation 하나만 사용했습니다.
- 종료 지점(stage / failure code): fresh manifest의 `template_count=1`과 위 digest exact 단일원소,
  role binding 불변, Compose exact read-only template bind, `runtime.mode=observer`, `dry_run=true`,
  `--write-enabled` 없음, qualification input 없음이 모두 PASS였습니다. runtime은 restart 0,
  fatal·traceback·`current_operation_failed_closed` log 0으로 계속 실행 중입니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. 원본 MQTT·device frame이나 daemon의
    durable state artifact를 보존하지 않았으므로 (a)·(b)로 올리지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: n/a (보존된 raw·daemon artifact 없음)
  - UTC span (시작 / 종료): `2026-09-10T07:38:06.609446697Z` /
    `2026-09-10T07:43:03.498710914Z`. 이는 stop-first live replacement부터 최종 확인까지의
    구간이며 그 안의 read-only MQTT 관측 창은 18초였습니다.
  - SHA-256 identity binding:
    `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`.
    이 값은 template root를 입력으로 삼지 않는
    `stable_role_binding_sha256(current install manifest)`이고 갱신 전후 byte 동일했습니다.
  - artifact digest (SHA-256): n/a
  - broker의 current-namespace native discovery config는 86개, `command_topic`은 0개,
    pre-namespace discovery config는 0개였습니다.
  - configured device 6개 모두 online availability와 fresh `observed_at` state를 발행했습니다.
    `current_endpoint_absent`, `current_endpoint_ambiguous`와 다른 unavailable event는 각각 0개였습니다.
  - Home Assistant registry에는 current native MQTT entity 86개, legacy local entity 0개,
    Jebao custom-component config entry 0개가 확인됐습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 장비 command·write와 control frame은 0회라
  장비 원복 대상은 없습니다. 기존 observer를 먼저 정지해 runtime 중첩도 0이었고, 새 observer는
  exactly one instance이며 old `jebao-flowd`는 0개입니다. private prior config는 current bytes의
  별도 owner-only 사본으로 보존됐지만 이번 PASS에서 rollback은 실행하지 않았으므로 실제 rollback
  실행을 검증했다고 주장하지 않습니다.
- **이 실행으로 새로 확정된 사실:** current app loader에서 positive evidence로 선택한 exact Pro
  template bytes를 기존 gate를 완화하지 않고 fresh manifest에 단일 결속할 수 있습니다. 이 결속은
  stable device identity·role mapping과 observer-only MQTT/HA 표면을 바꾸지 않았고, 6/6 fresh
  read-only 상태 발행도 유지했습니다. 따라서 qualification의 `template_count=0` blocker는 이
  exact app/product scope에서 해소됐습니다.
- 이 실행으로 확정하지 못한 것: 다른 product family·app build의 active template, 같은 URL의
  향후 remote bytes, firmware acceptance, qualification producer, 첫 code-driven write, Q1·Q2는
  확인하지 못했습니다. 이 결과를 control qualification이나 hardware capability PASS로 승격하지
  않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: app bundle·persisted product identity·compiled
  config URL이나 local template bytes가 바뀌면 D1g의 loader 증거를 다시 결속하고 manifest를
  재유도합니다. 다음 source slice는 기존 verifier와 catalog gate를 수정하지 않는 fail-closed
  qualification producer이고, 그 offline 구현·검토가 끝난 뒤에만 앱 preconditioning과 최초
  세 write를 한 번의 구체 operation으로 묻습니다.
