"""Executive board pack: preview + narrative editor + branded PDF."""
from urllib.parse import quote

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import (
    access_log_service,
    alert_service as alert_svc,
    board_service,
    pdf_service,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])


def _period(request: Request) -> str:
    return (request.query_params.get("period") or "current").strip() or "current"


@router.get("/reports/executive", response_class=HTMLResponse)
async def executive_report(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    period = _period(request)
    data = await board_service.gather(pool, organization_id=user.organization_id)
    narratives = await board_service.get_narratives(
        pool, organization_id=user.organization_id, period=period)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id)
    flash = request.query_params.get("flash")
    return templates.TemplateResponse(
        request, "reports/executive.html",
        context={
            "user": user, "data": data, "narratives": narratives, "period": period,
            "sections": board_service.SECTIONS,
            "flash": ({"message": flash.split(":", 1)[-1]} if flash else None),
            "notif_count": notif_count,
        },
    )


@router.post("/reports/executive")
async def save_narrative(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    period = str(form.get("period", "current")).strip() or "current"
    section = str(form.get("section", "")).strip()
    if section in board_service.SECTIONS:
        await board_service.set_narrative(
            pool, organization_id=user.organization_id, period=period,
            section=section, body=str(form.get("body", "")))
    return RedirectResponse(
        f"/reports/executive?period={quote(period)}&flash=" + quote("ok:Saved"),
        status_code=302)


@router.get("/reports/board-pack.pdf")
async def board_pack_pdf(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    period = _period(request)
    pdf = await pdf_service.render_board_pack(
        pool, organization_id=user.organization_id, period=period)
    await access_log_service.record(
        pool, user=user, action="export", entity_type="report",
        entity_name=f"board-pack-{period}.pdf", path=str(request.url.path),
        ip_address=request.client.host if request.client else None)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="board-pack-{period}.pdf"'})
