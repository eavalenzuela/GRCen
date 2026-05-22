"""Per-user settings: org switching, profile, sessions, MFA, and API tokens."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.models.user import User
from grcen.permissions import Permission, has_permission
from grcen.routers._pages_shared import (
    _csrf_check,
    templates,
)
from grcen.routers.deps import (
    get_current_user,
    get_db,
)
from grcen.services import (
    alert_service as alert_svc,
    audit_service as audit_svc,
    auth as auth_svc,
    organization_service,
    smtp_settings,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

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


@router.post("/tokens/{token_id}/allowed-ips")
async def my_token_update_ips(
    token_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update the IP allowlist on one of the user's own tokens."""
    from grcen.services import token_service

    token = await token_service.get_token_by_id(pool, token_id)
    if not token or token.user_id != user.id:
        request.session["token_error"] = "Token not found."
        return RedirectResponse("/tokens", status_code=302)

    form = await request.form()
    raw = str(form.get("allowed_ips", "")).strip()
    # One entry per line, comma, or whitespace.
    import re
    entries = [e.strip() for e in re.split(r"[\n,;]+|\s+", raw) if e.strip()]
    # Validate each entry parses; the runtime _ip_matches_allowlist already
    # logs and skips bad ones, but validating early gives the user feedback.
    import ipaddress
    bad: list[str] = []
    for e in entries:
        try:
            ipaddress.ip_network(e, strict=False)
        except ValueError:
            bad.append(e)
    if bad:
        request.session["token_error"] = (
            f"Could not parse: {', '.join(bad)}. "
            "Use IPs (10.0.0.1) or CIDR ranges (10.0.0.0/24)."
        )
        return RedirectResponse("/tokens", status_code=302)

    await token_service.update_allowed_ips(pool, token_id, entries)
    await audit_svc.log_audit_event(
        pool, user_id=user.id, username=user.username,
        action="update", entity_type="api_token",
        entity_id=token_id, entity_name=token.name,
        changes={"allowed_ips": {"new": entries}},
    )
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


