# Maintenance role release and resume result, 2026-10-26

The authorized repair is deployed as implementation commit `04345f8`. Only
`local_control/maintenance.py` changed in the runtime image. The original dashboard
finish command remains `maintenance_off`. Frozen native ASYNC experiments were not changed.

## Actual results

- Original linked slave OFF failure and master-first failure remain in their original records.
- Repair request released both GMP roles and completed the remaining slave OFF. Master and Bar
  were already OFF, so no redundant OFF was sent to them.
- The user clicked the dashboard finish button. Master, slave and Bar ON each received ACK and
  fresh state. Slave returned to async_slave, master to master; current 00:00 sine row
  (lower 40 / upper 35, frequency 80) and original Bar Timer ON were verified.
- Finish was verified at 2026-10-25T15:12:30.642160Z. User subsequently stated
  **“세 모터 모두 정상이에요”**, confirming normal physical operation of all three.
- The user then started maintenance again from normal running. Bar Timer OFF/Bar OFF,
  master/slave independent and master/slave OFF all completed with ACK and fresh readback.
  At final observation, three motors remain OFF, both GMP independent and scheduler paused.
  No automatic finish or extra ON cycle was sent by the agent.

## Evidence and preservation

- 80 isolated Python tests passed, including linked slave rejection on the old logic, legacy
  partial OFF, interrupted role changes, unresolved journal no-replay, restart/no automatic ON,
  current-row resume and actual ReadRetry interruption allowing the queued dashboard finish.
- Exact registered production JS passed finish action/correlation, duplicate click, successful
  pending reset, offline/reconnect, desktop/mobile checks. Offline UI tests sent no live commands.
- Nine first-cycle child commands each have one unique application control frame plus ACK and
  readback. Three duplicate TCP segments are retransmissions with the same TCP sequence and
  frame hash, not additional application sends. Six further normal-start OFF commands have
  verified qualified journals and ACK/readbacks.
- All 31 saved plan rows are unchanged except the resume epoch; GMP and Bar native image
  digests match their originals. Identity and qualified coordinator protections remain intact.
- Earlier hold observation stopped on ReadRetryInterrupted when the user's finish request
  deliberately interrupted its read-only query. Finish then completed successfully. A later
  observer stopped because the user started a new maintenance cycle; this is accounted for
  separately from unexpected device failure. Original observation outputs were retained.
- Follow-through reached 20.37 minutes after verified finish, ending 2026-10-25T15:32:52.703327+00:00.
  Its 31 periodic samples had no unexpected error/failure, container restart or image change.
  Earlier state/request/capture observations cover the transition; this is not a claim of
  uninterrupted polling across the entire period. Final authentication warnings: 0.

## Remaining limits

The next real 05:57 KST schedule boundary has not occurred. Current-row restoration was
verified. Process restart during maintenance is covered by isolated regressions, not a second
live restart. The latest OFF reports are device confirmations; physical shaft/flow stop and
mains isolation were not explicitly confirmed by the user. The user confirmed all three motors
normal after ON. The local workspace harness has no Jebao execution adapter, so it is not marked
passed; candidate/check evidence and independent tests/live evidence are retained separately.

## Deployment and recovery point

Runtime image: `sha256:cac2a674e573ed3e693fd05a5014d35301199e466b8482e687b7ac182bace91c`.
Runtime source SHA256: `9e906ed7e716ccb8063d57bcfae1da7b5519fdaab25b216f71bdeedbbc645200`.
Previous image, compose, source and scheduler state archive were preserved on the home server.
HA, recorder and unrelated controller were not restarted or cleared for this repair.
The original user's work branch remains unchanged; the fix has its own review branch.

run: aqua-maintenance-roles-20261025
decision: accept demonstrated OFF and explicit finish role/current-schedule restoration; preserve time-boundary and restart limitations
unfreeze: n/a
