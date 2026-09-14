# D1v — Bar-only OFF-state Flow diagnostic follow-up

Status: offline preparation authorized by the user's request to propose and perform a useful
retest after D1u. The concrete Bar-only physical sequence below is proposed, not yet approved
or executed. D1u's one-shot authority is exhausted; its FAIL remains unchanged.

## Question and alternatives

D1u established two Pro successes but left Bar's `send_once` exception unclassified. Empty
runtime logs cannot identify whether it arose before transport, during exchange/ACK validation,
or in the post-send permit check. The old event's original exception cannot be reconstructed.

1. Repeat the three-member group with better diagnostics: closer to D1u's session ordering,
   but again exposes all three wavemakers to OFF and potentially OFF recovery.
2. **Recommended first:** exercise only Bar's existing qualified direct controls while the
   group remains unarmed. This can test Bar's OFF-state Flow write without stopping the Pro pair.
   It is not an equivalent reproduction of group membership, generation guards, connection age,
   or shared group transaction order. A pass does not resolve D1u's cause or make group power PASS.

## Diagnostic preparation, without device writes

### D1u preserved timing reanalysis (no new device/broker contact)

The existing 98,022-byte observer stream (59 JSONL entries), SHA-256
`4b26d62dde51176f2096ad7b4f49d73213b93a142347beb4706cbc2d7b95b562`, was re-read and
its digest verified. For ON record `group_7144de92a67f8e3c106f71725dacbd17`:

| Preserved revision | Journal updated_at (UTC, 2026-09-14) | State |
|---|---|---|
| 12 | 01:34:06.137210 | Four Pro actions VERIFIED; both Bar actions PREPARED |
| 14 | 01:34:06.220774 | Bar Flow UNCERTAIN; Bar power still PREPARED |

The difference is **0.083564 seconds**. Revision13 is absent from both saved record snapshots
and journal-sample entries; do not claim a directly measured rev13→14 duration. The observer
captured the two snapshots at01:34:06.211269 and01:34:06.320501 respectively. In the serial
coordinator path, the Bar attempted save and send failure lie between revisions12 and14.
These wall-clock stamps do not show a five-second wait for this action, assuming no clock step.
The observer and writer may share the host's realtime clock; they are not independent clocks.
They are not monotonic send timing, an exception-type classification, proof of wire delivery,
or grounds to exclude connection closure, protocol rejection, cancellation, or permit failure.
The underlying exception remains UNKNOWN. No timeout/retry change follows from this timing.

### Proposed failure-only diagnostics

- Add failure-only exception-chain type and package-relative traceback locations at coordinator
  catch boundaries, after the existing uncertain journal save. Preserve the original raise/cause.
- Event version1 fixes `request_sha256 = SHA256(request_id.encode("ascii"))`, with **no prefix**.
  This definition must not silently change. The journal request id can reproduce the join key;
  matching it binds an observation to a request, not cryptographically to journal contents.
- Version1's top-level fields are exactly `event`, `event_version`, `request_sha256`,
  `boundary_elapsed_ns`, `revision`, `action_index`, `function`, `diagnostic`,
  `forward_payload_sha256`, `rollback_payload_sha256`, `exception_chain`, and
  `chain_truncated_or_cyclic`. Each chain entry has `type`, `frames`, `frames_truncated`;
  each frame has `module`, `function`, `line`. Changed field contracts require a new version.
- `boundary_elapsed_ns` uses monotonic samples immediately before the protected try and at its
  exception catch, before the uncertain save. The forward send and forward readback are separate
  boundaries. The rollback boundary includes send, ACK blob installation and readback. Thus this
  is catch-boundary elapsed time, not physical frame timing. Unavailable timing is null, never a
  control failure; no absolute monotonic timestamp is emitted. The old D1u timing is not repaired.
- Do not log exception messages, repr, traceback source text/locals, private identifiers,
  raw payloads, or endpoint paths. Bind diagnostics by request digest, action index and payload digest.
- A diagnostic failure must not replace the original failure, change journal revisions, send a
  frame, retry, add a gate, or change recovery decisions. Cancellation remains a failure of the
  original operation, not a retry trigger.
- Use offline code fault injection to verify chained errors, redaction, journal-first ordering,
  and unchanged single-send/recovery behavior. These are not fake firmware behavior tests.
- Keep writer, encoder/decoder, qualification schema, leases and frozen native ASYNC untouched.
  Verify the existing protocol-schema fingerprint remains identical; do not edit its hash inputs
  to preserve receipts. Bind the new source/image separately from the prior deployment.
- Preserve stderr externally as an owner-only artifact before any service replacement. Exception
  class/location can classify a new failure, not prove physical delivery. If exact wire evidence
  is collected, it is passive, target-scoped, private, bounded, and never treated as proof of
  impeller motion. Merely obtaining a pcap is not proof that the required frames were captured.
- The current local daemon does not configure logging; ERROR events reach stderr through
  `logging.lastResort`. A fresh-interpreter test checks actual delivery without pytest's handlers,
  both at the default root level and at ERROR. No `exc_info` or `extra` is attached; only the
  explicit JSON fields are supplied. The observer must remain synchronous, without awaits.
  The standard stderr handler flushes events; the runtime also sets `PYTHONUNBUFFERED=1`.
  These properties do not guarantee durable delivery: the logger is best effort,
  and only an actually preserved and digested capture may be listed as evidence. It is structured
  diagnostic output, not raw-wire evidence or a journal replacement.

## Proposed attended sequence

Do not arm or OFF the group. Keep both Pro configurations unchanged. Stop the service before
the user prepares **only Bar**: ON / TimerOFF / constant / independent / Flow31. Preserve its
fresh original classic91, Frequency80 and PulseTidefalse and both Pro baselines first.
Bar's preparation is a 60 percentage-point decrease plus waveform change, not just a 1-point test.

After the diagnostic image is reviewed and deployed, require current identity/qualification,
readiness, unarmed group, and fresh state. Use existing direct MQTT topics with one publish each:

| Stage | Expected action/revision | Write |
|---|---|---|
| Bar OFF | one changed power action, VERIFIED rev4 | power1 |
| Fresh OFF31 confirmation, then Bar Flow30 | one changed Flow action, VERIFIED rev4 | Flow1 |
| Fresh OFF30 confirmation, then Bar ON | one changed power action, VERIFIED rev4 | power1 |

Expected successful total: three direct records, Flow1 + power2; no Pro action or group record.
Each direct command has a fresh independent transaction/session, unlike D1u's group ON plan.
Verify terminal journals and authenticated state; MQTT accepted/retained values alone are not
persistence evidence. After ON, observe three complete fresh cycles, then stop for app restore.

One failed/uncertain command ends the test: no repeated command, no corrective direct ON,
and no next test stage. Preserve diagnostic output/journal first, read actual state, and recover
only a nonterminal record with the existing config-only recovery path before user restoration.
OFF recovery does not send ON; Flow recovery may restore31 while Bar remains OFF; failed ON
recovery may switch Bar OFF again. Recovery failure leaves state unknown. The user must be
onsite and able to turn Bar back on. The Pro pair is not a substitute for that confirmation.

Use the established internal/independent-host 180-second observation stop limit, no extension.
It does not guarantee a physical OFF-duration limit. Stop on any unexpected target/action,
fourth record, Flow attempt above1, power attempts above2, Pro write, or observer failure.
Superseded attempts are not physical frames; they end this narrow observation as UNKNOWN.

Restore Bar through the app to its preserved baseline, confirm both Pro states unchanged at
fresh observations (second's current active slot, not an old slot), and restore the exact prior
service unarmed. Compare declared fields, inventories, and discovery. Write an append-only
record before further source work. This run answers only the selected Bar/direct question.

If the same question reaches two live attempts or the one-day investigation limit, report the
new evidence and alternatives to the maintainer before another attempt; do not silently repeat.

## Offline verification before any deployment

The initial diagnostic revision passed19 coordinator tests and the full suite
(2,587 tests in106.76s). After the ERROR-level delivery refinement and two fresh-interpreter
tests, targeted21 and `ruff check src tests` passed, but the full run collected2,589 tests:
2,588 passed and1 failed in102.57s. The failure was the unchanged
`test_hardware_test.py::test_live_slave_failure_survives_masking_rollback_failure`, with
`primary_failure=None` instead of `SLAVE_POWER_CHANGE_NOT_VERIFIED`. It is not hidden by the
earlier full pass. That test passed three isolated runs on the candidate and three on the
separate HEAD source snapshot. Those results do not explain the full-run failure, establish
a load-related cause, or prove absence of interaction. Full-suite stability remains unresolved;
no frozen harness/test was edited and no timeout or assertion was relaxed.

A further run of the same final candidate again had2,588 passes and1 failure in102.57s,
this time in the unchanged
`test_device_verification.py::test_success_journals_before_each_write_and_exactly_restores`:
`lower_power_applied` was false at TIMEOUT. The prior Linkage test passed in that run.
The causes of both full-run failures remain UNKNOWN. Targeted21/Ruff PASS must not be
presented as an all-green final full suite, nor does source landing authorize deployment.

| Full-suite run | Source | Passed / failed | Failure |
|---|---|---|---|
| Initial diagnostics | Before two stderr-delivery tests | 2,587 / 0 | None |
| Refined candidate1 | Final diagnostic source | 2,588 / 1 | Linkage primary failure not observed |
| Refined candidate2 | Same final diagnostic source | 2,588 / 1 | First-write lower power not applied before timeout |
| HEAD comparison | Separate `dc4fd52` source snapshot | 2,578 / 0 | None (102.44s) |

The single HEAD pass does not establish that the candidate is unrelated to these failures.
This is a separately unresolved full-suite result-stability question, not D1u's Bar exception
cause. No more repeat-until-green runs are used to clear it. The honest validation status is
targeted21/Ruff PASS with final full-suite failures unresolved. This does not waive existing
deployment prerequisites; any deployment decision must explicitly disclose this state to the
maintainer, separately from HA readiness and approval of a new physical operation.

Tests ran in a disposable network-none container
with only the copied source/test workspace mounted, not production hardware, journal or receipt
roots. A test-only derivative supplied Git, which two existing source-pin tests require.
Before that environment correction, the full suite had2,584 passes and2 failures for a missing
Git executable; that result was not described as a full pass. A first derivative build also
failed for Docker filesystem ENOSPC; no application deployment or device command occurred.

Both the installed source and diagnostic source produce protocol-schema SHA-256
`fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`.
Writer/protocol/journal schema and frozen ASYNC files are unchanged. This preserves the named
schema fingerprint; it is not a fresh audit of live receipt files or authorization to write.
The tests prove code error-handling properties, not a Bar firmware hypothesis or a future PASS.
