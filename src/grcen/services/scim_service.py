"""SCIM 2.0 provisioning — the ingress connector for an IdP / directory.

Closes the deprovisioning gap that login-time OIDC/SAML JIT leaves open: an IdP
pushes user create / update / deactivate here, so a leaver disabled upstream is
disabled in GRCen without waiting for a (never-coming) final login.

A SCIM User maps to a GRCen login ``user`` plus a linked ``person`` asset. The
Person is externally-owned — projected through ``external_sync`` under
``source='scim'`` with the IdP's stable id (``externalId``, else ``userName``)
as ``source_ref`` — so it carries provenance like any synced asset. Deprovision
(``active:false`` via PUT/PATCH, or ``DELETE``) deactivates the login and
archives the Person. GRCen never hard-deletes on a SCIM DELETE: that would
cascade away audit history and any human-authored edges on the Person, so it
soft-deactivates instead.

Group→role mapping is deliberately out of this first slice: provisioned users
get the configured default role (``app_settings['scim_default_role']``, default
viewer); role sync via a Groups resource is a follow-up.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

import asyncpg

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import auth as auth_svc
from grcen.services import external_sync

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
ENTERPRISE_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

SOURCE = "scim"


class ScimError(Exception):
    """A SCIM-shaped failure. The router renders it as a SCIM Error response."""

    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type


# ── settings ──────────────────────────────────────────────────────────────


async def get_default_role(pool: asyncpg.Pool) -> UserRole:
    row = await pool.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'scim_default_role'"
    )
    if row:
        try:
            return UserRole(row["value"])
        except ValueError:
            pass
    return UserRole.VIEWER


# ── parse an incoming SCIM User resource ──────────────────────────────────


def _first_email(payload: dict) -> str | None:
    emails = payload.get("emails")
    if isinstance(emails, list) and emails:
        primary = next(
            (e for e in emails if isinstance(e, dict) and e.get("primary")), None
        )
        chosen = primary or next((e for e in emails if isinstance(e, dict)), None)
        if chosen and chosen.get("value"):
            return chosen["value"]
    return None


def _enterprise(payload: dict) -> dict:
    ent = payload.get(ENTERPRISE_SCHEMA)
    return ent if isinstance(ent, dict) else {}


def _display_name(payload: dict, username: str) -> str:
    dn = payload.get("displayName")
    if dn:
        return dn
    name = payload.get("name")
    if isinstance(name, dict):
        if name.get("formatted"):
            return name["formatted"]
        joined = " ".join(
            p for p in (name.get("givenName"), name.get("familyName")) if p
        )
        if joined:
            return joined
    return username


def parse_user(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ScimError(400, "request body must be a JSON object")
    username = payload.get("userName")
    if not username or not isinstance(username, str):
        raise ScimError(400, "userName is required", scim_type="invalidValue")
    ent = _enterprise(payload)
    manager = ent.get("manager")
    if isinstance(manager, dict):
        manager = manager.get("displayName") or manager.get("value")
    active = payload.get("active")
    return {
        "username": username,
        "external_id": payload.get("externalId") or None,
        "active": True if active is None else _coerce_bool(active),
        "display_name": _display_name(payload, username),
        "email": _first_email(payload),
        "title": payload.get("title") or None,
        "department": ent.get("department") or None,
        "employee_id": ent.get("employeeNumber") or None,
        "manager": manager or None,
    }


def _person_metadata(parsed: dict) -> dict:
    meta: dict = {}
    for key in ("email", "title", "department", "employee_id", "manager"):
        if parsed.get(key):
            meta[key] = parsed[key]
    return meta


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


# ── serialise GRCen user (+ person) back to a SCIM resource ───────────────


def to_scim(user, person_row) -> dict:
    meta: dict = {}
    external_id: str | None = None
    display_name = user.username
    if person_row is not None:
        display_name = person_row["name"]
        pm = person_row["metadata"]
        if isinstance(pm, str):
            pm = json.loads(pm) if pm else {}
        meta = pm or {}
        if person_row["source"] == SOURCE:
            external_id = person_row["source_ref"]

    resource: dict = {
        "schemas": [USER_SCHEMA],
        "id": str(user.id),
        "userName": user.username,
        "active": user.is_active,
        "displayName": display_name,
        "meta": {
            "resourceType": "User",
            "location": f"/scim/v2/Users/{user.id}",
        },
    }
    if external_id:
        resource["externalId"] = external_id
    if user.email:
        resource["emails"] = [{"value": user.email, "primary": True}]
    if meta.get("title"):
        resource["title"] = meta["title"]

    ent: dict = {}
    if meta.get("employee_id"):
        ent["employeeNumber"] = meta["employee_id"]
    if meta.get("department"):
        ent["department"] = meta["department"]
    if meta.get("manager"):
        ent["manager"] = {"displayName": meta["manager"]}
    if ent:
        resource["schemas"].append(ENTERPRISE_SCHEMA)
        resource[ENTERPRISE_SCHEMA] = ent
    return resource


async def _person_row(pool: asyncpg.Pool, person_id: UUID | None):
    if person_id is None:
        return None
    return await pool.fetchrow(
        "SELECT id, name, metadata, source, source_ref, status "
        "FROM assets WHERE id = $1",
        person_id,
    )


async def _load_view(pool: asyncpg.Pool, user) -> dict:
    return to_scim(user, await _person_row(pool, user.person_asset_id))


# ── CRUD ──────────────────────────────────────────────────────────────────


async def create_user(
    pool: asyncpg.Pool, organization_id: UUID, payload: object
) -> dict:
    parsed = parse_user(payload)

    if await auth_svc.get_user_by_username(pool, parsed["username"]):
        raise ScimError(
            409, f"userName '{parsed['username']}' already exists",
            scim_type="uniqueness",
        )
    source_ref = parsed["external_id"] or parsed["username"]
    dup = await pool.fetchrow(
        "SELECT id FROM assets "
        "WHERE organization_id = $1 AND source = $2 AND source_ref = $3",
        organization_id, SOURCE, source_ref,
    )
    if dup:
        raise ScimError(
            409, "a user is already provisioned for this externalId",
            scim_type="uniqueness",
        )

    role = await get_default_role(pool)
    user = await auth_svc.create_user(
        pool, parsed["username"], None, role, organization_id
    )
    if parsed["email"]:
        await auth_svc.set_user_email(pool, user.id, parsed["email"])
    if not parsed["active"]:
        await auth_svc.set_user_active(pool, user.id, False)

    up = await external_sync.upsert_external_asset(
        pool,
        organization_id=organization_id,
        source=SOURCE,
        source_ref=source_ref,
        asset_type=AssetType.PERSON.value,
        name=parsed["display_name"],
        status="active" if parsed["active"] else "archived",
        metadata=_person_metadata(parsed),
    )
    await auth_svc.set_person_asset_link(pool, user.id, up.id)

    user = await auth_svc.get_user_by_id(pool, user.id)
    return await _load_view(pool, user)


async def get_user(
    pool: asyncpg.Pool, organization_id: UUID, user_id: UUID
) -> dict:
    user = await auth_svc.get_user_by_id(pool, user_id)
    if not user or user.organization_id != organization_id:
        raise ScimError(404, f"User {user_id} not found")
    return await _load_view(pool, user)


def _parse_filter(filter_str: str | None) -> tuple[str, str] | None:
    if not filter_str:
        return None
    m = re.match(r'\s*(\w+)\s+eq\s+"([^"]*)"\s*$', filter_str, re.IGNORECASE)
    return (m.group(1), m.group(2)) if m else None


async def list_users(
    pool: asyncpg.Pool,
    organization_id: UUID,
    *,
    filter_str: str | None = None,
    start_index: int = 1,
    count: int = 100,
) -> dict:
    matched: list = []
    parsed = _parse_filter(filter_str)
    if filter_str and parsed is None:
        # A filter we don't understand → an empty, valid ListResponse rather
        # than a 400, so an IdP's existence probe simply finds nothing.
        matched = []
    elif parsed is not None:
        attr, val = parsed[0].lower(), parsed[1]
        if attr == "username":
            u = await auth_svc.get_user_by_username(pool, val)
            if u and u.organization_id == organization_id:
                matched = [u]
        elif attr == "externalid":
            row = await pool.fetchrow(
                "SELECT u.id FROM assets a JOIN users u ON u.person_asset_id = a.id "
                "WHERE a.organization_id = $1 AND a.source = $2 AND a.source_ref = $3",
                organization_id, SOURCE, val,
            )
            if row:
                u = await auth_svc.get_user_by_id(pool, row["id"])
                if u:
                    matched = [u]
    else:
        matched = await auth_svc.list_users(pool, organization_id=organization_id)

    resources = [await _load_view(pool, u) for u in matched]
    total = len(resources)
    start = max(start_index, 1)
    page = resources[start - 1: start - 1 + count]
    return {
        "schemas": [LIST_RESPONSE_SCHEMA],
        "totalResults": total,
        "startIndex": start,
        "itemsPerPage": len(page),
        "Resources": page,
    }


async def _write_person(
    pool: asyncpg.Pool, organization_id: UUID, user, parsed: dict
) -> None:
    """Update the linked Person in place (preserving its row/provenance), or
    project a fresh one via external_sync if the user has none yet."""
    status = "active" if parsed["active"] else "archived"
    meta = _person_metadata(parsed)
    if user.person_asset_id:
        await pool.execute(
            """UPDATE assets SET name = $1, metadata = $2, status = $3, updated_at = now()
               WHERE id = $4 AND organization_id = $5""",
            parsed["display_name"], json.dumps(meta), status,
            user.person_asset_id, organization_id,
        )
    else:
        up = await external_sync.upsert_external_asset(
            pool,
            organization_id=organization_id,
            source=SOURCE,
            source_ref=parsed["external_id"] or parsed["username"],
            asset_type=AssetType.PERSON.value,
            name=parsed["display_name"],
            status=status,
            metadata=meta,
        )
        await auth_svc.set_person_asset_link(pool, user.id, up.id)


async def replace_user(
    pool: asyncpg.Pool, organization_id: UUID, user_id: UUID, payload: object
) -> dict:
    user = await auth_svc.get_user_by_id(pool, user_id)
    if not user or user.organization_id != organization_id:
        raise ScimError(404, f"User {user_id} not found")
    parsed = parse_user(payload)
    await auth_svc.set_user_email(pool, user.id, parsed["email"])
    await auth_svc.set_user_active(pool, user.id, parsed["active"])
    await _write_person(pool, organization_id, user, parsed)
    user = await auth_svc.get_user_by_id(pool, user.id)
    return await _load_view(pool, user)


async def patch_user(
    pool: asyncpg.Pool, organization_id: UUID, user_id: UUID, payload: object
) -> dict:
    user = await auth_svc.get_user_by_id(pool, user_id)
    if not user or user.organization_id != organization_id:
        raise ScimError(404, f"User {user_id} not found")
    ops = payload.get("Operations") if isinstance(payload, dict) else None
    if not isinstance(ops, list):
        raise ScimError(400, "PATCH requires an Operations array",
                        scim_type="invalidValue")

    new_active: bool | None = None
    for op in ops:
        if not isinstance(op, dict):
            continue
        if (op.get("op") or "").lower() not in ("replace", "add"):
            continue
        path, value = op.get("path"), op.get("value")
        if path == "active":
            new_active = _coerce_bool(value)
        elif path is None and isinstance(value, dict) and "active" in value:
            new_active = _coerce_bool(value["active"])
        # Other paths are intentionally ignored in this slice.

    if new_active is not None and new_active != user.is_active:
        await auth_svc.set_user_active(pool, user.id, new_active)
        if user.person_asset_id:
            await pool.execute(
                "UPDATE assets SET status = $1, updated_at = now() "
                "WHERE id = $2 AND organization_id = $3",
                "active" if new_active else "archived",
                user.person_asset_id, organization_id,
            )
    user = await auth_svc.get_user_by_id(pool, user.id)
    return await _load_view(pool, user)


async def deactivate_user(
    pool: asyncpg.Pool, organization_id: UUID, user_id: UUID
) -> None:
    """SCIM DELETE → soft deprovision: deactivate login, archive Person."""
    user = await auth_svc.get_user_by_id(pool, user_id)
    if not user or user.organization_id != organization_id:
        raise ScimError(404, f"User {user_id} not found")
    await auth_svc.set_user_active(pool, user.id, False)
    if user.person_asset_id:
        await pool.execute(
            "UPDATE assets SET status = 'archived', updated_at = now() "
            "WHERE id = $1 AND organization_id = $2",
            user.person_asset_id, organization_id,
        )
