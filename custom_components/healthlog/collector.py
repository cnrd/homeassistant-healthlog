"""Read direct values or render HA-native templates at measurement capture."""

import math
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template


def _quiet_template_log(level, message):
    """Template errors may contain the source text or private health values."""


class Collector:
    """Cache compilation, not results; results/dependencies belong to each event."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._templates: dict[str, Template] = {}

    def _template(self, source):
        if source not in self._templates:
            if len(self._templates) >= 128:
                self._templates.pop(next(iter(self._templates)))
            self._templates[source] = Template(source, self.hass)
        return self._templates[source]

    def snapshot(
        self, mapping: dict[str, Any], state: State | None = None
    ) -> dict[str, Any] | None:
        source = mapping.get("value_template")
        if not source:
            if (
                state is None
                or state.state in ("unknown", "unavailable")
                or state.attributes.get("restored")
            ):
                return None
            value = (
                state.attributes.get(mapping["attribute"])
                if mapping.get("attribute")
                else state.state
            )
            unit = mapping.get("unit") or state.attributes.get("unit_of_measurement", "")
            return self._numeric_snapshot(value, unit, state.last_updated.timestamp())

        # An optional source entity supplies `value` and an explicit freshness
        # anchor. Template-only mappings derive freshness from all entity refs.
        anchor_id = mapping.get("entity_id")
        anchor = self.hass.states.get(anchor_id) if anchor_id else None
        value = None
        source_state = None
        if anchor is not None:
            # Pass detached data, not a live HA State object. Expressions can
            # read several attributes from the same source snapshot without
            # hardcoding/re-resolving an entity ID or mutating HA attributes.
            source_state = {
                "entity_id": anchor.entity_id,
                "state": anchor.state,
                "attributes": deepcopy(dict(anchor.attributes)),
                "last_changed": anchor.last_changed.isoformat(),
                "last_updated": anchor.last_updated.isoformat(),
            }
            value = (
                source_state["attributes"].get(mapping["attribute"])
                if mapping.get("attribute")
                else source_state["state"]
            )
        info = self._template(source).async_render_to_info(
            {
                "value": value,
                "source": source_state,
                "source_entity": anchor_id or None,
            },
            strict=True,
            log_fn=_quiet_template_log,
        )
        entities = set(info.entities)
        if anchor_id:
            entities.add(anchor_id)
        metadata: dict[str, Any] = {
            "template_entities": sorted(entities),
            "template_domains": sorted(set(info.domains) | set(info.domains_lifecycle)),
            "template_all_states": info.all_states
            or info.all_states_lifecycle
            or info.exception is not None,
        }
        # Keep dependency information even when rendering fails, so a source
        # becoming available during settling can repair the pending snapshot.
        try:
            result = info.result()
        except TemplateError:
            return {
                **metadata,
                "value": None,
                "unit": mapping.get("unit", ""),
                "updated": 0,
            }
        if anchor_id:
            stamps = (
                [anchor.last_updated.timestamp()]
                if anchor
                and anchor.state not in ("unknown", "unavailable")
                and not anchor.attributes.get("restored")
                else [0]
            )
        else:
            if metadata["template_all_states"]:
                entities.update(state.entity_id for state in self.hass.states.async_all())
            elif metadata["template_domains"]:
                entities.update(
                    state.entity_id
                    for state in self.hass.states.async_all()
                    if state.domain in metadata["template_domains"]
                )
            states = [self.hass.states.get(entity_id) for entity_id in entities]
            stamps = [
                state.last_updated.timestamp()
                if state
                and state.state not in ("unknown", "unavailable")
                and not state.attributes.get("restored")
                else 0
                for state in states
            ]
        # A template-only constant has no genuine freshness evidence. Do not
        # stamp it with collection time and turn it into a fresh sensor reading.
        updated = min(stamps) if stamps else 0
        snapshot = self._numeric_snapshot(result, mapping.get("unit", ""), updated)
        return {
            **metadata,
            **(snapshot or {"value": None, "unit": mapping.get("unit", ""), "updated": updated}),
        }

    @staticmethod
    def _numeric_snapshot(value, unit, updated):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None
        if isinstance(value, str) and len(value) > 128:
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if not isinstance(unit, str) or len(unit) > 128:
            return None
        return {"value": value, "unit": unit, "updated": updated}

    @staticmethod
    def depends_on(mapping, snapshot, entity_id):
        if mapping.get("entity_id") == entity_id:
            return True
        if not mapping.get("value_template") or not snapshot:
            return False
        return (
            snapshot.get("template_all_states", False)
            or entity_id in snapshot.get("template_entities", [])
            or entity_id.partition(".")[0] in snapshot.get("template_domains", [])
        )
