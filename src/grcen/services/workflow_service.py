"""Workflow / approval gating for asset writes.

Per-type configuration in `workflow_config` decides whether create / update /
delete actions on a given asset type require approval. When required, the
write becomes a row in `pending_changes` instead of touching `assets`. An
approver consumes the queue: approval applies the recorded payload through
the normal asset service; rejection / withdrawal closes it without effect.
"""
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from grcen.models.asset import Asset, AssetType
from grcen.models.user import User
from grcen.services import asset as asset_svc
from grcen.services import audit_service as audit_svc

# Asset fields tracked by the audit log — kept in sync with routers.assets._ASSET_FIELDS
_ASSET_FIELDS = ["name", "description", "status", "owner", "metadata"]


@dataclass
class WorkflowConfig:
    asset_type: str
    require_approval_create: bool
    require_approval_update: bool
    require_approval_delete: bool


@dataclass
class PendingChange:
    id: UUID
    action: str  # 'create' | 'update' | 'delete'
    asset_type: str
    target_asset_id: UUID | None
    title: str
    payload: dict
    status: str  # 'pending' | 'approved' | 'rejected' | 'withdrawn'
    submitted_by: UUID | None
    submitted_by_username: str
    submitted_at: datetime
    decided_by: UUID | None
    decided_by_username: str | None
    decided_at: datetime | None
    decision_note: str | None

    @classmethod
    def from_row(cls, row) -> "PendingChange":
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return cls(
            id=row["id"],
            action=row["action"],
            asset_type=row["asset_type"],
            target_asset_id=row["target_asset_id"],
            title=row["title"],
            payload=payload or {},
            status=row["status"],
            submitted_by=row["submitted_by"],
            submitted_by_username=row["submitted_by_username"],
            submitted_at=row["submitted_at"],
            decided_by=row["decided_by"],
            decided_by_username=row["decided_by_username"],
            decided_at=row["decided_at"],
            decision_note=row["decision_note"],
        )


# ---- workflow_config ----------------------------------------------------

async def get_config(pool: asyncpg.Pool, asset_type: AssetType) -> WorkflowConfig:
    row = await pool.fetchrow(
        "SELECT * FROM workflow_config WHERE asset_type = $1",
        asset_type.value,
    )
    if not row:
        return WorkflowConfig(
            asset_type=asset_type.value,
            require_approval_create=False,
            require_approval_update=False,
            require_approval_delete=False,
        )
    return WorkflowConfig(
        asset_type=row["asset_type"],
        require_approval_create=row["require_approval_create"],
        require_approval_update=row["require_approval_update"],
        require_approval_delete=row["require_approval_delete"],
    )


async def list_configs(pool: asyncpg.Pool) -> dict[str, WorkflowConfig]:
    rows = await pool.fetch("SELECT * FROM workflow_config")
    return {
        r["asset_type"]: WorkflowConfig(
            asset_type=r["asset_type"],
            require_approval_create=r["require_approval_create"],
            require_approval_update=r["require_approval_update"],
            require_approval_delete=r["require_approval_delete"],
        )
        for r in rows
    }


async def upsert_config(
    pool: asyncpg.Pool,
    asset_type: AssetType,
    *,
    require_approval_create: bool,
    require_approval_update: bool,
    require_approval_delete: bool,
) -> None:
    await pool.execute(
        """
        INSERT INTO workflow_config (asset_type, require_approval_create,
            require_approval_update, require_approval_delete, updated_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (asset_type) DO UPDATE SET
            require_approval_create = EXCLUDED.require_approval_create,
            require_approval_update = EXCLUDED.require_approval_update,
            require_approval_delete = EXCLUDED.require_approval_delete,
            updated_at = now()
        """,
        asset_type.value,
        require_approval_create,
        require_approval_update,
        require_approval_delete,
    )


async def requires_approval(
    pool: asyncpg.Pool, asset_type: AssetType, action: str
) -> bool:
    cfg = await get_config(pool, asset_type)
    if action == "create":
        return cfg.require_approval_create
    if action == "update":
        return cfg.require_approval_update
    if action == "delete":
        return cfg.require_approval_delete
    return False


# ---- pending_changes ----------------------------------------------------

async def submit(
    pool: asyncpg.Pool,
    *,
    action: str,
    asset_type: AssetType,
    target_asset_id: UUID | None,
    title: str,
    payload: dict,
    user: User,
) -> PendingChange:
    if action not in ("create", "update", "delete"):
        raise ValueError(f"Invalid pending-change action: {action}")
    # Block stacking duplicate pending changes for the same target+action
    if target_asset_id is not None:
        existing = await pool.fetchrow(
            """SELECT id FROM pending_changes
               WHERE target_asset_id = $1 AND action = $2 AND status = 'pending'""",
            target_asset_id,
            action,
        )
        if existing:
            raise ValueError(
                "An identical pending change for this asset is already awaiting approval."
            )
    row = await pool.fetchrow(
        """
        INSERT INTO pending_changes (id, action, asset_type, target_asset_id,
            title, payload, status, submitted_by, submitted_by_username)
        VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7, $8)
        RETURNING *
        """,
        uuid.uuid4(),
        action,
        asset_type.value,
        target_asset_id,
        title,
        json.dumps(payload),
        user.id,
        user.username,
    )
    return PendingChange.from_row(row)


async def get(pool: asyncpg.Pool, change_id: UUID) -> PendingChange | None:
    row = await pool.fetchrow("SELECT * FROM pending_changes WHERE id = $1", change_id)
    return PendingChange.from_row(row) if row else None


async def list_changes(
    pool: asyncpg.Pool,
    *,
    status: str | None = "pending",
    target_asset_id: UUID | None = None,
    submitted_by: UUID | None = None,
) -> list[PendingChange]:
    where: list[str] = []
    vals: list[Any] = []
    idx = 1
    if status:
        where.append(f"status = ${idx}")
        vals.append(status)
        idx += 1
    if target_asset_id is not None:
        where.append(f"target_asset_id = ${idx}")
        vals.append(target_asset_id)
        idx += 1
    if submitted_by is not None:
        where.append(f"submitted_by = ${idx}")
        vals.append(submitted_by)
        idx += 1
    where_clause = " AND ".join(where) if where else "TRUE"
    rows = await pool.fetch(
        f"""SELECT * FROM pending_changes
            WHERE {where_clause}
            ORDER BY submitted_at DESC""",
        *vals,
    )
    return [PendingChange.from_row(r) for r in rows]


async def withdraw(
    pool: asyncpg.Pool, change: PendingChange, user: User, *, note: str | None = None
) -> PendingChange:
    if change.status != "pending":
        raise ValueError("Only pending changes can be withdrawn.")
    if change.submitted_by != user.id:
        raise PermissionError("Only the submitter may withdraw a pending change.")
    row = await pool.fetchrow(
        """UPDATE pending_changes
           SET status='withdrawn', decided_by=$1, decided_by_username=$2,
               decided_at=now(), decision_note=$3
           WHERE id=$4 AND status='pending'
           RETURNING *""",
        user.id,
        user.username,
        note,
        change.id,
    )
    return PendingChange.from_row(row)


async def reject(
    pool: asyncpg.Pool, change: PendingChange, approver: User, *, note: str | None = None
) -> PendingChange:
    if change.status != "pending":
        raise ValueError("Only pending changes can be rejected.")
    if change.submitted_by == approver.id:
        raise PermissionError("Approvers may not act on their own pending change.")
    row = await pool.fetchrow(
        """UPDATE pending_changes
           SET status='rejected', decided_by=$1, decided_by_username=$2,
               decided_at=now(), decision_note=$3
           WHERE id=$4 AND status='pending'
           RETURNING *""",
        approver.id,
        approver.username,
        note,
        change.id,
    )
    return PendingChange.from_row(row)


async def approve(
    pool: asyncpg.Pool, change: PendingChange, approver: User, *, note: str | None = None
) -> tuple[PendingChange, Asset | None]:
    """Approve a pending change and apply it.

    Returns (updated_change, resulting_asset_or_None_for_delete).
    The asset write and the status transition share a transaction so a partial
    apply cannot leave the queue in a bogus state.
    """
    if change.status != "pending":
        raise ValueError("Only pending changes can be approved.")
    if change.submitted_by == approver.id:
        raise PermissionError("Approvers may not act on their own pending change.")

    asset_type = AssetType(change.asset_type)
    payload = change.payload or {}
    asset: Asset | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            if change.action == "create":
                asset = await asset_svc.create_asset(
                    conn,
                    type=asset_type,
                    name=payload.get("name") or change.title,
                    description=payload.get("description"),
                    status=payload.get("status") or "active",
                    owner_id=_uuid_or_none(payload.get("owner_id")),
                    metadata_=payload.get("metadata") or {},
                    updated_by=approver.id,
                    tags=payload.get("tags") or [],
                    criticality=payload.get("criticality"),
                )
                # Record the audit event under the approver, with the submitter noted.
                await audit_svc.log_audit_event(
                    conn,
                    user_id=approver.id,
                    username=approver.username,
                    action="create",
                    entity_type="asset",
                    entity_id=asset.id,
                    entity_name=asset.name,
                    changes={
                        "_workflow": {
                            "submitted_by": change.submitted_by_username,
                            "pending_change_id": str(change.id),
                        },
                        **audit_svc.create_snapshot(asset.__dict__, _ASSET_FIELDS),
                    },
                )
            elif change.action == "update":
                old = await asset_svc.get_asset(conn, change.target_asset_id)
                if old is None:
                    raise ValueError("Target asset no longer exists.")
                kwargs: dict[str, Any] = {}
                for key in ("name", "description", "status", "criticality"):
                    if key in payload:
                        kwargs[key] = payload[key]
                if "owner_id" in payload:
                    kwargs["owner_id"] = _uuid_or_none(payload["owner_id"])
                if "metadata" in payload:
                    kwargs["metadata_"] = payload["metadata"]
                if "tags" in payload:
                    kwargs["tags"] = payload["tags"]
                asset = await asset_svc.update_asset(
                    conn, change.target_asset_id, updated_by=approver.id, **kwargs
                )
                if asset is not None:
                    diff = audit_svc.compute_diff(
                        old.__dict__, asset.__dict__, _ASSET_FIELDS
                    )
                    if diff:
                        await audit_svc.log_audit_event(
                            conn,
                            user_id=approver.id,
                            username=approver.username,
                            action="update",
                            entity_type="asset",
                            entity_id=asset.id,
                            entity_name=asset.name,
                            changes={
                                "_workflow": {
                                    "submitted_by": change.submitted_by_username,
                                    "pending_change_id": str(change.id),
                                },
                                **diff,
                            },
                        )
            elif change.action == "delete":
                old = await asset_svc.get_asset(conn, change.target_asset_id)
                if old is None:
                    raise ValueError("Target asset no longer exists.")
                await asset_svc.delete_asset(conn, change.target_asset_id)
                await audit_svc.log_audit_event(
                    conn,
                    user_id=approver.id,
                    username=approver.username,
                    action="delete",
                    entity_type="asset",
                    entity_id=old.id,
                    entity_name=old.name,
                    changes={
                        "_workflow": {
                            "submitted_by": change.submitted_by_username,
                            "pending_change_id": str(change.id),
                        },
                        **audit_svc.delete_snapshot(old.__dict__, _ASSET_FIELDS),
                    },
                )
            row = await conn.fetchrow(
                """UPDATE pending_changes
                   SET status='approved', decided_by=$1, decided_by_username=$2,
                       decided_at=now(), decision_note=$3
                   WHERE id=$4 AND status='pending'
                   RETURNING *""",
                approver.id,
                approver.username,
                note,
                change.id,
            )
    return PendingChange.from_row(row), asset


def _uuid_or_none(val) -> UUID | None:
    if val in (None, "", "None"):
        return None
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val))
    except (ValueError, TypeError):
        return None


# ---- payload helpers ----------------------------------------------------

def asset_create_payload(
    *,
    name: str,
    description: str | None,
    status: str,
    owner_id: UUID | None,
    metadata: dict | None,
    tags: list[str] | None,
    criticality: str | None,
) -> dict:
    return {
        "name": name,
        "description": description,
        "status": status,
        "owner_id": str(owner_id) if owner_id else None,
        "metadata": metadata or {},
        "tags": tags or [],
        "criticality": criticality,
    }


def asset_update_payload(updates: dict) -> dict:
    """Coerce update kwargs into a JSON-safe payload."""
    out: dict = {}
    for key, val in updates.items():
        if key == "owner_id":
            out["owner_id"] = str(val) if val else None
        elif key == "metadata_":
            out["metadata"] = val or {}
        elif val is not None:
            out[key] = val
    return out
