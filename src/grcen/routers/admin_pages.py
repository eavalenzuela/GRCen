"""Admin pages: users, audit, organizations, sensitive fields, SSO/SMTP, webhooks, sessions, access log, rate limits, tokens, and encryption."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from grcen.config import settings
from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission, UserRole
from grcen.routers._pages_shared import (
    _USER_FIELDS,
    _csrf_check,
    templates,
)
from grcen.routers.deps import (
    get_db,
    require_permission,
)
from grcen.services import (
    access_log_service,
    alert_service as alert_svc,
    asset as asset_svc,
    audit_service as audit_svc,
    auth as auth_svc,
    encryption_config,
    oidc_settings,
    organization_service,
    redaction,
    saml_settings,
    smtp_settings,
)
from grcen.services.encryption import is_encryption_enabled
from grcen.services.encryption_scopes import ALL_PROFILES, ALL_SCOPES

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

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


@router.post("/assets/{asset_id}/sensitive-overrides")
async def asset_sensitive_overrides_save(
    asset_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    """Per-asset sensitive flag overrides — narrower than the per-type page.

    Form submits one entry per field with value 'sensitive', 'public', or
    'inherit' (drop the row, fall back to type-level rules).
    """
    asset = await asset_svc.get_asset(pool, asset_id, organization_id=user.organization_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    form = await request.form()
    for fdef in CUSTOM_FIELDS.get(asset.type, []):
        choice = str(form.get(f"override.{fdef.name}", "inherit")).strip()
        if choice == "inherit":
            await redaction.clear_asset_override(pool, asset_id, fdef.name)
        elif choice == "sensitive":
            await redaction.upsert_asset_override(pool, asset_id, fdef.name, True)
        elif choice == "public":
            await redaction.upsert_asset_override(pool, asset_id, fdef.name, False)
    return RedirectResponse(f"/assets/{asset_id}", status_code=302)


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


@router.get("/admin/rate-limits", response_class=HTMLResponse)
async def admin_rate_limits(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
    flash: str | None = None,
):
    """Edit the read/write budgets and route overrides without a redeploy."""
    rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE 'rate_limit_%'"
    )
    db = {r["key"]: r["value"] for r in rows}
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id,
    )
    flash_ctx = None
    if flash:
        ok, _, message = flash.partition(":")
        flash_ctx = {"ok": ok == "ok", "message": message or flash}
    return templates.TemplateResponse(
        request, "admin/rate_limits.html",
        context={
            "user": user,
            "db_read": db.get("rate_limit_read_per_minute", ""),
            "db_write": db.get("rate_limit_write_per_minute", ""),
            "db_overrides": db.get("rate_limit_route_overrides", ""),
            "env_read": settings.RATE_LIMIT_READ_PER_MINUTE,
            "env_write": settings.RATE_LIMIT_WRITE_PER_MINUTE,
            "env_overrides": settings.RATE_LIMIT_ROUTE_OVERRIDES,
            "notif_count": notif_count,
            "flash": flash_ctx,
        },
    )


@router.post("/admin/rate-limits")
async def admin_rate_limits_save(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    from grcen import rate_limit as rl
    form = await request.form()
    pairs: list[tuple[str, str]] = []
    for key_form, key_db in (
        ("read", "rate_limit_read_per_minute"),
        ("write", "rate_limit_write_per_minute"),
        ("overrides", "rate_limit_route_overrides"),
    ):
        raw = str(form.get(key_form, "")).strip()
        pairs.append((key_db, raw))
    for key_db, raw in pairs:
        if raw == "":
            await pool.execute("DELETE FROM app_settings WHERE key = $1", key_db)
        else:
            await pool.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                   VALUES ($1, $2, now())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                key_db, raw,
            )
    rl.invalidate_settings_cache()
    return RedirectResponse(
        "/admin/rate-limits?flash=ok:Rate-limit settings saved.", status_code=302
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
