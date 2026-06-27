"""Outbound vendor questionnaire campaigns.

`page_router` is the internal (authenticated) surface: build a campaign, add
questions, send it, review answers. `portal_router` is the login-less vendor
surface — no auth dependency, reached only with the campaign's secret
``access_token``, which is the capability that authorizes the read/write.
"""
from datetime import date
from urllib.parse import quote
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.config import settings
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import _csrf_check, templates
from grcen.routers.deps import get_db, require_permission
from grcen.services import (
    alert_service as alert_svc,
    asset as asset_svc,
    questionnaire_service,
    vendor_campaign_service as vc,
)

page_router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])
# Login-less: no auth, no CSRF dependency — the URL token is the capability.
portal_router = APIRouter(tags=["vendor-portal"])

STATUSES = ("draft", "sent", "in_progress", "submitted", "reviewed")


def _flash(flash: str | None) -> dict | None:
    if not flash:
        return None
    ok, _, message = flash.partition(":")
    return {"ok": ok == "ok", "message": message or flash}


def _portal_url(token: str) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/vendor-portal/{token}"


# --------------------------------------------------------------------------- #
# Internal (authenticated) surface
# --------------------------------------------------------------------------- #
@page_router.get("/vendor-campaigns", response_class=HTMLResponse)
async def campaigns_index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    campaigns = await vc.list_campaigns(pool, organization_id=user.organization_id)
    vendors, _ = await asset_svc.list_assets(
        pool, asset_type=AssetType.VENDOR, page=1, page_size=500,
        organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request, "vendor_campaigns/index.html",
        context={"user": user, "campaigns": campaigns, "vendors": vendors,
                 "flash": _flash(request.query_params.get("flash")),
                 "notif_count": notif_count},
    )


@page_router.post("/vendor-campaigns")
async def campaign_create(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    due_raw = str(form.get("due_date", "")).strip()
    vendor_raw = str(form.get("vendor_asset_id", "")).strip()
    campaign = await vc.create_campaign(
        pool, organization_id=user.organization_id, name=name,
        vendor_asset_id=UUID(vendor_raw) if vendor_raw else None,
        due_date=date.fromisoformat(due_raw) if due_raw else None,
        created_by=user.id)
    return RedirectResponse(f"/vendor-campaigns/{campaign['id']}", status_code=302)


@page_router.get("/vendor-campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail(
    request: Request,
    campaign_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    campaign = await vc.get_campaign(pool, campaign_id, organization_id=user.organization_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    questions = await vc.list_questions(pool, campaign_id)
    answered, total = vc.progress(questions)
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request, "vendor_campaigns/detail.html",
        context={"user": user, "campaign": campaign, "questions": questions,
                 "answered": answered, "total": total,
                 "portal_url": _portal_url(campaign["access_token"]),
                 "flash": _flash(request.query_params.get("flash")),
                 "notif_count": notif_count},
    )


@page_router.post("/vendor-campaigns/{campaign_id}/questions")
async def campaign_add_question(
    request: Request,
    campaign_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    await _require_campaign(pool, campaign_id, user)
    form = await request.form()
    text = str(form.get("question_text", "")).strip()
    if text:
        await vc.add_question(pool, campaign_id, organization_id=user.organization_id, text=text)
    return RedirectResponse(f"/vendor-campaigns/{campaign_id}", status_code=302)


@page_router.post("/vendor-campaigns/{campaign_id}/import")
async def campaign_import(
    request: Request,
    campaign_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    await _require_campaign(pool, campaign_id, user)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="No file uploaded")
    content = await upload.read()
    has_header = str(form.get("has_header", "")) in ("on", "true", "1")
    questions = questionnaire_service.parse_questions(content, column=0, has_header=has_header)
    n = await vc.import_questions(
        pool, campaign_id, organization_id=user.organization_id, questions=questions)
    return RedirectResponse(
        f"/vendor-campaigns/{campaign_id}?flash=" + quote(f"ok:Imported {n} question(s)"),
        status_code=302)


@page_router.post("/vendor-campaigns/{campaign_id}/send")
async def campaign_send(
    request: Request,
    campaign_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    campaign = await _require_campaign(pool, campaign_id, user)
    questions = await vc.list_questions(pool, campaign_id)
    if not questions:
        return RedirectResponse(
            f"/vendor-campaigns/{campaign_id}?flash=" + quote("fail:Add questions before sending"),
            status_code=302)
    if campaign["status"] == "draft":
        await vc.set_status(pool, campaign_id, "sent", organization_id=user.organization_id)
    msg = "ok:Campaign sent — share the portal link"
    return RedirectResponse(
        f"/vendor-campaigns/{campaign_id}?flash=" + quote(msg), status_code=302)


@page_router.post("/vendor-campaigns/{campaign_id}/status")
async def campaign_set_status(
    request: Request,
    campaign_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    await _require_campaign(pool, campaign_id, user)
    form = await request.form()
    status = str(form.get("status", "")).strip()
    if status in STATUSES:
        await vc.set_status(pool, campaign_id, status, organization_id=user.organization_id)
    return RedirectResponse(f"/vendor-campaigns/{campaign_id}", status_code=302)


async def _require_campaign(pool, campaign_id: UUID, user: User):
    campaign = await vc.get_campaign(pool, campaign_id, organization_id=user.organization_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


# --------------------------------------------------------------------------- #
# Login-less vendor portal
# --------------------------------------------------------------------------- #
@portal_router.get("/vendor-portal/{token}", response_class=HTMLResponse)
async def portal_view(request: Request, token: str, pool: asyncpg.Pool = Depends(get_db)):
    campaign = await vc.get_by_token(pool, token)
    if campaign is None or campaign["status"] not in vc.PORTAL_VISIBLE:
        raise HTTPException(status_code=404, detail="Not found")
    questions = await vc.list_questions(pool, campaign["id"])
    editable = campaign["status"] in vc.PORTAL_EDITABLE
    return templates.TemplateResponse(
        request, "vendor_portal.html",
        context={"campaign": campaign, "questions": questions,
                 "editable": editable, "token": token,
                 "flash": _flash(request.query_params.get("flash"))},
    )


@portal_router.post("/vendor-portal/{token}")
async def portal_submit(request: Request, token: str, pool: asyncpg.Pool = Depends(get_db)):
    campaign = await vc.get_by_token(pool, token)
    if campaign is None or campaign["status"] not in vc.PORTAL_EDITABLE:
        raise HTTPException(status_code=404, detail="Not found")
    questions = await vc.list_questions(pool, campaign["id"])
    form = await request.form()
    answers = {q["id"]: str(form.get(f"answer_{q['id']}", "")) for q in questions}
    await vc.save_answers(pool, campaign["id"], answers)
    if str(form.get("action", "")) == "submit":
        await vc.submit(pool, campaign["id"])
        msg = "ok:Thank you — your responses have been submitted."
    else:
        msg = "ok:Saved. You can return to this link to finish later."
    return RedirectResponse(f"/vendor-portal/{token}?flash=" + quote(msg), status_code=302)
