# Release notes

## v1.0.0-beta.2 — official HealthLog logo

- Replace the generic integration icon with the official HealthLog logo under
  [explicit maintainer permission](https://github.com/MBombeck/HealthLog/issues/1366#issuecomment-6746066303).
  The native 512px PNG is unchanged; the 256px icon is a Lanczos resize.
- Document logo ownership, permitted distribution and independent community
  maintenance. The integration's MIT license does not relicense the logo.
- Shorten the README and organize configuration, metrics, reliability,
  troubleshooting, security and development guidance into linked guides.
- No changes to delivery behavior, configuration or queue formats. Existing
  beta1 connections and measurement profiles can be upgraded in place; do not
  delete/recreate them or their queues for this update. Restart Home Assistant
  after installation. Icon caches may take time to refresh.

This remains a prerelease, not a public-catalog listing. All beta1 delivery and
token-validation limitations below still apply; Apple Health/iOS is outside the
tested scope. See [the reliability guide](docs/RELIABILITY.md).

## v1.0.0-beta.1 — first publication candidate

- New `healthlog` integration in standard Marketplace/HACS layout.
- Restricted measurement API token only; no passwords, MFA, refresh tokens,
  import endpoint, authentication store or predecessor migrations.
- Connection setup and token replacement use URL/API token plus an explicit
  insecure-HTTP opt-in (off by default). Account/scope guidance is descriptive
  text, not a confirmation checkbox. Scheme-less URLs default to HTTPS; ports,
  path prefixes, LAN and remote servers are supported, without an internet
  requirement for LAN hosting. HTTPS verification is never disabled and there
  is no automatic HTTP fallback.
- Uses Home Assistant's shared HTTP session with per-request cookie isolation
  and no automatic authenticated redirects.
- Retains generic per-person profiles, timestamp/stable-event triggers,
  state/attribute/Jinja mappings, dependency tracking and immutable snapshots.
- Uses native `ConfigSubentryFlow`: one server/account connection holds one token
  and supports multiple independently named profiles for different entities.
  Each profile has its own queue, operational entities and identity. Profile
  edits/removal do not restart sibling collectors; connection reauthentication
  pauses/resumes all profiles. Same-connection timestamp claims are coordinated.
- This remains beta1 and is a full replacement: earlier test configurations and
  storage formats are not recognized or migrated.
- Retains all 25 measurement types with safe units/ranges; glucose now requires
  explicit clinical context, shown only on a follow-up step when blood glucose
  is mapped. Non-glucose profiles skip that step. No guessed sleep intervals or
  water mass.
- Durable external identities for 16 exact batch mappings without notes;
  ordinary create for the remaining types or when notes must be preserved.
- Ordinary-route uncertain deliveries are held for explicit reconciliation.
  Retry never blindly replays them or bypasses persisted server backoff.
- Atomic queue read-back, copy-before-commit, cancellation-safe storage commits,
  persisted HTTP intent, indefinite completed-event identities and fail-closed
  corrupt-queue detection.
- Diagnostic delivery status, retry button, redacted diagnostic export, original
  branding, CI, comprehensive synthetic tests and first-user documentation.

### Prerelease limitations

No automatic permanent deduplication for ordinary-route writes under the current
HealthLog API; human reconciliation is required after uncertain results. Token
validation/owner binding cannot be checked during setup. All twelve real-service
HA → HealthLog E2E scenario groups passed. This is a beta prerelease, not a
public-catalog listing; Apple Health/iOS was outside the test scope.
See `docs/RELEASE.md`.
