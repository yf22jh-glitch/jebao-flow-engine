# 실기 실행 기록 (append-only)

이 디렉터리는 실제 장비에 대해 수행한 모든 실기 실행을 **한 번 쓰고 고치지 않는 기록**으로
남깁니다. 요약하거나 최근 몇 건만 남기고 덮어쓰지 않습니다.

## 왜 append-only인가

2026-08-26~28 사이 네이티브 Linkage 실기가 13회 수행됐지만, 실행 기록은 `README.md` 상단
상태 블록에만 있었고 새 실행이 나올 때마다 이전 기록을 지우며 덮어썼습니다. 그 결과 초기
실행 기록이 사라질 뻔했고, 같은 종류의 수정이 여러 번 반복된 것을 아무도 — 작업하던 에이전트
자신도 — 알아채지 못했습니다. 같은 기간 커밋 47개의 본문은 전부 비어 있어
(고정 역사 구간 `6bf85fc^..7dc5c59` 기준, `git log --format=%b 6bf85fc^..7dc5c59`의 공백 제외
0바이트) 어떤 커밋이 어떤 실기에 대한 대응인지 추적할 수 없었습니다. **`HEAD`가 아니라 이
고정 범위로 재현하십시오.** 새 커밋이 쌓이면 `HEAD` 기준 수치는 달라집니다.

이 디렉터리는 그 상태를 되풀이하지 않기 위한 것입니다.

## 규칙

- **실기 1회 = 이 디렉터리의 파일 1개.** 파일명은 `YYYY-MM-DD-<run-id>.md`.
  - *예외*: 실행 시점에 개별 기록이 없었던 legacy 구간은 집계 문서 하나로 보존할 수 있습니다.
    현재 예외는 [2026-08-26--28 이력](2026-08-26--2026-08-28-native-linkage-history.md)
    하나뿐이며, 새 실행에는 이 예외를 적용하지 않습니다.
- **기존 파일의 사실 기술은 수정하지 않습니다.** 나중에 잘못이 밝혀지면 그 파일을 고치지 말고
  파일 하단 `## 정정` 절에 정정 사유와 근거를 덧붙입니다.
  - *예외*: 비밀값(§비밀값)이 실수로 들어간 경우는 즉시 제거합니다. 제거 사실과 사유는
    `## 정정`에 남기되 비밀값 자체는 남기지 않습니다.
- `README.md`와 `PROJECT_CONTEXT.md`의 상태 블록은 이 디렉터리를 **링크만** 하고 서사를
  복제하지 않습니다.
- 실기 결과 파일이 커밋되기 전에는 `src/` 아래 파일을 수정하지 않습니다.
- **"이 실행으로 새로 확정된 사실"이 비어 있으면 그 실행은 정보 0입니다.**
  **정보 0 실행이 2회 연속이면** 코드를 고치지 말고 [`AGENTS.md`](../../AGENTS.md) §7의
  정지·보고 규칙을 따릅니다.

## 증거 등급 (세 문서 공통)

`AGENTS.md`, 이 디렉터리, `docs/hardware-readiness.md`가 같은 용어를 씁니다.

- **(a) preserved raw artifact** — 장비가 보낸 **원본 프레임**이 파일로 보존되어 오프라인
  재검증이 가능한 경우. 가장 강한 등급입니다.
- **(b) preserved structured/durable daemon artifact** — 데몬이 남긴 저널·영수증·intent 같은
  구조화 산출물이 실제 파일로 보존된 경우. **원본 프레임이 아니므로 "데몬이 그렇게 기록했다"는
  것까지만 증명하고, 장비가 실제로 그 상태였음을 증명하지 않습니다.**
- **(c) reconstructed operator observation** — 당시 사람이 보고 문서에 남긴 기술만 있고
  보존된 산출물이 없는 경우.

규칙:

- 보존 파일과 그 id·digest를 **실제로 확인하기 전에는 (b)로 올리지 않습니다.** 확인되면
  그때 (c)에서 (b)로 승격하고, 승격 근거를 함께 적습니다.
- 나중에 보존 파일의 부재가 확인되면 원래 실행 기록을 지우지 않고 그 run의 정정 절에 **부재를
  확인한 시각·범위·방법과 재검증 불가 상태**를 덧붙입니다. 당시 존재했다는 관측과 현재도
  보존돼 있다는 주장을 분리하며, digest만 남은 항목을 현재의 (b) 근거로 사용하지 않습니다.
- 등급을 섞어 "확인됐다"로 쓰지 않습니다.
- (b)·(c)를 (a)의 대체물로 쓰지 않습니다.

## 비밀값과 artifact 기록 형식

MAC, Gizwits device ID, 사설 IP, passcode, MQTT 비밀번호는 기록하지 않습니다. 논리 역할과
판정 결과만 남깁니다.

raw·구조화 산출물은 사설 주소를 포함하므로 저장소에 넣지 않습니다. **private 파일 위치나
절대경로도 적지 않습니다**(홈서버 디렉터리 구조 자체가 노출 정보입니다). 이 디렉터리에는
다음 네 가지만 기록합니다.

- **opaque artifact id** 또는 안전한 상대 논리 경로
- **UTC span** (수집 시작·종료)
- **SHA-256 identity binding** — 실제 MAC·device ID가 아니라 그 해시
- **artifact digest** (SHA-256)

## 색인

| 파일 | 범위 | 요지 |
|---|---|---|
| [2026-08-26--28 네이티브 Linkage 실기 이력](2026-08-26--2026-08-28-native-linkage-history.md) | 13회 (legacy 집계) | 슬롯별 `AutoFlow` 질문은 여전히 UNKNOWN. 직접 시도는 5회 |
| [2026-08-28 write-free collector pilot](2026-08-28-pilot-2bd1bf97.md) | 18 pair / 36 raw frame | collector transport·timing PASS. Q2는 판정하지 않음 |
| [2026-08-30 write-free collector 30초 cadence](2026-08-30-collector-a2f44ded.md) | 18 pair / 36 raw frame | 30초 cadence acquisition PASS. Q2·independent-control epoch는 미실행 |
| [2026-08-30 exact-restore preflight](2026-08-30-preflight-8e4204d2.md) | 1 pair / 2 raw frame | 역할 B latent `Flow=89`를 fresh raw로 재확인. restore admission은 계속 FAIL |
| [2026-08-30 current baseline](2026-08-30-baseline-a9e40866.md) | 1 pair / 2 raw frame | `708fed7`에서도 역할 B latent `Flow=89` 재확인. 앱 정규화 대기 |
| [2026-08-30 Q2 qualification retry](2026-08-30-q2-qualification-retry.md) | 저출력 qualification 1회 | 역할 해제 read-back 실패 뒤 attended recovery; 새 receipt 없음 |
| [2026-08-30 Q2 attempt 01](2026-08-30-q2-attempt-01.md) | write 전 거부 | receipt exception이 최종 armed plan에 전달되지 않음 |
| [2026-08-30 Q2 attempt 02](2026-08-30-q2-attempt-02.md) | role write 전 복구 | nested role spec의 receipt authority 누락 |
| [2026-08-30 Q2 attempt 03](2026-08-30-q2-attempt-03.md) | 두 role write 뒤 복구 | slave pair read 오류가 monitor 전 조기 원복을 유발 |
| [2026-08-30 Q2 attempt 04](2026-08-30-q2-attempt-04.md) | write 전 거부 | preflight→run 사이 boundary lead 소진 |
| [2026-08-30 Q2 attempt 05](2026-08-30-q2-attempt-05.md) | 900초 ASYNC epoch + exact restore | slave staged Flow 32→40 및 byte-exact 복원 확인; Mode·timing 소유권은 비판별이므로 정정 절 참조 |
| [2026-09-01 Q2-target 판별 시도](2026-09-01-q2-target-9c982c60.md) | field write 전 거부 + exact restore | 반대 Mode 계획이 기존 동일-topology guard에서 거부됨; Q2-target은 UNKNOWN, fresh raw 원복 검증 2회 PASS |
| [2026-09-01 Q2-target topology 교정 실행](2026-09-01-q2-target-corrected-a3adb738.md) | 900초 epoch + master raw 91 + exact restore | master Mode·Flow 전환만 raw 재분석으로 확인; slave pair read 90회 실패로 Q2-target은 UNKNOWN, fresh raw 원복 검증 2회 PASS |
| [2026-09-01 independent transport probe](2026-09-01-transport-probe-independent-c5b4eaeb.md) | write 0 + strict read 2회 | independent 두 Pro 모두 explicit `0x03`과 raw 보존 PASS; ASYNC 원인은 계속 UNKNOWN |
| [2026-09-01 current baseline 재확인](2026-09-01-current-baseline-78058401.md) | 2 pair / 4 raw frame, write 0 | 역할 B latent `Flow=89`가 현재도 유지됨을 재확인; restore admission FAIL, Q2-target은 UNKNOWN |
| [2026-09-02 Q2-slotflow 재검증](2026-09-02-q2-slotflow-a00f4b1.md) | 900초 epoch + state frame 822 + exact restore | master `40→35`는 확인; slave 411 report가 모두 경계 전 backlog이고 앱 `0→2→3`과 하네스 `0→3`이 달라 slot Flow는 UNKNOWN |
| [2026-09-02 Q2-slotflow 앱-sequence 교정](2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9.md) | master→Sync write + fresh raw 2 + exact restore | master·sync-slave 지정, TimerON, 장비별 schedule image 보존은 확인; runtime schedule 소유권 미판별, inactive manual mode/frequency drift gate에서 ASYNC write 0 |
| [2026-09-04 standalone exact-restore qualification](2026-09-04-exact-restore-qualification-4dcd8364.md) | sentinel write 8 + strict raw baseline 검증 + final verify 2회 | 두 장비 byte-exact 원복은 확인; 하나의 pair-gap 정책이 completion gap과 전체 취득창에 겹쳐 receipt는 미발급, non-terminal journal을 보존하고 park |
| [2026-09-07 B1 identity-resolution stop](2026-09-07-b1-identity-resolution-stop-7746b28a.md) | primary 2 pair 시도 / raw frame 0 / write 0 | 두 ordinal의 두 role 모두 identity-before exact resolution 실패; corroborating·retry는 실행하지 않았고 Q2는 UNKNOWN |
| [2026-09-10 local observer 첫 활성화](2026-09-10-local-observer-first-activation.md) | native entity 86 / exact discovery 4 of 6 / write 0 | native HA 등록 PASS; 한 target 누락이 read-only batch 전체를 unavailable로 접는 mismatch-isolation 결함 확인 |
| [2026-09-10 local observer endpoint 격리](2026-09-10-local-observer-endpoint-isolation.md) | fresh online 4 / absent 2 / write 0 | D1f target별 격리 PASS; exact-matched 4개는 fresh state를 발행하고 absent 2개만 unavailable |
| [2026-09-10 local observer discovery target 감사](2026-09-10-local-observer-discovery-target-audit.md) | directed exact match 2 / broadcast response 0 / write 0 | absent 두 장비는 online이며, stale unicast target 2개가 2/6 absent의 직접 원인임을 확인 |
| [2026-09-10 local observer target 임시 갱신](2026-09-10-local-observer-target-refresh.md) | fresh online 6 / absent·ambiguous 0 / write 0 | stale unicast 2개를 current exact-match 주소로 임시 갱신해 6/6 관측 복구; DHCP reservation은 미확정 |
| [2026-09-10 local observer DHCP reservation](2026-09-10-local-observer-dhcp-reservations.md) | reservation 6 / dnsmasq·DNS PASS / device write 0 | current lease 6개를 static reservation으로 고정하고 기존 host 29개 불변 확인; target drift 영구 원인 해소 |
| [2026-09-10 local observer active-template 결속](2026-09-10-local-observer-active-template-binding.md) | template 1 / fresh online 6 / command 0 / device write 0 | current app loader가 선택한 exact Pro template SHA-256을 manifest에 결속하고 observer-only 표면 불변 확인 |
| [2026-09-11 local qualification terminal tombstone interlock](2026-09-11-local-qualification-terminal-tombstone-interlock.md) | terminal parked artifact / qualification write 0 | valid exact-restore parked tombstone을 lease가 non-idle로 오인해 observer stop·device I/O 전에 qualification을 차단함 |
| [2026-09-11 local manual-flow qualification first write](2026-09-11-local-manual-flow-qualification-first-write.md) | same-value send boundary 1 / ACK 0 / lower·planned restore 0 | 첫 frame ACK 증거 부재로 즉시 fail-closed; baseline 40% 확인 뒤 recovery terminal, receipt 0 |
| [2026-09-11 local manual-flow qualification success](2026-09-11-local-manual-flow-qualification-success.md) | write 3 / verified 3 / receipt 2 / final 40% | sequence-confirmed empty ACK body를 보존해 `40 -> 39 -> 40` exact readback과 qualification binding 완결 |
| [2026-09-11 local-control MQTT manual-flow](2026-09-11-local-control-mqtt-manual-flow.md) | MQTT write 2 / verified 2 / final 40% / observer 복귀 | qualified Pro 한 대의 `manual_flow`에 `40 -> 39 -> 40`을 적용하고 durable journal·fresh readback·observer-only 복귀 확인 |
| [2026-09-11 local-control qualified activation](2026-09-11-local-control-qualified-activation.md) | persistent control 1 / fresh online 6 / startup write 0 | qualified `manual_flow` number 1개를 상시 노출하고 journal 불변·retained command 0·group armed 0 확인 |
| [2026-09-11 local-control actual recovery](2026-09-11-local-control-actual-recovery.md) | forward write 1 / recovery write 1 / terminal rollback / final 40% | 지정 readback fault 뒤 production recovery가 stored rollback을 실제 전송해 exact baseline 복원 |
| [2026-09-11 local-control reconnect and bounded soak](2026-09-11-local-control-reconnect-soak.md) | broker loss / restart 0→1 / soak 1,800초 / device write 0 | retained offline을 관측해 supervised restart를 확인하고 command·journal 변화 없이 6/6 fresh 유지 |
| [2026-09-11 local-control external app drift](2026-09-11-local-control-external-app-drift.md) | app Flow 40→41→40 / agent write 0 / final 40 | external drift를 direct fresh read와 retained state에 반영하고 journal·qualification 불변 확인 |
| [2026-09-11 second Pro manual-flow qualification](2026-09-11-local-manual-flow-qualification-second-pro.md) | write 3 / verified 3 / receipt 2 / candidate only | 두 번째 Pro `40 -> 39 -> 40` exact readback과 TimerON 뒤 active schedule `sine`·Flow 45 확인; surface 미개방 |
| [2026-09-12 first Pro D1l manual-flow requalification](2026-09-12-local-manual-flow-requalification-first-pro.md) | precondition write 1 + qualification write 3 / receipt 2 / final 40% | app-declared Flow range에 결속된 새 schema writer로 첫 번째 Pro를 재자격하고 old generation·surface 1 유지 |
| [2026-09-12 second Pro D1l manual-flow requalification](2026-09-12-local-manual-flow-requalification-second-pro.md) | qualification write 3 / receipt 2 / Timer schedule restored | 두 번째 Pro를 같은 새 schema로 재자격하고 현재 slot `sine`·Flow 45 복귀 확인; candidate only·surface 미개방 |
| [2026-09-12 two-Pro qualified-control deployment](2026-09-12-two-pro-qualified-control-deployment.md) | persistent control 2 / HA range 30..100 / fresh 6 / device write 0 | 새 schema receipt 네 장을 단일 generation에 배포해 두 Pro만 열고 discovery exact·journal 불변 확인 |
| [2026-09-12 Bar active-template static binding](2026-09-12-local-bar-active-template-static-binding.md) | app-loader static trace / candidate 2→1 / device write 0 | current Bar product URL에서 도달되는 exact template digest·size를 cache recency 없이 재결속 |
| [2026-09-12 Bar active-template deployment](2026-09-12-local-bar-active-template-deployment.md) | template membership 1→2 / fresh 6 / device write 0 | Bar template을 새 generation에 결속하고 기존 discovery·Pro control byte 불변과 Bar diagnostic-only 유지 확인 |
| [2026-09-13 Bar manual-flow qualification](2026-09-13-local-bar-manual-flow-qualification.md) | write 3 / verified 3 / receipt 2 / candidate only | Bar에서 `45→44→45` exact readback과 planned restore를 확인하고 receipt pair를 surface 미개방 candidate로 보존 |
| [2026-09-13 Bar qualified-control deployment](2026-09-13-local-bar-qualified-control-deployment.md) | persistent control 2→3 / discovery 86 exact / fresh 6 / device write 0 | Bar receipt를 current set에 merge해 `30..100` number 하나를 열고 기존 Pro·journal·receipt 불변 확인 |
| [2026-09-13 first Pro power qualification](2026-09-13-local-power-qualification-first-pro.md) | ON→OFF→ON write 3 / verified 3 / receipt 2 / candidate only | 첫 Pro에서 safer-OFF mapping의 first-send 전 durable 설치와 OFF 구간 비전원 필드 유지·최종 ON 복귀 확인 |

## 새 실행 기록 템플릿

```markdown
# run: <YYYY-MM-DD-run-id>

- 목적(한 문장):
- 이 실행이 답하려던 질문:
- 실행한 commit SHA:
- 사용한 명령·주요 파라미터:
- 종료 지점(stage / failure code):
- 장비가 실제로 돌려준 값:
  - 증거 등급: (a) preserved raw artifact / (b) preserved structured·durable daemon artifact /
    (c) reconstructed operator observation
  - opaque artifact id 또는 안전한 상대 논리 경로:
  - UTC span (시작 / 종료):
  - SHA-256 identity binding:
  - artifact digest (SHA-256):
- 원복 검증 결과(어떤 수준의 확인이었는지 명시):
- **이 실행으로 새로 확정된 사실:**
- 이 실행으로 확정하지 못한 것:
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것:
```
