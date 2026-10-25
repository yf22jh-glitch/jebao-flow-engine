# Master-first maintenance OFF, 2026-10-25

The user superseded the incident proposal: preserve master/async_slave linkage and try master OFF, then slave OFF. No role release or role-restoration implementation was added. Only the two Pro OFF steps were reordered; Bar timer/OFF, qualified single-send, readback, persistent override, journal reconciliation and explicit resume remain unchanged.

The recorded slave-first failure drives an orchestration regression: baseline fails; candidate passes all 24 maintenance tests, including unchanged roles, duplicate requests, uncertain sends, restart, image/identity preservation and explicit resume. This does not establish firmware success for the new order. Actual authorized OFF is the next check; user owns subsequent reactivation and cleaning retry.

The local HA workspace checker preserved baseline/candidate and flags reviewed test changes; Jebao execution has no installed harness adapter. Independent isolated Python 3.12 tests supply test evidence; neither flag is represented as a harness pass. Original test was updated only for the intentional command order. One regression test was added. No existing assertion was removed.

run: aqua-maintenance-fix-20261025
decision: preserve linkage; deploy minimal master-first OFF ordering for authorized device check
unfreeze: n/a
