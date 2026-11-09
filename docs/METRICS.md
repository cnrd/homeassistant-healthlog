# Supported measurement reference

[Documentation index](README.md) · [Configuration](CONFIGURATION.md) · [Reliability](RELIABILITY.md)

This reference covers the integration's 25 supported metric types, not every
metric HealthLog itself may accept. Canonical units (the units sent to HealthLog)
and inclusive ranges match
[models.py](../custom_components/healthlog/models.py); endpoint choices match
[transport.py](../custom_components/healthlog/transport.py).

Ranges are input-integrity guardrails, **not clinical advice or proof that a
reading is medically accurate**. The server also performs its own validation.

## Supported measurements

Values are converted to the canonical unit before the range is checked.
**Any nonempty device notes force the ordinary delivery path**, even for a
batch-capable type. Path names describe duplicate safeguards, not medical quality;
read [delivery-path limitations](RELIABILITY.md#delivery-paths-and-duplicate-prevention).

| Type | Canonical unit | Inclusive range | Delivery path without notes |
| --- | --- | --- | --- |
| WEIGHT | kg | 1–500 | Batch |
| BODY_MASS_INDEX | kg/m² | 8–70 | Batch |
| BODY_FAT | % | 1–80 | Batch |
| FAT_MASS | kg | 0–250 | Ordinary |
| FAT_FREE_MASS | kg | 10–250 | Ordinary |
| LEAN_BODY_MASS | kg | 10–250 | Batch |
| MUSCLE_MASS | kg | 5–200 | Ordinary |
| TOTAL_BODY_WATER | kg | 5–100 | Ordinary |
| BONE_MASS | kg | 0.5–8 | Ordinary |
| VISCERAL_FAT | rating | 0–30 | Ordinary |
| BLOOD_PRESSURE_SYS | mmHg | 40–300 | Batch |
| BLOOD_PRESSURE_DIA | mmHg | 20–200 | Batch |
| PULSE | bpm | 20–300 | Batch |
| RESTING_HEART_RATE | bpm | 25–220 | Batch |
| HEART_RATE_VARIABILITY | ms | 1–200 | Batch |
| OXYGEN_SATURATION | % | 50–100 | Batch |
| BLOOD_GLUCOSE | mg/dL | 20–800 | Ordinary; explicit context |
| BODY_TEMPERATURE | celsius | 28–45 | Batch |
| SKIN_TEMPERATURE | celsius | 20–45 | Ordinary |
| RESPIRATORY_RATE | breaths/min | 3–60 | Batch |
| VO2_MAX | mL/(kg·min) | 5–100 | Batch |
| SLEEP_DURATION | minutes | 0–1440 | Ordinary; no inferred interval/stage |
| ACTIVITY_STEPS | steps | 0–200000 | Batch |
| ACTIVE_ENERGY_BURNED | kcal | 0–10000 | Batch |
| WALKING_RUNNING_DISTANCE | m | 0–200000 | Batch |

## Source units and conversions

The source-unit field describes the **actual input**, or a template's result.
Leaving it blank on a direct mapping uses the entity's `unit_of_measurement`;
attributes may need explicit units. Templates always require a result unit.

Use the exact canonical spelling above, one of these aliases, or an applicable
conversion below. Unit spellings are case-sensitive; unknown spellings and
dimensionally incompatible units are rejected, not guessed.

| Accepted alias | Canonical spelling |
| --- | --- |
| `°C`, `C` | `celsius` |
| `min` | `minutes` |
| `kg/m2` | `kg/m²` |

| Source → target | Conversion |
| --- | --- |
| `lb` or `lbs` → `kg` | Multiply by 0.45359237. |
| `g` → `kg` | Multiply by 0.001. |
| `h` → `minutes` | Multiply by 60. |
| `s` → `minutes` | Divide by 60. |
| `km` → `m` | Multiply by 1000. |
| `mi` → `m` | Multiply by 1609.344. |
| `kJ` → `kcal` | Divide by 4.184. |
| `mmol/L` → `mg/dL` | Multiply by 18.0182, for glucose. |
| `°F` → `celsius` | Subtract 32, then multiply by 5/9. |

Conversions apply only to compatible target metrics. For example, `g` is a valid
input for a mass metric, not blood pressure. The integration does not offer
arbitrary unit relabeling or automatic percentage/fraction inference.

Values use Python/JSON floating-point numbers. No intentional rounding or
clamping is applied, but arbitrary decimal precision is not guaranteed. The
server stores timestamps at millisecond precision; see
[timestamp semantics](CONFIGURATION.md#timestamp-mode).

## Special requirements and interpretation

### Body composition and temperature

- **Body water is mass in kg, never percentage.** A water-percentage attribute
  cannot be made valid by typing `kg` in the unit field.
- `BODY_FAT` and `OXYGEN_SATURATION` use canonical percent values, not raw
  fractions. Batch wire-format scaling is handled internally, not by relabeling
  your source.
- `VISCERAL_FAT` is a rating, not body-fat percentage.
- Fat mass, fat-free mass, lean body mass, muscle mass and bone mass are distinct
  types. Do not substitute one to gain a different endpoint's duplicate protection.
- Body and skin temperature are distinct measurements even though both use
  `celsius`. They are not temperature deviations from a personal baseline.

### Blood glucose

The ordinary HealthLog endpoint requires an explicit context for glucose and
forbids it on other metrics. The profile's conditional context step offers:

| Stored context | UI label |
| --- | --- |
| `FASTING` | Fasting |
| `POSTPRANDIAL` | After a meal |
| `RANDOM` | Random |
| `BEDTIME` | Bedtime |

Choose the actual clinical context; **Random is not a fallback for unknown**.
If context varies, use separate context-specific profiles and triggers. If it is
unknown, do not configure the mapping. Existing context is prefilled for review
when editing, but a new profile receives no inferred default. Frozen records
keep the context captured with their event.

### HRV estimators

`HEART_RATE_VARIABILITY` means **SDNN**, not RMSSD. Milliseconds alone do not
make two estimators equivalent. RMSSD is not one of this integration's supported
types; do not map it as SDNN.

### Sleep duration

Supply a real measured duration in minutes or a supported time unit. A duration
alone does not provide genuine start/end dates or a sleep stage, so the
integration uses ordinary creation instead of inventing an interval/stage.

### Activity increments and cumulative totals

Steps, active energy and walking/running distance records must represent distinct
event amounts, such as a completed interval or one completed day's amount.
Do not repeatedly upload an increasing running daily total as new immutable
events: HealthLog may sum those records, overstating activity. The integration
neither computes deltas nor overwrites an earlier total for you.

## Invalid and unavailable readings

Booleans, NaN, infinity, missing/unavailable/restored sources, stale snapshots,
invalid templates, incompatible units and out-of-range values are not uploaded.
The integration does not replace them with zero, clamp them into range or invent
required context. Locally invalid readings are counted as rejected; they do not
become server-rejected queued payloads that the retry button can reconstruct.

Correct the source/mapping for future events and inspect the profile's diagnostic
status. Server rejection of an already-frozen record follows different rules:
see [rejected versus uncertain deliveries](RELIABILITY.md#retry-and-rate-limits).
Being listed here does not guarantee that the HealthLog iOS app can write that
type to Apple Health; its own writable types and permissions apply.
