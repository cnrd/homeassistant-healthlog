"""Retry confirmed rejections, never ambiguous writes or server backoff."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_PROFILES


async def async_setup_entry(hass, entry, async_add_entities):
    added = set()

    @callback
    def add_profiles(subentry_ids):
        for subentry_id in subentry_ids:
            if subentry_id not in added and subentry_id in entry.subentries:
                async_add_entities(
                    [RetryButton(entry, subentry_id)], config_subentry_id=subentry_id
                )
                added.add(subentry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_PROFILES}_{entry.entry_id}", add_profiles)
    )
    add_profiles(entry.runtime_data.workers)


class RetryButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Retry delivery"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, subentry_id):
        self.entry = entry
        self.subentry_id = subentry_id
        subentry = entry.subentries[subentry_id]
        self._attr_unique_id = f"{subentry.data['profile_id']}_retry"
        self._attr_device_info = {
            "identifiers": {("healthlog", subentry.data["profile_id"])},
            "name": subentry.title,
            "manufacturer": "HealthLog",
            "model": "Measurement profile",
        }

    async def async_press(self):
        await self.entry.runtime_data.workers[self.subentry_id].retry()
