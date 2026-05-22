"""Tag vocabulary pages (list, rename, delete)."""

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import (
    _csrf_check,
    templates,
)
from grcen.routers.deps import (
    get_db,
    require_permission,
)
from grcen.services import (
    alert_service as alert_svc,
    audit_service as audit_svc,
    tag_service,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

@router.get("/tags", response_class=HTMLResponse)
async def tags_index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
    flash: str | None = None,
):
    tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(
        request,
        "tags/index.html",
        context={
            "user": user,
            "tags": tags,
            "flash": flash_ctx,
            "notif_count": notif_count,
        },
    )


@router.post("/tags/{old}/rename")
async def tag_rename(
    old: str,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    new = str(form.get("new_name", "")).strip()
    if not new:
        return RedirectResponse("/tags?flash=fail:New name required", status_code=302)
    affected = await tag_service.rename_tag(pool, old, new, organization_id=user.organization_id)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="tag_rename",
        entity_type="tag",
        entity_name=old,
        changes={"old": {"old": old}, "new": {"new": new}, "assets_updated": {"new": affected}},
    )
    return RedirectResponse(
        f"/tags?flash=ok:Renamed '{old}' → '{new}' on {affected} asset(s)",
        status_code=302,
    )


@router.post("/tags/{name}/delete")
async def tag_delete(
    name: str,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    affected = await tag_service.delete_tag(pool, name, organization_id=user.organization_id)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="tag_delete",
        entity_type="tag",
        entity_name=name,
        changes={"assets_updated": {"new": affected}},
    )
    return RedirectResponse(
        f"/tags?flash=ok:Removed '{name}' from {affected} asset(s)",
        status_code=302,
    )


# --- Compliance Framework dashboards ---


