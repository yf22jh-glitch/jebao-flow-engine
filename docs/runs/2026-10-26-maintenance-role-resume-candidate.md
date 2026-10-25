# Maintenance detach/OFF and dashboard finish/restore candidate

User instruction: cleaning starts by releasing linkage and turning OFF; the dashboard cleaning-finish button powers ON, restores the original master/slave pair and resumes the current schedule row.

Only the deployed maintenance orchestrator changes. It persists original roles before any role command, and persists exactly one intended pair transition before sending. Fresh observations must match the last verified pair or this pending pair; identity/native-image/timer/output checks remain. Master then slave release precedes independent OFF. Explicit finish releases any legacy/partial pair as needed, powers ON, restores slave then master and activates the existing current-row scheduler. The normal scheduler's owned temporary master release is also recorded across interrupted resume. Unknown roles are rejected. Coordinator authority and single-send journals are unchanged; no frozen native ASYNC file is changed.

The recorded linked-slave rejection makes the new role regression fail before the fix. The candidate passes 79 isolated maintenance/external-scheduler checks: OFF/ON role restoration, legacy partial-OFF migration, applied/not-applied cancellation at either role release/restore, no replay on restart, unresolved journals, unknown roles, scheduler-interrupted release recovery, current-row activation and unchanged native images. Old role-preservation assertions were updated for the user's newly requested semantics; the captured legacy failure is retained as a fixture that skips the new release step. No original evidence is deleted.

The HA workspace candidate flags test changes and has no Jebao execution adapter. Separate isolated tests supply code evidence, not physical proof. Actual detached OFF is pending deployment; user owns the later dashboard finish/cleaning trial. The existing button already dispatches maintenance_off; no HA frontend resource replacement is needed.

run: aqua-maintenance-roles-20261025
decision: deploy persisted role transitions and preserve stopped state until dashboard finish
unfreeze: n/a
