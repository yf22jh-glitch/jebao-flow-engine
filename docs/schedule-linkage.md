# TimerON schedule-linkage diagnostic

`jebao-flow-schedule-test` is a separate, attended diagnostic for checking whether two qualified
Local Wavemaker Pro controllers continue to advance their own local schedules while assigned
native `master` and `async_slave` roles.

It is intentionally narrower than `jebao-flow-hwtest`:

- both controllers must already be independently running with `TimerON=true`;
- the current and next decoded slots, device-local clocks, and `Auto*` evidence must pass the
  audited boundary checks;
- both exact physical bindings need unexpired qualification receipts from the same explicitly
  named qualification operation;
- each device's manual fallback `Flow` and current/next effective `AutoFlow` must be at or below
  both its configured power limit and the fixed 45% attended-test ceiling;
- the only writable datapoint is `Linkage`;
- exit and recovery always detach the slave before the master;
- no qualification receipt is issued by this diagnostic.

The command shares the deployment-wide hardware lease, physical-device leases, emergency latch,
and recovery supervisor with the other hardware workflows. Its two fixed artifacts are direct
children of the private `0700` `/hardware-safety` mount:

```text
/hardware-safety/schedule-linkage-intent.json
/hardware-safety/schedule-linkage.json
```

The first audited boundary set supports current `constant`, `pulse`, `sine`, or `feed` followed
by `constant`, `pulse`, or `sine`. Other native modes remain read-only until their effective
`Auto*` semantics have separate hardware evidence.

The 45% ceiling is checked against the decoded manual fallback `Flow` and schedule data during
preflight, before authorization, journal creation, or any Linkage write. A high fallback value,
current slot, or next slot therefore fails read-only.
The diagnostic remains unavailable until both qualification receipts exist. A later 2026-08-27
low-power Sync bootstrap issued two receipts, but the following Async live-Flow diagnostic failed
automatic rollback and required attended recovery. Schedule-linkage therefore remains
operationally locked despite those receipts. Whether an `async_slave` applies its own `AutoMode`
and `AutoFlow` together at a slot boundary remains unverified on hardware.

The 2026-08-28 one-shot reached the full `master` + `async_slave` topology, but stopped before the
A-to-B boundary because the slave's manual `Frequency` no longer matched its pre-role snapshot in
repeated fresh explicit replies. Ordered automatic rollback completed, two new-session comparisons
matched the original controls and complete schedule images, and Observer resumed with writes locked.
This is evidence of a persistent role-induced manual-Frequency side effect; it is **not** evidence
that the slave did or did not apply its per-slot `AutoFlow`.

## Attended flow

Use a private control configuration with Observer disabled, writes enabled for exactly the two
selected Pro controllers, and `dry_run=false`. Do not start an ad-hoc container: every one-shot
must run inside the Compose recovery service so it shares the same `/hardware-safety` volume and
supervisor.

```bash
docker compose stop jebao-flowd
docker compose --profile hardware up -d --build jebao-flow-recovery
docker compose ps jebao-flow-recovery
```

First capture the read-only boundary preview inside that service:

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-schedule-test --config /config/hardware-test.yaml preflight \
  --operation-id <new-operation> \
  --qualification-operation-id <qualification-operation> \
  --master <logical-master> --slave <logical-slave> \
  --observation-window 180 --minimum-lead 45 \
  --verification-interval 1 --ambiguous-band 1 \
  --maximum-clock-skew 2 --clock-advance-tolerance 2
```

After reviewing the sanitized preview, repeat the exact arguments with the printed token:

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-schedule-test --config /config/hardware-test.yaml run-schedule-linkage \
    <the-exact-preflight-arguments> --confirm JFS-...
```

`SIGINT` and `SIGTERM` request a normal stop. They do not cancel the shielded role-detach sequence.
The result proves controller-register `Auto*` transitions; it does not by itself prove physical
water flow.

The composed `jebao-flow-schedule-flow-test` uses a narrower observation rule than the standalone
command above. Read-only field captures showed that Pro `NowTime` is refreshed in device-specific
batches, so it is retained as a fail-closed admission check during preflight and the final
pre-write gate, but it is not treated as a continuous clock after Linkage roles are active. The
composed test accepts this rule only for the exact temporary two-entry schedule that it owns. It
projects a conservative monotonic not-before window from the final pre-write clock sample and
rejects any earlier B evidence. It then uses explicit query replies to prove an exact master
A-to-B `Auto*` transition and holds one unchanged, safe slave result for two consecutive samples
and the full requested monotonic stability interval. Manual controls, `TimerON`, roles, health,
and the schedule fingerprint must remain exact on every sample. The only exception is the observed
manual-Frequency side effect after the full native topology exists. The composed fixed
Constant(0)-to-Sine test may pin one token-bound candidate only after two consecutive explicit
replies from separate fresh sessions agree. The candidate set is limited to the two captured manual
frequencies, both A effective frequencies, both B frequencies, and raw zero proven by the exact
Constant entries. The initial mismatch does not count. No Frequency write is sent, the pin is
run-local, and any later change fails closed; independent detach and outer restoration still require
the original Frequency exactly. This can answer whether the slave
applies its per-slot Flow, keeps the previous Flow, or follows the master; it cannot prove
subsecond phase alignment or the exact wall-clock instant at which either controller changed.

Safety-critical schedule snapshots, staged-write verification, and restore verification use an
explicit state reply and reject unsolicited reports as proof of the exact 432-byte schedule image.

## Status and recovery

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-schedule-test --config /config/hardware-test.yaml status
docker compose exec jebao-flow-recovery \
  jebao-flow-schedule-test --config /config/hardware-test.yaml recover-schedule-linkage
docker compose exec jebao-flow-recovery \
  jebao-flow-schedule-test --config /config/hardware-test.yaml \
    recover-schedule-linkage --confirm JFSR-...
```

The always-on recovery supervisor may automatically dispatch role-only detach only when the
instance-bound intent and journal match exactly, the mutation scope is `linkage_only`, the safety
latch is clear, and the record is within its expiry plus the 30-second recovery grace. Stale,
mismatched, safety-related, or incomplete terminal state requires attended confirmation.

An intent in `STARTED` with no journal is closed without connecting to a device: `STARTED` is
persisted before writable connection, the journal precedes every role write, and terminal intent
is persisted before journal removal.
