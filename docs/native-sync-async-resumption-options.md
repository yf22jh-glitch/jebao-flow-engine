# Native Sync/Async · 슬레이브 TimerON 파형별 Flow 재개 준비

상태: **Sync-only 구현·오프라인 검증 완료 / 새 장비 write·앱 설정·배포 미승인**.
변경 계약과 중간 실패를 포함한 검증 결과는 [구현 기록](native-sync-only-implementation.md)에 있습니다.
사용자는 software group 보조 시험보다 native Sync/Async에서 slave TimerON의 파형별 Flow
확인을 우선하라고 요청했습니다. [D1w](runs/2026-09-14-m4-group-power-diagnostic-retest.md)는
`774ad13`에 기록됐고 원래 서비스는 비무장으로 복귀했습니다. 현재 수용된 second Pro의
inactive manual30과 실제 스케줄 출력은 구분합니다. 다음 시험을 위해 이를 임의로 바꾸지 않습니다.

## 질문과 기존 증거

1. Sync에서 slave TimerON일 때 지정한 파형별 Flow가 protocol runtime state에 나타나는가?
2. Async에서도 같은 기능이 성립하는가?
3. 파형·전이 시각은 master를 따르면서 Flow만 slave 값을 쓰는가, 아니면 slave 자체 시간표인가?

1/2의 값 관측과3의 소유권 판정을 섞지 않습니다. 물리 유량·위상 측정은 별개입니다.
같은 Mode와 경계의 두 schedule에서 서로 다른 Flow가 나와도 그것만으로 master timing 추종을
증명하지 못합니다. 적어도 한쪽만 전환하는 구간이 있어야 타이밍 소유권을 구분할 수 있습니다.

9월2일 [Sync 중간 단계 기록](runs/2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9.md)은
master/sync_slave·TimerON·서로 다른 schedule image·AutoFlow40/35를 보고했지만, inactive
manual Mode/Frequency 차이로 Async 진입 전에 중단됐습니다. role 진입 뒤 변화 관측은 있으나
mirror 원인이나 정상 펌웨어 동작의 일반 규칙은 확정되지 않았습니다.

현재 source의 실제 흐름은 다음과 같습니다.

`_apply_fixed_slave_sync_intermediate` → raw pair read → `_assert_fixed_q2_sync_intermediate`
→ `_assert_pair_sample(allow_staged_control_transition=False)` → `_assert_immutable_snapshot`
→ `_assert_snapshot_control_sets` → 실패 시 Sync VERIFIED/Async intent 이전 중단.

`schedule_linkage.py`의 `pair_state_dimensions`는 진단 모델의 허용 enum 집합이지 실제 실행
게이트가 아닙니다. 거기서 Mode/Frequency를 빼는 변경은 실제 비교·raise를 제거하지 않으며,
여러 실패 범주의 진단 표현을 깨뜨릴 수 있습니다. 수정하지 않았습니다.
Sync만 관측하겠다는 이름으로 이 기존 검사에 도달하지 않는다고 가정하지도 않습니다.

## 새 code-write 없는 오프라인 projection

진단 image와 D1w ON 준비 시점의 보존 Pro status 두 개를 network-none/read-only로 사용했습니다.
장비·브로커·production volume은 접촉하지 않았습니다. 이 입력은 현재 fresh baseline이 아닙니다.
두 역할 방향을 모두 계산한 것이지 master/slave 물리 배치를 승인하거나 정한 것이 아닙니다.
기존 `compile_slave_schedules`에 첫 non-feed slot의 예시 Flow31만 명시했습니다.

| 가상 역할 배치 | Sync/Async 결과 | 명시하지 않은 충돌 슬롯(0-based) |
|---|---|---|
| second master / first slave | 둘 다 범위 검사 거부 | 1·4·7·10 |
| first master / second slave | 둘 다 범위 검사 거부 | 2·5·8·11 |

원인은 compiler가 timing/mode를 master image에서 복사하면서 명시하지 않은 Flow는 같은
index의 slave slot에서 유지하는 구성입니다. 위 index에서 slave의 feeding slot(mode7) Flow0이
master의 non-feed slot로 옮겨져 기존30..100 검사에 걸렸습니다. 이는 보존 입력과 순수 계산으로
확인한 거부 경로이며 장비 실패나 펌웨어 결함이 아닙니다. 범위를 완화하거나0을 clamp하지 않습니다.
실제 계획은 대응 슬롯 전체의 Flow를 명시하거나, feeding이 없는 승인된 두 구간 image를
별도로 정해야 합니다. 한 슬롯 설정이 전체432-byte schedule을 바꾸는 경로라는 점도 공개해야 합니다.

입력 schedule image SHA-256:

- second: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`
- first: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`

출력 artifact는 D1w private root의 별도 후속 파일 `native-schedule-offline-projection-v3.stdout`,
SHA-256 `7c68b6f7bebbbb9eb1b6fd2072136f9eb8de2d7aa82ec0b0029996e0b02dac41`입니다.
처음 v1은 잘못된 import 위치 때문에 계산 전 실패했고 stderr를 보존했습니다. v2는 올바른
device_writer의 추출 함수로 정정한 뒤 첫 범위 거부에서 끝났습니다. v3는 같은 compiler를 바꾸지
않고 양쪽 배치의 거부 원인을 각각 수집했습니다. production source·tests·schema 변경0,
device command0이며 실행 가능성을 PASS로 올리지 않았습니다.

## 가능한 경로와 실제 선행조건

| 경로 | 활용 가능한 부분 | 아직 필요한 것 |
|---|---|---|
| 기존 integrated native 하네스 | 자기 시작 snapshot과 schedule/outer-control 복원 capability의 과거 실기 근거 | 재동결된 source·직접 tests의 명시적 단회 해제, 현재 artifact/복구 경로 검증, 새 operation 승인 |
| 앱 설정 + read-only 관측 | 별도 reply_or_report collector가 source에 존재; code-write 하네스 동결을 실행하지 않음 | 앱 변경 전 qualified standalone exact restore·권한·이중 fresh baseline. 현재 미확보이므로 앱만 먼저 바꾸지 않음 |
| 현 local-control slot editor | fresh432-byte compiler·journal·schedule readback·HA slot projection 구현 | `slave_per_slot_flow_extension`의 독립 실기 qualification/receipt와 성공 후 baseline 복원 operation, 역할·TimerON 설정 권한 |

`execute_schedule`/`execute_schedule_flow`는 extension QUALIFIED 및 해당 physical target receipt를
요구합니다. 기존 manual_flow/power receipt를 대신 쓰지 않습니다. 현재 qualifier는 manual_flow와
power 전용이며 이 extension을 자동으로 자격 검증하지 않습니다. VERIFIED record는 일반 recover
대상이 아니므로, 성공 뒤 baseline schedule 복원을 실패용 recover에 맡길 수도 없습니다.
Sync/Async라는 compiler 인자는 bytes 구성의 메타데이터이며 role 전환·TimerON을 보내지 않습니다.

오래된 Flow89 baseline의 범위 문제와 standalone 복원 수단·권한 미확보는 별개입니다.
현재 latent가30/40이라고 해서 후자가 자동 해소되지 않습니다. 반대로 현재 사용자 수용값을
옛89 정규화 문제로 다시 취급하지 않습니다. 다른 펌프·Bar·리턴·도징은 native 시험에서 제외합니다.

## 권고하는 다음 결정 — 구현 승인과 실기 승인을 분리

가장 가까운 기존 근거는 integrated 하네스의 원 schedule 복원 능력입니다. **Sync-only 두 구간
관측을 먼저 준비하고 그 결과로 Async를 설계**하는 경로를 권고합니다. 9월3일 미사용 종료된
Sync-only 계획의 역할·샘플·복원 설계를 참조하되 stash나 과거 미검증 source를 병합하지 않습니다.
사용자의 현재 목표에 맞춰 native 준비를 앞당기되 M5 완료나 상시 진단 배포를 자동 선행조건으로
추가하지 않습니다. M5 자체의 미완료는 그대로 남깁니다.

필요한 구현 검토 범위는 기존 Sync-only 예외에 열거됐던 다음 네 source와 그 직접 tests입니다.

- `devices/schedule_flow_experiment.py`: 임시 두 구간·Sync-only outer role
- `devices/schedule_linkage.py`: Sync-only progress/topology·관측·해당 중간 검사 진단
- `schedule_flow_experiment_cli.py`: 정확한 fixed 계획·시간·운영자 안내
- `hardware_test.py`: 해당 fixed Sync-only sample topology에 한정한 결과 처리

사용자는 위 네 source와 직접 tests의 Sync 준비용 한정 해제 질문에 “ㅇㅇㅇ 진행하자”로
승인했습니다. **AGENTS의 사유·범위·재개 조건을 별도 커밋한 뒤에만 구현**하는 조건에 따라
`d81552f`를 먼저 기록하고 구현·오프라인 검증을 마쳤습니다. **실제 장비 하네스 실행은
아직 승인되지 않았습니다.** 결과와 독립 검토를 커밋하면 해당 구현 예외는 소진·재동결됩니다.
일반 Async 검사·identity·Flow 범위·single-write·journal·rollback은
보호하고, scope가 늘어나면 멈춰 별도 승인받습니다. 단순 enum 삭제나 timeout/retry 추가는 금지합니다.

구현 뒤에도 새 image/source 결속, current baseline·복구 artifact 검증, 구간별 예상 tuple·명령
수·관측 상한·정지/복원 절차를 담은 단회 실기 승인이 따로 필요합니다. 현시점에 앱 작업을
요청하지 않으며, 복원 수단을 확인하기 전 임시 schedule이나 role을 바꾸지 않습니다.
기존900초 관측 계약은 준비·원복 시간을 포함하지 않습니다. Sync와 Async 전체를 몇 분 안에
끝낼 수 있다고 약속하지 않고 실행 가능한 계획의 시간표가 정해진 뒤 예상 시간을 공개합니다.

추가 write 없이 park하는 대안은 즉시 가능하지만 기능 답은 미해결로 남습니다. 이번 문서는
§7의 증거·대안·영향 보고이며, 과거 승인이나 D1w 단회 권한을 native 시험에 재사용하지 않습니다.
