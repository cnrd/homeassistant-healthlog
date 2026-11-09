# HealthLog integration documentation

New users can start with [installation and quick start](../README.md#installation).
These guides describe the Home Assistant integration, not the HealthLog server
or iOS application's own implementation.

## User guides

| Guide | What it covers |
| --- | --- |
| [Configuration](CONFIGURATION.md) | Compatibility, tokens, connections, measurement profiles, triggers, templates and examples. |
| [Metric reference](METRICS.md) | All supported types, units, limits, conversions and special requirements. |
| [Reliability](RELIABILITY.md) | Persistent delivery queues, duplicate safeguards, retries, conflicts and manual reconciliation. |
| [Troubleshooting](TROUBLESHOOTING.md) | Symptom-based checks, diagnostic entities, authentication and Apple Health. |
| [Security](SECURITY.md) | Restricted measurement tokens, account isolation, transport, local storage and backups. |

**Important:** some delivery outcomes cannot be safely retried automatically.
Read [manual reconciliation](RELIABILITY.md#manual-reconciliation) before releasing
a held record. Do not delete a measurement profile to force delivery.

## Maintainer references

- [Development](DEVELOPMENT.md): environment setup and checks.
- [API contract audit](API_CONTRACT.md): exact upstream endpoint evidence and gaps.
- [E2E testing](E2E.md): disposable real-service test scope and execution.
- [Validation record](VALIDATION.md): checks actually executed and their limitations.
- [Release checklist](RELEASE.md): publication gates and packaging.
- [Changelog](../CHANGELOG.md), [license](../LICENSE) and [vulnerability reporting](../SECURITY.md).
