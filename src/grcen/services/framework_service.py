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
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg

from grcen.models.asset import POSTURE_TYPES
from grcen.services import evidence_service


@dataclass
class FrameworkSummary:
    id: UUID
    name: str
    metadata: dict[str, Any]
    requirement_count: int
    satisfied_count: int
    # Requirements that are a *direct* gap but cross-map (equivalent) to a
    # directly-satisfied requirement in another framework — "borrowed" coverage.
    borrowed_count: int = 0
    # Satisfied-but-weak controls, and coverage weighted by control effectiveness.
    weak_count: int = 0
    health_adjusted_coverage_percent: int = 0

    @property
    def coverage_percent(self) -> int:
        """Direct coverage only."""
        if self.requirement_count == 0:
            return 0
        return round(100 * self.satisfied_count / self.requirement_count)

    @property
    def effective_satisfied_count(self) -> int:
        return self.satisfied_count + self.borrowed_count

    @property
    def effective_coverage_percent(self) -> int:
        """Direct coverage plus coverage borrowed via equivalent crosswalks."""
        if self.requirement_count == 0:
            return 0
        return round(100 * self.effective_satisfied_count / self.requirement_count)


@dataclass
class RequirementStatus:
    id: UUID
    name: str
    satisfied: bool
    satisfiers: list[dict[str, Any]]  # [{id, name, type, via}]
    # Equivalent requirements in *other* frameworks (cross_maps edges):
    # [{id, name, code, framework, relationship}]
    crosswalks: list[dict[str, Any]] = field(default_factory=list)
    # True when this is a direct gap but an equivalent requirement elsewhere is
    # satisfied; borrowed_from holds those satisfied equivalents.
    covered_via_crosswalk: bool = False
    borrowed_from: list[dict[str, Any]] = field(default_factory=list)
    # Max effectiveness weight (0..1) among satisfying *control* assets; None when
    # satisfied only by a non-control (policy/system) or not satisfied at all.
    satisfaction_strength: float | None = None
    # Worst evidence freshness among satisfying controls (fresh/aging/expired);
    # None when no expiry-tracked evidence is attached.
    evidence_status: str | None = None
    # Statement of Applicability: in scope unless explicitly marked not applicable.
    applicable: bool = True
    implementation_status: str | None = None
    applicability_justification: str | None = None

    @property
    def stale_evidence(self) -> bool:
        return self.evidence_status in evidence_service.STALE

    @property
    def coverage(self) -> str:
        """One of: 'satisfied' | 'covered_via_crosswalk' | 'gap'."""
        if self.satisfied:
            return "satisfied"
        if self.covered_via_crosswalk:
            return "covered_via_crosswalk"
        return "gap"

    @property
    def graded(self) -> str:
        """Health-weighted tier: satisfied_strong | satisfied_weak | satisfied
        (ungraded) | covered_via_crosswalk | gap."""
        if self.satisfied:
            if self.satisfaction_strength is None:
                return "satisfied"  # backed by a non-control; can't grade health
            if self.satisfaction_strength >= _STRONG_THRESHOLD:
                return "satisfied_strong"
            return "satisfied_weak"
        if self.covered_via_crosswalk:
            return "covered_via_crosswalk"
        return "gap"


@dataclass
class FrameworkDetail:
    framework: dict[str, Any]  # asset row (name, description, metadata, ...)
    requirements: list[RequirementStatus]
    audits: list[dict[str, Any]]
    vendors: list[dict[str, Any]]
    in_scope_assets: list[dict[str, Any]]

    @property
    def applicable_requirements(self) -> list[RequirementStatus]:
        """In-scope requirements (SoA): excludes those marked not applicable.
        All coverage metrics divide by this set, so a deliberately-N/A requirement
        is never counted as a gap."""
        return [r for r in self.requirements if r.applicable]

    @property
    def applicable_count(self) -> int:
        return len(self.applicable_requirements)

    @property
    def not_applicable_count(self) -> int:
        return sum(1 for r in self.requirements if not r.applicable)

    @property
    def satisfied_count(self) -> int:
        return sum(1 for r in self.applicable_requirements if r.satisfied)

    @property
    def gap_count(self) -> int:
        """Not *directly* satisfied (includes those covered via crosswalk)."""
        return sum(1 for r in self.applicable_requirements if not r.satisfied)

    @property
    def open_gap_count(self) -> int:
        """Truly open: neither directly satisfied nor covered via a crosswalk."""
        return sum(1 for r in self.applicable_requirements if r.coverage == "gap")

    @property
    def coverage_percent(self) -> int:
        """Direct coverage over in-scope (applicable) requirements."""
        apps = self.applicable_requirements
        if not apps:
            return 0
        return round(100 * self.satisfied_count / len(apps))

    @property
    def borrowed_count(self) -> int:
        return sum(1 for r in self.applicable_requirements if r.covered_via_crosswalk)

    @property
    def weak_count(self) -> int:
        """Satisfied on paper but by a weak/ineffective/untested control."""
        return sum(1 for r in self.applicable_requirements if r.graded == "satisfied_weak")

    @property
    def stale_evidence_count(self) -> int:
        """Satisfied requirements whose control evidence is aging or expired."""
        return sum(1 for r in self.applicable_requirements if r.satisfied and r.stale_evidence)

    @property
    def evidence_freshness_percent(self) -> int:
        """Of satisfied requirements with expiry-tracked control evidence, the
        share whose evidence is still fresh. None of them → 100 (nothing stale)."""
        tracked = [r for r in self.applicable_requirements if r.satisfied and r.evidence_status]
        if not tracked:
            return 100
        fresh = sum(1 for r in tracked if r.evidence_status == "fresh")
        return round(100 * fresh / len(tracked))

    @property
    def health_adjusted_coverage_percent(self) -> int:
        """Coverage weighted by satisfying-control effectiveness (failing controls
        count for less). Borrowed and non-control-satisfied requirements count
        fully; direct gaps count zero."""
        apps = self.applicable_requirements
        if not apps:
            return 0
        total = 0.0
        for r in apps:
            if r.satisfied:
                total += r.satisfaction_strength if r.satisfaction_strength is not None else 1.0
            elif r.covered_via_crosswalk:
                total += 1.0
        return round(100 * total / len(apps))

    @property
    def effective_satisfied_count(self) -> int:
        return self.satisfied_count + self.borrowed_count

    @property
    def effective_coverage_percent(self) -> int:
        """Direct coverage plus coverage borrowed via equivalent crosswalks."""
        apps = self.applicable_requirements
        if not apps:
            return 0
        return round(100 * self.effective_satisfied_count / len(apps))

    @property
    def crosswalk_count(self) -> int:
        return sum(len(r.crosswalks) for r in self.requirements)


# Edges that mark a requirement as satisfied.
_OUTBOUND_SATISFIES = ("satisfied_by", "implemented_by")
_INBOUND_SATISFIES = ("satisfies",)

# Control effectiveness → satisfaction weight (mirrors risk_service._EFFECTIVENESS_WEIGHT).
# Used to grade coverage: a requirement satisfied only by a failing control is
# "covered on paper" but contributes less than its full weight.
_EFFECTIVENESS_WEIGHT = {
    "effective": 1.0,
    "partially_effective": 0.5,
    "ineffective": 0.0,
    "not_tested": 0.25,
}
_STRONG_THRESHOLD = 0.75


def _parse_metadata(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


# ── borrowed coverage (comply once, satisfy many) ─────────────────────────────
# A requirement that is a *direct* gap can still be "covered via crosswalk" when
# an EQUIVALENT requirement in another framework is itself directly satisfied —
# implement a control once and it counts everywhere the obligation is mirrored.
# Only `equivalent` crosswalks lend coverage; `partial`/`related` are too weak to
# imply the gap is met.


async def _satisfied_equivalents(pool: asyncpg.Pool, crosswalk_lists) -> set[UUID]:
    """Of the ``equivalent`` crosswalk targets in the given lists, the subset that
    is itself *directly* satisfied (and so can lend its coverage)."""
    targets = {
        cw["id"]
        for cws in crosswalk_lists
        for cw in cws
        if cw.get("relationship") == "equivalent"
    }
    if not targets:
        return set()
    sat = await _satisfied_requirements(pool, list(targets))
    return {tid for tid in targets if sat.get(tid)}


async def _attach_crosswalks_and_borrowing(
    pool: asyncpg.Pool,
    requirements: list[RequirementStatus],
    *,
    framework_name: str | None,
) -> None:
    """Populate each requirement's ``crosswalks`` and, for direct gaps, its
    ``covered_via_crosswalk`` / ``borrowed_from`` from satisfied equivalents."""
    req_ids = [r.id for r in requirements]
    xmap = await _crosswalks_for_requirements(
        pool, req_ids, exclude_framework=framework_name
    )
    for r in requirements:
        r.crosswalks = xmap.get(r.id, [])
    sat_equiv = await _satisfied_equivalents(
        pool, (r.crosswalks for r in requirements if not r.satisfied)
    )
    for r in requirements:
        if r.satisfied:
            continue
        borrowed = [
            cw
            for cw in r.crosswalks
            if cw.get("relationship") == "equivalent" and cw["id"] in sat_equiv
        ]
        if borrowed:
            r.covered_via_crosswalk = True
            r.borrowed_from = borrowed


async def _borrowed_count_for(
    pool: asyncpg.Pool,
    req_ids: list[UUID],
    satisfied_map: dict[UUID, bool],
    *,
    framework_name: str | None = None,
) -> int:
    """How many of these requirements are a direct gap but borrow coverage.

    ``framework_name`` is excluded from the crosswalk lookup so this matches the
    detail path exactly (borrowing is only from *other* frameworks).
    """
    gaps = [rid for rid in req_ids if not satisfied_map.get(rid)]
    if not gaps:
        return 0
    xmap = await _crosswalks_for_requirements(
        pool, gaps, exclude_framework=framework_name
    )
    sat_equiv = await _satisfied_equivalents(pool, xmap.values())
    return sum(
        1
        for rid in gaps
        if any(
            cw.get("relationship") == "equivalent" and cw["id"] in sat_equiv
            for cw in xmap.get(rid, [])
        )
    )


async def crosswalk_matrix(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> dict[str, Any]:
    """Framework×framework ``cross_maps`` edge counts (symmetric, deduped pairs).

    Returns ``{frameworks: [{id, name}], matrix: {a_id: {b_id: count}}, total}``
    with str ids, ready for a template to render as a grid.
    """
    fws = await pool.fetch(
        """SELECT id, name FROM assets WHERE type = 'framework'
           AND ($1::uuid IS NULL OR organization_id = $1) ORDER BY name""",
        organization_id,
    )
    rows = await pool.fetch(
        """SELECT r.source_asset_id AS ra, r.target_asset_id AS rb,
                  pa.source_asset_id AS a_fw, pb.source_asset_id AS b_fw
           FROM relationships r
           JOIN relationships pa
             ON pa.target_asset_id = r.source_asset_id
            AND pa.relationship_type = 'parent_of'
           JOIN relationships pb
             ON pb.target_asset_id = r.target_asset_id
            AND pb.relationship_type = 'parent_of'
           WHERE r.relationship_type = 'cross_maps'
             AND ($1::uuid IS NULL OR r.organization_id = $1)""",
        organization_id,
    )
    matrix: dict[str, dict[str, int]] = {str(f["id"]): {} for f in fws}
    total = 0
    # Count each undirected *requirement* pair once — robust to a requirement
    # with multiple parents (Cartesian rows) or a human-authored reverse edge.
    seen_pairs: set[tuple[str, str]] = set()
    for row in rows:
        ra, rb = str(row["ra"]), str(row["rb"])
        pair = (ra, rb) if ra <= rb else (rb, ra)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        a, b = str(row["a_fw"]), str(row["b_fw"])
        if a == b or a not in matrix or b not in matrix:
            continue
        matrix[a][b] = matrix[a].get(b, 0) + 1
        matrix[b][a] = matrix[b].get(a, 0) + 1
        total += 1
    return {
        "frameworks": [{"id": str(f["id"]), "name": f["name"]} for f in fws],
        "matrix": matrix,
        "total": total,
    }


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
        # SoA: only in-scope (applicable) requirements form the coverage denominator.
        applicable_ids = await _applicable_req_ids(pool, req_ids)
        if not applicable_ids:
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
        satisfied_map = await _satisfied_requirements(pool, applicable_ids)
        satisfied_ids = [rid for rid in applicable_ids if satisfied_map.get(rid)]
        strengths = await _requirement_strengths(pool, satisfied_ids)
        borrowed = await _borrowed_count_for(
            pool, applicable_ids, satisfied_map, framework_name=fw["name"]
        )
        # Health-weighted: each satisfied requirement contributes its control
        # strength (1.0 if satisfied by a non-control); borrowed counts fully.
        weak = sum(1 for rid in satisfied_ids
                   if rid in strengths and strengths[rid] < _STRONG_THRESHOLD)
        weighted = sum(strengths.get(rid, 1.0) for rid in satisfied_ids) + borrowed
        summaries.append(
            FrameworkSummary(
                id=fw["id"],
                name=fw["name"],
                metadata=_parse_metadata(fw["metadata"]),
                requirement_count=len(applicable_ids),
                satisfied_count=len(satisfied_ids),
                borrowed_count=borrowed,
                weak_count=weak,
                health_adjusted_coverage_percent=round(100 * weighted / len(applicable_ids)),
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
    await _attach_crosswalks_and_borrowing(
        pool, requirements, framework_name=framework["name"]
    )
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


async def _applicable_req_ids(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> list[UUID]:
    """The subset of requirements that are in scope (SoA): everything except
    those whose metadata explicitly marks ``applicable`` false."""
    if not req_ids:
        return []
    rows = await pool.fetch(
        "SELECT id, metadata FROM assets WHERE id = ANY($1::uuid[])", req_ids
    )
    return [
        r["id"] for r in rows
        if _parse_metadata(r["metadata"]).get("applicable") not in (False, "false", "False")
    ]


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


async def _requirement_strengths(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> dict[UUID, float]:
    """{req_id: max effectiveness weight among satisfying *control* assets}.

    Requirements with no control satisfier (e.g. satisfied only by a policy) are
    absent from the map — they can't be graded for control health.
    """
    if not req_ids:
        return {}
    rows = await pool.fetch(
        """SELECT e.req_id, c.metadata AS meta
           FROM (
               SELECT source_asset_id AS req_id, target_asset_id AS ctrl_id
               FROM relationships
               WHERE source_asset_id = ANY($1::uuid[])
                 AND relationship_type = ANY($2::text[])
               UNION ALL
               SELECT target_asset_id AS req_id, source_asset_id AS ctrl_id
               FROM relationships
               WHERE target_asset_id = ANY($1::uuid[])
                 AND relationship_type = ANY($3::text[])
           ) e
           JOIN assets c ON c.id = e.ctrl_id AND c.type = 'control'""",
        req_ids, list(_OUTBOUND_SATISFIES), list(_INBOUND_SATISFIES),
    )
    out: dict[UUID, float] = {}
    for r in rows:
        eff = _parse_metadata(r["meta"]).get("effectiveness")
        weight = _EFFECTIVENESS_WEIGHT.get(eff) if isinstance(eff, str) else None
        if weight is None:
            continue
        out[r["req_id"]] = max(out.get(r["req_id"], 0.0), weight)
    return out


async def _requirement_evidence(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> dict[UUID, str]:
    """{req_id: worst evidence freshness among satisfying *control* attachments}.

    Requirements whose satisfying controls carry no expiry-tracked evidence are
    absent from the map.
    """
    if not req_ids:
        return {}
    rows = await pool.fetch(
        """SELECT e.req_id, at.valid_until
           FROM (
               SELECT source_asset_id AS req_id, target_asset_id AS ctrl_id
               FROM relationships
               WHERE source_asset_id = ANY($1::uuid[])
                 AND relationship_type = ANY($2::text[])
               UNION ALL
               SELECT target_asset_id AS req_id, source_asset_id AS ctrl_id
               FROM relationships
               WHERE target_asset_id = ANY($1::uuid[])
                 AND relationship_type = ANY($3::text[])
           ) e
           JOIN assets c ON c.id = e.ctrl_id AND c.type = 'control'
           JOIN attachments at ON at.asset_id = c.id AND at.valid_until IS NOT NULL""",
        req_ids, list(_OUTBOUND_SATISFIES), list(_INBOUND_SATISFIES),
    )
    by_req: dict[UUID, list[str]] = {}
    for r in rows:
        by_req.setdefault(r["req_id"], []).append(evidence_service.classify(r["valid_until"]))
    out: dict[UUID, str] = {}
    for rid, statuses in by_req.items():
        w = evidence_service.worst(statuses)
        if w is not None:
            out[rid] = w
    return out


async def _crosswalks_for_requirements(
    pool: asyncpg.Pool, req_ids: list[UUID], *, exclude_framework: str | None = None
) -> dict[UUID, list[dict[str, Any]]]:
    """For each requirement, the equivalent requirements in *other* frameworks.

    Reads ``cross_maps`` edges in either direction, resolving the far endpoint's
    requirement, its short code, and its owning framework. The relationship label
    (equivalent/partial/related) is the leading token of the edge description.
    """
    by_req: dict[UUID, list[dict[str, Any]]] = {rid: [] for rid in req_ids}
    if not req_ids:
        return by_req
    rows = await pool.fetch(
        """SELECT r.source_asset_id AS a_id, r.target_asset_id AS b_id,
                  r.description AS rel,
                  oa.id AS other_id, oa.name AS other_name,
                  oa.metadata->>'reference_id' AS other_code,
                  fw.name AS other_framework
           FROM relationships r
           JOIN assets oa
             ON oa.id = CASE WHEN r.source_asset_id = ANY($1::uuid[])
                             THEN r.target_asset_id ELSE r.source_asset_id END
            AND oa.type = 'requirement'
           LEFT JOIN relationships fr
             ON fr.target_asset_id = oa.id AND fr.relationship_type = 'parent_of'
           LEFT JOIN assets fw
             ON fw.id = fr.source_asset_id AND fw.type = 'framework'
           WHERE r.relationship_type = 'cross_maps'
             AND (r.source_asset_id = ANY($1::uuid[])
                  OR r.target_asset_id = ANY($1::uuid[]))""",
        req_ids,
    )
    req_set = set(req_ids)
    for row in rows:
        anchor = row["a_id"] if row["a_id"] in req_set else row["b_id"]
        if exclude_framework and row["other_framework"] == exclude_framework:
            continue
        by_req.setdefault(anchor, []).append({
            "id": row["other_id"],
            "name": row["other_name"],
            "code": row["other_code"] or row["other_name"],
            "framework": row["other_framework"] or "—",
            # Relationship is the leading "·"-delimited token of the edge
            # description; normalise case so a human-authored "Equivalent" still
            # matches the vocabulary used for borrowing.
            "relationship": (row["rel"] or "related").split(" · ")[0].strip().lower(),
        })
    for links in by_req.values():
        links.sort(key=lambda x: (x["framework"], x["code"]))
    return by_req


async def _requirement_statuses(
    pool: asyncpg.Pool, req_ids: list[UUID]
) -> list[RequirementStatus]:
    if not req_ids:
        return []
    req_rows = await pool.fetch(
        "SELECT id, name, metadata FROM assets WHERE id = ANY($1::uuid[]) ORDER BY name",
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

    strengths = await _requirement_strengths(pool, req_ids)
    evidence = await _requirement_evidence(pool, req_ids)
    out: list[RequirementStatus] = []
    for r in req_rows:
        meta = _parse_metadata(r["metadata"])
        out.append(RequirementStatus(
            id=r["id"],
            name=r["name"],
            satisfied=bool(by_req.get(r["id"])),
            satisfiers=by_req.get(r["id"], []),
            satisfaction_strength=strengths.get(r["id"]),
            evidence_status=evidence.get(r["id"]),
            applicable=meta.get("applicable") not in (False, "false", "False"),
            implementation_status=meta.get("implementation_status"),
            applicability_justification=meta.get("applicability_justification"),
        ))
    return out


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
        """SELECT id, name FROM assets WHERE id = $1 AND type = 'framework'
           AND ($2::uuid IS NULL OR organization_id = $2)""",
        framework_id, organization_id,
    )
    if not fw:
        return []
    req_ids = await _requirement_ids(pool, framework_id)
    statuses = await _requirement_statuses(pool, req_ids)
    await _attach_crosswalks_and_borrowing(pool, statuses, framework_name=fw["name"])
    last_audited = await _last_audited_for_requirements(pool, req_ids)
    rows: list[dict[str, Any]] = []
    for r in statuses:
        sat_str = "; ".join(
            f"{s['name']} ({s['type']}) via {s['via']}" for s in r.satisfiers
        )
        borrowed_str = "; ".join(
            f"{b['code']} ({b['framework']})" for b in r.borrowed_from
        )
        rows.append({
            "requirement_id": str(r.id),
            "requirement_name": r.name,
            "coverage": r.coverage,
            "graded": r.graded,
            "satisfied": "yes" if r.satisfied else "no",
            "satisfier_count": len(r.satisfiers),
            "satisfiers": sat_str,
            "borrowed_from": borrowed_str,
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
             AND a.type::text <> ALL($2::text[])
           ORDER BY a.type::text, a.name""",
        req_ids,
        [t.value for t in POSTURE_TYPES],
    )
    return [
        {"id": r["id"], "name": r["name"], "type": r["type"], "status": r["status"]}
        for r in rows
    ]
