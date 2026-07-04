"""Asset watchers — per-user subscriptions to change notifications.

A user watches an asset from its detail page; when the asset is updated or
deleted, every watcher (except the actor themselves) gets a targeted in-app
notification (``notifications.user_id``), closing the "someone changed the
policy I audit" gap without polling the audit log. Watches are org-scoped and
cascade away with the asset or the user.
"""

from __future__ import annotations

import logging
import uuid
from uuid import UUID

import asyncpg

log = logging.getLogger(__name__)


async def watch(
    pool: asyncpg.Pool, asset_id: UUID, user_id: UUID, *, organization_id: UUID
) -> bool:
    """Subscribe a user to an asset. Returns False if the asset isn't visible
    to this org (no cross-tenant watches)."""
    row = await pool.fetchrow(
        "SELECT 1 FROM assets WHERE id = $1 AND organization_id = $2",
        asset_id, organization_id,
    )
    if not row:
        return False
    await pool.execute(
        """INSERT INTO asset_watchers (asset_id, user_id, organization_id)
           VALUES ($1, $2, $3)
           ON CONFLICT (asset_id, user_id) DO NOTHING""",
        asset_id, user_id, organization_id,
    )
    return True


async def unwatch(pool: asyncpg.Pool, asset_id: UUID, user_id: UUID) -> bool:
    result = await pool.execute(
        "DELETE FROM asset_watchers WHERE asset_id = $1 AND user_id = $2",
        asset_id, user_id,
    )
    return result == "DELETE 1"


async def is_watching(pool: asyncpg.Pool, asset_id: UUID, user_id: UUID) -> bool:
    row = await pool.fetchrow(
        "SELECT 1 FROM asset_watchers WHERE asset_id = $1 AND user_id = $2",
        asset_id, user_id,
    )
    return row is not None


async def watcher_ids(pool: asyncpg.Pool, asset_id: UUID) -> list[UUID]:
    rows = await pool.fetch(
        "SELECT user_id FROM asset_watchers WHERE asset_id = $1", asset_id
    )
    return [r["user_id"] for r in rows]


async def watched_assets(
    pool: asyncpg.Pool, user_id: UUID, *, organization_id: UUID
) -> list[dict]:
    """The assets a user watches (org-scoped), newest watch first."""
    rows = await pool.fetch(
        """SELECT a.id, a.name, a.type, a.status, w.created_at AS watched_at
           FROM asset_watchers w
           JOIN assets a ON a.id = w.asset_id
           WHERE w.user_id = $1 AND w.organization_id = $2
           ORDER BY w.created_at DESC""",
        user_id, organization_id,
    )
    return [dict(r) for r in rows]


async def notify_watchers(
    pool: asyncpg.Pool,
    *,
    asset_id: UUID,
    asset_name: str,
    organization_id: UUID,
    action: str,
    actor_id: UUID | None = None,
    actor_username: str = "",
    ids: list[UUID] | None = None,
) -> int:
    """Insert a targeted in-app notification for each watcher.

    The actor is skipped (you don't need to hear about your own edit).
    Best-effort: failures are logged, never raised into the write path.
    Delete paths pass ``ids`` captured *before* the delete, since the watch
    rows cascade away with the asset. Returns the number of watchers notified.
    """
    try:
        if ids is None:
            ids = await watcher_ids(pool, asset_id)
        notified = 0
        verb = {"update": "updated", "delete": "deleted"}.get(action, action)
        by = f" by {actor_username}" if actor_username else ""
        for uid in ids:
            if actor_id is not None and uid == actor_id:
                continue
            await pool.execute(
                """INSERT INTO notifications
                       (id, alert_id, user_id, title, message, organization_id)
                   VALUES ($1, NULL, $2, $3, $4, $5)""",
                uuid.uuid4(),
                uid,
                f"Watched asset {verb}: {asset_name}"[:255],
                f"'{asset_name}' was {verb}{by}.",
                organization_id,
            )
            notified += 1
        return notified
    except Exception:
        log.exception("Failed to notify watchers for asset %s", asset_id)
        return 0
