"""Token-only setup and generic measurement editor using current HA schemas."""

from uuid import uuid4

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import selector
from homeassistant.helpers.template import Template
from yarl import URL

from .api import normalize_url, validate_token
from .const import DEFAULTS, DOMAIN, SUBENTRY_PROFILE
from .models import METRICS
from .transport import durable_identity

CONTEXTS = {
    "FASTING": "Fasting",
    "POSTPRANDIAL": "After a meal",
    "RANDOM": "Random",
    "BEDTIME": "Bedtime",
}
LIMITS = {
    "settling_delay": (0, 300),
    "freshness_window": (0, 600),
    "batch_delay": (1, 300),
    "queue_capacity": (1, 1000),
}


def credentials_schema(defaults=None):
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                "url", default=defaults.get("url", vol.UNDEFINED)
            ): selector.TextSelector(),
            vol.Required("token"): selector.TextSelector({"type": "password"}),
            vol.Optional(
                "allow_insecure_http", default=defaults.get("allow_insecure_http", False)
            ): selector.BooleanSelector(),
        }
    )


def mapping_schema(options=None):
    defaults = {**DEFAULTS, **(options or {})}
    mappings = defaults.get("mappings", vol.UNDEFINED)
    if mappings is not vol.UNDEFINED:
        # Context is edited only on the conditional follow-up step. Do not
        # expose it as an unknown field in HA's static ObjectSelector dialog.
        mappings = [
            {key: value for key, value in mapping.items() if key != "glucose_context"}
            for mapping in mappings
        ]
    fields = {
        "type": {
            "required": True,
            "label": "Measurement type (output unit)",
            "selector": selector.SelectSelector(
                {
                    "options": [
                        {
                            "value": metric,
                            "label": f"{metric.replace('_', ' ').capitalize()} ({METRICS[metric][0]})",
                        }
                        for metric in sorted(METRICS)
                    ],
                    "mode": "dropdown",
                }
            ),
        },
        "entity_id": {"label": "Source entity", "selector": selector.EntitySelector()},
        "attribute": {"label": "Numeric attribute (optional)", "selector": selector.TextSelector()},
        "value_template": {
            "label": "Value template (optional)",
            "selector": selector.TemplateSelector(),
        },
        "unit": {"label": "Source/result unit", "selector": selector.TextSelector()},
    }
    schema = {
        vol.Required(
            "trigger_entity", default=defaults.get("trigger_entity", vol.UNDEFINED)
        ): selector.EntitySelector(),
        vol.Optional(
            "trigger_attribute", default=defaults["trigger_attribute"]
        ): selector.TextSelector(),
        vol.Required("trigger_mode", default=defaults["trigger_mode"]): selector.SelectSelector(
            {
                "options": [
                    {"value": "timestamp", "label": "Measurement timestamp (recommended)"},
                    {"value": "event_id", "label": "Stable measurement event ID"},
                ]
            }
        ),
        vol.Required("mappings", default=mappings): selector.ObjectSelector(
            {
                "fields": fields,
                "multiple": True,
                "description_field": "attribute",
                "translation_key": "measurement_mapping",
            }
        ),
        vol.Optional("description", default=defaults["description"]): selector.TextSelector(),
    }
    for key, (minimum, maximum) in LIMITS.items():
        schema[vol.Required(key, default=defaults[key])] = selector.NumberSelector(
            {"min": minimum, "max": maximum, "mode": "box"}
        )
    return vol.Schema(schema)


class MappingError(ValueError):
    """User-actionable error without private template details."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def validate_options(data, hass, *, require_glucose_context=True):
    data = {**DEFAULTS, **data}
    mappings = data["mappings"]
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= len(METRICS):
        raise ValueError("invalid_mapping")
    types = [mapping["type"] for mapping in mappings]
    if len(types) != len(set(types)) or any(metric not in METRICS for metric in types):
        raise ValueError("invalid_mapping")
    for mapping in mappings:
        if mapping["type"] == "BLOOD_GLUCOSE":
            context = mapping.get("glucose_context")
            if context not in CONTEXTS and (require_glucose_context or context is not None):
                raise MappingError("glucose_context_required")
        elif mapping.get("glucose_context"):
            raise MappingError("invalid_mapping")
        source = mapping.get("value_template")
        if source:
            if not isinstance(source, str) or len(source) > 8192:
                raise MappingError("invalid_template")
            try:
                Template(source, hass).ensure_valid()
            except TemplateError:
                raise MappingError("invalid_template") from None
            if not mapping.get("unit"):
                raise MappingError("template_unit_required")
        elif not mapping.get("entity_id"):
            raise MappingError("mapping_source_required")
        if mapping.get("attribute") and not mapping.get("entity_id"):
            raise MappingError("mapping_attribute_source_required")
    if len(data["description"]) > 500:
        raise ValueError("invalid_mapping")
    for key, (low, high) in LIMITS.items():
        if isinstance(data[key], bool) or not low <= float(data[key]) <= high:
            raise ValueError("invalid_mapping")
        data[key] = int(data[key])
    if data["trigger_mode"] not in ("timestamp", "event_id") or not data.get("trigger_entity"):
        raise ValueError("invalid_mapping")
    return data


def glucose_mapping(options):
    """One mapping per metric is already enforced by profile validation."""
    return next((m for m in options.get("mappings", []) if m["type"] == "BLOOD_GLUCOSE"), None)


async def glucose_step(flow, user_input):
    """Only glucose profiles reach this HA-native conditional step."""
    if flow._profile_options is None:
        return flow.async_abort(reason="profile_not_available")
    errors = {}
    if user_input is not None:
        context = user_input.get("glucose_context")
        if context not in CONTEXTS:
            errors["base"] = "glucose_context_required"
        else:
            options = {
                **flow._profile_options,
                "mappings": [dict(m) for m in flow._profile_options["mappings"]],
            }
            glucose_mapping(options)["glucose_context"] = context
            options = validate_options(options, flow.hass)
            return flow.finish_profile(options)
    return flow.async_show_form(
        step_id="glucose",
        data_schema=vol.Schema(
            {
                vol.Required(
                    "glucose_context",
                    default=flow._glucose_default
                    if flow._glucose_default in CONTEXTS
                    else vol.UNDEFINED,
                ): selector.SelectSelector(
                    {
                        "options": [
                            {"value": value, "label": label} for value, label in CONTEXTS.items()
                        ],
                        "mode": "dropdown",
                    }
                ),
            }
        ),
        errors=errors,
    )


class HealthLogConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """One server/account connection; profiles are native config subentries."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {SUBENTRY_PROFILE: HealthLogProfileFlow}

    async def async_on_create_entry(self, result):
        profile = await self.hass.config_entries.subentries.async_init(
            (result["result"].entry_id, SUBENTRY_PROFILE),
            context={"source": config_entries.SOURCE_USER},
        )
        result["next_flow"] = (config_entries.FlowType.CONFIG_SUBENTRIES_FLOW, profile["flow_id"])
        return result

    async def async_step_user(self, user_input=None):
        return await self.connection_step("user", user_input)

    async def connection_step(self, step, user_input, entry=None):
        errors = {}
        if user_input is not None:
            try:
                allow_http = user_input.get("allow_insecure_http", False)
                url = normalize_url(user_input.get("url"), allow_insecure_http=allow_http)
            except ValueError:
                errors["url"] = "invalid_url"
            try:
                token = validate_token(user_input.get("token"))
            except ValueError:
                errors["token"] = "invalid_token"
            if not errors:
                if entry and (
                    URL(url).host != URL(entry.data["url"]).host
                    or URL(url).raw_path.rstrip("/") != URL(entry.data["url"]).raw_path.rstrip("/")
                ):
                    errors["url"] = "server_change_not_allowed"
                else:
                    data = {
                        "url": url,
                        "token": token,
                        "allow_insecure_http": allow_http,
                    }
                    if entry:
                        if not entry.update_listeners:
                            # A setup error (for example HTTP without opt-in)
                            # has no runtime listener to reload the fixed entry.
                            return self.async_update_reload_and_abort(entry, data=data)
                        return self.async_update_and_abort(entry, data=data)
                    self._async_abort_entries_match({"url": url, "token": token})
                    return self.async_create_entry(title=f"HealthLog ({URL(url).host})", data=data)
        return self.async_show_form(
            step_id=step,
            data_schema=credentials_schema(entry.data if entry else None),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self.connection_step("reauth_confirm", user_input, self._get_reauth_entry())

    async def async_step_reconfigure(self, user_input=None):
        return await self.connection_step("reconfigure", user_input, self._get_reconfigure_entry())


class HealthLogProfileFlow(config_entries.ConfigSubentryFlow):
    """Add/edit/reconcile a profile without collecting credentials again."""

    def __init__(self):
        super().__init__()
        self._profile_options = None
        self._glucose_default = None
        self._profile_id = uuid4().hex
        self._profile_name = None

    async def async_step_user(self, user_input=None):
        return await self.async_step_profile(user_input)

    async def async_step_reconfigure(self, user_input=None):
        return self.async_show_menu(step_id="reconfigure", menu_options=["profile", "resolve"])

    def profile_defaults(self):
        if self.source == config_entries.SOURCE_RECONFIGURE:
            return self._get_reconfigure_subentry().data
        return {}

    async def async_step_profile(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                name = user_input["name"].strip()
                if not name or len(name) > 100:
                    raise MappingError("invalid_name")
                self._profile_name = name
                options = validate_options(
                    {key: value for key, value in user_input.items() if key != "name"},
                    self.hass,
                    require_glucose_context=False,
                )
            except MappingError as err:
                errors["base"] = err.reason
            except ValueError, KeyError, TypeError, OverflowError:
                errors["base"] = "invalid_mapping"
            else:
                if glucose_mapping(options) is not None:
                    self._profile_options = options
                    self._glucose_default = (glucose_mapping(self.profile_defaults()) or {}).get(
                        "glucose_context"
                    )
                    return await self.async_step_glucose()
                return self.finish_profile(options)
        default_name = (
            self._get_reconfigure_subentry().title
            if self.source == config_entries.SOURCE_RECONFIGURE
            else vol.UNDEFINED
        )
        schema = vol.Schema(
            {
                vol.Required("name", default=default_name): selector.TextSelector(),
                **mapping_schema(self.profile_defaults()).schema,
            }
        )
        return self.async_show_form(step_id="profile", data_schema=schema, errors=errors)

    async def async_step_glucose(self, user_input=None):
        return await glucose_step(self, user_input)

    def finish_profile(self, options):
        parent = self._get_entry()
        if self.source == config_entries.SOURCE_RECONFIGURE:
            subentry = self._get_reconfigure_subentry()
            data = {**options, "profile_id": subentry.data["profile_id"]}
            if not parent.update_listeners:
                return self.async_update_reload_and_abort(
                    parent, subentry, title=self._profile_name, data=data
                )
            return self.async_update_and_abort(
                parent, subentry, title=self._profile_name, data=data
            )
        return self.async_create_entry(
            title=self._profile_name,
            data={**options, "profile_id": self._profile_id},
            unique_id=self._profile_id,
        )

    async def async_step_resolve(self, user_input=None):
        runtime = getattr(self._get_entry(), "runtime_data", None)
        subentry = self._get_reconfigure_subentry()
        if runtime is None or subentry.subentry_id not in runtime.workers:
            return self.async_abort(reason="not_loaded")
        worker = runtime.workers[subentry.subentry_id]
        async with worker.delivery_lock:
            items = [
                item
                for event in worker.queue.data["events"]
                for item in event.get("payload", [])
                if item.get("hold") in ("ambiguous", "identity_conflict")
                or (item.get("inflight") and not durable_identity(item))
            ]
            if not items:
                return self.async_abort(reason="nothing_to_resolve")
            errors = {}
            if user_input:
                target = user_input["measurement"]
                if not user_input.get("confirmed") or target not in {
                    m["externalId"] for m in items
                }:
                    errors["base"] = "confirmation_required"
                else:
                    try:
                        if user_input["resolution"] == "delivered":
                            await worker.queue.acknowledge(target)
                        elif user_input["resolution"] == "not_delivered":

                            def release(data):
                                item = worker.queue.item(data, target)
                                item.pop("hold", None)
                                item.pop("inflight", None)

                            await worker.queue.change(release)
                        else:
                            raise ValueError("invalid resolution")
                    except OSError:
                        errors["base"] = "storage_error"
                    else:
                        worker.wake.set()
                        worker.notify()
                        return self.async_abort(reason="resolved")
        return self.async_show_form(
            step_id="resolve",
            data_schema=vol.Schema(
                {
                    vol.Required("measurement"): selector.SelectSelector(
                        {
                            "options": [
                                {
                                    "value": m["externalId"],
                                    "label": f"{m['type']} · {m['measuredAt']} · {m['externalId'][-8:]}",
                                }
                                for m in items
                            ]
                        }
                    ),
                    vol.Required("resolution"): selector.SelectSelector(
                        {
                            "options": [
                                {
                                    "value": "delivered",
                                    "label": "I verified it exists in HealthLog",
                                },
                                {
                                    "value": "not_delivered",
                                    "label": "I verified it was NOT stored; allow a new attempt",
                                },
                            ]
                        }
                    ),
                    vol.Required("confirmed", default=False): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )
