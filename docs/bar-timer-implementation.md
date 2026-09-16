# Bar timer: app-derived command contract

Status: command planner/writer, four-action qualifier, and CLI implemented and offline-tested;
**hardware acceptance,
qualification, executable promotion, and deployment are not yet complete**.
The current running Pro generation is unchanged. Return pumps and dosing are out of scope.

## App evidence

The bound Bar template has SHA-256
`e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`.
Two independent readers traced its actual standalone handlers, not just catalog labels:

| Operation | App command |
|---|---|
| Program ON | TimerON=true, FeedSwitch=false, Linkage=independent, plus AutoMode/AutoFlow/AutoFreq/AutoPulseTide from the current app-local schedule slot |
| Manual/OFF | TimerON=false, Linkage=independent, fresh Flow/Frequency/Mode, PulseTide only when current manual Mode is Classic |

Neither handler writes SwitchON. The linked-master editor's `onProgramSave` is a different
operation and is not used for this standalone control.

The release app excludes EE-filled slots but keeps all-zero slots. It appends generated gap rows,
uses inclusive boundaries, treats an end hour of zero as 24, and selects the last matching row.
Gap rows can override real rows. `bar_timer.py` preserves those observed app-source rules;
it does not substitute the generic display decoder's different unused-slot policy.
Direct evaluation of the preserved app's `At/_t/It` against 156 bounded planner inputs yielded
156 identical command seeds. This proves app-source equivalence for those inputs, not firmware
acceptance or physical output. Clock input is an explicit configured host-local minute, like the
phone's local time; a device clock difference is not hidden or made an old-snapshot admission gate.

## Persistent state versus changing seed values

ON sends Auto* seeds in the same frame as the persistent timer command. They are not generic
user-addressable scalar targets. The old general Auto* scalar assertion remains intact.
Verification and rollback contain **only persistent controls**; no old Auto* snapshot is compared
or replayed. A later slot can change Auto* without converting a successful timer flag write into
a failure. Conversely, flag verification does not prove that firmware accepted the seed or that
physical flow matches a request.

Every runnable schedule slot is checked against configured output bounds, including future slots.
The fresh manual output must also fit those bounds because restoring TimerOFF exposes it.
If the original timer is ON, the current active running AutoFlow and the schedule must fit the
same recovery envelope. This is a safety-range check, not equality with a previous observation.
**Bounded schedule bytes do not imply that stale Auto* values are bounded.**

The writer requires explicit timezone-aware local time and per-device bounds. The service factory
binds them from the private instance timezone/device limits; container UTC is never silently used.
The planner is included in the protocol-schema fingerprint along with the writer. The app catalog
also now declares the actual Auto* command fields. Existing receipts cannot be reused under either
changed content binding; the running old image continues using its original matching bindings.

## Hardware activation still required

The current inactive original schedule includes Random100. It must not be enabled under the
bounded qualification authorization. The proposed attended qualification saves the original
384 bytes, applies 48 contiguous half-hour Constant40 slots (no zero-row/gap ambiguity), sends
ON and OFF once each, then restores the original bytes with TimerOFF. Its durable rollback must
always use the operation's initial TimerOFF controls, including for the OFF action; it must never
automatically re-enable the temporary timer while undoing a failed qualification.

Successful completion means TimerOFF, the original schedule image, and app-equivalent manual
controls. The app clears an inactive PulseTide=true when the manual mode is Constant. That
normalization is reported, not converted into a new admission gate, and is not described as an
all-controls byte-exact restoration. Recovery, separately, retains the operation's exact owned
baseline controls. Active feeding is outside this qualification operation because ON clears
FeedSwitch while the normal app OFF command does not restore it.

The qualification CLI uses `run --function schedule_enable --trial-flow 40` with the existing
private current-config/manifest/template/output arguments and explicit `--write-enabled`.
There is no timer command available through HA until the attended qualification and separate
executable promotion have actually completed. All four forward actions are journaled before the
first send; the acknowledged-but-unverified ON test confirms receipt0 and OFF-before-image recovery.

Only ON/OFF may yield timer receipts; the temporary image setup/restore is not a schedule
qualification. Failed/uncertain operations emit no receipts. ACK or a byte-copy test is not a
physical-safety proof. New attended approval and source-matched hardware qualification remain
required before exposure. Pro renewal is separately disclosed because the content bindings change.

The preceding unrelated Bar manual-mode qualification stopped after ACK and was restored using
the existing scalar recovery. Its cause remains UNKNOWN; the new code does not retry it or turn
that result into PASS. Existing failure diagnostics are now also called by scalar qualification,
preserving exception type/code location without exposing exception messages or private endpoints.
When a readback was received, a second diagnostic includes expected values, observed named control
values, and the status digest. Its private content-addressed bytes are saved best-effort and the
log explicitly says whether that preservation succeeded; this observer adds no admission gate.
See [the preserved run](runs/2026-09-16-bar-qualification-mode-stop.md).

## Offline verification

The full local suite passed with 3,188 tests and one platform skip (the Linux boot-ID /
CLOCK_BOOTTIME test on macOS). After the final diagnostic wording adjustment, the timer
qualifier, catalog, import boundary, and qualification CLI tests passed again: 73 tests.
The timer qualifier's 16 tests include an acknowledged ON followed by a failed readback,
receipt suppression, and generic recovery that writes OFF before restoring the original image.
These results establish software behavior only; they do not replace the pending attended run.
