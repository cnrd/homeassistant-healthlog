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

The integration's own code is released under the [MIT license](../LICENSE).
That license does **not** purport to relicense the HealthLog logo. Logo copyright
and ownership remain with the [HealthLog project](https://github.com/MBombeck/HealthLog).

### Official HealthLog logo

HealthLog maintainer **MBombeck** explicitly authorized use of the unchanged logo
in this integration on November 9, 2026:
[permission and conditions](https://github.com/MBombeck/HealthLog/issues/1366#issuecomment-6746066303).
Permission covers redistribution through **GitHub, HACS, Home Assistant Marketplace
and `home-assistant/brands`**, to commercial and noncommercial users. Resizing
and format conversion are permitted; the original logo design must stay unchanged.
The listing must identify this as an independent community integration and link
to HealthLog. The [README](../README.md) retains both the disclaimer and that link.
Logo use does not imply official endorsement or ownership of the HealthLog brand.

Official artwork sources in `MBombeck/HealthLog`:

- [`public/logo-512.png`](https://github.com/MBombeck/HealthLog/blob/main/public/logo-512.png):
  source for both shipped icons.
- [`public/logo-readme.svg`](https://github.com/MBombeck/HealthLog/blob/main/public/logo-readme.svg):
  official vector alternative, downloaded for reference but not rasterized or
  duplicated in this repository.

The assets were obtained directly from upstream revision
[`f86a7a61e1e6226732474bfbdc5346748b2027e1`](https://github.com/MBombeck/HealthLog/tree/f86a7a61e1e6226732474bfbdc5346748b2027e1/public).
The PNG's SHA-256 is
`b17d9b736cec3937c024bba7da483cc94162b5839b8d5fbd37b89da2d3502569`.

| Shipped file | Preparation |
| --- | --- |
| `custom_components/healthlog/brand/icon@2x.png` | Byte-for-byte copy of the upstream 512 × 512 RGBA PNG. |
| `custom_components/healthlog/brand/icon.png` | 256 × 256 RGBA PNG, resized from that source with Pillow's Lanczos resampling. |

No cropping, recoloring, redrawing, background replacement or composition changes
are applied. The source's transparency and original canvas/margins are retained.
The [preparation helper](../tools/create_brand.py) only copies/resizes supplied
artwork; it no longer draws the retired generic icon. To reproduce these files,
download the PNG from the pinned revision, then run:

```sh
uv run --with pillow==12.3.0 python tools/create_brand.py /path/to/logo-512.png
```

### Marketplace branding and optional submission

The current [Home Assistant branding documentation](https://developers.home-assistant.io/docs/creating_integration_brand/)
supports local `custom_components/<domain>/brand/` images since HA 2026.3,
earlier than this integration's minimum HA version. The icon filenames and
dimensions above match its requirements. Square icons also serve as logo
fallbacks, so no additional variants are needed. The upstream PNG has transparent
outer margins; these are deliberately retained to preserve the authorized artwork
and exact native-size source, rather than applying the documentation's trimming
guidance. A separate brands-repository review may require discussing that canvas.

A `home-assistant/brands` submission is **optional**, already authorized by the
HealthLog maintainer, and is not opened automatically. The
[brands repository documentation](https://github.com/home-assistant/brands#marketplace-icons)
describes local custom-integration images and Marketplace's latest-release image
lookup/cache. Local assets take precedence after installation. Publishing these
files in a future separately authorized release/catalog update may be needed for
the new icon to appear in pre-installation listings; this change does not itself
publish, tag, release or submit anything.

HealthLog and Home Assistant are separate upstream projects with their own
licenses and branding. The client does not incorporate upstream HealthLog server
source; the [API audit](API_CONTRACT.md#sources-inspected) records the wire-contract
facts used to implement it independently. This remains a community-maintained
integration, not an official product of either project.
