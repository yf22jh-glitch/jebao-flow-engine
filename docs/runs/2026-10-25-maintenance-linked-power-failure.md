# Linked maintenance power failure, 2026-10-25

Result: two user maintenance requests stopped at the upper GMP scalar power-OFF step. The Bar was OFF; both GMP reports remained ON. No lower GMP OFF was attempted. Runtime source matches the deployed scheduler-boot-resume build.

Evidence: private capture artifact `aqua-maintenance-20261025`, interval 14:18–14:23 UTC, SHA256 `464519d7cad1142650a88bae6ee49e93688775b99195adf5b422feb98b63486d`. Failure streams 47 and 69 each contain exactly one control frame, no control retransmission, zero ACK, and continuing ON reports with the upper GMP in async_slave. The identical OFF payload excluding sequence has SHA256 `05b07b76bdb14d4a4001e79f4637a5e12ccd4f62c47f55adf57fde2dfa185194`; historical independent/master observations accepted it. Actual report replay also reproduces the timeout. Captures and coordinator terminal journals are preserved privately.

Decision: repair the deployed maintenance orchestrator. Persist the original pair roles and each intended transition before sending; release master then slave before individual power commands. Restore slave then master roles on explicit resume before activating the current schedule row. Keep identity, image, output range, native TimerOFF, single-send and durable reconciliation checks. Test legacy error recovery, interrupted role transitions, missing ACK, restart, duplicate requests and explicit resume. Frozen native ASYNC experiment files are outside scope.

The user explicitly requested correction, deployment and both GMP OFF, leaving explicit reactivation and another cleaning trial to them. No automatic ON test is authorized by this operation. Actual OFF after detaching and physical shaft/flow confirmation are still pending at this incident record. Unit models establish orchestration only, never firmware behavior.

run: aqua-maintenance-20261025
decision: repair deployed maintenance role/power ordering; retain all original images and journals
unfreeze: n/a
