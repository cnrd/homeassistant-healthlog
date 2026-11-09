"""Durable bounded queue; mutate memory only after the write succeeds."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN


async def async_remove_profile_queue(hass, storage_id: str) -> None:
    """Explicit deletion of one stopped profile, including its corrupt copies."""
    key = f"{DOMAIN}.{storage_id}.queue"
    await Store(hass, 1, key, private=True).async_remove()
    directory = Path(hass.config.path(".storage"))
    corrupt = await hass.async_add_executor_job(lambda: list(directory.glob(f"{key}.corrupt.*")))
    for path in corrupt:
        await hass.async_add_executor_job(path.unlink, True)


async def async_remove_unused_queues(hass, entry_id: str, active_ids: set[str]) -> None:
    """Explicit parent removal only; never use as automatic garbage collection."""
    directory = Path(hass.config.path(".storage"))
    paths = await hass.async_add_executor_job(
        lambda: [
            *directory.glob(f"{DOMAIN}.{entry_id}.*.queue"),
            *directory.glob(f"{DOMAIN}.{entry_id}.*.queue.corrupt.*"),
        ]
    )
    active_keys = {f"{DOMAIN}.{identifier}.queue" for identifier in active_ids}
    removed = set()
    for path in paths:
        key = path.name.split(".corrupt.", 1)[0]
        profile_id = key.removeprefix(f"{DOMAIN}.{entry_id}.").removesuffix(".queue")
        try:
            if UUID(profile_id).hex != profile_id:
                continue
        except ValueError:
            continue
        if key in active_keys:
            continue
        if key not in removed:
            await Store(hass, 1, key, private=True).async_remove()
            removed.add(key)
        if ".corrupt." in path.name:
            await hass.async_add_executor_job(path.unlink, True)


class DurableStore(Store):
    """HA Store logs some write errors instead of propagating them.

    With atomic writes, a failed replacement leaves the previous complete file.
    Read it back through Store after cache invalidation performed by async_save.
    Never acknowledge deferred shutdown saves as durable.
    """

    def __init__(self, hass, version, key, **kwargs):
        super().__init__(hass, version, key, atomic_writes=True, **kwargs)

    async def async_save(self, data):
        if self.hass.state is CoreState.stopping:
            raise OSError("Storage cannot be acknowledged during shutdown")
        await super().async_save(data)
        if await self.async_load() != data:
            raise OSError("Storage read-back did not match the requested write")


class QueueFull(Exception):
    """No event is silently evicted to admit a newer event."""


class Queue:
    """Store immutable payloads and pending captures, not mapping references."""

    def __init__(self, hass: HomeAssistant, entry_id: str, capacity: int = 500) -> None:
        self.store = DurableStore(hass, 1, f"{DOMAIN}.{entry_id}.queue", private=True)
        self.capacity = capacity
        self.lock = asyncio.Lock()
        self.data: dict[str, Any] = {
            "events": [],
            "seen": [],
            "identities": {},
            "last_success": None,
            "error": None,
            "next_attempt": 0,
            "failures": 0,
            "rejected_readings": 0,
        }

    async def load(self) -> None:
        def damaged_store():
            path = Path(self.store.path)
            return any(path.parent.glob(f"{path.name}.corrupt.*"))

        # HA retains corrupt files but otherwise starts empty. Delivery must not
        # silently forget identity/intent: require a backup repair first.
        if await self.store.hass.async_add_executor_job(damaged_store):
            raise OSError("HealthLog queue requires storage repair")
        loaded = await self.store.async_load()
        if loaded is None:
            if await self.store.hass.async_add_executor_job(damaged_store):
                raise OSError("HealthLog queue requires storage repair")
            return
        try:
            if (
                not isinstance(loaded, dict)
                or not isinstance(loaded["events"], list)
                or not isinstance(loaded["seen"], list)
                or not isinstance(loaded.get("identities"), dict)
            ):
                raise ValueError
            for event in loaded["events"]:
                if not isinstance(event["key"], str) or type(event["ready"]) is not bool:
                    raise ValueError
                if event["ready"]:
                    for item in event["payload"]:
                        if not all(
                            key in item
                            for key in ("type", "value", "unit", "measuredAt", "externalId")
                        ):
                            raise ValueError
                elif not all(
                    key in event
                    for key in (
                        "mappings",
                        "snapshots",
                        "received",
                        "due",
                        "freshness",
                        "description",
                        "measured_at",
                    )
                ):
                    raise ValueError
            if any(not isinstance(key, str) for key in loaded["seen"]):
                raise ValueError
        except ValueError, KeyError, TypeError:
            raise OSError("HealthLog queue schema invalid; restore a backup") from None
        self.data = {**self.data, **loaded}

    async def change[Result](self, mutate: Callable[[dict[str, Any]], Result]) -> Result:
        async with self.lock:
            data = deepcopy(self.data)
            result = mutate(data)

            async def commit():
                await self.store.async_save(data)
                self.data = data

            # Atomic replacement runs in HA's executor. Cancellation must not
            # release the lock/unload the entry while that thread can still
            # replace the file underneath a new instance of the queue.
            task = self.store.hass.async_create_task(commit(), "HealthLog queue commit")
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            return result

    @staticmethod
    def item(data: dict[str, Any], target: str) -> dict[str, Any]:
        return next(
            item
            for event in data["events"]
            for item in event.get("payload", [])
            if item["externalId"] == target
        )

    async def hold(self, target: str, reason: str) -> None:
        def mutate(data):
            item = self.item(data, target)
            item.pop("inflight", None)
            item["hold"] = reason
            data["error"] = reason

        await self.change(mutate)

    async def release(self, target: str, error: str | None = None) -> None:
        def mutate(data):
            self.item(data, target).pop("inflight", None)
            data["error"] = error

        await self.change(mutate)

    async def acknowledge(self, target: str) -> None:
        def mutate(data):
            for event in data["events"]:
                if event["ready"]:
                    event["payload"] = [m for m in event["payload"] if m["externalId"] != target]
            completed = [e for e in data["events"] if e["ready"] and not e["payload"]]
            # Do not expire delivered identities: the ordinary endpoint has no
            # permanent external-id guard if a source replays an old event.
            data["seen"] = data["seen"] + [e["key"] for e in completed]
            data["events"] = [e for e in data["events"] if e not in completed]
            data.update(last_success=datetime.now(UTC).isoformat(), failures=0)
            data["error"] = next(
                (m["hold"] for e in data["events"] for m in e.get("payload", []) if m.get("hold")),
                None,
            )

        await self.change(mutate)

    async def add(self, event: dict[str, Any]) -> bool:
        def mutate(data):
            if event["key"] in data["seen"] or any(
                e["key"] == event["key"] for e in data["events"]
            ):
                return False
            if len(data["events"]) >= self.capacity:
                raise QueueFull
            data["events"].append(event)
            return True

        return await self.change(mutate)
