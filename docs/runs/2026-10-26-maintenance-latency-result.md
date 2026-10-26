# Maintenance read latency result — 2026-10-26

The earlier baseline is `2026-10-26-maintenance-latency-baseline.md`.
Runtime source commit: `9de5fda` on `fix/maintenance-speed`.

## Change and preserved behavior

Maintenance resolves all three current identities within one unchanged full
five-second discovery window and captures their states with independent read
sessions. Every session is closed/drained on success, failure or interruption.
No endpoint or device state is cached between snapshots. Qualification checks,
raw frames, identity/image/role/power validation and serial control order remain.

Start reuses the immediately preceding validated snapshot before the first
write; unchanged all-OFF holds do not repeat the same read without a write.
Stop combines qualification and its initial fresh snapshot. Linked-slave release,
explicit power resume, original roles, current schedule row and Bar timer
restoration remain in the same order.

## Independent checks

- Frozen existing suites: 101 passed. Candidate: 111 passed.
- Two new latency tests failed on the old implementation before the change.
- Real Maintenance/LiveBackend adapter with delayed fake I/O: start discovery
  12→4; finish 16→9; unchanged hold 4→1. Counts exclude unchanged coordinator
  preflight and the fake plan validator; these are not full-cycle time estimates.
- Ambiguity, missing identity, invalid capture, qualification failure, timeout,
  cancellation and user interruption are covered. All sibling readers drain
  before return/retry. Existing restart, duplicate and partial-failure checks pass.
- Only two old injected crash callbacks accept the added snapshot keyword;
  their assertions are unchanged, and the frozen original tests pass separately.

## Production evidence

The scheduler was deployed at 2026-10-26T01:11:33.373603393Z; both running
source hashes match the candidate. Other controller and Home Assistant were not
restarted. Startup found the current row already applied and sent zero controls.

Two read-only production samples: 12.102 / 12.782 seconds before and
5.949 / 7.023 seconds after (47.9% lower mean). Device identity, power, role,
timer, mode, flow, frequency, native image, faults and custom frequency match.
Raw blobs are retained and verified by hash. Nine saved plan/template files and
31 plan rows are unchanged. No performance test initiated a physical OFF/ON cycle.

Observation: 20.25 minutes, 48 periodic samples,
zero restarts. One normal idle query missed slave discovery twice; the existing
read retry recovered by 01:13:30 UTC, with no control writes. The cause of that
transient loss is unconfirmed; this is not a zero-error claim.

Schedule boundary: 10:27 급여 행 → 10:33 tidal 40/40 복귀 실제 확인. parent2/child4 모두 verified, HA current_row_verified=true.
Final operating/authentication findings: 세 모터 ON·master/async_slave·Bar Timer ON·tidal 40/40·현재 행 검증 확인. 실행 소스/이미지 일치, 재시작0, 인증 경고0. 전체 환수 버튼 새 왕복은 미실행.

## Limits

The historical complete finish took 144.761 seconds. The optimized full button
start/finish has not been timed on physical hardware. The earlier user-confirmed
physical cycle remains historical evidence, not a new-version physical result.
Maintenance-mode process restart was exercised in isolation; the production
restart occurred during normal scheduled operation.

The harness baseline/candidate are preserved. Added tests were reviewed and a
new frozen oracle snapshot was registered. The Jebao harness execution adapter
is not implemented, so no harness execution job/pass is claimed. The isolated
Docker suite and real read-only measurement are separate evidence.

The HA workspace HTML report is
`ha-operations/docs/audits/2026-10-26-aqua-maintenance-speed.html`.
Private raw records, test logs and rollback state remain outside Git.

run: docs/runs/2026-10-26-maintenance-latency-baseline.md

decision: Deploy the measured fresh-read consolidation; retain full discovery,
serial writes, all state validation and raw evidence. Full physical cycle timing
remains pending the next user-triggered operation.

unfreeze: n/a
