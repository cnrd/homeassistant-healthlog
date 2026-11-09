# Development and testing

[Documentation index](README.md) · [E2E plan](E2E.md) · [Executed validation](VALIDATION.md) · [Release checklist](RELEASE.md)

## Environment setup

Use Python **3.14.2 or newer** and [uv](https://docs.astral.sh/uv/). HA's own
Python runtime is managed by HA; this requirement is for repository development.
The pinned development group in [pyproject.toml](../pyproject.toml) and
[uv.lock](../uv.lock) supplies the HA test fixtures, pytest coverage, Ruff and mypy.

```sh
git clone https://github.com/cnrd/homeassistant-healthlog.git
cd homeassistant-healthlog
uv sync --frozen --group dev
```

Run checks from the repository root. Do not add a production server/token or
health-history fixture: automated tests use synthetic data and mocked HTTP or
explicitly disposable real-service instances.

## Checks before a pull request

### Unit tests

```sh
uv run pytest
```

For a focused test or an XML coverage report:

```sh
uv run pytest tests/test_config_lifecycle.py
uv run pytest --cov-report=xml
```

The fixture version pins its own HA version. The real-container HA version is
separate; consult [the validation record](VALIDATION.md) instead of assuming
every compatible/newer HA or HealthLog version was tested.

### Linting, formatting and type checking

```sh
uv run ruff check custom_components tests tools
uv run ruff format --check custom_components tests tools
uv run mypy
```

Use `uv run ruff format custom_components tests tools` when intentionally applying
formatting. Mypy configuration checks the integration source. Preserve existing
HA lifecycle patterns, profile/account isolation and privacy-safe errors.

## Hassfest and HACS validation

[The validation workflow](../.github/workflows/validate.yml) runs Python checks,
tests, Hassfest and the official HACS action on pushes/pull requests.
For the local Hassfest image used in the validation record:

```sh
docker run --rm --platform linux/amd64 -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

The explicit platform supports running that image on an ARM development host.
HACS validation checks actual public repository content, not merely a mounted
local folder, and needs GitHub authentication. Run the configured action against
the intended revision; never print/embed a token or mistake unavailable remote
validation for a passing check. Passing validators is not public-catalog approval.
Publication gates live in [the release checklist](RELEASE.md#publication-gates).

## Real-service E2E tests

Docker Compose is required. The runner provisions its own HA, HealthLog,
PostgreSQL, TLS trust and synthetic accounts/sources; it must not use production
data. Start a full or shorter configuration/lifecycle run with:

```sh
uv run python tools/e2e/run.py
uv run python tools/e2e/run.py --configuration-smoke
```

These are alternative runs. See [E2E.md](E2E.md) for image pins, fault injection,
assertions, report locations, cleanup, debugging and limitations. Do not duplicate
or improvise its deployment settings against a live server. Private temporary
workspaces contain generated credentials/instance data; do not attach them to a
public issue. Only the safe summaries are intended for sharing.

The manual [E2E workflow](../.github/workflows/e2e.yml) executes the full suite.
Previously executed results are evidence for their recorded revision/environment,
not an automatic claim that later changes passed those checks.

## Architecture and review references

- [Reliability](RELIABILITY.md#capture-and-delivery-lifecycle): capture/freeze,
  persistence, intent, acknowledgement and recovery boundaries.
- [API audit](API_CONTRACT.md): exact upstream endpoint schemas and identity gaps.
- [Security](SECURITY.md): credentials, transport, backups and diagnostic allowlists.
- [Metric reference](METRICS.md): user-facing types, units and integrity rules.
- [Release checklist](RELEASE.md) and [changelog](../CHANGELOG.md): publication
  and version-specific notes, not runtime compatibility/migration promises.

Review cancellation, storage failures, uncertain delivery, concurrent sibling
reservations and connection-wide reauthentication when changing delivery code.
Do not simplify durable acknowledgement into an HTTP-success-only check.

## License and attribution

The integration and its original notebook/pulse artwork are released under the
[MIT license](../LICENSE). [The artwork renderer](../tools/create_brand.py)
reproduces the shipped images. They are not Home Assistant logos or an assertion
of official endorsement.

HealthLog and Home Assistant are separate upstream projects with their own
licenses and branding. The client does not incorporate upstream HealthLog server
source; the [API audit](API_CONTRACT.md#sources-inspected) records the wire-contract
facts used to implement it independently. This remains a community-maintained
integration, not an official product of either project.
