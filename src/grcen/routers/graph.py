from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.graph import GraphResponse
from grcen.services.graph import get_asset_graph

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/{asset_id}", response_model=GraphResponse)
async def graph(
    asset_id: UUID,
    depth: int = 1,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW_GRAPH)),
):
    return await get_asset_graph(pool, asset_id, depth)
