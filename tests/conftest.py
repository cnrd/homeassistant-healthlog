"""Synthetic inputs only; no user's server, token, or measurement data."""

from copy import deepcopy
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.healthlog import queue_id
from custom_components.healthlog.const import DEFAULTS
from custom_components.healthlog.importer import Importer
from custom_components.healthlog.storage import Queue
from custom_components.healthlog.transport import Transport

TOKEN = "hlk_" + "a" * 64
OPTIONS = {
    **DEFAULTS,
    "trigger_entity": "sensor.measurement_time",
    "mappings": [{"type": "WEIGHT", "entity_id": "sensor.weight", "unit": "kg"}],
}


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Use the real HA loader and fixtures."""


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="healthlog",
        title="HealthLog test connection",
        data={"url": "https://healthlog.example.com", "token": TOKEN, "allow_insecure_http": False},
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(entry, make_profile())
    return entry


def make_profile(profile_id="a" * 32, options=None, title="Test profile"):
    return ConfigSubentry(
        data=MappingProxyType({**deepcopy(options or OPTIONS), "profile_id": profile_id}),
        subentry_type="profile",
        title=title,
        unique_id=profile_id,
    )


def mock_transport():
    transport = Transport(AsyncMock())
    transport.deliver = AsyncMock()
    return transport


@pytest.fixture
def worker(hass, entry):
    subentry = next(iter(entry.subentries.values()))
    return Importer(hass, entry, subentry, mock_transport(), Queue(hass, queue_id(entry, subentry)))


def measurement(metric="WEIGHT", external_id="ha:synthetic-event-one"):
    return {
        "type": metric,
        "value": 60.1234 if metric == "WEIGHT" else 40.125,
        "unit": "kg",
        "measuredAt": "2026-01-02T03:04:05+00:00",
        "externalId": external_id,
    }


async def queued(worker, metric="WEIGHT"):
    item = measurement(metric)
    await worker.queue.add({"key": "event-one", "ready": True, "payload": [item]})
    return item
