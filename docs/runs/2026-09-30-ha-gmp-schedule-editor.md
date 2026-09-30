# 2026-09-30 — HA GMP time-table editor

## Scope and result

User requested a schedule-authoring UI. Added Aquarium view `gmp-schedule`
(**시간표**) with the installed 31-row external GMP plan. Upper/lower bindings
and Master/Async roles remain fixed. MCP Bar's native schedule is unchanged.

Draft and active plans are separate. Save sends zero physical commands;
activation is explicit through the existing coordinator/parent ledger. Pause
leaves the last manual state. Pulse/Sine expose Frequency, Tide Rise and
Fall/Constant do not. Feeding inserts stop and running rows six minutes apart.

No frozen harness file/test was modified. No HA restart or direct `.storage`
edit. No raw packets, private endpoints or credentials in this record.

## Preserved deployment evidence (grade b)

- UTC deployment span: 2026-09-30T04:42:30Z–04:43:59Z.
- Runner artifact: `pro-schedule-editor-20260930T044230Z`.
- Image: `sha256:2e40aec2bb017db6b9ba0a07ad53a619acce41c8617502aacf959e5618330d30`.
- Prior image retained: `sha256:76b90f503384351b8c25b0530793a30a306b36e6b9d69a40580d88ea787c3d7e`.
- Active plan unchanged: `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`.
- Active state byte-exact at handoff; digest
  `95888cf1719966cb9793ef1898b7f98e8d575c547f60a822841a474478753691`.
- Plan byte-exact; backup digest
  `7a9289b5cf46988b4682f59ff0724bb5a9028c465196f026343ad5cd12c8d8ef`.
- Parent operation count 25 before/after handoff; zero new row operations.
- HA artifact: `aqua-schedule-ui-20260930T044359Z`.
- JS HTTP readback SHA:
  `6749d2ed62d5c7045c690d63ef549ae24eaf08b110b414ae21ab96420a3c9fff`.
- Dashboard backup SHA:
  `2fa366ce49a82b415b51ff4e87028fead6efebcbbafb726aa436e34d9974df99`.
- Resources backup SHA:
  `d8a83bde3beaa0b397aa6478440c18e37985c6e91f5f03d33456ac593838c4d3`.
- HA release receipt SHA:
  `fd3a0290dd28ca5c34dee264c5d8e26a3e665ec1d65a987bb5d7a0f7d1eb1b3c`.
- Seven views after addition; prior views preserved by API readback.

## HA save round trip (grade b)

An identical-row save through HA MQTT (QoS 0, non-retained, no retries)
completed `saved / device_writes=0`. Request `1248a16b3297058746d456d5d780066e`;
durable request SHA:
`b8c5a39530e2e51e5f5279edf434e2f3d4c05562dcec685c9da74afec285e334`.

Active state remained byte-exact at the digest above; draft stayed the original
plan. Operation count became 26 solely because of the UI request. No actuator
row/child was created. Runner active, restart count 0.

## First continued automatic boundary (initial grade b; raw verification below)

At the existing 13:50 KST boundary, the new runner processed the unchanged plan:
Master release → Async upper Flow43 → Master rebind, three verified children.
Final pair: lower Master Pulse/Flow40/F80, upper Async Pulse/Flow43/F80,
both powered, both TimerOFF, both fault-free. No modified plan was activated.

Parent key `pro_manual_20260930_0830_e60eb4dd952dffcc`, phase `verified`, SHA
`13ae196fd4e62be4c9ece67498cc4d6c4b6de5f871aa8137848a3cd34fed51a5`.
Image baselines remained:
Master `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`,
Async `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`.
Next boundary persisted as 14:00 KST; runner restart count 0.

## Verification and limits

59 existing scheduler/editor tests passed in a network-disabled test container.
Covered save without hardware, active state preservation, explicit activation,
duplicate/crash replay rejection, stale drafts, concurrent pause ordering,
retained/duplicate/oversized ingress, and explicit Feeding exit. These tests
model code ordering, not a new firmware hypothesis.

JS syntax and editor validation/at-most-once tests passed. Existing overview
freshness/schedule/Inkbird tests remained green. Ruff and `git diff --check`
passed. Static review checked bindings, journal-before-write, runner exclusivity,
non-cancellation of physical children, and unchanged old CLI routes.

The actual custom card rendered in Orca at 1280px/390px without page overflow.
DOM events verified separate fixture Save/Activate and Tide's hidden Frequency.
The fixture is not hardware evidence; HA resource/config and save round-trip
readbacks above are separate evidence.

No edited plan was activated for deployment. Its activation path was tested
using the existing ordering model; the actuator has separate live records.
Five currently supported external GMP modes are exposed, not guessed support
for other modes. MCP Bar's native time table is not rewritten by this UI.

## Raw-artifact verification append

The final boundary frames are actually present as two private 462-byte blobs.
Both content digests were rechecked through the production journal's blob reader:
Master `69abcb0d70de75fbf437d0fc2ca443b898ac8f395c0c439e6d325336e7861f4e`,
Async `06489ee0c876cd8988f4a918271636bb45836f0d5dac2fca29cd30878ef857a5`.
This establishes preserved raw evidence (grade a), separate from the fixture UI
and structured claim above. Original frames remain private, not in the repository.

Offline re-decoding of those preserved frames confirmed Master action 3 and
Async action 4, the Mode/Flow/Frequency/roles/power/Timer values above, and exact
image digests. Parent creation time is 2026-09-30T04:50:07.176929Z; subsequent
HA readback at 04:57:12Z still showed the 13:50 row verified and the original
31-row plan active. No second activation was issued.
