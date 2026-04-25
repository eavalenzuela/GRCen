"""Approval queue and per-type workflow configuration."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import asset as asset_svc
from grcen.services import workflow_service


def _csrf_dep():
    from grcen.routers.pages import _csrf_check
    return _csrf_check


router = APIRouter(tags=["workflow"], dependencies=[Depends(_csrf_dep())])
api_router = APIRouter(prefix="/api/approvals", tags=["workflow"])


# ---- Pages -------------------------------------------------------------

def _templates():
    # Imported lazily to avoid a circular import (pages.py imports workflow_service)
    from grcen.routers.pages import templates
    return templates


@router.get("/approvals", response_class=HTMLResponse)
async def approvals_index(
    request: Request,
    status: str = "pending",
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    if status not in ("pending", "approved", "rejected", "withdrawn", "all"):
        status = "pending"
    items = await workflow_service.list_changes(
        pool, status=None if status == "all" else status,
        organization_id=user.organization_id,
    )
    return _templates().TemplateResponse(
        request,
        "workflow/approvals_list.html",
        context={"user": user, "items": items, "current_status": status},
    )


@router.get("/approvals/{change_id}", response_class=HTMLResponse)
async def approval_detail(
    request: Request,
    change_id: UUID,
    submitted: int | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        return HTMLResponse("Not found", status_code=404)
    target = None
    if change.target_asset_id:
        target = await asset_svc.get_asset(
            pool, change.target_asset_id, organization_id=user.organization_id
        )
    comments = await workflow_service.list_comments(pool, change.id)
    approvals = await workflow_service.list_approvals(pool, change.id)
    asset_type = AssetType(change.asset_type)
    cfg = await workflow_service.get_config(
        pool, asset_type, organization_id=user.organization_id
    )
    already_approved = any(a.approver_id == user.id for a in approvals)
    return _templates().TemplateResponse(
        request,
        "workflow/approvals_detail.html",
        context={
            "user": user,
            "change": change,
            "target": target,
            "just_submitted": bool(submitted),
            "comments": comments,
            "approvals": approvals,
            "required_approvals": cfg.required_approvals,
            "already_approved": already_approved,
        },
    )


@router.post("/approvals/{change_id}/comment")
async def approval_comment(
    change_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    change = await workflow_service.get(
        pool, change_id, organization_id=user.organization_id
    )
    if not change:
        raise HTTPException(status_code=404, detail="Pending change not found")
    form = await request.form()
    body = str(form.get("body", "")).strip()
    if not body:
        return RedirectResponse(f"/approvals/{change_id}", status_code=302)
    try:
        await workflow_service.add_comment(pool, change, user, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/approvals/{change_id}", status_code=302)


@router.post("/approvals/{change_id}/approve")
async def approval_approve(
    change_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.APPROVE)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Pending change not found")
    form = await request.form()
    note = str(form.get("note", "")).strip() or None
    try:
        await workflow_service.approve(pool, change, user, note=note)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/approvals/{change_id}", status_code=302)


@router.post("/approvals/{change_id}/reject")
async def approval_reject(
    change_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.APPROVE)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Pending change not found")
    form = await request.form()
    note = str(form.get("note", "")).strip() or None
    try:
        await workflow_service.reject(pool, change, user, note=note)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/approvals/{change_id}", status_code=302)


@router.post("/approvals/{change_id}/withdraw")
async def approval_withdraw(
    change_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Pending change not found")
    form = await request.form()
    note = str(form.get("note", "")).strip() or None
    try:
        await workflow_service.withdraw(pool, change, user, note=note)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/approvals/{change_id}", status_code=302)


# ---- Admin: per-type workflow config -----------------------------------

@router.get("/admin/workflow", response_class=HTMLResponse)
async def workflow_admin(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    configs = await workflow_service.list_configs(pool, organization_id=user.organization_id)
    rows = []
    for at in sorted(AssetType, key=lambda t: t.value):
        cfg = configs.get(
            at.value,
            workflow_service.WorkflowConfig(
                asset_type=at.value,
                require_approval_create=False,
                require_approval_update=False,
                require_approval_delete=False,
            ),
        )
        rows.append(cfg)
    return _templates().TemplateResponse(
        request,
        "admin/workflow_settings.html",
        context={"user": user, "rows": rows},
    )


@router.post("/admin/workflow")
async def workflow_admin_save(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    for at in AssetType:
        raw = form.get(f"required_{at.value}", "1")
        try:
            required = max(1, int(str(raw)))
        except (ValueError, TypeError):
            required = 1
        role_raw = str(form.get(f"approver_role_{at.value}", "")).strip()
        approver_role = role_raw if role_raw in ("admin", "editor", "viewer", "auditor") else None
        await workflow_service.upsert_config(
            pool,
            at,
            organization_id=user.organization_id,
            require_approval_create=f"create_{at.value}" in form,
            require_approval_update=f"update_{at.value}" in form,
            require_approval_delete=f"delete_{at.value}" in form,
            required_approvals=required,
            require_approval_relationship_create=f"rel_create_{at.value}" in form,
            require_approval_relationship_delete=f"rel_delete_{at.value}" in form,
            approver_role=approver_role,
        )
    return RedirectResponse("/admin/workflow", status_code=302)


# ---- REST API ----------------------------------------------------------

def _change_to_json(change: workflow_service.PendingChange) -> dict:
    return {
        "id": str(change.id),
        "action": change.action,
        "asset_type": change.asset_type,
        "target_asset_id": str(change.target_asset_id) if change.target_asset_id else None,
        "title": change.title,
        "payload": change.payload,
        "status": change.status,
        "submitted_by_username": change.submitted_by_username,
        "submitted_at": change.submitted_at.isoformat(),
        "decided_by_username": change.decided_by_username,
        "decided_at": change.decided_at.isoformat() if change.decided_at else None,
        "decision_note": change.decision_note,
    }


@api_router.get("/", summary="List pending changes")
async def api_list(
    status: str = "pending",
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    items = await workflow_service.list_changes(
        pool, status=None if status == "all" else status,
        organization_id=user.organization_id,
    )
    return [_change_to_json(c) for c in items]


@api_router.get("/{change_id}", summary="Fetch a pending change by id")
async def api_get(
    change_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Not found")
    return _change_to_json(change)


@api_router.post("/{change_id}/approve", summary="Approve a pending change")
async def api_approve(
    change_id: UUID,
    body: dict | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.APPROVE)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Not found")
    note = (body or {}).get("note") if isinstance(body, dict) else None
    try:
        updated, _ = await workflow_service.approve(pool, change, user, note=note)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _change_to_json(updated)


@api_router.post("/{change_id}/reject", summary="Reject a pending change")
async def api_reject(
    change_id: UUID,
    body: dict | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.APPROVE)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Not found")
    note = (body or {}).get("note") if isinstance(body, dict) else None
    try:
        updated = await workflow_service.reject(pool, change, user, note=note)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _change_to_json(updated)


@api_router.post("/{change_id}/withdraw", summary="Withdraw a pending change")
async def api_withdraw(
    change_id: UUID,
    body: dict | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    change = await workflow_service.get(pool, change_id, organization_id=user.organization_id)
    if not change:
        raise HTTPException(status_code=404, detail="Not found")
    note = (body or {}).get("note") if isinstance(body, dict) else None
    try:
        updated = await workflow_service.withdraw(pool, change, user, note=note)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _change_to_json(updated)
