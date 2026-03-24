import json
from datetime import datetime
from uuid import UUID

import asyncpg

from grcen.services import encryption_config
from grcen.services.encryption import encrypt_field

# Module-level config cache: {entity_type: (enabled, field_level)}
_config_cache: dict[str, tuple[bool, bool]] | None = None


async def _load_config(pool: asyncpg.Pool) -> dict[str, tuple[bool, bool]]:
    global _config_cache
    rows = await pool.fetch("SELECT entity_type, enabled, field_level FROM audit_config")
    _config_cache = {r["entity_type"]: (r["enabled"], r["field_level"]) for r in rows}
    return _config_cache


async def get_config(pool: asyncpg.Pool) -> dict[str, tuple[bool, bool]]:
    if _config_cache is None:
        return await _load_config(pool)
    return _config_cache


async def reload_config(pool: asyncpg.Pool) -> None:
    await _load_config(pool)


# --- Serialization & diff helpers ---


def _serialize(val):
    if val is None:
        return None
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, dict):
        return val
    if hasattr(val, "value"):  # enums
        return val.value
    return val


def compute_diff(old: dict, new: dict, fields: list[str]) -> dict:
    diff = {}
    for f in fields:
        old_val = _serialize(old.get(f))
        new_val = _serialize(new.get(f))
        if old_val != new_val:
            diff[f] = {"old": old_val, "new": new_val}
    return diff


def create_snapshot(obj: dict, fields: list[str]) -> dict:
    return {f: {"new": _serialize(obj.get(f))} for f in fields if obj.get(f) is not None}


def delete_snapshot(obj: dict, fields: list[str]) -> dict:
    return {f: {"old": _serialize(obj.get(f))} for f in fields if obj.get(f) is not None}


# --- PII sanitization for audit snapshots ---

# Fields covered by encryption scopes that may appear in audit diffs.
_PII_SCOPE_FIELDS: dict[str, set[str]] = {
    "user_pii": {"email"},
    "session_pii": {"ip_address", "user_agent"},
}


async def _sanitize_changes(pool: asyncpg.Pool, changes: dict | None) -> dict | None:
    """Encrypt PII values inside audit change dicts when audit_pii is active."""
    if not changes:
        return changes
    scopes = await encryption_config.get_active_scopes(pool)
    if "audit_pii" not in scopes:
        return changes

    pii_fields: set[str] = set()
    for scope_name, fields in _PII_SCOPE_FIELDS.items():
        if scope_name in scopes:
            pii_fields.update(fields)

    if not pii_fields:
        return changes

    sanitized = {}
    for field, val in changes.items():
        if field in pii_fields and isinstance(val, dict):
            sanitized[field] = {
                k: encrypt_field(str(v), "audit_pii") if v is not None else v
                for k, v in val.items()
            }
        else:
            sanitized[field] = val
    return sanitized


# --- Core logging ---


async def log_audit_event(
    pool: asyncpg.Pool,
    *,
    user_id: UUID | None,
    username: str,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    entity_name: str | None = None,
    changes: dict | None = None,
) -> None:
    config = await get_config(pool)
    entry = config.get(entity_type)
    if not entry or not entry[0]:  # not enabled
        return
    final_changes = changes if entry[1] else None  # strip if field_level disabled
    final_changes = await _sanitize_changes(pool, final_changes)
    await pool.execute(
        """INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, entity_name, changes)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        user_id,
        username,
        action,
        entity_type,
        entity_id,
        entity_name,
        json.dumps(final_changes) if final_changes else None,
    )


# --- Query functions for admin UI ---


async def list_audit_logs(
    pool: asyncpg.Pool,
    *,
    entity_type: str | None = None,
    action: str | None = None,
    username: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    where_parts: list[str] = []
    vals: list = []
    idx = 1

    if entity_type:
        where_parts.append(f"entity_type = ${idx}")
        vals.append(entity_type)
        idx += 1
    if action:
        where_parts.append(f"action = ${idx}")
        vals.append(action)
        idx += 1
    if username:
        where_parts.append(f"username ILIKE ${idx}")
        vals.append(f"%{username}%")
        idx += 1

    where_clause = " AND ".join(where_parts) if where_parts else "TRUE"

    total = await pool.fetchval(
        f"SELECT count(*) FROM audit_log WHERE {where_clause}", *vals
    )

    vals.append(page_size)
    vals.append((page - 1) * page_size)
    rows = await pool.fetch(
        f"SELECT * FROM audit_log WHERE {where_clause} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
        *vals,
    )
    return [dict(r) for r in rows], total


async def get_audit_config_all(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM audit_config ORDER BY entity_type")
    return [dict(r) for r in rows]


async def update_audit_config(
    pool: asyncpg.Pool, entity_type: str, *, enabled: bool, field_level: bool
) -> None:
    await pool.execute(
        "UPDATE audit_config SET enabled = $1, field_level = $2, updated_at = now() WHERE entity_type = $3",
        enabled,
        field_level,
        entity_type,
    )
    await reload_config(pool)
