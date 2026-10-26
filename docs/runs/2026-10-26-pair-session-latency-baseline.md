# Remaining pair/session latency baseline

Purpose: determine whether remaining pair serialization and repeated TCP setup
can be shortened without dropping fresh response, control or raw-data checks.

Read-only probe on runtime517fdec: 2026-10-26T04:33:43.433369+00:00. Control journals unchanged.
Pair read2.108s; three-device parallel read1.719s. GMP connect/auth costs were
0.003/0.893s and0.209/0.808s; Bar0.003/0.010s. Three reads per authenticated
connection succeeded: master action3, slave action4, Bar action3. No buffered
bytes were present before these short repeat probes. Raw state captures are
preserved privately, not committed. This steady-state sample does not prove
freshness after commands, cancellation, reconnect or longer unsolicited reports.

Artifact: session-probe-private.json; SHA256 0ae4f6981ca011b46c1fb82d421bcf84aef6ca1b80aae16490dfa57896aff507.

The real LiveBackend pair-path test with delayed reads fails on the baseline:
peak active readers1, expected2. Both decoded values and raw preservation already
match. This exposes code orchestration, not a fabricated firmware behavior.

Existing fixed-IP identity, serial writes, role/power/timer/schedule confirmation,
raw storage and recovery remain required. Native ASYNC frozen files are outside
this optimization. Further session reuse requires explicit freshness/lifetime
proof; repeat successes alone do not establish that contract.
