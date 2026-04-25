"""Data access log — records *reads* for compliance (HIPAA, SOC2 CC6, etc.).

Writes are already captured by ``audit_service``. This module handles views,
downloads, exports, and other egress events. Each call is best-effort: a
failed insert must never block the request being served.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

import asyncpg

from grcen.models.user import User

log = logging.getLogger(__name__)


async def record(
    pool: asyncpg.Pool,
    *,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    entity_name: str | None = None,
    path: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Best-effort access-log insert. Swallows DB errors after logging."""
    try:
        if user is not None:
            org_id = user.organization_id
        else:
            from grcen.services import organization_service
            org_id = await organization_service.get_default_org_id(pool)
        await pool.execute(
            """INSERT INTO data_access_log
                   (id, user_id, username, action, entity_type, entity_id,
                    entity_name, path, ip_address, organization_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            uuid.uuid4(),
            user.id if user else None,
            user.username if user else "anonymous",
            action,
            entity_type,
            entity_id,
            (entity_name or "")[:255],
            (path or "")[:400],
            (ip_address or "")[:64],
            org_id,
        )
    except Exception:
        log.exception("Failed to record access-log entry")


async def query(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    vals: list = []
    idx = 1
    if organization_id is not None:
        clauses.append(f"organization_id = ${idx}")
        vals.append(organization_id)
        idx += 1
    if user_id:
        clauses.append(f"user_id = ${idx}")
        vals.append(user_id)
        idx += 1
    if entity_type:
        clauses.append(f"entity_type = ${idx}")
        vals.append(entity_type)
        idx += 1
    if action:
        clauses.append(f"action = ${idx}")
        vals.append(action)
        idx += 1
    if since:
        clauses.append(f"created_at >= ${idx}")
        vals.append(since)
        idx += 1
    if until:
        clauses.append(f"created_at < ${idx}")
        vals.append(until)
        idx += 1
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = max(1, min(int(limit), 500))
    vals.append(limit)
    rows = await pool.fetch(
        f"""SELECT id, user_id, username, action, entity_type, entity_id,
                   entity_name, path, ip_address, created_at
            FROM data_access_log
            {where}
            ORDER BY created_at DESC
            LIMIT ${idx}""",
        *vals,
    )
    return [dict(r) for r in rows]
