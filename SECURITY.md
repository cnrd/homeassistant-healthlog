# Reporting security issues

Do not post API tokens, raw biometric readings, storage files or unredacted
source histories in public issues. Revoke a leaked token in HealthLog immediately.

For a vulnerability in `cnrd/homeassistant-healthlog`, use GitHub's private
vulnerability reporting if enabled, or arrange private contact with the
maintainer before publishing details. Integration diagnostics are allowlisted,
but review every attachment before sharing it.

The [privacy and security guide](docs/SECURITY.md) explains restricted measurement
tokens, account isolation, HTTPS/HTTP policy, credential/health-data storage,
backup protection and diagnostic redaction.
