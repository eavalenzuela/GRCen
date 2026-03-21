from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from grcen.custom_fields import CUSTOM_FIELDS, coerce_value
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

router = APIRouter(tags=["pages"])


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
async def login_page(request: Request, user: User | None = Depends(get_current_user_or_none)):
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login_submit(request: Request, pool: asyncpg.Pool = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    user = await auth_svc.authenticate_user(pool, str(username), str(password))
    if not user:
        return templates.TemplateResponse(
            "auth/login.html", {"request": request, "error": "Invalid credentials"}
        )
    request.session["user_id"] = str(user.id)
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
async def logout(request: Request):
    request.session.clear()
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
    type: AssetType | None = None,
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
    metadata_filters = None
    if meta_key and meta_value:
        metadata_filters = {meta_key: meta_value}
    items, total = await asset_svc.list_assets(
        pool,
        asset_type=type,
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
    if type:
        filter_params += f"&type={type.value}"
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
    return templates.TemplateResponse(
        "assets/form.html",
        {
            "request": request,
            "user": user,
            "asset": None,
            "asset_types": list(AssetType),
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
    asset = await asset_svc.create_asset(
        pool,
        type=asset_type,
        name=str(form["name"]),
        description=str(form.get("description", "")),
        status=str(form.get("status", "active")),
        owner_id=owner_id,
        metadata_=metadata,
        updated_by=user.id,
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
            "asset_custom_fields": CUSTOM_FIELDS.get(asset.type, []),
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
    return templates.TemplateResponse(
        "assets/form.html",
        {
            "request": request,
            "user": user,
            "asset": asset,
            "asset_types": list(AssetType),
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
    updated = await asset_svc.update_asset(
        pool,
        asset_id,
        name=str(form["name"]),
        description=str(form.get("description", "")),
        status=str(form.get("status", "active")),
        owner_id=owner_id,
        metadata_=metadata,
        updated_by=user.id,
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
    html = ""
    for a in results[:10]:
        label = a.type.value.replace("_", " ").title()
        html += (
            f'<div class="autocomplete-item" '
            f"onclick=\"selectOwner('{a.id}', '{a.name}')\">"
            f"{a.name} <small>({label})</small></div>"
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
    user: User = Depends(require_permission(Permission.IMPORT)),
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
    user: User = Depends(require_permission(Permission.EXPORT)),
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
    return templates.TemplateResponse(
        "reviews/index.html",
        {
            "request": request,
            "user": user,
            "reviews": reviews,
            "asset_types": list(AssetType),
            "current_type": type or "",
            "current_status": status or "",
            "notif_count": notif_count,
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
    user: User = Depends(require_permission(Permission.VIEW)),
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


# --- Admin pages ---


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    users = await auth_svc.list_users(pool)
    notif_count = await alert_svc.count_unread_notifications(pool)
    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
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
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
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
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
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
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "user": user,
            "edit_user": edit_user,
            "roles": list(UserRole),
            "notif_count": notif_count,
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
    return templates.TemplateResponse(
        "admin/audit_log.html",
        {
            "request": request,
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
    return templates.TemplateResponse(
        "admin/audit_settings.html",
        {
            "request": request,
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
