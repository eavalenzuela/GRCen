from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.routers.deps import get_current_user, get_current_user_or_none, get_db
from grcen.services import alert_service as alert_svc
from grcen.services import asset as asset_svc
from grcen.services import attachment as att_svc
from grcen.services import relationship as rel_svc
from grcen.services.auth import authenticate_user

templates = Jinja2Templates(directory="src/grcen/templates")

router = APIRouter(tags=["pages"])


# --- Auth pages ---


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: User | None = Depends(get_current_user_or_none)):
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login_submit(request: Request, pool: asyncpg.Pool = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    user = await authenticate_user(pool, str(username), str(password))
    if not user:
        return templates.TemplateResponse(
            "auth/login.html", {"request": request, "error": "Invalid credentials"}
        )
    request.session["user_id"] = str(user.id)
    return RedirectResponse("/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# --- Dashboard ---


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assets, total = await asset_svc.list_assets(pool, page=1, page_size=10)
    alerts = await alert_svc.list_alerts(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "recent_assets": assets,
            "total_assets": total,
            "alerts": alerts[:5],
            "notif_count": notif_count,
            "asset_types": list(AssetType),
        },
    )


# --- Asset pages ---


@router.get("/assets", response_class=HTMLResponse)
async def asset_list(
    request: Request,
    type: AssetType | None = None,
    page: int = 1,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = await asset_svc.list_assets(pool, asset_type=type, page=page, page_size=25)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "assets/list.html",
        {
            "request": request,
            "user": user,
            "assets": items,
            "total": total,
            "page": page,
            "pages": (total + 24) // 25,
            "current_type": type,
            "asset_types": list(AssetType),
            "notif_count": notif_count,
        },
    )


@router.get("/assets/new", response_class=HTMLResponse)
async def asset_new(
    request: Request,
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "assets/form.html",
        {
            "request": request,
            "user": user,
            "asset": None,
            "asset_types": list(AssetType),
            "notif_count": notif_count,
        },
    )


@router.post("/assets/new")
async def asset_create_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = await request.form()
    asset = await asset_svc.create_asset(
        pool,
        type=AssetType(form["type"]),
        name=str(form["name"]),
        description=str(form.get("description", "")),
        status=str(form.get("status", "active")),
        owner=str(form.get("owner", "")),
    )
    return RedirectResponse(f"/assets/{asset.id}", status_code=302)


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
async def asset_detail(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    rels = await rel_svc.list_relationships_for_asset(pool, asset_id)
    atts = await att_svc.list_attachments(pool, asset_id)
    alerts = await alert_svc.list_alerts(pool, asset_id)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "assets/detail.html",
        {
            "request": request,
            "user": user,
            "asset": asset,
            "relationships": rels,
            "attachments": atts,
            "alerts": alerts,
            "asset_types": list(AssetType),
            "notif_count": notif_count,
        },
    )


@router.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
async def asset_edit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "assets/form.html",
        {
            "request": request,
            "user": user,
            "asset": asset,
            "asset_types": list(AssetType),
            "notif_count": notif_count,
        },
    )


@router.post("/assets/{asset_id}/edit")
async def asset_update_submit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = await request.form()
    await asset_svc.update_asset(
        pool,
        asset_id,
        name=str(form["name"]),
        description=str(form.get("description", "")),
        status=str(form.get("status", "active")),
        owner=str(form.get("owner", "")),
    )
    return RedirectResponse(f"/assets/{asset_id}", status_code=302)


@router.post("/assets/{asset_id}/delete")
async def asset_delete_submit(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await asset_svc.delete_asset(pool, asset_id)
    return RedirectResponse("/assets", status_code=302)


# --- Graph page ---


@router.get("/graph/{asset_id}", response_class=HTMLResponse)
async def graph_page(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "graph/view.html",
        {
            "request": request,
            "user": user,
            "asset": asset,
            "notif_count": notif_count,
        },
    )


# --- Import page ---


@router.get("/imports", response_class=HTMLResponse)
async def import_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "imports/index.html",
        {"request": request, "user": user, "notif_count": notif_count},
    )


# --- Export page ---


@router.get("/exports", response_class=HTMLResponse)
async def export_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "exports/index.html",
        {
            "request": request,
            "user": user,
            "asset_types": list(AssetType),
            "notif_count": notif_count,
        },
    )


# --- Alerts page ---


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    alerts = await alert_svc.list_alerts(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "alerts/list.html",
        {
            "request": request,
            "user": user,
            "alerts": alerts,
            "notif_count": notif_count,
        },
    )


# --- Notifications page ---


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notifs = await alert_svc.list_notifications(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "alerts/notifications.html",
        {
            "request": request,
            "user": user,
            "notifications": notifs,
            "notif_count": notif_count,
        },
    )
