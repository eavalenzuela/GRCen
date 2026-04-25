import os
import uuid
from uuid import UUID

import asyncpg

from grcen.config import settings
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from grcen.custom_fields import CUSTOM_FIELDS, coerce_value
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission, UserRole, has_permission
from grcen.rate_limit import check_login_rate_limit
from grcen.routers.deps import (
    get_current_user,
    get_current_user_or_none,
    get_db,
    require_permission,
)
from grcen.services import alert_service as alert_svc
from grcen.services import asset as asset_svc
from grcen.services import attachment as att_svc
from grcen.services import audit_service as audit_svc
from grcen.services import auth as auth_svc
from grcen.services import (
    access_log_service,
    encryption_config,
    framework_service,
    oidc_settings,
    pdf_service,
    redaction,
    saml_settings,
    saved_search_service,
    smtp_settings,
    tag_service,
)
from grcen.services import relationship as rel_svc
from grcen.services import review_service as review_svc
from grcen.services import risk_service as risk_svc
from grcen.services import organization_service
from grcen.services import workflow_service
from grcen.services.encryption import is_encryption_enabled
from grcen.services.encryption_scopes import ALL_PROFILES, ALL_SCOPES

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


async def _sso_context(pool: asyncpg.Pool) -> dict:
    """Gather SSO provider state for the login template."""
    oidc_cfg = await oidc_settings.get_settings(pool)
    saml_cfg = await saml_settings.get_settings(pool)
    return {
        "oidc_enabled": oidc_cfg.enabled,
        "oidc_display_name": oidc_cfg.display_name,
        "saml_enabled": saml_cfg.enabled,
        "saml_display_name": saml_cfg.display_name,
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User | None = Depends(get_current_user_or_none),
):
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "auth/login.html", context=await _sso_context(pool)
    )


@router.post("/login")
async def login_submit(request: Request, pool: asyncpg.Pool = Depends(get_db), _rl=Depends(check_login_rate_limit)):
    from grcen.config import settings as app_settings
    from grcen.services import session_service

    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    # Check lockout
    if await auth_svc.check_lockout(pool, username):
        ctx = await _sso_context(pool)
        ctx["error"] = "Too many failed attempts. Try again later."
        return templates.TemplateResponse(
            request, "auth/login.html", context=ctx
        )

    user = await auth_svc.authenticate_user(pool, username, password)
    if not user:
        await auth_svc.record_failed_login(
            pool, username,
            app_settings.LOGIN_MAX_FAILED_ATTEMPTS,
            app_settings.LOGIN_LOCKOUT_MINUTES,
        )
        ctx = await _sso_context(pool)
        ctx["error"] = "Invalid credentials"
        return templates.TemplateResponse(
            request, "auth/login.html", context=ctx
        )

    await auth_svc.record_successful_login(pool, user.id)

    # If the user has TOTP enabled, the password step only established intent —
    # redirect to the second factor step and park the user id in the session.
    from grcen.services import totp_service
    if await totp_service.is_enabled(pool, user.id):
        request.session.clear()
        request.session["mfa_pending_user_id"] = str(user.id)
        return RedirectResponse("/login/mfa", status_code=302)

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


@router.get("/login/mfa", response_class=HTMLResponse)
async def login_mfa_page(request: Request):
    pending = request.session.get("mfa_pending_user_id")
    if not pending:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request, "auth/mfa.html", context={"error": None}
    )


@router.post("/login/mfa")
async def login_mfa_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
):
    from grcen.services import session_service, totp_service
    pending = request.session.get("mfa_pending_user_id")
    if not pending:
        return RedirectResponse("/login", status_code=302)
    user_id = UUID(pending)

    form = await request.form()
    code = str(form.get("code", "")).strip()

    if not await totp_service.verify_login_code(pool, user_id, code):
        return templates.TemplateResponse(
            request, "auth/mfa.html",
            context={"error": "Invalid code. Try again."},
        )

    user = await auth_svc.get_user_by_id(pool, user_id)
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

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
        action="login_mfa",
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
    tag: str | None = None,
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
        tag=tag,
        sort=sort,
        order=order,
        organization_id=user.organization_id,
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    all_tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    saved_searches = await saved_search_service.list_visible(
        pool, user.id, path="/assets", organization_id=user.organization_id
    )
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
    if tag:
        filter_params += f"&tag={tag}"
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
            "filter_tag": tag or "",
            "all_tags": all_tags,
            "saved_searches": saved_searches,
            "current_path": "/assets",
            "current_query": filter_params.lstrip("&"),
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
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    known_tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    return templates.TemplateResponse(request, "assets/form.html", context={
            "user": user,
            "asset": None,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
            "custom_fields": CUSTOM_FIELDS,
            "known_tags": known_tags,
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
    name = str(form["name"])
    description = str(form.get("description", ""))
    status = str(form.get("status", "active"))
    if await workflow_service.requires_approval(
        pool, asset_type, "create", organization_id=user.organization_id
    ):
        change = await workflow_service.submit(
            pool,
            action="create",
            asset_type=asset_type,
            target_asset_id=None,
            title=name,
            payload=workflow_service.asset_create_payload(
                name=name, description=description, status=status,
                owner_id=owner_id, metadata=metadata, tags=tags,
                criticality=criticality,
            ),
            user=user,
        )
        return RedirectResponse(f"/approvals/{change.id}?submitted=1", status_code=302)
    try:
        asset = await asset_svc.create_asset(
            pool,
            organization_id=user.organization_id,
            type=asset_type,
            name=name,
            description=description,
            status=status,
            owner_id=owner_id,
            metadata_=metadata,
            updated_by=user.id,
            tags=tags,
            criticality=criticality,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    await access_log_service.record(
        pool,
        user=user,
        action="view",
        entity_type="asset",
        entity_id=asset.id,
        entity_name=asset.name,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    asset.metadata_ = redaction.redact_metadata(asset.metadata_, asset.type, user)
    rels = await rel_svc.list_relationships_for_asset(pool, asset_id, organization_id=user.organization_id)
    if rels:
        rel_ids = [r.id for r in rels]
        count_rows = await pool.fetch(
            """SELECT relationship_id, count(*)::int AS n
               FROM attachments
               WHERE relationship_id = ANY($1::uuid[])
               GROUP BY relationship_id""",
            rel_ids,
        )
        counts = {row["relationship_id"]: row["n"] for row in count_rows}
        for r in rels:
            r.attachment_count = counts.get(r.id, 0)
    atts = await att_svc.list_attachments(pool, asset_id, organization_id=user.organization_id)
    alerts = await alert_svc.list_alerts(pool, asset_id, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    # For Person assets, find linked user account
    linked_user = None
    if asset.type == AssetType.PERSON:
        row = await pool.fetchrow(
            "SELECT id, username, role, is_active FROM users WHERE person_asset_id = $1",
            asset_id,
        )
        if row:
            linked_user = dict(row)
    pending_changes = await workflow_service.list_changes(
        pool, status="pending", target_asset_id=asset_id,
        organization_id=user.organization_id,
    )
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
            "pending_changes": pending_changes,
        },
    )


@router.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
async def asset_edit(
    request: Request,
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    known_tags = await tag_service.list_tags_with_counts(pool, organization_id=user.organization_id)
    return templates.TemplateResponse(request, "assets/form.html", context={
            "user": user,
            "asset": asset,
            "asset_types": sorted(AssetType, key=lambda t: t.value),
            "notif_count": notif_count,
            "custom_fields": CUSTOM_FIELDS,
            "asset_custom_fields": CUSTOM_FIELDS.get(asset.type, []),
            "known_tags": known_tags,
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
    old = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
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
    if old and await workflow_service.requires_approval(
        pool, old.type, "update", organization_id=user.organization_id
    ):
        try:
            change = await workflow_service.submit(
                pool,
                action="update",
                asset_type=old.type,
                target_asset_id=asset_id,
                title=str(form["name"]),
                payload=workflow_service.asset_update_payload({
                    "name": str(form["name"]),
                    "description": str(form.get("description", "")),
                    "status": str(form.get("status", "active")),
                    "owner_id": owner_id,
                    "metadata_": metadata,
                    "tags": tags,
                    "criticality": criticality,
                }),
                user=user,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return RedirectResponse(f"/approvals/{change.id}?submitted=1", status_code=302)
    try:
        updated = await asset_svc.update_asset(
            pool,
            asset_id,
            organization_id=user.organization_id,
            name=str(form["name"]),
            description=str(form.get("description", "")),
            status=str(form.get("status", "active")),
            owner_id=owner_id,
            metadata_=metadata,
            updated_by=user.id,
            tags=tags,
            criticality=criticality,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    old = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if old and await workflow_service.requires_approval(
        pool, old.type, "delete", organization_id=user.organization_id
    ):
        try:
            change = await workflow_service.submit(
                pool,
                action="delete",
                asset_type=old.type,
                target_asset_id=asset_id,
                title=old.name,
                payload={},
                user=user,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return RedirectResponse(f"/approvals/{change.id}?submitted=1", status_code=302)
    await asset_svc.delete_asset(pool, asset_id, organization_id=user.organization_id)
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
        pool, asset_id, organization_id=user.organization_id,
        clone_relationships=clone_rels, updated_by=user.id,
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
        pool, q, organization_id=user.organization_id,
        types=[AssetType.PERSON, AssetType.ORGANIZATIONAL_UNIT],
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
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if not asset:
        return HTMLResponse("Not found", status_code=404)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    users = await auth_svc.list_users(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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
        notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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
        pool, organization_id=user.organization_id,
        entity_type=entity_type, action=action, username=username, page=page,
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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


@router.post("/switch-org")
async def switch_organization(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Switch the active org for the current session.

    Membership is checked here in addition to the dep so we can reject early
    with a clear flash on /settings instead of silently falling back.
    """
    form = await request.form()
    target_id = str(form.get("organization_id", "")).strip()
    if not target_id:
        return RedirectResponse("/settings", status_code=302)
    try:
        target_uuid = UUID(target_id)
    except ValueError:
        return RedirectResponse("/settings?flash=fail:Invalid organization id", status_code=302)
    is_member, _ = await organization_service.is_member(pool, user.id, target_uuid)
    if not is_member and not user.is_superadmin:
        return RedirectResponse("/settings?flash=fail:You are not a member of that org", status_code=302)
    request.session["active_org_id"] = str(target_uuid)
    return RedirectResponse("/?", status_code=302)


@router.get("/admin/orgs", response_class=HTMLResponse)
async def admin_orgs_index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ORGS)),
    flash: str | None = None,
):
    rows = await organization_service.stats_for_orgs(pool)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(request, "admin/orgs.html", context={
        "user": user, "orgs": rows, "notif_count": notif_count, "flash": flash_ctx,
    })


@router.post("/admin/orgs")
async def admin_orgs_create(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ORGS)),
):
    form = await request.form()
    slug = str(form.get("slug", "")).strip().lower()
    name = str(form.get("name", "")).strip()
    if not slug or not name:
        return RedirectResponse(
            "/admin/orgs?flash=fail:Slug and name are required.", status_code=302
        )
    existing = await organization_service.get_by_slug(pool, slug)
    if existing:
        return RedirectResponse(
            f"/admin/orgs?flash=fail:Organization '{slug}' already exists.",
            status_code=302,
        )
    org = await organization_service.create_organization(pool, slug=slug, name=name)
    await audit_svc.log_audit_event(
        pool, user_id=user.id, username=user.username,
        action="create", entity_type="organization",
        entity_id=org.id, entity_name=org.name,
        organization_id=user.organization_id,
    )
    return RedirectResponse(
        f"/admin/orgs?flash=ok:Created organization '{slug}'.", status_code=302
    )


@router.post("/admin/orgs/{org_id}/delete")
async def admin_orgs_delete(
    org_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_ORGS)),
):
    try:
        deleted = await organization_service.delete_organization(pool, org_id)
    except ValueError as e:
        return RedirectResponse(f"/admin/orgs?flash=fail:{e}", status_code=302)
    if deleted:
        await audit_svc.log_audit_event(
            pool, user_id=user.id, username=user.username,
            action="delete", entity_type="organization",
            entity_id=org_id, entity_name=str(org_id),
            organization_id=user.organization_id,
        )
        return RedirectResponse("/admin/orgs?flash=ok:Organization deleted.", status_code=302)
    return RedirectResponse("/admin/orgs?flash=fail:Not found.", status_code=302)


@router.get("/admin/sensitive-fields", response_class=HTMLResponse)
async def admin_sensitive_fields(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    flash: str | None = None,
):
    """List every (asset_type, field) pair with its effective sensitive status."""
    overrides = await redaction.list_overrides(pool, user.organization_id)
    rows: list[dict] = []
    for at in sorted(AssetType, key=lambda t: t.value):
        for fdef in CUSTOM_FIELDS.get(at, []):
            override = overrides.get((at.value, fdef.name))
            effective = fdef.sensitive if override is None else override
            rows.append({
                "asset_type": at.value,
                "field_name": fdef.name,
                "label": fdef.label,
                "code_default": fdef.sensitive,
                "override": override,
                "effective": effective,
            })
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(
        request, "admin/sensitive_fields.html",
        context={
            "user": user, "rows": rows,
            "notif_count": notif_count, "flash": flash_ctx,
        },
    )


@router.post("/admin/sensitive-fields")
async def admin_sensitive_fields_save(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    """Save the form. The HTML form submits one entry per (type, field) with
    a value of 'sensitive', 'public', or 'inherit' so we can also clear an
    override and revert to the code default."""
    form = await request.form()
    for at in AssetType:
        for fdef in CUSTOM_FIELDS.get(at, []):
            choice = str(form.get(f"{at.value}.{fdef.name}", "inherit")).strip()
            if choice == "inherit":
                await redaction.clear_override(pool, user.organization_id, at, fdef.name)
            elif choice == "sensitive":
                await redaction.upsert_override(
                    pool, user.organization_id, at, fdef.name, sensitive=True
                )
            elif choice == "public":
                await redaction.upsert_override(
                    pool, user.organization_id, at, fdef.name, sensitive=False
                )
    return RedirectResponse(
        "/admin/sensitive-fields?flash=ok:Field sensitivity saved.", status_code=302
    )


@router.post("/admin/organization/branding")
async def admin_organization_branding(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    await organization_service.update_branding(
        pool,
        user.organization_id,
        email_from_name=str(form.get("email_from_name", "")).strip()[:120],
        email_brand_color=str(form.get("email_brand_color", "")).strip()[:20],
        email_logo_url=str(form.get("email_logo_url", "")).strip()[:500],
    )
    return RedirectResponse("/admin/organization?flash=ok:Branding saved.", status_code=302)


@router.get("/admin/organization", response_class=HTMLResponse)
async def admin_organization(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    flash: str | None = None,
):
    org = await organization_service.get_by_id(pool, user.organization_id)
    user_count = await pool.fetchval(
        "SELECT count(*) FROM users WHERE organization_id = $1", user.organization_id
    )
    asset_count = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE organization_id = $1", user.organization_id
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(request, "admin/organization.html", context={
        "user": user,
        "org": org,
        "user_count": user_count,
        "asset_count": asset_count,
        "notif_count": notif_count,
        "flash": flash_ctx,
    })


@router.get("/admin/audit/settings", response_class=HTMLResponse)
async def admin_audit_settings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    configs = await audit_svc.get_audit_config_all(pool)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
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


# --- SAML Settings ---


@router.get("/admin/saml-settings", response_class=HTMLResponse)
async def admin_saml_settings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    saml_cfg = await saml_settings.get_settings(pool)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "admin/saml_settings.html",
        context={
            "user": user,
            "saml": saml_cfg,
            "roles": list(UserRole),
            "notif_count": notif_count,
        },
    )


@router.post("/admin/saml-settings")
async def admin_saml_settings_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()

    # Preserve existing private key if the placeholder was submitted.
    sp_private_key = str(form.get("sp_private_key", "")).strip()
    if sp_private_key == "********":
        current = await saml_settings.get_settings(pool)
        sp_private_key = current.sp_private_key

    await saml_settings.update_settings(
        pool,
        idp_entity_id=str(form.get("idp_entity_id", "")).strip(),
        idp_sso_url=str(form.get("idp_sso_url", "")).strip(),
        idp_slo_url=str(form.get("idp_slo_url", "")).strip(),
        idp_x509_cert=str(form.get("idp_x509_cert", "")).strip(),
        sp_entity_id=str(form.get("sp_entity_id", "")).strip(),
        sp_x509_cert=str(form.get("sp_x509_cert", "")).strip(),
        sp_private_key=sp_private_key,
        name_id_format=str(form.get("name_id_format", "")).strip(),
        role_attribute=str(form.get("role_attribute", "Role")).strip(),
        role_mapping=str(form.get("role_mapping", "{}")).strip(),
        default_role=str(form.get("default_role", "viewer")).strip(),
        display_name=str(form.get("display_name", "SAML SSO")).strip(),
        want_assertions_signed="true" if "want_assertions_signed" in form else "false",
        want_name_id_encrypted="true" if "want_name_id_encrypted" in form else "false",
    )
    return RedirectResponse("/admin/saml-settings", status_code=302)


# --- SMTP Settings ---


@router.get("/admin/smtp-settings", response_class=HTMLResponse)
async def admin_smtp_settings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    test_result: str | None = None,
):
    smtp_cfg = await smtp_settings.get_settings(pool)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    deliveries = await pool.fetch(
        """SELECT email, status, error, attempted_at
           FROM notification_deliveries
           ORDER BY attempted_at DESC LIMIT 20"""
    )
    # Decode the compact test_result query param into a dict for the template.
    test_ctx = None
    if test_result:
        ok, _, payload = test_result.partition(":")
        test_ctx = {"ok": ok == "ok", "to": "", "error": ""}
        if ok == "ok":
            test_ctx["to"] = payload
        else:
            test_ctx["error"] = payload
    return templates.TemplateResponse(
        request,
        "admin/smtp_settings.html",
        context={
            "user": user,
            "smtp": smtp_cfg,
            "deliveries": deliveries,
            "test_result": test_ctx,
            "notif_count": notif_count,
        },
    )


@router.post("/admin/smtp-settings")
async def admin_smtp_settings_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()

    # Preserve existing password if the placeholder was submitted.
    password = str(form.get("password", "")).strip()
    if password == "********":
        current = await smtp_settings.get_settings(pool)
        password = current.password

    await smtp_settings.update_settings(
        pool,
        host=str(form.get("host", "")).strip(),
        port=str(form.get("port", "587")).strip() or "587",
        username=str(form.get("username", "")).strip(),
        password=password,
        from_address=str(form.get("from_address", "")).strip(),
        from_name=str(form.get("from_name", "GRCen")).strip(),
        use_starttls="true" if "use_starttls" in form else "false",
        use_ssl="true" if "use_ssl" in form else "false",
        enabled="true" if "enabled" in form else "false",
    )

    if form.get("action") == "test":
        from grcen.services import email_service
        if not user.email:
            return RedirectResponse(
                "/admin/smtp-settings?test_result=fail:current user has no email address",
                status_code=302,
            )
        ok, err = await email_service.send_email(
            pool,
            to=user.email,
            subject="[GRCen] SMTP test message",
            body="This is a test message from GRCen to verify SMTP configuration.",
            user_id=user.id,
        )
        suffix = f"ok:{user.email}" if ok else f"fail:{err or 'unknown error'}"
        return RedirectResponse(f"/admin/smtp-settings?test_result={suffix}", status_code=302)

    return RedirectResponse("/admin/smtp-settings", status_code=302)


# --- Webhook management ---


def _parse_event_filter(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _decode_flash(flash: str | None) -> dict | None:
    if not flash:
        return None
    ok, _, message = flash.partition(":")
    return {"ok": ok == "ok", "message": message or flash}


@router.get("/admin/webhooks", response_class=HTMLResponse)
async def admin_webhooks_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    flash: str | None = None,
):
    from grcen.services import webhook_service

    hooks = await webhook_service.list_webhooks(pool, organization_id=user.organization_id)
    deliveries = await pool.fetch(
        """SELECT event, url, status_code, error, attempted_at
           FROM webhook_deliveries
           ORDER BY attempted_at DESC LIMIT 20"""
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "admin/webhooks.html",
        context={
            "user": user,
            "webhooks": hooks,
            "deliveries": deliveries,
            "flash": _decode_flash(flash),
            "notif_count": notif_count,
        },
    )


@router.post("/admin/webhooks")
async def admin_webhooks_create(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import webhook_service

    form = await request.form()
    name = str(form.get("name", "")).strip()
    url = str(form.get("url", "")).strip()
    if not name or not url:
        return RedirectResponse(
            "/admin/webhooks?flash=fail:Name and URL are required", status_code=302
        )
    await webhook_service.create_webhook(
        pool,
        organization_id=user.organization_id,
        name=name,
        url=url,
        secret=str(form.get("secret", "")).strip(),
        enabled="enabled" in form,
        event_filter=_parse_event_filter(str(form.get("event_filter", ""))),
    )
    return RedirectResponse("/admin/webhooks?flash=ok:Webhook created", status_code=302)


@router.get("/admin/webhooks/{webhook_id}/edit", response_class=HTMLResponse)
async def admin_webhook_edit_page(
    request: Request,
    webhook_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import webhook_service

    hook = await webhook_service.get_webhook(pool, webhook_id, organization_id=user.organization_id)
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "admin/webhook_edit.html",
        context={"user": user, "hook": hook, "notif_count": notif_count},
    )


@router.post("/admin/webhooks/{webhook_id}/edit")
async def admin_webhook_edit_submit(
    request: Request,
    webhook_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import webhook_service

    form = await request.form()
    # Preserve existing secret if the placeholder was submitted.
    secret = str(form.get("secret", ""))
    if secret == "********":
        current = await webhook_service.get_webhook(pool, webhook_id, organization_id=user.organization_id)
        secret = current.secret if current else ""
    hook = await webhook_service.update_webhook(
        pool,
        webhook_id,
        name=str(form.get("name", "")).strip(),
        url=str(form.get("url", "")).strip(),
        secret=secret,
        enabled="enabled" in form,
        event_filter=_parse_event_filter(str(form.get("event_filter", ""))),
    )
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return RedirectResponse("/admin/webhooks?flash=ok:Webhook updated", status_code=302)


@router.post("/admin/webhooks/{webhook_id}/delete")
async def admin_webhook_delete(
    webhook_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import webhook_service

    await webhook_service.delete_webhook(pool, webhook_id, organization_id=user.organization_id)
    return RedirectResponse("/admin/webhooks?flash=ok:Webhook deleted", status_code=302)


@router.post("/admin/webhooks/{webhook_id}/test")
async def admin_webhook_test(
    webhook_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen.services import webhook_service

    hook = await webhook_service.get_webhook(pool, webhook_id, organization_id=user.organization_id)
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    ok, status, err = await webhook_service.send_to_webhook(
        pool, hook, "ping", {"message": "Test ping from GRCen"}, alert_id=None
    )
    if ok:
        msg = f"ok:Ping succeeded (HTTP {status})"
    else:
        detail = f"HTTP {status}" if status else (err or "error")
        msg = f"fail:Ping failed: {detail}"
    return RedirectResponse(f"/admin/webhooks?flash={msg}", status_code=302)


# --- Relationship evidence (attachments on relationships) ---


@router.get("/relationships/{rel_id}/evidence", response_class=HTMLResponse)
async def relationship_evidence_page(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    rel = await rel_svc.get_relationship(pool, rel_id, organization_id=user.organization_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    source = await asset_svc.get_asset(pool, rel.source_asset_id, organization_id=user.organization_id)
    target = await asset_svc.get_asset(pool, rel.target_asset_id, organization_id=user.organization_id)
    attachments = await att_svc.list_attachments_for_relationship(pool, rel_id, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "relationships/evidence.html",
        context={
            "user": user,
            "rel": rel,
            "source": source,
            "target": target,
            "attachments": attachments,
            "notif_count": notif_count,
        },
    )


@router.post("/relationships/{rel_id}/evidence")
async def relationship_evidence_create(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    from grcen.models.attachment import AttachmentKind

    form = await request.form()
    kind = AttachmentKind(str(form.get("kind", "url")))
    name = str(form.get("name", "")).strip()
    url_or_path = str(form.get("url_or_path", "")).strip()
    if not name or not url_or_path:
        raise HTTPException(status_code=400, detail="Name and URL/path are required")
    att = await att_svc.create_attachment(
        pool,
        organization_id=user.organization_id,
        relationship_id=rel_id,
        kind=kind,
        name=name,
        url_or_path=url_or_path,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.create_snapshot(att.__dict__, ["name", "kind", "url_or_path"]),
    )
    return RedirectResponse(f"/relationships/{rel_id}/evidence", status_code=302)


@router.post("/relationships/{rel_id}/evidence/upload")
async def relationship_evidence_upload(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    from grcen.models.attachment import AttachmentKind
    from grcen.routers.attachments import (
        _read_upload,
        _sanitize_filename,
        _write_upload,
    )

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=400, detail="No file uploaded")
    content, encrypted = await _read_upload(pool, upload)
    filename = f"{uuid.uuid4()}_{_sanitize_filename(upload.filename)}"
    owner_dir = os.path.join(settings.UPLOAD_DIR, "relationships", str(rel_id))
    filepath = _write_upload(content, owner_dir, filename)
    att = await att_svc.create_attachment(
        pool,
        organization_id=user.organization_id,
        relationship_id=rel_id,
        kind=AttachmentKind.FILE,
        name=upload.filename or "uploaded_file",
        url_or_path=filepath,
        encrypted=encrypted,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.create_snapshot(att.__dict__, ["name", "kind", "url_or_path"]),
    )
    return RedirectResponse(f"/relationships/{rel_id}/evidence", status_code=302)


@router.post("/relationships/{rel_id}/evidence/{att_id}/delete")
async def relationship_evidence_delete(
    rel_id: UUID,
    att_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    att = await att_svc.get_attachment(pool, att_id, organization_id=user.organization_id)
    if not att or att.relationship_id != rel_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await att_svc.delete_attachment(pool, att_id, organization_id=user.organization_id)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="delete",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.delete_snapshot(att.__dict__, ["name", "kind", "url_or_path"]),
    )
    return RedirectResponse(f"/relationships/{rel_id}/evidence", status_code=302)


# --- Data access log ---


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    flash: str | None = None,
):
    """Cross-user view of every active session in this org."""
    from grcen.services import session_service
    sessions = await session_service.list_all_sessions(
        pool, organization_id=user.organization_id
    )
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id,
    )
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(
        request, "admin/sessions.html",
        context={
            "user": user, "sessions": sessions,
            "notif_count": notif_count, "flash": flash_ctx,
        },
    )


@router.post("/admin/sessions/{session_id}/revoke")
async def admin_session_revoke(
    session_id: str,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    """Force-end a session belonging to any user in the same org."""
    await pool.execute(
        """DELETE FROM sessions
           WHERE session_id = $1
             AND user_id IN (SELECT id FROM users WHERE organization_id = $2)""",
        session_id, user.organization_id,
    )
    return RedirectResponse(
        "/admin/sessions?flash=ok:Session revoked.", status_code=302
    )


@router.get("/admin/access-log/export.csv")
async def admin_access_log_export(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_AUDIT)),
    user_id: str | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    limit: int = 10000,
):
    """Stream the access log as CSV, honoring the same filters as the page."""
    import csv
    import io
    filter_user_uuid = UUID(user_id) if user_id else None
    entries = await access_log_service.query(
        pool, organization_id=user.organization_id, user_id=filter_user_uuid,
        entity_type=entity_type or None, action=action or None,
        limit=max(1, min(int(limit), 50000)),
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "created_at", "user_id", "username", "action",
        "entity_type", "entity_id", "entity_name", "path", "ip_address",
    ])
    for e in entries:
        writer.writerow([
            str(e.get("id") or ""),
            e["created_at"].isoformat() if e.get("created_at") else "",
            str(e.get("user_id") or ""),
            e.get("username") or "",
            e.get("action") or "",
            e.get("entity_type") or "",
            str(e.get("entity_id") or ""),
            e.get("entity_name") or "",
            e.get("path") or "",
            e.get("ip_address") or "",
        ])
    # Record the export itself in the access log.
    await access_log_service.record(
        pool, user=user, action="export",
        entity_type="access_log", entity_id=None,
        entity_name="access_log.csv",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="access_log.csv"'},
    )


@router.post("/admin/access-log/retention")
async def admin_access_log_retention(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    form = await request.form()
    raw = str(form.get("retention_days", "")).strip()
    days: int | None = None
    if raw:
        try:
            days = int(raw)
        except ValueError:
            return RedirectResponse(
                "/admin/access-log?flash=fail:retention_days must be an integer",
                status_code=302,
            )
    await access_log_service.set_retention_days(pool, days)
    msg = f"Retention set to {days} days" if days else "Retention disabled (logs kept forever)"
    return RedirectResponse(f"/admin/access-log?flash=ok:{msg}", status_code=302)


@router.get("/admin/access-log", response_class=HTMLResponse)
async def admin_access_log(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_AUDIT)),
    user_id: str | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    flash: str | None = None,
):
    filter_user_uuid = UUID(user_id) if user_id else None
    entries = await access_log_service.query(
        pool,
        organization_id=user.organization_id,
        user_id=filter_user_uuid,
        entity_type=entity_type or None,
        action=action or None,
        limit=200,
    )
    users_rows = await pool.fetch(
        "SELECT id, username FROM users WHERE organization_id = $1 ORDER BY username",
        user.organization_id,
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    retention_days = await access_log_service.get_retention_days(pool)
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(
        request,
        "admin/access_log.html",
        context={
            "user": user,
            "entries": entries,
            "users": users_rows,
            "entity_types": ["asset", "attachment", "framework", "relationship"],
            "actions": ["view", "download", "export", "pdf_export"],
            "filter_user_id": user_id or "",
            "filter_entity_type": entity_type or "",
            "filter_action": action or "",
            "notif_count": notif_count,
            "retention_days": retention_days,
            "flash": flash_ctx,
        },
    )


# --- Tag vocabulary ---


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


@router.get("/frameworks", response_class=HTMLResponse)
async def frameworks_index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    frameworks = await framework_service.list_frameworks(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "frameworks/index.html",
        context={"user": user, "frameworks": frameworks, "notif_count": notif_count},
    )


@router.get("/frameworks/{framework_id}", response_class=HTMLResponse)
async def framework_detail(
    request: Request,
    framework_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    detail = await framework_service.get_framework_detail(pool, framework_id, organization_id=user.organization_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Framework not found")
    last_audited = await framework_service._last_audited_for_requirements(
        pool, [r.id for r in detail.requirements]
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "frameworks/detail.html",
        context={
            "user": user, "detail": detail,
            "last_audited": last_audited, "notif_count": notif_count,
        },
    )


@router.get("/frameworks/{framework_id}/gap-report.csv")
async def framework_gap_report_csv(
    framework_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    rows = await framework_service.gap_report_rows(
        pool, framework_id, organization_id=user.organization_id
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Framework not found")
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "requirement_id", "requirement_name", "satisfied",
        "satisfier_count", "satisfiers", "last_audited",
    ])
    for r in rows:
        writer.writerow([
            r["requirement_id"], r["requirement_name"], r["satisfied"],
            r["satisfier_count"], r["satisfiers"], r["last_audited"],
        ])
    await access_log_service.record(
        pool, user=user, action="export",
        entity_type="framework", entity_id=framework_id,
        entity_name=f"framework-{framework_id}-gap-report.csv",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="framework-{framework_id}-gap-report.csv"'
            ),
        },
    )


@router.get("/controls", response_class=HTMLResponse)
async def controls_library(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    """Inverted view: every Control with the requirements it covers."""
    controls = await framework_service.list_controls_with_coverage(
        pool, organization_id=user.organization_id
    )
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request, "frameworks/controls.html",
        context={"user": user, "controls": controls, "notif_count": notif_count},
    )


@router.get("/frameworks/{framework_id}/report.pdf")
async def framework_report_pdf(
    framework_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    pdf = await pdf_service.render_framework_report(pool, framework_id)
    if pdf is None:
        raise HTTPException(status_code=404, detail="Framework not found")
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="framework", entity_id=framework_id,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="framework-{framework_id}.pdf"',
        },
    )


@router.get("/assets/{asset_id}/report.pdf")
async def asset_report_pdf(
    asset_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    pdf = await pdf_service.render_asset_report(pool, asset_id, user=user)
    if pdf is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="asset", entity_id=asset_id,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="asset-{asset_id}.pdf"',
        },
    )


# --- User self-service settings ---


@router.get("/settings", response_class=HTMLResponse)
async def user_settings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
    saved: int = 0,
    flash: str | None = None,
):
    from grcen.services import totp_service

    smtp_cfg = await smtp_settings.get_settings(pool)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)

    from grcen.services import session_service
    sessions = await session_service.list_sessions_for_user(pool, user.id)
    current_sid = request.session.get("session_id")
    memberships = await organization_service.list_memberships(pool, user.id)
    enrollment = await totp_service.get_enrollment(pool, user.id)
    mfa_enabled = bool(enrollment and enrollment["enabled"])
    mfa_pending_ctx = None
    # Only show pending enrollment block when we've just begun enrolling
    # (session holds plaintext recovery codes + secret from the begin step).
    pending = request.session.get("mfa_pending")
    if pending and not mfa_enabled:
        mfa_pending_ctx = {
            "secret": pending["secret"],
            "qr_b64": totp_service.qr_png_b64(pending["secret"], user.username),
            # Only show recovery codes on the first render after begin;
            # clear afterward so a refresh doesn't leak them.
            "recovery_codes": pending.pop("recovery_codes", []),
        }
        # Keep the secret around so Confirm can still find the QR if the page
        # is rendered again without codes.
        request.session["mfa_pending"] = pending

    recovery_remaining = len(enrollment["recovery_codes"]) if enrollment else None
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}

    return templates.TemplateResponse(
        request,
        "settings.html",
        context={
            "user": user,
            "saved": bool(saved),
            "smtp_enabled": smtp_cfg.is_enabled,
            "notif_count": notif_count,
            "mfa_enabled": mfa_enabled,
            "mfa_pending": mfa_pending_ctx,
            "recovery_remaining": recovery_remaining if mfa_enabled else None,
            "flash": flash_ctx,
            "sessions": sessions,
            "current_session_id": current_sid,
            "memberships": memberships,
        },
    )


@router.post("/settings/sessions/{session_id}/revoke")
async def user_session_revoke(
    session_id: str,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Let a user revoke one of their own sessions.

    The DELETE only fires when the session also belongs to the requesting user
    so cookie-bearing strangers can't terminate someone else's session by
    guessing an id.
    """
    from grcen.services import session_service
    await pool.execute(
        "DELETE FROM sessions WHERE session_id = $1 AND user_id = $2",
        session_id, user.id,
    )
    if request.session.get("session_id") == session_id:
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/settings?flash=ok:Session revoked", status_code=302)


@router.post("/settings")
async def user_settings_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = await request.form()
    enabled = "email_notifications_enabled" in form
    # Don't let users enable the flag if they have no email on file.
    if enabled and not user.email:
        enabled = False
    await auth_svc.set_email_notifications_enabled(pool, user.id, enabled)
    mode = str(form.get("email_notification_mode", "immediate")).strip()
    if mode in ("immediate", "digest"):
        await auth_svc.set_email_notification_mode(pool, user.id, mode)
    return RedirectResponse("/settings?saved=1", status_code=302)


@router.post("/settings/mfa/begin")
async def mfa_begin(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from grcen.services import totp_service
    if user.is_sso:
        return RedirectResponse(
            "/settings?flash=fail:MFA is managed by your SSO provider", status_code=302,
        )
    secret, recovery_codes = await totp_service.begin_enrollment(pool, user.id)
    request.session["mfa_pending"] = {
        "secret": secret,
        "recovery_codes": recovery_codes,
    }
    return RedirectResponse("/settings", status_code=302)


@router.post("/settings/mfa/confirm")
async def mfa_confirm(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from grcen.services import totp_service
    form = await request.form()
    code = str(form.get("code", "")).strip()
    ok = await totp_service.confirm_enrollment(pool, user.id, code)
    if not ok:
        return RedirectResponse(
            "/settings?flash=fail:Code did not match. Try again.", status_code=302,
        )
    request.session.pop("mfa_pending", None)
    await audit_svc.log_audit_event(
        pool, user_id=user.id, username=user.username,
        action="mfa_enable", entity_type="user",
        entity_id=user.id, entity_name=user.username,
    )
    return RedirectResponse(
        "/settings?flash=ok:Two-factor authentication is now required on login.",
        status_code=302,
    )


@router.get("/settings/mfa/cancel")
async def mfa_cancel(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from grcen.services import totp_service
    # Drop the pending row — enabled was never flipped so this clears the draft.
    enrollment = await totp_service.get_enrollment(pool, user.id)
    if enrollment and not enrollment["enabled"]:
        await totp_service.disable(pool, user.id)
    request.session.pop("mfa_pending", None)
    return RedirectResponse("/settings", status_code=302)


@router.post("/settings/mfa/disable")
async def mfa_disable(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from grcen.services import totp_service
    await totp_service.disable(pool, user.id)
    await audit_svc.log_audit_event(
        pool, user_id=user.id, username=user.username,
        action="mfa_disable", entity_type="user",
        entity_id=user.id, entity_name=user.username,
    )
    return RedirectResponse(
        "/settings?flash=ok:MFA disabled.", status_code=302,
    )


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
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    from datetime import UTC
    from datetime import datetime as dt

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
    from datetime import UTC
    from datetime import datetime as dt

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
    users = await auth_svc.list_users(pool, organization_id=user.organization_id)
    users_by_id = {u.id: u for u in users}
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    from datetime import UTC
    from datetime import datetime as dt

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


# --- Encryption Settings ---


@router.get("/admin/encryption", response_class=HTMLResponse)
async def admin_encryption(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    import json as _json

    active_profile = await encryption_config.get_active_profile(pool)
    active_scopes = await encryption_config.get_active_scopes(pool)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    success = request.session.pop("enc_success", None)
    error = request.session.pop("enc_error", None)
    # Build a JSON map of profile -> scope list for the JS toggle logic.
    profile_scopes = {
        p.name: list(p.scope_names) for p in ALL_PROFILES.values()
    }
    return templates.TemplateResponse(request, "admin/encryption.html", context={
        "user": user,
        "notif_count": notif_count,
        "key_configured": is_encryption_enabled(),
        "active_profile": active_profile,
        "active_scopes": active_scopes,
        "all_profiles": ALL_PROFILES,
        "all_scopes": ALL_SCOPES,
        "profile_scopes_json": _json.dumps(profile_scopes),
        "success": success,
        "error": error,
    })


@router.post("/admin/encryption")
async def admin_encryption_submit(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    if not is_encryption_enabled():
        request.session["enc_error"] = (
            "Encryption key not configured. Set the ENCRYPTION_KEY environment variable."
        )
        return RedirectResponse("/admin/encryption", status_code=302)

    form = await request.form()
    profile = str(form.get("profile", "")).strip()

    custom_scopes: list[str] = []
    if profile == "custom":
        for scope_name in ALL_SCOPES:
            if f"scope_{scope_name}" in form:
                custom_scopes.append(scope_name)

    # Determine which scopes are changing.
    old_scopes = await encryption_config.get_active_scopes(pool)
    new_scopes = await encryption_config.set_profile(pool, profile, custom_scopes)

    # Run migrations for scopes that were added or removed.
    from grcen.services import encryption_migrate

    added = new_scopes - old_scopes
    removed = old_scopes - new_scopes
    migrated = 0
    for scope_name in added:
        migrated += await encryption_migrate.migrate_scope(pool, scope_name, encrypt=True)
    for scope_name in removed:
        migrated += await encryption_migrate.migrate_scope(pool, scope_name, encrypt=False)

    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="edit",
        entity_type="encryption_config",
        entity_name="encryption",
        changes={
            "profile": {"old": await encryption_config.get_active_profile(pool), "new": profile},
            "scopes": {"old": sorted(old_scopes), "new": sorted(new_scopes)},
        },
    )

    parts = []
    if profile:
        parts.append(f"Profile set to {profile}.")
    if migrated:
        parts.append(f"{migrated} value(s) migrated.")
    request.session["enc_success"] = " ".join(parts) or "Encryption settings saved."
    return RedirectResponse("/admin/encryption", status_code=302)
