# HealthLog for Home Assistant

Send Home Assistant sensor measurements to your self-hosted
[HealthLog](https://github.com/MBombeck/HealthLog) server using a restricted
`measurements:write` token. Use any sensor manufacturer, with mappings from entity
states, attributes or Jinja templates. A persistent delivery queue keeps captured
measurements across restarts and handles retries.

One connection can serve multiple measurement profiles. Supported readings can
optionally reach Apple Health through the **HealthLog iOS application**, not
directly from Home Assistant.

This is an independent, community-maintained integration—not an official
HealthLog or Home Assistant product, and not a medical device or diagnostic tool.
See [GitHub releases](https://github.com/cnrd/homeassistant-healthlog/releases)
for available versions and prerelease notes.

## Features

- Manufacturer-independent entity-state, attribute and Jinja mappings.
- Multiple HealthLog connections, each with named measurement-profile subentries.
- One restricted measurement token shared by profiles in the same connection.
- Explicit units, settling delays and freshness checks to protect data quality.
- Persistent per-profile delivery queues, status sensors and retry buttons.
- Optional Apple Health mirroring using the HealthLog iOS application.

## Requirements

- **Home Assistant 2026.10.4 or newer.**
- **HealthLog 1.46.0 or newer**, with measurement ingestion enabled.
- A **restricted measurement token** with `measurements:write` for the intended account.
- HTTPS is the default. Plain HTTP requires an explicit `http://` URL and
  **Allow insecure HTTP**; it exposes tokens and health data to connection observers.
- Optional Apple Health: a compatible **HealthLog iOS 1.5.0+** app with
  server-directed mirroring and HealthKit write permissions.

LAN and remotely hosted servers are supported; a LAN server needs no internet
access. See [compatibility details](docs/CONFIGURATION.md#compatibility).

<!-- ha-marketplace:exclude-start -->
## Installation

The integration is available as a **custom repository**, not yet as an approved
public Marketplace/HACS catalog entry.

1. Add `https://github.com/cnrd/homeassistant-healthlog` to Home Assistant's
   Marketplace custom repositories. In HACS, use **Custom repositories** and
   select category **Integration**; do not select `content_in_root`.
2. Install **HealthLog**. Enable beta/prerelease versions if needed to select
   the version shown on [GitHub releases](https://github.com/cnrd/homeassistant-healthlog/releases).
3. Alternatively, download a release and copy only `custom_components/healthlog/`
   into your HA configuration's `custom_components/` directory.
4. Restart when Home Assistant requests it, and always after replacing loaded code.
5. Open **Settings → Devices & services → Add integration → HealthLog**.

[![Start HealthLog setup](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=healthlog)
<!-- ha-marketplace:exclude-end -->

## Quick start

1. In HealthLog **Settings → API & Tokens → Measurement ingest token**, create
   a restricted measurement token. Copy the complete `hlk_…` value, without `Bearer`.
2. Optionally enable **Write readings to Apple Health** on that token.
3. In Home Assistant, configure the HealthLog connection with its server URL and token.
4. Setup continues with the first **measurement profile**. Later, use
   **Add measurement profile** beneath the same connection—no extra token needed.
5. Name the profile, select a measurement trigger and map one or more sensor values.
6. Produce a genuinely new event and verify its account, value and timestamp in HealthLog.

A token belongs to a **specific HealthLog account**. Multiple measurement profiles
can share that connection/token; separate accounts require separate connections.
Sources must already identify the intended person—HA persons are not detected.

Setup checks syntax without creating test readings. It cannot confirm a write-only
token's validity or owner; the first real delivery is the connection test.

## Configuration overview

| Setting | Purpose |
| --- | --- |
| Connection | Server URL, restricted measurement token and transport settings. |
| Measurement profile | An independent source with its own trigger, mappings and delivery queue. |
| Trigger identity | A timezone-aware measurement timestamp, or a unique stable event ID. |
| State or attribute | Read a numeric entity state or an exact attribute key. |
| Jinja template | Derive one number from source snapshots or referenced entities. |
| Source unit | Describe the actual input/result unit; supported conversions happen automatically. |
| Settling / freshness | Wait for related updates and reject readings that are too old. |

Only future identity changes are captured; startup/restored states are not imported.
Edit a measurement profile through its own **Reconfigure** action. Edit the
connection to replace credentials for all its profiles.

See the [configuration guide](docs/CONFIGURATION.md) for timing, templates,
glucose context, multiple accounts and advanced examples.

## Example

These are **fields in the profile UI, not YAML configuration**. For a fictional
scale, use `sensor.example_scale` as the trigger, its `measured_at` attribute as
the identity, and timestamp mode. Add these mappings using the same source entity:

| Measurement type | Numeric attribute | Source unit |
| --- | --- | --- |
| `WEIGHT` | `weight` | `kg` |
| `BODY_FAT` | `body_fat` | `%` |
| `BODY_MASS_INDEX` | `bmi` | `kg/m²` |

The source must provide a new timezone-aware timestamp for every measurement.
Check your entity's real attribute names in **Developer Tools → States**; these
names are examples, not automatic defaults. [More examples](docs/CONFIGURATION.md#examples).

## Supported measurements

The 25 supported types cover body composition, blood pressure and heart rate,
heart-rate variability (HRV), oxygen saturation, glucose, temperature, respiration,
fitness, sleep and activity.
See the [complete metric reference](docs/METRICS.md) for exact types, canonical
units, ranges, conversions and safety requirements.

## Reliability and limitations

**Delivery is not unconditionally exactly once. Read the
[reliability guide](docs/RELIABILITY.md) before relying on unattended exports.**

- Successfully captured measurements are stored locally before delivery; the
  delivery queue survives Home Assistant restarts.
- Some metrics have durable server-side duplicate prevention. Others—and **all
  measurements with device notes**—use an endpoint without equivalent event identity.
- Uncertain deliveries are held for [manual reconciliation](docs/RELIABILITY.md#manual-reconciliation),
  not blindly retried. The integration prioritizes avoiding silent duplication or loss.
- Full queues and storage failures need attention: not every source event can
  be recovered automatically. Avoid overlapping profiles and reused timestamps.
- Deleting a profile may permanently remove its pending delivery queue and identity
  history. Disable the connection instead when you need to retain data.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Nothing uploads | Did a new trigger identity occur after setup? Check the profile's **Delivery status**. |
| Invalid/expired token | Replace it through the connection's **Reconfigure** or reauthentication prompt. |
| Rejected/stale readings | Check attribute names, source units, template output and update timing. |
| Held/uncertain delivery | Inspect HealthLog, then follow the [reconciliation procedure](docs/RELIABILITY.md#manual-reconciliation). |
| Missing Apple Health data | Check token mirroring, iOS compatibility, write permissions and synchronization direction. |

The retry button does not release uncertain records or bypass server backoff.
See [expanded troubleshooting](docs/TROUBLESHOOTING.md) before forcing retries.

## Privacy and security

Restricted measurement tokens are stored in HA configuration. Captured health
data may be stored locally and included in HA backups; this integration does not
encrypt those files. HTTPS is the default, with explicit opt-in required for HTTP.
The integration performs no third-party telemetry. Apple Health synchronization
is handled by HealthLog's iOS application.

Read the [security guide](docs/SECURITY.md) for account isolation, storage,
certificate verification and diagnostic privacy. Use [the security policy](SECURITY.md)
to report vulnerabilities without posting credentials or health data publicly.

## Development and links

- [Documentation index](docs/README.md) · [Configuration](docs/CONFIGURATION.md) · [Metrics](docs/METRICS.md)
- [Development and testing](docs/DEVELOPMENT.md) · [API audit](docs/API_CONTRACT.md)
- [GitHub issues](https://github.com/cnrd/homeassistant-healthlog/issues) · [Changelog](CHANGELOG.md)
- [MIT license](LICENSE) · [License and artwork attribution](docs/DEVELOPMENT.md#license-and-attribution)
