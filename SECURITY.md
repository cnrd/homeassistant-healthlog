# Security and private data

Do not post tokens, raw biometric readings, storage files, or unredacted source
histories in public issues. Revoke a leaked token in HealthLog immediately.

For a security vulnerability, use GitHub's private vulnerability reporting for
`cnrd/homeassistant-healthlog` if enabled, or contact the maintainer privately
before publishing details. Ordinary integration diagnostics are intentionally
allowlisted; review any attachment before sharing.

The integration defaults to verified HTTPS; addresses without a scheme receive
`https://`. Plain HTTP requires both an explicit `http://` URL and the
`allow_insecure_http` option (off by default). HTTP exposes API tokens and health
measurements to anyone capable of observing the connection. This option never
disables HTTPS certificate verification or enables fallback after a TLS failure.
Authenticated requests never automatically follow redirects, including
cross-origin and HTTPS-to-HTTP redirects.

Requests use Home Assistant's shared HTTP session with request-local cookie-value
suppression, so a cached HealthLog browser cookie cannot override the profile's
bearer identity. The shared cookie jar/default headers are not changed.

A connection owns one token for one account. Its measurement-profile subentries
inherit that token and cannot override credentials or server URLs. Different
accounts must use separate connections; the integration does not identify HA
persons. Profile queues/identities are isolated, while credential failure pauses
all profiles in the affected connection. Profile deletion while loaded removes
its local queued data; disable the connection instead when data must be retained.
Unattached queues are never automatically garbage-collected on startup, since
HA's profile metadata and queue captures are separate durable writes. Explicit
connection removal clears all its owned stores, including unattached ones.

LAN and remote servers are supported; a local server does not require internet
access. The integration does not encrypt Home Assistant config entries, queues
or backups. Restrict access to those files and use measurement-write-only tokens,
never full-account tokens.
