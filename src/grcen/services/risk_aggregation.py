"""Aggregate open vulnerability Findings into Risk assets (roadmap I4).

Vuln findings (from uvt/scanners via ``vuln_sync``) are numerous and low-level;
the risk register wants a manageable set of tracked Risks. This rolls *open*
Findings up into Risk assets — grouped **by affected System** (default) or **by
severity** — so unpatched vulnerabilities show up on the register and the 5×5
heatmap next to every other risk, with worst-severity mapped to likelihood ×
impact so they land in a sensible band.

Idempotent via ``external_sync`` under ``source='vuln-agg'``: re-running upserts
each Risk in place, links ``Risk --threatens--> System`` and ``Risk
--relates_to--> Finding`` (so the aggregation is traceable in the graph), prunes
edges to findings that have closed, and archives a Risk once its group has no
open findings left (reactivating it if new findings appear). Runs in one
transaction; ``dry_run`` rolls back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from grcen.services import external_sync

DEFAULT_SOURCE = "vuln-agg"

# Finding lifecycle states that count as resolved (excluded from aggregation).
_NON_OPEN = {"remediated", "verified", "risk_accepted", "closed"}

# Severity → (likelihood, impact) on the risk register's 5-point scales, chosen
# to land each band sensibly (critical 4×5=20, high 3×4=12, medium 3×3=9, low 2×2=4).
_SEV_TO_LI = {
    "critical": ("likely", "catastrophic"),
    "high": ("possible", "major"),
    "medium": ("possible", "moderate"),
    "low": ("unlikely", "minor"),
}
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class RiskAggregationResult:
    group_by: str = "system"
    risks_created: int = 0
    risks_updated: int = 0
    risks_archived: int = 0
    threatens_edges: int = 0
    relates_edges: int = 0
    edges_pruned: int = 0
    findings_considered: int = 0
    unassigned: int = 0          # open findings with no affected System (system mode)
    stale_risks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def _parse(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


def _worst_severity(findings: list[dict]) -> str:
    best = "low"
    best_rank = 0
    for f in findings:
        sev = str(f["meta"].get("severity") or "").lower()
        rank = _SEV_RANK.get(sev, 0)
        if rank > best_rank:
            best_rank, best = rank, sev
    return best if best_rank else "medium"


def _worst_cvss(findings: list[dict]) -> float | None:
    scores = []
    for f in findings:
        raw = f["meta"].get("cvss_score")
        try:
            scores.append(float(raw))
        except (TypeError, ValueError):
            continue
    return max(scores) if scores else None


def _cves(findings: list[dict]) -> list[str]:
    return sorted({str(f["meta"]["cve_id"]) for f in findings if f["meta"].get("cve_id")})


def _risk_payload(findings: list[dict]) -> tuple[dict, str]:
    """Return (metadata, description) for a Risk aggregating ``findings``."""
    sev = _worst_severity(findings)
    lik, imp = _SEV_TO_LI.get(sev, _SEV_TO_LI["medium"])
    meta = {
        "risk_category": "security",
        "severity": sev,
        "likelihood": lik,
        "impact": imp,
        "treatment": "mitigate",
        "threat_source": "vulnerability scan",
        "finding_count": len(findings),
    }
    cvss = _worst_cvss(findings)
    if cvss is not None:
        meta["control_effectiveness"] = "not_assessed"
    cves = _cves(findings)
    desc_bits = [f"{len(findings)} open vulnerability finding(s)."]
    if cvss is not None:
        desc_bits.append(f"Worst CVSS: {cvss}.")
    if cves:
        desc_bits.append("CVEs: " + ", ".join(cves[:20]) + ("…" if len(cves) > 20 else ""))
    desc_bits.append("Auto-aggregated by GRCen from scanner findings.")
    return meta, " ".join(desc_bits)


async def aggregate_vuln_risks(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    group_by: str = "system",
    source: str = DEFAULT_SOURCE,
    dry_run: bool = False,
    prune: bool = True,
) -> RiskAggregationResult:
    """Roll open vuln findings into Risk assets for one organization."""
    result = RiskAggregationResult(group_by=group_by, dry_run=dry_run)
    if group_by not in ("system", "severity"):
        result.errors.append("group_by must be 'system' or 'severity'")
        return result

    rows = await pool.fetch(
        """SELECT f.id AS fid, f.name AS fname, f.metadata AS fmeta,
                  s.id AS sid, s.name AS sname
           FROM assets f
           LEFT JOIN relationships r
             ON r.target_asset_id = f.id AND r.relationship_type = 'affected_by'
                AND r.organization_id = $1
           LEFT JOIN assets s
             ON s.id = r.source_asset_id AND s.type IN ('system', 'device')
           WHERE f.type = 'finding' AND f.status = 'active' AND f.organization_id = $1""",
        organization_id,
    )

    # Collapse to one record per finding, collecting its affected systems.
    findings: dict[UUID, dict] = {}
    for row in rows:
        meta = _parse(row["fmeta"])
        if str(meta.get("finding_status") or "open").lower() in _NON_OPEN:
            continue
        rec = findings.setdefault(
            row["fid"], {"id": row["fid"], "name": row["fname"], "meta": meta, "systems": []}
        )
        if row["sid"] is not None:
            rec["systems"].append((row["sid"], row["sname"]))
    result.findings_considered = len(findings)

    # Build groups: key -> {name, system_id?, findings[]}.
    groups: dict[str, dict] = {}
    if group_by == "system":
        for rec in findings.values():
            if not rec["systems"]:
                result.unassigned += 1
                continue
            for sid, sname in rec["systems"]:
                g = groups.setdefault(
                    f"system:{sid}",
                    {"name": f"Unpatched vulnerabilities: {sname}", "system_id": sid,
                     "sname": sname, "findings": []})
                g["findings"].append(rec)
    else:  # severity
        for rec in findings.values():
            sev = str(rec["meta"].get("severity") or "unknown").lower()
            g = groups.setdefault(
                f"severity:{sev}",
                {"name": f"{sev.title()} vulnerabilities", "system_id": None,
                 "findings": []})
            g["findings"].append(rec)

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            seen_risk_refs: list[str] = []
            seen_edge_refs: list[str] = []

            for sref, g in groups.items():
                meta, description = _risk_payload(g["findings"])
                r = await external_sync.upsert_external_asset(
                    conn, organization_id=organization_id, source=source, source_ref=sref,
                    asset_type="risk", name=g["name"], description=description,
                    status="active", metadata=meta,
                )
                seen_risk_refs.append(sref)
                if r.inserted:
                    result.risks_created += 1
                else:
                    result.risks_updated += 1

                if g["system_id"] is not None:
                    esref = f"threatens:{g['system_id']}"
                    await external_sync.upsert_external_edge(
                        conn, organization_id=organization_id, source=source, source_ref=esref,
                        source_asset_id=r.id, target_asset_id=g["system_id"],
                        relationship_type="threatens")
                    seen_edge_refs.append(esref)
                    result.threatens_edges += 1

                for rec in g["findings"]:
                    esref = f"relates:{sref}:{rec['id']}"
                    await external_sync.upsert_external_edge(
                        conn, organization_id=organization_id, source=source, source_ref=esref,
                        source_asset_id=r.id, target_asset_id=rec["id"],
                        relationship_type="relates_to")
                    seen_edge_refs.append(esref)
                    result.relates_edges += 1

            result.edges_pruned = await external_sync.prune_stale_edges(
                conn, organization_id=organization_id, source=source, seen_refs=seen_edge_refs)

            # Risks whose group emptied (all findings resolved) → archive.
            stale = await conn.fetch(
                """SELECT id, source_ref FROM assets
                   WHERE organization_id = $1 AND source = $2 AND type = 'risk'
                     AND source_ref <> ALL($3::text[])""",
                organization_id, source, seen_risk_refs)
            result.stale_risks = [r["source_ref"] for r in stale]
            if prune and stale:
                archived = await conn.execute(
                    """UPDATE assets SET status = 'archived', updated_at = now()
                       WHERE id = ANY($1::uuid[]) AND status <> 'archived'""",
                    [r["id"] for r in stale])
                result.risks_archived = int(archived.split()[-1])

            if dry_run:
                await tx.rollback()
            else:
                await tx.commit()
        except Exception:
            await tx.rollback()
            raise

    return result


async def aggregate_all_orgs(pool: asyncpg.Pool, *, group_by: str = "system") -> None:
    """Aggregate for every organization — the scheduled-job entry point."""
    org_ids = [r["id"] for r in await pool.fetch("SELECT id FROM organizations")]
    for org_id in org_ids:
        await aggregate_vuln_risks(pool, organization_id=org_id, group_by=group_by)
