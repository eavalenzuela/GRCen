"""Field-level redaction for sensitive custom fields.

A custom field marked ``sensitive=True`` (in ``custom_fields.py`` *or* via a
runtime override in ``sensitive_field_overrides``) is visible only to users
whose role grants ``Permission.VIEW_PII``. For everyone else we replace the
value with a placeholder at every egress point.

The runtime override layer lets admins promote a field to sensitive without a
code change. Code defaults are merged with org-scoped overrides at lookup
time; an explicit ``sensitive=False`` override can also de-classify a field.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

import asyncpg

from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission, has_permission

REDACTED_PLACEHOLDER = "[redacted]"


def can_view_pii(user: User | None) -> bool:
    if user is None:
        return False
    return has_permission(user.role, Permission.VIEW_PII)


def code_sensitive_field_names(asset_type: AssetType) -> set[str]:
    """Fields marked sensitive in the code (``custom_fields.py``)."""
    return {f.name for f in CUSTOM_FIELDS.get(asset_type, []) if f.sensitive}


# Backwards-compat alias: synchronous code paths that don't have a pool still
# fall back to the code-only view. New callers should use
# ``effective_sensitive_field_names`` and pass a pool.
def sensitive_field_names(asset_type: AssetType) -> set[str]:
    return code_sensitive_field_names(asset_type)


async def effective_sensitive_field_names(
    pool: asyncpg.Pool, asset_type: AssetType, organization_id: UUID
) -> set[str]:
    """Code defaults + per-org overrides, with overrides winning."""
    base = code_sensitive_field_names(asset_type)
    rows = await pool.fetch(
        """SELECT field_name, sensitive FROM sensitive_field_overrides
           WHERE organization_id = $1 AND asset_type = $2""",
        organization_id, asset_type.value,
    )
    add = {r["field_name"] for r in rows if r["sensitive"]}
    drop = {r["field_name"] for r in rows if not r["sensitive"]}
    return (base | add) - drop


def redact_metadata(
    metadata: dict[str, Any] | None,
    asset_type: AssetType | str | None,
    user: User | None,
    *,
    sensitive_fields: set[str] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``metadata`` with sensitive fields masked for the user.

    Non-mutating. If the user has VIEW_PII (or the asset type has no sensitive
    fields) the original dict is returned unchanged. Pass ``sensitive_fields``
    to override the code-default lookup — the async wrapper does this when a
    pool is available so per-org overrides take effect.
    """
    if not metadata:
        return metadata or {}
    if can_view_pii(user):
        return metadata

    if isinstance(asset_type, str):
        try:
            asset_type = AssetType(asset_type)
        except ValueError:
            return metadata

    if sensitive_fields is None:
        sensitive_fields = sensitive_field_names(asset_type) if asset_type else set()
    if not sensitive_fields:
        return metadata

    masked = deepcopy(metadata)
    for key in list(masked.keys()):
        if key in sensitive_fields and masked[key] not in (None, "", [], {}):
            masked[key] = REDACTED_PLACEHOLDER
    return masked


async def redact_metadata_async(
    pool: asyncpg.Pool,
    metadata: dict[str, Any] | None,
    asset_type: AssetType | str | None,
    user: User | None,
    organization_id: UUID,
) -> dict[str, Any]:
    """Async variant that picks up per-org overrides."""
    if not metadata or can_view_pii(user) or asset_type is None:
        return redact_metadata(metadata, asset_type, user)
    if isinstance(asset_type, str):
        try:
            asset_type = AssetType(asset_type)
        except ValueError:
            return metadata
    fields = await effective_sensitive_field_names(pool, asset_type, organization_id)
    return redact_metadata(metadata, asset_type, user, sensitive_fields=fields)


# ── overrides admin API ─────────────────────────────────────────────────


async def list_overrides(
    pool: asyncpg.Pool, organization_id: UUID
) -> dict[tuple[str, str], bool]:
    rows = await pool.fetch(
        """SELECT asset_type, field_name, sensitive
           FROM sensitive_field_overrides WHERE organization_id = $1""",
        organization_id,
    )
    return {(r["asset_type"], r["field_name"]): r["sensitive"] for r in rows}


async def upsert_override(
    pool: asyncpg.Pool,
    organization_id: UUID,
    asset_type: AssetType,
    field_name: str,
    sensitive: bool,
) -> None:
    await pool.execute(
        """INSERT INTO sensitive_field_overrides
               (organization_id, asset_type, field_name, sensitive, updated_at)
           VALUES ($1, $2, $3, $4, now())
           ON CONFLICT (organization_id, asset_type, field_name) DO UPDATE SET
               sensitive = EXCLUDED.sensitive,
               updated_at = now()""",
        organization_id, asset_type.value, field_name, sensitive,
    )


async def clear_override(
    pool: asyncpg.Pool,
    organization_id: UUID,
    asset_type: AssetType,
    field_name: str,
) -> None:
    """Drop the override and fall back to the code default."""
    await pool.execute(
        """DELETE FROM sensitive_field_overrides
           WHERE organization_id = $1 AND asset_type = $2 AND field_name = $3""",
        organization_id, asset_type.value, field_name,
    )
