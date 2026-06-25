"""Register framework pages: the index and the per-register alias (Slice 1).

The canonical register surface is the existing ``/assets?type=X`` list. These
routes add a named landing page (``/registers``) and a pretty alias
(``/registers/{slug}``) that 302-redirects to the canonical list with the
register's curated columns + default sort applied — or to a richer bespoke page
(risk/framework/control) via ``canonical_path``.
"""
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen import registers
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import alert_service as alert_svc
from grcen.services import register_service

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])


@router.get("/registers", response_class=HTMLResponse)
async def registers_index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    counts = await register_service.register_counts(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    groups = [
        (label, [registers.REGISTERS[t] for t in types if t in registers.REGISTERS])
        for label, types in registers.GROUPS
    ]
    return templates.TemplateResponse(request, "registers/index.html", context={
        "user": user,
        "notif_count": notif_count,
        "groups": groups,
        "counts": counts,
    })


@router.get("/registers/{slug}")
async def register_alias(
    slug: str,
    user: User = Depends(require_permission(Permission.VIEW)),
):
    reg = registers.by_slug(slug)
    if reg is None:
        raise HTTPException(status_code=404, detail="Unknown register")
    if reg.canonical_path:
        return RedirectResponse(reg.canonical_path, status_code=302)
    target = (
        f"/assets?type={reg.type.value}&columns=curated"
        f"&sort={reg.default_sort}&order={reg.default_order}"
    )
    return RedirectResponse(target, status_code=302)
