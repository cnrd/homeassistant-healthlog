# Troubleshooting

[Documentation index](README.md) · [Configuration](CONFIGURATION.md) · [Reliability](RELIABILITY.md)

Start with the affected measurement profile, not a new token or replacement
profile. Keep queued data intact while investigating. Installation requirements
and tested versions are in [Compatibility](CONFIGURATION.md#compatibility).

## Use the operational entities

Each profile provides **Delivery status** and **Retry delivery** entities.
The sensor is `ready`, `pending` or `attention_required`. It is not a network
probe: `ready` with an empty queue does not prove a token/server is working.

| Attribute | How to use it |
| --- | --- |
| `authentication` | `reauth_required` means the connection's token needs attention; otherwise it is not proof of a successful probe. |
| `queued_batches` | Queued capture events, not the number of HTTP batch requests. Includes pending/held events. |
| `pending_measurements` | Frozen metric records still queued, including holds; an unsettled capture may not yet be counted here. |
| `ambiguous_measurements` | Records whose delivery outcome cannot safely be assumed. |
| `quarantined_measurements` | Frozen records rejected by the server, retained for attention/retry. |
| `identity_conflicts` | Timestamp/identity collisions requiring investigation. |
| `rejected_readings` | Locally invalid/stale readings that did not become uploadable payloads. |
| `last_success`, `last_error` | Delivery activity and sanitized operational error, not raw response bodies. |

Later success can clear a recent error while rejection counts remain useful.
These entities deliberately do not expose biometric values, tokens or templates.

**Retry delivery** affects only that profile. It wakes its worker, clears an
operational pause and permits rejected records to be tried again. It does not
reset persisted backoff, repair frozen values, reconstruct locally rejected
readings, fix credentials or release ambiguous/identity-conflict holds.

## Nothing uploads

1. Check that the connection is enabled and has a measurement-profile subentry.
   One connection can contain several source profiles without additional tokens.
2. Verify a genuinely **new trigger identity** occurred after setup. Initial,
   restored and late-loading baseline states are intentionally not imported.
3. Check the selected trigger attribute, if any. Timestamp mode requires a
   timezone-aware ISO value; stable event-ID mode needs a unique ID per event,
   not a reading or `on`/`off` state.
4. Inspect the sensor's queue count, rejection counters and error. Allow for
   settling, the delivery delay, multiple metrics and any backoff. New profiles
   use a five-second delivery delay and singleton requests. Five measurements
   need roughly 20 seconds of pacing between first and last, plus settling,
   coalescing and network time. Upgraded profiles retain their saved delay;
   change a saved 60 seconds to 5 through that profile's Reconfigure action.
5. If the queue is empty but local rejection counts rose, follow the
   [invalid/stale checks](#invalid-or-stale-readings). If the queue is held, do
   not recreate the profile or fake a new event ID to force export.

See [trigger identity](CONFIGURATION.md#trigger-selection-and-identity) and
[delivery timing](CONFIGURATION.md#timing-and-delivery-queue-capacity).

## Authentication and permission errors

### Invalid, expired or revoked token

- Enter the full `hlk_…` restricted measurement token, without `Bearer` or whitespace.
- Confirm it was created as a **Measurement ingest token** with
  `measurements:write`, for the intended HealthLog account. Do not substitute a
  full-access login token.
- A 401 requests reauthentication and pauses delivery across that connection's
  profiles. Use the connection's **Reconfigure** action or reauthentication
  prompt to replace it with a restricted token for the **same account**.
- Check queues after replacement. Frozen records retain their original values
  and timestamps; uncertain holds still need reconciliation.

Setup cannot validate token owner/scope. A failed arbitrary GET is not a useful
credential test: this token is write-only. Do not weaken account security or
grant broad account access to work around an ingest failure.

### Insufficient permissions or API disabled

A 403 is distinct from invalid credentials. Check the token scope, server API
ingestion setting and any reverse-proxy/access policy. The affected profile
pauses; after fixing the cause, use Retry or reload/reconfigure as appropriate.
Persisted backoff still applies. Other profiles may encounter the same server
policy when they next attempt delivery.

## Connection or certificate errors

1. Use the actual API base address, including any port and installation prefix.
   A scheme-less address uses HTTPS, even with insecure HTTP allowed.
2. Check reachability **from HA's network/container**, not only your browser.
   `localhost` inside a container means that container, not another host/service.
3. For HTTPS, check certificate validity, hostname matching and trust in HA.
   Use a trusted certificate/reverse proxy or supported private-CA environment;
   there is no certificate-verification bypass here.
4. For intentionally plain HTTP, enter `http://` explicitly and enable
   **Allow insecure HTTP**. That does not fix a bad HTTPS certificate or enable
   fallback. Understand the [plaintext exposure](SECURITY.md#transport-security).
5. Check reverse-proxy redirects/login pages. Authenticated redirects are never
   followed, including cross-origin or downgrade redirects. Configure the final
   base URL instead. A 404 may indicate a wrong path; 413 needs body-limit review.

A LAN HealthLog server does not require internet access. Ordinary-route network
failures can still be conservatively held even if the request never arrived;
see [ambiguous deliveries](RELIABILITY.md#ambiguous-deliveries).

## Invalid or stale readings

- Inspect the entity in **Developer Tools → States**. Verify that it exists,
  is available/not restored, and supplies the expected state or attribute.
- Check the exact attribute key and its actual unit. The unit field is not a
  request to relabel an incompatible quantity. Use the [metric reference](METRICS.md).
- Check template syntax and numeric output, with an explicit result unit.
  Integration variables such as `source` are not automatically available in the
  standalone preview. Avoid defaults that turn missing data into zero.
- Check update times. A new trigger alone does not make another entity fresh;
  repeated equal states/attributes may leave `last_updated` unchanged. Confirm
  the source integration reports updates in a usable way.
- Increase settling only when readings genuinely arrive late. Do not widen
  freshness merely to hide an old/unreliable source. Template-only/domain
  dependency checks can be conservative; see [template freshness](CONFIGURATION.md#dependencies-and-freshness).
- For glucose, select the real context on its conditional step. HRV must be
  SDNN, and water mass cannot come from an attribute that is only percentage.

Locally invalid readings are counted, not retained as importable records the
button can rebuild. Fix sources for future genuine events. If a valid reading
was missed, compare trusted source history with HealthLog before any manual
recovery; preserve its original time and do not manufacture a new trigger.

## Rejected frozen records

HTTP 400/422 or a batch **skipped** result holds a record as rejected. Check
server compatibility/validation, the profile's types, source-unit conversions,
notes and clinical context. Only an explicit per-entry batch duplicate is
success; generic 409 responses are not duplicate acknowledgements.

Fixing a mapping affects future captures, not the already-frozen record. Retry
sends that frozen data unchanged and should be used only after the actual cause
is resolved. The reconciliation screen is for uncertain/conflict records, not
an editor for rejected payloads. Keep unresolved records and report redacted
diagnostics rather than editing queue files or dropping profiles.

An error such as `http_200` can mean a response was successful at HTTP level but
did not match the required acknowledgement schema. Treat it as attention, not
proof of delivery; the [reliability rules](RELIABILITY.md#retry-and-rate-limits)
determine whether retry or manual review is safe.

## Held or uncertain deliveries

Follow the [manual reconciliation procedure](RELIABILITY.md#manual-reconciliation)
for the selected profile. Compare account, metric, original timestamp, actual
value, unit, notes and context in HealthLog against source history. If the
outcome cannot be proved, leave it held. Restart, token replacement, waiting
past 24 hours and the retry button do not release an uncertain hold.

For `identity_conflict`, inspect HealthLog immediately: an unexpected batch
update may already have changed another row. Remove overlapping source
configuration, not identity safeguards. Do not shift timestamps or retry
indefinitely to represent two records at a server identity that cannot hold them.

## Storage errors or a full delivery queue

Check HA free space, filesystem health/permissions, configured capacity and
the number of held events. Existing queued data is not evicted to make room,
but new events can fail admission and may need source-history reconciliation.

Stop the source if appropriate, back up, fix storage or increase capacity, then
review [recovery limits](RELIABILITY.md#storage-failures-and-capacity-exhaustion).
Corrupt queues fail closed and need backup/storage repair, not file deletion.
If the connection cannot load, resolve that before opening its reconciliation UI.
Unattached stores are retained for safety; do not attach one to a newly created
profile/account blindly or delete it as a reset shortcut.

## Apple Health measurements do not appear

1. Confirm the measurement exists correctly in HealthLog first. If not, fix
   the HA → HealthLog path before investigating iOS.
2. Enable **Write readings to Apple Health** on the restricted measurement
   token that sent it. `EXTERNAL` is the correct source; do not try to forge
   `IMPORT`, `MANUAL` or `APPLE_HEALTH` attribution.
3. Check [server/app compatibility](CONFIGURATION.md#compatibility), per-type
   write direction and HealthKit write permissions. Not all HealthLog metrics
   have a writable Apple Health equivalent.
4. Open the HealthLog iOS application to test foreground synchronization.
   iOS decides when background execution is granted, so eventual delivery is
   not a fixed-time promise.
5. For older/many readings, check the app/server's supported history behavior.
   At the [upstream audit](API_CONTRACT.md#sources-inspected), background mirroring
   covered a recent seven-day window with at most 50 writes per opportunity;
   toggling the setting later did not promise immediate full historical backfill.
   These upstream bounds may change with app/server versions.

Home Assistant does not write to HealthKit. Apple Health/iOS was outside the
integration's executed E2E scope; its behavior is an upstream dependency.

## Report a problem safely

Include integration, HA and HealthLog versions, reproducible steps and the
profile's sanitized operational status or diagnostic download. Review every
attachment. Do not post tokens, raw health values, entity histories, templates
with private literals, `.storage` files or private E2E workspaces.

Use [GitHub issues](https://github.com/cnrd/homeassistant-healthlog/issues) for
ordinary bugs and [private vulnerability reporting](../SECURITY.md) for security
issues. Diagnostics are described in [Security](SECURITY.md#logs-and-diagnostic-privacy).
