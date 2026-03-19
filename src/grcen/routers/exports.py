import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from grcen.models.asset import AssetStatus, AssetType
from grcen.models.user import User
from grcen.routers.deps import get_current_user, get_db
from grcen.services.export_service import export_assets

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/assets")
async def export(
    format: str = "csv",
    types: str | None = None,
    status: AssetStatus | None = None,
    columns: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    asset_types = [AssetType(t) for t in types.split(",")] if types else None
    cols = columns.split(",") if columns else None
    content = await export_assets(
        pool, format=format, asset_types=asset_types, status=status, columns=cols
    )

    if format == "json":
        media_type = "application/json"
        filename = "assets.json"
    else:
        media_type = "text/csv"
        filename = "assets.csv"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
