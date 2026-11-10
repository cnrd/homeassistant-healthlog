# Delivery reliability and limitations

[Documentation index](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Exact API audit](API_CONTRACT.md)

The integration prioritizes avoiding silent duplication or loss. That is not an
unconditional exactly-once guarantee: the source, HA storage, HealthLog's API and
operator decisions all affect the result. In particular, some uncertain uploads
must stay held until a person checks HealthLog.

## Capture and delivery lifecycle

Each measurement profile has an independent delivery queue. A new trigger
identity starts this sequence:

1. **Admit a pending capture:** persist the event identity, original measurement
   time, mappings and initial numeric/rendered snapshots before treating capture
   as durable.
2. **Settle:** collect relevant late sensor/template updates during the configured
   settling window. Unrelated events do not refresh readings indiscriminately.
3. **Freeze:** validate freshness, numeric values, units, ranges and required
   context; persist the resulting canonical measurement data.
4. **Record delivery intent:** persist the identity reservation and in-flight
   marker before making HTTP requests.
5. **Deliver and acknowledge:** remove only records with an accepted response or
   a proven batch duplicate. Persist acknowledgement before changing queue memory.

A single event may contain several metric records. Delivery uses one metric per
request, including singleton batch requests, so one rejection need not discard
its successful siblings. Held events still consume queue capacity.

Profile edits affect future captures, never reinterpret frozen records. Editing
one profile does not restart sibling collectors or interrupt their settling.
Connection changes reload its profiles, retaining their durable queues.

### Frozen measurements

A frozen record keeps its value, canonical unit, original timestamp, notes and
glucose context. It is not recalculated from live sensor values on upload, retry
or options changes. Recovered pending captures use stored snapshots rather than
unrelated states arriving after restart; their stored settings are also retained.

Local validation failures are different from server rejection. Invalid readings
are counted as rejected and are not uploaded. Empty captures can be completed
locally without retaining invalid raw values. The retry button cannot reconstruct
them; fixing a mapping does not rewrite an earlier capture.

## Event identity

Timestamp triggers use a normalized timezone-aware instant. Stable event-ID
triggers use the source's opaque ID, with the triggering HA state's update time
captured once as measurement time. Neither uses upload/retry time.

An external measurement ID derives from the random stable profile identity,
event identity and metric—not the numeric value. Two equal readings from
different genuinely new events therefore have distinct identities, provided
their source values pass freshness/validation. Startup/restored baselines and
duplicate identity notifications are not new events.

Completed event identities and timestamp claims are retained for the profile's
lifetime; these histories grow with events. The bounded delivery queue never
evicts an older pending record to admit a new one. Removing/recreating a profile
loses its local identity/history, so do not use that as a replay mechanism.

## Delivery paths and duplicate prevention

The [metric table](METRICS.md#supported-measurements) identifies the path used for
each metric without notes. The two HealthLog endpoints are not interchangeable.
**Any nonempty device notes force the ordinary endpoint for all mapped metrics.**

### Durable batch identities

Sixteen exactly mapped metrics without notes use `POST /api/measurements/batch`.
HealthLog stores their stable external IDs and enforces per-account/type/source
uniqueness. The integration sends no source assertion: a properly restricted
measurement token causes HealthLog to assign `EXTERNAL`.

Only the matching per-entry **inserted** or **duplicate** result is acknowledged.
A 200 response, a skipped entry or a summary count alone is not sufficient. A
failed/malformed answer stays queued for retry; a skipped entry is rejected;
an unexpected update is an identity conflict.

The same frozen ID can be retried after a lost response, restart or failed local
acknowledgement, even after the 24-hour response cache expires. This depends on
the server retaining its identity protection. Do not delete or alter records
awaiting retry; a write-only client cannot verify later manual changes or restore
a server row you intentionally removed.

### Ordinary endpoint limitations

The other nine metrics, and any metric with notes, use `POST /api/measurements`.
This preserves ordinary metric names, notes and explicit glucose context without
mislabeling metrics or fabricating sleep intervals.

That endpoint has no equivalent durable client event-ID contract. It has a
24-hour `Idempotency-Key` response cache and a timestamp-collision guard, but
these are not a permanent atomic commit-and-identity guarantee. A server may
store a measurement and lose/fail its response before caching it. A token
replacement may also select a different response-cache authority.

The integration therefore **does not automatically replay an uncertain ordinary
write, even within 24 hours**. A 409 collision does not prove that the correct
event, value, notes and clinical context already exist. The exact upstream
analysis is in [the API audit](API_CONTRACT.md#why-not-retry-ordinary-writes-with-the-same-key-for-24-hours).

## Ambiguous deliveries

An **ambiguous** delivery is one whose outcome cannot be established safely:
HealthLog might have stored it, or might never have received it. Ordinary-route
timeouts/connectivity failures, 5xx, unrecognized successful responses and 409
conflicts are held this way. Cancellation in flight or a failed local
acknowledgement leaves durable intent; recovery holds the record instead of
blindly sending it again.

This conservative behavior can hold even a request that never reached the
server. Other eligible records can continue after applicable pacing/backoff.
Restarting HA, changing tokens, waiting past 24 hours or pressing Retry does not
release these holds. The restricted measurement token cannot read records back
to resolve them automatically.

## Manual reconciliation

Reconciliation is an explicit decision about **one profile's one held record**.
An incorrect decision can acknowledge an undelivered record or permit a duplicate.

1. Identify the affected profile through its **Delivery status** sensor. Open
   that profile's **Reconfigure → Reconcile an uncertain delivery** action.
2. Select the held item. The UI identifies its metric, original timestamp and a
   short identity suffix; diagnostic entities do not expose its biometric value.
   Do not confirm an outcome yet.
3. Open the **intended HealthLog account** in its web UI. Search the metric and
   original timestamp, allowing for the server's millisecond precision. Inspect
   its `EXTERNAL` readings/source filter if available.
4. Compare the intended value, unit, notes and context against trusted source
   history associated with that original event/settling window—not simply the
   source's current live value. A timestamp match alone is insufficient.
5. Choose an outcome only when you can establish it:
   - **I verified it exists in HealthLog:** acknowledge/remove the local record.
   - **I verified it was NOT stored; allow a new attempt:** release the hold so
     the same frozen record may be attempted again, subject to persisted backoff.
6. Confirm that you checked HealthLog and understand the consequences. After a
   released record is delivered, verify one correct server record and an empty
   queue for that item. Sibling profiles are not reconciled by this action.

**If you cannot prove either outcome, leave the record held.** Do not recreate
the profile, edit its queue file, fake a new event ID, or delete a server record
to force replay. Cache and duplicate responses cannot establish the current
state of a record that was manually edited/deleted after its original write.

For `identity_conflict`, read the next section before choosing an outcome. A
known local collision cannot be represented as two server rows just by selecting
"not stored" and trying again.

## Timestamp collisions and identity conflicts

HealthLog also groups identity by account, metric, source, timestamp and stage.
A batch request with a different external ID at an occupied natural timestamp
can **update an existing row**, not insert a second immutable event. Distinct
events for the same metric/account need distinct timestamps at server
millisecond precision. No timestamps are artificially shifted to bypass this.

Profiles in one connection coordinate durable timestamp claims before HTTP.
Known sibling collisions are held locally without sending a second request.
These claims cannot see different connections/tokens or unrelated writers.
Deleting a profile also removes its persistent claim history.

An unexpected server **updated** result is held as `identity_conflict`, never
treated as success or automatically retried. Inspect HealthLog immediately: the
server may already have changed another row. The integration cannot undo that
write, decide which record is clinically correct, or read the account back.
Correct overlapping sources/configuration and investigate/correct server records
as appropriate; acknowledge only a verified representation of the intended
measurement. If it remains unrepresentable or uncertain, retain the hold.

An upstream refusal rather than overwrite is needed for stronger cross-writer
protection; see [the API gap](API_CONTRACT.md#healthkit-oriented-batch-route).

## Retry and rate limits

Backoff and next-attempt times are persisted per profile. Temporary failures
start at 120 seconds and grow exponentially to 3840 seconds, unless a longer
`Retry-After` applies. Seconds and HTTP-date header formats are supported.
Normal requests are at least two seconds apart per profile; new profiles default
to a five-second delivery delay. This paces successful singleton requests and
coalesces changes, not retries after errors. Settling (default 10 seconds) waits
for related updates; freshness (default 120 seconds) checks source age, not a
delivery delay. Saved profile settings survive upgrades unchanged. The server's
aggregate limits still apply across profiles, other tokens and other clients:
five seconds per profile is not a global rate-limit guarantee.

| Outcome | Integration behavior |
| --- | --- |
| 401 | Pause the connection's profile deliveries and start parent reauthentication. A restart may recheck credentials but does not replace them. |
| 403 | Pause the affected profile for insufficient scope/API-disabled attention; this is not classified as invalid credentials. |
| 400/422 or batch **skipped** | Hold that frozen record as rejected. Eligible siblings are retained; manual retry can release rejection after the cause is fixed. |
| Ordinary-route 409 | Hold as ambiguous, not a proven duplicate. A generic batch HTTP 409 is rejected, not a successful duplicate result. |
| 429 | Apply backoff and the server's `Retry-After`. |
| Network/5xx/unrecognized response | Retry batch records with stable IDs; hold ordinary records as ambiguous. |
| 404/413 | Pause the affected profile for server/path/body-limit attention. |
| Redirect | Never follow automatically. Pause for attention; ordinary-route redirects are also conservatively held as ambiguous. |
| Batch **updated** | Hold as an identity conflict and inspect server state. |

**Retry delivery** wakes that profile, clears its operational pause and releases
rejected records. It never bypasses persisted backoff, resets authentication,
releases ambiguous/conflict holds, edits frozen data or reconstructs locally
invalid readings. A rejected record is retried unchanged: changing its mapping
does not repair that already-frozen payload.

Audited server ceilings and their provenance remain in
[API pacing](API_CONTRACT.md#pacing), rather than being configuration promises
made by the integration.

## Crash, restart and storage recovery

The implementation separates collection, queue storage, transport and worker
timing. Queue changes copy data before committing; atomic writes are read back
before memory is acknowledged. Cancellation waits for an active storage commit
so an unloaded worker's executor write cannot race its replacement. Deferred
shutdown saves are not treated as durable capture acknowledgements.

On ordinary restart, queues restore pending captures, frozen data, holds,
delivery intent and identity history. Sibling claims are reconstructed from
their durable profile ledgers. Corrupt queue files/retained corruption markers
fail closed rather than silently starting an empty delivery queue. Restore an
appropriate backup or repair storage before resuming; do not delete files as a
way to clear delivery problems.

HA owns profile configuration separately from queue writes. A capture whose
profile metadata is lost after interrupted setup/partial backup restoration may
remain in an unattached store. Such files are retained, not automatically
uploaded under a new profile or garbage-collected. Operator/backup recovery is
needed; this is not automatic recovery of arbitrary filesystem damage.

## Storage failures and capacity exhaustion

Existing queued records are not evicted to make room. Capacity counts whole
pending/frozen/held events, not individual metrics. Reducing the configured
capacity never removes admitted data.

However, a full queue or failed initial storage write can prevent a new source
event being durably admitted. HA cannot capture events while offline, and a
source may not retain enough history to recover them later. These are limits,
not an unconditional no-loss guarantee. Fix the cause promptly, stop the source
if appropriate, back up, and reconcile genuine source history against HealthLog.
See [storage troubleshooting](TROUBLESHOOTING.md#storage-errors-or-a-full-delivery-queue).

## Queue deletion and retention

- Deleting a profile while its connection is loaded explicitly removes that
  profile's delivery queue, held data and persistent identity history, including
  its corrupt queue copies. It does not delete HealthLog's server records.
- Profile deletion while disabled, interrupted setup and partial restores can
  leave unattached stores. Startup does not automatically discard them.
- Explicit connection removal clears all its owned profile stores, including
  unattached/corrupt copies. Other connections' stores are not removed.
- To retain queued data, **disable the connection rather than deleting it**.
  Do not remove/recreate profiles to force delivery or replay old events with
  a new profile identity.

Protect local stores and backups as described in [Security](SECURITY.md#local-storage-and-backups).
