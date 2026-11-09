# HealthLog for Home Assistant

Send measurements from Home Assistant sensors to your **self-hosted
[HealthLog](https://github.com/MBombeck/HealthLog)** server with a restricted
`measurements:write` API token. Map any manufacturer's sensor states,
attributes, or Jinja-derived numbers. A persistent local queue keeps captured
events and frozen values across restarts.

**Version: 1.0.0-beta.1 — prerelease, not yet catalog-approved.**
Some API writes cannot be permanently deduplicated by HealthLog. Uncertain
deliveries through that endpoint are held for human reconciliation, not blindly
retried. Read [Reliability](#reliability) before enabling a profile. This is not a
medical device, diagnostic tool, historical importer, or automatic person detector.

## Requirements

- Home Assistant **2026.10.4 or newer**, Python 3.14. Unit-tested with 2026.10.4
  and real-service E2E-tested with 2026.11.0. Uses current Probatio schemas.
- HealthLog **1.46.0+**, with restricted measurement ingestion enabled. The API
  implementation was audited at **1.47.1**, commit
  `f86a7a61e1e6226732474bfbdc5346748b2027e1`. Real-service validation is recorded
  separately in [the E2E results](docs/E2E_RESULTS.json).
- A server URL, with optional port and installation path prefix. **HTTPS is the
  default**: a scheme-less address is prefixed with `https://`. Certificates
  must be trusted by Home Assistant; TLS verification cannot be disabled.
  Explicit `http://` URLs require **Allow insecure HTTP**, off by default.
  HTTP exposes API tokens and health measurements to anyone capable of observing
  the connection. There is never automatic HTTPS-to-HTTP fallback.
- Both **LAN-hosted and remotely hosted** HealthLog servers work. A LAN server
  does **not require internet access**.
- A restricted **Measurement ingest token** belonging to the intended person.
  Full-access login tokens are not supported. Scope and owner are not exposed
  through a safe write-token introspection endpoint: verify them in HealthLog.
- For Apple Health: HealthLog iOS **1.5.0 (build 302) or later**, including the
  then-current TestFlight build, with appropriate HealthKit write permissions
  and per-type synchronization directions. Older iOS 1.4.1 does not mirror
  token-origin `EXTERNAL` readings. See [server #1272](https://github.com/MBombeck/HealthLog/issues/1272)
  and [iOS #28](https://github.com/MBombeck/healthlog-iOS/issues/28).

<!-- ha-marketplace:exclude-start -->
## Installation

Install the **v1.0.0-beta.1 prerelease** from this custom repository, or use the
manual route below. This integration is **not listed in the public catalog** yet.
Enable prerelease/beta versions in your installer to select beta1.

1. In Home Assistant's Marketplace, add
   `https://github.com/cnrd/homeassistant-healthlog` as a custom repository, then
   install **HealthLog** and select the prerelease if necessary. On versions
   using HACS, add the same URL under **HACS → Custom repositories**, category
   **Integration**. Do not select `content_in_root`.
2. Alternatively copy **only** `custom_components/healthlog/` into your Home
   Assistant configuration's `custom_components/` folder.
3. Restart when Home Assistant requests it (always after replacing loaded code).
4. Open **Settings → Devices & services → Add integration → HealthLog**.

[![Start HealthLog setup](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=healthlog)
<!-- ha-marketplace:exclude-end -->

## Create the HealthLog token

In HealthLog **Settings → API & Tokens → Measurement ingest token** (called
**API → Measurement tokens** in some versions), create a named token. Copy the
complete `hlk_…` value when shown; do not prepend `Bearer` in Home Assistant.
Only the `measurements:write` permission is needed. It writes to its owner's
record, not a delegated/shared record.

Enable **Write readings to Apple Health** on this token if you want mirroring.
It is **off by default**. The server still assigns `source: EXTERNAL`; the
integration never claims `IMPORT`, `MANUAL`, or `APPLE_HEALTH`. The server's
`appleHealthMirror` decision tells the iOS app what to mirror.

**Apple Health synchronization is done by the HealthLog iOS application, not
Home Assistant.** iOS background execution is opportunistic, not immediate.
Open the app when troubleshooting. Current background mirroring is bounded
(recent seven days, at most 50 writes per background opportunity); historical
behavior depends on server/app versions. Not every HealthLog metric has an
Apple Health writable equivalent.

## Configure a measurement profile

Each integration config entry is a **connection**: one HealthLog server and one
account's restricted token. Add as many **measurement profile subentries** as
needed beneath it—no repeated integration setup or extra tokens for different
entities belonging to the same account. For example, one connection can contain
separate Weight scale, Blood pressure and Glucose profiles.

Each profile has its own name, trigger, mappings, timing, persistent queue,
delivery-status sensor, retry button and random stable identity. Creating,
editing or deleting one profile does not restart sibling collectors or interrupt
their settling windows. Connection/token changes apply to all its profiles.

**Different people/accounts require separate connections and appropriate
tokens**, with person-associated sources. There is no automatic Home Assistant
`person` identification. A shared scale must already expose an unambiguous
per-person event/source.

### Connection

Enter the server's base URL and token. Optional ports and installation subpaths
are supported, for example:

| Input | Connection |
| --- | --- |
| `healthlog.local` | `https://healthlog.local` |
| `healthlog.local:8443/healthlog/` | `https://healthlog.local:8443/healthlog` |
| `http://192.168.1.20:3000/healthlog` | Requires **Allow insecure HTTP** |

Enabling **Allow insecure HTTP** does not change a scheme-less address into
HTTP: enter `http://` explicitly. HTTPS certificates are always verified, even
when this option is enabled. A connection/certificate failure never causes a
fallback to HTTP. Unsupported schemes, embedded credentials, query strings,
fragments and malformed URLs are rejected.

Use a restricted token belonging to the intended account. **Setup performs only
local syntax checks**, not arbitrary GETs or test writes. An invalid, expired or
under-scoped token is discovered on the first *real future measurement*. An empty
queue showing `ready` is not proof of successful authentication.

Use **Reconfigure** or the reauthentication prompt to replace an expired/revoked
token. Verify the replacement belongs to the **same account**. Keep the same
hostname and installation path prefix, to avoid redirecting queued health data.
Protocol and port may change when moving that same server behind HTTPS or
explicitly opting into HTTP; the same security policy applies in all flows and
at runtime. For another account/server create a separate entry. The write-only API cannot
independently verify replacement ownership; account selection is your
responsibility, not a server-enforced account binding.

After saving the connection, setup continues with its first measurement profile.
Later, use **Add measurement profile** on that connection's integration page.
Use the individual profile's **Reconfigure** action to edit its name, trigger,
mappings and timing or reconcile its uncertain deliveries. Credentials are only
requested when creating/reconfiguring the connection, never when adding profiles.

This beta1 structure is a **full pre-publication replacement**. Remove earlier
test installations/config entries and configure it afresh; no old profiles,
credentials, queues or formats are imported or migrated.

### Trigger identity

- Select a trigger entity and optionally an attribute containing the identity.
- **Timestamp** (recommended): a new timezone-aware ISO timestamp represents a
  new measurement, e.g. a source's `measured_at`. That exact instant is retained,
  never upload time. HealthLog stores milliseconds; finer source precision is
  necessarily normalized by its server.
- **Stable event ID**: an opaque, unique ID for each measurement event. The
  measurement time is first receipt (`last_updated`) in Home Assistant, captured
  once. Use timestamp mode when the source provides its original measurement time.
  Do not use the numeric reading, `on`/`off`, or a reused counter as identity.
- Startup, restored states, a late-loading entity's initial baseline, duplicate
  identity notifications and attribute-only changes do not create measurements.
  Saving setup does **not** upload current or historical states.

### Mappings, units and templates

Add one mapping per metric; duplicate types are refused. For each mapping:

1. Choose the actual HealthLog metric (the selector shows its canonical unit).
2. Select a numeric entity state, or an exact attribute key. An entity may be
   reused for multiple attribute mappings.
3. Set the **source unit**, or leave it blank for the entity's
   `unit_of_measurement`. Attribute units often need explicit configuration.
4. Optionally add a Jinja **value template** returning one finite number. A
   template's explicit unit describes its *result*, not a desired output unit.

With a selected entity, templates have `value` (state/selected attribute),
`source` (detached state, attributes, entity ID and update timestamps), and
`source_entity` (entity ID). They may also reference other entities with
`states()` and `state_attr()`. Relevant and dynamically discovered dependencies
are tracked during settling, including dependencies discovered after an error.
Template-only mappings derive freshness conservatively from their entity
dependencies. A constant-only template has no freshness evidence and is rejected.
Avoid `float(0)` fallbacks: they can manufacture a health reading from an invalid
source. Syntax errors are refused in the editor; nonnumeric/invalid output is
counted as rejected, never converted to zero.

Blood glucose requires an explicitly configured **FASTING, POSTPRANDIAL, RANDOM,
or BEDTIME** context, matching the ordinary API validator. It is not inferred.
Choose `BLOOD_GLUCOSE` in the mapping editor; after submitting the profile,
a **Blood glucose context** step appears only for profiles containing that metric.
Other profiles skip it entirely. HA's standard Add/Edit object dialog cannot
conditionally hide a field based on its metric dropdown, so context is collected
on that follow-up step rather than cluttering every mapping dialog.
Use separate context-specific profiles/triggers if it varies. If the context is
unknown, do not configure this mapping. HRV means **SDNN**, not RMSSD.

### Timing and capacity

Defaults: **10 s settling**, **120 s freshness**, **60 s delivery/coalescing
delay**, **500 queued events**. During settling, related readings may arrive
late; when due, the snapshot is validated and frozen immutably. Freshness rejects
readings updated too far before the trigger. Recovered pending captures use
their stored snapshots, not unrelated live values after restart.

The delivery delay coalesces changes and spaces individual requests (minimum
two seconds). Each request contains one metric, even on the batch route, to
isolate rejections and uncertain outcomes. This favors correctness over bulk
throughput; a 25-metric event at the default delay takes about 25 minutes to drain.
The queue capacity counts pending/frozen/held events, not individual metrics.
Reducing capacity never evicts existing data. Capacity/storage exhaustion raises
operational errors; events that cannot be durably admitted cannot be recovered
automatically from a source without history. Fix promptly and reconcile source
history. This is not a replacement for backups.

Optional device notes (up to 500 characters) are preserved. **Notes force the
ordinary endpoint for every mapped metric**, because the batch schema has no
notes field; this changes duplicate guarantees, as explained below.

## Examples

These are editor field examples, not YAML integration configuration. Entity IDs
are fictional. Values in the tests are synthetic and not anyone's health data.

### Simple weight sensor

- Trigger: `sensor.scale_measurement_time`, timestamp mode.
- Mapping: `WEIGHT`, entity `sensor.scale_weight`, unit `kg` (or its actual
  `lb` unit). Leave attribute/template/notes blank.
- The source must update its measurement timestamp for *every* new weighing,
  even when two readings happen to be equal.

### Scale with multiple attributes

Trigger: `sensor.scale`, attribute `measured_at`, timestamp mode. Add mappings:

| Type | Entity | Attribute | Source unit |
| --- | --- | --- | --- |
| `WEIGHT` | `sensor.scale` | `weight` | `kg` |
| `BODY_FAT` | `sensor.scale` | `body_fat` | `%` |
| `BODY_MASS_INDEX` | `sensor.scale` | `bmi` | `kg/m²` |

These names are examples, not auto-detected defaults. Check Developer Tools →
States. Never map a `water_percentage` attribute to `TOTAL_BODY_WATER` with kg.

### Jinja-derived measurement

If the source reports a real measured mass in grams as `mass_g`, map `WEIGHT`,
source entity `sensor.scale`, result unit `kg`, with:

```jinja
{{ source.attributes.mass_g / 1000 }}
```

Or use a template-only mapping with result unit `kg`:

```jinja
{{ states('sensor.measured_mass_g') | float / 1000 }}
```

The second template's entity dependency supplies freshness. Templates execute in
Home Assistant, not HealthLog; the integration-specific variables are not
automatically available in Developer Tools' standalone preview.

### Multiple people

Create connection A using person A's HealthLog measurement token, then add a
scale profile using `sensor.scale_a_measurement_time` and only A's readings.
Add A's cuff or glucose sensor as additional profiles in that same connection,
without another token. Create connection B with B's own token, then add its scale
profile using `sensor.scale_b_measurement_time` and B's readings. Do not
send an undifferentiated shared-scale event to both profiles. Different profile
identities intentionally produce distinct server IDs: overlapping profiles for
the *same* person's event can create duplicates and are a configuration error.

## Supported metrics

All 25 original metric types are retained with current server plausibility
bands. Range checks are data-integrity guardrails, not clinical advice.

| Type | Canonical unit | Accepted range | Default route (no notes) |
| --- | --- | --- | --- |
| WEIGHT | kg | 1–500 | Durable batch |
| BODY_MASS_INDEX | kg/m² | 8–70 | Durable batch |
| BODY_FAT | % | 1–80 | Durable batch |
| FAT_MASS | kg | 0–250 | Ordinary |
| FAT_FREE_MASS | kg | 10–250 | Ordinary |
| LEAN_BODY_MASS | kg | 10–250 | Durable batch |
| MUSCLE_MASS | kg | 5–200 | Ordinary |
| TOTAL_BODY_WATER | kg | 5–100 | Ordinary |
| BONE_MASS | kg | 0.5–8 | Ordinary |
| VISCERAL_FAT | rating | 0–30 | Ordinary |
| BLOOD_PRESSURE_SYS | mmHg | 40–300 | Durable batch |
| BLOOD_PRESSURE_DIA | mmHg | 20–200 | Durable batch |
| PULSE | bpm | 20–300 | Durable batch |
| RESTING_HEART_RATE | bpm | 25–220 | Durable batch |
| HEART_RATE_VARIABILITY | ms | 1–200 | Durable batch |
| OXYGEN_SATURATION | % | 50–100 | Durable batch |
| BLOOD_GLUCOSE | mg/dL | 20–800 | Ordinary; explicit context |
| BODY_TEMPERATURE | celsius | 28–45 | Durable batch |
| SKIN_TEMPERATURE | celsius | 20–45 | Ordinary |
| RESPIRATORY_RATE | breaths/min | 3–60 | Durable batch |
| VO2_MAX | mL/(kg·min) | 5–100 | Durable batch |
| SLEEP_DURATION | minutes | 0–1440 | Ordinary; no invented interval/stage |
| ACTIVITY_STEPS | steps | 0–200000 | Durable batch |
| ACTIVE_ENERGY_BURNED | kcal | 0–10000 | Durable batch |
| WALKING_RUNNING_DISTANCE | m | 0–200000 | Durable batch |

Accepted conversions: lb/lbs/g → kg; h/s → minutes; km/mi → m;
kJ → kcal; mmol/L → mg/dL (factor 18.0182); °F → celsius. Aliases:
°C/C, min, kg/m2. Otherwise use the exact canonical unit. No dimensional guesses,
rounding, clamping, percent-to-mass conversions or clinical classifications are
made. NaN, infinity, booleans, unknown/unavailable and stale sources are rejected.
Activity amounts must describe distinct event increments, **not repeatedly
uploaded running daily totals**: this integration never overwrites an earlier
sample or computes deltas for you.

## Reliability

`Collector` gathers values; `Queue` durably stores pending captures, frozen
canonical readings, delivery intent and completed event identities; `Transport`
selects exact API schemas and verifies acknowledgements; the worker controls
settling, pacing, backoff and lifecycle. Queue mutations copy before commit and
read back atomic writes before changing memory. Cancellation waits for an
in-progress storage transaction, preventing a reloaded instance racing a late
executor write. Profile options never reinterpret frozen records.

### Durable batch identities

Sixteen exactly mapped metrics use `POST /api/measurements/batch`, with real
HealthKit identifier/unit conventions, a stable non-`stats:` external ID, and
equal start/end timestamps for spot readings. Percent readings are sent as
fractions. The server resolves `EXTERNAL` from the restricted credential and
enforces `(userId, type, source, externalId)` uniqueness permanently while the
row exists. Only the exact per-entry `inserted` or `duplicate` result is
acknowledged. Skips are not success. Failed/malformed answers remain queued.
Requests are deliberately singleton batches, so no successful sibling is lost
on a partial failure.

External IDs derive from random profile identity + trigger event identity +
metric, **not value or upload time**. Two equal readings from different events
remain distinct. After a timeout, restart, or lost acknowledgement, the same
frozen ID is safe to retry even beyond 24 hours. Deleting a server record can
remove that server-side protection; do not delete records awaiting retry.

### Ordinary endpoint and uncertain outcomes

The other nine metrics, and **any metric with notes**, use
`POST /api/measurements` with ordinary type/value/measuredAt/notes/context fields.
Unsupported batch types are never mislabeled as another metric. Duration-only
sleep is not converted into fabricated start/end dates or a guessed sleep stage.

This endpoint has **no durable external ID contract**. An `Idempotency-Key` is
sent, but its 24-hour cache is not an exactly-once transaction guarantee (a
server can commit before caching its answer). This integration therefore does
**not** automatically replay an uncertain ordinary write, even within 24 hours.

Before HTTP it persists delivery intent. A timeout, connectivity failure, 5xx,
unrecognized 2xx, 409 conflict, cancellation in flight, or failed local
acknowledgement leaves the record **held as ambiguous**. This may hold even a
request that never reached the server; deliberately conservative. A 409 is not
proof that the stored value is ours. Other eligible measurements can continue.

To resolve: the affected profile's **Reconfigure → Reconcile an uncertain delivery**, inspect HealthLog
for the metric, original timestamp and actual value against source history,
then explicitly confirm either **already delivered** (acknowledge locally) or
**not stored** (permit another attempt). If you cannot prove the outcome, leave
it held. An incorrect human decision can create a duplicate or acknowledge an
undelivered record; the integration cannot read your records to verify it.
Neither a restart, token replacement, nor the retry button releases these holds.

Completed event identities are retained for the profile's lifetime (the ledger
grows with events); the bounded delivery queue never evicts pending data.
Removing/recreating a profile intentionally loses its local identity and ledger.
Removing a connection removes all its profiles' queues. Do not replay old events
with a new profile.

### Timestamp identity conflicts

HealthLog also enforces a natural identity on metric/source/time/stage. A batch
request with a new external ID at an occupied timestamp can **update the existing
row**, rather than insert another. Distinct events for the same metric/account
must have distinct timestamps at server millisecond precision. The queue retains
timestamp-to-event claims and holds known local collisions *before* HTTP, without
shifting timestamps. Profiles within one connection coordinate their durable
claims before HTTP, preventing known sibling collisions. The local ledger cannot
see other connections/tokens or unrelated writers, so avoid overlapping sources.
An unexpected server
`updated` result is held as `identity_conflict`: inspect HealthLog immediately,
because it may already have changed an existing row. Retry does not release it.
Use reconciliation only after checking/correcting records in HealthLog; a local
timestamp collision cannot be represented as two rows by simply retrying.
Strictly rejecting those collisions needs the upstream enhancement described in
the API audit.

### Errors and retries

- **401**: delivery pauses for every profile in that connection and starts one
  Home Assistant reauthentication flow. Replacing the token resumes its profiles.
- **403**: insufficient scope/API disabled, not a password error; delivery
  pauses until manual retry or connection reload after fixing the server/token.
- **400/422 or batch skipped**: only that measurement is held as rejected.
  Frozen values are not silently edited. Inspect the mapping/server first;
  **Retry delivery** permits another attempt of rejected records.
- **429**: exponential backoff and `Retry-After`, including HTTP-date format.
- **5xx/network errors**: batch records retry; ordinary records are held as
  ambiguous. Persistent backoff starts at 120 s and caps at 3840 s, unless
  `Retry-After` is longer. Manual retry never bypasses it.
- **404/413/redirect**: pause for operator attention; redirects are never
  followed with credentials. Ordinary-route redirects are treated conservatively
  as ambiguous as well.

The obsolete five-imports/hour account gate is gone. Requests are paced per
profile, at least two seconds apart (30/minute). Audited server ceilings are
60 batch requests/minute/user and 300 ordinary writes/minute/account. Multiple
profiles share the server's limits, not a client-global lock; 429 is authoritative.

## Troubleshooting

Use the HealthLog profile's **Delivery status** diagnostic sensor for counts, last success/error,
held records and rejected readings; it never exposes measurement values or
credentials. The retry button wakes delivery and releases confirmed rejections,
not uncertain writes or server backoff. Download integration diagnostics when
reporting an issue, not your `.storage` files.

- **No uploads:** check that a new trigger *identity* occurred after setup.
  Startup/restored values are not events. Check source ownership and timing.
- **Authentication:** replace wrong/revoked/expired tokens via the connection's Reconfigure action.
  403 may also mean API access disabled by the server operator.
- **Invalid/stale mappings:** check attributes, actual units, numeric output,
  source update times, template dependencies and glucose context. Increase
  settling for late updates, not freshness just to hide a stale sensor.
- **Ambiguous/rejected:** follow the reconciliation procedure above. Do not
  edit queue files or recreate the entry as a way to force retries.
- **Storage/full queue:** stop the source if needed, back up, free space/repair
  storage or increase capacity, and reconcile missing source events. Corrupt
  stored queues fail closed and need backup repair; they are not discarded.
- **Missing Apple Health:** verify token mirroring is on, server ≥1.46.0,
  iOS ≥1.5.0 build 302, per-type write direction and HealthKit permissions.
  Check HealthLog first, then open its iOS app. HA cannot promise when iOS runs
  background work or mirror types Apple Health does not support.

## Privacy and security

Connection config entries store the server URL and one restricted token. Profile
subentries store their own settings and random profile identities, not tokens.
Private HA queue storage (`.storage/healthlog.<connection_id>.<profile_id>.queue`) stores pending
snapshots (including detached template inputs), frozen health values/units,
original timestamps, notes, context, delivery state and event identities.
Protect HA storage and backups: this integration does **not** encrypt them.
Selected measurement data is sent over verified HTTPS by default. With an
explicit `http://` URL and **Allow insecure HTTP**, the token and measurements
travel unencrypted and are exposed to anyone capable of observing that connection.
No analytics or third-party telemetry. The write-only API never fetches history.

Requests use Home Assistant's shared `async_get_clientsession(hass)` and its
normal certificate trust store. Cached cookie values are blanked per request
without changing the shared cookie jar: HealthLog prioritizes session cookies
over bearer tokens, so another cached session must not select the wrong account.
Tokens are not put in URLs, followed through redirects, logged in
exceptions, exported in diagnostics or exposed as entity attributes. Response
bodies and template exception details are not logged. Diagnostics use an
allowlist of operational metadata only. Sensor status timestamps still reveal
delivery activity. Deleting a profile while its connection is loaded deletes
only that profile's local queue and identities, including held data. Unattached
stores (for example after interrupted setup, partial backup restoration or
profile deletion while disabled) are retained rather than silently discarded.
Deleting the connection explicitly removes all its owned profile stores,
including unattached ones. **Disable the connection to retain its data instead**.

## Replace the unpublished predecessor

This is a fresh setup, not an upgrade migration. Do not run both integrations
against the same source.

1. Record/export profile settings, especially Jinja templates, attributes and units.
2. Verify the old queue is empty and disable the old integration so it no longer
   collects or delivers.
3. Back up Home Assistant.
4. Remove the old **config entry in Devices & services**, then remove its
   `custom_components/healthlog_import/` files. Deleting Python files alone does
   not remove the config entry. Restart.
5. Install `custom_components/healthlog/` and add **HealthLog**.
6. Create/select the intended account's restricted measurement token and enable
   token mirroring if needed.
7. Recreate mappings and timing; verify no old automation duplicates delivery.
8. Produce one genuinely new event and verify it reaches HealthLog **once**.
9. Verify the HealthLog iOS app eventually mirrors supported data to Apple Health.

No old credential/queue/configuration formats or storage namespaces are read.

## Development and release status

```sh
uv sync --frozen --group dev
uv run pytest
uv run ruff check custom_components tests tools
uv run ruff format --check custom_components tests tools
uv run mypy
```

CI includes Hassfest and the official HACS action. See
[API audit](docs/API_CONTRACT.md), [release checklist](docs/RELEASE.md), and
[release notes](CHANGELOG.md). The icon is original MIT-licensed artwork, not
Home Assistant branding. This is an independent integration, not an official
Home Assistant or HealthLog product.

For real-container Home Assistant → HealthLog testing (Docker Compose required):

```sh
uv run python tools/e2e/run.py
```

This provisions disposable accounts, verified TLS and synthetic sources; it does
not use your deployment or credentials. The fault proxy tests real retries and
crash recovery against the actual API/database. Apple Health/iOS is outside its
scope. See [E2E instructions and limitations](docs/E2E.md).
