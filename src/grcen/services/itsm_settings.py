"""Database-backed ITSM (ticketing) configuration with in-memory cache.

Mirrors ``smtp_settings`` / ``oidc_settings``: key-value rows in ``itsm_config``
with optional field-level encryption of the API token (``itsm_secrets`` scope).
Drives the external ticketing push (E3): create a Jira/ServiceNow ticket from a
finding and sync ticket status back.
"""

from dataclasses import dataclass

import asyncpg

from grcen.services import encryption_config
from grcen.services.encryption import decrypt_field, encrypt_field

# Supported providers. 'generic' POSTs a simple JSON body to base_url.
PROVIDERS = ("jira", "servicenow", "generic")

_DEFAULTS = {
    "provider": "generic",
    "base_url": "",
    "project_key": "",     # Jira project key, or ServiceNow table (e.g. 'incident')
    "issue_type": "Task",  # Jira issue type
    "auth_user": "",       # Jira/ServiceNow: basic-auth user (email); generic: unused
    "auth_token": "",      # API token (secret)
    "enabled": "false",
}

_SECRET_KEYS = frozenset({"auth_token"})


@dataclass
class ITSMSettings:
    provider: str = "generic"
    base_url: str = ""
    project_key: str = ""
    issue_type: str = "Task"
    auth_user: str = ""
    auth_token: str = ""
    enabled: str = "false"

    @property
    def is_enabled(self) -> bool:
        return self.enabled.lower() == "true" and bool(self.base_url)


_cache: ITSMSettings | None = None


async def _load(pool: asyncpg.Pool) -> ITSMSettings:
    global _cache
    rows = await pool.fetch("SELECT key, value FROM itsm_config")
    values = {r["key"]: r["value"] for r in rows}

    scopes = await encryption_config.get_active_scopes(pool)
    if "itsm_secrets" in scopes:
        for sk in _SECRET_KEYS:
            if sk in values and values[sk]:
                values[sk] = decrypt_field(values[sk], "itsm_secrets")

    _cache = ITSMSettings(**{k: values.get(k, v) for k, v in _DEFAULTS.items()})
    return _cache


async def get_settings(pool: asyncpg.Pool) -> ITSMSettings:
    if _cache is None:
        return await _load(pool)
    return _cache


async def reload(pool: asyncpg.Pool) -> ITSMSettings:
    return await _load(pool)


async def update_settings(pool: asyncpg.Pool, **kwargs: str) -> ITSMSettings:
    scopes = await encryption_config.get_active_scopes(pool)
    for key, value in kwargs.items():
        if key not in _DEFAULTS:
            continue
        if key == "provider" and value not in PROVIDERS:
            value = "generic"
        store_value = value
        if key in _SECRET_KEYS and "itsm_secrets" in scopes and value:
            store_value = encrypt_field(value, "itsm_secrets")
        await pool.execute(
            """INSERT INTO itsm_config (key, value, updated_at)
               VALUES ($1, $2, now())
               ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
            key,
            store_value,
        )
    return await _load(pool)
