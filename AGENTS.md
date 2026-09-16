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

### 2026-09-02 동일-Mode slave 슬롯별 Flow 단회 실행 종료

[`Q2-slotflow 재검증`](docs/runs/2026-09-02-q2-slotflow-a00f4b1.md)은 fixed schedule과
`TimerON`을 적용하고 900초 epoch를 끝까지 수행했습니다. master raw는 device-local 경계에서
`Sine/40/F50 -> Constant/35` 전환을 보였습니다. slave raw는 action `0x04` 411개가 남았지만
frame-local `NowTime`이 모두 경계 전이었고 host 수신 대비 지연이 계속 커진 report backlog라,
유효한 post-boundary slave sample은 0개였습니다. 따라서 slave의 슬롯별 `35 -> 47` 적용은
`PASS`나 `FAIL`로 분류하지 않고 계속 `UNKNOWN`입니다.

실행 뒤 cached Pro app template을 정적으로 교차검증한 결과, 앱 UI는 independent slave에 먼저
`Linkage=2`를 적용한 뒤 별도 `Linkage=3`을 보내 `sync_slave -> async_slave`로 전환합니다.
이번 하네스는 `Linkage=3` 한 번으로 직접 전환했으므로 앱의 role construction과 sequence가
동등하지 않습니다. intermediate role의 펌웨어 의미는 아직 `UNKNOWN`이며, 이 차이를 native
capability `FAIL` 근거로 사용하지 않습니다.

write-side rollback은 terminal `outer_restored`로 끝났고, 서로 다른 두 fresh source-attested
collector에서 원 controls와 두 432-byte schedule image digest가 byte-exact하게 일치했습니다.
이 기록 커밋부터 `13c5068`의 단회 해제는 **소진**되고 §1 동결이 다시 적용됩니다. 같은
operation의 자동 재시도는 없습니다. corrected 실기가 필요하면 repository maintainer의 새로운
명시적 승인, 앱과 같은 `independent -> sync_slave -> async_slave` sequence와 fresh post-boundary
slave evidence를 고정한 별도 해제 커밋, 그리고 on-site hardware approver 승인이 모두 필요합니다.

### 2026-09-02 앱 role sequence 교정 Q2-slotflow 단회 해제

repository maintainer가 위 `UNKNOWN`과 cached Pro 앱의 role dataflow를 확인한 뒤, 기존 fixed
schedule은 그대로 두고 앱과 같은 `independent -> sync_slave -> async_slave` 순서와 매 pair의
fresh transport만 교정한 **실장비 operation 1회**를 명시적으로 승인했습니다. 특히
`sync_slave`를 적용한 다음 새 paired read에서 해당 두 장비가 실제 master·sync slave로 지정돼
동작하는지 확인하기 전에는 `async_slave`를 보내지 말라고 지시했습니다. 질문·판정·복원 계약의
단일 출처는 commit `9c0e274`의
[`Native ASYNC slave 슬롯별 Flow 앱-sequence 교정 단회 계획`](docs/native-async-slave-slot-flow-app-sequence-recheck.md)
입니다.

schedule·safe manual·판정 signature와 상한은 직전 실행에서 변경하지 않습니다.

- master safe manual `Constant / Flow 30 / Frequency 20`
- slave safe manual `Constant / Flow 35 / Frequency 20`
- master A=`Sine / Flow 40 / Frequency 50`, B=`Constant / Flow 35 / wire Frequency 0`
- slave A=`Sine / Flow 35 / Frequency 50`, B=`Constant / Flow 47 / wire Frequency 0`
- 두 장비의 A/B 경계는 동일한 device-local 절대 시각, complete observation epoch는 `900초`,
  boundary exclusion은 `T-60초`부터 `T+60초`, post-boundary 안정은 최소 `300초`
- temporary schedule·native role authority 아래 guarded Flow 상한은 `47`

이번 단회에서 허용하는 source 변경은
`src/jebao_flow/devices/schedule_linkage.py` 하나뿐입니다. 직접 대응하는
`tests/unit/test_schedule_linkage_transaction.py`를 수정하고, persisted record의 기본값 추가가
기존 outer event·CLI 직렬화 테스트에 직접 영향을 줄 때만
`tests/unit/test_schedule_flow_experiment.py`와
`tests/unit/test_schedule_flow_experiment_cli.py`를 함께 수정할 수 있습니다.

다음 파일과 경로는 변경하지 않습니다.

- `src/jebao_flow/devices/schedule_flow_experiment.py`의 fixed schedule·classifier·상한
- `src/jebao_flow/devices/schedule_transaction.py`, `src/jebao_flow/devices/linkage.py`,
  `src/jebao_flow/devices/lan.py`
- 두 CLI source, 일반 daemon·MQTT, LAN read/write API, rollback write path와 persistence store

role write는 다음 durable 순서로만 수행합니다.

1. master intent를 먼저 fsync하고 기존 `Linkage=master`를 정확히 한 번 보낸 뒤 기존 fresh pair
   검증을 완료합니다.
2. slave의 기존 write intent와 새 `sync_intent`를 같은 durable successor에 fsync한 뒤
   `Linkage=sync_slave`를 정확히 한 번 보냅니다. ACK나 read-back이 불명확해도 같은 control
   frame을 재전송하지 않습니다.
3. adapter가 target 일치를 확인해 정상 반환한 뒤에도 새 paired session에서 recognised state
   frame을 장비별 한 번씩만 읽습니다. physical binding·online/no-error/`SwitchON`,
   master=`master`·slave=`sync_slave`, 양쪽 `TimerON`, 양쪽 temporary schedule fingerprint,
   양쪽 active Flow `<=47`을 모두 적극 확인한 경우에만 `sync_verified`를 fsync합니다.
4. 위 확인이 하나라도 실패하거나 불명확하면 `async_slave` write는 **0회**이며, 관측 raw와
   진단을 남기고 ordered rollback으로 갑니다.
5. `sync_verified` 뒤 `async_intent`를 fsync하고 나서만 `Linkage=async_slave`를 정확히 한 번
   보냅니다. adapter 정상 반환을 `async_returned`로 fsync한 뒤 기존 final pair 검증과 monitor로
   진행합니다. 이 frame도 재전송하지 않습니다.

slave progress는 새 실행에서만 `sync_then_async`로 표시하며 canonical prefix는
`() -> sync_intent -> sync_verified -> async_intent -> async_returned`뿐입니다. 기존 journal은
기본 `direct`로 계속 읽습니다. recovery가 write 전에 허용하는 slave role은 durable prefix와
detach 상태에 따라 다음으로 제한합니다.

- `()`: `independent`
- `sync_intent`: `independent | sync_slave`
- `sync_verified`: `sync_slave`
- `async_intent`: `sync_slave | async_slave`
- `async_returned`: `async_slave`
- slave detach 기록 뒤: `independent`

이 세부 progress는 기존 master-first `linkage_write_intent_device_ids`·`linked_device_ids`를
대체하지 않고 합성합니다. recovery는 기존 역순인 slave detach 뒤 master detach를 유지합니다.
중간 `sync_slave`가 manual DP를 바꾸지 않는다고 가정하지 않으며, exact role-only detach가 그
부수 drift로 terminal이 되지 못하면 소유된 outer journal의 기존 audited external-disarm proof만
사용합니다. 새 복구 write·새 recovery path는 만들지 않습니다.

fixed monitor는 각 pair마다 기존 paired disconnect/connect의 uninterruptible boundary를 끝낸 뒤
새 session에서 장비별 recognised frame을 정확히 한 번 선택합니다. 같은 session의 두 번째 판정
read와 같은 ordinal의 transport retry는 금지합니다. 한 participant가 실패해도 형제의 성공 raw는
보존하며, action `0x03`과 `0x04`의 구성·frame-local `NowTime`·host read 구간을 분리 기록합니다.
`0x04`는 explicit reply나 ACK로 부르지 않고, staging/TimerON arming 뒤이며 같은 boundary side인
frame만 판정합니다. sync 중간 확인에 쓰인 첫 raw pair ordinal은 monitor 판정 sample로 세지
않고, `async_returned` 뒤 다음 ordinal부터 boundary monitor를 시작합니다.

monitor acquisition은 paired refresh와 capture 전체에 `8초` authority deadline을 두고, acquisition
완료 뒤 다음 시작까지 최소 `10초`를 기다립니다. 연속 acquisition failure가 `3회`면 새 연결을
중단하고 `UNKNOWN` ordered rollback으로 갑니다. 900초 동안 최대 paired refresh `91회`, device
connect/auth `182회`이며, 정상 active-Flow 점검 간격은 최대 약 `18초`입니다. uninterruptible
refresh의 component timeout과 세 번의 연속 실패가 겹치면 마지막 valid sample 이후 최대 약
`90초` 동안 새 sample로 Flow 상한을 평가하지 못할 수 있음을 실행 전에 기록합니다. refresh와
capture 오류는 reason뿐 아니라 allow-listed 예외 class도 남깁니다. 실행 기록에는 실제 paired
refresh·device connect/auth 횟수와 transport 예외 class 분포를 남기고, `async_slave`에 반복
재연결·재인증을 가한 첫 실행이라는 measurement confound도 판정과 분리해 명시합니다.

physical identity·서로 다른 binding, 장비 limits·step, single-write, durable journal·fsync,
attended lease, stop·safety authority, exact ordered rollback은 완화하지 않습니다. 첫 hardware write
뒤 조기 ordered recovery 조건은 다음 다섯 가지뿐입니다.

1. physical identity binding 불일치
2. temporary schedule·native role authority 아래 실제 또는 보고된 Flow가 `47` 초과
3. 펌프·수조의 구체적 위험 동작
4. durable journal·복구 권한·현장 물리 차단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

diagnostic read 오류, action `0x04`, bounded tuple 불일치는 같은 write를 재전송할 이유가 아닙니다.
sync 중간의 적극 확인 실패는 다음 write 권한을 만들지 않으며, monitor의 연속 실패 상한은 새
연결 폭주를 끝내는 measurement 종료일 뿐 여섯 번째 물리 safety condition이 아닙니다.

source commit 전에는 직접 unit·fault-injection 테스트와 전체 suite를 통과시키고 Claude의
read-only `COMMIT_OK`를 받습니다. exact commit의 Linux/amd64 image를 만들고 revision·fixed
signature·Flow 상한을 대조한 뒤, 장비 write 0인 fresh baseline collector로 두 physical binding,
원 controls와 두 432-byte schedule image를 다시 보존합니다. live config는 정확히 두 Pro만
write-enabled여야 하고 다른 Observer·writer·recovery supervisor가 없어야 하며, TCP 12416 연결
0과 attended lease 단일 보유를 첫 write 직전에 다시 확인합니다.

repository maintainer 겸 on-site hardware approver는 물리 차단 수단의 구체적 열거를 면제하고
현장에 수습 가능한 인원이 있다고 확인했습니다. 이 operation별 결정은
`docs/hardware-readiness.md`의 물리 전원 차단 확인 항목을 충족으로 바꾸거나 삭제하지 않습니다.
실제 첫 write는 그 문서의 유효한 조건을 다시 확인한 뒤에만 시작합니다.

실행은 새 operation id와 fresh device-local 경계로 정확히 한 번만 수행합니다. 결과와 무관하게
기존 ordered restore와 서로 다른 두 source-attested fresh collector의 원 controls·두 image
byte-exact 검증을 완료하고 새 `docs/runs/` 기록을 append-only로 커밋하면 이 해제는 자동 소진되고
§1 동결이 다시 적용됩니다. 비-terminal intent가 남으면 새 실험과 fixed 상수 변경을 금지하고,
이 source의 exact image로 해당 operation의 기존 attended recovery와 read-only 검증만 허용합니다.

### 2026-09-02 앱 role sequence 교정 Q2-slotflow 단회 실행 종료

exact `ced43a9` image로 앱과 같은 첫 단계인 `independent -> sync_slave`를 수행했습니다. fresh raw
pair에서 master=`master`, slave=`sync_slave`, 양쪽 `TimerON`, 서로 다른 exact temporary schedule
image의 장비별 보존과 bounded active Flow `40`·`35`를 확인했습니다. Sync 역할 지정과 장비별
schedule image 보존은 성립했지만, 양쪽 A의 mode·frequency·boundary가 같아 runtime에서 master
schedule을 따랐는지 slave 로컬 schedule을 실행했는지는 판별하지 못했습니다. 그러나 slave의
inactive manual `Mode/Frequency`가 saved safe manual `Constant/F20`이 아니라 active A와 같은
`Sine/F50`으로 보고됐고, existing audited snapshot-control assertion이
`slave_pair_slave_state / mode,frequency`로 거부했습니다.

canonical progress가 `sync_verified`에 도달하지 않아 `async_intent`와 두 번째 slave role write는
생성되지 않았습니다. 따라서 `async_slave` write는 0회이고 900초 epoch도 시작하지 않았으며,
Q2-slotflow는 계속 `UNKNOWN`입니다. automatic ordered rollback은 terminal `outer_restored`로
끝났고, 서로 다른 두 source-attested fresh collector에서 원 controls와 두 432-byte schedule
image를 byte-exact하게 검증했습니다. 상세 증거는
[`2026-09-02 앱-sequence 교정 실행`](docs/runs/2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9.md)에
있습니다.

이 기록 커밋부터 `9a51786`의 단회 해제는 **소진**되고 §1 동결이 다시 적용됩니다. 같은 질문의
오늘 두 번째 live operation이므로 §7에 따라 자동 재시도나 즉시 source 수정을 하지 않습니다.
질문은 다시 park하고 software-independent 제품 트랙을 진행합니다. native 최적화를 다시 열려면
먼저 master/slave의 staged mode·frequency 또는 boundary를 다르게 해 Sync runtime schedule
소유권을 구분하는 새 계획이 필요합니다. 그 결과 위에서만 inactive manual drift와 core Sync
권한을 분리한 ASYNC 계획을 세우며, repository maintainer의 새 승인, 별도 해제 커밋과 on-site
hardware approver 승인이 모두 필요합니다.

### 2026-09-03 Sync runtime schedule 소유권 단회 해제

repository maintainer는 commit
`b276c7ccb751a8b5a2f082dced331bc3be04f717`의
[`Native Sync schedule 소유권 최소 단회 계획`](docs/native-sync-schedule-ownership-recheck.md)과
§7의 네 대안을 검토한 뒤 “진행하자”라고 답해, **계획된 구현과 Sync-only 실장비 operation
1회**를 명시적으로 승인했습니다. 이 승인은 repository maintainer 권한이며, 첫 hardware write에
필요한 on-site hardware approver의 operation별 승인·현장 감시·중단 수단 확인을 대신하지 않습니다.

이 단회가 닫을 질문은 `master`와 `sync_slave`가 서로 다른 432-byte image를 보유할 때 slave의
protocol-reported schedule content와 전이 시각을 master/slave 중 누가 소유하는가입니다. cached
Pro 앱에서 `TimerON`을 먼저 arm한 뒤 Slave를 선택하는 경로는 partial `{Linkage:2}` 한 건만
보내며 별도 pairing·master id·schedule copy command가 없습니다. 따라서 같은 순서와 wire action을
사용하되, 앱 정적 trace를 firmware runtime 증거로 승격하지 않습니다.

고정 값과 시간은 다음뿐입니다.

- master safe manual `Constant / Flow 30 / F20`, A=`Sine / Flow 35 / F30`,
  B=`Constant / Flow 30 / wire F0`, boundary=`T`
- slave safe manual `Constant / Flow 31 / F20`, A=`Constant / Flow 32 / wire F0`,
  B=`Sine / Flow 40 / F30`, boundary=`T+5분`
- fixed signature `(35, 32, 30, 40, 30)`, Flow write 집합 `{30,31,32,35,40}`,
  temporary schedule·role authority의 guarded maximum `41`
- master boundary는 fresh device-local clock의 다음 정시 분 +7분, preflight lead
  `(360,420]초`, execution 직전 minimum lead `380초`
- `ScheduleFlowExperimentSpec.minimum_lead_seconds=60`, outer observation `915초`, reserve `15초`,
  complete epoch `900초`, post-boundary stability `30초`, boundary exclusion `±60초`
- frequency write 집합 `{0,20,30}`, recognised read allowlist `{0,5,20,30}`. Constant wire F0의
  보고 F5는 진단으로 보존하고 attribution을 바꾸지 않음

role write는 master=`Linkage=master` 한 번, slave=`Linkage=sync_slave` 한 번뿐입니다.
`async_slave` write는 **0회**입니다. 장비별 safe control·temporary schedule·TimerON 여섯 건과
두 role write를 합친 forward intended change는 정확히 여덟 건이며, 각 intended change는 durable
intent-before-write와 기존 exactly-once/no-resend 계약을 유지합니다.

이번 해제에서 허용하는 source와 직접 테스트는 다음으로 한정합니다.

1. `src/jebao_flow/devices/schedule_flow_experiment.py`
   - fixed signature, safe manual과 cap의 중복 상수를 `schedule_linkage.py`와 동시에 변경
   - 한 `sine_frequency=30`을 공유하는 반대 Mode A/B와 slave `T+5분` image 구성
   - outer slave role을 Sync-only로 한정하고 content outcome 의미를 재도출
2. `src/jebao_flow/devices/schedule_linkage.py`
   - exact fixed constants·allowlist·cap과 exact 5분 boundary offset
   - `SYNC_ONLY` durable sequence와 recovery allowed-set
   - fixed operation의 role 적용·capability 확인·monitor `expected_roles`·sample 생성에서만 slave
     role을 exact `sync_slave`로 사용하고, 일반 Async 경로의 `async_slave` 요구는 그대로 유지
   - `ScheduleLinkageSample.slave_linkage` literal과 별도 `full_linkage_topology` 조건을 fixed
     Sync-only에서 함께 확장
   - W0/W2 content evidence는 durable sample로, mixed-boundary W1은 기존 private raw로 보존
3. `src/jebao_flow/schedule_flow_experiment_cli.py`
   - 위 fixed 값, +7분 master boundary와 +5분 slave offset, run minimum lead 380초,
     post-boundary stability 30초와 정확한 operator 출력
4. `src/jebao_flow/hardware_test.py`
   - fixed Sync-only spec에서만 durable sample의 `sync_slave` topology를 허용
   - 기존 Async topology 검사는 그대로 유지하고 `OWN_SCHEDULE`을 Async 성공 boolean으로
     승격하지 않음
5. 위 네 source에 직접 대응하는 기존
   `tests/unit/test_schedule_flow_experiment.py`,
   `tests/unit/test_schedule_linkage_transaction.py`,
   `tests/unit/test_schedule_flow_experiment_cli.py`,
   `tests/unit/test_hardware_test.py`

`src/jebao_flow/devices/schedule_transaction.py`, `src/jebao_flow/devices/linkage.py`,
`src/jebao_flow/devices/lan.py`, `src/jebao_flow/schedule_linkage_cli.py`, persistence, 일반
daemon·MQTT, Home Assistant, physical binding·single-write·journal·rollback write path는 변경하지
않습니다. 새 범용 CLI, 새 pre-write firmware 가설 gate, fake firmware knob, runtime mixed-window
PASS gate를 추가하지 않습니다.

새 `ScheduleLinkageSlaveRoleSequence.SYNC_ONLY`는 staged `slave_role_progress`를 만들지 않습니다.
기존 master-first `linkage_write_intent_device_ids`와 `linked_device_ids`만 write 전후를 증명하며,
recovery allowed-set은 다음으로 제한합니다.

`SYNC_ONLY`는 `ScheduleLinkageRecord` validator에서 `DIRECT`와 같이 staged progress 없음으로
취급해 intent-progress 동시 durable 요구를 적용하지 않습니다. `_allowed_recovery_roles()`에서는
`SYNC_THEN_ASYNC` prefix 판정보다 앞에 전용 분기를 두며, 빈 progress가 두 경로의 기존
`sync-then-async` raise에 도달하지 않아야 합니다.

- slave intent 전 또는 slave detach 기록 뒤: `{independent}`
- slave intent가 durable하고 detach 전: `{independent, sync_slave}`
- 어느 prefix에서도 `async_slave`는 허용하지 않음

W0는 기존 fixed 계약대로 동일 evidence 10 pair·host span 120초·conflict 0을 요구합니다. W1과
W2는 label 2 pair·host span 30초·conflict 0을 요구합니다. evidence pair는 같은 fresh ordinal,
8초 acquisition authority, exact identity·roles·TimerON·schedule digest·cap, 그리고 두 frame의
device-local `NowTime`이 같은 분이라는 조건을 모두 만족해야 합니다. W0의 master는 exact A,
W1·W2의 master는 같은 pair에서 exact B여야 합니다. pair clock이 다른 분이면 raw·진단만 남기고
판정에서 제외합니다.

runtime durable content outcome은 W0와 W2가 일치할 때만 다음 named value로 기록합니다.

- slave가 양 창에서 master tuple을 보고: `FULL_MASTER_FOLLOW`
- slave가 양 창에서 자기 tuple을 보고: `OWN_SCHEDULE`
- exact manual tuple 유지: `COMMON_MANUAL_FLOW`
- W2에서 slave A 유지: `A_SLOT_HOLD`
- 창 불일치·field split·그 밖의 bounded tuple: `UNEXPECTED_EFFECTIVE_STATE`
- evidence 부족·conflict·decode/transport·restore 미확인: `UNKNOWN`

`PER_SLOT_POWER_VERIFIED`는 Async 성공 신호이므로 이번 실행에서 만들지 않습니다.
`OWN_SCHEDULE`이어도 legacy `schedule_transition_verified`는 **False**로 기록하고, named outcome과
stable sample이 Sync content 판정입니다. 어느 outcome도 물리 유량·파형 적용을 증명하지 않습니다.

W1의 보존 raw에는 plan commit이 사전 고정한 다음 timing×content 매핑만 적용합니다. 같은 pair의
master exact B가 없으면 W1 증거가 아닙니다.

- slave master B `Constant/30/F0|5`: `MASTER_TIMING_MASTER_CONTENT`
- slave slave B `Sine/40/F30`: `MASTER_TIMING_SLAVE_CONTENT`
- slave master A `Sine/35/F30`: `SLAVE_TIMING_MASTER_CONTENT`
- slave slave A `Constant/32/F0|5`: `SLAVE_TIMING_SLAVE_CONTENT`
- manual `Constant/31/F20`: `MANUAL_ECHO`
- Mode와 Flow가 서로 다른 owner 후보를 가리킴: `FIELD_SPLIT`
- 그 밖의 bounded tuple 또는 자격 미달: `UNEXPECTED_EFFECTIVE_STATE` 또는 `UNKNOWN`

단일 Sine F30 때문에 Frequency는 Mode family만 구분하고 owner 축은 Mode·Flow로 판정합니다.
role-entry mirror가 slave A를 manual DP에 복사했다면 slave A와 manual 반향은 구조적으로 같으므로,
`SLAVE_TIMING_SLAVE_CONTENT`를 firmware schedule engine과 manual 반향까지 구분한 것으로 쓰지
않습니다. raw frame은 등급 (a), label은 그 raw에 사전 고정 규칙을 적용한 도출로 분리합니다.

fixed monitor의 기존 fresh-session transport는 변경하지 않습니다. pair마다 paired
disconnect/connect 뒤 participant별 report-capable capture 1회, acquisition authority 8초,
다음 acquisition 전 pause 10초, 같은 ordinal retry 0회, 연속 실패 3회 종료와 형제 raw 보존을
유지합니다. action `0x04`는 explicit reply나 ACK로 부르지 않고, 최종 기록에는 `0x03`/`0x04`,
pair refresh·connect/auth 횟수와 allow-listed transport exception class를 분리합니다.

첫 hardware write 뒤 조기 ordered recovery 조건은 다음 다섯 가지뿐입니다.

1. physical identity binding 불일치
2. temporary schedule·native role authority 아래 실제 또는 보고된 Flow가 `41` 초과
3. 펌프·수조의 구체적 위험 동작
4. durable journal·rollback 권한·현장 중단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

bounded unexpected tuple·action `0x04`·단일 read 오류는 write 재전송 사유가 아닙니다. 기존
ordered restore는 slave detach -> master detach -> safe TimerOFF/independent -> 원 432-byte
images -> 원 outer controls/TimerON을 유지합니다. 원 slave latent manual Flow 89는 temporary cap과
섞지 않고 기존 한 guarded outer-control+TimerON frame에서만 복원합니다. 정상 복원 후 원
AutoFlow에는 temporary cap 41을 적용하지 않습니다.

source commit 전 직접 unit/fault-injection 테스트와 전체 suite, Claude의 read-only COMMIT_OK를
받습니다. exact commit의 Linux/amd64 image와 revision·signature·cap을 대조하고, device write 0인
fresh baseline collector로 physical binding·원 controls·두 images를 보존합니다. 실제 첫 write는
on-site hardware approver가 이 operation을 승인하고 현장에서 감시·중단할 수 있으며 다른
writer·Observer·recovery supervisor가 없고 attended lease가 하나일 때만 시작합니다. Home
Assistant는 재시작하거나 조작하지 않습니다.

새 operation id와 fresh boundary로 **한 operation만** 실행합니다. 결과와 무관하게 terminal
ordered restore, 서로 다른 두 source-attested fresh collector의 원 controls·두 image byte-exact
검증, append-only `docs/runs/` 기록 커밋이 끝나면 이 해제는 자동 소진되고 §1 동결이 다시
적용됩니다. 비-terminal intent가 남으면 새 실험·fixed 상수 변경을 금지하고 이 exact source/image의
기존 attended recovery와 read-only 검증만 허용합니다.

### 2026-09-03 Sync-only 단회 해제 미사용 종료와 앱 주도 경로 전환

repository maintainer는 위 Sync-only 구현 준비 중, 제바오 앱에서 장비별 schedule을 직접
저장하고 `master`/`async_slave`로 전환한 뒤 read-only raw를 관측하는 경로를 우선하기로
결정했습니다. 2026-09-03에 “진행해”라고 명시적으로 승인했으며, 현재 두 장비의 432-byte
schedule image를 장비별 원본 바이트로 복원하는 것을 최우선 조건으로 다시 확인했습니다.

commit `9c9be19ad8fd224fac6bc57485a2121b59ace624`의 Sync-only 해제는 hardware write 없이,
source commit 없이 **미사용 종료**합니다. 이 기록부터 위 네 frozen source와 직접 테스트에는
§1 동결을 다시 적용하며, `9c9be19`를 이후 Sync write나 미완성 구현의 권한으로 재사용하지
않습니다. 준비 중이던 미커밋 prototype은 별도 stash에 격리됐고 현재 tree나 실행 image의
구성요소가 아닙니다.

새 앱 주도 계획은
[`Native app-driven ASYNC single-transition observation`](docs/native-app-async-single-transition-observation.md)
을 따릅니다. 앱 live-write는 §1 동결 대상이 아니지만 별도의 attended operation이며, 앱 write
전에 qualified standalone exact restore와 report-capable write-free collector, 서로 다른 두
fresh baseline의 byte-exact 교차확인이 모두 필요합니다. 이 계획을 위한 `exact_restore*`와
별도 read-only collector 변경은 frozen native schedule/linkage harness를 import·호출·노출하지
않는 범위에 한정합니다.

`exact_restore*`는 §1의 native ASYNC write 하네스 동결 범위 밖에 있는 별도 attended 도구입니다.
그 도구가 장비에 write하는 Phase 1 qualification과 Phase 4 final restore는 각각 고유 operation
id, on-site hardware approver의 해당 operation 승인·현장 감시·중단 수단 확인, 대면 attended
grant를 모두 요구합니다. 앱 operation의 승인이나 아래 `Flow 89` 수용을 이 두 grant로 대신하지
않습니다.

repository maintainer 겸 on-site hardware approver는 역할 B의 manual `Random / Flow 89 /
Frequency 34`가 과거 standalone 시험의 미사용 잔여값이라고 기억한다고 확인했고, 이번
operation의 exact restore 중 그 값이 잠시 active가 될 가능성을 수용했습니다. 값 자체는
preserved raw 등급 (a), 발생 원인과 수용 결정은 등급 (c)로 분리합니다. 이 operation별 수용은
다른 operation의 power authority로 이월하지 않습니다.

**Home Assistant는 이 경로에서 재시작·reload·disable·설정 변경·service call하지 않습니다.**
first write 전 HA의 직접 Jebao connection이 관측되면 에이전트가 이를 끄지 않고 `NO-GO`로
멈춰 repository maintainer에게 보고합니다.

### 2026-09-04 exact-restore final verifier 후속 범위

repository maintainer는 parked qualification 기록 뒤 “다음 단계 진행하자”고 지시했습니다. 이
지시는 [`Exact-restore final verification bound split and parked disposition`](docs/exact-restore-final-verification-and-park-plan.md)의
계획·source·test·offline 검증을 진행하는 승인입니다. 장비 network 접근, 앱 action, Home
Assistant 조작, current journal terminalization 실행이나 새 qualification hardware write 승인은
아닙니다.

source 범위는 `exact_restore.py`, `exact_restore_composition.py`, `exact_restore_receipts.py`,
`exact_restore_cli.py`, 별도 write-free `exact_restore_disposition.py`와 직접 테스트로 한정합니다.
completion coherence와 acquisition duration 분리, legacy canonical 호환, 성공과 구분되는
`PARKED_UNQUALIFIED` archive, 잠정 journal tombstone과 영구 version `2` operation-finalization
replay 차단만 다룹니다. native ASYNC write 하네스,
`read_only_collector.py`, `q2_epoch.py`, app verifier, HA/daemon/MQTT는 수정하지 않습니다.

current journal은 plan/source review 동안 그대로 보존합니다. terminalization은 reviewed exact image의
별도 device-network-free command와 repository maintainer의 journal·evidence digest-bound 대면
challenge가 있어야 하며, success receipt나 qualified bundle을 만들지 않습니다. 그 뒤 새
qualification은 영구 replay 장벽을 재검증한 reviewed tombstone 교체, fresh baseline·새 operation
id와 별도의 on-site hardware 승인을 다시 요구합니다. parked tombstone 구간에는 현재 exited인
recovery supervisor를 재기동하지 않으며 Home Assistant는 계속 건드리지 않습니다.

### 2026-09-08 local-first 제품 경로 부분 해제

repository maintainer는 write-free packet observatory와 inert Sync/Async schedule 모델을 담은
commit `e2ae787b1b5fe649b52d82475940a1d10711327d`의 완료 보고와 아래 범위를 확인한 뒤
“ㅇㅇㅇ 확인 했어 진행하자”로 **신규 local-control 제품 경로의 구현 재개를 명시 승인**했습니다.
짧은 답문의 범위는 그 직전 메시지가 제시한 아래 경계에 결속합니다.

이번 부분 해제는 다음 신규 경로에만 적용합니다.

- `src/jebao_flow/local_control/`과 그 직접 CLI·Home Assistant/MQTT adapter 및 직접 테스트
- 등록된 다섯 제품군에서 정적 catalog와 보존 capture로 확인된 앱 기능의 local equivalent
- Local Wavemaker Pro의 Sync/Async desired state: master의 48-position schedule vector를 각
  slave가 그대로 상속하고, active slot마다 slave별 Flow override를 독립 적용하는 경로
- 설치 시 현재 환경에서 변수를 다시 수집하고 이전 manifest를 읽거나 병합하지 않는 배포 경로

기존 §1의 여섯 frozen source 파일과 그 단위 테스트는 **계속 동결**합니다. 신규 경로는 그
하네스를 import·호출·복제하지 않으며, `operator_capture`의 write-free import boundary도 유지합니다.
새 write edge는 별도 `local_control/device_writer.py`에 격리하고 control encoder/session import는
그 파일과 직접 transport adapter 밖으로 퍼뜨리지 않습니다.
신규 write는 별도의 local-control write-ahead transaction journal을 사용합니다. 기존
exact-restore journal의 내용을 읽거나 교체·진행하지 않으며, shared operation lease 아래에서 그
workflow가 이미 terminalize되어 live journal이 부재함을 확인하기 전에는 local-control write를
시작하지 않습니다. `local_control`의 transitive import-closure 회귀는 기존 여섯 frozen 파일이
없고 control encoder/session import가 `device_writer.py`와 지정 transport adapter에만 있음을
기계적으로 고정합니다.
여기서 shared lease는 기존과 같은 `global_operation_lock_path()`의 deployment-wide lock과
`physical_lock_directory()` 아래의 `physical_identity_key(binding)` 장비별 lock입니다. 새 lock
namespace를 만들지 않으며 기존 경로와 신규 경로가 같은 물리 장비에 대해 같은 key를 유도하는지
회귀로 고정합니다. `configuration_fingerprint`나 그 scope 문자열은 `physical_identity_key`의
입력이 아니므로 lock identity 제약으로 승격하지 않습니다.

구현 재개 조건은 다음과 같습니다.

1. 영구 장비 결속은 `physical_identity_key`·`product_key`·operator-confirmed logical role만
   사용합니다. PID, IP, ephemeral port, hostname, container/session/cache path, file mtime과 이전
   자동탐지 결과는 mapping·승인 gate로 사용하지 않습니다. logical role의 현장 확인은 증거등급
   (c)와 확인 역할·시점을 기록합니다.
2. 앱이나 firmware 의미는 preserved raw capture 또는 기존 audited protocol fact에 결속합니다.
   근거 없는 firmware 가설은 거부 조건이나 fake 동작으로 만들지 않고 `UNKNOWN` 진단으로 둡니다.
3. Flow write는 저장소 안전 envelope `30..75`, physical identity, single-write, durable journal과
   rollback authority를 계속 지킵니다. Home Assistant의 native range도 `30..75`로 공개해 UI가
   더 넓은 앱 범위를 지원한다고 주장하지 않습니다. 이 부분 해제는 해당 안전 규칙의 완화가
   아닙니다.
4. 앱 표현 가능성과 device support를 분리합니다. control은 승인된 ACK·readback 증거로
   qualification된 capability만 승격하며, 앱의 optimistic UI를 성공 증거로 쓰지 않습니다.
5. 실제 장비 write·앱 조작·Home Assistant 연결은 이 구현 승인에 포함되지 않습니다. 각각
   `docs/hardware-readiness.md`의 fresh preflight, exact restore, on-site 승인과 중단 수단이 필요합니다.

정보 0 실기 또는 같은 미확정 질문의 반복은 §7 예산을 그대로 적용해 park합니다. 이번 해제는
신규 local-control 제품 경로를 지속적으로 구현·검토할 권한이며, 기존 frozen 하네스를 되살리거나
과거 단회 해제를 재사용할 권한은 아닙니다.

### 2026-09-08 plan-atomic 그룹 런타임 범위 확장

repository maintainer는 atomic software group actuation과 power-off recovery 정정을 각각 담은
commit `da647e3a5c727a7bc93b6039f85989d583619a71`과
`cac0535199a63d623f18fad04cf928b1f475e69a`의 완료 보고 뒤 “다음 단계 진행하자”로
**plan-atomic recurring group runtime의 source·test·offline 검증 진행을 명시 승인**했습니다.
이 짧은 답문의 범위는 두 commit의 완료 보고와 함께 제시된 다음 단계의 plan-level runtime,
full-plan port, journal·generation 경계 제안에 결속합니다.
이번 작업이 닫을 질문은 서로 다른 generation의 target을 하나의 물리 transaction으로 섞지 않고,
delivery가 불확정인 write를 재시도하지 않으며, mandatory canonical OFF를 supersede·shutdown에서도
보존할 수 있는가입니다. 근거는 firmware 가설이 아니라 plan identity·journal·동시성의 static trace와
fault injection입니다.

`src/jebao_flow/groups/runtime.py`는 §1 동결 대상이 아니므로 이 승인은 §1 동결 해제가 아닙니다.
commit `c40e6b33462640a349326d88fd50c92b46f551a6`가 부여한 local-first 제품 경로의 구현 범위를
다음 파일로만 확장합니다.

- `src/jebao_flow/local_control/`
- `src/jebao_flow/local_control_cli.py`
- `src/jebao_flow/groups/runtime.py`와 그 직접 단위 테스트
- `tests/unit/test_local_control_*`
- `docs/local-control.md`

기존 여섯 frozen source와 그 직접 테스트, `src/jebao_flow/protocol/*`,
`src/jebao_flow/devices/*`, `docs/runs/*`는 수정하지 않습니다. 기존 per-device
`GroupDispatchRuntime`의 callback을 물리 write barrier로 묶지 않고, 완전한 plan identity를 받는
별도 plan-level runtime/port 계약을 사용합니다. 물리 적용은 기존 `execute_batch`의 one lease,
one durable journal record, physical-identity-keyed resolution, reverse recovery와 qualification 경계를
재사용하며, generation·timestamp를 장비 identity나 승인 gate로 승격하지 않습니다.
기존 per-device `GroupDispatchRuntime`의 generation fencing, `(control_epoch, target)` dedupe,
mandatory canonical OFF 보존, drain·shutdown retry 동작과 이를 고정한 기존 단위 테스트 단정은
유지합니다. `groups/runtime.py` 변경은 별도 plan-level 계약을 추가하는 가산적 변경에 한정하며,
기존 단정을 완화하거나 삭제하려면 별도 repository maintainer 승인이 필요합니다.

supersede는 rollback으로 구현하지 않습니다. superseded transaction은 이미 확인된 물리 상태를
되돌리지 않고 종결하며, recovery는 실패했거나 delivery가 불확정인 transaction을 위한 별도
operation으로 남깁니다. durable record는 물리 frame 이전의 guard 거부와 delivery 불확정을
구분합니다. 전자는 이후 transaction을 차단하지 않는 terminal이고, 후자는
`RECOVERY_REQUIRED`로 운영자 해결을 요구합니다. 불확정 action이 하나라도 있는 record는
superseded terminal로 종결할 수 없습니다.

commit `cac0535`의 power-off recovery 의미 정정은 safety gate 완화가 아닙니다. 이미 OFF인 장비를
덜 안전한 이전 ON 상태로 되돌리는 것은 recovery가 아니므로 재활성화 frame을 보내지 않되, fresh
read가 forward safe state나 baseline 어느 쪽도 증명하지 못하면 transaction을 terminal로 가장하지
않고 `RECOVERY_REQUIRED`로 유지합니다.

실제 장비 write·앱 조작·Home Assistant 연결은 이 범위 확장에 포함되지 않습니다. 각각
`docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장 감시·중단 수단을
별도로 갖춰야 합니다.
같은 동시성·journal 질문을 반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-08 명시 명령 전 write-disarmed 그룹 supervisor 범위 확장

repository maintainer는 plan-atomic recurring runtime commit
`4f34447d09329f7c627b264831b2dd025ae4e1c8`의 완료 보고와 다음 supervisor 경계 제안에
“ㅇㅇㅇ 끝까지 일단 진행해”로 **source·test·offline 검증 진행을 명시 승인**했습니다. 이번 작업이
닫을 질문은 config의 group·member `enabled` 값과 무관하게 supervisor 객체 구성 뒤 명시
command 전 physical write를 0회로 유지하면서, fresh availability와 명시 command를 한 순서로
반영하고 변화 없는 mandatory OFF를 주기적으로 재전송하지 않는 supervisor lifecycle을
만들 수 있는가입니다. 근거는 firmware 가설이 아니라 planner generation·runtime submit 경계의
static trace와 fault injection입니다. 이 slice에서는 어떤 daemon이나 CLI도 supervisor를 구성하지
않으며 서비스 기동 lifecycle은 주장하지 않습니다.

이 확장은 다음 파일에 한정합니다.

- `src/jebao_flow/local_control/` 아래의 별도 group supervisor
- `tests/unit/test_local_control_*`
- `docs/local-control.md`

`src/jebao_flow/groups/manager.py`, `src/jebao_flow/groups/plan.py`,
`src/jebao_flow/groups/runtime.py`와 기존 직접 테스트, `src/jebao_flow/mqtt/*`,
`src/jebao_flow/app.py`, `custom_components/*`, `src/jebao_flow/protocol/*`,
`src/jebao_flow/devices/*`, 기존 여섯 frozen source와 그 직접 테스트, `docs/runs/*`는 수정하지
않습니다. supervisor는 기존 `GroupManager`, `PlanAtomicGroupDispatchRuntime`,
`SoftwareIndependentGroupActuator`를 조합하되 planner나 dispatcher 의미를 바꾸지 않습니다.
supervisor가 submit하는 모든 plan은 `GroupManager.plan_tick()`이 반환한 그 객체여야 하며,
`GroupTickPlan`을 직접 구성하지 않습니다.

명시 command가 supervisor의 ownership을 활성화하기 전에는 `plan_tick()`으로 진단용 plan을
계산할 수 있어도 `PlanAtomicGroupDispatchRuntime.submit()`을 호출하지 않습니다. 이 불변식은
config의 `enabled: false`, group state `STOPPED` 또는 member `enabled: false`에 기대지 않습니다.
이 값들은 모두 canonical OFF dispatch를 만들 수 있기 때문입니다. 활성화 뒤에는 대기 중인
availability와 command를 먼저 직렬 반영하고 그 다음 `plan_tick()`·`submit()`을 한 순서로
수행합니다. freshness 실패는 device offline 주장으로 바꾸지 않고 `online=None`으로 유지합니다.

변화 없는 mandatory-OFF plan은 주기 tick마다 다시 submit하지 않고, 명시 command 또는
ownership 전이에서만 제출합니다. 이 규칙을 위해 mandatory-OFF의 runtime 내부 dedupe 우회를
약화하지 않습니다. availability가 plan identity와 control epoch를 이미 바꾸는 경우 별도의
applied-cache invalidation을 중복 적용하지 않으며, fresh read와 applied target의 불일치처럼 아직
탐지 경로가 없는 외부 drift는 새 gate로 만들지 않고 `UNKNOWN`으로 문서화합니다. group 외부의
개별 control은 이 slice에서 supervisor command로 연결하지 않습니다.

회귀는 최소한 (1) group enabled, group disabled, member disabled 구성 각각에서 명시 command 전
여러 tick의 `GroupPlanPort.write_plan()` 호출 0회, (2) 명시 command 뒤 최초 plan의 같은 호출
정확히 1회, (3) 동일 일반 plan과 동일 mandatory-OFF의 반복 tick에서 추가
`GroupPlanPort.write_plan()` 호출 0회, (4) `plan_tick()`과 submit 사이의
availability 변경이 stale plan을 폐기하고 다음 tick으로 수렴함을 고정합니다. supervisor 자체는
Home Assistant entity나 MQTT command를 노출하지 않으며, 그 transport 연결은 별도 검토·승인
slice로 남깁니다.

실제 장비 write·앱 조작·broker 또는 Home Assistant 연결은 이 구현 승인에 포함되지 않습니다.
실제 활성화에는 `docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장
감시·중단 수단이 별도로 필요하고, 하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`은 유지합니다.
같은 lifecycle·submit 질문을 반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 production group lifecycle 연결 범위 확장

repository maintainer는 write-disarmed group supervisor commit
`0b4d037a152ff4e7e8ca29527cdb3c8254ab7a83`의 완료 보고와 production daemon/MQTT/Home
Assistant 연결이 다음 단계라는 설명 뒤 “이어서 진행하자”로 **첫 production lifecycle 연결의
source·test·offline 검증 진행을 명시 승인**했습니다. 이번 slice는 그 승인보다 좁게, current
`serve`가 supervisor inventory를 구성하고 완전한 fresh-state cycle을 전달하는 단계까지만
다룹니다. MQTT group command와 Home Assistant group entity는 아직 연결하지 않습니다. 실제 broker,
Home Assistant, 장비, 앱 또는 원격 환경을 실행하는 승인도 아닙니다.
이번 작업이 닫을 질문은 기존 read-only device-state service의 publish·availability·startup 의미를
바꾸지 않고 production lifecycle에 격리된 group supervisor inventory와 complete fresh-cycle feed를
추가할 수 있는가입니다. 근거는 firmware 가설이 아니라 construction·callback·shutdown 경계의 static
trace와 fault injection입니다.

이 확장은 이전 하위절의 “어떤 daemon이나 CLI도 supervisor를 구성하지 않는다”는 현재 구현
진술을 대체합니다. supervisor 내부의 **명시 command 전 write-disarmed** 구조는 그대로이고, 이번
slice에는 그 command의 production 호출자도 없으므로 물리 write는 0회입니다. 첫 보장은 다음 transport
slice에도 남고 두 번째 보장만 그때 별도 검토합니다. `LocalControlGroupSupervisor.apply_runtime_command()`를
묶는 내부 API는 존재할 수 있지만 이번 slice의 CLI·MQTT·Home Assistant 어느 경로에서도 호출하지
않습니다.

허용 범위는 다음 파일로 한정합니다.

- 신규 `src/jebao_flow/local_control/group_service.py`
- `src/jebao_flow/local_control/state.py`, `src/jebao_flow/local_control/mqtt.py`
- `src/jebao_flow/local_control_cli.py`
- `tests/unit/test_local_control_*`
- `docs/local-control.md`

`src/jebao_flow/groups/*`, 기존 `src/jebao_flow/local_control/group_supervisor.py`,
`src/jebao_flow/local_control/group.py`, `coordinator.py`, `lease.py`, `device_writer.py`,
`src/jebao_flow/mqtt/*`, `src/jebao_flow/app.py`, `custom_components/*`,
`src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`, 기존 여섯 frozen source와 그 직접 테스트,
`docs/runs/*`는 수정하지 않습니다. 기존 old MQTT/group service나 `devices.*` observer를 import하거나
우회 write 경로로 재사용하지 않습니다.
`state.py`·`mqtt.py`·`local_control_cli.py` 변경은 기존 read-only device-state 경로에 대한 가산적
변경으로 한정합니다. `tests/unit/test_local_control_state.py`,
`tests/unit/test_local_control_mqtt.py`, `tests/unit/test_local_control_cli.py`의 기존 단정은 완화하거나
삭제하지 않으며, 필요하면 별도 repository maintainer 승인을 먼저 받습니다.

각 group은 서로 독립적으로 구성합니다. native-linked strategy, 미지원 제품, 미구현 pattern,
`fallback_constant`, current manifest 결손, 중복 physical identity, 안전 Flow 교집합 부재 또는 기타
구성 실패는 stable English `diagnostic_reason`을 가진 unconstructed inventory 항목으로 남기며 다른
group이나 기존 device-state service의 기동을 막지 않습니다. group 하나의 planning/tick 예외도
fresh poller·MQTT adapter task로 전파하지 않고 해당 group 진단으로만 남깁니다. read-only `serve`는
가짜 no-write port를 만들지 않고 supervisor 0개와 그 이유를 보고합니다. 실제 supervisor와 actuator는
current `--write-enabled` coordinator가 존재할 때만 구성합니다.
`fallback_constant`는 현재 planner가 unavailable member를 처음 만날 때까지 실패가 잠복하므로 구성
시점에 미지원 진단으로 분류합니다. cycle 예외 격리 회귀는 이 정책 대신 별도 fault injection으로
검증합니다.

per-device `DeviceActuationLimits`는 current private config의 device limit와 선언된 local-control Flow
안전 봉투 `30..75`의 교집합으로 유도합니다. group-level effective limit와 device 봉투를 합치지 않고,
config에 없는 firmware step을 추측하지 않으며 현재 planner capability의 기본 step을 그대로 사용합니다.
device-state publication이 모두 성공한 fresh cycle마다 정확히 한 번, 일부가 아닌 전체 결과 tuple을
inventory에 전달합니다. device-state publish가 실패하면 callback만 조용히 누락한 채 다음 cycle로
계속하지 않고 cycle loop 자체를 종료합니다. 각 supervised group은 자기 member를 logical key로 골라
`observe_fresh_cycle()`을 한 번만 호출하며 별도 `tick()`을 겹쳐 호출하지 않습니다. planner 시간은
monotonic clock을 사용하고 `observed_at` wall clock을 generation·identity·gate로 사용하지 않습니다.

모든 group plan port는 기존 device router·fresh poller와 같은 process `operation_lock` 아래에서 물리
경계를 통과하도록 구성합니다. 이 slice에는 group을 arm하는 production caller가 없어 frame이 나갈 수
없지만, 다음 transport 연결이 group 자신의 write를 concurrent read로 오인해 control epoch을 바꾸지
않도록 지금 같은 직렬화 namespace를 고정합니다. 반면 group 밖의 개별 device command가
`GroupManager.set_manual_override(device_id)`를 거치는 결합은 group command transport를 여는 다음
slice의 선행조건으로 남깁니다. 같은 프로세스가 낸 device write를 external drift `UNKNOWN`으로 숨기지
않습니다.

종료는 구성된 supervisor 전부에 `aclose()`를 시도한 뒤 결과를 검사합니다. 한 supervisor의 실패가
나머지 close를 생략해서도 안 되고, `return_exceptions=True` 집계가
`UnresolvedGroupSafetyStopError`나 다른 close 오류를 삼켜서도 안 됩니다. unresolved mandatory OFF는
다른 종료 오류보다 우선해 adapter 밖으로 다시 전파되어야 하고, 반복 close에서도 clean success로
바뀌지 않습니다.

회귀는 최소한 (1) group enabled·group disabled·member disabled 각 구성의 여러 complete fresh cycle에서
`GroupPlanPort.write_plan()` 0회, (2) 구성 불가 group이 있어도 기존 device state publish와 service
startup 유지, (3) group tick 예외가 poller task를 종료하지 않음, (4) 전체 discovery 실패 cycle에서
write 0회, (5) supervisor 두 개 중 하나만 unresolved mandatory OFF여도 둘 다 close되고 오류 재전파,
(6) read-only `serve` supervisor 0개를 고정합니다. inventory와 status에는 logical group id·bounded
diagnostic만 내보내고 physical identity나 private path를 노출하지 않습니다.

실제 장비 write·앱 조작·broker 또는 Home Assistant 연결은 이 구현 승인에 포함되지 않습니다. 실제
활성화에는 `docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장 감시·중단
수단이 별도로 필요하고, 하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`은 유지합니다. 같은 production
lifecycle 격리 질문을 반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 fresh group output·device manual ownership 결속 범위 확장

production group lifecycle commit `b2f9828678f0a12811d6c2f2f6f0714f87e52cca`의 완료 보고와 함께 다음
단계를 production daemon·MQTT·Home Assistant 연결로 제시한 직후, repository maintainer는
“이어서 진행하자”로 답했습니다. 이번 B1은 그 특정 답변에 **source·test·offline 검증 범위로 좁게
결속**하며, 직전 하위절이 남긴 별도 검토·승인 요구를 이 범위에서 충족합니다. 최초 group command는
바로 열지 않고 두 slice로 나누며, B1은 decoded device truth의 read-only group output과 기존
direct-device command의 manual ownership 결속까지만 다룹니다. group ON/OFF command는 별도 B2 정책과
검토 뒤에만 열고, 실제 broker·Home Assistant·장비·앱·원격 환경 실행 승인이 아닙니다.

이번 작업이 닫을 질문은 qualification이나 desired state와 무관한 fresh decoded power를 config-relative
group output으로 과장 없이 투영할 수 있는가, 그리고 기존 direct-device command가 group manager에
manual ownership을 먼저 기록해 일반 group plan의 frame-before-send guard를 무효화하면서도 새 group
write caller를 만들지 않을 수 있는가입니다. 근거는 firmware 가설이 아니라 decoded status dataflow,
manager generation guard의 static trace와 fault injection입니다.

허용 범위는 다음 파일로 한정합니다.

- `src/jebao_flow/local_control/group_service.py`, `group_supervisor.py`
- `src/jebao_flow/local_control/state.py`, `ha.py`, `mqtt.py`
- `src/jebao_flow/local_control_cli.py`
- `tests/unit/test_local_control_*`
- `docs/local-control.md`

이 목록은 직전 하위절에서 무변경으로 둔 `group_supervisor.py`와 아직 열지 않았던 `ha.py`를 이번 B1에
한해서 명시적으로 다시 엽니다. `src/jebao_flow/groups/*`, `src/jebao_flow/local_control/group.py`,
`coordinator.py`, `lease.py`, `device_writer.py`, `journal.py`, `src/jebao_flow/mqtt/*`,
`src/jebao_flow/app.py`, `custom_components/*`, `src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`,
기존 여섯 frozen source와 그 직접 테스트, `docs/runs/*`는 수정하지 않습니다. old MQTT/group service나
`devices.*` observer를 import하거나 우회 write 경로로 재사용하지 않습니다.

이번 source 변경은 기존 read-only device-state와 group supervisor/runtime 의미에 대한 가산적 변경으로
한정합니다. `tests/unit/test_local_control_group_supervisor.py`,
`test_local_control_group_service.py`, `test_local_control_state.py`, `test_local_control_ha.py`,
`test_local_control_mqtt.py`, `test_local_control_cli.py`의 기존 단정을 완화하거나 삭제하지 않으며, 필요하면
별도 repository maintainer 승인을 먼저 받습니다. supervisor의 write-disarmed startup, disposition 다섯
종, exact manager-produced plan provenance, mandatory OFF 보존과 sticky close 의미를 그대로 유지합니다.
`group_supervisor.py` 변경은 manual-ownership 접근에 필요한 최소 가산 변경에 한정하고 arming·disposition·
close 의미를 바꾸지 않습니다.

typed power truth는 이미 한 번 수행한 product schema `decode_status()` 결과와 catalog descriptor의 field를
직접 결속해 유도합니다. 새 device I/O나 두 번째 decode를 추가하지 않고, HA control exposure 또는
qualification 여부와 무관하게 읽을 수 있어야 합니다. unavailable state에는 typed truth를 싣지 않고,
field가 없거나 bool이 아니면 추측하지 않고 group output을 `unknown`으로 둡니다.

group actual output은 complete fresh cycle의 물리 power truth만 뜻합니다. member 하나라도 unavailable이거나
power가 없으면 `unknown`, 모든 member가 OFF면 `off`, config에서 enabled인 member가 모두 ON이고 disabled인
member가 모두 OFF면 `on`, 나머지는 `mixed`입니다. manual override 여부는 이 네 값 계산에 섞지 않고
bounded logical `manual_override_members`와 ownership 진단으로 분리합니다. config 상대적 판정임을 current
install generation과 함께 표시합니다. `on`은 power 집계일 뿐 current Flow·pattern이 계획과 일치한다는
판정이 아니며, scalar target drift는 계속 `UNKNOWN`입니다.

Home Assistant에는 이 actual output을 읽는 command-free diagnostic sensor만 가산적으로 추가합니다.
group output state는 direct command result나 desired runtime으로 낙관 갱신하지 않고 successful fresh cycle
뒤에만 publish합니다. unknown이면 group entity availability를 current로 주장하지 않습니다. 이번 B1에서
group command topic·Start/Stop button·switch를 만들지 않고,
`LocalControlGroupSupervisor.apply_runtime_command()`의 production caller는 계속 0개입니다.

기존 direct-device MQTT route는 payload를 먼저 parse하고 대상 member의 supervised group ownership 상태를
확인한 뒤, 같은 process `operation_lock`을 유지한 채 아래 규율로 기존 coordinator write를 수행합니다.
scalar와 direct schedule route를 모두 같은 규율로 처리합니다. invalid payload는 ownership이나 journal을
만들지 않습니다. `EMERGENCY_STOP`이 아닌 경우 manual declaration은
`GroupManager.set_manual_override()`로 generation·control epoch만 전진시키고 새 group plan을 즉시 submit하지
않습니다. 그 generation 변화만으로 이미 대기 중인 ordinary plan의 `manager.is_current` pre-send guard가
false가 됩니다. old ordinary plan은 shared lock을 다시 얻은 뒤에도 frame 직전 pre-send guard에서 폐기되어
direct value를 덮지 않습니다. override는 운영자가 direct ownership을 선택했다는 뜻이므로 이후 coordinator
preflight가 frame 전에 거부돼도 유지합니다.

direct/group ownership 충돌은 다음 네 경우를 구분하며 `EMERGENCY_STOP` 규율이 항상 우선합니다. unarmed
group과 armed ordinary group은 manual override를 기록한 뒤 direct write를 허용합니다. armed group 중
state가 `EMERGENCY_STOP`이 아닌 경우에는 state가 `STOPPED`이거나 config에서 disabled여도 모든 direct
write를 허용하되, coordinator가 `VERIFIED`를 반환한 direct `power=true`의 command result와 group
diagnostic에만 stable English reason `group_forced_off_will_revert_next_cycle`을 싣습니다. 다음 successful
fresh cycle의 전체 canonical OFF가 power를 강제로 OFF로 만들어 물리 출력을 바꿀 수 있다는 뜻입니다.
canonical OFF는 direct `manual_flow`나 schedule scalar 자체를 rollback하지 않으므로 그 명령에는 이 reason을
붙이지 않고, 그 값의 향후 장비 적용은 추측하지 않은 채 `UNKNOWN`으로 둡니다. preflight 실패나 non-verified
direct `power=true`도 reason·watermark를 만들지 않습니다. 그때의 물리 상태는 다음 fresh cycle의 actual
output으로 드러내고, durable uncertain record가 생긴 경우에는 기존 operator-resolution 경로로만
해결합니다. group plan이 그 record를 recovery나 PASS로 대체하지 않습니다. 이를 조용히 숨기거나 유지
보장으로 표현하지 않습니다.
armed `EMERGENCY_STOP`에서는 stable reason `group_emergency_stop_blocks_direct_control`로 coordinator와
durable journal 이전에 거부하고 physical frame을 0회로 유지합니다. 이 branch는 manual override도 기록하지
않습니다. 거부된 command가 control epoch을 올려 다음 fresh cycle의 mandatory OFF 재write를 만드는 경로를
차단하기 위해서입니다. 국소 direct write가 emergency stop을 무력화할 수 없다는 기존 안전 의미를
보존하는 것이며, `STOPPED`까지 같은 거부로 넓히지 않습니다. 두 stable reason은 예외 class 이름에서
유도하지 않고 command result·status에 위 문자열 그대로 publish합니다.

STOPPED/disabled group에서 verified `power=true` direct write가 반복돼도 첫 선언과 같은 OFF 재확인 의미를
유지합니다. router는 coordinator가 `VERIFIED`를 반환한 뒤에도 같은 process `operation_lock`을 쥔 상태에서
그 group runtime의 현재 `safety_off_succeeded_count`를 supervisor-local watermark로 기록합니다. unchanged
mandatory-OFF 억제는 watermark가 없거나 이후 mandatory OFF 성공으로 counter가 watermark를 초과했을 때만
허용합니다. lock을 기다리던 기존 mandatory OFF가 direct write 뒤 성공했다면 그 기존 성공이 watermark를
초과하므로 새 OFF를 중복 전송하지 않고, 그런 성공이 없었다면 다음 complete fresh cycle이 OFF plan을
미해결 safety token이 없는 범위에서 정확히 한 번 제출합니다. 이 watermark를 만들기 위해
`runtime.invalidate_applied()`를 호출하거나 ownership을
toggle하거나 새 group plan을 즉시 submit하지 않습니다. non-verified direct result, direct preflight 실패,
`power=false`, `manual_flow`, schedule operation은 watermark를 만들지 않습니다. mandatory OFF의 실패·취소·
불확정·supersede·in-flight invalidation은 기존 runtime counter를 증가시키지 않으므로 reconciled로 가장하지
않고, outstanding safety token이 있으면 기존 runtime의 deferred·operator-resolution 의미를 그대로 따릅니다.

manual override는 이미 수락된 mandatory canonical OFF의 safety token을 취소하지 않습니다. 따라서 group
OFF가 진행 중일 때 direct write는 그 OFF에 의해 덮일 수 있으며, 이번 B1은 그 반대를 보장한다고
서술하지 않습니다. 이번 B1 production에는 group을 arm하는 caller가 없어 그 경합을 새로 만들지 않습니다.
armed ordinary group의 fault injection에서는 override 자체가 submit 0회임을 고정하고, 다음 successful fresh
cycle의 새 plan identity가 dispatch 가능한 남은 member를 포함한 plan-wide write를 정확히 1회 만들며 그 다음
unchanged cycle은 추가 `GroupPlanPort.write_plan()` 0회로 dedupe됨을 기록합니다. STOPPED/disabled group의
verified direct `power=true`는 같은 member에 대한 두 번째 이후 명령도 각각 다음 complete fresh cycle에서
OFF 재확인을 정확히 1회 만들고, 그 성공 뒤 unchanged cycle은 추가 호출 0회입니다. function이나 coordinator
결과와 무관하게 member의 첫 manual ownership 선언은 control epoch을 한 번 전진시키므로, STOPPED/disabled
group에서도 다음 cycle에 plan-wide canonical OFF 재확인 호출을 정확히 1회 만들고 다음 unchanged cycle은
추가 호출 0회입니다. 같은 member의 재선언은 identity를 바꾸지 않아 이 ownership 전이 비용을 다시 만들지
않습니다. 이는 per-member applied tracking을 새로 발명하지 않은 plan-atomic ownership 전이와 verified
direct power reconciliation의 서로 구분되고 유계인 원인이며, 두 원인이 같은 cycle에 있으면 하나의
plan-wide 호출로 합쳐집니다.

최소 회귀는 (1) unqualified·diagnostic-only entity에서도 decoded bool power truth가 I/O 추가 없이 남음,
(2) `off`·`on`·`mixed`·`unknown`과 member disabled·manual override 조합이 actual/ownership을 섞지 않음,
(3) group HA sensor에 command topic이 없고 direct command 결과가 state를 바꾸지 않음, (4) invalid payload는
override도 frame도 0회, valid direct scalar·schedule payload는 override가 coordinator보다 먼저 실행됨,
(5) manual generation 변화가 대기 중 ordinary plan을 pre-send에서 폐기하며 override 시점 submit은 0회,
(6) 이미 수락된 mandatory OFF는 override로 취소되지 않음, (7) armed ordinary group이 override 뒤 첫 fresh
cycle에서 idle까지 기다린 뒤 `GroupPlanPort.write_plan()` 호출을 정확히 1회 만들고 다음 unchanged cycle은
추가 호출 0회, (8) armed `STOPPED`에서는 미해결 safety token이 없는 fixture의 첫 번째와 반복 verified
direct `power=true`가 각각 warning과 다음 cycle의 canonical OFF 호출 정확히 1회를 만들고 성공 뒤 unchanged
cycle은 추가 호출 0회, (9) shared lock을
기다리던 기존 mandatory OFF가 direct write 뒤 성공하면 그 성공 counter가 watermark를 넘어 다음 cycle의
중복 OFF 호출은 0회, (10) direct preflight 실패·non-verified result·`power=false`·`manual_flow`·schedule은
watermark나 revert warning을 만들지 않되 해당 member의 첫 ownership 선언이면 다음 cycle의 plan-wide OFF
호출 정확히 1회와 그 다음 unchanged cycle 0회, 같은 member의 재선언이면 추가 호출 0회, (11) watermark를
세운 mandatory OFF 재확인의 실패·불확정은 counter를 전진시키지 않고 이후 submit이 deferred되어
`safety_off_required`를 유지하며 실패 이후 추가 port 호출은 0회,
(12) armed `EMERGENCY_STOP`에서는 `group_emergency_stop_blocks_direct_control`로 journal·frame 0회에 거부함,
(13) 기존 read-only startup·publish·shutdown과 public redaction 단정이 그대로 통과함을 고정합니다.

B1 source·offline test landing은 broker·Home Assistant·실제 device를 연결하거나 group write를 승인하지
않습니다. B2에서 command surface를 열기 전에는 identity-based command verdict, 유계 wait, qualification
진단을 별도 정책과 적대적 검토로 결속합니다. group command는 shared lock을 이미 소유하는
`LocalControlMqttRouter.handle()`을 경유하지 않고 별도 route에서 runtime worker의 완료를 기다려야 합니다.
실제 활성화에는
`docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장 감시·중단 수단이 필요하고,
하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`은 유지합니다. 같은 truth·ownership 질문을 반복해 정보가
늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 qualified group command transport B2 범위 확장

production lifecycle을 연결한 뒤 다음 단계를 production daemon·MQTT·Home Assistant 연결로
제시한 직후 repository maintainer가 “이어서 진행하자”로 답했습니다. 그 답은 제시된 다음
단계 전체에 대한 것이고, 이를 B1·B2로 나눈 것은 그 뒤 적대적 검토에서 채택해 B1
하위절에 기록한 내부 결정입니다. B1을 먼저 닫고, 그 하위절이 command surface 전에
요구한 별도 정책과 적대적 검토를 이제 충족합니다. 이번 B2는 같은 특정 답변이 승인한
단계의 남은 group ON/OFF command transport만 **source·test·offline 검증 범위로 좁게
결속**하며 그 발언보다 넓지 않습니다. 새 maintainer 승인을 추가로 주장하거나 이후 product
slice의 상시 승인으로 일반화하지 않고, 실제 broker·Home Assistant·장비·앱·원격 환경
실행 승인은 아닙니다.

이번 작업이 닫을 질문은 모든 member가 current physical identity에 대해 `power`·`manual_flow`
2/2 qualification을 가진 group command를 shared-lock deadlock 없이 유일한 plan-atomic write 경로로
route하고, 동시 fresh cycle이 끼어들어도 command 결과를 plan identity로 과장 없이 판정할 수
있는가입니다. 근거는 firmware 가설이 아니라 qualification fold, manager generation,
runtime submit·completion, MQTT task lifetime의 static trace와 fault injection입니다.

이 확장은 다음 파일로 한정합니다.

- 신규 pure module `src/jebao_flow/local_control/group_command.py`
- `src/jebao_flow/local_control/group_service.py`, `group_supervisor.py`, `ha.py`, `mqtt.py`
- `src/jebao_flow/local_control_cli.py`
- `tests/unit/test_local_control_*`
- `docs/local-control.md`

이 하위절은 B1의 “group command topic·switch·production caller 0개”라는 현재 구현 진술만 B2
범위에서 명시적으로 대체합니다. B1의 fresh device truth, command-free actual-output sensor, direct
manual ownership, watermark, write-disarmed startup과 sticky close 의미는 유지합니다.
`src/jebao_flow/groups/*`, `src/jebao_flow/local_control/state.py`, `group.py`, `coordinator.py`,
`lease.py`, `device_writer.py`, `journal.py`, `src/jebao_flow/mqtt/*`, `src/jebao_flow/app.py`,
`custom_components/*`, `src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`, 기존 여섯 frozen
source와 그 직접 테스트, `docs/runs/*`는 수정하지 않습니다. old MQTT/group service나
`devices.*` observer를 import하거나 우회 write 경로로 재사용하지 않습니다.

이번 source 변경은 기존 read-only device-state, B1 group truth·ownership, supervisor/runtime 의미에
대한 가산적 변경으로 한정합니다. `tests/unit/test_local_control_group_supervisor.py`,
`test_local_control_group_service.py`, `test_local_control_state.py`, `test_local_control_ha.py`,
`test_local_control_mqtt.py`, `test_local_control_cli.py`의 기존 단정은 완화하거나 삭제하지 않으며,
필요하면 별도 repository maintainer 승인을 먼저 받습니다. 특히 exact manager-produced plan
provenance, 명시 OFF의 mandatory-safety dedupe 우회, disposition 다섯 종, direct-ownership
watermark, unresolved OFF sticky close를 변경하지 않습니다.

`group_command.py`는 group control qualification의 단일 pure fold입니다. coordinator·manager·runtime·
journal·MQTT·Home Assistant를 import하지 않고 import-boundary 테스트의 전이 폐쇄로 이를
고정합니다. 새 qualification 술어나 receipt 의미를 만들지 않고, 기존
`LocalFunctionDescriptor.control_exposed_for(physical_identity_key)`를 각 group member의 `power`·
`manual_flow`에 대해 fold합니다. 결과는 `qualified: bool`과 정렬된 bounded
`(logical_member, function)` missing tuple을 함께 제공하고, 두 소비자인 projection과 lifecycle이
같은 결과로 stable English `physical_device_not_qualified` 진단을 만듭니다. physical identity,
receipt 내용, private path는 public schema·status·result에 싣지 않습니다.

기존 actual-output diagnostic sensor는 그대로 두고 별도 group switch를 모든 member가 2/2
qualified인 group에만 생성합니다. 1/2 qualification에 OFF-only button을 만들지 않으며,
빠진 logical member·function은 actual-output attributes와 lifecycle 진단에서 구분합니다. projection은
qualification만으로 switch 존재를 결정하고, construction 성공 여부는 switch availability와
command 수용에서 별도 판정합니다. qualified이지만 unconstructed인 group은 switch를
discovery할 수 있어도 availability `offline`으로 두고, command는 해당 group의 기존 stable
construction `diagnostic_reason`으로 coordinator·journal·frame 전에 거부합니다. projection과
lifecycle inventory 불일치는 raise로 device-state task를 종료하지 않고, B1과 같이 group
availability `offline`과 bounded inventory-mismatch 진단으로 격리합니다.

switch state는 desired runtime이나 command result를 반영하지 않고 successful complete fresh cycle의
actual output만 소비합니다. `on`·`off`일 때만 switch availability를 `online`으로 두고,
`mixed`·`unknown`을 어느 한쪽으로 접지 않으며 `offline`으로 둡니다. command 직후에 낙관적
switch state를 publish하지 않습니다. group switch command topic과 기존 device/schedule command
topic은 생성 시점에 disjoint함을 확인해 각 topic을 정확히 router 하나만 소유하게 합니다.
충돌을 발견하면 잘못된 target으로 route하지 않고 construction을 거부합니다.

group command는 별도 `LocalControlGroupCommandRouter`에서 bounded ASCII `ON`·`OFF`를 parse하고,
shared process `operation_lock`을 이미 잡는 `LocalControlMqttRouter.handle()`을 경유하지 않습니다.
group router와 그 command 대기는 shared `operation_lock`을 잡지 않고 runtime worker가 기존
`_OperationLockedGroupPlanPort` 경계에서 그 lock을 얻게 합니다. retained·duplicate-marked command는
기존처럼 거부하고 QoS 0을 유지합니다. invalid payload, 미존재·unconstructed·
unqualified group, topic inventory mismatch는 manager ownership·coordinator·durable journal·frame을 만들지
않고 stable bounded diagnostic으로 종결합니다.

유효한 switch command는 supervisor lifecycle lock 안의 await 없는 구간에서 현 runtime을 복사해
`ON = state RUNNING, enabled=True`, `OFF = state STOPPED, enabled=False`로 바꾸되 나머지 pattern·power·
limit·period 선호는 보존합니다. 순서는 반드시 `GroupManager.update_runtime()` 성공 →
`resume_all()` → `plan_tick()` → arm → exact 반환 객체 `submit()`입니다. `update_runtime()`
검증이 실패한 command가 manual override를 먼저 지우지 않게 하고, ownership-boundary
전이에서는 뒤 `resume_all()`이 no-op이며, 같은 state command에서는 남은 override를 해제합니다.
OFF plan은 기존 mandatory safety token·dedupe 우회·shutdown 보존을 그대로 사용하고 별도 지름길을
만들지 않습니다.

submit 직전·직후 runtime snapshot과 `deduplicated_count`는 lifecycle lock 안의 **await 없는 하나의
구간**에서 잡습니다. `deduplicated` action은 그 counter delta로만 판정하여, 이미 applied
identity가 같아도 mandatory OFF를 실제 재확인하는 explicit `OFF`를 dedupe로 오판하지 않습니다.
`no_dispatch`는 counter가 아닌 `cycle.plan.dispatch_decisions` 비어 있음으로 직접 판정합니다.
submit 후 await를 건너 `succeeded_count`·`deduplicated_count`·`no_dispatch_count` 등의 delta를
해당 command의 verdict 근거로 사용하지 않습니다. fresh cycle이 그 간격에 별도 plan을 submit할
수 있기 때문입니다.

command result는 행위와 현재 identity 상태를 분리합니다. `ownership_accepted`,
`submitted_plan_identity_sha256`, `runtime_submit_called`, `deduplicated`, `dispatch_decision_count`,
`applied_identity_matches`, `latest_identity_matches`, matching failure의 `delivery_certainty`,
`completion_pending`을 서로 독립된 bounded fact로 내보냅니다. identity 동등성만으로 특정
command의 physical operation 수행 여부를 새 boolean PASS로 추측하지 않습니다. 배타적 headline은
즉시 submit disposition과 완료 identity를 모두 소비하며, `deferred_safety_off` →
`suppressed_after_failure` → submitted identity와 결속된 `not_sent`·`uncertain` failure →
`awaiting_fresh_state` → `deduplicated` → `verified` → `superseded` → `pending` 우선순위로
판정합니다. `verified`는 final applied identity가 submitted identity와 같고 final failure가 없을 때만,
`superseded`는 latest identity가 submitted identity와 다를 때만 가능합니다. headline이
`deduplicated`여도 `applied_identity_matches`로 current state를 별도 표시하고,
`verified`와 latest mismatch가 겹칠 때도 `latest_identity_matches=False`를 숨기지 않습니다.
consumer는 `verified`만으로 “이 command가 썼다”를 추가로 발명하지 않습니다.
`deduplicated=False` ∧ `verified`는 submitted desired state가 현재 적용되어 있음을 뜻합니다. wait
사이 다른 identity로 supersede된 뒤 동일 identity가 다시 적용될 수 있으므로, 우리 job이
supersede되지 않았다는 별도 근거 없이 이 command 자신이 그 write를 냈다고 주장하지
않습니다. `deduplicated=True`는 해당 command가 no-op으로 합쳐졌다는 반대 방향의 권위 있는
신호이고, 그 외 조합의 command-specific 물리 수행 여부도 추측하지 않습니다.

runtime completion은 고정·유계 timeout으로 **보고만** 기다립니다. timeout은 PID·wall clock·
직전 관측같은 volatile 값에서 유도하지 않고, `pending`이지 `NOT_SENT`가 아닙니다. wait는
supervisor lifecycle lock과 shared process `operation_lock` 모두를 잡지 않으며, timeout·consumer
cancellation은 wait observer만 종결하고 runtime worker를 cancel하지 않습니다. 나중 fresh truth와
identity snapshot이 최종 결과를 드러내고, timeout 자체로 frame 미전송을 판정하지 않습니다.

MQTT consumer 또는 command waiter task를 추가하면 adapter 내부의 취소·
`gather(..., return_exceptions=True)` 대상으로만 둡니다. group service `aclose()`는 계속 adapter
`run()`의 외부 `finally`에서 모든 supervisor를 닫고, unresolved mandatory OFF를 다른 오류보다
우선해 adapter 밖으로 재전파합니다. 새 task와 close를 같은 `return_exceptions=True` 집계에
넣어 sticky failure를 삼키지 않습니다.

회귀는 최소한 (1) 2/2·2/1·1/2·0/2 qualification에서 switch 생성·미생성과 exact bounded
missing member/function 진단, pure import closure, (2) actual-output sensor 보존, switch의
fresh `on`·`off`·`mixed`·`unknown` state·availability와 optimistic publish 0회, (3) group·device·
schedule command topic의 단일 소유와 group route의 `LocalControlMqttRouter.handle()`·shared lock 비경유,
(4) retained·duplicate·invalid payload와 qualified-but-unconstructed·inventory mismatch의 manager·journal·
frame 0회 및 stable diagnostic, (5) `update_runtime()` 거부가 manual override를 보존하고 same-state
command는 성공 뒤 해제하며 exact `plan_tick()` 객체만 submit하고, `OFF`는 current runtime
값과 무관하게 `STOPPED`·disabled 검증을 통과함, (6) 반복 explicit OFF가 기존
applied identity와 같아도 `deduplicated=False`이고 미해결 token이 없으면
`GroupPlanPort.write_plan()` 1회에 도달함, (7) immediate counter delta의 dedupe와
identity-keyed `verified`·`superseded`·`not_sent`·`uncertain`, `awaiting_fresh_state`,
`deferred_safety_off`, `suppressed_after_failure`, `pending` 우선순위를 각각 fault injection하고
await 뒤 unrelated counter 변화가 verdict를 바꾸지 않음, (8) fresh cycle이 command wait 사이 더 새
plan을 submit해도 identity fact와 headline이 오판하지 않음, (9) timeout·consumer cancellation이 worker를
cancel하지 않고 timeout은 `pending`으로만 보고됨, (10) unconstructed switch availability offline·
construction reason 재사용과 projection/lifecycle mismatch 격리, (11) 새 command task를 닫아도 모든
supervisor close·unresolved OFF sticky 재전파가 유지됨, (12) 기존 B1·read-only startup·publish·
redaction 단정이 그대로 통과함을 고정합니다.

B2 source·offline test landing은 production command surface를 코드에 연결하지만 실제 broker·
Home Assistant·장비·앱·원격 환경을 실행하거나 장비 write를 승인하지 않습니다. 실제
활성화에는 `docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장 감시·
중단 수단이 별도로 필요하고, 하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`은 유지합니다.
같은 command verdict·transport 질문을 반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 per-member group ownership resume C1 범위 확장

qualified group ON/OFF transport commit `136f9acdc40e67c9d06ed4eb88c4f9fc24535c50`의 완료 보고 뒤
repository maintainer는 “다음 단계 진행하자”고 답했습니다. 그 발언은 다음 기능을 이름으로
선택한 승인이 아니라 B2 다음의 제품 작업을 계속하라는 요청이었고, 이후 read-only static trace에서
`GroupManager.resume_member()`가 구현·직접 테스트되어 있지만 production 호출자는 0개이며 기존
문서가 약속한 개별 “그룹 복귀”만 비어 있음을 확인했습니다. 이번 C1은 그 요청을 이 가장 작은
공백의 **source·test·offline 검증 범위로 좁게 결속**합니다. 이후 product slice의 상시 승인으로
일반화하지 않고 실제 broker·Home Assistant·장비·앱·원격 환경 실행이나 장비 write 승인이 아닙니다.

이번 작업이 닫을 질문은 이미 명시적 group ON/OFF command로 armed된 fully-qualified software-
independent group에서 manual override member 하나만 desired runtime을 바꾸거나 두 번째 arming 경로를
만들지 않고 기존 plan-atomic 경계로 복귀시킬 수 있는가입니다. 근거는 firmware 가설이 아니라
manager generation, supervisor arming·plan provenance, runtime identity verdict와 MQTT topic ownership의
static trace 및 fault injection입니다.

이 확장은 다음 파일로 한정합니다.

- `src/jebao_flow/local_control/group_service.py`, `group_supervisor.py`, `ha.py`, `mqtt.py`
- 필요한 production composition 갱신에 한한 `src/jebao_flow/local_control_cli.py`
- `tests/unit/test_local_control_*`
- `docs/local-control.md`, `docs/home-assistant.md`

`src/jebao_flow/groups/*`와 기존 `GroupManager.resume_member()` 구현·직접 테스트,
`src/jebao_flow/local_control/group_command.py`, `state.py`, `group.py`, `coordinator.py`, `lease.py`,
`device_writer.py`, `journal.py`, old `src/jebao_flow/mqtt/*`, `src/jebao_flow/app.py`,
`custom_components/*`, `src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`, 기존 여섯 frozen source와
그 직접 테스트, `docs/runs/*`는 수정하지 않습니다. old MQTT/group service나 `devices.*` observer를
import하거나 그 `resume_group`·`resume_all_members` action을 우회 경로로 재사용하지 않습니다.

이번 source 변경은 기존 read-only device state와 B1·B2 group truth, ownership, command verdict,
shutdown 의미에 대한 가산적 변경으로 한정합니다. `tests/unit/test_local_control_group_supervisor.py`,
`test_local_control_group_service.py`, `test_local_control_group_transport.py`,
`test_local_control_state.py`, `test_local_control_ha.py`, `test_local_control_mqtt.py`,
`test_local_control_cli.py`의 기존 단정은 완화하거나 삭제하지 않으며, 필요하면 별도 repository
maintainer 승인을 먼저 받습니다. write-disarmed startup, exact manager-produced plan provenance,
disposition 다섯 종, explicit mandatory OFF의 dedupe 우회와 token 보존, direct-power watermark,
identity-based eight-stage verdict, unresolved OFF sticky close를 변경하지 않습니다.

C1은 per-member resume만 추가합니다. 별도 “전체 그룹 복귀” control은 만들지 않습니다. 이미 armed·
RUNNING인 group에 같은 `ON` command를 다시 보내면 B2의 검증된 `update_runtime()` 뒤 `resume_all()`이
모든 override를 해제하고, stopped group도 다음 `ON`에서 같은 경로를 사용합니다. 이 기존 동작을
`docs/home-assistant.md`에 명시하되 새 능력이나 별도 action으로 표현하지 않습니다.

per-member resume는 기존 group qualification fold를 그대로 재사용하며 모든 member의 current
physical identity에 대해 `power`·`manual_flow`가 2/2 qualified인 group에만 member별 state-less Home
Assistant button을 projection합니다. 새 qualification 술어·receipt 의미·OFF-only fallback을 만들지
않습니다. button availability는 qualification, constructed supervisor와 current `armed` fact만
소비하며 actual output의 `mixed`·`unknown`을 임의의 power 상태로 접지 않습니다. command result나
button press를 actual-output 또는 group switch state에 낙관적으로 mirror하지 않습니다.

unarmed group의 resume는 stable English reason `group_not_armed`로 **manager mutation 전에** 거부하고
manager generation, manual override, runtime, coordinator, durable journal과 physical frame을 모두
바꾸지 않습니다. resume는 `_armed=True`를 설정하지 않으며 B2 group ON/OFF
`apply_runtime_command()`만 production arming 권한을 유지합니다. 첫 group `ON` 또는 `OFF` command가
기존 `resume_all()`을 무조건 지나므로 unarmed override가 영구히 갇히지 않습니다.

armed `EMERGENCY_STOP`에서는 기존 stable reason
`group_emergency_stop_blocks_direct_control`로 manager mutation 전에 거부합니다. 이 action은 emergency
stop의 별도 출구가 아니며, rejected command가 ownership epoch이나 mandatory OFF identity를 바꾸지
않습니다. 그 밖의 armed state에서는 desired `GroupRuntime`을 읽거나 수정하지 않고 supervisor
lifecycle lock 안의 await 없는 구간에서 다음 순서를 지킵니다.

1. exact logical member가 현재 group에 속하는지 검증
2. `before = GroupManager.generation`
3. `ownership_changed = GroupManager.resume_member(member) != before`
4. 위 비교가 끝난 뒤 `GroupManager.plan_tick()`을 정확히 한 번 호출
5. 그 exact 반환 객체를 `_finish_cycle(..., force_mandatory_off=False)`에 전달

`plan_tick()` 자체가 generation을 올리므로 2~3의 판정을 그 뒤로 미루지 않습니다. resume는
`update_runtime()`·`resume_all()`·`invalidate_applied()`·`retry_latest()`를 호출하거나 즉시 coordinator를
직접 호출하지 않습니다. `ownership_changed=False`도 별도 물리 shortcut을 만들지 않고 같은 plan
경로로 흘려, applied cache가 current이면 dedupe되고 invalidated 상태이면 기존 runtime 의미대로만
재확인됩니다. RUNNING resume는 기존 ordinary plan, STOPPED resume는 기존 canonical mandatory OFF이며,
watermark·safety token·failure retry 규칙을 새로 만들거나 우회하지 않습니다.

B2의 `GroupCommandResult.requested_on: bool`과 public switch result schema는 완화·변경하지 않습니다.
identity·matching failure·applied/latest 계산과
`deferred_safety_off -> suppressed_after_failure -> not_sent/uncertain -> awaiting_fresh_state ->
deduplicated -> verified -> superseded -> pending` precedence를 하나의 await-free pure completion helper로
분리해 switch와 resume가 공유합니다. helper는 runtime에 실제 submit을 시도한 cycle만 받으며
`WRITE_DISARMED` disposition은 내부 계약 오류로 거부합니다. 얇은 resume result는 action
`resume_member`, logical group·member, `ownership_changed`와 B2와 같은 completion facts를 내보내고,
별도 physical-authorship PASS boolean이나 새 headline을 만들지 않습니다. await 뒤 unrelated counter
delta를 command verdict로 사용하지 않습니다.

resume button topic은 기존 `LocalControlGroupCommandRouter`가 shared process `operation_lock` 없이 함께
소유하고 새 router나 task를 만들지 않습니다. group switch와 per-member resume route는 router 내부에서
서로 중복될 수 없고, 그 합집합은 기존 device·schedule command topics와 disjoint해야 하며 각 projected
topic에는 정확히 한 route만 존재합니다. retained·duplicate-marked·invalid payload, unknown member,
unqualified·unconstructed·unarmed·emergency group과 projection/lifecycle inventory mismatch는 stable
bounded diagnostic으로 pre-journal·frame 0회에 종결합니다. inventory mismatch는 기존 device-state
task로 raise하지 않고 해당 group control availability를 offline으로 격리합니다.

runtime completion은 B2의 고정·유계 observer-only wait과 identity facts를 그대로 사용합니다. wait는
supervisor lifecycle lock과 shared process lock을 잡지 않고 timeout·consumer cancellation로 worker를
cancel하지 않습니다. MQTT adapter는 기존 `_consume` task를 공유하며 group service `aclose()`는 계속
adapter `run()`의 외부 `finally`에서 모든 supervisor를 닫고 unresolved mandatory OFF를 우선 재전파합니다.

회귀는 최소한 (1) 2/2 qualification에서만 logical member별 button이 생기고 missing qualification에는
button 0개, (2) unarmed resume가 `group_not_armed`이며 manager generation·override·runtime·journal·frame
변화 0이고 뒤 첫 ON/OFF가 override를 모두 해제함, (3) armed RUNNING resume가 해당 member를
`SKIP_MANUAL_OVERRIDE`에서 `DISPATCH`로 복귀시키고 plan port 호출 정확히 1회 뒤 unchanged cycle 0회,
(4) armed STOPPED resume가 전 member canonical OFF를 정확히 1회 만들고 같은 member 재실행은
`ownership_changed=False`와 추가 write 0회인 stable-applied fixture, (5) EMERGENCY_STOP과 unknown member가
stable reason으로 manager·journal·frame 전 거부됨, (6) resume 전후 runtime의 state·enabled·pattern·power·
limits·period가 불변이고 `update_runtime()`·`resume_all()`·`invalidate_applied()`·`retry_latest()` 호출이
모두 0회, (7) switch와 resume가 동일 completion helper의 8단계 precedence·identity facts를 사용하며
B2 `requested_on` 계약이 불변이고 `WRITE_DISARMED` cycle을 helper에 넘기면 내부 계약 오류, (8) device·schedule·group
switch·group resume topic의 pairwise disjointness와 단일 소유, retained·duplicate·inventory mismatch의
service·journal·frame 0회, (9) button availability가 qualification·construction·arming만 반영하고
actual-output/switch optimistic publish 0회, (10) 기존 B1·B2와 read-only startup·publish·redaction·timeout·
shutdown 단정이 그대로 통과함을 고정합니다.

C1 source·offline test landing은 production schema와 command route만 코드에 추가합니다. 실제 broker·
Home Assistant·장비·앱·원격 환경 실행과 장비 write는 포함하지 않으며, 각각
`docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장 감시·중단 수단이
별도로 필요합니다. 하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`은 유지합니다. 같은 resume 질문을
반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 observer control-surface suppression D1 범위 확장

per-member group ownership resume commit `a18a0dd46de9d9e8bf5d776707977937b9c33300`의 완료 뒤
repository maintainer는 서버 OS 재부팅 전후의 read-only 안전 상태 확인을 요청했고, 재부팅 완료 후
“이어서 하자”고 답했습니다. 그 발언은 기능을 이름으로 고르거나 actual deployment를 활성화하라는
승인이 아니라 다음 제품 작업을 계속하라는 요청입니다. 이후 read-only static trace에서
`docs/home-assistant.md`가 `runtime.mode: observer`이면 모든 group·device control entity를 생성하지
않는다고 약속하지만, current local-control projection과 CLI composition은 `--write-enabled`, current
qualification, `dry_run`, per-device write flag만 소비하고 top-level runtime mode를 소비하지 않는
divergence를 확인했습니다. 이번 D1은 그 요청을 이 문서화된 경계의 **source·test·offline 검증
범위로 좁게 결속**하며, 이후 product slice의 상시 승인이나 broker·Home Assistant·장비·앱·원격 환경
실행 및 장비 write 승인으로 일반화하지 않습니다.
기존 old `src/jebao_flow/mqtt/service.py`는 이미 observer에서 control 광고와 command를 낮추거나
거부하므로, D1은 새 gate를 발명하는 것이 아니라 새 local-control stack이 잃은 기존 mode 계약을
현재 projection·composition 경계에 복원하는 작업입니다.

이번 작업이 닫을 질문은 `runtime.mode`가 `observer`이면 current qualification과 명시적
`--write-enabled`가 함께 있어도 **current process가 새로 projection·publish·subscribe하는** device
scalar·schedule, group switch와 per-member resume command surface를 0개로 유지하면서 기존
diagnostic·actual-output·read-only publish와 shutdown 의미를 그대로 보존할 수 있는가입니다. 근거는
firmware 가설이 아니라 `RuntimeMode`, CLI composition, Home Assistant projection과 MQTT subscription의
static trace 및 offline regression입니다. 이는 §3 P1을 주장하는 변경이 아니라, 최근 B2·C1이 넓힌
surface를 이미 문서화된 observer 자세에 맞추는 제한입니다.

이 확장은 다음 파일로 한정합니다.

- `src/jebao_flow/local_control/ha.py`, `src/jebao_flow/local_control_cli.py`
- 필요한 회귀에 한한 `tests/unit/test_local_control_*`
- 구현 경계 기록에 한한 `docs/local-control.md`, `docs/home-assistant.md`

`src/jebao_flow/config.py`, `src/jebao_flow/groups/*`, `src/jebao_flow/local_control/catalog.py`,
`group_command.py`, `group_service.py`, `group_supervisor.py`, `state.py`, `group.py`, `coordinator.py`, `lease.py`,
`device_writer.py`, `journal.py`, `mqtt.py`, old `src/jebao_flow/mqtt/*`, `src/jebao_flow/app.py`,
`custom_components/*`, `src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`, 기존 여섯 frozen source와
그 직접 테스트, `docs/runs/*`는 수정하지 않습니다. old MQTT/group service나 `devices.*` observer를
import하거나 우회 경로로 재사용하지 않습니다.

이번 source 변경은 observer-mode projection과 production composition의 **새 제어 표면을 줄이는 데만**
한정하며 broker retained state를 삭제하거나 migration하지 않습니다.
`tests/unit/test_local_control_group_supervisor.py`, `test_local_control_group_service.py`,
`test_local_control_group_command.py`, `test_local_control_group_transport.py`,
`test_local_control_state.py`, `test_local_control_ha.py`, `test_local_control_mqtt.py`,
`test_local_control_cli.py`의 기존 단정은 완화하거나 삭제하지 않으며, 필요하면 별도 repository
maintainer 승인을 먼저 받습니다. B1~C1의 fresh device truth, write-disarmed startup, exact
manager-produced plan provenance, disposition 다섯 종, mandatory OFF token·dedupe 우회, direct-power
watermark, identity-based eight-stage verdict와 unresolved OFF sticky close 의미를 변경하지 않습니다.

top-level `RuntimeMode.OBSERVER`는 control surface에 대해 `--write-enabled`, `runtime.dry_run`, retained
qualification과 per-device `allow_hardware_writes`보다 먼저 적용합니다. observer mode에서는 current
qualification 파일을 읽거나 write coordinator를 구성하지 않고, projection에 control authority가
없음을 projection-level bounded field `control_surface_diagnostic_reason`의 stable English 값
`runtime_mode_observer`로 명시적으로 전달합니다. 이 mode reason은 `physical_device_not_qualified` 같은
entity capability·qualification `diagnostic_reason`을 대체하지 않으며, 이미 qualified된 function의 control을
observer mode가 억제할 때 qualification 부재를 주장하지 않습니다. entity의 기존 capability reason은
observer와 control projection에서 그대로 보존합니다. `ha-schema`도 current
config의 runtime mode를 존중합니다. 따라서
qualified receipt가 남아 있거나 `--write-enabled`가 주어져도 device scalar·schedule command topic,
group switch와 member resume entity·topic은 생성되지 않고, group command router도 생성되지 않습니다.
MQTT adapter가 subscribe하는 command topic은 0개이며 group lifecycle은 coordinator 없는 기존
read-only inventory로 supervisor 0개를 유지합니다.

observer suppression은 관측 표면을 지우지 않습니다. device·system diagnostic state, existing product
schedule read-only observation, per-device availability, group actual-output diagnostic과 lifecycle inventory는 계속 projection·
publish하며, complete fresh cycle의 device publication 순서와 group callback을 바꾸지 않습니다. 이미
qualification된 function도 observer projection에서는 command topic 없는 read-only entity로만 나타나고,
qualification 파일을 읽지 않은 production observer는 그 권위를 추측하지 않습니다. command result나
desired state를 actual output으로 낙관 publish하지 않고 `mixed`·`unknown` 의미도 그대로 유지합니다.
production composition은 qualification 파일을 읽지 않는 상위 보장을 제공하고, projection 층은 injected
qualified catalog를 받아도 독립적으로 control을 억제하는 하위 보장을 제공하며 둘을 같은 판정으로
혼동하지 않습니다.

이 D1은 이전 run에서 retained된 MQTT discovery config를 제거하지 않습니다. current discovery topic,
`unique_id`와 Home Assistant device identifier는 `topic_prefix` 또는 instance 성분을 포함하지 않아, 같은
`discovery_prefix`와 논리 ID를 쓰는 다른 daemon의 config와 구분되지 않습니다. 그러므로 empty retained
payload를 자동 publish하면 삭제 대상이 이 instance 소유라는 사실을 증명할 수 없고 다른 daemon의 control
entity를 지울 수 있습니다. broker retained state를 조회하거나 지난 projection 목록을 file·volume에
저장하거나 operator opt-in으로 그 소유권 증명을 대체하지 않습니다. 이 slice는 어떤 discovery·state·
command·availability topic에도 deletion 목적의 empty retained payload를 publish하지 않습니다.

따라서 D1의 보장은 current process의 projection·route·subscription에 한정합니다. pre-D1 retained control
entity가 broker·Home Assistant에 남았는지는 `UNKNOWN`이고 화면에 남아 실제 control처럼 보일 수 있지만,
observer process는 그 command topic을 subscribe하지 않습니다. `docs/home-assistant.md`의 “같은 논리 ID를
쓰는 여러 daemon도 `topic_prefix`가 Home Assistant config entry를 구분한다”는 약속은 현재 discovery
identity에 구현되어 있지 않으며 이 divergence를 숨기지 않습니다. 후속 D1b가 discovery `unique_id`·device
identifier·node id를 stable instance namespace에 결속한 뒤에만, D1c가 그 소유 namespace 안의 retained
variant를 정리할 수 있습니다. D1b만 landing하면 legacy entity를 한 벌 더 만들 수 있으므로 D1c와 migration
비용을 별도 정책·적대적 검토에 먼저 결속해야 하고, pre-namespace legacy config는 자동 삭제하지 않습니다.

`RuntimeMode.CONTROL`에서는 B2·C1 behavior를 바꾸지 않습니다. current explicit write opt-in과
qualification을 기존과 같이 소비하고, device command, group switch와 member resume entity·topic,
topic disjointness, qualification·construction·arming availability와 pre-journal rejection을 그대로
유지합니다. `dry_run`과 per-device write lock은 observer-mode entity suppression으로 대체하지 않고
control-mode의 기존 live-opt-in 검증으로 남깁니다. observer 이외의 새 mode, firmware gate, device write
path 또는 MQTT task를 만들지 않으며, 위 `runtime_mode_observer` 이외의 새 stable reason도 만들지
않습니다. `control_surface_diagnostic_reason` 하나는 additive public diagnostic이며 CONTROL에서 `None`이고,
그 밖의 기존 public projection field와 의미를 바꾸지 않습니다.

회귀는 최소한 (1) default인 `mode=observer`에 `--write-enabled`, `dry_run=false`, current qualification을
함께 주어도 qualification 파일 read·coordinator construction·group supervisor가 각각 0이고 public
`control_count`, group switch와 resume entity, 모든 command topic·subscription이 0개, (2) 같은 observer
projection에서 device·system diagnostic, schedule observation, group actual output과 availability가 기존과
같이 생성·publish되고 각 entity의 기존 capability `diagnostic_reason`도 같은 catalog·manifest의 control
projection과 동일하게 보존됨, (3) direct qualified catalog를 observer projection에 주입해도 qualified device function은 command
topic 없는 read-only entity이고 entity capability reason을 qualification 부재로 바꾸지 않으며 projection의
`control_surface_diagnostic_reason`은 정확히 `runtime_mode_observer`이고 group switch·resume는 0개,
(4) `mode=control`에서는 additive mode-reason field가 `None`인 것 외에 기존 qualified device·group
switch·resume projection과 router 단일 소유가 byte/schema 의미상 불변,
(5) observer production composition의 group service는 coordinator·supervisor가 없어 injected group
switch·resume 호출도 기존 construction diagnostic으로 journal·frame 전에 거부되고 public MQTT route는
존재하지 않음, (6) 기존 read-only startup, fresh publish ordering, mismatch isolation, timeout, sticky
shutdown과 public redaction 단정이 그대로 통과함, (7) observer에서도 MQTT adapter가 어떤 discovery
topic에도 deletion 목적의 empty retained payload를 publish하지 않아 retained cleanup이 이 slice에 섞이지 않음을
고정합니다. (8) 기존 테스트가 다루지 않던 `ha-schema`의 첫 mode 회귀로, observer config와
current qualification path를 함께 주어도 qualification 파일 read가 0회이고 출력의 device·schedule
command topic, group switch와 resume entity가 모두 0개이며 `control_surface_diagnostic_reason`이
정확히 `runtime_mode_observer`임을 고정합니다. 이 단정은 `serve`와 composition block을 공유하지만
coordinator·group service 구성 전에 return하는 `ha-schema` 분기의 첫 직접 coverage입니다.

D1 source·offline test landing은 actual deployment를 재기동하거나 broker·Home Assistant·device·app에
접촉하지 않습니다. post-reboot read-only 확인은 새 write 권한을 만들지 않으며 실제 활성화에는
`docs/hardware-readiness.md`의 fresh preflight·exact restore·on-site 승인·현장 감시·중단 수단이 별도로
필요합니다. 하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`, frozen native harness를 유지합니다. 같은
observer-surface 질문을 반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 Home Assistant discovery instance namespace D1b 범위 확장

observer control-surface suppression commit `cc68af25fa73104d06688ab296b7f5d0c6c2a9f0`의 완료를
보고한 뒤 repository maintainer는 “다음 가자”고 답했습니다. 이 발언은 actual deployment나
broker retained-state migration을 활성화하라는 승인이 아니라, 직전 D1이 명시한 후속 제품
경계를 source·test·offline 검증으로 계속하라는 요청입니다. D1b 선택과 세부 설계는 그 발언
이후의 read-only static trace와 적대적 검토에서 정했으며, 이를 후속 slice의 상시 승인이나
broker·Home Assistant·장비·앱·원격 환경 실행 및 장비 write 승인으로 일반화하지 않습니다.
D1이 “D1c와 migration 비용을 별도 정책·적대적 검토에 먼저 결속”하라고 설정한 선행
조건은 아래의 device 단위 migration 비용, D1c의 namespaced-variant 한계, 이 적대적 검토로
이행합니다. 이 이행은 D1c 자체의 source·deployment를 승인하지 않으며 D1c는 여전히 별도
정책과 적대적 검토를 필요로 합니다.

이번 작업이 닫을 질문은 같은 `discovery_prefix`와 논리 device·group id를 쓰는 두 daemon이 서로
다른 `topic_prefix`를 갖는 경우, Home Assistant discovery object id·entity `unique_id`·device
identifier를 stable instance namespace에 같이 결속해 서로의 discovery identity를 공유하지 않게
하면서 state·command·availability topic과 B1~D1의 의미를 변경하지 않을 수 있는가입니다.
근거는 firmware 가설이 아니라 current Home Assistant projection의 identity 생성과 MQTT topic
구성에 대한 static trace, 서로 다른 prefix를 가진 projection 사이의 set-intersection regression입니다.
이는 §3 P1을 주장하거나 write-path gate를 늘리는 작업이 아니라, D1에서 명시한 multi-daemon
discovery identity divergence를 닫는 작업입니다.

이 확장은 다음 파일로 한정합니다.

- `src/jebao_flow/local_control/ha.py`
- 필요한 회귀에 한한 `tests/unit/test_local_control_ha.py`, `test_local_control_mqtt.py`
- identity·migration 경계 기록에 한한 `docs/local-control.md`, `docs/home-assistant.md`

`src/jebao_flow/config.py`, `src/jebao_flow/groups/*`, `src/jebao_flow/local_control/catalog.py`,
`group_command.py`, `group_service.py`, `group_supervisor.py`, `state.py`, `group.py`, `coordinator.py`, `lease.py`,
`device_writer.py`, `journal.py`, `mqtt.py`, `local_control_cli.py`, old `src/jebao_flow/mqtt/*`,
`src/jebao_flow/app.py`, `custom_components/*`, `src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`, 기존
여섯 frozen source와 그 직접 테스트, `docs/runs/*`는 수정하지 않습니다. 새 config field·operator
opt-in·broker query·durable prior-projection history를 만들지 않습니다.

instance namespace는 기존 규칙으로 검증된 **exact `topic_prefix` ASCII byte string**의 full,
non-truncated SHA-256 hex digest에 `instance_`를 붙인 고정 73-character printable ASCII 값으로
유도합니다. `project_home_assistant()`는 기존 prefix 규칙을 먼저 검증한 뒤에만 namespace를
hashing하고 entity를 생성합니다. Python process hash, PID, IP·port, hostname, filesystem path, mtime, current clock, startup order를
소비하지 않습니다. digest 전에 `strip()`이나 segment 치환을 하지 않으며, 실제 MQTT topic을 만드는
exact prefix가 다르면 namespace binding의 입력도 다르게 유지합니다. `discovery_prefix`는 이
namespace 입력에 포함하지 않습니다. discovery prefix가 다르면 discovery topic 경계가 이미 분리되고,
같은 discovery prefix 안에서 instance를 구분하는 static config 값은 `topic_prefix`이기 때문입니다.
`instance.id`를 섞지 않아 같은 MQTT command tree가 discovery identity 두 벌로 나뉘지 않고, stdlib
SHA-256 외의 새 import boundary를 만들지 않습니다.

이 namespace를 device function, system function, slave schedule slot, group actual-output sensor, group
switch, per-member resume button의 **모든** `unique_id`에 동일하게 넣습니다. current discovery topic
형식은 `{discovery_prefix}/{component}/{object_id}/config`이고 optional node-id segment를 쓰지 않으며,
`object_id` 자리에 namespaced `unique_id`를 그대로 사용합니다. D1의 “node id” 표현은 이 현재
object-id segment를 가리킨 것이며, D1b에서 새 discovery topic segment나 두 번째 namespace를
만들지 않습니다. `docs/local-control.md`의 “discovery node ids” 표현도 이 source 구조에 맞게
“discovery object ids”로 정정합니다. Home Assistant device identifier에도 같은 namespace를 넣되, 한 instance 안의
device function·schedule slot은 `(namespace, logical_member)`당 정확히 한 logical device identifier로
묶고 system function은 같은 namespace의 `local_system` device로 묶습니다. group actual·switch·resume는
같은 `(namespace, group_id)`에 대해 byte-identical group device identifier를 쓰며 member resume를 member
device로 옮기지 않습니다. namespace는 internal projection constructor에 required keyword-only 인자로
전파하고 state topic에서 역파싱하거나 silent legacy default를 두지 않습니다. human-readable entity name과
model·manufacturer는 바꾸지 않습니다.

같은 exact `topic_prefix`와 같은 logical config는 process restart·observer/control mode와 무관하게 같은
namespace를 유도합니다. 다른 prefix를 가진 두 projection은 같은 `discovery_prefix`를 써도
discovery topic, entity `unique_id`, Home Assistant device identifier 집합이 겹치지 않아야 합니다.
state·command·global/device/group availability topic은 기존 exact `topic_prefix` 문자열을 그대로 사용하며
이 slice에서 한 byte도 바꾸지 않습니다. public projection의 기존 field 집합과 `runtime_mode_observer`
의미도 바꾸지 않고 `schema_version` 1을 유지하며, namespace는 이미 공개되는 discovery
identity 내부에만 듭니다.

이 변경은 실제 활성화하면 기존 모든 Home Assistant entity의 `unique_id`·device identifier·discovery
object id를 바꾸므로 새 entity·device set을 만듭니다. Recorder history, entity/device-registry area·name
customization, dashboard reference의 연속성은 자동 보존된다고 주장하지 않으며 이 migration 비용을
docs에 명시합니다.
D1b는 retained discovery config를 삭제하거나 tombstone을 publish하지 않으며, pre-namespace
legacy entity의 존재·소유권은 `UNKNOWN`으로 남깁니다. 후속 D1c는 이 namespaced identity 안의
mode·qualification variant만 자동 정리할 수 있고 pre-namespace legacy config를 자동 삭제하지
않습니다. state·command topic이 byte-identical하므로 CONTROL mode에서 D1b를 배포하면 legacy와
namespaced entity가 같은 물리 명령 topic을 가리키는 동작 가능한 중복 control로 남습니다. 화면에서 둘은
같은 state를 보여 ghost를 구분할 근거도 없으며, 운영자의 중복 조작이 동일 write를 두 번
만들 수 있어 **deployment 시점에는 §3 duplicate-write P1 위험**입니다. OBSERVER mode에서는
현재 process가 command topic을 subscribe하지 않으므로 중복 diagnostic surface이지 장비 write P1은 아닙니다.
platform이 discovery topic의 component segment이므로 한 pre-namespace logical entity가 mode·qualification
전환 이력에 따라 sensor·number·switch·select·button 등 한 벌 이상의 legacy variant를 남길
수 있음도 exact inventory 비용에 포함합니다.

D1b→D1c만으로는 pre-namespace legacy의 소유권을 새로 증명할 수 없어 위 중복을 제거하지
못하며, 배포 가능 상태로 종결되지 않습니다. legacy 제거는 소유권 근거·exact inventory·복구
경로를 갖춘 별도 정책과 repository maintainer 승인, `docs/hardware-readiness.md`의 on-site 절차 아래
Home Assistant device/entity 제거 또는 의도적 broker cleanup으로만 수행합니다. 이는 D1b·D1c 코드 범위
밖입니다. 따라서 **D1b source landing 자체는 §3 P1이 아니지만**, D1c와 legacy migration 절차가
둘 다 별도로 검토·승인되기 전에 broker·Home Assistant에 배포하거나 실제 daemon을 이 identity
schema로 재기동하지 않습니다.

`tests/unit/test_local_control_group_supervisor.py`, `test_local_control_group_service.py`,
`test_local_control_group_command.py`, `test_local_control_group_transport.py`,
`test_local_control_state.py`, `test_local_control_ha.py`, `test_local_control_mqtt.py`,
`test_local_control_cli.py`의 기존 단정은 완화하거나 삭제하지 않으며, 필요하면 별도 repository
maintainer 승인을 먼저 받습니다. B1~D1의 fresh device truth, write-disarmed startup, exact
manager-produced plan provenance, disposition 다섯 종, mandatory OFF token·dedupe 우회, direct-power
watermark, identity-based eight-stage verdict, unresolved OFF sticky close, observer command-surface 0개·diagnostic
보존·empty retained publish 0회 의미를 변경하지 않습니다.

회귀는 최소한 (1) control 표면이 있는 같은 manifest·catalog·group·schedule·`discovery_prefix`에서 다른 두
`topic_prefix`를 쓰면 모든 entity family를 포함한 discovery topic, `unique_id`, Home Assistant
device identifier 집합의 교집합이 각각 0개, (2) 같은 exact prefix의 반복 projection은 세 집합이
완전히 동일하고 namespace가 `instance_` + exact prefix의 full SHA-256 lowercase hex와 일치하며
고정 prefix `jebao-flow/main`에서 golden value
`instance_ce1ffb4517be8e9224bd3b7aa88921621397bc66eb679d38817a78f24bdb1e49`인 73-character printable ASCII,
(3) device·system·schedule slot·group actual·switch·resume 전부의
`unique_id`·discovery object id·device identifier에 namespace가 **정확히 한 번** 들어가고 새 topic
segment가 생기지 않음, (4) device entity는 `(namespace, logical_member)`당 device identifier 하나를
쓰고 system은 `local_system`으로 묶이며, group actual·switch·resume의 같은 `group_id`는 byte-identical
group device identifier를 쓰고 resume가 member device로 옮겨지지 않음, (5) invalid `topic_prefix`는
namespace hashing·entity construction 전에 거부됨, (6) 같은 input의 state·command·global/device/group
availability topic이 D1b 전 exact 문자열과 동일, (7) 같은 prefix의 observer·control projection이
같은 namespace를 쓰고 두 mode에 공통으로 존재하는 device·system·group-actual identity와 device identifier가
byte-identical하고 전체 Home Assistant device identifier 집합도 두 mode에서 동일하며 D1의 observer
control 0개·diagnostic reason 보존·empty retained publish 0회가 그대로,
(8) `a/b`와 `a/b/`는 서로 다른 namespace이고 각자의 exact state·command topic과 정합하여
digest 전 normalization이 없음, (9) 다른 `discovery_prefix`는 namespace 입력을 바꾸지 않지만 discovery
topic을 분리함, (10) 기존 B1~D1과 read-only startup·fresh publish·timeout·shutdown·redaction 단정이
그대로 통과하고 새 MQTT task·subscription·empty retained publish가 0개임을 고정합니다.

D1b source·offline test landing은 identity schema만 저장소에 추가하며 actual deployment, daemon restart,
broker·Home Assistant·device·app·원격 환경 실행과 장비 write를 포함하지 않습니다. 하드웨어
`NO-GO / BLOCKING`과 Q2 `UNKNOWN`, frozen native harness를 유지합니다. 같은 discovery namespace
질문을 반복해 정보가 늘지 않으면 §7 예산에 따라 park합니다.

### 2026-09-09 Home Assistant owned discovery variant cleanup D1c 범위 확장

D1b source·docs·offline test commit `4740705ab043a916eea74dddb3e047e67ae8626a`의 완료를 보고한 뒤
repository maintainer는 “이어서 진행하자”고 답했습니다. 이 발언은 다음 제품 작업을 계속하라는
요청이며 D1c 기능을 이름으로 선택하거나 broker retained state, Home Assistant, actual deployment,
장비·앱·원격 환경 실행 및 장비 write를 승인한 발언이 아닙니다. D1c의 선택과 아래 경계는 그 뒤
current source의 read-only static trace와 적대적 검토에서 정했으며 후속 slice의 상시 승인으로
일반화하지 않습니다.

D1은 위 MQTT 테스트의 기존 deletion 0 단정을 완화하려면 별도·사전 repository maintainer 결정을
요구했습니다. maintainer의 일반적인 “이어서 진행하자”는 그 결정을 묻기 전에 나온 발언이므로
그 면제로 취급하지 않습니다. maintainer가 답할 좁은 질문은
`test_observer_adapter_does_not_publish_discovery_deletions`의 “모든 discovery payload가 non-empty”
단정 하나를 “current non-empty config와 current namespace의 owned `V - C` tombstone이 정확히
분할되고 다른 retained deletion은 0개”라는 더 강한 단정으로 교체해도 되는가입니다. 이 결정은
그 테스트 하나·그 단정 하나에만 적용하며, 답이 나오기 전까지 D1c source slice는 park합니다.

이번 작업이 닫을 질문은 D1b가 결속한 current instance namespace와 current topology 안에서,
mode·qualification 전환으로 더 이상 publish하지 않는 Home Assistant discovery platform variant만
current entity와 구조적으로 구분해 제거하면서 다른 namespace, pre-namespace legacy, runtime MQTT
topic과 B1~D1b의 장비 제어 의미를 바꾸지 않을 수 있는가입니다. 근거는 firmware 가설이 아니라
current projection의 entity construction과 adapter startup ordering에 대한 static trace, namespace·set
difference·publish ordering 회귀입니다. source·offline test landing 자체는 §3 P1을 주장하지 않고,
D1b가 기록한 pre-namespace actionable duplicate-control deployment blocker도 해제하지 않습니다.

이 확장은 다음 파일로 한정합니다.

- owned discovery variant 집합 유도에 한한 `src/jebao_flow/local_control/ha.py`
- bounded tombstone publish와 startup ordering에 한한 `src/jebao_flow/local_control/mqtt.py`
- 필요한 회귀에 한한 `tests/unit/test_local_control_ha.py`, `test_local_control_mqtt.py`
- cleanup 경계 기록에 한한 `docs/local-control.md`, `docs/home-assistant.md`

`src/jebao_flow/config.py`, `src/jebao_flow/groups/*`, `src/jebao_flow/local_control/catalog.py`,
`group_command.py`, `group_service.py`, `group_supervisor.py`, `state.py`, `group.py`, `coordinator.py`,
`lease.py`, `device_writer.py`, `journal.py`, `local_control_cli.py`, old `src/jebao_flow/mqtt/*`,
`src/jebao_flow/app.py`, `custom_components/*`, `src/jebao_flow/protocol/*`, `src/jebao_flow/devices/*`,
기존 여섯 frozen source와 그 직접 테스트, `docs/runs/*`는 수정하지 않습니다. 새 config field,
operator opt-in, broker query, retained inventory read, durable prior-projection history를 만들지 않습니다.

projection은 raw deletion topic을 외부에서 받지 않고, D1b의 current instance namespace와 current
`discovery_prefix` 안의 `(HomeAssistantPlatform, namespaced unique_id)` variant 집합을 current
config·manifest·catalog·group·schedule topology에서만 유도합니다. 모든 current discovery identity를
포함한 potential variant 집합을 `V`, 이번 projection이 실제 publish하는 identity 집합을 `C`라 할 때
tombstone 집합은 정확히 `V - C`입니다. identity를 topic으로 바꿀 때는 D1b가 고정한
`{discovery_prefix}/{component}/{unique_id}/config` 형식만 사용합니다. 따라서 tombstone 집합과
current discovery topic 집합의 교집합은 구조적으로 0이고, arbitrary topic string이나 state topic
역파싱을 deletion authority로 사용하지 않습니다.

`V`는 projection 결과에서 역산하지 않고 raw manifest devices × catalog functions, validated schedule
plans × slaves × 48, config groups × members에서 **mode·qualification filter보다 먼저** 유도합니다.
`control_surface_enabled`, qualification receipt, `control_exposed`, per-device physical qualification·write
flag는 `V`를 바꾸지 않고 `C`와의 분할만 바꿉니다. `V`는 다음 bounded 상위집합입니다.

- non-system device function마다 current SENSOR variant와, 기존 `_platform_for()`가 그
  `descriptor.kind`를 control 가능으로 접었을 때 SENSOR가 아닌 경우의 SWITCH·NUMBER·SELECT·BUTTON
  variant를 함께 포함합니다. 새 kind-to-platform 표를 복제하지 않습니다.
- current schedule plan의 검증된 slave마다 `slot_index 0..47`의 NUMBER variant를 포함합니다.
- current group마다 group SWITCH와 current member별 resume BUTTON variant를 포함합니다.
- system function과 group actual-output은 양 mode에서 유지되는 current SENSOR만 포함하고 alternate
  variant를 만들지 않습니다.

`HomeAssistantPlatform.BINARY_SENSOR`는 current source에 발행 경로가 없으므로 `V`에 추측으로
추가하지 않습니다. 상한은 `48 × Σ|plan.slaves| + Σ(device function당 최대 2 variant) +
Σ(group당 1 switch + |members| buttons)`에 양 mode의 current-only SENSOR를 더한 current topology
함수이며, reconnect 횟수나 broker history에 따라 커지지 않습니다.

존재한 적 없는 potential topic에 대한 zero-byte retained publish는 broker의 no-op이고, 집합 크기는
current topology에 의해 유계입니다. 반면 current config에서 제거된 device·group·member·schedule,
과거 `descriptor.kind`, 과거 `discovery_prefix`, 다른 instance namespace와 pre-namespace object id는
`V`에 추측으로 복원하지 않으며 자동 삭제하지 않습니다. 이 범위 밖 retained state는 `UNKNOWN`으로
남기고 D1b가 요구한 별도 exact legacy inventory·제거·복구 절차가 담당합니다. schedule topology는
각 invocation의 `--schedule-pair`가 있으면 그것을, 없으면 current config의 `native_pair`를 소비하므로,
이 입력이 빠지거나 바뀌어 더 이상 유도되지 않는 과거 slot topic도 D1c가 정리했다고 주장하지 않습니다.

여기서 “owned”는 exact `topic_prefix`가 한 daemon instance에 유일하다는 operator configuration
불변식 아래에서만 성립합니다. current code에는 이 유일성을 host 간에 강제하는 namespace lease가
없고 per-device write lock도 그 역할을 하지 않습니다. 같은 exact `topic_prefix`를 두 daemon이 다른
mode·qualification으로 공유하면 namespace도 같아 한쪽 `V - C`가 다른 쪽 current discovery를
삭제할 수 있습니다. 이를 추측성 lock이나 새 config gate로 막지 않고, 코드가 강제하지 않는 조건과
상호 삭제 failure mode를 문서화해 actual deployment NO-GO의 추가 근거로 유지합니다.

adapter는 MQTT connection마다 먼저 기존 순서대로 global·device·group availability를 `offline`으로
publish한 뒤, command topic을 subscribe하기 **전에** 정렬·중복 제거된 tombstone topic 각각에 exact
zero-byte payload `b""`, QoS 1, `retain=True`를 정확히 한 번 publish합니다. 그 뒤에만 current command
topic subscribe, non-empty current discovery config publish, diagnostic state와 availability `online`을
진행합니다. 이 순서는 daemon 자신의 tombstone 발행 국면 동안 command subscription을 열지 않지만,
broker 전달과 Home Assistant 처리는 비동기이므로 화면의 stale actionable window가 완전히 닫혔다고
주장하지 않습니다. tombstone publish가 실패하면 subscription·current discovery·online으로 진행하지 않고
기존 MQTT 오류 전파와 offline availability를 유지합니다. 같은 mode의 재기동에서도 alternate
tombstone은 connection당 다시 한 번 나갈 수 있으며 이는 idempotent입니다. 근거 없이 0회라고
주장하거나 broker history를 조회해 최적화하지 않습니다.

삭제 범위는 discovery config topic으로만 한정합니다. pre-namespace와 다른 namespace의 discovery,
current discovery, device·group state, command, availability, lifecycle status에는 empty payload를
publish하지 않습니다. current config payload는 계속 canonical non-empty JSON, QoS 1, retained로
publish하고, runtime command QoS 0·retained/duplicate rejection·single consumer·fresh state·shutdown task
의미는 바꾸지 않습니다. 새 task·subscription·write path·journal·coordinator·stable reason·firmware
gate를 만들지 않으며 public projection field 집합과 `schema_version` 1도 바꾸지 않습니다.
zero-length 외의 `null`, `{}` 또는 JSON empty string은 Home Assistant deletion이 아니라 잘못된 config가
되므로 사용하지 않습니다. stale와 current entity가 같은 state·command·availability topic을 공유하므로
그 topic을 지우면 current entity까지 손상되는 것이 이 금지의 load-bearing 근거입니다.

이 D1c는 D1과 D1b가 고정한 “deletion 목적 empty retained publish 0회”를 **current D1b namespace의
`V - C` discovery config에 한해서만** 대체합니다. 그 외 topic의 0회 의미와 observer control surface
0개·qualification 미열람·capability diagnostic 보존은 그대로입니다.
repository maintainer가 위 좁은 질문을 승인하면,
`test_observer_adapter_does_not_publish_discovery_deletions`의 모든 discovery payload가 non-empty라는
기존 한 단정만 이 좁은 supersession을 반영해 current non-empty config와 owned tombstone을 정확히
분할하는 더 강한 단정으로 교체합니다. 그 밖의 기존 테스트와 단정은 완화·삭제하지 않습니다.

회귀는 최소한 (1) tombstone 집합과 current discovery·모든 state·모든 command·모든 availability topic
네 집합의 교집합이 각각 0, (2) 모든 tombstone object id가 current namespace를 정확히 한 번 포함하고
current `discovery_prefix`의 exact topic 형식을 쓰며 다른 prefix·namespace·pre-namespace id가 0개,
(3) 같은 raw topology의 observer·control 및 qualified·unqualified projection에서 `tombstone ∪ current`
합집합은 동일하고 분할만 이동하며 observer 쪽 tombstone에 qualified device non-SENSOR, slot NUMBER,
group SWITCH·resume BUTTON이 포함되고 current SENSOR·group actual·system diagnostic은 제외됨,
(4) adapter combined event ordering에서 모든 tombstone이 마지막 availability-offline publish 뒤, 첫
subscribe와 첫 current discovery publish 앞에 있고 sorted unique topic마다 exact `b""`/QoS 1/retained로
정확히 한 번이며 connect 전체 empty publish 총수가 정확히 `|V - C|`이고 current topic과
state·command·availability에는 empty publish 0회, (5) 위 상한을
초과하지 않고 연속 두 connection이 같은 deterministic tombstone sequence를 만들며 current topology에서
제거한 group·member·schedule과 과거 discovery prefix를 추측하지 않음, (6) device variant는 기존
`_platform_for()` 결과를 그대로 소비해 kind mapping을 복제하지 않고 미매핑 kind와 BINARY_SENSOR를
대상에 추가하지 않음, (7) tombstone publish fault injection이 subscription·current discovery·online을
0회로 유지하고 예외를 전파, (8) D1 observer의 control topic subscription 0·reason 보존과 D1b
namespace·topic-byte 불변, B1~C1의 write-disarmed startup·plan provenance·mandatory OFF·watermark·verdict·
sticky close 및 full read-only suite가 그대로 통과함을 고정합니다.

D1c source·offline test landing은 owned cleanup algorithm만 저장소에 추가하며 실제 MQTT connection,
broker retained mutation, Home Assistant, daemon restart·deployment, device·app·원격 환경 실행과 장비
write를 포함하지 않습니다. D1c 뒤에도 pre-namespace actionable legacy는 그대로일 수 있으므로 exact
inventory·제거·복구 절차가 별도 검토·승인되기 전에는 D1b identity schema를 deploy하지 않습니다.
같은 `topic_prefix` 유일성과 Home Assistant의 tombstone 처리 완료를 daemon이 증명하지 못하므로
D1c 자체도 그 deployment blocker를 해제하지 않습니다.
하드웨어 `NO-GO / BLOCKING`과 Q2 `UNKNOWN`, frozen native harness를 유지합니다.

#### 2026-09-09 D1c 단일 회귀 교체 승인

repository maintainer는 위의 좁은 질문에 “응 진행해”라고 답했습니다. 이 답은
`test_observer_adapter_does_not_publish_discovery_deletions`의 “모든 discovery payload가 non-empty”
단정 하나를, current non-empty config와 current namespace의 owned `V - C` tombstone이 정확히
분할되고 다른 retained deletion은 0개라는 단정으로 교체하는 것만 승인합니다. 이에 따라 D1c
source·offline 회귀의 park를 해제합니다. broker retained mutation, Home Assistant, daemon deployment,
장비·앱·원격 환경 실행과 장비 write 승인으로 넓히지 않으며, 다른 기존 단정은 완화·삭제하지 않습니다.

### 2026-09-09 pre-namespace discovery 관측 inventory와 selection D1d

D1c source·docs·offline test commit `d048736941e9047fff7f4d496c0a8652281fb76e`의 완료 뒤
repository maintainer는 “다음 진행하자”고 요청했습니다. 이 발언은 다음 제품 작업을 계속하라는
요청이며 legacy discovery cleanup을 이름으로 선택하거나 실제 broker·Home Assistant·daemon deployment,
장비·앱·원격 환경 실행 및 장비 write를 승인한 발언이 아닙니다. 다음 경계의 선택은 그 뒤 current
source와 D1b 이전 source의 read-only static trace, aiomqtt 2.5.1의 MQTT 3.1.1 client semantics,
OASIS MQTT 3.1.1 §3.8.4에 대한 검토와 적대적 교차검토에서 정했습니다. 후속 slice의 상시 승인이나
외부 접촉 승인으로 일반화하지 않습니다.

이번 작업이 닫을 질문은 pre-namespace Jebao Home Assistant discovery config 중 **유계 관측 창에
실제로 전달된 retained record**의 topic과 payload 원문을 owner-only artifact에 무손실 보존하고,
그 관측 artifact의 부분집합에 한해서 repository maintainer가 별도로 제공한 exact digest 목록을
감사 가능한 selection artifact로 결속할 수 있는가입니다. 이 slice는 broker 전체 retained set의
완전성, 관측되지 않은 topic의 부재, pre-namespace record의 최종 소유권, 삭제 성공이나 deployment
가능 상태를 답하지 않습니다. 근거는 firmware 가설이나 fake broker behavior가 아니라 MQTT 표준의
retained delivery 의미와 완료 표식의 부재, current·historical discovery topic/payload construction,
private artifact filesystem 경계에 대한 static trace와 fault injection입니다.

MQTT 3.1.1은 새 subscription에 맞는 retained message를 보내도록 요구하지만 retained replay의
끝을 알리는 packet이나 count를 정의하지 않고, matching PUBLISH를 SUBACK보다 먼저 보낼 수도 있습니다.
따라서 SUBACK, 고정 quiet period, 두 scan의 우연한 동등성은 complete inventory 증거가 아닙니다.
self-published sentinel도 cross-topic retained backlog 뒤에 온다는 표준 보장이 없고 publish 0회라는
collector 경계를 깨므로 사용하지 않습니다. artifact와 public result는 오직 “predeclared window에서
관측됨”이라고 서술하며 `complete`, `absent`, `all`, `exhaustive` 같은 긍정 완전성 fact를 만들지 않습니다.
관측되지 않음은 broker 부재나 이미 삭제됨으로 접지 않습니다.

이 확장은 다음 파일로 한정합니다.

- 새 broker-neutral 관측·분류·selection 모델과 owner-only store인
  `src/jebao_flow/legacy_discovery_inventory.py`
- source module로만 실행하는 별도 CLI `src/jebao_flow/legacy_discovery_inventory_cli.py`
- 새 모듈 전용 회귀 `tests/unit/test_legacy_discovery_inventory.py`,
  `tests/unit/test_legacy_discovery_inventory_cli.py`
- 관측·selection·후속 mutation park 경계 기록에 한한 `docs/local-control.md`,
  `docs/home-assistant.md`

`src/jebao_flow/local_control_cli.py`, `src/jebao_flow/local_control/{ha,mqtt,catalog,group_command,
group_service,group_supervisor,state,group,coordinator,lease,device_writer,journal,install}.py`,
`src/jebao_flow/config.py`, `src/jebao_flow/groups/*`, old `src/jebao_flow/mqtt/*`,
`src/jebao_flow/read_only_collector.py`, `read_only_collector_cli.py`, `source_attestation.py`,
`src/jebao_flow/protocol/*`, `devices/*`, `app.py`, `custom_components/*`, 기존 여섯 frozen source와
그 직접 테스트, `docs/runs/*`, `pyproject.toml`, `config.example.yaml`는 수정하지 않습니다. 새 config
field, write-enabled flag, broker-specific `$SYS`·administrative API parser, discovery tombstone publish,
Home Assistant registry mutation, device write path, coordinator·journal·runtime task를 만들지 않습니다.

collector는 current config의 exact `discovery_prefix`에 대해
`{discovery_prefix}/+/+/config` subscription 하나만 사용합니다. D1b가 고정한 optional node-id 없는
discovery topic 형식보다 넓은 `#` filter를 사용하지 않습니다. 수신 message 중 `retain is True`, payload가
non-empty bytes이고 object id가 `jebao_flow_local_`로 시작하는 exact 3-level discovery config만 artifact
기록 전에 메모리에서 선별합니다. live publish, non-config topic, 더 깊은 node-id 형상, 다른 integration
object id와 empty deletion payload는 private artifact에도 기록하지 않습니다. record 수·개별 payload·
전체 artifact byte 상한을 고정하고, 같은 topic의 byte-identical QoS 중복은 하나로 접되 다른 payload가
겹치면 두 관측을 모두 보존하고 그 topic을 ambiguous·selection 불가로 표시합니다. 상한을 넘으면
truncate·부분 artifact를 남기지 않고 artifact 생성을 실패시킵니다.

inventory client는 매 실행 새 random client id와 non-persistent clean session을 사용하며 will을 설정하지
않습니다. publish API는 sentinel·availability·diagnostic을 포함해 한 번도 호출하지 않습니다. 고정 client
id로 실행 중 daemon session을 밀어내거나 adapter의 router·poller·group service를 재사용하지 않습니다.
관측 시간은 monotonic deadline으로 유계이며 외부 cancellation을 그대로 전파합니다. 실제 첫 broker
연결은 이 source landing과 별개의 구체적인 broker-contact 승인 대상이지만, pump hardware write가
아니므로 현장 감시·물리 전원 차단을 software gate로 복제하지 않습니다.

private inventory artifact는 exact topic UTF-8 bytes와 raw payload bytes, retain fact, 관측 UTC span,
source kind `mqtt_retained_window_observation`, exact filter digest, current config·`discovery_prefix`·
`topic_prefix` binding digest를 canonical bytes로 보존합니다. root와 generation은 owner-only 0700,
파일은 0600이며 symlink를 따르지 않고 no-clobber 생성, file fsync 뒤 parent-directory fsync가 성공한
뒤에만 usable artifact id를 반환합니다. 부분 기록·late fsync ambiguity는 성공으로 보고하지 않습니다.
public stdout·stderr·exception·log에는 raw topic, payload 부분열, exact prefixes, broker host·username·
password나 private 절대경로를 싣지 않고 opaque artifact id, record·candidate count, UTC span과 SHA-256
digest만 냅니다.

분류기는 artifact에 이미 보존된 record 밖으로 candidate를 확장하지 않습니다. D1b 이전 commit
`cc68af25fa73104d06688ab296b7f5d0c6c2a9f0`의 device function·system function·schedule slot·group
actual-output·group switch·member-resume ID 형식과 당시 platform mapping은 advisory classification에만
사용합니다. payload `unique_id`와 discovery object id의 exact 일치, JSON object·bounded field shape,
pre-namespace 형식, payload 안 topic-valued field의 current exact `topic_prefix` binding을 별도 fact로
보고합니다. current `instance_<64-lowercase-hex>` namespace, malformed JSON, 다른 prefix binding,
unknown family와 한 topic의 conflicting payload는 selection artifact 입력으로 거부합니다. historical
공식 일치도 소유권이나 삭제 권한으로 취급하지 않습니다.

selection은 interactive prompt나 “전체 선택” option이 아니라 별도 offline command입니다. 입력은
repository maintainer가 제공한 exact record id·topic SHA-256·payload SHA-256 목록이고, 출력은 inventory
digest, current config·discovery-prefix·topic-prefix binding digest, 각 선택 record의 두 digest와 bounded
classification을 포함한 owner-only no-clobber selection artifact입니다. 입력 항목이 inventory의 정확한
부분집합이 아니거나 어느 digest·binding이라도 다르면 broker 접촉 전에 거부합니다. selection은 사람이
검토한 소유권 결정을 감사 가능하게 보존할 뿐이며 broker에 그 record가 아직 존재함, inventory가
완전함, 삭제가 승인됨을 주장하지 않습니다.

apply·restore source는 이 D1d에서 만들지 않고 park합니다. 후속 apply는 실제 첫 inventory artifact를
검토한 뒤 별도 정책·적대적 검토와 repository maintainer의 좁은 mutation 결정을 받아야 합니다.
그 정책은 최소한 선택 topic의 exact retained payload가 fresh positive readback에서 baseline digest와
일치하지 않거나 관측되지 않으면 **abort**하고, 미관측을 absence·이미 삭제됨·skip 성공으로 읽지 않아야
합니다. local exclusive claim은 다른 host publisher를 막는 ownership proof가 아니며, ambiguity나 partial
failure를 자동 restore로 덮지 않습니다. 후속 restore도 bounded readback의 미관측을 broker absence
증거로 사용하지 않고, 다른 non-empty successor를 관측하면 절대 덮어쓰지 않습니다. broker-native
complete snapshot 또는 별도로 입증된 deployment-specific acquisition evidence는 artifact provenance로
결속할 수 있지만 generic collector에 broker별 추측 코드를 넣지 않습니다.

D1d는 §4의 이미 소진된 “첫 write-free read-only collector 구현 1회” 예외를 원용하거나 재사용하지
않습니다. 그 예외는 실제 장비 pilot 뒤 결과 파일보다 먼저 `src/`를 다시 수정하지 못하게 한 순서에서,
최초 장비 collector가 아직 없을 때만 source 구현을 허용한 것입니다. §4 본문이 밝히듯 그 순서는 실기
실행 뒤에 발동하고, D1d source landing은 장비뿐 아니라 broker에도 접촉하지 않으므로 D1·D1b·D1c와
같이 그 예외가 필요하지 않습니다. 첫 실제 broker 관측을 §4 run 기록 대상으로 볼지는 별도
broker-contact 승인에서 결정하며, 어느 쪽이든 소진된 예외를 근거로 삼지 않습니다. 기록 대상으로
결정하면 그 결과 파일을 먼저 커밋할 때까지 추가 `src/` 수정은 하지 않습니다.

회귀는 최소한 (1) fake client의 publish 0회·will 없음·실행별 client id 차이·clean session과 exact
`{discovery_prefix}/+/+/config` subscription 하나, (2) retained/non-retained, exact/deeper topic,
Jebao/third-party object id가 섞여도 eligible retained Jebao config만 **기록 전에** 남음, (3) artifact와
public result가 bounded observation만 말하고 미관측·complete·absence fact를 만들지 않음, (4) legacy
여섯 family classification과 D1b namespaced·malformed·other-prefix 격리가 inventory 밖 candidate를
만들지 않음, (5) selection이 exact inventory 부분집합과 topic·payload·inventory·config binding digest를
모두 검사하고 불일치는 network factory 호출 0회에서 끝남, (6) existing destination·symlink·mode·link
count·file/parent fsync fault가 no-clobber 또는 unusable failure로 끝나고 raw bytes round-trip은 exact,
(7) stdout·stderr·exception과 public dict가 ASCII이고 private topic·payload·prefix·credential 부분열을
포함하지 않음, (8) 새 source import closure가 frozen six·write edge·`protocol/*`·groups runtime·
`read_only_collector`·local-control MQTT adapter를 포함하지 않으며 기존 D1~D1c와 full read-only 단정이
수정 없이 통과함을 고정합니다. conflicting payload topic은 모두 보존되지만 selection 0개이고, 상한
초과는 truncate·부분 artifact 없이 실패함을 함께 고정합니다. 기존 테스트의 assert를 삭제·완화·
개명하지 않습니다.

D1d source·offline test landing은 collector와 review artifact 계약만 저장소에 추가하며 실제 broker
connection, retained mutation, Home Assistant, daemon restart·deployment, device·app·원격 환경 실행과
장비 write를 포함하지 않습니다. 한 번의 observed inventory가 생기기 전에는 apply 조건을 fake에 맞춰
구체화하지 않습니다. D1b/D1c deployment는 pre-namespace actionable legacy와 exact `topic_prefix`
유일성, cleanup 처리 완료가 확인되지 않아 계속 별도로 blocked이고, 하드웨어 `NO-GO / BLOCKING`,
Q2 `UNKNOWN`, frozen native harness를 유지합니다.

### 2026-09-10 Home Assistant 첫 배포 표면 선택과 read-only 감사 D1e

repository maintainer는 Jebao Home Assistant·local-control을 과거에 배포하거나 사용한 적이 없고
지금 처음 만드는 것이라고 확인한 뒤, 홈서버의 Home Assistant 상태를 SSH로 읽기 전용 감사하라고
지시했습니다. 감사 결과와 다음 단계 설명을 받은 뒤 “ㅇㅇㅇ 다음단계 수행해”라고 답했습니다. 이
답은 아래의 Home Assistant 표면 정합화, 문서 정정, current `jebao-flow-local` observer 첫 배포와
read-only 검증을 진행하는 승인으로 결속합니다. control mode, 장비 write, vendor app 조작이나
pre-namespace retained config 삭제 승인은 아닙니다. observer operation에는 장비 write가 없으므로
현장 승인이나 물리 전원 차단 확인을 반복적인 software gate로 추가하지 않습니다.
답변 전에 제시한 다음 단계도 문서 정합화와 observer 첫 배포·read-only 검증이었으며, 이 결속은
그 제시 범위와 같습니다.

이번 작업이 닫을 질문은 (1) bounded broker 관측과 Home Assistant registry·recorder 감사에서 실제
legacy migration 대상이 발견됐는가, (2) 현재 제품의 Home Assistant 표면이 old aggregate custom
component와 새 local-control native MQTT discovery 중 어느 경로인가, (3) 전자를 잘못 설치·구성하지
않고 후자 하나만 observer로 처음 활성화할 수 있는가입니다. 근거는 실제 write-free broker artifact,
Home Assistant의 read-only registry·recorder·설치 파일 감사, 두 MQTT contract의 static trace입니다.

첫 broker capture는 reviewed source commit `ae026be09c57b736cdd2792ad12d6a5c26b24508`와 byte-identical한
코드로 20초의 bounded window를 관측했습니다. private artifact는 opaque id
`legacy-inventory-fe3db9aa99408baad7cd8998d3aa581f`, digest
`f76f36ae4916e815281b2602445f06945d2ffe8ba24357c26a8b2545d2f993a4`, UTC span
`2026-09-09T15:31:58.438207Z..2026-09-09T15:32:18.439326Z`로 보존됐고, 전달된 152개 메시지는
모두 기록 전 필터에서 제외되어 record·candidate·ambiguous count가 각각 0이었습니다. 이는 증거등급
(b)의 persisted bounded-observation claim이며 broker retained set의 완전한 snapshot이나 부재 증명이
아닙니다.

별도 Home Assistant read-only 감사에서는 entity registry 4,539개 중 Jebao entity, MQTT Jebao entity,
pre-namespace·namespaced local-control entity가 모두 0개였고 Jebao device registry와 config entry도
각각 0개였습니다. Recorder의 current·legacy state metadata와 statistics, YAML config에서도 Jebao
entity·integration 참조가 0개였습니다. 설치된 `custom_components/jebao_flow` tree는 존재하지만
config entry가 없어 inert하고, repository 사본보다 파일 하나가 부족하며 여덟 파일이 byte-different한
구형 사본입니다. 로그의 두 Jebao 문자열은 custom integration 일반 경고이고 integration error는
없었습니다. 이 감사는 broker retained absence를 증명하지 않지만, maintainer의 배포 이력 확인과
record·candidate 0인 bounded artifact를 함께 보면 실제로 선택·삭제·복원할 legacy 대상이 없습니다.
따라서 D1d apply·restore와 추가 capture·counter 계측은 §7에 따라 park하며, provenance 있는 eligible
record가 1개 이상 새로 생길 때만 재개합니다.

바로 앞 D1d가 유지한 D1b/D1c deployment block은 이 **첫 observer activation에만** 다음처럼 좁게
적용하지 않습니다. pre-namespace actionable legacy는 bounded record 0, Home Assistant artifact 0과
maintainer의 미배포 이력 확인으로 실제 조치 대상이 없고, broker absence를 증명했다고 일반화하지
않습니다. exact `topic_prefix` 유일성은 code-enforced가 아니라 시작 전 host-local process·Compose
inventory와 maintainer의 단일 홈서버 배포 확인으로 충족하는 operator precondition이며 cross-host
daemon 부재를 증명하지 않습니다. cleanup 처리 완료는 prior Jebao retained config가 실재한 이력이
없어 이번 첫 activation에는 삭제할 대상이 없으므로 moot이지, Home Assistant 처리를 증명한 것이
아닙니다. control mode 배포나 이 precondition을 확인하지 못한 배포에는 기존 block이 그대로
유효합니다.

이 broker·Home Assistant 감사는 장비나 펌프의 동작 질문을 측정한 실기가 아니라 첫 배포 전 외부
상태의 write-free 감사입니다. 따라서 §4 `docs/runs/` hardware run으로 분류하지 않고, 이미 소진된
collector 예외도 원용하지 않습니다. capture 승인은 read-only broker 접촉 1회에 한정됐고 retained
mutation을 승인하지 않았다는 범위도 이 절에 함께 보존합니다. 이 결정 때문에 후속 `src/` 변경 순서가
§4 run record를 기다리지는 않지만 frozen native harness는 계속 동결합니다.

static trace로 두 Home Assistant 계약은 대체 관계임을 확인했습니다. `custom_components/jebao_flow`
runtime은 old `jebao-flowd`의 aggregate `system/config`, `groups/+/state`, `devices/+/state`,
`requests/+/result` 계약만 소비하고 aggregate group·device command를 발행합니다. 반면 current
deployment adapter인 `jebao-flow-local serve`는 `src/jebao_flow/local_control/ha.py`가 만드는 native
MQTT Discovery와 `topic_prefix/local/...` state·command만 사용하며 old aggregate topic을 만들지
않습니다. custom card도 custom component entity의 `jebao_flow_*` attributes에 의존하므로 native
discovery entity를 자동 소비하지 않습니다. 두 daemon·HA surface를 함께 띄우는 것은 current 제품
구성이 아니며, 같은 물리 장비에 대해 중복되고 서로 다른 authority 표면을 만듭니다. MQTT topic
tree는 서로 달라도 old writer는 local-control의 per-device lease와 journal에 참여하지 않으므로
동시 배포 시 duplicate write, 서로의 계산을 모르는 out-of-envelope 결과, local-control journal이
모르는 상태를 rollback하는 손실 가능성이 생깁니다. 이는 source가 저장소에 함께 있는 현재 상태의
P1이 아니라 **동시 배포 시점의 §3 P1**입니다. 일반 daemon·old MQTT 경로는 앞선 하위절에서도
계속 동결한다고 명시됐으므로, 이번 작업은 그 경로를 기동·수정하거나 조용히 해제하지 않습니다.

따라서 첫 배포의 유일한 current Home Assistant 경로는 **built-in MQTT integration +
`jebao-flow-local` native MQTT Discovery**입니다. 홈서버의 unconfigured 구형 custom component를
repository 사본으로 덮거나 config entry를 만들거나 Lovelace card를 등록하지 않습니다. 그 tree는
이번 observer 배포에 필요하지 않고, 단순 sync는 local-control 계약을 연결하지 못합니다. 제거 역시
이 작업의 필요조건이 아니므로 별도 Home Assistant mutation으로 확대하지 않습니다. custom card를
native discovery에 맞추는 작업은 별도 제품 slice로 park합니다. old `jebao-flowd`와 current
`jebao-flow-local`을 동시에 실행하지 않습니다. `pyproject.toml`의 old console entry를 삭제하는 것도
동결 해제가 필요하고 기동 금지보다 추가 보호가 없으므로 이번 범위에 넣지 않습니다.

문서 정합화는 `docs/home-assistant.md`, `docs/local-control.md`, `README.md`, `PROJECT_CONTEXT.md`에서
current native-discovery 경로와 parked legacy custom-component 경로를 명확히 구분하는 데 한정합니다.
`custom_components/*`, old `src/jebao_flow/mqtt/*`, `src/jebao_flow/app.py`, frozen six와 직접 테스트,
write edge, groups runtime, protocol·devices, `docs/runs/*`는 수정하지 않습니다. 기존 custom component
source와 test는 역사적 aggregate 계약 보존용으로 유지하며 current deployment에 사용된다고 주장하지
않습니다.

observer 첫 배포는 reviewed current source와 private current config·manifest를 사용해 정확히 한
`jebao-flow-local` instance만 시작합니다. 시작 전에 config의 `runtime.mode=observer`, current
`topic_prefix`를 쓰는 다른 daemon 0개, old `jebao-flowd` 0개, custom component config entry 0개를
확인합니다. qualification은 읽지 않고 coordinator·journal·writer를 구성하지 않으며 command topic을
projection·subscribe하지 않습니다. 이 확인은 구체적인 authority 충돌 방지이고 generic 현장 gate가
아닙니다. 불일치가 있으면 추측해 서비스를 고치지 않고 보고합니다.

첫 활성화는 native discovery config와 D1c의 owned current-namespace tombstone을 broker에 publish하고
Home Assistant가 entity를 생성하는 외부 mutation임을 숨기지 않습니다. 이 mutation은 maintainer의
“다음단계 수행” 승인에 포함하되, 장비에는 read-only fresh state session만 열고 control frame은 0회로
유지합니다. 검증은 (1) local-control service exactly one, (2) HA에 current namespace의 native entity가
생기고 legacy custom entity·config entry는 0, (3) observer control entity·command subscription 0,
(4) fresh state와 availability가 현재 관측으로 갱신, (5) local-control journal·write coordinator·device
writer 사용 0을 확인합니다. 이 observer 결과는 Q1·Q2 또는 firmware capability 판정으로 승격하지
않고, control·실기 단계는 별도 concrete operation 지시 뒤에 진행합니다. 하드웨어는 계속
`NO-GO / BLOCKING`, Q2는 `UNKNOWN`이고 frozen native harness와 일반 daemon·old MQTT 경로의
동결도 유지합니다.

### 2026-09-10 observer의 장비별 endpoint 격리 D1f

D1e가 승인한 첫 native observer 활성화는
[`docs/runs/2026-09-10-local-observer-first-activation.md`](docs/runs/2026-09-10-local-observer-first-activation.md)에
먼저 기록하고 commit `aff9413`으로 source 수정 전에 고정했습니다. 같은 discovery 관측에서 current
physical binding 6개 중 4개가 정확히 한 번씩 식별되고 2개는 관측되지 않았지만, read-only poller는
6개 모두를 `current_discovery_unavailable`로 publish했습니다. static trace에서 poller가 writer와 같은
strict `resolve_many()`를 호출하고 첫 unresolved target의 예외를 batch 전체 실패로 접는 것이 그
원인입니다. 이번 작업이 닫을 질문은 **한 번의 current discovery에서 정확히 식별된 장비의 fresh
read를 살리면서, writer의 all-or-nothing resolution과 wrong-device 방어를 전혀 완화하지 않을 수
있는가**입니다.

관측되지 않은 두 장비가 전원·LAN·stale config 중 어느 이유인지 이 slice에서 추측하거나 고치지
않습니다. 또한 absent와 ambiguous는 같은 사실이 아닙니다. match 0개는
`current_endpoint_absent`, match 2개 이상은 wrong-device 선행 신호인
`current_endpoint_ambiguous`로 해당 target에만 보고합니다. discovery socket 자체가 실패한 경우의
`current_discovery_unavailable`과도 구분합니다.

구현은 `src/jebao_flow/local_control/resolver.py`의 기존 matching 계산을 하나의 순수한 per-target
observation 함수로 공유하고, target 순서를 보존하는 observer 전용 batch API를 추가하는 데
한정합니다. 이 API는 cycle당 current discovery를 정확히 한 번만 수행하고 각 target에 matched
endpoint 또는 위 두 diagnostic 중 하나를 돌려줍니다. 기존 `resolve()`와 `resolve_many()`는 그
분류를 사용하더라도 non-match에서 기존 `CurrentEndpointResolutionError` 타입과 메시지,
all-or-nothing 의미를 그대로 유지합니다. coordinator의 다섯 strict resolution 호출부와 write
경로는 수정하지 않습니다.

`src/jebao_flow/local_control/state.py`의 poller만 observer API를 소비합니다. 반환 길이·순서,
observation target, matched endpoint의 target이 현재 manifest와 byte-equivalent하지 않으면 새 API의
주장을 신뢰하지 않고 batch 전체를 기존 `current_resolution_binding_mismatch`로 유지합니다. 정상
matched target은 기존 shared read lease·read-only session·fresh projection 순서를 그대로 수행하고,
absent·ambiguous target만 publication 없는 unavailable 결과가 됩니다. endpoint address를 cache,
artifact, public diagnostic에 보존하지 않습니다.

source 범위는 위 두 파일과 `tests/unit/test_local_control_resolver.py`,
`tests/unit/test_local_control_state.py`, 동작 경계 기록에 한한 `docs/local-control.md`입니다. frozen six와
직접 테스트, coordinator·device writer·journal·lease·groups, MQTT adapter, config, protocol·devices,
old daemon·MQTT, custom component는 수정하지 않습니다. 이 결함은 정상 장비를 unavailable로 보는
false-negative라 §3 P1로 과장하지 않으며, 새 write gate 없이 read 표면의 과도한 batch 실패를 줄이는
§2 정합 수정입니다.

회귀는 (1) one discovery에서 matched 4·absent 2이면 네 target만 fresh state와 online availability로
진행하고 두 target만 `current_endpoint_absent`, (2) ambiguous target 하나는 다른 target을 막지 않고
정확히 `current_endpoint_ambiguous`, (3) discovery 자체 예외는 계속 전 target
`current_discovery_unavailable`, (4) 길이·순서·target binding 불일치는 계속 전 target
`current_resolution_binding_mismatch`, (5) existing strict resolver·coordinator assertions를 삭제·완화
없이 통과하고 strict 예외 타입·메시지가 불변, (6) observer cycle당 discovery 호출 1회와 address
비공개를 고정합니다. source가 landing되면 reviewed commit으로 observer Compose를 교체하고 4개의
matched 장비가 fresh `observed_at` state와 online availability를 실제 publish하는지 다시 확인하되,
장비 write·control mode·vendor app 조작은 수행하지 않습니다. 교체는 기존 instance를 먼저 정지한
뒤 새 instance를 시작해 중첩을 0으로 유지하며, 시작 직전에 D1e의 네 precondition
(`runtime.mode=observer`, 같은 `topic_prefix`의 다른 daemon 0, old `jebao-flowd` 0, custom component
config entry 0)을 다시 확인합니다. 이 확인은 host-local이고 cross-host daemon 부재를 증명하지
않습니다. 하드웨어 `NO-GO / BLOCKING`, Q2 `UNKNOWN`, frozen native harness와 일반 daemon·old MQTT
경로의 동결은 유지합니다.

### 2026-09-10 Local Wavemaker Pro active-template positive binding D1g

repository maintainer의 “다음 진행하자”는 qualification 경로의 다음 선행조건을 계속 해소하라는
지시로 받습니다. 이 절은 installed app과 private install input의 read-only 정적 감사, 그 증거를
current observer manifest에 결속하는 작업까지만 승인합니다. 제바오 앱 조작, local-control의 장비
write, qualification receipt 발급이나 control mode 활성화 승인은 아닙니다. 기존 D1e observer의
stop-first 교체와 read-only 검증만 그 절이 이미 승인한 범위 안에서 재사용합니다.

이번 작업이 닫을 질문은 **현재 설치된 Jebao Aqua app build와 현재 결속된 Local Wavemaker Pro의
product identity에 대해, 세 cache 후보 중 실제 loader가 선택하는 template bytes를 “newest cache”
추측 없이 positive evidence로 특정할 수 있는가**입니다. qualification receipt를 아무리 정확히
만들어도 fresh manifest의 `template_sha256` set이 비어 있으면 `catalog.py`의 기존 membership gate에서
control qualification이 성립하지 않으므로, 첫 code-driven write 승인보다 이 증거와 manifest 결속이
먼저입니다. 해당 gate와 `qualification.py`를 완화하거나 수정하지 않습니다.

read-only static trace는 app을 실행하거나 app·broker·장비·network에 접촉하지 않고 installed app
version `3.4.18`, build `341800000`, Hermes bytecode version 96의 bundle을 분석했습니다. bundle bytes의
SHA-256은 `97b8eb17a915b8071dcef16c177f1e5bc3ab31e154510c69e6d3f9af2a533c71`이고,
분석에는 hermes-dec commit `a0f18f97ab661eb8ed659c8c683a0d21ea619e69`을 임시 경로에서만
사용했습니다. decompiler, decompiled source와 raw app/cache 산출물은 저장소에 넣지 않습니다.

증거 사슬은 다음과 같습니다. 현재 persisted device list의 exact product key는 원문 대신 SHA-256
`38136f879949c06a8099c94adf98b0c02ee88318f28e44556a017a56b691922e`로 결속합니다. 등록 장비
page는 그 `device.productKey`를 `getItemForPk`에 주고, current compiled product config array에서
일치하는 단일 config object를 얻어 `templates.asyncGetComponent(config)`와 `goToDevicePage`로
전달합니다. 그 object의 template download URL은 하나이며 원문 대신 SHA-256
`c1aaf3dcf32d94d301029bfb57baff863d00b5d91af48551c0dd251cf5b72756`로 결속합니다. URL에서
결정적으로 유도되는 현재 cache root의 `index.js`는 182,637 bytes이고 SHA-256
`c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`입니다. 같은 product key의
bundle 출현은 이 config object와 `experienceProducts` list 하나뿐이고, 후자는 Experience Center의
제품 목록 filter·sort에만 쓰이며 등록 장비 loader의 config 선택이나 template path를 바꾸지 않습니다.
따라서 다른 두 cache candidate는 오래돼 보이기 때문이 아니라 **current config URL로부터 도달할 수
없기 때문에** 이번 binding에서 제외합니다.

`asyncGetComponent`의 현재 분기도 함께 고정합니다. `templateDownloadUrl`이 없을 때만
`templateId`로 이미 등록된 component를 조회합니다. URL이 있으면 app은 URL의 MD5를 in-memory data
key와 cache directory locator로 사용하고, 성공 상태의 component가 있으면 그것을 반환합니다. 그렇지
않으면 같은 URL의 exact root에서 기존 `index.js` 또는 `index.html`을 먼저 읽고 component 생성에
성공하면 network download 없이 사용합니다. 파일이 없거나 component 생성이 실패할 때만 같은 URL을
download·unzip하고 다시 생성합니다. 이 경로에는 newest-directory scan, mtime 선택, version·ETag
비교나 다른 bundled-template fallback이 없습니다. **MD5는 vendor app의 cache locator를 재현한 값일
뿐 Jebao repository의 identity나 승인 근거가 아니며**, 우리 결속은 exact template bytes의 SHA-256만
사용합니다. 두 파일이 모두 있으면 loader는 `index.html` fallback component를 `index.js` eval보다
우선하지만, 위 current exact root에는 `index.js`만 존재하고 `index.html`은 없습니다. 따라서 이번에
실제로 선택되는 bytes는 위 182,637-byte `index.js` 하나입니다.

이 positive binding의 유효 범위는 위 exact app version·build·bundle digest, product-key digest,
URL digest와 template-content digest의 조합뿐입니다. app bundle·persisted product identity·compiled
config URL이 바뀌거나 cache가 다시 download·교체되면 active bytes를 재검증하고 새 근거로 다시
결속해야 합니다. 같은 URL이라도 remote bytes가 영원히 같다고 추측하지 않습니다. 다른 product
family, 다른 app version, firmware acceptance나 장비 write 동작으로 일반화하지 않고, public app
catalog 전체의 `active_template_version="unresolved"`를 이 한 product의 증거로 덮어쓰지 않습니다.

이번 결속은 source code 변경 없이 기존 install pipeline을 사용합니다. reviewed template root는
private install input으로만 제공하고 `prepare-*`를 새로 유도합니다. 성공 조건은 fresh manifest가
`template_count=1`이고 `template_sha256` set이 위 exact content digest 하나와 일치하며, current app
version·app catalog·schema·stable role binding도 기존 install 검증을 통과하는 것입니다. broad cache
root나 세 후보 전체를 입력으로 주지 않습니다. `template_count=1`은 이 slice의 단일-product 범위를
반영하며 후속 product에 대해 별도 positive binding이 생기면 그 검증된 digest를 추가할 수 있습니다.
digest가 다르거나 count가 1이 아니면 qualification을 열지 않고 멈춥니다. private root·product key·
URL·cache locator 원문은 public output, log, exception, commit이나 `docs/runs/`에 기록하지 않습니다.

manifest 재결속을 배포할 때는 running observer를 먼저 완전히 정지하고 새 observer 한 instance만
시작해 중첩을 0으로 유지합니다. 시작 직전에 D1e의 네 precondition을 다시 확인하고, 6/6 fresh
online, observer command surface 0, old daemon 0, role-binding digest 불변과 manifest의 exact template
count·digest를 확인합니다. 이는 장비 read-only observer 교체이며 qualification producer는 이 확인이
통과할 때까지 park합니다. 이 작업은 §4 hardware run이나 frozen native harness의 해제가 아니고,
재배포 결과는 앞선 observer 조작과 같이 새 append-only `docs/runs/` 기록으로 남깁니다. 하드웨어
`NO-GO / BLOCKING`, Q2 `UNKNOWN`, 일반 daemon·old MQTT 경로 동결을 유지합니다.

### 2026-09-10 local-control manual-flow qualification producer D1h

D1g의 manifest 결속과 observer 교체 결과는
[`docs/runs/2026-09-10-local-observer-active-template-binding.md`](docs/runs/2026-09-10-local-observer-active-template-binding.md)에
먼저 기록하고 commit `ff9bdc5`로 source 수정 전에 고정했습니다. repository maintainer의 “이어서
진행하자”는 이 다음 단계인 qualification producer의 정책·source·offline 회귀를 진행하라는
지시로 받습니다. 이 승인은 제바오 앱 조작이나 장비 write를 실행하는 승인이 아니며, 실제 operation은
producer가 offline으로 완성·검토된 뒤 한 대·한 기능·세 planned frame을 명시해 한 번만 별도로 묻습니다.

이번 작업이 닫을 질문은 **기존 local-control writer·lease·journal과 qualification verifier를 완화하지
않고, 한 Local Wavemaker Pro의 `manual_flow`에 대한 same-value → 1..5%p 하향 → exact restore를
crash-safe한 단일 operation으로 실행하고, 실제 변화와 계획된 복원을 증명하는 receipt 두 장만 만들 수
있는가**입니다. 이는 firmware 결과를 fake로 미리 정하는 작업이 아니라, static trace와 durable
fault-injection으로 write 순서·복원 권한·증거 발급의 코드 내부 계약을 닫는 작업입니다.

producer는 기존 `LocalControlJournalStore`의 **request record 하나**를 operation journal로 사용하고,
그 record에 `same_value`, `lower_power`, `planned_restore` 세 `JournalAction`을 첫 send 전에 모두
durably 생성합니다. action 0과 1이 `VERIFIED`, action 2가 `PREPARED`인 동안 transaction은 계속
`EXECUTING`이므로, step 뒤 원래 값으로 돌아가야 할 의무가 terminal success로 오인되지 않습니다.
이 조합이 `LOWER_POWER_ACTIVE`의 보유 상태이고 복원 권한은 세 request로 쪼개지지 않습니다. 각
action은 `FORWARD_ATTEMPTING`을 fsync한 뒤에만 send하고, ACK bytes를 blob으로 보존한 뒤 fresh
readback을 exact 검증해야 `VERIFIED`로 이동합니다. record의 `intent_sha256`은 target·function·
baseline·step·두 phase request id와 app/content/firmware epoch를 담은 canonical operation-intent
blob의 digest이며, 그 blob도 첫 send 전에 같은 store에 보존해 terminal 뒤 offline finalization이
휘발성 process state에 의존하지 않게 합니다. 기존 recovery가 다른 unfinished request를 거부하므로
phase별 record 세 개는 서로의 복구를 막습니다. 따라서 operation record 하나와 action 세 개를 유지합니다.

세 action은 각각 새 strict resolution과 새 `ConnectedDeviceWriter` / authenticated session을
사용합니다. 같은 writer instance가 같은 payload digest를 다시 보내지 않는 기존 duplicate guard는
수정하거나 우회하지 않습니다. same-value와 planned restore의 payload가 byte 동일해도 중간에 실제
하향 step이 있고 writer instance가 서로 다르므로, 세 frame은 각 phase의 독립 send입니다. producer는
`observe_many()`를 쓰지 않고 strict `resolve()`만 사용하며 absent·ambiguous·binding mismatch면
첫 send 전에는 send 0으로, step 뒤에는 forward 재개 없이 recovery-required로 갑니다.

same-value는 기존 first-write 계약의 transport-reachability phase라 `no_change`여도 정확히 한 번
send하고 ACK·fresh exact readback을 journal에 남기지만 receipt는 만들지 않습니다. 하향 step과
**계획된** restore만 서로 다른 receipt request id로 `LocalControlQualificationReceipt` 두 장을
만듭니다. planned restore는 자기 ACK·readback을 가진 세 번째 일급 action이고, crash recovery의
rollback action과 합치지 않습니다. crash·cancel·ACK 오류·readback mismatch에서 forward test phase를
재시도하거나 이어서 수행하지 않으며, 기존 journal recovery는 fresh strict resolution 뒤 baseline만
exact restore하고 receipt를 0장으로 유지합니다.

정상 operation은 세 action의 exact readback 뒤 transaction을 `VERIFIED`로 먼저 durably terminalize한
다음 receipt와 qualification binding을 offline finalization합니다. receipt는 step과 planned restore의
canonical intent, exact control payload, raw ACK, exact canonical observed fields의 SHA-256을 결속하고,
binding file은 두 receipt digest를 명시합니다. terminal journal 뒤 artifact finalization이 중단되면 같은
operation id의 재실행은 network·send 없이 exact journal evidence만으로 finalization할 수 있지만,
non-terminal journal은 recovery 외 어떤 forward phase도 재개하지 않습니다. receipt 하나만 남거나
binding 생성이 실패한 상태는 qualification으로 취급하지 않습니다. 재진입에서 receipt나 binding의
기존 파일이 canonical expected bytes와 byte 동일하고 기대 SHA-256과 일치하면 verify-and-accept하고,
다른 bytes이거나 digest가 다르면 덮어쓰지 않고 거부합니다. 이는 no-clobber를 유지하면서 terminal
finalization을 멱등으로 만듭니다.

pre-send envelope은 `devices/verification.py`의 값비싼 안전 지식을 느슨하게 다시 만들지 않고 다음을
코드에서 강제합니다. 대상은 current manifest와 private config가 같은 stable physical identity로
결속한 Local Wavemaker Pro 한 대이고 기능은 `manual_flow` 하나입니다. fresh decoded state는 online,
power ON, `Mode=constant`, `TimerON=false`, `Linkage=independent`, 현재 Flow `<=45`여야 합니다. step은
1..5 percentage point **하향만** 허용하고, 현재값·착지값·복원값은 current device config의
`limits.min_power` / `limits.max_power`와 기존 writer의 30..75 encoder envelope 안에 있어야 합니다.
값을 clamp하거나 상향·경계 밖 값을 대신 선택하지 않고 send 전에 실패합니다.

content/version epoch도 receipt 생산 전에 고정합니다. current manifest의 app-catalog digest와 current
protocol-schema digest가 설치 bytes와 exact 일치하고, D1g가 결속한 active template digest가 fresh
manifest set에 명시적으로 하나 존재해야 합니다. app version은 그 catalog source와 같아야 하고,
strict discovery가 돌려준 printable firmware version 하나를 결정적으로 선택해 세 phase에서 동일한지
확인합니다. config·manifest·content binding이 operation 동안 바뀌면 baseline 복원 뒤 receipt 0장으로
끝냅니다. `template_count=0`이나 추측한 newest cache를 producer 쪽에서 보정하지 않습니다.

receipt root와 output qualification file은 owner-only `0700` / `0600`, no-symlink, no-clobber,
link-count 1을 요구하고 file·parent directory fsync와 exact readback 뒤에만 usable로 보고합니다.
receipt filename은 canonical bytes의 SHA-256과 일치해야 하며 existing verifier가 그대로 읽어 두
distinct request와 같은 physical identity를 확인해야 성공입니다. public stdout·stderr·log·exception은
opaque operation/target id, phase, count, UTC, digest만 담고 logical member 원문·address·MAC·vendor
device id·status/payload/ACK bytes, credential과 private path를 노출하지 않습니다.

source 범위는 새 `src/jebao_flow/local_control/qualification_producer.py`, 자체
`src/jebao_flow/local_control_qualification_cli.py`, 그 두 전용 unit test, 가산적 import-boundary test,
`docs/local-control.md`, 그리고 별도 console entry를 위한 `pyproject.toml`로 한정합니다.
`qualification.py` verifier, `catalog.py` membership gate, `coordinator.py`, `journal.py`, `lease.py`,
`device_writer.py`, config·HA·MQTT·groups, frozen six와 직접 테스트, `devices/verification.py`, old daemon·
old MQTT, `custom_components/*`는 수정하지 않습니다. 기존 regular control surface를 활성화하거나 running
observer를 이 source landing에서 교체하지 않습니다. 새 CLI는 명시적 `--write-enabled` 없이는 producer
operation을 시작하지 않으며, 이 opt-in은 별도 live operation 승인이나 envelope 검증을 대신하지 않습니다.

offline 회귀는 최소한 다음을 고정합니다. (1) TimerON·non-constant·power OFF·non-independent·current
상한 초과·step 0 또는 >5·상향·config/encoder envelope 밖 각각 send 0, (2) strict resolver absent·
ambiguous·binding mismatch send 0, (3) 세 action과 모든 rollback blob이 첫 send보다 먼저 durable이고
phase mapping이 위 operation journal 하나에 있음, (4) same-value는 journaled·sent·verified이되 receipt
0이고 세 phase가 writer/session 세 개를 써 duplicate guard를 바꾸지 않음, (5) 성공 경로는 send 정확히
3회와 step·planned-restore receipt 정확히 2장이고 existing verifier가 그 physical key만 자격화,
(6) ACK 또는 exact readback 불일치·send fault·cancel은 receipt 0과 recovery-required이고 어떤 forward
payload도 재전송하지 않음, (7) step 뒤 crash recovery는 baseline만 복원하고 test step을 재개하지 않으며
receipt 0, (8) canonical receipt·digest filename·0700/0600·O_NOFOLLOW/O_EXCL·file/parent fsync fault를
fail-closed로 검증, (9) app/catalog/template/schema/firmware epoch 불일치면 receipt 0, (10) terminal 뒤
artifact finalization 재개는 network·writer·send 0임을 검증합니다. 기존 verifier·coordinator·writer
회귀의 assert는 삭제·완화하지 않습니다.

§3의 wrong-device는 strict current resolution, out-of-envelope은 send 전 exact envelope, duplicate
write는 journal-first phase와 writer instance별 single-send, rollback 권한은 operation record 하나와
preinstalled baseline blobs, real FAIL의 PASS 오판은 ACK와 fresh exact readback의 동시 요구로 막습니다.
이는 기존 안전핵심을 재사용한 것이며 정상 regular write에 새 gate를 추가하지 않습니다. producer
source·offline test가 landing되고 별도 적대적 검토가 끝날 때까지 앱 preconditioning과 장비 write는
0회로 유지합니다. 그 뒤 묻는 성공 경로는 현재 켜진 Pro 한 대를 앱에서 `constant 40% · TimerOFF ·
independent`로 맞추고 `manual_flow` same-value 1회 → 1%p 하향 1회 → 40% planned restore 1회이며,
다른 장비·기능은 건드리지 않습니다. 실제 run은 observer를 먼저 정지해 qualifier 한 process만 장비
session을 소유하고, terminal restore 확인 뒤 observer를 다시 시작합니다. 실패·crash recovery 권한은
그 질문에서 planned 세 frame과 분리해 명시합니다. 시작 전 D1e precondition 네 개를 다시 확인하고,
재시작 뒤 6/6 fresh·command surface 0·old daemon 0·role-binding digest 불변을 확인합니다. 하드웨어
`NO-GO / BLOCKING`, Q2 `UNKNOWN`,
frozen native harness와 일반 daemon·old MQTT 경로 동결을 유지합니다.

### 2026-09-11 terminal exact-restore tombstone의 local-control idle 판정 D1i

D1h qualification의 live preflight 결과는
[`docs/runs/2026-09-11-local-qualification-terminal-tombstone-interlock.md`](docs/runs/2026-09-11-local-qualification-terminal-tombstone-interlock.md)에
먼저 기록하고 commit `563c9f0`으로 source 수정 전에 고정했습니다. repository maintainer가 승인한
한 장비·한 기능·세 planned frame operation은 그대로 유효하지만, preflight는 observer stop과
qualification CLI 실행 전에 멈췄고 장비 write는 0회였습니다. 이번 작업이 닫을 질문은 **보존된
exact-restore terminal replay barrier를 삭제하거나 약화하지 않으면서, unrelated local-control
operation의 liveness 판정에서 그 barrier를 진행 중 journal로 오인하지 않을 수 있는가**입니다.

current `local_control/lease.py`는 native-linkage, verification, schedule-linkage,
temporary-schedule, exact-restore journal 다섯 경로를 모두 파일 존재만으로 non-idle로 판정하지만,
intent 세 경로는 hardened local parser로 terminal envelope을 확인해 idle로 인정합니다. exact-restore
journal에는 별도 intent가 없고 terminal `parked_unqualified` disposition이 같은 journal path의
tombstone으로 남습니다. 따라서 tombstone을 삭제하면 idle이면서 정직하게 보존하면 non-idle인 현재
판정은 방향이 반대입니다. exact-restore 자신의 disposition/archive 검증과 재실행 거부가 replay
권한을 소유하고, local-control lease의 질문은 legacy write가 현재 비행 중인지에 한정합니다.
이전 D1 계열 subsection들이 `lease.py`를 무변경 목록에 고정했으므로, 이 subsection만 그 파일을
아래 한 predicate 범위로 명시적으로 다시 엽니다. 다른 subsection의 무변경 경계를 일반 해제하지
않습니다.

변경은 `exact_restore_journal_path()` 하나를 별도 predicate로 분리해 **파일 부재 또는 exact version-2
`parked_unqualified` terminal envelope만** idle로 인정합니다. native-linkage, verification,
schedule-linkage, temporary-schedule journal 네 경로는 계속 파일 존재만으로 non-idle이고, 기존 intent
세 경로의 terminal 판정도 바꾸지 않습니다. malformed·잘린 JSON·size 초과·다른 version·phase·key
집합에 해당하는 exact-restore 파일은 모두 non-idle입니다. terminal envelope은 exact key
집합, `version=2`, `journal_format=2`, bounded operation id, exact `ERD-` disposition id와 lowercase
SHA-256 세 필드의 형식을 검사합니다. lease에서 operation·disposition·journal snapshot digest를
재유도하거나 암호학적 backing·qualification을 주장하지 않으며, 그 권위는 기존 disposition과 archive
verifier에 그대로 둡니다.

파일 검사는 기존 `_terminal_intent_or_absent()`와 같은 저수준 안전 경계를 유지합니다.
`O_NOFOLLOW`가 없으면 거부하고, open 전 `lstat`과 open 뒤 `fstat`·재조회에서 owner uid, regular file,
mode `0600`, link count 1, size 상한과 `(st_dev, st_ino)` 동일성을 확인합니다. symlink·non-regular·uid
불일치·mode 불일치·hard link·inode 교체·read 오류는 전부 non-idle입니다. 이 검사는
`exact_restore_disposition`, `exact_restore_receipts`, `exact_restore`, `protocol/*`, frozen harness나
read-only collector를 import하지 않는 stdlib-only envelope parser입니다. global exact-restore guard
lease를 획득한 상태에서 이 predicate와 이후 per-device lease를 잇는 기존 순서는 바꾸지 않습니다.

tombstone과 영구 archive는 **삭제·개명·이동·소비·재작성하지 않습니다.** exact-restore disposition의
`outcome != parked_unqualified` 재실행 거부, successor admission, archive backing과 journal store는
수정하지 않습니다. local-control producer가 lease를 우회하거나 별도 lock/journal namespace를 만들지
않고, 어떤 journal이든 단순 존재를 idle로 일반화하지 않습니다. 이 변경은 검증된 terminal liveness
오탐 하나만 제거하므로 regular write의 identity·envelope·single-write·journal·rollback gate를
완화하지 않고 §2의 과도 gate 제거 방향에 해당합니다. 현재 결함은 안전한 false-negative이고 §3
P1으로 과장하지 않습니다.

source 범위는 `src/jebao_flow/local_control/lease.py`,
`tests/unit/test_local_control_lease.py`, 가산적
`tests/unit/test_local_control_import_boundary.py`와 이 경계를 설명하는 `docs/local-control.md`로
한정합니다. `exact_restore*`, `hardware_safety.py`, qualification producer·verifier, coordinator,
device writer, journal, config·HA·MQTT·groups, frozen six와 그 직접 테스트, old daemon·old MQTT,
`custom_components/*`는 수정하지 않습니다. offline source·회귀가 landing되고 별도 적대적 검토가
끝날 때까지 observer를 정지하거나 qualification CLI와 장비 write를 실행하지 않습니다.

offline 회귀는 최소한 다음을 고정합니다. (1) exact terminal parked envelope만 idle이며 파일 부재도
기존대로 idle, (2) malformed·non-terminal·다른 phase/version·잘린 JSON·size 초과는 non-idle,
(3) symlink·non-regular·uid·mode·link-count·inode 불일치는 각각 non-idle, (4) 나머지 journal 네
경로는 존재만으로 계속 non-idle, (5) 기존 intent 세 경로 판정 불변, (6) `lease.py` import closure에
`protocol/*`·exact-restore disposition/archive·frozen module이 없음, (7) production tombstone serializer와
동일한 safe fixture는 idle이고 required terminal field 한 byte 불일치는 non-idle, (8) 기존 lease·coordinator·
writer 회귀 assert를 삭제·완화하지 않음입니다. source landing 뒤 actual private tombstone을 mutation
없이 다시 읽어 idle을 확인한 뒤에만 이미 승인된 qualification preflight를 처음부터 재개합니다.
하드웨어 `NO-GO / BLOCKING`, Q2 `UNKNOWN`, frozen native harness와 일반 daemon·old MQTT 경로 동결을
유지합니다.

### 2026-09-11 local-control actual recovery frame 검증 D1j

qualified control의 첫 MQTT write와 qualification write는 terminal journal·exact readback까지
확인했지만, 실제 `LocalControlCoordinator.recover()`가 rollback payload를 장비에 보내는 분기는 아직
실기 0회입니다. 첫 qualification 실패는 fresh state가 이미 baseline이라 recovery frame 0으로
종결됐습니다. 이번 작업이 닫을 질문은 **production coordinator·writer·journal·lease를 완화하거나
복제하지 않고, 통제된 한 번의 forward-readback fault 뒤 기존 운영용 recovery CLI가 저장된 scalar
rollback payload를 정확히 한 번 보내 baseline을 exact restore할 수 있는가**입니다.

repository maintainer이자 on-site hardware approver인 사용자는 Jebao 실기와 이미 확인된 상태의 필요한
변경을 에이전트가 별도 반복 질문 없이 진행하도록 명시 승인했고, 모든 Flow의 상한을 `50`으로
지정했습니다. 이번 operation은 그 안의 고정 `39..40`만 사용합니다. 이 승인은 HA Core 업데이트·
재부팅·HA 설정 변경을 포함하지 않고, frozen native ASYNC 하네스는 별도 해제 commit 요건을 그대로
유지합니다. 이 `50`은 현재 에이전트 operation의 승인·검토 경계이며 global runtime gate가 아닙니다.
현재 direct HA/MQTT number는 catalog native range `30..75`를 기술적으로 받을 수 있으므로 사용자가
쓰지 않는 행위적 통제를 유지하고, 후속 direct surface 범위 축소는 별도 제품 slice로 다룹니다.

drill은 qualified Local Wavemaker Pro 한 대의 `manual_flow`만 대상으로 하고 exact operation은
`40 -> 39 -> 40`입니다. 첫 `40 -> 39`는 normal `LocalControlCoordinator.execute()`와 real
`ConnectedDeviceWriter`가 수행합니다. 새 forward-only writer adapter는 coordinator의 기존
`writer_factory` seam에 instance-local wrapper 객체로만 주입하고 module-global 치환이나 import-time
patch를 하지 않으며, 모든 connect·prepare·send·readback·close를 그대로 delegate합니다.
`prepare_scalar()`가 반환한 exact `PreparedDeviceWrite`를 보관하고, real writer가 돌려준 fresh
readback에 **production `verify_prepared_readback(prepared, readback)` 자체**를 적용해 True일 때만
bytes를 바꾸거나 숨겨진 비교를 추가하지 않고 지정 fault를 정확히 한 번 raise합니다. predicate가
False이면 readback을 그대로 coordinator에 반환해 기존 `forward_readback_mismatch` 경로가 판정하며,
transport·permit·identity·qualification 오류도 지정 fault로 바꾸지 않습니다.

그 지정 fault는 coordinator의 기존 `forward_readback_unavailable` / `UNCERTAIN` /
`RECOVERY_REQUIRED` write-ahead 경로를 실제 journal에 남깁니다. forward CLI는 cause가 지정 fault이고
freshly reloaded journal이 그 exact non-terminal 상태임을 확인한 경우에만 계획된 forward phase 완료로
보고합니다. 이 시점의 장비 상태는 production predicate가 확인한 `39`이고 forward physical write는
정확히 1회입니다. 다른 오류가 발생하면 새 request로 retry하거나 다음 forward를 보내지 않고, 존재하는
unfinished journal의 기존 recovery 권한만 사용합니다.

복구는 drill module이나 adapter가 호출하지 않습니다. forward process가 완전히 종료된 뒤 **별도
프로세스의 기존 `jebao-flow-local-qualify recover --operation-id <같은 request id>
--write-enabled`**만 실행합니다. 이 recovery subcommand는 current Compose input을 검증한 뒤 config를
읽어 unwrapped production coordinator를 만들고, install manifest를 재유도하거나 교체하지 않습니다.
따라서 recovery의 resolver·lease·writer·readback에는 drill adapter가 import되거나 도달할 수 없습니다.
recovery coordinator는 qualification 없이 구성되고 `recover()`도 current qualification을 요구하지
않으므로, forward와 recovery 사이 자격 상태가 바뀌어도 baseline 복원 권한을 잃지 않습니다. existing recovery는
fresh current가 baseline이 아님을 확인하고 stored rollback payload `40`을
`ROLLBACK_ATTEMPTING` durable 저장 뒤 정확히 한 번 보내며, ACK와 production exact baseline readback이
모두 맞아야 action·transaction을 `ROLLED_BACK`으로 terminalize합니다. actual recovery
`physical_write_count`는 정확히 1이어야 하고, 0이면 baseline-already-present 분기일 뿐 이번 질문의
PASS가 아닙니다. 복구 실패는 `ROLLBACK_UNCERTAIN` 권한을 보존하고 추가 write나 자동 retry를 하지
않습니다.

새 fault artifact 종류는 만들지 않습니다. journal의 `forward_readback_unavailable` diagnostic은
production 문자열이며 synthetic provenance 자체를 증명하지 않습니다. append-only run record가 opaque
request id, forward CLI의 지정-fault 확인, terminal journal digest·revision·referenced blob, recovery
`physical_write_count=1`, independent pre/post fresh readback과 UTC span을 함께 결속합니다. raw frame을
보존하지 않으면 structured journal 주장은 (b), operator fresh observation은 (c)로 구분하고 (a)를
주장하지 않습니다. receipt·qualification binding과 command surface는 추가하지 않습니다.

live 순서는 persistent control service를 먼저 정지해 device authority를 하나로 만들고, D1e current
precondition 네 개와 reviewed source/image, unfinished local journal 0, legacy idle, 6/6 fresh, target의
power ON·TimerOFF·constant·independent·Flow 40, current template·role·qualification binding, 동일
hardware-safety volume을 확인합니다. forward CLI와 recovery CLI는 겹치지 않고 차례로 한 프로세스씩
실행합니다. 성공 조건은 physical write 총 2회(`39` forward 1 + `40` recovery 1), receipt 증가 0,
terminal `ROLLED_BACK`, unfinished 0, independent fresh Flow 40입니다. 그 뒤 reviewed persistent control
service 1개를 재기동해 6/6 fresh·command surface 1·retained command 0·old daemon 0·role-binding 불변을
확인합니다. HA Core 업데이트·재부팅·설정 변경은 하지 않습니다.

source 범위는 새 `src/jebao_flow/local_control/recovery_drill.py`, forward-only
`src/jebao_flow/local_control_recovery_drill_cli.py`, 두 전용 unit test, 가산적
`tests/unit/test_local_control_import_boundary.py`, `docs/local-control.md`, console entry를 위한
`pyproject.toml`로 한정합니다. `local_control_cli.py`와
`local_control_qualification_cli.py`의 기존 recovery 경로, coordinator, device writer, journal, lease,
qualification producer·verifier, catalog, config·HA·MQTT·groups, frozen six와 그 직접 테스트, old
daemon·old MQTT, `custom_components/*`는 수정하지 않습니다. CLI는 explicit
`--write-enabled`, opaque request id와 private logical target을 요구하고 public 출력에는 address·MAC·
vendor id·payload·ACK bytes·credential·private path를 내보내지 않습니다.

offline 회귀는 최소한 다음을 고정합니다. (1) adapter는 production
`verify_prepared_readback()`가 True일 때만 정확히 한 번 지정 fault를 raise, (2) False이면 bytes를
그대로 반환해 coordinator의 `forward_readback_mismatch`와 recovery-required가 유지, (3) transport·
permit 오류는 지정 fault로 오분류하지 않고 기존 fail-closed 경로 유지, (4) 지정 fault 뒤 journal은
`UNCERTAIN` / `forward_readback_unavailable` / `RECOVERY_REQUIRED`이고 다섯 blob과 rollback payload는
coordinator가 만든 exact prepared 값, (5) 그 record를 unwrapped production coordinator가 복구하면
rollback frame 1회·`ROLLED_BACK`·`physical_write_count=1`, (6) drill source에는 `recover()` 호출 경로가
없고 recovery process import closure에도 drill adapter가 없음, (7) baseline-already-present recovery
write 0은 drill PASS가 아님, (8) wrong target·unqualified function·Flow 40 이외 baseline·39 이외
destination·unfinished journal은 first send 0, (9) forward와 recovery의 total send count가 2를 넘지
않고 receipt artifact 0, (10) frozen harness·`devices/*` import가 없습니다. 기존 coordinator·writer·
journal·lease assert는 삭제·완화하지 않습니다. (11) forward CLI는 cause가 지정 fault이고 freshly
reloaded journal이 `UNCERTAIN` / `forward_readback_unavailable` / `RECOVERY_REQUIRED`인 경우에만
planned-forward-complete를 보고하며, 다른 cause나 journal 상태는 non-success exit이고 계획 완료를
주장하지 않습니다. (12) adapter `close()`는 delegate close를 정확히 한 번 호출하고 그 예외를 삼키지
않습니다.

이 slice는 정상 persistent write 경로에 새 gate를 추가하지 않고, test-only fake가 아닌 real device
state에서 기존 recovery의 미실행 분기 하나를 검증합니다. wrong-device는 current strict resolution과
qualification, out-of-envelope은 고정 `39..40`, duplicate write는 서로 다른 process와 기존
single-send, rollback-journal loss는 기존 write-ahead store, real FAIL의 PASS 오판은 production exact
readback predicate 재사용과 terminal recovery readback으로 막습니다. 물리 위험 또는 §3 P1이 아니면
비차단 진단을 새 gate로 키우지 않고 run record에 남깁니다. Q1·Q2와 frozen native ASYNC 하네스는 이
slice에서 계속 `UNKNOWN`·동결 상태입니다.

### 2026-09-11 immutable local-control qualification-set 병합 D1k

M2의 actual recovery·supervised restart·bounded soak·external app drift는 각각 append-only 실기
기록과 독립 검토로 닫혔습니다. M3에서 다음 장비·기능의 receipt를 더 만들기 전에 닫을 질문은
**현재 persistent control의 자격 입력을 덮어쓰거나 약화하지 않고, 기존과 새 receipt를 정확히
검증한 새 immutable qualification set으로 합쳐 의도한 `(product, function, physical identity)`
surface만 다음 deployment generation에 전달할 수 있는가**입니다. 이 slice는 offline artifact
composition만 수행하고 app·broker·device·network에 접촉하거나 장비 write를 하지 않습니다.

qualification receipt는 fixed owner-only hardware-safety receipt root의 content-addressed 파일이고,
`QualificationBinding`은 `(product_key, function)`마다 하나입니다. 같은 key의 장비가 여럿이면 한
binding의 receipt tuple에 함께 들어가며, 각 physical identity가 서로 다른 verified request 두 개
이상을 가져야만 `qualified_physical_identity_keys()`가 그 identity를 반환합니다. 같은 binding을
공유하는 receipt의 app/catalog/template/schema/firmware 필드는 전부 같아야 합니다. 두 Pro의 firmware
일치는 현재 시점 관측을 merger 전제로 복사하지 않고, **병합 결과를 existing verifier로 다시
검증하는 시점**에 재판정합니다. Bar처럼 product key가 다른 binding에는 Pro firmware 일치를 요구하지
않습니다.

merger library는 optional current set, 하나의 candidate set, 새 output path와 운영자 의도에서 유도한
하나 이상의 expected-add triple `(product_key, function, physical_identity_key)`을 입력으로 받습니다.
CLI는 raw physical identity key를 argv로 받지 않고 private logical member와 function만 받은 뒤 read-only
current manifest의 exact 한 device binding에서 product와 physical key를 유도합니다. manifest를
재유도하거나 쓰지 않으며, unknown·duplicate logical member는 output 전에 거부합니다. current와
candidate의 모든 binding을 먼저 `verify_qualification_receipts()`로 검증하고, 각 binding 안의 기존
distinct request 계약을 유지하며 전체 verified result에서도 request id 중복을 허용하지 않습니다.
같은 `(product_key, function)`이 양쪽에 있으면 receipt digest의 집합 합집합만 만들되 나머지 다섯
version/content 필드가 byte 동일해야 합니다. 다른 key는 가산적으로 추가합니다. current-only의
영향받지 않은 binding은 seven evidence field와 receipt digest 집합이 exact 동일해야 하고, 허용되는
변화는 아래 canonical 순서뿐입니다. 첫 merge에서 기존 producer 순서가 lexicographic이 아니면 이
순서만 정당하게 바뀔 수 있습니다. `min_length=2`, key 유일성, seven-field receipt 교차검사,
canonical receipt bytes와 digest filename 검증은 삭제·완화하지 않습니다. merger는 receipt를 새로
설치·이동·복사하지 않고, producer가 fixed content-addressed root에 설치한 artifact를 digest로 참조해
existing verifier로 읽기만 합니다.

candidate의 binding 수나 receipt 수를 `1`·`2`로 하드코딩하지 않습니다. 대신 병합 결과의 binding
key 집합은 `current keys ∪ expected-add의 (product,function) keys`와 정확히 같고, 각 binding을 existing
verifier로 다시 읽어 도출한 qualified surface triple 집합은
`current qualified surface ∪ expected-add triples`와 정확히 같아야 합니다. expected-add는 current
surface와 겹치지 않는 명시적 추가분이어야 하며, 결과의 초과와 부족을 모두 거부합니다. 이 검사는
후보가 의도 밖 장비를 함께 자격화하는 과다 노출과 기존 자격이 조용히 사라지는 과소 노출을 동시에
막습니다. caller가 candidate 자체를 의도라고 주장한 값을 다시 믿지 않고 verified result에서
재유도합니다.

결정론적 output은 binding을 `(product_key, function)`으로 정렬하고, 각 binding 내부
`receipt_sha256`를 lexicographic 정렬한 뒤 compact ASCII JSON과 trailing newline으로 직렬화합니다.
이 정렬은 authorization 의미를 추가하는 gate가 아니라 merger 재실행과 후속 Compose input의
byte-identical 결정성을 위한 것입니다. output은 owner-only `0700` parent 아래 새 `0600` regular
file로만 설치하고 symlink·hard link·unsafe uid/mode와 size 초과를 거부합니다. random temporary file을
write·fsync한 뒤 no-clobber link로 publish하고 parent를 fsync하며 exact reload와 전체 verifier·surface
검사를 다시 통과해야 성공입니다. 같은 output path에 expected bytes가 이미 있으면 verify-and-accept,
다른 bytes면 기존 파일을 바꾸거나 지우지 않고 거부합니다. current·candidate 파일과 running Compose
generation은 성공·실패 모두 mutation하지 않습니다.

이 output은 실행 중 service가 직접 읽는 파일이 아니라 후속 `prepare-compose --qualifications`가 새
private generation 안의 `qualifications.json`으로 재정규화할 source입니다. 따라서 이 slice에서는
manifest·role binding·Compose generation을 재유도하거나 service를 재기동하지 않습니다. 후속 배포
결속은 bundle descriptor가 기록한 actual `qualifications.json` SHA-256을 근거로 별도 확인하며, 실패
시 rollback은 현재 generation을 그대로 유지하는 것입니다.

source 범위는 새 `src/jebao_flow/local_control/qualification_set.py`, 별도
`src/jebao_flow/local_control_qualification_set_cli.py`, 두 전용 unit test, 가산적
`tests/unit/test_local_control_import_boundary.py`, `docs/local-control.md`, console entry를 위한
`pyproject.toml`로 한정합니다. `qualification.py` verifier·models, qualification producer와
`local_control_qualification_cli.py` recovery route, catalog·compose·install·config, coordinator·writer·
journal·lease, HA·MQTT·groups, frozen six와 직접 테스트, old daemon·old MQTT, `custom_components/*`는
수정하지 않습니다. CLI의 private logical member와 input/output path는 help와 public JSON에 노출하지
않고, raw physical identity key도 argv·stdout·stderr에 싣지 않습니다. 결과는
binding/receipt/qualified-pair/addition count와 output SHA-256만 보고합니다.

offline 회귀는 최소한 다음을 고정합니다. (1) current 부재 시 verified candidate로 새 set 생성,
(2) 같은 key·같은 seven fields는 receipt union, (3) firmware를 포함한 어느 field든 다르면 output 0,
(4) 결과 binding key와 qualified surface가 선언 union과 정확히 일치하며 초과·부족 각각 거부,
(5) 영향받지 않은 current binding의 evidence field·receipt set 동일과 canonical order 외 변화 0,
(6) missing·digest mismatch·noncanonical·duplicate request
receipt 거부, (7) input 순서와 receipt 순서를 섞어도 output bytes 동일, (8) 동일 output 재실행은
verify-and-accept하고 다른 existing bytes는 mutation 없이 거부, (9) unsafe parent/file·symlink·hard
link·fsync/reload fault는 fail-closed, (10) current·candidate·running generation mutation 0,
(11) CLI의 logical member가 current manifest의 exact 한 binding으로 expected triple을 유도하며 raw
physical key를 argv에 요구하지 않음, (12) import closure에 device/network/write/frozen module이 없고
기존 verifier·producer·catalog 회귀 assert를 삭제·완화하지 않습니다.

이 작업은 device write operation이 아니므로 §3의 직접 physical-write 범주는 열리지 않습니다.
간접 위험인 command-surface 과다·과소는 verified exact result union과 immutable no-clobber generation으로
막습니다. 정책·source가 각각 별도 Claude 검토와 commit을 마친 뒤에만 두 번째 Pro
`manual_flow` qualification으로 이동합니다. Flow `<=50` 승인 경계, persistent 단일 authority,
Q1·Q2의 M6 필수·현재 `UNKNOWN`, frozen native 하네스 동결은 그대로 유지합니다.

### 2026-09-11 Flow 범위 `30..100` 정정 D1l

repository maintainer이자 on-site hardware approver인 사용자는 local-control에 들어간 Flow 상한
`75`를 자신이 요청하거나 승인한 값이 아니라고 명시적으로 정정하고, Jebao app과 일치하는
`30..100`으로 수정하도록 지시했습니다. 기존 app catalog도 Local Wavemaker Pro를 포함한 해당
manual Flow를 `30..100`으로 선언합니다. 따라서 2026-09-08 local-first 부분 해제의 `30..75`
문구와 그 값을 복제한 후속 policy는 이 절로 supersede합니다. `75`를 observed manual register나
example config에서 가져와 제품 최대·encoder 최대·안전 최대라고 다시 해석하지 않습니다.

정정 범위는 local-control catalog·HA/MQTT number validation, scalar writer, software-independent
group envelope, local schedule composition, qualification producer의 encoder 범위, write-free schedule
model과 그 직접 tests/docs입니다. 하한 `30`과 상한 `100`은 각 app function의 declared range에서
유도하고, consumer마다 hard-coded `75`나 `100`을 복제하지 않습니다. 자체 integer range가 없는
`slave_per_slot_flow_extension`은 같은 Pro의 app-declared `manual_flow` range에 결속합니다. current
private config처럼 capability보다 좁거나 같은 per-device limit는 group envelope과 qualification
producer에서 operator 설정으로서 계속 교집합을 이룹니다. direct HA/MQTT number는 config limit와
교집합을 만들지 않고 catalog descriptor의 `30..100` 전체를 그대로 공개·수용합니다. 기존 frozen
native ASYNC write 하네스 여섯 파일과 그 직접 테스트는 수정하지 않습니다.
과거 `docs/runs/`의 `30..75`는 당시 배포가 실제로 광고한 범위에 대한 append-only 사실이므로
고치지 않고, 정정 배포를 새 run record로 남깁니다.

`local_control/schedule.py`의 동작은 `operator_capture/schedule_model.py`의 Flow range 상수에
의존하지만 현재 `current_protocol_schema_sha256()`는 전자만 hash하고 후자를 빠뜨렸습니다. 이 상태는
hash 밖 dependency만 바꿔도 기존 receipt가 current로 남는 static-trace FAIL-to-PASS 경로이므로,
이번에 `schedule_model.py`도 protocol-schema digest 입력에 넣고 그 closure를 회귀로 고정합니다.
이는 frozen native ASYNC 하네스의 schema를 바꾸거나 해제하는 작업이 아닙니다.

이 제품 범위 정정은 현재 live 실기의 유량 충격 방지 가이드와 별개입니다. 사용자가 지정한 agent
실기 조작값 `<=50`은 돌아가는 수조에 갑작스러운 유량 변화를 주지 않기 위한 이번 작업의 attended
operation bound일 뿐, 장비 capability·영구 설정 상한·입력 validation gate가 아닙니다.
qualification producer의 `45`도 first-write 실기 전용 baseline 제한이며 제품·앱·HA 범위가 아니므로
이번 정정에서 `100`으로 승격하지 않습니다. 향후 그 전용 제한을 바꾸려면 별도 operation 근거와
사용자 지시를 요구합니다. 정정된 HA/MQTT number는 사람에게 app과 같은 `30..100`을 공개하고 그
범위를 수용하며, direct HA 사용을 `50` 이하로 기술적으로 제한하지 않는다는 결과를 사용자에게
공개하고 run record에 남깁니다. `hardware_test.py`의 attended maximum `45`와 frozen
`schedule_flow_experiment_cli.py`의 `45`도 제품 capability가 아닌 별도 실기 한계이므로 이번
정정 범위 밖에 두고 완화하지 않습니다.

`device_writer.py`와 `local_control/schedule.py`는 protocol-schema digest의 입력이므로 이번 정정은
기존 두 Pro의 receipt를 의도적으로 stale하게 만듭니다. 이 content binding을 완화·제외하거나 기존
receipt를 변환하지 않습니다. source 정정과 offline 회귀 자체는 device write `0`이고, 정정 source를
배포하기 전 두 Pro 각각을 새 schema에서 기존 fixed `40 -> 39 -> 40` qualification operation으로
재자격합니다. 각 operation의 최대 agent Flow는 `40`, physical write는 `3`회이고, 이미 승인된
`<=50` Jebao 실기 범위 안에서 별도 append-only run record를 먼저 landing합니다. 앞서 만든 old-schema
merged set은 변경·삭제하지 않고 미배포 obsolete artifact로 남깁니다. 두 새 candidate를 fresh set으로
결속한 뒤에만 새 generation을 한 번 배포하며, 그 전에는 현재 old generation의 surface를 유지합니다.
group capability는 `30..100`으로 정직하게 고치지만 아직 qualified write surface는 `0`입니다. M4에서
처음 arm하기 전에는 그 attended 실기의 configured·calculated output을 유량 충격 방지 가이드 `<=50`에
맞춰 검산하며, capability 최대 `100`을 그 실기의 계획값으로 자동 재사용하지 않습니다.

offline 회귀는 최소한 다음을 고정합니다. (1) 각 integer Flow descriptor range가 해당 app function의
declared range와 같음, (2) `slave_per_slot_flow_extension` range가 같은 Pro의 `manual_flow` range와
같고 `None`이 아님, (3) scalar writer의 허용 경계와 descriptor range가 다시 갈라지지 않음,
(4) `schedule_model.py`가 protocol-schema digest 입력에 포함됨, (5) 그 파일의 range source bytes만
바뀌어도 digest가 바뀜, (6) 더 좁은 config limit와의 교집합은 group과 qualification producer에서
계속 유지됨, (7) `_SAFE_FIRST_WRITE_MAXIMUM = 45` 불변, (8) frozen six와 직접 tests 무수정 및
import closure 불변, (9) 기존 `30..75` 결과 단정을 app-declared 관계로 교체하고 `100`은 허용·`101`은
거부함입니다. 기존 assert를 삭제하거나 입력 `100`을 거부하는 새 fake로 관계 검사를 대신하지 않습니다.

### 2026-09-12 Local Wavemaker Bar active-template positive binding D1m

repository maintainer의 “내가 직접 조작해야 하는 것 외에는 모두 진행” 지시는 현재 설치된 앱의
Bar active template을 재결속하고, 장비 write 없이 current manifest와 새 Compose generation에
추가해 배포하는 operation을 승인합니다. 이 범위는 Bar 앱 화면 조작이나 `manual_flow`·`power`
장비 write를 포함하지 않습니다. 이번 작업이 닫을 질문은 **현재 앱 build와 current manifest의
정확히 한 Bar product binding에 대해, 두 cache 후보 중 loader가 실제 선택하는 template bytes를
추측 없이 다시 특정하고, 기존 두 Pro qualification·control surface와 장비 상태를 바꾸지 않은 채
그 content binding을 배포할 수 있는가**입니다.

read-only static trace는 installed Jebao Aqua app `3.4.18` / build `341800000`, 16,665,122-byte
Hermes bundle SHA-256
`97b8eb17a915b8071dcef16c177f1e5bc3ab31e154510c69e6d3f9af2a533c71`에 결속합니다. current
manifest에는 Bar product binding이 정확히 하나 있고, 그 product key의 원문 대신 SHA-256
`197ae39afb4414b1c55c1c5873ece68813b7a7ac6b2e336ea8ba2011faa32b38`만 기록합니다. compiled
product config에서 이 key와 일치하는 device config object의 template download URL도 하나이며,
원문 대신 SHA-256
`078b2e1bccdac4c0189c1fa40c19c6a191743ef7281ca7ae1045dfa226f51880`로 결속합니다. loader는 이
URL의 MD5를 cache locator로 사용하며 newest-directory·mtime·파일 크기로 후보를 고르지 않습니다.
그 exact locator 아래에는 `index.js` 하나, `index.html` 0개가 있고, 선택되는 `index.js`는 162,553
bytes, SHA-256
`e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`입니다. 같은 component의
다른 110,295-byte candidate SHA-256
`d6b35da90a9ee5db09f91a7819bfb5cf89c439d061e789a69af2b40706ebb6ab`는 현재 URL locator에서
도달되지 않으므로 제외합니다. raw product key·URL·cache locator와 private 경로는 출력·문서·커밋에
싣지 않습니다.

write-free 조사라도 positive evidence를 특정한 즉시 app/build·product·URL 결속, 선택한 content의
digest와 byte size, 배제 후보의 도달 불가 사유를 append-only `docs/runs/`에 남깁니다. mutable todo의
체크박스만으로 완료 처리하지 않습니다. 이 규칙은 조사 때 device write가 없다는 사실과 조사 결과를
내구성 있게 보존해야 한다는 의무를 분리합니다. 임시 decompile·raw app/cache 산출물은 저장소에
넣지 않고, 보존되지 않은 static trace는 증거 등급 (c)를 넘지 않습니다.

선택한 bytes는 current Pro template과 다른 새 durable private template root에 `index.js` 하나로만
설치합니다. file SHA-256·byte size를 다시 검증하고 file mode `0444`, directory mode `0755`, link
count `1`, file·directory·private parent fsync를 완료한 뒤에만 install input으로 사용합니다. 기존
Pro root나 running generation은 수정하지 않습니다. fresh manifest의 `template_sha256`는 기존 Pro
digest와 위 Bar digest의 정확한 두 원소여야 하고, six `physical_identity_key`와 stable role binding은
byte 동일해야 합니다. template membership 판정이므로 기존 Pro receipt 네 장은 계속 current여야
하며, offline projection은 qualified `manual_flow` surface 정확히 `2`, ignored qualification `0`,
Bar command surface `0`, group command surface `0`이어야 합니다.

배포는 새 immutable source와 Compose generation으로만 하고 current file을 in-place 수정하지 않습니다.
Docker가 실제로 읽을 host bind source가 stop-first 전에 존재하는지 확인하고, rollback은 old build
context를 재빌드하지 않고 보존한 exact prior image와 prior bundle을 사용합니다. 기존 persistent
service를 완전히 정지한 뒤 새 instance 하나만 시작합니다. journal·receipt tree와 unfinished `0`은
전후 byte 동일하고, startup device write `0`, retained command `0`, fresh authenticated state `6/6`,
qualified direct surface `2`, Bar diagnostic-only, group `0`, discovery expected/present/exact가 모두
일치해야 PASS입니다. local-control frame의 write-0 추론은 모든 그 경로의 frame보다 journal-first
저장이 선행한다는 현재 coordinator 계약에만 한정합니다.

이 slice는 source·frozen six·qualification verifier·receipt를 수정하지 않고 장비 command도 보내지
않습니다. Bar active template을 결속했다고 firmware write acceptance, Bar `manual_flow` qualification,
다른 app build의 active bytes나 remote URL bytes의 영속성을 주장하지 않습니다. app build, current
product binding, compiled URL 또는 cache bytes가 달라지면 같은 positive binding을 다시 수행합니다.
배포 run record가 commit되기 전에는 Bar family source를 수정하지 않습니다.

### 2026-09-13 Local Wavemaker Bar manual-flow family profile D1n

D1m의 Bar template deployment는
[`docs/runs/2026-09-12-local-bar-active-template-deployment.md`](docs/runs/2026-09-12-local-bar-active-template-deployment.md)에
기록하고 commit `df6b055`로 source 변경 전에 고정했습니다. repository maintainer의 “내가 직접
조작해야 하는 것 외에는 모두 진행” 지시는 Bar `manual_flow` producer의 family source·offline 회귀·
image build와 첫 물리 write 직전 preflight까지를 승인합니다. 앱 조작과 장비 write는 포함하지 않으며,
그 경계에서는 현장 감시와 전원 차단 수단을 다시 확인합니다.

이번 작업이 닫을 질문은 **기존 Local Wavemaker Pro의 세-frame qualification 동작과 안전 계약을
바꾸지 않고, current app catalog·protocol schema·writer가 공통으로 선언한 Local Wavemaker Bar의
scalar fields로 같은 `manual_flow` operation을 구성할 수 있는가**입니다. 이는 Bar firmware가 frame을
수용한다는 결론을 fake로 만드는 작업이 아닙니다. source slice의 PASS는 offline 구성 가능성만 닫고,
firmware acceptance와 exact restore는 이후 한 번의 attended live run에서만 판정합니다.

family profile은 product 이름이나 README의 pairing 설명으로 추측하지 않고 current product-scoped
catalog descriptor와 기존 `ConnectedDeviceWriter.prepare_scalar()` mapping에 결속합니다. Pro profile은
기존 probe 순서와 값인 `schedule_enable=false`에서 `SwitchON`·`TimerON`,
`linkage_role=independent`, `manual_mode=constant`, `manual_flow=<declared minimum>`을 그대로 유지합니다.
Bar의 `schedule_enable`은 app 상태에 따라 command shape가 달라 deterministic encoder가 의도적으로
없으므로 이를 새로 열거나 `prepare_scalar()`로 가장하지 않습니다. Bar는 production product schema의
strict status decoder로 `TimerON`을 read-only 추출하고, `power=true` probe의 `SwitchON`,
`linkage_role=independent`, `manual_mode=constant`, `manual_flow=<declared minimum>` 네 scalar mapping을
기존 writer로 검증합니다. Bar에도 `Linkage` register가 있고 current fresh read가 `independent`를
돌려줬으므로 이를 생략하지 않습니다. Pro linkage choices
`independent/master/sync_slave/async_slave`와 Bar choices `independent/master/slave`는 서로 복사하지
않고 product descriptor의 실제 어휘를 유지합니다.

여기서 Pro의 네 probe와 Bar의 네 scalar probe·한 decoded field read는 current status field를
유도하는 send `0` 검사입니다. physical frame은 두 family 모두 same-value·하향·planned restore의
정확히 3회뿐이며, status inspection 개수를 frame 승인으로 해석하지 않습니다.

두 family 모두 live admission은 power ON, TimerOFF, `Mode=constant`, `Linkage=independent`, current
Flow와 하향 착지값이 config·app declared range 안, current Flow `<=45`, downward step `1..5`라는 기존
계약을 그대로 사용합니다. `45`는 제품 capability나 영구 입력 상한이 아니라 attended first-write
상한이고, 일반 direct HA/MQTT의 `30..100` capability와 섞지 않습니다. 2026-09-12 fresh read의 Bar는
power ON·TimerOFF·`Mode=classic`·`Linkage=independent`·Flow `91`이므로 현재 상태로는 반드시 send와
journal 생성 전에 거부되어야 합니다. `91`에서 실기 baseline으로의 큰 하향 변경은 agent가 임의로
수행하지 않고, source landing 뒤 원래 `classic/91`과 복원 결정을 공개한 상태에서 사용자가 앱으로
직접 수행합니다.

source 범위는 기존 `qualification_producer.py`의 family profile 추가, 그 전용 unit test,
`local_control_qualification_cli.py`의 사실 문구와 `docs/local-control.md`의 supported-family 설명으로
한정합니다. journal·lease·coordinator·device writer·catalog·qualification verifier·receipt model,
normal persistent control path, group, frozen six와 그 직접 테스트는 수정하지 않습니다. 이 module은
protocol-schema digest 입력이 아니므로 기존 두 Pro receipt와 running generation을 무효화하거나
재배포하지 않습니다. source·test·build 중 device·broker·HA·running service 접촉과 durable hardware
artifact mutation은 0입니다.

offline 회귀는 최소한 다음을 고정합니다. (1) 기존 Pro success가 같은 4 probe와
`40 -> 39 -> 40`, writer/session 3개, journal action 3개, receipt 2장을 유지, (2) Bar success가 별도
power probe와 strict schema decoder의 `TimerON` read를 사용하고 같은 exact 3-write·2-receipt 계약을 사용,
(3) Bar `classic`·Flow `91`·TimerON·power OFF·non-independent 각각 send 0, journal 0, receipt 0,
(4) 지원하지 않는 family나 manual-flow encoder 부재는 writer·network 전에 거부, (5) Bar의 app-declared
mode/linkage 어휘, schema `TimerON`, writer scalar field가 바뀌면 기존 product-scoped 구성 검증이 fail-closed,
(6) strict resolution·epoch·single-write·journal-first·ACK/readback·recovery·artifact no-clobber 관련 기존
assert를 삭제하거나 완화하지 않음입니다. 테스트용 Bar status는 production `LOCAL_WAVEMAKER` schema
bytes를 사용하며 firmware 동작을 새 fake knob로 만들지 않습니다.

§3의 wrong-device·duplicate·journal·FAIL→PASS 범주는 기존 exact identity, lease, durable record와
receipt verifier를 그대로 사용합니다. out-of-envelope는 family 추가 뒤에도 같은 first-write admission을
적용해 닫습니다. source landing 뒤에도 Bar command surface는 `0`이고 running two-Pro surface는 `2`로
유지합니다. 다음 live 계획은 사용자가 앱에서 승인한 저출력 constant baseline을 만든 뒤 Bar 한 대만
same-value → 1%p 하향 → exact planned restore로 실행하며, 그 전까지 장비 write는 시작하지 않습니다.

### 2026-09-13 local-control power qualification producer D1o

D1n의 Bar family source는 commit `d687739`로 landing했고 기존 Pro 동작을 포함한 전체 offline suite가
통과했습니다. repository maintainer의 “직접 조작해야 하는 것 외에는 모두 진행” 지시는 세 수류모터의
`power` qualification 정책·source·offline 회귀·image build와 첫 live write 직전 preflight까지를
승인합니다. 앱 조작과 장비 write는 포함하지 않으며, 실제 operation마다 on-site 감시와 즉시 전원 차단
수단을 다시 확인합니다.

이번 작업이 닫을 질문은 **기존 exact identity·global/device lease·journal-first·single-send·ACK와 fresh
readback 계약을 완화하지 않고, ON인 한 수류모터를 same-value ON → OFF → planned ON으로 검증하면서
어느 중간 실패에서도 agent recovery가 무인 ON을 보내지 않는 receipt pair를 만들 수 있는가**입니다.
이는 firmware 결과를 fake로 정하는 작업이 아니라 existing coordinator recovery의 static trace와 durable
fault injection으로 write·복구 순서의 코드 내부 계약을 닫는 작업입니다.

단순히 기존 writer가 만든 same-value ON action을 첫 action으로 두면 안 됩니다. 그 action의 일반
rollback과 baseline readback은 ON이므로, 뒤의 OFF가 `safe_state_retained`로 끝나도 coordinator가 reverse
순회의 마지막 same-value action에서 ON frame을 다시 보낼 수 있습니다. 이는 실패 시 safer-OFF를 유지한다는
power recovery 계약을 깨는 실제 §3 물리 위험입니다. power producer는 첫 send 전에 actual
`ConnectedDeviceWriter.prepare_scalar(power=False)`가 만든 **실제 OFF payload**를 얻고, 합성 bytes를
만들지 않은 채 다음 세 action을 하나의 operation record에 모두 durably 설치합니다.

1. `same_on`: forward·expected readback은 ON, rollback payload·baseline readback은 OFF,
   `restore_on_recovery=true`
2. `power_off`: writer가 만든 원형을 그대로 보존해 forward·expected readback은 OFF, rollback payload·
   baseline readback은 실제 시작 상태 ON, `restore_on_recovery=false`
3. `planned_on`: forward·expected readback은 ON, rollback payload·baseline readback은 OFF,
   `restore_on_recovery=true`

따라서 `same_on.forward == power_off.rollback == planned_on.forward`이고
`same_on.rollback == power_off.forward == planned_on.rollback`이어야 합니다. OFF action의
`baseline_readback=ON`은 OFF frame이 아직 적용되지 않아 장비가 ON인 경우를
`baseline_already_present`로 무전송 분류하는 데 필요하므로 OFF로 바꾸지 않습니다. 세 action의
`baseline_status`는 operation 시작 때 실제로 읽은 동일한 full ON status blob을 보존합니다. 반면
`same_on`·`planned_on`의 `baseline_readback=OFF`는 시작 상태 주장이 아니라 **보수적 recovery 수렴
목표**입니다. 두 의미를 run record에서도 구분합니다.

기존 manual-flow producer의 `_create_record()`는 모든 action의 `restore_on_recovery`를 `true`로
하드코딩하므로 power producer가 그 builder를 상속해 사용하지 않습니다. power 전용 builder가 위
세 `PreparedDeviceWrite` 각각의 recovery policy를 그대로 journal에 저장하고, 첫 send 전에 store에서
record를 다시 읽어 `true/false/true`와 각 rollback payload digest가 위 mapping과 exact 일치함을
검증합니다. 메모리 객체만 검사해 durable journal의 policy 손실을 놓치지 않습니다.

`same_on`은 fresh baseline과 같은 값이라 writer의 `no_change=true`가 정상입니다. 그러나 이 phase는
transport-reachability evidence를 위한 planned physical frame이므로 coordinator의
`_execute_prepared()`를 거치지 않습니다. 그 경로는 `no_change` action을 `NO_CHANGE`로 저장하고 send를
생략하기 때문입니다. 새 producer는 기존 producer의 journal-first `_execute_action()`을 직접 사용해
same ON도 정확히 한 번 보내며, terminal journal에는 `NO_CHANGE` action이 0개여야 합니다. 준비 때
same ON의 `no_change=true`와 OFF의 `no_change=false`를 확인하는 것은 기존 baseline 일관성 검사의
power 대응이고 새 firmware gate가 아닙니다.

recovery는 세 action을 역순으로 처리하되 사용자 앱 조작 없이 TimerOFF·independent 상태가 유지되는
동안 이미 OFF면 frame 0, ON이면 actual prepared OFF frame 최대 1회로 수렴하고 ON frame은 보내지
않습니다. planned ON의 send와 readback이 성공한 뒤 transaction terminal save 전에 실패한 경우도
final state를 OFF로 되돌립니다. 이는 보수적 선택의 의도된 결과이며,
“복원 성공을 관측했으니 ON을 다시 유지한다”로 승격하지 않습니다. production power readback은
`SwitchON` boolean 하나이므로 유효한 상태에 제3의 값을 만들지 않습니다. `same_on`·`planned_on`
recovery는 exact OFF면 무전송 terminal, exact ON이면 prepared OFF를 한 번 시도합니다. 이 두 action의
recovery connect/read 자체가 실패하면 coordinator가 action을 새로 저장하기 전 예외가 나므로 직전
action phase는 그대로이고, forward failure 직후 producer가 이미 transaction을
`RECOVERY_REQUIRED`로 저장한 상태를 유지합니다. `power_off` recovery는 exact OFF를
`safe_state_retained`, exact ON을 `baseline_already_present`로 각각 무전송 terminalize하며, read/decode
실패는 기존 safe-state 분기가 action `UNCERTAIN`·transaction `RECOVERY_REQUIRED`를 저장하고 frame을
보내지 않습니다. production schema로 도달 불가능한 제3의 power 값을 fake로 만들어 no-send 분기를
증명하지 않습니다.
어느 실패든 자동 재기동하지 않으므로 on-site approver가 앱 또는 물리 수단으로 ON을 복구해야 합니다.
정상 실행만 세 exact write 뒤 journal을 `VERIFIED`로 terminalize하고 OFF와 planned ON action의 receipt
두 장을 발급하며 최종 power ON을 요구합니다.

live admission은 D1n에서 검증한 product-scoped current state를 재사용해 power ON·TimerOFF·
`Mode=constant`·`Linkage=independent`, current Flow `<=45`, config와 app-declared Flow range 안을 모두
요구합니다. 이는 제품 capability를 `45`로 줄이는 것이 아니라 OFF 뒤 ON 복원이 만드는 순간 유량 step과
linkage 전파를 한 장비·저출력 attended operation으로 묶는 안전 조건입니다. 각 phase의 fresh state는
power만 계획대로 ON/OFF/ON이고 Timer·mode·linkage·Flow는 시작값과 같아야 합니다. drift는 forward를
이어가지 않고 recovery-required로 끝냅니다. OFF 체류 시간을 추측한 코드 타이머나 새 retry gate는
추가하지 않습니다.

source 범위는 새 `src/jebao_flow/local_control/power_qualification_producer.py`, 별도 explicit
`src/jebao_flow/local_control_power_qualification_cli.py`, 두 전용 unit test, 가산적 import-boundary test,
`docs/local-control.md`, console entry를 위한 `pyproject.toml`로 한정합니다. 기존 검증된
`qualification_producer.py`와 `local_control_qualification_cli.py`의 manual-flow·recover 동작과 bytes는
수정하지 않습니다. 새 producer는 기존 producer의 function-independent lease·resolution·journal transition
machinery만 재사용하고 manual-flow execute·intent·finalization을 호출하지 않습니다. recovery는 새 경로를
만들지 않고 기존 `jebao-flow-local-qualify recover`가 같은 generic journal record를 처리합니다. journal,
lease, coordinator, device writer, catalog, receipt verifier/model, persistent path, group, frozen six와 직접
테스트는 수정하지 않습니다. 새 module은 protocol-schema digest 입력이 아니므로 current receipt와 running
generation을 무효화하지 않습니다. 특히 `device_writer.py`는 순수 helper 추출이나 refactor 명목으로도
수정하지 않습니다. 그 파일은 protocol-schema digest 입력이라 한 byte 변경도 현재 세 장비의 기존
qualification을 stale하게 만들기 때문입니다.

offline 회귀는 최소한 다음을 고정합니다. (1) 성공은 하나의 preinstalled 3-action journal, writer/session
3개, physical write `ON/OFF/ON` 정확히 3회, terminal journal의 `NO_CHANGE` 0개, revision `10`,
OFF·planned-ON receipt 2장, terminal ON, (2) 첫 send 전에 journal store에서 reload한 record 기준으로 위
두 payload equality·세 baseline-readback·`true/false/true` recovery policy·각 rollback digest와 모든
blob이 durable하고 power 전용 builder가 inherited hardcode를 사용하지 않음, (3) OFF action의 actual ON
baseline은 유지되고 same/planned ON만 actual prepared OFF로 remap하며 same ON의 `no_change=true`와
OFF의 `no_change=false`를 준비 시 확인, (4) same ON·OFF·planned ON 각 send/ACK/readback/cancel fault와
planned ON 검증 뒤 terminal-save fault에서 receipt 0, forward retry 0, recovery ON write 0·stable-state
OFF write 최대 1, (5) 유효한 boolean recovery state가 이미 OFF 또는 아직 ON일 때 위 action별 exact
terminal·OFF-attempt 분류를 고정하고, same/planned recovery read failure는 새 save 0·직전 action phase
유지·이미 저장된 transaction `RECOVERY_REQUIRED`, OFF recovery read failure는 action `UNCERTAIN`·
transaction `RECOVERY_REQUIRED`·send 0임을 구분하며 production schema에 없는 제3의 power 값을 만들지
않음, (6) power OFF·TimerON·non-constant·non-independent·Flow `>45`·config/app range 밖, strict target·epoch·
firmware mismatch 각각 첫 send와 journal과 receipt 0, (7) phase 사이 Timer·mode·linkage·Flow drift와
record 생성 뒤 resolve·connect·envelope·writer close fault는 새 forward 없이 transaction
`RECOVERY_REQUIRED`와 OFF recovery로 끝남, (8) receipt canonical bytes·distinct request·binding function `power`·artifact
no-clobber와 terminal finalization의 network/write 0 멱등성, (9) manual-flow producer·CLI source와 기존
assert byte 불변, frozen/import closure 불변, (10) 새 producer가 `LocalControlLease`에 같은
`physical_identity_key` 하나를 넘기며 새 lock namespace를 만들지 않음, (11) power producer가
coordinator `_execute_prepared()`를 호출하지 않고 producer `_execute_action()`만 사용함입니다. test
status는 production Pro·Bar schema bytes를 사용하고 firmware 결과를 새 fake knob로 만들지 않습니다.

§3의 wrong-device는 strict manifest binding과 같은 physical lock key, out-of-envelope·다른 장비 전파는
저출력 independent admission, duplicate는 action별 새 writer와 no-retry, journal loss는 첫 send 전 세
recovery mapping 설치, real FAIL의 PASS 오판은 ACK·fresh exact full-state predicate와 terminal journal을
모두 요구해 닫습니다. source landing 뒤에도 장비 write와 새 qualification surface는 0입니다. 이후 live는
Bar `manual_flow`를 먼저 완료한 뒤 별도 승인된 한 장비·한 function power operation으로 순차 수행하며,
세 장비를 동시에 OFF/ON하지 않습니다.

### 2026-09-13 software-independent Constant minimum group commissioning D1p

[`power authority deployment`](docs/runs/2026-09-13-local-power-authority-deployment.md)은 세 수류모터의
`manual_flow`·`power` qualification을 하나의 current authority로 합쳐 direct control `6`, group switch
`1`, member resume `3`을 startup device write 없이 열었습니다. repository maintainer와 on-site
hardware approver는 현재 집에서 수조를 직접 감시하고 물리 전원 차단 수단을 쥔 상태로, 사람이 앱에서
직접 해야 하는 조작을 제외한 M4 실기를 계속 진행하도록 승인했습니다. 이 승인은 아래 Constant minimum
one-shot 한 건, 그 앞의 임시 `power=30` config-only 배포, 그 뒤의 exact prior `power=50` 복귀 배포와
ordered recovery·exact restore에 한정하며 group OFF, Sync, Anti Phase, direct override, resume, 장애
주입이나 다른 M4/M5 operation을 포함하지 않습니다. 두 배포는 각각 start·rollback 실패 가능성을 독립적으로
가지므로 one-shot이라는 표현으로 그 위험을 생략하지 않습니다.

이번 작업이 닫을 질문은 **current exact three-member binding과 dual-function qualification 아래에서,
software-independent Constant minimum plan 하나가 모든 baseline을 첫 frame 전에 읽고 하나의 plan-wide
journal을 만든 뒤 각 멤버의 `manual_flow`만 `31 -> 30`으로 정확히 한 번 바꾸고, same-value power ON은
전송하지 않으며, 후속 fresh cycle에서 같은 physical plan을 중복 전송하지 않고 세 장비와 prior deployment를
exact restore할 수 있는가**입니다. MQTT 문구나 group aggregate만으로 PASS를 만들지 않고 terminal journal,
멤버별 exact scalar readback, fresh authenticated state와 전후 durable tree로 판정합니다.

현재 private group default는 `pattern=constant`, `power=50`, `min_power=30`, `max_power=75`,
`period_seconds=10`, `transition_seconds=0`이고 세 멤버의 gain은 모두 `1`입니다. `GroupRuntime` 모델의
일반 기본 state는 `STOPPED`이지만 production `GroupManager.__init__`은 current `group.enabled=true`를 읽어
runtime state를 명시적으로 `RUNNING`으로 초기화하고 pattern·power·range·period는 `None`으로 둡니다.
따라서 첫 group ON은 현재 private default를 그대로 사용합니다. M4의 “최소 출력”을 추측으로 `50`에
붙이지 않고, 장비 write 전에 private config의 group
`default.power` 하나만 `50 -> 30`으로 바꾼 임시 immutable generation을 만듭니다. app-declared capability
`30..100`, per-device trusted limit `30..100`, persistent group min/max `30..75`, failure policy와 나머지
config·manifest·qualification·image bytes는 바꾸지 않습니다. 이는 capability나 HA input range를 줄이는
새 gate가 아니라 이 attended operation의 exact target 선택입니다.

config-only 준비는 owner-only no-clobber 새 input과 새 Compose generation으로 수행합니다. 같은 output
경로에 다른 bytes를 덮지 않으며, 재시도에는 새 경로를 씁니다. prior exact config `power=50`, image,
Compose와 private bundle generation 전체를 rollback·최종 restore용으로 보존합니다. offline projection에서
binding `4`, receipt `12`, qualified pair `6`, ignored category별 `0`, discovery `90`, direct control `6`,
group switch `1`, resume `3`, command topic `10`과 모든 discovery payload가 current와 byte 동일해야 합니다.
배포는 persistent service stop-first 뒤 임시 generation 하나만 시작하고 journal·receipt tree byte 불변,
unfinished `0`, retained command `0`, fresh `6/6`, group supervisor `1`, armed `false`, cycle failure `0`,
startup local-control device write `0`을 확인한 뒤에만 앱 precondition으로 갑니다. startup write `0`은
journal-first인 direct·group 경로에 한해 journal tree 불변으로 판정합니다.

앱 조작 전에는 세 멤버 각각의 current decoded state를 같은 field 집합으로 보존한 뒤 persistent service를
먼저 완전히 stop-first합니다. 현재 알려진 운전
baseline은 첫 Pro `ON / TimerOFF / constant / independent / Flow 40`, 두 번째 Pro
`ON / TimerON / constant / independent / latent manual Flow 40`과 active schedule, Bar
`ON / TimerOFF / classic / independent / Flow 91 / Frequency 80 / PulseTide false`입니다. 실제 fresh
read가 이 값과 다르면 기록되지 않은 drift로 중단하고 새 baseline을 추측하지 않습니다. 두 번째 Pro의
TimerON 상태에서 manual Flow readback만 바꾸면 active schedule 출력은 그대로여도 PASS처럼 보일 수 있으므로
TimerOFF는 실기 의미를 지키는 필수 precondition입니다.

service가 정지한 상태에서 on-site approver가 앱으로 세 장비를 각각 `power ON`, `TimerOFF`, `Mode=constant`,
`Linkage=independent`, `Flow=31`로 만듭니다. Bar의 `classic/91 -> constant/31`은 60%p 하향과 파형
변경이고, 두 Pro의 `40 -> 31`도 9%p 하향입니다. agent가 이 앱 조작의 step이나 frame 수를 정하지
않으며 approver가 수조 상태를 보며 나눠 적용할 수 있습니다. 이때 HA group/direct control을 건드리지
않고, 앱 precondition 뒤 separate fresh authenticated read로 세 exact anchor가 모두 위 다섯 조건과
Flow `31`을 만족하는지 확인합니다. 한 멤버라도 다르거나 identity가 exact one-to-one으로 풀리지 않으면
device command·journal 생성 전에 중단합니다.

첫 command 직전에는 current journal·receipt inventory와 unfinished `0`, legacy exact-restore idle,
qualified pair `6`, global `/hardware-safety` lease·latch availability, 단일 group supervisor와 shared
direct/group operation-lock identity를 다시 확인합니다. service를 다시 시작하면 armed `false`여야 하고,
service의 fresh read로도 세 precondition이 그대로인지 재확인한 뒤 subscribe-only MQTT observer를
result·group status·availability에 먼저 붙입니다. command는 retained가 아닌 group ON boolean 한 번뿐이고
group OFF를 먼저 보내거나 recovery·retry 대신 사용하지 않습니다.

`apply_group_switch(true)`는 production 초기화로 이미 `RUNNING`인 fresh runtime의 ON ownership을
명시적으로 arm하고 default Constant `30` plan과 이후 observation-driven submission을 활성화합니다. 세
멤버 모두 gain `1`, online, manual override `0`이어야 하므로 plan은
각 멤버에 `manual_flow=30` 뒤 `power=true` 순서의 exact two-intent target 하나씩, 총 여섯 action을
만들어야 합니다. 세 `manual_flow` action은 baseline `31`과 달라 각각 physical frame 한 번과 exact
readback을 요구하고, 세 `power=true` action은 baseline ON과 같아 `NO_CHANGE`로 journal만 한 번 저장하며
frame을 보내지 않습니다. 따라서 사전 예상은 dispatch member `3`, action `6`, Flow physical write
정확히 `3`, power physical write `0`, 전체 write `3`, `NO_CHANGE` `3`, receipt mutation `0`, terminal
`VERIFIED revision 13`입니다. revision은 changed action `3 x 3 save`, no-change action `3 x 1 save`,
transaction terminal save `1`에서 유도합니다. 실제 action 순서·frame 수·revision이 다르면 사후 설명을
만들지 않고 FAIL로 조사합니다.

첫 frame 전에는 resolver가 세 complete stable binding을 exact one-to-one으로 다시 풀고 모든 function
qualification을 검증하며, deployment-wide global lease와 세 distinct physical lease를 한 번에 잡고, 세
장비 baseline을 모두 fresh read한 뒤에만 one request journal을 durably 생성해야 합니다. result는 MQTT
subscribe-only observation으로 받고 terminal record와 referenced blobs를 private store에서 재독합니다.
성공 뒤에는 세 장비가 `ON / TimerOFF / constant / independent / Flow 30`인지 별도 fresh read로 확인하고,
최소 세 번의 완전한 후속 fresh group cycle 동안 journal operation 수를 연속 관측해 같은 plan identity가
deduplicate되고 operation·physical write가 늘지 않는지 확인합니다. initial terminal operation 뒤 새
operation이나 physical write가 하나라도 늘면 즉시 persistent service를 stop-first하고 group OFF는 보내지
않습니다. aggregate group output은 보조 관측일 뿐 멤버별 exact readback을 대신하지 않습니다.

어느 command failure든 group ON을 새 request로 retry하거나 group OFF를 보내지 않습니다. 먼저 실제 세
장비의 power·Flow를 fresh read하고 persistent service를 정지합니다. non-terminal journal이 있으면 오직
`jebao-flow-local-qualify recover`의 existing generic recovery로 같은 request를 처리합니다;
`jebao-flow-local recover`는 random install generation을 새로 만들 수 있어 금지합니다. reverse recovery는
실제로 시도된 `manual_flow=30` changed action만 각 action의 보존 baseline `31`로 되돌리고 power action은
frame을 보내지 않아야 합니다. recovery frame 수는 `VERIFIED`와 `UNCERTAIN`을 합친 attempted changed
action 수 이하, 최대 `3`이고 terminal
`ROLLED_BACK` 뒤에도 actual state를 fresh read하기 전에는 복구 완료를 주장하지 않습니다. verified
transaction 뒤의 postcheck mismatch는 recovery로 승격하지 않고 on-site app restore로 처리합니다.

성공·실패 어느 쪽이든 group runtime을 armed 상태로 남기지 않습니다. terminal·idle을 확인한 뒤 service를
먼저 정지하고 on-site approver가 앱에서 각 장비를 위에 보존한 개별 baseline으로 되돌립니다. 특히 첫
Pro는 `constant/40/TimerOFF`, 두 번째 Pro는 latent manual `40`을 복원한 뒤 `TimerON` 및 그 시점 active
slot과 일치하는 `AutoMode`/`AutoFlow`로 schedule ownership,
Bar는 `classic/91/Frequency 80/PulseTide false/TimerOFF`를 같은 field 집합으로 확인합니다. 그 뒤 임시
`power=30` deployment를 폐기하지 않고 기록용 input으로 보존하되, 실행 전 보존한 exact prior
`power=50` config·image·Compose·bundle generation으로 service를 복귀시킵니다. final service는 armed
`false`, discovery·control byte exact, retained command `0`, fresh `6/6`, individual baselines exact,
unfinished `0`이어야 합니다. journal에는 이번 terminal operation을 보존하고 qualification receipt tree는
byte 불변이어야 합니다.

이 slice는 frozen six·source·test·catalog·writer·coordinator·journal·qualification·HA schema를 수정하지
않고 새 CLI·gate·retry·timer를 만들지 않습니다. private config-only 임시 generation, existing MQTT group
command, existing generic recovery와 read-only audits만 사용합니다. 실제 실행 1회는 append-only
`docs/runs/` record와 README index를 먼저 commit하고, 그 전에는 `src/`를 수정하거나 Sync·Anti Phase·
group OFF·direct override·resume 실기로 넘어가지 않습니다. raw private identifiers·paths·broker secret은
출력·문서·commit에 싣지 않고 public aggregate, domain-separated identity binding과 artifact digest만
기록합니다.

### D1r — software-independent Sync 30/31 bounded epoch

2026-09-13 D1q Constant 실기와 사용자 수용 상태 복귀를
`docs/runs/2026-09-13-local-group-constant-commissioning.md`로 commit한 뒤 사용자는 다음 작업의
진행을 지시했습니다. 다음은 native Linkage Sync가 아닌 software-independent Sync의 계획·준비와
현장 precondition 뒤 bounded epoch 한 건입니다. 앞선 현장 감시·중단 수단 경계는 유지하고, 앱 조작은
사용자가 수행합니다. 이 정책만으로 아직 맞추지 않은 장비 상태를 충족했다고 간주하지 않습니다.
Anti Phase·group OFF·override·resume·장애 주입이나 native frozen 하네스는 이번 범위 밖입니다.

닫을 질문은 **세 manual-owned 멤버에 같은 30/31 목표를 주는 Sync square-wave가 두 번의 방향 전환을
실제로 적용하고, 같은 목표의 후속 fresh cycle에서는 중복 operation을 만들지 않으며, 세 번째 terminal
plan 뒤 service 정지로 끝나는가**입니다. SyncPattern은 runtime.power/effective_power가 아니라
effective_limits의 max/min을 반주기마다 고릅니다. 따라서 default.power만 30으로 바꾸는 것은 출력
제한이 아니며 prior max75를 남기면 75가 선택됩니다. 임시 immutable config는 default.pattern=sync,
default.power=30, default.min_power=30, default.max_power=31, default.period_seconds=120,
transition_seconds=0, 세 member gain=1로 명시합니다. 나머지 authority·identity·source·schema·receipt와
app capability 30..100은 바꾸지 않습니다. min/max30/31은 이 operation의 두 목표값 선택이지 영구
capability 축소나 새 runtime gate가 아닙니다. 60초 반주기는 시험 파형이며 power-OFF 체류 timer가 아닙니다.

GroupManager는 생성 시 monotonic epoch를 시작하고 이미 RUNNING인 group의 ON은 그 epoch를 reset하지
않습니다. public MQTT lifecycle에는 epoch.started_at이나 내부 dispatch dedupe counter가 없으므로
외부 observer가 그것을 읽었다고 주장하지 않습니다. 최초 ON의 command-result에만 있는 submitted
identity는 보조 관측으로 보존하되 verdict로 쓰지 않고 후속 lifecycle에도 있다고 가정하지 않습니다.
처음 선택되는 값은 30 또는31의 두 분기로 사전
등록하고, 첫 terminal에서 실제 값을 확인한 뒤 다음 두 terminal 목표가 보수값으로 교대해야 한다고
판정합니다. 두 Pro·Bar의 물리 적용은 직렬 장비 트랜잭션이므로 동일 plan timestamp·같은 목표와 실제
수류의 완전 동시성은 구분합니다. 이 실기는 정밀한 물리 위상 동기나 정각 60초 적용 지연까지 입증하지
않으며 그런 부분은 UNKNOWN으로 둡니다.

준비는 D1q에서 복귀한 exact prior Constant50 image·Compose·input generation 전체와 current 상태를
보존하는 것으로 시작합니다. 임시 Sync 배포와 prior Constant50 복귀 배포를 각각 수행하며 실패 자료는
원 stderr·stage log·Compose 상태까지 보존합니다. source secret은 production처럼 owner-only 0700
parent 안의 0444 파일이고 backup/runtime private file의 0600과 혼동하지 않습니다. 파일 owner·mode·
link count와 fsync, exact bytes·host bind source 존재를 확인하고 preserved image만 사용하며 rebuild하지
않습니다. 잘못된 output을 덮지 않고 새 artifact로 정정합니다. offline projection에서 기존 binding4·
receipt12·qualified pair6·ignored0, discovery90 exact·tombstone53·command10·direct6·group1/resume3가
byte 불변이어야 합니다. 임시 기동 뒤 readiness·fresh6·unarmed·journal/receipt 불변을 확인합니다.

앱 조작 전에는 서비스부터 정지합니다. fresh baseline은 첫 Pro ON/TimerOFF/constant/independent/
Flow40/Frequency34, 두 번째 Pro ON/TimerON/constant/independent/latentFlow30/Frequency32 및 그 시점
active schedule, Bar ON/TimerOFF/classic/independent/Flow91/Frequency80/PulseTidefalse입니다.
실제 관측이 다르면 새 baseline을 추측하지 않고 사용자와 정리합니다. 사용자가 세 장비를 ON·TimerOFF·
constant·independent·Flow30으로 맞춥니다. Bar는 91→30의 61%p 하향과 파형 변경, 첫 Pro는40→30,
두 번째 Pro는 manual30 유지와 schedule 소유권 해제입니다. 두 번째 Pro의 레지스터가 그대로여도
TimerOFF는 그 시점 schedule 출력에서30으로 실제 운전을 바꿉니다. active slot을 조작 직전에 읽어
변화 폭을 공개하며 앞선 sine45를 현재값으로 고정하지 않습니다. 앱 조작의 step·frame 수는 agent가
정하지 않고 사용자가 수조를 보며 적용합니다. latent30이 있다는 이유로 TimerOFF 확인을 생략하지
않습니다. 이 상태에서 separate authenticated fresh read, 서비스 재시작 후 readiness·unarmed·fresh
field 재확인을 마친 뒤에만 ON을 한 번 발행합니다. 그동안 HA direct/group control이나 앱을 병행 조작하지
않도록 안내합니다. first write의 global+physical lease·latch·exact resolver와 journal-first는 기존
coordinator가 담당하며 감사 프로세스가 out-of-band LOCK_EX를 취득하지 않습니다.

관측자는 current canonical inventory를 pre anchor로 고정하고 결과·group lifecycle·device state를
subscribe-only로 먼저 받습니다. 이전 실행의 수치 14 operations/48 blobs/62 files 및 receipt18은
예상 기준이며 실행 직전 exact bytes를 다시 확인합니다. group ON은 QoS0·retainfalse 한 번뿐입니다.
초기 MQTT completion_pending을 실패나 성공 verdict로 사용하지 않습니다.

사전 등록할 정상 분기는 다음 두 가지입니다. 각 record의 action 순서는 manual_flow/power ×3입니다.

- 최초 목표30: 첫 record는 여섯 action 모두 NO_CHANGE, revision7, frame0. 이후31·30의 두
  record는 각각 Flow VERIFIED3·power NO_CHANGE3, revision13, Flow frame3입니다.
  합계 record3·Flow frame6·power frame0입니다.
- 최초 목표31: 31·30·31의 세 record 모두 Flow VERIFIED3·power NO_CHANGE3, revision13,
  각 Flow frame3입니다. 합계 record3·Flow frame9·power frame0입니다.

모든 record는 세 exact anchor를 각각 두 action에 결속하며 expected Flow는 각 record 내 모두 같은
30 또는31, baseline Flow는 첫 record의30 또는 직전 verified record 목표여야 합니다. power의 baseline과
expected는 모두 ON이고 power frame은 없습니다. terminal action·revision·readback 또는 교대 순서가
두 사전 분기와 다르면 사후 설명으로 통과시키지 않습니다. 같은 목표가 반복되는 hold의 lifecycle cycle과
journal bytes를 함께 보존해 중복 억제를 확인합니다. 내부 dedupe counter 대신 어느 fresh cycle 사이에서
새 operation이 없었는지 기록하며, 관측이 부족하면 그 항목은 미확정으로 남기고 실행을 연장하지 않습니다.
새 blob 개수는 이전 content-addressed 내용과 비교해 종류별 신규/재사용을 설명하며 generic coordinator의
intent digest에 보존 preimage가 있다고 가정하지 않습니다.

D1q의 '새 operation 하나면 정지' guard는 이번에 재사용하지 않습니다. Sync는 정상 반주기 전환에도
새 record와 Flow3 frame이 필요합니다. 새 record는 최대3개이고 세 번째 VERIFIED가 확인되면 즉시
persistent service를 stop-first합니다. 4번째 record, 예상 밖 target·action·nonterminal failure,
기존 record 또는 이미 terminal인 record 변경, lifecycle failure·감시 프로세스 오류가 생겨도 즉시
stop-first합니다. publish 후180초는 전체 관측의 상한이며, 두 전환을 못 봤다고 시간을 연장하거나
ON을 다시 보내지 않습니다. 이 감시 상한은 관측 기반 중단 규칙이지 네 번째 frame의 전송을 물리적으로
불가능하게 만드는 새 gate라고 주장하지 않습니다. 정상 중단 목표는 ON 및 Flow30 또는31이고, 이를
살아 있는 수조에 무조건 무해하다고 일반화하지 않습니다.

service 종료는 새 OFF를 합성하지 않지만 이미 실행 중인 일반 plan이 마무리될 수 있습니다. 정지 완료
후 actual state와 durable record를 재독하며 stopped 명령 성공만으로 장비 상태를 단정하지 않습니다.
unfinished가 있으면 기존 config-only `jebao-flow-local-qualify recover`로 해당 operation만 처리하며
`jebao-flow-local recover`·새 ON·group OFF·forward retry는 금지합니다. reverse recovery는 마지막
nonterminal record에서 시도된 changed Flow action(VERIFIED/UNCERTAIN 포함, 최대3)을 그 record의
실제 baseline30 또는31로 되돌립니다. 이전 VERIFIED record를 recovery 대상으로 삼거나 전체 epoch의
앱 baseline을 자동 복구한다고 주장하지 않습니다. receipt mutation은0입니다.

정지 후 사용자가 firstPro40/TimerOFF, secondPro latent30/TimerON, Bar classic91/TimerOFF로 돌리고
Frequency34/32/80·Bar PulseTidefalse 및 secondPro의 그 시점 active-slot AutoMode/AutoFlow를 확인합니다.
사용자가 다른 최종 상태를 수용하면 원래값 exact 복원과 분리 기록합니다. prior Constant50 image·Compose·
bundle로 복귀한 뒤 unarmed·fresh6·discovery90 exact·retainedcommand0·unfinished0·최종 journal 보존과
receipt 불변을 확인합니다. 앱 altered-regime 및 서비스 부재의 시각·관측 한계를 따로 남기고 짧은 command
span으로 전체 물리 비용을 축소하지 않습니다.

이 slice는 src/tests·frozen six·writer·coordinator·catalog·journal·qualification·HA schema를 수정하지
않고 새 CLI·retry·gate를 만들지 않습니다. 결과는 새 append-only run과 README에 먼저 commit하며,
그 전에 다른 M4/M5 실기나 source 변경으로 넘어가지 않습니다.

### D1s — software-independent Anti Phase 30/31 bounded epoch

사용자는 D1r Sync 기록 `41487db`와 prior Constant50 복귀 뒤 다음 Anti Phase 준비·진행을
지시했습니다. 이번 범위는 native Linkage가 아닌 기존 software-independent AntiPhasePattern의
단회 bounded commissioning입니다. 기존 현장 감시·중단 수단과 사용자 앱 조작 경계를 유지합니다.
임시 immutable generation 배포, 앱 precondition, MQTT ON 한 번, 목표 벡터 네 번 전환 관측,
service stop, 앱 복원과 exact prior Constant50 복귀가 범위이며 다른 physical operation은 하지 않습니다.

닫을 질문은 **기존 member phase0/180/90의 서로 다른 목표가 네 번 전환해 처음 벡터로 돌아오고,
같은 목표의 후속 cycle에서는 새 operation을 만들지 않는가**입니다. current config의 세 phase는
secondPro0·firstPro180·Bar90이며 gain1·invertfalse·enabledtrue입니다. 이름이나 모델 기본값으로
유추하지 않고 manifest의 exact physical anchor로 역해석한 값입니다. phase·gain·invert·role은
바꾸지 않습니다. 임시 delta는 default.pattern=anti_phase, max_power=31, period_seconds=120의
세 항목뿐입니다. min30·power50·transition0과 나머지 config는 그대로입니다. AntiPhasePattern은
member.phase와 min/max를 사용하고 default.power·member.invert는 읽지 않습니다. power50 유지가
목표50을 뜻하지 않으며 이번 목표는30/31입니다. app capability·device limits·qualification·HA
surface를 좁히는 새 gate가 아니라 이 operation의 파형 선택입니다.

멤버 순서를 (secondPro, firstPro, Bar)로 고정한 계산기 목표 벡터는 다음과 같습니다.

| epoch 내 구간 | 벡터 | 다음 경계에서 바뀌는 멤버 수 |
|---|---|---:|
| A: 0..30초 | (31,30,30) | 1 |
| B: 30..60초 | (31,30,31) | 2 |
| C: 60..90초 | (30,31,31) | 1 |
| D: 90..120초 | (30,31,30) | 2 |

구간은 시작 포함·끝 제외이고 A→B→C→D→A의 순환입니다. 최초 위상은 네 분기로 사전 등록하고
첫 terminal의 actual vector 이후에는 이 순서만 허용합니다. 서비스 시작 wall-clock을 manager의
monotonic epoch로 동일시하지 않습니다. 설정상 두 Pro가 반대 목표이고 Bar가90도 지연되지만 실제
장비 처리는 직렬이므로 정확한 물리 동시성·위상차·정각30초 적용이나 실측 수류량을 주장하지 않습니다.
직전 실기의 약13초 처리 시간은 이번 처리 상한이 아닙니다. 경계와 처리 시점이 겹쳐 다음 record가
곧이어 생길 수 있으므로 record 간30초 간격을 gate로 삼지 않으며, 전환 누락은 성공으로 꾸미지 않습니다.
서로 다른 멤버 목표를 주는 실기이며 1%p 범위를 살아 있는 수조에 무조건 무해하다고 일반화하지 않습니다.

준비 시 exact prior image·Compose·전체 input generation과 canonical journal/receipt inventory를
보존합니다. 예상 pre는17 operations/55 blobs/72 files와 receipt18이나 직접 재확인하며, 전후 root·
relative POSIX path·정렬·canonical ASCII JSON 방식은 D1r과 같습니다. source0444/parent0700·owner·
link count1을 production 생성 경로로 유지하고 backup0600과 혼동하지 않습니다. 새 output은 no-clobber,
Compose 교체는 production 함수를 사용하고 재빌드하지 않습니다. offline projection의 binding4·
authority receipt12·pair6·ignored0, discovery90 exact·tombstone53·command10·direct6·group1/resume3
불변을 확인합니다. 임시 배포 뒤 readiness·unarmed·fresh6·두 tree 불변을 확인합니다.

앱 조작 전 service를 먼저 정지합니다. baseline은 첫Pro40/TimerOFF/constant/Frequency34,
두 번째Pro latent31/TimerON/constant/Frequency32 및 그 시점 active schedule,
Bar91/TimerOFF/classic/Frequency80/PulseTidefalse이며 모두 ON·independent입니다. fresh read가
다르면 관측값을 먼저 보존하고 사용자와 정리합니다. 앱 precondition은 세 장비 ON·TimerOFF·constant·
independent·Flow31입니다. 첫Pro40→31은9%p, Bar91→31은60%p 하향과 파형 변경입니다.
두 번째Pro의 manual31이 같아도 TimerOFF는 그 시점 schedule 출력에서31로 실제 운전을 바꾸므로
조작 직전 slot을 읽어 변화 폭을 알립니다. 사용자 수조 관찰에 따른 앱 조작 step과 frame 수는
agent가 강제하지 않습니다. 앱 조작 후 separate authenticated read, 재기동 후 readiness·unarmed·
fresh field 재확인을 통과한 뒤에만 ON을 보냅니다. 그동안 앱·HA 병행 조작을 하지 않도록 안내합니다.
기존 coordinator의 exact resolver·lease·latch·journal-first를 사용하고 감사용 out-of-band lock은 없습니다.

초기 baseline은 (31,31,31)입니다. A 또는D에서 시작하면 changed Flow2·NO_CHANGE4·revision11·
Flow frame2, B 또는C에서 시작하면 changed Flow1·NO_CHANGE5·revision9·Flow frame1입니다.
후속 네 전환에는 changed Flow1/2가 번갈아 발생해 합계6 frame입니다. 따라서 record5·총 Flow7 또는8·
power frame0이며, 각 record는 action6(manual_flow/power ×3), revision=7+2×changed Flow 수입니다.
첫 baseline은31, 이후 baseline은 직전 terminal의 멤버별 target이어야 하며 모든 power는 ON의
NO_CHANGE입니다. 다섯 번째 target은 첫 target과 같아야 합니다. source expected readback과 실제
journal target·action·revision·frame 산술이 다르면 사후 설명으로 PASS를 만들지 않습니다.

ON은 QoS0·retainfalse 한 번입니다. 최초 result의 pending·submitted identity는 보조 관측이며 verdict가
아닙니다. public lifecycle에 epoch나 내부 dedupe counter가 있다고 가정하지 않고 cycle와 record bytes로
유한 hold를 관측합니다. 첫 frame 전 rev0을 별도로 확보하지 않았다면 확보했다고 쓰지 않습니다.
generic coordinator의 intent preimage 보존은 가정하지 않고 새 blob은 참조별로 대조합니다.

다섯 번째 VERIFIED 직후 즉시 stop-first합니다. 여섯 번째 record·총 Flow8 초과·power attempted·
예상 밖 vector/순서/action·nonterminal failure·기존 또는 terminal record 변경·receipt 변경·lifecycle
failure·observer 오류도 즉시 stop 사유입니다. publish 후180초의 내부·host 독립 감시 상한을 적용하며
관측 부족이나 누락 전환을 보충하려고 연장·재ON·group OFF를 하지 않습니다. 이는 관측 기반 중단 규칙이며
추가 frame을 물리적으로 불가능하게 만드는 새 gate가 아닙니다. 정상 stop 목표는 ON·각Flow30/31이고
진행 중 plan이 종료 중 마무리될 수 있으므로 정지 뒤 실제 상태와 journal을 재독합니다.

unfinished는 기존 config-only `jebao-flow-local-qualify recover`로 마지막 nonterminal operation만
처리합니다. `jebao-flow-local recover`·forward retry·group OFF는 금지합니다. recovery 대상은 해당
record에서 시도된 changed Flow(VERIFIED/UNCERTAIN 포함)이고 그 멤버의 실제 baseline30/31로 돌아갑니다.
이전 VERIFIED record나 앱 baseline 전체 자동 복구를 뜻하지 않으며 fresh read 전에 복구 완료를 주장하지
않습니다. 복구 frame 수는 실제 attempted changed action 수로 판정합니다.

service가 정지한 상태에서 사용자가 first40/TimerOFF, second manual31 후 TimerON, Barclassic91로
복원하고 Frequency34/32/80·BarPulseTidefalse·second의 그 시점 active-slot 일치를 확인합니다.
초기 C/D 분기는 최종 second Flow30이므로 all31 precondition만으로 latent31 복원이 보장되지 않습니다.
사용자가 다른 latent 최종값을 수용하면 exact 원복과 분리해 기록합니다. prior Constant50로 복귀 후
unarmed·fresh6·discovery90 exact·retainedcommand0·unfinished0·최종 journal/receipt 불변을 확인합니다.
정상 복귀가 group 목표50의 실제 재전송을 뜻하지 않습니다. 앱 준비·복원 대기와 service 부재도 기록하며
짧은 command span으로 물리 비용을 축소하지 않습니다. 실패한 deploy는 원 stderr·stage log·Compose
상태를 보존하고 exact prior image/input으로 복귀하며 이전 UNKNOWN의 원인을 소급 추정하지 않습니다.

src/tests·frozen six·writer·coordinator·journal·qualification·HA schema는 무수정이고 새 CLI·gate·retry는
추가하지 않습니다. 실기 뒤 append-only run과 README를 먼저 commit하며 다른 M4/M5로 넘어가지 않습니다.

### D1t — Constant31 direct override와 member resume bounded epoch

D1s 기록 `d115250`과 prior Constant50 복귀 뒤 사용자는 에이전트가 혼자 진행할 수 없는 지점까지
계속 진행하라고 지시했습니다. 이번 질문은 **첫 Pro의 direct manual_flow override가 후속 그룹
cycle에 덮이지 않고, 그 멤버의 resume 뒤에는 첫 Pro만 그룹 목표로 돌아오는가**입니다. 임시 배포,
사용자 앱 precondition, group ON1·direct Flow32 1·member PRESS1, 유한 hold, stop과 앱 복원·
prior 배포가 범위입니다. 앱 선행 조작과 현장 감시·중단 수단은 사용자가 담당하며 그 전에는 실행하지 않습니다.
native Linkage·group OFF·다른 멤버의 direct 조작·추가 pattern 실기를 포함하지 않습니다.

기존 Constant config의 default.power만50→31로 바꿉니다. min30/max75/period10/transition0 및
member phase·gain·invert·role은 불변입니다. Constant는 default.power31을 사용하므로 초기 baseline31과
일치할 때만 arm이 no-change입니다. prior50에서 ON을 눌러도 같은 결과라는 주장은 금지합니다.
group clamp를 direct 입력 범위로 쓰지 않습니다. direct32는 app/device30..100 안이며 attended50 이하이고
실기 변화는 첫 Pro31→32→31의1%p 왕복입니다. 이는 capability나 영구 상한을 낮추는 새 gate가 아닙니다.

현재 private manifest의 physical anchor로 firstPro를 exact-one 역해석합니다. 다른 두 anchor도
일대일 결속합니다. 임시 입력은 production prepare-compose의 source0444/parent0700/owner/link1로 만들고
exact prior image·Compose·전체 bundle·canonical journal/receipt inventory를 보존합니다. offline
projection은 discovery90 byte 불변·old/new/common-changed0, binding4/receipt12/pair6/ignored0,
tombstone53/command10/direct6/group1/resume3을 확인합니다. 코드·tests·writer·journal·HA schema를
수정하거나 새 CLI·production gate·retry를 만들지 않습니다. 감사는 production lock을 별도로 취득하지 않습니다.

임시 배포 뒤 readiness·unarmed·fresh6·startup journal/receipt 불변을 확인한 다음 service를 먼저
정지합니다. 앱 baseline은 first40/TimerOFF/constant/Frequency34, second latent31/TimerON/constant/
Frequency32와 그 시점 active schedule, Barclassic91/TimerOFF/Frequency80/PulseTidefalse입니다.
앱 준비는 모두 ON/TimerOFF/constant/independent/Flow31입니다. first40→31은9%p, Bar91→31은60%p와
파형 변경이며 second도 현재 schedule 출력에서31로 바뀝니다. 조작 직전 slot을 읽어 실제 차이를 알리고
과거 slot 값을 고정하지 않습니다. 앱 완료 후 separate fresh read, 서비스 재시작 후 readiness·unarmed·
fresh field 재확인을 거쳐야 ON을 보냅니다. 앱·HA 병행 조작은 하지 않도록 안내합니다.

사전 등록한 성공 경로의 durable 산술은 다음과 같습니다. 모든 power action은 ON의 NO_CHANGE입니다.

| 단계 | journal 구성 | revision | physical Flow frame |
|---|---|---:|---:|
| group ON, all31 | 3멤버·action6 모두 NO_CHANGE | 7 | 0 |
| 첫 Pro direct32 | 1멤버·manual_flow action1 VERIFIED | 4 | 1 |
| override 후 reduced group | second·Bar action4 모두 NO_CHANGE, first 제외 | 5 | 0 |
| 첫 Pro resume | 3멤버·action6, first32→31만 VERIFIED, 나머지 NO_CHANGE5 | 9 | 1 |

합계 record4·Flow frame2·power0입니다. 초기 group terminal 뒤 최소3번의 완전한 fresh cycle 동안
record/bytes 불변을 확인하고 direct32를 한 번 보냅니다. direct terminal과 reduced group terminal을
모두 확인한 뒤 최소3번의 완전한 후속 fresh cycle에서 first32·나머지31과 override exact-one 유지 및
새 operation0을 확인합니다. 그때만 firstPro resume topic에 PRESS를 한 번 보냅니다. resume terminal 뒤
최소3번의 완전한 fresh cycle에서 all31·override0·record 불변을 확인하고 즉시 service stop합니다.
fresh scalar 관측 시각은 해당 단계 terminal 뒤여야 하며 publish된 direct 값만 fresh 증거로 쓰지 않습니다.

override는 전송 전에 claim되므로 direct 실패 시에도 flag가 남을 수 있습니다. 이를 아무 일 없었던 것으로
쓰지 않습니다. 성공 경로를 실패 뒤 계속하려고 PRESS나 ON을 더 보내지 않고 즉시 service stop합니다.
volatile ownership은 process 재생성 뒤 unarmed 상태에서 재확인하며 실제 장비 복원은 fresh read로 확인합니다.
실패 경로의 corrective resume rev7/frame0을 이번 성공 시나리오의 대체물로 허용하지 않습니다.

정적 freshness 근거는 claim의 manager generation/control_epoch 변경, runtime._plan_guard의
owner_revision 및 manager.is_current 검사, group port→execute_batch→GenerationGuardedLocalControlPermit→
send_once의 기존 pre-send 검사입니다. 이미 전송한 frame의 ACK/readback은 끝낼 수 있고 다음 stale frame은
superseded로 끝납니다. 초기 steady hold 뒤에 direct를 보내는 이번 실기는 인위적인 경합 주입 검증이 아닙니다.
reduced plan은 skip decision과 control_epoch가 달라 새 identity·record를 만들므로 이를 중복 write로
오판하지 않습니다. stale plan은 frame을 보내기 전에 SUPERSEDED record를 남길 수 있습니다.
이는 기존 guard가 정상 작동한 사건일 수 있고 device 실패나 중복 전송의 증거가 아닙니다. 이번 좁은
시나리오는 그 extra record를 허용하며 계속하지 않고 stop하여 관측 미완료 UNKNOWN으로 남깁니다.
정상 record4와 별개로 추가1을 허용하는 확장은 하지 않습니다. FORWARD_ATTEMPTING 뒤 pre-send에서
superseded된 action을 physical frame으로 단정하지 않으며, 보수적인 attempted 상한 초과도 같은 방식으로
정지 원인과 실제 송신 증거를 구분합니다. 무해한 중단 가능성을 없애려고 재실기·자동 retry를 만들지 않습니다.

MQTT command는 각각 QoS0/retainfalse/1회이며 초기 결과·pending·ownership 문구는 보조 관측입니다.
판정은 terminal journal·exact scalar readback·그 이후 fresh state·lifecycle ownership과 durable tree로 합니다.
manual_override_members 원문은 private logical names이므로 private stream 안에서만 읽고 화면·중계·record에는
count와 anchor로 변환합니다. resume availability는 armed 조건이지 해당 멤버 override의 증거가 아닙니다.
resume은 전체 group plan을 재평가하지만 물리적으로 firstPro만 변경됐는지는 action별로 판정합니다.

record5·총 attempted Flow2 초과·power attempted·허용하지 않은 멤버/target/순서/action/revision·
nonterminal failure·기존/terminal record 변경·receipt 변경·lifecycle failure·observer 오류는 즉시 stop입니다.
같은 단계의 hold에서는 지정한 추가 operation을 제외하고 증가0을 요구합니다. 최초 ON부터180초의
내부·host 독립 관측 상한이며 완주 보장이나 물리 frame 차단 gate가 아닙니다. 부족하면 연장·재ON·
재direct·재PRESS·group OFF 없이 FAIL/UNKNOWN으로 끝냅니다. stop 중 in-flight plan이 끝날 수 있어
정지 뒤 실제 상태와 journal을 먼저 재독합니다.

nonterminal recovery만 기존 config-only jebao-flow-local-qualify recover로 마지막 record의 실제
attempted changed action을 그 record baseline으로 처리합니다. 이번 정상 계획의 record별 changed는 최대1이고
기존 VERIFIED record나 앱 baseline 전체 자동 복구를 주장하지 않습니다. local recover는 manifest를 바꾸므로
사용하지 않습니다. durable terminal만 보고 복구 완료로 쓰지 않으며 actual fresh state를 먼저 확인합니다.
서비스 정지 상태에서 앱으로 first40/TimerOFF, second31 뒤TimerON, Barclassic91과 Frequency34/32/80·
BarPulseTidefalse를 복원하고 second는 그 시점 active-slot 일치를 확인합니다. prior Constant50 복귀 뒤
unarmed·fresh6·discovery90 exact·command0·unfinished0·journal/receipt 불변을 확인합니다. 준비·복원 대기와
service 부재는 command span과 분리해 기록합니다. 다른 physical operation 전에 append-only run을 commit합니다.

### D1u — group canonical OFF와 OFF-state Flow-first ON 단회

D1t 실기와 복원·기록 commit `0f51f99` 뒤 사용자는 다음 진행을 요청했습니다. 에이전트가
세 수류모터 모두 OFF인 구간과 실패 시 OFF로 남아 사용자가 즉시 직접 켜야 한다는 결과를
공개하고 현장 복원 준비를 확인한 질문에 사용자는 “ㅇㅇㅇㅇ 진행해줘”로 명시 승인했습니다.
승인은 [D1u 계획](docs/local-group-power-commissioning-plan.md)의 임시 Constant30 배포,
사용자 앱 all31/TimerOFF/constant/independent precondition, OFF1·ON1, 유한 관측과
ordered recovery·앱 복원·exact prior Constant50 복귀 한 건에만 결속합니다. 다른 실기는 포함하지 않습니다.

닫을 질문은 canonical OFF가 power만 보내고, 뒤 ON이 꺼진 장비의 Flow31→30을 먼저 적용한 뒤
power ON을 별도 frame으로 보내는가입니다. source·tests·schema·qualification·새 gate는 변경하지
않습니다. 세 exact physical binding과 기존 coordinator/lease/journal-first/single-send를 사용합니다.
정상 예상은 OFF action3/rev10/power3, ON action6/rev19/Flow3+power3, record2/frame9이며
receipt 변화0입니다. OFF-state Flow write는 이번이 첫 실기이고 성공을 미리 가정하지 않습니다.

OFF 뒤 terminal과 independent authenticated read 한 번으로 OFF/Flow31 및 선언 field를 확인한
경우에만 ON1로 갑니다. OFF 상태에서 세 cycle을 기다리지 않습니다. ON 뒤에는 세 complete fresh
cycle에서 allON/Flow30·record 불변을 확인하고 stop합니다. 처음 OFF intent부터180초의 내부·host
독립 상한은 관측 중단 규칙일 뿐 OFF 시간의 물리 보장이 아닙니다. extra record·예상 밖 action/
target/순서·Flow attempted3 또는 power attempted6 초과·nonterminal failure·기존 record/receipt
변경·observer/lifecycle 오류는 즉시 중단하며 재OFF·재ON·corrective direct ON은 하지 않습니다.
supersession의 attempting은 실제 frame과 구분하며 완주하지 못하면 FAIL/UNKNOWN으로 기록합니다.

정지는 자동 ON을 만들지 않고 이미 accepted mandatory OFF는 drain될 수 있습니다. nonterminal만
기존 config-only qualification recover로 처리하고 일반 local recover나 이전 VERIFIED record는
사용하지 않습니다. OFF recovery는 fresh OFF/원ON 분류만 하며 ON을 보내지 않습니다. ON recovery는
actual attempted Flow baseline31 및 power baselineOFF로 역순 복구(최대6 frame)하므로 이미 켠
멤버를 다시 끌 수 있습니다. terminal만으로 전원 복원을 주장하지 않고 현장 복원과 fresh read가 필요합니다.

app·HA 병행 조작은 피하고 service stop 후에만 앱 precondition/restore를 요청합니다. 원 baseline은
fresh로 재확인하고 first40/TimerOFF/Frequency34, second latent31/TimerON/Frequency32와 그 시점
active slot, Barclassic91/TimerOFF/Frequency80/PulseTidefalse를 같은 field 집합으로 복원합니다.
source0444/parent0700의 production 생성·교체와 exact prior image/Compose/bundle을 사용하고
재빌드하지 않습니다. prior 복귀는 unarmed·fresh6·discovery90 exact·unfinished0·command0·두 tree
결속으로 확인합니다. 공개에는 anchor·count·digest만 남기고 MAC/private names/paths는 넣지 않습니다.
실행 후 append-only run을 먼저 commit하며 device별 OFF/ON 관측과 command span·service 부재를
분리합니다. MQTT 발행 간격을 실제 세 펌프 OFF 체류 시간으로 동일시하지 않습니다.

### D1v — 진단 image와 Bar-only OFF-state Flow 단회

D1u FAIL·recovery·앱 복원 기록과 diagnostic source `269e92b`, HA 업데이트 read-only 기록
`d9589ce`가 커밋된 뒤, 사용자는 targeted21/Ruff PASS와 full-suite 실패2건 미해결,
Bar만 OFF→Flow30→ON을 시도하고 실패하면 OFF로 남아 현장 앱 복원이 필요할 수 있다는
설명을 듣고 “ㅇㅇ 진행하자”로 진행을 승인했습니다. 두 Pro는 write 대상이 아닙니다.
승인은 [D1v 계획](docs/local-bar-off-flow-diagnostic-plan.md)의 단회와 필요한 ordered recovery,
사용자 앱 복원 및 exact prior service 복귀에 한정합니다. D1u 권한을 재사용하지 않습니다.

새 diagnostic image는 source·schema 결속과 offline projection을 확인한 뒤 stop-first로 배포합니다.
full-suite 실패 원인은 미해결이며 승인으로 PASS로 바뀌지 않습니다. prior image·Compose·whole
input bundle을 보존하고 rollback 때 재빌드하지 않습니다. config와 HA 표면은 변경하지 않습니다.
readiness·unarmed·fresh6·discovery90 exact·journal105/receipt18 불변을 확인한 뒤 서비스를 정지하고
fresh baseline을 보존합니다. 그 뒤에만 사용자에게 Bar의 classic91→constant31 준비를 요청합니다.
이 준비는60%p 하향과 파형 변경이며, Pro pair·group default Constant50은 그대로 유지합니다.
앱 준비 회신과 현재 현장 감시·직접 ON 복원 가능 확인 전에는 첫 device command를 보내지 않습니다.

group은 계속 비무장입니다. Bar ON/TimerOFF/constant/independent/Flow31에서 direct OFF1,
fresh OFF31 확인 후 direct Flow30 1, fresh OFF30 확인 후 direct ON1만 보냅니다.
성공 예상은 record3·rev4×3·Flow1/power2이고 ON 뒤 complete fresh cycle3에서 유지됨을 확인합니다.
첫 publish intent부터 내부·독립 host180초 상한을 적용하며 연장·재전송·group ON/OFF는 없습니다.
extra record·예상 밖 대상/값/action·Flow attempted1 초과·power attempted2 초과·Pro write·
observer 실패는 즉시 stop합니다. superseded는 physical frame으로 단정하지 않고 UNKNOWN stop합니다.

실패하면 후속 stage·corrective ON 없이 먼저 actual fresh state를 읽습니다. 마지막 nonterminal만
기존 config-only recovery로 처리하며 OFF recovery는 ON을 보내지 않습니다. Flow recovery는
baseline31로 되돌려도 Bar는 OFF일 수 있고 ON recovery는 다시 OFF를 보낼 수 있습니다.
recovery 실패는 상태 UNKNOWN이므로 terminal만으로 복원을 주장하지 않습니다. recovery 뒤
fresh 확인, 사용자 앱 복원, 재독 순서입니다. Bar classic91/TimerOFF/Frequency80/PulseTidefalse와
Pro first40/TimerOFF/Frequency34, second latent31/TimerON/Frequency32·그 시점 slot을 대조합니다.
diagnostic stderr를 보존·digest 검증하며 원인 미확정은 그대로 둡니다. direct 성공은 group D1u 원인
해소나 group PASS가 아니며 source·test·gate·timeout·retry·frozen ASYNC 변경 없이 기록을 먼저 닫습니다.

배포·readiness/postcheck 실패는 자료 보존과 candidate 정지 뒤 exact prior 복귀만 수행하고
device command0으로 종료합니다. prior 복귀도 실패하면 정지·보고하며 같은 승인으로 candidate
2차 배포를 재시도하지 않습니다. OFF 체류는 확인 절차 때문에 수십 초 규모일 수 있다는 추정과,
180초는 물리 ON 복귀 보장이 아닌 관측 상한이라는 점을 앱 준비 안내 때 함께 공개합니다.

### 2026-09-14 full-suite 실패 단일 테스트 수정 예외

repository maintainer는 full-suite 실패 두 건을 배포 전에 확인·처리하도록 요청했고,
에이전트가 재동결된 남은 실패 테스트 한 건만 수정하는 예외를 요청한 뒤
“ㅇㅇ 진행하자”로 명시 승인했습니다. 이 절은 그 예외를 별도 커밋으로 기록합니다.

허용 범위는 `tests/unit/test_hardware_test.py`의
`test_live_slave_failure_survives_masking_rollback_failure` 함수 내부뿐입니다.
이 테스트의 전체 80ms 기한과 monitor 진입 뒤 20ms 변경 예약이 경쟁하는 경로를
격리 simulator에서 진단하고, 기존 실패 주입·판정 단언을 유지한 채 실행 시점을 명시합니다.
해당 한 테스트에 대한 외부 격리 진단 script와 결과 문서화도 허용합니다.

forward 실패와 masking rollback 실패의 보존, primary failure 분류, durable evidence,
redaction 단언을 삭제하거나 완화하지 않습니다. 단순 반복 통과를 해결 근거로 쓰지 않으며,
강제 지연 재현을 원래 full-suite 실패 원인의 증명으로 올리지 않습니다.
native source, 다른 frozen 테스트·공용 fixture, 생산 시간 제한·안전 게이트는 변경하지 않습니다.
실장비 write·배포·재시험 권한은 포함하지 않습니다.

진단·해당 테스트 수정·검증 결과를 기록하고 변경을 커밋하면 이 예외는 자동 소진되어
해당 함수도 재동결됩니다. 범위 밖 수정이 필요하면 중단하고 별도 승인을 요청합니다.
전체 시험 검증 상태와 D1u 예외 원인은 서로 분리해 기록하고, 배포 보류는 유지합니다.

### D1w — 동일 group 순서의 진단 재시험 단회 승인

사용자는 D1u 재시험을 요청했고, (A) 진단 image 임시 배포1회와 exact prior 복귀,
(B) 세 대 OFF→Flow30→ON 시험1회와 필요한 마지막 nonterminal recovery·앱 복원을
각각 밝힌 질문에 “ㅇㅇ 진행하자”로 승인했습니다. 같은 질문에서 현재 수조 앞 감시와
실패 시 일부 또는 전부 OFF인 장비를 직접 켤 수 있는지도 확인했습니다.
기존 기록의 대상·순서·회계 이탈 미관측, 재실패 예외 확보의 가치, 재현·과거 원인 규명
미보장, 전부/일부 OFF 잔류와 recovery의 재-OFF 가능성을 먼저 공개했습니다.

승인은 [D1w 계획](docs/local-group-power-diagnostic-retest-plan.md)에만 결속합니다.
상시 배포, D1u/D1v 권한 재사용, native ASYNC 해제, source/test·timeout/retry/gate 변경은
포함하지 않습니다. candidate는 기존 diagnostic image, 임시 config delta는 Constant
default.power50→30 하나이며 member 순서 second→first→Bar·phase/gain/role은 유지합니다.
fresh baseline allON/TimerOFF/constant/independent/Flow31을 사용자 앱 준비 뒤 확인하기
전에는 device command를 보내지 않습니다. 앱 준비 전에 서비스 정지와 복원 baseline 보존이 필요합니다.

정상 OFF1은 action3/rev10/power3, 독립 fresh OFF31 확인 뒤 ON1은 action6/rev19/
Flow3+power3이며, ON 후 complete fresh cycle3까지 record2/Flow3/power6을 유지합니다.
처음 OFF intent부터 내부·host180초 관측 상한, 추가 record·attempt 상한 초과·잘못된
대상/값/순서·nonterminal failure·관측 오류·tree 이탈은 stop입니다. superseded는 UNKNOWN
stop이며 attempted를 실제 frame으로 단정하지 않습니다. stop은 물리 차단이나 자동 ON이 아닙니다.
첫 publish 전 runtime logs follow와 최종 logs를 보존하고 예외 event를 request digest/index로
journal과 대조합니다. event 부재나 단회 성공을 원인 규명으로 쓰지 않습니다.

실패 시 stop·실제 state와 journal 보존을 먼저 하고, 마지막 nonterminal에만 기존 config-only
recovery1회(최대6 frame)를 적용합니다. 그 뒤 fresh 확인과 사용자 앱 복원 순서이며 이전
VERIFIED record는 복구하지 않습니다. 긴급 사용자 복원이 우선되면 시험을 폐기하고 기존
baseline recovery를 자동 실행하지 않습니다. 재OFF·재ON·corrective direct ON·복구 retry는 금지합니다.
배포/readiness 실패는 command0 종료와 exact prior 복귀 검증만 허용하며 복귀 실패 시 정지·보고합니다.

사용자 앱의 first40/TimerOFF/F34, second latent31 뒤 TimerON/F32·그 시점 active slot,
Barclassic91/TimerOFF/F80/Pulsefalse 복원을 fresh로 확인하고 exact prior Constant50에
복귀합니다. 실행 image·generation·unarmed·fresh6·discovery90·unfinished0·새 journal과
기존 receipt 결속을 확인합니다. 기록 커밋으로 이 단회 권한은 소진되며 자동 반복하지 않습니다.

### 2026-09-14 — native Sync-only 준비의 한정 구현 해제

사용자는 native Sync/Async에서 slave TimerON의 파형별 Flow 확인을 우선 요청했고,
“관련 native 코드4개와 직접 테스트만 Sync 시험 준비 범위로 한정해 동결을 풀어도 될까요?
안전·복원 조건 유지, 실제 장비 시험 승인은 별도”라는 질문에 “ㅇㅇㅇ 진행하자”로 승인했습니다.
이 승인은 repository maintainer의 **구현·오프라인 검증 승인만**이며, on-site hardware
approver의 새 실기 승인이나 배포·앱 설정 승인이 아닙니다.

해제 사유는 [재개 준비](docs/native-sync-async-resumption-options.md)의 미확정 질문을
측정할 Sync-only 두 구간 경로를 준비하는 것입니다. 9월2일 Sync 중간 검사에서 보고된
inactive manual Mode/Frequency 차이를 정상 펌웨어 동작으로 단정하지 않습니다.
9월3일 미사용 종료 계획은 설계 참고일 뿐, 그 승인·stash·미검증 변경을 재사용하지 않습니다.

수정 가능한 source와 직접 테스트는 다음 여덟 파일뿐입니다.

- `src/jebao_flow/devices/schedule_flow_experiment.py`
- `src/jebao_flow/devices/schedule_linkage.py`
- `src/jebao_flow/schedule_flow_experiment_cli.py`
- `src/jebao_flow/hardware_test.py`
- `tests/unit/test_schedule_flow_experiment.py`
- `tests/unit/test_schedule_linkage_transaction.py`
- `tests/unit/test_schedule_flow_experiment_cli.py`
- `tests/unit/test_hardware_test.py`

별도 정책 커밋 뒤에만 위 파일의 Sync-only fixed 계획·역할 sequence·sample topology·
관측 결과·운영자 안내와 직접 검증을 수정합니다. identity·online/no-error·SwitchON·
TimerON·정확한 schedule·active cap·manual Flow exact/bounded·single-write·durable
journal/fsync·복구 권한은 유지합니다. inactive manual Mode/Frequency 차이는 해당
Sync 경로의 기존 bounded staged-control 비교와 보존 진단으로 다루며, 일반 Async 검사나
진단 enum 집합을 일괄 완화하지 않습니다. legacy record의 로드·복구 의미를 보존합니다.
새 firmware 가설 gate·retry·timeout 변경, 다른 frozen source·테스트 수정은 금지합니다.

검증은 코드의 순서·상태·복원 계약을 대상으로 하며 fake로 Sync/Async 펌웨어 답을 만들지
않습니다. 역할별 예상 출력은 가설로만 남기고 Sync 결과를 Async PASS로 올리지 않습니다.
구현·검증 결과와 독립 검토를 기록하고 변경을 커밋하면 이 해제는 자동 소진되어 재동결됩니다.
범위 밖 수정이 필요하면 중단하고 별도 승인을 요청합니다.

이 기간 device command·production 배포·앱 요청은 0으로 유지합니다. 실제 operation은
새 image/source 결속, current baseline·복구 수단 검증, 명령 회계·구간별 관측·중단/복원
절차 및 별도 실기 승인을 갖춘 뒤에만 가능하며 과거 D1u/D1v/D1w 권한을 재사용하지 않습니다.
현재 TimerON의 수용된 inactive manual 값과 active slot 출력도 서로 구분합니다.

### 2026-09-14 — 두 Pro Sync 관측·원복 단회 실행 해제

사용자는 두 Pro를 master·sync_slave로 묶고 서로 다른 슬롯 출력·전환 시각을 900초 관측한 뒤
원복하며 Bar와 Async를 제외한다는 설명에 “ㅇㅇ 계속 진행해줘”라고 지시했습니다.
이어 “장비 신원·기록 일치·출력 제한·복원 검사는 그대로 유지하고, 이번 시험 한 번만
만료 날짜 검사를 예외 처리하는 것까지 승인하실까요?”라는 질문에
“ㅇㅇㅇㅇ 그후 데이터 원복만 시켜주면되”라고 승인했습니다.

이 결정은 repository maintainer의 아래 단회 실행 해제 및 on-site hardware approver의
Sync 관측·원복과 지명된 native 자격 기록의 시간 유효성 예외 승인입니다.
`6ecb276`의 구현 해제는 이미 소진됐으며 새 승인은 그 권한을 재사용하지 않습니다.
구현은 변경하지 않고, 이 절을 별도 커밋한 뒤에만 잠긴 source/image의 실행을 준비합니다.

- 대상은 새로 identity를 확인한 Pro 두 대뿐이며 master/slave 배치를 실행 기록에 고정합니다.
  Bar·리턴·도징·Async write, 일반 배포, source/tests 수정은 승인 범위가 아닙니다.
- 임시 writer와 같은 source/image의 attended recovery 및 원래 서비스 복귀를 한 operation으로
  준비합니다. 설정·image·원복 수단을 먼저 보존하고 경쟁 writer를 배제합니다.
- 임시 safe manual은 master Constant30/F20·slave Constant31/F20이고, master는
  Sine35/F30→Constant30, slave는 Constant32→Sine40/F30, 경계는 T·T+5분입니다.
  임시 출력 상한41·관측900초를 지키며 준비와 원복 시간은900초에 포함되지 않습니다.
- 지정 native qualification 두 기록의 존재·operation·device_id·physical binding은 그대로
  확인하고 시간 유효성만 본 단회에서 제외합니다. 파일이나 날짜는 고치지 않으며
  local-control receipt로 대체하거나 새 qualification write를 자동 실행하지 않습니다.
- 시작 TimerON 요건 때문에 필요한 앱 준비는 현재 실제 출력과 전환 후 예상 slot을 따로
  공개하고 사용자 회신 뒤에만 진행합니다. second Pro의 수용된 inactive manual30은
  정상화하지 않습니다. 앱 전 baseline과 하네스 소유 baseline을 분리합니다.
- 하네스가 자기 시작 controls·원432-byte schedule을 durable하게 소유하고 ordered rollback을
  수행합니다. 앱 이전 상태를 standalone 복원하는 권한은 만들지 않으며, 앱 준비로 바뀐
  부분은 마지막 사용자 앱 복원까지 확인합니다. 원복 전 시험 완료로 보고하지 않습니다.
- sentinel 검사·복원도 실제 write에 포함합니다. 정상 경로의 논리 호출과 물리 frame·ACK를
  구분하며 실패 분기의 보상은 따로 계상합니다. 같은 전진 명령을 재전송하지 않습니다.
- identity·범위/step·single-write·durable journal/fsync·복구 권한은 유지합니다.
  실패 때 새 가설 gate·retry·timeout을 추가하거나 조건을 바꿔 재시험하지 않습니다.
- 기존 raw sink·진단·W0/W1/W2 판정 기준으로 관측하고 sample이 부족하면 UNKNOWN으로
  기록합니다. Sync의 OWN_SCHEDULE을 Async 성공이나 물리 유량 증명으로 올리지 않습니다.
- 중단 시 기존 ordered recovery를 우선하고 복원 미확인이나 구체적 위험을 즉시 보고합니다.
  서비스 stop·시간 상한은 물리 차단이나 자동 원복 보장이 아닙니다. 현장 개입 뒤 stale
  baseline으로 자동 복구하지 않습니다. 안전상 필요하면 사람이 중단 수단을 사용합니다.
- 앱 요청 전에 준비가 실패하면 device command0으로 종료하고 원 서비스 복귀를 확인합니다.
  원복도 실패하면 정지·보고하며 같은 승인으로 추가 배포·재시험하지 않습니다.
- 실기 결과와 원복·독립 검토를 append-only 기록으로 커밋하면 이 실행 해제는 자동 소진되고
  하네스는 다시 동결됩니다. 이후 Sync 반복·Async·상시 배포에는 새 승인이 필요합니다.

세부 절차는 [단회 계획](docs/native-sync-live-plan-2026-09-14.md)을 따릅니다.
이 절은 관련 source의 새 수정이나 기존 보호 검사의 완화를 허용하지 않습니다.

### 2026-09-14 — Sync 진입의 보존된 park 기록 판정 한정 수정

사용자는 검색 설정 정정 뒤 “ㅇㅇㅇ 시작하자”로 준비 재개를 지시했습니다. 이어 native 진입이
기존 park 기록의 존재 때문에 막혔다는 보고와 “그 기록을 삭제하지 않고 처리하도록, 진입 검사
수정·검증을 별도로 진행해도 될까요?”라는 질문에 “ㅇㅇㅇㅇ”라고 승인했습니다.
이 절은 그 repository maintainer 승인과 범위를 결속합니다. 실기 준비의 관측은
[준비 기록](docs/runs/2026-09-14-native-sync-preparation-admission.md)에 먼저 보존합니다.

이 절을 별도 커밋한 뒤 다음 범위만 수정·오프라인 검증합니다.

- `src/jebao_flow/schedule_flow_experiment_cli.py`: 이 CLI만 사용하는 진입 guard를 좁게 구성.
- `tests/unit/test_schedule_flow_experiment_cli.py` 및 신규 `tests/unit/test_schedule_flow_parked_admission.py`:
  기존 거부·원복 단언 보존과 admission 회귀 검증.
- 관련 결과·계획 문서. 그 밖의 source/test는 수정하지 않습니다.

공용 `hardware_guard.py`와 다른 CLI의 기존 차단 의미는 그대로 둡니다. 이 CLI의 guard만
기존 exact-restore journal reader와 archive의 `confirm_parked_tombstone`을 사용해 canonical
보류 기록·immutable finalization/disposition/snapshot 결속을 검증합니다. 파일 부재 또는 이
검증에 성공한 terminal park만 진행 중 복원과 구분합니다. 단순 phase 문자열·면제 플래그·
임의 callback으로 통과시키지 않습니다. 검증은 기존 전역 operation lock을 보유한 상태에서
수행하고, 모든 실패는 차단하며 journal·archive·영구 replay barrier를 삭제/변경하지 않습니다.
이는 liveness 판정이며 qualification 성공이나 같은 exact-restore operation의 재실행 권한이
아닙니다. 기존 reader의 owner/mode/inode/canonical 검사와 archive 검증을 재구현/완화하지 않습니다.
identity·출력 범위·single-write·fsync·원복 순서·현재 상태 재독·긴급중단 보호는 그대로입니다.
기존 Async·다른 native 하네스·local-control·schema 입력·timeout/retry 변경은 범위 밖입니다.

격리 검증은 real journal/archive fixture로 정상 보류·누락/변조·nonterminal·파일 안전성·
전역 lock·영구 replay 거부와 원본 불변을 확인하고, firmware 가설 fake를 만들지 않습니다.
수정·검증 결과 커밋으로 이 구현 예외는 자동 소진·재동결됩니다. 구현 중 device write·
앱 조작·배포는0이며, 새 image를 이용한 원래 단회 Sync 재개는 결과 공개·준비 재검증 뒤에만
진행합니다. 원래의 시간 만료 한정 승인·원복 의무·Async 제외는 바꾸지 않습니다.

### 2026-09-14 — 파형 주도권·슬레이브 Flow 세 구간 구현과 두 역할 실행

사용자는 "니가 하려는게 파형을 누가 주도하는가, 슬레이브의 플로우 변경이 가능한가"라고
목표를 바로잡고, 필요한 질문을 한 세션에 묶어 10분·15분에 변경하도록 요청했다.
에이전트가 "이 시나리오 구현에 필요한 하네스·직접 테스트의 한정 동결 해제까지
승인해 주실까요?"라고 물은 뒤 사용자는 "제발좀 한번에하라고 내가 승인했잔아 쫌 하자"로
재승인하고 "어떻게 동작하는지 잘 모니터링 해야해"라고 지시했다.
이 절은 해당 maintainer 구현 승인과 현장 두 역할 시험 요청을 구분해 결속하며,
같은 승인을 다시 질문하는 대신 정확한 범위와 실행 전제 확인을 수행한다.

직전 단회는 [field 준비 중단 기록](docs/runs/2026-09-14-native-sync-field-preparation-stop.md)에
먼저 커밋했다. 원예외 UNKNOWN·앱 이전 first TimerOFF·원 서비스 복귀 미완료는 그대로 남으며,
이 새 승인으로 이전 미완료를 성공 처리하지 않는다. 새 source 변경은 이 절의 별도 커밋 뒤에만 한다.

구현·직접 테스트 해제 범위는 다음 열 파일과 관련 계획·결과 문서뿐이다.

1. `src/jebao_flow/devices/schedule_flow_experiment.py`
2. `src/jebao_flow/devices/schedule_linkage.py`
3. `src/jebao_flow/devices/schedule_transaction.py`
4. `src/jebao_flow/schedule_flow_experiment_cli.py`
5. `src/jebao_flow/hardware_test.py`
6. `tests/unit/test_schedule_flow_experiment.py`
7. `tests/unit/test_schedule_linkage_transaction.py`
8. `tests/unit/test_schedule_transaction.py`
9. `tests/unit/test_schedule_flow_experiment_cli.py`
10. `tests/unit/test_hardware_test.py`

범위 사유는 기존 device당 한 경계·두 슬롯 가정을 슬레이브의 두 경계·세 슬롯으로 표현하고,
계획 식별·역할 선택·관측 구간·결과 분류·진입을 동일 계약으로 결속하기 위함이다.
temporary schedule의 두 슬롯 제한과 옛 opposing topology 허용 목록을 전역 삭제하지 않는다.
새 exact 계획의 세 슬롯만 명시적으로 인지하고 legacy·그 밖의 거부 의미는 보존한다.
`local_control/`, schema fingerprint 입력, 공용 guard, `devices/linkage.py`, 다른 CLI,
기존 timeout/retry·전송 정책은 수정하지 않는다. 추가 파일이 실제 필요하면 범위를 보고하고 멈춘다.

저장 계획은 master Sine35/F30→Constant32/F0(10분), slave Constant30/F0→Sine40/F30(10분)
→Sine35/F30(15분)이다. 임시 상한41·safe manual master30/slave31을 유지한다.
두 전환은 실행 전 fresh device clock으로 확정한 기준 시각에서 계산한다. 역할 진입·준비·원복
예산과 경계 제외·안정 표본 구간이 실제로 들어가는지 오프라인으로 검산한 뒤 절대 시각을 고정한다.
새 계획의 관측·표본 수 한도는 15분 경계 이후 유효 관측을 포함하도록 유한한 값으로 명시하고,
기존 전체 outer 1200초 계약을 넘기지 않는다. legacy 관측 예산은 바꾸지 않는다.

파형과 Flow는 독립적으로 분류하며 raw·ordinal·장비시각·취득 monotonic span을 보존한다.
10분에 Constant40/Sine40/Constant32/Sine32/이전값/기타를 구분하고, 15분에는 master32 유지와
slave 자기35·이전40·master32를 구분한다. protocol 보고값을 실제 임펠러 파형·위상·수류량으로
승격하지 않으며, 동시 전환을 master 시계의 내부 인과 증명으로 쓰지 않는다.
새 계획 구성·journal·복구·분류·legacy 호환성을 격리 테스트하며 펌웨어 가설 fake를 쓰지 않는다.
원예외 class chain과 stage를 비밀값 없이 남기는 진단은 위 CLI 범위에서 함께 보강할 수 있다.

구현 검증 후 한 현장 세션 안에서 Sync1회와 Anti-Sync1회, 합계 두 operation을 진행한다.
각각 새 intent·원본 snapshot·기존 ordered rollback을 소유한다. Anti-Sync 진입은 기존 검증된
역할 전환 경로를 사용하고 실제 logical 회계를 실행 전에 공개한다. 새 편의 write 경로를 만들지 않는다.
첫 회의 원복 실패·위험·기술적 실행 실패는 두 번째 자동 실행을 중단한다. 기능 가설의 부정적
관측 자체는 시험 실패와 구분하고, 원복이 확인된 경우에만 두 번째 역할로 넘어간다.
지명 native receipt의 시간 유효성 한정 예외는 이 두 operation에 명시적으로 한정하며
receipt 존재·operation·device·physical binding 검사와 파일 불변은 유지한다.
같은 구성의 추가 반복·자동 retry·새 qualification write는 포함하지 않는다.

새 실행 전 이전 앱 준비 상태와 복원 의무를 확인하고, 운영 서비스·원 시간표432B·controls·
source/image·journal·receipt를 결속한다. 현장 감시·중단 수단·현재 fresh identity는 유지한다.
앱이 필요한 마지막 원복은 사용자 소유이며, 그 전후 snapshot을 혼동하지 않는다.
구현 결과 커밋으로 위 source/test 예외는 소진·재동결된다. 실기 두 회와 원복·독립 검토를
각각 기록·커밋하면 실행 권한도 소진된다. 사용자 중지 지시는 어느 단계에서든 우선한다.

세부 관측표는 [통합 시나리오](docs/native-follow-wave-own-flow-scenario.md)를 따른다.

### 2026-09-15 — 마스터 시간표 상속·Flow 전용 차이 한정 재개

사용자는 마스터에 파형·Flow를 지정하고 슬레이브에는 해당 시간표 Flow만 지정하는 Sync
10분·15분 실기를 추가 요청한 뒤 "실기 테스트 까지 쭉 진행하고 보고 해줘"라고 지시했다.
에이전트가 "새 스케줄 지원과 종료 오류 진단에 한정해 관련 코드 4개·테스트 4개의 동결
해제도 승인해 주실까요?"라고 물었고 사용자는 "그냥 진행하라니깐"으로 승인했다.
이 절을 별도 커밋한 뒤 다음 여덟 파일만 구현·오프라인 검증을 위해 한정 해제한다.

1. `src/jebao_flow/devices/schedule_flow_experiment.py`
2. `src/jebao_flow/devices/schedule_linkage.py`
3. `src/jebao_flow/devices/schedule_transaction.py`
4. `src/jebao_flow/schedule_flow_experiment_cli.py`
5. `tests/unit/test_schedule_flow_experiment.py`
6. `tests/unit/test_schedule_linkage_transaction.py`
7. `tests/unit/test_schedule_transaction.py`
8. `tests/unit/test_schedule_flow_experiment_cli.py`

사유는 앞선 반대 파형·master2/slave3 슬롯 구성과 구분되는 **동일 master 시간표·양쪽3슬롯**
계획을 표현하고, 직전 종료 오류에서 사라진 원 예외를 보존하는 것이다.
master는 Sine35/F30 → Constant32/F0 → Sine38/F30, slave는 같은 시간·Mode·Frequency에서
Flow30→40→35만 다르게 한다. 10분·15분 경계와 상한41, safe manual30/31은 유지한다.
slave에 Mode 필드를 생략하는 것이 아니라 전체432B image에서 master와 다른 바이트를
세 슬롯의 Flow로만 한정한다. 새 계획은 별도 durable 표식으로 구분하고 legacy serialization·
기록 로드·확인 토큰·복원 의미를 유지한다. 기존 허용 집합을 전역 삭제하지 않는다.

진단은 `schedule_transaction`의 observer 수거·disarm completion에서 원 예외 class/frame과
분기 표식만 보존한다. 메시지·locals·비밀값·exc_info/extra는 금지하고 기존 예외 전파,
`from None`, 제어 흐름·retry·timeout·원복 순서·안전 게이트는 변경하지 않는다.
기능 가설 fake나 실제 출력 성공을 단정하는 테스트를 만들지 않는다.
`local_control/`, schema fingerprint 입력, 공용 guard, `devices/linkage.py`, `hardware_test.py`,
다른 CLI·직접 테스트는 범위 밖이다. 추가 파일 필요성이 나오면 멈추고 범위를 보고한다.
구현·검증 결과를 기록하고 커밋하면 이 여덟 파일은 자동 재동결된다.

실기 요청 범위는 새 구성 **Sync1회**이며 Anti-Sync나 성공할 때까지의 반복은 포함하지 않는다.
구현 결과·정확한 source/image·관측 항목·현재 현장 감시·fresh 신원·원 시간표/controls·
기존 원복 의무를 결속한 뒤에만 시작한다. 준비 실패나 새 위험이 있으면 재시도 없이 보고한다.
지명 native receipt의 시간 유효성 예외가 필요한 경우 이번 단회에 명시적으로 결속하며,
존재·operation·device·physical binding 확인과 receipt 원본 불변은 유지한다.
새 qualification이나 standalone 원복 권한으로 확대하지 않는다.

직전 Sync는 [실행 기록](docs/runs/2026-09-14-native-sync-wave-flow.md)에 먼저 커밋했다.
역할 해제 확인 오류·자동 창 자격 미달을 해결됐다고 쓰지 않는다. 새 스케줄은 그 오류의
해결책이 아니며 동일 오류가 나도 원인 정보를 남기도록 진단만 보강한다.
장비 날짜·같은 분·관측 안정 요건을 조용히 바꾸지 않는다. raw 보고와 실제 임펠러·수류를
구분하며 자동 판정 미달을 기능 부정이나 PASS로 바꾸지 않는다.
첫 Pro의 앱 이전 TimerOFF/manual constant40/F34와 원 서비스 복귀 의무는 별개로 유지한다.
현재 TimerON을 이어 쓰더라도 전후 snapshot을 혼동하지 않고 마지막 앱 원복은 사용자 소유다.
실행·원복·독립 검토를 기록 커밋하면 이번 실기 권한도 소진된다. 사용자 중지는 항상 우선한다.

### 2026-09-15 — 외부 시나리오 실행·응답 대조 이미지 한정 재개

사용자는 Sync/Async·파형·파형별 옵션·Flow·시간표를 외부에서 지정하고, 시나리오대로
반환값이 오는지 확인하는 재사용 이미지를 먼저 만들어 통합 시험하라고 요청했다.
"지금 뭐 할때마다 빌드하고 검증하고 시간이 너무 들어"라는 지적에 따라 시험값을 코드의
고정 서명으로 추가하는 방식을 중단한다. 이 요청은 아래 구현과 최초 통합 시험의 근거이며,
과거 단회 권한을 재사용하지 않는다. 이 절을 별도 커밋한 뒤 구현한다.

구현·오프라인 검증 범위는 다음 열두 파일과 대응 사용법·시나리오·결과 문서다.

1. `src/jebao_flow/devices/native_scenario.py` (신규: 순수 입력 계약·스케줄 컴파일)
2. `src/jebao_flow/devices/native_scenario_evidence.py` (신규: 순수 기대값/실측값 대조)
3. `src/jebao_flow/devices/schedule_flow_experiment.py`
4. `src/jebao_flow/devices/schedule_linkage.py`
5. `src/jebao_flow/devices/schedule_transaction.py`
6. `src/jebao_flow/schedule_flow_experiment_cli.py`
7. `tests/unit/test_native_scenario.py`
8. `tests/unit/test_native_scenario_evidence.py`
9. `tests/unit/test_schedule_flow_experiment.py`
10. `tests/unit/test_schedule_linkage_transaction.py`
11. `tests/unit/test_schedule_transaction.py`
12. `tests/unit/test_schedule_flow_experiment_cli.py`

JSON 시나리오는 실행 코드가 아닌 자료다. 임의 Python·셸 명령·원격 주소·비밀값을 받지
않는다. 역할, 슬롯, 파형별 옵션, Flow, 전환 시각, 명시적 기대 반환값을 검증하고 canonical
digest로 preflight·intent·journal·raw·결과를 결속한다. preflight 뒤 입력이 달라지면 기존
확인 토큰으로 실행하지 않는다. 날짜·분·표본 안정성의 기존 판정은 조용히 완화하지 않는다.
새 자료 기반 판정은 별도 계약으로 명시하고 입력 read-back·기능 응답·기술 종료·원복을
분리한다. 누락을 기대값으로 채우거나 시험 코드가 펌웨어 답을 만드는 fake는 금지한다.

기존 outer/temporary/role 트랜잭션의 identity·binding·cap/step·single-write·journal/fsync·
역순 복원·취소 shield를 그대로 사용한다. legacy spec의 직렬화·확인 토큰·recovery 의미를
보존한다. 고정 실험값은 새 시나리오에서만 자료로 대체하며 기존 허용 목록을 전역 삭제하지
않는다. 파형별 입력은 기존 Pro codec과 장비 capability가 표현하는 범위로 제한하고,
인코딩 가능성을 실기 검증 완료로 쓰지 않는다. 임시 Flow 상한41과 safe manual30/31을
유지한다. 공용 guard, `devices/linkage.py`, `local_control/`, protocol/schema 입력,
다른 CLI·timeout/retry·전송 정책은 범위 밖이다. 실제 추가 범위가 필요하면 보고한다.

직전 disarm 실패는 해결됐다고 쓰지 않는다. 이 범위에서 기대/실측 보호 필드와 실패 분기를
비밀값 없이 보존하는 진단을 보강할 수 있으나 안전 거부·예외 전파·원복 순서는 바꾸지
않는다. 새 master-only 실시간 schedule write 경로를 만들거나 detach/stage/relink를
"연동 중 자동 전파" 시험으로 이름만 바꾸는 것은 포함하지 않는다.

최초 통합 실기는 Pro pair에 Sync1회·Async1회, 최대 두 operation이다. 각 operation의
시나리오·현재 신원·원 시간표432B·controls·image/source·지명 native receipt를 실행 전에
결속한다. receipt 만료 한정 예외를 사용할 경우 이번 두 operation에만 결속하고 나머지
존재·operation·device·physical binding 검사는 유지한다. 새 qualification은 자동 실행하지
않는다. 각 회차는 자기 baseline을 복원하며 기술 실패·위험·원복 미확인 시 다음 회차는
자동 중단한다. 기능 가설과 다른 응답 자체는 보존할 결과이지 반복 명령의 근거가 아니다.
현장 감시와 사용자 중단 우선은 유지하되 같은 승인·현장 질문을 반복하지 않는다.

동일 이미지에서 자료만 바꾸는 것은 재빌드·전체 suite 반복 사유가 아니다. 매 자료의 순수
검증과 실행 전 fresh 확인은 남는다. 실행 로직이 바뀔 때만 이미지·코드 검증을 갱신한다.
구현 결과 커밋 시 source/test 예외는 재동결되고, 두 실기 결과·원복 기록 커밋으로 실행
권한을 소진한다. 기존 first Pro 앱 이전 TimerOFF/manual constant40/F34와 원 서비스 복귀
의무는 별도 유지하며 현재 TimerON을 원본으로 혼동하지 않는다.

### 2026-09-15 — Sync 종료 결과 보고 뒤 Async 한 회 재개

사용자는 `29078d3`의 Sync 결과(입력/응답 PASS, 종료 exit2, 사후 원432B/controls exact,
Async 미실행)를 안내받은 뒤 **"async 진행해"**라고 명시적으로 재개를 지시했다.
이는 이전 기술 실패 후 자동 중단을 없던 것으로 만드는 것이 아니라, 중단·보고 이후의
새 Async1회 승인이다. 같은 현장 질문을 반복하거나 이 지시를 앱 OFF 완료 회신으로 바꾸지 않는다.

이번 범위는 source `a4fcd98`·기존 재사용 이미지와 외부 `async-wave-and-own-flow` case를
그대로 사용한 Async1회다. source/test 수정·재빌드·전체 suite 반복·새 timeout/retry·새
장비 write 경로는 포함하지 않는다. 이 절을 별도 커밋하고 현재 신원·원432B·controls·
차단 journal 부재·source/image·scenario와 새 operation을 결속한 뒤 실행한다.
기존 parent 정책의 지명 native receipt 만료 한정 예외를 이번 Async1회에만 유지하며,
존재·operation·device·physical binding 및 나머지 안전 검사는 그대로 둔다.

직전 최초 role 오류와 composed disarm의 근본 원인은 UNKNOWN이며 재발할 수 있다.
이 위험을 해결됐다고 쓰지 않고 기존 복원·취소 shield·진단을 그대로 사용한다. 현재 복원
미확인이나 위험·실행 오류가 나오면 추가 전진/반복을 중단하고 자기 snapshot 복원을
확인·보고한다. 정상 경로 logical 예상23은 physical send 수나 성공 횟수가 아니다.
기능 기대와 다른 응답은 보존할 결과이고 성공할 때까지 반복할 권한은 아니다.

각 구간의 기대/실측·원본·세 창 response, 기술 종료, 원복을 각각 기록한다. Async PASS를
물리 역위상 계측이나 내부 파형/시계 소유 증명으로 올리지 않는다. 이번 시작 snapshot과
기존 first Pro 앱 이전 TimerOFF/manual constant40/F34·원 서비스 복귀 의무는 분리한다.
최종 앱 조작은 사용자 소유로 유지하며 불필요한 OFF→ON 왕복을 만들지 않는다.
실기 결과·원복 기록 커밋으로 이번 Async 단회 권한은 소진되고 source/test는 계속 동결이다.

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

## 2026-09-15 수류모터 제품 구현 범위

repository maintainer는 Bar 한 대·Pro 두 대의 앱 동등 개별 기능과 master 시간표·파형·부가값
상속 및 slave 슬롯별 Flow를 요청하고 “일단 계획서 만들고 검토한 후 바로 구현들어가자”라고 했다.
추가로 “sync랑 async 해제는 기존 값으로 덮어 씌워서 해제하는걸로 구현하자”라고 요청했다.
계획은 `docs/wavemaker-product-plan.md`이며 정적 계약 검토 후 제품 구현을 진행한다.

이번 허용은 `local_control/`의 `wavemaker.py`, `wavemaker_restore.py`, `coordinator.py`,
`device_writer.py`, `catalog.py`, `ha.py`, `mqtt.py`, `local_control_cli.py` 및 직접 테스트와
예시·제품 문서의 소프트웨어 구현·오프라인 검증이다. 기존 `local_control/schedule.py` compiler는
재사용하며 이 범위에서 수정하지 않는다. 범위 밖 필수 변경이 발견되면 근거를 보고한다.
§1 frozen native 여섯 파일과 그 직접 tests, `protocol/`, 공용 hardware guard, 옛 daemon/HA는
재동결 그대로다. 새 실기·배포·장비 초기화·상시 write 승인이 아니다.

identity·range/step·single-write·journal/fsync·qualification·복구 권한은 유지한다.
Bar의 384-byte 시간표·반환값 소프트웨어 구현을 실제 복원 자격으로 올리지 않는다.
Bar timer의 미확정 앱 coupling을 Pro bool 명령으로 대체하지 않는다. `device_writer.py` 수정은
schema digest 변경을 수반하므로 기존 receipt는 새 내용에 자동 유효하지 않다. fingerprint 계산을
바꿔 재qualification을 회피하지 않는다.

해제는 연동 전 자기 snapshot을 사용하는 명시적 새 작업이다. 기존 `VERIFIED` 기록에 대한
`recover` 의미를 바꾸거나 과거 성공을 현재 상태라고 출판하지 않는다. 시간표 overwrite만으로
역할이 해제됐다고 판정하지 않으며 원본 image·독립 역할·제어값의 실제 반환값을 비교한다.
오류 시 자동 반복·timeout 증가·Sync 재점화·factory reset은 포함하지 않는다.
이전 first Pro 최종 앱 OFF·서비스 복귀 의무는 새 제품 작업과 별개로 유지한다.
구현 단계별 결과를 문서화하고, 완료 커밋으로 그 구현 범위를 소진한다. 미완료 단계는 명시한다.

## 2026-09-15 수류모터 native lifecycle 실행기 구현 재개

repository maintainer는 1차 구현의 미완료 범위를 보고받은 뒤
“실제 Sync/Async 결속·해제 실행기는 아직 미완료예요--- 이것도 완료 해야하는데”라고 요청했다.
이는 실제 실행기의 소프트웨어 구현·오프라인 검증 재개이며 새 실기·배포 승인이 아니다.
상세 계약은 `docs/wavemaker-lifecycle-plan.md`다.

범위는 `local_control/journal.py`, `coordinator.py`, `device_writer.py`,
`wavemaker.py`, `wavemaker_restore.py`, 새 `wavemaker_lifecycle.py`, `local_control_cli.py`와
직접 tests·예시·문서다. journal 변경은 기존 root 안의 소유 원본·단계·증거 결속이며 별도 safety
namespace를 만들지 않는다. legacy 직렬화·일반 recovery·power-off 정책은 유지한다.
native 작업만 일반 reverse rollback과 구분하고, 역할 해제와 TimerOFF·safe 제어를 atomic frame으로
실행한 뒤 원 image·원 controls 순서로 복원한다. 해제 실패 후 재결속·같은 frame 자동 재시도는 없다.
성공한 attach 기록은 undo로 재실행하지 않고, 자기 원본에 결속된 새 detach 작업을 기록한다.
신원·출력 범위/step·단일 전송·fsync·원복 권한·현재 qualification 결속은 유지한다.
reply/report를 수용하는 native 전용 읽기는 action·raw·시각을 보존하며 기존 읽기 정책은 바꾸지 않는다.
§1 frozen native·직접 tests, `protocol/`, 공용 guard, 기존 HA/MQTT/서비스는 변경하지 않는다.
schema fingerprint 변경을 숨기거나 기존 자격으로 새 구현이 검증됐다고 쓰지 않는다.
완료 커밋으로 이번 구현 범위를 소진하고 실장 자격·배포·이전 원복 의무는 별도 상태로 남긴다.

## 2026-09-15 제품 배포를 위한 새 실장 자격

사용자는 `708d761`의 배포 중단 보고(기존 6개 제어의 schema 불일치와 신규 native의 자격 부재)를
받고, “새 버전으로 실제 장비 검증·자격 갱신 … 설정 변경 후 원복하는 이 검증까지 포함”하는
요청에 “ㅇㅇ 진행해줘”라고 승인했다. 구현·실장 검증·자격 발급·기존 기능을 보존한 배포를
진행하며 동일 현장 질문을 반복하지 않는다. 이를 앱 OFF 완료 발언으로 각색하지 않는다.

새 7종 qualification producer 부재를 메우는 범위는 새 `local_control/pro_qualification.py`와
그 CLI, `pyproject.toml` entry point, 직접 tests·예시·문서다. 기존 NativeWavemakerController의
원본 소유·순서·journal·raw capture·no-resend·전용 복구를 재사용한다. `device_writer.py`는
관측된 inactive FeedTime=0의 선택 필드 rollback을 원본 bytes로 만드는 변경에 한정한다.
현재 값의 encode 범위를 낮추거나 원본 0을 새 입력값으로 허용하지 않는다. 이 변경의 최종
schema에 모든 새 자격을 결속하며 fingerprint 계산과 기존 영수증은 변경하지 않는다.

Pro 두 대의 9종은 각 기능의 서로 다른 두 실제 명령과 ACK·반환값이 있는 경우만 발급한다.
전체 432B 시간표 두 개, Timer/Switch 결합, master/independent 역할 전환을 포함한다.
compound native 명령을 scalar 자격으로 바꾸어 세지 않는다. Flow 준비/시험은 39·40,
주파수 20·21과 짧은 부가값을 사용하며 상세 순서는 `docs/wavemaker-qualification-plan.md`다.
자기 시작 원본의 최종 전체 image·8개 controls 복원이 확인되기 전에는 자격을 발급하지 않는다.
Bar는 기존 manual_flow/power producer만 사용하며 native·시간표 write를 하지 않는다.
새 qualification은 후보로 격리하고, 기존 제어 6개와 필요한 native 자격의 보존/확인이 끝나기
전 운영 generation을 교체하지 않는다. 새 Sync·Async 실제 결속/해제는 각각 한 번이며,
전 회차 원복과 자격 확인 후 다음 회차로 간다. 전진 실패 시 자동 재시험하지 않는다.

§1 frozen 여섯 파일·직접 tests, protocol, 공용 guard, transport timeout/retry는 불변이다.
실기 결과는 source 수정 전에 append-only 커밋한다. 실패·미확인·중단 요청 시 전진을 멈추고
자기 원본 복원 또는 증거가 남는 미확인 정지로 끝낸다. 초기화·무인 반복은 포함하지 않는다.
이전 앱 OFF/원 서비스 복귀 의무는 별도 유지한다. 결과 기록으로 이번 실행 권한을 소진한다.

## 2026-09-15 자격 시험값·ACK 계약 정정 후 배포 재개

사용자는 `f50a63a`의 실기 중단·원본 복원, app 최소5 미만 custom 시험값1·2 및
빈 ACK body를 거부하는 별도 발급 gate를 보고받고 “될때까지 수정하고 배포해”라고 요청했다.
이는 보고된 결함을 수정·검증하고 실제 자격·제품 Sync/Async 결속/해제·배포를 이어가라는 승인이다.
동일 현장 확인 질문을 반복하지 않으며, 사용자 발언을 최종 앱 원복 완료로 각색하지 않는다.

첫 정정 범위는 `local_control/pro_qualification.py`와 그 직접 test,
`docs/wavemaker-qualification-plan.md` 및 구현/실기 기록이다. 기존 app 범위 helper로 부가값을
선정하고 command·sequence 검증 뒤 빈 body도 정상 ACK가 될 수 있다는 기존 계약을 따른다.
ACK digest 부재·blob 손상·실제 전송/after raw 부재·원복 미확인 시 발급 금지는 그대로다.
writer·protocol·schema fingerprint·기존 receipt·transport timeout/retry는 변경하지 않는다.
이 문서의 별도 커밋 뒤 구현한다. source 변경이 필요할 때마다 앞선 실기 결과부터 커밋한다.

새 Pro 단회는 별도 operation과 후보 산출물로 실행하며 이전 실패 id를 재전송하지 않는다.
완료 후 Bar 기존 두 producer, Sync/Async 제품 lifecycle 각1회와 기존6개 제어 보존 배포를 잇는다.
실패 시 해당 전진을 멈추고 자기 원본 복원·실제 증거·원인을 먼저 확인한다. 이 승인은 같은 실패
명령의 자동 재시도·근거 없는 값 탐색·factory reset·안전 게이트 완화가 아니다. 증거로 확인한
같은 제품화 결함의 정정과 재검증은 결과를 보고하며 계속하고, 다른 장비/권한으로 확대하지 않는다.
Bar bootstrap/native/schedule write, 무인 시험, 상시 group arming은 포함하지 않는다.
§1 frozen native 여섯 파일과 직접 tests는 해제하지 않는다. 재개 판단과 실제 결과를 매번 분리해
기록하며 기능 응답·자격 발급·원복·운영 서비스 교체를 각각 확인하기 전 완료로 올리지 않는다.

## 2026-09-15 제품 Sync 미완료 해제의 소유 복구·배포 재개

사용자는 `31dc0a0`의 ACK timeout·재전송 없는 복구 정지와 별도 복구 절차 제안을 보고받고
“ㅇㅇㅇㅇ 진행해”, “끝까지 진행시켜서 배포 완료해”라고 승인했다. 기존 미완료 detach를 지우거나
같은 atomic 명령을 재전송하지 않고, 자기 attach 원본에 결속된 별도 scalar 지원 작업으로
복구·검증·배포를 이어간다. 상세 계약은 `docs/wavemaker-owned-detach-assistance.md`다.

범위는 `local_control/wavemaker_lifecycle.py`, `local_control_cli.py`, 두 직접 test 파일과
관련 계획·구현·실기 문서다. 기존 journal/lease에 결정적 지원 id와 원본/실패 기록의 결속을
보존한다. writer·schema 입력9개·기존 자격·일반 coordinator/guard·transport timeout/retry·
§1 frozen 파일/직접 tests는 불변이다. 이 별도 정책 커밋 뒤 구현하며 결과 기록을 먼저 보존한다.

단회 실장은 자기 Pro pair의 첫 slave atomic 해제가 UNCERTAIN이고 나머지5단계가 미시도인
경우에 한한다. master independent → slave 앱 schedule_enable=False → slave constant·준비
Flow·Frequency라는 최대5개 새 scalar 단계다. 모두 기존 실장 자격을 요구하며 실제 일치 단계는
전송하지 않는다. master 먼저 해제하는 순서 변경과 TimerOFF 중 수동 운전은 명시적 범위다.
성공을 가정하지 않으며 시도한 scalar도 재전송하지 않는다. 기존 실패 payload는 재사용하지 않는다.
준비 상태가 실제 확인되면 기존 UNCERTAIN은 fresh 일치로만 해소하고 자기 원432B·8controls를
기존 나머지 단계로 복원한다. 다른 미완료 기록은 허용하지 않고 generic recover는 계속 거부한다.

실패 시 증거·원본·미완료 상태를 유지하고 자동 대안 반복하지 않는다. 사용자에게 같은 현장
질문을 반복하지 않는다. 이전 first 앱 OFF·Bar 앱 준비 이전 복귀·원 서비스 의무를 흡수하지
않으며 새 운영 generation은 원 서비스의 동일 복귀가 아니라 대체임을 기록한다. 복원 확인 후
남은 제품 Async 단회와 기존6개 제어 보존 배포를 잇는다. 초기화·상시 group arming은 없다.

## 2026-09-15 실패한 제품 Async의 자기 원본 복구

`4c083af`로 마지막 Async 전환과 자동 detach의 ACK timeout을 먼저 기록했다. 사용자의
“끝까지 진행시켜서 배포 완료해” 지시에 따라, 성공 attach에만 묶인 소유 지원 진입을 이번
실패 형태에 한해 정정한다. 소유는 성공 여부가 아니라 첫 write 이전 reply 원본에 있다.
범위는 `local_control/wavemaker_lifecycle.py`와 직접 test, 관련 문서다. 별도 정책 커밋 뒤
구현한다. writer·schema9개·40receipt·공용 guard·timeout/retry·frozen native는 변경하지 않는다.

추가 대상은 Pro pair/Async/source RECOVERY_REQUIRED, action7개 중 앞6 VERIFIED·마지막
slave async role만 UNCERTAIN인 형태다. child 첫 slave atomic UNCERTAIN/나머지5 PREPARED,
자기 attach/child/assist 세 id·원본·qualification·동일5scalar·no-resend·최종 reply 전체
432B/8controls 조건은 유지한다. 복구 완료는 원 Async action의 전달 성공을 뜻하지 않는다.
그 action의 ACK 부재·증거를 보존하고 전달 UNKNOWN을 별도 진단으로 남긴다.

이번 작업은 미완료 자기 원복 의무의 수습이며 새로운 Async 전진 시험이 아니다. 제품 결속/
해제 질문은 Sync/Async 두 실기 뒤 §7 보고 대상이고, 수습과 제품 재시험 횟수를 섞지 않는다.
사용자는 추가로 “sync에서 async로 바로가면 안될꺼야 무조건 원복했다가 다시 async로
가야 할거야”라고 했다. 앞선 Sync 원복은 실제 완료됐지만 새 Async 내부에도 Sync 경유가
있었다. 이를 원인 확정으로 삼지 않고, 현재 원복 뒤 독립→Async 직접 진입 설계를 검토한다.
이번 복구 정정에는 새 Async 재시험·Sync 재점화·factory reset을 포함하지 않는다.

## 2026-09-15 제품 Async 역할 단독 전환의 제한된 재개

사용자는 아까 응답 PASS인 시험과 현재 제품의 실제 코드/명령 차이를 보고받고
“이어서 계속 진행하자 async 될때까지”라고 요청했다. 이전 성공 시험도 원복 뒤 내부 Sync를
거쳤으며 제품과 달리 Async에서 Linkage만 썼다. `c8d116c`에 현재 자기 원복 완료를 먼저 기록했다.
§7의 제품 실기2회 보고 뒤 새 승인에 따른 세 번째 제품 단회이며, 무기한 동일 명령 반복은 아니다.
현재 사용자 요청을 성공했던 순서와 전송 차이를 좁혀 재개하는 것으로 해석해 먼저 공개한다.

별도 정책 커밋 뒤 `local_control/wavemaker_lifecycle.py`와 직접 test·관련 문서만 정정한다.
마지막 Async 전환을 Linkage 단독으로 바꾸고, 직전 양쪽 fresh session의 실제 상태를 보존한다.
추가 기대값 게이트·ACK 면제·timeout/read retry 증가·동결 하네스 재사용은 없다. 기존 source
7단계/앞6 VERIFIED/마지막만 UNCERTAIN인 소유 복구는 원3필드 또는 새Linkage단독 expected만
허용한다. 나머지 소유/child/단일전송/원복 검사는 그대로이며 schema·writer·40receipt 불변이다.

실장1회는 원복 확인 뒤 동일 Pro pair·동일 시간표의 새 operation으로 실행한다. 원복→Sync
준비→Linkage단독 Async이며, 앞선 실패한 id/payload를 재전송하지 않는다. 전환 응답·전체image
확인과 자기 원복까지 범위이며 시간표10/15분 경계 전체 재검증이나 위상 계측으로 주장하지 않는다.
오류시 전진 중지·기존 자기 원복/소유 지원·기록을 먼저 수행한다. 같은질문 누적3회 및 새승인
단회 결과를 분리하고, 새증거가 없으면 임의대안/값탐색/timeout확대 없이 보고한다.
사용자의 최종 목적은 동작과 배포이며, 개별기능 자격·Async 수용·해제·운영generation을 각각
검증한다. factory reset·상시group arming·first/Bar 앱이전원복 의무의 흡수는 없다.

## 2026-09-15 Async 연결 유지·ACK 부재의 상태 확인 후 배포

사용자는 누적3회 결과와 보존 원복 raw의 Async 보고 재발견을 보고받고 “수정해서 배포해”라고
요청했다. 원인 미확정과 §7 보고 뒤의 새 수정·단회 검증·배포 승인으로 기록한다. 과거 성공이나
이번 보고를 원 명령 ACK로 바꾸지 않는다. 별도 정책 커밋 뒤 `local_control/wavemaker_lifecycle.py`,
`local_control_cli.py`(공개 결과가 필요한 경우만), 직접 tests와 관련 문서만 수정한다.

마지막 Async 직전 fresh pair read에 사용한 연결을 그 역할 단독 write까지 유지하여 현재의
관측 close→재연결→중복 BEFORE 읽기를 없앤다. pair 읽기는 관측이며 Auto/시각 고정 게이트가 아니다.
마지막 Async send_once의 통신 오류 뒤에만 원 오류·UNCERTAIN·ACK 부재를 먼저 내구화하고,
기존 writer를 닫은 뒤 새 연결의 reply-only 읽기1회로 목표 Linkage를 확인한다. 재전송은0이다.
일치하면 ACK 성공과 구분되는 상태 확인 진단을 남긴다. 불일치·읽기 실패·취소는 성공 처리하지
않고 기존 자기 원복으로 간다. report 단독으로 이 ACK 부재를 해소하지 않는다. 명령 전달의
인과는 UNKNOWN으로 두고 새로운 직접 응답의 목표 상태 확인만 주장한다. 최종 전체 image와
명시 제어 대조는 유지한다. 다른 단계·qualification의 실제 ACK 요구는 불변이다.

writer/schema9개·40receipt·공용 coordinator/guard·protocol timeout/retry·frozen native6개와
직접 tests는 불변이다. 새 firmware fake·재전송·원본 외 복구·factory reset은 없다. 연결 변경과
후속 상태 확인을 함께 도입하므로 성공을 하나의 원인 규명으로 귀속하지 않는다.
기존 시험 단언의 변경은 마지막 Async ACK 부재에서 직접 상태가 확인되는 새 계약에만 한정한다.

실장은 동일 Pro pair의 원복 상태·source/image/자격을 결속한 새 operation1회다(제품 누적4회).
전진 실패 때는 원본 보존·known scalar assist를 포함한 자기 복원을 먼저 끝내고 보고한다.
동일 명령 자동 반복이나 추가 다섯 번째 전진은 포함하지 않는다. 소프트웨어·수용/원복 검증 후
기존6개 제어가 포함된 새 자격으로 운영 generation을 교체·확인한다. startup 장비 write0,
상시 group 비무장과 설치 실패 시 exact prior 서비스 rollback을 유지한다. 새 서비스는 중단된
원 서비스의 동일 복귀가 아니라 대체다. first 앱OFF·Bar 앱이전값 복귀 의무는 별도로 남긴다.
같은 승인/현장 질문을 반복하지 않으며 이를 앱 완료·물리 안전 선언으로 각색하지 않는다.
각 결과를 기록하고 구현/실기 단회 권한을 소진한다. 배포·기능수용·원복 완료를 서로 대체하지 않는다.

새 상태 확인 뒤 final 대조가 실패한 source도 자기 원복이 가능해야 한다. 소유 지원의 기존
Async7단계/앞6 VERIFIED/마지막 exact payload/원본/child 전제는 유지하면서, 마지막이
ACKNone·reply확인 전용 진단·VERIFIED인 경우도 포함한다. 원 명령 전달 UNKNOWN은 유지한다.

## 2026-09-15 성공했던 단계별 활성화 절차로 제품 재검증

`9c02764`에 제품 누적4회 실패와 자기 원복 완료를 기록·보고한 뒤, 사용자가 “야 확인하고 다시
수정 배포해 뭐가 잘 못되었는지 ?? 성공했던 절차대로 진행해봐”라고 새로 지시했다. 이는 §7
보고 뒤 성공 경로와의 실제 차이를 대조하고 수정·단회 재검증·배포하는 새 범위다. 앞선 실행이
성공 절차 전체의 재현이었다고 쓰지 않는다. 누적 횟수를 초기화하지 않으며 이번은 제품5회차다.

먼저 `a4fcd98` 성공 하네스의 명령 mapping·호출 순서·연결 교체·읽기·간격을 정적으로 대조한다.
제품의 TimerON+역할 복합 쓰기를 independent TimerON 두 단계와 master/Sync/Async 역할 단독
쓰기로 분리한다. 기존 준비·image와 합해 Async9개/Sync8개 action이며 첫 전송 전에 전량
내구화한다. 양쪽 연결 준비 완료 뒤 pair read, 그 연결에서 다음 역할 write, 기존 성공 adapter의
명령 간격1초·ACK 후 읽기 간격0.5초/최대3회 및 역할 전 read-only settle1초를 명시해 적용한다.
이는 해당 제품 절차의 정상 readback을 기존 성공 경로에 맞추는 변경이지 shared transport의
timeout·제어 재전송 변경이 아니다. ACK 오류 뒤 직접reply1회·원 전달UNKNOWN 경계는 유지한다.

source 범위는 `local_control/wavemaker_lifecycle.py`, `wavemaker.py`, 필요한 native 전용
연결/순서 모듈과 직접 tests·문서다. 기존 writer의 payload·single-send를 재사용하며, 양쪽 인증
완료를 기다리는 native 읽기 표면만 따로 구성한다. `device_writer.py`와 schema9개·40receipt·
일반 coordinator/guard·protocol·frozen native6개는 불변이다. 새 prepare Flow 지정은 기존
모델 범위·qualification 검사 안에서 master30/slave31/F20을 명시할 수 있게 하며 과거 실기
상수를 숨은 제품 기본값으로 승계하지 않는다. 기본 공통 준비값은 그대로다.

신규 context에 절차 버전을 결속하고 옛 context는 새 단계를 재해석하지 않는다. 기존6개 child
복원과5개 scalar assist는 자기 원본·identity·qualification·3-id·no-resend를 유지한다. 새9단계의
slave 역할 전송 실패/ACK 없는 상태확인 뒤 final 실패도 정확한 source 형상을 대조해 자기
원복이 막히지 않게 한다. 원복은 전달 성공의 증명이 아니며 UNKNOWN 진단을 계속 보존한다.
읽기 중 관측되는 frame은 기존 진단 observer로 수집하되 판정/timeout에 개입하지 않는다.

이번 동일화 대상은 제품의 정상 활성화 명령·연결·읽기 절차다. 이미 current인 자격을 얻기 위한
옛 sentinel 쓰기, 동결 하네스의 날짜/Auto/같은 분 게이트·10/15분 epoch는 되살리지 않는다.
현재 선언 시간표의 값·절대시각과 과거 시험 조건도 동일하다고 주장하지 않는다. 명령순서와
펌웨어 원인의 동일성은 별개다. mock은 byte-copy·fault injection과 순서 검사뿐이다.

최종 source/image/자격·자기 원복 상태를 결속한 Pro pair 신규 operation1회만 실행한다.
실패하면 기존 자기 원복/소유 지원과 증거 보존을 먼저 완료하고 보고한다. 동일payload 재전송·
임의 대안 반복·여섯 번째 전진은 이번 범위가 아니다. 수용·원복 확인 뒤 기존6개 제어를 보존해
운영 generation 교체와 startup write0·group 비무장·상태 출판을 검증한다. 배포 실패 시 exact
prior 서비스 rollback, first 앱OFF·Bar 앱이전값 복귀 별도 의무를 유지한다. 동일 현장 질문을
반복하지 않으며 사용자 발언을 새 앱 완료나 물리 안전 선언으로 바꾸지 않는다.

구현 범위의 새 모듈은 `local_control/wavemaker_sequence.py`로 고정한다. native 읽기 subclass는
`send_once`·`prepare_*`·payload 구성을 override하지 않는다. ACK 후 최대3회 읽기에서 transport
실패가 난 stream은 기존대로 quarantine/close한다. 남은 읽기만 새 writer에서 재인증하고,
제어는 재전송하지 않는다. 역할 write 뒤에는 양쪽 새 연결의 pair read를 다시 완료하고 그
연결로 다음 역할을 쓴다. 따라서 읽기 실패 stream을 재사용하거나 최종 역할 직전에 별도로
연결을 바꾸는 방식이 아니다. 취소는 읽기 재시도 대상이 아니다.

단계 위치가 고정된 새 실행은 Pro pair에 한정한다. 소유 지원은 신규 절차 버전에 결속된
Sync8개/Async9개 전체 target·function·expected를 대조하며, slave Sync/Async 단계 또는 완료
action 뒤 pair/final 대조 실패만 추가로 받는다. 이전 context의7개 형상은 그대로다. 앞선
준비/마스터 실패까지 임의로 확대하지 않는다. 이 명시는 이미 승인된 단계 분리에 따른
도달 가능 상태와 연결 형상을 고정하며, 추가 전진 실기나 공유 writer 변경 권한은 아니다.

## 2026-09-16 사용자 직접 관찰을 위한 Async 전환 후 원복 보류

`3248641`에 제품5회차의 실패·자기 원복·검색 결과를 기록한 뒤 사용자가 “그럼 async 전환하고
기다려봐 내그 변했는지 안변했는지 실제로 볼께”라고 요청했다. 에이전트의 최대2분 후 자동
원복 제안에 “아니 걍 원복하지말고 기다리게 해”라고 명시했다. 이는 §7 보고 뒤의 신규 단회이며
누적 제품6회차다. 현장 질문을 반복하지 않고, 사용자 관찰을 앱 조작 완료나 물리 안전 증명으로
각색하지 않는다. 이 실행은 전달 성공을 억지로 판정하는 재시험이 아니라 사용자 관찰 창을 남긴다.

별도 정책 커밋 뒤 `local_control/wavemaker_lifecycle.py`, `local_control_cli.py`와 직접 tests·
관련 문서만 수정한다. 명시적 관찰 옵션을 immutable context와 request intent에 결속하며
옵션 없는 실행·옛 context의 자동 복구 의미는 불변이다. 새 Async9단계의 마지막 slave 역할
명령이 시도된 뒤에만 자동 detach를 하지 않고 자기 원본과 미완료 기록을 유지한다. 앞선 단계
실패·마지막 명령 미전송은 기존 복구로 처리한다. 이는 관찰 중 즉시 원복하지 말라는 사용자
지시에 한정한 복구 시점 변경이며, 원복 권한·원본·journal·identity 검사의 삭제가 아니다.

동일 Pro pair·기존 시간표·준비값으로 한 번 실행한다. ACK 부재·UNCERTAIN·원 오류는 보존하고
관찰 대기를 PASS 또는 원복 완료로 바꾸지 않는다. 모든 action이 검증돼도 관찰 대기 record는
미완료로 유지해 다른 write가 자동으로 끼어들지 못하게 한다. 명령 재전송·시간 제한에 의한
자동 원복·자동 재시험·배포·Bar write는 하지 않는다. 반환/재실행/프로세스 종료가 자동 원복을
시작하지 않으며 동일 요청 재조회는 장비 무접촉이다. 사용자의 후속 원복 지시 때만 기존 자기
detach/assist를 실행할 수 있고, 관찰 완료 발언만으로 원복 지시를 추정하지 않는다.

현재 미확인 역할·시험 시간표가 사용자 결정까지 남는다는 사실, 명령 ACK와 직접 응답의 결과,
보존 원본 및 명시적 다음 복구 명령을 공개한다. 전환 후 사용자에게 즉시 알리고 채팅 입력을
막지 않는다. frozen native6개·writer/schema9개·40receipt·공용 guard·timeout/retry는 불변이다.
구체적 물리 위험이 관측되면 즉시 보고하며 사람이 중단 수단을 행사한다. 이전 first 앱OFF·
Bar 앱이전값 복귀·원서비스 의무는 별도 유지하며 이 관찰로 배포나 원복 완료를 주장하지 않는다.

### 2026-09-16 원복 뒤 기존 Sync→Async 관찰 단회 재개

`270ed1d`에 앱 조작 관측과 제품6회차 자기 원복을 먼저 기록했다. 사용자는 공장 초기화 대신
원복을 지시했고, 원복 완료 뒤 잠시 정지했다가 “일단 원래대러 async 시도해봐”, “그리고
확인해봥”이라고 새로 지시했다. 이는 기존9860107 이미지·동일 Pro pair/plan의 staged9단계
Sync→Async를 새 operation1회 실행해 관찰 보류하는 범위다. 직접 Async용 source/절차 변경과
공장 초기화는 하지 않는다. 제품 계열 누적7회이며 좁은 질문으로 누적 횟수를 리셋하지 않는다.

직전 source ROLLED_BACK·child VERIFIED/원432B/8controls exact 뒤 새 원본을 포착한다.
기존 --await-operator-observation을 유지해 마지막 시도 뒤 자동 원복·재전송을 하지 않는다.
사용자가 제안한 master 링크 해제→slave 보고 확인→master 링크 재설정은 이번 첫 결속과
별도 단계이며 여기서 자동 실행하지 않는다. 첫 결속 결과와 원본 보고를 먼저 알린다.
이 단계가 다시 ACK timeout이면 원인 규명이나 실패 해소로 세지 않는다. source/test·writer·
schema9·40receipt·shared timeout/retry·frozen6은 전부 불변이며 새 배포·Bar write도0이다.

## 2026-09-16 Pro 두 대의 사용자 일일 시간표와 선택적 master 링크 cycle

`17beba0`에 일곱 번째 전환과 사용자 앱 master 해제/재설정 뒤 Async 직접 응답을 기록했다.
사용자는 이 절차를 옵션으로 구현·빌드·테스트하고, 위 Pro를 master로 바꿔 제시한 16구간
시간표로 실제 Async 운영하라고 명시했다. Bar는 후속 지시로 제외했다. 질문을 반복하지 않고
선택 가능한 두 모드 중 circulation을 사용한다. 현재 보류 source는 먼저 기존 자기 원본으로
복원하고 새 역할 배치의 independent baseline에서 새 operation을 시작한다. 공장 초기화는 없다.

별도 정책 커밋 뒤 lifecycle·CLI·새 `local_control/wavemaker_master_cycle.py`·직접 tests·
시간표 예시·문서를 수정한다. 기존 staged9와 원복6/지원5의 payload는 불변이다. 명시 옵션만
마지막 Async 시도 후 실패에 연결된다. 별도 결정적 cycle id/kind로 master Linkage independent,
master 두 scalar를 전량 내구화한 뒤 최대 한 번씩 전송한다. 기존 scalar qualification과 owned
payload 동일성·신원·lease·journal/fsync를 재사용하며 slave Async를 재전송하지 않는다.

cycle은 source 원본 context에 결속되지만 원 action의 UNCERTAIN/ACK/진단을 수정하지 않는다.
해제 뒤 fresh reply에서 slave Async와 전체 image를 확인하고 재설정 뒤 역할/image를 관측한다.
최종 report 수용 여부와 출처를 공개하고 상태 도달을 원 명령 전달 증명으로 승격하지 않는다.
실패/취소는 자동 원복이나 새 id 반복 없이 보존·정지한다. cycle의 미완료도 자기 detach/assist를
막지 않도록 정확히 그 attach에서 파생한 id만 허용하고 원복 완료 때 cycle의 복구 회계도 닫는다.
동일 id 조회/완료 replay는 장비 무접촉이며 source 자체가 미완료이면 일반 전역 차단은 유지한다.

writer/schema9·40receipt·공용 guard/protocol·frozen6은 수정하지 않는다. 새 fake 펌웨어 가설은
넣지 않고 byte-copy/fault injection으로 계약을 시험한다. 위/아래 Flow 범위와 24시간 슬롯을
사전 컴파일 대조하고 실제 전체 432B·역할과 현재 Auto 보고를 따로 보존한다. 누적 제품 질문은
8회차로 기록하며 master 배치/시간표/cycle이 함께 바뀌므로 성공도 단일 원인 증명은 아니다.

실제 적용 뒤 원복하지 않고 사용자 시간표를 유지한다. 별도 read-only 야간 collector를 배치해
장비 시각·Auto·역할·raw frame·시각/출처를 영속 보존한다. 관측 실패는 기록하며 write로 보정하지
않는다. 아침 대조는 보고된 Flow와 시간표의 비교이지 물리 유량 측정이 아니다. Bar·옛 앱 원복·
중단된 원 서비스 복귀 의무를 이번 Pro 운영으로 완료 처리하지 않는다. 실패한 live 결과를
커밋하기 전 source 변경 금지와 동일 payload 재전송 금지는 계속 적용한다.

## 2026-09-16 HA 제어 활성화 전 Pro 일일 운전의 소유 복구

조회 전용 배포 후 사용자는 실제 HA 조작을 요청했다. 현재 Async 유지와 전역 미완료 차단
해소가 기존 코드에서 양립하지 않음을 Claude와 독립 확인했고, 운영 source rev79와 자기
detach 자식 부재를 읽기 전용으로 확인했다. 에이전트는 현재 Async 종료·연동 전 저장 원본
복원 및 현장 감시·중단 수단의 필요를 질문했고, 사용자는 “진행하자 이따 … async 전환하고
스케만 넣어 줄 수 있으면 돼”라고 승인했다. 기존 일일 계획·역할 배치를 보존한다. 이는 이후
재적용의 결과를 미리 PASS로 보거나 현재 미확인 ACK를 성공으로 바꾸는 승인이 아니다.

첫 operation은 `wm_pro_daily_fdcddc0_01`의 기존 자기 원본 복구다. 기존 fdcddc0 이미지와
그 context에 결속된 입력을 그대로 사용하며, 장비는 그 Pro 두 대뿐이다. collector와 observer를
중지한 뒤 기존 `recover-wavemaker`를 한 번 실행한다. 첫 atomic 해제가 미확인이면 기존
별도 scalar own-assist의 정확한 진입 조건을 확인하고 그 절차만 사용한다. 시도한 명령의
재전송·자동 새 id 반복·공장 초기화·generic recover·직접 journal 수정은 없다.

자기 원432B와 원8controls의 반환값으로 복원 여부를 확인하고, 시각·Auto 출력·활성 슬롯의
과거 일치는 판정 조건으로 삼지 않는다. 실패 시 원본·증거를 보존하고 전진을 멈춘다.
복구 결과를 먼저 append-only 기록·커밋한 뒤에 새 schema의 기능별 자격·제어 배포를 잇는다.
이번 첫 복구에는 리턴/Bar/도징 write나 새 Async 재결속을 포함하지 않는다. 후속 Async와
시간표 재적용은 보존 계획으로 별도 기록하며, 재결속 미완료가 다시 제어를 막을 수 있다는
기존 계약을 숨기거나 완화하지 않는다. frozen6·protocol·공용 guard·writer 변경은 없다.

### 2026-09-16 소유 복구 후 Pro 제어 자격 갱신

`2b72726`에 자기 원복·직접reply exact·미완료0을 먼저 기록했다. 사용자의 HA 실제 조작 및
이후 Async/시간표 적용 요청을 잇는 다음 단위는 **두 Pro의 기존 고정9기능 producer 단회**다.
출력39/40·준비F20·임시시간표·짧은 OFF/ON과 자기 원본 복원, 최대38전진+6원복 logical action
범위를 실행 전에 공개한다. 다른 장비를 자격 대상으로 넣지 않는다.

이미 전체 Linux3153 tests를 통과한 d792897 제품 source/image와 새3e6 schema를 그대로 사용한다.
별도 OBSERVER qualification config/manifest와 새 후보 출력 경로를 만들고 기존 운영 config 및
40영수증은 보존한다. source/child 최종 증거와 자기 원본 복원이 완료된 경우에만 새36receipt를
발급한다. 실패 시 전진을 반복하지 않고 기존 소유 복구·원본·raw 증거를 우선한다. 새schema에
유효한 자격이 없는 경우 옛receipt로 새image의 보조 명령을 통과시키지 않는다.

이번 단위에는 새 Sync/Async 결속, 리턴·Bar·도징 write, endpoint 시험, schema/guard 완화가 없다.
qualification과 제어용 runtime 모드는 별도 입력으로 분리하며, 운영 배포는 새 자격 검증 후
해당 Pro 기능만 열린 generation을 사용한다. 원 Async 전달UNKNOWN은 계속 보존한다.

### 2026-09-16 Bar 단독 자격 갱신·HA 활성화

Pro 제어 배포와 나머지 장비 읽기 전용 확인을 기록한 뒤 사용자는 Bar의 OFF/ON을 허용하고
"그것만 일단 진행하자"고 대상을 좁혔다. 이후 앱의 Classic/Constant 혼동을 정정하며
Constant 준비 완료를 알렸다. 새 publication에서도 Bar ON/TimerOFF/independent/Constant·
Flow40/F80/Feed10을 확인했다. 리턴 두 대·도징·새 Async·시간표 쓰기는 이번 범위가 아니다.

기존 d792897 제품 이미지/3e6 schema의 producer만 사용해 Bar manual_flow(40→39→40),
manual_frequency(80→5→80), manual_mode(Constant→Classic→Constant), feeding_duration(10→1→10),
power(ON→OFF→ON)를 기능별 한 번 수행한다. 각 최초값 재전송을 포함해 정상 완주는15send이며,
함수마다 자기 baseline 복원·terminal VERIFIED 확인 뒤에만 다음으로 간다. 실제 시작 시 원본은
fresh capture에서 소유하며 과거 시각/Auto/슬롯 일치로 admission을 만들지 않는다.

기존 Pro HA runtime은 경합 방지를 위해 시험 동안 중지하고 입력/volume을 보존한다. 각 실패는
즉시 멈추고 같은 operation의 기존 자기 복구만 고려한다. 새 id 반복·미확인 명령 재전송은 없다.
power 실패는 기존 OFF 복구 의미를 유지하며 원래 ON으로 자동 복원하지 않는다. 결과·원본과
증거 등급을 먼저 기록하고, 성공한 새 Bar10receipt를 기존 Pro36receipt와 검증 merger로 병합해
해당 세 장비만 조작 가능한 새 generation을 만든다. 배포 전 owned discovery를 실제 보존하고
기동 write0·기록 불변·HA registry를 확인한다. 실패 시 보존한 Pro-only 서비스로 복귀한다.

Bar timer/schedule/feed-switch/pulse-or-tide/linkage의 새 승격은 하지 않는다. 단순히 출력 범위를
넓히거나 자격을 합성하지 않으며, 기존 group은 비무장 상태로 둔다. frozen native6·protocol·
공유 guard·writer/schema·제품 source/test 변경0이다. 사용자가 이후 요청할 시간표 재적용은 별도다.

#### Bar 시간표 적용 추가 요청

첫 Bar write 전 사용자가 "스케쥴도 활성화 해야해"라고 추가했다. 위 단위에 기존
ScheduleQualificationProducer의 Bar A/B 두384-byte image와 자기 원본 raw 복원3send를 추가한다.
TimerOFF를 유지한 채 full-day Constant40과39를 시험하고, 원본은 임의 재해석 없이 정확히
복원한다. scalar5종과 합쳐 정상 완주는18send/12receipt다. inactive 원본 image에 더 큰 값이
있어도 이를 켜지 않으며 원본 복원을 임의로 낮춘 새 시간표로 바꾸지 않는다.

실장 자격과 원본 복원 결과를 먼저 기록·커밋한 뒤 `local_control/catalog.py`의 Bar schedule
executable과 직접 HA/MQTT/qualification tests·문서를 필요한 범위만 수정할 수 있다. 기존
image compiler·writer·protocol·schema 계산식과 동결 하네스는 변경하지 않는다. 새 image의
schema가 실제로 동일한지 확인하고 기존 Pro36+Bar12receipt를 검증 merger로 병합한다.
이는 시간표 편집/적용 API이며, shape가 미확정인 Bar TimerON을 단순 bool로 발명하는
권한이 아니다. 자동 운전 ON 경로의 남은 제한과 실제 시간표 재적용을 구분해 보고한다.

#### Bar 타이머 ON/OFF 구현 추가 요청

사용자가 "스케쥴 온하는것도 구현해서 같이 실기 활성화해"라고 범위를 추가했다. 직전 모드
시험의 ACK 이후 검증 중단과 기존 generic 자기 복구 결과는 `3b8812b`에 먼저 기록했다.
새 구현의 근거는 physical template binding `e609fa34…6329`의 `switchToProgram`과
`switchToManual`이다. ON은 앱 로컬 시각의 시간표 슬롯에서 네 Auto* 초기값을 함께 보내고,
OFF는 fresh manual 제어값을 복사한다. Pro의 inverse SwitchON 명령을 재사용하지 않는다.

이 요청에 필요한 local-control writer·timer 준비/복구 계약·자격 producer·CLI·catalog와
직접 tests·문서를 구현할 수 있다. 앱 선언의 실제 ON/OFF 필드 정정도 포함한다. 일반 Auto*
직접 제어를 개방하지 않으며, 독립 Bar의 명시된 timer 명령만 별도 경로로 다룬다. 새 timer
코드는 schema fingerprint에 포함하고 지문 변화로 기존 receipt가 무효가 되는 사실을
숨기거나 hash 입력에서 제외하지 않는다. frozen native6와 protocol byte codec은 변경하지 않는다.
변하는 시각·Auto 출력은 과거 snapshot 일치 gate나 성공 기준이 아니며 명령 준비 입력과
진단으로만 사용한다. identity·출력 상한·single-send·journal·복구 권한은 유지한다.

현재 inactive 원본은 Random100이므로 그대로 ON하지 않는다. 기존 원본을 보존한 bounded
임시 시간표·ON/OFF·TimerOFF 뒤 자기 원본 복원의 실기 범위, 그리고 새 schema에서 Pro 제어를
유지하기 위한 Pro 재자격 범위는 사용자에게 별도로 고지했다. 현장 승인 응답 전에는 새 실기와
새 generation 활성화를 실행하지 않으며 기존 Pro 서비스는 보존된 버전으로 유지한다.
리턴·도징·새 Async는 이번 범위에 추가하지 않는다.

#### 2026-09-16 Bar 타이머 bounded 실기·Pro 자격 갱신 승인

위 구현과 독립 검토 뒤 에이전트는 Bar 임시 Constant40 시간표→ON/OFF→TimerOFF 원384B 복원,
그리고 지문 변경으로 필요한 두 Pro의 기존39/40·짧은 OFF/ON·임시시간표·자기 원본 복원을
명시하고 현장 감시와 즉시 전원 차단 가능 여부를 함께 물었다. 사용자는 "ㅇㅇ 진행하자"고
승인했다. 이 범위에서 새 source-matched image와 별도 OBSERVER 입력/manifest/후보 경로를
준비하고 각 operation을 한 번만 수행한다. Bar는 새4-action timer producer, Pro는 기존9기능
producer를 사용한다. Bar 원 inactive Random100은 ON하지 않는다.

실기 중 기존 Pro 서비스를 정지해 동시 접근을 막고, 각 operation의 terminal·자기 원본 복원을
확인한 뒤 다음으로 간다. 실패하면 전진을 멈추고 보존된 자기 복구만 수행하며 새 id 반복이나
미확인 frame 재전송은 하지 않는다. 결과·raw/durable 증거를 먼저 기록·커밋한다. 새 자격이
확인되기 전에는 기존 Pro 운영 generation을 교체하지 않는다. Bar schedule A/B 자격은 앞서
승인된40/39·원384B 복원 범위를 유지하며, 성공한 기능만 별도 executable 승격·HA 배포한다.
앞선 Bar manual_mode UNKNOWN을 새 id로 반복하지 않고 리턴·도징·새 Async는 포함하지 않는다.
동결 하네스·protocol·상한·identity·single-send·journal·복구 guard 변경은 없다.
