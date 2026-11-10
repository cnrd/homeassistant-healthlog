# Configuration guide

[Documentation index](README.md) · [Metric reference](METRICS.md) · [Reliability](RELIABILITY.md)

A **connection** holds one HealthLog server URL and one account's restricted
measurement token. A **measurement profile** is a named subentry beneath it,
with independent source entities, mappings, trigger and delivery queue.

## Compatibility

This is the detailed compatibility reference; the front-page requirements are a
summary. Setup does not query server versions or validate the token remotely.

| Component | Requirement / evidence |
| --- | --- |
| Home Assistant | Minimum **2026.10.4**, as declared in [hacs.json](../hacs.json). HA manages its Python runtime; the development environment requires Python 3.14.2+. |
| HealthLog server | Documented baseline **1.46.0+**, with restricted measurement ingestion enabled. The API was audited and real-service tests were run against **1.47.1**. |
| Authentication | A restricted measurement token with `measurements:write`, belonging to the intended account. Do not supply a full-access login token. |
| Network | LAN or remote hosting. A LAN server does not require internet access. HTTPS is the default; HTTP requires explicit opt-in. |
| Optional Apple Health | Server-directed mirroring requires HealthLog server 1.46.0+ and a compatible iOS 1.5.0+ build, HealthKit write authorization and suitable per-type directions. The first upstream-confirmed build was 302. |

The [validation record](VALIDATION.md) identifies versions actually tested;
later versions are not automatically covered by those results. The
[API audit](API_CONTRACT.md#sources-inspected) preserves upstream evidence,
including the original iOS compatibility confirmation. iOS/Apple Health was
outside the integration's E2E test scope. Do not assume version 1.4.1 mirrors
restricted-token `EXTERNAL` readings.

## Create and manage a connection

1. In HealthLog **Settings → API & Tokens → Measurement ingest token**, create
   a named restricted measurement token. Some versions label this area
   **API → Measurement tokens**.
2. Copy the complete `hlk_…` value when shown. Enter it in Home Assistant without
   adding `Bearer`; the integration supplies the authorization header.
3. If desired, enable **Write readings to Apple Health** on that token. It is
   off by default. This changes mirroring eligibility, not the token's account
   or the server-controlled `EXTERNAL` source attribution.
4. Add **HealthLog** in **Settings → Devices & services** and enter its base URL
   and restricted measurement token.

The token writes only to its owner's record, not another account's shared or
delegated record. Scope and owner cannot be introspected with this write-only
credential. Setup checks syntax and makes no HTTP requests or test measurements.
An empty queue showing `ready` is not proof of successful authentication: verify
the first genuinely new event in HealthLog.

### Server URL and transport

| Input | Result |
| --- | --- |
| `healthlog.local` | `https://healthlog.local` |
| `healthlog.local:8443/healthlog/` | `https://healthlog.local:8443/healthlog` |
| `https://[2001:db8::1]:8443/healthlog` | An explicit IPv6 URL with port and prefix. |
| `http://192.168.1.20:3000/healthlog` | Accepted only with **Allow insecure HTTP**. |

Ports and installation prefixes are supported. A scheme-less address always
uses HTTPS—even when **Allow insecure HTTP** is checked. Enter `http://`
explicitly to use HTTP. HTTP exposes the token and measurements to anyone able
to observe the connection. HTTPS certificates remain verified, and failures
never trigger automatic HTTP fallback. Unsupported schemes, embedded
credentials, malformed URLs, query strings and fragments are rejected.

For transport and certificate details, see [Security](SECURITY.md#transport-security).

### Replace a token or connection details

Use the **connection's Reconfigure** action or its reauthentication prompt.
Changes apply to every measurement profile beneath that connection. A 401 pauses
their delivery until authentication is restored; collection can still fill queues.

Verify that a replacement token belongs to the **same account**. Keep the same
hostname and installation path prefix; protocol and port may change for the same
server. Use a separate connection for another account/server. The integration
cannot verify replacement ownership for you. Queued records are not retargeted
per profile, and uncertain-delivery holds are not released by changing a token.

## Add and edit measurement profiles

Saving a connection continues into its first measurement profile. Later, use
**Add measurement profile** on that connection's integration page. Do not add a
new integration or mint another token just to use another source entity.

Give each profile a name, such as **Weight scale** or **Blood pressure**. Profile
names are 1–100 characters. Select the trigger, mappings and timing settings;
the profile inherits the connection's credentials and cannot override them.
Each profile gets a **Delivery status** sensor and **Retry delivery** button.

Use the individual measurement profile's **Reconfigure → Edit trigger, mappings
and timing** action to edit it. A profile edit restarts that collector, not its
siblings. Frozen records retain their original values, timestamp, units, notes
and context. Pending captures retain their original captured settings/snapshots;
they are not reinterpreted using new mappings.

Deleting a profile is not a retry or recovery action. Read
[queue deletion and retention](RELIABILITY.md#queue-deletion-and-retention) first.

## Multiple people and shared devices

Multiple measurement profiles can share one connection/token **only when they
should write to the same HealthLog account**. Different people/accounts need
separate connections with their own restricted measurement tokens.

There is no automatic Home Assistant `person` recognition. A shared scale or
other shared device must already expose an unambiguous per-person event and
associated readings. Do not send the same undifferentiated source to both
people's profiles. Avoid overlapping profiles for one physical measurement;
different profile identities deliberately produce different external IDs.

## Trigger selection and identity

The trigger entity announces a **new measurement event**, rather than merely
holding a numeric value. Use its state, or enter an optional trigger attribute
containing the identity. Unavailable, empty and oversized identities are rejected.

### Timestamp mode

Prefer the source's original timezone-aware ISO timestamp, such as a `measured_at`
attribute. Equivalent offsets are normalized to UTC for event identity. The
measurement instant is preserved, not replaced by upload/retry time. Timestamps
before 1900 or more than five minutes into the future are rejected.

The local record/request can retain sub-millisecond precision, but HealthLog
stores milliseconds. Distinct events for the same metric/account must not collide
at that precision; see [timestamp conflicts](RELIABILITY.md#timestamp-collisions-and-identity-conflicts).

### Stable event-ID mode

Use an opaque, stable and unique ID for each measurement when no original
timestamp is available. The integration records the triggering HA state's
`last_updated` time once as measurement time. This is HA receipt/update time,
not an original clinical timestamp recovered from the ID.

Do not use the numeric reading, `on`/`off`, or a reused counter as identity.
Two equal readings can be distinct events, but both need genuine new identities
and sufficiently fresh source values.

Startup/restored states, the first valid baseline of a late-loading trigger,
and notifications whose identity has not changed do not create measurements.
Setup does not scan historical states. See [event identity](RELIABILITY.md#event-identity)
for the persistent identity history.

## Map states, attributes and units

Add one mapping per metric; duplicate types within a profile are refused.

1. Choose the actual HealthLog measurement type. Its displayed unit is the
   canonical output unit, not necessarily your source's unit.
2. Select an entity's numeric state, or an exact numeric attribute key. You may
   reuse the same entity for several attribute mappings.
3. Enter the **source unit**, or leave it blank for the entity's
   `unit_of_measurement`. Attribute mappings often need an explicit unit.
4. Optionally add a Jinja value template. Its explicit unit describes the
   rendered result, not a desired output unit.

Check attribute names and actual units in **Developer Tools → States**. The
integration does not auto-detect a scale model or infer physical dimensions.
The [metric reference](METRICS.md) is authoritative for supported input spellings,
conversions and ranges. Never relabel water percentage as kilograms.

## Jinja templates

A value template must return one finite number. Syntax is checked in the editor;
invalid output is rejected rather than substituted with zero. Templates require
an explicit result unit. Avoid `float(0)` fallbacks: a missing reading can become
a fabricated health value. Templates are limited to 8192 characters.

With a selected source entity, these variables are available:

| Variable | Meaning |
| --- | --- |
| `value` | The selected state or numeric attribute. |
| `source` | Detached snapshot containing `entity_id`, `state`, `attributes`, `last_changed` and `last_updated`. |
| `source_entity` | The selected entity ID. |

Templates may also use HA's `states()` and `state_attr()` functions. The optional
source entity is not required for template-only mappings. The integration-specific
variables above are not automatically injected into Developer Tools' standalone
template preview.

### Dependencies and freshness

Relevant entity dependencies are tracked during settling, including dynamically
discovered references and references retained after a render error. A late value
can repair an invalid pending result before the snapshot is frozen.

- **With a selected source:** that entity anchors freshness. Additional referenced
  entities can trigger re-rendering but do not become extra freshness anchors.
- **Template-only:** freshness is the oldest update time among discovered entity
  dependencies. Domain/all-state reads can broaden this to the relevant domain
  or entire state collection, so unrelated stale states may make the check
  conservative. Missing, unavailable or restored dependencies are not fresh.
- **Constant-only template:** there is no freshness evidence, so it is rejected
  at collection/freeze rather than stamped with the current time.

Freshness is based on HA update times relative to event receipt, not the age of
the original clinical timestamp. A new trigger alone does not refresh another
entity. If repeated equal readings leave state/attributes unchanged, check
whether the source updates HA's `last_updated` as needed.

## Blood-glucose context

Choose `BLOOD_GLUCOSE` in the mapping editor. After submitting the profile, only
profiles containing that metric see the **Blood glucose context** follow-up step.
Other profiles skip it. The standard HA Add/Edit object dialog cannot hide a
sibling field live based on its metric dropdown.

New profiles have no guessed context; existing selections are prefilled for
review. Choose the actual context from the [supported glucose contexts](METRICS.md#blood-glucose).
If it varies, use separate context-specific profiles/triggers. If it is unknown,
do not configure this mapping. Changes affect new captures, not queued context.

## Timing and delivery-queue capacity

| Setting | Default | Allowed range | Purpose |
| --- | --- | --- | --- |
| Settling delay | 10 seconds | 0–300 | Collect related late updates before freezing. |
| Freshness window | 120 seconds | 0–600 | Reject source values updated too far before event receipt. |
| Delivery/coalescing delay | 5 seconds | 1–300 | Coalesce changes and pace individual requests. |
| Queue capacity | 500 events | 1–1000 | Bound pending, frozen and held events without evicting old ones. |

One event can contain several metric records. Delivery uses one metric per
request, even on the batch endpoint, at least two seconds apart per profile.
At the five-second default, five measurements use five requests with roughly
20 seconds of pacing between the first and last, plus settling/coalescing and
network time. This is not a fixed-time or global rate-limit guarantee: multiple
profiles independently generate requests.

Settling waits for related updates before freezing; freshness checks source age
and does **not** add a delivery wait. Delivery delay coalesces changes and paces
normal requests. Error backoff is separate and applies after delivery problems;
persisted backoff and `Retry-After` take priority over the five-second setting.

The new default applies to new profiles or settings not explicitly saved.
Upgrading from beta2 does not rewrite existing profiles: a saved **60-second**
delay remains 60 seconds. To opt in, use that profile's **Reconfigure → Edit
trigger, mappings and timing** action and set **Delivery/coalescing delay** to 5.
No connection, token, profile, queue or entity needs to be recreated.

Reducing capacity does not evict records. Full queues and failed storage writes
can prevent new events being admitted; the source history may need manual
reconciliation. See [storage and capacity limits](RELIABILITY.md#storage-failures-and-capacity-exhaustion).

### Optional device notes

Notes are limited to 500 characters and are frozen with the event. **Nonempty
notes force ordinary-endpoint delivery for every metric**, because the batch
schema cannot preserve them. This changes duplicate safeguards; read
[ordinary-endpoint limitations](RELIABILITY.md#ordinary-endpoint-limitations).

## Examples

These illustrate profile UI fields, not YAML integration configuration. Entity
IDs are fictional; use your source's actual semantics, units and update timing.

### Simple weight sensor

Use `sensor.example_scale_measurement_time` as a timestamp trigger. Map `WEIGHT`
from `sensor.example_scale_weight`, using its actual `kg` or `lb` unit. Leave
attribute, template and notes blank. The trigger must change for each weighing,
including equal readings, and the weight source must be fresh.

### Several attributes on a scale

Use the same source entity for separate numeric-attribute mappings, as in the
[front-page scale example](../README.md#example). Verify each attribute's own
unit rather than assuming it matches the aggregate entity's unit.

### Convert an explicitly measured mass in a template

For a source reporting a numeric `mass_g` attribute, map `WEIGHT` from
`sensor.example_scale`, use result unit `kg`, and set:

```jinja
{{ source.attributes.mass_g / 1000 }}
```

For a template-only mapping with result unit `kg`:

```jinja
{{ states('sensor.example_measured_mass_g') | float / 1000 }}
```

These convert an existing measurement; they do not infer a new clinical metric.

### Different devices, one account

Create one connection with that account's restricted measurement token. Add
**Weight scale** using `sensor.example_scale_measurement_time`, then **Blood
pressure** using `sensor.example_cuff_measurement_time`. The cuff profile may map
its own systolic, diastolic and pulse entities; it needs no additional token.

### Two people

Create separate connections with person A's and B's own restricted measurement
tokens. Under each connection, add profiles with only that person's associated
sources, for example `sensor.example_scale_a_time` and
`sensor.example_scale_b_time`. Do not duplicate one undifferentiated shared-device
event across both accounts.
