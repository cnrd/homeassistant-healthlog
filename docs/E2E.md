# Home Assistant → HealthLog end-to-end testing

[Documentation index](README.md) · [Development setup](DEVELOPMENT.md) · [Validation record](VALIDATION.md)

This suite runs **real services**, not the mocked HTTP fixtures in `tests/`:

```text
Synthetic HA state events
  → real Home Assistant connection + profile subentries, collectors and queues
  → verified HTTPS (or explicitly opted-in HTTP) / controllable fault proxy
  → real HealthLog API
  → real PostgreSQL database
```

**Apple Health and iOS are excluded.** No production server, token, sensor,
Home Assistant instance, or GitHub publication is involved.
The separate maintainer-reported [production observation](VALIDATION.md#production-observation)
does not expand this automated suite's scope.

## Latest execution

**Stable 1.0.0, 2026-11-10: all 13 scenario groups passed in 468.70 seconds.**
The new default-delay scenario delivered five measurements as five individual
requests with five-second delivery pacing and 10-second settling, then drained
the durable queue. Full results and image digests are in
[V1_0_0_E2E_RESULTS.json](V1_0_0_E2E_RESULTS.json). HA 2026.11.0, HealthLog 1.47.1
and PostgreSQL 18.6 were used. Disposable containers, networks, database volume
and private generated credentials/workspace were cleaned up after the run.
Apple Health/iOS is excluded; see the separate production observation above.

### Beta2 execution

**Beta2, 2026-11-09: all 12 scenario groups passed in 436.51 seconds.** The run
used Home Assistant 2026.11.0, HealthLog 1.47.1 and PostgreSQL 18.6. Image digests
and per-check durations are in [BETA2_E2E_RESULTS.json](BETA2_E2E_RESULTS.json).
The original beta1 report below is retained as historical evidence. Both runs
exclude Apple Health/iOS and use only synthetic, disposable data.

### Original beta1 execution

**2026-11-09: all 12 scenario groups passed in 435.44 seconds.** The run used
Home Assistant 2026.11.0, HealthLog 1.47.1 (build `5e1c39438fd4`) and PostgreSQL
18.6. Image digests and individual check durations are in
[E2E_RESULTS.json](E2E_RESULTS.json). Disposable containers, networks, database
volume and private temporary credentials were cleaned up afterward. This run
includes native profile subentries, the HTTP/HTTPS policy and HA's shared session.

The runner now uses native `ConfigSubentryFlow`: one connection per account and
endpoint, with multiple profiles sharing its token. It tests profile add/edit/
delete while a sibling is settling, and token replacement across sibling
profiles. Older pre-subentry smoke reports are intentionally not retained.

## Run

Requirements: Docker with Compose v2, Python 3.14.2+, and the pinned development
environment. Allow several minutes for image pulls/startup, two complete metric
events and two real two-minute retry/backoff checks.

```sh
uv sync --frozen --group dev
uv run python tools/e2e/run.py
```

For shorter setup/delivery checks, including conditional glucose context and
shared-connection subentry lifecycle, without lengthy failure/backoff scenarios:

```sh
uv run python tools/e2e/run.py --configuration-smoke
```

This writes `docs/CONFIG_FLOW_E2E_RESULTS.json` without replacing the full-suite
report.

The runner creates a uniquely named Compose project and a private temporary
directory. It generates an independent test CA, TLS certificate, database keys,
HA user and two HealthLog accounts with restricted measurement-write tokens.
It uses the actual HA onboarding, connection, subentry and reauthentication APIs.
Sensor states are injected through HA's authenticated REST API, so collection is
driven by real state-change events rather than direct worker calls.

Pinned service tags:

- Home Assistant `2026.11.0` (separate from the 2026.10.4 unit-test environment).
- HealthLog `v1.47.1`.
- PostgreSQL `18.6-alpine`.

Actual pulled digests are recorded in the result. Home Assistant trusts only the
generated test CA for these requests; TLS verification is **not disabled**.
The test CA is not installed into the host operating system.

HealthLog and PostgreSQL are on an internal Docker network with no published
ports. HA and the HTTPS/HTTP proxies also join a separate control network so their management
ports can be published on **127.0.0.1 only**. Outbound HealthLog IP lookups,
password-breach queries, updates and optional environment services are disabled;
its internal-only network also prevents external egress.

## Assertions

- The new-profile delivery default is five seconds. A five-metric event at that
  setting, with 10-second settling, makes five individual successful requests
  and drains its durable queue while retaining the event identity. Other
  scenarios explicitly use shorter delays to keep their execution bounded.
- All 25 metrics reach the real database with correct values, canonical units,
  original timestamps, restricted-token linkage and server-assigned `EXTERNAL`.
- The real glucose validator accepts the explicitly configured context.
- Setup sends no measurement writes.
- Adding a profile does not request another token or create another account
  connection. Different entities in one account use independent subentries.
- Adding/editing a sibling does not interrupt an existing settling capture.
  Deleting one subentry deletes only its queue; its sibling continues delivering.
- Scheme-less URLs use verified HTTPS; optional ports and installation prefixes
  are preserved. Plain HTTP is refused until explicitly opted in, then delivers
  to the real server through a separate LAN-only HTTP endpoint.
- A certificate hostname mismatch is refused even when HTTP opt-in is enabled;
  no HTTPS-to-HTTP fallback or unverified HTTPS request creates a record.
- Equal readings from distinct events are stored separately; duplicate trigger
  identity notifications do not create more records.
- Direct state/unit conversion, attribute mappings and a Jinja mapping with a
  late dependency update work through the actual HA event loop.
- Two users' credentials deliver only to their corresponding records.
- Notes force ordinary-route delivery and are retained/encrypted by HealthLog.
- Pending captures survive a real HA process restart, without rereading missing
  live sources after startup.
- Frozen payloads and persisted delivery intent survive cancellation of a real
  in-flight HTTPS request and process restart.
- Lost responses before/after commit hold ordinary writes, including across
  restart and manual retry. Real profile subentry flows reconcile a proven non-commit and
  a proven commit without blind replay.
- A batch response lost after commit is retried without a duplicate. The suite
  deletes **only the disposable server's idempotency response-cache rows** before
  retry, so a replay cache cannot hide a missing durable identity mechanism.
  This tests cache absence, not an actual 24-hour wall-clock passage.
- 429/Retry-After and 422 quarantine survive a real backoff interval; the retry
  button does not bypass pacing.
- A genuinely revoked test token produces 401 and pauses its connection's
  profiles. One parent reauthentication replaces it for all siblings, preserving
  frozen values, timestamps and account ownership.
- Injected 403 and cross-origin HTTPS-to-HTTP redirects are classified safely;
  the target receives no redirected authenticated request.
- Explicit subentry removal deletes only that profile's local queued test data.

Assertions inspect the disposable database using separate **test-harness**
credentials. The integration receives only a restricted write token and never
reads the account's health records. Fault refusals/drops are injected by the
proxy; ordinary successful responses, writes, validation and duplicate handling
come from the actual HealthLog server.

## Results, diagnostics and cleanup

The nonsensitive summary is `docs/E2E_RESULTS.json`: check names, results,
durations, image versions/digests and public server version information. It
contains no credentials or raw biometric values. Do not equate mocked unit
coverage with a passing E2E run; inspect the summary's `passed` field.

By default the runner removes **only its own** containers, network and database
volume on exit. Private temporary files remain outside the repository for
diagnosis, protected by directory permissions. Do not upload that directory:
it contains generated credentials, TLS keys and synthetic instance storage.

For debugging, `--keep` retains this run's containers/volume, and `--resume DIR`
reuses its private provisioning state. Resume is intended for startup debugging,
not rerunning completed scenarios: the full suite requires an empty measurement
database. For a clean repeat, use a new temporary directory. Never point it at a
production configuration or database.

The manual `HealthLog E2E` GitHub workflow can run the same suite after publication
is authorized. It needs no production secrets and uploads only the safe JSON
summary, not private workspace files.

The harness waits for durable event admission/acknowledgement rather than trusting
an asynchronously updated diagnostic sensor. It also rediscovers HA's ephemeral
management port after each Docker restart. These synchronization checks and the
strict test-CA certificate extensions have dedicated unit regression tests.

## Limits

This is an API/event/lifecycle E2E suite, not a visual browser test of selector
rendering. It does not test physical hardware, iOS, Apple Health, every possible
storage/filesystem failure, or concurrent unrelated writers on another server.
The upstream ordinary-identity and batch-timestamp collision limitations remain
as documented in [the API audit](API_CONTRACT.md), even when every E2E assertion passes.
