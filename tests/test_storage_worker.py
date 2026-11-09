"""Crash boundaries, copy-before-commit, and safe ambiguous delivery."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store

from custom_components.healthlog import queue_id
from custom_components.healthlog.api import APIError, AuthError
from custom_components.healthlog.importer import Importer
from custom_components.healthlog.storage import DurableStore, Queue, QueueFull

from .conftest import measurement, mock_transport, queued


async def test_copy_before_commit(worker):
    original = deepcopy(worker.queue.data)
    with patch.object(worker.queue.store, "async_save", side_effect=OSError):
        with pytest.raises(OSError):
            await queued(worker)
    assert worker.queue.data == original


async def test_store_readback_and_shutdown(hass):
    store = DurableStore(hass, 1, "healthlog.synthetic", private=True)
    await store.async_save({"test": 1})
    assert await Store(hass, 1, "healthlog.synthetic").async_load() == {"test": 1}
    with patch.object(Store, "async_save", new=AsyncMock()):
        with pytest.raises(OSError, match="read-back"):
            await store.async_save({"test": 2})
    hass.set_state(CoreState.stopping)
    with pytest.raises(OSError, match="shutdown"):
        await store.async_save({"test": 3})


async def test_full_queue_no_eviction(worker):
    worker.queue.capacity = 1
    await queued(worker)
    assert not await worker.queue.add({"key": "event-one"})
    with pytest.raises(QueueFull):
        await worker.queue.add({"key": "event-two"})
    assert worker.queue.data["events"][0]["key"] == "event-one"


async def test_concurrent_queue_changes(worker):
    await asyncio.gather(
        *(worker.queue.add({"key": f"event-{i}", "ready": True, "payload": []}) for i in range(10))
    )
    assert len(worker.queue.data["events"]) == 10
    reloaded = Queue(worker.hass, queue_id(worker.entry, worker.subentry))
    await reloaded.load()
    assert reloaded.data == worker.queue.data


@pytest.mark.parametrize("metric", ["WEIGHT", "FAT_MASS"])
async def test_ack_and_seen_are_durable(worker, metric):
    await queued(worker, metric)
    await worker.deliver()
    assert not worker.queue.data["events"]
    reloaded = Queue(worker.hass, queue_id(worker.entry, worker.subentry))
    await reloaded.load()
    assert not await reloaded.add({"key": "event-one"})
    assert reloaded.data["last_success"]


@pytest.mark.parametrize("status", [0, 200, 409, 500, 503])
async def test_single_uncertain_is_held_even_after_24_hours_and_manual_retry(
    worker, status, freezer
):
    await queued(worker, "FAT_MASS")
    worker.transport.deliver.side_effect = APIError(status)
    await worker.deliver()
    assert worker.queue.data["events"][0]["payload"][0]["hold"] == "ambiguous"
    await worker.retry()
    freezer.tick(48 * 3600)
    await worker.deliver()
    worker.transport.deliver.assert_awaited_once()
    assert worker.status()["ambiguous_measurements"] == 1


@pytest.mark.parametrize("status", [0, 200, 500, 503])
async def test_batch_durable_retry_beyond_24_hours(worker, status, freezer):
    await queued(worker)
    worker.transport.deliver.side_effect = APIError(status)
    await worker.deliver()
    first = deepcopy(worker.transport.deliver.call_args.args[0])
    freezer.tick(48 * 3600)
    worker.transport.deliver.side_effect = None
    await worker.deliver()
    assert worker.transport.deliver.call_args.args[0] == first
    assert not worker.queue.data["events"]


@pytest.mark.parametrize("metric", ["WEIGHT", "FAT_MASS"])
async def test_cancellation_during_http_restart(worker, metric, freezer):
    await queued(worker, metric)
    reached = asyncio.Event()

    async def interrupted(item):
        reached.set()
        await asyncio.Event().wait()

    worker.transport.deliver.side_effect = interrupted
    task = asyncio.create_task(worker.deliver())
    await reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    queue = Queue(worker.hass, queue_id(worker.entry, worker.subentry))
    await queue.load()
    restarted = Importer(worker.hass, worker.entry, worker.subentry, mock_transport(), queue)
    freezer.tick(48 * 3600)
    await restarted.deliver()
    if metric == "FAT_MASS":
        restarted.transport.deliver.assert_not_awaited()
        assert restarted.status()["ambiguous_measurements"] == 1
    else:
        restarted.transport.deliver.assert_awaited_once()


async def test_no_http_when_intent_write_fails(worker):
    await queued(worker)
    with patch.object(worker.queue.store, "async_save", side_effect=OSError):
        with pytest.raises(OSError):
            await worker.deliver()
    worker.transport.deliver.assert_not_called()


@pytest.mark.parametrize("metric", ["WEIGHT", "FAT_MASS"])
async def test_failed_ack_persistence_no_unsafe_replay(worker, metric, freezer):
    await queued(worker, metric)
    actual_save = worker.queue.store.async_save
    calls = 0

    async def save(data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError
        await actual_save(data)

    with patch.object(worker.queue.store, "async_save", side_effect=save):
        with pytest.raises(OSError):
            await worker.deliver()
    freezer.tick(48 * 3600)
    await worker.deliver()
    assert worker.transport.deliver.await_count == (2 if metric == "WEIGHT" else 1)


@pytest.mark.parametrize("status", [400, 422])
async def test_rejection_quarantine_retry(worker, status, freezer):
    await queued(worker)
    worker.transport.deliver.side_effect = APIError(status)
    await worker.deliver()
    assert worker.status()["quarantined_measurements"] == 1
    worker.transport.deliver.side_effect = None
    await worker.retry()
    await worker.deliver()
    assert worker.transport.deliver.await_count == 1  # Backoff still enforced.
    freezer.tick(3600)
    await worker.deliver()
    assert not worker.queue.data["events"]


@pytest.mark.parametrize("metric", ["WEIGHT", "FAT_MASS"])
async def test_rate_limit_retry_after(worker, metric, freezer):
    await queued(worker, metric)
    worker.transport.deliver.side_effect = APIError(429, 7200)
    delay = await worker.deliver()
    assert delay >= 7199
    assert not worker.status()["ambiguous_measurements"]
    await worker.retry()
    freezer.tick(100)
    await worker.deliver()
    assert worker.transport.deliver.await_count == 1


async def test_401_starts_reauth_without_holding(worker):
    await queued(worker, "FAT_MASS")
    worker.transport.deliver.side_effect = AuthError(401)
    with patch.object(worker.entry, "async_start_reauth") as reauth:
        await worker.deliver()
        reauth.assert_called_once()
    assert worker.status()["authentication"] == "reauth_required"
    assert not worker.status()["ambiguous_measurements"]


async def test_403_pauses_not_bad_password(worker):
    await queued(worker, "FAT_MASS")
    worker.transport.deliver.side_effect = APIError(403)
    await worker.deliver()
    assert worker.queue.data["paused"]
    assert worker.status()["authentication"] == "ready"
    assert worker.status()["last_error"] == "http_403"


async def test_metric_partial_delivery_isolated(worker, freezer):
    one = measurement()
    two = measurement("FAT_MASS", "ha:second")
    await worker.queue.add({"key": "one", "ready": True, "payload": [one, two]})
    await worker.deliver()
    assert worker.queue.data["events"][0]["payload"] == [two]
    freezer.tick(1000)
    worker.transport.deliver.side_effect = APIError(422)
    await worker.deliver()
    assert worker.status()["quarantined_measurements"] == 1
    assert worker.queue.data["seen"] == []


async def test_same_reading_distinct_events(worker, freezer):
    await queued(worker)
    await worker.queue.add(
        {
            "key": "two",
            "ready": True,
            "payload": [
                {**measurement(external_id="ha:second"), "measuredAt": "2026-01-02T03:04:06+00:00"}
            ],
        }
    )
    await worker.deliver()
    freezer.tick(1000)
    await worker.deliver()
    assert worker.transport.deliver.await_count == 2
    assert worker.queue.data["seen"] == ["event-one", "two"]


async def test_same_timestamp_different_event_held_before_http(worker, freezer):
    await queued(worker)
    await worker.deliver()
    await worker.queue.add(
        {"key": "two", "ready": True, "payload": [measurement(external_id="ha:second")]}
    )
    freezer.tick(1000)
    await worker.deliver()
    assert worker.status()["identity_conflicts"] == 1
    worker.transport.deliver.assert_awaited_once()
    await worker.retry()
    assert worker.status()["identity_conflicts"] == 1


async def test_store_commit_cancellation_waits(worker):
    started, finish = asyncio.Event(), asyncio.Event()
    real_save = worker.queue.store.async_save

    async def delayed(data):
        started.set()
        await finish.wait()
        await real_save(data)

    with patch.object(worker.queue.store, "async_save", side_effect=delayed):
        task = asyncio.create_task(queued(worker))
        await started.wait()
        task.cancel()
        # A concurrent queue mutation cannot enter before the executor commit.
        assert worker.queue.lock.locked()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(worker.queue.data["events"]) == 1
    reloaded = Queue(worker.hass, queue_id(worker.entry, worker.subentry))
    await reloaded.load()
    assert reloaded.data == worker.queue.data


@pytest.mark.parametrize(
    "loaded",
    [{}, {"events": "invalid", "seen": []}, {"events": [{"key": "x", "ready": False}], "seen": []}],
)
async def test_invalid_store_schema_fails_closed(worker, loaded):
    with patch.object(worker.queue.store, "async_load", return_value=loaded):
        with pytest.raises(OSError, match="schema"):
            await worker.queue.load()


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("metric", ["WEIGHT", "FAT_MASS"])
async def test_timeout_before_or_after_server_commit(worker, freezer, committed, metric):
    from custom_components.healthlog.transport import Transport

    rows = {}
    attempts = 0

    class FakeServer:
        async def post(self, path, body, key):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                if committed:
                    rows[key] = body
                raise APIError()
            duplicate = key in rows
            rows[key] = body
            return {"entries": [{"index": 0, "status": "duplicate" if duplicate else "inserted"}]}

    worker.transport = Transport(FakeServer())
    await queued(worker, metric)
    await worker.deliver()
    freezer.tick(48 * 3600)
    await worker.deliver()
    if metric == "WEIGHT":
        assert len(rows) == 1
        assert not worker.queue.data["events"]
        assert attempts == 2
    else:
        assert len(rows) == int(committed)
        assert attempts == 1
        assert worker.status()["ambiguous_measurements"] == 1


async def test_server_updated_is_held_conflict(worker, freezer):
    from custom_components.healthlog.transport import IdentityConflict

    await queued(worker)
    worker.transport.deliver.side_effect = IdentityConflict(409)
    await worker.deliver()
    assert worker.status()["identity_conflicts"] == 1
    await worker.retry()
    freezer.tick(48 * 3600)
    await worker.deliver()
    worker.transport.deliver.assert_awaited_once()


async def test_completed_identity_does_not_expire(worker):
    worker.queue.data["seen"] = [f"old-{n}" for n in range(2100)]
    await queued(worker)
    await worker.deliver()
    assert "old-0" in worker.queue.data["seen"]
    assert not await worker.queue.add({"key": "old-0"})


async def test_corrupt_backup_marker_blocks_new_empty_queue(worker):
    with patch(
        "custom_components.healthlog.storage.Path.glob",
        return_value=iter(["synthetic-corruption-marker"]),
    ):
        with pytest.raises(OSError, match="repair"):
            await worker.queue.load()


async def test_profile_queue_cleanup_is_narrowly_scoped(hass, tmp_path):
    from custom_components.healthlog.storage import async_remove_unused_queues

    active, removed = "a" * 32, "b" * 32
    prefix = "healthlog.connection-test."
    keep = [
        prefix + active + ".queue",
        prefix + active + ".queue.corrupt.sample",
        prefix + removed + ".queue.bak",
        prefix + "not-a-profile.queue",
        "healthlog.other." + removed + ".queue",
    ]
    discard = [prefix + removed + ".queue", prefix + removed + ".queue.corrupt.sample"]
    for name in keep + discard:
        (tmp_path / name).write_text("synthetic test data")

    async def remove(store):
        (tmp_path / store.key).unlink(missing_ok=True)

    with (
        patch.object(hass.config, "path", return_value=str(tmp_path)),
        patch.object(Store, "async_remove", new=remove),
    ):
        await async_remove_unused_queues(hass, "connection-test", {"connection-test." + active})
    assert all((tmp_path / name).exists() for name in keep)
    assert not any((tmp_path / name).exists() for name in discard)
