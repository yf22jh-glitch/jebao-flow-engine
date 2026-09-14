# Group OFF/ON bounded commissioning — D1u

Status: **completed FAIL; one-shot authority exhausted**. Prepared after D1t at `c47cbd3`.
See the [committed result](runs/2026-09-14-m4-group-power-commissioning.md).
The historical approval and sequence below do not authorize another execution.
After disclosure that all three wavemakers may remain OFF on failure and require immediate
on-site restoration, the maintainer/on-site approver explicitly answered “ㅇㅇㅇㅇ 진행해줘”.
This binds the temporary deployment, one OFF/ON sequence, recovery, app restoration and prior
deployment return below; it does not replace fresh preconditions or approve other operations.
Existing source and qualified scalar paths remain unchanged. Native ASYNC remains frozen.

## Question and evidence

Can the current three-member group apply canonical OFF once, then establish a lower manual
Flow before each member's ON, using the existing plan-wide journal and qualified scalar writes?
Terminal action order, payload/readback bindings, fresh authenticated state, and canonical
pre/post inventories are the evidence. MQTT completion snapshots are not verdicts. Exact
physical simultaneity, measured water flow, and indefinite suppression are not claimed.

The proposed baseline is all three members ON / TimerOFF / constant / independent / Flow31.
Temporary Constant target30 changes only `default.power` from prior50. Existing phases, gains,
limits, period, qualification, image, and discovery payloads stay unchanged. Timer and mode
preparation/restoration remain user app operations; no unqualified agent path is substituted.

## Expected normal path

| Command | Durable actions | Revision | Physical frames |
|---|---|---:|---:|
| OFF once | Three power=false VERIFIED actions; no Flow action | 10 | Power3 |
| ON once after verified OFF and fresh state confirmation | Per member Flow31→30, then power=false→true; six VERIFIED actions | 19 | Flow3 + Power3 |

Expected total: two records, nine frames (Flow3 / Power6), no qualification receipt mutation.
The ON baseline must actually be OFF/Flow31 for all members. These counts are not asserted if
preparation or OFF-state observations differ. Constant31 would instead produce no-change Flow
actions and would not test a physical Flow-before-power transition; it is not equivalent evidence.
All-member baseline reads precede journal creation; action execution is member-ordered, not
three simultaneous physical sends. Existing coordinator preparation uses each member's one
pre-action status for both intents.
OFF-state Flow writing is new live evidence: prior power qualification observed Flow while OFF,
but did not write Flow in that state. Its additional failure point is not declared risk-free.

## Physical consequence and failure boundary

All three wavemakers can be OFF together between the two commands. OFF has no automatic
ON deadline, and stopping the service does not turn the pumps back on. The approver must be
present and able to restore operation immediately. No unconditional claim that this is harmless
or that past single-pump OFF durations bound this group operation is made.

OFF actions have `restore_on_recovery=false`: recovery classifies fresh OFF or original ON
without sending ON. A failed OFF may therefore leave a mixed state even after terminal rollback.
ON actions prepared from OFF have baseline OFF and ordinary recovery authority; recovery may
send OFF to already restarted members. Flow recovery uses its stored31 baseline. Only the last
nonterminal record may be recovered with the existing config-only qualification recovery CLI;
prior VERIFIED records and the user's full app baseline are not automatically restored.
For a failed ON, successful recovery converges to OFF; if recovery reads or sends fail, state
remains unknown and can be mixed. Neither failure branch permits assuming all three states:
fresh per-device observation and on-site restoration are required.

There is no repeated OFF, ON retry, fault injection, or corrective direct ON in this operation.
After any ambiguous send, unexpected state, or observation failure: stop further test commands,
preserve the journal, and resolve actual state and operator restoration. An already accepted
mandatory OFF can drain during shutdown; stop is not an instantaneous cancellation or an ON.

## Bounded observer and execution prerequisites

- From first OFF intent, apply an internal and independent host observation limit of180 seconds.
  Stop earlier on an extra third record, Flow attempted above3, power attempted above6,
  unexpected identity/target/action/revision/order, nonterminal failure, changed prior/terminal
  records or receipts, lifecycle failure, or observer failure. No extension or repeated command.
  The host timeout is a stop rule, not a guaranteed upper bound on physical OFF dwell; expiry
  requires immediate on-site attention, not an automatic ON. A pre-send superseded action is
  not proof of a transmitted frame; preserve attempted and confirmed counts separately.
- Complete independent static review and bind the selected operation in policy before live work.
- Preserve exact prior deployment and fresh per-device app baselines; prepare the temporary
  generation through production file creation, with source0444 / parent0700 and no rebuild.
- Stop service before user app preparation; verify all three exact identities and fields, then
  restart and require readiness, unarmed state, current qualifications, and unchanged inventories.
- Finalize the observer: no deliberate multi-cycle OFF hold; proceed to the single ON only after
  terminal OFF and fresh power/Flow confirmation. After ON, observe three complete fresh cycles
  without extra operations, then stop. Predeclare failure and attempted-frame accounting.
  OFF confirmation must also preserve TimerOFF/constant/independent and declared extra fields.
  A skipped or unavailable member is not a complete three-member result. Command snapshots
  are auxiliary; post-ON persistence requires fresh complete publication batches after terminal,
  not the retained scalar alone. Unchanged mandatory-OFF hold is not separately proven by this
  short OFF window. Recovering a failed ON has at most six attempted changed actions (three
  Flow and three power); reverse recovery can send up to six frames, never a power ON from
  this all-OFF baseline. OFF recovery emits zero frames when fresh boolean classification works.
- Restore first40/TimerOFF, second manual31 then TimerON with its current active slot, and
  Bar classic91/TimerOFF; compare Frequency34/32/80 and Bar PulseTidefalse against fresh baseline.
  Restore exact prior Constant50 deployment unarmed; preserve actual OFF/readback timestamps,
  app-regime uncertainty, and service absence separately in an append-only execution record.

This file records no device observation and is not a `docs/runs/` result or a PASS.
