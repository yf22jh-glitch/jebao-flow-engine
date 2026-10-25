# Maintenance role release and restore, 2026-10-25

The linked slave-first and master-first failures are preserved in the preceding incident records. Master-first proved that master OFF alone does not let the linked slave accept OFF: zero slave ACK, continuing ON/feed reports.

The user now explicitly requests role release before OFF and original master/slave restoration when turning ON again. This supersedes the earlier instruction to keep linkage. Implement in the deployed local-control maintenance orchestrator, outside the frozen native ASYNC experiment harness. Preserve identities, native images, qualified coordinator/single-send journals, current-row resume and explicit reactivation. Original roles and each pending role transition must survive partial failure and restart; retry observes/reconciles before any new send. Support the existing legacy partial-OFF error state without editing its persistent record by hand.

Actual OFF after releasing roles remains to be demonstrated. User retains subsequent reactivation/cleaning trial. No automatic ON cycle is part of deployment. Physical flow and full real resume are separate from isolated orchestration tests.

run: aqua-maintenance-roles-20261025
decision: implement persisted role release/OFF and explicit ON/role restoration per user request
unfreeze: n/a
