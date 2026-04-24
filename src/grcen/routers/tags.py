"""Cross-cutting tag REST API."""

from dataclasses import asdict

import asyncpg
from fastapi import APIRouter, Depends

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import tag_service

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("/", summary="List all tags with asset counts")
async def list_tags(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    rows = await tag_service.list_tags_with_counts(pool)
    return [asdict(r) for r in rows]
