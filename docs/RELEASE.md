# Release checklist and current gates

Initial release: **v1.0.0-beta.1**, not v1.0.0. The maintainer has authorized the
initial push and GitHub prerelease. Further releases require separate approval.

Executed checks are recorded in [VALIDATION.md](VALIDATION.md).

## Local validation

Use the exact pinned development dependencies (`uv.lock`) and Python 3.14:

```sh
uv sync --frozen --group dev
uv run pytest
uv run ruff check custom_components tests tools
uv run ruff format --check custom_components tests tools
uv run mypy
docker run --rm --platform linux/amd64 -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

The official HACS action needs published repository content and is not a fully
offline validator. After the authorized initial push, it passed all 10 checks.
GitHub Actions also passed the Python, Hassfest and HACS jobs. Executed results
and the initial run link are recorded in `VALIDATION.md`.

## Publication gates

- [x] Explicit maintainer authorization to publish/push as a prerelease.
- [x] Review the license and original artwork for publication.
- [x] Review local tests and current validation results in the handoff.
- [x] Run Hassfest and HACS on the public repository revision; both passed.
- [x] Set GitHub description: **Home Assistant integration for durable measurement delivery to self-hosted HealthLog.**
- [x] Set topics: `home-assistant`, `custom-integration`, `healthlog`, `hacs`, `health`, `self-hosted`.
- [x] Keep GitHub issues enabled and align beta prerelease metadata/tag version.
- [x] Review repository contents/packaging for private data and generated files.
- [x] Test a fresh installation and multiple entries on a disposable HA against
  real HealthLog: all 12 E2E scenario groups passed (see `E2E_RESULTS.json`).
- [ ] With maintainer consent, separately verify the intended deployment and
  eventual iOS mirroring. Apple Health/iOS is outside the automated HA → HealthLog
  E2E scope and has not been tested.
- [ ] Verify `healthlog` remains unused by built-in Home Assistant integrations
  and is not occupied by another Marketplace repository at submission time.
- [ ] Submit to the public catalog only after all required remote checks pass.

The domain was absent from the installed Home Assistant 2026.10.4 components and
the upstream core `dev` manifest URL at audit time (404). This is a point-in-time
check, not a reservation.

This remains **beta1**, with a fresh connection/subentry layout. Earlier test
configurations and storage are not supported or migrated. Remove those test
entries and set up the new structure from scratch. No compatibility code is
included for any prior deployment/layout.

## Why a prerelease?

All 25 metrics are implemented deliberately, but full unattended durable retries
are impossible for nine ordinary-route metrics and for measurements with notes
under the audited public API. The safest behavior is implemented: uncertain
writes stay durable and require explicit reconciliation. The API enhancement in
`API_CONTRACT.md` is not present upstream. Token scope/owner introspection is
also unavailable, so setup cannot verify a token without a genuine event.

The real HA → HealthLog fresh-install/recovery E2E suite passed locally, and
Hassfest/HACS validation passed on the public repository. Apple Health/iOS was
excluded from testing, and no public catalog submission has been made. This
release remains beta1 for controlled testing with the API limitations above;
passing validators does not imply catalog approval or exactly-once delivery for
all metrics. A stable release requires a separate maintainer decision.

The batch reconciler can also update a same-timestamp/different-external-ID
collision. Local timestamp claims block known collisions; unexpected server
updates are held for inspection. Cross-writer collisions cannot be prevented
locally. Distinct events need distinct timestamps and non-overlapping profiles.
An upstream refusal rather than overwrite is needed for a stronger guarantee.

## Packaging

Only one integration directory: `custom_components/healthlog/`. Marketplace/HACS
installs that directory. Everything it imports is inside it; tests/tooling/docs
are not runtime dependencies. No release ZIP layout override is needed.
`translations/en.json` is the complete shipped translation (no strings.json).
Local images are original transparent PNGs, 256px/512px, with no external license
or Home Assistant logo. `tools/create_brand.py` reproduces them.

The manifest uses service type and `assumed_state`: the integration issues
outgoing writes and cannot poll the service or receive remote measurement state.
Neither cloud_push nor local_push correctly describes a write-only API that can
be hosted locally or remotely.
