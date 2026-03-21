"""Database-backed OIDC configuration with in-memory cache."""

from dataclasses import dataclass

import asyncpg

_DEFAULTS = {
    "issuer_url": "",
    "client_id": "",
    "client_secret": "",
    "scopes": "openid email profile",
    "role_claim": "groups",
    "role_mapping": "{}",
    "default_role": "viewer",
    "display_name": "SSO",
}


@dataclass
class OIDCSettings:
    issuer_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: str = "openid email profile"
    role_claim: str = "groups"
    role_mapping: str = "{}"
    default_role: str = "viewer"
    display_name: str = "SSO"

    @property
    def enabled(self) -> bool:
        return bool(self.issuer_url and self.client_id and self.client_secret)


# Module-level cache
_cache: OIDCSettings | None = None


async def _load(pool: asyncpg.Pool) -> OIDCSettings:
    global _cache
    rows = await pool.fetch("SELECT key, value FROM oidc_config")
    values = {r["key"]: r["value"] for r in rows}
    _cache = OIDCSettings(**{k: values.get(k, v) for k, v in _DEFAULTS.items()})
    return _cache


async def get_settings(pool: asyncpg.Pool) -> OIDCSettings:
    if _cache is None:
        return await _load(pool)
    return _cache


async def reload(pool: asyncpg.Pool) -> OIDCSettings:
    return await _load(pool)


async def update_settings(pool: asyncpg.Pool, **kwargs: str) -> OIDCSettings:
    for key, value in kwargs.items():
        if key in _DEFAULTS:
            await pool.execute(
                """INSERT INTO oidc_config (key, value, updated_at)
                   VALUES ($1, $2, now())
                   ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
                key,
                value,
            )
    return await _load(pool)
