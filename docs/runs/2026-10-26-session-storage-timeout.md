# Preserve raw-storage failure when heartbeat also times out

The new authenticated session reuse saved raw-frame observer errors for
propagation after the heartbeat. If the heartbeat also timed out, its transport
exception bypassed that propagation and opened a new connection. The real TCP
regression test failed on1decdb2: no expected OSError, two reads/authentications.

The transport-exception handler now raises any earlier raw-storage error before
reconnecting. The session is quarantined, so no subsequent read or control
proceeds past that failure. Normal heartbeat timeout reconnect is unchanged.
247 related tests pass, including the new combined-failure regression.

This was reproduced in an isolated peer, not by failing storage on live devices.
The previously observed production read timeout did not involve storage failure.
Runtime deployment and a new observation window are recorded separately.
