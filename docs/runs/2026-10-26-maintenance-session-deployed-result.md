# Request-scoped sessions and parallel pair reads: deployed result

Runtime: 850180f. Started 2026-10-26T05:03:36.290560636Z.
Image: `sha256:6b023eb3544edb02f72cbd9a69e86b3a532cff3b3f00e97dff57fd350818e145`.

The previous fixed-IP build still serialized two GMP captures and repeated
TCP authentication for each writer. This build reads the pair concurrently and
reuses authenticated connections only within one serialized maintenance request.
Each operation still resolves identity, captures state, checks qualifications and
preserves raw data. Writers, commands, leases and journal verification remain
separate; control commands are never replayed by the connection pool.

## Functional and failure evidence

247 related tests passed, including 17 new tests. The real TCP test peer covers
reuse, fresh report selection, unknown raw-frame preservation, timeout before
control, partial-read cancellation, endpoint change, scope cleanup and no control
replay. Existing maintenance, role/power, schedule, boot, resolver, lease, writer
and protocol checks pass. Ruff checks on the added modules pass.

Red evidence demonstrated baseline peak readers1 versus expected2, baseline
authentication2 versus expected1, and an initial candidate selecting a stale
report coalesced behind the heartbeat. The latter was fixed by retaining the
remaining raw bytes and reopening the connection before the next state read.
A further combined raw-storage error and heartbeat timeout test failed on1decdb2.
850180f now propagates the earlier storage failure before reconnecting;
247 tests including that regression pass. The same author reviewed the call paths and failures; no independent agent
review was performed. Native ASYNC frozen files and protocol command payloads
were not modified.

## Real-device read-only comparison

Four reads (pair, three devices, pair, three devices):
- Baseline: 9.104s and 9.391s.
- Candidate: 5.569s and 4.224s; second round reversed the comparison order.
- Deployed build: 4.114s.
- After observation: 4.138s.

Compared semantic states matched; raw captures remain private. These read-only
probes did not create control journals. The timings do not measure or predict
whole maintenance start/stop times.

## Runtime observation

1208.9s observation, 48 samples,
status `observed_no_failure`, restarts 0.
Authentication warnings during the review window: 0.
Current row 14:00, mode tidal,
verified True.

New scheduled control journal phases since final850180f startup: [].
The automatic13:50 and14:00 schedule boundaries, including seven verified
controls, were observed on initial1decdb2 before the explicit storage-error
follow-up deployment. That initial observation lasted17.52 minutes.
Final850180f resumed the14:00 row without writes and was observed anew.
Neither observation replaces a complete maintenance button cycle.
Final-build communication events: []
Initial-build events: [{"outcome": "read_retry", "state": "state_retry", "stage": "state_query", "attempt": 1, "retry_after_seconds": 1, "error_type": "ProtocolTimeoutError", "last_identity_reply_at": "2026-10-26T04:50:04.412526+00:00", "at": "2026-10-26T04:50:09.790763+00:00", "device_writes": 0}]

Recovered read timeouts are retained as observed events. Their cause remains
unconfirmed; successful subsequent rows do not erase them. The automatic
scheduler runs outside the maintenance session scope, although pair
parallelization applies.

## Related branch synchronization

The user requested all branches related to this maintenance change be brought
up to date. The maintenance-fixed-ip, maintenance-master-first,
maintenance-power-off, maintenance-role-resume, maintenance-sessions,
maintenance-speed and scheduler-boot-resume branches share ancestor history.
Synchronize all seven local and remote refs to this final documentation commit
using fast-forward-only ref checks and one atomic push. Keep the currently
checked-out VSON branch and its worktree unchanged. Final ref equality is
recorded in the HA operations audit after the push succeeds.

## Preservation and limits

Three deployed module hashes, image, loaded config and unchanged private config
were verified. 9 saved plan/template
files remain identical. Final reads confirm original device schedule images,
master/async_slave roles, all three powers ON, no faults and Bar timer ON.

Source hashes:
{
  "/usr/local/lib/python3.12/site-packages/jebao_flow/local_control/maintenance_sessions.py": "915e4e46f35b7d506f766862bc838fcda92884a75bed85e5d66807bc71d4b95e",
  "/usr/local/lib/python3.12/site-packages/jebao_flow/local_control_external_manual_cli.py": "ff59d79c12ff4f25835d72d8235221d4506328d8e95eb332cf0ba4676eddb785",
  "/usr/local/lib/python3.12/site-packages/jebao_flow/local_control/external_manual_editor.py": "e1e73bc7b06788a530386eea7df1422a772dc9da0dc853a36f73f25da0b3064c"
}

The isolated MCP adapter is not implemented for Jebao; no verification job was
reported as passed. Baseline and candidate snapshots remain preserved.
Previous runtime517fdec, compose, config, sources, scheduler backup and raw
evidence remain available for rollback.

A full start/stop with physical motor and water-flow confirmation, and an
interruption of a live maintenance operation, were not performed on this build.
Earlier20–30s start /30–45s stop figures remain estimates, not measurements.
HTML report and latest checklist are in the HA operations repository:
`docs/audits/2026-10-26-aqua-session-optimization.html` and
`docs/verification-checklist.html`.
