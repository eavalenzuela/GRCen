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
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

SOURCE = "scim"

# Role precedence (highest privilege first) for resolving a user's role from the
# roles their groups map to.
_ROLE_PRIORITY = [UserRole.ADMIN, UserRole.EDITOR, UserRole.AUDITOR, UserRole.VIEWER]


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
    try:
        user = await auth_svc.create_user(
            pool, parsed["username"], None, role, organization_id
        )
    except auth_svc.UserExistsError as exc:
        # The check above is a separate statement, so a concurrent POST can slip
        # between it and this insert; the unique index catches that.
        raise ScimError(
            409, f"userName '{parsed['username']}' already exists",
            scim_type="uniqueness",
        ) from exc
    if parsed["email"]:
        await auth_svc.set_user_email(pool, user.id, parsed["email"])
    if not parsed["active"]:
        await auth_svc.set_user_active(pool, user.id, False, organization_id=organization_id)

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
    await auth_svc.set_user_active(pool, user.id, parsed["active"], organization_id=organization_id)
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
        await auth_svc.set_user_active(pool, user.id, new_active, organization_id=organization_id)
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
    await auth_svc.set_user_active(pool, user.id, False, organization_id=organization_id)
    if user.person_asset_id:
        await pool.execute(
            "UPDATE assets SET status = 'archived', updated_at = now() "
            "WHERE id = $1 AND organization_id = $2",
            user.person_asset_id, organization_id,
        )


# ── Groups → role sync (I1) ───────────────────────────────────────────────
#
# An IdP provisions Groups and memberships via SCIM; GRCen maps a group's
# displayName to a role (configurable ``app_settings['scim_group_role_map']``,
# a {displayName: role} JSON object) and each member's login role becomes the
# highest-privilege role among the groups they belong to. Removed from every
# role-granting group → reset to the SCIM default role.


async def get_group_role_map(pool: asyncpg.Pool) -> dict[str, str]:
    row = await pool.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'scim_group_role_map'"
    )
    if row and row["value"]:
        try:
            data = json.loads(row["value"])
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


async def set_group_role_map(pool: asyncpg.Pool, mapping: dict) -> None:
    await pool.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES ('scim_group_role_map', $1, now())
           ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = now()""",
        json.dumps(mapping),
    )


async def set_default_role(pool: asyncpg.Pool, role: str) -> None:
    if role not in {r.value for r in UserRole}:
        role = UserRole.VIEWER.value
    await pool.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES ('scim_default_role', $1, now())
           ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = now()""",
        role,
    )


async def recompute_user_role(
    pool: asyncpg.Pool, organization_id: UUID, user_id: UUID
) -> None:
    """Set the user's role from their group memberships. Highest-privilege role
    among mapped groups wins; if they belong to no role-granting group, reset to
    the SCIM default (so removal from the admin group actually demotes)."""
    mapping = await get_group_role_map(pool)
    rows = await pool.fetch(
        """SELECT g.display_name FROM scim_group_members m
           JOIN scim_groups g ON g.id = m.group_id
           WHERE m.user_id = $1 AND g.organization_id = $2""",
        user_id, organization_id,
    )
    candidates: list[UserRole] = []
    for r in rows:
        mapped = mapping.get(r["display_name"])
        if mapped:
            try:
                candidates.append(UserRole(mapped))
            except ValueError:
                pass
    role = (
        min(candidates, key=lambda x: _ROLE_PRIORITY.index(x))
        if candidates else await get_default_role(pool)
    )
    await auth_svc.update_user_role(pool, user_id, role, organization_id=organization_id)


def parse_group(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ScimError(400, "request body must be a JSON object")
    display_name = payload.get("displayName")
    if not display_name or not isinstance(display_name, str):
        raise ScimError(400, "displayName is required", scim_type="invalidValue")
    members: list[str] = []
    for m in payload.get("members") or []:
        if isinstance(m, dict) and m.get("value"):
            members.append(str(m["value"]))
    return {
        "display_name": display_name,
        "external_id": payload.get("externalId") or None,
        "member_ids": members,
    }


async def _valid_member_ids(pool, organization_id: UUID, ids: list[str]) -> list[UUID]:
    """Keep only ids that are real users in this org (ignore unknown/foreign)."""
    out: list[UUID] = []
    for raw in ids:
        try:
            uid = UUID(str(raw))
        except (ValueError, TypeError):
            continue
        row = await pool.fetchrow(
            "SELECT 1 FROM users WHERE id = $1 AND organization_id = $2", uid, organization_id)
        if row:
            out.append(uid)
    return out


async def _group_members(pool, group_id: UUID) -> list[dict]:
    return [
        dict(r) for r in await pool.fetch(
            """SELECT m.user_id, u.username FROM scim_group_members m
               JOIN users u ON u.id = m.user_id
               WHERE m.group_id = $1 ORDER BY u.username""",
            group_id,
        )
    ]


def to_scim_group(group_row, member_rows) -> dict:
    resource: dict = {
        "schemas": [GROUP_SCHEMA],
        "id": str(group_row["id"]),
        "displayName": group_row["display_name"],
        "members": [
            {"value": str(m["user_id"]), "display": m["username"]} for m in member_rows
        ],
        "meta": {
            "resourceType": "Group",
            "location": f"/scim/v2/Groups/{group_row['id']}",
        },
    }
    if group_row["external_id"]:
        resource["externalId"] = group_row["external_id"]
    return resource


async def _load_group(pool, organization_id: UUID, group_id: UUID):
    row = await pool.fetchrow(
        "SELECT * FROM scim_groups WHERE id = $1 AND organization_id = $2",
        group_id, organization_id)
    if row is None:
        raise ScimError(404, f"Group {group_id} not found")
    return row


async def create_group(pool: asyncpg.Pool, organization_id: UUID, payload: object) -> dict:
    parsed = parse_group(payload)
    dup = await pool.fetchrow(
        "SELECT 1 FROM scim_groups WHERE organization_id = $1 AND display_name = $2",
        organization_id, parsed["display_name"])
    if dup:
        raise ScimError(409, f"group '{parsed['display_name']}' already exists",
                        scim_type="uniqueness")
    row = await pool.fetchrow(
        """INSERT INTO scim_groups (organization_id, external_id, display_name)
           VALUES ($1, $2, $3) RETURNING *""",
        organization_id, parsed["external_id"], parsed["display_name"])
    member_ids = await _valid_member_ids(pool, organization_id, parsed["member_ids"])
    for uid in member_ids:
        await pool.execute(
            "INSERT INTO scim_group_members (group_id, user_id) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING", row["id"], uid)
    for uid in member_ids:
        await recompute_user_role(pool, organization_id, uid)
    return to_scim_group(row, await _group_members(pool, row["id"]))


async def get_group(pool: asyncpg.Pool, organization_id: UUID, group_id: UUID) -> dict:
    row = await _load_group(pool, organization_id, group_id)
    return to_scim_group(row, await _group_members(pool, group_id))


async def list_groups(
    pool: asyncpg.Pool, organization_id: UUID, *,
    filter_str: str | None = None, start_index: int = 1, count: int = 100,
) -> dict:
    parsed = _parse_filter(filter_str)
    if filter_str and parsed is None:
        rows = []
    elif parsed is not None:
        attr, val = parsed[0].lower(), parsed[1]
        if attr == "displayname":
            rows = await pool.fetch(
                "SELECT * FROM scim_groups WHERE organization_id = $1 AND display_name = $2",
                organization_id, val)
        elif attr == "externalid":
            rows = await pool.fetch(
                "SELECT * FROM scim_groups WHERE organization_id = $1 AND external_id = $2",
                organization_id, val)
        else:
            rows = []
    else:
        rows = await pool.fetch(
            "SELECT * FROM scim_groups WHERE organization_id = $1 ORDER BY display_name",
            organization_id)
    resources = [to_scim_group(r, await _group_members(pool, r["id"])) for r in rows]
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


async def _set_members(pool, organization_id, group_id, new_ids: list[UUID]) -> set[UUID]:
    """Replace the group's members with new_ids. Returns the set of affected
    users (added ∪ removed) so callers can recompute their roles."""
    existing = {r["user_id"] for r in await pool.fetch(
        "SELECT user_id FROM scim_group_members WHERE group_id = $1", group_id)}
    target = set(new_ids)
    for uid in target - existing:
        await pool.execute(
            "INSERT INTO scim_group_members (group_id, user_id) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING", group_id, uid)
    for uid in existing - target:
        await pool.execute(
            "DELETE FROM scim_group_members WHERE group_id = $1 AND user_id = $2",
            group_id, uid)
    return existing ^ target


async def replace_group(pool, organization_id: UUID, group_id: UUID, payload: object) -> dict:
    await _load_group(pool, organization_id, group_id)
    parsed = parse_group(payload)
    await pool.execute(
        """UPDATE scim_groups SET display_name = $1, external_id = $2, updated_at = now()
           WHERE id = $3 AND organization_id = $4""",
        parsed["display_name"], parsed["external_id"], group_id, organization_id)
    member_ids = await _valid_member_ids(pool, organization_id, parsed["member_ids"])
    affected = await _set_members(pool, organization_id, group_id, member_ids)
    for uid in affected:
        await recompute_user_role(pool, organization_id, uid)
    row = await _load_group(pool, organization_id, group_id)
    return to_scim_group(row, await _group_members(pool, group_id))


_MEMBER_REMOVE_RE = re.compile(r'members\[\s*value\s+eq\s+"([^"]+)"\s*\]', re.IGNORECASE)


async def patch_group(pool, organization_id: UUID, group_id: UUID, payload: object) -> dict:
    await _load_group(pool, organization_id, group_id)
    ops = payload.get("Operations") if isinstance(payload, dict) else None
    if not isinstance(ops, list):
        raise ScimError(400, "PATCH requires an Operations array", scim_type="invalidValue")

    affected: set[UUID] = set()
    for op in ops:
        if not isinstance(op, dict):
            continue
        operation = (op.get("op") or "").lower()
        path = op.get("path") or ""
        value = op.get("value")

        # displayName replace
        if operation == "replace" and (path == "displayName"
                                       or (not path and isinstance(value, dict) and "displayName" in value)):
            new_name = value if path == "displayName" else value.get("displayName")
            if new_name:
                await pool.execute(
                    "UPDATE scim_groups SET display_name = $1, updated_at = now() "
                    "WHERE id = $2 AND organization_id = $3",
                    str(new_name), group_id, organization_id)
            continue

        # member add / remove / replace
        m = _MEMBER_REMOVE_RE.match(path)
        member_replace = operation == "replace" and (
            path == "members"
            or (not path and isinstance(value, dict) and "members" in value)
        )
        if operation == "remove" and m:
            ids = await _valid_member_ids(pool, organization_id, [m.group(1)])
        else:
            raw_members = (
                value.get("members")
                if (not path and isinstance(value, dict))
                else value
            )
            raw = [
                v.get("value") for v in (raw_members or [])
                if isinstance(v, dict) and v.get("value")
            ]
            ids = await _valid_member_ids(pool, organization_id, raw)

        if member_replace:
            # `replace` on members is authoritative — the supplied list *is* the
            # membership, so anyone absent from it is no longer a member. This
            # was handled as add-only, which left users the IdP had dropped
            # still in the group and still holding the role it grants. PUT
            # (replace_group) already had these semantics; PATCH did not.
            existing = [
                r["user_id"] for r in await pool.fetch(
                    "SELECT user_id FROM scim_group_members WHERE group_id = $1",
                    group_id,
                )
            ]
            keep = set(ids)
            removed = [u for u in existing if u not in keep]
            if removed:
                await pool.execute(
                    "DELETE FROM scim_group_members "
                    "WHERE group_id = $1 AND user_id = ANY($2::uuid[])",
                    group_id, removed,
                )
                affected.update(removed)

        if not ids:
            continue
        for uid in ids:
            if operation == "remove":
                await pool.execute(
                    "DELETE FROM scim_group_members WHERE group_id = $1 AND user_id = $2",
                    group_id, uid)
            else:  # add / replace members
                await pool.execute(
                    "INSERT INTO scim_group_members (group_id, user_id) VALUES ($1, $2) "
                    "ON CONFLICT DO NOTHING", group_id, uid)
            affected.add(uid)

    for uid in affected:
        await recompute_user_role(pool, organization_id, uid)
    row = await _load_group(pool, organization_id, group_id)
    return to_scim_group(row, await _group_members(pool, group_id))


async def delete_group(pool, organization_id: UUID, group_id: UUID) -> None:
    await _load_group(pool, organization_id, group_id)
    members = [r["user_id"] for r in await pool.fetch(
        "SELECT user_id FROM scim_group_members WHERE group_id = $1", group_id)]
    await pool.execute(
        "DELETE FROM scim_groups WHERE id = $1 AND organization_id = $2",
        group_id, organization_id)
    for uid in members:
        await recompute_user_role(pool, organization_id, uid)
