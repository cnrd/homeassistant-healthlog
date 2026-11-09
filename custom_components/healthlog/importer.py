"""Capture orchestration and a durable, per-profile delivery worker."""

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.dt import parse_datetime

from .api import APIError, AuthError
from .collector import Collector
from .const import DEFAULTS, SIGNAL
from .models import convert, identity
from .storage import Queue, QueueFull
from .transport import IdentityConflict, Transport, durable_identity

_LOGGER = logging.getLogger(__name__)


def now() -> float:
    return datetime.now(UTC).timestamp()


class Importer:
    """All payloads are frozen before upload; options never reinterpret the queue."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        transport: Transport,
        queue: Queue,
    ) -> None:
        self.hass, self.entry, self.transport, self.queue = (
            hass,
            entry,
            transport,
            queue,
        )
        self.subentry = subentry
        self.configuration = deepcopy(dict(subentry.data))
        self.profile_title = subentry.title
        self.profile_id = subentry.data["profile_id"]
        self.options = {**DEFAULTS, **self.configuration}
        self.signal = f"{SIGNAL}_{entry.entry_id}_{subentry.subentry_id}"
        self.wake = asyncio.Event()
        self.capture_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[None]] = set()
        self.worker: asyncio.Task[None] | None = None
        self.unsubscribe: Callable[[], None] | None = None
        self.auth_required = False
        self.awaiting_baseline = False
        self.storage_error = False
        self.collector = Collector(hass)
        self.recovered_keys = {event["key"] for event in queue.data["events"] if not event["ready"]}

    @callback
    def notify(self):
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def start(self):
        if self.worker and not self.worker.done():
            return
        state = self.hass.states.get(self.options["trigger_entity"])
        try:
            if state is None or state.attributes.get("restored"):
                raise ValueError
            self.event_identity(state)
        except ValueError:
            # The first valid state of a late-loading entity is a baseline, not
            # evidence of a new measurement that occurred after subscription.
            self.awaiting_baseline = True
        # Filter synchronously before creating tasks. Template dependencies can
        # change dynamically, and old pending events retain their own mappings.
        self.unsubscribe = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self.on_state)
        self.worker = self.entry.async_create_background_task(
            self.hass, self.run(), "HealthLog delivery"
        )

    async def stop(self):
        if self.unsubscribe:
            self.unsubscribe()
            self.unsubscribe = None
        tasks = [*self.tasks, *([self.worker] if self.worker else [])]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @callback
    def matches_pending(self, entity_id):
        return any(
            not pending["ready"]
            and pending["key"] not in self.recovered_keys
            and pending["due"] >= now()
            and any(
                self.collector.depends_on(mapping, snapshot, entity_id)
                for mapping, snapshot in zip(pending["mappings"], pending["snapshots"], strict=True)
            )
            for pending in self.queue.data["events"]
        )

    @callback
    def on_state(self, event):
        entity_id = event.data["entity_id"]
        # A capture's new dependency set is not visible in the committed queue
        # until its disk write completes. Do not miss source changes in that gap.
        if (
            entity_id != self.options["trigger_entity"]
            and not self.capture_lock.locked()
            and not self.matches_pending(entity_id)
        ):
            return
        task = self.entry.async_create_background_task(
            self.hass, self.capture(event), "HealthLog capture"
        )
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def snapshot(self, mapping, state):
        return self.collector.snapshot(mapping, state)

    def event_identity(self, state: State) -> tuple[str, str]:
        attribute = self.options["trigger_attribute"]
        value = state.attributes.get(attribute) if attribute else state.state
        if value is None or str(value) in ("unknown", "unavailable", "") or len(str(value)) > 512:
            raise ValueError("invalid_trigger")
        if self.options["trigger_mode"] == "timestamp":
            stamp = parse_datetime(str(value))
            if stamp is None or stamp.tzinfo is None:
                raise ValueError("invalid_trigger")
            stamp = stamp.astimezone(UTC)
            if stamp.year < 1900 or stamp.timestamp() > now() + 300:
                raise ValueError("invalid_trigger")
            key = stamp.isoformat()
        else:
            # Explicit opaque stable source ID; receipt time is captured just once.
            key = str(value)
            stamp = state.last_updated.astimezone(UTC)
        return key, stamp.isoformat()

    async def capture(self, event: Event) -> None:
        async with self.capture_lock:
            if event.data["entity_id"] != self.options[
                "trigger_entity"
            ] and not self.matches_pending(event.data["entity_id"]):
                return
            state = event.data.get("new_state")
            if state is None and event.data["entity_id"] == self.options["trigger_entity"]:
                return
            try:
                if event.data["entity_id"] == self.options["trigger_entity"]:
                    # Ignore initialization/restoration and attribute-only notifications
                    # whose source identity has not changed.
                    old = event.data.get("old_state")
                    if not state.attributes.get("restored") and (
                        old is None or old.attributes.get("restored") or self.awaiting_baseline
                    ):
                        self.event_identity(state)
                        self.awaiting_baseline = False
                        return
                    if old is not None and not state.attributes.get("restored"):
                        key, stamp = self.event_identity(state)
                        try:
                            old_key, _ = self.event_identity(old)
                        except ValueError:
                            old_key = None
                        if key != old_key:
                            received = now()
                            mappings = deepcopy(self.options["mappings"])
                            await self.queue.add(
                                {
                                    "key": key,
                                    "measured_at": stamp,
                                    "received": received,
                                    "due": received + self.options["settling_delay"],
                                    "freshness": self.options["freshness_window"],
                                    "description": self.options["description"],
                                    "mappings": mappings,
                                    "snapshots": [
                                        self.snapshot(
                                            m,
                                            self.hass.states.get(m["entity_id"])
                                            if m.get("entity_id")
                                            else None,
                                        )
                                        for m in mappings
                                    ],
                                    "ready": False,
                                }
                            )

                def update(data):
                    for pending in data["events"]:
                        if (
                            pending["ready"]
                            or pending["due"] < now()
                            or pending["key"] in self.recovered_keys
                        ):
                            continue
                        for index, mapping in enumerate(pending["mappings"]):
                            if self.collector.depends_on(
                                mapping,
                                pending["snapshots"][index],
                                event.data["entity_id"],
                            ):
                                pending["snapshots"][index] = self.snapshot(mapping, state)

                await self.queue.change(update)
            except (ValueError, QueueFull) as err:
                code = "queue_full" if isinstance(err, QueueFull) else "invalid_trigger"
                try:
                    await self.queue.change(lambda data: data.update(error=code))
                except OSError:
                    self.storage_error = True
                _LOGGER.warning("HealthLog capture requires attention: %s", code)
            except OSError:
                self.storage_error = True
                _LOGGER.error("HealthLog queue storage failed; event was not acknowledged")
            self.wake.set()
            self.notify()

    async def freeze(self) -> None:
        def mutate(data):
            for event in data["events"]:
                if event["ready"] or (
                    event["due"] > now() and event["key"] not in self.recovered_keys
                ):
                    continue
                payload = []
                invalid = 0
                for mapping, snapshot in zip(event["mappings"], event["snapshots"], strict=True):
                    try:
                        if (
                            not snapshot
                            or snapshot["updated"] < event["received"] - event["freshness"]
                        ):
                            raise ValueError
                        value, unit = convert(snapshot["value"], snapshot["unit"], mapping["type"])
                        item = {
                            "type": mapping["type"],
                            "value": value,
                            "unit": unit,
                            "measuredAt": event["measured_at"],
                            "externalId": identity(
                                self.profile_id,
                                event["key"],
                                mapping["type"],
                            ),
                        }
                        if event["description"]:
                            item["notes"] = event["description"]
                        if mapping["type"] == "BLOOD_GLUCOSE":
                            context = mapping.get("glucose_context")
                            if context not in ("FASTING", "POSTPRANDIAL", "RANDOM", "BEDTIME"):
                                raise ValueError("missing_glucose_context")
                            item["glucoseContext"] = context
                        payload.append(item)
                    except ValueError, TypeError:
                        invalid += 1
                event.update(ready=True, payload=payload, rejected=invalid)
                if invalid:
                    data["error"] = "invalid_sensor"
                    data["rejected_readings"] += invalid
                elif data["error"] == "invalid_sensor":
                    data["error"] = None
                # Captures are no longer needed; avoid retaining duplicate health data.
                event.pop("snapshots")
                event.pop("mappings")
            # Invalid local readings never became importable measurements.
            # Keep their count/error, not empty captures that exhaust capacity.
            empty = [e for e in data["events"] if e["ready"] and not e["payload"]]
            data["seen"] = data["seen"] + [e["key"] for e in empty]
            data["events"] = [e for e in data["events"] if e not in empty]

        await self.queue.change(mutate)

    async def deliver(self) -> float:
        async with self.delivery_lock:
            return await self._deliver()

    async def _deliver(self) -> float:
        """Persist intent before HTTP. A single-route uncertain write is never replayed."""
        if self.auth_required or self.transport.auth_required or self.queue.data.get("paused"):
            return 3600
        delay = self.queue.data["next_attempt"] - now()
        if delay > 0:
            return delay
        candidate = next(
            (
                item
                for event in self.queue.data["events"]
                if event["ready"]
                for item in event["payload"]
                if not item.get("hold")
            ),
            None,
        )
        if candidate is None:
            return 3600
        target = candidate["externalId"]
        durable = durable_identity(candidate)
        # A previous HTTP call or acknowledgement may have been interrupted.
        if candidate.get("inflight") and not durable:
            await self.queue.hold(target, "ambiguous")
            return 1

        def reserve(data):
            item = self.queue.item(data, target)
            stamp = parse_datetime(item["measuredAt"]).astimezone(UTC)
            stamp = stamp.replace(microsecond=stamp.microsecond // 1000 * 1000)
            natural_key = f"{item['type']}:{stamp.isoformat()}"
            claims = (
                data["identities"].get(natural_key),
                self.transport.identities.get(natural_key),
            )
            if any(claimed is not None and claimed != target for claimed in claims):
                item["hold"] = "identity_conflict"
                data["error"] = "identity_conflict"
                return False
            data["identities"][natural_key] = target
            item["inflight"] = True
            data["next_attempt"] = now() + max(2, self.options["batch_delay"])
            return True

        async with self.transport.identity_lock:
            try:
                reserved = await self.queue.change(reserve)
            finally:
                # Queue.change waits for its commit even on cancellation. Keep
                # peer reservations consistent with that durable outcome.
                for natural, external in self.queue.data["identities"].items():
                    self.transport.identities.setdefault(natural, external)
            if not reserved:
                return 1
        try:
            await self.transport.deliver(candidate)
        except IdentityConflict:
            await self.queue.hold(target, "identity_conflict")
            return 1
        except AuthError:
            self.auth_required = True
            self.transport.auth_required = True
            self.entry.async_start_reauth(self.hass)
            async_dispatcher_send(self.hass, f"{SIGNAL}_{self.entry.entry_id}")
            await self.queue.release(target, error="reauth_required")
            return 3600
        except APIError as err:
            # Only explicit pre-write refusals prove this single-route call did
            # not commit. A 409 cannot prove that OUR value already exists.
            refused = err.status in (400, 401, 403, 404, 413, 422, 429)
            if not durable and not refused:
                await self.queue.hold(target, "ambiguous")
            elif err.status in (400, 409, 422):
                await self.queue.hold(target, "rejected")
            else:
                await self.queue.release(
                    target, error=f"http_{err.status}" if err.status else "cannot_connect"
                )

            def failed(data, err=err):
                data["failures"] += 1
                data["next_attempt"] = now() + max(
                    60 * 2 ** min(data["failures"], 6), err.retry_after
                )
                if err.status in (403, 404, 413) or 300 <= err.status < 400:
                    data["paused"] = True

            await self.queue.change(failed)
            return max(1, self.queue.data["next_attempt"] - now())
        # If this write fails, intent stays durable. Ordinary-route records will
        # be held, even in this process, rather than silently sent again.
        await self.queue.acknowledge(target)
        return max(2, self.options["batch_delay"])

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                await self.freeze()
                delay = await self.deliver()
                pending = [e["due"] for e in self.queue.data["events"] if not e["ready"]]
                if pending:
                    delay = min(delay, max(0.1, min(pending) - now()))
                self.storage_error = False
                self.notify()
            except OSError:
                self.storage_error = True
                self.notify()
                _LOGGER.error("HealthLog storage unavailable; delivery paused")
                delay = 60
            try:
                async with asyncio.timeout(max(0.1, delay)):
                    await self.wake.wait()
                    # Coalesce nearby changes before delivery.
                    await asyncio.sleep(self.options["batch_delay"])
            except TimeoutError:
                pass

    async def retry(self) -> None:
        def mutate(data):
            # Manual retry never bypasses persisted backoff / Retry-After.
            data["paused"] = False
            for event in data["events"]:
                for item in event.get("payload", []):
                    if item.get("hold") == "rejected":
                        item.pop("hold")

        await self.queue.change(mutate)
        self.wake.set()

    def status(self):
        data = self.queue.data
        return {
            "authentication": "reauth_required"
            if self.auth_required or self.transport.auth_required
            else "ready",
            "queued_batches": len(data["events"]),
            "pending_measurements": sum(len(e.get("payload", [])) for e in data["events"]),
            "ambiguous_measurements": sum(
                item.get("hold") == "ambiguous"
                or (item.get("inflight", False) and not durable_identity(item))
                for e in data["events"]
                for item in e.get("payload", [])
            ),
            "quarantined_measurements": sum(
                item.get("hold") == "rejected"
                for e in data["events"]
                for item in e.get("payload", [])
            ),
            "identity_conflicts": sum(
                item.get("hold") == "identity_conflict"
                for e in data["events"]
                for item in e.get("payload", [])
            ),
            "last_success": data["last_success"],
            "last_error": "storage_error" if self.storage_error else data["error"],
            "rejected_readings": data["rejected_readings"],
        }
