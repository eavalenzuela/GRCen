"""OIDC/SSO authentication routes."""

import json
import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.routers.deps import get_db
from grcen.services import oidc_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["oidc"])

# Cache the OAuth client; reset when settings change.
_oauth = None
_oauth_issuer: str = ""


def reset_oauth():
    """Clear the cached OAuth client so it is rebuilt on next request."""
    global _oauth, _oauth_issuer
    _oauth = None
    _oauth_issuer = ""


async def get_oauth(pool: asyncpg.Pool):
    """Lazily create and cache the OAuth client from DB settings."""
    global _oauth, _oauth_issuer

    cfg = await oidc_settings.get_settings(pool)
    # Rebuild if issuer changed (settings were updated)
    if _oauth is not None and _oauth_issuer == cfg.issuer_url:
        return _oauth

    from authlib.integrations.starlette_client import OAuth

    _oauth = OAuth()
    _oauth.register(
        name="oidc",
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        server_metadata_url=f"{cfg.issuer_url.rstrip('/')}/.well-known/openid-configuration",
        client_kwargs={"scope": cfg.scopes},
    )
    _oauth_issuer = cfg.issuer_url
    return _oauth


_ROLE_PRIORITY = [UserRole.ADMIN, UserRole.EDITOR, UserRole.AUDITOR, UserRole.VIEWER]


def resolve_role(userinfo: dict, cfg: oidc_settings.OIDCSettings) -> UserRole:
    """Map OIDC claims to a GRCen role using configured mapping + default."""
    default = UserRole(cfg.default_role)
    mapping = json.loads(cfg.role_mapping)
    if not mapping:
        return default

    # Navigate dot-path to extract claim value
    value = userinfo
    for part in cfg.role_claim.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
    if value is None:
        return default

    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return default

    # Match against mapping, pick highest-privilege role
    best = default
    for group in value:
        mapped = mapping.get(group)
        if mapped:
            try:
                candidate = UserRole(mapped)
            except ValueError:
                continue
            if _ROLE_PRIORITY.index(candidate) < _ROLE_PRIORITY.index(best):
                best = candidate
    return best


async def _require_oidc(pool: asyncpg.Pool = Depends(get_db)):
    cfg = await oidc_settings.get_settings(pool)
    if not cfg.enabled:
        raise HTTPException(status_code=404)


@router.get("/login")
async def oidc_login(request: Request, pool: asyncpg.Pool = Depends(get_db), _=Depends(_require_oidc)):
    oauth = await get_oauth(pool)
    callback_url = str(request.url_for("oidc_callback"))
    return await oauth.oidc.authorize_redirect(request, callback_url)


@router.get("/callback")
async def oidc_callback(request: Request, pool: asyncpg.Pool = Depends(get_db), _=Depends(_require_oidc)):
    from grcen.services import asset as asset_svc
    from grcen.services import auth as auth_svc

    oauth = await get_oauth(pool)
    cfg = await oidc_settings.get_settings(pool)
    token = await oauth.oidc.authorize_access_token(request)
    userinfo = token.get("userinfo", {})

    sub = userinfo.get("sub", "")
    email = userinfo.get("email")
    preferred_username = userinfo.get("preferred_username") or email or sub
    display_name = userinfo.get("name") or preferred_username

    if not sub:
        raise HTTPException(status_code=400, detail="OIDC response missing subject claim")

    role = resolve_role(userinfo, cfg)

    # Find existing user: by oidc_sub first, then by email
    user = await auth_svc.get_user_by_oidc_sub(pool, sub)
    first_login = False

    if user is None and email:
        user = await auth_svc.get_user_by_email(pool, email)
        if user:
            # Link existing local account to this OIDC identity
            await auth_svc.update_oidc_user(pool, user.id, oidc_sub=sub, email=email, role=role)
            user = await auth_svc.get_user_by_id(pool, user.id)

    if user is None:
        # New user — create account
        user = await auth_svc.create_oidc_user(pool, preferred_username, email, sub, role)
        first_login = True
    else:
        # Existing SSO user — sync role and email
        await auth_svc.update_oidc_user(pool, user.id, email=email, role=role)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # Auto-create Person asset on first login
    if first_login:
        person = await asset_svc.create_asset(
            pool,
            type=AssetType.PERSON,
            name=display_name,
            status="active",
            metadata_={"email": email} if email else None,
            updated_by=user.id,
        )
        await auth_svc.set_person_asset_link(pool, user.id, person.id)

    # Set session
    request.session["user_id"] = str(user.id)
    request.session["oidc_id_token"] = token.get("id_token")

    # Audit log
    try:
        from grcen.services import audit_service as audit_svc

        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="oidc_login",
            entity_type="user",
            entity_id=user.id,
            entity_name=user.username,
            changes={"provider": cfg.issuer_url, "first_login": first_login},
        )
    except Exception:
        logger.warning("Failed to log OIDC login audit event", exc_info=True)

    return RedirectResponse("/", status_code=302)
