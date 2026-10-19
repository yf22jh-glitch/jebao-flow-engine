# VSON reservation concurrency

Two concurrent reservation additions could both read the same free slot and
write the same reservation number. This is an application ordering defect;
it does not depend on a hypothesis about pump or feeder firmware behavior.

The feeder client now serializes reservation reads, allocation and writes with
the same lock used by edits and deletions. An ambiguous write is not retried.
Cancellation releases the lock so the next request can proceed. The existing
one-shot feeding lock and cooldown remain independent of reservation changes.

`tests/unit/test_vson_feeder_schedule.py` preserves the deterministic reproduction
and failure diagnostics: the complete set of allocated numbers, stored times,
write ordering and request counts. Six concurrent additions must preserve all
six new reservations. A timed-out write must produce one request, and a cancelled
allocation must produce none.

Run the feeder suite with Python 3.14 and Home Assistant 2026.9.4:

```sh
python -m pip install homeassistant==2026.9.4 pycryptodome==3.23.0
python -m unittest discover -s tests/unit -p 'test_vson_feeder*.py' -v
```

The tests replace cloud requests and do not feed or edit a physical feeder.
Serialization covers requests made by this client, not concurrent edits from an
external mobile app. The frozen native pump write harness and the parked Q1/Q2
questions are unchanged.
