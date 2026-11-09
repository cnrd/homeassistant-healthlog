# Privacy and security guide

[Documentation index](README.md) · [Reliability](RELIABILITY.md) · [Report a vulnerability](../SECURITY.md)

Health measurements are private information. Protect the HA installation,
HealthLog server and their backups—not only the network connection between them.
This is a community-maintained integration, not an official product or medical
device. Server/iOS security and permissions are separate responsibilities.

## Restricted measurement tokens and account isolation

Use only a **restricted measurement token** with `measurements:write`. HealthLog
limits that credential to writing its owner's record through supported ingest
routes; it cannot read history or write to another shared/delegated account.
Do not grant HA a full-access login token to work around an ingestion problem.

A connection owns one server URL and one account's token. Its measurement-profile
subentries inherit those credentials; they cannot supply their own URL/token.
Different accounts require separate connections and appropriately associated
sources. HA persons and the person using a shared device are **not detected
automatically**. A misconfigured source or deliberately wrong token can still
send readings to the wrong intended person.

Profile delivery queues and identities are isolated. A 401 pauses delivery for
the connection's profiles and requests parent reauthentication. Per-request
authorization keeps unrelated connections' tokens separate, even though they
use HA's shared HTTP session.

### No automatic token or owner introspection

No safe non-mutating restricted-token validation endpoint was found in the
audited API. Setup performs local URL/token syntax checks only and does not send
GET probes, manufacture readings or obtain broader credentials. It cannot prove
that a syntactically valid `hlk_` value has restricted scope or belongs to the
intended owner. Verify both in HealthLog; an empty `ready` queue is not proof of
authentication. Test with one genuinely new event in the correct account.

The upstream evidence is in [API authentication](API_CONTRACT.md#authentication-and-validation).

## Transport security

- Addresses without a scheme receive `https://`; optional ports/path prefixes
  are preserved. LAN and remote servers are supported without an inherent
  internet requirement for a LAN instance.
- HTTPS always verifies the certificate using HA's normal trusted certificate
  store. There is no integration setting to disable TLS verification.
- HTTP requires **both** an explicit `http://` address and **Allow insecure HTTP**
  (`allow_insecure_http`), off by default. It exposes the token and health
  measurements to anyone capable of observing that connection.
- Enabling HTTP does not change a scheme-less URL to HTTP, bypass HTTPS
  verification or allow fallback after a connection/certificate failure.
- Embedded URL credentials, unsupported schemes and malformed URLs are rejected.
  Tokens belong in the token field, not a URL path/query or embedded password.

A TLS reverse proxy may serve HTTPS to HA while forwarding HTTP internally to
HealthLog. That backend hop has its own trust boundary; secure it appropriately.
For private CAs, configure trust using HA's supported certificate environment
rather than weakening verification here. Certificate hostname and validity must
still match the configured URL. See [URL setup](CONFIGURATION.md#server-url-and-transport).

### Redirects and shared-session cookies

Authenticated requests never automatically follow redirects, including
same-origin, cross-origin and HTTPS-to-HTTP redirects. Configure the actual API
base URL instead of relying on a redirect to a new host, prefix or login page.

Requests use HA's managed `async_get_clientsession(hass)` with request-local
bearer headers. The integration does not close that shared session or mutate its
default headers/cookie values. Cached cookie values, including both known
HealthLog session-cookie names, are blanked for each request: the audited server
prefers a valid session cookie over a bearer token, so a cached browser session
must not override the connection's account identity.

## Changing tokens and destinations

Use the **connection's Reconfigure** action or reauthentication prompt to replace
a token. The change applies to all profiles beneath it, including queued data.
The replacement must belong to the **same account**; owner binding cannot be
independently verified with the write-only API.

The hostname and installation path prefix must remain the same for an existing
connection. Protocol/port changes are permitted for that same server, subject to
the same security policy. Use a separate connection for another account/server.
Do not redirect another person's queue or change tokens as a way to release
held records. Frozen values/identities and uncertain holds remain unchanged.

## Local storage and backups

HA configuration stores connection URLs and restricted measurement tokens.
Profile subentries store their names, source/mapping/template settings and
random profile identities, not additional credentials.

Private HA queue files use:

```text
.storage/healthlog.<connection_id>.<profile_id>.queue
```

They can contain pending numeric source/rendered values, units, update times,
mapping/template text, dependency identifiers, original measurement timestamps,
frozen health values, notes, glucose context, delivery state and identity history.
Templates receive detached source attributes during rendering; the collector's
stored snapshot contains the numeric/rendered result and dependency metadata,
not the entire detached `source` object. Configured template text/notes can
themselves contain private information.

The integration does **not** encrypt configuration, queues or backups. Restrict
HA filesystem/UI access and protect local/off-site backups, using backup
encryption where available. Copies outside HA may survive integration removal.
Do not publish `.storage` files, raw recorder histories, tokens or private
temporary E2E workspaces in bug reports.

### Deletion and retained queues

Deleting a profile while its connection is loaded removes only its local queue,
held records and identity history. Unattached stores after interrupted setup,
partial restores or deletion while disabled are retained for safety. Explicit
connection removal clears all its owned profile stores, including unattached
and corrupt copies; it does not delete HealthLog's records or external backups.

**Disable the connection to retain its data rather than deleting it.** For
recovery implications, see [queue deletion and retention](RELIABILITY.md#queue-deletion-and-retention).

## Logs and diagnostic privacy

Operational entities expose counts, delivery activity, status and sanitized
error codes, not API tokens or raw biometric values. Diagnostic downloads use an
allowlist: connection authentication state, entry state, profile/mapping counts
and operational profile status. They do not export configured URLs, tokens,
source entity names, templates, notes or queue payloads.

Raw HTTP response bodies and private template exception details are not logged
by the integration. Status timestamps/counts still reveal measurement-delivery
activity. Review any attachment before sharing, and do not assume unrelated HA
logs, other integrations or upstream server logs have identical redaction rules.

## Data destination and Apple Health

The integration performs no third-party analytics or telemetry and sends selected
measurements to the configured HealthLog endpoint. This does not describe the
server's optional integrations or iOS application's own network behavior.

Apple Health mirroring is performed by **HealthLog's iOS application**, using
the server's eligibility decision, token setting and HealthKit permissions.
Home Assistant does not write to HealthKit directly. See
[Apple Health troubleshooting](TROUBLESHOOTING.md#apple-health-measurements-do-not-appear).
