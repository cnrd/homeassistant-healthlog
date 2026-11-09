"""Native connection/subentry flows, isolation, lifecycle and diagnostics."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import probatio as vol
import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.healthlog import async_remove_entry, queue_id
from custom_components.healthlog.config_flow import (
    CONTEXTS,
    MappingError,
    mapping_schema,
    validate_options,
)
from custom_components.healthlog.diagnostics import async_get_config_entry_diagnostics
from custom_components.healthlog.models import identity

from .conftest import OPTIONS, TOKEN, make_profile, measurement, queued


def only_profile(entry):
    return next(iter(entry.subentries.values()))


def only_worker(entry):
    return next(iter(entry.runtime_data.workers.values()))


async def setup_entry(hass, entry):
    with patch("custom_components.healthlog.importer.Importer.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def profile_flow(hass, entry, subentry=None):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "profile"),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id}
        if subentry
        else {"source": "user"},
    )
    if subentry:
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "profile"}
        )
    return result


async def submit_profile(hass, flow, options=None, name="Another profile"):
    return await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": name, **deepcopy(options or OPTIONS)}
    )


async def test_connection_setup_no_http_and_next_subentry(hass):
    with patch("custom_components.healthlog.api.API.post", new_callable=AsyncMock) as http:
        result = await hass.config_entries.flow.async_init("healthlog", context={"source": "user"})
        assert set(result["data_schema"].schema) == {"url", "token", "allow_insecure_http"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "healthlog.local:8443/base", "token": TOKEN}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        assert entry.data == {
            "url": "https://healthlog.local:8443/base",
            "token": TOKEN,
            "allow_insecure_http": False,
        }
        assert not entry.options
        assert not entry.subentries
        assert result["next_flow"][0] is config_entries.FlowType.CONFIG_SUBENTRIES_FLOW
        profile = await hass.config_entries.subentries.async_configure(result["next_flow"][1])
        assert profile["step_id"] == "profile"
        assert "token" not in profile["data_schema"].schema
        assert "url" not in profile["data_schema"].schema
        with patch("custom_components.healthlog.importer.Importer.start"):
            created = await submit_profile(hass, profile)
            await hass.async_block_till_done()
        assert created["type"] is FlowResultType.CREATE_ENTRY
        assert len(entry.subentries) == len(entry.runtime_data.workers) == 1
        assert only_profile(entry).data["mappings"] == OPTIONS["mappings"]
        http.assert_not_called()


async def test_duplicate_connection_aborts(hass, entry):
    result = await hass.config_entries.flow.async_init("healthlog", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": entry.data["url"], "token": TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"url": "http://example.com"}, "url"),
        ({"token": ""}, "token"),
        ({"url": "ftp://example.com"}, "url"),
    ],
)
async def test_bad_connection_form(hass, changes, field):
    result = await hass.config_entries.flow.async_init("healthlog", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "https://healthlog.local", "token": TOKEN, **changes}
    )
    assert field in result["errors"]


async def test_missing_token_is_form_error(hass):
    result = await hass.config_entries.flow.async_init("healthlog", context={"source": "user"})
    with pytest.raises(InvalidData, match="token"):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "https://healthlog.local"}
        )


@pytest.mark.parametrize(
    "mapping,reason",
    [
        ({"type": "WEIGHT"}, "mapping_source_required"),
        ({"type": "WEIGHT", "value_template": "{{ bad", "unit": "kg"}, "invalid_template"),
        ({"type": "WEIGHT", "value_template": "{{ 60 }}"}, "template_unit_required"),
        (
            {"type": "WEIGHT", "value_template": "{{ 60 }}", "unit": "kg", "attribute": "x"},
            "mapping_attribute_source_required",
        ),
        ({"type": "BLOOD_GLUCOSE", "entity_id": "sensor.glucose"}, "glucose_context_required"),
    ],
)
async def test_invalid_mapping(hass, mapping, reason):
    with pytest.raises(MappingError) as caught:
        validate_options({**OPTIONS, "mappings": [mapping]}, hass)
    assert caught.value.reason == reason


async def test_duplicate_unknown_metrics(hass):
    for mappings in ([OPTIONS["mappings"][0]] * 2, [{"type": "UNKNOWN"}], []):
        with pytest.raises(ValueError):
            validate_options({**OPTIONS, "mappings": mappings}, hass)


def test_mapping_editor_context_defaults_not_mutated():
    options = {
        **deepcopy(OPTIONS),
        "mappings": [
            {
                "type": "BLOOD_GLUCOSE",
                "entity_id": "sensor.glucose",
                "unit": "mg/dL",
                "glucose_context": "FASTING",
            }
        ],
    }
    original = deepcopy(options)
    schema = mapping_schema(options)
    selector = schema.schema["mappings"]
    assert "glucose_context" not in selector.config["fields"]
    default_key = next(key for key in schema.schema if key == "mappings")
    defaults = default_key.default()
    assert "glucose_context" not in defaults[0]
    assert selector(defaults) == defaults
    assert options == original


@pytest.mark.parametrize("context", CONTEXTS)
async def test_subentry_glucose_step(hass, entry, context):
    flow = await profile_flow(hass, entry)
    data = {
        **OPTIONS,
        "mappings": [{"type": "BLOOD_GLUCOSE", "entity_id": "sensor.glucose", "unit": "mg/dL"}],
    }
    count = len(entry.subentries)
    result = await submit_profile(hass, flow, data)
    assert result["step_id"] == "glucose"
    assert next(iter(result["data_schema"].schema)).default is vol.UNDEFINED
    assert len(entry.subentries) == count
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"glucose_context": "GUESSED"}
        )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"glucose_context": context}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["mappings"][0]["glucose_context"] == context
    assert len(entry.subentries) == count + 1
    assert "token" not in result["data"]


async def test_subentry_invalid_name(hass, entry):
    flow = await profile_flow(hass, entry)
    result = await submit_profile(hass, flow, name="  ")
    assert result["errors"]["base"] == "invalid_name"


async def test_glucose_reconfigure_preserves_identity_and_frozen_queue(hass, entry):
    subentry = only_profile(entry)
    mapping = {
        "type": "BLOOD_GLUCOSE",
        "entity_id": "sensor.glucose",
        "unit": "mg/dL",
        "glucose_context": "FASTING",
    }
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "mappings": [mapping]}
    )
    await setup_entry(hass, entry)
    worker = only_worker(entry)
    item = {
        **measurement("BLOOD_GLUCOSE"),
        "unit": "mg/dL",
        "value": 100,
        "glucoseContext": "FASTING",
    }
    await worker.queue.add({"key": "frozen", "ready": True, "payload": [item]})
    flow = await profile_flow(hass, entry, subentry)
    result = await submit_profile(
        hass,
        flow,
        {**OPTIONS, "mappings": [{k: v for k, v in mapping.items() if k != "glucose_context"}]},
    )
    assert result["step_id"] == "glucose"
    assert next(iter(result["data_schema"].schema)).default() == "FASTING"
    with patch("custom_components.healthlog.importer.Importer.start"):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"glucose_context": "RANDOM"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert subentry.data["mappings"][0]["glucose_context"] == "RANDOM"
    assert subentry.data["profile_id"] == worker.profile_id
    assert only_worker(entry).queue is worker.queue
    assert worker.queue.data["events"][0]["payload"][0] == item


async def test_multiple_profiles_share_connection_not_identity(hass, entry):
    await setup_entry(hass, entry)
    first = only_worker(entry)
    flow = await profile_flow(hass, entry)
    with patch("custom_components.healthlog.importer.Importer.start"):
        result = await submit_profile(
            hass, flow, {**OPTIONS, "trigger_entity": "sensor.second_time"}, name="Second"
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries("healthlog")) == 1
    assert len(entry.runtime_data.workers) == 2
    second = next(w for w in entry.runtime_data.workers.values() if w is not first)
    assert first.transport is second.transport
    assert first.queue.store.key != second.queue.store.key
    assert identity(first.profile_id, "same-event", "WEIGHT") != identity(
        second.profile_id, "same-event", "WEIGHT"
    )
    await queued(first)
    assert not second.queue.data["events"]
    # Adding a profile does not restart a sibling or freeze its pending capture.
    assert entry.runtime_data.workers[first.subentry.subentry_id] is first


async def test_profile_edit_does_not_restart_sibling(hass, entry):
    second = make_profile("b" * 32, {**OPTIONS, "trigger_entity": "sensor.second_time"}, "Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    sibling = entry.runtime_data.workers[second.subentry_id]
    first = only_profile(entry)
    previous = entry.runtime_data.workers[first.subentry_id]
    flow = await profile_flow(hass, entry, first)
    with patch("custom_components.healthlog.importer.Importer.start"):
        result = await submit_profile(hass, flow, {**OPTIONS, "settling_delay": 15})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert entry.runtime_data.workers[second.subentry_id] is sibling
    assert entry.runtime_data.workers[first.subentry_id] is not previous
    assert entry.runtime_data.workers[first.subentry_id].options["settling_delay"] == 15


async def test_entities_are_linked_to_subentries(hass, entry):
    from homeassistant.helpers import entity_registry as er

    second = make_profile("b" * 32, title="Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 4
    for subentry in entry.subentries.values():
        assert len([e for e in entities if e.config_subentry_id == subentry.subentry_id]) == 2


async def test_remove_one_profile_retains_sibling_queue(hass, entry, hass_storage):
    second = make_profile("b" * 32, title="Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    first = only_profile(entry)
    one = entry.runtime_data.workers[first.subentry_id]
    two = entry.runtime_data.workers[second.subentry_id]
    await queued(one)
    await queued(two)
    hass.config_entries.async_remove_subentry(entry, first.subentry_id)
    await hass.async_block_till_done()
    assert first.subentry_id not in entry.runtime_data.workers
    assert one.queue.store.key not in hass_storage
    assert two.queue.store.key in hass_storage
    assert entry.runtime_data.workers[second.subentry_id] is two


async def test_parent_removal_deletes_profile_queues(hass, entry, hass_storage):
    await setup_entry(hass, entry)
    worker = only_worker(entry)
    await queued(worker)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_remove_entry(hass, entry)
    assert worker.queue.store.key not in hass_storage


async def test_setup_never_garbage_collects_unattached_capture(hass, entry):
    # Config-subentry metadata is saved by HA separately from queue writes.
    # An interrupted setup/partial backup must not make us delete a capture.
    with patch("custom_components.healthlog.async_remove_unused_queues", new=AsyncMock()) as remove:
        await setup_entry(hass, entry)
    remove.assert_not_called()


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_RECONFIGURE, config_entries.SOURCE_REAUTH]
)
async def test_parent_token_replacement_preserves_profiles(hass, entry, source):
    await setup_entry(hass, entry)
    subentry = only_profile(entry)
    await queued(only_worker(entry))
    result = await hass.config_entries.flow.async_init(
        "healthlog",
        context={"source": source, "entry_id": entry.entry_id},
        data=entry.data if source == config_entries.SOURCE_REAUTH else None,
    )
    token = "hlk_" + "b" * 64
    with patch("custom_components.healthlog.importer.Importer.start"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": entry.data["url"], "token": token}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert entry.data["token"] == token
    assert only_profile(entry).subentry_id == subentry.subentry_id
    assert len(only_worker(entry).queue.data["events"]) == 1
    assert only_worker(entry).transport.api._token == token


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_RECONFIGURE, config_entries.SOURCE_REAUTH]
)
async def test_parent_http_requires_opt_in(hass, entry, source):
    await setup_entry(hass, entry)
    result = await hass.config_entries.flow.async_init(
        "healthlog",
        context={"source": source, "entry_id": entry.entry_id},
        data=entry.data if source == config_entries.SOURCE_REAUTH else None,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "http://healthlog.example.com:3000", "token": TOKEN}
    )
    assert result["errors"]["url"] == "invalid_url"
    with patch("custom_components.healthlog.importer.Importer.start"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "url": "http://healthlog.example.com:3000",
                "token": TOKEN,
                "allow_insecure_http": True,
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert only_worker(entry).transport.api.url == "http://healthlog.example.com:3000"


async def test_runtime_http_requires_opt_in(hass, entry):
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "url": "http://healthlog.example.com:3000"}
    )
    assert not await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.flow.async_init(
        "healthlog", context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    with patch("custom_components.healthlog.importer.Importer.start"):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"url": entry.data["url"], "token": TOKEN, "allow_insecure_http": True},
        )
        await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_reconfigure_cannot_redirect_parent(hass, entry):
    result = await hass.config_entries.flow.async_init(
        "healthlog", context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "https://other.example.com", "token": TOKEN}
    )
    assert result["errors"]["url"] == "server_change_not_allowed"


@pytest.mark.parametrize("resolution", ["delivered", "not_delivered"])
async def test_reconcile_only_selected_profile(hass, entry, resolution):
    second = make_profile("b" * 32, title="Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    subentry = only_profile(entry)
    worker = entry.runtime_data.workers[subentry.subentry_id]
    sibling = entry.runtime_data.workers[second.subentry_id]
    item = await queued(worker, "FAT_MASS")
    await queued(sibling)
    await worker.queue.hold(item["externalId"], "ambiguous")
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "profile"),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    flow = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"next_step_id": "resolve"}
    )
    data = {"measurement": item["externalId"], "resolution": resolution}
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {**data, "confirmed": False}
    )
    assert result["errors"]
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {**data, "confirmed": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert len(sibling.queue.data["events"]) == 1
    if resolution == "delivered":
        assert not worker.queue.data["events"]
    else:
        assert "hold" not in worker.queue.data["events"][0]["payload"][0]


async def test_diagnostics_allowlist(hass, entry):
    await setup_entry(hass, entry)
    await queued(only_worker(entry))
    result = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (
        TOKEN,
        "example.com",
        "sensor.weight",
        "60.1234",
        "ha:synthetic",
        only_profile(entry).title,
    ):
        assert secret not in result
    assert json.loads(result)["profile_count"] == 1


async def test_real_worker_and_unload(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    worker = only_worker(entry)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.weight", "60", {"unit_of_measurement": "kg"})
    hass.states.async_set("sensor.measurement_time", "2026-01-02T03:04:05Z")
    await hass.async_block_till_done()
    assert not worker.queue.data["events"]
    hass.states.async_set("sensor.measurement_time", "2026-01-02T03:04:06Z")
    await hass.async_block_till_done()
    assert len(worker.queue.data["events"]) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert worker.worker.done()
    assert not worker.tasks


async def test_two_accounts_do_not_share_transport(hass, entry):
    await setup_entry(hass, entry)
    other = MockConfigEntry(
        domain="healthlog", title="Other account", data={**entry.data, "token": "hlk_" + "b" * 64}
    )
    other.add_to_hass(hass)
    hass.config_entries.async_add_subentry(other, make_profile("c" * 32))
    await setup_entry(hass, other)
    assert only_worker(entry).transport is not only_worker(other).transport
    assert queue_id(entry, only_profile(entry)) != queue_id(other, only_profile(other))


async def test_auth_failure_pauses_all_profiles_in_connection(hass, entry):
    from custom_components.healthlog.api import AuthError

    second = make_profile("b" * 32, title="Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    first = only_worker(entry)
    sibling = entry.runtime_data.workers[second.subentry_id]
    await queued(first)
    await queued(sibling)
    with (
        patch.object(
            first.transport, "deliver", new=AsyncMock(side_effect=AuthError(401))
        ) as deliver,
        patch.object(entry, "async_start_reauth") as reauth,
    ):
        await first.deliver()
        await sibling.deliver()
    deliver.assert_awaited_once()
    reauth.assert_called_once()
    assert (
        first.status()["authentication"] == sibling.status()["authentication"] == "reauth_required"
    )
    assert len(sibling.queue.data["events"]) == 1


async def test_natural_identity_guard_is_shared_across_profiles(hass, entry, freezer):
    second = make_profile("b" * 32, title="Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    first = only_worker(entry)
    sibling = entry.runtime_data.workers[second.subentry_id]
    await first.queue.add(
        {"key": "event-a", "ready": True, "payload": [measurement(external_id="ha:profile-one")]}
    )
    await sibling.queue.add(
        {"key": "event-b", "ready": True, "payload": [measurement(external_id="ha:profile-two")]}
    )
    with patch.object(first.transport, "deliver", new=AsyncMock()) as deliver:
        await first.deliver()
        await sibling.deliver()
    deliver.assert_awaited_once()
    assert sibling.status()["identity_conflicts"] == 1
    # A connection restart reconstructs the claim from the delivered profile's
    # durable ledger, even though its queue is empty.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await setup_entry(hass, entry)
    sibling = entry.runtime_data.workers[second.subentry_id]
    assert sibling.status()["identity_conflicts"] == 1
    freezer.tick(86400)
    with patch.object(sibling.transport, "deliver", new=AsyncMock()) as retry:
        await sibling.retry()
        await sibling.deliver()
    retry.assert_not_called()


async def test_parallel_profiles_reserve_identity_before_http(hass, entry):
    import asyncio

    second = make_profile("b" * 32, title="Second")
    hass.config_entries.async_add_subentry(entry, second)
    await setup_entry(hass, entry)
    first, sibling = entry.runtime_data.workers.values()
    await first.queue.add(
        {"key": "a", "ready": True, "payload": [measurement(external_id="ha:parallel-one")]}
    )
    await sibling.queue.add(
        {"key": "b", "ready": True, "payload": [measurement(external_id="ha:parallel-two")]}
    )
    with patch.object(first.transport, "deliver", new=AsyncMock()) as deliver:
        await asyncio.gather(first.deliver(), sibling.deliver())
    deliver.assert_awaited_once()
    assert sum(worker.status()["identity_conflicts"] for worker in (first, sibling)) == 1


async def test_removing_glucose_skips_context(hass, entry):
    subentry = only_profile(entry)
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={
            **subentry.data,
            "mappings": [
                {
                    "type": "BLOOD_GLUCOSE",
                    "entity_id": "sensor.glucose",
                    "glucose_context": "FASTING",
                }
            ],
        },
    )
    await setup_entry(hass, entry)
    flow = await profile_flow(hass, entry, subentry)
    with patch("custom_components.healthlog.importer.Importer.start"):
        result = await submit_profile(hass, flow)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert subentry.data["mappings"] == OPTIONS["mappings"]
