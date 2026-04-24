"""Database-backed SMTP configuration with in-memory cache.

Mirrors the shape of ``oidc_settings`` / ``saml_settings``: key-value rows in
``smtp_config`` with optional field-level encryption of the password.
"""

from dataclasses import dataclass

import asyncpg

from grcen.services import encryption_config
from grcen.services.encryption import decrypt_field, encrypt_field

_DEFAULTS = {
    "host": "",
    "port": "587",
    "username": "",
    "password": "",
    "from_address": "",
    "from_name": "GRCen",
    "use_starttls": "true",
    "use_ssl": "false",
    "enabled": "false",
}

_SECRET_KEYS = frozenset({"password"})


@dataclass
class SMTPSettings:
    host: str = ""
    port: str = "587"
    username: str = ""
    password: str = ""
    from_address: str = ""
    from_name: str = "GRCen"
    use_starttls: str = "true"
    use_ssl: str = "false"
    enabled: str = "false"

    @property
    def is_enabled(self) -> bool:
        return self.enabled.lower() == "true" and bool(self.host and self.from_address)

    @property
    def port_int(self) -> int:
        try:
            return int(self.port)
        except (TypeError, ValueError):
            return 587

    @property
    def starttls(self) -> bool:
        return self.use_starttls.lower() == "true"

    @property
    def ssl(self) -> bool:
        return self.use_ssl.lower() == "true"


_cache: SMTPSettings | None = None


async def _load(pool: asyncpg.Pool) -> SMTPSettings:
    global _cache
    rows = await pool.fetch("SELECT key, value FROM smtp_config")
    values = {r["key"]: r["value"] for r in rows}

    scopes = await encryption_config.get_active_scopes(pool)
    if "smtp_secrets" in scopes:
        for sk in _SECRET_KEYS:
            if sk in values and values[sk]:
                values[sk] = decrypt_field(values[sk], "smtp_secrets")

    _cache = SMTPSettings(**{k: values.get(k, v) for k, v in _DEFAULTS.items()})
    return _cache


async def get_settings(pool: asyncpg.Pool) -> SMTPSettings:
    if _cache is None:
        return await _load(pool)
    return _cache


async def reload(pool: asyncpg.Pool) -> SMTPSettings:
    return await _load(pool)


async def update_settings(pool: asyncpg.Pool, **kwargs: str) -> SMTPSettings:
    scopes = await encryption_config.get_active_scopes(pool)
    for key, value in kwargs.items():
        if key in _DEFAULTS:
            store_value = value
            if key in _SECRET_KEYS and "smtp_secrets" in scopes and value:
                store_value = encrypt_field(value, "smtp_secrets")
            await pool.execute(
                """INSERT INTO smtp_config (key, value, updated_at)
                   VALUES ($1, $2, now())
                   ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
                key,
                store_value,
            )
    return await _load(pool)
