"""REST API for the data access log."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import access_log_service

router = APIRouter(prefix="/api/access-log", tags=["access-log"])


@router.get("/", summary="Query the data access (read) log")
async def list_access_log(
    user_id: UUID | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_AUDIT)),
):
    entries = await access_log_service.query(
        pool,
        organization_id=user.organization_id,
        user_id=user_id,
        entity_type=entity_type,
        action=action,
        since=since,
        until=until,
        limit=limit,
    )
    # UUID and datetime → string for JSON
    out = []
    for e in entries:
        row = {k: v for k, v in e.items()}
        row["id"] = str(row["id"]) if row.get("id") else None
        row["user_id"] = str(row["user_id"]) if row.get("user_id") else None
        row["entity_id"] = str(row["entity_id"]) if row.get("entity_id") else None
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
        out.append(row)
    return out
