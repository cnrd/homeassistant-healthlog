"""Explicit supported mappings and conservative unit conversion.

Units/ranges verified against HealthLog unit-map.ts and value-ranges.ts.
These client-side guardrails do not replace the server's validation.
"""

import hashlib
import math

# type: (canonical unit, inclusive minimum, inclusive maximum)
METRICS = {
    "WEIGHT": ("kg", 1, 500),
    "BODY_FAT": ("%", 1, 80),
    "BODY_MASS_INDEX": ("kg/m²", 8, 70),
    "TOTAL_BODY_WATER": ("kg", 5, 100),
    "BONE_MASS": ("kg", 0.5, 8),
    "FAT_FREE_MASS": ("kg", 10, 250),
    "LEAN_BODY_MASS": ("kg", 10, 250),
    "FAT_MASS": ("kg", 0, 250),
    "MUSCLE_MASS": ("kg", 5, 200),
    "VISCERAL_FAT": ("rating", 0, 30),
    "BLOOD_PRESSURE_SYS": ("mmHg", 40, 300),
    "BLOOD_PRESSURE_DIA": ("mmHg", 20, 200),
    "PULSE": ("bpm", 20, 300),
    "RESTING_HEART_RATE": ("bpm", 25, 220),
    "HEART_RATE_VARIABILITY": ("ms", 1, 200),
    "OXYGEN_SATURATION": ("%", 50, 100),
    "BLOOD_GLUCOSE": ("mg/dL", 20, 800),
    "BODY_TEMPERATURE": ("celsius", 28, 45),
    "SKIN_TEMPERATURE": ("celsius", 20, 45),
    "RESPIRATORY_RATE": ("breaths/min", 3, 60),
    "VO2_MAX": ("mL/(kg·min)", 5, 100),
    "SLEEP_DURATION": ("minutes", 0, 1440),
    "ACTIVITY_STEPS": ("steps", 0, 200000),
    "ACTIVE_ENERGY_BURNED": ("kcal", 0, 10000),
    "WALKING_RUNNING_DISTANCE": ("m", 0, 200000),
}


def convert(value, unit, metric):
    """Convert only dimensionally valid units; never guess percent vs mass."""
    if isinstance(value, bool):
        raise ValueError("invalid_value")
    if not isinstance(value, (str, int, float)) or metric not in METRICS:
        raise ValueError("invalid_value")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError("invalid_value") from None
    target, low, high = METRICS[metric]
    aliases = {"°C": "celsius", "C": "celsius", "min": "minutes", "kg/m2": "kg/m²"}
    unit = aliases.get(unit, unit)
    if unit != target:
        factors = {
            ("lb", "kg"): 0.45359237,
            ("lbs", "kg"): 0.45359237,
            ("g", "kg"): 0.001,
            ("h", "minutes"): 60,
            ("s", "minutes"): 1 / 60,
            ("km", "m"): 1000,
            ("mi", "m"): 1609.344,
            ("kJ", "kcal"): 1 / 4.184,
            ("mmol/L", "mg/dL"): 18.0182,
        }
        if unit == "°F" and target == "celsius":
            value = (value - 32) * 5 / 9
        elif (unit, target) in factors:
            value *= factors[unit, target]
        else:
            raise ValueError("invalid_unit")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid_value")
    return value, target


def identity(profile_id, event_key, metric):
    """Bounded, non-personal stable identifier, independent of upload time."""
    digest = hashlib.sha256(f"{profile_id}:{event_key}:{metric}".encode()).hexdigest()
    return f"ha:{digest}"
