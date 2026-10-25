# Master-first linked OFF result, 2026-10-25

Result: NOT RESOLVED. User-authorized master-first OFF without role release completed one master OFF with ACK and OFF readback, then one slave OFF without ACK. Slave remained ON/async_slave/feed. No role command or ON command was sent. Scheduler remains paused; Bar remains OFF. The user owns physical inspection and subsequent explicit reactivation. Do not describe this as both pumps stopped.

Actual capture artifact `aqua-maintenance-fix-20261025`, SHA256 `1e0eb0283f770be9c297c898320a8a200542d1272861e3fc819e2cc72aa74990`: master control at 14:44:57.842282 UTC (one control, one ACK, OFF); slave at 14:45:06.638134 UTC (one control, zero ACK, five ON/feed reports). Both use the same OFF payload SHA256 `05b07b76bdb14d4a4001e79f4637a5e12ccd4f62c47f55adf57fde2dfa185194`; no control TCP retransmissions. Slave was already feed before its command, after master OFF.

Production image `sha256:3a9b44228eeded6cc4f71e070e428aca02647c39d042da85198972e3a78f2486`, code commit `8a38df2`; only maintenance.py differs from prior operating image. Native images, active schedule digest and roles are preserved. Slave journal finalized abandoned/target_not_observed_drop after observation, with no unfinished command. No failed send was replayed. The original image, compose, state archive and raw evidence remain preserved privately. Authentication warnings remain zero.

Added regression from this actual outcome: partial OFF remains error, roles remain unchanged, restart and elapsed boundaries do not re-send or power ON. The 25 maintenance tests pass; this is orchestration evidence, not firmware success. The ordering test intentionally only tests order under a conditional rejection model. This later actual result disproves treating that model as firmware behavior.

The HA error observed snapshot precedes the partial command; fresh device inspection is the source for current OFF/ON. Physical flow, a full stop/resume cycle and 20-minute normal-operation success remain unverified. This document supersedes any tentative success interpretation of the deployment plan.

run: aqua-maintenance-fix-20261025
decision: preserve linkage and partial stopped state; report slave OFF failure without further commands
unfreeze: n/a
