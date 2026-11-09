"""Exact endpoint schemas and acknowledgement rules, separate from scheduling."""

import asyncio
import math

from homeassistant.util.dt import parse_datetime

from .api import API, APIError

# Only exact HealthKit -> canonical mappings. Never alias different clinical types.
# Sleep is deliberately absent: a duration alone cannot supply genuine interval dates.
BATCH_TYPES = {
    "WEIGHT": ("BodyMass", "kg"),
    "BODY_FAT": ("BodyFatPercentage", "%"),
    "BODY_MASS_INDEX": ("BodyMassIndex", "count"),
    "LEAN_BODY_MASS": ("LeanBodyMass", "kg"),
    "BLOOD_PRESSURE_SYS": ("BloodPressureSystolic", "mmHg"),
    "BLOOD_PRESSURE_DIA": ("BloodPressureDiastolic", "mmHg"),
    "PULSE": ("HeartRate", "count/min"),
    "RESTING_HEART_RATE": ("RestingHeartRate", "count/min"),
    "HEART_RATE_VARIABILITY": ("HeartRateVariabilitySDNN", "ms"),
    "OXYGEN_SATURATION": ("OxygenSaturation", "fraction"),
    "BODY_TEMPERATURE": ("BodyTemperature", "degC"),
    "RESPIRATORY_RATE": ("RespiratoryRate", "count/min"),
    "VO2_MAX": ("VO2Max", "mL/(kg*min)"),
    "ACTIVITY_STEPS": ("StepCount", "count"),
    "ACTIVE_ENERGY_BURNED": ("ActiveEnergyBurned", "kcal"),
    "WALKING_RUNNING_DISTANCE": ("DistanceWalkingRunning", "m"),
}


def durable_identity(item: dict) -> bool:
    """Notes and clinical context are not accepted by the HealthKit batch schema."""
    return item["type"] in BATCH_TYPES and not item.get("notes")


class IdentityConflict(APIError):
    """The server reconciled a timestamp collision instead of an immutable insert."""


class Transport:
    """One metric per request isolates rejections and ambiguous outcomes."""

    def __init__(self, api: API) -> None:
        self.api = api
        self.auth_required = False
        # Protect identity reservations only, never serialize HTTP requests.
        # Reconstructed from each profile's durable identity ledger on setup.
        self.identity_lock = asyncio.Lock()
        self.identities: dict[str, str] = {}

    async def deliver(self, item: dict) -> None:
        key = item["externalId"]
        if durable_identity(item):
            suffix, unit = BATCH_TYPES[item["type"]]
            value = item["value"]
            if item["type"] in ("BODY_FAT", "OXYGEN_SATURATION"):
                value /= 100
            data = await self.api.post(
                "/api/measurements/batch",
                {
                    "entries": [
                        {
                            "hkIdentifier": f"HKQuantityTypeIdentifier{suffix}",
                            "value": value,
                            "unit": unit,
                            "startDate": item["measuredAt"],
                            "endDate": item["measuredAt"],
                            "externalId": key,
                        }
                    ]
                },
                key,
            )
            results = data.get("entries")
            if (
                not isinstance(results, list)
                or len(results) != 1
                or not isinstance(results[0], dict)
                or type(results[0].get("index")) is not int
                or results[0]["index"] != 0
            ):
                raise APIError(200)
            status = results[0].get("status")
            if status in ("inserted", "duplicate"):
                return
            if status == "updated":
                raise IdentityConflict(409)
            # Our immutable, non-stats identities must never produce an update.
            raise APIError(422 if status == "skipped" else 200)
        payload = {
            key: item[key]
            for key in ("type", "value", "measuredAt", "notes", "glucoseContext")
            if key in item
        }
        data = await self.api.post("/api/measurements", payload, key)
        # A 2xx alone is not proof: require the actual created/replayed row.
        value = data.get("value")
        expected = parse_datetime(item["measuredAt"])
        # HealthLog stores JS Date/PostgreSQL timestamps at millisecond precision.
        # Keep the original timestamp in the queue/request; compare its stored form.
        expected = expected.replace(microsecond=expected.microsecond // 1000 * 1000)
        if (
            not isinstance(data.get("id"), str)
            or not data["id"]
            or data.get("source") != "EXTERNAL"
            or data.get("type") != item["type"]
            or data.get("unit") != item["unit"]
            or ("glucoseContext" in item and data.get("glucoseContext") != item["glucoseContext"])
            or ("notes" in item and data.get("notes") != item["notes"])
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value != item["value"]
            or not isinstance(data.get("measuredAt"), str)
            or parse_datetime(data["measuredAt"]) != expected
        ):
            raise APIError(200)
