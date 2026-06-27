"""Risk appetite: admin config page + breach API."""
from urllib.parse import quote

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import alert_service as alert_svc, appetite_service

page_router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])
api_router = APIRouter(prefix="/api/risk-appetite", tags=["risk-appetite"])

CATEGORIES = ["security", "compliance", "operational", "financial",
              "reputational", "strategic"]


def _flash(flash: str | None) -> dict | None:
    if not flash:
        return None
    ok, _, message = flash.partition(":")
    return {"ok": ok == "ok", "message": message or flash}


@page_router.get("/admin/risk-appetite", response_class=HTMLResponse)
async def appetite_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    appetite = await appetite_service.get_appetite(pool, organization_id=user.organization_id)
    summary = await appetite_service.breach_summary(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request, "admin/risk_appetite.html",
        context={
            "user": user, "appetite": appetite, "summary": summary,
            "categories": CATEGORIES, "flash": _flash(request.query_params.get("flash")),
            "notif_count": notif_count,
        },
    )


@page_router.post("/admin/risk-appetite")
async def appetite_set(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    category = str(form.get("risk_category", "")).strip()
    if str(form.get("action", "set")) == "delete":
        await appetite_service.delete_appetite(
            pool, organization_id=user.organization_id, risk_category=category)
        return RedirectResponse(
            "/admin/risk-appetite?flash=" + quote("ok:Band removed"), status_code=302)
    try:
        max_score = int(str(form.get("max_score")))
        warn_score = int(str(form.get("warn_score")))
    except (TypeError, ValueError):
        return RedirectResponse(
            "/admin/risk-appetite?flash=" + quote("fail:Scores must be whole numbers"),
            status_code=302)
    await appetite_service.set_appetite(
        pool, organization_id=user.organization_id, risk_category=category,
        max_score=max_score, warn_score=warn_score)
    return RedirectResponse(
        "/admin/risk-appetite?flash=" + quote("ok:Appetite saved"), status_code=302)


@api_router.get("", summary="Risk appetite config + breach summary")
async def appetite_api(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    org = user.organization_id
    return {
        "appetite": await appetite_service.get_appetite(pool, organization_id=org),
        "summary": await appetite_service.breach_summary(pool, organization_id=org),
    }
