"""Dashboard and operational landing pages (imports, exports, reviews, risk management, alerts, org views, notifications)."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.asset import AssetType
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
    asset as asset_svc,
    audit_service as audit_svc,
    review_service as review_svc,
    risk_service as risk_svc,
    saved_search_service,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    assets, total = await asset_svc.list_assets(
        pool, page=1, page_size=10, organization_id=user.organization_id
    )
    alerts = await alert_svc.list_alerts(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    heatmap = await risk_svc.get_risk_heatmap(pool, organization_id=user.organization_id)
    top_risks = await risk_svc.get_top_risks(pool, organization_id=user.organization_id)
    review_counts = await review_svc.get_review_counts(pool, organization_id=user.organization_id)
    return templates.TemplateResponse(request, "dashboard.html", context={
            "user": user,
            "recent_assets": assets,
            "total_assets": total,
            "alerts": alerts[:5],
            "notif_count": notif_count,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "heatmap": heatmap,
            "top_risks": top_risks,
            "likelihood_levels": risk_svc.LIKELIHOOD_LEVELS,
            "impact_levels": risk_svc.IMPACT_LEVELS,
            "score_color": risk_svc.score_color,
            "review_counts": review_counts,
        },
    )


# --- Asset pages ---


@router.get("/imports", response_class=HTMLResponse)
async def import_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "imports/index.html", context={"user": user, "notif_count": notif_count},
    )


# --- Export page ---


@router.get("/exports", response_class=HTMLResponse)
async def export_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "exports/index.html", context={
            "user": user,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
        },
    )


# --- Reviews page ---


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_page(
    request: Request,
    type: str | None = None,
    status: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    reviews = await review_svc.get_reviews(
        pool, asset_type=type, status_filter=status, organization_id=user.organization_id
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "reviews/index.html", context={
            "user": user,
            "reviews": reviews,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "current_type": type or "",
            "current_status": status or "",
            "notif_count": notif_count,
        },
    )


# --- Risk Management page ---


@router.get("/risk-management", response_class=HTMLResponse)
async def risk_management_page(
    request: Request,
    category: str | None = None,
    treatment: str | None = None,
    effectiveness: str | None = None,
    owner: str | None = None,
    overdue: str | None = None,
    likelihood: str | None = None,
    impact: str | None = None,
    sort: str = "score",
    order: str = "desc",
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    is_overdue = overdue == "1"
    risks = await risk_svc.get_risk_register(
        pool,
        category=category,
        treatment=treatment,
        effectiveness=effectiveness,
        owner=owner,
        overdue=is_overdue,
        likelihood_filter=likelihood,
        impact_filter=impact,
        sort=sort,
        order=order,
        organization_id=user.organization_id,
    )
    summary = await risk_svc.get_risk_summary(pool, organization_id=user.organization_id)
    heatmap = await risk_svc.get_risk_heatmap(pool, organization_id=user.organization_id)
    trend = await risk_svc.get_severity_trend(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    bulk_owners = await pool.fetch(
        """SELECT id, name FROM assets
           WHERE type IN ('person', 'organizational_unit') AND status = 'active'
             AND organization_id = $1
           ORDER BY name""",
        user.organization_id,
    )
    saved_searches = await saved_search_service.list_visible(
        pool, user.id, organization_id=user.organization_id, path="/risk-management"
    )

    # Build filter_params for sort links
    filter_params = ""
    if category:
        filter_params += f"&category={category}"
    if treatment:
        filter_params += f"&treatment={treatment}"
    if effectiveness:
        filter_params += f"&effectiveness={effectiveness}"
    if owner:
        filter_params += f"&owner={owner}"
    if is_overdue:
        filter_params += "&overdue=1"
    if likelihood:
        filter_params += f"&likelihood={likelihood}"
    if impact:
        filter_params += f"&impact={impact}"

    return templates.TemplateResponse(request, "risks/index.html", context={
            "user": user,
            "risks": risks,
            "summary": summary,
            "heatmap": heatmap,
            "notif_count": notif_count,
            "likelihood_levels": risk_svc.LIKELIHOOD_LEVELS,
            "impact_levels": risk_svc.IMPACT_LEVELS,
            "score_color": risk_svc.score_color,
            "filter_category": category or "",
            "filter_treatment": treatment or "",
            "filter_effectiveness": effectiveness or "",
            "filter_owner": owner or "",
            "filter_overdue": is_overdue,
            "filter_likelihood": likelihood or "",
            "filter_impact": impact or "",
            "current_sort": sort,
            "current_order": order,
            "filter_params": filter_params,
            "bulk_owners": bulk_owners,
            "trend": trend,
            "saved_searches": saved_searches,
            "current_path": "/risk-management",
            "current_query": filter_params.lstrip("&"),
        },
    )


@router.post("/risk-management/bulk-update")
async def risk_bulk_update(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    raw_ids = form.getlist("risk_ids")
    risk_ids: list[UUID] = []
    for v in raw_ids:
        try:
            risk_ids.append(UUID(v))
        except (ValueError, TypeError):
            continue
    treatment = (str(form.get("treatment", "")).strip() or None)
    owner_raw = str(form.get("owner_id", "")).strip()
    owner_id = UUID(owner_raw) if owner_raw else None
    review_date = (str(form.get("review_date", "")).strip() or None)

    updated = await risk_svc.bulk_update_risks(
        pool,
        risk_ids,
        treatment=treatment,
        owner_id=owner_id,
        review_date=review_date,
        updated_by=user.id,
        organization_id=user.organization_id,
    )
    for rid in updated:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="bulk_update",
            entity_type="asset",
            entity_id=rid,
            entity_name="risk",
            changes={
                "treatment": {"new": treatment} if treatment else {},
                "owner_id": {"new": str(owner_id)} if owner_id else {},
                "review_date": {"new": review_date} if review_date else {},
            },
        )
    # Preserve filters when redirecting
    qs = request.url.query
    return RedirectResponse(
        f"/risk-management{'?' + qs if qs else ''}", status_code=302
    )


# --- Alerts page ---


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    alerts = await alert_svc.list_alerts(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "alerts/list.html", context={
            "user": user,
            "alerts": alerts,
            "notif_count": notif_count,
        },
    )


# --- Org Views page ---


@router.get("/org-views", response_class=HTMLResponse)
async def org_views_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "org_views.html", context={
            "user": user,
            "notif_count": notif_count,
        },
    )


# --- Notifications page ---


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    notifs = await alert_svc.list_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "alerts/notifications.html", context={
            "user": user,
            "notifications": notifs,
            "notif_count": notif_count,
        },
    )


# --- Admin pages ---


