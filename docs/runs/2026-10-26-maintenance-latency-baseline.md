# Maintenance latency baseline, 2026-10-26

The preserved successful finish request was accepted at 2026-10-25T15:10:05.881160Z
and completed at 15:12:30.642160Z: 144.761 seconds. Six child journal intervals
sum to 5.239799 seconds, excluding discovery and baseline reads. The remaining
139.521201 seconds includes discovery, readback, current-row restoration and other work.
Production configuration uses a five-second full discovery window. Its discovery,
resolver, maintenance and scheduler adapter hashes match the reviewed branch.

The user requests improvement after confirming the function. Optimize repeated
read-only maintenance discovery/captures and unchanged-state duplicate reads.
Keep the full discovery window, conflicting-identity rejection, qualified serial
control commands, durable journals, unknown-state handling and explicit finish restoration.
No stale endpoint/state cache and no protocol/writer/frozen experiment changes.
Existing raw evidence and role/current-row recovery tests are retained.

This records the measured slow path before source modification. Isolated delayed-I/O
comparison and live read-only latency will be measured separately from a new live
OFF/ON cycle; user-triggered actions remain distinct from benchmark reads.

run: docs/runs/2026-10-26-maintenance-latency-baseline.md
decision: consolidate same-phase fresh discovery and read-only snapshots without changing serial control semantics
unfreeze: n/a
