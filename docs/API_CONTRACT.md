# Audited HealthLog API contract

Audit date: 2026-11-09. Upstream `MBombeck/HealthLog` main at
`f86a7a61e1e6226732474bfbdc5346748b2027e1`, package version 1.47.1.
This document records implementation evidence, not an assumed OpenAPI schema.
No upstream source code was incorporated; wire identifiers and validation facts
were independently implemented here. The upstream server has its own license.

[Documentation index](README.md) · [User-facing reliability](RELIABILITY.md) · [Compatibility](CONFIGURATION.md#compatibility)

## Sources inspected

All server paths below are relative to the
[pinned source tree](https://github.com/MBombeck/HealthLog/tree/f86a7a61e1e6226732474bfbdc5346748b2027e1):

- `docs/api/README.md`, `docs/api/openapi.yaml`
- `docs/integrations/home-assistant.md`
- `src/app/api/measurements/route.ts`
- `src/lib/validations/measurement.ts`
- `src/lib/measurements/create/create-readings.ts`
- `src/app/api/measurements/batch/route.ts`
- `src/lib/measurements/batch-ingest/{entries,prepare,ingest,write-measurements}.ts`
- `src/lib/measurements/reconcile-external-measurement.ts` and natural-identity indexes
- `src/lib/measurements/{apple-health-mapping,unit-map,value-ranges}.ts`
- `src/lib/measurements/apple-health-mirror.ts`
- `src/lib/idempotency.ts`, `src/lib/auth/write-meter.ts`, `src/lib/rate-limit.ts`
- `src/app/api/tokens/measurements/route.ts`
- `prisma/schema.prisma`, including measurement uniqueness indexes
- Server [#1272 and maintainer comments](https://github.com/MBombeck/HealthLog/issues/1272)
- iOS [#28 and maintainer comments](https://github.com/MBombeck/healthlog-iOS/issues/28)

Server mirroring shipped in 1.46.0. Maintainer confirmation on November 8:
iOS 1.5.0 build 302 is on TestFlight and honors `appleHealthMirror` in foreground
and bounded background sync. This is not a promise of immediate iOS execution.

## Authentication and validation

Both POST routes admit `measurements:write`, confined to the token owner's own
record. Neither permits client source attribution under that credential. Both
resolve `EXTERNAL`, link the ingest token, and expose its mirroring decision on
measurement reads. No account-ID lookup, login or session cookie is necessary.

No safe non-mutating token introspection endpoint was found. Other authenticated
GETs are not available to this scope. Setup deliberately performs **zero HTTP
requests**. Setup instructions explain the required restricted scope and owner;
the connection form accepts the server URL, API token and an optional insecure
HTTP opt-in, disabled by default.
Syntax validation cannot distinguish a restricted token from a full-access
`hlk_` token: users must not supply a full-access credential. Owner/scope
validation and binding on replacement remain an upstream API gap.

## Connection transport policy

The audited server selects a valid cookie session before considering a bearer
token. The client therefore suppresses cached cookie values per request while
using HA's shared session, preserving the connection's token identity.
Authenticated redirects are never followed.

The maintained client policy for verified HTTPS, explicit HTTP opt-in,
certificates, cookies and credential replacement is in
[Security](SECURITY.md#transport-security). User-facing URL, connection and
profile instructions are in [Configuration](CONFIGURATION.md#create-and-manage-a-connection).

## Ordinary create route

`POST /api/measurements`: object with `type`, finite numeric `value`, original
`measuredAt` (ISO, timezone), optional `notes`, optional `glucoseContext`.
No `unit` or `externalId` is declared in the create schema. The server selects
canonical units. Array mode supports 1–5 records in one transaction, **not** the
HealthKit batch envelope. This integration uses object mode only.

Glucose requires one of FASTING, POSTPRANDIAL, RANDOM, BEDTIME and other metrics
must not set context. The editor requires the real context, with no default.
Although the device batch route permits contextless glucose, we choose ordinary
delivery to preserve an explicit context rather than silently erase it.

Success is HTTP 201 `{data: <measurement>, error: null}`; cached replay can be
HTTP 200. We verify a nonempty ID, type, canonical unit, finite numeric value,
original instant at server millisecond precision, `source: EXTERNAL`, and any
supplied notes or glucose context. A mismatched 2xx is uncertain, not success.
409 `measurement.duplicate_timestamp` does not identify our immutable event/value
and is never an acknowledgement.

The ordinary schema provides no durable client identity. The deployed natural
identity index uses NULLS NOT DISTINCT (the Prisma declaration alone omits that
detail), so it can reject timestamp collisions even with null sleep stage.
That still cannot prove the correct event/value/context was stored: a 409
provides no identity or immutable-payload comparison and must not be acknowledged.

## HealthKit-oriented batch route

`POST /api/measurements/batch`: `{entries: [{hkIdentifier, value, unit,
startDate, endDate, externalId}]}`; maximum 500. The API can return HTTP 200 with
partial success. Response `data` contains `processed`, `inserted`, `updated`,
`duplicates`, `failed`, `skipped`, and `entries` with **index + status**, not
per-measurement IDs. Statuses: inserted, duplicate, updated, skipped, failed.
One entry per call deliberately makes partial failure isolation trivial.

Only matching index 0 / status inserted or duplicate is acknowledged. Skipped
is quarantined; failed/malformed/updated is not success. Updated would indicate
aggregate/overwrite semantics, which we never request. Response reason strings,
converted values, and range details are never logged or exported.

Permanent uniqueness is `(userId, type, source, externalId)`, not token ID.
The client uses a stable profile/event/type identity with the prefix `ha:`.
Never use `stats:`: it changes ingest into mutable aggregate upserts. Sending no
source lets restricted-token attribution keep it outside the phone's namespace.

**Additional server limitation:** the reconciler can update a natural-identity
collision (same owner/type/source/millisecond/stage) even for a non-`stats:`
sample with a *different* external ID. Therefore distinct events must have
distinct measurement timestamps for each metric/account. Profile queues retain
timestamp-to-external-ID claims; their connection coordinates durable reservations
under a short lock before HTTP, preventing known sibling collisions without
serializing network requests.
An unexpected server `updated` result is held as `identity_conflict`, never
acknowledged or automatically retried; the server may already have changed a
row, so inspect it immediately. The local ledger cannot detect a collision with
an unrelated writer or a different connection/token. Do not configure
overlapping profiles. No timestamps are artificially shifted to bypass this.

### Mapping coverage

`transport.BATCH_TYPES` contains **16** exact mappings. The
[metric reference](METRICS.md#supported-measurements) lists all 25 canonical
types, units, ranges and chosen routes. Canonical ranges were
cross-checked against `value-ranges.ts`; units against `unit-map.ts`.

Wire differences from canonical units:

| Canonical | HealthKit wire convention |
| --- | --- |
| BODY_FAT percent | BodyFatPercentage, raw fraction (divide by 100) |
| OXYGEN_SATURATION percent | OxygenSaturation, fraction (divide by 100) |
| BMI kg/m² | BodyMassIndex, count |
| PULSE/resting HR bpm | HeartRate/RestingHeartRate, count/min |
| RESPIRATORY_RATE breaths/min | RespiratoryRate, count/min |
| BODY_TEMPERATURE celsius | BodyTemperature, degC |
| VO2_MAX mL/(kg·min) | VO2Max, mL/(kg*min) |
| ACTIVITY_STEPS steps | StepCount, count |

Remaining mapped mass, pressure, HRV, energy and distance units are identity
conversions. All spot entries send their true measurement instant as both dates;
the mapper stores `endDate`. No clinical interval is invented.

The batch map does not provide generic mappings for TOTAL_BODY_WATER, BONE_MASS,
FAT_FREE_MASS, FAT_MASS, MUSCLE_MASS, VISCERAL_FAT or SKIN_TEMPERATURE. These must
not be relabeled as lean mass, body temperature or body-fat percentage. Sleep's
category schema needs interval/stage information not provided by a duration-only
profile. It therefore uses ordinary create, not invented dates/stages.

The batch schema has **no notes field**. Any configured notes force ordinary
delivery so user data is not silently stripped. There is no undocumented
`externalId` field sent to ordinary create as a pretend solution.

## Why not retry ordinary writes with the same key for 24 hours?

`withIdempotency` provides a 24-hour response cache, a two-minute pending claim,
and request fingerprints. For restricted credentials the key cell includes the
token authority. A token replacement need not replay the same cell. A handler
can also commit a row and fail before writing its cached response. Therefore
even an in-window retry is **not** a durable commit-and-identity transaction.

The integration sends a stable key but never relies on it as sole duplicate
protection. Durable delivery intent is recorded **before** ordinary HTTP. Any
uncertain outcome is held indefinitely, including a failed local acknowledgement.
Only an explicit pre-write refusal (400/401/403/404/413/422/429) clears that
request's intent; 401/403 pause as appropriate. Cancellation leaves intent intact.
Manual retry cannot release uncertain records. Reconciliation requires an
explicit human check in HealthLog; no automatic deletion/recreation is performed.
The actionable procedure is in [manual reconciliation](RELIABILITY.md#manual-reconciliation).

## Pacing

The batch route owns a 60 requests/minute/user bucket. Ordinary writes use the
shared 300/minute/account meter. This worker spaces requests at least two seconds
per profile, preserves backoff and Retry-After on disk, and never holds a global
account lock. Independent profiles can still meet the server's aggregate ceiling;
429 remains authoritative. No legacy five-imports/hour ledger remains.

## Proposed narrowly scoped upstream improvements (not implemented/published)

1. Add an optional bounded `externalId` to ordinary create (object and array),
   persisted transactionally with the row under EXTERNAL-source uniqueness.
2. For same owner/type/source/externalId, return an explicit per-row **duplicate**
   only if immutable clinical fields agree; return a machine-readable identity
   conflict if values/timestamps/context disagree. Never overwrite an immutable
   reading or mistake a timestamp collision for a matching event.
3. Keep identity independent of token rotation, enforce restricted scope, and do
   not accept arbitrary source claims. Include OpenAPI and transaction/race tests.
4. Add a non-mutating restricted-token validation endpoint returning scope,
   stable owner binding and effective mirroring flag, without health history.
5. On non-`stats:` restricted batch writes, reject a different external ID at an
   occupied natural timestamp rather than updating/replacing the existing row.
   An immutable client event must not overwrite an unrelated measurement.

That would allow automatic durable retries for every supported metric, preserve
notes and context, and verify same-account token replacement. It is not assumed
to exist in this release. No issue or PR was submitted automatically.
