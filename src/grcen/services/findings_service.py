"""Findings register helpers: audit rollups, overdue tracking, gated closure.

A finding is ``asset(type=finding)`` with a CAPA lifecycle (``finding_status``),
severity, remediation due date, root cause, and corrective-action plan. It links
to what it hits (``relates_to`` → Control/Requirement/Risk/System), where it came
from (``raised_by`` → Audit), and who owns it (``owner_id`` → Person). The generic
register framework gives it a register-grade surface at ``/registers/findings``
for free; these helpers add the cross-asset rollup the Audit panel needs and a
closure gate (independent verification + a corrective-action plan).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

# A finding stops counting as "open" once it reaches a terminal disposition.
TERMINAL_STATUSES = frozenset({"closed", "risk_accepted"})


def _parse(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


async def audit_finding_rollup(
    pool: asyncpg.Pool, audit_ids: list[UUID]
) -> dict[UUID, dict[str, int]]:
    """{audit_id: {open, total}} from Finding assets linked to the audit.

    Replaces the stale ``open_findings`` integer on the Audit with a live count
    of linked, non-terminal findings.
    """
    out: dict[UUID, dict[str, int]] = {aid: {"open": 0, "total": 0} for aid in audit_ids}
    if not audit_ids:
        return out
    rows = await pool.fetch(
        """SELECT DISTINCT f.id AS finding_id, a.id AS audit_id, f.metadata AS meta
           FROM assets f
           JOIN relationships r
             ON (r.source_asset_id = f.id OR r.target_asset_id = f.id)
           JOIN assets a
             ON a.id = CASE WHEN r.source_asset_id = f.id
                            THEN r.target_asset_id ELSE r.source_asset_id END
            AND a.type = 'audit'
           WHERE f.type = 'finding' AND a.id = ANY($1::uuid[])""",
        audit_ids,
    )
    for r in rows:
        bucket = out[r["audit_id"]]
        bucket["total"] += 1
        if _parse(r["meta"]).get("finding_status") not in TERMINAL_STATUSES:
            bucket["open"] += 1
    return out


async def overdue_findings(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None, today: date | None = None
) -> list[dict[str, Any]]:
    """Non-terminal findings past their remediation due date."""
    today = today or date.today()
    rows = await pool.fetch(
        """SELECT id, name, owner, metadata FROM assets
           WHERE type = 'finding' AND status = 'active'
             AND ($1::uuid IS NULL OR organization_id = $1)
           ORDER BY name""",
        organization_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = _parse(r["metadata"])
        if meta.get("finding_status") in TERMINAL_STATUSES:
            continue
        due = meta.get("due_date")
        if not due:
            continue
        try:
            overdue = date.fromisoformat(due) < today
        except (ValueError, TypeError):
            continue
        if overdue:
            out.append({
                "id": r["id"], "name": r["name"], "owner": r["owner"],
                "severity": meta.get("severity"), "due_date": due,
                "finding_status": meta.get("finding_status"),
            })
    return out


async def close_finding(
    pool: asyncpg.Pool, finding_id: UUID, *, verified_by: str, organization_id: UUID
) -> dict[str, Any]:
    """Close a finding. Gated: it needs a corrective-action plan and independent
    verification — the verifier may not be the finding's own owner."""
    row = await pool.fetchrow(
        """SELECT f.id, f.metadata, COALESCE(p.name, f.owner) AS owner_name
           FROM assets f LEFT JOIN assets p ON p.id = f.owner_id
           WHERE f.id = $1 AND f.type = 'finding' AND f.organization_id = $2""",
        finding_id, organization_id,
    )
    if row is None:
        raise ValueError("finding not found")
    meta = _parse(row["metadata"])
    if not str(meta.get("corrective_action_plan") or "").strip():
        raise ValueError("a corrective action plan is required before closing")
    if not str(verified_by or "").strip():
        raise ValueError("a verifier is required")
    owner = str(row["owner_name"] or "").strip().lower()
    if owner and owner == verified_by.strip().lower():
        raise ValueError(
            "independent verification required: the verifier cannot be the finding owner"
        )
    meta["finding_status"] = "closed"
    meta["verified_by"] = verified_by
    meta["verified_at"] = date.today().isoformat()
    await pool.execute(
        "UPDATE assets SET metadata = $1::jsonb, updated_at = now() WHERE id = $2",
        json.dumps(meta), finding_id,
    )
    return meta
