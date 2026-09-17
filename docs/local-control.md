# Local-first control runtime

## What is implemented

The local-control track implements the product path without depending on the Jebao cloud or on
the legacy native-linkage harness. It includes:

- a complete English catalog for the five registered product families and the audited app shell;
- stable device binding by `physical_identity_key`, `product_key`, and an operator-attested logical
  role;
- fresh endpoint discovery for every operation, with IP addresses kept only inside the active
  transport object;
- a separate owner-only write-ahead journal with immutable forward, rollback, baseline, ACK, and
  readback blobs plus an explicit per-action recovery policy;
- the deployment-wide operation lease and the existing per-device physical lock namespace;
- deterministic scalar writers for the audited schemas, with no `Auto*` status field exposed as a
  write target;
- exact Local Wavemaker Pro schedule-image writes;
- Sync and Async schedule composition in which every slave receives the current master's exact
  48-slot timing/mode image and may independently override Flow for each active slot;
- one-shot and recurring plan-atomic software-independent group actuation under one all-member
  lease and one durable transaction;
- English Home Assistant/MQTT entity contracts, fresh read-only state polling, and a sequential
  command adapter;
- fresh install, systemd staging, and standalone Docker Compose commands that replace, rather than
  merge, prior generated artifacts.

The current Home Assistant boundary is the built-in MQTT integration consuming native discovery
published by `jebao-flow-local`. It does not install or configure
`custom_components/jebao_flow`. That component consumes the frozen `jebao-flowd` aggregate
`system/config`, `groups`, `devices`, and `requests` contract and is not an adapter for the
`topic_prefix/local/...` contract below. Running both paths would create two physical-device
authorities without a shared journal or per-device lease, so the legacy daemon, component config
entry, and custom card remain inactive during current deployment. A card-only native-discovery
adaptation is a separate parked product slice.

This is a local-first controller plus a passive observatory, not an inline transparent relay. The
observatory can retain app/device traffic from a mirror without becoming a writer, while Home
Assistant commands terminate at the local controller. The current implementation does not arbitrate
simultaneous vendor-app and local-controller writes through one MITM data path; an approved live
local-control operation therefore still requires the vendor app and other writers to be quiescent.

Home Assistant current state does not depend on the passive mirror. None of the five registered
devices has an observed unsolicited status push, so a mirror can go quiet while a device remains
online. The primary state source is instead one fresh discovery followed by one authenticated
read-only LAN session per device. That session type has no control-send method. Each session reads
once and closes; it neither imports the control encoder nor reuses a previous endpoint. A passive
capture remains useful corroborating evidence about which client caused a change, but never makes a
cached value current.

Fresh reads share the existing stable per-device lock in read mode. They do not take the
deployment-wide write lease and therefore do not turn polling into write admission. A writer's
exclusive device lock prevents a poll from observing its intermediate state. Polling is
single-flight with one connection attempt per device and cycle. Failure causes bounded backoff,
publishes no old state, and marks only fresh-confirmed devices available; it is not retried in a
tight loop. Concurrent vendor-app session capacity remains unknown, so inability to authenticate is
reported as unavailable rather than being hidden by reconnect churn.

Before that shared lock namespace is used, the poller opens the fixed safety mount and verifies its
existing identity marker without running the writable mount probe. An absent or mismatched root is
reported unavailable before any directory can be created. Once the current mount is identified, a
missing per-device lock may be created there as coordination state; its existence is never treated
as prior device evidence or an installation input.

An encoder being deterministic means only that it can produce bytes. A private control deployment
may expose an executable function by listing it in that device's `identity_bound_controls`. The
runtime binds the list to the freshly derived install manifest's exact product and
`physical_identity_key`; a different physical device receives no command topic and is rejected
again before journal creation. The list is effective only with `runtime.mode: control`,
`runtime.dry_run: false`, the invocation's `--write-enabled`, and that device's
`allow_hardware_writes: true`.

This identity-bound path does not bypass output ranges, single-send handling, the deployment-wide
lease, the durable journal, or rollback/recovery. It also does not turn a function without a sender
into a control: the Dosing Pump remains read-only, and Aquarium `transport_mode` remains excluded
because the runtime has no handover/return implementation. Legacy version/content-bound control
bindings remain readable for existing deployments, but they are optional and are not required by
an identity-bound deployment. Public catalog and HA output expose only counts, never physical
identity keys.

## Priority schedule behavior

The priority path is `slave_per_slot_flow_extension` for Local Wavemaker Pro. There is no vendor
datapoint for "inherit master schedule" and there is no per-slot slave Flow editor in the app.
Inheritance is therefore a local daemon composition, not a firmware claim:

1. Resolve the master and slave from a new discovery using their stable physical identity keys.
2. Read the master's current 452-byte status and extract its current 432-byte schedule image.
3. Read every selected slave's current 432-byte schedule image in the same operation and bind each
   image by its stable physical identity key, never by discovery order or a positional cache.
4. Copy every master slot byte to the slave image.
5. Preserve each slave's current Flow byte for every active slot unless that slave has an explicit
   override for the slot. A slave slot that is currently unused starts with the current master Flow.
6. Build one exact 48-slot control payload, journal the forward and rollback bytes, send it once,
   and require exact readback.

This operation is available for both `sync` and `async`. It never reads a stored schedule or an old
install manifest. A Home Assistant projection creates 48 independent `30..100` Flow number entities
for every slave whose own physical identity has two verified receipts. The master is a fresh
read-only composition source, so it does not need a write receipt for this operation. An inactive
master slot remains visible as a position but is not written; the current master image decides
activity at operation time.

The app-visible common slave `Flow` remains a separate scalar function. It is not merged with the
per-slot extension. Top-level `AutoMode`, `AutoFlow`, `AutoFreq`, `AutoFeedTime`, and related fields
are readback status only and are absent from the local writer allowlist.

Untouched app-produced bytes are not reinterpreted. In particular, a Feeding slot with Flow `0`
and a feed duration above an older decoder's `60` limit is copied exactly. The local path does not
use strict full-image validation as a snapshot or rollback prerequisite.

## Current-value rule

The generated install manifest contains no address, PID, hostname, container id, session id,
ephemeral port, cache path, cache directory name, file mtime, or previous auto-detection result.
Those values are neither mappings nor approval gates.

Every install-bearing invocation, including `prepare-install`, `prepare-service`,
`prepare-compose`, `ha-schema`, and `serve`:

1. reads the current owner-only private YAML configuration;
2. hashes the current template `index.js` contents, without using path, directory name, size, or
   mtime as identity;
3. hashes the currently installed protocol sources;
4. creates a new random generation and current `rederived_at` value;
5. writes a complete new manifest by atomic replacement without parsing the old bytes.

Runtime network endpoints are newer still: every command performs fresh discovery. A changed IP or
other transport allocation therefore cannot produce an "old manifest value" failure. Durable
qualification receipts and unfinished rollback journals are intentionally different: they are
safety authority, not auto-detected environment values, and are never silently rewritten.
If a retained qualification no longer matches the freshly hashed app catalog, template, schema, or
audited app version, it is ignored and reported as `qualification_not_current`; the current HA
diagnostics still start, while the old receipt cannot expose a control or turn into an install
failure.

If no current template root is supplied, preparation still succeeds with an empty template binding
and reports `no_current_template_binding_controls_remain_diagnostic`. It does not guess a cache
directory and it does not inherit a root from an older manifest. `--role-attested-by` remains an
explicit operator statement; it is never filled from a username, hostname, or another machine
attribute. A generated service carries the operator's actual attestation time instead of inventing
a new confirmation time on every automatic restart. It also carries a private content digest of
only the stable logical-member, logical-role, product, and physical-identity bindings. Each restart
rederives those facts from the current config and refuses to reuse the attestation if that stable
role mapping changed. Runtime address, PID, hostname, path, mtime, and service generation are absent
from that digest.

## Installation variables

The entry point is `jebao-flow-local`. Private paths can be supplied by environment so they do not
need to be repeated in shell history:

```bash
export JEBAO_FLOW_CONFIG="${CURRENT_PRIVATE_CONFIG:?set the current private config path}"
export JEBAO_FLOW_LOCAL_INSTALL_MANIFEST="${CURRENT_INSTALL_MANIFEST:?set a durable destination}"
export JEBAO_FLOW_LOCAL_QUALIFICATIONS="${CURRENT_QUALIFICATIONS:-}"
export JEBAO_FLOW_APP_TEMPLATE_ROOTS="${CURRENT_TEMPLATE_ROOTS:-}"

jebao-flow-local prepare-install \
  --role-attested-by on_site_operator
```

`JEBAO_FLOW_LOCAL_QUALIFICATIONS` is optional. If it is absent, only functions explicitly listed in
the private config's `identity_bound_controls` can become command surfaces; without either input the
surface is diagnostic-only. The public command result contains counts and content digests, never
private paths or raw device identifiers. Multiple current template roots use the platform path
separator.
The write-ahead journal has no command-line or environment override: production always uses the
fixed `local-control` directory inside the identity-verified hardware-safety root. A process cannot
hide an unfinished transaction by changing an XDG variable or launch context.

`prepare-service` builds a complete owner-only systemd staging unit from the same current inputs:

```bash
jebao-flow-local prepare-service \
  --role-attested-by on_site_operator \
  --output "${CURRENT_SERVICE_STAGING:?set a durable staging destination}" \
  --mqtt-password-file "${CURRENT_MQTT_PASSWORD_FILE:?set the current 0600 password file}"
```

The command never reads or merges an older unit, never writes a system service directory, and never
invokes `systemctl`. The generated unit contains the current executable, config, manifest, and
template paths as execution settings only; write-enabled generation additionally carries the
current qualification path. They are not device identity inputs and their digest is not added to
the install manifest. Run `prepare-service` again when those current install locations change, and
explicitly re-attest if the stable logical-role mapping changes. The MQTT password value is never
copied into the unit
or a command argument. At service start it is read from the named current owner-only `0600` regular
file, while an arbitrary environment file is never sourced. If the private config does not request
an MQTT password, omit `--mqtt-password-file`; a stale password-file setting then has no effect.
The current `JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID` is copied by replacement into a separate
owner-only `0600` value file beside the staged unit (or at the explicit
`--hardware-safety-volume-id-file` destination). The unit contains only that file path and clears
any manager-level raw value. The service reads the file after process start, so the raw authority is
not present in the unit or initial service environment. It is never printed, inherited from an old
unit, or regenerated from a missing or mismatched marker.

The generated bare-host unit declares `RequiresMountsFor=/hardware-safety`. The identified safety
volume must already be mounted there before systemd starts the service. The current Docker topology
instead supplies `/hardware-safety` as a named volume, so this bare unit is only for a host where
the same identified volume is mounted at that fixed path. Absence remains an explicit dependency
failure rather than permanent unknown state.

The generated service omits `--write-enabled` by default. In that mode a qualification path is not
retained, writer journals and write leases are not opened, and retained qualifications are neither
read nor projected as commands. The service therefore remains a useful read-only state and
diagnostic surface instead of failing merely because receipt files exist or later move. Adding the
flag to the staging command uses only the current verified receipts and records the separately
selected runtime opt-in; it does not
install or start the service and it does not replace the qualification, private-config, lease,
journal, or on-site live-write gates.

### Standalone Docker Compose deployment

`prepare-compose` is the deployment adapter for the current named-volume topology. It writes a
standalone Compose file and one new private input generation from values read during that invocation:

```bash
export JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID="${CURRENT_SAFETY_VOLUME_ID:?set current value}"

jebao-flow-local prepare-compose \
  --role-attested-by on_site_operator \
  --output "${CURRENT_COMPOSE_STAGING:?set a durable Compose destination}" \
  --bundle-root "${CURRENT_COMPOSE_INPUT_ROOT:?set a durable private input root}" \
  --mqtt-password-file "${CURRENT_MQTT_PASSWORD_FILE:?set the current 0600 password file}"

docker compose -f "${CURRENT_COMPOSE_STAGING}" config --quiet
docker compose -f "${CURRENT_COMPOSE_STAGING}" up -d --build jebao-flow-local
```

The preparation command does not invoke Docker, start a container, contact Home Assistant, or
contact a device. An older Compose file, generation, install manifest, or automatic discovery
result is never an input or default for a new value. The cleanup pass reads only an older generation
descriptor to bound the exact directory entries it may remove. The new random generation is fully
written first, the complete Compose file is atomically replaced last, and only then are older
directories that validate as generated inputs removed. The descriptor is deleted after its secret
files, so a later preparation can also finish an interrupted cleanup. Unrecognized entries are left
untouched. Because each removed generation may contain a private volume identity and MQTT password,
the adapter does not retain an accumulating history of old secret copies.

The generated source-secret directory is owner-only mode `0700`. Its files are mode `0444` with one
link: local Docker Compose bind-mounts file-backed secrets and does not implement secret UID/GID or
mode remapping, so a portable non-root container must be able to read the mounted file. Host-side
confidentiality comes from the non-traversable `0700` generation directory. The stage container
also verifies that each production secret is on a read-only mount before reading it. It copies the
verified current inputs into `/data/local-control` as mode `0600` files under a mode `0700`
directory, removes known optional files that are absent from the new generation, writes the
generation descriptor last, and never merges prior bytes. The runtime-generated install manifest
is also removed before startup and then wholly regenerated from the newly staged config; it is
never a bundle input.

Every preparation rechecks the bundle root and every retained generation directory for the same
owner and exact `0700` mode before writing another generation. Do not move the generated `0444`
files out of that directory or loosen either parent mode between preparation and `docker compose
up`; run `prepare-compose` again if the staging tree was copied or restored.

Both generated services retain the image's fixed `jebao-flow` user; the Compose file has no `user:`
override. The stage service has no network, drops every capability, uses a read-only root filesystem,
and mounts only the source secrets plus the private data volume. It never mounts the hardware-safety
volume. The runtime also drops every capability and uses a read-only root filesystem. Its private
data volume is deliberately `rw` because every start replaces the freshly derived install manifest
and the stage replaces the current inputs. The `/data` mount must remain writable by the image
user, and `/data/local-control` remains owned by that user with exact mode `0700`.

The runtime mounts the existing external volume named `jebao-flow-hardware-safety` at the fixed
`/hardware-safety` path with `rw` access. This is required even for device-read-only operation
because shared/exclusive coordination locks are created and flocked there. The mount mode does not
enable a device write: the read-only runtime still constructs no writer, journal, or exclusive
write lease. The stage service never sees this volume. The runtime uses Linux host networking
because current endpoint discovery uses local UDP broadcast; this generated topology is therefore
intended for the Linux deployment host, not a portable bridged-network substitute.

The input descriptor binds every staged file digest and the install generation. The generated
runtime passes that generation as an explicit command argument and validates it plus every current
file digest before loading the volume identity or private config. An ambient or older generation
environment variable is not consulted. This is a startup gate, not a periodic one: after every new
`prepare-compose`, run the `docker compose up` command again so the stage and runtime consume the
new generation. A process already running from the old generation keeps its in-memory config until
it is recreated; no background path silently mixes the two generations.

Read-only generation never reads or bundles legacy control bindings. A write-enabled generation may
omit them entirely and use only the explicit per-device `identity_bound_controls` stored in the
owner-only private config. `--write-enabled` remains an explicit generation option and still does
not bypass private-config permission, stable identity resolution, ranges, leases, journal,
rollback, or on-site live-write approval.

`ha-schema` accepts repeatable current topology declarations:

```bash
jebao-flow-local ha-schema \
  --role-attested-by on_site_operator \
  --schedule-pair async:wavemaker_master:wavemaker_slave
```

If a current config already contains an explicit native pair, the command derives the same
topology directly and `--schedule-pair` is unnecessary. No previous generated topology is read.
An interactive invocation without `--role-attested-at` records a new role-attestation time. A
generated service deliberately reuses the attestation created by its own current preparation and
checks its stable role-binding digest on every restart; it does not read a timestamp from an older
manifest or unit. An interactive `serve` without that digest is a new explicit operator attestation,
not the generated-service continuity gate.

`serve --write-enabled` exposes only executable functions explicitly authorized for the exact
freshly derived physical identity, plus any valid legacy bindings supplied by an older deployment.
An identity-bound function still requires the current private device settings to permit writes; an
unknown function, unsupported sender, product mismatch, disabled device, or different identity
fails before a journal or device frame is created. Running a command against live hardware remains
a separate operation from deployment and discovery publication.

### Manual-flow qualification producer

`jebao-flow-local-qualify` is a separate, explicit first-write tool for one current Local
Wavemaker Pro or Local Wavemaker Bar `manual_flow` binding. It does not enable the regular control
surface. A `run` requires `--write-enabled`, an opaque operation id, one privately selected current
target, a 1..5 percentage-point downward step, the reviewed active-template SHA-256, and a
no-clobber qualification output path. The flag is only an invocation opt-in; it does not replace a
separately approved live operation or any current envelope check.

The state inspection is product-scoped and sends no frame. The existing Pro profile derives
SwitchON and TimerON together from its coupled timer shape. The Bar timer command shape depends on
app state and deliberately has no deterministic encoder, so qualification does not invent one: it
reads TimerON through the strict production status decoder and derives SwitchON from the existing
power mapping. Both profiles then require the product's own `constant` and `independent` choices.
The preparation probes and decoded-field read are inspection only; both live qualification
sequences still contain exactly three physical frames.

The producer creates one local-control journal record with three actions before the first frame:
same-value, the exact downward step, and the exact planned restore. Every action uses a new strict
endpoint resolution and a new authenticated writer session. The same-value frame is sent and
verified as transport-reachability evidence but creates no receipt. Only the changed step and the
planned restore create the two distinct ACK/readback receipts required by the unchanged verifier.
The transaction cannot become `VERIFIED` while the planned restore is still pending.

`GizwitsSession.send_raw_control()` returns only the bytes after the response sequence number. An
empty returned body is therefore valid ACK evidence once the session has already received a
`SERIAL_CONTROL_RESPONSE` with the exact request sequence; it is preserved as the SHA-256 of empty
bytes and is not reclassified as a missing ACK. A timeout, wrong response command, truncated
sequence, sequence mismatch, or exact fresh-readback mismatch still fails closed.

Receipt and binding files are finalized only after the journal is durably terminal and the device
has exact-read-back at its original value. Re-entering the same terminal operation performs no
discovery and creates no writer: byte-identical, digest-matching files are verified and accepted,
while any different existing content is left untouched and rejected. An unfinished operation may
only use `recover`; recovery freshly resolves the stable device, restores the original baseline,
never resumes a test phase, and never creates a receipt.

The send envelope is fixed before the first frame and rechecked at every phase: device on,
`constant`, TimerOFF, `independent`, current Flow no greater than 45, a downward step of 1..5, and
both values inside the current private config limits and the app-declared `30..100` encoder range.
The 45 ceiling is an attended first-write bound, not the device capability or Home Assistant input
range. The current config, manifest generation, app catalog, selected template, protocol sources, and
discovered firmware must remain byte/content-bound for the whole operation. Values are never
clamped or substituted to make the run proceed.

No live qualification was performed while landing this producer. For a later separately approved
operation, stop the observer first so the qualifier is the only device-session owner; after a
terminal planned restore or separately authorized recovery, restart the observer and recheck its
read-only surface. The native ASYNC harness, old daemon/MQTT path, verifier, catalog membership
gate, journal, coordinator, and writer remain unchanged. The later D1i lease change only recognizes
the exact terminal parked tombstone described under recovery semantics; it does not alter this
producer's write, recovery, or evidence contract.

### Power qualification producer

`jebao-flow-local-power-qualify` is a separate explicit tool for qualifying one current `power`
binding. It creates one journal containing `same_on`, `power_off`, and `planned_on` before its first
frame, then uses three fresh authenticated writer sessions to send exactly ON, OFF, ON. The
same-value ON frame is transport-reachability evidence and deliberately is not skipped as a
no-change action. Only OFF and planned ON create receipts; the existing manual-flow CLI remains the
only recovery entry point and does not resume a qualification phase.

The journal's recovery mapping is deliberately asymmetric and is reloaded from durable storage
before the first send. The two ON actions use the actual writer-produced OFF payload and OFF scalar
readback as their rollback target, while the OFF action retains its actual ON baseline and sets
`restore_on_recovery=false`. Thus any nonterminal failure, including one after planned ON readback
but before the transaction's terminal save, converges conservatively to OFF and never sends an ON
frame during recovery. An already-OFF device needs no recovery frame; an ON device may receive at
most one prepared OFF frame while TimerOFF and independent ownership remain stable. The operation's
full baseline status still records the true initial ON state—the OFF scalar baseline on the two ON
actions is a recovery target, not a claim about the initial device state.

The live admission state is power ON, TimerOFF, `constant`, `independent`, and current Flow no
greater than 45 and within both current config and app-declared bounds. This is an attended
hydraulic-step condition, not a device capability or Home Assistant input limit. No dwell timer,
automatic retry, alternate lock namespace, or alternate recovery command is added. A failed run
may leave the pump OFF; the on-site approver must retain an app or physical means to restore it.
The producer does not modify the protocol-schema-bound writer, the existing qualification
producer, or either existing qualification/recovery behavior.

### Immutable qualification-set composition

`jebao-flow-local-qualification-set` is an offline composer for receipts that a qualification
producer has already installed in the fixed content-addressed hardware-safety receipt directory.
It never creates, moves, copies, or rewrites a receipt and imports no device, network, writer, or
native-linkage module. It also leaves the current qualification source, install manifest, and
running Compose generation unchanged.

The command accepts an optional current set, one candidate set, and a new no-clobber output path.
Each intended addition is named by private logical member and public function; the command reads
the current manifest only to resolve that member to exactly one current product and physical
identity. It does not accept a raw physical identity on the command line or report logical member,
physical identity, or private paths in its public JSON. In a generated deployment it reads the
current owner-only hardware-safety volume-identity file into process memory before validating the
same mounted receipt root; it neither reports nor rewrites that value.

```text
jebao-flow-local-qualification-set \
  --current <current-qualification-source> \
  --candidate <new-candidate-source> \
  --output <new-immutable-source> \
  --manifest <current-install-manifest> \
  --expect-add <logical-member> <function>
```

Every current and candidate binding is first checked by the unchanged receipt verifier. Bindings
with the same `(product_key, function)` may merge only their receipt-digest sets and only when all
seven version/content fields agree; other binding keys are additive. The merged result is verified
again, and its binding keys and verified `(product, function, physical identity)` surface must equal
the current verified surface plus exactly the operator-declared additions. This rejects both an
unexpectedly broadened command surface and silent loss of an existing qualification. Distinct
request ids remain global across the whole result, and the existing two-receipt-per-identity,
canonical-receipt, digest, firmware, and content checks are not relaxed.

Output bindings are sorted by `(product_key, function)`, and each binding's receipt digests are
sorted lexicographically before compact ASCII JSON with one trailing newline is written. A random
owner-only temporary file is fsynced and linked into a new owner-only destination without replacing
anything. Byte-identical re-entry is verified and accepted; different existing bytes, unsafe
metadata, a missing receipt, or any reload/verification failure leave every prior input untouched.

The immutable output is only a source for a later separately reviewed
`prepare-compose --qualifications` operation. The deployment evidence is the
`qualifications.json` SHA-256 in that new generation's bundle descriptor, not merely the composer's
source-file digest. Creating the set alone neither enables a control nor changes the service.

### Observer control surface

Top-level `runtime.mode: observer` takes precedence over the current `--write-enabled` invocation,
identity-bound control lists, retained legacy binding files, `runtime.dry_run`, and per-device write
flags. Both `serve` and `ha-schema` treat that mode as having no control-surface authority: they do
not read a legacy binding file or construct a write coordinator, and the projection contains no
device scalar or per-slot schedule command topic, group switch, or per-member resume button. The
MQTT adapter therefore subscribes to no command topic. Device and system diagnostics, schedule
observation, availability, fresh device state, group actual output, and lifecycle inventory remain
present.

The projection reports the bounded top-level reason `runtime_mode_observer` in
`control_surface_diagnostic_reason`. That field is additive and does not replace an entity's
capability or qualification `diagnostic_reason`; the same catalog and manifest therefore retain the
same per-entity explanation in control and observer projections. An already qualified function is
projected as a command-free diagnostic sensor rather than being mislabeled as physically
unqualified. Structurally, mode suppression changes only projected control availability;
`diagnostic_reason_for()` continues to depend solely on catalog capability and current physical
qualification. In `runtime.mode: control`, the mode reason is `null` and the existing qualification,
construction, arming, and live-opt-in behavior is unchanged.

Fresh observer polling classifies every manifest target from one current discovery without making
the writer resolver partial: exactly one physical match proceeds to the existing read-only session,
no match reports `current_endpoint_absent`, and multiple matches report
`current_endpoint_ambiguous`. A malformed position or target binding from that observer API still
makes the whole cycle `current_resolution_binding_mismatch`. The strict resolver calls used by the
coordinator keep their existing all-or-nothing exception contract, and no endpoint address is
retained in public diagnostics or artifacts.

Newly projected Home Assistant identities now bind discovery object ids, entity `unique_id`
values, and device identifiers to a stable instance namespace. The namespace is `instance_`
followed by the full SHA-256 digest of the exact validated `topic_prefix`; it is independent of
runtime mode, process identity, host data, and startup order. Device functions and schedule slots
remain grouped by logical device, while actual-output, switch, and resume entities for one group
remain grouped by logical group. State, command, and availability topics keep their existing exact
`topic_prefix`-based values.

On each MQTT connection, the adapter now removes only current-topology platform variants owned by
this instance namespace that the current projection no longer publishes. It derives a bounded
potential set before runtime-mode and qualification filtering, subtracts the current discovery set,
and publishes the resulting sorted topics as exact zero-length QoS 1 retained payloads after all
availability topics are offline and before command subscriptions or current discovery. State,
command, availability, and current discovery topics are never deletion targets. A failed tombstone
publish aborts the remaining startup sequence rather than opening command subscriptions beside a
stale control surface.

This cleanup is intentionally not a complete retained-state migration. It uses only the current
configuration, manifest, catalog, groups, and invocation-derived schedule topology; it does not
query the broker or preserve prior projection history. Removed topology, an older function kind or
discovery prefix, another namespace, and pre-namespace discovery configs are therefore outside its
authority and remain `UNKNOWN`. Exact `topic_prefix` uniqueness per daemon is an operator invariant,
not a code-enforced lease: two daemons sharing it can delete each other's current platform variants.
Pre-namespace legacy entities can also remain actionable against the same command topic, so actual
deployment stays blocked until a separately reviewed exact legacy inventory, removal, and restore
procedure is approved. Recorder history, entity/device-registry customization, area placement, and
dashboard references are not assumed to transfer automatically, and pre-namespace config is never
deleted automatically.

### Observed pre-namespace discovery inventory

The source-only `jebao_flow.legacy_discovery_inventory_cli` provides a write-free evidence step for
that legacy boundary. `capture` creates a fresh MQTT 3.1.1 clean session with a random client id, no
will, and no publish call. It subscribes only to
`{discovery_prefix}/+/+/config` for a predeclared monotonic window. Before durable storage it drops
live messages, empty deletion payloads, deeper discovery shapes, and object ids outside the
`jebao_flow_local_` family. A retained replay has no protocol-level end marker, so the artifact says
only that its records were delivered during that window; a missing record is not evidence about
broker state.

The artifact root must already be an owner-only `0700` directory. Each opaque generation is `0700`,
its canonical files are `0600`, and usable ids are returned only after file and parent-directory
fsync. Exact topic UTF-8 and payload bytes remain private. `inspect` emits only opaque ids, counts,
UTC bounds, bounded classifications, and SHA-256 bindings. Conflicting retained payloads for one
topic are both preserved and marked unselectable rather than guessed.

```text
python -m jebao_flow.legacy_discovery_inventory_cli capture \
  --config <private-config> --output-root <private-owner-only-root> \
  --window-seconds <bounded-seconds>

python -m jebao_flow.legacy_discovery_inventory_cli inspect \
  --config <private-config> --output-root <private-owner-only-root> \
  --inventory-id <opaque-inventory-id>
```

Selection is a separate offline command, not an interactive or select-everything action. A
maintainer-provided `0600` JSON request names the inventory id and digest, repeats its four binding
digests, and lists each chosen record's opaque id plus exact topic and payload SHA-256 values. Every
entry must be an eligible record in that inventory. The resulting artifact explicitly records
`mutation_authorized: false`; it is a reviewable ownership decision, not proof of current retained
presence or permission to delete.

```json
{
  "schema_version": 1,
  "request_kind": "maintainer_exact_observed_record_selection",
  "inventory_artifact_id": "legacy-inventory-00000000000000000000000000000000",
  "inventory_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
  "bindings": {
    "config_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
    "discovery_prefix_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
    "subscription_filter_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
    "topic_prefix_sha256": "5555555555555555555555555555555555555555555555555555555555555555"
  },
  "records": [
    {
      "record_id": "legacy-record-6666666666666666666666666666666666666666666666666666666666666666",
      "topic_sha256": "7777777777777777777777777777777777777777777777777777777777777777",
      "payload_sha256": "8888888888888888888888888888888888888888888888888888888888888888"
    }
  ]
}
```

The values come from the redacted `inspect` output after reviewing the corresponding private raw
record; the example values above are placeholders and do not authorize any record.

```text
python -m jebao_flow.legacy_discovery_inventory_cli select \
  --config <private-config> --output-root <private-owner-only-root> \
  --inventory-id <opaque-inventory-id> --request <private-selection-request>
```

This source landing does not connect to a broker by itself. An intentional first `capture`
invocation is a separate broker-contact decision, but it performs no Home Assistant, daemon, or
pump write and does not add an on-site hardware gate. Apply and restore are not implemented. Their
rules will be designed only after a real observed artifact is reviewed; non-observation can never
be treated as deletion success or as broker absence.

## Software-independent group actuation

The actuator accepts one already-built `GroupTickPlan` and can be called directly for a one-shot
operation. Recurring concurrency is handled by the separate `PlanAtomicGroupDispatchRuntime`, not
by modifying the existing per-device `GroupDispatchRuntime`. The plan-level runtime passes the
complete immutable plan, its generation, a semantic plan digest, one pre-send guard, and an opaque
safety token through a single port call. It never collects independent member callbacks into a
transaction, so two generations cannot be mixed by a callback barrier. The runtime and durable
actuator are connected in code, but no daemon tick source or new Home Assistant/MQTT group command
is exposed yet. The port's pure plan validator runs before the runtime accepts generation ownership,
so a malformed or out-of-envelope replacement cannot first supersede valid current work and fail
only afterward.

For one explicit plan, the actuator uses only existing qualified scalar operations. An enabled
member is represented by a `manual_flow` frame followed by a separate `power=true` frame. Flow is
sent first so a currently stopped member is not briefly enabled at its old value. The frames are
not combined into a new payload shape: each keeps its own per-device, two-receipt qualification.
Canonical OFF is exactly `DeviceTarget(enabled=false, power=0)` and sends only `power=false`, so it
preserves Flow and needs no invented composite qualification. A stopped or emergency-stopped group
requires canonical OFF and current power qualification for every member before any frame is sent.
If even one member is currently unreachable, that whole-group stop attempt sends no frames and is
reported as unavailable rather than claiming a partial stop.

Every operation resolves all dispatched targets from one current observation and associates them
by `physical_identity_key`, never discovery order. A deliberately unavailable member does not have
to appear in discovery merely to be skipped. Complete stable bindings and every dispatched
component qualification are checked before a session or lease is opened. It still acquires every
group member key in one lease call, then reads all dispatched device baselines, creates one journal
record, and sends the ordered actions. The transaction becomes `VERIFIED` only after every
non-no-change action has exact readback. A partial failure remains one `RECOVERY_REQUIRED` record
whose actions are recovered in reverse order under their durable per-action policies; it is never
synthesized into group success.
Deliberately skipped members are visible through the total and dispatched member counts rather than
being reported as physical writes.

`power=false` is a safer terminal action, so its durable recovery policy differs from an ordinary
setting change. Recovery never sends that action's stored `power=true` baseline merely to recreate
the less-safe pre-write state. It instead performs one fresh read in the original action direction:
an exact OFF match is terminalized as `safe_state_retained`, while an exact original ON match is
terminalized as `baseline_already_present`. If neither state can be established, the action remains
`UNCERTAIN`, the transaction remains `RECOVERY_REQUIRED`, no frame is sent, and later transactions
remain blocked for operator resolution. A `ROLLED_BACK` group record can therefore contain a mix of
retained-safe and baseline-present members; consumers must use each action's diagnostic rather than
assuming the record phase means every member returned to its original state.

For recurring dispatch, a newer generation replaces only the one pending complete plan. A different
plan already in flight may finish the frame whose pre-send guard passed, including its ACK and exact
readback, but the guard stops the next frame before it can leave. The journal then terminalizes the
known-not-sent remainder as `SUPERSEDED` without rollback: earlier verified actions remain explicit,
and every superseded action has neither an ACK nor uncertain delivery. A transaction with any
uncertain action cannot enter that terminal phase. A missing ACK, unavailable readback, or any other
post-send ambiguity remains `RECOVERY_REQUIRED`; the runtime never retries it and suppresses later
ordinary plans until operator reconciliation.

The recurring runtime treats a complete STOPPED or EMERGENCY_STOP plan, and a failure-policy stop,
as mandatory OFF when every dispatched target is canonical OFF. That plan's safety token bypasses
ordinary generation supersession, later ordinary plans are deferred rather than retained, and
shutdown drains an already accepted stop. Shutdown never invents a new OFF plan. It retries a
mandatory stop at most once only when the port proved that no frame was sent; uncertain delivery
causes shutdown to report an unresolved safety stop instead of claiming a clean close. Because a
STOPPED or EMERGENCY_STOP whole-group plan requires canonical OFF and qualification for every
member, one unreachable member still makes that explicit stop attempt send zero frames. A
failure-policy stop may instead carry only `SKIP_OFFLINE` or `SKIP_UNKNOWN` for unavailable members;
it locks the whole group while stopping every reachable dispatched member. If another durable
transaction is already `RECOVERY_REQUIRED`, neither form can bypass its journal authority and the
stop remains unresolved until the operator reconciles that record.

The request id is the durable journal idempotency identity. Direct one-shot reuse with the same
physical intent returns the terminal record without another discovery or send; reuse with a
different intent is rejected. The recurring runtime allocates one request id per accepted physical
plan operation and reuses that exact id only for an explicit retry proven `NOT_SENT`. It is not a
device identity or qualification gate. Planner generation and timestamp remain execution
provenance and are excluded from both the durable physical-intent identity and the semantic plan
digest. A changed control epoch, member action, target, derived group status, or failure-stop marker
does change that digest.

The applied-plan cache is only a confirmed in-process optimization. A fresh external observation,
reconnect, or other evidence of drift must call `invalidate_applied()` before the same semantic plan
may be treated as already applied. This runtime contract and its fault-injection tests do not claim
that a service loop is currently running or that any device accepted a group write.

### Write-disarmed group supervisor lifecycle

`LocalControlGroupSupervisor` is an offline-tested lifecycle boundary around one `GroupManager` and
one `PlanAtomicGroupDispatchRuntime`. Construction and diagnostic ticks are structurally
write-disarmed: before a valid explicit `GroupRuntime` command, the supervisor may calculate plans
but never calls `PlanAtomicGroupDispatchRuntime.submit()`. This remains true when the configured
group is enabled, disabled, or contains a disabled member; each of those configurations can produce
a physical dispatch and is therefore not itself a no-write guard.

Every submitted plan is the exact object returned by `GroupManager.plan_tick()`. The supervisor
never constructs a `GroupTickPlan` or a target independently. Fresh-state cycles, explicit desired
runtime updates, planning, and submission share one operation lock. A current fresh observation
maps to `online=true`; any failed fresh observation remains `online=unknown`, because the current
read path does not prove that a device is offline. If manager state changes after a plan is produced
but before submission, the plan runtime rejects that generation and a later tick recomputes from
current state.

The plan runtime intentionally bypasses ordinary deduplication for mandatory OFF. The supervisor
therefore suppresses unchanged periodic resubmission only while the runtime either confirms that
semantic plan as applied or still owns its mandatory-OFF token. Invalidating the runtime's applied
cache also invalidates this suppression. A new explicit command may request the same OFF again. If
an earlier safety token is still unresolved, that explicit request is reported as
`deferred_safety_off`, not as a submitted physical operation. The supervisor does not weaken the
runtime safety-token bypass, invent a retry, or turn an uncertain delivery into success. Ordinary
unchanged plans still use the runtime's confirmed applied-plan deduplication. If a latched ordinary
failure suppresses a later plan, the cycle is `suppressed_after_failure` rather than `submitted`.
An unresolved safety OFF remains a sticky shutdown error on every `aclose()` call; repeated
shutdown observers cannot turn it into a clean return.

The supervisor module does not run a background tick or directly know about MQTT, Home Assistant,
device construction, or discovery. The production lifecycle now constructs it and the qualified
group-command router described below calls it only through the lifecycle service. The runtime still
cannot compare projected fresh scalar state with a typed group target, so external target drift
remains `UNKNOWN` rather than becoming a speculative write gate. Broker, Home Assistant, and live
hardware activation remain outside the offline implementation and require the hardware-readiness
preconditions.

### Production group lifecycle inventory

The local `serve` path now constructs an isolated lifecycle inventory and feeds it exactly one
complete result tuple after each fresh device-state cycle. This is additive to the existing retained
device truth: individual values and availability are published first, and a group construction or
planning failure is converted to a bounded group diagnostic instead of terminating the poller or
MQTT adapter. Current inventory is retained at `<topic-prefix>/local/groups/status`; it is diagnostic
JSON, not a Home Assistant entity or command surface, and it contains no physical identity or
private path. The inventory callback runs exactly once after all device-state publications succeed;
if a device-state publish fails, the cycle loop itself terminates rather than continuing with a
silently omitted group observation.

Groups are constructed independently. Native-linked groups, products without the current local
power/Flow encoder pair, unimplemented patterns, `fallback_constant`, missing current manifest
members, duplicate physical identities, and empty intersections with the app-declared `30..100`
Flow range remain `unconstructed` with a stable English reason. Per-device actuation limits use the
intersection of current private config and that declared app range; the planner's currently
declared step remains unchanged. Group-level effective pattern limits remain a separate input.

A read-only `serve` process reports the inventory but constructs zero supervisors and never creates
a fake write port. With `--write-enabled`, each supported group receives the current coordinator and
its physical plan boundary shares the same process operation lock as direct device commands and
fresh reads. The supervisor itself remains structurally write-disarmed until an explicit group
runtime command. The qualified group switch described below is now the sole production caller for
that command. Startup and every fresh cycle before the first accepted switch command still submit
zero group plans regardless of configured group or member `enabled` values.

Shutdown attempts every constructed supervisor. An unresolved mandatory OFF is re-raised outside
the adapter's task-cancellation gather and remains an error on repeated close; it cannot become a
clean service exit. The command transport preserves the same rule: direct per-device writes set the
affected manager's manual override, and group entities remain based on fresh device truth. External
scalar-target drift detection is still `UNKNOWN`, not an inferred success or new write gate.

### Fresh group output and direct-device ownership

Each configured group now has a command-free Home Assistant diagnostic sensor backed only by the
complete fresh device-state cycle. The existing status decode also produces one typed power value;
this does not add a read, a second decode, or a qualification dependency. A member that is
unavailable, lacks a power field, or reports a non-boolean power value makes the group output
`unknown` and keeps the group entity unavailable. Otherwise the aggregate is `off` when every
member is physically off, `on` when configured enabled members are on and configured disabled
members are off, and `mixed` for every other combination.

This aggregate is power truth, not desired state or proof that the current Flow matches the group
pattern. Its attributes include the current install generation, the bounded logical manual-override
member list, and `pattern_conformance: unknown`. Manual ownership never changes the aggregate by
itself, and a direct command result never updates it optimistically. The sensor state and
availability are published only after the individual device-state publications for that fresh
cycle have succeeded. The manual-override list follows the planner's current report, so after a plan
cycle it enumerates config-enabled overridden members rather than claiming a complete ownership
ledger for disabled members. A group-output inventory mismatch makes group output unavailable and
publishes a bounded lifecycle diagnostic instead of terminating device-state synchronization. The
qualified group switch added below is a separate entity and never changes this diagnostic sensor's
fresh-truth semantics.

Existing qualified direct-device scalar and schedule commands declare manual ownership after their
payload has been parsed and before the current coordinator operation begins. The declaration and
the coordinator write run under the same process operation lock. It advances the manager's control
epoch but does not submit a replacement group plan. An ordinary plan already waiting on that lock
therefore fails its guard again at the frame boundary after it reacquires the lock and cannot
overwrite the direct value. The next successful fresh cycle may perform one plan-wide reevaluation
for the remaining group-owned members; the following unchanged cycle is deduplicated.

Manual ownership is an operator declaration, not a claim that the direct write succeeded, so it
remains set if later coordinator preflight rejects the operation. A stopped or config-disabled
armed group still permits every direct control operation. Only a direct `power=true` operation that
the coordinator durably returns as `VERIFIED` carries
`group_forced_off_will_revert_next_cycle`. While the same process operation lock is still held, that
result records the group's current `safety_off_succeeded_count` as a supervisor-local watermark. An
unchanged mandatory-OFF plan is suppressed only when no such watermark exists or a later mandatory
OFF success has advanced the counter beyond it. Thus an OFF already waiting on the lock can satisfy
the reconciliation without a duplicate plan-wide call; otherwise the next complete fresh cycle
submits one OFF check, and the following unchanged cycle is suppressed.

The canonical OFF check changes power and physical output only. It does not roll back the scalar
written by a direct `manual_flow` or schedule operation, and those operations, `power=false`,
preflight failures, and non-verified `power=true` results carry neither the watermark nor the revert
warning. A member's first manual-ownership declaration still changes the plan identity once, so a
stopped group may pay one plan-wide OFF check for that ownership transition regardless of function
or coordinator result; redeclaring the same member does not repeat that cost. An uncertain direct
record remains under the existing operator-resolution authority, while the next fresh cycle reports
physical power truth without treating a group plan as recovery or PASS.

An armed emergency-stopped group takes precedence over disabled/stopped handling: it rejects direct
control before a request id, durable journal, or physical frame is created and reports
`group_emergency_stop_blocks_direct_control`. That rejection does not record an override, because
changing the control epoch would itself create another mandatory-OFF plan. An already accepted
mandatory OFF remains uncancellable by a later ordinary generation. The group-command service now
exposes the only production arming caller, while these direct-ownership interactions retain the
same frame-boundary and mandatory-OFF contracts.

### Qualified group command transport

A separate Home Assistant switch is projected only when every current group member has both the
existing identity-bound `power` and `manual_flow` qualifications. The pure qualification fold
returns the exact sorted missing logical member/function pairs and imports no manager, dispatch
runtime, coordinator, journal, MQTT, or Home Assistant implementation. There is no one-sided OFF
button for a partially qualified group. Construction is a separate fact: a qualified but
unconstructed group keeps its switch unavailable and rejects a command before coordinator,
journal, or frame creation using the lifecycle's existing stable construction reason.

The switch is non-optimistic. Its retained state is published only from a successful complete fresh
device-state cycle: `on` and `off` are available, while `mixed` and `unknown` leave the switch
unavailable and are never folded into a guessed binary state. The command topic is owned by a
separate `LocalControlGroupCommandRouter`; it is checked for disjointness from every device and
schedule command topic and never enters `LocalControlMqttRouter.handle()`. Consequently the group
router does not hold the shared process operation lock while it waits. The existing plan port takes
that lock only when the runtime worker reaches the physical plan boundary.

An accepted `ON` command copies the current runtime with `state=RUNNING` and `enabled=true`; `OFF`
uses `state=STOPPED` and `enabled=false`. Other runtime preferences are preserved. Under the
supervisor lifecycle lock, validation succeeds before manual ownership is cleared, then the manager
plans and submits that exact returned object. A failed runtime validation therefore preserves the
existing override, while a same-state accepted command clears it. Explicit OFF continues to use the
existing mandatory-safety token and intentionally bypasses ordinary applied-plan deduplication.
The per-group `cycle_count` covers both successful fresh-observation planning cycles and accepted
explicit command planning cycles; the top-level `fresh_cycle_count` continues to count only complete
fresh observations.

The command result separates immediate action from current identity state. It reports the submitted
plan identity, whether runtime submission was called, immediate deduplication, dispatch count,
applied- and latest-identity matches, matching failure certainty, and whether completion remains
pending. Immediate deduplication comes only from the submit call's no-await counter delta; it is not
inferred from the pre-submit applied identity, because an explicit repeated mandatory OFF may still
perform a plan-wide check. The headline precedence is `deferred_safety_off`,
`suppressed_after_failure`, identity-matched `not_sent`/`uncertain`, `awaiting_fresh_state`,
`deduplicated`, `verified`, `superseded`, then `pending`.

`verified` means that the submitted desired identity is currently applied and no runtime failure is
latched. It does not claim that this command authored a physical frame: during the bounded wait a
different identity may supersede it and a later cycle may apply the same desired identity again.
No command-specific physical-operation boolean is guessed. Completion waiting uses a fixed bounded
timeout outside both locks; timeout reports only `pending` and does not cancel the runtime worker.
Consumer cancellation has the same observer-only effect.

The command consumer remains inside the adapter's ordinary task-cancellation gather. Group service
close remains outside that gather, attempts every supervisor, and re-raises an unresolved mandatory
OFF ahead of other close errors. Retained, duplicate-marked, malformed, unqualified, unconstructed,
or inventory-mismatched group commands produce bounded diagnostics without an optimistic state
publication. This is a source and offline-test connection only: no broker, Home Assistant, app, or
physical device was contacted, hardware remains `NO-GO / BLOCKING`, and Q2 remains `UNKNOWN`.

### Per-member return to group ownership

Each member of a fully qualified group has a state-less Home Assistant `Resume ... To Group`
button. It clears only that member's manual override; it does not change group state, enabled,
pattern, power, limits, or period, and it never arms a group. An unarmed group rejects the action as
`group_not_armed` before manager mutation. An armed emergency-stopped group similarly rejects it as
`group_emergency_stop_blocks_direct_control`. The first later ON or OFF group command still clears
all manual overrides, so an unarmed override cannot become permanent.

For any other armed state, the supervisor validates the exact logical member, records whether
`resume_member()` changed manager generation, then calls `plan_tick()` once and submits that exact
plan through the existing plan-atomic runtime. A running group therefore restores the member to its
ordinary dispatch. A stopped group uses the existing canonical mandatory OFF. Repeating the action
for an already group-owned member does not change ownership; normal applied-plan deduplication or
mandatory-OFF suppression decides whether the current plan needs a check. No direct coordinator,
retry, applied-cache invalidation, or alternate write path is added.

The button is available only when qualification and supervisor construction are current and the
group has already been armed. Unlike the binary group switch, its availability does not depend on
fresh aggregate output being `on` or `off`: `mixed` is commonly the state in which a member return
is needed, and `unknown` is not folded into a guessed power state. The button result reports
`action=resume_member`, logical group and member, `ownership_changed`, and the same identity-bound
completion facts and headline precedence as the group switch. It has no `requested_on` field and
does not claim physical-frame authorship.

The existing group router owns both switch and resume topics, while its combined topic set remains
disjoint from device and schedule commands. `PRESS` is the only accepted resume payload. Retained,
duplicate-marked, malformed, unknown-member, unqualified, unconstructed, unarmed, emergency, and
inventory-mismatched actions stop before journal or frame creation. No new MQTT consumer task is
created, and unresolved mandatory-OFF shutdown remains sticky outside the adapter cancellation
gather.

There is no separate group-wide resume entity. Sending `ON` again to an already running group uses
the existing validated same-state command path and clears every manual override; sending `ON` to a
stopped group does the same while starting it. This source and offline-test surface does not contact
a broker, Home Assistant, app, or physical device. Hardware remains `NO-GO / BLOCKING`, and Q2
remains `UNKNOWN`.

## Product audit mapping

`ENC` means deterministic bytes exist. `DEC` means deterministic status decoding exists. Neither
term means the device accepted a write.

| Product | App surface implemented in the catalog | Current wire status |
|---|---|---|
| Local Wavemaker Pro | power, timer, four linkage roles, nine modes, Flow, Frequency, custom frequency, feeding, 48x9 schedule, common slave Flow, per-slot slave Flow extension, current schedule status, reset, clock, faults | scalar and schedule `ENC/DEC`; exact reset executable after per-device qualification; per-slot local composition implemented; accepted `0` |
| Local Wavemaker | power, timer, feed switch, pulse/tide, three linkage roles, four modes, Flow, Frequency, feeding, 48x8 schedule, current schedule status, clock, faults | scalar `ENC/DEC`; full-image schedule and clock `ENC/DEC` but diagnostic-only until an execution contract exists; no separate app reset action; accepted `0` |
| DC Pump Pro | power, timer, five manual modes, Flow, Frequency, feeding, 48x8 schedule, current schedule status, reset, clock, faults | scalar and schedule `ENC/DEC`; exact reset executable after per-device qualification; schedule mode codes 1/2 and timer coupling remain unknown; accepted `0` |
| Aquarium Pump | power, transport mode, feed switch, motor speed, feeding, 48x6 schedule, current schedule status, clock, faults | known scalar plus full-image schedule and clock `ENC/DEC`; transport direction, motor safe range, and timer coupling remain unknown; no separate app reset action; accepted `0` |
| Dosing Pump | power, four manual channels, four timers, intervals, calibration channel/value/duration, four 24-entry schedules, faults | channel images `DEC` and exact app-shaped preview available; sending remains disabled; accepted `0` |

### Automatic daily dosing planner

The local runtime also includes a wire-free dosing planner for the four-channel Dosing Pump. It
accepts a channel, exact daily amount, dose count (`1..24` from the audited app-derived per-channel
layout, not yet a wire claim), first dose time, and explicit amount
step. It spaces the requested doses evenly across 24 hours, balances individual amounts within one
step, and preserves the daily Decimal total exactly without binary floating-point rounding.

```bash
jebao-flow-local plan-dosing \
  --channel 1 \
  --daily-amount 12 \
  --doses 4 \
  --first-dose 01:30
```

The result contains the exact times, per-dose amounts, unused slot count, and a content digest.
The local planner defaults to a `0.5 mL` amount step and accepts a different exact decimal step.
That is local planning precision only. Static inspection of both retained Dosing templates confirms
that the app schedule stores each dose as `[hour, minute, volume_uint16_be]`, labels the value in
milliliters, applies no fractional scale, and accepts integer input. The smallest positive value the
app can express directly is therefore `1 mL`; a `0.5 mL` planned dose is never rounded or encoded.

For an integer-milliliter plan, `--app-image-preview` materializes the exact 96-byte
`CHnSWTime` image (`24 x 4`) and publishes only its layout, size, and SHA-256. It still reports
`wire_send_available=false`, `physical_unit=unknown`, and `device_acceptance=unknown`; no Dosing
write path exists. A fractional plan is rejected by this preview instead of being silently scaled.

The app calibration input is exactly `10, 20, ..., 100`; the observed `10 mL` calibration is its
minimum reference value, not a fractional schedule scale. The read-only `time1` datapoint is exposed
as `Calibration duration` with vendor range `10..250`, but its unit is not declared. It does not
provide a place to encode `0.5 mL`.

For a smaller long-term target, `plan-average-dosing` finds the shortest integer-dose interval.
For example, a `0.5 mL/day` target becomes a local plan of `1 mL` every `2` days. The app labels the
interval picker as Days, but the vendor definition declares no unit and there is no accepted device
write, so `interval_device_semantics` and `device_acceptance` remain `unknown`.

The same plans are now available through the existing HA/MQTT diagnostic preview route; see
[the dosing planning API](pump-mqtt-apis.md#도징-계획-api-추가). It accepts exact decimal amounts,
returns the planned times and quantities, and never sends a dose, changes calibration, or installs
a device timer. In average-interval mode the requested count applies to each dose day, not every day.

The packet version recorded by product documents and the GAgent frame magic are different fields.
The former is not used to replace protocol magic `0x00000003`.

## Detailed app audit boundary

The static audit covers the installed app shell, its Mach-O executable, linked Gizwits and Hermes
frameworks, the Hermes JavaScript bundle, all 14 cached plain-JavaScript templates, all registered
product definitions, `Info.plist`, and English resources. Provisioning material, Keychain,
credential/token stores, SDK/HTTP logs, and crash caches remain excluded. Decompiled or extracted
vendor bytes are not committed.

The native boundary is now statically resolved: template `sendCmd` calls host
`callService("sendCmd", semantic_payload)`, the app enters the SDK through `write:withSN:`, and the
SDK re-encodes datapoint names from the vendor product definition. The SDK exposes the 0x0093
app-to-device and 0x0094 device-to-app P0 command pair. Product JSON is therefore the byte-layout
authority; a deterministic encoder is still not evidence that firmware accepted a write.

Template identity is its JavaScript content SHA-256. Cache names, bundle labels, UUID paths, sizes,
and mtimes are provenance only. Bundle names were not reliable product identities, so templates are
mapped by product-specific datapoint fingerprints and content digest. The actual runtime-active
template selection remains unresolved, but findings shared by the audited retained versions do not
depend on choosing the newest cache entry.

Static app dataflow now fixes the four schedule geometries, the zero-prefixed HMS clock command,
the product-specific single-slot versus full-image save forms, and the `0xEE` unused sentinel. Pro
and DC expose separate all-slot reset actions; Bar and Aquarium do not. Pro timer enable also writes
inverse `SwitchON` and detaches a current slave to `independent`. DC has two conflicting timer
couplings, while Bar and Aquarium include current `Auto*` fields, so those three timer controls stay
diagnostic. Pro manual mode code 6 is exposed in English as `Cross-flow`; DC schedule mode codes 1
and 2 remain unresolved even though its manual mode map is known.

The catalog therefore keeps unsupported or unresolved functions visible with an English diagnostic
reason. It does not invent a hidden control, a firmware rejection gate, or a fake successful action.
The product-by-product symbols, ranges, template surface, and unresolved call paths are retained in
[the registered-device app analysis](capabilities/registered-device-app-analysis.md).

## Recovery and command semantics

The local-control journal is independent from the exact-restore journal and has one non-overridable
location inside the identified hardware-safety root. Before a physical send, the code acquires the
existing deployment-wide operation lock, rejects every live native-linkage, verification,
schedule-linkage, or temporary-schedule journal, accepts only absent or terminal legacy intent
envelopes, and acquires `<physical_identity_key>.lock` for every target. The exact-restore journal
path is idle only when absent or when its owner-only file is the exact version-2
`parked_unqualified` terminal tombstone envelope. That narrow recognition answers only whether the
legacy workflow is still in flight; it does not qualify the old operation or replace the existing
archive/replay verification. Malformed, non-terminal, unsafe-metadata, or future-version files stay
blocked. The tombstone is never consumed or changed by local control. Any other unfinished
local-control transaction blocks a new request until recovery terminalizes it.

Each forward frame is preceded by a fresh baseline read and durable `PREPARED` state. The journal is
advanced to `FORWARD_ATTEMPTING` before send. A missing ACK or readback never causes an automatic
forward retry. Recovery resolves current endpoints again and uses the exact pre-write rollback bytes
for ordinary settings. A `power=false` action is the explicit exception: it is classified by fresh
readback without sending a rollback frame, because turning a stopped pump back on is not recovery to
a safe state. This policy covers both single-device and group power-off operations. A `VERIFIED`
transaction is already terminal and cannot be repurposed as an undo request through `recover`.

MQTT retained and duplicate-marked command messages are rejected. Command discovery and
subscription use QoS 0 deliberately, so a broker cannot replay an older command after a newer
physical operation; a lost command leaves state unchanged and may be issued again by the operator.
Commands are consumed sequentially and the local runtime assigns the journal request id. Home
Assistant optimistic mode is explicitly off, and state is retained only after the coordinator has
verified exact device readback. That state is a verified point-in-time observation, not a claim that
an external app can never change the device later. Public MQTT/HA state uses logical member names
and English ASCII labels only; raw physical identifiers and current addresses remain private.

`devices[].control.restore_on_reconnect` is retained only for configuration compatibility. The
current local-control runtime does not read the value, and neither `true` nor `false` causes a device
write at service startup or MQTT/device reconnect. Operators must not treat it as either an
automatic-restore promise or a switch that disables a restore path.

The daemon does not call `LocalControlCoordinator.recover()` automatically. A non-terminal journal
therefore remains a visible blocking recovery obligation instead of being replayed after a process
or broker reconnect. Operational monitoring inventories
`LocalControlJournalStore.unfinished_records()` from a fresh process against the current identified
hardware-safety root. When exactly one expected request remains, ordinary command authority stays
stopped and the reviewed mutation-free command
`jebao-flow-local-qualify recover --operation-id <same-opaque-id> --write-enabled` reconciles that
record without resuming its forward action. Control returns only after the journal is terminal and
an independent fresh read confirms the recorded baseline. An unexpected or multiple unfinished
inventory is reported and left untouched; it is never guessed, automatically retried, or hidden by
restarting the service.

### Bounded actual-recovery drill

`jebao-flow-local-recovery-drill` is a forward-only, attended diagnostic for the one still-unproven
rollback branch. It accepts one already qualified Local Wavemaker Pro `manual_flow` target at exact
Flow 40, delegates the normal journal-first write to Flow 39, and calls the production
`verify_prepared_readback()` predicate on the real fresh readback. Only after that predicate succeeds
does its instance-local writer wrapper raise one designated fault. It never changes readback bytes,
retries the frame, calls recovery, or imports the frozen native-linkage harness.

Successful forward-drill output means only that exactly one acknowledged Flow-39 write was followed
by the expected durable `RECOVERY_REQUIRED` / `UNCERTAIN` /
`forward_readback_unavailable` journal. The forward process must then exit completely. Recovery is a
separate invocation of the existing mutation-free production path:

```text
jebao-flow-local-qualify recover \
  --operation-id <same-opaque-id> \
  --write-enabled
```

The similarly named `jebao-flow-local recover` command is not used for this drill because its normal
input-preparation path replaces the install manifest. Drill PASS additionally requires the separate
recovery process to send the stored Flow-40 rollback payload exactly once, verify exact baseline
readback, terminalize the journal as `ROLLED_BACK`, leave no unfinished local-control record, and
create no qualification receipt. A zero-write `baseline_already_present` recovery is safe but does
not answer this drill's question. The persistent control service remains stopped until exact Flow 40
is independently confirmed, then the reviewed service is restored and checked again.

## Command pacing in the current local-control path

Static trace at `c47cbd3` establishes that `DeviceControlConfig.minimum_command_interval_ms`
does **not** impose a minimum send interval on `jebao-flow-local` direct or group writes.
`local_control_cli._coordinator()` constructs the production coordinator without passing that
setting. Its default `ConnectedDeviceWriter` uses `protocol.control_session.GizwitsSession`;
`send_once()` checks the existing permit and per-session duplicate-payload guard, then calls
`send_raw_control()`. The latter exchanges one sequence-bound control request without a
configured inter-command dwell.

The setting is consumed by the separate `devices.factory` / `LanJebaoDevice` path, where
`_respect_command_interval()` uses a last-command timestamp and a sleep. That legacy path is
not the current local-control writer and must not be started alongside it to obtain pacing.
Shared operation locks, sequential ACK/readback, fresh-cycle cadence, and unchanged-plan
deduplication are not an implementation of the named millisecond setting. Neither transaction
timestamps nor a successful bounded run establish a universal minimum physical send interval.

This is a source-level answer about configuration consumption, not a new hardware experiment,
a claim of excessive physical command rate, or a reason to invent a firmware timing gate.
Actual per-device send timing and any separately justified pacing change remain open. No
source, configuration, or write-path guard was changed to obtain this result.

## Still requiring evidence

The code is ready for isolated and simulator review, but hardware controls remain hidden until
qualification. The next evidence work should prioritize:

1. accepted captures for Pro scalar and schedule writes;
2. a bounded Sync/Async per-slot Flow test with exact ACK and readback;
3. product acceptance for the statically derived reset, schedule, and clock payloads;
4. DC Pump Pro schedule mode codes 1/2 and its two timer coupling paths;
5. Bar and Aquarium timer writes that include current `Auto*` fields;
6. Aquarium transport boolean direction and motor safe range;
7. Dosing Pump volume code physical unit, calibration relation, interval semantics, and write
   acceptance;
8. the P0 attribute-flag bit order as a retained wire-level differential.

An unsuccessful or unsupported result is retained as a result. It is not converted into a guessed
constraint or a mock-only implementation.
