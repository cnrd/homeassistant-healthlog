# Validation record

[Documentation index](README.md) · [Development checks](DEVELOPMENT.md) · [E2E plan](E2E.md)

Executed locally on 2026-11-09. Unit environment: macOS, Python 3.14.4,
Home Assistant 2026.10.4 and pytest-homeassistant-custom-component 0.13.375.
All inputs/accounts/measurements are synthetic. No production deployment or
credentials were used. The version remains **1.0.0-beta.1**.

| Check | Result |
| --- | --- |
| `uv sync --frozen --group dev` | Passed |
| `uv run pytest -q --tb=short` | **338 passed**, 93% integration statement coverage |
| `uv run ruff check custom_components tests tools` | Passed |
| `uv run ruff format --check custom_components tests tools` | Passed, 23 files |
| `uv run mypy` | Passed, 12 integration source files |
| Official Hassfest container (linux/amd64) | Passed, 1 integration, 0 invalid |
| Official HACS action | **Passed: all 10 checks**, against the public repository |
| GitHub Actions validation | **Passed**: Python checks/tests, Hassfest and HACS ([initial run](https://github.com/cnrd/homeassistant-healthlog/actions/runs/42627984152)) |
| Real Home Assistant → HealthLog E2E | **All 12 scenario groups passed**, 435.44 s |
| Real-service versions | HA 2026.11.0 / HealthLog 1.47.1 / PostgreSQL 18.6 |
| Apple Health/iOS | **Not performed; outside scope** |
| Initial public push | Completed with maintainer authorization |
| Repository description/topics/issues | Configured for publication |
| Public catalog submission | **Not performed** |

See [E2E_RESULTS.json](E2E_RESULTS.json) for exact image digests, public server
version/build and individual check durations, and [E2E.md](E2E.md) for reproduction.

## Connection/subentry validation

- Native `ConfigSubentryFlow`: one connection/server/token, multiple named
  profiles for different entity sources, with per-profile queues and entities.
- Adding/editing/deleting a profile does not restart sibling collectors or
  interrupt their settling. Real-container checks verified continued delivery
  after a sibling was deleted.
- Parent token revocation pauses all its profiles; one parent reauthentication
  resumes them with original frozen values/timestamps and correct account.
- Durable identity reservations are coordinated across sibling profiles, not
  across unrelated accounts. Concurrent collision tests allow only one HTTP call.
- Frozen queues survive edits, restart, cancellation and acknowledgement failures.
  Explicit deletion is scoped to the owning profile/connection. Unattached stores
  are not automatically discarded on startup, since HA saves profile metadata
  separately from queue captures.
- All 25 metrics, exact API schemas, state/attribute/Jinja mappings, late template
  dependencies, units/ranges, notes, glucose context and privacy allowlists.
- HTTPS defaults, URL/port/prefix normalization, explicit HTTP opt-in, invalid
  certificates even with HTTP allowed, shared-session cookie/token isolation,
  cross-origin/downgrade redirect refusal, and no fabricated setup writes.
- Actual lost responses before/after commit, real persisted backoff, server cache
  removal before batch retry, quarantine/manual retry and per-profile explicit
  reconciliation. Unit tests separately simulate retries after 48 hours.

The full real-service run used the new subentry structure and a fresh database.
Earlier test-layout smoke artifacts are not retained. All disposable containers,
networks, database volumes and generated private credentials/configuration were
cleaned up after testing.

This is a full beta1 replacement. No compatibility or migration code reads any
earlier configuration or storage layout. Earlier test entries must be removed
and recreated. Passing tests do not remove the upstream API identity limitations
documented in [API_CONTRACT.md](API_CONTRACT.md), nor constitute HACS catalog approval.

## Publication checks

The domain was absent from installed HA 2026.10.4 and the upstream core dev
manifest URL at audit time. Shipped JSON parses, branding is original RGBA PNG
(256/512px), and runtime predecessor-domain/auth/import searches have no matches.

The maintainer authorized the initial push and beta prerelease. After the initial
commit was pushed, the official authenticated HACS action passed all 10 checks:
archive status, brands, description, information, categories, integration
manifest, issues, license, topics and hacs.json. GitHub Actions also passed its
Python, Hassfest and HACS jobs on the public revision. Earlier empty-repository
validation failures are superseded by these executed results.

This is still a beta release, not approval for the public Marketplace/HACS
catalog. Apple Health/iOS remains outside the executed E2E scope.
