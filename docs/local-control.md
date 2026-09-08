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
  readback blobs;
- the deployment-wide operation lease and the existing per-device physical lock namespace;
- deterministic scalar writers for the audited schemas, with no `Auto*` status field exposed as a
  write target;
- exact Local Wavemaker Pro schedule-image writes;
- Sync and Async schedule composition in which every slave receives the current master's exact
  48-slot timing/mode image and may independently override Flow for each active slot;
- English Home Assistant/MQTT entity contracts, fresh read-only state polling, and a sequential
  command adapter;
- fresh install and service-staging commands that replace, rather than merge, prior generated
  artifacts.

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

This is implementation status, not a claim of device acceptance. At the time of this document,
the five retained device records contain **zero accepted write capabilities**. An encoder being
deterministic means only that it can produce bytes. A Home Assistant control appears only after a
version/content-bound qualification supplies two distinct, preserved ACK/readback receipt files
for that exact physical device. One product/function binding may contain receipt pairs for several
devices, but qualified device membership is derived only from those verified files and is never a
declared field that can name an unreceipted device. Each content-addressed file is hashed again and
its product, function, physical identity, content versions, firmware, intent, ACK, and readback
fields are validated before exposure and again before a send. Until then the same function appears
as a diagnostic for that device and cannot receive a command. Public catalog and HA output expose
only the qualified device count, never the physical identity keys.

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
install manifest. A Home Assistant projection creates 48 independent `30..75` Flow number entities
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

Every invocation of `prepare-install`, `ha-schema`, or `serve`:

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

`JEBAO_FLOW_LOCAL_QUALIFICATIONS` is optional. If it is absent, the resulting HA surface is
diagnostic-only. The public command result contains counts and content digests, never private paths
or raw device identifiers. Multiple current template roots use the platform path separator.
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
volume must already be mounted there before systemd starts the service. The current production
topology instead supplies `/hardware-safety` as a Docker named volume, so this bare unit is not the
current deployment adapter and will not operate there merely by being copied into systemd. A
compose-specific adapter must mount the same identified volume before this service shape is used in
that topology; absence remains an explicit dependency failure rather than permanent unknown state.

The generated service omits `--write-enabled` by default. In that mode a qualification path is not
retained, writer journals and write leases are not opened, and retained qualifications are neither
read nor projected as commands. The service therefore remains a useful read-only state and
diagnostic surface instead of failing merely because receipt files exist or later move. Adding the
flag to the staging command uses only the current verified receipts and records the separately
selected runtime opt-in; it does not
install or start the service and it does not replace the qualification, private-config, lease,
journal, or on-site live-write gates.

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

`serve --write-enabled` only exposes controls when current qualifications match the current app
audit version, app-catalog digest, template content digest, protocol source digest, and discovered
firmware, and the current private device settings also permit hardware writes. The protocol digest
includes the encoder and decoder sources as well as framing, profile, and schedule sources. Every
claimed receipt names a canonical `local-control-receipt-<sha256>.json` artifact in the fixed
qualification directory; a missing, changed, mismatched, reused-request, or wrong-device receipt
cannot expose a control. A singleton receipt for a device remains insufficient without invalidating
another device's complete pair. Running it against live hardware is a separate approved operation;
this implementation and its tests did not start the service.

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
schedule-linkage, temporary-schedule, or exact-restore journal, accepts only absent or terminal
legacy intent envelopes, and acquires `<physical_identity_key>.lock` for every target. Any other
unfinished local-control transaction blocks a new request until recovery terminalizes it.

Each forward frame is preceded by a fresh baseline read and durable `PREPARED` state. The journal is
advanced to `FORWARD_ATTEMPTING` before send. A missing ACK or readback never causes an automatic
forward retry. Recovery resolves current endpoints again and uses the exact pre-write rollback bytes.

MQTT retained and duplicate-marked command messages are rejected. Command discovery and
subscription use QoS 0 deliberately, so a broker cannot replay an older command after a newer
physical operation; a lost command leaves state unchanged and may be issued again by the operator.
Commands are consumed sequentially and the local runtime assigns the journal request id. Home
Assistant optimistic mode is explicitly off, and state is retained only after the coordinator has
verified exact device readback. That state is a verified point-in-time observation, not a claim that
an external app can never change the device later. Public MQTT/HA state uses logical member names
and English ASCII labels only; raw physical identifiers and current addresses remain private.

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
