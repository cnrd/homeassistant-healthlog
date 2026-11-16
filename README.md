# HealthLog for Home Assistant

**Home Assistant sensors → HealthLog → optionally Apple Health.**

Send measurements from your existing Home Assistant entities to your self-hosted
[HealthLog](https://github.com/MBombeck/HealthLog) server. Map numeric states,
attributes, or Jinja templates to HealthLog measurements, regardless of sensor
manufacturer.

One HealthLog connection supports multiple named measurement profiles, each with
its own event trigger, mappings, and persistent delivery queue. Captured
measurements survive Home Assistant restarts and follow conservative retry and
duplicate-prevention rules.

**Optional Apple Health mirroring** happens through the HealthLog iOS app, with
explicit permission on the measurement token. Home Assistant does not write to
Apple Health directly.

This is an independent, community-maintained integration—not an official
HealthLog or Home Assistant product, and not a medical device or diagnostic tool.
See the [stable releases](https://github.com/cnrd/homeassistant-healthlog/releases)
for updates.

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

[![Open HealthLog in Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cnrd&repository=homeassistant-healthlog&category=integration)

**Home Assistant 2026.11+:** Open **Settings → Marketplace**, search for
**HealthLog**, select the integration and choose **Install**.

**Home Assistant 2026.10.4–2026.10.x:** Open **HACS**, search for **HealthLog**
in the integration catalog and install it. The badge above works with both
Marketplace and older HACS installations.

Restart Home Assistant after installation, then go to
**Settings → Devices & services → Add integration → HealthLog**.

[![Start HealthLog setup](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=healthlog)

**Manual installation:** If you do not use Marketplace/HACS, download a
[release](https://github.com/cnrd/homeassistant-healthlog/releases), copy
`custom_components/healthlog/` into your Home Assistant configuration's
`custom_components/` directory, and restart Home Assistant. You no longer
need to add this project as a custom repository in Marketplace/HACS.
<!-- ha-marketplace:exclude-end -->

## Quick start: your first measurement

1. **Create a restricted token in HealthLog.** Go to
   **Settings → API & Tokens → Measurement ingest token** and copy the complete
   `hlk_…` token. Do not add `Bearer`. Some HealthLog versions call this
   **API → Measurement tokens**.
2. **Connect Home Assistant.** Add the **HealthLog** integration, then enter
   your server URL and the restricted token.
3. **Configure a measurement profile.** Name the source, select an entity that
   produces a genuinely new timestamp or stable event ID for each measurement,
   and map its numeric states/attributes (or templates) to the appropriate
   HealthLog measurement types and source units.
4. **Take a new measurement.** Confirm the values and timestamp in HealthLog.
   Check the profile's **Delivery status** sensor if nothing appears.
5. **Optionally enable Apple Health.** Turn on **Write readings to Apple Health**
   for the token in HealthLog and configure HealthKit write permissions and
   supported measurement directions in the HealthLog iOS app.

A connection belongs to **one HealthLog account**. Profiles can share its
token only when measurements belong to the same person; separate accounts
require separate connections. Shared devices must identify the intended person
in their source events—Home Assistant people are not detected automatically.

Setup checks configuration syntax without creating a test reading. Because the
token is write-only, setup cannot verify its owner or validity: the first real
measurement is the connection test.

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

New profiles default to a **five-second delivery delay**. Existing profiles keep
their saved setting, including 60 seconds; change it through the profile's
**Reconfigure** action if desired. Settling and freshness settings are unchanged.

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

## Optional Apple Health sync

HealthLog's iOS app can mirror supported measurements received from this
integration into Apple Health. To use it, you need **HealthLog server 1.46.0+**,
a compatible **HealthLog iOS 1.5.0+** app, HealthKit write permissions, and
**Write readings to Apple Health** enabled for the restricted token.

The server keeps the readings' source as `EXTERNAL`; it determines whether
each reading is eligible for mirroring based on its token. Not every HealthLog
measurement type is writable to Apple Health. Background mirroring is
**eventual, not immediate**: iOS decides when the app gets background execution
time. See [compatibility and configuration](docs/CONFIGURATION.md#compatibility).

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
