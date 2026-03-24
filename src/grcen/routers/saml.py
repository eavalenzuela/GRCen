"""SAML 2.0 authentication routes.

Provides SP metadata, AuthnRequest initiation, Assertion Consumer Service
(ACS), and Single Logout Service (SLS) endpoints using python3-saml.
"""

import json
import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.routers.deps import get_db
from grcen.services import saml_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/saml", tags=["saml"])

_ROLE_PRIORITY = [UserRole.ADMIN, UserRole.EDITOR, UserRole.AUDITOR, UserRole.VIEWER]


def _prepare_saml_settings(
    cfg: saml_settings.SAMLSettings, request: Request
) -> dict:
    """Build the python3-saml settings dict from DB config and request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    base = f"{scheme}://{host}"

    sp_entity_id = cfg.sp_entity_id or f"{base}/auth/saml/metadata"

    settings_dict: dict = {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sp_entity_id,
            "assertionConsumerService": {
                "url": f"{base}/auth/saml/acs",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "singleLogoutService": {
                "url": f"{base}/auth/saml/sls",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "NameIDFormat": cfg.name_id_format,
        },
        "idp": {
            "entityId": cfg.idp_entity_id,
            "singleSignOnService": {
                "url": cfg.idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": cfg.idp_x509_cert,
        },
        "security": {
            "nameIdEncrypted": cfg.name_id_encrypted,
            "authnRequestsSigned": bool(cfg.sp_private_key and cfg.sp_x509_cert),
            "logoutRequestSigned": bool(cfg.sp_private_key and cfg.sp_x509_cert),
            "logoutResponseSigned": bool(cfg.sp_private_key and cfg.sp_x509_cert),
            "signMetadata": bool(cfg.sp_private_key and cfg.sp_x509_cert),
            "wantMessagesSigned": False,
            "wantAssertionsSigned": cfg.assertions_signed,
            "wantNameIdEncrypted": cfg.name_id_encrypted,
        },
    }

    # IdP SLO (optional)
    if cfg.idp_slo_url:
        settings_dict["idp"]["singleLogoutService"] = {
            "url": cfg.idp_slo_url,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        }

    # SP signing/encryption certs (optional)
    if cfg.sp_x509_cert:
        settings_dict["sp"]["x509cert"] = cfg.sp_x509_cert
    if cfg.sp_private_key:
        settings_dict["sp"]["privateKey"] = cfg.sp_private_key

    return settings_dict


def _prepare_request_data(request: Request) -> dict:
    """Extract request data in the format python3-saml expects."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return {
        "https": "on" if scheme == "https" else "off",
        "http_host": host,
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": {},  # filled in ACS
    }


def resolve_role(
    attributes: dict, cfg: saml_settings.SAMLSettings
) -> UserRole:
    """Map SAML attributes to a GRCen role."""
    default = UserRole(cfg.default_role)
    mapping = json.loads(cfg.role_mapping)
    if not mapping:
        return default

    values = attributes.get(cfg.role_attribute, [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return default

    best = default
    for group in values:
        mapped = mapping.get(group)
        if mapped:
            try:
                candidate = UserRole(mapped)
            except ValueError:
                continue
            if _ROLE_PRIORITY.index(candidate) < _ROLE_PRIORITY.index(best):
                best = candidate
    return best


async def _require_saml(pool: asyncpg.Pool = Depends(get_db)):
    cfg = await saml_settings.get_settings(pool)
    if not cfg.enabled:
        raise HTTPException(status_code=404)


@router.get("/metadata")
async def saml_metadata(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
):
    """Serve the SP metadata XML for IdP registration."""
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    cfg = await saml_settings.get_settings(pool)
    settings_dict = _prepare_saml_settings(cfg, request)
    saml_settings_obj = OneLogin_Saml2_Settings(
        settings_dict, sp_validation_only=True
    )
    metadata = saml_settings_obj.get_sp_metadata()
    errors = saml_settings_obj.validate_metadata(metadata)
    if errors:
        raise HTTPException(
            status_code=500,
            detail=f"SP metadata validation failed: {', '.join(errors)}",
        )
    return Response(content=metadata, media_type="application/xml")


@router.get("/login")
async def saml_login(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    _=Depends(_require_saml),
):
    """Initiate a SAML AuthnRequest redirect to the IdP."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    cfg = await saml_settings.get_settings(pool)
    req_data = _prepare_request_data(request)
    settings_dict = _prepare_saml_settings(cfg, request)
    auth = OneLogin_Saml2_Auth(req_data, settings_dict)
    redirect_url = auth.login()
    return RedirectResponse(redirect_url, status_code=302)


@router.post("/acs")
async def saml_acs(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
):
    """Assertion Consumer Service — processes the IdP's SAML Response."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    from grcen.services import asset as asset_svc
    from grcen.services import auth as auth_svc

    cfg = await saml_settings.get_settings(pool)
    if not cfg.enabled:
        raise HTTPException(status_code=404)

    form = await request.form()
    req_data = _prepare_request_data(request)
    req_data["post_data"] = dict(form)

    settings_dict = _prepare_saml_settings(cfg, request)
    auth = OneLogin_Saml2_Auth(req_data, settings_dict)
    auth.process_response()

    errors = auth.get_errors()
    if errors:
        logger.error(
            "SAML ACS errors: %s (reason: %s)",
            errors,
            auth.get_last_error_reason(),
        )
        raise HTTPException(
            status_code=400,
            detail="SAML authentication failed",
        )

    if not auth.is_authenticated():
        raise HTTPException(status_code=401, detail="SAML: not authenticated")

    # Extract identity
    name_id = auth.get_nameid()
    attributes = auth.get_attributes()
    session_index = auth.get_session_index()

    email = (
        attributes.get("email", [None])[0]
        or attributes.get(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
            [None],
        )[0]
        or (name_id if "@" in (name_id or "") else None)
    )
    display_name = (
        attributes.get("displayName", [None])[0]
        or attributes.get(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
            [None],
        )[0]
    )
    username = email or name_id or "saml_user"

    if not name_id:
        raise HTTPException(
            status_code=400, detail="SAML response missing NameID"
        )

    role = resolve_role(attributes, cfg)

    # Find or create user
    user = await auth_svc.get_user_by_saml_sub(pool, name_id)
    first_login = False

    if user is None and email:
        user = await auth_svc.get_user_by_email(pool, email)
        if user:
            await auth_svc.update_saml_user(
                pool, user.id, saml_sub=name_id, email=email, role=role
            )
            user = await auth_svc.get_user_by_id(pool, user.id)

    if user is None:
        user = await auth_svc.create_saml_user(
            pool, username, email, name_id, role
        )
        first_login = True
    else:
        await auth_svc.update_saml_user(
            pool, user.id, email=email, role=role
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # Auto-create Person asset on first login
    if first_login:
        person = await asset_svc.create_asset(
            pool,
            type=AssetType.PERSON,
            name=display_name or username,
            status="active",
            metadata_={"email": email} if email else None,
            updated_by=user.id,
        )
        await auth_svc.set_person_asset_link(pool, user.id, person.id)

    # Set session
    request.session.clear()
    request.session["user_id"] = str(user.id)
    if session_index:
        request.session["saml_session_index"] = session_index
    request.session["saml_name_id"] = name_id

    # Create server-side session
    from grcen.services import session_service

    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    sid = await session_service.create_session(
        pool, user.id, ip_address=ip, user_agent=request.headers.get("user-agent")
    )
    request.session["session_id"] = sid

    await auth_svc.record_successful_login(pool, user.id)

    # Audit log
    try:
        from grcen.services import audit_service as audit_svc

        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="saml_login",
            entity_type="user",
            entity_id=user.id,
            entity_name=user.username,
            changes={
                "provider": cfg.idp_entity_id,
                "first_login": first_login,
            },
        )
    except Exception:
        logger.warning("Failed to log SAML login audit event", exc_info=True)

    return RedirectResponse("/", status_code=302)


@router.get("/sls")
async def saml_sls(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
):
    """Single Logout Service — processes IdP-initiated logout."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    cfg = await saml_settings.get_settings(pool)
    if not cfg.enabled or not cfg.idp_slo_url:
        raise HTTPException(status_code=404)

    req_data = _prepare_request_data(request)
    req_data["get_data"] = dict(request.query_params)
    settings_dict = _prepare_saml_settings(cfg, request)
    auth = OneLogin_Saml2_Auth(req_data, settings_dict)

    url = auth.process_slo(keep_local_session=False)

    # Invalidate server-side session
    sid = request.session.get("session_id")
    if sid:
        from grcen.services import session_service

        await session_service.invalidate_session(pool, sid)

    request.session.clear()

    if url:
        return RedirectResponse(url, status_code=302)
    return RedirectResponse("/login", status_code=302)
