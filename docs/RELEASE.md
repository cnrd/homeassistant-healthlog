# Release checklist and current gates

Current release target: **v1.0.0**, the first stable release. The maintainer has
authorized publishing it after production validation of beta2 and the full
regression suite. Further releases require separate approval. Existing beta1
and beta2 tags must not be changed.

Executed checks are recorded in [VALIDATION.md](VALIDATION.md).

[Documentation index](README.md) · [Development](DEVELOPMENT.md)

## Local validation

Follow [Development and testing](DEVELOPMENT.md#checks-before-a-pull-request)
using the pinned dependencies and supported Python environment. Run its unit,
lint/format, type-checking and [Hassfest/HACS checks](DEVELOPMENT.md#hassfest-and-hacs-validation)
against the intended release revision, plus the [real-service E2E suite](E2E.md).

The official HACS action needs published repository content and is not a fully
offline validator. After the authorized initial push, it passed all 10 checks.
GitHub Actions also passed the Python, Hassfest and HACS jobs. Executed results
and the initial run link are recorded in [VALIDATION.md](VALIDATION.md).

## Publication gates

- [x] Explicit maintainer authorization to publish/push **v1.0.0 as stable**.
- [x] Review the software license and [HealthLog logo permission](DEVELOPMENT.md#license-and-attribution).
- [x] Review local tests and current validation results in the handoff.
- [x] Run Hassfest and HACS on the public repository revision; both passed.
- [x] Set GitHub description: **Home Assistant integration for durable measurement delivery to self-hosted HealthLog.**
- [x] Set topics: `home-assistant`, `custom-integration`, `healthlog`, `hacs`, `health`, `self-hosted`.
- [x] Keep GitHub issues enabled; align internal `1.0.0` and annotated `v1.0.0` tag.
- [x] Review repository contents/packaging for private data and generated files.
- [x] Prior beta1/beta2 disposable-service E2E runs passed; retain their reports.
- [x] Stable regression: 347 tests, 93% coverage, and all 13 real-service E2E
  groups passed, including five singleton requests at the five-second default
  (see [the stable validation record](VALIDATION.md#stable-100-automated-validation)).
- [x] Maintainer reported two real Xiaomi S400 weigh-ins reaching HealthLog on
  beta2; the second reached Apple Health through the iOS app without manually
  opening it. See [production observation](VALIDATION.md#production-observation).
  Apple Health/iOS remains outside automated E2E scope.
- [ ] Verify `healthlog` remains unused by built-in Home Assistant integrations
  and is not occupied by another Marketplace repository at submission time.
- [ ] Submit to the public catalog only after all required remote checks pass.

The domain was absent from the installed Home Assistant 2026.10.4 components and
the upstream core `dev` manifest URL at audit time (404). This is a point-in-time
check, not a reservation.

Stable preserves beta2's connection/subentry layout and queue format. Upgrade
beta installations in place and restart HA; do not delete profiles or queues.
Saved delivery delays remain unchanged, including 60 seconds. Opt into the new
five-second default through each existing profile's Reconfigure flow if desired.
Only the earlier unpublished, pre-beta1 test layouts remain unsupported and
unmigrated. This is not a request to recreate existing beta1 installations.

## Stable release scope and limitations

All 25 metrics are implemented deliberately, but full unattended durable retries
are impossible for nine ordinary-route metrics and for measurements with notes
under the audited public API. The safest behavior is implemented: uncertain
writes stay durable and require explicit reconciliation. The API enhancement in
[the API audit](API_CONTRACT.md#proposed-narrowly-scoped-upstream-improvements-not-implementedpublished)
is not present upstream. Token scope/owner introspection is
also unavailable, so setup cannot verify a token without a genuine event.

Stable publication reflects the maintainer's observed production success and
regression validation, not removal of upstream API limitations. Production
evidence covers the two reported weigh-ins and one unattended iOS sync, not all
metrics, recovery cases or background conditions. Automated HA → HealthLog E2E
checks remain synthetic and exclude iOS/Apple Health. Passing validators and
stable status do not imply public-catalog approval or exactly-once delivery.
No catalog or brands-repository submission is part of this release.

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
Local `brand/icon.png` and `brand/icon@2x.png` are the official HealthLog logo,
256px/512px, used under [explicit permission](DEVELOPMENT.md#license-and-attribution),
not relicensed as MIT artwork. `tools/create_brand.py` prepares them from the
upstream PNG without redrawing or restyling. No Home Assistant logo is used.

The manifest uses service type and `assumed_state`: the integration issues
outgoing writes and cannot poll the service or receive remote measurement state.
Neither cloud_push nor local_push correctly describes a write-only API that can
be hosted locally or remotely.
