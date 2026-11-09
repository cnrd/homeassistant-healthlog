"""HA states, detached templates, dynamic dependencies, and pending recovery."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, State

from custom_components.healthlog import queue_id
from custom_components.healthlog.collector import Collector
from custom_components.healthlog.importer import Importer
from custom_components.healthlog.storage import Queue


def trigger(worker, value, old_value="2026-01-01T00:00:00Z", restored=False):
    return Event(
        EVENT_STATE_CHANGED,
        {
            "entity_id": worker.options["trigger_entity"],
            "old_state": State(worker.options["trigger_entity"], old_value) if old_value else None,
            "new_state": State(worker.options["trigger_entity"], value, {"restored": restored}),
        },
    )


async def test_state_and_attribute_mapping(hass):
    collector = Collector(hass)
    hass.states.async_set("sensor.scale", "60.123", {"weight": 61.25, "unit_of_measurement": "kg"})
    state = hass.states.get("sensor.scale")
    assert collector.snapshot({}, state)["value"] == "60.123"
    assert collector.snapshot({"attribute": "weight"}, state)["value"] == 61.25
    assert collector.snapshot({"attribute": "missing"}, state) is None
    assert collector.snapshot({}, None) is None


@pytest.mark.parametrize("value", ["unknown", "unavailable"])
async def test_unavailable(hass, value):
    assert Collector(hass).snapshot({}, State("sensor.scale", value, {"weight": 60})) is None


async def test_template_detached_source_and_additional_dependency(hass):
    hass.states.async_set("sensor.scale", "ready", {"weight": 60})
    hass.states.async_set("sensor.offset", "1")
    collector = Collector(hass)
    mapping = {
        "entity_id": "sensor.scale",
        "unit": "kg",
        "value_template": "{{ source.attributes.weight + states('sensor.offset') | float }}",
    }
    snapshot = collector.snapshot(mapping)
    assert snapshot["value"] == 61
    assert snapshot["template_entities"] == ["sensor.offset", "sensor.scale"]
    assert collector.depends_on(mapping, snapshot, "sensor.offset")
    assert not collector.depends_on(mapping, snapshot, "sensor.unrelated")
    hass.states.async_set("sensor.scale", "ready", {"weight": 62})
    assert snapshot["value"] == 61


async def test_template_only_freshness_and_dynamic_dependency(hass, freezer):
    collector = Collector(hass)
    hass.states.async_set("sensor.choice", "a")
    hass.states.async_set("sensor.a", "60")
    freezer.tick(20)
    hass.states.async_set("sensor.b", "61")
    mapping = {
        "unit": "kg",
        "value_template": "{{ states('sensor.' ~ states('sensor.choice')) | float }}",
    }
    snapshot = collector.snapshot(mapping)
    assert snapshot["value"] == 60
    assert snapshot["updated"] == hass.states.get("sensor.a").last_updated.timestamp()
    hass.states.async_set("sensor.choice", "b")
    new = collector.snapshot(mapping)
    assert collector.depends_on(mapping, new, "sensor.b")
    assert not collector.depends_on(mapping, new, "sensor.a")
    constant = collector.snapshot({"unit": "kg", "value_template": "{{ 60 }}"})
    assert constant["updated"] == 0


async def test_invalid_template_preserves_dependency_metadata(hass):
    collector = Collector(hass)
    mapping = {"unit": "kg", "value_template": "{{ states('sensor.missing') | float }}"}
    snapshot = collector.snapshot(mapping)
    assert snapshot["value"] is None
    assert collector.depends_on(mapping, snapshot, "sensor.missing")
    hass.states.async_set("sensor.missing", "60")
    assert collector.snapshot(mapping)["value"] == 60


async def test_pending_capture_late_values_freeze_and_reload(worker, freezer):
    worker.hass.states.async_set("sensor.weight", "60", {"unit_of_measurement": "kg"})
    stamp = datetime.now(UTC).isoformat()
    event = trigger(worker, stamp)
    await worker.capture(event)
    assert len(worker.queue.data["events"]) == 1
    assert not worker.queue.data["events"][0]["ready"]
    freezer.tick(2)
    worker.hass.states.async_set("sensor.weight", "61", {"unit_of_measurement": "kg"})
    await worker.capture(
        Event(
            EVENT_STATE_CHANGED,
            {"entity_id": "sensor.weight", "new_state": worker.hass.states.get("sensor.weight")},
        )
    )
    freezer.tick(20)
    await worker.freeze()
    payload = worker.queue.data["events"][0]["payload"][0]
    assert payload["value"] == 61
    assert payload["measuredAt"] == stamp
    worker.hass.states.async_set("sensor.weight", "62")
    await worker.capture(event)  # Duplicate identity never replaces frozen data.
    assert worker.queue.data["events"][0]["payload"][0] == payload
    reloaded = Queue(worker.hass, queue_id(worker.entry, worker.subentry))
    await reloaded.load()
    assert reloaded.data == worker.queue.data


async def test_pending_restart_uses_persisted_snapshots_not_live(worker, freezer):
    worker.hass.states.async_set("sensor.weight", "60", {"unit_of_measurement": "kg"})
    await worker.capture(trigger(worker, datetime.now(UTC).isoformat()))
    queue = Queue(worker.hass, queue_id(worker.entry, worker.subentry))
    await queue.load()
    worker.hass.states.async_set("sensor.weight", "65")
    restarted = Importer(worker.hass, worker.entry, worker.subentry, worker.transport, queue)
    await restarted.freeze()
    assert queue.data["events"][0]["payload"][0]["value"] == 60


@pytest.mark.parametrize("old_value,restored", [(None, False), ("2026-01-01T00:00:00Z", True)])
async def test_startup_restored_not_measurement(worker, old_value, restored):
    await worker.capture(trigger(worker, datetime.now(UTC).isoformat(), old_value, restored))
    assert worker.queue.data["events"] == []


async def test_duplicate_updates_not_new_event(worker):
    stamp = datetime.now(UTC).isoformat()
    await worker.capture(trigger(worker, stamp, stamp))
    assert worker.queue.data["events"] == []


async def test_event_id_mode_timestamp_and_attribute(worker):
    worker.options.update(trigger_mode="event_id", trigger_attribute="event")
    state = State("sensor.measurement_time", "ready", {"event": "opaque-unique-event"})
    key, stamp = worker.event_identity(state)
    assert key == "opaque-unique-event"
    assert stamp == state.last_updated.isoformat()


@pytest.mark.parametrize(
    "value",
    ["unavailable", "", "2026-01-01", "1899-01-01T00:00:00Z", "9999-01-01T00:00:00Z", "not-a-date"],
)
async def test_bad_trigger_identity(worker, value):
    with pytest.raises(ValueError):
        worker.event_identity(State("sensor.measurement_time", value))


@pytest.mark.parametrize("value,unit", [("nan", "kg"), ("6000", "kg"), ("60", "%"), (True, "kg")])
async def test_bad_readings_counted_not_uploaded(worker, freezer, value, unit):
    worker.hass.states.async_set("sensor.weight", "ready", {"reading": value})
    worker.options["mappings"] = [
        {"type": "WEIGHT", "entity_id": "sensor.weight", "attribute": "reading", "unit": unit}
    ]
    await worker.capture(trigger(worker, datetime.now(UTC).isoformat()))
    freezer.tick(30)
    await worker.freeze()
    assert not worker.queue.data["events"]
    assert worker.status()["rejected_readings"] == 1
    worker.transport.deliver.assert_not_called()


async def test_stale_reading_rejected(worker, freezer):
    worker.hass.states.async_set("sensor.weight", "60", {"unit_of_measurement": "kg"})
    freezer.tick(601)
    await worker.capture(trigger(worker, datetime.now(UTC).isoformat()))
    freezer.tick(30)
    await worker.freeze()
    assert worker.status()["rejected_readings"] == 1


async def test_capture_storage_failure_visible(worker):
    with patch.object(worker.queue.store, "async_save", side_effect=OSError):
        await worker.capture(trigger(worker, datetime.now(UTC).isoformat()))
    assert worker.status()["last_error"] == "storage_error"
    assert not worker.queue.data["events"]


async def test_restored_source_rejected(hass):
    snapshot = Collector(hass).snapshot(
        {}, State("sensor.scale", "60", {"restored": True, "unit_of_measurement": "kg"})
    )
    assert snapshot is None


async def test_glucose_context_and_notes_frozen(worker, freezer):
    worker.hass.states.async_set("sensor.glucose", "100.125", {"unit_of_measurement": "mg/dL"})
    worker.options["description"] = "Synthetic meter"
    worker.options["mappings"] = [
        {
            "type": "BLOOD_GLUCOSE",
            "entity_id": "sensor.glucose",
            "unit": "mg/dL",
            "glucose_context": "FASTING",
        }
    ]
    await worker.capture(trigger(worker, datetime.now(UTC).isoformat()))
    freezer.tick(30)
    await worker.freeze()
    item = worker.queue.data["events"][0]["payload"][0]
    assert item["glucoseContext"] == "FASTING"
    assert item["notes"] == "Synthetic meter"
    assert item["value"] == 100.125


async def test_template_late_dependency_change_during_settling(worker, freezer):
    worker.options["mappings"] = [
        {
            "type": "WEIGHT",
            "unit": "kg",
            "value_template": "{{ states('sensor.late_mass') | float }}",
        }
    ]
    await worker.capture(trigger(worker, datetime.now(UTC).isoformat()))
    freezer.tick(2)
    worker.hass.states.async_set("sensor.late_mass", "60.25")
    await worker.capture(
        Event(
            EVENT_STATE_CHANGED,
            {
                "entity_id": "sensor.late_mass",
                "new_state": worker.hass.states.get("sensor.late_mass"),
            },
        )
    )
    freezer.tick(30)
    await worker.freeze()
    assert worker.queue.data["events"][0]["payload"][0]["value"] == 60.25
