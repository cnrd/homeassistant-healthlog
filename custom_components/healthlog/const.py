"""Integration constants."""

DOMAIN = "healthlog"
VERSION = "1.0.0-beta.1"
DEFAULTS = {
    "settling_delay": 10,
    "freshness_window": 120,
    "batch_delay": 60,
    "queue_capacity": 500,
    "trigger_mode": "timestamp",
    "trigger_attribute": "",
    "description": "",
}
SIGNAL = f"{DOMAIN}_status"
SIGNAL_PROFILES = f"{DOMAIN}_profiles"
SUBENTRY_PROFILE = "profile"
