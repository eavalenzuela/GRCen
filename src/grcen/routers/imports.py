import json

import asyncpg
from fastapi import APIRouter, Body, Depends, Form, UploadFile

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import audit_service as audit_svc
from grcen.services.import_service import (
    execute_asset_import,
    execute_relationship_import,
    preview_asset_import,
    preview_relationship_import,
)

router = APIRouter(prefix="/api/imports", tags=["imports"])


# ── file-upload flows (used by the /imports UI) ───────────────────────────


@router.post("/assets/preview", summary="Preview an asset import file")
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


@router.post("/assets/execute", summary="Execute an asset import (with optional dry-run)")
async def execute_import(
    file: UploadFile,
    dry_run: bool = Form(False),
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = (await file.read()).decode("utf-8")
    fmt = "json" if file.filename and file.filename.endswith(".json") else "csv"
    result = await execute_asset_import(pool, content, fmt, dry_run=dry_run, organization_id=user.organization_id)
    if not dry_run:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="import",
            entity_type="asset",
            entity_name=file.filename or "assets",
            changes={"created": {"new": result.created}, "errors": {"new": len(result.errors)}},
        )
    return {"created": result.created, "errors": result.errors, "dry_run": dry_run}


@router.post("/relationships/preview", summary="Preview a relationship import file")
async def preview_rel_import(
    file: UploadFile,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = (await file.read()).decode("utf-8")
    fmt = "json" if file.filename and file.filename.endswith(".json") else "csv"
    preview = await preview_relationship_import(pool, content, fmt)
    return {
        "total_rows": preview.total_rows,
        "valid_rows": preview.valid_rows,
        "errors": preview.errors,
        "sample": preview.sample,
    }


@router.post("/relationships/execute", summary="Execute a relationship import (with optional dry-run)")
async def execute_rel_import(
    file: UploadFile,
    dry_run: bool = Form(False),
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = (await file.read()).decode("utf-8")
    fmt = "json" if file.filename and file.filename.endswith(".json") else "csv"
    result = await execute_relationship_import(pool, content, fmt, dry_run=dry_run, organization_id=user.organization_id)
    if not dry_run:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="import",
            entity_type="relationship",
            entity_name=file.filename or "relationships",
            changes={"created": {"new": result.created}, "errors": {"new": len(result.errors)}},
        )
    return {"created": result.created, "errors": result.errors, "dry_run": dry_run}


# ── JSON-body bulk flows (for programmatic API clients) ───────────────────


@router.post(
    "/assets/bulk",
    summary="Bulk-create assets from a JSON array",
    description=(
        "POST a JSON array of asset rows. Same row shape as the CSV import "
        "(keys: type, name, description, status, owner, plus any custom fields). "
        "Set `dry_run=true` to validate without writing."
    ),
)
async def bulk_assets(
    rows: list[dict] = Body(..., embed=False),
    dry_run: bool = False,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = json.dumps(rows)
    result = await execute_asset_import(pool, content, "json", dry_run=dry_run, organization_id=user.organization_id)
    if not dry_run and result.created:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="import",
            entity_type="asset",
            entity_name="bulk",
            changes={"created": {"new": result.created}, "errors": {"new": len(result.errors)}},
        )
    return {"created": result.created, "errors": result.errors, "dry_run": dry_run}


@router.post(
    "/relationships/bulk",
    summary="Bulk-create relationships from a JSON array",
    description=(
        "POST a JSON array of relationship rows with keys: source_name, source_type, "
        "target_name, target_type, relationship_type, description. "
        "Set `dry_run=true` to validate without writing."
    ),
)
async def bulk_relationships(
    rows: list[dict] = Body(..., embed=False),
    dry_run: bool = False,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    content = json.dumps(rows)
    result = await execute_relationship_import(pool, content, "json", dry_run=dry_run, organization_id=user.organization_id)
    if not dry_run and result.created:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="import",
            entity_type="relationship",
            entity_name="bulk",
            changes={"created": {"new": result.created}, "errors": {"new": len(result.errors)}},
        )
    return {"created": result.created, "errors": result.errors, "dry_run": dry_run}
