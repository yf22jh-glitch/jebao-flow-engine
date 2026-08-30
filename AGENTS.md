# AGENTS.md — 코딩 에이전트 작업 규칙

이 파일은 사람이 아니라 코딩 에이전트(Codex, Claude Code 등)를 위한 것입니다.
`README.md`는 제품을, `PROJECT_CONTEXT.md`는 설계를, `docs/hardware-readiness.md`는 실기 안전을
다룹니다. **이 파일은 "무엇을 만들 것인가"가 아니라 "어떤 순서로 일하고 언제 멈추는가"만
정합니다.**

우선순위가 충돌하면: 물리 안전(`docs/hardware-readiness.md`) > 이 파일 > 나머지 문서.
단 **"더 안전하게 만들자"는 판단은 이 파일보다 아래입니다.**

## 역할

- **repository maintainer** — 저장소의 코드·문서 변경과 동결 해제를 승인하는 사람.
- **on-site hardware approver** — 장비에 대한 실제 write를 **승인하고, 그 operation 동안
  현장에서 감시하며, 물리 전원 차단 등 중단 수단을 쥐고 있는 사람.**

두 역할이 같은 사람일 수 있지만 **권한은 분리해서 다룹니다.** 에이전트가 스스로 승인하거나
동결을 해제할 수 없습니다.

**에이전트는 명시적으로 승인된 operation의 범위 안에서 원격 명령을 실행할 수 있습니다.**
이 문서는 기존 운용 방식(사람이 승인하고 감시하는 동안 에이전트가 원격으로 명령을 돌리는 것)을
금지하지 않습니다. 금지하는 것은 승인 없는 write, 승인 범위를 벗어난 write, 그리고 현장 감시와
중단 수단이 확보되지 않은 상태의 write입니다. 앱 조작처럼 물리적 접근이 필요한 단계는
on-site hardware approver가 수행합니다.

## 0. 이 파일이 존재하는 이유

2026-08-26~28 사이 커밋 47개(고정 역사 구간 `6bf85fc^..7dc5c59`), 네이티브 Linkage 실기
13회를 수행했지만
이 기간의 **두 기능 질문이 모두 `UNKNOWN` → `UNKNOWN`**이었습니다.

- **Q2** — `async_slave`가 자기 스케줄 슬롯의 출력(`AutoFlow`)을 독립 적용하는가.
  직접 시도 5회, 그중 측정 지점 도달 1회.
- **Q1** — `async_slave`가 마스터와 다른 manual `Flow`를 유지하는가. 직접 시도 4회.
  당시 "유지되지 않음"으로 기록됐다가 delivery/full-state read-back 미증명으로 철회됐습니다.

둘 다 미해결이며, "유일한 미해결 질문"은 Q2만 남았다는 뜻이 아니라 Q1이 현재 범위 밖으로
park됐다는 뜻입니다(`docs/hardware-readiness.md` 현재 판정 표).
같은 기간 `ScheduleLinkageRunFailure`는 0개 → 73개로 늘기만 했고, 그중 32개는 첫 Linkage
write 이전에 발동합니다.

원인은 실력이 아니라 **피드백 구조**입니다. 실기는 드물고 유인이며 실패해도 raw 산출물을
남기지 않는데, mock 기반 테스트는 무한히 만들 수 있고 항상 초록불이 됩니다. 그래서

    실기 실패 → 원인 추정 → 그 추정대로 동작하는 fake 작성 → 그에 맞는 게이트 작성
    → 테스트 통과 → 다음 실기가 새 게이트에서 종료 → 반복

이 닫힌 고리가 만들어집니다. 아래 규칙은 그 고리를 끊기 위한 것입니다.
전체 이력은 [`docs/runs/`](docs/runs/README.md)에 있습니다.

## 1. 동결 — 네이티브 ASYNC write 하네스

**다음 코드는 2026-08-28부터 동결 상태입니다.**

- `src/jebao_flow/devices/schedule_linkage.py`
- `src/jebao_flow/devices/schedule_flow_experiment.py`
- `src/jebao_flow/devices/schedule_transaction.py`
- `src/jebao_flow/devices/linkage.py`
- `src/jebao_flow/schedule_flow_experiment_cli.py`
- `src/jebao_flow/schedule_linkage_cli.py`
- 위 파일들의 단위 테스트

동결 중 **금지**: 위 파일에 대한 새 게이트, 새 실패 코드, 새 계층, 새 mock 테스트,
새 방어 로직, 이 하네스를 사용하는 새 실기 실행. **새 CLI 금지는 이 동결 대상 하네스를
구성하거나 외부에 노출하는 CLI에 한정합니다.**

동결 중 **허용**: 비-안전핵심 게이트·중복 검증의 삭제와 통합(§2), 진단 출력 추가,
`jebao-flow` 코드 write 없이 같은 질문에 답하는 경로 탐색.

**동결 대상 밖**: 장비에 write하지 않는 별도의 read-only collector 구현과 그 CLI는 동결
대상이 아닙니다. 동결 하네스의 코드를 재사용하거나 write 경로를 노출하지만 않으면 됩니다.

### 동결 대상 코드에서 P1을 발견하면

§2·§3의 채택 요건을 만족하더라도 **동결이 우선합니다.**

1. `docs/runs/` 또는 이슈에 **기록하고 보고만** 합니다.
2. 수정과 새 테스트는 (a) repository maintainer의 **명시적 긴급 예외 승인**이 있거나
   (b) 동결이 해제된 뒤에만 합니다.
3. 구체적 물리 위험(§3 마지막 문단)은 예외입니다 — 즉시 멈추고 보고하며, 필요한 조치는
   사람이 결정합니다.

"P1이니까 지금 고쳐야 한다"로 동결을 우회하지 않습니다. 지난 3일의 루프가 정확히 그
경로였습니다.

### 동결이 덮지 않는 것

동결 대상은 **`jebao-flow` 코드가 장비에 write하는 경로**입니다. on-site hardware approver가
**제바오 앱으로** 장비 설정을 바꾸는 것은 이 동결의 대상이 아니며, 별도의 승인된 live-write
operation으로 다룹니다. 절차와 안전 순서는 `docs/hardware-readiness.md`를 따릅니다.

### 해제

해제에는 두 가지가 **모두** 필요합니다.

1. repository maintainer의 명시적 승인
2. 해제 사유와 재개 조건을 본문에 적은 **별도 커밋**으로 이 절을 수정

에이전트가 스스로 해제할 수 없습니다. 다른 작업의 일부로 조용히 되살리는 것도 해제입니다.

### 2026-08-30 Q2 단회 해제

repository maintainer와 on-site hardware approver가 현재 작업에서 **Q2 실기 1회**를 명시적으로
승인했습니다. 이 승인은 `async_slave`가 자기 스케줄의 서로 다른 A/B `AutoFlow`를 실제 경계에서
적용하는지 15분 동안 관측한 뒤 원상복구하라는 반복 지시에 한정됩니다.

이 단회 해제에서 허용하는 변경과 실행은 다음뿐입니다.

- 고정 계획 `master 31% / slave 32%`에서 `master 35% / slave 40%`로 넘어가는 스케줄 1회
- 보존 raw에서 확인된 장비 시계 차이를 반영한 기존 비안전 clock-skew 게이트 완화
- 2026-08-30 저출력 재자격 실행이 자동 rollback 판정에서 실패하고 attended recovery로
  terminal 복구된 뒤에는, 이 Q2 1회에 한해 **동일 operation id와 동일 physical binding의 기존
  자격 영수증**에서 만료 시각만 무시. 영수증 부재·identity 불일치·operation 불일치는 우회 금지
- 전체 관측 시간을 900초로 연장하고, 스케줄 write 이후의 판정 불일치·일시적 read 오류를
  기록하면서 물리 안전 위반이 없는 한 관측 종료까지 계속하는 변경
- 관측 종료 뒤 `slave role detach → master role detach → TimerOFF → exact schedule images →
  original outer controls/TimerON` 순서의 복구와 그 복구에 필요한 명령
- 위 범위에 직접 대응하는 기존 단위 테스트 수정

출력 상한·identity·single-write·durable journal·rollback 권한은 완화하지 않습니다. 새 pre-write
게이트나 새 실패 계층은 추가하지 않습니다. 실제 출력이 45%를 넘거나 identity가 달라지는 등
구체적인 물리 위험만 즉시 중단 사유이며, 그 밖의 중간 오류는 조기 원복 사유로 쓰지 않습니다.

실행이 terminal 상태와 exact restore를 확인하고 `docs/runs/` 기록을 커밋하면 이 단회 해제는
자동 종료되고 위 동결이 다시 적용됩니다. 복구가 남으면 새 실험은 금지하고 해당 operation의
ordered recovery만 허용합니다.

## 2. 첫 write 이전 게이트 — 무엇을 줄이고 무엇을 지키는가

이 규칙은 **게이트를 무조건 줄이라는 뜻이 아닙니다.** 늘리기만 하던 방향을 멈추는 것이 목적이며,
안전 핵심은 그대로 둡니다.

### 삭제·완화 금지 (안전 핵심)

**이 절의 보호 규칙은 `jebao-flow` 코드가 장비에 write하는 경로(code-write path)에
적용됩니다.** 제바오 앱을 통한 live-write는 이 코드 경로를 거치지 않으므로 single-write
보장이나 durable journal을 그대로 제공하지 못합니다. 앱 live-write의 대체 통제는
`docs/hardware-readiness.md`에 따로 정의합니다.

다음은 동결 중에도 삭제하거나 완화하지 않습니다.

- 대상 장비 **identity** 확인과 물리 바인딩 검증
- **출력 범위·step** 검증과 안전 상한
- **single-write** 보장 (같은 변경을 재전송하지 않음)
- **durable journal** 생성과 fsync 순서
- **rollback / 복구 권한** 관련 게이트

### 추가 억제 대상

위에 해당하지 않는, "읽은 값이 기대와 달라서 거부"하는 종류의 게이트는 늘리지 않습니다.
이 구간의 변경은 삭제·통합·완화를 우선합니다.

### 새 게이트의 근거 요건

- **펌웨어 동작에 대한 가설**을 근거로 새 게이트를 만들려면 **실제 raw capture로 재현**되어야
  합니다. fake에서만 재현되는 펌웨어 가설은 게이트가 아니라 `UNKNOWN` 또는 진단으로 남깁니다.
- **코드 내부 결함**(중복 write 가능성, journal 손실, 순서 위반 등)은 raw capture가 없어도
  됩니다. static trace, fault injection, durable artifact 검사 중 하나로 재현하면 채택합니다.

## 3. P1 판정 기준

적대적 코드 리뷰(자동·수동 무관)가 올린 지적은 다음 중 하나에 해당할 때만 P1입니다.

- 잘못된 장비를 대상으로 하거나, 안전 범위 밖 출력을 보내거나, write가 중복될 가능성
- rollback 권한이나 durable journal이 손실될 가능성
- 실제 FAIL을 PASS로 오판할 가능성

**"증명이 덜 엄격하다", "mock에서 이론상 가능하다"는 P1이 아닙니다.** 게이트를 추가하지 말고
`UNKNOWN`으로 두거나 진단 출력으로 남기십시오.

리뷰어가 P1을 올릴 때는 위 셋 중 어디에 해당하는지와 §2의 근거 요건을 어떻게 만족하는지를
함께 제시해야 합니다. 제시하지 못하면 그 지적은 기각합니다.

**단, 구체적인 물리적 위험**(장비 손상, 안전 범위 밖 출력이 실제로 나갈 수 있는 경로, 생물에
대한 위해)이 확인되면 위 절차와 무관하게 **즉시 멈추고 보고합니다.** 이 경우는 동결·게이트
정책보다 우선합니다.

## 4. 실기 기록 — `docs/runs/`는 append-only

- 실기 1회 = `docs/runs/YYYY-MM-DD-<run-id>.md` 파일 1개. 규칙·예외·템플릿은
  [`docs/runs/README.md`](docs/runs/README.md).
- **실기 결과 파일이 커밋되기 전에는 `src/` 아래 파일을 수정하지 않습니다.**
- 위 문장은 **실기 실행 뒤의 순서**를 고정합니다. 아직 실행 가능한 collector가 없어 선행
  실기 결과 자체를 만들 수 없는 경우에는, 다음 조건을 모두 만족하는 **첫 write-free
  read-only collector 구현 1회**만 예외로 허용합니다.
  - 장비 write API와 동결된 native ASYNC 하네스를 import·호출·노출하지 않음
  - clean child worktree에서 구현하고 격리된 `src/tests +3412/-260`을 가져오지 않음
  - static import-graph 검사와 transport 테스트로 control frame 0회를 검증
  - pilot 실기 뒤에는 그 결과 파일을 먼저 커밋할 때까지 다시 `src/`를 수정하지 않음
  이 예외는 복원 도구, actuator, 일반 기능 개발에는 적용되지 않습니다.
  **이 일회 예외는 `f699cfb` collector와 `9dcc19e` pilot 기록으로 사용 완료됐습니다.**
  다시 적용하거나 두 번째 collector 선행 구현의 근거로 쓰지 않습니다.
- **기존 기록의 사실 기술은 수정하지 않습니다.** 정정은 덧붙이기로만 합니다.
  비밀값이 실수로 들어간 경우만 예외로 제거합니다.
- `README.md`·`PROJECT_CONTEXT.md` 상태 블록은 `docs/runs/`를 **링크만** 하고 서사를
  복제하거나 최근 몇 건만 남기고 덮어쓰지 않습니다.
- **"이 실행으로 새로 확정된 사실"이 비어 있으면 정보 0입니다. 정보 0 실행이 2회 연속이면**
  코드를 고치지 말고 §7로 갑니다.
- 증거 등급을 구분해서 씁니다 — (a) preserved raw artifact, (b) preserved structured/durable
  daemon artifact(원본 프레임이 아니며 데몬의 persisted claim까지만 증명),
  (c) reconstructed operator observation. 정의는
  [`docs/runs/README.md`](docs/runs/README.md). 보존 파일과 id·digest를 실제로 확인하기
  전에는 (b)로 올리지 않습니다.

## 5. 현재 미커밋 변경 — 격리, 통째 병합 금지

2026-08-28 시점 작업트리의 `src/`·`tests/` 변경은 **`+3412 / -260`**입니다
(문서·설정을 포함한 전체 tracked diff는 `+3612 / -345`).

이 변경은 **격리된 미검증 worktree 프로토타입(quarantined unvalidated worktree prototype)**
입니다. **"현재 구현"이라고 서술하지 않습니다.** 실기 검증 없이 한꺼번에 병합하지 않습니다.

- 실기에 한 번도 돌지 않았고, `docs/hardware-readiness.md`의 GO/NO-GO 자체가
  "worktree에 검토하지 않은 source 변경 없음"을 요구합니다.
- 같은 날 앞선 커밋이 추가한 검사들을 다시 완화하는 진동이 포함돼 있습니다.
- `FRESH_CAPTURE_*` 진단은 삭제되지 않았지만, 새 owned-receipt 경로가 해당 capture를
  우회하므로 `_09` 경로에서는 더 이상 발동하지 않습니다.
- 복구 경로에 "30초 안에 연속 두 fresh pair"를 요구하는 변경은 지금까지 매번 성공해 온
  복구의 성공률을 낮출 수 있습니다.

문서·capability·config 정직성 수정은 별개로 보존·커밋합니다.

## 6. 작업 트랙 — 병렬

두 트랙을 **병렬로** 진행합니다. 한쪽이 다른 쪽을 막지 않습니다.

**트랙 A — 증거 (Q2 판정) · 현재 `NO-GO / BLOCKED`**

남은 선행조건이 충족되기 전에는 앱 live-write나 measurement epoch를 시작하지 않습니다.

1. **선행조건** — (i) write-free read-only collector 구현과 실기 pilot은 완료
   ([`docs/runs/2026-08-28-pilot-2bd1bf97.md`](docs/runs/2026-08-28-pilot-2bd1bf97.md)),
   (ii) 검증된 exact restore 수단과 권한은 아직 미확보입니다. 따라서 트랙 A는 계속
   `NO-GO / BLOCKED`입니다.
2. **independent control epoch** — 대조군. 슬레이브 시간표가 `independent`에서 그 자체로
   동작하는지 유효 경계 3회로 확인합니다.
3. **앱 ASYNC 전환과 덮어쓰기 확인** — 전환 후 슬레이브 슬롯 출력이 남아 있는지 확인합니다.
   덮어썼다면 펌웨어 FAIL이 아니라 **"앱으로 시험 조건 구성 불가"**이며, 그 자체가 결과입니다.
4. **ASYNC epoch** — 유효 경계 3회.

각 epoch는 `jebao-flow` 코드 write 0회이고, 앱은 epoch 시작 전에 완전히 종료합니다.
전체 절차·안전 순서·판정 기준은 `docs/hardware-readiness.md`가 단일 출처입니다.

**트랙 B — 제품**

3. **software-independent actuator와 그룹 런타임** — `groups/manager.py`는 현재 스텁이고
   데몬에는 actuator가 없습니다. 이 작업은 Q2의 답이 PASS든 FAIL이든 필요하므로 트랙 A의
   결과를 기다리지 않습니다.

## 7. `UNKNOWN`을 만났을 때 — park는 정상 종료입니다

- 먼저 **`jebao-flow` write 없이 관측으로 답할 수 있는지** 묻습니다. 관측으로 답할 수 있는
  질문에 트랜잭션 하네스를 쓰지 않습니다.
- write가 꼭 필요하면 "안전한 전체 실험"이 아니라 "두 가설을 가르는 가장 작은 조작"을
  설계합니다.
- 같은 질문에 실기 2회 또는 1일을 넘기거나 정보 0 실행이 2회 연속이면, 코드를 쓰지 말고
  **멈추고 repository maintainer에게 보고**합니다. 보고서에는 (1) 지금까지의 증거
  (2) 최소 2개의 대안 경로 (3) park했을 때의 영향을 씁니다. 대안 없이 "한 번 더
  하드닝하겠다"는 보고는 무효입니다.
- 답이 없어도 진행할 수 있으면 park하고 사유·재개 조건을 기록합니다. park는 실패가 아닙니다.

## 8. "완료"의 정의

기능 질문에 대한 완료는 **오직** 다음 둘 중 하나입니다.

1. **답함** — `docs/runs/`에 실제 장비가 만든 sample이 기록되어 있고,
   `docs/hardware-readiness.md`의 "현재 판정" 표에서 해당 행이 `UNKNOWN`이 아닌 값으로 바뀜
2. **park함** — §7 형식으로 사유와 재개 조건이 기록됨

다음은 완료가 아니며 완료로 보고하면 안 됩니다: 전체 테스트 통과, 새 게이트·실패 코드 추가,
리팩터링, 커버리지 증가, 문서 갱신.

**"아니오"도 답입니다.** 슬레이브가 마스터를 따라가거나 이전 값을 유지한다는 관측은 질문을
**닫는** 결과이며, 실패한 실험이 아니라 성공한 측정으로 기록합니다.

## 9. 테스트

- **가설을 코드로 옮긴 fake로 그 가설을 검증하지 않습니다.** 같은 커밋에서 "새 fake 노브 +
  그 노브를 쓰는 게이트 + 그 게이트를 통과하는 테스트"를 함께 만드는 것은 동어반복입니다.
  (이는 §2의 **펌웨어 가설**에 대한 규칙입니다. 코드 내부 결함에 대한 fault injection 테스트는
  정상적인 검증 수단입니다.)
- 실기가 알려준 펌웨어 동작이 아니면 fake에 넣지 않습니다. 넣을 때는 근거가 된
  `docs/runs/` 파일을 주석으로 인용합니다.
- 실기의 답이 나올 결과를 단정하는 end-to-end 테스트를 만들지 않습니다.
- 시뮬레이터 재현을 실기 GO 전제조건으로 삼지 않습니다.
- 테스트 줄 수는 성과 지표가 아닙니다.

## 10. 진단

- 유인 실기는 비싸고 드뭅니다. **정보를 남기지 않는 실행은 실행하지 않은 것과 같습니다.**
- 실패 시 분류된 reason 문자열만 남기지 말고, 다음 실기를 태우지 않고 오프라인 재현이
  가능한 수준의 값을 남깁니다.
- 비밀값이 아닌 **판정 값**(기대값/실측값, 실패 코드, 단계)은 운영자 화면과 산출물에 나와야
  합니다. 측정 대상 값이 실패 보고서에 나오지 못하게 막는 테스트가 있으면 그 테스트를 고칩니다.
- 하드닝 커밋 1개당 진단 개선 1개를 함께 넣습니다. 넣을 진단이 없으면 그 하드닝은 아직
  근거가 부족한 것입니다.

## 11. 비밀값

MAC 주소, Gizwits device ID, 사설 IP, passcode, MQTT 비밀번호는 저장소에 기록하지 않습니다.
실제 값은 홈서버의 gitignored private 설정에만 두고, 저장소에는 논리 역할과 판정 결과만
남깁니다. 예시 설정에는 RFC 5737 문서용 주소를 사용합니다.

**raw probe·capture 산출물은 사설 주소를 포함하므로 저장소에 커밋하지 않습니다.**
gitignored private 경로에 보존하고, 저장소에는 다음만 남깁니다.

- **opaque artifact id** 또는 안전한 상대 논리 경로. private 절대경로를 적지 않습니다
  (홈서버 디렉터리 구조도 노출 정보입니다).
- 수집 구간의 **UTC span** (시작·종료)
- **SHA-256 identity binding** — 실제 MAC이나 device ID가 아니라 그 해시. 원문은 금지입니다.
- 산출물 **digest** (SHA-256)

## 12. 커밋

- **커밋 본문을 비워 두지 않습니다.** 고정 역사 구간 `6bf85fc^..7dc5c59`의 커밋 47개는
  본문이 전부 비어 있어서 같은 수정이 여러 번 반복된 것을 추적할 수 없었습니다.
  (이 수치는 그 고정 구간에 대한 것이며 `HEAD` 기준이 아닙니다.)
- 실기 대응 커밋의 본문에는 다음을 씁니다.

  ```
  run: docs/runs/<파일>
  decision: <무엇을 왜 바꿨는가>
  unfreeze: <동결 관련이면 해제 조건, 아니면 n/a>
  ```

- `fix: harden ...` / `fix: verify ...` 같은 제목은 쓰지 않습니다. 무엇을 어떤 관측 근거로
  바꿨는지 씁니다.

## 13. 세션을 시작할 때

코드를 읽기 전에 이 순서로 읽습니다.

1. 이 파일
2. [`docs/runs/`](docs/runs/README.md) 전체
3. `docs/hardware-readiness.md`의 "현재 판정" 표
4. §1 동결 상태 확인

그리고 첫 행동을 정하기 전에 한 문장으로 답하십시오.

> "내가 지금 하려는 일은 **어떤 미확정 질문을 어떤 증거로** 닫는가?"

답할 수 없으면 그 일은 하지 않습니다.
