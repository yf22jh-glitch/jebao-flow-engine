# run: 2026-09-11-local-qualification-terminal-tombstone-interlock

- 목적(한 문장): 선택한 Pro 한 대의 최초 manual-flow qualification을 시작하기 전에 기존
  write workflow가 모두 idle인지 확인하고, 장비 접촉 전에 발견된 exact-restore terminal
  tombstone interlock을 보존했습니다.
- 이 실행이 답하려던 질문: reviewed D1h qualification producer가 observer를 정지하거나 장비에
  frame을 보내기 전에 current manifest·journal·lease 선행조건을 모두 충족해 계획된 세 write를
  시작할 수 있는가.
- 실행한 commit SHA: `56d1f66bda456f2f403393b2e86f4e6e9697483a`
- 사용한 명령·주요 파라미터: current observer image 위에 위 commit의 exact Python source tree만
  올린 one-shot qualification image를 만들었습니다. local과 remote image의 Python file count는
  각각 131개였고 canonical tree SHA-256은
  `46df87e10d7be82261e5f12229bc5c2825bd92ece491333703132731627c06a1`로 일치했습니다. image id의
  SHA-256은 `a93ad0b80062bff68dbff20d686b28c5e33f3775aff209f478b62712e036df17`입니다. current observer
  하나가 `runtime.mode=observer`, `dry_run=true`, `--write-enabled` 없음, qualification input 없음인
  상태와 old `jebao-flowd` 0개, configured·enabled·manifest device 각 6개, unfinished local-control
  journal 0개, manifest `template_count=1`, active-template SHA-256
  `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`, role binding SHA-256
  `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`를 read-only로 확인했습니다.
- 사용한 명령·주요 파라미터(계속): legacy write-workflow idle 검사는 exact-restore journal path에
  파일이 존재한다는 이유로 false를 반환했습니다. 이를 삭제·이동·개명·재작성하거나 lease를
  우회하지 않고, one-shot image를 `--network none`으로 실행해 parked-disposition status와 archive
  backing을 다시 읽었습니다. 첫 진단 호출은 archive adapter 종류를 잘못 선택해 상태 변경 전에
  실패했고, 올바른 archive verifier로 다시 실행해 terminal finalization과 immutable journal
  snapshot의 교차검증을 통과했습니다. 두 호출 모두 device network와 device write는 0회였습니다.
- 종료 지점(stage / failure code): observer stop과 qualification CLI 실행 전의 private preflight에서
  `_legacy_write_workflows_idle() == false`를 확인해 중단했습니다. CLI를 호출해 예외를 발생시키지는
  않았습니다. qualifier의 operation journal, writer와 device session은 생성되지 않았고 planned
  same-value·하향·복원 frame은 모두 0회입니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (b) preserved structured/durable daemon artifact. owner-only exact-restore tombstone,
    version-2 parked finalization과 immutable journal snapshot을 서로 다른 fresh process에서 다시 읽어
    결속을 확인했습니다. 원본 device frame은 아니므로 장비 상태 증거인 (a)로 올리지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로:
    `ERD-26abc5aa31d509a3ddb49f497114e8fd`
  - UTC span (시작 / 종료): `2026-09-11T01:20:37.854202571Z` /
    `2026-09-11T01:20:39.633345567Z`. 이는 network-disabled terminal tombstone 재검증 구간입니다.
  - SHA-256 identity binding: n/a. 이번 preflight는 target resolution 전에 멈췄고 새 device identity를
    관측하거나 결속하지 않았습니다. 기존 operation SHA-256은
    `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`입니다.
  - artifact digest (SHA-256): disposition
    `712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`; backing journal snapshot
    `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`.
  - durable outcome은 `parked_unqualified`, finalization version은 `2`, qualification은 false,
    device-network access와 device write count는 각각 false와 0이었습니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 이 preflight에서 observer를 정지하지 않았고
  장비 frame을 보내지 않았으므로 새 원복 대상은 없습니다. 기존 exact-restore tombstone과 archive,
  backing journal snapshot은 검증 전후 그대로 보존했습니다. tombstone 삭제가 idle 판정을 통과하게
  만들더라도 그렇게 하지 않았고, 실제 replay rejection 경로도 수정하거나 실행하지 않았습니다.
- **이 실행으로 새로 확정된 사실:** 보존된 exact-restore artifact는 진행 중 journal이 아니라
  positively verified terminal `parked_unqualified` tombstone입니다. 그러나 current local-control
  lease idle 검사는 exact-restore journal path에 대해서 terminal envelope을 해석하지 않고 파일
  존재만으로 non-idle을 반환하므로, 정직하게 보존한 terminal barrier가 unrelated qualification을
  장비 I/O 전에 막습니다. tombstone 부재는 idle로 인정하는 현재 판정과도 방향이 반대입니다.
- 이 실행으로 확정하지 못한 것: 선택한 장비의 app precondition을 network readback으로 재확인하지
  않았고, qualification의 same-value 도달·하향 변화·정확 복원, receipt 발급, firmware acceptance,
  Q1·Q2는 확인하지 못했습니다. 이번 결과를 hardware capability나 control qualification으로
  승격하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: `local_control/lease.py`의 기존 hardened
  terminal-envelope 검사 패턴을 한 exact-restore journal path에만 확장해, 부재 또는 exact
  `parked_unqualified` terminal envelope만 idle로 인정하고 그 밖의 journal은 계속 fail-closed로
  막습니다. exact-restore disposition·archive·replay rejection은 수정하지 않고 tombstone도
  소비·삭제·개명·이동·재작성하지 않습니다. 이 범위를 별도 정책·source slice로 결속하고 offline
  회귀와 독립 검토를 통과한 뒤, 이미 승인된 planned qualification operation의 preflight부터 다시
  시작합니다.
