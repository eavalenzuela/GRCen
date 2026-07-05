"""SCIM 2.0 endpoints for IdP user provisioning.

Business logic lives in ``services/scim_service``. These endpoints are
authenticated with a GRCen API token scoped to the ``sync`` permission (mint one
under Settings > API Tokens, or ``POST /api/tokens`` with ``permissions:
["sync"]``, and hand it to the IdP as the SCIM bearer token). The token is
org-bound, so provisioning is scoped to that token's organization.

Responses use ``application/scim+json`` and the SCIM error envelope. Kept out of
the OpenAPI schema — SCIM has its own (RFC 7643/7644) contract.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import scim_service
from grcen.services.scim_service import ScimError

router = APIRouter(prefix="/scim/v2", tags=["scim"], include_in_schema=False)

_SCIM_CT = "application/scim+json"
_sync_required = require_permission(Permission.SYNC)


def _scim(content: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status, media_type=_SCIM_CT)


def _error(exc: ScimError) -> JSONResponse:
    body = {
        "schemas": [scim_service.ERROR_SCHEMA],
        "detail": exc.detail,
        "status": str(exc.status),
    }
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return JSONResponse(content=body, status_code=exc.status, media_type=_SCIM_CT)


async def _json_body(request: Request):
    try:
        return await request.json()
    except Exception as exc:  # noqa: BLE001 — any parse failure is a 400
        raise ScimError(400, "invalid JSON body") from exc


@router.get("/ServiceProviderConfig")
async def service_provider_config(user: User = Depends(_sync_required)):
    return _scim({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "/INTEGRATIONS.md",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "Bearer Token",
            "description": "A GRCen API token scoped to the 'sync' permission.",
        }],
    })


@router.get("/Users")
async def list_users(
    request: Request, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    def _int(name: str, default: int) -> int:
        try:
            return int(request.query_params.get(name, default))
        except (TypeError, ValueError):
            return default

    try:
        result = await scim_service.list_users(
            pool, user.organization_id,
            filter_str=request.query_params.get("filter"),
            start_index=_int("startIndex", 1),
            count=_int("count", 100),
        )
    except ScimError as e:
        return _error(e)
    return _scim(result)


@router.post("/Users")
async def create_user(
    request: Request, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    try:
        payload = await _json_body(request)
        resource = await scim_service.create_user(pool, user.organization_id, payload)
    except ScimError as e:
        return _error(e)
    return _scim(resource, status=201)


@router.get("/Users/{user_id}")
async def get_user(
    user_id: UUID, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    try:
        return _scim(await scim_service.get_user(pool, user.organization_id, user_id))
    except ScimError as e:
        return _error(e)


@router.put("/Users/{user_id}")
async def replace_user(
    user_id: UUID, request: Request,
    pool=Depends(get_db), user: User = Depends(_sync_required),
):
    try:
        payload = await _json_body(request)
        return _scim(
            await scim_service.replace_user(pool, user.organization_id, user_id, payload)
        )
    except ScimError as e:
        return _error(e)


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: UUID, request: Request,
    pool=Depends(get_db), user: User = Depends(_sync_required),
):
    try:
        payload = await _json_body(request)
        return _scim(
            await scim_service.patch_user(pool, user.organization_id, user_id, payload)
        )
    except ScimError as e:
        return _error(e)


@router.delete("/Users/{user_id}")
async def delete_user(
    user_id: UUID, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    try:
        await scim_service.deactivate_user(pool, user.organization_id, user_id)
    except ScimError as e:
        return _error(e)
    return Response(status_code=204)


# ── Groups (I1: membership → role sync) ──────────────────────────────────


@router.get("/Groups")
async def list_groups(
    request: Request, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    def _int(name: str, default: int) -> int:
        try:
            return int(request.query_params.get(name, default))
        except (TypeError, ValueError):
            return default

    try:
        result = await scim_service.list_groups(
            pool, user.organization_id,
            filter_str=request.query_params.get("filter"),
            start_index=_int("startIndex", 1),
            count=_int("count", 100),
        )
    except ScimError as e:
        return _error(e)
    return _scim(result)


@router.post("/Groups")
async def create_group(
    request: Request, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    try:
        payload = await _json_body(request)
        resource = await scim_service.create_group(pool, user.organization_id, payload)
    except ScimError as e:
        return _error(e)
    return _scim(resource, status=201)


@router.get("/Groups/{group_id}")
async def get_group(
    group_id: UUID, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    try:
        return _scim(await scim_service.get_group(pool, user.organization_id, group_id))
    except ScimError as e:
        return _error(e)


@router.put("/Groups/{group_id}")
async def replace_group(
    group_id: UUID, request: Request,
    pool=Depends(get_db), user: User = Depends(_sync_required),
):
    try:
        payload = await _json_body(request)
        return _scim(
            await scim_service.replace_group(pool, user.organization_id, group_id, payload)
        )
    except ScimError as e:
        return _error(e)


@router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: UUID, request: Request,
    pool=Depends(get_db), user: User = Depends(_sync_required),
):
    try:
        payload = await _json_body(request)
        return _scim(
            await scim_service.patch_group(pool, user.organization_id, group_id, payload)
        )
    except ScimError as e:
        return _error(e)


@router.delete("/Groups/{group_id}")
async def delete_group(
    group_id: UUID, pool=Depends(get_db), user: User = Depends(_sync_required)
):
    try:
        await scim_service.delete_group(pool, user.organization_id, group_id)
    except ScimError as e:
        return _error(e)
    return Response(status_code=204)
