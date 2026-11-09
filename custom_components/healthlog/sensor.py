"""One diagnostic sensor with non-biometric operational attributes."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL, SIGNAL_PROFILES


async def async_setup_entry(hass, entry, async_add_entities):
    added = set()

    @callback
    def add_profiles(subentry_ids):
        for subentry_id in subentry_ids:
            if subentry_id not in added and subentry_id in entry.subentries:
                async_add_entities(
                    [StatusSensor(entry, subentry_id)], config_subentry_id=subentry_id
                )
                added.add(subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_PROFILES}_{entry.entry_id}", add_profiles)
    )
    add_profiles(entry.runtime_data.workers)


class StatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Delivery status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry, subentry_id):
        self.entry = entry
        self.subentry_id = subentry_id
        subentry = entry.subentries[subentry_id]
        self._attr_unique_id = f"{subentry.data['profile_id']}_delivery"
        self._attr_device_info = {
            "identifiers": {("healthlog", subentry.data["profile_id"])},
            "name": subentry.title,
            "manufacturer": "HealthLog",
            "model": "Measurement profile",
        }

    async def async_added_to_hass(self):
        for signal in (
            f"{SIGNAL}_{self.entry.entry_id}_{self.subentry_id}",
            f"{SIGNAL}_{self.entry.entry_id}",
        ):
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal, self.async_write_ha_state)
            )

    @property
    def available(self):
        return (
            self.subentry_id in self.entry.runtime_data.workers
            and not self.entry.runtime_data.stopping
        )

    @property
    def native_value(self):
        worker = self.entry.runtime_data.workers.get(self.subentry_id)
        if worker is None:
            return None
        status = worker.status()
        if (
            status["last_error"]
            or status["ambiguous_measurements"]
            or status["authentication"] == "reauth_required"
        ):
            return "attention_required"
        return "pending" if status["queued_batches"] else "ready"

    @property
    def extra_state_attributes(self):
        worker = self.entry.runtime_data.workers.get(self.subentry_id)
        return worker.status() if worker else {}
