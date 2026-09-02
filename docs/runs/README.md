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
