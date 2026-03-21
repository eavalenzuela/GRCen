import asyncpg
from fastapi import APIRouter, Depends, UploadFile

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import audit_service as audit_svc
from grcen.services.import_service import (
    execute_asset_import,
    execute_relationship_import,
    preview_asset_import,
)

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("/assets/preview")
async def preview_import(
    file: UploadFile,
    _user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = (await file.read()).decode("utf-8")
    fmt = "json" if file.filename and file.filename.endswith(".json") else "csv"
    preview = preview_asset_import(content, fmt)
    return {
        "total_rows": preview.total_rows,
        "valid_rows": preview.valid_rows,
        "errors": preview.errors,
        "sample": preview.sample,
    }


@router.post("/assets/execute")
async def execute_import(
    file: UploadFile,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = (await file.read()).decode("utf-8")
    fmt = "json" if file.filename and file.filename.endswith(".json") else "csv"
    result = await execute_asset_import(pool, content, fmt)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="import",
        entity_type="asset",
        entity_name=file.filename or "assets",
        changes={"created": {"new": result.created}, "errors": {"new": len(result.errors)}},
    )
    return {"created": result.created, "errors": result.errors}


@router.post("/relationships/execute")
async def execute_rel_import(
    file: UploadFile,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = (await file.read()).decode("utf-8")
    fmt = "json" if file.filename and file.filename.endswith(".json") else "csv"
    result = await execute_relationship_import(pool, content, fmt)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="import",
        entity_type="relationship",
        entity_name=file.filename or "relationships",
        changes={"created": {"new": result.created}, "errors": {"new": len(result.errors)}},
    )
    return {"created": result.created, "errors": result.errors}
