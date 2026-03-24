from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from grcen.custom_fields import CUSTOM_FIELDS, coerce_value
from grcen.rate_limit import check_login_rate_limit
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission, has_permission
from grcen.routers.deps import get_current_user, get_current_user_or_none, get_db, require_permission
from grcen.services import alert_service as alert_svc
from grcen.services import asset as asset_svc
from grcen.services import attachment as att_svc
from grcen.services import relationship as rel_svc
from grcen.permissions import UserRole
from grcen.services import auth as auth_svc
from grcen.services import audit_service as audit_svc
from grcen.services import oidc_settings
from grcen.services import review_service as review_svc
from grcen.services import risk_service as risk_svc

# Static mapping: relationship_type -> (outgoing_label, incoming_label)
RELATIONSHIP_LABELS: dict[str, tuple[str, str]] = {
    "manages": ("manages", "managed by"),
    "owns": ("owns", "owned by"),
    "leads": ("leads", "led by"),
    "member_of": ("member of", "has member"),
    "governs": ("governs", "governed by"),
    "depends_on": ("depends on", "depended on by"),
    "deployed_on": ("deployed on", "hosts"),
    "authenticates_via": ("authenticates via", "authenticates"),
    "authenticates": ("authenticates", "authenticated by"),
    "runs_on": ("runs on", "hosts"),
    "deploys_to": ("deploys to", "deployed from"),
    "monitors": ("monitors", "monitored by"),
    "protects": ("protects", "protected by"),
    "processes": ("processes", "processed by"),
    "stores": ("stores", "stored in"),
    "references": ("references", "referenced by"),
    "assesses": ("assesses", "assessed by"),
    "reviews": ("reviews", "reviewed by"),
    "satisfied_by": ("satisfied by", "satisfies"),
    "implemented_by": ("implemented by", "implements"),
    "operates_on": ("operates on", "operated on by"),
    "scans": ("scans", "scanned by"),
    "approves_changes_to": ("approves changes to", "changes approved by"),
    "threatens": ("threatens", "threatened by"),
    "mitigated_by": ("mitigated by", "mitigates"),
    "trained_on": ("trained on", "trains"),
    "used_by": ("used by", "uses"),
    "describes": ("describes", "described by"),
    "defines": ("defines", "defined by"),
    "sends_data_to": ("sends data to", "receives data from"),
    "connects_to": ("connects to", "connected from"),
    "links_to": ("links to", "linked from"),
    "replaced_by": ("replaced by", "replaces"),
    "mirrors": ("mirrors", "mirrored by"),
    "enforces": ("enforces", "enforced by"),
    "classifies": ("classifies", "classified by"),
    "provides_service_to": ("provides service to", "serviced by"),
    "affected_by": ("affected by", "affects"),
    "triggered_by": ("triggered by", "triggered"),
    "resulted_in": ("resulted in", "resulted from"),
    "subprocessor_of": ("subprocessor of", "has subprocessor"),
    "certifies": ("certifies", "certified by"),
    "tested_by": ("tested by", "tests"),
    "parent_of": ("parent of", "child of"),
}


def _rel_direction_label(rel_type: str, is_outgoing: bool) -> str:
    """Return a human-readable direction label for a relationship."""
    labels = RELATIONSHIP_LABELS.get(rel_type)
    if labels:
        return labels[0] if is_outgoing else labels[1]
    return rel_type if is_outgoing else f"incoming: {rel_type}"

_ASSET_FIELDS = ["name", "description", "status", "owner", "metadata"]
_USER_FIELDS = ["username", "role", "is_active"]

templates = Jinja2Templates(directory="src/grcen/templates")
templates.env.globals["has_perm"] = has_permission
templates.env.globals["Permission"] = Permission
templates.env.globals["rel_label"] = _rel_direction_label

async def _csrf_check(request: Request):
    """Verify CSRF token on POST form submissions.

    Accepts the token from either:
    - A ``csrf_token`` form field (standard HTML forms)
    - The ``X-CSRF-Token`` header (useful for programmatic clients)
    """
    if request.method != "POST":
        return

    expected = request.session.get("csrf_token", "")
    if not expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="CSRF token mismatch")

    # Check header first (e.g. from test clients or JS fetch)
    header_token = request.headers.get("x-csrf-token", "")
    if header_token:
        import hmac
        if hmac.compare_digest(str(header_token), str(expected)):
            return

    # Fall back to form field
    content_type = request.headers.get("content-type", "")
    is_form = (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    )
    if is_form:
        form = await request.form()
        submitted = form.get("csrf_token", "")
        import hmac
        if submitted and hmac.compare_digest(str(submitted), str(expected)):
            return

    from fastapi import HTTPException
    raise HTTPException(status_code=403, detail="CSRF token mismatch")


router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])


def _extract_metadata(form, asset_type: AssetType) -> dict:
    """Extract custom field values from form data into a metadata dict."""
    metadata = {}
    for field_def in CUSTOM_FIELDS.get(asset_type, []):
        key = f"metadata.{field_def.name}"
        raw = str(form.get(key, ""))
        # Checkboxes are absent from form when unchecked
        if field_def.field_type == "boolean":
            metadata[field_def.name] = key in form
        elif raw:
            metadata[field_def.name] = coerce_value(field_def, raw)
    return metadata


# --- Auth pages ---


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User | None = Depends(get_current_user_or_none),
):
    if user:
        return RedirectResponse("/", status_code=302)
    oidc_cfg = await oidc_settings.get_settings(pool)
    return templates.TemplateResponse(request, "auth/login.html", context={
        "oidc_enabled": oidc_cfg.enabled,
        "oidc_display_name": oidc_cfg.display_name,
    })


@router.post("/login")
async def login_submit(request: Request, pool: asyncpg.Pool = Depends(get_db), _rl=Depends(check_login_rate_limit)):
    from grcen.config import settings as app_settings
    from grcen.services import session_service

    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    # Check lockout
    if await auth_svc.check_lockout(pool, username):
        oidc_cfg = await oidc_settings.get_settings(pool)
        return templates.TemplateResponse(request, "auth/login.html", context={
                "error": "Too many failed attempts. Try again later.",
                "oidc_enabled": oidc_cfg.enabled,
                "oidc_display_name": oidc_cfg.display_name,
            }
        )

    user = await auth_svc.authenticate_user(pool, username, password)
    if not user:
        await auth_svc.record_failed_login(
            pool, username,
            app_settings.LOGIN_MAX_FAILED_ATTEMPTS,
            app_settings.LOGIN_LOCKOUT_MINUTES,
        )
        oidc_cfg = await oidc_settings.get_settings(pool)
        return templates.TemplateResponse(request, "auth/login.html", context={
                "error": "Invalid credentials",
                "oidc_enabled": oidc_cfg.enabled,
                "oidc_display_name": oidc_cfg.display_name,
            }
        )

    await auth_svc.record_successful_login(pool, user.id)

    # Session fixation prevention: clear old session, create server-side session
    request.session.clear()
    session_id = await session_service.create_session(
        pool,
        user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    request.session["session_id"] = session_id

    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="login",
        entity_type="user",
        entity_id=user.id,
        entity_name=user.username,
    )
    return RedirectResponse("/", status_code=302)


@router.get("/logout")
async def logout(request: Request, pool: asyncpg.Pool = Depends(get_db)):
    from grcen.services import session_service

    oidc_id_token = request.session.get("oidc_id_token")
    session_id = request.session.get("session_id")
    if session_id:
        await session_service.invalidate_session(pool, session_id)
    request.session.clear()

    oidc_cfg = await oidc_settings.get_settings(pool)
    if oidc_id_token and oidc_cfg.enabled:
        try:
            from grcen.routers.oidc import get_oauth

            oauth = await get_oauth(pool)
            metadata = await oauth.oidc.load_server_metadata()
            end_session = metadata.get("end_session_endpoint")
            if end_session:
                redirect_uri = str(request.url_for("login_page"))
                return RedirectResponse(
                    f"{end_session}?post_logout_redirect_uri={redirect_uri}",
                    status_code=302,
                )
        except Exception:
            pass

    return RedirectResponse("/login", status_code=302)


# --- Dashboard ---


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    assets, total = await asset_svc.list_assets(pool, page=1, page_size=10)
    alerts = await alert_svc.list_alerts(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    heatmap = await risk_svc.get_risk_heatmap(pool)
    top_risks = await risk_svc.get_top_risks(pool)
    review_counts = await review_svc.get_review_counts(pool)
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


@router.get("/assets", response_class=HTMLResponse)
async def asset_list(
    request: Request,
    type: str | None = None,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    meta_key: str | None = None,
    meta_value: str | None = None,
    sort: str = "name",
    order: str = "asc",
    page: int = 1,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    asset_type = AssetType(type) if type else None
    metadata_filters = None
    if meta_key and meta_value:
        metadata_filters = {meta_key: meta_value}
    items, total = await asset_svc.list_assets(
        pool,
        asset_type=asset_type,
        page=page,
        page_size=25,
        q=q,
        status=status,
        owner=owner,
        created_after=created_after,
        created_before=created_before,
        metadata_filters=metadata_filters,
        sort=sort,
        order=order,
    )
    notif_count = await alert_svc.count_unread_notifications(pool)
    # Build filter params string for pagination links
    filter_params = ""
    if asset_type:
        filter_params += f"&type={asset_type.value}"
    if q:
        filter_params += f"&q={q}"
    if status:
        filter_params += f"&status={status}"
    if owner:
        filter_params += f"&owner={owner}"
    if created_after:
        filter_params += f"&created_after={created_after}"
    if created_before:
        filter_params += f"&created_before={created_before}"
    if meta_key and meta_value:
        filter_params += f"&meta_key={meta_key}&meta_value={meta_value}"
    if sort != "name":
        filter_params += f"&sort={sort}"
    if order != "asc":
        filter_params += f"&order={order}"
    return templates.TemplateResponse(request, "assets/list.html", context={
            "user": user,
            "assets": items,
            "total": total,
            "page": page,
            "pages": (total + 24) // 25,
            "current_type": asset_type,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
            "filter_q": q or "",
            "filter_status": status or "",
            "filter_owner": owner or "",
            "filter_created_after": created_after or "",
            "filter_created_before": created_before or "",
            "filter_meta_key": meta_key or "",
            "filter_meta_value": meta_value or "",
            "filter_params": filter_params,
            "statuses": ["active", "inactive", "draft", "archived"],
            "sort": sort,
            "order": order,
        },
    )


@router.get("/assets/new", response_class=HTMLResponse)
async def asset_new(
    request: Request,
    user: User = Depends(require_permission(Permission.CREATE)),
    pool: asyncpg.Pool = Depends(get_db),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "assets/form.html", context={
            "user": user,
            "asset": None,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
            "custom_fields": CUSTOM_FIELDS,
        },
    )


@router.post("/assets/new")
async def asset_create_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    form = await request.form()
    asset_type = AssetType(form["type"])
    metadata = _extract_metadata(form, asset_type)
    if asset_type == AssetType.RISK:
        score = risk_svc.compute_risk_score(metadata.get("likelihood"), metadata.get("impact"))
        if score is not None:
            metadata["inherent_risk_score"] = score
    owner_id = None
    owner_id_str = str(form.get("owner_id", "")).strip()
    if owner_id_str:
        from uuid import UUID as _UUID
        try:
            owner_id = _UUID(owner_id_str)
        except ValueError:
            pass
    tags_raw = str(form.get("tags", "")).strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    criticality = str(form.get("criticality", "")).strip() or None
    asset = await asset_svc.create_asset(
        pool,
        type=asset_type,
        name=str(form["name"]),
        description=str(form.get("description", "")),
        status=str(form.get("status", "active")),
        owner_id=owner_id,
        metadata_=metadata,
        updated_by=user.id,
        tags=tags,
        criticality=criticality,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="asset",
        entity_id=asset.id,
        entity_name=asset.name,
        changes=audit_svc.create_snapshot(asset.__dict__, _ASSET_FIELDS),
    )
    return RedirectResponse(f"/assets/{asset.id}", status_code=302)


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
async def asset_detail(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    rels = await rel_svc.list_relationships_for_asset(pool, asset_id)
    atts = await att_svc.list_attachments(pool, asset_id)
    alerts = await alert_svc.list_alerts(pool, asset_id)
    notif_count = await alert_svc.count_unread_notifications(pool)
    # For Person assets, find linked user account
    linked_user = None
    if asset.type == AssetType.PERSON:
        row = await pool.fetchrow(
            "SELECT id, username, role, is_active FROM users WHERE person_asset_id = $1",
            asset_id,
        )
        if row:
            linked_user = dict(row)
    return templates.TemplateResponse(request, "assets/detail.html", context={
            "user": user,
            "asset": asset,
            "relationships": rels,
            "attachments": atts,
            "alerts": alerts,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
            "asset_custom_fields": CUSTOM_FIELDS.get(asset.type, []),
            "linked_user": linked_user,
        },
    )


@router.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
async def asset_edit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "assets/form.html", context={
            "user": user,
            "asset": asset,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
            "custom_fields": CUSTOM_FIELDS,
            "asset_custom_fields": CUSTOM_FIELDS.get(asset.type, []),
        },
    )


@router.post("/assets/{asset_id}/edit")
async def asset_update_submit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    old = await asset_svc.get_asset(pool, asset_id)
    metadata = _extract_metadata(form, old.type) if old else {}
    if old and old.type == AssetType.RISK:
        score = risk_svc.compute_risk_score(metadata.get("likelihood"), metadata.get("impact"))
        if score is not None:
            metadata["inherent_risk_score"] = score
    owner_id = None
    owner_id_str = str(form.get("owner_id", "")).strip()
    if owner_id_str:
        from uuid import UUID as _UUID
        try:
            owner_id = _UUID(owner_id_str)
        except ValueError:
            pass
    tags_raw = str(form.get("tags", "")).strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    criticality = str(form.get("criticality", "")).strip() or None
    updated = await asset_svc.update_asset(
        pool,
        asset_id,
        name=str(form["name"]),
        description=str(form.get("description", "")),
        status=str(form.get("status", "active")),
        owner_id=owner_id,
        metadata_=metadata,
        updated_by=user.id,
        tags=tags,
        criticality=criticality,
    )
    if old and updated:
        diff = audit_svc.compute_diff(old.__dict__, updated.__dict__, _ASSET_FIELDS)
        if diff:
            await audit_svc.log_audit_event(
                pool,
                user_id=user.id,
                username=user.username,
                action="update",
                entity_type="asset",
                entity_id=asset_id,
                entity_name=updated.name,
                changes=diff,
            )
    return RedirectResponse(f"/assets/{asset_id}", status_code=302)


@router.post("/assets/{asset_id}/delete")
async def asset_delete_submit(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await asset_svc.get_asset(pool, asset_id)
    await asset_svc.delete_asset(pool, asset_id)
    if old:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="delete",
            entity_type="asset",
            entity_id=old.id,
            entity_name=old.name,
            changes=audit_svc.delete_snapshot(old.__dict__, _ASSET_FIELDS),
        )
    return RedirectResponse("/assets", status_code=302)


@router.post("/assets/{asset_id}/clone")
async def asset_clone_submit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    form = await request.form()
    clone_rels = "clone_relationships" in form
    clone = await asset_svc.clone_asset(
        pool, asset_id, clone_relationships=clone_rels, updated_by=user.id,
    )
    if not clone:
        return HTMLResponse("Not found", status_code=404)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="clone",
        entity_type="asset",
        entity_id=clone.id,
        entity_name=clone.name,
        changes={"cloned_from": {"new": str(asset_id)}},
    )
    return RedirectResponse(f"/assets/{clone.id}", status_code=302)


# --- Owner autocomplete (returns HTML fragment for htmx) ---


@router.get("/api/owner-search", response_class=HTMLResponse)
async def owner_search(
    request: Request,
    q: str = "",
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    if len(q) < 2:
        return HTMLResponse("")
    results = await asset_svc.search_assets(
        pool, q, types=[AssetType.PERSON, AssetType.ORGANIZATIONAL_UNIT],
    )
    from html import escape
    html = ""
    for a in results[:10]:
        label = a.type.value.replace("_", " ").title()
        safe_name = escape(a.name, quote=True)
        # Use data attributes instead of inline JS to prevent XSS
        html += (
            f'<div class="autocomplete-item" '
            f'data-id="{a.id}" data-name="{safe_name}" '
            f'onclick="selectOwner(this.dataset.id, this.dataset.name)">'
            f"{safe_name} <small>({label})</small></div>"
        )
    if not html:
        html = '<div class="autocomplete-item" style="color:var(--text-muted)">No results</div>'
    return HTMLResponse(html)


# --- Graph page ---


@router.get("/graph/{asset_id}", response_class=HTMLResponse)
async def graph_page(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_GRAPH)),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "graph/view.html", context={
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
    user: User = Depends(require_permission(Permission.IMPORT)),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "imports/index.html", context={"user": user, "notif_count": notif_count},
    )


# --- Export page ---


@router.get("/exports", response_class=HTMLResponse)
async def export_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
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
    reviews = await review_svc.get_reviews(pool, asset_type=type, status_filter=status)
    notif_count = await alert_svc.count_unread_notifications(pool)
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
    )
    summary = await risk_svc.get_risk_summary(pool)
    heatmap = await risk_svc.get_risk_heatmap(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)

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
        },
    )


# --- Alerts page ---


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    alerts = await alert_svc.list_alerts(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
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
    notif_count = await alert_svc.count_unread_notifications(pool)
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
    notifs = await alert_svc.list_notifications(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "alerts/notifications.html", context={
            "user": user,
            "notifications": notifs,
            "notif_count": notif_count,
        },
    )


# --- Admin pages ---


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    users = await auth_svc.list_users(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "admin/users.html", context={
            "user": user,
            "users": users,
            "roles": list(UserRole),
            "notif_count": notif_count,
        },
    )


@router.get("/admin/users/new", response_class=HTMLResponse)
async def admin_user_new(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "admin/user_form.html", context={
            "user": user,
            "edit_user": None,
            "roles": list(UserRole),
            "notif_count": notif_count,
        },
    )


@router.post("/admin/users/new")
async def admin_user_create_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    username = str(form["username"]).strip()
    password = str(form["password"]).strip()
    role = UserRole(form["role"])
    if not username or not password:
        notif_count = await alert_svc.count_unread_notifications(pool)
        return templates.TemplateResponse(request, "admin/user_form.html", context={
                "user": user,
                "edit_user": None,
                "roles": list(UserRole),
                "notif_count": notif_count,
                "error": "Username and password are required.",
            },
        )
    new_user = await auth_svc.create_user(pool, username, password, role=role)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="user",
        entity_id=new_user.id,
        entity_name=new_user.username,
        changes=audit_svc.create_snapshot(new_user.__dict__, _USER_FIELDS),
    )
    return RedirectResponse("/admin/users", status_code=302)


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
async def admin_user_edit(
    request: Request,
    user_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    edit_user = await auth_svc.get_user_by_id(pool, user_id)
    if not edit_user:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool)
    person_assets = await pool.fetch(
        "SELECT id, name FROM assets WHERE type = 'person' ORDER BY name"
    )
    return templates.TemplateResponse(request, "admin/user_form.html", context={
            "user": user,
            "edit_user": edit_user,
            "roles": list(UserRole),
            "notif_count": notif_count,
            "person_assets": person_assets,
        },
    )


@router.post("/admin/users/{user_id}/edit")
async def admin_user_update_submit(
    request: Request,
    user_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    old_user = await auth_svc.get_user_by_id(pool, user_id)
    role = UserRole(form["role"])
    await auth_svc.update_user_role(pool, user_id, role)
    # Update password if provided
    password = str(form.get("password", "")).strip()
    if password:
        hashed = auth_svc.hash_password(password)
        await pool.execute(
            "UPDATE users SET hashed_password = $1, updated_at = now() WHERE id = $2",
            hashed, user_id,
        )
    # Update person asset link
    person_asset_raw = str(form.get("person_asset_id", "")).strip()
    person_asset_id = UUID(person_asset_raw) if person_asset_raw else None
    await auth_svc.set_person_asset_link(pool, user_id, person_asset_id)
    updated_user = await auth_svc.get_user_by_id(pool, user_id)
    if old_user and updated_user:
        diff = audit_svc.compute_diff(old_user.__dict__, updated_user.__dict__, _USER_FIELDS)
        if password:
            diff["password"] = {"old": "***", "new": "***"}
        if diff:
            await audit_svc.log_audit_event(
                pool,
                user_id=user.id,
                username=user.username,
                action="update",
                entity_type="user",
                entity_id=user_id,
                entity_name=updated_user.username,
                changes=diff,
            )
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/admin/users/{user_id}/toggle-active")
async def admin_user_toggle_active(
    user_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    target = await auth_svc.get_user_by_id(pool, user_id)
    if not target:
        return HTMLResponse("Not found", status_code=404)
    if target.id == user.id:
        return RedirectResponse("/admin/users", status_code=302)
    new_active = not target.is_active
    await auth_svc.set_user_active(pool, user_id, new_active)
    action = "activate" if new_active else "deactivate"
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action=action,
        entity_type="user",
        entity_id=user_id,
        entity_name=target.username,
        changes={"is_active": {"old": target.is_active, "new": new_active}},
    )
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/admin/users/{user_id}/delete")
async def admin_user_delete_submit(
    user_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    if user_id == user.id:
        return RedirectResponse("/admin/users", status_code=302)
    target = await auth_svc.get_user_by_id(pool, user_id)
    await auth_svc.delete_user(pool, user_id)
    if target:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="delete",
            entity_type="user",
            entity_id=user_id,
            entity_name=target.username,
            changes=audit_svc.delete_snapshot(target.__dict__, _USER_FIELDS),
        )
    return RedirectResponse("/admin/users", status_code=302)


# --- Audit log pages ---


@router.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit_log(
    request: Request,
    page: int = 1,
    entity_type: str | None = None,
    action: str | None = None,
    username: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_AUDIT)),
):
    logs, total = await audit_svc.list_audit_logs(
        pool, entity_type=entity_type, action=action, username=username, page=page
    )
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "admin/audit_log.html", context={
            "user": user,
            "logs": logs,
            "total": total,
            "page": page,
            "pages": (total + 49) // 50,
            "filter_entity_type": entity_type or "",
            "filter_action": action or "",
            "filter_username": username or "",
            "notif_count": notif_count,
        },
    )


@router.get("/admin/audit/settings", response_class=HTMLResponse)
async def admin_audit_settings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    configs = await audit_svc.get_audit_config_all(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "admin/audit_settings.html", context={
            "user": user,
            "configs": configs,
            "notif_count": notif_count,
        },
    )


@router.post("/admin/audit/settings")
async def admin_audit_settings_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    configs = await audit_svc.get_audit_config_all(pool)
    for cfg in configs:
        et = cfg["entity_type"]
        enabled = f"enabled_{et}" in form
        field_level = f"field_level_{et}" in form
        await audit_svc.update_audit_config(pool, et, enabled=enabled, field_level=field_level)
    return RedirectResponse("/admin/audit/settings", status_code=302)


# --- OIDC Settings ---


@router.get("/admin/oidc-settings", response_class=HTMLResponse)
async def admin_oidc_settings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    oidc_cfg = await oidc_settings.get_settings(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(request, "admin/oidc_settings.html", context={
            "user": user,
            "oidc": oidc_cfg,
            "roles": list(UserRole),
            "notif_count": notif_count,
        },
    )


@router.post("/admin/oidc-settings")
async def admin_oidc_settings_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    await oidc_settings.update_settings(
        pool,
        issuer_url=str(form.get("issuer_url", "")).strip(),
        client_id=str(form.get("client_id", "")).strip(),
        client_secret=str(form.get("client_secret", "")).strip(),
        scopes=str(form.get("scopes", "openid email profile")).strip(),
        role_claim=str(form.get("role_claim", "groups")).strip(),
        role_mapping=str(form.get("role_mapping", "{}")).strip(),
        default_role=str(form.get("default_role", "viewer")).strip(),
        display_name=str(form.get("display_name", "SSO")).strip(),
    )
    # Reset cached OAuth client so it picks up new settings
    from grcen.routers.oidc import reset_oauth

    reset_oauth()

    return RedirectResponse("/admin/oidc-settings", status_code=302)


# --- Token management pages ---


@router.get("/tokens", response_class=HTMLResponse)
async def my_tokens_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
    created_token: str | None = None,
):
    from grcen.permissions import ROLE_PERMISSIONS
    from grcen.services import token_service

    tokens = await token_service.list_tokens_for_user(pool, user.id)
    max_expiry_days = await token_service.get_max_expiry_days(pool)
    available_permissions = sorted(ROLE_PERMISSIONS.get(user.role, set()), key=lambda p: p.value)
    notif_count = await alert_svc.count_unread_notifications(pool)
    from datetime import UTC, datetime as dt

    return templates.TemplateResponse(request, "tokens/my_tokens.html", context={
            "user": user,
            "tokens": tokens,
            "available_permissions": available_permissions,
            "max_expiry_days": max_expiry_days,
            "is_admin": has_permission(user.role, Permission.MANAGE_USERS),
            "created_token": request.session.pop("created_token", None),
            "error": request.session.pop("token_error", None),
            "notif_count": notif_count,
            "now": dt.now(UTC),
        },
    )


@router.post("/tokens")
async def my_tokens_create(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from datetime import UTC, datetime as dt

    from grcen.permissions import ROLE_PERMISSIONS
    from grcen.services import token_service

    form = await request.form()
    name = str(form.get("name", "")).strip()
    permissions = form.getlist("permissions")
    expires_at_str = str(form.get("expires_at", "")).strip()
    is_service_account = form.get("is_service_account") == "1"

    if not name:
        request.session["token_error"] = "Token name is required."
        return RedirectResponse("/tokens", status_code=302)

    if not permissions:
        request.session["token_error"] = "At least one permission is required."
        return RedirectResponse("/tokens", status_code=302)

    # Validate permissions against user's role
    role_perms = {p.value for p in ROLE_PERMISSIONS.get(user.role, set())}
    invalid = [p for p in permissions if p not in role_perms]
    if invalid:
        request.session["token_error"] = f"Invalid permissions: {', '.join(invalid)}"
        return RedirectResponse("/tokens", status_code=302)

    if is_service_account and not has_permission(user.role, Permission.MANAGE_USERS):
        request.session["token_error"] = "Only admins can create service account tokens."
        return RedirectResponse("/tokens", status_code=302)

    expires_at = None
    if expires_at_str:
        try:
            expires_at = dt.fromisoformat(expires_at_str).replace(tzinfo=UTC)
        except ValueError:
            request.session["token_error"] = "Invalid expiration date format."
            return RedirectResponse("/tokens", status_code=302)

    token, raw = await token_service.create_token(
        pool,
        user_id=user.id,
        name=name,
        permissions=permissions,
        expires_at=expires_at,
        is_service_account=is_service_account,
    )

    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="api_token",
        entity_id=token.id,
        entity_name=token.name,
    )

    request.session["created_token"] = raw
    return RedirectResponse("/tokens", status_code=302)


@router.post("/tokens/{token_id}/revoke")
async def my_token_revoke(
    token_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from grcen.services import token_service

    token = await token_service.get_token_by_id(pool, token_id)
    if not token or token.user_id != user.id:
        request.session["token_error"] = "Token not found."
        return RedirectResponse("/tokens", status_code=302)

    await token_service.revoke_token(pool, token_id)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="revoke",
        entity_type="api_token",
        entity_id=token_id,
        entity_name=token.name,
    )
    return RedirectResponse("/tokens", status_code=302)


@router.get("/admin/tokens", response_class=HTMLResponse)
async def admin_tokens_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import token_service

    tokens = await token_service.list_all_tokens(pool)
    max_expiry_days = await token_service.get_max_expiry_days(pool)
    users = await auth_svc.list_users(pool)
    users_by_id = {u.id: u for u in users}
    notif_count = await alert_svc.count_unread_notifications(pool)
    from datetime import UTC, datetime as dt

    return templates.TemplateResponse(request, "admin/tokens.html", context={
            "user": user,
            "tokens": tokens,
            "max_expiry_days": max_expiry_days,
            "users_by_id": users_by_id,
            "success": request.session.pop("token_success", None),
            "error": request.session.pop("token_error", None),
            "notif_count": notif_count,
            "now": dt.now(UTC),
        },
    )


@router.post("/admin/tokens/config")
async def admin_tokens_config_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import token_service

    form = await request.form()
    raw = str(form.get("max_expiry_days", "")).strip()
    days = int(raw) if raw else None

    await token_service.set_max_expiry_days(pool, days)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="update",
        entity_type="token_config",
        entity_id=None,
        entity_name="token_max_expiry_days",
        changes={"max_expiry_days": days},
    )
    request.session["token_success"] = "Token settings updated."
    return RedirectResponse("/admin/tokens", status_code=302)


@router.post("/admin/tokens/{token_id}/revoke")
async def admin_token_revoke(
    token_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import token_service

    token = await token_service.get_token_by_id(pool, token_id)
    if not token:
        request.session["token_error"] = "Token not found."
        return RedirectResponse("/admin/tokens", status_code=302)

    await token_service.revoke_token(pool, token_id)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="revoke",
        entity_type="api_token",
        entity_id=token_id,
        entity_name=token.name,
    )
    request.session["token_success"] = f"Token '{token.name}' revoked."
    return RedirectResponse("/admin/tokens", status_code=302)
