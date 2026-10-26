# Fixed-address identity optimization: deployed result

Runtime `517fdec`, deployed `2026-10-26T02:34:49.456240182Z`.
Image `sha256:4bc3d232e8ad67cc11a9aedf081beb476d18e57fb3a5b090bada533a6bdca392`. Actual running source hashes match both
reviewed modules. Only the scheduler-specific private configuration copy gained
three router-verified fixed addresses; the original configuration and the other
local controller were unchanged.

## Cause and change

The three devices had static DHCP reservations, but the scheduler configuration
had no addresses and each resolver request waited the full discovery window.
The new fixed-address resolver sends the existing read-only identity query to
one configured peer and returns on its valid reply. It verifies the same current
MAC/vendor ID/product binding on every call, stores all raw replies (including
unknown frames), and does not cache identity, firmware metadata or state.
Unconfigured devices keep full discovery. Missing replies retain the existing
five-second timeout and read retries; a mismatched identity stops before control.

Scalar frames, writer/coordinator checks, serial control order, ACK/readback,
maintenance role detach/restore, power, native schedule images and current-row
resume logic are unchanged. TCP control sessions still open per operation.
No frozen native ASYNC file was changed.

## Evidence

- Pre-change real-device direct identity replies: six samples, 3.5–125 ms.
- The regression uses real UDP sockets and the actual decoder/LiveBackend wiring.
  Old code took 0.357 seconds despite a 20 ms reply with a 0.35 second discovery
  window, failing the 0.20 second bound.
- 121 related tests passed: previous 111 unchanged plus 10 UDP/coordination tests.
  Delayed replies, silence after prior success, mismatched identity before any
  writer, malformed raw preservation, cancellation/drain, mixed fixed/fallback
  lookup and original maintenance/scheduler behavior were covered. Ruff passed.
- Same-state real reads before: 7.045 and 6.417 seconds; candidate: 1.957 and
  2.108 seconds. All semantic fields matched and control journals were unchanged.
- Actual deployed reads: 1.886 and 2.042 seconds. Master/async_slave roles, both
  GMP motors ON/timer OFF, Bar ON/timer ON and native images were retained.
  The final post-observation read took 2.197 seconds with the same semantic state.
- Startup writes: zero. Nine saved plan/template files unchanged, 31 plan rows.
- Observation: 1202.226 seconds / 48 samples;
  `stable_with_recovered_read_retry`; restarts 0.
- One ordinary state-query ProtocolTimeoutError occurred after current identity
  replies. Existing retry recovered, HA communication cleared and current-row
  verification remained true. The precise cause is unconfirmed; no control write
  was made by the retry. This observation is not an error-free claim.
- 11:40 and 11:50 scheduled rows verified. Their tidal40/40 settings matched the
  preceding row, so these are no-write boundary observations, not physical mode
  transitions. HA reported the current row verified; authentication warnings: 0.

## Limits and recovery

No new maintenance start/finish was triggered. Its total button-to-completion
time and physical water flow remain unmeasured on this build. Earlier 77/110
second estimates belong to the prior 9de5fda optimization, not this deployment.
Do not apply the component-read improvement percentage to a complete cycle.
Live interruption/restart while maintenance is enabled was not repeated.

Harness baseline/candidate and reviewed additive tests are retained. The reviewed
run is `run-20261026T023255Z-827a5b06f380`; Jebao has no isolated execution adapter,
so its status is `adapter_unimplemented_not_pass` with no verification job.
Separate Docker tests and production evidence are not represented as a job pass.

Private raw observations, logs, hashes, timings and journals are retained in the
workspace diagnostic dossier `aqua-fixed-ip-optimization-20261026`. The previous
9de5fda image, original configuration, source, compose and scheduler archive are
retained on the host for rollback. No IP, MAC, device ID or credential is in this
run document. The HA operations report and checklist link the current evidence.
