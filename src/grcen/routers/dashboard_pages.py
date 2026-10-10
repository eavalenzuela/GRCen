"""Dashboard and operational landing pages (imports, exports, reviews, risk management, alerts, org views, notifications)."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.asset import ORGANIZATIONAL_TYPES, AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import (
    _csrf_check,
    flash,
    templates,
)
from grcen.routers.alerts import _ALERT_FIELDS
from grcen.routers.deps import (
    get_db,
    require_permission,
)
from grcen.services import (
    alert_service as alert_svc,
    answer_service,
    appetite_service,
    asset as asset_svc,
    audit_service as audit_svc,
    review_service as review_svc,
    risk_service as risk_svc,
    saved_search_service,
    workflow_service,
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
    answers_needs_review = await answer_service.count_needs_review(
        pool, organization_id=user.organization_id
    )
    return templates.TemplateResponse(request, "dashboard.html", context={
            "user": user,
            "recent_assets": assets,
            "total_assets": total,
            "alerts": alerts[:5],
            "notif_count": notif_count,
            "asset_types": ORGANIZATIONAL_TYPES,
            "answers_needs_review": answers_needs_review,
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
            "asset_types": ORGANIZATIONAL_TYPES,
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
            "asset_types": ORGANIZATIONAL_TYPES,
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
    appetite_evals = await appetite_service.evaluate_risks(pool, organization_id=user.organization_id)
    appetite_status = {e["id"]: e["status"] for e in appetite_evals}
    appetite_out = sum(1 for e in appetite_evals if e["status"] == "out")
    appetite_near = sum(1 for e in appetite_evals if e["status"] == "near")
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
            "appetite_status": appetite_status,
            "appetite_out": appetite_out,
            "appetite_near": appetite_near,
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

    has_change = bool(treatment or owner_id is not None or review_date)
    # Honor per-type approval gating, mirroring /assets/bulk-update: when risk
    # `update` is gated, each selected risk becomes its own pending_changes row
    # (capped at 200) and the user is routed to the approval queue. (Resolves
    # register_framework_spec.md open decision #2 — risk bulk no longer diverges.)
    if (
        risk_ids and has_change
        and await workflow_service.requires_approval(
            pool, AssetType.RISK, "update", organization_id=user.organization_id
        )
    ):
        submitted = 0
        for rid in risk_ids[:200]:
            current = await asset_svc.get_asset(pool, rid, organization_id=user.organization_id)
            if current is None or current.type != AssetType.RISK:
                continue
            updates: dict = {}
            if owner_id is not None:
                updates["owner_id"] = owner_id
            if treatment or review_date:
                merged = dict(current.metadata_ or {})
                if treatment:
                    merged["treatment"] = treatment
                if review_date:
                    merged["review_date"] = review_date
                updates["metadata_"] = merged
            try:
                await workflow_service.submit(
                    pool, action="update", asset_type=AssetType.RISK,
                    target_asset_id=rid, title=current.name,
                    payload=workflow_service.asset_update_payload(updates), user=user,
                )
                submitted += 1
            except ValueError:
                continue  # an identical change for this risk is already pending
        return RedirectResponse(f"/approvals?submitted={submitted}", status_code=302)

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


async def _alert_form_from_request(request: Request) -> tuple[dict, dict]:
    """Parse the alert form into (values, errors). Values keep what was typed
    so a validation failure never discards the user's input."""
    from datetime import UTC, datetime

    from pydantic import ValidationError

    from grcen.schemas.alert import AlertCreate

    form = await request.form()
    values = {
        "title": str(form.get("title", "")).strip(),
        "asset_id": str(form.get("asset_id", "")).strip(),
        "message": str(form.get("message", "")).strip() or None,
        "schedule_type": str(form.get("schedule_type", "once")).strip(),
        "next_fire_at": str(form.get("next_fire_at", "")).strip(),
        "cron_expression": str(form.get("cron_expression", "")).strip() or None,
        "enabled": form.get("enabled") is not None,
    }
    errors: dict[str, str] = {}
    if not values["title"]:
        errors["title"] = "A title is required."
    if not values["asset_id"]:
        errors["asset_id"] = "Pick an asset from the search results."
    fire_at = None
    if values["next_fire_at"]:
        try:
            fire_at = datetime.fromisoformat(values["next_fire_at"])
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=UTC)
        except ValueError:
            errors["next_fire_at"] = "Enter a valid date and time."
    if values["schedule_type"] == "once":
        if not values["next_fire_at"] and "next_fire_at" not in errors:
            errors["next_fire_at"] = "One-time alerts need a date and time."
        elif fire_at is not None and fire_at <= datetime.now(UTC):
            errors["next_fire_at"] = "That time is in the past; the alert would never fire."
        values["cron_expression"] = None
    elif values["schedule_type"] == "recurring":
        if not values["cron_expression"]:
            errors["cron_expression"] = "Recurring alerts need a cron expression."
    else:
        errors["schedule_type"] = "Choose a schedule."
    if not errors and values["schedule_type"] == "recurring" and fire_at is None:
        # A cron schedule without a first occurrence never fires: the ticker
        # only advances alerts that are already due. Seed the first one.
        from apscheduler.triggers.cron import CronTrigger

        try:
            fire_at = CronTrigger.from_crontab(values["cron_expression"]).get_next_fire_time(
                None, datetime.now(UTC)
            )
        except ValueError as exc:
            errors["cron_expression"] = f"Invalid cron expression: {exc}"
    if not errors:
        try:
            AlertCreate(
                asset_id=values["asset_id"], title=values["title"], message=values["message"],
                schedule_type=values["schedule_type"], cron_expression=values["cron_expression"],
                next_fire_at=fire_at, enabled=values["enabled"],
            )
        except ValidationError as exc:
            for err in exc.errors():
                field = str(err["loc"][0]) if err.get("loc") else "_"
                errors[field] = err["msg"].removeprefix("Value error, ")
    values["_next_fire_at_parsed"] = fire_at
    return values, errors


async def _alert_page_context(pool, user, *, form=None, errors=None, asset_name=None, show_form=False):
    alerts = await alert_svc.list_alerts(pool, organization_id=user.organization_id)
    asset_names: dict = {}
    if alerts:
        rows = await pool.fetch(
            "SELECT id, name FROM assets WHERE id = ANY($1::uuid[])",
            list({a.asset_id for a in alerts}),
        )
        asset_names = {r["id"]: r["name"] for r in rows}
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return {
        "user": user, "alerts": alerts, "asset_names": asset_names, "notif_count": notif_count,
        "form": form or {}, "errors": errors or {}, "asset_name": asset_name, "show_form": show_form,
    }


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    asset_id: UUID | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    form: dict = {}
    asset_name = None
    if asset_id is not None:
        asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
        if asset is not None:
            form = {"asset_id": str(asset.id)}
            asset_name = asset.name
    ctx = await _alert_page_context(pool, user, form=form, asset_name=asset_name, show_form=asset_id is not None)
    return templates.TemplateResponse(request, "alerts/list.html", context=ctx)


@router.post("/alerts")
async def alerts_create(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ALERTS)),
):
    values, errors = await _alert_form_from_request(request)
    asset = None
    if values["asset_id"] and "asset_id" not in errors:
        try:
            asset = await asset_svc.get_asset(pool, UUID(values["asset_id"]), organization_id=user.organization_id)
        except ValueError:
            asset = None
        if asset is None:
            errors["asset_id"] = "That asset no longer exists; pick another."
    if errors:
        ctx = await _alert_page_context(
            pool, user, form=values, errors=errors,
            asset_name=asset.name if asset else None, show_form=True,
        )
        return templates.TemplateResponse(request, "alerts/list.html", context=ctx, status_code=400)
    alert = await alert_svc.create_alert(
        pool, asset_id=asset.id, title=values["title"], message=values["message"],
        schedule_type=values["schedule_type"], cron_expression=values["cron_expression"],
        next_fire_at=values["_next_fire_at_parsed"], enabled=values["enabled"],
        organization_id=user.organization_id,
    )
    await audit_svc.log_audit_event(
        pool, user_id=user.id, username=user.username, action="create",
        entity_type="alert", entity_id=alert.id, entity_name=alert.title,
        changes=audit_svc.create_snapshot(alert.__dict__, _ALERT_FIELDS),
    )
    flash(request, "success", f"Alert \u201c{alert.title}\u201d created for {asset.name}.")
    return RedirectResponse("/alerts", status_code=302)


@router.get("/alerts/{alert_id}/edit", response_class=HTMLResponse)
async def alert_edit_page(
    request: Request,
    alert_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ALERTS)),
):
    alert = await alert_svc.get_alert(pool, alert_id, organization_id=user.organization_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    asset = await asset_svc.get_asset(pool, alert.asset_id, organization_id=user.organization_id)
    form = {
        "title": alert.title, "asset_id": str(alert.asset_id), "message": alert.message,
        "schedule_type": alert.schedule_type.value, "cron_expression": alert.cron_expression,
        "next_fire_at": alert.next_fire_at.strftime("%Y-%m-%dT%H:%M") if alert.next_fire_at else "",
        "enabled": alert.enabled,
    }
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(request, "alerts/edit.html", context={
        "user": user, "alert_id": alert.id, "form": form, "errors": {},
        "asset_name": asset.name if asset else None, "notif_count": notif_count,
    })


@router.post("/alerts/{alert_id}/edit")
async def alert_edit_submit(
    request: Request,
    alert_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ALERTS)),
):
    alert = await alert_svc.get_alert(pool, alert_id, organization_id=user.organization_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    values, errors = await _alert_form_from_request(request)
    # Editing keeps the existing fire time for one-time alerts that already
    # passed (or already fired, leaving next_fire_at NULL); only a *changed*
    # past time is an error.
    if values["schedule_type"] == "once" and "next_fire_at" in errors:
        current = alert.next_fire_at.strftime("%Y-%m-%dT%H:%M") if alert.next_fire_at else ""
        if values["next_fire_at"] == current:
            errors.pop("next_fire_at")
            values["_next_fire_at_parsed"] = alert.next_fire_at
    asset = None
    if values["asset_id"] and "asset_id" not in errors:
        try:
            asset = await asset_svc.get_asset(pool, UUID(values["asset_id"]), organization_id=user.organization_id)
        except ValueError:
            asset = None
        if asset is None:
            errors["asset_id"] = "That asset no longer exists; pick another."
    if errors:
        notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
        return templates.TemplateResponse(request, "alerts/edit.html", context={
            "user": user, "alert_id": alert.id, "form": values, "errors": errors,
            "asset_name": asset.name if asset else None, "notif_count": notif_count,
        }, status_code=400)
    # One statement with explicit NULL semantics: update_alert() treats None as
    # "unchanged", which cannot clear a message or a cron expression.
    await pool.execute(
        """UPDATE alerts
              SET asset_id = $2, title = $3, message = $4, schedule_type = $5,
                  cron_expression = $6, next_fire_at = $7, enabled = $8, updated_at = now()
            WHERE id = $1""",
        alert.id, asset.id, values["title"], values["message"], values["schedule_type"],
        values["cron_expression"], values["_next_fire_at_parsed"], values["enabled"],
    )
    updated = await alert_svc.get_alert(pool, alert.id, organization_id=user.organization_id)
    diff = audit_svc.compute_diff(alert.__dict__, updated.__dict__, _ALERT_FIELDS)
    if diff:
        await audit_svc.log_audit_event(
            pool, user_id=user.id, username=user.username, action="update",
            entity_type="alert", entity_id=alert.id, entity_name=values["title"],
            changes=diff,
        )
    flash(request, "success", f"Alert \u201c{values['title']}\u201d saved.")
    return RedirectResponse("/alerts", status_code=302)


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


