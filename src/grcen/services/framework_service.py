"""Compliance framework dashboard queries.

A *framework* (asset type ``framework``) represents a compliance standard
such as SOC 2, PCI DSS, GDPR, or ISO 27001.  Its requirements are linked
via ``parent_of`` edges, audits via ``certifies``, and vendors that claim
certification via ``certified_by`` (vendor → framework).

A requirement is considered *satisfied* when any of these hold:

- It has an outgoing ``satisfied_by`` edge to a policy, or
- It has an outgoing ``implemented_by`` edge to any asset, or
- Some control points at it with a ``satisfies`` edge.

Otherwise the requirement is a *gap*.  Coverage is the fraction of
requirements that are satisfied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


@dataclass
class FrameworkSummary:
    id: UUID
    name: str
    metadata: dict[str, Any]
    requirement_count: int
    satisfied_count: int

    @property
    def coverage_percent(self) -> int:
        if self.requirement_count == 0:
            return 0
        return round(100 * self.satisfied_count / self.requirement_count)


@dataclass
class RequirementStatus:
    id: UUID
    name: str
    satisfied: bool
    satisfiers: list[dict[str, Any]]  # [{id, name, type, via}]


@dataclass
class FrameworkDetail:
    framework: dict[str, Any]  # asset row (name, description, metadata, ...)
    requirements: list[RequirementStatus]
    audits: list[dict[str, Any]]
    vendors: list[dict[str, Any]]
    in_scope_assets: list[dict[str, Any]]

    @property
    def satisfied_count(self) -> int:
        return sum(1 for r in self.requirements if r.satisfied)

    @property
    def gap_count(self) -> int:
        return sum(1 for r in self.requirements if not r.satisfied)

    @property
    def coverage_percent(self) -> int:
        if not self.requirements:
            return 0
        return round(100 * self.satisfied_count / len(self.requirements))


# Edges that mark a requirement as satisfied.
_OUTBOUND_SATISFIES = ("satisfied_by", "implemented_by")
_INBOUND_SATISFIES = ("satisfies",)


def _parse_metadata(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


async def list_frameworks(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[FrameworkSummary]:
    """Return every framework with its requirement count and satisfied count."""
    fw_rows = await pool.fetch(
        """SELECT id, name, metadata FROM assets
           WHERE type = 'framework'
             AND ($1::uuid IS NULL OR organization_id = $1)
           ORDER BY name""",
        organization_id,
    )
    if not fw_rows:
        return []

    summaries: list[FrameworkSummary] = []
    for fw in fw_rows:
        req_ids = await _requirement_ids(pool, fw["id"])
        if not req_ids:
            summaries.append(
                FrameworkSummary(
                    id=fw["id"],
                    name=fw["name"],
                    metadata=_parse_metadata(fw["metadata"]),
                    requirement_count=0,
                    satisfied_count=0,
                )
            )
            continue
        satisfied_map = await _satisfied_requirements(pool, req_ids)
        summaries.append(
            FrameworkSummary(
                id=fw["id"],
                name=fw["name"],
                metadata=_parse_metadata(fw["metadata"]),
                requirement_count=len(req_ids),
                satisfied_count=sum(1 for rid in req_ids if satisfied_map.get(rid)),
            )
        )
    return summaries


async def get_framework_detail(
    pool: asyncpg.Pool, framework_id: UUID, *, organization_id: UUID | None = None
) -> FrameworkDetail | None:
    fw = await pool.fetchrow(
        """SELECT id, name, description, metadata, status FROM assets
           WHERE id = $1 AND type = 'framework'
             AND ($2::uuid IS NULL OR organization_id = $2)""",
        framework_id,
        organization_id,
    )
    if not fw:
        return None

    framework = {
        "id": fw["id"],
        "name": fw["name"],
        "description": fw["description"],
        "status": fw["status"],
        "metadata": _parse_metadata(fw["metadata"]),
    }

    req_ids = await _requirement_ids(pool, framework_id)
    requirements = await _requirement_statuses(pool, req_ids)
    audits = await _certified_audits(pool, framework_id)
    vendors = await _certified_vendors(pool, framework_id)
    in_scope_assets = await _in_scope_assets(pool, req_ids)

    return FrameworkDetail(
        framework=framework,
        requirements=requirements,
        audits=audits,
        vendors=vendors,
        in_scope_assets=in_scope_assets,
    )


# ── internal helpers ─────────────────────────────────────────────────────


async def _requirement_ids(pool: asyncpg.Pool, framework_id: UUID) -> list[UUID]:
    rows = await pool.fetch(
        """SELECT r.target_asset_id AS id
           FROM relationships r
           JOIN assets a ON a.id = r.target_asset_id
           WHERE r.source_asset_id = $1
             AND r.relationship_type = 'parent_of'
             AND a.type = 'requirement'""",
        framework_id,
    )
    return [r["id"] for r in rows]


async def _satisfied_requirements(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> dict[UUID, bool]:
    """Return {req_id: True/False} for a batch of requirement ids."""
    if not req_ids:
        return {}

    out_rows = await pool.fetch(
        """SELECT DISTINCT source_asset_id FROM relationships
           WHERE source_asset_id = ANY($1::uuid[])
             AND relationship_type = ANY($2::text[])""",
        req_ids,
        list(_OUTBOUND_SATISFIES),
    )
    in_rows = await pool.fetch(
        """SELECT DISTINCT target_asset_id FROM relationships
           WHERE target_asset_id = ANY($1::uuid[])
             AND relationship_type = ANY($2::text[])""",
        req_ids,
        list(_INBOUND_SATISFIES),
    )
    satisfied = {r["source_asset_id"] for r in out_rows} | {
        r["target_asset_id"] for r in in_rows
    }
    return {rid: rid in satisfied for rid in req_ids}


async def _requirement_statuses(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> list[RequirementStatus]:
    if not req_ids:
        return []
    req_rows = await pool.fetch(
        "SELECT id, name FROM assets WHERE id = ANY($1::uuid[]) ORDER BY name",
        req_ids,
    )

    # Fetch all satisfier relationships in two batches
    out_rows = await pool.fetch(
        """SELECT r.source_asset_id AS req_id,
                  a.id AS satisfier_id,
                  a.name AS satisfier_name,
                  a.type::text AS satisfier_type,
                  r.relationship_type AS via
           FROM relationships r
           JOIN assets a ON a.id = r.target_asset_id
           WHERE r.source_asset_id = ANY($1::uuid[])
             AND r.relationship_type = ANY($2::text[])""",
        req_ids,
        list(_OUTBOUND_SATISFIES),
    )
    in_rows = await pool.fetch(
        """SELECT r.target_asset_id AS req_id,
                  a.id AS satisfier_id,
                  a.name AS satisfier_name,
                  a.type::text AS satisfier_type,
                  r.relationship_type AS via
           FROM relationships r
           JOIN assets a ON a.id = r.source_asset_id
           WHERE r.target_asset_id = ANY($1::uuid[])
             AND r.relationship_type = ANY($2::text[])""",
        req_ids,
        list(_INBOUND_SATISFIES),
    )

    by_req: dict[UUID, list[dict]] = {rid: [] for rid in req_ids}
    for row in list(out_rows) + list(in_rows):
        by_req[row["req_id"]].append({
            "id": row["satisfier_id"],
            "name": row["satisfier_name"],
            "type": row["satisfier_type"],
            "via": row["via"],
        })

    return [
        RequirementStatus(
            id=r["id"],
            name=r["name"],
            satisfied=bool(by_req.get(r["id"])),
            satisfiers=by_req.get(r["id"], []),
        )
        for r in req_rows
    ]


async def _certified_audits(
    pool: asyncpg.Pool, framework_id: UUID
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """SELECT a.id, a.name, a.status, a.metadata
           FROM relationships r
           JOIN assets a ON a.id = r.target_asset_id
           WHERE r.source_asset_id = $1
             AND r.relationship_type = 'certifies'
             AND a.type = 'audit'
           ORDER BY a.name""",
        framework_id,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "status": r["status"],
            "metadata": _parse_metadata(r["metadata"]),
        }
        for r in rows
    ]


async def _certified_vendors(
    pool: asyncpg.Pool, framework_id: UUID
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """SELECT a.id, a.name, a.status
           FROM relationships r
           JOIN assets a ON a.id = r.source_asset_id
           WHERE r.target_asset_id = $1
             AND r.relationship_type = 'certified_by'
             AND a.type = 'vendor'
           ORDER BY a.name""",
        framework_id,
    )
    return [
        {"id": r["id"], "name": r["name"], "status": r["status"]} for r in rows
    ]


async def gap_report_rows(
    pool: asyncpg.Pool, framework_id: UUID, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Flat per-requirement rows for a CSV export of the framework's gap report.

    Each requirement contributes one row regardless of satisfier count;
    satisfiers are concatenated as ``name (type) via edge_type; ...`` so the
    row stays grep-friendly in a spreadsheet.
    """
    fw = await pool.fetchrow(
        """SELECT id FROM assets WHERE id = $1 AND type = 'framework'
           AND ($2::uuid IS NULL OR organization_id = $2)""",
        framework_id, organization_id,
    )
    if not fw:
        return []
    req_ids = await _requirement_ids(pool, framework_id)
    statuses = await _requirement_statuses(pool, req_ids)
    last_audited = await _last_audited_for_requirements(pool, req_ids)
    rows: list[dict[str, Any]] = []
    for r in statuses:
        sat_str = "; ".join(
            f"{s['name']} ({s['type']}) via {s['via']}" for s in r.satisfiers
        )
        rows.append({
            "requirement_id": str(r.id),
            "requirement_name": r.name,
            "satisfied": "yes" if r.satisfied else "no",
            "satisfier_count": len(r.satisfiers),
            "satisfiers": sat_str,
            "last_audited": (
                last_audited[r.id].isoformat() if last_audited.get(r.id) else ""
            ),
        })
    return rows


async def _last_audited_for_requirements(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> dict[UUID, Any]:
    """Most-recent audit_log timestamp tied to each requirement or its satisfiers.

    "Audited" here means any update event recorded against the requirement
    asset itself or any of its satisfier assets — that's the proxy we have
    until per-audit reports land. Returns {req_id: datetime|None}.
    """
    if not req_ids:
        return {}
    # Build the (req_id → satisfier_ids) graph in one query, then ask the
    # audit_log for the most recent update timestamp across that union.
    rels = await pool.fetch(
        """SELECT r.source_asset_id AS req_id, r.target_asset_id AS sat_id
           FROM relationships r
           WHERE r.source_asset_id = ANY($1::uuid[])
             AND r.relationship_type = ANY($2::text[])
           UNION
           SELECT r.target_asset_id AS req_id, r.source_asset_id AS sat_id
           FROM relationships r
           WHERE r.target_asset_id = ANY($1::uuid[])
             AND r.relationship_type = ANY($3::text[])""",
        req_ids,
        list(_OUTBOUND_SATISFIES),
        list(_INBOUND_SATISFIES),
    )
    sats_by_req: dict[UUID, set[UUID]] = {rid: {rid} for rid in req_ids}
    for r in rels:
        sats_by_req.setdefault(r["req_id"], {r["req_id"]}).add(r["sat_id"])

    all_ids = {rid for ids in sats_by_req.values() for rid in ids}
    if not all_ids:
        return {rid: None for rid in req_ids}

    # Pull the most recent update timestamp per asset id from audit_log.
    audit_rows = await pool.fetch(
        """SELECT entity_id, max(created_at) AS last_at
           FROM audit_log
           WHERE entity_type = 'asset'
             AND entity_id = ANY($1::uuid[])
             AND action IN ('create', 'update')
           GROUP BY entity_id""",
        list(all_ids),
    )
    last_by_asset = {r["entity_id"]: r["last_at"] for r in audit_rows}

    out: dict[UUID, Any] = {}
    for rid, sat_ids in sats_by_req.items():
        timestamps = [last_by_asset.get(sid) for sid in sat_ids if last_by_asset.get(sid)]
        out[rid] = max(timestamps) if timestamps else None
    return out


async def list_controls_with_coverage(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Inverted view: every Control asset and the requirements it covers.

    The graph stores satisfaction as ``control --[satisfies]--> requirement``
    or ``requirement --[satisfied_by/implemented_by]--> control``; both edges
    count here so admins see the full picture without having to chase the
    direction.
    """
    rows = await pool.fetch(
        """SELECT c.id AS control_id, c.name AS control_name,
                  c.metadata AS control_meta,
                  rq.id AS req_id, rq.name AS req_name, fw.id AS fw_id,
                  fw.name AS fw_name
           FROM assets c
           LEFT JOIN relationships r
                  ON ((r.source_asset_id = c.id AND r.relationship_type = 'satisfies')
                   OR (r.target_asset_id = c.id AND r.relationship_type = ANY($2::text[])))
           LEFT JOIN assets rq ON rq.id = CASE
                WHEN r.source_asset_id = c.id THEN r.target_asset_id
                ELSE r.source_asset_id
           END AND rq.type = 'requirement'
           LEFT JOIN relationships fr
                  ON fr.target_asset_id = rq.id AND fr.relationship_type = 'parent_of'
           LEFT JOIN assets fw
                  ON fw.id = fr.source_asset_id AND fw.type = 'framework'
           WHERE c.type = 'control'
             AND ($1::uuid IS NULL OR c.organization_id = $1)
           ORDER BY c.name""",
        organization_id,
        list(_OUTBOUND_SATISFIES),
    )
    by_control: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        bucket = by_control.setdefault(r["control_id"], {
            "id": r["control_id"],
            "name": r["control_name"],
            "metadata": _parse_metadata(r["control_meta"]),
            "requirements": [],
        })
        if r["req_id"] is None:
            continue
        bucket["requirements"].append({
            "id": r["req_id"],
            "name": r["req_name"],
            "framework_id": r["fw_id"],
            "framework_name": r["fw_name"],
        })
    return list(by_control.values())


async def _in_scope_assets(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> list[dict[str, Any]]:
    """Distinct assets connected to any requirement in this framework (excluding requirements themselves)."""
    if not req_ids:
        return []
    rows = await pool.fetch(
        """SELECT DISTINCT a.id, a.name, a.type::text AS type, a.status
           FROM assets a
           JOIN relationships r
             ON (r.source_asset_id = ANY($1::uuid[]) AND r.target_asset_id = a.id)
             OR (r.target_asset_id = ANY($1::uuid[]) AND r.source_asset_id = a.id)
           WHERE a.type <> 'requirement'
             AND a.type <> 'framework'
           ORDER BY a.type::text, a.name""",
        req_ids,
    )
    return [
        {"id": r["id"], "name": r["name"], "type": r["type"], "status": r["status"]}
        for r in rows
    ]
