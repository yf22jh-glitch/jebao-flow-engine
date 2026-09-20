# run: 2026-09-20-slave-manual-reassert-attach-attempt

- 목적(한 문장): slave 역할 획득 시 장비가 manual 블록을 덮어쓰는 문제를 고친 빌드를 배포하고,
  09-18과 동일한 계획으로 Async attach 1회를 돌려 수정이 실제로 막는지 확인한다.
- 이 실행이 답하려던 질문: 역할 전환 뒤 slave의 manual `Mode`/`Frequency`를 계획값으로
  되돌리는 프레임이 장비에 실제로 적용되는가?
- 실행한 commit SHA: `6440958deb11e88915e904db5a489ab935e62396`
  (빌드 image `sha256:e4bd4ba04c860590a830613ac8e99878606212f3ccce8d70c2ff000cb8989e47`)
- 사용한 계획: `kaga_daily_async_20260918_01`의 저장된 계획 그대로. preview에서 image digest가
  09-18과 바이트 동일함을 먼저 확인했다(master `73e0f45b5ce883f1…`, slave `2e6a9814c08ff86d…`,
  `hardware_write=false`).
- 사용한 명령: MQTT `jebao-flow/main/local/wavemakers/request`로
  (1) `preview`, (2) `attach` `kaga_daily_async_20260920_01`
  (`master_cycle_on_async_failure=true`), (3) `assist_detach`.
- physical-write UTC span: `2026-09-20T03:00:10.336Z`–`2026-09-20T03:04:16.952Z`
- 종료 지점: **attach 실패.** action 08에서 ACK 타임아웃으로 `recovery_required`.
  `assist_detach`가 두 장비를 attach 이전 원본으로 복원하고 6/6 `verified`로 종결했다.

## 실제 결과

### 0. 배포 전 거부 — 하위호환 결함 (장비 전송 0건)

첫 배포본 `6a64141`은 첫 write 전에 attach를 거부했다. 단계별 action 배치에 slave manual
재확인을 끼워 넣으면서 Sync 역할 뒤 위치가 밀렸는데 그 새 위치를 **기존 기록에도** 적용했다.
`_require_available`은 저장된 모든 기록을 `_context`로 검증하므로, 09-18의 9-action 스냅샷을
들고 있는 `wm_cycle_…`의 `validate_cycle`이 `_awaiting_operator`에서 action 10개를 요구받아
실패했다. 기록 생성 0건, 장비 전송 0건으로 확인했다. `activation_sequence`를 배치 버전으로
분리해(`staged_roles_v1` / `staged_roles_v2`) 수정하고 재배포했다(`6440958`).

### 1. 장비의 manual 블록 자기 재작성 — 실시간 재현

| 시각(UTC) | stage | act | Linkage | manual Mode / Flow / Freq |
|---|---|---|---|---|
| 03:00:30.680 | `staged_post_role_pair` | 3 | independent | constant / 40 / 80 |
| 03:00:32.296 | `staged_after_read` | 4 | **sync_slave** | **feed / 40 / 5** |

09-18(`constant→feed`, `80→70`)과 09-15 세 건(`constant→sine`, `20→30`)에 이어 **관측 5회 중
5회**다. 이번에는 장비 로컬 12:00, 즉 계획의 `12:00–12:03 feed` 구간이 활성인 시각이었다.
값의 선택 규칙은 여전히 `UNKNOWN`이며 펌웨어 가설로 적지 않는다.

### 2. 수정 프레임은 발동했지만 ACK를 받지 못했다

action 08(`{"Frequency":80,"Mode":"constant"}`, 하단)은 `send_once`에서
`ProtocolTimeoutError`로 끝났다(`acknowledgement_sha256=None`, `uncertain`,
`wavemaker_sequence:advance_staged_roles:407`). action 09(`async_slave`)는 `prepared`로 남아
전송되지 않았다. 이어 자기 원복이 시작됐지만 detach child의 action 00(하단 대상)도 같은
`send_once` 타임아웃으로 `uncertain`이 됐다(`wavemaker_lifecycle:_advance:770`).

- **이 실행으로 새로 확정된 사실:** **역할이 붙은 Pro slave는 제어 쓰기에 ACK를 주지 않는다.**
  오늘 두 건(manual 재확인, detach 첫 프레임)이 모두 `send_once`에서 timeout이었고, 결속 이전의
  모든 쓰기는 정상 ACK를 받았다. 같은 형태가 09-18 async 역할 쓰기, 09-19 recover 시도,
  09-15 세 건의 async 역할 쓰기에 모두 있다. 반례는 없다.
- 따라서 **`async_slave` 역할 쓰기가 ACK를 받은 실행은 지금까지 한 번도 없으며**, Async 결속은
  언제나 master-only로 동작하는 master cycle이 완성해 왔다.
- 파생 결론: slave의 manual 블록은 **결속이 걸려 있는 동안에는 고칠 수 없다.** 역할 사이에
  재확인 프레임을 넣은 이번 설계는 위치가 틀렸다.

### 3. 복구

`assist_detach` `wm_assist_c26166cc…`가 5 scalar action으로 성공했다. 첫 프레임이 상단을
`independent`로 풀어 결속을 해제하고, 그 뒤 하단 쓰기가 다시 동작했다. detach child
`wm_detach_c26166cc…`는 6/6 `verified`, source는 `rolled_back`이다.

- 장비가 실제로 돌려준 값:
  - 증거 등급: (b) preserved structured·durable daemon artifact
  - source `kaga_daily_async_20260920_01` journal `7e572d40bab36540be5e67fc2b73c9cc…`,
    `activation_sequence=staged_roles_v2`, 10 actions
  - assist `fc9f949898691723e2afa7ff4f3c02a3…`, detach `14ef78126d02ba5ca3a28c9a0e947611…`
  - 복원 뒤 상단 `independent / constant / Flow 40 / Freq 34`,
    하단 `independent / constant / Flow 30 / Freq 32`, 두 대 모두 power ON·schedule ON,
    최종 대조 두 장비 모두 `complete=true`
  - 미완료 기록 0건
- 원복 검증 결과: attach 이전 원본 제어값과 원본 schedule image로 복원됐다. 일일 Async 계획은
  적용돼 있지 않다.

### 4. 배포 롤백

배포본을 실기 이전의 알려진 정상 image
`sha256:b81464ec1eb28d4a49936d462a53067c22c740122b51e390e0d3f2b2006bb6e0`로 되돌렸다.
이번 빌드는 Async attach를 **이전보다 나쁘게** 만든다 — 실패 지점이 action 08로 앞당겨지면서
`_awaiting_operator`가 성립하지 않아 자동 master cycle 구제 경로까지 막히기 때문이다.

## 부수 확인

- 역할 이후 pair barrier의 report 프레임 수용은 동작했다. 03:00:33.835 `staged_post_role_pair`
  하단 캡처가 act=4였고 barrier가 이를 받아들였다.
- preview는 `hardware_write=false`로 장비 전송 0건이었다.

## 현재 판정

- 이 실행으로 확정하지 못한 것: 장비가 manual `Mode`에 쓰는 값의 선택 규칙,
  `2026-09-18T11:36:31Z` 직전 `TimerON`을 끈 주체, 물리 유량.
- 다음 실험 제안: slave의 manual 블록을 쓸 수 있는 유일한 창은 **master가 결속을 놓고 있는
  구간**이다. master cycle은 이미 `independent`로 푼 뒤 `released_reply_pair`에서 하단을 act=3
  명시 응답으로 읽는다. 그 구간에서 manual `Mode`/`Frequency`를 재확인하는 설계를 다음 후보로
  둔다. 이번처럼 오프라인 시험만으로 배포하지 않고, 먼저 그 구간에서 하단 쓰기가 ACK를 받는지
  단독으로 확인한다.

## 덧붙임 — 두 번째 위치(master cycle released 구간)도 성립하지 않는다

§4에 따라 위 기술은 고치지 않고 덧붙인다. 위 「다음 실험 제안」대로 복구를 master cycle의
released 구간으로 옮겨(`08aab8cff96ede9ad47c417383cc2d1dfe0a2c55`,
image `sha256:15ef95d45b75a080102518cbc219da78dd75e74d4b8540099ed8cc664c5b9bdc`) 같은 계획으로
attach 1회를 더 돌렸다(`kaga_daily_async_20260920_02`).

### 결과 — ACK는 오고, 적용은 되지 않는다

cycle `wm_cycle_695a50f2…`의 3 action 중:

| action | 대상 | 내용 | 결과 |
|---|---|---|---|
| 0 | 상단 | `Linkage=independent` | `verified`, ack=Y |
| 1 | 하단 | `Mode=constant` | **ack=Y**, 반환값 불일치로 `uncertain` |
| 2 | 상단 | `Linkage=master` | 미전송 |

action 1의 증거:

```
09:08:23.738  released_reply_pair  하단  act=3  Link=async_slave  Mode=feed    (complete)
09:08:26.286  before               하단  act=3  Link=async_slave  Mode=feed    → 전송, ack=Y
09:08:27.188  after                하단  act=4  Link=async_slave  Mode=feed    ✗
09:09:33      (운영자 읽기)        하단        Link=async_slave  Mode=feed    ✗ 66초 뒤에도
```

- **이 실행으로 새로 확정된 사실:** 상단이 released여도 **`async_slave`는 manual `Mode` 쓰기를
  ACK만 하고 적용하지 않는다.** 66초 뒤에도 `feed`였으므로 readback 창 길이 문제가 아니다.
  즉 두 번째 위치도 틀렸다.
- 대조 관측: 2026-09-20 03:03:52.912에는 하단이 **`sync_slave`** 이고 상단이 released인 상태에서
  manual이 `constant/80`으로 돌아와 있었다. 역할 종류에 따라 다르며, 그 규칙은 `UNKNOWN`이다.
- 파생 결론: **manual `Mode`는 slave가 역할을 벗어난 상태에서만 고칠 수 있다.** 실제로
  09-20 03:03:56의 `schedule_enable`(앱 shape상 `Linkage=independent` 동반) 프레임은 ack=Y로
  적용됐고 그 뒤 하단 쓰기가 전부 정상 동작했다.

### 복구와 롤백

`recover` `kaga_recover_20260920_01`이 detach child `wm_detach_695a50f2…`를 6/6 `verified`로
끝냈고 두 장비 모두 `complete=true`로 attach 이전 원본에 복원됐다. 미완료 기록 0건.
09:11:50 확인: 상단 `independent/constant/34`, 하단 `independent/constant/32`, 둘 다 schedule ON.
배포본은 다시 `sha256:b81464ec…`로 되돌렸다 — 이번 빌드는 기존에 성공하던 master cycle을
실패시키므로 이전보다 나쁘다.

### 다음 실험 제안

남은 후보는 **released 구간에서 하단을 한 번 결속 해제했다가 다시 붙이는** 순서다.
`상단→independent`, `하단→independent`, `하단 Mode=constant`, `하단→async_slave`,
`상단→master`. 앞의 세 프레임은 09-20 03:03:51~03:03:59에 전부 ack·적용이 확인된 것과 같은
shape다. 미확인은 마지막 재결속이 manual을 다시 덮어쓰는지 하나뿐이며, `rearmed_pair` 대조에
하단 `Mode`를 포함해 두면 그 한 가지가 이 실행 1회로 판정된다.

대안은 latent 값을 고치는 대신 **`TimerON`이 내려가는 사건 자체를 감시해 기존
`slave_timer_rearm` 자식을 돌리는** 방향이다. 어느 쪽을 제품 동작으로 삼을지는 사람이 정한다.

## 덧붙임 — 판정 실기: 덮어쓰기 주체는 "활성 master가 링크를 잡고 있는 상태"다

위 「다음 실험 제안」의 순서를 구현해(`6e9a92a98eebe572cbb290b9d9d153a0a992469d`,
image `sha256:337a80b778c0b855ec2d147727dcbca125356e7458d1de1f1619b04836b15930`) 같은 계획으로
attach 1회를 더 돌렸다(`kaga_daily_async_20260920_03`). 배포 전에 live journal 대상
read-only 전제 검사를 먼저 통과시켰다.

### cycle 5 action은 전부 verified, 그리고 그 직후가 답이다

`wm_cycle_a4afa6bb…`:

| 시각(UTC) | 대상 | 프레임 | 결과 |
|---|---|---|---|
| 09:30:11.158 | 상단 | `Linkage=independent` | ack=Y, verified |
| 09:30:18.748 | 하단 | `Linkage=independent` | ack=Y, `async_slave→independent`, Mode 여전히 `feed` |
| 09:30:20.694 | 하단 | `Mode=constant` | ack=Y, **적용됨** (`feed→constant`) |
| 09:30:22.483 | 하단 | `Linkage=async_slave` | ack=Y, `async_slave` 복귀, **Mode `constant` 유지** |
| 09:30:24.477 | 상단 | `Linkage=master` | ack=Y, verified |
| 09:30:26.015 | 하단 | (rearmed_pair 읽기) | act=4, **Mode `feed`** ✗ |

- **이 실행으로 새로 확정된 사실:**
  1. 장비가 역할을 벗어나면 manual `Mode` 쓰기는 정상 적용된다(09:30:20.694).
  2. **하단의 자기 역할 복귀(independent→async_slave)는 manual을 덮어쓰지 않는다**
     (09:30:22.483에 `constant` 유지).
  3. **덮어쓰는 것은 상단이 링크를 다시 잡는 순간이다** — 09:30:24.477에 상단이 `master`가
     되고 1.5초 뒤 하단 manual이 `feed`가 됐다.
  4. 따라서 **pair가 링크되어 있는 동안 하단 manual `Mode`는 `feed`로 고정되며, 어떤 쓰기
     순서로도 다른 값으로 유지할 수 없다.** manual 블록 복구는 불가능으로 확정한다.
- 추가한 `rearmed_pair`의 하단 `Mode` 대조가 이를 잡아 cycle을 실패시켰다. feed를 숨긴 채
  성공을 주장하지 않았다는 점에서 가드는 의도대로 동작했다.
- 여전히 `UNKNOWN`: 장비가 `feed`라는 값을 고르는 규칙. 09-20 09:30 시점 활성 구간은
  `18:08–19:00 pulse/45/70`이었으므로 현재 프로그램 값도 아니다. 펌웨어 가설은 적지 않는다.

### 복구

`recover`는 detach child의 action 00이 `uncertain`이 되며 실패했고, `assist_detach`
`kaga_assist_20260920_02`가 두 장비를 attach 이전 원본으로 복원해 6/6 `verified`로 끝냈다.
미완료 기록 0건. 배포본은 다시 `sha256:b81464ec…`로 되돌렸다.

## 이 조사의 종결 판정

- **왜 `feed`에 들어갔는가:** pair가 링크되면 장비가 하단 manual `Mode`를 스스로 `feed`로
  만든다. 관측 7회 중 7회. 우리 코드가 보낸 적은 한 번도 없다.
- **고칠 수 있는가:** 없다. 링크 중에는 쓰기가 적용되지 않고, 링크를 풀고 고쳐도 상단이
  다시 잡는 순간 되돌아간다. 세 가지 위치를 실기로 배제했다.
- **그러면 무엇이 보호인가:** latent 값을 고치는 대신 **하단이 `TimerON`을 잃는 사건**을
  감시해 기존 `slave_timer_rearm` 자식으로 시간표를 되살리는 것이다. 그 자식은 이미 있고
  2026-09-18에 동작이 확인됐다. 다만 지금은 보류된 source에만 결속되므로, 정상 종료된
  attachment에서도 쓸 수 있게 하는 변경이 필요하다.
- 함께 필요한 것: master cycle이 성공했을 때 source를 닫아 장비 소유를 해제하는 변경
  (09-18에 22시간 동안 모든 명령을 막았던 원인). 이번 세 번의 실행에서 cycle이 한 번도
  끝까지 성공하지 못해 그 경로는 아직 실기로 확인되지 않았다.
