import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from grcen.models.asset import AssetStatus, AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import access_log_service
from grcen.services.export_service import export_assets

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/assets")
async def export(
    request: Request,
    format: str = "csv",
    types: str | None = None,
    status: AssetStatus | None = None,
    columns: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    asset_types = [AssetType(t) for t in types.split(",")] if types else None
    cols = columns.split(",") if columns else None
    content = await export_assets(
        pool, format=format, asset_types=asset_types, status=status,
        columns=cols, user=user, organization_id=user.organization_id,
    )
    await access_log_service.record(
        pool, user=user, action="export",
        entity_type="asset",
        entity_name=f"assets.{format} ({','.join(t.value for t in asset_types) if asset_types else 'all types'})",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
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
