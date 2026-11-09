"""Allowlisted diagnostics: never export configuration or queue contents."""


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = getattr(entry, "runtime_data", None)
    return {
        "version": 2,
        "authentication": ("reauth_required" if runtime.transport.auth_required else "ready")
        if runtime
        else "not_loaded",
        "entry_state": entry.state.value,
        "profile_count": len(entry.subentries),
        "profiles": [
            {"status": worker.status(), "mapping_count": len(worker.options["mappings"])}
            for worker in runtime.workers.values()
        ]
        if runtime
        else [],
    }
