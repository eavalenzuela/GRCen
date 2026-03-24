"""Database-backed encryption configuration.

Stores which profile and scopes are active in the ``encryption_config``
table so that operators can change the encryption posture at runtime
through the admin UI without restarting the application.
"""

from __future__ import annotations

import asyncpg

from grcen.services.encryption import is_encryption_enabled
from grcen.services.encryption_scopes import ALL_PROFILES, ALL_SCOPES

# Module-level cache — invalidated by :func:`reload`.
_cache: set[str] | None = None
_profile_cache: str | None = None


async def _load(pool: asyncpg.Pool) -> tuple[str, set[str]]:
    global _cache, _profile_cache
    rows = await pool.fetch("SELECT key, value FROM encryption_config")
    cfg = {r["key"]: r["value"] for r in rows}

    profile = cfg.get("profile", "")
    raw_scopes = cfg.get("enabled_scopes", "")
    _profile_cache = profile
    _cache = {s.strip() for s in raw_scopes.split(",") if s.strip()} if raw_scopes else set()
    return profile, _cache


async def get_active_scopes(pool: asyncpg.Pool) -> set[str]:
    """Return the set of currently enabled scope names.

    Returns an empty set when encryption is disabled (no key configured)
    or when no scopes have been activated by an admin.
    """
    if not is_encryption_enabled():
        return set()
    if _cache is None:
        _, scopes = await _load(pool)
        return scopes
    return _cache


async def get_active_profile(pool: asyncpg.Pool) -> str:
    """Return the active profile name, or ``""`` if none is set."""
    if _profile_cache is None:
        profile, _ = await _load(pool)
        return profile
    return _profile_cache


async def is_scope_active(pool: asyncpg.Pool, scope_name: str) -> bool:
    return scope_name in await get_active_scopes(pool)


async def set_profile(
    pool: asyncpg.Pool, profile_name: str, custom_scopes: list[str] | None = None
) -> set[str]:
    """Persist the chosen profile and its scopes.

    For the ``custom`` profile, *custom_scopes* determines which scopes
    are enabled.  For named profiles the scope list comes from the
    profile definition.
    """
    if profile_name == "custom":
        scopes = {s for s in (custom_scopes or []) if s in ALL_SCOPES}
    elif profile_name in ALL_PROFILES:
        scopes = set(ALL_PROFILES[profile_name].scope_names)
    else:
        scopes = set()

    scopes_csv = ",".join(sorted(scopes))
    await _upsert(pool, "profile", profile_name)
    await _upsert(pool, "enabled_scopes", scopes_csv)
    await reload(pool)
    return scopes


async def clear(pool: asyncpg.Pool) -> None:
    """Disable all encryption scopes (remove profile)."""
    await _upsert(pool, "profile", "")
    await _upsert(pool, "enabled_scopes", "")
    await reload(pool)


async def reload(pool: asyncpg.Pool) -> None:
    await _load(pool)


async def _upsert(pool: asyncpg.Pool, key: str, value: str) -> None:
    await pool.execute(
        """INSERT INTO encryption_config (key, value, updated_at)
           VALUES ($1, $2, now())
           ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
        key,
        value,
    )
