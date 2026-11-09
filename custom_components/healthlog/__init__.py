"""One HealthLog connection with independent measurement-profile subentries."""

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import UUID

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .api import API
from .const import DOMAIN, SIGNAL_PROFILES, SUBENTRY_PROFILE
from .importer import Importer
from .storage import Queue, async_remove_profile_queue, async_remove_unused_queues
from .transport import Transport

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
_LOGGER = logging.getLogger(__name__)


@dataclass
class Runtime:
    transport: Transport
    connection_data: dict
    workers: dict[str, Importer] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stopping: bool = False


type HealthLogEntry = ConfigEntry[Runtime]


def queue_id(entry: ConfigEntry, subentry: ConfigSubentry) -> str:
    """Immutable per-profile namespace; no credentials or source IDs in filenames."""
    profile_id = subentry.data["profile_id"]
    if (
        not isinstance(profile_id, str)
        or UUID(profile_id).hex != profile_id
        or subentry.unique_id != profile_id
    ):
        raise ValueError("Invalid measurement profile identity")
    return f"{entry.entry_id}.{profile_id}"


async def async_setup_entry(hass, entry: HealthLogEntry) -> bool:
    try:
        api = API(
            async_get_clientsession(hass),
            entry.data["url"],
            entry.data["token"],
            allow_insecure_http=entry.data.get("allow_insecure_http", False),
        )
        profiles = list(entry.get_subentries_of_type(SUBENTRY_PROFILE))
        for subentry in profiles:
            queue_id(entry, subentry)
    except ValueError, KeyError:
        raise ConfigEntryError(
            "Invalid HealthLog connection or profile settings; reconfigure the connection"
        ) from None
    runtime = entry.runtime_data = Runtime(Transport(api), dict(entry.data))
    try:
        # Never garbage-collect unattached queues on startup: HA owns profile
        # metadata and its config write can lag a durable capture after a crash.
        for subentry in profiles:
            queue = Queue(hass, queue_id(entry, subentry), subentry.data.get("queue_capacity", 500))
            await queue.load()
            runtime.workers[subentry.subentry_id] = Importer(
                hass, entry, subentry, runtime.transport, queue
            )
            for natural, external in queue.data["identities"].items():
                runtime.transport.identities.setdefault(natural, external)
    except OSError as err:
        raise ConfigEntryNotReady("HealthLog local storage unavailable") from err
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_entry))
    # A subentry may have been added while platforms were being loaded.
    await async_update_entry(hass, entry)
    for worker in runtime.workers.values():
        worker.start()
    return True


async def async_update_entry(hass, entry: HealthLogEntry) -> None:
    """Connection changes reload all; profile changes touch only that profile."""
    runtime = entry.runtime_data
    if runtime.connection_data != dict(entry.data):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    async with runtime.lock:
        if runtime.stopping:
            return
        profiles = {s.subentry_id: s for s in entry.get_subentries_of_type(SUBENTRY_PROFILE)}
        removed_ids = runtime.workers.keys() - profiles.keys()
        for removed in removed_ids:
            worker = runtime.workers.pop(removed)
            await worker.stop()
            await async_remove_profile_queue(hass, f"{entry.entry_id}.{worker.profile_id}")
        added = []
        try:
            for subentry_id, subentry in profiles.items():
                worker = runtime.workers.get(subentry_id)
                configuration = deepcopy(dict(subentry.data))
                storage_id = queue_id(entry, subentry)
                if worker and worker.configuration == configuration:
                    worker.profile_title = subentry.title
                    continue
                if worker:
                    if worker.profile_id != subentry.data["profile_id"]:
                        raise ValueError("Profile identity cannot change")
                    await worker.stop()
                    # Preserve the committed in-memory queue, including intent
                    # after a failed acknowledgement. Do not reinterpret it.
                    queue = worker.queue
                    queue.capacity = subentry.data.get("queue_capacity", 500)
                else:
                    queue = Queue(hass, storage_id, subentry.data.get("queue_capacity", 500))
                    await queue.load()
                current = entry.subentries.get(subentry_id)
                if current is None or dict(current.data) != configuration:
                    continue
                replacement = Importer(hass, entry, subentry, runtime.transport, queue)
                runtime.workers[subentry_id] = replacement
                for natural, external in queue.data["identities"].items():
                    runtime.transport.identities.setdefault(natural, external)
                if worker is None:
                    added.append(subentry_id)
                replacement.start()
                replacement.notify()
            for subentry in profiles.values():
                device = dr.async_get(hass).async_get_device_by_identifier(
                    (DOMAIN, subentry.data["profile_id"]), entry.entry_id
                )
                if device and device.name != subentry.title:
                    dr.async_get(hass).async_update_device(device.id, name=subentry.title)
        except OSError, ValueError, KeyError:
            _LOGGER.error("HealthLog profile update failed; reloading safely")
            hass.config_entries.async_schedule_reload(entry.entry_id)
            return
        if added:
            async_dispatcher_send(hass, f"{SIGNAL_PROFILES}_{entry.entry_id}", added)


async def async_unload_entry(hass, entry: HealthLogEntry) -> bool:
    runtime = entry.runtime_data
    async with runtime.lock:
        if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
            return False
        runtime.stopping = True
        await asyncio.gather(*(worker.stop() for worker in runtime.workers.values()))
    return True


async def async_remove_entry(hass, entry) -> None:
    """Explicit removal deletes all this connection's private profile queues."""
    for subentry in entry.get_subentries_of_type(SUBENTRY_PROFILE):
        try:
            storage_id = queue_id(entry, subentry)
        except ValueError, KeyError:
            continue  # The exact parent namespace scan still handles owned files.
        await Store(hass, 1, f"{DOMAIN}.{storage_id}.queue", private=True).async_remove()
    await async_remove_unused_queues(hass, entry.entry_id, set())
