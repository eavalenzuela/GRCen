"""Login, MFA challenge, and logout pages."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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


