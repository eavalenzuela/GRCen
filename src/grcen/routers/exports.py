import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
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
    status: str | None = None,
    columns: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    # The /exports form sends empty strings for "all"; comma lists may carry
    # spaces and mixed case. Bad values are a 400, not a 500.
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be csv or json")
    status_filter: AssetStatus | None = None
    if status and status.strip():
        try:
            status_filter = AssetStatus(status.strip().lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status: {status}") from None
    asset_types: list[AssetType] | None = None
    if types and types.strip():
        asset_types = []
        for raw in types.split(","):
            name = raw.strip().lower().replace(" ", "_")
            if not name:
                continue
            try:
                asset_types.append(AssetType(name))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Unknown asset type: {raw.strip()}") from None
        if not asset_types:
            asset_types = None
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns and columns.strip() else None
    status = status_filter
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
