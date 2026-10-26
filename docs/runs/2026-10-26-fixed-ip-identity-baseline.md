# Fixed-IP identity response baseline — 2026-10-26

User confirmed fixed addresses and authorized applying the optimization. Router read-only observations match one static DHCP assignment and current lease for each of three registered devices. Application address fields are empty; coordinator resolves before every scalar operation.

The current full discovery waits five seconds even after a device replies. Targeted UDP identity requests using the existing discovery packet/parser returned the expected MAC/vendor/product binding in 125.0ms, 7.5ms, 8.0ms, 10.1ms, 4.3ms, 3.5ms. Six raw responses are preserved privately. No device control was sent; all control journals were unchanged.

Evidence grade: (a) preserved raw response bytes; router assignments are (b) structured read-only tool observations.

Artifact: fixed-probe-private.json
UTC collected: 2026-10-26T02:21:14.117299+00:00
Artifact SHA-256: 331ebc0246104b93c998acfe1fb7cfd33e720643922a485abeb051be80c6f4af

Decision: use configured fixed IPs for a direct current identity probe, return on its valid reply, preserve metadata/raw and all existing qualified serial control checks. Keep the existing full discovery path for unconfigured devices. Timeout/mismatch must never authorize a write or redirect a configured target. No protocol/control-frame semantics or native ASYNC harness changes.

run: docs/runs/2026-10-26-fixed-ip-identity-baseline.md
decision: optimize measured full-window waiting with current fixed-address identity evidence
unfreeze: n/a
