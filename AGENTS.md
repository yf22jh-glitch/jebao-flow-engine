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

### 2026-08-30 단회 해제 종료

[`Q2 attempt 05`](docs/runs/2026-08-30-q2-attempt-05.md)가 900초 epoch를 완료했고,
fresh explicit raw에서 두 역할의 `independent`, 원 `TimerON`·manual controls와 두 432-byte
schedule image의 byte-exact 복원을 확인했습니다. 다만 양쪽에 같은 Mode 순서와 경계를 넣어
master Mode 소유권을 판별하지 못했으므로, 확인 범위는 `async_slave` 상태의 staged Flow 차이와
exact restore(Q2-narrow)까지입니다. master Mode·timing + slave per-mode Flow(Q2-target)는
`UNKNOWN`입니다.

이 기록을 포함한 문서 커밋부터 단회 해제는 소진됐으며 **§1 동결이 다시 적용됩니다.** 같은
native write 실기를 반복하거나 위 하네스의 코드·테스트를 다시 수정하려면 repository
maintainer의 새로운 명시적 승인과 별도 해제 커밋이 필요합니다.

### 2026-08-31 Q2-target 판별 실기 단회 해제

repository maintainer가 기존 운전 스케줄을 exact snapshot한 뒤 정지하고, 판별용 신규 스케줄을
적용해 15분 동안 관측한 다음 원복하는 **Q2-target 실기 1회**를 명시적으로 승인했습니다.
승인된 질문과 전체 절차는 commit `4b58b91`과 same-mode 전제의 지정 축소를 고정한
후속 commit `8756f65`의
[`Native ASYNC 모드별 slave Flow 실기 계획서`](docs/native-async-per-mode-flow-test-plan.md)가
단일 출처입니다. 장비 write는 같은 계획의 현장 preflight가 모두 통과한 뒤에만 시작합니다.

고정 계획은 다음과 같습니다. 장비별 safe manual Flow까지 포함한 여섯 값은 서로 다르며,
승인 상한은 `60`입니다.

- master manual `30`; A=`Sine / Flow 50 / Frequency 40`; B=`Constant / Flow 55 /
  wire Frequency 0`
- slave manual/common `35`; A=`Constant / Flow 45 / wire Frequency 0`; B=`Sine /
  Flow 60 / Frequency 35`
- 양쪽 A/B 경계는 동일한 device-local 절대 시각이고, 전체 observation epoch는 `900초`
- 역할은 master=`master`, slave=`async_slave`; Sync·Independent 대조는 이번 해제에 포함하지 않음

허용하는 코드 변경은 아래 여덟 항목뿐입니다.

1. master와 slave 각각에 독립적인 A/B `Mode`, `Flow`, `Frequency`를 주는 고정 spec
2. master safe manual `30`, slave safe manual/common `35`를 slot Flow와 분리
3. 함수 시그니처만 장비별로 일반화하고 승인된 상수를 고정하는 2-slot encoder
4. Mode 소유권과 Flow 소유권을 함께 판정하는 배타 classifier
5. 첫 write 뒤 비안전 진단 오류만으로 조기 원복하지 않는 900초 completion rule
6. explicit reply action을 포함한 원본 frame과 장비별 `NowTime` 진단 보존
7. 위 고정 계획에 직접 대응하는 기존 단위 테스트와 fault-injection 테스트
8. `schedule_linkage.py`의 pre-write 전제를 이번 고정 계획에 한해 지정 축소
   - `_snapshots_from_states()`에서는 두 장비의 `before.mode` 동일 및 `after_mode` 동일 요구만
     제거하고, 동일 `boundary_at`, 서로 다른 physical binding, slave A/B Flow 차이 및
     slave-after/master-after Flow 차이는 유지
   - `_assert_staged_auto_transition_preconditions()`에서는 장비별 `Constant -> Sine` 고정과
     `snapshot.mode == before.mode` 요구만 이번 장비별 mode 쌍으로 교체하고, 2-entry 비순환,
     `after_valid_until` 단일 창, 경계 후 안정 예산과 frequency allowlist는 유지
   - master A=`Sine/40`, slave B=`Sine/35`, 두 Constant wire frequency=`0`을 근거로
     frequency allowlist를 이번 고정 계획에 맞게 다시 기술
   - 이 고정 계획의 exact signature가 일치할 때만 내부 staged-flow 상한을 `45`에서 `60`으로
     선택. 한 값이라도 다르면 기존 `45`를 유지하며, 범용 상한이나 장비 limits는 변경 금지
   - `owned_staged_auto_transition_observation=False`로 전제 블록 전체를 끄는 우회는 불승인

수정 허용 파일은 다음으로 한정합니다.

- `src/jebao_flow/devices/schedule_flow_experiment.py`
- raw/`NowTime` 진단 추가와 위 8항의 지정 전제 축소에 한한
  `src/jebao_flow/devices/schedule_linkage.py`
- `src/jebao_flow/devices/lan.py`는 가산적 read-only 변경 두 가지에 한함
  - typing 전용 `RawSession` protocol에 기존 `read_raw_state_capture()` 계약 선언
  - 기존 `_io_lock`과 장비 read 1회를 그대로 유지하면서 decoded state와 같은 read의
    `RawStateCapture`를 함께 반환하는 공개 메서드 1개 추가
- 장비별 patch 전달에 실제 변경이 필요한 경우의
  `src/jebao_flow/devices/schedule_transaction.py`
- qualification 만료 방침에 필요한 최소 변경에 한한
  `src/jebao_flow/schedule_flow_experiment_cli.py`
- 위 변경에 직접 대응하는 기존 단위 테스트

`devices/linkage.py`, `schedule_linkage_cli.py`, 일반 데몬, MQTT 경로는 계속 동결합니다.
`devices/lan.py`의 기존 `read_raw_state` 경로와 모든 write 경로도 변경하지 않습니다. capture는
`protocol/session.py`가 같은 read에서 이미 생성한 뒤 버리던 값만 전달해야 하며, 추가 round-trip,
두 번째 세션, private `_session` 접근, 원본 frame 재구성은 금지합니다. 원본 wire frame은
`ScheduleLinkageSample`이나 공개 로그에 넣지 않고 별도 private raw sink로만 전달합니다. sample에는
reply action, frame SHA-256, `NowTime`, 길이만 남깁니다. 이 capture 경로를 제공할 수 없으면 첫 write
전에 `NO-GO`로 종료합니다.

CLI에 임의 Mode·Flow 값을 노출하는 범용 실험기, 새 장비-read pre-write gate, 새 실패 계층,
Sync·Independent 대조, 같은 ASYNC 실기 반복은 승인하지 않습니다. 첫 write 뒤 900초 epoch에
진입한 operation은 한 번만 허용하며, 첫 write 전 `NO-GO`가 정보 0으로 2회 연속되면 §7에 따라
정지·보고합니다.

이 단회에 한해 아래 통제 변경 세 건을 명시적으로 승인합니다.

1. manifest에 고정한 지정 qualification operation의 기존 2/2 영수증에서 **만료 시각만** 무시.
   영수증 부재·identity·operation 불일치는 우회 금지
2. 보존 raw의 장비 시계 차이를 반영한 clock-skew gate `<=30초`
3. 출력 상한을 이전 단회 `45`에서 이번 고정 계획의 `60`으로 재설정

세 번째 항목은 일반 안전 상한의 완화가 아니라 여섯 개의 판별값을 가진 이번 고정 계획 전용
상한입니다. `60`은 보존 baseline active schedule 범위(role A `30..60`, role B `50..80`) 안이며
새 물리 출력 영역을 열지 않습니다.

이 셋 외의 identity·single-write·durable journal·rollback 권한은 완화하지 않습니다. 기존
스케줄 정지는 두 장비 각각에 `TimerOFF + independent + Constant + safe Flow + safe Frequency`를
하나의 합성 frame으로 정확히 한 번 적용합니다. `TimerOFF` 단독 write로 latent manual
`Flow=89`를 노출하지 않습니다.

이 합성 frame은 새 write 경로가 아닙니다. 기존
`ScheduleFlowExperimentController._stage_devices()`가 `SwitchON`을 포함한 여섯 제어 필드를
하나의 `DeviceTarget`으로 구성해 장비마다 `write_target()`을 정확히 한 번 호출하고,
`LanJebaoDevice._target_changes()`와 `_apply_changes()`가 그 mapping을 하나의 control payload로
인코딩합니다. 따라서 `devices/linkage.py` 수정이나 `devices/lan.py`의 write-path 수정은 필요하지
않습니다.
`TimerOFF`를 먼저 보내거나 두 개 이상의 control frame으로 나누는 구현은 승인하지 않습니다.
이 합성 target을 단일 payload로 preview할 수 없으면 첫 write 전에 `NO-GO`로 종료합니다.

첫 hardware write 뒤에는 예상 밖 Mode·Flow·Auto tuple, 비원자 수렴, read timeout, timing skew,
부분 적용 같은 **진단 오류만으로 900초 전에 원복하지 않습니다.** 즉시 ordered recovery로
전환하는 조건은 다음뿐입니다.

1. physical identity binding 불일치
2. 실제 또는 보고된 출력이 `60` 초과
3. 펌프·수조의 위험한 물리 동작
4. 복구 권한·durable journal·현장 차단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

종료 뒤 복구 순서는 `slave role detach → master role detach → 안전 합성 TimerOFF → slave/master
원 432-byte schedule image → 원 outer controls/TimerON`입니다. writer는 write-side restore의
terminal evidence를 fsync한 뒤 journal을 지우지 않고 종료합니다. 별도 fresh session 두 번의
exact 검증이 통과한 뒤에만 journal 정리와 lease 해제를 수행합니다. 불일치하면 journal을 유지한
`RECOVERY_REQUIRED`로 남기고 추가 write나 임의 승격을 금지합니다.

900초 epoch, byte-exact restore, terminal journal, 새 `docs/runs/` 기록의 append-only 커밋이 모두
완료되면 이 단회 해제는 자동 소진되고 §1 동결이 다시 적용됩니다. 복구가 남으면 새 실험은
금지하고 이 operation의 ordered recovery와 read-only 검증만 허용합니다.

### 2026-09-01 Q2-target 단회 실행 종료

[`Q2-target 판별 시도`](docs/runs/2026-09-01-q2-target-9c982c60.md)는 outer safe pause와
sentinel qualification·restore를 완료한 뒤, 본 field schedule의 첫 write 전에 기존
동일-topology guard에서 종료됐습니다. master `Sine -> Constant`와 slave
`Constant -> Sine`은 의도한 판별 계획이지만, `schedule_transaction.py`의 field image 검증은
두 장비 topology의 완전 동일성을 계속 요구했습니다. 따라서 native role과 900초 epoch에는
도달하지 못했고 Q2-target은 `UNKNOWN`입니다.

write-side rollback은 terminal로 끝났고 journal 3종은 비었습니다. 이후 서로 다른 두 fresh
source-attested collector session에서 원 control 여섯 필드와 두 432-byte schedule image의
exact digest가 모두 일치했습니다. 추가 attended recovery는 없습니다.

실장비 write를 포함한 단회 operation은 이 실행으로 **소진**됐으며 §1 동결이 다시 적용됩니다.
같은 실험을 재시도하거나 동결 하네스의 코드·테스트를 수정하려면 repository maintainer의 새로운
명시적 승인과 별도 해제 커밋이 필요합니다. 승인 전에는 Q2-target을 `UNKNOWN`으로 두고,
software-independent actuator와 그룹 런타임을 기본 제품 경로로 진행합니다.

### 2026-09-01 Q2-target topology 교정 단회 해제

repository maintainer와 on-site hardware approver가 위 소진 기록과 원인 분석을 확인한 뒤,
기존 반대 Mode 고정 계획을 **정확히 한 번 교정·재실행**하도록 새로 승인했습니다. 이 해제는
[`2026-09-01 Q2-target 판별 시도`](docs/runs/2026-09-01-q2-target-9c982c60.md)가 실장비에서
outer safe pause와 sentinel write·read-back·exact restore를 완료했지만, 본 field write 전에
`TemporaryScheduleController._expected_images()`의 cross-device 동일-topology 전제에 막힌
코드 내부 충돌만 바로잡기 위한 것입니다. 네트워크·펌웨어 가설을 새 게이트로 옮기지 않습니다.

허용하는 source와 test 변경은 다음 두 파일로 한정합니다.

- `src/jebao_flow/devices/schedule_transaction.py`
- 위 변경을 직접 검증하는 `tests/unit/test_schedule_transaction.py`

일반적인 topology 완화는 승인하지 않습니다. 기존 field-image 검증은 아래 exact fixed signature가
모두 일치할 때만 서로 반대인 Mode topology를 허용합니다.

- master: A=`Sine / Flow 50 / Frequency 40`, B=`Constant / Flow 55 / wire Frequency 0`
- slave: A=`Constant / Flow 45 / wire Frequency 0`, B=`Sine / Flow 60 / Frequency 35`
- 두 장비는 각각 정확히 2개의 연속·비순환 entry를 가지며 start/end와 하나의 A→B 경계 시각은
  서로 일치

서명 인식은 기존 `device_patches`와 patch가 적용된 image의 Mode·Flow·Frequency·경계 값에서만
파생합니다. `TemporaryScheduleSpec`에 새 persisted field를 추가하거나 기존 confirmation·recovery
token payload를 바꾸지 않습니다.

Mode·Flow·Frequency·순서·경계 중 하나라도 이 서명과 다르면 기존 동일-topology 요구를 그대로
적용합니다. identity와 서로 다른 physical binding, 출력 상한 `60`, 장비 min/max/step,
single-write, durable journal, rollback 권한, sentinel qualification, 경계 후 300초 안정 조건과
900초 complete epoch는 변경하지 않습니다. 새 실패 코드·새 pre-write 계층·범용 CLI·펌웨어 fake
노브는 추가하지 않습니다.

기존 field-image `if`에서 이 exact signature가 우회할 수 있는 것은
`master_topology != slave_topology` 한 항뿐입니다. `slave_flows[0] != slave_flows[1]`과
`master_flows[1] != slave_flows[1]` 판별 조건은 그대로 유지합니다.

source 변경을 커밋하기 전에는 다음 write-free 검증을 모두 통과해야 합니다.

1. 위 종료 실행 뒤 보존된 서로 다른 두 source-attested collector series의 raw에서 원 outer
   controls와 두 432-byte schedule image를 다시 추출
2. 그 exact image에 승인 patch를 적용한 `_expected_images()`가 고정 서명만 허용함을 확인
3. Mode·Flow·Frequency·경계의 단일 값 변이, master/slave 역할 교환, entry 수·연속성·비순환
   조건 변이와 다른 cross-device topology는 모두 기존처럼 거부
4. 변경 전 golden confirmation token과 변경 후 token이 동일하고 새 필드가 canonical payload에
   들어가지 않음을 확인
5. 관련 테스트와 전체 suite 통과, exact commit Docker image 빌드, Claude의 read-only
   `COMMIT_OK`

그 뒤 새 operation id와 fresh device-local 경계를 사용해
[`Native ASYNC 모드별 slave Flow 실기 계획서`](docs/native-async-per-mode-flow-test-plan.md)의
동일한 고정 계획을 **실장비에서 한 번만** 실행합니다. 첫 hardware write 뒤에는 진단 불일치나
일시적 read 오류만으로 중단하지 않고 900초 epoch를 끝까지 관측합니다. 조기 ordered recovery는
기존 다섯 조건 — identity 불일치, 출력 `60` 초과, 구체적 물리 위험, journal·복구·현장 차단
권한 상실, 명시적 비상 정지 — 에서만 시작합니다.

종료 뒤에는 `slave role detach → master role detach → 안전 합성 TimerOFF → slave/master 원
432-byte schedule image → 원 outer controls/TimerON` 순서로 복구하고, 서로 다른 두 fresh
source-attested session의 raw에서 원 control과 두 image의 byte-exact 일치를 확인합니다. 결과는
성공·실패와 무관하게 새 `docs/runs/` 파일에 append-only로 기록합니다. 이 한 operation과 복구·
독립 검증이 끝나면 단회 해제는 자동 소진되고 §1 동결이 다시 적용됩니다. 자동 재시도는 없으며,
복구가 남으면 새 실험 없이 해당 operation의 ordered recovery와 read-only 검증만 허용합니다.

### 2026-09-01 Q2-target topology 교정 단회 실행 종료

[`Q2-target topology 교정 실행`](docs/runs/2026-09-01-q2-target-corrected-a3adb738.md)은 fixed
field schedule과 `TimerON`을 적용하고 900초 observation epoch를 끝까지 수행했습니다. master
explicit raw 91개의 오프라인 재분석은 `Sine/AutoFlow50 -> Constant/AutoFlow55` Mode·Flow 전환과
399.464초 유지를 확인했습니다. Constant 슬롯의 wire `Frequency=0`과 보고 `AutoFreq=5` 차이는
해석하지 않습니다. slave가 포함된 pair read는 90회 모두 `monitor_state_read`로 실패해 slave
raw와 유효 pair sample은 0개였습니다. 따라서 master Mode·Flow 전환만 확인됐고 Q2-target은
계속 `UNKNOWN`입니다.

write-side ordered rollback은 terminal로 완료됐고 rollback failure·recovery reason은 0입니다.
이후 서로 다른 두 fresh source-attested collector session에서 원 control 여섯 필드와 두
432-byte schedule image digest가 모두 일치했습니다. private config도 원 digest로 relock됐고
`dry_run=true`, write-enabled device 0을 확인했습니다.

topology 교정 단회 operation은 이 실행으로 **소진**됐으며 §1 동결이 다시 적용됩니다. 자동
재시도는 없습니다. 새 실장 write를 승인하기 전에 보존 raw와 exact commit으로 live ASYNC
slave의 role/state-dependent explicit reply와 pair-read 경로를 write 없이 먼저 분석합니다.
이후에도 Q2-target 실기가 필요하면 repository maintainer의 새로운 명시적 승인과 별도 해제
커밋이 필요합니다. 기본 제품
경로는 software-independent actuator와 그룹 런타임입니다.

### 2026-09-02 동일-Mode slave 슬롯별 Flow 재검증 단회 해제

repository maintainer가 현재 두 Pro의 controls와 432-byte schedule image를 백업한 뒤 앱 UI를
거치지 않고 임시 schedule을 넣어, master의 Mode·경계를 따르는 `async_slave`가 자기 슬롯별
`Flow`를 적용하는지 관측하고 원상복구하는 **실기 1회**를 명시적으로 승인했습니다. 질문과
판정·복원 계약의 단일 출처는 commit `6c51dda`의
[`Native ASYNC slave 슬롯별 Flow 단회 재검증 계획`](docs/native-async-slave-slot-flow-recheck.md)
입니다. 실제 장비 write는 on-site hardware approver가 현장에서 감시하고 구체적인 물리 전원
차단 수단을 확인한 뒤에만 시작합니다.

이번 exact signature는 다음뿐입니다.

- master safe manual `Constant / Flow 30 / Frequency 20`
- slave safe manual `Constant / Flow 35 / Frequency 20`
- master A=`Sine / Flow 40 / Frequency 50`, B=`Constant / Flow 35 / wire Frequency 0`
- slave A=`Sine / Flow 35 / Frequency 50`, B=`Constant / Flow 47 / wire Frequency 0`
- 두 장비의 A/B 경계는 동일한 device-local 절대 시각, 역할은 master=`master`,
  slave=`async_slave`, complete observation epoch는 `900초`

Mode 소유권은 이번 판정 대상이 아닙니다. slave가 master의 `Sine -> Constant` Mode·경계를
따르는 동안 slave raw의 `AutoFlow`가 stable `35 -> 47`을 만드는지만 판정합니다. master의
`40 -> 35` 경계가 유효한데 slave가 `35` 고정, master Flow 추종 또는 다른 stable Flow를
보고하면 `FAIL`이며, usable slave raw나 안정 sample이 없으면 `UNKNOWN`입니다. 어느 결과도
같은 operation의 자동 재시도를 허용하지 않습니다.

허용하는 source와 test 변경은 아래로 한정합니다.

1. `src/jebao_flow/devices/schedule_flow_experiment.py`
   - 위 exact five-value signature, 두 장비 모두 `Sine -> Constant`인 schedule, Flow 상한 `47`
   - `slave A == master B == 35`를 이 signature에서만 허용
   - slave `Sine/35/F50 -> Constant/47`을 PASS로 분류하고 나머지를 배타 분류
2. `src/jebao_flow/devices/schedule_linkage.py`
   - 위 exact A/B 상수·Frequency allowlist·Flow 상한 `47`
   - fixed monitor가 같은 read에서 선택된 recognised action `0x03` 또는 `0x04`의 raw frame과
     decoded state를 보존하도록 지정 축소
3. `src/jebao_flow/devices/lan.py`
   - 기존 `_io_lock` 안에서 `read_raw_state_capture(accept_reports=True)`를 정확히 한 번 호출해
     decoded state와 같은 `RawStateCapture`를 반환하는 가산적 read-only 메서드 1개
4. `src/jebao_flow/schedule_flow_experiment_cli.py`
   - 임의 값 옵션 없이 위 exact signature만 구성
5. 위 변경에 직접 대응하는 기존 unit·fault-injection tests

`schedule_transaction.py`의 topology 예외는 사용하지 않습니다. 두 temporary image는 동일한
`Sine -> Constant` topology이므로 기존 동일-topology 검증을 그대로 통과해야 합니다.
`devices/linkage.py`, `devices/schedule_transaction.py`, `schedule_linkage_cli.py`, 일반 daemon·MQTT
경로와 LAN write path는 수정하지 않습니다.

action `0x04`는 explicit reply나 요청 ACK로 부르지 않습니다. participant, action, exact frame
digest·길이, device-local `NowTime`, host read 구간을 private raw sink에 보존하고, 같은 boundary
exclusion side의 master/slave frame pair만 판정에 사용합니다. 한쪽 raw 부재·decode 실패는
`UNKNOWN` 근거이지 재시도 근거가 아닙니다.

이번 단회에 한해 exact physical binding과 qualification operation이 일치하는 기존 영수증에서
만료 시각만 무시할 수 있습니다. 영수증 부재·identity·operation 불일치, 장비 limits/step,
single-write, durable journal, rollback 권한은 완화하지 않습니다. `Frequency=50`은 current raw의
동일 product waveform-frequency envelope 안에서 이 exact signature에만 허용하며 endpoint·step
전체를 검증했다고 일반화하지 않습니다.

시작은 장비별 `TimerOFF + independent + Constant + safe Flow + Frequency 20`을 하나의 control
frame으로 정확히 한 번 적용합니다. 종료는 기존 audited 순서인 `slave role detach → master role
detach → safe TimerOFF → slave/master 원 432-byte schedule image → 원 outer controls/TimerON`을
사용합니다. 자동 rollback이 terminal로 끝나지 않으면 새 실험이나 임의 write를 하지 않고,
남은 journal을 소유한 기존 `recover_experiment()` attended recovery만 운영자가 수동 호출합니다.

첫 hardware write 뒤 조기 ordered recovery 조건은 다음 다섯 가지뿐입니다.

1. physical identity binding 불일치
2. temporary schedule authority 또는 native 역할이 활성인 동안 실제 또는 보고된 Flow가 `47`
   초과
3. 펌프·수조의 위험한 물리 동작
4. durable journal·복구 권한·현장 물리 차단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

결과와 무관하게 terminal write-side restore와 서로 다른 두 fresh source-attested collector의 원
controls·두 image exact 검증을 끝내고 새 `docs/runs/` 기록을 커밋하면 이 단회 해제는 자동
소진되고 §1 동결이 다시 적용됩니다. 복구가 남으면 새 실험은 금지하고 해당 operation의 ordered
recovery와 read-only 검증만 허용합니다.

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

### 2026-08-31 상태 정정

위 수치와 "미커밋 변경" 서술은 capability branch가 아니라 **별도 main worktree에 격리된
prototype**을 가리킵니다. 2026-08-31 재측정에서도 그 worktree의 `src/`·`tests/` diff는
`+3412 / -260`이며 미커밋 상태입니다. capability 작업의 기준 commit `1e08cce`는 clean
tree였습니다. 이 정정은 격리 prototype을 현재 구현으로 승격하거나 통째 병합을 승인하지
않습니다.

위에 기록된 검사 진동, `FRESH_CAPTURE_*` 우회, 30초 fresh-pair 복구 조건의 성공률 우려는
이 정정으로 해소되지 않았습니다. 별도 증거가 생길 때까지 미해결 기술 경고로 유지합니다.

## 6. 작업 트랙 — 병렬

세 트랙을 **병렬로** 진행합니다. 한쪽이 다른 쪽을 막지 않습니다.

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

**트랙 C — 수류모터 capability (2026-08-31 repository maintainer 승인 read-only 범위)**

- preserved raw 오프라인 재분석과 기존 v2를 보존하는 가산적 3장비 read-only collector는
  진행할 수 있습니다. 마지막 실기 기록이 `1e08cce`에 커밋되고 기준 tree가 clean이었으므로
  §4의 코드 수정 순서 조건을 충족합니다. 이는 §1 동결 해제가 아닙니다.
- 바형 수류모터는 승인된 384-byte exact restore가 없으므로 **관측 전용**입니다. 일반
  actuator 트랙에서 generic restore가 준비되기 전에는 write하지 않습니다.
- 0/100 endpoint나 범위 밖 값을 장비에 보내지 않습니다. schema 범위, 장비 read-back,
  물리 효과는 서로 다른 claim으로 기록합니다.
- 오프라인 재분석과 read-only 수집이 각각 새로 확정된 사실 0이면 §7의 2회 연속 정보 0
  조건으로 멈추고 보고합니다.
- 상세 판정·호환성 규칙은
  [`docs/capabilities/README.md`](docs/capabilities/README.md)와
  [`collector-v3-requirements.md`](docs/capabilities/collector-v3-requirements.md)가 단일
  출처입니다.

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
