# Validation record

[Documentation index](README.md) · [Development checks](DEVELOPMENT.md) · [E2E plan](E2E.md)

Executed locally on 2026-11-09. Unit environment: macOS, Python 3.14.4,
Home Assistant 2026.10.4 and pytest-homeassistant-custom-component 0.13.375.
All inputs/accounts/measurements are synthetic. No production deployment or
credentials were used. The version for that original run was **1.0.0-beta.1**.

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
manifest URL at audit time. Shipped JSON parsed, and the then-shipped generic
RGBA PNG branding (256/512px) passed validation. It has since been replaced by
the official HealthLog logo under [maintainer permission](DEVELOPMENT.md#license-and-attribution);
the executed checks above describe the original validation run, not a new test
of later branding changes. Runtime predecessor-domain/auth/import searches had
no matches.

The maintainer authorized the initial push and beta prerelease. After the initial
commit was pushed, the official authenticated HACS action passed all 10 checks:
archive status, brands, description, information, categories, integration
manifest, issues, license, topics and hacs.json. GitHub Actions also passed its
Python, Hassfest and HACS jobs on the public revision. Earlier empty-repository
validation failures are superseded by these executed results.

This is still a beta release, not approval for the public Marketplace/HACS
catalog. Apple Health/iOS remains outside the executed E2E scope.

## Official-logo validation

The branding replacement was checked locally on 2026-11-09, separately from the
original software validation above:

- Both official source assets (`public/logo-512.png` and
  `public/logo-readme.svg`) downloaded successfully from the upstream revision
  recorded in [the logo attribution](DEVELOPMENT.md#official-healthlog-logo).
- Both shipped icons decode as RGBA PNGs with transparency: `icon.png` is
  256 × 256 and `icon@2x.png` is 512 × 512. The 512px file is byte-identical to
  upstream; the 256px pixels match a Lanczos resize of that source. Both were
  opened and visually reviewed. The original source canvas was retained.
- The official Hassfest container passed against the local working tree:
  **1 integration, 0 invalid integrations**.
- Ruff lint and formatting checks passed for the updated branding preparation
  helper. No integration runtime Python, version, domain, manifest, HACS metadata
  or software license was changed.
- The official HACS action passed **all 10 checks** against published revision
  `2923bc6afe1158655ca265d4fb96a04e2ed61a2f`. It retrieves public repository files,
  so that result does **not** validate the unpushed replacement images. Their
  format, dimensions and source fidelity were checked locally as described above;
  rerun the action against the new revision after a separately authorized push.

The README's independent-community disclaimer and official HealthLog link remain
unchanged. No release, tag, push or brands-repository submission was performed
as part of the branding replacement.

## Beta2 release validation

Release target: **v1.0.0-beta.2**. The maintainer separately authorized publishing
beta2 with the official logo. Beta1 profiles, connections and delivery queues
upgrade in place; only version metadata, branding, tooling and documentation
changed since beta1, not integration behavior or storage formats.

Locally executed against the beta2 preparation on 2026-11-09:

| Check | Result |
| --- | --- |
| `uv sync --frozen --group dev` | Passed; dependency pins unchanged |
| `uv run --frozen pytest -q --tb=short` | **338 passed**, 93% integration statement coverage |
| Ruff lint / format checks | Passed, 23 files formatted consistently |
| `uv run --frozen mypy` | Passed, 12 integration source files |
| Official Hassfest container | Passed, 1 integration, 0 invalid |
| Full real-service E2E | **All 12 scenario groups passed**, 436.51 seconds |
| Release versions | Manifest and constant `1.0.0-beta.2`; project/lock metadata `1.0.0b2` |
| Official logo | Native 512px source hash unchanged; valid transparent RGBA PNGs at 256px/512px |

The beta2 result is [BETA2_E2E_RESULTS.json](BETA2_E2E_RESULTS.json), generated by
`uv run --frozen python tools/e2e/run.py --report docs/BETA2_E2E_RESULTS.json`.
Real-service versions were HA 2026.11.0, HealthLog 1.47.1 and PostgreSQL 18.6.
The original beta1 E2E report remains [E2E_RESULTS.json](E2E_RESULTS.json), not
overwritten by release preparation. Apple Health/iOS remains outside E2E scope.
Remote Python/Hassfest/HACS results for the published revision are available in
[the validation workflow](https://github.com/cnrd/homeassistant-healthlog/actions/workflows/validate.yml).
