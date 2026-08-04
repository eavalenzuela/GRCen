"""Login, MFA challenge, and logout pages."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen import tenancy
from grcen.models.user import User
from grcen.rate_limit import check_login_rate_limit
from grcen.routers._pages_shared import (
    _csrf_check,
    _sso_context,
    templates,
)
from grcen.routers.deps import (
    get_current_user_or_none,
    get_db,
)
from grcen.services import (
    audit_service as audit_svc,
    auth as auth_svc,
    oidc_settings,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User | None = Depends(get_current_user_or_none),
    invited: int = 0,
):
    if user:
        return RedirectResponse("/", status_code=302)
    context = await _sso_context(pool)
    if invited:
        context["notice"] = "Account created — log in with your new credentials."
    return templates.TemplateResponse(
        request, "auth/login.html", context=context
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

    # Authenticated, so the tenant is known from here on. Login has no
    # get_current_user dependency to have set this for us, and everything below
    # (session-eviction notices, the login audit event) writes to RLS-protected
    # tables — without a context those writes are rejected by the policy.
    tenancy.set_tenant(user.organization_id)

    await auth_svc.record_successful_login(pool, user.id)

    # If the user has TOTP enabled, the password step only established intent —
    # redirect to the second factor step and park the user id in the session.
    from grcen.services import totp_service
    mfa_enabled = await totp_service.is_enabled(pool, user.id)

    # Per-role MFA enforcement: if the user's role is in the required list and
    # they haven't enrolled, refuse the login outright. SSO users skip this
    # check — their IdP is the source of truth for MFA on those accounts.
    required_roles = {
        r.strip().lower()
        for r in (app_settings.MFA_REQUIRED_FOR_ROLES or "").split(",")
        if r.strip()
    }
    if (
        not mfa_enabled
        and not user.is_sso
        and user.role.value in required_roles
    ):
        ctx = await _sso_context(pool)
        ctx["error"] = (
            "Two-factor authentication is required for your role. "
            "Ask an administrator to set it up via the CLI, "
            "or finish enrollment on a device that's already MFA-enabled."
        )
        return templates.TemplateResponse(
            request, "auth/login.html", context=ctx
        )

    if mfa_enabled:
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

    tenancy.set_tenant(user.organization_id)

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




# --- Org invitation accept (public, token-authorized) ---


@router.get("/invite/{token}", response_class=HTMLResponse)
async def invite_accept_page(
    token: str,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
):
    # Fully pre-auth: the invite token is what identifies the tenant, and
    # accepting it creates the membership that would scope the request.
    with tenancy.system_context():
        from grcen.services import invitation_service, organization_service

        invitation = await invitation_service.get_valid_by_token(pool, token)
        if invitation is None:
            return templates.TemplateResponse(
                request, "auth/invite.html",
                context={"invalid": True, "user": None}, status_code=404,
            )
        org = await organization_service.get_by_id(pool, invitation["organization_id"])
        return templates.TemplateResponse(
            request, "auth/invite.html",
            context={
                "invalid": False,
                "user": None,
                "token": token,
                "org_name": org.name if org else "GRCen",
                "role": invitation["role"],
                "suggested_email": invitation["email"],
                "error": None,
            },
        )


@router.post("/invite/{token}")
async def invite_accept_submit(
    token: str,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    _rl=Depends(check_login_rate_limit),
):
    # Fully pre-auth: the invite token is what identifies the tenant, and
    # accepting it creates the membership that would scope the request.
    with tenancy.system_context():
        from grcen.services import invitation_service, organization_service

        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        try:
            user = await invitation_service.accept_invitation(
                pool, token, username=username, password=password
            )
        except invitation_service.InvitationError as e:
            invitation = await invitation_service.get_valid_by_token(pool, token)
            if invitation is None:
                return templates.TemplateResponse(
                    request, "auth/invite.html",
                    context={"invalid": True, "user": None}, status_code=404,
                )
            org = await organization_service.get_by_id(pool, invitation["organization_id"])
            return templates.TemplateResponse(
                request, "auth/invite.html",
                context={
                    "invalid": False,
                    "user": None,
                    "token": token,
                    "org_name": org.name if org else "GRCen",
                    "role": invitation["role"],
                    "suggested_email": invitation["email"],
                    "error": str(e),
                },
                status_code=400,
            )
        await audit_svc.log_audit_event(
            pool, user_id=user.id, username=user.username,
            action="invite_accept", entity_type="user",
            entity_id=user.id, entity_name=user.username,
        )
        return RedirectResponse("/login?invited=1", status_code=302)
