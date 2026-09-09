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
