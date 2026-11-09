"""Canonical safety, precision, ranges, and independent event identities."""

import math

import pytest

from custom_components.healthlog.models import METRICS, convert, identity


@pytest.mark.parametrize("metric", METRICS)
def test_all_metrics_boundaries(metric):
    unit, low, high = METRICS[metric]
    assert convert(low, unit, metric) == (low, unit)
    assert convert(high, unit, metric) == (high, unit)
    for value in (low - 0.001, high + 0.001):
        with pytest.raises(ValueError):
            convert(value, unit, metric)


@pytest.mark.parametrize(
    "value",
    [True, False, None, [], {}, "nan", "inf", float("nan"), float("inf"), "unknown", "unavailable"],
)
def test_invalid_numbers(value):
    with pytest.raises((ValueError, TypeError)):
        convert(value, "kg", "WEIGHT")


@pytest.mark.parametrize(
    "value,unit,metric,expected",
    [
        (100, "lb", "WEIGHT", 45.359237),
        (60000, "g", "WEIGHT", 60),
        (98.6, "°F", "BODY_TEMPERATURE", 37),
        (5, "mmol/L", "BLOOD_GLUCOSE", 90.091),
        (1, "h", "SLEEP_DURATION", 60),
        (60, "s", "SLEEP_DURATION", 1),
        (1, "mi", "WALKING_RUNNING_DISTANCE", 1609.344),
        (418.4, "kJ", "ACTIVE_ENERGY_BURNED", 100),
    ],
)
def test_conversions(value, unit, metric, expected):
    assert math.isclose(convert(value, unit, metric)[0], expected)


@pytest.mark.parametrize(
    "metric,unit",
    [("TOTAL_BODY_WATER", "%"), ("WEIGHT", "m"), ("BLOOD_GLUCOSE", "kg"), ("VISCERAL_FAT", "%")],
)
def test_incompatible_units(metric, unit):
    with pytest.raises(ValueError):
        convert(40, unit, metric)


def test_precision_and_identity():
    assert convert("60.123456789", "kg", "WEIGHT")[0] == 60.123456789
    assert identity("a", "one", "WEIGHT") == identity("a", "one", "WEIGHT")
    assert (
        len(
            {
                identity(profile, event, metric)
                for profile in ("a", "b")
                for event in ("one", "two")
                for metric in ("WEIGHT", "BODY_FAT")
            }
        )
        == 8
    )
