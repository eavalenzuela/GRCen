"""OAuth 2.0 token endpoint (client-credentials grant only).

``POST /api/oauth/token`` accepts standard RFC 6749 form encoding::

    grant_type=client_credentials&client_id=...&client_secret=...

and returns the usual envelope::

    {"access_token": "grcen_...", "token_type": "Bearer", "expires_in": 3600}

The issued token is a normal GRCen API token (short-lived, permission-scoped,
org-bound) so it works everywhere a static token does. Error responses follow
RFC 6749 §5.2 (``invalid_request`` / ``unsupported_grant_type`` /
``invalid_client``).
"""

import asyncpg
from fastapi import APIRouter, Depends, Form
from fastapi.responses import JSONResponse

from grcen.routers.deps import get_db
from grcen.services import oauth_service

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": code}, status_code=status,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/token", summary="OAuth 2.0 token endpoint (client_credentials)")
async def token_endpoint(
    grant_type: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    pool: asyncpg.Pool = Depends(get_db),
):
    if grant_type != "client_credentials":
        return _error("unsupported_grant_type")
    if not client_id or not client_secret:
        return _error("invalid_request")
    issued = await oauth_service.issue_token(pool, client_id, client_secret)
    if issued is None:
        return _error("invalid_client", status=401)
    raw_token, expires_in = issued
    return JSONResponse(
        {
            "access_token": raw_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
